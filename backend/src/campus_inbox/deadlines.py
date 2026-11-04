"""Audited deadline proposals. No task changes until an explicit confirmation."""

from datetime import UTC, date, datetime

from fastapi import HTTPException
from sqlalchemy import update
from sqlmodel import Session

from campus_inbox.extraction import deadline_supported, extract_rules
from campus_inbox.legacy_records import context as legacy_context
from campus_inbox.models import (
    DateDeadlineChange,
    DateDeadlineProposal,
    DeadlineChange,
    DeadlineProposal,
    Message,
    Suggestion,
    Task,
    TaskEvent,
    now,
)
from campus_inbox.schemas import (
    DeadlineChangeRead,
    DeadlinePreview,
    DeadlinePreviewRequest,
    TaskRead,
)


def require(session: Session, table, key: str):
    record = session.get(table, key)
    if record is None:
        raise HTTPException(404, "记录不存在")
    return record


def task_snapshot(task: Task) -> dict:
    return TaskRead.model_validate(task).model_dump(mode="json")


def find_change(session: Session, suggestion_id: str):
    return session.get(DeadlineChange, suggestion_id) or session.get(
        DateDeadlineChange, suggestion_id
    )


def find_proposal(session: Session, proposal_id: str):
    proposal = session.get(DeadlineProposal, proposal_id) or session.get(
        DateDeadlineProposal, proposal_id
    )
    if proposal is None:
        raise HTTPException(404, "记录不存在")
    return proposal


def change_read(
    session: Session, change: DeadlineChange | DateDeadlineChange
) -> DeadlineChangeRead:
    task = require(session, Task, change.task_id)
    return DeadlineChangeRead(
        suggestion_id=change.suggestion_id,
        task=TaskRead.model_validate(task),
        event_id=change.event_id,
        undo_event_id=change.undo_event_id,
        applied_version=change.applied_version,
        reverted=change.undo_event_id is not None,
        can_undo=change.undo_event_id is None and task.version == change.applied_version,
    )


def preview(session: Session, suggestion_id: str, body: DeadlinePreviewRequest) -> DeadlinePreview:
    suggestion = require(session, Suggestion, suggestion_id)
    message = require(session, Message, suggestion.message_id)
    if suggestion.status != "pending" or suggestion.action != "review":
        raise HTTPException(409, "仅待处理的变更建议可以预览，请刷新后核对。")
    if not deadline_supported(message.text):
        raise HTTPException(422, "这条通知涉及取消、其他变更或多个任务，不能按截止时间变更处理。")
    task = require(session, Task, body.task_id)
    if task.status not in {"todo", "in_progress", "done"}:
        raise HTTPException(409, "只能更新尚未提交的任务，请先核对任务状态。")
    context = legacy_context(session, message.id)
    unknown_time = message.dedupe_key.startswith("unknown-time:") or (
        context is not None and not context["time_known"]
    )
    if unknown_time and body.due_at is None and body.due_date is None:
        raise HTTPException(409, "原消息发布时间未知，请核对后手动填写新截止日期或时间。")
    fields, source_evidence = suggestion.fields, suggestion.evidence
    if not unknown_time and not (fields.get("due_at") or fields.get("due_date")):
        # Older drafts may have been saved before date-only changes were recognized.
        parsed = extract_rules(
            message.text,
            message.sent_at,
            message.timezone,
            scoped_deadline=message.dedupe_key.startswith("chaoxing:"),
        )
        fields, source_evidence = parsed.fields, parsed.evidence
    default_due = (
        datetime.fromisoformat(fields["due_at"])
        if not unknown_time and fields.get("due_at")
        else None
    )
    default_date = (
        date.fromisoformat(fields["due_date"])
        if not unknown_time and fields.get("due_date")
        else None
    )
    if body.due_at is None and body.due_date is None:
        due, due_date = default_due, default_date
    else:
        due, due_date = body.due_at, body.due_date
    if due is None and due_date is None:
        raise HTTPException(422, "未找到明确的新截止日期，请核对原文后手动填写。")
    if due is not None:
        due = due.astimezone(UTC)
    if task.due_at == due and task.due_date == due_date:
        raise HTTPException(422, "新旧截止时间相同，无需更新。")
    evidence = None
    if due is not None and due == default_due:
        evidence = source_evidence.get("due_at")
    elif due_date is not None and due_date == default_date:
        evidence = source_evidence.get("due_date")
    evidence = evidence or {"method": "user", "quote": "用户核对通知后填写新截止时间"}
    proposal_data = dict(
        suggestion_id=suggestion.id,
        task_id=task.id,
        task_version=task.version,
        before_due_at=task.due_at,
        proposed_due_at=due,
        suggestion_updated_at=suggestion.updated_at,
        evidence={**evidence, "message_id": message.id},
    )
    if task.due_date is not None or due_date is not None:
        proposal = DateDeadlineProposal(
            **proposal_data, before_due_date=task.due_date, proposed_due_date=due_date
        )
    else:
        proposal = DeadlineProposal(**proposal_data)
    task_read = TaskRead.model_validate(task)
    warnings = []
    if due is not None and task.due_at is not None and due < task.due_at:
        warnings.append("新时间早于当前截止时间，请核对是否为提前通知或旧消息。")
    if due_date is not None and task.due_date is not None and due_date < task.due_date:
        warnings.append("新日期早于当前截止日期，请核对是否为提前通知或旧消息。")
    if due is not None and due < now():
        warnings.append("新截止时间已经过去，请核对原通知和时间。")
    session.add(proposal)
    session.commit()
    session.refresh(proposal)
    return DeadlinePreview(
        id=proposal.id,
        suggestion_id=suggestion.id,
        task=task_read,
        proposed_due_at=proposal.proposed_due_at,
        proposed_due_date=due_date,
        warnings=warnings,
        evidence=proposal.evidence,
        created_at=proposal.created_at,
    )


