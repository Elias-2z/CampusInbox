import asyncio
import io
import json
import threading
import zipfile
from datetime import datetime

import httpx
import pytest
from sqlmodel import Session

from campus_inbox import attachments
from campus_inbox.database import initialize, make_engine
from campus_inbox.extraction import extract_rules
from campus_inbox.main import create_app
from campus_inbox.mineru import (
    MinerUError,
    MinerUFailed,
    MinerUProvider,
    markdown_from_zip,
    safe_url,
)
from campus_inbox.models import Attachment

pytestmark = pytest.mark.anyio
NOTICE = "软件工程：请于明天18:00前提交需求分析报告，提交至学习通。"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeMinerU:
    calls = 0
    polls = 0
    fail = False

    def submit(self, path, filename, attachment_id):
        assert path.read_bytes() == b"fake-pdf"
        FakeMinerU.calls += 1
        return "test-batch"

    def poll(self, batch_id):
        FakeMinerU.polls += 1
        if FakeMinerU.fail:
            raise MinerUFailed("文档损坏")
        return None if FakeMinerU.polls == 1 else NOTICE


@pytest.fixture
async def context(tmp_path, monkeypatch):
    monkeypatch.setattr(attachments, "api_key", lambda: "test-token")
    FakeMinerU.calls, FakeMinerU.polls, FakeMinerU.fail = 0, 0, False
    app = create_app(tmp_path / "test.db", mineru_factory=FakeMinerU)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, tmp_path


async def upload(client, name="通知.txt", content=None, consent=False):
    return await client.post(
        "/api/imports/files",
        params={"filename": name, "consent": str(consent).lower()},
        content=NOTICE.encode() if content is None else content,
        headers={"Content-Type": "application/octet-stream"},
    )


async def test_local_text_review_preserves_original_and_deduplicates_attachment(context):
    client, _ = context
    response = await upload(client, content=NOTICE.encode("gb18030"))
    assert response.status_code == 200, response.text
    file = response.json()
    assert file["status"] == "ready" and file["text"] == NOTICE
    assert FakeMinerU.calls == 0
    assert (await client.get("/api/tasks")).json() == []
    body = {
        "text": NOTICE.replace("需求分析报告", "需求报告"),
        "attachment_id": file["id"],
        "sent_at": "2026-11-01T12:00:00+08:00",
    }
    response = await client.post("/api/inbox", json=body)
    assert response.status_code == 200, response.text
    item = response.json()
    assert item["message"]["attachments"][0]["id"] == file["id"]
    assert (await client.get("/api/tasks")).json() == []
    repeated = await client.post("/api/inbox", json={**body, "text": NOTICE})
    assert repeated.json()["id"] == item["id"]
    task = await client.post(f"/api/suggestions/{item['id']}/accept", json=item["fields"])
    assert task.status_code == 200, task.text
    detail = (await client.get(f"/api/tasks/{task.json()['id']}")).json()
    assert detail["source"]["attachments"][0]["filename"] == "通知.txt"
    original = await client.get(f"/api/attachments/{file['id']}/download")
    assert original.content == NOTICE.encode("gb18030")
    assert "attachment" in original.headers["content-disposition"]
    parsed = await client.get(f"/api/attachments/{file['id']}/text")
    assert parsed.status_code == 200
    assert parsed.json()["text"] == NOTICE
    assert parsed.headers["cache-control"] == "no-store"
    assert (await client.get("/api/imports/files")).json() == []


async def test_cloud_consent_and_configuration_checked_before_saving(context, monkeypatch):
    client, path = context
    assert (await upload(client, "notice.pdf", b"fake-pdf")).status_code == 422
    monkeypatch.setattr(attachments, "api_key", lambda: "")
    assert (await upload(client, "notice.pdf", b"fake-pdf", True)).status_code == 503
    assert (await client.get("/api/imports/capabilities")).json()["mineru_configured"] is False
    assert not (path / "uploads").exists()
    assert (await upload(client)).status_code == 200


