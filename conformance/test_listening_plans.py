"""Narration operations that need no provider: local plans, and refusals that queue nothing.

Nothing here generates audio. The plans are computed locally. The refusals are only exercised when the
server has no Gemini key and no Breeze URL, so that no request can be billed: a server that has them is
skipped, never driven. The device narrator (`system`) is never used, because it would run the machine's
own speech engine.
"""
from __future__ import annotations

import pytest

from . import helpers


def _status(api):
    return api.call('getStatus').json


def _gemini_unconfigured(api) -> bool:
    return not next(p for p in _status(api)['analysis_providers'] if p['id'] == 'gemini')['has_api_key']


def _breeze_unconfigured(api) -> bool:
    return not _status(api)['breeze_url']


@pytest.fixture()
def no_gemini(api):
    if not _gemini_unconfigured(api):
        pytest.skip('this server has a Gemini key; the request would be billed')


@pytest.fixture()
def no_breeze(api):
    if not _breeze_unconfigured(api):
        pytest.skip('this server has a Breeze URL; the request would reach it')


def _chapter_passages_from(book, passage_id):
    passage = next(s for s in book['passages'] if s['id'] == passage_id)
    chapter = [s for s in book['passages'] if s['chapter_id'] == passage['chapter_id']]
    return chapter[chapter.index(passage):]


# ---------------------------------------------------------------- chapter listening plan

def test_chapter_plan_covers_the_rest_of_the_chapter_and_asks_nothing_of_a_provider(api, txt_book):
    start = txt_book['passages'][3]
    plan = api.call('previewChapterListening', path={'book_id': txt_book['id']}, json={'passage_id': start['id']}).json
    rest = _chapter_passages_from(txt_book, start['id'])
    assert plan['chapter_id'] == start['chapter_id']
    assert plan['passages_total'] == len(rest), 'the plan starts at the passage and runs to the chapter end'
    assert plan['passages_ready'] == 0 and plan['ready_seconds'] == 0, 'nothing has been generated'
    chunks = plan['chunks']
    assert chunks[0]['first_passage_id'] == start['id'] and chunks[-1]['last_passage_id'] == rest[-1]['id']
    assert sum(chunk['passage_count'] for chunk in chunks) == len(rest), 'chunks cover every planned passage once'
    assert plan['requests_needed'] == len(chunks)
    assert plan['limits']['rpd'] == plan['quota']['rpd']
    assert plan['session']['book_id'] == txt_book['id'] and plan['session']['provider'] == 'gemini'


def test_chapter_plan_session_is_deterministic(api, txt_book):
    path = {'book_id': txt_book['id']}
    passage = txt_book['passages'][0]['id']
    first = api.call('previewChapterListening', path=path, json={'passage_id': passage}).json['session']
    again = api.call('previewChapterListening', path=path, json={'passage_id': passage}).json['session']
    assert first == again, 'the narrator session is a deterministic function of book, provider, voice and model'
    other = api.call('previewChapterListening', path=path, json={'passage_id': passage, 'voice': 'Puck'}).json['session']
    assert other['id'] != first['id'] and other['voice'] == 'Puck'
    takes = api.call('listListeningTakes', path=path, query={'session_id': first['id']}).json
    assert takes['session'] == first and takes['takes'] == []


def test_chapter_plan_errors(api, txt_book):
    path = {'book_id': txt_book['id']}
    assert api.call('previewChapterListening', path={'book_id': 'no-such-book'}, json={'passage_id': 'x'},
                    expect=404).code == 'book_not_found'
    assert api.call('previewChapterListening', path=path, json={'passage_id': 'no-such-passage'}, expect=400).code == 'unknown_passage'
    assert api.call('previewChapterListening', path=path, json={'passage_id': txt_book['passages'][0]['id'], 'model': 'no-such-model'},
                    expect=400).code == 'model_unsupported'
    api.call('previewChapterListening', path=path, json={'passage_id': txt_book['passages'][0]['id'], 'provider': 'system'},
             negative=True, expect=422)
    api.call('previewChapterListening', path=path, json={}, negative=True, expect=422)


def test_listening_takes_errors(api, txt_book):
    path = {'book_id': txt_book['id']}
    assert api.call('listListeningTakes', path=path, query={'session_id': 'f' * 64}, expect=404).code == 'listening_session_not_found'
    api.call('listListeningTakes', path=path, query={}, negative=True, expect=422)
    assert api.call('listListeningTakes', path={'book_id': 'no-such-book'}, query={'session_id': 'f' * 64},
                    expect=404).code in ('book_not_found', 'listening_session_not_found')


# ---------------------------------------------------------------- performance plan

