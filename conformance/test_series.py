"""Series: membership and reading order, volume placeholders, cross-book identities and their confirmed links."""
from __future__ import annotations

from . import helpers
from .helpers import add_character, book_in_series, character_named, new_series

# ---------------------------------------------------------------- series records

def test_series_names_are_collapsed_unique_and_validated(api):
    made = new_series(api)
    assert made['name'] == made['name'].strip() and '  ' not in made['name']
    dup = api.call('createSeries', json={'name': made['name'].upper()}, expect=400)
    assert dup.code == 'series_name_taken', 'names are unique ignoring case'
    assert api.call('createSeries', json={'name': 'bad\x00name'}, expect=400).code in ('text_invalid', 'name_invalid')
    other = new_series(api)
    assert other['id'] != made['id'], 'creating is not idempotent'
    assert api.call('renameSeries', path={'series_id': other['id']}, json={'name': made['name']}, expect=400).code == 'series_name_taken'
    renamed = api.call('renameSeries', path={'series_id': other['id']}, json={'name': '  Renamed   Saga  '}).json
    assert renamed == {'id': other['id'], 'name': 'Renamed Saga'}


def test_list_series_orders_by_name_ignoring_case(api):
    a = api.call('createSeries', json={'name': f'zz order b {helpers.token()}'}).json
    b = api.call('createSeries', json={'name': f'ZZ order A {helpers.token()}'}).json
    names = [series['name'] for series in api.call('listSeries').json]
    assert names.index(b['name']) < names.index(a['name'])


def test_unknown_series_is_404(api):
    for operation, extra in (('renameSeries', {'json': {'name': 'x'}}), ('archiveSeries', {}), ('restoreSeries', {}),
                             ('getSeriesMap', {}), ('listSeriesRuns', {}), ('listSeriesCharacters', {}),
                             ('createSeriesCharacter', {'json': {'name': 'x'}}),
                             ('putSeriesVolume', {'json': {'position': 1}}),
                             ('planSeriesProcessing', {'json': {'steps': ['census']}})):
        reply = api.call(operation, path={'series_id': 'no-such-series'}, expect=404, **extra)
        assert reply.code == 'series_not_found', operation
    api.call('deleteSeriesVolume', path={'series_id': 'no-such-series', 'position': 1}, expect=404)


def test_series_archive_and_restore_are_reversible_and_idempotent(api):
    series = new_series(api)
    path = {'series_id': series['id']}
    book = book_in_series(api, series, 1)
    archived = api.call('archiveSeries', path=path).json
    assert archived == {'id': series['id'], 'archived': True, 'retained': True}
    assert api.call('archiveSeries', path=path).json == archived, 'archiving twice changes nothing'
    assert series['id'] not in [s['id'] for s in api.call('listSeries').json]
    library = api.call('getLibrary', query={'include_archived': True}).json
    assert series['id'] in [s['id'] for s in library['series']]
    assert series['id'] not in [s['id'] for s in api.call('getLibrary').json['series']]
    assert api.call('getSeriesMap', path=path).json['series']['archived'] is True, 'a removed series can still be read'
    api.call('listSeriesRuns', path=path)
    api.call('listSeriesCharacters', path=path)
    for reply in (api.call('renameSeries', path=path, json={'name': 'Nope'}, expect=409),
                  api.call('putSeriesVolume', path=path, json={'position': 5}, expect=409),
                  api.call('createSeriesCharacter', path=path, json={'name': 'Nobody'}, expect=409)):
        assert reply.code == 'series_archived'
    assert api.call('planSeriesProcessing', path=path, json={'steps': ['census']}, expect=409).code == 'series_archived'
    assert api.call('getBook', path={'book_id': book['id']}).json['id'] == book['id'], 'member books stay available'
    assert api.call('restoreSeries', path=path).json == {'id': series['id'], 'archived': False, 'retained': True}
    assert api.call('restoreSeries', path=path).json['archived'] is False
    assert series['id'] in [s['id'] for s in api.call('listSeries').json]


# ---------------------------------------------------------------- volumes and membership

