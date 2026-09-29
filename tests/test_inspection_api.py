"""Inspection API contract fixes (contract 0.2.0), on the step pipeline of contract 0.3.0.

Offline: synthetic prose, a fake analysis provider, and tmp_path libraries.

Dropped with the Classic engine (contract 0.3.0): the three tests that a cancelled Classic run shows
`cancelled` (not `interrupted`) on its inspector stage. The inspector's stage cards now come from the
step pipeline and never read a Classic checkpoint. The Classic `/analysis`, `/preprocessing`,
`/analysis-plan` and `/analyze` routes are gone, so their error-code checks became the pipeline run's.
"""
import io
import json
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from bardic.app import create_app
from bardic.importer import parse_book
from bardic.processing import ProcessingStore
from bardic.series import SeriesRepository
from bardic.store import Store
from classic_fixtures import add_classic_tables, classic_references
from test_analysis_pipeline import MODEL, STORY, FakeProvider, run


SOURCE = 'Chapter One\n\nMara waited by the gate.\n\n“Stay,” Mara said.\n\nChapter Two\n\nElio did not answer.\n'

# Domain tables a GET must leave untouched. The census cache (book_preprocessing) and the
# search index (search_books, passage_search) are disposable derived caches and may change.
DOMAIN_TABLES = ('books', 'jobs', 'takes', 'analysis_attempts', 'schema_migrations',
                 'pipeline_events', 'resource_operations', 'artifact_versions', 'artifact_heads',
                 'artifact_dependencies', 'artifact_backfills', 'character_observations', 'character_references',
                 'pipeline_runs', 'pipeline_step_runs', 'pipeline_decisions', 'pipeline_state', 'pipeline_units')


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
    assert not {'process_id', 'api_key', 'private_response'} & set(first)
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


def analyzed_library(client, monkeypatch):
    """A book with accepted pipeline results, a linked earlier volume with Classic-era evidence, and outside changes
    written straight to the store, so a sync would record versions and rebuild references, and the census is stale."""
    runtime = client.app.state.runtime
    store = runtime.store
    client.provider = FakeProvider()
    monkeypatch.setattr('bardic.analysis._openai_request', client.provider)
    runtime.api_keys['openai'] = 'test-openai-secret'
    client.post('/api/settings', json={'analysis_provider': 'openai', 'analysis_models_by_provider': {'openai': MODEL},
                                       'preprocess_models_by_provider': {'openai': MODEL}})
    book = import_book(client, STORY, 'story.txt')
    job, _ = run(client, book['id'], ['discovery', 'profiles', 'directing'])
    assert job['status'] == 'completed', job
    earlier = import_book(client, 'Mara used a low voice.', 'earlier.txt')
    early = store.book(earlier['id'])
    early['characters'].append({'id': 'early-mara', 'name': 'Mara', 'aliases': [], 'description': '', 'direction': ''})
    store.save_book(early)
    series = SeriesRepository(store)
    saga = series.create_series('Saga')
    series.set_membership(early['id'], saga['id'], 1)
    series.set_membership(book['id'], saga['id'], 2)
    current = store.book(book['id'])
    mara = next(c for c in current['characters'] if c['name'] == 'Mara')
    linked = series.create_character(saga['id'], 'Mara')
    series.link_character(early['id'], 'early-mara', linked['id'])
    series.link_character(book['id'], mara['id'], linked['id'])
    chapter = early['chapters'][0]
    # A reference the removed Classic engine wrote, as the Classic data drop keeps it.
    classic_references(store, early['id'], [{
        'id': 'observation', 'character_id': 'early-mara', 'chapter_id': chapter['id'], 'start': 0,
        'end': len(chapter['text']), 'quote': chapter['text'], 'kind': 'profile_evidence',
        'profile_description': 'Low voice.', 'provider': 'openai', 'model': 'older-model'}])
    # Outside changes: a speaker the accepted directing version does not explain, and a new cast member
    # (which also makes the cached census stale).
    current = store.book(book['id'])
    elio = next(c['id'] for c in current['characters'] if c['name'] == 'Elio')
    line = next(s for s in current['segments'] if s['kind'] == 'dialogue' and s['speaker_id'] == mara['id'])
    line['speaker_id'] = elio
    current['characters'].append({'id': 'guest', 'name': 'Guest', 'aliases': [], 'description': '', 'direction': ''})
    store.save_book(current)
    return store.book(book['id']), mara['id']


def test_inspection_gets_create_no_domain_records(client, monkeypatch):
    book, character_id = analyzed_library(client, monkeypatch)
    store = client.app.state.runtime.store
    artifact_id = client.get(f"/api/books/{book['id']}/artifacts", params={'limit': 1}).json()['items'][0]['id']
    base = f"/api/books/{book['id']}"
    # The overview reports the outside changes it would record ...
    overview = client.get(base + '/analysis-pipeline').json()
    directing = next(s for s in overview['steps'] if s['id'] == 'directing')
    assert directing['accepted_origins'].get('external', 0) >= 1
    before = snapshot(store)
    for route, params in (('/pipeline', {}), ('/resources', {}), ('/artifacts', {}), ('/artifacts/' + artifact_id, {}),
                          ('/story-map', {}), ('/analysis-export', {}), ('/search', {'q': 'Mara'}),
                          ('/search', {'q': 'Mara', 'scope': 'earlier'}), ('/analysis-pipeline', {}),
                          (f'/characters/{character_id}/references', {}), ('/series/context', {}),
                          ('/series/suggestions', {})):
        response = client.get(base + route, params=params)
        assert response.status_code == 200, (route, response.text)
    # ... but no GET records it (only the disposable census cache and search index may change).
    assert snapshot(store) == before
    assert client.get(base + '/series/context').json()['characters']
    # The next pipeline POST records what the GETs only reported.
    plan = client.post(base + '/analysis-pipeline/plan', json={'steps': ['census']})
    assert plan.status_code == 200, plan.text
    after = snapshot(store)
    assert after['pipeline_step_runs'] != before['pipeline_step_runs']
    assert after['artifact_versions'] != before['artifact_versions']


