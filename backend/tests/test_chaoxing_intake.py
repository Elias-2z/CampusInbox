"""Connector intake uses isolated databases and synthetic messages only."""

import base64
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from campus_inbox import reminders, services
from campus_inbox.attachments import original_path
from campus_inbox.connectors.chaoxing import ChaoxingEvent, ChaoxingIntake, historical_deadline
from campus_inbox.database import initialize, make_engine
from campus_inbox.models import Attachment, Message, Suggestion, Task
from campus_inbox.providers import OllamaProvider
from campus_inbox.schemas import AcceptSuggestion, TaskFields


@pytest.fixture
def intake(tmp_path):
    engine = make_engine(tmp_path / "campus.db")
    initialize(engine)
    yield ChaoxingIntake(engine, OllamaProvider)
    engine.dispose()


def notice(**overrides):
    job = {
        "kind": "course_notice",
        "message_id": "notice_example",
        "text": "软件工程：请于2026年12月8日18:00前提交实验报告。",
        "title": "课程通知",
        "course": "软件工程",
        "conversation": "软件工程一班",
        "sender": "教师",
        "captured_at": "2026-11-03T09:00:00+08:00",
        "direction": "received",
    }
    job.update(overrides)
    return job


def test_known_time_enters_main_inbox_once_without_creating_formal_task(intake):
    event_id, created = intake.receive("local-account", notice())
    assert created
    assert intake.receive("local-account", notice()) == (event_id, False)
    assert intake.process(event_id) == "done"
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        assert len(session.exec(select(Message)).all()) == 1
        suggestions = session.exec(select(Suggestion)).all()
        assert len(suggestions) == 1
        assert session.exec(select(Task)).all() == []
        event = session.get(ChaoxingEvent, event_id)
        assert event.payload["course"] == "软件工程"
        assert event.payload["conversation"] == "软件工程一班"
        assert event.suggestion_id is not None
        display = services.inbox_item(session, suggestions[0])
        assert display.message.source_context["course"] == "软件工程"
        assert display.message.source_context["group_name"] == "软件工程一班"
        assert suggestions[0].fields["course"] == "软件工程"


def test_verified_empty_notice_is_archived_once_with_original_title(intake):
    job = notice(text="")
    event_id, _ = intake.receive("local-account", job)
    assert intake.process(event_id) == "needs_review"
    job["empty_body_ready"] = True
    assert intake.receive("local-account", job) == (event_id, False)
    assert intake.process(event_id) == "done"
    assert intake.receive("local-account", job) == (event_id, False)
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        suggestion = session.exec(select(Suggestion)).one()
        assert suggestion.status == "archived"
        assert suggestion.action == "ignore"
        assert suggestion.fields["title"] == job["title"]
        assert session.exec(select(Message)).one().text == ""
        assert session.exec(select(Task)).all() == []


@pytest.mark.parametrize(
    ("overrides", "state"),
    [
        ({"empty_body_ready": False}, "needs_review"),
        ({"media_pending_count": 1}, "needs_review"),
        ({"attachments": [{"name": "说明.pdf"}]}, "needs_review"),
        ({"title": ""}, "needs_review"),
        ({"captured_at": None}, "needs_time"),
    ],
)
def test_empty_notice_requires_verified_body_title_time_and_no_media(intake, overrides, state):
    job = notice(**({"text": "", "empty_body_ready": True} | overrides))
    event_id, _ = intake.receive("local-account", job)
    assert intake.process(event_id) == state
    with Session(intake.engine) as session:
        assert session.exec(select(Message)).all() == []


def test_incomplete_platform_attachments_are_not_used_to_infer_task(intake):
    job = notice(
        message_id="partial-attachment",
        text="请阅读附件说明。",
        media_pending_count=1,
        attachments=[
            {
                "name": "要求.txt",
                "content_base64": base64.b64encode("请提交实验报告。".encode()).decode(),
            }
        ],
    )
    event_id, _ = intake.receive("local-account", job)
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        suggestion = session.exec(select(Suggestion)).one()
        refreshed = services.retry(session, suggestion, OllamaProvider)
        assert refreshed.action == "ignore"
        assert not refreshed.fields.get("title")
        assert any("附件" in warning for warning in refreshed.warnings)
        assert session.exec(select(Task)).all() == []


