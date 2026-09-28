"""Voice routes the rest of the suite never calls successfully; offline with fake providers.

Every response here is also validated against the published contract by conftest.
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from bardic import breeze, gemini_voices
from bardic.app import create_app
from test_app import import_text
from test_narration_providers import FakeBreeze, _clear_environment, connect
from test_voice_library import FakeGemini, design_voice, library


@pytest.fixture
def servers(monkeypatch):
    fake_breeze, fake_gemini = FakeBreeze(), FakeGemini()
    monkeypatch.setattr(breeze, "_transport", httpx.MockTransport(fake_breeze))
    monkeypatch.setattr(breeze, "_sleep", lambda seconds: None)
    monkeypatch.setattr(gemini_voices, "_transport", httpx.MockTransport(fake_gemini))
    return fake_breeze, fake_gemini


@pytest.fixture
def client(tmp_path, monkeypatch, servers):
    _clear_environment(monkeypatch)
    monkeypatch.setattr("bardic.voice_routes.list_system_voices", lambda: [])
    with TestClient(create_app(tmp_path)) as client:
        yield client


def test_edit_voice_renames_locally_and_on_the_breeze_server_without_changing_the_pin(client, servers):
    fake_breeze, _ = servers
    connect(client)
    book = import_text(client)
    voice = design_voice(client, book)[1]["voice"]
    server_id = voice["versions"][0]["provider_voice_id"]
    edited = client.patch(f"/api/voices/{voice['id']}", json={"name": "  Lighthouse keeper ", "description": "Low and patient."})
    assert edited.status_code == 200, edited.text
    edited = edited.json()
    assert edited["name"] == "Lighthouse keeper" and edited["description"] == "Low and patient."
    assert edited["versions"][0]["revision"] == voice["versions"][0]["revision"]
    assert ("PATCH", f"/v1/voices/{server_id}") in [(method, path) for method, path, _ in fake_breeze.requests]
    # Omitted fields stay; whitespace-only names are refused by the library (400), not by request validation.
    assert client.patch(f"/api/voices/{voice['id']}", json={}).json()["name"] == "Lighthouse keeper"
    assert client.patch(f"/api/voices/{voice['id']}", json={"name": "   "}).status_code == 400
    assert client.patch("/api/voices/vl_0000000000000000", json={"name": "X"}).status_code == 404


def test_edit_draft_changes_open_drafts_only(client, servers):
    connect(client)
    draft = client.post("/api/voices/drafts", json={"provider": "breeze", "name": "Tide", "description": "Calm."}).json()
    edited = client.patch(f"/api/voices/drafts/{draft['id']}",
                          json={"description": " A calm harbour pilot. ", "sample_text": "Steady as she goes."})
    assert edited.status_code == 200, edited.text
    edited = edited.json()
    assert (edited["name"], edited["description"], edited["sample_text"]) == ("Tide", "A calm harbour pilot.", "Steady as she goes.")
    assert client.post(f"/api/voices/drafts/{draft['id']}/abandon").json()["status"] == "abandoned"
    assert client.patch(f"/api/voices/drafts/{draft['id']}", json={"name": "Late"}).status_code == 409
    assert client.patch("/api/voices/drafts/vd_0000000000000000", json={"name": "X"}).status_code == 404


def test_discard_marks_a_breeze_preview_and_deletes_a_gemini_candidate(client, servers):
    fake_breeze, fake_gemini = servers
    connect(client)
    draft = client.post("/api/voices/drafts", json={"provider": "breeze", "name": "Tide", "description": "Calm pilot."}).json()
    draft = client.post(f"/api/voices/drafts/{draft['id']}/generate", json={"count": 2}).json()
    discarded = client.post(f"/api/voices/drafts/{draft['id']}/candidates/c1/discard")
    assert discarded.status_code == 200, discarded.text
    assert [c["discarded"] for c in discarded.json()["candidates"]] == [True, False]
    again = client.post(f"/api/voices/drafts/{draft['id']}/candidates/c1/discard")  # idempotent
    assert again.status_code == 200 and again.json()["candidates"][0]["discarded"]
    assert client.post(f"/api/voices/drafts/{draft['id']}/candidates/c9/discard").status_code == 404
    refused = client.post(f"/api/voices/drafts/{draft['id']}/save", json={"candidate_id": "c1", "name": "Tide"})
    assert refused.status_code == 400

    client.app.state.runtime.api_keys["gemini"] = "gemini-test-key"
    book = import_text(client)
    gemini = client.post("/api/voices/drafts", json={"provider": "gemini", "name": "Pilot",
                                                      "description": "A calm harbour pilot."}).json()
    gemini = client.post(f"/api/voices/drafts/{gemini['id']}/generate",
                         json={"book_id": book["id"], "confirm_cost": True}).json()
    made = gemini["candidates"][0]["provider_voice_id"]
    gone = client.post(f"/api/voices/drafts/{gemini['id']}/candidates/c1/discard")
    assert gone.status_code == 200, gone.text
    assert gone.json()["candidates"][0]["discarded"] and fake_gemini.deleted == [made]
    assert library(client)["drafts"][0]["id"] == gemini["id"]  # still open
