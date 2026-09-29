"""Book pronunciations reach every narration path without changing book text or unrelated audio.

Offline only: fake synthesizers, original synthetic prose with invented names.
"""
from __future__ import annotations

import time
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient

from bardic import audio, pronunciation, tts_limits
from bardic.app import create_app
from bardic.audio import DEFAULT_TTS_MODEL, render_fingerprint, voice_id
from bardic.listening import ListeningRepository
from bardic.voice_previews import VoicePreviewRepository
from test_audio import wav_bytes

TEXT = ('Chapter One\n\nThe next morning, Cthaelor crossed the square. “Wait,” Eilidh said.\n\n'
        'The ferryman did not look up.\n\n'
        'Chapter Two\n\nRain fell on the pier while Cthaelor waited.\n')


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr('bardic.app.list_system_voices', lambda: [])
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda *_a, **_k: pytest.fail('No live provider calls allowed'))
    monkeypatch.setattr('bardic.performances.shutil.which', lambda name: '/fake/' + name if name in {'say', 'ffmpeg'} else None)
    monkeypatch.setattr('bardic.app.shutil.which', lambda name: '/fake/' + name if name in {'say', 'ffmpeg'} else None)
    tts_limits.LIMITER.reset()
    with TestClient(create_app(tmp_path)) as client:
        yield client
    tts_limits.LIMITER.reset()


def fake_synth(monkeypatch):
    calls = []

    def synthesize(segment, character, scene, provider, model, key, path, **_kwargs):
        recipe = audio._recipe(segment, character, scene, provider, model)
        calls.append({'segment': deepcopy(segment), 'sent': recipe['text'], 'recipe': recipe})
        path.write_bytes(wav_bytes(frames=2400 + 16 * len(calls)))
        return {'fingerprint': render_fingerprint(segment, character, scene, provider, model), 'duration': .1,
                'provider': provider, 'model': model, 'voice': voice_id(character, provider)}
    for target in ('bardic.app.synthesize', 'bardic.performances.synthesize'):
        monkeypatch.setattr(target, synthesize)
    return calls


def import_book(client):
    response = client.post('/api/books', files={'file': ('square.txt', TEXT.encode(), 'text/plain')})
    assert response.status_code == 200, response.text
    return response.json()


