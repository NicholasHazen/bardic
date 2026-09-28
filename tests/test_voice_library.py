"""Voice library: design, versions, defaults and cast assignment, offline with fake providers."""
import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from bardic import breeze, gemini_voices
from bardic.app import create_app
from bardic.audio import AudioError, render_fingerprint, voice_selection
from test_app import import_text, wait_job
from test_audio import wav_bytes
from test_narration_providers import FakeBreeze, _clear_environment, connect


class FakeGemini:
    """The documented Gemini Voices API, including its paid create."""

    def __init__(self):
        self.voices, self.creates, self.deleted, self.keys = {}, [], [], []

    def __call__(self, request: httpx.Request):
        self.keys.append(request.headers.get("x-goog-api-key"))
        path = request.url.path
        if path == "/v1beta/voices" and request.method == "POST":
            body = json.loads(request.content)
            self.creates.append(body)
            voice_id = f"voice_{len(self.creates):04d}"
            voice = {"id": voice_id, "displayName": body["voice"]["display_name"], "type": "prompted",
                     "languageCode": body["voice"]["language_code"], "expireTime": "2027-09-28T00:00:00Z"}
            self.voices[voice_id] = voice
            return httpx.Response(200, json={**voice, "sampleAudio": {"mimeType": "audio/wav",
                                                                      "data": base64.b64encode(wav_bytes()).decode()}})
        if path == "/v1beta/voices" and request.method == "GET":
            return httpx.Response(200, json={"voices": list(self.voices.values())})
        voice_id = path.rsplit("/", 1)[-1]
        if request.method == "DELETE":
            self.deleted.append(voice_id)
            return httpx.Response(200, json={}) if self.voices.pop(voice_id, None) else httpx.Response(404, json={})
        return httpx.Response(404, json={})


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
    with TestClient(create_app(tmp_path)) as client:
        yield client


def library(client):
    response = client.get("/api/voices")
    assert response.status_code == 200, response.text
    return response.json()


def voice_named(client, name):
    return next(voice for voice in library(client)["voices"] if voice["name"] == name)


def narrator_take(client, book):
    presented = client.get(f"/api/books/{book['id']}").json()
    segment = next(s for s in presented["segments"] if s["speaker_id"] == "narrator")
    return segment, segment["audio"]


def render(client, book, segment_id, provider="breeze"):
    job = client.post(f"/api/books/{book['id']}/render", json={"provider": provider, "segment_id": segment_id})
    assert job.status_code == 200, job.text
    finished = wait_job(client, job.json()["id"])
    assert finished["status"] == "completed", finished
    return finished


def design_voice(client, book, character_id="narrator", **save):
    draft = client.post("/api/voices/drafts", json={"provider": "breeze", "book_id": book["id"],
                                                     "character_id": character_id}).json()
    draft = client.post(f"/api/voices/drafts/{draft['id']}/generate", json={"book_id": book["id"], "count": 2})
    assert draft.status_code == 200, draft.text
    draft = draft.json()
    saved = client.post(f"/api/voices/drafts/{draft['id']}/save",
                        json={"candidate_id": draft["candidates"][0]["id"], "name": "Keeper", **save})
    assert saved.status_code == 200, saved.text
    return draft, saved.json()


def test_check_connection_imports_server_voices_and_sets_the_default(client, servers):
    fake_breeze, _ = servers
    assert library(client)["voices"] == []
    connect(client)
    voices = library(client)["voices"]
    assert [voice["name"] for voice in voices] == ["Narrator"]  # the designed voice is not stable enough
    narrator = voices[0]
    assert narrator["origin"] == "imported" and narrator["is_default"] and narrator["versions"][0]["server_state"] == "ok"
    assert library(client)["defaults"]["breeze"] == narrator["id"]
    assert client.get(narrator["versions"][0]["audition_url"]).status_code == 200
    client.post("/api/narration/breeze/refresh")  # A second check imports nothing twice.
    assert len(library(client)["voices"]) == 1
    fake_breeze.reference = b"changed on the server"
    client.post("/api/narration/breeze/refresh")
    changed = voice_named(client, "Narrator")
    assert changed["versions"][0]["server_state"] == "changed" and changed["warnings"] and not changed["assignable"]