def test_complete_collected_attachment_can_be_reanalyzed_in_main_inbox(intake):
    job = notice(
        message_id="complete-attachment",
        text="请阅读附件说明。",
        attachments=[
            {
                "name": "要求.txt",
                "content_base64": base64.b64encode("请提交实验报告。".encode()).decode(),
            }
        ],
    )
    event_id, _ = intake.receive("local-account", job)
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        suggestion = session.exec(select(Suggestion)).one()
        refreshed = services.retry(session, suggestion, OllamaProvider)
        assert refreshed.action == "create"
        assert refreshed.fields["title"] == "实验报告"
        assert refreshed.evidence["title"]["method"] == "attachment"
        assert session.exec(select(Task)).all() == []


@pytest.mark.parametrize(
    ("extra_text", "parse_warning", "expected_action"),
    [
        ("设备清单：示波器、万用表。", "", "create"),
        ("请提交实验报告。", "", "create"),
        ("请提交课堂练习。", "", "ignore"),
        ("请于2026年12月8日前上传课堂练习。", "", "ignore"),
        ("设备清单：示波器、万用表。", "仅识别了部分页面", "ignore"),
        (None, "", "ignore"),
    ],
)
def test_multiple_attachments_require_one_clear_task_and_all_originals(
    intake, extra_text, parse_warning, expected_action
):
    instructions = {
        "name": "要求.txt",
        "content_base64": base64.b64encode("请提交实验报告。".encode()).decode(),
    }
    extra = {"name": "补充.txt"}
    if extra_text:
        extra["content_base64"] = base64.b64encode(extra_text.encode()).decode()
    event_id, _ = intake.receive(
        "local-account", notice(text="请阅读附件说明。", attachments=[instructions, extra])
    )
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        if parse_warning:
            attachment = session.exec(
                select(Attachment).where(Attachment.filename == "补充.txt")
            ).one()
            attachment.error = parse_warning
            session.add(attachment)
            session.commit()
        suggestion = session.exec(select(Suggestion)).one()
        refreshed = services.retry(session, suggestion, OllamaProvider)
        assert refreshed.action == expected_action
        if expected_action == "create":
            assert refreshed.fields["title"] == "实验报告"
            assert "要求.txt" in refreshed.evidence["title"]["quote"]
        else:
            assert not refreshed.fields.get("title")
        assert session.exec(select(Task)).all() == []


def test_notice_with_only_attachment_enters_review_and_preserves_empty_body(intake):
    attachment = {"name": "要求.txt", "url": "https://d0.cldisk.com/download/example"}
    job = notice(text="", attachments=[attachment])
    event_id, _ = intake.receive("local-account", job)
    assert intake.process(event_id) == "needs_review"
    attachment["content_base64"] = base64.b64encode("请提交实验报告。".encode()).decode()
    assert intake.receive("local-account", job) == (event_id, False)
    with Session(intake.engine) as session:
        assert session.get(ChaoxingEvent, event_id).state == "pending"
    assert intake.process(event_id) == "done"
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        suggestion = session.exec(select(Suggestion)).one()
        assert suggestion.status == "pending"
        assert session.exec(select(Message)).one().text == ""
        assert session.exec(select(Attachment)).one().status == "ready"
        refreshed = services.retry(session, suggestion, OllamaProvider)
        assert refreshed.fields["title"] == "实验报告"
        assert refreshed.evidence["title"]["method"] == "attachment"
        assert session.exec(select(Task)).all() == []


def test_task_center_scan_time_never_becomes_publication_time(intake):
    job = notice(
        kind="task_center",
        message_id="task_1",
        captured_at="2026-11-03T10:00:00+08:00",
        published_at=None,
        text="实验报告明天提交",
    )
    event_id, _ = intake.receive("local-account", job)
    assert intake.process(event_id) == "needs_time"
    with Session(intake.engine) as session:
        assert session.exec(select(Message)).all() == []
        assert session.get(ChaoxingEvent, event_id).payload["text"] == "实验报告明天提交"


