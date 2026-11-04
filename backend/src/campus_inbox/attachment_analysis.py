"""Use one unambiguous attachment as reviewable evidence for an inbox draft."""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from campus_inbox.extraction import extract_rules
from campus_inbox.models import Attachment, Message

ABSOLUTE_DATE = re.compile(r"\d{4}(?:年\d{1,2}月\d{1,2}[日号]|[-/]\d{1,2}[-/]\d{1,2})")


def supplement(session: Session, message: Message, result, expected_count: int) -> bool:
    if result.action not in {"create", "ignore"}:
        return False
    files = session.exec(
        select(Attachment).where(Attachment.message_id == message.id).limit(expected_count + 1)
    ).all()
    if len(files) != expected_count or any(
        file.status != "ready" or file.error or not 2 <= len(file.text) <= 20000 for file in files
    ):
        return False
    candidate = None
    seen_text = set()
    for file in files:
        text = file.text.strip()
        if text in seen_text:
            continue
        seen_text.add(text)
        extraction = extract_rules(
            file.text,
            message.sent_at,
            message.timezone,
            scoped_deadline=message.dedupe_key.startswith("chaoxing:"),
        )
        if extraction.action == "ignore" and not (
            extraction.fields.get("due_at") or extraction.fields.get("due_date")
        ):
            continue
        if extraction.action != "create" or not extraction.fields.get("title") or candidate:
            if expected_count > 1:
                result.warnings.append("多份附件存在任务或日期线索，请逐份核对。")
            return False
        candidate = file, extraction
    if candidate is None:
        return False
    source, parsed = candidate
    title = result.fields.get("title", "")
    if (
        title
        and re.sub(r"\W+", "", title).casefold()
        != re.sub(r"\W+", "", parsed.fields["title"]).casefold()
    ):
        result.warnings.append("附件识别出的任务名称与通知不同，请手动核对，未合并附件字段。")
        return False
    if result.action == "ignore":
        result.action = "create"
        result.warnings = [w for w in result.warnings if not w.startswith("未发现明确任务线索")]
    result.warnings.append("附件已参与识别，请对照原件核对任务内容。")

    def add_evidence(name):
        original = parsed.evidence[name]
        return {"method": "attachment", "quote": f"附件「{source.filename}」：{original['quote']}"}

    for name in ("title", "course", "submission_platform", "file_requirements"):
        if not result.fields.get(name) and parsed.fields.get(name) and name in parsed.evidence:
            result.fields[name] = parsed.fields[name]
            result.evidence[name] = add_evidence(name)
    if result.fields.get("kind") == "other" and parsed.fields.get("kind") != "other":
        result.fields["kind"] = parsed.fields["kind"]

    date_key = "due_at" if parsed.fields.get("due_at") else "due_date"
    incoming = parsed.fields.get(date_key)
    if not incoming:
        return True
    quote = parsed.evidence.get(date_key, {}).get("quote", "")
    if not ABSOLUTE_DATE.search(quote):
        result.warnings.append("附件中的相对日期或缺少年份的日期需要人工核对。")
        return True
    existing_time = result.fields.get("due_at")
    existing_date = result.fields.get("due_date")
    if existing_time or existing_date:
        if existing_time and date_key == "due_at":
            conflict = datetime.fromisoformat(existing_time) != datetime.fromisoformat(incoming)
        else:
            tz = ZoneInfo(message.timezone)
            old_day = (
                datetime.fromisoformat(existing_time).astimezone(tz).date().isoformat()
                if existing_time
                else existing_date
            )
            new_day = (
                datetime.fromisoformat(incoming).astimezone(tz).date().isoformat()
                if date_key == "due_at"
                else incoming
            )
            conflict = old_day != new_day
        if conflict:
            result.fields["due_at"] = result.fields["due_date"] = None
            result.evidence.pop("due_at", None)
            result.evidence.pop("due_date", None)
            result.warnings.append("通知正文与附件的截止时间不一致，请手动核对。")
        return True
    if any("多个可能的截止时间" in warning for warning in result.warnings):
        return True
    result.fields[date_key] = incoming
    result.evidence[date_key] = add_evidence(date_key)
    result.warnings = [
        warning
        for warning in result.warnings
        if warning
        not in {"通知未明确具体截止时刻，已留空，请补充。", "未找到明确截止时间，已留空，请补充。"}
    ]
    if date_key == "due_date":
        result.warnings.append("附件仅明确截止日期，请核对具体时刻。")
    return True
