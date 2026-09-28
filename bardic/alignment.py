"""Estimate passage boundaries inside one multi-passage narration take.

A chunk request returns one WAV for many passages. Highlighting needs a start
and end for each passage, but the provider returns no timestamps. This module
finds pauses in the audio and matches passage boundaries to them with a
monotonic dynamic program: consecutive boundaries should be separated by about
the duration their text implies at the chunk's own average speech rate, and
paragraph or sentence boundaries should land in longer pauses.

The result is an estimate, labelled with its method and version. It is not
speech recognition or word timing and does not prove every word was spoken.
Only the standard library is used; the audio is read once in 10 ms frames.
"""
from __future__ import annotations

import bisect
import math
import re
import wave
from array import array
from pathlib import Path

ALIGNMENT_METHOD = 'pause_alignment'
ALIGNMENT_VERSION = 1
FRAME_SECONDS = 0.01
_SENTENCE_END = re.compile(r'[.!?…]["\'”’)\]]*\s*$')
_CLAUSE_END = re.compile(r'[,;:—–-]["\'”’)\]]*\s*$')


def frame_peaks(path: Path) -> tuple[list[int], float]:
    """Peak absolute amplitude of each 10 ms frame of a 16-bit mono WAV."""
    with wave.open(str(path), 'rb') as reader:
        if reader.getsampwidth() != 2 or reader.getnchannels() != 1:
            raise ValueError('Alignment requires normalized 16-bit mono audio.')
        rate = reader.getframerate()
        size = max(1, round(rate * FRAME_SECONDS))
        peaks = []
        total = 0
        remainder = array('h')
        while block := reader.readframes(size * 2048):
            samples = array('h')
            samples.frombytes(block)
            if remainder:
                samples = remainder + samples
            usable = len(samples) - len(samples) % size
            for offset in range(0, usable, size):
                frame = samples[offset:offset + size]
                peaks.append(max(max(frame), -min(frame)))
            remainder = samples[usable:]
            total += len(block) // 2
    if remainder:
        peaks.append(max(max(remainder), -min(remainder)))
    return peaks, total / rate


def find_pauses(peaks: list[int], *, min_frames: int = 4) -> list[tuple[float, float]]:
    """Runs of quiet frames as (start, end) seconds, with a level-relative threshold."""
    if not peaks:
        return []
    ordered = sorted(peaks)
    loud = ordered[int(0.6 * (len(ordered) - 1))]
    threshold = max(64, min(0.06 * loud, 800))
    pauses, run_start = [], None
    for index, peak in enumerate([*peaks, threshold + 1]):
        if peak < threshold:
            if run_start is None:
                run_start = index
        elif run_start is not None:
            if index - run_start >= min_frames:
                pauses.append((run_start * FRAME_SECONDS, index * FRAME_SECONDS))
            run_start = None
    # Leading and trailing silence are not passage boundaries.
    return pauses


def boundary_kind(text: str, gap: str) -> str:
    if '\n' in gap:
        return 'paragraph'
    if _SENTENCE_END.search(text):
        return 'sentence'
    if _CLAUSE_END.search(text):
        return 'clause'
    return 'other'


# Expected pause strength after a phrase, by the punctuation that ends it.
_PAUSE_WEIGHT = {'paragraph': 14.0, 'sentence': 7.0, 'clause': 3.0, 'other': 0.5}
_MISS_COST = {'paragraph': 2.5, 'sentence': 1.4, 'clause': 0.35, 'other': 0.15}
_PAUSE_BONUS = {'paragraph': 0.8, 'sentence': 0.6, 'clause': 0.3, 'other': 0.15}
_PHRASE_END = re.compile(r'(?:[.!?…]+|[,;:—–]|\s-\s)["\'”’)\]]*(?=\s)')
_BEAM = 80


def _tokens(passages: list[dict]) -> list[dict]:
    """Phrase ends in reading order; each passage end is a boundary token."""
    tokens, weight = [], 0.0
    for index, passage in enumerate(passages):
        text = passage['text']
        cursor = 0
        for match in _PHRASE_END.finditer(text):
            end = match.end()
            if end >= len(text.rstrip()):
                break
            kind = 'sentence' if re.match(r'[.!?…]', match.group()) else 'clause'
            weight += max(1, end - cursor) + _PAUSE_WEIGHT[kind] / 2
            tokens.append({'weight': weight, 'kind': kind, 'passage': None})
            weight += _PAUSE_WEIGHT[kind] / 2
            cursor = end
        weight += max(1, len(text) - cursor)
        if index < len(passages) - 1:
            kind = boundary_kind(text, passage.get('gap_after', ''))
            weight += _PAUSE_WEIGHT[kind] / 2
            tokens.append({'weight': weight, 'kind': kind, 'passage': index})
            weight += _PAUSE_WEIGHT[kind] / 2
    return tokens, weight


