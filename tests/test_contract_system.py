"""System, diagnostics and job responses that other tests do not reach, checked against the contract.

Offline: provider listings are faked and no job does real work.
"""
import asyncio
import hashlib
import json
import re
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from bardic.apispec import VERSION
from bardic.apispec.__main__ import CONTRACT
from bardic.apispec.spec import identity
from bardic.app import create_app
from bardic.model_catalog import ModelCatalog


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for name in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'BREEZE_TTS_URL', 'BREEZE_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr('bardic.app.list_system_voices', lambda: [])


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as test_client:
        yield test_client


def test_refreshed_model_catalog_and_status_match_the_contract(client, monkeypatch):
    listed = {'id': 'gpt-6-sol-mini', 'label': 'gpt-6-sol-mini', 'tier': 'other', 'roles': ['preprocess', 'analysis'],
              'structured_output': None, 'context_tokens': None, 'max_output_tokens': None,
              'input_usd_per_million': None, 'output_usd_per_million': None, 'price_date': None,
              'source_url': 'https://example.invalid/models', 'pricing_source_url': None, 'availability': 'listed',
              'price_valid_until': None, 'price_input_token_limit': None}
    monkeypatch.setattr(ModelCatalog, '_fetch', staticmethod(lambda provider, key: ([listed], True)))
    assert client.post('/api/settings', json={'api_keys': {'openai': 'offline-test-key'}}).status_code == 200
    refreshed = client.post('/api/models/openai/refresh')
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body['state'] == 'ready' and body['partial'] is True and body['cached'] is False
    assert body['models'][-1]['id'] == 'gpt-6-sol-mini'
    again = client.post('/api/models/openai/refresh').json()
    assert again['cached'] is True
    status = client.get('/api/status').json()
    assert status['model_catalogs']['openai']['state'] == 'ready'
    assert 'offline-test-key' not in str(status)


def test_cancelling_a_queued_job_is_immediate_and_idempotent(client):
    store = client.app.state.runtime.store
    queued = store.create_job('book-without-worker', 'render', 3)
    cancelled = client.post(f"/api/jobs/{queued['id']}/cancel", json={})
    assert cancelled.status_code == 200
    job = cancelled.json()
    assert job['status'] == 'cancelled' and job['cancel_requested'] is True
    again = client.post(f"/api/jobs/{queued['id']}/cancel")
    assert again.status_code == 200 and again.json() == job
    listed = client.get('/api/jobs', params={'book_id': 'book-without-worker'}).json()
    assert [item['id'] for item in listed] == [queued['id']]
    assert client.get('/api/jobs', params={'active': 'true'}).json() == []


def test_getting_one_job_returns_the_listed_job_and_records_nothing(client):
    store = client.app.state.runtime.store
    queued = store.create_job('book-without-worker', 'render', 3)
    before = json.dumps(store.job(queued['id']), sort_keys=True)
    got = client.get(f"/api/jobs/{queued['id']}")
    assert got.status_code == 200
    assert got.json() == next(job for job in client.get('/api/jobs', params={'book_id': 'book-without-worker'}).json()
                              if job['id'] == queued['id'])
    assert got.json()['status'] == 'queued' and got.json()['kind'] == 'render'
    # A GET never writes: the stored document, including `updated_at`, is untouched.
    assert json.dumps(store.job(queued['id']), sort_keys=True) == before
    # It follows the job to its terminal status, and stays readable there.
    client.post(f"/api/jobs/{queued['id']}/cancel")
    cancelled = client.get(f"/api/jobs/{queued['id']}").json()
    assert cancelled['status'] == 'cancelled' and cancelled['cancel_requested'] is True
    assert client.get(f"/api/jobs/{queued['id']}").json() == cancelled


def test_getting_one_job_presents_it_like_the_list_does(client):
    # A listen job saved before contract 0.2.0 kept recipe bookkeeping inside its audio; the list and the single
    # job both present it through the current audio view.
    store = client.app.state.runtime.store
    asset = 'a' * 64
    job = store.create_job('book-legacy', 'listen', 1)
    legacy = {'mode': 'simple', 'available': True, 'url': f'/api/books/book-legacy/listen/audio/{asset}',
              'asset_id': asset, 'duration': 1.5, 'provider': 'system', 'model': 'macos-say', 'voice': 'Fred',
              'session_id': 's' * 64, 'segment_id': 'p1', 'created_at': '2026-09-01T00:00:00+00:00',
              'cache_hit': True, 'fingerprint': 'f' * 64, 'recipe': 'r' * 64}
    # A stored narration job may still carry the `phase` label it once had; it is not presented.
    store.update_job(job['id'], status='completed', audio=legacy, session_id='s' * 64, segment_id='p1', provider='system', model='macos-say', phase='simple_listen')
    got = client.get(f"/api/jobs/{job['id']}").json()
    assert got == next(item for item in client.get('/api/jobs', params={'book_id': 'book-legacy'}).json() if item['id'] == job['id'])
    assert got['audio']['asset_id'] == asset
    for name in ('mode', 'available', 'cache_hit', 'fingerprint', 'recipe'):
        assert name not in got['audio']
    assert got['audio']['passage_id'] == 'p1' and 'segment_id' not in got['audio'], 'the wire says passage'
    assert 'phase' not in got


