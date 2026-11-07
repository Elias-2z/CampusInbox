"""Isolated adapter contract tests; no real QQ account, bridge or network."""

from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError
from sqlmodel import Session, select

from campus_inbox.connectors import chaoxing
from campus_inbox.connectors.qq import AdapterState, Batch, Group, Incoming, Member, QQEvent
from campus_inbox.main import create_app
from campus_inbox.models import Message

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Adapter:
    def __init__(self):
        self.connected = True
        self.events = []
        self.cursors = []
        self.fail = False

    def status(self):
        return AdapterState(connected=self.connected, account_id="100")

    def groups(self):
        return [Group(id="200", name="软件工程群")]

    def members(self, group_id):
        return [Member(id="300", name="老师")]

    def poll(self, cursor):
        self.cursors.append(cursor)
        if self.fail:
            raise RuntimeError("private-token-and-notice")
        return Batch(events=self.events, cursor="next")

    def disconnect(self):
        self.connected = False


def event(**changes):
    return Incoming.model_validate(
        dict(
            account_id="100",
            group_id="200",
            sender_id="300",
            message_id="m1",
            sent_at=datetime(2026, 11, 2, 8, tzinfo=UTC),
            text="软件工程：明天晚上八点前提交需求分析报告。",
            **changes,
        )
    )


