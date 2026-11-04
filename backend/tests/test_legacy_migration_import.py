"""Migration is explicit, atomic and isolated from the user's old database."""

import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, select

from campus_inbox.main import create_app
from campus_inbox.models import Message, Task

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def fixture(tmp_path):
    app = create_app(tmp_path / "main.db", qq_background=False, chaoxing_background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            yield client, app, tmp_path


def document():
    return {
        "version": "0.1.0",
        "messages": [
            {
                "id": "m1",
                "text": "第一次作业请提交报告",
                "source": "学习通网页",
                "captured_at": "2026-11-01T12:00:00+08:00",
            },
            {"id": "m2", "text": "截止改为12月8日", "external": {"time_known": False}},
        ],
        "proposals": [
            {
                "id": "p1",
                "message_id": "m1",
                "status": "confirmed",
                "action": "create",
                "candidate": {"title": "第一次作业", "course": "软件工程", "kind": "assignment"},
            },
            {
                "id": "p2",
                "message_id": "m2",
                "status": "pending",
                "action": "update",
                "candidate": {"title": "第一次作业", "course": "软件工程", "kind": "assignment"},
            },
        ],
        "tasks": [
            {
                "id": "t1",
                "title": "第一次作业",
                "course": "软件工程",
                "kind": "assignment",
                "deadline": "2026-12-08",
                "date_precision": "day",
                "status": "done",
                "version": 3,
                "sources": [{"id": "m1"}, {"id": "m2"}],
                "history": [
                    {
                        "action": "create",
                        "message_id": "m1",
                        "at": "2026-11-01T12:01:00+08:00",
                        "after": {"title": "第一次作业", "deadline": "2026-12-07"},
                    },
                    {
                        "action": "deadline_update",
                        "message_id": "m2",
                        "at": "2026-11-02T12:00:00+08:00",
                        "before": {"deadline": "2026-12-07"},
                        "after": {"deadline": "2026-12-08"},
                    },
                ],
                "checklist": [{"text": "报告", "done": True}],
            }
        ],
    }


def encode(data):
    return json.dumps(data, ensure_ascii=False).encode()


async def plan(client, data):
    response = await client.post(
        "/api/migrations/legacy/plan?namespace=test-old", content=encode(data)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def apply(client, data, token):
    return await client.post(
        "/api/migrations/legacy/import?namespace=test-old",
        headers={"X-Migration-Plan": token, "X-Migration-Confirm": "import"},
        content=encode(data),
    )


async def test_migration_round_trip_from_legacy_export(fixture, monkeypatch):
    client, _, directory = fixture
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "local-assistant"))
    from courseinbox.core import Inbox
    from courseinbox.http import export_backup, seed_demo

    legacy = Inbox(directory / "legacy.sqlite3")
    seed_demo(legacy)
    data = export_backup(legacy)
    assert len(data["tasks"]) == 3
    preview = await plan(client, data)
    result = await apply(client, data, preview["plan_token"])
    assert result.status_code == 200, result.text
    tasks = (await client.get("/api/tasks")).json()
    assert sorted(task["title"] for task in tasks) == sorted(
        task["title"] for task in data["tasks"]
    )
    assert (await client.get("/api/inbox")).json()
    repeated = await plan(client, export_backup(legacy))
    assert repeated["new"]["tasks"] == 0
    assert repeated["skipped"]["tasks"] == 3