def wait_job(client, job_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.app.state.runtime.store.job(job_id)
        if job['status'] not in {'queued', 'running'}:
            return job
        time.sleep(.02)
    pytest.fail('job did not finish')


def render_all(client, book):
    job = client.post(f"/api/books/{book['id']}/render", json={'provider': 'system'}).json()
    assert wait_job(client, job['id'])['status'] == 'completed'
    return client.get(f"/api/books/{book['id']}").json()


def with_term(passages, term):
    return [s for s in passages if term in s['text']]


# Recipe ---------------------------------------------------------------------

def test_recipe_respells_only_matching_passages_and_keeps_other_identities():
    character, scene = {'id': 'narrator', 'voice': 'Kore'}, {}
    lexicon = [pronunciation.normalize_entry({'term': 'Cthaelor', 'respelling': 'Kaylor'})]
    plain = {'id': 's1', 'text': 'The ferryman did not look up.'}
    named = {'id': 's2', 'text': 'Cthaelor crossed the square.'}
    for segment in (plain, named):
        assert audio._recipe(segment, character, scene, 'gemini', None)['text'] == segment['text']
    assert (render_fingerprint(pronunciation.with_lexicon(plain, lexicon), character, scene, 'gemini', None) ==
            render_fingerprint(plain, character, scene, 'gemini', None)), 'unaffected audio keeps its identity'
    recipe = audio._recipe(pronunciation.with_lexicon(named, lexicon), character, scene, 'gemini', None)
    assert recipe['text'] == 'Kaylor crossed the square.'
    assert recipe['pronunciation'] == {'version': 1, 'applied': [['Cthaelor', 'Kaylor']]}
    payload = audio._gemini_payload(recipe)
    assert payload['input'][0]['content'][0]['text'] == 'Kaylor crossed the square.'
    assert 'Cthaelor' not in str(payload)
    assert named['text'] == 'Cthaelor crossed the square.' and 'pronunciations' not in named


# Studio and the lexicon API ----------------------------------------------------

def test_lexicon_edits_retire_only_affected_studio_takes_and_reuse_archived_audio(client, monkeypatch):
    calls = fake_synth(monkeypatch)
    book = import_book(client)
    url = f"/api/books/{book['id']}"
    rendered = render_all(client, book)
    first_pass = len(calls)
    assert all(s['audio'] for s in rendered['passages'])
    named = {s['id'] for s in with_term(rendered['passages'], 'Cthaelor')}
    assert len(named) == 2

    response = client.post(f'{url}/pronunciations', json={'term': 'Cthaelor', 'respelling': 'Kaylor'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['retired_takes'] == 2
    entry = result['pronunciations'][0]
    assert entry['usage']['occurrences'] == 2 and entry['usage']['passages'] == 2
    assert entry['usage']['rendered_passages'] == 0
    assert 'Cthaelor' in entry['usage']['examples'][0]['context']
    after = result['book']
    for passage in after['passages']:
        assert bool(passage['audio']) is (passage['id'] not in named)
    assert [c['text'] for c in after['chapters']] == [c['text'] for c in book['chapters']], 'book text never changes'
    assert all('pronunciations' not in s for s in client.app.state.runtime.store.book(book['id'])['segments'])

    render_all(client, book)
    fresh = calls[first_pass:]
    assert {call['segment']['id'] for call in fresh} == named
    assert all('Kaylor' in call['sent'] and 'Cthaelor' not in call['sent'] for call in fresh)
    assert client.get(f'{url}/pronunciations').json()['pronunciations'][0]['usage']['rendered_passages'] == 2

    # Removing the entry restores the original recipes; archived WAVs are reused without a request.
    removed = client.delete(f"{url}/pronunciations/{entry['id']}").json()
    assert removed['retired_takes'] == 2 and removed['pronunciations'] == []
    count = len(calls)
    render_all(client, book)
    assert len(calls) == count, 'archived takes matched the restored recipe'


def test_lexicon_validation_and_errors(client):
    book = import_book(client)
    url = f"/api/books/{book['id']}/pronunciations"
    assert client.post(url, json={'term': 'Eilidh', 'respelling': '(laugh) Aylee'}).status_code == 400
    assert client.post(url, json={'term': 'Eilidh', 'respelling': 'Aylee', 'character_id': 'nobody'}).status_code == 400
    assert client.post(url, json={'term': 'Eilidh', 'respelling': 'Aylee', 'ipa': 'eɪli'}).status_code == 422
    created = client.post(url, json={'term': 'Eilidh', 'respelling': 'Aylee', 'providers': {'breeze': 'Eilidh'}})
    assert created.status_code == 200
    entry = created.json()['pronunciations'][0]
    assert entry['providers'] == {'breeze': 'Eilidh'}
    assert client.post(url, json={'term': 'Eilidh', 'respelling': 'Ay-lee'}).status_code == 400
    edited = client.patch(f"{url}/{entry['id']}", json={'term': 'Eilidh', 'respelling': 'Ay-lee'})
    assert edited.status_code == 200 and edited.json()['pronunciations'][0]['respelling'] == 'Ay-lee'
    assert edited.json()['pronunciations'][0]['id'] == entry['id']
    assert client.patch(f'{url}/pr_000000000000', json={'term': 'X', 'respelling': 'Ex'}).status_code == 404
    assert client.delete(f'{url}/pr_000000000000').status_code == 404
    assert client.get(url).json()['pronunciations'][0]['usage']['passages'] == 1


# Enhanced performances ---------------------------------------------------------

def test_cast_performance_keeps_the_pronunciations_it_started_with(client, monkeypatch):
    calls = fake_synth(monkeypatch)
    book = import_book(client)
    url = f"/api/books/{book['id']}"
    entry = client.post(f'{url}/pronunciations', json={'term': 'Cthaelor', 'respelling': 'Kaylor'}).json()['pronunciations'][0]
    body = {'mode': 'cast', 'provider': 'system', 'chapter_ids': [c['id'] for c in book['chapters']]}
    preview = client.post(f'{url}/performances/preview', json=body).json()
    assert any('1 saved pronunciation applies' in note for note in preview['notes'])
    result = client.post(f'{url}/performances', json=body).json()
    assert result['performance']['pronunciation_count'] == 1
    assert 'pronunciation_snapshot' not in result['performance']
    assert wait_job(client, result['job']['id'])['status'] == 'completed'
    sent = [call['sent'] for call in calls]
    assert any('Kaylor' in text for text in sent) and not any('Cthaelor' in text for text in sent)

    # A later edit does not reach the saved performance, and its plan says so.
    client.patch(f"{url}/pronunciations/{entry['id']}", json={'term': 'Cthaelor', 'respelling': 'Kay-lor'})
    store = client.app.state.runtime.store
    book_now = store.book(book['id'])
    named = with_term(book_now['segments'], 'Cthaelor')[0]
    chapter = next(c for c in book_now['chapters'] if c['id'] == named['chapter_id'])
    changed = named['text'].replace('square', 'plaza')
    chapter['text'] = chapter['text'][:named['start']] + changed + chapter['text'][named['end']:]
    for segment in book_now['segments']:
        if segment['chapter_id'] == chapter['id'] and segment['start'] > named['start']:
            segment['start'] += len(changed) - len(named['text'])
            segment['end'] += len(changed) - len(named['text'])
    named['end'] += len(changed) - len(named['text'])
    named['text'] = changed
    store.save_book(book_now)
    from bardic import performances
    record = performances.PerformanceRepository(store).get(book['id'], result['performance']['id'])
    notes = performances.plan(client.app.state.runtime, book['id'], record, record=record)['public']['notes']
    assert any('keeps its original pronunciations' in note for note in notes)
    count = len(calls)
    resumed = client.post(f"{url}/performances/{result['performance']['id']}/prepare").json()
    assert wait_job(client, resumed['job']['id'])['status'] == 'completed'
    assert [call['sent'] for call in calls[count:]] == [changed.replace('Cthaelor', 'Kaylor')]


# Simple listening --------------------------------------------------------------

def test_simple_listening_respells_and_keeps_unaffected_cached_takes(client):
    store = client.app.state.runtime.store
    book = import_book(client)
    repo = ListeningRepository(store)
    session = repo.session(book['id'], 'system', 'Samantha')
    sent = []

    def render(segment, character, scene, provider, model, key, path, **_kwargs):
        sent.append(audio._recipe(segment, character, scene, provider, model)['text'])
        path.write_bytes(wav_bytes(frames=2400 + len(sent)))
        return {'fingerprint': render_fingerprint(segment, character, scene, provider, model), 'duration': .1,
                'provider': provider, 'model': model, 'voice': 'Samantha'}
    for segment in book['passages']:
        repo.render_passage(book['id'], session['id'], segment['id'], synthesizer=render)
    assert len(sent) == len(book['passages'])
    assert len(repo.takes(book['id'], session['id'])['takes']) == len(book['passages'])

    saved = store.book(book['id'])
    saved['pronunciations'] = [pronunciation.normalize_entry({'term': 'Cthaelor', 'respelling': 'Kaylor'})]
    store.save_book(saved)
    kept = {take['segment_id'] for take in repo.takes(book['id'], session['id'])['takes']}
    named = {s['id'] for s in with_term(book['passages'], 'Cthaelor')}
    assert kept == {s['id'] for s in book['passages']} - named
    for segment_id in named:
        assert repo.cached(book['id'], session['id'], segment_id) is None
        repo.render_passage(book['id'], session['id'], segment_id, synthesizer=render)
    assert all('Kaylor' in text for text in sent[-len(named):])
    assert len(repo.takes(book['id'], session['id'])['takes']) == len(book['passages'])


# Voice previews -----------------------------------------------------------------

def test_preview_auditions_an_unsaved_respelling_in_its_sentence(client):
    store = client.app.state.runtime.store
    book = import_book(client)
    repo = VoicePreviewRepository(store)
    plain = repo.prepare(book['id'], 'system', 'Samantha')
    draft = {'term': 'Eilidh', 'respelling': 'Aylee'}
    preview = repo.prepare(book['id'], 'system', 'Samantha', pronunciation_draft=draft)
    assert preview['source'] == 'passage'
    assert preview['text'] == '“Wait,” Eilidh said.'
    assert preview['spoken_text'] == '“Wait,” Aylee said.'
    assert preview['pronunciation'] == {'term': 'Eilidh', 'spoken': 'Aylee'}
    segment = next(s for s in book['passages'] if s['id'] == preview['segment_id'])
    chapter = next(c for c in book['chapters'] if c['id'] == segment['chapter_id'])
    anchor = preview['source_anchor']
    assert chapter['text'][anchor['start']:anchor['end']] == preview['text'], 'exact source coordinates'
    other = repo.prepare(book['id'], 'system', 'Samantha', pronunciation_draft={**draft, 'respelling': 'Ay-lee'})
    assert other['id'] != preview['id']
    assert repo.prepare(book['id'], 'system', 'Samantha')['id'] == plain['id'], 'a draft is never saved'
    # A word the book does not contain is auditioned in a neutral carrier sentence.
    demo = repo.prepare(book['id'], 'system', 'Samantha', pronunciation_draft={'term': 'Xhosari', 'respelling': 'Zosahree'})
    assert demo['source'] == 'demo' and demo['spoken_text'].startswith('The next morning, Zosahree crossed')


def test_preview_api_rejects_invalid_drafts(client):
    book = import_book(client)
    response = client.post(f"/api/books/{book['id']}/voice-preview",
                           json={'provider': 'system', 'pronunciation': {'term': 'Eilidh', 'respelling': '<sigh>'}})
    assert response.status_code == 400


def test_preview_identity_ignores_unrelated_entries_draft_ids_and_notes(client):
    store = client.app.state.runtime.store
    book = import_book(client)
    repo = VoicePreviewRepository(store)
    draft = {'term': 'Eilidh', 'respelling': 'Aylee'}
    first = repo.prepare(book['id'], 'system', 'Samantha', pronunciation_draft=draft)
    assert repo.prepare(book['id'], 'system', 'Samantha', pronunciation_draft=draft)['id'] == first['id'], \
        'hearing the same unsaved spelling twice reuses the first example'
    demo = repo.prepare(book['id'], 'system', 'Samantha')
    saved = store.book(book['id'])
    saved['pronunciations'] = [pronunciation.normalize_entry({'term': 'Zyrrhan', 'respelling': 'Zeer-an', 'note': 'secret'}),
                               pronunciation.normalize_entry({'term': 'Eilidh', 'respelling': 'Aylee', 'note': 'secret'})]
    store.save_book(saved)
    assert repo.prepare(book['id'], 'system', 'Samantha')['id'] == demo['id'], 'an unrelated entry changes nothing'
    assert repo.prepare(book['id'], 'system', 'Samantha', pronunciation_draft=draft)['id'] == first['id']
    with store.connect() as conn:
        bodies = '\n'.join(row[0] for row in conn.execute('SELECT body FROM voice_preview_requests'))
    assert 'secret' not in bodies and 'Zyrrhan' not in bodies and 'pr_' not in bodies


def test_patch_keeps_omitted_fields_and_stale_character_links_do_not_block_other_edits(client):
    book = import_book(client)
    url = f"/api/books/{book['id']}"
    added = client.post(f'{url}/characters', json={'name': 'Eilidh'}).json()
    eilidh = next(c for c in added['characters'] if c['name'] == 'Eilidh')
    entry = client.post(f'{url}/pronunciations', json={'term': 'Eilidh', 'respelling': 'Aylee', 'note': 'Scottish',
                                                        'character_id': eilidh['id'], 'providers': {'breeze': 'Eilidh'}}
                        ).json()['pronunciations'][0]
    kept = client.patch(f"{url}/pronunciations/{entry['id']}", json={'term': 'Eilidh', 'respelling': 'Ay-lee'}).json()
    saved = kept['pronunciations'][0]
    assert saved['note'] == 'Scottish' and saved['character_id'] == eilidh['id'] and saved['providers'] == {'breeze': 'Eilidh'}
    cleared = client.patch(f"{url}/pronunciations/{entry['id']}",
                           json={'term': 'Eilidh', 'respelling': 'Ay-lee', 'providers': {}, 'note': None}).json()
    assert cleared['pronunciations'][0]['providers'] is None and cleared['pronunciations'][0]['note'] is None
    # A character removed later (e.g. by re-analysis) keeps its link, which must not block other entries.
    store = client.app.state.runtime.store
    stored = store.book(book['id'])
    stored['characters'] = [c for c in stored['characters'] if c['id'] != eilidh['id']]
    store.save_book(stored)
    assert client.post(f'{url}/pronunciations', json={'term': 'Cthaelor', 'respelling': 'Kaylor'}).status_code == 200
    assert client.patch(f"{url}/pronunciations/{entry['id']}", json={'term': 'Eilidh', 'respelling': 'Aylee'}).status_code == 400


def test_performances_from_before_pronunciations_say_they_do_not_use_them(client, monkeypatch):
    fake_synth(monkeypatch)
    book = import_book(client)
    url = f"/api/books/{book['id']}"
    body = {'mode': 'cast', 'provider': 'system', 'chapter_ids': [c['id'] for c in book['chapters']]}
    result = client.post(f'{url}/performances', json=body).json()
    wait_job(client, result['job']['id'])
    from bardic import performances
    record = performances.PerformanceRepository(client.app.state.runtime.store).get(book['id'], result['performance']['id'])
    record.pop('pronunciation_snapshot')
    client.post(f'{url}/pronunciations', json={'term': 'Cthaelor', 'respelling': 'Kaylor'})
    notes = performances.plan(client.app.state.runtime, book['id'], record, record=record)['public']['notes']
    assert any('created before pronunciations existed' in note for note in notes)
    assert not any('changed after this performance started' in note for note in notes)
