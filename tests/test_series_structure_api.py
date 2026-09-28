"""Reviewable series links and lossless refresh of existing library structure."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from spintails.app import create_app
from spintails.staged_analysis import fingerprint
from test_structure import epub, toc


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("spintails.app.list_system_voices", lambda: [])
    with TestClient(create_app(tmp_path)) as test_client:
        yield test_client


def imported(client, name="story.txt", data=b"Mara whispered."):
    response = client.post("/api/books", files={"file":(name,data)})
    assert response.status_code == 200, response.text
    return response.json()


def character(client, book_id):
    result = client.post(f"/api/books/{book_id}/characters", json={"name":"Mara"})
    assert result.status_code == 200
    return next(c for c in result.json()["characters"] if c["name"] == "Mara")


def test_series_membership_identity_link_and_unlink(client):
    book = imported(client)
    person = character(client,book["id"])
    series = client.post("/api/series",json={"name":"  Example Cycle  "}).json()
    assert series["name"] == "Example Cycle"
    assert client.get("/api/series").json()[0]["id"] == series["id"]
    membership = client.put(f"/api/books/{book['id']}/series",json={"series_id":series["id"],"position":9}).json()
    assert membership["membership"]["position"] == 9
    assert membership["series"]["id"] == series["id"] and membership["links"] == []
    identity = client.post(f"/api/series/{series['id']}/characters",json={"name":"Mara"}).json()
    url = f"/api/books/{book['id']}/series/characters/{person['id']}"
    assert client.put(url,json={"series_character_id":identity["id"]}).json()["series_character_id"] == identity["id"]
    view = client.get(f"/api/books/{book['id']}/series").json()
    assert view["links"][0]["character_id"] == person["id"]
    assert view["characters"][0]["id"] == identity["id"]
    context = client.get(f"/api/books/{book['id']}/series/context").json()
    assert context["series"]["id"] == series["id"] and context["included_observations"] == 0
    assert client.put(url,json={"series_character_id":None}).json()["linked"] is False
    removed = client.put(f"/api/books/{book['id']}/series",json={"series_id":None}).json()
    assert removed == {"membership":None,"series":None,"characters":[],"links":[]}


def test_series_validation_and_missing_resources(client):
    one, two = imported(client), imported(client)
    series = client.post("/api/series",json={"name":"Example"}).json()
    assert client.post("/api/series",json={"name":"example"}).status_code == 400
    assert client.post("/api/series",json={"name":"   "}).status_code == 400
    assert client.put(f"/api/books/{one['id']}/series",json={"series_id":series["id"],"position":1}).status_code == 200
    assert client.put(f"/api/books/{two['id']}/series",json={"series_id":series["id"],"position":1}).status_code == 400
    assert client.put(f"/api/books/{two['id']}/series",json={"series_id":series["id"],"position":-1}).status_code == 400
    assert client.put(f"/api/books/{two['id']}/series",json={"position":2}).status_code == 400
    assert client.get("/api/books/missing/series").status_code == 404
    assert client.get("/api/series/missing/characters").status_code == 404
    assert client.put(f"/api/books/{one['id']}/series/characters/narrator",json={"series_character_id":"anything"}).status_code == 400


def test_active_job_prevents_membership_links_and_structure_changes(client):
    book = imported(client)
    client.app.state.runtime.store.create_job(book["id"],"analyze")
    assert client.put(f"/api/books/{book['id']}/series",json={}).status_code == 409
    assert client.put(f"/api/books/{book['id']}/series/characters/narrator",json={}).status_code == 409
    assert client.post(f"/api/books/{book['id']}/repair-structure").status_code == 409


def legacy_saved(client):
    data = epub({"recap":"<p>Mara whispered.</p>"},nav=toc([("recap.xhtml","The Story Thus Far")]))
    book = imported(client,"volume.epub",data)
    store = client.app.state.runtime.store
    book = store.book(book["id"])
    book.pop("structure_version",None)
    for chapter in book["chapters"]:
        for key in list(chapter):
            if key not in {"id","index","title","text"}:
                chapter.pop(key)
        chapter["title"] = "Chapter 4"
    book["characters"].append({"id":"mara","name":"Mara","aliases":[],"description":"Quiet","direction":"Softly","voice":"Kore","system_voice":""})
    book["segments"][0]["audio"] = {"fingerprint":"retained-take","duration":2.}
    ref = {"id":"ref","character_id":"mara","chapter_id":book["chapters"][0]["id"],"segment_id":book["segments"][0]["id"],
           "start":0,"end":4,"quote":"Mara","kind":"profile_evidence"}
    checkpoint = {"provider":"openai","model":"gpt-6-sol","status":"failed","stage":"discovery", "working_book":deepcopy(book),
                  "chapters":[{"id":book["chapters"][0]["id"],"title":"Chapter 4"}],"units":{"paid-result":{"stage":"discovery","result":{"characters":[]}}},"references":[ref]}
    store.commit_analysis(book,fingerprint(book,"openai","gpt-6-sol"),checkpoint)
    return book,checkpoint


def test_structure_refresh_preserves_source_audio_profiles_and_paid_checkpoint(client):
    book, checkpoint = legacy_saved(client)
    response = client.post(f"/api/books/{book['id']}/repair-structure")
    assert response.status_code == 200, response.text
    store = client.app.state.runtime.store
    updated = store.book(book["id"])
    assert updated["chapters"][0]["title"] == "The Story Thus Far"
    assert updated["chapters"][0]["kind"] == "recap"
    assert updated["chapters"][0]["text"] == book["chapters"][0]["text"]
    assert updated["segments"] == book["segments"] and updated["characters"] == book["characters"]
    migrated = store.analysis_checkpoint(book["id"],fingerprint(updated,"openai","gpt-6-sol"))
    assert migrated["units"] == checkpoint["units"]
    assert migrated["references"] == checkpoint["references"]
    assert migrated["working_book"]["chapters"][0]["title"] == "The Story Thus Far"
    assert store.character_references(book["id"]) == checkpoint["references"]


def test_structure_refresh_refuses_changed_source_without_losing_saved_work(client):
    book, checkpoint = legacy_saved(client)
    store = client.app.state.runtime.store
    old_fingerprint = store.analysis_status(book["id"])["fingerprint"]
    (store.root/"originals"/book["id"]/"source.epub").write_bytes(epub({"recap":"<p>Different source.</p>"}))
    response = client.post(f"/api/books/{book['id']}/repair-structure")
    assert response.status_code == 400 and "preserved" in response.text
    assert store.book(book["id"]) == book
    assert store.analysis_status(book["id"])["fingerprint"] == old_fingerprint


def test_structure_refresh_requires_original_and_local_origin(client):
    book = imported(client)
    path = client.app.state.runtime.store.root/"originals"/book["id"]/"source.txt"
    path.unlink()
    assert client.post(f"/api/books/{book['id']}/repair-structure").status_code == 400
    assert client.post(f"/api/books/{book['id']}/repair-structure",headers={"origin":"https://foreign.example"}).status_code == 403
    assert client.post("/api/books/missing/repair-structure").status_code == 404
