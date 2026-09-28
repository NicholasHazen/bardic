import json

import httpx
import pytest

from spintails import account_checks as checks


RESPONSES = {
    "openai": {"status": "completed", "output": [{"type": "reasoning"}, {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "OK"}]}], "usage": {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10}},
    "anthropic": {"type": "message", "stop_reason": "end_turn", "content": [{"type": "text", "text": "OK"}], "usage": {"input_tokens": 8, "output_tokens": 2}},
    "gemini": {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "OK"}]}}], "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 2, "totalTokenCount": 10}},
}


def mock_provider(monkeypatch, payload=None, status=200, error=None, text=None):
    requests, options = [], []
    real_client = httpx.Client

    def handler(request):
        requests.append(request)
        if error:
            raise error
        return httpx.Response(status, text=text) if text is not None else httpx.Response(status, json=payload)

    def client(**kwargs):
        options.append(kwargs)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(checks.httpx, "Client", client)
    return requests, options


@pytest.mark.parametrize("provider,host,path,header", [
    ("openai", "api.openai.com", "/v1/responses", "authorization"),
    ("anthropic", "api.anthropic.com", "/v1/messages", "x-api-key"),
    ("gemini", "generativelanguage.googleapis.com", "/v1beta/models/test-model:generateContent", "x-goog-api-key"),
])
def test_small_request_uses_only_selected_provider_and_normalizes_usage(monkeypatch, provider, host, path, header):
    requests, options = mock_provider(monkeypatch, RESPONSES[provider])
    result = checks.check_account(provider, "test-key", "test-model")
    assert result["state"] == "ready"
    assert result["usage"] == {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10}
    assert result["http_status"] == 200
    assert len(requests) == 1
    request = requests[0]
    assert request.url.scheme == "https" and request.url.host == host and request.url.path == path
    assert request.method == "POST" and not request.url.query
    assert request.headers[header] == ("Bearer test-key" if provider == "openai" else "test-key")
    assert sum(name in request.headers for name in ("authorization", "x-api-key", "x-goog-api-key")) == 1
    body = json.loads(request.content)
    assert "test-key" not in str(body)
    assert checks.CHECK_PROMPT in str(body)
    assert "tools" not in body and "stream" not in body
    if provider == "openai":
        assert body["store"] is False and body["max_output_tokens"] == 128
    elif provider == "anthropic":
        assert body["max_tokens"] == 128 and request.headers["anthropic-version"] == "2023-06-01"
    else:
        assert body["generationConfig"]["maxOutputTokens"] == 128
    assert options[0]["follow_redirects"] is False
    assert options[0]["timeout"].read == 20


@pytest.mark.parametrize("provider,status,error,state", [
    ("openai", 429, {"code": "credit_balance_exhausted", "type": "insufficient_quota"}, "billing_blocked"),
    ("openai", 429, {"code": "organization_spend_limit_exceeded"}, "billing_blocked"),
    ("openai", 429, {"code": "project_spend_limit_exceeded"}, "billing_blocked"),
    ("openai", 429, {"code": "organization_usage_limit_exceeded"}, "billing_blocked"),
    ("openai", 429, {"code": "insufficient_quota"}, "billing_blocked"),
    ("openai", 429, {"type": "rate_limit_error", "code": "slow_down"}, "rate_limited"),
    ("openai", 401, {"message": "Incorrect API key. Check billing"}, "invalid_key"),
    ("openai", 401, {"message": "IP not authorized"}, "access_denied"),
    ("openai", 403, {"message": "Country not supported"}, "access_denied"),
    ("openai", 404, {"code": "model_not_found"}, "model_unavailable"),
    ("anthropic", 400, {"type": "invalid_request_error", "message": "Your credit balance is too low to access the API"}, "billing_blocked"),
    ("anthropic", 400, {"type": "invalid_request_error", "message": "You have reached your workspace spend limit"}, "billing_blocked"),
    ("anthropic", 402, {"type": "billing_error"}, "billing_blocked"),
    ("anthropic", 401, {"type": "authentication_error"}, "invalid_key"),
    ("anthropic", 403, {"type": "permission_error"}, "access_denied"),
    ("anthropic", 429, {"type": "rate_limit_error"}, "rate_limited"),
    ("anthropic", 400, {"type": "invalid_request_error", "message": "max_tokens has an unsupported value"}, "provider_error"),
    ("gemini", 400, {"message": "API key not valid. Please pass a valid API key."}, "invalid_key"),
    ("gemini", 403, {"details": [{"reason": "API_KEY_INVALID"}]}, "invalid_key"),
    ("gemini", 403, {"details": [{"reason": "BILLING_DISABLED"}]}, "billing_blocked"),
    ("gemini", 402, {"status": "RESOURCE_EXHAUSTED"}, "billing_blocked"),
    ("gemini", 429, {"status": "RESOURCE_EXHAUSTED", "message": "Your prepayment credits are depleted."}, "billing_blocked"),
    ("gemini", 429, {"status": "RESOURCE_EXHAUSTED", "message": "You exceeded your current quota, check plan and billing details"}, "rate_limited"),
    ("gemini", 403, {"details": [{"reason": "API_KEY_SERVICE_BLOCKED"}]}, "access_denied"),
    ("gemini", 404, {"status": "NOT_FOUND"}, "model_unavailable"),
    ("gemini", 503, {"status": "UNAVAILABLE"}, "provider_error"),
])
def test_classifies_errors_without_retry(monkeypatch, provider, status, error, state):
    requests, _ = mock_provider(monkeypatch, {"error": error}, status)
    result = checks.check_account(provider, "test-key", "test-model")
    assert result["state"] == state
    assert result["http_status"] == status and result["usage"] is None
    assert len(requests) == 1
    if provider == "gemini" and state == "rate_limited":
        assert "credits are unknown" in result["message"]


