"""Explicit original retrieval uses the main inbox and current account only."""

import httpx
import pytest
from sqlmodel import Session, select

from campus_inbox.attachments import original_path
from campus_inbox.connectors.chaoxing import ChaoxingEvent
from campus_inbox.main import create_app
from campus_inbox.models import Attachment, Task

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def context(tmp_path):
    app = create_app(tmp_path / "main.db", qq_background=False, chaoxing_background=False)
    async with app.router.lifespan_context(app):
        runtime = app.state.chaoxing
        with Session(app.state.engine) as session:
            settings = runtime.settings(session)
            runtime.account_id = settings.account_id
            session.commit()
        job = {
            "kind": "task_center",
            "message_id": "work-1",
            "title": "第一次作业",
            "text": "《软件工程》第一次作业，请提交报告。",
            "url": "https://mooc1.chaoxing.com/mooc-ans/work/studentstudy?courseId=1&classId=2&workId=3",
            "published_at": "2026-11-03T09:00:00+08:00",
            "attachments": [
                {"name": "要求.txt", "url": "https://d0.cldisk.com/download/" + "a" * 32}
            ],
        }
        event_id, _ = runtime.intake.receive(runtime.account_id, job)
        assert runtime.intake.process(event_id) == "done"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            yield client, runtime, event_id


async def test_completed_event_with_missing_original_stays_visible(context):
    client, _, event_id = context
    response = await client.get("/api/connectors/chaoxing/events")
    assert response.status_code == 200
    item = next(item for item in response.json() if item["id"] == event_id)
    assert item["state"] == "done" and item["attachments"][0]["fetchable"]
    assert "cldisk.com" not in response.text


async def test_notice_original_retry_uses_no_credentials_and_rejects_redirects(
    context, monkeypatch
):
    client, runtime, _ = context
    url = "https://d0.cldisk.com/download/" + "b" * 32
    event_id, _ = runtime.intake.receive(
        runtime.account_id,
        {
            "kind": "course_notice",
            "message_id": "notice_retry",
            "text": "软件工程：请提交实验报告。",
            "captured_at": "2026-11-03T09:00:00+08:00",
            "attachments": [{"name": "说明.txt", "url": url}],
        },
    )
    assert runtime.intake.process(event_id) == "done"
    real_client = httpx.Client
    redirected = True
    seen = []

    def handle(request):
        seen.append(str(request.url))
        assert "authorization" not in request.headers and "cookie" not in request.headers
        if redirected:
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})
        return httpx.Response(200, content=b"requirements")

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    path = f"/api/connectors/chaoxing/events/{event_id}/attachments/0/fetch"
    assert (await client.post(path)).status_code == 409
    redirected = False
    assert (await client.post(path)).status_code == 200
    assert seen == [url, url]
    with Session(runtime.engine) as session:
        assert session.exec(select(Attachment)).one().text == "requirements"
        assert session.exec(select(Task)).all() == []


async def test_original_fetch_adds_file_without_duplicate_message_or_task(context, monkeypatch):
    client, runtime, event_id = context
    before = await client.get("/api/inbox")
    source = before.json()[0]["message"]["source_context"]
    assert source["event_id"] == event_id
    assert source["missing_originals"] == [{"index": 0, "name": "要求.txt"}]
    assert "cldisk.com" not in before.text
    monkeypatch.setattr(runtime, "_fetch_original", lambda _job, _item: "请提交实验报告".encode())
    response = await client.post(f"/api/connectors/chaoxing/events/{event_id}/attachments/0/fetch")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "done"
    assert (await client.get("/api/tasks")).json() == []
    inbox = (await client.get("/api/inbox")).json()
    assert len(inbox) == 1
    assert inbox[0]["message"]["source_context"]["missing_originals"] == []
    item = inbox[0]["message"]["attachments"][0]
    original = await client.get(f"/api/attachments/{item['id']}/download")
    assert original.content == "请提交实验报告".encode()
    response = await client.post(f"/api/connectors/chaoxing/events/{event_id}/attachments/0/fetch")
    assert response.status_code == 200
    with Session(runtime.engine) as session:
        assert len(session.exec(select(Attachment)).all()) == 1
        assert len(session.exec(select(ChaoxingEvent)).all()) == 1
        assert session.exec(select(Task)).all() == []


