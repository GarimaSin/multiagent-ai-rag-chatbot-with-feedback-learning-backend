import json

import httpx
import pytest

from app.config import Settings
from app.errors import ProviderError
from app.provider import OpenAIProvider


def settings():
    return Settings(_env_file=None, app_env="test", provider="openai", openai_api_key="test-provider-key-not-real")


def sse(*events):
    return "".join("data: " + json.dumps(event) + "\n\n" for event in events)


async def collect(provider):
    return "".join([token async for token in provider.stream("Question", [], [], "general")])


async def test_responses_stream_contract():
    async def handler(request):
        assert request.url.path == "/v1/responses"
        payload = json.loads(request.content)
        assert payload["stream"] is True and payload["store"] is False
        assert payload["model"] == "gpt-4.1-mini"
        assert request.headers["authorization"].startswith("Bearer ")
        return httpx.Response(200, text=sse({"type": "response.output_text.delta", "delta": "Hello"}, {"type": "response.completed"}), headers={"content-type": "text/event-stream"})
    provider = OpenAIProvider(settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await collect(provider) == "Hello"
    await provider.close()


async def test_retry_before_first_token():
    calls = []
    async def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"message": "rate limit"}})
        return httpx.Response(200, text=sse({"type": "response.output_text.delta", "delta": "OK"}, {"type": "response.completed"}))
    provider = OpenAIProvider(settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await collect(provider) == "OK" and len(calls) == 2
    await provider.close()


async def test_no_retry_after_output_has_started():
    calls = []
    async def handler(request):
        calls.append(1)
        return httpx.Response(200, text=sse({"type": "response.output_text.delta", "delta": "Partial"}, {"type": "response.failed"}))
    provider = OpenAIProvider(settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderError):
        await collect(provider)
    assert len(calls) == 1
    await provider.close()


async def test_eof_without_completed_event_is_error():
    provider = OpenAIProvider(settings(), httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=sse({"type": "response.output_text.delta", "delta": "Partial"})))))
    with pytest.raises(ProviderError) as error:
        await collect(provider)
    assert error.value.code == "incomplete_response"
    await provider.close()


async def test_authentication_error_not_retried():
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(401, json={"error": {"message": "do not expose raw provider errors"}})
    provider = OpenAIProvider(settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderError) as error:
        await collect(provider)
    assert error.value.code == "provider_authentication" and len(calls) == 1
    await provider.close()


async def test_fallback_uses_configured_model():
    calls = []
    def handler(request):
        calls.append(json.loads(request.content)["model"])
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, text=sse({"type": "response.output_text.delta", "delta": "fallback"}, {"type": "response.completed"}))
    config = settings(); config.fallback_model = "example-fallback"
    provider = OpenAIProvider(config, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await collect(provider) == "fallback"
    assert calls == ["gpt-4.1-mini", "example-fallback"]
    await provider.close()


async def test_embeddings_count_dimension_and_redaction():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["dimensions"] == 1536
        assert "person@example.com" not in str(payload)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0]*1536}]})
    provider = OpenAIProvider(settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert len((await provider.embed(["person@example.com"]))[0]) == 1536
    await provider.close()


@pytest.mark.parametrize("data", [[], [{"index": 0, "embedding": [0.1]}], [{"index": 1, "embedding": [0.0]*1536}]])
async def test_invalid_embeddings_fail_closed(data):
    provider = OpenAIProvider(settings(), httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": data}))))
    with pytest.raises(ProviderError) as error:
        await provider.embed(["a query"])
    assert error.value.code == "invalid_embeddings"
    await provider.close()


async def test_structured_verifier_contract():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["text"]["format"]["type"] == "json_schema"
        assert payload["text"]["format"]["strict"] is True
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"supported":true,"reason":"matches"}'}]}]})
    provider = OpenAIProvider(settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert (await provider.verify("Q", "A", ["A"]))["supported"] is True
    await provider.close()


@pytest.mark.parametrize("verdict", ['{"supported":"false","reason":"bad type"}', '{}', '{"supported":true,"reason":"ok","extra":"bad"}'])
async def test_invalid_verdict_does_not_approve(verdict):
    provider = OpenAIProvider(settings(), httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": verdict}]}]}))))
    with pytest.raises(ProviderError) as error:
        await provider.verify("Q", "A", ["A"])
    assert error.value.code == "invalid_verdict"
    await provider.close()
