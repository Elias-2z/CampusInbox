"""Deterministic workflow; transaction boundaries surround all user task writes."""

import hashlib
import json
import re
from datetime import UTC, datetime
from difflib import SequenceMatcher
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from campus_inbox import attachment_analysis, cancellations, merging
from campus_inbox.deadlines import change_read, find_change, find_proposal
from campus_inbox.extraction import (
    Extraction,
    cancellation_supported,
    deadline_supported,
    extract_rules,
    lab_schedule_quotes,
    merge_model,
    numbered_parts,
)
from campus_inbox.models import (
    Attachment,
    CancellationChange,
    DateDeadlineChange,
    DeadlineChange,
    MergeChange,
    MergeProposal,
    Message,
    SplitDraft,
    Suggestion,
    Task,
    TaskEvent,
    now,
)
from campus_inbox.providers import ProviderError
from campus_inbox.schemas import (
    AcceptSuggestion,
    AttachmentInfo,
    EventRead,
    ImportMessage,
    InboxItem,
    MessageRead,
    SplitDraftRead,
    TaskDetail,
    TaskFields,
    TaskListItem,
    TaskRead,
    TaskRecognitionPreview,
    TaskRecognitionRequest,
    TaskUpdate,
)


def utc_value(value: datetime | None) -> datetime | None:
    return value.astimezone(UTC) if value is not None else None


def get_or_404(session: Session, table, key: str):
    value = session.get(table, key)
    if value is None:
        raise HTTPException(404, "记录不存在")
    return value


def config(session: Session):
    from campus_inbox.inference import local_config

    return local_config(session)


def message_read(session: Session, message: Message) -> MessageRead:
    result = MessageRead.model_validate(message)
    attachments = session.exec(select(Attachment).where(Attachment.message_id == message.id)).all()
    if message.dedupe_key.startswith("unknown-time:"):
        result.sent_at = None
    if message.dedupe_key.startswith("legacy:"):
        from campus_inbox.legacy_records import context

        result.source_context = context(session, message.id)
    if message.dedupe_key.startswith("qq:"):
        from campus_inbox.connectors.qq import source_context

        result.source_context = source_context(session, message.dedupe_key)
        if result.source_context:
            context = result.source_context
            result.source = (
                f"QQ · {context['course'] or context['group_name'] or '课程群'}"
                f" · 发件人 {context['sender_name'] or context['sender_id']}"
            )
    elif message.dedupe_key.startswith("chaoxing:"):
        from campus_inbox.connectors.chaoxing import source_context

        result.source_context = source_context(
            session, message.dedupe_key, linked_attachments=attachments
        )
    result.attachments = [AttachmentInfo.model_validate(attachment) for attachment in attachments]
    return result


def normalized(value: str) -> str:
    return re.sub(r"[\W_]", "", value).casefold()


def course_key(value: str | None) -> str:
    # Course aliases can name distinct real courses; only ignore whitespace/case.
    return re.sub(r"\s+", "", value or "").casefold()


def task_ordinals(title: str) -> dict[str, set[int]]:
    from campus_inbox.extraction import number

    result: dict[str, set[int]] = {}
    pattern = (
        r"第([一二三四五六七八九十两\d]+)(次|章|周|单元|讲)"
        r"|(?:实验|作业|习题|练习|报告)([一二三四五六七八九十两\d]+)"
    )
    for match in re.finditer(pattern, normalized(title)):
        value = number(match[1] or match[3])
        if value >= 0:
            result.setdefault(match[2] or "次", set()).add(value)
    return result


def association_conflict(fields: dict, task: Task) -> str | None:
    course = course_key(fields.get("course"))
    if course and course_key(task.course) and course != course_key(task.course):
        return "课程不同"
    kind = fields.get("kind")
    if kind and kind != "other" and task.kind != "other" and kind != task.kind:
        return "任务类型不同"
    incoming, existing = task_ordinals(fields.get("title", "")), task_ordinals(task.title)
    if any(incoming[key] != existing[key] for key in incoming.keys() & existing.keys()):
        return "任务序号不同"
    return None


