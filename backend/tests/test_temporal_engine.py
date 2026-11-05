"""Calendar, source evidence and model fallback share one date contract."""

from datetime import UTC, datetime

import pytest

from campus_inbox.extraction import extract_rules, merge_model
from campus_inbox.providers import EXTRACTION_PROMPT

ANCHOR = datetime(2026, 11, 5, 7, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("phrase", "day"),
    [
        ("三天后", "2026-11-08"),
        ("两周后", "2026-11-19"),
        ("下下周三", "2026-11-18"),
        ("下礼拜一", "2026-11-09"),
        ("本礼拜三", "2026-11-04"),
        ("上周五", "2026-10-30"),
        ("大后天", "2026-11-08"),
        ("本月底", "2026-11-30"),
        ("下个月5日", "2026-12-05"),
        ("下月末", "2026-12-31"),
        ("2026年十一月八日", "2026-11-08"),
    ],
)
def test_calendar_forms_preserve_date_precision(phrase, day):
    r = extract_rules(f"截止日期：{phrase}，请提交实验报告。", ANCHOR, "Asia/Shanghai")
    assert r.fields["due_date"] == day
    assert r.fields["due_at"] is None
    assert r.evidence["due_date"]["quote"] == phrase


@pytest.mark.parametrize(
    ("anchor", "phrase", "expected"),
    [
        (datetime(2028, 2, 10, tzinfo=UTC), "本月底", "2028-02-29"),
        (datetime(2026, 12, 10, tzinfo=UTC), "下个月5日", "2027-01-05"),
        (datetime(2026, 11, 5, 20, tzinfo=UTC), "明天", "2026-11-07"),
    ],
)
def test_leap_year_year_boundary_and_source_timezone(anchor, phrase, expected):
    r = extract_rules(f"{phrase}前提交报告。", anchor, "Asia/Shanghai")
    assert r.fields["due_date"] == expected


@pytest.mark.parametrize(
    ("text", "quote", "key", "expected"),
    [
        ("请务必赶在本月底把实验报告发过来。", "本月底", "due_date", "2026-11-30"),
        ("请务必赶在下礼拜三把实验报告发过来。", "下礼拜三", "due_date", "2026-11-11"),
        ("请务必赶在11月8日晚把实验报告发过来。", "11月8日晚", "due_date", "2026-11-08"),
        (
            "请务必赶在11月8日20:00把实验报告发过来。",
            "11月8日20:00",
            "due_at",
            "2026-11-08T12:00:00+00:00",
        ),
    ],
)
def test_model_can_select_an_original_span_without_inventing_time(text, quote, key, expected):
    r = extract_rules(text, ANCHOR, "Asia/Shanghai")
    assert not r.fields.get("due_date") and not r.fields.get("due_at")
    r = merge_model(r, {"due_text": quote}, text, ANCHOR, "Asia/Shanghai")
    assert r.fields[key] == expected
    assert r.evidence[key] == {"method": "llm+date", "quote": quote}
    assert not any("已留空" in w for w in r.warnings)


@pytest.mark.parametrize(
    ("text", "quote"),
    [
        ("本月底或下个月5日前提交报告。", "本月底"),
        ("不是本月底提交报告，等通知。", "本月底"),
        ("发布时间：11月8日。请提交报告。", "11月8日"),
        ("11月8日上课，报告截止另行通知。", "11月8日"),
        ("11月8日前提交报告，11月9日前提交代码。", "11月8日"),
        ("报告截止时间另行通知。", "2026年11月8日"),
        ("本月三十一日前提交报告。", "本月三十一日"),
    ],
)
def test_model_cannot_bypass_source_conflict_negation_or_calendar_validity(text, quote):
    r = merge_model(
        extract_rules(text, ANCHOR, "Asia/Shanghai"),
        {"due_text": quote},
        text,
        ANCHOR,
        "Asia/Shanghai",
    )
    assert not r.fields.get("due_date") and not r.fields.get("due_at")


def test_shared_prompt_describes_time_precision_and_event_selection():
    assert "只" in EXTRACTION_PROMPT
    for term in ("可只有日期", "禁止自行换算", "上课时间", "11月8日晚之前", "23:59"):
        assert term in EXTRACTION_PROMPT
