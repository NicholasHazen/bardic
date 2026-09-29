"""Regression tests for the library defects and inconsistencies in issue #17 (contract 0.2.0).

Offline: synthetic text, tmp_path libraries and mocked narration only.
"""
import asyncio
import io
import json
import sqlite3
import zipfile

from fastapi.testclient import TestClient
import pytest

from bardic.app import create_app
from bardic.importer import MAX_UPLOAD
from test_app import fake_audio, wait_job
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


def epub_with(title, author):
    """The illustrated test EPUB with other package metadata (entries are compressed, so rebuild it)."""
    source, output = zipfile.ZipFile(io.BytesIO(illustrated_epub())), io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in source.namelist():
            data = source.read(name)
            if name.endswith('.opf'):
                data = data.replace(b'The Lantern', title.encode()).replace(b'A. Writer', author.encode())
            archive.writestr(name, data)
    return output.getvalue()


def ledger_rows(store, book_id):
    with sqlite3.connect(store.db) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='resource_operations'").fetchone():
            return []
        return conn.execute('SELECT stage FROM resource_operations WHERE book_id=?', (book_id,)).fetchall()


def library_states(store, book_id):
    with sqlite3.connect(store.db) as conn:
        return conn.execute("SELECT count(*) FROM artifact_versions WHERE book_id=? AND kind='library_state'",
                            (book_id,)).fetchone()[0]


# Item 1 ---------------------------------------------------------------------------------------

def test_refresh_metadata_for_unknown_book_is_404_without_a_ledger_row(client):
    response = client.post('/api/books/no-such-book/refresh-metadata')
    assert response.status_code == 404 and response.json()['code'] == 'book_not_found'
    assert ledger_rows(client.app.state.runtime.store, 'no-such-book') == []


def test_refused_refresh_records_nothing_and_a_real_refresh_is_measured(client):
    book = imported(client)
    store = client.app.state.runtime.store
    job = store.create_job(book['id'], 'analyze')
    before = ledger_rows(store, book['id'])
    response = client.post(f"/api/books/{book['id']}/refresh-metadata")
    assert response.status_code == 409 and response.json()['code'] == 'job_active'
    assert ledger_rows(store, book['id']) == before
    store.update_job(job['id'], status='cancelled')
    assert client.post(f"/api/books/{book['id']}/refresh-metadata").status_code == 200
    assert ledger_rows(store, book['id']).count(('metadata_refresh',)) == 1


# Item 2 ---------------------------------------------------------------------------------------

def test_metadata_edit_locks_only_changed_fields(client):
    book = imported(client, 'Book.epub', illustrated_epub())
    book_id = book['id']
    assert book['title'] == 'The Lantern' and book['author'] == 'A. Writer'
    # Only the title changes; the author is sent unchanged.
    response = client.patch(f'/api/books/{book_id}/metadata', json={'title': 'Edited title', 'author': 'A. Writer'})
    assert response.status_code == 200, response.text
    store = client.app.state.runtime.store
    source = store.root / 'originals' / book_id / 'source.epub'
    source.write_bytes(epub_with('Other Name', 'B. Author'))
    refreshed = client.post(f'/api/books/{book_id}/refresh-metadata').json()
    assert refreshed['title'] == 'Edited title'   # reviewed: preserved
    assert refreshed['author'] == 'B. Author'     # never edited: refreshed
    # A later author-only edit adds the author lock and keeps the title lock.
    client.patch(f'/api/books/{book_id}/metadata', json={'title': 'Edited title', 'author': 'Chosen Author'})
    source.write_bytes(epub_with('Third Name', 'C. Other'))
    refreshed = client.post(f'/api/books/{book_id}/refresh-metadata').json()
    assert (refreshed['title'], refreshed['author']) == ('Edited title', 'Chosen Author')


def test_old_stored_lock_shape_still_locks_both_fields(client):
    book = imported(client, 'Book.epub', illustrated_epub())
    store = client.app.state.runtime.store
    stored = store.book(book['id'])
    stored.update(title='Kept', author='Kept author', metadata_edited={'title': True, 'author': True})
    store.save_book(stored)
    refreshed = client.post(f"/api/books/{book['id']}/refresh-metadata").json()
    assert (refreshed['title'], refreshed['author']) == ('Kept', 'Kept author')


