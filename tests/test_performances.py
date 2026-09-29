"""Saved performances: selected chapters, pinned narrator or cast snapshot, resumable jobs.

Every test is offline. Synthesizers are fakes writing small WAVs; prose is
original synthetic text; no provider request can leave the process.
"""
from __future__ import annotations

import threading
import time
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient

from bardic import tts_limits
from bardic.app import create_app
from bardic.audio import DEFAULT_TTS_MODEL, render_fingerprint, voice_id
from test_audio import wav_bytes
from test_chapter_listening import fake_chunk_synthesizer

TEXT = ('Chapter One\n\nThe lamps were lit along the quay. “Come in,” Mara said.\n\n'
        'The door closed behind the ferry cook.\n\n'
        'Chapter Two\n\nRain fell on the pier. “Wait,” said Elio.\n\nThe gulls settled on the rail.\n\n'
        'Chapter Three\n\nMorning came slowly over the harbor.\n')


def long_text(chapters=3, paragraphs=10):
    lines = []
    for number in range(chapters):
        lines += [f'Chapter {"One Two Three Four".split()[number]}', '']
        for index in range(paragraphs):
            lines += [f'The keeper counted lantern {index} of the {number} wall. “Is the tide turning?” asked the cook. '
                      'The keeper nodded, slowly, and walked on toward the pier.', '']
    return '\n'.join(lines)


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr('bardic.app.list_system_voices', lambda: [])
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda *_a, **_k: pytest.fail('No live provider calls allowed'))
    monkeypatch.setattr('bardic.performances.shutil.which', lambda name: '/fake/' + name if name in {'say', 'ffmpeg'} else None)
    tts_limits.LIMITER.reset()
    with TestClient(create_app(tmp_path)) as client:
        response = client.post('/api/settings', json={'tts_limits': {DEFAULT_TTS_MODEL: {'rpm': 1000, 'tpm': 10_000_000, 'rpd': 1000}}})
        assert response.status_code == 200, response.text
        yield client
    tts_limits.LIMITER.reset()


def device(monkeypatch, gate=None):
    calls = []

    def synthesize(segment, character, scene, provider, model, key, path, **_kwargs):
        calls.append({'segment': deepcopy(segment), 'character': deepcopy(character), 'provider': provider})
        if gate is not None:
            assert gate.wait(5), 'test synthesizer was not released'
        path.write_bytes(wav_bytes(frames=2400 + 16 * len(calls)))
        return {'fingerprint': render_fingerprint(segment, character, scene, provider, model), 'duration': .1,
                'provider': provider, 'model': model, 'voice': voice_id(character, provider)}
    monkeypatch.setattr('bardic.performances.synthesize', synthesize)
    monkeypatch.setattr('bardic.app.synthesize', synthesize)
    return calls


def import_book(client, text=TEXT):
    response = client.post('/api/books', files={'file': ('quay.txt', text.encode(), 'text/plain')})
    assert response.status_code == 200, response.text
    return response.json()


