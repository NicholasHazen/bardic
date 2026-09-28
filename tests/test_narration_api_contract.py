"""Contract 0.2.0 fixes for narration, listening, performances and voice previews.

Offline: fake synthesizers, synthetic prose, no provider request can leave the process.
"""
from __future__ import annotations

import sqlite3

import pytest

from bardic import tts_limits
from bardic.audio import DEFAULT_TTS_MODEL
from bardic.resources import ResourceLedger
from test_app import import_text, wait_job
from test_listen_api import begin, client, renderer  # noqa: F401  (fixtures)

CORE = {'url', 'asset_id', 'duration', 'provider', 'model', 'voice', 'created_at'}
# Storage bookkeeping and constant labels that must never reach the wire again.
GONE = {'fingerprint', 'recipe', 'synthesis_key', 'source_anchor', 'available', 'mode', 'cache_hit',
        'performance_id'}


def audio_objects(value):
    """Every dict carrying a playback URL, anywhere in a response."""
    if isinstance(value, dict):
        if isinstance(value.get('url'), str) and ('/audio' in value['url'] or '/listen/' in value['url']):
            yield value
        for item in value.values():
            yield from audio_objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from audio_objects(item)


def assert_audio_refs(value, *, least=1):
    found = list(audio_objects(value))
    assert len(found) >= least, value
    for audio in found:
        assert CORE <= set(audio), f'missing core fields {CORE - set(audio)} in {audio}'
        assert not GONE & set(audio), f'internal or constant fields {GONE & set(audio)} in {audio}'
    return found


def stored_job(runtime, job_id):
    with sqlite3.connect(runtime.store.db) as conn:
        import json
        return json.loads(conn.execute('SELECT body FROM jobs WHERE id=?', (job_id,)).fetchone()[0])


# Item: POST /render during shutdown ------------------------------------------

def test_render_while_stopping_is_503_shutting_down_and_queues_nothing(client, renderer):
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    runtime.stopping.set()
    try:
        response = client.post(f"/api/books/{book['id']}/render", json={'provider': 'gemini'})
    finally:
        runtime.stopping.clear()
    assert response.status_code == 503, response.text
    assert response.json()['code'] == 'shutting_down'
    assert runtime.store.jobs(book['id']) == []


def test_render_refused_by_a_stopped_pool_is_503_and_the_job_is_failed(client, renderer):
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    runtime.pool.shutdown(wait=True)
    response = client.post(f"/api/books/{book['id']}/render", json={'provider': 'gemini'})
    assert response.status_code == 503, response.text
    assert response.json()['code'] == 'shutting_down'
    [job] = runtime.store.jobs(book['id'])
    assert job['kind'] == 'render' and job['status'] == 'failed'


# Item: chapter start over the library's daily request count -------------------

def test_chapter_start_over_the_library_daily_count_is_429_with_retry_after(client):
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    tts_limits.LIMITER.reset()
    try:
        saved = client.post('/api/settings', json={'tts_limits': {DEFAULT_TTS_MODEL: {'rpm': 1000, 'tpm': 10_000_000, 'rpd': 1}}})
        assert saved.status_code == 200, saved.text
        # Another path (for example a voice example) already spent today's only request.
        with ResourceLedger(runtime.store).operation('elsewhere', 'voice_preview', provider='gemini',
                                                     model=DEFAULT_TTS_MODEL, kind='narration') as metrics:
            metrics.update(request_count=1)
        book = import_text(client)
        response = client.post(f"/api/books/{book['id']}/listen/chapter", json={
            'provider': 'gemini', 'voice': 'Kore', 'model': DEFAULT_TTS_MODEL, 'segment_id': book['segments'][0]['id']})
        assert response.status_code == 429, response.text
        assert response.json()['code'] == 'daily_quota_reached'
        assert 0 < int(response.headers['retry-after']) <= 25 * 3600
        assert [job for job in runtime.store.jobs(book['id']) if job['kind'] == 'listen_chapter'] == []
    finally:
        tts_limits.LIMITER.reset()


# Item: cache_hit is transient -------------------------------------------------

def test_worker_cache_hit_is_not_persisted_in_the_job(client, renderer):
    import threading
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
    finally:
        release.set()
    wait_job(client, first['job']['id'])
    two = wait_job(client, second['job']['id'])
    assert two['status'] == 'completed' and len(calls) == 1, 'the second job reused the first take'
    assert 'cache_hit' not in two['audio']
    assert 'cache_hit' not in stored_job(runtime, second['job']['id'])['audio']


# Item: one audio shape, no internal fields -------------------------------------

