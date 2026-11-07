"""OneBot wire-contract and signed callback tests, never a real QQ login."""

import hashlib
import hmac
import json
import sys
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from campus_inbox.connectors import chaoxing
from campus_inbox.connectors.onebot import (
    BridgeConfig,
    BridgeError,
    BridgeUpdate,
    OneBotAdapter,
    normalize,
)
from campus_inbox.main import create_app

pytestmark = pytest.mark.anyio
TOKEN = "test-only-token-1234567890"
SECRET = "test-only-event-1234567890"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Secrets:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key, "")

    def set(self, key, value):
        self.values[key] = value


def setup_bridge(path, handler=None):
    def handle(request):
        assert request.headers["authorization"] == "Bearer " + TOKEN
        action = request.url.path
        data = {
            "/get_status": {"online": True, "good": True},
            "/get_login_info": {"user_id": 100, "nickname": "测试账号"},
            "/get_group_list": [{"group_id": 200, "group_name": "课程群"}],
            "/get_group_member_list": [{"user_id": 300, "card": "老师"}],
        }[action]
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": data})

    store = Secrets()
    bridge = OneBotAdapter(path, secrets=store, transport=httpx.MockTransport(handler or handle))
    bridge.configure(BridgeUpdate(enabled=True, access_token=TOKEN, event_secret=SECRET))
    return bridge, store


def notice(**changes):
    return {
        "post_type": "message",
        "message_type": "group",
        "self_id": 100,
        "group_id": 200,
        "user_id": 300,
        "message_id": 1,
        "time": 1793613600,
        "message": [{"type": "text", "data": {"text": "软件工程：明天18:00前提交需求分析报告。"}}],
        **changes,
    }


async def send(client, payload, secret=SECRET, headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode()
    signature = "sha1=" + hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
    return await client.post(
        "/api/connectors/qq/onebot/events",
        content=body,
        headers={
            "X-Signature": signature,
            "Content-Type": "application/json",
            **(headers or {}),
        },
    )


async def test_catalog_credentials_persistence_and_reconnect(tmp_path):
    bridge, secrets = setup_bridge(tmp_path)
    assert bridge.status().account_id == "100"
    assert bridge.groups()[0].name == "课程群"
    assert bridge.members("200")[0].name == "老师"
    public = json.dumps(bridge.public(), default=str)
    assert TOKEN not in public and SECRET not in public
    assert TOKEN not in bridge.path.read_text()
    assert SECRET not in bridge.path.read_text()
    if sys.platform != "win32":
        assert bridge.path.stat().st_mode & 0o777 == 0o600
    reopened = OneBotAdapter(tmp_path, secrets=secrets, transport=bridge.transport)
    assert reopened.status().connected
    bridge.disconnect()
    assert not bridge.status().connected
    assert not OneBotAdapter(tmp_path, secrets=secrets).config.enabled


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example:443",
        "http://localhost:3000",
        "http://127.0.0.1:3000@evil.example:80",
        "http://127.0.0.1:3000/path",
        "http://127.0.0.1:3000?token=abc",
        "http://127.0.0.1",
        "http://127.0.0.1:3000#x",
        "http://192.168.1.1:3000",
    ],
)
def test_only_literal_loopback_bridge_endpoints(url):
    with pytest.raises(ValidationError):
        BridgeConfig(base_url=url)


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(302, headers={"location": "https://evil.example"}),
        httpx.Response(401),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"status": "failed", "retcode": 1, "data": TOKEN}),
        httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1)),
    ],
)
def test_bridge_failures_are_safe_and_not_reported_connected(tmp_path, reply):
    bridge, _ = setup_bridge(tmp_path, lambda _: reply)
    assert not bridge.status().connected
    assert TOKEN not in json.dumps(bridge.public(), default=str)
    with pytest.raises(BridgeError):
        bridge.call("get_login_info")


