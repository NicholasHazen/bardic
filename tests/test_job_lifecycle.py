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
    store.update_job(job['id'], status='completed', audio=legacy, session_id='s' * 64, segment_id='p1', provider='system', model='macos-say', phase='simple_listen')
    listed = next(j for j in client.get('/api/jobs', params={'book_id': 'book-legacy'}).json() if j['id'] == job['id'])
    assert listed['audio']['url'] == legacy['url'] and listed['audio']['asset_id'] == asset
    for name in ('mode', 'available', 'cache_hit', 'fingerprint', 'recipe', 'synthesis_key', 'source_anchor'):
        assert name not in listed['audio']
    assert listed['audio']['passage_id'] == 'p1' and 'segment_id' not in listed['audio'], 'the wire says passage'
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
    job = store.create_job('book-without-worker', 'render', 1)  # any kind: the rule is the store's, not a kind's
    client.post(f"/api/jobs/{job['id']}/cancel")
    runtime.stopping.set()
    try:
        # What the chapter/listen/preview done-callbacks write when shutdown cancels the queued future.
        store.update_job(job['id'], status='interrupted', cancel_requested=True, message='Stopped before any chunk was requested.')
    finally:
        runtime.stopping.clear()
    assert store.job(job['id'])['status'] == 'cancelled'


# ---------------------------------------------------------------- unambiguous field names

# The fields a job of these kinds always has (its schema requires them); the legacy part is what differs.
CHAPTER = {'session_id': 's' * 64, 'chapter_id': 'c1', 'provider': 'gemini', 'model': 'gemini-3.8-flash-tts', 'voice': 'Kore',
           'intent': 'queue', 'scope_start_segment_id': 'p1', 'focus_segment_id': 'p1', 'ramp_restart': 0, 'joins': 0,
           'phase': 'chapter_listen', 'chunks': [],
           'chunking': {'ramp_seconds': [], 'target_seconds': 60.0, 'concurrency': 1},
           'calibration': {'samples': [], 'truncated_chars_per_second': None, 'max_chars': None, 'chars_per_second': 14.0,
                           'chars_per_second_low': 10.0, 'realtime_factor': 2.0}}
PERFORMANCE = {'performance_id': 'pf_1', 'provider': 'system', 'model': 'macos-say', 'phase': 'performance'}

