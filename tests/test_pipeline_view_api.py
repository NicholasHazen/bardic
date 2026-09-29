"""Offline public views of retained pipeline outputs, provenance and source search."""
from copy import deepcopy
import io
import json
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from bardic.app import create_app
from bardic.artifacts import ArtifactRepository, record
from bardic.processing import ProcessingStore
from bardic.series import SeriesRepository
from classic_fixtures import classic_references


SOURCE = ('Chapter One\n\nMara remembered Elio beneath the moon 🌙.\n\n'
          '“Wait,” Mara said.\n\nChapter Two\n\nElio was mentioned again.\n')


@pytest.fixture
def client(tmp_path, monkeypatch):
    for variable in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr('bardic.app.list_system_voices', lambda: [])

    def no_network(*_args, **_kwargs):
        pytest.fail('Pipeline browsing and export must not call providers or external services')

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', no_network)
    with TestClient(create_app(tmp_path)) as test_client:
        yield test_client


def import_book(client, text=SOURCE, filename='story.txt'):
    response = client.post('/api/books', files={'file': (filename, text.encode(), 'text/plain')})
    assert response.status_code == 200, response.text
    return response.json()


def save_artifact(store, book_id, key, payload, *, dependencies=(), kind='test_output', stage='discovery'):
    ArtifactRepository(store)
    with store.lock, store.connect() as conn:
        return record(conn, book_id, kind, key, payload, dependencies=dependencies, stage=stage,
                      label=f'Saved {key}', provider='offline-test', model='test-model')


def book_with_references(client):
    imported = import_book(client)
    store = client.app.state.runtime.store
    book = store.book(imported['id'])
    for identifier, name in (('mara', 'Mara'), ('elio', 'Elio')):
        book['characters'].append({'id': identifier, 'name': name, 'aliases': [], 'description': 'Unknown vocal traits.',
                                   'direction': 'Natural delivery.', 'evidence': [], 'voice': 'Kore', 'system_voice': ''})
    dialogue = next(segment for segment in book['segments'] if segment['kind'] == 'dialogue')
    dialogue.update(speaker_id='mara', confidence=1.0, edited=True)
    store.save_book(book)
    chapter = next(chapter for chapter in book['chapters'] if chapter['id'] == dialogue['chapter_id'])
    name_start = chapter['text'].index('Elio')
    evidence_start = chapter['text'].index('Mara said')
    refs = [
        {'id': 'mention', 'character_id': 'elio', 'chapter_id': chapter['id'], 'segment_id': None,
         'start': name_start, 'end': name_start + 4, 'quote': 'Elio', 'kind': 'mention', 'provider': 'local', 'model': None},
        {'id': 'dialogue', 'character_id': 'mara', 'chapter_id': chapter['id'], 'segment_id': dialogue['id'],
         'start': dialogue['start'], 'end': dialogue['end'], 'quote': dialogue['text'], 'kind': 'dialogue',
         'confidence': 1.0, 'provider': 'reviewed', 'model': None},
        {'id': 'evidence', 'character_id': 'mara', 'chapter_id': chapter['id'], 'segment_id': None,
         'start': evidence_start, 'end': evidence_start + len('Mara said'), 'quote': 'Mara said',
         'kind': 'profile_evidence', 'profile_description': 'Named in an explicit speech tag.',
         'profile_direction': 'No permanent vocal trait established.', 'provider': 'local', 'model': None},
    ]
    classic_references(store, book['id'], refs)
    return book, refs


