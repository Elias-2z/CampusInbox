"""Conservative, independently testable extraction. No database or network access."""

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

import dateparser

DATE = re.compile(
    r"(?:(?P<year>\d{4})年)?(?P<month>\d{1,2})月(?P<day>\d{1,2})[日号]?"
    r"|(?P<iso>\d{4}[-/]\d{1,2}[-/]\d{1,2})"
    r"|(?P<relative>今天|明天|后天|今晚|明晚|(?:下周|本周|这周|周|星期)[一二三四五六日天])"
)
TIME = re.compile(
    r"^\s*(?:的)?\s*(?P<period>上午|早上|下午|晚上|晚间|中午|凌晨)?\s*"
    r"(?P<hour>\d{1,2}|[一二三四五六七八九十两]{1,3})"
    r"(?:(?:[:：])(?P<minute>\d{2})|(?:点|时)(?P<cnminute>\d{1,2}|一刻|三刻|半|整|[零一二三四五六七八九十两]{1,3})?(?:分)?)"
)
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
    r"[^。，；\n]{0,40}(?:调整|改[为到成]|变更)"
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
        subject = clause[: action.start()].strip(" 《》")
        if subject and tail not in {"", "了"}:
            return ""
        if not subject and action[0] in {"取消", "作废"}:
            subject = clause[action.end() :].strip(" 《》")
        subject = re.sub(r"^(?:关于|通知|请注意|本次|本周|原定的?|现决定)", "", subject)
        subject = re.sub(r"(?:已|现已|正式|予以|的)?$", "", subject).strip(" 《》")
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
    for clause in re.split(r"[，。；\n：:]", text):
        change = DEADLINE_CHANGE.search(clause)
        if not change:
            continue
        subject = clause[: change.start()].strip(" 《》")
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
    if value.isdigit():
        return int(value)
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    digits["两"] = 2
    if "十" in value:
        left, right = value.split("十", 1)
        return digits.get(left, 1) * 10 + digits.get(right, 0)
    return digits.get(value, -1)


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
        ):
            if clause not in quotes:
                quotes.append(clause)
            if len(quotes) == 3:
                break
    return quotes


def deadline_clause_score(text: str, start: int, end: int) -> int:
    prefix = re.split(r"[，,。；;！!？?\n]", text[:start])[-1].strip()
    suffix = re.split(r"[，,。；;！!？?\n]", text[end:])[0].strip()
    if re.search(r"发布时间|发布于|布置于|开始时间|开课时间|实验时间", prefix):
        return 0
    if re.match(r"发布|布置|发出|收到|之后|以后|上课(?!前)|开始(?!前)", suffix):
        return 0
    if re.search(r"(?:无需|不用|不要|并非)[^：:]{0,12}$", prefix):
        return 0
    if REVISED_DEADLINE_PREFIX.search(prefix):
        return 3
    if re.search(r"(?:截止(?:时间|日期)?|截至|最晚|最迟)[：:\s至到为]*$", prefix):
        return 2
    if re.match(r"(?:截止|为止|(?:之|以)?前\s*(?:提交|上交|上传|完成|交|将|把))", suffix):
        return 2
    if re.match(r"(?:提交|上交|上传|补交)", suffix):
        return 1
    return 0


