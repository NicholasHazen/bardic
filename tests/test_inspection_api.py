"""Inspection and classic-analysis API contract fixes (contract 0.2.0).

Offline: synthetic prose, a fake analysis provider, and tmp_path libraries.
"""
from copy import deepcopy
import io
import json
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from bardic.app import create_app
from bardic.processing import ProcessingStore
from bardic.series import SeriesRepository
from test_progressive import FakeProvider, process, story


SOURCE = 'Chapter One\n\nMara waited by the gate.\n\n“Stay,” Mara said.\n\nChapter Two\n\nElio did not answer.\n'

# Domain tables a GET must leave untouched. The census cache (book_preprocessing) and the
# search index (search_books, passage_search) are disposable derived caches and may change.
DOMAIN_TABLES = ('books', 'jobs', 'takes', 'analysis_checkpoints', 'analysis_units', 'analysis_attempts',
                 'pipeline_events', 'resource_operations', 'artifact_versions', 'artifact_heads',
                 'artifact_dependencies', 'artifact_backfills', 'character_observations', 'character_references')


def offline(monkeypatch):
    for variable in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr('bardic.app.list_system_voices', lambda: [])

    def no_network(*_args, **_kwargs):
        pytest.fail('Inspection must not contact a provider or external service')

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', no_network)


@pytest.fixture
def client(tmp_path, monkeypatch):
    offline(monkeypatch)
    with TestClient(create_app(tmp_path)) as test_client:
        yield test_client


def import_book(client, text=SOURCE, filename='story.txt'):
    response = client.post('/api/books', files={'file': (filename, text.encode(), 'text/plain')})
    assert response.status_code == 200, response.text
    return response.json()


def snapshot(store):
    with store.lock, store.connect() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {table: sorted(map(repr, conn.execute(f'SELECT * FROM {table}').fetchall()))
                for table in DOMAIN_TABLES if table in tables}


def count(store, table, book_id):
    with store.lock, store.connect() as conn:
        return conn.execute(f'SELECT COUNT(*) FROM {table} WHERE book_id=?', (book_id,)).fetchone()[0]


# ------------------------------------------------------------ 1. cancelled stage


def interrupted_scan(store, monkeypatch, job_status):
    """A real classic run stopped by cancellation: the checkpoint records `interrupted`."""
    book = story()
    store.save_book(book)
    job = store.create_job(book['id'], 'analyze')
    provider = FakeProvider(monkeypatch)

    def stop(stage, result, prompt):
        raise InterruptedError('stopped by the test')
    provider.transform = stop
    with pytest.raises(InterruptedError):
        process(book, store, cancelled=lambda: True, run_id=job['id'])
    store.update_job(job['id'], status=job_status)
    assert store.analysis_status(book['id'])['status'] == 'interrupted'
    return book


def test_pipeline_stage_reports_a_cancelled_run_as_cancelled(client, monkeypatch):
    store = client.app.state.runtime.store
    book = interrupted_scan(store, monkeypatch, 'cancelled')
    stages = {s['id']: s for s in client.get(f"/api/books/{book['id']}/pipeline").json()['stages']}
    assert stages['discovery']['status'] == 'cancelled'


def test_pipeline_stage_reports_a_restart_as_interrupted(client, monkeypatch):
    store = client.app.state.runtime.store
    book = interrupted_scan(store, monkeypatch, 'interrupted')
    stages = {s['id']: s for s in client.get(f"/api/books/{book['id']}/pipeline").json()['stages']}
    assert stages['discovery']['status'] == 'interrupted'


def test_checkpoint_without_a_run_id_stays_interrupted(client):
    book = import_book(client)
    store = client.app.state.runtime.store
    job = store.create_job(book['id'], 'analyze')
    store.update_job(job['id'], status='cancelled')
    store.save_analysis_checkpoint(book['id'], 'older-checkpoint', {
        'status': 'interrupted', 'stage': 'discovery', 'provider': 'openai', 'model': 'test-model',
        'working_book': deepcopy(store.book(book['id'])), 'units': {}, 'chapters': []})
    stages = {s['id']: s for s in client.get(f"/api/books/{book['id']}/pipeline").json()['stages']}
    assert stages['discovery']['status'] == 'interrupted'


