"""Narration provider seam: pinned legacy identities and offline Breeze adapter tests."""
import base64
import json
import math
import struct

import httpx
import pytest
from fastapi.testclient import TestClient

from bardic import audio, breeze
from bardic.app import create_app
from bardic.audio import AudioError, validate_audio, voice_id
from bardic.importer import parse_book
from bardic.listening import ListeningRepository
from bardic.store import Store
from test_app import import_text, wait_job
from test_audio import wav_bytes


SEGMENT = {"id": "segment-1", "text": "“Shall we begin?” she asked.", "direction": "Quietly.", "cues": ["pause"]}
LEGACY_CHARACTER = {"id": "c1", "voice": "Puck", "system_voice": "Samantha", "direction": "Warm."}
MAPPED_CHARACTER = {"id": "c1", "voices": {"gemini": {"id": "Puck"}, "system": {"id": "Samantha"}}, "direction": "Warm."}
SCENE = {"tone": "Hushed", "direction": "Slow."}

# Recorded from the implementation before the provider seam existed. Changing
# any of these would orphan existing takes, listening sessions and caches.
GOLDEN = {
    ("gemini", None): "f2df15dd0e5a1bbd6512cdd511e60976696a4a8d3ea856a4f6a6f0adf7b00979",
    ("gemini", "gemini-3.8-flash-lite-tts"): "6589f2ce65a2de3842cf1131c604e1d7a46f96ff1db38d7c5a098c7609cf6abd",
    ("gemini", "gemini-3.1-flash-tts-preview"): "098a64afb80e52868f27f4dfbd6b2a64297454552f34797b77f8eb9330145713",
    ("system", None): "649caa5e5db32db0a83169c7f5dc7ed9a4b3844cf707ddac884dec90ba75acbd",
    ("local", "ignored"): "649caa5e5db32db0a83169c7f5dc7ed9a4b3844cf707ddac884dec90ba75acbd",
}
GOLDEN_SIMPLE = {
    ("gemini", None, None): ("4d931edd6a0096ce2e7d6fd5a9505233032a9ddc24929266f62bbb6d50360ae2",
                             "edf4b323b504d7f818a89b682160987d239e0fd1c127040798b725df6350ed61",
                             "349ac5fb49d38a17a2bb25d4c8fd2b44e9aff4be4bc4463c749331884b4923cc"),
    ("gemini", "Charon", "gemini-3.8-flash-tts"): ("2ce237d43a55f79cf10cfa0af2c503ba00f0b32746f8db39cfecff13d843c2e8",
                                                   "ca81f9ee2feb6bf7438755d971de788f062cfc428daf8deeb737ad0663f9733a",
                                                   "28b66f728add2346e3f5252bf63e2d54bf14f86275d81496e6154b5b54bbd1c2"),
    ("system", "", None): ("6d11a01eff3ebbd07b56f40c673091f26c0551525323311aa0017ddc68748326",
                           "51f212118e3c2ef0bbd22e00b1f1f984926f39bbdae387719eefe298c2536d07",
                           "849556d9ca53aaf6d2f10fb95846291d9dde090826c6a9ec570e96cf489ec819"),
    ("system", "Daniel", "macos-say"): ("561099758b8a1d2622b58571c3c26bd687d78e5c7e6816a4dd66db67d41158e0",
                                        "1c82228c947b41ba0b4fdb999cd144ef0ebbae8240c63576f631d2f383db7799",
                                        "b01ed29222bfed04d3554184622bc13413a770efbef9a71a1b9af4c09f1dae47"),
}


@pytest.mark.parametrize("character", [LEGACY_CHARACTER, MAPPED_CHARACTER], ids=["legacy-fields", "voices-map"])
@pytest.mark.parametrize("provider,model", list(GOLDEN))
def test_existing_provider_fingerprints_are_unchanged(character, provider, model):
    assert audio.render_fingerprint(SEGMENT, character, SCENE, provider, model) == GOLDEN[(provider, model)]


def test_existing_default_voice_fingerprints_are_unchanged():
    passage = {"id": "s", "text": "Hi."}
    assert audio.render_fingerprint(passage, {}, {}, "gemini", None) == "dcb2929262bc96ec5c54fcfd1768a3a3a2193f3640f616a8795a1c5c7cfbf6d7"
    assert audio.render_fingerprint(passage, {}, {}, "system", None) == "71c746f4c28fe47ebad29f21adba0d456b4a7db0d4fc431955d18cf3304bdc2d"


