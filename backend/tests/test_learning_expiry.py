from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from test_chaoxing_intake import notice as learning_notice
from test_learning_followups import job, receive

from campus_inbox import services
from campus_inbox.connectors import chaoxing
from campus_inbox.connectors.chaoxing_runtime import ChaoxingRuntime
from campus_inbox.database import initialize, make_engine
from campus_inbox.main import create_app
from campus_inbox.models import Message, Suggestion, Task
from campus_inbox.providers import OllamaProvider
from campus_inbox.schemas import TaskFields


@pytest.fixture
def inbox_client(tmp_path):
    with TestClient(
        create_app(tmp_path / "flows.db", qq_background=False, chaoxing_background=False)
    ) as client:
        yield client


def paste(client, text):
    response = client.post(
        "/api/inbox", json={"text": text, "sent_at": "2026-11-05T17:21:00+08:00"}
    )
    assert response.status_code == 200
    return response.json()


def test_expiry_between_merge_preview_and_confirm_preserves_task(inbox_client, monkeypatch):
    client = inbox_client
    clock = [datetime(2026, 11, 5, 12, tzinfo=UTC)]
    monkeypatch.setattr(chaoxing, "now", lambda: clock[0])
    original = client.post(
        "/api/tasks",
        json={"title": "数据库课程报告", "course": "数据库原理", "due_date": "2026-11-09"},
    ).json()
    item = paste(client, "数据库原理：11月6日19:00前提交数据库课程报告。")
    proposal = client.post(
        f"/api/suggestions/{item['id']}/merge-preview",
        json={"task_id": original["id"], "fields": ["due_at"]},
    ).json()
    clock[0] = datetime(2026, 11, 6, 11, tzinfo=UTC)
    assert client.post(f"/api/merge-proposals/{proposal['id']}/confirm").status_code == 409
    assert client.get(f"/api/tasks/{original['id']}").json()["task"] == original


def test_expired_split_archives_only_the_expired_item(inbox_client, monkeypatch):
    client = inbox_client
    clock = [datetime(2026, 11, 5, 12, tzinfo=UTC)]
    monkeypatch.setattr(chaoxing, "now", lambda: clock[0])
    item = paste(
        client, "软件工程：\n1. 11月6日19:00前提交设计作业。\n2. 11月9日提交需求分析报告。"
    )
    first, second = client.post(f"/api/suggestions/{item['id']}/split").json()["split_items"]
    clock[0] = datetime(2026, 11, 6, 11, tzinfo=UTC)
    response = client.post(
        f"/api/split-drafts/{first['id']}/accept",
        json={**first["fields"], "expected_updated_at": first["updated_at"]},
    )
    assert response.status_code == 409
    [pending] = client.get("/api/inbox?status=pending").json()
    assert [draft["status"] for draft in pending["split_items"]] == ["archived", "pending"]
    assert (
        client.post(
            f"/api/split-drafts/{second['id']}/accept",
            json={**second["fields"], "expected_updated_at": second["updated_at"]},
        ).status_code
        == 200
    )


def test_submitted_platform_task_closes_older_draft_at_confirmation(inbox_client):
    client = inbox_client
    intake = chaoxing.ChaoxingIntake(client.app.state.engine, OllamaProvider)
    original = job(captured_at="2026-11-05T09:00:00Z")
    old_id = receive(intake, original)
    receive(
        intake, {**original, "platform_status": "已提交", "captured_at": "2026-11-06T09:00:00Z"}
    )
    assert (
        client.post(
            f"/api/suggestions/{old_id}/accept",
            json={"title": "第一次作业", "course": "数据库", "kind": "homework"},
        ).status_code
        == 409
    )
    assert client.get("/api/inbox?status=pending").json() == []
    assert client.get("/api/tasks").json() == []


def test_ended_checkin_archives_previous_draft_and_cannot_be_restored(inbox_client):
    client = inbox_client
    intake = chaoxing.ChaoxingIntake(client.app.state.engine, OllamaProvider)
    active = learning_notice(
        kind="checkin_activity",
        message_id="active",
        text="位置签到",
        title="位置签到",
        published_at="2099-01-02T09:00:00+08:00",
        deadline="2099-01-02T09:30:00+08:00",
        activity_id="123",
        course_id="456",
        class_id="789",
        field_evidence={"deadline": "学习通签到活动结束时间"},
    )
    old_id = receive(intake, active)
    ended_id = receive(
        intake,
        {
            **active,
            "message_id": "ended",
            "deadline": None,
            "activity_state": "ended",
            "field_evidence": {"activity_state": "已结束"},
        },
    )
    assert client.get("/api/inbox?status=pending").json() == []
    for item_id in (old_id, ended_id):
        assert client.post(f"/api/suggestions/{item_id}/restore-archive").status_code == 409


