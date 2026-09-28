"""Catalog settings do not generate text, leak credentials, or overwrite choices."""
import copy
import json
import threading

import pytest
from fastapi.testclient import TestClient

from bardic.app import create_app
from bardic.model_catalog import PREPROCESS_DEFAULTS, catalog


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("bardic.app.list_system_voices", lambda: [])


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as test_client:
        yield test_client


def test_status_contains_offline_models_and_role_defaults_without_network(client, monkeypatch):
    monkeypatch.setattr(client.app.state.runtime.model_catalog, "refresh", lambda *_: pytest.fail("No automatic refresh"))
    response = client.get("/api/status")
    status = response.json()
    assert status["preprocess_models_by_provider"] == PREPROCESS_DEFAULTS
    assert all(len(value["models"]) >= 3 for value in status["model_catalogs"].values())
    assert response.headers["cache-control"] == "no-store"


def test_custom_preprocessors_persist_without_keys_and_independent_of_analysis(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        before = client.get("/api/status").json()
        response = client.post("/api/settings", json={"preprocess_models_by_provider":{"openai":"my-cheap-model:latest"}, "api_keys":{"openai":"private-key"}})
        assert response.status_code == 200
        after = response.json()
        assert after["analysis_models_by_provider"] == before["analysis_models_by_provider"]
        assert after["preprocess_models_by_provider"]["openai"] == "my-cheap-model:latest"
        assert "private-key" not in json.dumps(client.app.state.runtime.store.settings())
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/status").json()["preprocess_models_by_provider"]["openai"] == "my-cheap-model:latest"
        assert client.app.state.runtime.api_keys["openai"] == ""


@pytest.mark.parametrize("bad", [{"unknown":"model"},{"openai":""},{"openai":"https://example.com/model"}])
def test_invalid_preprocessing_setting_cannot_partially_save_keys(client, bad):
    runtime = client.app.state.runtime
    before = copy.deepcopy(runtime.preferences)
    response = client.post("/api/settings", json={"api_keys":{"openai":"new-key"}, "preprocess_models_by_provider":bad})
    assert response.status_code == 400
    assert runtime.preferences == before and runtime.api_keys["openai"] == ""


def test_refresh_uses_only_selected_provider_key_and_no_book_or_model(client, monkeypatch):
    client.post("/api/settings", json={"api_keys":{"openai":"openai-key","anthropic":"anthropic-key"}})
    calls = []
    def refresh(provider, key):
        calls.append((provider,key))
        return {**catalog(provider), "state":"ready"}
    monkeypatch.setattr(client.app.state.runtime.model_catalog, "refresh", refresh)
    assert client.post("/api/models/openai/refresh").json()["state"] == "ready"
    assert calls == [("openai","openai-key")]
    assert client.post("/api/models/local/refresh").status_code == 400
    assert client.post("/api/models/openai/refresh", headers={"origin":"https://foreign.example"}).status_code == 403
    assert len(calls) == 1


def test_refresh_rejects_response_for_replaced_key(client, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    runtime = client.app.state.runtime
    client.post("/api/settings", json={"api_keys":{"openai":"old-key"}})
    def refresh(provider,key):
        entered.set()
        assert release.wait(2)
        return {**catalog(provider), "state":"ready"}
    monkeypatch.setattr(runtime.model_catalog, "refresh", refresh)
    results = []
    worker = threading.Thread(target=lambda:results.append(client.post("/api/models/openai/refresh")))
    worker.start()
    try:
        assert entered.wait(2)
        assert client.post("/api/settings", json={"api_keys":{"openai":"new-key"}}).status_code == 200
    finally:
        release.set()
        worker.join(2)
    assert results[0].status_code == 409
    assert "old-key" not in results[0].text


def test_unexpected_inventory_error_cannot_echo_secret(client, monkeypatch):
    def refresh(*args):
        raise RuntimeError("transport echoed private-key")
    client.post("/api/settings", json={"api_keys":{"gemini":"private-key"}})
    monkeypatch.setattr(client.app.state.runtime.model_catalog, "refresh", refresh)
    response = client.post("/api/models/gemini/refresh")
    assert response.status_code == 200 and response.json()["state"] == "unavailable"
    assert "private-key" not in response.text
