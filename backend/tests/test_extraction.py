from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from campus_inbox.extraction import extract_rules, merge_model, parse_deadline

ANCHOR = datetime(2026, 10, 29, 14, 20, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.mark.parametrize(
    ("phrase", "clock", "day"),
    [
        ("11/9 20:00前提交", "2026-11-09T12:00:00+00:00", None),
        ("11-09 20:00前提交", "2026-11-09T12:00:00+00:00", None),
        ("2026.11.09 20:00前提交", "2026-11-09T12:00:00+00:00", None),
        ("截止时间：2026-11-09T20:00:00+09:00", "2026-11-09T11:00:00+00:00", None),
        ("11月9日20:00前在学习通提交", "2026-11-09T12:00:00+00:00", None),
        ("11月9日20:00前请提交", "2026-11-09T12:00:00+00:00", None),
        ("11月9日24:00前提交", "2026-11-09T16:00:00+00:00", None),
        ("下周一课前交", None, "2026-11-02"),
        ("下周五放学前提交", None, "2026-11-06"),
        ("周一中午前提交", None, "2026-11-02"),
        ("11月9号交给班长", None, "2026-11-09"),
        ("2026年11月9日周一晚上8点前提交", "2026-11-09T12:00:00+00:00", None),
        ("2026年11月9日周二晚上8点前提交", None, None),
        ("11月9日下午三点到五点之间提交", None, None),
        ("11月9日午夜前提交", None, "2026-11-09"),
    ],
)
@pytest.mark.parametrize("scoped", [False, True])
def test_deadline_formats_and_windows(phrase, clock, day, scoped):
    text = "软件工程：实验报告，" + phrase + "。"
    result = extract_rules(text, ANCHOR, "Asia/Shanghai", scoped_deadline=scoped)
    assert (result.fields["due_at"], result.fields["due_date"]) == (clock, day)


def test_explicit_revision_to_new_deadline_is_a_change():
    text = "实验报告原定11月8日20:00截止，现改至11月9日22:00。"
    result = extract_rules(text, ANCHOR, "Asia/Shanghai", scoped_deadline=True)
    assert result.action == "review"
    assert result.fields["due_at"] == "2026-11-09T14:00:00+00:00"
    assert result.fields["title"] == "实验报告"


@pytest.mark.parametrize(
    "text",
    [
        "大家都提交作业了吗？",
        "老师，实验报告是明天18:00前提交吗？",
        "软件工程：本周没有作业。",
        "作业已提交成功，系统回执仅供留存。",
    ],
)
def test_question_and_status_messages_do_not_create_tasks(text):
    result = extract_rules(text, ANCHOR, "Asia/Shanghai")
    assert result.action == "ignore"
    assert not result.fields["due_at"] and not result.fields["due_date"]


def test_negated_deadline_does_not_fill_review_draft():
    text = "不是今天19:00前提交实验报告，具体时间另行通知。"
    result = extract_rules(text, ANCHOR, "Asia/Shanghai")
    assert result.action == "review"
    assert result.fields["due_at"] is None
    merge_model(result, {"due_text": "不是今天19:00"}, text, ANCHOR, "Asia/Shanghai")
    assert result.fields["due_at"] is None


@pytest.mark.parametrize("scoped", [False, True])
def test_before_class_submission_has_date_without_assumed_clock(scoped):
    result = extract_rules(
        "周一上课前提交实验报告。", ANCHOR, "Asia/Shanghai", scoped_deadline=scoped
    )
    assert result.fields["due_date"] == "2026-11-02"
    assert result.fields["due_at"] is None


def test_real_deadline_is_preserved_when_followed_by_a_question():
    text = "通知：明天18:00前提交实验报告，请问后天还要提交作业吗？"
    result = extract_rules(text, ANCHOR, "Asia/Shanghai")
    assert result.action == "create"
    assert result.fields["due_at"] == "2026-10-30T10:00:00+00:00"


@pytest.mark.parametrize(
    "text",
    [
        "数据库原理：作业成绩已公布，请自行查看。",
        "实验报告提交成功，请注意查收。",
        "第一次作业已批阅，可在学习通查看。",
        "期末考试已结束。",
    ],
)
def test_status_only_notice_is_not_a_task(text):
    result = extract_rules(text, ANCHOR, "Asia/Shanghai")
    assert result.action == "ignore"
    assert not result.needs_llm


@pytest.mark.parametrize(
    "text",
    [
        "作业成绩已公布，请在明天18:00前提交成绩复核申请。",
        "实验报告提交成功，请在下周一重新上传源代码。",
        "考试已结束，请于明天提交实验报告。",
        "数据库原理：下周一9:00进行期中考试。",
        "作业已退回，请修改后重新提交。",
        "请确认实验报告已提交。",
    ],
)
def test_status_notice_with_an_action_remains_reviewable(text):
    assert extract_rules(text, ANCHOR, "Asia/Shanghai").action == "create"


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


def test_scoped_deadlines_keep_explicit_cutoff_and_reject_model_guesses():
    timezone = "Asia/Shanghai"
    schedule = "实验时间：2026年11月2日18:00。请提交实验报告。"
    explicit = extract_rules(
        schedule + "截止时间：2026年11月3日18:00。",
        ANCHOR,
        timezone,
        scoped_deadline=True,
    )
    assert explicit.fields["due_at"] == "2026-11-03T10:00:00+00:00"
    unknown = extract_rules(schedule, ANCHOR, timezone, scoped_deadline=True)
    result = merge_model(
        unknown,
        {"due_text": "2026年11月2日18:00"},
        schedule,
        ANCHOR,
        timezone,
        scoped_deadline=True,
    )
    assert result.fields["due_at"] is None
    assert result.fields["due_date"] is None


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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("软件工程：请于2026年11月2日前提交实验报告。", "2026-11-02"),
        ("软件工程：提交实验报告，截止日期为2026-11-02。", "2026-11-02"),
        ("软件工程：周五截止，请提交实验报告。", "2026-10-30"),
        ("软件工程：明天提交实验报告。", "2026-10-30"),
    ],
)
def test_explicit_date_only_deadlines_are_preserved_without_a_time(text, expected):
    result = extract_rules(text, ANCHOR, "Asia/Shanghai")
    assert result.fields["due_date"] == expected
    assert result.fields["due_at"] is None
    assert result.evidence["due_date"]["quote"] in text
    assert any("时刻" in warning for warning in result.warnings)


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize(
    "text",
    [
        "11 月 9 日在学习通提交第一次作业",
        "11月9日在学习通提交第一次作业",
        "2026 年 11 月 9 日通过学习通上传第一次作业",
        "十一 月 九 日在学习通提交第一次作业",
    ],
)
def test_submission_dates_allow_spaces_and_platform_before_the_action(text, scoped):
    result = extract_rules(text, ANCHOR, "Asia/Shanghai", scoped_deadline=scoped)
    assert result.fields["due_date"] == "2026-11-09"
    assert result.fields["due_at"] is None
    assert result.evidence["due_date"]["quote"] in text


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize(
    "text",
    [
        "11 月 9 日在学习通发布作业，截止时间待定。",
        "11 月 9 日开始在学习通提交第一次作业。",
        "11 月 9 日在学习通不用提交第一次作业。",
    ],
)
def test_spaced_publication_start_and_negated_submission_dates_are_not_deadlines(text, scoped):
    result = extract_rules(text, ANCHOR, "Asia/Shanghai", scoped_deadline=scoped)
    assert not result.fields.get("due_date")
    assert not result.fields.get("due_at")