def test_characters_on_default_follow_the_default_voice_and_switching_back_restores_takes(client, servers):
    connect(client)
    book = import_text(client)
    segment, _ = narrator_take(client, book)
    render(client, book, segment["id"])
    first = narrator_take(client, book)[1]
    assert first and first["voice"] == "narrator"
    _, saved = design_voice(client, book)
    keeper = saved["voice"]
    original = library(client)["defaults"]["breeze"]
    assert client.post("/api/voices/defaults", json={"provider": "breeze", "voice_id": keeper["id"]}).status_code == 200
    assert narrator_take(client, book)[1] is None  # Re-voiced: the old take is out of date, not deleted.
    render(client, book, segment["id"])
    assert narrator_take(client, book)[1]["voice"] == keeper["versions"][0]["provider_voice_id"]
    client.post("/api/voices/defaults", json={"provider": "breeze", "voice_id": original})
    # The earlier take is still selected history for the narrator voice; render reuses it without a request.
    sent = len(servers[0].speech)
    render(client, book, segment["id"])
    assert len(servers[0].speech) == sent and narrator_take(client, book)[1]["asset_id"] == first["asset_id"]
    kinds = [event["kind"] for event in client.app.state.runtime.voices.events()]
    assert kinds.count("default_changed") == 3  # initial import, keeper, back to narrator


def test_design_with_character_context_saves_the_audited_clip_and_assigns(client, servers):
    fake_breeze, _ = servers
    connect(client)
    book = import_text(client)
    character = next(c for c in book["characters"] if c["id"] == "narrator")
    draft = client.post("/api/voices/drafts", json={"provider": "breeze", "book_id": book["id"], "character_id": "narrator"}).json()
    assert draft["name"] == "Narrator" and draft["context"]["character_id"] == "narrator"
    assert character["direction"] in draft["description"]
    line = next(s["text"] for s in book["segments"] if s["speaker_id"] == "narrator")
    assert draft["sample_text"] == line[:len(draft["sample_text"])]
    assert client.post(f"/api/voices/drafts/{draft['id']}/generate", json={"count": 2}).status_code == 200
    draft = client.get("/api/voices").json()["drafts"][0]
    assert len(draft["candidates"]) == 2 and all(c["audio_url"] for c in draft["candidates"])
    heard = client.get(draft["candidates"][1]["audio_url"]).content
    saved = client.post(f"/api/voices/drafts/{draft['id']}/save",
                        json={"candidate_id": draft["candidates"][1]["id"], "name": "Keeper",
                              "assign": {"book_id": book["id"], "character_id": "narrator"}}).json()
    upload = fake_breeze.clones[-1]
    assert upload["reference_audio"] == heard  # byte-identical to the audition
    assert upload["reference_text"].decode() == draft["sample_text"]
    assert upload["id"].decode().startswith("bardic-")
    assert json.loads(upload["labels"]) == {"bardic_voice": saved["voice"]["id"], "bardic_version": "1"}
    assert "assignment_error" not in saved
    narrator = next(c for c in saved["book"]["characters"] if c["id"] == "narrator")
    assert narrator["voices"]["breeze"] == {"library": saved["voice"]["id"]}
    assert library(client)["drafts"] == []
    # The saved check learns about the voice Bardic just made: no false "missing".
    assert voice_named(client, "Keeper")["versions"][0]["server_state"] == "ok"
    assert voice_named(client, "Keeper")["assignable"] is True
    usage = voice_named(client, "Keeper")["usage"]
    assert usage == [{"book_id": book["id"], "book_title": book["title"], "character_id": "narrator",
                      "character_name": "Narrator", "follows": "assigned"}]


def test_iterating_saves_a_new_current_version_and_old_versions_stay_renderable(client, servers):
    fake_breeze, _ = servers
    connect(client)
    book = import_text(client)
    _, saved = design_voice(client, book, assign={"book_id": book["id"], "character_id": "narrator"})
    keeper = saved["voice"]
    segment, _ = narrator_take(client, book)
    render(client, book, segment["id"])
    v1_take = narrator_take(client, book)[1]
    draft = client.post("/api/voices/drafts", json={"provider": "breeze", "base_voice_id": keeper["id"],
                                                     "description": "Warmer and slower."}).json()
    assert draft["base_voice_name"] == "Keeper" and draft["sample_text"]
    client.post(f"/api/voices/drafts/{draft['id']}/generate", json={"count": 1})
    draft = library(client)["drafts"][0]
    version = client.post(f"/api/voices/drafts/{draft['id']}/save",
                          json={"candidate_id": draft["candidates"][0]["id"], "name": "Keeper", "mode": "version"}).json()["voice"]
    assert version["current_version"] == 2 and len(version["versions"]) == 2
    assert version["versions"][0]["provider_voice_id"] != version["versions"][1]["provider_voice_id"]
    assert narrator_take(client, book)[1] is None  # follows the new version
    render(client, book, segment["id"])
    assert narrator_take(client, book)[1]["voice"] == version["versions"][1]["provider_voice_id"]
    back = client.post(f"/api/voices/{keeper['id']}/current", json={"version": 1}).json()
    assert back["current_version"] == 1
    render(client, book, segment["id"])
    assert narrator_take(client, book)[1]["asset_id"] == v1_take["asset_id"]
    assert version["versions"][0]["provider_voice_id"] in fake_breeze.voices  # never overwritten


