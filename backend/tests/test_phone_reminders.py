"""Exercise isolated Android reminder transport against a temporary root database."""

import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlmodel import Session

from campus_inbox.database import initialize, make_engine
from campus_inbox.main import create_app
from campus_inbox.models import Message, Suggestion, Task, TaskEvent

try:
    from campus_inbox.phone_reminders import PhoneReminders, control_router
except ModuleNotFoundError as error:
    if error.name != "campus_inbox.phone_reminders":
        raise
    PhoneReminders = None

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_root_app_exposes_disabled_mobile_control_without_opening_lan(tmp_path):
    app = create_app(tmp_path / "root.db", qq_background=False, chaoxing_background=False)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8000"
        ) as client:
            response = await client.get("/api/mobile-reminders/status")
            assert response.status_code == 200
            assert response.json()["enabled"] is False
            assert response.json()["address"] is None


class Clock:
    def __init__(self):
        self.current = datetime(2026, 11, 3, 8, tzinfo=UTC)

    def __call__(self):
        return self.current

    def advance(self, **parts):
        self.current += timedelta(**parts)


@pytest.fixture
async def context(tmp_path):
    assert PhoneReminders is not None, "phone reminder service has not been implemented"
    engine = make_engine(tmp_path / "root.sqlite3")
    initialize(engine)
    clock = Clock()
    phone = PhoneReminders(engine, clock=clock)
    app = FastAPI()
    app.include_router(control_router(phone))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://127.0.0.1:8000",
    ) as admin:
        try:
            yield admin, phone, engine, clock
        finally:
            phone.stop()
            engine.dispose()


async def start(admin):
    response = await admin.post(
        "/api/mobile-reminders/start",
        json={"host": "127.0.0.1", "port": 0, "allow_unencrypted_lan": True},
    )
    assert response.status_code == 200, response.text
    return response.json()["address"]


async def pair(admin, client, name="测试手机"):
    response = await admin.post("/api/mobile-reminders/pairing")
    assert response.status_code == 200, response.text
    code = response.json()["code"]
    response = await client.post("/v1/pair", json={"code": code, "name": name})
    assert response.status_code == 200, response.text
    return response.json()


def auth(phone):
    return {"Authorization": "Bearer " + phone["token"]}


def seed_old_task(engine, clock):
    old = clock() - timedelta(days=1)
    with Session(engine) as session:
        session.add(
            Message(
                id="old-message",
                text="旧通知",
                source="学习通",
                sent_at=old,
                received_at=old,
                dedupe_key="old",
            )
        )
        session.commit()
        session.add(Suggestion(id="old-suggestion", message_id="old-message", status="accepted"))
        session.commit()
        session.add(
            Task(
                id="task-1",
                suggestion_id="old-suggestion",
                message_id="old-message",
                title="旧任务",
                course="数据库",
                due_at=clock() + timedelta(hours=25),
                created_at=old,
                updated_at=old,
            )
        )
        session.commit()


async def test_service_is_opt_in_and_exposes_only_narrow_phone_protocol(context):
    admin, phone, _engine, _clock = context
    assert (await admin.get("/api/mobile-reminders/status")).json()["enabled"] is False
    assert (await admin.post("/api/mobile-reminders/pairing")).status_code == 409
    assert (
        await admin.post(
            "/api/mobile-reminders/start",
            json={
                "host": "127.0.0.1",
                "port": 0,
                "allow_unencrypted_lan": False,
            },
        )
    ).status_code == 422
    assert (
        await admin.post(
            "/api/mobile-reminders/start",
            json={
                "host": "0.0.0.0",
                "port": 0,
                "allow_unencrypted_lan": True,
            },
        )
    ).status_code == 422
    assert phone.status()["enabled"] is False
    address = await start(admin)
    async with httpx.AsyncClient(base_url=address, trust_env=False) as client:
        health = await client.get("/v1/health")
        assert health.status_code == 200
        assert health.json()["app"] == "CourseInbox Reminders"
        assert health.json()["version"] == 1
        assert (await client.get("/api/tasks")).status_code == 404
        assert (await client.get("/api/export.json")).status_code == 404
        assert (await client.get("/v1/alerts")).status_code == 401
        assert (
            await client.get("/v1/alerts", headers={"Authorization": "Bearer old-main-token"})
        ).status_code == 401
        assert (await client.get("/v1/health", headers={"Host": "evil.example"})).status_code == 403
        assert (
            await client.get("/v1/health", headers={"Origin": "https://evil.example"})
        ).status_code == 403
    remote = FastAPI()
    remote.include_router(control_router(phone))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=remote, client=("192.0.2.7", 1234)),
        base_url="http://127.0.0.1:8000",
    ) as attacker:
        assert (await attacker.post("/api/mobile-reminders/pairing")).status_code == 403


