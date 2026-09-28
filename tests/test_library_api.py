"""Offline HTTP coverage for library metadata, covers and reversible removal."""
from copy import deepcopy
import io

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from bardic.app import create_app
from test_library import illustrated_epub


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr('bardic.app.list_system_voices', lambda: [])
    with TestClient(create_app(tmp_path)) as client:
        yield client


def imported(client, *, filename='story.txt', content=b'Chapter 1\n\nMara spoke softly.'):
    response = client.post('/api/books', files={'file': (filename, content)})
    assert response.status_code == 200, response.text
    return response.json()


def test_imported_epub_language_is_presented_and_txt_has_none(client):
    from test_importer import epub_file
    book = imported(client, filename='lang.epub', content=epub_file(metadata='<dc:language>es-MX</dc:language>'))
    assert book['language'] == 'es-MX'
    assert client.get(f"/api/books/{book['id']}").json()['language'] == 'es-MX'
    assert client.get(f"/api/books/{imported(client)['id']}").json()['language'] is None
    # A book stored before the field existed presents it as unknown.
    store = client.app.state.runtime.store
    older = store.book(book['id'])
    older.pop('language')
    store.save_book(older)
    assert client.get(f"/api/books/{book['id']}").json()['language'] is None


def test_metadata_api_preserves_source_ids_takes_and_checkpoint(client):
    book = imported(client)
    store = client.app.state.runtime.store
    book = store.book(book['id'])
    book['segments'][0]['audio'] = {'fingerprint': 'retained-take', 'duration': 3, 'provider': 'system', 'model': 'macos-say'}
    store.save_book(book)
    checkpoint = {'status': 'failed', 'stage': 'discovery', 'chapters': [], 'references': [],
                  'units': {'retained': {'result': {'characters': []}}}, 'working_book': deepcopy(book)}
    store.save_analysis_checkpoint(book['id'], 'saved-fingerprint', checkpoint)
    before_checkpoint = store.analysis_checkpoint(book['id'], 'saved-fingerprint')
    response = client.patch(f"/api/books/{book['id']}/metadata", json={'title': '  A new title  ', 'author': '  A Writer  '})
    assert response.status_code == 200, response.text
    assert response.json()['title'] == 'A new title' and response.json()['author'] == 'A Writer'
    updated = store.book(book['id'])
    for field in ('id', 'chapters', 'scenes', 'segments', 'characters'):
        assert updated[field] == book[field]
    assert store.analysis_checkpoint(book['id'], 'saved-fingerprint') == before_checkpoint
    assert client.get('/api/books').json()[0]['title'] == 'A new title'
    assert client.patch(f"/api/books/{book['id']}/metadata", json={'title': 'bad', 'source_name': '../other.txt'}).status_code == 422
    assert client.patch(f"/api/books/{book['id']}/metadata", json={'title': '   '}).status_code == 400


def test_cover_api_returns_small_safe_jpeg_and_refresh_preserves_edited_metadata(client):
    data = illustrated_epub()
    book = imported(client, filename='Book.epub', content=data)
    book_id = book['id']
    assert '_cover_data' not in book
    response = client.get(f'/api/books/{book_id}/cover')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'image/jpeg'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers.get('etag')
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.format == 'JPEG' and image.width <= 240 and image.height <= 360
    store = client.app.state.runtime.store
    before = store.book(book_id)
    assert client.patch(f'/api/books/{book_id}/metadata', json={'title': 'Edited title', 'author': 'Edited author'}).status_code == 200
    response = client.post(f'/api/books/{book_id}/refresh-metadata')
    assert response.status_code == 200 and response.json()['title'] == 'Edited title'
    assert response.json()['author'] == 'Edited author' and response.json()['cover']['url']
    after = store.book(book_id)
    assert after['chapters'] == before['chapters'] and after['segments'] == before['segments']
    plain = imported(client)
    assert client.get(f"/api/books/{plain['id']}/cover").status_code == 404
    assert client.get('/api/books/missing/cover').status_code == 404


def test_book_archive_restore_keeps_files_takes_history_and_direct_access(client):
    book = imported(client)
    store = client.app.state.runtime.store
    book_id = book['id']
    store.save_take(book_id, book['segments'][0]['id'], {'fingerprint': 'retained', 'duration': 1})
    before = store.book(book_id)
    source = store.root / 'originals' / book_id / 'source.txt'
    original_bytes = source.read_bytes()
    result = client.post(f'/api/books/{book_id}/archive')
    assert result.status_code == 200 and result.json()['archived'] is True
    assert client.get('/api/books').json() == []
    assert client.get('/api/library').json()['books'] == []
    removed = client.get('/api/library?include_archived=true').json()['books']
    assert len(removed) == 1 and removed[0]['archived'] is True
    assert client.get(f'/api/books/{book_id}').status_code == 200
    assert store.book(book_id) == before and source.read_bytes() == original_bytes
    assert client.post(f'/api/books/{book_id}/analyze', json={'provider': 'local'}).status_code == 400
    assert client.patch(f'/api/books/{book_id}/metadata', json={'title': 'Blocked'}).status_code == 400
    assert client.post(f'/api/books/{book_id}/restore').status_code == 200
    assert client.get('/api/books').json()[0]['id'] == book_id
    assert store.book(book_id) == before