def source_fields(fields: dict, evidence: dict, context: dict) -> tuple[dict, dict]:
    if context.get("platform") == "chaoxing" and context.get("course"):
        course = context["course"]
        return {**fields, "course": course}, {
            **evidence,
            "course": {"method": "connector", "quote": f"学习通来源的课程：{course}"},
        }
    return fields, evidence


def matching_tasks(session: Session, fields: dict, *, message: Message | None = None) -> list[Task]:
    title = normalized(fields.get("title", ""))
    if len(title) < 2:
        return []
    from campus_inbox.connectors.platform_status import message_identity, task_identities

    incoming_key = message_identity(session, message) if message else ""
    identities = task_identities(session) if incoming_key else {}

    def experiment(value: str) -> int | None:
        from campus_inbox.extraction import number

        match = re.search(r"实验([一二三四五六七八九十两0-9]+)", value)
        return number(match[1]) if match else None

    ranked = []
    for task in session.exec(select(Task).where(Task.status.notin_(["archived", "cancelled"]))):
        course = fields.get("course", "")
        if course_key(course) != course_key(task.course):
            continue
        if association_conflict(fields, task):
            continue
        if incoming_key and any(key != incoming_key for key in identities.get(task.id, set())):
            continue
        existing = normalized(task.title)
        incoming_number, existing_number = experiment(title), experiment(existing)
        if (
            incoming_number is not None
            and existing_number is not None
            and incoming_number != existing_number
        ):
            continue
        ratio = SequenceMatcher(None, title, existing).ratio()
        score = 100 if title == existing else 80 * ratio if ratio >= 0.72 else 0
        if min(len(title), len(existing)) >= 3 and (title in existing or existing in title):
            score = max(score, 70)
        if experiment(title) is not None and experiment(title) == experiment(existing):
            score = max(score, 55)
        if score:
            ranked.append((score, task))
    ranked.sort(key=lambda item: (-item[0], item[1].id))
    return [task for _, task in ranked[:5]]


def inbox_item(session: Session, suggestion: Suggestion) -> InboxItem:
    task = session.exec(select(Task).where(Task.suggestion_id == suggestion.id)).first()
    change = find_change(session, suggestion.id)
    cancellation = session.get(CancellationChange, suggestion.id)
    merge = merging.find_change(session, merging.source_key(suggestion.id, None))
    split_merges = {
        change.split_draft_id: change
        for change in session.exec(
            select(MergeChange).where(
                MergeChange.suggestion_id == suggestion.id, MergeChange.split_draft_id.is_not(None)
            )
        )
    }
    items = list(
        session.exec(
            select(SplitDraft)
            .where(SplitDraft.suggestion_id == suggestion.id)
            .order_by(SplitDraft.position)
        )
    )
    message = get_or_404(session, Message, suggestion.message_id)
    display_message = message_read(session, message)
    context = display_message.source_context or {}
    fields, evidence = source_fields(suggestion.fields, suggestion.evidence, context)
    is_checkin = context.get("kind") == "checkin_activity"
    supported = suggestion.action == "review" and deadline_supported(message.text)
    warnings = [
        "检测到截止时间变更，请核对目标任务和新旧时间后确认。"
        if supported and warning == "检测到变更或取消信息：本版仅提示核对，不自动修改或创建任务。"
        else warning
        for warning in suggestion.warnings
    ]
    return InboxItem(
        id=suggestion.id,
        status=suggestion.status,
        action=suggestion.action,
        method=suggestion.method,
        fields=fields,
        evidence=evidence,
        warnings=warnings,
        message=display_message,
        activity_schedule=lab_schedule_quotes(message.text),
        matched_tasks=[
            TaskRead.model_validate(t)
            for t in ([] if is_checkin else matching_tasks(session, fields, message=message))
        ],
        task_id=(
            None
            if items
            else task.id
            if task
            else change.task_id
            if change
            else cancellation.task_id
            if cancellation
            else merge.task_id
            if merge
            else None
        ),
        merge_change=merging.change_read(session, merge) if merge else None,
        cancellation_supported=suggestion.action == "review"
        and cancellation_supported(message.text),
        cancellation_change=cancellations.change_read(session, cancellation)
        if cancellation
        else None,
        split_supported=suggestion.action == "review" and bool(numbered_parts(message.text)[1]),
        split_items=[
            SplitDraftRead(
                **item.model_dump(exclude={"task_id", "fields", "evidence"}),
                fields=source_fields(item.fields, item.evidence, context)[0],
                evidence=source_fields(item.fields, item.evidence, context)[1],
                task_id=item.task_id
                or (split_merges[item.id].task_id if item.id in split_merges else None),
                merge_change=merging.change_read(session, split_merges[item.id])
                if item.id in split_merges
                else None,
                matched_tasks=[
                    TaskRead.model_validate(t)
                    for t in matching_tasks(
                        session,
                        source_fields(item.fields, item.evidence, context)[0],
                        message=message,
                    )
                ],
            )
            for item in items
        ],
        updated_at=suggestion.updated_at,
        deadline_supported=supported,
        deadline_change=change_read(session, change) if change else None,
    )


