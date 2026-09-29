"""Manual edits to a book (cast, passages, scenes, pronunciations). The canonical text never changes."""
from __future__ import annotations

import pytest

from . import helpers


def _book(api, book):
    return api.call('getBook', path={'book_id': book['id']}).json


def _character(book, name):
    return next(c for c in book['characters'] if c['name'] == name)


def _assert_text_untouched(before, after):
    assert helpers.canonical_text(after) == helpers.canonical_text(before), 'canonical chapter text is immutable'
    assert helpers.passage_anchors(after) == helpers.passage_anchors(before), 'passage ids, offsets and text never move'
    helpers.assert_book_invariants(after)


# ---------------------------------------------------------------- cast

def test_add_character_returns_the_book_with_the_new_cast_member(api, fresh_book):
    path = {'book_id': fresh_book['id']}
    book = api.call('addCharacter', path=path, json={'name': 'Mira Vale', 'aliases': ['Mira'], 'description': 'Watches the tide.',
                                                     'direction': 'Speak quietly.'}).json
    assert book['revision'] == fresh_book['revision'] + 1
    mira = _character(book, 'Mira Vale')
    assert mira['aliases'] == ['Mira'] and mira['description'] == 'Watches the tide.' and mira['direction'] == 'Speak quietly.'
    assert mira['voices'].get('gemini') == {'id': 'Kore'}, 'a new character starts with the default Gemini voice'
    assert mira['id'] not in ('narrator', 'unassigned')
    assert len(book['characters']) == len(fresh_book['characters']) + 1
    _assert_text_untouched(fresh_book, book)


def test_add_character_needs_a_name(api, fresh_book):
    path = {'book_id': fresh_book['id']}
    for body in ({}, {'name': None}, {'description': 'nameless'}):
        assert api.call('addCharacter', path=path, json=body, expect=400).code == 'character_name_required', body
    assert api.call('addCharacter', path=path, json={'name': ''}, negative=True, expect=422).code == 'validation_error'
    assert api.call('addCharacter', path=path, json={'name': 'x' * 101}, negative=True, expect=422).code == 'validation_error'
    assert api.call('addCharacter', path={'book_id': 'no-such-book'}, json={'name': 'Nobody'}, expect=404).code == 'book_not_found'
    assert _book(api, fresh_book)['revision'] == fresh_book['revision'], 'a refused edit changes nothing'


def test_renaming_a_character_remembers_the_old_name(api, fresh_book):
    book = api.call('addCharacter', path={'book_id': fresh_book['id']}, json={'name': 'Tomas'}).json
    tomas = _character(book, 'Tomas')
    path = {'book_id': fresh_book['id'], 'character_id': tomas['id']}
    renamed = api.call('editCharacter', path=path, json={'name': 'Tomas Reed'}).json
    edited = next(c for c in renamed['characters'] if c['id'] == tomas['id'])
    assert edited['name'] == 'Tomas Reed' and 'Tomas' in (edited.get('former_names') or [])
    assert 'Tomas' not in edited['aliases'], 'former names are not aliases'
    assert renamed['revision'] == book['revision'] + 1


def test_an_edit_that_changes_nothing_is_a_no_op(api, fresh_book):
    book = api.call('addCharacter', path={'book_id': fresh_book['id']}, json={'name': 'Steady', 'description': 'Same.'}).json
    steady = _character(book, 'Steady')
    path = {'book_id': fresh_book['id'], 'character_id': steady['id']}
    for body in ({}, {'name': None, 'description': None}, {'name': 'Steady', 'description': 'Same.'}):
        again = api.call('editCharacter', path=path, json=body).json
        assert again['revision'] == book['revision'], f'{body} changes nothing, so the revision stays'
        assert again == book