@pytest.mark.parametrize('job_id', ['0123456789abcdef0123456789abcdef', 'not-a-job'])
def test_getting_an_unknown_job_is_not_found(client, job_id):
    response = client.get(f'/api/jobs/{job_id}')
    assert response.status_code == 404 and response.json() == {'detail': 'Job not found', 'code': 'job_not_found'}
    assert client.get('/api/jobs', params={'active': 'true'}).json() == []


def test_unknown_job_cancel_is_not_found(client):
    response = client.post('/api/jobs/0123456789abcdef0123456789abcdef/cancel', json={})
    assert response.status_code == 404 and response.json() == {'detail': 'Job not found', 'code': 'job_not_found'}


def test_diagnostics_query_validation_is_the_standard_list(client):
    # The non-echoing diagnostics body applies to the POST only; a bad query parameter gets FastAPI's list.
    response = client.get('/api/diagnostics', params={'limit': 'many'})
    assert response.status_code == 422
    body = response.json()
    assert body['code'] == 'validation_error' and isinstance(body['detail'], list)
    assert body['detail'][0]['loc'] == ['query', 'limit']


def test_diagnostics_limit_is_clamped_and_a_bad_book_filter_has_a_code(client):
    book = '12345678-1234-1234-1234-123456789abc'
    for _ in range(3):
        client.post('/api/diagnostics', json={'event': 'buffer_failed', 'book_id': book, 'http_status': 500 + _})
    assert len(client.get('/api/diagnostics', params={'limit': 0}).json()['events']) == 1
    assert len(client.get('/api/diagnostics', params={'limit': -3}).json()['events']) == 1
    assert len(client.get('/api/diagnostics', params={'limit': 6000}).json()['events']) == 3
    response = client.get('/api/diagnostics', params={'book_id': 'not-a-book-id'})
    assert response.status_code == 400 and response.json()['code'] == 'book_id_invalid'


@pytest.mark.parametrize('field, value', [('job_id', '0' * 32), ('session_id', 'a' * 64), ('passage_id', 'p_' + 'b' * 12)])
def test_diagnostic_identifiers_without_a_book_are_refused(client, field, value):
    response = client.post('/api/diagnostics', json={'event': 'buffer_failed', field: value})
    assert response.status_code == 422
    assert response.json() == {'detail': 'Invalid diagnostic event fields.', 'code': 'validation_error'}
    assert client.get('/api/diagnostics').json()['events'] == []
    book = '12345678-1234-1234-1234-123456789abc'
    accepted = client.post('/api/diagnostics', json={'event': 'buffer_failed', 'book_id': book, field: value})
    assert accepted.status_code == 200 and accepted.json()['recorded'] is True


# ---------------------------------------------------------------- settings

def test_saving_unrelated_settings_keeps_daily_quota_blocks(client):
    from bardic.app import TTS_MODELS
    from bardic.tts_limits import LIMITER
    first, second = TTS_MODELS[0], TTS_MODELS[1]
    LIMITER.block_day(first, 3600)
    LIMITER.block_day(second, 3600)

    def blocked(model):
        return client.get('/api/status').json()['tts_rate'][model]['daily_block_seconds'] > 0

    assert client.post('/api/settings', json={'analysis_provider': 'local'}).status_code == 200
    assert blocked(first) and blocked(second)
    # Re-sending the same limits changes nothing, so nothing is lifted.
    same = client.get('/api/status').json()['tts_limits'][first]
    assert client.post('/api/settings', json={'tts_limits': {first: same}}).status_code == 200
    assert blocked(first)
    # Changing one model's limits lifts only that model's block.
    assert client.post('/api/settings', json={'tts_limits': {first: {'rpd': 500}}}).status_code == 200
    assert not blocked(first) and blocked(second)
    # Re-sending the current key is no change either.
    assert client.post('/api/settings', json={'api_keys': {'gemini': ''}}).status_code == 200
    assert blocked(second)
    # A new Gemini key may be another project: every block is lifted.
    assert client.post('/api/settings', json={'api_keys': {'gemini': 'offline-other-project'}}).status_code == 200
    assert not blocked(second)


