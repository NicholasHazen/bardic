"""File-backed audio honors HTTP Range requests, as the contract documents (206 and 416)."""
from test_app import import_text, wait_job
from test_listen_api import begin, client, renderer  # noqa: F401  (fixtures)


def test_listening_audio_serves_byte_ranges(client, renderer):  # noqa: F811
    client.app.state.runtime.api_key = 'offline-key'
    book = import_text(client)
    job = wait_job(client, begin(client, book)['job']['id'])
    assert job['status'] == 'completed'
    url = job['audio']['url']
    whole = client.get(url)
    assert whole.status_code == 200 and whole.headers['accept-ranges'] == 'bytes'

    part = client.get(url, headers={'Range': 'bytes=0-11'})
    assert part.status_code == 206
    assert part.content == whole.content[:12]
    assert part.headers['content-range'] == f'bytes 0-11/{len(whole.content)}'

    refused = client.get(url, headers={'Range': f'bytes={len(whole.content) + 10}-'})
    assert refused.status_code == 416
