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


def code(response, status):
    assert response.status_code == status, response.text
    return response.json()["code"]


def test_voice_errors_carry_their_documented_codes(client, servers):
    runtime = client.app.state.runtime
    book = import_text(client)
    # Without a Breeze URL or a Gemini key.
    breeze_draft = client.post("/api/voices/drafts", json={"provider": "breeze", "description": "Calm pilot."}).json()
    assert code(client.post(f"/api/voices/drafts/{breeze_draft['id']}/generate", json={}), 400) == "breeze_url_missing"
    assert code(client.post("/api/voices/gemini/refresh"), 400) == "gemini_key_missing"
    gemini_draft = client.post("/api/voices/drafts", json={"provider": "gemini", "description": "A calm pilot."}).json()
    generate = f"/api/voices/drafts/{gemini_draft['id']}/generate"
    assert code(client.post(generate, json={"book_id": book["id"]}), 400) == "cost_not_confirmed"
    assert code(client.post(generate, json={"confirm_cost": True}), 400) == "book_id_required"
    assert code(client.post(generate, json={"confirm_cost": True, "book_id": book["id"]}), 400) == "gemini_key_missing"
    runtime.api_keys["gemini"] = "gemini-test-key"
    assert code(client.post(generate, json={"confirm_cost": True, "book_id": "missing"}), 400) == "unknown_book"
    assert code(client.post(generate, json={"confirm_cost": True, "book_id": book["id"], "language_code": "English"}),
                400) == "voice_design_invalid"
    short = client.post("/api/voices/drafts", json={"provider": "breeze", "description": "a"}).json()
    assert code(client.post(f"/api/voices/drafts/{short['id']}/generate", json={}), 400) == "description_too_short"
    # Unknown IDs in a request body are 400; unknown IDs in the path are 404.
    for body, expected in (({"book_id": "missing", "character_id": "narrator"}, "unknown_book"),
                           ({"book_id": book["id"], "character_id": "nobody"}, "unknown_character"),
                           ({"base_voice_id": "vl_0000000000000000"}, "unknown_voice")):
        assert code(client.post("/api/voices/drafts", json={"provider": "breeze", **body}), 400) == expected
    assert code(client.post("/api/voices/defaults", json={"provider": "breeze", "voice_id": "vl_0000000000000000"}),
                400) == "unknown_voice"
    assert code(client.get("/api/voices/vl_0000000000000000/versions/1/audition"), 404) == "voice_not_found"
    assert code(client.get(f"/api/voices/drafts/{short['id']}/candidates/c1/audio"), 404) == "candidate_not_found"
    assert code(client.post("/api/voices/drafts/vd_0000000000000000/abandon"), 404) == "voice_draft_not_found"
    save = f"/api/voices/drafts/{short['id']}/save"
    assert code(client.post(save, json={"candidate_id": "c1", "name": "X"}), 400) == "unknown_candidate"
    # A busy or finished draft.
    runtime.voice_busy.add(short["id"])
    assert code(client.post(f"/api/voices/drafts/{short['id']}/abandon"), 409) == "draft_busy"
    runtime.voice_busy.discard(short["id"])
    client.post(f"/api/voices/drafts/{short['id']}/abandon")
    assert code(client.patch(f"/api/voices/drafts/{short['id']}", json={"name": "Late"}), 409) == "draft_finished"
    # Clone form checks.
    recording = ("mara.wav", b"RIFF", "audio/wav")
    form = {"name": "Mara", "reference_text": "Hello there.", "consent": "true"}
    assert code(client.post("/api/voices/breeze/clone", data={**form, "consent": "yes"},
                            files={"reference_audio": recording}), 400) == "consent_required"
    assert code(client.post("/api/voices/breeze/clone", data={**form, "reference_text": " "},
                            files={"reference_audio": recording}), 400) == "reference_text_missing"
    assert code(client.post("/api/voices/breeze/clone", data=form,
                            files={"reference_audio": ("mara.wav", b"", "audio/wav")}), 400) == "recording_empty"
    assert code(client.post("/api/voices/breeze/clone", data=form, files={"reference_audio": recording}),
                400) == "breeze_url_missing"
