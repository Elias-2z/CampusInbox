import asyncio

import httpx
import pytest

from campus_inbox.main import create_app
from campus_inbox.providers import ProviderError
from campus_inbox.schemas import TaskFields

pytestmark = pytest.mark.anyio


class FakeProvider:
    calls = 0
    fail = False

    def __init__(self, _config):
        pass

    def list_models(self):
        return ["test-local:4b"]

    def generate_structured(self, _text, _reference, _timezone):
        FakeProvider.calls += 1
        if self.fail:
            raise ProviderError("本地模型超时，保留规则结果。")
        return {"title": "报告", "location": "虚构地点"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(tmp_path):
    FakeProvider.calls, FakeProvider.fail = 0, False
    app = create_app(tmp_path / "test.db", FakeProvider)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


def notification(text="软件工程：请于10月31日18:00前提交需求分析报告，提交至学习通。", day=29):
    return {"text": text, "sent_at": f"2026-10-{day}T14:20:00+08:00", "timezone": "Asia/Shanghai"}


async def import_one(client, **kwargs):
    response = await client.post("/api/inbox", json=notification(**kwargs))
    assert response.status_code == 200, response.text
    return response.json()


def task_data(task):
    return {k: v for k, v in task.items() if k in TaskFields.model_fields}


async def test_real_import_review_confirm_and_source(client):
    item = await import_one(client)
    assert item["status"] == "pending"
    assert (await client.get("/api/tasks")).json() == []
    response = await client.post(f"/api/suggestions/{item['id']}/accept", json=item["fields"])
    assert response.status_code == 200, response.text
    task = response.json()
    assert task["due_at"] == "2026-10-31T10:00:00Z"
    detail = (await client.get(f"/api/tasks/{task['id']}")).json()
    assert detail["source"]["text"] == notification()["text"]
    assert detail["events"][0]["operation"] == "create"
    assert detail["evidence"]["title"]["method"] == "rule"
    assert detail["evidence"]["due_at"]["method"] == "rule"


async def test_duplicate_import_and_parallel_accept_are_idempotent(client):
    item = await import_one(client)
    assert (await import_one(client))["id"] == item["id"]
    responses = await asyncio.gather(
        *[
            client.post(f"/api/suggestions/{item['id']}/accept", json=item["fields"])
            for _ in range(2)
        ]
    )
    assert [r.status_code for r in responses] == [200, 200]
    assert len((await client.get("/api/tasks")).json()) == 1
    assert responses[0].json()["id"] == responses[1].json()["id"]


async def test_relative_date_deduplication_respects_original_day(client):
    a = await import_one(client, text="明天18:00前提交实验报告", day=29)
    b = await import_one(client, text="明天18:00前提交实验报告", day=30)
    assert a["id"] != b["id"]
    assert a["fields"]["due_at"] != b["fields"]["due_at"]


async def test_version_conflict_archive_and_restore(client):
    task = (await client.post("/api/tasks", json={"title": "手动任务"})).json()
    payload = {**task_data(task), "expected_version": 1, "status": "completed"}
    response = await client.patch(f"/api/tasks/{task['id']}", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    assert (await client.patch(f"/api/tasks/{task['id']}", json=payload)).status_code == 409
    response = await client.delete(f"/api/tasks/{task['id']}?expected_version=2")
    assert response.json()["status"] == "archived"
    restored = await client.patch(
        f"/api/tasks/{task['id']}",
        json={**task_data(task), "expected_version": 3, "status": "todo"},
    )
    assert restored.status_code == 200
    assert len((await client.get(f"/api/tasks/{task['id']}")).json()["events"]) == 4


async def test_change_cannot_create_and_ignore_can_be_reopened(client):
    item = await import_one(client, text="数据库实验三延期至下周一23:59。")
    fields = {**item["fields"], "title": "实验三"}
    assert (
        await client.post(f"/api/suggestions/{item['id']}/accept", json=fields)
    ).status_code == 409
    assert (await client.post(f"/api/suggestions/{item['id']}/ignore")).json()[
        "status"
    ] == "ignored"
    assert (await client.post(f"/api/suggestions/{item['id']}/reopen")).json()[
        "status"
    ] == "pending"


async def test_no_deadline_can_be_reviewed_and_user_edits_recorded(client):
    item = await import_one(client, text="请完成英语阅读练习。")
    assert item["fields"]["due_at"] is None
    response = await client.post(
        f"/api/suggestions/{item['id']}/accept",
        json={**item["fields"], "title": "阅读练习（用户修改）"},
    )
    assert response.status_code == 200, response.text
    detail = (await client.get(f"/api/tasks/{response.json()['id']}")).json()
    assert detail["task"]["due_at"] is None
    assert detail["evidence"]["title"]["method"] == "user"


async def test_model_failure_and_rule_first(client):
    config = {"enabled": True, "model": "test-local:4b"}
    assert (await client.put("/api/settings/model", json=config)).status_code == 200
    await import_one(client)
    assert FakeProvider.calls == 0
    FakeProvider.fail = True
    item = await import_one(client, text="请提交报告，时间待定。")
    assert FakeProvider.calls == 1
    assert item["message"]["text"] == "请提交报告，时间待定。"
    assert item["status"] == "pending"
    assert any("超时" in w for w in item["warnings"])


async def test_duplicate_task_needs_explicit_confirmation(client):
    first = await import_one(client)
    await client.post(f"/api/suggestions/{first['id']}/accept", json=first["fields"])
    second = await import_one(
        client, text="软件工程：10月31日18:00前提交需求分析报告，请注意格式。"
    )
    assert second["matched_tasks"]
    assert (
        await client.post(f"/api/suggestions/{second['id']}/accept", json=second["fields"])
    ).status_code == 409
    response = await client.post(
        f"/api/suggestions/{second['id']}/accept",
        json={**second["fields"], "allow_duplicate": True},
    )
    assert response.status_code == 200


async def test_unsafe_model_url_and_naive_time_rejected(client):
    assert (
        await client.put("/api/settings/model", json={"base_url": "https://example.com"})
    ).status_code == 422
    assert (
        await client.post("/api/inbox", json={**notification(), "sent_at": "2026-10-29T14:20:00"})
    ).status_code == 422
    assert (await client.post("/api/settings/model/probe", json={})).json()["models"] == [
        "test-local:4b"
    ]


async def test_data_survives_app_restart(tmp_path):
    path = tmp_path / "persistent.db"
    app = create_app(path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            response = await c.post("/api/tasks", json={"title": "重启后保留"})
            assert response.status_code == 200, response.text
    reopened = create_app(path)
    async with reopened.router.lifespan_context(reopened):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=reopened), base_url="http://test"
        ) as c:
            assert (await c.get("/api/tasks")).json()[0]["title"] == "重启后保留"