@pytest.mark.parametrize("provider,payload", [
    ("openai", {"status": "incomplete", "output": RESPONSES["openai"]["output"]}),
    ("openai", {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "No"}]}]}),
    ("openai", {"status": "completed", "output": [{"type": "message", "role": "assistant", "status": "incomplete", "content": [{"type": "output_text", "text": "OK"}]}]}),
    ("openai", {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "No"}, {"type": "output_text", "text": "OK"}]}]}),
    ("openai", {"status": "completed", "output": [{"type": "message", "role": "user", "content": [{"type": "output_text", "text": "OK"}]}]}),
    ("openai", {"status": "completed", "output": None}),
    ("anthropic", {"type": "message", "stop_reason": "max_tokens", "content": [{"type": "text", "text": "O"}]}),
    ("anthropic", {"type": "message", "stop_reason": "refusal", "content": [{"type": "text", "text": "No"}]}),
    ("anthropic", {"type": "message", "stop_reason": "end_turn", "content": "OK"}),
    ("anthropic", {"type": "message", "role": "user", "stop_reason": "end_turn", "content": [{"type": "text", "text": "OK"}]}),
    ("gemini", {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "O"}]}}]}),
    ("gemini", {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"thought": True, "text": "thinking"}]}}]}),
    ("gemini", {"promptFeedback": {"blockReason": "SAFETY"}, **RESPONSES["gemini"]}),
    ("gemini", {"candidates": [{"finishReason": "STOP", "content": None}]}),
    ("gemini", {"candidates": [None]}),
    ("gemini", {"candidates": None}),
    ("gemini", []),
    ("openai", None),
])
def test_never_reports_malformed_or_incomplete_generation_as_ready(monkeypatch, provider, payload):
    requests, _ = mock_provider(monkeypatch, payload)
    result = checks.check_account(provider, "test-key", "test-model")
    assert result["state"] == "inconclusive"
    assert len(requests) == 1


def test_provider_errors_do_not_echo_keys_or_arbitrary_details(monkeypatch):
    key = "private-key-used-for-check"
    mock_provider(monkeypatch, {"error": {"message": f"Bad request: {key} https://other.example/?key={key} " + "x" * 5000}}, 400)
    result = checks.check_account("openai", key, "test-model")
    assert key not in str(result) and "https:" not in str(result)
    assert len(result["message"]) <= 250


@pytest.mark.parametrize("error", [httpx.ConnectError("secret-key"), httpx.ReadTimeout("secret-key")])
def test_network_error_is_safe_and_not_retried(monkeypatch, error):
    requests, _ = mock_provider(monkeypatch, error=error)
    result = checks.check_account("openai", "secret-key", "test-model")
    assert result["state"] == "network_error" and result["http_status"] is None
    assert "secret-key" not in str(result) and len(requests) == 1


@pytest.mark.parametrize("status,state", [(200, "inconclusive"), (302, "provider_error"), (500, "provider_error")])
def test_non_json_response_is_safe(monkeypatch, status, state):
    requests, _ = mock_provider(monkeypatch, status=status, text="<h1>secret-key</h1>")
    result = checks.check_account("anthropic", "secret-key", "test-model")
    assert result["state"] == state and "secret-key" not in str(result)
    assert len(requests) == 1


@pytest.mark.parametrize("key,state", [("", "missing_key"), ("   ", "missing_key"), ("wrong\nkey", "invalid_key"), ("keyé", "invalid_key")])
def test_missing_or_malformed_key_does_not_send_a_request(monkeypatch, key, state):
    requests, _ = mock_provider(monkeypatch)
    assert checks.check_account("gemini", key, "test-model")["state"] == state
    assert not requests


@pytest.mark.parametrize("provider,model", [("evil", "test-model"), ("gemini", "https://evil.example"), ("openai", "x\ny")])
def test_invalid_provider_and_model_cannot_change_destination(monkeypatch, provider, model):
    requests, _ = mock_provider(monkeypatch)
    with pytest.raises(ValueError):
        checks.check_account(provider, "test-key", model)
    assert not requests


def test_malformed_usage_does_not_invent_spend_or_balance(monkeypatch):
    mock_provider(monkeypatch, {**RESPONSES["openai"], "usage": {"input_tokens": True, "output_tokens": -3, "total_tokens": "20", "cost": 50}})
    result = checks.check_account("openai", "test-key", "test-model")
    assert result["state"] == "ready" and result["usage"] is None
    assert "balance" not in result and "cost" not in result


def test_incomplete_probe_still_reports_its_actual_token_usage(monkeypatch):
    mock_provider(monkeypatch, {**RESPONSES["openai"], "status": "incomplete"})
    result = checks.check_account("openai", "test-key", "test-model")
    assert result["state"] == "inconclusive"
    assert result["usage"]["total_tokens"] == 10
