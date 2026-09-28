"""Account checks are explicit, scoped to one configuration, and session-only."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import json
import threading

import pytest
from fastapi.testclient import TestClient

from bardic.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("bardic.app.list_system_voices", lambda: [])
    with TestClient(create_app(tmp_path)) as client:
        yield client


def ready_result():
    return {"state": "ready", "message": "Account probe succeeded.", "http_status": 200,
            "usage": {"input_tokens": 4, "output_tokens": 1, "total_tokens": 5}}


def set_keys(client, **keys):
    response = client.post("/api/settings", json={"api_keys": keys})
    assert response.status_code == 200, response.text
    return response.json()


def test_status_reports_configuration_without_probing(client, monkeypatch):
    def unexpected_probe(*args):
        pytest.fail("Reading status must not make a provider request")

    monkeypatch.setattr("bardic.app.check_account", unexpected_probe)
    checks = client.get("/api/status").json()["account_checks"]
    assert set(checks) == {"gemini", "openai", "anthropic"}
    assert all(check["state"] == "missing_key" for check in checks.values())
    configured = set_keys(client, openai="test-openai-secret")["account_checks"]["openai"]
    assert configured["state"] == "unchecked"
    assert configured["checked_at"] is None
    assert configured["balance"] is None
    assert configured["usage"] is None
    assert configured["billing_url"].startswith("https://platform.openai.com/")
    assert configured["usage_url"].startswith("https://platform.openai.com/")
    assert client.get("/api/status").json()["account_checks"]["openai"] == configured


def test_checks_use_only_the_selected_provider_key_and_saved_model(client, monkeypatch):
    calls = []

    def probe(provider, key, model):
        calls.append((provider, key, model))
        return ready_result()

    monkeypatch.setattr("bardic.app.check_account", probe)
    keys = {provider: f"test-{provider}-secret" for provider in ("gemini", "openai", "anthropic")}
    models = {provider: f"custom-{provider}-model" for provider in keys}
    response = client.post("/api/settings", json={"api_keys": keys,
                           "analysis_models_by_provider": models, "analysis_provider": "local"})
    assert response.status_code == 200
    for provider in keys:
        response = client.post(f"/api/account-checks/{provider}")
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["provider"] == provider
        assert result["model"] == models[provider]
        assert result["state"] == "ready"
        assert result["checked_at"]
        assert result["cached"] is False
        assert result["balance"] is None, "A successful request is not a credit balance"
        assert result["usage"] == ready_result()["usage"]
        assert all(key not in response.text for key in keys.values())
    assert calls == [(provider, keys[provider], models[provider]) for provider in keys]
    status = client.get("/api/status")
    assert all(key not in status.text for key in keys.values())
    store = client.app.state.runtime.store
    with store.connect() as connection:
        persisted = "\n".join(connection.iterdump())
    assert all(key not in persisted for key in keys.values())
    assert "Account probe succeeded." not in persisted
    assert "account_checks" not in json.dumps(store.settings())


def test_missing_credentials_and_unsupported_provider(client, monkeypatch):
    calls = []

    def probe(provider, key, model):
        calls.append((provider, key, model))
        assert key == ""
        return {"state": "missing_key", "message": "Add an API key.", "usage": None, "http_status": None}

    monkeypatch.setattr("bardic.app.check_account", probe)
    unsupported = client.post("/api/account-checks/unknown")
    assert unsupported.status_code == 400
    assert calls == []
    response = client.post("/api/account-checks/anthropic")
    assert response.status_code == 200
    assert response.json()["state"] == "missing_key"
    assert response.json()["balance"] is None
    assert len(calls) == 1


def test_check_cache_expires_after_thirty_seconds(client, monkeypatch):
    calls = []
    clock = [100.0]
    monkeypatch.setattr("bardic.app.time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr("bardic.app.check_account", lambda *args: calls.append(args) or ready_result())
    set_keys(client, openai="test-openai-secret")
    first = client.post("/api/account-checks/openai").json()
    clock[0] = 129.9
    cached = client.post("/api/account-checks/openai").json()
    assert cached == {**first, "cached": True}
    assert len(calls) == 1
    clock[0] = 130.0
    refreshed = client.post("/api/account-checks/openai").json()
    assert refreshed["cached"] is False
    assert len(calls) == 2


def test_key_and_model_edits_invalidate_only_the_changed_provider(client, monkeypatch):
    calls = []
    monkeypatch.setattr("bardic.app.check_account", lambda *args: calls.append(args) or ready_result())
    set_keys(client, openai="first-openai-secret", anthropic="test-anthropic-secret")
    client.post("/api/account-checks/openai")
    client.post("/api/account-checks/anthropic")
    status = set_keys(client, openai="replacement-openai-secret")
    assert status["account_checks"]["openai"]["state"] == "unchecked"
    assert status["account_checks"]["anthropic"]["state"] == "ready"
    assert client.post("/api/account-checks/anthropic").json()["cached"] is True
    assert client.post("/api/account-checks/openai").json()["cached"] is False
    status = client.post("/api/settings", json={"analysis_models_by_provider": {"openai": "another-model"}}).json()
    assert status["account_checks"]["openai"]["state"] == "unchecked"
    assert client.post("/api/account-checks/openai").json()["cached"] is False
    assert calls[-1] == ("openai", "replacement-openai-secret", "another-model")
    assert len(calls) == 4


def test_only_one_check_per_provider_runs_but_other_providers_are_independent(client, monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []

    def probe(provider, key, model):
        calls.append(provider)
        if provider == "openai":
            started.set()
            assert release.wait(5), "test did not release the account check"
        return ready_result()

    monkeypatch.setattr("bardic.app.check_account", probe)
    set_keys(client, openai="test-openai-secret", anthropic="test-anthropic-secret")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, "/api/account-checks/openai")
        try:
            assert started.wait(3)
            assert client.get("/api/status").json()["account_checks"]["openai"]["state"] == "checking"
            assert client.post("/api/account-checks/openai").status_code == 409
            other = client.post("/api/account-checks/anthropic")
            assert other.status_code == 200
            assert other.json()["state"] == "ready"
        finally:
            release.set()
        assert pending.result(timeout=3).status_code == 200
    assert calls == ["openai", "anthropic"]
    assert client.get("/api/status").json()["account_checks"]["openai"]["state"] == "ready"


def test_in_flight_result_cannot_replace_a_changed_credentials_status(client, monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []

    def probe(provider, key, model):
        calls.append(key)
        if key == "old-openai-secret":
            started.set()
            assert release.wait(5), "test did not release the account check"
        return ready_result()

    monkeypatch.setattr("bardic.app.check_account", probe)
    set_keys(client, openai="old-openai-secret")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, "/api/account-checks/openai")
        try:
            assert started.wait(3)
            status = set_keys(client, openai="new-openai-secret")
            assert status["account_checks"]["openai"]["state"] == "unchecked"
        finally:
            release.set()
        response = pending.result(timeout=3)
    assert response.status_code == 409
    assert response.json()["code"] == "settings_changed"
    assert client.get("/api/status").json()["account_checks"]["openai"]["state"] == "unchecked"
    current = client.post("/api/account-checks/openai")
    assert current.status_code == 200
    assert current.json()["cached"] is False
    assert calls == ["old-openai-secret", "new-openai-secret"]


def test_unexpected_helper_failure_does_not_expose_credentials(client, monkeypatch):
    secret = "test-sensitive-openai-secret"
    set_keys(client, openai=secret)

    def broken_probe(*args):
        raise RuntimeError(f"Transport failure for Authorization: Bearer {secret}")

    monkeypatch.setattr("bardic.app.check_account", broken_probe)
    response = client.post("/api/account-checks/openai")
    assert response.status_code == 200
    assert response.json()["state"] == "provider_error"
    assert response.json()["balance"] is None
    assert response.json()["usage"] is None
    assert secret not in response.text
    assert secret not in client.get("/api/status").text
    assert client.post("/api/account-checks/openai").json()["cached"] is True


def test_account_check_rejects_cross_origin_requests_before_probing(client, monkeypatch):
    calls = []
    monkeypatch.setattr("bardic.app.check_account", lambda *args: calls.append(args) or ready_result())
    set_keys(client, openai="test-openai-secret")
    response = client.post("/api/account-checks/openai", headers={"Origin": "https://untrusted.example"})
    assert response.status_code == 403
    response = client.post("/api/account-checks/openai", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert calls == []
    response = client.post("/api/account-checks/openai", headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert len(calls) == 1
