"""Calendar boundaries and exported files validated with an independent iCalendar parser."""

from datetime import UTC, datetime

import httpx
import pytest
from icalendar import Calendar

from campus_inbox.main import create_app
from campus_inbox.schemas import TaskFields

pytestmark = pytest.mark.anyio
PARAMS = {"start": "2026-10-01", "end": "2026-11-01", "timezone": "Asia/Shanghai"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def clock():
    return [datetime(2026, 3, 1, tzinfo=UTC)]


@pytest.fixture
async def client(tmp_path, clock):
    app = create_app(tmp_path / "calendar.db", reminder_clock=lambda: clock[0])
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


async def task(client, **changes):
    response = await client.post(
        "/api/tasks",
        json={
            "title": "实验四报告",
            "course": "数据库原理",
            "due_at": "2026-10-31T23:30:00+08:00",
            **changes,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def edit(original, **changes):
    return {
        **{key: value for key, value in original.items() if key in TaskFields.model_fields},
        "expected_version": original["version"],
        "status": original["status"],
        **changes,
    }


async def feed(client, **params):
    response = await client.get("/api/calendar", params={**PARAMS, **params})
    assert response.status_code == 200, response.text
    return response.json()


async def export(client, **params):
    response = await client.get("/api/calendar/export.ics", params={**PARAMS, **params})
    assert response.status_code == 200, response.text
    return response


def events(response):
    return Calendar.from_ical(response.content).walk("VEVENT")


async def test_expired_entries_are_hidden_from_calendar_and_export(client, clock):
    clock[0] = datetime(2026, 10, 1, tzinfo=UTC)
    old_date = await task(client, title="昨日截止", due_at=None, due_date="2026-09-30")
    old_time = await task(client, title="已到时刻", due_at="2026-10-01T08:00:00+08:00")
    today = await task(client, title="今日截止", due_at=None, due_date="2026-10-01")
    params = {"start": "2026-09-29", "status": "all"}
    assert [item["id"] for item in (await feed(client, **params))["tasks"]] == [today["id"]]
    assert len(events(await export(client, **params))) == 1
    ids = {item["id"] for item in (await client.get("/api/tasks")).json()}
    assert {old_date["id"], old_time["id"]} <= ids


async def test_local_month_boundaries_and_undated_tasks(client):
    before = await task(client, title="上个月", due_at="2026-09-30T23:59:59+08:00")
    first = await task(client, title="月初", due_at="2026-10-01T00:00:00+08:00")
    last = await task(client)
    after = await task(client, title="下个月", due_at="2026-11-01T00:00:00+08:00")
    undated = await task(client, title="截止时间待补充", due_at=None)
    result = await feed(client)
    assert {item["id"] for item in result["tasks"]} == {first["id"], last["id"]}
    assert [item["id"] for item in result["undated"]] == [undated["id"]]
    assert before["id"] not in {item["id"] for item in result["tasks"]}
    assert after["id"] not in {item["id"] for item in result["tasks"]}
    assert result["start"] == "2026-10-01" and result["end"] == "2026-11-01"
    utc_result = await feed(client, timezone="UTC")
    assert {item["id"] for item in utc_result["tasks"]} == {last["id"], after["id"]}


@pytest.mark.parametrize(
    "scope,status",
    [
        ("todo", "todo"),
        ("in_progress", "in_progress"),
        ("completed", "completed"),
        ("cancelled", "cancelled"),
        ("archived", "archived"),
    ],
)
async def test_explicit_status_filters_and_export_match(client, scope, status):
    original = await task(client)
    await client.patch(f"/api/tasks/{original['id']}", json=edit(original, status=status))
    result = await feed(client, status=scope)
    assert len(result["tasks"]) == 1 and result["tasks"][0]["status"] == status
    parsed = events(await export(client, status=scope))
    assert len(parsed) == 1
    assert str(parsed[0]["STATUS"]) == ("CANCELLED" if status == "cancelled" else "CONFIRMED")
    if status in {"cancelled", "completed", "archived"}:
        assert str(parsed[0]["SUMMARY"]).startswith("[已")


async def test_default_active_and_all_excludes_archive_with_course_filter(client):
    active = await task(client)
    completed = await task(client, title="已完成报告")
    await client.patch(f"/api/tasks/{completed['id']}", json=edit(completed, status="completed"))
    archived = await task(client, title="已归档报告")
    await client.patch(f"/api/tasks/{archived['id']}", json=edit(archived, status="archived"))
    other = await task(client, title="软件工程报告", course="软件工程")
    assert {item["id"] for item in (await feed(client))["tasks"]} == {active["id"], other["id"]}
    assert {
        item["id"] for item in (await feed(client, status="all", course="数据库原理"))["tasks"]
    } == {active["id"], completed["id"]}


async def test_date_window_across_dst_is_local_not_fixed_24_hours(client):
    early = await task(client, due_at="2026-03-08T05:00:00Z")
    late = await task(client, title="当天最后", due_at="2026-03-09T03:59:59Z")
    await task(client, title="次日边界", due_at="2026-03-09T04:00:00Z")
    result = await feed(client, start="2026-03-08", end="2026-03-09", timezone="America/New_York")
    assert {item["id"] for item in result["tasks"]} == {early["id"], late["id"]}


@pytest.mark.parametrize(
    "params",
    [
        {"start": "2026-11-01", "end": "2026-10-01"},
        {"start": "2026-10-01", "end": "2026-10-01"},
        {"start": "2026-01-01", "end": "2026-12-31"},
        {"start": "2026-02-30"},
        {"timezone": "Invalid/Zone"},
        {"status": "unknown"},
        {"course": "x" * 101},
        {"start": "0001-01-01", "end": "0001-01-02", "timezone": "Asia/Shanghai"},
    ],
)
async def test_invalid_window_filters_are_rejected_for_feed_and_export(client, params):
    for path in ("/api/calendar", "/api/calendar/export.ics"):
        assert (await client.get(path, params={**PARAMS, **params})).status_code == 422


async def test_ics_preserves_unicode_escaping_utc_and_does_not_invent_duration(client):
    title = "实验报告：中文🌱,分号;反斜线\\换行\nBEGIN:VEVENT"
    description = "中文与emoji🌞" * 80 + "\r\n第二行,含;符号\\结尾"
    original = await task(
        client,
        title=title,
        description=description,
        location="实验楼,三层;A\\B",
        submission_platform="学习通",
    )
    response = await export(client)
    assert response.headers["content-type"].startswith("text/calendar")
    assert "attachment" in response.headers["content-disposition"]
    assert "campusinbox-20261001-20261031.ics" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert response.content.endswith(b"\r\n")
    assert b"\n" not in response.content.replace(b"\r\n", b"")
    assert all(len(line) <= 75 for line in response.content.split(b"\r\n"))
    for line in response.content.split(b"\r\n"):
        line.decode("utf-8")  # Fold only between Unicode code points.
    parsed = events(response)
    assert len(parsed) == 1
    event = parsed[0]
    assert str(event["SUMMARY"]) == f"数据库原理 · {title}"
    assert description.replace("\r\n", "\n") in str(event["DESCRIPTION"])
    assert str(event["LOCATION"]) == "实验楼,三层;A\\B"
    assert event.decoded("DTSTART") == datetime(2026, 10, 31, 15, 30, tzinfo=UTC)
    assert str(event["UID"]) == f"{original['id']}@campusinbox.local"
    assert "DTEND" not in event and "DURATION" not in event
    assert not event.walk("VALARM") and str(event["TRANSP"]) == "TRANSPARENT"


async def test_export_is_read_only_and_stable_uid_with_updated_sequence(client):
    original = await task(client)
    before = (await client.get(f"/api/tasks/{original['id']}")).json()
    first = events(await export(client))[0]
    assert (await client.get(f"/api/tasks/{original['id']}")).json() == before
    assert str(events(await export(client))[0]["UID"]) == str(first["UID"])
    changed = await client.patch(
        f"/api/tasks/{original['id']}", json=edit(original, due_at="2026-11-02T20:00:00+08:00")
    )
    assert changed.status_code == 200
    assert (await feed(client))["tasks"] == []
    second = events(await export(client, start="2026-11-01", end="2026-12-01"))[0]
    assert str(second["UID"]) == str(first["UID"])
    assert int(second["SEQUENCE"]) > int(first["SEQUENCE"])
    assert second.decoded("DTSTART") == datetime(2026, 11, 2, 12, tzinfo=UTC)


async def test_undated_only_or_empty_scope_is_not_exported_as_invented_event(client):
    await task(client, due_at=None)
    result = await feed(client)
    assert not result["tasks"] and len(result["undated"]) == 1
    response = await client.get("/api/calendar/export.ics", params=PARAMS)
    assert response.status_code == 422


async def test_calendar_updates_after_deadline_and_cancellation_review(client):
    original = await task(client)
    item = (
        await client.post(
            "/api/inbox",
            json={
                "text": "数据库原理：实验四报告延期到下周一晚上八点。",
                "sent_at": "2026-10-31T15:00:00+08:00",
            },
        )
    ).json()
    preview = await client.post(
        f"/api/suggestions/{item['id']}/deadline-preview", json={"task_id": original["id"]}
    )
    assert preview.status_code == 200
    assert (
        await client.post(f"/api/deadline-proposals/{preview.json()['id']}/confirm")
    ).status_code == 200
    assert not (await feed(client))["tasks"]
    assert len((await feed(client, start="2026-11-01", end="2026-12-01"))["tasks"]) == 1
    cancel = (
        await client.post(
            "/api/inbox",
            json={
                "text": "数据库原理：实验四报告取消，不用再提交。",
                "sent_at": "2026-10-31T15:00:00+08:00",
            },
        )
    ).json()
    preview = await client.post(
        f"/api/suggestions/{cancel['id']}/cancellation-preview", json={"task_id": original["id"]}
    )
    assert preview.status_code == 200
    assert (
        await client.post(f"/api/cancellation-proposals/{preview.json()['id']}/confirm")
    ).status_code == 200
    assert not (await feed(client, start="2026-11-01", end="2026-12-01"))["tasks"]
    assert (
        len(events(await export(client, start="2026-11-01", end="2026-12-01", status="cancelled")))
        == 1
    )
