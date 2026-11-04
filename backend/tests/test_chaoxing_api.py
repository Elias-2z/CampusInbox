"""Root API integration without a real learning-platform account."""

import base64
import io
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlmodel import Session, select

from campus_inbox.connectors.platform_status import PlatformObservation
from campus_inbox.main import create_app

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def context(tmp_path):
    app = create_app(tmp_path / "campus.db", qq_background=False, chaoxing_background=False)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8000"
        ) as client:
            yield app, client


async def test_sync_report_excludes_private_runtime_error(context):
    app, client = context
    runtime = app.state.chaoxing
    runtime.error = "private browser/session path"
    response = await client.get("/api/connectors/chaoxing/report")
    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]
    assert response.headers["Cache-Control"] == "no-store"
    assert "private browser" not in response.text
    assert set(response.json()["sources"]) == {"notice", "chat", "task_center"}
    assert (
        await client.get(
            "/api/connectors/chaoxing/report", headers={"Origin": "https://example.org"}
        )
    ).status_code == 403


async def test_browser_companion_is_paired_and_uses_the_main_inbox(context):
    _, client = context
    path = "/api/connectors/chaoxing/browser"
    job = {
        "kind": "course_notice",
        "message_id": "notice_browser",
        "text": "软件工程：请提交报告。",
        "captured_at": "2026-11-03T09:00:00+08:00",
    }
    assert (await client.post(path, json=job)).status_code == 401
    token = (await client.post(path + "/pair")).json()["token"]
    headers = {"Authorization": f"Bearer {token}", "Origin": "chrome-extension://" + "a" * 32}
    for _ in range(2):
        assert (await client.post(path, headers=headers, json=job)).status_code == 200
    assert len((await client.get("/api/inbox")).json()) == 1
    assert (await client.get("/api/tasks")).json() == []
    assert (await client.delete(path + "/pair")).status_code == 200
    assert (await client.post(path, headers=headers, json=job)).status_code == 401


async def test_notice_gap_recheck_is_limited_to_reported_rows(context):
    app, client = context
    runtime = app.state.chaoxing
    runtime.enabled = True
    runtime.state = "partial"
    runtime.stdin = io.StringIO()
    issue = {"key": "a" * 64, "notice_index": 3, "detail_pending": True, "media_pending_count": 2}
    runtime.handle(
        {
            "type": "source",
            "source": "notice",
            "state": "partial",
            "issue_count": 14,
            "issues": [{**issue, "url": "private"}, {**issue, "key": "invalid"}],
        }
    )
    response = await client.get("/api/connectors/chaoxing")
    assert response.json()["sources"]["notice"]["issues"] == [issue]
    assert response.json()["sources"]["notice"]["issue_count"] == 14
    report = await client.get("/api/connectors/chaoxing/report")
    assert report.json()["sources"]["notice"]["issue_count"] == 14
    assert "private" not in report.text
    path = "/api/connectors/chaoxing/notices/recheck"
    assert (await client.post(path, json={"key": "b" * 64})).status_code == 409
    assert (await client.post(path, json={"key": issue["key"]})).status_code == 200
    assert json.loads(runtime.stdin.getvalue()) == {"type": "repair_notice", "key": issue["key"]}
    runtime.handle({"type": "source", "source": "notice", "state": "connected", "issues": []})
    assert (await client.get("/api/connectors/chaoxing")).json()["sources"]["notice"][
        "issue_count"
    ] == 0
    assert (await client.post(path, json={"key": issue["key"]})).status_code == 409


async def test_discovered_course_section_can_be_rechecked(context):
    app, client = context
    runtime = app.state.chaoxing
    runtime.enabled = True
    runtime.state = "connected"
    runtime.stdin = io.StringIO()
    course = {"course_id": "01", "class_id": "2", "course_index": 1, "course": "软件工程"}
    runtime.handle({"type": "task_inventory", "phase": "catalog", "courses": [course]})
    result = {"type": "task_inventory", "phase": "section", "kind": "exam", **course}
    runtime.handle({**result, "complete": False})
    response = await client.post(
        "/api/connectors/chaoxing/sections/recheck", json={"key": "1|2|exam"}
    )
    assert response.status_code == 200, response.text
    assert json.loads(runtime.stdin.getvalue()) == {"type": "repair_sections", "keys": ["1|2|exam"]}
    assert response.json()["task_sections"][1]["state"] == "queued"
    runtime.handle({**result, "complete": True})
    status = (await client.get("/api/connectors/chaoxing")).json()
    assert status["task_sections"][1]["state"] == "checked"
    assert (
        await client.post("/api/connectors/chaoxing/sections/recheck", json={"key": "3|4|exam"})
    ).status_code == 409


