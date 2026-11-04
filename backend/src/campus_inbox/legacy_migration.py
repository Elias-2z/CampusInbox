"""Review and explicitly migrate selected legacy records into the main application."""

import json
import os
import re
import sqlite3
import tempfile
import zipfile
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from campus_inbox.attachments import persist_original
from campus_inbox.connectors.chaoxing_api import loopback
from campus_inbox.legacy_bundle import (
    MAX_BUNDLE,
    STATUSES,
    digest,
    fields,
    load_bundle,
    stamp,
    validate,
)
from campus_inbox.legacy_records import LegacyRecord
from campus_inbox.models import (
    Attachment,
    Message,
    SplitDraft,
    Suggestion,
    Task,
    TaskEvent,
    TaskMaterials,
    now,
)

MAX_BYTES = 10 * 1024 * 1024
MAX_RECORDS = 50000


def known_time(message):
    external = message.get("external")
    external = external if isinstance(external, dict) else {}
    if external.get("time_known") is False:
        return False
    value = (
        external.get("published_at")
        if external.get("kind") == "task_center"
        else message.get("captured_at")
    )
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.tzinfo is not None and stamp.utcoffset() is not None
    except (ValueError, OverflowError):
        return False


def inspect_export(data):
    if not isinstance(data, dict) or data.get("version") != "0.1.0":
        raise ValueError("版本无法识别")
    collections = [data.get(key) for key in ("messages", "proposals", "tasks")]
    if any(not isinstance(items, list) or len(items) > MAX_RECORDS for items in collections):
        raise ValueError("记录格式无效")
    if any(not isinstance(item, dict) for items in collections for item in items):
        raise ValueError("记录格式无效")
    messages, proposals, tasks = collections
    for task in tasks:
        if any(
            not isinstance(task.get(key, []), list) for key in ("sources", "history", "checklist")
        ):
            raise ValueError("任务详情格式无效")
    message_ids = Counter(
        item.get("id") for item in messages if isinstance(item.get("id"), str) and item["id"]
    )
    task_ids = Counter(
        item.get("id") for item in tasks if isinstance(item.get("id"), str) and item["id"]
    )
    source_uses = Counter()
    missing_sources = 0
    multi_source = 0
    for task in tasks:
        ids = {
            source.get("id")
            for source in task.get("sources", [])
            if isinstance(source, dict) and isinstance(source.get("id"), str) and source["id"]
        }
        missing_sources += not ids or any(source_id not in message_ids for source_id in ids)
        multi_source += len(ids) > 1
        source_uses.update(ids)
    attachment_references = 0
    for message in messages:
        external = message.get("external")
        files = external.get("attachments") if isinstance(external, dict) else None
        if isinstance(files, list):
            attachment_references += len(files)
    return {
        "counts": {
            "messages": len(messages),
            "proposals": len(proposals),
            "tasks": len(tasks),
            "history": sum(len(task.get("history", [])) for task in tasks),
            "materials": sum(len(task.get("checklist", [])) for task in tasks),
        },
        "issues": {
            "unknown_message_time": sum(not known_time(message) for message in messages),
            "multi_source_tasks": multi_source,
            "messages_shared_by_tasks": sum(count > 1 for count in source_uses.values()),
            "missing_task_sources": missing_sources,
            "duplicate_message_ids": sum(count - 1 for count in message_ids.values()),
            "duplicate_task_ids": sum(count - 1 for count in task_ids.values()),
            "missing_message_ids": len(messages) - sum(message_ids.values()),
            "missing_task_ids": len(tasks) - sum(task_ids.values()),
            "attachment_references": attachment_references,
        },
    }


def identity(namespace, kind, ident):
    return str(uuid5(NAMESPACE_URL, f"campusinbox-legacy:{namespace}:{kind}:{ident}"))


def records_for(document, originals):
    result = {kind: dict(items) for kind, items in document.items()}
    result["originals"] = {
        f"{item['message_id']}:{item['index']}": {
            key: value for key, value in item.items() if key not in {"content", "path"}
        }
        for item in originals
    }
    # Source messages have their own identity; duplicated embedded snapshots are not authoritative.
    result["tasks"] = {
        ident: {**item, "sources": [{"id": source["id"]} for source in item["sources"]]}
        for ident, item in result["tasks"].items()
    }
    return result


