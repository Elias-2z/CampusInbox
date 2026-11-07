"""Durable intake for learning-platform events; never infer an unknown sent time."""

import base64
import binascii
import hashlib
import json
import re
import threading
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import JSON, Column, update
from sqlmodel import Field, Session, SQLModel, select

from campus_inbox import services
from campus_inbox.attachments import local_text, original_path, persist_original
from campus_inbox.connectors import platform_status
from campus_inbox.extraction import (
    CHANGE_WORDS,
    DEADLINE_CHANGE,
    Extraction,
    class_schedule_evidence,
    extract_rules,
)
from campus_inbox.models import (
    Attachment,
    LocalAttachmentExtraction,
    Message,
    SplitDraft,
    Suggestion,
    Task,
    now,
    uid,
)
from campus_inbox.schemas import ImportMessage

UNPROCESSED_ATTACHMENT = "原附件已保存，内容尚未解析，请下载后核对。"
REMINDER_TITLE = re.compile(r"(?:作业|任务|考试|测验)(?:即将)?(?:结束|截止|到期)提醒[：:！!。]?")
REMINDER_COUNTDOWN = re.compile(
    r"(?:你的|您的|该|本次)?(?:作业|任务|考试|测验)"
    r"(?:还有|还剩|剩余)\s*(?:[\d一二三四五六七八九十百半两]+\s*(?:个)?"
    r"(?:小时|分钟|天|日|周)\s*)+(?:就要|即将|将)?(?:结束|截止|到期)"
)
COUNTDOWN = re.compile(
    r"(^|[；;，,\n])[ \t]*(?:剩余|还剩)\s*[:：]?\s*"
    r"(?:\d+(?:\.\d+)?\s*(?:天|小时|分钟|时|分|秒)\s*)+(?=$|[；;，,\n])"
)


def without_countdown(value):
    if not isinstance(value, str):
        return value
    return re.sub(r"[；;]\s*[；;]+", "；", COUNTDOWN.sub(r"\1", value)).strip(" \t\r\n；;，,")


def public_notice_original(item):
    url = item.get("url")
    if not isinstance(url, str) or not url.startswith("https://") or len(url) > 2048:
        return False
    if re.fullmatch(r"https://d0\.cldisk\.com/download/[a-fA-F0-9]{32}", url):
        return True
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return (
        item.get("kind") == "inline_image"
        and parts.netloc == "p.ananas.chaoxing.com"
        and not parts.fragment
        and re.search(r"\.(?:png|jpe?g|bmp|webp)$", parts.path, re.I) is not None
    )


def reminder_only(job):
    text = job.get("text")
    if (
        job.get("kind") not in ("chat", "course_notice")
        or job.get("attachments")
        or job.get("media_pending_count")
        or not isinstance(text, str)
        or not 2 <= len(text.strip()) <= 20000
    ):
        return False
    if CHANGE_WORDS.search(text) or DEADLINE_CHANGE.search(text):
        return False
    lines = [line.strip() for line in text.replace("\u200b", "").splitlines() if line.strip()]
    if lines and REMINDER_TITLE.fullmatch(lines[0]):
        lines = lines[1:]
    course = task = countdown = False
    for line in lines:
        if re.fullmatch(r"课程名称\s*[：:]\s*[^\s。；;，,！!]+", line):
            course = True
        elif re.fullmatch(r"(?:作业|任务|考试|测验)名称\s*[：:]\s*[^\s。；;，,！!]+", line):
            task = True
        else:
            for clause in (part.strip() for part in re.split(r"[。；;，,！!]", line)):
                if REMINDER_COUNTDOWN.fullmatch(clause) or re.fullmatch(
                    r"(?:本次|你的|您的)?(?:作业|任务|考试|测验)(?:已结束|已截止|已收卷)",
                    clause,
                ):
                    countdown = True
                elif clause and not re.fullmatch(r"请(?:尽快|及时)(?:作答|完成|提交|查看)", clause):
                    return False
    return course and task and countdown


def verified_empty_notice(job):
    return (
        job["kind"] == "course_notice"
        and job.get("empty_body_ready") is True
        and isinstance(job.get("text"), str)
        and not job["text"].strip()
        and isinstance(job.get("title"), str)
        and 1 <= len(job["title"].strip()) <= 160
        and not job.get("attachments")
        and not job.get("media_pending_count")
    )


def attachment_only_notice(job):
    files = job.get("attachments")
    return (
        job["kind"] == "course_notice"
        and isinstance(job.get("text"), str)
        and not job["text"].strip()
        and isinstance(files, list)
        and any(isinstance(item, dict) and item.get("content_base64") for item in files)
    )


def closed_task_status(job):
    status = "".join(str(job.get("platform_status") or "").split())
    return job["kind"] == "task_center" and (
        platform_status.suggested(status) == "submitted"
        or status
        in {
            "已结束",
            "已经结束",
            "已截止",
            "已过期",
            "已关闭",
            "已收卷",
            "已完成",
            "已过教师设置的截止时间",
        }
    )