@pytest.fixture
async def context(tmp_path, monkeypatch):
    # Keep the example QQ notices inside their active deadline window.
    monkeypatch.setattr(chaoxing, "now", lambda: datetime(2026, 11, 2, tzinfo=UTC))
    adapter = Adapter()
    app = create_app(tmp_path / "db.sqlite3", qq_adapter=adapter, qq_background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield app, adapter, client, tmp_path


async def enable(client, members=None):
    status = (await client.get("/api/connectors/qq")).json()
    config = status["config"]
    config.update(
        enabled=True,
        account_id="100",
        groups=[
            dict(
                id="200",
                name="软件工程群",
                member_ids=members or [],
            )
        ],
    )
    response = await client.put("/api/connectors/qq/settings", json=config)
    assert response.status_code == 200, response.text
    return config


async def test_catalog_receive_dedupe_and_inbox_not_formal_tasks(context):
    app, adapter, client, _ = context
    await enable(client)
    assert (await client.get("/api/connectors/qq/groups")).json()[0]["id"] == "200"
    assert (await client.get("/api/connectors/qq/groups/200/members")).json()[0]["id"] == "300"
    adapter.events = [event(), event()]
    for _ in range(2):
        response = await client.post("/api/connectors/qq/sync")
        assert response.json()["counts"]["done"] == 1
    assert adapter.cursors == ["", "next"]
    inbox = (await client.get("/api/inbox")).json()
    assert len(inbox) == 1
    assert inbox[0]["message"]["text"] == event().text
    assert inbox[0]["fields"]["due_at"].startswith("2026-11-03")
    assert "300" in inbox[0]["message"]["source"]
    assert (await client.get("/api/tasks")).json() == []
    assert app.state.qq_core.receive(event()) == "duplicate"


async def test_filters_account_group_sender_self_and_pause(context):
    app, _, client, _ = context
    core = app.state.qq_core
    assert core.receive(event()) == "filtered"
    await enable(client, ["300"])
    for field, value in [
        ("account_id", "999"),
        ("group_id", "999"),
        ("sender_id", "999"),
        ("sender_id", "100"),
    ]:
        data = event().model_dump()
        data[field] = value
        assert core.receive(data) == "filtered"
    assert core.receive(event()) == "queued"
    config = (await client.get("/api/connectors/qq")).json()["config"]
    config["groups"] = []
    config["enabled"] = False
    await client.put("/api/connectors/qq/settings", json=config)
    core.process()
    assert core.status()["counts"]["pending"] == 1
    await enable(client, ["999"])
    core.process()
    assert core.status()["counts"]["blocked"] == 1
    await enable(client, ["300"])
    core.process()
    assert core.status()["counts"]["done"] == 1


async def test_config_revision_and_scope_validation(context):
    _, _, client, _ = context
    old = await enable(client)
    assert (await client.put("/api/connectors/qq/settings", json=old)).status_code == 409
    config = (await client.get("/api/connectors/qq")).json()["config"]
    config["groups"].append(config["groups"][0])
    assert (await client.put("/api/connectors/qq/settings", json=config)).status_code == 422


async def test_restart_preserves_queue_config_and_cursor(context):
    app, adapter, client, path = context
    await enable(client)
    core = app.state.qq_core
    core.receive(event())
    reopened = create_app(path / "db.sqlite3", qq_adapter=Adapter(), qq_background=False)
    async with reopened.router.lifespan_context(reopened):
        other = reopened.state.qq_core
        assert other.status()["config"].enabled
        assert other.status()["counts"]["pending"] == 1
        other.sync()
        assert other.status()["counts"]["done"] == 1
        assert other.receive(event()) == "duplicate"
    adapter.events = [event()]
    core.sync()
    assert core.status()["counts"]["done"] == 1


async def test_failure_retry_and_safe_error(context, monkeypatch):
    from campus_inbox.connectors import qq

    app, adapter, client, _ = context
    await enable(client)
    adapter.fail = True
    assert "private-token" not in (await client.post("/api/connectors/qq/sync")).text
    assert adapter.cursors == [""]
    adapter.fail = False
    adapter.events = [event()]
    original = qq.services.import_message

    def fail(*args, **kwargs):
        raise RuntimeError("secret")

    monkeypatch.setattr(qq.services, "import_message", fail)
    assert (await client.post("/api/connectors/qq/sync")).json()["counts"]["failed"] == 1
    monkeypatch.setattr(qq.services, "import_message", original)
    assert (await client.post("/api/connectors/qq/retry")).json()["counts"]["done"] == 1
    assert app.state.qq_core.status()["error"] == ""


async def test_crash_after_notice_commit_reuses_existing_notice(context):
    from campus_inbox import services
    from campus_inbox.schemas import ImportMessage

    app, _, client, _ = context
    await enable(client)
    core = app.state.qq_core
    core.receive(event())
    with Session(core.engine) as session:
        row = session.exec(select(QQEvent)).one()
        services.import_message(
            session,
            ImportMessage(
                text=event().text,
                source="QQ 群200 · 发件人300",
                sent_at=event().sent_at,
                timezone="Asia/Shanghai",
            ),
            core.provider_factory,
            dedupe_key="qq:" + row.identity,
        )
    core.process()
    with Session(core.engine) as session:
        assert len(session.exec(select(Message)).all()) == 1
    assert core.status()["counts"]["done"] == 1


async def test_disconnect_pauses_and_keeps_saved_scope(context):
    app, adapter, client, _ = context
    await enable(client)
    response = (await client.post("/api/connectors/qq/disconnect")).json()
    assert not response["config"]["enabled"]
    assert response["config"]["groups"][0]["id"] == "200"
    assert not adapter.connected
    assert app.state.qq_core.receive(event()) == "filtered"


async def test_content_revisions_and_external_ids_remain_separate(context):
    app, _, client, _ = context
    await enable(client)
    core = app.state.qq_core
    data = event().model_dump()
    for changes in [
        {},
        {"message_id": "m2"},
        {"text": "软件工程：需求分析报告延期至周五晚上八点。"},
    ]:
        assert core.receive({**data, **changes}) == "queued"
    core.process()
    assert core.status()["counts"]["done"] == 3
    assert len((await client.get("/api/inbox")).json()) == 3


def test_unknown_time_and_invalid_identity_rejected():
    data = event().model_dump()
    for changes in [{"sent_at": datetime(2026, 11, 2)}, {"group_id": "abc"}, {"text": "  "}]:
        with pytest.raises(ValidationError):
            Incoming.model_validate({**data, **changes})


async def test_concurrent_delivery_queues_once(context):
    import asyncio

    app, _, client, _ = context
    await enable(client)
    outcomes = await asyncio.gather(
        *[asyncio.to_thread(app.state.qq_core.receive, event()) for _ in range(4)]
    )
    assert outcomes.count("queued") == 1
    assert outcomes.count("duplicate") == 3


async def test_background_intake_and_shutdown(tmp_path):
    import asyncio

    adapter = Adapter()
    adapter.events = [event()]
    app = create_app(tmp_path / "auto.db", qq_adapter=adapter)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await enable(client)
            for _ in range(50):
                await asyncio.sleep(0.1)
                if app.state.qq_core.status()["counts"]["done"]:
                    break
            assert app.state.qq_core.status()["counts"]["done"] == 1
            assert (await client.get("/api/tasks")).json() == []
    assert not adapter.connected
    assert not app.state.qq_core.worker.is_alive()


async def test_blocked_events_do_not_starve_new_allowed_messages(context):
    app, _, client, _ = context
    await enable(client)
    core = app.state.qq_core
    for i in range(11):
        core.receive({**event().model_dump(), "message_id": str(i)})
    await enable(client, ["999"])
    core.process()
    assert core.status()["counts"]["blocked"] == 10
    core.receive({**event().model_dump(), "message_id": "new", "sender_id": "999"})
    core.process()
    assert core.status()["counts"]["done"] == 1


async def test_group_course_fills_missing_and_reanalysis_preserves_snapshot(context):
    app, _, client, _ = context
    await enable(client)
    core = app.state.qq_core
    message = {**event().model_dump(), "text": "请于明天18:00前提交实验报告。"}
    core.receive(message)
    core.process()
    item = (await client.get("/api/inbox")).json()[0]
    assert item["fields"]["course"] == "软件工程"
    assert item["evidence"]["course"]["method"] == "connector"
    assert item["message"]["text"] == message["text"]
    config = (await client.get("/api/connectors/qq")).json()["config"]
    config["groups"][0]["course"] = "另一门课程"
    await client.put("/api/connectors/qq/settings", json=config)
    assert core.receive(message) == "duplicate"
    again = await client.post(f"/api/inbox/{item['id']}/analyze")
    assert again.json()["fields"]["course"] == "软件工程"


@pytest.mark.parametrize("mapping,expected", [("机械制造基础", "机械制造基础"), ("", "")])
async def test_explicit_course_alias_or_opt_out(context, mapping, expected):
    app, _, client, _ = context
    await enable(client)
    config = (await client.get("/api/connectors/qq")).json()["config"]
    config["groups"][0]["course"] = mapping
    await client.put("/api/connectors/qq/settings", json=config)
    core = app.state.qq_core
    core.receive({**event().model_dump(), "text": "明天18:00前提交实验报告。"})
    core.process()
    row = (await client.get("/api/inbox")).json()[0]
    assert row["fields"]["course"] == expected
    if not expected:
        assert "course" not in row["evidence"]


async def test_explicit_notice_course_wins_over_group_mapping(context):
    app, _, client, _ = context
    await enable(client)
    core = app.state.qq_core
    core.receive({**event().model_dump(), "text": "数据库原理：明天18:00前提交实验报告。"})
    core.process()
    row = (await client.get("/api/inbox")).json()[0]
    assert row["fields"]["course"] == "数据库原理"
    assert row["evidence"]["course"]["method"] == "rule"


async def test_readable_source_and_context_without_altering_original_source(context):
    app, _, client, _ = context
    await enable(client)
    config = (await client.get("/api/connectors/qq")).json()["config"]
    config["groups"][0]["name"] = "2026年机械制造基础三班"
    await client.put("/api/connectors/qq/settings", json=config)
    incoming = {
        **event().model_dump(),
        "sender_name": "张老师",
        "text": "明天18:00前提交实验报告。",
    }
    core = app.state.qq_core
    core.receive(incoming)
    core.process()
    row = (await client.get("/api/inbox")).json()[0]
    assert row["message"]["source"] == "QQ · 机械制造基础 · 发件人 张老师"
    assert row["message"]["source_context"]["group_id"] == "200"
    assert row["message"]["source_context"]["group_name"] == "2026年机械制造基础三班"
    assert row["message"]["text"] == incoming["text"]
    with Session(core.engine) as session:
        assert session.exec(select(Message)).one().source == "QQ 群200 · 发件人300"
    assert core.receive({**incoming, "sender_name": "老师新昵称"}) == "duplicate"


async def test_course_suggestion_needs_confirmation_and_does_not_create_tasks(context):
    _, _, client, _ = context
    result = await client.post(
        "/api/connectors/qq/course-suggestion",
        json={
            "group_name": "2026年软件工程三班",
            "use_model": False,
        },
    )
    assert result.json() == {"course": "软件工程", "method": "rule", "needs_confirmation": True}
    assert (await client.get("/api/tasks")).json() == []
