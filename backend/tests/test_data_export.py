"""Export actual business records without modifying the database or exposing credentials."""

import sqlite3
from datetime import UTC, date, datetime

import httpx
import pytest
from sqlalchemy import event
from sqlmodel import Session

from campus_inbox.connectors.chaoxing import ChaoxingEvent
from campus_inbox.connectors.qq import QQEvent, QQSettings
from campus_inbox.main import create_app
from campus_inbox.models import (
    Attachment,
    CancellationChange,
    CancellationProposal,
    DeadlineChange,
    DeadlineProposal,
    MergeChange,
    MergeProposal,
    Message,
    ModelSettings,
    ReminderState,
    SplitDraft,
    Suggestion,
    Task,
    TaskEvent,
)

pytestmark = pytest.mark.anyio
STAMP = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
SECRET = "test-private-token-never-export"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def export_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MINERU_API_KEY", SECRET)
    app = create_app(tmp_path / "test.db", qq_background=False)
    async with app.router.lifespan_context(app):
        with Session(app.state.engine) as session:
            session.add_all(
                [
                    Message(id="m1", text="原始通知全文", sent_at=STAMP, dedupe_key="one"),
                    Message(id="m2", text="已忽略的通知", sent_at=STAMP, dedupe_key="two"),
                ]
            )
            session.commit()
            session.add_all(
                [
                    Suggestion(
                        id="s1", message_id="m1", status="accepted", fields={"title": "作业"}
                    ),
                    Suggestion(
                        id="s2", message_id="m2", status="ignored", fields={"title": "建议"}
                    ),
                ]
            )
            session.commit()
            session.add(
                Task(
                    id="t1",
                    suggestion_id="s1",
                    message_id="m1",
                    title="已归档作业",
                    course="软件工程",
                    status="archived",
                    due_date=date(2026, 12, 8),
                    description="任务详细说明",
                )
            )
            session.commit()
            session.add_all(
                [
                    TaskEvent(
                        id="e1",
                        task_id="t1",
                        message_id="m1",
                        operation="edit",
                        before={"title": "旧"},
                        after={"title": "新"},
                    ),
                    SplitDraft(
                        id="d1",
                        suggestion_id="s1",
                        position=1,
                        quote="拆分原文",
                        fields={"title": "草稿"},
                    ),
                    DeadlineProposal(
                        id="dp1",
                        suggestion_id="s1",
                        task_id="t1",
                        task_version=1,
                        proposed_due_at=STAMP,
                        suggestion_updated_at=STAMP,
                    ),
                    CancellationProposal(
                        id="cp1",
                        suggestion_id="s1",
                        task_id="t1",
                        task_version=1,
                        before_status="todo",
                        suggestion_updated_at=STAMP,
                    ),
                    Attachment(
                        id="a1",
                        filename="原件.txt",
                        size=12,
                        status="ready",
                        text="附件提取文字",
                        message_id="m1",
                        batch_id=SECRET,
                        error=SECRET,
                    ),
                    Attachment(id="a2", filename="待处理.txt", size=8, text="未提交提取文字"),
                    ReminderState(
                        id="r1", task_id="t1", due_at=STAMP, phase="upcoming", is_read=True
                    ),
                    QQEvent(
                        id="q1",
                        identity="queue-1",
                        state="pending",
                        payload={
                            "text": "未处理群消息",
                            "sender_id": "123",
                            "sender_name": {"password": SECRET},
                            "access_token": SECRET,
                        },
                        error=SECRET,
                    ),
                    QQSettings(config={"access_token": SECRET}, cursor=SECRET, error=SECRET),
                    ChaoxingEvent(
                        id="cx1",
                        identity="cx-identity",
                        account_id=SECRET,
                        state="needs_time",
                        payload={
                            "kind": "task_center",
                            "message_id": "work-1",
                            "title": "学习通待处理作业",
                            "text": "学习通原文待核对",
                            "description": {"password": SECRET},
                            "deadline": "2026-11-30",
                            "url": "https://example.test/?token=" + SECRET,
                            "access_token": SECRET,
                            "field_evidence": {"deadline": "截止时间原文", "password": SECRET},
                            "attachments": [
                                {
                                    "name": "材料.pdf",
                                    "status": "partial",
                                    "text": "材料提取文字",
                                    "content_base64": SECRET,
                                    "url": SECRET,
                                }
                            ],
                        },
                    ),
                ]
            )
            session.commit()
            session.add(
                MergeProposal(
                    id="mp1",
                    source_key="source",
                    suggestion_id="s1",
                    split_draft_id="d1",
                    source_updated_at=STAMP,
                    task_id="t1",
                    task_version=1,
                    before_fields={"title": "旧"},
                    after_fields={"title": "新"},
                )
            )
            session.commit()
            session.add_all(
                [
                    DeadlineChange(
                        suggestion_id="s1",
                        task_id="t1",
                        proposal_id="dp1",
                        event_id="e1",
                        applied_version=2,
                    ),
                    CancellationChange(
                        suggestion_id="s1",
                        task_id="t1",
                        proposal_id="cp1",
                        event_id="e1",
                        applied_version=2,
                    ),
                    MergeChange(
                        id="mc1",
                        source_key="source",
                        suggestion_id="s1",
                        split_draft_id="d1",
                        task_id="t1",
                        proposal_id="mp1",
                        event_id="e1",
                        applied_version=2,
                    ),
                ]
            )
            settings = session.get(ModelSettings, 1)
            settings.base_url = SECRET
            session.add(settings)
            session.commit()
        uploads = tmp_path / "uploads"
        uploads.mkdir()
        (uploads / "a1.txt").write_text("attachment binary " + SECRET, encoding="utf-8")
        (tmp_path / "qq-onebot.json").write_text(SECRET, encoding="utf-8")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
            base_url="http://127.0.0.1:8000",
        ) as client:
            yield client, tmp_path / "test.db"


