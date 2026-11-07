"""Task recognition proposes fields without changing confirmed records."""

import httpx
import pytest

from campus_inbox.main import create_app
from campus_inbox.schemas import TaskFields

pytestmark = pytest.mark.anyio


class FakeProvider:
    def __init__(self, config):
        self.config = config

    def generate_structured(self, text, reference, timezone):
        return {"title": "设计说明书", "location": "教学楼A101"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(tmp_path):
    app = create_app(
        tmp_path / "recognition.db", FakeProvider, qq_background=False, chaoxing_background=False
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            yield client


async def create_imported_task(client):
    response = await client.post(
        "/api/inbox",
        json={
            "text": "软件工程：11 月 9 日在学习通提交第一次作业。",
            "sent_at": "2026-11-05T15:00:00+08:00",
        },
    )
    item = response.json()
    response = await client.post(
        f"/api/suggestions/{item['id']}/accept",
        json={**item["fields"], "title": "用户确认的标题", "due_date": None},
    )
    assert response.status_code == 200, response.text
    return response.json(), item


async def recognize(client, task, **body):
    return await client.post(
        f"/api/tasks/{task['id']}/recognize",
        json={"expected_version": task["version"], **body},
    )


async def test_recognition_is_a_read_only_preview_with_original_source(client):
    task, item = await create_imported_task(client)
    before = (await client.get(f"/api/tasks/{task['id']}")).json()
    response = await recognize(client, task)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["task"] == task
    assert preview["fields"]["due_date"] == "2026-11-09"
    assert preview["fields"]["due_at"] is None
    assert "description" not in preview["fields"]
    assert preview["message"]["id"] == item["message"]["id"]
    assert preview["evidence"]["due_date"]["method"] == "rule"
    assert (await client.get(f"/api/tasks/{task['id']}")).json() == before
    inbox = (await client.get("/api/inbox?status=all")).json()
    assert inbox[0]["status"] == "accepted"
    assert len(inbox) == 1


async def test_confirm_uses_existing_versioned_task_update_without_duplicates(client):
    task, _ = await create_imported_task(client)
    preview = (await recognize(client, task)).json()
    fields = {key: task[key] for key in TaskFields.model_fields}
    fields["due_date"] = preview["fields"]["due_date"]
    response = await client.patch(
        f"/api/tasks/{task['id']}",
        json={**fields, "status": task["status"], "expected_version": preview["task"]["version"]},
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["id"] == task["id"]
    assert saved["title"] == "用户确认的标题"
    assert saved["due_date"] == "2026-11-09"
    assert len((await client.get("/api/tasks")).json()) == 1
    history = (await client.get(f"/api/tasks/{task['id']}")).json()["events"]
    assert history[-1]["operation"] == "update"


async def test_recognition_rejects_stale_versions_and_unrelated_sources(client):
    task, _ = await create_imported_task(client)
    other = (await client.post("/api/tasks", json={"title": "另一项任务"})).json()
    response = await recognize(client, task, message_id=other["message_id"])
    assert response.status_code == 422
    response = await recognize(client, task, expected_version=task["version"] + 1)
    assert response.status_code == 409


async def test_split_task_only_recognizes_its_own_numbered_item(client):
    item = (
        await client.post(
            "/api/inbox",
            json={
                "text": "软件工程：\n1. 2026年11月9日18:00前提交实验报告。\n"
                "2. 2026年11月10日20:00前提交原型设计。",
                "sent_at": "2026-11-05T15:00:00+08:00",
            },
        )
    ).json()
    split = (await client.post(f"/api/suggestions/{item['id']}/split")).json()
    draft = split["split_items"][0]
    task = (
        await client.post(
            f"/api/split-drafts/{draft['id']}/accept",
            json={**draft["fields"], "expected_updated_at": draft["updated_at"]},
        )
    ).json()
    response = await recognize(client, task)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["fields"]["due_at"].startswith("2026-11-09T10:00:00")
    assert "原型设计" not in preview["message"]["text"]


@pytest.mark.parametrize("status", ["completed", "cancelled", "archived"])
async def test_recognition_preserves_task_status_and_materials(client, status):
    task, _ = await create_imported_task(client)
    material = await client.put(
        f"/api/tasks/{task['id']}/materials",
        json={
            "expected_version": task["version"],
            "items": [{"text": "已整理的材料", "done": True}],
        },
    )
    assert material.status_code == 200, material.text
    task = (await client.get(f"/api/tasks/{task['id']}")).json()["task"]
    fields = {key: task[key] for key in TaskFields.model_fields}
    task = (
        await client.patch(
            f"/api/tasks/{task['id']}",
            json={**fields, "status": status, "expected_version": task["version"]},
        )
    ).json()
    before = (await client.get(f"/api/tasks/{task['id']}")).json()
    response = await recognize(client, task)
    assert response.status_code == 200, response.text
    assert response.json()["task"]["status"] == status
    assert (await client.get(f"/api/tasks/{task['id']}")).json() == before


async def test_manual_task_can_use_model_assistance_without_changing_its_inbox_record(client):
    task = (
        await client.post(
            "/api/tasks",
            json={
                "title": "用户标题",
                "description": "请整理《设计说明书》，交到教学楼A101，时间待定。",
            },
        )
    ).json()
    inbox = (await client.get("/api/inbox?status=all")).json()
    saved = await client.put(
        "/api/settings/model", json={"enabled": True, "model": "test-local:4b"}
    )
    assert saved.status_code == 200
    response = await recognize(client, task)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["fields"]["title"] == "设计说明书"
    assert preview["fields"]["location"] == "教学楼A101"
    assert preview["evidence"]["location"]["method"] == "llm"
    assert (await client.get("/api/inbox?status=all")).json() == inbox


async def test_an_associated_notification_can_be_selected_as_the_source(client):
    task, _ = await create_imported_task(client)
    incoming = (
        await client.post(
            "/api/inbox",
            json={
                "text": "软件工程：11月10日提交第一次作业。",
                "sent_at": "2026-11-05T15:00:00+08:00",
            },
        )
    ).json()
    proposal = (
        await client.post(
            f"/api/suggestions/{incoming['id']}/merge-preview",
            json={"task_id": task["id"], "fields": []},
        )
    ).json()
    confirmed = await client.post(f"/api/merge-proposals/{proposal['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    task = (await client.get(f"/api/tasks/{task['id']}")).json()["task"]
    response = await recognize(client, task, message_id=incoming["message"]["id"])
    assert response.status_code == 200, response.text
    assert response.json()["fields"]["due_date"] == "2026-11-10"


async def test_a_preview_cannot_overwrite_changes_made_after_recognition(client):
    task, _ = await create_imported_task(client)
    response = await recognize(client, task)
    assert response.status_code == 200, response.text
    preview = response.json()
    fields = {key: task[key] for key in TaskFields.model_fields}
    saved = await client.patch(
        f"/api/tasks/{task['id']}",
        json={
            **fields,
            "title": "识别后的另一次编辑",
            "status": task["status"],
            "expected_version": task["version"],
        },
    )
    assert saved.status_code == 200
    stale = await client.patch(
        f"/api/tasks/{task['id']}",
        json={
            **fields,
            "due_date": preview["fields"]["due_date"],
            "status": task["status"],
            "expected_version": preview["task"]["version"],
        },
    )
    assert stale.status_code == 409
    assert (await client.get(f"/api/tasks/{task['id']}")).json()["task"] == saved.json()
