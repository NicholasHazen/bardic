"""Single-voice listening, isolated from the enhanced production projection.

Sessions describe a narrator choice, not character casting. Takes are retained
under their exact source and voice recipe in a separate append-only archive.
Only render_passage can synthesize; inspecting sessions or cached takes is local.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

from .alignment import align_file
from .audio import (AudioError, BREEZE_MODEL, ContentBlocked, DEFAULT_TTS_MODEL, PROVIDERS, SYSTEM_MODEL, TTS_MODELS, render_fingerprint,
                    validate_audio)
from .chunking import CHUNKING_VERSION, OUTPUT_TOKEN_CAP, PROVIDER_AUDIO_CAP_SECONDS
from .store import now
from . import pronunciation
from .take_archive import produce_take
from .audio_refs import audio_ref
from .errors import Conflict, Invalid, NotFound


VERSION = 1
SYNTHESIS_CACHE_VERSION = 1
# Version 1 multi-passage takes: one WAV for an exact chapter slice, with
# estimated passage clips from pause alignment. See docs/DATA-MODEL.md.
CHUNK_VERSION = 1
# Version 1 records of text Gemini refused under its content policy, and of the fallback narrator's takes
# that stand in for those passages. See docs/DATA-MODEL.md.
BLOCKED_VERSION = 1
SUBSTITUTE_VERSION = 1
# A block of these roles is final for its passages: 'half' (one half of a split chunk) and 'single'
# (a one-passage request). A 'chunk' block is open: it is split once into halves.
FINAL_BLOCK_ROLES = ('half', 'single')
# Faster than this many code points per audio second suggests skipped text.
_SUSPICIOUS_CHARS_PER_SECOND = 26.0
# Integrity results per asset file state: (root, book, asset, size, mtime_ns) -> ok.
_asset_checks: dict[tuple, bool] = {}


class KnownContentBlock(ContentBlocked):
    """Bardic already knows Gemini blocked this text, so no request was sent."""

    def __init__(self):
        super().__init__("Gemini already blocked this text under its content policy, so it was not sent again.")


class TruncatedChunk(AudioError):
    """The provider returned incomplete audio for the chunk text; nothing was retained."""

    def __init__(self, chars: int, duration: float, message: str | None = None):
        super().__init__(message or 'Gemini stopped at its audio length limit before finishing this chunk. '
                         'Smaller chunks will be requested.')
        self.chars = chars
        self.duration = duration


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


# Optional public extras of a single-passage take, copied when the stored record has them.
# Provider usage is served by the resources routes, not with the audio.
_PASSAGE_EXTRAS = ('provider_timing', 'breeze', 'voice_revision', 'substitute')
# Public fields of a reuse pointer; its recipe and producer fingerprint stay in storage.
_REUSE_FIELDS = ('schema_version', 'take_id', 'book_id', 'session_id', 'segment_id')


def present_take(book_id, metadata):
    """The public audio object (contract ``ListeningPassageAudio``) for a retained take.

    A whitelist: recipe hashes (also those of a reuse pointer), provider usage,
    source anchors, lookup keys and any transient marker stay in storage. It is
    idempotent, so it also cleans an audio object presented by an earlier
    version (for example inside a stored job).
    """
    reuse = metadata.get('reuse')
    extras = {key: metadata[key] for key in _PASSAGE_EXTRAS if key in metadata}
    if isinstance(reuse, dict):
        extras['reuse'] = {key: reuse.get(key) for key in _REUSE_FIELDS}
    return audio_ref(f'/api/books/{quote(book_id, safe="")}/listen/audio/{metadata["asset_id"]}',
                     asset_id=metadata['asset_id'], duration=metadata.get('duration'),
                     provider=metadata.get('provider'), model=metadata.get('model'), voice=metadata.get('voice'),
                     created_at=metadata.get('created_at'), session_id=metadata.get('session_id'),
                     segment_id=metadata.get('segment_id'), **extras)


def present_clip(book_id, clip):
    """The public audio object (contract ``ListeningChunkClipAudio``) for one passage's chunk clip."""
    return audio_ref(f'/api/books/{quote(book_id, safe="")}/listen/audio/{clip["asset_id"]}',
                     asset_id=clip['asset_id'], duration=clip.get('duration'), provider=clip.get('provider'),
                     model=clip.get('model'), voice=clip.get('voice'), created_at=clip.get('created_at'),
                     segment_id=clip.get('segment_id'), chunk_id=clip['chunk_id'], clip_start=clip.get('clip_start'),
                     clip_end=clip.get('clip_end'), chunk_duration=clip.get('chunk_duration'),
                     timing=clip.get('timing', 'estimated'), session_id=clip.get('session_id'),
                     flags=list(clip.get('flags') or []))


def require_active_book(store, book_id):
    """404 ``book_not_found`` for an unknown book, 409 ``book_archived`` for an archived one."""
    store.book(book_id)
    if store.is_archived(book_id):
        raise Conflict('book_archived', 'This book is archived.')


def present_audio(book_id, audio):
    """Re-present any simple-listening audio object, including one stored by an earlier version."""
    return present_clip(book_id, audio) if 'chunk_id' in audio else present_take(book_id, audio)