def test_analysis_export_needs_no_audio_and_contains_history_and_transitive_cross_book_inputs(client):
    target = import_book(client, filename='target.txt')
    previous = import_book(client, 'Mara remembers the old lamp.', 'earlier.txt')
    earliest = import_book(client, 'Mara first found the lamp.', 'first.txt')
    store = client.app.state.runtime.store
    before = deepcopy(store.book(target['id']))
    runtime = client.app.state.runtime
    runtime.api_keys['openai'] = 'credential-not-for-export'
    store.save_settings({'private_preference': 'settings-not-for-export'})
    leaf = save_artifact(store, earliest['id'], 'first-observation', {'quote': 'Mara first found the lamp.'})
    middle = save_artifact(store, previous['id'], 'earlier-profile', {'description': 'Known earlier context'}, dependencies=[leaf])
    unrelated = save_artifact(store, previous['id'], 'unrelated-output', {'description': 'Must not travel with this export'})
    old = save_artifact(store, target['id'], 'current-profile', {'description': 'First interpretation'}, dependencies=[middle])
    current = save_artifact(store, target['id'], 'current-profile', {'description': 'Revised interpretation'}, dependencies=[middle])

    current_view = client.get(f"/api/books/{target['id']}/artifacts/{current}").json()
    assert current_view['dependency_links'] == [{'id': middle, 'book_id': previous['id']}]
    assert client.get(f"/api/books/{target['id']}/artifacts/{middle}").status_code == 404
    prior_view = client.get(f"/api/books/{previous['id']}/artifacts/{middle}").json()
    assert prior_view['dependency_links'] == [{'id': leaf, 'book_id': earliest['id']}]
    assert not any(segment.get('audio') for segment in before['segments'])
    assert client.get(f"/api/books/{target['id']}/export").status_code == 400
    response = client.get(f"/api/books/{target['id']}/analysis-export")
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('application/zip')
    assert 'attachment;' in response.headers['content-disposition']
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert {'manifest.json', 'book.json', 'story-map.json', 'artifacts.jsonl', 'observations.json',
                'references.json', 'series.json', 'analysis-attempts.json', 'pipeline-events.jsonl', 'README.txt'} <= names
        assert not any(name.endswith(('.wav', '.mp3', '.m4b')) for name in names)
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['schema_version'] == 2 and manifest['format'] == 'spintails-analysis'
        assert manifest['source_text_included'] is True and manifest['audio_files_included'] is False
        assert manifest['word_alignment'] is False
        assert set(manifest['external_book_dependencies']) == {previous['id'], earliest['id']}
        exported_book = json.loads(archive.read('book.json'))
        assert exported_book == before
        artifacts = [json.loads(line) for line in archive.read('artifacts.jsonl').splitlines()]
        by_id = {artifact['id']: artifact for artifact in artifacts}
        assert {leaf, middle, old, current} <= by_id.keys()
        assert unrelated not in by_id
        assert by_id[old]['is_current'] is False and by_id[current]['is_current'] is True
        assert by_id[old]['payload']['description'] == 'First interpretation'
        assert by_id[current]['payload']['description'] == 'Revised interpretation'
        assert all(set(artifact['dependencies']) <= by_id.keys() for artifact in artifacts)
        assert manifest['artifact_count'] == len(artifacts)
        exported_text = b'\n'.join(archive.read(name) for name in names)
        assert b'credential-not-for-export' not in exported_text
        assert b'settings-not-for-export' not in exported_text
    assert store.book(target['id']) == before
    assert client.get('/api/jobs').json() == []


def test_artifact_metadata_is_paginated_filtered_book_scoped_and_payload_is_inspectable(client):
    first = import_book(client)
    second = import_book(client, 'Mara is an unrelated namesake.', 'other.txt')
    store = client.app.state.runtime.store
    old = save_artifact(store, first['id'], 'one', {'value': 'old'})
    current = save_artifact(store, first['id'], 'one', {'value': 'current'})
    other = save_artifact(store, second['id'], 'one', {'value': 'other book'})
    base = f"/api/books/{first['id']}/artifacts"
    first_page = client.get(base, params={'kind': 'test_output', 'stage': 'discovery', 'limit': 1}).json()
    second_page = client.get(base, params={'kind': 'test_output', 'stage': 'discovery', 'limit': 1, 'offset': 1}).json()
    assert first_page['total'] == second_page['total'] == 2
    assert first_page['offset'] == 0 and first_page['limit'] == 1
    assert second_page['offset'] == 1 and second_page['limit'] == 1
    assert first_page['items'][0]['id'] == current
    assert second_page['items'][0]['id'] == old
    assert 'payload' not in first_page['items'][0]
    assert first_page['items'][0]['provider'] == 'offline-test'
    assert first_page['items'][0]['schema_version'] == 1
    assert first_page['items'][0]['legacy_provenance'] is False
    history = client.get(base, params={'kind': 'test_output', 'current': 'false'}).json()
    assert history['total'] == 1 and history['items'][0]['id'] == old
    active = client.get(base, params={'kind': 'test_output', 'current': 'true'}).json()
    assert active['total'] == 1 and active['items'][0]['id'] == current
    assert client.get(base, params={'kind': 'unknown-kind'}).json()['total'] == 0
    assert client.get(base, params={'kind': 'test_output', 'offset': 10}).json()['items'] == []
    inspected = client.get(base + '/' + old)
    assert inspected.status_code == 200
    assert inspected.json()['payload'] == {'value': 'old'}
    assert inspected.json()['is_current'] is False
    assert client.get(base + '/' + other).status_code == 404
    assert client.get(f"/api/books/{second['id']}/artifacts/{current}").status_code == 404
    assert client.get(base + '/missing').status_code == 404


