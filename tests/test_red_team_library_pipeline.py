"""Red-team follow-ups for library, books, series, pipeline and inspection (contract 0.2.0).

Offline: synthetic prose, tmp_path libraries, fake narration and a fake analysis provider.
Each test pins one behavior the red-team review found wrong.
"""
import json
from pathlib import Path
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from bardic.apispec.series import OPS as SERIES_OPS
from bardic.app import create_app, manual_fields
from bardic.pipeline.evidence import reviewed_speaker
from bardic.pipeline.repository import PipelineRepository
from bardic.pipeline_view import ATTEMPT_FIELDS
from test_analysis_pipeline import MODEL, STORY, FakeProvider, import_book, run, wait_job
import conftest
from test_app import fake_audio, import_text


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'BREEZE_TTS_URL', 'BREEZE_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr('bardic.app.list_system_voices', lambda: [])
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request',
                        lambda *_args, **_kwargs: pytest.fail('These tests must not make network requests'))
    with TestClient(create_app(tmp_path)) as test_client:
        test_client.provider = FakeProvider()
        monkeypatch.setattr('bardic.analysis._openai_request', test_client.provider)
        test_client.post('/api/settings', json={'api_keys': {'openai': 'test-openai-secret'}, 'analysis_provider': 'openai',
                                                'analysis_models_by_provider': {'openai': MODEL},
                                                'preprocess_models_by_provider': {'openai': MODEL}})
        yield test_client


ROUTE_GONE = 405  # An unknown POST path reaches the static-file mount, which allows only GET.


def store_of(client):
    return client.app.state.runtime.store


def count(client, table, book_id):
    with sqlite3.connect(store_of(client).db) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            return 0
        return conn.execute(f'SELECT count(*) FROM {table} WHERE book_id=?', (book_id,)).fetchone()[0]


def series_with(client, *books):
    series = client.post('/api/series', json={'name': 'The Harbor Lamps'}).json()
    for position, book in enumerate(books, 1):
        response = client.put(f"/api/books/{book['id']}/series", json={'series_id': series['id'], 'position': float(position)})
        assert response.status_code == 200, response.text
    return series


def versions(client, book_id, step_id):
    return client.get(f'/api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions').json()['items']


# 1. The library snapshot counts playable takes like the book list -----------------------------

def test_library_snapshot_audio_count_matches_the_book_list(client, monkeypatch):
    fake_audio(monkeypatch)
    client.post('/api/settings', json={'api_keys': {'gemini': 'test-only'}})
    book = import_text(client, 'Chapter One\n\nThe lamps were lit.\n\n“Come in,” Mara said.\n')
    url = f"/api/books/{book['id']}"
    assert wait_job(client, client.post(f'{url}/render', json={'provider': 'gemini'}).json()['id'])['status'] == 'completed'
    store_of(client).save_take(book['id'], book['segments'][0]['id'],
                               {'fingerprint': 'stale-take', 'duration': 1, 'provider': 'gemini', 'model': 'x'})
    playable = sum(bool(s['audio']) for s in client.get(url).json()['segments'])
    assert playable == len(book['segments']) - 1
    listed = next(b for b in client.get('/api/books').json() if b['id'] == book['id'])
    snapshot = next(b for b in client.get('/api/library').json()['books'] if b['id'] == book['id'])
    assert listed['audio_count'] == snapshot['audio_count'] == playable


# 2. A series plan's writes are the ones its description names -----------------------------------

def test_series_plan_records_what_its_description_names_but_creates_no_jobs(client):
    book = import_text(client)
    series = series_with(client, book)
    before = count(client, 'artifact_versions', book['id'])
    response = client.post(f"/api/series/{series['id']}/plan", json={'steps': ['discovery']})
    assert response.status_code == 200, response.text
    # Each book's sync records its outside changes (here the first `baseline` versions) as artifacts.
    assert count(client, 'artifact_versions', book['id']) > before
    assert [v['origin'] for v in versions(client, book['id'], 'directing')] == ['baseline']
    assert client.get('/api/jobs').json() == []
    description = next(o for o in SERIES_OPS if o.id == 'planSeriesProcessing').description
    assert 'creates no jobs or records' not in description
    assert 'census' in description and 'records outside changes' in description


