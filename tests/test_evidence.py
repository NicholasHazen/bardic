"""Source anchoring accepts presentation changes, never invented story facts."""
from copy import deepcopy

import pytest

from bardic import analysis
from bardic.importer import parse_book


def profile(evidence):
    return {"characters": [{"name": "Mara", "aliases": [], "description": "Vocal traits unknown.", "direction": "Natural.", "evidence": evidence}]}


def test_exact_evidence_keeps_source_offsets_and_typography():
    source = 'Preface. “Wait,” Mara said. Afterward.'
    quote = '“Wait,” Mara said.'
    span, = analysis._evidence_spans([quote], source, "character evidence")
    assert span == {"quote": quote, "start": 9, "end": 27, "match": "exact"}
    assert source[span["start"]:span["end"]] == span["quote"]


@pytest.mark.parametrize("original,quotation", [
    ('“Don’t go,” Mara whispered.', '"Don\'t go," Mara whispered.'),
    ('Mara\u00a0\n\t whispered.', 'Mara whispered.'),
    ('Mara said—then stopped…', 'Mara said-then stopped...'),
    ('An un\u00adusual voice.', 'An unusual voice.'),
    ('Café is quiet.', 'Cafe\u0301 is quiet.'),
    ('Cafe\u0301 is quiet.', 'Café is quiet.'),
    ('a\u0301\u0323 voice', 'a\u0323\u0301 voice'),
    ('Mara\u00a0whispered.', '  Mara whispered.  '),
])
def test_typography_and_whitespace_changes_anchor_original_span(original, quotation):
    source = 'Before. ' + original + ' After.'
    span, = analysis._evidence_spans([quotation], source, "character evidence")
    assert span["quote"] == original
    assert span["start"] == len('Before. ')
    assert span["end"] == len('Before. ' + original)
    assert source[span["start"]:span["end"]] == original
    assert span["match"] == "typography"


@pytest.mark.parametrize("quotation", [
    'mara whispered.',
    'Mara spoke softly.',
    'Mara ... whispered.',
    'Mara said.',
    'whispered Mara.',
    'Mara whispered!',
    'Mara whispers.',
])
def test_words_case_order_and_punctuation_are_never_fuzzy_matched(quotation):
    with pytest.raises(analysis.EvidenceValidationError, match="does not occur"):
        analysis._evidence_spans([quotation], 'Mara whispered.', "character evidence")


@pytest.mark.parametrize("quotation,expected", [
    # Closing mark added where the speech continues in the source.
    ('"The kettle never sings for strangers, Mara."', '“The kettle never sings for strangers, Mara.'),
    # Opening mark added where the excerpt starts mid-speech.
    ('"the lanterns are late again?" she asked.', 'the lanterns are late again?” she asked.'),
    # Both, around the middle of a longer speech.
    ('"Owls prefer the east tower."', 'Owls prefer the east tower.'),
    ("'Owls prefer the east tower.'", 'Owls prefer the east tower.'),
])
def test_quote_mark_added_at_an_excerpt_edge_anchors_the_exact_source(quotation, expected):
    source = ('Tobin sighed. “The kettle never sings for strangers, Mara. Give it time.” '
              '“So the lanterns are late again?” she asked. '
              '“Bats nest low. Owls prefer the east tower. Mind the ladder.”')
    span, = analysis._evidence_spans([quotation], source, "character evidence")
    assert span["quote"] == expected
    assert source[span["start"]:span["end"]] == expected
    assert span["match"] == "quote_boundary"


@pytest.mark.parametrize("quotation", [
    '"Yes."',                      # one word: too weak to anchor without its marks
    '"Mara whispered softly."',    # words still differ
    'Mara "whispered".',           # only edge marks are ever removed
    '""Mara whispered.""',         # at most one mark per edge
])
def test_quote_boundary_tolerance_is_not_fuzzy_matching(quotation):
    with pytest.raises(analysis.EvidenceValidationError, match="does not occur"):
        analysis._evidence_spans([quotation], 'Mara whispered. Yes. Mara whispered.', "character evidence")


