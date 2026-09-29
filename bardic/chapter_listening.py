"""Prepare a chapter for simple listening in large, rate-paced requests.

One coordinator thread per chapter job plans chunks from the listener's
position, reserves a rate-limit slot before each send, keeps up to
``concurrency`` requests in flight, and retains every finished chunk even when
the job later stops. It never resends an uncertain request, never retries past
a daily quota, and bounds rate-limit and truncation retries.

Text Gemini refuses under its content policy is handled with a fixed budget of
requests. A blocked chunk is retained as a block and split once, at a passage
boundary near its middle, into two halves that are requested (each reserved and
paced like any chunk). A half that is blocked again is not split: its passages,
like a blocked one-passage request, are read by a free local fallback narrator
when one is available, and otherwise stay blocked and unrecorded. Blocks are
retained durably, so no later job resends known-blocked text. One blocked chunk
therefore costs at most three requests: the original and its two halves.
"""
from __future__ import annotations

import concurrent.futures
import time
from functools import partial

from .audio import AudioError, ContentBlocked, RateLimited, UncertainRequest, synthesize
from .chunking import HARD_MAX_SECONDS, Calibration, chunk_between, plan, split_chunk
from .listening import ListeningRepository, TruncatedChunk
from .resources import ResourceLedger
from .store import now
from .tts_limits import LIMITER, estimate_input_tokens, quota_day, requests_today, seconds_until_reset

MAX_CONSECUTIVE_RATE_LIMITS = 5
MAX_TRUNCATIONS = 2


class QuotaReached(Exception):
    """The daily request quota is used up; resume after the Pacific-time reset."""

    def __init__(self, message: str, resume_after: str):
        super().__init__(message)
        self.resume_after = resume_after


def request_timeout(expected_seconds: float, realtime_factor: float = 2.0) -> float:
    """Read timeout for one chunk: generous against slow generation, never unbounded.

    A timeout is an uncertain (possibly billed) outcome that is never resent,
    so it scales with the chunk and with this narrator's measured speed.
    """
    audio = min(expected_seconds, HARD_MAX_SECONDS)
    return min(900.0, max(90.0 + 1.5 * audio, 60.0 + 2.5 * audio / max(0.5, realtime_factor)))


def summarize(chunk: dict) -> dict:
    return {'first_segment_id': chunk['segment_ids'][0], 'last_segment_id': chunk['segment_ids'][-1],
            'segment_count': len(chunk['segment_ids']), 'chars': chunk['chars'],
            'target_seconds': chunk['target_seconds'], 'expected_seconds': chunk['expected_seconds']}


