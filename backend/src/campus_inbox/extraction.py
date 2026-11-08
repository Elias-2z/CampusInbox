"""Conservative, independently testable extraction. No database or network access."""

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from campus_inbox.temporal import chinese_number, resolve_day

DATE = re.compile(
    r"(?:(?:(?P<year>\d{4})[ \t\u3000]*年[ \t\u3000]*)?"
    r"(?P<month>[一二三四五六七八九十两\d]{1,3})[ \t\u3000]*月[ \t\u3000]*"
    r"(?P<day>[一二三四五六七八九十两\d]{1,3})(?:[ \t\u3000]*[日号])?"
    r"|(?P<iso>(?<!\d)\d{4}(?P<date_sep>[-/.])\d{1,2}(?P=date_sep)\d{1,2}(?!\d))"
    r"|(?P<short>(?<![\d/.-])\d{1,2}[-/]\d{1,2}(?![\d/.-])))"
    r"(?:[ \t]*(?:周|星期)(?P<weekday>[一二三四五六日天]))?"
    r"|(?P<relative>今天|明天|后天|今晚|明晚|(?:下星期|本星期|这星期|下周|本周|这周|周|星期)[一二三四五六日天])"
    r"|(?P<natural>大后天|昨天|前天|(?:下下|下|上|本|这)?礼拜[一二三四五六日天]"
    r"|(?:下下周|上周)[一二三四五六日天]"
    r"|[零一二三四五六七八九十两\d]+(?:天|日|周|星期)后"
    r"|(?:本月|这个月|下月|下个月)(?:底|末|[一二三四五六七八九十两\d]{1,3}[日号]))"
)
TIME = re.compile(
    r"^(?:(?P<iso_clock>T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)"
    r"|\s*(?:的)?\s*(?P<period>上午|早上|下午|晚上|晚间|中午|凌晨|早|晚)?\s*"
    r"(?P<hour>\d{1,2}|[一二三四五六七八九十两]{1,3})"
    r"(?:(?:[:：])(?P<minute>\d{2})|(?:点|时)(?P<cnminute>\d{1,2}|一刻|三刻|半|整|[零一二三四五六七八九十两]{1,3})?(?:分)?))"
)
SUBMISSION_AFTER_DATE = re.compile(
    r"^\s*(?:(?:在|通过)\s*(?:学习通|雨课堂|课程群|QQ(?:群)?|微信(?:群)?|邮箱)"
    r"(?:上|中|内)?\s*)?(?:提交|上交|上传|补交|交[给到至])"
)
BEFORE_CLASS_SUBMISSION = re.compile(
    r"^(?:上课|开课|实验课|课|下课|放学)前\s*(?:请|务必)?\s*(?:提交|上交|上传|补交|完成|交)"
)
BEFORE_SUBMISSION = re.compile(
    r"^(?:之|以)?前\s*(?:请|务必)?\s*(?:(?:在|通过)\s*(?:学习通|雨课堂|课程群|QQ(?:群)?|微信(?:群)?|邮箱)"
    r"(?:上|中|内)?\s*)?(?:提交|上交|上传|补交|完成|交|将|把)"
)
SUBMISSION_WINDOW = re.compile(r"^\s*(?:到|至|[-—–~～])")
CLAUSE_BOUNDARY = re.compile(r"[，,。；;！!？?\n]")
NEGATED_DATE = re.compile(r"(?:不是|并非|无需|不用|不要|不必)[^：:]{0,12}$")
COURSES = [
    "数据库原理",
    "软件工程",
    "高等数学",
    "大学英语",
    "数据结构",
    "操作系统",
    "计算机网络",
    "数据库",
]
TASK_WORDS = re.compile(
    r"提交|作业|实验|报告|考试|报名|截止|练习|答辩|上交|补交|打卡|上传|完成|交[到至给]"
)
CHANGE_WORDS = re.compile(
    r"延期|延迟|推迟|提前|取消|不用交|无需提交|改为|改到|改成|顺延|调整|更正|变更|另行通知|以此为准"
    r"|不(?:用|需要|必)(?:再)?(?:提交|上交|交)|不要(?:再)?(?:提交|上交)|作废"
)


