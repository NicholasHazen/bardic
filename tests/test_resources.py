"""Offline ledger accounting: measured != reserved != missing."""
from datetime import date
import json

import httpx
import pytest

from bardic import audio, processing, resources
from bardic.importer import parse_book
from bardic.preprocessing import census
from bardic.processing import ProcessingStore, RequestBudget
from bardic.resources import ResourceLedger, resource_summary, tts_usage
from bardic.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def test_operation_is_durable_on_start_and_failure_and_measures_only_requested_cpu(store, monkeypatch):
    clock = iter([10., 12.5])
    cpu = iter([4., 4.125])
    monkeypatch.setattr(resources.time, 'perf_counter', lambda: next(clock))
    monkeypatch.setattr(resources.time, 'thread_time', lambda: next(cpu))
    with pytest.raises(ValueError, match='private error'):
        with ResourceLedger(store).operation('book', 'import', unit_key='source', measure_cpu=True) as metrics:
            row = resource_summary(store, 'book')['operations'][0]
            assert row['status'] == 'running' and row['elapsed_seconds'] is None
            metrics.update(output_bytes=12, api_key='private-key', source='Private book passage')
            raise ValueError('private error')
    row = resource_summary(Store(store.root), 'book')['operations'][0]
    assert row['status'] == 'failed' and row['elapsed_seconds'] == 2.5
    assert row['cpu_seconds'] == .125 and row['cpu_scope'] == 'current_python_thread'
    assert row['request_count'] == 0 and row['estimated_cost_usd'] == 0
    assert row['output_bytes'] == 12
    assert 'private' not in json.dumps(row).lower()


def test_operation_without_cpu_opt_in_and_late_cache_reuse(store):
    with ResourceLedger(store).operation('book', 'narration', provider='gemini', kind='narration') as metrics:
        metrics.update(cached=True, audio_seconds=2., output_bytes=96044)
    row = resource_summary(store, 'book')['operations'][0]
    assert row['cached'] and row['request_count'] == 0
    assert row['cpu_seconds'] is None and row['estimated_cost_usd'] == 0
    assert row['audio_seconds'] == 2.


def test_actual_cloud_request_is_not_erased_by_late_cache_flag(store):
    with ResourceLedger(store).operation('book', 'narration', provider='gemini') as metrics:
        resources.publish_metrics(request_count=1, estimated_cost_usd=None)
        metrics['cached'] = True
    row = resource_summary(store, 'book')['operations'][0]
    assert row['request_count'] == 1 and not row['cached'] and row['estimated_cost_usd'] is None


def test_no_nested_operation_double_counting_and_bad_metrics_stay_unknown(store):
    with ResourceLedger(store).operation('book', 'export') as metrics:
        metrics.update(output_bytes=True, audio_seconds=float('nan'))
        with pytest.raises(ValueError, match='non-nested'):
            with ResourceLedger(store).operation('book', 'other'):
                pytest.fail('Nested operation must not start')
    value = resource_summary(store, 'book')
    assert value['total_operations'] == 1
    assert value['operations'][0]['output_bytes'] is None
    assert value['operations'][0]['audio_seconds'] is None


def test_orphaned_operation_after_restart_has_unknown_time_not_running_forever(store, monkeypatch):
    ledger = ResourceLedger(store)
    ledger._save({'id': 'orphan', 'book_id': 'book', 'stage': 'import', 'status': 'running', 'process_id': 'previous-process',
                  'request_count': 0, 'estimated_cost_usd': 0., 'created_at': '2026-09-26T00:00:00Z'})
    row = resource_summary(store, 'book')['operations'][0]
    assert row['status'] == 'interrupted'
    assert row.get('elapsed_seconds') is None