def test_seed_does_not_enter_unseeded_provider_recipes():
    seeded = {**SEGMENT, "seed": 99}
    assert audio.render_fingerprint(seeded, LEGACY_CHARACTER, SCENE, "gemini", None) == GOLDEN[("gemini", None)]
    assert audio.render_fingerprint(seeded, LEGACY_CHARACTER, SCENE, "system", None) == GOLDEN[("system", None)]


@pytest.mark.parametrize("provider,voice,model", list(GOLDEN_SIMPLE))
def test_existing_listening_session_and_cache_identities_are_unchanged(tmp_path, provider, voice, model):
    store = Store(tmp_path)
    book = parse_book("story.txt", b"Chapter One\n\nHello there.")
    book["id"] = "book-1"
    store.save_book(book)
    repository = ListeningRepository(store)
    session = repository.session("book-1", provider, voice, model)
    passage = {"id": "segment-1", "text": "Hello there."}
    session_id, recipe, synthesis = GOLDEN_SIMPLE[(provider, voice, model)]
    assert session["id"] == session_id
    assert ListeningRepository._audio_recipe(passage, session)[-1] == recipe
    assert ListeningRepository._synthesis_key(passage, session) == synthesis


# Breeze ---------------------------------------------------------------------

REFERENCE = b"RIFF-fake-reference-clip"
VOICE = {"object": "voice", "id": "narrator", "name": "Narrator", "kind": "cloned", "description": "Warm.",
         "instruction": None, "reference": {"text": "Welcome aboard.", "duration_ms": 8800,
                                            "audio_url": "/v1/voices/narrator/reference"},
         "settings": {"seed": None, "cfg_scale": None, "temperature": None, "top_k": None, "top_p": None},
         "labels": {"role": "narrator"}, "is_default": True,
         "created_at": "2026-09-28T04:28:48Z", "updated_at": "2026-09-28T04:28:48Z"}
DESIGNED = {**VOICE, "id": "sailor", "name": "Sailor", "kind": "designed", "reference": None, "is_default": False}
CONFIG = {"base_url": "http://breeze.local:7860", "api_key": ""}


def multipart_fields(request: httpx.Request) -> dict[str, bytes]:
    """Split a multipart/form-data body into {field name: raw bytes}."""
    boundary = request.headers["content-type"].split("boundary=")[1].encode()
    fields = {}
    for part in request.content.split(b"--" + boundary)[1:-1]:
        head, _, value = part.strip(b"\r\n").partition(b"\r\n\r\n")
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        fields[name] = value
    return fields


def pcm(samples=4800):
    return b"".join(struct.pack("<h", int(6000 * math.sin(i * 0.05))) for i in range(samples))