# ----------------------------------------------------------- 2. dangling edges


def test_story_map_has_no_edge_or_scene_character_for_a_speaker_missing_from_the_cast(client):
    imported = import_book(client)
    store = client.app.state.runtime.store
    book = store.book(imported['id'])
    book['characters'].append({'id': 'mara', 'name': 'Mara', 'aliases': [], 'description': '', 'direction': '',
                               'evidence': [], 'voice': 'Kore', 'system_voice': ''})
    dialogue = next(s for s in book['segments'] if s['kind'] == 'dialogue')
    chapter_id = dialogue['chapter_id']
    book['scenes'] = [{'id': 'scene-one', 'chapter_id': chapter_id, 'title': 'At the gate'}]
    for segment in book['segments']:
        if segment['chapter_id'] == chapter_id:
            segment['scene_id'] = 'scene-one'
    dialogue.update(speaker_id='a-removed-character', confidence=.9)
    narration = next(s for s in book['segments'] if s['chapter_id'] == chapter_id and s['kind'] != 'dialogue')
    narration.update(kind='dialogue', speaker_id='mara', confidence=1.0)
    store.save_book(book)

    graph = client.get(f"/api/books/{book['id']}/story-map").json()
    nodes = {node['id'] for node in graph['nodes']}
    assert all(edge['from'] in nodes and edge['to'] in nodes for edge in graph['edges'])
    speakers = [edge for edge in graph['edges'] if edge['type'] == 'attributed_speaker']
    assert [edge['to'] for edge in speakers] == [f"{book['id']}:character:mara"]
    scene = next(s for c in graph['chapters'] for s in c['scenes'] if s['id'] == 'scene-one')
    assert scene['character_ids'] == ['mara']
    assert store.book(book['id'])['segments'] == book['segments']  # The stale attribution is kept in the book.


# ------------------------------------------------------- 3. attempt allowlist


def test_analysis_export_attempts_use_the_inspector_allowlist(client):
    book = import_book(client)
    store = client.app.state.runtime.store
    processing = ProcessingStore(store)
    processing.save_attempt({
        'id': 'attempt-one', 'book_id': book['id'], 'run_id': 'finished-run', 'stage': 'discovery',
        'unit_key': 'unit', 'chapter_id': book['chapters'][0]['id'], 'provider': 'openai', 'model': 'test-model',
        'status': 'received', 'http_status': 200, 'input_tokens': 10, 'output_tokens': 5,
        'cached_input_tokens': None, 'cache_write_input_tokens': None, 'reserved_input_tokens': 100,
        'reserved_output_tokens': 20, 'charged_estimate_usd': .001, 'cost_basis': 'usage_estimate_with_guard_uplift',
        'price_as_of': '2026-09-27', 'price_source': 'https://example.invalid/prices', 'elapsed_seconds': 1.5,
        'created_at': '2026-09-27T12:00:00+00:00', 'completed_at': '2026-09-27T12:00:02+00:00',
        'process_id': 'a-server-process', 'input_rate': 1.0, 'output_rate': 2.0,
        'api_key': 'must-not-leak', 'private_response': 'not-public'})
    processing.save_attempt({'id': 'attempt-two', 'book_id': book['id'], 'run_id': 'gone-run', 'status': 'reserved'})
    processing.event(book['id'], 'finished-run', 'discovery', 'unit', 'accepted', attempt_id='attempt-one')

    inspector = client.get(f"/api/books/{book['id']}/pipeline").json()['attempts']
    response = client.get(f"/api/books/{book['id']}/analysis-export")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        exported = json.loads(archive.read('analysis-attempts.json'))
    assert exported == inspector
    first = exported[0]
    assert first['validation_state'] == 'accepted' and first['cost_basis'] == 'usage_estimate_with_guard_uplift'
    assert not {'process_id', 'input_rate', 'output_rate', 'api_key', 'private_response'} & set(first)
    assert exported[1]['status'] == 'interrupted_unknown'
    assert b'must-not-leak' not in response.content and b'a-server-process' not in response.content