def test_character_edit_errors(api, fresh_book):
    path = {'book_id': fresh_book['id']}
    assert api.call('editCharacter', path={**path, 'character_id': 'no-such-character'}, json={'name': 'X'},
                    expect=404).code == 'character_not_found'
    assert api.call('editCharacter', path={**path, 'character_id': 'narrator'}, json={'voices': {'no-such-provider': {'id': 'x'}}},
                    expect=400).code == 'voice_provider_unknown'
    assert api.call('editCharacter', path={**path, 'character_id': 'narrator'},
                    json={'voices': {'gemini': {'id': 'Puck', 'library': 'vl_0123456789abcdef'}}}, negative=True,
                    expect=422).code == 'validation_error', 'a voice choice is exactly one of `id` or `library`'


def test_a_voice_choice_can_be_set_and_cleared(api, fresh_book):
    book = api.call('addCharacter', path={'book_id': fresh_book['id']}, json={'name': 'Voiced'}).json
    who = _character(book, 'Voiced')
    path = {'book_id': fresh_book['id'], 'character_id': who['id']}
    chosen = api.call('editCharacter', path=path, json={'voices': {'gemini': {'id': 'Puck'}}}).json
    assert next(c for c in chosen['characters'] if c['id'] == who['id'])['voices']['gemini'] == {'id': 'Puck'}
    cleared = api.call('editCharacter', path=path, json={'voices': {'gemini': {'id': ''}}}).json
    assert 'gemini' not in next(c for c in cleared['characters'] if c['id'] == who['id'])['voices'], 'a blank id clears the choice'


# ---------------------------------------------------------------- passages and scenes

def test_confirming_a_speaker_sets_confidence_locks_it_and_fills_the_scene_cast(api, fresh_book):
    book = api.call('addCharacter', path={'book_id': fresh_book['id']}, json={'name': 'Mira'}).json
    mira = _character(book, 'Mira')
    dialogue = next(s for s in book['passages'] if s['kind'] == 'dialogue' and 'The water is late' in s['text'])
    edited = api.call('editPassage', path={'book_id': book['id'], 'passage_id': dialogue['id']},
                      json={'speaker_id': mira['id']}).json
    passage = next(s for s in edited['passages'] if s['id'] == dialogue['id'])
    assert passage['speaker_id'] == mira['id'] and passage['confidence'] == 1.0
    assert 'speaker_id' in passage['manual_fields'] and passage['manual_fields'] == sorted(passage['manual_fields'])
    assert passage['text'] == dialogue['text'] and passage['start'] == dialogue['start'] and passage['end'] == dialogue['end']
    scene = next(s for s in edited['scenes'] if s['id'] == passage['scene_id'])
    assert mira['id'] in scene['character_ids'], 'a scene lists the speakers of its passages'
    for each in edited['scenes']:
        speakers = {s['speaker_id'] for s in edited['passages'] if s['scene_id'] == each['id']}
        assert set(each['character_ids']) == speakers and each['character_ids'] == sorted(each['character_ids']), (
            'after an edit every scene lists its passages\' speakers, sorted')
    assert edited['revision'] == book['revision'] + 1
    _assert_text_untouched(book, edited)


def test_passage_edit_direction_cues_and_seed(api, fresh_book):
    path = {'book_id': fresh_book['id'], 'passage_id': fresh_book['passages'][0]['id']}
    edited = api.call('editPassage', path=path, json={'direction': 'Slowly.', 'cues': ['quiet', 'urgent'], 'seed': 7}).json
    passage = next(s for s in edited['passages'] if s['id'] == path['passage_id'])
    assert passage['direction'] == 'Slowly.' and passage['cues'] == ['quiet', 'urgent'] and passage['seed'] == 7
    assert {'direction', 'cues', 'seed'} <= set(passage['manual_fields'])
    same = api.call('editPassage', path=path, json={'direction': 'Slowly.', 'cues': ['quiet', 'urgent'], 'seed': 7}).json
    assert same['revision'] == edited['revision'], 'resending the current values saves nothing'
    cleared = api.call('editPassage', path=path, json={'seed': None, 'cues': [], 'direction': ''}).json
    passage = next(s for s in cleared['passages'] if s['id'] == path['passage_id'])
    assert not passage.get('seed') and passage['cues'] == [] and passage['direction'] == '', 'null clears a seed; "" and [] clear the rest'
    _assert_text_untouched(fresh_book, cleared)