def test_delete_rules_protect_the_default_and_imported_server_voices(client, servers):
    fake_breeze, _ = servers
    connect(client)
    book = import_text(client)
    narrator = voice_named(client, "Narrator")
    assert client.delete(f"/api/voices/{narrator['id']}").status_code == 409  # it is the default
    _, saved = design_voice(client, book, make_default=True)
    removed = client.delete(f"/api/voices/{narrator['id']}").json()
    assert removed["server_deleted"] == [] and "narrator" in fake_breeze.voices  # imported: Bardic only
    client.post("/api/narration/breeze/refresh")
    assert "Narrator" not in [voice["name"] for voice in library(client)["voices"]]  # not re-imported
    other = design_voice(client, book)[1]["voice"]
    gone = client.delete(f"/api/voices/{other['id']}").json()
    assert gone["server_deleted"] == [other["versions"][0]["provider_voice_id"]]
    assert other["versions"][0]["provider_voice_id"] in fake_breeze.deleted
    assert [event["kind"] for event in client.app.state.runtime.voices.events(other["id"])] == ["created", "deleted"]


def test_deleted_or_unresolved_assignments_fail_closed(client, servers):
    connect(client)
    book = import_text(client)
    keeper = design_voice(client, book, assign={"book_id": book["id"], "character_id": "narrator"})[1]["voice"]
    client.delete(f"/api/voices/{keeper['id']}")
    segment, _ = narrator_take(client, book)
    blocked = client.post(f"/api/books/{book['id']}/render", json={"provider": "breeze", "segment_id": segment["id"]})
    assert blocked.status_code == 400 and "was deleted" in blocked.json()["detail"]
    for character in ({"voices": {"gemini": {"library": "vl_0123456789abcdef"}}}, {"voice": "library:vl_0123456789abcdef"}):
        with pytest.raises(AudioError, match="not resolved"):
            render_fingerprint({"id": "s", "text": "Hi."}, character, {}, "gemini", None)
    with pytest.raises(AudioError, match="deleted"):
        voice_selection({"voices": {"breeze": {"error": "The voice “Keeper” was deleted."}}}, "breeze")


def test_simple_listening_accepts_library_voices_and_breeze_default(client, servers):
    fake_breeze, _ = servers
    connect(client)
    book = import_text(client)
    keeper = design_voice(client, book)[1]["voice"]
    segment = book["segments"][0]
    by_library = client.post(f"/api/books/{book['id']}/listen", json={"provider": "breeze", "voice": f"library:{keeper['id']}",
                                                                       "segment_id": segment["id"]}).json()
    assert by_library["session"]["voice"] == keeper["versions"][0]["provider_voice_id"]
    assert wait_job(client, by_library["job"]["id"])["status"] == "completed"
    by_default = client.post(f"/api/books/{book['id']}/listen", json={"provider": "breeze", "voice": None,
                                                                       "segment_id": segment["id"]}).json()
    assert by_default["session"]["voice"] == "narrator"  # the Bardic default, not a server guess
    missing = client.post(f"/api/books/{book['id']}/listen", json={"provider": "breeze", "voice": "library:vl_0000000000000000",
                                                                    "segment_id": segment["id"]})
    assert missing.status_code == 400