@pytest.mark.parametrize('params,status', [
    ({'limit': 'not-a-number'}, 422), ({'offset': 'not-a-number'}, 422), ({'current': 'not-a-boolean'}, 422),
])
def test_invalid_artifact_pagination_is_rejected(client, params, status):
    # Out-of-range numbers are clamped (tests/test_inspection_api.py); only malformed values are rejected.
    book = import_book(client)
    response = client.get(f"/api/books/{book['id']}/artifacts", params=params)
    assert response.status_code == status, response.text
    assert client.get('/api/jobs').json() == []


def test_story_map_has_verified_source_anchors_and_typed_edges_without_invented_presence(client):
    book, refs = book_with_references(client)
    store = client.app.state.runtime.store
    original = deepcopy(store.book(book['id']))
    response = client.get(f"/api/books/{book['id']}/story-map")
    assert response.status_code == 200, response.text
    graph = response.json()
    assert graph['schema_version'] == 1
    assert graph['reference_counts'] == {'mention': 1, 'dialogue': 1, 'profile_evidence': 1}
    assert graph['references'] == refs
    assert 'not verified physical presence' in graph['note']
    nodes = {node['id']: node for node in graph['nodes']}
    source_by_chapter = {chapter['id']: chapter for chapter in book['chapters']}
    passages = {segment['id']: segment for segment in book['segments']}
    assert {edge['type'] for edge in graph['edges']} == {'contains', 'next', 'attributed_speaker'}
    assert all(edge['from'] in nodes and edge['to'] in nodes for edge in graph['edges'])
    for node in graph['nodes']:
        if node['type'] != 'passage':
            continue
        passage = passages[node['passage_id']]
        anchor = node['source_anchor']
        assert anchor is not None
        chapter = source_by_chapter[node['chapter_id']]
        assert chapter['text'][anchor['start']:anchor['end']] == passage['text']
        source = client.get(f"/api/books/{book['id']}/artifacts/{anchor['artifact_id']}").json()
        assert source['kind'] == 'source' and source['payload']['text'] == chapter['text']
    speaker_edges = [edge for edge in graph['edges'] if edge['type'] == 'attributed_speaker']
    assert len(speaker_edges) == 1
    speaker_node = nodes[speaker_edges[0]['to']]
    assert speaker_node['type'] == 'character'
    assert speaker_node['character_id'] == 'mara'
    assert speaker_edges[0]['confidence'] == 1.0
    assert all('elio' not in scene['character_ids'] for chapter in graph['chapters'] for scene in chapter['scenes'])
    assert store.book(book['id']) == original


def test_story_map_marks_unverified_legacy_passages_without_fabricating_anchors(client):
    imported = import_book(client)
    store = client.app.state.runtime.store
    book = store.book(imported['id'])
    bad_id = book['segments'][0]['id']
    book['segments'][0]['text'] = 'This legacy passage no longer matches its source.'
    store.save_book(book)
    graph = client.get(f"/api/books/{book['id']}/story-map").json()
    bad = next(node for node in graph['nodes'] if node.get('passage_id') == bad_id)
    assert bad['source_anchor'] is None
    assert store.book(book['id']) == book


