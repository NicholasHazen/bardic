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
        self.fail = {}  # "list" or "delete:<voice id>" -> an HTTP status, or "text" for a non-JSON 200

    def __call__(self, request: httpx.Request):
        self.keys.append(request.headers.get("x-goog-api-key"))
        path = request.url.path
        failure = self.fail.get("list" if path == "/v1beta/voices" and request.method == "GET" else
                                f"{request.method.lower()}:{path.rsplit('/', 1)[-1]}")
        if failure == "text":
            return httpx.Response(200, content=b"<html>not json</html>")
        if failure:
            return httpx.Response(failure, json={"error": {"message": "secret provider text"}})
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
    segment = next(s for s in presented["passages"] if s["speaker_id"] == "narrator")
    return segment, segment["audio"]


def render(client, book, passage_id, provider="breeze"):
    job = client.post(f"/api/books/{book['id']}/render", json={"provider": provider, "passage_id": passage_id})
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


def test_a_version_without_a_recorded_recipe_still_has_a_recipe_description(client):
    # The contract requires `recipe.description`. No route makes a recipe-less version, but the library accepts one.
    client.app.state.runtime.voices.create("gemini", name="Bare", description="", origin="designed",
                                           version={"provider_voice_id": "voice_0001"})
    recipe = voice_named(client, "Bare")["versions"][0]["recipe"]
    assert recipe == {"description": "", "sample_text": None, "model": None, "language_code": None, "gender": None}


def test_check_connection_imports_server_voices_and_sets_the_default(client, servers):
    fake_breeze, _ = servers
    assert library(client)["voices"] == []
    connect(client)
    voices = library(client)["voices"]
    assert [voice["name"] for voice in voices] == ["Narrator"]  # the designed voice is not stable enough
    narrator = voices[0]
    assert narrator["origin"] == "imported" and narrator["is_default"] and narrator["versions"][0]["server_state"] == "ok"
    assert library(client)["defaults"]["breeze"] == narrator["id"]
    assert client.get(narrator["versions"][0]["audition"]["url"]).status_code == 200
    client.post("/api/narration/breeze/refresh")  # A second check imports nothing twice.
    assert len(library(client)["voices"]) == 1
    fake_breeze.reference = b"changed on the server"
    client.post("/api/narration/breeze/refresh")
    changed = voice_named(client, "Narrator")
    assert changed["versions"][0]["server_state"] == "changed" and changed["warnings"] and not changed["assignable"]


def test_refresh_imports_a_server_voice_whose_name_exceeds_the_library_limit(client, servers):
    fake_breeze, _ = servers
    # Server names are not user input: Breeze may send up to 200 characters, the library keeps 100.
    long_name = "Ölander " + "\U0001F56F" * 60 + " the Lamplighter of the Northern Wharf and the Last Ferry"
    fake_breeze.voices["narrator"]["name"] = long_name
    view = client.post("/api/settings", json={"breeze_url": "http://breeze.local:7860/"})
    assert view.status_code == 200, view.text
    for _ in range(2):  # the second refresh must not fail either
        refreshed = client.post("/api/narration/breeze/refresh")
        assert refreshed.status_code == 200, refreshed.text
    voices = library(client)["voices"]
    assert len(voices) == 1 and library(client)["defaults"]["breeze"] == voices[0]["id"]
    assert voices[0]["name"] == long_name[:100].rstrip() and len(voices[0]["name"]) <= 100


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
    line = next(s["text"] for s in book["passages"] if s["speaker_id"] == "narrator")
    assert draft["sample_text"] == line[:len(draft["sample_text"])]
    assert client.post(f"/api/voices/drafts/{draft['id']}/generate", json={"count": 2}).status_code == 200
    draft = client.get("/api/voices").json()["drafts"][0]
    assert len(draft["candidates"]) == 2 and all(c["audio"] for c in draft["candidates"])
    heard = client.get(draft["candidates"][1]["audio"]["url"]).content
    saved = client.post(f"/api/voices/drafts/{draft['id']}/save",
                        json={"candidate_id": draft["candidates"][1]["id"], "name": "Keeper",
                              "assign": {"book_id": book["id"], "character_id": "narrator"}}).json()
    upload = fake_breeze.clones[-1]
    assert upload["reference_audio"] == heard  # byte-identical to the audition
    assert upload["reference_text"].decode() == draft["sample_text"]
    assert upload["id"].decode().startswith("bardic-")
    assert json.loads(upload["labels"]) == {"bardic_voice": saved["voice"]["id"], "bardic_version": "1"}
    assert saved["assignment_error"] is None and saved["cleanup_error"] is None
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
    assert removed["voice_id"] == narrator["id"] and removed["server_deleted"] == [] and "narrator" in fake_breeze.voices  # imported: Bardic only
    client.post("/api/narration/breeze/refresh")
    assert "Narrator" not in [voice["name"] for voice in library(client)["voices"]]  # not re-imported
    other = design_voice(client, book)[1]["voice"]
    gone = client.delete(f"/api/voices/{other['id']}").json()
    assert gone["voice_id"] == other["id"] and gone["server_deleted"] == [other["versions"][0]["provider_voice_id"]]
    assert other["versions"][0]["provider_voice_id"] in fake_breeze.deleted
    assert [event["kind"] for event in client.app.state.runtime.voices.events(other["id"])] == [
        "created", "server_voice_deleted", "deleted"]


