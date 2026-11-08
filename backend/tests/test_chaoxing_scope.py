"""Learning-platform scope filters retain records but never hide confirmed user tasks."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from campus_inbox.connectors.chaoxing import ChaoxingEvent, ChaoxingIntake
from campus_inbox.connectors.chaoxing_scope import archive_excluded, skip_reason
from campus_inbox.database import initialize, make_engine
from campus_inbox.models import Message, Suggestion, Task, now
from campus_inbox.providers import OllamaProvider


@pytest.mark.parametrize(
    ("kind", "age", "reason"),
    [
        ("chat", 0, "chat_disabled"),
        ("course_notice", 29, None),
        ("course_notice", 30, None),
        ("course_notice", 31, None),
        ("course_notice", 89, None),
        ("course_notice", 90, None),
        ("course_notice", 91, "old_notice"),
        ("task_center", 100, None),
        ("checkin_activity", 100, None),
    ],
)
def test_scope_uses_publication_date_and_leaves_task_center_independent(kind, age, reason):
    reference = datetime(2026, 11, 5, 10, tzinfo=UTC)
    stamp = reference - timedelta(days=age)
    assert skip_reason({"kind": kind, "captured_at": stamp.isoformat()}, reference) == reason


def test_unknown_notice_time_is_never_treated_as_recent_scan_time():
    assert skip_reason({"kind": "course_notice", "captured_at": None}) is None


@pytest.mark.parametrize(
    "status,past,expected",
    [
        ("archived", False, "pending"),
        ("archived", True, "archived"),
        ("ignored", False, "ignored"),
    ],
)
def test_extended_scope_recovers_old_auto_exclusions_but_not_expired_or_ignored(
    tmp_path, status, past, expected
):
    engine = make_engine(tmp_path / "extended.db")
    initialize(engine)
    intake = ChaoxingIntake(engine, OllamaProvider)
    stamp = now() - timedelta(days=60)
    event_id, _ = intake.receive(
        "account",
        {
            "kind": "course_notice",
            "message_id": "old-scope",
            "text": "请提交实验报告。",
            "captured_at": stamp.isoformat(),
        },
    )
    with Session(engine) as session:
        message = Message(text="请提交实验报告。", sent_at=stamp, dedupe_key="old-scope")
        session.add(message)
        session.flush()
        suggestion = Suggestion(
            message_id=message.id,
            status=status,
            action="create",
            fields={"title": "实验报告", "due_date": "2000-01-01" if past else "2099-12-10"},
            evidence={"due_date": {"method": "rule", "quote": "截止日期"}},
            warnings=["按采集范围归档：课程通知已超出近 30 天。"],
        )
        session.add(suggestion)
        session.flush()
        ident = suggestion.id
        event = session.get(ChaoxingEvent, event_id)
        event.suggestion_id, event.state = ident, "filtered"
        session.add(event)
        session.commit()
    archive_excluded(engine)
    with Session(engine) as session:
        assert session.get(Suggestion, ident).status == expected
        assert session.get(ChaoxingEvent, event_id).state == "done"
    engine.dispose()


def test_existing_old_and_chat_records_archive_without_changing_confirmed_work(tmp_path):
    engine = make_engine(tmp_path / "scope.db")
    initialize(engine)
    intake = ChaoxingIntake(engine, OllamaProvider)
    recent = now()
    created = []
    for index, (kind, age, status) in enumerate(
        [
            ("chat", 0, "pending"),
            ("course_notice", 91, "pending"),
            ("course_notice", 2, "pending"),
            ("chat", 0, "accepted"),
        ]
    ):
        job = dict(
            kind=kind,
            message_id=f"synthetic-{index}",
            text="请提交测试报告。",
            captured_at=(recent - timedelta(days=age)).isoformat(),
            direction="received",
        )
        event_id, _ = intake.receive("test-account", job)
        with Session(engine) as session:
            event = session.get(ChaoxingEvent, event_id)
            message = Message(
                text=job["text"], dedupe_key="chaoxing:" + event.identity, sent_at=recent
            )
            session.add(message)
            session.flush()
            suggestion = Suggestion(message_id=message.id, status=status)
            session.add(suggestion)
            session.flush()
            event.suggestion_id, event.state = suggestion.id, "done"
            session.add(event)
            if status == "accepted":
                session.add(
                    Task(title="已确认的任务", message_id=message.id, suggestion_id=suggestion.id)
                )
            session.commit()
            created.append(suggestion.id)
    result = archive_excluded(engine)
    assert result == {"filtered": 2, "archived": 2}
    with Session(engine) as session:
        assert [session.get(Suggestion, ident).status for ident in created] == [
            "archived",
            "archived",
            "pending",
            "accepted",
        ]
        assert len(session.exec(select(Message)).all()) == 4
        assert len(session.exec(select(Task)).all()) == 1
        assert session.exec(select(Task)).one().title == "已确认的任务"
    assert archive_excluded(engine) == {"filtered": 0, "archived": 0}
    engine.dispose()
