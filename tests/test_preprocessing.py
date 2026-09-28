"""The local census stays cheap and source-grounded."""
from copy import deepcopy

import pytest

from bardic import preprocessing
from bardic.importer import parse_book
from bardic.preprocessing import census, eligible_chapters
from bardic.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def book_with_cast():
    book = parse_book('story.txt', (
        'Chapter 1\n\nMara said, “Wait.” Elias replied, “All right.”\n\nMara watched Mara Voss.\n\n'
        'Chapter 2\n\nMara whispered, “Stay.” Mara waited.\n\n'
        'Chapter 3\n\nA quiet ending.').encode())
    book['characters'].append({'id': 'mara', 'name': 'Mara Voss', 'aliases': ['Mara']})
    return book


def test_census_is_local_and_complete_without_any_provider(store, monkeypatch):
    book = book_with_cast()
    monkeypatch.setattr(preprocessing.a, '_post_analysis', lambda *args: pytest.fail('Census cannot call a provider'))
    result = census(book, store)
    assert result['local_complete'] is True
    assert result['local_chapters_scanned'] == 3


def test_unique_alias_mentions_do_not_double_count_longer_names(store):
    result = census(book_with_cast(), store)
    mara = next(c for c in result['characters'] if c['id'] == 'mara')
    assert mara['mentions'] == 5
    assert mara['explicit_speech_tags'] == 2
    assert mara['chapter_count'] == 2
    assert mara['known_character'] is True
    elias = next(c for c in result['characters'] if c['name'] == 'Elias')
    assert elias['known_character'] is False and elias['explicit_speech_tags'] == 1
    assert elias['id'].startswith('candidate_')
    assert 'identity' in result['note'] and 'importance' in result['note']


def test_pronouns_are_not_promoted_to_character_candidates(store):
    book = parse_book('story.txt', b'He said, "Wait." She replied, "No." They whispered, "Later."')
    assert census(book, store)['characters'] == []


def test_front_back_matter_excluded_but_recap_and_unknown_body_eligible(store):
    book = book_with_cast()
    book['chapters'][0]['kind'] = 'front_matter'
    book['chapters'][1]['kind'] = 'recap'
    book['chapters'][2]['kind'] = 'back_matter'
    result = census(book, store)
    assert [c['id'] for c in eligible_chapters(book)] == [book['chapters'][1]['id']]
    assert result['eligible_chapters'] == 1
    assert result['local_chapters_scanned'] == 3
    assert result['chapters'][0]['eligible'] is False
    assert result['estimated_source_tokens'] == result['chapters'][1]['estimated_tokens']
    assert not any(c['name'] == 'Elias' for c in result['characters'])
    book['chapters'][1]['kind'] = 'section'
    assert len(eligible_chapters(book)) == 1


def test_ambiguity_and_low_confidence_prioritize_work_without_asserting_identity(store):
    book = book_with_cast()
    book['characters'].extend([{'id': 'captain', 'name': 'Captain Orin', 'aliases': ['Captain']},
                               {'id': 'other-captain', 'name': 'Captain Hale', 'aliases': ['Captain']}])
    dialogue = next(s for s in book['segments'] if s['kind'] == 'dialogue')
    dialogue.update(speaker_id='mara', confidence=.3)
    result = census(book, store)
    mara = next(c for c in result['characters'] if c['id'] == 'mara')
    assert mara['dialogue_turns'] == 1 and mara['uncertain_attributions'] == 1
    assert mara['priority'] == 'deep' and mara['recommended_evidence_limit'] == 16
    captain = next(c for c in result['characters'] if c['id'] == 'captain')
    assert captain['ambiguous_aliases'] == 1 and captain['priority'] == 'deep'
    assert captain['mentions'] == 0


def test_cache_reuses_exact_inputs_and_invalidates_meaningful_changes(store, monkeypatch):
    book = book_with_cast()
    original = census(book, store)
    class NoScan:
        def finditer(self, _):
            pytest.fail('Matching census inputs must reuse saved local result')
    with monkeypatch.context() as patch:
        patch.setattr(preprocessing.a, 'NAME_TAG', NoScan())
        assert census(book, store) == original
    renamed = deepcopy(book)
    renamed['chapters'][0]['title'] = 'Corrected chapter title'
    assert census(renamed, store)['chapters'][0]['title'] == 'Corrected chapter title'
    renamed['characters'][-1]['aliases'].append('M')
    assert census(renamed, store)['fingerprint'] != original['fingerprint']
    assigned = deepcopy(book)
    next(s for s in assigned['segments'] if s['kind'] == 'dialogue')['speaker_id'] = 'mara'
    assert census(assigned, store)['fingerprint'] != original['fingerprint']
    changed_kind = deepcopy(book)
    next(s for s in changed_kind['segments'] if s['kind'] == 'dialogue')['kind'] = 'narration'
    assert census(changed_kind, store)['fingerprint'] != original['fingerprint']


def test_unknown_confidence_counts_as_uncertain_instead_of_crashing(store):
    book = book_with_cast()
    next(s for s in book['segments'] if s['kind'] == 'dialogue').update(speaker_id='mara', confidence=None)
    mara = next(c for c in census(book, store)['characters'] if c['id'] == 'mara')
    assert mara['uncertain_attributions'] == 1


def test_front_matter_only_book_has_no_eligible_chapters(store):
    book = parse_book('copyright.txt', b'Copyright 2026.')
    book['chapters'][0]['kind'] = 'front_matter'
    assert census(book, store)['eligible_chapters'] == 0
    assert eligible_chapters(book) == []
