"""Bounded local playback diagnostics with no free-form content or credentials.

Jobs retain provider errors separately. These records correlate safe event codes
and identifiers across the browser and worker; they never copy messages, URLs,
book text, exception traces, HTTP payloads, or API keys into a new log.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import re
from uuid import uuid4

from .errors import Invalid
from .store import now


RETENTION_LIMIT = 5000
CLIENT_EVENTS = frozenset({
    'listen_request_failed', 'listen_poll_failed', 'listen_job_failed',
    'buffer_failed', 'cache_read_failed', 'playback_media_error',
    'playback_play_rejected', 'playback_waiting', 'playback_resumed',
    'preview_failed',
})
SERVER_EVENTS = frozenset({'listen_job_failed', 'listen_job_stopped', 'listen_submit_failed',
                           'voice_preview_failed', 'voice_preview_stopped', 'voice_preview_submit_failed'})
OPERATIONS = frozenset({'request', 'poll', 'play', 'prefetch', 'media', 'prepare', 'settle', 'cache_read'})
IDENTIFIERS = {
    'book_id': r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}',
    'segment_id': r'(?:segment|p)_[a-f0-9]{12,32}',
    'session_id': r'[a-f0-9]{64}',
    'job_id': r'[a-f0-9]{32}',
}
CLIENT_FIELDS = frozenset((*IDENTIFIERS, 'playback_rate', 'http_status', 'media_error_code', 'operation'))
CLIENT_RATE_LIMIT = 120
DUPLICATE_SECONDS = 2


def _details(fields, source):
    """Validate again at persistence so non-HTTP callers cannot add prose."""
    fields = {key: value for key, value in fields.items() if value is not None}
    allowed = CLIENT_FIELDS | ({'status', 'provider'} if source == 'server' else set())
    if fields.keys() - allowed:
        raise ValueError('Diagnostic records accept only bounded event fields.')
    for name, pattern in IDENTIFIERS.items():
        if name in fields and (not isinstance(fields[name], str) or re.fullmatch(pattern, fields[name]) is None):
            raise ValueError('Invalid diagnostic identifier.')
    if any(name in fields for name in ('segment_id', 'session_id', 'job_id')) and 'book_id' not in fields:
        raise ValueError('A book is required for diagnostic source identifiers.')
    for name, low, high in (('http_status', 100, 599), ('media_error_code', 1, 4)):
        if name in fields and (type(fields[name]) is not int or not low <= fields[name] <= high):
            raise ValueError('Invalid diagnostic numeric field.')
    rate = fields.get('playback_rate')
    if rate is not None and (type(rate) not in (int, float) or not math.isfinite(rate) or not .1 <= rate <= 8):
        raise ValueError('Invalid diagnostic playback rate.')
    if fields.get('operation') not in OPERATIONS | ({'worker', 'submit'} if source == 'server' else set()) | {None}:
        raise ValueError('Invalid diagnostic operation.')
    if fields.get('status') not in {None, 'failed', 'cancelled', 'interrupted'}:
        raise ValueError('Invalid diagnostic job status.')
    if fields.get('provider') not in {None, 'gemini', 'system', 'breeze'}:
        raise ValueError('Invalid diagnostic provider.')
    return fields


class DiagnosticRepository:
    def __init__(self, store):
        self.store = store
        if getattr(store, '_diagnostics_schema', False):
            return  # Created once per library (the app does it at startup).
        with store.lock, store.connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS diagnostic_events (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, source TEXT NOT NULL,
                event TEXT NOT NULL, book_id TEXT, body TEXT NOT NULL)''')
            conn.execute('CREATE INDEX IF NOT EXISTS diagnostic_events_book ON diagnostic_events(book_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS diagnostic_events_time ON diagnostic_events(source,created_at)')
        store._diagnostics_schema = True

    def record(self, event, *, source='client', **fields):
        if source not in {'client', 'server'} or event not in (CLIENT_EVENTS if source == 'client' else SERVER_EVENTS):
            raise ValueError('Unknown diagnostic event.')
        details = _details(fields, source)
        body = json.dumps(details, sort_keys=True, separators=(',', ':'))
        created_at = now()
        clock = datetime.now(timezone.utc)
        recent = (clock - timedelta(seconds=DUPLICATE_SECONDS)).isoformat()
        since = (clock - timedelta(minutes=1)).isoformat()
        book_id = details.get('book_id')
        with self.store.lock, self.store.connect() as conn:
            duplicate = conn.execute('''SELECT id FROM diagnostic_events
                WHERE source=? AND event=? AND book_id IS ? AND body=? AND created_at>=?
                ORDER BY rowid DESC LIMIT 1''', (source, event, book_id, body, recent)).fetchone()
            if duplicate:
                return {'recorded': False, 'reason': 'duplicate', 'id': duplicate[0]}
            if source == 'client':
                count = conn.execute("SELECT count(*) FROM diagnostic_events WHERE source='client' AND created_at>=?", (since,)).fetchone()[0]
                if count >= CLIENT_RATE_LIMIT:
                    return {'recorded': False, 'reason': 'rate_limited', 'id': None}
            identifier = uuid4().hex
            conn.execute('INSERT INTO diagnostic_events VALUES (?,?,?,?,?,?)',
                         (identifier, created_at, source, event, book_id, body))
            # Keep the newest bounded window. Diagnostics are explicitly a
            # rotating operational log, unlike immutable story/take artifacts.
            conn.execute('''DELETE FROM diagnostic_events WHERE rowid IN
                (SELECT rowid FROM diagnostic_events ORDER BY rowid DESC LIMIT -1 OFFSET ?)''', (RETENTION_LIMIT,))
        return {'recorded': True, 'id': identifier, 'reason': None}

    def events(self, *, book_id=None, limit=100):
        """Newest first. ``limit`` is clamped to 1..RETENTION_LIMIT, like other paging."""
        if book_id is not None and (not isinstance(book_id, str) or re.fullmatch(IDENTIFIERS['book_id'], book_id) is None):
            raise Invalid('book_id_invalid', 'The book_id filter is not a book ID.')
        limit = min(max(int(limit), 1), RETENTION_LIMIT)
        with self.store.lock, self.store.connect() as conn:
            rows = conn.execute('SELECT id,created_at,source,event,body FROM diagnostic_events'
                                + (' WHERE book_id=?' if book_id else '') + ' ORDER BY rowid DESC LIMIT ?',
                                (book_id, limit) if book_id else (limit,)).fetchall()
        return {'events': [{'id': row[0], 'created_at': row[1], 'source': row[2], 'event': row[3],
                            **json.loads(row[4])} for row in rows], 'retention_limit': RETENTION_LIMIT}


def record_safely(store, event, *, source='server', **fields):
    """Logging failure must never change a synthesis/playback/job outcome."""
    try:
        return DiagnosticRepository(store).record(event, source=source, **fields)
    except Exception:
        return {'recorded': False, 'reason': 'unavailable', 'id': None}
