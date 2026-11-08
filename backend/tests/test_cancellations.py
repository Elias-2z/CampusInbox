"""Cancellation must remain explicit, auditable and safe across retries and edits."""

import asyncio

import httpx
import pytest

from campus_inbox.extraction import cancellation_supported
from campus_inbox.main import create_app
from campus_inbox.schemas import TaskFields

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(tmp_path):
    app = create_app(tmp_path / "cancellations.db")
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
            "kind": "homework",
            "due_at": "2026-10-30T23:59:00+08:00",
            "file_requirements": "学号+姓名",
            **changes,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def notice(client, text="数据库原理：实验四报告取消，不用再提交。"):
    response = await client.post(
        "/api/inbox",
        json={
            "text": text,
            "sent_at": "2026-10-29T14:00:00+08:00",
            "source": "课程群",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def edit_data(original, **changes):
    return {
        **{k: v for k, v in original.items() if k in TaskFields.model_fields},
        "expected_version": original["version"],
        "status": original["status"],
        **changes,
    }


async def preview(client, item, original):
    response = await client.post(
        f"/api/suggestions/{item['id']}/cancellation-preview", json={"task_id": original["id"]}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def confirm(client, proposal):
    response = await client.post(f"/api/cancellation-proposals/{proposal['id']}/confirm")
    assert response.status_code == 200, response.text
    return response.json()


async def test_preview_confirm_preserves_fields_and_links_source(client):
    original = await task(client)
    item = await notice(client)
    assert item["action"] == "review" and item["cancellation_supported"]
    assert item["fields"]["title"] == "实验四报告"
    assert item["matched_tasks"][0]["id"] == original["id"]
    proposal = await preview(client, item, original)
    detail = (await client.get(f"/api/tasks/{original['id']}")).json()
    assert detail["task"] == original
    assert len(detail["sources"]) == 1
    result = await confirm(client, proposal)
    changed = result["task"]
    assert changed["status"] == "cancelled" and changed["version"] == 2
    for key in set(original) - {"status", "version", "updated_at"}:
        assert original[key] == changed[key]
    assert len((await client.get("/api/tasks")).json()) == 1
    detail = (await client.get(f"/api/tasks/{original['id']}")).json()
    assert detail["sources"][1]["id"] == item["message"]["id"]
    assert detail["events"][-1]["operation"] == "cancel"
    assert detail["events"][-1]["message_id"] == item["message"]["id"]
    assert detail["cancellation_changes"][0]["can_undo"]
    settled = next(
        i for i in (await client.get("/api/inbox?status=all")).json() if i["id"] == item["id"]
    )
    assert settled["task_id"] == original["id"] and settled["status"] == "accepted"
    assert (
        await client.post(f"/api/suggestions/{item['id']}/accept", json={"title": "错误新任务"})
    ).status_code == 409


async def test_concurrent_confirm_undo_and_replay_are_idempotent(client):
    original = await task(client)
    edited = await client.patch(
        f"/api/tasks/{original['id']}", json=edit_data(original, status="in_progress")
    )
    original = edited.json()
    item = await notice(client)
    proposal = await preview(client, item, original)
    results = await asyncio.gather(
        *[client.post(f"/api/cancellation-proposals/{proposal['id']}/confirm") for _ in range(2)]
    )
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json()["event_id"] == results[1].json()["event_id"]
    results = await asyncio.gather(
        *[client.post(f"/api/cancellation-changes/{item['id']}/undo") for _ in range(2)]
    )
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json()["task"]["status"] == "in_progress"
    assert results[0].json()["task"]["version"] == 4
    assert results[0].json()["undo_event_id"] == results[1].json()["undo_event_id"]
    assert (await confirm(client, proposal))["reverted"]
    detail = (await client.get(f"/api/tasks/{original['id']}")).json()
    assert len(detail["sources"]) == 2 and len(detail["events"]) == 4


@pytest.mark.parametrize("operation", ["edit", "complete", "archive", "reanalyze", "ignore-reopen"])
async def test_stale_preview_rejected(client, operation):
    original = await task(client)
    item = await notice(client)
    proposal = await preview(client, item, original)
    if operation in {"edit", "complete", "archive"}:
        changes = (
            {"title": "编辑过的报告"}
            if operation == "edit"
            else {"status": "completed" if operation == "complete" else "archived"}
        )
        assert (
            await client.patch(f"/api/tasks/{original['id']}", json=edit_data(original, **changes))
        ).status_code == 200
    elif operation == "reanalyze":
        await client.post(f"/api/inbox/{item['id']}/analyze")
    else:
        await client.post(f"/api/suggestions/{item['id']}/ignore")
        await client.post(f"/api/suggestions/{item['id']}/reopen")
    assert (
        await client.post(f"/api/cancellation-proposals/{proposal['id']}/confirm")
    ).status_code == 409


async def test_undo_blocked_by_later_edit_and_persists_after_restart(client, tmp_path):
    original = await task(client)
    item = await notice(client)
    applied = await confirm(client, await preview(client, item, original))
    app = create_app(tmp_path / "cancellations.db")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as fresh:
            detail = (await fresh.get(f"/api/tasks/{original['id']}")).json()
            assert detail["cancellation_changes"][0]["can_undo"]
            await fresh.patch(
                f"/api/tasks/{original['id']}", json=edit_data(applied["task"], remarks="后续修改")
            )
            assert (
                await fresh.post(f"/api/cancellation-changes/{item['id']}/undo")
            ).status_code == 409
            assert not (await fresh.get(f"/api/tasks/{original['id']}")).json()[
                "cancellation_changes"
            ][0]["can_undo"]


@pytest.mark.parametrize("status", ["completed", "cancelled", "archived"])
async def test_inactive_target_rejected(client, status):
    original = await task(client)
    await client.patch(f"/api/tasks/{original['id']}", json=edit_data(original, status=status))
    item = await notice(client)
    assert (
        await client.post(
            f"/api/suggestions/{item['id']}/cancellation-preview", json={"task_id": original["id"]}
        )
    ).status_code == 409


async def test_same_notice_cannot_cancel_two_tasks_or_skip_selection(client):
    first = await task(client)
    second = await task(client, course="软件工程")
    item = await notice(client, "实验四报告取消，不用再提交。")
    # With no course in the notice, neither course may be recommended by title alone.
    assert item["matched_tasks"] == []
    assert (
        await client.post(f"/api/suggestions/{item['id']}/cancellation-preview", json={})
    ).status_code == 422
    a, b = await preview(client, item, first), await preview(client, item, second)
    await confirm(client, a)
    assert (await client.post(f"/api/cancellation-proposals/{b['id']}/confirm")).status_code == 409
    assert (await client.get(f"/api/tasks/{second['id']}")).json()["task"] == second


async def test_deadline_preview_invalidated_by_cancel_and_old_undo_stays_blocked(client):
    original = await task(client)
    delay = await notice(client, "数据库原理：实验四报告延期到下周一晚上八点。")
    dp = (
        await client.post(
            f"/api/suggestions/{delay['id']}/deadline-preview", json={"task_id": original["id"]}
        )
    ).json()
    item = await notice(client)
    cp = await preview(client, item, original)
    await confirm(client, cp)
    assert (await client.post(f"/api/deadline-proposals/{dp['id']}/confirm")).status_code == 409
    await client.post(f"/api/cancellation-changes/{item['id']}/undo")
    assert (await client.post(f"/api/deadline-proposals/{dp['id']}/confirm")).status_code == 409


@pytest.mark.parametrize(
    "text",
    [
        "数据库原理：如果实验四报告取消，就不用提交。",
        "数据库原理：实验四报告可能取消。",
        "数据库原理：建议取消实验四报告。",
        "数据库原理：实验四报告还没取消。",
        "数据库原理：没有说取消实验四报告。",
        "数据库原理：听说实验四报告取消。",
        "数据库原理：实验四报告是否取消？",
        "数据库原理：实验四报告取消了吗",
        "数据库原理：实验四报告未取消，照常提交。",
        "数据库原理：不要取消实验四报告。",
        "数据库原理：实验四报告延期取消，周五照常提交。",
        "数据库原理：实验四报告取消上传限制。",
        "数据库原理：实验四报告无需提交纸质版。",
        "数据库原理：取消实验四报告的纸质提交要求。",
        "数据库原理：实验四报告取消，实验五报告延期到明天20:00。",
        "数据库原理：实验四报告和实验五报告取消。",
        "1. 实验四报告取消。\n2. 实验五报告取消。",
        "数据库原理：实验四报告取消，实验五报告周五20:00提交。",
        "数据库原理：实验四报告无需提交到学习通。",
        "数据库原理：实验四报告没有作废。",
        "数据库原理：实验四报告取消后重新提交。",
    ],
)
async def test_ambiguous_or_partial_cancellation_cannot_be_forced(client, text):
    original = await task(client)
    item = await notice(client, text)
    assert not item["cancellation_supported"], text
    response = await client.post(
        f"/api/suggestions/{item['id']}/cancellation-preview", json={"task_id": original["id"]}
    )
    assert response.status_code in {409, 422}


@pytest.mark.parametrize(
    "text",
    [
        "数据库原理：实验四报告取消，不用再提交。",
        "数据库原理：取消实验四报告。",
        "数据库原理：实验四报告无需提交。",
        "数据库原理：实验四报告作废。",
    ],
)
async def test_explicit_cancellation_recognized_without_llm(client, text):
    item = await notice(client, text)
    assert item["cancellation_supported"]
    assert item["fields"]["title"] == "实验四报告"
    assert item["method"] == "rule"
    assert not any("模型" in w or "截止时间" in w for w in item["warnings"])


def test_negated_void_is_not_cancellation():
    assert not cancellation_supported("数据库原理：实验四报告没有作废。")


async def test_restart_undo_restores_exact_status_and_confirm_replay_stays_undone(client, tmp_path):
    original = await task(client)
    item = await notice(client)
    proposal = await preview(client, item, original)
    await confirm(client, proposal)
    app = create_app(tmp_path / "cancellations.db")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as fresh:
            response = await fresh.post(f"/api/cancellation-changes/{item['id']}/undo")
            assert response.status_code == 200
            assert response.json()["task"]["status"] == original["status"]
            assert response.json()["reverted"]
            replay = await confirm(fresh, proposal)
            assert replay["reverted"] and replay["task"]["version"] == 3