def test_task_center_title_is_available_even_if_description_has_no_task_keywords(intake):
    event_id, _ = intake.receive(
        "local-account",
        notice(
            kind="task_center",
            message_id="title-only",
            title="课后练习",
            text="请查看附件说明。",
            published_at="2026-11-03T09:00:00+08:00",
            field_evidence={"title": "课后练习"},
            task_type="assignment",
        ),
    )
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        suggestion = session.exec(select(Suggestion)).one()
        assert suggestion.action == "create"
        assert suggestion.fields["title"] == "课后练习"
        assert suggestion.fields["kind"] == "homework"
        assert suggestion.evidence["title"]["method"] == "connector"
        assert session.exec(select(Task)).all() == []


def test_platform_title_does_not_turn_a_change_notice_into_a_new_task(intake):
    event_id, _ = intake.receive(
        "local-account",
        notice(
            kind="task_center",
            message_id="change",
            title="实验报告",
            text="实验报告延期至2026年12月1日。",
            published_at="2026-11-03T09:00:00+08:00",
            field_evidence={"title": "实验报告"},
            task_type="assignment",
        ),
    )
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        suggestion = session.exec(select(Suggestion)).one()
        assert suggestion.action == "review"
        assert session.exec(select(Task)).all() == []


@pytest.mark.parametrize(
    ("deadline", "due_date", "due_at"),
    [
        ("2099-11-08", "2099-11-08", None),
        ("2099-11-08T20:00:00+08:00", None, "2099-11-08T12:00:00+00:00"),
    ],
)
def test_task_center_structured_deadline_is_kept_with_its_original_precision(
    intake, deadline, due_date, due_at
):
    event_id, _ = intake.receive(
        "local-account",
        notice(
            kind="task_center",
            message_id="future",
            text="软件工程：请提交实验报告。",
            published_at="2026-11-03T09:00:00+08:00",
            deadline=deadline,
            field_evidence={"deadline": "学习通截止时间：" + deadline},
        ),
    )
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        suggestion = session.exec(select(Suggestion)).one()
        assert suggestion.fields["due_date"] == due_date
        assert suggestion.fields["due_at"] == due_at
        assert suggestion.evidence["due_date" if due_date else "due_at"]["method"] == "connector"


def test_task_center_conflicting_text_and_platform_deadlines_require_manual_review(intake):
    event_id, _ = intake.receive(
        "local-account",
        notice(
            kind="task_center",
            message_id="conflict",
            text="软件工程：2099年11月9日前提交报告。",
            published_at="2026-11-03T09:00:00+08:00",
            deadline="2000-11-08",
            field_evidence={"deadline": "学习通截止日期：2000-11-08"},
        ),
    )
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        suggestion = session.exec(select(Suggestion)).one()
        assert not suggestion.fields["due_date"] and not suggestion.fields["due_at"]
        assert any("不一致" in warning for warning in suggestion.warnings)
        assert suggestion.status == "pending"


@pytest.mark.parametrize("deadline", ["2099-99-99", "2099-11-08T20:00:00", "not-a-date"])
def test_invalid_or_unzoned_platform_deadlines_are_not_invented(intake, deadline):
    event_id, _ = intake.receive(
        "local-account",
        notice(
            kind="task_center",
            message_id="invalid",
            text="软件工程：请提交实验报告。",
            published_at="2026-11-03T09:00:00+08:00",
            deadline=deadline,
            field_evidence={"deadline": "学习通原文：" + deadline},
        ),
    )
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        suggestion = session.exec(select(Suggestion)).one()
        assert not suggestion.fields["due_date"] and not suggestion.fields["due_at"]
        assert suggestion.status == "pending"


def test_user_can_supply_actual_time_and_retry_after_restart(intake):
    event_id, _ = intake.receive(
        "local-account", notice(captured_at=None, text="软件工程明天提交报告")
    )
    assert intake.process(event_id) == "needs_time"
    reopened = ChaoxingIntake(intake.engine, OllamaProvider)
    assert reopened.process(event_id, sent_at=datetime(2026, 11, 3, 1, tzinfo=UTC)) == "done"
    with Session(intake.engine) as session:
        assert len(session.exec(select(Message)).all()) == 1
        assert len(session.exec(select(Suggestion)).all()) == 1


