"""The detailed status of a saved performance: per-chapter progress and an estimate of when it will be finished.

Everything here is computed from retained state (the job, the ready audio, the override log); nothing contacts a
provider. The estimate says how it was made:

- ``measured``: characters recorded so far in the current run divided by the run's elapsed time, applied to what is
  left (only once the run has produced something and has run for a few seconds);
- ``estimated``: for a Gemini one-narrator performance, before anything was measured, from the speech rate and
  generation speed learned by earlier chunk jobs, the concurrency and the per-minute limit;
- ``unknown``: no speed is known yet. It is never guessed.

Unknown cost or speed is not zero; a daily-quota pause is reported as a pause with its resume time, not folded into a
duration.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .errors import NotFound
from .performances import (ACTIVE, PROVIDER_LABELS, chunk_options, plan, previous_calibration, progress,
                           ready_audio, refused_passages, selection, voice_label)

MIN_ELAPSED = 10.0    # seconds of a run before its speed counts as measured
MIN_CHARS = 200       # characters recorded in a run before its speed counts as measured
MAX_NOTES = 200


def _job(store, job_id: str | None) -> dict | None:
    """The stored job, with the run measurements the public job leaves out."""
    if not job_id:
        return None
    try:
        return store.job(job_id)
    except (KeyError, NotFound):
        return None


def _parse(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def _seconds(value: float | None) -> float | None:
    return None if value is None else round(max(0.0, value), 1)


def status(runtime, record: dict, *, at: datetime | None = None) -> dict:
    at = at or datetime.now(timezone.utc)
    store = runtime.store
    book = store.book(record['book_id'])
    chapters, passages = selection(book, record['chapter_ids'])
    ready = ready_audio(runtime, record, book)
    refused = refused_passages(store, record)
    view = progress(book, record, ready, refused)
    job = _job(store, record.get('job_id'))
    active = bool(job and job['status'] in ACTIVE)
    rerecord = (job or {}).get('rerecord') if (job or {}).get('phase') == 'rerecord' else None

    # Per-chapter facts -----------------------------------------------------------------------
    rows, chars_total, chars_ready, remaining_chars, remaining_passages = [], 0, 0, 0, 0
    per_chapter_remaining: dict[str, int] = {}
    for chapter, counts in zip(chapters, view['chapters']):
        segments = passages[chapter['id']]
        total = sum(len(segment['text']) for segment in segments)
        recorded = sum(len(segment['text']) for segment in segments if segment['id'] in ready)
        left = [segment for segment in segments if segment['id'] not in ready and segment['id'] not in refused]
        left_chars = sum(len(segment['text']) for segment in left)
        per_chapter_remaining[chapter['id']] = left_chars
        chars_total += total
        chars_ready += recorded
        remaining_chars += left_chars
        remaining_passages += len(left)
        rows.append({**counts, 'passages_remaining': len(left), 'chars_total': total, 'chars_ready': recorded,
                     'chars_remaining': left_chars,
                     'seconds_ready': round(sum(float(ready[s['id']].get('duration') or 0) for s in segments if s['id'] in ready), 1),
                     'run_passages': None, 'run_done': None, 'eta_seconds': None})

    # The run ---------------------------------------------------------------------------------
    run = None
    started = _parse((job or {}).get('work_started_at')) or _parse((job or {}).get('created_at'))
    elapsed = max(0.0, (at - started).total_seconds()) if started and active else 0.0
    if job:
        run_total = int(job.get('total') or 0)
        run_done = int(job.get('progress') or 0)
        run = {'job_id': job['id'], 'kind': 'rerecord' if rerecord else 'record', 'status': job['status'],
               'message': job.get('message') or '', 'started_at': (job.get('work_started_at') or job.get('created_at')),
               'updated_at': job.get('updated_at'), 'waiting_seconds': job.get('waiting_seconds'),
               'passages_total': run_total, 'passages_done': min(run_done, run_total) if run_total else run_done,
               'chars_total': int(job.get('work_chars') or 0), 'chars_done': int(job.get('chars_done') or 0),
               'elapsed_seconds': round(elapsed, 1) if active else None,
               'narrator_label': None, 'current_chapter_id': job.get('current_chapter_id'), 'issues': [
                   {**issue, 'message': _issue_message(issue)} for issue in (job.get('passage_issues') or [])
                   if issue.get('outcome') == 'unrecorded' and issue.get('segment_id') not in ready]}
        if rerecord:
            run['narrator_label'] = f"{voice_label(runtime, rerecord['provider'], rerecord['voice'])} · {PROVIDER_LABELS[rerecord['provider']]}"
            done_by = rerecord.get('done_by_chapter') or {}
            counts = rerecord.get('chapter_counts') or {}
            for row in rows:
                if row['id'] in counts:
                    row['run_passages'], row['run_done'] = counts[row['id']], done_by.get(row['id'], 0)

    # Speed and estimate --------------------------------------------------------------------
    if rerecord:
        chars_left = max(0, run['chars_total'] - run['chars_done'])
        done_run = run['chars_done']
    else:
        chars_left = remaining_chars
        done_run = max(0, int((job or {}).get('work_chars') or 0) - remaining_chars) if active else 0
    rate, basis, note = None, 'unknown', None
    if active and done_run >= MIN_CHARS and elapsed >= MIN_ELAPSED:
        rate, basis = done_run / elapsed, 'measured'
    elif chars_left and record['provider'] == 'gemini' and record['mode'] == 'simple' and not rerecord:
        guess = _gemini_rate(runtime, record)
        if guess:
            rate, basis = guess, 'estimated'
    if not chars_left:
        basis = 'none'
    elif rate is None:
        note = ('No speed is known yet. It is measured once this run has recorded some passages.' if active or job
                else 'No speed is known yet; recording will measure it.')
    seconds = chars_left / rate if rate and chars_left else (0.0 if not chars_left else None)

    paused, resumes = None, None
    if job and job['status'] == 'quota_limited':
        paused, resumes = 'quota', job.get('resume_after')
    elif job and job['status'] == 'budget_limited':
        paused = 'budget'
    requests_remaining = left_today = None
    if record['provider'] == 'gemini' and record['mode'] == 'simple' and remaining_passages and not rerecord:
        try:
            planned = plan(runtime, record['book_id'], record, record=record)['public']
            requests_remaining = planned['requests_estimate']
            quota = planned.get('quota')
            if quota:
                left_today = max(0, quota['rpd'] - quota['requests_today'])
                if requests_remaining > left_today:
                    paused = paused or 'quota'
                    resumes = resumes or quota['resets_at']
                    note = (f'About {requests_remaining} requests are left and {left_today} remain today; '
                            'recording pauses at the daily limit and resumes after the reset.')
        except Exception:  # noqa: BLE001 - the plan is context, never a reason to fail the status
            pass
    eta = {'seconds': _seconds(seconds), 'finishes_at': (at + timedelta(seconds=seconds)).isoformat() if seconds is not None and paused is None else None,
           'basis': basis, 'chars_remaining': chars_left, 'passages_remaining': (run['passages_total'] - run['passages_done']) if rerecord and run else remaining_passages,
           'rate_chars_per_second': round(rate, 2) if rate else None, 'requests_remaining': requests_remaining,
           'requests_left_today': left_today, 'paused_reason': paused, 'resumes_at': resumes, 'note': note}

    # Per-chapter state and finish estimates --------------------------------------------------
    current = _current_chapter(job, chapters, per_chapter_remaining, active, rerecord)
    cumulative = 0.0
    total_pending = sum(max(0, (row['run_passages'] or 0) - (row['run_done'] or 0)) for row in rows) or 1
    for row in rows:
        if rerecord:
            # A re-record's chapters are the ones it covers; the others keep their readiness state.
            pending = (row['run_passages'] or 0) - (row['run_done'] or 0)
            if not row['run_passages']:
                row['state'] = 'done' if not row['chars_remaining'] else _idle_state(row, job)
            elif pending <= 0:
                row['state'] = 'done'
            else:
                row['state'] = 'active' if active and row['id'] == current else 'queued' if active else _idle_state(row, job)
                if rate:
                    cumulative += chars_left * pending / total_pending
                    row['eta_seconds'] = _seconds(cumulative / rate)
            continue
        left = row['chars_remaining']
        if not row['passages_total']:
            row['state'] = 'empty'
        elif not left:
            row['state'] = 'blocked' if row['passages_blocked'] else 'done'
        elif active:
            row['state'] = 'active' if row['id'] == current else 'queued'
        else:
            row['state'] = _idle_state(row, job)
        if left and rate:
            cumulative += left
            row['eta_seconds'] = _seconds(cumulative / rate)

    notes = []
    for chapter in chapters:
        for segment in passages[chapter['id']]:
            mark = (ready.get(segment['id'], {}).get('substitute') or {})
            if mark:
                notes.append({'chapter_id': chapter['id'], 'segment_id': segment['id'], 'reason': mark['reason'],
                              'provider': ready[segment['id']].get('provider'), 'model': ready[segment['id']].get('model'),
                              'voice': ready[segment['id']].get('voice'), 'created_at': ready[segment['id']].get('created_at'),
                              'excerpt': segment['text'][:80]})
    notes = notes[:MAX_NOTES]

    return {'performance_id': record['id'], 'generated_at': at.isoformat(), 'state': _state(view, job, active),
            'run': run, 'eta': eta, 'chapters': rows, 'notes': notes,
            'totals': {'passages_total': view['passages_total'], 'passages_ready': view['passages_ready'],
                       'passages_fallback': view['passages_fallback'], 'passages_rerecorded': view['passages_rerecorded'],
                       'passages_blocked': view['passages_blocked'], 'passages_remaining': remaining_passages,
                       'chars_total': chars_total, 'chars_ready': chars_ready, 'chars_remaining': remaining_chars,
                       'seconds_ready': view['seconds_ready']}}


def _issue_message(issue: dict) -> str:
    return {'content_blocked': 'Gemini blocked this text and no fallback narrator could read it.',
            'failed': 'The narrator could not produce this passage and no fallback narrator could read it.'
            }.get(issue.get('reason'), 'This passage could not be recorded.')


def _idle_state(row: dict, job: dict | None) -> str:
    ended = {'quota_limited': 'paused', 'budget_limited': 'paused', 'failed': 'stopped', 'cancelled': 'stopped',
             'interrupted': 'stopped'}.get((job or {}).get('status'))
    if row['passages_remaining'] and ended:
        return ended
    return 'partial' if row['passages_ready'] else 'not_started'


def _state(view: dict, job: dict | None, active: bool) -> str:
    if job and job['status'] == 'running':
        return 'recording'
    if job and job['status'] == 'queued':
        return 'queued'
    settled = view['passages_total'] > 0 and view['passages_ready'] + view['passages_blocked'] >= view['passages_total']
    if settled and view['passages_blocked']:
        return 'blocked'
    if view['passages_total'] > 0 and view['passages_ready'] >= view['passages_total']:
        return 'complete'
    ended = {'quota_limited': 'paused', 'budget_limited': 'paused', 'failed': 'stopped', 'cancelled': 'stopped',
             'interrupted': 'stopped'}.get((job or {}).get('status'))
    if ended:
        return ended
    return 'partial' if view['passages_ready'] else 'not_started'


def _current_chapter(job, chapters, remaining, active, rerecord) -> str | None:
    if not active:
        return None
    if job.get('current_chapter_id'):
        return job['current_chapter_id']
    if rerecord:
        return next((chapter['id'] for chapter in chapters if chapter['id'] in (rerecord.get('chapter_counts') or {})), None)
    return next((chapter['id'] for chapter in chapters if remaining.get(chapter['id'])), None)


def _gemini_rate(runtime, record: dict) -> float | None:
    """Characters per second a Gemini one-narrator recording is expected to manage, from what earlier chunk jobs learned."""
    try:
        calibration = previous_calibration(runtime.store, record['book_id'], record['session_id'])
    except Exception:  # noqa: BLE001
        return None
    if not calibration.samples:
        return None
    options = chunk_options(runtime)
    # Audio seconds produced per second of waiting, times the requests kept in flight, times the speech rate.
    return calibration.chars_per_second * calibration.realtime_factor * max(1, int(options.get('concurrency', 1)))
