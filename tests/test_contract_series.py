"""Series routes that the rest of the suite does not exercise with a success response.

Offline: synthetic prose, no provider keys, no network. The contract hook in
conftest.py validates every response below against contract/openapi.json.
"""
import pytest
from fastapi.testclient import TestClient

from bardic.app import create_app
from classic_fixtures import classic_references


TEXT = 'Mira lit the lamp and spoke softly.'


def synthetic_book(book_id):
    return {'id': book_id, 'title': f'Lantern {book_id}', 'author': 'Test Author',
            'chapters': [{'id': 'chapter-1', 'title': 'The gate', 'text': TEXT}],
            'characters': [{'id': 'narrator', 'name': 'Narrator'}, {'id': 'unassigned', 'name': 'Unassigned'},
                           {'id': 'mira', 'name': 'Mira'}],
            'segments': [{'id': 'segment-1', 'chapter_id': 'chapter-1', 'start': 0, 'end': len(TEXT),
                          'text': TEXT, 'audio': None}]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY',
                 'BREEZE_TTS_URL', 'BREEZE_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr('bardic.app.list_system_voices', lambda: [])
    with TestClient(create_app(tmp_path)) as client:
        yield client


def linked_series(client):
    store = client.app.state.runtime.store
    for book_id in ('one', 'two'):
        store.save_book(synthetic_book(book_id))
    series = client.post('/api/series', json={'name': 'The Lantern Books'}).json()
    for position, book_id in enumerate(('one', 'two'), start=1):
        response = client.put(f'/api/books/{book_id}/series', json={'series_id': series['id'], 'position': position})
        assert response.status_code == 200, response.text
    assert client.put(f"/api/series/{series['id']}/volumes",
                      json={'position': 3, 'title': 'Unwritten', 'status': 'planned'}).status_code == 200
    identity = client.post(f"/api/series/{series['id']}/characters", json={'name': 'Mira'}).json()
    for book_id in ('one', 'two'):
        response = client.put(f'/api/books/{book_id}/series/characters/mira',
                              json={'series_character_id': identity['id']})
        assert response.status_code == 200, response.text
    reference = {'id': 'reference-1', 'character_id': 'mira', 'chapter_id': 'chapter-1', 'segment_id': 'segment-1',
                 'start': 0, 'end': len(TEXT), 'quote': TEXT, 'kind': 'profile_evidence',
                 'profile_description': 'A soft voice', 'profile_direction': 'Softly',
                 'provider': 'test', 'model': 'test-model', 'confidence': .9}
    classic_references(store, 'one', [reference])
    return series, identity


def test_series_characters_and_map_list_confirmed_identities_and_all_volumes(client):
    series, identity = linked_series(client)

    response = client.get(f"/api/series/{series['id']}/characters")
    assert response.status_code == 200, response.text
    [character] = response.json()
    assert character['id'] == identity['id'] and character['series_id'] == series['id']
    assert [(link['book_id'], link['character_id']) for link in character['links']] == [('one', 'mira'), ('two', 'mira')]

    response = client.get(f"/api/series/{series['id']}/map")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['series']['id'] == series['id'] and body['note']
    assert [(v['position'], v['kind'], v['status'], v.get('book_id')) for v in body['series']['volumes']] == [
        (1, 'supplied', 'available', 'one'), (2, 'supplied', 'available', 'two'), (3, 'placeholder', 'planned', None)]
    assert [c['id'] for c in body['characters']] == [identity['id']]

    context = client.get('/api/books/two/series/context')
    assert context.status_code == 200, context.text
    [entry] = context.json()['characters']
    assert entry['observations'][0]['book_id'] == 'one' and entry['observations'][0]['quote'] == TEXT

    unlinked = client.put('/api/books/two/series/characters/mira', json={'series_character_id': None})
    assert unlinked.json() == {'character_id': 'mira', 'kind': 'unlinked'}


def test_removed_series_stays_readable_on_the_map_and_keeps_its_identities(client):
    series, identity = linked_series(client)
    assert client.post(f"/api/series/{series['id']}/archive").status_code == 200

    removed = client.get(f"/api/series/{series['id']}/map")
    assert removed.status_code == 200 and removed.json()['series']['archived'] is True
    assert [c['id'] for c in removed.json()['characters']] == [identity['id']]
    response = client.get(f"/api/series/{series['id']}/characters")
    assert response.status_code == 200 and response.json()[0]['id'] == identity['id']
    assert client.get('/api/books/one/series').json()['membership'] is None

    assert client.get('/api/series/series_missing/map').status_code == 404
    assert client.get('/api/series/series_missing/characters').status_code == 404
