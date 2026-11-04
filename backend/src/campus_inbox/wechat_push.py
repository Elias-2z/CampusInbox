"""Opt-in PushPlus reminders with Windows current-user credentials."""

import json
import os
import re
from datetime import datetime, timedelta
from threading import Event, Lock, Thread
from time import monotonic

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from pydantic import Field as InputField
from sqlalchemy import func, literal
from sqlmodel import Field, Session, SQLModel, select

from campus_inbox import reminders
from campus_inbox.connectors.chaoxing import ChaoxingEvent, actual_sent_time
from campus_inbox.connectors.chaoxing_api import loopback
from campus_inbox.connectors.platform_status import aware, identity
from campus_inbox.connectors.session_crypto import seal, unseal
from campus_inbox.extraction import cancellation_supported, deadline_supported
from campus_inbox.models import Message, Suggestion, TaskEvent, now

ENTROPY = b"campusinbox-pushplus-v1"


class WechatPushSettings(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    enabled: bool = False
    token: bytes | None = None
    result: str = "none"
    sending_at: datetime | None = None


class WechatPushReceipt(SQLModel, table=True):
    id: str = Field(primary_key=True)


class WechatNoticeSettings(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    since: datetime | None = None


class WechatExpandedNoticeSettings(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    since: datetime | None = None


class WechatReviewSettings(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    since: datetime | None = None


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    token: str | None = InputField(default=None, pattern=r"^[A-Za-z0-9_-]{16,256}$")
    clear_token: bool = False
    include_notices: bool | None = None
    include_expanded_notices: bool | None = None
    include_review_results: bool | None = None


def new_review_keys(session, since, current):
    key = literal("review:") + TaskEvent.id
    return session.exec(
        select(key)
        .select_from(TaskEvent)
        .join(Message, Message.id == TaskEvent.message_id)
        .join(ChaoxingEvent, Message.dedupe_key == literal("chaoxing:") + ChaoxingEvent.identity)
        .where(
            TaskEvent.operation.in_(("create", "deadline_update", "cancel")),
            TaskEvent.actor == "user",
            TaskEvent.created_at >= since,
            TaskEvent.created_at <= current,
            ~select(WechatPushReceipt.id).where(WechatPushReceipt.id == key).exists(),
        )
    ).all()


def new_notice_keys(session, course_since, expanded_since, current):
    since = min(value for value in (course_since, expanded_since) if value is not None)
    message_key = literal("notice:") + func.json_array(
        ChaoxingEvent.account_id, ChaoxingEvent.payload["message_id"].as_string()
    )
    rows = session.exec(
        select(
            message_key,
            ChaoxingEvent.account_id,
            ChaoxingEvent.payload,
            ChaoxingEvent.received_at,
            Suggestion.action,
        )
        .join(Suggestion, Suggestion.id == ChaoxingEvent.suggestion_id)
        .join(Message, Message.id == Suggestion.message_id)
        .where(
            Suggestion.status == "pending",
            ChaoxingEvent.state == "done",
            ChaoxingEvent.payload["kind"].as_string().in_(("course_notice", "chat", "task_center")),
            ChaoxingEvent.received_at >= since,
            Message.sent_at >= since,
            Message.sent_at <= current,
        )
    )
    keys = {}
    for receipt, account, payload, received_at, action in rows:
        sent_at = actual_sent_time(payload)
        if sent_at is None or not aware(since) <= sent_at <= current:
            continue
        kind = payload["kind"]
        expanded = (
            expanded_since is not None
            and sent_at >= aware(expanded_since)
            and aware(received_at) >= aware(expanded_since)
        )
        course = (
            kind == "course_notice"
            and course_since is not None
            and sent_at >= aware(course_since)
            and aware(received_at) >= aware(course_since)
        )
        message = payload.get("text")
        change = (
            expanded
            and action == "review"
            and isinstance(message, str)
            and (deadline_supported(message) or cancellation_supported(message))
        )
        if not (course or change or (kind != "course_notice" and expanded)):
            continue
        if kind == "task_center":
            coordinates = identity(account, payload)
            evidence = payload.get("field_evidence")
            if (
                not coordinates
                or not isinstance(evidence, dict)
                or not evidence.get("published_at")
            ):
                continue
            receipt = "task:" + coordinates
        keys.setdefault(receipt, "change" if change else kind)
    candidates = list(keys)
    for offset in range(0, len(candidates), 500):
        sent = session.exec(
            select(WechatPushReceipt.id).where(
                WechatPushReceipt.id.in_(candidates[offset : offset + 500])
            )
        )
        for receipt in sent:
            keys.pop(receipt, None)
    return keys


def deliver(token: str, content: str) -> str:
    deadline = monotonic() + 15
    try:
        with httpx.Client(timeout=8, follow_redirects=False, trust_env=False) as client:
            with client.stream(
                "POST",
                "https://www.pushplus.plus/send",
                json={
                    "token": token,
                    "title": "CampusInbox 提醒",
                    "content": content,
                    "template": "txt",
                    "channel": "wechat",
                },
            ) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 32768 or monotonic() > deadline:
                        return "unknown"
        result = json.loads(data)
        if not isinstance(result, dict):
            return "unknown"
        if result.get("code") != 200:
            return "rejected"
        receipt = result.get("data")
        return (
            "accepted"
            if isinstance(receipt, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", receipt)
            else "unknown"
        )
    except (httpx.HTTPError, ValueError):
        return "unknown"


class WechatPush:
    def __init__(self, engine, clock=now):
        self.engine = engine
        self.clock = clock
        self.lock = Lock()
        self.stopping = Event()
        self.thread = None

    def status(self):
        with Session(self.engine) as session:
            settings = session.get(WechatPushSettings, 1) or WechatPushSettings()
            notices = session.get(WechatNoticeSettings, 1)
            expanded = session.get(WechatExpandedNoticeSettings, 1)
            review = session.get(WechatReviewSettings, 1)
            return {
                "supported": os.name == "nt",
                "enabled": settings.enabled,
                "configured": bool(settings.token),
                "include_notices": bool(notices and notices.since),
                "include_expanded_notices": bool(expanded and expanded.since),
                "include_review_results": bool(review and review.since),
                "result": settings.result,
            }

    def configure(self, body: Configuration):
        if os.name != "nt":
            raise HTTPException(409, "微信推送凭据仅支持 Windows 当前用户加密。")
        if body.clear_token and (body.enabled or body.token):
            raise HTTPException(422, "清除 Token 时请关闭推送。")
        with self.lock, Session(self.engine) as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            settings = session.get(WechatPushSettings, 1) or WechatPushSettings()
            notices = session.get(WechatNoticeSettings, 1) or WechatNoticeSettings()
            expanded = (
                session.get(WechatExpandedNoticeSettings, 1) or WechatExpandedNoticeSettings()
            )
            review = session.get(WechatReviewSettings, 1) or WechatReviewSettings()
            if settings.result == "sending":
                raise HTTPException(409, "推送发送中，请稍后再保存设置。")
            try:
                if body.clear_token:
                    settings.token = None
                if body.token:
                    settings.token = seal(body.token.encode(), entropy=ENTROPY)
                if body.enabled:
                    if not settings.token:
                        raise HTTPException(422, "请先配置 PushPlus Token。")
                    unseal(settings.token, entropy=ENTROPY)
                    if not settings.enabled or body.token:
                        for item in reminders.feed(session, self.clock()).items:
                            session.connection().exec_driver_sql(
                                "INSERT OR IGNORE INTO wechatpushreceipt (id) VALUES (?)",
                                (item.id,),
                            )
            except OSError:
                raise HTTPException(409, "无法保存或读取本机凭据，请重新填写 Token。") from None
            for preference, include in (
                (notices, body.include_notices),
                (expanded, body.include_expanded_notices),
                (review, body.include_review_results),
            ):
                if body.clear_token or include is False:
                    preference.since = None
                elif (include is True or preference.since is not None) and (
                    not preference.since or (body.enabled and not settings.enabled) or body.token
                ):
                    preference.since = self.clock()
                session.add(preference)
            settings.enabled = body.enabled
            settings.result = "none"
            session.add(settings)
            session.commit()
        if body.enabled:
            self.start()
        return self.status()

    def dispatch(self, *, test=False):
        lines = []
        with self.lock:
            with Session(self.engine) as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                settings = session.get(WechatPushSettings, 1)
                if not settings or not settings.token:
                    if test:
                        raise HTTPException(409, "请先保存 PushPlus Token。")
                    return
                if settings.result == "sending":
                    if not settings.sending_at or self.clock() - aware(
                        settings.sending_at
                    ) >= timedelta(minutes=2):
                        settings.enabled = False
                        settings.result = "unknown"
                        session.commit()
                    return
                if not test and not settings.enabled:
                    return
                if not test:
                    current = self.clock()
                    items = [
                        item
                        for item in reminders.feed(session, current).items
                        if item.status == "unread"
                        and item.phase == "upcoming"
                        and session.get(WechatPushReceipt, item.id) is None
                    ]
                    notices = session.get(WechatNoticeSettings, 1)
                    expanded = session.get(WechatExpandedNoticeSettings, 1)
                    course_since = notices.since if notices else None
                    expanded_since = expanded.since if expanded else None
                    notice_keys = (
                        new_notice_keys(session, course_since, expanded_since, current)
                        if course_since or expanded_since
                        else {}
                    )
                    review = session.get(WechatReviewSettings, 1)
                    review_keys = (
                        new_review_keys(session, review.since, current)
                        if review and review.since
                        else []
                    )
                    if not items and not notice_keys and not review_keys:
                        return
                    session.add_all(WechatPushReceipt(id=item.id) for item in items)
                    session.add_all(WechatPushReceipt(id=key) for key in notice_keys)
                    session.add_all(WechatPushReceipt(id=key) for key in review_keys)
                    if items:
                        lines.append("有新的截止提醒")
                    kinds = set(notice_keys.values())
                    if "course_notice" in kinds:
                        lines.append("学习通有新课程通知")
                    if "chat" in kinds:
                        lines.append("学习通有新聊天消息")
                    if "task_center" in kinds:
                        lines.append("学习通有新作业或考试")
                    if "change" in kinds:
                        lines.append("学习通有任务变更待核对")
                    if review_keys:
                        lines.append("学习通任务审核结果已更新")
                try:
                    token = unseal(settings.token, entropy=ENTROPY).decode()
                except (OSError, ValueError):
                    settings.enabled = False
                    settings.result = "credential_error"
                    session.commit()
                    return
                settings.result = "sending"
                settings.sending_at = self.clock()
                session.commit()
            content = (
                "这是一条 CampusInbox 测试提醒。"
                if test
                else "；".join(lines) + "，请打开电脑上的 CampusInbox 查看。"
            )
            result = deliver(token, content)
            with Session(self.engine) as session:
                settings = session.get(WechatPushSettings, 1)
                settings.result = result
                settings.sending_at = None
                if result != "accepted":
                    settings.enabled = False
                session.commit()

    def start(self):
        with self.lock:
            if self.thread or os.name != "nt":
                return
            self.thread = Thread(target=self.run, name="CampusInbox PushPlus", daemon=True)
            self.thread.start()

    def run(self):
        while not self.stopping.wait(30):
            try:
                self.dispatch()
            except Exception:
                # A persisted sending claim is handled before any future delivery.
                continue

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=10)


def router(push):
    api = APIRouter(prefix="/api/connectors/wechat/push", dependencies=[Depends(loopback)])
    api.get("")(push.status)
    api.put("")(push.configure)

    @api.post("/test")
    def test_push():
        push.dispatch(test=True)
        return push.status()

    return api