# 3. PATCH of a pronunciation is partial -----------------------------------------------------------

def test_pronunciation_patch_changes_only_the_fields_sent(client):
    book = import_text(client, 'Chapter One\n\nSiobhan lit the lamp.\n')
    url = f"/api/books/{book['id']}/pronunciations"
    entry = client.post(url, json={'term': 'Siobhan', 'respelling': 'shih-VAWN'}).json()['pronunciations'][0]
    response = client.patch(f"{url}/{entry['id']}", json={'note': 'Irish name'})
    assert response.status_code == 200, response.text
    saved = response.json()['pronunciations'][0]
    assert (saved['term'], saved['respelling'], saved['note']) == ('Siobhan', 'shih-VAWN', 'Irish name')
    revision = response.json()['book']['revision']
    # Sending a field unchanged is a no-op, and a term cannot be cleared.
    same = client.patch(f"{url}/{entry['id']}", json={'respelling': 'shih-VAWN'})
    assert same.status_code == 200 and same.json()['book']['revision'] == revision
    cleared = client.patch(f"{url}/{entry['id']}", json={'term': None})
    assert cleared.status_code == 400 and cleared.json()['code'] == 'pronunciation_invalid'


# 4. Paging offsets beyond SQLite's integer range are clamped ------------------------------------

def test_huge_paging_offsets_are_clamped_not_server_errors(client):
    book = import_book(client)
    huge = 9223372036854775808
    for path in (f"/api/books/{book['id']}/artifacts?offset={huge}",
                 f"/api/books/{book['id']}/resources?offset={huge}",
                 f"/api/books/{book['id']}/analysis-pipeline/steps/directing/versions/accepted?offset={huge}"):
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)
        assert response.json()['offset'] == 2 ** 53 - 1


# 5. Unknown steps inside `gates` and `configs` are refused --------------------------------------

def test_unknown_steps_in_gates_or_configs_are_400_and_queue_nothing(client):
    book = import_book(client)
    base = f"/api/books/{book['id']}/analysis-pipeline"
    gates = client.post(f'{base}/runs', json={'steps': ['census'], 'gates': {'no_such_step': 'auto'},
                                              'limits': {'max_requests': 5}})
    assert gates.status_code == 400 and gates.json()['code'] == 'unknown_step'
    configs = {'no_such_step': {'provider': 'local'}}
    for path, body in ((f'{base}/runs', {'steps': ['census'], 'configs': configs, 'limits': {'max_requests': 5}}),
                       (f'{base}/plan', {'steps': ['census'], 'configs': configs})):
        response = client.post(path, json=body)
        assert response.status_code == 400 and response.json()['code'] == 'unknown_step', response.text
    assert client.get('/api/jobs').json() == []
    # A known step that is not requested is still ignored, as documented.
    ignored = client.post(f'{base}/plan', json={'steps': ['census'], 'configs': {'structure': {'provider': 'local'}}})
    assert ignored.status_code == 200, ignored.text


# 6. Classic previews are gone -------------------------------------------------------------------
# Main (0.2.0) made the Classic preview refuse archived books. The Classic routes were removed in
# contract 0.3.0, so they are not served at all; nothing is recorded for any book. The book
# pipeline's plan still accepts a removed book, as its description says.

def test_removed_classic_routes_are_not_served_and_record_nothing(client):
    book = import_text(client)
    assert client.post(f"/api/books/{book['id']}/archive").status_code == 200
    before = count(client, 'artifact_versions', book['id'])
    for route in ('analysis-plan', 'analyze'):
        # Sent without the contract check (conftest), which refuses any path the contract does not describe.
        request = client.build_request('POST', f"/api/books/{book['id']}/{route}", json={'provider': 'local'})
        response = conftest._send(client, request)
        assert response.status_code == ROUTE_GONE, (route, response.status_code, response.text)
        assert response.json()['code'] == 'route_not_found'
    assert count(client, 'artifact_versions', book['id']) == before
    assert client.get('/api/jobs').json() == []


# 7. One name per concept --------------------------------------------------------------------------