def parse_deadline(
    text: str, sent_at: datetime, timezone: str, *, scoped_deadline: bool = False
) -> DateResult:
    anchor = sent_at.replace(tzinfo=UTC) if sent_at.tzinfo is None else sent_at
    anchor = anchor.astimezone(ZoneInfo(timezone))
    candidates: list[tuple[int, datetime, str]] = []
    warnings: list[str] = []
    found_date = False
    for match in DATE.finditer(text):
        found_date = True
        clock = TIME.match(text[match.end() :])
        if clock is None:
            continue
        hour = number(clock["hour"])
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
        if period in {"晚上", "晚间"} and hour == 12:
            warnings.append("晚上12点的日期归属不明确，请手动补充。")
            continue
        if period in {"下午", "晚上", "晚间", "中午"} and hour < 12:
            hour += 12
        elif period == "凌晨" and hour == 12:
            hour = 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            warnings.append("原文时间不合法，请核对小时和分钟。")
            continue
        try:
            if match["relative"]:
                token = match["relative"]
                if token in {"今天", "明天", "后天", "今晚", "明晚"}:
                    offset = {"今天": 0, "今晚": 0, "明天": 1, "明晚": 1, "后天": 2}[token]
                    day = anchor.date() + timedelta(days=offset)
                else:
                    weekday = "一二三四五六日".index(token[-1].replace("天", "日"))
                    week = 7 if token.startswith("下周") else 0
                    day = anchor.date() + timedelta(days=weekday - anchor.weekday() + week)
                resolved = datetime.combine(day, datetime.min.time())
            else:
                # dateparser handles explicit calendar syntax; missing time is never supplied by it.
                token = (
                    match["iso"]
                    or f"{match['year'] or anchor.year}年{match['month']}月{match['day']}日"
                )
                resolved = dateparser.parse(
                    token, settings={"DATE_ORDER": "YMD", "STRICT_PARSING": True}
                )
                if resolved is None:
                    raise ValueError("invalid calendar date")
                if not match["year"] and not match["iso"] and resolved.date() < anchor.date():
                    warnings.append("日期未写年份且早于消息发送日，请核对年份后手动补充。")
                    continue
            due = resolved.replace(
                hour=hour, minute=minute, second=0, microsecond=0, tzinfo=anchor.tzinfo
            )
        except (ValueError, OverflowError):
            warnings.append("原文日期不合法，请手动核对。")
            continue
        end = match.end() + clock.end()
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
        if scoped_deadline:
            score = deadline_clause_score(text, match.start(), end)
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
    for match in DATE.finditer(text):
        if TIME.match(text[match.end() :]):
            continue
        if scoped_deadline and not deadline_clause_score(text, match.start(), match.end()):
            continue
        prefix = re.split(r"[，,。；;！!？?\n]", text[: match.start()])[-1][-20:]
        suffix = re.split(r"[，,。；;！!？?\n]", text[match.end() :])[0][:20]
        if re.search(r"(?:不|无需|不用|不要|并非)[^，。；]{0,8}$", prefix):
            continue
        if not (
            re.search(r"截止|截至|最晚|最迟|延期至|延至|改至|改到", prefix)
            or REVISED_DEADLINE_PREFIX.search(prefix.strip())
            or re.match(r"\s*(?:之前|以前|前|截止|为止|提交|上交|上传|完成)", suffix)
        ):
            continue
        try:
            if match["relative"]:
                token = match["relative"]
                if token in {"今天", "明天", "后天", "今晚", "明晚"}:
                    day = anchor.date() + timedelta(
                        days={"今天": 0, "今晚": 0, "明天": 1, "明晚": 1, "后天": 2}[token]
                    )
                else:
                    weekday = "一二三四五六日".index(token[-1].replace("天", "日"))
                    week = 7 if token.startswith("下周") else 0
                    day = anchor.date() + timedelta(days=weekday - anchor.weekday() + week)
            elif match["iso"]:
                year, month, day_number = map(int, re.split(r"[-/]", match["iso"]))
                day = date(year, month, day_number)
            else:
                day = date(
                    int(match["year"] or anchor.year), int(match["month"]), int(match["day"])
                )
                if not match["year"] and day < anchor.date():
                    continue
        except (ValueError, OverflowError):
            continue
        candidates.append((day, match.group()))
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
    elif not TASK_WORDS.search(text):
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
    elif date_only and not any("多个可能的截止时间" in w for w in deadline.warnings):
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
        not is_multiple(text)
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
        and not any("多个可能的截止时间" in w for w in result.warnings)
    ):
        if scoped_deadline or due_text not in text:
            rejected = True
        else:
            parsed = parse_deadline(due_text, sent_at, timezone)
            if parsed.due_at:
                result.fields["due_at"] = parsed.due_at.isoformat()
                result.evidence["due_at"] = {"method": "llm+date", "quote": due_text}
                changed = True
    if rejected:
        result.warnings.append("模型部分字段缺乏原文依据或语义不符，已舍弃；请手动核对。")
    if changed:
        result.method = "mixed"
    return result