async def test_local_ocr_preserves_original_without_cloud_or_consent(context, monkeypatch):
    client, _ = context
    monkeypatch.setattr(attachments, "supports_local_ocr", lambda: True)
    monkeypatch.setattr(attachments, "extract_local_ocr", lambda path: ("识别后的通知", ""))
    response = await client.post(
        "/api/imports/files",
        params={"filename": "通知.png", "extractor": "local"},
        content=b"synthetic-image",
    )
    assert response.status_code == 200, response.text
    item = response.json()
    assert item["status"] == "ready" and item["text"] == "识别后的通知"
    assert FakeMinerU.calls == 0
    assert (
        await client.get(f"/api/attachments/{item['id']}/download")
    ).content == b"synthetic-image"


def synthetic_docx(document):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)
    return output.getvalue()


async def test_docx_can_be_read_locally_without_windows_or_cloud(context, monkeypatch):
    client, _ = context
    monkeypatch.setattr(attachments, "supports_local_ocr", lambda: False)
    content = synthetic_docx(
        """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>作业通知</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>提交报告</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>"""
    )
    capabilities = (await client.get("/api/imports/capabilities")).json()
    assert capabilities["local_docx"] is True
    response = await client.post(
        "/api/imports/files",
        params={"filename": "通知.docx", "extractor": "local"},
        content=content,
    )
    assert response.status_code == 200, response.text
    item = response.json()
    assert item["status"] == "ready" and item["text"] == "作业通知\n提交报告"
    assert FakeMinerU.calls == 0
    assert (await client.get(f"/api/attachments/{item['id']}/download")).content == content


async def test_unsafe_or_broken_docx_is_kept_but_not_sent_to_cloud(context, monkeypatch):
    client, _ = context
    monkeypatch.setattr(attachments, "supports_local_ocr", lambda: False)
    for content in (b"not-a-zip", synthetic_docx('<!DOCTYPE x [<!ENTITY e "unsafe">]><x>&e;</x>')):
        response = await client.post(
            "/api/imports/files",
            params={"filename": "通知.docx", "extractor": "local"},
            content=content,
        )
        assert response.status_code == 200
        item = response.json()
        assert item["status"] == "failed"
        assert (await client.get(f"/api/attachments/{item['id']}/download")).content == content
        assert (await client.post(f"/api/imports/files/{item['id']}/retry")).status_code == 422
        assert (await client.post(f"/api/imports/files/{item['id']}/local-ocr")).json()[
            "status"
        ] == "failed"
    assert FakeMinerU.calls == 0


async def test_docx_materials_are_reviewable_after_task_confirmation(context, monkeypatch):
    client, _ = context
    monkeypatch.setattr(attachments, "supports_local_ocr", lambda: False)
    content = synthetic_docx(
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>请上传源代码。</w:t></w:r></w:p></w:body></w:document>"
    )
    result = await client.post(
        "/api/imports/files",
        params={"filename": "要求.docx", "extractor": "local"},
        content=content,
    )
    item = result.json()
    inbox = await client.post(
        "/api/inbox",
        json={
            "text": "软件工程：请提交实验报告。",
            "attachment_id": item["id"],
            "sent_at": "2026-11-03T09:00:00+08:00",
        },
    )
    assert inbox.status_code == 200, inbox.text
    suggestion = inbox.json()
    assert (await client.get("/api/tasks")).json() == []
    accepted = await client.post(
        f"/api/suggestions/{suggestion['id']}/accept", json=suggestion["fields"]
    )
    assert accepted.status_code == 200, accepted.text
    task_id = accepted.json()["id"]
    candidates = (await client.get(f"/api/tasks/{task_id}/material-suggestions")).json()["items"]
    assert any(x["text"] == "源代码" and x["source"] == "附件：要求.docx" for x in candidates)
    assert (await client.get(f"/api/tasks/{task_id}")).json()["materials"] == []
    assert FakeMinerU.calls == 0