@pytest.mark.xfail(strict=True, reason='Known Python defect (contract 0.4.0): editPassage locks speaker_id when only direction was sent. Fix in Python or Rust; strict, so this fails once it passes and the marker must go.')
def test_manual_fields_lists_only_the_fields_a_person_changed(api, fresh_book):
    """`manual_fields`: "the passage fields a person set by hand"; an edit locks each field it actually changes."""
    path = {'book_id': fresh_book['id'], 'passage_id': fresh_book['passages'][0]['id']}
    edited = api.call('editPassage', path=path, json={'direction': 'Slowly.'}).json
    passage = next(s for s in edited['passages'] if s['id'] == path['passage_id'])
    assert passage['manual_fields'] == ['direction'], 'editing the direction alone does not lock the speaker'
    untouched = next(s for s in edited['passages'] if s['id'] != path['passage_id'])
    assert untouched['manual_fields'] == []


def test_passage_edit_errors(api, fresh_book):
    passage_id = fresh_book['passages'][0]['id']
    path = {'book_id': fresh_book['id'], 'passage_id': passage_id}
    assert api.call('editPassage', path=path, json={'speaker_id': 'no-such-character'}, expect=400).code == 'character_not_in_cast'
    assert api.call('editPassage', path={**path, 'passage_id': 'no-such-passage'}, json={}, expect=404).code == 'passage_not_found'
    assert api.call('editPassage', path=path, json={'seed': -1}, negative=True, expect=422).code == 'validation_error'
    assert api.call('editPassage', path=path, json={'seed': 2 ** 32}, negative=True, expect=422).code == 'validation_error'
    assert api.call('editPassage', path=path, json={'text': 'rewritten'}, negative=True, expect=422).code == 'validation_error', (
        'the text of a passage cannot be edited')
    assert _book(api, fresh_book)['revision'] == fresh_book['revision']


def test_the_retired_segments_route_is_gone(api, fresh_book):
    """A passage is edited at `/passages/{passage_id}`; the old `/segments/` route no longer exists and changes nothing."""
    passage = fresh_book['passages'][0]['id']
    reply = api.request('PATCH', f'/api/books/{fresh_book["id"]}/segments/{passage}', json={'direction': 'Slowly.'}, unrouted=True)
    assert reply.status in (404, 405) and reply.code == 'route_not_found', reply.summary()
    assert _book(api, fresh_book)['revision'] == fresh_book['revision']


def test_scene_edit(api, fresh_book):
    scene = fresh_book['scenes'][0]
    path = {'book_id': fresh_book['id'], 'scene_id': scene['id']}
    edited = api.call('editScene', path=path, json={'title': 'The Sea Wall', 'summary': 'Waiting for the tide.',
                                                    'tone': 'Wistful', 'direction': 'Keep it low.'}).json
    changed = next(s for s in edited['scenes'] if s['id'] == scene['id'])
    assert (changed['title'], changed['summary'], changed['tone'], changed['direction']) == (
        'The Sea Wall', 'Waiting for the tide.', 'Wistful', 'Keep it low.')
    assert changed['passage_ids'] == scene['passage_ids'], 'scene boundaries cannot be edited'
    assert edited['revision'] == fresh_book['revision'] + 1
    assert api.call('editScene', path=path, json={}).json['revision'] == edited['revision']
    assert api.call('editScene', path={**path, 'scene_id': 'no-such-scene'}, json={'title': 'X'}, expect=404).code == 'scene_not_found'
    api.call('editScene', path=path, json={'title': ''}, negative=True, expect=422)
    _assert_text_untouched(fresh_book, edited)


# ---------------------------------------------------------------- references