class FakeBreeze:
    """An offline stand-in for the documented Breeze HTTP contract."""

    def __init__(self):
        self.voices = {"narrator": dict(VOICE), "sailor": dict(DESIGNED)}
        self.reference = REFERENCE
        self.requests = []
        self.speech = []
        self.busy = 0
        self.segments = None
        self.stream_error = None
        self.finish = True
        self.previews = {}
        self.preview_requests = []
        self.clones = []
        self.references = {}
        self.deleted = []

    def sse(self, text):
        audio_bytes = pcm()
        duration = len(audio_bytes) // 2 * 1000 // 24000
        segments = self.segments if self.segments is not None else [
            {"index": 0, "utterance_index": 0, "char_start": 0, "char_end": len(text), "text": text,
             "voice_id": "narrator", "start_ms": 0, "end_ms": duration}]
        events = [("speech.audio.delta", {"type": "speech.audio.delta", "audio": base64.b64encode(audio_bytes[:4000]).decode()}),
                  ("speech.ping", {"type": "speech.ping"}),
                  ("speech.audio.delta", {"type": "speech.audio.delta", "audio": base64.b64encode(audio_bytes[4000:]).decode()})]
        events += [("speech.segment", {"type": "speech.segment", "segment": segment}) for segment in segments]
        if self.stream_error:
            events.append(("error", {"type": "error", "error": {"type": "internal_error", "code": self.stream_error,
                                                                 "message": f"failed on: {text}"}}))
        elif self.finish:
            events.append(("speech.audio.done", {"type": "speech.audio.done", "output_format": "pcm_24000",
                                                 "duration_ms": duration, "usage": {"characters": len(text)}}))
        return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()

    def __call__(self, request: httpx.Request):
        path = request.url.path
        self.requests.append((request.method, path, request.headers.get("authorization")))
        if path == "/health":
            return httpx.Response(200, json={"status": "ready", "model": "breeze-tts-2"})
        if path == "/v1/voices":
            return httpx.Response(200, json={"object": "list", "data": list(self.voices.values()),
                                             "has_more": False, "default_voice_id": "narrator"})
        if path == "/v1/voice-previews" and request.method == "POST":
            body = json.loads(request.content)
            self.preview_requests.append(body)
            data = []
            for offset in range(body.get("count", 3)):
                preview_id = f"prv_{len(self.previews) + 1}"
                self.previews[preview_id] = wav_bytes(frames=4800 + 480 * len(self.previews))
                data.append({"object": "voice_preview", "id": preview_id, "description": body["description"],
                             "text": body.get("text"), "seed": 42 + offset, "duration_ms": 200,
                             "audio_url": f"/v1/voice-previews/{preview_id}/audio", "expires_at": "2026-09-29T00:00:00Z"})
            return httpx.Response(200, json={"object": "list", "data": data})
        if path.startswith("/v1/voice-previews/") and path.endswith("/audio"):
            audio_bytes = self.previews.get(path.split("/")[3])
            return httpx.Response(200, content=audio_bytes, headers={"content-type": "audio/wav"}) if audio_bytes else \
                httpx.Response(404, json={"error": {"code": "preview_not_found"}})
        if path == "/v1/voices/clone" and request.method == "POST":
            fields = multipart_fields(request)
            voice_id = fields["id"].decode()
            if voice_id in self.voices:
                return httpx.Response(409, json={"error": {"code": "voice_exists"}})
            self.clones.append(fields)
            self.voices[voice_id] = {**VOICE, "id": voice_id, "name": fields["name"].decode(), "is_default": False,
                                     "description": fields.get("description", b"").decode() or None,
                                     "labels": json.loads(fields.get("labels", b"{}")),
                                     "reference": {"text": fields["reference_text"].decode(), "duration_ms": 200,
                                                   "audio_url": f"/v1/voices/{voice_id}/reference"},
                                     "created_at": f"2026-09-28T05:{len(self.voices):02d}:00Z"}
            self.references[voice_id] = fields["reference_audio"]
            return httpx.Response(201, json=self.voices[voice_id])
        if path.startswith("/v1/voices/"):
            voice_id_ = path.split("/")[3]
            voice = self.voices.get(voice_id_)
            if voice is None:
                return httpx.Response(404, json={"error": {"code": "voice_not_found", "message": "gone"}})
            if request.method == "DELETE":
                self.deleted.append(voice_id_)
                del self.voices[voice_id_]
                return httpx.Response(200, json={"id": voice_id_, "deleted": True})
            if request.method == "PATCH":
                voice.update({key: value for key, value in json.loads(request.content).items() if key in ("name", "description")})
                return httpx.Response(200, json=voice)
            if path.endswith("/reference"):
                if voice["kind"] != "cloned":
                    return httpx.Response(404, json={"error": {"code": "no_reference"}})
                return httpx.Response(200, content=self.references.get(voice_id_, self.reference))
            return httpx.Response(200, json=voice)
        if path == "/v1/speech/stream":
            body = json.loads(request.content)
            self.speech.append(body)
            if self.busy:
                self.busy -= 1
                return httpx.Response(503, headers={"retry-after": "2"}, json={"error": {"code": "server_busy"}})
            if body["input"] == "too long":
                return httpx.Response(400, json={"error": {"code": "segment_too_long",
                                                           "message": f"Segment: {body['input']} secret"}})
            return httpx.Response(200, headers={"content-type": "text/event-stream", "x-request-id": "req_test"},
                                  content=self.sse(body["input"]))
        return httpx.Response(404)


@pytest.fixture
def fake_breeze(monkeypatch):
    server = FakeBreeze()
    monkeypatch.setattr(breeze, "_transport", httpx.MockTransport(server))
    monkeypatch.setattr(breeze, "_sleep", lambda seconds: None)
    return server