def test_legacy_data_is_retained_at_startup_once_not_by_gets(tmp_path, monkeypatch):
    offline(monkeypatch)
    # A library written by a version before the Classic data drop: its unit cache still has a row.
    store = Store(tmp_path)
    book = parse_book('story.txt', SOURCE.encode())
    store.save_book(book)
    legacy = {'stage': 'discovery', 'chapter_id': book['chapters'][0]['id'], 'start': 0, 'end': 5,
              'provider': 'openai', 'model': 'older-model', 'unit_key': 'legacy-unit', 'result': {'characters': []}}
    with store.lock, store.connect() as conn:
        add_classic_tables(conn, units=[(book['id'], 'legacy-unit', 'discovery', 'old-source', legacy)])

    def tables(store):
        with store.lock, store.connect() as conn:
            return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    with TestClient(create_app(tmp_path)) as client:
        store = client.app.state.runtime.store
        # Startup retained the unit, then dropped the table.
        page = client.get(f"/api/books/{book['id']}/artifacts", params={'kind': 'analysis_output'}).json()
        assert [(item['logical_key'], item['legacy_provenance']) for item in page['items']] == [('legacy-unit', True)]
        assert not {'analysis_units', 'analysis_checkpoints'} & tables(store)
        before = snapshot(store)
        for route in ('artifacts', 'pipeline', 'story-map', 'analysis-export'):
            assert client.get(f"/api/books/{book['id']}/{route}").status_code == 200
        assert snapshot(store) == before
        after = count(store, 'artifact_versions', book['id'])
    with TestClient(create_app(tmp_path)) as client:
        store = client.app.state.runtime.store
        assert count(store, 'artifact_versions', book['id']) == after
        assert not {'analysis_units', 'analysis_checkpoints'} & tables(store)
        with store.lock, store.connect() as conn:
            assert conn.execute('SELECT version FROM artifact_backfills WHERE book_id=?', (book['id'],)).fetchone() == (1,)
            assert conn.execute("SELECT status FROM schema_migrations WHERE id='classic_removal_v1'").fetchone() == ('completed',)


# -------------------------------------------------------- 7. internal fields


def test_internal_fields_are_not_presented(client):
    book = import_book(client)
    # The local census step measures its work in the resource ledger; no key is needed.
    job, _ = run(client, book['id'], ['census'])
    assert job['status'] == 'completed', job
    operations = client.get(f"/api/books/{book['id']}/resources").json()['operations']
    assert operations and not any('process_id' in row for row in operations)
    # Storage keeps the bookkeeping it needs.
    with client.app.state.runtime.store.connect() as conn:
        stored = [json.loads(row[0]) for row in conn.execute('SELECT body FROM resource_operations WHERE book_id=?',
                                                             (book['id'],))]
    assert any('process_id' in row for row in stored)


# ------------------------------------------------------------ 8. error codes


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()['code'] == code, response.text
    assert 'Settings' not in response.json()['detail']


def test_pipeline_run_errors_have_codes(client):
    book = import_book(client)
    store = client.app.state.runtime.store
    runtime = client.app.state.runtime
    url = f"/api/books/{book['id']}/analysis-pipeline/runs"
    census = {'steps': ['census'], 'limits': {'max_requests': 5}}
    discovery = {'steps': ['discovery'], 'configs': {'discovery': {'provider': 'openai', 'model': MODEL}},
                 'limits': {'max_requests': 5}}
    error(client.post(url, json={**census, 'chapter_ids': ['no-such-chapter']}), 400, 'unknown_chapter')
    error(client.post(url, json=discovery), 400, 'api_key_missing')
    error(client.post('/api/books/missing/analysis-pipeline/runs', json=census), 404, 'book_not_found')
    runtime.stopping.set()
    try:
        error(client.post(url, json=census), 503, 'shutting_down')
    finally:
        runtime.stopping.clear()
    series = store.create_job('a-series', 'series')
    store.update_job(series['id'], book_ids=[book['id']])
    error(client.post(url, json=census), 409, 'series_run_active')
    store.update_job(series['id'], status='cancelled')
    busy = store.create_job(book['id'], 'render')
    error(client.post(url, json=census), 409, 'job_active')
    store.update_job(busy['id'], status='cancelled')
    assert client.post(f"/api/books/{book['id']}/archive").status_code == 200
    error(client.post(url, json=census), 409, 'book_archived')
    assert [job['id'] for job in store.jobs(book['id'])] == [busy['id']]  # No refused request queued a job.


def test_inspection_errors_have_codes(client):
    book = import_book(client)
    base = f"/api/books/{book['id']}"
    error(client.get(base + '/search', params={'q': '   '}), 400, 'search_query_invalid')
    error(client.get(base + '/search', params={'q': 'x' * 301}), 400, 'search_query_invalid')
    error(client.get(base + '/search', params={'q': 'Mara', 'scope': 'everything'}), 400, 'search_scope_invalid')
    error(client.get(base + '/artifacts/artifact_missing'), 404, 'artifact_not_found')
    for route in ('pipeline', 'resources', 'artifacts', 'artifacts/x', 'story-map', 'analysis-export'):
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