# ------------------------------------------------------------- 4. clamped paging


@pytest.mark.parametrize('params,limit,offset', [
    ({'limit': 0}, 1, 0), ({'limit': 201}, 200, 0), ({'offset': -1}, 30, 0), ({'limit': -5, 'offset': -9}, 1, 0),
])
def test_out_of_range_artifact_paging_is_clamped(client, params, limit, offset):
    book = import_book(client)
    response = client.get(f"/api/books/{book['id']}/artifacts", params=params)
    assert response.status_code == 200, response.text
    page = response.json()
    assert (page['limit'], page['offset']) == (limit, offset)
    assert len(page['items']) == min(limit, page['total'])


# ------------------------------------------------------------------ 5. alias


def test_search_returns_items_without_the_results_alias(client):
    book = import_book(client)
    body = client.get(f"/api/books/{book['id']}/search", params={'q': 'Mara'}).json()
    assert body['items'] and 'results' not in body


# ------------------------------------------------------------- 6. GET safety


def legacy_library(client, monkeypatch):
    """A book with pre-upgrade discovery (only in its checkpoint), series context and an uncached census."""
    store = client.app.state.runtime.store
    book = story()
    store.save_book(book)
    FakeProvider(monkeypatch)
    scanned = process(book, store)
    earlier = import_book(client, 'Mara used a low voice.', 'earlier.txt')
    early = store.book(earlier['id'])
    early['characters'].append({'id': 'early-mara', 'name': 'Mara', 'aliases': [], 'description': '', 'direction': ''})
    store.save_book(early)
    series = SeriesRepository(store)
    saga = series.create_series('Saga')
    series.set_membership(early['id'], saga['id'], 1)
    series.set_membership(book['id'], saga['id'], 2)
    mara = next(c for c in scanned['characters'] if c['name'] == 'Mara')
    linked = series.create_character(saga['id'], 'Mara')
    series.link_character(early['id'], 'early-mara', linked['id'])
    series.link_character(book['id'], mara['id'], linked['id'])
    chapter = early['chapters'][0]
    store.save_analysis_checkpoint(early['id'], 'earlier', {'references': [{
        'id': 'observation', 'character_id': 'early-mara', 'chapter_id': chapter['id'], 'start': 0,
        'end': len(chapter['text']), 'quote': chapter['text'], 'kind': 'profile_evidence',
        'profile_description': 'Low voice.', 'provider': 'openai', 'model': 'older-model'}]})
    # Pre-upgrade: validated discovery lived only in the checkpoint, not in the unit cache.
    with store.lock, store.connect() as conn:
        conn.execute('DELETE FROM analysis_units WHERE book_id=?', (book['id'],))
    # A cast change makes the cached census stale, so a view must compute it again.
    current = store.book(book['id'])
    current['characters'].append({'id': 'guest', 'name': 'Guest', 'aliases': [], 'description': '', 'direction': ''})
    store.save_book(current)
    return current


