"""Explicit source association and selected field merging must be atomic and reversible."""

import asyncio

import httpx
import pytest

from campus_inbox.main import create_app
from campus_inbox.schemas import TaskFields

pytestmark = pytest.mark.anyio
TEXT = "软件工程：下周三下午六点前提交需求分析报告，上传至学习通，文件命名为学号+姓名。"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(tmp_path):
    app = create_app(tmp_path / "merges.db")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


async def task(client, **changes):
    response = await client.post(
        "/api/tasks",
        json={
            "title": "需求分析报告",
            "course": "软件工程",
            "kind": "homework",
            "due_at": "2026-11-02T20:00:00+08:00",
            "description": "用户整理的原要求",
            **changes,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def notice(client, text=TEXT):
    response = await client.post(
        "/api/inbox",
        json={"text": text, "source": "补充通知群", "sent_at": "2026-10-30T15:00:00+08:00"},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def preview(client, item, original, fields=(), split=False):
    response = await client.post(
        f"/api/{'split-drafts' if split else 'suggestions'}/{item['id']}/merge-preview",
        json={"task_id": original["id"], "fields": list(fields)},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def confirm(client, proposal):
    response = await client.post(f"/api/merge-proposals/{proposal['id']}/confirm")
    assert response.status_code == 200, response.text
    return response.json()


def edit_data(original, **changes):
    return {
        **{k: v for k, v in original.items() if k in TaskFields.model_fields},
        "expected_version": original["version"],
        "status": original["status"],
        **changes,
    }


async def current(client, item):
    return next(
        i for i in (await client.get("/api/inbox?status=all")).json() if i["id"] == item["id"]
    )


async def test_link_only_preserves_all_business_fields_and_links_sources(client):
    original = await task(client)
    item = await notice(client)
    proposal = await preview(client, item, original)
    assert proposal["after_fields"] == {} and proposal["before_fields"] == {}
    assert any("截止时间存在冲突" in warning for warning in proposal["warnings"])
    assert (await client.get(f"/api/tasks/{original['id']}")).json()["task"] == original
    applied = await confirm(client, proposal)
    assert applied["can_undo"]
    for key in set(original) - {"version", "updated_at"}:
        assert applied["task"][key] == original[key]
    assert len((await client.get("/api/tasks")).json()) == 1
    detail = (await client.get(f"/api/tasks/{original['id']}")).json()
    assert len(detail["sources"]) == 2 and detail["sources"][1]["id"] == item["message"]["id"]
    assert detail["events"][-1]["operation"] == "merge"
    assert detail["events"][-1]["message_id"] == item["message"]["id"]
    assert detail["merge_changes"][0]["id"] == applied["id"]
    settled = await current(client, item)
    assert settled["status"] == "accepted" and settled["task_id"] == original["id"]
    assert settled["merge_change"]["id"] == applied["id"]
    assert (
        await client.post(f"/api/suggestions/{item['id']}/accept", json={"title": "不得重复创建"})
    ).status_code == 409


async def test_only_checked_fields_are_applied_and_undo_restores_evidence(client):
    original = await task(client)
    before = (await client.get(f"/api/tasks/{original['id']}")).json()
    item = await notice(client)
    proposal = await preview(client, item, original, ["submission_platform", "file_requirements"])
    assert set(proposal["after_fields"]) == {"submission_platform", "file_requirements"}
    assert proposal["before_fields"] == {"submission_platform": "", "file_requirements": ""}
    applied = await confirm(client, proposal)
    assert applied["task"]["submission_platform"] == "学习通"
    assert applied["task"]["file_requirements"] == "学号+姓名"
    assert applied["task"]["due_at"] == original["due_at"]
    assert applied["task"]["description"] == original["description"]
    detail = (await client.get(f"/api/tasks/{original['id']}")).json()
    assert detail["evidence"]["submission_platform"]["message_id"] == item["message"]["id"]
    undone = await client.post(f"/api/merge-changes/{applied['id']}/undo")
    assert undone.status_code == 200
    for key in set(original) - {"version", "updated_at"}:
        assert undone.json()["task"][key] == original[key]
    detail = (await client.get(f"/api/tasks/{original['id']}")).json()
    assert detail["evidence"] == before["evidence"]
    assert len(detail["sources"]) == 2 and detail["events"][-1]["operation"] == "merge_undo"


async def test_explicit_deadline_replacement_and_undo_preserve_other_fields(client):
    original = await task(client)
    item = await notice(client)
    proposal = await preview(client, item, original, ["due_at"])
    assert set(proposal["after_fields"]) == {"due_at"}
    changed = await confirm(client, proposal)
    assert changed["task"]["due_at"] == "2026-11-04T10:00:00Z"
    assert changed["task"]["description"] == original["description"]
    undone = await client.post(f"/api/merge-changes/{changed['id']}/undo")
    assert undone.json()["task"]["due_at"] == original["due_at"]


async def test_same_instant_and_same_fields_are_not_treated_as_changes(client):
    original = await task(client, due_at="2026-11-04T18:00:00+08:00")
    item = await notice(client)
    proposal = await preview(client, item, original, ["title", "course", "due_at", "due_at"])
    assert proposal["after_fields"] == {}
    assert not any("截止时间存在冲突" in w for w in proposal["warnings"])


async def test_parallel_confirm_and_undo_are_idempotent_and_replay_does_not_reapply(client):
    original = await task(client)
    item = await notice(client)
    proposal = await preview(client, item, original, ["submission_platform"])
    responses = await asyncio.gather(
        *[client.post(f"/api/merge-proposals/{proposal['id']}/confirm") for _ in range(2)]
    )
    assert [r.status_code for r in responses] == [200, 200]
    change = responses[0].json()
    assert responses[1].json()["id"] == change["id"]
    responses = await asyncio.gather(
        *[client.post(f"/api/merge-changes/{change['id']}/undo") for _ in range(2)]
    )
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json()["undo_event_id"] == responses[1].json()["undo_event_id"]
    assert responses[0].json()["task"]["version"] == 3
    replay = await confirm(client, proposal)
    assert replay["reverted"] and replay["task"]["submission_platform"] == ""
    detail = (await client.get(f"/api/tasks/{original['id']}")).json()
    assert len(detail["events"]) == 3


@pytest.mark.parametrize(
    "operation", ["edit", "complete", "archive", "reanalyze", "ignore-reopen", "create"]
)
async def test_stale_task_or_notice_preview_rejected(client, operation):
    original = await task(client)
    item = await notice(client)
    proposal = await preview(client, item, original, ["submission_platform"])
    if operation in {"edit", "complete", "archive"}:
        updates = (
            {"remarks": "后来编辑"}
            if operation == "edit"
            else {"status": "completed" if operation == "complete" else "archived"}
        )
        assert (
            await client.patch(f"/api/tasks/{original['id']}", json=edit_data(original, **updates))
        ).status_code == 200
    elif operation == "reanalyze":
        await client.post(f"/api/inbox/{item['id']}/analyze")
    elif operation == "create":
        assert (
            await client.post(
                f"/api/suggestions/{item['id']}/accept",
                json={**item["fields"], "allow_duplicate": True},
            )
        ).status_code == 200
    else:
        await client.post(f"/api/suggestions/{item['id']}/ignore")
        await client.post(f"/api/suggestions/{item['id']}/reopen")
    assert (await client.post(f"/api/merge-proposals/{proposal['id']}/confirm")).status_code == 409


async def test_same_notice_cannot_be_applied_to_two_tasks(client):
    a, b = await task(client), await task(client, title="需求分析报告附录")
    item = await notice(client)
    pa, pb = await preview(client, item, a), await preview(client, item, b)
    await confirm(client, pa)
    assert (await client.post(f"/api/merge-proposals/{pb['id']}/confirm")).status_code == 409
    assert (await client.get(f"/api/tasks/{b['id']}")).json()["task"] == b


@pytest.mark.parametrize("status", ["completed", "cancelled", "archived"])
async def test_inactive_target_rejected(client, status):
    original = await task(client)
    await client.patch(f"/api/tasks/{original['id']}", json=edit_data(original, status=status))
    item = await notice(client)
    assert (
        await client.post(
            f"/api/suggestions/{item['id']}/merge-preview", json={"task_id": original["id"]}
        )
    ).status_code == 409


@pytest.mark.parametrize(
    "text",
    [
        "软件工程：需求分析报告取消，不用再提交。",
        "软件工程：需求分析报告延期到下周三18:00。",
        "1. 提交需求分析报告。\n2. 提交原型设计。",
        "今天天气很好。",
    ],
)
async def test_other_notice_types_cannot_be_forced_into_merge(client, text):
    original = await task(client)
    item = await notice(client, text)
    assert (
        await client.post(
            f"/api/suggestions/{item['id']}/merge-preview", json={"task_id": original["id"]}
        )
    ).status_code in {409, 422}


@pytest.mark.parametrize("fields", [["status"], ["version"], ["id"], ["made_up"]])
async def test_only_allowed_fields_can_be_requested(client, fields):
    original = await task(client)
    item = await notice(client)
    assert (
        await client.post(
            f"/api/suggestions/{item['id']}/merge-preview",
            json={"task_id": original["id"], "fields": fields},
        )
    ).status_code == 422


async def test_empty_notice_fields_cannot_clear_existing_values(client):
    original = await task(client, location="教学楼")
    item = await notice(client)
    assert (
        await client.post(
            f"/api/suggestions/{item['id']}/merge-preview",
            json={"task_id": original["id"], "fields": ["location"]},
        )
    ).status_code == 422


async def test_later_edit_blocks_undo_and_restart_keeps_change(client, tmp_path):
    original = await task(client)
    item = await notice(client)
    change = await confirm(client, await preview(client, item, original, ["submission_platform"]))
    app = create_app(tmp_path / "merges.db")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as fresh:
            detail = (await fresh.get(f"/api/tasks/{original['id']}")).json()
            assert detail["merge_changes"][0]["can_undo"]
            assert (
                await fresh.patch(
                    f"/api/tasks/{original['id']}",
                    json=edit_data(change["task"], remarks="保留后续操作"),
                )
            ).status_code == 200
            assert (await fresh.post(f"/api/merge-changes/{change['id']}/undo")).status_code == 409
            assert not (await fresh.get(f"/api/tasks/{original['id']}")).json()["merge_changes"][0][
                "can_undo"
            ]


async def test_restart_undo_restores_values_and_original_provenance(client, tmp_path):
    original = await task(client, submission_platform="课程群")
    item = await notice(client)
    change = await confirm(client, await preview(client, item, original, ["submission_platform"]))
    app = create_app(tmp_path / "merges.db")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as fresh:
            response = await fresh.post(f"/api/merge-changes/{change['id']}/undo")
            assert (
                response.status_code == 200
                and response.json()["task"]["submission_platform"] == "课程群"
            )
            detail = (await fresh.get(f"/api/tasks/{original['id']}")).json()
            assert detail["evidence"]["submission_platform"]["method"] == "user"


async def split(client, text):
    item = await notice(client, text)
    response = await client.post(f"/api/suggestions/{item['id']}/split")
    assert response.status_code == 200, response.text
    return response.json()


async def test_split_item_association_then_other_creation_preserves_independent_provenance(client):
    original = await task(client)
    item = await split(
        client,
        "软件工程：\n1. 下周三18:00前提交需求分析报告，上传至学习通。\n"
        "2. 下周五20:00前提交原型设计。",
    )
    first, second = item["split_items"]
    change = await confirm(
        client, await preview(client, first, original, ["submission_platform"], split=True)
    )
    partial = await current(client, item)
    assert partial["status"] == "pending"
    assert partial["split_items"][0]["status"] == "accepted"
    assert partial["split_items"][0]["task_id"] == original["id"]
    assert partial["split_items"][0]["merge_change"]["id"] == change["id"]
    response = await client.post(
        f"/api/split-drafts/{second['id']}/accept",
        json={**second["fields"], "expected_updated_at": second["updated_at"]},
    )
    assert response.status_code == 200, response.text
    other = response.json()
    final = await current(client, item)
    assert final["status"] == "accepted"
    assert len((await client.get("/api/tasks")).json()) == 2
    existing_detail = (await client.get(f"/api/tasks/{original['id']}")).json()
    assert existing_detail["evidence"]["submission_platform"]["message_id"] == item["message"]["id"]
    assert existing_detail["evidence"]["description"]["method"] == "user"
    other_detail = (await client.get(f"/api/tasks/{other['id']}")).json()
    assert other_detail["evidence"]["title"]["quote"] == "原型设计"
    assert not other_detail["merge_changes"]
    # Old create requests replay the linked task instead of creating a second task, even after undo.
    await client.post(f"/api/merge-changes/{change['id']}/undo")
    replay = await client.post(
        f"/api/split-drafts/{first['id']}/accept",
        json={**first["fields"], "expected_updated_at": first["updated_at"]},
    )
    assert replay.status_code == 200 and replay.json()["id"] == original["id"]
    assert len((await client.get("/api/tasks")).json()) == 2


async def test_split_draft_edit_invalidates_preview(client):
    original = await task(client)
    item = await split(client, "1. 周五20:00前提交需求分析报告。\n2. 周六20:00前提交原型设计。")
    draft = item["split_items"][0]
    proposal = await preview(client, draft, original, split=True)
    response = await client.patch(
        f"/api/split-drafts/{draft['id']}",
        json={**draft["fields"], "title": "新标题", "expected_updated_at": draft["updated_at"]},
    )
    assert response.status_code == 200
    assert (await client.post(f"/api/merge-proposals/{proposal['id']}/confirm")).status_code == 409


async def test_two_split_items_can_link_same_task_without_duplicating_original_source(client):
    original = await task(client)
    item = await split(client, "1. 周五20:00前提交需求分析报告。\n2. 上传需求分析报告至学习通。")
    for draft in item["split_items"]:
        fresh = (await client.get(f"/api/tasks/{original['id']}")).json()["task"]
        await confirm(client, await preview(client, draft, fresh, split=True))
    assert (await current(client, item))["status"] == "accepted"
    assert len((await client.get("/api/tasks")).json()) == 1
    detail = (await client.get(f"/api/tasks/{original['id']}")).json()
    assert len(detail["sources"]) == 2 and len(detail["merge_changes"]) == 2
    assert not detail["merge_changes"][0]["can_undo"] or not detail["merge_changes"][1]["can_undo"]


async def test_deadline_undo_restores_merge_provenance_and_old_merge_undo_stays_blocked(client):
    original = await task(client)
    item = await notice(client)
    merge = await confirm(client, await preview(client, item, original, ["due_at"]))
    delay = await notice(client, "软件工程：需求分析报告延期到下周五晚上八点。")
    dp = await client.post(
        f"/api/suggestions/{delay['id']}/deadline-preview", json={"task_id": original["id"]}
    )
    assert dp.status_code == 200
    assert (
        await client.post(f"/api/deadline-proposals/{dp.json()['id']}/confirm")
    ).status_code == 200
    assert (await client.post(f"/api/deadline-changes/{delay['id']}/undo")).status_code == 200
    detail = (await client.get(f"/api/tasks/{original['id']}")).json()
    assert detail["task"]["due_at"] == merge["task"]["due_at"]
    assert detail["evidence"]["due_at"]["message_id"] == item["message"]["id"]
    assert (await client.post(f"/api/merge-changes/{merge['id']}/undo")).status_code == 409


async def test_existing_split_task_retains_its_creation_evidence_after_another_link(client):
    parent = await split(
        client, "软件工程：\n1. 周一20:00前提交需求分析报告。\n2. 周三18:00前提交原型设计。"
    )
    original_draft = parent["split_items"][0]
    created = await client.post(
        f"/api/split-drafts/{original_draft['id']}/accept",
        json={**original_draft["fields"], "expected_updated_at": original_draft["updated_at"]},
    )
    assert created.status_code == 200
    original = created.json()
    other = await split(
        client, "软件工程：\n1. 提交需求分析报告，上传至学习通。\n2. 下周五20:00前提交测试报告。"
    )
    await confirm(
        client,
        await preview(
            client, other["split_items"][0], original, ["submission_platform"], split=True
        ),
    )
    detail = (await client.get(f"/api/tasks/{original['id']}")).json()
    assert detail["evidence"]["title"]["quote"] == original_draft["evidence"]["title"]["quote"]
    assert detail["evidence"]["due_at"] == original_draft["evidence"]["due_at"]
    assert detail["evidence"]["submission_platform"]["message_id"] == other["message"]["id"]
    assert detail["source"]["id"] == parent["message"]["id"]
    assert len(detail["sources"]) == 2


async def test_merge_tables_added_to_existing_database_preserve_task_and_settings(client, tmp_path):
    import sqlite3

    original = await task(client)
    before = (await client.get(f"/api/tasks/{original['id']}")).json()
    settings = (await client.get("/api/settings/model")).json()
    with sqlite3.connect(tmp_path / "merges.db") as connection:
        connection.execute("DROP TABLE mergechange")
        connection.execute("DROP TABLE mergeproposal")
    app = create_app(tmp_path / "merges.db")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as fresh:
            assert (await fresh.get(f"/api/tasks/{original['id']}")).json() == before
            assert (await fresh.get("/api/settings/model")).json() == settings
            item = await notice(fresh)
            change = await confirm(fresh, await preview(fresh, item, original))
            assert change["task"]["id"] == original["id"]
