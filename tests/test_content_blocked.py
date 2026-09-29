"""Gemini content-policy blocks: recognition, one split, a fallback narrator and durable memory.

Every test is offline. Gemini is a fake at the HTTP layer (``httpx.post``), so the real error parsing runs;
the fallback device narrator is a fake synthesizer. Prose is original and synthetic. No provider is contacted.
"""
from __future__ import annotations

import base64
import json
import shutil

import httpx
import pytest

from bardic import audio, tts_limits
from bardic.audio import ContentBlocked, DEFAULT_TTS_MODEL, render_fingerprint
from bardic.chunking import split_point
from bardic.listening import ListeningRepository
from bardic.tts_limits import requests_today
from test_audio import wav_bytes
from test_chapter_listening import (RATE, chapter_request, client, fake_chunk_synthesizer, import_story,  # noqa: F401
                                    speech_wav, start, wait_job)

ECHO = 'ECHOED-REQUEST-TEXT'
CHUNKING = {'ramp_seconds': [], 'target_seconds': 120, 'concurrency': 1}


class Gemini:
    """A fake Gemini HTTP endpoint and the fallback device narrator, with the requests each received."""

    def __init__(self, monkeypatch, blocked):
        self.texts, self.fallback, self.blocked = [], [], blocked
        monkeypatch.setattr(audio.httpx, 'post', self.post)
        base = fake_chunk_synthesizer([])

        def routed(segment, character, scene, provider, model, key, path, **kwargs):
            if provider == 'gemini':
                return audio.synthesize(segment, character, scene, provider, model, key, path, **kwargs)
            self.fallback.append({'id': segment['id'], 'text': segment['text'], 'provider': provider})
            return base(segment, character, scene, provider, model, key, path, **kwargs)

        monkeypatch.setattr('bardic.chapter_listening.synthesize', routed)

    def post(self, url, **kwargs):
        text = kwargs['json']['input'][0]['content'][0]['text']
        self.texts.append(text)
        if self.blocked(text, len(self.texts)):
            # The real reply echoes nothing useful; this one echoes request text to prove it is never kept.
            return httpx.Response(400, json={'error': {'code': 'content_blocked',
                                                       'message': f'Request blocked for an unspecified policy reason. {ECHO} {text[:30]}'}})
        data, _ = speech_wav(text)
        return httpx.Response(200, json={'steps': [{'type': 'model_output', 'content': [
            {'type': 'audio', 'mime_type': 'audio/wav', 'data': base64.b64encode(data).decode()}]}]})


@pytest.fixture(autouse=True)
def device_narration(monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda name: '/fake/' + name if name in {'say', 'ffmpeg'} else None)


def poison_marker(book, index=4):
    marker = f'step {index} along'
    segment = next(s for s in book['segments'] if marker in s['text'])
    return marker, segment


def takes(client, book, session):
    return client.get(f"/api/books/{book['id']}/listen/takes", params={'session_id': session['id']}).json()['takes']


def ids_between(book, first, last):
    ids = [s['id'] for s in book['segments']]
    return ids[ids.index(first):ids.index(last) + 1]


# Recognition ----------------------------------------------------------------

RECIPE = {'model': DEFAULT_TTS_MODEL, 'text': 'text', 'style': '', 'voice': 'Kore'}


def test_content_blocked_is_recognised_and_only_a_fixed_message_is_kept(monkeypatch):
    tts_limits.LIMITER.reset()
    body = {'error': {'code': 'content_blocked', 'message': f'blocked. {ECHO}'}}
    monkeypatch.setattr(audio.httpx, 'post', lambda *a, **k: httpx.Response(400, json=body))
    with pytest.raises(ContentBlocked) as error:
        audio._generate_gemini(RECIPE, 'private-test-key', pace=False)
    assert error.value.code == 'content_blocked'
    assert str(error.value) == audio.GEMINI_CONTENT_BLOCKED_MESSAGE
    assert ECHO not in str(error.value) and 'not a model, voice or length problem' in str(error.value)
    assert isinstance(error.value, audio.AudioError)
    tts_limits.LIMITER.reset()


