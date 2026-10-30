"""One immutable notification, independently reviewed task drafts and safe schema upgrade."""

import asyncio
import sqlite3

import httpx
import pytest
from sqlmodel import Session, select

from campus_inbox.database import initialize, make_engine
from campus_inbox.main import create_app
from campus_inbox.models import ModelSettings, Suggestion
from campus_inbox.schemas import TaskFields

pytestmark = pytest.mark.anyio
TEXT = (
    "软件工程：\n1. 下周一晚上八点前提交需求分析报告。\n"
    "2. 下周三下午六点前提交原型设计，上传至学习通。"
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(tmp_path):
    app = create_app(tmp_path / "split.db")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


async def notice(client, text=TEXT):
    response = await client.post(
        "/api/inbox",
        json={"text": text, "source": "软件工程课程群", "sent_at": "2026-10-29T14:00:00+08:00"},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def split(client, item):
    response = await client.post(f"/api/suggestions/{item['id']}/split")
    assert response.status_code == 200, response.text
    return response.json()


def data(draft, **changes):
    return {
        **{k: v for k, v in draft["fields"].items() if k in TaskFields.model_fields},
        "expected_updated_at": draft["updated_at"],
        **changes,
    }


async def accept(client, draft, **changes):
    response = await client.post(
        f"/api/split-drafts/{draft['id']}/accept", json=data(draft, **changes)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def decision(client, draft, operation="ignore"):
    response = await client.post(
        f"/api/split-drafts/{draft['id']}/{operation}",
        json={"expected_updated_at": draft["updated_at"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def current(client, item):
    return next(
        i for i in (await client.get("/api/inbox?status=all")).json() if i["id"] == item["id"]
    )


async def test_split_preview_has_no_tasks_and_scoped_fields(client):
    item = await notice(client)
    assert item["split_supported"] and item["action"] == "review"
    assert item["split_items"] == []
    assert not any("模型" in warning for warning in item["warnings"])
    item = await split(client, item)
    first, second = item["split_items"]
    assert first["fields"]["title"] == "需求分析报告"
    assert first["fields"]["due_at"] == "2026-11-02T12:00:00+00:00"
    assert first["fields"]["course"] == "软件工程"
    assert first["fields"]["submission_platform"] == ""
    assert second["fields"]["title"] == "原型设计"
    assert second["fields"]["due_at"] == "2026-11-04T10:00:00+00:00"
    assert second["fields"]["submission_platform"] == "学习通"
    assert first["quote"] in TEXT and second["quote"] in TEXT
    assert first["evidence"]["course"]["quote"] in TEXT
    assert (await client.get("/api/tasks")).json() == []
    assert (await split(client, item))["split_items"] == item["split_items"]


async def test_confirm_items_independently_share_original_source_and_history(client):
    item = await split(client, await notice(client))
    first, second = item["split_items"]
    a = await accept(client, first, title="审核后的需求分析报告")
    partial = await current(client, item)
    assert partial["status"] == "pending"
    assert partial["split_items"][0]["status"] == "accepted"
    assert partial["split_items"][1]["status"] == "pending"
    b = await accept(client, second)
    final = await current(client, item)
    assert final["status"] == "accepted"
    assert len((await client.get("/api/tasks")).json()) == 2
    for task in (a, b):
        assert task["message_id"] == item["message"]["id"]
        assert task["suggestion_id"] == item["id"]
        detail = (await client.get(f"/api/tasks/{task['id']}")).json()
        assert len(detail["sources"]) == 1 and detail["source"]["text"] == TEXT
        assert detail["events"][0]["operation"] == "create"
    detail = (await client.get(f"/api/tasks/{a['id']}")).json()
    assert detail["evidence"]["title"]["method"] == "user"
    assert detail["evidence"]["due_at"]["quote"] in first["quote"]
    repeated = await notice(client)
    assert repeated["id"] == item["id"] and repeated["split_items"] == final["split_items"]
    assert (
        await client.post(f"/api/suggestions/{item['id']}/accept", json={"title": "错误整条确认"})
    ).status_code == 409


async def test_parallel_generation_and_acceptance_are_idempotent(client):
    item = await notice(client)
    responses = await asyncio.gather(
        *[client.post(f"/api/suggestions/{item['id']}/split") for _ in range(2)]
    )
    assert [r.status_code for r in responses] == [200, 200]
    assert [d["id"] for d in responses[0].json()["split_items"]] == [
        d["id"] for d in responses[1].json()["split_items"]
    ]
    a, b = responses[0].json()["split_items"]
    responses = await asyncio.gather(
        *[client.post(f"/api/split-drafts/{a['id']}/accept", json=data(a)) for _ in range(2)]
    )
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json()["id"] == responses[1].json()["id"]
    await accept(client, b)
    assert len((await client.get("/api/tasks")).json()) == 2
    assert (await accept(client, a))["id"] == responses[0].json()["id"]


async def test_save_edit_conflict_and_user_evidence_survive_restart(client, tmp_path):
    item = await split(client, await notice(client))
    first = item["split_items"][0]
    response = await client.patch(
        f"/api/split-drafts/{first['id']}",
        json=data(first, title="需求分析报告终稿", due_at="2026-11-03T20:00:00+08:00"),
    )
    assert response.status_code == 200, response.text
    saved = response.json()["split_items"][0]
    assert saved["evidence"]["title"]["method"] == "user"
    assert saved["evidence"]["due_at"]["method"] == "user"
    assert (await client.get("/api/tasks")).json() == []
    assert (
        await client.post(f"/api/split-drafts/{first['id']}/accept", json=data(first))
    ).status_code == 409
    assert (
        await client.patch(f"/api/split-drafts/{first['id']}", json=data(first))
    ).status_code == 409
    app = create_app(tmp_path / "split.db")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as fresh:
            task = await accept(fresh, saved)
            assert task["title"] == "需求分析报告终稿"
            assert task["due_at"] == "2026-11-03T12:00:00Z"
            after = await current(fresh, item)
            assert after["split_items"][1]["status"] == "pending"


async def test_skip_restore_and_notice_completion(client):
    item = await split(client, await notice(client))
    first, second = item["split_items"]
    await accept(client, first)
    after = await decision(client, second)
    assert after["status"] == "accepted" and after["split_items"][1]["status"] == "ignored"
    after = await decision(client, after["split_items"][1], "reopen")
    assert after["status"] == "pending"
    await accept(client, after["split_items"][1])
    assert (await current(client, item))["status"] == "accepted"


async def test_all_skipped_can_be_reopened_without_duplicate_tasks(client):
    item = await split(client, await notice(client))
    await decision(client, item["split_items"][0])
    after = await decision(client, item["split_items"][1])
    assert after["status"] == "ignored"
    assert (await client.get("/api/tasks")).json() == []
    assert (await client.post(f"/api/suggestions/{item['id']}/reopen")).status_code == 409
    reopened = await decision(client, after["split_items"][0], "reopen")
    assert reopened["status"] == "pending"
    await accept(client, reopened["split_items"][0])
    assert len((await client.get("/api/tasks")).json()) == 1


async def test_retry_and_whole_notice_ignore_cannot_discard_partial_progress(client):
    item = await split(client, await notice(client))
    assert (await client.post(f"/api/inbox/{item['id']}/analyze")).status_code == 409
    await accept(client, item["split_items"][0])
    assert (await client.post(f"/api/suggestions/{item['id']}/ignore")).status_code == 409
    after = await current(client, item)
    assert after["status"] == "pending" and after["split_items"][1]["status"] == "pending"


async def test_duplicates_rechecked_at_confirmation(client):
    item = await split(client, await notice(client))
    draft = item["split_items"][0]
    original = await client.post("/api/tasks", json=draft["fields"])
    assert original.status_code == 200
    assert (
        await client.post(f"/api/split-drafts/{draft['id']}/accept", json=data(draft))
    ).status_code == 409
    after = await current(client, item)
    assert len(after["split_items"][0]["matched_tasks"]) == 1
    await accept(client, draft, allow_duplicate=True)
    assert len((await client.get("/api/tasks")).json()) == 2


@pytest.mark.parametrize(
    "text",
    [
        "1. 实验四报告取消，不用再提交。\n2. 周五20:00前提交原型设计。",
        "1. 实验四报告延期到下周一20:00。\n2. 周五20:00前提交原型设计。",
        "1. 今天天气很好。\n2. 周五20:00前提交原型设计。",
    ],
)
async def test_changes_and_non_tasks_cannot_be_forced_into_creation(client, text):
    item = await split(client, await notice(client, text))
    first = item["split_items"][0]
    assert not first["can_create"]
    response = await client.post(
        f"/api/split-drafts/{first['id']}/accept", json=data(first, title="强制创建")
    )
    assert response.status_code == 409
    assert (await client.get("/api/tasks")).json() == []


@pytest.mark.parametrize(
    "text",
    [
        "1. 提交需求分析报告。\n3. 提交原型设计。",
        "1. 提交需求分析报告。\n1. 提交原型设计。",
        "1. 提交需求分析报告。\n2. ",
        "软件工程：周五20:00前提交需求分析报告。",
        "\n".join(f"{i}. 提交报告。" for i in range(1, 22)),
    ],
)
async def test_invalid_numbering_cannot_generate_drafts(client, text):
    item = await notice(client, text)
    assert not item["split_supported"]
    assert (await client.post(f"/api/suggestions/{item['id']}/split")).status_code in {409, 422}


async def test_missing_times_and_shared_course_do_not_invent_or_leak_fields(client):
    item = await split(
        client,
        await notice(
            client,
            "软件工程，数据库原理：\n（1）下周一提交需求分析报告。\n（2）提交原型设计，上传至学习通。",
        ),
    )
    for draft in item["split_items"]:
        assert draft["fields"]["course"] == ""
        assert draft["fields"]["due_at"] is None
    assert item["split_items"][0]["fields"]["submission_platform"] == ""
    assert item["split_items"][1]["fields"]["submission_platform"] == "学习通"


async def test_generated_notice_requires_per_item_decisions_even_before_accept(client):
    item = await split(client, await notice(client))
    assert (await client.post(f"/api/suggestions/{item['id']}/ignore")).status_code == 409
    draft = item["split_items"][0]
    await accept(client, draft)


async def test_legacy_unique_index_migrates_with_backup_and_all_history_preserved(tmp_path):
    path = tmp_path / "legacy.db"
    app = create_app(path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            original = (await client.post("/api/tasks", json={"title": "旧实验报告"})).json()
            before = (await client.get(f"/api/tasks/{original['id']}")).json()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX ix_task_suggestion_id")
        connection.execute("CREATE UNIQUE INDEX ix_task_suggestion_id ON task (suggestion_id)")
        connection.execute("DROP TABLE splitdraft")
        connection.execute("UPDATE modelsettings SET model = 'keep-local:4b'")
    app = create_app(path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get(f"/api/tasks/{original['id']}")).json() == before
            assert (await client.get("/api/settings/model")).json()["model"] == "keep-local:4b"
            item = await split(client, await notice(client))
            await accept(client, item["split_items"][0])
            await accept(client, item["split_items"][1])
            assert len((await client.get("/api/tasks")).json()) == 3
    backups = list((tmp_path / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as old:
        assert (
            old.execute("SELECT title FROM task WHERE id=?", (original["id"],)).fetchone()[0]
            == "旧实验报告"
        )
    engine = make_engine(path)
    initialize(engine)
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1
    with Session(engine) as session:
        assert session.get(ModelSettings, 1).model == "keep-local:4b"
        assert len(session.exec(select(Suggestion)).all()) == 2
    with sqlite3.connect(path) as current_db:
        assert current_db.execute("PRAGMA foreign_key_check").fetchall() == []
    engine.dispose()


async def test_split_task_keeps_own_evidence_through_deadline_and_cancellation(client):
    item = await split(client, await notice(client))
    a, b = item["split_items"]
    first = await accept(client, a, title="需求分析报告")
    second = await accept(client, b)
    delay = await notice(client, "软件工程：需求分析报告延期到下周五晚上八点。")
    dp = await client.post(
        f"/api/suggestions/{delay['id']}/deadline-preview", json={"task_id": first["id"]}
    )
    assert dp.status_code == 200
    changed = await client.post(f"/api/deadline-proposals/{dp.json()['id']}/confirm")
    assert changed.status_code == 200
    cancel = await notice(client, "软件工程：需求分析报告取消，不用再提交。")
    cp = await client.post(
        f"/api/suggestions/{cancel['id']}/cancellation-preview", json={"task_id": first["id"]}
    )
    assert cp.status_code == 200
    assert (
        await client.post(f"/api/cancellation-proposals/{cp.json()['id']}/confirm")
    ).status_code == 200
    detail = (await client.get(f"/api/tasks/{first['id']}")).json()
    assert len(detail["sources"]) == 3
    assert detail["evidence"]["due_at"]["message_id"] == delay["message"]["id"]
    assert detail["events"][-1]["message_id"] == cancel["message"]["id"]
    unaffected = (await client.get(f"/api/tasks/{second['id']}")).json()
    assert unaffected["task"] == second and len(unaffected["sources"]) == 1
    assert unaffected["evidence"]["due_at"]["quote"] in b["quote"]


async def test_numbered_message_does_not_invoke_single_task_model(tmp_path):
    def never_provider(_settings):
        raise AssertionError("A multi-task notification must not be sent to a single-task model")

    app = create_app(tmp_path / "no-model.db", provider_factory=never_provider)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (
                await client.put("/api/settings/model", json={"enabled": True, "model": "local:4b"})
            ).status_code == 200
            item = await split(client, await notice(client))
            assert len(item["split_items"]) == 2
