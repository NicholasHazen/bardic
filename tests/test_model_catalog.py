"""No-network catalog tests: capabilities, credential isolation and bounded reads."""
from datetime import date
import json
import threading

import httpx
import pytest

from bardic import model_catalog as m


def fake_transport(monkeypatch, handler):
    original = httpx.Client
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request)

    def client(**options):
        assert options["follow_redirects"] is False
        assert options["timeout"].connect <= 5
        assert options["timeout"].read <= 12
        return original(transport=httpx.MockTransport(wrapped), **options)

    monkeypatch.setattr(m.httpx, "Client", client)
    return calls


@pytest.mark.parametrize("provider", m.ANALYSIS_CATALOG)
def test_offline_catalog_has_multiple_documented_choices_and_cheap_default(provider):
    inventory = m.catalog(provider)
    assert inventory["catalog_date"] == "2026-09-27"
    assert len(inventory["models"]) >= 3
    assert all(item["structured_output"] is True for item in inventory["models"])
    assert all(item["availability"] == "unverified" for item in inventory["models"])
    assert m.PREPROCESS_DEFAULTS[provider] in [item["id"] for item in inventory["models"]]
    inventory["models"][0]["id"] = "modified"
    assert m.catalog(provider)["models"][0]["id"] != "modified"


def test_prices_are_dated_and_unknown_or_expired_is_not_zero():
    assert m.model_price("openai", "gpt-6-luna")["input_usd_per_million"] == .1
    assert m.model_price("gemini", "gemini-3.5-flash-lite")["output_usd_per_million"] == 2.5
    assert m.model_price("anthropic", "claude-haiku-4-5-20251001")["output_usd_per_million"] == 5
    assert m.model_price("openai", "custom")["input_usd_per_million"] is None
    assert m.model_price("gemini", "gemini-3.1-pro-preview")["input_usd_per_million"] is None
    assert m.model_price("gemini", "gemini-3.8-flash", today=date(2026, 12, 31))["input_usd_per_million"] == .75
    assert m.model_price("gemini", "gemini-3.8-flash", today=date(2027, 1, 1))["input_usd_per_million"] is None
    assert m.model_price("openai", "gpt-6-luna", input_tokens=272001)["output_usd_per_million"] is None


def test_status_and_missing_keys_never_make_requests(monkeypatch):
    def unexpected(**kwargs):
        pytest.fail("catalog status must not call providers")

    monkeypatch.setattr(m.httpx, "Client", unexpected)
    inventory = m.ModelCatalog()
    assert inventory.view("gemini", "test-key")["state"] == "curated"
    assert inventory.refresh("openai", "")["state"] == "missing_key"
    assert inventory.refresh("anthropic", "new\nkey")["state"] == "invalid_key"
    with pytest.raises(ValueError):
        inventory.refresh("other", "key")


@pytest.mark.parametrize("provider,url,header,payload,listed", [
    ("openai", "https://api.openai.com/v1/models", "authorization", {"data":[
        {"id":"gpt-6-luna"}, {"id":"gpt-7-new"}, {"id":"gpt-audio"}, {"id":"gpt-6-codex"},
        {"id":"text-embedding-3-small"}, {"id":"https://bad.example"}, {"id":"gpt-6-luna"},
    ]}, {"gpt-6-luna", "gpt-7-new"}),
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000", "x-goog-api-key", {"models":[
        {"name":"models/gemini-3.5-flash-lite","supportedGenerationMethods":["generateContent"],"inputTokenLimit":1048576},
        {"name":"models/gemini-4-new","supportedGenerationMethods":["generateContent"]},
        {"name":"models/gemini-3.8-flash-tts","supportedGenerationMethods":["generateContent"]},
        {"name":"models/gemini-embedding","supportedGenerationMethods":["embedContent"]},
        {"name":"models/gemini-3.1-flash-image","supportedGenerationMethods":["generateContent"]},
    ]}, {"gemini-3.5-flash-lite", "gemini-4-new"}),
    ("anthropic", "https://api.anthropic.com/v1/models?limit=1000", "x-api-key", {"data":[
        {"id":"claude-sonnet-5","capabilities":{"structured_outputs":{"supported":True}},"max_input_tokens":1000000},
        {"id":"claude-new","max_input_tokens":True},
        {"id":"claude-no-json","capabilities":{"structured_outputs":{"supported":False}}},
    ], "has_more":False}, {"claude-sonnet-5", "claude-new"}),
])
def test_authenticated_inventory_has_fixed_get_endpoints_and_filters_modalities(monkeypatch, provider, url, header, payload, listed):
    calls = fake_transport(monkeypatch, lambda request: httpx.Response(200, json=payload))
    result = m.ModelCatalog().refresh(provider, "test-secret-key")
    assert result["state"] == "ready"
    assert len(calls) == 1 and calls[0].method == "GET" and str(calls[0].url) == url
    assert calls[0].content == b""
    assert "test-secret-key" in calls[0].headers[header]
    assert "test-secret-key" not in str(calls[0].url)
    assert {item["id"] for item in result["models"] if item["availability"] == "listed"} == listed
    unknown = next(item for item in result["models"] if item["tier"] == "other")
    assert unknown["structured_output"] is None
    assert unknown["input_usd_per_million"] is None
    assert "test-secret-key" not in json.dumps(result)
    assert len({item["id"] for item in result["models"]}) == len(result["models"])