@pytest.mark.parametrize('response', [
    httpx.Response(400, json={'error': {'code': 400, 'message': ECHO}}),
    httpx.Response(400, json={'error': {'code': 'invalid_argument', 'message': ECHO}}),
    httpx.Response(400, json={'error': 'content_blocked'}),
    httpx.Response(400, json=['content_blocked']),
    httpx.Response(400, text=ECHO),
    httpx.Response(500, json={'error': {'code': 'content_blocked'}}),
])
def test_unrelated_errors_keep_the_generic_hint(monkeypatch, response):
    tts_limits.LIMITER.reset()
    monkeypatch.setattr(audio.httpx, 'post', lambda *a, **k: response)
    with pytest.raises(audio.AudioError) as error:
        audio._generate_gemini(RECIPE, 'private-test-key', pace=False)
    assert not isinstance(error.value, ContentBlocked) and ECHO not in str(error.value)
    assert f'HTTP {response.status_code}' in str(error.value)
    if response.status_code == 400:
        assert 'Check the model, voice, and passage length.' in str(error.value)
    tts_limits.LIMITER.reset()


def test_split_point_prefers_a_strong_boundary_near_the_middle():
    texts = [f'Sentence {i} of the test chapter goes here.' for i in range(20)]
    chapter, segments = '', []
    for index, text in enumerate(texts):
        start = len(chapter)
        chapter += text
        segments.append({'id': f's{index}', 'chapter_id': 'c', 'start': start, 'end': len(chapter), 'text': text,
                         'scene_id': 'a' if index < 12 else 'b'})
        chapter += '\n\n' if index in (8, 11) else ' '
    # Boundaries after s8 (paragraph, 9 of 20 passages) and s11 (scene change, 12 of 20): the scene change is
    # farther from the middle but inside the window and much stronger.
    assert split_point(segments, chapter, 0, 19) == 11
    assert split_point(segments, chapter, 0, 1) == 0 and split_point(segments, chapter, 3, 3) is None


def test_fallback_narrator_prefers_the_device_voice_then_breeze_then_none(client, monkeypatch):
    runtime = client.app.state.runtime
    book = import_story(client, 2)
    device = runtime.fallback_narrator(book['id'])
    assert device['provider'] == 'system' and device['model'] == 'macos-say' and device['voice'] == ''
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    assert runtime.fallback_narrator(book['id']) is None, 'neither a device voice nor a Breeze server'
    monkeypatch.setattr(runtime, 'breeze_url', lambda: 'http://breeze.test')
    pin = {'id': 'bardic-default', 'revision': 'a' * 64, 'seed': 7}
    monkeypatch.setattr(runtime, 'narrator_choice', lambda provider, voice: ('bardic-default', pin))
    breeze = runtime.fallback_narrator(book['id'])
    assert breeze['provider'] == 'breeze' and breeze['voice'] == 'bardic-default'
    assert breeze['session_id'] != device['session_id']
    monkeypatch.setattr(runtime, 'narrator_choice', lambda provider, voice: (_ for _ in ()).throw(ValueError('no default voice')))
    assert runtime.fallback_narrator(book['id']) is None


# One split ---------------------------------------------------------------------

def test_a_blocked_chunk_is_split_once_and_both_halves_are_kept(client, monkeypatch):
    gemini = Gemini(monkeypatch, lambda text, n: n == 1)
    book = import_story(client)
    result = start(client, book, chunking=CHUNKING)
    job = wait_job(client, result['job']['id'])
    assert job['status'] == 'completed', job
    first, one, two, *rest = job['chunks']
    assert first['status'] == 'blocked' and first['split_into'] == 2 and 'split' not in first
    assert one['status'] == two['status'] == 'done' and one['split'] and two['split']
    assert one['first_segment_id'] == first['first_segment_id'] and two['last_segment_id'] == first['last_segment_id']
    assert ids_between(book, one['first_segment_id'], one['last_segment_id'])[-1] != first['last_segment_id']
    assert first['segment_count'] == one['segment_count'] + two['segment_count']
    # Exactly the original plus its two halves for that chunk, and the daily count includes all of them.
    assert len(gemini.texts) == len(job['chunks']) == 3 + len(rest)
    assert gemini.texts[1] + '\n\n' in gemini.texts[0] or gemini.texts[1] in gemini.texts[0]
    assert requests_today(client.app.state.runtime.store, DEFAULT_TTS_MODEL) == len(gemini.texts)
    assert gemini.fallback == [] and 'content_blocked' not in job
    assert job['progress'] == job['total'] == len(book['segments'])
    audio_items = takes(client, book, result['session'])
    assert len(audio_items) == len(book['segments']) and all('substitute' not in t['audio'] for t in audio_items)
    assert all(t['audio']['provider'] == 'gemini' for t in audio_items)


