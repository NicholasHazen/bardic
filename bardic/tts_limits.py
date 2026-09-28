"""Pace Gemini speech requests to the project's configured quota.

Google applies requests-per-minute, input-tokens-per-minute and requests-per-day
limits per project and model. RPD resets at midnight Pacific time. This process
can see only its own traffic: another app or Bardic instance sharing the project
can still exhaust a limit, so provider 429 responses remain authoritative.
Counts here are lower bounds, never balances.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from collections import deque
from contextvars import ContextVar
from datetime import datetime, time as clock_time, timedelta, timezone

DEFAULT_LIMITS = {'rpm': 10, 'tpm': 10_000, 'rpd': 100}
WINDOW_SECONDS = 61.0  # One second of margin over a provider minute.
NARRATION_STAGES = {'simple_listen', 'listen_chunk', 'voice_preview', 'narration'}
# A paced single request never waits longer than this; it fails instead so the
# shared worker, cancellation and shutdown are not held hostage by a cooldown.
MAX_PACED_WAIT = 120.0
# Set by the worker for the current job so paced waits honour Stop/shutdown.
CANCEL_CHECK: ContextVar = ContextVar('tts_cancel_check', default=None)


def normalize_limits(value: dict | None) -> dict:
    limits = dict(DEFAULT_LIMITS)
    for name, maximum in (('rpm', 10_000), ('tpm', 100_000_000), ('rpd', 10_000_000)):
        if value and name in value:
            number = value[name]
            if type(number) is not int or not 1 <= number <= maximum:
                raise ValueError(f'{name.upper()} must be a whole number from 1 to {maximum:,}.')
            limits[name] = number
    return limits


def estimate_input_tokens(text: str) -> int:
    """Conservative pre-send estimate; English prose averages about 4 characters per token."""
    return math.ceil(len(text) / 3) + 16


def _pacific():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo('America/Los_Angeles')
    except Exception:
        return timezone(timedelta(hours=-8))


def quota_day(now: datetime | None = None) -> tuple[datetime, datetime]:
    """UTC start of the current Pacific quota day and the next reset."""
    zone = _pacific()
    local = (now or datetime.now(timezone.utc)).astimezone(zone)
    start = datetime.combine(local.date(), clock_time(0), zone)
    reset = datetime.combine(local.date() + timedelta(days=1), clock_time(0), zone)
    return start.astimezone(timezone.utc), reset.astimezone(timezone.utc)


def seconds_until_reset(now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    return max(1.0, (quota_day(now)[1] - now).total_seconds())


def requests_today(store, model: str, now: datetime | None = None) -> int:
    """Gemini speech requests this library recorded in the current quota day."""
    start, _ = quota_day(now)
    start_text = start.isoformat()
    count = 0
    with store.lock, store.connect() as conn:
        rows = conn.execute('SELECT body FROM resource_operations WHERE stage IN (%s)'
                            % ','.join('?' * len(NARRATION_STAGES)), tuple(NARRATION_STAGES)).fetchall()
    for (body,) in rows:
        row = json.loads(body)
        if (row.get('provider') == 'gemini' and row.get('model') == model and
                str(row.get('created_at', '')) >= start_text and type(row.get('request_count')) is int):
            count += row['request_count']
    return count


class RateLimiter:
    """Sliding-window RPM/TPM gate shared by every Gemini speech request."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._lock = threading.Lock()
        self._sent: dict[str, deque] = {}
        self._cooldown: dict[str, float] = {}
        self._daily: dict[str, float] = {}
        self._limits: dict[str, dict] = {}

    def configure(self, limits_by_model: dict[str, dict]):
        """Apply limits. Saving limits is an explicit user decision, so it
        also lifts a daily block (for example after a quota tier upgrade)."""
        with self._lock:
            self._limits = {model: normalize_limits(limits) for model, limits in limits_by_model.items()}
            self._daily.clear()

    def block_day(self, model: str, seconds: float):
        """Stop every sender for this model until the quota day resets."""
        with self._lock:
            until = self.clock() + max(1.0, seconds)
            self._daily[model] = max(self._daily.get(model, 0.0), until)

    def daily_block(self, model: str) -> float:
        with self._lock:
            return max(0.0, self._daily.get(model, 0.0) - self.clock())

    def limits(self, model: str) -> dict:
        with self._lock:
            return dict(self._limits.get(model, DEFAULT_LIMITS))

    def _delay(self, model: str, tokens: int, now: float) -> float:
        limits = self._limits.get(model, DEFAULT_LIMITS)
        sent = self._sent.setdefault(model, deque())
        while sent and sent[0][0] <= now - WINDOW_SECONDS:
            sent.popleft()
        delay = max(0.0, self._cooldown.get(model, 0.0) - now)
        if len(sent) >= limits['rpm']:
            delay = max(delay, sent[len(sent) - limits['rpm']][0] + WINDOW_SECONDS - now)
        used = sum(item[1] for item in sent)
        if used + tokens > limits['tpm'] and sent:
            # Wait until enough earlier input tokens leave the window.
            for when, amount in sent:
                used -= amount
                if used + tokens <= limits['tpm']:
                    delay = max(delay, when + WINDOW_SECONDS - now)
                    break
            else:
                # One oversized request may use an otherwise empty window.
                delay = max(delay, sent[-1][0] + WINDOW_SECONDS - now)
        return delay

    def try_acquire(self, model: str, tokens: int) -> float:
        """Record a send and return 0, or return seconds to wait without recording."""
        with self._lock:
            now = self.clock()
            delay = self._delay(model, tokens, now)
            if delay <= 0:
                self._sent.setdefault(model, deque()).append((now, tokens))
            return delay

    def acquire(self, model: str, tokens: int, *, sleep=time.sleep, check_cancel=None, max_wait=MAX_PACED_WAIT):
        """Wait for a send slot, checking cancellation; never wait past ``max_wait``."""
        from .audio import RateLimited
        check_cancel = check_cancel or CANCEL_CHECK.get() or (lambda: None)
        waited = 0.0
        while True:
            check_cancel()
            if self.daily_block(model) > 0:
                raise RateLimited('The daily Gemini request quota for this model is used up; it resets at midnight Pacific time.',
                                  'day', self.daily_block(model))
            delay = self.try_acquire(model, tokens)
            if delay <= 0:
                return
            if waited + delay > max_wait:
                raise RateLimited('Gemini narration is paused by a rate-limit cooldown. Nothing was sent; try again shortly.',
                                  'minute', delay)
            step = min(delay, 1.0)
            sleep(step)
            waited += step

    def reset(self):
        """Forget recorded sends and cooldowns (tests and a fresh process only)."""
        with self._lock:
            self._sent.clear()
            self._cooldown.clear()
            self._daily.clear()

    def cool_down(self, model: str, seconds: float):
        """Pause every sender after the provider reported a rate limit."""
        with self._lock:
            until = self.clock() + max(1.0, seconds)
            self._cooldown[model] = max(self._cooldown.get(model, 0.0), until)

    def view(self, model: str) -> dict:
        with self._lock:
            now = self.clock()
            sent = [item for item in self._sent.get(model, ()) if item[0] > now - WINDOW_SECONDS]
            return {'recent_requests': len(sent), 'recent_input_tokens': sum(item[1] for item in sent),
                    'cooldown_seconds': round(max(0.0, self._cooldown.get(model, 0.0) - now), 1),
                    'daily_block_seconds': round(max(0.0, self._daily.get(model, 0.0) - now), 1)}