def test_gemini_design_requires_confirmation_records_cost_and_cleans_up(client, servers):
    _, fake_gemini = servers
    runtime = client.app.state.runtime
    runtime.api_keys["gemini"] = "gemini-test-key"
    book = import_text(client)
    draft = client.post("/api/voices/drafts", json={"provider": "gemini", "name": "Astronomer",
                                                     "description": "A warm astronomer in his sixties."}).json()
    refused = client.post(f"/api/voices/drafts/{draft['id']}/generate", json={"book_id": book["id"]})
    assert refused.status_code == 400 and fake_gemini.creates == []
    for _ in range(2):
        made = client.post(f"/api/voices/drafts/{draft['id']}/generate",
                           json={"book_id": book["id"], "confirm_cost": True, "gender": "male"})
        assert made.status_code == 200, made.text
    assert [create["store"] for create in fake_gemini.creates] == [True, True]
    assert fake_gemini.creates[0]["voice"]["prompted"]["input"] == "A warm astronomer in his sixties."
    draft = made.json()
    assert [c["provider_voice_id"] for c in draft["candidates"]] == ["voice_0001", "voice_0002"]
    assert client.get(draft["candidates"][0]["audio_url"]).status_code == 200
    rows = client.get(f"/api/books/{book['id']}/resources").json()["operations"]
    design = [row for row in rows if row["stage"] == "voice_design"]
    assert len(design) == 2 and all(row["request_count"] == 1 and row["estimated_cost_usd"] is None
                                     and row["cost_basis"] == "unknown" for row in design)
    saved = client.post(f"/api/voices/drafts/{draft['id']}/save",
                        json={"candidate_id": draft["candidates"][1]["id"], "name": "Astronomer"}).json()
    assert fake_gemini.deleted == ["voice_0001"]  # the unchosen stored voice is removed
    voice = saved["voice"]
    assert voice["versions"][0]["provider_voice_id"] == "voice_0002" and voice["versions"][0]["expires_at"]
    client.post("/api/voices/gemini/refresh")
    gemini = library(client)["providers"]["gemini"]
    assert gemini["stored_count"] == 1 and gemini["project_voices"][0]["in_library"]
    runtime.api_keys["gemini"] = "another-project-key"
    assert voice_named(client, "Astronomer")["versions"][0]["server_state"] == "other_project"
    assert client.delete(f"/api/voices/{voice['id']}").status_code == 409


def test_gemini_abandon_deletes_every_stored_candidate(client, servers):
    _, fake_gemini = servers
    client.app.state.runtime.api_keys["gemini"] = "gemini-test-key"
    book = import_text(client)
    draft = client.post("/api/voices/drafts", json={"provider": "gemini", "name": "Sailor",
                                                     "description": "A gravelly old sailor."}).json()
    client.post(f"/api/voices/drafts/{draft['id']}/generate", json={"book_id": book["id"], "confirm_cost": True})
    abandoned = client.post(f"/api/voices/drafts/{draft['id']}/abandon").json()
    assert abandoned["status"] == "abandoned" and fake_gemini.deleted == ["voice_0001"]
    assert library(client)["drafts"] == []


def test_a_busy_draft_refuses_a_second_generation(client, servers):
    connect(client)
    draft = client.post("/api/voices/drafts", json={"provider": "breeze", "name": "A", "description": "Calm narrator."}).json()
    client.app.state.runtime.voice_busy.add(draft["id"])
    assert client.post(f"/api/voices/drafts/{draft['id']}/generate", json={"count": 1}).status_code == 409


def test_clone_requires_consent_and_pins_the_uploaded_recording(client, servers):
    fake_breeze, _ = servers
    connect(client)
    recording = wav_bytes(frames=24000)
    refused = client.post("/api/voices/breeze/clone", data={"name": "Mara", "reference_text": "Hello there.", "consent": "false"},
                          files={"reference_audio": ("mara.wav", recording, "audio/wav")})
    assert refused.status_code == 400 and fake_breeze.clones == []
    made = client.post("/api/voices/breeze/clone", data={"name": "Mara", "reference_text": "Hello there.", "consent": "true"},
                       files={"reference_audio": ("mara.wav", recording, "audio/wav")})
    assert made.status_code == 200, made.text
    voice = made.json()["voice"]
    assert voice["origin"] == "cloned" and voice["versions"][0]["server_state"] in ("ok", "unknown")
    assert fake_breeze.clones[-1]["reference_audio"] == recording


def test_switching_a_followed_voice_is_refused_while_narration_runs(client, servers):
    connect(client)
    book = import_text(client)
    keeper = design_voice(client, book, assign={"book_id": book["id"], "character_id": "narrator"})[1]["voice"]
    runtime = client.app.state.runtime
    job = runtime.store.create_job(book["id"], "render", 1)
    runtime.store.update_job(job["id"], status="running")
    assert client.post(f"/api/voices/{keeper['id']}/current", json={"version": 1}).status_code == 409
    assert client.delete(f"/api/voices/{keeper['id']}").status_code == 409