@pytest.mark.parametrize("expires_before_import", [True, False])
def test_manual_import_and_confirm_enforce_expiry(tmp_path, monkeypatch, expires_before_import):
    clock = [datetime(2026, 11, 5, 11 if expires_before_import else 10, tzinfo=UTC)]
    monkeypatch.setattr(chaoxing, "now", lambda: clock[0])
    with TestClient(
        create_app(tmp_path / "confirm.db", qq_background=False, chaoxing_background=False)
    ) as client:
        imported = client.post(
            "/api/inbox",
            json={
                "text": "数据库原理：今天19:00前提交数据库课程报告，上传至学习通。",
                "source": "手动粘贴",
                "sent_at": "2026-11-05T17:21:00+08:00",
            },
        ).json()
        assert imported["status"] == ("archived" if expires_before_import else "pending")
        clock[0] = datetime(2026, 11, 5, 11, tzinfo=UTC)
        response = client.post(
            f"/api/suggestions/{imported['id']}/accept",
            json={"title": "数据库课程报告", "course": "数据库原理", "kind": "homework"},
        )
        assert response.status_code == 409
        with Session(client.app.state.engine) as session:
            assert session.get(Suggestion, imported["id"]).status == "archived"
            assert session.exec(select(Task)).all() == []


@pytest.mark.parametrize(
    "source,key",
    [("手动粘贴", "manual-paste"), ("QQ通知", "qq:expiry-test"), ("图片导入", "image-import")],
)
def test_expired_notices_from_any_source_are_archived(intake, source, key):
    with Session(intake.engine) as session:
        message = Message(
            text="今天19:00前提交数据库课程报告。",
            source=source,
            sent_at=datetime(2026, 11, 5, 9, 21, tzinfo=UTC),
            dedupe_key=key,
        )
        session.add(message)
        session.flush()
        suggestion = Suggestion(
            message_id=message.id,
            status="pending",
            action="create",
            fields={
                "title": "数据库课程报告",
                "due_at": "2026-11-05T11:00:00+00:00",
                "due_date": None,
            },
            evidence={"due_at": {"method": "rule", "quote": "今天19:00"}},
        )
        session.add(suggestion)
        session.commit()
        assert chaoxing.archive_expired(session, datetime(2026, 11, 6, tzinfo=UTC)) == 1
        session.refresh(suggestion)
        assert suggestion.status == "archived"
        assert session.get(Message, message.id).text == "今天19:00前提交数据库课程报告。"
        assert session.exec(select(Task)).all() == []


def test_date_only_expiry_respects_non_learning_message_timezone(intake):
    with Session(intake.engine) as session:
        message = Message(
            text="11月5日前提交报告",
            source="手动粘贴",
            timezone="America/Los_Angeles",
            sent_at=datetime(2026, 11, 1, tzinfo=UTC),
            dedupe_key="timezone-expiry",
        )
        session.add(message)
        session.flush()
        source = Suggestion(
            message_id=message.id,
            status="pending",
            action="create",
            fields={"title": "课程报告", "due_date": "2026-11-05"},
            evidence={"due_date": {"method": "rule", "quote": "11月5日"}},
        )
        session.add(source)
        session.commit()
        assert chaoxing.archive_expired(session, datetime(2026, 11, 6, 5, tzinfo=UTC)) == 0
        assert chaoxing.archive_expired(session, datetime(2026, 11, 6, 8, tzinfo=UTC)) == 1


def test_old_lab_schedule_is_corrected_before_expiry(intake):
    with Session(intake.engine) as session:
        message = Message(
            text="本周三第一次实验课，不要迟到，记得穿工作服！",
            source="手动粘贴",
            sent_at=datetime(2026, 11, 2, tzinfo=UTC),
            dedupe_key="old-lab-schedule",
        )
        session.add(message)
        session.flush()
        suggestion = Suggestion(
            message_id=message.id,
            status="pending",
            action="create",
            fields={"title": "第一次实验课", "due_date": "2026-11-04"},
            evidence={"due_date": {"method": "rule", "quote": "本周三"}},
        )
        session.add(suggestion)
        session.commit()
        assert chaoxing.archive_expired(session, datetime(2026, 11, 7, tzinfo=UTC)) == 0
        session.refresh(suggestion)
        assert suggestion.status == "pending"
        assert suggestion.fields.get("due_date") is None
        assert "due_date" not in suggestion.evidence
        assert services.inbox_item(session, suggestion).activity_schedule == ["本周三第一次实验课"]


