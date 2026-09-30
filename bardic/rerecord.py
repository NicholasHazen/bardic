"""Re-record part of a saved performance with another narrator, and choose between the takes it keeps.

A re-record reads the chosen passages with a chosen provider and voice and links each result to the performance
(see ``performance_overrides``). The performance's own audio and every earlier take stay retained, so the listener
can hear the new take, go back to the original, or pick any earlier one again. Nothing here starts work except an
explicit re-record; the preview, the take list and a restore are local.
"""
from __future__ import annotations

from . import performances
from .audio import ContentBlocked
from .errors import Invalid, NotFound, Unavailable
from .listening import ListeningRepository, require_active_book
from .performance_overrides import AUTOMATIC, NarrationStopped, OverrideRepository, audio_of, narrator_view, read_passage
from .performances import (CHARS_PER_SECOND, own_ready, PROVIDER_LABELS, PerformanceRepository, override_rows, provider_problems,
                           ready_audio, refuse, resolve_model, selection, source_key, voice_label)
from .chapter_listening import QuotaReached
from .store import now, public_job
from .tts_limits import DEFAULT_LIMITS, LIMITER, quota_day, requests_today

MAX_PASSAGES = 1000  # one re-record request: a bound on a single job, not on the performance


def passages_in_scope(runtime, book: dict, record: dict, request: dict) -> list[tuple[dict, dict]]:
    """The (chapter, passage) pairs a re-record covers, in reading order.

    Scope is the first of: explicit ``segment_ids``, a ``from_segment_id``..``to_segment_id`` range, a
    ``chapter_id``, else the whole performance. ``only='fallback'`` then keeps the passages another narrator reads
    because the main narration could not.
    """
    chapters, passages = selection(book, record['chapter_ids'])
    order = [(chapter, segment) for chapter in chapters for segment in passages[chapter['id']]]
    index = {segment['id']: position for position, (_, segment) in enumerate(order)}
    if request.get('segment_ids') is not None:
        unknown = [segment_id for segment_id in request['segment_ids'] if segment_id not in index]
        if unknown:
            raise Invalid('unknown_passage', 'A passage ID is not in this performance.')
        picked = [order[position] for position in sorted({index[segment_id] for segment_id in request['segment_ids']})]
    elif request.get('from_segment_id') or request.get('to_segment_id'):
        first, last = request.get('from_segment_id'), request.get('to_segment_id')
        if not (first and last):
            raise Invalid('range_incomplete', 'A passage range needs both from_segment_id and to_segment_id.')
        if first not in index or last not in index:
            raise Invalid('unknown_passage', 'A passage ID is not in this performance.')
        if index[first] > index[last]:
            raise Invalid('range_invalid', 'from_segment_id comes after to_segment_id in reading order.')
        picked = order[index[first]:index[last] + 1]
    elif request.get('chapter_id'):
        if request['chapter_id'] not in passages:
            raise Invalid('unknown_chapter', 'The chapter is not part of this performance.')
        picked = [(chapter, segment) for chapter, segment in order if chapter['id'] == request['chapter_id']]
    else:
        picked = order
    if request.get('only') == 'fallback':
        ready = ready_audio(runtime, record, book)
        picked = [(chapter, segment) for chapter, segment in picked
                  if (ready.get(segment['id'], {}).get('substitute') or {}).get('reason') in AUTOMATIC]
    if not picked:
        raise Invalid('nothing_to_rerecord', 'No passage matches: choose passages, a chapter or a range that has text '
                                             '(with only=fallback, that a fallback narrator currently reads).')
    if len(picked) > MAX_PASSAGES:
        raise Invalid('scope_too_large', f'A re-record covers at most {MAX_PASSAGES} passages; choose a chapter or a range.')
    return picked


def narrator_choice(runtime, request: dict) -> dict:
    """The validated ``{provider, voice, model?}`` the request names (400 for an unknown provider or unusable voice)."""
    provider = request.get('provider')
    choice = runtime.fallback_choice({'provider': provider, 'voice': request.get('voice') or ''}) if provider else None
    if not choice:
        raise Invalid('provider_unsupported', 'A re-record needs a provider: system, gemini or breeze.')
    choice['model'] = resolve_model(provider, request.get('model') or None, runtime)  # 400 for a model the provider does not take
    return choice