LEGACY = [
    ({'kind': 'pipeline', 'mode': 'parallel', 'run_id': 'pr_1', 'steps': ['structure']}, {'scheduling': 'parallel'}, {'mode'}),
    ({'kind': 'series', 'limits': {'max_requests': 5, 'max_input_tokens': 1000, 'max_output_tokens': 1000, 'budget_usd': None},
      'plan_fingerprint': 'f' * 64},
     {'analysis_limits': {'max_requests': 5, 'max_input_tokens': 1000, 'max_output_tokens': 1000, 'budget_usd': None}},
     {'limits', 'plan_fingerprint'}),
    ({'kind': 'listen_chapter', **CHAPTER, 'limits': {'rpm': 10, 'tpm': 10000, 'rpd': 100}},
     {'speech_limits': {'rpm': 10, 'tpm': 10000, 'rpd': 100}}, {'limits'}),
    ({'kind': 'performance', **PERFORMANCE, 'mode': 'cast'}, {'mode': 'cast', 'child_job_ids': [], 'child_job_id': None}, set()),
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


def test_series_parent_carries_analysis_limits_and_keeps_its_fingerprint_private(client):
    series = client.post('/api/series', json={'name': 'The Lanterns'}).json()
    book = import_text(client, 'Chapter One\n\nMara found the lamp.')
    client.put(f"/api/books/{book['id']}/series", json={'series_id': series['id'], 'position': 1.0})
    # Series runs are step-pipeline runs; a local step needs no key and sends nothing.
    limits = {'max_requests': 7, 'max_input_tokens': 5000, 'max_output_tokens': 4000, 'budget_usd': 0.5}
    parent = client.post(f"/api/series/{series['id']}/process",
                         json={'steps': ['census'], 'scheduling': 'parallel', 'limits': limits}).json()
    assert parent['analysis_limits'] == limits and 'limits' not in parent and 'plan_fingerprint' not in parent
    assert parent['scheduling'] == 'parallel' and 'mode' not in parent
    assert wait_job(client, parent['id'])['status'] == 'completed'
    run = client.get(f"/api/series/{series['id']}/runs").json()['runs'][0]
    assert run['analysis_limits'] == limits and 'plan_fingerprint' not in run
    assert all('plan_fingerprint' not in child for child in run['children'])
    stored = client.app.state.runtime.store.job(parent['id'])
    assert len(stored['plan_fingerprint']) == 64, 'storage keeps the confirmed plan fingerprint'


def test_series_parent_stored_with_pipeline_era_names_is_read_with_the_current_names(client):
    store = client.app.state.runtime.store
    parent = store.create_job('series:series_old', 'series')
    # A pipeline series run recorded before contract 0.3.0 stored `mode` and `limits`.
    with store.lock, store.connect() as conn:
        body = {**parent, 'mode': 'serial', 'limits': {'max_requests': None, 'max_input_tokens': None,
                                                       'max_output_tokens': None, 'budget_usd': None}}
        conn.execute('UPDATE jobs SET body=? WHERE id=?', (json.dumps(body), parent['id']))
    read = client.get('/api/jobs', params={'book_id': 'series:series_old'}).json()[0]
    assert read['scheduling'] == 'serial' and read['analysis_limits']['max_requests'] is None
    assert 'mode' not in read and 'limits' not in read


def test_pipeline_job_reports_scheduling_not_mode(client):
    book = import_text(client)
    response = client.post(f"/api/books/{book['id']}/analysis-pipeline/runs",
                           json={'steps': ['structure'], 'scheduling': 'parallel', 'limits': {'max_requests': 1}})
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


# ---------------------------------------------------------------- every kind has its own fields

def test_a_job_document_has_exactly_the_always_sent_fields_of_its_kind(client):
    from bardic.store import JOB_DEFAULTS, upgrade_job
    base = {'id': 'a' * 32, 'book_id': 'b', 'status': 'queued', 'progress': 0, 'total': 0, 'message': '', 'error': None,
            'created_at': 't', 'updated_at': 't', 'cancel_requested': False}
    # A worker reports these later, and an older version never stored them: reading fills each with "not set yet".
    assert upgrade_job({**base, 'kind': 'listen'})['audio'] is None
    assert upgrade_job({**base, 'kind': 'voice_preview'})['audio'] is None
    chapter = upgrade_job({**base, 'kind': 'listen_chapter'})
    assert {name: chapter[name] for name in JOB_DEFAULTS['listen_chapter']} == dict.fromkeys(JOB_DEFAULTS['listen_chapter'])
    assert upgrade_job({**base, 'kind': 'performance'})['child_job_ids'] == []
    assert upgrade_job({**base, 'kind': 'pipeline'})['run_id'] is None
    # Nothing is added to a kind that has no such fields, a stored value is kept, and the defaults are not shared.
    assert upgrade_job({**base, 'kind': 'render'}) == {**base, 'kind': 'render'}
    assert upgrade_job({**base, 'kind': 'listen', 'audio': {'url': 'u'}})['audio'] == {'url': 'u'}
    first, second = upgrade_job({**base, 'kind': 'series'}), upgrade_job({**base, 'kind': 'series'})
    first['book_ids'].append('x')
    assert second['book_ids'] == []
    # A series parent's book_id names its series, so an old record without series_id still has one.
    assert upgrade_job({**base, 'book_id': 'series:series_9', 'kind': 'series'})['series_id'] == 'series_9'


def test_a_started_job_has_the_fields_of_its_own_kind_and_no_other(client):
    """Strict contract validation refuses a field a kind does not declare, so this pins the presence side."""
    book = import_text(client, 'Chapter One\n\nMara found the lamp.')
    render = client.post(f"/api/books/{book['id']}/render", json={'provider': 'system'})
    assert render.status_code == 200
    job = render.json()
    assert job['kind'] == 'render'
    assert set(job) - {'resume_after'} == {'id', 'book_id', 'kind', 'status', 'progress', 'total', 'message', 'error',
                                          'created_at', 'updated_at', 'cancel_requested'}
    other = import_text(client, 'Chapter One\n\nTomas lit the second lamp.')  # a book with no render job holding it
    started = client.post(f"/api/books/{other['id']}/analysis-pipeline/runs", json={'steps': ['structure'], 'limits': {'max_requests': 5}})
    assert started.status_code == 200, started.text
    pipeline = started.json()['job']
    assert pipeline['kind'] == 'pipeline' and pipeline['run_id'] and pipeline['steps'] == ['structure']
    assert not {'series_id', 'series_run_id', 'audio', 'session_id', 'passage_id'} & set(pipeline)


# ---------------------------------------------------------------- what a stopped job says about itself

def test_a_series_child_that_never_started_is_marked_however_it_stopped(client, tmp_path):
    from bardic.store import Store
    store = client.app.state.runtime.store
    queued = store.create_job('book_a', 'pipeline')
    store.update_job(queued['id'], series_run_id='run_1', series_id='series_1', position=1.0)
    running = store.create_job('book_b', 'pipeline')
    store.update_job(running['id'], series_run_id='run_1', series_id='series_1', position=2.0, status='running', run_id='r1')
    alone = store.create_job('book_c', 'pipeline')  # Not a series child: no marker.
    # Cancelling a queued child on its own marks it.
    cancelled = client.post(f"/api/jobs/{queued['id']}/cancel").json()
    assert cancelled['status'] == 'cancelled' and cancelled['not_started'] is True
    # A restart interrupts queued children that never started (marked) and running ones (not marked).
    queued_again = store.create_job('book_d', 'pipeline')
    store.update_job(queued_again['id'], series_run_id='run_1', series_id='series_1', position=3.0)
    restored = Store(tmp_path)
    assert restored.job(queued_again['id'])['status'] == 'interrupted' and restored.job(queued_again['id'])['not_started'] is True
    assert restored.job(running['id'])['status'] == 'interrupted' and 'not_started' not in restored.job(running['id'])
    assert restored.job(alone['id'])['status'] == 'interrupted' and 'not_started' not in restored.job(alone['id'])


def test_a_chapter_job_interrupted_by_a_restart_is_not_waiting_and_plans_nothing(client, tmp_path):
    from bardic.store import Store
    store = client.app.state.runtime.store
    job = store.create_job('book_a', 'listen_chapter')
    store.update_job(job['id'], status='running', waiting_seconds=12.5, projection=[{'n': 1}], closing=False)
    restored = Store(tmp_path).job(job['id'])
    assert restored['status'] == 'interrupted' and restored['waiting_seconds'] is None and restored['projection'] == []


def test_a_series_child_recorded_by_an_intermediate_build_reads_with_the_fields_a_child_always_has(client):
    from bardic.store import upgrade_job
    base = {'id': 'a' * 32, 'book_id': 'b', 'kind': 'pipeline', 'status': 'queued', 'progress': 0, 'total': 0, 'message': '',
            'error': None, 'created_at': 't', 'updated_at': 't', 'cancel_requested': False, 'series_run_id': 'run_1'}
    child = upgrade_job(dict(base))
    assert (child['title'], child['consent_fingerprint'], child['context_pending'], child['context_sources']) == ('', None, [], [])
    # A stored value is kept, and a pipeline job started from the book gets no series fields.
    assert upgrade_job({**base, 'title': 'Vol 1', 'consent_fingerprint': 'f'})['consent_fingerprint'] == 'f'
    standalone = upgrade_job({k: v for k, v in base.items() if k != 'series_run_id'})
    assert 'consent_fingerprint' not in standalone