def test_every_audio_object_is_an_audio_ref_without_internal_fields(client, renderer, monkeypatch):
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    monkeypatch.setattr('bardic.app.shutil.which', lambda name: '/fake/' + name)
    monkeypatch.setattr('bardic.performances.shutil.which', lambda name: '/fake/' + name)
    monkeypatch.setattr('bardic.performances.synthesize', renderer[1])
    book = import_text(client)
    segment = book['segments'][0]
    # Enhanced (Studio) take on the book document.
    render = client.post(f"/api/books/{book['id']}/render", json={'provider': 'gemini', 'segment_id': segment['id']}).json()
    assert wait_job(client, render['id'])['status'] == 'completed'
    document = client.get(f"/api/books/{book['id']}").json()
    [take] = assert_audio_refs(document)
    assert 'resource_usage' not in take and take['created_at'] is None
    # Simple listening: job audio, the cached POST and the takes listing.
    job = wait_job(client, begin(client, book)['job']['id'])
    assert_audio_refs(job['audio'])
    cached = begin(client, book)
    assert cached['cached'] is True
    assert_audio_refs(cached['audio'])
    takes = client.get(f"/api/books/{book['id']}/listen/takes", params={'session_id': cached['session']['id']}).json()
    assert_audio_refs(takes)
    # Performances: simple (the listening objects) and cast.
    for mode in ('simple', 'cast'):
        created = client.post(f"/api/books/{book['id']}/performances", json={
            'mode': mode, 'provider': 'system', 'voice': 'Samantha',
            'chapter_ids': [book['chapters'][0]['id']]})
        assert created.status_code == 200, created.text
        performance = created.json()['performance']
        if created.json()['job']:
            assert wait_job(client, created.json()['job']['id'])['status'] == 'completed'
        ready = client.get(f"/api/books/{book['id']}/performances/{performance['id']}/audio").json()
        assert_audio_refs(ready['audio'], least=len(ready['audio']))
    # Voice previews: the job's audio and the cached POST.
    preview = client.post(f"/api/books/{book['id']}/voice-preview", json={'provider': 'gemini', 'voice': 'Kore',
                                                                            'segment_id': segment['id']}).json()
    assert_audio_refs(wait_job(client, preview['job']['id'])['audio'])
    again = client.post(f"/api/books/{book['id']}/voice-preview", json={'provider': 'gemini', 'voice': 'Kore',
                                                                          'segment_id': segment['id']}).json()
    assert again['cached'] is True
    assert_audio_refs(again['audio'])


# Error codes ---------------------------------------------------------------------

def test_listening_errors_carry_codes_and_neutral_details(client, renderer):
    runtime = client.app.state.runtime
    book = import_text(client)
    base = f"/api/books/{book['id']}"
    segment_id = book['segments'][0]['id']
    missing_key = client.post(base + '/listen', json={'provider': 'gemini', 'segment_id': segment_id})
    assert missing_key.status_code == 400 and missing_key.json()['code'] == 'gemini_key_missing'
    assert 'Settings' not in missing_key.json()['detail']
    unknown = client.post(base + '/listen', json={'provider': 'gemini', 'segment_id': 'missing'})
    assert unknown.status_code == 400 and unknown.json()['code'] == 'unknown_passage'
    model = client.post(base + '/listen', json={'provider': 'gemini', 'model': 'unlisted', 'segment_id': segment_id})
    assert model.status_code == 400 and model.json()['code'] == 'model_unsupported'
    runtime.api_key = 'offline-key'
    busy = runtime.store.create_job(book['id'], 'analyze')
    blocked = client.post(base + '/listen', json={'provider': 'gemini', 'segment_id': segment_id})
    assert blocked.status_code == 409 and blocked.json()['code'] == 'job_active'
    runtime.store.update_job(busy['id'], status='cancelled')
    runtime.stopping.set()
    try:
        stopping = client.post(base + '/listen', json={'provider': 'gemini', 'segment_id': segment_id})
    finally:
        runtime.stopping.clear()
    assert stopping.status_code == 503 and stopping.json()['code'] == 'shutting_down'
    preview = client.post(base + '/voice-preview', json={'provider': 'gemini', 'character_id': 'nobody'})
    assert preview.status_code == 400 and preview.json()['code'] == 'unknown_character'
    chapter = client.post(base + '/listen/chapter/preview', json={'segment_id': 'missing'})
    assert chapter.status_code == 400 and chapter.json()['code'] == 'unknown_passage'
    performance = client.get(f"/api/books/missing-book/performances/pf_missing")
    assert performance.status_code == 404 and performance.json()['code'] == 'book_not_found'
    assert client.post(base + '/archive').status_code == 200
    archived = client.post(base + '/listen', json={'provider': 'gemini', 'segment_id': segment_id})
    assert archived.status_code == 409 and archived.json()['code'] == 'book_archived'


def test_audio_stored_by_earlier_versions_is_re_presented_without_internal_fields():
    """Jobs keep the audio object they were given; one stored before 0.2.0 can be cleaned on output."""
    from bardic.listening import present_audio
    from bardic.voice_previews import present_take as present_preview
    legacy = {'fingerprint': 'f' * 64, 'recipe': 'r' * 64, 'synthesis_key': 'k' * 64, 'available': True,
              'mode': 'simple', 'cache_hit': True, 'source_anchor': {'schema_version': 1}, 'asset_id': 'a' * 64,
              'duration': 1.5, 'provider': 'system', 'model': 'macos-say', 'voice': 'Samantha',
              'created_at': '2026-09-27T00:00:00+00:00', 'session_id': 's' * 64, 'segment_id': 'segment_1',
              'url': '/api/books/book/listen/audio/' + 'a' * 64}
    clip = {**legacy, 'chunk_id': 'c' * 64, 'clip_start': 0.0, 'clip_end': 1.5, 'chunk_duration': 3.0,
            'timing': 'estimated', 'flags': []}
    preview = {**legacy, 'mode': 'preview', 'preview_id': 'p' * 64, 'schema_version': 1}
    for audio in (present_audio('book', legacy), present_audio('book', clip), present_preview('book', preview)):
        assert_audio_refs(audio)
    assert present_audio('book', legacy) == present_audio('book', present_audio('book', legacy)), 'idempotent'


