"""Voice audition recipes, immutable history and cache tests use synthetic audio."""
from copy import deepcopy
import sqlite3

import pytest

from bardic.audio import DEFAULT_TTS_MODEL
from bardic.importer import parse_book
from bardic.store import Store
from bardic.voice_previews import DEMO_TEXT, VoicePreviewRepository
from test_listening import setup, production_snapshot


def prepare(repo, book, **kwargs):
    return repo.prepare(book['id'], 'gemini', 'Kore', DEFAULT_TTS_MODEL, **kwargs)


def test_simple_and_generic_previews_do_not_use_cast_notes_or_mutate_production(setup):
    store, _, book, calls, render = setup
    repo = VoicePreviewRepository(store)
    before = production_snapshot(store)
    simple = prepare(repo, book, segment_id=book['segments'][0]['id'])
    audio = repo.render(book['id'], simple['id'], 'private-key', synthesizer=render)
    assert simple['source'] == 'passage' and simple['text'] == book['segments'][0]['text']
    assert simple['character_id'] is None and not simple['truncated']
    assert calls[0]['scene'] == {} and 'direction' not in calls[0]['character']
    assert 'cues' not in calls[0]['segment'] and 'direction' not in calls[0]['segment']
    generic = prepare(repo, book)
    repo.render(book['id'], generic['id'], synthesizer=render)
    assert generic['text'] == DEMO_TEXT and generic['source'] == 'demo'
    assert generic['source_anchor'] is None and generic['segment_id'] is None
    assert audio['preview_id'] == simple['id'] and repo.asset_path(book['id'], audio['asset_id']).is_file()
    assert 'fingerprint' not in audio and 'source_anchor' not in audio, 'recipe identity stays in storage'
    assert production_snapshot(store) == before
    with store.connect() as conn:
        assert 'private-key' not in '\n'.join(row[0] for row in conn.execute('SELECT body FROM voice_preview_requests'))


def test_cast_preview_uses_selected_passage_and_unsaved_overrides(setup):
    store, _, book, calls, render = setup
    repo = VoicePreviewRepository(store)
    segment, character = book['segments'][0], book['characters'][0]
    before = deepcopy(store.book(book['id']))
    preview = prepare(repo, book, segment_id=segment['id'], character_id=character['id'],
                      direction='Warm, amused.', segment_direction='A sudden hush.')
    repo.render(book['id'], preview['id'], synthesizer=render)
    assert calls[0]['character']['direction'] == 'Warm, amused.'
    assert calls[0]['segment']['direction'] == 'A sudden hush.'
    assert calls[0]['segment']['cues'] == segment['cues']
    assert calls[0]['scene'] == {'direction': 'An intense confrontation.', 'tone': 'Angry'}
    assert store.book(book['id']) == before


def test_explicit_passage_can_audition_an_unsaved_speaker_choice(setup):
    store, _, book, calls, render = setup
    book['characters'].append({'id': 'new-character', 'name': 'New voice', 'direction': 'Tired'})
    store.save_book(book)
    repo = VoicePreviewRepository(store)
    preview = prepare(repo, book, segment_id=book['segments'][0]['id'], character_id='new-character')
    repo.render(book['id'], preview['id'], synthesizer=render)
    assert preview['source'] == 'passage'
    assert calls[0]['character']['id'] == 'new-character'
    assert calls[0]['segment']['text'] == book['segments'][0]['text']


def test_cast_fallback_uses_first_assigned_passage_else_demo(setup):
    store, _, book, calls, render = setup
    book['characters'].append({'id': 'unused-character', 'name': 'Unseen', 'direction': 'Bright'})
    store.save_book(book)
    repo = VoicePreviewRepository(store)
    character = book['characters'][0]
    expected = next(s for s in book['segments'] if s['speaker_id'] == character['id'])
    preview = prepare(repo, book, character_id=character['id'])
    assert preview['segment_id'] == expected['id']
    demo = prepare(repo, book, character_id='unused-character')
    repo.render(book['id'], demo['id'], synthesizer=render)
    assert demo['source'] == 'demo' and demo['text'] == DEMO_TEXT
    assert calls[0]['character']['direction'] == 'Bright'


def test_excerpt_is_exact_bounded_unicode_prefix_with_source_coordinates(setup):
    store, _, _, calls, render = setup
    text = '🌙 Moonlight on the water. ' * 30
    book = parse_book('long.txt', text.encode())
    store.save_book(book)
    repo = VoicePreviewRepository(store)
    segment = book['segments'][0]
    preview = prepare(repo, book, segment_id=segment['id'])
    # The importer may split this paragraph. Consolidate the synthetic source
    # passage only in this fixture to explicitly exercise the preview bound.
    if len(segment['text']) <= 400:
        book['segments'][0].update(text=text, start=0, end=len(text))
        store.save_book(book)
        preview = prepare(repo, book, segment_id=segment['id'])
    assert preview['truncated'] and 0 < len(preview['text']) <= 400
    assert text.startswith(preview['text'])
    anchor = preview['source_anchor']
    assert text[anchor['start']:anchor['end']] == preview['text']
    repo.render(book['id'], preview['id'], synthesizer=render)
    assert calls[0]['segment']['text'] == preview['text']