@pytest.mark.parametrize('mode, provider', [('simple', 'gemini'), ('cast', 'gemini'), ('simple', 'breeze')])
def test_performance_plan_counts_the_selected_chapters(api, txt_book, mode, provider):
    chapters = [c['id'] for c in txt_book['chapters']]
    plan = api.call('previewPerformance', path={'book_id': txt_book['id']},
                    json={'mode': mode, 'chapter_ids': chapters, 'provider': provider}).json
    assert plan['mode'] == mode and plan['provider'] == provider and plan['chapter_ids'] == chapters
    assert plan['passages_total'] == len(txt_book['passages'])
    assert [c['id'] for c in plan['chapters']] == chapters
    for entry in plan['chapters']:
        assert entry['passages_total'] == len(helpers.passages_of(txt_book, entry['id']))
        assert entry['passages_ready'] <= entry['passages_total']
    assert plan['passages_ready'] + plan['passages_to_generate'] == plan['passages_total']
    assert plan['passages_ready'] == 0, 'nothing is generated by a plan'
    assert plan['expected_seconds'] > 0 and isinstance(plan['problems'], list) and isinstance(plan['notes'], list)


def test_performance_plan_reports_missing_configuration_as_problems_not_errors(api, txt_book, no_gemini):
    plan = api.call('previewPerformance', path={'book_id': txt_book['id']},
                    json={'mode': 'simple', 'chapter_ids': [txt_book['chapters'][0]['id']], 'provider': 'gemini'}).json
    assert 'gemini_key_missing' in [problem['code'] for problem in plan['problems']]
    assert api.call('listPerformances', path={'book_id': txt_book['id']}).json['performances'] == [], 'a plan saves nothing'


def test_performance_plan_errors(api, txt_book):
    path = {'book_id': txt_book['id']}
    good = {'mode': 'simple', 'chapter_ids': [txt_book['chapters'][0]['id']], 'provider': 'gemini'}
    assert api.call('previewPerformance', path=path, json={**good, 'chapter_ids': ['no-such-chapter']}, expect=400).code == 'unknown_chapter'
    assert api.call('previewPerformance', path=path, json={**good, 'model': 'no-such-model'}, expect=400).code == 'model_unsupported'
    assert api.call('previewPerformance', path={'book_id': 'no-such-book'}, json=good, expect=404).code == 'book_not_found'
    for bad in ({**good, 'mode': 'duet'}, {**good, 'chapter_ids': []}, {**good, 'provider': 'no-such-provider'}):
        api.call('previewPerformance', path=path, json=bad, negative=True, expect=422)
    assert api.call('getPerformance', path={**path, 'performance_id': 'no-such-performance'}, expect=404).code == 'performance_not_found'
    assert api.call('updatePerformance', path={**path, 'performance_id': 'no-such-performance'}, json={'name': 'x'},
                    expect=404).code == 'performance_not_found'
    assert api.call('getPerformanceAudio', path={**path, 'performance_id': 'no-such-performance'},
                    expect=404).code == 'performance_not_found'


# ---------------------------------------------------------------- refusals queue nothing

def _jobs(api, book):
    return api.call('listJobs', query={'book_id': book['id']}).json


def test_narration_without_a_gemini_key_is_refused_and_queues_nothing(api, no_gemini):
    book = helpers.import_fresh_txt(api, 'nokey')
    path = {'book_id': book['id']}
    passage, chapter = book['passages'][0]['id'], book['chapters'][0]['id']
    refusals = [
        api.call('listenToPassage', path=path, json={'provider': 'gemini', 'passage_id': passage}, expect=400),
        api.call('startChapterListening', path=path, json={'passage_id': passage}, expect=400),
        api.call('startEnhancedRender', path=path, json={'provider': 'gemini'}, expect=400),
        api.call('startVoicePreview', path=path, json={'provider': 'gemini'}, expect=400),
        api.call('createPerformance', path=path, json={'mode': 'simple', 'chapter_ids': [chapter], 'provider': 'gemini'}, expect=400),
    ]
    assert [reply.code for reply in refusals] == ['gemini_key_missing'] * 5
    assert _jobs(api, book) == [], 'a refused request queues no job'
    assert api.call('listPerformances', path=path).json['performances'] == []


def test_narration_without_a_breeze_url_is_refused_and_queues_nothing(api, no_breeze):
    book = helpers.import_fresh_txt(api, 'nobreeze')
    path = {'book_id': book['id']}
    passage, chapter = book['passages'][0]['id'], book['chapters'][0]['id']
    refusals = [
        api.call('listenToPassage', path=path, json={'provider': 'breeze', 'passage_id': passage}, expect=400),
        api.call('startVoicePreview', path=path, json={'provider': 'breeze'}, expect=400),
        api.call('createPerformance', path=path, json={'mode': 'simple', 'chapter_ids': [chapter], 'provider': 'breeze'}, expect=400),
    ]
    # Both conditions hold (no URL, and so no usable default voice); the contract lists both codes.
    assert {reply.code for reply in refusals} <= {'breeze_url_missing', 'narrator_voice_invalid', 'narrator_voice_missing'}
    assert _jobs(api, book) == []