def test_content_updates_are_distinct_and_task_scan_timestamps_do_not_duplicate(intake):
    first = notice(kind="task_center", message_id="task_1", published_at=None)
    event_id, _ = intake.receive("local-account", first)
    assert intake.receive(
        "local-account", dict(first, captured_at="2026-11-03T11:00:00+08:00")
    ) == (event_id, False)
    updated_id, created = intake.receive("local-account", dict(first, text="实验报告延期一周"))
    assert created and updated_id != event_id


def test_task_center_countdown_is_not_a_new_content_version(intake):
    job = notice(
        kind="task_center",
        message_id="version-one",
        url="https://mooc1.chaoxing.com/mooc-ans/work/studentstudy?courseId=1&classId=2&workId=3",
        text="提交实验报告；剩余2小时30分钟",
    )
    event_id, created = intake.receive("account", job)
    assert created
    repeated = dict(job, message_id="version-two", text="提交实验报告；还剩1小时")
    assert intake.receive("account", repeated) == (event_id, False)
    assert intake.receive("account", dict(repeated, text="提交新版实验报告"))[1]
    assert intake.receive("another-account", repeated)[1]


@pytest.mark.parametrize(
    "text",
    [
        "作业即将截止提醒\n课程名称：软件工程\n作业名称：实验报告\n你的作业还剩2小时即将截止",
        "考试结束提醒\n课程名称：软件工程\n考试名称：期末考试\n本次考试已收卷",
    ],
)
def test_platform_reminder_archive_keeps_new_instructions_and_media_in_review(intake, text):
    event, _ = intake.receive("account", notice(text=text))
    assert intake.process(event) == "done"
    updated, _ = intake.receive("account", notice(text=text + "\n另请提交源代码。"))
    assert intake.process(updated) == "done"
    media, _ = intake.receive("account", notice(text=text, media_pending_count=1))
    assert intake.process(media) == "done"
    with Session(intake.engine) as session:
        assert sorted(item.status for item in session.exec(select(Suggestion))) == [
            "archived",
            "pending",
            "pending",
        ]


def test_sent_messages_are_not_ingested(intake):
    with pytest.raises(ValueError):
        intake.receive("local-account", notice(direction="sent"))


def test_verified_checkins_with_distinct_start_times_can_both_be_confirmed(intake):
    body = AcceptSuggestion(title="位置签到")
    for day in (2, 3):
        start = f"2099-01-0{day}T09:00:00+08:00"
        event_id, _ = intake.receive(
            "local-account",
            notice(
                kind="checkin_activity",
                message_id=f"checkin-{day}",
                text="位置签到",
                title="位置签到",
                published_at=start,
                deadline=f"2099-01-0{day}T09:30:00+08:00",
                activity_id="123",
                course_id="456",
                class_id="789",
                field_evidence={"deadline": "学习通签到活动结束时间"},
            ),
        )
        assert intake.process(event_id) == "done"
        with Session(intake.engine) as session:
            event = session.get(ChaoxingEvent, event_id)
            suggestion = session.get(Suggestion, event.suggestion_id)
            assert suggestion.status == "pending"
            assert services.inbox_item(session, suggestion).matched_tasks == []
            task = services.accept(session, suggestion, body)
            assert task.kind == "event"
            assert task.due_at.replace(tzinfo=UTC).isoformat() == f"2099-01-0{day}T01:30:00+00:00"


def test_new_ended_checkin_record_blocks_an_older_draft(intake):
    active = notice(
        kind="checkin_activity",
        message_id="checkin-active",
        text="位置签到",
        title="位置签到",
        published_at="2099-01-02T09:00:00+08:00",
        deadline="2099-01-02T09:30:00+08:00",
        activity_id="123",
        course_id="456",
        class_id="789",
        field_evidence={"deadline": "学习通签到活动结束时间"},
    )
    event_id, _ = intake.receive("local-account", active)
    assert intake.process(event_id) == "done"
    ended_id, _ = intake.receive(
        "local-account",
        {
            **active,
            "message_id": "checkin-ended",
            "activity_state": "ended",
            "field_evidence": {"activity_state": "已结束"},
            "deadline": None,
        },
    )
    assert intake.process(ended_id) == "done"
    with Session(intake.engine) as session:
        ended = session.get(ChaoxingEvent, ended_id)
        assert session.get(Suggestion, ended.suggestion_id).status == "archived"
        event = session.get(ChaoxingEvent, event_id)
        suggestion = session.get(Suggestion, event.suggestion_id)
        with pytest.raises(HTTPException, match="已结束"):
            services.accept(session, suggestion, AcceptSuggestion(title="位置签到"))
        assert session.exec(select(Task)).all() == []


