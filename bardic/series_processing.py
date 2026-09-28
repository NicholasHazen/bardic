"""Series runs on the step pipeline: one book's pipeline run per supplied volume, in reading order.

A series plan is the ordered list of each active book's step-pipeline plan for
the same steps and configuration, an aggregate estimate (an unknown price stays
unknown, never zero) and one fingerprint over every book's plan fingerprint.
Confirming that fingerprint authorizes the run; optional limits cap each book.

A series run is a parent job (kind ``series``) that reserves every book and
runs one child job (kind ``pipeline``) per book, one at a time. Before a child
starts, the coordinator re-plans that book and refuses it if its plan no longer
matches the confirmed fingerprint. A child cancelled while queued never starts.
Each HTTP attempt is still reserved and recorded by the pipeline runner.

The pipeline does not yet carry knowledge between volumes: accepted results in
one book are not read by another. Reading order is kept so that the only
cross-book input that exists today (confirmed-link observations the older phase
engine retained, read by the profiles step) comes strictly from earlier volumes.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import Field

from .artifacts import record
from .pipeline import projection
from .pipeline.api import (LABELS, PROVIDER_LABELS, Limits, StepConfig, Strict, credentials_needed, execute_run, resolve_configs,
                           setup_message, step_settings)
from .pipeline.repository import ACTIVE, PipelineRepository
from .pipeline.runner import plan as book_plan
from .series import SeriesRepository
from .store import now

PLAN_VERSION = 2
NOTES = (
    'Books run one at a time in reading order. Missing and planned volumes are skipped; nothing is inferred about them.',
    'Each book is planned from its own accepted results. Accepted results in one book are not read by another.',
    'Estimates cover currently known work before retries or evidence repairs. Work in a step that reads another step '
    'in the same run is only known once that step finishes. Provider invoices are authoritative.',
    'Before each book starts, its plan is checked again. If it changed, that book and the rest of the series are not run.',
)


class SeriesPlanRequest(Strict):
    steps: list[str] = Field(min_length=1, max_length=40)
    configs: dict[str, StepConfig] | None = None
    # Request new samples in every book even when an identical validated unit is cached.
    fresh: bool = False


class SeriesRunRequest(SeriesPlanRequest):
    mode: Literal['serial', 'parallel'] = 'serial'
    gates: dict[str, Literal['auto', 'review']] | None = None
    # Model requests in flight inside the running book. Books run one at a time.
    concurrency: int = Field(default=2, ge=1, le=4)
    # Optional caps for API callers, applied to each book's run.
    limits: Limits = Field(default_factory=Limits)
    expected_fingerprint: str | None = Field(default=None, max_length=64)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def series_view(store, series_id):
    series = next((s for s in SeriesRepository(store).list_series() if s['id'] == series_id), None)
    if series is None:
        raise KeyError('Series not found')
    return series


def _steps(registry, step_ids):
    try:
        return registry.closure(step_ids)
    except KeyError as exc:
        raise ValueError(str(exc).strip("'")) from None


def _book_plan(runtime, registry, book_id, step_ids, configs, fresh):
    """One book's pipeline plan, after recording outside changes (as the book plan endpoint does)."""
    store = runtime.store
    with store.lock:
        book = store.book(book_id)
        with store.connect() as conn:
            projection.sync(PipelineRepository(store), registry, conn, book)
        return book, book_plan(store, registry, book_id, step_ids, configs, fresh=fresh)