async def test_single_attachment_can_suggest_a_task_with_its_own_evidence(context):
    client, _ = context
    content = "软件工程：请于2026年12月8日18:00前提交实验报告。"
    attachment = (await upload(client, "要求.txt", content.encode())).json()
    response = await client.post(
        "/api/inbox",
        json={
            "text": "请阅读附件说明。",
            "attachment_id": attachment["id"],
            "sent_at": "2026-11-03T09:00:00+08:00",
        },
    )
    assert response.status_code == 200, response.text
    suggestion = response.json()
    assert suggestion["action"] == "create"
    assert suggestion["fields"]["title"] == "实验报告"
    assert suggestion["fields"]["due_at"] == "2026-12-08T10:00:00+00:00"
    assert suggestion["evidence"]["title"]["method"] == "attachment"
    assert "要求.txt" in suggestion["evidence"]["title"]["quote"]
    assert (await client.get("/api/tasks")).json() == []
    accepted = await client.post(
        f"/api/suggestions/{suggestion['id']}/accept", json=suggestion["fields"]
    )
    assert accepted.status_code == 200, accepted.text
    assert (await client.get(f"/api/tasks/{accepted.json()['id']}")).json()["source"][
        "attachments"
    ][0]["id"] == attachment["id"]


async def test_attachment_relative_dates_are_not_anchored_to_notice_time(context):
    client, _ = context
    attachment = (await upload(client, "要求.txt", "请于明天18:00前提交实验报告。".encode())).json()
    response = await client.post(
        "/api/inbox",
        json={
            "text": "请阅读附件说明。",
            "attachment_id": attachment["id"],
            "sent_at": "2026-11-03T09:00:00+08:00",
        },
    )
    suggestion = response.json()
    assert suggestion["action"] == "create"
    assert suggestion["fields"]["due_at"] is None and suggestion["fields"]["due_date"] is None
    assert any("相对日期" in warning for warning in suggestion["warnings"])


async def test_attachment_with_change_notice_does_not_create_task(context):
    client, _ = context
    attachment = (await upload(client, "变更.txt", "实验报告延期至2026年12月8日。".encode())).json()
    response = await client.post(
        "/api/inbox",
        json={
            "text": "请阅读附件说明。",
            "attachment_id": attachment["id"],
            "sent_at": "2026-11-03T09:00:00+08:00",
        },
    )
    assert response.json()["action"] != "create"
    assert (await client.get("/api/tasks")).json() == []


async def test_attachment_conflicting_absolute_deadline_requires_review(context):
    client, _ = context
    attachment = (
        await upload(client, "要求.txt", "请于2026年12月9日18:00前提交实验报告。".encode())
    ).json()
    response = await client.post(
        "/api/inbox",
        json={
            "text": "请于2026年12月8日18:00前提交实验报告。",
            "attachment_id": attachment["id"],
            "sent_at": "2026-11-03T09:00:00+08:00",
        },
    )
    item = response.json()
    assert item["fields"]["due_at"] is None and item["fields"]["due_date"] is None
    assert any("附件" in warning and "不一致" in warning for warning in item["warnings"])


async def test_unmatched_attachment_task_is_not_merged_into_notice(context):
    client, _ = context
    attachment = (
        await upload(client, "要求.txt", "请于2026年12月9日18:00前提交报名材料。".encode())
    ).json()
    response = await client.post(
        "/api/inbox",
        json={
            "text": "请提交实验报告。",
            "attachment_id": attachment["id"],
            "sent_at": "2026-11-03T09:00:00+08:00",
        },
    )
    item = response.json()
    assert item["fields"]["title"] == "实验报告"
    assert item["fields"]["due_at"] is None and item["fields"]["due_date"] is None
    assert any("附件" in warning and "名称" in warning for warning in item["warnings"])


async def test_late_attachment_parse_is_used_only_after_reanalysis(context):
    client, directory = context
    original = (
        await client.post(
            "/api/inbox",
            json={
                "text": "请阅读附件说明。",
                "sent_at": "2026-11-03T09:00:00+08:00",
            },
        )
    ).json()
    engine = make_engine(directory / "test.db")
    try:
        with Session(engine) as session:
            item = Attachment(
                filename="要求.txt",
                size=10,
                status="ready",
                text="请提交实验报告。",
                message_id=original["message"]["id"],
            )
            session.add(item)
            session.commit()
        fresh = await client.post(f"/api/inbox/{original['id']}/analyze")
        assert fresh.status_code == 200 and fresh.json()["action"] == "create"
        assert fresh.json()["evidence"]["title"]["method"] == "attachment"
        assert (await client.get("/api/tasks")).json() == []
    finally:
        engine.dispose()


