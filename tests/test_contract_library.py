"""Contract coverage for library shapes that the behavior tests do not produce together.

Every /api response here is validated against contract views by tests/conftest.py.
Synthetic text only; no provider or network access.
"""
from fastapi.testclient import TestClient
import pytest

from bardic.app import create_app
from test_library import illustrated_epub


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'BREEZE_TTS_URL', 'BREEZE_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr('bardic.app.list_system_voices', lambda: [])
    with TestClient(create_app(tmp_path)) as client:
        yield client


def imported(client, filename='story.txt', content=b'Chapter 1\n\n"Hello," said Mara. The tide turned.'):
    response = client.post('/api/books', files={'file': (filename, content)})
    assert response.status_code == 200, response.text
    return response.json()


def test_library_snapshot_with_series_volumes_membership_and_cover(client):
    covered = imported(client, 'Covered.epub', illustrated_epub())
    member = imported(client)
    removed = imported(client, 'removed.txt')
    series_id = client.post('/api/series', json={'name': 'Lanterns'}).json()['id']
    for book, position in ((covered, 1), (member, 2), (removed, 3)):
        assert client.put(f"/api/books/{book['id']}/series", json={'series_id': series_id, 'position': position}).status_code == 200
    assert client.put(f'/api/series/{series_id}/volumes', json={'position': 2.5, 'title': 'Side story', 'status': 'planned'}).status_code == 200
    assert client.post(f"/api/books/{removed['id']}/archive").status_code == 200

    library = client.get('/api/library').json()
    assert {b['id'] for b in library['books']} == {covered['id'], member['id']}
    series = library['series'][0]
    assert [b['book_id'] for b in series['books']] == [covered['id'], member['id']]
    assert [v['status'] for v in series['volumes']] == ['available', 'available', 'planned', 'archived']
    summary = next(b for b in library['books'] if b['id'] == covered['id'])
    assert summary['membership'] == {'series_id': series_id, 'series_name': 'Lanterns', 'position': 1.0}
    assert summary['cover']['url'] == f"/api/books/{covered['id']}/cover?v={summary['cover']['sha256']}"

    everything = client.get('/api/library?include_archived=true').json()
    hidden = next(b for b in everything['books'] if b['id'] == removed['id'])
    assert hidden['archived'] is True and hidden['archived_at'] and hidden['membership']['series_id'] == series_id
    assert len(everything['series'][0]['books']) == 3


def test_cover_headers_and_visibility_edge_cases_are_as_documented(client):
    book = imported(client, 'Covered.epub', illustrated_epub())
    summary = client.get('/api/books').json()[0]
    response = client.get(summary['cover']['url'])
    assert response.status_code == 200 and response.headers['content-type'] == 'image/jpeg'
    # The ETag is the unquoted hex digest; the /api middleware replaces the route's Cache-Control.
    assert response.headers['etag'] == summary['cover']['sha256']
    assert response.headers['cache-control'] == 'no-store'
    assert client.get(summary['cover']['url'], headers={'If-None-Match': summary['cover']['sha256']}).status_code == 200

    # Restoring a book that is not removed succeeds; removing twice is refused.
    assert client.post(f"/api/books/{book['id']}/restore").json() == {'id': book['id'], 'archived': False, 'retained': True}
    assert client.post(f"/api/books/{book['id']}/archive").json() == {'id': book['id'], 'archived': True, 'retained': True}
    assert client.post(f"/api/books/{book['id']}/archive").status_code == 400
    assert client.get(summary['cover']['url']).status_code == 200


def test_demo_has_no_original_to_refresh(client):
    demo = client.post('/api/demo').json()
    response = client.post(f"/api/books/{demo['id']}/refresh-metadata")
    assert response.status_code == 400
    summary = client.get('/api/books').json()[0]
    assert summary['storage']['original_bytes'] == 0 and summary['cover'] is None
    assert summary['source_name'] == 'The Last Light.txt' and summary['analysis']['provider'] == 'local'
