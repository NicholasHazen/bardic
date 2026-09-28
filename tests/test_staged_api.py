"""Public chapter analysis controls, durable progress and source reference routes."""
import copy
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from spintails.app import create_app


STORY = ('Chapter One\n\nMara lit the lamp.\n\n“Hello,” Mara said.\n\n'
         'Chapter Two\n\nElio opened the gate.\n\n“Goodbye,” Elio said.\n')


@pytest.fixture
def client(tmp_path, monkeypatch):
    for variable in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr("spintails.app.list_system_voices", lambda: [])

    def no_network(*_args, **_kwargs):
        pytest.fail("Staged API tests must not make real network requests")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    with TestClient(create_app(tmp_path)) as test_client:
        yield test_client


def import_book(client):
    response = client.post("/api/books", files={"file": ("stages.txt", STORY.encode(), "text/plain")})
    assert response.status_code == 200, response.text
    book = response.json()
    assert len(book["chapters"]) == 2
    return book


def wait_job(client, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = next(item for item in client.get("/api/jobs").json() if item["id"] == job_id)
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(.01)
    pytest.fail("analysis worker did not finish")


def test_analysis_summary_is_read_only_and_starts_with_pending_chapters(client, monkeypatch):
    book = import_book(client)

    def unexpected_analysis(*_args, **_kwargs):
        pytest.fail("Reading progress must not start analysis")

    monkeypatch.setattr("spintails.app.analyze_book", unexpected_analysis)
    for _ in range(2):
        response = client.get(f"/api/books/{book['id']}/analysis")
        assert response.status_code == 200
        summary = response.json()
        assert summary["status"] == "not_started"
        assert summary["completed_units"] == summary["total_units"] == 0
        assert [chapter["id"] for chapter in summary["chapters"]] == [chapter["id"] for chapter in book["chapters"]]
        assert all(chapter["status"] == "pending" for chapter in summary["chapters"])
        assert not any(chapter["discovery_complete"] or chapter["directing_complete"] for chapter in summary["chapters"])
        assert not {"working_book", "units", "references", "api_key"} & summary.keys()
    assert client.get("/api/jobs").json() == []
    assert client.app.state.runtime.store.analysis_status(book["id"]) is None
    assert client.get("/api/books/not-a-book/analysis").status_code == 404


@pytest.mark.parametrize("provider", ["local", "gemini", "openai", "anthropic"])
@pytest.mark.parametrize("resume", [None, False])
def test_analysis_request_routes_chapter_scope_and_resume_choice(client, monkeypatch, provider, resume):
    book = import_book(client)
    chapter_id = book["chapters"][1]["id"]
    calls = []

    def fake_analysis(source, selected_provider, key, model, progress, cancelled, **options):
        calls.append((selected_provider, key, model, options))
        return copy.deepcopy(source)

    monkeypatch.setattr("spintails.app.analyze_book", fake_analysis)
    if provider != "local":
        saved = client.post("/api/settings", json={
            "api_keys": {provider: "test-selected-credential"},
            "analysis_models_by_provider": {provider: "test-selected-model"},
        })
        assert saved.status_code == 200
    body = {"provider": provider, "chapter_id": chapter_id}
    if resume is not None:
        body["resume"] = resume
    response = client.post(f"/api/books/{book['id']}/analyze", json=body)
    assert response.status_code == 200, response.text
    job = wait_job(client, response.json()["id"])
    assert job["status"] == "completed", job
    assert job["chapter_id"] == chapter_id
    assert len(calls) == 1
    selected_provider, key, model, options = calls[0]
    assert selected_provider == provider
    assert key == ("" if provider == "local" else "test-selected-credential")
    assert model == (None if provider == "local" else "test-selected-model")
    assert options["chapter_id"] == chapter_id
    assert options["resume"] is (True if resume is None else False)
    assert options["store"] is client.app.state.runtime.store
    assert callable(options["prepare"])


@pytest.mark.parametrize("body,status", [
    ({"provider": "local", "chapter_id": "not-this-book"}, 400),
    ({"provider": "local", "chapter_id": 123}, 422),
    ({"provider": "local", "resume": "not-a-boolean"}, 422),
    ({"provider": "local", "restart_everything": True}, 422),
    ({"provider": "unknown"}, 400),
    ({"provider": "openai"}, 400),
])
def test_invalid_analysis_options_never_queue_work(client, body, status):
    book = import_book(client)
    response = client.post(f"/api/books/{book['id']}/analyze", json=body)
    assert response.status_code == status, response.text
    assert client.get("/api/jobs").json() == []


def test_local_chapter_scope_preserves_other_chapter_and_exposes_anchored_references(client):
    book = import_book(client)
    base = f"/api/books/{book['id']}"
    first, second = book["chapters"]
    other_before = [segment for segment in book["segments"] if segment["chapter_id"] == second["id"]]
    response = client.post(f"{base}/analyze", json={"provider": "local", "chapter_id": first["id"]})
    assert response.status_code == 200, response.text
    assert wait_job(client, response.json()["id"])["status"] == "completed"

    updated = client.get(base).json()
    assert updated["chapters"] == book["chapters"]
    assert [segment for segment in updated["segments"] if segment["chapter_id"] == second["id"]] == other_before
    assert not any(character["name"] == "Elio" for character in updated["characters"])
    summary = client.get(f"{base}/analysis").json()
    assert summary["status"] == "completed"
    assert summary["scope_chapter_id"] == first["id"]
    assert summary["completed_units"] == summary["total_units"] > 0
    rows = {row["id"]: row for row in summary["chapters"]}
    assert rows[first["id"]]["discovery_complete"] and rows[first["id"]]["directing_complete"]
    assert not rows[second["id"]]["discovery_complete"]
    assert not rows[second["id"]]["directing_complete"]

    mara = next(character for character in updated["characters"] if character["name"] == "Mara")
    references = client.get(f"{base}/characters/{mara['id']}/references")
    assert references.status_code == 200
    refs = references.json()
    assert refs
    assert {reference["kind"] for reference in refs} >= {"mention", "dialogue"}
    for reference in refs:
        assert reference["character_id"] == mara["id"]
        chapter = next(chapter for chapter in updated["chapters"] if chapter["id"] == reference["chapter_id"])
        assert reference["quote"] == chapter["text"][reference["start"]:reference["end"]]
        assert any(segment["id"] == reference["segment_id"] for segment in updated["segments"])
    assert client.get(f"{base}/characters/narrator/references").json() == []
    assert client.get(f"{base}/characters/not-a-character/references").status_code == 404
    assert client.get(f"/api/books/not-a-book/characters/{mara['id']}/references").status_code == 404


def profile(name):
    return {"name": name, "aliases": [], "description": "Vocal traits are unspecified.",
            "direction": "Read naturally.", "evidence": [f"{name} said"]}


def test_cloud_failure_exposes_validated_chapter_and_resume_reuses_it(client, monkeypatch):
    book = import_book(client)
    base = f"/api/books/{book['id']}"
    first, second = book["chapters"]
    client.post("/api/settings", json={"api_keys": {"openai": "test-openai-secret"},
                                      "analysis_models_by_provider": {"openai": "test-model"},
                                      "preprocess_models_by_provider": {"openai": "test-model"}})
    fail_second = True
    discoveries = []

    def fake_request(_client, model, key, prompt, schema, cancelled):
        assert model == "test-model" and key == "test-openai-secret"
        if "BOOK EXCERPT:\n" in prompt:
            name = "Mara" if "Chapter One" in prompt else "Elio"
            discoveries.append(name)
            if name == "Elio" and fail_second:
                raise ValueError("Simulated provider interruption")
            return {"characters": [profile(name)]}
        if "CANDIDATES:\n" in prompt:
            candidates = json.JSONDecoder().raw_decode(prompt.split("CANDIDATES:\n", 1)[1])[0]
            return {"characters": [profile(candidate["name"]) for candidate in candidates]}
        passages = json.loads(prompt.split("\nPASSAGES:\n", 1)[1].split("\nCONTEXT AFTER:\n", 1)[0])
        return {"summary": "A brief meeting.", "tone": "Quiet", "direction": "Natural pacing.",
                "scene_starts": [], "segments": [
                    {"id": passage["id"], "speaker_id": "narrator" if passage["kind"] == "narration" else "unassigned",
                     "confidence": 1 if passage["kind"] == "narration" else 0,
                     "direction": "", "cues": [], "evidence": []} for passage in passages]}

    monkeypatch.setattr("spintails.analysis._openai_request", fake_request)
    response = client.post(f"{base}/analyze", json={"provider": "openai", "phase": "full"})
    assert response.status_code == 200, response.text
    failed = wait_job(client, response.json()["id"])
    assert failed["status"] == "failed", failed
    assert "Accepted analysis steps are saved" in failed["error"]
    summary = client.get(f"{base}/analysis").json()
    assert summary["status"] == "failed"
    assert summary["completed_units"] == 1
    assert summary["current_chapter_id"] == second["id"]
    rows = {row["id"]: row for row in summary["chapters"]}
    assert rows[first["id"]]["discovery_complete"]
    assert rows[second["id"]]["status"] == "failed"
    assert not rows[second["id"]]["discovery_complete"]
    assert not {"working_book", "units", "references", "api_key"} & summary.keys()
    assert "test-openai-secret" not in json.dumps(summary)

    partial = client.get(base).json()
    mara = next(character for character in partial["characters"] if character["name"] == "Mara")
    refs = client.get(f"{base}/characters/{mara['id']}/references").json()
    evidence = next(reference for reference in refs if reference["kind"] == "profile_evidence")
    assert evidence["chapter_id"] == first["id"]
    assert evidence["quote"] == first["text"][evidence["start"]:evidence["end"]] == "Mara said"
    assert partial["analysis"]["status"] == "partial"

    fail_second = False
    resumed = client.post(f"{base}/analyze", json={"provider": "openai", "phase": "full"})
    assert resumed.status_code == 200, resumed.text
    completed = wait_job(client, resumed.json()["id"])
    assert completed["status"] == "completed", completed
    assert discoveries == ["Mara", "Elio", "Elio"], "validated first-chapter discovery must be reused"
    final_summary = client.get(f"{base}/analysis").json()
    assert final_summary["status"] == "completed"
    assert all(row["discovery_complete"] and row["directing_complete"] for row in final_summary["chapters"])
    final = client.get(base).json()
    assert [segment["text"] for segment in final["segments"]] == [segment["text"] for segment in book["segments"]]
    assert {character["name"] for character in final["characters"]} >= {"Mara", "Elio"}
