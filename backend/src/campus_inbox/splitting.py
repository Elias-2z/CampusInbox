"""Persist numbered task drafts, then create each task only after its own review."""

from datetime import datetime

from fastapi import HTTPException
from sqlmodel import Session, select

from campus_inbox import services
from campus_inbox.extraction import COURSES, extract_rules, numbered_parts
from campus_inbox.models import MergeChange, Message, SplitDraft, Suggestion, Task, TaskEvent, now
from campus_inbox.schemas import SplitAccept, SplitEdit, TaskFields


def drafts(session: Session, suggestion_id: str) -> list[SplitDraft]:
    return list(
        session.exec(
            select(SplitDraft)
            .where(SplitDraft.suggestion_id == suggestion_id)
            .order_by(SplitDraft.position)
        )
    )


def generate(session: Session, suggestion_id: str):
    suggestion = services.get_or_404(session, Suggestion, suggestion_id)
    existing = drafts(session, suggestion_id)
    if existing:
        return services.inbox_item(session, suggestion)
    from campus_inbox.legacy_records import context

    legacy = context(session, suggestion.message_id)
    if legacy and not legacy["time_known"]:
        raise HTTPException(409, "原消息发布时间未知，不能按迁移时间重新拆分识别。")
    if suggestion.status != "pending" or suggestion.action != "review":
        raise HTTPException(409, "仅待审核的多任务通知可以拆分。")
    message = services.get_or_404(session, Message, suggestion.message_id)
    header, parts = numbered_parts(message.text)
    if not parts:
        raise HTTPException(422, "请使用从 1 开始连续编号的 2–20 个独立条目，每项另起一行。")
    courses = [course for course in COURSES if course in header]
    # The dictionary contains aliases, e.g. 数据库 is contained in 数据库原理.
    courses = [c for c in courses if not any(c != other and c in other for other in courses)]
    for position, quote in enumerate(parts, start=1):
        sent_at = None if message.dedupe_key.startswith("unknown-time:") else message.sent_at
        result = extract_rules(quote, sent_at, message.timezone)
        if not result.fields.get("course") and len(courses) == 1:
            result.fields["course"] = courses[0]
            result.evidence["course"] = {"method": "rule", "quote": courses[0]}
        can_create = result.action == "create"
        warnings = list(result.warnings)
        if not can_create:
            warnings.append("此条涉及变更、取消或非任务内容，不创建新任务。请另行导入处理或跳过。")
        session.add(
            SplitDraft(
                suggestion_id=suggestion.id,
                position=position,
                quote=quote,
                can_create=can_create,
                fields=result.fields,
                evidence=result.evidence,
                warnings=list(dict.fromkeys(warnings)),
            )
        )
    suggestion.updated_at = now()
    session.add(suggestion)
    session.commit()
    session.refresh(suggestion)
    return services.inbox_item(session, suggestion)


def pending(session: Session, draft: SplitDraft, revision: datetime) -> Suggestion:
    suggestion = services.get_or_404(session, Suggestion, draft.suggestion_id)
    if suggestion.status != "pending" or draft.status != "pending":
        raise HTTPException(409, "条目或通知已处理，请刷新后核对。")
    if draft.updated_at != revision:
        raise HTTPException(409, "草稿已在其他地方修改，请刷新后核对，避免覆盖新内容。")
    return suggestion


def reviewed(draft: SplitDraft, body: TaskFields) -> tuple[dict, dict]:
    fields = body.model_dump(mode="json", include=set(TaskFields.model_fields))
    evidence = dict(draft.evidence)
    for key, value in fields.items():
        original = draft.fields.get(key)
        same = value == original
        if key == "due_at" and value and original:
            same = datetime.fromisoformat(value) == datetime.fromisoformat(original)
        if not same:
            evidence[key] = {
                "method": "user",
                "quote": "用户审核填写" if value not in (None, "") else "用户审核清空",
                "original": evidence.get(key),
            }
    return fields, evidence


def save(session: Session, draft_id: str, body: SplitEdit):
    draft = services.get_or_404(session, SplitDraft, draft_id)
    suggestion = pending(session, draft, body.expected_updated_at)
    if not draft.can_create:
        raise HTTPException(409, "此条不能按新任务处理，请核对原文或跳过。")
    draft.fields, draft.evidence = reviewed(draft, body)
    draft.updated_at = now()
    session.add(draft)
    session.commit()
    return services.inbox_item(session, suggestion)


def settle(session: Session, suggestion: Suggestion):
    session.flush()
    items = drafts(session, suggestion.id)
    if all(item.status != "pending" for item in items):
        suggestion.status = "accepted" if any(i.status == "accepted" for i in items) else "ignored"
    suggestion.updated_at = now()
    session.add(suggestion)


def accept(session: Session, draft_id: str, body: SplitAccept) -> Task:
    draft = services.get_or_404(session, SplitDraft, draft_id)
    if draft.status == "accepted":
        if draft.task_id:
            return services.get_or_404(session, Task, draft.task_id)
        merge = session.exec(
            select(MergeChange).where(MergeChange.split_draft_id == draft.id)
        ).first()
        if merge:
            return services.get_or_404(session, Task, merge.task_id)
    suggestion = pending(session, draft, body.expected_updated_at)
    if not draft.can_create:
        raise HTTPException(409, "此条涉及变更或其他内容，不能作为新任务创建。")
    fields, evidence = reviewed(draft, body)
    if services.matching_tasks(session, fields) and not body.allow_duplicate:
        raise HTTPException(409, "发现可能重复的任务，请核对并明确选择仍然创建独立任务。")
    values = body.model_dump(include=set(TaskFields.model_fields))
    values["due_at"] = services.utc_value(body.due_at)
    task = Task(**values, suggestion_id=suggestion.id, message_id=suggestion.message_id)
    session.add(task)
    session.flush()
    draft.fields, draft.evidence = fields, evidence
    draft.status, draft.task_id, draft.updated_at = "accepted", task.id, now()
    session.add(draft)
    session.add(
        TaskEvent(
            task_id=task.id,
            message_id=suggestion.message_id,
            operation="create",
            after=services.snapshot(task),
        )
    )
    settle(session, suggestion)
    session.commit()
    session.refresh(task)
    return task


def decide(session: Session, draft_id: str, revision: datetime, reopen: bool = False):
    draft = services.get_or_404(session, SplitDraft, draft_id)
    suggestion = services.get_or_404(session, Suggestion, draft.suggestion_id)
    expected, target = ("ignored", "pending") if reopen else ("pending", "ignored")
    if draft.status != expected or draft.updated_at != revision:
        raise HTTPException(409, "条目已处理或修改，请刷新后核对。")
    if not reopen and suggestion.status != "pending":
        raise HTTPException(409, "请先将通知恢复为待处理。")
    draft.status, draft.updated_at = target, now()
    session.add(draft)
    if reopen:
        suggestion.status = "pending"
    settle(session, suggestion)
    session.commit()
    return services.inbox_item(session, suggestion)
