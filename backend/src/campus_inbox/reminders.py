"""In-app reminders derived from current tasks, with persistent per-occurrence decisions."""

import hashlib
from datetime import UTC, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import or_
from sqlmodel import Session, select

from campus_inbox.connectors.chaoxing import closed_checkin_suggestions
from campus_inbox.models import DateReminderState, ReminderSettings, ReminderState, Task
from campus_inbox.schemas import (
    ReminderAction,
    ReminderConfig,
    ReminderFeed,
    ReminderRead,
    TaskRead,
)


def config(session: Session) -> ReminderConfig:
    settings = session.get(ReminderSettings, 1)
    return ReminderConfig(**settings.model_dump(exclude={"id"})) if settings else ReminderConfig()


def occurrence(task_id: str, due: datetime, phase: str) -> str:
    value = f"{task_id}|{due.astimezone(UTC).isoformat()}|{phase}"
    return hashlib.sha256(value.encode()).hexdigest()


def feed(session: Session, current: datetime) -> ReminderFeed:
    current = current.astimezone(UTC)
    local = current.astimezone(ZoneInfo("Asia/Shanghai"))
    settings = config(session)
    if not settings.enabled:
        return ReminderFeed(config=settings, items=[], evaluated_at=current)
    tasks = session.exec(
        select(Task).where(
            Task.status.in_(["todo", "in_progress", "done"]),
            or_(
                Task.due_at <= current + timedelta(minutes=settings.lead_minutes),
                Task.due_date <= (local + timedelta(minutes=settings.lead_minutes)).date(),
            ),
        )
    ).all()
    closed_checkins = closed_checkin_suggestions(
        session, [task.suggestion_id for task in tasks], current
    )
    eligible = []
    for task in tasks:
        if task.suggestion_id in closed_checkins:
            continue
        if task.due_date is not None:
            # The date is a civil day, not an assertion about a deadline time.
            phase = "overdue" if task.due_date < local.date() else "upcoming"
            value = f"{task.id}|date:{task.due_date.isoformat()}|{phase}"
            key = hashlib.sha256(value.encode()).hexdigest()
        else:
            phase = "overdue" if task.due_at <= current else "upcoming"
            key = occurrence(task.id, task.due_at, phase)
        if phase == "overdue" and not settings.overdue_enabled:
            continue
        eligible.append((task, phase, key))
    # Chunk IN queries to stay below SQLite parameter limits with large task lists.
    states = {}
    for start in range(0, len(eligible), 500):
        ids = [item[2] for item in eligible[start : start + 500]]
        states.update(
            {
                row.id: row
                for row in session.exec(select(ReminderState).where(ReminderState.id.in_(ids)))
            }
        )
        states.update(
            {
                row.id: row
                for row in session.exec(
                    select(DateReminderState).where(DateReminderState.id.in_(ids))
                )
            }
        )
    items = []
    for task, phase, key in eligible:
        state = states.get(key)
        snoozed = state and state.snoozed_until and state.snoozed_until > current
        status = "read" if state and state.is_read else "snoozed" if snoozed else "unread"
        items.append(
            ReminderRead(
                id=key,
                task=TaskRead.model_validate(task),
                phase=phase,
                status=status,
                due_at=task.due_at,
                due_date=task.due_date,
                read_at=state.read_at if state else None,
                snoozed_until=state.snoozed_until if snoozed else None,
                version=state.version if state else 0,
            )
        )
    items.sort(
        key=lambda item: (
            item.phase != "overdue",
            item.due_date or item.due_at.astimezone(local.tzinfo).date(),
            item.due_at is not None,
            item.due_at or datetime.combine(item.due_date, time.min, tzinfo=UTC),
            item.task.id,
        )
    )
    return ReminderFeed(config=settings, items=items, evaluated_at=current)


def act(
    session: Session,
    reminder_id: str,
    operation: Literal["read", "snooze", "restore"],
    body: ReminderAction,
    current: datetime,
) -> ReminderRead:
    item = next((item for item in feed(session, current).items if item.id == reminder_id), None)
    if item is None:
        raise HTTPException(409, "提醒已随截止时间、任务状态或设置变化，请刷新后核对。")
    state = session.get(ReminderState, reminder_id)
    if operation == "read" and item.status == "read":
        return item
    if operation == "restore" and item.status == "unread":
        return item
    if item.version != body.expected_version:
        raise HTTPException(409, "提醒已在其他地方处理，请刷新后再操作。")
    if state is None:
        if item.due_date is not None:
            state = session.get(DateReminderState, reminder_id)
            if state is None:
                state = DateReminderState(
                    id=item.id, task_id=item.task.id, due_date=item.due_date, phase=item.phase
                )
        else:
            state = ReminderState(
                id=item.id, task_id=item.task.id, due_at=item.due_at, phase=item.phase
            )
    state.is_read = operation == "read"
    state.read_at = current if operation == "read" else None
    state.snoozed_until = (
        current + timedelta(minutes=body.snooze_minutes) if operation == "snooze" else None
    )
    state.version += 1
    state.updated_at = current
    session.add(state)
    session.flush()
    result = next(item for item in feed(session, current).items if item.id == reminder_id)
    session.commit()
    return result
