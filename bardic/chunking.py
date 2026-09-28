"""Plan large simple-listening requests from exact, contiguous source passages.

Daily request quotas, not text size, limit cloud narration. A chunk therefore
carries as much audio as the provider's output cap safely allows. Chunks are
exact chapter slices from one passage start to a later passage end, so the
provider receives original prose (including paragraph breaks) and nothing else.
Planning is pure: it reads passages and calibration, never audio or providers.
"""
from __future__ import annotations

import re

CHUNKING_VERSION = 1
# Gemini 3.8 TTS: 16,384 output tokens per request and a measured 32 audio
# tokens per second (not the 25 quoted in pricing material) => 512 seconds.
OUTPUT_TOKEN_CAP = 16_384
AUDIO_TOKENS_PER_SECOND = 32
PROVIDER_AUDIO_CAP_SECONDS = OUTPUT_TOKEN_CAP / AUDIO_TOKENS_PER_SECOND
# Never plan beyond this, whatever the preferences say.
HARD_MAX_SECONDS = 470
DEFAULT_RAMP_SECONDS = (30, 60)
DEFAULT_TARGET_SECONDS = 420
DEFAULT_CONCURRENCY = 2
# Speech-rate priors in source code points per audio second. Long single
# passages measured 14.2-17.8; the low prior protects the output cap until
# this narrator has finished chunks of its own.
PRIOR_CHARS_PER_SECOND = 14.0
PRIOR_CHARS_PER_SECOND_LOW = 12.0
PRIOR_REALTIME_FACTOR = 2.0
_SENTENCE_END = re.compile(r'[.!?…]["\'”’)\]]*\s*$')


def normalize_options(options: dict | None) -> dict:
    """Validate chunk preferences; the UI offers presets over this contract."""
    options = dict(options or {})
    ramp = options.get('ramp_seconds', list(DEFAULT_RAMP_SECONDS))
    target = options.get('target_seconds', DEFAULT_TARGET_SECONDS)
    concurrency = options.get('concurrency', DEFAULT_CONCURRENCY)
    if (not isinstance(ramp, (list, tuple)) or len(ramp) > 6 or
            any(type(step) not in (int, float) or not 10 <= step <= HARD_MAX_SECONDS for step in ramp)):
        raise ValueError('Chunk ramp steps must be 10–470 seconds, with at most six steps.')
    if type(target) not in (int, float) or not 30 <= target <= HARD_MAX_SECONDS:
        raise ValueError(f'Chunk length must be 30–{HARD_MAX_SECONDS} seconds of audio.')
    if type(concurrency) is not int or not 1 <= concurrency <= 3:
        raise ValueError('Choose one to three chunk requests at a time.')
    return {'ramp_seconds': [float(step) for step in ramp], 'target_seconds': float(target),
            'concurrency': concurrency}


def boundary_strength(segments: list[dict], index: int, chapter_text: str) -> int:
    """How natural it is to end a request after ``segments[index]``.

    4 chapter end, 3 scene change, 2 paragraph break, 1 sentence end,
    0 mid-sentence (for example between a quotation and its speech tag).
    """
    if index >= len(segments) - 1:
        return 4
    current, following = segments[index], segments[index + 1]
    if current.get('scene_id') != following.get('scene_id'):
        return 3
    if '\n' in chapter_text[current['end']:following['start']]:
        return 2
    return 1 if _SENTENCE_END.search(current['text']) else 0