def pinned(**extra):
    return breeze.pin(breeze.fetch_catalog(CONFIG), "narrator", **extra)


def speak(text, selection, path, config=CONFIG):
    return audio.synthesize({"id": "s1", "text": text}, {"voices": {"breeze": selection}}, {}, "breeze", None, config, path)


@pytest.mark.parametrize("value,expected", [
    ("http://speech-box.local:7860/", "http://speech-box.local:7860"),
    ("https://10.0.0.5:8443", "https://10.0.0.5:8443"),
    ("  ", ""),
])
def test_breeze_url_accepts_only_a_server_root(value, expected):
    assert breeze.normalize_base_url(value) == expected


@pytest.mark.parametrize("value", ["ftp://host", "http://user:pw@host:1", "http://host/v1", "http://host?x=1",
                                   "host:7860", "http://host:99999"])
def test_breeze_url_rejects_paths_credentials_and_other_schemes(value):
    with pytest.raises(ValueError):
        breeze.normalize_base_url(value)


def test_catalog_pins_cloned_voices_and_rejects_designed_ones(fake_breeze):
    catalog = breeze.fetch_catalog(CONFIG)
    assert catalog["state"] == "ready" and catalog["default_voice_id"] == "narrator"
    voices = {voice["id"]: voice for voice in catalog["voices"]}
    assert voices["narrator"]["usable"] and len(voices["narrator"]["revision"]) == 64
    assert not voices["sailor"]["usable"] and "Designed voices" in voices["sailor"]["reason"]
    assert breeze.pin(catalog, None) == {"id": "narrator", "revision": voices["narrator"]["revision"], "seed": 42}
    with pytest.raises(ValueError, match="cannot narrate"):
        breeze.pin(catalog, "sailor")
    with pytest.raises(ValueError, match="Refresh Breeze voices"):
        breeze.pin(catalog, "missing")


def test_revision_tracks_speech_inputs_not_labels():
    base = breeze.voice_revision(VOICE, "a" * 64)
    assert breeze.voice_revision({**VOICE, "labels": {"x": "y"}, "description": "New", "name": "N",
                                  "updated_at": "2027-01-01T00:00:00Z"}, "a" * 64) == base
    assert breeze.voice_revision(VOICE, "b" * 64) != base
    assert breeze.voice_revision({**VOICE, "settings": {**VOICE["settings"], "temperature": .5}}, "a" * 64) != base
    assert breeze.voice_revision({**VOICE, "created_at": "2026-10-01T00:00:00Z"}, "a" * 64) != base


def test_unreachable_server_is_a_state_not_an_exception(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)
    monkeypatch.setattr(breeze, "_transport", httpx.MockTransport(refuse))
    catalog = breeze.fetch_catalog(CONFIG)
    assert catalog["state"] == "unreachable" and catalog["voices"] == []


def test_breeze_recipe_pins_revision_seed_and_direction(fake_breeze):
    selection = pinned()
    character = {"id": "c", "voices": {"breeze": selection}, "direction": "Warm."}
    base = audio.render_fingerprint(SEGMENT, character, SCENE, "breeze", None)
    recipe = audio._recipe(SEGMENT, character, SCENE, "breeze", None)
    assert recipe["voice"] == "narrator" and recipe["seed"] == 42 and recipe["model"] == "breeze-tts-2"
    assert recipe["style"] == "Character: Warm. Scene mood: Hushed. Scene: Slow. Passage: Quietly. Cues: pause."
    assert audio.render_fingerprint({**SEGMENT, "seed": 7}, character, SCENE, "breeze", None) != base
    changed = {**character, "voices": {"breeze": {**selection, "revision": "f" * 64}}}
    assert audio.render_fingerprint(SEGMENT, changed, SCENE, "breeze", None) != base
    with pytest.raises(AudioError, match="Choose a Breeze voice"):
        audio.render_fingerprint(SEGMENT, {"id": "c"}, SCENE, "breeze", None)
    with pytest.raises(AudioError, match="Refresh Breeze voices"):
        audio.render_fingerprint(SEGMENT, {"voices": {"breeze": {"id": "narrator"}}}, SCENE, "breeze", None)
    with pytest.raises(AudioError, match="longer than Breeze accepts"):
        audio.render_fingerprint(SEGMENT, {**character, "direction": "x" * 1001}, SCENE, "breeze", None)