DEADLINE_CHANGE = re.compile(
    r"延期|延迟|推迟|顺延|延后|延至|提前(?:到|至|为|[一二三四五六七八九十0-9])"
    r"|(?:截止(?:时间|日期|期限)?|(?:提交|交付|上交)(?:时间|日期|期限))"
    r"(?:[^。，；\n]{0,40}(?:调整|改[为到成至]|变更)|[，,]\s*现改至)"
)
REVISED_DEADLINE_PREFIX = re.compile(
    r"(?:延期|延迟|推迟|提前|顺延|延后|延至|调整|变更|更改|改)[到至为成]?$"
)
CANCELLATION = re.compile(
    r"取消|作废|不用交|无需提交|不(?:用|需要|必)(?:再)?(?:提交|上交|交)|不要(?:再)?(?:提交|上交)"
    r"|(?:没有|并未|并非|未|不)(?:再)?(?:延期|延迟|推迟|提前|调整)"
)


LIST_MARKER = re.compile(r"(?m)^\s*(?:([0-9]{1,2})[.、．]|[（(]([0-9]{1,2})[)）])\s*")


def numbered_parts(text: str) -> tuple[str, list[str]]:
    markers = list(LIST_MARKER.finditer(text))
    if len(markers) < 2 or len(markers) > 20:
        return "", []
    numbers = [int(m[1] or m[2]) for m in markers]
    if numbers != list(range(1, len(markers) + 1)):
        return "", []
    parts = [
        text[m.end() : markers[i + 1].start() if i + 1 < len(markers) else len(text)].strip()
        for i, m in enumerate(markers)
    ]
    return (text[: markers[0].start()].strip(), parts) if all(parts) else ("", [])


def is_multiple(text: str) -> bool:
    return len(list(LIST_MARKER.finditer(text))) >= 2


CANCEL_ACTION = re.compile(r"取消|作废|不(?:用|需要|必)(?:再)?(?:提交|上交|交)|无需(?:再)?提交")


def cancellation_subject(text: str) -> str:
    """Only explicit single-task cancellation, excluding questions and mixed changes."""
    if any(
        [
            re.search(r"[？?]|是否|能否|可否|能不能|可不可以|(?:取消|作废)(?:了)?(?:吗|么)", text),
            re.search(
                r"(?:未|不|没有|并非|并未|不要|不能|无需|尚未|暂不)(?:被)?(?:取消|作废)|取消.{0,6}取消",
                text,
            ),
            re.search(
                r"如果|假如|可能|或许|据说|听说|建议|考虑|计划|拟|预计|还没|没(?:有)?说", text
            ),
            DEADLINE_CHANGE.search(text),
            re.search(
                r"截止|限制|要求|资格|预约|报名|上传|文件名|命名|改为|调整|另行通知|纸质|电子版|线上|线下|仍需|照常|仅|但|不过|恢复|重新|改成",
                text,
            ),
            is_multiple(text),
        ]
    ):
        return ""
    subjects = []
    for clause in re.split(r"[，。；\n：:]", text):
        clause = clause.strip()
        action = CANCEL_ACTION.search(clause)
        if not action:
            if TASK_WORDS.search(clause):
                return ""
            continue
        tail = clause[action.end() :].strip()
        subject = clause[: action.start()].strip(" 《》【】")
        if subject and tail not in {"", "了"}:
            return ""
        if not subject and action[0] in {"取消", "作废"}:
            subject = clause[action.end() :].strip(" 《》【】")
        subject = re.sub(r"^(?:关于|通知|请注意|本次|本周|原定的?|现决定)", "", subject)
        subject = re.sub(r"(?:已|现已|正式|予以|的)?$", "", subject).strip(" 《》【】")
        for course in COURSES:
            if subject.startswith(course):
                subject = subject[len(course) :].lstrip("的 ")
                break
        if subject in {"", "本次", "本周", "现已", "请大家", "同学们"}:
            continue
        if re.search(r"和|以及|、|与|及|全部|所有|等任务", subject):
            return ""
        if 2 <= len(subject) <= 100 and TASK_WORDS.search(subject):
            subjects.append(subject)
        else:
            return ""
    return subjects[0] if subjects and len(set(subjects)) == 1 else ""


def cancellation_supported(text: str) -> bool:
    return bool(cancellation_subject(text))


