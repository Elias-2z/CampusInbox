"""Bounded, inert input for explicitly selected legacy migration bundles."""

import hashlib
import io
import json
import re
import stat
import zipfile
from datetime import UTC, date, datetime
from pathlib import PurePosixPath

from campus_inbox.schemas import MaterialItem, TaskFields

MAX_JSON = 10 * 1024 * 1024
MAX_BUNDLE = 100 * 1024 * 1024
MAX_ORIGINAL = 32 * 1024 * 1024
STATUSES = {
    "pending": "todo",
    "in_progress": "in_progress",
    "done": "done",
    "submitted": "submitted",
    "cancelled": "cancelled",
    "archived": "archived",
}
KINDS = {
    "experiment": "homework",
    "project": "homework",
    "presentation": "homework",
    "assignment": "homework",
    "homework": "homework",
    "exam": "exam",
    "event": "event",
    "checkin": "event",
    "other": "other",
}
# Never carry connector credentials or credential-bearing URLs into migration storage.
PRIVATE = {
    "token",
    "access_token",
    "secret",
    "cookie",
    "cookies",
    "password",
    "authorization",
    "session",
    "session_data",
    "url",
    "source_url",
    "detail_url",
}


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items() if key.lower() not in PRIVATE}
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    raise ValueError("invalid JSON value")


def stamp(value):
    if not isinstance(value, str) or "T" not in value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone missing")
    return parsed.astimezone(UTC)


def fields(record):
    deadline = record.get("deadline")
    due_at = due_date = None
    if deadline:
        if not isinstance(deadline, str):
            raise ValueError("invalid deadline")
        if len(deadline) == 10:
            due_date = date.fromisoformat(deadline)
        else:
            due_at = stamp(deadline)
            if due_at is None:
                raise ValueError("invalid deadline")
    kind = record.get("kind") or "other"
    if kind not in KINDS:
        raise ValueError("unknown task kind")
    requirements = record.get("requirements", [])
    if not isinstance(requirements, list) or any(
        not isinstance(item, str) for item in requirements
    ):
        raise ValueError("invalid requirements")
    return TaskFields(
        title=record.get("title") or "旧版待核对任务",
        course=record.get("course", ""),
        kind=KINDS[kind],
        description=record.get("description", "") or "\n".join(requirements),
        due_at=due_at,
        due_date=due_date,
        submission_method=record.get("submission_method") or "",
        location=record.get("location") or "",
        remarks=record.get("remarks") or "",
    ).model_dump(mode="json")


def safe_entry(name):
    path = PurePosixPath(name)
    return (
        bool(name)
        and not path.is_absolute()
        and "\\" not in name
        and ":" not in name
        and all(part not in {"", ".", ".."} for part in name.split("/"))
        and "\x00" not in name
        and len(name) <= 200
    )