def test_breeze_take_streams_validates_and_keeps_sentence_timing(fake_breeze, tmp_path):
    selection = pinned()
    text = "The lamp \U0001F56F️ flickered (sigh) twice."
    metadata = speak(text, selection, tmp_path / "take.wav")
    assert validate_audio(tmp_path / "take.wav") == pytest.approx(0.2)
    sent = fake_breeze.speech[-1]
    assert sent["input"] == text and sent["voice"] == "narrator" and sent["speed"] == 1.0
    assert sent["settings"] == {"seed": 42} and sent["output_format"] == "pcm_24000" and sent["stream_format"] == "sse"
    assert "instruction" not in sent
    assert metadata["provider_timing"]["segments"] == [{"char_start": 0, "char_end": len(text), "start": 0.0, "end": 0.2}]
    assert metadata["breeze"] == {"request_id": "req_test", "timing_accepted": True, "vocal_event_markup": ["(sigh)"]}
    assert metadata["fingerprint"] == audio.render_fingerprint({"id": "s1", "text": text}, {"voices": {"breeze": selection}},
                                                               {}, "breeze", None)


def test_breeze_rejects_timing_that_does_not_match_the_sent_text(fake_breeze, tmp_path):
    selection = pinned()
    fake_breeze.segments = [{"index": 0, "utterance_index": 0, "char_start": 0, "char_end": 5, "text": "Other",
                             "start_ms": 0, "end_ms": 100}]
    metadata = speak("Hello there.", selection, tmp_path / "take.wav")
    assert metadata["provider_timing"] is None and metadata["breeze"]["timing_accepted"] is False


def test_breeze_refuses_to_send_when_the_voice_changed_on_the_server(fake_breeze, tmp_path):
    selection = pinned()
    fake_breeze.reference = b"a different reference clip"
    with pytest.raises(AudioError, match="changed on the server"):
        speak("Hello.", selection, tmp_path / "take.wav")
    del fake_breeze.voices["narrator"]
    with pytest.raises(AudioError, match="no longer on the server"):
        speak("Hello.", selection, tmp_path / "take.wav")
    assert fake_breeze.speech == [] and not (tmp_path / "take.wav").exists()


def test_breeze_waits_out_a_busy_server_then_stops_retrying(fake_breeze, tmp_path):
    selection = pinned()
    fake_breeze.busy = 2
    speak("Hello.", selection, tmp_path / "take.wav")
    assert len(fake_breeze.speech) == 3
    fake_breeze.busy = 10
    with pytest.raises(AudioError, match=r"HTTP 503 \(server_busy\)"):
        speak("Hello.", selection, tmp_path / "take2.wav")
    assert len(fake_breeze.speech) == 3 + 1 + breeze.MAX_BUSY_RETRIES


def test_breeze_errors_keep_the_code_but_never_echo_server_text(fake_breeze, tmp_path):
    selection = pinned()
    with pytest.raises(AudioError) as error:
        speak("too long", selection, tmp_path / "take.wav")
    assert "segment_too_long" in str(error.value) and "secret" not in str(error.value)
    fake_breeze.stream_error = "generation_failed"
    with pytest.raises(AudioError) as error:
        speak("Private words.", selection, tmp_path / "take.wav")
    assert "generation_failed" in str(error.value) and "Private words" not in str(error.value)
    fake_breeze.stream_error, fake_breeze.finish = None, False
    with pytest.raises(AudioError, match="ended before the take was complete"):
        speak("Hello.", selection, tmp_path / "take.wav")
    assert not (tmp_path / "take.wav").exists()


def test_breeze_sends_the_optional_api_key_as_a_bearer_token(fake_breeze, tmp_path):
    config = {**CONFIG, "api_key": "breeze-secret"}
    selection = breeze.pin(breeze.fetch_catalog(config), "narrator")
    speak("Hello.", selection, tmp_path / "take.wav", config)
    assert all(auth == "Bearer breeze-secret" for _, path, auth in fake_breeze.requests if path.startswith("/v1/"))


