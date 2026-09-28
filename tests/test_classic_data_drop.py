"""The one-time Classic data drop (Classic removal, stage 4; bardic.migrations).

A synthetic library from before the drop has the removed engine's tables and
rows. Starting the app retains them as artifacts, drops the tables, deletes the
observation rows and records the migration, once. Offline, tmp_path, synthetic prose.
"""
import json
import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient

from bardic import migrations
from bardic.app import create_app
from bardic.artifacts import ArtifactRepository
from bardic.importer import parse_book
from bardic.migrations import CLASSIC_REMOVAL, migration, remove_classic_data
from bardic.series import SeriesRepository, observation_of, source_hash
from bardic.store import Store
from classic_fixtures import add_classic_tables, write_references

TEXT = 'Chapter 1\n\nMara said, "Keep the lamp lit." Mara waited by the gate.'
KEPT = ('analysis_attempts', 'pipeline_events', 'book_preprocessing', 'character_references', 'artifact_versions',
        'artifact_dependencies', 'pipeline_state', 'series_character_links')


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for name in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr('bardic.app.list_system_voices', lambda: [])


def volume(name, text=TEXT):
    book = parse_book(f'{name}.txt', text.encode())
    book['id'] = name
    book['title'] = f'Volume {name}'
    book['characters'].append({'id': 'mara', 'name': 'Mara', 'aliases': [], 'description': 'A lamplighter.',
                               'direction': 'Low and steady.'})
    return book


def ref(book, quote, kind, **fields):
    chapter = book['chapters'][0]
    start = chapter['text'].index(quote)
    return {'id': f'{kind}-{start}', 'character_id': 'mara', 'chapter_id': chapter['id'], 'segment_id': None,
            'start': start, 'end': start + len(quote), 'quote': quote, 'kind': kind, 'confidence': .8,
            'provider': 'anthropic', 'model': 'classic-model', **fields}


def observed(book, reference):
    observation = observation_of(book['id'], reference, source_hash(book['chapters'][0]['text']))
    return {**observation, 'recorded_at': '2026-01-01T00:00:00+00:00'}


def counts(path, tables=KEPT):
    with sqlite3.connect(path / 'library.sqlite3') as conn:
        present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {name: conn.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0] for name in tables if name in present}


