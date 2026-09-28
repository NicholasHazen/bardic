"""Simple listening routes never spend provider credits in these tests."""
from copy import deepcopy
import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from bardic.app import create_app
from bardic.audio import DEFAULT_TTS_MODEL, render_fingerprint
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
                'duration':.1,'provider':provider,'model':model,'voice':character.get('system_voice') if provider=='system' else character['voice']}
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
    assert archived.status_code == 400
    assert client.get(audio['url']).status_code == 200, 'Archiving retains readable saved assets'
