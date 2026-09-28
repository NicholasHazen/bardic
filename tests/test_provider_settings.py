"""Provider selection, credential isolation, migration, and queued-job settings."""
import copy
import json
import threading
import time
import wave

import pytest
from fastapi.testclient import TestClient

from bardic.app import TTS_MODELS, create_app
from bardic.audio import render_fingerprint, voice_id
from bardic.store import Store


KEYS = {provider: f"test-{provider}-credential" for provider in ("gemini", "openai", "anthropic")}
MODELS = {provider: f"custom-{provider}-model:latest" for provider in KEYS}


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for variable in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr("bardic.app.list_system_voices", lambda: [])


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as test_client:
        yield test_client


def import_book(client):
    response = client.post("/api/books", files={"file": ("story.txt", b'The lamp glowed.\n\n"Hello," Mara said.\n', "text/plain")})
    assert response.status_code == 200, response.text
    return response.json()


def wait_job(client, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = next(item for item in client.get("/api/jobs").json() if item["id"] == job_id)
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(.01)
    pytest.fail("analysis worker did not finish")


ADAPTERS = {"gemini": "_request", "openai": "_openai_request", "anthropic": "_anthropic_request"}


def record_analysis(monkeypatch, fail=None):
    """Replace every cloud adapter; a discovery request that finds nobody is a valid result."""
    calls = []

    def adapter(provider):
        def request(_client, model, key, prompt, schema, cancelled):
            calls.append((provider, key, model))
            if fail:
                fail(key)
            return {"characters": []}
        return request

    for provider, name in ADAPTERS.items():
        monkeypatch.setattr(f"bardic.analysis.{name}", adapter(provider))
    return calls


def start_discovery(client, book_id, **body):
    """Start a Discovery run the way the Analyze tab does: preview, then confirm its fingerprint."""
    preview = client.post(f"/api/books/{book_id}/analysis-pipeline/plan", json={"steps": ["discovery"], **body})
    assert preview.status_code == 200, preview.text
    return client.post(f"/api/books/{book_id}/analysis-pipeline/runs",
                       json={"steps": ["discovery"], "expected_fingerprint": preview.json()["fingerprint"], **body})


def settings(provider, keys=KEYS):
    return {"analysis_provider": provider, "api_keys": keys, "analysis_models_by_provider": MODELS,
            "preprocess_models_by_provider": MODELS}


@pytest.mark.parametrize("provider", ["gemini", "openai", "anthropic"])
def test_saved_analysis_provider_routes_only_its_own_key_and_model(client, monkeypatch, provider):
    calls = record_analysis(monkeypatch)
    saved = client.post("/api/settings", json=settings(provider))
    assert saved.status_code == 200, saved.text
    book = import_book(client)
    response = start_discovery(client, book["id"])
    assert response.status_code == 200, response.text
    job = wait_job(client, response.json()["job"]["id"])
    assert job["status"] == "completed", job
    assert calls and set(calls) == {(provider, KEYS[provider], MODELS[provider])}
    assert not any(key in json.dumps(job) for key in KEYS.values())
    assert not any(key in json.dumps(response.json()) for key in KEYS.values())


def test_explicit_step_config_overrides_saved_choice_without_changing_it(client, monkeypatch):
    calls = record_analysis(monkeypatch)
    client.post("/api/settings", json=settings("openai"))
    book = import_book(client)
    configs = {"discovery": {"provider": "anthropic", "model": MODELS["anthropic"]}}
    response = start_discovery(client, book["id"], configs=configs)
    assert response.status_code == 200, response.text
    assert wait_job(client, response.json()["job"]["id"])["status"] == "completed"
    assert calls and set(calls) == {("anthropic", KEYS["anthropic"], MODELS["anthropic"])}
    assert client.get("/api/status").json()["analysis_provider"] == "openai"


@pytest.mark.parametrize("provider,label", [("gemini", "Gemini"), ("openai", "OpenAI"), ("anthropic", "Anthropic")])
def test_missing_selected_key_never_falls_back_to_another_provider(client, monkeypatch, provider, label):
    calls = record_analysis(monkeypatch)
    other_keys = {name: key for name, key in KEYS.items() if name != provider}
    client.post("/api/settings", json=settings(provider, other_keys))
    book = import_book(client)
    plan = client.post(f"/api/books/{book['id']}/analysis-pipeline/plan", json={"steps": ["discovery"]}).json()
    assert plan["steps"][0]["provider"] == provider
    response = start_discovery(client, book["id"])
    assert response.status_code == 400
    assert label in response.json()["detail"]
    assert calls == []
    assert client.get("/api/jobs").json() == []


def test_provider_settings_persist_but_credentials_do_not(tmp_path):
    with TestClient(create_app(tmp_path)) as first:
        response = first.post("/api/settings", json={
            "api_keys": KEYS, "analysis_models_by_provider": MODELS, "analysis_provider": "anthropic",
        })
        assert response.status_code == 200, response.text
        status = response.json()
        assert all(provider["available"] for provider in status["analysis_providers"])
        assert not any(key in response.text for key in KEYS.values())
        persisted = first.app.state.runtime.store.settings()
        assert persisted["analysis_models_by_provider"] == MODELS
        assert not any(key in json.dumps(persisted) for key in KEYS.values())
        assert not any(key.encode() in file.read_bytes() for key in KEYS.values() for file in tmp_path.glob("library.sqlite3*"))
    with TestClient(create_app(tmp_path)) as second:
        status = second.get("/api/status").json()
        assert status["analysis_provider"] == "anthropic"
        assert status["analysis_models_by_provider"] == MODELS
        assert status["analysis_model"] == MODELS["gemini"]
        assert not status["has_api_key"]
        cloud = [provider for provider in status["analysis_providers"] if provider["id"] != "local"]
        assert all(not provider["available"] and not provider["has_api_key"] for provider in cloud)


def test_legacy_gemini_preferences_and_fields_remain_compatible(tmp_path):
    Store(tmp_path).save_settings({"analysis_model": "gemini-legacy-custom", "tts_model": TTS_MODELS[-1]})
    with TestClient(create_app(tmp_path)) as client:
        status = client.get("/api/status").json()
        assert status["analysis_model"] == "gemini-legacy-custom"
        assert status["analysis_models_by_provider"]["gemini"] == "gemini-legacy-custom"
        assert status["tts_model"] == TTS_MODELS[-1]
        response = client.post("/api/settings", json={"api_key": KEYS["gemini"], "analysis_model": "gemini-new-custom"})
        assert response.status_code == 200, response.text
        assert response.json()["has_api_key"]
        assert response.json()["analysis_models_by_provider"]["gemini"] == "gemini-new-custom"
        runtime = client.app.state.runtime
        assert runtime.api_key == KEYS["gemini"]
        assert runtime.api_keys["gemini"] == KEYS["gemini"]


def test_partial_settings_and_clearing_one_key_preserve_other_providers(client):
    client.post("/api/settings", json={"api_keys": KEYS, "analysis_models_by_provider": MODELS, "analysis_provider": "anthropic"})
    response = client.post("/api/settings", json={"api_keys": {"openai": ""}, "analysis_models_by_provider": {"anthropic": "new-model-2026"}})
    assert response.status_code == 200, response.text
    status = response.json()
    availability = {item["id"]: item["available"] for item in status["analysis_providers"]}
    assert availability == {"local": True, "gemini": True, "openai": False, "anthropic": True}
    assert status["analysis_models_by_provider"] == {**MODELS, "anthropic": "new-model-2026"}
    assert status["analysis_provider"] == "anthropic"
    assert client.app.state.runtime.api_keys == {**KEYS, "openai": ""}


@pytest.mark.parametrize("invalid", [
    {"api_keys": {"unknown": "key"}},
    {"analysis_models_by_provider": {"unknown": "model"}},
    {"analysis_provider": "unknown"},
    {"analysis_models_by_provider": {"openai": "https://foreign.example/model"}},
    {"analysis_models_by_provider": {"openai": ""}},
    {"analysis_models_by_provider": {"openai": "model\nInjected"}},
    {"tts_model": "unsupported"},
    {"api_key": "conflicting-legacy-key"},
    {"analysis_model": "conflicting-legacy-model"},
])
def test_invalid_mixed_settings_do_not_partially_change_keys_or_preferences(client, invalid):
    runtime = client.app.state.runtime
    before_preferences = copy.deepcopy(runtime.preferences)
    before_settings = runtime.store.settings()
    response = client.post("/api/settings", json={
        "api_keys": KEYS, "analysis_provider": "anthropic", "analysis_models_by_provider": MODELS, **invalid,
    })
    assert response.status_code == 400, response.text
    assert runtime.api_keys == {provider: "" for provider in KEYS}
    assert runtime.preferences == before_preferences
    assert runtime.store.settings() == before_settings


def test_environment_keys_are_separate_and_google_alias_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", KEYS["gemini"])
    monkeypatch.setenv("OPENAI_API_KEY", KEYS["openai"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEYS["anthropic"])
    with TestClient(create_app(tmp_path)) as client:
        assert client.app.state.runtime.api_keys == KEYS
        response = client.get("/api/status")
        assert all(provider["available"] for provider in response.json()["analysis_providers"])
        assert not any(key in response.text for key in KEYS.values())


def test_queued_analysis_retains_provider_key_and_model_when_settings_change(client, monkeypatch):
    calls = record_analysis(monkeypatch)
    client.post("/api/settings", json=settings("openai"))
    book = import_book(client)
    started, release = threading.Event(), threading.Event()

    def occupy_worker():
        started.set()
        assert release.wait(5)

    client.app.state.runtime.pool.submit(occupy_worker)
    assert started.wait(2)
    try:
        response = start_discovery(client, book["id"])
        assert response.status_code == 200, response.text
        assert response.json()["job"]["status"] == "queued"
        changed = client.post("/api/settings", json={
            "analysis_provider": "anthropic", "api_keys": {"openai": "rotated-openai-key"},
            "analysis_models_by_provider": {"openai": "new-openai-model"},
            "preprocess_models_by_provider": {"openai": "new-openai-model"},
        })
        assert changed.status_code == 200, changed.text
    finally:
        release.set()
    job = wait_job(client, response.json()["job"]["id"])
    assert job["status"] == "completed", job
    assert calls and set(calls) == {("openai", KEYS["openai"], MODELS["openai"])}


def test_analysis_failure_after_key_rotation_redacts_old_and_current_keys(client, monkeypatch):
    started, release = threading.Event(), threading.Event()
    rotated = "rotated-openai-secret"

    def fail(key):
        started.set()
        assert release.wait(5)
        raise ValueError(f"Provider failed: {key}, {rotated}, {KEYS['anthropic']}")

    record_analysis(monkeypatch, fail)
    client.post("/api/settings", json=settings("openai"))
    book = import_book(client)
    response = start_discovery(client, book["id"])
    assert response.status_code == 200, response.text
    try:
        assert started.wait(2)
        client.post("/api/settings", json={"api_keys": {"openai": rotated}})
    finally:
        release.set()
    job = wait_job(client, response.json()["job"]["id"])
    assert job["status"] == "failed"
    assert "Provider failed: [redacted], [redacted], [redacted]" in job["error"]
    assert not any(key in json.dumps(job) for key in (*KEYS.values(), rotated))


def test_gemini_narration_uses_its_own_key_and_model_with_anthropic_analysis_selected(client, monkeypatch):
    calls = []

    def synthesize(segment, character, scene, provider, model, key, output_path):
        calls.append((provider, model, key))
        with wave.open(str(output_path), "wb") as wav:
            wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x10\0" * 2400)
        return {"fingerprint": render_fingerprint(segment, character, scene, provider, model),
                "duration": .1, "provider": provider, "model": model, "voice": voice_id(character, provider)}

    monkeypatch.setattr("bardic.app.synthesize", synthesize)
    response = client.post("/api/settings", json={
        "api_keys": KEYS, "analysis_provider": "anthropic", "analysis_models_by_provider": MODELS, "tts_model": TTS_MODELS[-1],
    })
    assert response.status_code == 200, response.text
    book = import_book(client)
    response = client.post(f"/api/books/{book['id']}/render", json={"provider": "gemini", "segment_id": book["segments"][0]["id"]})
    assert response.status_code == 200, response.text
    job = wait_job(client, response.json()["id"])
    assert job["status"] == "completed", job
    assert calls == [("gemini", TTS_MODELS[-1], KEYS["gemini"])]