class Calibration:
    """Speech rate and generation speed learned from this job's finished chunks."""

    def __init__(self, data: dict | None = None):
        data = data or {}
        self.samples = [sample for sample in data.get('samples', [])
                        if isinstance(sample, dict) and sample.get('chars', 0) > 0 and sample.get('duration', 0) > 0]
        self.truncated_chars_per_second = data.get('truncated_chars_per_second')
        self.max_chars = data.get('max_chars')

    @property
    def chars_per_second(self) -> float:
        # A pseudo-observation keeps one unusual first chunk from dominating.
        chars = 1500 + sum(s['chars'] for s in self.samples)
        seconds = 1500 / PRIOR_CHARS_PER_SECOND + sum(s['duration'] for s in self.samples)
        return chars / seconds

    @property
    def chars_per_second_low(self) -> float:
        """Conservative rate used only to stay under the provider output cap."""
        observed = [s['chars'] / s['duration'] for s in self.samples if s['duration'] >= 20]
        low = min(observed) * 0.85 if observed else PRIOR_CHARS_PER_SECOND_LOW
        if self.truncated_chars_per_second:
            low = min(low, self.truncated_chars_per_second)
        return max(4.0, low)

    @property
    def realtime_factor(self) -> float:
        timed = [s for s in self.samples if s.get('latency', 0) > 0]
        if not timed:
            return PRIOR_REALTIME_FACTOR
        return max(.2, sum(s['duration'] for s in timed) / sum(s['latency'] for s in timed))

    def record(self, chars: int, duration: float, latency: float | None):
        self.samples.append({'chars': chars, 'duration': duration, 'latency': latency or 0})
        self.samples = self.samples[-12:]

    def record_truncation(self, chars: int):
        # The provider stopped at its cap before finishing ``chars``. Both the
        # rate estimate and an absolute ceiling shrink, so a retry is smaller.
        rate = chars / PROVIDER_AUDIO_CAP_SECONDS * 0.9
        self.truncated_chars_per_second = min(self.truncated_chars_per_second or rate, rate)
        self.max_chars = min(self.max_chars or chars, int(chars * 0.75))

    @property
    def hard_chars(self) -> float:
        """Largest chunk the provider can finish, from rate and any truncation."""
        limit = HARD_MAX_SECONDS * self.chars_per_second_low
        return min(limit, self.max_chars) if self.max_chars else limit

    def expected_latency(self, audio_seconds: float) -> float:
        return 3.0 + audio_seconds / self.realtime_factor

    def view(self) -> dict:
        return {'samples': self.samples, 'truncated_chars_per_second': self.truncated_chars_per_second,
                'max_chars': self.max_chars,
                'chars_per_second': round(self.chars_per_second, 3),
                'chars_per_second_low': round(self.chars_per_second_low, 3),
                'realtime_factor': round(self.realtime_factor, 3)}


def step_seconds(options: dict, step: int) -> float:
    ramp = options['ramp_seconds']
    return min(ramp[step] if step < len(ramp) else options['target_seconds'], HARD_MAX_SECONDS)


def next_chunk(segments: list[dict], chapter_text: str, start: int, blocked: set[str],
               target_seconds: float, calibration: Calibration) -> dict | None:
    """Choose one chunk beginning at ``segments[start]``; never crosses ``blocked``."""
    if start >= len(segments) or segments[start]['id'] in blocked:
        return None
    target_chars = max(1, target_seconds * calibration.chars_per_second)
    hard_chars = calibration.hard_chars
    first = segments[start]
    run_end = start
    while run_end + 1 < len(segments) and segments[run_end + 1]['id'] not in blocked:
        run_end += 1
    best, best_score, last_fit = None, None, start
    for index in range(start, run_end + 1):
        chars = segments[index]['end'] - first['start']
        if chars > hard_chars and index > start:
            break
        last_fit = index
        if chars < 0.6 * target_chars and index < run_end:
            continue
        if chars > 1.2 * target_chars and index > start:
            break
        fill = min(chars, target_chars) / target_chars
        overshoot = max(0., chars - target_chars) / target_chars
        score = boundary_strength(segments, index, chapter_text) + 4 * fill - 2 * overshoot
        if best_score is None or score > best_score:
            best, best_score = index, score
    remaining = segments[run_end]['end'] - first['start']
    if remaining <= min(hard_chars, 1.35 * target_chars):
        # A short tail costs a whole daily request; carry it in this chunk.
        best = run_end
    if best is None:
        best = last_fit
    last = segments[best]
    chars = last['end'] - first['start']
    return {'first_index': start, 'last_index': best,
            'segment_ids': [segment['id'] for segment in segments[start:best + 1]],
            'start': first['start'], 'end': last['end'], 'chars': chars,
            'target_seconds': round(target_seconds, 3),
            'expected_seconds': round(chars / calibration.chars_per_second, 3)}


def work_order(segments: list[dict], scope_start: int, focus: int) -> list[int]:
    """Listener position first, then the rest of the requested scope."""
    focus = min(max(focus, scope_start), len(segments) - 1) if segments else 0
    return list(range(focus, len(segments))) + list(range(scope_start, focus))


def plan(segments: list[dict], chapter_text: str, *, scope_start: int, focus: int,
         blocked: set[str], covered: set[str] | None = None, options: dict,
         calibration: Calibration, step: int = 0, limit: int | None = None) -> list[dict]:
    """Project remaining chunks in the order they will be requested.

    ``blocked`` passages (chunk audio or in flight) end a chunk. ``covered``
    adds passages that already have single-passage audio: a chunk never starts
    there, but may run through them rather than splitting into more requests.
    """
    chunks, taken = [], set(blocked)
    done = taken | set(covered or ())
    for index in work_order(segments, scope_start, focus):
        if limit is not None and len(chunks) >= limit:
            break
        if segments[index]['id'] in done:
            continue
        chunk = next_chunk(segments, chapter_text, index, taken, step_seconds(options, step + len(chunks)), calibration)
        if chunk:
            chunks.append(chunk)
            taken.update(chunk['segment_ids'])
            done.update(chunk['segment_ids'])
    return chunks
