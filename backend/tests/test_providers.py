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
            return httpx.Response(200, json={"model_info": {"general.architecture": "qwen"}})
        body = json.loads(request.content)
        assert body["format"]["type"] == "object"
        assert body["stream"] is False
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
