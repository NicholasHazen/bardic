"""Explicit, single-request auditions use only mocked local/provider audio."""
from concurrent.futures import Future
import json
import sqlite3
import threading

import pytest

from bardic.audio import DEFAULT_TTS_MODEL
from bardic.diagnostics import DiagnosticRepository
from bardic.resources import publish_metrics
from bardic.store import Store
from bardic.voice_previews import VoicePreviewRepository
from test_app import import_text, wait_job
from test_listen_api import client, renderer
from test_listening import production_snapshot


def begin(client, book, **kwargs):
    return client.post(f"/api/books/{book['id']}/voice-preview", json={
        'provider': 'gemini', 'voice': 'Kore', **kwargs})


@pytest.mark.parametrize('provider', ['gemini', 'system'])
def test_bounded_preview_preserves_other_audio_and_cached_replay_needs_no_provider(client, renderer, monkeypatch, provider):
    calls, _ = renderer
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-private-key'
    monkeypatch.setattr('bardic.app.shutil.which', lambda name: '/fake/' + name)
    book = import_text(client)
    before = production_snapshot(runtime.store)
    segment = book['passages'][0]
    args = {'provider': provider, 'voice': 'Samantha' if provider == 'system' else 'Kore', 'passage_id': segment['id']}
    response = begin(client, book, **args)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['kind'] == 'queued' and result['preview']['text'] == segment['text']
    assert result['job']['kind'] == 'voice_preview' and result['job']['total'] == 1
    job = wait_job(client, result['job']['id'])
    assert job['status'] == 'completed' and job['progress'] == 1
    assert job['audio']['preview_id'] == result['preview']['id'] and job['preview'] == result['preview']
    assert client.get(job['audio']['url']).status_code == 200
    assert production_snapshot(runtime.store) == before and len(calls) == 1
    runtime.api_key = ''
    monkeypatch.setattr('bardic.app.shutil.which', lambda _: None)
    cached = begin(client, book, **args).json()
    assert cached['kind'] == 'cached' and cached['audio']['asset_id'] == job['audio']['asset_id']
    assert len(calls) == 1
    operations = client.get(f"/api/books/{book['id']}/resources").json()['operations']
    operations = [operation for operation in operations if operation['stage'] == 'voice_preview']
    assert len(operations) == 2 and sum(o['cached'] for o in operations) == 1
    assert next(o for o in operations if o['cached'])['request_count'] == 0
    if provider == 'gemini':
        assert next(o for o in operations if not o['cached'])['request_count'] == 1


def test_renamed_character_reuses_audio_without_key_and_preserves_original_provenance(client, renderer):
    calls, _ = renderer
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    character = book['characters'][0]
    first = begin(client, book, character_id=character['id']).json()
    job = wait_job(client, first['job']['id'])
    assert job['status'] == 'completed'
    with runtime.store.connect() as conn:
        original_request = conn.execute('SELECT body FROM voice_preview_requests WHERE id=?', (first['preview']['id'],)).fetchone()[0]
        original_take = conn.execute('SELECT id,body FROM voice_preview_takes WHERE preview_id=?', (first['preview']['id'],)).fetchone()
    rename = client.patch(f"/api/books/{book['id']}/characters/{character['id']}", json={'name': 'Renamed voice'})
    assert rename.status_code == 200
    runtime.api_key = ''
    renamed = begin(client, book, character_id=character['id'])
    assert renamed.status_code == 200, renamed.text
    result = renamed.json()
    assert result['kind'] == 'cached' and result['preview']['character_name'] == 'Renamed voice'
    assert result['preview']['id'] != first['preview']['id']
    assert result['audio']['asset_id'] == job['audio']['asset_id']
    assert result['audio']['preview_id'] == result['preview']['id']
    assert result['audio']['reuse'] == {'schema_version': 1, 'take_id': original_take[0], 'preview_id': first['preview']['id']}
    assert 'resource_usage' not in result['audio'] and len(calls) == 1
    with runtime.store.connect() as conn:
        assert conn.execute('SELECT body FROM voice_preview_requests WHERE id=?', (first['preview']['id'],)).fetchone()[0] == original_request
        assert conn.execute('SELECT body FROM voice_preview_takes WHERE id=?', (original_take[0],)).fetchone()[0] == original_take[1]
        assert conn.execute('SELECT count(*) FROM voice_preview_takes').fetchone()[0] == 2
    usage = client.get(f"/api/books/{book['id']}/resources").json()['operations']
    cached = next(operation for operation in usage if operation['stage'] == 'voice_preview' and operation['cached'])
    assert cached['request_count'] == 0 and cached['estimated_cost_usd'] == 0
    # A later effective delivery change still requires a new paid request.
    assert begin(client, book, character_id=character['id'], direction='A different delivery').status_code == 400