def table_names(path):
    with sqlite3.connect(path / 'library.sqlite3') as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def classic_library(path):
    """Two linked volumes and an archived book analysed by the Classic engine, plus one orphaned unit."""
    store = Store(path)
    one, two, three = volume('one'), volume('two'), volume('three')
    for book in (one, two, three):
        store.save_book(book)
    series = SeriesRepository(store)
    saga = series.create_series('The Lamp Books')
    series.set_membership('one', saga['id'], 1)
    series.set_membership('two', saga['id'], 2)
    identity = series.create_character(saga['id'], 'Mara')
    for book_id in ('one', 'two'):
        series.link_character(book_id, 'mara', identity['id'])
    evidence = ref(one, 'Mara waited by the gate.', 'profile_evidence', profile_description='Patient.',
                   profile_direction='Unhurried.')
    dialogue = ref(one, '"Keep the lamp lit."', 'dialogue')
    mention = ref(one, 'Mara', 'mention')
    # A row whose observation was never retained: nothing proves its source, so series context never reads it.
    unproven = ref(one, 'the gate', 'profile_evidence', profile_description='Unproven.')
    chapter = one['chapters'][0]
    recipe = {'prompt': 'A synthetic saved prompt.', 'schema': {'type': 'object'}}
    unit = {'unit_key': 'discovery:one', 'stage': 'discovery', 'chapter_id': chapter['id'], 'start': 0,
            'end': len(chapter['text']), 'provider': 'anthropic', 'model': 'classic-model', 'input_recipe': recipe,
            'result': {'characters': [{'name': 'Mara'}]}}
    checkpoint = {'provider': 'anthropic', 'model': 'classic-model', 'status': 'completed', 'stage': 'directing',
                  'working_book': one, 'chapters': [{'id': chapter['id'], 'title': 'Chapter 1', 'status': 'completed'}],
                  'units': {'profiles:mara': {'stage': 'profiles', 'chapter_id': chapter['id'], 'start': 0,
                                              'end': len(chapter['text']), 'result': {'characters': []}}},
                  'references': [evidence, dialogue, mention]}
    archived_checkpoint = {'provider': 'openai', 'model': 'old', 'status': 'interrupted', 'units': {}, 'chapters': []}
    three_evidence = ref(three, 'Mara waited by the gate.', 'profile_evidence')
    from bardic.processing import source_hash as book_hash
    with store.connect() as conn:
        write_references(conn, 'one', [evidence, dialogue, mention, unproven])
        write_references(conn, 'three', [three_evidence])
        add_classic_tables(
            conn,
            units=[('one', 'discovery:one', 'discovery', book_hash(one), unit),
                   ('gone', 'discovery:gone', 'discovery', 'unknown', {'stage': 'discovery', 'result': {}})],
            checkpoints=[('one', 'classic-fingerprint', checkpoint), ('three', 'older', archived_checkpoint)],
            observations=[observed(one, evidence), observed(one, dialogue), observed(one, mention),
                          observed(three, three_evidence)])
        conn.execute('INSERT INTO library_archives VALUES (?,?,?)', ('book', 'three', '2026-02-01T00:00:00+00:00'))
        attempt = {'id': 'attempt-1', 'book_id': 'one', 'run_id': 'classic-run', 'stage': 'discovery', 'unit_key': 'discovery:one',
                   'provider': 'anthropic', 'model': 'classic-model', 'status': 'received', 'created_at': '2026-01-01T00:00:00+00:00'}
        event = {'id': 'e' * 32, 'book_id': 'one', 'run_id': 'classic-run', 'stage': 'discovery', 'unit_key': 'discovery:one',
                 'event': 'accepted', 'attempt_id': 'attempt-1', 'created_at': '2026-01-01T00:00:01+00:00'}
        conn.execute('INSERT INTO analysis_attempts VALUES (?,?,?,?)', ('attempt-1', 'one', 'classic-run', json.dumps(attempt)))
        conn.execute('INSERT INTO pipeline_events VALUES (?,?,?,?,?,?)', (event['id'], 'one', 'classic-run', 'discovery:one',
                                                                         'discovery', json.dumps(event)))
        conn.execute('INSERT INTO book_preprocessing VALUES (?,?,?)', ('one', 'census', '{"words":12}'))
    return {'one': one, 'evidence': evidence, 'dialogue': dialogue, 'mention': mention, 'unproven': unproven,
            'checkpoint': checkpoint, 'recipe': recipe, 'observations': [observed(one, r) for r in (evidence, dialogue, mention)]}


def started(path):
    """Start and stop the app once, as a server restart does."""
    with TestClient(create_app(path)):
        pass


def head(store, book_id, kind, key):
    return ArtifactRepository(store).output_head(book_id, kind, key)