def test_quote_mark_present_in_source_is_kept():
    source = 'He said, “Mara whispered.” Then left.'
    span, = analysis._evidence_spans(['"Mara whispered."'], source, "character evidence")
    assert span["quote"] == '“Mara whispered.”' and span["match"] == "typography"


@pytest.mark.parametrize("source,quotation", [('…', '.'), ('é', 'e'), ('ü', 'u')])
def test_normalization_cannot_match_only_part_of_an_expanded_source_glyph(source, quotation):
    with pytest.raises(analysis.EvidenceValidationError):
        analysis._evidence_spans([quotation], source, "character evidence")


def test_offsets_remain_correct_after_prior_normalization_expansions():
    source = 'Café…  an un\u00adusual pause.\n“Mara,” he whispered.'
    span, = analysis._evidence_spans(['"Mara," he whispered.'], source, "character evidence")
    assert span["start"] == source.index('“Mara')
    assert span["end"] == len(source)
    assert span["quote"] == source[span["start"]:]


def test_whitespace_normalization_cannot_expand_evidence_beyond_its_storage_bound():
    with pytest.raises(analysis.EvidenceValidationError, match="anchored source quotation exceeds 600"):
        analysis._evidence_spans(['Mara said.'], 'Mara' + '\n' * 600 + 'said.', "character evidence")


def test_first_source_occurrence_is_explicit_and_source_scope_is_respected():
    source = 'Mara said. Mara said.'
    span, = analysis._evidence_spans(['Mara said.'], source, "character evidence")
    assert span["start"] == 0
    with pytest.raises(analysis.EvidenceValidationError):
        analysis._evidence_spans(['Mara said.'], 'A different chapter.', "character evidence")


@pytest.mark.parametrize("evidence", [None, [], [''] * 9, [None], [1], [' '], ['x' * 601], ['\u00ad']])
def test_invalid_quote_shapes_are_repairable_without_echoing_values(evidence):
    with pytest.raises(analysis.EvidenceValidationError):
        analysis._evidence_spans(evidence, 'The source.', "character evidence")


def test_errors_identify_response_location_without_echoing_book_or_model_text():
    with pytest.raises(analysis.EvidenceValidationError) as caught:
        analysis._cast_result(profile(['Mara said.', 'Untrusted model secret.']), 'Mara said. Confidential book content.')
    message = str(caught.value)
    assert 'profile 1' in message and 'quote 2' in message
    assert 'Untrusted model secret' not in message
    assert 'Confidential book content' not in message
    assert caught.value.index == 1


def test_cast_evidence_is_canonicalized_to_original_source():
    source = '“Wait,” Mara\u00a0said.'
    cast = analysis._cast_result(profile(['"Wait," Mara said.']), source)
    assert cast[0]["evidence"] == [source]


@pytest.mark.parametrize('quotation', ['Mara said.\nLater she left.', 'Mara said. Later she left.'])
def test_global_profile_cannot_quote_across_artificial_candidate_boundaries(quotation):
    with pytest.raises(analysis.EvidenceValidationError, match='single supplied candidate quotation'):
        analysis._profile_result(profile([quotation]), ['Mara said.', 'Later she left.'])


def test_global_profile_can_use_individual_excerpt_or_typography_equivalent():
    cast = analysis._profile_result(profile(['Mara said.', '"Wait," Mara said.']),
                                    ['At noon, Mara said.', '“Wait,” Mara said.'])
    assert cast[0]['evidence'] == ['Mara said.', '“Wait,” Mara said.']


def test_global_profile_can_reuse_evidence_iterable_for_multiple_quotes():
    cast = analysis._profile_result(profile(['Mara said.', 'Mara whispered.']),
                                    iter(['Mara said.', 'Mara whispered.']))
    assert cast[0]['evidence'] == ['Mara said.', 'Mara whispered.']


