"""QQ semantic intake uses synthetic messages, isolated databases and model doubles."""

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from sqlmodel import Session

from campus_inbox.connectors.qq import AdapterState, Batch, Config, Incoming, Selection
from campus_inbox.main import create_app
from campus_inbox.models import ModelSettings
from campus_inbox.triage import classify


class Model:
    def __init__(self, decision="notification", quote=None, **fields):
        self.calls = []
        self.decision, self.quote, self.fields = decision, quote, fields
        self.fail = False

    def classify_contextual(self, text, reference, timezone, context):
        self.calls.append((text, reference, timezone, context))
        if self.fail:
            raise RuntimeError("private-provider-error")
        return dict(
            classification=self.decision,
            classification_quote=self.quote if self.quote is not None else text[:500],
            **self.fields,
        )

    def generate_structured(self, *args):
        pytest.fail("QQ classification and extraction must use one call, not two")


SETTINGS = SimpleNamespace(enabled=True, model="synthetic:local")


@pytest.mark.parametrize(
    "text", ["收到", "好的！", "谢谢。", "哈哈哈～", "嗯嗯", "好的，谢谢！", "收到\n收到", "ＯＫ！"]
)
def test_exact_short_replies_skip_the_model(text):
    def no_model(_):
        pytest.fail("Short replies must not call the model factory")

    decision, output = classify(text, SETTINGS, no_model, "2026-11-04", "Asia/Shanghai", {})
    assert decision.decision == "chat" and decision.method == "rule"
    assert output is None


@pytest.mark.parametrize(
    ("text", "decision"),
    [
        ("收到，明晚八点前发到平台上。", "notification"),
        ("这次考试好难啊。", "chat"),
        ("明天交不交？", "uncertain"),
        ("作品名《图表》，明晚八点前发到平台上。", "notification"),
        ("好的，实验报告取消了。", "notification"),
        ("好的，明天交。", "notification"),
        ("考试", "uncertain"),
        ("谢谢？", "uncertain"),
        ("不好意思", "chat"),
        ("收到，好的，但是明天不行", "uncertain"),
    ],
)
def test_long_replies_and_keyword_free_messages_reach_the_model(text, decision):
    model = Model(decision)
    result, _ = classify(text, SETTINGS, lambda _: model, "2026-11-04", "Asia/Shanghai", {})
    assert result.decision == decision and result.method == "llm"
    assert len(model.calls) == 1


def test_task_allowlist_takes_priority_over_reply_denylist(monkeypatch):
    monkeypatch.setattr("campus_inbox.triage.SHORT_REPLIES", frozenset({"作业"}))
    model = Model("uncertain")
    result, _ = classify("作业", SETTINGS, lambda _: model, "2026-11-04", "Asia/Shanghai", {})
    assert result.method == "llm"
    assert len(model.calls) == 1


@pytest.mark.parametrize("mode", ["disabled", "too_long", "failure", "bad_quote", "bad_shape"])
def test_unavailable_or_invalid_classification_never_discards_messages(mode):
    model = Model("chat")
    settings, text = SETTINGS, "请关注明天的安排。"
    if mode == "disabled":
        settings = SimpleNamespace(enabled=False, model="")
    elif mode == "too_long":
        text *= 1500
    elif mode == "failure":
        model.fail = True
    elif mode == "bad_quote":
        model.quote = "不在原文里的证据"
    elif mode == "bad_shape":
        model.decision = "execute_task"
    decision, output = classify(text, settings, lambda _: model, "2026-11-04", "Asia/Shanghai", {})
    assert decision.decision == "uncertain" and decision.method == "fallback"
    assert "private-provider-error" not in decision.note
    assert output is None
    assert len(model.calls) == (0 if mode in ("disabled", "too_long") else 1)


