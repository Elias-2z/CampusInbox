"""API mode tests use only synthetic messages and mock network/credential backends."""

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError
from sqlmodel import Session, select

from campus_inbox.inference import APIConfig, APIProvider, APIUsage, ModelManager
from campus_inbox.main import create_app
from campus_inbox.models import ModelSettings
from campus_inbox.providers import ProviderError

pytestmark = pytest.mark.anyio
SECRET = "test-only-api-key-never-real"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Secrets:
    label = "测试安全存储"

    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key, "")

    def set(self, key, value):
        self.data[key] = value


class Local:
    calls = 0

    def __init__(self, config):
        self.config = config

    def list_models(self):
        return ["local:test"]

    def generate_structured(self, *args):
        Local.calls += 1
        return {"title": "报告"}


@pytest.fixture
async def context(tmp_path):
    secrets, calls = Secrets(), []

    def handle(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer " + SECRET
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["stream"] is False
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps({"title": "报告"}, ensure_ascii=False),
                        },
                    }
                ]
            },
        )

    Local.calls = 0
    transport = httpx.MockTransport(handle)
    app = create_app(
        tmp_path / "db.sqlite3",
        provider_factory=Local,
        model_secrets=secrets,
        api_transport=transport,
        qq_background=False,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, secrets, calls, app, tmp_path, transport


async def settings(client):
    state = (await client.get("/api/settings/inference")).json()
    return {
        k: v for k, v in state.items() if k in ("mode", "revision", "local", "api") and k != "api"
    } | {
        "api": {k: v for k, v in state["api"].items() if k != "key_saved"},
    }


async def enable(client, **changes):
    body = await settings(client)
    body["mode"] = "api"
    body["api"].update(
        enabled=True,
        base_url="https://provider.example/v1",
        model="test-model",
        consent=True,
        api_key=SECRET,
        **changes,
    )
    response = await client.put("/api/settings/inference", json=body)
    assert response.status_code == 200, response.text
    return body


async def import_notice(client, text="请提交报告，时间待定。"):
    return await client.post(
        "/api/inbox", json={"text": text, "sent_at": "2026-11-04T12:00:00+08:00"}
    )


async def test_api_selection_inbox_and_key_never_returned_or_stored_in_db(context):
    client, secrets, calls, app, _, _ = context
    await enable(client)
    result = await import_notice(client)
    assert result.status_code == 200, result.text
    assert len(calls) == 1
    assert Local.calls == 0
    assert result.json()["status"] == "pending"
    assert result.json()["fields"]["title"] == "报告"
    assert (await client.get("/api/tasks")).json() == []
    public = await client.get("/api/settings/inference")
    assert public.json()["api"]["key_saved"]
    assert SECRET not in public.text
    assert public.json()["usage"]["used"] == 1
    from campus_inbox.inference import InferenceSettings

    with Session(app.state.engine) as session:
        row = session.get(InferenceSettings, 1)
        assert SECRET not in json.dumps(row.api)
    assert list(secrets.data.values()) == [SECRET]
    assert (await client.get("/api/settings/model")).json()["base_url"] == "http://127.0.0.1:11434"


async def test_switch_back_local_preserves_each_mode_and_does_not_call_api(context):
    client, _, calls, _, _, _ = context
    await enable(client)
    body = await settings(client)
    body["mode"] = "local"
    body["local"].update(enabled=True, model="local:test")
    assert (await client.put("/api/settings/inference", json=body)).status_code == 200
    await import_notice(client)
    assert Local.calls == 1 and not calls
    state = (await client.get("/api/settings/inference")).json()
    assert state["api"]["key_saved"]
    assert state["api"]["model"] == "test-model"
    # Legacy local-only configuration API remains compatible.
    assert (await client.get("/api/settings/model")).json()["model"] == "local:test"


async def test_no_consent_no_key_or_new_destination_cannot_send(context):
    client, _, calls, _, _, _ = context
    body = await settings(client)
    body["mode"] = "api"
    body["api"].update(
        enabled=True, base_url="https://provider.example/v1", model="test-model", api_key=SECRET
    )
    assert (await client.put("/api/settings/inference", json=body)).status_code == 422
    body["api"].update(consent=True, api_key="")
    assert (await client.put("/api/settings/inference", json=body)).status_code == 422
    assert (await client.post("/api/settings/inference/probe", json=body)).json()[
        "reachable"
    ] is False
    assert not calls
    await enable(client)
    changed = await settings(client)
    changed["api"]["base_url"] = "https://other.example/v1"
    # A blank key cannot carry the old provider's key to another endpoint.
    assert (await client.put("/api/settings/inference", json=changed)).status_code == 422
    assert not (await client.post("/api/settings/inference/probe", json=changed)).json()[
        "reachable"
    ]
    assert not calls


async def test_probe_uses_only_synthetic_text_and_does_not_activate_or_store_key(context):
    client, secrets, calls, _, _, _ = context
    body = await settings(client)
    body["mode"] = "api"
    body["api"].update(
        base_url="https://provider.example/v1", model="test-model", consent=True, api_key=SECRET
    )
    response = await client.post("/api/settings/inference/probe", json=body)
    assert response.json()["reachable"]
    text = json.loads(json.loads(calls[0].content)["messages"][-1]["content"])["notification"]
    assert text == "连接测试：请提交测试报告。"
    assert not secrets.data
    assert (await client.get("/api/settings/inference")).json()["mode"] == "local"


async def test_limit_is_persistent_and_failure_has_no_local_fallback(context):
    client, secrets, calls, app, path, transport = context
    await enable(client, daily_limit=1)
    await import_notice(client)
    response = await import_notice(client, "请提交另一份报告，时间待定。")
    assert len(calls) == 1
    assert any("上限" in w for w in response.json()["warnings"])
    assert Local.calls == 0
    reopened = create_app(
        path / "db.sqlite3", model_secrets=secrets, api_transport=transport, qq_background=False
    )
    async with reopened.router.lifespan_context(reopened):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=reopened), base_url="http://test"
        ) as other:
            state = (await other.get("/api/settings/inference")).json()
            assert state["mode"] == "api" and state["api"]["key_saved"]
            assert state["usage"]["used"] == 1


