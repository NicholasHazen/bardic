"""The wire says "passage"; storage says "segment". The API translates at its boundary (bardic/wire.py)."""
import json

import pytest

from bardic import wire
from bardic.artifacts import ArtifactRepository
from test_app import client, import_text  # noqa: F401  (fixture)


# ---------------------------------------------------------------- the translation helpers

def test_responses_are_renamed_at_any_depth_but_only_by_key():
    internal = {'segment_id': 's1', 'segment_ids': ['segment_a'], 'jobs': [{'first_segment_id': 'a', 'last_segment_id': 'b',
                'segment_count': 2, 'scope_start_segment_id': 'a', 'focus_segment_id': 'b', 'message': 'segment_id'}]}
    assert wire.to_wire(internal) == {'passage_id': 's1', 'passage_ids': ['segment_a'], 'jobs': [
        {'first_passage_id': 'a', 'last_passage_id': 'b', 'passage_count': 2, 'scope_start_passage_id': 'a',
         'focus_passage_id': 'b', 'message': 'segment_id'}]}
    assert internal['segment_id'] == 's1', 'the input is not changed'


def test_a_retained_payload_is_shown_exactly_as_stored():
    payload = {'segment_id': 'x', 'segments': [{'segment_id': 'y'}]}
    assert wire.to_wire({'id': 'a', 'segment_id': 'z', 'payload': payload}) == {'id': 'a', 'passage_id': 'z', 'payload': payload}


def test_a_book_and_sentence_timing_take_their_wire_names():
    book = wire.book({'id': 'b', 'segments': [{'id': 'p'}], 'characters': [
        {'id': 'c', 'voices': {'breeze': {'id': 'v', 'revision': 'r', 'seed': 1}, 'gemini': {'id': 'Kore'}}}]})
    assert 'segments' not in book and book['passages'] == [{'id': 'p'}]
    assert book['characters'][0]['voices'] == {'breeze': {'id': 'v', 'voice_revision': 'r', 'seed': 1}, 'gemini': {'id': 'Kore'}}
    timing = {'schema_version': 1, 'kind': 'sentence', 'segments': [{'char_start': 0, 'char_end': 5, 'start': 0.5, 'end': 1.25}]}
    assert wire.provider_timing(timing) == {'schema_version': 1, 'kind': 'sentence', 'sentences': [
        {'char_start': 0, 'char_end': 5, 'start_seconds': 0.5, 'end_seconds': 1.25}]}
    assert wire.provider_timing(None) is None
    assert wire.units({'total': 3, 'done': 2, 'cached': 1, 'failed': 0}) == {'total': 3, 'done': 2, 'cached_units': 1, 'failed': 0}


# ---------------------------------------------------------------- the routes and bodies

def test_the_book_document_has_passages_and_no_segments(client):  # noqa: F811
    book = import_text(client)
    assert 'segments' not in book and book['passages']
    assert all('passage_ids' in scene and 'segment_ids' not in scene for scene in book['scenes'])
    assert client.get(f"/api/books/{book['id']}").json()['passages'] == book['passages']
    stored = client.app.state.runtime.store.book(book['id'])
    assert 'segments' in stored and 'passages' not in stored, 'storage keeps its own names'


def test_a_passage_is_edited_through_the_passages_route(client):  # noqa: F811
    book = import_text(client)
    passage = book['passages'][0]
    edited = client.patch(f"/api/books/{book['id']}/passages/{passage['id']}", json={'direction': 'Softly.'})
    assert edited.status_code == 200 and edited.json()['passages'][0]['direction'] == 'Softly.'
    unknown = client.patch(f"/api/books/{book['id']}/passages/missing", json={'direction': 'x'})
    assert unknown.status_code == 404 and unknown.json()['code'] == 'passage_not_found'


@pytest.mark.parametrize('path, body', [
    ('listen', {'provider': 'system'}),
    ('listen/chapter', {'provider': 'gemini'}),
    ('render', {'provider': 'system'}),
    ('voice-preview', {'provider': 'system'}),
])
def test_requests_take_passage_id_and_refuse_the_old_name(client, path, body):  # noqa: F811
    book = import_text(client)
    url = f"/api/books/{book['id']}/{path}"
    old = client.post(url, json={**body, 'segment_id': book['passages'][0]['id']})
    assert old.status_code == 422 and old.json()['code'] == 'validation_error'
    assert any(issue['type'] in ('extra_forbidden', 'missing') and issue['loc'][-1] in ('segment_id', 'passage_id')
               for issue in old.json()['detail'])
    if path == 'voice-preview':
        old = client.post(url, json={**body, 'passage_id': book['passages'][0]['id'], 'segment_direction': 'x'})
        assert old.status_code == 422 and old.json()['detail'][0]['loc'][-1] == 'segment_direction'
        assert client.post(url, json={**body, 'passage_direction': 'x'}).json()['code'] == 'passage_direction_incomplete'


def test_diagnostics_take_passage_id(client):  # noqa: F811
    book = import_text(client)
    passage = book['passages'][0]['id']
    old = client.post('/api/diagnostics', json={'event': 'buffer_failed', 'book_id': book['id'], 'segment_id': passage})
    assert old.status_code == 422
    accepted = client.post('/api/diagnostics', json={'event': 'buffer_failed', 'book_id': book['id'], 'passage_id': passage})
    assert accepted.status_code == 200 and accepted.json()['recorded'] is True
    event = client.get('/api/diagnostics', params={'book_id': book['id']}).json()['events'][0]
    assert event['passage_id'] == passage and 'segment_id' not in event


def test_an_artifact_payload_keeps_the_names_it_was_retained_with(client):  # noqa: F811
    book = import_text(client)
    store = client.app.state.runtime.store
    repository = ArtifactRepository(store)
    identifier = repository.output_head(book['id'], 'scene_map', book['chapters'][0]['id'])
    assert identifier, 'import retains a scene map'
    stored = repository.get(book['id'], identifier)['payload']
    served = client.get(f"/api/books/{book['id']}/artifacts/{identifier}").json()['payload']
    assert served == stored
    assert 'segment' in json.dumps(stored), 'the retained payload names passages the way it was stored'


def test_the_library_summary_counts_passages(client):  # noqa: F811
    import_text(client)
    summary = client.get('/api/books').json()[0]
    assert summary['passage_count'] >= 1 and 'segment_count' not in summary


def test_status_no_longer_carries_the_duplicate_timing_kind(client):  # noqa: F811
    status = client.get('/api/status').json()
    assert 'timing_kind' not in status
    assert all(info['capabilities']['timing'] == 'passage' for info in status['narration_providers'].values())