def test_ended_checkin_stops_reminders_without_changing_local_progress(intake):
    active = notice(
        kind="checkin_activity",
        message_id="checkin-active",
        text="位置签到",
        title="位置签到",
        published_at="2099-01-02T09:00:00+08:00",
        deadline="2099-01-02T09:30:00+08:00",
        activity_id="123",
        course_id="456",
        class_id="789",
        field_evidence={"deadline": "学习通签到活动结束时间"},
    )
    event_id, _ = intake.receive("local-account", active)
    assert intake.process(event_id) == "done"
    current = datetime(2099, 1, 2, 1, 20, tzinfo=UTC)
    with Session(intake.engine) as session:
        event = session.get(ChaoxingEvent, event_id)
        suggestion = session.get(Suggestion, event.suggestion_id)
        task = services.accept(session, suggestion, AcceptSuggestion(title="位置签到"))
        task_id = task.id
        task.status = "in_progress"
        session.add(task)
        session.commit()
        assert [item.task.id for item in reminders.feed(session, current).items] == [task_id]
        assert not services.detail(session, task).checkin_closed
        assert not services.list_tasks(session, current)[0].checkin_closed
        assert reminders.feed(session, datetime(2099, 1, 2, 1, 30, tzinfo=UTC)).items == []
    ended = {
        **active,
        "message_id": "checkin-ended",
        "activity_state": "ended",
        "field_evidence": {"activity_state": "已结束"},
        "deadline": None,
    }
    intake.receive("another-account", ended)
    intake.receive("local-account", {**ended, "published_at": "2099-01-03T09:00:00+08:00"})
    with Session(intake.engine) as session:
        assert [item.task.id for item in reminders.feed(session, current).items] == [task_id]
    intake.receive("local-account", ended)
    with Session(intake.engine) as session:
        assert reminders.feed(session, current).items == []
        detail = services.detail(session, session.get(Task, task_id))
        assert detail.checkin_closed
        assert detail.task.status == "in_progress"
        [listed] = services.list_tasks(session, current)
        assert listed.checkin_closed and listed.status == "in_progress"


def test_signin_and_already_submitted_items_are_held_for_review(intake):
    signin, _ = intake.receive("local-account", notice(kind="checkin_activity", message_id="sign1"))
    submitted, _ = intake.receive(
        "local-account",
        notice(
            kind="task_center",
            message_id="work1",
            published_at="2026-11-03T09:00:00+08:00",
            platform_status="待批阅",
        ),
    )
    assert intake.process(signin) == "needs_review"
    assert intake.process(submitted) == "needs_review"
    with Session(intake.engine) as session:
        assert session.exec(select(Suggestion)).all() == []


@pytest.mark.parametrize("status", ["已交卷", "已阅卷", "已过教师设置的截止时间"])
def test_closed_exam_status_preserves_history_without_new_task(intake, status):
    event_id, _ = intake.receive(
        "local-account",
        notice(
            kind="task_center",
            title="期末考试",
            text="软件工程期末考试",
            url="https://mooc1.chaoxing.com/exam/detail?courseId=1&classId=2&examId=3",
            published_at="2026-11-03T09:00:00+08:00",
            platform_status=status,
        ),
    )
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        suggestion = session.exec(select(Suggestion)).one()
        assert suggestion.status == "archived"
        assert status in suggestion.warnings[0]
        assert session.exec(select(Task)).all() == []


