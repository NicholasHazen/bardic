"""Offline checks of the book document and manual-edit routes.

Every response here is also validated against the published contract by
tests/conftest.py, including each error `code`.
"""
import pytest
from fastapi.testclient import TestClient

from bardic.app import create_app
from classic_fixtures import classic_references


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY',
                 'BREEZE_TTS_URL', 'BREEZE_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(tmp_path)) as c:
        yield c


# ------------------------------------------------------------------ helpers

def runtime(client):
    return client.app.state.runtime


def stored(client, book_id):
    return runtime(client).store.book(book_id)


def item(book, collection, item_id):
    return next(x for x in book[collection] if x['id'] == item_id)


def dialogue(book, name):
    cid = next(c['id'] for c in book['characters'] if c['name'] == name)
    return cid, [s for s in book['passages'] if s['speaker_id'] == cid]


def use_breeze_catalog(client):
    rt = runtime(client)
    url = 'http://127.0.0.1:9'
    rt.preferences['breeze_url'] = url
    rt.preferences['breeze_catalog'] = {
        'base_url': url, 'state': 'ready', 'message': '', 'model': None, 'default_voice_id': 'keeper',
        'voices': [{'id': 'keeper', 'name': 'Keeper', 'usable': True, 'reason': None, 'revision': 'r1', 'seed': 7},
                   {'id': 'tide', 'name': 'Tide', 'usable': True, 'reason': None, 'revision': 'r2', 'seed': None}]}


# ------------------------------------------------------------------ edits

def test_scene_edit_records_changed_fields_and_returns_the_book(client):
    book = client.post('/api/demo').json()
    scene = book['scenes'][0]
    response = client.patch(f"/api/books/{book['id']}/scenes/{scene['id']}",
                            json={'title': 'At the lighthouse', 'tone': scene['tone'], 'summary': None})
    assert response.status_code == 200, response.text
    updated = response.json()
    edited = next(s for s in updated['scenes'] if s['id'] == scene['id'])
    assert edited['title'] == 'At the lighthouse'
    assert edited['summary'] == scene['summary']  # null is ignored
    record = item(stored(client, book['id']), 'scenes', scene['id'])
    assert record['edited'] is True and record['edited_fields'] == ['title']  # unchanged tone is not locked
    assert updated['revision'] == book['revision'] + 1
    assert [s['text'] for s in updated['passages']] == [s['text'] for s in book['passages']]


def test_unchanged_or_empty_edits_change_nothing(client):
    book = client.post('/api/demo').json()
    scene = book['scenes'][0]
    passage = book['passages'][0]
    mara_id, _ = dialogue(book, 'Mara')
    mara = item(book, 'characters', mara_id)
    requests = [
        (f"scenes/{scene['id']}", {}),
        (f"scenes/{scene['id']}", {'title': scene['title'], 'tone': scene['tone'], 'summary': scene['summary']}),
        (f"passages/{passage['id']}", {}),
        (f"passages/{passage['id']}", {'speaker_id': passage['speaker_id'], 'direction': passage['direction'],
                                       'cues': passage['cues']}),
        (f"characters/{mara_id}", {}),
        (f"characters/{mara_id}", {'name': mara['name'], 'description': mara['description'],
                                   'voices': {'gemini': mara['voices']['gemini']}}),
    ]
    for path, body in requests:
        response = client.patch(f"/api/books/{book['id']}/{path}", json=body)
        assert response.status_code == 200, (path, body, response.text)
        assert response.json()['revision'] == book['revision'], (path, body)
    saved = stored(client, book['id'])
    assert saved['revision'] == book['revision']
    for collection, item_id in (('scenes', scene['id']), ('segments', passage['id']), ('characters', mara_id)):
        record = item(saved, collection, item_id)
        assert 'edited' not in record and 'edited_fields' not in record, (collection, record)


def test_unchanged_voice_of_a_legacy_character_is_a_no_op(client):
    book = client.post('/api/demo').json()
    mara_id, _ = dialogue(book, 'Mara')
    saved = stored(client, book['id'])
    legacy = item(saved, 'characters', mara_id)
    legacy.pop('voices', None)
    legacy['voice'] = 'Leda'
    runtime(client).store.save_book(saved)
    response = client.patch(f"/api/books/{book['id']}/characters/{mara_id}", json={'voices': {'gemini': {'id': 'Leda'}}})
    assert response.status_code == 200
    assert item(response.json(), 'characters', mara_id)['voices'] == {'gemini': {'id': 'Leda'}}
    assert response.json()['revision'] == book['revision']
    assert item(stored(client, book['id']), 'characters', mara_id)['voice'] == 'Leda'


