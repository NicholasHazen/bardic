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


def stored(store, audio):
    """The retained take record behind a presented audio object (newest when bytes repeat)."""
    book_id = audio['url'].split('/')[3]
    with store.connect() as conn:
        row = conn.execute("""SELECT body FROM listening_takes WHERE book_id=? AND session_id=? AND segment_id=?
            AND asset_id=? ORDER BY rowid DESC LIMIT 1""", (book_id, audio['session_id'], audio['segment_id'],
                                                            audio['asset_id'])).fetchone()
    return json.loads(row[0])


PUBLIC = {'url', 'asset_id', 'duration', 'provider', 'model', 'voice', 'created_at', 'session_id', 'segment_id',
          'reuse', 'resource_usage', 'provider_timing', 'breeze', 'voice_revision'}


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
        assert set(audio) <= PUBLIC, 'recipe hashes, anchors and lookup keys stay in storage'
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
    assert stored(store, replacement)['recipe'] != stored(store, audio)['recipe'] and len(calls) == 2
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
    assert stored(store, new)['recipe'] != stored(store, old)['recipe']
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
    with pytest.raises(ValueError, match='No passage'):
        repo.render_passage(book['id'], session['id'], 'missing', synthesizer=render)
    malformed = store.book(book['id'])
    malformed['segments'][0]['text'] = 'Text absent from the original source'
    store.save_book(malformed)
    with pytest.raises(ValueError, match='original source'):
        repo.render_passage(book['id'], session['id'], malformed['segments'][0]['id'], synthesizer=render)
    with pytest.raises(ValueError) as error:
        repo.render_passage(book['id'], session['id'], malformed['segments'][0]['id'], synthesizer=render)
    assert error.value.code == 'passage_source_mismatch'


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


def test_same_text_reuses_across_passage_ids_books_and_restart_with_real_provenance(setup):
    store, repo, book, calls, render = setup
    segment = book['segments'][0]
    session = repo.session(book['id'], 'gemini', 'Kore')
    first = repo.render_passage(book['id'], session['id'], segment['id'], synthesizer=render)
    with store.connect() as conn:
        original_id, original_body = conn.execute('SELECT id,body FROM listening_takes').fetchone()
    # New source IDs and offsets must not become new paid synthesis requests.
    repeated = parse_book('repeat.txt', f"{segment['text']}\n\n{segment['text']}".encode())
    store.save_book(repeated)
    repo = ListeningRepository(Store(store.root))
    repeated_session = repo.session(repeated['id'], 'gemini', 'Kore')
    first_record = stored(store, first)
    for target in repeated['segments']:
        reused = repo.render_passage(repeated['id'], repeated_session['id'], target['id'],
                                     synthesizer=lambda *_: pytest.fail('Equivalent speech should be reused'))
        assert 'cache_hit' not in reused, 'a cache hit is never a field of the audio'
        assert reused['asset_id'] == first['asset_id']
        record = stored(store, reused)
        assert record['fingerprint'] == first_record['fingerprint'], 'Do not invent a new producer fingerprint'
        assert record['source_anchor']['fingerprint'] != first_record['fingerprint']
        assert record['source_anchor']['book_id'] == repeated['id']
        assert record['source_anchor']['start'] == target['start']
        assert record['recipe'] != first_record['recipe']
        assert reused['reuse']['fingerprint'] == first_record['fingerprint']
        with store.connect() as conn:
            reused_row = conn.execute('SELECT body FROM listening_takes WHERE id=?', (reused['reuse']['take_id'],)).fetchone()
        assert reused_row, 'Every reuse dependency references an actual retained take'
        assert json.loads(reused_row[0])['asset_id'] == first['asset_id']
    assert len(calls) == 1
    assert len(repo.takes(repeated['id'], repeated_session['id'])['takes']) == len(repeated['segments']) == 2
    assert repo.asset_path(book['id'], first['asset_id']).stat().st_ino != repo.asset_path(repeated['id'], first['asset_id']).stat().st_ino
    with store.connect() as conn:
        assert conn.execute('SELECT body FROM listening_takes WHERE id=?', (original_id,)).fetchone()[0] == original_body


def test_legacy_source_cache_hit_builds_shared_index_without_rewriting_take(setup):
    store, repo, book, calls, render = setup
    segment = book['segments'][0]
    session = repo.session(book['id'], 'gemini', 'Kore')
    # Build a realistic pre-index row without new metadata using only synthetic
    # test data. The repository never removes immutability guards in production.
    from bardic.listening import _hash
    from bardic.take_archive import produce_take
    _, passage, narrator, identity, recipe = repo._inputs(book['id'], session['id'], segment['id'])
    old = produce_take(passage, narrator, {}, 'gemini', session['model'], None,
                       store.root / 'listen-audio' / book['id'], synthesizer=render)
    old.update(session_id=session['id'], segment_id=segment['id'], recipe=recipe,
               source_anchor=identity, created_at='2026-09-27T00:00:00+00:00')
    old_body = json.dumps(old)
    with store.connect() as conn:
        conn.execute('INSERT INTO listening_takes VALUES (?,?,?,?,?,?,?)',
                     (_hash([book['id'], recipe, old['asset_id']]), book['id'], session['id'],
                      segment['id'], recipe, old['asset_id'], old_body))
    assert repo.cached(book['id'], session['id'], segment['id'])['asset_id'] == old['asset_id']
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM listening_synthesis_cache').fetchone()[0] == 1
        assert conn.execute('SELECT body FROM listening_takes').fetchone()[0] == old_body
    another = parse_book('another.txt', segment['text'].encode())
    store.save_book(another)
    another_session = repo.session(another['id'], 'gemini', 'Kore')
    assert repo.cached(another['id'], another_session['id'], another['segments'][0]['id'])['asset_id'] == old['asset_id']
    assert len(calls) == 1


