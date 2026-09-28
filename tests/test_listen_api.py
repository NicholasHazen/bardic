"""Simple listening routes never spend provider credits in these tests."""
from copy import deepcopy
import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from bardic.app import create_app
from bardic.audio import DEFAULT_TTS_MODEL, render_fingerprint, voice_id
from bardic.resources import publish_metrics
from test_app import import_text, wait_job
from test_audio import wav_bytes
from test_listening import production_snapshot


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key in ('GEMINI_API_KEY','GOOGLE_API_KEY','OPENAI_API_KEY','ANTHROPIC_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr('bardic.app.list_system_voices', lambda: [])
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda *_args, **_kwargs: pytest.fail('No live provider calls allowed'))
    with TestClient(create_app(tmp_path)) as client:
        yield client


@pytest.fixture
def renderer(monkeypatch):
    calls = []
    def synthesize(segment, character, scene, provider, model, key, path):
        calls.append({'segment':deepcopy(segment),'character':deepcopy(character),'scene':deepcopy(scene),
                      'provider':provider,'model':model,'key':key})
        if provider == 'gemini':
            publish_metrics(request_count=1, http_status=200, input_tokens=12, output_tokens=20,
                            cached_input_tokens=0, estimated_cost_usd=.002, cost_basis='offline_test_usage')
        path.write_bytes(wav_bytes(frames=2400+len(calls)))
        return {'fingerprint':render_fingerprint(segment,character,scene,provider,model),
                'duration':.1,'provider':provider,'model':model,'voice':voice_id(character, provider)}
    monkeypatch.setattr('bardic.app.synthesize', synthesize)
    return calls, synthesize


def begin(client, book, provider='gemini', segment=None):
    segment = segment or book['segments'][0]
    response = client.post(f"/api/books/{book['id']}/listen", json={
        'provider':provider,'voice':'Kore' if provider=='gemini' else 'Samantha',
        'model':DEFAULT_TTS_MODEL if provider=='gemini' else 'macos-say','segment_id':segment['id']})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('provider',['gemini','system'])
def test_lazy_single_passage_preserves_enhanced_work_and_cached_audio_needs_no_provider(client, renderer, monkeypatch, provider):
    calls, _ = renderer
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    monkeypatch.setattr('bardic.app.shutil.which', lambda name:'/fake/'+name if name in {'say','ffmpeg'} else None)
    book = import_text(client)
    # An existing enhanced take is separate from simple listening.
    enhanced = client.post(f"/api/books/{book['id']}/render", json={'provider':'gemini','segment_id':book['segments'][0]['id']}).json()
    assert wait_job(client,enhanced['id'])['status'] == 'completed'
    before = deepcopy(runtime.store.book(book['id']))
    projection = production_snapshot(runtime.store)
    original = (runtime.store.root/'originals'/book['id']/'source.txt').read_bytes()
    result = begin(client,book,provider)
    assert result['cached'] is False and result['job']['kind'] == 'listen' and result['job']['total'] == 1
    finished = wait_job(client,result['job']['id'])
    assert finished['status'] == 'completed' and finished['progress'] == 1
    assert finished['audio']['mode'] == 'simple'
    assert len(calls) == 2, 'Only one requested simple passage should synthesize'
    assert calls[-1]['segment'] == {'id':book['segments'][0]['id'],'text':book['segments'][0]['text']}
    assert calls[-1]['scene'] == {}
    url = finished['audio']['url']
    wav = client.get(url)
    assert wav.status_code == 200 and wav.headers['content-type'].startswith('audio/wav')
    session_id = result['session']['id']
    indexed = client.get(f"/api/books/{book['id']}/listen/takes",params={'session_id':session_id}).json()
    assert len(indexed['takes']) == 1 and indexed['takes'][0]['segment_id'] == book['segments'][0]['id']
    # Cache retrieval comes before checking keys or installed speech tools.
    runtime.api_key = ''
    monkeypatch.setattr('bardic.app.shutil.which',lambda _name:None)
    cached = begin(client,book,provider)
    assert cached['cached'] is True and 'job' not in cached
    assert cached['audio']['asset_id'] == finished['audio']['asset_id']
    assert len(calls) == 2 and len(runtime.store.jobs(book['id'])) == 2
    assert runtime.store.book(book['id']) == before
    assert production_snapshot(runtime.store) == projection
    assert (runtime.store.root/'originals'/book['id']/'source.txt').read_bytes() == original
    usage = client.get(f"/api/books/{book['id']}/resources").json()
    operations = [row for row in usage['operations'] if row['stage']=='simple_listen']
    assert len(operations) == 2 and all(row['status']=='completed' for row in operations)
    generated = next(row for row in operations if not row['cached'])
    reused = next(row for row in operations if row['cached'])
    assert generated['run_id'] == result['job']['id'] and generated['chapter_id'] == book['segments'][0]['chapter_id']
    assert generated['audio_seconds'] > 0 and generated['output_bytes'] > 44
    assert generated['elapsed_seconds'] >= 0
    assert generated['request_count'] == (1 if provider=='gemini' else 0)
    assert generated['estimated_cost_usd'] == (.002 if provider=='gemini' else 0)
    assert reused['request_count'] == 0 and reused['estimated_cost_usd'] == 0
    assert 'offline-key' not in json.dumps(usage)


