"""Manage the existing read-only Chaoxing collector using the main database."""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import func, or_, update
from sqlmodel import Field, Session, SQLModel, select

from campus_inbox import attachments
from campus_inbox.connectors import platform_status
from campus_inbox.connectors.chaoxing import (
    UNPROCESSED_ATTACHMENT,
    ChaoxingEvent,
    ChaoxingIntake,
    archive_expired,
    attachment_only_notice,
    public_notice_original,
)
from campus_inbox.connectors.chaoxing_scope import archive_excluded, skip_reason
from campus_inbox.models import Attachment, LocalAttachmentExtraction, Message, Suggestion, Task

HEARTBEAT_TIMEOUT = 90
SOURCE_NAMES = ("notice", "chat", "task_center")
COUNT_FIELDS = (
    "checked_count",
    "captured_count",
    "failed_count",
    "pending_count",
    "detail_pending_count",
    "unsupported_detail_count",
    "media_pending_count",
)


def node_binary():
    node = shutil.which("node")
    if node:
        return node
    # Finder/launchd apps do not inherit the user's shell PATH.
    if sys.platform == "darwin":
        for candidate in (
            "/opt/homebrew/opt/node@24/bin/node",
            "/usr/local/opt/node@24/bin/node",
            "/opt/homebrew/bin/node",
            "/usr/local/bin/node",
        ):
            if Path(candidate).is_file() and os.access(candidate, os.X_OK):
                return candidate
    return None


def empty_sources():
    return {
        name: {
            "state": "disabled" if name == "chat" else "idle",
            "coverage": "unknown",
            "last_checked": None,
            "last_complete": None,
            "discovery_complete": False,
            "issues": [],
            "issue_count": 0,
            **dict.fromkeys(COUNT_FIELDS, 0),
        }
        for name in SOURCE_NAMES
    }