async def test_multiple_attachments_are_not_combined_into_one_task(context):
    client, directory = context
    original = (
        await client.post(
            "/api/inbox",
            json={
                "text": "请阅读附件说明。",
                "sent_at": "2026-11-03T09:00:00+08:00",
            },
        )
    ).json()
    engine = make_engine(directory / "test.db")
    try:
        with Session(engine) as session:
            session.add_all(
                [
                    Attachment(
                        filename="任务一.txt",
                        size=10,
                        status="ready",
                        text="请提交实验报告。",
                        message_id=original["message"]["id"],
                    ),
                    Attachment(
                        filename="任务二.txt",
                        size=10,
                        status="ready",
                        text="请提交代码。",
                        message_id=original["message"]["id"],
                    ),
                ]
            )
            session.commit()
        fresh = await client.post(f"/api/inbox/{original['id']}/analyze")
        assert fresh.status_code == 200 and fresh.json()["action"] == "ignore"
        assert (await client.get("/api/tasks")).json() == []
    finally:
        engine.dispose()


async def test_local_ocr_failure_keeps_original_and_can_be_retried(context, monkeypatch):
    client, _ = context
    monkeypatch.setattr(attachments, "supports_local_ocr", lambda: True)

    def fail(_path):
        raise ValueError("no OCR language")

    monkeypatch.setattr(attachments, "extract_local_ocr", fail)
    response = await client.post(
        "/api/imports/files",
        params={"filename": "通知.png", "extractor": "local"},
        content=b"image",
    )
    item = response.json()
    assert response.status_code == 200 and item["status"] == "failed"
    assert (await client.get(f"/api/attachments/{item['id']}/download")).content == b"image"
    monkeypatch.setattr(attachments, "extract_local_ocr", lambda path: ("重新识别成功", ""))
    retry = await client.post(f"/api/imports/files/{item['id']}/local-ocr")
    assert retry.status_code == 200 and retry.json()["text"] == "重新识别成功"


async def test_locally_uploaded_file_is_not_sent_to_mineru_without_new_consent(
    context, monkeypatch
):
    client, _ = context
    monkeypatch.setattr(attachments, "supports_local_ocr", lambda: True)

    def fail(_path):
        raise ValueError("no OCR language")

    monkeypatch.setattr(attachments, "extract_local_ocr", fail)
    result = await client.post(
        "/api/imports/files",
        params={"filename": "通知.pdf", "extractor": "local"},
        content=b"fake-pdf",
    )
    item = result.json()
    assert item["status"] == "failed"
    refused = await client.post(f"/api/imports/files/{item['id']}/retry")
    assert refused.status_code == 422
    assert FakeMinerU.calls == 0
    accepted = await client.post(f"/api/imports/files/{item['id']}/retry?consent=true")
    assert accepted.status_code == 200 and accepted.json()["status"] == "parsing"
    assert FakeMinerU.calls == 1


async def test_bmp_cannot_be_sent_to_unsupported_cloud_extractor(context, monkeypatch):
    client, _ = context
    monkeypatch.setattr(attachments, "supports_local_ocr", lambda: True)

    def fail(_path):
        raise ValueError("failed")

    monkeypatch.setattr(attachments, "extract_local_ocr", fail)
    result = await client.post(
        "/api/imports/files", params={"filename": "通知.bmp", "extractor": "local"}, content=b"bmp"
    )
    assert result.json()["status"] == "failed"
    refused = await client.post(f"/api/imports/files/{result.json()['id']}/retry?consent=true")
    assert refused.status_code == 422
    assert FakeMinerU.calls == 0


