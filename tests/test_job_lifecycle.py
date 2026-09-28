"""Job documents: terminal statuses are final, field names are unambiguous, and every embedded job is a full Job.

Offline: analysis and speech are faked; no provider is contacted.
"""
import json
import threading
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient

from bardic.app import create_app
from bardic.audio import DEFAULT_TTS_MODEL
from test_app import import_text, wait_job
from test_chapter_listening import fake_chunk_synthesizer, import_story, start as start_chapter


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'BREEZE_TTS_URL', 'BREEZE_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr('bardic.app.list_system_voices', lambda: [])
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda *_a, **_k: pytest.fail('No live provider calls allowed'))
    with TestClient(create_app(tmp_path)) as test_client:
        test_client.app.state.runtime.api_keys['openai'] = 'offline-openai-key'
        yield test_client


# ---------------------------------------------------------------- terminal statuses are final

def test_storage_never_rewrites_a_terminal_status_or_message(client):
    store = client.app.state.runtime.store
    job = store.create_job('book-without-worker', 'render', 1)
    cancelled = client.post(f"/api/jobs/{job['id']}/cancel").json()
    assert cancelled['status'] == 'cancelled'
    store.update_job(job['id'], status='interrupted', message='Server stopped.', error='late', resume_after='x')
    stored = store.job(job['id'])
    assert stored['status'] == 'cancelled' and stored['message'] == cancelled['message']
    assert stored['error'] is None and 'resume_after' not in stored
    # Non-outcome bookkeeping is still recorded.
    store.update_job(job['id'], progress=1)
    assert store.job(job['id'])['progress'] == 1


def test_jobs_stored_with_pre_0_2_audio_are_listed_without_internal_fields(client):
    # A listen job saved by an earlier version kept the whole take, recipe hashes and cache marker included.
    store = client.app.state.runtime.store
    asset = 'a' * 64
    job = store.create_job('book-legacy', 'listen', 1)
    legacy = {'mode': 'simple', 'available': True, 'url': f'/api/books/book-legacy/listen/audio/{asset}',
              'asset_id': asset, 'duration': 1.5, 'provider': 'system', 'model': 'macos-say', 'voice': 'Fred',
              'session_id': 's' * 64, 'segment_id': 'p1', 'created_at': '2026-09-01T00:00:00+00:00',
              'cache_hit': True, 'fingerprint': 'f' * 64, 'recipe': 'r' * 64, 'synthesis_key': 'k',
              'source_anchor': {'schema_version': 1}}
    store.update_job(job['id'], status='completed', audio=legacy)
    listed = next(j for j in client.get('/api/jobs', params={'book_id': 'book-legacy'}).json() if j['id'] == job['id'])
    assert listed['audio']['url'] == legacy['url'] and listed['audio']['asset_id'] == asset
    for name in ('mode', 'available', 'cache_hit', 'fingerprint', 'recipe', 'synthesis_key', 'source_anchor'):
        assert name not in listed['audio']
    assert store.job(job['id'])['audio']['fingerprint'] == 'f' * 64  # storage keeps it


@pytest.mark.parametrize('stopping', [False, True])
def test_a_job_cancelled_while_queued_is_not_settled_again_by_its_worker(client, stopping):
    runtime = client.app.state.runtime
    job = runtime.store.create_job('book-without-worker', 'render', 1)
    client.post(f"/api/jobs/{job['id']}/cancel")
    before = runtime.store.job(job['id'])
    ran = []
    if stopping:
        runtime.stopping.set()
    try:
        # The worker slot comes up after the cancel (or during shutdown).
        runtime.run(job, lambda: ran.append(True))
    finally:
        runtime.stopping.clear()
    after = runtime.store.job(job['id'])
    assert not ran
    assert after['status'] == 'cancelled' and after['message'] == before['message']


def test_a_cancelled_queued_future_during_shutdown_stays_cancelled(client):
    runtime = client.app.state.runtime
    store = runtime.store
    job = store.create_job('book-without-worker', 'listen_chapter', 1)
    client.post(f"/api/jobs/{job['id']}/cancel")
    runtime.stopping.set()
    try:
        # What the chapter/listen/preview done-callbacks write when shutdown cancels the queued future.
        store.update_job(job['id'], status='interrupted', cancel_requested=True, message='Stopped before any chunk was requested.')
    finally:
        runtime.stopping.clear()
    assert store.job(job['id'])['status'] == 'cancelled'


# ---------------------------------------------------------------- unambiguous field names

