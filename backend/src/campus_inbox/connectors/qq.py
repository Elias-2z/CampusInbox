"""OS-independent QQ intake. No QQ login, HTTP bridge, or fake production connection."""

import hashlib
import json
import threading
from datetime import UTC, datetime
from typing import Literal, Protocol

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import JSON, Column, and_, func, or_
from sqlmodel import Field as DBField
from sqlmodel import Session, SQLModel, select

from campus_inbox import services
from campus_inbox.connectors.courses import course_name
from campus_inbox.models import now, uid
from campus_inbox.schemas import ImportMessage


class Group(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    name: str = Field(min_length=1, max_length=100)


class Member(Group):
    pass


class Selection(Group):
    course: str | None = Field(default=None, max_length=100)
    member_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("member_ids")
    @classmethod
    def members(cls, values):
        return list(dict.fromkeys(Group(id=v, name="member").id for v in values))


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    account_id: str = ""
    groups: list[Selection] = Field(default_factory=list, max_length=50)
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def valid_scope(self):
        if self.account_id:
            Group(id=self.account_id, name="account")
        if self.enabled and (not self.account_id or not self.groups):
            raise ValueError("启用前请选择账号和至少一个群")
        if len({g.id for g in self.groups}) != len(self.groups):
            raise ValueError("群号不能重复")
        return self


class Incoming(BaseModel):
    """Adapters supply actual sent time. Unknown-time events stay outside this intake."""

    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    group_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    sender_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    sender_name: str = Field(default="", max_length=100)
    message_id: str = Field(min_length=1, max_length=100)
    media_types: list[str] = Field(default_factory=list, max_length=40)
    sent_at: datetime
    text: str = Field(min_length=2, max_length=20000)

    @field_validator("sent_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("QQ 消息必须提供真实发送时间和时区")
        return value.astimezone(UTC)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("消息正文不能为空")
        return value


class AdapterState(BaseModel):
    connected: bool = False
    account_id: str = ""
    name: str = "尚未接入 Mac / Windows 消息桥"


class Batch(BaseModel):
    events: list[Incoming] = Field(default_factory=list, max_length=100)
    cursor: str = Field(default="", max_length=2000)


class QQAdapter(Protocol):
    def status(self) -> AdapterState: ...
    def groups(self) -> list[Group]: ...
    def members(self, group_id: str) -> list[Member]: ...
    def poll(self, cursor: str) -> Batch: ...
    def disconnect(self) -> None: ...


class DisconnectedAdapter:
    def status(self):
        return AdapterState()

    def groups(self):
        return []

    def members(self, group_id):
        return []

    def poll(self, cursor):
        return Batch(cursor=cursor)

    def disconnect(self):
        pass


class QQSettings(SQLModel, table=True):
    id: int = DBField(default=1, primary_key=True)
    config: dict = DBField(default_factory=dict, sa_column=Column(JSON, nullable=False))
    cursor: str = ""
    error: str = ""
    last_received_at: datetime | None = None
    last_sync_at: datetime | None = None


class QQEvent(SQLModel, table=True):
    id: str = DBField(default_factory=uid, primary_key=True)
    identity: str = DBField(unique=True, index=True)
    payload: dict = DBField(sa_column=Column(JSON, nullable=False))
    state: str = "pending"
    blocked_revision: int = -1
    suggestion_id: str | None = DBField(default=None, foreign_key="suggestion.id")
    error: str = ""
    received_at: datetime = DBField(default_factory=now)


def source_context(session, dedupe_key, *, persist=False):
    if not dedupe_key.startswith("qq:"):
        return None
    event = session.exec(select(QQEvent).where(QQEvent.identity == dedupe_key[3:])).first()
    if event is None:
        return None
    payload = dict(event.payload)
    if "_course" not in payload:
        settings = session.get(QQSettings, 1)
        config = Config.model_validate(settings.config) if settings else Config()
        group = next((g for g in config.groups if g.id == payload["group_id"]), None)
        if not group or config.account_id != payload["account_id"]:
            return None
        payload["_course"] = group.name if group.course is None else group.course
        payload["_group_name"] = group.name
        payload["_course_mode"] = "auto" if group.course is None else "manual"
    if "_course_mode" not in payload:
        payload["_course_mode"] = (
            "auto" if payload["_course"] == payload.get("_group_name") else "manual"
        )
    if payload["_course_mode"] == "auto":
        payload["_course"] = course_name(payload.get("_group_name", ""))
    if persist:
        event.payload = payload
        session.add(event)
    return dict(
        platform="qq",
        group_id=payload["group_id"],
        group_name=payload.get("_group_name", ""),
        sender_id=payload["sender_id"],
        sender_name=payload.get("sender_name", ""),
        course=payload["_course"],
        sent_at=payload["sent_at"],
    )


class QQCore:
    def __init__(self, engine, provider_factory, adapter=None):
        self.engine, self.provider_factory = engine, provider_factory
        self.adapter = adapter or DisconnectedAdapter()
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.worker = None

    def settings(self, session):
        row = session.get(QQSettings, 1)
        if row is None:
            row = QQSettings(config=Config().model_dump())
            session.add(row)
            session.commit()
        return row

    def adapter_state(self):
        try:
            return AdapterState.model_validate(self.adapter.status())
        except Exception:
            return AdapterState(name="适配器状态不可用，请检查连接")

    def status(self):
        with self.lock, Session(self.engine) as session:
            row = self.settings(session)
            counts = dict(
                session.exec(select(QQEvent.state, func.count()).group_by(QQEvent.state)).all()
            )
            return dict(
                config=Config.model_validate(row.config),
                course_suggestions={
                    g.id: course_name(g.name) for g in Config.model_validate(row.config).groups
                },
                adapter=self.adapter_state(),
                counts={
                    state: counts.get(state, 0)
                    for state in ("pending", "done", "failed", "blocked")
                },
                last_received_at=row.last_received_at.replace(tzinfo=UTC)
                if row.last_received_at
                else None,
                last_sync_at=row.last_sync_at.replace(tzinfo=UTC) if row.last_sync_at else None,
                error=row.error,
            )

    def configure(self, config):
        with self.lock, Session(self.engine) as session:
            row = self.settings(session)
            previous = Config.model_validate(row.config)
            if config.revision != previous.revision:
                raise HTTPException(409, "连接设置已变化，请重新读取后保存")
            config.revision += 1
            row.config = config.model_dump()
            if config.account_id != previous.account_id:
                row.cursor = ""
            session.add(row)
            session.commit()
        return self.status()

    @staticmethod
    def allowed(config, event):
        return (
            config.enabled
            and config.account_id == event.account_id
            and any(
                g.id == event.group_id and (not g.member_ids or event.sender_id in g.member_ids)
                for g in config.groups
            )
            and event.sender_id != event.account_id
        )

    def receive(self, event):
        """Durable ack for future push adapters; duplicates survive process restarts."""
        event = Incoming.model_validate(event)
        with self.lock, Session(self.engine) as session:
            config = Config.model_validate(self.settings(session).config)
            if not self.allowed(config, event):
                return "filtered"
            payload = event.model_dump(mode="json", exclude_defaults=True)
            # Content revisions are separate evidence; key ordering is irrelevant.
            identity = hashlib.sha256(
                json.dumps(
                    {k: v for k, v in payload.items() if k != "sender_name"}, sort_keys=True
                ).encode()
            ).hexdigest()
            if session.exec(select(QQEvent).where(QQEvent.identity == identity)).first():
                return "duplicate"
            if len(session.exec(select(QQEvent).where(QQEvent.state == "pending")).all()) >= 1000:
                raise RuntimeError("QQ queue full")
            group = next(g for g in config.groups if g.id == event.group_id)
            payload["_course"] = (
                course_name(group.name) if group.course is None else group.course
            ).strip()
            payload["_course_mode"] = "auto" if group.course is None else "manual"
            payload["_group_name"] = group.name
            session.add(QQEvent(identity=identity, payload=payload))
            row = self.settings(session)
            row.last_received_at = now()
            session.add(row)
            session.commit()
            return "queued"

    def sync(self):
        with self.lock:
            try:
                with Session(self.engine) as session:
                    row = self.settings(session)
                    config = Config.model_validate(row.config)
                    cursor = row.cursor
                state = self.adapter_state()
                if not config.enabled:
                    return self.status()
                if not state.connected or state.account_id != config.account_id:
                    raise RuntimeError("QQ adapter disconnected or account changed")
                batch = Batch.model_validate(self.adapter.poll(cursor))
                for event in batch.events:
                    self.receive(event)
                # Advance only after the entire batch has been persisted or filtered.
                with Session(self.engine) as session:
                    row = self.settings(session)
                    row.cursor, row.error, row.last_sync_at = batch.cursor, "", now()
                    session.add(row)
                    session.commit()
                self.process()
            except Exception:
                # Adapter errors may contain URLs/tokens/messages; never echo them.
                with Session(self.engine) as session:
                    row = self.settings(session)
                    row.error = "QQ 同步未完成，请检查适配器连接和账号后重试。"
                    session.add(row)
                    session.commit()
            return self.status()

    def process(self):
        with self.lock, Session(self.engine) as session:
            config = Config.model_validate(self.settings(session).config)
            if not config.enabled:
                return
            ids = list(
                session.exec(
                    select(QQEvent.id)
                    .where(
                        or_(
                            QQEvent.state == "pending",
                            and_(
                                QQEvent.state == "blocked",
                                QQEvent.blocked_revision < config.revision,
                            ),
                        )
                    )
                    .order_by(QQEvent.received_at)
                    .limit(10)
                )
            )
        for event_id in ids:
            with self.lock, Session(self.engine) as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                config = Config.model_validate(self.settings(session).config)
                row = session.get(QQEvent, event_id)
                event = Incoming.model_validate(
                    {k: v for k, v in row.payload.items() if not k.startswith("_")}
                )
                if not self.allowed(config, event):
                    row.state = "blocked"
                    row.blocked_revision = config.revision
                else:
                    try:
                        suggestion = services.import_message(
                            session,
                            ImportMessage(
                                text=event.text,
                                source=f"QQ 群{event.group_id} · 发件人{event.sender_id}",
                                sent_at=event.sent_at,
                                timezone="Asia/Shanghai",
                            ),
                            self.provider_factory,
                            dedupe_key="qq:" + row.identity,
                        )
                        if event.media_types:
                            warning = "QQ 消息含未读取的图片或附件，请核对原群消息后确认。"
                            suggestion.warnings = list(
                                dict.fromkeys([*suggestion.warnings, warning])
                            )
                            session.add(suggestion)
                        row.state, row.suggestion_id, row.error = "done", suggestion.id, ""
                    except Exception:
                        session.rollback()
                        row = session.get(QQEvent, event_id)
                        row.state, row.error = "failed", "识别中断，原消息已保存，可重试。"
                session.add(row)
                session.commit()

    def retry(self):
        with self.lock, Session(self.engine) as session:
            for row in session.exec(select(QQEvent).where(QQEvent.state == "failed")):
                # If the notice was committed before failure, reuse it and its audit state.
                row.state, row.error = "pending", ""
                session.add(row)
            session.commit()
        return self.sync()

    def start(self):
        def run():
            while not self.stop.wait(3):
                self.sync()

        self.worker = threading.Thread(target=run, daemon=True, name="qq-core")
        self.worker.start()

    def close(self):
        self.stop.set()
        if self.worker:
            self.worker.join()
        getattr(self.adapter, "close", self.adapter.disconnect)()


class CourseRequest(BaseModel):
    group_name: str = Field(min_length=1, max_length=100)
    use_model: bool = False


def router(core):
    api = APIRouter(prefix="/api/connectors/qq", tags=["QQ connector core"])

    @api.get("")
    def status():
        return core.status()

    @api.put("/settings")
    def settings(config: Config):
        return core.configure(config)

    @api.post("/disconnect")
    def disconnect():
        with core.lock:
            config = core.status()["config"]
            config.enabled = False
            core.configure(config)
            try:
                core.adapter.disconnect()
            except Exception:
                raise HTTPException(503, "范围已暂停，适配器断开失败，请检查桥接程序") from None
        return core.status()

    @api.post("/course-suggestion")
    def suggest_course(body: CourseRequest):
        course = course_name(body.group_name)
        method = "rule" if course else "none"
        if body.use_model:
            with Session(core.engine) as session:
                settings = services.config(session)
            if settings.enabled and settings.model:
                try:
                    result = core.provider_factory(settings).generate_structured(
                        body.group_name, now().isoformat(), "Asia/Shanghai"
                    )
                    candidate = (result.get("course") or "").strip()
                    if (
                        candidate
                        and candidate in body.group_name
                        and course_name(candidate) == candidate
                    ):
                        course, method = candidate, "llm"
                except Exception:
                    pass  # Retain rule suggestion; no cloud fallback or database writes.
        return {"course": course, "method": method, "needs_confirmation": True}

    @api.get("/groups", response_model=list[Group])
    def groups():
        try:
            return core.adapter.groups()
        except Exception:
            raise HTTPException(503, "无法读取群列表，请检查消息桥") from None

    @api.get("/groups/{group_id}/members", response_model=list[Member])
    def members(group_id: str):
        if not group_id.isdigit():
            raise HTTPException(422, "群号无效")
        try:
            return core.adapter.members(group_id)
        except Exception:
            raise HTTPException(503, "无法读取成员列表，请检查消息桥") from None

    @api.post("/sync")
    def sync():
        return core.sync()

    @api.post("/retry")
    def retry():
        return core.retry()

    @api.get("/events")
    def events(state: Literal["pending", "done", "failed", "blocked"] | None = None):
        with Session(core.engine) as session:
            query = select(QQEvent)
            if state:
                query = query.where(QQEvent.state == state)
            return session.exec(query.order_by(QQEvent.received_at.desc()).limit(50)).all()

    return api
