"""Synthetic collector protocol tests; never launch a real browser or read user data."""

import base64
import io
import json
import shutil
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from campus_inbox import attachments
from campus_inbox.connectors.chaoxing import ChaoxingEvent
from campus_inbox.connectors.chaoxing_runtime import ChaoxingRuntime
from campus_inbox.database import initialize, make_engine
from campus_inbox.models import Attachment, LocalAttachmentExtraction, Message
from campus_inbox.providers import OllamaProvider


@pytest.fixture
def runtime(tmp_path):
    engine = make_engine(tmp_path / "campus.db")
    initialize(engine)
    service = ChaoxingRuntime(engine, OllamaProvider, tmp_path)
    service.enabled = True
    service.account_id = "test-account"
    service.stdin = io.StringIO()
    yield service
    service.shutdown()
    engine.dispose()


def test_collector_acknowledges_only_after_message_is_durable(runtime):
    runtime.handle(
        {
            "type": "notice",
            "job": {
                "kind": "course_notice",
                "message_id": "n1",
                "text": "课程作业请明天提交",
                "captured_at": None,
            },
        }
    )
    ack = json.loads(runtime.stdin.getvalue())
    assert ack == {"type": "accepted", "id": "n1", "complete": True}
    with Session(runtime.engine) as session:
        assert len(session.exec(select(ChaoxingEvent)).all()) == 1
        assert session.exec(select(Message)).all() == []
    runtime.process_pending()
    with Session(runtime.engine) as session:
        assert session.exec(select(ChaoxingEvent)).one().state == "needs_time"


def docx_bytes():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>请上传源代码</w:t></w:r></w:p></w:body></w:document>",
        )
    return stream.getvalue()


def test_collected_docx_is_processed_locally_after_message_is_saved(runtime):
    content = docx_bytes()
    runtime.handle(
        {
            "type": "notice",
            "job": {
                "kind": "course_notice",
                "message_id": "docx-1",
                "text": "请核对附件要求。",
                "captured_at": "2026-11-03T09:00:00+08:00",
                "attachments": [
                    {"name": "要求.docx", "content_base64": base64.b64encode(content).decode()}
                ],
            },
        }
    )
    runtime.process_pending()
    with Session(runtime.engine) as session:
        item = session.exec(select(Attachment)).one()
        assert item.status == "ready" and item.text == "请上传源代码"
        assert item.message_id is not None
        assert "对照原文件" in item.error
        filename = f"{item.id}.docx"
    assert (Path(runtime.engine.url.database).parent / "uploads" / filename).read_bytes() == content
    runtime.process_pending()
    with Session(runtime.engine) as session:
        assert len(session.exec(select(Attachment)).all()) == 1


def test_collected_partial_pdf_ocr_does_not_become_ready_evidence(runtime, monkeypatch):
    monkeypatch.setattr(attachments, "supports_local_ocr", lambda: True)
    monkeypatch.setattr(
        attachments, "extract_local_ocr", lambda _path: ("第一页面文字", "PDF 仅识别了部分页面。")
    )
    runtime.handle(
        {
            "type": "notice",
            "job": {
                "kind": "course_notice",
                "message_id": "pdf-1",
                "text": "请核对附件要求。",
                "captured_at": "2026-11-03T09:00:00+08:00",
                "attachments": [{"name": "要求.pdf", "content_base64": "cGRm"}],
            },
        }
    )
    runtime.process_pending()
    with Session(runtime.engine) as session:
        item = session.exec(select(Attachment)).one()
        assert item.status == "failed"
        assert item.text == "第一页面文字"
        assert "部分页面" in item.error
        assert session.exec(select(LocalAttachmentExtraction)).one().state == "complete"


