"""Source-anchored lexical retrieval stays local and within reading scope."""
from copy import deepcopy
import hashlib
import math
import sqlite3

import httpx
import pytest

from spintails.search import search
from spintails.series import SeriesRepository
from spintails.store import Store


def book(identifier, passages):
    text, segments = '', []
    for index, passage in enumerate(passages):
        start = len(text)
        text += passage + '\n\n'
        segments.append({'id': f'passage-{index}', 'chapter_id': 'chapter-one',
                         'start': start, 'end': start + len(passage), 'text': passage})
    return {'id': identifier, 'title': f'Volume {identifier}', 'author': 'Test',
            'chapters': [{'id': 'chapter-one', 'title': 'At the gate', 'text': text}],
            'segments': segments, 'characters': [], 'scenes': []}


@pytest.fixture
def store(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail('Lexical search must not contact a provider or external service')
    monkeypatch.setattr(httpx.Client, 'request', no_network)
    monkeypatch.setattr(httpx, 'request', no_network)
    return Store(tmp_path)


def test_series_scope_filters_before_limit_and_excludes_other_namesakes(store):
    books = [book('one', ['Mara waited at the old quiet gate beside the little lantern.']),
             book('nine', ['Mara listened near the gate.']),
             book('ten', ['Mara Mara Mara.']),
             book('other', ['Mara Mara Mara Mara.']),
             book('unassigned', ['Mara Mara Mara Mara Mara.'])]
    for source in books:
        store.save_book(source)
        search(store, source['id'], 'Mara')  # Every excluded book is already indexed.
    repository = SeriesRepository(store)
    series = repository.create_series('The Lamps')
    for identifier, position in [('one', 1), ('nine', 9), ('ten', 10)]:
        repository.set_membership(identifier, series['id'], position)
    other = repository.create_series('Unrelated Namesakes')
    repository.set_membership('other', other['id'], 1)
    result = search(store, 'nine', 'Mara', scope='earlier', limit=1)
    assert len(result['items']) == 1
    assert result['items'][0]['book_id'] in {'one', 'nine'}
    assert {item['book_id'] for item in search(store, 'nine', 'Mara', scope='earlier')['items']} == {'one', 'nine'}
    assert {item['book_id'] for item in search(store, 'nine', 'Mara')['items']} == {'nine'}
    assert {item['book_id'] for item in search(store, 'one', 'Mara', scope='earlier')['items']} == {'one'}
    assert {item['book_id'] for item in search(store, 'unassigned', 'Mara', scope='earlier')['items']} == {'unassigned'}


def test_membership_changes_cannot_leave_old_series_hits_visible(store):
    for identifier in ('one', 'nine'):
        store.save_book(book(identifier, ['Mara waited.']))
    repository = SeriesRepository(store)
    series = repository.create_series('The Lamps')
    repository.set_membership('one', series['id'], 1)
    repository.set_membership('nine', series['id'], 9)
    assert len(search(store, 'nine', 'Mara', scope='earlier')['items']) == 2
    repository.set_membership('one', None)
    assert {item['book_id'] for item in search(store, 'nine', 'Mara', scope='earlier')['items']} == {'nine'}


def test_fts_operators_are_literal_words_and_never_query_grammar(store):
    store.save_book(book('one', ['Mara waited.', 'Elio waited.', 'Mara or Elio waited.']))
    result = search(store, 'one', 'Mara OR Elio')
    assert [item['text'] for item in result['items']] == ['Mara or Elio waited.']
    for query in ['Mara\") OR text:Elio*', "Mara'; DROP TABLE books; --", 'NEAR(Mara Elio, 1)', '"unterminated']:
        assert search(store, 'one', query)['items'] == []
    assert store.book('one')['title'] == 'Volume one'


def test_bounded_query_does_not_silently_discard_final_search_terms(store):
    store.save_book(book('one', ['Mara waited.']))
    assert search(store, 'one', 'Mara ' * 30 + 'Elio')['items'] == []


@pytest.mark.parametrize('query', ['', '  ', None, 5, 'x' * 301, ' ' * 300 + 'Mara'])
def test_query_length_and_type_are_bounded(store, query):
    store.save_book(book('one', ['Mara waited.']))
    with pytest.raises(ValueError):
        search(store, 'one', query)


def test_punctuation_only_query_returns_no_hits_and_invalid_scope_is_rejected(store):
    store.save_book(book('one', ['Mara waited.']))
    assert search(store, 'one', '"*():-')['items'] == []
    with pytest.raises(ValueError):
        search(store, 'one', 'Mara', scope='all')


def test_unicode_offsets_and_hash_identify_exact_source_not_highlight_markup(store):
    original = book('one', ['A moon 🌙 rose.', '“Mára,” she whispered, “wait.”'])
    store.save_book(original)
    before = deepcopy(store.book('one'))
    item = search(store, 'one', 'mara')['items'][0]
    source = original['chapters'][0]['text']
    assert source[item['start']:item['end']] == item['text'] == original['segments'][1]['text']
    assert item['passage_id'] == 'passage-1'
    assert item['chapter_id'] == 'chapter-one'
    assert item['source_hash'] == hashlib.sha256(source.encode('utf-8')).hexdigest()
    assert store.book('one') == before
    assert '<b>' not in item['text']


def test_title_changes_use_current_labels_without_rebuilding_source_index(store):
    original = book('one', ['Mara waited.'])
    store.save_book(original)
    before = search(store, 'one', 'Mara')['items'][0]
    with store.connect() as conn:
        fingerprint = conn.execute('SELECT fingerprint FROM search_books WHERE book_id=?', ('one',)).fetchone()[0]
    original['title'] = 'The Ninth Lamp'
    original['chapters'][0]['title'] = 'The story thus far'
    store.save_book(original)
    after = search(store, 'one', 'Mara')['items'][0]
    assert after['book_title'] == 'The Ninth Lamp' and after['chapter_title'] == 'The story thus far'
    assert after['text'] == before['text'] and after['source_hash'] == before['source_hash']
    with store.connect() as conn:
        assert conn.execute('SELECT fingerprint FROM search_books WHERE book_id=?', ('one',)).fetchone()[0] == fingerprint


def test_source_and_segment_changes_rebuild_index_without_old_hits(store):
    store.save_book(book('one', ['Mara waited.']))
    assert search(store, 'one', 'Mara')['items']
    store.save_book(book('one', ['Elio ran through the rain.', 'Mara left.']))
    items = search(store, 'one', 'Mara')['items']
    assert [item['text'] for item in items] == ['Mara left.']
    assert items[0]['passage_id'] == 'passage-1'
    assert [item['text'] for item in search(store, 'one', 'Elio')['items']] == ['Elio ran through the rain.']
    assert search(store, 'one', 'waited')['items'] == []
    with store.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM passage_search WHERE book_id=?', ('one',)).fetchone()[0] == 2


def test_source_only_change_removes_unanchored_stale_rows_from_index(store):
    original = book('one', ['Mara waited.'])
    store.save_book(original)
    assert search(store, 'one', 'Mara')['items']
    original['chapters'][0]['text'] = 'Elio wandered elsewhere.'
    store.save_book(original)  # Simulate old malformed data with stale segment text.
    assert search(store, 'one', 'Mara')['items'] == []
    with store.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM passage_search WHERE book_id=?', ('one',)).fetchone()[0] == 0


def test_unanchored_high_rank_hit_cannot_hide_valid_match_at_limit(store):
    original = book('one', ['Mara Mara Mara.', 'Mara waited beside the quiet gate.'])
    original['segments'][0]['start'] = -1
    store.save_book(original)
    result = search(store, 'one', 'Mara', limit=1)
    assert [item['passage_id'] for item in result['items']] == ['passage-1']


def test_rank_is_lexical_ordering_and_results_are_bounded(store):
    store.save_book(book('one', ['Mara ' * (index + 1) for index in range(70)]))
    result = search(store, 'one', 'Mara', limit=1000)
    assert len(result['items']) == 50
    ranks = [item['rank'] for item in result['items']]
    assert ranks == sorted(ranks)
    assert all(math.isfinite(rank) for rank in ranks)
    assert all('confidence' not in item and 'character_id' not in item for item in result['items'])
    assert 'not confidence or proof of character identity' in result['note']


def test_missing_fts5_degrades_without_changing_saved_source(store, monkeypatch):
    original = book('one', ['Mara waited.'])
    store.save_book(original)
    connect = store.connect

    class WithoutFts:
        def __init__(self):
            self.connection = connect()

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def execute(self, statement, *args):
            if statement.startswith('CREATE VIRTUAL TABLE'):
                raise sqlite3.OperationalError('no such module: fts5')
            return self.connection.execute(statement, *args)

    monkeypatch.setattr(store, 'connect', WithoutFts)
    result = search(store, 'one', 'Mara')
    assert result['available'] is False and result['items'] == []
    assert 'no FTS5 module' in result['note']
    assert store.book('one')['chapters'] == original['chapters']
