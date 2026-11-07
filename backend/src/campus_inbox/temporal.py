"""Calendar resolution anchored to the source message, never to wall-clock time."""

import calendar
import re
from datetime import date, timedelta


def chinese_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    digits.update({"两": 2, "〇": 0})
    if "十" in value:
        left, right = value.split("十", 1)
        return digits.get(left, 1) * 10 + digits.get(right, 0)
    if value and all(c in digits for c in value):
        return int("".join(str(digits[c]) for c in value))
    return -1


def resolve_day(match, anchor) -> date:
    token = match["relative"] or match["natural"]
    if token:
        offsets = {
            "今天": 0,
            "今晚": 0,
            "明天": 1,
            "明晚": 1,
            "后天": 2,
            "大后天": 3,
            "昨天": -1,
            "前天": -2,
        }
        if token in offsets:
            return anchor.date() + timedelta(days=offsets[token])
        week = re.fullmatch(r"(下下|下|上|本|这)?(?:周|星期|礼拜)([一二三四五六日天])", token)
        if week:
            shift = {"下下": 14, "下": 7, "上": -7}.get(week[1], 0)
            weekday = "一二三四五六日".index(week[2].replace("天", "日"))
            return anchor.date() + timedelta(days=weekday - anchor.weekday() + shift)
        delta = re.fullmatch(r"([零一二三四五六七八九十两\d]+)(天|日|周|星期)后", token)
        if delta:
            count = chinese_number(delta[1])
            if not 0 <= count <= 366:
                raise ValueError("unsupported offset")
            return anchor.date() + timedelta(days=count * (7 if delta[2] in {"周", "星期"} else 1))
        month = re.fullmatch(
            r"(本月|这个月|下月|下个月)(?:底|末|([零一二三四五六七八九十两\d]+)[日号])", token
        )
        if month:
            index = anchor.year * 12 + anchor.month - 1 + int(month[1].startswith("下"))
            year, m = divmod(index, 12)
            m += 1
            day = chinese_number(month[2]) if month[2] else calendar.monthrange(year, m)[1]
            return date(year, m, day)
        raise ValueError("unsupported calendar expression")
    if match["iso"]:
        result = date(*map(int, re.split(r"[-/.]", match["iso"])))
    elif match["short"]:
        result = date(anchor.year, *map(int, re.split(r"[-/]", match["short"])))
    else:
        result = date(
            int(match["year"] or anchor.year),
            chinese_number(match["month"]),
            chinese_number(match["day"]),
        )
    if not (match["iso"] or match["year"]) and result < anchor.date():
        raise ValueError("missing year precedes message date")
    if match["weekday"] and "一二三四五六日"[result.weekday()] != match["weekday"].replace(
        "天", "日"
    ):
        raise ValueError("weekday conflicts with date")
    return result