@pytest.mark.parametrize(
    "text",
    [
        "软件工程：11月2日发布作业，11月3日上课。",
        "软件工程：11月2日前或11月3日前提交实验报告。",
        "软件工程：10月31日25:90截止，请提交报告。",
        "软件工程：11月2日18:00或11月3日前提交实验报告。",
    ],
)
def test_ambiguous_or_invalid_dates_do_not_become_date_only_deadlines(text):
    result = extract_rules(text, ANCHOR, "Asia/Shanghai")
    assert not result.fields.get("due_date")
    assert not result.fields.get("due_at")


def test_model_does_not_replace_rule_date_only_with_guessed_time():
    text = "软件工程：2026年11月2日前提交报告。"
    result = merge_model(
        extract_rules(text, ANCHOR, "Asia/Shanghai"),
        {"due_text": "2026年11月2日18:00"},
        text,
        ANCHOR,
        "Asia/Shanghai",
    )
    assert result.fields["due_date"] == "2026-11-02"
    assert result.fields["due_at"] is None


def test_model_cannot_choose_one_of_two_competing_deadlines():
    text = "软件工程：11月2日18:00或11月3日前提交实验报告。"
    result = merge_model(
        extract_rules(text, ANCHOR, "Asia/Shanghai"),
        {"due_text": "11月2日18:00"},
        text,
        ANCHOR,
        "Asia/Shanghai",
    )
    assert result.fields["due_at"] is None
    assert result.fields["due_date"] is None


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