def plan(runtime, registry, series_id, steps, *, configs=None, fresh=False):
    """Read-only (apart from free census caches and outside-change capture) series preview."""
    selected = _steps(registry, steps)
    step_ids = [s.id for s in selected]
    configs = resolve_configs(runtime, registry, selected, configs)
    with runtime.store.lock:
        series = series_view(runtime.store, series_id)
        members = sorted(series['books'], key=lambda b: (b['position'], b['book_id']))
        entries = []
        for item in members:
            book, preview = _book_plan(runtime, registry, item['book_id'], step_ids, configs, fresh)
            entries.append({'book_id': book['id'], 'title': book.get('title', 'Untitled'), 'position': item['position'],
                            'fingerprint': preview['fingerprint'], 'plan': preview})
    _, _, missing_credentials = credentials_needed(runtime, registry, configs)
    costs = [e['plan']['estimated_cost_usd'] for e in entries]
    known = [c for c in costs if c is not None]
    total = lambda key: sum(e['plan'][key] for e in entries)
    result = {
        'series_id': series_id, 'name': series['name'], 'plan_version': PLAN_VERSION,
        'steps': step_ids, 'configs': configs, 'fresh': fresh,
        'books': entries,
        'volumes': series.get('volumes', []),
        'skipped_volumes': [v for v in series.get('volumes', []) if v.get('status') != 'available'],
        'requests': total('requests'), 'cached_units': total('cached_units'), 'service_calls': total('service_calls'),
        'estimated_input_tokens': total('estimated_input_tokens'), 'output_token_allowance': total('output_token_allowance'),
        # Unknown is not zero: one unpriced book makes the series total unknown.
        'estimated_cost_usd': round(sum(known), 6) if entries and len(known) == len(costs) else None if entries else 0.0,
        'known_cost_usd': round(sum(known), 6),
        'unknown_cost_books': [e['book_id'] for e in entries if e['plan']['estimated_cost_usd'] is None],
        'missing_inputs': {e['book_id']: e['plan']['missing_inputs'] for e in entries if e['plan']['missing_inputs']},
        'missing_credentials': [{'provider': p, 'label': LABELS.get(p, p), 'needs': 'api_key' if p in PROVIDER_LABELS else 'url'}
                                for p in missing_credentials],
        'notes': list(NOTES),
    }
    result['fingerprint'] = _digest({'version': PLAN_VERSION, 'series_id': series_id, 'steps': step_ids,
                                     'configs': configs, 'fresh': fresh,
                                     'books': [[e['book_id'], e['position'], e['fingerprint']] for e in entries]})
    return result


def _missing_inputs_message(registry, preview):
    titles = {e['book_id']: e['title'] for e in preview['books']}
    needed = {}
    for book_id, missing in preview['missing_inputs'].items():
        for step_id, inputs in missing.items():
            key = (step_id, tuple(inputs))
            needed.setdefault(key, []).append(titles[book_id])
    parts = []
    for (step_id, inputs), books in needed.items():
        labels = ' and '.join(registry.get(i).label for i in inputs)
        parts.append(f"{registry.get(step_id).label} needs accepted results from {labels} in {', '.join(books)}.")
    return ' '.join(parts) + ' Run and accept those steps for the series first, or include them in this run.'


def _record_run(store, book_ids, job):
    # Book-scoped provenance for the series run; each book's pipeline run keeps its own record.
    payload = {k: copy.deepcopy(v) for k, v in job.items() if k != 'error'}
    with store.lock, store.connect() as conn:
        for book_id in book_ids:
            record(conn, book_id, 'series_run', job['id'], payload, stage='series', label='Series processing run')