def test_one_blocked_half_goes_to_the_fallback_narrator_and_is_never_split_again(client, monkeypatch):
    book = import_story(client)
    marker, poison = poison_marker(book)
    gemini = Gemini(monkeypatch, lambda text, n: marker in text)
    result = start(client, book, chunking=CHUNKING)
    job = wait_job(client, result['job']['id'])
    assert job['status'] == 'completed', job
    statuses = [(entry['status'], entry.get('split', False)) for entry in job['chunks'][:3]]
    assert statuses[0] == ('blocked', False)
    assert sorted(statuses[1:]) == [('blocked', True), ('done', True)]
    # The poisoned passage was requested exactly twice (the original chunk and its blocked half): never a third time.
    assert sum(marker in text for text in gemini.texts) == 2
    assert len(gemini.texts) == len(job['chunks']) == 3 + len(job['chunks'][3:])
    half = next(entry for entry in job['chunks'][1:3] if entry['status'] == 'blocked')
    expected = ids_between(book, half['first_segment_id'], half['last_segment_id'])
    assert poison['id'] in expected and half['split'] is True and 'split_into' not in half
    # Only that half is read by the fallback, one passage at a time, with no Gemini request.
    # Passages with identical text share one retained take (the usual equivalent-speech reuse), so the narrator
    # is asked once per distinct text.
    texts = {s['id']: s['text'] for s in book['segments']}
    assert [call['id'] for call in gemini.fallback] == [i for n, i in enumerate(expected) if texts[i] not in [texts[j] for j in expected[:n]]]
    assert {call['provider'] for call in gemini.fallback} == {'system'}
    view = job['content_blocked']
    assert view['fallback_passage_ids'] == expected and view['blocked_passage_ids'] == []
    assert view['fallback']['provider'] == 'system' and job['fallback'] == view['fallback']
    assert 'read by a device voice because Gemini blocked them' in job['message']
    assert job['progress'] == job['total'] == len(book['segments'])
    # Playback: every passage in order; the blocked half is the fallback's, marked, immutable and not Gemini audio.
    listed = takes(client, book, result['session'])
    assert [t['segment_id'] for t in listed] == [s['id'] for s in book['segments']]
    substitutes = [t['audio'] for t in listed if 'substitute' in t['audio']]
    assert [a['segment_id'] for a in substitutes] == expected
    assert all(a['provider'] == 'system' and a['substitute'] == {'reason': 'content_blocked', 'for_provider': 'gemini',
                                                                  'for_model': DEFAULT_TTS_MODEL} for a in substitutes)
    assert all(client.get(a['url']).status_code == 200 for a in substitutes)
    assert all(t['audio']['provider'] == 'gemini' for t in listed if 'substitute' not in t['audio'])
    # The take is recipe-distinct from Gemini audio and lives in its own session; Gemini's takes are untouched.
    assert result['session']['id'] not in {a['session_id'] for a in substitutes}
    # The passage endpoint answers from the retained fallback take with no provider request.
    before = len(gemini.texts)
    single = client.post(f"/api/books/{book['id']}/listen", json={'provider': 'gemini', 'voice': 'Kore', 'model': DEFAULT_TTS_MODEL,
                                                                 'segment_id': poison['id']}).json()
    assert single['cached'] is True and single['audio']['substitute']['reason'] == 'content_blocked'
    assert len(gemini.texts) == before