def test_simple_listening_cache_keys_separate_breeze_voice_revisions(tmp_path, fake_breeze):
    store = Store(tmp_path)
    book = parse_book("story.txt", b"Chapter One\n\nHello there.")
    store.save_book(book)
    repository = ListeningRepository(store)
    selection = pinned()
    first = repository.session(book["id"], "breeze", "narrator", None, selection=selection)
    second = repository.session(book["id"], "breeze", "narrator", None, selection={**selection, "revision": "e" * 64})
    passage = {"id": book["segments"][0]["id"], "text": "Hello there."}
    assert first["id"] != second["id"]
    assert ListeningRepository._synthesis_key(passage, first) != ListeningRepository._synthesis_key(passage, second)
    with pytest.raises(ValueError, match="Breeze voice"):
        repository.session(book["id"], "breeze", "narrator", None)


# Breeze through the app -----------------------------------------------------

def _clear_environment(monkeypatch):
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "BREEZE_API_KEY", "BREEZE_TTS_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("bardic.app.list_system_voices", lambda: [])


@pytest.fixture
def breeze_client(tmp_path, monkeypatch, fake_breeze):
    _clear_environment(monkeypatch)
    with TestClient(create_app(tmp_path)) as client:
        yield client


def connect(client):
    response = client.post("/api/settings", json={"breeze_url": "http://breeze.local:7860/"})
    assert response.status_code == 200, response.text
    assert response.json()["breeze"]["state"] == "unchecked"
    view = client.post("/api/narration/breeze/refresh").json()
    assert view["state"] == "ready", view
    return view


def test_status_describes_breeze_without_contacting_it(breeze_client, fake_breeze):
    status = breeze_client.get("/api/status").json()
    assert status["breeze"]["state"] == "unconfigured"
    assert status["narration_providers"]["breeze"]["capabilities"]["seeded_takes"] is True
    assert status["narration_providers"]["gemini"]["capabilities"]["chunked_listening"] is True
    assert next(p for p in status["providers"] if p["id"] == "breeze")["available"] is False
    assert "breeze_catalog" not in status and fake_breeze.requests == []
    assert breeze_client.post("/api/settings", json={"breeze_url": "http://host/path"}).status_code == 400
    view = connect(breeze_client)
    assert view["base_url"] == "http://breeze.local:7860" and view["has_api_key"] is False
    assert next(p for p in breeze_client.get("/api/status").json()["providers"] if p["id"] == "breeze")["available"] is True
    breeze_client.post("/api/settings", json={"breeze_api_key": "server-secret"})
    status = breeze_client.get("/api/status").json()
    assert status["breeze"]["has_api_key"] is True and "server-secret" not in json.dumps(status)


def test_breeze_simple_listening_survives_restart_while_server_is_offline(tmp_path, monkeypatch, fake_breeze):
    _clear_environment(monkeypatch)
    with TestClient(create_app(tmp_path)) as client:
        connect(client)
        book = import_text(client)
        segment = book["segments"][0]
        started = client.post(f"/api/books/{book['id']}/listen", json={"provider": "breeze", "voice": "narrator",
                                                                          "model": None, "segment_id": segment["id"]}).json()
        finished = wait_job(client, started["job"]["id"])
        assert finished["status"] == "completed", finished
        assert finished["audio"]["provider"] == "breeze" and finished["audio"]["breeze"]["timing_accepted"] is True
        session = started["session"]
        assert session["voice_revision"] and session["seed"] == 42
    sent = len(fake_breeze.speech)

    def offline(request):
        raise httpx.ConnectError("offline", request=request)
    monkeypatch.setattr(breeze, "_transport", httpx.MockTransport(offline))
    with TestClient(create_app(tmp_path)) as client:
        again = client.post(f"/api/books/{book['id']}/listen", json={"provider": "breeze", "voice": "narrator",
                                                                        "segment_id": segment["id"]})
        assert again.status_code == 200, again.text
        assert again.json()["cached"] is True and again.json()["session"]["id"] == session["id"]
        assert client.get(again.json()["audio"]["url"]).status_code == 200
    assert len(fake_breeze.speech) == sent


