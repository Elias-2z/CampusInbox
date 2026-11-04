"""Local OneBot HTTP bridge, with OS-vault credentials and signed event intake."""

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from campus_inbox.connectors.credentials import WindowsCredentials
from campus_inbox.connectors.qq import AdapterState, Batch, Group, Incoming, Member

MAX_BYTES = 256 * 1024


class BridgeError(Exception):
    pass


class Keychain:
    """Only the OS credential store holds tokens. Never capture or log command errors."""

    label = "macOS 钥匙串"

    @property
    def supported(self):
        return sys.platform == "darwin"

    def __init__(self, identity):
        self.service = "CampusInbox.QQ." + hashlib.sha256(str(identity).encode()).hexdigest()[:16]

    def get(self, key):
        if sys.platform != "darwin":
            return ""
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", self.service, "-a", key, "-w"],
            capture_output=True,
            timeout=15,
            check=False,
        )
        return result.stdout.decode().strip() if result.returncode == 0 else ""

    def set(self, key, value):
        if sys.platform != "darwin":
            raise BridgeError("当前配置入口使用 macOS 钥匙串，Windows 请接入对应凭据适配器。")
        # security's supported CLI accepts the password as an argument; no shell or logs.
        result = subprocess.run(
            [
                "/usr/bin/security",
                "add-generic-password",
                "-U",
                "-s",
                self.service,
                "-a",
                key,
                "-w",
                value,
            ],
            capture_output=True,
            timeout=15,
            check=False,
        )
        if result.returncode:
            raise BridgeError("无法保存 QQ 凭据，请检查 macOS 钥匙串权限。")


def credential_store(directory):
    """Keep existing Mac targets; Windows uses its own user-local credential vault."""
    return WindowsCredentials(directory) if sys.platform == "win32" else Keychain(directory)


def local_url(value):
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in ("127.0.0.1", "::1")
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or not parsed.port
        ):
            raise ValueError
    except ValueError:
        raise ValueError("只接受带端口的本机地址，例如 http://127.0.0.1:3000") from None
    return value.rstrip("/")


class BridgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    base_url: str = "http://127.0.0.1:3000"

    _local = field_validator("base_url")(local_url)


class BridgeUpdate(BridgeConfig):
    access_token: str = Field(default="", max_length=2048)
    event_secret: str = Field(default="", max_length=2048)

    @field_validator("access_token", "event_secret")
    @classmethod
    def credentials(cls, value):
        if value and (len(value) < 24 or any(ord(c) < 33 or ord(c) > 126 for c in value)):
            raise ValueError("密钥需为至少 24 位的 ASCII 字符，不能含空格或换行")
        return value