def start(runtime, registry, series_id, body: SeriesRunRequest):
    store = runtime.store
    limits = body.limits.model_dump()
    with store.lock:
        preview = plan(runtime, registry, series_id, body.steps, configs=body.configs, fresh=body.fresh)
        if not preview['books']:
            raise ValueError('Add a book to this series before processing it.')
        if not body.expected_fingerprint and all(v is None for v in limits.values()):
            raise ValueError('Preview the series run and confirm it (send expected_fingerprint), or set limits.')
        if body.expected_fingerprint and body.expected_fingerprint != preview['fingerprint']:
            raise HTTPException(409, 'The series plan changed since the preview. Review the new estimate before running.')
        if preview['missing_inputs']:
            raise ValueError(_missing_inputs_message(registry, preview))
        needed, credentials, missing = credentials_needed(runtime, registry, preview['configs'])
        if missing:
            raise ValueError(setup_message(missing))
        if any(j['status'] in ACTIVE for j in store.jobs('series:' + series_id, limit=None)):
            raise HTTPException(409, 'This series already has an active run.')
        book_ids = [b['book_id'] for b in preview['books']]
        for book_id in book_ids:
            runtime.require_idle(book_id)
        settings = step_settings(runtime, registry)
        steps = preview['steps']
        gates = {s: (body.gates or {}).get(s) or settings[s]['gate'] for s in steps}
        # Snapshot credentials and server URLs now; a later settings change must not alter queued work.
        secrets = {p: credentials[p] for p in needed}
        parent = store.create_job('series:' + series_id, 'series', len(book_ids))
        children = []
        for entry in preview['books']:
            child = store.create_job(entry['book_id'], 'pipeline')
            children.append(store.update_job(child['id'], series_run_id=parent['id'], series_id=series_id,
                                             position=entry['position'], title=entry['title'], steps=steps,
                                             plan_fingerprint=entry['fingerprint'], run_id=None,
                                             message='Waiting for earlier volumes'))
        parent = store.update_job(parent['id'], series_id=series_id, steps=steps, configs=preview['configs'], gates=gates,
                                  mode=body.mode, concurrency=body.concurrency, fresh=body.fresh, limits=limits,
                                  book_ids=book_ids, child_job_ids=[c['id'] for c in children],
                                  plan_fingerprint=preview['fingerprint'], estimated_cost_usd=preview['estimated_cost_usd'],
                                  requests=preview['requests'], message='Series queued. Books run one at a time in reading order.')
        _record_run(store, book_ids, parent)

    options = {'steps': steps, 'configs': preview['configs'], 'gates': gates, 'mode': body.mode,
               'concurrency': body.concurrency, 'fresh': body.fresh, 'limits': limits}
    try:
        runtime.series_pool.submit(coordinate, runtime, registry, parent, children, secrets, options)
    except Exception as exc:
        # Submission can fail during shutdown. Release every reservation so a
        # rejected coordinator never leaves books waiting for an absent worker.
        error = _redact(str(exc), secrets)
        with store.lock:
            store.update_job(parent['id'], status='failed', error=error, finished_at=now(),
                             message='The series worker could not start. No analysis was started.')
            _settle_waiting(store, children, 'interrupted', 'The series worker could not start. Nothing ran for this book.')
            _record_run(store, book_ids, store.job(parent['id']))
        raise ValueError('The series worker could not start. No analysis was started; try again.') from None
    return store.job(parent['id'])


def _redact(message, secrets):
    for value in secrets.values():
        if value:
            message = message.replace(value, '[redacted]')
    return message[:1200]


def _settle_waiting(store, children, status, message):
    """Children that never started end here; they are marked, never started later."""
    for child in children:
        if store.job(child['id'])['status'] == 'queued':
            store.update_job(child['id'], status=status, not_started=True, cancel_requested=status == 'cancelled',
                             message=message)