# Item 3 ---------------------------------------------------------------------------------------

def test_audio_count_counts_only_current_playable_takes(client, monkeypatch):
    fake_audio(monkeypatch)
    client.post('/api/settings', json={'api_keys': {'gemini': 'test-only'}})
    book = imported(client, content='Chapter One\n\nThe lamps were lit.\n\n“Come in,” Mara said.\n'.encode())
    url = f"/api/books/{book['id']}"
    assert wait_job(client, client.post(f'{url}/render', json={'provider': 'gemini'}).json()['id'])['status'] == 'completed'
    store = client.app.state.runtime.store
    stale = book['passages'][0]
    store.save_take(book['id'], stale['id'], {'fingerprint': 'stale-take', 'duration': 1, 'provider': 'gemini', 'model': 'x'})
    presented = client.get(url).json()
    playable = sum(bool(s['audio']) for s in presented['passages'])
    assert playable == len(book['passages']) - 1
    summary = next(b for b in client.get('/api/books').json() if b['id'] == book['id'])
    assert summary['audio_count'] == playable


# Item 4 ---------------------------------------------------------------------------------------

def test_cover_has_strong_etag_honors_if_none_match_and_keeps_its_cache_control(client):
    imported(client, 'Covered.epub', illustrated_epub())
    summary = client.get('/api/books').json()[0]
    digest, url = summary['cover']['sha256'], summary['cover']['url']
    versioned = client.get(url)
    assert versioned.status_code == 200 and versioned.headers['etag'] == f'"{digest}"'
    assert versioned.headers['cache-control'] == 'private, max-age=31536000, immutable'
    plain = client.get(f"/api/books/{summary['id']}/cover")
    assert plain.headers['cache-control'] == 'private, no-cache' and plain.headers['etag'] == f'"{digest}"'
    stale = client.get(f"/api/books/{summary['id']}/cover?v=older")
    assert stale.headers['cache-control'] == 'private, no-cache'
    for header in (f'"{digest}"', f'W/"{digest}"', f'"other", "{digest}"', '*'):
        response = client.get(url, headers={'If-None-Match': header})
        assert response.status_code == 304 and response.content == b''
        assert response.headers['etag'] == f'"{digest}"'
    assert client.get(url, headers={'If-None-Match': '"other"'}).status_code == 200
    # Other /api responses without their own Cache-Control are still not stored.
    assert client.get('/api/books').headers['cache-control'] == 'no-store'
    missing = client.get('/api/books/missing/cover')
    assert missing.status_code == 404 and missing.headers['cache-control'] == 'no-store'


# Item 5 ---------------------------------------------------------------------------------------

def _raw_upload(app, *, content_length, chunks):
    """Drive the ASGI app directly and count how many body chunks it pulled."""
    pulled, sent = [], []
    boundary = b'bardicboundary'
    head = (b'--' + boundary + b'\r\nContent-Disposition: form-data; name="file"; filename="big.txt"\r\n'
            b'Content-Type: text/plain\r\n\r\n')

    async def receive():
        index = len(pulled)
        pulled.append(index)
        if index == 0:
            return {'type': 'http.request', 'body': head, 'more_body': True}
        if index <= chunks:
            return {'type': 'http.request', 'body': b'a' * (1024 * 1024), 'more_body': True}
        return {'type': 'http.request', 'body': b'\r\n--' + boundary + b'--\r\n', 'more_body': False}

    async def send(message):
        sent.append(message)

    headers = [(b'host', b'testserver'), (b'content-type', b'multipart/form-data; boundary=' + boundary)]
    if content_length is not None:
        headers.append((b'content-length', str(content_length).encode()))
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'POST', 'scheme': 'http',
             'path': '/api/books', 'raw_path': b'/api/books', 'query_string': b'', 'root_path': '',
             'headers': headers, 'client': ('127.0.0.1', 1234), 'server': ('testserver', 80)}
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m['type'] == 'http.response.start')
    body = b''.join(m.get('body', b'') for m in sent if m['type'] == 'http.response.body')
    return start['status'], body, len(pulled)


def test_upload_with_oversized_content_length_is_rejected_before_reading(client):
    status, body, pulled = _raw_upload(client.app, content_length=MAX_UPLOAD + 2 * 1024 * 1024, chunks=32)
    assert pulled == 0
    assert status == 413 and json.loads(body)['code'] == 'upload_too_large'