def test_pipeline_runs_use_scheduling_and_old_runs_are_read_with_it(client):
    book = import_book(client)
    base = f"/api/books/{book['id']}/analysis-pipeline"
    old = client.post(f'{base}/runs', json={'steps': ['census'], 'mode': 'serial', 'limits': {'max_requests': 5}})
    assert old.status_code == 422
    response = client.post(f'{base}/runs', json={'steps': ['census'], 'scheduling': 'parallel', 'limits': {'max_requests': 5}})
    assert response.status_code == 200, response.text
    started = response.json()
    assert started['run']['scheduling'] == 'parallel' == started['job']['scheduling'] and 'mode' not in started['run']
    wait_job(client, started['job']['id'])
    # A run stored before the rename kept `mode`; it is presented as `scheduling`.
    with sqlite3.connect(store_of(client).db) as conn:
        body = json.loads(conn.execute('SELECT body FROM pipeline_runs WHERE id=?', (started['run']['id'],)).fetchone()[0])
        body['mode'] = body.pop('scheduling')
        conn.execute('UPDATE pipeline_runs SET body=? WHERE id=?', (json.dumps(body), started['run']['id']))
    recent = client.get(base).json()['recent_runs'][0]
    assert recent['scheduling'] == 'parallel' and 'mode' not in recent


def test_series_book_plans_have_no_limits_and_character_edits_have_no_voice_aliases(client):
    book = import_text(client)
    series = series_with(client, book)
    plan = client.post(f"/api/series/{series['id']}/plan", json={'steps': ['discovery']}).json()
    assert 'limits' not in plan['books'][0]['plan']
    schemas = json.loads((Path(__file__).parents[1] / 'contract' / 'openapi.json').read_text())['components']['schemas']
    assert 'SeriesBookAnalysisPlan' not in schemas  # Removed with the Classic engine (contract 0.3.0).
    assert not {'voice', 'system_voice'} & set(schemas['CharacterEdit']['properties'])
    character = book['characters'][-1]['id']
    for alias in ('voice', 'system_voice'):
        response = client.patch(f"/api/books/{book['id']}/characters/{character}", json={alias: 'Puck'})
        assert response.status_code == 422, response.text


# 8. Minor ordering and validation --------------------------------------------------------------------

def test_plan_for_an_unknown_book_is_404_before_config_validation(client):
    response = client.post('/api/books/no-such-book/analysis-pipeline/plan',
                           json={'steps': ['discovery'], 'configs': {'discovery': {'provider': 'nope'}}})
    assert response.status_code == 404 and response.json()['code'] == 'book_not_found'


def test_series_names_with_control_characters_are_refused_at_creation(client):
    response = client.post('/api/series', json={'name': 'Lamps\x01of the harbor'})
    assert response.status_code == 400 and response.json()['code'] == 'text_invalid'
    assert client.get('/api/series').json() == []


def test_auto_accepted_analysis_note_names_no_ui_location(client):
    book = import_book(client)
    job, _ = run(client, book['id'], ['discovery'])
    assert job['status'] == 'completed'
    note = client.get(f"/api/books/{book['id']}").json()['analysis']['notes']
    assert 'Analysis tab' not in note and note == 'Character discovery accepted from a pipeline run.'


# 9. Error details describe only the condition -------------------------------------------------------

def test_archived_details_are_one_sentence_per_code(client):
    book = import_text(client)
    series = series_with(client, book)
    assert client.post(f"/api/series/{series['id']}/archive").status_code == 200
    renamed = client.patch(f"/api/series/{series['id']}", json={'name': 'Other'})
    assert renamed.json() == {'detail': 'This series is archived.', 'code': 'series_archived'}
    planned = client.post(f"/api/series/{series['id']}/plan", json={'steps': ['discovery']})
    assert planned.json() == {'detail': 'This series is archived.', 'code': 'series_archived'}
    assert client.post(f"/api/books/{book['id']}/archive").status_code == 200
    archived = {'detail': 'This book is archived.', 'code': 'book_archived'}
    responses = [client.patch(f"/api/books/{book['id']}/scenes/{book['scenes'][0]['id']}", json={'title': 'X'}),
                 client.patch(f"/api/books/{book['id']}/metadata", json={'title': 'X', 'author': ''}),
                 client.post(f"/api/books/{book['id']}/analysis-pipeline/runs",
                             json={'steps': ['census'], 'limits': {'max_requests': 5}}),
                 client.post(f"/api/books/{book['id']}/listen/chapter/preview",
                             json={'segment_id': book['segments'][0]['id']})]
    for response in responses:
        assert response.status_code == 409, response.text
        assert response.json() == archived, response.request.url