def test_inspection_gets_create_no_domain_records(client, monkeypatch):
    book = legacy_library(client, monkeypatch)
    store = client.app.state.runtime.store
    artifact_id = client.get(f"/api/books/{book['id']}/artifacts", params={'limit': 1}).json()['items'][0]['id']
    before = snapshot(store)
    base = f"/api/books/{book['id']}"
    coverage = client.get(base + '/preprocessing')
    assert coverage.status_code == 200, coverage.text
    for route, params in (('/analysis', {}), ('/pipeline', {}), ('/resources', {}), ('/artifacts', {}),
                          ('/artifacts/' + artifact_id, {}), ('/story-map', {}), ('/analysis-export', {}),
                          ('/search', {'q': 'Mara'}), ('/search', {'q': 'Mara', 'scope': 'earlier'})):
        response = client.get(base + route, params=params)
        assert response.status_code == 200, (route, response.text)
    assert snapshot(store) == before
    # Checkpoint-only discovery still counts, exactly as after the plan preview imports it.
    semantic = coverage.json()['semantic_chapter_ids']
    assert semantic
    plan = client.post(base + '/analysis-plan', json={'provider': 'openai', 'phase': 'profiles'})
    assert plan.status_code == 200, plan.text
    assert plan.json()['coverage']['semantic_chapter_ids'] == semantic
    assert count(store, 'analysis_units', book['id']) > 0  # The plan POST may import and retain.
    assert snapshot(store)['artifact_versions'] != before['artifact_versions']


def test_legacy_data_is_retained_at_startup_once_not_by_gets(tmp_path, monkeypatch):
    offline(monkeypatch)
    with TestClient(create_app(tmp_path)) as client:
        book = import_book(client)
        store = client.app.state.runtime.store
        ProcessingStore(store)
        legacy = {'stage': 'discovery', 'chapter_id': book['chapters'][0]['id'], 'start': 0, 'end': 5,
                  'provider': 'openai', 'model': 'older-model', 'unit_key': 'legacy-unit', 'result': {'characters': []}}
        with store.lock, store.connect() as conn:
            conn.execute('INSERT INTO analysis_units VALUES (?,?,?,?,?)',
                         (book['id'], 'legacy-unit', 'discovery', 'old-source', json.dumps(legacy)))
        before = count(store, 'artifact_versions', book['id'])
        for route in ('artifacts', 'pipeline', 'story-map', 'analysis-export'):
            assert client.get(f"/api/books/{book['id']}/{route}").status_code == 200
        assert count(store, 'artifact_versions', book['id']) == before
    with TestClient(create_app(tmp_path)) as client:
        page = client.get(f"/api/books/{book['id']}/artifacts", params={'kind': 'analysis_output'}).json()
        assert [(item['logical_key'], item['legacy_provenance']) for item in page['items']] == [('legacy-unit', True)]
        after = count(client.app.state.runtime.store, 'artifact_versions', book['id'])
    with TestClient(create_app(tmp_path)) as client:
        store = client.app.state.runtime.store
        assert count(store, 'artifact_versions', book['id']) == after
        with store.lock, store.connect() as conn:
            assert conn.execute('SELECT version FROM artifact_backfills WHERE book_id=?', (book['id'],)).fetchone() == (1,)


# -------------------------------------------------------- 7. internal fields


def test_internal_fields_are_not_presented(client, monkeypatch):
    store = client.app.state.runtime.store
    book = story()
    store.save_book(book)
    FakeProvider(monkeypatch)
    process(book, store, run_id='offline-run')
    status = client.get(f"/api/books/{book['id']}/analysis").json()
    assert status['status'] == 'completed' and 'fingerprint' not in status
    local = client.get(f"/api/books/{book['id']}/preprocessing").json()['local']
    assert not {'fingerprint', 'source_hash'} & set(local)
    plan = client.post(f"/api/books/{book['id']}/analysis-plan", json={'provider': 'openai'}).json()
    assert not {'fingerprint', 'source_hash'} & set(plan['coverage']['local'])
    operations = client.get(f"/api/books/{book['id']}/resources").json()['operations']
    assert operations and not any('process_id' in row for row in operations)
    # Storage keeps the bookkeeping it needs.
    assert store.analysis_status(book['id'])['fingerprint']


# ------------------------------------------------------------ 8. error codes


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()['code'] == code, response.text
    assert 'Settings' not in response.json()['detail']