@pytest.mark.parametrize("year,expected_status", [(2026, "archived"), (2099, "pending")])
def test_old_cached_end_time_is_recovered_before_expiry_filter(
    intake, monkeypatch, year, expected_status
):
    monkeypatch.setattr(chaoxing, "now", lambda: datetime(2026, 9, 15, 8, tzinfo=UTC))
    text = (
        f"课程名称：自然辩证法概论\n作业名称：作业{year}0915\n"
        f"开始时间：{year}-09-15 16:25\n结束时间：{year}-09-15 17:45"
    )
    source_id = receive(
        intake,
        job(
            kind="course_notice",
            course="自然辩证法概论",
            text=text,
            captured_at="2026-09-15T08:25:00Z",
        ),
    )
    with Session(intake.engine) as session:
        source = session.get(Suggestion, source_id)
        source.fields = {**source.fields, "due_at": None, "due_date": None}
        source.evidence = {
            k: v for k, v in source.evidence.items() if k not in {"due_at", "due_date"}
        }
        source.warnings = ["发现日期和时刻，但无法确认是截止时间，请手动核对。"]
        session.add(source)
        session.commit()
    with Session(intake.engine) as session:
        chaoxing.archive_expired(session, datetime(2026, 11, 6, tzinfo=UTC))
    with Session(intake.engine) as session:
        source = session.get(Suggestion, source_id)
        assert source.fields["due_at"] == f"{year}-09-15T09:45:00+00:00"
        assert source.fields["due_date"] is None
        assert source.status == expected_status
        assert "发现日期和时刻，但无法确认是截止时间，请手动核对。" not in source.warnings


@pytest.fixture
def intake(tmp_path):
    engine = make_engine(tmp_path / "expiry.db")
    initialize(engine)
    yield chaoxing.ChaoxingIntake(engine, OllamaProvider)
    engine.dispose()


@pytest.mark.parametrize("kind", ["course_notice", "task_center"])
@pytest.mark.parametrize(
    "text",
    [
        "数据库：11月3日20:00前提交实验报告。",
        "数据库：明天晚上八点前提交实验报告。",
    ],
)
def test_expired_notice_does_not_enter_pending_even_when_title_matches_task(
    intake, monkeypatch, kind, text
):
    monkeypatch.setattr(chaoxing, "now", lambda: datetime(2026, 11, 6, tzinfo=UTC))
    with Session(intake.engine) as session:
        task = services.manual_create(session, TaskFields(title="实验报告", course="数据库"))
        original = services.snapshot(task)
        task_id = task.id
    source_id = receive(
        intake,
        job(
            kind=kind,
            title="实验报告",
            text=text,
            captured_at="2026-11-01T01:00:00Z",
            published_at="2026-11-01T01:00:00Z",
            deadline=None,
            field_evidence={"title": "实验报告"},
        ),
    )
    with Session(intake.engine) as session:
        assert session.get(Suggestion, source_id).status == "archived"
        assert services.snapshot(session.get(Task, task_id)) == original


def test_date_only_remains_pending_today_then_archives_on_next_background_check(
    intake, monkeypatch, tmp_path
):
    clock = [datetime(2026, 11, 6, 15, 59, tzinfo=UTC)]
    monkeypatch.setattr(chaoxing, "now", lambda: clock[0])
    source_id = receive(
        intake,
        job(
            deadline="2026-11-06",
            published_at="2026-11-01T01:00:00Z",
            field_evidence={"title": "第一次作业", "deadline": "截止日期2026-11-06"},
        ),
    )
    with Session(intake.engine) as session:
        assert session.get(Suggestion, source_id).status == "pending"
    clock[0] = datetime(2026, 11, 6, 16, 0, tzinfo=UTC)
    runtime = ChaoxingRuntime(intake.engine, OllamaProvider, tmp_path)
    runtime.enabled = True
    runtime.process_pending()
    with Session(intake.engine) as session:
        assert session.get(Suggestion, source_id).status == "archived"
        assert session.exec(select(Task)).all() == []


def test_conflicting_or_unknown_dates_are_not_silently_archived(intake, monkeypatch):
    monkeypatch.setattr(chaoxing, "now", lambda: datetime(2026, 11, 6, tzinfo=UTC))
    source_id = receive(
        intake,
        job(
            text="数据库：2099年12月10日20:00前提交第一次作业。",
            deadline="2000-01-01T12:00:00+08:00",
        ),
    )
    with Session(intake.engine) as session:
        assert session.get(Suggestion, source_id).status == "pending"


@pytest.mark.parametrize("status", ["accepted", "ignored"])
def test_background_expiry_preserves_handled_records(intake, monkeypatch, tmp_path, status):
    clock = [datetime(2026, 11, 6, tzinfo=UTC)]
    monkeypatch.setattr(chaoxing, "now", lambda: clock[0])
    source_id = receive(
        intake, job(deadline="2026-11-07", field_evidence={"deadline": "截止日期2026-11-07"})
    )
    with Session(intake.engine) as session:
        source = session.get(Suggestion, source_id)
        source.status = status
        session.add(source)
        session.commit()
    clock[0] = datetime(2026, 11, 9, tzinfo=UTC)
    runtime = ChaoxingRuntime(intake.engine, OllamaProvider, tmp_path)
    runtime.enabled = True
    runtime.process_pending()
    with Session(intake.engine) as session:
        assert session.get(Suggestion, source_id).status == status
