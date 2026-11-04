"""Associate an incoming notice with a task; apply only explicitly selected fields."""

from datetime import date, datetime

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import update
from sqlmodel import Session, select

from campus_inbox.deadlines import require, task_snapshot
from campus_inbox.extraction import extract_rules, is_multiple
from campus_inbox.models import (
    MergeChange,
    MergeProposal,
    Message,
    SplitDraft,
    Suggestion,
    Task,
    TaskEvent,
    now,
)
from campus_inbox.schemas import (
    MergeChangeRead,
    MergePreview,
    MergePreviewRequest,
    TaskFields,
    TaskRead,
)


def source_key(suggestion_id: str, split_draft_id: str | None) -> str:
    return f"split:{split_draft_id}" if split_draft_id else f"suggestion:{suggestion_id}"


def find_change(session: Session, key: str) -> MergeChange | None:
    return session.exec(select(MergeChange).where(MergeChange.source_key == key)).first()


def change_read(session: Session, change: MergeChange) -> MergeChangeRead:
    task = require(session, Task, change.task_id)
    return MergeChangeRead(
        id=change.id,
        suggestion_id=change.suggestion_id,
        split_draft_id=change.split_draft_id,
        task=TaskRead.model_validate(task),
        event_id=change.event_id,
        undo_event_id=change.undo_event_id,
        applied_version=change.applied_version,
        reverted=change.undo_event_id is not None,
        can_undo=change.undo_event_id is None and task.version == change.applied_version,
    )


def pending_source(session: Session, suggestion_id: str, split_id: str | None):
    suggestion = require(session, Suggestion, suggestion_id)
    message = require(session, Message, suggestion.message_id)
    from campus_inbox.connectors.chaoxing import checkin_event

    if checkin_event(session, message):
        raise HTTPException(409, "签到按平台场次处理，不能按同名任务合并。")
    if suggestion.status != "pending":
        raise HTTPException(409, "通知已处理或正在识别，请刷新后核对。")
    if split_id:
        source = require(session, SplitDraft, split_id)
        if (
            source.suggestion_id != suggestion.id
            or source.status != "pending"
            or not source.can_create
        ):
            raise HTTPException(409, "此条草稿不能关联为新任务通知，请核对处理进度。")
        text = source.quote
    else:
        source = suggestion
        if suggestion.action != "create" or is_multiple(message.text):
            raise HTTPException(409, "仅新任务通知可以关联；变更通知请使用对应审核流程。")
        text = message.text
    sent_at = None if message.dedupe_key.startswith("unknown-time:") else message.sent_at
    if extract_rules(text, sent_at, message.timezone).action != "create":
        raise HTTPException(422, "原文涉及其他变更或非任务内容，不能按新任务通知关联。")
    return source, suggestion, message


def preview(
    session: Session, suggestion_id: str, body: MergePreviewRequest, split_id: str | None = None
) -> MergePreview:
    source, suggestion, message = pending_source(session, suggestion_id, split_id)
    task = require(session, Task, body.task_id)
    if task.status not in {"todo", "in_progress", "done"}:
        raise HTTPException(409, "只能关联尚未提交的任务，请先核对状态。")
    snapshot = task_snapshot(task)
    selected = {}
    for key in dict.fromkeys(body.fields):
        value = source.fields.get(key)
        if value in (None, ""):
            raise HTTPException(422, "选中的字段没有明确通知内容，不能用空值清空原任务。")
        selected[key] = value
    if "due_at" in selected and "due_date" in selected:
        raise HTTPException(422, "截止日期与截止时间只能选择一项。")
    if "due_at" in selected and task.due_date is not None:
        selected["due_date"] = None
    if "due_date" in selected and task.due_at is not None:
        selected["due_at"] = None
    try:
        final = TaskFields.model_validate(
            {**{k: snapshot[k] for k in TaskFields.model_fields}, **selected}
        )
    except ValidationError as exc:
        raise HTTPException(422, "通知字段尚不完整或无效，请先核对草稿，或只关联来源。") from exc
    validated = final.model_dump(mode="json")
    # Normalize timestamps so equal instants in different offsets are not treated as conflicts.
    for key in list(selected):
        same = validated[key] == snapshot[key]
        if key == "due_at" and validated[key] and snapshot[key]:
            same = datetime.fromisoformat(validated[key]) == datetime.fromisoformat(snapshot[key])
        if same:
            del selected[key]
        else:
            selected[key] = validated[key]
    warnings = []
    incoming_due = source.fields.get("due_at")
    if task.due_at and incoming_due and task.due_at != datetime.fromisoformat(incoming_due):
        warnings.append("截止时间存在冲突，请对照原文核对；未勾选截止时间时会保留原值。")
    if task.course and source.fields.get("course") and task.course != source.fields["course"]:
        warnings.append("通知与目标任务的课程不同，请确认关联的是同一项任务。")
    if message.dedupe_key.startswith("unknown-time:") and selected:
        warnings.append("原通知发布时间未知，请核对字段后再关联。")
    elif message.sent_at < task.created_at and selected:
        warnings.append("通知发布时间早于任务创建时间，请核对是否为旧通知，避免覆盖较新的信息。")
    proposal = MergeProposal(
        source_key=source_key(suggestion_id, split_id),
        suggestion_id=suggestion.id,
        split_draft_id=split_id,
        source_updated_at=source.updated_at,
        task_id=task.id,
        task_version=task.version,
        before_fields={key: snapshot[key] for key in selected},
        after_fields=selected,
        evidence={
            key: {
                **source.evidence.get(key, {"method": "user", "quote": "用户确认采用通知字段"}),
                "message_id": message.id,
                "confirmed": True,
            }
            for key in selected
        },
    )
    task_read = TaskRead.model_validate(task)
    session.add(proposal)
    session.commit()
    session.refresh(proposal)
    return MergePreview(
        id=proposal.id,
        task=task_read,
        before_fields=proposal.before_fields,
        after_fields=proposal.after_fields,
        warnings=warnings,
        created_at=proposal.created_at,
    )