@pytest.mark.parametrize('provider,payload,inputs,cached,created', [
    ('openai', {'usage': {'input_tokens': 40, 'output_tokens': 5, 'input_tokens_details': {'cached_tokens': 30}}}, 40, 30, None),
    ('anthropic', {'usage': {'input_tokens': 10, 'output_tokens': 5, 'cache_read_input_tokens': 30, 'cache_creation_input_tokens': 20}}, 60, 30, 20),
    ('gemini', {'usageMetadata': {'promptTokenCount': 40, 'candidatesTokenCount': 5, 'cachedContentTokenCount': 30}}, 40, 30, None),
    ('openai', {'usage': {'input_tokens': 40, 'output_tokens': 5}}, 40, None, None),
])
def test_attempt_timings_cached_tokens_and_chapter_context(store, monkeypatch, provider, payload, inputs, cached, created):
    monkeypatch.setattr(processing, 'price_for', lambda *args, **kw: {'input_usd_per_million': 1, 'output_usd_per_million': 2})
    clock = iter([7., 9.25])
    monkeypatch.setattr(processing.time, 'perf_counter', lambda: next(clock))
    budget = RequestBudget(ProcessingStore(store), 'book', run_id='run')
    with budget.context('profile', 'unit', chapter_id='chapter'):
        attempt = budget.reserve(provider, 'model', {}, processing.request_context())
        budget.finish(attempt, payload, 200)
    row = resource_summary(store, 'book')['operations'][0]
    assert row['input_tokens'] == inputs and row['cached_input_tokens'] == cached
    assert row['cache_write_input_tokens'] == created and row['chapter_id'] == 'chapter'
    assert row['elapsed_seconds'] == 2.25
    assert row['cost_basis'] == 'usage_estimate_with_guard_uplift'


def test_failed_retries_and_cache_event_are_counted_once_and_unknown_is_not_zero(store):
    repo = ProcessingStore(store)
    budget = RequestBudget(repo, 'book', run_id='run', budget_usd=None)
    for status in [503, 200]:
        attempt = budget.reserve('openai', 'custom-unknown-model', {}, {'stage': 'discovery', 'unit_key': 'u', 'max_output_tokens': 10})
        budget.finish(attempt, {'usage': {'input_tokens': 20, 'output_tokens': 3}} if status == 200 else None, status)
    repo.event('book', 'run', 'discovery', 'u', 'validation_rejected', attempt_id=attempt['id'])
    repo.event('book', 'next-run', 'discovery', 'u', 'cache_hit')
    with ResourceLedger(store).operation('book', 'export', measure_cpu=True):
        pass
    value = resource_summary(store, 'book', limit=2)
    totals = value['totals']
    assert value['total_operations'] == 4 and len(value['operations']) == 2
    assert totals['requests'] == 2 and totals['cached_operations'] == 1
    assert totals['failed_operations'] == 2 and totals['input_tokens'] == 20
    assert totals['unknown_input_tokens_operations'] == 1
    assert totals['estimated_cost_usd'] == 0 and totals['unknown_estimated_cost_usd_operations'] == 2
    assert resource_summary(store, 'book', run_id='run')['totals']['requests'] == 2
    page2 = resource_summary(store, 'book', limit=2, offset=2)
    assert not {r['id'] for r in page2['operations']} & {r['id'] for r in value['operations']}
    assert value['totals'] == page2['totals']


def test_legacy_zero_and_missing_usage_are_not_reconstructed_from_dates(store):
    repo = ProcessingStore(store)
    repo.save_attempt({'id': 'legacy', 'book_id': 'book', 'run_id': 'run', 'stage': 'profile',
                       'created_at': '2026-01-01T00:00:00Z', 'completed_at': '2026-01-01T01:00:00Z',
                       'input_tokens': 0, 'output_tokens': None, 'charged_estimate_usd': None})
    value = resource_summary(store, 'book')
    assert value['totals']['input_tokens'] == 0
    assert value['totals']['output_tokens'] is None
    assert value['totals']['elapsed_seconds'] is None
    assert value['totals']['estimated_cost_usd'] is None


def test_http_200_with_unparseable_result_is_still_a_failed_resource_attempt(store):
    repository = ProcessingStore(store)
    budget = RequestBudget(repository, 'book', budget_usd=None)
    attempt = budget.reserve('openai', 'custom', {}, {'stage': 'discovery', 'unit_key': 'u', 'max_output_tokens': 10})
    budget.finish(attempt, {}, 200)
    repository.event('book', budget.run_id, 'discovery', 'u', 'failed', attempt_id=attempt['id'])
    value = resource_summary(store, 'book')
    assert value['totals']['failed_operations'] == 1
    assert value['operations'][0]['http_status'] == 200
    assert value['operations'][0]['validation_state'] == 'unknown'


def test_local_census_records_only_fresh_work_not_every_read(store):
    book = parse_book('test.txt', b'Chapter 1\n\nMara said, "Come here."')
    census(book, store)
    census(book, store)
    value = resource_summary(store, book['id'])
    assert value['total_operations'] == 1
    assert value['stages'][0]['id'] == 'census'
    assert value['totals']['cpu_seconds'] is not None and value['totals']['requests'] == 0