def recognize_message(
    session: Session,
    message: Message,
    provider_factory,
    *,
    use_active_model=False,
    include_attachments=True,
) -> Extraction:
    time_known = not message.dedupe_key.startswith("unknown-time:")
    scoped_deadline = message.dedupe_key.startswith("chaoxing:")
    result = extract_rules(
        message.text,
        message.sent_at if time_known else None,
        message.timezone,
        scoped_deadline=scoped_deadline,
    )
    context = None
    if message.dedupe_key.startswith("qq:"):
        from campus_inbox.connectors.qq import source_context

        context = source_context(session, message.dedupe_key, persist=True)
    elif message.dedupe_key.startswith("chaoxing:"):
        from campus_inbox.connectors.chaoxing import (
            apply_platform_deadline,
            apply_platform_task,
            source_context,
        )

        context = source_context(session, message.dedupe_key)
        if context:
            apply_platform_task(result, context)
            apply_platform_deadline(result, context)
    manual_task = message.source == "手动创建" and re.fullmatch(
        r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", message.dedupe_key
    )
    if use_active_model and (re.fullmatch(r"[0-9a-f]{64}", message.dedupe_key) or manual_task):
        from campus_inbox.inference import active_config

        settings = active_config(session)
    else:
        settings = config(session)
    text, sent_at, timezone = message.text, message.sent_at, message.timezone
    session.commit()  # Release database transaction before waiting for inference.
    if time_known and result.needs_llm and result.action != "ignore":
        if settings.enabled and settings.model and len(text) <= 12000:
            try:
                provider = provider_factory(settings)
                reference = sent_at.replace(tzinfo=UTC).isoformat()
                model = (
                    provider.generate_contextual(text, reference, timezone, context)
                    if context and callable(getattr(provider, "generate_contextual", None))
                    else provider.generate_structured(text, reference, timezone)
                )
                result = merge_model(
                    result, model, text, sent_at, timezone, scoped_deadline=scoped_deadline
                )
            except ProviderError as exc:
                result.warnings.append(str(exc))
        else:
            result.warnings.append(
                "通知较长，已保留规则结果，请人工核对。"
                if len(text) > 12000
                else "模型兜底未启用或未选择模型，可直接手动完善。"
            )
    if (
        context
        and context["course"]
        and (not result.fields.get("course") or context.get("platform") == "chaoxing")
    ):
        result.fields["course"] = context["course"]
        result.evidence["course"] = {
            "method": "connector",
            "quote": (
                f"QQ群「{context['group_name']}」的课程设置：{context['course']}"
                if context.get("platform") == "qq"
                else f"学习通来源的课程：{context['course']}"
            ),
        }
    attachment_count = (
        context["attachment_analysis_count"]
        if context and context.get("platform") == "chaoxing"
        else 1
    )
    used_attachment = (
        include_attachments
        and time_known
        and attachment_count > 0
        and attachment_analysis.supplement(session, message, result, attachment_count)
    )
    if not result.fields.get("title") and result.action == "create":
        result.warnings.append("任务名称待补充。")
    if (
        context
        and context.get("platform") == "chaoxing"
        and context.get("has_unprocessed_media")
        and not used_attachment
    ):
        result.warnings.append("学习通附件或图片尚未用于任务识别，请查看原文及附件核对。")
    return result