def test_duplicate_active_requests_join_but_other_auditions_and_book_edits_conflict(client, renderer, monkeypatch):
    calls, render = renderer
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    started, release = threading.Event(), threading.Event()
    def slow(*args):
        started.set()
        assert release.wait(5)
        return render(*args)
    monkeypatch.setattr('bardic.app.synthesize', slow)
    try:
        first = begin(client, book).json()
        assert started.wait(3)
        same = begin(client, book).json()
        assert same['job']['id'] == first['job']['id']
        assert begin(client, book, voice='Puck').status_code == 409
        assert client.patch(f"/api/books/{book['id']}/characters/{book['characters'][0]['id']}", json={'voices': {'gemini': {'id': 'Puck'}}}).status_code == 409
    finally:
        release.set()
    assert wait_job(client, first['job']['id'])['status'] == 'completed'
    assert len(calls) == 1


def test_cancel_in_flight_retains_finished_audio_for_explicit_replay(client, renderer, monkeypatch):
    calls, render = renderer
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    started, release = threading.Event(), threading.Event()
    def slow(*args):
        started.set()
        assert release.wait(5)
        return render(*args)
    monkeypatch.setattr('bardic.app.synthesize', slow)
    try:
        response = begin(client, book).json()
        assert started.wait(3)
        client.post(f"/api/jobs/{response['job']['id']}/cancel")
    finally:
        release.set()
    job = wait_job(client, response['job']['id'])
    assert job['status'] == 'cancelled'
    runtime.pool.submit(lambda: None).result(timeout=3)
    event = client.get('/api/diagnostics').json()['events'][0]
    assert event['event'] == 'voice_preview_stopped' and event['job_id'] == job['id']
    assert event['status'] == 'cancelled' and 'session_id' not in event
    assert begin(client, book).json()['kind'] == 'cached' and len(calls) == 1


def test_queued_cancel_never_calls_provider_and_configuration_is_snapshotted(client, renderer, monkeypatch):
    calls, _ = renderer
    runtime = client.app.state.runtime
    runtime.api_key = 'before-key'
    book = import_text(client)
    blocker, release = threading.Event(), threading.Event()
    def occupy():
        blocker.set()
        assert release.wait(5)
    runtime.pool.submit(occupy)
    assert blocker.wait(3)
    try:
        queued = begin(client, book).json()
        assert queued['job']['status'] == 'queued'
        assert client.post(f"/api/jobs/{queued['job']['id']}/cancel").json()['status'] == 'cancelled'
        second = begin(client, book, voice='Puck').json()
        runtime.api_key = 'after-key'
        runtime.preferences['tts_model'] = 'gemini-3.8-flash-lite-tts'
    finally:
        release.set()
    assert wait_job(client, second['job']['id'])['status'] == 'completed'
    assert len(calls) == 1 and calls[0]['key'] == 'before-key'
    assert calls[0]['model'] == DEFAULT_TTS_MODEL
    assert runtime.store.job(queued['job']['id'])['status'] == 'cancelled'
    events = client.get('/api/diagnostics').json()['events']
    assert any(event['event'] == 'voice_preview_stopped' and event['job_id'] == queued['job']['id'] for event in events)


def test_failure_is_redacted_retains_usage_and_requires_explicit_retry(client, renderer, monkeypatch):
    _, render = renderer
    runtime = client.app.state.runtime
    runtime.api_key = 'private-test-key'
    book = import_text(client)
    attempts = []
    def fail(*args):
        attempts.append(True)
        publish_metrics(request_count=1, input_tokens=11, estimated_cost_usd=.002)
        raise ValueError('provider failed with private-test-key')
    monkeypatch.setattr('bardic.app.synthesize', fail)
    first = begin(client, book, passage_id=book['passages'][0]['id']).json()
    job = wait_job(client, first['job']['id'])
    assert job['status'] == 'failed' and 'private-test-key' not in job['error']
    assert len(attempts) == 1
    runtime.pool.submit(lambda: None).result(timeout=3)
    events = client.get('/api/diagnostics').json()['events']
    assert len(events) == 1 and events[0]['event'] == 'voice_preview_failed'
    assert events[0]['job_id'] == job['id'] and events[0]['passage_id'] == book['passages'][0]['id']
    assert events[0]['status'] == 'failed' and 'session_id' not in events[0]
    assert 'private-test-key' not in json.dumps(events) and 'provider failed' not in json.dumps(events)
    usage = client.get(f"/api/books/{book['id']}/resources", params={'run_id': job['id']}).json()['operations']
    assert usage[0]['status'] == 'failed' and usage[0]['input_tokens'] == 11
    monkeypatch.setattr('bardic.app.synthesize', render)
    second = begin(client, book, passage_id=book['passages'][0]['id']).json()
    assert second['preview']['id'] == first['preview']['id']
    assert wait_job(client, second['job']['id'])['status'] == 'completed'


