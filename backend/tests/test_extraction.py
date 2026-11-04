from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from campus_inbox.extraction import extract_rules, merge_model, parse_deadline

ANCHOR = datetime(2026, 10, 29, 14, 20, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("数据库原理：下周一上午9:00进行期中考试，地点为教学楼A301。", "期中考试"),
        ("数据库原理：下周一上午9：30举行期中考试。", "期中考试"),
        ("数据库原理：9:00进行期中考试。", "期中考试"),
        ("数据库原理：下周一上午九点举行期中考试。", "期中考试"),
        ("数据库原理：下周一上午9:00进行第2次考试。", "第2次考试"),
        ("2026级考试", "2026级考试"),
        ("大学英语：英语四级考试", "英语四级考试"),
        ("软件工程：请于明天18:00前提交实验四报告。", "实验四报告"),
    ],
)
def test_task_titles_do_not_include_clock_minutes(text, expected):
    result = extract_rules(text, ANCHOR, "Asia/Shanghai")
    assert result.fields["title"] == expected
    assert result.evidence["title"]["quote"] == expected
    assert expected in text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("明天23:59前提交报告", "2026-10-30T15:59:00+00:00"),
        ("下周一下午六点提交报告", "2026-11-02T10:00:00+00:00"),
        ("周五18:00截止", "2026-10-30T10:00:00+00:00"),
        ("2026年11月2日23:59截止", "2026-11-02T15:59:00+00:00"),
        ("2026-11-02 18:00截止", "2026-11-02T10:00:00+00:00"),
        ("今晚八点提交", "2026-10-29T12:00:00+00:00"),
    ],
)
def test_dates_use_message_context(text, expected):
    assert parse_deadline(text, ANCHOR, "Asia/Shanghai").due_at.isoformat() == expected


@pytest.mark.parametrize(
    "text",
    [
        "周五截止",
        "11月2日提交",
        "10月31日25:90截止",
        "11月31日18:00截止",
        "10月30日18:00或10月31日18:00截止",
        "提交报告，截止日期另行通知",
    ],
)
def test_ambiguous_or_invalid_time_stays_empty(text):
    result = parse_deadline(text, ANCHOR, "Asia/Shanghai")
    assert result.due_at is None
    assert result.warnings


def test_complete_rule_result_skips_model():
    result = extract_rules(
        "软件工程：请于10月31日18:00前提交需求分析报告，文件名为学号+姓名。",
        ANCHOR,
        "Asia/Shanghai",
    )
    assert result.fields["title"] == "需求分析报告"
    assert result.fields["course"] == "软件工程"
    assert result.fields["file_requirements"] == "学号+姓名"
    assert result.evidence["due_at"]["quote"] == "10月31日18:00"
    assert not result.needs_llm


def test_changes_never_become_an_ordinary_new_task():
    result = extract_rules("数据库实验三延期至下周一23:59。", ANCHOR, "Asia/Shanghai")
    assert result.action == "review"


def test_model_cannot_invent_fields_or_overwrite_rules():
    text = "软件工程：请于10月31日18:00前提交需求分析报告。"
    rules = extract_rules(text, ANCHOR, "Asia/Shanghai")
    result = merge_model(
        rules,
        {"title": "新的任务", "course": "高等数学", "location": "月球", "due_text": "明天18:00"},
        text,
        ANCHOR,
        "Asia/Shanghai",
    )
    assert result.fields["title"] == "需求分析报告"
    assert result.fields["course"] == "软件工程"
    assert result.fields["location"] == ""
    assert result.fields["due_at"] == "2026-10-31T10:00:00+00:00"
    assert any("舍弃" in w for w in result.warnings)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("明天下午6点30分前提交作业", "2026-10-30T10:30:00+00:00"),
        ("明天下午六点一刻前提交报告", "2026-10-30T10:15:00+00:00"),
        ("明天中午12点前提交报告", "2026-10-30T04:00:00+00:00"),
    ],
)
def test_explicit_minutes_and_periods(text, expected):
    assert parse_deadline(text, ANCHOR, "Asia/Shanghai").due_at.isoformat() == expected


@pytest.mark.parametrize(
    "text",
    [
        "明天18:00发布作业",
        "明天18:00之后提交报告",
        "明天晚上12点前提交报告",
        "10月31日18:00上课，记得提交报告",
    ],
)
def test_clock_time_does_not_automatically_mean_deadline(text):
    assert parse_deadline(text, ANCHOR, "Asia/Shanghai").due_at is None


def test_source_supported_header_or_platform_is_not_a_valid_task_or_place():
    text = (
        "软件工程课程通知：本次交付物为《校园二手交易平台需求规格说明书》，明天20:00前放到学习通。"
    )
    result = merge_model(
        extract_rules(text, ANCHOR, "Asia/Shanghai"),
        {"title": "软件工程课程通知", "location": "学习通"},
        text,
        ANCHOR,
        "Asia/Shanghai",
    )
    assert result.fields["title"] == ""
    assert result.fields["location"] == ""
    assert any("语义不符" in warning for warning in result.warnings)