async def test_only_observed_task_can_be_rechecked(context):
    app, client = context
    runtime = app.state.chaoxing
    runtime.enabled = True
    runtime.state = "connected"
    runtime.stdin = io.StringIO()
    course = {"course_id": "1", "class_id": "2", "course_index": 1}
    runtime.handle({"type": "task_inventory", "phase": "catalog", "courses": [course]})
    job = {
        "kind": "task_center",
        "title": "实验",
        "url": "https://mooc1.chaoxing.com/mooc2/work/view?courseid=1&classid=2&workid=3",
    }
    runtime.handle(
        {
            "type": "task_inventory",
            "phase": "section",
            "kind": "assignment",
            "jobs": [job],
            **course,
        }
    )
    response = await client.get("/api/connectors/chaoxing/sections/tasks?key=1%7C2%7Cassignment")
    assert response.json() == [{"key": "1|2|assignment|3", "title": "实验", "state": "listed"}]
    response = await client.post(
        "/api/connectors/chaoxing/tasks/recheck", json={"key": "1|2|assignment|3"}
    )
    assert response.status_code == 200
    assert json.loads(runtime.stdin.getvalue()) == {
        "type": "repair_tasks",
        "keys": ["1|2|assignment|3"],
    }
    response = await client.post(
        "/api/connectors/chaoxing/tasks/recheck", json={"key": "1|2|assignment|4"}
    )
    assert response.status_code == 409


async def test_unknown_time_is_visible_for_review_before_entering_root_inbox(context):
    app, client = context
    runtime = app.state.chaoxing
    runtime.enabled = True
    runtime.account_id = "test-account"
    runtime.stdin = io.StringIO()
    runtime.handle(
        {
            "type": "chat",
            "job": {
                "kind": "chat",
                "message_id": "m1",
                "text": "软件工程明天提交实验报告",
                "course": "软件工程",
                "conversation": "软件工程一班",
                "captured_at": None,
            },
        }
    )
    runtime.process_pending()
    pending = (await client.get("/api/connectors/chaoxing/events")).json()
    assert len(pending) == 1 and pending[0]["state"] == "needs_time"
    assert (await client.get("/api/inbox")).json() == []
    result = await client.post(
        f"/api/connectors/chaoxing/events/{pending[0]['id']}/resolve",
        json={"sent_at": "2026-11-03T09:00:00+08:00"},
    )
    assert result.status_code == 200 and result.json()["state"] == "done"
    inbox = (await client.get("/api/inbox")).json()
    assert len(inbox) == 1 and inbox[0]["message"]["text"] == "软件工程明天提交实验报告"
    assert (await client.get("/api/tasks")).json() == []
    assert (
        await client.post(
            f"/api/connectors/chaoxing/events/{pending[0]['id']}/resolve",
            json={"sent_at": "2026-11-03T09:00:00+08:00"},
        )
    ).status_code == 200
    assert len((await client.get("/api/inbox")).json()) == 1


async def test_connector_rejects_cross_origin_and_nonlocal_host(context):
    _, client = context
    result = await client.get(
        "/api/connectors/chaoxing", headers={"Origin": "https://untrusted.example"}
    )
    assert result.status_code == 403
    result = await client.get("/api/connectors/chaoxing", headers={"Host": "untrusted.example"})
    assert result.status_code == 403


@pytest.mark.parametrize("status", ["待批阅", "已收卷"])
async def test_verified_closed_platform_work_is_history_only(context, status):
    app, client = context
    intake = app.state.chaoxing.intake
    job = {
        "kind": "task_center",
        "message_id": "closed-work",
        "title": "实验报告",
        "text": "实验报告；" + status,
        "published_at": "2026-11-01T09:00:00+08:00",
        "platform_status": status,
        "url": "https://mooc1.chaoxing.com/mooc2/work/view?courseid=1&classid=2&workid=3",
    }
    event_id, _ = intake.receive("account", job)
    assert intake.process(event_id) == "done"
    archived = (await client.get("/api/inbox?status=archived")).json()
    assert len(archived) == 1
    item = archived[0]
    assert item["message"]["text"] == job["text"]
    assert item["message"]["source_context"]["closed_task_status"]
    assert (await client.post(f"/api/suggestions/{item['id']}/restore-archive")).status_code == 409
    assert (await client.get("/api/tasks")).json() == []
    changed, _ = intake.receive("account", {**job, "text": "实验报告截止时间改为明天。"})
    assert intake.process(changed) == "needs_review"


