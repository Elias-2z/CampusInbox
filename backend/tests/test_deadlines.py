import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from sqlmodel import Session, SQLModel

from campus_inbox.database import make_engine
from campus_inbox.main import create_app
from campus_inbox.models import Message, ModelSettings, Suggestion, Task
from campus_inbox.schemas import TaskFields

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(tmp_path):
    app = create_app(tmp_path / "deadlines.db")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


async def task(client, **changes):
    response = await client.post(
        "/api/tasks",
        json={
            "title": "实验四报告",
            "course": "数据库原理",
            "kind": "homework",
            "due_at": "2026-10-30T23:59:00+08:00",
            "description": "完成SQL实验",
            "submission_platform": "学习通",
            "file_requirements": "学号+姓名",
            **changes,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def notice(client, text="数据库原理：实验四报告延期到下周一晚上八点，其他要求不变。"):
    response = await client.post(
        "/api/inbox",
        json={
            "text": text,
            "source": "老师群通知",
            "sent_at": "2026-10-29T14:00:00+08:00",
            "timezone": "Asia/Shanghai",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def preview(client, item, original, **changes):
    response = await client.post(
        f"/api/suggestions/{item['id']}/deadline-preview",
        json={
            "task_id": original["id"],
            **changes,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def confirm(client, proposal):
    response = await client.post(f"/api/deadline-proposals/{proposal['id']}/confirm")
    assert response.status_code == 200, response.text
    return response.json()


def edit_data(original, **changes):
    return {
        **{k: v for k, v in original.items() if k in TaskFields.model_fields},
        "expected_version": original["version"],
        "status": original["status"],
        **changes,
    }


async def test_preview_then_confirm_updates_only_deadline_and_links_both_sources(client):
    original = await task(client)
    item = await notice(client)
    assert item["deadline_supported"]
    assert item["fields"]["title"] == "实验四报告"
    assert item["matched_tasks"][0]["id"] == original["id"]
    proposal = await preview(client, item, original)
    assert proposal["task"]["due_at"] == original["due_at"]
    assert proposal["proposed_due_at"] == "2026-11-02T12:00:00Z"
    assert (await client.get(f"/api/tasks/{original['id']}")).json()["task"]["version"] == 1
    result = await confirm(client, proposal)
    changed = result["task"]
    assert changed["due_at"] == proposal["proposed_due_at"]
    assert changed["version"] == 2
    for field in set(original) - {"due_at", "version", "updated_at"}:
        assert changed[field] == original[field], field
    assert len((await client.get("/api/tasks")).json()) == 1
    detail = (await client.get(f"/api/tasks/{original['id']}")).json()
    assert len(detail["sources"]) == 2
    assert detail["sources"][1]["id"] == item["message"]["id"]
    assert detail["events"][-1]["operation"] == "deadline_update"
    assert detail["events"][-1]["message_id"] == item["message"]["id"]
    assert detail["evidence"]["due_at"]["message_id"] == item["message"]["id"]
    assert detail["deadline_changes"][0]["can_undo"]
    settled = next(
        i for i in (await client.get("/api/inbox?status=all")).json() if i["id"] == item["id"]
    )
    assert settled["status"] == "accepted"
    assert settled["task_id"] == original["id"]


async def test_parallel_confirm_and_undo_are_idempotent_and_do_not_reapply(client):
    original = await task(client)
    item = await notice(client)
    proposal = await preview(client, item, original)
    results = await asyncio.gather(
        *[client.post(f"/api/deadline-proposals/{proposal['id']}/confirm") for _ in range(2)]
    )
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json()["event_id"] == results[1].json()["event_id"]
    undos = await asyncio.gather(
        *[client.post(f"/api/deadline-changes/{item['id']}/undo") for _ in range(2)]
    )
    assert [r.status_code for r in undos] == [200, 200]
    assert undos[0].json()["task"]["due_at"] == original["due_at"]
    assert undos[0].json()["task"]["version"] == 3
    replay = await confirm(client, proposal)
    assert replay["reverted"] and replay["task"]["version"] == 3
    detail = (await client.get(f"/api/tasks/{original['id']}")).json()
    assert len(detail["sources"]) == 2
    assert [e["operation"] for e in detail["events"]] == [
        "create",
        "deadline_update",
        "deadline_undo",
    ]
    assert detail["evidence"]["due_at"]["method"] == "user"


async def test_stale_task_preview_cannot_overwrite_new_edit(client):
    original = await task(client)
    item = await notice(client)
    proposal = await preview(client, item, original)
    edited = await client.patch(
        f"/api/tasks/{original['id']}", json=edit_data(original, title="用户已修改")
    )
    assert edited.status_code == 200
    rejected = await client.post(f"/api/deadline-proposals/{proposal['id']}/confirm")
    assert rejected.status_code == 409
    after = (await client.get(f"/api/tasks/{original['id']}")).json()["task"]
    assert after["title"] == "用户已修改" and after["due_at"] == original["due_at"]
    fresh = await preview(client, item, after)
    assert (await confirm(client, fresh))["task"]["title"] == "用户已修改"


@pytest.mark.parametrize("operation", ["analyze", "ignore-and-reopen"])
async def test_changed_suggestion_invalidates_old_preview(client, operation):
    original = await task(client)
    item = await notice(client)
    proposal = await preview(client, item, original)
    if operation == "analyze":
        response = await client.post(f"/api/inbox/{item['id']}/analyze")
        assert response.status_code == 200
    else:
        await client.post(f"/api/suggestions/{item['id']}/ignore")
        await client.post(f"/api/suggestions/{item['id']}/reopen")
    assert (
        await client.post(f"/api/deadline-proposals/{proposal['id']}/confirm")
    ).status_code == 409


async def test_second_preview_cannot_apply_same_notice_to_another_task(client):
    first, second = await task(client), await task(client, title="实验四附加题")
    item = await notice(client)
    a, b = await preview(client, item, first), await preview(client, item, second)
    await confirm(client, a)
    assert (await client.post(f"/api/deadline-proposals/{b['id']}/confirm")).status_code == 409
    assert (await client.get(f"/api/tasks/{second['id']}")).json()["task"]["version"] == 1


async def test_undo_cannot_clobber_later_edits(client):
    original = await task(client)
    item = await notice(client)
    applied = await confirm(client, await preview(client, item, original))
    changed = applied["task"]
    edit = await client.patch(
        f"/api/tasks/{changed['id']}", json=edit_data(changed, status="in_progress")
    )
    assert edit.status_code == 200
    response = await client.post(f"/api/deadline-changes/{item['id']}/undo")
    assert response.status_code == 409
    detail = (await client.get(f"/api/tasks/{original['id']}")).json()
    assert not detail["deadline_changes"][0]["can_undo"]
    assert detail["task"]["due_at"] == changed["due_at"]


@pytest.mark.parametrize(
    "text",
    [
        "数据库原理：实验四报告延期取消，不用再提交。",
        "数据库原理：实验四报告没有延期，仍然周五23:59前提交。",
        "1. 实验四报告延期到下周一20:00。\n2. 实验五报告延期到下周二20:00。",
        "软件工程：明天18:00前提交需求分析报告。",
        "数据库原理：实验四报告文件名改为学号+姓名，截止时间不变。",
        "数据库原理：实验四报告能否延期到下周一晚上八点？",
        "数据库原理：请提前检查实验四报告，周五23:59前提交。",
    ],
)
async def test_unsupported_notification_cannot_be_forced_into_deadline_change(client, text):
    original = await task(client)
    item = await notice(client, text)
    assert not item["deadline_supported"]
    response = await client.post(
        f"/api/suggestions/{item['id']}/deadline-preview",
        json={
            "task_id": original["id"],
            "due_at": "2026-11-02T20:00:00+08:00",
        },
    )
    assert response.status_code in {409, 422}


async def test_ambiguous_date_requires_manual_entry_and_missing_old_deadline_can_be_restored(
    client,
):
    original = await task(client, due_at=None)
    item = await notice(client, "数据库原理：实验四报告延期，下周一或下周二截止。")
    url = f"/api/suggestions/{item['id']}/deadline-preview"
    assert (await client.post(url, json={"task_id": original["id"]})).status_code == 422
    proposal = await preview(client, item, original, due_at="2026-11-02T20:00:00+08:00")
    assert proposal["evidence"]["method"] == "user"
    await confirm(client, proposal)
    undone = await client.post(f"/api/deadline-changes/{item['id']}/undo")
    assert undone.json()["task"]["due_at"] is None


@pytest.mark.parametrize("status", ["completed", "cancelled", "archived"])
async def test_inactive_task_is_not_a_deadline_target(client, status):
    original = await task(client)
    await client.patch(f"/api/tasks/{original['id']}", json=edit_data(original, status=status))
    item = await notice(client)
    response = await client.post(
        f"/api/suggestions/{item['id']}/deadline-preview", json={"task_id": original["id"]}
    )
    assert response.status_code == 409


async def test_ambiguous_candidates_need_explicit_task_id_and_numbers_are_respected(client):
    first = await task(client)
    second = await task(client, course="软件工程")
    item = await notice(client, "实验4报告延期到下周一晚上八点。")
    # Missing course information must not recommend tasks across courses by title alone.
    assert item["matched_tasks"] == []
    assert (
        await client.post(f"/api/suggestions/{item['id']}/deadline-preview", json={})
    ).status_code == 422
    await confirm(client, await preview(client, item, second))
    assert (await client.get(f"/api/tasks/{first['id']}")).json()["task"]["version"] == 1
    unrelated = await notice(client, "数据库原理：实验五报告延期到下周一晚上八点。")
    assert unrelated["matched_tasks"] == []


async def test_old_and_new_timestamps_prefer_explicit_revised_time(client):
    original = await task(client)
    item = await notice(client, "数据库原理：实验四报告截止时间由10月30日23:59调整为11月2日20:00。")
    assert item["deadline_supported"]
    assert item["fields"]["due_at"] == "2026-11-02T12:00:00+00:00"
    proposal = await preview(client, item, original)
    assert proposal["proposed_due_at"] == "2026-11-02T12:00:00Z"
    same = await client.post(
        f"/api/suggestions/{item['id']}/deadline-preview",
        json={"task_id": original["id"], "due_at": original["due_at"]},
    )
    assert same.status_code == 422


async def test_confirm_and_undo_survive_server_restart(client, tmp_path):
    original = await task(client)
    item = await notice(client)
    await confirm(client, await preview(client, item, original))
    app = create_app(tmp_path / "deadlines.db")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as fresh:
            detail = (await fresh.get(f"/api/tasks/{original['id']}")).json()
            assert detail["deadline_changes"][0]["can_undo"]
            undone = await fresh.post(f"/api/deadline-changes/{item['id']}/undo")
            assert undone.status_code == 200
            assert undone.json()["task"]["due_at"] == original["due_at"]


async def test_additive_schema_initialization_preserves_old_data_and_model_settings(tmp_path):
    path = tmp_path / "old.db"
    engine = make_engine(path)
    tables = [
        table
        for table in SQLModel.metadata.sorted_tables
        if table.name
        not in {"deadlineproposal", "deadlinechange", "cancellationproposal", "cancellationchange"}
    ]
    SQLModel.metadata.create_all(engine, tables=tables)
    with Session(engine) as session:
        message = Message(
            text="旧通知原文", sent_at=datetime(2026, 10, 29, tzinfo=UTC), dedupe_key="old"
        )
        session.add(message)
        session.flush()
        suggestion = Suggestion(message_id=message.id, status="accepted")
        session.add(suggestion)
        session.flush()
        original = Task(
            title="已有真实任务",
            course="原有课程",
            suggestion_id=suggestion.id,
            message_id=message.id,
        )
        session.add(original)
        session.add(ModelSettings(enabled=True, model="user-local:4b"))
        session.commit()
        task_id = original.id
    engine.dispose()
    app = create_app(path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            result = await c.get(f"/api/tasks/{task_id}")
            assert result.json()["task"]["title"] == "已有真实任务"
            assert result.json()["source"]["text"] == "旧通知原文"
            assert (await c.get("/api/settings/model")).json()["model"] == "user-local:4b"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("本周三第一次实验课，不要迟到，记得穿工作服！", None),
        ("下周三第一次实验课，记得穿工作服。", None),
        ("明天上课，记得带实验服。", None),
        ("11月6日考试，请带学生证。", "2026-11-06"),
        ("不是本周三上课，请等通知。", None),
        ("本周三天气不错，实验报告另行通知。", None),
    ],
)
def test_course_schedule_dates_use_message_time_without_inventing_clock(text, expected):
    from campus_inbox.extraction import extract_rules

    result = extract_rules(text, datetime(2026, 11, 5, 7, 8, tzinfo=UTC), "Asia/Shanghai")
    assert result.fields["due_date"] == expected
    assert result.fields["due_at"] is None


@pytest.mark.parametrize(
    "text",
    ["本周三18:00第一次实验课，不要迟到。", "2026年11月4日18:00实验课，不要迟到。"],
)
def test_experimental_class_schedule_clock_is_not_a_deadline(text):
    from campus_inbox.extraction import extract_rules, lab_schedule_quotes, merge_model

    sent_at = datetime(2026, 11, 5, 7, 8, tzinfo=UTC)
    result = extract_rules(text, sent_at, "Asia/Shanghai")
    assert result.fields["due_at"] is None
    assert result.fields["due_date"] is None
    assert lab_schedule_quotes(text) == [text.split("，")[0]]
    merge_model(result, {"due_text": text.split("，")[0]}, text, sent_at, "Asia/Shanghai")
    assert result.fields["due_at"] is None
    assert result.fields["due_date"] is None


async def test_old_date_and_new_clock_notice_previews_without_manual_time_or_early_update(client):
    original = await task(client, title="作业", course="软件工程")
    item = await notice(
        client,
        "【软件工程作业延期通知】原定于今天提交的软件工程作业，"
        "现统一延期至后天23:59前提交。作业内容与提交方式保持不变。",
    )
    assert item["fields"]["due_at"] == "2026-10-31T15:59:00+00:00"
    proposal = await preview(client, item, original)
    assert proposal["proposed_due_at"] == "2026-10-31T15:59:00Z"
    before = (await client.get(f"/api/tasks/{original['id']}")).json()["task"]
    assert before["due_at"] == original["due_at"]
    await confirm(client, proposal)
    after = (await client.get(f"/api/tasks/{original['id']}")).json()["task"]
    assert after["due_at"] == "2026-10-31T15:59:00Z"
    assert len((await client.get("/api/tasks")).json()) == 1
