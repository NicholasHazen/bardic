"""Plan and execute pipeline steps. Runs write candidates only, never the book.

Every paid request goes through the existing metered adapters: the run's
``RequestBudget`` reserves each HTTP attempt before it is sent, evidence gets
at most one repair generation, and validated unit results are cached by their
exact request identity so a repeated or resumed run reuses them.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
import json
import threading
import time

import httpx

from .. import analysis as a
from .. import local_services
from ..artifacts import output_head, record
from ..processing import BudgetReached, ProcessingStore, RequestBudget, digest, price_for, request_context, token_estimate
from ..store import now
from .contract import LLM_PROVIDERS, SERVICE_PROVIDERS, StepContext, Unit
from .projection import sync
from .repository import PipelineRepository

IDENTITY_VERSION = 1
ADAPTER_VERSION = 1
SERVICE_ADAPTER_VERSION = 1
# Service response fields not retained: the analyzer's script restates the chapter text.
UNRETAINED_SERVICE_FIELDS = ('script',)


def _adapter(provider):
    # Resolved per call so tests and integrations can substitute an adapter.
    return {'gemini': a._request, 'openai': a._openai_request, 'anthropic': a._anthropic_request,
            'local_llm': a._local_llm_request}[provider]


def _service_call(client, provider, base_url, body, cancelled):
    # Resolved per call so tests can substitute a fake service.
    return local_services.analyze(client, provider, base_url, body, cancelled)


def unit_identity(step, unit: Unit, provider, model):
    """The effective request and validation recipe, and the cache key it implies."""
    request = unit.request
    recipe = {'identity_version': IDENTITY_VERSION, 'adapter_version': ADAPTER_VERSION, 'step_id': step.id,
              'step_version': step.request_version or step.version, 'provider': provider, 'model': model,
              'system_instruction': a.DIRECTOR_INSTRUCTION, 'prompt': request.prompt,
              'response_schema': deepcopy(request.schema), 'output_token_limit': request.output_cap,
              'temperature': .2 if provider in ('gemini', 'local_llm') else None, 'locator': unit.key}
    return recipe, f'{step.id}:{digest(recipe)}'


def service_identity(step, unit: Unit, provider):
    """A service call's exact body and its cache key.

    The server's own model string is recorded with the result but is not part of
    the key, because planning never contacts the network. At run time a cached
    result from a different server model is treated as a miss, so a plan made
    after a service upgrade can undercount its service calls.
    """
    recipe = {'identity_version': IDENTITY_VERSION, 'service_adapter_version': SERVICE_ADAPTER_VERSION,
              'step_id': step.id, 'step_version': step.request_version or step.version, 'provider': provider, 'path': '/v1/analyze',
              'body': deepcopy(unit.service.body), 'locator': unit.key}
    return recipe, f'{step.id}:{digest(recipe)}'


def identity(step, unit, provider, model):
    """(recipe, key) for a model or service unit; (None, None) for a local computation."""
    if unit.service is not None:
        return service_identity(step, unit, provider)
    if unit.request is not None:
        return unit_identity(step, unit, provider, model)
    return None, None


def context_for(store, repository, step, book, conn, *, chapter_ids=None, provider='local', model=None, cancelled=lambda: False):
    inputs, heads = {}, {}
    for input_id in step.inputs:
        inputs[input_id], heads[input_id] = repository.accepted(conn, book['id'], input_id)
    return StepContext(book=deepcopy(book), store=store, inputs=inputs, input_heads=heads,
                       chapter_ids=frozenset(chapter_ids) if chapter_ids else None,
                       provider=provider, model=model, cancelled=cancelled)


def plan(store, registry, book_id, step_ids, configs, *, chapter_ids=None, fresh=False):
    """Known work and conservative estimates. Read-only apart from free census caches."""
    repository = PipelineRepository(store)
    steps = registry.closure(step_ids)
    requested = {s.id for s in steps}
    result, fingerprint_parts = [], []
    with store.lock:
        book = store.book(book_id)
        with store.connect() as conn:
            missing = registry.missing_inputs(lambda step_id: repository.heads(conn, book_id, step_id), step_ids)
        for step in steps:
            config = configs[step.id]
            with store.connect() as conn:
                ctx = context_for(store, repository, step, book, conn, chapter_ids=chapter_ids if step.chapter_scoped else None,
                                  provider=config['provider'], model=config.get('model'))
            units = step.units(ctx)
            item = {'step_id': step.id, 'label': step.label, 'method': step.method, 'provider': config['provider'],
                    'model': config.get('model'), 'units': len(units), 'cached_units': 0, 'requests': 0,
                    'service_calls': 0, 'estimated_input_tokens': 0, 'output_token_allowance': 0, 'estimated_cost_usd': 0.0,
                    'inputs_pending': sorted(requested.intersection(step.inputs)),
                    'missing_inputs': missing.get(step.id, []),
                    'scopes': len({u.scope for u in units})}
            keys = []
            for unit in units:
                if unit.service is not None:
                    _, key = service_identity(step, unit, config['provider'])
                    keys.append(key)
                    if not fresh and repository.unit(book_id, key):
                        item['cached_units'] += 1
                    else:
                        item['service_calls'] += 1
                    continue
                if unit.request is None:
                    keys.append(unit.key)
                    continue
                recipe, key = unit_identity(step, unit, config['provider'], config.get('model'))
                keys.append(key)
                if not fresh and repository.unit(book_id, key):
                    item['cached_units'] += 1
                    continue
                tokens = token_estimate(unit.request.prompt) + token_estimate(json.dumps(unit.request.schema))
                item['requests'] += 1
                item['estimated_input_tokens'] += tokens
                item['output_token_allowance'] += unit.request.output_cap
                price = price_for(config['provider'], config.get('model'), input_tokens=tokens)
                if item['estimated_cost_usd'] is not None:
                    if price.get('input_usd_per_million') is None or price.get('output_usd_per_million') is None:
                        item['estimated_cost_usd'] = None
                    else:
                        item['estimated_cost_usd'] += (tokens * price['input_usd_per_million'] * 1.25 +
                                                       unit.request.output_cap * price['output_usd_per_million']) / 1e6
            if item['inputs_pending']:
                item['note'] = ('Estimated from the currently accepted ' + ', '.join(item['inputs_pending']) +
                                '. This run may change those inputs and therefore this work.')
            fingerprint_parts.append([step.id, step.version, config['provider'], config.get('model'), keys])
            result.append(item)
    costs = [s['estimated_cost_usd'] for s in result]
    return {'book_id': book_id, 'steps': result, 'chapter_ids': sorted(chapter_ids) if chapter_ids else None,
            'requests': sum(s['requests'] for s in result), 'cached_units': sum(s['cached_units'] for s in result),
            'service_calls': sum(s['service_calls'] for s in result),
            'estimated_input_tokens': sum(s['estimated_input_tokens'] for s in result),
            'output_token_allowance': sum(s['output_token_allowance'] for s in result),
            'estimated_cost_usd': None if any(c is None for c in costs) else round(sum(costs), 6),
            'fresh': fresh, 'missing_inputs': missing,
            'fingerprint': digest([book.get('revision', 0), sorted(chapter_ids) if chapter_ids else None, fresh, fingerprint_parts]),
            'note': 'Estimates cover currently known work before retries or evidence repairs; provider invoices are authoritative.'}


class RunExecutor:
    """Execute one orchestrated run inside a job worker thread."""

    def __init__(self, store, registry, run, *, secrets, cancelled, progress, accept, limits, concurrency=2, fresh=False):
        self.store, self.registry, self.run = store, registry, run
        self.book_id = run['book_id']
        self.repository = PipelineRepository(store)
        self.processing = ProcessingStore(store)
        self.secrets = secrets
        self.cancelled = cancelled
        self.progress = progress
        self.accept = accept
        self.budget = RequestBudget(self.processing, self.book_id, run['job_id'], **(limits or {}))
        self.slots = threading.BoundedSemaphore(max(1, concurrency))
        self.concurrency = max(1, concurrency)
        # Fresh runs request new samples instead of reusing identical validated units.
        self.fresh = fresh
        self.counter_lock = threading.Lock()
        self.total = self.done = 0
        self.outcomes = {}
        # What each self-hosted service reports it runs (asked once per run). local_services serializes the calls.
        self.served = {}

    # --- orchestration -----------------------------------------------------------------
    def execute(self):
        steps = self.registry.closure(self.run['steps'])
        self.repository.update_run(self.run['id'], status='running', started_at=now())
        failure = None
        try:
            with httpx.Client(timeout=httpx.Timeout(180, connect=15)) as self.client:
                if self.run.get('scheduling') == 'parallel':
                    failure = self._parallel(steps)
                else:
                    for step in steps:
                        failure = self._attempt(step) or failure
                        if isinstance(failure, (BudgetReached, InterruptedError)):
                            break
        finally:
            status = ('budget_limited' if isinstance(failure, BudgetReached) else 'cancelled' if isinstance(failure, InterruptedError)
                      else 'failed' if failure else 'completed')
            self.repository.update_run(self.run['id'], status=status, completed_at=now(), outcomes=self.outcomes,
                                       error=self._safe(failure) if failure else None)
        if failure:
            raise failure

    def _parallel(self, steps):
        remaining = list(steps)
        running = {}
        failure = None
        with ThreadPoolExecutor(max_workers=len(steps) or 1, thread_name_prefix='pipeline-step') as pool:
            while remaining or running:
                stop = isinstance(failure, (BudgetReached, InterruptedError))
                for step in list(remaining):
                    if stop:
                        remaining.remove(step)
                        self.outcomes[step.id] = {'status': 'skipped', 'reason': 'The run stopped.'}
                        continue
                    waiting = [i for i in step.inputs if i in self.run['steps'] and self.outcomes.get(i) is None]
                    if waiting:
                        continue
                    remaining.remove(step)
                    running[pool.submit(self._attempt, step)] = step
                if not running:
                    break
                finished, _ = wait(list(running), return_when=FIRST_COMPLETED)
                for future in finished:
                    running.pop(future)
                    result = future.result()
                    # The first budget stop or cancellation decides the run; later errors must not hide it.
                    if not isinstance(failure, (BudgetReached, InterruptedError)):
                        failure = result or failure
        return failure

    def _blocked(self, step):
        # Only required inputs gate a step; others are recorded (and still run first).
        for input_id in step.required_inputs:
            if input_id not in self.run['steps']:
                continue
            label = self.registry.get(input_id).label
            outcome = self.outcomes.get(input_id) or {}
            if outcome.get('status') != 'completed':
                return f'{label} did not complete in this run.'
            if not outcome.get('accepted') and outcome.get('scopes'):
                return f'{label} is waiting for your review.'
            with self.store.lock, self.store.connect() as conn:
                if not self.repository.heads(conn, self.book_id, input_id):
                    return f'{label} has no accepted result to read.'
        return None

    def _attempt(self, step):
        """Run one step; return the exception that should stop the run, if any."""
        reason = self._blocked(step)
        if reason:
            self.outcomes[step.id] = {'status': 'skipped', 'reason': reason}
            return None
        step_run = {}
        try:
            self._run_step(step, step_run)
            return None
        except Exception as exc:
            if self.cancelled() and not isinstance(exc, (BudgetReached, InterruptedError)):
                exc = a.AnalysisCancelled('Stopped. Validated units are saved and will be reused.')
            status = ('budget_limited' if isinstance(exc, BudgetReached) else
                      'cancelled' if isinstance(exc, InterruptedError) else 'failed')
            self.outcomes.setdefault(step.id, {'status': status, 'step_run_id': step_run.get('id'), 'scopes': 0,
                                               'accepted': False, 'error': self._safe(exc)})
            # A failure before results were recorded must not leave a 'running' version behind.
            if step_run.get('id') and self.repository.step_run(self.book_id, step_run['id'])['status'] in {'queued', 'running'}:
                self.repository.update_step_run(step_run['id'], status=status, error=self._safe(exc), completed_at=now())
            return exc

    # --- one step ---------------------------------------------------------------------------
    def _run_step(self, step, created):
        config = self.run['configs'][step.id]
        provider, model = config['provider'], config.get('model')
        chapter_ids = self.run.get('chapter_ids') if step.chapter_scoped else None
        with self.store.lock, self.store.connect() as conn:
            book = self.store._hydrate(json.loads(conn.execute('SELECT body FROM books WHERE id=?', (self.book_id,)).fetchone()[0]), conn)
            sync(self.repository, self.registry, conn, book)
            ctx = context_for(self.store, self.repository, step, book, conn, chapter_ids=chapter_ids,
                              provider=provider, model=model, cancelled=self.cancelled)
        step_run = self.repository.create_step_run(self.book_id, step, run_id=self.run['id'], provider=provider,
                                                   model=model, status='running', inputs=ctx.input_heads,
                                                   chapter_ids=chapter_ids)
        created.update(step_run)
        self.run.setdefault('step_run_ids', []).append(step_run['id'])
        self.repository.update_run(self.run['id'], step_run_ids=self.run['step_run_ids'])
        self._check()
        units = step.units(ctx)
        with self.counter_lock:
            self.total += len(units)
        counts = {'total': len(units), 'done': 0, 'cached': 0, 'failed': 0}
        self.repository.update_step_run(step_run['id'], units=counts)
        self._progress(f'{step.label} · planned {len(units)} units')
        done, failures = [], []
        stop = threading.Event()

        def work(unit):
            if stop.is_set():
                return None
            self._check()
            if unit.service is not None:
                return self._service(step, ctx, unit, provider)
            if unit.request is None:
                return step.execute(ctx, unit), False
            return self._llm(step, ctx, unit, provider, model)

        workers = min(step.parallel, self.concurrency)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f'pipeline-{step.id}') as pool:
            futures = {pool.submit(work, unit): unit for unit in units}
            for future in futures:
                unit = futures[future]
                try:
                    outcome = future.result()
                except Exception as exc:
                    if isinstance(exc, (BudgetReached, InterruptedError)):
                        stop.set()
                    failures.append((unit, exc))
                    counts['failed'] += 1
                    continue
                if outcome is None:
                    continue
                result, cached = outcome
                done.append((unit, result))
                counts['done'] += 1
                counts['cached'] += bool(cached)
                with self.counter_lock:
                    self.done += 1
                self.repository.update_step_run(step_run['id'], units=counts)
                try:
                    self._progress(f'{step.label} · {unit.label}')
                except InterruptedError as exc:
                    # Keep what already validated; stop starting new units.
                    stop.set()
                    failures.append((unit, exc))
        # A scope becomes a version only when every one of its units validated.
        finished = {u.key for u, _ in done}
        incomplete = {u.scope for u in units if u.key not in finished}
        complete = [(u, r) for u, r in done if u.scope not in incomplete]
        versions, unchanged = self._record(step, ctx, complete, provider, model)
        stopping = next((exc for _, exc in failures if isinstance(exc, (BudgetReached, InterruptedError))), None)
        error = stopping or (failures[0][1] if failures else None)
        status = ('budget_limited' if isinstance(error, BudgetReached) else 'cancelled' if isinstance(error, InterruptedError)
                  else 'failed' if error else 'completed')
        self.repository.update_step_run(step_run['id'], status=status, scopes=versions, unchanged_scopes=unchanged,
                                        units=counts, error=self._safe(error) if error else None, completed_at=now(),
                                        incomplete_scopes=sorted(incomplete))
        outcome = {'status': status, 'step_run_id': step_run['id'], 'scopes': len(versions), 'accepted': False}
        gate = self.run.get('gates', {}).get(step.id, step.default_gate)
        if versions and status == 'completed' and gate == 'auto':
            self.accept(step, versions, step_run['id'])
            outcome['accepted'] = True
        self.outcomes[step.id] = outcome
        if error:
            raise error

    def _record(self, step, ctx, complete, provider, model):
        payloads = step.assemble(ctx, complete)
        versions, unchanged = {}, []
        with self.store.lock, self.store.connect() as conn:
            unit_outputs = {}
            for unit, _ in complete:
                _, key = identity(step, unit, provider, model)
                if key:
                    identifier = output_head(conn, self.book_id, 'analysis_output', key)
                    if identifier:
                        unit_outputs.setdefault(unit.scope, []).append(identifier)
            flat_inputs = [i for scopes in ctx.input_heads.values() for i in scopes.values()]
            for scope, payload in payloads.items():
                identifier = self.repository.record_version(
                    conn, self.book_id, step, scope, payload, origin='run', provider=provider if step.method != 'plain' else 'local',
                    model=model if step.method != 'plain' else None, inputs=ctx.input_heads,
                    dependencies=[*flat_inputs, *unit_outputs.get(scope, [])])
                versions[scope] = identifier
                if self.repository.head(conn, self.book_id, step.id, scope) == identifier:
                    unchanged.append(scope)
        return versions, sorted(unchanged)

    # --- metered LLM unit ----------------------------------------------------------------------------
    def _llm(self, step, ctx, unit, provider, model):
        api_key = self.secrets.get(provider)
        if provider not in LLM_PROVIDERS or not api_key:
            raise ValueError('Configure the selected analysis provider first.')
        recipe, key = unit_identity(step, unit, provider, model)
        cached = None if self.fresh else self.repository.unit(self.book_id, key)
        if cached:
            try:
                result = step.validate(ctx, unit, deepcopy(cached['result']))
            except (ValueError, KeyError, TypeError) as exc:
                self.repository.forget_unit(self.book_id, key)
                self.processing.event(self.book_id, self.run['job_id'], step.id, key, 'cache_rejected', error=self._safe(exc))
            else:
                self.processing.event(self.book_id, self.run['job_id'], step.id, key, 'cache_hit')
                return result, True
        with self.store.lock, self.store.connect() as conn:
            dependencies = list(unit.dependencies)
            if unit.chapter_id:
                source = output_head(conn, self.book_id, 'source', unit.chapter_id)
                if source:
                    dependencies.append(source)
            dependencies = [d for d in dependencies if conn.execute('SELECT 1 FROM artifact_versions WHERE id=?', (d,)).fetchone()]
            input_id = record(conn, self.book_id, 'analysis_input', key, recipe, label=f'{step.id} request recipe',
                              stage=step.id, provider=provider, model=model, dependencies=dependencies)
        attempts_before = set()

        def last_attempt():
            matches = [x for x in self.processing.attempts(self.book_id, self.run['job_id'])
                       if x['unit_key'] == key and x['id'] not in attempts_before and x.get('input_artifact_id') == input_id]
            return matches[-1]['id'] if matches else None

        def call(repair):
            nonlocal attempts_before
            attempts_before = {x['id'] for x in self.processing.attempts(self.book_id, self.run['job_id'])}
            self.processing.event(self.book_id, self.run['job_id'], step.id, key, 'started', artifact_id=input_id, repair=bool(repair))
            with self.slots, self.budget.context(step.id, key, unit.request.output_cap, chapter_id=unit.chapter_id):
                request_context()['input_artifact_id'] = input_id
                return _adapter(provider)(self.client, model, api_key, unit.request.prompt + ('\n\n' + repair if repair else ''),
                                          unit.request.schema, self.cancelled)

        def validate(result):
            received = deepcopy(result)
            try:
                return step.validate(ctx, unit, result)
            except Exception as exc:
                attempt = last_attempt()
                with self.store.lock, self.store.connect() as conn:
                    rejection = record(conn, self.book_id, 'analysis_rejection', key,
                                       {'result': received, 'validation_error': self._safe(exc), 'unit_key': key, 'attempt_id': attempt},
                                       label=f'{step.id} rejected result', stage=step.id, provider=provider, model=model,
                                       dependencies=[input_id])
                self.processing.event(self.book_id, self.run['job_id'], step.id, key, 'validation_rejected',
                                      artifact_id=rejection, attempt_id=attempt, error=self._safe(exc))
                raise

        try:
            result = a._repairable_request(call, validate, self.cancelled)
        except Exception as exc:
            self.processing.event(self.book_id, self.run['job_id'], step.id, key,
                                  'budget_limited' if isinstance(exc, BudgetReached) else 'failed',
                                  attempt_id=None if isinstance(exc, BudgetReached) else last_attempt(), error=self._safe(exc))
            raise
        attempt = last_attempt()
        value = {'step_id': step.id, 'unit_key': key, 'locator': unit.key, 'scope': unit.scope, 'provider': provider,
                 'model': model, 'result': deepcopy(result), 'producing_attempt_id': attempt, 'created_at': now()}
        artifact_id = self.repository.save_unit(self.book_id, step.id, key, value, dependencies=[input_id])
        self.processing.event(self.book_id, self.run['job_id'], step.id, key, 'accepted', artifact_id=artifact_id, attempt_id=attempt)
        return result, False

    # --- self-hosted chapter service --------------------------------------------------------------------
    def _served(self, provider, base_url):
        """What the service says it runs, asked once per run (provenance, not identity)."""
        with self.counter_lock:
            if provider in self.served:
                return self.served[provider]
        value = local_services.health(self.client, provider, base_url)
        with self.counter_lock:
            return self.served.setdefault(provider, value)

    def _service(self, step, ctx, unit, provider):
        """One free service call. Not metered (no charge), but retained and cached like a model unit.

        The exact body is recorded as the request recipe; a result that fails
        validation is retained as a rejection and never retried automatically.
        """
        base_url = self.secrets.get(provider)
        if provider not in SERVICE_PROVIDERS or not base_url:
            raise ValueError(f'No {local_services.SERVICES[provider]["label"] if provider in local_services.SERVICES else provider} '
                             'server URL is configured.')
        recipe, key = service_identity(step, unit, provider)
        cached = None if self.fresh else self.repository.unit(self.book_id, key)
        served = self._served(provider, base_url)
        # A server that now reports a different model gives different results: not a cache hit.
        # (A server that does not answer keeps its cached results usable.)
        if cached and served and (cached.get('service') or {}).get('model') not in (None, served.get('model')):
            self.processing.event(self.book_id, self.run['job_id'], step.id, key, 'cache_superseded')
            cached = None
        if cached:
            try:
                result = step.validate(ctx, unit, deepcopy(cached['result']))
            except (ValueError, KeyError, TypeError) as exc:
                self.repository.forget_unit(self.book_id, key)
                self.processing.event(self.book_id, self.run['job_id'], step.id, key, 'cache_rejected', error=self._safe(exc))
            else:
                self.processing.event(self.book_id, self.run['job_id'], step.id, key, 'cache_hit')
                return result, True
        with self.store.lock, self.store.connect() as conn:
            dependencies = list(unit.dependencies)
            if unit.chapter_id:
                source = output_head(conn, self.book_id, 'source', unit.chapter_id)
                if source:
                    dependencies.append(source)
            dependencies = [d for d in dependencies if conn.execute('SELECT 1 FROM artifact_versions WHERE id=?', (d,)).fetchone()]
            input_id = record(conn, self.book_id, 'analysis_input', key, recipe, label=f'{step.id} service request',
                              stage=step.id, provider=provider, dependencies=dependencies)
        self.processing.event(self.book_id, self.run['job_id'], step.id, key, 'started', artifact_id=input_id)
        started = time.perf_counter()
        try:
            self._check()
            result = _service_call(self.client, provider, base_url, unit.service.body, self.cancelled)
        except Exception as exc:
            self.processing.event(self.book_id, self.run['job_id'], step.id, key,
                                  'cancelled' if isinstance(exc, InterruptedError) else 'failed', error=self._safe(exc))
            raise
        elapsed = round(time.perf_counter() - started, 3)
        result = {k: v for k, v in result.items() if k not in UNRETAINED_SERVICE_FIELDS}
        try:
            result = step.validate(ctx, unit, result)
        except Exception as exc:
            with self.store.lock, self.store.connect() as conn:
                rejection = record(conn, self.book_id, 'analysis_rejection', key,
                                   {'result': result, 'validation_error': self._safe(exc), 'unit_key': key},
                                   label=f'{step.id} rejected service result', stage=step.id, provider=provider,
                                   dependencies=[input_id])
            self.processing.event(self.book_id, self.run['job_id'], step.id, key, 'validation_rejected',
                                  artifact_id=rejection, error=self._safe(exc))
            raise
        value = {'step_id': step.id, 'unit_key': key, 'locator': unit.key, 'scope': unit.scope, 'provider': provider,
                 'model': None, 'service': served, 'elapsed_seconds': elapsed, 'result': deepcopy(result), 'created_at': now()}
        artifact_id = self.repository.save_unit(self.book_id, step.id, key, value, dependencies=[input_id])
        self.processing.event(self.book_id, self.run['job_id'], step.id, key, 'accepted', artifact_id=artifact_id)
        return result, False

    # --- helpers ---------------------------------------------------------------------------------------
    def _check(self):
        if self.cancelled():
            raise a.AnalysisCancelled('Stopped. Validated units are saved and will be reused.')

    def _progress(self, message):
        with self.counter_lock:
            done, total = self.done, self.total
        try:
            self.progress(done, total, message)
        except Exception:
            # The job layer signals cancellation through its progress callback.
            if self.cancelled():
                raise a.AnalysisCancelled('Stopped. Validated units are saved and will be reused.') from None
            raise

    def _safe(self, exc):
        message = str(exc) or type(exc).__name__
        for key in self.secrets.values():
            if key:
                message = message.replace(key, '[redacted]')
        return message[:900]