def test_upload_without_content_length_stops_reading_at_the_limit(client):
    chunks = 60
    status, body, pulled = _raw_upload(client.app, content_length=None, chunks=chunks)
    assert pulled <= MAX_UPLOAD // (1024 * 1024) + 3 < chunks
    assert status == 413 and json.loads(body)['code'] == 'upload_too_large'


def test_failed_import_leaves_no_original_or_ledger_row(client, monkeypatch):
    store = client.app.state.runtime.store

    def broken(book):
        raise RuntimeError('simulated storage failure')
    monkeypatch.setattr(store, 'save_book', broken)
    with pytest.raises(RuntimeError):
        client.post('/api/books', files={'file': ('story.txt', b'Chapter 1\n\nMara spoke softly.')})
    originals = store.root / 'originals'
    assert not originals.exists() or list(originals.iterdir()) == []
    with sqlite3.connect(store.db) as conn:
        assert conn.execute("SELECT count(*) FROM resource_operations WHERE stage='import'").fetchone()[0] == 0
    monkeypatch.undo()


def test_unreadable_upload_is_400_with_code_and_leaves_nothing(client):
    response = client.post('/api/books', files={'file': ('story.pdf', b'%PDF-1.4 not a book')})
    assert response.status_code == 400 and response.json()['code'] == 'book_file_invalid'
    store = client.app.state.runtime.store
    with sqlite3.connect(store.db) as conn:
        assert conn.execute("SELECT count(*) FROM resource_operations WHERE stage='import'").fetchone()[0] == 0
    assert not (store.root / 'originals').exists() or list((store.root / 'originals').iterdir()) == []


# Item 6 ---------------------------------------------------------------------------------------

def test_archive_and_restore_are_idempotent_and_record_only_changes(client):
    book = imported(client)
    store = client.app.state.runtime.store
    url = f"/api/books/{book['id']}"
    assert client.post(f'{url}/restore').json() == {'id': book['id'], 'archived': False, 'retained': True}
    assert library_states(store, book['id']) == 0
    for _ in range(2):
        response = client.post(f'{url}/archive')
        assert response.status_code == 200, response.text
        assert response.json() == {'id': book['id'], 'archived': True, 'retained': True}
    assert library_states(store, book['id']) == 1
    for _ in range(2):
        assert client.post(f'{url}/restore').json()['archived'] is False
    assert library_states(store, book['id']) == 2


def test_guards_use_409_codes_consistently(client):
    book = imported(client)
    store = client.app.state.runtime.store
    url = f"/api/books/{book['id']}"
    assert client.post(f'{url}/archive').status_code == 200
    job = store.create_job(book['id'], 'analyze')
    response = client.post(f'{url}/restore')
    assert response.status_code == 409 and response.json()['code'] == 'job_active'
    store.update_job(job['id'], status='cancelled')
    response = client.patch(f'{url}/metadata', json={'title': 'Blocked'})
    assert response.status_code == 409 and response.json()['code'] == 'book_archived'
    response = client.post(f'{url}/refresh-metadata')
    assert response.status_code == 409 and response.json()['code'] == 'book_archived'
    assert client.post(f'{url}/restore').status_code == 200
    job = store.create_job(book['id'], 'analyze')
    for response in (client.post(f'{url}/archive'), client.post(f'{url}/refresh-metadata'),
                     client.patch(f'{url}/metadata', json={'title': 'Blocked'})):
        assert response.status_code == 409 and response.json()['code'] == 'job_active'
    store.update_job(job['id'], status='cancelled')
    series_id = client.post('/api/series', json={'name': 'Lanterns'}).json()['id']
    client.put(f"{url}/series", json={'series_id': series_id, 'position': 1})
    parent = store.create_job('series:' + series_id, 'series')
    store.update_job(parent['id'], status='running', book_ids=[book['id']], child_job_ids=[])
    for response in (client.post(f'{url}/archive'), client.post(f'{url}/refresh-metadata'),
                     client.patch(f'{url}/metadata', json={'title': 'Blocked'})):
        assert response.status_code == 409 and response.json()['code'] == 'series_run_active'
    store.update_job(parent['id'], status='completed')


# Item 7 ---------------------------------------------------------------------------------------