async def test_platform_status_requires_exact_identity_and_explicit_current_confirmation(context):
    app, client = context
    job = {
        "kind": "task_center",
        "message_id": "old",
        "text": "实验报告；未提交",
        "title": "实验报告",
        "published_at": "2026-11-01T09:00:00+08:00",
        "captured_at": datetime.now(UTC).isoformat(),
        "platform_status": "未提交",
        "url": "https://mooc1.chaoxing.com/mooc2/work/view?courseid=1&classid=2&workid=3",
        "field_evidence": {"title": "实验报告"},
    }
    event_id, _ = app.state.chaoxing.intake.receive("account-a", job)
    assert app.state.chaoxing.intake.process(event_id) == "done"
    item = (await client.get("/api/inbox?status=pending")).json()[0]
    task = (await client.post(f"/api/suggestions/{item['id']}/accept", json=item["fields"])).json()
    runtime = app.state.chaoxing
    recheck = f"/api/connectors/chaoxing/tasks/{task['id']}/recheck"
    assert (await client.post(recheck)).status_code == 409
    with Session(app.state.engine) as session:
        settings = runtime.settings(session)
        settings.account_id = "account-a"
        session.add(settings)
        session.commit()
    runtime.enabled = True
    runtime.state = "connected"
    runtime.stdin = io.StringIO()
    course = {"course_id": "1", "class_id": "2", "course_index": 1}
    runtime.handle({"type": "task_inventory", "phase": "catalog", "courses": [course]})
    runtime.handle(
        {
            "type": "task_inventory",
            "phase": "section",
            "kind": "assignment",
            "jobs": [job],
            **course,
        }
    )
    assert (await client.post(recheck)).json()["state"] == "queued"
    assert json.loads(runtime.stdin.getvalue())["keys"] == ["1|2|assignment|3"]
    app.state.chaoxing.intake.receive(
        "different-account", {**job, "message_id": "other", "platform_status": "已提交"}
    )
    before = (await client.get(f"/api/tasks/{task['id']}")).json()
    assert before["platform_observation"]["status"] == "未提交"
    newer = {
        **job,
        "message_id": "new",
        "platform_status": "待批阅",
        "text": "实验报告；待批阅",
        "captured_at": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
    }
    app.state.chaoxing.intake.receive("account-a", newer)
    detail = (await client.get(f"/api/tasks/{task['id']}")).json()
    observation = detail["platform_observation"]
    assert observation["status"] == "待批阅" and observation["suggested_status"] == "submitted"
    assert detail["task"]["status"] == "todo"
    body = {"expected_version": task["version"], "observation_id": observation["id"]}
    latest = {
        **newer,
        "message_id": "latest",
        "platform_status": "已批阅",
        "text": "实验报告；已批阅",
        "captured_at": (datetime.now(UTC) + timedelta(seconds=1.5)).isoformat(),
    }
    app.state.chaoxing.intake.receive("account-a", latest)
    assert (
        await client.post(f"/api/tasks/{task['id']}/platform-status", json=body)
    ).status_code == 409
    observation = (await client.get(f"/api/tasks/{task['id']}")).json()["platform_observation"]
    body["observation_id"] = observation["id"]
    applied = await client.post(f"/api/tasks/{task['id']}/platform-status", json=body)
    assert applied.status_code == 200 and applied.json()["status"] == "submitted"
    assert (
        await client.post(f"/api/tasks/{task['id']}/platform-status", json=body)
    ).status_code == 409
    detail = (await client.get(f"/api/tasks/{task['id']}")).json()
    assert any(event["operation"] == "platform_status" for event in detail["events"])
    with Session(app.state.engine) as session:
        row = session.exec(
            select(PlatformObservation).where(PlatformObservation.id == body["observation_id"])
        ).one()
        row.checked_at = datetime.now(UTC) - timedelta(days=2)
        session.add(row)
        session.commit()
    stale = await client.post(
        f"/api/tasks/{task['id']}/platform-status",
        json={
            "expected_version": applied.json()["version"],
            "observation_id": body["observation_id"],
        },
    )
    assert stale.status_code == 409
    returned = {
        **latest,
        "message_id": "returned",
        "platform_status": "退回重做",
        "text": "实验报告；退回重做",
        "captured_at": datetime.now(UTC).isoformat(),
    }
    assert runtime.intake.receive("account-a", returned) == (event_id, False)
    assert (await client.get("/api/inbox?status=pending")).json() == []
    detail = (await client.get(f"/api/tasks/{task['id']}")).json()
    assert detail["task"]["status"] == "submitted"
    observation = detail["platform_observation"]
    assert observation["status"] == "退回重做" and observation["suggested_status"] == "todo"
    response = await client.post(
        f"/api/tasks/{task['id']}/platform-status",
        json={"expected_version": detail["task"]["version"], "observation_id": observation["id"]},
    )
    assert response.status_code == 200 and response.json()["status"] == "todo"
    detail = (await client.get(f"/api/tasks/{task['id']}")).json()
    assert any(
        event["operation"] == "platform_status"
        and event["after"]["platform_observation"]["status"] == "退回重做"
        for event in detail["events"]
    )
    updated_id, created = runtime.intake.receive(
        "account-a", {**returned, "text": "请提交实验报告并附源代码。"}
    )
    assert created and updated_id != event_id