def test_real_edit_locks_only_changed_fields_and_confirmation_locks_the_speaker(client):
    book = client.post('/api/demo').json()
    mara_id, lines = dialogue(book, 'Mara')
    line = lines[0]
    assert line['confidence'] < 1
    # Confirming the proposed speaker is a review: confidence becomes 1.0 and the speaker is locked.
    response = client.patch(f"/api/books/{book['id']}/passages/{line['id']}",
                            json={'speaker_id': mara_id, 'direction': line['direction']})
    assert response.status_code == 200
    assert response.json()['revision'] == book['revision'] + 1
    record = item(stored(client, book['id']), 'segments', line['id'])
    assert record['confidence'] == 1.0 and record['edited'] is True and record['edited_fields'] == ['speaker_id']
    again = client.patch(f"/api/books/{book['id']}/passages/{line['id']}", json={'speaker_id': mara_id})
    assert again.json()['revision'] == book['revision'] + 1


def test_legacy_whole_item_lock_is_kept_by_a_later_edit(client):
    book = client.post('/api/demo').json()
    scene_id = book['scenes'][0]['id']
    saved = stored(client, book['id'])
    item(saved, 'scenes', scene_id)['edited'] = True
    runtime(client).store.save_book(saved)
    client.patch(f"/api/books/{book['id']}/scenes/{scene_id}", json={'tone': 'Stormy'})
    record = item(stored(client, book['id']), 'scenes', scene_id)
    assert record['edited_fields'] == ['*', 'tone'] and record['tone'] == 'Stormy'


def test_passage_speaker_must_be_in_the_cast(client):
    book = client.post('/api/demo').json()
    response = client.patch(f"/api/books/{book['id']}/passages/{book['passages'][0]['id']}", json={'speaker_id': 'nobody'})
    assert response.status_code == 400 and response.json()['code'] == 'character_not_in_cast'


# ------------------------------------------------------------------ voice choices and seeds

def test_blank_voice_id_clears_the_choice_for_every_provider(client):
    book = client.post('/api/demo').json()
    use_breeze_catalog(client)
    mara_id, _ = dialogue(book, 'Mara')
    url = f"/api/books/{book['id']}/characters/{mara_id}"
    everything = {'voices': {'breeze': {'id': 'tide'}, 'gemini': {'id': 'Puck'}, 'system': {'id': 'Fred'}}}
    chosen = client.patch(url, json=everything)
    assert chosen.status_code == 200, chosen.text
    assert set(item(chosen.json(), 'characters', mara_id)['voices']) == {'breeze', 'gemini', 'system'}
    for blank in ('', '   '):
        client.patch(url, json=everything)
        cleared = client.patch(url, json={'voices': {'breeze': {'id': blank}, 'gemini': {'id': blank}, 'system': {'id': blank}}})
        assert cleared.status_code == 200, cleared.text
        assert item(cleared.json(), 'characters', mara_id)['voices'] == {}, blank
    # A blank Breeze ID never pins the server's default voice.
    client.patch(url, json={'voices': {'breeze': {'id': ' '}}})
    assert 'breeze' not in item(stored(client, book['id']), 'characters', mara_id)['voices']


