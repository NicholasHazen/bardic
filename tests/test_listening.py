"""Simple narration uses isolated storage and offline fake audio only."""
from copy import deepcopy
import json
import sqlite3

import pytest

from bardic.audio import DEFAULT_TTS_MODEL, SYSTEM_MODEL, render_fingerprint
from bardic.importer import parse_book
from bardic.listening import ListeningRepository
from bardic.store import Store
from test_audio import wav_bytes


@pytest.fixture
def setup(tmp_path):
    book = parse_book('story.txt', 'Chapter One\n\nMara looked up.\n\n“Stay,” she said.\n\nChapter Two\n\nThe moon shone.'.encode())
    book['characters'][0].update(direction='An enhanced dramatic voice.', voice='Puck')
    book['scenes'][0].update(direction='An intense confrontation.', tone='Angry')
    book['segments'][0].update(direction='Whisper fiercely.', cues=['laughing'])
    store = Store(tmp_path)
    store.save_book(book)
    repository = ListeningRepository(store)
    calls = []

    def render(segment, character, scene, provider, model, key, path):
        calls.append({'segment': deepcopy(segment), 'character': deepcopy(character), 'scene': deepcopy(scene),
                      'provider': provider, 'model': model, 'key': key})
        path.write_bytes(wav_bytes(frames=2400 + len(calls)))
        return {'fingerprint': render_fingerprint(segment, character, scene, provider, model),
                'duration': .1, 'provider': provider, 'model': model,
                'voice': character.get('system_voice') if provider == 'system' else character.get('voice')}

    return store, repository, book, calls, render


def production_snapshot(store):
    with store.connect() as conn:
        return {table: conn.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
                for table in ('books','takes','artifact_versions','artifact_heads','artifact_dependencies',
                              'analysis_checkpoints','character_references')}


def test_one_voice_verbatim_transcript_and_no_enhanced_production_mutation(setup):
    store, repo, book, calls, render = setup
    original = deepcopy(store.book(book['id']))
    before = production_snapshot(store)
    session = repo.session(book['id'], 'gemini', 'Kore', DEFAULT_TTS_MODEL)
    assert repo.takes(book['id'], session['id']) == {'session':session, 'takes':[]}
    for segment in book['segments'][:2]:
        audio = repo.render_passage(book['id'], session['id'], segment['id'], 'private-test-key', synthesizer=render)
        assert audio['mode'] == 'simple' and audio['available'] is True
        assert audio['voice'] == 'Kore' and audio['duration'] > 0
        assert audio['url'].startswith(f"/api/books/{book['id']}/listen/audio/")
        assert repo.asset_path(book['id'], audio['asset_id']).read_bytes()
    assert len(calls) == 2
    for segment, call in zip(book['segments'], calls):
        assert call['segment'] == {'id':segment['id'], 'text':segment['text']}
        assert call['scene'] == {}
        assert call['character'] == {'id':'simple-narrator', 'voice':'Kore', 'system_voice':'Kore'}
    assert len(repo.takes(book['id'], session['id'])['takes']) == 2
    assert store.book(book['id']) == original
    assert production_snapshot(store) == before
    with store.connect() as conn:
        bodies = '\n'.join(row[0] for row in conn.execute('SELECT body FROM listening_takes'))
    assert 'private-test-key' not in bodies
    assert store.jobs(book['id']) == []


def test_cached_recipe_survives_restart_enhanced_edits_and_voice_round_trip(setup):
    store, repo, book, calls, render = setup
    segment = book['segments'][0]
    first = repo.session(book['id'], 'gemini', 'Kore')
    audio = repo.render_passage(book['id'], first['id'], segment['id'], synthesizer=render)
    original_bytes = repo.asset_path(book['id'], audio['asset_id']).read_bytes()
    # A fresh repository and restored narrator choice find the same exact recipe.
    repo = ListeningRepository(store)
    assert repo.session(book['id'], 'gemini', 'Kore')['id'] == first['id']
    changed = store.book(book['id'])
    changed['characters'][0]['direction'] = 'A completely different enhanced performance.'
    changed['segments'][0].update(direction='Shout.', cues=['sobbing'], speaker_id='unassigned')
    changed['scenes'][0]['tone'] = 'Joyful'
    changed['revision'] += 1
    store.save_book(changed)
    assert repo.render_passage(book['id'], first['id'], segment['id'], synthesizer=render)['asset_id'] == audio['asset_id']
    assert len(calls) == 1
    other = repo.session(book['id'], 'gemini', 'Puck')
    replacement = repo.render_passage(book['id'], other['id'], segment['id'], synthesizer=render)
    assert replacement['recipe'] != audio['recipe'] and len(calls) == 2
    assert repo.cached(book['id'], first['id'], segment['id'])['asset_id'] == audio['asset_id']
    assert repo.asset_path(book['id'], audio['asset_id']).read_bytes() == original_bytes
    assert len(repo.takes(book['id'], first['id'])['takes']) == 1
    assert len(repo.takes(book['id'], other['id'])['takes']) == 1


def test_device_cached_takes_do_not_query_or_require_installed_voices(setup, monkeypatch):
    store, repo, book, calls, render = setup
    monkeypatch.setattr('bardic.audio.list_system_voices', lambda: pytest.fail('A cache read cannot query device voices'))
    session = repo.session(book['id'], 'system', 'Samantha')
    assert session['model'] == SYSTEM_MODEL
    segment = book['segments'][0]
    audio = repo.render_passage(book['id'], session['id'], segment['id'], synthesizer=render)
    assert repo.cached(book['id'], session['id'], segment['id'])['asset_id'] == audio['asset_id']
    assert len(calls) == 1 and calls[0]['key'] is None