def test_series_archive_restore_keeps_books_membership_and_identity_links(client):
    book = imported(client)
    person = client.post(f"/api/books/{book['id']}/characters", json={'name': 'Mara'}).json()['characters'][-1]
    series = client.post('/api/series', json={'name': 'Lanterns'}).json()
    series_id = series['id']
    assert client.put(f"/api/books/{book['id']}/series", json={'series_id': series_id, 'position': 9}).status_code == 200
    identity = client.post(f'/api/series/{series_id}/characters', json={'name': 'Mara'}).json()
    url = f"/api/books/{book['id']}/series/characters/{person['id']}"
    assert client.put(url, json={'series_character_id': identity['id']}).status_code == 200
    before = client.get(f"/api/books/{book['id']}/series").json()['links']
    assert client.patch(f'/api/series/{series_id}', json={'name': 'The Lantern Cycle'}).status_code == 200
    assert client.post(f'/api/series/{series_id}/archive').status_code == 200
    assert client.get('/api/series').json() == []
    assert len(client.get('/api/books').json()) == 1
    assert client.get('/api/library?include_archived=true').json()['series'][0]['archived'] is True
    assert client.get(f"/api/books/{book['id']}/series").json()['links'] == before
    assert client.post(f'/api/series/{series_id}/restore').status_code == 200
    restored = client.get(f"/api/books/{book['id']}/series").json()
    assert restored['membership']['position'] == 9 and restored['links'] == before
    assert restored['series']['name'] == 'The Lantern Cycle'


def test_volume_placeholder_api_fills_existing_slot_without_duplicate_book(client):
    book = imported(client)
    series_id = client.post('/api/series', json={'name': 'Lanterns'}).json()['id']
    base = f'/api/series/{series_id}/volumes'
    assert client.put(base, json={'position': 1, 'title': 'First', 'status': 'missing'}).status_code == 200
    assert client.put(base, json={'position': 1.5, 'title': 'Side story', 'status': 'planned'}).status_code == 200
    assert client.put(f"/api/books/{book['id']}/series", json={'series_id': series_id, 'position': 1}).status_code == 200
    volumes = client.get('/api/series').json()[0]['volumes']
    assert [(v['position'], v['status']) for v in volumes] == [(1, 'available'), (1.5, 'planned')]
    assert client.put(base, json={'position': 1, 'title': 'Duplicate', 'status': 'missing'}).status_code == 400
    assert client.delete(base + '/1.5').status_code == 200
    assert len(client.get('/api/series').json()[0]['volumes']) == 1
    assert client.put(base, json={'position': -1}).status_code == 422
    assert client.put(base, json={'position': 2, 'status': 'invented'}).status_code == 422


def test_library_mutations_require_local_origin_idle_books_and_valid_resources(client):
    book = imported(client)
    series_id = client.post('/api/series', json={'name': 'Lanterns'}).json()['id']
    client.put(f"/api/books/{book['id']}/series", json={'series_id': series_id, 'position': 1})
    store = client.app.state.runtime.store
    job = store.create_job(book['id'], 'analyze')
    assert client.post(f"/api/books/{book['id']}/archive").status_code == 409
    assert client.post(f"/api/books/{book['id']}/refresh-metadata").status_code == 409
    assert client.patch(f"/api/books/{book['id']}/metadata", json={'title': 'Blocked'}).status_code == 409
    assert client.post(f'/api/series/{series_id}/archive').status_code == 409
    store.update_job(job['id'], status='cancelled')
    assert client.post(f"/api/books/{book['id']}/archive", headers={'origin': 'https://elsewhere.invalid'}).status_code == 403
    assert client.post('/api/books/missing/archive').status_code == 404
    assert client.post('/api/series/missing/restore').status_code == 404


def test_metadata_refresh_rejects_missing_and_outside_source_without_changing_book(client, tmp_path):
    book = imported(client)
    store = client.app.state.runtime.store
    book_id = book['id']
    before = store.book(book_id)
    source = store.root / 'originals' / book_id / 'source.txt'
    source.unlink()
    assert client.post(f'/api/books/{book_id}/refresh-metadata').status_code == 400
    outside = tmp_path.parent / (tmp_path.name + '-outside.txt')
    outside.write_text('Private unrelated source', encoding='utf-8')
    try:
        source.symlink_to(outside)
        response = client.post(f'/api/books/{book_id}/refresh-metadata')
        assert response.status_code == 400 and 'unavailable' in response.text
        assert store.book(book_id) == before
        assert client.get('/api/library').json()['books'][0]['storage']['original_bytes'] == 0
    finally:
        outside.unlink()