def test_seed_is_rejected_where_it_does_not_apply(client):
    book = client.post('/api/demo').json()
    use_breeze_catalog(client)
    mara_id, _ = dialogue(book, 'Mara')
    url = f"/api/books/{book['id']}/characters/{mara_id}"
    for choice in ({'gemini': {'id': 'Puck', 'seed': 3}}, {'system': {'id': 'Fred', 'seed': 3}},
                   {'breeze': {'library': 'vl_0123456789abcdef', 'seed': 3}}, {'breeze': {'id': ' ', 'seed': 3}}):
        response = client.patch(url, json={'voices': choice})
        assert response.status_code == 400, choice
        assert response.json()['code'] == 'seed_not_applicable'
    assert stored(client, book['id'])['revision'] == book['revision']
    pinned = client.patch(url, json={'voices': {'breeze': {'id': 'tide', 'seed': 11}}})
    assert item(pinned.json(), 'characters', mara_id)['voices']['breeze'] == {'id': 'tide', 'voice_revision': 'r2', 'seed': 11}
    unknown = client.patch(url, json={'voices': {'breeze': {'id': 'nobody'}}})
    assert unknown.status_code == 400 and unknown.json()['code'] == 'breeze_voice_unavailable'
    library = client.patch(url, json={'voices': {'gemini': {'library': 'vl_0123456789abcdef'}}})
    assert library.status_code == 400 and library.json()['code'] == 'library_voice_unavailable'
    provider = client.patch(url, json={'voices': {'elevenlabs': {'id': 'x'}}})
    assert provider.status_code == 400 and provider.json()['code'] == 'voice_provider_unknown'


def test_passage_seed_can_be_set_and_cleared(client):
    book = client.post('/api/demo').json()
    passage = book['passages'][0]
    url = f"/api/books/{book['id']}/passages/{passage['id']}"
    assert item(client.patch(url, json={'seed': 5}).json(), 'passages', passage['id'])['seed'] == 5
    kept = client.patch(url, json={'direction': 'Softly'}).json()
    assert item(kept, 'passages', passage['id'])['seed'] == 5  # omitted: unchanged
    cleared = client.patch(url, json={'seed': None}).json()
    assert 'seed' not in item(cleared, 'passages', passage['id'])
    assert cleared['revision'] == book['revision'] + 3
    assert 'seed' in item(stored(client, book['id']), 'segments', passage['id'])['edited_fields']


# ------------------------------------------------------------------ add character

def test_added_character_gets_a_device_voice_like_imported_ones(client, monkeypatch):
    monkeypatch.setattr('bardic.app.list_system_voices',
                        lambda: [{'id': 'Samantha', 'name': 'Samantha', 'locale': 'en-US'},
                                 {'id': 'Daniel', 'name': 'Daniel', 'locale': 'en-GB'}])
    book = client.post('/api/demo').json()
    response = client.post(f"/api/books/{book['id']}/characters", json={'name': 'Wren'})
    assert response.status_code == 200, response.text
    wren = next(c for c in response.json()['characters'] if c['name'] == 'Wren')
    assert wren['voices']['system']['id'] in {'Samantha', 'Daniel'}
    assert wren['voices']['gemini'] == {'id': 'Kore'}
    # Nobody else's voices change.
    others = {c['id']: c['voices'] for c in book['characters']}
    assert {c['id']: c['voices'] for c in response.json()['characters'] if c['id'] in others} == others
    # An explicit Default for the device voice is respected.
    default = client.post(f"/api/books/{book['id']}/characters", json={'name': 'Ash', 'voices': {'system': None}})
    assert 'system' not in next(c for c in default.json()['characters'] if c['name'] == 'Ash')['voices']


def test_add_character_requires_a_name(client):
    book = client.post('/api/demo').json()
    response = client.post(f"/api/books/{book['id']}/characters", json={'description': 'Quiet'})
    assert response.status_code == 400 and response.json()['code'] == 'character_name_required'


# ------------------------------------------------------------------ references

def test_references_follow_manual_edits_and_other_projection_writers(client):
    book = client.post('/api/demo').json()
    mara_id, lines = dialogue(book, 'Mara')
    elias_id, elias_lines = dialogue(book, 'Elias')
    url = f"/api/books/{book['id']}/characters/{mara_id}/references"
    refs = client.get(url).json()
    assert {r['passage_id'] for r in refs if r['kind'] == 'dialogue'} == {s['id'] for s in lines}
    assert any(r['kind'] == 'mention' and r['quote'] == 'Mara' for r in refs)
    chapters = {c['id']: c['text'] for c in book['chapters']}
    assert all(chapters[r['chapter_id']][r['start']:r['end']] == r['quote'] for r in refs)
    # A manual reassignment is reflected at once, as a reviewed attribution.
    moved = elias_lines[0]
    client.patch(f"/api/books/{book['id']}/passages/{moved['id']}", json={'speaker_id': mara_id})
    after = client.get(url).json()
    reviewed = next(r for r in after if r['passage_id'] == moved['id'] and r['kind'] == 'dialogue')
    assert reviewed['provider'] == 'reviewed' and reviewed['confidence'] == 1.0
    elias_refs = client.get(f"/api/books/{book['id']}/characters/{elias_id}/references").json()
    assert moved['id'] not in {r['passage_id'] for r in elias_refs if r['kind'] == 'dialogue'}
    # A rename changes the mentions.
    client.patch(f"/api/books/{book['id']}/characters/{mara_id}", json={'name': 'Marah'})
    assert not any(r['kind'] == 'mention' for r in client.get(url).json())
    # Any other writer of the book (for example pipeline acceptance) is reflected too.
    saved = stored(client, book['id'])
    item(saved, 'characters', mara_id)['aliases'] = ['Mara']
    runtime(client).store.save_book(saved)
    assert any(r['kind'] == 'mention' and r['quote'] == 'Mara' for r in client.get(url).json())