class OneBotAdapter:
    def __init__(self, directory: Path, *, secrets=None, transport=None):
        self.path = directory / "qq-onebot.json"
        self.secrets = secrets if secrets is not None else credential_store(directory.resolve())
        self.transport = transport
        self.lock = threading.RLock()
        self.config = BridgeConfig()
        self.state = AdapterState(name="未配置本机 OneBot 消息桥")
        self.checked = 0.0
        self.issue = ""
        self.tokens = {}
        if self.path.exists():
            try:
                self.config = BridgeConfig.model_validate_json(self.path.read_text())
            except (ValueError, OSError):
                self.issue = "QQ 桥接配置无法读取，请重新保存。"

    def secret(self, key):
        if key not in self.tokens:
            self.tokens[key] = self.secrets.get(key)
        return self.tokens[key]

    def public(self):
        with self.lock:
            saved = {}
            credential_error = ""
            for key in ("access", "events"):
                try:
                    saved[key] = bool(self.secret(key))
                except Exception:
                    saved[key] = False
                    credential_error = "无法读取系统凭据，请重新填写两项密钥或检查权限。"
            return dict(
                **self.config.model_dump(),
                platform=sys.platform,
                credential_storage=getattr(self.secrets, "label", "本机安全存储"),
                credential_supported=getattr(self.secrets, "supported", True),
                token_saved=saved["access"],
                event_secret_saved=saved["events"],
                state=self.state,
                error=credential_error or self.issue,
            )

    def configure(self, update):
        with self.lock:
            if update.enabled and not (
                (update.access_token or self.secret("access"))
                and (update.event_secret or self.secret("events"))
            ):
                raise BridgeError("请填写消息桥 Access Token 和事件签名密钥。")
            for key, value in (("access", update.access_token), ("events", update.event_secret)):
                if value:
                    self.secrets.set(key, value)
                    self.tokens[key] = value
            config = BridgeConfig(enabled=update.enabled, base_url=update.base_url)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(dir=self.path.parent, prefix=".qq-config-")
            try:
                with os.fdopen(descriptor, "w") as file:
                    file.write(config.model_dump_json())
                Path(name).replace(self.path)
            finally:
                Path(name).unlink(missing_ok=True)
            self.config, self.checked, self.issue = config, 0.0, ""
            self.state = AdapterState(
                name="配置已保存，请检查连接" if config.enabled else "消息桥已停用"
            )
            return self.public()

    def call(self, action, params=None):
        if not self.config.enabled or not self.secret("access"):
            raise BridgeError("消息桥未启用或缺少凭据。")
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=5,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream(
                    "POST",
                    self.config.base_url + "/" + action,
                    headers={"Authorization": "Bearer " + self.secret("access")},
                    json=params or {},
                ) as response:
                    response.raise_for_status()
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 2 * 1024 * 1024:
                            raise ValueError("response too large")
            result = json.loads(body)
            if (
                not isinstance(result, dict)
                or result.get("status") != "ok"
                or type(result.get("retcode")) is not int
                or result["retcode"] != 0
            ):
                raise ValueError("invalid bridge response")
            return result["data"]
        except Exception:
            raise BridgeError(
                "消息桥请求失败，请核对地址、Token、QQ 登录和 OneBot HTTP 服务。"
            ) from None

    def status(self, force=False):
        with self.lock:
            if not self.config.enabled:
                return AdapterState(name="本机 OneBot 消息桥未启用")
            if not force and time.monotonic() - self.checked < 10:
                return self.state
            self.checked = time.monotonic()
            try:
                health, login = self.call("get_status"), self.call("get_login_info")
                if (
                    not isinstance(health, dict)
                    or health.get("online") is not True
                    or health.get("good") is not True
                ):
                    raise BridgeError("QQ 当前不在线，请在消息桥中登录。")
                account = str(login["user_id"])
                Group(id=account, name="account")
                self.state = AdapterState(
                    connected=True,
                    account_id=account,
                    name="本机 OneBot HTTP 消息桥",
                )
                self.issue = ""
            except Exception:
                self.state = AdapterState(name="消息桥或 QQ 账号不可用")
                self.issue = "检查未通过，请确认消息桥服务、Token 和 QQ 登录状态。"
            return self.state

    def groups(self):
        with self.lock:
            if not self.status().connected:
                return []
            data = self.call("get_group_list")
            try:
                if not isinstance(data, list) or len(data) > 2000:
                    raise ValueError
                return [
                    Group(id=str(g["group_id"]), name=g["group_name"] or str(g["group_id"]))
                    for g in data
                ]
            except Exception:
                raise BridgeError("消息桥群列表格式不正确。") from None

    def members(self, group_id):
        Group(id=group_id, name="group")
        with self.lock:
            if not self.status().connected:
                return []
            data = self.call("get_group_member_list", {"group_id": int(group_id)})
            try:
                if not isinstance(data, list) or len(data) > 10000:
                    raise ValueError
                return [
                    Member(
                        id=str(m["user_id"]),
                        name=(m.get("card") or m.get("nickname") or str(m["user_id"]))[:100],
                    )
                    for m in data
                ]
            except Exception:
                raise BridgeError("消息桥成员列表格式不正确。") from None

    def poll(self, cursor):
        # HTTP POST receives events durably. No in-memory notification queue or fake history cursor.
        return Batch(cursor=cursor)

    def close(self):
        # App shutdown releases no live HTTP client and must preserve saved connection intent.
        pass

    def disconnect(self):
        with self.lock:
            self.configure(BridgeUpdate(enabled=False, base_url=self.config.base_url))