def test_partial_tts_limits_merge_over_saved_values(client):
    from bardic.app import TTS_MODELS
    model = TTS_MODELS[0]
    saved = client.post('/api/settings', json={'tts_limits': {model: {'rpm': 50, 'tpm': 20_000, 'rpd': 500}}}).json()
    assert saved['tts_limits'][model] == {'rpm': 50, 'tpm': 20_000, 'rpd': 500}
    merged = client.post('/api/settings', json={'tts_limits': {model: {'rpd': 700}}}).json()
    assert merged['tts_limits'][model] == {'rpm': 50, 'tpm': 20_000, 'rpd': 700}
    assert client.app.state.runtime.store.settings()['tts_limits'][model] == {'rpm': 50, 'tpm': 20_000, 'rpd': 700}
    for bad in ({'rpx': 5}, {'rpm': 0}, {'rpm': '5'}, {'rpm': True}, {'rpd': 10_000_001}):
        response = client.post('/api/settings', json={'tts_limits': {model: bad}})
        assert response.status_code == 422, (bad, response.text)
    assert client.get('/api/status').json()['tts_limits'][model] == {'rpm': 50, 'tpm': 20_000, 'rpd': 700}


def test_environment_breeze_url_is_used_but_never_saved(tmp_path, monkeypatch):
    monkeypatch.setenv('BREEZE_TTS_URL', 'http://breeze.invalid:7860/')
    with TestClient(create_app(tmp_path)) as first:
        assert first.get('/api/status').json()['breeze_url'] == 'http://breeze.invalid:7860'
        assert first.post('/api/settings', json={'analysis_provider': 'local'}).status_code == 200
        assert first.app.state.runtime.store.settings().get('breeze_url', '') == ''
        assert first.app.state.runtime.breeze_config()['base_url'] == 'http://breeze.invalid:7860'
    monkeypatch.delenv('BREEZE_TTS_URL')
    with TestClient(create_app(tmp_path)) as second:
        assert second.get('/api/status').json()['breeze_url'] == ''
        # A URL the client sends is saved and wins over the environment.
        assert second.post('/api/settings', json={'breeze_url': 'http://saved.invalid:9000'}).status_code == 200
    monkeypatch.setenv('BREEZE_TTS_URL', 'http://breeze.invalid:7860')
    with TestClient(create_app(tmp_path)) as third:
        assert third.get('/api/status').json()['breeze_url'] == 'http://saved.invalid:9000'
        # Clearing it falls back to the environment at once; the empty value is what is saved.
        cleared = third.post('/api/settings', json={'breeze_url': ''}).json()
        assert cleared['breeze_url'] == 'http://breeze.invalid:7860'
        assert third.app.state.runtime.store.settings()['breeze_url'] == ''


def test_status_has_no_alias_or_server_path(client):
    status = client.get('/api/status').json()
    assert 'analysis_models' not in status and 'data_directory' not in status
    assert 'analysis_model' not in status and 'has_api_key' not in status
    assert status['model_catalogs']['gemini']['models']


@pytest.mark.parametrize('body, code', [
    ({'api_keys': {'mistral': 'x'}}, 'cloud_provider_unknown'),
    ({'analysis_models_by_provider': {'openai': 'bad model'}}, 'model_id_invalid'),
    ({'analysis_provider': 'mistral'}, 'analysis_provider_unknown'),
    ({'tts_model': 'not-a-tts-model'}, 'tts_model_unsupported'),
    ({'tts_limits': {'not-a-tts-model': {'rpm': 5}}}, 'tts_model_unsupported'),
    ({'breeze_url': 'ftp://host'}, 'breeze_url_invalid'),
    ({'local_service_urls': {'nowhere': 'http://host'}}, 'local_service_unknown'),
    ({'local_service_urls': {'booknlp': 'http://host/path'}}, 'service_url_invalid'),
])
def test_settings_errors_have_codes(client, body, code):
    response = client.post('/api/settings', json=body)
    assert response.status_code == 400 and response.json()['code'] == code, response.text
    assert 'Settings' not in response.json()['detail']