def test_no_fallback_narrator_leaves_a_distinct_blocked_outcome_and_the_rest_completes(client, monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    book = import_story(client)
    marker, poison = poison_marker(book)
    gemini = Gemini(monkeypatch, lambda text, n: marker in text)
    result = start(client, book, chunking=CHUNKING)
    job = wait_job(client, result['job']['id'])
    assert job['status'] == 'completed' and job.get('error') is None, job
    assert job['fallback'] is None
    view = job['content_blocked']
    half = next(entry for entry in job['chunks'] if entry['status'] == 'blocked' and entry.get('split'))
    expected = ids_between(book, half['first_segment_id'], half['last_segment_id'])
    assert view['blocked_passage_ids'] == expected and view['fallback_passage_ids'] == [] and view['fallback'] is None
    assert gemini.fallback == []
    assert job['progress'] == len(book['segments']) - len(expected) and job['total'] == len(book['segments'])
    assert 'blocked by Gemini’s content policy and left unrecorded' in job['message']
    listed = {t['segment_id'] for t in takes(client, book, result['session'])}
    assert listed == {s['id'] for s in book['segments']} - set(expected), 'everything else is prepared'
    # A re-run never resends known-blocked text (or a chunk containing it).
    sent = len(gemini.texts)
    again = wait_job(client, start(client, book, chunking=CHUNKING)['job']['id'])
    assert again['status'] == 'completed' and len(gemini.texts) == sent and again['chunks'] == []
    assert again['content_blocked']['blocked_passage_ids'] == expected


def test_a_one_passage_request_that_is_blocked_goes_straight_to_the_fallback(client, monkeypatch):
    text = 'The lantern keeper counted the quiet boats along the harbor wall, and the night went on and on. ' * 6
    book = client.post('/api/books', files={'file': ('one.txt', ('Chapter One\n\n' + text.strip() + '\n').encode(), 'text/plain')}).json()
    body = [s for s in book['segments'] if len(s['text']) > 300]
    assert len(body) == 1
    gemini = Gemini(monkeypatch, lambda t, n: t.strip() == body[0]['text'])
    result = start(client, book, segment=body[0], chunking=CHUNKING)
    job = wait_job(client, result['job']['id'])
    assert job['status'] == 'completed', job
    assert [(e['status'], e['segment_count']) for e in job['chunks']] == [('blocked', 1)] and 'split_into' not in job['chunks'][0]
    assert len(gemini.texts) == 1 and [c['id'] for c in gemini.fallback] == [body[0]['id']]
    assert job['content_blocked']['fallback_passage_ids'] == [body[0]['id']]


def test_resume_after_the_original_was_blocked_requests_the_halves_not_the_whole(client, monkeypatch):
    gemini = Gemini(monkeypatch, lambda text, n: False)
    book = import_story(client)
    store = client.app.state.runtime.store
    repository = ListeningRepository(store)
    session = repository.session(book['id'], 'gemini', 'Kore', DEFAULT_TTS_MODEL)
    chapter = book['chapters'][0]
    ids = [s['id'] for s in book['segments'] if s['chapter_id'] == chapter['id']][:12]
    # A job that stopped right after the original chunk was blocked, before its halves were requested.
    repository.record_block(book['id'], session['id'], chapter['id'], ids, 'chunk')
    whole = client.app.state.runtime.store.book(book['id'])['chapters'][0]['text']
    job = wait_job(client, start(client, book, chunking=CHUNKING)['job']['id'])
    assert job['status'] == 'completed', job
    first_two = job['chunks'][:2]
    assert all(entry.get('split') for entry in first_two)
    assert sum(entry['segment_count'] for entry in first_two) == 12
    start_of = min(gemini.texts[0], gemini.texts[1], key=lambda t: whole.index(t))
    assert start_of == gemini.texts[0], 'halves are requested in reading order'
    original = whole[book['segments'][0]['start']:book['segments'][11]['end']]
    assert original not in gemini.texts and not any(original in text for text in gemini.texts)


def test_a_changed_recipe_or_text_invalidates_the_memory(client, monkeypatch):
    book = import_story(client)
    marker, poison = poison_marker(book)
    gemini = Gemini(monkeypatch, lambda text, n: marker in text)
    result = start(client, book, chunking=CHUNKING)
    wait_job(client, result['job']['id'])
    store = client.app.state.runtime.store
    repository = ListeningRepository(store)
    assert poison['id'] in repository.content_blocks(book['id'], result['session']['id'])['refused']
    # A different voice is a different recipe (session): nothing known about Gemini refusing this text under it.
    other = repository.session(book['id'], 'gemini', 'Aoede', DEFAULT_TTS_MODEL)
    assert repository.content_blocks(book['id'], other['id'])['refused'] == {}
    sent = len(gemini.texts)
    wait_job(client, start(client, book, voice='Aoede', chunking=CHUNKING)['job']['id'])
    assert len(gemini.texts) > sent and any(marker in text for text in gemini.texts[sent:]), 'the text is asked for again'
    # An edited passage no longer matches the retained block.
    edited = store.book(book['id'])
    chapter = edited['chapters'][0]
    old = 'step 4 along'
    chapter['text'] = chapter['text'].replace(old, 'step 7 along', 1)
    for segment in edited['segments']:
        if old in segment['text']:
            segment['text'] = segment['text'].replace(old, 'step 7 along', 1)
    store.save_book(edited)
    assert poison['id'] not in repository.content_blocks(book['id'], result['session']['id'])['refused']


def test_daily_count_reserved_before_each_half_and_stops_at_the_limit(client, monkeypatch):
    # 2 requests a day: the original and the first half are sent, the second half is refused before sending.
    client.post('/api/settings', json={'tts_limits': {DEFAULT_TTS_MODEL: {'rpm': 1000, 'tpm': 10_000_000, 'rpd': 2}}})
    gemini = Gemini(monkeypatch, lambda text, n: n == 1)
    book = import_story(client)
    job = wait_job(client, start(client, book, chunking=CHUNKING)['job']['id'])
    assert job['status'] == 'quota_limited' and len(gemini.texts) == 2
    assert [e['status'] for e in job['chunks']] == ['blocked', 'done']
    # Resume after the reset: the remaining half is requested (not the blocked original).
    client.post('/api/settings', json={'tts_limits': {DEFAULT_TTS_MODEL: {'rpm': 1000, 'tpm': 10_000_000, 'rpd': 1000}}})
    again = wait_job(client, start(client, book, chunking=CHUNKING)['job']['id'])
    assert again['status'] == 'completed' and again['chunks'][0].get('split')
    assert gemini.texts.count(gemini.texts[0]) == 1


# Records ----------------------------------------------------------------------------

def test_no_response_body_is_ever_persisted_and_records_are_immutable(client, monkeypatch):
    book = import_story(client)
    marker, _ = poison_marker(book)
    Gemini(monkeypatch, lambda text, n: marker in text)
    result = start(client, book, chunking=CHUNKING)
    job = wait_job(client, result['job']['id'])
    store = client.app.state.runtime.store
    with store.connect() as conn:
        dump = '\n'.join(conn.iterdump())
        rows = conn.execute('SELECT body FROM listening_blocked').fetchall()
    assert ECHO not in dump and 'Request blocked for an unspecified policy reason' not in dump
    assert rows and all(json.loads(body)['code'] == 'content_blocked' for (body,) in rows)
    assert ECHO not in json.dumps(job) and 'policy reason' not in json.dumps(job)
    with pytest.raises(Exception):
        with store.connect() as conn:
            conn.execute('DELETE FROM listening_blocked')
    with pytest.raises(Exception):
        with store.connect() as conn:
            conn.execute("UPDATE listening_substitutes SET body='{}'")


def test_other_400s_still_fail_the_job_with_the_generic_hint(client, monkeypatch):
    monkeypatch.setattr(audio.httpx, 'post', lambda *a, **k: httpx.Response(400, json={'error': {'code': 400, 'message': ECHO}}))
    book = import_story(client)
    job = wait_job(client, start(client, book, chunking=CHUNKING)['job']['id'])
    assert job['status'] == 'failed' and 'Check the model, voice, and passage length.' in job['error']
    assert 'error_code' not in job and ECHO not in job['error'] and 'content_blocked' not in job
    with client.app.state.runtime.store.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM listening_blocked').fetchone()[0] == 0


def test_a_blocked_single_passage_listen_job_reports_a_code_and_is_not_resent(client, monkeypatch):
    book = import_story(client, 4)
    target = book['segments'][1]
    posts = []

    def post(url, **kwargs):
        posts.append(kwargs['json']['input'][0]['content'][0]['text'])
        return httpx.Response(400, json={'error': {'code': 'content_blocked', 'message': ECHO}})

    monkeypatch.setattr(audio.httpx, 'post', post)
    monkeypatch.setattr('bardic.app.synthesize', audio.synthesize)
    body = {'provider': 'gemini', 'voice': 'Kore', 'model': DEFAULT_TTS_MODEL, 'segment_id': target['id']}
    job = wait_job(client, client.post(f"/api/books/{book['id']}/listen", json=body).json()['job']['id'])
    assert job['status'] == 'failed' and job['error_code'] == 'content_blocked'
    assert job['error'] == audio.GEMINI_CONTENT_BLOCKED_MESSAGE and len(posts) == 1
    again = wait_job(client, client.post(f"/api/books/{book['id']}/listen", json=body).json()['job']['id'])
    assert again['status'] == 'failed' and again['error_code'] == 'content_blocked' and len(posts) == 1, 'known text is not resent'


# Performances ------------------------------------------------------------------------

def perf_request(book):
    return {'mode': 'simple', 'provider': 'gemini', 'voice': 'Kore', 'chapter_ids': [book['chapters'][0]['id']]}


def wait_performance(client, book, performance_id, timeout=15):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = client.get(f"/api/books/{book['id']}/performances/{performance_id}").json()['performance']
        if record['job'] is None or record['job']['status'] not in {'queued', 'running'}:
            return record
        time.sleep(.02)
    pytest.fail('performance did not finish')


def test_a_performance_reports_passages_read_by_the_fallback_and_resumes_without_resending(client, monkeypatch):
    book = import_story(client)
    marker, poison = poison_marker(book)
    gemini = Gemini(monkeypatch, lambda text, n: marker in text)
    created = client.post(f"/api/books/{book['id']}/performances", json=perf_request(book))
    assert created.status_code == 200, created.text
    record = wait_performance(client, book, created.json()['performance']['id'])
    progress = record['progress']
    assert record['job']['status'] == 'completed'
    assert progress['passages_ready'] == progress['passages_total'] == len(book['segments'])
    assert progress['passages_fallback'] > 0 and progress['passages_blocked'] == 0 and progress['fallback_provider'] == 'system'
    chapter = progress['chapters'][0]
    assert chapter['passages_fallback'] == progress['passages_fallback'] and chapter['blocked_passage_ids'] == []
    assert 'read by a device voice because Gemini blocked them' in record['job']['message']
    assert record['job']['message'].startswith('Performance ready.')
    audio_map = client.get(f"/api/books/{book['id']}/performances/{record['id']}/audio").json()['audio']
    assert sum('substitute' in item for item in audio_map.values()) == progress['passages_fallback']
    assert list(audio_map)[0] != poison['id'] or 'substitute' in audio_map[poison['id']]
    # Preview and resume plan nothing: known-blocked text is not requested again and needs no request.
    plan = client.post(f"/api/books/{book['id']}/performances/{record['id']}/preview", json={}).json()
    assert plan['passages_to_generate'] == 0 and plan['requests_estimate'] == 0
    sent = len(gemini.texts)
    resumed = client.post(f"/api/books/{book['id']}/performances/{record['id']}/prepare")
    assert resumed.status_code == 200 and resumed.json()['job'] is None and len(gemini.texts) == sent
    listing = client.get(f"/api/books/{book['id']}/performances").json()['performances'][0]
    assert listing['progress']['passages_fallback'] == progress['passages_fallback']


def test_a_performance_without_a_fallback_narrator_keeps_the_blocked_passages_distinct(client, monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    book = import_story(client)
    marker, _ = poison_marker(book)
    gemini = Gemini(monkeypatch, lambda text, n: marker in text)
    created = client.post(f"/api/books/{book['id']}/performances", json=perf_request(book))
    assert created.status_code == 200, created.text
    record = wait_performance(client, book, created.json()['performance']['id'])
    progress = record['progress']
    assert record['job']['status'] == 'completed' and record['job'].get('error') is None
    assert progress['passages_blocked'] > 0 and progress['passages_fallback'] == 0 and progress['fallback_provider'] is None
    assert progress['passages_ready'] == progress['passages_total'] - progress['passages_blocked']
    assert progress['chapters'][0]['blocked_passage_ids'] and record['job']['message'].startswith('Performance prepared.')
    assert 'left unrecorded' in record['job']['message']
    plan = client.post(f"/api/books/{book['id']}/performances/{record['id']}/preview", json={}).json()
    assert plan['passages_to_generate'] == 0 and any('not requested again' in note for note in plan['notes'])
    assert plan['chapters'][0]['passages_blocked'] == progress['passages_blocked']
    sent = len(gemini.texts)
    assert client.post(f"/api/books/{book['id']}/performances/{record['id']}/prepare").json()['job'] is None
    assert len(gemini.texts) == sent


def cast_request(book, monkeypatch, client):
    runtime = client.app.state.runtime
    voices = {'narrator': {'id': 'narrator', 'name': 'Narrator', 'voices': {'gemini': {'id': 'Kore'}}}}
    monkeypatch.setattr(runtime, 'resolved_cast', lambda _book: voices)
    return {'mode': 'cast', 'provider': 'gemini', 'chapter_ids': [book['chapters'][0]['id']]}


def test_a_blocked_cast_passage_without_a_fallback_narrator_fails_with_a_code_and_the_fixed_sentence(client, monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    book = import_story(client, 4)
    monkeypatch.setattr(audio.httpx, 'post', lambda *a, **k: httpx.Response(400, json={'error': {'code': 'content_blocked', 'message': ECHO}}))
    monkeypatch.setattr('bardic.performances.synthesize', audio.synthesize)
    created = client.post(f"/api/books/{book['id']}/performances", json=cast_request(book, monkeypatch, client))
    assert created.status_code == 200, created.text
    record = wait_performance(client, book, created.json()['performance']['id'])
    assert record['job']['status'] == 'failed' and record['job']['error_code'] == 'content_blocked'
    assert 'could not be narrated' in record['job']['error'] and audio.GEMINI_CONTENT_BLOCKED_MESSAGE in record['job']['error']
    assert ECHO not in record['job']['error']


def test_a_blocked_cast_passage_is_read_by_the_fallback_narrator_and_noted(client, monkeypatch):
    book = import_story(client, 4)
    monkeypatch.setattr(audio.httpx, 'post', lambda *a, **k: httpx.Response(400, json={'error': {'code': 'content_blocked', 'message': ECHO}}))
    base, spoken = fake_chunk_synthesizer([]), []

    def routed(segment, character, scene, provider, model, key, path, **kwargs):
        if provider == 'gemini':
            return audio.synthesize(segment, character, scene, provider, model, key, path, **kwargs)
        spoken.append(segment['id'])
        return base(segment, character, scene, provider, model, key, path, **kwargs)

    monkeypatch.setattr('bardic.performances.synthesize', routed)
    created = client.post(f"/api/books/{book['id']}/performances", json=cast_request(book, monkeypatch, client))
    assert created.status_code == 200, created.text
    record = wait_performance(client, book, created.json()['performance']['id'])
    progress = record['progress']
    assert record['job']['status'] == 'completed', record['job']
    assert progress['passages_ready'] == progress['passages_total'] and progress['passages_fallback'] == progress['passages_total']
    assert progress['fallback_reasons'] == {'content_blocked': progress['passages_total'], 'failed': 0}
    assert 'Gemini blocked' in record['job']['message'] and record['fallback']['provider'] == 'system'
    audio_map = client.get(f"/api/books/{book['id']}/performances/{record['id']}/audio").json()['audio']
    assert all(item['substitute']['reason'] == 'content_blocked' for item in audio_map.values()) and spoken, 'equal text shares one retained take'
    assert ECHO not in json.dumps(record)


def audio_module_synthesize():
    import bardic.chapter_listening
    return bardic.chapter_listening.synthesize


class Flaky(Gemini):
    """Gemini that answers HTTP 500 to a request whose text holds the marker: always, or for the first ``failures`` requests."""

    def __init__(self, monkeypatch, marker, failures=None):
        super().__init__(monkeypatch, lambda text, n: False)
        self.marker, self.failures, self.failed = marker, failures, 0
        # A performance's own fallback readings go through the same fake engine.
        monkeypatch.setattr('bardic.performances.synthesize', audio_module_synthesize())

    def post(self, url, **kwargs):
        text = kwargs['json']['input'][0]['content'][0]['text']
        if self.marker in text and (self.failures is None or self.failed < self.failures):
            self.failed += 1
            self.texts.append(text)
            return httpx.Response(500, json={'error': {'code': 500, 'message': ECHO}})
        return super().post(url, **kwargs)


def test_a_gemini_chunk_that_keeps_failing_is_read_by_the_fallback_and_the_rest_continues(client, monkeypatch):
    book = import_story(client)
    marker, poison = poison_marker(book)
    gemini = Flaky(monkeypatch, marker)
    created = client.post(f"/api/books/{book['id']}/performances", json={**perf_request(book), 'fallback': {'provider': 'system', 'voice': ''}})
    assert created.status_code == 200, created.text
    record = wait_performance(client, book, created.json()['performance']['id'])
    progress = record['progress']
    assert record['job']['status'] == 'completed', record['job']
    assert progress['passages_ready'] == progress['passages_total'] == len(book['segments'])
    assert progress['passages_fallback'] > 0 and progress['fallback_reasons']['failed'] == progress['passages_fallback']
    assert progress['fallback_reasons']['content_blocked'] == 0 and progress['fallback_provider'] == 'system'
    assert 'because the narrator could not produce' in record['job']['message']
    ready = client.get(f"/api/books/{book['id']}/performances/{record['id']}/audio").json()['audio']
    noted = {key for key, item in ready.items() if (item.get('substitute') or {}).get('reason') == 'failed'}
    assert poison['id'] in noted and len(noted) == progress['passages_fallback']
    assert all(ready[key]['provider'] == 'gemini' for key in ready if key not in noted), 'the rest is still Gemini audio'
    assert ECHO not in json.dumps(record)
    # The failing text was requested twice (the original and one retry); the fallback then read it and resuming asks for nothing.
    assert sum(marker in text for text in gemini.texts) == 2
    sent = len(gemini.texts)
    resumed = client.post(f"/api/books/{book['id']}/performances/{record['id']}/prepare")
    assert resumed.status_code == 200 and resumed.json()['job'] is None and len(gemini.texts) == sent
    # Status notes exactly those passages.
    notes = client.get(f"/api/books/{book['id']}/performances/{record['id']}/status").json()['notes']
    assert {note['segment_id'] for note in notes} == noted and {note['reason'] for note in notes} == {'failed'}


def test_a_transient_gemini_error_is_retried_once_and_needs_no_fallback(client, monkeypatch):
    book = import_story(client)
    marker, _ = poison_marker(book)
    gemini = Flaky(monkeypatch, marker, failures=1)
    created = client.post(f"/api/books/{book['id']}/performances", json={**perf_request(book), 'fallback': {'provider': 'system', 'voice': ''}})
    record = wait_performance(client, book, created.json()['performance']['id'])
    progress = record['progress']
    assert record['job']['status'] == 'completed' and progress['passages_ready'] == progress['passages_total']
    assert progress['passages_fallback'] == 0 and gemini.fallback == [], 'the retry succeeded: nobody else read anything'
    assert sum(marker in text for text in gemini.texts) == 2


def test_an_uncertain_gemini_request_is_never_retried_and_goes_straight_to_the_fallback(client, monkeypatch):
    book = import_story(client)
    marker, poison = poison_marker(book)
    gemini = Gemini(monkeypatch, lambda text, n: False)
    monkeypatch.setattr('bardic.performances.synthesize', audio_module_synthesize())
    real = gemini.post

    def dropped(url, **kwargs):
        text = kwargs['json']['input'][0]['content'][0]['text']
        if marker in text:
            gemini.texts.append(text)
            raise httpx.ReadTimeout('timed out')
        return real(url, **kwargs)

    monkeypatch.setattr(audio.httpx, 'post', dropped)
    created = client.post(f"/api/books/{book['id']}/performances", json={**perf_request(book), 'fallback': {'provider': 'system', 'voice': ''}})
    record = wait_performance(client, book, created.json()['performance']['id'])
    assert record['job']['status'] == 'completed' and record['progress']['passages_fallback'] > 0
    assert sum(marker in text for text in gemini.texts) == 1, 'a request that may have been billed is not resent'
    ready = client.get(f"/api/books/{book['id']}/performances/{record['id']}/audio").json()['audio']
    assert ready[poison['id']]['substitute']['reason'] == 'failed'


def test_a_gemini_fallback_is_never_asked_to_read_text_gemini_blocked():
    from bardic.performances import Fallback
    narrator = {'session_id': 's', 'provider': 'gemini', 'model': 'm', 'voice': 'Kore'}
    assert Fallback(narrator).reads('content_blocked') is False and Fallback(narrator).reads('failed') is True
    assert Fallback(narrator).coordinator_narrator is None
    assert Fallback(None).reads('failed') is False
    local = {**narrator, 'provider': 'system'}
    assert Fallback(local).reads('content_blocked') is True and Fallback(local).coordinator_narrator == local