def deadline_supported(text: str) -> bool:
    unchanged = re.search(
        r"(?:截止|提交|交付|上交)(?:时间|日期|期限)?(?:仍然|保持)?(?:不变|未变|不调整|不更改)",
        text,
    )
    question = re.search(
        r"(?:是否|能否|可否|能不能|可不可以).{0,8}(?:延期|推迟|提前)"
        r"|(?:延期|推迟|提前)(?:吗|么|[？?])",
        text,
    )
    return bool(DEADLINE_CHANGE.search(text)) and not any(
        [
            CANCELLATION.search(text),
            unchanged,
            question,
            is_multiple(text),
        ]
    )


def infer_change_subject(text: str) -> str:
    # The comma before an explicit revision belongs to the same deadline change.
    for clause in re.split(r"[。；\n]", text):
        change = DEADLINE_CHANGE.search(clause)
        if not change:
            continue
        prefix = clause[: change.start()]
        subject = re.split(r"[，,：:]", prefix.split("原定", 1)[0])[-1].strip(" 《》【】")
        subject = re.sub(r"^(?:关于|通知|请注意|本次)", "", subject)
        subject = re.sub(r"(?:的)?(?:(?:截止|提交|交付|上交)(?:时间|日期|期限))?的?$", "", subject)
        for course in COURSES:
            if subject.startswith(course):
                subject = subject[len(course) :].lstrip("的 ")
                break
        if 2 <= len(subject) <= 100:
            return subject
    return ""


@dataclass
class DateResult:
    due_at: datetime | None = None
    quote: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class Extraction:
    fields: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    action: str = "create"
    method: str = "rule"
    needs_llm: bool = True


def number(value: str) -> int:
    return chinese_number(value)


def lab_schedule_quotes(text: str) -> list[str]:
    quotes = []
    for clause in re.split(r"[，,。；;！!？?\n]", text):
        clause = clause.strip()
        if not clause or len(clause) > 240:
            continue
        if CHANGE_WORDS.search(clause) or re.search(
            r"如果|假如|若|可能|是否|待定|另行通知|不(?:再)?(?:进行|参加|开展)实验|截止|提交|上交",
            clause,
        ):
            continue
        stamp = DATE.search(clause)
        if stamp and (
            re.search(r"实验(?:时间|日期|安排)[：:\s为定于]*$", clause[: stamp.start()])
            or re.search(r"(?:进行|参加|开展)实验|(?:到|前往)实验室", clause[stamp.end() :])
            or ("实验课" in clause and class_schedule_date(clause, stamp.end()))
        ):
            if clause not in quotes:
                quotes.append(clause)
            if len(quotes) == 3:
                break
    return quotes


def learning_end_label(text: str, prefix: str) -> bool:
    """An explicit learning-platform end field, not the end of a class/activity."""
    label = re.search(
        r"(?P<task>作业|考试|测验|任务|提交)?结束(?:时间|日期)[：:\s为至到]*$", prefix
    )
    if not label or re.search(r"(?:并非|不是|无需|不用|不要)[^：:]{0,12}[：:\s]*$", prefix):
        return False
    if label["task"]:
        return True
    if re.search(
        r"(?:实验(?:课)?|课程|课堂|上课|活动|会议|讲座|直播|签到)(?:的)?\s*$",
        prefix[: label.start()],
    ):
        return False
    return not re.search(
        r"实验(?:时间|安排)|上课时间|课程时间|活动(?:时间|安排|通知)|会议|讲座|直播|签到", text
    )


def date_period_suffix(suffix: str) -> str:
    """Classify date-only wording without inventing an hour for '晚之前'."""
    return re.sub(r"^\s*(?:上午|早上|下午|晚上|晚间|中午|凌晨|午夜|早|晚)\s*", "", suffix)


def question_clause(clause: str) -> bool:
    return bool(re.search(r"(?:吗|么|呢)\s*$|^\s*(?:请问|想问|我想问|咨询一下)", clause))


def unasserted_date(text: str, start: int, end: int) -> bool:
    prefix = CLAUSE_BOUNDARY.split(text[:start])[-1]
    suffix = CLAUSE_BOUNDARY.split(text[end:])[0]
    return bool(NEGATED_DATE.search(prefix)) or question_clause(prefix + suffix)


