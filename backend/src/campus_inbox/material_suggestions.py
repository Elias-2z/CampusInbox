"""Extract reviewable material candidates without changing a task."""

import re

from sqlmodel import Session, select

from campus_inbox.models import Attachment, MergeChange, Message, SplitDraft, Suggestion, Task

NEGATION = re.compile(
    r"(?:无需|不用|不必|不需要|不需|不要|请勿|不得|不能|免于|免|可选|自愿)[^，,；;。]{0,12}$"
)
ACTION = re.compile(r"(?:提交|上传|上交)(?!\s*方式)\s*[:：]?\s*")
BOUNDARY = re.compile(r"(?:截止|截至|最晚|于\s*\d|在\s*\d|至|到|并(?:请|在|于|将|上传|提交)|然后)")
MATERIAL_HEADING = re.compile(r"(?:请|需|须)?(?:提交|上传|上交)(?:以下|的)?(?:材料|文件)\s*[：:]")
MATERIAL_BULLET = re.compile(r"\s*(?:[-*•]|\d{1,2}[.、．]|[（(]\d{1,2}[)）])\s*(.+)")
OPTIONAL_MATERIAL = re.compile(r"无需|不用|不必|不需要|不需|不要|可选|选交|免交|自愿")


def listed_materials(text):
    heading = ""
    for raw in text.splitlines():
        line = raw.strip()
        if MATERIAL_HEADING.fullmatch(line):
            heading = line
            continue
        bullet = MATERIAL_BULLET.fullmatch(line) if heading else None
        if not bullet:
            heading = ""
            continue
        item = bullet[1].strip(" ：:。\t")
        if (
            item
            and len(item) <= 200
            and not OPTIONAL_MATERIAL.search(item)
            and not re.search(
                r"截止|截至|最晚|(?:^|\s)(?:备注|说明)[：:]",
                item,
            )
        ):
            yield item, f"{heading}\n{line}"[:300]


def candidates(session: Session, task: Task) -> list[dict[str, str]]:
    draft = session.exec(select(SplitDraft).where(SplitDraft.task_id == task.id)).first()
    source = session.get(Message, task.message_id)
    texts = [("通知正文", draft.quote if draft else source.text if source else "")]
    attachment_sources = [source.id] if source and not draft else []
    linked = session.exec(
        select(Message, SplitDraft)
        .join(Suggestion, Suggestion.message_id == Message.id)
        .join(MergeChange, MergeChange.suggestion_id == Suggestion.id)
        .outerjoin(SplitDraft, SplitDraft.id == MergeChange.split_draft_id)
        .where(MergeChange.task_id == task.id, MergeChange.undo_event_id.is_(None))
        .order_by(Message.received_at, MergeChange.id)
        .limit(20)
    )
    for message, split in linked:
        texts.append(("补充通知正文", split.quote if split else message.text))
        if split is None:
            attachment_sources.append(message.id)
    if attachment_sources:
        attachments = session.exec(
            select(Attachment)
            .where(
                Attachment.message_id.in_(attachment_sources),
                Attachment.status == "ready",
                Attachment.error.not_like("%部分页面%"),
            )
            .order_by(Attachment.created_at, Attachment.id)
            .limit(20)
        ).all()
        texts.extend((f"附件：{item.filename}", item.text) for item in attachments)
    results, seen = [], set()
    for label, text in texts:
        for item, quote in listed_materials(text):
            if item not in seen:
                seen.add(item)
                results.append({"text": item, "quote": quote, "source": label})
                if len(results) >= 30:
                    return results
        for clause in re.split(r"[。；;！!？?\n]", text):
            if MATERIAL_HEADING.fullmatch(clause.strip()):
                continue
            actions = list(ACTION.finditer(clause))
            for index, match in enumerate(actions):
                if NEGATION.search(clause[: match.start()]):
                    continue
                end = actions[index + 1].start() if index + 1 < len(actions) else len(clause)
                body = BOUNDARY.split(clause[match.end() : end], maxsplit=1)[0]
                if index + 1 < len(actions):
                    body = re.sub(r"(?:并|然后)?(?:请|需要|务必)\s*$", "", body)
                for part in re.split(r"[、，,和及+＋]", body):
                    item = part.strip(" ：:。\t")
                    if (
                        not item
                        or len(item) > 200
                        or OPTIONAL_MATERIAL.search(item)
                        or re.search(r"\d.*(?:月|日|:)|明天|后天|周[一二三四五六日]", item)
                    ):
                        continue
                    if item not in seen:
                        seen.add(item)
                        results.append(
                            {"text": item, "quote": clause.strip()[:300], "source": label}
                        )
                        if len(results) >= 30:
                            return results
    return results