@pytest.mark.parametrize('change', ['voice', 'model', 'provider', 'text', 'audio_version', 'cache_version'])
def test_shared_cache_never_reuses_changed_speech_inputs(setup, monkeypatch, change):
    store, repo, book, calls, render = setup
    segment = book['segments'][0]
    session = repo.session(book['id'], 'gemini', 'Kore')
    old = repo.render_passage(book['id'], session['id'], segment['id'], synthesizer=render)
    text = segment['text'] + (' Again.' if change == 'text' else '')
    another = parse_book('another.txt', text.encode())
    store.save_book(another)
    if change == 'audio_version':
        monkeypatch.setattr('bardic.audio._RECIPE_VERSION', 999)
    if change == 'cache_version':
        monkeypatch.setattr('bardic.listening.SYNTHESIS_CACHE_VERSION', 999)
    other = repo.session(another['id'], 'system' if change == 'provider' else 'gemini',
                         'Puck' if change == 'voice' else 'Kore',
                         'gemini-3.8-flash-lite-tts' if change == 'model' else None)
    assert repo.cached(another['id'], other['id'], another['segments'][0]['id']) is None
    new = repo.render_passage(another['id'], other['id'], another['segments'][0]['id'], synthesizer=render)
    assert len(calls) == 2
    assert stored(store, new)['synthesis_key'] != stored(store, old)['synthesis_key']
    assert 'reuse' not in new


def test_valid_but_tampered_wav_never_counts_as_cache_hit(setup):
    store, repo, book, calls, render = setup
    segment = book['segments'][0]
    session = repo.session(book['id'], 'gemini', 'Kore')
    first = repo.render_passage(book['id'], session['id'], segment['id'], synthesizer=render)
    # The WAV decoder accepts these bytes; the retained content hash must not.
    repo.asset_path(book['id'], first['asset_id']).write_bytes(wav_bytes(frames=5000))
    assert repo.cached(book['id'], session['id'], segment['id']) is None
    another = parse_book('another.txt', segment['text'].encode())
    store.save_book(another)
    other = repo.session(another['id'], 'gemini', 'Kore')
    assert repo.cached(another['id'], other['id'], another['segments'][0]['id']) is None
    second = repo.render_passage(another['id'], other['id'], another['segments'][0]['id'], synthesizer=render)
    assert second['asset_id'] != first['asset_id'] and len(calls) == 2


def test_failed_reuse_copy_does_not_start_paid_fallback_or_publish_take(setup, monkeypatch):
    store, repo, book, calls, render = setup
    segment = book['segments'][0]
    session = repo.session(book['id'], 'gemini', 'Kore')
    repo.render_passage(book['id'], session['id'], segment['id'], synthesizer=render)
    another = parse_book('another.txt', segment['text'].encode())
    store.save_book(another)
    other = repo.session(another['id'], 'gemini', 'Kore')
    def full_disk(*_args):
        raise OSError('Test disk full')
    monkeypatch.setattr('bardic.listening.shutil.copyfile', full_disk)
    with pytest.raises(OSError, match='Test disk full'):
        repo.render_passage(another['id'], other['id'], another['segments'][0]['id'], synthesizer=render)
    assert len(calls) == 1
    assert repo.takes(another['id'], other['id'])['takes'] == []


def test_cache_version_bump_retains_new_identity_even_when_audio_bytes_are_identical(setup, monkeypatch):
    store, repo, book, calls, render = setup
    session = repo.session(book['id'], 'gemini', 'Kore')
    segment = book['segments'][0]
    fixed_audio = wav_bytes(frames=2500)
    def deterministic(*args):
        metadata = render(*args)
        args[-1].write_bytes(fixed_audio)
        return metadata
    first = repo.render_passage(book['id'], session['id'], segment['id'], synthesizer=deterministic)
    with store.connect() as conn:
        old_id, old_body = conn.execute('SELECT id,body FROM listening_takes').fetchone()
    monkeypatch.setattr('bardic.listening.SYNTHESIS_CACHE_VERSION', 2)
    second = repo.render_passage(book['id'], session['id'], segment['id'], synthesizer=deterministic)
    old, new = json.loads(old_body), stored(store, second)
    assert second['asset_id'] == first['asset_id'] and new['recipe'] == old['recipe']
    assert new['synthesis_key'] != old['synthesis_key']
    assert len(calls) == 2 and 'reuse' not in second
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM listening_takes').fetchone()[0] == 2
        assert conn.execute('SELECT body FROM listening_takes WHERE id=?', (old_id,)).fetchone()[0] == old_body
    assert repo.cached(book['id'], session['id'], segment['id'])['created_at'] == new['created_at']
    assert repo.render_passage(book['id'], session['id'], segment['id'], synthesizer=deterministic) is not None
    assert len(calls) == 2, 'the new identity is a cache hit'