def load_bundle(content):
    originals = []
    if content.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if (
                len(entries) > 1002
                or len(set(names)) != len(names)
                or sum(entry.file_size for entry in entries) > MAX_BUNDLE
                or any(
                    not safe_entry(entry.filename)
                    or entry.flag_bits & 1
                    or stat.S_ISLNK(entry.external_attr >> 16)
                    for entry in entries
                )
                or "export.json" not in names
                or "originals.json" not in names
            ):
                raise ValueError("invalid bundle")
            sizes = {entry.filename: entry.file_size for entry in entries}
            if sizes["export.json"] > MAX_JSON or sizes["originals.json"] > MAX_JSON:
                raise ValueError("large manifest")
            raw = archive.read("export.json")
            manifest = json.loads(archive.read("originals.json").decode("utf-8-sig"))
            if not isinstance(manifest, list) or len(manifest) > 1000:
                raise ValueError("invalid originals")
            seen = set()
            seen_paths = set()
            for item in manifest:
                if not isinstance(item, dict) or set(item) != {
                    "message_id",
                    "index",
                    "path",
                    "sha256",
                }:
                    raise ValueError("invalid original")
                mid, index, name = item["message_id"], item["index"], item["path"]
                if (
                    not isinstance(mid, str)
                    or type(index) is not int
                    or index < 0
                    or not isinstance(name, str)
                    or not name.startswith("originals/")
                    or not safe_entry(name)
                    or name not in sizes
                    or not 0 < sizes[name] <= MAX_ORIGINAL
                    or (mid, index) in seen
                    or name in seen_paths
                    or not re.fullmatch("[a-f0-9]{64}", str(item["sha256"]))
                ):
                    raise ValueError("invalid original identity")
                data = archive.read(name)
                if hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise ValueError("bad hash")
                seen.add((mid, index))
                seen_paths.add(name)
                originals.append({**item, "content": data})
            if set(names) != {
                "export.json",
                "originals.json",
                *(item["path"] for item in manifest),
            }:
                raise ValueError("unlisted file")
    else:
        if len(content) > MAX_JSON:
            raise ValueError("large JSON")
        raw = content
    document = json.loads(
        raw.decode("utf-8-sig"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
    )
    return clean(document), originals


def validate(document, originals):
    from campus_inbox.legacy_migration import inspect_export

    summary = inspect_export(document)
    issues = summary["issues"]
    if any(
        issues[key]
        for key in (
            "duplicate_message_ids",
            "duplicate_task_ids",
            "missing_message_ids",
            "missing_task_ids",
            "missing_task_sources",
        )
    ):
        raise ValueError("invalid identities or references")
    collections = {
        name: {item["id"]: item for item in document[name]} for name in ("messages", "tasks")
    }
    proposals = {}
    for proposal in document["proposals"]:
        ident = proposal.get("id")
        if not isinstance(ident, str) or not ident or ident in proposals:
            raise ValueError("invalid proposal identity")
        if proposal.get("message_id") not in collections["messages"]:
            raise ValueError("missing proposal source")
        if proposal.get("target_id") and proposal["target_id"] not in collections["tasks"]:
            raise ValueError("missing proposal target")
        if proposal.get("status") not in {
            "pending",
            "confirmed",
            "dismissed",
            "archived",
            "superseded",
        }:
            raise ValueError("unknown proposal status")
        if not isinstance(proposal.get("candidate"), dict):
            raise ValueError("missing candidate")
        fields(proposal["candidate"])
        proposals[ident] = proposal
    collections["proposals"] = proposals
    for items in collections.values():
        for ident in items:
            if len(ident) > 200:
                raise ValueError("identity too long")
    for message in collections["messages"].values():
        if (
            not isinstance(message.get("text"), str)
            or not message["text"]
            or len(message["text"]) > 200000
        ):
            raise ValueError("invalid source text")
        if not isinstance(message.get("source", ""), str) or len(message.get("source", "")) > 100:
            raise ValueError("invalid source label")
        external = message.get("external") or {}
        if not isinstance(external, dict):
            raise ValueError("invalid source metadata")
        attachments = external.get("attachments") or []
        if not isinstance(attachments, list) or len(attachments) > 1000:
            raise ValueError("invalid attachment references")
        for attachment in attachments:
            if not isinstance(attachment, dict) or not isinstance(attachment.get("name", ""), str):
                raise ValueError("invalid attachment reference")
    for task in collections["tasks"].values():
        if type(task.get("version", 1)) is not int or not 1 <= task.get("version", 1) < 2**31:
            raise ValueError("invalid task version")
        if task.get("status") not in STATUSES or not task.get("title"):
            raise ValueError("unknown task status")
        fields(task)
        if len(task.get("checklist", [])) > 100:
            raise ValueError("too many materials")
        for item in task.get("checklist", []):
            if not isinstance(item, dict) or type(item.get("done", False)) is not bool:
                raise ValueError("invalid material")
            MaterialItem(**item)
        for source in task["sources"]:
            if not isinstance(source, dict) or source.get("id") not in collections["messages"]:
                raise ValueError("invalid source")
        for event in task.get("history", []):
            if not isinstance(event, dict):
                raise ValueError("invalid history")
            if event.get("message_id") and event["message_id"] not in collections["messages"]:
                raise ValueError("missing history source")
    for item in originals:
        message = collections["messages"].get(item["message_id"])
        files = (message.get("external") or {}).get("attachments", []) if message else []
        if item["index"] >= len(files):
            raise ValueError("original not referenced")
    return summary, collections
