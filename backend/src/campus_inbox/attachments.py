"""Persist originals and extraction drafts before entering the existing inbox workflow."""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import update
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from campus_inbox.mineru import MinerUError, MinerUFailed, MinerUProvider, api_key
from campus_inbox.models import Attachment, LocalOCRSource
from campus_inbox.schemas import AttachmentRead

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
TEXT_TYPES = {".txt", ".md"}
CLOUD_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".pdf", ".doc", ".docx", ".ppt", ".pptx"}
LOCAL_OCR_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".pdf"}
LOCAL_DOCUMENT_TYPES = {".docx"}
OCR_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "windows-ocr.ps1"


def supports_local_ocr():
    return os.name == "nt" and OCR_SCRIPT.is_file()


def extract_local_docx(path: Path) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            documents = [entry for entry in entries if entry.filename == "word/document.xml"]
            if (
                len(entries) > 2000
                or len(documents) != 1
                or documents[0].file_size > 20 * 1024 * 1024
            ):
                raise ValueError("invalid document")
            xml = archive.read(documents[0]).decode("utf-8-sig")
        if "\x00" in xml or "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
            raise ValueError("unsafe document")
        root = ElementTree.fromstring(xml)
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs = []
        for paragraph in root.iter(ns + "p"):
            parts = []
            for element in paragraph.iter():
                if element.tag == ns + "t":
                    parts.append(element.text or "")
                elif element.tag == ns + "tab":
                    parts.append("\t")
                elif element.tag in (ns + "br", ns + "cr"):
                    parts.append("\n")
            text = "".join(parts).strip()
            if text:
                paragraphs.append(text)
        text = "\n".join(paragraphs)
        if not text or len(text) > 200000:
            raise ValueError("empty or too long")
        return text, "仅提取正文文字，图片等内容请对照原文件核对。"
    except (
        OSError,
        ValueError,
        UnicodeError,
        KeyError,
        zipfile.BadZipFile,
        ElementTree.ParseError,
    ):
        raise ValueError("Word 文档无法在本机解析，请核对原文件。") from None