def usage_payload():
    return {'usage': {'total_input_tokens': 100, 'total_output_tokens': 250, 'total_cached_tokens': 20,
                      'input_tokens_by_modality': [{'modality': 'text', 'tokens': 100}],
                      'output_tokens_by_modality': [{'modality': 'audio', 'tokens': 250}]}}


def test_tts_usage_prices_only_reported_complete_modality_counts():
    value = tts_usage(usage_payload(), audio.DEFAULT_TTS_MODEL, today=date(2026, 9, 27))
    assert value['estimated_cost_usd'] == pytest.approx((80 * .5 + 20 * .125 + 250 * 9) / 1e6)
    assert value['input_tokens'] == 100 and value['output_tokens'] == 250
    assert value['cost_basis'] == 'standard_paid_tier_usage_estimate'
    assert tts_usage(usage_payload(), audio.DEFAULT_TTS_MODEL, today=date(2027, 1, 1))['estimated_cost_usd'] is None
    payload = usage_payload()
    del payload['usage']['output_tokens_by_modality']
    assert tts_usage(payload, audio.DEFAULT_TTS_MODEL)['estimated_cost_usd'] is None
    assert tts_usage({}, audio.DEFAULT_TTS_MODEL)['input_tokens'] is None


@pytest.mark.parametrize('field,value', [('total_input_tokens', True), ('total_output_tokens', -1), ('total_cached_tokens', 1000)])
def test_malformed_tts_counts_are_not_priced(field, value):
    payload = usage_payload()
    payload['usage'][field] = value
    assert tts_usage(payload, audio.DEFAULT_TTS_MODEL)['estimated_cost_usd'] is None


def test_rejected_audio_retains_reported_usage_without_persisting_payload_or_key(store, monkeypatch):
    payload = usage_payload() | {'steps': [], 'private': 'Do not persist source text'}
    monkeypatch.setattr(audio.httpx, 'post', lambda *args, **kwargs: httpx.Response(200, json=payload))
    with pytest.raises(audio.AudioError):
        with ResourceLedger(store).operation('book', 'narration', provider='gemini', model=audio.DEFAULT_TTS_MODEL):
            audio._generate_gemini({'model': audio.DEFAULT_TTS_MODEL, 'text': 'secret source', 'style': '', 'voice': 'Kore'}, 'private-key')
    row = resource_summary(store, 'book')['operations'][0]
    assert row['status'] == 'failed' and row['input_tokens'] == 100 and row['output_tokens'] == 250
    assert row['request_count'] == 1 and row['estimated_cost_usd'] > 0
    assert 'private' not in json.dumps(row) and 'secret' not in json.dumps(row)


def test_provider_usage_is_saved_before_later_audio_processing_and_network_failure_unknown(store, monkeypatch):
    def fail(*args, **kwargs):
        value = resource_summary(store, 'book')['operations'][0]
        assert value['request_count'] == 1 and value['estimated_cost_usd'] is None
        raise httpx.ReadTimeout('private-key')
    monkeypatch.setattr(audio.httpx, 'post', fail)
    with pytest.raises(audio.AudioError):
        with ResourceLedger(store).operation('book', 'narration', provider='gemini'):
            audio._generate_gemini({'model': audio.DEFAULT_TTS_MODEL, 'text': 'text', 'style': '', 'voice': 'Kore'}, 'private-key')
    row = resource_summary(store, 'book')['operations'][0]
    assert row['status'] == 'failed' and row['estimated_cost_usd'] is None and row['input_tokens'] is None


def test_resource_api_is_read_only_and_paginated(tmp_path):
    from fastapi.testclient import TestClient
    from bardic.app import create_app
    with TestClient(create_app(tmp_path)) as client:
        store = client.app.state.runtime.store
        book = parse_book('test.txt', b'Chapter 1\n\nMara said, "Wait."')
        store.save_book(book)
        for index in range(3):
            with ResourceLedger(store).operation(book['id'], 'local_test', unit_key=str(index)):
                pass
        response = client.get(f"/api/books/{book['id']}/resources", params={'limit': 2, 'offset': 1})
        assert response.status_code == 200
        assert response.json()['total_operations'] == 3
        assert len(response.json()['operations']) == 2
        assert response.json()['totals']['requests'] == 0
        assert client.get('/api/books/missing/resources').status_code == 404