def test_starting_a_classic_library_retains_then_drops_the_legacy_data(tmp_path, caplog):
    data = classic_library(tmp_path)
    before = counts(tmp_path)
    caplog.set_level(logging.INFO, logger='bardic.migrations')
    with TestClient(create_app(tmp_path)) as client:
        store = client.app.state.runtime.store
        repository = ArtifactRepository(store)
        # Retained before anything was dropped: the unit with its recipe, the whole checkpoint and its
        # units, every observation (with the verified source span), and the orphaned unit of a missing book.
        output = repository.get('one', head(store, 'one', 'analysis_output', 'discovery:one'))
        recipe = head(store, 'one', 'analysis_input', 'discovery:one')
        assert output['legacy_provenance'] is True and recipe in output['dependencies']
        assert repository.get('one', recipe)['payload'] == data['recipe']
        assert head(store, 'one', 'analysis_output', 'profiles:mara')
        checkpoint = repository.get('one', head(store, 'one', 'analysis_checkpoint', 'book'))
        assert checkpoint['payload'] == json.loads(json.dumps(data['checkpoint'])) and checkpoint['dependencies'] == []
        for observation in data['observations']:
            retained = repository.get('one', head(store, 'one', 'character_observation', observation['id']))
            assert retained['payload'] == observation and len(retained['dependencies']) == 1
        assert head(store, 'three', 'analysis_checkpoint', 'book')  # archived books too
        assert repository.get('gone', head(store, 'gone', 'analysis_output', 'discovery:gone'))['dependencies'] == []

        # Dropped and deleted; everything else kept.
        assert not {'analysis_units', 'analysis_checkpoints'} & table_names(tmp_path)
        assert counts(tmp_path, ('character_observations',)) == {'character_observations': 0}
        after = counts(tmp_path)
        assert after['artifact_versions'] > before['artifact_versions']
        assert {k: v for k, v in after.items() if k not in {'artifact_versions', 'artifact_dependencies', 'pipeline_state'}} == \
               {k: v for k, v in before.items() if k not in {'artifact_versions', 'artifact_dependencies', 'pipeline_state'}}

        # The recorded migration, with per-book counts.
        record = migration(store, CLASSIC_REMOVAL)
        assert record['status'] == 'completed' and record['dropped_tables'] == ['analysis_units', 'analysis_checkpoints']
        assert record['deleted_observations'] == 4
        assert record['books']['one'] | {'artifacts_added': 0} == {
            'book': True, 'analysis_units': 1, 'analysis_checkpoints': 1, 'checkpoint_units': 1,
            'character_observations': 3, 'classic_references': 4, 'classic_references_in_series_context': 2,
            'artifacts_added': 0}
        assert record['books']['gone']['book'] is False and record['books']['three']['analysis_checkpoints'] == 1
        assert record['classic_references'] == 5

        # The app still works: the book opens, Cast references carry the Classic evidence, and
        # the later volume's series context reads the earlier volume's proven Classic rows.
        assert client.get('/api/books/one').status_code == 200
        references = client.get('/api/books/one/characters/mara/references')
        assert references.status_code == 200, references.text
        assert data['evidence']['id'] in {r['id'] for r in references.json()}
        context = client.get('/api/books/two/series/context')
        assert context.status_code == 200, context.text
        read = [o for c in context.json()['characters'] for o in c['observations']]
        assert sorted((o['kind'], o['quote']) for o in read) == [
            ('dialogue', '"Keep the lamp lit."'), ('profile_evidence', 'Mara waited by the gate.')]
        assert all(o['version_id'] is None and o['step'] is None for o in read)  # no provenance was invented
        assert client.get('/api/books/one/pipeline').status_code == 200
        assert client.get('/api/library').status_code == 200
    messages = [r.getMessage() for r in caplog.records if r.name == 'bardic.migrations']
    assert messages[0].startswith('Classic removal (classic_removal_v1): 3 book(s) in the library; legacy rows: '
                                  'analysis_units 2, analysis_checkpoints 2 (1 checkpoint units), character_observations 4, '
                                  'in 3 book(s).')
    assert messages[-1].startswith('Classic removal: done. Dropped tables: analysis_units, analysis_checkpoints. '
                                   'Deleted 4 character_observations row(s)')


def test_a_second_start_is_a_no_op(tmp_path):
    classic_library(tmp_path)
    started(tmp_path)
    store = Store(tmp_path)
    first = migration(store, CLASSIC_REMOVAL)
    before = counts(tmp_path)
    assert remove_classic_data(store) is None
    started(tmp_path)
    assert migration(store, CLASSIC_REMOVAL) == first
    assert counts(tmp_path) == before


def test_a_failed_retention_drops_nothing_and_the_next_start_completes(tmp_path, monkeypatch, caplog):
    classic_library(tmp_path)
    before = counts(tmp_path, KEPT + ('character_observations', 'analysis_units', 'analysis_checkpoints'))
    real = migrations.retain_legacy

    def failing(conn, book_id, tables):
        if book_id == 'three':
            raise ValueError('synthetic unreadable legacy row')
        return real(conn, book_id, tables)

    monkeypatch.setattr(migrations, 'retain_legacy', failing)
    caplog.set_level(logging.INFO, logger='bardic.migrations')
    started(tmp_path)
    after = counts(tmp_path, KEPT + ('character_observations', 'analysis_units', 'analysis_checkpoints'))
    assert {'analysis_units', 'analysis_checkpoints'} <= table_names(tmp_path)
    assert {k: after[k] for k in ('character_observations', 'analysis_units', 'analysis_checkpoints', 'character_references')} == \
           {k: before[k] for k in ('character_observations', 'analysis_units', 'analysis_checkpoints', 'character_references')}
    record = migration(Store(tmp_path), CLASSIC_REMOVAL)
    assert record['status'] == 'failed' and 'synthetic unreadable legacy row' in record['failures']['three']
    assert any('Classic removal postponed' in r.getMessage() and r.levelno == logging.ERROR for r in caplog.records)

    monkeypatch.setattr(migrations, 'retain_legacy', real)
    retained = counts(tmp_path, ('artifact_versions',))
    started(tmp_path)
    assert migration(Store(tmp_path), CLASSIC_REMOVAL)['status'] == 'completed'
    assert not {'analysis_units', 'analysis_checkpoints'} & table_names(tmp_path)
    # Book one was retained on the failed start; the retry added only what was missing (book three's rows).
    added = counts(tmp_path, ('artifact_versions',))['artifact_versions'] - retained['artifact_versions']
    assert 0 < added <= 3


