"""Preview legacy exports using synthetic data without writing application records."""

import json

import httpx
import pytest

from campus_inbox.main import create_app

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(tmp_path):
    app = create_app(tmp_path / "isolated.db", qq_background=False, chaoxing_background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as session:
            yield session


def legacy_export():
    return {
        "version": "0.1.0",
        "exported_at": "2026-11-03T10:00:00+08:00",
        "messages": [
            {"id": "m1", "text": "请提交报告", "captured_at": "2026-11-03T09:00:00+08:00"},
            {
                "id": "m2",
                "text": "通知",
                "captured_at": "2026-11-03T09:00:00+08:00",
                "external": {"time_known": False},
            },
        ],
        "proposals": [{"id": "p1", "status": "pending"}],
        "tasks": [
            {
                "id": "t1",
                "title": "报告",
                "status": "pending",
                "sources": [{"id": "m1"}],
                "history": [{"action": "create"}],
                "checklist": [{"text": "报告", "done": False}],
            },
            {
                "id": "t2",
                "title": "作业",
                "status": "submitted",
                "sources": [{"id": "m1"}, {"id": "m2"}],
                "history": [],
            },
        ],
    }


async def test_preview_counts_risks_without_importing_or_returning_private_text(client):
    document = legacy_export()
    response = await client.post(
        "/api/migrations/legacy/preview", content=json.dumps(document, ensure_ascii=False).encode()
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["counts"] == {
        "messages": 2,
        "proposals": 1,
        "tasks": 2,
        "history": 1,
        "materials": 1,
    }
    assert data["issues"]["unknown_message_time"] == 1
    assert data["issues"]["multi_source_tasks"] == 1
    assert data["issues"]["messages_shared_by_tasks"] == 1
    assert "请提交报告" not in response.text
    assert (await client.get("/api/tasks")).json() == []
    assert (await client.get("/api/inbox")).json() == []


@pytest.mark.parametrize(
    "body", [b"bad-json", b"{}", b'{"version":"0.1.0","messages":{},"proposals":[],"tasks":[]}']
)
async def test_preview_rejects_invalid_or_unrecognized_export(client, body):
    response = await client.post("/api/migrations/legacy/preview", content=body)
    assert response.status_code == 422
    assert (await client.get("/api/tasks")).json() == []


async def test_preview_rejects_oversized_input_before_parsing(client):
    response = await client.post(
        "/api/migrations/legacy/preview", content=b" " * (10 * 1024 * 1024 + 1)
    )
    assert response.status_code == 413


async def test_preview_rejects_remote_origins(client):
    response = await client.post(
        "/api/migrations/legacy/preview",
        headers={"Origin": "https://example.com"},
        content=json.dumps(legacy_export()).encode(),
    )
    assert response.status_code == 403


async def test_preview_accepts_real_legacy_export_shape(client, tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "local-assistant"))
    from courseinbox.core import Inbox

    legacy = Inbox(tmp_path / "synthetic-old.db")
    imported = legacy.ingest(
        "《软件工程》第一次作业，请提交实验报告。截止2026年12月8日18:00。",
        captured_at="2026-11-03T09:00:00+08:00",
    )
    legacy.confirm(imported["proposals"][0]["id"])
    document = {
        "version": "0.1.0",
        "exported_at": "2026-11-03T10:00:00+08:00",
        "messages": legacy.messages(limit=None, include_hidden=True),
        "proposals": legacy.proposals(None),
        "tasks": [legacy.task_detail(task["id"]) for task in legacy.tasks()],
    }
    response = await client.post(
        "/api/migrations/legacy/preview", content=json.dumps(document, ensure_ascii=False).encode()
    )
    assert response.status_code == 200, response.text
    assert response.json()["counts"]["tasks"] == 1
    assert response.json()["counts"]["messages"] == 1
    assert response.json()["issues"]["unknown_message_time"] == 0
    assert (await client.get("/api/tasks")).json() == []


async def test_preview_counts_attachment_references_and_invalid_identities(client):
    document = legacy_export()
    document["messages"][0]["external"] = {"attachments": [{"name": "要求.pdf"}]}
    document["messages"].append({"id": "m1", "captured_at": "2026-11-03T09:00:00"})
    document["tasks"].append({"id": "", "sources": [{"id": "absent"}]})
    response = await client.post(
        "/api/migrations/legacy/preview", content=json.dumps(document).encode()
    )
    assert response.status_code == 200
    issues = response.json()["issues"]
    assert issues["attachment_references"] == 1
    assert issues["duplicate_message_ids"] == 1
    assert issues["missing_task_ids"] == 1
    assert issues["missing_task_sources"] == 1
    assert issues["unknown_message_time"] == 2
