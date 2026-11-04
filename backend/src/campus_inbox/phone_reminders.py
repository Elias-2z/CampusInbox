"""Optional isolated Android reminder listener backed by the root database.

The Android v1 protocol uses plain LAN HTTP. It is off until explicitly started,
and the separate listener never exposes the main application's API.
"""

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel import Session, select

from campus_inbox import reminders
from campus_inbox.connectors.chaoxing_api import loopback
from campus_inbox.models import Message, Task, TaskEvent

PRIVATE_V4 = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{24,256}\Z")
EVENT_PATTERN = re.compile(r"[A-Za-z0-9:_.-]{1,200}\Z")


class PhoneError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


def allowed_ipv4(value: str) -> bool:
    try:
        address = ipaddress.IPv4Address(value)
        return address.is_loopback or any(address in network for network in PRIVATE_V4)
    except ipaddress.AddressValueError:
        return False


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def stamp(value: datetime) -> str:
    return aware(value).isoformat()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PhoneReminders:
    def __init__(self, engine, clock=lambda: datetime.now(UTC)):
        database = engine.url.database
        if not database or database == ":memory:":
            raise ValueError("Phone reminders require a persistent root database")
        self.engine = engine
        self.path = Path(database).with_name(Path(database).name + ".phone-reminders.db")
        self.clock = clock
        self.lock = threading.RLock()
        self.control = threading.Lock()
        self.server = None
        self.thread = None
        self.code = None
        self.code_expires = None
        self.attempts = {}
        self.code_attempts = 0
        self.server_id = None

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS devices ("
                "id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, name TEXT NOT NULL, "
                "paired_at TEXT NOT NULL, last_seen TEXT);"
                "CREATE TABLE IF NOT EXISTS receipts ("
                "device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE, "
                "event_id TEXT NOT NULL, issued INTEGER NOT NULL, acknowledged INTEGER NOT NULL, "
                "PRIMARY KEY(device_id,event_id));"
            )
            db.execute("INSERT OR IGNORE INTO meta VALUES ('server_id', ?)", (uuid4().hex,))
            self.server_id = db.execute("SELECT value FROM meta WHERE key='server_id'").fetchone()[
                0
            ]
        # No plaintext device token is stored. OS account permissions still apply.
        if self.path.exists():
            self.path.chmod(0o600)

    def status(self):
        with self.lock:
            devices = []
            if self.path.is_file():
                with self.connection() as db:
                    devices = [
                        dict(row)
                        for row in db.execute(
                            "SELECT id,name,paired_at,last_seen FROM devices ORDER BY paired_at"
                        )
                    ]
            address = None
            if self.server is not None:
                host, port = self.server.server_address
                address = f"http://{host}:{port}"
            return {
                "enabled": self.server is not None,
                "address": address,
                "devices": devices,
                "security_notice": (
                    "安卓连接使用局域网明文 HTTP，配对码、令牌和提醒可能被同网人员截获。"
                    "仅在可信网络开启。"
                ),
            }

    def start(self, host, port):
        if not allowed_ipv4(host) or type(port) is not int or not 0 <= port <= 65535:
            raise PhoneError(422, "请选择本机私有 IPv4 地址和有效端口。")
        with self.control:
            with self.lock:
                if self.server is not None:
                    raise PhoneError(409, "手机提醒服务已开启，请先停止。")
                self.initialize()
                try:
                    server = make_server(self, host, port)
                except OSError as error:
                    raise PhoneError(409, "无法监听该本机地址或端口，请检查网络和端口。") from error
                self.server = server
                self.thread = threading.Thread(target=server.serve_forever, daemon=True)
                self.thread.start()
            return self.status()

    def stop(self):
        with self.control:
            with self.lock:
                server, thread = self.server, self.thread
                self.server, self.thread = None, None
                self.code, self.code_expires = None, None
            if server is not None:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
            return self.status()

    def make_pairing(self):
        with self.lock:
            if self.server is None:
                raise PhoneError(409, "请先开启手机提醒服务。")
            self.code = f"{secrets.randbelow(1000000):06d}"
            self.code_expires = aware(self.clock()) + timedelta(minutes=5)
            self.attempts, self.code_attempts = {}, 0
            return {"code": self.code, "expires_at": stamp(self.code_expires)}

    def events(self, paired_at, current):
        cutoff = aware(paired_at)
        with Session(self.engine) as session:
            session.connection().exec_driver_sql("BEGIN")
            messages = session.exec(
                select(Message)
                .where(
                    Message.received_at >= cutoff,
                    Message.sent_at >= cutoff,
                    Message.source != "手动创建",
                )
                .order_by(Message.received_at, Message.id)
            ).all()
            changes = session.exec(
                select(TaskEvent, Task)
                .join(Task, Task.id == TaskEvent.task_id)
                .where(TaskEvent.created_at >= cutoff)
                .order_by(TaskEvent.created_at, TaskEvent.id)
            ).all()
            due = reminders.feed(session, aware(current)).items
            events = [
                {
                    "id": "message:" + row.id,
                    "kind": "new_message",
                    "title": "收到新通知",
                    "body": (row.source + "\n" + row.text)[:240],
                }
                for row in messages
            ]
            for change, task in changes:
                name = change.after.get("title") or task.title
                events.append(
                    {
                        "id": "task_event:" + change.id,
                        "kind": "new_task" if change.operation == "create" else "task_change",
                        "title": "任务已创建" if change.operation == "create" else "任务有变更",
                        "body": (task.course + " · " + str(name))[:240],
                    }
                )
            for item in due:
                when = (
                    item.due_date.isoformat() + "（具体时刻未明确）"
                    if item.due_date
                    else stamp(item.due_at)
                )
                events.append(
                    {
                        "id": "deadline:" + item.id,
                        "kind": "deadline_overdue" if item.phase == "overdue" else "deadline_due",
                        "title": "课程任务已逾期"
                        if item.phase == "overdue"
                        else "课程任务截止提醒",
                        "body": (item.task.course + " · " + item.task.title + "\n截止：" + when)[
                            :240
                        ],
                    }
                )
            return events

    def pair(self, code, name, peer):
        with self.lock:
            if self.server is None:
                raise PhoneError(409, "手机提醒服务已关闭。")
            tries = self.attempts.get(peer, 0)
            self.attempts[peer] = tries + 1
            self.code_attempts += 1
            if len(self.attempts) > 256:
                self.code = None
            if (
                tries >= 5
                or self.code_attempts > 20
                or not isinstance(code, str)
                or re.fullmatch(r"[0-9]{6}", code) is None
                or self.code is None
                or not hmac.compare_digest(code, self.code)
                or aware(self.clock()) >= self.code_expires
            ):
                raise PhoneError(400, "配对码无效、过期或尝试次数过多，请重新生成。")
            if (
                not isinstance(name, str)
                or not name.strip()
                or len(name) > 80
                or any(ord(character) < 32 for character in name)
            ):
                raise PhoneError(400, "请填写 1–80 字的手机名称。")
            moment = aware(self.clock())
            with self.connection() as db:
                if db.execute("SELECT COUNT(*) FROM devices").fetchone()[0] >= 10:
                    raise PhoneError(400, "最多配对 10 台设备，请先撤销旧设备。")
                token, device = secrets.token_urlsafe(32), uuid4().hex
                db.execute(
                    "INSERT INTO devices VALUES (?,?,?,?,NULL)",
                    (device, digest(token), name.strip(), stamp(moment)),
                )
                baseline = self.events(moment, moment)
                db.executemany(
                    "INSERT INTO receipts VALUES (?,?,0,1)",
                    ((device, event["id"]) for event in baseline),
                )
            self.code, self.code_expires = None, None
            return {"token": token, "device_id": device, "server_id": self.server_id}

    def device(self, db, token):
        if self.server is None:
            raise PhoneError(401, "服务已关闭。")
        if not isinstance(token, str) or TOKEN_PATTERN.fullmatch(token) is None:
            raise PhoneError(401, "设备未配对或已撤销。")
        row = db.execute("SELECT * FROM devices WHERE token_hash=?", (digest(token),)).fetchone()
        if row is None:
            raise PhoneError(401, "设备未配对或已撤销。")
        return row

    def poll(self, token):
        with self.lock, self.connection() as db:
            device = self.device(db, token)
            moment = aware(self.clock())
            events = self.events(datetime.fromisoformat(device["paired_at"]), moment)
            acknowledged = {
                row[0]
                for row in db.execute(
                    "SELECT event_id FROM receipts WHERE device_id=? AND acknowledged=1",
                    (device["id"],),
                )
            }
            pending = [event for event in events if event["id"] not in acknowledged][:20]
            db.executemany(
                "INSERT OR IGNORE INTO receipts VALUES (?,?,1,0)",
                ((device["id"], event["id"]) for event in pending),
            )
            db.execute("UPDATE devices SET last_seen=? WHERE id=?", (stamp(moment), device["id"]))
            return {
                "enabled": True,
                "server_id": self.server_id,
                "events": pending,
                "poll_after": 15,
            }

    def ack(self, token, ids):
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 100
            or any(
                not isinstance(value, str) or EVENT_PATTERN.fullmatch(value) is None
                for value in ids
            )
        ):
            raise PhoneError(400, "提醒回执无效。")
        with self.lock, self.connection() as db:
            device = self.device(db, token)
            for event_id in ids:
                if not db.execute(
                    "SELECT 1 FROM receipts WHERE device_id=? AND event_id=? AND issued=1",
                    (device["id"], event_id),
                ).fetchone():
                    raise PhoneError(400, "只能确认已发给本设备的提醒。")
            db.executemany(
                "UPDATE receipts SET acknowledged=1 WHERE device_id=? AND event_id=?",
                ((device["id"], event_id) for event_id in ids),
            )
            return {"ok": True}

    def revoke(self, device_id):
        if not isinstance(device_id, str) or re.fullmatch(r"[0-9a-f]{32}", device_id) is None:
            raise PhoneError(422, "设备编号无效。")
        with self.lock:
            if not self.path.is_file():
                raise PhoneError(404, "设备不存在。")
            with self.connection() as db:
                if not db.execute("DELETE FROM devices WHERE id=?", (device_id,)).rowcount:
                    raise PhoneError(404, "设备不存在。")
            return {"ok": True}


