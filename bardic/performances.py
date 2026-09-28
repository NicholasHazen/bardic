"""Saved performances: a named selection of chapters and a narrator or cast.

A performance record is a mutable label (name, archive flag, latest job). Its
audio lives in immutable records that are never rewritten:

- ``simple`` performances point at one pinned listening session; their audio
  is that session's retained takes and chunk clips (see listening.py), so takes
  made earlier by live listening with the same narrator count as ready.
- ``cast`` performances snapshot the resolved cast when created and retain one
  ``performance_takes`` row per generated or reused passage take. They never
  write the Studio's current take selection (the ``takes`` table).

Audio applies only while it matches the passage's current source; a changed
passage shows as not ready and a resume regenerates only it. Only an explicit
create or prepare starts work; previews and listings are local reads.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from urllib.parse import quote
from uuid import uuid4

from . import pronunciation
from .audio import (BREEZE_MODEL, SYSTEM_MODEL, TTS_MODELS, AudioError, RateLimited, render_fingerprint,
                    synthesize, validate_audio, voice_id, voice_selection)
from .chapter_listening import ChapterCoordinator, QuotaReached
from .chunking import Calibration, normalize_options, plan as plan_chunks
from .listening import ListeningRepository, require_active_book
from .resources import ResourceLedger
from .store import now, public_job
from .take_archive import produce_take
from .tts_limits import DEFAULT_LIMITS, LIMITER, quota_day, requests_today, seconds_until_reset
from .audio_refs import audio_ref
from .errors import ApiError, Invalid, NotFound, Unavailable

SCHEMA_VERSION = 1
SOURCE_KEY_VERSION = 1
NARRATOR = 'narrator'
UNASSIGNED = 'unassigned'
PROVIDER_LABELS = {'system': 'Device voices', 'gemini': 'Gemini', 'breeze': 'Breeze'}
VOICE_NOUNS = {'system': 'device', 'gemini': 'Gemini', 'breeze': 'Breeze'}
# Prior speech rate for unnarrated text, as elsewhere in listening estimates.
CHARS_PER_SECOND = 14.0
MAX_CONSECUTIVE_RATE_LIMITS = 5
ACTIVE = {'queued', 'running'}


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def source_key(segment: dict) -> str:
    """Identity of a passage's current source: chapter, exact coordinates and text."""
    return _hash({'schema_version': SOURCE_KEY_VERSION, 'chapter_id': segment.get('chapter_id'),
                  'start': segment.get('start'), 'end': segment.get('end'), 'text': segment.get('text')})