def test_references_are_anchored_to_the_exact_source(api, fresh_book):
    book = api.call('addCharacter', path={'book_id': fresh_book['id']}, json={'name': 'Mira'}).json
    mira = _character(book, 'Mira')
    dialogue = next(s for s in book['passages'] if s['kind'] == 'dialogue' and 'The water is late' in s['text'])
    book = api.call('editPassage', path={'book_id': book['id'], 'passage_id': dialogue['id']}, json={'speaker_id': mira['id']}).json
    refs = api.call('listCharacterReferences', path={'book_id': book['id'], 'character_id': mira['id']}).json
    assert {ref['kind'] for ref in refs} >= {'dialogue', 'mention'}
    assert all(ref['character_id'] == mira['id'] for ref in refs)
    for ref in refs:
        text = helpers.chapter_by_id(book, ref['chapter_id'])['text']
        assert text[ref['start']:ref['end']] == ref['quote'], 'chapter.text[start:end] == quote, in code points'
    order = [(book['chapters'].index(helpers.chapter_by_id(book, r['chapter_id'])), r['start']) for r in refs]
    assert order == sorted(order), 'references come in reading order'
    dialogue_refs = [r for r in refs if r['kind'] == 'dialogue']
    assert dialogue_refs[0]['passage_id'] == dialogue['id'] and dialogue_refs[0]['quote'] == dialogue['text']
    assert dialogue_refs[0]['provider'] == 'reviewed' and dialogue_refs[0]['origin'] == 'manual'


def test_reserved_characters_have_no_references_and_unknown_ones_404(api, txt_book):
    path = {'book_id': txt_book['id']}
    assert api.call('listCharacterReferences', path={**path, 'character_id': 'narrator'}).json == []
    assert api.call('listCharacterReferences', path={**path, 'character_id': 'unassigned'}).json == []
    assert api.call('listCharacterReferences', path={**path, 'character_id': 'no-such-character'},
                    expect=404).code == 'character_not_found'


# ---------------------------------------------------------------- pronunciations

def _add(api, book, **entry):
    return api.call('addPronunciation', path={'book_id': book['id']}, json=entry).json


def test_pronunciation_usage_counts_whole_words_and_respects_case(api, fresh_book):
    saved = _add(api, fresh_book, term='harbour', respelling='HAR-ber')
    assert saved['book']['revision'] == fresh_book['revision'] + 1 and saved['retired_takes'] == 0
    entry = saved['pronunciations'][0]
    assert entry['id'] and entry['term'] == 'harbour' and entry['respelling'] == 'HAR-ber' and entry['match_case'] is True
    usage = entry['usage']
    assert usage['occurrences'] >= 2 and usage['passages'] >= 1 and usage['rendered_passages'] == 0
    assert 0 < len(usage['examples']) <= 3
    for example in usage['examples']:
        text = helpers.chapter_by_id(saved['book'], example['chapter_id'])['text']
        assert text[example['start']:example['end']] == 'harbour' and 'harbour' in example['context']
    assert usage['first_passage_id'] in {s['id'] for s in saved['book']['passages']}
    assert _add(api, fresh_book, term='harbo', respelling='HAR')['pronunciations'][1]['usage']['occurrences'] == 0, (
        'matching is by whole word')
    exact = _add(api, fresh_book, term='MIRA', respelling='MEER-ah')['pronunciations'][2]
    loose = _add(api, fresh_book, term='THOMAS', respelling='TOM-ass', match_case=False)['pronunciations'][3]
    assert exact['usage']['occurrences'] == 0, 'a case-sensitive term matches its exact case only'
    assert loose['match_case'] is False
    listed = api.call('listPronunciations', path={'book_id': fresh_book['id']}).json['pronunciations']
    assert [p['term'] for p in listed] == ['harbour', 'harbo', 'MIRA', 'THOMAS'], 'saved order'
    _assert_text_untouched(fresh_book, api.call('getBook', path={'book_id': fresh_book['id']}).json)