def test_anthropic_live_capability_can_confirm_unknown_model(monkeypatch):
    fake_transport(monkeypatch, lambda request: httpx.Response(200, json={"data":[{
        "id":"claude-future", "capabilities":{"structured_outputs":{"supported":True}},
        "max_input_tokens":1000000, "max_tokens":65536, "display_name":"ignore freeform content",
    }]}))
    model = next(item for item in m.ModelCatalog().refresh("anthropic", "key")["models"] if item["id"] == "claude-future")
    assert model["structured_output"] is True and model["context_tokens"] == 1000000
    assert model["label"] == "claude-future" and model["input_usd_per_million"] is None


@pytest.mark.parametrize("provider,first,second,query", [
    ("gemini", {"models":[],"nextPageToken":"next"}, {"models":[]}, "pageToken"),
    ("anthropic", {"data":[],"has_more":True,"last_id":"next"}, {"data":[],"has_more":False}, "after_id"),
])
def test_pagination_uses_cursors_but_never_provider_urls(monkeypatch, provider, first, second, query):
    def handler(request):
        return httpx.Response(200, json=second if query in request.url.params else first)

    calls = fake_transport(monkeypatch, handler)
    result = m.ModelCatalog().refresh(provider, "key")
    assert len(calls) == 2 and calls[1].url.params[query] == "next"
    assert result["state"] == "ready" and not result["partial"]


def test_pagination_is_bounded_and_incomplete_list_does_not_claim_absence(monkeypatch):
    n = 0

    def handler(request):
        nonlocal n
        n += 1
        return httpx.Response(200, json={"models":[], "nextPageToken":str(n)})

    calls = fake_transport(monkeypatch, handler)
    result = m.ModelCatalog().refresh("gemini", "key")
    assert len(calls) == 3 and result["partial"]
    assert all(item["availability"] == "unverified" for item in result["models"])


def test_cache_reuses_same_credential_and_invalidates_rotated_key(monkeypatch):
    clock = [0.]
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    calls = fake_transport(monkeypatch, lambda request: httpx.Response(200, json={"data":[]}))
    inventory = m.ModelCatalog()
    first = inventory.refresh("openai", "key-one")
    clock[0] = 3599
    assert inventory.refresh("openai", "key-one")["cached"]
    assert len(calls) == 1
    assert inventory.view("openai", "key-two")["state"] == "curated"
    inventory.refresh("openai", "key-two")
    assert len(calls) == 2
    clock[0] = 7199
    inventory.refresh("openai", "key-two")
    assert len(calls) == 3
    first["models"].clear()
    assert inventory.view("openai", "key-two")["models"]
    assert "key-two" not in repr(inventory._cache)


@pytest.mark.parametrize("status,state", [(401,"invalid_key"),(403,"access_denied"),(429,"rate_limited"),(500,"unavailable"),(302,"unavailable")])
def test_errors_are_safe_no_retry_and_keep_offline_choices(monkeypatch, status, state):
    clock = [0.]
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    calls = fake_transport(monkeypatch, lambda request: httpx.Response(status, json={"error":"super-secret-key"}, headers={"location":"https://foreign.example"}))
    inventory = m.ModelCatalog()
    result = inventory.refresh("openai", "super-secret-key")
    assert result["state"] == state and len(result["models"]) >= 3
    assert len(calls) == 1 and "super-secret-key" not in json.dumps(result)
    clock[0] = 29
    assert inventory.refresh("openai", "super-secret-key")["cached"]
    assert len(calls) == 1
    clock[0] = 30
    inventory.refresh("openai", "super-secret-key")
    assert len(calls) == 2


@pytest.mark.parametrize("payload", [[], {}, {"data":{}}, {"data":"secret"}])
def test_malformed_payload_falls_back_without_echo(monkeypatch, payload):
    fake_transport(monkeypatch, lambda request: httpx.Response(200, json=payload))
    result = m.ModelCatalog().refresh("openai", "secret")
    assert result["state"] == "unavailable" and "secret" not in json.dumps(result)


def test_timeout_message_does_not_expose_key(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("secret-key", request=request)

    calls = fake_transport(monkeypatch, handler)
    result = m.ModelCatalog().refresh("openai", "secret-key")
    assert result["state"] == "unavailable" and len(calls) == 1
    assert "secret-key" not in json.dumps(result)


def test_concurrent_provider_refresh_does_not_duplicate_network(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def handler(request):
        entered.set()
        assert release.wait(2)
        return httpx.Response(200, json={"data":[]})

    calls = fake_transport(monkeypatch, handler)
    inventory = m.ModelCatalog()
    worker = threading.Thread(target=inventory.refresh, args=("openai","key"))
    worker.start()
    try:
        assert entered.wait(2)
        assert inventory.refresh("openai", "key")["state"] == "refreshing"
        assert len(calls) == 1
    finally:
        release.set()
        worker.join(2)
    assert inventory.view("openai", "key")["state"] == "ready"