def deadline_clause_score(text: str, start: int, end: int, *, learning_end: bool = False) -> int:
    prefix = re.split(r"[，,。；;！!？?\n]", text[:start])[-1].strip()
    suffix = re.split(r"[，,。；;！!？?\n]", text[end:])[0].strip()
    suffix = date_period_suffix(suffix)
    if learning_end and learning_end_label(text, prefix):
        return 2
    if re.search(r"发布时间|发布于|布置于|开始时间|开课时间|实验时间", prefix):
        return 0
    if re.match(r"发布|布置|发出|收到|之后|以后|上课(?!前)|开始(?!前)", suffix):
        return 0
    if re.search(r"(?:不是|无需|不用|不要|并非)[^：:]{0,12}$", prefix):
        return 0
    if REVISED_DEADLINE_PREFIX.search(prefix):
        return 3
    if re.search(
        r"(?:截止(?:时间|日期)?|截至|(?:最晚|最迟)(?:提交)?(?:时间|日期)?)[：:\s至到为是]*$",
        prefix,
    ):
        return 2
    if re.match(r"(?:截止|为止)", suffix) or BEFORE_SUBMISSION.match(suffix):
        return 2
    if SUBMISSION_AFTER_DATE.match(suffix) or BEFORE_CLASS_SUBMISSION.match(suffix):
        return 1
    if re.match(r"收齐|交齐|交稿", suffix) and TASK_WORDS.search(text):
        return 1
    return 0


def class_schedule_date(text: str, date_end: int) -> bool:
    suffix = text[date_end:]
    if clock := TIME.match(suffix):
        suffix = suffix[clock.end() :]
    return bool(
        re.match(
            r"(?:有|安排|进行)?(?:第[一二三四五六七八九十百\d]+次)?"
            r"(?:实验课|上课|开课)(?!前)",
            date_period_suffix(suffix).lstrip(),
        )
    )


def class_schedule_evidence(text: str, quote: str) -> bool:
    if not quote or not DATE.search(quote):
        return False
    spans = list(re.finditer(re.escape(quote), text))
    return bool(spans) and all(
        any(class_schedule_date(text, span.start() + date.end()) for date in DATE.finditer(quote))
        for span in spans
    )


def course_schedule_date(text: str, suffix: str) -> bool:
    """Do not borrow a class date for a separate assignment in the same notice."""
    return not re.search(r"作业|报告|习题|提交|上交|补交|上传", text) and bool(
        re.match(
            r"\s*(?:有|安排|进行)?(?:第[一二三四五六七八九十百\d]+次)?"
            r"(?:考试|答辩|开会|举行会议)(?!取消|延期)",
            suffix,
        )
    )


def active_date_matches(text: str):
    """An explicit revision target supersedes the old deadline's date/precision."""
    matches = list(DATE.finditer(text))
    if not deadline_supported(text):
        return matches
    revised = []
    for match in matches:
        prefix = re.split(r"[，,。；;！!？?\n]", text[: match.start()])[-1].strip()
        if REVISED_DEADLINE_PREFIX.search(prefix) or re.search(
            r"新(?:的)?截止(?:时间|日期)?[：:\s为至到]*$", prefix
        ):
            revised.append(match)
    return revised or matches


