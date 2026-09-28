"""Provider contracts are checked offline; tests never need real credentials."""
import json

import httpx
import pytest

from bardic import analysis


ADAPTERS = {"gemini": analysis._request, "openai": analysis._openai_request, "anthropic": analysis._anthropic_request}
ENDPOINTS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent",
    "openai": "https://api.openai.com/v1/responses",
    "anthropic": "https://api.anthropic.com/v1/messages",
}


def envelope(provider, text='{"characters": []}'):
    if provider == "gemini":
        return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"thought": True, "text": "not JSON"}, {"text": text}]}}]}
    if provider == "openai":
        return {"status": "completed", "output": [{"type": "reasoning", "summary": []}, {"type": "message", "status": "completed", "content": [{"type": "output_text", "text": text}]}]}
    return {"stop_reason": "end_turn", "content": [{"type": "thinking", "thinking": "not JSON"}, {"type": "text", "text": text}]}


def request(provider, handler, cancelled=lambda: False):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return ADAPTERS[provider](client, "test-model", "private-test-key", "Source text", analysis.CAST_SCHEMA, cancelled)


@pytest.mark.parametrize("provider", ADAPTERS)
def test_only_selected_provider_receives_its_credentials_and_structured_request(provider):
    captured = []

    def handler(req):
        captured.append(req)
        return httpx.Response(200, json=envelope(provider))

    assert request(provider, handler) == {"characters": []}
    assert len(captured) == 1
    req = captured[0]
    assert str(req.url) == ENDPOINTS[provider]
    body = json.loads(req.content)
    assert "tools" not in body
    assert "private-test-key" not in req.content.decode()
    if provider == "gemini":
        assert req.headers["x-goog-api-key"] == "private-test-key"
        assert "authorization" not in req.headers and "x-api-key" not in req.headers
        assert body["generationConfig"]["responseJsonSchema"] == analysis.CAST_SCHEMA
    elif provider == "openai":
        assert req.headers["authorization"] == "Bearer private-test-key"
        assert "x-api-key" not in req.headers and "x-goog-api-key" not in req.headers
        assert body["text"]["format"] == {"type": "json_schema", "name": "audiobook_analysis", "strict": True, "schema": analysis.CAST_SCHEMA}
        assert body["store"] is False
        assert body["input"] == [{"role": "user", "content": "Source text"}]
    else:
        assert req.headers["x-api-key"] == "private-test-key"
        assert req.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in req.headers and "x-goog-api-key" not in req.headers
        assert body["output_config"]["format"] == {"type": "json_schema", "schema": analysis.CAST_SCHEMA}
        assert body["messages"] == [{"role": "user", "content": "Source text"}]


@pytest.mark.parametrize("provider,payload,match", [
    ("gemini", {"candidates": [{"finishReason": "MAX_TOKENS"}]}, "complete analysis"),
    ("openai", {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}, "complete analysis"),
    ("openai", {"status": "failed", "error": {"message": "private-test-key"}}, "complete analysis"),
    ("openai", {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "private-test-key"}]}]}, "declined"),
    ("anthropic", {"stop_reason": "refusal", "content": [{"type": "text", "text": "private-test-key"}]}, "declined"),
    ("anthropic", {"stop_reason": "max_tokens", "content": []}, "complete analysis"),
    ("anthropic", {"stop_reason": "tool_use", "content": []}, "complete analysis"),
])
def test_incomplete_and_refused_responses_are_errors_not_partial_analysis(provider, payload, match):
    with pytest.raises(ValueError, match=match) as exc:
        request(provider, lambda _: httpx.Response(200, json=payload))
    assert "private-test-key" not in str(exc.value)


@pytest.mark.parametrize("provider", ADAPTERS)
@pytest.mark.parametrize("text", ['{"characters":', '[]', '', 'null'])
def test_invalid_json_and_nonobject_results_are_rejected(provider, text):
    with pytest.raises(ValueError, match="invalid analysis JSON"):
        request(provider, lambda _: httpx.Response(200, json=envelope(provider, text)))


@pytest.mark.parametrize("provider", ADAPTERS)
def test_malformed_provider_envelope_is_a_readable_error(provider):
    payload = {"status": "completed", "output": [None], "stop_reason": "end_turn", "content": [None], "candidates": [None]}
    with pytest.raises(ValueError, match="invalid analysis response"):
        request(provider, lambda _: httpx.Response(200, json=payload))


@pytest.mark.parametrize("provider", ADAPTERS)
def test_transient_errors_retry_same_endpoint_and_redact_key_on_final_error(provider, monkeypatch):
    monkeypatch.setattr(analysis.time, "sleep", lambda _: None)
    urls = []

    def handler(req):
        urls.append(str(req.url))
        return httpx.Response(429, json={"error": {"message": "Invalid private-test-key"}})

    with pytest.raises(ValueError, match="HTTP 429") as exc:
        request(provider, handler)
    assert "private-test-key" not in str(exc.value)
    assert "[redacted]" in str(exc.value)
    assert urls == [ENDPOINTS[provider]] * 3


@pytest.mark.parametrize("provider", ADAPTERS)
def test_cancellation_after_request_does_not_retry_or_consume_result(provider):
    captured = []

    def handler(req):
        captured.append(req)
        return httpx.Response(429, json={"error": {"message": "Try again"}})

    with pytest.raises(analysis.AnalysisCancelled):
        request(provider, handler, cancelled=lambda: bool(captured))
    assert len(captured) == 1


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_transport_failure_is_reported_without_the_key(provider, monkeypatch):
    def offline(req):
        raise httpx.ConnectError("private-test-key", request=req)

    monkeypatch.setattr(analysis.time, "sleep", lambda _: None)
    with pytest.raises(ValueError, match="could not connect") as exc:
        request(provider, offline)
    assert "private-test-key" not in str(exc.value)
