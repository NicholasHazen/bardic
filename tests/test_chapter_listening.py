"""Chunked simple listening: planning, pause alignment, quota pacing and chapter jobs.

Every test is offline. Speech is synthetic tone bursts shaped like phrases, and
provider responses are fakes; no provider credit or real book text is used.
"""
from __future__ import annotations

import io
import json
import math
import re
import struct
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from bardic import chunking, tts_limits
from bardic.alignment import align, align_file, find_pauses, frame_peaks
from bardic.app import create_app
from bardic.audio import DEFAULT_TTS_MODEL, RateLimited, UncertainRequest, render_fingerprint
from bardic.chapter_listening import ChapterCoordinator, request_timeout
from bardic.listening import ListeningRepository, TruncatedChunk
from bardic.resources import publish_metrics
from bardic.tts_limits import RateLimiter, classify_rate_limit, quota_day, requests_today

RATE = 24_000
PHRASE = re.compile(r'[^.!?,;:\n]+[.!?,;:]*["”’]?|\n+')


def speech_wav(text: str, *, chars_per_second=15.0, pause=0.28, paragraph=0.6, leading=0.05) -> tuple[bytes, list[float]]:
    """Tone bursts per phrase with pauses at punctuation. Returns WAV and phrase-end times."""
    samples, ends = [], []

    def tone(seconds):
        start = len(samples)
        samples.extend(int(6000 * math.sin((start + i) * 0.07)) for i in range(int(seconds * RATE)))

    def silence(seconds):
        samples.extend([0] * int(seconds * RATE))

    silence(leading)
    for match in PHRASE.finditer(text):
        piece = match.group()
        if piece.startswith('\n'):
            silence(paragraph)
            continue
        if not piece.strip():
            continue
        tone(max(0.08, len(piece.strip()) / chars_per_second))
        ends.append(len(samples) / RATE)
        silence(pause if piece.rstrip()[-1:] in '.!?,;:”’"' else 0.02)
    silence(leading)
    output = io.BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(RATE)
        writer.writeframes(struct.pack(f'<{len(samples)}h', *samples))
    return output.getvalue(), ends


def story(paragraphs=18):
    # Original synthetic prose; no user text is used as a fixture.
    lines = ['Chapter One', '']
    for index in range(paragraphs):
        lines.append(f'The lantern keeper counted step {index} along the harbor wall. '
                     f'“Is the tide turning yet?” asked the ferry cook. The keeper nodded, slowly, and walked on.')
        lines.append('')
    return '\n'.join(lines)


# Planner -------------------------------------------------------------------

def segments_for(texts, *, scenes=None, paragraph_every=1):
    chapter, segments, cursor = '', [], 0
    for index, text in enumerate(texts):
        start = len(chapter)
        chapter += text
        segments.append({'id': f's{index}', 'chapter_id': 'c', 'start': start, 'end': len(chapter), 'text': text,
                         'scene_id': (scenes or {}).get(index, 'scene-a')})
        chapter += '\n\n' if (index + 1) % paragraph_every == 0 else ' '
    return segments, chapter


def test_planner_fills_requests_up_to_the_safe_cap_and_prefers_strong_boundaries():
    texts = [f'Sentence number {i} goes on for a while here.' for i in range(400)]
    # The scene change lands at about 85% of the first chunk's target.
    segments, chapter = segments_for(texts, scenes={i: 'scene-b' for i in range(107, 400)}, paragraph_every=5)
    calibration = chunking.Calibration()
    options = chunking.normalize_options({'ramp_seconds': [], 'target_seconds': 420})
    chunks = chunking.plan(segments, chapter, scope_start=0, focus=0, blocked=set(), options=options, calibration=calibration)
    hard_chars = calibration.hard_chars
    assert all(chunk['chars'] <= hard_chars for chunk in chunks)
    assert sum(len(chunk['segment_ids']) for chunk in chunks) == 400
    # Full-size requests: every chunk except the last is at least 60% of target.
    target_chars = 420 * calibration.chars_per_second
    assert all(chunk['chars'] >= 0.6 * target_chars for chunk in chunks[:-1])
    for chunk in chunks[:-1]:
        assert chunking.boundary_strength(segments, chunk['last_index'], chapter) >= 2, 'ends at a paragraph or scene'
    assert chunks[0]['last_index'] == 106, 'a scene change near the target beats a fuller paragraph break'
    # Chunks are exact slices of contiguous passages.
    for chunk in chunks:
        assert chunk['start'] == segments[chunk['first_index']]['start'] and chunk['end'] == segments[chunk['last_index']]['end']


def test_planner_ramp_focus_wrap_and_single_take_passages():
    texts = [f'Line {i} of the quiet test chapter continues.' for i in range(120)]
    segments, chapter = segments_for(texts)
    calibration = chunking.Calibration()
    options = chunking.normalize_options({'ramp_seconds': [30, 60], 'target_seconds': 240})
    covered = {f's{i}' for i in range(52, 56)}  # older single-passage takes
    chunks = chunking.plan(segments, chapter, scope_start=10, focus=50, blocked=set(), covered=covered,
                           options=options, calibration=calibration)
    assert chunks[0]['first_index'] == 50, 'generation starts at the listener'
    assert chunks[0]['target_seconds'] == 30 and chunks[1]['target_seconds'] == 60 and chunks[2]['target_seconds'] == 240
    assert chunks[0]['expected_seconds'] < chunks[1]['expected_seconds'] < chunks[2]['expected_seconds']
    assert any(set(covered) & set(chunk['segment_ids']) for chunk in chunks), 'single takes do not fragment requests'
    wrapped = [chunk for chunk in chunks if chunk['first_index'] < 50]
    assert wrapped and wrapped[0]['first_index'] == 10, 'earlier scope is filled after the listener position'
    assert all(not any(s in chunk['segment_ids'] for s in ('s0', 's9')) for chunk in chunks)
    blocked = {f's{i}' for i in range(60, 62)}
    stopped = chunking.plan(segments, chapter, scope_start=50, focus=50, blocked=blocked, options=options,
                            calibration=calibration, limit=1)[0]
    assert stopped['last_index'] <= 59, 'a chunk never crosses audio that already exists'