async def test_signed_real_http_contract_into_original_inbox(tmp_path, monkeypatch):
    monkeypatch.setattr(chaoxing, "now", lambda: datetime.fromtimestamp(notice()["time"], UTC))
    bridge, _ = setup_bridge(tmp_path)
    app = create_app(tmp_path / "test.db", qq_adapter=bridge, qq_background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            scope = (await client.get("/api/connectors/qq")).json()["config"]
            scope.update(
                enabled=True,
                account_id="100",
                groups=[
                    {
                        "id": "200",
                        "name": "软件工程",
                        "member_ids": ["300"],
                    }
                ],
            )
            assert (await client.put("/api/connectors/qq/settings", json=scope)).status_code == 200
            assert (await send(client, notice(), secret="wrong")).status_code == 401
            assert (
                await send(client, notice(), headers={"Origin": "http://evil.example"})
            ).status_code == 403
            assert (await send(client, notice(time=None))).status_code == 422
            assert (await send(client, notice())).status_code == 204
            assert (await send(client, notice())).status_code == 204
            assert (await send(client, notice(user_id=999))).status_code == 204
            app.state.qq_core.sync()
            inbox = (await client.get("/api/inbox")).json()
            assert len(inbox) == 1
            assert (await client.get("/api/tasks")).json() == []
            # Mixed media is never silently treated as fully read.
            mixed = notice(
                message_id=2,
                message=[
                    {
                        "type": "text",
                        "data": {"text": "软件工程：请提交实验报告，具体要求见图片。"},
                    },
                    {"type": "image", "data": {"url": "http://evil.example/never-fetched"}},
                ],
            )
            assert (await send(client, mixed)).status_code == 204
            app.state.qq_core.sync()
            assert any(
                "图片或附件" in warning
                for item in (await client.get("/api/inbox")).json()
                for warning in item["warnings"]
            )
            body = b"x" * (256 * 1024 + 1)
            assert (
                await client.post("/api/connectors/qq/onebot/events", content=body)
            ).status_code == 413
            assert (await send(client, {"post_type": "meta_event"})).status_code == 204


def test_normalization_cq_plain_text_and_invalid_identity():
    normalized = normalize(notice(message="软件工程：交报告&#91;一&#93;[CQ:image,file=x]"))
    assert normalized.text == "软件工程：交报告[一]"
    assert normalized.media_types == ["image"]
    assert normalized.sent_at.tzinfo is not None
    assert normalize(notice(message=[{"type": "image", "data": {}}])) is None
    assert normalize(notice(message_type="private")) is None
    for update in ({"time": True}, {"message_id": {}}, {"message_id": "../abc"}, {"time": -1}):
        with pytest.raises((ValueError, ValidationError)):
            normalize(notice(**update))
    assert isinstance(normalized.sent_at, datetime)


async def test_bridge_api_does_not_return_secret_and_blank_preserves_it(tmp_path):
    bridge, store = setup_bridge(tmp_path)
    app = create_app(tmp_path / "test.db", qq_adapter=bridge, qq_background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            response = await client.put("/api/connectors/qq/onebot", json={"enabled": True})
            assert response.status_code == 200
            assert TOKEN not in response.text and SECRET not in response.text
            assert store.get("access") == TOKEN
            assert (await client.post("/api/connectors/qq/onebot/probe")).json()["state"][
                "connected"
            ]


async def test_invalid_credentials_not_echoed_and_shutdown_preserves_intent(tmp_path):
    bridge, _ = setup_bridge(tmp_path)
    app = create_app(tmp_path / "db.sqlite3", qq_adapter=bridge, qq_background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            bad = "private-invalid-secret!\n"
            response = await client.put("/api/connectors/qq/onebot", json={"access_token": bad})
            assert response.status_code == 422
            assert "private-invalid-secret" not in response.text
    assert OneBotAdapter(tmp_path, secrets=bridge.secrets).config.enabled


async def test_credential_store_errors_are_safe_and_can_be_reconfigured(tmp_path):
    class BrokenStore(Secrets):
        def get(self, key):
            raise RuntimeError(TOKEN)

    bridge = OneBotAdapter(tmp_path, secrets=BrokenStore())
    app = create_app(tmp_path / "test.db", qq_adapter=bridge, qq_background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            for method, path in (("GET", ""), ("POST", "/probe")):
                response = await client.request(method, "/api/connectors/qq/onebot" + path)
                # Return safe config metadata so the form remains usable for repair.
                assert response.status_code == 200
                assert response.json()["error"]
                assert not response.json()["token_saved"]
                assert TOKEN not in response.text
            response = await client.put(
                "/api/connectors/qq/onebot",
                json={"enabled": True, "access_token": TOKEN, "event_secret": SECRET},
            )
            assert response.status_code == 200
            assert response.json()["token_saved"]
            assert TOKEN not in response.text and SECRET not in response.text


async def test_failed_vault_write_is_not_reported_as_saved(tmp_path):
    class DeniedStore(Secrets):
        def set(self, key, value):
            raise RuntimeError(value)

    bridge = OneBotAdapter(tmp_path, secrets=DeniedStore())
    app = create_app(tmp_path / "test.db", qq_adapter=bridge, qq_background=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            response = await client.put(
                "/api/connectors/qq/onebot",
                json={"enabled": True, "access_token": TOKEN, "event_secret": SECRET},
            )
            assert response.status_code == 422
            assert TOKEN not in response.text and SECRET not in response.text
            assert not bridge.config.enabled
            assert not bridge.path.exists()