def test_cancelling_current_request_retains_completed_simple_take_without_publishing_enhanced_audio(client, renderer, monkeypatch):
    calls, synthesize = renderer
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    before = production_snapshot(runtime.store)
    entered, release = threading.Event(), threading.Event()
    def blocked(*args):
        entered.set()
        assert release.wait(3), 'Test renderer was not released'
        return synthesize(*args)
    monkeypatch.setattr('bardic.app.synthesize', blocked)
    result = begin(client,book)
    try:
        assert entered.wait(2)
        cancellation = client.post(f"/api/jobs/{result['job']['id']}/cancel")
        assert cancellation.status_code == 200
    finally:
        release.set()
    job = wait_job(client,result['job']['id'])
    assert job['status'] == 'cancelled'
    takes = client.get(f"/api/books/{book['id']}/listen/takes",params={'session_id':result['session']['id']}).json()['takes']
    assert len(takes) == 1
    assert client.get(takes[0]['audio']['url']).status_code == 200
    assert production_snapshot(runtime.store) == before
    assert begin(client,book)['cached'] is True
    assert len(calls) == 1


def test_failed_generation_is_retryable_and_resource_failure_retains_reported_usage(client, renderer, monkeypatch):
    calls, synthesize = renderer
    runtime = client.app.state.runtime
    runtime.api_key = 'private-offline-key'
    book = import_text(client)
    def fails(*_args):
        publish_metrics(request_count=1,http_status=429,estimated_cost_usd=None)
        raise ValueError('Quota unavailable private-offline-key')
    monkeypatch.setattr('bardic.app.synthesize',fails)
    result = begin(client,book)
    job = wait_job(client,result['job']['id'])
    assert job['status'] == 'failed' and 'private-offline-key' not in json.dumps(job)
    assert client.get(f"/api/books/{book['id']}/listen/takes",params={'session_id':result['session']['id']}).json()['takes'] == []
    usage = client.get(f"/api/books/{book['id']}/resources",params={'run_id':job['id']}).json()
    assert len(usage['operations']) == 1
    assert usage['operations'][0]['status'] == 'failed' and usage['operations'][0]['request_count'] == 1
    assert usage['operations'][0]['estimated_cost_usd'] is None
    monkeypatch.setattr('bardic.app.synthesize',synthesize)
    retry = begin(client,book)
    assert wait_job(client,retry['job']['id'])['status'] == 'completed'
    assert len(calls) == 1


def test_book_and_session_scopes_invalid_inputs_archive_and_busy_guards(client, renderer):
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book, other = import_text(client),import_text(client,'Another book.')
    result = begin(client,book)
    audio = wait_job(client,result['job']['id'])['audio']
    base = f"/api/books/{book['id']}/listen"
    other_base = f"/api/books/{other['id']}/listen"
    assert client.get(other_base+'/takes',params={'session_id':result['session']['id']}).status_code == 404
    assert client.get(other_base+'/audio/'+audio['asset_id']).status_code == 404
    assert client.get(base+'/audio/'+'g'*64).status_code == 404
    assert client.post(base,json={'provider':'openai','segment_id':book['segments'][0]['id']}).status_code in {400,422}
    assert client.post(base,json={'provider':'gemini','model':'unlisted','segment_id':book['segments'][0]['id']}).status_code == 400
    assert client.post(base,json={'provider':'gemini','segment_id':'missing'}).status_code == 404
    busy = runtime.store.create_job(book['id'],'analyze')
    assert begin(client,book)['cached'] is True, 'Read-only cache reuse remains available during other work'
    blocked = client.post(base,json={'provider':'gemini','segment_id':book['segments'][1]['id']})
    assert blocked.status_code == 409
    runtime.store.update_job(busy['id'],status='cancelled')
    assert client.post(f"/api/books/{book['id']}/archive").status_code == 200
    archived = client.post(base,json={'provider':'gemini','segment_id':book['segments'][0]['id']})
    assert archived.status_code == 409 and archived.json()['code'] == 'book_archived'
    assert client.get(audio['url']).status_code == 200, 'Archiving retains readable saved assets'


def test_cross_book_cached_speech_works_without_key_and_has_zero_new_usage(client, renderer):
    calls, _ = renderer
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    started = begin(client, book)
    audio = wait_job(client, started['job']['id'])['audio']
    other = import_text(client, book['segments'][0]['text'])
    runtime.api_key = ''
    reused = begin(client, other)
    assert reused['cached'] is True and 'job' not in reused
    assert reused['audio']['asset_id'] == audio['asset_id']
    assert reused['audio']['reuse']['book_id'] == book['id']
    assert client.get(reused['audio']['url']).content == client.get(audio['url']).content
    assert len(calls) == 1 and runtime.store.jobs(other['id']) == []
    usage = [row for row in client.get(f"/api/books/{other['id']}/resources").json()['operations']
             if row['stage'] == 'simple_listen']
    assert len(usage) == 1 and usage[0]['cached'] is True
    assert usage[0]['request_count'] == 0 and usage[0]['estimated_cost_usd'] == 0


