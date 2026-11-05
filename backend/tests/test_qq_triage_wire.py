"""Real provider request/response contracts with synthetic HTTP transports only."""

import json

import httpx
import pytest

from campus_inbox.inference import APIConfig, APIProvider
from campus_inbox.providers import OllamaProvider, ProviderError
from campus_inbox.schemas import LocalModelConfig


@pytest.mark.parametrize("kind", ["local", "api"])
@pytest.mark.parametrize("valid", [True, False])
def test_classification_and_fields_share_one_inference_request(kind, valid):
    calls, reserved = [], []
    reply = {"classification": "chat", "classification_quote": "这次考试好难", "title": None}
    if not valid:
        reply.pop("classification")

    def handle(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "local:test"}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"model_info": {"architecture": "synthetic"}})
        body = json.loads(request.content)
        calls.append(body)
        user = json.loads(body["messages"][-1]["content"])
        assert user["notification"] == "这次考试好难啊。"
        assert user["source_context"]["course"] == "软件工程"
        assert "group_id" not in user["source_context"]
        assert "sender_id" not in user["source_context"]
        assert "uncertain" in body["messages"][0]["content"]
        if kind == "local":
            assert "classification" in body["format"]["required"]
            assert body["format"]["properties"]["classification"]["enum"] == [
                "notification",
                "chat",
                "uncertain",
            ]
            return httpx.Response(200, json={"message": {"content": json.dumps(reply)}})
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(reply)}}]
            },
        )

    transport = httpx.MockTransport(handle)
    provider = (
        OllamaProvider(LocalModelConfig(enabled=True, model="local:test"), transport)
        if kind == "local"
        else APIProvider(
            APIConfig(
                enabled=True,
                model="api:test",
                base_url="https://synthetic.example/v1",
                consent=True,
            ),
            "synthetic-api-key",
            reserve=lambda: reserved.append(True),
            transport=transport,
        )
    )

    def classify():
        return provider.classify_contextual(
            "这次考试好难啊。",
            "2026-11-04T08:00:00Z",
            "Asia/Shanghai",
            {"course": "软件工程", "group_id": "200", "sender_id": "300"},
        )

    if valid:
        result = classify()
        assert result["classification"] == "chat"
        assert result["classification_quote"] == "这次考试好难"
    else:
        with pytest.raises(ProviderError):
            classify()
    assert len(calls) == 1
    assert len(reserved) == (1 if kind == "api" else 0)