# A. Outside writers record the projection they replace ----------------------------------------------

# Main's Classic-analysis capture test is dropped: the Classic engine is gone. The pipeline's own
# accept path syncs before it writes, and test_analysis_pipeline covers outside-change capture.


def test_manual_edits_structure_repair_and_series_runs_record_the_prior_projection(client):
    edited = import_book(client)
    title = edited['scenes'][0]['title']
    assert client.patch(f"/api/books/{edited['id']}/scenes/{edited['scenes'][0]['id']}", json={'title': 'Hand-made'}).status_code == 200
    baseline = versions(client, edited['id'], 'directing')
    assert [v['origin'] for v in baseline] == ['baseline']
    # The baseline holds the state before the edit. (Accepting it keeps the edit: edited fields are locked.)
    repository, store = PipelineRepository(store_of(client)), store_of(client)
    scopes = repository.step_run(edited['id'], baseline[0]['id'])['scopes']
    with store.connect() as conn:
        payloads = repository.payloads(conn, scopes.values())
    titles = [scene['title'] for payload in payloads.values() for scene in payload['result']['scenes']]
    assert title in titles and 'Hand-made' not in titles

    repaired = import_book(client)
    assert client.post(f"/api/books/{repaired['id']}/repair-structure").status_code == 200
    assert [v['origin'] for v in versions(client, repaired['id'], 'structure')] == ['baseline']

    # Series children are pipeline runs, which sync before they run: the prior projection is a baseline.
    member = import_book(client)
    series = series_with(client, member)
    assert versions(client, member['id'], 'directing') == []
    started = client.post(f"/api/series/{series['id']}/process", json={'steps': ['census'], 'limits': {'max_requests': 5}})
    assert started.status_code == 200, started.text
    assert wait_job(client, started.json()['id'])['status'] == 'completed'
    assert [v['origin'] for v in versions(client, member['id'], 'directing')] == ['baseline']


# B. Main-era confirmations keep their reviewed label ----------------------------------------------

def test_main_era_confirmed_speaker_is_still_reviewed():
    segment = {'id': 's1', 'chapter_id': 'c1', 'kind': 'dialogue', 'start': 0, 'end': 7, 'speaker_id': 'mara',
               'confidence': 1.0, 'edited': True, 'edited_fields': ['direction'],
               'analysis_provider': 'openai', 'analysis_model': MODEL}
    assert reviewed_speaker(segment) and 'speaker_id' in manual_fields(segment)
    segment['confidence'] = .8
    assert not reviewed_speaker(segment) and manual_fields(segment) == ['direction']


def test_main_era_confirmed_speaker_is_reviewed_in_character_references(client):
    book = import_book(client)
    job, _ = run(client, book['id'], ['discovery', 'profiles', 'directing'])
    assert job['status'] == 'completed'
    store = store_of(client)
    stored = store.book(book['id'])
    line = next(s for s in stored['segments'] if s['kind'] == 'dialogue' and s['speaker_id'] not in {'narrator', 'unassigned'})
    # A confirmation saved before per-field locks recorded it: `edited` with confidence 1.0, no speaker lock.
    line.update(edited=True, edited_fields=['direction'], confidence=1.0)
    store.save_book(stored)
    rows = client.get(f"/api/books/{book['id']}/characters/{line['speaker_id']}/references").json()
    row = next(r for r in rows if r['kind'] == 'dialogue' and r['segment_id'] == line['id'])
    assert row['provider'] == 'reviewed' and row['origin'] == 'manual'


# C. Attempts keep their pricing provenance ------------------------------------------------------

def test_attempts_include_the_rates_behind_their_cost_estimate(client):
    book = import_book(client)
    job, _ = run(client, book['id'], ['discovery'])
    assert job['status'] == 'completed'
    assert {'book_id', 'input_rate', 'output_rate'} <= set(ATTEMPT_FIELDS)
    attempts = client.get(f"/api/books/{book['id']}/pipeline").json()['attempts']
    assert attempts and all('input_rate' in a and 'output_rate' in a and a['book_id'] == book['id'] for a in attempts)