def parse_deadline(
    text: str, sent_at: datetime, timezone: str, *, scoped_deadline: bool = False
) -> DateResult:
    anchor = sent_at.replace(tzinfo=UTC) if sent_at.tzinfo is None else sent_at
    anchor = anchor.astimezone(ZoneInfo(timezone))
    candidates: list[tuple[int, datetime, str]] = []
    warnings: list[str] = []
    found_date = False
    for match in active_date_matches(text):
        if class_schedule_date(text, match.end()):
            continue
        found_date = True
        clock = TIME.match(text[match.end() :])
        if clock is None:
            continue
        end = match.end() + clock.end()
        if unasserted_date(text, match.start(), end):
            continue
        tail = text[end:]
        if (separator := SUBMISSION_WINDOW.match(tail)) and TIME.match(tail[separator.end() :]):
            warnings.append("通知包含提交时间段，请核对实际截止时间。")
            continue
        hour = number(clock["hour"]) if clock["hour"] else 0
        minute = (
            int(clock["minute"])
            if clock["minute"]
            else (
                30
                if clock["cnminute"] == "半"
                else 0
                if clock["cnminute"] in {None, "整"}
                else number(clock["cnminute"])
            )
        )
        if clock["cnminute"] in {"一刻", "三刻"}:
            minute = {"一刻": 15, "三刻": 45}[clock["cnminute"]]
        period = clock["period"] or ("晚上" if match["relative"] in {"今晚", "明晚"} else "")
        if period in {"晚上", "晚间", "晚"} and hour == 12:
            warnings.append("晚上12点的日期归属不明确，请手动补充。")
            continue
        if period in {"下午", "晚上", "晚间", "晚", "中午"} and hour < 12:
            hour += 12
        elif period == "凌晨" and hour == 12:
            hour = 0
        midnight = hour == 24 and minute == 0 and not period
        if not (midnight or 0 <= hour <= 23 and 0 <= minute <= 59):
            warnings.append("原文时间不合法，请核对小时和分钟。")
            continue
        try:
            resolved = datetime.combine(resolve_day(match, anchor), datetime.min.time())
            if clock["iso_clock"]:
                due = datetime.fromisoformat(resolved.date().isoformat() + clock["iso_clock"])
                if due.tzinfo is None:
                    due = due.replace(tzinfo=anchor.tzinfo)
            else:
                due = resolved.replace(hour=hour % 24, minute=minute, tzinfo=anchor.tzinfo)
                if midnight:
                    due += timedelta(days=1)
        except (ValueError, OverflowError):
            warnings.append("原文日期不合法，请手动核对。")
            continue
        nearby = text[end : end + 16]
        prefix = text[max(0, match.start() - 10) : match.start()]
        score = 2 if re.search(r"截止|[之以]?前|最[晚迟]", nearby + prefix) else 0
        if re.search(
            r"(?:延期|延迟|推迟|提前|顺延|延后|延至|调整|变更|更改|改)[到至为成]?\s*$", prefix
        ):
            score = 3
        if not score and re.search(r"提交|上交|完成|上传", nearby):
            score = 1
        if re.match(r"\s*(?:发布|布置|发出|收到|之后|以后|上课(?!前)|开始(?!前))", nearby):
            score = 0
        if not scoped_deadline and not score and course_schedule_date(text, nearby):
            score = 1
        if scoped_deadline:
            score = deadline_clause_score(text, match.start(), end, learning_end=True)
        candidates.append((score, due, text[match.start() : end]))
    if not candidates:
        if found_date and not warnings:
            warnings.append("通知未明确具体截止时刻，已留空，请补充。")
        elif not warnings:
            warnings.append("未找到明确截止时间，已留空，请补充。")
        return DateResult(warnings=list(dict.fromkeys(warnings)))
    best = max(c[0] for c in candidates)
    if best == 0:
        return DateResult(warnings=["发现日期和时刻，但无法确认是截止时间，请手动核对。"])
    options = [c for c in candidates if c[0] == best]
    if len({c[1] for c in options}) > 1:
        return DateResult(warnings=["通知包含多个可能的截止时间，请核对后手动选择。"])
    _, due, quote = options[0]
    if due < anchor:
        warnings.append("截止时间早于消息发送时间，请核对是否已经逾期。")
    return DateResult(due.astimezone(UTC), quote, list(dict.fromkeys(warnings)))


def has_alternative_dates(text: str) -> bool:
    for first, second in pairwise(DATE.finditer(text)):
        between = text[first.end() : second.start()]
        if clock := TIME.match(between):
            between = between[clock.end() :]
        between = date_period_suffix(between)
        if re.fullmatch(r"\s*(?:(?:之|以)?前|截止)?\s*(?:或者|或)\s*", between):
            return True
    return False