def event_identity(account_id, job):
    content = dict(job)
    if job["kind"] == "task_center":
        content.pop("captured_at", None)
    previous = hashlib.sha256(
        json.dumps([account_id, content], ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    task_key = platform_status.identity(account_id, job)
    if task_key:
        content.pop("message_id", None)
        content["url"] = task_key
        for field in ("text", "description"):
            if field in content:
                content[field] = without_countdown(content[field])
        evidence = content.get("field_evidence")
        if isinstance(evidence, dict) and "deadline" in evidence:
            content["field_evidence"] = {
                **evidence,
                "deadline": without_countdown(evidence["deadline"]),
            }
    old_canonical = hashlib.sha256(
        json.dumps([account_id, content], ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    if verified_empty_notice(job):
        content.pop("empty_body_ready")
    files = content.get("attachments")
    if isinstance(files, list) and all(
        isinstance(item, dict) and item.get("name") and item.get("url") for item in files
    ):
        # Raw bytes and extractor bookkeeping may arrive in a later scan.
        content["attachments"] = [
            {
                key: value
                for key, value in item.items()
                if key not in {"content_base64", "status", "text", "error"}
            }
            for item in files
        ]
    current = hashlib.sha256(
        json.dumps([account_id, content], ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return current, old_canonical, previous


def fill_originals(previous, incoming):
    old, new = previous.get("attachments"), incoming.get("attachments")
    if not isinstance(old, list) or not isinstance(new, list) or len(old) != len(new):
        return old, False, False
    result, changed = [], False
    for existing, candidate in zip(old, new, strict=True):
        if (
            not isinstance(existing, dict)
            or not isinstance(candidate, dict)
            or not existing.get("url")
            or not existing.get("name")
            or any(existing.get(key) != candidate.get(key) for key in ("name", "url"))
        ):
            return old, False, False
        data = candidate.get("content_base64")
        prior_data = existing.get("content_base64")
        if data and prior_data and data != prior_data:
            return old, False, True
        if isinstance(data, str) and data and not prior_data:
            result.append({**existing, "content_base64": data, "status": "downloaded"})
            changed = True
        else:
            result.append(existing)
    return result, changed, False


def task_content_identity(account_id, job):
    """Ignore only the status field and its generated display text."""
    status = job.get("platform_status")
    text = job.get("text")
    title = job.get("title")
    description = job.get("description")
    if isinstance(status, str) and status and isinstance(text, str):
        lines = text.split("\n")
        suffix = "；" + status
        if (
            isinstance(title, str)
            and lines[0].startswith(title + "；")
            and lines[0].endswith(suffix)
            and not (isinstance(description, str) and description and text.startswith(description))
        ):
            lines[0] = lines[0][: -len(suffix)]
        if (
            isinstance(description, str)
            and description
            and text.count("\n") > description.count("\n")
        ):
            end = len(lines)
            while end > 1 and lines[end - 1].startswith("附件："):
                end -= 1
            if (
                end > 1
                and lines[end - 1] == status
                and not "\n".join(lines[:end]).endswith(description)
            ):
                del lines[end - 1]
        text = "\n".join(lines)
    return event_identity(account_id, {**job, "platform_status": "", "text": text})[0]


def notice_media_identity(account_id, job):
    """Compare coverage reports only for a notice with the same listed originals."""
    files = job.get("attachments")
    pending = job.get("media_pending_count")
    if (
        job["kind"] == "course_notice"
        and type(pending) is int
        and pending >= 0
        and isinstance(files, list)
        and files
        and all(isinstance(file, dict) and file.get("name") and file.get("url") for file in files)
    ):
        return event_identity(account_id, {**job, "media_pending_count": 0})[0]
    return None


class ChaoxingEvent(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    identity: str = Field(unique=True, index=True)
    account_id: str
    payload: dict = Field(sa_column=Column(JSON, nullable=False))
    state: str = Field(default="pending", index=True)
    suggestion_id: str | None = Field(default=None, foreign_key="suggestion.id")
    received_at: datetime = Field(default_factory=now)


def attachment_info(payload):
    items = payload.get("attachments")
    if not isinstance(items, list):
        return []
    return [
        {
            "index": index,
            "name": str(item.get("name") or "附件")[:180],
            "available": isinstance(item.get("content_base64"), str)
            and bool(item["content_base64"]),
            "fetchable": not item.get("content_base64")
            and (
                payload.get("kind") == "task_center"
                and bool(item.get("url"))
                or payload.get("kind") == "course_notice"
                and public_notice_original(item)
            ),
        }
        for index, item in enumerate(items[:100])
        if isinstance(item, dict)
    ]


def collected_attachment_id(event_id, index):
    return str(uuid5(NAMESPACE_URL, f"campusinbox:chaoxing:{event_id}:{index}"))


def source_context(session, dedupe_key, *, linked_attachments=None):
    if not dedupe_key.startswith("chaoxing:"):
        return None
    event = session.exec(
        select(ChaoxingEvent).where(ChaoxingEvent.identity == dedupe_key[9:])
    ).first()
    if event is None:
        return None
    job = event.payload
    files = job.get("attachments")
    attachment_analysis_safe = (
        isinstance(files, list)
        and 1 <= len(files) <= 5
        and all(
            isinstance(item, dict)
            and isinstance(item.get("content_base64"), str)
            and bool(item["content_base64"])
            and item.get("status") not in ("partial", "failed", "pending")
            for item in files
        )
        and not job.get("media_pending_count")
    )
    context = {
        "platform": "chaoxing",
        "course": str(job.get("course") or "")[:100],
        "group_name": str(job.get("conversation") or "")[:100],
        "sender_name": str(job.get("sender") or "")[:100],
        "message_id": str(job.get("message_id") or "")[:200],
        "kind": job.get("kind"),
        "title": str(job.get("title") or "")[:161],
        "title_evidence": str((job.get("field_evidence") or {}).get("title") or "")[:300]
        if isinstance(job.get("field_evidence"), dict)
        else "",
        "task_type": job.get("task_type"),
        "platform_status": str(job.get("platform_status") or "")[:100],
        "closed_task_status": closed_task_status(job),
        "deadline": job.get("deadline") if job.get("kind") == "task_center" else None,
        "deadline_evidence": str((job.get("field_evidence") or {}).get("deadline") or "")[:300]
        if isinstance(job.get("field_evidence"), dict)
        else "",
        "has_unprocessed_media": bool(job.get("attachments") or job.get("media_pending_count")),
        "attachment_analysis_safe": attachment_analysis_safe,
        "attachment_analysis_count": len(files) if attachment_analysis_safe else 0,
    }
    if linked_attachments is not None:
        context["event_id"] = event.id
        missing = []
        linked = {attachment.id: attachment for attachment in linked_attachments}
        directory = Path(session.get_bind().url.database).parent / "uploads"
        for item in attachment_info(job):
            entry = {"index": item["index"], "name": item["name"]}
            if item["fetchable"]:
                missing.append(entry)
            elif item["available"] and job["kind"] in {"task_center", "course_notice"}:
                attachment = linked.get(collected_attachment_id(event.id, item["index"]))
                if attachment is None or not original_path(directory, attachment).is_file():
                    missing.append({**entry, "recoverable": True})
        context["missing_originals"] = missing
    return context


def platform_timestamp(value):
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None and stamp.utcoffset() is not None else None


def actual_sent_time(job):
    # Task-centre captured_at is the scanning time, not the publication time.
    field = (
        "published_at" if job.get("kind") in {"task_center", "checkin_activity"} else "captured_at"
    )
    return platform_timestamp(job.get(field))


CHECKIN_IDS = ("activity_id", "course_id", "class_id")


def checkin_fields(job):
    start = actual_sent_time(job)
    deadline = platform_timestamp(job.get("deadline"))
    evidence = job.get("field_evidence")
    title = job.get("title")
    if (
        not start
        or not isinstance(title, str)
        or not 1 <= len(title.strip()) <= 160
        or not all(re.fullmatch(r"[0-9]{1,32}", str(job.get(key, ""))) for key in CHECKIN_IDS)
        or not isinstance(evidence, dict)
    ):
        return None
    ended = job.get("activity_state") == "ended" and evidence.get("activity_state") in {
        "已结束",
        "已截止",
        "已过教师设置的截止时间",
    }
    if not ended and (
        deadline is None or deadline <= start or not str(evidence.get("deadline") or "").strip()
    ):
        return None
    return start, deadline, ended


def checkin_event(session, message):
    if not message.dedupe_key.startswith("chaoxing:"):
        return None
    return session.exec(
        select(ChaoxingEvent).where(
            ChaoxingEvent.identity == message.dedupe_key[9:],
            ChaoxingEvent.payload["kind"].as_string() == "checkin_activity",
        )
    ).first()


def validate_checkin_accept(session, event):
    job = event.payload
    evidence = checkin_fields(job)
    if not evidence or not evidence[1] or evidence[2] or evidence[1] <= now():
        raise HTTPException(409, "签到已结束或尚未核验，不能创建待办。")
    siblings = session.exec(
        select(ChaoxingEvent).where(
            ChaoxingEvent.account_id == event.account_id,
            ChaoxingEvent.payload["kind"].as_string() == "checkin_activity",
            *(ChaoxingEvent.payload[key].as_string() == str(job[key]) for key in CHECKIN_IDS),
        )
    )
    for sibling in siblings:
        if sibling.id == event.id or actual_sent_time(sibling.payload) != evidence[0]:
            continue
        sibling_fields = checkin_fields(sibling.payload)
        if sibling_fields and (sibling_fields[2] or sibling.received_at > event.received_at):
            raise HTTPException(409, "该场签到有更新或已结束，请核对最新记录。")
        if (
            sibling.suggestion_id
            and session.exec(
                select(Task.id).where(Task.suggestion_id == sibling.suggestion_id)
            ).first()
        ):
            raise HTTPException(409, "该场签到已有任务。")
    return evidence[1]


def closed_checkin_suggestions(session, suggestion_ids, current):
    origins = []
    for offset in range(0, len(suggestion_ids), 500):
        origins.extend(
            session.exec(
                select(ChaoxingEvent).where(
                    ChaoxingEvent.suggestion_id.in_(suggestion_ids[offset : offset + 500]),
                    ChaoxingEvent.payload["kind"].as_string() == "checkin_activity",
                )
            )
        )
    if not origins:
        return set()

    def instance(event, start):
        return (event.account_id, *(str(event.payload[key]) for key in CHECKIN_IDS), start)

    ended = set()
    accounts = list({event.account_id for event in origins})
    for offset in range(0, len(accounts), 500):
        rows = session.exec(
            select(ChaoxingEvent).where(
                ChaoxingEvent.account_id.in_(accounts[offset : offset + 500]),
                ChaoxingEvent.payload["kind"].as_string() == "checkin_activity",
                ChaoxingEvent.payload["activity_state"].as_string() == "ended",
            )
        )
        for row in rows:
            activity = checkin_fields(row.payload)
            if activity and activity[2]:
                ended.add(instance(row, activity[0]))
    closed = set()
    for origin in origins:
        activity = checkin_fields(origin.payload)
        if activity:
            start, deadline, is_ended = activity
            if is_ended or (deadline and deadline <= current) or instance(origin, start) in ended:
                closed.add(origin.suggestion_id)
    return closed


def historical_deadline(job, current):
    evidence = job.get("field_evidence")
    deadline = job.get("deadline")
    if (
        job.get("kind") != "task_center"
        or not isinstance(evidence, dict)
        or not evidence.get("deadline")
    ):
        return False
    if not isinstance(deadline, str):
        return False
    try:
        if len(deadline) == 10:
            return (
                date.fromisoformat(deadline) < current.astimezone(ZoneInfo("Asia/Shanghai")).date()
            )
        value = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        return value.tzinfo is not None and value.utcoffset() is not None and value <= current
    except (ValueError, OverflowError):
        return False


def historical_notice(job, suggestion, current, timezone="Asia/Shanghai"):
    for field in ("due_at", "due_date"):
        evidence = suggestion.evidence.get(field, {})
        quote = evidence.get("quote", "")
        if not quote or evidence.get("method") not in {
            "rule",
            "connector",
            "attachment",
            "llm+date",
        }:
            continue
        if field == "due_date" and suggestion.fields.get(field):
            try:
                return (
                    date.fromisoformat(suggestion.fields[field])
                    < current.astimezone(ZoneInfo(timezone)).date()
                )
            except (ValueError, KeyError, TypeError):
                return False
        return historical_deadline(
            {
                "kind": "task_center",
                "deadline": suggestion.fields.get(field),
                "field_evidence": {"deadline": quote},
            },
            current,
        )
    return False


def expired_task_notice(job, result, current, timezone="Asia/Shanghai"):
    if result.action != "create" or len(services.normalized(result.fields.get("title") or "")) < 2:
        return False
    if any(
        "多个可能的截止时间" in w or "不一致" in w or "任务名称与正文提取结果不同" in w
        for w in result.warnings
    ):
        return False
    return historical_deadline(job, current) or historical_notice(job, result, current, timezone)


EXPIRED_NOTICE = "截止日期已过，已放入历史归档，原文仍可查看。"
ENDED_CHECKIN = "该场签到已结束，已保留平台活动记录。"
CLOSED_WORK = "学习通显示该作业已提交或结束，已放入历史归档。"
REMINDER_ARCHIVED = "平台倒计时提醒已归档，可在历史归档查看原文。"


def archive_reason(session, suggestion, message, current, event=None):
    payload = event.payload if event else {}
    if reminder_only(payload):
        return REMINDER_ARCHIVED
    if payload.get("kind") == "checkin_activity" and closed_checkin_suggestions(
        session, [event.suggestion_id], current
    ):
        return ENDED_CHECKIN
    if payload.get("kind") == "task_center":
        key = platform_status.identity(event.account_id, payload)
        observation = session.get(platform_status.PlatformObservation, key) if key else None
        if closed_task_status(payload) or (
            observation and closed_task_status({**payload, "platform_status": observation.status})
        ):
            return CLOSED_WORK
    if expired_task_notice(
        payload, suggestion, current, message.timezone if message else "Asia/Shanghai"
    ):
        return EXPIRED_NOTICE
    return None


def require_restorable(session, suggestion):
    if (
        session.exec(
            select(SplitDraft.id).where(
                SplitDraft.suggestion_id == suggestion.id, SplitDraft.status == "archived"
            )
        ).first()
        and suggestion.status == "archived"
    ):
        raise HTTPException(409, EXPIRED_NOTICE)
    event = session.exec(
        select(ChaoxingEvent).where(ChaoxingEvent.suggestion_id == suggestion.id)
    ).first()
    message = session.get(Message, suggestion.message_id)
    if reason := archive_reason(session, suggestion, message, now(), event):
        raise HTTPException(409, reason)


def correct_class_deadline(message, suggestion):
    fields = dict(suggestion.fields)
    evidence = dict(suggestion.evidence)
    for key in ("due_at", "due_date"):
        origin = evidence.get(key, {})
        if fields.get(key) and origin.get("method") in {"rule", "llm+date"}:
            if class_schedule_evidence(message.text, origin.get("quote", "")):
                fields[key] = None
                evidence.pop(key)
    if fields != suggestion.fields:
        warnings = [
            warning
            for warning in suggestion.warnings
            if warning
            not in {
                "截止时间早于消息发送时间，请核对是否已经逾期。",
                "原文未明确具体时刻，已保留日期，请核对。",
            }
        ]
        warnings.append("原文中的上课时间已从截止字段移除，请核对安排。")
        return {"fields": fields, "evidence": evidence, "warnings": list(dict.fromkeys(warnings))}
    return {}


def archive_expired(session, current=None, *, suggestion_id=None, commit=True):
    from campus_inbox import splitting

    current = current or now()
    count = 0
    modified = 0
    query = (
        select(Suggestion, ChaoxingEvent)
        .outerjoin(ChaoxingEvent, ChaoxingEvent.suggestion_id == Suggestion.id)
        .where(Suggestion.status == "pending")
    )
    if suggestion_id is not None:
        query = query.where(Suggestion.id == suggestion_id)
    for suggestion, event in session.exec(query).all():
        message = session.get(Message, suggestion.message_id)
        if items := splitting.drafts(session, suggestion.id):
            expired_items = [
                draft
                for draft in items
                if draft.status == "pending" and splitting.expired(draft, message, current)
            ]
            for draft in expired_items:
                draft.status, draft.updated_at = "archived", current
                draft.warnings = list(dict.fromkeys([*draft.warnings, EXPIRED_NOTICE]))
                session.add(draft)
            if expired_items:
                splitting.settle(session, suggestion)
                modified += 1
                count += suggestion.status == "archived"
            continue
        payload = event.payload if event else {}
        values = correct_class_deadline(message, suggestion) if message else {}
        candidate = Extraction(**values, action=suggestion.action) if values else suggestion
        if not values and (
            suggestion.action == "create"
            and payload.get("kind") in {"course_notice", "task_center"}
            and not suggestion.fields.get("due_at")
            and not suggestion.fields.get("due_date")
            and not any("不一致" in w or "多个可能" in w for w in suggestion.warnings)
            and not any(
                suggestion.evidence.get(k, {}).get("method") == "user"
                for k in ("due_at", "due_date")
            )
        ):
            if message and not message.dedupe_key.startswith("unknown-time:"):
                parsed = extract_rules(
                    message.text, message.sent_at, message.timezone, scoped_deadline=True
                )
                if parsed.action == "create" and (
                    parsed.fields.get("due_at") or parsed.fields.get("due_date")
                ):
                    fields = {
                        **suggestion.fields,
                        **{k: parsed.fields[k] for k in ("due_at", "due_date")},
                    }
                    evidence = {
                        **suggestion.evidence,
                        **{k: v for k, v in parsed.evidence.items() if k in ("due_at", "due_date")},
                    }
                    stale = {
                        "发现日期和时刻，但无法确认是截止时间，请手动核对。",
                        "未找到明确截止时间，已留空，请补充。",
                        "通知未明确具体截止时刻，已留空，请补充。",
                    }
                    if not parsed.needs_llm:
                        stale.add("模型兜底未启用或未选择模型，可直接手动完善。")
                    warnings = list(
                        dict.fromkeys(
                            [w for w in suggestion.warnings if w not in stale] + parsed.warnings
                        )
                    )
                    candidate = Extraction(
                        fields=fields,
                        evidence=evidence,
                        warnings=warnings,
                        action=suggestion.action,
                    )
                    values = {"fields": fields, "evidence": evidence, "warnings": warnings}
        reason = archive_reason(session, candidate, message, current, event)
        if reason:
            values.update(
                status="archived",
                warnings=list(dict.fromkeys([*candidate.warnings, reason])),
            )
        if not values:
            continue
        changed = session.execute(
            update(Suggestion)
            .where(
                Suggestion.id == suggestion.id,
                Suggestion.status == "pending",
                Suggestion.updated_at == suggestion.updated_at,
            )
            .values(**values, updated_at=current)
        ).rowcount
        modified += changed
        count += changed if reason else 0
    if modified and commit:
        session.commit()
    return count


def apply_platform_deadline(result, context):
    raw = context.get("deadline")
    evidence = context.get("deadline_evidence")
    if result.action != "create" or not isinstance(raw, str) or not evidence:
        return
    try:
        if len(raw) == 10:
            platform_date, platform_time = date.fromisoformat(raw), None
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                return
            platform_time = parsed.astimezone(UTC)
            platform_date = parsed.astimezone(ZoneInfo("Asia/Shanghai")).date()
    except (ValueError, OverflowError):
        return
    existing_date = result.fields.get("due_date")
    existing_time = result.fields.get("due_at")
    conflict = False
    if existing_date:
        conflict = date.fromisoformat(existing_date) != platform_date
    elif existing_time:
        previous = datetime.fromisoformat(existing_time)
        conflict = (
            previous != platform_time
            if platform_time
            else previous.astimezone(ZoneInfo("Asia/Shanghai")).date() != platform_date
        )
    if conflict:
        result.fields["due_at"] = result.fields["due_date"] = None
        result.evidence.pop("due_at", None)
        result.evidence.pop("due_date", None)
        result.warnings.append("多个可能的截止时间：学习通与通知正文不一致，请手动核对。")
        return
    if any("多个可能的截止时间" in w for w in result.warnings):
        return
    if existing_date and platform_time:
        result.fields["due_date"] = None
        result.evidence.pop("due_date", None)
        existing_date = None
    if existing_date or existing_time:
        return
    key = "due_at" if platform_time else "due_date"
    result.fields[key] = platform_time.isoformat() if platform_time else platform_date.isoformat()
    result.evidence[key] = {"method": "connector", "quote": evidence}
    result.warnings = [
        w
        for w in result.warnings
        if w
        not in {
            "通知未明确具体截止时刻，已留空，请补充。",
            "未找到明确截止时间，已留空，请补充。",
            "原文未明确具体时刻，已保留日期，请核对。",
        }
    ]
    if not platform_time:
        result.warnings.append("学习通仅提供截止日期，请核对具体时刻。")


def apply_platform_task(result, context):
    if context.get("kind") != "task_center" or result.action == "review":
        return
    title = context.get("title", "").strip()
    evidence = context.get("title_evidence", "")
    if not title or len(title) > 160 or title not in evidence or CHANGE_WORDS.search(title):
        return
    if result.action == "ignore":
        result.action = "create"
        result.warnings = [w for w in result.warnings if not w.startswith("未发现明确任务线索")]
        result.warnings.append("此任务来自学习通任务中心，请核对原始内容。")
    if not result.fields.get("title"):
        result.fields["title"] = title
        result.evidence["title"] = {"method": "connector", "quote": evidence}
    elif result.fields["title"] != title:
        result.warnings.append("学习通任务名称与正文提取结果不同，请核对。")
    if result.fields.get("kind") == "other":
        task_type = context.get("task_type")
        kind = (
            {"assignment": "homework", "experiment": "homework", "exam": "exam"}.get(task_type)
            if isinstance(task_type, str)
            else None
        )
        if kind:
            result.fields["kind"] = kind
            result.evidence["kind"] = {
                "method": "connector",
                "quote": "学习通任务中心类型：" + str(context["task_type"]),
            }


class ChaoxingIntake:
    """Store before acknowledging; events and root inbox share one SQLite database."""

    def __init__(self, engine, provider_factory):
        self.engine = engine
        self.provider_factory = provider_factory
        self.lock = threading.RLock()

    def _save_originals(self, session, event, message_id, *, only_index=None):
        directory = Path(self.engine.url.database).parent / "uploads"
        for index, item in enumerate(event.payload.get("attachments") or []):
            if only_index is not None and index != only_index:
                continue
            if not isinstance(item, dict) or not item.get("content_base64"):
                continue
            try:
                content = base64.b64decode(item["content_base64"], validate=True)
            except (ValueError, TypeError, binascii.Error) as exc:
                raise ValueError("学习通附件内容无效") from exc
            if not content or len(content) > 8 * 1024 * 1024:
                raise ValueError("学习通附件大小无效")
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", str(item.get("name") or "附件"))[:180]
            attachment_id = collected_attachment_id(event.id, index)
            existing = session.get(Attachment, attachment_id)
            if existing:
                if existing.message_id != message_id:
                    raise ValueError("学习通附件来源不匹配")
                path = original_path(directory, existing)
                if not path.is_file():
                    directory.mkdir(parents=True, exist_ok=True)
                    persist_original(path, content)
                continue
            attachment = Attachment(
                id=attachment_id,
                filename=name,
                size=len(content),
                status="failed",
                error=UNPROCESSED_ATTACHMENT,
                message_id=message_id,
            )
            if Path(name).suffix.lower() in {".txt", ".md"}:
                try:
                    attachment.text = local_text(content)
                    attachment.status, attachment.error = "ready", ""
                except HTTPException:
                    attachment.error = "原附件已保存，但无法提取文字，请下载后核对。"
            directory.mkdir(parents=True, exist_ok=True)
            path = original_path(directory, attachment)
            persist_original(path, content)
            session.add(attachment)
            if Path(name).suffix.lower() == ".docx":
                session.flush()
                session.add(LocalAttachmentExtraction(attachment_id=attachment.id))

    def _same_linked_task_origin(self, session, account_id, job):
        identity = platform_status.identity(account_id, job)
        if not identity:
            return None
        content = task_content_identity(account_id, job)
        for _, origin in platform_status.linked_task_sources(session):
            if (
                origin.account_id == account_id
                and platform_status.identity(account_id, origin.payload) == identity
                and task_content_identity(account_id, origin.payload) == content
                and not any(fill_originals(origin.payload, job)[1:])
            ):
                return origin
        return None

    def receive(self, account_id, job):
        if not isinstance(account_id, str) or not account_id.strip() or len(account_id) > 200:
            raise ValueError("学习通账号标识无效")
        if not isinstance(job, dict) or job.get("kind") not in {
            "chat",
            "course_notice",
            "task_center",
            "checkin_activity",
        }:
            raise ValueError("学习通消息类型无效")
        if job.get("direction", "received") != "received":
            raise ValueError("不接收本人发送的消息")
        if not isinstance(job.get("message_id"), str) or not 1 <= len(job["message_id"]) <= 200:
            raise ValueError("学习通消息编号无效")
        encoded = json.dumps(job, ensure_ascii=False, sort_keys=True)
        # The collector can include up to 8 MiB of attachment bytes encoded as
        # base64. Retain them durably until the attachment importer handles them.
        if len(encoded.encode("utf-8")) > 12 * 1024 * 1024:
            raise ValueError("学习通消息过大，未确认接收")
        identity, old_canonical, previous = event_identity(account_id, job)
        with self.lock, Session(self.engine) as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            existing = session.exec(
                select(ChaoxingEvent).where(
                    ChaoxingEvent.identity.in_([identity, old_canonical, previous])
                )
            ).first()
            media_identity = notice_media_identity(account_id, job)
            if media_identity and (existing is None or fill_originals(existing.payload, job)[2]):
                versions = session.exec(
                    select(ChaoxingEvent)
                    .where(
                        ChaoxingEvent.account_id == account_id,
                        ChaoxingEvent.payload["kind"].as_string() == "course_notice",
                        ChaoxingEvent.payload["message_id"].as_string() == job["message_id"],
                    )
                    .order_by(ChaoxingEvent.received_at.desc(), ChaoxingEvent.id)
                )
                for version in versions:
                    if notice_media_identity(account_id, version.payload) != media_identity:
                        continue
                    if not fill_originals(version.payload, job)[2]:
                        existing = version
                        break
            platform_status.observe(session, account_id, job)
            if existing:
                if (
                    existing.state != "done"
                    and verified_empty_notice(job)
                    and not verified_empty_notice(existing.payload)
                ):
                    existing.payload = {**existing.payload, "empty_body_ready": True}
                    existing.state = "pending"
                    session.add(existing)
                originals, changed, conflict = fill_originals(existing.payload, job)
                if not conflict:
                    coverage_complete = (
                        media_identity
                        and job["media_pending_count"] == 0
                        and existing.payload.get("media_pending_count")
                        and originals
                        and all(item.get("content_base64") for item in originals)
                    )
                    if changed or coverage_complete:
                        payload = {**existing.payload, "attachments": originals}
                        if coverage_complete:
                            payload["media_pending_count"] = 0
                        if len(json.dumps(payload, ensure_ascii=False).encode()) > 12 * 1024 * 1024:
                            raise ValueError("补齐后的附件超过同条消息的大小上限。")
                        existing.payload = payload
                        if existing.state == "needs_review" and attachment_only_notice(payload):
                            existing.state = "pending"
                        if existing.suggestion_id:
                            suggestion = session.get(Suggestion, existing.suggestion_id)
                            if suggestion:
                                self._save_originals(session, existing, suggestion.message_id)
                        session.add(existing)
                    session.commit()
                    return existing.id, False
                # Distinct bytes at the same source URL are a new version.
                identity = previous
                repeat = session.exec(
                    select(ChaoxingEvent).where(ChaoxingEvent.identity == identity)
                ).first()
                if repeat:
                    session.commit()
                    return repeat.id, False
            origin = self._same_linked_task_origin(session, account_id, job)
            if origin:
                session.commit()
                return origin.id, False
            row = ChaoxingEvent(identity=identity, account_id=account_id, payload=job)
            session.add(row)
            session.commit()
            return row.id, True

    def process(self, event_id, *, sent_at=None):
        if sent_at is not None and (sent_at.tzinfo is None or sent_at.utcoffset() is None):
            raise ValueError("消息时间需要时区")
        with self.lock, Session(self.engine) as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = session.get(ChaoxingEvent, event_id)
            if row is None:
                raise ValueError("学习通消息不存在")
            if row.state == "done":
                return row.state
            job = row.payload
            stamp = sent_at or actual_sent_time(job)
            is_reminder = reminder_only(job)
            archive_reminder = (
                is_reminder
                and not session.exec(
                    select(Message.id).where(Message.dedupe_key == "chaoxing:" + row.identity)
                ).first()
            )
            early = None
            if stamp is not None and isinstance(job.get("text"), str):
                parsed = extract_rules(job["text"], stamp, "Asia/Shanghai", scoped_deadline=True)
                context = source_context(session, "chaoxing:" + row.identity)
                if context:
                    apply_platform_task(parsed, context)
                    apply_platform_deadline(parsed, context)
                    if context.get("course"):
                        parsed.fields["course"] = context["course"]
                        parsed.evidence["course"] = {
                            "method": "connector",
                            "quote": context["course"],
                        }
                if (
                    expired_task_notice(job, parsed, now())
                    and not session.exec(
                        select(Message.id).where(Message.dedupe_key == "chaoxing:" + row.identity)
                    ).first()
                ):
                    early = parsed
            closed = closed_task_status(job)
            archive_closed = (
                closed
                and stamp is not None
                and platform_status.identity(row.account_id, job)
                and isinstance(job.get("title"), str)
                and 1 <= len(job["title"].strip()) <= 160
                and isinstance(job.get("text"), str)
                and 1 <= len(job["text"].strip()) <= 20000
                and not CHANGE_WORDS.search(job["text"])
                and not DEADLINE_CHANGE.search(job["text"])
            )
            if job["kind"] == "checkin_activity":
                activity = checkin_fields(job)
                if activity is None:
                    row.state = "needs_review"
                else:
                    start, deadline, ended = activity
                    archived = ended or deadline <= now()
                    title = job["title"].strip()
                    message = Message(
                        text=job["text"] if isinstance(job.get("text"), str) else title,
                        source="学习通签到",
                        sent_at=start.astimezone(UTC),
                        dedupe_key="chaoxing:" + row.identity,
                    )
                    session.add(message)
                    session.flush()
                    suggestion = Suggestion(
                        message_id=message.id,
                        status="archived" if archived else "pending",
                        action="ignore" if archived else "create",
                        fields={
                            "title": title,
                            "course": str(job.get("course") or job.get("conversation") or "")[:100],
                            "kind": "event",
                            "due_at": deadline.astimezone(UTC).isoformat() if deadline else None,
                        },
                        evidence={
                            "due_at": {
                                "method": "connector",
                                "quote": str(job["field_evidence"].get("deadline") or "")[:300],
                            }
                        }
                        if deadline
                        else {},
                        warnings=[
                            "该场签到已结束，已保留平台活动记录。"
                            if archived
                            else "请核对签到场次。创建待办时采用平台截止时间。"
                        ],
                    )
                    session.add(suggestion)
                    session.flush()
                    row.suggestion_id = suggestion.id
                    row.state = "done"
            elif closed and not archive_closed:
                # Do not turn work already submitted on the platform into new tasks.
                row.state = "needs_review"
            elif stamp is None:
                row.state = "needs_time"
            elif early is not None:
                message = Message(
                    text=job["text"],
                    source="学习通作业考试" if job["kind"] == "task_center" else "学习通课程通知",
                    sent_at=stamp.astimezone(UTC),
                    dedupe_key="chaoxing:" + row.identity,
                )
                session.add(message)
                session.flush()
                suggestion = Suggestion(
                    message_id=message.id,
                    status="archived",
                    action=early.action,
                    fields=early.fields,
                    evidence=early.evidence,
                    warnings=[*early.warnings, EXPIRED_NOTICE],
                )
                session.add(suggestion)
                session.flush()
                try:
                    self._save_originals(session, row, message.id)
                    row.suggestion_id, row.state = suggestion.id, "done"
                except (ValueError, OSError):
                    session.rollback()
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                    row = session.get(ChaoxingEvent, event_id)
                    row.state = "failed"
            elif (
                archive_reminder
                or archive_closed
                or verified_empty_notice(job)
                or attachment_only_notice(job)
            ):
                archive = archive_reminder or archive_closed or verified_empty_notice(job)
                warning = "此通知只有附件。请核对原件，或在提取文字后重新识别。"
                if archive_reminder:
                    warning = REMINDER_ARCHIVED
                elif archive_closed:
                    status = str(job["platform_status"]).strip()[:100]
                    warning = f"平台状态为「{status}」，已归档原通知。"
                elif archive:
                    warning = "平台详情确认正文为空，已保留通知标题。"
                message = Message(
                    text=job["text"],
                    source="学习通作业考试" if archive_closed else "学习通课程通知",
                    sent_at=stamp.astimezone(UTC),
                    dedupe_key="chaoxing:" + row.identity,
                )
                session.add(message)
                session.flush()
                suggestion = Suggestion(
                    message_id=message.id,
                    status="archived" if archive else "pending",
                    action="ignore",
                    fields={"title": str(job.get("title") or "").strip()[:160]} if archive else {},
                    warnings=[warning],
                )
                session.add(suggestion)
                session.flush()
                try:
                    self._save_originals(session, row, message.id)
                    row.suggestion_id = suggestion.id
                    row.state = "done"
                except (ValueError, OSError):
                    session.rollback()
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                    row = session.get(ChaoxingEvent, event_id)
                    row.state = "failed"
            elif not isinstance(job.get("text"), str) or not 2 <= len(job["text"].strip()) <= 20000:
                # Never truncate or invent attachment-only message content.
                row.state = "needs_review"
            else:
                source = {
                    "chat": "学习通聊天",
                    "course_notice": "学习通课程通知",
                    "task_center": "学习通作业考试",
                    "checkin_activity": "学习通签到",
                }[job["kind"]]
                try:
                    suggestion = services.import_message(
                        session,
                        ImportMessage(text=job["text"], source=source, sent_at=stamp),
                        self.provider_factory,
                        dedupe_key="chaoxing:" + row.identity,
                    )
                    if suggestion.status == "processing":
                        suggestion.status = "pending"
                        session.add(suggestion)
                        session.commit()
                        suggestion = services.retry(session, suggestion, self.provider_factory)
                    # A retry can reuse an already committed suggestion while the
                    # intake's explicit transaction is still open. End that read
                    # before reacquiring the write lock and refreshing the result.
                    session.commit()
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                    session.refresh(suggestion)
                    if suggestion.status == "pending" and is_reminder:
                        suggestion.status = "archived"
                        suggestion.warnings = [
                            *suggestion.warnings,
                            REMINDER_ARCHIVED,
                        ]
                        session.add(suggestion)
                    elif suggestion.status == "pending" and expired_task_notice(
                        job, suggestion, now()
                    ):
                        suggestion.status = "archived"
                        suggestion.warnings = [
                            *suggestion.warnings,
                            EXPIRED_NOTICE,
                        ]
                        session.add(suggestion)
                    row = session.get(ChaoxingEvent, event_id)
                    self._save_originals(session, row, suggestion.message_id)
                    row.suggestion_id = suggestion.id
                    row.state = "done"
                except Exception:
                    session.rollback()
                    row = session.get(ChaoxingEvent, event_id)
                    row.state = "failed"
            session.add(row)
            session.commit()
            return row.state