def analyze(
    session: Session, suggestion: Suggestion, provider_factory, *, use_active_model=False
) -> Suggestion:
    from campus_inbox.connectors import chaoxing

    message = get_or_404(session, Message, suggestion.message_id)
    result = recognize_message(
        session, message, provider_factory, use_active_model=use_active_model
    )
    suggestion.fields, suggestion.evidence = result.fields, result.evidence
    suggestion.warnings = list(dict.fromkeys(result.warnings))
    suggestion.action, suggestion.method = result.action, result.method
    suggestion.status, suggestion.updated_at = "pending", now()
    if not message.dedupe_key.startswith("chaoxing:") and chaoxing.expired_task_notice(
        {}, result, chaoxing.now(), message.timezone
    ):
        suggestion.status = "archived"
        suggestion.warnings.append(chaoxing.EXPIRED_NOTICE)
    session.add(suggestion)
    session.commit()
    session.refresh(suggestion)
    return suggestion


def recognize_task(
    session: Session, task: Task, body: TaskRecognitionRequest, provider_factory
) -> TaskRecognitionPreview:
    if task.version != body.expected_version:
        raise HTTPException(409, "任务已发生变化，请重新读取后识别。")
    snapshot_task = TaskRead.model_validate(task)
    sources = detail(session, task).sources
    message_id = body.message_id or task.message_id
    if not any(source.id == message_id for source in sources):
        raise HTTPException(422, "请选择该任务的原始通知。")
    message = get_or_404(session, Message, message_id)
    draft = session.exec(
        select(SplitDraft).where(
            SplitDraft.task_id == task.id, SplitDraft.suggestion_id == task.suggestion_id
        )
    ).first()
    if message_id != task.message_id:
        changes = session.exec(
            select(MergeChange).where(
                MergeChange.task_id == task.id,
                MergeChange.split_draft_id.is_not(None),
                MergeChange.undo_event_id.is_(None),
            )
        ).all()
        linked = [
            session.get(SplitDraft, change.split_draft_id)
            for change in changes
            if get_or_404(session, Suggestion, change.suggestion_id).message_id == message_id
        ]
        if len(linked) > 1:
            raise HTTPException(422, "该来源关联了多个拆分条目，请选择其他原始通知。")
        draft = linked[0] if linked else None
    if draft:
        message = Message(**{**message.model_dump(), "text": draft.quote})
    result = recognize_message(
        session,
        message,
        provider_factory,
        use_active_model=True,
        include_attachments=draft is None,
    )
    return TaskRecognitionPreview(
        task=snapshot_task,
        fields={key: value for key, value in result.fields.items() if key != "description"},
        evidence=result.evidence,
        warnings=list(dict.fromkeys(result.warnings)),
        method=result.method,
        message=message_read(session, message),
    )