# GET safety -------------------------------------------------------------------------

def test_listening_reads_do_not_run_schema_statements(client, renderer):
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    job = wait_job(client, begin(client, book)['job']['id'])
    session_id = job['session_id']
    statements = []
    connect = runtime.store.connect

    def traced():
        conn = connect()
        conn.set_trace_callback(statements.append)
        return conn
    runtime.store.connect = traced
    try:
        base = f"/api/books/{book['id']}"
        assert client.get(base + '/listen/takes', params={'session_id': session_id}).status_code == 200
        assert client.get(job['audio']['url']).status_code == 200
        assert client.get(base + '/performances').status_code == 200
    finally:
        runtime.store.connect = connect
    ddl = [s for s in statements if s.lstrip().upper().startswith(('CREATE', 'DROP', 'ALTER'))]
    assert ddl == []


def test_narration_and_voice_messages_name_no_ui_location():
    """Error texts, job errors and warnings describe the condition; the UI adds hints keyed on `code`."""
    import io
    import pathlib
    import re
    import tokenize
    root = pathlib.Path(__file__).resolve().parents[1] / 'bardic'
    location = re.compile(r'\bin (Settings|Cast|Voices|Studio)\b|\b(Settings|Analysis|Listen|Cast|Voices) tab\b')
    found = []
    for name in ('analysis.py', 'audio.py', 'breeze.py', 'gemini_voices.py', 'listening.py', 'performances.py',
                 'voice_library.py', 'voice_previews.py', 'voice_routes.py', 'chapter_listening.py', 'take_archive.py',
                 'pipeline/runner.py'):
        source = (root / name).read_text()
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.STRING and not token.string.lstrip('rbfuRBFU').startswith(('"""', "'''")) \
                    and location.search(token.string):
                found.append(f'{name}:{token.start[0]}: {token.string}')
    assert found == []


# Damaged stored data -------------------------------------------------------------

def test_listen_skips_a_damaged_reuse_copy_instead_of_failing_the_request(client, renderer):
    """A damaged asset in the target book is damaged stored data, not a bad request: reuse is skipped."""
    calls, _ = renderer
    runtime = client.app.state.runtime
    runtime.api_key = 'offline-key'
    book = import_text(client)
    first = wait_job(client, begin(client, book)['job']['id'])['audio']
    other = import_text(client, book['segments'][0]['text'])
    damaged = runtime.store.root / 'listen-audio' / other['id'] / f"{first['asset_id']}.wav"
    damaged.parent.mkdir(parents=True, exist_ok=True)
    damaged.write_bytes(b'not the retained take')
    response = client.post(f"/api/books/{other['id']}/listen", json={
        'provider': 'gemini', 'voice': 'Kore', 'model': DEFAULT_TTS_MODEL, 'segment_id': other['segments'][0]['id']})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['cached'] is False and 'job' in body
    finished = wait_job(client, body['job']['id'])
    assert finished['status'] == 'completed' and len(calls) == 2
    assert finished['audio']['asset_id'] != first['asset_id'] and 'reuse' not in finished['audio']
    assert damaged.read_bytes() == b'not the retained take', 'a retained file is never overwritten'


def test_chapter_listening_on_a_passage_with_a_dangling_chapter_is_a_server_defect(client):
    store = client.app.state.runtime.store
    book = store.book(import_text(client)['id'])
    book['segments'][0]['chapter_id'] = 'chapter_missing'
    store.save_book(book)
    body = {'segment_id': book['segments'][0]['id']}
    import conftest  # its contract wrapper rejects every 500; these expect one, so they are sent unchecked
    for path in ('/listen/chapter/preview', '/listen/chapter'):
        response = conftest._send(client, client.build_request('POST', f"/api/books/{book['id']}{path}", json=body))
        assert response.status_code == 500 and response.json()['code'] == 'internal_error', response.text


def test_cancel_skips_a_dangling_child_job_id(client):
    store = client.app.state.runtime.store
    book = import_text(client)
    child = store.create_job(book['id'], 'analyze')
    parent = store.create_job('series:s1', 'series')
    store.update_job(parent['id'], status='running', book_ids=[book['id']],
                     child_job_ids=['0' * 32, child['id']])
    response = client.post(f"/api/jobs/{parent['id']}/cancel")
    assert response.status_code == 200, response.text
    assert response.json()['cancel_requested'] is True
    assert store.job(child['id'])['status'] == 'cancelled'