async def test_confirmed_import_preserves_sources_history_materials_and_date(fixture):
    client, app, directory = fixture
    data = document()
    preview = await plan(client, data)
    assert preview["can_import"] and preview["new"]["tasks"] == 1
    assert (await client.get("/api/tasks")).json() == []
    result = await apply(client, data, preview["plan_token"])
    assert result.status_code == 200, result.text
    assert result.json()["backup"]
    backups = list((directory / "backups").glob("before-legacy-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("SELECT count(*) FROM task").fetchone()[0] == 0
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    tasks = (await client.get("/api/tasks")).json()
    assert len(tasks) == 1 and tasks[0]["status"] == "done"
    assert tasks[0]["due_date"] == "2026-12-08" and tasks[0]["due_at"] is None
    detail = (await client.get("/api/tasks/" + tasks[0]["id"])).json()
    assert len(detail["sources"]) == 2 and detail["materials"] == [{"text": "报告", "done": True}]
    assert detail["legacy"]["record"]["history"] == data["tasks"][0]["history"]
    assert any(e["operation"] == "legacy_import" for e in detail["events"])
    inbox = (await client.get("/api/inbox")).json()
    assert len(inbox) == 1 and inbox[0]["action"] == "review"
    assert inbox[0]["message"]["source_context"]["time_known"] is False
    with Session(app.state.engine) as session:
        assert len(session.exec(select(Message)).all()) == 2


async def test_identical_reimport_is_idempotent_even_after_user_edits(fixture):
    client, app, _ = fixture
    data = document()
    first = await plan(client, data)
    assert (await apply(client, data, first["plan_token"])).status_code == 200
    with Session(app.state.engine) as session:
        task = session.exec(select(Task)).one()
        task.title = "我后来编辑的标题"
        session.add(task)
        session.commit()
    second = await plan(client, data)
    assert second["new"]["tasks"] == 0 and second["skipped"]["tasks"] == 1
    response = await apply(client, data, second["plan_token"])
    assert response.status_code == 200 and response.json()["imported"]["tasks"] == 0
    assert (await client.get("/api/tasks")).json()[0]["title"] == "我后来编辑的标题"


async def test_changed_identity_conflicts_without_overwriting(fixture):
    client, _, _ = fixture
    data = document()
    first = await plan(client, data)
    assert (await apply(client, data, first["plan_token"])).status_code == 200
    data["tasks"][0]["title"] = "旧系统中已改变"
    second = await plan(client, data)
    assert not second["can_import"] and second["conflicts"]["tasks"] == 1
    assert (await apply(client, data, second["plan_token"])).status_code == 409
    assert (await client.get("/api/tasks")).json()[0]["title"] == "第一次作业"


async def test_confirmation_and_matching_preview_are_required(fixture):
    client, _, _ = fixture
    data = document()
    response = await client.post(
        "/api/migrations/legacy/import?namespace=test-old", content=encode(data)
    )
    assert response.status_code == 409
    preview = await plan(client, data)
    data["tasks"][0]["title"] = "上传后变化"
    assert (await apply(client, data, preview["plan_token"])).status_code == 409
    assert (await client.get("/api/tasks")).json() == []


@pytest.mark.parametrize(
    "fault", ["duplicate", "missing_source", "unknown_status", "bad_deadline", "bad_material"]
)
async def test_invalid_graph_rejected_without_writes(fixture, fault):
    client, _, _ = fixture
    data = document()
    if fault == "duplicate":
        data["messages"].append(dict(data["messages"][0]))
    elif fault == "missing_source":
        data["tasks"][0]["sources"].append({"id": "missing"})
    elif fault == "unknown_status":
        data["tasks"][0]["status"] = "surprise"
    elif fault == "bad_deadline":
        data["tasks"][0]["deadline"] = "2026-12-08T12:00:00"
    else:
        data["tasks"][0]["checklist"] = [{"text": "", "done": "false"}]
    response = await client.post("/api/migrations/legacy/plan", content=encode(data))
    assert response.status_code == 422, response.text
    assert (await client.get("/api/tasks")).json() == []


async def test_unknown_publication_time_never_enables_relative_reanalysis(fixture):
    client, _, _ = fixture
    data = document()
    data["messages"][1]["text"] = "明天提交作业"
    preview = await plan(client, data)
    assert (await apply(client, data, preview["plan_token"])).status_code == 200
    item = (await client.get("/api/inbox")).json()[0]
    response = await client.post("/api/inbox/" + item["id"] + "/analyze")
    assert response.status_code == 409
    assert "时间" in response.text


async def test_failed_import_rolls_back_all_rows_and_can_retry(fixture, monkeypatch):
    from campus_inbox import legacy_migration

    client, app, _ = fixture
    data = document()
    preview = await plan(client, data)
    original = legacy_migration.write_records

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("synthetic secret details")

    monkeypatch.setattr(legacy_migration, "write_records", fail)
    response = await apply(client, data, preview["plan_token"])
    assert response.status_code == 503 and "secret" not in response.text
    with Session(app.state.engine) as session:
        assert session.exec(select(Message)).all() == []
    monkeypatch.setattr(legacy_migration, "write_records", original)
    assert (await apply(client, data, preview["plan_token"])).status_code == 200


def bundle(data, contents=b"original synthetic file", name="originals/a.bin"):
    data = json.loads(json.dumps(data))
    data["messages"][0]["external"] = {"attachments": [{"name": "要求.txt", "text": "报告"}]}
    manifest = [
        {
            "message_id": "m1",
            "index": 0,
            "path": name,
            "sha256": hashlib.sha256(contents).hexdigest(),
        }
    ]
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("export.json", encode(data))
        archive.writestr("originals.json", encode(manifest))
        archive.writestr(name, contents)
    return stream.getvalue()


async def test_bundle_import_copies_verified_original_and_downloads_it(fixture):
    client, _, _ = fixture
    body = bundle(document())
    response = await client.post("/api/migrations/legacy/plan?namespace=test-old", content=body)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["originals"]["available"] == 1
    response = await client.post(
        "/api/migrations/legacy/import?namespace=test-old",
        headers={"X-Migration-Plan": preview["plan_token"], "X-Migration-Confirm": "import"},
        content=body,
    )
    assert response.status_code == 200, response.text
    task = (await client.get("/api/tasks")).json()[0]
    detail = (await client.get("/api/tasks/" + task["id"])).json()
    attachment = detail["sources"][0]["attachments"][0]
    download = await client.get("/api/attachments/" + attachment["id"] + "/download")
    assert download.content == b"original synthetic file"


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/escape", "originals/../../escape"])
async def test_zip_path_traversal_is_rejected(fixture, name):
    client, _, _ = fixture
    response = await client.post(
        "/api/migrations/legacy/plan", content=bundle(document(), name=name)
    )
    assert response.status_code == 422


async def test_remote_migration_is_rejected(fixture):
    client, _, _ = fixture
    response = await client.post(
        "/api/migrations/legacy/plan",
        headers={"Origin": "https://evil.invalid"},
        content=encode(document()),
    )
    assert response.status_code == 403


async def test_preview_invalidates_when_main_tasks_change(fixture):
    client, _, _ = fixture
    data = document()
    preview = await plan(client, data)
    created = await client.post("/api/tasks", json={"title": "另一项手动任务"})
    assert created.status_code == 200, created.text
    response = await apply(client, data, preview["plan_token"])
    assert response.status_code == 409
    assert len((await client.get("/api/tasks")).json()) == 1


async def test_failed_backup_stops_before_any_writes(fixture, monkeypatch):
    from campus_inbox import legacy_migration

    client, _, _ = fixture
    data = document()
    preview = await plan(client, data)

    def fail(_engine):
        raise OSError("private disk detail")

    monkeypatch.setattr(legacy_migration, "backup_database", fail)
    response = await apply(client, data, preview["plan_token"])
    assert response.status_code == 503 and "private" not in response.text
    assert (await client.get("/api/inbox?status=all")).json() == []


async def test_multiple_create_drafts_share_source_without_creating_tasks(fixture):
    client, _, _ = fixture
    data = document()
    data["tasks"] = []
    data["proposals"] = [
        {
            "id": "p1",
            "message_id": "m1",
            "status": "pending",
            "action": "create",
            "candidate": {"title": "报告一", "kind": "assignment"},
        },
        {
            "id": "p2",
            "message_id": "m1",
            "status": "pending",
            "action": "create",
            "candidate": {"title": "报告二", "kind": "assignment"},
        },
    ]
    preview = await plan(client, data)
    assert (await apply(client, data, preview["plan_token"])).status_code == 200
    item = (await client.get("/api/inbox")).json()[0]
    assert len(item["split_items"]) == 2
    assert (await client.get("/api/tasks")).json() == []
    draft = item["split_items"][0]
    response = await client.post(
        "/api/split-drafts/" + draft["id"] + "/accept",
        json={"title": "报告一", "expected_updated_at": draft["updated_at"]},
    )
    assert response.status_code == 200, response.text


async def test_originals_can_be_added_after_json_without_duplicate_messages(fixture):
    client, _, _ = fixture
    data = document()
    data["messages"][0]["external"] = {"attachments": [{"name": "要求.txt", "text": "报告"}]}
    preview = await plan(client, data)
    assert preview["originals"]["missing"] == 1
    assert (await apply(client, data, preview["plan_token"])).status_code == 200
    body = bundle(data)
    preview_response = await client.post(
        "/api/migrations/legacy/plan?namespace=test-old", content=body
    )
    preview = preview_response.json()
    assert preview["new"]["messages"] == 0 and preview["new"]["originals"] == 1
    response = await client.post(
        "/api/migrations/legacy/import?namespace=test-old",
        headers={"X-Migration-Plan": preview["plan_token"], "X-Migration-Confirm": "import"},
        content=body,
    )
    assert response.status_code == 200, response.text
    assert len((await client.get("/api/tasks")).json()) == 1


async def test_credentials_are_removed_from_migrated_provenance(fixture):
    client, _, _ = fixture
    data = document()
    data["tasks"][0]["history"][0]["after"]["token"] = "test-private-token"
    data["messages"][0]["external"] = {
        "cookie": "test-private-cookie",
        "url": "https://example.com/?token=secret",
    }
    preview = await plan(client, data)
    assert (await apply(client, data, preview["plan_token"])).status_code == 200
    task = (await client.get("/api/tasks")).json()[0]
    response = await client.get("/api/tasks/" + task["id"])
    assert "test-private" not in response.text and "?token=secret" not in response.text


async def test_corrupt_hash_unlisted_files_and_symlinks_rejected(fixture):
    client, _, _ = fixture
    valid = bundle(document())
    for fault in ("hash", "unlisted", "symlink"):
        stream = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(valid)) as source, zipfile.ZipFile(stream, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                if fault == "hash" and name == "originals/a.bin":
                    data = b"tampered"
                info = zipfile.ZipInfo(name)
                if fault == "symlink" and name == "originals/a.bin":
                    info.create_system = 3
                    info.external_attr = 0o120777 << 16
                target.writestr(info, data)
            if fault == "unlisted":
                target.writestr("private-extra.txt", "do not accept")
        response = await client.post("/api/migrations/legacy/plan", content=stream.getvalue())
        assert response.status_code == 422


@pytest.mark.parametrize("kind", ["experiment", "project", "presentation"])
async def test_all_old_task_kinds_are_preserved_in_provenance(fixture, kind):
    client, _, _ = fixture
    data = document()
    data["tasks"][0]["kind"] = kind
    preview = await plan(client, data)
    assert (await apply(client, data, preview["plan_token"])).status_code == 200
    task = (await client.get("/api/tasks")).json()[0]
    detail = (await client.get("/api/tasks/" + task["id"])).json()
    assert detail["legacy"]["record"]["kind"] == kind


async def test_one_new_draft_on_source_of_old_task_does_not_return_old_task(fixture):
    client, _, _ = fixture
    data = document()
    data["proposals"][0]["status"] = "pending"
    data["proposals"][0]["candidate"]["title"] = "不同的新任务"
    preview = await plan(client, data)
    assert (await apply(client, data, preview["plan_token"])).status_code == 200
    item = next(
        i
        for i in (await client.get("/api/inbox")).json()
        if i["message"]["text"] == data["messages"][0]["text"]
    )
    assert len(item["split_items"]) == 1
    draft = item["split_items"][0]
    response = await client.post(
        "/api/split-drafts/" + draft["id"] + "/accept",
        json={"title": "不同的新任务", "expected_updated_at": draft["updated_at"]},
    )
    assert response.status_code == 200
    assert len((await client.get("/api/tasks")).json()) == 2


async def test_unknown_time_cannot_be_used_to_auto_parse_relative_deadline_change(fixture):
    client, _, _ = fixture
    data = document()
    data["messages"][1]["text"] = "第一次作业延期到明天18点截止"
    preview = await plan(client, data)
    assert (await apply(client, data, preview["plan_token"])).status_code == 200
    item = (await client.get("/api/inbox")).json()[0]
    task = (await client.get("/api/tasks")).json()[0]
    response = await client.post(
        "/api/suggestions/" + item["id"] + "/deadline-preview", json={"task_id": task["id"]}
    )
    assert response.status_code == 409
    response = await client.post(
        "/api/suggestions/" + item["id"] + "/deadline-preview",
        json={"task_id": task["id"], "due_date": "2026-12-09"},
    )
    assert response.status_code == 200, response.text


async def test_unknown_time_cannot_generate_new_numbered_drafts(fixture):
    client, _, _ = fixture
    data = document()
    data["messages"][1]["text"] = "1. 第一次作业明天提交报告\n2. 第二次作业后天提交报告"
    preview = await plan(client, data)
    assert (await apply(client, data, preview["plan_token"])).status_code == 200
    item = (await client.get("/api/inbox")).json()[0]
    response = await client.post("/api/suggestions/" + item["id"] + "/split")
    assert response.status_code == 409


async def test_repeated_zip_original_path_cannot_amplify_memory(fixture):
    client, _, _ = fixture
    stream = io.BytesIO()
    data = document()
    data["messages"][0]["external"] = {"attachments": [{"name": "a.txt"}, {"name": "b.txt"}]}
    content = b"bounded original"
    manifest = [
        {
            "message_id": "m1",
            "index": i,
            "path": "originals/a.bin",
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        for i in range(2)
    ]
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("export.json", encode(data))
        archive.writestr("originals.json", encode(manifest))
        archive.writestr("originals/a.bin", content)
    response = await client.post("/api/migrations/legacy/plan", content=stream.getvalue())
    assert response.status_code == 422


async def test_interrupted_original_write_does_not_publish_partial_file(fixture, monkeypatch):
    from campus_inbox import legacy_migration

    client, _, directory = fixture
    content = bundle(document())
    response = await client.post("/api/migrations/legacy/plan?namespace=test-old", content=content)
    token = response.json()["plan_token"]
    headers = {"X-Migration-Plan": token, "X-Migration-Confirm": "import"}
    original = legacy_migration.os.fsync

    def fail(_descriptor):
        raise OSError("simulate disk interruption")

    monkeypatch.setattr(legacy_migration.os, "fsync", fail)
    response = await client.post(
        "/api/migrations/legacy/import?namespace=test-old", headers=headers, content=content
    )
    assert response.status_code == 503
    assert list((directory / "uploads").glob("*.txt")) == []
    assert (await client.get("/api/tasks")).json() == []
    monkeypatch.setattr(legacy_migration.os, "fsync", original)
    response = await client.post(
        "/api/migrations/legacy/import?namespace=test-old", headers=headers, content=content
    )
    assert response.status_code == 200, response.text


async def test_partial_legacy_extraction_does_not_become_ready_evidence(fixture):
    client, app, _ = fixture
    data = document()
    stream = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(bundle(data))) as source,
        zipfile.ZipFile(stream, "w") as target,
    ):
        for name in source.namelist():
            content = source.read(name)
            if name == "export.json":
                record = json.loads(content)
                record["messages"][0]["external"]["attachments"][0]["status"] = "partial"
                content = encode(record)
            target.writestr(name, content)
    content = stream.getvalue()
    response = await client.post("/api/migrations/legacy/plan?namespace=test-old", content=content)
    token = response.json()["plan_token"]
    response = await client.post(
        "/api/migrations/legacy/import?namespace=test-old",
        headers={"X-Migration-Plan": token, "X-Migration-Confirm": "import"},
        content=content,
    )
    assert response.status_code == 200
    from campus_inbox.models import Attachment

    with Session(app.state.engine) as session:
        attachment = session.exec(select(Attachment)).one()
        assert attachment.status == "failed" and attachment.error
        assert attachment.text == "报告"
