"""Fallback narrator, re-recording and the detailed status of saved performances.

Every test is offline. Synthesizers are fakes writing small WAVs that fail on chosen text or voices; prose is
original and synthetic; no provider request can leave the process.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from bardic import performance_status
from bardic.audio import AudioError, render_fingerprint, voice_id
from test_audio import wav_bytes
from test_performances import (audio, client, create, import_book, long_text, passages, request,  # noqa: F401
                               wait_for, wait_job)

MAIN, BACKUP = 'Samantha', 'Alex'


def voices(monkeypatch, fail=lambda text, voice: False, gate=None):
    """A fake speech engine for every provider; ``fail(text, voice)`` makes a reading raise AudioError."""
    calls = []

    def synthesize(segment, character, scene, provider, model, key, path, **_kwargs):
        voice = voice_id(character, provider)
        calls.append({'id': segment['id'], 'text': segment['text'], 'voice': voice, 'provider': provider})
        if gate is not None:
            assert gate(len(calls)), 'test synthesizer was not released'
        if fail(segment['text'], voice):
            raise AudioError('The fake engine refused this passage.')
        path.write_bytes(wav_bytes(frames=2400 + 16 * len(calls)))
        return {'fingerprint': render_fingerprint(segment, character, scene, provider, model), 'duration': .1,
                'provider': provider, 'model': model, 'voice': voice}

    monkeypatch.setattr('bardic.performances.synthesize', synthesize)
    monkeypatch.setattr('bardic.app.synthesize', synthesize)
    return calls


def status(client, book, performance):
    response = client.get(f"/api/books/{book['id']}/performances/{performance['id']}/status")
    assert response.status_code == 200, response.text
    return response.json()


def performance_of(client, book, performance_id):
    return client.get(f"/api/books/{book['id']}/performances/{performance_id}").json()['performance']


def settled(client, made):
    """The performance of a create result, once its job has finished (a book runs one job at a time)."""
    if made['job']:
        wait_job(client, made['job']['id'])
    return made['performance']


def fallback_choice(voice=BACKUP, provider='system'):
    return {'provider': provider, 'voice': voice}


def bad_passage(book, chapter):
    return passages(book, chapter)[1]


# Settings and pinning ------------------------------------------------------------

def test_the_saved_fallback_narrator_is_validated_saved_and_cleared(client):
    assert client.get('/api/status').json()['fallback_narrator'] is None
    saved = client.post('/api/settings', json={'fallback_narrator': fallback_choice()})
    assert saved.status_code == 200 and saved.json()['fallback_narrator'] == fallback_choice()
    assert client.get('/api/status').json()['fallback_narrator'] == fallback_choice()
    # An unrelated update keeps it; clearing removes it.
    assert client.post('/api/settings', json={'tts_model': saved.json()['tts_model']}).json()['fallback_narrator'] == fallback_choice()
    assert client.post('/api/settings', json={'fallback_narrator': {'provider': None}}).json()['fallback_narrator'] is None
    unknown = client.post('/api/settings', json={'fallback_narrator': {'provider': 'nowhere', 'voice': ''}})
    assert unknown.status_code == 400 and unknown.json()['code'] == 'fallback_unsupported'
    # An unusable voice is refused before anything is saved.
    bad = client.post('/api/settings', json={'fallback_narrator': {'provider': 'breeze', 'voice': ''}})
    assert bad.status_code == 400 and bad.json()['code'] == 'narrator_voice_invalid'
    assert client.get('/api/status').json()['fallback_narrator'] is None


def test_a_performance_pins_its_fallback_and_a_later_default_does_not_change_it(client, monkeypatch):
    voices(monkeypatch)
    book = import_book(client)
    one = book['chapters'][0]
    client.post('/api/settings', json={'fallback_narrator': fallback_choice()})
    pinned = settled(client, create(client, book, [one]))
    assert pinned['fallback'] == {'provider': 'system', 'voice': BACKUP, 'automatic': False,
                                  'label': f'{BACKUP} · Device voices', 'available': True}
    override = settled(client, create(client, book, [book['chapters'][1]], fallback=fallback_choice('Fred')))
    assert override['fallback']['voice'] == 'Fred'
    client.post('/api/settings', json={'fallback_narrator': {'provider': None}})
    assert performance_of(client, book, pinned['id'])['fallback']['voice'] == BACKUP
    automatic = settled(client, create(client, book, [book['chapters'][2]]))
    assert automatic['fallback']['automatic'] is True and automatic['fallback']['provider'] == 'system'
    preview = client.post(f"/api/books/{book['id']}/performances/preview", json=request(book, [one], fallback=fallback_choice('Fred'))).json()
    assert preview['fallback']['voice'] == 'Fred'


def test_resuming_can_change_the_pinned_fallback(client, monkeypatch):
    calls = voices(monkeypatch)
    book = import_book(client)
    one = book['chapters'][0]
    made = create(client, book, [one])
    wait_job(client, made['job']['id'])
    changed = client.post(f"/api/books/{book['id']}/performances/{made['performance']['id']}/prepare",
                          json={'fallback': fallback_choice('Fred')})
    assert changed.status_code == 200 and changed.json()['performance']['fallback']['voice'] == 'Fred'
    assert performance_of(client, book, made['performance']['id'])['fallback']['voice'] == 'Fred'
    assert client.post(f"/api/books/{book['id']}/performances/{made['performance']['id']}/prepare",
                       json={'fallback': {'provider': 'nowhere', 'voice': ''}}).status_code == 422
    assert len(calls) == len(passages(book, one))


# Fallback reading -------------------------------------------------------------------

def test_a_passage_the_main_narrator_cannot_produce_is_read_by_the_fallback_and_noted(client, monkeypatch):
    book = import_book(client)
    one = book['chapters'][0]
    poison = bad_passage(book, one)
    calls = voices(monkeypatch, fail=lambda text, voice: voice == MAIN and text == poison['text'])
    made = create(client, book, [one], fallback=fallback_choice())
    job = wait_job(client, made['job']['id'])
    assert job['status'] == 'completed' and job['fallback']['voice'] == BACKUP, job
    record = performance_of(client, book, made['performance']['id'])
    progress = record['progress']
    assert progress['passages_ready'] == progress['passages_total'] == len(passages(book, one))
    assert progress['passages_fallback'] == 1 and progress['fallback_reasons'] == {'content_blocked': 0, 'failed': 1}
    assert progress['fallback_provider'] == 'system' and progress['chapters'][0]['passages_fallback'] == 1
    assert 'read by a device voice because the narrator could not produce it' in job['message']
    ready = audio(client, book, record)
    assert ready[poison['id']]['voice'] == BACKUP
    assert ready[poison['id']]['substitute']['reason'] == 'failed' and ready[poison['id']]['substitute']['override_id']
    assert all('substitute' not in item for key, item in ready.items() if key != poison['id'])
    assert client.get(ready[poison['id']]['url']).status_code == 200
    # The fallback read exactly that passage, once; a resume asks for nothing.
    assert [call['voice'] for call in calls if call['id'] == poison['id']] == [MAIN, BACKUP]
    sent = len(calls)
    assert client.post(f"/api/books/{book['id']}/performances/{record['id']}/prepare").json()['job'] is None
    assert len(calls) == sent
    # The status notes it, durably.
    note = status(client, book, record)['notes']
    assert [(n['passage_id'], n['reason'], n['voice']) for n in note] == [(poison['id'], 'failed', BACKUP)]
    assert note[0]['excerpt'] == poison['text'][:80]


def test_a_passage_no_narrator_can_read_stays_unrecorded_and_the_rest_completes(client, monkeypatch):
    book = import_book(client)
    one = book['chapters'][0]
    poison = bad_passage(book, one)
    voices(monkeypatch, fail=lambda text, voice: text == poison['text'])
    made = create(client, book, [one], fallback=fallback_choice())
    job = wait_job(client, made['job']['id'])
    assert job['status'] == 'completed' and job['message'].startswith('Performance prepared.'), job
    assert '1 passage could not be recorded by any narrator' in job['message']
    detail = status(client, book, made['performance'])
    assert detail['totals']['passages_remaining'] == 1 and detail['state'] == 'partial'
    assert [issue['passage_id'] for issue in detail['run']['issues']] == [poison['id']]
    assert detail['run']['issues'][0]['outcome'] == 'unrecorded' and detail['chapters'][0]['state'] == 'partial'


def test_without_a_usable_fallback_a_failing_passage_still_fails_the_job(client, monkeypatch):
    book = import_book(client)
    one = book['chapters'][0]
    poison = bad_passage(book, one)
    voices(monkeypatch, fail=lambda text, voice: text == poison['text'])
    # A Gemini fallback with no Gemini key cannot read anything: the pinned choice is kept but unavailable.
    unusable = fallback_choice('Kore', 'gemini')
    preview = client.post(f"/api/books/{book['id']}/performances/preview", json=request(book, [one], fallback=unusable)).json()
    assert preview['fallback']['available'] is False and any('not available now' in note for note in preview['notes'])
    made = create(client, book, [one], fallback=unusable)
    job = wait_job(client, made['job']['id'])
    assert job['status'] == 'failed' and 'fake engine' in job['error'], job
    assert job['fallback'] is None
    progress = performance_of(client, book, made['performance']['id'])['progress']
    assert progress['passages_fallback'] == 0 and progress['passages_ready'] < progress['passages_total']


def test_a_cast_passage_that_keeps_failing_is_read_by_the_fallback_and_the_cast_carries_on(client, monkeypatch):
    book = import_book(client)
    one, two, _ = book['chapters']
    poison = bad_passage(book, one)
    calls = voices(monkeypatch, fail=lambda text, voice: voice != BACKUP and text == poison['text'])
    body = {'mode': 'cast', 'provider': 'system', 'chapter_ids': [one['id'], two['id']], 'fallback': fallback_choice()}
    made = client.post(f"/api/books/{book['id']}/performances", json=body)
    assert made.status_code == 200, made.text
    job = wait_job(client, made.json()['job']['id'])
    assert job['status'] == 'completed', job
    record = performance_of(client, book, made.json()['performance']['id'])
    progress = record['progress']
    assert progress['passages_ready'] == progress['passages_total'] and progress['passages_fallback'] == 1
    ready = audio(client, book, record)
    assert ready[poison['id']]['substitute']['reason'] == 'failed' and ready[poison['id']]['voice'] == BACKUP
    assert len(ready) == len(passages(book, one, two))
    sent = len(calls)
    assert client.post(f"/api/books/{book['id']}/performances/{record['id']}/prepare").json()['job'] is None
    assert len(calls) == sent


def test_a_daily_quota_is_never_a_passage_failure(client, monkeypatch):
    from bardic.chapter_listening import QuotaReached
    book = import_book(client)
    one = book['chapters'][0]

    def limited(segment, character, scene, provider, model, key, path, **_kwargs):
        raise QuotaReached('The daily quota is used up.', '2026-09-30T07:00:00+00:00')

    monkeypatch.setattr('bardic.performances.synthesize', limited)
    monkeypatch.setattr('bardic.app.synthesize', limited)
    made = create(client, book, [one], fallback=fallback_choice())
    job = wait_job(client, made['job']['id'])
    assert job['status'] == 'quota_limited', job
    assert performance_of(client, book, made['performance']['id'])['progress']['passages_fallback'] == 0


# Re-recording --------------------------------------------------------------------------

def ready_performance(client, monkeypatch, fail=lambda text, voice: False, mode='simple'):
    calls = voices(monkeypatch, fail=fail)
    book = import_book(client)
    one, two, three = book['chapters']
    if mode == 'simple':
        made = create(client, book, [one, two])
    else:
        body = {'mode': 'cast', 'provider': 'system', 'chapter_ids': [one['id'], two['id']]}
        made = client.post(f"/api/books/{book['id']}/performances", json=body).json()
    assert wait_job(client, made['job']['id'])['status'] == 'completed'
    return book, (one, two, three), made['performance'], calls


def rerecord(client, book, performance, **body):
    return client.post(f"/api/books/{book['id']}/performances/{performance['id']}/rerecord",
                       json={'provider': 'system', 'voice': 'Fred', **body})


@pytest.mark.parametrize('mode', ['simple', 'cast'])
def test_rerecording_a_chapter_keeps_the_original_and_can_be_undone(client, monkeypatch, mode):
    book, (one, two, _), performance, calls = ready_performance(client, monkeypatch, mode=mode)
    before = audio(client, book, performance)
    preview = client.post(f"/api/books/{book['id']}/performances/{performance['id']}/rerecord/preview",
                          json={'provider': 'system', 'voice': 'Fred', 'chapter_id': two['id']})
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert plan['passages_total'] == len(passages(book, two)) and plan['chapter_ids'] == [two['id']]
    assert plan['requests_estimate'] == 0 and plan['problems'] == [] and plan['quota'] is None
    sent = len(calls)
    started = rerecord(client, book, performance, chapter_id=two['id'])
    assert started.status_code == 200, started.text
    job = wait_job(client, started.json()['job']['id'])
    assert job['status'] == 'completed' and job['fallback']['voice'] == 'Fred', job
    assert job['message'].startswith(f"Re-recorded {len(passages(book, two))} passages with Fred")
    now = audio(client, book, performance)
    for segment in passages(book, one):
        assert now[segment['id']] == before[segment['id']]
    for segment in passages(book, two):
        assert now[segment['id']]['voice'] == 'Fred' and now[segment['id']]['substitute']['reason'] == 'rerecord'
    progress = performance_of(client, book, performance['id'])['progress']
    assert progress['passages_rerecorded'] == len(passages(book, two)) and progress['passages_fallback'] == 0
    assert progress['passages_ready'] == progress['passages_total']
    assert len(calls) > sent
    # Every retained take can be listed; the newest is current.
    first = passages(book, two)[0]
    takes = client.get(f"/api/books/{book['id']}/performances/{performance['id']}/takes",
                       params={'passage_id': first['id']}).json()['takes']
    assert [(t['reason'], t['current'], t['voice']) for t in takes] == [('rerecord', True, 'Fred')]
    assert takes[0]['audio']['url'] == now[first['id']]['url'] and client.get(takes[0]['audio']['url']).status_code == 200
    # Back to the original: the performance's own audio plays again; the re-record stays retained.
    back = client.post(f"/api/books/{book['id']}/performances/{performance['id']}/takes/restore",
                       json={'passage_ids': [first['id']]})
    assert back.status_code == 200, back.text
    assert audio(client, book, performance)[first['id']] == before[first['id']]
    listed = client.get(f"/api/books/{book['id']}/performances/{performance['id']}/takes", params={'passage_id': first['id']}).json()['takes']
    assert [(t['action'], t['current']) for t in listed] == [('original', True), ('use', False)]
    # And forward again to the earlier take.
    forward = client.post(f"/api/books/{book['id']}/performances/{performance['id']}/takes/restore",
                          json={'passage_ids': [first['id']], 'take_id': listed[1]['id']})
    assert forward.status_code == 200, forward.text
    again = audio(client, book, performance)[first['id']]
    assert again['voice'] == 'Fred' and again['substitute']['reason'] == 'rerecord'
    final = client.get(f"/api/books/{book['id']}/performances/{performance['id']}/takes", params={'passage_id': first['id']}).json()['takes']
    assert len(final) == 3 and [t['current'] for t in final] == [True, False, False] and final[0]['restored_from'] == listed[1]['id']


def test_rerecording_only_the_fallback_passages_and_scope_validation(client, monkeypatch):
    book = import_book(client)
    one = book['chapters'][0]
    poison = bad_passage(book, one)
    voices(monkeypatch, fail=lambda text, voice: voice == MAIN and text == poison['text'])
    made = create(client, book, [one], fallback=fallback_choice())
    assert wait_job(client, made['job']['id'])['status'] == 'completed'
    performance = made['performance']
    started = rerecord(client, book, performance, only='fallback', voice='Fred')
    assert started.status_code == 200, started.text
    job = wait_job(client, started.json()['job']['id'])
    assert job['status'] == 'completed' and job['total'] == 1
    ready = audio(client, book, performance)
    assert ready[poison['id']]['voice'] == 'Fred' and ready[poison['id']]['substitute']['reason'] == 'rerecord'
    progress = performance_of(client, book, performance['id'])['progress']
    assert progress['passages_fallback'] == 0 and progress['passages_rerecorded'] == 1
    # Nothing is read by a fallback any more, so `only: fallback` matches nothing.
    empty = rerecord(client, book, performance, only='fallback')
    assert empty.status_code == 400 and empty.json()['code'] == 'nothing_to_rerecord'
    segments = passages(book, one)
    checks = [({'passage_ids': ['nope']}, 'unknown_passage'), ({'chapter_id': 'nope'}, 'unknown_chapter'),
              ({'from_passage_id': segments[0]['id']}, 'range_incomplete'),
              ({'from_passage_id': segments[3]['id'], 'to_passage_id': segments[1]['id']}, 'range_invalid')]
    for body, code in checks:
        response = rerecord(client, book, performance, **body)
        assert response.status_code == 400 and response.json()['code'] == code, (body, response.text)
    ranged = rerecord(client, book, performance, from_passage_id=segments[0]['id'], to_passage_id=segments[1]['id'])
    assert ranged.status_code == 200 and wait_job(client, ranged.json()['job']['id'])['total'] == 2


def test_a_failing_rerecord_stops_with_finished_passages_kept_and_never_falls_back(client, monkeypatch):
    book, (one, two, _), performance, calls = ready_performance(client, monkeypatch)
    target = passages(book, two)
    stop_at = target[2]['text']
    voices(monkeypatch, fail=lambda text, voice: voice == 'Fred' and text == stop_at)
    started = rerecord(client, book, performance, chapter_id=two['id'])
    job = wait_job(client, started.json()['job']['id'])
    assert job['status'] == 'failed' and 'could not be re-recorded' in job['error'] and job['progress'] == 2
    ready = audio(client, book, performance)
    assert [ready[s['id']].get('voice') for s in target[:2]] == ['Fred', 'Fred'], 'finished passages are kept'
    assert ready[target[2]['id']]['voice'] == MAIN and 'substitute' not in ready[target[2]['id']], 'no automatic fallback'


def test_restore_is_validated_stale_takes_are_hidden_and_a_busy_book_refuses_a_rerecord(client, monkeypatch):
    book, (one, two, _), performance, _ = ready_performance(client, monkeypatch)
    path = f"/api/books/{book['id']}/performances/{performance['id']}/takes/restore"
    listing = f"/api/books/{book['id']}/performances/{performance['id']}/takes"
    first = passages(book, two)[0]
    assert client.post(path, json={'passage_ids': ['nope']}).json()['code'] == 'unknown_passage'
    assert client.post(path, json={'passage_ids': [first['id']], 'take_id': 'nope'}).json()['code'] == 'take_not_found'
    assert wait_job(client, rerecord(client, book, performance, chapter_id=two['id']).json()['job']['id'])['status'] == 'completed'
    take = client.get(listing, params={'passage_id': first['id']}).json()['takes'][0]
    many = client.post(path, json={'passage_ids': [first['id'], passages(book, two)[1]['id']], 'take_id': take['id']})
    assert many.status_code == 400 and many.json()['code'] == 'restore_ambiguous'
    # A changed passage makes its old takes stale: they are neither listed nor selectable.
    from test_performances import edit_passage
    store = client.app.state.runtime.store
    edited = passages(book, two)[1]  # the first passage is the chapter heading
    old_take = client.get(listing, params={'passage_id': edited['id']}).json()['takes'][0]
    edit_passage(store, book['id'], edited['id'], 'Rain', 'Hail')
    assert client.get(listing, params={'passage_id': edited['id']}).json()['takes'] == []
    stale = client.post(path, json={'passage_ids': [edited['id']], 'take_id': old_take['id']})
    assert stale.status_code == 400 and stale.json()['code'] == 'take_stale'
    # One job at a time per book.
    gate = threading.Event()
    voices(monkeypatch, gate=lambda n: gate.wait(5))
    started = rerecord(client, book, performance, chapter_id=one['id'])
    assert started.status_code == 200, started.text
    wait_for(lambda: store.job(started.json()['job']['id'])['status'] == 'running')
    busy = rerecord(client, book, performance, chapter_id=two['id'])
    assert busy.status_code == 409 and busy.json()['code'] == 'job_active'
    gate.set()
    assert wait_job(client, started.json()['job']['id'])['status'] == 'completed'


# Status -----------------------------------------------------------------------------------

def test_status_of_a_complete_performance_reports_chapters_totals_and_no_work_left(client, monkeypatch):
    book, (one, two, _), performance, _ = ready_performance(client, monkeypatch)
    detail = status(client, book, performance)
    assert detail['state'] == 'complete' and detail['performance_id'] == performance['id']
    assert detail['eta']['basis'] == 'none' and detail['eta']['seconds'] == 0 and detail['eta']['finishes_at'] is not None
    assert [row['id'] for row in detail['chapters']] == [one['id'], two['id']]
    assert all(row['state'] == 'done' and row['passages_remaining'] == 0 and row['eta_seconds'] is None for row in detail['chapters'])
    totals = detail['totals']
    assert totals['passages_ready'] == totals['passages_total'] == len(passages(book, one, two))
    assert totals['chars_ready'] == totals['chars_total'] == sum(len(s['text']) for s in passages(book, one, two))
    assert detail['run']['kind'] == 'record' and detail['run']['status'] == 'completed' and detail['notes'] == []


def test_status_of_a_running_recording_says_speed_is_unknown_until_measured(client, monkeypatch):
    book, (one, two, three), performance, _ = ready_performance(client, monkeypatch)
    gate = threading.Event()
    voices(monkeypatch, gate=lambda n: gate.wait(5))
    grown = client.post(f"/api/books/{book['id']}/performances/{performance['id']}/chapters", json={'chapter_ids': [three['id']]})
    assert grown.status_code == 200, grown.text
    job_id = grown.json()['job']['id']
    wait_for(lambda: client.app.state.runtime.store.job(job_id)['status'] == 'running')
    detail = status(client, book, performance)
    assert detail['state'] == 'recording' and detail['run']['kind'] == 'record'
    assert detail['eta']['basis'] == 'unknown' and detail['eta']['seconds'] is None and detail['eta']['note']
    states = {row['id']: row['state'] for row in detail['chapters']}
    assert states[one['id']] == states[two['id']] == 'done' and states[three['id']] == 'active'
    assert detail['chapters'][2]['passages_remaining'] == len(passages(book, three))
    gate.set()
    wait_job(client, job_id)


def test_status_measures_speed_from_the_current_run_and_projects_chapter_finish_times(client, monkeypatch):
    book = import_book(client, long_text(3, 6))
    chapters = book['chapters']
    release = threading.Event()
    voices(monkeypatch, gate=lambda n: n <= 6 or release.wait(5))
    made = create(client, book, chapters)
    job_id = made['job']['id']
    store = client.app.state.runtime.store
    wait_for(lambda: store.job(job_id).get('progress', 0) >= 6)
    record = store.job(job_id)
    started = datetime.fromisoformat(record['work_started_at'])
    runtime = client.app.state.runtime
    from bardic.performances import PerformanceRepository
    performance = PerformanceRepository(store).get(book['id'], made['performance']['id'])
    soon = performance_status.status(runtime, performance, at=started + timedelta(seconds=2))
    assert soon['eta']['basis'] == 'unknown', 'too early to trust a speed'
    later = performance_status.status(runtime, performance, at=started + timedelta(seconds=60))
    eta = later['eta']
    assert eta['basis'] == 'measured' and eta['rate_chars_per_second'] > 0 and eta['seconds'] > 0
    assert eta['chars_remaining'] == later['totals']['chars_remaining'] > 0
    finish = datetime.fromisoformat(eta['finishes_at']) - datetime.fromisoformat(later['generated_at'])
    assert finish.total_seconds() == pytest.approx(eta['seconds'], abs=.2)
    etas = [row['eta_seconds'] for row in later['chapters'] if row['eta_seconds'] is not None]
    assert etas == sorted(etas) and etas[-1] == pytest.approx(eta['seconds'], abs=.2), 'later chapters finish later'
    assert later['run']['passages_done'] >= 6 and later['run']['elapsed_seconds'] == pytest.approx(60, abs=.2)
    release.set()
    wait_job(client, job_id)


def test_status_of_a_rerecord_reports_per_chapter_progress(client, monkeypatch):
    book, (one, two, _), performance, _ = ready_performance(client, monkeypatch)
    gate = threading.Event()
    voices(monkeypatch, gate=lambda n: n <= 2 or gate.wait(5))
    started = rerecord(client, book, performance)
    job_id = started.json()['job']['id']
    store = client.app.state.runtime.store
    wait_for(lambda: store.job(job_id).get('progress', 0) >= 2)
    detail = status(client, book, performance)
    run = detail['run']
    assert run['kind'] == 'rerecord' and run['narrator_label'] == 'Fred · Device voices' and run['passages_done'] >= 2
    assert run['passages_total'] == len(passages(book, one, two))
    first = next(row for row in detail['chapters'] if row['id'] == one['id'])
    assert first['run_passages'] == len(passages(book, one)) and first['run_done'] >= 2 and first['state'] == 'active'
    assert detail['state'] == 'recording'
    gate.set()
    assert wait_job(client, job_id)['status'] == 'completed'
    done = status(client, book, performance)
    assert all(row['run_done'] == row['run_passages'] and row['state'] == 'done' for row in done['chapters'])
    assert done['totals']['passages_rerecorded'] == len(passages(book, one, two))


def test_the_new_operations_refuse_unknown_performances(client):
    book = import_book(client)
    base = f"/api/books/{book['id']}/performances/pf_missing"
    for method, path, body in (('get', '/status', None), ('get', '/takes', None),
                               ('post', '/rerecord/preview', {'provider': 'system'}), ('post', '/rerecord', {'provider': 'system'}),
                               ('post', '/takes/restore', {'passage_ids': ['x']})):
        response = getattr(client, method)(base + path, **({} if body is None else {'json': body}))
        assert response.status_code == 404 and response.json()['code'] == 'performance_not_found', (path, response.text)


# Red-team regressions ---------------------------------------------------------------------

def test_a_narrator_that_fails_every_passage_stops_the_job_instead_of_swapping_the_whole_book(client, monkeypatch):
    book = import_book(client, long_text(1, 10))
    one = book['chapters'][0]
    voices(monkeypatch, fail=lambda text, voice: voice != BACKUP)
    made = create(client, book, [one], fallback=fallback_choice())
    job = wait_job(client, made['job']['id'])
    assert job['status'] == 'failed' and 'looks unavailable' in job['error'], job
    progress = performance_of(client, book, made['performance']['id'])['progress']
    assert progress['passages_fallback'] == 5 and progress['passages_ready'] == 5, 'what was read stays noted, the rest waits'


def test_a_success_between_failures_resets_the_streak(client, monkeypatch):
    book = import_book(client, long_text(1, 10))
    one = book['chapters'][0]
    texts = [s['text'] for s in passages(book, one)]
    bad = {text for index, text in enumerate(texts) if index % 2 == 0}  # every other passage fails: never six in a row
    voices(monkeypatch, fail=lambda text, voice: voice != BACKUP and text in bad)
    made = create(client, book, [one], fallback=fallback_choice())
    assert wait_job(client, made['job']['id'])['status'] == 'completed'


def test_an_empty_segment_list_is_refused_not_read_as_everything(client, monkeypatch):
    book, (one, two, _), performance, _ = ready_performance(client, monkeypatch)
    response = rerecord(client, book, performance, passage_ids=[])
    assert response.status_code == 422


def test_restoring_the_original_of_a_passage_that_never_had_one_is_refused(client, monkeypatch):
    book = import_book(client)
    one = book['chapters'][0]
    poison = bad_passage(book, one)
    voices(monkeypatch, fail=lambda text, voice: voice == MAIN and text == poison['text'])
    made = create(client, book, [one], fallback=fallback_choice())
    assert wait_job(client, made['job']['id'])['status'] == 'completed'
    response = client.post(f"/api/books/{book['id']}/performances/{made['performance']['id']}/takes/restore",
                           json={'passage_ids': [poison['id']]})
    assert response.status_code == 400 and response.json()['code'] == 'no_original'
    assert audio(client, book, made['performance'])[poison['id']]['voice'] == BACKUP


def test_restore_is_refused_while_a_job_runs(client, monkeypatch):
    book, (one, two, _), performance, _ = ready_performance(client, monkeypatch)
    gate = threading.Event()
    voices(monkeypatch, gate=lambda n: gate.wait(5))
    started = rerecord(client, book, performance, chapter_id=two['id'])
    store = client.app.state.runtime.store
    wait_for(lambda: store.job(started.json()['job']['id'])['status'] == 'running')
    response = client.post(f"/api/books/{book['id']}/performances/{performance['id']}/takes/restore",
                           json={'passage_ids': [passages(book, two)[0]['id']]})
    assert response.status_code == 409 and response.json()['code'] == 'job_active'
    gate.set()
    wait_job(client, started.json()['job']['id'])


def test_only_the_chunk_that_caused_the_failure_is_blamed():
    from bardic.performances import failed_chunk
    segments = [{'id': f's{i}'} for i in range(6)]
    truncated = {'status': 'truncated', 'error': 'cut short', 'first_segment_id': 's0', 'last_segment_id': 's2'}
    limited = {'status': 'rate_limited', 'error': 'HTTP 429', 'first_segment_id': 's3', 'last_segment_id': 's5'}
    rate_stop = {'error': 'Gemini kept rejecting requests for its rate limit.', 'chunks': [truncated, limited]}
    assert failed_chunk(rate_stop, segments, {}) == (None, [])
    real = {'status': 'failed', 'error': 'Gemini returned HTTP 500.', 'first_segment_id': 's3', 'last_segment_id': 's5'}
    entry, bad = failed_chunk({'error': 'Gemini returned HTTP 500.', 'chunks': [real, truncated]}, segments, {'s4': {}})
    assert entry is real and [s['id'] for s in bad] == ['s3', 's5']


def test_a_rerecord_skips_text_gemini_blocks_and_says_so(client, monkeypatch):
    from bardic.audio import ContentBlocked
    book, (one, two, _), performance, _ = ready_performance(client, monkeypatch)
    target = passages(book, two)
    blocked = target[2]['text']
    real = voices(monkeypatch)
    inner = __import__('bardic.performances', fromlist=['synthesize']).synthesize

    def refusing(segment, character, scene, provider, model, key, path, **kwargs):
        if voice_id(character, provider) == 'Fred' and segment['text'] == blocked:
            raise ContentBlocked()
        return inner(segment, character, scene, provider, model, key, path, **kwargs)

    monkeypatch.setattr('bardic.performances.synthesize', refusing)
    started = rerecord(client, book, performance, chapter_id=two['id'])
    job = wait_job(client, started.json()['job']['id'])
    assert job['status'] == 'completed' and 'Gemini blocks the text' in job['message'], job
    ready = audio(client, book, performance)
    assert ready[target[3]['id']]['voice'] == 'Fred', 'later passages were still re-recorded'
    assert ready[target[2]['id']]['voice'] == MAIN
    assert real is not None