def test_classic_analysis_errors_have_codes(client):
    book = import_book(client)
    store = client.app.state.runtime.store
    runtime = client.app.state.runtime
    url = f"/api/books/{book['id']}/analyze"
    error(client.post(url, json={'provider': 'local', 'chapter_id': 'no-such-chapter'}), 400, 'unknown_chapter')
    error(client.post(url, json={'provider': 'mystery'}), 400, 'unknown_provider')
    error(client.post(url, json={'provider': 'gemini'}), 400, 'gemini_key_missing')
    error(client.post(url, json={'provider': 'openai'}), 400, 'api_key_missing')
    error(client.post(url, json={'provider': 'anthropic'}), 400, 'api_key_missing')
    error(client.post('/api/books/missing/analyze', json={'provider': 'local'}), 404, 'book_not_found')
    runtime.stopping.set()
    try:
        error(client.post(url, json={'provider': 'local'}), 503, 'shutting_down')
    finally:
        runtime.stopping.clear()
    series = store.create_job('a-series', 'series')
    store.update_job(series['id'], book_ids=[book['id']])
    error(client.post(url, json={'provider': 'local'}), 409, 'series_run_active')
    store.update_job(series['id'], status='cancelled')
    busy = store.create_job(book['id'], 'analyze')
    error(client.post(url, json={'provider': 'local'}), 409, 'job_active')
    store.update_job(busy['id'], status='cancelled')
    assert client.post(f"/api/books/{book['id']}/archive").status_code == 200
    error(client.post(url, json={'provider': 'local'}), 409, 'book_archived')
    assert [job['id'] for job in store.jobs(book['id'])] == [busy['id']]  # No refused request queued a job.

    plan = f"/api/books/{book['id']}/analysis-plan"
    error(client.post(plan, json={'provider': 'local', 'chapter_id': 'no-such-chapter'}), 400, 'unknown_chapter')
    error(client.post(plan, json={'provider': 'mystery'}), 400, 'unknown_provider')
    error(client.post('/api/books/missing/analysis-plan', json={}), 404, 'book_not_found')


def test_inspection_errors_have_codes(client):
    book = import_book(client)
    base = f"/api/books/{book['id']}"
    error(client.get(base + '/search', params={'q': '   '}), 400, 'search_query_invalid')
    error(client.get(base + '/search', params={'q': 'x' * 301}), 400, 'search_query_invalid')
    error(client.get(base + '/search', params={'q': 'Mara', 'scope': 'everything'}), 400, 'search_scope_invalid')
    error(client.get(base + '/artifacts/artifact_missing'), 404, 'artifact_not_found')
    for route in ('analysis', 'preprocessing', 'pipeline', 'resources', 'artifacts', 'artifacts/x', 'story-map',
                  'analysis-export'):
        error(client.get(f'/api/books/missing/{route}'), 404, 'book_not_found')
    error(client.get('/api/books/missing/search', params={'q': 'Mara'}), 404, 'book_not_found')


def test_a_book_whose_legacy_retention_fails_does_not_stop_startup_and_is_retried(tmp_path, monkeypatch):
    offline(monkeypatch)
    with TestClient(create_app(tmp_path)) as client:
        book = import_book(client)
    from bardic.artifacts import ArtifactRepository

    def broken(self, book_id):
        raise ValueError('unreadable legacy data')
    with monkeypatch.context() as patch:
        patch.setattr(ArtifactRepository, 'backfill', broken)
        with TestClient(create_app(tmp_path)) as client:
            assert client.get(f"/api/books/{book['id']}/artifacts").status_code == 200
            store = client.app.state.runtime.store
            with store.lock, store.connect() as conn:
                assert conn.execute('SELECT 1 FROM artifact_backfills WHERE book_id=?', (book['id'],)).fetchone() is None
    with TestClient(create_app(tmp_path)) as client:
        store = client.app.state.runtime.store
        with store.lock, store.connect() as conn:
            assert conn.execute('SELECT 1 FROM artifact_backfills WHERE book_id=?', (book['id'],)).fetchone() == (1,)