def test_volume_placeholders_hold_reading_order_only(api):
    series = new_series(api)
    path = {'series_id': series['id']}
    slot = api.call('putSeriesVolume', path=path, json={'position': 2, 'title': 'The Lost Tide'}).json
    assert slot['position'] == 2 and slot['title'] == 'The Lost Tide' and slot['status'] == 'missing'
    replaced = api.call('putSeriesVolume', path=path, json={'position': 2, 'title': 'Renamed', 'status': 'planned'}).json
    assert replaced['title'] == 'Renamed' and replaced['status'] == 'planned', 'upsert keyed by position'
    volumes = api.call('getSeriesMap', path=path).json['series']['volumes']
    assert [v['position'] for v in volumes] == [2] and volumes[0]['title'] == 'Renamed'
    removed = api.call('deleteSeriesVolume', path={**path, 'position': 2}).json
    assert removed['removed'] is True
    assert api.call('deleteSeriesVolume', path={**path, 'position': 2}).json['removed'] is True, 'idempotent'
    assert api.call('getSeriesMap', path=path).json['series']['volumes'] == []


def test_book_membership_sets_moves_and_detaches(api):
    series = new_series(api)
    book = helpers.import_fresh_txt(api, 'member')
    path = {'book_id': book['id']}
    assert api.call('getBookSeries', path=path).json['membership'] is None
    placed = api.call('setBookSeries', path=path, json={'series_id': series['id'], 'position': 1.5}).json
    assert placed['membership']['position'] == 1.5 and placed['series']['id'] == series['id']
    moved = api.call('setBookSeries', path=path, json={'series_id': series['id'], 'position': 0}).json
    assert moved['membership']['position'] == 0, 'positions run from 0; decimals allowed'
    detached = api.call('setBookSeries', path=path, json={'series_id': None}).json
    assert detached['membership'] is None and detached['series'] is None
    assert api.call('getBookSeries', path=path).json['membership'] is None


def test_a_placeholder_is_replaced_by_the_book_assigned_to_its_position(api):
    series = new_series(api)
    api.call('putSeriesVolume', path={'series_id': series['id']}, json={'position': 3, 'title': 'Placeholder'})
    book = book_in_series(api, series, 3)
    volumes = api.call('getSeriesMap', path={'series_id': series['id']}).json['series']['volumes']
    assert [v['position'] for v in volumes] == [3]
    assert volumes[0]['status'] == 'available' and volumes[0]['book_id'] == book['id']


def test_a_position_is_used_once_and_membership_errors(api):
    series = new_series(api)
    first = book_in_series(api, series, 1)
    other = helpers.import_fresh_txt(api, 'other')
    taken = api.call('setBookSeries', path={'book_id': other['id']}, json={'series_id': series['id'], 'position': 1}, expect=400)
    assert taken.code == 'position_taken'
    assert api.call('setBookSeries', path={'book_id': other['id']}, json={'series_id': 'no-such-series', 'position': 1},
                    expect=400).code == 'unknown_series'
    assert api.call('setBookSeries', path={'book_id': other['id']}, json={'series_id': series['id']}, expect=400).code in (
        'position_invalid', 'position_without_series')
    assert api.call('setBookSeries', path={'book_id': other['id']}, json={'series_id': series['id'], 'position': -1},
                    expect=400).code == 'position_invalid'
    assert api.call('setBookSeries', path={'book_id': other['id']}, json={'series_id': series['id'], 'position': '2'},
                    negative=True, expect=422).code == 'validation_error', 'a position is a JSON number, not a numeric string'
    assert api.call('setBookSeries', path={'book_id': 'no-such-book'}, json={'series_id': None}, expect=404).code == 'book_not_found'
    assert first['id'] != other['id']


def test_series_listing_shows_supplied_books_in_reading_order(api):
    series = new_series(api)
    late = book_in_series(api, series, 5, 'late')
    early = book_in_series(api, series, 2, 'early')
    api.call('putSeriesVolume', path={'series_id': series['id']}, json={'position': 3, 'title': 'Not in the library'})
    listed = next(s for s in api.call('listSeries').json if s['id'] == series['id'])
    assert [volume['position'] for volume in listed['volumes']] == [2, 3, 5]
    assert [b['book_id'] for b in listed['books']] == [early['id'], late['id']], 'supplied books in reading order'
    library = next(s for s in api.call('getLibrary').json['series'] if s['id'] == series['id'])
    assert [b['book_id'] for b in library['books']] == [early['id'], late['id']]