def confirm(session: Session, proposal_id: str) -> DeadlineChangeRead:
    proposal = find_proposal(session, proposal_id)
    existing = find_change(session, proposal.suggestion_id)
    if existing:
        if existing.proposal_id != proposal.id:
            raise HTTPException(409, "这条通知已通过另一个预览处理，请查看任务历史。")
        return change_read(session, existing)
    suggestion = require(session, Suggestion, proposal.suggestion_id)
    task = require(session, Task, proposal.task_id)
    if suggestion.status != "pending" or suggestion.updated_at != proposal.suggestion_updated_at:
        raise HTTPException(409, "通知已重新识别或处理，请重新预览后确认。")
    if task.status not in {"todo", "in_progress", "done"}:
        raise HTTPException(409, "任务状态已变化，请重新核对。")
    if (
        task.version != proposal.task_version
        or task.due_at != proposal.before_due_at
        or task.due_date != getattr(proposal, "before_due_date", None)
    ):
        raise HTTPException(409, "任务在预览后被修改，请刷新并重新预览，避免覆盖新内容。")
    before = task_snapshot(task)
    changed = session.execute(
        update(Task)
        .where(
            Task.id == task.id,
            Task.version == proposal.task_version,
        )
        .values(
            due_at=proposal.proposed_due_at,
            due_date=getattr(proposal, "proposed_due_date", None),
            version=Task.version + 1,
            updated_at=now(),
        )
    ).rowcount
    if changed != 1:
        session.rollback()
        raise HTTPException(409, "任务已被修改，请重新预览。")
    session.expire(task)
    session.refresh(task)
    event = TaskEvent(
        task_id=task.id,
        message_id=suggestion.message_id,
        operation="deadline_update",
        before=before,
        after=task_snapshot(task),
    )
    session.add(event)
    session.flush()
    change_type = (
        DateDeadlineChange if isinstance(proposal, DateDeadlineProposal) else DeadlineChange
    )
    change = change_type(
        suggestion_id=suggestion.id,
        task_id=task.id,
        proposal_id=proposal.id,
        event_id=event.id,
        applied_version=task.version,
    )
    suggestion.status, suggestion.updated_at = "accepted", now()
    session.add(suggestion)
    session.add(change)
    session.commit()
    session.refresh(change)
    return change_read(session, change)


def undo(session: Session, suggestion_id: str) -> DeadlineChangeRead:
    change = find_change(session, suggestion_id)
    if change is None:
        raise HTTPException(404, "记录不存在")
    if change.undo_event_id is not None:
        return change_read(session, change)
    task = require(session, Task, change.task_id)
    proposal = find_proposal(session, change.proposal_id)
    if task.version != change.applied_version:
        raise HTTPException(409, "更新后任务又被修改，不能直接撤销；请核对历史后手动编辑。")
    before = task_snapshot(task)
    changed = session.execute(
        update(Task)
        .where(
            Task.id == task.id,
            Task.version == change.applied_version,
        )
        .values(
            due_at=proposal.before_due_at,
            due_date=getattr(proposal, "before_due_date", None),
            version=Task.version + 1,
            updated_at=now(),
        )
    ).rowcount
    if changed != 1:
        session.rollback()
        raise HTTPException(409, "任务已被修改，不能直接撤销。")
    session.expire(task)
    session.refresh(task)
    event = TaskEvent(
        task_id=task.id,
        message_id=require(session, Suggestion, suggestion_id).message_id,
        operation="deadline_undo",
        before=before,
        after=task_snapshot(task),
    )
    session.add(event)
    session.flush()
    change.undo_event_id = event.id
    session.add(change)
    session.commit()
    session.refresh(change)
    return change_read(session, change)