def test_broken_docx_preserves_original_and_does_not_retry_forever(runtime, monkeypatch):
    runtime.handle(
        {
            "type": "notice",
            "job": {
                "kind": "course_notice",
                "message_id": "docx-bad",
                "text": "请核对附件要求。",
                "captured_at": "2026-11-03T09:00:00+08:00",
                "attachments": [
                    {"name": "错误.docx", "content_base64": base64.b64encode(b"bad").decode()}
                ],
            },
        }
    )
    runtime.process_pending()
    with Session(runtime.engine) as session:
        item = session.exec(select(Attachment)).one()
        assert item.status == "failed" and item.message_id is not None
        assert (
            Path(runtime.engine.url.database).parent / "uploads" / f"{item.id}.docx"
        ).read_bytes() == b"bad"
    from campus_inbox import attachments

    calls = []

    def unexpected(_path):
        calls.append(True)
        raise ValueError("unexpected retry")

    monkeypatch.setattr(attachments, "extract_local_docx", unexpected)
    runtime.process_pending()
    assert calls == []


def test_collected_docx_queue_survives_restart_and_does_not_hold_write_lock(
    runtime, monkeypatch, tmp_path
):
    from campus_inbox import attachments

    content = docx_bytes()
    event_id, _ = runtime.intake.receive(
        "test-account",
        {
            "kind": "course_notice",
            "message_id": "restart",
            "text": "请核对附件要求。",
            "captured_at": "2026-11-03T09:00:00+08:00",
            "attachments": [
                {"name": "要求.docx", "content_base64": base64.b64encode(content).decode()}
            ],
        },
    )
    assert runtime.intake.process(event_id) == "done"
    original = attachments.extract_local_docx

    def parse_without_lock(path):
        with runtime.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            connection.rollback()
        return original(path)

    monkeypatch.setattr(attachments, "extract_local_docx", parse_without_lock)
    restarted = ChaoxingRuntime(runtime.engine, OllamaProvider, tmp_path)
    restarted.enabled = True
    restarted.process_pending()
    with Session(runtime.engine) as session:
        assert session.exec(select(Attachment)).one().text == "请上传源代码"
    restarted.shutdown()


def test_background_extraction_does_not_overwrite_a_newer_manual_result(runtime, monkeypatch):
    from campus_inbox import attachments

    event_id, _ = runtime.intake.receive(
        "test-account",
        {
            "kind": "course_notice",
            "message_id": "race",
            "text": "请核对附件要求。",
            "captured_at": "2026-11-03T09:00:00+08:00",
            "attachments": [
                {"name": "要求.docx", "content_base64": base64.b64encode(docx_bytes()).decode()}
            ],
        },
    )
    assert runtime.intake.process(event_id) == "done"

    def concurrent_manual_update(_path):
        with Session(runtime.engine) as session:
            item = session.exec(select(Attachment)).one()
            item.status, item.text = "ready", "用户核对后的内容"
            session.add(item)
            session.commit()
        return "旧后台结果", ""

    monkeypatch.setattr(attachments, "extract_local_docx", concurrent_manual_update)
    runtime.process_pending()
    with Session(runtime.engine) as session:
        assert session.exec(select(Attachment)).one().text == "用户核对后的内容"


def test_existing_unprocessed_collected_docx_is_enqueued_after_upgrade(runtime):
    event_id, _ = runtime.intake.receive(
        "test-account",
        {
            "kind": "course_notice",
            "message_id": "before-upgrade",
            "text": "请核对附件要求。",
            "captured_at": "2026-11-03T09:00:00+08:00",
            "attachments": [
                {"name": "要求.DOCX", "content_base64": base64.b64encode(docx_bytes()).decode()}
            ],
        },
    )
    assert runtime.intake.process(event_id) == "done"
    with Session(runtime.engine) as session:
        session.delete(session.exec(select(LocalAttachmentExtraction)).one())
        session.commit()
    runtime.process_pending()
    with Session(runtime.engine) as session:
        item = session.exec(select(Attachment)).one()
        assert item.status == "ready" and item.text == "请上传源代码"