def test_deleted_or_unresolved_assignments_fail_closed(client, servers):
    connect(client)
    book = import_text(client)
    keeper = design_voice(client, book, assign={"book_id": book["id"], "character_id": "narrator"})[1]["voice"]
    client.delete(f"/api/voices/{keeper['id']}")
    segment, _ = narrator_take(client, book)
    blocked = client.post(f"/api/books/{book['id']}/render", json={"provider": "breeze", "passage_id": segment["id"]})
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
    segment = book["passages"][0]
    by_library = client.post(f"/api/books/{book['id']}/listen", json={"provider": "breeze", "voice": f"library:{keeper['id']}",
                                                                       "passage_id": segment["id"]}).json()
    assert by_library["session"]["voice"] == keeper["versions"][0]["provider_voice_id"]
    assert wait_job(client, by_library["job"]["id"])["status"] == "completed"
    by_default = client.post(f"/api/books/{book['id']}/listen", json={"provider": "breeze", "voice": None,
                                                                       "passage_id": segment["id"]}).json()
    assert by_default["session"]["voice"] == "narrator"  # the Bardic default, not a server guess
    missing = client.post(f"/api/books/{book['id']}/listen", json={"provider": "breeze", "voice": "library:vl_0000000000000000",
                                                                    "passage_id": segment["id"]})
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
    assert client.get(draft["candidates"][0]["audio"]["url"]).status_code == 200
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


def test_gemini_cleanup_never_deletes_with_another_projects_key(client, servers):
    _, fake_gemini = servers
    runtime = client.app.state.runtime
    runtime.api_keys["gemini"] = "key-a"
    book = import_text(client)
    draft = client.post("/api/voices/drafts", json={"provider": "gemini", "name": "Sailor",
                                                     "description": "A gravelly old sailor."}).json()
    for _ in range(2):
        draft = client.post(f"/api/voices/drafts/{draft['id']}/generate",
                            json={"book_id": book["id"], "confirm_cost": True}).json()
    runtime.api_keys["gemini"] = "key-b"
    refused = client.post(f"/api/voices/drafts/{draft['id']}/abandon")
    assert refused.status_code == 409 and fake_gemini.deleted == []
    third = client.post(f"/api/voices/drafts/{draft['id']}/generate", json={"book_id": book["id"], "confirm_cost": True}).json()
    saved = client.post(f"/api/voices/drafts/{draft['id']}/save",
                        json={"candidate_id": third["candidates"][-1]["id"], "name": "Sailor"}).json()
    # Key A's unchosen candidates stay (and are reported); nothing was deleted with key B.
    assert fake_gemini.deleted == [] and "different Google API key" in saved["cleanup_error"]


