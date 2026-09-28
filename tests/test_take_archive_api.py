"""Immutable takes survive retries while the selected take drives playback."""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from bardic.app import create_app
from bardic.audio import AudioError, render_fingerprint
from test_app import import_text, wait_job
from test_take_archive import fake_renderer, wav_bytes


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("bardic.app.list_system_voices", lambda: [])
    with TestClient(create_app(tmp_path)) as client:
        client.post("/api/settings", json={"api_keys": {"gemini": "fake-key"}})
        yield client


def render(client, book_id, **options):
    response = client.post(f"/api/books/{book_id}/render", json={"provider": "gemini", **options})
    assert response.status_code == 200, response.text
    return wait_job(client, response.json()["id"])


def test_forced_takes_remain_accessible_and_latest_take_is_exported(client, monkeypatch):
    book = import_text(client, "The lamp flickered.")
    base = f"/api/books/{book['id']}"
    takes, recipes = [], []
    for sample in (16, 32):
        data = wav_bytes(sample)
        monkeypatch.setattr("bardic.app.synthesize", fake_renderer(data))
        assert render(client, book["id"], force=True)["status"] == "completed"
        take = client.get(base).json()["segments"][0]["audio"]
        takes.append(take)
        recipes.append(client.app.state.runtime.store.book(book["id"])["segments"][0]["audio"]["fingerprint"])
        assert client.get(take["url"]).content == data
        assert "fingerprint" not in take, "the recipe fingerprint stays in storage"
    assert recipes[0] == recipes[1]
    assert takes[0]["asset_id"] != takes[1]["asset_id"]
    assert takes[0]["url"] != takes[1]["url"]
    for take, sample in zip(takes, (16, 32)):
        archived = client.get(f"{base}/audio-assets/{take['asset_id']}")
        assert archived.status_code == 200
        assert archived.content == wav_bytes(sample)
    with zipfile.ZipFile(io.BytesIO(client.get(base + "/export").content)) as archive:
        assert archive.read(f"takes/{book['segments'][0]['id']}.wav") == wav_bytes(32)
        assert archive.read("chapters/001.wav").endswith(wav_bytes(32)[44:])


def test_no_force_reuses_selected_asset_before_legacy_recipe_cache(client, monkeypatch):
    book = import_text(client, "The lamp flickered.")
    runtime = client.app.state.runtime
    segment = book["segments"][0]
    character = next(c for c in book["characters"] if c["id"] == segment["speaker_id"])
    scene = next(s for s in book["scenes"] if s["id"] == segment["scene_id"])
    fingerprint = render_fingerprint(segment, character, scene, "gemini", runtime.preferences["tts_model"])
    legacy = runtime.audio_path(book["id"], fingerprint)
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(wav_bytes(16))
    calls = []
    monkeypatch.setattr("bardic.app.synthesize", fake_renderer(wav_bytes(32), calls))
    assert render(client, book["id"])["status"] == "completed"
    assert calls == []  # Pre-archive libraries retain their recipe cache.
    base = f"/api/books/{book['id']}"
    original = client.get(base).json()["segments"][0]["audio"]
    assert original["asset_id"] is None, "a recipe-named legacy file is not content-addressed"
    assert client.get(original["url"]).content == wav_bytes(16)
    assert render(client, book["id"], force=True)["status"] == "completed"
    selected = client.get(base).json()["segments"][0]["audio"]
    assert render(client, book["id"])["status"] == "completed"
    assert len(calls) == 1
    assert client.get(base).json()["segments"][0]["audio"] == selected
    assert client.get(selected["url"]).content == wav_bytes(32)
    assert legacy.read_bytes() == wav_bytes(16)


def test_failed_force_keeps_selected_take_and_removes_partial_output(client, monkeypatch):
    book = import_text(client, "The lamp flickered.")
    base = f"/api/books/{book['id']}"
    monkeypatch.setattr("bardic.app.synthesize", fake_renderer(wav_bytes()))
    assert render(client, book["id"])["status"] == "completed"
    before = client.get(base).json()["segments"][0]["audio"]

    def fail(*args):
        args[-1].write_bytes(b"unfinished")
        raise AudioError("Provider stopped")

    monkeypatch.setattr("bardic.app.synthesize", fail)
    assert render(client, book["id"], force=True)["status"] == "failed"
    assert client.get(base).json()["segments"][0]["audio"] == before
    assert client.get(before["url"]).content == wav_bytes()
    directory = client.app.state.runtime.take_path(book["id"], before).parent
    assert [p.name for p in directory.iterdir()] == [f"{before['asset_id']}.wav"]


def test_edit_invalidates_selected_playback_but_retains_archived_audio(client, monkeypatch):
    book = import_text(client, "The lamp flickered.")
    base = f"/api/books/{book['id']}"
    monkeypatch.setattr("bardic.app.synthesize", fake_renderer(wav_bytes()))
    assert render(client, book["id"])["status"] == "completed"
    before = client.get(base).json()["segments"][0]["audio"]
    edited = client.patch(f"{base}/segments/{book['segments'][0]['id']}", json={"direction": "Speak slowly"})
    assert edited.status_code == 200
    assert edited.json()["segments"][0]["audio"] is None
    assert client.get(before["url"]).status_code == 404
    assert client.get(f"{base}/audio-assets/{before['asset_id']}").content == wav_bytes()
    assert client.get(f"{base}/audio-assets/not-an-id").status_code == 404
    assert client.get(f"{base}/audio-assets/{'0' * 64}").status_code == 404


def test_restoring_performance_reuses_archived_take_without_provider_call(client, monkeypatch):
    book = import_text(client, 'The lamp flickered.')
    base = f"/api/books/{book['id']}"
    segment = book['segments'][0]
    monkeypatch.setattr('bardic.app.synthesize', fake_renderer(wav_bytes(16)))
    assert render(client, book['id'])['status'] == 'completed'
    original = client.get(base).json()['segments'][0]['audio']
    assert client.patch(f"{base}/segments/{segment['id']}", json={'direction': 'Speak slowly'}).status_code == 200
    monkeypatch.setattr('bardic.app.synthesize', fake_renderer(wav_bytes(32)))
    assert render(client, book['id'])['status'] == 'completed'
    assert client.patch(f"{base}/segments/{segment['id']}", json={'direction': segment.get('direction', '')}).status_code == 200
    def no_generation(*args):
        pytest.fail('Restored performance must reuse its retained audio')
    monkeypatch.setattr('bardic.app.synthesize', no_generation)
    job = render(client, book['id'])
    assert job['status'] == 'completed', job
    assert client.get(base).json()['segments'][0]['audio'] == original
