"""Local diagnostic logs retain bounded codes and IDs, never private prose."""
import json
import sqlite3
import threading
from uuid import uuid4

import pytest

from bardic.diagnostics import DiagnosticRepository, RETENTION_LIMIT, record_safely
from bardic.store import Store
from test_app import import_text, wait_job
from test_listen_api import begin, client, renderer  # Reuse offline-only fixtures.


def test_client_events_round_trip_are_scoped_and_downloadable(client):
    book, other = import_text(client), import_text(client, 'An original second test book.')
    payload = {'event': 'playback_media_error', 'book_id': book['id'],
               'segment_id': book['segments'][0]['id'], 'playback_rate': 2.5,
               'media_error_code': 3, 'operation': 'media'}
    result = client.post('/api/diagnostics', json=payload)
    assert result.status_code == 200 and result.json()['recorded'] is True
    assert client.post('/api/diagnostics', json={'event': 'cache_read_failed', 'book_id': other['id'],
                                               'operation': 'cache_read', 'http_status': 503}).status_code == 200
    response = client.get('/api/diagnostics', params={'book_id': book['id']})
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    scoped = response.json()
    assert scoped['retention_limit'] == RETENTION_LIMIT == 5000
    assert len(scoped['events']) == 1
    event = scoped['events'][0]
    assert event['id'] == result.json()['id'] and event['source'] == 'client'
    assert all(event[key] == value for key, value in payload.items())
    assert 'created_at' in event
    download = client.get('/api/diagnostics', params={'limit': 5000}).json()
    assert len(download['events']) == 2
    assert book['chapters'][0]['text'] not in json.dumps(download)


@pytest.mark.parametrize('operation', ['request', 'poll'])
def test_voice_preview_client_failure_keeps_only_bounded_identifiers(client, operation):
    book = import_text(client)
    payload = {'event': 'preview_failed', 'book_id': book['id'], 'segment_id': book['segments'][0]['id'],
               'job_id': uuid4().hex, 'operation': operation, 'http_status': 503}
    result = client.post('/api/diagnostics', json=payload)
    assert result.status_code == 200 and result.json()['recorded'] is True
    saved = client.get('/api/diagnostics').json()['events'][0]
    assert all(saved[key] == value for key, value in payload.items())
    assert 'session_id' not in saved and 'message' not in saved
    rejected = client.post('/api/diagnostics', json={**payload, 'message': 'private preview text'})
    assert rejected.status_code == 422 and 'private preview text' not in rejected.text


@pytest.mark.parametrize('event', ['voice_preview_failed', 'voice_preview_stopped', 'voice_preview_submit_failed'])
def test_voice_preview_worker_events_cannot_be_spoofed_as_client_events(client, event):
    assert client.post('/api/diagnostics', json={'event': event}).status_code == 422
    assert client.get('/api/diagnostics').json()['events'] == []


@pytest.mark.parametrize('changes', [
    {'message': 'secret-test-key and private book text'},
    {'url': 'https://provider.invalid/?api_key=secret-test-key'},
    {'stack': 'secret-test-key'}, {'api_key': 'secret-test-key'},
    {'event': 'secret-test-key'}, {'operation': 'secret-test-key'},
    {'book_id': 'secret-test-key'}, {'segment_id': 'secret-test-key'},
    {'session_id': 'secret-test-key'}, {'job_id': 'secret-test-key'},
    {'source': 'server'}, {'provider': 'gemini'}, {'status': 'failed'},
    {'playback_rate': 999}, {'playback_rate': '2.5'}, {'playback_rate': True},
    {'http_status': 0}, {'http_status': '429'}, {'http_status': True},
    {'media_error_code': 5}, {'media_error_code': False},
])
def test_invalid_client_fields_are_rejected_without_echo_or_retention(client, changes):
    response = client.post('/api/diagnostics', json={'event': 'buffer_failed', **changes})
    assert response.status_code == 422
    assert 'secret-test-key' not in response.text
    assert client.get('/api/diagnostics').json()['events'] == []


def test_client_events_obey_existing_origin_guard_and_read_limits(client):
    forbidden = client.post('/api/diagnostics', json={'event': 'buffer_failed'},
                            headers={'origin': 'https://outside.invalid'})
    assert forbidden.status_code == 403
    for limit in (-1, 0, 5001):
        assert client.get('/api/diagnostics', params={'limit': limit}).status_code == 400
    assert client.get('/api/diagnostics', params={'book_id': 'not-a-book-id'}).status_code == 400
    assert client.get('/api/diagnostics').json()['events'] == []


def test_recent_duplicate_events_and_client_rate_are_bounded(client, monkeypatch):
    monkeypatch.setattr('bardic.diagnostics.CLIENT_RATE_LIMIT', 2)
    first = client.post('/api/diagnostics', json={'event': 'buffer_failed', 'operation': 'prepare'}).json()
    duplicate = client.post('/api/diagnostics', json={'event': 'buffer_failed', 'operation': 'prepare'}).json()
    assert first['recorded'] is True
    assert duplicate == {'recorded': False, 'reason': 'duplicate', 'id': first['id']}
    second = client.post('/api/diagnostics', json={'event': 'cache_read_failed'}).json()
    dropped = client.post('/api/diagnostics', json={'event': 'playback_waiting'}).json()
    assert second['recorded'] is True and dropped == {'recorded': False, 'reason': 'rate_limited'}
    assert len(client.get('/api/diagnostics').json()['events']) == 2
    # Client noise cannot suppress a server failure record at the rate limit.
    result = record_safely(client.app.state.runtime.store, 'listen_submit_failed', operation='submit', status='failed')
    assert result['recorded'] is True