def wait_job(client, job_id, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.app.state.runtime.store.job(job_id)
        if job['status'] not in {'queued', 'running'}:
            return job
        time.sleep(.02)
    pytest.fail('job did not finish')


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    pytest.fail('condition not reached')


def request(book, chapters, **fields):
    return {'mode': 'simple', 'provider': 'system', 'voice': 'Samantha',
            'chapter_ids': [chapter['id'] for chapter in chapters], **fields}


def create(client, book, chapters, **fields):
    response = client.post(f"/api/books/{book['id']}/performances", json=request(book, chapters, **fields))
    assert response.status_code == 200, response.text
    return response.json()


def audio(client, book, performance):
    response = client.get(f"/api/books/{book['id']}/performances/{performance['id']}/audio")
    assert response.status_code == 200, response.text
    return response.json()['audio']


def passages(book, *chapters):
    wanted = {chapter['id'] for chapter in chapters}
    return [segment for segment in book['segments'] if segment['chapter_id'] in wanted]


def edit_passage(store, book_id, segment_id, old, new):
    """A same-length source change keeps every other passage's coordinates."""
    assert len(old) == len(new)
    book = store.book(book_id)
    segment = next(s for s in book['segments'] if s['id'] == segment_id)
    chapter = next(c for c in book['chapters'] if c['id'] == segment['chapter_id'])
    text = segment['text'].replace(old, new, 1)
    assert text != segment['text']
    chapter['text'] = chapter['text'][:segment['start']] + text + chapter['text'][segment['end']:]
    segment['text'] = text
    store.save_book(book)


def studio_state(store):
    with store.connect() as conn:
        return (conn.execute('SELECT * FROM takes ORDER BY rowid').fetchall(),
                conn.execute("SELECT COUNT(*) FROM artifact_versions WHERE kind='audio_take'").fetchone()[0])


def test_device_simple_performance_prepares_only_selected_chapters_and_replays_without_work(client, monkeypatch):
    calls = device(monkeypatch)
    book = import_book(client)
    one, two, three = book['chapters']
    result = create(client, book, [three, one], name='Evening chapters')
    performance, job = result['performance'], result['job']
    assert performance['chapter_ids'] == [one['id'], three['id']], 'chapters are kept in book order'
    assert performance['name'] == 'Evening chapters' and performance['mode'] == 'simple' and performance['session_id']
    assert job['kind'] == 'performance' and performance['job']['id'] == job['id']
    finished = wait_job(client, job['id'])
    assert finished['status'] == 'completed' and finished['message'] == 'Performance ready', finished
    selected = passages(book, one, three)
    assert finished['total'] == finished['progress'] == len(selected) == len(calls)
    assert {call['segment']['id'] for call in calls} == {segment['id'] for segment in selected}
    ready = audio(client, book, performance)
    assert set(ready) == {segment['id'] for segment in selected}
    assert all(item['session_id'] == performance['session_id'] for item in ready.values())
    assert client.get(next(iter(ready.values()))['url']).status_code == 200
    again = client.post(f"/api/books/{book['id']}/performances/{performance['id']}/prepare").json()
    assert again['job'] is None and len(calls) == len(selected)
    progress = again['performance']['progress']
    assert progress['passages_ready'] == progress['passages_total'] == len(selected)
    assert [row['id'] for row in progress['chapters']] == [one['id'], three['id']]
    assert again['performance']['narrator_label'] == 'Samantha · Device voices'
    # Replay needs no speech tools and no provider.
    monkeypatch.setattr('bardic.performances.shutil.which', lambda _name: None)
    assert set(audio(client, book, performance)) == set(ready)
    stages = [row for row in client.get(f"/api/books/{book['id']}/resources").json()['operations']
              if row['run_id'] == job['id']]
    assert len(stages) == len(selected) and all(row['stage'] == 'simple_listen' for row in stages)


def test_live_listening_takes_with_the_same_narrator_are_reused(client, monkeypatch):
    calls = device(monkeypatch)
    book = import_book(client)
    one = book['chapters'][0]
    first, second = passages(book, one)[:2]
    for segment in (first, second):
        response = client.post(f"/api/books/{book['id']}/listen", json={'provider': 'system', 'voice': 'Samantha',
                                                                       'segment_id': segment['id']})
        assert response.status_code == 200, response.text
        wait_job(client, response.json()['job']['id'])
    assert len(calls) == 2
    preview = client.post(f"/api/books/{book['id']}/performances/preview", json=request(book, [one])).json()
    assert preview['passages_ready'] == 2 and preview['passages_to_generate'] == len(passages(book, one)) - 2
    assert any('earlier listening' in note for note in preview['notes'])
    result = create(client, book, [one])
    wait_job(client, result['job']['id'])
    generated = [call['segment']['id'] for call in calls[2:]]
    assert first['id'] not in generated and second['id'] not in generated
    assert len(generated) == len(passages(book, one)) - 2
    assert set(audio(client, book, result['performance'])) == {s['id'] for s in passages(book, one)}


def test_cancel_keeps_finished_audio_and_a_cancelled_job_never_continues(client, monkeypatch):
    gate = threading.Event()
    calls = device(monkeypatch, gate)
    book = import_book(client)
    result = create(client, book, book['chapters'])
    wait_for(lambda: len(calls) == 1)
    cancelled = client.post(f"/api/jobs/{result['job']['id']}/cancel")
    assert cancelled.status_code == 200
    gate.set()
    job = wait_job(client, result['job']['id'])
    assert job['status'] == 'cancelled' and job['progress'] == 1
    time.sleep(.2)
    assert len(calls) == 1 and client.app.state.runtime.store.job(job['id'])['status'] == 'cancelled'
    ready = audio(client, book, result['performance'])
    assert list(ready) == [calls[0]['segment']['id']]
    resumed = client.post(f"/api/books/{book['id']}/performances/{result['performance']['id']}/prepare").json()
    assert resumed['job']['total'] == len(book['segments']) - 1
    assert wait_job(client, resumed['job']['id'])['status'] == 'completed'
    assert len(calls) == len(book['segments'])


@pytest.fixture
def gemini(client):
    client.app.state.runtime.api_key = 'offline-key'
    return client


def test_gemini_simple_runs_queued_chapter_jobs_and_stops_at_the_daily_quota(gemini, monkeypatch):
    client = gemini
    calls = []
    monkeypatch.setattr('bardic.chapter_listening.synthesize', fake_chunk_synthesizer(calls))
    client.post('/api/settings', json={'tts_limits': {DEFAULT_TTS_MODEL: {'rpm': 1000, 'tpm': 10_000_000, 'rpd': 1}}})
    book = import_book(client, long_text(3, 4))
    result = create(client, book, book['chapters'][:2], provider='gemini', voice='Kore', model=DEFAULT_TTS_MODEL)
    job = wait_job(client, result['job']['id'])
    assert job['status'] == 'quota_limited' and job['resume_after'], job
    store = client.app.state.runtime.store
    children = [j for j in store.jobs(book['id'], limit=None) if j.get('parent_id') == job['id']]
    assert len(children) == 2 and job['child_job_ids'] == [c['id'] for c in reversed(children)]
    first, second = reversed(children)
    assert first['kind'] == 'listen_chapter' and first['intent'] == 'queue' and first['chunking']['ramp_seconds'] == []
    assert first['status'] == 'completed' and second['status'] == 'quota_limited'
    assert first['chapter_id'] == book['chapters'][0]['id'] and second['chapter_id'] == book['chapters'][1]['id']
    assert len(calls) == 1
    ready = audio(client, book, result['performance'])
    assert set(ready) == {s['id'] for s in passages(book, book['chapters'][0])}, 'finished chapter work is kept'
    summary = client.get(f"/api/books/{book['id']}/performances/{result['performance']['id']}").json()['performance']
    assert summary['job']['status'] == 'quota_limited' and summary['job']['resume_after']


def test_cancelling_a_gemini_performance_cancels_its_active_chapter_job(gemini, monkeypatch):
    client = gemini
    gate = threading.Event()
    calls = []
    monkeypatch.setattr('bardic.chapter_listening.synthesize', fake_chunk_synthesizer(calls, gate=gate))
    book = import_book(client, long_text(3, 4))
    result = create(client, book, book['chapters'], provider='gemini', voice='Kore', model=DEFAULT_TTS_MODEL)
    wait_for(lambda: len(calls) == 1)
    store = client.app.state.runtime.store
    child = next(j for j in store.jobs(book['id'], limit=None) if j.get('parent_id') == result['job']['id'])
    client.post(f"/api/jobs/{result['job']['id']}/cancel")
    assert store.job(child['id'])['cancel_requested'] is True
    gate.set()
    job = wait_job(client, result['job']['id'])
    assert job['status'] == 'cancelled' and wait_job(client, child['id'])['status'] == 'cancelled'
    time.sleep(.2)
    assert len(calls) == 1
    assert len([j for j in store.jobs(book['id'], limit=None) if j.get('parent_id') == job['id']]) == 1
    assert set(audio(client, book, result['performance'])) == {s['id'] for s in passages(book, book['chapters'][0])}


def test_cast_performance_uses_its_snapshot_and_never_touches_studio_takes(client, monkeypatch):
    calls = device(monkeypatch)
    book = import_book(client)
    one, two, _ = book['chapters']
    added = client.post(f"/api/books/{book['id']}/characters", json={'name': 'Mara', 'voices': {'system': {'id': 'Daniel'}}}).json()
    mara = next(c for c in added['characters'] if c['name'] == 'Mara')
    added = client.post(f"/api/books/{book['id']}/characters", json={'name': 'Elio'}).json()
    elio = next(c for c in added['characters'] if c['name'] == 'Elio')
    come_in = next(s for s in book['segments'] if s['text'] == '“Come in,”')
    wait = next(s for s in book['segments'] if s['text'] == '“Wait,”')
    gulls = next(s for s in book['segments'] if s['text'].startswith('The gulls'))
    for segment, speaker in ((come_in, mara), (gulls, elio)):
        assert client.patch(f"/api/books/{book['id']}/segments/{segment['id']}", json={'speaker_id': speaker['id']}).status_code == 200
    # One Studio take whose recipe the performance can reuse, read-only.
    door = next(s for s in book['segments'] if s['text'].startswith('The door'))
    studio = client.post(f"/api/books/{book['id']}/render", json={'provider': 'system', 'segment_id': door['id']}).json()
    assert wait_job(client, studio['id'])['status'] == 'completed' and len(calls) == 1
    before = studio_state(client.app.state.runtime.store)

    body = {'mode': 'cast', 'provider': 'system', 'chapter_ids': [one['id'], two['id']]}
    preview = client.post(f"/api/books/{book['id']}/performances/preview", json=body).json()
    assert preview['problems'] == [] and preview['narrator_label'] == 'Full cast · Device voices'
    assert any('Elio has no device voice and will use the narrator' in note for note in preview['notes'])
    assert any('no identified speaker' in note for note in preview['notes'])
    result = client.post(f"/api/books/{book['id']}/performances", json=body).json()
    assert wait_job(client, result['job']['id'])['status'] == 'completed'
    selected = passages(client.app.state.runtime.store.book(book['id']), one, two)
    assert len(calls) == 1 + len(selected) - 1, 'the matching Studio take was reused'
    ready = audio(client, book, result['performance'])
    assert set(ready) == {s['id'] for s in selected}
    assert ready[come_in['id']]['voice'] == 'Daniel' and ready[come_in['id']]['fallback'] is False
    assert ready[come_in['id']]['character_id'] == mara['id']
    for segment in (wait, gulls):
        assert ready[segment['id']]['fallback'] is True and ready[segment['id']]['character_id'] == 'narrator'
    assert ready[door['id']]['asset_id'] == client.app.state.runtime.store.book(book['id'])['segments'][
        [s['id'] for s in book['segments']].index(door['id'])]['audio']['asset_id']
    assert client.get(ready[come_in['id']]['url']).status_code == 200
    assert studio_state(client.app.state.runtime.store) == before, 'Studio takes are untouched'
    cast = {row['character_id']: row for row in result['performance']['cast']}
    assert cast[mara['id']]['fallback'] is False and cast[elio['id']]['fallback'] is True

    # A later voice edit does not change the saved performance.
    edited = client.patch(f"/api/books/{book['id']}/characters/{mara['id']}", json={'voices': {'system': {'id': 'Moira'}}})
    assert edited.status_code == 200
    assert audio(client, book, result['performance']) == ready
    # A changed passage regenerates with the snapshot voice, not the new one.
    store = client.app.state.runtime.store
    edit_passage(store, book['id'], come_in['id'], 'in', 'on')
    assert come_in['id'] not in audio(client, book, result['performance'])
    count = len(calls)
    resumed = client.post(f"/api/books/{book['id']}/performances/{result['performance']['id']}/prepare").json()
    assert resumed['job']['total'] == 1 and wait_job(client, resumed['job']['id'])['status'] == 'completed'
    assert len(calls) == count + 1 and voice_id(calls[-1]['character'], 'system') == 'Daniel'
    assert audio(client, book, result['performance'])[come_in['id']]['voice'] == 'Daniel'
    # A second performance reuses matching performance takes; only Mara's new voice is new work.
    count = len(calls)
    other = client.post(f"/api/books/{book['id']}/performances", json={**body, 'chapter_ids': [one['id']]}).json()
    assert wait_job(client, other['job']['id'])['status'] == 'completed'
    assert [call['segment']['id'] for call in calls[count:]] == [come_in['id']]
    assert voice_id(calls[-1]['character'], 'system') == 'Moira'
    with store.connect() as conn, pytest.raises(Exception):
        conn.execute('DELETE FROM performance_takes')


def test_source_change_hides_only_that_passage_and_resume_regenerates_it(client, monkeypatch):
    calls = device(monkeypatch)
    book = import_book(client)
    one = book['chapters'][0]
    result = create(client, book, [one])
    wait_job(client, result['job']['id'])
    lamps = next(s for s in book['segments'] if s['text'].startswith('The lamps'))
    store = client.app.state.runtime.store
    edit_passage(store, book['id'], lamps['id'], 'lit', 'dim')
    ready = audio(client, book, result['performance'])
    assert lamps['id'] not in ready and len(ready) == len(passages(book, one)) - 1
    performance = client.get(f"/api/books/{book['id']}/performances/{result['performance']['id']}").json()['performance']
    assert performance['progress']['passages_ready'] == len(passages(book, one)) - 1
    count = len(calls)
    resumed = client.post(f"/api/books/{book['id']}/performances/{result['performance']['id']}/prepare").json()
    assert wait_job(client, resumed['job']['id'])['status'] == 'completed'
    assert [call['segment']['id'] for call in calls[count:]] == [lamps['id']]
    assert calls[-1]['segment']['text'].startswith('The lamps were dim')
    assert len(audio(client, book, result['performance'])) == len(passages(book, one))


def test_preview_is_local_and_reports_problems_notes_and_request_estimates(client, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail('preview must not synthesize')
    monkeypatch.setattr('bardic.performances.synthesize', forbidden)
    monkeypatch.setattr('bardic.chapter_listening.synthesize', forbidden)
    book = import_book(client, long_text(2, 10))
    url = f"/api/books/{book['id']}/performances/preview"
    local = client.post(url, json=request(book, book['chapters'])).json()
    assert local['problems'] == [] and local['quota'] is None
    assert local['requests_estimate'] == local['passages_to_generate'] == local['passages_total'] == len(book['segments'])
    assert local['expected_seconds'] > 0 and [c['id'] for c in local['chapters']] == [c['id'] for c in book['chapters']]
    cloud = request(book, book['chapters'], provider='gemini', voice='Kore', model=DEFAULT_TTS_MODEL)
    missing_key = client.post(url, json=cloud).json()
    assert any('Gemini API key' in problem['detail'] for problem in missing_key['problems'])
    refused = client.post(f"/api/books/{book['id']}/performances", json=cloud)
    assert refused.status_code == 400 and 'Gemini API key' in refused.json()['detail']
    client.app.state.runtime.api_key = 'offline-key'
    chunked = client.post(url, json=cloud).json()
    assert chunked['problems'] == [] and chunked['narrator_label'] == 'Kore · Gemini'
    assert 1 <= chunked['requests_estimate'] < chunked['passages_to_generate']
    assert chunked['quota']['rpd'] == 1000 and chunked['quota']['requests_today'] == 0 and chunked['quota']['resets_at']
    monkeypatch.setattr('bardic.performances.shutil.which', lambda _name: None)
    assert any('say and ffmpeg' in p['detail'] for p in client.post(url, json=request(book, book['chapters'])).json()['problems'])
    unknown = client.post(url, json={**request(book, book['chapters']), 'chapter_ids': ['chapter_missing']})
    assert unknown.status_code == 400
    assert client.post(url, json={**request(book, book['chapters']), 'chapter_ids': []}).status_code == 422
    breeze = client.post(url, json={**request(book, book['chapters']), 'provider': 'breeze', 'voice': None}).json()
    assert breeze['problems']
    assert client.app.state.runtime.store.jobs(book['id']) == []


def test_preview_problems_are_coded_so_clients_can_offer_a_fix(client, monkeypatch):
    book = import_book(client)
    url = f"/api/books/{book['id']}/performances/preview"
    cloud = request(book, book['chapters'], provider='gemini', voice='Kore', model=DEFAULT_TTS_MODEL)
    problems = client.post(url, json=cloud).json()['problems']
    assert problems == [{'code': 'gemini_key_missing', 'detail': 'No Gemini API key is configured.'}]
    breeze = client.post(url, json={**request(book, book['chapters']), 'provider': 'breeze', 'voice': None}).json()
    assert [problem['code'] for problem in breeze['problems']] == ['breeze_url_missing', 'narrator_voice_invalid']
    assert all(set(problem) == {'code', 'detail'} and problem['detail'] for problem in breeze['problems'])
    monkeypatch.setattr('bardic.performances.shutil.which', lambda _name: None)
    device_problems = client.post(url, json=request(book, book['chapters'])).json()['problems']
    assert [problem['code'] for problem in device_problems] == ['device_narration_unavailable']


def test_editing_a_performance_of_an_archived_book_is_refused(client, monkeypatch):
    device(monkeypatch)
    book = import_book(client)
    created = create(client, book, [book['chapters'][0]])
    wait_job(client, created['job']['id'])
    assert client.post(f"/api/books/{book['id']}/archive").status_code == 200
    url = f"/api/books/{book['id']}/performances/{created['performance']['id']}"
    for body in ({'name': 'Renamed'}, {'archived': True}):
        response = client.patch(url, json=body)
        assert response.status_code == 409 and response.json()['code'] == 'book_archived', response.text
    assert client.get(url).json()['performance']['name'] == created['performance']['name']


def test_a_dangling_listening_session_is_a_server_defect_not_a_missing_resource(client, monkeypatch):
    from bardic.performances import PerformanceRepository
    device(monkeypatch)
    book = import_book(client)
    created = create(client, book, [book['chapters'][0]])
    wait_job(client, created['job']['id'])
    performance_id = created['performance']['id']
    PerformanceRepository(client.app.state.runtime.store).update(book['id'], performance_id, session_id='f' * 64)
    base = f"/api/books/{book['id']}/performances"
    import conftest  # its contract wrapper rejects every 500; these expect one, so they are sent unchecked
    for method, url in (('GET', f'{base}/{performance_id}'), ('GET', f'{base}/{performance_id}/audio'),
                        ('GET', base), ('POST', f'{base}/{performance_id}/prepare')):
        response = conftest._send(client, client.build_request(method, url))
        assert response.status_code == 500 and response.json()['code'] == 'internal_error', (url, response.text)


def test_rename_and_archive_are_label_changes_that_keep_audio(client, monkeypatch):
    device(monkeypatch)
    book = import_book(client)
    first = create(client, book, [book['chapters'][0]])
    wait_job(client, first['job']['id'])
    second = create(client, book, [book['chapters'][1]])
    wait_job(client, second['job']['id'])
    base = f"/api/books/{book['id']}/performances"
    listed = client.get(base).json()['performances']
    assert [p['id'] for p in listed] == [second['performance']['id'], first['performance']['id']]
    renamed = client.patch(f"{base}/{first['performance']['id']}", json={'name': '  Night reading '}).json()['performance']
    assert renamed['name'] == 'Night reading'
    before = audio(client, book, first['performance'])
    archived = client.patch(f"{base}/{first['performance']['id']}", json={'archived': True}).json()['performance']
    assert archived['archived'] is True
    assert [p['id'] for p in client.get(base).json()['performances']] == [second['performance']['id']]
    assert len(client.get(base, params={'archived': True}).json()['performances']) == 2
    assert audio(client, book, first['performance']) == before
    runtime = client.app.state.runtime
    for item in before.values():
        assert (runtime.store.root / 'listen-audio' / book['id'] / f"{item['asset_id']}.wav").is_file()
    assert client.patch(f"{base}/{first['performance']['id']}", json={'name': ' '}).status_code == 400
    assert client.get(f"{base}/pf_missing").status_code == 404


def test_a_busy_book_refuses_new_performance_work(client, monkeypatch):
    gate = threading.Event()
    calls = device(monkeypatch, gate)
    book = import_book(client)
    first = create(client, book, [book['chapters'][0]])
    wait_for(lambda: calls)
    base = f"/api/books/{book['id']}/performances"
    assert client.post(base, json=request(book, [book['chapters'][1]])).status_code == 409
    assert client.post(f"{base}/{first['performance']['id']}/prepare").status_code == 409
    # Finished audio is readable while the job runs.
    assert client.get(f"{base}/{first['performance']['id']}/audio").status_code == 200
    gate.set()
    assert wait_job(client, first['job']['id'])['status'] == 'completed'
    assert len(client.get(base).json()['performances']) == 1


def test_require_idle_sees_a_running_parent_behind_more_than_100_child_jobs(client):
    store = client.app.state.runtime.store
    book = import_book(client)
    parent = store.create_job(book['id'], 'performance', 1)
    store.update_job(parent['id'], status='running')
    for _ in range(101):
        child = store.create_job(book['id'], 'listen_chapter', 1)
        store.update_job(child['id'], status='completed', parent_id=parent['id'])
    response = client.post(f"/api/books/{book['id']}/performances", json=request(book, book['chapters'][:1]))
    assert response.status_code == 409, 'one job per book holds however many children a run has made'


def test_resume_replaces_a_damaged_cast_file(client, monkeypatch):
    calls = device(monkeypatch)
    book = import_book(client)
    result = create(client, book, book['chapters'][:1], mode='cast', provider='system', voice=None)
    assert wait_job(client, result['job']['id'])['status'] == 'completed'
    before = audio(client, book, result['performance'])
    segment_id, damaged = next(iter(before.items()))
    client.app.state.runtime.audio_path(book['id'], damaged['asset_id']).write_bytes(b'RIFF-damaged')
    made = len(calls)
    resumed = client.post(f"/api/books/{book['id']}/performances/{result['performance']['id']}/prepare").json()
    assert resumed['job'] is not None, 'resume validates cast audio instead of trusting that a file exists'
    assert wait_job(client, resumed['job']['id'])['status'] == 'completed'
    assert len(calls) == made + 1, 'only the damaged passage is narrated again'
    assert audio(client, book, result['performance'])[segment_id]['asset_id'] != damaged['asset_id']


def test_live_listening_does_not_join_a_performance_chapter_job(gemini):
    client = gemini
    store = client.app.state.runtime.store
    book = import_book(client)
    first = book['segments'][0]
    parent = store.create_job(book['id'], 'performance', 1)
    child = store.create_job(book['id'], 'listen_chapter', 1)
    store.update_job(child['id'], status='running', parent_id=parent['id'], chapter_id=first['chapter_id'])
    response = client.post(f"/api/books/{book['id']}/listen/chapter",
                           json={'provider': 'gemini', 'voice': 'Kore', 'model': DEFAULT_TTS_MODEL,
                                 'segment_id': first['id'], 'intent': 'play'})
    assert response.status_code == 409
    assert 'saved performance' in response.json()['detail']
    for job in (child, parent):
        store.update_job(job['id'], status='cancelled')


# Extending a performance ----------------------------------------------------------

def test_adding_chapters_extends_the_same_performance_and_reuses_what_is_saved(client, monkeypatch):
    calls = device(monkeypatch)
    book = import_book(client)
    one, two, three = book['chapters']
    first = create(client, book, [one], name='Evenings')
    assert wait_job(client, first['job']['id'])['status'] == 'completed'
    performance_id = first['performance']['id']
    before = audio(client, book, first['performance'])
    base = f"/api/books/{book['id']}/performances/{performance_id}"

    plan = client.post(f'{base}/preview', json={'chapter_ids': [two['id']]})
    assert plan.status_code == 200, plan.text
    plan = plan.json()
    assert plan['added_chapter_ids'] == [two['id']] and plan['chapter_ids'] == [one['id'], two['id']]
    assert plan['passages_ready'] == len(before) and plan['passages_to_generate'] == len(passages(book, two))
    assert client.get(base).json()['performance']['chapter_ids'] == [one['id']], 'a preview stores nothing'
    assert client.post(f'{base}/preview', json={'chapter_ids': []}).json()['added_chapter_ids'] == []

    made = len(calls)
    added = client.post(f'{base}/chapters', json={'chapter_ids': [three['id'], two['id'], one['id']]})
    assert added.status_code == 200, added.text
    result = added.json()
    assert result['performance']['id'] == performance_id and result['performance']['name'] == 'Evenings'
    assert result['performance']['chapter_ids'] == [one['id'], two['id'], three['id']], 'book order, no duplicates'
    assert [entry['chapter_ids'] for entry in result['performance']['chapters_added']] == [[two['id'], three['id']]]
    assert wait_job(client, result['job']['id'])['status'] == 'completed'
    assert len(calls) - made == len(passages(book, two, three)), 'only the added chapters are narrated'
    after = audio(client, book, first['performance'])
    assert {key: after[key] for key in before} == before, 'retained audio is never rewritten'
    assert len(after) == len(book['segments'])
    assert len(client.get(f"/api/books/{book['id']}/performances").json()['performances']) == 1

    # Nothing new and nothing missing: no job, and no history entry.
    again = client.post(f'{base}/chapters', json={'chapter_ids': [two['id']]}).json()
    assert again['job'] is None
    assert len(again['performance']['chapters_added']) == 1, 'an unchanged selection adds no history'


def test_adding_chapters_is_validated_and_needs_an_idle_book(client, monkeypatch):
    gate = threading.Event()
    calls = device(monkeypatch, gate)
    book = import_book(client)
    one, two, _ = book['chapters']
    first = create(client, book, [one])
    wait_for(lambda: calls)
    base = f"/api/books/{book['id']}/performances/{first['performance']['id']}"
    busy = client.post(f'{base}/chapters', json={'chapter_ids': [two['id']]})
    assert busy.status_code == 409 and busy.json()['code'] == 'job_active'
    assert client.post(f'{base}/preview', json={'chapter_ids': [two['id']]}).status_code == 200, 'planning is a local read'
    gate.set()
    assert wait_job(client, first['job']['id'])['status'] == 'completed'
    missing = client.post(f'{base}/chapters', json={'chapter_ids': ['nope']})
    assert missing.status_code == 400 and missing.json()['code'] == 'unknown_chapter'
    empty = client.post(f'{base}/chapters', json={'chapter_ids': []})
    assert empty.status_code == 400 and empty.json()['code'] == 'no_chapters_selected'
    assert client.post(f'{base}/preview', json={'chapter_ids': ['nope']}).json()['code'] == 'unknown_chapter'
    assert client.post(f"/api/books/{book['id']}/performances/pf_none/chapters", json={'chapter_ids': [two['id']]}).status_code == 404
    assert client.get(base).json()['performance']['chapter_ids'] == [one['id']], 'a refused request changes nothing'


def test_cast_extension_keeps_the_pinned_cast_and_says_so(client, monkeypatch):
    calls = device(monkeypatch)
    book = import_book(client)
    one, two, _ = book['chapters']
    first = create(client, book, [one], mode='cast', provider='system', voice=None)
    assert wait_job(client, first['job']['id'])['status'] == 'completed'
    base = f"/api/books/{book['id']}/performances/{first['performance']['id']}"
    plan = client.post(f'{base}/preview', json={'chapter_ids': [two['id']]}).json()
    assert any('pinned when this performance was created' in note for note in plan['notes'])
    made = len(calls)
    result = client.post(f'{base}/chapters', json={'chapter_ids': [two['id']]}).json()
    assert wait_job(client, result['job']['id'])['status'] == 'completed'
    assert len(calls) - made == len(passages(book, two))
    assert set(audio(client, book, first['performance'])) == {s['id'] for s in passages(book, one, two)}


# Listening never changes what is requested ------------------------------------------

def request_plan(store, book_id, sent, known_jobs):
    """What was sent to the provider and how the jobs planned it: request sizes in order, chunk shapes,
    the chunking each chapter job snapshotted, and the jobs that exist."""
    jobs = [job for job in store.jobs(book_id, limit=None) if job['id'] not in known_jobs]
    children = sorted((job for job in jobs if job['kind'] == 'listen_chapter'), key=lambda job: job['created_at'])
    return {'requests': [len(text) for text in sent],
            'jobs': sorted(job['kind'] for job in jobs),
            'chunks': [[(entry['segment_count'], entry['chars']) for entry in child.get('chunks', [])] for child in children],
            'chunking': [child['chunking'] for child in children],
            'joins': [child.get('joins') for child in children]}


def listen_while_recording(client, book, performance, job_id):
    """A listener making the reads the player makes, as fast as it can, until the job settles.
    Returns the ready-passage counts it saw."""
    base = f"/api/books/{book['id']}/performances/{performance['id']}"
    store = client.app.state.runtime.store
    seen = []
    while store.job(job_id)['status'] in {'queued', 'running'}:
        record = client.get(base).json()['performance']
        ready = client.get(f'{base}/audio').json()['audio']
        seen.append(record['progress']['passages_ready'])
        for reference in list(ready.values())[:2]:
            assert client.get(reference['url'], headers={'Range': 'bytes=0-63'}).status_code in (200, 206)
        time.sleep(.005)
    return seen


@pytest.mark.parametrize('mode,provider', [('simple', 'gemini'), ('cast', 'system')])
def test_an_active_listener_never_changes_the_requests_a_performance_makes(gemini, monkeypatch, mode, provider):
    client = gemini
    sent = []
    chunked = fake_chunk_synthesizer([])

    def slow(segment, character, scene, prov, model, key, path, **kwargs):
        time.sleep(.03)  # long enough for the listener to overlap every request
        sent.append(segment['text'])
        if prov == 'gemini':
            return chunked(segment, character, scene, prov, model, key, path, **kwargs)
        path.write_bytes(wav_bytes(frames=2400 + 16 * len(sent)))
        return {'fingerprint': render_fingerprint(segment, character, scene, prov, model), 'duration': .1,
                'provider': prov, 'model': model, 'voice': voice_id(character, prov)}
    monkeypatch.setattr('bardic.chapter_listening.synthesize', slow)
    monkeypatch.setattr('bardic.performances.synthesize', slow)
    fields = ({'provider': 'gemini', 'voice': 'Kore', 'model': DEFAULT_TTS_MODEL} if mode == 'simple'
              else {'provider': 'system', 'voice': None})
    store = client.app.state.runtime.store
    plans, seen = [], []
    for listening in (False, True):
        # Same shape, same-length words, so the second run cannot reuse the first run's audio.
        book = import_book(client, long_text(3, 4).replace('lantern', 'candle!' if listening else 'lantern'))
        sent.clear()
        known = {job['id'] for job in store.jobs(book['id'], limit=None)}
        result = create(client, book, book['chapters'], mode=mode, **fields)
        if listening:
            seen = listen_while_recording(client, book, result['performance'], result['job']['id'])
        assert wait_job(client, result['job']['id'])['status'] == 'completed'
        assert len(audio(client, book, result['performance'])) == len(book['segments'])
        plans.append(request_plan(store, book['id'], list(sent), known))
    quiet, loud = plans
    assert quiet['requests'], 'the run made provider requests'
    assert loud == quiet, 'the same requests, sizes, chunks and jobs with a listener as without'
    assert min(seen) < max(seen), 'the listener overlapped the recording and saw it progress'
    assert not [job for job in store.jobs(book['id'], limit=None)
                if job['kind'] in {'listen', 'listen_chapter'} and not job.get('parent_id')], 'listening started no job of its own'
