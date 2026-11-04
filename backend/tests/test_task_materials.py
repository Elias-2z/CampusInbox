"""A task's material checklist is versioned and survives a restart."""

from datetime import UTC, datetime

import httpx
import pytest
from sqlmodel import Session

from campus_inbox.data_export import snapshot
from campus_inbox.main import create_app
from campus_inbox.models import Attachment, Message, SplitDraft, Suggestion, Task

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_checklist_survives_restart_and_rejects_stale_edits(tmp_path):
    path = tmp_path / "materials.db"
    app = create_app(path, qq_background=False, chaoxing_background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            task = (await client.post("/api/tasks", json={"title": "实验"})).json()
            result = await client.put(
                f"/api/tasks/{task['id']}/materials",
                json={
                    "expected_version": task["version"],
                    "items": [
                        {"text": "实验报告", "done": True},
                        {"text": "源代码", "done": False},
                    ],
                },
            )
            assert result.status_code == 200, result.text
            assert result.json()["version"] == task["version"] + 1
            repeated = await client.put(
                f"/api/tasks/{task['id']}/materials",
                json={
                    "expected_version": result.json()["version"],
                    "items": [
                        {"text": "实验报告", "done": True},
                        {"text": "源代码", "done": False},
                    ],
                },
            )
            assert repeated.json()["version"] == result.json()["version"]
            invalid = await client.put(
                f"/api/tasks/{task['id']}/materials",
                json={"expected_version": result.json()["version"], "items": [{"text": "  "}]},
            )
            assert invalid.status_code == 422
            assert (
                snapshot(app.state.engine)["records"]["task_materials"][0]["items"][0]["text"]
                == "实验报告"
            )
            stale = await client.put(
                f"/api/tasks/{task['id']}/materials",
                json={"expected_version": task["version"], "items": []},
            )
            assert stale.status_code == 409
    app = create_app(path, qq_background=False, chaoxing_background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            detail = (await client.get(f"/api/tasks/{task['id']}")).json()
            assert detail["materials"] == [
                {"text": "实验报告", "done": True},
                {"text": "源代码", "done": False},
            ]
            assert any(event["operation"] == "materials" for event in detail["events"])


async def test_material_candidates_use_only_split_task_text_and_do_not_modify_data(tmp_path):
    app = create_app(tmp_path / "materials.db", qq_background=False, chaoxing_background=False)
    async with app.router.lifespan_context(app):
        with Session(app.state.engine) as session:
            message = Message(
                text="1. 提交实验报告和源代码\n2. 提交演示视频",
                dedupe_key="one",
                sent_at=datetime(2026, 11, 3, tzinfo=UTC),
            )
            session.add(message)
            session.flush()
            suggestion = Suggestion(message_id=message.id, status="accepted")
            session.add(suggestion)
            session.flush()
            task = Task(title="实验报告", suggestion_id=suggestion.id, message_id=message.id)
            session.add(task)
            session.flush()
            session.add(
                SplitDraft(
                    suggestion_id=suggestion.id,
                    position=1,
                    quote="请提交实验报告和源代码",
                    status="accepted",
                    task_id=task.id,
                )
            )
            session.add(
                Attachment(
                    filename="其他任务.txt",
                    size=1,
                    status="ready",
                    text="请提交演示视频",
                    message_id=message.id,
                )
            )
            session.commit()
            task_id = task.id
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/tasks/{task_id}/material-suggestions")
            assert response.status_code == 200
            candidates = response.json()["items"]
            assert [item["text"] for item in candidates] == ["实验报告", "源代码"]
            assert all("提交实验报告和源代码" in item["quote"] for item in candidates)
            detail = (await client.get(f"/api/tasks/{task_id}")).json()
            assert detail["materials"] == []
            assert detail["task"]["version"] == 1


async def test_material_candidates_include_ready_attachments_with_source_for_review(tmp_path):
    app = create_app(tmp_path / "materials.db", qq_background=False, chaoxing_background=False)
    async with app.router.lifespan_context(app):
        with Session(app.state.engine) as session:
            message = Message(
                text="请提交报告", dedupe_key="one", sent_at=datetime(2026, 11, 3, tzinfo=UTC)
            )
            other = Message(
                text="其他消息", dedupe_key="two", sent_at=datetime(2026, 11, 3, tzinfo=UTC)
            )
            session.add_all([message, other])
            session.flush()
            suggestion = Suggestion(message_id=message.id, status="accepted")
            session.add(suggestion)
            session.flush()
            task = Task(title="实验", suggestion_id=suggestion.id, message_id=message.id)
            session.add(task)
            session.add_all(
                [
                    Attachment(
                        filename="要求.docx",
                        size=1,
                        status="ready",
                        text="请上传源代码。",
                        message_id=message.id,
                    ),
                    Attachment(
                        filename="未解析.docx",
                        size=1,
                        status="failed",
                        text="请上传错误文件。",
                        message_id=message.id,
                    ),
                    Attachment(
                        filename="无关.docx",
                        size=1,
                        status="ready",
                        text="请上传无关文件。",
                        message_id=other.id,
                    ),
                    Attachment(
                        filename="部分识别.pdf",
                        size=1,
                        status="ready",
                        error="PDF 仅识别了部分页面。",
                        text="请上传不完整材料。",
                        message_id=message.id,
                    ),
                ]
            )
            session.commit()
            task_id = task.id
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/tasks/{task_id}/material-suggestions")
            assert response.status_code == 200
            items = response.json()["items"]
            assert [(item["text"], item["source"]) for item in items] == [
                ("报告", "通知正文"),
                ("源代码", "附件：要求.docx"),
            ]
            assert items[1]["quote"] == "请上传源代码"
            detail = (await client.get(f"/api/tasks/{task_id}")).json()
            assert detail["materials"] == []
            assert detail["task"]["version"] == 1


async def test_material_candidates_do_not_treat_negated_submission_as_required(tmp_path):
    app = create_app(tmp_path / "materials.db", qq_background=False, chaoxing_background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            task = (
                await client.post(
                    "/api/tasks",
                    json={"title": "作业", "description": "无需提交纸质版。请上传电子报告。"},
                )
            ).json()
            result = await client.get(f"/api/tasks/{task['id']}/material-suggestions")
            assert [item["text"] for item in result.json()["items"]] == ["电子报告"]


@pytest.mark.parametrize(
    "description",
    [
        "无需在学习通提交纸质版。请提交实验报告，请上传源代码。",
        "请提交以下材料：\n1. 实验报告\n2. 源代码\n3. 可选提交纸质版\n截止：明天\n1. 无关内容",
        "请提交实验报告、源代码、演示视频（选交）、纸质版（无需提交）。",
    ],
)
async def test_material_candidates_handle_negation_and_multiple_actions(tmp_path, description):
    app = create_app(tmp_path / "materials.db", qq_background=False, chaoxing_background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            task = (
                await client.post(
                    "/api/tasks",
                    json={
                        "title": "作业",
                        "description": description,
                    },
                )
            ).json()
            result = await client.get(f"/api/tasks/{task['id']}/material-suggestions")
            assert [item["text"] for item in result.json()["items"]] == ["实验报告", "源代码"]