def test_calibration_learns_rate_and_truncation_lowers_the_cap():
    calibration = chunking.Calibration()
    assert calibration.chars_per_second_low == chunking.PRIOR_CHARS_PER_SECOND_LOW
    calibration.record(6000, 400.0, 200.0)
    assert abs(calibration.chars_per_second - (1500 + 6000) / (1500 / 14 + 400)) < 1e-9
    assert calibration.chars_per_second_low == pytest.approx(15 * 0.85)
    assert calibration.realtime_factor == pytest.approx(2.0)
    calibration.record_truncation(8000)
    assert calibration.chars_per_second_low < 8000 / chunking.PROVIDER_AUDIO_CAP_SECONDS
    restored = chunking.Calibration(json.loads(json.dumps(calibration.view())))
    assert restored.chars_per_second_low == calibration.chars_per_second_low


@pytest.mark.parametrize('options', [{'ramp_seconds': [5]}, {'target_seconds': 600}, {'concurrency': 4},
                                     {'ramp_seconds': [30] * 7}, {'concurrency': 2.0}])
def test_invalid_chunk_options_are_rejected(options):
    with pytest.raises(ValueError):
        chunking.normalize_options(options)


# Alignment -----------------------------------------------------------------

def aligned_case(tmp_path, texts, gaps, **speech):
    passages = [{'id': f'p{i}', 'text': text, 'gap_after': gaps[i] if i < len(gaps) else ''} for i, text in enumerate(texts)]
    chunk_text = ''.join(text + (gaps[i] if i < len(gaps) else '') for i, text in enumerate(texts))
    data, _ = speech_wav(chunk_text, **speech)
    path = tmp_path / 'chunk.wav'
    path.write_bytes(data)
    # Truth: synthesize each passage alone with the same generator and sum.
    truth, total = [], 0.0
    for i, text in enumerate(texts):
        piece = text + (gaps[i] if i < len(gaps) else '')
        single, _ = speech_wav(piece, leading=0, **speech)
        with wave.open(io.BytesIO(single)) as reader:
            total += reader.getnframes() / RATE
        truth.append(total)
    return path, passages, truth


@pytest.mark.parametrize('rate', [11.0, 15.0, 19.0])
def test_pause_alignment_recovers_passage_boundaries(tmp_path, rate):
    texts = []
    for i in range(24):
        texts.append(f'“Where did the lamp go, number {i}?”' if i % 3 == 0 else
                     'asked the ferry cook.' if i % 3 == 1 else
                     f'The keeper looked at the water for a long moment, then answered, softly, that it was gone.')
    gaps = [' ' if i % 3 == 0 else '\n\n' if i % 3 == 2 else ' ' for i in range(len(texts))]
    path, passages, truth = aligned_case(tmp_path, texts, gaps, chars_per_second=rate)
    timing = align_file(path, passages)
    assert timing['method'] == 'pause_alignment' and timing['estimated'] is True
    clips = timing['clips']
    assert clips[0]['start'] == 0 and all(a['end'] == b['start'] for a, b in zip(clips, clips[1:]))
    errors = [abs(clip['end'] - expected) for clip, expected in zip(clips[:-1], truth[:-1])]
    assert max(errors) < 0.6, errors
    assert timing['quality']['matched'] == timing['quality']['boundaries']


def test_alignment_without_pauses_falls_back_to_contiguous_proportional_clips():
    passages = [{'id': 'a', 'text': 'x' * 10, 'gap_after': ' '}, {'id': 'b', 'text': 'y' * 30, 'gap_after': ''}]
    result = align(passages, 8.0, [])
    assert result['clips'][0]['end'] == result['clips'][1]['start']
    assert 1.0 < result['clips'][0]['end'] < 3.5
    assert result['quality']['matched'] == 0
    single = align(passages[:1], 3.0, [])
    assert single['clips'] == [{'segment_id': 'a', 'start': 0.0, 'end': 3.0}]


def test_frame_peaks_and_pause_detection_on_real_wav(tmp_path):
    data, _ = speech_wav('One, two. Three.\n\nFour.')
    path = tmp_path / 'short.wav'
    path.write_bytes(data)
    peaks, duration = frame_peaks(path)
    pauses = find_pauses(peaks)
    assert len(peaks) == math.ceil(duration / 0.01)
    assert len([p for p in pauses if 0.05 < p[0] and p[1] < duration - 0.05]) >= 3


# Rate limits and quota -------------------------------------------------------