def normalize(event):
    if not isinstance(event, dict):
        raise ValueError("event must be object")
    if event.get("post_type") != "message" or event.get("message_type") != "group":
        return None
    message = event.get("message")
    media = []
    if isinstance(message, str):
        # CQ encoded strings: never interpret CQ URLs or download media.
        media = re.findall(r"\[CQ:([a-zA-Z_]+)(?:,[^\]]*)?\]", message)
        text = re.sub(r"\[CQ:[^\]]*\]", "", message)
        text = text.replace("&#91;", "[").replace("&#93;", "]").replace("&amp;", "&")
    elif isinstance(message, list) and len(message) <= 200:
        parts = []
        for segment in message:
            if not isinstance(segment, dict) or not isinstance(segment.get("data"), dict):
                raise ValueError("invalid message segment")
            if segment.get("type") == "text":
                value = segment["data"].get("text")
                if not isinstance(value, str):
                    raise ValueError("invalid text segment")
                parts.append(value)
            else:
                kind = segment.get("type")
                if not isinstance(kind, str) or not re.fullmatch(r"[a-zA-Z_]{1,40}", kind):
                    raise ValueError("invalid segment type")
                media.append(kind)
        text = "".join(parts)
    else:
        raise ValueError("invalid message")
    if len(text.strip()) < 2:
        return None  # Pure media is not a text task; cannot claim its contents were read.
    timestamp = event.get("time")
    if type(timestamp) is not int or timestamp <= 0:
        raise ValueError("missing actual timestamp")
    for key in ("self_id", "group_id", "user_id", "message_id"):
        if type(event.get(key)) not in (int, str):
            raise ValueError("invalid identity")
    if not re.fullmatch(r"-?[0-9]{1,20}", str(event["message_id"])):
        raise ValueError("invalid message identity")
    return Incoming(
        account_id=str(event["self_id"]),
        group_id=str(event["group_id"]),
        sender_id=str(event["user_id"]),
        message_id=str(event["message_id"]),
        sent_at=datetime.fromtimestamp(timestamp, UTC),
        text=text,
        sender_name=str(
            (event.get("sender") or {}).get("card")
            or (event.get("sender") or {}).get("nickname")
            or ""
        )[:100]
        if isinstance(event.get("sender"), dict)
        else "",
        media_types=sorted(set(media) - {"at", "face", "reply"}),
    )


class PrivateValidationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # FastAPI's default validation detail echoes the submitted password as input.
                raise HTTPException(
                    422, "消息桥配置无效；使用本机带端口地址、至少 24 位 ASCII 密钥。"
                ) from None

        return safe


def router(core):
    api = APIRouter(
        prefix="/api/connectors/qq/onebot",
        tags=["local OneBot bridge"],
        route_class=PrivateValidationRoute,
    )

    def driver():
        if not isinstance(core.adapter, OneBotAdapter):
            raise HTTPException(409, "当前使用其他 QQ 适配器。")
        return core.adapter

    def describe(request):
        port = (request.scope.get("server") or ("127.0.0.1", 8000))[1]
        return {
            **driver().public(),
            "callback_url": f"http://127.0.0.1:{port}/api/connectors/qq/onebot/events",
        }

    @api.get("")
    def config(request: Request):
        try:
            return describe(request)
        except Exception:
            raise HTTPException(
                503, "无法读取消息桥配置或系统凭据，请检查权限或重新配置。"
            ) from None

    @api.put("")
    def configure(body: BridgeUpdate, request: Request):
        try:
            driver().configure(body)
            return describe(request)
        except Exception:
            raise HTTPException(422, "无法保存配置，请检查密钥及系统凭据存储权限。") from None

    @api.post("/probe")
    def probe(request: Request):
        try:
            bridge = driver()
            bridge.status(force=True)
            return describe(request)
        except Exception:
            raise HTTPException(503, "无法读取系统凭据，请重新配置或检查权限。") from None

    @api.post("/events", status_code=204)
    async def receive(request: Request):
        bridge = driver()
        # Callback is machine-to-machine only. Never accept browser-originated uploads.
        host = request.url.hostname
        if (
            request.headers.get("origin")
            or host not in ("127.0.0.1", "localhost", "::1")
            or not request.client
            or request.client.host not in ("127.0.0.1", "::1")
        ):
            raise HTTPException(403, "事件上报仅允许本机消息桥。")
        if not bridge.config.enabled:
            raise HTTPException(503, "消息桥接收已停用。")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise HTTPException(413, "事件超过 256 KB。")
        try:
            secret = bridge.secret("events")
        except Exception:
            raise HTTPException(503, "无法读取事件凭据。") from None
        expected = "sha1=" + hmac.new(secret.encode(), bytes(body), hashlib.sha1).hexdigest()
        if not secret or not hmac.compare_digest(
            request.headers.get("x-signature", "").encode(), expected.encode()
        ):
            raise HTTPException(401, "事件签名无效。")
        try:
            event = normalize(json.loads(body))
        except Exception:
            raise HTTPException(422, "QQ 事件格式无效或缺少实际发送时间。") from None
        if event is not None:
            try:
                await run_in_threadpool(core.receive, event)
            except Exception:
                raise HTTPException(503, "事件尚未保存，请消息桥稍后重试。") from None
        return Response(status_code=204)

    return api
