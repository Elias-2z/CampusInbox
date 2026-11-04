"""Read-only deadline calendar and RFC 5545 exports; no invented event duration."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlmodel import Session, select

from campus_inbox.models import Task, now
from campus_inbox.schemas import CalendarFeed, TaskRead

CalendarScope = Literal[
    "active",
    "all",
    "todo",
    "in_progress",
    "done",
    "submitted",
    "completed",
    "cancelled",
    "archived",
]
STATUS_NAMES = {
    "todo": "待办",
    "in_progress": "进行中",
    "done": "已做完",
    "submitted": "已提交",
    "completed": "已完成",
    "cancelled": "已取消",
    "archived": "已归档",
}


def feed(
    session: Session, start: date, end: date, timezone: str, status: CalendarScope, course: str
) -> CalendarFeed:
    if not 0 < (end - start).days <= 62:
        raise HTTPException(422, "日期范围须为 1–62 天，结束日期不包含在内。")
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(422, "日历时区无效，请核对浏览器时区。") from exc
    try:
        lower = datetime.combine(start, time.min, zone).astimezone(UTC)
        upper = datetime.combine(end, time.min, zone).astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise HTTPException(422, "日期超出当前时区可转换范围。") from exc
    query = select(Task)
    if status == "active":
        query = query.where(Task.status.in_(["todo", "in_progress", "done"]))
    elif status == "all":
        query = query.where(Task.status != "archived")
    else:
        query = query.where(Task.status == status)
    if course:
        query = query.where(Task.course == course)
    dated = session.exec(
        query.where(
            or_(
                and_(Task.due_at >= lower, Task.due_at < upper),
                and_(Task.due_date >= start, Task.due_date < end),
            )
        )
    ).all()
    undated = session.exec(
        query.where(Task.due_at.is_(None), Task.due_date.is_(None)).order_by(
            Task.created_at, Task.id
        )
    ).all()
    task_reads = [TaskRead.model_validate(task) for task in dated]
    task_reads.sort(
        key=lambda task: (
            task.due_date or task.due_at.astimezone(zone).date(),
            task.due_at is not None,
            task.due_at or datetime.min.replace(tzinfo=UTC),
            task.id,
        )
    )
    return CalendarFeed(
        start=start,
        end=end,
        timezone=timezone,
        tasks=task_reads,
        undated=[TaskRead.model_validate(task) for task in undated],
    )


def text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(c for c in value if c in "\n\t" or ord(c) >= 32 and ord(c) != 127)
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def fold(line: str) -> str:
    lines, current, size = [], "", 0
    for character in line:
        length = len(character.encode("utf-8"))
        if size + length > 75:
            lines.append(current)
            current, size = " ", 1
        current += character
        size += length
    lines.append(current)
    return "\r\n".join(lines)


def utc(value: datetime) -> str:
    value = value.astimezone(UTC)
    return (
        f"{value.year:04d}{value.month:02d}{value.day:02d}T"
        f"{value.hour:02d}{value.minute:02d}{value.second:02d}Z"
    )


def export(result: CalendarFeed, stamp: datetime | None = None) -> bytes:
    if not result.tasks:
        raise HTTPException(422, "当前日期范围和筛选下，没有带截止时间的任务可导出。")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//CampusInbox//Deadline Calendar//ZH",
        "CALSCALE:GREGORIAN",
    ]
    for task in result.tasks:
        status = STATUS_NAMES[task.status]
        prefix = (
            f"[{status}] "
            if task.status in {"done", "submitted", "completed", "cancelled", "archived"}
            else ""
        )
        summary = f"{prefix}{task.course + ' · ' if task.course else ''}{task.title}"
        details = ["CampusInbox 任务截止时间", f"状态：{status}"]
        for label, value in [
            ("课程", task.course),
            ("任务内容", task.description),
            ("提交平台", task.submission_platform),
            ("提交方式", task.submission_method),
            ("文件要求", task.file_requirements),
            ("备注", task.remarks),
        ]:
            if value:
                details.append(f"{label}：{value}")
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{task.id}@campusinbox.local",
                f"DTSTAMP:{utc(stamp or now())}",
                f"SEQUENCE:{task.version}",
                f"CREATED:{utc(task.created_at)}",
                f"LAST-MODIFIED:{utc(task.updated_at)}",
                f"DTSTART;VALUE=DATE:{task.due_date:%Y%m%d}"
                if task.due_date
                else f"DTSTART:{utc(task.due_at)}",
                f"SUMMARY:{text(summary)}",
                f"DESCRIPTION:{text(chr(10).join(details))}",
                "TRANSP:TRANSPARENT",
                "STATUS:CANCELLED" if task.status == "cancelled" else "STATUS:CONFIRMED",
            ]
        )
        if task.location:
            lines.append(f"LOCATION:{text(task.location)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return ("\r\n".join(fold(line) for line in lines) + "\r\n").encode("utf-8")


def filename(start: date, end: date) -> str:
    return f"campusinbox-{start:%Y%m%d}-{end - timedelta(days=1):%Y%m%d}.ics"