def align(passages: list[dict], duration: float, pauses: list[tuple[float, float]]) -> dict:
    """Return contiguous clips for ``passages`` ({id, text, gap_after}) in ``duration``.

    ``gap_after`` is the exact source text between this passage and the next.
    Phrase ends inside passages are aligned too, so speech-rate differences
    between sentences cannot accumulate across a long take.
    """
    count = len(passages)
    if count == 0 or duration <= 0:
        raise ValueError('Nothing to align.')
    if count == 1:
        return {'clips': [{'segment_id': passages[0]['id'], 'start': 0.0, 'end': duration}],
                'quality': {'boundaries': 0, 'matched': 0, 'mean_rate_error': 0.0}}
    tokens, total = _tokens(passages)
    rate = duration / total
    speech = sorted(((a + b) / 2, b - a) for a, b in pauses if a > 0.05 and b < duration - 0.05)
    centers = [center for center, _ in speech]
    # Unexplained long pauses are evidence of a missed phrase end.
    spare = [0.0]
    for _, length in speech:
        spare.append(spare[-1] + 0.25 * min(1.0, length / 0.5))

    def rate_cost(delta_time, delta_expected):
        return 4.0 * math.log((delta_time + 0.25) / (delta_expected + 0.25)) ** 2

    def anchor(time, weight):
        return 0.5 * ((time - weight * rate) / (0.2 * duration)) ** 2

    # State: (token index, pause index) -> (score, previous state). Start is
    # token -1 at time zero with pause index -1; the final "token" is the end.
    best_state = {(-1, -1): (0.0, None)}
    by_token = {-1: [(-1, -1)]}
    for token_index in range(-1, len(tokens)):
        states = sorted(by_token.pop(token_index, []), key=lambda state: best_state[state][0])
        for i, j in states[:_BEAM]:
            score = best_state[(i, j)][0]
            time = 0.0 if j < 0 else centers[j]
            weight = 0.0 if i < 0 else tokens[i]['weight']
            skipped_cost = 0.0
            for step in range(1, 5):
                target = i + step
                if target >= len(tokens):
                    break
                token = tokens[target]
                expected = (token['weight'] - weight) * rate
                low, high = time + max(0.02, 0.45 * expected - 0.3), time + 2.2 * expected + 0.6
                first = bisect.bisect_left(centers, low, j + 1)
                last = bisect.bisect_right(centers, high)
                for k in range(first, last):
                    center, length = speech[k]
                    value = (score + skipped_cost + rate_cost(center - time, expected) + anchor(center, token['weight'])
                             - _PAUSE_BONUS[token['kind']] * min(1.0, length / 0.3)
                             + (spare[k] - spare[j + 1]))
                    key = (target, k)
                    if key not in best_state:
                        by_token.setdefault(target, []).append(key)
                    if key not in best_state or value < best_state[key][0]:
                        best_state[key] = (value, (i, j))
                skipped_cost += _MISS_COST[token['kind']]
    # Close at the end of the audio, allowing up to four unmatched final tokens.
    finish, finish_from = float('inf'), None
    for (i, j), (score, _) in best_state.items():
        if i < len(tokens) - 5:
            continue
        time = 0.0 if j < 0 else centers[j]
        weight = 0.0 if i < 0 else tokens[i]['weight']
        missed = sum(_MISS_COST[t['kind']] for t in tokens[i + 1:])
        value = score + missed + rate_cost(duration - time, (total - weight) * rate) + (spare[len(speech)] - spare[j + 1])
        if value < finish:
            finish, finish_from = value, (i, j)
    assigned = {}
    state = finish_from
    while state and state[0] >= 0:
        assigned[state[0]] = centers[state[1]]
        state = best_state[state][1]
    # Interpolate unmatched tokens between matched neighbours by text weight.
    anchors = [(0.0, 0.0)] + [(tokens[i]['weight'], assigned[i]) for i in sorted(assigned)] + [(total, duration)]
    times = []
    for token in tokens:
        position = bisect.bisect_right([a for a, _ in anchors], token['weight']) - 1
        position = min(position, len(anchors) - 2)
        (left_weight, left_time), (right_weight, right_time) = anchors[position], anchors[position + 1]
        share = (token['weight'] - left_weight) / max(1e-9, right_weight - left_weight)
        times.append(left_time + share * (right_time - left_time))
    for index, token in enumerate(tokens):
        if index in assigned:
            times[index] = assigned[index]
    boundaries = [(token['passage'], times[index], index in assigned) for index, token in enumerate(tokens) if token['passage'] is not None]
    # Contiguous, ordered and in range even for implausibly short audio: a
    # boundary may repeat (a zero-length clip) but never goes backwards.
    edges = [0.0]
    for _, time, _ in boundaries:
        edges.append(min(duration, max(time, edges[-1])))
    edges.append(duration)
    clips = [{'segment_id': passage['id'], 'start': round(edges[i], 3), 'end': round(edges[i + 1], 3)}
             for i, passage in enumerate(passages)]
    matched_times = [(tokens[i]['weight'], assigned[i]) for i in sorted(assigned)]
    deviations = []
    previous = (0.0, 0.0)
    for weight, time in [*matched_times, (total, duration)]:
        deviations.append(abs(math.log((time - previous[1] + 0.25) / ((weight - previous[0]) * rate + 0.25))))
        previous = (weight, time)
    return {'clips': clips, 'quality': {
        'boundaries': len(boundaries), 'matched': sum(1 for _, _, real in boundaries if real),
        'phrases': len(tokens), 'phrases_matched': len(assigned),
        'mean_rate_error': round(sum(deviations) / len(deviations), 4)}}


def align_file(path: Path, passages: list[dict]) -> dict:
    peaks, duration = frame_peaks(Path(path))
    result = align(passages, duration, find_pauses(peaks))
    return {'method': ALIGNMENT_METHOD, 'version': ALIGNMENT_VERSION, 'estimated': True, **result}