def test_a_billed_gemini_voice_is_kept_when_its_sample_cannot_be_stored(client, servers, monkeypatch):
    _, fake_gemini = servers
    runtime = client.app.state.runtime
    runtime.api_keys["gemini"] = "gemini-test-key"
    book = import_text(client)

    def broken(_data):
        raise AudioError("The sample could not be decoded.")

    monkeypatch.setattr(runtime.voices, "store_audio", broken)
    draft = client.post("/api/voices/drafts", json={"provider": "gemini", "name": "Guide",
                                                     "description": "A patient mountain guide."}).json()
    made = client.post(f"/api/voices/drafts/{draft['id']}/generate", json={"book_id": book["id"], "confirm_cost": True})
    assert made.status_code == 200, made.text
    candidate = made.json()["candidates"][0]
    assert candidate["provider_voice_id"] == "voice_0001" and candidate["audio"] is None


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


# Known issues (#17): each test failed before its fix. ---------------------------------------------


def breeze_failing(monkeypatch, fake_breeze, method, voice_id, status=500):
    """Route Breeze requests to the fake, except one method on one server voice, which fails."""
    def transport(request):
        if request.method == method and request.url.path == f"/v1/voices/{voice_id}":
            return httpx.Response(status, json={"error": {"code": "internal_error", "message": "secret"}})
        return fake_breeze(request)
    monkeypatch.setattr(breeze, "_transport", httpx.MockTransport(transport))


def gemini_voice(client, book, name="Astronomer"):
    draft = client.post("/api/voices/drafts", json={"provider": "gemini", "name": name,
                                                     "description": "A warm astronomer in his sixties."}).json()
    draft = client.post(f"/api/voices/drafts/{draft['id']}/generate", json={"book_id": book["id"], "confirm_cost": True})
    assert draft.status_code == 200, draft.text
    saved = client.post(f"/api/voices/drafts/{draft.json()['id']}/save", json={"candidate_id": "c1", "name": name})
    assert saved.status_code == 200, saved.text
    return saved.json()["voice"]


def test_a_failed_gemini_refresh_is_a_502_and_the_library_still_reports_it(client, servers):
    _, fake_gemini = servers
    client.app.state.runtime.api_keys["gemini"] = "gemini-test-key"
    book = import_text(client)
    voice = gemini_voice(client, book)
    assert client.post("/api/voices/gemini/refresh").status_code == 200
    for failure in (500, "text"):
        fake_gemini.fail["list"] = failure
        refreshed = client.post("/api/voices/gemini/refresh")
        assert refreshed.status_code == 502 and refreshed.json()["code"] == "provider_error", refreshed.text
        assert "secret" not in refreshed.text
        gemini = library(client)["providers"]["gemini"]  # 200, not a server error
        assert gemini["state"] == "error" and gemini["message"] and gemini["checked_at"]
        assert gemini["stored_count"] is None and gemini["project_voices"] == []
        assert voice_named(client, "Astronomer")["versions"][0]["server_state"] == "unknown"
    del fake_gemini.fail["list"]
    assert client.post("/api/voices/gemini/refresh").json()["state"] == "ready"
    assert voice_named(client, "Astronomer")["versions"][0]["server_state"] == "ok"
    # A provider failure while deleting is a 502 too, and the voice stays in the library.
    fake_gemini.fail[f"delete:{voice['versions'][0]['provider_voice_id']}"] = 403
    failed = client.delete(f"/api/voices/{voice['id']}")
    assert failed.status_code == 502 and failed.json()["code"] == "provider_error", failed.text
    assert voice_named(client, "Astronomer")["deleted"] is False


