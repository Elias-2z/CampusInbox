"""Conservative QQ allow/deny rules before model classification and extraction."""

import re
import unicodedata

from campus_inbox.providers import ClassifiedFields
from campus_inbox.schemas import MessageClassification

# Allowlist protects task clues; a match is not proof that a message is a task.
TASK_CLUES = ("考试", "作业", "通知", "提交", "截止", "报告", "实验", "补交", "取消", "改期")
# Only complete reply segments match. Keep the vocabulary deliberately small.
SHORT_REPLIES = frozenset({"收到", "好的", "谢谢", "哈哈", "哈哈哈", "嗯嗯", "ok"})
REPLY_SEPARATORS = re.compile(r"[\s,，、。！!~～.]+")


def is_short_reply(text):
    normalized = unicodedata.normalize("NFKC", text).strip().casefold()
    if not normalized or len(normalized) > 40:
        return False
    if any(clue in normalized for clue in TASK_CLUES):
        return False
    segments = [part for part in REPLY_SEPARATORS.split(normalized) if part]
    return bool(segments) and all(part in SHORT_REPLIES for part in segments)


def classify(text, settings, provider_factory, reference, timezone, context, *, reclassify=False):
    if not reclassify and is_short_reply(text):
        return MessageClassification(
            decision="chat", method="rule", quote=text, note="完整消息为简短回复，已自动分流。"
        ), None
    unavailable = "未启用分类模型，已保留消息和规则字段，请人工核对。"
    if len(text) > 12000:
        unavailable = "消息超出模型分析长度，已保留原文和规则字段，请人工核对。"
    elif settings.enabled and settings.model:
        try:
            provider = provider_factory(settings)
            output = ClassifiedFields.model_validate(
                provider.classify_contextual(text, reference, timezone, context or {})
            )
            if not output.classification_quote.strip() or output.classification_quote not in text:
                raise ValueError("Classification quote has no source support")
            return MessageClassification(
                decision=output.classification,
                method="llm",
                quote=output.classification_quote,
                note={
                    "notification": "模型识别为任务或安排通知，仍需人工确认。",
                    "chat": "模型识别为普通聊天，已自动分流，可恢复并重新识别。",
                    "uncertain": "消息性质或上下文不明确，请人工核对。",
                }[output.classification],
            ), output.model_dump()
        except Exception:
            # Provider/credential/schema errors must never discard a message or echo its content.
            unavailable = "分类未完成或结果无效，已保留消息和规则字段，请人工核对。"
    return MessageClassification(decision="uncertain", method="fallback", note=unavailable), None
