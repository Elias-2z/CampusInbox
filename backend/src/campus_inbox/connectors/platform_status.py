"""Read-only platform observations bound to exact task identities."""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from sqlalchemy import literal
from sqlmodel import Field, Session, SQLModel, select

from campus_inbox.models import MergeChange, Message, Suggestion, Task, now


class PlatformObservation(SQLModel, table=True):
    identity: str = Field(primary_key=True)
    id: str = Field(default_factory=lambda: uuid4().hex, index=True)
    status: str
    checked_at: datetime


def task_coordinates(job):
    if job.get("kind") != "task_center" or not isinstance(job.get("url"), str):
        return ""
    try:
        url = urlsplit(job["url"])
        if (
            url.scheme != "https"
            or url.hostname not in {"mooc1.chaoxing.com", "mooc2-ans.chaoxing.com"}
            or url.port
            or url.username
            or url.password
        ):
            return ""
        path = url.path.lower()
        kind = "exam" if "/exam" in path else "work" if "/work/" in path else ""
        if not kind:
            return ""
        values = {}
        for key, value in parse_qsl(url.query):
            key = key.lower().replace("_", "").replace("-", "")
            key = "classid" if key == "clazzid" else key
            if key not in {"courseid", "classid", "workid", "examid", "id"}:
                continue
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
                return ""
            value = str(int(value)) if value.isascii() and value.isdigit() else value
            if key in values and values[key] != value:
                return ""
            values[key] = value
        key = "examid" if kind == "exam" else "workid"
        if values.get(key) and values.get("id") and values[key] != values["id"]:
            return ""
        if (kind == "exam" and values.get("workid")) or (kind == "work" and values.get("examid")):
            return ""
        parts = [
            values.get("courseid"),
            values.get("classid"),
            kind,
            values.get(key) or values.get("id"),
        ]
        return parts if all(parts) else None
    except ValueError:
        return ""


def identity(account, job):
    parts = task_coordinates(job)
    return (
        hashlib.sha256(json.dumps([account, *parts]).encode()).hexdigest()
        if account and parts
        else ""
    )


def suggested(status):
    status = "".join(status.split())
    if status in {
        "已提交",
        "已交",
        "待批阅",
        "待批改",
        "已批阅",
        "已批改",
        "提交成功",
        "已提交作业",
        "已经提交",
        "已提交试卷",
        "已交卷",
        "交卷成功",
        "待阅卷",
        "已阅卷",
    }:
        return "submitted"
    if status in {
        "已退回",
        "退回重做",
        "待重交",
        "需重交",
        "未提交",
        "未交",
        "待重新提交",
        "需重新提交",
        "已退回重做",
        "请重新提交",
    }:
        return "todo"
    return None


def suggested_for_task(status, task_status):
    if "".join(status.split()) in {"未提交", "未交"} and task_status in {
        "todo",
        "in_progress",
        "done",
    }:
        return None
    return suggested(status)


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def observe(session, account, job):
    key, status, stamp = identity(account, job), job.get("platform_status"), job.get("captured_at")
    if status is None:
        status = ""
    if not key or not isinstance(status, str) or len(status) > 100 or not isinstance(stamp, str):
        return
    try:
        checked = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if (
            checked.tzinfo is None
            or checked.utcoffset() is None
            or checked > now() + timedelta(seconds=2)
        ):
            return
    except (ValueError, OverflowError):
        return
    previous = session.get(PlatformObservation, key)
    if previous and aware(previous.checked_at) >= aware(checked):
        return
    row = previous or PlatformObservation(identity=key, status=status.strip(), checked_at=checked)
    row.id, row.status, row.checked_at = uuid4().hex, status.strip(), checked
    session.add(row)


def linked_task_sources(session: Session):
    """Exact platform origins, including active explicit source associations."""
    from campus_inbox.connectors.chaoxing import ChaoxingEvent

    event_link = Message.dedupe_key == literal("chaoxing:") + ChaoxingEvent.identity
    primary = session.exec(
        select(Task.id, ChaoxingEvent)
        .join(Message, Task.message_id == Message.id)
        .join(ChaoxingEvent, event_link)
    ).all()
    merged = session.exec(
        select(MergeChange.task_id, ChaoxingEvent)
        .join(Task, Task.id == MergeChange.task_id)
        .join(Suggestion, Suggestion.id == MergeChange.suggestion_id)
        .join(Message, Message.id == Suggestion.message_id)
        .join(ChaoxingEvent, event_link)
        .where(MergeChange.undo_event_id.is_(None))
    ).all()
    return [*primary, *merged]


def for_task(session: Session, task: Task):
    owners: dict[str, set[str]] = {}
    keys = set()
    for task_id, event in linked_task_sources(session):
        key = identity(event.account_id, event.payload)
        if key:
            owners.setdefault(key, set()).add(task_id)
            if task_id == task.id:
                keys.add(key)
    if len(keys) != 1:
        return None
    key = next(iter(keys))
    return session.get(PlatformObservation, key) if owners[key] == {task.id} else None


def message_identity(session: Session, message: Message) -> str:
    from campus_inbox.connectors.chaoxing import ChaoxingEvent

    if not message.dedupe_key.startswith("chaoxing:"):
        return ""
    event = session.exec(
        select(ChaoxingEvent).where(ChaoxingEvent.identity == message.dedupe_key[9:])
    ).first()
    return identity(event.account_id, event.payload) if event else ""


def task_identities(session: Session) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for task_id, event in linked_task_sources(session):
        key = identity(event.account_id, event.payload)
        if key:
            result.setdefault(task_id, set()).add(key)
    return result


def public(row, task_status=None):
    return (
        {
            "id": row.id,
            "status": row.status,
            "checked_at": aware(row.checked_at),
            "suggested_status": suggested_for_task(row.status, task_status),
        }
        if row
        else None
    )