def test_model_and_source_changes_need_distinct_recipes(setup):
    store, repo, book, calls, render = setup
    segment = book['segments'][0]
    first = repo.session(book['id'], 'gemini', 'Kore')
    old = repo.render_passage(book['id'], first['id'], segment['id'], synthesizer=render)
    lite = repo.session(book['id'], 'gemini', 'Kore', 'gemini-3.8-flash-lite-tts')
    assert lite['id'] != first['id']
    assert repo.cached(book['id'], lite['id'], segment['id']) is None
    edited = store.book(book['id'])
    target = edited['segments'][0]
    chapter = next(c for c in edited['chapters'] if c['id'] == target['chapter_id'])
    old_text = target['text']
    replacement = 'Z' + old_text[1:]
    chapter['text'] = chapter['text'][:target['start']] + replacement + chapter['text'][target['end']:]
    target['text'] = replacement
    store.save_book(edited)
    assert repo.cached(book['id'], first['id'], segment['id']) is None
    new = repo.render_passage(book['id'], first['id'], segment['id'], synthesizer=render)
    assert new['recipe'] != old['recipe']
    assert repo.asset_path(book['id'], old['asset_id']).is_file()


def test_missing_or_invalid_audio_regenerates_without_overwriting_earlier_rows(setup):
    store, repo, book, calls, render = setup
    session = repo.session(book['id'], 'gemini', 'Kore')
    segment = book['segments'][0]
    old = repo.render_passage(book['id'], session['id'], segment['id'], synthesizer=render)
    repo.asset_path(book['id'], old['asset_id']).write_bytes(b'broken audio')
    assert repo.cached(book['id'], session['id'], segment['id']) is None
    new = repo.render_passage(book['id'], session['id'], segment['id'], synthesizer=render)
    assert new['asset_id'] != old['asset_id'] and len(calls) == 2
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM listening_takes').fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute("UPDATE listening_takes SET body='{}'")
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute('DELETE FROM listening_takes')


def test_cancellation_and_renderer_failure_do_not_publish_incomplete_takes(setup):
    store, repo, book, calls, render = setup
    session = repo.session(book['id'], 'gemini', 'Kore')
    segment = book['segments'][0]
    def cancelled():
        raise InterruptedError('Stopped')
    with pytest.raises(InterruptedError):
        repo.render_passage(book['id'], session['id'], segment['id'], synthesizer=render, check_cancel=cancelled)
    assert not calls
    def fails(*_args):
        raise ValueError('Offline synthesis failed')
    with pytest.raises(ValueError, match='Offline synthesis'):
        repo.render_passage(book['id'], session['id'], segment['id'], synthesizer=fails)
    assert repo.takes(book['id'], session['id'])['takes'] == []


def test_completed_take_is_retained_if_stop_arrives_during_synthesis(setup):
    store, repo, book, calls, render = setup
    session = repo.session(book['id'], 'gemini', 'Kore')
    stopped = False
    def check():
        if stopped:
            raise InterruptedError()
    def render_then_stop(*args):
        nonlocal stopped
        result = render(*args)
        stopped = True
        return result
    audio = repo.render_passage(book['id'], session['id'], book['segments'][0]['id'],
                                synthesizer=render_then_stop, check_cancel=check)
    assert stopped and repo.asset_path(book['id'], audio['asset_id']).is_file()


def test_source_anchors_and_cross_book_ownership_are_enforced(setup):
    store, repo, book, calls, render = setup
    session = repo.session(book['id'], 'gemini', 'Kore')
    audio = repo.render_passage(book['id'], session['id'], book['segments'][0]['id'], synthesizer=render)
    other = parse_book('other.txt', b'Another unrelated source.')
    store.save_book(other)
    with pytest.raises(KeyError):
        repo.get_session(other['id'], session['id'])
    with pytest.raises(KeyError):
        repo.asset_path(other['id'], audio['asset_id'])
    for identifier in ('../escape', 'g' * 64, '../' + audio['asset_id']):
        with pytest.raises(KeyError):
            repo.asset_path(book['id'], identifier)
    with pytest.raises(KeyError):
        repo.render_passage(book['id'], session['id'], 'missing', synthesizer=render)
    malformed = store.book(book['id'])
    malformed['segments'][0]['text'] = 'Text absent from the original source'
    store.save_book(malformed)
    with pytest.raises(ValueError, match='original source'):
        repo.render_passage(book['id'], session['id'], malformed['segments'][0]['id'], synthesizer=render)


@pytest.mark.parametrize('provider,voice,model', [('openai','Kore',None),('gemini','Kore','bad-model'),
    ('system','Samantha','cloud-model'),('gemini','x'*257,None),('gemini',123,None),
    ('gemini','a-custom-voice','gemini-3.1-flash-tts-preview')])
def test_invalid_configuration_never_creates_a_session(setup, provider, voice, model):
    store, repo, book, calls, render = setup
    with pytest.raises(ValueError):
        repo.session(book['id'], provider, voice, model)
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM listening_sessions').fetchone()[0] == 0
    assert not calls