def test_a_removed_book_stays_a_volume_of_its_series(api):
    series = new_series(api)
    book = book_in_series(api, series, 1)
    api.call('archiveBook', path={'book_id': book['id']})
    volumes = api.call('getSeriesMap', path={'series_id': series['id']}).json['series']['volumes']
    assert volumes[0]['status'] == 'archived' and volumes[0]['book_id'] == book['id']
    assert api.call('getBookSeries', path={'book_id': book['id']}).json['membership'] is None, (
        'membership reads as null while the book is removed')
    api.call('restoreBook', path={'book_id': book['id']})
    assert api.call('getBookSeries', path={'book_id': book['id']}).json['membership']['position'] == 1


# ---------------------------------------------------------------- identities and confirmed links

def test_confirmed_links_join_characters_across_books(api):
    series = new_series(api)
    spath = {'series_id': series['id']}
    first = book_in_series(api, series, 1, 'first')
    second = book_in_series(api, series, 2, 'second')
    add_character(api, first, 'Mira Vale')
    add_character(api, second, 'mira  vale')
    first, second = (api.call('getBook', path={'book_id': b['id']}).json for b in (first, second))
    a, b = character_named(first, 'Mira Vale'), character_named(second, 'mira  vale')
    identity = api.call('createSeriesCharacter', path=spath, json={'name': 'Mira'}).json
    assert api.call('createSeriesCharacter', path=spath, json={'name': 'Mira'}).json['id'] != identity['id'], (
        'a shared name is not a shared identity: duplicates are allowed')
    # Nothing links by name alone.
    assert api.call('getBookSeries', path={'book_id': second['id']}).json['links'] == []
    link = api.call('linkSeriesCharacter', path={'book_id': first['id'], 'character_id': a['id']},
                    json={'series_character_id': identity['id']}).json
    assert link['kind'] == 'linked'
    assert link['character_id'] == a['id'] and link['series_character_id'] == identity['id'] and link['stale'] is False
    again = api.call('linkSeriesCharacter', path={'book_id': first['id'], 'character_id': a['id']},
                     json={'series_character_id': identity['id']}).json
    assert again['confirmed_at'] == link['confirmed_at'], 're-linking the same identity keeps the original confirmation time'
    suggestions = api.call('listSeriesLinkSuggestions', path={'book_id': second['id']}).json
    assert suggestions['series_id'] == series['id']
    proposed = [s for s in suggestions['suggestions'] if s['character_id'] == b['id']]
    assert proposed and identity['id'] in [c['series_character_id'] for c in proposed[0]['candidates']], (
        'an exact name match (ignoring case and repeated spaces) with a linked earlier character is proposed')
    assert api.call('getBookSeries', path={'book_id': second['id']}).json['links'] == [], 'a suggestion links nothing'
    api.call('linkSeriesCharacter', path={'book_id': second['id'], 'character_id': b['id']},
             json={'series_character_id': identity['id']})
    characters = api.call('listSeriesCharacters', path=spath).json
    linked = next(c for c in characters if c['id'] == identity['id'])
    assert {(l['book_id'], l['character_id']) for l in linked['links']} == {(first['id'], a['id']), (second['id'], b['id'])}
    unlinked = api.call('linkSeriesCharacter', path={'book_id': second['id'], 'character_id': b['id']},
                        json={'series_character_id': None}).json
    assert unlinked == {'character_id': b['id'], 'kind': 'unlinked'}, 'the unlink result has a `kind` and no `linked` flag'
    assert api.call('linkSeriesCharacter', path={'book_id': second['id'], 'character_id': b['id']}, json={}).json == unlinked