def test_library_summary_has_passage_count_without_the_old_name(client):
    imported(client)
    summary = client.get('/api/books').json()[0]
    assert summary['passage_count'] >= 1 and 'segment_count' not in summary


# Items 8 and 9 ----------------------------------------------------------------------------------

def test_export_writes_presented_book_and_no_ledger_row(client, monkeypatch):
    fake_audio(monkeypatch)
    client.post('/api/settings', json={'api_keys': {'gemini': 'test-only'}})
    book = imported(client, content='Chapter One\n\nThe lamps were lit.\n\n“Come in,” Mara said.\n'.encode())
    url = f"/api/books/{book['id']}"
    assert wait_job(client, client.post(f'{url}/render', json={'provider': 'gemini'}).json()['id'])['status'] == 'completed'
    store = client.app.state.runtime.store
    before = ledger_rows(store, book['id'])
    export = client.get(f'{url}/export')
    assert export.status_code == 200
    assert ledger_rows(store, book['id']) == before
    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        assert json.loads(archive.read('production.json')) == client.get(url).json()


def test_export_without_audio_has_a_code(client):
    book = imported(client)
    response = client.get(f"/api/books/{book['id']}/export")
    assert response.status_code == 400 and response.json()['code'] == 'export_audio_missing'


# Item 10 --------------------------------------------------------------------------------------

def test_library_order_is_import_time_and_survives_saves(client):
    first, second = imported(client, 'first.txt'), imported(client, 'second.txt')
    assert [b['id'] for b in client.get('/api/books').json()] == [second['id'], first['id']]
    assert client.patch(f"/api/books/{first['id']}/metadata", json={'title': 'Renamed'}).status_code == 200
    assert [b['id'] for b in client.get('/api/books').json()] == [second['id'], first['id']]
    assert [b['id'] for b in client.get('/api/library').json()['books']] == [second['id'], first['id']]


def test_books_without_import_time_keep_a_stable_order_after_saves(client):
    first, second = imported(client, 'first.txt'), imported(client, 'second.txt')
    store = client.app.state.runtime.store
    for identifier in (first['id'], second['id']):
        stored = store.book(identifier)
        stored.pop('created_at')
        store.save_book(stored)
    order = [b['id'] for b in client.get('/api/books').json()]
    client.patch(f"/api/books/{order[-1]}/metadata", json={'title': 'Renamed'})
    assert [b['id'] for b in client.get('/api/books').json()] == order


# Item 5 and 11, through the whole app (validated against the contract by conftest) -------------

def test_upload_limits_through_the_full_app_return_the_documented_code(client):
    # Just over the file limit: the body is small enough to reach the route, which checks the file itself.
    response = client.post('/api/books', files={'file': ('big.txt', b'a' * (MAX_UPLOAD + 1))})
    assert response.status_code == 413 and response.json()['code'] == 'upload_too_large'
    assert response.headers['cache-control'] == 'no-store'
    # Far over the limit: refused by the declared Content-Length before the route runs.
    response = client.post('/api/books', files={'file': ('big.txt', b'a' * (MAX_UPLOAD + 128 * 1024))})
    assert response.status_code == 413 and response.json()['code'] == 'upload_too_large'
    assert response.headers['cache-control'] == 'no-store'


def test_invalid_metadata_has_a_code_and_describes_the_condition(client):
    book = imported(client)
    for body in ({'title': '   '}, {'title': 'Bell\x07'}):
        response = client.patch(f"/api/books/{book['id']}/metadata", json=body)
        assert response.status_code == 400 and response.json()['code'] == 'metadata_invalid'
    assert client.patch(f"/api/books/{book['id']}/metadata", json={'title': 'x' * 501}).status_code == 422


def test_a_book_stored_without_the_fields_an_old_import_lacked_is_still_presented_whole(client):
    book = imported(client, 'old.txt')
    store = client.app.state.runtime.store
    stored = store.book(book['id'])
    for name in ('created_at', 'author', 'source_name', 'revision', 'analysis'):
        stored.pop(name, None)
    store.save_book(stored)
    presented = client.get(f"/api/books/{book['id']}")
    assert presented.status_code == 200, presented.text
    body = presented.json()
    assert body['created_at'] is None and body['analysis'] is None
    assert body['author'] == '' and body['source_name'] == '' and body['revision'] == 0