def test_sliding_window_rpm_tpm_and_cooldown():
    clock = [1000.0]
    limiter = RateLimiter(clock=lambda: clock[0])
    limiter.configure({'m': {'rpm': 2, 'tpm': 1000, 'rpd': 5}})
    assert limiter.try_acquire('m', 100) == 0
    clock[0] += 30
    assert limiter.try_acquire('m', 100) == 0
    wait = limiter.try_acquire('m', 100)
    assert wait == pytest.approx(tts_limits.WINDOW_SECONDS - 30), 'RPM: wait for the oldest send to leave'
    clock[0] += wait
    assert limiter.try_acquire('m', 950) == pytest.approx(30), 'TPM: the second send is still in the window'
    assert limiter.try_acquire('m', 850) == 0
    limiter.cool_down('m', 30)
    clock[0] += 70
    assert limiter.try_acquire('m', 10) == 0
    limiter.cool_down('m', 30)
    assert 29 < limiter.try_acquire('m', 10) <= 30
    assert limiter.view('m')['cooldown_seconds'] > 0
    limiter.reset()
    assert limiter.try_acquire('m', 10) == 0


class Response429:
    def __init__(self, body, headers=None):
        self._body, self.headers = body, headers or {}

    def json(self):
        return self._body


def test_rate_limit_classification_reads_only_quota_identifiers_and_delay():
    day = Response429({'error': {'code': 429, 'message': 'secret prompt echoed', 'details': [
        {'@type': 'type.googleapis.com/google.rpc.QuotaFailure',
         'violations': [{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel'}]},
        {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '41s'}]}})
    assert classify_rate_limit(day) == ('day', 41.0)
    minute = Response429({'error': {'details': [{'violations': [{'quotaMetric': 'x/generate_requests_per_minute'}]}]}},
                         {'retry-after': '7'})
    assert classify_rate_limit(minute) == ('minute', 7.0)
    assert classify_rate_limit(Response429(ValueError)) == ('unknown', 30.0)


def test_quota_day_resets_at_pacific_midnight():
    start, reset = quota_day(datetime(2026, 9, 28, 6, 30, tzinfo=timezone.utc))  # 23:30 PDT on the 27th
    assert start == datetime(2026, 9, 27, 7, 0, tzinfo=timezone.utc)
    assert reset == datetime(2026, 9, 28, 7, 0, tzinfo=timezone.utc)


def test_gemini_adapter_classifies_429_and_uncertain_failures(monkeypatch):
    from bardic import audio
    recipe = {'model': DEFAULT_TTS_MODEL, 'text': 'text', 'style': '', 'voice': 'Kore'}
    tts_limits.LIMITER.reset()
    body = {'error': {'details': [{'violations': [{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel'}]}]}}
    monkeypatch.setattr(audio.httpx, 'post', lambda *a, **k: httpx.Response(429, json=body))
    with pytest.raises(RateLimited) as error:
        audio._generate_gemini(recipe, 'key', pace=False)
    assert error.value.scope == 'day' and 'midnight Pacific' in str(error.value)

    def timeout(*_args, **kwargs):
        assert kwargs['timeout'].read == 700
        raise httpx.ReadTimeout('slow')
    monkeypatch.setattr(audio.httpx, 'post', timeout)
    with pytest.raises(UncertainRequest):
        audio._generate_gemini(recipe, 'key', timeout=700, pace=False)
    tts_limits.LIMITER.reset()
    assert request_timeout(470) == pytest.approx(795) and request_timeout(10) == pytest.approx(105)


# Repository and API -------------------------------------------------------

def fake_chunk_synthesizer(calls, *, truncate_over=None, rate_limit=None, fail_on=None, gate=None, rate=15.0):
    def synthesize(segment, character, scene, provider, model, key, path, *, timeout=None, pace=True):
        calls.append({'text': segment['text'], 'timeout': timeout, 'pace': pace, 'key': key})
        if gate:
            gate.wait(5)
        if rate_limit and rate_limit(len(calls)):
            raise RateLimited('Gemini returned HTTP 429.', *rate_limit(len(calls)))
        if fail_on and fail_on(len(calls)):
            raise UncertainRequest('Gemini narration timed out.')
        data, _ = speech_wav(segment['text'], chars_per_second=rate)
        duration = (len(data) - 44) / 2 / RATE
        output_tokens = int(duration * 32)
        if truncate_over and len(segment['text']) > truncate_over:
            output_tokens = 16_384
        publish_metrics(request_count=1, http_status=200, input_tokens=len(segment['text']) // 4,
                        output_tokens=output_tokens, estimated_cost_usd=None, cost_basis='offline_test_usage')
        Path(path).write_bytes(data)
        return {'fingerprint': render_fingerprint(segment, character, scene, provider, model), 'duration': duration,
                'provider': provider, 'model': model, 'voice': character['voice'],
                'resource_usage': {'output_tokens': output_tokens}}
    return synthesize


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr('bardic.app.list_system_voices', lambda: [])
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda *_a, **_k: pytest.fail('No live provider calls allowed'))
    tts_limits.LIMITER.reset()
    with TestClient(create_app(tmp_path)) as client:
        client.app.state.runtime.api_key = 'offline-key'
        response = client.post('/api/settings', json={'tts_limits': {DEFAULT_TTS_MODEL: {'rpm': 1000, 'tpm': 10_000_000, 'rpd': 1000}}})
        assert response.status_code == 200, response.text
        yield client
    tts_limits.LIMITER.reset()


def import_story(client, paragraphs=18):
    response = client.post('/api/books', files={'file': ('harbor.txt', story(paragraphs).encode(), 'text/plain')})
    assert response.status_code == 200, response.text
    return response.json()


def wait_job(client, job_id, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = next(j for j in client.get('/api/jobs').json() if j['id'] == job_id)
        if job['status'] not in {'queued', 'running'}:
            return job
        time.sleep(.02)
    pytest.fail('chapter job did not finish')


def chapter_request(book, segment=None, **fields):
    segment = segment or book['segments'][0]
    return {'provider': 'gemini', 'voice': 'Kore', 'model': DEFAULT_TTS_MODEL, 'segment_id': segment['id'],
            'chunking': {'ramp_seconds': [10], 'target_seconds': 30, 'concurrency': 2}, **fields}


def start(client, book, **fields):
    response = client.post(f"/api/books/{book['id']}/listen/chapter", json=chapter_request(book, **fields))
    assert response.status_code == 200, response.text
    return response.json()


def test_chapter_job_generates_contiguous_chunks_with_exact_source_and_clip_projection(client, monkeypatch):
    calls = []
    monkeypatch.setattr('bardic.chapter_listening.synthesize', fake_chunk_synthesizer(calls))
    book = import_story(client)
    chapter = book['chapters'][0]
    preview = client.post(f"/api/books/{book['id']}/listen/chapter/preview", json=chapter_request(book)).json()
    assert preview['requests_needed'] >= 3 and preview['quota']['rpd'] == 1000
    assert preview['chunks'][0]['target_seconds'] == 10 and preview['passages_ready'] == 0
    result = start(client, book, intent='play')
    assert result['job']['kind'] == 'listen_chapter' and result['joined'] is False
    job = wait_job(client, result['job']['id'])
    assert job['status'] == 'completed', job
    assert job['progress'] == job['total'] == len([s for s in book['segments'] if s['chapter_id'] == chapter['id']])
    assert len(calls) == len(job['chunks']) == preview['requests_needed']
    assert all(call['pace'] is False and call['timeout'] >= 105 and call['key'] == 'offline-key' for call in calls)
    source = client.app.state.runtime.store.book(book['id'])['chapters'][0]['text']
    for call in calls:
        assert call['text'] in source, 'every request is an exact chapter slice'
    takes = client.get(f"/api/books/{book['id']}/listen/takes", params={'session_id': result['session']['id']}).json()['takes']
    assert len(takes) == job['total']
    by_chunk = {}
    for take in takes:
        audio = take['audio']
        assert audio['timing'] == 'estimated' and audio['clip_end'] > audio['clip_start']
        by_chunk.setdefault(audio['chunk_id'], []).append(audio)
    assert len(by_chunk) == len(calls)
    for clips in by_chunk.values():
        assert clips[0]['clip_start'] == 0 and clips[-1]['clip_end'] == pytest.approx(clips[-1]['chunk_duration'], abs=.01)
        assert all(a['clip_end'] == b['clip_start'] for a, b in zip(clips, clips[1:]))
        assert client.get(clips[0]['url']).status_code == 200
    # A per-passage request finds the chunk clip locally and starts no work.
    single = client.post(f"/api/books/{book['id']}/listen", json={'provider': 'gemini', 'voice': 'Kore', 'model': DEFAULT_TTS_MODEL,
                                                                 'segment_id': takes[3]['segment_id']}).json()
    assert single['cached'] is True and single['audio']['chunk_id'] == takes[3]['audio']['chunk_id']
    assert len(calls) == len(job['chunks'])
    stages = [json.loads(body)['stage'] for (body,) in client.app.state.runtime.store.connect().execute('SELECT body FROM resource_operations')]
    assert stages.count('listen_chunk') == len(calls)
    assert requests_today(client.app.state.runtime.store, DEFAULT_TTS_MODEL) == len(calls)
    # Rows are immutable.
    with pytest.raises(Exception):
        with client.app.state.runtime.store.connect() as conn:
            conn.execute('DELETE FROM listening_chunks')


def test_source_change_invalidates_chunk_clips_without_deleting_them(client, monkeypatch):
    calls = []
    monkeypatch.setattr('bardic.chapter_listening.synthesize', fake_chunk_synthesizer(calls))
    book = import_story(client, 6)
    result = start(client, book)
    assert wait_job(client, result['job']['id'])['status'] == 'completed'
    store = client.app.state.runtime.store
    repository = ListeningRepository(store)
    assert repository.chunk_clips(book['id'], result['session']['id'])
    edited = store.book(book['id'])
    edited['chapters'][0]['text'] = edited['chapters'][0]['text'].replace('harbor', 'harbour', 1)
    store.save_book(edited)
    assert repository.chunk_clips(book['id'], result['session']['id']) == {}
    with store.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM listening_chunks').fetchone()[0] == len(calls)


def test_truncated_output_is_not_retained_and_replanned_smaller(client, monkeypatch):
    calls = []
    monkeypatch.setattr('bardic.chapter_listening.synthesize', fake_chunk_synthesizer(calls, truncate_over=400))
    book = import_story(client, 8)
    result = start(client, book, chunking={'ramp_seconds': [], 'target_seconds': 60, 'concurrency': 1})
    job = wait_job(client, result['job']['id'])
    statuses = [entry['status'] for entry in job['chunks']]
    assert 'truncated' in statuses
    truncated = [entry for entry in job['chunks'] if entry['status'] == 'truncated']
    later = [entry for entry in job['chunks'][statuses.index('truncated') + 1:]]
    assert later and all(entry['chars'] < truncated[0]['chars'] for entry in later)
    with client.app.state.runtime.store.connect() as conn:
        rows = [json.loads(body) for (body,) in conn.execute('SELECT body FROM listening_chunks')]
    assert all(row['chars'] <= 400 for row in rows)
    if job['status'] == 'failed':
        assert 'audio length limit' in job['error']


def test_minute_rate_limit_retries_and_daily_quota_stops_with_saved_work(client, monkeypatch):
    calls = []
    # Second request is rejected per-minute (no audio, not charged); third hits the daily quota.
    rules = {2: ('minute', 0.01), 4: ('day', 3600)}
    monkeypatch.setattr('bardic.chapter_listening.synthesize',
                        fake_chunk_synthesizer(calls, rate_limit=lambda n: rules.get(n)))
    book = import_story(client)
    result = start(client, book, chunking={'ramp_seconds': [], 'target_seconds': 30, 'concurrency': 1})
    job = wait_job(client, result['job']['id'])
    assert job['status'] == 'quota_limited', job
    assert 'midnight Pacific' in job['message'] and job['resume_after']
    assert [entry['status'] for entry in job['chunks']][:4] == ['done', 'rate_limited', 'done', 'rate_limited']
    assert job['chunks'][1]['first_segment_id'] == job['chunks'][2]['first_segment_id'], 'the rejected chunk is retried'
    takes = client.get(f"/api/books/{book['id']}/listen/takes", params={'session_id': result['session']['id']}).json()['takes']
    assert takes, 'finished chunks stay playable'


def test_library_daily_count_blocks_before_sending(client, monkeypatch):
    calls = []
    monkeypatch.setattr('bardic.chapter_listening.synthesize', fake_chunk_synthesizer(calls))
    client.post('/api/settings', json={'tts_limits': {DEFAULT_TTS_MODEL: {'rpm': 1000, 'tpm': 10_000_000, 'rpd': 2}}})
    book = import_story(client)
    job = wait_job(client, start(client, book, chunking={'ramp_seconds': [], 'target_seconds': 30, 'concurrency': 1})['job']['id'])
    assert job['status'] == 'quota_limited' and len(calls) == 2
    assert job['quota']['requests_today'] == 2 and job['quota']['rpd'] == 2


def test_uncertain_failure_is_never_resent_and_other_work_is_kept(client, monkeypatch):
    calls = []
    monkeypatch.setattr('bardic.chapter_listening.synthesize', fake_chunk_synthesizer(calls, fail_on=lambda n: n == 2))
    book = import_story(client)
    job = wait_job(client, start(client, book, chunking={'ramp_seconds': [], 'target_seconds': 30, 'concurrency': 1})['job']['id'])
    assert job['status'] == 'failed' and 'timed out' in job['error']
    assert len(calls) == 2
    assert [entry['status'] for entry in job['chunks']] == ['done', 'failed']


def test_join_moves_focus_extends_scope_and_restarts_ramp_but_rejects_other_chapters(client, monkeypatch):
    gate = threading.Event()
    calls = []
    monkeypatch.setattr('bardic.chapter_listening.synthesize', fake_chunk_synthesizer(calls, gate=gate))
    book = import_story(client)
    segments = [s for s in book['segments'] if s['chapter_id'] == book['chapters'][0]['id']]
    first = start(client, book, segment=segments[10], chunking={'ramp_seconds': [], 'target_seconds': 30, 'concurrency': 1})
    joined = start(client, book, segment=segments[2], intent='play')
    assert joined['joined'] is True and joined['job']['id'] == first['job']['id']
    assert joined['job']['scope_start_segment_id'] == segments[2]['id']
    assert joined['job']['focus_segment_id'] == segments[2]['id'] and joined['job']['ramp_restart'] == 1
    other = client.post(f"/api/books/{book['id']}/listen/chapter", json={**chapter_request(book, segment=segments[3]), 'voice': 'Puck'})
    assert other.status_code == 409
    # Book edits are blocked while the chapter job holds the book.
    assert client.post(f"/api/books/{book['id']}/render", json={'provider': 'gemini'}).status_code == 409
    gate.set()
    job = wait_job(client, first['job']['id'])
    assert job['status'] == 'completed' and job['progress'] == len(segments) - 2


def test_cancel_retains_in_flight_chunk_and_starts_nothing_more(client, monkeypatch):
    gate = threading.Event()
    calls = []
    monkeypatch.setattr('bardic.chapter_listening.synthesize', fake_chunk_synthesizer(calls, gate=gate))
    book = import_story(client)
    result = start(client, book, chunking={'ramp_seconds': [], 'target_seconds': 30, 'concurrency': 1})
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(.01)
    client.post(f"/api/jobs/{result['job']['id']}/cancel")
    gate.set()
    job = wait_job(client, result['job']['id'])
    assert job['status'] == 'cancelled' and len(calls) == 1
    assert job['chunks'][0]['status'] == 'done'
    assert ListeningRepository(client.app.state.runtime.store).chunk_clips(book['id'], result['session']['id'])


def test_settings_validate_limits_and_chunking(client):
    bad = client.post('/api/settings', json={'tts_limits': {DEFAULT_TTS_MODEL: {'rpm': 0}}})
    assert bad.status_code == 400
    assert client.post('/api/settings', json={'tts_limits': {'unknown-model': {'rpm': 5}}}).status_code == 400
    assert client.post('/api/settings', json={'listen_chunking': {'target_seconds': 900}}).status_code == 422
    saved = client.post('/api/settings', json={'listen_chunking': {'ramp_seconds': [20, 45, 90], 'concurrency': 3}}).json()
    assert saved['listen_chunking'] == {'ramp_seconds': [20.0, 45.0, 90.0], 'target_seconds': 420.0, 'concurrency': 3}
    assert saved['tts_limits'][DEFAULT_TTS_MODEL]['rpm'] == 1000
    assert client.get('/api/status').json()['tts_rate'][DEFAULT_TTS_MODEL]['recent_requests'] == 0


class FakeFuturePool:
    """Runs submitted work synchronously so pacing can be checked with a fake clock."""

    def submit(self, fn, *args):
        from concurrent.futures import Future
        future = Future()
        try:
            future.set_result(fn(*args))
        except Exception as error:  # noqa: BLE001
            future.set_exception(error)
        return future


def test_coordinator_waits_for_the_rate_window_instead_of_sending(client, monkeypatch):
    calls = []
    book = import_story(client)
    store = client.app.state.runtime.store
    repository = ListeningRepository(store)
    session = repository.session(book['id'], 'gemini', 'Kore', DEFAULT_TTS_MODEL)
    job = store.create_job(book['id'], 'listen_chapter')
    first = book['segments'][0]
    job = store.update_job(job['id'], session_id=session['id'], chapter_id=first['chapter_id'], provider='gemini',
                           model=DEFAULT_TTS_MODEL, scope_start_segment_id=first['id'], focus_segment_id=first['id'],
                           chunking=chunking.normalize_options({'ramp_seconds': [], 'target_seconds': 30, 'concurrency': 1}),
                           limits={'rpm': 2, 'tpm': 10_000_000, 'rpd': 100}, chunks=[])
    clock = [0.0]
    limiter = RateLimiter(clock=lambda: clock[0])
    limiter.configure({DEFAULT_TTS_MODEL: {'rpm': 2, 'tpm': 10_000_000, 'rpd': 100}})
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    coordinator = ChapterCoordinator(store, job['id'], 'offline-key', FakeFuturePool(), cancelled=lambda: False,
                                     synthesizer=fake_chunk_synthesizer(calls), limiter=limiter, sleep=sleep,
                                     clock=lambda: clock[0])
    coordinator.run()
    assert len(calls) >= 3
    assert sum(sleeps) >= tts_limits.WINDOW_SECONDS * ((len(calls) - 1) // 2) - 1
    final = store.job(job['id'])
    assert all(entry['status'] == 'done' for entry in final['chunks'])


def test_analysis_export_and_storage_accounting_include_chunk_metadata(client, monkeypatch):
    import zipfile
    calls = []
    monkeypatch.setattr('bardic.chapter_listening.synthesize', fake_chunk_synthesizer(calls))
    book = import_story(client, 4)
    assert wait_job(client, start(client, book)['job']['id'])['status'] == 'completed'
    export = client.get(f"/api/books/{book['id']}/analysis-export")
    assert export.status_code == 200
    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        chunks = json.loads(archive.read('listening-chunks.json'))
        assert len(chunks) == len(calls) and all(chunk['timing']['method'] == 'pause_alignment' for chunk in chunks)
        assert not [name for name in archive.namelist() if name.endswith('.wav')]
    from bardic.library import _payload_bytes
    store = client.app.state.runtime.store
    with store.connect() as conn:
        with_chunks = _payload_bytes(conn, book['id'])
        chunk_bytes = conn.execute('SELECT SUM(length(CAST(body AS BLOB))) FROM listening_chunks').fetchone()[0]
    assert chunk_bytes and with_chunks >= chunk_bytes


# Red-team regressions: storage integrity -------------------------------------

def repo_setup(client, paragraphs=4):
    book = import_story(client, paragraphs)
    store = client.app.state.runtime.store
    repository = ListeningRepository(store)
    session = repository.session(book['id'], 'gemini', 'Kore', DEFAULT_TTS_MODEL)
    chapter_id = book['segments'][0]['chapter_id']
    ids = [s['id'] for s in book['segments'] if s['chapter_id'] == chapter_id]
    return book, store, repository, session, chapter_id, ids


def test_implausibly_short_audio_is_rejected_not_retained(client):
    book, store, repository, session, chapter_id, ids = repo_setup(client)
    calls = []
    short = fake_chunk_synthesizer(calls, rate=200.0)  # ~10x too fast: skipped text
    with pytest.raises(TruncatedChunk, match='much less audio'):
        repository.render_chunk(book['id'], session['id'], chapter_id, ids, 'key', synthesizer=short)
    assert repository.chunk_clips(book['id'], session['id']) == {}
    assert not list((store.root / 'listen-audio' / book['id']).glob('*.wav'))


def test_alignment_clips_stay_ordered_and_in_range_for_tiny_audio():
    passages = [{'id': f'p{i}', 'text': 'word ' * 20, 'gap_after': ' '} for i in range(50)]
    for duration in (0.01, 0.3, 1.0):
        clips = align(passages, duration, [(0.1, 0.12)])['clips']
        assert clips[0]['start'] == 0 and clips[-1]['end'] == duration
        assert all(0 <= c['start'] <= c['end'] <= duration for c in clips)
        assert all(a['end'] == b['start'] for a, b in zip(clips, clips[1:]))


def test_damaged_chunk_is_excluded_everywhere_once_detected(client):
    book, store, repository, session, chapter_id, ids = repo_setup(client)
    calls = []
    chunk = repository.render_chunk(book['id'], session['id'], chapter_id, ids, 'key', synthesizer=fake_chunk_synthesizer(calls))
    path = store.root / 'listen-audio' / book['id'] / f"{chunk['asset_id']}.wav"
    data = bytearray(path.read_bytes())
    middle = len(data) // 2
    data[middle:middle + 400] = bytes(400)  # same size, different content (inside speech)
    import os
    os.chmod(path, 0o644)
    path.write_bytes(bytes(data))
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert repository.cached(book['id'], session['id'], ids[1]) is None or 'chunk_id' not in repository.cached(book['id'], session['id'], ids[1])
    assert repository.chunk_clips(book['id'], session['id']) == {}, 'listing and planning agree with the failed check'
    assert all('chunk_id' not in take['audio'] for take in repository.takes(book['id'], session['id'])['takes'])


def test_recipe_change_invalidates_chunks_like_single_takes(client, monkeypatch):
    book, store, repository, session, chapter_id, ids = repo_setup(client)
    repository.render_chunk(book['id'], session['id'], chapter_id, ids, 'key', synthesizer=fake_chunk_synthesizer([]))
    assert repository.chunk_clips(book['id'], session['id'])
    monkeypatch.setattr('bardic.audio._RECIPE_VERSION', 99)
    assert repository.chunk_clips(book['id'], session['id']) == {}


def test_pronunciations_respell_chunks_and_invalidate_only_chunks_that_use_them(client):
    from bardic import audio, pronunciation
    book, store, repository, session, chapter_id, ids = repo_setup(client)
    calls = []
    repository.render_chunk(book['id'], session['id'], chapter_id, ids, 'key', synthesizer=fake_chunk_synthesizer(calls))
    assert repository.chunk_clips(book['id'], session['id'])
    saved = store.book(book['id'])
    saved['pronunciations'] = [pronunciation.normalize_entry({'term': 'Zyrrhan', 'respelling': 'Zeer-an'})]
    store.save_book(saved)
    assert repository.chunk_clips(book['id'], session['id']), 'a word the chunk does not contain changes nothing'
    saved['pronunciations'] = [pronunciation.normalize_entry({'term': 'keeper', 'respelling': 'keepah'})]
    store.save_book(saved)
    assert repository.chunk_clips(book['id'], session['id']) == {}
    sent, inner = [], fake_chunk_synthesizer(calls)

    def recording(segment, character, scene, provider, model, key, path, **kwargs):
        sent.append(audio._recipe(segment, character, scene, provider, model)['text'])
        return inner(segment, character, scene, provider, model, key, path, **kwargs)
    chunk = repository.render_chunk(book['id'], session['id'], chapter_id, ids, 'key', synthesizer=recording)
    assert 'keepah' in sent[-1] and 'keeper' not in sent[-1]
    chapter = next(c for c in store.book(book['id'])['chapters'] if c['id'] == chapter_id)
    source = chapter['text'][chunk['start']:chunk['end']]
    assert calls[-1]['text'] == source, 'the book slice itself is unchanged'
    assert source.count('keeper') == sent[-1].count('keepah') > 0
    assert repository.chunk_clips(book['id'], session['id'])


def test_replace_cannot_overwrite_a_retained_chunk(client):
    book, store, repository, session, chapter_id, ids = repo_setup(client)
    chunk = repository.render_chunk(book['id'], session['id'], chapter_id, ids, 'key', synthesizer=fake_chunk_synthesizer([]))
    with store.connect() as conn:
        conn.execute('INSERT OR REPLACE INTO listening_chunks VALUES (?,?,?,?,?,?)',
                     (chunk['id'], book['id'], session['id'], chapter_id, 'f' * 64, '{}'))
        row = conn.execute('SELECT asset_id, body FROM listening_chunks WHERE id=?', (chunk['id'],)).fetchone()
    assert row[0] == chunk['asset_id'] and json.loads(row[1])['id'] == chunk['id']


def test_source_change_during_request_publishes_no_asset(client):
    book, store, repository, session, chapter_id, ids = repo_setup(client)
    inner = fake_chunk_synthesizer([])

    def editing(*args, **kwargs):
        result = inner(*args, **kwargs)
        edited = store.book(book['id'])
        edited['chapters'][0]['text'] = edited['chapters'][0]['text'].replace('harbor', 'harbour', 1)
        store.save_book(edited)
        return result
    with pytest.raises(ValueError, match='changed during narration'):
        repository.render_chunk(book['id'], session['id'], chapter_id, ids, 'key', synthesizer=editing)
    assert not list((store.root / 'listen-audio' / book['id']).glob('*.wav'))


# Red-team regressions: requests, quota and cancellation -----------------------

def test_daily_quota_rejection_blocks_new_jobs_until_limits_are_saved(client, monkeypatch):
    calls = []
    monkeypatch.setattr('bardic.chapter_listening.synthesize',
                        fake_chunk_synthesizer(calls, rate_limit=lambda n: ('day', 3600) if n == 1 else None))
    book = import_story(client)
    job = wait_job(client, start(client, book, chunking={'ramp_seconds': [], 'target_seconds': 30, 'concurrency': 1})['job']['id'])
    assert job['status'] == 'quota_limited' and len(calls) == 1
    again = client.post(f"/api/books/{book['id']}/listen/chapter", json=chapter_request(book))
    assert again.status_code == 429 and 'midnight Pacific' in again.json()['detail']
    assert len(calls) == 1, 'no request is sent while the daily block holds'
    assert client.get('/api/status').json()['tts_rate'][DEFAULT_TTS_MODEL]['daily_block_seconds'] > 0
    # Saving limits (for example after a tier upgrade) is the explicit way to lift it.
    client.post('/api/settings', json={'tts_limits': {DEFAULT_TTS_MODEL: {'rpm': 1000, 'tpm': 10_000_000, 'rpd': 2000}}})
    resumed = wait_job(client, start(client, book, chunking={'ramp_seconds': [], 'target_seconds': 30, 'concurrency': 1})['job']['id'])
    assert resumed['status'] == 'completed' and len(calls) > 1


def test_paced_wait_honours_cancellation_and_never_waits_unbounded():
    from bardic.tts_limits import CANCEL_CHECK
    clock = [0.0]
    limiter = RateLimiter(clock=lambda: clock[0])
    limiter.configure({'m': {'rpm': 1, 'tpm': 10_000, 'rpd': 100}})
    limiter.acquire('m', 1, sleep=lambda s: None)
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    class Stop(Exception):
        pass
    state = {'cancel': False}

    def check():
        if state['cancel']:
            raise Stop()
    token = CANCEL_CHECK.set(check)
    try:
        def cancel_soon(seconds):
            sleep(seconds)
            state['cancel'] = len(sleeps) >= 3
        with pytest.raises(Stop):
            limiter.acquire('m', 1, sleep=cancel_soon)
    finally:
        CANCEL_CHECK.reset(token)
    assert limiter.view('m')['recent_requests'] == 1, 'the cancelled wait recorded no send'
    limiter.cool_down('m', 3600)
    with pytest.raises(RateLimited) as error:
        limiter.acquire('m', 1, sleep=sleep)
    assert error.value.scope == 'minute' and sum(sleeps) < 130
    limiter.block_day('m', 7200)
    with pytest.raises(RateLimited) as daily:
        limiter.acquire('m', 1, sleep=sleep)
    assert daily.value.scope == 'day'


def test_new_job_inherits_calibration_and_learns_before_parallel_full_chunks(client, monkeypatch):
    calls = []
    monkeypatch.setattr('bardic.chapter_listening.synthesize', fake_chunk_synthesizer(calls, truncate_over=900))
    book = import_story(client, 30)
    options = {'ramp_seconds': [], 'target_seconds': 300, 'concurrency': 3}
    first = wait_job(client, start(client, book, chunking=options)['job']['id'])
    starts = [entry['started_at'] for entry in first['chunks']]
    assert first['chunks'][0]['status'] == 'truncated'
    assert starts[1] >= first['chunks'][0]['finished_at'], 'no second full-size request before the first is measured'
    assert first['calibration']['max_chars'] and first['calibration']['max_chars'] < first['chunks'][0]['chars']
    second = client.post(f"/api/books/{book['id']}/listen/chapter/preview", json=chapter_request(book, chunking=options)).json()
    assert second['calibration']['max_chars'] == first['calibration']['max_chars'], 'preview uses learned ceilings'
    assert all(chunk['chars'] <= first['calibration']['max_chars'] for chunk in second['chunks'])


def test_join_arriving_as_the_job_closes_is_not_lost(client, monkeypatch):
    calls = []
    monkeypatch.setattr('bardic.chapter_listening.synthesize', fake_chunk_synthesizer(calls))
    import bardic.chapter_listening as module
    real_plan = module.plan
    store = client.app.state.runtime.store
    book = import_story(client, 6)
    segments = [s for s in book['segments'] if s['chapter_id'] == book['chapters'][0]['id']]
    injected = {'done': False}

    def racing_plan(*args, **kwargs):
        result = real_plan(*args, **kwargs)
        if not result and not injected['done']:
            injected['done'] = True
            # A listener joins after this pass read the job, before it closes.
            job = next(j for j in store.jobs(book['id']) if j['kind'] == 'listen_chapter')
            store.update_job(job['id'], joins=job.get('joins', 0) + 1, scope_start_segment_id=segments[0]['id'],
                             focus_segment_id=segments[0]['id'])
        return result
    monkeypatch.setattr(module, 'plan', racing_plan)
    job = wait_job(client, start(client, book, segment=segments[4], chunking={'ramp_seconds': [], 'target_seconds': 30, 'concurrency': 1})['job']['id'])
    assert job['status'] == 'completed' and injected['done']
    clips = ListeningRepository(store).chunk_clips(book['id'], job['session_id'])
    assert all(segment['id'] in clips for segment in segments), 'the late join scope was generated'
    closing = client.post(f"/api/books/{book['id']}/listen/chapter", json=chapter_request(book))
    assert closing.status_code == 200  # a closed job is not joined; a new job may start


def test_daily_count_is_recounted_before_each_send(client, monkeypatch):
    from bardic.resources import ResourceLedger
    calls = []
    inner = fake_chunk_synthesizer(calls)
    store = client.app.state.runtime.store

    def with_other_traffic(*args, **kwargs):
        result = inner(*args, **kwargs)
        if len(calls) == 1:
            # Another path (for example a voice example) spends a request meanwhile.
            def other():
                with ResourceLedger(store).operation('elsewhere', 'voice_preview', provider='gemini', model=DEFAULT_TTS_MODEL,
                                                     kind='narration') as metrics:
                    metrics.update(request_count=1)
            threading.Thread(target=other).start()
            time.sleep(.1)
        return result
    monkeypatch.setattr('bardic.chapter_listening.synthesize', with_other_traffic)
    client.post('/api/settings', json={'tts_limits': {DEFAULT_TTS_MODEL: {'rpm': 1000, 'tpm': 10_000_000, 'rpd': 3}}})
    book = import_story(client)
    job = wait_job(client, start(client, book, chunking={'ramp_seconds': [], 'target_seconds': 30, 'concurrency': 1})['job']['id'])
    assert job['status'] == 'quota_limited'
    assert len(calls) == 2, 'the other request counted against the daily limit'