class PerformanceRepository:
    def __init__(self, store):
        self.store = store
        # Schema setup runs once per store, never on each request.
        if getattr(store, '_performance_schema_ready', False):
            return
        with store.lock, store.connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS performances (
                book_id TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY(book_id,id))''')
            # One immutable row per retained cast-performance passage take. A
            # damaged file can be replaced by a new asset for the same source,
            # so the asset is part of the key; readers take the newest present one.
            conn.execute('''CREATE TABLE IF NOT EXISTS performance_takes (
                book_id TEXT NOT NULL, performance_id TEXT NOT NULL, segment_id TEXT NOT NULL,
                source_key TEXT NOT NULL, asset_id TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY(performance_id,segment_id,source_key,asset_id))''')
            conn.execute('CREATE INDEX IF NOT EXISTS performance_takes_book ON performance_takes(book_id)')
            conn.execute('''CREATE TRIGGER IF NOT EXISTS performance_takes_no_replace
                BEFORE INSERT ON performance_takes WHEN EXISTS(SELECT 1 FROM performance_takes
                WHERE performance_id=NEW.performance_id AND segment_id=NEW.segment_id
                AND source_key=NEW.source_key AND asset_id=NEW.asset_id)
                BEGIN SELECT RAISE(IGNORE); END''')
            for operation in ('UPDATE', 'DELETE'):
                conn.execute(f'''CREATE TRIGGER IF NOT EXISTS performance_takes_no_{operation.lower()}
                    BEFORE {operation} ON performance_takes BEGIN
                    SELECT RAISE(ABORT, 'Performance takes are immutable'); END''')
        store._performance_schema_ready = True

    def create(self, record: dict) -> dict:
        with self.store.lock, self.store.connect() as conn:
            conn.execute('INSERT INTO performances VALUES (?,?,?)',
                         (record['book_id'], record['id'], json.dumps(record, ensure_ascii=False)))
        return record

    def get(self, book_id: str, performance_id: str) -> dict:
        with self.store.lock, self.store.connect() as conn:
            row = conn.execute('SELECT body FROM performances WHERE book_id=? AND id=?',
                               (book_id, performance_id)).fetchone()
        if not row:
            raise NotFound('performance_not_found', 'Performance not found')
        return json.loads(row[0])

    def list(self, book_id: str, include_archived: bool = False) -> list[dict]:
        with self.store.lock, self.store.connect() as conn:
            rows = conn.execute('SELECT body FROM performances WHERE book_id=? ORDER BY rowid DESC',
                                (book_id,)).fetchall()
        records = [json.loads(body) for (body,) in rows]
        return [record for record in records if include_archived or not record.get('archived')]

    def update(self, book_id: str, performance_id: str, **fields) -> dict:
        """Change label fields only (name, archived, job_id); audio records are untouched."""
        with self.store.lock:
            record = self.get(book_id, performance_id)
            record.update(fields, updated_at=now())
            with self.store.connect() as conn:
                conn.execute('UPDATE performances SET body=? WHERE book_id=? AND id=?',
                             (json.dumps(record, ensure_ascii=False), book_id, performance_id))
            return record

    def retain(self, book_id: str, performance_id: str, segment_id: str, key: str, body: dict) -> dict:
        with self.store.lock, self.store.connect() as conn:
            conn.execute('INSERT OR IGNORE INTO performance_takes VALUES (?,?,?,?,?,?)',
                         (book_id, performance_id, segment_id, key, body['asset_id'],
                          json.dumps(body, ensure_ascii=False)))
            row = conn.execute('''SELECT body FROM performance_takes WHERE performance_id=? AND segment_id=?
                AND source_key=? AND asset_id=?''', (performance_id, segment_id, key, body['asset_id'])).fetchone()
        return json.loads(row[0])

    def rows(self, book_id: str, performance_id: str | None = None) -> list[tuple[str, str, str, dict]]:
        """(performance_id, segment_id, source_key, body), newest first."""
        query = 'SELECT performance_id,segment_id,source_key,body FROM performance_takes WHERE book_id=?'
        args: tuple = (book_id,)
        if performance_id is not None:
            query += ' AND performance_id=?'
            args += (performance_id,)
        with self.store.lock, self.store.connect() as conn:
            rows = conn.execute(query + ' ORDER BY rowid DESC', args).fetchall()
        return [(pid, segment_id, key, json.loads(body)) for pid, segment_id, key, body in rows]


# Selection, cast and labels ---------------------------------------------------

def selection(book: dict, chapter_ids) -> tuple[list[dict], dict[str, list[dict]]]:
    """Selected chapters in book order and their passages. Chapters no longer in the book are skipped."""
    wanted = set(chapter_ids)
    chapters = [chapter for chapter in book['chapters'] if chapter['id'] in wanted]
    passages = {chapter['id']: [] for chapter in chapters}
    for segment in book['segments']:
        if segment['chapter_id'] in passages:
            passages[segment['chapter_id']].append(segment)
    return chapters, passages


def resolve_model(provider: str, model: str | None, runtime) -> str:
    if provider == 'gemini':
        model = model or runtime.preferences['tts_model']
        if model not in TTS_MODELS:
            raise Invalid('model_unsupported', 'The Gemini speech model is not a supported TTS model.')
        return model
    fixed = SYSTEM_MODEL if provider == 'system' else BREEZE_MODEL
    if model not in (None, '', fixed):
        raise Invalid('model_unsupported', f'{PROVIDER_LABELS[provider]} narration uses the {fixed} model.')
    return fixed


def _renders(character: dict, provider: str, model: str) -> bool:
    try:
        render_fingerprint({'id': 'voice-check', 'text': 'Voice check.'}, character, {}, provider, model)
        return True
    except (AudioError, ValueError, KeyError, TypeError):
        return False


def has_own_voice(character: dict | None, provider: str, model: str) -> bool:
    """A concrete, renderable voice chosen for this character and provider."""
    if not isinstance(character, dict):
        return False
    try:
        chosen = voice_selection(character, provider)
    except AudioError:
        return False
    return bool(chosen and chosen.get('id')) and _renders(character, provider, model)


def cast_voice(snapshot: dict, speaker_id: str, provider: str, model: str) -> tuple[str, dict, bool]:
    """(character id used, character, fallback). Unknown, unassigned and voiceless speakers use the narrator."""
    if speaker_id == NARRATOR:
        return NARRATOR, snapshot[NARRATOR], False
    character = snapshot.get(speaker_id)
    if speaker_id != UNASSIGNED and has_own_voice(character, provider, model):
        return speaker_id, character, False
    return NARRATOR, snapshot[NARRATOR], True


def snapshot_cast(runtime, book: dict) -> dict:
    # Evidence quotes are not rendering inputs; everything else stays so
    # fingerprints match Studio takes of the same recipe exactly.
    return {character_id: {key: value for key, value in character.items() if key != 'evidence'}
            for character_id, character in runtime.resolved_cast(book).items()}


def names(values: list[str]) -> str:
    values = list(dict.fromkeys(values))
    if len(values) > 5:
        return f"{', '.join(values[:5])} and {len(values) - 5} others"
    return values[0] if len(values) == 1 else f"{', '.join(values[:-1])} and {values[-1]}"


def voice_label(runtime, provider: str, voice: str | None) -> str:
    if isinstance(voice, str) and voice.startswith('library:'):
        entry = runtime.library_index().get(voice[len('library:'):])
        return (entry or {}).get('name') or 'Library voice'
    if voice:
        return voice
    return {'gemini': 'Kore'}.get(provider, 'Default voice')


def narrator_label(runtime, record: dict) -> str:
    provider = PROVIDER_LABELS.get(record['provider'], record['provider'])
    if record['mode'] == 'cast':
        return f'Full cast · {provider}'
    return f"{voice_label(runtime, record['provider'], record.get('voice'))} · {provider}"


def default_name(label: str, chapters: list[dict], book: dict) -> str:
    if len(chapters) == len(book['chapters']):
        scope = 'Whole book'
    elif len(chapters) == 1:
        scope = chapters[0].get('title') or 'One chapter'
    else:
        scope = f'{len(chapters)} chapters'
    return f'{label} · {scope}'[:200]


def provider_problems(runtime, provider: str) -> list[tuple[str, str]]:
    """Provider conditions that block generation, as (error code, neutral sentence) pairs."""
    if provider == 'gemini' and not runtime.api_key:
        return [('gemini_key_missing', 'No Gemini API key is configured.')]
    if provider == 'system' and not (shutil.which('say') and shutil.which('ffmpeg')):
        return [('device_narration_unavailable', 'Device narration requires macOS say and ffmpeg on the server.')]
    if provider == 'breeze' and not runtime.breeze_url():
        return [('breeze_url_missing', 'No Breeze server URL is configured.')]
    return []


def refuse(problems: list[tuple[str, str]]) -> Invalid:
    """One 400 for blocking problems: the first problem's code, every problem's sentence."""
    return Invalid(problems[0][0], ' '.join(text for _, text in problems))


# Ready audio ------------------------------------------------------------------

def stored_session(repository, book_id: str, session_id: str) -> dict:
    """The listening session a performance record pinned. Sessions are never deleted, so a missing one
    is damaged stored data (a server defect, 500), not a resource the request named."""
    try:
        return repository.get_session(book_id, session_id)
    except NotFound:
        raise KeyError(session_id) from None  # a bare KeyError is a 500 internal_error


def simple_ready(store, book_id: str, session_id: str, wanted: set[str]) -> dict[str, dict]:
    """Session audio for the wanted passages that matches their current source. Local, no WAV rereads."""
    repository = ListeningRepository(store)
    stored_session(repository, book_id, session_id)
    takes = repository.takes(book_id, session_id)['takes']
    return {take['segment_id']: take['audio'] for take in takes if take['segment_id'] in wanted}


def cast_ready(runtime, record: dict, segments: list[dict], rows=None, *, validate: bool = False) -> dict[str, dict]:
    """Newest retained take per passage whose source key is current and whose file exists
    (``validate``: whose WAV also passes validation, so resume can replace a damaged one)."""
    book_id, performance_id = record['book_id'], record['id']
    rows = PerformanceRepository(runtime.store).rows(book_id, performance_id) if rows is None else rows
    saved: dict[tuple[str, str], list[dict]] = {}
    for _, segment_id, key, body in rows:
        saved.setdefault((segment_id, key), []).append(body)
    ready = {}
    for segment in segments:
        for body in saved.get((segment['id'], source_key(segment)), []):
            try:
                present = (_valid_file(runtime, book_id, body['asset_id']) is not None if validate
                           else runtime.audio_path(book_id, body['asset_id']).is_file())
            except (ValueError, TypeError, KeyError):
                present = False
            if present:
                # A reused legacy Studio take is stored under its recipe fingerprint: not a content address.
                content = body['asset_id'] if body['asset_id'] != body.get('fingerprint') else None
                ready[segment['id']] = audio_ref(
                    f'/api/books/{quote(book_id, safe="")}/audio-assets/{body["asset_id"]}',
                    asset_id=content, duration=body.get('duration'), provider=body.get('provider'),
                    model=body.get('model'), voice=body.get('voice'), created_at=body.get('created_at'),
                    speaker_id=body.get('speaker_id'), character_id=body.get('character_id'),
                    fallback=bool(body.get('fallback')))
                break
    return ready


def ready_audio(runtime, record: dict, book: dict | None = None) -> dict[str, dict]:
    book = book or runtime.store.book(record['book_id'])
    _, passages = selection(book, record['chapter_ids'])
    segments = [segment for chapter_segments in passages.values() for segment in chapter_segments]
    if record['mode'] == 'simple':
        ready = simple_ready(runtime.store, record['book_id'], record['session_id'], {s['id'] for s in segments})
    else:
        ready = cast_ready(runtime, record, segments)
    return ready


def progress(book: dict, record: dict, ready: dict) -> dict:
    chapters, passages = selection(book, record['chapter_ids'])
    rows, total, count, seconds = [], 0, 0, 0.0
    for chapter in chapters:
        segments = passages[chapter['id']]
        done = [segment for segment in segments if segment['id'] in ready]
        rows.append({'id': chapter['id'], 'title': chapter.get('title', ''),
                     'passages_total': len(segments), 'passages_ready': len(done)})
        total += len(segments)
        count += len(done)
        seconds += sum(float(ready[segment['id']].get('duration') or 0) for segment in done)
    return {'passages_total': total, 'passages_ready': count, 'seconds_ready': round(seconds, 1), 'chapters': rows}


def job_summary(store, job_id: str | None) -> dict | None:
    if not job_id:
        return None
    try:
        return public_job(store.job(job_id))
    except KeyError:
        return None


def cast_summary(runtime, record: dict, book: dict) -> list[dict]:
    snapshot, provider, model = record['cast_snapshot'], record['provider'], record['model']
    _, passages = selection(book, record['chapter_ids'])
    speakers = [NARRATOR] + [segment['speaker_id'] for segments in passages.values() for segment in segments]
    summary = []
    for speaker_id in dict.fromkeys(speakers):
        if NARRATOR not in snapshot:
            break
        used_id, character, fallback = cast_voice(snapshot, speaker_id, provider, model)
        named = snapshot.get(speaker_id) or {}
        summary.append({'character_id': speaker_id, 'name': named.get('name') or speaker_id,
                        'voice_label': voice_label(runtime, provider, voice_id(character, provider)),
                        'fallback': fallback})
    return summary


def present(runtime, record: dict, book: dict | None = None, ready: dict | None = None) -> dict:
    book = book or runtime.store.book(record['book_id'])
    ready = ready_audio(runtime, record, book) if ready is None else ready
    result = {key: value for key, value in record.items() if key not in ('cast_snapshot', 'pronunciation_snapshot')}
    if record.get('pronunciation_snapshot'):
        result['pronunciation_count'] = len(record['pronunciation_snapshot'])
    if record['mode'] == 'cast':
        result['cast'] = cast_summary(runtime, record, book)
    result['job'] = job_summary(runtime.store, record.get('job_id'))
    result['progress'] = progress(book, record, ready)
    result['narrator_label'] = narrator_label(runtime, record)
    return result


# Planning (local only) ----------------------------------------------------------

def chunk_options(runtime) -> dict:
    # Performances queue work ahead of listening: every request is full size.
    return normalize_options({**runtime.preferences['listen_chunking'], 'ramp_seconds': []})


def previous_calibration(store, book_id: str, session_id: str) -> Calibration:
    previous = next((job for job in store.jobs(book_id, limit=None)
                     if job['kind'] == 'listen_chapter' and job.get('session_id') == session_id
                     and job.get('calibration')), None)
    return Calibration(previous.get('calibration') if previous else None)


def plan(runtime, book_id: str, request: dict, record: dict | None = None, *, validate: bool = False) -> dict:
    """Resolve and estimate a performance without contacting any provider.

    ``record`` plans the resume of an existing performance with its pinned
    session or cast snapshot. Invalid requests raise ``Invalid``; conditions the
    user can fix are returned as ``problems``: (error code, sentence) pairs, public as ``{code, detail}``.
    """
    store = runtime.store
    book = store.book(book_id)
    mode, provider = request['mode'], request['provider']
    if mode not in ('simple', 'cast'):
        raise Invalid('mode_unsupported', 'The performance mode must be simple or cast.')
    if provider not in PROVIDER_LABELS:
        raise Invalid('provider_unsupported', 'The narration provider must be system, gemini or breeze.')
    requested = list(request.get('chapter_ids') or [])
    if not requested:
        raise Invalid('no_chapters_selected', 'A performance needs at least one chapter.')
    known = {chapter['id'] for chapter in book['chapters']}
    unknown = [chapter_id for chapter_id in requested if chapter_id not in known]
    if unknown and record is None:
        raise Invalid('unknown_chapter', 'A chapter ID is not in this book.')
    chapters, passages = selection(book, requested)
    chapter_ids = [chapter['id'] for chapter in chapters]
    problems = provider_problems(runtime, provider)
    notes: list[str] = []
    session = snapshot = None
    model = record['model'] if record else resolve_model(provider, request.get('model'), runtime)
    wanted = {segment['id'] for segments in passages.values() for segment in segments}
    if mode == 'simple':
        repository = ListeningRepository(store)
        if record:
            session = stored_session(repository, book_id, record['session_id'])
        else:
            try:
                voice, pinned = runtime.narrator_choice(provider, request.get('voice'))
                session = repository.session(book_id, provider, voice, model, selection=pinned)
            except ApiError as error:
                problems.append((error.code, error.detail))
        ready = simple_ready(store, book_id, session['id'], wanted) if session else {}
        if ready and not record:
            notes.append(f'{len(ready)} passage{"s" if len(ready) != 1 else ""} already have audio from '
                         'earlier listening with this narrator and will be reused.')
    else:
        snapshot = record['cast_snapshot'] if record else snapshot_cast(runtime, book)
        narrator = snapshot.get(NARRATOR)
        if narrator is None or not _renders(narrator, provider, model):
            problems.append(('narrator_voice_missing', f'The narrator has no usable {VOICE_NOUNS[provider]} voice.'))
        ready = cast_ready(runtime, record, [s for segs in passages.values() for s in segs], validate=validate) if record else {}
        speakers = [segment['speaker_id'] for segs in passages.values() for segment in segs]
        voiceless = [snapshot.get(speaker, {}).get('name') or speaker for speaker in dict.fromkeys(speakers)
                     if speaker not in (NARRATOR, UNASSIGNED) and not has_own_voice(snapshot.get(speaker), provider, model)]
        if voiceless:
            verb = 'has' if len(voiceless) == 1 else 'have'
            notes.append(f'{names(voiceless)} {verb} no {VOICE_NOUNS[provider]} voice and will use the narrator.')
        lexicon = pronunciation.book_lexicon(book)
        pinned = (record.get('pronunciation_snapshot') or []) if record else lexicon
        if pinned:
            notes.append(f'{len(pinned)} saved pronunciation{"s" if len(pinned) != 1 else ""} '
                         f'appl{"y" if len(pinned) != 1 else "ies"} to this performance.')
        if record and 'pronunciation_snapshot' not in record and lexicon:
            notes.append('This performance was created before pronunciations existed and does not use them; '
                         'create a new performance to use them.')
        elif record and pinned != lexicon:
            notes.append('Pronunciations changed after this performance started. It keeps its original '
                         'pronunciations; create a new performance to use the current ones.')
        unassigned = speakers.count(UNASSIGNED)
        if unassigned:
            notes.append(f'{unassigned} passage{"s" if unassigned != 1 else ""} with no identified speaker '
                         f'use{"s" if unassigned == 1 else ""} the narrator.')
        unanalyzed = [chapter.get('title') or chapter['id'] for chapter in chapters
                      if passages[chapter['id']] and all(s['speaker_id'] in (NARRATOR, UNASSIGNED) for s in passages[chapter['id']])]
        if unanalyzed:
            one = len(unanalyzed) == 1
            notes.append(f'{names(unanalyzed)} {"has" if one else "have"} no speaker assignments '
                         f'and {"uses" if one else "use"} the narrator throughout.')
    missing = {chapter['id']: [segment for segment in passages[chapter['id']] if segment['id'] not in ready]
               for chapter in chapters}
    to_generate = sum(len(segments) for segments in missing.values())
    requests_estimate = to_generate
    quota = None
    if provider == 'gemini':
        limits = runtime.preferences['tts_limits'].get(model, dict(DEFAULT_LIMITS))
        used = requests_today(store, model)
        quota = {'requests_today': used, 'rpd': limits['rpd'], 'resets_at': quota_day()[1].isoformat()}
        if mode == 'simple' and session:
            options, calibration = chunk_options(runtime), previous_calibration(store, book_id, session['id'])
            clips = ListeningRepository(store).chunk_clips(book_id, session['id'])
            requests_estimate = 0
            for chapter in chapters:
                if not missing[chapter['id']]:
                    continue
                segments = passages[chapter['id']]
                first = next(i for i, segment in enumerate(segments) if segment['id'] not in ready)
                requests_estimate += len(plan_chunks(
                    segments, chapter['text'], scope_start=first, focus=first,
                    blocked={s['id'] for s in segments if s['id'] in clips}, covered=set(ready),
                    options=options, calibration=calibration))
        if LIMITER.daily_block(model) > 0:
            notes.append('The daily Gemini request quota for this model is used up. Generation stops at once and '
                         'can be resumed after midnight Pacific time.')
        elif requests_estimate > max(0, limits['rpd'] - used):
            notes.append(f'About {requests_estimate} requests are needed and this library has '
                         f'{max(0, limits["rpd"] - used)} of {limits["rpd"]} daily requests left for this model. '
                         'Preparation stops at the daily limit; resume it after midnight Pacific time.')
    expected = (sum(len(segment['text']) for segments in missing.values() for segment in segments) / CHARS_PER_SECOND +
                sum(float(audio.get('duration') or 0) for audio in ready.values()))
    label_record = {'mode': mode, 'provider': provider, 'voice': request.get('voice') if mode == 'simple' else None}
    public = {
        'mode': mode, 'provider': provider, 'model': model, 'chapter_ids': chapter_ids,
        'passages_total': len(wanted), 'passages_ready': len(ready), 'passages_to_generate': to_generate,
        'requests_estimate': requests_estimate, 'expected_seconds': round(expected, 1),
        'chapters': [{'id': chapter['id'], 'title': chapter.get('title', ''),
                      'passages_total': len(passages[chapter['id']]),
                      'passages_ready': len(passages[chapter['id']]) - len(missing[chapter['id']])} for chapter in chapters],
        'problems': [{'code': code, 'detail': text} for code, text in problems], 'notes': notes, 'quota': quota,
        'narrator_label': narrator_label(runtime, record or label_record),
    }
    return {'public': public, 'problems': problems, 'book': book, 'session': session, 'snapshot': snapshot,
            'model': model, 'chapters': chapters, 'to_generate': to_generate}


# Creation and jobs ------------------------------------------------------------

def create(runtime, book_id: str, request: dict) -> dict:
    """Validate, record and start a performance. Call under the store lock."""
    require_active_book(runtime.store, book_id)
    runtime.require_idle(book_id)
    if runtime.stopping.is_set():
        raise Unavailable('shutting_down', 'The server is shutting down and accepts no new narration.')
    planned = plan(runtime, book_id, request)
    public = planned['public']
    if planned['problems']:
        raise refuse(planned['problems'])
    book, stamp = planned['book'], now()
    name = (request.get('name') or '').strip() or default_name(public['narrator_label'], planned['chapters'], book)
    record = {'id': 'pf_' + uuid4().hex, 'book_id': book_id, 'schema_version': SCHEMA_VERSION,
              'name': name[:200], 'mode': public['mode'], 'chapter_ids': public['chapter_ids'],
              'provider': public['provider'], 'model': planned['model'],
              'voice': (request.get('voice') or '') if public['mode'] == 'simple' else None,
              'created_at': stamp, 'updated_at': stamp, 'archived': False, 'job_id': None}
    if public['mode'] == 'simple':
        record['session_id'] = planned['session']['id']
    else:
        record['cast_snapshot'] = planned['snapshot']
        # Pinned like the cast: a later lexicon edit must not mix pronunciations within one performance.
        record['pronunciation_snapshot'] = pronunciation.book_lexicon(book)
    repository = PerformanceRepository(runtime.store)
    repository.create(record)
    job = start(runtime, record, planned['to_generate']) if planned['to_generate'] else None
    record = repository.get(book_id, record['id'])
    return {'performance': present(runtime, record, book), 'job': job}


def prepare(runtime, book_id: str, performance_id: str) -> dict:
    """Resume missing work with the same narrator session or cast snapshot. Call under the store lock."""
    repository = PerformanceRepository(runtime.store)
    runtime.store.book(book_id)
    record = repository.get(book_id, performance_id)
    require_active_book(runtime.store, book_id)
    runtime.require_idle(book_id)
    if runtime.stopping.is_set():
        raise Unavailable('shutting_down', 'The server is shutting down and accepts no new narration.')
    problems = provider_problems(runtime, record['provider'])
    if problems:
        raise refuse(problems)
    planned = plan(runtime, book_id, record, record=record, validate=True)
    if planned['problems']:
        raise refuse(planned['problems'])
    job = start(runtime, record, planned['to_generate']) if planned['to_generate'] else None
    record = repository.get(book_id, performance_id)
    return {'performance': present(runtime, record, planned['book']), 'job': job}


def start(runtime, record: dict, total: int) -> dict:
    store = runtime.store
    book_id, provider = record['book_id'], record['provider']
    # Snapshot credentials and server configuration for this queued job.
    key = runtime.narration_credentials(provider)
    limits = dict(runtime.preferences['tts_limits'].get(record['model'], DEFAULT_LIMITS))
    options = chunk_options(runtime)
    job = store.create_job(book_id, 'performance', total)
    job = store.update_job(job['id'], performance_id=record['id'], mode=record['mode'], provider=provider,
                           model=record['model'], phase='performance', child_job_ids=[], child_job_id=None,
                           message='Waiting for the performance worker')
    PerformanceRepository(store).update(book_id, record['id'], job_id=job['id'])
    work = {'simple': _simple_work, 'cast': _cast_work}[record['mode']]
    try:
        future = runtime.performance_pool.submit(runtime.run, job, lambda: work(runtime, job['id'], record, key, limits, options),
                                                 runtime.narration_secrets(provider))
    except RuntimeError:
        store.update_job(job['id'], status='failed', error='The local narration worker could not accept this request.',
                         message='No narration was started. Restart Bardic and try again.')
        raise Unavailable('shutting_down', 'The narration worker is stopping and accepted no work. No narration was started.') from None

    def settle_cancelled(future):
        if future.cancelled():
            store.update_job(job['id'], status='interrupted' if runtime.stopping.is_set() else 'cancelled',
                             cancel_requested=True, message='Stopped before any passage was prepared.')
    future.add_done_callback(settle_cancelled)
    return job


def _quota_error(message: str, model: str) -> QuotaReached:
    LIMITER.block_day(model, seconds_until_reset())
    return QuotaReached(message, quota_day()[1].isoformat())


def _simple_work(runtime, job_id: str, record: dict, key, limits: dict, options: dict):
    from .app import Cancelled
    from .processing import BudgetReached

    store = runtime.store
    book_id, provider = record['book_id'], record['provider']
    repository = ListeningRepository(store)
    session = stored_session(repository, book_id, record['session_id'])
    book = store.book(book_id)
    chapters, passages = selection(book, record['chapter_ids'])
    wanted = {segment['id'] for segments in passages.values() for segment in segments}
    ready = simple_ready(store, book_id, session['id'], wanted)
    missing = {chapter['id']: [s for s in passages[chapter['id']] if s['id'] not in ready] for chapter in chapters}
    total = sum(len(segments) for segments in missing.values())
    store.update_job(job_id, total=total, progress=0)
    pending = [chapter for chapter in chapters if missing[chapter['id']]]
    done = 0
    if provider == 'gemini':
        model = session['model']
        if LIMITER.daily_block(model) > 0:
            raise QuotaReached('The daily Gemini request quota for this model is used up. Finished audio is saved; '
                               'resume after midnight Pacific time.', quota_day()[1].isoformat())
        for number, chapter in enumerate(pending, 1):
            segments = passages[chapter['id']]
            first = missing[chapter['id']][0]
            # Checked and created under the lock the cancel route holds, so a
            # cancelled performance never starts another chapter job.
            with store.lock:
                runtime.check_cancel(job_id)
                position = next(i for i, segment in enumerate(segments) if segment['id'] == first['id'])
                child = store.create_job(book_id, 'listen_chapter', len(segments) - position)
                child = store.update_job(
                    child['id'], session_id=session['id'], chapter_id=chapter['id'], provider='gemini',
                    model=model, voice=session['voice'], intent='queue',
                    scope_start_segment_id=first['id'], focus_segment_id=first['id'],
                    chunking=options, speech_limits=limits, ramp_restart=0, joins=0, phase='chapter_listen', chunks=[],
                    calibration=previous_calibration(store, book_id, session['id']).view(), parent_id=job_id)
                parent = store.job(job_id)
                store.update_job(job_id, child_job_id=child['id'],
                                 child_job_ids=[*parent.get('child_job_ids', []), child['id']],
                                 message=f'Chapter {number} of {len(pending)} · {len(missing[chapter["id"]])} passages in chunks')
            child_id = child['id']
            coordinator = ChapterCoordinator(store, child_id, key, runtime.narration_pool,
                                             cancelled=lambda: runtime.cancelled(child_id) or runtime.cancelled(job_id))
            runtime.run(child, coordinator.run, (key,))
            settled = store.job(child_id)
            state = settled['status']
            ready = simple_ready(store, book_id, session['id'], wanted)
            done += sum(1 for segment in missing[chapter['id']] if segment['id'] in ready)
            store.update_job(job_id, progress=done, child_job_id=None)
            if state == 'quota_limited':
                raise QuotaReached(settled.get('message') or 'The daily Gemini request quota is used up.',
                                   settled.get('resume_after') or quota_day()[1].isoformat())
            if state == 'budget_limited':
                raise BudgetReached(settled.get('message') or 'A request limit was reached.')
            if state in ('cancelled', 'interrupted'):
                raise Cancelled()
            if state == 'failed':
                raise RuntimeError(settled.get('error') or 'Chapter preparation failed. Finished audio is saved.')
            runtime.check_cancel(job_id)
        return
    for number, chapter in enumerate(pending, 1):
        segments = passages[chapter['id']]
        for segment in missing[chapter['id']]:
            runtime.check_cancel(job_id)
            index = next(i for i, s in enumerate(segments) if s['id'] == segment['id'])
            store.update_job(job_id, message=f'Chapter {number} of {len(pending)} · passage {index + 1} of {len(segments)}')
            with ResourceLedger(store).operation(book_id, 'simple_listen', run_id=job_id, unit_key=segment['id'],
                                                 chapter_id=chapter['id'], provider=provider, model=session['model'],
                                                 kind='narration') as metrics:
                audio = repository.cached(book_id, session['id'], segment['id'])
                if audio is not None:
                    metrics['cached'] = True
                else:
                    audio = repository.generate_passage(book_id, session['id'], segment['id'], key, synthesizer=synthesize,
                                                        check_cancel=lambda: runtime.check_cancel(job_id))
                metrics.update(audio_seconds=audio['duration'],
                               output_bytes=repository.asset_path(book_id, audio['asset_id']).stat().st_size)
            done += 1
            store.update_job(job_id, progress=done)


def _studio_take(runtime, book_id: str, segment: dict, fingerprint: str) -> dict | None:
    """Read-only reuse of the book's current or archived Studio take with this exact recipe."""
    current = segment.get('audio')
    candidates = [current] if isinstance(current, dict) and current.get('fingerprint') == fingerprint else []
    with runtime.store.lock, runtime.store.connect() as conn:
        rows = conn.execute('''SELECT payload FROM artifact_versions WHERE book_id=? AND kind='audio_take'
            AND logical_key=? ORDER BY created_at DESC,rowid DESC''', (book_id, segment['id'])).fetchall()
    for (payload,) in rows:
        prior = json.loads(payload).get('audio') or {}
        if prior.get('fingerprint') == fingerprint:
            candidates.append(prior)
    for metadata in candidates:
        try:
            path = runtime.take_path(book_id, metadata)
            duration = validate_audio(path)
        except (OSError, EOFError, ValueError, KeyError, TypeError):
            continue
        return {key: value for key, value in metadata.items() if key not in ('resource_usage', 'url')} | {
            'duration': duration, 'asset_id': metadata.get('asset_id') or metadata['fingerprint']}
    return None


def _valid_file(runtime, book_id: str, asset_id: str) -> float | None:
    try:
        return validate_audio(runtime.audio_path(book_id, asset_id))
    except (OSError, EOFError, ValueError, KeyError, TypeError):
        return None


def _cast_work(runtime, job_id: str, record: dict, key, limits: dict, options: dict):
    store = runtime.store
    book_id, provider, model, performance_id = record['book_id'], record['provider'], record['model'], record['id']
    repository = PerformanceRepository(store)
    snapshot = record['cast_snapshot']
    book = store.book(book_id)
    scenes = {scene['id']: scene for scene in book['scenes']}
    chapters, passages = selection(book, record['chapter_ids'])
    rows = repository.rows(book_id)
    own: dict[tuple[str, str], list[dict]] = {}
    by_fingerprint: dict[str, list[dict]] = {}
    for pid, segment_id, source, body in rows:
        if pid == performance_id:
            own.setdefault((segment_id, source), []).append(body)
        by_fingerprint.setdefault(body.get('fingerprint'), []).append(body)
    work = []
    for number, chapter in enumerate(chapters, 1):
        segments = passages[chapter['id']]
        for index, segment in enumerate(segments):
            source = source_key(segment)
            if any(_valid_file(runtime, book_id, body['asset_id']) for body in own.get((segment['id'], source), [])):
                continue
            work.append((number, chapter, index, len(segments), segment, source))
    store.update_job(job_id, total=len(work), progress=0)
    for done, (number, chapter, index, count, segment, source) in enumerate(work):
        runtime.check_cancel(job_id)
        used_id, character, fallback = cast_voice(snapshot, segment['speaker_id'], provider, model)
        scene = scenes.get(segment.get('scene_id'), {})
        # Records made before pronunciations existed have no snapshot and keep their original recipes.
        rendered = pronunciation.with_lexicon(segment, record.get('pronunciation_snapshot'))
        fingerprint = render_fingerprint(rendered, character, scene, provider, model)
        store.update_job(job_id, message=f'Chapter {number} of {len(chapters)} · passage {index + 1} of {count}')
        metadata, reuse = None, None
        for body in by_fingerprint.get(fingerprint, []):
            duration = _valid_file(runtime, book_id, body['asset_id'])
            if duration is not None:
                metadata = {key: value for key, value in body.items()
                            if key not in ('resource_usage', 'reuse', 'created_at', 'speaker_id', 'character_id',
                                           'fallback', 'source', 'performance_id', 'segment_id')}
                metadata['duration'] = duration
                reuse = {'kind': 'performance_take', 'performance_id': body.get('performance_id'),
                         'segment_id': body.get('segment_id'), 'asset_id': body['asset_id']}
                break
        if metadata is None:
            studio = _studio_take(runtime, book_id, segment, fingerprint)
            if studio is not None:
                metadata, reuse = studio, {'kind': 'studio_take', 'segment_id': segment['id'], 'asset_id': studio['asset_id']}
        if metadata is not None:
            with ResourceLedger(store).operation(book_id, 'narration', run_id=job_id, unit_key=segment['id'],
                                                 chapter_id=chapter['id'], provider=provider, model=model,
                                                 cached=True, kind='narration') as metrics:
                metrics.update(audio_seconds=metadata['duration'],
                               output_bytes=runtime.audio_path(book_id, metadata['asset_id']).stat().st_size)
        else:
            try:
                metadata = _generate(runtime, job_id, book_id, rendered, character, scene, provider, model, key,
                                     limits, chapter['id'])
            except (InterruptedError, QuotaReached):  # includes BudgetReached
                raise
            except Exception as error:
                from .app import Cancelled  # imported here: app imports this module
                if isinstance(error, Cancelled):
                    raise
                title = chapter.get('title') or f'chapter {number}'
                raise RuntimeError(f'Passage {index + 1} of “{title}” could not be narrated: {error}') from error
        body = {**metadata, 'performance_id': performance_id, 'segment_id': segment['id'],
                'speaker_id': segment['speaker_id'], 'character_id': used_id, 'fallback': fallback,
                'source': {'chapter_id': segment['chapter_id'], 'start': segment.get('start'), 'end': segment.get('end')},
                'created_at': now(), **({'reuse': reuse} if reuse else {})}
        body = repository.retain(book_id, performance_id, segment['id'], source, body)
        by_fingerprint.setdefault(fingerprint, []).insert(0, body)
        store.update_job(job_id, progress=done + 1)


def _generate(runtime, job_id, book_id, segment, character, scene, provider, model, key, limits, chapter_id):
    """One passage take. A rejected (429) request is certain to be unprocessed and may be retried;
    an uncertain failure is never resent."""
    store = runtime.store
    audio_dir = store.root / 'audio' / book_id
    rejections = 0
    while True:
        runtime.check_cancel(job_id)
        if provider == 'gemini':
            used = requests_today(store, model)
            if LIMITER.daily_block(model) > 0 or used >= limits['rpd']:
                raise _quota_error(
                    f'This library has used {used} of {limits["rpd"]} daily Gemini requests for this model. '
                    'Finished audio is saved; resume after midnight Pacific time.' if used >= limits['rpd'] else
                    'The daily Gemini request quota for this model is used up. Finished audio is saved; '
                    'resume after midnight Pacific time.', model)
        try:
            with ResourceLedger(store).operation(book_id, 'narration', run_id=job_id, unit_key=segment['id'],
                                                 chapter_id=chapter_id, provider=provider, model=model,
                                                 kind='narration') as metrics:
                metadata = produce_take(segment, character, scene, provider, model, key, audio_dir, synthesizer=synthesize)
                metrics.update(audio_seconds=metadata['duration'],
                               output_bytes=runtime.audio_path(book_id, metadata['asset_id']).stat().st_size)
            return metadata
        except RateLimited as error:
            if error.scope == 'day':
                raise _quota_error('The daily Gemini request quota for this model is used up. Finished audio is saved; '
                                   'resume after midnight Pacific time.', model) from None
            rejections += 1
            if rejections > MAX_CONSECUTIVE_RATE_LIMITS:
                raise RuntimeError('Gemini kept rejecting requests for its rate limit. Finished audio is saved; '
                                   'try again later.') from None