def test_backfill_ignores_unrelated_uploaded_files(runtime):
    with Session(runtime.engine) as session:
        session.add(
            Attachment(
                filename="其他.docx",
                size=3,
                status="failed",
                error="原附件已保存，内容尚未解析，请下载后核对。",
            )
        )
        session.commit()
    runtime.process_pending()
    with Session(runtime.engine) as session:
        assert session.exec(select(LocalAttachmentExtraction)).all() == []
        assert session.exec(select(Attachment)).one().status == "failed"


def test_paused_sync_retains_inflight_docx_for_later_retry(runtime, monkeypatch):
    from campus_inbox import attachments

    event_id, _ = runtime.intake.receive(
        "test-account",
        {
            "kind": "course_notice",
            "message_id": "pause-docx",
            "text": "请核对附件要求。",
            "captured_at": "2026-11-03T09:00:00+08:00",
            "attachments": [
                {"name": "要求.docx", "content_base64": base64.b64encode(docx_bytes()).decode()}
            ],
        },
    )
    assert runtime.intake.process(event_id) == "done"
    original = attachments.extract_local_docx

    def pause_during_parse(path):
        result = original(path)
        runtime.pause()
        return result

    monkeypatch.setattr(attachments, "extract_local_docx", pause_during_parse)
    runtime.process_pending()
    with Session(runtime.engine) as session:
        assert session.exec(select(Attachment)).one().status == "failed"
        assert session.exec(select(LocalAttachmentExtraction)).one().state == "pending"
    monkeypatch.setattr(attachments, "extract_local_docx", original)
    runtime.stopping.clear()
    runtime.enabled = True
    runtime.process_pending()
    with Session(runtime.engine) as session:
        assert session.exec(select(Attachment)).one().status == "ready"


def test_invalid_or_oversized_event_never_receives_ack(runtime):
    runtime.handle(
        {
            "type": "notice",
            "job": {"kind": "course_notice", "message_id": "x", "text": "a" * (13 * 1024 * 1024)},
        }
    )
    assert runtime.stdin.getvalue() == ""
    with Session(runtime.engine) as session:
        assert session.exec(select(ChaoxingEvent)).all() == []
    assert runtime.status()["error"]


def test_worker_error_never_exposes_private_details(runtime):
    runtime.handle({"type": "status", "state": "retrying", "reason": "token-secret-message-text"})
    assert "token-secret" not in json.dumps(runtime.status(), ensure_ascii=False)


def test_source_health_reports_partial_coverage_without_leaking_worker_details(runtime):
    runtime.handle(
        {
            "type": "source",
            "source": "notice",
            "state": "connected",
            "checked": True,
            "coverage": "complete",
            "pending_count": 2,
            "media_pending_count": 1,
            "reason": "private-secret",
        }
    )
    notice = runtime.status()["sources"]["notice"]
    assert notice["state"] == "partial" and notice["pending_count"] == 2
    assert notice["last_checked"] is not None and notice["last_complete"] is None
    assert "private-secret" not in json.dumps(runtime.status())
    runtime.handle(
        {
            "type": "source",
            "source": "notice",
            "state": "connected",
            "checked": True,
            "coverage": "complete",
            "pending_count": 0,
            "media_pending_count": 0,
        }
    )
    assert runtime.status()["sources"]["notice"]["last_complete"] is not None


def test_task_center_is_not_complete_until_course_discovery_is_complete(runtime):
    runtime.handle(
        {
            "type": "source",
            "source": "task_center",
            "state": "connected",
            "checked": True,
            "coverage": "complete",
            "discovery_complete": False,
        }
    )
    current = runtime.status()["sources"]["task_center"]
    assert current["state"] == "partial" and current["last_complete"] is None
    runtime.handle(
        {
            "type": "source",
            "source": "task_center",
            "state": "connected",
            "checked": True,
            "coverage": "complete",
            "discovery_complete": True,
        }
    )
    assert runtime.status()["sources"]["task_center"]["last_complete"] is not None