class ChapterCoordinator:
    def __init__(self, store, job_id: str, api_key: str, pool, *, cancelled, synthesizer=None,
                 limiter=LIMITER, sleep=time.sleep, clock=time.monotonic, poll_seconds: float = 0.5,
                 fallback_credentials=None):
        self.store = store
        # The fallback narrator itself is snapshotted on the job; only its credentials (never stored) come here.
        self.fallback_credentials = fallback_credentials
        self.final_message = None
        self.repository = ListeningRepository(store)
        self.job_id = job_id
        self.api_key = api_key
        self.pool = pool
        self.cancelled = cancelled
        self.synthesizer = synthesizer
        self.limiter = limiter
        self.sleep = sleep
        self.clock = clock
        self.poll_seconds = poll_seconds

    # Work performed in a narration pool thread. The ledger operation and
    # provider metrics stay in that thread's context.
    def _request(self, job, chunk, entry):
        synthesizer = partial(self.synthesizer or synthesize, pace=False,
                              timeout=request_timeout(chunk['expected_seconds'], entry.get('realtime_factor', 2.0)))
        started = self.clock()
        with ResourceLedger(self.store).operation(
                job['book_id'], 'listen_chunk', run_id=job['id'], unit_key=chunk['segment_ids'][0],
                chapter_id=job['chapter_id'], provider=job['provider'], model=job['model'], kind='narration') as metrics:
            result = self.repository.render_chunk(
                job['book_id'], job['session_id'], job['chapter_id'], chunk['segment_ids'], self.api_key,
                synthesizer=synthesizer, block_role=self._role(chunk),
                request={'job_id': job['id'], 'n': entry['n'], 'target_seconds': chunk['target_seconds'],
                         'expected_seconds': chunk['expected_seconds']})
            metrics.update(audio_seconds=result['duration'],
                           output_bytes=self.repository.asset_path(job['book_id'], result['asset_id']).stat().st_size)
        return result, self.clock() - started

    @staticmethod
    def _role(chunk):
        """What a block of this request means: 'half' and 'single' are final, 'chunk' is split once."""
        return 'half' if chunk.get('split') else 'single' if len(chunk['segment_ids']) == 1 else 'chunk'

    def _read_by_fallback(self, job, segment_ids):
        """Have the fallback narrator read refused passages; returns (passage IDs read, first error text).

        Runs in a narration pool thread. A passage the fallback cannot read stays blocked and unrecorded;
        that never stops the job or the other passages.
        """
        fallback, read, error = job['fallback'], [], None
        for segment_id in segment_ids:
            if self.cancelled():
                break
            try:
                with ResourceLedger(self.store).operation(
                        job['book_id'], 'simple_listen', run_id=job['id'], unit_key=segment_id, chapter_id=job['chapter_id'],
                        provider=fallback['provider'], model=fallback['model'], kind='narration') as metrics:
                    audio = self.repository.read_by_fallback(
                        job['book_id'], job['session_id'], segment_id, fallback, self.fallback_credentials,
                        synthesizer=self.synthesizer or synthesize)
                    metrics.update(audio_seconds=audio['duration'],
                                   output_bytes=self.repository.asset_path(job['book_id'], audio['asset_id']).stat().st_size)
                read.append(segment_id)
            except AudioError as failure:
                error = error or str(failure)[:300]
        return read, error

    def completed_message(self):
        """The completion text when some passages were blocked, else None (the usual message applies)."""
        return self.final_message

    def _quota_stop(self, message):
        _, reset = quota_day()
        self.limiter.block_day(self.model, seconds_until_reset())
        return QuotaReached(message, reset.isoformat())

    def run(self):
        job = self.store.job(self.job_id)
        book = self.store.book(job['book_id'])
        chapter, segments = self.repository.chapter_segments(book, job['chapter_id'])
        position = {segment['id']: index for index, segment in enumerate(segments)}
        session_id = job['session_id']
        clips = self.repository.chunk_clips(job['book_id'], session_id)
        blocked = {segment['id'] for segment in segments if segment['id'] in clips}
        takes = self.repository.takes(job['book_id'], session_id)['takes']
        covered = {take['segment_id'] for take in takes if take['segment_id'] in position}
        # Passages a performance's fallback narrator took over after their chunk kept failing: never requested here.
        covered.update(segment_id for segment_id in job.get('skip_segment_ids') or [] if segment_id in position)
        options, limits, model = job['chunking'], job['speech_limits'], job['model']
        self.model = model
        # Learned speech rate and truncation ceilings carry over from earlier jobs.
        calibration = Calibration(job.get('calibration'))
        fallback = job.get('fallback')
        # Text Gemini refused, from earlier requests and jobs. `refused` passages are never requested again;
        # `forced` halves of an earlier blocked chunk are requested next; `pending` await the fallback narrator.
        memory = self.repository.content_blocks(job['book_id'], session_id)
        refused = {segment_id for segment_id in memory['refused'] if segment_id in position}
        forced: list[dict] = []
        split_born: set[str] = set()
        for record in memory['open']:
            ids = [entry[0] for entry in record['segments']]
            if any(segment_id not in position for segment_id in ids):
                continue
            whole = chunk_between(segments, position[ids[0]], position[ids[-1]], calibration)
            for half in split_chunk(segments, chapter['text'], whole, calibration) or []:
                split_born.update(half['segment_ids'])
                if all(segment_id not in covered and segment_id not in refused for segment_id in half['segment_ids']):
                    forced.append({**half, 'split': True})
        pending = [segment['id'] for segment in segments if segment['id'] in refused and segment['id'] not in covered]
        fallback_error = None
        entries = list(job.get('chunks', []))
        step, ramp_seen, joins_seen = 0, job.get('ramp_restart', 0), job.get('joins', 0)
        inflight: dict[concurrent.futures.Future, tuple[dict, dict]] = {}
        stopping, failure, quota = False, None, None
        rate_limited = truncations = epoch = 0
        waiting_until, last_write, dirty = None, 0.0, True
        day_start, sent_today = quota_day()[0], 0

        def used_today():
            # Recount before every send: other paths also spend the quota, and
            # a job that crosses midnight Pacific starts a fresh day.
            nonlocal day_start, sent_today
            start = quota_day()[0]
            if start != day_start:
                day_start, sent_today = start, 0
            return max(requests_today(self.store, model), sent_today)

        used = used_today()

        def blocked_view():
            """Refused passages by outcome, or nothing when Gemini blocked none of this chapter's text."""
            ids = [segment['id'] for segment in segments if segment['id'] in refused]
            if not ids:
                return {}
            return {'content_blocked': {
                'fallback': dict(fallback) if fallback else None,
                'fallback_passage_ids': [segment_id for segment_id in ids if segment_id in covered],
                'blocked_passage_ids': [segment_id for segment_id in ids if segment_id not in covered],
                'fallback_error': fallback_error}}

        def blocked_summary():
            read = sum(1 for segment_id in refused if segment_id in covered)
            unrecorded = len(refused) - read
            voice = {'system': 'a device voice', 'breeze': 'Breeze'}.get((fallback or {}).get('provider'), 'the fallback narrator')
            parts = []
            if read:
                parts.append(f'{read} passage{"s" if read != 1 else ""} read by {voice} because Gemini blocked {"them" if read != 1 else "it"}')
            if unrecorded:
                parts.append(f'{unrecorded} passage{"s" if unrecorded != 1 else ""} blocked by Gemini’s content policy and left unrecorded')
            return '. '.join(parts) + '.' if parts else ''

        def write(message, **fields):
            nonlocal last_write, dirty
            scope_start = position.get(current.get('scope_start_segment_id'), 0)
            scope = [segment['id'] for segment in segments[scope_start:]]
            ready = sum(1 for segment_id in scope if segment_id in covered)
            fields.update(blocked_view())
            self.store.update_job(self.job_id, message=message, progress=ready, total=len(scope),
                                  chunks=entries, projection=projection, calibration=calibration.view(),
                                  quota={'requests_today': used, 'rpd': limits['rpd'], 'resets_at': quota_day()[1].isoformat(),
                                         'scope': 'this library'},
                                  waiting_seconds=round(waiting_until, 1) if waiting_until else None, **fields)
            last_write, dirty = self.clock(), False

        current = job
        projection = []
        try:
            while True:
                current = self.store.job(self.job_id)
                if self.cancelled():
                    stopping = True
                if current.get('ramp_restart', 0) != ramp_seen:
                    ramp_seen, step = current.get('ramp_restart', 0), 0
                for future in [future for future in inflight if future.done()]:
                    entry, chunk = inflight.pop(future)
                    dirty = True
                    if chunk.get('fallback'):
                        # The fallback narrator finished reading refused passages; a passage it could not read
                        # stays blocked and unrecorded. This never stops the job.
                        read, problem = future.result()
                        covered.update(read)
                        fallback_error = fallback_error or problem
                        continue
                    entry['finished_at'] = now()
                    try:
                        result, latency = future.result()
                    except ContentBlocked as error:
                        # Gemini refused this text. The block is already retained (render_chunk); resending it
                        # is certain to fail. One split into halves is allowed; a half is never split again.
                        entry.update(status='blocked', error=str(error))
                        rate_limited = 0
                        halves = split_chunk(segments, chapter['text'], chunk, calibration) if self._role(chunk) == 'chunk' else None
                        if halves:
                            for half in halves:
                                split_born.update(half['segment_ids'])
                                forced.append({**half, 'split': True})
                            entry['split_into'] = len(halves)
                        else:
                            refused.update(chunk['segment_ids'])
                            pending.extend(segment_id for segment_id in chunk['segment_ids'] if segment_id not in covered)
                    except RateLimited as error:
                        entry.update(status='rate_limited', error=str(error))
                        if chunk.get('split'):
                            forced.insert(0, chunk)  # the half was rejected, not sent: request it again first
                        if error.scope == 'day':
                            quota = self._quota_stop('The daily Gemini request quota for this model is used up. '
                                                     'Finished chunks are saved; resume after midnight Pacific time.')
                            stopping = True
                        else:
                            self.limiter.cool_down(model, error.retry_after)
                            rate_limited += 1
                            if rate_limited > MAX_CONSECUTIVE_RATE_LIMITS:
                                failure = RuntimeError('Gemini kept rejecting requests for its rate limit. Finished chunks are saved; try again later.')
                                stopping = True
                    except TruncatedChunk as error:
                        entry.update(status='truncated', error=str(error), duration=round(error.duration, 3))
                        # Chunks planned from the same estimate share one mistake:
                        # count it once, then plan smaller from the new ceiling.
                        if entry.get('epoch', 0) == epoch:
                            truncations += 1
                            epoch += 1
                        calibration.record_truncation(error.chars)
                        if truncations > MAX_TRUNCATIONS:
                            failure = error
                            stopping = True
                    except Exception as error:  # noqa: BLE001 - reported through Runtime.run
                        entry.update(status='failed', error=str(error)[:300], uncertain=isinstance(error, UncertainRequest))
                        failure, stopping = error, True
                    else:
                        rate_limited = 0
                        covered.update(chunk['segment_ids'])
                        blocked.update(chunk['segment_ids'])
                        calibration.record(result['chars'], result['duration'], latency)
                        entry.update(status='done', chunk_id=result['id'], duration=round(result['duration'], 3),
                                     latency=round(latency, 3), flags=result.get('flags', []),
                                     matched=result['timing']['quality'].get('matched'),
                                     boundaries=result['timing']['quality'].get('boundaries'))
                # Refused passages go to the fallback narrator now (free, local), concurrently with the requests.
                if pending and not stopping:
                    todo = [segment_id for segment_id in dict.fromkeys(pending) if segment_id not in covered]
                    pending.clear()
                    if todo and fallback:
                        inflight[self.pool.submit(self._read_by_fallback, current, todo)] = ({}, {'segment_ids': todo, 'fallback': True})
                        dirty = True
                in_flight_ids = {segment_id for _, chunk in inflight.values() for segment_id in chunk['segment_ids']}
                held = {segment_id for half in forced for segment_id in half['segment_ids']}
                scope_start = position.get(current.get('scope_start_segment_id'), 0)
                focus = position.get(current.get('focus_segment_id'), scope_start)
                # Refused text and halves still to be requested are never planned into another chunk.
                planned = [] if stopping else plan(
                    segments, chapter['text'], scope_start=scope_start, focus=focus,
                    blocked=blocked | in_flight_ids | held | refused,
                    covered=covered, options=options, calibration=calibration, step=step)
                upcoming = [] if stopping else [*forced, *planned]
                projection = [summarize(chunk) for chunk in upcoming]
                if not inflight and (stopping or not upcoming):
                    # Close only if no listener joined since this pass read the
                    # job; the join endpoint checks `closing` under the same lock.
                    with self.store.lock:
                        latest = self.store.job(self.job_id)
                        if not stopping and not self.cancelled() and latest.get('joins', 0) != joins_seen:
                            joins_seen = latest.get('joins', 0)
                            continue
                        self.store.update_job(self.job_id, closing=True)
                    break
                joins_seen = current.get('joins', 0)
                waiting_until = None
                message = None
                # Until a full-size chunk has been measured, one mistake in the
                # speech-rate prior must not truncate several requests at once.
                concurrency = options['concurrency']
                if upcoming and not calibration.samples and upcoming[0]['target_seconds'] > 120:
                    concurrency = 1
                requests_inflight = sum(1 for _, sent in inflight.values() if not sent.get('fallback'))
                if upcoming and requests_inflight < concurrency:
                    chunk = upcoming[0]
                    used = used_today()
                    if used >= limits['rpd'] or self.limiter.daily_block(model) > 0:
                        quota = self._quota_stop(
                            f'This library has used {used} of {limits["rpd"]} daily Gemini requests for this model. '
                            'Finished chunks are saved; resume after midnight Pacific time.' if used >= limits['rpd'] else
                            'The daily Gemini request quota for this model is used up. Finished chunks are saved; '
                            'resume after midnight Pacific time.')
                        stopping, dirty = True, True
                        continue
                    text = chapter['text'][chunk['start']:chunk['end']]
                    delay = self.limiter.try_acquire(model, estimate_input_tokens(text))
                    if delay > 0:
                        waiting_until = delay
                        message = f'Waiting {int(delay) + 1}s for the per-minute rate limit…'
                    else:
                        if forced and chunk is forced[0]:
                            forced.pop(0)
                        elif chunk['segment_ids'][0] in split_born:
                            chunk = {**chunk, 'split': True}  # text from a split is never split again
                        step += 1
                        sent_today += 1
                        used += 1
                        entry = {'n': len(entries) + 1, **summarize(chunk), **({'split': True} if chunk.get('split') else {}),
                                 'expected_latency': round(calibration.expected_latency(chunk['expected_seconds']), 3),
                                 'realtime_factor': round(calibration.realtime_factor, 3), 'epoch': epoch,
                                 'status': 'requesting', 'started_at': now()}
                        entries.append(entry)
                        inflight[self.pool.submit(self._request, current, chunk, entry)] = (entry, chunk)
                        dirty = True
                        continue
                if message is None:
                    active = [entry for entry, sent in inflight.values() if not sent.get('fallback')]
                    message = (f'Generating {len(active)} chunk{"s" if len(active) != 1 else ""} · '
                               f'{len(covered & set(position))} of {len(segments)} passages ready') if active else 'Planning…'
                    if stopping:
                        message = 'Stopping after the requests already sent. Their audio will be saved.'
                if dirty or self.clock() - last_write >= 1.0:
                    write(message)
                if inflight:
                    concurrent.futures.wait(list(inflight), timeout=min(self.poll_seconds, waiting_until or self.poll_seconds),
                                            return_when=concurrent.futures.FIRST_COMPLETED)
                else:
                    self.sleep(min(self.poll_seconds, waiting_until or self.poll_seconds))
        except BaseException:
            # Never orphan paid requests: let them finish (their audio is
            # retained by the request itself) before the job reports failure.
            concurrent.futures.wait(list(inflight))
            raise
        waiting_until = None
        finished = not (failure or quota or self.cancelled())
        summary = blocked_summary()
        if finished and summary:
            self.final_message = f'{"Chapter prepared" if any(i not in covered for i in refused) else "Chapter ready to listen"}. {summary}'
        done_message = ((self.final_message or 'Chapter ready to listen.') if finished
                        else str(quota) if quota else 'Stopped. Finished chunks are saved.')
        write(done_message)
        if failure:
            raise failure
        if quota:
            raise quota