def test_breeze_cast_voices_are_pinned_per_provider_and_enhanced_takes_stay_valid(breeze_client, fake_breeze):
    connect(breeze_client)
    book = import_text(breeze_client)
    narrator = next(c for c in book["characters"] if c["id"] == "narrator")
    assert narrator["voices"] == {"gemini": {"id": "Kore"}}
    segment = next(s for s in book["segments"] if s["speaker_id"] == "narrator")
    # Without a default voice, a character with no Breeze choice cannot render.
    runtime = breeze_client.app.state.runtime
    saved_default = runtime.preferences["narration_defaults"]["breeze"]
    runtime.preferences["narration_defaults"]["breeze"] = None
    blocked = breeze_client.post(f"/api/books/{book['id']}/render", json={"provider": "breeze", "segment_id": segment["id"]})
    assert blocked.status_code == 400 and "Narrator" in blocked.json()["detail"] and "default Breeze voice" in blocked.json()["detail"]
    runtime.preferences["narration_defaults"]["breeze"] = saved_default
    assert breeze_client.patch(f"/api/books/{book['id']}/characters/narrator",
                               json={"voices": {"breeze": {"id": "sailor"}}}).status_code == 400
    edited = breeze_client.patch(f"/api/books/{book['id']}/characters/narrator",
                                 json={"voices": {"breeze": {"id": "narrator", "seed": 11}}, "voice": "Puck"}).json()
    voices = next(c for c in edited["characters"] if c["id"] == "narrator")["voices"]
    assert voices["gemini"] == {"id": "Puck"} and voices["breeze"]["seed"] == 11 and len(voices["breeze"]["revision"]) == 64
    stored = next(c for c in breeze_client.app.state.runtime.store.book(book["id"])["characters"] if c["id"] == "narrator")
    assert "voice" not in stored and "system_voice" not in stored and voice_id(stored, "gemini") == "Puck"
    job = breeze_client.post(f"/api/books/{book['id']}/render", json={"provider": "breeze", "segment_id": segment["id"]}).json()
    assert wait_job(breeze_client, job["id"])["status"] == "completed"
    assert fake_breeze.speech[-1]["settings"] == {"seed": 11}
    presented = breeze_client.get(f"/api/books/{book['id']}").json()
    take = next(s for s in presented["segments"] if s["id"] == segment["id"])["audio"]
    assert take and take["provider"] == "breeze" and take["voice"] == "narrator"
    # A new seed on the passage is a new take; the old audio stays in history.
    retake = breeze_client.patch(f"/api/books/{book['id']}/segments/{segment['id']}", json={"seed": 12345}).json()
    assert next(s for s in retake["segments"] if s["id"] == segment["id"])["audio"] is None
    job = breeze_client.post(f"/api/books/{book['id']}/render", json={"provider": "breeze", "segment_id": segment["id"]}).json()
    assert wait_job(breeze_client, job["id"])["status"] == "completed"
    assert fake_breeze.speech[-1]["settings"] == {"seed": 12345}
    cleared = breeze_client.patch(f"/api/books/{book['id']}/characters/narrator", json={"voices": {"breeze": None}}).json()
    assert "breeze" not in next(c for c in cleared["characters"] if c["id"] == "narrator")["voices"]


def test_breeze_voice_example_pins_the_voice_in_its_retained_recipe(breeze_client, fake_breeze):
    connect(breeze_client)
    book = import_text(breeze_client)
    started = breeze_client.post(f"/api/books/{book['id']}/voice-preview", json={"provider": "breeze", "voice": "narrator"})
    assert started.status_code == 200, started.text
    assert wait_job(breeze_client, started.json()["job"]["id"])["status"] == "completed"
    again = breeze_client.post(f"/api/books/{book['id']}/voice-preview", json={"provider": "breeze", "voice": "narrator"}).json()
    assert again["cached"] is True and len(fake_breeze.speech) == 1
    missing = breeze_client.post(f"/api/books/{book['id']}/voice-preview", json={"provider": "breeze", "voice": "nobody"})
    assert missing.status_code == 400 and "Refresh Breeze voices" in missing.json()["detail"]


def test_breeze_job_errors_redact_the_server_key(breeze_client, fake_breeze, monkeypatch):
    connect(breeze_client)
    breeze_client.post("/api/settings", json={"breeze_api_key": "server-secret"})
    book = import_text(breeze_client)

    def leak(*args, **kwargs):
        raise AudioError("failed with server-secret in it")
    monkeypatch.setattr("bardic.breeze.generate", leak)
    started = breeze_client.post(f"/api/books/{book['id']}/listen", json={"provider": "breeze", "voice": "narrator",
                                                                           "segment_id": book["segments"][0]["id"]}).json()
    failed = wait_job(breeze_client, started["job"]["id"])
    assert failed["status"] == "failed" and "server-secret" not in failed["error"] and "[redacted]" in failed["error"]