def make_server(hub, host, port):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *_args):
            pass

        def reply(self, status, result):
            content = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(content)

        def body(self):
            length = self.headers.get("Content-Length", "")
            if (
                not length.isdigit()
                or not 0 < int(length) <= 32768
                or self.headers.get("Transfer-Encoding")
                or not self.headers.get("Content-Type", "").lower().startswith("application/json")
            ):
                raise PhoneError(400, "请求格式无效。")
            try:
                value = json.loads(self.rfile.read(int(length)))
            except (ValueError, UnicodeDecodeError):
                raise PhoneError(400, "请求格式无效。") from None
            if not isinstance(value, dict):
                raise PhoneError(400, "请求格式无效。")
            return value

        def do_GET(self):
            self.route("GET")

        def do_POST(self):
            self.route("POST")

        def route(self, method):
            self.connection.settimeout(8)
            try:
                hosts, origins = (
                    self.headers.get_all("Host", []),
                    self.headers.get_all("Origin", []),
                )
                if len(hosts) != 1 or len(origins) > 1 or not allowed_ipv4(self.client_address[0]):
                    raise PhoneError(403, "只允许可信局域网连接。")
                target = urlsplit("http://" + hosts[0])
                bound_host, bound_port = self.server.server_address
                if (
                    target.hostname != bound_host
                    or (target.port or 80) != bound_port
                    or target.username
                    or target.password
                    or target.path
                    or target.query
                    or target.fragment
                    or (origins and origins[0] != f"http://{hosts[0]}")
                ):
                    raise PhoneError(403, "请求地址或来源不受信任。")
                if self.path == "/v1/health" and method == "GET":
                    return self.reply(
                        200,
                        {"app": "CourseInbox Reminders", "version": 1, "server_id": hub.server_id},
                    )
                if self.path == "/v1/pair" and method == "POST":
                    body = self.body()
                    if set(body) != {"code", "name"}:
                        raise PhoneError(400, "配对参数无效。")
                    return self.reply(
                        200, hub.pair(body["code"], body["name"], self.client_address[0])
                    )
                if self.path not in {"/v1/alerts", "/v1/ack"}:
                    raise PhoneError(404, "接口不存在。")
                headers = self.headers.get_all("Authorization", [])
                token = (
                    headers[0][7:] if len(headers) == 1 and headers[0].startswith("Bearer ") else ""
                )
                if self.path == "/v1/alerts" and method == "GET":
                    return self.reply(200, hub.poll(token))
                if self.path == "/v1/ack" and method == "POST":
                    body = self.body()
                    if set(body) != {"ids"}:
                        raise PhoneError(400, "提醒回执无效。")
                    return self.reply(200, hub.ack(token, body["ids"]))
                raise PhoneError(405, "方法不支持。")
            except PhoneError as error:
                self.reply(error.status, {"error": error.message})
            except (ValueError, TypeError):
                self.reply(400, {"error": "请求格式无效。"})
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
            except Exception:
                self.reply(500, {"error": "提醒服务暂不可用。"})

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = False

    return Server((host, port), Handler)


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str
    port: int = Field(default=8766, ge=0, le=65535)
    allow_unencrypted_lan: Literal[True]

    @field_validator("host")
    @classmethod
    def private_host(cls, value):
        if not allowed_ipv4(value):
            raise ValueError("请选择本机私有 IPv4 地址")
        return value


class RevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str


def control_router(hub):
    api = APIRouter(prefix="/api/mobile-reminders", dependencies=[Depends(loopback)])

    def invoke(operation, *args):
        try:
            return operation(*args)
        except PhoneError as error:
            raise HTTPException(error.status, error.message) from None

    @api.get("/status")
    def status():
        return hub.status()

    @api.post("/start")
    def start(body: StartRequest):
        return invoke(hub.start, body.host, body.port)

    @api.post("/stop")
    def stop():
        return hub.stop()

    @api.post("/pairing")
    def pairing():
        return invoke(hub.make_pairing)

    @api.post("/revoke")
    def revoke(body: RevokeRequest):
        return invoke(hub.revoke, body.device_id)

    return api