def plan(runtime, book_id: str, performance_id: str, request: dict) -> dict:
    """What a re-record would do, without contacting any provider or storing anything."""
    store = runtime.store
    book = store.book(book_id)
    record = PerformanceRepository(store).get(book_id, performance_id)
    picked = passages_in_scope(runtime, book, record, request)
    choice = narrator_choice(runtime, request)
    provider, model = choice['provider'], choice['model']
    problems = provider_problems(runtime, provider)
    notes = ['Passages this narrator already read are reused without a request. The audio being replaced is kept and '
             'can be chosen again.']
    if record['mode'] == 'cast':
        notes.append('The chosen narrator reads these passages in one voice instead of the cast.')
    quota = None
    requests = len(picked) if provider == 'gemini' else 0
    if provider == 'gemini':
        limits = runtime.preferences['tts_limits'].get(model, dict(DEFAULT_LIMITS))
        used = requests_today(store, model)
        quota = {'requests_today': used, 'rpd': limits['rpd'], 'resets_at': quota_day()[1].isoformat()}
        if LIMITER.daily_block(model) > 0:
            notes.append('The daily Gemini request quota for this model is used up. A re-record stops at once and can be '
                         'repeated after midnight Pacific time.')
        elif requests > max(0, limits['rpd'] - used):
            notes.append(f'About {requests} requests are needed and this library has {max(0, limits["rpd"] - used)} of '
                         f'{limits["rpd"]} daily requests left for this model. The re-record stops at the daily limit; '
                         'repeat it after midnight Pacific time to finish.')
    chapters: dict[str, dict] = {}
    for chapter, _ in picked:
        chapters.setdefault(chapter['id'], {'id': chapter['id'], 'title': chapter.get('title', ''), 'passages': 0})['passages'] += 1
    characters = sum(len(segment['text']) for _, segment in picked)
    return {'public': {
        'performance_id': performance_id, 'provider': provider, 'model': model, 'voice': choice['voice'],
        'narrator_label': f'{voice_label(runtime, provider, choice["voice"])} · {PROVIDER_LABELS[provider]}',
        'chapter_ids': list(chapters), 'passages_total': len(picked), 'characters': characters,
        'requests_estimate': requests, 'expected_seconds': round(characters / CHARS_PER_SECOND, 1),
        'chapters': list(chapters.values()), 'problems': [{'code': code, 'detail': text} for code, text in problems],
        'notes': notes, 'quota': quota},
        'problems': problems, 'record': record, 'picked': picked, 'choice': choice, 'model': model}


def start(runtime, book_id: str, performance_id: str, request: dict) -> dict:
    """Validate and queue a re-record. Call under the store lock."""
    require_active_book(runtime.store, book_id)
    runtime.require_idle(book_id)
    if runtime.stopping.is_set():
        raise Unavailable('shutting_down', 'The server is shutting down and accepts no new narration.')
    planned = plan(runtime, book_id, performance_id, request)
    if planned['problems']:
        raise refuse(planned['problems'])
    record, picked, choice, public = planned['record'], planned['picked'], planned['choice'], planned['public']
    narrator = runtime.fallback_narrator(book_id, choice)
    if narrator is None:
        raise Invalid('narrator_unavailable', 'The chosen narrator cannot be used now: check its provider and voice.')
    limits = (dict(runtime.preferences['tts_limits'].get(narrator['model'], DEFAULT_LIMITS))
              if narrator['provider'] == 'gemini' else None)
    credentials = runtime.narration_credentials(narrator['provider'])
    store = runtime.store
    job = store.create_job(book_id, 'performance', len(picked))
    job = store.update_job(job['id'], performance_id=performance_id, mode=record['mode'], provider=narrator['provider'],
                           model=narrator['model'], child_job_ids=[], child_job_id=None,
                           fallback=narrator, passage_issues=[],
                           rerecord={'provider': narrator['provider'], 'model': narrator['model'], 'voice': narrator['voice'],
                                     'passages': len(picked), 'chapter_ids': public['chapter_ids'],
                                     'chapter_counts': {chapter['id']: chapter['passages'] for chapter in public['chapters']},
                                     'done_by_chapter': {}},
                           work_chars=public['characters'], chars_done=0,
                           message='Waiting for the performance worker')
    PerformanceRepository(store).update(book_id, performance_id, job_id=job['id'])
    label = public['narrator_label']

    def work():
        _work(runtime, job['id'], record, picked, narrator, credentials, limits)
    try:
        future = runtime.performance_pool.submit(
            runtime.run, job, work, runtime.narration_secrets(narrator['provider']),
            lambda: _completed(store, job['id'], len(picked), label))
    except RuntimeError:
        store.update_job(job['id'], status='failed', error='The local narration worker could not accept this request.',
                         message='No narration was started. Restart Bardic and try again.')
        raise Unavailable('shutting_down', 'The narration worker is stopping and accepted no work. No narration was started.') from None

    def settle_cancelled(future):
        if future.cancelled():
            store.update_job(job['id'], status='interrupted' if runtime.stopping.is_set() else 'cancelled',
                             cancel_requested=True, message='Stopped before any passage was re-recorded.')
    future.add_done_callback(settle_cancelled)
    book = store.book(book_id)
    return {'performance': performances.present(runtime, PerformanceRepository(store).get(book_id, performance_id), book),
            'job': public_job(store.job(job['id']))}


