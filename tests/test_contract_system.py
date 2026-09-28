"""System, diagnostics and job responses that other tests do not reach, checked against the contract.

Offline: provider listings are faked and no job does real work.
"""
import pytest
from fastapi.testclient import TestClient

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
              'source_url': 'https://example.invalid/models', 'pricing_source_url': None, 'availability': 'listed'}
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


def test_unknown_job_cancel_is_not_found(client):
    response = client.post('/api/jobs/0123456789abcdef0123456789abcdef/cancel', json={})
    assert response.status_code == 404 and response.json() == {'detail': 'Job not found'}


def test_diagnostics_query_validation_uses_the_generic_string_detail(client):
    response = client.get('/api/diagnostics', params={'limit': 'many'})
    assert response.status_code == 422
    assert response.json() == {'detail': 'Invalid diagnostic event fields.'}


def test_diagnostic_identifiers_without_a_book_are_dropped_not_refused(client):
    response = client.post('/api/diagnostics', json={'event': 'buffer_failed', 'job_id': '0' * 32})
    assert response.status_code == 200
    assert response.json() == {'recorded': False, 'reason': 'unavailable'}
