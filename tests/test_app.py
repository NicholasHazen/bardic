"""Cross-component checks: source integrity, jobs, cache, invalidation and export."""
import io
import json
import time
import threading
import wave
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bardic.app import create_app
from bardic.audio import render_fingerprint, voice_id
from bardic.store import InstanceLock, Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with TestClient(create_app(tmp_path)) as c:
        yield c


def import_text(client, text='Chapter One\n\nThe lamps were lit.\n\n“Come in,” Mara said.\n'):
    response = client.post('/api/books', files={'file': ('lamp.txt', text.encode(), 'text/plain')})
    assert response.status_code == 200, response.text
    return response.json()


def wait_job(client, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = next(j for j in client.get('/api/jobs').json() if j['id'] == job_id)
        if job['status'] not in {'queued', 'running'}:
            return job
        time.sleep(.01)
    pytest.fail('worker did not finish')


def fake_audio(monkeypatch):
    calls = []
    def synthesize(segment, character, scene, provider, model, api_key, output_path):
        calls.append(segment['id'])
        with wave.open(str(output_path), 'wb') as wav:
            wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            wav.writeframes(b'\x10\0' * 2400)
        return dict(fingerprint=render_fingerprint(segment, character, scene, provider, model), duration=.1,
                    provider=provider, model=model, voice=voice_id(character, provider))
    monkeypatch.setattr('bardic.app.synthesize', synthesize)
    return calls


def test_import_preserves_unicode_text_and_presented_gaps(client):
    text = 'A moon 🌙 rose.\n\n“Wait,” she whispered.\n'
    book = import_text(client, text)
    chapter = book['chapters'][0]
    rebuilt = ''.join(s['leading_text'] + s['text'] for s in book['segments']) + chapter['trailing_text']
    assert rebuilt == chapter['text'] == text
    assert client.get('/api/books').json()[0]['title'] == book['title']
    original = client.app.state.runtime.store.root / 'originals' / book['id'] / 'source.txt'
    assert original.read_bytes() == text.encode()


def test_key_is_never_returned_or_saved(client):
    response = client.post('/api/settings', json={'api_key': 'secret-example-key'})
    assert response.status_code == 200
    assert response.json()['has_api_key']
    assert 'secret-example-key' not in response.text
    assert 'secret-example-key' not in json.dumps(client.app.state.runtime.store.settings())
    assert client.post('/api/settings', json={'api_key': ''}).json()['has_api_key'] is False


def test_cloud_requires_key_and_unknown_fields_rejected(client):
    book = import_text(client)
    assert client.post(f"/api/books/{book['id']}/render", json={'provider': 'gemini'}).status_code == 400
    segment = book['segments'][0]
    response = client.patch(f"/api/books/{book['id']}/segments/{segment['id']}", json={'text': 'rewritten'})
    assert response.status_code == 422
    response = client.patch(f"/api/books/{book['id']}/segments/{segment['id']}", json={'speaker_id': 'missing'})
    assert response.status_code == 400


def test_reject_foreign_origins_and_dns_rebinding(client):
    assert client.post('/api/demo', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/demo', headers={'Origin': 'http://testserver'}).status_code == 200
    assert client.get('/api/books', headers={'Host': 'evil.example'}).status_code == 400


def test_render_resume_edit_invalidation_and_export(client, monkeypatch):
    calls = fake_audio(monkeypatch)
    client.post('/api/settings', json={'api_key': 'test-only'})
    book = import_text(client)
    url = f"/api/books/{book['id']}"
    first = client.post(f'{url}/render', json={'provider': 'gemini'}).json()
    assert wait_job(client, first['id'])['status'] == 'completed'
    assert len(calls) == len(book['segments'])
    rendered = client.get(url).json()
    assert all(s['audio'] for s in rendered['segments'])
    audio_url = rendered['segments'][0]['audio']['url']
    assert client.get(audio_url).content.startswith(b'RIFF')
    second = client.post(f'{url}/render', json={'provider': 'gemini'}).json()
    assert wait_job(client, second['id'])['status'] == 'completed'
    assert len(calls) == len(book['segments']), 'completed takes should be reused'
    export = client.get(f'{url}/export')
    assert export.status_code == 200
    operations = client.get(f'{url}/resources').json()['operations']
    measured_export = next(row for row in operations if row['stage'] == 'audio_export')
    assert measured_export['status'] == 'completed'
    assert measured_export['output_bytes'] == len(export.content)
    assert measured_export['elapsed_seconds'] >= 0
    assert measured_export['request_count'] == 0
    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        manifest = json.loads(archive.read('timeline.json'))
        assert manifest['complete'] is True
        assert manifest['timing_kind'] == 'segment'
        timeline = manifest['chapters'][0]['segments']
        assert timeline[-1]['end'] == pytest.approx(.1 * len(book['segments']))
    first_segment = rendered['segments'][0]
    edited = client.patch(f"{url}/segments/{first_segment['id']}", json={'direction': 'Quiet wonder'}).json()
    assert edited['segments'][0]['text'] == first_segment['text']
    assert edited['segments'][0]['audio'] is None
    assert all(s['audio'] for s in edited['segments'][1:])
    assert client.get(audio_url).status_code == 404
    assert edited['segments'][0]['edited'] is True


def test_worker_failure_retains_completed_takes_and_retry(client, monkeypatch):
    calls = fake_audio(monkeypatch)
    from bardic import app as module
    original = module.synthesize
    def fail_second(*args):
        if len(calls) == 1:
            raise ValueError('Simulated provider timeout')
        return original(*args)
    monkeypatch.setattr(module, 'synthesize', fail_second)
    client.post('/api/settings', json={'api_key': 'test-only'})
    book = import_text(client)
    url = f"/api/books/{book['id']}"
    job = client.post(f'{url}/render', json={'provider': 'gemini'}).json()
    assert wait_job(client, job['id'])['status'] == 'failed'
    assert sum(bool(s['audio']) for s in client.get(url).json()['segments']) == 1
    monkeypatch.setattr(module, 'synthesize', original)
    job = client.post(f'{url}/render', json={'provider': 'gemini'}).json()
    assert wait_job(client, job['id'])['status'] == 'completed'
    assert len(calls) == len(book['segments'])


def test_restart_marks_incomplete_jobs_and_keeps_checkpoints(tmp_path):
    store = Store(tmp_path)
    book = {'id': 'book', 'segments': [{'id': 'one', 'audio': None}]}
    store.save_book(book)
    store.save_take('book', 'one', {'fingerprint': 'abc', 'duration': 1})
    job = store.create_job('book', 'render', 2)
    store.update_job(job['id'], status='running', progress=1)
    restored = Store(tmp_path)
    assert restored.job(job['id'])['status'] == 'interrupted'
    assert restored.book('book')['segments'][0]['audio']['duration'] == 1


def test_second_instance_cannot_recover_live_jobs(tmp_path):
    first = InstanceLock(tmp_path)
    try:
        with pytest.raises(RuntimeError, match='already using'):
            InstanceLock(tmp_path)
    finally:
        first.close()
    second = InstanceLock(tmp_path)
    second.close()


def test_restart_recovers_more_than_recent_job_page(tmp_path):
    store = Store(tmp_path)
    first = store.create_job('old-book', 'render')
    for _ in range(101):
        store.create_job('another-book', 'render')
    restored = Store(tmp_path)
    assert restored.job(first['id'])['status'] == 'interrupted'


def test_cancel_checkpoints_current_take_and_blocks_concurrent_edits(client, monkeypatch):
    calls = fake_audio(monkeypatch)
    from bardic import app as module
    original = module.synthesize
    started, release = threading.Event(), threading.Event()
    def delayed(*args):
        started.set()
        assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(module, 'synthesize', delayed)
    client.post('/api/settings', json={'api_key': 'test-only'})
    book = import_text(client)
    url = f"/api/books/{book['id']}"
    job = client.post(f'{url}/render', json={'provider': 'gemini'}).json()
    assert started.wait(2)
    try:
        segment = book['segments'][0]
        assert client.patch(f"{url}/segments/{segment['id']}", json={'direction': 'new'}).status_code == 409
        assert client.post(f'{url}/render', json={'provider': 'gemini'}).status_code == 409
        response = client.post(f"/api/jobs/{job['id']}/cancel")
        assert response.json()['cancel_requested'] is True
    finally:
        release.set()
    assert wait_job(client, job['id'])['status'] == 'cancelled'
    assert len(calls) == 1
    assert sum(bool(s['audio']) for s in client.get(url).json()['segments']) == 1