async def test_root_data_is_read_only_and_each_phone_acknowledges_independently(context):
    admin, _phone, engine, clock = context
    seed_old_task(engine, clock)
    address = await start(admin)
    async with httpx.AsyncClient(base_url=address, trust_env=False) as client:
        first = await pair(admin, client, "第一台")
        second = await pair(admin, client, "第二台")
        assert (await client.get("/v1/alerts", headers=auth(first))).json()["events"] == []
        clock.advance(minutes=1)
        with Session(engine) as session:
            session.add_all(
                [
                    Message(
                        id="new-message",
                        text="新通知正文",
                        source="学习通",
                        sent_at=clock(),
                        received_at=clock(),
                        dedupe_key="new",
                    ),
                    Message(
                        id="late-old",
                        text="补读的旧通知",
                        source="学习通",
                        sent_at=clock() - timedelta(days=3),
                        received_at=clock(),
                        dedupe_key="late",
                    ),
                    TaskEvent(
                        id="event-1",
                        task_id="task-1",
                        message_id="old-message",
                        operation="update",
                        before={"title": "旧任务"},
                        after={"title": "调整任务"},
                        created_at=clock(),
                    ),
                ]
            )
            session.commit()
        with sqlite3.connect(engine.url.database) as connection:
            before = list(connection.iterdump())
        batch = (await client.get("/v1/alerts", headers=auth(first))).json()["events"]
        assert {event["kind"] for event in batch} == {"new_message", "task_change"}
        assert "新通知正文" in str(batch) and "补读的旧通知" not in str(batch)
        assert batch == (await client.get("/v1/alerts", headers=auth(second))).json()["events"]
        ids = [event["id"] for event in batch]
        assert (
            await client.post("/v1/ack", headers=auth(first), json={"ids": ids + ["fake"]})
        ).status_code == 400
        assert (await client.get("/v1/alerts", headers=auth(first))).json()["events"] == batch
        assert (
            await client.post("/v1/ack", headers=auth(first), json={"ids": ids})
        ).status_code == 200
        assert (await client.get("/v1/alerts", headers=auth(first))).json()["events"] == []
        assert (await client.get("/v1/alerts", headers=auth(second))).json()["events"] == batch
        clock.advance(hours=2)
        deadline = (await client.get("/v1/alerts", headers=auth(first))).json()["events"]
        assert len(deadline) == 1 and deadline[0]["kind"] == "deadline_due"
        assert "旧任务" in deadline[0]["body"]
        with sqlite3.connect(engine.url.database) as connection:
            assert list(connection.iterdump()) == before


async def test_pairing_is_one_time_expires_and_is_limited(context):
    admin, _phone, _engine, clock = context
    address = await start(admin)
    async with httpx.AsyncClient(base_url=address, trust_env=False) as client:
        code = (await admin.post("/api/mobile-reminders/pairing")).json()["code"]
        clock.advance(minutes=6)
        assert (
            await client.post("/v1/pair", json={"code": code, "name": "手机"})
        ).status_code == 400
        code = (await admin.post("/api/mobile-reminders/pairing")).json()["code"]
        for _ in range(5):
            assert (
                await client.post("/v1/pair", json={"code": "invalid", "name": "手机"})
            ).status_code == 400
        assert (
            await client.post("/v1/pair", json={"code": code, "name": "手机"})
        ).status_code == 400
        paired = await pair(admin, client)
        response = await client.post("/v1/pair", json={"code": code, "name": "手机"})
        assert response.status_code == 400
        status = (await admin.get("/api/mobile-reminders/status")).text
        assert paired["token"] not in status and code not in status


async def test_stop_restart_and_revoke_preserve_receipts_without_reusing_secrets(context):
    admin, phone, engine, clock = context
    seed_old_task(engine, clock)
    address = await start(admin)
    async with httpx.AsyncClient(base_url=address, trust_env=False) as client:
        paired = await pair(admin, client)
        clock.advance(minutes=1)
        with Session(engine) as session:
            session.add(
                Message(
                    id="fresh",
                    text="新提醒",
                    source="学习通",
                    sent_at=clock(),
                    received_at=clock(),
                    dedupe_key="fresh",
                )
            )
            session.commit()
        event = (await client.get("/v1/alerts", headers=auth(paired))).json()["events"][0]
        assert (
            await client.post("/v1/ack", headers=auth(paired), json={"ids": [event["id"]]})
        ).status_code == 200
    assert (await admin.post("/api/mobile-reminders/stop")).status_code == 200
    assert phone.status()["enabled"] is False
    address = await start(admin)
    async with httpx.AsyncClient(base_url=address, trust_env=False) as client:
        assert (await client.get("/v1/alerts", headers=auth(paired))).json()["events"] == []
        assert (
            await admin.post(
                "/api/mobile-reminders/revoke", json={"device_id": paired["device_id"]}
            )
        ).status_code == 200
        assert (await client.get("/v1/alerts", headers=auth(paired))).status_code == 401