def parse_date_only(
    text: str, sent_at: datetime, timezone: str, *, scoped_deadline: bool = False
) -> tuple[date, str] | None:
    """Return an unambiguous civil deadline without inventing a clock time."""
    anchor = sent_at.replace(tzinfo=UTC) if sent_at.tzinfo is None else sent_at
    anchor = anchor.astimezone(ZoneInfo(timezone))
    candidates = []
    for match in active_date_matches(text):
        if TIME.match(text[match.end() :]) or class_schedule_date(text, match.end()):
            continue
        if unasserted_date(text, match.start(), match.end()):
            continue
        if scoped_deadline and not deadline_clause_score(
            text, match.start(), match.end(), learning_end=True
        ):
            continue
        prefix = re.split(r"[，,。；;！!？?\n]", text[: match.start()])[-1][-20:]
        suffix = date_period_suffix(re.split(r"[，,。；;！!？?\n]", text[match.end() :])[0][:20])
        if re.search(r"(?:不|无需|不用|不要|并非)[^，。；]{0,8}$", prefix):
            continue
        if not (
            re.search(r"截止|截至|最晚|最迟|延期至|延至|改至|改到", prefix)
            or (scoped_deadline and learning_end_label(text, prefix.strip()))
            or REVISED_DEADLINE_PREFIX.search(prefix.strip())
            or re.match(r"\s*(?:之前|以前|前|截止|为止|提交|上交|上传|完成)", suffix)
            or SUBMISSION_AFTER_DATE.match(suffix)
            or BEFORE_CLASS_SUBMISSION.match(suffix.strip())
            or course_schedule_date(text, suffix)
        ):
            continue
        try:
            day = resolve_day(match, anchor)
        except (ValueError, OverflowError):
            continue
        if (
            (
                BEFORE_CLASS_SUBMISSION.match(suffix.strip())
                or BEFORE_SUBMISSION.match(suffix.strip())
            )
            and re.fullmatch(r"(?:周|星期)[一二三四五六日天]", match[0])
            and day < anchor.date()
        ):
            day += timedelta(days=7)
        period = re.match(r"\s*(?:上午|早上|下午|晚上|晚间|中午|凌晨|早|晚)", text[match.end() :])
        quote = text[match.start() : match.end() + (period.end() if period else 0)]
        candidates.append((day, quote))
    if not candidates or len({day for day, _ in candidates}) != 1:
        return None
    return candidates[0]


def infer_title(text: str) -> str:
    match = re.search(r"(?:提交|上交|补交|上传|完成|撰写)([^，。；\n]{2,100})", text)
    if match:
        title = re.split(r"(?:至|到)(?:学习通|课程群|邮箱)|[，。；\n]", match[1])[0].strip(" ：:。")
        # Avoid treating "时间延期" or platform names as task names.
        if 2 <= len(title) <= 100 and not title.startswith(("时间", "截止", "至", "到")):
            return title
    # Keep clock fragments out of fallback names (9:00 previously yielded "00进行期中考试").
    # Do not alter the source text or remove digits that belong to task names.
    title_text = re.sub(TIME.pattern.removeprefix("^"), "：", text)
    match = re.search(
        r"([\u4e00-\u9fffA-Za-z0-9]{0,14}(?:实验[一二三四五六七八九十0-9]+|课后习题|阅读练习|需求分析报告|作业|考试|报名))",
        title_text,
    )
    if match:
        return re.sub(r"^(?:请|通知|关于|本次|本周|进行|举行|参加|组织|安排|开展)+", "", match[1])[
            :100
        ]
    return ""


STATUS_NOTICE = re.compile(
    r"(?!.*(?:请|务必|需要|必须|确认|核对|检查|是否|如果|重新|尚未|未能))"
    r"[\w《》· -]{0,30}"
    r"(?:(?:作业|考试|测验|实验)?(?:成绩|分数|批阅结果)(?:已|已经)(?:公布|发布|更新)"
    r"|(?:作业|报告|试卷|考试|测验)(?:提交成功|已(?:经)?(?:提交(?:成功)?|批阅|批改|阅卷|结束|截止))"
    r"|(?:本周|这周|今天|本次)?(?:没有|暂无|无)(?:新的?)?作业)"
)
STATUS_FOLLOWUP = re.compile(
    r"(?:请(?:自行|注意|及时)?(?:查看|查收)|可(?:在学习通|在课程群)?查看(?:成绩|结果)?"
    r"|(?:系统)?回执仅供留存)"
)


def status_only_notice(text: str) -> bool:
    first, *rest = re.split(r"[，,。；;！!\n]+", text.replace("\u200b", "").strip())
    prefix, sep, body = (
        re.split(r"([：:])", first, maxsplit=1) if re.search(r"[：:]", first) else ("", "", first)
    )
    if sep and prefix.strip() not in {*COURSES, "通知", "系统通知", "提交回执", "成绩通知"}:
        return False
    return bool(STATUS_NOTICE.fullmatch(body.strip())) and all(
        not clause.strip() or STATUS_FOLLOWUP.fullmatch(clause.strip()) for clause in rest
    )