async def test_missing_local_file_is_restored_from_the_saved_original(context, monkeypatch):
    client, runtime, event_id = context
    fetch_url = f"/api/connectors/chaoxing/events/{event_id}/attachments/0/fetch"
    content = "请提交实验报告。".encode()
    monkeypatch.setattr(runtime, "_fetch_original", lambda _job, _item: content)
    assert (await client.post(fetch_url)).status_code == 200
    with Session(runtime.engine) as session:
        attachment = session.exec(select(Attachment)).one()
        attachment_id = attachment.id
        path = original_path(runtime.data_dir.parent / "uploads", attachment)
    path.unlink()
    source = (await client.get("/api/inbox")).json()[0]["message"]["source_context"]
    assert source["missing_originals"] == [{"index": 0, "name": "要求.txt", "recoverable": True}]

    def unexpected_download(*_):
        pytest.fail("restoration must use the saved copy")

    monkeypatch.setattr(runtime, "_fetch_original", unexpected_download)
    assert (await client.post(fetch_url)).status_code == 200
    assert (await client.get(f"/api/attachments/{attachment_id}/download")).content == content
    source = (await client.get("/api/inbox")).json()[0]["message"]["source_context"]
    assert source["missing_originals"] == []


async def test_fetch_queues_notice_with_only_an_attachment(context, monkeypatch):
    client, runtime, _ = context
    event_id, _ = runtime.intake.receive(
        runtime.account_id,
        {
            "kind": "course_notice",
            "message_id": "attachment-only",
            "title": "课程通知",
            "text": "",
            "captured_at": "2026-11-03T09:00:00+08:00",
            "attachments": [
                {"name": "要求.txt", "url": "https://d0.cldisk.com/download/" + "a" * 32}
            ],
        },
    )
    assert runtime.intake.process(event_id) == "needs_review"
    monkeypatch.setattr(runtime, "_fetch_original", lambda _job, _item: "请提交实验报告。".encode())
    response = await client.post(f"/api/connectors/chaoxing/events/{event_id}/attachments/0/fetch")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "pending"
    assert runtime.intake.process(event_id) == "done"
    inbox = (await client.get("/api/inbox")).json()
    item = next(item for item in inbox if item["message"]["text"] == "")
    assert item["message"]["attachments"][0]["filename"] == "要求.txt"
    assert (await client.get("/api/tasks")).json() == []


async def test_account_change_during_fetch_discards_the_result(context, monkeypatch):
    client, runtime, event_id = context

    def switch_account(_job, _item):
        with Session(runtime.engine) as session:
            settings = runtime.settings(session)
            settings.account_id = "another-test-account"
            session.add(settings)
            session.commit()
        return b"must not attach to previous account"

    monkeypatch.setattr(runtime, "_fetch_original", switch_account)
    response = await client.post(f"/api/connectors/chaoxing/events/{event_id}/attachments/0/fetch")
    assert response.status_code == 409
    with Session(runtime.engine) as session:
        assert session.exec(select(Attachment)).all() == []


async def test_failed_download_retains_reference_and_reports_safe_error(context, monkeypatch):
    client, runtime, event_id = context

    def fail(_job, _item):
        raise ValueError("secret-cookie-should-not-escape")

    monkeypatch.setattr(runtime, "_fetch_original", fail)
    response = await client.post(f"/api/connectors/chaoxing/events/{event_id}/attachments/0/fetch")
    assert response.status_code == 409
    assert "secret-cookie" not in response.text
    with Session(runtime.engine) as session:
        event = session.get(ChaoxingEvent, event_id)
        assert "content_base64" not in event.payload["attachments"][0]


async def test_oversized_and_invalid_returned_bytes_are_not_saved(context, monkeypatch):
    client, runtime, event_id = context
    for value in [b"", b"x" * (8 * 1024 * 1024 + 1), "not bytes"]:
        monkeypatch.setattr(runtime, "_fetch_original", lambda _job, _item, result=value: result)
        response = await client.post(
            f"/api/connectors/chaoxing/events/{event_id}/attachments/0/fetch"
        )
        assert response.status_code == 409
    with Session(runtime.engine) as session:
        assert session.exec(select(Attachment)).all() == []


async def test_current_account_required_before_any_external_download(context, monkeypatch):
    client, runtime, event_id = context
    with Session(runtime.engine) as session:
        settings = runtime.settings(session)
        settings.account_id = "different-account"
        session.add(settings)
        session.commit()

    def fail_if_called(*_args):
        pytest.fail("old account original must not be downloaded")

    monkeypatch.setattr(runtime, "_fetch_original", fail_if_called)
    response = await client.post(f"/api/connectors/chaoxing/events/{event_id}/attachments/0/fetch")
    assert response.status_code == 409


async def test_remote_origin_cannot_trigger_original_download(context):
    client, _, event_id = context
    response = await client.post(
        f"/api/connectors/chaoxing/events/{event_id}/attachments/0/fetch",
        headers={"Origin": "https://example.com"},
    )
    assert response.status_code == 403