def test_a_partly_failed_voice_deletion_is_recorded_and_a_retry_finishes_it(client, servers, monkeypatch):
    fake_breeze, _ = servers
    connect(client)
    book = import_text(client)
    keeper = design_voice(client, book)[1]["voice"]
    draft = client.post("/api/voices/drafts", json={"provider": "breeze", "base_voice_id": keeper["id"],
                                                     "description": "Warmer and slower."}).json()
    client.post(f"/api/voices/drafts/{draft['id']}/generate", json={"count": 1})
    saved = client.post(f"/api/voices/drafts/{draft['id']}/save",
                        json={"candidate_id": "c1", "name": "Keeper", "mode": "version"})
    assert saved.status_code == 200, saved.text
    first, second = sorted(version["provider_voice_id"] for version in saved.json()["voice"]["versions"])
    breeze_failing(monkeypatch, fake_breeze, "DELETE", second)
    failed = client.delete(f"/api/voices/{keeper['id']}")
    assert failed.status_code == 502 and failed.json()["code"] == "provider_error", failed.text
    assert "1 of 2" in failed.json()["detail"]
    assert fake_breeze.deleted == [first]
    left = voice_named(client, "Keeper")  # still in the library, and it says what is gone
    assert {v["provider_voice_id"]: v["server_state"] for v in left["versions"]} == {first: "missing", second: "ok"}
    assert any("deletion" in warning for warning in left["warnings"])
    monkeypatch.setattr(breeze, "_transport", httpx.MockTransport(fake_breeze))
    done = client.delete(f"/api/voices/{keeper['id']}")
    assert done.status_code == 200, done.text
    assert done.json()["voice_id"] == keeper["id"] and done.json()["server_deleted"] == [first, second]
    assert fake_breeze.deleted == [first, second]  # the retry did not delete the first again
    assert "Keeper" not in [voice["name"] for voice in library(client)["voices"]]
    kinds = [event["kind"] for event in client.app.state.runtime.voices.events(keeper["id"])]
    assert kinds.count("server_voice_deleted") == 2 and kinds[-1] == "deleted"


def test_saving_and_cloning_validate_before_uploading_a_server_voice(client, servers):
    fake_breeze, _ = servers
    connect(client)
    book = import_text(client)
    draft = client.post("/api/voices/drafts", json={"provider": "breeze", "name": "Tide", "description": "Calm pilot."}).json()
    client.post(f"/api/voices/drafts/{draft['id']}/generate", json={"count": 1})
    blank = client.post(f"/api/voices/drafts/{draft['id']}/save", json={"candidate_id": "c1", "name": "   "})
    assert blank.status_code == 400 and blank.json()["code"] == "voice_name_invalid", blank.text
    assert fake_breeze.clones == []

    keeper = design_voice(client, book)[1]["voice"]
    uploads = len(fake_breeze.clones)
    iteration = client.post("/api/voices/drafts", json={"provider": "breeze", "base_voice_id": keeper["id"],
                                                         "description": "Warmer."}).json()
    client.post(f"/api/voices/drafts/{iteration['id']}/generate", json={"count": 1})
    assert client.delete(f"/api/voices/{keeper['id']}").status_code == 200
    stale = client.post(f"/api/voices/drafts/{iteration['id']}/save",
                        json={"candidate_id": "c1", "name": "Keeper", "mode": "version"})
    assert stale.status_code == 409 and stale.json()["code"] == "base_voice_deleted", stale.text
    assert len(fake_breeze.clones) == uploads

    recording = wav_bytes(frames=24000)
    form = {"name": "Mara", "reference_text": "Hello there.", "consent": "true"}
    for ids, code in (({"book_id": "missing", "character_id": "narrator"}, "unknown_book"),
                      ({"book_id": book["id"], "character_id": "nobody"}, "unknown_character")):
        refused = client.post("/api/voices/breeze/clone", data={**form, **ids},
                              files={"reference_audio": ("mara.wav", recording, "audio/wav")})
        assert refused.status_code == 400 and refused.json()["code"] == code, refused.text
    assert len(fake_breeze.clones) == uploads