def test_retention_prunes_oldest_records_and_survives_restart(tmp_path):
    store = Store(tmp_path)
    repository = DiagnosticRepository(store)
    ids = [uuid4().hex for _ in range(RETENTION_LIMIT + 5)]
    with store.connect() as conn:
        conn.executemany('INSERT INTO diagnostic_events VALUES (?,?,?,?,?,?)',
                         [(identifier, '2020-01-01T00:00:00+00:00', 'server', 'listen_job_stopped', None, '{}')
                          for identifier in ids])
    newest = repository.record('playback_waiting')
    saved = DiagnosticRepository(Store(tmp_path)).events(limit=5000)
    assert len(saved['events']) == RETENTION_LIMIT
    assert saved['events'][0]['id'] == newest['id']
    assert saved['events'][-1]['id'] == ids[6]
    assert not set(ids[:6]) & {row['id'] for row in saved['events']}


def test_repository_rejects_private_fields_and_unsanitized_non_http_calls(tmp_path):
    repository = DiagnosticRepository(Store(tmp_path))
    for fields in ({'message': 'private test prose'}, {'api_key': 'test-secret'},
                   {'book_id': 'test-secret'}, {'operation': 'private test prose'},
                   {'playback_rate': float('nan')}, {'http_status': True},
                   {'segment_id': 'segment_' + 'a' * 12}):
        with pytest.raises(ValueError):
            repository.record('playback_media_error', **fields)
    assert repository.events()['events'] == []


def test_failed_listen_job_is_correlated_without_copying_its_error(client, renderer, monkeypatch):
    runtime = client.app.state.runtime
    runtime.api_key = 'private-offline-secret'
    book = import_text(client)
    def failed(*_args):
        raise ValueError('private-offline-secret with private text and https://private.invalid')
    monkeypatch.setattr('bardic.app.synthesize', failed)
    started = begin(client, book)
    job = wait_job(client, started['job']['id'])
    assert job['status'] == 'failed'
    # Job terminal state is saved just before its best-effort diagnostic.
    runtime.pool.submit(lambda: None).result(timeout=3)
    saved = client.get('/api/diagnostics', params={'book_id': book['id']}).json()
    assert len(saved['events']) == 1
    row = saved['events'][0]
    assert row['source'] == 'server' and row['event'] == 'listen_job_failed'
    assert row['job_id'] == job['id'] and row['session_id'] == started['session']['id']
    assert row['segment_id'] == book['segments'][0]['id'] and row['status'] == 'failed'
    serialized = json.dumps(saved)
    assert all(private not in serialized for private in ('private-offline-secret', 'private text', 'https://private.invalid'))


def test_cancelled_listen_and_executor_rejection_are_correlated(client, renderer, monkeypatch):
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    entered, release = threading.Event(), threading.Event()
    def occupy_worker():
        entered.set()
        assert release.wait(5)
    runtime.pool.submit(occupy_worker)
    try:
        assert entered.wait(2)
        first = begin(client, book)
        client.post(f"/api/jobs/{first['job']['id']}/cancel")
    finally:
        release.set()
    runtime.pool.submit(lambda: None).result(timeout=3)
    stopped = client.get('/api/diagnostics').json()['events'][0]
    assert stopped['event'] == 'listen_job_stopped' and stopped['status'] == 'cancelled'
    assert stopped['job_id'] == first['job']['id']
    def rejected(*_args):
        raise RuntimeError('Test worker unavailable')
    monkeypatch.setattr(runtime.pool, 'submit', rejected)
    response = client.post(f"/api/books/{book['id']}/listen", json={
        'provider': 'gemini', 'voice': 'Kore', 'segment_id': book['segments'][0]['id']})
    assert response.status_code == 503
    failure = client.get('/api/diagnostics').json()['events'][0]
    assert failure['event'] == 'listen_submit_failed' and failure['status'] == 'failed'
    assert runtime.store.job(failure['job_id'])['status'] == 'failed'


def test_broken_diagnostics_do_not_change_playback_or_failed_job_outcomes(client, renderer, monkeypatch):
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    def unavailable(*_args, **_kwargs):
        raise sqlite3.OperationalError('Diagnostic storage unavailable')
    monkeypatch.setattr(DiagnosticRepository, 'record', unavailable)
    response = client.post('/api/diagnostics', json={'event': 'playback_waiting'})
    assert response.status_code == 200 and response.json() == {'recorded': False, 'reason': 'unavailable'}
    def failed(*_args):
        raise ValueError('Original narration failure')
    with monkeypatch.context() as scoped:
        scoped.setattr('bardic.app.synthesize', failed)
        started = begin(client, book)
        job = wait_job(client, started['job']['id'])
    assert job['status'] == 'failed' and job['error'] == 'Original narration failure'
    retry = begin(client, book)
    assert wait_job(client, retry['job']['id'])['status'] == 'completed'
