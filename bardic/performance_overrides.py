"""Passages of a saved performance that a different narrator reads.

A performance's own audio is never rewritten. When a passage cannot be narrated (Gemini blocks its text, or it
keeps failing), or the listener asks for another voice, that passage is read by a chosen narrator and the result
is *linked* to the performance in an append-only log:

- a row says "for this performance, this exact source text, use this asset" (``use``) or "go back to the audio the
  performance itself made" (``original``);
- the newest row for a passage whose source text is still current decides what plays, so a re-record, a fallback
  and a restore are all just later rows, and every earlier take stays retained and can be chosen again;
- the audio itself is an ordinary immutable simple-listening take of the reading narrator's session (its own recipe
  and content hash), so it is reused by listening and never overwrites another narrator's take.

This module is the storage and the reading step. Planning, jobs and presentation live in ``rerecord.py`` and
``performances.py``.
"""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from .audio import AudioError, ContentBlocked, RateLimited, synthesize
from .listening import ListeningRepository
from .resources import ResourceLedger
from .store import now
from .tts_limits import LIMITER, quota_day, requests_today, seconds_until_reset

SCHEMA_VERSION = 1
MAX_CONSECUTIVE_RATE_LIMITS = 5
# Why a passage was read by another narrator. ``rerecord`` is the listener's choice; the others are automatic.
REASONS = ('content_blocked', 'failed', 'rerecord')
AUTOMATIC = ('content_blocked', 'failed')
# Fixed sentences: a provider's own error text is never retained.
ERROR_SENTENCES = {
    'content_blocked': 'Gemini blocked this text under its content policy.',
    'failed': 'The narrator could not produce usable audio for this passage.',
}


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class OverrideRepository:
    def __init__(self, store):
        self.store = store
        if getattr(store, '_performance_override_schema_ready', False):
            return
        with store.lock, store.connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS performance_overrides (
                id TEXT PRIMARY KEY, book_id TEXT NOT NULL, performance_id TEXT NOT NULL,
                segment_id TEXT NOT NULL, source_key TEXT NOT NULL, body TEXT NOT NULL)''')
            conn.execute('''CREATE INDEX IF NOT EXISTS performance_overrides_performance
                ON performance_overrides(book_id,performance_id)''')
            for operation in ('UPDATE', 'DELETE'):
                conn.execute(f'''CREATE TRIGGER IF NOT EXISTS performance_overrides_no_{operation.lower()}
                    BEFORE {operation} ON performance_overrides BEGIN
                    SELECT RAISE(ABORT, 'Performance overrides are immutable'); END''')
        store._performance_override_schema_ready = True

    def add(self, book_id: str, performance_id: str, segment_id: str, source_key: str, *, action: str, reason: str,
            audio: dict | None = None, narrator: dict | None = None, error: str | None = None,
            run: str | None = None, restored_from: str | None = None, original: dict | None = None) -> dict:
        """Append one row. ``action`` is ``use`` (with ``audio`` and ``narrator``) or ``original``."""
        if action not in ('use', 'original') or (action == 'use') != bool(audio):
            raise ValueError('A use row needs audio; an original row has none.')
        body = {'schema_version': SCHEMA_VERSION, 'id': uuid4().hex, 'book_id': book_id, 'performance_id': performance_id,
                'segment_id': segment_id, 'source_key': source_key, 'action': action, 'reason': reason,
                'audio': audio, 'narrator': narrator, 'error': error, 'run': run, 'restored_from': restored_from,
                'for_provider': (original or {}).get('provider'), 'for_model': (original or {}).get('model'),
                'created_at': now()}
        with self.store.lock, self.store.connect() as conn:
            conn.execute('INSERT INTO performance_overrides VALUES (?,?,?,?,?,?)',
                         (body['id'], book_id, performance_id, segment_id, source_key, json.dumps(body, ensure_ascii=False)))
        return body

    def rows(self, book_id: str, performance_id: str) -> list[dict]:
        """Every row for the performance, newest first."""
        with self.store.lock, self.store.connect() as conn:
            found = conn.execute('SELECT body FROM performance_overrides WHERE book_id=? AND performance_id=? ORDER BY rowid DESC',
                                 (book_id, performance_id)).fetchall()
        return [json.loads(body) for (body,) in found]


def narrator_view(narrator: dict | None) -> dict | None:
    return {'provider': narrator['provider'], 'model': narrator['model'], 'voice': narrator['voice']} if narrator else None


def current(rows: list[dict], segments: list[dict], key_of, exists) -> dict[str, dict]:
    """The row deciding each passage: its newest row for the passage's current source text.

    ``key_of`` maps a segment to its source key; ``exists`` says whether a ``use`` row's audio file is present, so a
    damaged or missing file never wins. A passage whose newest matching row is ``original`` maps to that row too.
    """
    keys = {segment['id']: key_of(segment) for segment in segments}
    chosen: dict[str, dict] = {}
    for row in rows:  # newest first
        segment_id = row['segment_id']
        if segment_id in chosen or keys.get(segment_id) != row['source_key']:
            continue
        if row['action'] == 'use' and not exists(row['audio']['asset_id']):
            continue  # damaged: fall through to the next newest row
        chosen[segment_id] = row
    return chosen


def audio_of(row: dict) -> dict:
    """The public audio object for a ``use`` row: a simple-listening take marked as standing in for the passage."""
    return {**row['audio'], 'substitute': {'reason': row['reason'], 'for_provider': row.get('for_provider'),
                                            'for_model': row.get('for_model') or '', 'override_id': row['id']}}


class NarrationStopped(Exception):
    """A read stopped for a reason that is not the passage's fault (quota, repeated rate limits): retry later."""


def read_passage(runtime, job_id: str, book_id: str, chapter_id: str, segment_id: str, narrator: dict, credentials,
                 limits: dict | None, *, synthesizer=None, check_cancel=lambda: None) -> dict:
    """Have ``narrator`` (``{session_id, provider, model, voice}``) read one passage; returns the public take.

    Retained audio for the same recipe is reused without a request. A Gemini reading first checks the daily count and
    the per-minute limiter (``synthesize`` paces it); a rejected (429) request, which is certain to be unprocessed, is
    retried a bounded number of times, and an uncertain failure is never resent. A daily quota or repeated rate limit
    raises ``QuotaReached`` / ``NarrationStopped`` so the caller pauses instead of degrading the passage.
    """
    from .chapter_listening import QuotaReached
    store = runtime.store
    repository = ListeningRepository(store)
    synth = synthesizer or synthesize
    rejections = 0
    while True:
        check_cancel()
        if narrator['provider'] == 'gemini':
            used = requests_today(store, narrator['model'])
            rpd = (limits or {}).get('rpd')
            if LIMITER.daily_block(narrator['model']) > 0 or (rpd is not None and used >= rpd):
                LIMITER.block_day(narrator['model'], seconds_until_reset())
                raise QuotaReached('The daily Gemini request quota for this model is used up. Finished audio is saved; '
                                   'resume after midnight Pacific time.', quota_day()[1].isoformat())
        try:
            with ResourceLedger(store).operation(
                    book_id, 'simple_listen', run_id=job_id, unit_key=segment_id, chapter_id=chapter_id,
                    provider=narrator['provider'], model=narrator['model'], kind='narration') as metrics:
                audio = repository.render_passage(book_id, narrator['session_id'], segment_id, credentials,
                                                  synthesizer=synth, check_cancel=check_cancel)
                metrics.update(audio_seconds=audio['duration'],
                               output_bytes=repository.asset_path(book_id, audio['asset_id']).stat().st_size)
            return audio
        except RateLimited as error:
            if error.scope == 'day':
                LIMITER.block_day(narrator['model'], seconds_until_reset())
                raise QuotaReached('The daily Gemini request quota for this model is used up. Finished audio is saved; '
                                   'resume after midnight Pacific time.', quota_day()[1].isoformat()) from None
            rejections += 1
            if rejections > MAX_CONSECUTIVE_RATE_LIMITS:
                raise NarrationStopped('Gemini kept rejecting requests for its rate limit. Finished audio is saved; '
                                       'try again later.') from None


def failure_reason(error: BaseException) -> str | None:
    """The reason a fallback may read the passage, or None when the failure is not the passage's own.

    Cancellation, quota, rate limits and shutdown are never the passage's fault: they pause or stop the job and
    must not silently swap voices. Any other narration error that survived the bounded retries is.
    """
    from .chapter_listening import QuotaReached
    from .listening import KnownContentBlock
    if isinstance(error, (RateLimited, QuotaReached, NarrationStopped, InterruptedError)):
        return None
    if type(error).__name__ in ('Cancelled', 'BudgetReached'):
        return None
    if isinstance(error, (ContentBlocked, KnownContentBlock)):
        return 'content_blocked'
    if isinstance(error, (AudioError, ValueError, EOFError, OSError)):
        return 'failed'
    return None