def _completed(store, job_id: str, count: int, label: str) -> str:
    blocked = len({issue['segment_id'] for issue in store.job(job_id).get('passage_issues') or []})
    done = count - blocked
    text = f'Re-recorded {done} passage{"s" if done != 1 else ""} with {label}. The earlier audio is kept.'
    if blocked:
        text += f' {blocked} passage{"s" if blocked != 1 else ""} could not be re-recorded because Gemini blocks the text; they keep their current audio.'
    return text


def _work(runtime, job_id: str, record: dict, picked: list, narrator: dict, credentials, limits: dict | None):
    from .app import Cancelled  # imported here: app imports this module
    store, repository = runtime.store, OverrideRepository(runtime.store)
    total = len(picked)
    store.update_job(job_id, work_started_at=now())
    chars_done, done_by = 0, {}
    for done, (chapter, segment) in enumerate(picked):
        runtime.check_cancel(job_id)
        title = chapter.get('title') or 'chapter'
        store.update_job(job_id, current_chapter_id=chapter['id'], message=f'Re-recording passage {done + 1} of {total} · {title}')
        try:
            audio = read_passage(runtime, job_id, record['book_id'], chapter['id'], segment['id'], narrator, credentials,
                                 limits, synthesizer=performances.synthesize, check_cancel=lambda: runtime.check_cancel(job_id))
        except (QuotaReached, NarrationStopped, Cancelled, InterruptedError):
            raise
        except ContentBlocked:
            # Text Gemini blocks cannot be re-recorded by Gemini: note it and go on with the rest of the scope.
            performances.note_issue(store, job_id, chapter['id'], segment['id'], 'content_blocked', 'unrecorded')
            store.update_job(job_id, progress=done + 1)
            continue
        except Exception as error:
            raise RuntimeError(f'Passage {done + 1} of {total} could not be re-recorded: {error}') from error
        repository.add(record['book_id'], record['id'], segment['id'], source_key(segment), action='use', reason='rerecord',
                       audio={key: value for key, value in audio.items() if key != 'substitute'},
                       narrator=narrator_view(narrator), run=job_id,
                       original={'provider': record['provider'], 'model': record['model']})
        chars_done += len(segment['text'])
        done_by[chapter['id']] = done_by.get(chapter['id'], 0) + 1
        with store.lock:
            rerecord = dict(store.job(job_id).get('rerecord') or {})
            store.update_job(job_id, progress=done + 1, chars_done=chars_done, rerecord={**rerecord, 'done_by_chapter': dict(done_by)})


# Takes and restore ------------------------------------------------------------