def values(fields: dict) -> dict:
    result = dict(fields)
    if result.get("due_at"):
        result["due_at"] = datetime.fromisoformat(result["due_at"])
    if result.get("due_date"):
        result["due_date"] = date.fromisoformat(result["due_date"])
    return result


def confirm(session: Session, proposal_id: str) -> MergeChangeRead:
    proposal = require(session, MergeProposal, proposal_id)
    existing = find_change(session, proposal.source_key)
    if existing:
        if existing.proposal_id != proposal.id:
            raise HTTPException(409, "这条通知已通过另一个预览处理，请查看原任务。")
        return change_read(session, existing)
    source, suggestion, message = pending_source(
        session, proposal.suggestion_id, proposal.split_draft_id
    )
    task = require(session, Task, proposal.task_id)
    if source.updated_at != proposal.source_updated_at:
        raise HTTPException(409, "通知或草稿在预览后已更新，请重新预览。")
    if task.version != proposal.task_version or task.status not in {"todo", "in_progress", "done"}:
        raise HTTPException(409, "任务在预览后有变化，请刷新并重新预览。")
    before = task_snapshot(task)
    changed = session.execute(
        update(Task)
        .where(Task.id == task.id, Task.version == proposal.task_version)
        .values(**values(proposal.after_fields), version=Task.version + 1, updated_at=now())
    ).rowcount
    if changed != 1:
        session.rollback()
        raise HTTPException(409, "任务已被修改，请重新预览。")
    session.expire(task)
    session.refresh(task)
    event = TaskEvent(
        task_id=task.id,
        message_id=message.id,
        operation="merge",
        before=before,
        after=task_snapshot(task),
    )
    session.add(event)
    session.flush()
    change = MergeChange(
        source_key=proposal.source_key,
        suggestion_id=suggestion.id,
        split_draft_id=proposal.split_draft_id,
        task_id=task.id,
        proposal_id=proposal.id,
        event_id=event.id,
        applied_version=task.version,
    )
    session.add(change)
    source.status, source.updated_at = "accepted", now()
    session.add(source)
    if proposal.split_draft_id:
        from campus_inbox.splitting import settle

        settle(session, suggestion)
    session.commit()
    session.refresh(change)
    return change_read(session, change)


def undo(session: Session, change_id: str) -> MergeChangeRead:
    change = require(session, MergeChange, change_id)
    if change.undo_event_id:
        return change_read(session, change)
    task = require(session, Task, change.task_id)
    proposal = require(session, MergeProposal, change.proposal_id)
    if task.version != change.applied_version:
        raise HTTPException(409, "关联后任务又有变化，不能直接撤销；请查看历史后人工核对。")
    before = task_snapshot(task)
    changed = session.execute(
        update(Task)
        .where(Task.id == task.id, Task.version == change.applied_version)
        .values(**values(proposal.before_fields), version=Task.version + 1, updated_at=now())
    ).rowcount
    if changed != 1:
        session.rollback()
        raise HTTPException(409, "任务已被修改，不能直接撤销。")
    session.expire(task)
    session.refresh(task)
    event = TaskEvent(
        task_id=task.id,
        message_id=require(session, Suggestion, change.suggestion_id).message_id,
        operation="merge_undo",
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
