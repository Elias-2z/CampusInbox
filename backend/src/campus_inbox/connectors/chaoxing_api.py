"""Loopback-only control surface for the Chaoxing collector."""

import base64
import binascii
import json
from datetime import UTC, datetime
from ipaddress import ip_address
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, text
from sqlmodel import Session, select

from campus_inbox.connectors.chaoxing import ChaoxingEvent, attachment_info


def loopback(request: Request):
    try:
        remote = ip_address(request.client.host if request.client else "")
        host = request.url.hostname
        origin = request.headers.get("origin")
        parsed = urlsplit(origin) if origin else None
        valid_origin = not parsed or (
            parsed.scheme in {"http", "https"}
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and parsed.username is None
            and parsed.password is None
        )
        if (
            not remote.is_loopback
            or host not in {"127.0.0.1", "localhost", "::1"}
            or not valid_origin
        ):
            raise ValueError()
    except ValueError:
        raise HTTPException(403, "该功能仅允许本机访问。") from None


class Connect(BaseModel):
    visible: bool = False


class SectionRepair(BaseModel):
    key: str = Field(max_length=252)


class ResolveTime(BaseModel):
    sent_at: datetime

    @field_validator("sent_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("请填写带时区的真实发布时间")
        return value


def router(runtime):
    api = APIRouter(prefix="/api/connectors/chaoxing", dependencies=[Depends(loopback)])

    @api.get("")
    def status():
        return runtime.status()

    @api.get("/report")
    def report():
        status = runtime.status()
        data = {
            "generated_at": datetime.now(UTC).isoformat(),
            "enabled": status["enabled"],
            "freshness": status["freshness"],
            "last_heartbeat": status["last_heartbeat"],
            "last_received": status["last_received"],
            "counts": status["counts"],
            "sources": status["sources"],
        }
        return Response(
            json.dumps(data, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="chaoxing-sync-report.json"',
                "Cache-Control": "no-store",
            },
        )

    @api.post("/connect")
    def connect(body: Connect):
        return runtime.start(visible=body.visible)

    @api.post("/pause")
    def pause():
        return runtime.pause()

    @api.post("/disconnect")
    def disconnect():
        try:
            return runtime.forget_login()
        except OSError:
            raise HTTPException(500, "清除登录状态失败，请检查本机文件权限。") from None

    @api.post("/recheck")
    def recheck():
        if not runtime.enabled:
            raise HTTPException(409, "请先连接学习通。")
        runtime._command({"type": "recheck"})
        runtime.wake.set()
        return runtime.status()

    @api.post("/notices/recheck")
    def recheck_notice(body: SectionRepair):
        try:
            runtime.repair_notice(body.key)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return runtime.status()

    @api.post("/sections/recheck")
    def recheck_section(body: SectionRepair):
        try:
            runtime.repair_section(body.key)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return runtime.status()

    @api.get("/sections/tasks")
    def section_tasks(key: str):
        return runtime.listed_tasks(key)

    @api.post("/tasks/recheck")
    def recheck_task(body: SectionRepair):
        try:
            runtime.repair_task(body.key)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"state": "queued"}

    @api.post("/tasks/{task_id}/recheck")
    def recheck_linked_task(task_id: str):
        try:
            runtime.recheck_linked_task(task_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"state": "queued"}

    @api.get("/qr")
    def qr():
        path = runtime.data_dir / "qr.png"
        if (
            not runtime.status()["qr_ready"]
            or not path.is_file()
            or path.stat().st_size > 1024 * 1024
        ):
            raise HTTPException(404, "二维码尚未准备好。")
        return Response(
            path.read_bytes(), media_type="image/png", headers={"Cache-Control": "no-store"}
        )

    @api.get("/events")
    def events():
        with Session(runtime.engine) as session:
            rows = session.exec(
                select(ChaoxingEvent)
                .where(
                    or_(
                        ChaoxingEvent.state.in_(["needs_time", "needs_review", "failed"]),
                        (ChaoxingEvent.state == "pending")
                        & ChaoxingEvent.account_id.startswith("browser:"),
                        (ChaoxingEvent.state == "done")
                        & text("""EXISTS (
                        SELECT 1 FROM json_each(chaoxingevent.payload, '$.attachments') AS file
                        WHERE CASE WHEN file.type = 'object' THEN
                            COALESCE(json_extract(file.value, '$.content_base64'), '') = ''
                        ELSE 0 END
                    )"""),
                    )
                )
                .order_by(ChaoxingEvent.received_at.desc())
                .limit(100)
            ).all()
            return [
                {
                    "id": row.id,
                    "state": row.state,
                    "kind": row.payload.get("kind"),
                    "title": str(row.payload.get("title") or "")[:160],
                    "text": str(row.payload.get("text") or "")[:20000],
                    "course": str(row.payload.get("course") or "")[:100],
                    "display_time": str(row.payload.get("display_time") or "")[:100],
                    "attachments": attachment_info(row.payload),
                }
                for row in rows
            ]

    @api.get("/events/{event_id}/attachments/{index}")
    def event_attachment(event_id: str, index: int):
        with Session(runtime.engine) as session:
            event = session.get(ChaoxingEvent, event_id)
            attachments = event.payload.get("attachments") if event else None
            if not isinstance(attachments, list) or index < 0 or index >= len(attachments):
                raise HTTPException(404, "附件不存在。")
            item = attachments[index]
            raw = item.get("content_base64") if isinstance(item, dict) else None
            if not isinstance(raw, str) or not raw or len(raw) > 12 * 1024 * 1024:
                raise HTTPException(404, "附件尚未下载。")
            try:
                content = base64.b64decode(raw, validate=True)
            except (ValueError, binascii.Error):
                raise HTTPException(422, "附件内容无效。") from None
            if not content or len(content) > 8 * 1024 * 1024:
                raise HTTPException(422, "附件大小无效。")
            name = str(item.get("name") or "附件")[:180]
            return Response(
                content,
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": "attachment; filename*=UTF-8''" + quote(name, safe=""),
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )

    @api.post("/events/{event_id}/resolve")
    def resolve(event_id: str, body: ResolveTime):
        try:
            state = runtime.intake.process(event_id, sent_at=body.sent_at)
        except ValueError as error:
            raise HTTPException(404, str(error)) from None
        return {"state": state}

    @api.post("/events/{event_id}/retry")
    def retry(event_id: str):
        try:
            return {"state": runtime.intake.process(event_id)}
        except ValueError as error:
            raise HTTPException(404, str(error)) from None

    @api.post("/events/{event_id}/attachments/{index}/fetch")
    def fetch_original(event_id: str, index: int):
        try:
            return {"state": runtime.fetch_original(event_id, index)}
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        except Exception:
            raise HTTPException(503, "原件保存失败，请检查磁盘和连接后重试。") from None

    return api
