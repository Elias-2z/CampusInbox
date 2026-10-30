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

from campus_inbox import cancellations
from campus_inbox.deadlines import change_read
from campus_inbox.extraction import (
    cancellation_supported,
    deadline_supported,
    extract_rules,
    merge_model,
)
from campus_inbox.models import (
    CancellationChange,
    DeadlineChange,
    DeadlineProposal,
    Message,
    ModelSettings,
    Suggestion,
    Task,
    TaskEvent,
    now,
)
from campus_inbox.providers import ProviderError
from campus_inbox.schemas import (
    AcceptSuggestion,
    EventRead,
    ImportMessage,
    InboxItem,
    LocalModelConfig,
    MessageRead,
    TaskDetail,
    TaskFields,
    TaskRead,
    TaskUpdate,
)


def utc_value(value: datetime | None) -> datetime | None:
    return value.astimezone(UTC) if value is not None else None


def get_or_404(session: Session, table, key: str):
    value = session.get(table, key)
    if value is None:
        raise HTTPException(404, "记录不存在")
    return value


def config(session: Session) -> LocalModelConfig:
    settings = session.get(ModelSettings, 1)
    return (
        LocalModelConfig(**settings.model_dump(exclude={"id"})) if settings else LocalModelConfig()
    )


def normalized(value: str) -> str:
    return re.sub(r"[\W_]", "", value).casefold()


def matching_tasks(session: Session, fields: dict) -> list[Task]:
    title = normalized(fields.get("title", ""))
    if len(title) < 2:
        return []
    aliases = {"数据库": "数据库原理", "英语": "大学英语", "高数": "高等数学"}

    def course_key(value: str) -> str:
        return aliases.get(value, value)

    def experiment(value: str) -> int | None:
        from campus_inbox.extraction import number

        match = re.search(r"实验([一二三四五六七八九十两0-9]+)", value)
        return number(match[1]) if match else None

    ranked = []
    for task in session.exec(select(Task).where(Task.status.notin_(["archived", "cancelled"]))):
        course = fields.get("course", "")
        if course and task.course and course_key(course) != course_key(task.course):
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
    change = session.get(DeadlineChange, suggestion.id)
    cancellation = session.get(CancellationChange, suggestion.id)
    message = get_or_404(session, Message, suggestion.message_id)
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
        fields=suggestion.fields,
        evidence=suggestion.evidence,
        warnings=warnings,
        message=MessageRead.model_validate(message),
        matched_tasks=[
            TaskRead.model_validate(t) for t in matching_tasks(session, suggestion.fields)
        ],
        task_id=(
            task.id
            if task
            else change.task_id
            if change
            else cancellation.task_id
            if cancellation
            else None
        ),
        cancellation_supported=suggestion.action == "review"
        and cancellation_supported(message.text),
        cancellation_change=cancellations.change_read(session, cancellation)
        if cancellation
        else None,
        updated_at=suggestion.updated_at,
        deadline_supported=supported,
        deadline_change=change_read(session, change) if change else None,
    )


def analyze(session: Session, suggestion: Suggestion, provider_factory) -> Suggestion:
    message = get_or_404(session, Message, suggestion.message_id)
    result = extract_rules(message.text, message.sent_at, message.timezone)
    settings = config(session)
    text, sent_at, timezone = message.text, message.sent_at, message.timezone
    session.commit()  # Release database transaction before waiting for inference.
    if result.needs_llm and result.action != "ignore":
        if settings.enabled and settings.model and len(text) <= 12000:
            try:
                model = provider_factory(settings).generate_structured(
                    text,
                    sent_at.replace(tzinfo=UTC).isoformat(),
                    timezone,
                )
                result = merge_model(result, model, text, sent_at, timezone)
            except ProviderError as exc:
                result.warnings.append(str(exc))
        else:
            result.warnings.append(
                "通知较长，已保留规则结果，请人工核对。"
                if len(text) > 12000
                else "本地模型兜底未启用或未选择模型，可直接手动完善。"
            )
    if not result.fields.get("title") and result.action == "create":
        result.warnings.append("任务名称待补充。")
    suggestion.fields, suggestion.evidence = result.fields, result.evidence
    suggestion.warnings = list(dict.fromkeys(result.warnings))
    suggestion.action, suggestion.method = result.action, result.method
    suggestion.status, suggestion.updated_at = "pending", now()
    session.add(suggestion)
    session.commit()
    session.refresh(suggestion)
    return suggestion


def import_message(session: Session, body: ImportMessage, provider_factory) -> Suggestion:
    day = body.sent_at.astimezone(ZoneInfo(body.timezone)).date().isoformat()
    key = hashlib.sha256(
        json.dumps(
            [re.sub(r"\s+", " ", body.text), body.source, day, body.timezone],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    existing = session.exec(select(Message).where(Message.dedupe_key == key)).first()
    if existing:
        return session.exec(select(Suggestion).where(Suggestion.message_id == existing.id)).one()
    message = Message(
        text=body.text,
        source=body.source,
        sent_at=utc_value(body.sent_at),
        timezone=body.timezone,
        dedupe_key=key,
    )
    suggestion = Suggestion(message_id=message.id)
    session.add(message)
    session.add(suggestion)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.exec(select(Message).where(Message.dedupe_key == key)).one()
        return session.exec(select(Suggestion).where(Suggestion.message_id == existing.id)).one()
    session.refresh(suggestion)
    return analyze(session, suggestion, provider_factory)


def retry(session: Session, suggestion: Suggestion, provider_factory) -> Suggestion:
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
    return analyze(session, suggestion, provider_factory)


def snapshot(task: Task) -> dict:
    return TaskRead.model_validate(task).model_dump(mode="json")


def accept(session: Session, suggestion: Suggestion, body: AcceptSuggestion) -> Task:
    existing = session.exec(select(Task).where(Task.suggestion_id == suggestion.id)).first()
    if existing:
        return existing
    if suggestion.status != "pending":
        raise HTTPException(409, "建议已处理或仍在识别，请刷新后重试。")
    if suggestion.action != "create":
        raise HTTPException(409, "这条通知需要人工核对，不能直接按新任务入库。")
    fields = body.model_dump(exclude={"allow_duplicate"})
    if matching_tasks(session, fields) and not body.allow_duplicate:
        raise HTTPException(409, "发现可能重复的任务，请核对并明确选择仍然创建。")
    fields["due_at"] = utc_value(body.due_at)
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


def detail(session: Session, task: Task) -> TaskDetail:
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
    by_event = {change.event_id: change for change in changes}
    by_undo = {change.undo_event_id: change for change in changes if change.undo_event_id}
    evidence = dict(suggestion.evidence)
    prior_due = {}
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
            prior_due[event.id] = evidence.get("due_at")
            proposal = get_or_404(session, DeadlineProposal, by_event[event.id].proposal_id)
            evidence["due_at"] = {**proposal.evidence, "confirmed": True}
        elif event.operation == "deadline_undo" and event.id in by_undo:
            previous = prior_due.get(by_undo[event.id].event_id)
            if previous is None:
                evidence.pop("due_at", None)
            else:
                evidence["due_at"] = previous
    source_ids = list(dict.fromkeys([task.message_id, *[event.message_id for event in events]]))
    sources = [MessageRead.model_validate(get_or_404(session, Message, mid)) for mid in source_ids]
    return TaskDetail(
        task=TaskRead.model_validate(task),
        source=sources[0],
        sources=sources,
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