def dump_database(path):
    with sqlite3.connect(path) as connection:
        return list(connection.iterdump())


async def test_json_exports_all_business_records_without_secrets_or_writes(export_app):
    client, path = export_app
    before = dump_database(path)
    response = await client.get("/api/export.json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    records = data["records"]
    assert [x["title"] for x in records["tasks"]] == ["已归档作业"]
    assert records["tasks"][0]["due_date"] == "2026-12-08"
    assert {x["text"] for x in records["messages"]} == {"原始通知全文", "已忽略的通知"}
    assert {x["status"] for x in records["suggestions"]} == {"accepted", "ignored"}
    assert records["task_events"][0]["before"] == {"title": "旧"}
    assert {x["text"] for x in records["attachments"]} == {"附件提取文字", "未提交提取文字"}
    assert records["qq_events"][0]["payload"]["text"] == "未处理群消息"
    chaoxing = records["chaoxing_events"][0]["payload"]
    assert chaoxing["text"] == "学习通原文待核对"
    assert chaoxing["attachments"][0]["text"] == "材料提取文字"
    assert chaoxing["field_evidence"]["deadline"] == "截止时间原文"
    for key in (
        "split_drafts",
        "deadline_proposals",
        "deadline_changes",
        "cancellation_proposals",
        "cancellation_changes",
        "merge_proposals",
        "merge_changes",
        "reminder_states",
        "reminder_settings",
    ):
        assert len(records[key]) == 1, key
    assert SECRET not in response.text
    assert "附件" in str(data["scope"]) and "恢复" in str(data["scope"])
    assert dump_database(path) == before


async def test_readable_report_contains_all_tasks_suggestions_and_source_text(export_app):
    client, path = export_app
    before = dump_database(path)
    response = await client.get("/api/export.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    for text in (
        "已归档作业",
        "软件工程",
        "原始通知全文",
        "已忽略的通知",
        "建议",
        "学习通原文待核对",
    ):
        assert text in response.text
    assert "附件" in response.text and "恢复" in response.text
    assert SECRET not in response.text
    assert dump_database(path) == before


async def test_empty_database_still_exports_usable_files(tmp_path):
    app = create_app(tmp_path / "empty.db", qq_background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
            base_url="http://127.0.0.1:8000",
        ) as client:
            data = (await client.get("/api/export.json")).json()
            assert data["records"]["tasks"] == []
            assert data["records"]["messages"] == []
            assert (await client.get("/api/export.txt")).status_code == 200


async def test_related_records_share_one_snapshot_during_concurrent_write(tmp_path):
    path = tmp_path / "concurrent.db"
    app = create_app(path, qq_background=False)
    async with app.router.lifespan_context(app):
        inserted = False

        def write_after_first_select(_conn, _cursor, statement, _params, _context, _many):
            nonlocal inserted
            if inserted or "FROM message" not in statement:
                return
            inserted = True
            with sqlite3.connect(path) as writer:
                writer.execute(
                    "INSERT INTO message (id,text,source,sent_at,timezone,received_at,dedupe_key) "
                    "VALUES ('new','并发原文','test',?,'UTC',?,'new')",
                    (STAMP, STAMP),
                )
                writer.execute(
                    "INSERT INTO suggestion "
                    "(id,message_id,status,action,method,fields,evidence,warnings,"
                    "created_at,updated_at) "
                    "VALUES ('new','new','pending','create','rule','{}','{}','[]',?,?)",
                    (STAMP, STAMP),
                )

        event.listen(app.state.engine, "after_cursor_execute", write_after_first_select)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
                base_url="http://127.0.0.1:8000",
            ) as client:
                response = await client.get("/api/export.json")
        finally:
            event.remove(app.state.engine, "after_cursor_execute", write_after_first_select)
        assert inserted
        assert response.status_code == 200
        assert response.json()["records"]["messages"] == []
        assert response.json()["records"]["suggestions"] == []
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM suggestion").fetchone() == (1,)