def migration_plan(session, namespace, document, originals):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", namespace):
        raise ValueError("invalid namespace")
    summary, collections = validate(document, originals)
    records = records_for(collections, originals)
    new, skipped, conflicts = {}, {}, {}
    existing = {
        row.id: row
        for row in session.exec(select(LegacyRecord).where(LegacyRecord.namespace == namespace))
    }
    for kind, items in records.items():
        new[kind] = skipped[kind] = conflicts[kind] = 0
        for ident, item in items.items():
            prior = existing.get(identity(namespace, kind, ident))
            if prior is None:
                new[kind] += 1
            elif prior.content_hash == digest(item):
                skipped[kind] += 1
            else:
                conflicts[kind] += 1
    for ident, proposal in collections["proposals"].items():
        if (
            identity(namespace, "proposals", ident) not in existing
            and identity(namespace, "messages", proposal["message_id"]) in existing
        ):
            conflicts["proposals"] += 1
    # Changes in the main task set invalidate an old preview, including user edits.
    tasks = session.exec(select(Task).order_by(Task.id)).all()
    task_state = [
        [task.id, task.version, task.title, task.course, task.updated_at.isoformat()]
        for task in tasks
    ]
    names = {(task.title.strip().casefold(), task.course.strip().casefold()) for task in tasks}
    collisions = sum(
        (item["title"].strip().casefold(), item.get("course", "").strip().casefold()) in names
        and identity(namespace, "tasks", ident) not in existing
        for ident, item in collections["tasks"].items()
    )
    token = digest(
        {
            "namespace": namespace,
            "records": records,
            "task_state": task_state,
            "existing": sorted((key, row.content_hash) for key, row in existing.items()),
        }
    )
    return (
        {
            **summary,
            "namespace": namespace,
            "new": new,
            "skipped": skipped,
            "conflicts": conflicts,
            "possible_task_collisions": collisions,
            "originals": {
                "available": len(originals),
                "missing": summary["issues"]["attachment_references"] - len(originals),
            },
            "can_import": not any(conflicts.values()),
            "plan_token": token,
        },
        collections,
        records,
    )


def add_record(session, namespace, kind, ident, target, payload):
    row = LegacyRecord(
        id=identity(namespace, kind, ident),
        namespace=namespace,
        kind=kind,
        legacy_id=ident,
        content_hash=digest(payload),
        target_id=target,
        payload=payload,
    )
    session.add(row)