def section_key(value, kind):
    parts = [value.get("course_id"), value.get("class_id")]
    if kind not in ("assignment", "exam") or any(
        not isinstance(part, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", part)
        for part in parts
    ):
        return None
    ids = [str(int(part)) if part.isascii() and part.isdigit() else part for part in parts]
    return "|".join([*ids, kind])


class ChaoxingSettings(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    enabled: bool = False
    account_id: str = Field(default_factory=lambda: uuid4().hex)


class ChaoxingRuntime:
    def __init__(self, engine, provider_factory, data_dir, worker_root=None):
        self.engine = engine
        self.intake = ChaoxingIntake(engine, provider_factory)
        self.data_dir = Path(data_dir) / "chaoxing"
        self.worker_root = (
            Path(worker_root)
            if worker_root
            else Path(__file__).resolve().parents[4] / "integrations" / "chaoxing"
        )
        self.lock = threading.RLock()
        self.original_lock = threading.Lock()
        self.process = None
        self.stdin = None
        self.enabled = False
        self.account_id = ""
        self.state = "idle"
        self.error = ""
        self.session_error = ""
        self.qr_ready = False
        self.last_received = None
        self.reader = None
        self.queue_thread = None
        self.restart_attempts = 0
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.sources = empty_sources()
        self.sections = {}
        self.section_tasks = {}
        self.heartbeat_tick = None
        self.last_heartbeat = None

    def settings(self, session):
        row = session.get(ChaoxingSettings, 1)
        if row is None:
            row = ChaoxingSettings()
            session.add(row)
            session.commit()
            session.refresh(row)
        return row

    def status(self):
        with self.lock, Session(self.engine) as session:
            counts = {
                state: 0 for state in ("pending", "done", "failed", "needs_time", "needs_review")
            }
            for state, count in session.exec(
                select(ChaoxingEvent.state, func.count()).group_by(ChaoxingEvent.state)
            ):
                if state in counts:
                    counts[state] = count
            alive = self.process is not None and self.process.poll() is None
            freshness = (
                "paused"
                if not self.enabled
                else "offline"
                if not alive
                else "starting"
                if self.heartbeat_tick is None
                else "stale"
                if time.monotonic() - self.heartbeat_tick > HEARTBEAT_TIMEOUT
                else "starting"
                if self.last_heartbeat is None
                else "fresh"
            )
            return {
                "enabled": self.enabled,
                "session_saved": (self.data_dir / "session.bin").is_file(),
                "session_error": self.session_error,
                "session_storage": (
                    "macOS 钥匙串密钥 + 本机加密文件"
                    if sys.platform == "darwin"
                    else "Windows DPAPI 加密文件"
                    if sys.platform == "win32"
                    else "当前系统尚不支持安全保存登录"
                ),
                "state": "error" if freshness == "stale" else self.state,
                "error": "学习通采集器无响应，请重新连接。" if freshness == "stale" else self.error,
                "qr_ready": self.qr_ready and freshness != "stale",
                "last_received": self.last_received,
                "counts": counts,
                "sources": {name: dict(value) for name, value in self.sources.items()},
                "task_sections": list(self.sections.values()),
                "freshness": freshness,
                "last_heartbeat": self.last_heartbeat,
            }

    def _command(self, command):
        with self.lock:
            if self.stdin is None:
                return False
            try:
                self.stdin.write(json.dumps(command, ensure_ascii=False) + "\n")
                self.stdin.flush()
                return True
            except (OSError, ValueError):
                self.error = "学习通采集进程连接中断，请重新连接。"
                return False

    def repair_notice(self, key):
        with self.lock:
            if not any(item["key"] == key for item in self.sources["notice"]["issues"]):
                raise ValueError("请选择本次检查发现的通知缺口。")
            if not self.enabled or self.state not in ("connected", "syncing", "partial"):
                raise ValueError("请先确认学习通连接在线。")
            if not self._command({"type": "repair_notice", "key": key}):
                raise ValueError("采集进程不可用，请重新连接。")

    def repair_section(self, key):
        with self.lock:
            if key not in self.sections:
                raise ValueError("请选择本次连接已发现的课程栏目。")
            if not self.enabled or self.state not in ("connected", "syncing", "partial"):
                raise ValueError("请先确认学习通连接在线。")
            if not self._command({"type": "repair_sections", "keys": [key]}):
                raise ValueError("采集进程不可用，请重新连接。")
            self.sections[key] = {**self.sections[key], "state": "queued"}

    def listed_tasks(self, key):
        with self.lock:
            return list(self.section_tasks.get(key, {}).values())

    def repair_task(self, key):
        with self.lock:
            section = key.rsplit("|", 1)[0]
            tasks = self.section_tasks.get(section, {})
            if key not in tasks:
                raise ValueError("请选择本次连接已发现的作业或考试。")
            if not self.enabled or self.state not in ("connected", "syncing", "partial"):
                raise ValueError("请先确认学习通连接在线。")
            if not self._command({"type": "repair_tasks", "keys": [key]}):
                raise ValueError("采集进程不可用，请重新连接。")
            tasks[key] = {**tasks[key], "state": "queued"}

    def recheck_linked_task(self, task_id):
        with self.lock, Session(self.engine) as session:
            task = session.get(Task, task_id)
            message = session.get(Message, task.message_id) if task else None
            event = None
            if message and message.dedupe_key.startswith("chaoxing:"):
                event = session.exec(
                    select(ChaoxingEvent).where(ChaoxingEvent.identity == message.dedupe_key[9:])
                ).first()
            if event is None or event.account_id != self.settings(session).account_id:
                raise ValueError("该任务未关联当前学习通账号的作业或考试。")
            parts = platform_status.task_coordinates(event.payload)
            if not parts:
                raise ValueError("原通知缺少可核对的学习通任务编号。")
            course, group, category, platform_task = parts
            kind = "exam" if category == "exam" else "assignment"
            self.repair_task("|".join((course, group, kind, platform_task)))

    def record_sections(self, message):
        phase = message.get("phase")
        with self.lock:
            if phase == "catalog":
                courses = message.get("courses")
                if not isinstance(courses, list) or len(courses) > 100:
                    return
                sections = {}
                for course in courses:
                    if not isinstance(course, dict):
                        continue
                    index = course.get("course_index")
                    if type(index) is not int or not 1 <= index <= 100:
                        continue
                    for kind in ("assignment", "exam"):
                        key = section_key(course, kind)
                        if key:
                            sections[key] = {
                                "state": "unchecked",
                                **self.sections.get(key, {}),
                                "key": key,
                                "course": str(course.get("course") or "")[:100],
                                "course_index": index,
                                "kind": kind,
                            }
                self.sections = sections
                self.section_tasks = {
                    key: tasks for key, tasks in self.section_tasks.items() if key in sections
                }
                return
            key = section_key(message, message.get("kind"))
            if key not in self.sections:
                return
            if phase == "failure":
                state = "failed"
            elif phase == "section":
                state = "checked" if message.get("complete") is True else "partial"
                jobs = message.get("jobs")
                if isinstance(jobs, list) and len(jobs) <= 100:
                    prior = self.section_tasks.get(key, {})
                    tasks = {} if state == "checked" else dict(prior)
                    for job in jobs:
                        parts = (
                            platform_status.task_coordinates(job) if isinstance(job, dict) else None
                        )
                        if not parts:
                            continue
                        course, group, category, task = parts
                        kind = "exam" if category == "exam" else "assignment"
                        if "|".join((course, group, kind)) != key:
                            continue
                        task_key = key + "|" + task
                        status = "read" if job.get("detail_state") == "read" else "listed"
                        existing = prior.get(task_key, {})
                        if (
                            status == "listed"
                            and message.get("preliminary") is True
                            and existing.get("state") in ("read", "queued")
                        ):
                            status = existing["state"]
                        tasks[task_key] = {
                            "key": task_key,
                            "title": str(job.get("title") or "未命名任务")[:160],
                            "state": status,
                        }
                    self.section_tasks[key] = tasks
            else:
                return
            self.sections[key] = {**self.sections[key], "state": state, "checked_at": time.time()}

    def _save_session(self, value):
        from campus_inbox.connectors.session_crypto import seal

        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        if not isinstance(value, dict) or len(raw) > 2 * 1024 * 1024:
            raise ValueError("session invalid")
        encrypted = seal(raw, identity=self.data_dir)
        target = self.data_dir / "session.bin"
        temp = target.with_suffix(".tmp")
        for attempt in range(5):
            try:
                with os.fdopen(
                    os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb"
                ) as stream:
                    stream.write(encrypted)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temp, 0o600)
                temp.replace(target)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.2 * (attempt + 1))

    def _load_session(self):
        from campus_inbox.connectors.session_crypto import unseal

        path = self.data_dir / "session.bin"
        return (
            json.loads(unseal(path.read_bytes(), identity=self.data_dir))
            if path.is_file()
            else None
        )

    def _fetch_original(self, job, item):
        """Use the existing collector's source/permission-checked downloader."""
        if job.get("kind") == "course_notice":
            if not public_notice_original(item):
                raise ValueError("该通知附件的下载来源不受支持。")
            deadline = time.monotonic() + 20
            content = bytearray()
            with (
                httpx.Client(timeout=8, follow_redirects=False, trust_env=False) as client,
                client.stream(
                    "GET", item["url"], headers={"Accept-Encoding": "identity"}
                ) as response,
            ):
                response.raise_for_status()
                if (
                    response.status_code != 200
                    or response.headers.get("content-type", "").lower().startswith("text/html")
                    or response.headers.get("content-encoding", "identity") != "identity"
                ):
                    raise ValueError("附件响应格式不受支持。")
                for chunk in response.iter_bytes(chunk_size=65536):
                    if len(content) + len(chunk) > 8 * 1024 * 1024 or time.monotonic() > deadline:
                        raise ValueError("附件下载超出限制。")
                    content.extend(chunk)
            return bytes(content)
        session_data = self._load_session()
        node = node_binary()
        script = self.worker_root / "desktop-sync" / "original-file.cjs"
        if not node or not script.is_file() or not isinstance(session_data, dict):
            raise ValueError("请先登录学习通并安装采集器依赖。")
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("CI_MODEL_") and key not in {"CI_ACCESS_TOKEN", "MINERU_API_KEY"}
        }
        result = subprocess.run(
            [node, str(script)],
            input=json.dumps({"session": session_data, "job": job, "file": item}),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=100,
            cwd=self.worker_root,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode or len(result.stdout) > 12 * 1024 * 1024:
            raise ValueError("原件下载失败或超过主项目 8 MB 限制。")
        raw = json.loads(result.stdout).get("content_base64")
        if not isinstance(raw, str):
            raise ValueError("原件下载失败，请核对平台权限。")
        return base64.b64decode(raw, validate=True)

    def fetch_original(self, event_id, index):
        if not self.original_lock.acquire(blocking=False):
            raise ValueError("已有原件正在下载，请稍后重试。")
        try:
            with self.lock, self.intake.lock, Session(self.engine) as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                row = session.get(ChaoxingEvent, event_id)
                if row is None or row.account_id != self.settings(session).account_id:
                    raise ValueError("原件不属于当前学习通连接。")
                job = dict(row.payload)
                files = job.get("attachments")
                if (
                    job.get("kind") not in ("task_center", "course_notice")
                    or not isinstance(files, list)
                    or not 0 <= index < len(files)
                    or not isinstance(files[index], dict)
                ):
                    raise ValueError("该附件暂不支持下载。")
                item = dict(files[index])
                if item.get("content_base64"):
                    if row.suggestion_id:
                        suggestion = session.get(Suggestion, row.suggestion_id)
                        if suggestion is None:
                            raise ValueError("原通知不存在，请重新检查。")
                        self.intake._save_originals(
                            session, row, suggestion.message_id, only_index=index
                        )
                        session.commit()
                        self.wake.set()
                    return row.state
                if job["kind"] == "course_notice" and not public_notice_original(item):
                    raise ValueError("该通知附件的下载来源不受支持。")
                if not item.get("url"):
                    raise ValueError("附件地址尚未获取，请先在学习通核对原任务。")
                account_id = row.account_id
            try:
                content = self._fetch_original(job, item)
            except Exception:
                raise ValueError("原件下载失败，请核对登录、原任务权限及本机依赖后重试。") from None
            if not isinstance(content, bytes) or not 0 < len(content) <= 8 * 1024 * 1024:
                raise ValueError("原件为空或超过主项目 8 MB 限制，未保存。")
            with self.lock, self.intake.lock, Session(self.engine) as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                row = session.get(ChaoxingEvent, event_id)
                current_account = self.settings(session).account_id
                current = row.payload.get("attachments") if row else None
                if (
                    row is None
                    or row.account_id != account_id
                    or current_account != account_id
                    or not isinstance(current, list)
                    or index >= len(current)
                    or not isinstance(current[index], dict)
                    or any(current[index].get(key) != item.get(key) for key in ("name", "url"))
                ):
                    raise ValueError("下载期间账号或原件信息已变化，结果未关联，请重新检查。")
                if current[index].get("content_base64"):
                    return row.state
                updated = list(current)
                updated[index] = {
                    **current[index],
                    "content_base64": base64.b64encode(content).decode(),
                    "status": "downloaded",
                }
                row.payload = {**row.payload, "attachments": updated}
                if row.state == "needs_review" and attachment_only_notice(row.payload):
                    row.state = "pending"
                if row.suggestion_id:
                    suggestion = session.get(Suggestion, row.suggestion_id)
                    if suggestion is None:
                        raise ValueError("原通知不存在，请重新检查。")
                    self.intake._save_originals(session, row, suggestion.message_id)
                session.add(row)
                session.commit()
                # Do not call the model or alter reviewed tasks. Text extraction and
                # reanalysis retain the existing queue and explicit review flows.
                self.wake.set()
                return row.state
        finally:
            self.original_lock.release()

    def handle(self, message):
        if not isinstance(message, dict):
            return
        with self.lock:
            if not self.enabled:
                return
        kind = message.get("type")
        if not isinstance(kind, str):
            return
        if kind == "task_inventory":
            self.record_sections(message)
            return
        if kind == "heartbeat":
            with self.lock:
                self.heartbeat_tick, self.last_heartbeat = time.monotonic(), time.time()
            return
        if kind == "source":
            name, state = message.get("source"), message.get("state")
            if (
                not isinstance(name, str)
                or not isinstance(state, str)
                or name not in SOURCE_NAMES
                or state
                not in {
                    "idle",
                    "connecting",
                    "syncing",
                    "connected",
                    "retrying",
                    "partial",
                    "expired",
                    "error",
                }
            ):
                return
            with self.lock:
                source = self.sources[name]
                source["state"] = state
                if isinstance(message.get("coverage"), str) and message.get("coverage") in {
                    "unknown",
                    "loaded_text",
                    "partial",
                    "list_only",
                    "complete",
                }:
                    source["coverage"] = message["coverage"]
                if state != "connected" and source["coverage"] == "complete":
                    source["coverage"] = "partial" if state == "partial" else "unknown"
                for field in COUNT_FIELDS:
                    value = message.get(field)
                    if type(value) is int and 0 <= value <= 1_000_000:
                        source[field] = value
                issues = message.get("issues")
                if name == "notice" and isinstance(issues, list):
                    source["issues"] = [
                        {
                            "key": item["key"],
                            "notice_index": item["notice_index"],
                            "detail_pending": item.get("detail_pending") is True,
                            "media_pending_count": item["media_pending_count"],
                        }
                        for item in issues[:10]
                        if isinstance(item, dict)
                        and isinstance(item.get("key"), str)
                        and re.fullmatch(r"[a-f0-9]{64}", item["key"])
                        and type(item.get("notice_index")) is int
                        and 1 <= item["notice_index"] <= 1_000_000
                        and type(item.get("media_pending_count")) is int
                        and 0 <= item["media_pending_count"] <= 1_000_000
                    ]
                    listed = len(source["issues"])
                    total = message.get("issue_count")
                    source["issue_count"] = (
                        total if type(total) is int and listed <= total <= 1_000_000 else listed
                    )
                if name == "task_center" and type(message.get("discovery_complete")) is bool:
                    source["discovery_complete"] = message["discovery_complete"]
                pending = any(
                    source[field]
                    for field in (
                        "failed_count",
                        "pending_count",
                        "detail_pending_count",
                        "unsupported_detail_count",
                        "media_pending_count",
                    )
                )
                if state == "connected" and (
                    pending
                    or source["coverage"] in {"partial", "list_only"}
                    or (name == "task_center" and not source["discovery_complete"])
                ):
                    source["state"] = "partial"
                    if source["coverage"] == "complete":
                        source["coverage"] = "partial"
                if message.get("checked") is True and source["state"] in {
                    "connected",
                    "partial",
                    "syncing",
                }:
                    source["last_checked"] = time.time()
                    if (
                        source["state"] == "connected"
                        and source["coverage"] == "complete"
                        and not pending
                    ):
                        source["last_complete"] = source["last_checked"]
            return
        if kind == "status":
            with self.lock:
                state = message.get("state")
                if isinstance(state, str) and state in {
                    "idle",
                    "connecting",
                    "waiting_scan",
                    "connected",
                    "syncing",
                    "expired",
                    "retrying",
                    "partial",
                    "error",
                }:
                    self.state = state
                self.qr_ready = bool(message.get("qr_ready")) and self.state == "waiting_scan"
                if self.state in {"retrying", "error", "expired"}:
                    self.error = (
                        "未找到学习通专用浏览器，请安装 Chrome，或按说明安装 Playwright Chromium。"
                        if message.get("error") == "browser_missing"
                        else "学习通暂时无法同步，请检查网络或重新登录。"
                    )
                elif self.state in {"connected", "syncing"}:
                    self.error = ""
                    if self.state == "connected" and message.get("checked") is True:
                        self.restart_attempts = 0
            return
        if kind == "session":
            try:
                self._save_session(message.get("session"))
                with self.lock:
                    self.session_error = ""
            except Exception:
                with self.lock:
                    self.session_error = (
                        "登录状态无法安全保存，重启后可能需要重新扫码；请检查钥匙串或系统凭据权限。"
                    )
                    self.error = self.session_error
            return
        if kind not in {"notice", "chat", "task_center", "checkin_activity"}:
            return
        job = message.get("job")
        expected = {
            "notice": "course_notice",
            "chat": "chat",
            "task_center": "task_center",
            "checkin_activity": "checkin_activity",
        }[kind]
        if not isinstance(job, dict) or job.get("kind") != expected:
            return
        if skip_reason(job):
            ident = job.get("message_id")
            if isinstance(ident, str) and 0 < len(ident) <= 200:
                self._command({"type": "accepted", "id": ident, "complete": True})
            return
        if kind == "chat" and job.get("direction") == "sent":
            if isinstance(job.get("message_id"), str) and 0 < len(job["message_id"]) <= 200:
                self._command({"type": "accepted", "id": job["message_id"], "complete": True})
            return
        try:
            self.intake.receive(self.account_id, job)
        except (ValueError, TypeError, OSError):
            with self.lock:
                self.error = "部分学习通消息未能保存，采集器将保留重试。"
            return
        except Exception:
            with self.lock:
                self.error = "学习通消息保存失败，采集器将保留重试。"
            return
        # The acknowledgement follows the durable database commit, not analysis.
        # Attachments are retained in the raw event but are not yet imported into
        # the main attachment store. Keep them eligible for a later retry.
        media_incomplete = bool(job.get("attachments") or job.get("media_pending_count"))
        self._command(
            {"type": "accepted", "id": job["message_id"], "complete": not media_incomplete}
        )
        with self.lock:
            self.last_received = time.time()
        self.wake.set()

    def process_pending(self):
        with Session(self.engine) as session:
            archive_expired(session)
            ids = list(
                session.exec(
                    select(ChaoxingEvent.id).where(ChaoxingEvent.state == "pending").limit(100)
                )
            )
        for event_id in ids:
            if self.stopping.is_set() or not self.enabled:
                break
            self.intake.process(event_id)
        if self.enabled and not self.stopping.is_set():
            self.process_local_attachments()
        with Session(self.engine) as session:
            archive_expired(session)

    def process_local_attachments(self):
        extensions = attachments.LOCAL_DOCUMENT_TYPES
        if attachments.supports_local_ocr():
            extensions = extensions | attachments.LOCAL_OCR_TYPES
        with Session(self.engine) as session:
            # Revisit the old unconditional DOCX warning once, for pending drafts only.
            legacy_ids = session.exec(
                select(Attachment.id)
                .join(Suggestion, Suggestion.message_id == Attachment.message_id)
                .where(
                    Suggestion.status == "pending",
                    Attachment.status == "ready",
                    Attachment.error == "仅提取正文文字，图片等内容请对照原文件核对。",
                    func.lower(Attachment.filename).endswith(".docx"),
                )
                .limit(5)
            ).all()
            for attachment_id in legacy_ids:
                changed = session.execute(
                    update(Attachment)
                    .where(
                        Attachment.id == attachment_id,
                        Attachment.status == "ready",
                        Attachment.error == "仅提取正文文字，图片等内容请对照原文件核对。",
                    )
                    .values(status="failed", error=UNPROCESSED_ATTACHMENT)
                ).rowcount
                if not changed:
                    continue
                session.connection().exec_driver_sql(
                    "INSERT INTO localattachmentextraction (attachment_id, state) "
                    "VALUES (?, 'pending') "
                    "ON CONFLICT(attachment_id) DO UPDATE SET state='pending'",
                    (attachment_id,),
                )
            # Only enqueue untouched learning-platform originals in the existing local queue.
            old_ids = session.exec(
                select(Attachment.id)
                .join(Suggestion, Suggestion.message_id == Attachment.message_id)
                .join(ChaoxingEvent, ChaoxingEvent.suggestion_id == Suggestion.id)
                .outerjoin(
                    LocalAttachmentExtraction,
                    LocalAttachmentExtraction.attachment_id == Attachment.id,
                )
                .where(
                    LocalAttachmentExtraction.attachment_id.is_(None),
                    Attachment.status == "failed",
                    Attachment.error == UNPROCESSED_ATTACHMENT,
                    or_(*(func.lower(Attachment.filename).endswith(ext) for ext in extensions)),
                )
                .limit(5)
            ).all()
            for attachment_id in old_ids:
                session.connection().exec_driver_sql(
                    "INSERT OR IGNORE INTO localattachmentextraction "
                    "(attachment_id, state) VALUES (?, 'pending')",
                    (attachment_id,),
                )
            session.commit()
            ids = list(
                session.exec(
                    select(LocalAttachmentExtraction.attachment_id)
                    .where(LocalAttachmentExtraction.state == "pending")
                    .limit(5)
                )
            )
        directory = Path(self.engine.url.database).parent / "uploads"
        for attachment_id in ids:
            if self.stopping.is_set() or not self.enabled:
                break
            with Session(self.engine) as session:
                item = session.get(Attachment, attachment_id)
                eligible = bool(item and item.status == "failed" and not item.batch_id)
                path = attachments.original_path(directory, item) if eligible else None
                previous_error = item.error if eligible else None
            if path:
                try:
                    is_ocr = path.suffix.lower() in attachments.LOCAL_OCR_TYPES
                    extractor = (
                        attachments.extract_local_ocr if is_ocr else attachments.extract_local_docx
                    )
                    extracted, warning = extractor(path)
                    values = {
                        "status": "failed" if is_ocr and warning else "ready",
                        "text": extracted,
                        "error": warning,
                    }
                except Exception:
                    values = {"status": "failed", "error": "本机解析失败，请下载原文件核对。"}
            else:
                values = None
            # Pausing or shutting down must leave in-flight work pending for restart.
            with self.lock:
                if self.stopping.is_set() or not self.enabled:
                    break
                with Session(self.engine) as session:
                    queue_state = "complete"
                    if values is not None:
                        changed = session.execute(
                            update(Attachment)
                            .where(
                                Attachment.id == attachment_id,
                                Attachment.status == "failed",
                                Attachment.batch_id == "",
                                Attachment.error == previous_error,
                            )
                            .values(**values)
                        ).rowcount
                        if changed and values["status"] == "ready" and not values["error"]:
                            queue_state = "awaiting_refresh"
                    session.execute(
                        update(LocalAttachmentExtraction)
                        .where(
                            LocalAttachmentExtraction.attachment_id == attachment_id,
                            LocalAttachmentExtraction.state == "pending",
                        )
                        .values(state=queue_state)
                    )
                    session.commit()

        from campus_inbox.services import refresh_attachment_suggestion

        # Refresh intent is durable so a failure/restart does not repeat OCR or lose the update.
        with Session(self.engine) as session:
            pending_refresh = session.exec(
                select(LocalAttachmentExtraction.attachment_id, Attachment.message_id)
                .join(Attachment, Attachment.id == LocalAttachmentExtraction.attachment_id)
                .where(LocalAttachmentExtraction.state == "awaiting_refresh")
                .limit(100)
            ).all()
        for attachment_id, message_id in pending_refresh:
            if self.stopping.is_set() or not self.enabled:
                break
            with Session(self.engine) as session:
                if message_id:
                    refresh_attachment_suggestion(session, message_id)
                session.execute(
                    update(LocalAttachmentExtraction)
                    .where(
                        LocalAttachmentExtraction.attachment_id == attachment_id,
                        LocalAttachmentExtraction.state == "awaiting_refresh",
                    )
                    .values(state="complete")
                )
                session.commit()

    def _queue_loop(self):
        while not self.stopping.is_set():
            self.wake.wait(5)
            self.wake.clear()
            try:
                self.process_pending()
            except Exception:
                with self.lock:
                    self.error = "学习通消息处理未完成，可稍后重试。"

    def _read(self, process):
        try:
            for line in process.stdout:
                if len(line) > 12 * 1024 * 1024:
                    continue
                try:
                    value = json.loads(line)
                except (ValueError, TypeError):
                    continue
                with self.lock:
                    if not self.enabled or self.process is not process:
                        break
                    self.handle(value)
        finally:
            with self.lock:
                if self.enabled and self.process is process:
                    self.qr_ready = False
                    if self.restart_attempts >= 5:
                        self.state = "error"
                        self.error = "学习通采集进程反复停止，请检查依赖后重新连接。"
                    else:
                        delay = min(10 * 2**self.restart_attempts, 60)
                        self.restart_attempts += 1
                        self.state = "retrying"
                        self.error = "学习通采集进程已停止，正在尝试恢复。"
                        threading.Thread(
                            target=self._recover, args=(process, delay), daemon=True
                        ).start()

    def _recover(self, old_process, delay):
        if self.stopping.wait(delay):
            return
        with self.lock:
            if not self.enabled or self.process is not old_process:
                return
            if old_process.poll() is None:
                old_process.terminate()
            self.process, self.stdin = None, None
            self.start()

    def start(self, *, visible=False):
        with self.lock:
            if (
                self.process is not None
                and self.process.poll() is None
                and self.heartbeat_tick is not None
                and time.monotonic() - self.heartbeat_tick > HEARTBEAT_TIMEOUT
            ):
                self._stop_process()
            if self.process is not None and self.process.poll() is None:
                if visible:
                    try:
                        session_data = self._load_session()
                    except Exception:
                        session_data = None
                    self.state, self.qr_ready = "connecting", False
                    self._command(
                        {"type": "start", "session": session_data, "visible": True, "accepted": []}
                    )
                return self.status()
            with Session(self.engine) as session:
                row = self.settings(session)
                row.enabled = True
                self.account_id = row.account_id
                session.add(row)
                session.commit()
            self.enabled = True
            self.stopping.clear()
            archive_excluded(self.engine)
            self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if sys.platform == "darwin":
                self.data_dir.chmod(0o700)
            node = node_binary()
            if not node:
                self.state, self.error = "error", "未找到 Node.js，请先按说明安装依赖。"
                return self.status()
            try:
                session_data = self._load_session()
            except Exception:
                session_data = None
                self.error = "原登录状态无法读取，请重新扫码。"
            env = dict(os.environ)
            for key in list(env):
                if key.startswith("CI_MODEL_") or key in {"CI_ACCESS_TOKEN", "MINERU_API_KEY"}:
                    env.pop(key)
            worker_dir = self.worker_root / "desktop-sync"
            if not (worker_dir / "worker.cjs").is_file():
                self.state, self.error = "error", "未找到学习通采集器，请检查项目文件。"
                return self.status()
            try:
                probe = subprocess.run(
                    [node, "-e", "require.resolve('playwright')"],
                    cwd=worker_dir,
                    env=env,
                    capture_output=True,
                    timeout=8,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if probe.returncode:
                    self.state = "error"
                    self.error = "缺少学习通 Playwright 依赖，请按项目说明安装。"
                    return self.status()
            except (OSError, subprocess.TimeoutExpired):
                self.state, self.error = "error", "Node.js 无法运行，请检查学习通依赖。"
                return self.status()
            try:
                self.process = subprocess.Popen(
                    [
                        node,
                        str(self.worker_root / "desktop-sync" / "worker.cjs"),
                        str(self.data_dir),
                    ],
                    cwd=self.worker_root,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError:
                self.state, self.error = "error", "无法启动学习通采集器，请检查 Node.js 和依赖。"
                return self.status()
            self.stdin = self.process.stdin
            self.state = "connecting"
            self.sources = empty_sources()
            self.sections = {}
            self.section_tasks = {}
            self.heartbeat_tick = time.monotonic()
            self.last_heartbeat = None
            self.reader = threading.Thread(target=self._read, args=(self.process,), daemon=True)
            self.reader.start()
            if self.queue_thread is None or not self.queue_thread.is_alive():
                self.queue_thread = threading.Thread(target=self._queue_loop, daemon=True)
                self.queue_thread.start()
            self._command(
                {"type": "start", "session": session_data, "visible": visible, "accepted": []}
            )
            self.wake.set()
            return self.status()

    def resume_if_enabled(self):
        with Session(self.engine) as session:
            row = session.get(ChaoxingSettings, 1)
            enabled = row is not None and row.enabled
        if enabled:
            self.start()

    def pause(self):
        with self.lock:
            self.enabled = False
            with Session(self.engine) as session:
                row = self.settings(session)
                row.enabled = False
                session.add(row)
                session.commit()
            self._stop_process()
            self.state, self.qr_ready = "paused", False
            for source in self.sources.values():
                source["state"] = "paused"
            return self.status()

    def forget_login(self):
        self.pause()
        with self.lock:
            scan_state = self.data_dir / "scan-state.json"
            if scan_state.exists():
                # Keep the old account's cursor for recovery, but never apply it
                # to a different account after the next login.
                scan_state.replace(self.data_dir / f"scan-state-previous-{uuid4().hex}.json")
            (self.data_dir / "session.bin").unlink(missing_ok=True)
            (self.data_dir / "qr.png").unlink(missing_ok=True)
            with Session(self.engine) as session:
                row = self.settings(session)
                row.account_id = uuid4().hex
                self.account_id = row.account_id
                session.add(row)
                session.commit()
            self.state, self.error = "idle", ""
            self.session_error = ""
            self.sources = empty_sources()
            self.sections = {}
            self.section_tasks = {}
            self.heartbeat_tick = self.last_heartbeat = None
            return self.status()

    def _stop_process(self):
        self.stopping.set()
        self.wake.set()
        process = self.process
        if process is not None and process.poll() is None:
            self._command({"type": "close"})
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=4)
        self.process, self.stdin = None, None

    def shutdown(self):
        with self.lock:
            self.enabled = False
            self._stop_process()