def test_two_disagreeing_copies_of_a_unit_are_both_retained_without_changing_the_selection(tmp_path):
    store = Store(tmp_path)
    book = volume('solo')
    store.save_book(book)
    chapter = book['chapters'][0]
    cached = {'stage': 'discovery', 'chapter_id': chapter['id'], 'start': 0, 'end': 5, 'provider': 'anthropic',
              'model': 'classic-model', 'result': {'characters': [{'name': 'Mara'}]}}
    saved = {**cached, 'result': {'characters': [{'name': 'Mara'}, {'name': 'Tom'}]}}
    with store.connect() as conn:
        add_classic_tables(conn, units=[('solo', 'discovery:same', 'discovery', 'old', cached)],
                           checkpoints=[('solo', 'fp', {'provider': 'anthropic', 'model': 'classic-model',
                                                         'units': {'discovery:same': saved}})])
    assert remove_classic_data(store)['status'] == 'completed'
    repository = ArtifactRepository(store)
    versions = repository.list('solo', kind='analysis_output')['items']
    payloads = {json.dumps(repository.get('solo', v['id'])['payload']['result'], sort_keys=True): v['is_current']
                for v in versions}
    assert payloads == {json.dumps(cached['result'], sort_keys=True): True,
                        json.dumps(saved['result'], sort_keys=True): False}


def test_an_unreadable_legacy_row_postpones_the_drop_without_stopping_the_server(tmp_path):
    classic_library(tmp_path)
    with sqlite3.connect(tmp_path / 'library.sqlite3') as conn:
        conn.execute("UPDATE analysis_checkpoints SET body='{not json' WHERE book_id='three'")
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/api/books/one').status_code == 200
        record = migration(client.app.state.runtime.store, CLASSIC_REMOVAL)
    assert record['status'] == 'failed' and set(record['failures']) == {'three'}
    assert {'analysis_units', 'analysis_checkpoints'} <= table_names(tmp_path)
    assert counts(tmp_path, ('character_observations',)) == {'character_observations': 4}


def test_an_interrupted_drop_resumes_without_losing_or_double_archiving(tmp_path, monkeypatch):
    classic_library(tmp_path)
    real = migrations._record

    def interrupted(conn, status, body):
        if status == 'completed':  # after the deletes and DROP statements, inside their transaction
            raise KeyboardInterrupt('synthetic power loss inside the drop transaction')
        return real(conn, status, body)

    monkeypatch.setattr(migrations, '_record', interrupted)
    with pytest.raises(KeyboardInterrupt):
        remove_classic_data(Store(tmp_path))
    # Everything destructive rolled back together; the retained artifacts stay.
    assert {'analysis_units', 'analysis_checkpoints'} <= table_names(tmp_path)
    assert counts(tmp_path, ('character_observations',)) == {'character_observations': 4}
    assert migration(Store(tmp_path), CLASSIC_REMOVAL) is None
    versions = counts(tmp_path, ('artifact_versions',))

    monkeypatch.setattr(migrations, '_record', real)
    with TestClient(create_app(tmp_path)) as client:
        store = client.app.state.runtime.store
        record = migration(store, CLASSIC_REMOVAL)
        assert record['status'] == 'completed' and record['deleted_observations'] == 4
        # Nothing was archived twice; the proof of the Classic rows is the retained artifacts.
        assert counts(tmp_path, ('artifact_versions',)) == versions
        read = client.get('/api/books/two/series/context').json()
        assert sum(len(c['observations']) for c in read['characters']) == 2


