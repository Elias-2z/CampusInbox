"""Date-only task deadlines must never become fabricated clock times."""

import sqlite3
from datetime import UTC, date, datetime

import httpx
import pytest
from icalendar import Calendar

from campus_inbox.main import create_app
from campus_inbox.schemas import TaskFields

pytestmark = pytest.mark.anyio


class Clock:
    current = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)

    def __call__(self):
        return self.current


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def context(tmp_path):
    clock = Clock()
    app = create_app(tmp_path / "dates.db", reminder_clock=clock, qq_background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, clock


async def create_date_task(client, day="2026-11-03"):
    response = await client.post("/api/tasks", json={"title": "实验报告", "due_date": day})
    assert response.status_code == 200, response.text
    return response.json()


def edit(task, **changes):
    return {
        **{key: task[key] for key in TaskFields.model_fields if key in task},
        "status": task["status"],
        "expected_version": task["version"],
        **changes,
    }


async def test_date_only_round_trip_and_legacy_edit_do_not_lose_date(context):
    client, _ = context
    task = await create_date_task(client)
    assert task["due_at"] is None and task["due_date"] == "2026-11-03"
    legacy = edit(task, title="修改标题")
    legacy.pop("due_date")
    response = await client.patch(f"/api/tasks/{task['id']}", json=legacy)
    assert response.status_code == 200, response.text
    assert response.json()["due_date"] == "2026-11-03"
    assert (await client.patch(f"/api/tasks/{task['id']}", json=legacy)).status_code == 409
    changed = await client.patch(
        f"/api/tasks/{task['id']}",
        json=edit(response.json(), due_date=None, due_at="2026-11-03T20:00:00+08:00"),
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["due_date"] is None
    assert changed.json()["due_at"] == "2026-11-03T12:00:00Z"
    invalid = await client.post(
        "/api/tasks",
        json={"title": "冲突", "due_date": "2026-11-03", "due_at": "2026-11-03T20:00:00Z"},
    )
    assert invalid.status_code == 422
    invalid_date = await client.post(
        "/api/tasks", json={"title": "错误日期", "due_date": "2026-11-03T00:00:00Z"}
    )
    assert invalid_date.status_code == 422
    timestamp_date = await client.post(
        "/api/tasks", json={"title": "错误日期", "due_date": 1793664000}
    )
    assert timestamp_date.status_code == 422


async def test_date_only_notice_can_be_reviewed_and_saved_without_inventing_a_time(context):
    client, _ = context
    response = await client.post(
        "/api/inbox",
        json={
            "text": "软件工程：2026年11月8日前提交实验报告。",
            "sent_at": "2026-11-03T09:00:00+08:00",
        },
    )
    assert response.status_code == 200, response.text
    suggestion = response.json()
    assert suggestion["fields"]["due_date"] == "2026-11-08"
    assert suggestion["fields"]["due_at"] is None
    accepted = await client.post(
        f"/api/suggestions/{suggestion['id']}/accept", json=suggestion["fields"]
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["due_date"] == "2026-11-08"
    assert accepted.json()["due_at"] is None


async def test_done_remains_reminded_submitted_and_legacy_completed_do_not(context):
    client, clock = context
    task = await create_date_task(client)
    done = await client.patch(f"/api/tasks/{task['id']}", json=edit(task, status="done"))
    assert done.status_code == 200, done.text
    initial = (await client.get("/api/reminders")).json()["items"]
    assert len(initial) == 1
    assert initial[0]["phase"] == "upcoming"
    assert initial[0]["due_at"] is None and initial[0]["due_date"] == "2026-11-03"
    read = await client.post(
        f"/api/reminders/{initial[0]['id']}/read", json={"expected_version": 0}
    )
    assert read.status_code == 200, read.text
    assert (await client.get("/api/reminders")).json()["items"][0]["status"] == "read"
    clock.current = datetime(2026, 11, 3, 15, 59, tzinfo=UTC)
    assert (await client.get("/api/reminders")).json()["items"][0]["phase"] == "upcoming"
    clock.current = datetime(2026, 11, 3, 16, 0, tzinfo=UTC)
    overdue = (await client.get("/api/reminders")).json()["items"][0]
    assert overdue["phase"] == "overdue" and overdue["id"] != initial[0]["id"]
    assert overdue["status"] == "unread"
    submitted = await client.patch(
        f"/api/tasks/{task['id']}", json=edit(done.json(), status="submitted")
    )
    assert submitted.status_code == 200, submitted.text
    assert (await client.get("/api/reminders")).json()["items"] == []
    completed = await client.patch(
        f"/api/tasks/{task['id']}", json=edit(submitted.json(), status="completed")
    )
    assert completed.status_code == 200, completed.text
    assert (await client.get("/api/reminders")).json()["items"] == []


async def test_date_calendar_and_ics_use_an_all_day_date(context):
    client, _ = context
    task = await create_date_task(client)
    params = {"start": "2026-11-03", "end": "2026-11-04", "timezone": "America/New_York"}
    result = await client.get("/api/calendar", params=params)
    assert result.status_code == 200, result.text
    assert [item["id"] for item in result.json()["tasks"]] == [task["id"]]
    assert result.json()["undated"] == []
    exported = await client.get("/api/calendar/export.ics", params=params)
    assert exported.status_code == 200, exported.text
    event = Calendar.from_ical(exported.content).walk("VEVENT")[0]
    assert event.decoded("DTSTART") == date(2026, 11, 3)
    assert "DTEND" not in event and "DURATION" not in event


async def test_date_deadline_review_and_undo_preserve_precision_and_done_state(context):
    client, _ = context
    original = await create_date_task(client)
    done = await client.patch(f"/api/tasks/{original['id']}", json=edit(original, status="done"))
    notice = await client.post(
        "/api/inbox",
        json={"text": "实验报告延期到11月5日。", "sent_at": "2026-11-02T12:00:00+08:00"},
    )
    assert notice.status_code == 200, notice.text
    suggestion = notice.json()
    assert suggestion["fields"]["due_date"] == "2026-11-05"
    preview = await client.post(
        f"/api/suggestions/{suggestion['id']}/deadline-preview",
        json={"task_id": original["id"]},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["proposed_due_date"] == "2026-11-05"
    assert preview.json()["proposed_due_at"] is None
    assert preview.json()["evidence"]["method"] == "rule"
    assert preview.json()["evidence"]["quote"] == "11月5日"
    assert (await client.get(f"/api/tasks/{original['id']}")).json()["task"]["due_date"] == (
        "2026-11-03"
    )
    applied = await client.post(f"/api/deadline-proposals/{preview.json()['id']}/confirm")
    assert applied.status_code == 200, applied.text
    assert applied.json()["task"]["due_date"] == "2026-11-05"
    assert applied.json()["task"]["status"] == "done"
    detail = await client.get(f"/api/tasks/{original['id']}")
    assert detail.json()["deadline_changes"][0]["can_undo"]
    undone = await client.post(f"/api/deadline-changes/{suggestion['id']}/undo")
    assert undone.status_code == 200, undone.text
    assert undone.json()["task"]["due_date"] == "2026-11-03"
    assert undone.json()["task"]["due_at"] is None
    assert undone.json()["task"]["version"] == done.json()["version"] + 2


@pytest.mark.parametrize(
    "original,proposed",
    [
        ({"due_date": "2026-11-03"}, {"due_at": "2026-11-05T20:00:00+08:00"}),
        ({"due_at": "2026-11-03T20:00:00+08:00"}, {"due_date": "2026-11-05"}),
    ],
)
async def test_review_switches_precision_and_restores_original_on_undo(context, original, proposed):
    client, _ = context
    response = await client.post("/api/tasks", json={"title": "实验报告", **original})
    assert response.status_code == 200, response.text
    task = response.json()
    notice = (
        await client.post(
            "/api/inbox",
            json={"text": "实验报告延期到11月5日。", "sent_at": "2026-11-02T12:00:00+08:00"},
        )
    ).json()
    preview = await client.post(
        f"/api/suggestions/{notice['id']}/deadline-preview",
        json={"task_id": task["id"], **proposed},
    )
    assert preview.status_code == 200, preview.text
    applied = await client.post(f"/api/deadline-proposals/{preview.json()['id']}/confirm")
    assert applied.status_code == 200, applied.text
    changed = applied.json()["task"]
    assert (changed["due_date"] is None) != (changed["due_at"] is None)
    undone = await client.post(f"/api/deadline-changes/{notice['id']}/undo")
    assert undone.status_code == 200, undone.text
    assert undone.json()["task"]["due_at"] == task["due_at"]
    assert undone.json()["task"]["due_date"] == task["due_date"]


async def test_date_review_rejects_stale_task_version(context):
    client, _ = context
    original = await create_date_task(client)
    notice = (
        await client.post(
            "/api/inbox",
            json={"text": "实验报告延期到11月5日。", "sent_at": "2026-11-02T12:00:00+08:00"},
        )
    ).json()
    preview = await client.post(
        f"/api/suggestions/{notice['id']}/deadline-preview",
        json={"task_id": original["id"], "due_date": "2026-11-05"},
    )
    assert preview.status_code == 200, preview.text
    edited = await client.patch(
        f"/api/tasks/{original['id']}", json=edit(original, due_date="2026-11-04")
    )
    assert edited.status_code == 200
    assert (
        await client.post(f"/api/deadline-proposals/{preview.json()['id']}/confirm")
    ).status_code == 409
    assert (await client.get(f"/api/tasks/{original['id']}")).json()["task"]["due_date"] == (
        "2026-11-04"
    )


async def test_date_review_and_reminder_decisions_survive_restart(context, tmp_path):
    client, clock = context
    original = await create_date_task(client)
    reminder = (await client.get("/api/reminders")).json()["items"][0]
    assert (
        await client.post(f"/api/reminders/{reminder['id']}/read", json={"expected_version": 0})
    ).status_code == 200
    notice = (
        await client.post(
            "/api/inbox",
            json={"text": "实验报告延期到11月5日。", "sent_at": "2026-11-02T12:00:00+08:00"},
        )
    ).json()
    preview = await client.post(
        f"/api/suggestions/{notice['id']}/deadline-preview",
        json={"task_id": original["id"], "due_date": "2026-11-05"},
    )
    assert preview.status_code == 200, preview.text
    app = create_app(tmp_path / "dates.db", reminder_clock=clock, qq_background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as fresh:
            assert (await fresh.get("/api/reminders")).json()["items"][0]["status"] == "read"
            confirmed = await fresh.post(f"/api/deadline-proposals/{preview.json()['id']}/confirm")
            assert confirmed.status_code == 200, confirmed.text
            undone = await fresh.post(f"/api/deadline-changes/{notice['id']}/undo")
            assert undone.status_code == 200, undone.text
            assert (await fresh.get("/api/reminders")).json()["items"][0]["status"] == "read"
            assert undone.json()["task"]["due_date"] == "2026-11-03"


async def test_cancel_review_restores_done_state_and_date(context):
    client, _ = context
    original = await create_date_task(client)
    done = await client.patch(f"/api/tasks/{original['id']}", json=edit(original, status="done"))
    notice = (
        await client.post(
            "/api/inbox",
            json={"text": "实验报告取消，不用再提交。", "sent_at": "2026-11-02T12:00:00+08:00"},
        )
    ).json()
    preview = await client.post(
        f"/api/suggestions/{notice['id']}/cancellation-preview", json={"task_id": original["id"]}
    )
    assert preview.status_code == 200, preview.text
    applied = await client.post(f"/api/cancellation-proposals/{preview.json()['id']}/confirm")
    assert applied.status_code == 200, applied.text
    assert applied.json()["task"]["status"] == "cancelled"
    undone = await client.post(f"/api/cancellation-changes/{notice['id']}/undo")
    assert undone.status_code == 200, undone.text
    assert undone.json()["task"]["status"] == "done"
    assert undone.json()["task"]["due_date"] == done.json()["due_date"]


async def test_date_to_precise_deadline_merge_and_undo_restores_date(context):
    client, _ = context
    original = await create_date_task(client)
    notice = await client.post(
        "/api/inbox",
        json={
            "text": "实验报告请于11月5日晚上八点前提交。",
            "sent_at": "2026-11-02T12:00:00+08:00",
        },
    )
    assert notice.status_code == 200, notice.text
    preview = await client.post(
        f"/api/suggestions/{notice.json()['id']}/merge-preview",
        json={"task_id": original["id"], "fields": ["due_at"]},
    )
    assert preview.status_code == 200, preview.text
    changed = await client.post(f"/api/merge-proposals/{preview.json()['id']}/confirm")
    assert changed.status_code == 200, changed.text
    assert changed.json()["task"]["due_at"] == "2026-11-05T12:00:00Z"
    assert changed.json()["task"]["due_date"] is None
    undone = await client.post(f"/api/merge-changes/{changed.json()['id']}/undo")
    assert undone.status_code == 200, undone.text
    assert undone.json()["task"]["due_date"] == "2026-11-03"
    assert undone.json()["task"]["due_at"] is None


async def test_existing_database_is_backed_up_before_date_column_migration(context, tmp_path):
    client, _ = context
    old = (await client.post("/api/tasks", json={"title": "旧任务"})).json()
    path = tmp_path / "dates.db"
    with sqlite3.connect(path) as db:
        columns = {r[1] for r in db.execute("PRAGMA table_info(task)")}
        if "due_date" in columns:
            db.execute("ALTER TABLE task DROP COLUMN due_date")
    app = create_app(path, qq_background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as fresh:
            migrated = (await fresh.get("/api/tasks")).json()
            assert migrated[0]["id"] == old["id"]
            assert migrated[0]["status"] == "todo" and migrated[0]["due_date"] is None
    backups = list((tmp_path / "backups").glob("before-task-date-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("SELECT title FROM task WHERE id=?", (old["id"],)).fetchone() == (
            "旧任务",
        )
        assert "due_date" not in {r[1] for r in backup.execute("PRAGMA table_info(task)")}
    app = create_app(path, qq_background=False)
    async with app.router.lifespan_context(app):
        pass
    assert len(list((tmp_path / "backups").glob("before-task-date-*.sqlite3"))) == 1