def test_pipeline_distinguishes_http_success_validation_and_unfinished_reservations(client):
    imported = import_book(client)
    store = client.app.state.runtime.store
    original = deepcopy(store.book(imported['id']))
    processing = ProcessingStore(store)
    active = store.create_job(imported['id'], 'analyze')
    store.update_job(active['id'], status='running', phase='scan')
    attempts = [
        ('unknown', 'received', 'old-run'), ('accepted', 'received', 'old-run'),
        ('rejected', 'received', 'old-run'), ('orphaned', 'reserved', 'missing-run'),
        ('in-flight', 'reserved', active['id']),
    ]
    for identifier, state, run_id in attempts:
        processing.save_attempt({'id': identifier, 'book_id': imported['id'], 'run_id': run_id, 'stage': 'discovery',
            'unit_key': 'same-unit', 'provider': 'openai', 'model': 'test-model', 'status': state,
            'http_status': 200 if state == 'received' else None, 'input_tokens': None, 'output_tokens': None,
            'reserved_input_tokens': 100, 'reserved_output_tokens': 20, 'charged_estimate_usd': .001,
            'created_at': '2026-09-27T12:00:00+00:00', 'api_key': 'must-not-leak-from-attempt', 'private_response': 'not-public'})
    processing.event(imported['id'], 'old-run', 'discovery', 'same-unit', 'accepted', attempt_id='accepted')
    processing.event(imported['id'], 'old-run', 'discovery', 'same-unit', 'validation_rejected', attempt_id='rejected', error='Evidence validation failed.')
    processing.event(imported['id'], 'later-run', 'discovery', 'same-unit', 'cache_hit', artifact_id='recorded-output')
    # A saved artifact for the same unit does not prove the unknown attempt was accepted.
    save_artifact(store, imported['id'], 'same-unit', {'characters': []}, kind='analysis_output')
    response = client.get(f"/api/books/{imported['id']}/pipeline")
    assert response.status_code == 200, response.text
    view = response.json()
    public = {attempt['id']: attempt for attempt in view['attempts']}
    assert public['unknown']['http_status'] == 200 and public['unknown']['validation_state'] == 'unknown'
    assert public['accepted']['validation_state'] == 'accepted'
    assert public['rejected']['validation_state'] == 'rejected'
    assert public['orphaned']['status'] == 'interrupted_unknown'
    assert public['in-flight']['status'] == 'reserved'
    assert 'must-not-leak-from-attempt' not in response.text and 'not-public' not in response.text
    stages = {stage['id']: stage for stage in view['stages']}
    assert stages['alignment']['status'] == 'planned' and stages['alignment']['total'] is None
    assert view['capabilities']['word_alignment'] is False
    assert stages['export']['status'] == 'ready'
    assert stages['narration']['completed'] == 0
    assert {'directing', 'voices'} <= set(stages['narration']['dependencies'])
    assert 'progress_percent' not in view
    assert any(event['event'] == 'cache_hit' for event in view['events'])
    assert view['usage']['attempts'] == 5
    assert store.book(imported['id']) == original
    assert len(store.jobs(imported['id'])) == 1  # Browsing did not create another run.


def test_pipeline_validation_history_is_not_lost_when_latest_event_preview_is_bounded(client):
    book = import_book(client)
    store = client.app.state.runtime.store
    processing = ProcessingStore(store)
    processing.save_attempt({'id': 'accepted-attempt', 'book_id': book['id'], 'run_id': 'old', 'stage': 'profiles',
                            'unit_key': 'profile-unit', 'status': 'received', 'http_status': 200,
                            'input_tokens': 1, 'output_tokens': 1, 'charged_estimate_usd': 0})
    processing.event(book['id'], 'old', 'profiles', 'profile-unit', 'accepted', attempt_id='accepted-attempt')
    for index in range(105):
        processing.event(book['id'], 'recent', 'discovery', f'unit-{index}', 'cache_hit')
    view = client.get(f"/api/books/{book['id']}/pipeline").json()
    assert len(view['events']) == 100
    assert not any(event['event'] == 'accepted' for event in view['events'])
    assert view['attempts'][0]['validation_state'] == 'accepted'