def test_a_row_without_a_book_id_stops_the_drop(tmp_path):
    classic_library(tmp_path)
    with sqlite3.connect(tmp_path / 'library.sqlite3') as conn:
        conn.execute('INSERT INTO analysis_units VALUES (NULL,?,?,?,?)', ('discovery:nobody', 'discovery', 'x', '{"result":{}}'))
    started(tmp_path)
    record = migration(Store(tmp_path), CLASSIC_REMOVAL)
    assert record['status'] == 'failed' and 'None' in record['failures']
    assert {'analysis_units', 'analysis_checkpoints'} <= table_names(tmp_path)
    assert counts(tmp_path, ('character_observations', 'analysis_units')) == {'character_observations': 4, 'analysis_units': 3}


def test_a_failing_reference_count_does_not_block_the_drop(tmp_path, monkeypatch):
    classic_library(tmp_path)

    def broken(conn):
        raise KeyError('text')

    monkeypatch.setattr(migrations, '_classic_references', broken)
    result = remove_classic_data(Store(tmp_path))
    assert result['status'] == 'completed' and result['classic_references'] is None
    assert not {'analysis_units', 'analysis_checkpoints'} & table_names(tmp_path)
    assert migration(Store(tmp_path), CLASSIC_REMOVAL)['classic_references'] is None


def test_a_rerun_keeps_the_earlier_completed_record(tmp_path):
    classic_library(tmp_path)
    started(tmp_path)
    first = migration(Store(tmp_path), CLASSIC_REMOVAL)
    # A Classic table reappears (for example after running an older build): it is migrated again.
    with sqlite3.connect(tmp_path / 'library.sqlite3') as conn:
        add_classic_tables(conn)
    started(tmp_path)
    second = migration(Store(tmp_path), CLASSIC_REMOVAL)
    assert second['status'] == 'completed' and second['deleted_observations'] == 0
    assert not {'analysis_units', 'analysis_checkpoints'} & table_names(tmp_path)
    earlier = second['earlier_runs']
    assert len(earlier) == 1 and earlier[0]['deleted_observations'] == 4
    assert earlier[0]['books'] == first['books']


def test_a_library_like_the_owners_migrates_cleanly(tmp_path, caplog):
    """One book analysed only by the step pipeline, empty Classic tables and many attempts."""
    store = Store(tmp_path)
    book = volume('solo')
    store.save_book(book)
    projected = {**ref(book, 'Mara waited by the gate.', 'profile_evidence'), 'projection': 1, 'step': 'discovery',
                 'version_id': None, 'origin': 'run'}
    with store.connect() as conn:
        add_classic_tables(conn)
        write_references(conn, 'solo', [projected])
        conn.executemany('INSERT INTO analysis_attempts VALUES (?,?,?,?)',
                         [(f'attempt-{i}', 'solo', 'run', '{}') for i in range(25)])
    before = counts(tmp_path)
    caplog.set_level(logging.INFO, logger='bardic.migrations')
    started(tmp_path)
    assert not {'analysis_units', 'analysis_checkpoints'} & table_names(tmp_path)
    after = counts(tmp_path)
    assert after['analysis_attempts'] == 25 and after['character_references'] == before['character_references'] == 1
    record = migration(Store(tmp_path), CLASSIC_REMOVAL)
    assert record['status'] == 'completed' and record['books'] == {} and record['deleted_observations'] == 0
    assert record['classic_references'] == 0 and record['dropped_tables'] == ['analysis_units', 'analysis_checkpoints']
    assert [r.getMessage() for r in caplog.records if r.name == 'bardic.migrations'] == [
        'Classic removal (classic_removal_v1): 1 book(s) in the library; legacy rows: analysis_units 0, '
        'analysis_checkpoints 0 (0 checkpoint units), character_observations 0, in 0 book(s). '
        'Tables present: analysis_units, analysis_checkpoints.',
        'Classic removal: done. Dropped tables: analysis_units, analysis_checkpoints. Deleted 0 character_observations '
        'row(s) (the table is kept; their history is in character_observation artifacts). Kept 0 Classic-written '
        'character_references row(s), 0 of them in series context. Recorded as classic_removal_v1 in schema_migrations.']


def test_a_new_library_records_the_migration_and_never_has_the_tables(tmp_path):
    started(tmp_path)
    started(tmp_path)
    assert not {'analysis_units', 'analysis_checkpoints'} & table_names(tmp_path)
    assert 'character_observations' in table_names(tmp_path)
    record = migration(Store(tmp_path), CLASSIC_REMOVAL)
    assert record['status'] == 'completed' and record['dropped_tables'] == [] and record['book_count'] == 0