LEGACY = [
    ({'kind': 'pipeline', 'mode': 'parallel', 'run_id': 'pr_1', 'steps': ['structure']}, {'scheduling': 'parallel'}, {'mode'}),
    ({'kind': 'series', 'limits': {'max_requests': 5, 'max_input_tokens': 1000, 'max_output_tokens': 1000, 'budget_usd': None},
      'plan_fingerprint': 'f' * 64},
     {'analysis_limits': {'max_requests': 5, 'max_input_tokens': 1000, 'max_output_tokens': 1000, 'budget_usd': None}},
     {'limits', 'plan_fingerprint'}),
    ({'kind': 'listen_chapter', 'limits': {'rpm': 10, 'tpm': 10000, 'rpd': 100}},
     {'speech_limits': {'rpm': 10, 'tpm': 10000, 'rpd': 100}}, {'limits'}),
    ({'kind': 'performance', 'mode': 'cast', 'performance_id': 'pf_1'}, {'mode': 'cast'}, set()),
]


@pytest.mark.parametrize('legacy, expected, absent', LEGACY)
def test_jobs_stored_with_legacy_names_are_read_with_new_names(client, legacy, expected, absent):
    store = client.app.state.runtime.store
    job = {'id': 'a' * 32, 'book_id': 'legacy-book', 'status': 'completed', 'progress': 1, 'total': 1, 'message': 'Done',
           'error': None, 'created_at': '2026-09-01T00:00:00+00:00', 'updated_at': '2026-09-01T00:00:00+00:00',
           'cancel_requested': False, **legacy}
    with store.lock, store.connect() as conn:
        conn.execute('INSERT INTO jobs(id,book_id,body) VALUES (?,?,?)', (job['id'], job['book_id'], json.dumps(job)))
    listed = client.get('/api/jobs', params={'book_id': 'legacy-book'}).json()[0]
    for name, value in expected.items():
        assert listed[name] == value
    assert not absent & listed.keys()
    assert client.post(f"/api/jobs/{job['id']}/cancel").json() == listed
    # Reading never rewrites the stored document.
    with store.lock, store.connect() as conn:
        assert json.loads(conn.execute('SELECT body FROM jobs WHERE id=?', (job['id'],)).fetchone()[0]) == job


def test_series_parent_carries_analysis_limits_and_keeps_its_fingerprint_private(client, monkeypatch):
    series = client.post('/api/series', json={'name': 'The Lanterns'}).json()
    book = import_text(client, 'Chapter One\n\nMara found the lamp.')
    client.put(f"/api/books/{book['id']}/series", json={'series_id': series['id'], 'position': 1.0})
    monkeypatch.setattr('bardic.analysis.analyze_book', lambda book, *args, **kwargs: deepcopy(book))
    limits = {'max_requests': 7, 'max_input_tokens': 5000, 'max_output_tokens': 4000, 'budget_usd': 0.5}
    parent = client.post(f"/api/series/{series['id']}/process", json={'provider': 'openai', 'limits': limits}).json()
    assert parent['analysis_limits'] == limits and 'limits' not in parent and 'plan_fingerprint' not in parent
    wait_job(client, parent['id'])
    run = client.get(f"/api/series/{series['id']}/runs").json()['runs'][0]
    assert run['analysis_limits'] == limits and 'plan_fingerprint' not in run
    stored = client.app.state.runtime.store.job(parent['id'])
    assert len(stored['plan_fingerprint']) == 64, 'storage keeps the confirmed plan fingerprint'


def test_pipeline_job_reports_scheduling_not_mode(client):
    book = import_text(client)
    response = client.post(f"/api/books/{book['id']}/analysis-pipeline/runs",
                           json={'steps': ['structure'], 'mode': 'parallel', 'limits': {'max_requests': 1}})
    assert response.status_code == 200, response.text
    job = response.json()['job']
    assert job['scheduling'] == 'parallel' and 'mode' not in job
    assert wait_job(client, job['id'])['scheduling'] == 'parallel'


def test_chapter_job_reports_speech_limits(client, monkeypatch):
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-gemini-key'
    limits = {'rpm': 1000, 'tpm': 10_000_000, 'rpd': 1000}
    assert client.post('/api/settings', json={'tts_limits': {DEFAULT_TTS_MODEL: limits}}).status_code == 200
    gate = threading.Event()
    monkeypatch.setattr('bardic.chapter_listening.synthesize', fake_chunk_synthesizer([], gate=gate))
    book = import_story(client)
    try:
        job = start_chapter(client, book)['job']
        assert job['speech_limits'] == limits and 'limits' not in job
    finally:
        gate.set()
        client.post(f"/api/jobs/{job['id']}/cancel")
        wait_job(client, job['id'])


# ---------------------------------------------------------------- embedded jobs are full jobs

def test_pipeline_inspector_lists_full_jobs(client):
    book = import_text(client)
    job = client.app.state.runtime.store.create_job(book['id'], 'render', 2)
    client.post(f"/api/jobs/{job['id']}/cancel")
    listed = client.get(f"/api/books/{book['id']}/pipeline").json()['jobs']
    assert listed == client.get('/api/jobs', params={'book_id': book['id']}).json()