def test_malformed_source_health_payload_is_ignored_without_stopping_collector(runtime):
    runtime.handle({"type": "source", "source": "notice", "state": [], "coverage": {}})
    runtime.handle({"type": "source", "source": "notice", "state": "partial", "coverage": {}})
    assert runtime.status()["sources"]["notice"]["state"] == "partial"


def test_failed_source_does_not_claim_a_successful_check(runtime):
    runtime.handle(
        {
            "type": "source",
            "source": "chat",
            "state": "error",
            "checked": True,
            "coverage": "complete",
        }
    )
    source = runtime.status()["sources"]["chat"]
    assert source["last_checked"] is None and source["coverage"] != "complete"


def test_expired_heartbeat_is_reported_without_claiming_a_connection(runtime):
    class LiveProcess:
        def poll(self):
            return None

    runtime.process = LiveProcess()
    runtime.state = "connected"
    runtime.qr_ready = True
    runtime.heartbeat_tick = time.monotonic() - 91
    status = runtime.status()
    assert status["freshness"] == "stale" and status["state"] == "error" and not status["qr_ready"]
    runtime.process = None


def test_new_worker_is_starting_until_its_first_heartbeat(runtime):
    class LiveProcess:
        def poll(self):
            return None

    runtime.process = LiveProcess()
    runtime.heartbeat_tick = time.monotonic()
    runtime.last_heartbeat = None
    assert runtime.status()["freshness"] == "starting"
    runtime.handle({"type": "heartbeat"})
    assert runtime.status()["freshness"] == "fresh"
    runtime.process = None


def test_reconnect_stops_stale_worker_before_starting_a_new_one(runtime):
    class LiveProcess:
        def poll(self):
            return None

    runtime.process = LiveProcess()
    runtime.heartbeat_tick = time.monotonic() - 91

    def stop():
        runtime.process = None

    with (
        patch.object(runtime, "_stop_process", side_effect=stop) as stopped,
        patch("campus_inbox.connectors.chaoxing_runtime.node_binary", return_value=None),
    ):
        result = runtime.start()
    stopped.assert_called_once()
    assert result["state"] == "error" and "Node.js" in result["error"]


def test_replaced_worker_cannot_deliver_old_account_data(runtime):
    class Process:
        def __init__(self):
            self.stdout = io.StringIO(
                json.dumps(
                    {
                        "type": "notice",
                        "job": {"kind": "course_notice", "message_id": "old", "text": "old data"},
                    }
                )
                + "\n"
            )

        def poll(self):
            return None

    old, active = Process(), Process()
    runtime.process = active
    runtime._read(old)
    with Session(runtime.engine) as session:
        assert session.exec(select(ChaoxingEvent)).all() == []
    runtime.process = None


def test_status_counts_events_without_loading_attachment_payloads(runtime):
    runtime.intake.receive("test-account", {"kind": "chat", "message_id": "one", "text": "作业"})
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lower())

    event.listen(runtime.engine, "before_cursor_execute", capture)
    try:
        assert runtime.status()["counts"]["pending"] == 1
    finally:
        event.remove(runtime.engine, "before_cursor_execute", capture)
    assert all("payload" not in statement for statement in statements)


def test_stop_never_deletes_messages(runtime):
    runtime.handle(
        {
            "type": "notice",
            "job": {
                "kind": "course_notice",
                "message_id": "m1",
                "text": "保留原文",
                "captured_at": None,
            },
        }
    )
    runtime.pause()
    with Session(runtime.engine) as session:
        assert session.exec(select(ChaoxingEvent)).one().payload["text"] == "保留原文"


def test_sent_chat_is_filtered_and_acknowledged(runtime):
    runtime.handle(
        {
            "type": "chat",
            "job": {
                "kind": "chat",
                "message_id": "own1",
                "text": "自己发的消息",
                "direction": "sent",
            },
        }
    )
    assert json.loads(runtime.stdin.getvalue()) == {
        "type": "accepted",
        "id": "own1",
        "complete": True,
    }
    with Session(runtime.engine) as session:
        assert session.exec(select(ChaoxingEvent)).all() == []