def coordinate(runtime, registry, parent, children, secrets, options):
    """The series worker: each book's pipeline run in reading order, stopping at the first that does not complete."""
    store = runtime.store
    repository = PipelineRepository(store)
    book_ids = [c['book_id'] for c in children]
    stopped = None  # (parent status, message)
    try:
        with store.lock:
            if runtime.cancelled(parent['id']):
                store.update_job(parent['id'], status='cancelled', finished_at=now(), message='Series cancelled before processing.')
                _settle_waiting(store, children, 'cancelled', 'Series cancelled before this book started.')
                return
            store.update_job(parent['id'], status='running', message='Processing supplied books in reading order')
        for index, child in enumerate(children):
            # Decide and mark the start under the store lock, so a cancel cannot slip between.
            with store.lock:
                current = store.job(child['id'])
                if runtime.cancelled(parent['id']) or current['status'] != 'queued' or current.get('cancel_requested'):
                    stopped = ('cancelled', 'Series cancelled.')
                    break
                _, preview = _book_plan(runtime, registry, child['book_id'], options['steps'], options['configs'], options['fresh'])
                if preview['fingerprint'] != child['plan_fingerprint']:
                    store.update_job(child['id'], status='failed', finished_at=now(),
                                     error='This book changed after the series preview. Nothing was sent for it.',
                                     message='Plan changed since the preview; not run.')
                    stopped = ('failed', f"{child.get('title') or 'A book'} changed after the preview. Preview the series again.")
                    break
                run = repository.create_run(child['book_id'], job_id=child['id'], steps=options['steps'], mode=options['mode'],
                                            chapter_ids=None, configs=options['configs'], gates=options['gates'],
                                            concurrency=options['concurrency'], fresh=options['fresh'], limits=options['limits'],
                                            series_run_id=parent['id'])
                # From here a cancel stops this child's run; it can no longer be skipped as "queued".
                job = store.update_job(child['id'], status='running', run_id=run['id'], message='Starting…')
                store.update_job(parent['id'], progress=index,
                                 message=f"Book {index + 1} of {len(children)}: {child.get('title') or child['book_id']}")
            execute_run(runtime, registry, job, run, secrets, limits=options['limits'],
                        concurrency=options['concurrency'], fresh=options['fresh'],
                        cancelled=lambda child_id=child['id']: runtime.cancelled(child_id) or runtime.cancelled(parent['id']))
            outcome = store.job(child['id'])['status']
            if outcome != 'completed':
                stopped = ((outcome if outcome in {'cancelled', 'interrupted', 'budget_limited', 'quota_limited'} else 'failed'),
                           f"Stopped at {child.get('title') or 'a book'} ({outcome.replace('_', ' ')}).")
                break
            final = repository.run(run['id'])
            waiting = [registry.get(s).label for s, o in (final.get('outcomes') or {}).items()
                       if o.get('status') == 'completed' and o.get('scopes') and not o.get('accepted')]
            store.update_job(child['id'], message='Waiting for your review: ' + ', '.join(waiting) if waiting
                             else 'Done; results are in use.')
            store.update_job(parent['id'], progress=index + 1)
        with store.lock:
            if stopped is None and runtime.cancelled(parent['id']):
                stopped = ('cancelled', 'Series cancelled.')
            if runtime.stopping.is_set() and stopped:
                stopped = ('interrupted', 'The server stopped during the series run.')
            status, headline = stopped or ('completed', 'Series complete.')
            if status == 'cancelled':
                _settle_waiting(store, children, 'cancelled', 'Series cancelled before this book started.')
            else:
                _settle_waiting(store, children, 'interrupted',
                                'Not started: the series stopped at an earlier book. Validated work is kept and reused.')
            done = sum(store.job(c['id'])['status'] == 'completed' for c in children)
            store.update_job(parent['id'], status=status, progress=done, finished_at=now(),
                             message=f'{headline} {done}/{len(children)} books completed; their results are kept.')
    except Exception as exc:
        with store.lock:
            store.update_job(parent['id'], status='failed', error=_redact(str(exc) or type(exc).__name__, secrets),
                             finished_at=now(), message='Series stopped on an error; completed books keep their results.')
            _settle_waiting(store, children, 'interrupted', 'Not started: the series stopped on an error.')
    finally:
        _record_run(store, book_ids, store.job(parent['id']))


def runs(runtime, series_id, limit=20):
    """Recent series runs with each child job and its pipeline run's step outcomes."""
    store = runtime.store
    series_view(store, series_id)
    repository = PipelineRepository(store)
    result = []
    for parent in store.jobs('series:' + series_id, limit=limit):
        children = []
        for identifier in parent.get('child_job_ids', []):
            child = store.job(identifier)
            if child.get('run_id'):
                try:
                    run = repository.run(child['run_id'])
                    child['run'] = {k: run.get(k) for k in ('id', 'status', 'outcomes', 'error')}
                except KeyError:
                    child['run'] = None
            children.append(child)
        result.append({**parent, 'children': children})
    return {'runs': result}
