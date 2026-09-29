"""Offline checks for artifact retention, projection lineage and legacy import."""
from copy import deepcopy
import json
import sqlite3

import pytest

from bardic.artifacts import ArtifactRepository, capture_book, initialize_schema, output_head, record
from bardic.importer import parse_book
from bardic.migrations import retain_legacy, unretained
from bardic.processing import ProcessingStore, source_hash
from bardic.store import Store
from classic_fixtures import add_classic_tables, classic_references


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


@pytest.fixture
def repository(store):
    return ArtifactRepository(store)


def save(repository, key='unit', payload=None, **kwargs):
    with repository.store.lock, repository.store.connect() as conn:
        return record(conn, 'book', 'analysis_output', key, payload or {'answer': 'one'},
                      stage='discovery', **kwargs)


def story():
    return parse_book('story.txt', b'Chapter 1\n\nMara said, "Wait."\n\nChapter 2\n\nThe door opened.')


def legacy_unit(store, book_id, key, source, value):
    """A row the removed Classic engine wrote, in a library from before the Classic data drop."""
    with store.connect() as conn:
        add_classic_tables(conn, units=[(book_id, key, value.get('stage', ''), source, value)])


def retain(store, book_id):
    """What the Classic data drop does for one book before deleting anything; returns the versions added."""
    repository = ArtifactRepository(store)
    before = repository.counts(book_id)['total']
    repository.backfill(book_id)
    with store.lock, store.connect() as conn:
        retain_legacy(conn, book_id, {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        assert unretained(conn, book_id, {'analysis_units', 'analysis_checkpoints', 'character_observations'}) == {}
    return repository.counts(book_id)['total'] - before


def test_content_identity_is_canonical_and_ignores_operational_time(repository):
    first = save(repository, payload={'answer': {'b': 2, 'a': 1}, 'created_at': 'yesterday'}, label='Original')
    again = save(repository, payload={'created_at': 'today', 'answer': {'a': 1, 'b': 2}}, label='Changed label')
    assert first == again
    assert repository.list('book')['total'] == 1
    item = repository.get('book', first)
    assert item['payload']['created_at'] == 'yesterday'
    assert item['label'] == 'Original'
    # Time within the story is substantive content.
    changed = save(repository, payload={'answer': {'a': 1, 'b': 2}, 'time': 'midnight'})
    assert changed != first


def test_same_recipe_new_output_retains_old_version_and_can_select_previous_content(repository):
    first = save(repository, payload={'answer': 'one'})
    second = save(repository, payload={'answer': 'two'})
    assert first != second
    assert repository.get('book', first)['is_current'] is False
    assert repository.get('book', second)['is_current'] is True
    assert save(repository, payload={'answer': 'one'}) == first
    assert repository.get('book', first)['is_current'] is True
    assert repository.get('book', second)['payload'] == {'answer': 'two'}
    assert repository.list('book')['total'] == 2


def test_producer_dependencies_and_scope_are_identity_inputs(repository):
    first = save(repository, key='source')
    second = save(repository, key='second-source')
    a = save(repository, dependencies=[first, second], provider='anthropic', model='model-a')
    b = save(repository, dependencies=[second, first, first], provider='anthropic', model='model-a')
    assert a == b
    assert save(repository, dependencies=[first], provider='anthropic', model='model-a') != a
    assert save(repository, dependencies=[first, second], provider='openai', model='model-a') != a
    assert save(repository, dependencies=[first, second], provider='anthropic', model='model-b') != a
    assert save(repository, key='other-unit', dependencies=[first, second], provider='anthropic', model='model-a') != a
    assert repository.get('book', a)['dependencies'] == sorted([first, second])


def test_dependencies_must_exist_and_cross_book_history_remains_scoped(repository):
    parent = save(repository, key='parent')
    with repository.store.connect() as conn:
        child = record(conn, 'sequel', 'analysis_input', 'profile', {'context': 'earlier volume'}, dependencies=[parent])
    assert repository.get('sequel', child)['dependencies'] == [parent]
    with pytest.raises(KeyError):
        repository.get('book', child)
    with pytest.raises(ValueError, match='does not exist'):
        save(repository, dependencies=['invented-unit-key'])
    assert repository.list('book')['total'] == 1


def test_history_is_immutable_even_with_direct_sql(repository):
    source = save(repository, key='source')
    child = save(repository, dependencies=[source])
    with repository.store.connect() as conn:
        for sql, args in [
            ('UPDATE artifact_versions SET payload=? WHERE id=?', ('{}', child)),
            ('DELETE FROM artifact_versions WHERE id=?', (child,)),
            ('DELETE FROM artifact_dependencies WHERE artifact_id=?', (child,)),
        ]:
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                conn.execute(sql, args)
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute('INSERT OR REPLACE INTO artifact_versions SELECT * FROM artifact_versions WHERE id=?', (child,))
    assert repository.get('book', child)['payload'] == {'answer': 'one'}


def test_record_joins_callers_transaction_and_rolls_back_on_publication_failure(repository):
    with pytest.raises(RuntimeError):
        with repository.store.connect() as conn:
            record(conn, 'book', 'source', 'chapter', {'text': 'Kept atomically.'})
            raise RuntimeError('Book publication failed')
    assert repository.list('book')['total'] == 0
    assert repository.counts('book')['current'] == 0


def test_paginated_metadata_filters_include_current_history_and_stage(repository):
    first = save(repository, payload={'answer': 'one'})
    second = save(repository, payload={'answer': 'two'})
    third = save(repository, key='other', payload={'answer': 'three'})
    with repository.store.connect() as conn:
        profile = record(conn, 'book', 'character_profile', 'mara', {'name': 'Mara'}, stage='profiles')
    history = repository.list('book', kind='analysis_output', stage='discovery', current=False, limit=1)
    assert history['total'] == 1 and history['items'][0]['id'] == first
    current = repository.list('book', stage='discovery', current=True, limit=1, offset=1)
    assert current['total'] == 2 and len(current['items']) == 1
    assert current['offset'] == 1 and current['limit'] == 1
    assert 'payload' not in current['items'][0]
    assert {i['id'] for i in repository.list('book', current=True)['items']} == {second, third, profile}
    assert repository.counts('book') == {'kinds': ['analysis_output', 'character_profile'],
                                        'stages': {'discovery': 3, 'profiles': 1}, 'total': 4, 'current': 3}


@pytest.mark.parametrize('kwargs', [{'limit': True}, {'offset': True}, {'current': 'yes'}])
def test_invalid_history_pages_are_rejected(repository, kwargs):
    with pytest.raises(ValueError):
        repository.list('book', **kwargs)


@pytest.mark.parametrize('kwargs,limit,offset', [({'limit': 0}, 1, 0), ({'limit': 201}, 200, 0), ({'offset': -1}, 30, 0)])
def test_out_of_range_history_pages_are_clamped(repository, kwargs, limit, offset):
    page = repository.list('book', **kwargs)
    assert (page['limit'], page['offset']) == (limit, offset)


def test_capture_keeps_exact_source_and_portable_passage_scene_edges(repository):
    book = story()
    original = deepcopy(book)
    with repository.store.connect() as conn:
        heads = capture_book(conn, book)
    assert book == original
    for chapter in book['chapters']:
        source = repository.get(book['id'], heads['source'][chapter['id']])
        assert source['payload']['text'] == chapter['text']
        scene_map = repository.get(book['id'], heads['scene_map'][chapter['id']])
        assert scene_map['dependencies'] == [source['id']]
        assert [s['id'] for s in scene_map['payload']['scenes']] == [s['id'] for s in book['scenes'] if s['chapter_id'] == chapter['id']]
        for passage in scene_map['payload']['passages']:
            segment = next(s for s in book['segments'] if s['id'] == passage['id'])
            anchor = passage['source_anchor']
            assert chapter['text'][anchor['start']:anchor['end']] == segment['text']
            assert anchor['artifact_id'] == source['id']
            assert passage['scene_id'] == segment['scene_id']
            assert 'text' not in passage and 'audio' not in passage


def test_title_repairs_and_voice_changes_do_not_duplicate_source_or_profiles(repository):
    book = story()
    with repository.store.connect() as conn:
        first = capture_book(conn, book)
        book['chapters'][0]['title'] = 'Corrected chapter name'
        book['characters'][0]['voice'] = 'Puck'
        second = capture_book(conn, book)
    assert first['source'] == second['source']
    assert first['character_profile'] == second['character_profile']
    assert first['structure'] != second['structure']
    assert first['voice_assignment']['narrator'] != second['voice_assignment']['narrator']


def test_removed_scenes_characters_chapters_and_takes_lose_heads_but_not_history(repository):
    book = story()
    book['characters'].append({'id': 'mara', 'name': 'Mara', 'voice': 'Kore'})
    book['segments'][0]['audio'] = {'fingerprint': 'old-audio', 'provider': 'system', 'model': 'say', 'duration': 2}
    with repository.store.connect() as conn:
        before = capture_book(conn, book)
        book['characters'].pop()
        removed = book['chapters'].pop()
        book['segments'] = [s for s in book['segments'] if s['chapter_id'] != removed['id']]
        book['scenes'] = []
        book['segments'][0]['audio'] = None
        capture_book(conn, book)
    for kind, key in [('source', removed['id']), ('scene_map', removed['id']), ('character_profile', 'mara'),
                      ('voice_assignment', 'mara'), ('audio_take', book['segments'][0]['id'])]:
        identifier = before[kind][key]
        assert repository.output_head(book['id'], kind, key) is None
        assert repository.get(book['id'], identifier)['is_current'] is False
    active_map = repository.get(book['id'], repository.output_head(book['id'], 'scene_map', book['chapters'][0]['id']))
    assert active_map['payload']['scenes'] == []


def test_minimal_books_and_unverified_anchors_are_retained_without_false_provenance(repository):
    book = {'id': 'minimal', 'title': 'Minimal'}
    with repository.store.connect() as conn:
        heads = capture_book(conn, book)
    assert heads['source'] == {} and heads['structure']['book']
    book.update(chapters=[{'id': 'chapter', 'text': 'Source'}],
                segments=[{'id': 'passage', 'chapter_id': 'chapter', 'start': 0, 'end': 999, 'text': 'Different'}])
    with repository.store.connect() as conn:
        heads = capture_book(conn, book)
    scene_map = repository.get('minimal', heads['scene_map']['chapter'])
    assert scene_map['payload']['passages'][0]['source_anchor'] is None


def test_profile_dependency_requires_matching_saved_output_and_never_guesses_for_reviewed_edits(repository):
    book = story()
    character = {'id': 'mara', 'name': 'Mara', 'aliases': [], 'description': 'Grounded profile', 'direction': 'Calm', 'profile_input_key': 'profile-unit'}
    book['characters'].append(character)
    with repository.store.connect() as conn:
        produced = record(conn, book['id'], 'analysis_output', 'profile-unit', {'result': {'characters': [character]}}, stage='profiles')
        first = capture_book(conn, book)
        character['description'] = 'Human correction'
        character['edited'] = True
        second = capture_book(conn, book)
    assert repository.get(book['id'], first['character_profile']['mara'])['dependencies'] == [produced]
    assert repository.get(book['id'], second['character_profile']['mara'])['dependencies'] == []


def test_backfill_is_idempotent_and_does_not_export_settings_or_change_live_heads(repository):
    book = story()
    store = repository.store
    store.save_book(book)
    store.save_settings({'api_key': 'must-not-be-exported', 'analysis_model': 'private-preference'})
    ProcessingStore(store)  # the shared census cache table
    chapter = book['chapters'][0]
    unit = {'unit_key': 'discovery-key', 'stage': 'discovery', 'chapter_id': chapter['id'], 'start': 0,
            'end': len(chapter['text']), 'provider': 'anthropic', 'model': 'known-model', 'result': {'characters': []}}
    legacy_unit(store, book['id'], 'discovery-key', source_hash(book), unit)
    with store.connect() as conn:
        conn.execute('INSERT INTO book_preprocessing VALUES (?,?,?)', (book['id'], 'census-key', '{"words":12}'))
    with store.connect() as conn:
        current = record(conn, book['id'], 'character_profile', 'narrator', {'name': 'Preserved live profile'}, stage='profiles')
    first = retain(store, book['id'])
    count = repository.counts(book['id'])['total']
    second = retain(store, book['id'])
    assert first > 0 and second == 0
    assert repository.counts(book['id'])['total'] == count
    assert repository.output_head(book['id'], 'character_profile', 'narrator') == current
    rows = repository.list(book['id'], limit=200)['items']
    serialized = json.dumps([repository.get(book['id'], r['id']) for r in rows])
    assert 'must-not-be-exported' not in serialized and 'private-preference' not in serialized
    output = repository.get(book['id'], repository.output_head(book['id'], 'analysis_output', 'discovery-key'))
    assert output['legacy_provenance'] is True
    assert output['provider'] == 'anthropic' and output['model'] == 'known-model'
    assert len(output['dependencies']) == 1


def test_backfill_keeps_legacy_checkpoint_units_and_observations_with_verified_spans(repository):
    book = story()
    store = repository.store
    store.save_book(book)
    chapter = book['chapters'][0]
    unit = {'stage': 'discovery', 'chapter_id': chapter['id'], 'start': 0, 'end': len(chapter['text']), 'result': {'characters': []}}
    checkpoint = {'provider': 'anthropic', 'model': 'old-model', 'working_book': book, 'units': {'legacy-unit': unit},
                  'chapters': [], 'status': 'failed'}
    from bardic.series import source_hash as text_hash
    observation = {'id': 'observation', 'book_id': book['id'], 'character_id': 'mara', 'chapter_id': chapter['id'],
                   'start': 0, 'end': 7, 'quote': chapter['text'][:7],
                   'source_hash': text_hash(chapter['text']), 'provider': 'anthropic', 'model': 'old-model'}
    with store.connect() as conn:
        add_classic_tables(conn, checkpoints=[(book['id'], 'old', checkpoint)], observations=[observation])
    retain(store, book['id'])
    output = repository.get(book['id'], repository.output_head(book['id'], 'analysis_output', 'legacy-unit'))
    recorded = repository.get(book['id'], repository.output_head(book['id'], 'character_observation', 'observation'))
    assert output['provider'] == 'anthropic' and len(output['dependencies']) == 1
    assert recorded['payload']['quote'] == chapter['text'][:7] and len(recorded['dependencies']) == 1
    # The whole checkpoint is retained as it was stored, without claimed inputs.
    whole = repository.get(book['id'], repository.output_head(book['id'], 'analysis_checkpoint', 'book'))
    assert whole['payload'] == checkpoint and whole['dependencies'] == [] and whole['legacy_provenance'] is True


def test_backfill_does_not_link_old_source_output_to_changed_current_source(repository):
    book = story()
    store = repository.store
    store.save_book(book)
    chapter = book['chapters'][0]
    legacy_unit(store, book['id'], 'older', 'different-source-hash',
        {'stage': 'discovery', 'chapter_id': chapter['id'], 'start': 0, 'end': len(chapter['text']), 'result': {'characters': []}})
    retain(store, book['id'])
    artifact = repository.get(book['id'], repository.output_head(book['id'], 'analysis_output', 'older'))
    assert artifact['dependencies'] == [] and artifact['legacy_provenance'] is True


def test_backfill_recipe_is_separate_from_output_and_history_is_book_scoped(repository):
    book = story()
    repository.store.save_book(book)
    chapter = book['chapters'][0]
    recipe = {'prompt': 'A known saved prompt', 'schema': {'type': 'object'}}
    legacy_unit(repository.store, book['id'], 'with-recipe', source_hash(book),
        {'stage': 'discovery', 'chapter_id': chapter['id'], 'start': 0, 'end': len(chapter['text']),
         'input_recipe': recipe, 'result': {'characters': []}})
    retain(repository.store, book['id'])
    output = repository.get(book['id'], repository.output_head(book['id'], 'analysis_output', 'with-recipe'))
    input_id = repository.output_head(book['id'], 'analysis_input', 'with-recipe')
    assert input_id in output['dependencies']
    assert 'input_recipe' not in output['payload']
    assert repository.get(book['id'], input_id)['payload'] == recipe
    with pytest.raises(KeyError, match='Book not found'):
        repository.backfill('missing')
    assert repository.list('missing')['total'] == 0


def test_standalone_schema_and_lookup_helpers_work_without_repository_construction():
    with sqlite3.connect(':memory:') as conn:
        initialize_schema(conn)
        initialize_schema(conn)
        identifier = record(conn, 'book', 'source', 'chapter', {'text': 'Source'})
        assert output_head(conn, 'book', 'source', 'chapter') == identifier
        assert output_head(conn, 'book', 'source', 'missing') is None


def test_store_initializes_schema_enforces_dependencies_and_retains_prior_legacy_projection(store):
    original = story()
    with store.connect() as conn:
        assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        conn.execute('INSERT INTO books VALUES (?,?)', (original['id'], json.dumps(original)))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('INSERT INTO artifact_heads VALUES (?,?,?,?,?)', ('book', 'source', 'chapter', 'missing', 'now'))
    changed = deepcopy(original)
    changed['characters'][0]['description'] = 'Reviewed narrator profile'
    changed['characters'][0]['edited'] = True
    store.save_book(changed)
    repository = ArtifactRepository(store)
    versions = repository.list(changed['id'], kind='character_profile')['items']
    narrator = [repository.get(changed['id'], v['id']) for v in versions if v['logical_key'] == 'narrator']
    assert len(narrator) == 2
    assert {v['legacy_provenance'] for v in narrator} == {True, False}
    assert next(v for v in narrator if v['is_current'])['payload']['description'] == 'Reviewed narrator profile'
    assert store.book(original['id'])['chapters'] == original['chapters']


def test_take_hook_preserves_previous_take_and_does_not_reversion_unrelated_projections(store):
    book = story()
    store.save_book(book)
    repository = ArtifactRepository(store)
    segment_id = book['segments'][0]['id']
    before = repository.counts(book['id'])['total']
    old = {'fingerprint': 'old', 'duration': 1.5, 'provider': 'system', 'file': 'old.wav'}
    new = {'fingerprint': 'new', 'duration': 2.0, 'provider': 'system', 'file': 'new.wav'}
    # A pre-upgrade take exists only in the mutable takes table.
    with store.connect() as conn:
        conn.execute('INSERT INTO takes VALUES (?,?,?)', (book['id'], segment_id, json.dumps(old)))
    store.save_take(book['id'], segment_id, new)
    assert repository.counts(book['id'])['total'] == before + 2
    versions = [repository.get(book['id'], v['id']) for v in repository.list(book['id'], kind='audio_take')['items']]
    assert len(versions) == 2 and all(len(v['dependencies']) == 1 for v in versions)
    assert next(v for v in versions if not v['is_current'])['payload']['audio'] == old
    assert store.book(book['id'])['segments'][0]['audio'] == new
    store.save_take(book['id'], segment_id, new)
    assert repository.counts(book['id'])['total'] == before + 2
    changed = store.book(book['id'])
    changed['segments'][0]['audio'] = None
    store.save_book(changed)
    assert repository.output_head(book['id'], 'audio_take', segment_id) is None
    assert repository.list(book['id'], kind='audio_take')['total'] == 2


def test_classic_observations_have_exact_source_dependencies_and_survive_the_row_deletion(store):
    book = story()
    book['characters'].append({'id': 'mara', 'name': 'Mara'})
    store.save_book(book)
    chapter = book['chapters'][0]
    start = chapter['text'].index('Mara')
    reference = {'id': 'mention', 'character_id': 'mara', 'chapter_id': chapter['id'], 'start': start,
                 'end': start + 4, 'quote': 'Mara', 'kind': 'mention', 'provider': 'anthropic', 'model': 'known-model'}
    # Retained with the row, then the row deleted, as the Classic data drop leaves it; twice changes nothing.
    classic_references(store, book['id'], [reference])
    repository = ArtifactRepository(store)
    first = repository.list(book['id'], kind='character_observation')['items']
    assert len(first) == 1 and len(first[0]['dependencies']) == 1
    classic_references(store, book['id'], [reference])
    with store.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM character_observations').fetchone()[0] == 0
    assert repository.list(book['id'], kind='character_observation')['total'] == 1
    artifact = repository.get(book['id'], first[0]['id'])
    source = repository.get(book['id'], artifact['dependencies'][0])
    assert source['payload']['text'][start:start + 4] == artifact['payload']['quote']


def test_series_link_and_membership_history_survives_explicit_unlink(store):
    from bardic.series import SeriesRepository
    book = story()
    book['characters'].append({'id': 'mara', 'name': 'Mara'})
    store.save_book(book)
    series = SeriesRepository(store)
    saga = series.create_series('The Lantern Books')
    identity = series.create_character(saga['id'], 'Mara')
    series.set_membership(book['id'], saga['id'], 9)
    series.link_character(book['id'], 'mara', identity['id'])
    repository = ArtifactRepository(store)
    linked = repository.output_head(book['id'], 'series_context', 'book')
    series.unlink_character(book['id'], 'mara')
    series.set_membership(book['id'])
    current = repository.get(book['id'], repository.output_head(book['id'], 'series_context', 'book'))
    assert current['payload'] == {'book_id': book['id'], 'membership': None, 'links': []}
    prior = repository.get(book['id'], linked)
    assert prior['is_current'] is False and prior['payload']['links'][0]['series_character_id'] == identity['id']
    assert prior['payload']['membership']['position'] == 9