def test_new_account_gets_a_new_identity_without_deleting_events(runtime):
    runtime.data_dir.mkdir(parents=True)
    (runtime.data_dir / "scan-state.json").write_text('{"notice_cursor": 123}', encoding="utf-8")
    runtime.handle(
        {
            "type": "notice",
            "job": {
                "kind": "course_notice",
                "message_id": "m1",
                "text": "原消息",
                "captured_at": None,
            },
        }
    )
    old_account = runtime.account_id
    runtime.forget_login()
    assert runtime.account_id != old_account
    assert not (runtime.data_dir / "scan-state.json").exists()
    assert len(list(runtime.data_dir.glob("scan-state-previous-*.json"))) == 1
    with Session(runtime.engine) as session:
        assert session.exec(select(ChaoxingEvent)).one().payload["text"] == "原消息"


def test_media_is_saved_but_not_acknowledged_as_complete(runtime):
    runtime.handle(
        {
            "type": "notice",
            "job": {
                "kind": "course_notice",
                "message_id": "media1",
                "text": "请查看附件",
                "attachments": [{"name": "报告.pdf"}],
            },
        }
    )
    assert json.loads(runtime.stdin.getvalue()) == {
        "type": "accepted",
        "id": "media1",
        "complete": False,
    }
    with Session(runtime.engine) as session:
        assert (
            session.exec(select(ChaoxingEvent)).one().payload["attachments"][0]["name"]
            == "报告.pdf"
        )


def test_open_login_window_restarts_existing_worker_in_visible_mode(runtime):
    class LiveProcess:
        def poll(self):
            return None

        def wait(self, timeout):
            return 0

    runtime.process = LiveProcess()
    runtime.start(visible=True)
    command = json.loads(runtime.stdin.getvalue())
    assert command["type"] == "start" and command["visible"] is True
    runtime.process = None


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js unavailable")
def test_synthetic_collector_process_reaches_main_inbox(tmp_path):
    worker = tmp_path / "worker" / "desktop-sync"
    worker.mkdir(parents=True)
    dependency = worker / "node_modules" / "playwright"
    dependency.mkdir(parents=True)
    (dependency / "index.js").write_text("module.exports = {};", encoding="utf-8")
    (worker / "worker.cjs").write_text(
        "const rl=require('readline').createInterface({input:process.stdin});"
        "rl.on('line',s=>{let m=JSON.parse(s);"
        "if(m.type==='start'){console.log(JSON.stringify({type:'status',state:'connected'}));"
        "console.log(JSON.stringify({type:'heartbeat'}));"
        "console.log(JSON.stringify({type:'source',source:'notice',state:'partial',checked:true,"
        "coverage:'partial',pending_count:1}));"
        "console.log(JSON.stringify({type:'notice',job:{kind:'course_notice',message_id:'demo1',"
        "text:'软件工程：请提交实验报告',course:'软件工程',captured_at:'2026-11-03T09:00:00+08:00'}}))}"
        "if(m.type==='close')process.exit(0)});",
        encoding="utf-8",
    )
    engine = make_engine(tmp_path / "main.db")
    initialize(engine)
    service = ChaoxingRuntime(engine, OllamaProvider, tmp_path, worker_root=worker.parent)
    try:
        service.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with Session(engine) as session:
                if session.exec(select(ChaoxingEvent).where(ChaoxingEvent.state == "done")).first():
                    break
            time.sleep(0.05)
        with Session(engine) as session:
            assert session.exec(select(Message)).one().text == "软件工程：请提交实验报告"
            assert session.exec(select(ChaoxingEvent)).one().state == "done"
        status = service.status()
        assert status["freshness"] == "fresh"
        assert status["sources"]["notice"]["pending_count"] == 1
    finally:
        service.shutdown()
        engine.dispose()


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js unavailable")
def test_missing_collector_dependency_is_reported_before_start(tmp_path):
    worker = tmp_path / "worker" / "desktop-sync"
    worker.mkdir(parents=True)
    (worker / "worker.cjs").write_text("", encoding="utf-8")
    engine = make_engine(tmp_path / "main.db")
    initialize(engine)
    service = ChaoxingRuntime(engine, OllamaProvider, tmp_path, worker_root=worker.parent)
    try:
        result = service.start()
        assert result["state"] == "error"
        assert "Playwright" in result["error"]
        assert service.process is None
    finally:
        service.shutdown()
        engine.dispose()


