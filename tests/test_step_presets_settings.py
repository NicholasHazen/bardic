"""Owner-authored saved step settings (analysis_step_presets): validated, versioned, persisted."""
import pytest
from fastapi.testclient import TestClient

from bardic.app import create_app
from bardic.store import Store


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for variable in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr("bardic.app.list_system_voices", lambda: [])


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as test_client:
        yield test_client


def preset(**changes):
    value = {"id": "p_scan", "name": "Cheap scan", "step": "discovery", "version": 1,
             "config": {"provider": "openai", "model": "gpt-cheap", "gate": "review", "concurrency": 3, "fresh": False,
                        "chapter_id": None}}
    config = changes.pop("config", {})
    value.update(changes)
    value["config"] = {**value["config"], **config}
    return value


def test_presets_default_to_empty_and_round_trip_through_status(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/status").json()["analysis_step_presets"] == []
        saved = client.post("/api/settings", json={"analysis_step_presets": [
            preset(name="  Cheap   scan "),
            preset(id="p_local", name="Plain", step="census", config={"provider": "local", "model": None, "chapter_id": "c9"}),
        ]})
        assert saved.status_code == 200, saved.text
        items = saved.json()["analysis_step_presets"]
        assert items[0] == {"id": "p_scan", "name": "Cheap scan", "step": "discovery", "version": 1,
                            "config": {"provider": "openai", "model": "gpt-cheap", "custom_model": False, "gate": "review",
                                       "concurrency": 3, "fresh": False, "chapter_id": None}}
        # A step that is not chapter-scoped keeps no section; a local step no model.
        assert items[1]["config"]["chapter_id"] is None and items[1]["config"]["model"] is None
    with TestClient(create_app(tmp_path)) as again:
        assert [item["id"] for item in again.get("/api/status").json()["analysis_step_presets"]] == ["p_scan", "p_local"]
        # Other settings leave the saved list alone; an empty list removes every saved setting.
        again.post("/api/settings", json={"analysis_provider": "openai"})
        assert len(again.get("/api/status").json()["analysis_step_presets"]) == 2
        assert again.post("/api/settings", json={"analysis_step_presets": []}).json()["analysis_step_presets"] == []


@pytest.mark.parametrize("item, fragment", [
    (preset(step="nope"), "Unknown analysis step"),
    (preset(config={"provider": "local", "model": None}), "Choose"),
    (preset(config={"model": "bad model id"}), "valid model ID"),
    (preset(step="census"), "runs locally"),
    (preset(config={"provider": "booknlp", "model": None}), "Choose"),
])
def test_presets_reject_steps_providers_and_models_the_step_does_not_accept(client, item, fragment):
    before = client.get("/api/status").json()["analysis_step_presets"]
    response = client.post("/api/settings", json={"analysis_step_presets": [item]})
    assert response.status_code == 400
    assert fragment in response.json()["detail"]
    assert client.get("/api/status").json()["analysis_step_presets"] == before


@pytest.mark.parametrize("items", [
    [preset(), preset(name="Other")],                     # duplicate ID
    [preset(), preset(id="p_two", name="cheap SCAN")],    # duplicate name for one step
])
def test_presets_need_unique_ids_and_names(client, items):
    assert client.post("/api/settings", json={"analysis_step_presets": items}).status_code == 400


@pytest.mark.parametrize("item", [
    preset(version=2),
    preset(id="has space"),
    preset(name=""),
    preset(name="x" * 61),
    preset(config={"concurrency": 5}),
    preset(config={"concurrency": "2"}),
    preset(config={"gate": "sometimes"}),
    preset(config={"limit": 3}),
    {**preset(), "extra": True},
])
def test_presets_reject_malformed_entries(client, item):
    assert client.post("/api/settings", json={"analysis_step_presets": [item]}).status_code == 422


def test_presets_are_capped(client):
    items = [preset(id=f"p{i}", name=f"Set {i}") for i in range(51)]
    assert client.post("/api/settings", json={"analysis_step_presets": items}).status_code == 422


def test_malformed_stored_presets_are_dropped_on_load(tmp_path):
    Store(tmp_path).save_settings({"analysis_step_presets": [preset(), {"id": "broken"}, "text", preset(id="p2", version=7)]})
    with TestClient(create_app(tmp_path)) as client:
        assert [item["id"] for item in client.get("/api/status").json()["analysis_step_presets"]] == ["p_scan"]