def write_records(session, namespace, collections, records, originals):
    def mapped(kind, ident):
        return identity(namespace, kind, ident)

    def fresh(kind, ident):
        return session.get(LegacyRecord, mapped(kind, ident)) is None

    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    by_message = defaultdict(list)
    for proposal in collections["proposals"].values():
        by_message[proposal["message_id"]].append(proposal)
    task_sources = {task["sources"][0]["id"] for task in collections["tasks"].values()}
    new_messages = set()
    for ident, item in collections["messages"].items():
        if not fresh("messages", ident):
            continue
        new_messages.add(ident)
        external = item.get("external") or {}
        sent = (
            external.get("published_at")
            if external.get("kind") == "task_center"
            else item.get("captured_at")
        )
        sent_at = stamp(sent) if known_time(item) else epoch
        message = Message(
            id=mapped("messages", ident),
            text=item["text"],
            source=item.get("source") or "旧版通知",
            sent_at=sent_at,
            dedupe_key="legacy:" + mapped("messages", ident),
            received_at=stamp(item.get("received_at")) or now(),
        )
        session.add(message)
        session.flush()
        proposals = by_message[ident]
        pending = [p for p in proposals if p["status"] == "pending"]
        accepted = any(p["status"] == "confirmed" for p in proposals)
        archived = any(p["status"] == "archived" for p in proposals)
        item_fields = (
            fields((pending[0] if pending else proposals[0]).get("candidate", {}))
            if proposals
            else {}
        )
        warnings = ["从旧版迁入，请核对原文；旧版识别结果不是新的平台核验。"]
        if sent_at == epoch:
            warnings.append("原消息发布时间未知；不能根据接收或迁移时间推算相对日期。")
        suggestion = Suggestion(
            id=mapped("suggestions", ident),
            message_id=message.id,
            status="pending"
            if pending
            else "accepted"
            if accepted
            else "archived"
            if archived
            else "ignored",
            action="create"
            if len(pending) == 1 and pending[0].get("action") == "create"
            else "review",
            method="legacy",
            fields=item_fields,
            warnings=warnings,
            evidence={
                key: {"method": "legacy", "quote": "旧版记录，需对照原文核对"}
                for key, value in item_fields.items()
                if value not in (None, "")
            },
        )
        session.add(suggestion)
        add_record(session, namespace, "messages", ident, message.id, records["messages"][ident])
        session.flush()
        if len(pending) > 1 or (
            pending and pending[0].get("action") == "create" and ident in task_sources
        ):
            for position, proposal in enumerate(pending, 1):
                session.add(
                    SplitDraft(
                        id=mapped("drafts", proposal["id"]),
                        suggestion_id=suggestion.id,
                        position=position,
                        quote=proposal["candidate"].get("evidence") or item["text"],
                        can_create=proposal.get("action") == "create",
                        fields=fields(proposal["candidate"]),
                        warnings=["旧版草稿，逐项核对；变更与取消条目不可作为新任务创建。"],
                    )
                )
    session.flush()
    for ident, item in collections["proposals"].items():
        if fresh("proposals", ident):
            # A newer export can append proposals to an already imported message.
            # Such changes are rejected below rather than silently losing review state.
            if item["message_id"] not in new_messages:
                raise HTTPException(
                    409, "已有通知新增了旧版草稿，请在旧系统核对后迁移到新的数据集。"
                )
            add_record(
                session,
                namespace,
                "proposals",
                ident,
                mapped("suggestions", item["message_id"]),
                item,
            )
    for ident, item in collections["tasks"].items():
        if not fresh("tasks", ident):
            continue
        sources = list(dict.fromkeys(source["id"] for source in item["sources"]))
        sid = sources[0]
        values = fields(item)
        values["due_at"] = stamp(values["due_at"]) if values["due_at"] else None
        values["due_date"] = date.fromisoformat(values["due_date"]) if values["due_date"] else None
        task = Task(
            **values,
            id=mapped("tasks", ident),
            suggestion_id=mapped("suggestions", sid),
            message_id=mapped("messages", sid),
            status=STATUSES[item["status"]],
            version=max(1, item.get("version", 1)),
            created_at=stamp(item.get("created_at")) or now(),
            updated_at=stamp(item.get("updated_at")) or now(),
        )
        session.add(task)
        session.flush()
        session.add(TaskMaterials(task_id=task.id, items=item.get("checklist", [])))
        # Keep historic before/after untouched in the audit payload. They are not replayable
        # main-app operations, whose semantics/version checks differ from the old program.
        session.add(
            TaskEvent(
                task_id=task.id,
                message_id=task.message_id,
                operation="legacy_import",
                actor="legacy_migration",
                after={"version": task.version},
            )
        )
        for source_id in sources[1:]:
            session.add(
                TaskEvent(
                    task_id=task.id,
                    message_id=mapped("messages", source_id),
                    operation="legacy_source",
                    actor="legacy_migration",
                )
            )
        add_record(session, namespace, "tasks", ident, task.id, records["tasks"][ident])
    session.flush()
    directory = Path(session.bind.url.database).parent / "uploads"
    directory.mkdir(exist_ok=True)
    for item in originals:
        ident = f"{item['message_id']}:{item['index']}"
        if not fresh("originals", ident):
            continue
        source = collections["messages"][item["message_id"]]["external"]["attachments"][
            item["index"]
        ]
        filename = source.get("name") or source.get("filename") or "attachment.bin"
        filename = (
            re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", filename).strip(". ")[:200] or "attachment.bin"
        )
        target = mapped("attachments", ident)
        suffix = Path(filename).suffix.lower()
        path = directory / f"{target}{suffix}"
        persist_original(path, item["content"])
        text = source.get("text") if isinstance(source.get("text"), str) else ""
        extraction_ready = bool(text) and source.get("status") in {"ready", "extracted"}
        session.add(
            Attachment(
                id=target,
                filename=filename,
                size=len(item["content"]),
                message_id=mapped("messages", item["message_id"]),
                status="ready" if extraction_ready else "failed",
                text=text,
                error=""
                if extraction_ready
                else "原件已迁入，旧版提取未完成或状态未知，请核对或重新解析。",
            )
        )
        add_record(session, namespace, "originals", ident, target, records["originals"][ident])