def takes(runtime, book_id: str, performance_id: str, chapter_id: str | None = None, segment_id: str | None = None) -> list[dict]:
    """The retained takes linked to the performance, newest first, for passages whose source text is still current.

    ``current`` marks the one row deciding what plays. Audio the performance itself made is not listed: it is the
    ``original`` a restore returns to.
    """
    book = runtime.store.book(book_id)
    record = PerformanceRepository(runtime.store).get(book_id, performance_id)
    chapters, passages = selection(book, record['chapter_ids'])
    by_id = {segment['id']: (chapter, segment) for chapter in chapters for segment in passages[chapter['id']]}
    listening = ListeningRepository(runtime.store)
    decided = override_rows(runtime, record, [segment for _, segment in by_id.values()])
    result = []
    for row in OverrideRepository(runtime.store).rows(book_id, performance_id):
        pair = by_id.get(row['segment_id'])
        if pair is None or source_key(pair[1]) != row['source_key']:
            continue
        chapter, _ = pair
        if (chapter_id and chapter['id'] != chapter_id) or (segment_id and row['segment_id'] != segment_id):
            continue
        present = row['action'] == 'use' and listening.asset_path(book_id, row['audio']['asset_id']).is_file()
        narrator = row.get('narrator') or {}
        result.append({
            'id': row['id'], 'segment_id': row['segment_id'], 'chapter_id': chapter['id'], 'action': row['action'],
            'reason': row['reason'], 'created_at': row['created_at'], 'restored_from': row.get('restored_from'),
            'provider': narrator.get('provider'), 'model': narrator.get('model'), 'voice': narrator.get('voice'),
            'voice_label': voice_label(runtime, narrator['provider'], narrator.get('voice')) if narrator.get('provider') else None,
            'error': row.get('error'), 'available': present or row['action'] == 'original',
            'current': decided.get(row['segment_id'], {}).get('id') == row['id'],
            'audio': audio_of(row) if present else None})
        if len(result) >= 500:
            break
    return result


def restore(runtime, book_id: str, performance_id: str, segment_ids: list[str], override_id: str | None) -> dict:
    """Choose what plays for passages: the performance's own audio (``override_id`` null) or one retained take.

    Appends rows; nothing is deleted or rewritten, so the choice can itself be undone.
    """
    require_active_book(runtime.store, book_id)
    runtime.require_idle(book_id)  # a running writer's later row would silently reverse the choice
    book = runtime.store.book(book_id)
    repository = PerformanceRepository(runtime.store)
    record = repository.get(book_id, performance_id)
    chapters, passages = selection(book, record['chapter_ids'])
    by_id = {segment['id']: segment for chapter in chapters for segment in passages[chapter['id']]}
    ids = list(dict.fromkeys(segment_ids))
    unknown = [segment_id for segment_id in ids if segment_id not in by_id]
    if unknown:
        raise Invalid('unknown_passage', 'A passage ID is not in this performance.')
    log = OverrideRepository(runtime.store)
    listening = ListeningRepository(runtime.store)
    if override_id is None:
        own = own_ready(runtime, record, [by_id[segment_id] for segment_id in ids])
        missing = [segment_id for segment_id in ids if segment_id not in own]
        if missing:
            raise Invalid('no_original', 'The performance never made its own audio for a passage (a fallback narrator read it), '
                                         'so there is nothing to return to. Choose one of its takes instead.')
    if override_id is not None:
        if len(ids) != 1:
            raise Invalid('restore_ambiguous', 'Choosing a specific take applies to exactly one passage.')
        row = next((item for item in log.rows(book_id, performance_id) if item['id'] == override_id), None)
        if row is None or row['segment_id'] != ids[0]:
            raise NotFound('take_not_found', 'The performance has no such take for this passage.')
        if row['source_key'] != source_key(by_id[ids[0]]):
            raise Invalid('take_stale', 'The passage text changed after this take was made.')
        if row['action'] != 'use':
            raise Invalid('take_not_playable', 'That entry returned to the performance’s own audio; restore with no take instead.')
        if not listening.asset_path(book_id, row['audio']['asset_id']).is_file():
            raise Invalid('take_missing', 'The audio file for this take is missing.')
        log.add(book_id, performance_id, ids[0], row['source_key'], action='use', reason=row['reason'], audio=row['audio'],
                narrator=row['narrator'], error=row.get('error'), restored_from=row['id'],
                original={'provider': row.get('for_provider'), 'model': row.get('for_model')})
    else:
        for segment_id in ids:
            log.add(book_id, performance_id, segment_id, source_key(by_id[segment_id]), action='original', reason='restore')
    return {'performance': performances.present(runtime, repository.get(book_id, performance_id), book)}