def reuse_imported_suggestion(session: Session, message_id: str, *, manual: bool) -> Suggestion:
    suggestion = session.exec(select(Suggestion).where(Suggestion.message_id == message_id)).one()
    if manual and suggestion.status == "ignored":
        drafts = session.exec(
            select(SplitDraft).where(SplitDraft.suggestion_id == suggestion.id)
        ).all()
        if drafts and not any(draft.status == "pending" for draft in drafts):
            raise HTTPException(409, "这条通知包含已处理的拆分条目，请在已忽略中逐项恢复待审核。")
        session.execute(
            update(Suggestion)
            .where(Suggestion.id == suggestion.id, Suggestion.status == "ignored")
            .values(status="pending", updated_at=now())
        )
        session.commit()
        session.refresh(suggestion)
    if manual and suggestion.status == "pending":
        from campus_inbox.connectors.chaoxing import archive_expired

        archive_expired(session, suggestion_id=suggestion.id)
        session.refresh(suggestion)
    return suggestion


def import_message(
    session: Session,
    body: ImportMessage,
    provider_factory,
    *,
    dedupe_key: str | None = None,
    use_active_model=False,
) -> Suggestion:
    attachment = None
    if body.attachment_id:
        attachment = get_or_404(session, Attachment, body.attachment_id)
        if attachment.status != "ready":
            raise HTTPException(409, "文件尚未解析完成，请先核对提取文本。")
        if attachment.message_id:
            # Repeated submission of a corrected OCR draft must not create another notice.
            return reuse_imported_suggestion(
                session, attachment.message_id, manual=dedupe_key is None
            )
    day = (
        body.sent_at.astimezone(ZoneInfo(body.timezone)).date().isoformat()
        if body.sent_at
        else None
    )
    key = (
        dedupe_key
        or hashlib.sha256(
            json.dumps(
                [re.sub(r"\s+", " ", body.text), body.source, day, body.timezone],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
    )
    if body.sent_at is None:
        key = "unknown-time:" + key
    existing = session.exec(select(Message).where(Message.dedupe_key == key)).first()
    if existing:
        if attachment:
            attachment.message_id = existing.id
            session.add(attachment)
            session.commit()
        return reuse_imported_suggestion(session, existing.id, manual=dedupe_key is None)
    message = Message(
        text=body.text,
        source=body.source,
        sent_at=utc_value(body.sent_at) or datetime(1970, 1, 1, tzinfo=UTC),
        timezone=body.timezone,
        dedupe_key=key,
    )
    suggestion = Suggestion(message_id=message.id)
    session.add(message)
    session.add(suggestion)
    try:
        if attachment:
            # Persist the parent row before updating an existing attachment's foreign key.
            session.flush()
            attachment.message_id = message.id
            session.add(attachment)
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.exec(select(Message).where(Message.dedupe_key == key)).first()
        if existing is None:
            raise
        if attachment:
            attachment.message_id = existing.id
            session.add(attachment)
            session.commit()
        return reuse_imported_suggestion(session, existing.id, manual=dedupe_key is None)
    session.refresh(suggestion)
    return analyze(session, suggestion, provider_factory, use_active_model=use_active_model)


def refresh_attachment_suggestion(session: Session, message_id: str) -> None:
    """Supplement a pending, unsplit draft without touching any confirmed task."""
    source = session.exec(select(Suggestion).where(Suggestion.message_id == message_id)).first()
    if source is None or source.status != "pending":
        return
    if session.exec(select(SplitDraft.id).where(SplitDraft.suggestion_id == source.id)).first():
        return
    message = get_or_404(session, Message, message_id)
    if message.dedupe_key.startswith("unknown-time:"):
        return
    context = message_read(session, message).source_context or {}
    count = context.get("attachment_analysis_count", 0)
    if not count:
        return
    result = Extraction(
        fields=dict(source.fields),
        evidence=dict(source.evidence),
        warnings=list(source.warnings),
        action=source.action,
        method=source.method,
    )
    if not attachment_analysis.supplement(session, message, result, count):
        return
    result.warnings = [
        warning
        for warning in result.warnings
        if warning != "学习通附件或图片尚未用于任务识别，请查看原文及附件核对。"
    ]
    session.execute(
        update(Suggestion)
        .where(
            Suggestion.id == source.id,
            Suggestion.status == "pending",
            Suggestion.updated_at == source.updated_at,
        )
        .values(
            fields=result.fields,
            evidence=result.evidence,
            warnings=list(dict.fromkeys(result.warnings)),
            action=result.action,
            updated_at=now(),
        )
    )
    session.commit()


def retry(
    session: Session, suggestion: Suggestion, provider_factory, *, use_active_model=False
) -> Suggestion:
    from campus_inbox.connectors.chaoxing import checkin_event
    from campus_inbox.legacy_records import context

    if checkin_event(session, get_or_404(session, Message, suggestion.message_id)):
        raise HTTPException(409, "签到使用平台活动详情，请通过学习通同步刷新。")
    legacy = context(session, suggestion.message_id)
    if legacy and not legacy["time_known"]:
        raise HTTPException(409, "旧版原消息发布时间未知，请手动核对字段，不能据迁移时间重新识别。")
    if session.exec(select(SplitDraft).where(SplitDraft.suggestion_id == suggestion.id)).first():
        raise HTTPException(409, "已生成拆分草稿，请逐项编辑核对，避免重新识别覆盖处理进度。")
    changed = session.execute(
        update(Suggestion)
        .where(
            Suggestion.id == suggestion.id,
            Suggestion.status == "pending",
        )
        .values(status="processing", updated_at=now())
    ).rowcount
    session.commit()
    if changed != 1:
        raise HTTPException(409, "只有待审核建议可以重新识别；正在识别时请稍候。")
    session.refresh(suggestion)
    return analyze(session, suggestion, provider_factory, use_active_model=use_active_model)


def snapshot(task: Task) -> dict:
    return TaskRead.model_validate(task).model_dump(mode="json")


def accept(session: Session, suggestion: Suggestion, body: AcceptSuggestion) -> Task:
    if session.exec(select(SplitDraft).where(SplitDraft.suggestion_id == suggestion.id)).first():
        raise HTTPException(409, "多任务通知请逐项确认草稿。")
    existing = session.exec(select(Task).where(Task.suggestion_id == suggestion.id)).first()
    if existing:
        return existing
    if suggestion.status != "pending":
        raise HTTPException(409, "建议已处理或仍在识别，请刷新后重试。")
    if suggestion.action != "create":
        raise HTTPException(409, "这条通知需要人工核对，不能直接按新任务入库。")
    from campus_inbox.connectors.chaoxing import (
        archive_expired,
        checkin_event,
        validate_checkin_accept,
    )

    previous_fields = suggestion.fields
    if archive_expired(session, suggestion_id=suggestion.id):
        raise HTTPException(409, suggestion.warnings[-1])
    if suggestion.fields != previous_fields:
        raise HTTPException(409, "通知的时间字段已更新，请刷新核对后确认。")

    checkin = checkin_event(session, get_or_404(session, Message, suggestion.message_id))
    fields = body.model_dump(exclude={"allow_duplicate"})
    if checkin:
        fields.update(due_at=validate_checkin_accept(session, checkin), due_date=None, kind="event")
    elif (
        matching_tasks(session, fields, message=get_or_404(session, Message, suggestion.message_id))
        and not body.allow_duplicate
    ):
        raise HTTPException(409, "发现可能重复的任务，请核对并明确选择仍然创建。")
    fields["due_at"] = utc_value(fields["due_at"])
    task = Task(**fields, suggestion_id=suggestion.id, message_id=suggestion.message_id)
    changed = session.execute(
        update(Suggestion)
        .where(
            Suggestion.id == suggestion.id,
            Suggestion.status == "pending",
        )
        .values(status="accepted", updated_at=now())
    ).rowcount
    if changed != 1:
        session.rollback()
        existing = session.exec(select(Task).where(Task.suggestion_id == suggestion.id)).first()
        if existing:
            return existing
        raise HTTPException(409, "建议状态已变化，请刷新后重试。")
    evidence = dict(suggestion.evidence)
    for key, value in body.model_dump(mode="json", exclude={"allow_duplicate"}).items():
        original_value = suggestion.fields.get(key)
        same = value == original_value
        if key == "due_at" and value and original_value:
            same = datetime.fromisoformat(value) == datetime.fromisoformat(original_value)
        if not same or suggestion.method == "manual" and value not in (None, ""):
            evidence[key] = {
                "method": "user",
                "quote": "用户审核填写" if value not in (None, "") else "用户审核清空",
                "original": evidence.get(key),
            }
    suggestion.evidence, suggestion.status = evidence, "accepted"
    session.add(suggestion)
    session.add(task)
    session.flush()
    session.add(
        TaskEvent(
            task_id=task.id, message_id=task.message_id, operation="create", after=snapshot(task)
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.exec(select(Task).where(Task.suggestion_id == suggestion.id)).first()
        if existing:
            return existing
        raise
    session.refresh(task)
    return task


def manual_create(session: Session, body: TaskFields) -> Task:
    data = body.model_dump(mode="json")
    message = Message(
        text=f"手动创建任务：{body.title}\n课程：{body.course or '待分类'}\n{body.description}",
        source="手动创建",
        sent_at=now(),
        dedupe_key=str(uuid4()),
    )
    suggestion = Suggestion(message_id=message.id, status="pending", method="manual", fields=data)
    session.add(message)
    session.add(suggestion)
    session.flush()
    return accept(session, suggestion, AcceptSuggestion(**data, allow_duplicate=True))


def update_task(session: Session, task: Task, body: TaskUpdate) -> Task:
    before = snapshot(task)
    data = body.model_dump(exclude={"expected_version"})
    data["due_at"] = utc_value(body.due_at)
    # Older clients do not know the date-only field; a title/status edit must
    # not silently erase it. Supplying a precise time intentionally replaces it.
    if "due_date" not in body.model_fields_set and body.due_at is None:
        data.pop("due_date", None)
    changed = session.execute(
        update(Task)
        .where(
            Task.id == task.id,
            Task.version == body.expected_version,
        )
        .values(**data, version=Task.version + 1, updated_at=now())
    ).rowcount
    if changed != 1:
        session.rollback()
        raise HTTPException(409, "任务已被其他操作修改，请刷新后重试，避免覆盖新内容。")
    session.expire(task)
    session.refresh(task)
    session.add(
        TaskEvent(
            task_id=task.id,
            message_id=task.message_id,
            operation="update",
            before=before,
            after=snapshot(task),
        )
    )
    session.commit()
    session.refresh(task)
    return task


def list_tasks(session: Session, current: datetime) -> list[TaskListItem]:
    from campus_inbox.connectors.chaoxing import closed_checkin_suggestions

    tasks = session.exec(select(Task).order_by(Task.created_at.desc())).all()
    closed = closed_checkin_suggestions(session, [task.suggestion_id for task in tasks], current)
    methods = dict(
        session.exec(
            select(Suggestion.id, Suggestion.method).where(
                Suggestion.id.in_([task.suggestion_id for task in tasks])
            )
        ).all()
    )
    return [
        TaskListItem(
            **task.model_dump(),
            checkin_closed=task.suggestion_id in closed,
            recognition_method=methods.get(task.suggestion_id, ""),
        )
        for task in tasks
    ]


def detail(session: Session, task: Task) -> TaskDetail:
    from campus_inbox.connectors.chaoxing import closed_checkin_suggestions
    from campus_inbox.connectors.platform_status import for_task, public
    from campus_inbox.legacy_records import task_record
    from campus_inbox.models import TaskMaterials

    suggestion = get_or_404(session, Suggestion, task.suggestion_id)
    events = list(
        session.exec(
            select(TaskEvent)
            .where(TaskEvent.task_id == task.id)
            .order_by(
                TaskEvent.created_at,
                TaskEvent.id,
            )
        )
    )
    changes = list(session.exec(select(DeadlineChange).where(DeadlineChange.task_id == task.id)))
    changes.extend(
        session.exec(select(DateDeadlineChange).where(DateDeadlineChange.task_id == task.id))
    )
    by_event = {change.event_id: change for change in changes}
    by_undo = {change.undo_event_id: change for change in changes if change.undo_event_id}
    split_draft = session.exec(select(SplitDraft).where(SplitDraft.task_id == task.id)).first()
    evidence = dict(split_draft.evidence if split_draft else suggestion.evidence)
    merges = list(session.exec(select(MergeChange).where(MergeChange.task_id == task.id)))
    merge_events = {change.event_id: change for change in merges}
    merge_undos = {change.undo_event_id: change for change in merges if change.undo_event_id}
    prior_merge = {}
    prior_due = {}
    events.sort(key=lambda event: (event.after.get("version", 0), event.created_at, event.id))
    for event in events:
        if event.operation == "update":
            for key in event.after:
                if event.after[key] != event.before.get(key) and key in TaskFields.model_fields:
                    evidence[key] = {
                        "method": "user",
                        "quote": "用户编辑",
                        "original": evidence.get(key),
                    }
        elif event.operation == "deadline_update" and event.id in by_event:
            prior_due[event.id] = {key: evidence.get(key) for key in ("due_at", "due_date")}
            proposal = find_proposal(session, by_event[event.id].proposal_id)
            key = "due_date" if getattr(proposal, "proposed_due_date", None) else "due_at"
            evidence.pop("due_at", None)
            evidence.pop("due_date", None)
            evidence[key] = {**proposal.evidence, "confirmed": True}
        elif event.operation == "deadline_undo" and event.id in by_undo:
            previous = prior_due.get(by_undo[event.id].event_id, {})
            for key in ("due_at", "due_date"):
                if previous.get(key) is None:
                    evidence.pop(key, None)
                else:
                    evidence[key] = previous[key]
        elif event.operation == "merge" and event.id in merge_events:
            proposal = get_or_404(session, MergeProposal, merge_events[event.id].proposal_id)
            prior_merge[event.id] = {key: evidence.get(key) for key in proposal.after_fields}
            for key, value in proposal.evidence.items():
                evidence[key] = value
        elif event.operation == "merge_undo" and event.id in merge_undos:
            previous = prior_merge.get(merge_undos[event.id].event_id, {})
            for key, value in previous.items():
                if value is None:
                    evidence.pop(key, None)
                else:
                    evidence[key] = value
    source_ids = list(dict.fromkeys([task.message_id, *[event.message_id for event in events]]))
    sources = [message_read(session, get_or_404(session, Message, mid)) for mid in source_ids]
    is_checkin = (sources[0].source_context or {}).get("kind") == "checkin_activity"
    return TaskDetail(
        legacy=task_record(session, task.id),
        task=TaskRead.model_validate(task),
        platform_observation=public(for_task(session, task), task.status),
        checkin_closed=is_checkin
        and task.suggestion_id in closed_checkin_suggestions(session, [task.suggestion_id], now()),
        materials=(session.get(TaskMaterials, task.id) or TaskMaterials(task_id=task.id)).items,
        source=sources[0],
        sources=sources,
        merge_changes=[merging.change_read(session, change) for change in merges],
        deadline_changes=[change_read(session, change) for change in changes],
        cancellation_changes=[
            cancellations.change_read(session, change)
            for change in session.exec(
                select(CancellationChange).where(CancellationChange.task_id == task.id)
            )
        ],
        events=[EventRead.model_validate(e) for e in events],
        evidence=evidence,
        method=suggestion.method,
    )