def extract_rules(
    text: str, sent_at: datetime | None, timezone: str, *, scoped_deadline: bool = False
) -> Extraction:
    result = Extraction(
        fields={
            "title": "",
            "course": "",
            "kind": "other",
            "description": text,
            "due_at": None,
            "due_date": None,
            "submission_platform": "",
            "submission_method": "",
            "file_requirements": "",
            "location": "",
            "remarks": "",
        }
    )
    if CHANGE_WORDS.search(text) or DEADLINE_CHANGE.search(text) or cancellation_supported(text):
        result.action = "review"
        result.warnings.append(
            "检测到截止时间变更，请核对目标任务和新旧时间后确认。"
            if deadline_supported(text)
            else "检测到任务取消，请核对目标任务后确认。"
            if cancellation_supported(text)
            else "检测到其他变更或取消信息，请人工核对，不自动修改或创建任务。"
        )
    elif status_only_notice(text) or not any(
        TASK_WORDS.search(clause) and not question_clause(clause)
        for clause in CLAUSE_BOUNDARY.split(text)
    ):
        result.action = "ignore"
        result.warnings.append("未发现明确任务线索。可忽略，或通过“新建任务”手动记录。")
    title = (
        cancellation_subject(text)
        or (infer_change_subject(text) if deadline_supported(text) else "")
        or infer_title(text)
    )
    if title:
        result.fields["title"] = title
        result.evidence["title"] = {"method": "rule", "quote": title}
    course = next((c for c in COURSES if c in text), "")
    if course:
        result.fields["course"] = course
        result.evidence["course"] = {"method": "rule", "quote": course}
    if sent_at is None:
        deadline = DateResult(warnings=["发布时间未知，请核对原文并手动填写截止时间。"])
        date_only = None
    elif cancellation_supported(text):
        deadline, date_only = DateResult(), None
    elif has_alternative_dates(text):
        deadline = DateResult(warnings=["通知包含多个可能的截止时间，请核对后手动选择。"])
        date_only = None
    else:
        deadline = parse_deadline(text, sent_at, timezone, scoped_deadline=scoped_deadline)
        date_only = parse_date_only(text, sent_at, timezone, scoped_deadline=scoped_deadline)
    if (
        deadline.due_at
        and date_only
        and deadline.due_at.astimezone(ZoneInfo(timezone)).date() != date_only[0]
    ):
        deadline = DateResult(warnings=["通知包含多个可能的截止时间，请核对后手动选择。"])
        date_only = None
    result.fields["due_at"] = deadline.due_at.isoformat() if deadline.due_at else None
    result.warnings.extend(deadline.warnings)
    if deadline.quote:
        result.evidence["due_at"] = {"method": "rule", "quote": deadline.quote}
    elif date_only and not any(
        "多个可能的截止时间" in w or "提交时间段" in w for w in deadline.warnings
    ):
        result.fields["due_date"] = date_only[0].isoformat()
        result.evidence["due_date"] = {"method": "rule", "quote": date_only[1]}
        result.warnings = [
            w for w in result.warnings if w != "通知未明确具体截止时刻，已留空，请补充。"
        ]
        result.warnings.append("原文未明确具体时刻，已保留日期，请核对。")
    for platform in ["学习通", "雨课堂", "课程群", "QQ", "微信", "邮箱"]:
        if platform in text:
            result.fields["submission_platform"] = platform
            result.evidence["submission_platform"] = {"method": "rule", "quote": platform}
            break
    naming = re.search(
        r"(?:文件名|命名|文件命名)(?:为|要求|统一为|：|:)?\s*([^，。；\n]{2,120})", text
    )
    if naming:
        result.fields["file_requirements"] = naming[1].strip()
        result.evidence["file_requirements"] = {"method": "rule", "quote": naming[0]}
    kind = (
        "exam"
        if "考试" in text
        else "homework"
        if re.search(r"作业|实验|报告|习题|练习", text)
        else "event"
        if "报名" in text
        else "other"
    )
    result.fields["kind"] = kind
    if is_multiple(text):
        result.action = "review"
        result.warnings.append("这段通知包含多个条目，请生成拆分草稿后逐项核对。")
    result.needs_llm = (
        result.action != "ignore"
        and not is_multiple(text)
        and not cancellation_supported(text)
        and not (
            title
            and deadline.due_at
            and not deadline.warnings
            and (result.action == "create" or deadline_supported(text))
        )
    )
    return result