def test_global_profile_reports_the_actual_rejected_quote_without_echoing_it():
    with pytest.raises(analysis.EvidenceValidationError) as caught:
        analysis._profile_result(profile(['Mara said.', 'Private fabricated content.']), ['Mara said.'])
    assert caught.value.index == 1
    assert 'quote 2' in str(caught.value)
    assert 'Private fabricated content.' not in str(caught.value)


@pytest.mark.parametrize('evidence', [[], None, ['Mara said.'] * 9])
def test_global_profile_enforces_the_existing_evidence_shape(evidence):
    with pytest.raises(analysis.EvidenceValidationError, match='1–8'):
        analysis._profile_result(profile(evidence), ['Mara said.'])


def test_passage_evidence_is_canonicalized_without_rewriting_book():
    book = analysis.analyze_book(parse_book('example.txt', '“Wait,” Mara said.'.encode()), 'local')
    original_chapters = deepcopy(book['chapters'])
    original_spans = [(s['start'], s['end'], s['text']) for s in book['segments']]
    mara = next(c for c in book['characters'] if c['name'] == 'Mara')
    response = {
        'summary': 'A request.', 'tone': 'Quiet.', 'direction': 'Natural.', 'scene_starts': [],
        'segments': [
            {'id': s['id'], 'speaker_id': mara['id'] if s['kind'] == 'dialogue' else 'narrator',
             'confidence': 0.9, 'direction': 'Natural.', 'cues': [], 'evidence': ['"Wait," Mara said.']}
            for s in book['segments']
        ],
    }
    analysis._apply_annotations(book, book['scenes'][0], book['segments'], response, {})
    assert all(s['evidence'] == ['“Wait,” Mara said.'] for s in book['segments'])
    assert book['chapters'] == original_chapters
    assert [(s['start'], s['end'], s['text']) for s in book['segments']] == original_spans


def test_one_bounded_repair_regenerates_quotes_from_supplied_source():
    calls = []

    def call(note):
        calls.append(note)
        return profile(['Mara says.' if not note else 'Mara said.'])

    result = analysis._repairable_request(call, lambda response: analysis._cast_result(response, 'Mara said.'))
    assert result[0]['evidence'] == ['Mara said.']
    assert len(calls) == 2 and calls[0] == ''
    assert 'SOURCE EVIDENCE CORRECTION' in calls[1]
    assert 'profile 1' in calls[1]
    assert 'check every quotation' in calls[1]
    assert 'Mara says.' not in calls[1]


def test_director_instruction_forbids_adding_edge_quote_marks():
    assert 'do not add opening or closing quotation marks' in analysis.DIRECTOR_INSTRUCTION


def test_second_invalid_response_stops_instead_of_dropping_or_trusting_evidence():
    calls = []

    def call(note):
        calls.append(note)
        return profile(['Invented quotation.'])

    with pytest.raises(analysis.EvidenceValidationError, match='single evidence repair attempt also failed'):
        analysis._repairable_request(call, lambda response: analysis._cast_result(response, 'Mara said.'))
    assert len(calls) == 2


def test_valid_evidence_needs_only_one_request():
    calls = []

    def call(note):
        calls.append(note)
        return profile(['Mara said.'])

    analysis._repairable_request(call, lambda response: analysis._cast_result(response, 'Mara said.'))
    assert calls == ['']


def test_unrelated_validation_errors_do_not_trigger_an_evidence_repair():
    calls = []

    def call(note):
        calls.append(note)
        return {'characters': None}

    with pytest.raises(ValueError, match='invalid cast list'):
        analysis._repairable_request(call, lambda response: analysis._cast_result(response, 'Mara said.'))
    assert calls == ['']


def test_cancellation_between_attempts_stops_before_another_paid_request():
    calls = []
    stop = False

    def call(note):
        calls.append(note)
        return profile(['Invented quotation.'])

    def validate(response):
        nonlocal stop
        stop = True
        return analysis._cast_result(response, 'Mara said.')

    with pytest.raises(analysis.AnalysisCancelled):
        analysis._repairable_request(call, validate, cancelled=lambda: stop)
    assert calls == ['']