def test_snapshot_survives_subsequent_cast_changes_and_no_key_needed_for_cache(setup):
    store, _, book, calls, render = setup
    repo = VoicePreviewRepository(store)
    preview = prepare(repo, book, character_id=book['characters'][0]['id'])
    book['characters'][0]['direction'] = 'Changed after request.'
    store.save_book(book)
    first = repo.render(book['id'], preview['id'], 'key', synthesizer=render)
    assert calls[0]['character']['direction'] == 'An enhanced dramatic voice.'
    later = VoicePreviewRepository(Store(store.root))
    second = later.render(book['id'], preview['id'], synthesizer=lambda *_: pytest.fail('cache must avoid provider'))
    assert first['asset_id'] == second['asset_id'] and 'cache_hit' not in second
    changed = prepare(repo, book, character_id=book['characters'][0]['id'])
    assert changed['id'] != preview['id']


@pytest.mark.parametrize('change', ['voice', 'model', 'provider', 'direction', 'segment_direction'])
def test_changed_performance_inputs_have_distinct_recipe(setup, change):
    store, _, book, _, _ = setup
    repo = VoicePreviewRepository(store)
    args = {'provider': 'gemini', 'voice': 'Kore', 'model': DEFAULT_TTS_MODEL,
            'segment_id': book['segments'][0]['id'], 'character_id': book['characters'][0]['id']}
    first = repo.prepare(book['id'], **args)
    args.update({'voice': {'voice': 'Puck'}, 'model': {'model': 'gemini-3.8-flash-lite-tts'},
                 'provider': {'provider': 'system', 'model': None, 'voice': 'Samantha'},
                 'direction': {'direction': 'Quieter'}, 'segment_direction': {'segment_direction': 'Louder'}}[change])
    assert repo.prepare(book['id'], **args)['id'] != first['id']


def test_corrupt_audio_is_not_replayed_and_retry_retains_history(setup):
    store, _, book, calls, render = setup
    repo = VoicePreviewRepository(store)
    preview = prepare(repo, book)
    audio = repo.render(book['id'], preview['id'], synthesizer=render)
    path = repo.asset_path(book['id'], audio['asset_id'])
    path.write_bytes(b'corrupt')
    assert repo.cached(book['id'], preview['id']) is None
    with pytest.raises(KeyError):
        repo.asset_path(book['id'], audio['asset_id'])
    second = repo.render(book['id'], preview['id'], synthesizer=render)
    assert second['asset_id'] != audio['asset_id'] and len(calls) == 2
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM voice_preview_takes').fetchone()[0] == 2
        for table in ('voice_preview_requests', 'voice_preview_takes'):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(f"UPDATE {table} SET body='{{}}'")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(f'DELETE FROM {table}')


def test_cancellation_before_synthesis_and_during_synthesis(setup):
    store, _, book, calls, render = setup
    repo = VoicePreviewRepository(store)
    preview = prepare(repo, book)
    def cancel():
        raise InterruptedError()
    with pytest.raises(InterruptedError):
        repo.render(book['id'], preview['id'], synthesizer=render, check_cancel=cancel)
    assert calls == []
    state = {'cancelled': False}
    def check():
        if state['cancelled']:
            cancel()
    def stop_after(*args):
        audio = render(*args)
        state['cancelled'] = True
        return audio
    audio = repo.render(book['id'], preview['id'], synthesizer=stop_after, check_cancel=check)
    assert repo.cached(book['id'], preview['id'])['asset_id'] == audio['asset_id']


@pytest.mark.parametrize('args', [
    {'segment_id': 'missing'}, {'character_id': 'missing'}, {'direction': 'Invalid'},
    {'segment_direction': 'Invalid'}, {'provider': 'other'}, {'provider': 'system', 'model': 'wrong'},
])
def test_invalid_requests_rejected_before_retention(setup, args):
    store, _, book, calls, _ = setup
    repo = VoicePreviewRepository(store)
    with pytest.raises((ValueError, KeyError)):
        repo.prepare(book['id'], **({'provider': 'gemini', 'voice': 'Kore'} | args))
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM voice_preview_requests').fetchone()[0] == 0
    assert calls == []


def test_wrong_source_and_cross_book_asset_access_are_rejected(setup):
    store, _, book, _, render = setup
    repo = VoicePreviewRepository(store)
    preview = prepare(repo, book)
    audio = repo.render(book['id'], preview['id'], synthesizer=render)
    other = parse_book('other.txt', b'Another short story.')
    store.save_book(other)
    with pytest.raises(KeyError):
        repo.asset_path(other['id'], audio['asset_id'])
    with pytest.raises(KeyError):
        repo.asset_path(book['id'], '../bad')
    book['segments'][0]['text'] = 'Invented prose'
    store.save_book(book)
    with pytest.raises(ValueError, match='original source'):
        prepare(repo, book, segment_id=book['segments'][0]['id'])
