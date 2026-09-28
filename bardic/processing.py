"""Durable analysis units and conservative, per-attempt spending guards."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import math
import time
from uuid import uuid4

from .store import now

REQUEST_CONTEXT = ContextVar('analysis_request_context', default=None)


class BudgetReached(InterruptedError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def source_hash(book):
    return digest([(c['id'], c['text']) for c in book['chapters']])


def token_estimate(text):
    return max(1, math.ceil(len(text.encode('utf-8')) / 3))


def price_for(provider, model, *, input_tokens=None):
    from .model_catalog import model_price
    return model_price(provider, model, input_tokens=input_tokens)


def initialize_schema(conn):
    """Create the processing tables. The Store runs this once when it opens a library."""
    conn.execute('CREATE TABLE IF NOT EXISTS analysis_units (book_id TEXT, unit_key TEXT, stage TEXT, source_hash TEXT, body TEXT NOT NULL, PRIMARY KEY(book_id,unit_key))')
    conn.execute('CREATE INDEX IF NOT EXISTS analysis_units_stage ON analysis_units(book_id,stage,source_hash)')
    conn.execute('CREATE TABLE IF NOT EXISTS analysis_attempts (id TEXT PRIMARY KEY, book_id TEXT, run_id TEXT, body TEXT NOT NULL)')
    conn.execute('CREATE INDEX IF NOT EXISTS analysis_attempts_book ON analysis_attempts(book_id,run_id)')
    conn.execute('CREATE TABLE IF NOT EXISTS book_preprocessing (book_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, body TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS pipeline_events (id TEXT PRIMARY KEY, book_id TEXT NOT NULL, run_id TEXT, unit_key TEXT, stage TEXT, body TEXT NOT NULL)')
    conn.execute('CREATE INDEX IF NOT EXISTS pipeline_events_book ON pipeline_events(book_id,run_id)')


class ProcessingStore:
    def __init__(self, store):
        # Tables are created once by Store.__init__ (initialize_schema), not per construction.
        self.store = store

    def unit(self, book_id, key):
        with self.store.lock, self.store.connect() as conn:
            row = conn.execute('SELECT body FROM analysis_units WHERE book_id=? AND unit_key=?', (book_id, key)).fetchone()
        return json.loads(row[0]) if row else None

    def save_unit(self, book_id, key, stage, source, value):
        from .artifacts import record
        with self.store.lock, self.store.connect() as conn:
            dependencies = value.get('dependency_artifact_ids', [])
            recipe = value.get('input_recipe')
            if recipe:
                recipe_id = record(conn, book_id, 'analysis_input', key, recipe, label=f'{stage} request recipe', stage=stage,
                                   provider=value.get('provider'), model=value.get('model'), dependencies=dependencies)
                dependencies = [recipe_id]
            payload = {k: v for k, v in value.items() if k not in {'input_recipe', 'dependency_artifact_ids'}}
            artifact_id = record(conn, book_id, 'analysis_output', key, payload, label=f'{stage} accepted result', stage=stage,
                                 provider=value.get('provider'), model=value.get('model'), dependencies=dependencies,
                                 legacy_provenance=not bool(recipe))
            conn.execute('INSERT OR REPLACE INTO analysis_units VALUES (?,?,?,?,?)', (book_id, key, stage, source, json.dumps(value, ensure_ascii=False)))
        return artifact_id

    def units(self, book_id, stage, source):
        with self.store.lock, self.store.connect() as conn:
            return [json.loads(r[0]) for r in conn.execute('SELECT body FROM analysis_units WHERE book_id=? AND stage=? AND source_hash=? ORDER BY rowid', (book_id, stage, source))]

    def preprocessing(self, book_id, fingerprint):
        with self.store.lock, self.store.connect() as conn:
            row = conn.execute('SELECT body FROM book_preprocessing WHERE book_id=? AND fingerprint=?', (book_id, fingerprint)).fetchone()
        return json.loads(row[0]) if row else None

    def save_preprocessing(self, book_id, fingerprint, value, *, retain=True):
        """Cache a census; ``retain`` also records it as the current ``census`` artifact."""
        from .artifacts import record
        with self.store.lock, self.store.connect() as conn:
            conn.execute('INSERT OR REPLACE INTO book_preprocessing VALUES (?,?,?)', (book_id, fingerprint, json.dumps(value, ensure_ascii=False)))
            if retain:
                record(conn, book_id, 'census', 'book', value, label='Whole-book local census', stage='census', provider='local')

    def event(self, book_id, run_id, stage, unit_key, event, **details):
        value = {'id': uuid4().hex, 'book_id': book_id, 'run_id': run_id, 'stage': stage, 'unit_key': unit_key,
                 'event': event, 'created_at': now(), **details}
        with self.store.lock, self.store.connect() as conn:
            conn.execute('INSERT INTO pipeline_events VALUES (?,?,?,?,?,?)',
                         (value['id'], book_id, run_id, unit_key, stage, json.dumps(value)))
        return value

    def events(self, book_id, limit=100):
        with self.store.lock, self.store.connect() as conn:
            return [json.loads(row[0]) for row in conn.execute('SELECT body FROM pipeline_events WHERE book_id=? ORDER BY rowid DESC LIMIT ?',
                                                            (book_id, max(1, min(1000, limit))))]

    def attempts(self, book_id, run_id=None):
        with self.store.lock, self.store.connect() as conn:
            query = 'SELECT body FROM analysis_attempts WHERE book_id=?'
            args = (book_id,)
            if run_id is not None:
                query += ' AND run_id=?'
                args += (run_id,)
            return [json.loads(r[0]) for r in conn.execute(query + ' ORDER BY rowid', args)]

    def save_attempt(self, attempt):
        with self.store.lock, self.store.connect() as conn:
            conn.execute('INSERT OR REPLACE INTO analysis_attempts VALUES (?,?,?,?)', (attempt['id'], attempt['book_id'], attempt['run_id'], json.dumps(attempt)))

    def usage(self, book_id):
        attempts = self.attempts(book_id)
        return {'attempts': len(attempts), 'input_tokens': sum(a.get('input_tokens') or 0 for a in attempts),
                'output_tokens': sum(a.get('output_tokens') or 0 for a in attempts),
                'estimated_spend_usd': round(sum(a.get('charged_estimate_usd') or 0 for a in attempts), 6),
                'unknown_cost_attempts': sum(a.get('charged_estimate_usd') is None for a in attempts),
                'unknown_usage_attempts': sum(a.get('input_tokens') is None or a.get('output_tokens') is None for a in attempts),
                'note': 'Tracked analysis only. Estimated charges use dated prices and conservative reservations when usage is unavailable; provider invoices are authoritative.'}


class RequestBudget:
    """Reserve each HTTP attempt before sending; failed/unknown usage keeps its reserve.

    Dollar limits apply to tracked attempts for the entire book, including prior
    runs. Request/token limits apply to this run. ``None`` means no cap; the
    attempt is still reserved and recorded. Estimates are not an invoice.
    """
    def __init__(self, repository, book_id, run_id=None, *, max_requests=25, max_input_tokens=1000000,
                 max_output_tokens=100000, budget_usd=1.0):
        self.repository, self.book_id, self.run_id = repository, book_id, run_id or uuid4().hex
        self.max_requests, self.max_input_tokens, self.max_output_tokens = max_requests, max_input_tokens, max_output_tokens
        self.budget_usd = budget_usd
        self._started = {}

    @contextmanager
    def context(self, stage, unit_key, max_output_tokens=4096, *, chapter_id=None):
        token = REQUEST_CONTEXT.set({'budget': self, 'stage': stage, 'unit_key': unit_key, 'max_output_tokens': max_output_tokens,
                                     'chapter_id': chapter_id})
        try:
            yield
        finally:
            REQUEST_CONTEXT.reset(token)

    def reserve(self, provider, model, body, context):
        from .resources import PROCESS_ID
        text = json.dumps(body, ensure_ascii=False)
        # UTF-8 bytes plus protocol allowance is deliberately much larger than
        # the displayed token estimate. No discounts are assumed for the guard.
        input_reserve = len(text.encode('utf-8')) + 2048
        output_reserve = context['max_output_tokens']
        price = price_for(provider, model, input_tokens=input_reserve)
        input_rate, output_rate = price.get('input_usd_per_million'), price.get('output_usd_per_million')
        estimated_cost = None
        if input_rate is not None and output_rate is not None:
            # Include possible cache-write uplift instead of assuming a cache hit.
            estimated_cost = (input_reserve * input_rate * 1.25 + output_reserve * output_rate) / 1000000
        with self.repository.store.lock:
            attempts = self.repository.attempts(self.book_id)
            run = [a for a in attempts if a['run_id'] == self.run_id]
            if self.max_requests is not None and len(run) >= self.max_requests:
                raise BudgetReached('Run request limit reached. Saved steps are retained; review the plan before resuming.')
            if self.max_input_tokens is not None and sum(a.get('input_tokens') if a.get('input_tokens') is not None else a['reserved_input_tokens'] for a in run) + input_reserve > self.max_input_tokens:
                raise BudgetReached('Run input-token allowance reached. Saved steps are retained.')
            if self.max_output_tokens is not None and sum(a.get('output_tokens') if a.get('output_tokens') is not None else a['reserved_output_tokens'] for a in run) + output_reserve > self.max_output_tokens:
                raise BudgetReached('Run output-token allowance reached. Saved steps are retained.')
            if self.budget_usd is not None:
                if estimated_cost is None:
                    raise BudgetReached('This model has no verified price. Choose a priced model, or explicitly use request/token limits without a dollar guard.')
                if any(a.get('charged_estimate_usd') is None for a in attempts):
                    raise BudgetReached('Earlier tracked requests have unknown cost. Review usage and explicitly use request/token limits to continue without a dollar guard.')
                if sum(a.get('charged_estimate_usd') or 0 for a in attempts) + estimated_cost > self.budget_usd:
                    raise BudgetReached('Book spending guard reached before the next request. Raise the book allowance explicitly to continue; saved work is retained.')
            attempt = {'id': uuid4().hex, 'book_id': self.book_id, 'run_id': self.run_id, 'provider': provider, 'model': model,
                       'process_id': PROCESS_ID,
                       'input_artifact_id': context.get('input_artifact_id'),
                       'chapter_id': context.get('chapter_id'), 'elapsed_seconds': None,
                       'stage': context['stage'], 'unit_key': context['unit_key'], 'status': 'reserved', 'created_at': now(),
                       'reserved_input_tokens': input_reserve, 'reserved_output_tokens': output_reserve,
                       'input_tokens': None, 'output_tokens': None, 'cached_input_tokens': None, 'cache_write_input_tokens': None,
                       'charged_estimate_usd': estimated_cost, 'cost_basis': 'reservation' if estimated_cost is not None else 'unknown',
                       'input_rate': input_rate, 'output_rate': output_rate, 'price_as_of': price.get('as_of'), 'price_source': price.get('source_url')}
            self.repository.save_attempt(attempt)
            self._started[attempt['id']] = time.perf_counter()
            return attempt

    def finish(self, attempt, payload=None, http_status=None, *, not_sent=False):
        if not_sent:
            # The connection failed before the request was written: nothing to bill.
            started = self._started.pop(attempt['id'], None)
            attempt.update(status='not_sent', http_status=None, input_tokens=0, output_tokens=0, cached_input_tokens=0,
                           cache_write_input_tokens=0, charged_estimate_usd=0.0, cost_basis='not_sent',
                           elapsed_seconds=max(0., time.perf_counter() - started) if started is not None else None,
                           completed_at=now())
            self.repository.save_attempt(attempt)
            return
        usage = (payload or {}).get('usageMetadata' if attempt['provider'] == 'gemini' else 'usage', {})
        usage = usage if isinstance(usage, dict) else {}
        def integer(value):
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        def optional_count(key):
            # Absent provider-specific categories mean none were reported. A
            # present malformed value is unknown, never a zero-cost shortcut.
            return integer(usage[key]) if key in usage else 0
        if attempt['provider'] == 'gemini':
            inputs = integer(usage.get('promptTokenCount'))
            outputs = integer(usage.get('candidatesTokenCount'))
            thought = optional_count('thoughtsTokenCount')
            if outputs is not None:
                outputs = outputs + thought if thought is not None else None
            cached, creation = integer(usage.get('cachedContentTokenCount')), None
        else:
            inputs, outputs = integer(usage.get('input_tokens')), integer(usage.get('output_tokens'))
            detail = usage.get('input_tokens_details')
            cached = integer(detail.get('cached_tokens')) if isinstance(detail, dict) else None
            creation = None
            if attempt['provider'] == 'anthropic':
                if inputs is not None:
                    creation, cached = optional_count('cache_creation_input_tokens'), optional_count('cache_read_input_tokens')
                    inputs = inputs + creation + cached if creation is not None and cached is not None else None
                creation, cached = integer(usage.get('cache_creation_input_tokens')), integer(usage.get('cache_read_input_tokens'))
        if cached is not None and inputs is not None and cached > inputs:
            cached = None
        started = self._started.pop(attempt['id'], None)
        attempt.update(status='received' if http_status is not None else 'uncertain', http_status=http_status,
                       input_tokens=inputs, output_tokens=outputs, cached_input_tokens=cached, cache_write_input_tokens=creation,
                       elapsed_seconds=max(0., time.perf_counter() - started) if started is not None else attempt.get('elapsed_seconds'),
                       completed_at=now())
        if inputs is not None and outputs is not None and attempt['input_rate'] is not None and attempt['output_rate'] is not None:
            attempt['charged_estimate_usd'] = (inputs * attempt['input_rate'] * 1.25 + outputs * attempt['output_rate']) / 1000000
            attempt['cost_basis'] = 'usage_estimate_with_guard_uplift'
        self.repository.save_attempt(attempt)


def request_context():
    return REQUEST_CONTEXT.get()
