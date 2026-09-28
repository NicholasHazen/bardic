"""Series runs on the step pipeline: one book's pipeline run per supplied volume, in reading order.

A series plan is the ordered list of each active book's step-pipeline plan for
the same steps and configuration, an aggregate estimate (an unknown price stays
unknown, never zero) and one fingerprint over every book's **consent**
fingerprint. Confirming that fingerprint authorizes the run; optional limits
cap each book.

Series memory. A step that reads series context (profiles) reads earlier
volumes' **accepted** evidence through confirmed links. In a later book whose
linked characters have an earlier volume in the same run, that step is
**context-pending**: what its prompts say depends on what the earlier books
accept during the run, but which units exist does not. The book's consent
fingerprint therefore covers its unit set, provider, model, ``fresh`` and step
version, and the cache keys of the other steps, but not the context-pending
prompts. Its estimate is "up to": every context-pending unit counts as a
request (a saved result may no longer match), and tokens are estimated from
the current context, which can grow.

A series run is a parent job (kind ``series``) that reserves every book and
runs one child job (kind ``pipeline``) per book, one at a time. Before a child
starts, the coordinator re-plans that book and refuses it when its consent
fingerprint changed. A child cancelled while queued never starts. Each HTTP
attempt is still reserved and recorded by the pipeline runner.

Review gates. A later book must never read unreviewed candidates. When a book
finishes with results waiting for review and a later book in the run reads
it, the series pauses: the parent stays ``running`` with
``waiting_for_review``, and no worker runs. While paused only the waiting book
accepts version decisions (the reservation otherwise holds); the owner
reviews it in its Analysis tab, then resumes (:func:`resume`) or cancels.
Resume refuses while anything is still waiting, and never starts a cancelled
child. A server restart interrupts a paused run like any active job.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Literal

from pydantic import Field

from .artifacts import record
from .pipeline import projection
from .pipeline.api import (LABELS, PROVIDER_LABELS, Limits, StepConfig, Strict, _versions_view, credentials_needed,
                           execute_run, missing_credentials_error, resolve_configs, step_settings)
from .pipeline.repository import ACTIVE, PipelineRepository
from .pipeline.runner import plan as book_plan
from .series import EVIDENCE_STEPS, SeriesRepository, evidence_inputs
from .store import now, public_job
from .errors import Conflict, Invalid, NotFound, Unavailable
from .series import SERIES_ARCHIVED

PLAN_VERSION = 3
CONSENT_VERSION = 1
NOTES = (
    'Books run one at a time in reading order. Missing and planned volumes are skipped; nothing is inferred about them.',
    'Character profiles in a later book read the accepted results of earlier books, through confirmed character links '
    'only. A book with a linked earlier book in this run is estimated "up to": every profile counts as a request, and '
    'its prompts can grow as earlier books accept new evidence, so its cost can be higher than estimated.',
    'When a book waits for your review and a later book reads it, the series pauses until you review that book and '
    'resume. A later book never reads results you have not accepted.',
    'Estimates cover currently known work before retries or evidence repairs. Work in a step that reads another step '
    'in the same run is only known once that step finishes. Provider invoices are authoritative.',
    'Before each book starts, its plan is checked again (steps, providers, models, the work planned and, except for '
    'context from earlier books, what is sent). If it changed, that book and the rest of the series are not run.',
)
ESTIMATE_KEYS = ('requests', 'estimated_input_tokens', 'output_token_allowance')


class SeriesPlanRequest(Strict):
    steps: list[str] = Field(min_length=1, max_length=40)
    configs: dict[str, StepConfig] | None = None
    # Request new samples in every book even when an identical validated unit is cached.
    fresh: bool = False


class SeriesRunRequest(SeriesPlanRequest):
    scheduling: Literal['serial', 'parallel'] = 'serial'
    gates: dict[str, Literal['auto', 'review']] | None = None
    # Model requests in flight inside the running book. Books run one at a time.
    concurrency: int = Field(default=2, ge=1, le=4)
    # Optional caps for API callers, applied to each book's run.
    limits: Limits = Field(default_factory=Limits)
    expected_fingerprint: str | None = Field(default=None, max_length=64)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def series_view(store, series_id, *, active=False):
    """The series entry, removed or not (404 when unknown). ``active`` refuses a removed one (409)."""
    series = SeriesRepository(store).series(series_id)
    if active and series['archived']:
        raise Conflict('series_archived', SERIES_ARCHIVED)
    return series


def _steps(registry, step_ids, configs=None, gates=None):
    """The requested steps and their inputs; 400 ``unknown_step`` when ``steps``, ``configs`` or ``gates`` names an unknown one."""
    unknown = sorted({s for s in [*step_ids, *(configs or {}), *(gates or {})] if s not in registry})
    if unknown:
        raise Invalid('unknown_step', f'Unknown pipeline step: {", ".join(unknown)}.')
    return registry.closure(step_ids)


def _book_plan(runtime, registry, book_id, step_ids, configs, fresh, *, consent=False):
    """One book's pipeline plan, after recording outside changes (as the book plan endpoint does)."""
    store = runtime.store
    with store.lock:
        book = store.book(book_id)
        with store.connect() as conn:
            projection.sync(PipelineRepository(store), registry, conn, book)
        return book, book_plan(store, registry, book_id, step_ids, configs, fresh=fresh, consent=consent)