async def test_linked_unreadable_attachment_can_be_reprocessed_locally(context, monkeypatch):
    client, directory = context
    monkeypatch.setattr(attachments, "supports_local_ocr", lambda: True)

    def fail(_path):
        raise ValueError("failed")

    monkeypatch.setattr(attachments, "extract_local_ocr", fail)
    item = (
        await client.post(
            "/api/imports/files",
            params={"filename": "通知.png", "extractor": "local"},
            content=b"image",
        )
    ).json()
    suggestion = (
        await client.post(
            "/api/inbox",
            json={"text": "软件工程：请提交实验报告", "sent_at": "2026-11-03T09:00:00+08:00"},
        )
    ).json()
    engine = make_engine(directory / "test.db")
    try:
        with Session(engine) as session:
            linked = session.get(Attachment, item["id"])
            linked.message_id = suggestion["message"]["id"]
            session.add(linked)
            session.commit()
    finally:
        engine.dispose()
    monkeypatch.setattr(attachments, "extract_local_ocr", lambda path: ("识别后的要求", ""))
    response = await client.post(f"/api/imports/files/{item['id']}/local-ocr")
    assert response.status_code == 200 and response.json()["text"] == "识别后的要求"
    assert (await client.get(f"/api/attachments/{item['id']}/text")).json()[
        "text"
    ] == "识别后的要求"


async def test_cloud_poll_resume_after_restart_without_duplicate_submission(context):
    client, path = context
    response = await upload(client, "notice.pdf", b"fake-pdf", True)
    assert response.status_code == 200, response.text
    file = response.json()
    assert file["status"] == "parsing"
    assert FakeMinerU.calls == 1
    poll_url = f"/api/imports/files/{file['id']}/poll"
    assert (await client.post(poll_url)).json()["status"] == "parsing"
    reopened = create_app(path / "test.db", mineru_factory=FakeMinerU)
    async with reopened.router.lifespan_context(reopened):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=reopened), base_url="http://test"
        ) as other:
            assert (await other.get("/api/imports/files")).json()[0]["id"] == file["id"]
            done = (await other.post(poll_url)).json()
            assert done["status"] == "ready" and done["text"] == NOTICE
            assert FakeMinerU.calls == 1
            assert (await other.get("/api/tasks")).json() == []


async def test_failed_cloud_parse_can_be_retried(context):
    client, _ = context
    file = (await upload(client, "notice.pdf", b"fake-pdf", True)).json()
    FakeMinerU.fail = True
    failed = await client.post(f"/api/imports/files/{file['id']}/poll")
    assert failed.json()["status"] == "failed"
    assert "文档损坏" in failed.json()["error"]
    retry_url = f"/api/imports/files/{file['id']}/retry"
    assert (await client.post(retry_url)).json()["status"] == "parsing"
    assert FakeMinerU.calls == 2
    await client.post(retry_url)
    assert FakeMinerU.calls == 2


@pytest.mark.parametrize(
    "name,content", [("../a.txt", b"x"), ("a.svg", b"x"), ("a.txt", b""), ("a.txt", b"\x00")]
)
async def test_invalid_files_never_leave_saved_bytes(context, name, content):
    client, path = context
    assert (await upload(client, name, content)).status_code == 422
    assert (await client.get("/api/imports/files")).json() == []
    assert not list((path / "uploads").glob("*"))


async def test_upload_limit_cleans_partial_file(context, monkeypatch):
    client, path = context
    monkeypatch.setattr(attachments, "MAX_UPLOAD_BYTES", 4)
    assert (await upload(client, content=b"12345")).status_code == 413
    assert not list((path / "uploads").glob("*"))


async def test_unfinished_extraction_cannot_enter_inbox(context):
    client, _ = context
    file = (await upload(client, "notice.pdf", b"fake-pdf", True)).json()
    response = await client.post(
        "/api/inbox",
        json={
            "attachment_id": file["id"],
            "text": NOTICE,
            "sent_at": "2026-11-01T12:00:00+08:00",
        },
    )
    assert response.status_code == 409


def zip_result(text=NOTICE):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("result/full.md", text)
    return buffer.getvalue()