def test_stored_profile_evidence_is_kept_while_it_still_matches(client):
    book = client.post('/api/demo').json()
    mara_id, _ = dialogue(book, 'Mara')
    chapter = book['chapters'][0]
    start = chapter['text'].index('lighthouse door')
    evidence = {'id': 'e1', 'character_id': mara_id, 'chapter_id': chapter['id'], 'segment_id': None,
                'start': start, 'end': start + len('lighthouse door'), 'quote': 'lighthouse door',
                'kind': 'profile_evidence', 'confidence': None, 'provider': 'gemini', 'model': 'm',
                'profile_description': 'Watchful.', 'profile_direction': 'Low.'}
    stale = {**evidence, 'id': 'e2', 'start': start + 1}
    classic_references(runtime(client).store, book['id'], [evidence, stale])
    refs = client.get(f"/api/books/{book['id']}/characters/{mara_id}/references").json()
    kept = [r for r in refs if r['kind'] == 'profile_evidence']
    assert [r['id'] for r in kept] == ['e1'] and kept[0]['profile_description'] == 'Watchful.'


# ------------------------------------------------------------------ not found, conflicts and server errors

def test_unknown_ids_in_the_path_are_404_with_specific_codes(client):
    book = client.post('/api/demo').json()
    base = f"/api/books/{book['id']}"
    cases = [
        (client.get('/api/books/missing'), 'book_not_found'),
        (client.patch('/api/books/missing/scenes/x', json={'title': 'X'}), 'book_not_found'),
        (client.patch(f'{base}/scenes/missing', json={'title': 'X'}), 'scene_not_found'),
        (client.patch(f'{base}/passages/missing', json={'direction': 'X'}), 'passage_not_found'),
        (client.patch(f'{base}/characters/missing', json={'name': 'X'}), 'character_not_found'),
        (client.get(f'{base}/characters/missing/references'), 'character_not_found'),
        (client.get('/api/books/missing/characters/narrator/references'), 'book_not_found'),
        (client.patch(f'{base}/pronunciations/pr_000000000000', json={'term': 'Mara', 'respelling': 'Marra'}),
         'pronunciation_not_found'),
        (client.delete(f'{base}/pronunciations/pr_000000000000'), 'pronunciation_not_found'),
        (client.get('/api/books/missing/pronunciations'), 'book_not_found'),
    ]
    for response, code in cases:
        assert response.status_code == 404, (code, response.text)
        assert response.json()['code'] == code


def test_a_dangling_reference_in_stored_data_is_a_server_error_not_404(client):
    import conftest  # its contract wrapper rejects every 500; this test expects one, so it sends unchecked
    book = client.post('/api/demo').json()
    saved = stored(client, book['id'])
    saved['segments'][0]['chapter_id'] = 'chapter_that_does_not_exist'
    runtime(client).store.save_book(saved)
    response = conftest._send(client, client.build_request('GET', f"/api/books/{book['id']}"))
    assert response.status_code == 500
    assert response.json() == {'detail': 'The server hit an unexpected error.', 'code': 'internal_error'}


