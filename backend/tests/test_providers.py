import json

import httpx
import pytest

from campus_inbox.providers import OllamaProvider, ProviderError
from campus_inbox.schemas import LocalModelConfig


def provider(handler):
    return OllamaProvider(LocalModelConfig(model="local:4b"), httpx.MockTransport(handler))


def test_structured_schema_sent_and_json_validated():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "local:4b"}]})
        if request.url.path == "/api/show":
            return httpx.Response(
                200,
                json={
                    "model_info": {"general.architecture": "qwen3"},
                    "capabilities": ["completion", "thinking"],
                },
            )
        body = json.loads(request.content)
        assert body["format"]["type"] == "object"
        assert body["stream"] is False
        assert body["think"] is False
        assert body["messages"][0]["role"] == "system"
        return httpx.Response(200, json={"message": {"content": '{"title":"报告"}'}})

    assert (
        provider(handler).generate_structured("请提交报告", "2026-10-29", "Asia/Shanghai")["title"]
        == "报告"
    )
    assert calls == ["/api/tags", "/api/show", "/api/chat"]


def test_remote_model_is_never_sent_a_notification():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": "local:4b", "remote_host": "https://ollama.com"}]}
            )
        pytest.fail("Remote model must not receive /api/chat")

    with pytest.raises(ProviderError):
        provider(handler).generate_structured("私人通知", "2026-10-29", "Asia/Shanghai")
    assert calls == ["/api/tags"]


def test_remote_alias_is_rejected_by_show_metadata():
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "local:4b"}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"remote_model": "upstream", "model_info": {"x": 1}})
        pytest.fail("Remote alias must not receive /api/chat")

    with pytest.raises(ProviderError):
        provider(handler).generate_structured("私人通知", "2026-10-29", "Asia/Shanghai")


def test_invalid_json_and_transport_failure_are_recoverable():
    def malformed(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "local:4b"}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"model_info": {"x": 1}})
        return httpx.Response(200, json={"message": {"content": "not json"}})

    with pytest.raises(ProviderError, match="结构化"):
        provider(malformed).generate_structured("通知", "2026-10-29", "Asia/Shanghai")

    def timeout(request):
        raise httpx.ReadTimeout("timeout", request=request)

    assert not provider(timeout).health_check()


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        ({"done_reason": "length", "message": {"content": "", "thinking": "internal"}}, "长度上限"),
        ({"done_reason": "length", "message": {"content": '{"title":null}'}}, "长度上限"),
        ({"done_reason": "stop", "message": {"content": "", "thinking": "internal"}}, "思考内容"),
    ],
)
def test_truncated_or_thinking_only_response_is_not_accepted(reply, error):
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "local:4b"}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"model_info": {"x": 1}})
        assert "think" not in json.loads(request.content)
        return httpx.Response(200, json=reply)

    with pytest.raises(ProviderError, match=error):
        provider(handler).generate_structured("通知", "2026-10-30", "Asia/Shanghai")


def test_context_is_separate_from_notification_and_excludes_identifiers():
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "local:4b"}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"model_info": {"x": 1}})
        body = json.loads(request.content)
        data = json.loads(body["messages"][-1]["content"])
        assert data["notification"] == "明天交报告"
        assert data["source_context"]["group_name"] == "2026年软件工程三班"
        assert data["source_context"]["sender_name"] == "老师"
        assert "group_id" not in data["source_context"]
        assert data["sent_at"] == "2026-11-02T12:00:00+00:00"
        return httpx.Response(200, json={"message": {"content": '{"title":"报告"}'}})

    provider(handler).generate_contextual(
        "明天交报告",
        "2026-11-02T12:00:00+00:00",
        "Asia/Shanghai",
        {
            "group_name": "2026年软件工程三班",
            "group_id": "12345",
            "sender_name": "老师",
        },
    )
