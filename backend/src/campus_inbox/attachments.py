"""Persist originals and extraction drafts before entering the existing inbox workflow."""

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import update
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from campus_inbox.mineru import MinerUError, MinerUFailed, MinerUProvider, api_key
from campus_inbox.models import Attachment
from campus_inbox.schemas import AttachmentRead

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
TEXT_TYPES = {".txt", ".md"}
CLOUD_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".pdf", ".doc", ".docx", ".ppt", ".pptx"}


def get_attachment(session: Session, attachment_id: str) -> Attachment:
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(404, "附件不存在。")
    return attachment


def original_path(directory: Path, attachment: Attachment) -> Path:
    return directory / f"{attachment.id}{Path(attachment.filename).suffix.lower()}"


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
            "max_bytes": MAX_UPLOAD_BYTES,
            "extensions": sorted(TEXT_TYPES | CLOUD_TYPES),
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

    @api.post("/api/imports/files", response_model=AttachmentRead)
    async def upload(
        request: Request, filename: str = Query(max_length=180), consent: bool = False
    ):
        if not filename.strip() or re.search(r"[/\\\x00-\x1f\x7f]", filename):
            raise HTTPException(422, "文件名无效。")
        extension = Path(filename).suffix.lower()
        if extension not in TEXT_TYPES | CLOUD_TYPES:
            raise HTTPException(422, "支持 TXT、Markdown、PDF、Word、PPT 和 PNG/JPG/WebP 图片。")
        if extension in CLOUD_TYPES:
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
                    target.write(chunk)
            if not attachment.size:
                raise HTTPException(422, "不能上传空文件。")
            if extension in TEXT_TYPES:
                attachment.text = local_text(path.read_bytes())
                attachment.status = "ready"
            with Session(engine) as session:
                session.add(attachment)
                session.commit()
                session.refresh(attachment)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        if extension in CLOUD_TYPES:
            return await run_in_threadpool(submit, attachment.id)
        return attachment

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
    def retry(attachment_id: str):
        with Session(engine) as session:
            attachment = get_attachment(session, attachment_id)
            if attachment.message_id or attachment.status == "ready":
                raise HTTPException(409, "文件已解析或提交，请使用原有记录。")
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