def extract_local_ocr(path: Path) -> tuple[str, str]:
    if not supports_local_ocr():
        raise ValueError("本机 OCR 仅在 Windows 可用。")
    powershell = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    try:
        result = subprocess.run(
            [
                str(powershell),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(OCR_SCRIPT),
                "-Path",
                str(path),
            ],
            capture_output=True,
            timeout=90 if path.suffix.lower() == ".pdf" else 40,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
            raise ValueError("OCR failed")
        payload = json.loads(result.stdout.decode("utf-8-sig"))
        text = payload.get("text")
        if not isinstance(text, str):
            raise ValueError("OCR result invalid")
        text = re.sub(
            r"(?<=[\u4e00-\u9fff0-9《》【】：、，。])[^\S\r\n]+(?=[\u4e00-\u9fff0-9《》【】：、，。])",
            "",
            text,
        ).strip()
        if not text or len(text) > 200000:
            raise ValueError("OCR text empty or too long")
        partial = path.suffix.lower() == ".pdf" and payload.get("complete") is not True
        return text, "PDF 仅识别了部分页面，请对照原文件补充。" if partial else ""
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        raise ValueError("本机识别失败，请检查文件及 Windows OCR 语言设置。") from None


def get_attachment(session: Session, attachment_id: str) -> Attachment:
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(404, "附件不存在。")
    return attachment


def original_path(directory: Path, attachment: Attachment) -> Path:
    return directory / f"{attachment.id}{Path(attachment.filename).suffix.lower()}"


def persist_original(path: Path, content: bytes) -> None:
    """Publish a complete immutable file; interrupted temporary files are not originals."""
    descriptor, temporary = tempfile.mkstemp(prefix="original-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as file:
            os.chmod(temporary, 0o600)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if (
                path.is_symlink()
                or path.stat().st_size != len(content)
                or hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(content).digest()
            ):
                raise ValueError("已保存的原件与本次内容不一致，未覆盖。") from None
    finally:
        Path(temporary).unlink(missing_ok=True)


def local_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = content.decode(encoding).strip()
            if not text or "\x00" in text:
                break
            if len(text) > 200000:
                raise HTTPException(422, "文本过长，请拆分为较短通知后上传。")
            return text
        except UnicodeError:
            continue
    raise HTTPException(422, "无法读取文字，请使用 UTF-8 或 GB18030 编码的文本文件。")


def router(engine, provider_factory=MinerUProvider) -> APIRouter:
    api = APIRouter(tags=["file imports"])
    directory = Path(engine.url.database).parent / "uploads"

    @api.get("/api/imports/capabilities")
    def capabilities():
        return {
            "mineru_configured": bool(api_key()),
            "local_ocr": supports_local_ocr(),
            "local_docx": True,
            "max_bytes": MAX_UPLOAD_BYTES,
            "extensions": sorted(TEXT_TYPES | CLOUD_TYPES | LOCAL_OCR_TYPES),
        }

    @api.get("/api/imports/files", response_model=list[AttachmentRead])
    def unsubmitted_files():
        with Session(engine) as session:
            return session.exec(
                select(Attachment)
                .where(Attachment.message_id.is_(None))
                .order_by(Attachment.created_at.desc())
                .limit(20)
            ).all()

    def submit(attachment_id: str) -> Attachment:
        with Session(engine) as session:
            attachment = get_attachment(session, attachment_id)
            filename, path, file_id = (
                attachment.filename,
                original_path(directory, attachment),
                attachment.id,
            )
            session.commit()
            try:
                attachment.batch_id = provider_factory().submit(path, filename, file_id)
                attachment.status, attachment.error = "parsing", ""
            except MinerUError as exc:
                attachment.status, attachment.error = "failed", str(exc)
            session.add(attachment)
            session.commit()
            session.refresh(attachment)
            return attachment

    def local_submit(attachment_id: str) -> Attachment:
        with Session(engine) as session:
            attachment = get_attachment(session, attachment_id)
            path = original_path(directory, attachment)
            session.commit()
            try:
                text, warning = (
                    extract_local_docx(path)
                    if path.suffix.lower() in LOCAL_DOCUMENT_TYPES
                    else extract_local_ocr(path)
                )
                values = {"status": "ready", "text": text, "error": warning}
            except Exception:
                values = {
                    "status": "failed",
                    "error": "本机解析失败，请检查原文件或识别环境。",
                }
            session.execute(
                update(Attachment)
                .where(
                    Attachment.id == attachment_id,
                    Attachment.status == "parsing",
                    Attachment.batch_id == "",
                )
                .values(**values)
            )
            session.commit()
            session.refresh(attachment)
            return attachment

    @api.post("/api/imports/files", response_model=AttachmentRead)
    async def upload(
        request: Request,
        filename: str = Query(max_length=180),
        consent: bool = False,
        extractor: Literal["cloud", "local"] = "cloud",
    ):
        if not filename.strip() or re.search(r"[/\\\x00-\x1f\x7f]", filename):
            raise HTTPException(422, "文件名无效。")
        extension = Path(filename).suffix.lower()
        if extension not in TEXT_TYPES | CLOUD_TYPES | LOCAL_OCR_TYPES:
            raise HTTPException(422, "支持 TXT、Markdown、PDF、Word、PPT 和 PNG/JPG/WebP 图片。")
        if extension not in TEXT_TYPES and extractor == "local":
            if extension not in LOCAL_OCR_TYPES | LOCAL_DOCUMENT_TYPES:
                raise HTTPException(422, "本机解析支持 DOCX、图片与 PDF。")
            if extension in LOCAL_OCR_TYPES and not supports_local_ocr():
                raise HTTPException(503, "本机 OCR 仅在支持的 Windows 环境可用。")
        elif extension not in TEXT_TYPES:
            if extension not in CLOUD_TYPES:
                raise HTTPException(422, "当前云端解析不支持此文件格式。")
            if not consent:
                raise HTTPException(422, "请先确认允许将此文件发送到 MinerU 进行解析。")
            if not api_key():
                raise HTTPException(503, "尚未配置 MinerU Token，可先粘贴文本或手动填写。")
        attachment = Attachment(filename=filename, size=0)
        directory.mkdir(parents=True, exist_ok=True)
        path = original_path(directory, attachment)
        try:
            with path.open("xb") as target:
                async for chunk in request.stream():
                    attachment.size += len(chunk)
                    if attachment.size > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "文件不能超过 20 MB，请压缩或拆分后重试。")
                    if (
                        extractor == "local"
                        and extension not in TEXT_TYPES
                        and attachment.size > 8 * 1024 * 1024
                    ):
                        raise HTTPException(413, "本机解析文件不能超过 8 MB。")
                    target.write(chunk)
            if not attachment.size:
                raise HTTPException(422, "不能上传空文件。")
            if extension in TEXT_TYPES:
                attachment.text = local_text(path.read_bytes())
                attachment.status = "ready"
            with Session(engine) as session:
                session.add(attachment)
                if extension not in TEXT_TYPES and extractor == "local":
                    session.flush()
                    session.add(LocalOCRSource(attachment_id=attachment.id))
                session.commit()
                session.refresh(attachment)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        if extension not in TEXT_TYPES and extractor == "local":
            return await run_in_threadpool(local_submit, attachment.id)
        if extension in CLOUD_TYPES:
            return await run_in_threadpool(submit, attachment.id)
        return attachment

    @api.post("/api/imports/files/{attachment_id}/local-ocr", response_model=AttachmentRead)
    async def retry_local(attachment_id: str):
        with Session(engine) as session:
            attachment = get_attachment(session, attachment_id)
            extension = Path(attachment.filename).suffix.lower()
            if (
                extension not in LOCAL_OCR_TYPES | LOCAL_DOCUMENT_TYPES
                or attachment.size > 8 * 1024 * 1024
            ):
                raise HTTPException(422, "本机解析支持不超过 8 MB 的 DOCX、图片与 PDF。")
            if extension in LOCAL_OCR_TYPES and not supports_local_ocr():
                raise HTTPException(503, "本机 OCR 仅在支持的 Windows 环境可用。")
            changed = session.execute(
                update(Attachment)
                .where(
                    Attachment.id == attachment_id,
                    Attachment.status == "failed",
                    Attachment.batch_id == "",
                )
                .values(status="parsing", error="")
            ).rowcount
            session.commit()
            if changed != 1:
                raise HTTPException(409, "文件正在处理或已经提取，请刷新后重试。")
        return await run_in_threadpool(local_submit, attachment_id)

    @api.post("/api/imports/files/{attachment_id}/poll", response_model=AttachmentRead)
    def poll(attachment_id: str):
        with Session(engine) as session:
            attachment = get_attachment(session, attachment_id)
            if attachment.status != "parsing":
                return attachment
            batch_id = attachment.batch_id
            session.commit()  # Do not hold a DB transaction while contacting MinerU.
            if not batch_id:
                raise HTTPException(409, "上传尚未完成或被中断，请点击重新解析。")
            try:
                text = provider_factory().poll(batch_id)
            except MinerUFailed as exc:
                values = {"status": "failed", "error": str(exc), "batch_id": ""}
            except MinerUError as exc:
                # Keep the batch ID so a transient failure can be polled again.
                values = {"error": str(exc)}
            else:
                values = {"error": ""}
                if text is not None:
                    values.update(text=text, status="ready")
            # A response for an old batch must not undo completion or a newer retry.
            session.execute(
                update(Attachment)
                .where(
                    Attachment.id == attachment_id,
                    Attachment.status == "parsing",
                    Attachment.batch_id == batch_id,
                )
                .values(**values)
            )
            session.commit()
            session.refresh(attachment)
            return attachment

    @api.post("/api/imports/files/{attachment_id}/retry", response_model=AttachmentRead)
    def retry(attachment_id: str, consent: bool = False):
        with Session(engine) as session:
            attachment = get_attachment(session, attachment_id)
            if attachment.message_id or attachment.status == "ready":
                raise HTTPException(409, "文件已解析或提交，请使用原有记录。")
            if Path(attachment.filename).suffix.lower() not in CLOUD_TYPES:
                raise HTTPException(422, "当前云端解析不支持此文件格式。")
            local_source = session.get(LocalOCRSource, attachment_id)
            if local_source is not None and not local_source.cloud_consent:
                if not consent:
                    raise HTTPException(422, "请先同意将文件发送到 MinerU 云端解析。")
                local_source.cloud_consent = True
                session.add(local_source)
            if attachment.batch_id:
                return attachment  # Poll an existing remote task rather than submit twice.
            if attachment.status == "parsing":
                raise HTTPException(409, "文件正在上传，请稍后再查询。")
            # Reserve retry before making the remote request.
            changed = (
                session.connection()
                .exec_driver_sql(
                    "UPDATE attachment SET status='parsing', error='' "
                    "WHERE id=? AND status='failed'",
                    (attachment_id,),
                )
                .rowcount
            )
            session.commit()
            if changed != 1:
                raise HTTPException(409, "解析状态已变化，请刷新后重试。")
        return submit(attachment_id)

    @api.get("/api/attachments/{attachment_id}/text", response_model=AttachmentRead)
    def attachment_text(attachment_id: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        with Session(engine) as session:
            return get_attachment(session, attachment_id)

    @api.get("/api/attachments/{attachment_id}/download")
    def download(attachment_id: str):
        with Session(engine) as session:
            attachment = get_attachment(session, attachment_id)
            path = original_path(directory, attachment)
            if not path.is_file():
                raise HTTPException(404, "原始附件文件不存在。")
            return FileResponse(
                path,
                filename=attachment.filename,
                media_type="application/octet-stream",
                headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
            )

    return api