def test_queued_equivalent_books_recheck_cache_before_spending(client, renderer):
    calls, _ = renderer
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    other = import_text(client, book['segments'][0]['text'])
    entered, release = threading.Event(), threading.Event()
    def occupy_worker():
        entered.set()
        assert release.wait(5)
    runtime.pool.submit(occupy_worker)
    try:
        assert entered.wait(2)
        first, second = begin(client, book), begin(client, other)
        assert first['cached'] is False and second['cached'] is False
    finally:
        release.set()
    one = wait_job(client, first['job']['id'])
    two = wait_job(client, second['job']['id'])
    assert one['status'] == two['status'] == 'completed'
    assert one['audio']['asset_id'] == two['audio']['asset_id']
    assert two['audio']['cache_hit'] is True and len(calls) == 1
    usage = client.get(f"/api/books/{other['id']}/resources", params={'run_id': second['job']['id']}).json()['operations']
    assert len(usage) == 1 and usage[0]['cached'] is True
    assert usage[0]['request_count'] == 0 and usage[0]['estimated_cost_usd'] == 0


@pytest.mark.parametrize('state', ['queued', 'running'])
def test_duplicate_active_listen_requests_join_one_job(client, renderer, monkeypatch, state):
    calls, synthesize = renderer
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    entered, release = threading.Event(), threading.Event()
    def block():
        entered.set()
        assert release.wait(5), 'Test worker was not released'
    if state == 'queued':
        runtime.pool.submit(block)
    else:
        def blocked_render(*args):
            block()
            return synthesize(*args)
        monkeypatch.setattr('bardic.app.synthesize', blocked_render)
    try:
        first = begin(client, book)
        assert entered.wait(2)
        # A missing/replaced key cannot make the same snapshotted request spend
        # again or fail while its already-authorized job continues.
        runtime.api_key = ''
        duplicate = begin(client, book)
        assert duplicate['job']['id'] == first['job']['id']
        assert duplicate['job']['status'] == state
        assert len(runtime.store.jobs(book['id'])) == 1
        changed = client.post(f"/api/books/{book['id']}/listen", json={
            'provider': 'gemini', 'voice': 'Puck', 'segment_id': book['segments'][0]['id']})
        assert changed.status_code == 409
    finally:
        release.set()
    assert wait_job(client, first['job']['id'])['status'] == 'completed'
    assert len(calls) == 1 and calls[0]['key'] == 'offline-key'


def test_cancelled_queued_job_is_not_joined_or_started_later(client, renderer):
    calls, _ = renderer
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
        cancelled = client.post(f"/api/jobs/{first['job']['id']}/cancel").json()
        assert cancelled['status'] == 'cancelled'
        replacement = begin(client, book)
        assert replacement['job']['id'] != first['job']['id']
    finally:
        release.set()
    assert wait_job(client, replacement['job']['id'])['status'] == 'completed'
    assert wait_job(client, first['job']['id'])['status'] == 'cancelled'
    assert len(calls) == 1


def test_cancel_requested_running_job_cannot_be_joined(client, renderer, monkeypatch):
    _, synthesize = renderer
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    entered, release = threading.Event(), threading.Event()
    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return synthesize(*args)
    monkeypatch.setattr('bardic.app.synthesize', blocked)
    try:
        first = begin(client, book)
        assert entered.wait(2)
        assert client.post(f"/api/jobs/{first['job']['id']}/cancel").json()['cancel_requested']
        response = client.post(f"/api/books/{book['id']}/listen", json={
            'provider': 'gemini', 'voice': 'Kore', 'segment_id': book['segments'][0]['id']})
        assert response.status_code == 409
    finally:
        release.set()
    assert wait_job(client, first['job']['id'])['status'] == 'cancelled'


def test_executor_rejection_settles_failed_job_without_spend_and_allows_retry(client, renderer, monkeypatch):
    calls, _ = renderer
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    def rejected(*_args):
        raise RuntimeError('Test worker unavailable')
    with monkeypatch.context() as scoped:
        scoped.setattr(runtime.pool, 'submit', rejected)
        response = client.post(f"/api/books/{book['id']}/listen", json={
            'provider': 'gemini', 'voice': 'Kore', 'segment_id': book['segments'][0]['id']})
    assert response.status_code == 503
    jobs = runtime.store.jobs(book['id'])
    assert len(jobs) == 1 and jobs[0]['status'] == 'failed'
    assert calls == []
    retry = begin(client, book)
    assert wait_job(client, retry['job']['id'])['status'] == 'completed'
    assert len(calls) == 1


def test_executor_cancellation_settles_unstarted_listen_job(client, renderer, monkeypatch):
    from concurrent.futures import Future
    calls, _ = renderer
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    pending = Future()
    monkeypatch.setattr(runtime.pool, 'submit', lambda *_args: pending)
    started = begin(client, book)
    runtime.stopping.set()
    assert pending.cancel()
    settled = runtime.store.job(started['job']['id'])
    assert settled['status'] == 'interrupted' and settled['cancel_requested'] is True
    assert calls == []