class ListeningRepository:
    def __init__(self, store):
        self.store = store
        # Schema setup runs once per store, never on each request.
        if getattr(store, '_listening_schema_ready', False):
            return
        with store.lock, store.connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS listening_sessions (
                book_id TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY(book_id,id))''')
            conn.execute('''CREATE TABLE IF NOT EXISTS listening_takes (
                id TEXT PRIMARY KEY, book_id TEXT NOT NULL, session_id TEXT NOT NULL,
                segment_id TEXT NOT NULL, recipe TEXT NOT NULL, asset_id TEXT NOT NULL,
                body TEXT NOT NULL)''')
            conn.execute('''CREATE INDEX IF NOT EXISTS listening_recipe
                ON listening_takes(book_id,session_id,segment_id,recipe)''')
            # An index of retained takes, not another mutable take selection.
            # Source/session identities remain in listening_takes; equivalent
            # speech inputs may reuse bytes without pretending to generate them.
            conn.execute('''CREATE TABLE IF NOT EXISTS listening_synthesis_cache (
                content_key TEXT NOT NULL, take_id TEXT NOT NULL,
                PRIMARY KEY(content_key,take_id),
                FOREIGN KEY(take_id) REFERENCES listening_takes(id))''')
            # One immutable row per multi-passage request. Passage audio is a
            # projection of valid chunks, never a rewritten single-passage take.
            conn.execute('''CREATE TABLE IF NOT EXISTS listening_chunks (
                id TEXT PRIMARY KEY, book_id TEXT NOT NULL, session_id TEXT NOT NULL,
                chapter_id TEXT NOT NULL, asset_id TEXT NOT NULL, body TEXT NOT NULL)''')
            conn.execute('''CREATE INDEX IF NOT EXISTS listening_chunks_session
                ON listening_chunks(book_id,session_id,chapter_id)''')
            # REPLACE bypasses delete triggers; a duplicate insert is skipped
            # so INSERT OR IGNORE still reports the first retained record.
            conn.execute('''CREATE TRIGGER IF NOT EXISTS listening_chunks_no_replace
                BEFORE INSERT ON listening_chunks WHEN EXISTS(SELECT 1 FROM listening_chunks WHERE id=NEW.id)
                BEGIN SELECT RAISE(IGNORE); END''')
            # Immutable facts: this exact text and recipe was refused by the provider (no response body is kept),
            # and which fallback narrator's take stands in for a refused passage.
            conn.execute('''CREATE TABLE IF NOT EXISTS listening_blocked (
                id TEXT PRIMARY KEY, book_id TEXT NOT NULL, session_id TEXT NOT NULL,
                chapter_id TEXT NOT NULL, body TEXT NOT NULL)''')
            conn.execute('''CREATE INDEX IF NOT EXISTS listening_blocked_session
                ON listening_blocked(book_id,session_id,chapter_id)''')
            conn.execute('''CREATE TABLE IF NOT EXISTS listening_substitutes (
                id TEXT PRIMARY KEY, book_id TEXT NOT NULL, session_id TEXT NOT NULL,
                segment_id TEXT NOT NULL, body TEXT NOT NULL)''')
            conn.execute('''CREATE INDEX IF NOT EXISTS listening_substitutes_session
                ON listening_substitutes(book_id,session_id,segment_id)''')
            for table in ('listening_blocked', 'listening_substitutes'):
                conn.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_replace
                    BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE id=NEW.id)
                    BEGIN SELECT RAISE(IGNORE); END''')
            for table in ('listening_takes', 'listening_chunks', 'listening_blocked', 'listening_substitutes'):
                for operation in ('UPDATE', 'DELETE'):
                    name = f'{table}_no_{operation.lower()}'
                    conn.execute(f'''CREATE TRIGGER IF NOT EXISTS {name}
                        BEFORE {operation} ON {table} BEGIN
                        SELECT RAISE(ABORT, 'Listening takes are immutable'); END''')
        store._listening_schema_ready = True

    def session(self, book_id, provider, voice=None, model=None, *, selection=None):
        """Resolve a narrator choice. ``selection`` is the pinned Breeze voice
        ``{id, revision, seed}``; it becomes part of the session identity, so a
        voice changed on the server starts a new session and keeps old takes."""
        self.store.book(book_id)
        if provider not in PROVIDERS:
            raise Invalid('provider_unsupported', 'The narration provider must be system, gemini or breeze.')
        if voice is not None and (not isinstance(voice, str) or len(voice) > 256):
            raise Invalid('narrator_voice_invalid', 'The narrator voice is not a valid voice value.')
        voice = voice.strip() if voice else ''
        pinned = {}
        if provider == 'system':
            if model not in (None, '', SYSTEM_MODEL):
                raise Invalid('model_unsupported', f'Device narration uses the {SYSTEM_MODEL} model.')
            model = SYSTEM_MODEL
        elif provider == 'breeze':
            if model not in (None, '', BREEZE_MODEL):
                raise Invalid('model_unsupported', f'Breeze narration uses the {BREEZE_MODEL} model.')
            if not isinstance(selection, dict) or (voice and selection.get('id') != voice):
                raise Invalid('narrator_voice_invalid', 'The Breeze voice is not in the last Breeze voice check.')
            voice, model = selection['id'], BREEZE_MODEL
            # Only speech-affecting pins; Gemini and device sessions keep their original identity.
            pinned = {'voice_revision': selection.get('revision'), 'seed': selection.get('seed'),
                      **({'settings': selection['settings']} if selection.get('settings') else {})}
        else:
            voice = voice or 'Kore'
            model = model or DEFAULT_TTS_MODEL
            if model not in TTS_MODELS:
                raise Invalid('model_unsupported', 'The Gemini speech model is not a supported TTS model.')
        config = {'schema_version': VERSION, 'book_id': book_id, 'provider': provider,
                  'voice': voice, 'model': model, **pinned}
        # Provider validation is pure; it neither queries installed voices nor calls a model.
        try:
            self._audio_recipe({'id': 'configuration-check', 'text': 'Voice configuration'}, config)
        except AudioError as error:
            raise Invalid('narrator_voice_invalid', str(error)) from None
        config['id'] = _hash(config)
        with self.store.lock, self.store.connect() as conn:
            conn.execute('INSERT OR IGNORE INTO listening_sessions VALUES (?,?,?)',
                         (book_id, config['id'], json.dumps(config)))
        return config

    def get_session(self, book_id, session_id):
        self.store.book(book_id)
        with self.store.lock, self.store.connect() as conn:
            row = conn.execute('SELECT body FROM listening_sessions WHERE book_id=? AND id=?',
                               (book_id, session_id)).fetchone()
        if not row:
            raise NotFound('listening_session_not_found', 'Listening session not found')
        return json.loads(row[0])

    @staticmethod
    def _audio_recipe(segment, session, lexicon=None):
        # Deliberately copy only the transcript identity. No inferred speaker,
        # stage direction, character trait, emotion, or cue enters simple mode.
        # The book's pronunciations do: they belong to the text, not the cast.
        passage = pronunciation.with_lexicon({'id': segment['id'], 'text': segment['text']}, lexicon)
        narrator = {'id': 'simple-narrator', 'voice': session['voice'] or 'Kore',
                    'system_voice': session['voice']}
        if session['provider'] == 'breeze':
            narrator['voices'] = {'breeze': {'id': session['voice'], 'revision': session.get('voice_revision'),
                                             'seed': session.get('seed'),
                                             **({'settings': session['settings']} if session.get('settings') else {})}}
        fingerprint = render_fingerprint(passage, narrator, {}, session['provider'], session['model'])
        return passage, narrator, fingerprint

    @classmethod
    def _synthesis_key(cls, passage, session):
        # This constant ID is ONLY a lookup identity. Never pass it to synthesis
        # or publish its fingerprint as the source-bound generated fingerprint.
        fingerprint = cls._audio_recipe({'id': 'simple-speech-content', 'text': passage['text']}, session,
                                        passage.get('pronunciations'))[-1]
        return _hash({'schema_version': SYNTHESIS_CACHE_VERSION, 'fingerprint': fingerprint})

    def _validated_asset(self, book_id, asset_id):
        path = self._path(book_id, asset_id)
        with path.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != asset_id:
                raise AudioError('Saved narration failed its content integrity check.')
        return validate_audio(path)

    def _index(self, content_key, take_id):
        with self.store.lock, self.store.connect() as conn:
            conn.execute('INSERT OR IGNORE INTO listening_synthesis_cache VALUES (?,?)', (content_key, take_id))

    def _retain(self, book_id, session_id, segment_id, identity, recipe, content_key, metadata):
        metadata = {**metadata, 'session_id': session_id, 'segment_id': segment_id,
                    'recipe': recipe, 'source_anchor': identity, 'synthesis_key': content_key,
                    'created_at': now()}
        # A new lookup format/version must not collide with an older immutable
        # row if deterministic synthesis happens to return the same WAV bytes.
        # Legacy identifiers remain accepted by exact-source cache reads.
        identifier = _hash([book_id, recipe, metadata['asset_id'], content_key])
        with self.store.lock, self.store.connect() as conn:
            conn.execute('INSERT OR IGNORE INTO listening_takes VALUES (?,?,?,?,?,?,?)',
                         (identifier, book_id, session_id, segment_id, recipe, metadata['asset_id'], json.dumps(metadata)))
            conn.execute('INSERT OR IGNORE INTO listening_synthesis_cache VALUES (?,?)', (content_key, identifier))
            # If a concurrent read already retained this take, report its real
            # first creation/provenance, not the losing caller's proposed row.
            return json.loads(conn.execute('SELECT body FROM listening_takes WHERE id=?', (identifier,)).fetchone()[0])

    def _copy_asset(self, source_book_id, book_id, asset_id):
        source, target = self._path(source_book_id, asset_id), self._path(book_id, asset_id)
        if source == target:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        # Copy rather than hardlink the retained source so accidental damage to
        # one book's file cannot also corrupt the other book's saved narration.
        with tempfile.TemporaryDirectory(prefix='.reuse-take-', dir=target.parent) as directory:
            temporary = Path(directory) / 'take.wav'
            shutil.copyfile(source, temporary)
            with temporary.open('rb') as copied:
                if hashlib.file_digest(copied, 'sha256').hexdigest() != asset_id:
                    raise AudioError('Saved narration changed while it was being reused.')
                os.fsync(copied.fileno())
            validate_audio(temporary)
            try:
                os.link(temporary, target)
            except FileExistsError:
                # Never overwrite a retained asset, even when its path is bad.
                self._validated_asset(book_id, asset_id)

    def _inputs(self, book_id, session_id, segment_id):
        session = self.get_session(book_id, session_id)
        book = self.store.book(book_id)
        segment = next((s for s in book['segments'] if s['id'] == segment_id), None)
        if segment is None:
            raise Invalid('unknown_passage', 'No passage with this ID is in the book.')
        chapter = next((c for c in book['chapters'] if c['id'] == segment['chapter_id']), None)
        return self._source_inputs(book_id, session, segment, chapter, pronunciation.book_lexicon(book))

    def _source_inputs(self, book_id, session, segment, chapter, lexicon=None):
        start, end = segment.get('start'), segment.get('end')
        if (chapter is None or type(start) is not int or type(end) is not int or
                not 0 <= start < end <= len(chapter['text']) or chapter['text'][start:end] != segment['text']):
            raise Invalid('passage_source_mismatch', 'The passage text does not match its original source coordinates.')
        passage, narrator, fingerprint = self._audio_recipe(segment, session, lexicon)
        identity = {'schema_version': VERSION, 'book_id': book_id, 'session_id': session['id'],
                    'chapter_id': chapter['id'], 'segment_id': segment['id'],
                    'start': start, 'end': end, 'fingerprint': fingerprint}
        return session, passage, narrator, identity, _hash(identity)

    def _path(self, book_id, asset_id):
        if not isinstance(book_id, str) or re.fullmatch(r'[a-zA-Z0-9_-]+', book_id) is None:
            raise NotFound('audio_not_found', 'Listening audio not found')
        if not isinstance(asset_id, str) or re.fullmatch(r'[a-f0-9]{64}', asset_id) is None:
            raise NotFound('audio_not_found', 'Listening audio not found')
        return self.store.root / 'listen-audio' / book_id / f'{asset_id}.wav'

    def asset_path(self, book_id, asset_id):
        self.store.book(book_id)
        target = self._path(book_id, asset_id)
        with self.store.lock, self.store.connect() as conn:
            found = (conn.execute('SELECT 1 FROM listening_takes WHERE book_id=? AND asset_id=? LIMIT 1',
                                  (book_id, asset_id)).fetchone() or
                     conn.execute('SELECT 1 FROM listening_chunks WHERE book_id=? AND asset_id=? LIMIT 1',
                                  (book_id, asset_id)).fetchone())
        if not found or not target.is_file():
            raise NotFound('audio_not_found', 'Listening audio not found')
        return target

    def cached(self, book_id, session_id, segment_id):
        """Retained audio for the passage (public shape), or None. Never contacts a provider."""
        session, passage, _, identity, recipe = self._inputs(book_id, session_id, segment_id)
        clip = self.chunk_clips(book_id, session_id, verify=True).get(segment_id)
        if clip:
            return clip
        content_key = self._synthesis_key(passage, session)
        with self.store.lock, self.store.connect() as conn:
            rows = conn.execute('''SELECT id,body FROM listening_takes WHERE book_id=? AND session_id=?
                AND segment_id=? AND recipe=? ORDER BY rowid DESC''',
                                (book_id, session_id, segment_id, recipe)).fetchall()
        for take_id, body in rows:
            metadata = json.loads(body)
            if metadata.get('synthesis_key', content_key) != content_key:
                continue
            try:
                duration = self._validated_asset(book_id, metadata['asset_id'])
            except (OSError, EOFError, ValueError, KeyError):
                continue
            self._index(content_key, take_id)
            return present_take(book_id, {**metadata, 'duration': duration})
        with self.store.lock, self.store.connect() as conn:
            rows = conn.execute('''SELECT t.id,t.book_id,t.body FROM listening_synthesis_cache c
                JOIN listening_takes t ON t.id=c.take_id
                WHERE c.content_key=? ORDER BY t.rowid DESC''', (content_key,)).fetchall()
        for take_id, source_book_id, body in rows:
            original = json.loads(body)
            try:
                duration = self._validated_asset(source_book_id, original['asset_id'])
            except (OSError, EOFError, ValueError, KeyError):
                continue
            try:
                self._copy_asset(source_book_id, book_id, original['asset_id'])
            except (EOFError, ValueError):
                # The source changed while copying, or this book already holds a damaged file
                # under that asset ID (never overwritten). Damaged stored data is skipped like
                # a damaged source, so the passage can be generated. An OSError (for example a
                # full disk) still propagates: it must not start a paid fallback.
                continue
            # The producer fingerprint remains the real original fingerprint.
            # The target source-bound recipe is recorded in source_anchor;
            # reuse explicitly points to the actual retained input take.
            reused = {key: value for key, value in original.items() if key != 'resource_usage'}
            reused.update(duration=duration, reuse={'schema_version': 1, 'take_id': take_id,
                          'book_id': source_book_id, 'session_id': original['session_id'],
                          'segment_id': original['segment_id'], 'recipe': original['recipe'],
                          'fingerprint': original['fingerprint']})
            metadata = self._retain(book_id, session_id, segment_id, identity, recipe, content_key, reused)
            return present_take(book_id, metadata)
        # Last: audio Gemini made for this text (here or in another book) always wins over a stand-in.
        return self._substitutes(self.store.book(book_id), session).get(segment_id)

    def takes(self, book_id, session_id):
        session = self.get_session(book_id, session_id)
        book = self.store.book(book_id)
        with self.store.lock, self.store.connect() as conn:
            rows = conn.execute('''SELECT segment_id,recipe,body FROM listening_takes
                WHERE book_id=? AND session_id=? ORDER BY rowid DESC''', (book_id, session_id)).fetchall()
        saved = {}
        for segment_id, recipe, body in rows:
            saved.setdefault((segment_id, recipe), []).append(json.loads(body))
        chapters = {chapter['id']: chapter for chapter in book['chapters']}
        # Chunk clips come first: consecutive clips in one WAV play gaplessly.
        clips = self._chunk_clips(book, session)
        lexicon = pronunciation.book_lexicon(book)
        # A fallback narrator's take stands in only where Gemini has no audio of its own for the passage.
        substitutes = self._substitutes(book, session)
        takes = []
        for segment in book['segments']:
            if segment['id'] in clips:
                takes.append({'segment_id': segment['id'], 'audio': clips[segment['id']]})
                continue
            try:
                recipe = self._source_inputs(book_id, session, segment, chapters.get(segment['chapter_id']), lexicon)[-1]
            except ValueError:
                continue
            for metadata in saved.get((segment['id'], recipe), []):
                # Listing a finished novel must not reread gigabytes of WAV
                # samples. Assets were fully validated when archived; the
                # per-passage cache path validates again before synthesis reuse.
                if self._path(book_id, metadata['asset_id']).is_file():
                    takes.append({'segment_id': segment['id'], 'audio': present_take(book_id, metadata)})
                    break
            else:
                if segment['id'] in substitutes:
                    takes.append({'segment_id': segment['id'], 'audio': substitutes[segment['id']]})
        return {'session': session, 'takes': takes}

    # Multi-passage chunks -------------------------------------------------

    def _asset_state(self, book_id, asset_id, *, verify):
        """True/False once a file state was checked; None when unverified.

        Listing uses only prior results (no rereading of large WAVs); cache
        reuse verifies. A changed file (size or mtime) is checked again, and a
        chunk known to be damaged is excluded from every projection.
        """
        path = self._path(book_id, asset_id)
        try:
            stat = path.stat()
        except OSError:
            return False
        key = (str(self.store.root), book_id, asset_id, stat.st_size, stat.st_mtime_ns)
        if key not in _asset_checks and verify:
            try:
                self._validated_asset(book_id, asset_id)
                _asset_checks[key] = True
            except (OSError, EOFError, ValueError, KeyError):
                _asset_checks[key] = False
        return _asset_checks.get(key)

    @staticmethod
    def _chunk_recipe(chapter_id, start, end, text, session, lexicon=None):
        source_id = 'chunk-' + _hash([chapter_id, start, end])[:24]
        return ListeningRepository._audio_recipe({'id': source_id, 'text': text}, session, lexicon)

    @classmethod
    def _chunk_valid(cls, chunk, chapters, segments, session, lexicon=None, version=CHUNK_VERSION):
        """A chunk applies only while its source slice, anchors and recipe are unchanged."""
        chapter = chapters.get(chunk.get('chapter_id'))
        if chunk.get('schema_version') != version or chunk.get('session_id') != session['id'] or not chapter:
            return False
        start, end = chunk.get('start'), chunk.get('end')
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(chapter['text']):
            return False
        text = chapter['text'][start:end]
        if hashlib.sha256(text.encode()).hexdigest() != chunk.get('text_sha256'):
            return False
        for segment_id, segment_start, segment_end in chunk.get('segments', []):
            segment = segments.get(segment_id)
            if (not segment or segment['chapter_id'] != chapter['id'] or segment['start'] != segment_start or
                    segment['end'] != segment_end or chapter['text'][segment_start:segment_end] != segment['text']):
                return False
        try:
            # A recipe version, narrator or pronunciation change invalidates chunks exactly as it does single takes.
            if cls._chunk_recipe(chapter['id'], start, end, text, session, lexicon)[-1] != chunk.get('fingerprint'):
                return False
        except AudioError:
            return False
        return bool(chunk.get('segments'))

    @staticmethod
    def _present_clip(chunk, clip):
        return present_clip(chunk['book_id'], {
            'segment_id': clip['segment_id'], 'asset_id': chunk['asset_id'], 'chunk_id': chunk['id'],
            'clip_start': clip['start'], 'clip_end': clip['end'], 'duration': round(clip['end'] - clip['start'], 3),
            'chunk_duration': chunk['duration'], 'timing': 'estimated', 'provider': chunk['provider'],
            'model': chunk['model'], 'voice': chunk['voice'], 'session_id': chunk['session_id'],
            'created_at': chunk['created_at'], 'flags': chunk.get('flags', [])})

    def _chunk_clips(self, book, session, *, verify=False):
        with self.store.lock, self.store.connect() as conn:
            rows = conn.execute('''SELECT body FROM listening_chunks WHERE book_id=? AND session_id=?
                ORDER BY rowid DESC''', (book['id'], session['id'])).fetchall()
        chapters = {chapter['id']: chapter for chapter in book['chapters']}
        segments = {segment['id']: segment for segment in book['segments']}
        lexicon = pronunciation.book_lexicon(book)
        clips = {}
        for (body,) in rows:
            chunk = json.loads(body)
            if not self._chunk_valid(chunk, chapters, segments, session, lexicon):
                continue
            if self._asset_state(book['id'], chunk['asset_id'], verify=verify) is False:
                continue
            for clip in chunk['timing']['clips']:
                clips.setdefault(clip['segment_id'], self._present_clip(chunk, clip))
        return clips

    def chunk_clips(self, book_id, session_id, *, verify=False):
        return self._chunk_clips(self.store.book(book_id), self.get_session(book_id, session_id), verify=verify)

    def chapter_segments(self, book, chapter_id):
        chapter = next((c for c in book['chapters'] if c['id'] == chapter_id), None)
        if chapter is None:
            raise NotFound('chapter_not_found', 'Chapter not found in this book')
        return chapter, [segment for segment in book['segments'] if segment['chapter_id'] == chapter_id]

    def _chunk_inputs(self, book_id, session, chapter_id, segment_ids):
        """Exact slice, recipe and identity of a request for consecutive passages of one chapter."""
        book = self.store.book(book_id)
        chapter, ordered = self.chapter_segments(book, chapter_id)
        positions = {segment['id']: index for index, segment in enumerate(ordered)}
        indexes = [positions.get(segment_id) for segment_id in segment_ids]
        if not segment_ids or None in indexes or indexes != list(range(indexes[0], indexes[0] + len(indexes))):
            raise ValueError('A chunk must be consecutive passages from one chapter.')
        selected = [ordered[index] for index in indexes]
        lexicon = pronunciation.book_lexicon(book)
        for segment in selected:
            self._source_inputs(book_id, session, segment, chapter, lexicon)
        start, end = selected[0]['start'], selected[-1]['end']
        text = chapter['text'][start:end]
        passage, narrator, fingerprint = self._chunk_recipe(chapter_id, start, end, text, session, lexicon)
        identity = {'schema_version': CHUNK_VERSION, 'chunking_version': CHUNKING_VERSION,
                    'book_id': book_id, 'session_id': session['id'], 'chapter_id': chapter_id,
                    'start': start, 'end': end, 'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
                    'segments': [[s['id'], s['start'], s['end']] for s in selected], 'fingerprint': fingerprint}
        return {'book': book, 'chapter': chapter, 'selected': selected, 'lexicon': lexicon, 'start': start, 'end': end,
                'text': text, 'passage': passage, 'narrator': narrator, 'identity': identity}

    # Text Gemini refused ---------------------------------------------------

    def record_block(self, book_id, session_id, chapter_id, segment_ids, role, request=None):
        """Remember durably that Gemini refused this exact text under this recipe. Idempotent; keeps no response body."""
        session = self.get_session(book_id, session_id)
        inputs = self._chunk_inputs(book_id, session, chapter_id, segment_ids)
        identity = inputs['identity']
        body = {**{key: identity[key] for key in ('book_id', 'session_id', 'chapter_id', 'start', 'end', 'text_sha256',
                                                  'segments', 'fingerprint')},
                'schema_version': BLOCKED_VERSION, 'role': role, 'code': ContentBlocked.code,
                'request': request or {}, 'created_at': now()}
        body['id'] = _hash([book_id, session_id, chapter_id, identity['start'], identity['end'],
                            identity['text_sha256'], identity['fingerprint'], role])
        with self.store.lock, self.store.connect() as conn:
            conn.execute('INSERT OR IGNORE INTO listening_blocked VALUES (?,?,?,?,?)',
                         (body['id'], book_id, session_id, chapter_id, json.dumps(body)))
            return json.loads(conn.execute('SELECT body FROM listening_blocked WHERE id=?', (body['id'],)).fetchone()[0])

    def _valid_blocks(self, book, session):
        """Retained blocks that still describe the passage source and the session's recipe, newest first."""
        with self.store.lock, self.store.connect() as conn:
            rows = conn.execute('SELECT body FROM listening_blocked WHERE book_id=? AND session_id=? ORDER BY rowid DESC',
                                (book['id'], session['id'])).fetchall()
        if not rows:
            return []
        chapters = {chapter['id']: chapter for chapter in book['chapters']}
        segments = {segment['id']: segment for segment in book['segments']}
        lexicon = pronunciation.book_lexicon(book)
        records = (json.loads(body) for (body,) in rows)
        return [record for record in records
                if record.get('schema_version') == BLOCKED_VERSION and
                self._chunk_valid({**record, 'schema_version': CHUNK_VERSION}, chapters, segments, session, lexicon)]

    def has_blocks(self, book_id, session_id):
        """Whether any block was ever retained for the session: a cheap check that avoids loading the book."""
        with self.store.lock, self.store.connect() as conn:
            return conn.execute('SELECT 1 FROM listening_blocked WHERE book_id=? AND session_id=? LIMIT 1',
                                (book_id, session_id)).fetchone() is not None

    def content_blocks(self, book_id, session_id):
        """What is known about text Gemini refused: ``refused`` maps each passage that must not be requested again
        (final blocks) to its block ID; ``open`` lists blocks of whole chunks that are still to be split."""
        book, session = self.store.book(book_id), self.get_session(book_id, session_id)
        blocks = self._valid_blocks(book, session)
        refused = {}
        for record in blocks:
            if record['role'] in FINAL_BLOCK_ROLES:
                for segment_id, _, _ in record['segments']:
                    refused.setdefault(segment_id, record['id'])
        return {'refused': refused, 'open': [record for record in blocks if record['role'] not in FINAL_BLOCK_ROLES],
                'blocks': blocks}

    @staticmethod
    def known_block_within(blocks, chapter_id, start, end):
        """A retained block whose text lies entirely inside [start, end): sending that slice would resend it."""
        return next((record for record in blocks if record['chapter_id'] == chapter_id
                     and start <= record['start'] and record['end'] <= end), None)

    def _retain_substitute(self, book_id, session_id, segment_id, block_id, fallback_session_id, asset_id):
        body = {'schema_version': SUBSTITUTE_VERSION, 'session_id': session_id, 'segment_id': segment_id,
                'blocked_id': block_id, 'reason': ContentBlocked.code, 'fallback_session_id': fallback_session_id,
                'asset_id': asset_id, 'created_at': now()}
        body['id'] = _hash([book_id, session_id, segment_id, block_id, fallback_session_id, asset_id])
        with self.store.lock, self.store.connect() as conn:
            conn.execute('INSERT OR IGNORE INTO listening_substitutes VALUES (?,?,?,?,?)',
                         (body['id'], book_id, session_id, segment_id, json.dumps(body)))
        return body

    def read_by_fallback(self, book_id, session_id, segment_id, fallback, credentials=None, *,
                         synthesizer=None, check_cancel=lambda: None):
        """Have the fallback narrator read a passage Gemini refused, and link its retained take to the block.

        The take is an ordinary immutable take of the fallback session (its own recipe and content hash);
        nothing is written into the Gemini session's takes. Returns the public audio object.
        """
        block_id = self.content_blocks(book_id, session_id)['refused'].get(segment_id)
        if block_id is None:
            raise ValueError('Only a passage Gemini refused can be read by the fallback narrator.')
        audio = self.render_passage(book_id, fallback['session_id'], segment_id, credentials,
                                    synthesizer=synthesizer, check_cancel=check_cancel)
        self._retain_substitute(book_id, session_id, segment_id, block_id, fallback['session_id'], audio['asset_id'])
        return self._substitutes(self.store.book(book_id), self.get_session(book_id, session_id))[segment_id]

    def _substitutes(self, book, session):
        """Current fallback takes standing in for refused passages, in public shape, by passage ID."""
        with self.store.lock, self.store.connect() as conn:
            rows = conn.execute('SELECT body FROM listening_substitutes WHERE book_id=? AND session_id=? ORDER BY rowid DESC',
                                (book['id'], session['id'])).fetchall()
        if not rows:
            return {}
        block_ids = {record['id'] for record in self._valid_blocks(book, session) if record['role'] in FINAL_BLOCK_ROLES}
        chapters = {chapter['id']: chapter for chapter in book['chapters']}
        segments = {segment['id']: segment for segment in book['segments']}
        lexicon = pronunciation.book_lexicon(book)
        found = {}
        for (body,) in rows:
            record = json.loads(body)
            segment = segments.get(record['segment_id'])
            if segment is None or record['segment_id'] in found or record['blocked_id'] not in block_ids:
                continue
            try:
                other = self.get_session(book['id'], record['fallback_session_id'])
                recipe = self._source_inputs(book['id'], other, segment, chapters.get(segment['chapter_id']), lexicon)[-1]
            except (NotFound, ValueError):
                continue
            with self.store.lock, self.store.connect() as conn:
                take = conn.execute('''SELECT body FROM listening_takes WHERE book_id=? AND session_id=? AND segment_id=?
                    AND recipe=? AND asset_id=? ORDER BY rowid DESC''',
                                    (book['id'], other['id'], record['segment_id'], recipe, record['asset_id'])).fetchone()
            if take and self._path(book['id'], record['asset_id']).is_file():
                found[record['segment_id']] = present_take(book['id'], {
                    **json.loads(take[0]), 'substitute': {'reason': ContentBlocked.code, 'for_provider': session['provider'],
                                                          'for_model': session['model']}})
        return found

    def render_chunk(self, book_id, session_id, chapter_id, segment_ids, api_key=None, *,
                     synthesizer=None, check_cancel=lambda: None, request=None, block_role='chunk'):
        """Synthesize one exact chapter slice and retain it with estimated passage clips.

        Text Gemini is already known to have blocked is never sent again (``KnownContentBlock``). A refusal
        during this request is retained under ``block_role`` and raised as ``ContentBlocked``.
        """
        check_cancel()
        session = self.get_session(book_id, session_id)
        inputs = self._chunk_inputs(book_id, session, chapter_id, segment_ids)
        book, chapter, selected, lexicon = inputs['book'], inputs['chapter'], inputs['selected'], inputs['lexicon']
        start, end, text, passage, narrator, identity = (inputs[key] for key in ('start', 'end', 'text', 'passage', 'narrator', 'identity'))
        if session['provider'] == 'gemini' and self.known_block_within(self._valid_blocks(book, session), chapter_id, start, end):
            raise KnownContentBlock()
        # Boundaries are estimated from what was spoken, so respelled names weigh as heard.
        alignment_input = [{'id': segment['id'],
                            'text': pronunciation.apply(segment['text'], lexicon, session['provider'])[0],
                            'gap_after': chapter['text'][segment['end']:selected[i + 1]['start']] if i + 1 < len(selected) else ''}
                           for i, segment in enumerate(selected)]

        def accept(path, metadata):
            duration = metadata['duration']
            output_tokens = (metadata.get('resource_usage') or {}).get('output_tokens')
            if session['provider'] == 'gemini' and (
                    duration >= PROVIDER_AUDIO_CAP_SECONDS - 4 or
                    (type(output_tokens) is int and output_tokens >= OUTPUT_TOKEN_CAP - 32)):
                raise TruncatedChunk(len(text), duration)
            if len(text) / duration > _SUSPICIOUS_CHARS_PER_SECOND:
                # Far too little audio for the text: the provider stopped early
                # or skipped prose. Never make it permanent passage audio.
                raise TruncatedChunk(len(text), duration, 'Gemini returned much less audio than this chunk needs, '
                                     'so it was not saved. Smaller chunks will be requested.')
            # Recheck the source before the asset is published, not after.
            latest = self.store.book(book_id)
            latest_chapter, latest_ordered = self.chapter_segments(latest, chapter_id)
            if not self._chunk_valid({**identity, 'session_id': session['id']}, {latest_chapter['id']: latest_chapter},
                                     {segment['id']: segment for segment in latest_ordered}, session,
                                     pronunciation.book_lexicon(latest)):
                raise ValueError('The source passages or pronunciations changed during narration. Prepare the chapter again.')
            timing = align_file(path, alignment_input)
            edges = [clip['start'] for clip in timing['clips']] + [timing['clips'][-1]['end']]
            if (edges[0] != 0 or any(b < a for a, b in zip(edges, edges[1:])) or edges[-1] > duration + 0.01 or
                    [clip['segment_id'] for clip in timing['clips']] != [s['id'] for s in selected]):
                raise AudioError('Passage timing for this chunk could not be estimated consistently.')
            return {'timing': timing}

        check_cancel()
        try:
            metadata = produce_take(passage, narrator, {}, session['provider'], session['model'], api_key,
                                    self.store.root / 'listen-audio' / book_id, synthesizer=synthesizer, accept=accept)
        except ContentBlocked:
            self.record_block(book_id, session_id, chapter_id, segment_ids, block_role, request)
            raise
        timing = metadata.pop('timing')
        flags = []
        quality = timing['quality']
        if quality['boundaries'] and quality['matched'] / quality['boundaries'] < 0.6:
            flags.append('weak_alignment')
        body = {**identity, 'asset_id': metadata['asset_id'], 'duration': metadata['duration'],
                'provider': metadata['provider'], 'model': metadata['model'], 'voice': metadata['voice'],
                'chars': len(text), 'timing': timing, 'flags': flags, 'request': request or {},
                'created_at': now(),
                **({'resource_usage': metadata['resource_usage']} if 'resource_usage' in metadata else {})}
        body['id'] = _hash([book_id, identity, metadata['asset_id']])
        with self.store.lock, self.store.connect() as conn:
            conn.execute('INSERT OR IGNORE INTO listening_chunks VALUES (?,?,?,?,?,?)',
                         (body['id'], book_id, session['id'], chapter_id, body['asset_id'], json.dumps(body)))
            body = json.loads(conn.execute('SELECT body FROM listening_chunks WHERE id=?', (body['id'],)).fetchone()[0])
        self._asset_state(book_id, body['asset_id'], verify=True)
        return body

    def render_passage(self, book_id, session_id, segment_id, api_key=None, *, synthesizer=None, check_cancel=lambda: None):
        """Retained audio for the passage, generating it only when none is retained. Public shape."""
        check_cancel()
        existing = self.cached(book_id, session_id, segment_id)
        if existing:
            return existing
        return self.generate_passage(book_id, session_id, segment_id, api_key, synthesizer=synthesizer,
                                     check_cancel=check_cancel)

    def generate_passage(self, book_id, session_id, segment_id, api_key=None, *, synthesizer=None, check_cancel=lambda: None):
        """Synthesize and retain a new take without consulting the cache. Public shape."""
        session, passage, narrator, identity, recipe = self._inputs(book_id, session_id, segment_id)
        check_cancel()
        segment = next(s for s in self.store.book(book_id)['segments'] if s['id'] == segment_id)
        if session['provider'] == 'gemini' and segment_id in self.content_blocks(book_id, session_id)['refused']:
            raise KnownContentBlock()
        try:
            metadata = produce_take(passage, narrator, {}, session['provider'], session['model'], api_key,
                                    self.store.root / 'listen-audio' / book_id, synthesizer=synthesizer)
        except ContentBlocked:
            self.record_block(book_id, session_id, segment['chapter_id'], [segment_id], 'single')
            raise
        # Concurrent enhanced changes do not affect this recipe. A source change
        # must not publish a new take against the wrong passage coordinates.
        if self._inputs(book_id, session_id, segment_id)[-1] != recipe:
            raise ValueError('The source passage or its pronunciations changed during narration. Select the passage again.')
        metadata = self._retain(book_id, session_id, segment_id, identity, recipe,
                                self._synthesis_key(passage, session), metadata)
        # Completed audio is retained even if Stop was pressed during the call.
        # The worker's cancellation check controls whether playback resumes.
        return present_take(book_id, metadata)
