"""Reminder timing, lifecycle invalidation and persistent decisions with a deterministic clock."""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from campus_inbox.connectors import chaoxing
from campus_inbox.main import create_app
from campus_inbox.schemas import TaskFields

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self):
        self.current = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)

    def __call__(self):
        return self.current

    def advance(self, **kwargs):
        self.current += timedelta(**kwargs)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def context(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(chaoxing, "now", clock)
    app = create_app(tmp_path / "reminders.db", reminder_clock=clock)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, clock


async def task(client, clock, minutes=60, **changes):
    response = await client.post(
        "/api/tasks",
        json={
            "title": "实验报告",
            "course": "软件工程",
            "due_at": (clock() + timedelta(minutes=minutes)).isoformat(),
            **changes,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def feed(client):
    response = await client.get("/api/reminders")
    assert response.status_code == 200, response.text
    return response.json()


async def act(client, item, operation="read", **changes):
    response = await client.post(
        f"/api/reminders/{item['id']}/{operation}",
        json={"expected_version": item["version"], **changes},
    )
    assert response.status_code == 200, response.text
    return response.json()


def edited(original, **changes):
    return {
        **{key: value for key, value in original.items() if key in TaskFields.model_fields},
        "status": original["status"],
        "expected_version": original["version"],
        **changes,
    }


async def configure(client, **changes):
    response = await client.put(
        "/api/settings/reminders",
        json={"enabled": True, "lead_minutes": 1440, "overdue_enabled": True, **changes},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_default_feed_includes_only_eligible_tasks_and_is_read_only(context, tmp_path):
    client, clock = context
    upcoming = await task(client, clock)
    overdue = await task(client, clock, minutes=-30)
    boundary = await task(client, clock, minutes=1440)
    await task(client, clock, minutes=1441)
    await task(client, clock, due_at=None)
    result = await feed(client)
    assert result["config"] == {"enabled": True, "lead_minutes": 1440, "overdue_enabled": True}
    assert [item["task"]["id"] for item in result["items"]] == [
        overdue["id"],
        upcoming["id"],
        boundary["id"],
    ]
    assert [item["phase"] for item in result["items"]] == ["overdue", "upcoming", "upcoming"]
    assert all(item["status"] == "unread" and item["version"] == 0 for item in result["items"])
    assert await feed(client) == result
    with sqlite3.connect(tmp_path / "reminders.db") as connection:
        assert connection.execute("SELECT count(*) FROM reminderstate").fetchone()[0] == 0
    assert (await client.get(f"/api/tasks/{upcoming['id']}")).json()["task"] == upcoming


async def test_read_is_persistent_and_does_not_complete_or_edit_task(context, tmp_path):
    client, clock = context
    original = await task(client, clock)
    before = (await client.get(f"/api/tasks/{original['id']}")).json()
    item = (await feed(client))["items"][0]
    marked = await act(client, item)
    assert marked["status"] == "read" and marked["version"] == 1
    assert (await client.get(f"/api/tasks/{original['id']}")).json() == before
    app = create_app(tmp_path / "reminders.db", reminder_clock=clock)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as fresh:
            assert (await feed(fresh))["items"][0]["status"] == "read"
            restored = await act(fresh, marked, "restore")
            assert restored["status"] == "unread" and restored["version"] == 2


async def test_snooze_expiry_and_restore_use_server_time(context):
    client, clock = context
    await task(client, clock, minutes=120)
    item = (await feed(client))["items"][0]
    snoozed = await act(client, item, "snooze", snooze_minutes=15)
    assert snoozed["status"] == "snoozed"
    assert datetime.fromisoformat(snoozed["snoozed_until"]) == clock() + timedelta(minutes=15)
    clock.advance(minutes=14, seconds=59)
    assert (await feed(client))["items"][0]["status"] == "snoozed"
    clock.advance(seconds=1)
    expired = (await feed(client))["items"][0]
    assert expired["status"] == "unread" and expired["snoozed_until"] is None
    again = await act(client, expired, "snooze", snooze_minutes=30)
    restored = await act(client, again, "restore")
    assert restored["status"] == "unread" and restored["snoozed_until"] is None


@pytest.mark.parametrize("decision", ["read", "snooze"])
async def test_overdue_is_new_occurrence_even_if_upcoming_handled(context, decision):
    client, clock = context
    await task(client, clock, minutes=5)
    item = (await feed(client))["items"][0]
    await act(client, item, decision)
    clock.advance(minutes=5)
    expired = (await feed(client))["items"][0]
    assert expired["phase"] == "overdue" and expired["id"] != item["id"]
    assert expired["status"] == "unread" and expired["version"] == 0
    assert (
        await client.post(f"/api/reminders/{item['id']}/read", json={"expected_version": 1})
    ).status_code == 409


@pytest.mark.parametrize("status", ["completed", "cancelled", "archived"])
async def test_inactive_tasks_disappear_and_stale_actions_fail(context, status):
    client, clock = context
    original = await task(client, clock)
    item = (await feed(client))["items"][0]
    response = await client.patch(
        f"/api/tasks/{original['id']}", json=edited(original, status=status)
    )
    assert response.status_code == 200
    assert (await feed(client))["items"] == []
    assert (
        await client.post(f"/api/reminders/{item['id']}/snooze", json={"expected_version": 0})
    ).status_code == 409
    await client.patch(
        f"/api/tasks/{original['id']}", json=edited(response.json(), status="in_progress")
    )
    assert (await feed(client))["items"][0]["id"] == item["id"]


async def test_deadline_changes_remove_old_occurrence_and_recompute_window(context):
    client, clock = context
    original = await task(client, clock)
    item = (await feed(client))["items"][0]
    await act(client, item)
    later = clock() + timedelta(days=3)
    changed = await client.patch(
        f"/api/tasks/{original['id']}", json=edited(original, due_at=later.isoformat())
    )
    assert changed.status_code == 200
    assert (await feed(client))["items"] == []
    assert (
        await client.post(f"/api/reminders/{item['id']}/restore", json={"expected_version": 1})
    ).status_code == 409
    clock.advance(days=2)
    new = (await feed(client))["items"][0]
    assert new["id"] != item["id"] and new["status"] == "unread"
    # Returning to the original due date returns that occurrence's own prior decision.
    await client.patch(
        f"/api/tasks/{original['id']}", json=edited(changed.json(), due_at=original["due_at"])
    )
    # Original due is now overdue, which is independent of the prior upcoming read state.
    restored = (await feed(client))["items"][0]
    assert restored["phase"] == "overdue" and restored["status"] == "unread"


async def test_title_edits_keep_occurrence_decisions_but_show_latest_title(context):
    client, clock = context
    original = await task(client, clock)
    item = (await feed(client))["items"][0]
    await act(client, item)
    await client.patch(
        f"/api/tasks/{original['id']}", json=edited(original, title="更新后的实验报告")
    )
    current = (await feed(client))["items"][0]
    assert current["id"] == item["id"] and current["status"] == "read"
    assert current["task"]["title"] == "更新后的实验报告"


async def test_settings_windows_disabled_and_overdue_toggle_preserve_decisions(context):
    client, clock = context
    future = await task(client, clock, minutes=120)
    await task(client, clock, minutes=-1)
    first = await feed(client)
    upcoming = next(item for item in first["items"] if item["task"]["id"] == future["id"])
    await act(client, upcoming)
    await configure(client, lead_minutes=60, overdue_enabled=False)
    assert (await feed(client))["items"] == []
    await configure(client, enabled=False)
    assert not (await feed(client))["config"]["enabled"]
    assert (await feed(client))["items"] == []
    assert (
        await client.post(f"/api/reminders/{upcoming['id']}/restore", json={"expected_version": 1})
    ).status_code == 409
    await configure(client)
    restored = next(item for item in (await feed(client))["items"] if item["id"] == upcoming["id"])
    assert restored["status"] == "read"


@pytest.mark.parametrize("lead", [0, -1, 4, 10081])
async def test_invalid_settings_are_rejected(context, lead):
    client, _clock = context
    assert (
        await client.put("/api/settings/reminders", json={"lead_minutes": lead})
    ).status_code == 422


async def test_concurrent_read_is_idempotent_and_stale_snooze_cannot_override_it(context):
    client, clock = context
    await task(client, clock)
    item = (await feed(client))["items"][0]
    responses = await asyncio.gather(
        *[
            client.post(f"/api/reminders/{item['id']}/read", json={"expected_version": 0})
            for _ in range(2)
        ]
    )
    assert [r.status_code for r in responses] == [200, 200]
    assert [r.json()["version"] for r in responses] == [1, 1]
    assert (
        await client.post(f"/api/reminders/{item['id']}/snooze", json={"expected_version": 0})
    ).status_code == 409


async def test_concurrent_snooze_does_not_extend_due_to_retry(context):
    client, clock = context
    await task(client, clock)
    item = (await feed(client))["items"][0]
    responses = await asyncio.gather(
        *[
            client.post(f"/api/reminders/{item['id']}/snooze", json={"expected_version": 0})
            for _ in range(2)
        ]
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert (await feed(client))["items"][0]["version"] == 1


@pytest.mark.parametrize("minutes", [-1, 0, 5, 1440])
async def test_invalid_snooze_values_are_rejected(context, minutes):
    client, clock = context
    await task(client, clock)
    item = (await feed(client))["items"][0]
    assert (
        await client.post(
            f"/api/reminders/{item['id']}/snooze",
            json={"expected_version": 0, "snooze_minutes": minutes},
        )
    ).status_code == 422


async def test_equivalent_timezone_instants_keep_same_id(context):
    client, clock = context
    original = await task(client, clock, due_at="2026-11-02T19:00:00+08:00")
    item = (await feed(client))["items"][0]
    await act(client, item)
    await client.patch(
        f"/api/tasks/{original['id']}", json=edited(original, due_at="2026-11-02T11:00:00Z")
    )
    changed = (await feed(client))["items"][0]
    assert changed["id"] == item["id"] and changed["status"] == "read"


async def test_snooze_and_settings_survive_restart(context, tmp_path):
    client, clock = context
    await task(client, clock)
    await configure(client, lead_minutes=180, overdue_enabled=False)
    item = (await feed(client))["items"][0]
    snoozed = await act(client, item, "snooze", snooze_minutes=60)
    app = create_app(tmp_path / "reminders.db", reminder_clock=clock)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as fresh:
            result = await feed(fresh)
            assert result["config"]["lead_minutes"] == 180
            assert not result["config"]["overdue_enabled"]
            assert result["items"][0] == snoozed


async def notice(client, clock, text):
    response = await client.post("/api/inbox", json={"text": text, "sent_at": clock().isoformat()})
    assert response.status_code == 200, response.text
    return response.json()


async def test_deadline_and_cancel_review_flows_invalidate_reminders(context):
    client, clock = context
    original = await task(client, clock)
    initial = (await feed(client))["items"][0]
    item = await notice(client, clock, "软件工程：实验报告延期到下周一晚上八点。")
    proposal = await client.post(
        f"/api/suggestions/{item['id']}/deadline-preview", json={"task_id": original["id"]}
    )
    assert proposal.status_code == 200
    assert (
        await client.post(f"/api/deadline-proposals/{proposal.json()['id']}/confirm")
    ).status_code == 200
    assert (await feed(client))["items"] == []
    assert (await client.post(f"/api/deadline-changes/{item['id']}/undo")).status_code == 200
    assert (await feed(client))["items"][0]["id"] == initial["id"]
    cancel = await notice(client, clock, "软件工程：实验报告取消，不用再提交。")
    cp = await client.post(
        f"/api/suggestions/{cancel['id']}/cancellation-preview", json={"task_id": original["id"]}
    )
    assert cp.status_code == 200
    assert (
        await client.post(f"/api/cancellation-proposals/{cp.json()['id']}/confirm")
    ).status_code == 200
    assert (await feed(client))["items"] == []
    assert (await client.post(f"/api/cancellation-changes/{cancel['id']}/undo")).status_code == 200
    assert (await feed(client))["items"][0]["id"] == initial["id"]


async def test_adding_reminder_schema_preserves_existing_records_and_model_settings(
    context, tmp_path
):
    client, clock = context
    original = await task(client, clock)
    before = (await client.get(f"/api/tasks/{original['id']}")).json()
    model = (await client.get("/api/settings/model")).json()
    with sqlite3.connect(tmp_path / "reminders.db") as connection:
        connection.execute("DROP TABLE reminderstate")
        connection.execute("DROP TABLE remindersettings")
    app = create_app(tmp_path / "reminders.db", reminder_clock=clock)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as fresh:
            assert (await fresh.get(f"/api/tasks/{original['id']}")).json() == before
            assert (await fresh.get("/api/settings/model")).json() == model
            assert len((await feed(fresh))["items"]) == 1


async def test_pending_notification_is_not_a_reminder_until_task_confirmed(context):
    client, clock = context
    item = await notice(client, clock, "软件工程：今天晚上八点前提交需求分析报告。")
    assert (await feed(client))["items"] == []
    accepted = await client.post(f"/api/suggestions/{item['id']}/accept", json=item["fields"])
    assert accepted.status_code == 200, accepted.text
    result = await feed(client)
    assert len(result["items"]) == 1
    assert result["items"][0]["task"]["id"] == accepted.json()["id"]


async def test_restoring_same_future_due_returns_that_occurrences_previous_read_state(context):
    client, clock = context
    original = await task(client, clock)
    initial = (await feed(client))["items"][0]
    await act(client, initial)
    changed = await client.patch(
        f"/api/tasks/{original['id']}",
        json=edited(original, due_at=(clock() + timedelta(hours=2)).isoformat()),
    )
    assert changed.status_code == 200
    other = (await feed(client))["items"][0]
    assert other["status"] == "unread" and other["id"] != initial["id"]
    restored = await client.patch(
        f"/api/tasks/{original['id']}", json=edited(changed.json(), due_at=original["due_at"])
    )
    assert restored.status_code == 200
    previous = (await feed(client))["items"][0]
    assert previous["id"] == initial["id"] and previous["status"] == "read"
