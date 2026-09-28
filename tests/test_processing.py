"""Offline transport-level spending and persistence checks; no real keys."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import httpx
import pytest

from bardic import analysis, processing
from bardic.importer import parse_book
from bardic.processing import BudgetReached, ProcessingStore, RequestBudget
from bardic.store import Store


@pytest.fixture
def repository(tmp_path):
    return ProcessingStore(Store(tmp_path))


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    monkeypatch.setattr(analysis.time, 'sleep', lambda _: None)


@pytest.fixture
def priced(monkeypatch):
    def price(provider, model, *, input_tokens=None):
        return {'input_usd_per_million': 1., 'output_usd_per_million': 2., 'as_of': '2026-09-27'}
    monkeypatch.setattr(processing, 'price_for', price)


def transport_request(budget, handler, *, provider='openai', model='test-model', output_cap=17, body_extra=None):
    body = {'model': model, 'prompt': 'Private source excerpt', **(body_extra or {})}
    if provider == 'gemini':
        body.pop('model')
        body['generationConfig'] = {'maxOutputTokens': 999}
    url = f'https://example.invalid/{model}:generateContent' if provider == 'gemini' else 'https://example.invalid/messages'
    with httpx.Client(transport=httpx.MockTransport(handler)) as client, budget.context('discovery', 'unit-one', output_cap):
        return analysis._post_analysis(client, provider, url, {'Authorization': 'private-test-key'}, body,
                                       'private-test-key', lambda: False)


@pytest.mark.parametrize('provider,usage', [
    ('openai', {'usage': {'input_tokens': 10, 'output_tokens': 3}}),
    ('anthropic', {'usage': {'input_tokens': 10, 'output_tokens': 3}}),
    ('gemini', {'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 3}}),
])
def test_attempt_is_durable_before_http_and_exact_cap_sent(repository, priced, provider, usage):
    budget = RequestBudget(repository, 'book')
    def handler(request):
        # A separate connection can already see the committed reservation.
        records = ProcessingStore(Store(repository.store.root)).attempts('book')
        assert len(records) == 1 and records[0]['status'] == 'reserved'
        assert records[0]['reserved_output_tokens'] == 17
        assert records[0]['input_tokens'] is None
        body = json.loads(request.content)
        actual_cap = body['generationConfig']['maxOutputTokens'] if provider == 'gemini' else body['max_output_tokens' if provider == 'openai' else 'max_tokens']
        assert actual_cap == 17
        return httpx.Response(200, json=usage)
    assert transport_request(budget, handler, provider=provider) == usage
    attempt = repository.attempts('book')[0]
    assert attempt['provider'] == provider and attempt['model'] == 'test-model'
    assert attempt['input_tokens'] == 10 and attempt['output_tokens'] == 3
    assert attempt['charged_estimate_usd'] == pytest.approx((10 * 1.25 + 3 * 2) / 1e6)
    assert 'Private source' not in json.dumps(attempt)
    assert 'private-test-key' not in json.dumps(attempt)


@pytest.mark.parametrize('guard,value,message', [('max_requests', 0, 'request limit'), ('max_input_tokens', 1, 'input-token'), ('max_output_tokens', 1, 'output-token'), ('budget_usd', 0, 'spending guard')])
def test_budget_refusal_prevents_any_http_or_attempt(repository, priced, guard, value, message):
    budget = RequestBudget(repository, 'book', **{guard: value})
    with pytest.raises(BudgetReached, match=message):
        transport_request(budget, lambda _: pytest.fail('HTTP must not happen'))
    assert repository.attempts('book') == []


@pytest.mark.parametrize('status', [429, 500, 502, 503, 504, 529])
def test_transient_retry_is_separately_reserved_and_bounded_to_two_attempts(repository, priced, status):
    def handler(_):
        records = repository.attempts('book')
        assert records[-1]['status'] == 'reserved'
        assert all(r['status'] == 'received' for r in records[:-1])
        return httpx.Response(status, json={'error': {'message': 'Temporarily unavailable'}}, headers={'retry-after': '0'})
    with pytest.raises(ValueError, match=f'HTTP {status}'):
        transport_request(RequestBudget(repository, 'book'), handler)
    records = repository.attempts('book')
    assert len(records) == 2 and len({r['id'] for r in records}) == 2
    assert all(r['input_tokens'] is None and r['charged_estimate_usd'] > 0 for r in records)


def test_request_limit_is_checked_again_before_transient_retry(repository, priced):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(503, json={'error': {'message': 'Unavailable'}}, headers={'retry-after': '0'})
    with pytest.raises(BudgetReached, match='request limit'):
        transport_request(RequestBudget(repository, 'book', max_requests=1), handler)
    assert len(calls) == 1 and len(repository.attempts('book')) == 1


@pytest.mark.parametrize('provider,status,payload', [
    ('openai', 401, {'error': {'code': 'invalid_api_key', 'message': 'Invalid key'}}),
    ('openai', 429, {'error': {'code': 'insufficient_quota', 'message': 'Check billing'}}),
    ('openai', 429, {'error': {'code': 'credit_balance_exhausted', 'message': 'Credits exhausted'}}),
    ('anthropic', 400, {'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low'}}),
    ('anthropic', 401, {'error': {'type': 'authentication_error', 'message': 'Invalid key'}}),
    ('gemini', 402, {'error': {'status': 'FAILED_PRECONDITION', 'message': 'Insufficient credits'}}),
    ('gemini', 401, {'error': {'status': 'UNAUTHENTICATED', 'message': 'Invalid key'}}),
])
def test_authentication_and_billing_failures_never_retry(repository, priced, provider, status, payload):
    with pytest.raises(ValueError, match=f'HTTP {status}'):
        transport_request(RequestBudget(repository, 'book'), lambda _: httpx.Response(status, json=payload), provider=provider)
    assert len(repository.attempts('book')) == 1


@pytest.mark.parametrize('error', [httpx.ConnectError, httpx.ConnectTimeout])
def test_failed_connection_is_not_billed_and_retried_once(repository, priced, error):
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise error('No connection', request=request)
        return httpx.Response(200, json={'usage': {'input_tokens': 10, 'output_tokens': 3}})
    transport_request(RequestBudget(repository, 'book'), handler)
    first, second = repository.attempts('book')
    assert first['status'] == 'not_sent' and first['charged_estimate_usd'] == 0 and first['cost_basis'] == 'not_sent'
    assert first['input_tokens'] == 0 and first['output_tokens'] == 0
    assert second['status'] == 'received'
    assert repository.usage('book')['unknown_usage_attempts'] == 0


def test_failed_connection_retry_is_bounded_and_reserved(repository, priced):
    def handler(request):
        raise httpx.ConnectError('No connection', request=request)
    with pytest.raises(ValueError, match='No request was sent'):
        transport_request(RequestBudget(repository, 'book'), handler)
    assert [a['status'] for a in repository.attempts('book')] == ['not_sent', 'not_sent']
    # The retry still passes the run request guard before it is attempted.
    with pytest.raises(BudgetReached, match='request limit'):
        transport_request(RequestBudget(repository, 'other', max_requests=1), handler)
    assert len(repository.attempts('other')) == 1


@pytest.mark.parametrize('error', [httpx.ReadTimeout, httpx.WriteError, httpx.RemoteProtocolError])
def test_uncertain_network_failure_keeps_reservation_and_does_not_repeat(repository, priced, error):
    def handler(request):
        raise error('Unknown delivery state', request=request)
    with pytest.raises(ValueError, match='not automatically repeated'):
        transport_request(RequestBudget(repository, 'book'), handler)
    attempts = repository.attempts('book')
    assert len(attempts) == 1 and attempts[0]['status'] == 'uncertain'
    assert attempts[0]['charged_estimate_usd'] > 0
    assert repository.usage('book')['unknown_usage_attempts'] == 1


def test_spending_guard_persists_across_runs_and_server_restart(repository, priced):
    transport_request(RequestBudget(repository, 'book', run_id='first'), lambda _: httpx.Response(200, json={}))
    reserved_cost = repository.attempts('book')[0]['charged_estimate_usd']
    reopened = ProcessingStore(Store(repository.store.root))
    budget = RequestBudget(reopened, 'book', run_id='second', budget_usd=reserved_cost * 1.5)
    with pytest.raises(BudgetReached, match='spending guard'):
        transport_request(budget, lambda _: pytest.fail('Previous run spend must be counted'))
    assert len(reopened.attempts('book')) == 1
    # Another book has its own tracked allowance.
    transport_request(RequestBudget(reopened, 'other', budget_usd=reserved_cost * 1.5), lambda _: httpx.Response(200, json={}))


def test_unknown_price_requires_explicit_no_dollar_guard_and_blocks_later_guard(repository):
    with pytest.raises(BudgetReached, match='no verified price'):
        transport_request(RequestBudget(repository, 'book'), lambda _: pytest.fail('Unknown price'), model='custom-model')
    transport_request(RequestBudget(repository, 'book', budget_usd=None), lambda _: httpx.Response(200, json={'usage': {'input_tokens': 5, 'output_tokens': 2}}), model='custom-model')
    attempt = repository.attempts('book')[0]
    assert attempt['charged_estimate_usd'] is None
    assert repository.usage('book')['unknown_cost_attempts'] == 1
    with pytest.raises(BudgetReached, match='Earlier tracked requests have unknown cost'):
        transport_request(RequestBudget(repository, 'book'), lambda _: pytest.fail('Historical unknown cost'), model='gpt-6-luna')


def test_price_tier_is_checked_against_conservative_input_reservation(repository):
    with pytest.raises(BudgetReached, match='no verified price'):
        transport_request(RequestBudget(repository, 'book', budget_usd=100), lambda _: pytest.fail('Long-context tier is unpriced'),
                          model='gpt-6-luna', body_extra={'large': 'x' * 280000})


@pytest.mark.parametrize('provider,payload,input_expected,output_expected', [
    ('openai', {'usage': {'input_tokens': 8, 'output_tokens': 3}}, 8, 3),
    ('openai', {'usage': {'input_tokens': 0, 'output_tokens': 0}}, 0, 0),
    ('anthropic', {'usage': {'input_tokens': 8, 'output_tokens': 3, 'cache_creation_input_tokens': 20, 'cache_read_input_tokens': 30}}, 58, 3),
    ('gemini', {'usageMetadata': {'promptTokenCount': 8, 'candidatesTokenCount': 3, 'thoughtsTokenCount': 11}}, 8, 14),
    ('openai', {'usage': {'input_tokens': True, 'output_tokens': -1}}, None, None),
    ('openai', {'usage': {'input_tokens': '8', 'output_tokens': 3.0}}, None, None),
    ('anthropic', {'usage': {'input_tokens': 8, 'output_tokens': 3, 'cache_creation_input_tokens': 'unknown'}}, None, 3),
    ('gemini', {'usageMetadata': {'promptTokenCount': 8, 'candidatesTokenCount': 3, 'thoughtsTokenCount': True}}, 8, None),
    ('openai', {'usage': ['invalid']}, None, None),
    ('openai', {}, None, None),
])
def test_usage_extraction_is_strict_and_missing_values_keep_cost_reservation(repository, priced, provider, payload, input_expected, output_expected):
    transport_request(RequestBudget(repository, 'book'), lambda _: httpx.Response(200, json=payload), provider=provider)
    attempt = repository.attempts('book')[0]
    assert attempt['input_tokens'] == input_expected and attempt['output_tokens'] == output_expected
    if input_expected is None or output_expected is None:
        assert attempt['charged_estimate_usd'] == pytest.approx((attempt['reserved_input_tokens'] * 1.25 + attempt['reserved_output_tokens'] * 2) / 1e6)


def test_context_is_reset_after_exception_and_nested_context(repository):
    first = RequestBudget(repository, 'one')
    second = RequestBudget(repository, 'two')
    assert processing.request_context() is None
    with first.context('discovery', 'one'):
        assert processing.request_context()['budget'] is first
        with pytest.raises(RuntimeError):
            with second.context('profile', 'two'):
                assert processing.request_context()['budget'] is second
                raise RuntimeError('stop')
        assert processing.request_context()['budget'] is first
    assert processing.request_context() is None


def test_parallel_reservations_cannot_overspend_single_request_limit(repository, priced):
    budget = RequestBudget(repository, 'book', max_requests=1)
    context = {'stage': 'discovery', 'unit_key': 'unit', 'max_output_tokens': 10}
    def reserve(_):
        try:
            budget.reserve('openai', 'test-model', {}, context)
            return 'reserved'
        except BudgetReached:
            return 'blocked'
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(reserve, range(4)))
    assert outcomes.count('reserved') == 1 and outcomes.count('blocked') == 3


def test_source_hash_ignores_titles_audio_and_profiles_but_tracks_exact_text_and_identity():
    book = parse_book('story.txt', b'Chapter 1\n\nMara said, "Wait."')
    original = processing.source_hash(book)
    revised = deepcopy(book)
    revised['chapters'][0]['title'] = 'New display title'
    revised['segments'][0]['audio'] = {'fingerprint': 'new'}
    revised['characters'][0]['direction'] = 'New performance'
    assert processing.source_hash(revised) == original
    revised['chapters'][0]['text'] += '!'
    assert processing.source_hash(revised) != original
    revised = deepcopy(book)
    revised['chapters'][0]['id'] = 'other'
    assert processing.source_hash(revised) != original


def test_unit_and_preprocessing_caches_require_matching_book_stage_and_source(repository):
    value = {'chapter_id': 'chapter', 'result': {'characters': []}}
    repository.save_unit('book', 'unit-a', 'discovery', 'source-a', value)
    repository.save_unit('book', 'unit-b', 'profiles', 'source-a', {'profile': True})
    repository.save_unit('book', 'unit-c', 'discovery', 'source-b', {'stale': True})
    repository.save_unit('other', 'unit-a', 'discovery', 'source-a', {'other': True})
    assert repository.unit('book', 'unit-a') == value
    assert repository.unit('missing', 'unit-a') is None
    assert repository.units('book', 'discovery', 'source-a') == [value]
    repository.save_preprocessing('book', 'fingerprint', {'free': True})
    assert repository.preprocessing('book', 'fingerprint') == {'free': True}
    assert repository.preprocessing('book', 'different') is None
    assert repository.preprocessing('other', 'fingerprint') is None