class Adapter:
    def status(self):
        return AdapterState(connected=True, account_id="100")

    def poll(self, cursor):
        return Batch(cursor=cursor)

    def disconnect(self):
        pass


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def intake(tmp_path):
    model = Model()
    app = create_app(
        tmp_path / "isolated.db",
        provider_factory=lambda _: model,
        qq_adapter=Adapter(),
        qq_background=False,
    )
    async with app.router.lifespan_context(app):
        with Session(app.state.engine) as session:
            settings = session.get(ModelSettings, 1)
            settings.enabled, settings.model = True, "synthetic:local"
            session.add(settings)
            session.commit()
        app.state.qq_core.configure(
            Config(enabled=True, account_id="100", groups=[Selection(id="200", name="软件工程群")])
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield app, model, client


def receive(app, text, message_id="1"):
    core = app.state.qq_core
    event = Incoming(
        account_id="100",
        group_id="200",
        sender_id="300",
        message_id=message_id,
        text=text,
        sent_at=datetime(2026, 11, 4, 8, tzinfo=UTC),
    )
    assert core.receive(event) == "queued"
    core.sync()
    assert core.receive(event) == "duplicate"


@pytest.mark.anyio
async def test_keyword_free_notification_gets_fields_without_creating_a_task(intake):
    app, model, client = intake
    text = "作品名《图表》，明晚八点前发到平台上。"
    model.fields = {"title": "图表", "due_text": "明晚八点前", "location": "虚构教室"}
    receive(app, text)
    items = (await client.get("/api/inbox")).json()
    assert len(items) == 1
    item = items[0]
    assert item["classification"]["decision"] == "notification"
    assert item["fields"]["title"] == "图表"
    assert item["fields"]["due_at"] == "2026-11-05T12:00:00+00:00"
    assert item["fields"]["location"] == ""
    assert item["message"]["text"] == text
    assert "_classification" not in item["evidence"]
    assert model.calls[0][3]["group_name"] == "软件工程群"
    assert len(model.calls) == 1
    assert (await client.get("/api/tasks")).json() == []


@pytest.mark.anyio
async def test_chat_is_hidden_from_pending_but_recoverable_and_can_bypass_reply_rule(intake):
    app, model, client = intake
    receive(app, "收到")
    assert model.calls == []
    assert (await client.get("/api/inbox")).json() == []
    items = (await client.get("/api/inbox?status=ignored")).json()
    assert len(items) == 1
    item = items[0]
    assert item["message"]["text"] == "收到"
    assert item["classification"]["decision"] == "chat"
    assert (await client.post(f"/api/suggestions/{item['id']}/reopen")).status_code == 200
    model.decision = "uncertain"
    updated = await client.post(f"/api/inbox/{item['id']}/analyze")
    assert updated.status_code == 200
    assert updated.json()["status"] == "pending"
    assert updated.json()["classification"]["decision"] == "uncertain"
    assert len(model.calls) == 1


@pytest.mark.anyio
async def test_keyword_chat_is_filtered_and_failure_stays_pending(intake):
    app, model, client = intake
    model.decision = "chat"
    receive(app, "这次考试好难啊。")
    assert (await client.get("/api/inbox")).json() == []
    assert len((await client.get("/api/inbox?status=ignored")).json()) == 1
    model.fail = True
    receive(app, "明晚八点前发到平台上。", "2")
    items = (await client.get("/api/inbox")).json()
    assert len(items) == 1
    assert items[0]["classification"]["decision"] == "uncertain"
    assert items[0]["status"] == "pending"
    assert "private-provider-error" not in str(items[0])
    assert (await client.get("/api/tasks")).json() == []


@pytest.mark.anyio
async def test_manual_import_keeps_original_rule_pipeline(intake):
    _, model, client = intake
    response = await client.post(
        "/api/inbox",
        json={"text": "软件工程：明天18:00前提交实验四报告。", "sent_at": "2026-11-04T08:00:00Z"},
    )
    # Original manual pipeline uses field extraction, not QQ semantic classification.
    assert model.calls == []
    assert response.status_code == 200
    assert response.json()["classification"] is None
    assert response.json()["fields"]["title"] == "实验四报告"