@pytest.mark.parametrize("label", ["结束时间", "作业结束时间", "任务结束时间", "考试结束时间"])
def test_learning_platform_explicit_end_time_is_a_deadline(label):
    text = f"软件工程作业：请提交需求分析报告。\n{label}：2026-11-10 18:26"
    result = extract_rules(text, ANCHOR, "Asia/Shanghai", scoped_deadline=True)
    assert result.fields["due_at"] == "2026-11-10T10:26:00+00:00"
    assert result.fields["due_date"] is None
    assert result.evidence["due_at"]["method"] == "rule"
    assert result.evidence["due_at"]["quote"] in text


def test_learning_start_and_end_on_same_line_choose_only_end():
    text = "作业：请提交实验报告。开始时间：2026-11-01 08:00 结束时间：2026-11-10 18:26"
    result = extract_rules(text, ANCHOR, "Asia/Shanghai", scoped_deadline=True)
    assert result.fields["due_at"] == "2026-11-10T10:26:00+00:00"


@pytest.mark.parametrize(
    "text",
    [
        "请提交实验报告。实验结束时间：2026-11-10 18:26",
        "活动通知：请提交报名表。结束时间：2026-11-10 18:26",
        "会议通知：请提交会议报告。会议结束时间：2026-11-10 18:26",
        "请提交实验报告。实验时间：2026-11-10 08:00\n结束时间：2026-11-10 18:26",
        "作业通知。不是结束时间：2026-11-10 18:26",
    ],
)
def test_activity_end_fields_do_not_become_learning_deadlines(text):
    result = extract_rules(text, ANCHOR, "Asia/Shanghai", scoped_deadline=True)
    assert result.fields["due_at"] is None
    assert result.fields["due_date"] is None


def test_learning_end_date_does_not_invent_a_clock():
    result = extract_rules(
        "请提交作业。结束日期：2026-11-10", ANCHOR, "Asia/Shanghai", scoped_deadline=True
    )
    assert result.fields["due_at"] is None
    assert result.fields["due_date"] == "2026-11-10"


def test_conflicting_learning_end_fields_require_review():
    result = extract_rules(
        "请提交作业。结束时间：2026-11-10 18:26\n截止时间：2026-11-11 20:00",
        ANCHOR,
        "Asia/Shanghai",
        scoped_deadline=True,
    )
    assert result.fields["due_at"] is None
    assert any("多个可能" in warning for warning in result.warnings)


def test_learning_end_time_after_course_and_publication_metadata():
    text = (
        "课程名称：软件工程 作业名称：需求分析报告 "
        "发布时间：2026-11-01 08:00 结束时间：2026-11-10 18:26"
    )
    result = extract_rules(text, ANCHOR, "Asia/Shanghai", scoped_deadline=True)
    assert result.fields["due_at"] == "2026-11-10T10:26:00+00:00"