@pytest.mark.parametrize("path", ["/api/export.json", "/api/export.txt"])
@pytest.mark.parametrize(
    ("remote", "headers"),
    [
        ("192.0.2.4", {"origin": "http://127.0.0.1:5173"}),
        ("127.0.0.1", {"host": "attacker.example"}),
        ("127.0.0.1", {"origin": "https://attacker.example"}),
        ("127.0.0.1", {"origin": "http://127.0.0.1.attacker.example"}),
        ("127.0.0.1", {"origin": "null"}),
    ],
)
async def test_export_rejects_nonlocal_request(tmp_path, path, remote, headers):
    app = create_app(tmp_path / "access.db", qq_background=False)
    async with app.router.lifespan_context(app):
        with Session(app.state.engine) as session:
            session.add(Message(text="private notice contents", sent_at=STAMP, dedupe_key="x"))
            session.commit()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(remote, 1234)),
            base_url="http://127.0.0.1:8000",
        ) as client:
            response = await client.get(path, headers=headers)
    assert response.status_code == 403
    assert "private notice contents" not in response.text


@pytest.mark.parametrize("path", ["/api/export.json", "/api/export.txt"])
@pytest.mark.parametrize(
    ("remote", "base_url", "origin"),
    [
        ("127.0.0.1", "http://127.0.0.1:8000", None),
        ("127.0.0.1", "http://127.0.0.1:8000", "http://127.0.0.1:5173"),
        ("127.0.0.1", "http://localhost:8000", "http://localhost:5173"),
        ("::1", "http://[::1]:8000", "http://[::1]:5173"),
    ],
)
async def test_local_export_allows_expected_origins(tmp_path, path, remote, base_url, origin):
    app = create_app(tmp_path / "local.db", qq_background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(remote, 1234)),
            base_url=base_url,
        ) as client:
            response = await client.get(path, headers={"origin": origin} if origin else {})
    assert response.status_code == 200