async def test_concurrent_budget_reservation_cannot_exceed_limit(context):
    _, secrets, _, app, _, transport = context
    manager = ModelManager(app.state.engine, secrets=secrets, transport=transport)
    config = APIConfig(base_url="https://provider.example/v1", daily_limit=1)

    async def attempt():
        try:
            await asyncio.to_thread(manager.reserve, config)
            return True
        except ProviderError:
            return False

    assert sum(await asyncio.gather(*[attempt() for _ in range(4)])) == 1
    with Session(app.state.engine) as session:
        assert session.exec(select(APIUsage)).one().attempts == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"error": SECRET}),
        httpx.Response(429, json={"error": SECRET}),
        httpx.Response(500, text=SECRET),
        httpx.Response(302, headers={"location": "https://other.example"}),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(
            200, json={"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}
        ),
        httpx.Response(200, json={"choices": [{"message": {"content": '{"unexpected":"value"}'}}]}),
    ],
)
def test_bad_api_responses_do_not_echo_keys_or_raw_server_body(response):
    config = APIConfig(base_url="https://provider.example/v1", model="test", consent=True)
    calls = []
    provider = APIProvider(
        config,
        SECRET,
        transport=httpx.MockTransport(lambda request: calls.append(request) or response),
    )
    with pytest.raises(ProviderError) as error:
        provider.generate_structured("测试报告", "2026-11-04", "Asia/Shanghai")
    assert SECRET not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.example/v1",
        "https://user:pass@api.example/v1",
        "https://api.example/v1?key=secret",
        "https://api.example/v1#key",
        "https://api.example/v1/chat/completions",
        "https://api.example:0",
    ],
)
def test_invalid_endpoint(url):
    with pytest.raises(ValidationError):
        APIConfig(base_url=url)


async def test_validation_never_echoes_key_and_stale_save_is_rejected(context):
    client, _, _, _, _, _ = context
    body = await enable(client)
    assert (await client.put("/api/settings/inference", json=body)).status_code == 409
    body = await settings(client)
    body["api"]["api_key"] = SECRET + "\n"
    response = await client.put("/api/settings/inference", json=body)
    assert response.status_code == 422
    assert SECRET not in response.text


async def test_local_existing_config_is_preserved_without_enabling_cloud(context):
    client, _, calls, app, _, _ = context
    with Session(app.state.engine) as session:
        config = session.get(ModelSettings, 1)
        config.model, config.enabled = "local:test", True
        session.add(config)
        session.commit()
    result = (await client.get("/api/settings/inference")).json()
    assert result["mode"] == "local"
    assert result["local"]["model"] == "local:test"
    await import_notice(client)
    assert Local.calls == 1 and not calls


async def test_rules_skip_api_and_provider_failure_keeps_notice_without_local_fallback(
    context, monkeypatch
):
    client, _, calls, _, _, _ = context
    await enable(client)
    response = await import_notice(
        client, "软件工程：请于明天18:00前提交需求分析报告，提交至学习通。"
    )
    assert response.status_code == 200
    assert not calls

    def unavailable(*args, **kwargs):
        raise ProviderError("API 暂不可用")

    monkeypatch.setattr(APIProvider, "generate_structured", unavailable)
    response = await import_notice(client)
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert response.json()["message"]["text"] == "请提交报告，时间待定。"
    assert "API 暂不可用" in response.json()["warnings"]
    assert Local.calls == 0


async def test_secure_store_failure_still_allows_switching_to_local(context, monkeypatch):
    client, secrets, _, _, _, _ = context
    await enable(client)

    def unavailable(key):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(secrets, "get", unavailable)
    state = (await client.get("/api/settings/inference")).json()
    assert state["credential_error"] and SECRET not in json.dumps(state)
    body = await settings(client)
    body["mode"] = "local"
    result = await client.put("/api/settings/inference", json=body)
    assert result.status_code == 200 and result.json()["mode"] == "local"


def test_api_context_and_json_validation_do_not_send_identifiers():
    def handle(request):
        body = json.loads(request.content)
        user = json.loads(body["messages"][-1]["content"])
        assert user["notification"] == "请提交报告。"
        assert user["source_context"]["group_name"] == "软件工程群"
        assert "group_id" not in user["source_context"]
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '{"title":"报告"}'}}]
            },
        )

    config = APIConfig(base_url="https://provider.example/v1", model="test", consent=True)
    provider = APIProvider(config, SECRET, transport=httpx.MockTransport(handle))
    assert (
        provider.generate_contextual(
            "请提交报告。",
            "2026-11-04",
            "Asia/Shanghai",
            {
                "group_name": "软件工程群",
                "sender_name": "老师",
                "group_id": "123456",
            },
        )["title"]
        == "报告"
    )


async def test_legacy_model_write_invalidates_draft_without_changing_api_mode(context):
    client, *_ = context
    await enable(client)
    draft = await settings(client)
    local = draft["local"] | {"model": "local:other"}
    assert (await client.put("/api/settings/model", json=local)).status_code == 200
    state = (await client.get("/api/settings/inference")).json()
    assert state["mode"] == "api"
    assert state["local"]["model"] == "local:other"
    assert state["revision"] == draft["revision"] + 1
    assert (await client.put("/api/settings/inference", json=draft)).status_code == 409