def test_edits_conflict_with_active_jobs_series_runs_and_archiving(client):
    book = client.post('/api/demo').json()
    rt = runtime(client)
    scene_url = f"/api/books/{book['id']}/scenes/{book['scenes'][0]['id']}"
    job = rt.store.create_job(book['id'], 'render')
    busy = client.patch(scene_url, json={'title': 'X'})
    assert busy.status_code == 409 and busy.json()['code'] == 'job_active'
    rt.store.update_job(job['id'], status='completed')
    series = rt.store.create_job('series-x', 'series')
    rt.store.update_job(series['id'], book_ids=[book['id']])
    reserved = client.post(f"/api/books/{book['id']}/pronunciations", json={'term': 'Mara', 'respelling': 'Marra'})
    assert reserved.status_code == 409 and reserved.json()['code'] == 'series_run_active'
    rt.store.update_job(series['id'], status='completed')
    assert client.post(f"/api/books/{book['id']}/archive").status_code == 200
    for response in (client.patch(scene_url, json={'title': 'X'}),
                     client.post(f"/api/books/{book['id']}/characters", json={'name': 'Wren'}),
                     client.post(f"/api/books/{book['id']}/repair-structure"),
                     client.post(f"/api/books/{book['id']}/pronunciations", json={'term': 'Mara', 'respelling': 'Marra'})):
        assert response.status_code == 409 and response.json()['code'] == 'book_archived', response.text
    assert stored(client, book['id'])['revision'] == book['revision']


def test_pronunciation_errors_have_codes(client):
    book = client.post('/api/demo').json()
    url = f"/api/books/{book['id']}/pronunciations"
    assert client.post(url, json={'term': 'Mara', 'respelling': 'Marra'}).status_code == 200
    duplicate = client.post(url, json={'term': 'Mara', 'respelling': 'Mahra'})
    assert duplicate.status_code == 400 and duplicate.json()['code'] == 'pronunciation_duplicate'
    invalid = client.post(url, json={'term': 'Elias', 'respelling': '(laugh)'})
    assert invalid.status_code == 400 and invalid.json()['code'] == 'pronunciation_invalid'
    outsider = client.post(url, json={'term': 'Elias', 'respelling': 'Eliass', 'character_id': 'nobody'})
    assert outsider.status_code == 400 and outsider.json()['code'] == 'character_not_in_cast'


# ------------------------------------------------------------------ repair-structure

def test_repair_structure_unknown_book_is_404_and_records_no_measurement(client):
    response = client.post('/api/books/no-such-book/repair-structure')
    assert response.status_code == 404
    assert response.json()['code'] == 'book_not_found'
    from bardic.resources import ResourceLedger
    store = runtime(client).store
    ResourceLedger(store)  # creates the (empty) table if no measurement was ever written
    with store.connect() as conn:
        rows = conn.execute("SELECT count(*) FROM resource_operations WHERE book_id='no-such-book'").fetchone()[0]
    assert rows == 0


def test_repair_structure_without_an_original_is_refused_with_a_code(client):
    book = client.post('/api/demo').json()
    response = client.post(f"/api/books/{book['id']}/repair-structure")
    assert response.status_code == 400
    assert response.json()['code'] == 'original_missing'


# ------------------------------------------------------------------ presentation

def test_presented_book_omits_internal_bookkeeping(client):
    book = client.post('/api/demo').json()
    mara_id, lines = dialogue(book, 'Mara')
    saved = stored(client, book['id'])
    mara = item(saved, 'characters', mara_id)
    mara.update(voice='Leda', system_voice='Samantha', profile_input_key='k', edited=True, edited_fields=['*'])
    mara.pop('voices', None)
    item(saved, 'scenes', book['scenes'][0]['id']).update(edited=True, edited_fields=['tone'])
    item(saved, 'segments', lines[0]['id']).update(edited=True, edited_fields=['speaker_id'])
    saved['metadata_edited'] = {'title': True}
    runtime(client).store.save_book(saved)
    presented = client.get(f"/api/books/{book['id']}").json()
    assert 'metadata_edited' not in presented
    character = item(presented, 'characters', mara_id)
    for name in ('voice', 'system_voice', 'profile_input_key', 'edited', 'edited_fields'):
        assert name not in character
    assert character['voices'] == {'gemini': {'id': 'Leda'}, 'system': {'id': 'Samantha'}}  # legacy folded in
    for collection in ('scenes', 'passages'):
        assert all('edited' not in x and 'edited_fields' not in x for x in presented[collection])
    # Storage keeps the bookkeeping.
    kept = stored(client, book['id'])
    assert kept['metadata_edited'] == {'title': True} and item(kept, 'characters', mara_id)['profile_input_key'] == 'k'