async def test_platform_status_is_not_assigned_when_two_local_tasks_share_platform_identity(
    context,
):
    app, client = context
    base = {
        "kind": "task_center",
        "text": "请提交实验报告",
        "title": "实验报告",
        "published_at": "2026-11-01T09:00:00+08:00",
        "captured_at": datetime.now(UTC).isoformat(),
        "platform_status": "待批阅",
        "url": "https://mooc1.chaoxing.com/mooc2/work/view?courseid=1&classid=2&workid=3",
    }
    # Explicitly create two local tasks from two captured versions of one platform item.
    # Already-submitted captures are held for review, so use unsubmitted first.
    ids = []
    for number in (1, 2):
        event_id, _ = app.state.chaoxing.intake.receive(
            "account",
            {
                **base,
                "message_id": f"version-{number}",
                "text": f"请提交实验报告，使用第 {number} 版模板。",
                "platform_status": "未提交",
            },
        )
        assert app.state.chaoxing.intake.process(event_id) == "done"
        items = (await client.get("/api/inbox?status=pending")).json()
        item = next(
            item
            for item in items
            if item["message"]["source_context"]["message_id"] == f"version-{number}"
        )
        task = (
            await client.post(
                f"/api/suggestions/{item['id']}/accept",
                json={**item["fields"], "allow_duplicate": True},
            )
        ).json()
        ids.append(task["id"])
    app.state.chaoxing.intake.receive(
        "account",
        {
            **base,
            "message_id": "submitted",
            "captured_at": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
        },
    )
    for task_id in ids:
        assert (await client.get(f"/api/tasks/{task_id}")).json()["platform_observation"] is None


async def test_actual_publication_time_requires_timezone(context):
    app, client = context
    event_id, _ = app.state.chaoxing.intake.receive(
        "test", {"kind": "chat", "message_id": "m1", "text": "明天提交作业", "captured_at": None}
    )
    result = await client.post(
        f"/api/connectors/chaoxing/events/{event_id}/resolve",
        json={"sent_at": "2026-11-03T09:00:00"},
    )
    assert result.status_code == 422
    assert (await client.get("/api/inbox")).json() == []


async def test_archived_suggestion_can_be_restored_without_losing_message(context):
    app, client = context
    event_id, _ = app.state.chaoxing.intake.receive(
        "test",
        {
            "kind": "task_center",
            "message_id": "old",
            "text": "提交实验报告",
            "published_at": "2000-01-01T09:00:00+08:00",
            "deadline": "2000-01-10",
            "field_evidence": {"deadline": "截止 2000-01-10"},
        },
    )
    assert app.state.chaoxing.intake.process(event_id) == "done"
    items = (await client.get("/api/inbox?status=archived")).json()
    assert len(items) == 1 and items[0]["message"]["text"] == "提交实验报告"
    assert (await client.get("/api/inbox?status=pending")).json() == []
    restored = await client.post(f"/api/suggestions/{items[0]['id']}/restore-archive")
    assert restored.status_code == 200 and restored.json()["status"] == "pending"
    assert len((await client.get("/api/inbox?status=pending")).json()) == 1


async def test_unprocessed_attachment_can_be_downloaded_without_exposing_its_bytes_in_list(context):
    app, client = context
    event_id, _ = app.state.chaoxing.intake.receive(
        "test",
        {
            "kind": "course_notice",
            "message_id": "attachment-only",
            "text": "",
            "captured_at": None,
            "attachments": [
                {"name": "作业说明.txt", "content_base64": base64.b64encode(b"demo").decode()},
                {"name": "缺失.pdf", "status": "failed"},
            ],
        },
    )
    assert app.state.chaoxing.intake.process(event_id) == "needs_time"
    events = (await client.get("/api/connectors/chaoxing/events")).json()
    assert events[0]["attachments"] == [
        {"name": "作业说明.txt", "available": True, "index": 0, "fetchable": False},
        {"name": "缺失.pdf", "available": False, "index": 1, "fetchable": False},
    ]
    assert "content_base64" not in str(events)
    path = f"/api/connectors/chaoxing/events/{event_id}/attachments/0"
    response = await client.get(path)
    assert response.content == b"demo"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert (
        await client.get(path, headers={"Origin": "https://untrusted.example"})
    ).status_code == 403
    assert (
        await client.get(f"/api/connectors/chaoxing/events/{event_id}/attachments/1")
    ).status_code == 404
