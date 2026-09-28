"""Offline checks of book-edit routes the rest of the suite does not exercise.

Every response here is also validated against the published contract by
tests/conftest.py.
"""
import pytest
from fastapi.testclient import TestClient

from bardic.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY',
                 'BREEZE_TTS_URL', 'BREEZE_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_scene_edit_records_changed_fields_and_returns_the_book(client):
    book = client.post('/api/demo').json()
    scene = book['scenes'][0]
    response = client.patch(f"/api/books/{book['id']}/scenes/{scene['id']}",
                            json={'title': 'At the lighthouse', 'tone': scene['tone'], 'summary': None})
    assert response.status_code == 200, response.text
    updated = response.json()
    edited = next(s for s in updated['scenes'] if s['id'] == scene['id'])
    assert edited['title'] == 'At the lighthouse'
    assert edited['summary'] == scene['summary']  # null is ignored
    assert edited['edited'] is True and edited['edited_fields'] == ['title']  # unchanged tone is not locked
    assert updated['revision'] == book['revision'] + 1
    assert [s['text'] for s in updated['segments']] == [s['text'] for s in book['segments']]


def test_scene_edit_rejects_unknown_items_and_archived_books(client):
    book = client.post('/api/demo').json()
    scene_id = book['scenes'][0]['id']
    assert client.patch(f"/api/books/{book['id']}/scenes/scene_missing", json={'title': 'X'}).status_code == 404
    assert client.patch(f"/api/books/missing/scenes/{scene_id}", json={'title': 'X'}).status_code == 404
    assert client.post(f"/api/books/{book['id']}/archive").status_code == 200
    archived = client.patch(f"/api/books/{book['id']}/scenes/{scene_id}", json={'title': 'X'})
    assert archived.status_code == 409 and archived.json()['code'] == 'book_archived'
    assert client.get(f"/api/books/{book['id']}").json()['revision'] == book['revision']