LIMITER = RateLimiter()

_DURATION = re.compile(r'^(\d+(?:\.\d+)?)s$')


def classify_rate_limit(response) -> tuple[str, float]:
    """Return ('day'|'minute'|'unknown', retry seconds) from a 429 held in memory only.

    The error body can echo request content, so only quota identifiers and the
    retry delay are read; nothing from it is logged or persisted.
    """
    scope, retry = 'unknown', None
    header = response.headers.get('retry-after') if hasattr(response, 'headers') else None
    if header and header.strip().isdigit():
        retry = float(header.strip())
    try:
        details = response.json().get('error', {}).get('details', [])
    except (ValueError, AttributeError):
        details = []
    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict):
            continue
        for violation in detail.get('violations', []) if isinstance(detail.get('violations'), list) else []:
            identifier = ' '.join(str(violation.get(key, '')) for key in ('quotaId', 'quotaMetric')
                                  if isinstance(violation, dict)).lower()
            if 'perday' in identifier or 'per_day' in identifier:
                scope = 'day'
            elif scope != 'day' and ('perminute' in identifier or 'per_minute' in identifier):
                scope = 'minute'
        delay = detail.get('retryDelay')
        if isinstance(delay, str) and (match := _DURATION.match(delay.strip())):
            retry = float(match.group(1))
    return scope, min(max(retry if retry is not None else 30.0, 1.0), 3600.0)