def test_case_insensitive_term_matches_any_case(api, fresh_book):
    entry = _add(api, fresh_book, term='MIRA', respelling='MEER-ah', match_case=False)['pronunciations'][0]
    assert entry['usage']['occurrences'] >= 2


def test_pronunciation_update_delete_and_the_book_field(api, fresh_book):
    saved = _add(api, fresh_book, term='lantern', respelling='LAN-tern', note='first', id='ignored-by-the-server')
    entry = saved['pronunciations'][0]
    assert entry['id'] != 'ignored-by-the-server', 'an id sent when adding is ignored'
    assert entry['note'] == 'first'
    path = {'book_id': fresh_book['id'], 'entry_id': entry['id']}
    patched = api.call('updatePronunciation', path=path, json={'respelling': 'LAN-turn', 'note': None}).json
    changed = patched['pronunciations'][0]
    assert changed['respelling'] == 'LAN-turn' and changed['term'] == 'lantern' and not changed.get('note'), (
        'fields left out keep their values; null clears one')
    unchanged = api.call('updatePronunciation', path=path, json={'respelling': 'LAN-turn'}).json
    assert unchanged['book']['revision'] == patched['book']['revision'], 'a change that leaves the entry as it was saves nothing'
    assert api.call('updatePronunciation', path=path, json={'term': None}, expect=400).code == 'pronunciation_invalid'
    removed = api.call('deletePronunciation', path=path).json
    assert removed['pronunciations'] == [] and not removed['book'].get('pronunciations'), (
        'removing the last entry removes the book field')
    assert removed['book']['revision'] > patched['book']['revision']
    assert api.call('deletePronunciation', path=path, expect=404).code == 'pronunciation_not_found'
    assert api.call('updatePronunciation', path=path, json={'note': 'gone'}, expect=404).code == 'pronunciation_not_found'


def test_pronunciation_validation(api, fresh_book):
    _add(api, fresh_book, term='lantern', respelling='LAN-tern')
    duplicate = api.call('addPronunciation', path={'book_id': fresh_book['id']}, json={'term': 'lantern', 'respelling': 'x'}, expect=400)
    assert duplicate.code == 'pronunciation_duplicate'
    for bad in ('say (laugh)', 'a<b>c', 'x[[y]]', 'back\\slash', 'curly{brace}'):
        reply = api.call('addPronunciation', path={'book_id': fresh_book['id']}, json={'term': 'other', 'respelling': bad}, expect=400)
        assert reply.code == 'pronunciation_invalid', bad
    assert api.call('addPronunciation', path={'book_id': fresh_book['id']}, json={'term': '!!!', 'respelling': 'x'},
                    expect=400).code == 'pronunciation_invalid', 'a term needs a letter or digit'
    assert api.call('addPronunciation', path={'book_id': fresh_book['id']},
                    json={'term': 'harbour', 'respelling': 'x', 'character_id': 'no-such-character'},
                    expect=400).code == 'character_not_in_cast'
    api.call('addPronunciation', path={'book_id': fresh_book['id']}, json={'term': 'x'}, negative=True, expect=422)
    assert api.call('addPronunciation', path={'book_id': 'no-such-book'}, json={'term': 'x', 'respelling': 'y'},
                    expect=404).code == 'book_not_found'
    assert api.call('listPronunciations', path={'book_id': 'no-such-book'}, expect=404).code == 'book_not_found'


def test_per_narrator_overrides(api, fresh_book):
    saved = _add(api, fresh_book, term='lantern', respelling='LAN-tern', providers={'gemini': 'LAN-turn', 'breeze': ''})
    providers = saved['pronunciations'][0].get('providers') or {}
    assert providers.get('gemini') == 'LAN-turn' and 'breeze' not in providers, 'an empty override is dropped'
    api.call('addPronunciation', path={'book_id': fresh_book['id']}, json={'term': 'other', 'respelling': 'x',
                                                                          'providers': {'no-such-narrator': 'y'}},
             negative=True, expect=422)
