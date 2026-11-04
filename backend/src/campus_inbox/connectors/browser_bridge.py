"""Adapt the existing browser companion to the main inbox."""

import hashlib
import io
import json
import re
import secrets
import zipfile
from ipaddress import ip_address
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlmodel import Field, Session, SQLModel
from starlette.concurrency import run_in_threadpool

from campus_inbox.connectors.chaoxing_api import loopback

SOURCE = Path(__file__).resolve().parents[4] / "local-assistant" / "browser-companion"
FILES = (
    "manifest.json",
    "background.js",
    "content.js",
    "task-content.js",
    "notices.js",
    "parser.js",
    "tasks.js",
    "options.css",
    "options.html",
    "options.js",
)


class BrowserPair(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    digest: str


def package():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            content = (
                (SOURCE / name)
                .read_text(encoding="utf-8")
                .replace("http://127.0.0.1:8765", "http://127.0.0.1:8000")
            )
            if name == "background.js":
                content = content.replace(
                    "/api/chaoxing/messages", "/api/connectors/chaoxing/browser"
                )
                begin = content.index("async function prepare(")
                end = content.index("async function flush()", begin)
                content = (
                    content[:begin]
                    + """async function prepare(job, token, readInstructions) {
  if (!readInstructions || !['course_notice','task_center'].includes(job.kind)) return job;
  const result = structuredClone(job);
  const files = Array.isArray(result.attachments) ? result.attachments : [];
  const supported = /\\.(docx|pdf|txt|md|png|jpg|jpeg|webp|bmp)$/i;
  const file = files.find(item => item && supported.test(item.name)
    && /^https:\\/\\/d0\\.cldisk\\.com\\/download\\/[a-f0-9]{32}$/i.test(item.url || ''));
  if (file && await permitted(token)) {
    try {
      file.content_base64 = await download(file.url);
      file.status = 'downloaded';
    } catch { file.status = 'failed'; }
  }
  return result;
}

"""
                    + content[end:]
                )
                content = content.replace(
                    "credentials:'omit',signal", "credentials:'omit',redirect:'error',signal"
                )
            archive.writestr(name, content)
    return buffer.getvalue()


def router(runtime):
    api = APIRouter(prefix="/api/connectors/chaoxing/browser", tags=["browser companion"])

    def is_paired(digest):
        with Session(runtime.engine) as session:
            pairing = session.get(BrowserPair, 1)
            return pairing is not None and secrets.compare_digest(digest, pairing.digest)

    @api.get("/download", dependencies=[Depends(loopback)])
    def download():
        return Response(
            package(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="campusinbox-browser.zip"'},
        )

    @api.post("/pair", dependencies=[Depends(loopback)])
    def pair():
        token = secrets.token_hex(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with Session(runtime.engine) as session:
            session.merge(BrowserPair(digest=digest))
            session.commit()
        return Response(
            json.dumps({"token": token}),
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    @api.delete("/pair", dependencies=[Depends(loopback)])
    def revoke():
        with Session(runtime.engine) as session:
            pairing = session.get(BrowserPair, 1)
            if pairing:
                session.delete(pairing)
                session.commit()
        return {"revoked": True}

    @api.post("")
    async def ingest(request: Request, background: BackgroundTasks):
        origin = request.headers.get("origin", "")
        try:
            local = request.client and ip_address(request.client.host).is_loopback
        except ValueError:
            local = False
        if (
            not local
            or request.url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or origin
            and not re.fullmatch(r"chrome-extension://[a-p]{32}", origin)
        ):
            raise HTTPException(403, "网页组件仅可连接本机服务。")
        token = re.fullmatch(r"Bearer ([a-f0-9]{64})", request.headers.get("authorization", ""))
        digest = hashlib.sha256(token[1].encode()).hexdigest() if token else ""
        if not digest or not await run_in_threadpool(is_paired, digest):
            raise HTTPException(401, "请重新配对网页组件。")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 12 * 1024 * 1024:
                raise HTTPException(413, "消息或附件过大。")
            body.extend(chunk)
        try:
            job = json.loads(body)
            if not isinstance(job, dict):
                raise ValueError()
            # Keep browser captures separate from the desktop login's account identity.
            account = "browser:" + digest
            if not await run_in_threadpool(is_paired, digest):
                raise HTTPException(401, "请重新配对网页组件。")
            event_id, _ = await run_in_threadpool(runtime.intake.receive, account, job)
        except (ValueError, TypeError, RecursionError):
            raise HTTPException(422, "网页消息格式无效。") from None
        background.add_task(runtime.intake.process, event_id)
        return {"accepted": True}

    return api