def context_sources(store, book_id, run_book_ids):
    """Earlier books of this run whose accepted evidence the book's linked characters read."""
    with store.lock, store.connect() as conn:
        sources = set().union(*evidence_inputs(conn, book_id).values()) if book_id else set()
    return sorted(sources & set(run_book_ids))


def consent_fingerprint(book_id, position, preview, pending, fresh):
    """What a series run's consent covers for one book.

    Every step: its ID, version, provider, model and unit locators (the unit set).
    Steps not context-pending also contribute their cache keys (what is sent), as a
    book plan fingerprint does. Context-pending steps do not: their prompts carry
    earlier books' accepted evidence, which the run itself may change.
    """
    parts = [[step_id, version, provider, model, sorted(locators) if step_id in pending else keys]
             for step_id, version, provider, model, keys, locators in preview['consent_parts']]
    return _digest({'version': CONSENT_VERSION, 'book_id': book_id, 'position': position,
                    'revision': preview['revision'], 'fresh': fresh, 'steps': parts})


def _up_to(preview, upper, pending):
    """The book estimate with context-pending steps counted as all-new work (their fresh plan)."""
    figures = {key: preview[key] for key in ESTIMATE_KEYS}
    cost = preview['estimated_cost_usd']
    for item in preview['steps']:
        if item['step_id'] not in pending:
            continue
        fresh_item = next(s for s in upper['steps'] if s['step_id'] == item['step_id'])
        for key in ESTIMATE_KEYS:
            figures[key] += fresh_item[key] - item[key]
        if cost is not None:
            cost = None if fresh_item['estimated_cost_usd'] is None or item['estimated_cost_usd'] is None \
                else cost - item['estimated_cost_usd'] + fresh_item['estimated_cost_usd']
    return {**figures, 'estimated_cost_usd': None if cost is None else round(cost, 6)}