def merge_model(
    result: Extraction,
    model: dict,
    text: str,
    sent_at: datetime,
    timezone: str,
    *,
    scoped_deadline: bool = False,
) -> Extraction:
    """Only source-supported, missing fields may be filled by the model."""
    mapping = {
        "title": "title",
        "course": "course",
        "submission_platform": "submission_platform",
        "submission_method": "submission_method",
        "file_requirements": "file_requirements",
        "location": "location",
        "remarks": "remarks",
    }
    rejected = False
    changed = False
    for key, target in mapping.items():
        value = model.get(key)
        if not value or result.fields.get(target):
            continue
        generic_titles = {"通知", "课程通知", "课程安排", "公告", "提醒"}
        course = result.fields.get("course", "")
        generic_titles.update({course + "课程通知", course + "通知", course + "课程安排"})
        platforms = {"学习通", "雨课堂", "QQ", "微信", "课程群", "邮箱"}
        invalid_meaning = (
            target == "title"
            and value.strip("《》：: ") in generic_titles
            or target == "location"
            and value in platforms
            or target == "file_requirements"
            and value.strip("《》") == result.fields.get("title")
        )
        if value not in text or invalid_meaning:
            rejected = True
            continue
        result.fields[target] = value
        result.evidence[target] = {"method": "llm", "quote": value}
        changed = True
    due_text = model.get("due_text")
    if (
        due_text
        and not result.fields.get("due_at")
        and not result.fields.get("due_date")
        and not any("多个可能的截止时间" in w or "提交时间段" in w for w in result.warnings)
    ):
        source_position = text.find(due_text)
        prefix = re.split(r"[，,。；;！!？?\n]", text[:source_position])[-1]
        suffix = text[source_position + len(due_text) :]
        unsupported_context = bool(
            re.search(r"发布时间|发布于|布置于|开始时间|开课时间|上课时间", prefix)
            or re.search(r"(?:不是|并非|无需|不用|不要)[^，。；]{0,12}$", prefix)
            or re.match(r"\s*(?:上课|发布|布置|开始|之后|以后)", suffix)
        )
        if (
            due_text not in text
            or has_alternative_dates(text)
            or len(list(DATE.finditer(text))) > 1
            or class_schedule_evidence(text, due_text)
            or any(
                unasserted_date(text, source_position + date.start(), source_position + date.end())
                for date in DATE.finditer(due_text)
            )
            or unsupported_context
            or (
                scoped_deadline
                and deadline_clause_score(
                    text, source_position, source_position + len(due_text), learning_end=True
                )
                == 0
            )
        ):
            rejected = True
        else:
            # The model selects an evidence span, never a computed date. The same
            # deterministic calendar resolver handles precise and date-only output.
            selected = "截止时间：" + due_text
            parsed = parse_deadline(selected, sent_at, timezone)
            if parsed.due_at:
                result.fields["due_at"] = parsed.due_at.isoformat()
                result.evidence["due_at"] = {"method": "llm+date", "quote": due_text}
                changed = True
            elif day := parse_date_only(selected, sent_at, timezone):
                result.fields["due_date"] = day[0].isoformat()
                result.evidence["due_date"] = {"method": "llm+date", "quote": due_text}
                result.warnings.append("原文未明确具体时刻，已保留日期，请核对。")
                changed = True
            if result.fields.get("due_at") or result.fields.get("due_date"):
                result.warnings = [
                    w
                    for w in result.warnings
                    if w
                    not in {
                        "通知未明确具体截止时刻，已留空，请补充。",
                        "未找到明确截止时间，已留空，请补充。",
                        "发现日期和时刻，但无法确认是截止时间，请手动核对。",
                    }
                ]
    if rejected:
        result.warnings.append("模型部分字段缺乏原文依据或语义不符，已舍弃；请手动核对。")
    if changed:
        result.method = "mixed"
    return result