def test_preview_input_constraints_and_availability_errors(client, renderer, monkeypatch):
    runtime = client.app.state.runtime
    book = import_text(client)
    assert begin(client, book).status_code == 400
    assert begin(client, book, provider='other').status_code == 422
    assert begin(client, book, text='User-supplied prose').status_code == 422
    assert begin(client, book, voice='x' * 257).status_code == 422
    assert begin(client, book, direction='No character').status_code == 400
    no_passage = begin(client, book, passage_direction='No character')
    assert no_passage.status_code == 400 and no_passage.json()['code'] == 'passage_direction_incomplete'
    assert begin(client, book, character_id='missing').json()['code'] == 'unknown_character'
    other = import_text(client, 'A wholly different source.')
    unknown = begin(client, book, passage_id=other['passages'][0]['id'])
    assert unknown.status_code == 400 and unknown.json()['code'] == 'unknown_passage'
    runtime.api_key = 'offline-key'
    runtime.stopping.set()
    assert begin(client, book).status_code == 503
    runtime.stopping.clear()
    monkeypatch.setattr('bardic.app.shutil.which', lambda _: None)
    assert begin(client, book, provider='system').status_code == 400
    assert client.get(f"/api/books/{book['id']}/voice-preview/audio/" + 'f'*64).status_code == 404


def test_series_reservation_and_archived_book_prevent_generation(client, renderer):
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    parent = runtime.store.create_job('series:synthetic', 'series')
    runtime.store.update_job(parent['id'], book_ids=[book['id']])
    assert begin(client, book).status_code == 409
    runtime.store.update_job(parent['id'], status='completed')
    from bardic.library import LibraryRepository
    LibraryRepository(runtime.store).archive_book(book['id'])
    archived = begin(client, book)
    assert archived.status_code == 409 and archived.json()['code'] == 'book_archived'


def test_executor_failure_and_cancelled_future_settle_job(client, renderer, monkeypatch):
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    def fail_submit(*args):
        raise RuntimeError('closed')
    monkeypatch.setattr(runtime.pool, 'submit', fail_submit)
    assert begin(client, book).status_code == 503
    assert runtime.store.jobs(book['id'])[0]['status'] == 'failed'
    failure = client.get('/api/diagnostics').json()['events'][0]
    assert failure['event'] == 'voice_preview_submit_failed' and failure['status'] == 'failed'
    future = Future()
    future.cancel()
    monkeypatch.setattr(runtime.pool, 'submit', lambda *args: future)
    response = begin(client, book).json()
    assert runtime.store.job(response['job']['id'])['status'] == 'cancelled'
    stopped = client.get('/api/diagnostics').json()['events'][0]
    assert stopped['event'] == 'voice_preview_stopped' and stopped['job_id'] == response['job']['id']


def test_broken_diagnostics_never_replace_preview_failure_or_prevent_retry(client, renderer, monkeypatch):
    _, render = renderer
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    def broken_log(*args, **kwargs):
        raise sqlite3.OperationalError('Diagnostics unavailable')
    def broken_voice(*args):
        raise ValueError('Original preview failure')
    monkeypatch.setattr(DiagnosticRepository, 'record', broken_log)
    monkeypatch.setattr('bardic.app.synthesize', broken_voice)
    first = begin(client, book).json()
    failed = wait_job(client, first['job']['id'])
    assert failed['status'] == 'failed' and failed['error'] == 'Original preview failure'
    monkeypatch.setattr('bardic.app.synthesize', render)
    second = begin(client, book).json()
    assert wait_job(client, second['job']['id'])['status'] == 'completed'


def test_restart_marks_unfinished_preview_interrupted_and_keeps_saved_audio(client, renderer):
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    response = begin(client, book).json()
    job = wait_job(client, response['job']['id'])
    queued = runtime.store.create_job(book['id'], 'voice_preview', 1)
    restored = Store(runtime.store.root)
    assert restored.job(queued['id'])['status'] == 'interrupted'
    audio = VoicePreviewRepository(restored).cached(book['id'], response['preview']['id'])
    assert audio['asset_id'] == job['audio']['asset_id']


def test_presented_audition_take_keeps_usage_and_format_version_in_storage():
    from bardic.voice_previews import present_take
    audio = present_take('b', {'asset_id': 'a' * 64, 'duration': 1.0, 'provider': 'gemini', 'model': DEFAULT_TTS_MODEL,
                               'voice': 'Kore', 'created_at': '2026-09-28T00:00:00Z', 'preview_id': 'p' * 64,
                               'schema_version': 1, 'fingerprint': 'f' * 64, 'resource_usage': {'output_tokens': 7}})
    assert not {'schema_version', 'resource_usage', 'fingerprint'} & set(audio)
    assert audio['preview_id'] == 'p' * 64 and present_take('b', audio) == audio