def plan(runtime, registry, series_id, steps, *, configs=None, fresh=False, gates=None):
    """Read-only (apart from free census caches, outside-change capture and context provenance) series preview.

    404 for an unknown series, 409 ``series_archived`` for a removed one.
    """
    series_view(runtime.store, series_id, active=True)
    selected = _steps(registry, steps, configs, gates)
    step_ids = [s.id for s in selected]
    reads_context = [s.id for s in selected if s.reads_series_context]
    configs = resolve_configs(runtime, registry, selected, configs)
    with runtime.store.lock:
        series = series_view(runtime.store, series_id, active=True)
        members = sorted(series['books'], key=lambda b: (b['position'], b['book_id']))
        run_book_ids = [item['book_id'] for item in members]
        entries = []
        for item in members:
            book, preview = _book_plan(runtime, registry, item['book_id'], step_ids, configs, fresh, consent=True)
            sources = context_sources(runtime.store, book['id'], run_book_ids) if reads_context else []
            pending = reads_context if sources else []
            upper = book_plan(runtime.store, registry, book['id'], pending, configs, fresh=True) if pending else None
            entry = {'book_id': book['id'], 'title': book.get('title', 'Untitled'), 'position': item['position'],
                     'fingerprint': preview['fingerprint'],
                     'consent_fingerprint': consent_fingerprint(book['id'], item['position'], preview, pending, fresh),
                     'context_pending': pending, 'context_sources': sources,
                     'up_to': _up_to(preview, upper, pending) if pending else
                     {**{key: preview[key] for key in ESTIMATE_KEYS}, 'estimated_cost_usd': preview['estimated_cost_usd']}}
            preview.pop('consent_parts')
            preview.pop('revision')
            entries.append({**entry, 'plan': preview})
    _, _, missing_credentials = credentials_needed(runtime, registry, configs)
    costs = [e['plan']['estimated_cost_usd'] for e in entries]
    known = [c for c in costs if c is not None]
    upper_costs = [e['up_to']['estimated_cost_usd'] for e in entries]
    upper_known = [c for c in upper_costs if c is not None]
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
        'context_pending_books': [e['book_id'] for e in entries if e['context_pending']],
        'up_to': {**{key: sum(e['up_to'][key] for e in entries) for key in ESTIMATE_KEYS},
                  'estimated_cost_usd': round(sum(upper_known), 6) if entries and len(upper_known) == len(upper_costs)
                  else None if entries else 0.0,
                  'known_cost_usd': round(sum(upper_known), 6)},
        'missing_inputs': {e['book_id']: e['plan']['missing_inputs'] for e in entries if e['plan']['missing_inputs']},
        'missing_credentials': [{'provider': p, 'label': LABELS.get(p, p), 'needs': 'api_key' if p in PROVIDER_LABELS else 'url'}
                                for p in missing_credentials],
        'notes': list(NOTES),
    }
    result['fingerprint'] = _digest({'version': PLAN_VERSION, 'series_id': series_id, 'steps': step_ids,
                                     'configs': configs, 'fresh': fresh,
                                     'books': [[e['book_id'], e['position'], e['consent_fingerprint']] for e in entries]})
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
    return ' '.join(parts) + ' Those steps have no accepted results in these books, and this run does not include them.'


def _record_run(store, book_ids, job):
    # Book-scoped provenance for the series run; each book's pipeline run keeps its own record.
    payload = {k: copy.deepcopy(v) for k, v in job.items() if k != 'error'}
    with store.lock, store.connect() as conn:
        for book_id in book_ids:
            record(conn, book_id, 'series_run', job['id'], payload, stage='series', label='Series processing run')


def _paused(runtime):
    """In-memory state of paused runs: {parent job ID: (children, secrets, options, next index)}.

    Credentials stay in memory only, as for a queued run; a restart interrupts the run.
    """
    with runtime.store.lock:
        if not hasattr(runtime, 'series_paused'):
            runtime.series_paused = {}
        return runtime.series_paused