def test_media_coverage_update_preserves_existing_notice_and_original(intake):
    job = notice(
        text="请阅读附件说明。",
        media_pending_count=1,
        attachments=[{"name": "要求.txt", "url": "https://d0.cldisk.com/download/example"}],
    )
    event_id, _ = intake.receive("account", job)
    assert intake.process(event_id) == "done"
    complete = {
        **job,
        "media_pending_count": 0,
        "attachments": [
            {
                **job["attachments"][0],
                "content_base64": base64.b64encode("请提交实验报告。".encode()).decode(),
            }
        ],
    }
    assert intake.receive("account", complete) == (event_id, False)
    assert intake.receive("account", job) == (event_id, False)
    with Session(intake.engine) as session:
        event = session.exec(select(ChaoxingEvent)).one()
        assert event.payload["media_pending_count"] == 0
        suggestion = session.get(Suggestion, event.suggestion_id)
        refreshed = services.retry(session, suggestion, OllamaProvider)
        assert refreshed.fields["title"] == "实验报告"
        assert session.exec(select(Task)).all() == []
    revised = {
        **complete,
        "attachments": [
            {
                **complete["attachments"][0],
                "content_base64": base64.b64encode("请提交课堂练习。".encode()).decode(),
            }
        ],
    }
    revision_id, created = intake.receive("account", revised)
    assert created and revision_id != event_id
    assert intake.receive("account", complete) == (event_id, False)
    assert intake.receive("account", revised) == (revision_id, False)


def test_late_original_attaches_to_existing_notice_and_changed_bytes_are_distinct(intake):
    file = {"name": "要求.txt", "url": "https://d0.cldisk.com/download/" + "a" * 32}
    event, _ = intake.receive("account", notice(attachments=[{**file, "status": "listed"}]))
    assert intake.process(event) == "done"
    received = notice(attachments=[{**file, "content_base64": "YWJj"}])
    assert intake.receive("account", received) == (event, False)
    with Session(intake.engine) as session:
        attachment = session.exec(select(Attachment)).one()
        assert attachment.text == "abc"
        assert len(session.exec(select(Message)).all()) == 1
    changed = notice(attachments=[{**file, "content_base64": "ZGVm"}])
    version, created = intake.receive("account", changed)
    assert created and version != event
    assert intake.receive("account", changed) == (version, False)


@pytest.mark.parametrize(
    ("text", "schedule"),
    [
        ("实验报告发布时间：2000年11月1日09:00，请提交实验报告。", []),
        (
            "实验时间：2000年11月1日09:00，提交实验报告的时间另行通知。",
            ["实验时间：2000年11月1日09:00"],
        ),
        ("实验报告发布时间：2000年11月1日。请提交实验报告。", []),
        ("如果明天进行实验，请提交实验报告。", []),
    ],
)
def test_notice_schedule_or_publication_time_is_not_a_deadline(intake, text, schedule):
    event_id, _ = intake.receive("account", notice(text=text))
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        suggestion = session.exec(select(Suggestion)).one()
        assert suggestion.status == "pending"
        assert suggestion.fields["due_at"] is None
        assert suggestion.fields["due_date"] is None
        assert services.inbox_item(session, suggestion).activity_schedule == schedule


def test_notice_with_explicit_historical_deadline_is_archived(intake):
    event, _ = intake.receive(
        "account",
        notice(
            text="软件工程：请于2025年12月8日18:00前提交实验报告。",
            captured_at="2025-12-03T09:00:00+08:00",
        ),
    )
    assert intake.process(event) == "done"
    with Session(intake.engine) as session:
        assert session.exec(select(Suggestion)).one().status == "archived"
        assert session.exec(select(Task)).all() == []


def test_expired_learning_platform_task_is_archived_without_creating_task(intake):
    event_id, _ = intake.receive(
        "local-account",
        notice(
            kind="task_center",
            message_id="old-work",
            published_at="2000-01-01T09:00:00+08:00",
            deadline="2000-01-10",
            field_evidence={"deadline": "截止日期 2000-01-10"},
            text="软件工程：提交实验报告。",
        ),
    )
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        suggestion = session.exec(select(Suggestion)).one()
        assert suggestion.status == "archived"
        assert any("截止" in item for item in suggestion.warnings)
        assert session.exec(select(Task)).all() == []


def test_historical_deadline_requires_clear_platform_evidence_and_preserves_today():
    current = datetime(2026, 11, 3, 1, tzinfo=UTC)
    base = {"kind": "task_center", "field_evidence": {"deadline": "截止日期"}}
    assert historical_deadline({**base, "deadline": "2026-11-02"}, current)
    assert not historical_deadline({**base, "deadline": "2026-11-03"}, current)
    assert not historical_deadline({**base, "deadline": "2026-11-03T09:00:00"}, current)
    assert not historical_deadline(
        {**base, "deadline": "2000-01-01", "field_evidence": {}}, current
    )
    assert not historical_deadline({**base, "deadline": "2000-01-01", "kind": "chat"}, current)


