"""Explicit, versioned cancellation review; never delete a task or its sources."""

from fastapi import HTTPException
from sqlalchemy import update
from sqlmodel import Session

from campus_inbox.deadlines import require, task_snapshot
from campus_inbox.extraction import cancellation_supported
from campus_inbox.models import (
    CancellationChange,
    CancellationProposal,
    Message,
    Suggestion,
    Task,
    TaskEvent,
    now,
)
from campus_inbox.schemas import (
    CancellationChangeRead,
    CancellationPreview,
    CancellationPreviewRequest,
    TaskRead,
)


def change_read(session: Session, change: CancellationChange) -> CancellationChangeRead:
    task = require(session, Task, change.task_id)
    return CancellationChangeRead(
        suggestion_id=change.suggestion_id,
        task=TaskRead.model_validate(task),
        event_id=change.event_id,
        undo_event_id=change.undo_event_id,
        applied_version=change.applied_version,
        reverted=change.undo_event_id is not None,
        can_undo=change.undo_event_id is None and task.version == change.applied_version,
    )


def preview(
    session: Session, suggestion_id: str, body: CancellationPreviewRequest
) -> CancellationPreview:
    suggestion = require(session, Suggestion, suggestion_id)
    message = require(session, Message, suggestion.message_id)
    if suggestion.status != "pending" or suggestion.action != "review":
        raise HTTPException(409, "仅待处理的取消建议可以预览，请刷新后核对。")
    if not cancellation_supported(message.text):
        raise HTTPException(422, "未识别到明确的单任务取消，不能按取消任务处理。")
    task = require(session, Task, body.task_id)
    if task.status not in {"todo", "in_progress"}:
        raise HTTPException(409, "只能取消待办或进行中的任务，请先核对任务状态。")
    proposal = CancellationProposal(
        suggestion_id=suggestion.id,
        task_id=task.id,
        task_version=task.version,
        before_status=task.status,
        suggestion_updated_at=suggestion.updated_at,
    )
    task_read = TaskRead.model_validate(task)
    session.add(proposal)
    session.commit()
    session.refresh(proposal)
    return CancellationPreview(
        id=proposal.id,
        suggestion_id=suggestion.id,
        task=task_read,
        created_at=proposal.created_at,
    )


def confirm(session: Session, proposal_id: str) -> CancellationChangeRead:
    proposal = require(session, CancellationProposal, proposal_id)
    existing = session.get(CancellationChange, proposal.suggestion_id)
    if existing:
        if existing.proposal_id != proposal.id:
            raise HTTPException(409, "这条通知已通过另一个预览处理，请查看任务历史。")
        return change_read(session, existing)
    suggestion = require(session, Suggestion, proposal.suggestion_id)
    task = require(session, Task, proposal.task_id)
    if suggestion.status != "pending" or suggestion.updated_at != proposal.suggestion_updated_at:
        raise HTTPException(409, "通知已重新识别或处理，请重新预览后确认。")
    if (
        task.version != proposal.task_version
        or task.status != proposal.before_status
        or task.status not in {"todo", "in_progress"}
    ):
        raise HTTPException(409, "任务在预览后有变化，请刷新并重新预览。")
    before = task_snapshot(task)
    changed = session.execute(
        update(Task)
        .where(Task.id == task.id, Task.version == proposal.task_version)
        .values(status="cancelled", version=Task.version + 1, updated_at=now())
    ).rowcount
    if changed != 1:
        session.rollback()
        raise HTTPException(409, "任务已被修改，请重新预览。")
    session.expire(task)
    session.refresh(task)
    event = TaskEvent(
        task_id=task.id,
        message_id=suggestion.message_id,
        operation="cancel",
        before=before,
        after=task_snapshot(task),
    )
    session.add(event)
    session.flush()
    change = CancellationChange(
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


def undo(session: Session, suggestion_id: str) -> CancellationChangeRead:
    change = require(session, CancellationChange, suggestion_id)
    if change.undo_event_id is not None:
        return change_read(session, change)
    task = require(session, Task, change.task_id)
    proposal = require(session, CancellationProposal, change.proposal_id)
    if task.version != change.applied_version:
        raise HTTPException(409, "取消后任务又被修改，不能直接撤销；请核对历史后手动编辑。")
    before = task_snapshot(task)
    changed = session.execute(
        update(Task)
        .where(Task.id == task.id, Task.version == change.applied_version)
        .values(status=proposal.before_status, version=Task.version + 1, updated_at=now())
    ).rowcount
    if changed != 1:
        session.rollback()
        raise HTTPException(409, "任务已被修改，不能直接撤销。")
    session.expire(task)
    session.refresh(task)
    event = TaskEvent(
        task_id=task.id,
        message_id=require(session, Suggestion, suggestion_id).message_id,
        operation="cancel_undo",
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