def start(runtime, registry, series_id, body: SeriesRunRequest):
    store = runtime.store
    limits = body.limits.model_dump()
    with store.lock:
        if runtime.stopping.is_set():
            raise Unavailable('shutting_down', 'The series worker is not accepting work (the server is shutting down).')
        preview = plan(runtime, registry, series_id, body.steps, configs=body.configs, fresh=body.fresh, gates=body.gates)
        if not preview['books']:
            raise Invalid('series_empty', 'The series has no supplied, active book to process.')
        if not body.expected_fingerprint and all(v is None for v in limits.values()):
            raise Invalid('run_unconfirmed', 'The series run is not authorized: send `expected_fingerprint` from a plan, '
                                             'or set a limit.')
        if body.expected_fingerprint and body.expected_fingerprint != preview['fingerprint']:
            raise Conflict('plan_stale', 'The series plan changed since the preview. Nothing was queued.')
        if preview['missing_inputs']:
            raise Invalid('step_inputs_missing', _missing_inputs_message(registry, preview))
        needed, credentials, missing = credentials_needed(runtime, registry, preview['configs'])
        if missing:
            raise missing_credentials_error(missing)
        if any(j['status'] in ACTIVE for j in store.jobs('series:' + series_id, limit=None)):
            raise Conflict('series_run_active', 'This series already has an active processing run.')
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
                                             plan_fingerprint=entry['fingerprint'],
                                             consent_fingerprint=entry['consent_fingerprint'],
                                             context_pending=entry['context_pending'],
                                             context_sources=entry['context_sources'], run_id=None,
                                             message='Waiting for earlier volumes'))
        parent = store.update_job(parent['id'], series_id=series_id, steps=steps, configs=preview['configs'], gates=gates,
                                  scheduling=body.scheduling, concurrency=body.concurrency, fresh=body.fresh,
                                  analysis_limits=limits,
                                  book_ids=book_ids, child_job_ids=[c['id'] for c in children],
                                  plan_fingerprint=preview['fingerprint'], estimated_cost_usd=preview['estimated_cost_usd'],
                                  requests=preview['requests'], context_pending_books=preview['context_pending_books'],
                                  message='Series queued. Books run one at a time in reading order.')
        _record_run(store, book_ids, parent)

    options = {'steps': steps, 'configs': preview['configs'], 'gates': gates, 'scheduling': body.scheduling,
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
        raise Unavailable('shutting_down', 'The series worker is not accepting work (the server is shutting down). '
                                           'No analysis was started.') from None
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


def waiting_steps(runtime, registry, book_id, run_id):
    """Step IDs of a pipeline run whose version still waits for a decision (a candidate)."""
    repository = PipelineRepository(runtime.store)
    try:
        run = repository.run(run_id)
    except KeyError:
        return []
    waiting = []
    with runtime.store.lock, runtime.store.connect() as conn:
        for step_run_id in run.get('step_run_ids') or []:
            try:
                step_run = repository.step_run(book_id, step_run_id)
            except KeyError:
                continue
            step = registry.get(step_run['step_id'])
            view = _versions_view(repository, registry, conn, book_id, step, [step_run])
            if view and view[0]['state'] == 'candidate' and step.id not in waiting:
                waiting.append(step.id)
    return waiting


def _pause_after(store, children, index, waiting):
    """Pause when a waiting step is evidence later books read, and a later, still queued book reads this one."""
    if not set(waiting) & set(EVIDENCE_STEPS):
        return False  # e.g. a waiting census or quote check: nothing later volumes read
    book_id = children[index]['book_id']
    return any(book_id in (child.get('context_sources') or []) and store.job(child['id'])['status'] == 'queued'
               for child in children[index + 1:])


def coordinate(runtime, registry, parent, children, secrets, options, start_index=0):
    """The series worker: each book's pipeline run in reading order, stopping at the first that does not complete.

    Returns early, without settling, when the series pauses for review.
    """
    store = runtime.store
    repository = PipelineRepository(store)
    book_ids = [c['book_id'] for c in children]
    stopped = None  # (parent status, message)
    paused = False
    try:
        with store.lock:
            if runtime.cancelled(parent['id']):
                store.update_job(parent['id'], status='cancelled', finished_at=now(),
                                 message='Series cancelled before processing.' if not start_index else 'Series cancelled.')
                _settle_waiting(store, children, 'cancelled', 'Series cancelled before this book started.')
                return
            store.update_job(parent['id'], status='running', message='Processing supplied books in reading order')
        for index in range(start_index, len(children)):
            child = children[index]
            # Decide and mark the start under the store lock, so a cancel cannot slip between.
            with store.lock:
                current = store.job(child['id'])
                if runtime.cancelled(parent['id']) or current['status'] != 'queued' or current.get('cancel_requested'):
                    stopped = ('cancelled', 'Series cancelled.')
                    break
                pending = child.get('context_pending') or []
                _, preview = _book_plan(runtime, registry, child['book_id'], options['steps'], options['configs'],
                                        options['fresh'], consent=True)
                expected = child.get('consent_fingerprint')
                actual = consent_fingerprint(child['book_id'], child['position'], preview, pending, options['fresh'])
                if expected is None:  # a run queued before consent fingerprints: compare the whole plan
                    expected, actual = child['plan_fingerprint'], preview['fingerprint']
                if actual != expected:
                    store.update_job(child['id'], status='failed', finished_at=now(),
                                     error='This book changed after the series preview. Nothing was sent for it.',
                                     message='Plan changed since the preview; not run.')
                    stopped = ('failed', f"{child.get('title') or 'A book'} changed after the preview. Preview the series again.")
                    break
                run = repository.create_run(child['book_id'], job_id=child['id'], steps=options['steps'],
                                            scheduling=options['scheduling'],
                                            chapter_ids=None, configs=options['configs'], gates=options['gates'],
                                            concurrency=options['concurrency'], fresh=options['fresh'], limits=options['limits'],
                                            series_run_id=parent['id'])
                # From here a cancel stops this child's run; it can no longer be skipped as "queued".
                job = store.update_job(child['id'], status='running', run_id=run['id'], message='Starting…')
                store.update_job(parent['id'], progress=index,
                                 message=f"Book {index + 1} of {len(children)}: {child.get('title') or child['book_id']}")
            def outcome_message(book_id=child['book_id'], run_id=run['id']):
                # A terminal message is final, so the review state is written with the completion.
                labels = [registry.get(s).label for s in waiting_steps(runtime, registry, book_id, run_id)]
                return 'Waiting for your review: ' + ', '.join(labels) if labels else 'Done; results are in use.'

            execute_run(runtime, registry, job, run, secrets, limits=options['limits'],
                        concurrency=options['concurrency'], fresh=options['fresh'],
                        cancelled=lambda child_id=child['id']: runtime.cancelled(child_id) or runtime.cancelled(parent['id']),
                        completed_message=outcome_message)
            outcome = store.job(child['id'])['status']
            if outcome != 'completed':
                stopped = ((outcome if outcome in {'cancelled', 'interrupted', 'budget_limited', 'quota_limited'} else 'failed'),
                           f"Stopped at {child.get('title') or 'a book'} ({outcome.replace('_', ' ')}).")
                break
            waiting = waiting_steps(runtime, registry, child['book_id'], run['id'])
            store.update_job(parent['id'], progress=index + 1)
            if waiting and _pause_after(store, children, index, waiting):
                with store.lock:
                    if runtime.cancelled(parent['id']):
                        stopped = ('cancelled', 'Series cancelled.')
                        break
                    title = child.get('title') or 'this book'
                    store.update_job(parent['id'], message=f'Waiting for your review of {title}.',
                                     waiting_for_review={'book_id': child['book_id'], 'child_job_id': child['id'],
                                                         'title': child.get('title') or '', 'position': child['position'],
                                                         'steps': waiting, 'since': now()})
                    for later in children[index + 1:]:
                        if store.job(later['id'])['status'] == 'queued':
                            store.update_job(later['id'], message=f'Waiting for your review of {title}.')
                    _paused(runtime)[parent['id']] = (children, secrets, options, index + 1)
                    paused = True
                return
        with store.lock:
            if stopped is None and runtime.cancelled(parent['id']):
                stopped = ('cancelled', 'Series cancelled.')
            if runtime.stopping.is_set() and stopped:
                stopped = ('interrupted', 'The server stopped during the series run.')
            _settle(runtime, parent['id'], children, *(stopped or ('completed', 'Series complete.')))
    except Exception as exc:
        paused = False
        with store.lock:
            _paused(runtime).pop(parent['id'], None)
            store.update_job(parent['id'], status='failed', error=_redact(str(exc) or type(exc).__name__, secrets),
                             waiting_for_review=None, finished_at=now(),
                             message='Series stopped on an error; completed books keep their results.')
            _settle_waiting(store, children, 'interrupted', 'Not started: the series stopped on an error.')
    finally:
        _record_run(store, book_ids, store.job(parent['id']))
        if paused and runtime.stopping.is_set():
            # Shutting down while pausing: nothing will resume this run.
            with store.lock:
                if _paused(runtime).pop(parent['id'], None):
                    _settle(runtime, parent['id'], children, 'interrupted', 'The server stopped during the series run.')


def _settle(runtime, parent_id, children, status, headline):
    """End a series run: unstarted children are marked, never started later. Caller holds the store lock."""
    store = runtime.store
    if status == 'cancelled':
        _settle_waiting(store, children, 'cancelled', 'Series cancelled before this book started.')
    else:
        _settle_waiting(store, children, 'interrupted',
                        'Not started: the series stopped at an earlier book. Validated work is kept and reused.')
    done = sum(store.job(c['id'])['status'] == 'completed' for c in children)
    store.update_job(parent_id, status=status, progress=done, finished_at=now(), waiting_for_review=None,
                     message=f'{headline} {done}/{len(children)} books completed; their results are kept.')


def _parent(runtime, series_id, job_id):
    series_view(runtime.store, series_id)
    try:
        job = runtime.store.job(job_id)
    except NotFound:
        job = None
    if job is None or job.get('kind') != 'series' or job.get('series_id') != series_id:
        raise NotFound('series_run_not_found', 'This series has no run with this ID.')
    return job


def resume(runtime, registry, series_id, job_id):
    """Continue a series paused for review, once the waiting book has nothing left to review."""
    store = runtime.store
    with store.lock:
        parent = _parent(runtime, series_id, job_id)
        wait = parent.get('waiting_for_review')
        if parent['status'] not in ACTIVE or not wait:
            # Includes a run the server restart interrupted: startup ends it and clears the pause.
            raise Conflict('series_run_not_waiting', 'This series run is not waiting for review.')
        if parent.get('cancel_requested'):
            raise Conflict('series_run_not_waiting', 'This series run was cancelled.')
        state = _paused(runtime).get(job_id)
        if state is None:
            raise Conflict('series_run_not_resumable', 'This series run has no paused worker state left to resume. '
                                                       'Start the series again; saved results are reused.')
        child = store.job(wait['child_job_id'])
        waiting = waiting_steps(runtime, registry, wait['book_id'], child.get('run_id')) if child.get('run_id') else []
        if waiting:
            labels = ', '.join(registry.get(s).label for s in waiting)
            raise Conflict('review_pending', f"{wait.get('title') or 'The book'} still has results waiting for review "
                                             f'({labels}). Accept them or set them aside first.')
        children, secrets, options, index = _paused(runtime).pop(job_id)
        parent = store.update_job(job_id, waiting_for_review=None, message='Resuming after your review.')
        for later in children[index:]:
            if store.job(later['id'])['status'] == 'queued':
                store.update_job(later['id'], message='Waiting for earlier volumes')
        try:
            runtime.series_pool.submit(coordinate, runtime, registry, parent, children, secrets, options, index)
        except Exception as exc:
            store.update_job(job_id, status='failed', error=_redact(str(exc), secrets), finished_at=now(),
                             message='The series worker could not resume. Nothing more was started.')
            _settle_waiting(store, children, 'interrupted', 'The series worker could not resume. Nothing ran for this book.')
            _record_run(store, [c['book_id'] for c in children], store.job(job_id))
            raise Unavailable('shutting_down', 'The series worker is not accepting work (the server is shutting down). '
                                               'Nothing more was started.') from None
        return store.job(job_id)


def cancel_paused(runtime, job):
    """Settle a series cancelled while paused for review: no worker is running to do it.

    The cancel route has already cancelled the queued children. Caller holds the store lock.
    """
    store = runtime.store
    state = _paused(runtime).pop(job['id'], None)
    if state:
        children = state[0]
    else:
        children = []
        for identifier in job.get('child_job_ids', []):
            try:
                children.append(store.job(identifier))
            except NotFound:
                continue  # A dangling child job ID in stored data: skip it, as runs() and cancelJob do.
    for child in children:
        current = store.job(child['id'])
        if current['status'] == 'cancelled' and not current.get('run_id') and not current.get('not_started'):
            store.update_job(child['id'], not_started=True)
    _settle(runtime, job['id'], children, 'cancelled', 'Series cancelled while waiting for your review.')
    _record_run(runtime.store, job.get('book_ids', []), runtime.store.job(job['id']))
    return runtime.store.job(job['id'])


def runs(runtime, series_id, limit=20):
    """Recent series runs with each child job and its pipeline run's step outcomes."""
    store = runtime.store
    series_view(store, series_id)
    repository = PipelineRepository(store)
    result = []
    for parent in store.jobs('series:' + series_id, limit=limit):
        children = []
        for identifier in parent.get('child_job_ids', []):
            try:
                child = public_job(store.job(identifier))
            except NotFound:
                continue  # A dangling child job ID in stored data: skip it, as cancelJob does.
            if child.get('run_id'):
                try:
                    run = repository.run(child['run_id'])
                    child['run'] = {k: run.get(k) for k in ('id', 'status', 'outcomes', 'error')}
                except KeyError:
                    child['run'] = None
            children.append(child)
        result.append({**public_job(parent), 'children': children})
    return {'runs': result}
