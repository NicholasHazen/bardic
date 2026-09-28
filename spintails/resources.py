"""Durable measurements, separate from artifacts and conservative budget guards.

Analysis HTTP attempts remain the sole analysis usage ledger. Local and narration
operations supplement them; cache events describe reuse without inventing a call.
No source text, HTTP payload, exception message, or credentials belong here.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
import json
import math
import time
from uuid import uuid4

from .store import now

_OPERATION = ContextVar('resource_operation', default=None)
PROCESS_ID = uuid4().hex
PRICE_SOURCE = 'https://ai.google.dev/gemini-api/docs/pricing'
PRICE_DATE = '2026-09-27'
CPU_NOTE = 'CPU is measured for the current Python thread only; excludes subprocesses, other threads, GPU and remote provider compute.'
_COUNTS = ('request_count', 'input_tokens', 'output_tokens', 'cached_input_tokens', 'cache_write_input_tokens', 'output_bytes')
_NUMBERS = ('elapsed_seconds', 'cpu_seconds', 'audio_seconds', 'estimated_cost_usd')
_STRINGS = ('artifact_id', 'asset_id', 'cost_basis', 'price_as_of', 'price_source', 'usage_source', 'cpu_scope')


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _metrics(value):
    result = {key: _count(value.get(key)) for key in _COUNTS}
    result.update({key: _number(value.get(key)) for key in _NUMBERS})
    result.update({key: value[key][:1024] for key in _STRINGS if isinstance(value.get(key), str)})
    if type(value.get('http_status')) is int:
        result['http_status'] = value['http_status']
    return result


class ResourceLedger:
    def __init__(self, store):
        self.store = store
        with store.lock, store.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS resource_operations (id TEXT PRIMARY KEY, book_id TEXT NOT NULL, run_id TEXT, stage TEXT NOT NULL, body TEXT NOT NULL)')
            conn.execute('CREATE INDEX IF NOT EXISTS resource_operations_book ON resource_operations(book_id,run_id)')

    def _save(self, row):
        with self.store.lock, self.store.connect() as conn:
            conn.execute('INSERT INTO resource_operations VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                         (row['id'], row['book_id'], row.get('run_id'), row['stage'], json.dumps(row)))

    @contextmanager
    def operation(self, book_id, stage, *, run_id=None, unit_key=None, chapter_id=None,
                  provider='local', model=None, cached=False, measure_cpu=False, kind='local'):
        """Measure one leaf step. Do not wrap analysis HTTP attempts again.

        `measure_cpu` is appropriate only for synchronous local Python work.
        Callers can update the yielded metrics with output_bytes/audio_seconds.
        Nested wrappers are rejected to prevent double-counting measured time.
        """
        if _OPERATION.get() is not None:
            raise ValueError('Resource operations must measure separate, non-nested steps.')
        local = provider in {'local', 'system'}
        metrics = {'request_count': 0 if local or cached else None,
                   'estimated_cost_usd': 0. if local or cached else None,
                   'cost_basis': 'no_provider_request' if local or cached else 'unknown'}
        row = {'id': uuid4().hex, 'book_id': book_id, 'run_id': run_id, 'stage': stage,
               'unit_key': unit_key, 'chapter_id': chapter_id, 'provider': provider, 'model': model,
               'kind': kind, 'cached': bool(cached), 'status': 'running', 'process_id': PROCESS_ID, 'created_at': now(), **_metrics(metrics)}
        self._save(row)
        started, cpu = time.perf_counter(), time.thread_time() if measure_cpu and local else None
        token = _OPERATION.set((self, row, metrics))
        try:
            yield metrics
        except BaseException as error:
            row['status'] = 'interrupted' if isinstance(error, (KeyboardInterrupt, InterruptedError, SystemExit)) else 'failed'
            raise
        else:
            row['status'] = 'completed'
        finally:
            if metrics.get('cached') is True and not metrics.get('request_count'):
                row['cached'] = True
                metrics.update(request_count=0, estimated_cost_usd=0., cost_basis='no_provider_request')
            metrics['elapsed_seconds'] = max(0., time.perf_counter() - started)
            if cpu is not None:
                metrics.update(cpu_seconds=max(0., time.thread_time() - cpu), cpu_scope='current_python_thread')
            row.update(_metrics(metrics), completed_at=now())
            _OPERATION.reset(token)
            self._save(row)


def publish_metrics(**metrics):
    """Persist provider-reported usage immediately, even if audio later fails validation."""
    active = _OPERATION.get()
    if active:
        ledger, row, target = active
        target.update(metrics)
        row.update(_metrics(target))
        ledger._save(row)


def operation_active():
    return _OPERATION.get() is not None


def tts_usage(payload, model, *, today=None):
    """Gemini Interactions usage; counts are reported, never inferred from WAV length.

    Standard paid-tier list estimate, not account-specific billing. No cache
    storage or free-tier entitlement is inferred. Unknown/mixed modalities stay
    unpriced. Published scheduled rates are used only through their dated window.
    """
    usage = payload.get('usage') if isinstance(payload, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    result = {'input_tokens': _count(usage.get('total_input_tokens')),
              'output_tokens': _count(usage.get('total_output_tokens')),
              'cached_input_tokens': _count(usage.get('total_cached_tokens')),
              'usage_source': 'gemini_interactions' if usage else 'not_reported',
              'estimated_cost_usd': None, 'cost_basis': 'unknown', 'price_as_of': PRICE_DATE, 'price_source': PRICE_SOURCE}
    rates = {'gemini-3.8-flash-tts': (.5, 9., .125), 'gemini-3.8-flash-lite-tts': (.5, 6., .125),
             'gemini-3.1-flash-tts-preview': (1., 20., None)}.get(model)
    today = today or date.today()
    if not rates or today > date(2026, 12, 31):
        return result
    # A total without modality detail is still reported usage, but cannot safely
    # prove that every billed output token is audio rather than another modality.
    def modality_total(field, allowed, total):
        parts = usage.get(field)
        return (isinstance(parts, list) and bool(parts) and
                all(isinstance(p, dict) and p.get('modality') == allowed and _count(p.get('tokens')) is not None for p in parts) and
                sum(p['tokens'] for p in parts) == total)
    inputs, outputs, cached = result['input_tokens'], result['output_tokens'], result['cached_input_tokens']
    if (inputs is None or outputs is None or cached is None or cached > inputs or
            not modality_total('input_tokens_by_modality', 'text', inputs) or
            not modality_total('output_tokens_by_modality', 'audio', outputs) or
            _count(usage.get('total_thought_tokens', 0)) != 0 or _count(usage.get('total_tool_use_tokens', 0)) != 0 or
            (cached and rates[2] is None)):
        return result
    result.update(estimated_cost_usd=((inputs - cached) * rates[0] + outputs * rates[1] + cached * (rates[2] or 0)) / 1e6,
                  cost_basis='standard_paid_tier_usage_estimate')
    return result


def _aggregate(rows):
    result = {'operations': len(rows), 'requests': sum(r.get('request_count') or 0 for r in rows),
              'cached_operations': sum(bool(r.get('cached')) for r in rows),
              'failed_operations': sum(r.get('status') in {'failed', 'uncertain', 'interrupted'} or r.get('validation_state') == 'rejected' for r in rows),
              'running_operations': sum(r.get('status') in {'running', 'reserved'} for r in rows)}
    for field in (*_NUMBERS, 'input_tokens', 'output_tokens', 'cached_input_tokens', 'cache_write_input_tokens', 'output_bytes'):
        # Token/remote-cost coverage concerns provider requests only. Local work
        # has no model tokens; old requests lacking counts remain unknown.
        eligible = [r for r in rows if field not in {'input_tokens', 'output_tokens', 'cached_input_tokens', 'cache_write_input_tokens'} or r.get('request_count') != 0]
        values = [r[field] for r in eligible if _number(r.get(field)) is not None]
        result[field] = round(sum(values), 8) if values else (0 if not eligible else None)
        result['unknown_' + field + '_operations'] = sum(_number(r.get(field)) is None for r in eligible)
    result['reserved_cost_usd'] = round(sum(r.get('estimated_cost_usd') or 0 for r in rows if r.get('cost_basis') == 'reservation'), 8)
    result['unknown_request_count_operations'] = sum(r.get('request_count') is None for r in rows)
    return result


def resource_summary(store, book_id, *, limit=100, offset=0, run_id=None):
    """Read recorded work only. Never calls a provider or backfills guessed usage."""
    from .processing import ProcessingStore
    repository = ProcessingStore(store)
    ResourceLedger(store)
    limit, offset = max(1, min(200, int(limit))), max(0, int(offset))
    with store.lock, store.connect() as conn:
        events = [json.loads(r[0]) for r in conn.execute('SELECT body FROM pipeline_events WHERE book_id=?', (book_id,))]
        operations = [json.loads(r[0]) for r in conn.execute('SELECT body FROM resource_operations WHERE book_id=?', (book_id,))]
        # Source locators are safe metadata; avoid reading/returning full recipes.
        locators = {r[0]: r[1] for r in conn.execute("SELECT id,json_extract(payload,'$.source_locator.chapter_id') FROM artifact_versions WHERE book_id=? AND kind='analysis_input'", (book_id,))}
    validation = {event.get('attempt_id'): event['event'].removeprefix('validation_') for event in events
                  if event.get('attempt_id') and event['event'] in {'accepted', 'validation_accepted', 'validation_rejected'}}
    failed_attempts = {event['attempt_id'] for event in events if event.get('attempt_id') and event['event'] == 'failed'}
    rows = []
    for attempt in repository.attempts(book_id):
        status = attempt.get('status', 'unknown')
        if (isinstance(attempt.get('http_status'), int) and attempt['http_status'] >= 400) or attempt['id'] in failed_attempts:
            status = 'failed'
        rows.append({key: attempt.get(key) for key in ('id', 'book_id', 'run_id', 'stage', 'unit_key', 'provider', 'model', 'created_at', 'completed_at', 'process_id',
                      'input_tokens', 'output_tokens', 'cached_input_tokens', 'cache_write_input_tokens', 'elapsed_seconds', 'price_as_of', 'price_source', 'http_status')} |
                    {'kind': 'analysis_request', 'chapter_id': attempt.get('chapter_id') or locators.get(attempt.get('input_artifact_id')),
                     'status': status, 'cached': False, 'request_count': 1, 'validation_state': validation.get(attempt['id'], 'unknown'),
                     'estimated_cost_usd': attempt.get('charged_estimate_usd'), 'cost_basis': attempt.get('cost_basis', 'legacy_estimate' if attempt.get('charged_estimate_usd') is not None else 'unknown')})
    rows.extend(operations)
    # Cache events have no provider request. Duration was not recorded; don't
    # present 0 seconds as a measured speed or price the reused historic output.
    rows.extend({'id': event['id'], 'book_id': book_id, 'run_id': event.get('run_id'), 'stage': event.get('stage'),
                 'unit_key': event.get('unit_key'), 'kind': 'cache_reuse', 'cached': True, 'request_count': 0,
                 'created_at': event.get('created_at'), 'status': 'completed', 'estimated_cost_usd': 0.,
                 'cost_basis': 'no_provider_request'} for event in events if event['event'] == 'cache_hit')
    if run_id is not None:
        rows = [r for r in rows if r.get('run_id') == run_id]
    jobs = {j['id']: j for j in store.jobs(book_id, limit=None)}
    for row in rows:
        previous_process = row.get('process_id') and row['process_id'] != PROCESS_ID
        finished_job = jobs.get(row.get('run_id'), {}).get('status') in {'failed', 'interrupted', 'cancelled', 'completed'}
        if row.get('status') in {'running', 'reserved'} and (previous_process or finished_job):
            row['status'] = 'interrupted'
    rows.sort(key=lambda r: (r.get('created_at') or '', r['id']), reverse=True)
    stages, runs = defaultdict(list), defaultdict(list)
    for row in rows:
        stages[row.get('stage') or 'unrecorded'].append(row)
        if row.get('run_id'):
            runs[row['run_id']].append(row)
    for key in jobs:
        if run_id is None or run_id == key:
            runs.setdefault(key, [])
    run_rows = [{'id': key, 'status': jobs.get(key, {}).get('status', 'unrecorded'),
                 'kind': jobs.get(key, {}).get('kind'), 'created_at': value[-1].get('created_at') if value else jobs.get(key, {}).get('created_at'),
                 'has_measurements': bool(value), **_aggregate(value)} for key, value in runs.items()]
    run_rows.sort(key=lambda r: r.get('created_at') or '', reverse=True)
    return {'schema_version': 1, 'book_id': book_id, 'run_id': run_id, 'totals': _aggregate(rows),
            'stages': [{'id': key, **_aggregate(value)} for key, value in stages.items()],
            'runs': run_rows[:100], 'total_runs': len(run_rows), 'operations': rows[offset:offset + limit],
            'total_operations': len(rows), 'unmeasured_runs': sum(not r['has_measurements'] for r in run_rows), 'limit': limit, 'offset': offset,
            'price_sources': sorted({r['price_source'] for r in rows if r.get('price_source')}), 'notes': [
                'Only recorded work is included. Historical work may have no timing or usage; unknown never means free.',
                'Elapsed totals sum recorded leaf operations, not end-to-end job latency. Remote compute and retry backoff are not measured.',
                CPU_NOTE,
                'Analysis cost is the conservative spending-guard estimate, including input uplift and reservations where usage is missing. TTS uses dated standard paid-tier list prices when modality usage is available. These are not invoices or account credit balances.',
                'Reusing saved output adds no provider request. Missing cached-token counts stay unknown. Cache storage and account discounts are not priced.']}
