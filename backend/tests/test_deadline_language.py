"""Natural notification dates must not mix class time and submission deadlines."""

from datetime import UTC, datetime

import pytest

from campus_inbox.extraction import extract_rules

ANCHOR = datetime(2026, 11, 4, 3, 19, tzinfo=UTC)
NOTICE = (
    "同学们好，2026-2027秋季学期-高级软件产品经理-理论与实践-期末大作业提交入口"
    "已经在日新学堂建好，大家在11月8日晚之前提交期末大作业。"
    "明天11月5日上课时，每组准备20分钟ppt进行期末大作业汇报。"
)


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("period", ["晚", "晚上", "下午", "中午", "早上"])
def test_vague_period_keeps_submission_date_and_evidence(period, scoped):
    text = NOTICE.replace("8日晚", "8日" + period)
    r = extract_rules(text, ANCHOR, "Asia/Shanghai", scoped_deadline=scoped)
    assert r.fields["due_date"] == "2026-11-08"
    assert r.fields["due_at"] is None
    assert r.evidence["due_date"]["quote"] == "11月8日" + period
    assert any("具体时刻" in w for w in r.warnings)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("11月8日晚8点前提交期末大作业。11月5日上课。", "2026-11-08T12:00:00+00:00"),
        ("11月8日晚上20:30前提交期末大作业。", "2026-11-08T12:30:00+00:00"),
        ("下星期三18:00前提交报告。", "2026-11-11T10:00:00+00:00"),
        ("本星期五18:00前提交报告。", "2026-11-06T10:00:00+00:00"),
    ],
)
def test_explicit_clock_and_weekday_aliases(text, expected):
    r = extract_rules(text, ANCHOR, "Asia/Shanghai")
    assert r.fields["due_at"] == expected
    assert r.fields["due_date"] is None


@pytest.mark.parametrize(
    "text",
    [
        "11月8日晚上发布作业，11月9日上午上课。",
        "不是11月8日晚之前提交作业，截止时间另行通知。",
        "11月8日晚之前提交报告，11月9日晚之前提交代码。",
        "11月8日晚上25:90前提交报告。",
        "11月8日晚之前或11月9日18:00前提交报告。",
    ],
)
def test_non_deadline_negated_conflicting_and_invalid_dates_stay_empty(text):
    r = extract_rules(text, ANCHOR, "Asia/Shanghai")
    assert r.fields["due_at"] is None
    assert r.fields["due_date"] is None


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize(
    ("text", "key", "expected"),
    [
        (
            "原定于今天提交的软件工程作业，现统一延期至后天23:59前提交。",
            "due_at",
            "2026-11-06T15:59:00+00:00",
        ),
        ("作业原定今天18:00前提交，现延期至后天。", "due_date", "2026-11-06"),
        ("作业原定11月5日18:00前提交，现延期至11月8日晚之前。", "due_date", "2026-11-08"),
        (
            "作业原定今天18:00前提交，现延期至后天23:59前提交。",
            "due_at",
            "2026-11-06T15:59:00+00:00",
        ),
        (
            "作业延期，原定今天18:00前提交，新的截止时间为后天23:59。",
            "due_at",
            "2026-11-06T15:59:00+00:00",
        ),
    ],
)
def test_revised_deadline_supersedes_old_date_and_precision(text, key, expected, scoped):
    r = extract_rules(text, ANCHOR, "Asia/Shanghai", scoped_deadline=scoped)
    assert r.action == "review"
    assert r.fields[key] == expected
    assert r.fields["due_date" if key == "due_at" else "due_at"] is None
    assert not any("多个可能" in w for w in r.warnings)
    assert "今天" not in r.evidence[key]["quote"]


@pytest.mark.parametrize(
    "text",
    [
        "作业原定今天提交，延期至明天或后天23:59。",
        "作业原定今天提交，延期至11月31日18:00。",
        "作业原定今天提交，延期至后天25:90。",
    ],
)
def test_revision_still_rejects_conflicting_or_invalid_new_dates(text):
    r = extract_rules(text, ANCHOR, "Asia/Shanghai")
    assert r.fields["due_at"] is None and r.fields["due_date"] is None