def test_the_retired_segment_request_names_are_refused_and_queue_nothing(api, no_gemini):
    """Request fields say `passage_id` and `passage_direction`. The old `segment_*` names are unknown fields: 422.

    Each body is valid except for the one retired name, so only that name can be the reason for the refusal.
    """
    book = helpers.import_fresh_txt(api, 'oldnames')
    path = {'book_id': book['id']}
    passage = book['passages'][0]['id']
    bodies = [
        ('listenToPassage', {'provider': 'gemini', 'passage_id': passage, 'segment_id': passage}),
        ('startChapterListening', {'provider': 'gemini', 'passage_id': passage, 'segment_id': passage}),
        ('previewChapterListening', {'passage_id': passage, 'segment_id': passage}),
        ('startEnhancedRender', {'provider': 'gemini', 'passage_id': passage, 'segment_id': passage}),
        ('startVoicePreview', {'provider': 'gemini', 'passage_id': passage, 'segment_id': passage}),
        ('startVoicePreview', {'provider': 'gemini', 'character_id': 'narrator', 'passage_id': passage,
                               'segment_direction': 'Slowly.'}),
    ]
    for operation, body in bodies:
        reply = api.call(operation, path=path, json=body, negative=True, expect=422)
        assert reply.code == 'validation_error', (operation, body, reply.summary())
    diagnostic = {'event': 'buffer_failed', 'book_id': book['id'], 'passage_id': passage, 'segment_id': passage}
    assert api.call('recordDiagnostic', json=diagnostic, negative=True, expect=422).code == 'validation_error'
    assert _jobs(api, book) == [], 'a refused request queues no job'


def test_voice_preview_direction_needs_its_selectors_and_queues_nothing(api, no_gemini):
    """`direction` needs a character; `passage_direction` needs both a passage and a character. Checked before any provider."""
    book = helpers.import_fresh_txt(api, 'nodirection')
    path = {'book_id': book['id']}
    passage = book['passages'][0]['id']
    cases = [
        ({'passage_direction': 'Slowly.'}, 'passage_direction_incomplete'),
        ({'passage_id': passage, 'passage_direction': 'Slowly.'}, 'passage_direction_incomplete'),
        ({'character_id': 'narrator', 'passage_direction': 'Slowly.'}, 'passage_direction_incomplete'),
        ({'direction': 'Softly.'}, 'direction_requires_character'),
    ]
    for body, code in cases:
        reply = api.call('startVoicePreview', path=path, json={'provider': 'gemini', **body}, expect=400)
        assert reply.code == code, (body, reply.summary())
    assert _jobs(api, book) == []


def test_narration_of_missing_things_is_refused(api, txt_book, no_gemini):
    path = {'book_id': txt_book['id']}
    assert api.call('listenToPassage', path={'book_id': 'no-such-book'}, json={'provider': 'gemini', 'passage_id': 'x'},
                    expect=404).code == 'book_not_found'
    assert api.call('startChapterListening', path={'book_id': 'no-such-book'}, json={'passage_id': 'x'}, expect=404).code == 'book_not_found'
    assert api.call('startEnhancedRender', path={'book_id': 'no-such-book'}, json={'provider': 'gemini'}, expect=404).code == 'book_not_found'
    assert api.call('startVoicePreview', path={'book_id': 'no-such-book'}, json={'provider': 'gemini'}, expect=404).code == 'book_not_found'
    assert api.call('preparePerformance', path={**path, 'performance_id': 'no-such-performance'}, expect=404).code == 'performance_not_found'
    assert _jobs(api, txt_book) == []


def test_audio_of_missing_things_is_404(api, txt_book):
    path = {'book_id': txt_book['id']}
    passage = txt_book['passages'][0]['id']
    assert api.call('getPassageAudio', path={**path, 'passage_id': passage}, expect=404).code == 'audio_not_found'
    assert api.call('getPassageAudio', path={**path, 'passage_id': 'no-such-passage'}, expect=404).code == 'passage_not_found'
    assert api.call('getPassageAudio', path={'book_id': 'no-such-book', 'passage_id': passage}, expect=404).code == 'book_not_found'
    for operation, extra in (('getRetainedAudioAsset', 'asset_id'), ('getListeningAudio', 'asset_id'),
                             ('getVoicePreviewAudio', 'asset_id')):
        assert api.call(operation, path={**path, extra: 'no-such-asset'}, expect=404).code == 'audio_not_found', operation
        assert api.call(operation, path={'book_id': 'no-such-book', extra: 'x'}, expect=404).code == 'book_not_found', operation