def backup_database(engine):
    directory = Path(engine.url.database).parent / "backups"
    directory.mkdir(exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="before-legacy-", suffix=".sqlite3", dir=directory)
    os.close(descriptor)
    os.chmod(name, 0o600)
    with sqlite3.connect(
        Path(engine.url.database).resolve().as_uri() + "?mode=ro", uri=True
    ) as source:
        with sqlite3.connect(name) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise OSError("backup integrity check failed")
    return Path(name).name


def router(engine=None):
    api = APIRouter(tags=["legacy migration"], dependencies=[Depends(loopback)])

    @api.post("/api/migrations/legacy/preview")
    async def preview(request: Request):
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > MAX_BYTES:
                raise HTTPException(413, "文件超过 10 MB，请使用较小的旧版 JSON 导出。")
            content.extend(chunk)
        try:
            result = inspect_export(json.loads(content.decode("utf-8-sig")))
        except (ValueError, UnicodeError, RecursionError, TypeError):
            raise HTTPException(422, "无法识别旧版 JSON 导出，请检查文件。") from None
        return result

    async def read_bundle(request):
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > MAX_BUNDLE:
                raise HTTPException(413, "迁移文件超过 100 MB，请拆分后重试。")
            content.extend(chunk)
        try:
            return load_bundle(bytes(content))
        except (
            ValueError,
            UnicodeError,
            TypeError,
            KeyError,
            RecursionError,
            zipfile.BadZipFile,
            RuntimeError,
            NotImplementedError,
            OverflowError,
        ):
            raise HTTPException(422, "迁移文件无效，请检查 JSON、原件清单及文件校验。") from None

    @api.post("/api/migrations/legacy/plan")
    async def plan(request: Request, namespace: str = "local-assistant"):
        document, originals = await read_bundle(request)
        try:
            with Session(engine) as session:
                session.connection().exec_driver_sql("BEGIN")
                result, _, _ = migration_plan(session, namespace, document, originals)
                return result
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            raise HTTPException(422, "旧版记录或关联无效，请先在旧版核对，不会写入数据。") from None

    @api.post("/api/migrations/legacy/import")
    async def import_legacy(request: Request, namespace: str = "local-assistant"):
        token = request.headers.get("x-migration-plan", "")
        if request.headers.get("x-migration-confirm") != "import" or not token:
            raise HTTPException(409, "请先检查文件，再明确确认迁移。")
        document, originals = await read_bundle(request)
        try:
            with Session(engine) as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                result, collections, records = migration_plan(
                    session, namespace, document, originals
                )
                if token != result["plan_token"] or not result["can_import"]:
                    raise HTTPException(
                        409, "数据或检查结果已变化，或存在冲突，请重新检查；未导入。"
                    )
                backup = None
                if any(result["new"].values()):
                    backup = backup_database(engine)
                    write_records(session, namespace, collections, records, originals)
                    session.commit()
                return {"imported": result["new"], "skipped": result["skipped"], "backup": backup}
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            raise HTTPException(422, "记录无法迁移，整批已回滚，请核对原导出。") from None
        except (OSError, SQLAlchemyError):
            raise HTTPException(
                503, "备份或写入失败，整批已回滚；请检查磁盘后重新检查文件。"
            ) from None

    return api