def test_historical_notice_matching_existing_task_remains_reviewable(intake):
    with Session(intake.engine) as session:
        services.manual_create(session, TaskFields(title="实验报告", course="软件工程"))
    event_id, _ = intake.receive(
        "local-account",
        notice(
            kind="task_center",
            message_id="old-match",
            title="实验报告",
            published_at="2000-01-01T09:00:00+08:00",
            deadline="2000-01-10",
            field_evidence={"deadline": "截止日期 2000-01-10"},
            text="软件工程：实验报告，请提交实验报告。",
        ),
    )
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        event = session.get(ChaoxingEvent, event_id)
        suggestion = session.get(Suggestion, event.suggestion_id)
        assert suggestion.status == "pending"
        assert services.matching_tasks(session, suggestion.fields)


def test_attachment_gap_is_visible_in_suggestion(intake):
    event_id, _ = intake.receive(
        "local-account", notice(attachments=[{"name": "题目.pdf", "status": "listed"}])
    )
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        suggestion = session.exec(select(Suggestion)).one()
        assert any("附件" in warning for warning in suggestion.warnings)


def test_collected_original_is_linked_to_message_and_saved_only_once(intake):
    event_id, _ = intake.receive(
        "local-account",
        notice(
            attachments=[
                {"name": "../报告.pdf", "content_base64": base64.b64encode(b"demo").decode()}
            ]
        ),
    )
    assert intake.process(event_id) == "done"
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        attachment = session.exec(select(Attachment)).one()
        message = session.exec(select(Message)).one()
        assert attachment.message_id == message.id
        assert "/" not in attachment.filename
        directory = Path(intake.engine.url.database).parent / "uploads"
        assert original_path(directory, attachment).read_bytes() == b"demo"


def test_text_attachment_is_extracted_locally_without_changing_original_message(intake):
    content = "请提交电子报告".encode("gb18030")
    event_id, _ = intake.receive(
        "local-account",
        notice(
            attachments=[{"name": "说明.txt", "content_base64": base64.b64encode(content).decode()}]
        ),
    )
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        attachment = session.exec(select(Attachment)).one()
        assert attachment.status == "ready"
        assert attachment.text == "请提交电子报告"
        assert session.exec(select(Message)).one().text == notice()["text"]


def test_unreadable_text_attachment_keeps_original_and_stays_in_review(intake):
    event_id, _ = intake.receive(
        "local-account",
        notice(
            attachments=[
                {"name": "说明.txt", "content_base64": base64.b64encode(b"\x00\x00").decode()}
            ]
        ),
    )
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        attachment = session.exec(select(Attachment)).one()
        assert attachment.status == "failed"
        directory = Path(intake.engine.url.database).parent / "uploads"
        assert original_path(directory, attachment).read_bytes() == b"\x00\x00"


@pytest.mark.parametrize("retry_state", ["failed", "pending"])
def test_interrupted_intake_reuses_saved_suggestion_without_nested_transaction(intake, retry_state):
    event_id, _ = intake.receive("local-account", notice())
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        event = session.get(ChaoxingEvent, event_id)
        suggestion_id = event.suggestion_id
        event.state, event.suggestion_id = retry_state, None
        session.add(event)
        session.commit()
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        assert session.get(ChaoxingEvent, event_id).suggestion_id == suggestion_id
        assert len(session.exec(select(Message)).all()) == 1
        assert len(session.exec(select(Suggestion)).all()) == 1
        assert session.exec(select(Task)).all() == []


def test_learning_notice_end_time_reaches_review_without_creating_a_task(intake):
    job = notice(text="软件工程作业：请提交需求分析报告。\n结束时间：2026-11-10 18:26")
    event_id, _ = intake.receive("local-account", job)
    assert intake.process(event_id) == "done"
    with Session(intake.engine) as session:
        event = session.get(ChaoxingEvent, event_id)
        suggestion = session.get(Suggestion, event.suggestion_id)
        assert suggestion.fields["due_at"] == "2026-11-10T10:26:00+00:00"
        assert suggestion.evidence["due_at"]["method"] == "rule"
        assert suggestion.status == "pending"
        assert session.exec(select(Task)).all() == []
