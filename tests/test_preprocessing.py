"""Local census and discovery coverage remain cheap and source-grounded."""
from copy import deepcopy

import pytest

from bardic import preprocessing
from bardic.importer import parse_book
from bardic.legacy_phase import LegacyProcessingStore as ProcessingStore, coverage
from bardic.preprocessing import census, eligible_chapters
from bardic.processing import source_hash
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


def discovery_unit(store, book, chapter, start=0, end=None, *, provider='anthropic', key=None, source=None):
    end = len(chapter['text']) if end is None else end
    unit = {'chapter_id': chapter['id'], 'start': start, 'end': end, 'provider': provider, 'stage': 'discovery', 'result': {'characters': []}}
    ProcessingStore(store).save_unit(book['id'], key or f'{chapter["id"]}-{start}-{end}', 'discovery', source or source_hash(book), unit)


def checkpoint(store, book, *, provider='anthropic', source=None):
    value = {'provider': provider, 'model': 'test-model', 'working_book': deepcopy(source or book),
             'chapters': [{'id': c['id'], 'title': c['title'], 'discovery_complete': True} for c in book['chapters']]}
    store.save_analysis_checkpoint(book['id'], 'legacy-key', value)


def test_census_is_local_complete_but_never_semantic_complete_by_itself(store, monkeypatch):
    book = book_with_cast()
    monkeypatch.setattr(preprocessing.a, '_post_analysis', lambda *args: pytest.fail('Census cannot call a provider'))
    result = coverage(book, store)
    assert result['local']['local_complete'] is True
    assert result['local']['local_chapters_scanned'] == 3
    assert result['semantic_chapters_complete'] == 0
    assert result['whole_book_discovered'] is False and result['profiles_provisional'] is True
    assert result['usage']['attempts'] == 0


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


def test_full_semantic_ranges_are_needed_for_every_eligible_chapter(store):
    book = book_with_cast()
    for chapter in book['chapters'][:-1]:
        discovery_unit(store, book, chapter)
    partial = coverage(book, store)
    assert partial['semantic_chapters_complete'] == 2 and partial['profiles_provisional'] is True
    discovery_unit(store, book, book['chapters'][-1])
    complete = coverage(book, store)
    assert complete['semantic_chapters_complete'] == 3 and complete['whole_book_discovered'] is True
    assert complete['profiles_provisional'] is False


def test_discovery_coverage_merges_whitespace_gaps_and_overlap_but_not_prose_gaps(store):
    book = parse_book('story.txt', b'Alpha.\n\nBeta.')
    chapter = book['chapters'][0]
    discovery_unit(store, book, chapter, 0, 6)
    discovery_unit(store, book, chapter, 8)
    assert coverage(book, store)['whole_book_discovered'] is True
    other = parse_book('story.txt', b'Alpha. Missing words. Beta.')
    discovery_unit(store, other, other['chapters'][0], 0, 6)
    discovery_unit(store, other, other['chapters'][0], 21)
    assert coverage(other, store)['whole_book_discovered'] is False
    discovery_unit(store, other, other['chapters'][0], 5, 23)
    assert coverage(other, store)['whole_book_discovered'] is True


@pytest.mark.parametrize('start,end', [(-1, 999), (0, 999), (False, 12), (0, True), (8, 3), (0, 0)])
def test_invalid_source_ranges_cannot_claim_semantic_coverage(store, start, end):
    book = parse_book('story.txt', b'Alpha. Beta.')
    discovery_unit(store, book, book['chapters'][0], start, end)
    assert coverage(book, store)['semantic_chapters_complete'] == 0


def test_stale_source_units_and_other_book_units_do_not_count(store):
    book = parse_book('story.txt', b'Alpha. Beta.')
    discovery_unit(store, book, book['chapters'][0], source='stale-source-hash')
    other = deepcopy(book)
    other['id'] = 'other-book'
    discovery_unit(store, other, other['chapters'][0])
    assert coverage(book, store)['semantic_chapters_complete'] == 0


def test_local_heuristic_checkpoints_and_units_never_claim_semantic_discovery(store):
    book = parse_book('story.txt', b'Mara said, "Wait."')
    checkpoint(store, book, provider='local')
    discovery_unit(store, book, book['chapters'][0], provider='local')
    assert coverage(book, store)['whole_book_discovered'] is False


def test_legacy_cloud_checkpoint_counts_only_matching_immutable_source(store):
    book = book_with_cast()
    checkpoint(store, book)
    assert coverage(book, store)['whole_book_discovered'] is True
    changed = deepcopy(book)
    changed['chapters'][0]['text'] += ' Newly changed source.'
    # Original baseline still proves the two unchanged chapters only.
    assert coverage(changed, store)['semantic_chapters_complete'] == 2


def test_front_matter_only_book_does_not_claim_comprehensive_character_profiles(store):
    book = parse_book('copyright.txt', b'Copyright 2026.')
    book['chapters'][0]['kind'] = 'front_matter'
    result = coverage(book, store)
    assert result['eligible_chapters'] == 0
    assert result['whole_book_discovered'] is False and result['profiles_provisional'] is True


def test_progressive_cloud_checkpoint_cannot_promote_old_local_completion_flags(store):
    book = book_with_cast()
    checkpoint(store, book, provider='local')
    value = store.analysis_checkpoint(book['id'], 'legacy-key')
    value.update(provider='anthropic', phase='profiles')
    store.save_analysis_checkpoint(book['id'], 'legacy-key', value)
    assert coverage(book, store)['semantic_chapters_complete'] == 0
    assert coverage(book, store)['profiles_provisional'] is True