def test_pipeline_does_not_count_a_local_draft_as_discovery(client):
    from bardic.analysis import analyze_book

    book = import_book(client)
    store = client.app.state.runtime.store
    with store.lock:
        draft = analyze_book(store.book(book['id']), 'local')
        store.save_book(draft)
    view = client.get(f"/api/books/{book['id']}/pipeline").json()
    stages = {stage['id']: stage for stage in view['stages']}
    # Discovery output cannot be recovered from a book, so an outside draft never counts as discovered.
    assert stages['discovery']['completed'] == 0 and stages['discovery']['status'] == 'pending'
    assert stages['discovery']['total'] == 2 and stages['discovery']['unit_label'] == 'eligible sections'
    assert [(chapter['id'], chapter['text']) for chapter in draft['chapters']] == [
        (chapter['id'], chapter['text']) for chapter in book['chapters']]
    assert ProcessingStore(store).attempts(book['id']) == []


def test_views_and_search_preserve_source_and_do_not_queue_processing(client):
    book = import_book(client)
    store = client.app.state.runtime.store
    before = deepcopy(store.book(book['id']))
    original_path = store.root / 'originals' / book['id'] / 'source.txt'
    original_bytes = original_path.read_bytes()
    for route in ('pipeline', 'artifacts', 'story-map', 'analysis-export'):
        response = client.get(f"/api/books/{book['id']}/{route}")
        assert response.status_code == 200, response.text
    response = client.get(f"/api/books/{book['id']}/search", params={'q': 'Mara', 'scope': 'book', 'limit': 20})
    assert response.status_code == 200, response.text
    search = response.json()
    assert search['available'] is True and search['scope'] == 'book' and search['query'] == 'Mara'
    assert search['items']
    by_chapter = {chapter['id']: chapter for chapter in before['chapters']}
    for result in search['items']:
        assert result['book_id'] == book['id']
        assert by_chapter[result['chapter_id']]['text'][result['start']:result['end']] == result['text']
        assert isinstance(result['rank'], (int, float))
    assert store.book(book['id']) == before
    assert original_path.read_bytes() == original_bytes
    assert ProcessingStore(store).attempts(book['id']) == []
    assert client.get('/api/jobs').json() == []


def test_search_api_earlier_series_scope_includes_current_and_prior_but_not_future_or_other_series(client):
    books = [import_book(client, f'Mara saw the lantern in volume {name}.', f'{name}.txt')
             for name in ('one', 'nine', 'ten', 'other')]
    store = client.app.state.runtime.store
    series = SeriesRepository(store)
    saga = series.create_series('The Lanterns')
    for source, order in zip(books[:3], (1, 9, 10)):
        series.set_membership(source['id'], saga['id'], order)
    other = series.create_series('Namesakes')
    series.set_membership(books[3]['id'], other['id'], 1)
    for source in books:  # Excluded books are already indexed; filtering still precedes ranking.
        assert client.get(f"/api/books/{source['id']}/search", params={'q': 'Mara'}).status_code == 200
    response = client.get(f"/api/books/{books[1]['id']}/search", params={'q': 'Mara', 'scope': 'earlier'})
    assert response.status_code == 200
    data = response.json()
    assert data['query'] == 'Mara' and data['scope'] == 'earlier'
    assert {item['book_id'] for item in data['items']} == {books[0]['id'], books[1]['id']}
    assert client.get('/api/jobs').json() == []


@pytest.mark.parametrize('route,params', [
    ('pipeline', {}), ('artifacts', {}), ('artifacts/missing', {}), ('story-map', {}),
    ('analysis-export', {}), ('search', {'q': 'Mara'}), ('search', {'q': '***'}),
])
def test_missing_book_views_return_404(client, route, params):
    response = client.get(f'/api/books/missing/{route}', params=params)
    assert response.status_code == 404, response.text


@pytest.mark.parametrize('params,status', [({}, 422), ({'q': ''}, 400), ({'q': 'Mara', 'scope': 'all'}, 400)])
def test_search_api_rejects_missing_query_or_invalid_scope(client, params, status):
    book = import_book(client)
    response = client.get(f"/api/books/{book['id']}/search", params=params)
    assert response.status_code == status, response.text