def test_moving_within_a_series_keeps_links_and_detaching_deletes_them(api):
    series = new_series(api)
    book = book_in_series(api, series, 1)
    add_character(api, book, 'Tomas Reed')
    book = api.call('getBook', path={'book_id': book['id']}).json
    char = character_named(book, 'Tomas Reed')
    identity = api.call('createSeriesCharacter', path={'series_id': series['id']}, json={'name': 'Tomas'}).json
    api.call('linkSeriesCharacter', path={'book_id': book['id'], 'character_id': char['id']},
             json={'series_character_id': identity['id']})
    api.call('setBookSeries', path={'book_id': book['id']}, json={'series_id': series['id'], 'position': 4})
    assert len(api.call('getBookSeries', path={'book_id': book['id']}).json['links']) == 1, 'moving within the series keeps links'
    other = new_series(api)
    api.call('setBookSeries', path={'book_id': book['id']}, json={'series_id': other['id'], 'position': 1})
    assert api.call('getBookSeries', path={'book_id': book['id']}).json['links'] == [], 'moving to another series deletes links'


def test_link_errors(api):
    series = new_series(api)
    book = book_in_series(api, series, 1)
    loose = helpers.import_fresh_txt(api, 'loose')
    identity = api.call('createSeriesCharacter', path={'series_id': series['id']}, json={'name': 'Someone'}).json
    add_character(api, book, 'Someone Real')
    book = api.call('getBook', path={'book_id': book['id']}).json
    char = character_named(book, 'Someone Real')
    assert api.call('linkSeriesCharacter', path={'book_id': book['id'], 'character_id': 'narrator'},
                    json={'series_character_id': identity['id']}, expect=400).code == 'character_not_linkable'
    assert api.call('linkSeriesCharacter', path={'book_id': book['id'], 'character_id': char['id']},
                    json={'series_character_id': 'no-such-identity'}, expect=400).code == 'unknown_series_character'
    assert api.call('linkSeriesCharacter', path={'book_id': loose['id'], 'character_id': 'narrator'},
                    json={'series_character_id': identity['id']}, expect=400).code in ('book_not_in_series', 'character_not_linkable')
    assert api.call('linkSeriesCharacter', path={'book_id': book['id'], 'character_id': 'no-such-character'},
                    json={'series_character_id': identity['id']}, expect=404).code == 'character_not_found'


def test_context_comes_only_from_confirmed_links_of_strictly_earlier_volumes(api):
    series = new_series(api)
    early = book_in_series(api, series, 1, 'early')
    late = book_in_series(api, series, 2, 'late')
    for book in (early, late):
        context = api.call('getBookSeriesContext', path={'book_id': book['id']}).json
        assert context['series']['id'] == series['id']
        assert context['characters'] == [], 'no confirmed links, no context: names alone never carry knowledge'
        assert context['fingerprint'] == api.call('getBookSeriesContext', path={'book_id': book['id']}).json['fingerprint']
    loose = helpers.import_fresh_txt(api, 'loose')
    assert api.call('getBookSeriesContext', path={'book_id': loose['id']}).json['series'] is None


def test_series_map_and_runs_of_an_idle_series(api):
    series = new_series(api)
    book_in_series(api, series, 1)
    api.call('createSeriesCharacter', path={'series_id': series['id']}, json={'name': 'Mira'})
    series_map = api.call('getSeriesMap', path={'series_id': series['id']}).json
    assert series_map['series']['id'] == series['id'] and len(series_map['characters']) == 1
    assert api.call('listSeriesRuns', path={'series_id': series['id']}).json == {'runs': []}


def test_series_plan_of_local_steps_costs_nothing(api):
    series = new_series(api)
    book = book_in_series(api, series, 1)
    plan = api.call('planSeriesProcessing', path={'series_id': series['id']}, json={'steps': ['census']}).json
    assert [entry['book_id'] for entry in plan['books']] == [book['id']]
    assert plan['requests'] == 0 and plan['estimated_cost_usd'] == 0
    assert api.call('planSeriesProcessing', path={'series_id': series['id']}, json={'steps': ['no-such-step']},
                    expect=400).code == 'unknown_step'
    empty = new_series(api)
    assert api.call('planSeriesProcessing', path={'series_id': empty['id']}, json={'steps': ['census']}).json['books'] == []


def test_resuming_a_run_that_does_not_exist_is_404(api):
    series = new_series(api)
    reply = api.call('resumeSeriesProcessing', path={'series_id': series['id'], 'job_id': 'no-such-job'}, expect=404)
    assert reply.code == 'series_run_not_found'