def test_a_record_failure_after_the_upload_removes_the_uploaded_server_voice(client, servers, monkeypatch):
    from bardic.errors import NotFound
    fake_breeze, _ = servers
    connect(client)
    book = import_text(client)
    keeper = design_voice(client, book)[1]["voice"]
    iteration = client.post("/api/voices/drafts", json={"provider": "breeze", "base_voice_id": keeper["id"],
                                                         "description": "Warmer."}).json()
    client.post(f"/api/voices/drafts/{iteration['id']}/generate", json={"count": 1})
    runtime = client.app.state.runtime

    def deleted_meanwhile(voice_id, fields, **kwargs):  # the base voice was deleted after validation
        raise NotFound("voice_not_found", "Voice not found")

    monkeypatch.setattr(runtime.voices, "add_version", deleted_meanwhile)
    stale = client.post(f"/api/voices/drafts/{iteration['id']}/save",
                        json={"candidate_id": "c1", "name": "Keeper", "mode": "version"})
    assert stale.status_code == 409 and stale.json()["code"] == "base_voice_deleted", stale.text
    uploaded = fake_breeze.clones[-1]["id"].decode()
    assert uploaded in fake_breeze.deleted and uploaded not in fake_breeze.voices
    assert "removed" in stale.json()["detail"]
    assert uploaded not in [voice["id"] for voice in client.get("/api/status").json()["breeze"]["voices"]]
    assert library(client)["drafts"][0]["id"] == iteration["id"]  # still open, so the save can be retried


def test_a_clone_whose_read_back_fails_is_removed_from_the_server(client, servers, monkeypatch):
    fake_breeze, _ = servers
    connect(client)

    def transport(request):  # The server keeps the upload, but its reference clip cannot be read back.
        if request.method == "GET" and request.url.path.startswith("/v1/voices/bardic-"):
            return httpx.Response(500, json={"error": {"code": "internal_error"}})
        return fake_breeze(request)

    monkeypatch.setattr(breeze, "_transport", httpx.MockTransport(transport))
    made = client.post("/api/voices/breeze/clone", data={"name": "Mara", "reference_text": "Hello there.", "consent": "true"},
                       files={"reference_audio": ("mara.wav", wav_bytes(frames=24000), "audio/wav")})
    assert made.status_code == 502 and made.json()["code"] == "provider_error", made.text
    uploaded = fake_breeze.clones[-1]["id"].decode()
    assert uploaded in fake_breeze.deleted and uploaded not in fake_breeze.voices
    assert "Mara" not in [voice["name"] for voice in library(client)["voices"]]


def test_single_voice_responses_compare_with_the_saved_provider_checks(client, servers):
    connect(client)
    narrator = voice_named(client, "Narrator")
    edited = client.patch(f"/api/voices/{narrator['id']}", json={"description": "Steady."})
    assert edited.status_code == 200 and edited.json()["versions"][0]["server_state"] == "ok"
    switched = client.post(f"/api/voices/{narrator['id']}/current", json={"version": 1}).json()
    assert switched["versions"][0]["server_state"] == "ok"
    book = import_text(client)
    assert design_voice(client, book)[1]["voice"]["versions"][0]["server_state"] == "ok"
    recording = wav_bytes(frames=24000)
    cloned = client.post("/api/voices/breeze/clone", data={"name": "Mara", "reference_text": "Hello there.", "consent": "true"},
                         files={"reference_audio": ("mara.wav", recording, "audio/wav")}).json()["voice"]
    assert cloned["versions"][0]["server_state"] == "ok"


def test_audio_in_voice_responses_is_an_audio_object(client, servers):
    connect(client)
    book = import_text(client)
    narrator = voice_named(client, "Narrator")
    audition = narrator["versions"][0]["audition"]
    assert audition["url"] == f"/api/voices/{narrator['id']}/versions/1/audition"
    assert audition["provider"] == "breeze" and audition["voice"] == "narrator" and audition["model"] is None
    # The fake server's reference clip is not a WAV, so nothing is retained: the URL fetches from the provider.
    assert audition["asset_id"] is None and audition["duration"] is None and audition["created_at"] is None
    assert client.get(audition["url"]).status_code == 200
    draft, saved = design_voice(client, book)
    audio = draft["candidates"][0]["audio"]
    assert audio["url"].endswith("/candidates/c1/audio") and audio["duration"] > 0 and audio["provider"] == "breeze"
    assert audio["asset_id"] and audio["voice"] is None and audio["model"] == "breeze-tts-2"
    kept = saved["voice"]["versions"][0]["audition"]  # the auditioned clip is retained as the version's audition
    assert kept["asset_id"] == audio["asset_id"] and kept["duration"] == audio["duration"] and kept["created_at"]
    assert kept["voice"] == saved["voice"]["versions"][0]["provider_voice_id"]
