"""Default learning-platform scope: no chats, course notices from the last 30 days."""

from datetime import UTC, timedelta

from sqlmodel import Session, select

from campus_inbox.connectors.chaoxing import ChaoxingEvent, actual_sent_time
from campus_inbox.models import Message, SplitDraft, Suggestion, now

NOTICE_DAYS = 30


def skip_reason(job, reference=None):
    if not isinstance(job, dict):
        return None  # The intake validates malformed messages separately.
    if job.get("kind") == "chat":
        return "chat_disabled"
    if job.get("kind") != "course_notice":
        return None
    stamp = actual_sent_time(job)
    # Unknown publication dates remain in the explicit needs_time queue.
    if stamp is not None and stamp < (reference or now()).astimezone(UTC) - timedelta(
        days=NOTICE_DAYS
    ):
        return "old_notice"
    return None


def archive_excluded(engine):
    """Preserve evidence and confirmed/manual work; hide old automated inbox clutter."""
    result = {"filtered": 0, "archived": 0}
    reference = now()
    with Session(engine) as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        for event in session.exec(select(ChaoxingEvent).where(ChaoxingEvent.state != "filtered")):
            reason = skip_reason(event.payload, reference)
            if reason is None:
                continue
            suggestion = (
                session.get(Suggestion, event.suggestion_id) if event.suggestion_id else None
            )
            if suggestion is None:
                message = session.exec(
                    select(Message).where(Message.dedupe_key == "chaoxing:" + event.identity)
                ).first()
                if message:
                    suggestion = session.exec(
                        select(Suggestion).where(Suggestion.message_id == message.id)
                    ).first()
            if suggestion and (
                suggestion.status == "accepted"
                or session.exec(
                    select(SplitDraft).where(SplitDraft.suggestion_id == suggestion.id)
                ).first()
            ):
                continue
            event.state = "filtered"
            session.add(event)
            result["filtered"] += 1
            if suggestion and suggestion.status in {"pending", "processing"}:
                suggestion.status = "archived"
                suggestion.warnings = [
                    *suggestion.warnings,
                    "按采集范围归档：聊天不参与识别。"
                    if reason == "chat_disabled"
                    else "按采集范围归档：课程通知已超出近 30 天。",
                ]
                suggestion.updated_at = reference
                session.add(suggestion)
                result["archived"] += 1
        session.commit()
    return result
