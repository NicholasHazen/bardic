"""Bounded parallel discovery followed by ordered series interpretation."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import copy

from .artifacts import record
from .processing import BudgetReached, digest, source_hash
from .series import SeriesRepository
from .store import now
from .errors import Conflict, Invalid, Unavailable
from .series import SERIES_ARCHIVED


def series_view(store, series_id, *, active=False):
    """The series entry, removed or not (404 when unknown). ``active`` refuses a removed one (409)."""
    series = SeriesRepository(store).series(series_id)
    if active and series['archived']:
        raise Conflict('series_archived', SERIES_ARCHIVED)
    return series


def plan(runtime, series_id, *, provider=None, phase='scan', concurrency=2, limits=None):
    from .progressive import plan as book_plan
    if phase not in {'scan', 'profiles', 'direct', 'full'}:
        raise Invalid('phase_invalid', 'The phase must be scan, profiles, direct or full.')
    if type(concurrency) is not int or not 1 <= concurrency <= 2:
        raise Invalid('concurrency_invalid', 'Series discovery concurrency must be 1 or 2.')
    provider = provider or runtime.preferences['analysis_provider']
    if provider not in {'gemini', 'openai', 'anthropic'}:
        raise Invalid('provider_not_cloud', 'Series processing needs a cloud analysis provider: gemini, openai or anthropic.')
    series = series_view(runtime.store, series_id, active=True)
    books = sorted(series['books'], key=lambda b: (b['position'], b['book_id']))
    books = [b for b in books if not runtime.store.is_archived(b['book_id'])]
    model = runtime.preferences['analysis_models_by_provider'][provider]
    scan_model = runtime.preferences['preprocess_models_by_provider'][provider]
    entries, inputs = [], []
    for item in books:
        book = runtime.store.book(item['book_id'])
        preview = book_plan(book, runtime.store, provider, model, scan_model, phase=phase)
        entries.append({'book_id': book['id'], 'title': book['title'], 'position': item['position'], 'plan': preview})
        inputs.append({'book_id': book['id'], 'revision': book.get('revision'), 'source_hash': source_hash(book),
                       'series_context': SeriesRepository(runtime.store).context_for_book(book['id'])['fingerprint']})
    estimates = [b['plan']['estimated_cost_usd'] for b in entries]
    result = {'series_id': series_id, 'name': series['name'], 'provider': provider, 'model': model, 'scan_model': scan_model,
            'phase': phase, 'concurrency': concurrency if phase in {'scan', 'full'} else 1, 'books': entries,
            'volumes': series.get('volumes', []), 'limits_per_book': limits,
            'requests': sum(b['plan']['requests'] for b in entries),
            'estimated_cost_usd': sum(estimates) if all(v is not None for v in estimates) else None,
            'notes': ['Discovery can run on independent books concurrently; profiles and direction run in reading order.',
                      'Only supplied, active books are processed. Missing and planned volumes do not block the run.',
                      'Cross-book evidence uses confirmed character links only. Review new identities after discovery.',
                      'A full run estimate excludes newly discovered work and retries. Limits apply to each book; total possible spend scales with the selected collection.',
                      'The available library may be incomplete; no run establishes knowledge of absent volumes.']}
    result['plan_fingerprint'] = digest({'version': 1, 'plan': result, 'book_inputs': inputs})
    return result


def _record_run(store, series_id, book_ids, job):
    # A series run is reusable book-scoped provenance, not a replacement for
    # the per-book artifacts produced by its children.
    payload = {k: copy.deepcopy(v) for k, v in job.items() if k not in {'error'}}
    with store.lock, store.connect() as conn:
        for book_id in book_ids:
            record(conn, book_id, 'series_run', job['id'], payload, stage='series', label='Series processing run')


def start(runtime, series_id, *, provider=None, phase='scan', concurrency=2, limits=None, expected_plan_fingerprint=None):
    with runtime.store.lock:
        preview = plan(runtime, series_id, provider=provider, phase=phase, concurrency=concurrency, limits=limits)
        if expected_plan_fingerprint is not None and expected_plan_fingerprint != preview['plan_fingerprint']:
            raise Conflict('plan_stale', 'The series or processing plan changed since the preview. No processing was queued.')
        if not preview['books']:
            raise Invalid('series_empty', 'The series has no supplied, active book to process.')
        if not runtime.api_keys.get(preview['provider']):
            raise Invalid('api_key_missing', f"No API key is configured for the {preview['provider']} analysis provider.")
        if any(j['status'] in {'queued', 'running'} for j in runtime.store.jobs('series:' + series_id)):
            raise Conflict('series_run_active', 'This series already has an active processing run.')
        book_ids = [b['book_id'] for b in preview['books']]
        for book_id in book_ids:
            runtime.require_idle(book_id)
        parent = runtime.store.create_job('series:' + series_id, 'series', len(book_ids))
        children = []
        for entry in preview['books']:
            child = runtime.store.create_job(entry['book_id'], 'analyze')
            child = runtime.store.update_job(child['id'], series_run_id=parent['id'], series_id=series_id,
                position=entry['position'], provider=preview['provider'], model=preview['model'], scan_model=preview['scan_model'],
                phase=phase, message='Waiting for series processing')
            children.append(child)
        parent = runtime.store.update_job(parent['id'], series_id=series_id, phase=phase, provider=preview['provider'],
            model=preview['model'], scan_model=preview['scan_model'], concurrency=preview['concurrency'],
            child_job_ids=[j['id'] for j in children], book_ids=book_ids, limits=limits, plan_fingerprint=preview['plan_fingerprint'],
            message='Series queued; missing volumes will be skipped')
        key = runtime.api_keys[preview['provider']]
        _record_run(runtime.store, series_id, book_ids, parent)

    def cancelled(child=None):
        return runtime.cancelled(parent['id']) or bool(child and runtime.cancelled(child['id']))

    def phase_work(child, selected_phase):
        from .analysis import analyze_book
        if cancelled(child):
            raise InterruptedError('Series processing stopped')
        runtime.store.update_job(child['id'], status='running', phase=selected_phase, message=f'Series: {selected_phase}')
        book = runtime.store.book(child['book_id'])
        def progress(done, total, message):
            if cancelled(child):
                raise InterruptedError('Series processing stopped')
            runtime.store.update_job(child['id'], progress=done, total=total, message=message)
        def prepare(snapshot):
            runtime.assign_local_voices(snapshot)
            cast = runtime.resolved_cast(snapshot)
            for segment in snapshot['segments']:
                if segment.get('audio') and not runtime.valid_audio(snapshot, segment, cast):
                    segment['audio'] = None
        result = analyze_book(book, preview['provider'], key, preview['model'], progress, lambda: cancelled(child),
            store=runtime.store, resume=True, prepare=prepare, phase=selected_phase,
            scan_model=preview['scan_model'], limits=limits, run_id=child['id'])
        prepare(result)
        result['revision'] = book.get('revision', 0) + 1
        runtime.store.save_book(result)
        return result

    def attempt(child, selected_phase):
        try:
            phase_work(child, selected_phase)
            return True
        except Exception as exc:
            error = str(exc)
            if key:
                error = error.replace(key, '[redacted]')
            status = ('budget_limited' if isinstance(exc, BudgetReached) else
                      'cancelled' if isinstance(exc, InterruptedError) else 'failed')
            runtime.store.update_job(child['id'], status=status, error=error[:1200], message='Saved work retained; series stopped')
            return False

    def work():
        stopped = False
        try:
            with runtime.store.lock:
                if cancelled():
                    runtime.store.update_job(parent['id'], status='cancelled', message='Series cancelled before processing.')
                    for child in children:
                        if runtime.store.job(child['id'])['status'] in {'queued', 'running'}:
                            runtime.store.update_job(child['id'], status='cancelled', cancel_requested=True,
                                message='Series cancelled before this book started.')
                    return
                runtime.store.update_job(parent['id'], status='running', message='Scanning supplied books')
            if phase in {'scan', 'full'}:
                pending = iter(children)
                with ThreadPoolExecutor(max_workers=preview['concurrency'], thread_name_prefix='series-discovery') as workers:
                    in_flight = {}
                    for _ in range(preview['concurrency']):
                        child = next(pending, None)
                        if child:
                            in_flight[workers.submit(attempt, child, 'scan')] = child
                    while in_flight:
                        done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                        for future in done:
                            child = in_flight.pop(future)
                            if not future.result():
                                stopped = True
                            else:
                                runtime.store.update_job(child['id'], status='completed' if phase == 'scan' else 'running',
                                    message='Discovery saved' if phase == 'scan' else 'Discovery saved; waiting for earlier volumes')
                        # Observe every finished result before queuing more work;
                        # one simultaneous failure must stop all replacements.
                        if not stopped and not cancelled():
                            for _ in done:
                                next_child = next(pending, None)
                                if next_child:
                                    in_flight[workers.submit(attempt, next_child, 'scan')] = next_child
            if not stopped and not cancelled() and phase != 'scan':
                for index, child in enumerate(children):
                    runtime.store.update_job(parent['id'], progress=index, message=f'Interpreting volume {index+1} of {len(children)} in reading order')
                    if cancelled() or not attempt(child, 'full' if phase == 'full' else phase):
                        stopped = True
                        break
                    runtime.store.update_job(child['id'], status='completed', message='Series stage saved')
            statuses = [runtime.store.job(c['id']) for c in children]
            successful = sum(c['status'] == 'completed' for c in statuses)
            status = 'cancelled' if cancelled() else 'failed' if stopped else 'completed'
            for child in statuses:
                if child['status'] in {'queued', 'running'}:
                    runtime.store.update_job(child['id'], status='cancelled' if cancelled() else 'interrupted',
                        message='Waiting work stopped. Validated results are retained; resume the series to continue.')
            runtime.store.update_job(parent['id'], status=status, progress=successful,
                message=f'{successful}/{len(children)} supplied books completed. Saved results retained.', finished_at=now())
        except Exception as exc:
            error = str(exc).replace(key, '[redacted]')[:1200] if key else str(exc)[:1200]
            runtime.store.update_job(parent['id'], status='failed', error=error, message='Series stopped; saved results retained.')
            for child in children:
                if runtime.store.job(child['id'])['status'] in {'queued', 'running'}:
                    runtime.store.update_job(child['id'], status='interrupted', message='Series stopped; resume from saved work.')
        finally:
            _record_run(runtime.store, series_id, book_ids, runtime.store.job(parent['id']))

    try:
        runtime.series_pool.submit(work)
    except Exception as exc:
        # Submission can fail during shutdown. Release every reservation so a
        # rejected coordinator never leaves books waiting for an absent worker.
        error = str(exc).replace(key, '[redacted]')[:1200] if key else str(exc)[:1200]
        with runtime.store.lock:
            runtime.store.update_job(parent['id'], status='failed', error=error, finished_at=now(),
                                     message='Series worker could not start. No analysis was started; saved work is retained.')
            for child in children:
                if runtime.store.job(child['id'])['status'] in {'queued', 'running'}:
                    runtime.store.update_job(child['id'], status='interrupted',
                                             message='Series worker could not start. Resume to try again.')
            _record_run(runtime.store, series_id, book_ids, runtime.store.job(parent['id']))
        raise Unavailable('shutting_down', 'The series worker is not accepting work (the server is shutting down). '
                                           'No analysis was started.') from None
    return runtime.store.job(parent['id'])