def test_provider_api_contract_never_leaks_token_to_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("MINERU_API_KEY", "private-test-token")
    path = tmp_path / "a.pdf"
    path.write_bytes(b"fake-pdf")

    def handle(request):
        host = request.url.host
        if host == "mineru.net":
            assert request.headers["Authorization"] == "Bearer private-test-token"
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "batch_id": "test-batch",
                            "file_urls": ["https://mineru.oss-cn-shanghai.aliyuncs.com/upload"],
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "state": "done",
                                "full_zip_url": "https://cdn-mineru.openxlab.org.cn/result.zip",
                            }
                        ]
                    },
                },
            )
        assert "Authorization" not in request.headers
        if request.method == "PUT":
            assert request.content == b"fake-pdf"
            return httpx.Response(200)
        return httpx.Response(200, content=zip_result())

    provider = MinerUProvider(httpx.MockTransport(handle))
    assert provider.submit(path, "a.pdf", "attachment-id") == "test-batch"
    assert provider.poll("test-batch") == NOTICE


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/a",
        "https://evil.example/a",
        "https://mineru.net.evil.example/a",
        "https://mineru.net:9000/a",
        None,
        [],
        {},
    ],
)
def test_provider_rejects_untrusted_storage_addresses(url):
    with pytest.raises(MinerUError):
        safe_url(url)


def test_invalid_zip_and_empty_ocr_are_recoverable():
    with pytest.raises(MinerUError):
        markdown_from_zip(b"not-a-zip")
    with pytest.raises(MinerUError):
        markdown_from_zip(zip_result(""))