def test_provider_route_errors_have_codes(client):
    for path in ('/api/account-checks/mistral', '/api/models/local/refresh'):
        response = client.post(path)
        assert response.status_code == 400 and response.json()['code'] == 'cloud_provider_unknown'
    response = client.post('/api/narration/breeze/refresh')
    assert response.status_code == 400 and response.json()['code'] == 'breeze_url_missing'
    assert 'Settings' not in response.json()['detail']


def test_account_check_reports_provider_refusals_in_the_body(client, monkeypatch):
    # A classified refusal is the check's answer (HTTP 200), not a gateway failure.
    monkeypatch.setattr('bardic.app.check_account', lambda provider, key, model: {
        'state': 'invalid_key', 'message': 'The provider rejected this key.', 'usage': None, 'http_status': 401})
    client.post('/api/settings', json={'api_keys': {'openai': 'offline-test-key'}})
    response = client.post('/api/account-checks/openai')
    assert response.status_code == 200 and response.json()['state'] == 'invalid_key'


# ---------------------------------------------------------------- version handshake

def test_status_reports_the_contract_this_server_implements(client):
    contract = client.get('/api/status').json()['contract']
    assert contract['version'] == VERSION
    # The hash is of the checked-in document, as recorded under this version in the changelog.
    published = (CONTRACT / 'openapi.json').read_bytes()
    assert contract['sha256'] == hashlib.sha256(published).hexdigest()
    assert json.loads(published)['info']['version'] == contract['version']
    newest = re.search(r'^## (\d+\.\d+\.\d+) — .*\n<!-- contract-sha256: ([0-9a-f]{64}) -->',
                       (CONTRACT / 'CHANGELOG.md').read_text(encoding='utf-8'), re.M)
    assert newest.groups() == (contract['version'], contract['sha256'])
    # Saving settings answers with the same status object.
    assert client.post('/api/settings', json={}).json()['contract'] == contract
    # The served document is the one that was hashed.
    assert client.get('/openapi.json').json()['info']['version'] == contract['version']


def test_every_api_response_names_the_contract_version(tmp_path):
    with TestClient(create_app(tmp_path)) as test_client:
        ok = test_client.get('/api/status')
        assert ok.status_code == 200 and ok.headers['Bardic-Contract-Version'] == VERSION
        missing = test_client.get('/api/jobs/no-such-job')
        assert missing.status_code == 404 and missing.headers['Bardic-Contract-Version'] == VERSION
        nowhere = test_client.get('/api/nowhere')
        assert nowhere.status_code == 404 and nowhere.headers['Bardic-Contract-Version'] == VERSION
        invalid = test_client.get('/api/diagnostics', params={'limit': 'many'})
        assert invalid.status_code == 422 and invalid.headers['Bardic-Contract-Version'] == VERSION
        guarded = test_client.post('/api/settings', json={}, headers={'Origin': 'http://other.example'})
        assert guarded.status_code == 403 and guarded.json()['code'] == 'cross_origin_write'
        assert guarded.headers['Bardic-Contract-Version'] == VERSION and guarded.headers['Cache-Control'] == 'no-store'
        host = test_client.get('/api/status', headers={'Host': 'other.example'})
        assert host.status_code == 400 and host.headers['Bardic-Contract-Version'] == VERSION
        # Non-API paths are not part of the contract.
        assert 'Bardic-Contract-Version' not in test_client.get('/openapi.json').headers


def test_the_first_contract_build_is_safe_under_concurrent_callers(tmp_path):
    # The contract is built lazily and the build is not repeatable, so simultaneous first callers must share one.
    app = create_app(tmp_path)
    results, errors = [], []

    def build():
        try:
            results.append(identity(app))
            results.append(app.openapi())
        except Exception as error:  # pragma: no cover - only on a regression
            errors.append(error)

    threads = [threading.Thread(target=build) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len({json.dumps(result, sort_keys=True) for result in results}) == 2  # one identity, one document


def test_an_unexpected_defect_still_names_the_contract_version(tmp_path):
    # Starlette answers an escaped exception outside the middleware, so the handler sets the headers itself.
    # (The shared test client rejects any 500, so the handler is called directly.)
    app = create_app(tmp_path)
    for path, expected in (('/api/status', True), ('/openapi.json', False)):
        request = Request({'type': 'http', 'method': 'GET', 'path': path, 'headers': [], 'query_string': b''})
        response = asyncio.run(app.exception_handlers[Exception](request, RuntimeError('defect')))
        assert response.status_code == 500 and json.loads(response.body)['code'] == 'internal_error'
        assert (response.headers.get('Bardic-Contract-Version') == VERSION) is expected
        assert (response.headers.get('Cache-Control') == 'no-store') is expected