def test_incoming_book_cannot_join_active_target_series_but_unrelated_book_work_is_allowed(client):
    member, incoming = imported(client), imported(client)
    series_id = client.post('/api/series', json={'name': 'Running series'}).json()['id']
    client.put(f"/api/books/{member['id']}/series", json={'series_id': series_id, 'position': 1})
    store = client.app.state.runtime.store
    parent = store.create_job('series:' + series_id, 'series')
    store.update_job(parent['id'], status='running', book_ids=[member['id']], child_job_ids=[])
    response = client.put(f"/api/books/{incoming['id']}/series", json={'series_id': series_id, 'position': 2})
    assert response.status_code == 409 and 'series run' in response.text.lower()
    assert client.get(f"/api/books/{incoming['id']}/series").json()['membership'] is None
    assert len(client.get('/api/series').json()[0]['books']) == 1
    store.update_job(parent['id'], status='completed')
    # A job on a different book is not a series-wide reservation.
    other_job = store.create_job(member['id'], 'analyze')
    response = client.put(f"/api/books/{incoming['id']}/series", json={'series_id': series_id, 'position': 2})
    assert response.status_code == 200, response.text
    assert response.json()['membership']['position'] == 2
    store.update_job(other_job['id'], status='cancelled')


def test_restoring_archived_member_cannot_change_an_active_series_input_set(client):
    earlier, later = imported(client), imported(client)
    series_id = client.post('/api/series', json={'name': 'Running series'}).json()['id']
    for book, position in ((earlier, 1), (later, 9)):
        assert client.put(f"/api/books/{book['id']}/series", json={'series_id': series_id, 'position': position}).status_code == 200
    assert client.post(f"/api/books/{earlier['id']}/archive").status_code == 200
    store = client.app.state.runtime.store
    parent = store.create_job('series:' + series_id, 'series')
    store.update_job(parent['id'], status='running', book_ids=[later['id']], child_job_ids=[])
    response = client.post(f"/api/books/{earlier['id']}/restore")
    assert response.status_code == 409 and store.is_archived(earlier['id'])
    assert [book['book_id'] for book in client.get('/api/series').json()[0]['books']] == [later['id']]
    store.update_job(parent['id'], status='completed')
    other_job = store.create_job(later['id'], 'analyze')
    response = client.post(f"/api/books/{earlier['id']}/restore")
    assert response.status_code == 200, response.text
    assert not store.is_archived(earlier['id'])
    store.update_job(other_job['id'], status='cancelled')


def test_active_series_reserves_member_identity_links_order_and_collection_edits(client):
    book = imported(client)
    series_id = client.post('/api/series', json={'name': 'Reserved series'}).json()['id']
    other_id = client.post('/api/series', json={'name': 'Other series'}).json()['id']
    person = client.post(f"/api/books/{book['id']}/characters", json={'name': 'Mara'}).json()['characters'][-1]
    identity = client.post(f'/api/series/{series_id}/characters', json={'name': 'Mara'}).json()
    client.put(f"/api/books/{book['id']}/series", json={'series_id': series_id, 'position': 1})
    link = f"/api/books/{book['id']}/series/characters/{person['id']}"
    client.put(link, json={'series_character_id': identity['id']})
    client.put(f'/api/series/{series_id}/volumes', json={'position': 2, 'status': 'missing'})
    store = client.app.state.runtime.store
    parent = store.create_job('series:' + series_id, 'series')
    store.update_job(parent['id'], status='running', book_ids=[book['id']], child_job_ids=[])
    for response in (
        client.put(link, json={'series_character_id': None}),
        client.put(link, json={'series_character_id': identity['id']}),
        client.put(f"/api/books/{book['id']}/series", json={'series_id': other_id, 'position': 1}),
        client.put(f"/api/books/{book['id']}/series", json={'series_id': None}),
        client.patch(f'/api/series/{series_id}', json={'name': 'Changed'}),
        client.put(f'/api/series/{series_id}/volumes', json={'position': 3, 'status': 'planned'}),
        client.delete(f'/api/series/{series_id}/volumes/2'),
        client.post(f'/api/series/{series_id}/archive'),
    ):
        assert response.status_code == 409, response.text
    assert client.get(f"/api/books/{book['id']}/series").json()['links'][0]['series_character_id'] == identity['id']
    store.update_job(parent['id'], status='completed')