def test_markdown_retains_course_in_filtered_header_and_deduplicates_margins():
    body = "请于2026年11月8日20:00前提交实验四报告，上传至学习通。"
    blocks = [
        {"type": "text", "text": body},
        {"type": "header", "text": "数据库原理实验作业通知"},
        {"type": "header", "text": "数据库原理 实验作业通知"},
        {"type": "footer", "text": "仅供测试"},
        {"type": "page_number", "text": "2026"},
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("full.md", body)
        archive.writestr("notice_content_list.json", json.dumps(blocks, ensure_ascii=False))
    text = markdown_from_zip(buffer.getvalue())
    assert text.count("数据库原理") == 1
    assert "【页眉文字】\n数据库原理实验作业通知" in text
    assert "【页脚文字】\n仅供测试" in text
    assert text.count("2026") == 1
    fields = extract_rules(
        text, datetime.fromisoformat("2026-11-01T10:00:00+08:00"), "Asia/Shanghai"
    ).fields
    assert fields["course"] == "数据库原理"
    assert fields["title"] == "实验四报告"
    assert fields["due_at"] == "2026-11-08T12:00:00+00:00"


@pytest.mark.parametrize(
    "metadata",
    [
        "invalid json",
        "{}",
        '[{"type":"header","text":null}]',
        '[{"type":[],"text":"ignored"}]',
        '[{"type":{},"text":"ignored"}]',
    ],
)
def test_optional_margin_metadata_cannot_break_valid_markdown(metadata):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("full.md", NOTICE)
        archive.writestr("notice_content_list.json", metadata)
    assert markdown_from_zip(buffer.getvalue()) == NOTICE


async def test_concurrent_retry_submits_only_one_new_remote_batch(context):
    client, _ = context
    file = (await upload(client, "notice.pdf", b"fake-pdf", True)).json()
    FakeMinerU.fail = True
    await client.post(f"/api/imports/files/{file['id']}/poll")
    responses = await asyncio.gather(
        *[client.post(f"/api/imports/files/{file['id']}/retry") for _ in range(2)]
    )
    assert all(response.status_code in {200, 409} for response in responses)
    assert any(response.status_code == 200 for response in responses)
    assert FakeMinerU.calls == 2


async def test_transient_poll_error_preserves_remote_batch_and_can_resume(context, monkeypatch):
    client, path = context
    file = (await upload(client, "notice.pdf", b"fake-pdf", True)).json()

    def unavailable(_self, _batch):
        raise MinerUError("temporary outage")

    monkeypatch.setattr(FakeMinerU, "poll", unavailable)
    failed = (await client.post(f"/api/imports/files/{file['id']}/poll")).json()
    assert failed["status"] == "parsing" and failed["error"] == "temporary outage"
    engine = make_engine(path / "test.db")
    with Session(engine) as session:
        assert session.get(Attachment, file["id"]).batch_id == "test-batch"
    engine.dispose()
    monkeypatch.setattr(FakeMinerU, "poll", lambda _self, _batch: NOTICE)
    done = (await client.post(f"/api/imports/files/{file['id']}/poll")).json()
    assert done["status"] == "ready" and done["text"] == NOTICE and done["error"] == ""
    assert FakeMinerU.calls == 1


async def test_interrupted_upload_is_recovered_and_retried_after_initialize(context):
    client, path = context
    file = (await upload(client, "notice.pdf", b"fake-pdf", True)).json()
    engine = make_engine(path / "test.db")
    with Session(engine) as session:
        attachment = session.get(Attachment, file["id"])
        attachment.batch_id = ""
        session.add(attachment)
        session.commit()
    initialize(engine)
    with Session(engine) as session:
        assert session.get(Attachment, file["id"]).status == "failed"
    engine.dispose()
    retried = (await client.post(f"/api/imports/files/{file['id']}/retry")).json()
    assert retried["status"] == "parsing"
    assert FakeMinerU.calls == 2


@pytest.mark.parametrize("stale_outcome", ["failed", "ready"])
async def test_stale_poll_cannot_overwrite_new_retry_batch(context, monkeypatch, stale_outcome):
    client, path = context
    file = (await upload(client, "notice.pdf", b"fake-pdf", True)).json()
    entered, release = threading.Event(), threading.Event()
    call_lock = threading.Lock()
    calls = 0

    def controlled_poll(_self, batch_id):
        nonlocal calls
        with call_lock:
            calls += 1
            number = calls
        if number == 1:
            entered.set()
            assert release.wait(10), "test did not release delayed poll"
            if stale_outcome == "ready":
                return "old batch result"
        raise MinerUFailed("old batch failed")

    monkeypatch.setattr(FakeMinerU, "poll", controlled_poll)
    poll_url = f"/api/imports/files/{file['id']}/poll"
    delayed = asyncio.create_task(client.post(poll_url))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        assert (await client.post(poll_url)).json()["status"] == "failed"
        monkeypatch.setattr(FakeMinerU, "submit", lambda *_args: "new-batch")
        assert (await client.post(f"/api/imports/files/{file['id']}/retry")).json()[
            "status"
        ] == "parsing"
    finally:
        release.set()
    response = await delayed
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "parsing"
    engine = make_engine(path / "test.db")
    with Session(engine) as session:
        attachment = session.get(Attachment, file["id"])
        assert attachment.batch_id == "new-batch"
        assert attachment.error == "" and attachment.text == ""
    engine.dispose()


@pytest.mark.parametrize(
    "payload",
    [None, [], "invalid", {"code": 0, "data": None}, {"code": 0, "data": []}],
)
def test_malformed_remote_json_is_recoverable_without_echoing_secrets(
    monkeypatch, tmp_path, payload
):
    monkeypatch.setenv("MINERU_API_KEY", "private-test-token")
    path = tmp_path / "notice.pdf"
    path.write_bytes(b"fake-pdf")
    provider = MinerUProvider(
        httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    )
    for operation in (
        lambda: provider.submit(path, path.name, "id"),
        lambda: provider.poll("batch"),
    ):
        with pytest.raises(MinerUError) as error:
            operation()
        assert "private-test-token" not in str(error.value)


def test_expired_remote_task_is_terminal_and_allows_resubmission(monkeypatch):
    monkeypatch.setenv("MINERU_API_KEY", "private-test-token")
    provider = MinerUProvider(
        httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"code": -60012, "msg": "task expired"})
        )
    )
    with pytest.raises(MinerUFailed, match="过期"):
        provider.poll("expired-batch")