def test_temporary_session_file_lock_is_retried_without_losing_previous_session(runtime):
    runtime.data_dir.mkdir(parents=True)
    target = runtime.data_dir / "session.bin"
    target.write_bytes(b"old-encrypted")
    original = Path.replace
    attempts = []

    def replace(source, destination):
        attempts.append(1)
        if len(attempts) < 3:
            assert target.read_bytes() == b"old-encrypted"
            raise PermissionError("temporary sharing violation")
        return original(source, destination)

    with (
        patch("campus_inbox.connectors.session_crypto.seal", return_value=b"new-encrypted"),
        patch.object(Path, "replace", replace),
        patch("campus_inbox.connectors.chaoxing_runtime.time.sleep"),
    ):
        runtime._save_session({"cookies": []})
    assert target.read_bytes() == b"new-encrypted"
    assert len(attempts) == 3


def test_mac_node_discovery_works_without_shell_path():
    import sys

    from campus_inbox.connectors.chaoxing_runtime import node_binary

    with (
        patch.object(sys, "platform", "darwin"),
        patch("campus_inbox.connectors.chaoxing_runtime.shutil.which", return_value=None),
        patch.object(
            Path, "is_file", lambda p: p.as_posix() == "/opt/homebrew/opt/node@24/bin/node"
        ),
        patch("campus_inbox.connectors.chaoxing_runtime.os.access", return_value=True),
    ):
        assert node_binary() == "/opt/homebrew/opt/node@24/bin/node"


def test_windows_node_discovery_still_uses_path():
    import sys

    from campus_inbox.connectors.chaoxing_runtime import node_binary

    with (
        patch.object(sys, "platform", "win32"),
        patch("campus_inbox.connectors.chaoxing_runtime.shutil.which", return_value="node.exe"),
    ):
        assert node_binary() == "node.exe"


def test_vault_failure_warning_survives_connected_status(runtime):
    with patch.object(runtime, "_save_session", side_effect=OSError("synthetic-secret")):
        runtime.handle({"type": "session", "session": {"cookies": []}})
    runtime.handle({"type": "status", "state": "connected", "checked": True})
    assert runtime.status()["session_error"]
    assert "synthetic-secret" not in runtime.status()["session_error"]
    with patch.object(runtime, "_save_session"):
        runtime.handle({"type": "session", "session": {"cookies": []}})
    assert not runtime.status()["session_error"]


@pytest.mark.parametrize(
    "job",
    [
        {"kind": "chat", "message_id": "filtered-chat", "text": "聊天不入箱"},
        {
            "kind": "course_notice",
            "message_id": "filtered-old",
            "text": "旧通知不入箱",
            "captured_at": "2020-01-01T00:00:00+08:00",
        },
    ],
)
def test_scope_filter_acknowledges_without_storing_or_analyzing(runtime, job):
    with patch.object(runtime.intake, "receive") as receive:
        runtime.handle({"type": "chat" if job["kind"] == "chat" else "notice", "job": job})
    receive.assert_not_called()
    assert json.loads(runtime.stdin.getvalue()) == {
        "type": "accepted",
        "id": job["message_id"],
        "complete": True,
    }
