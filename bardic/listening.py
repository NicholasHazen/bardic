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

from .audio import AudioError, DEFAULT_TTS_MODEL, SYSTEM_MODEL, render_fingerprint, validate_audio
from .store import now
from .take_archive import produce_take


VERSION = 1
SYNTHESIS_CACHE_VERSION = 1


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


class ListeningRepository:
    def __init__(self, store):
        self.store = store
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
            for operation in ('UPDATE', 'DELETE'):
                conn.execute(f'''CREATE TRIGGER IF NOT EXISTS listening_takes_no_{operation.lower()}
                    BEFORE {operation} ON listening_takes BEGIN
                    SELECT RAISE(ABORT, 'Listening takes are immutable'); END''')

    def session(self, book_id, provider, voice=None, model=None):
        self.store.book(book_id)
        if provider not in {'system', 'gemini'}:
            raise ValueError('Choose system or Gemini narration.')
        if voice is not None and (not isinstance(voice, str) or len(voice) > 256):
            raise ValueError('Choose a valid narrator voice.')
        voice = voice.strip() if voice else ''
        if provider == 'system':
            if model not in (None, '', SYSTEM_MODEL):
                raise ValueError('Device narration uses the installed macOS voice model.')
            model = SYSTEM_MODEL
        else:
            voice = voice or 'Kore'
            model = model or DEFAULT_TTS_MODEL
        config = {'schema_version': VERSION, 'book_id': book_id, 'provider': provider,
                  'voice': voice, 'model': model}
        # Provider validation is pure; it neither queries installed voices nor calls a model.
        self._audio_recipe({'id': 'configuration-check', 'text': 'Voice configuration'}, config)
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
            raise KeyError('Listening session not found')
        return json.loads(row[0])

    @staticmethod
    def _audio_recipe(segment, session):
        # Deliberately copy only the transcript identity. No inferred speaker,
        # stage direction, character trait, emotion, or cue enters simple mode.
        passage = {'id': segment['id'], 'text': segment['text']}
        narrator = {'id': 'simple-narrator', 'voice': session['voice'] or 'Kore',
                    'system_voice': session['voice']}
        fingerprint = render_fingerprint(passage, narrator, {}, session['provider'], session['model'])
        return passage, narrator, fingerprint

    @classmethod
    def _synthesis_key(cls, passage, session):
        # This constant ID is ONLY a lookup identity. Never pass it to synthesis
        # or publish its fingerprint as the source-bound generated fingerprint.
        fingerprint = cls._audio_recipe({'id': 'simple-speech-content', 'text': passage['text']}, session)[-1]
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
            raise KeyError('Passage not found in this book')
        chapter = next((c for c in book['chapters'] if c['id'] == segment['chapter_id']), None)
        return self._source_inputs(book_id, session, segment, chapter)

    def _source_inputs(self, book_id, session, segment, chapter):
        start, end = segment.get('start'), segment.get('end')
        if (chapter is None or type(start) is not int or type(end) is not int or
                not 0 <= start < end <= len(chapter['text']) or chapter['text'][start:end] != segment['text']):
            raise ValueError('This passage does not match its original source. Repair its source mapping before listening.')
        passage, narrator, fingerprint = self._audio_recipe(segment, session)
        identity = {'schema_version': VERSION, 'book_id': book_id, 'session_id': session['id'],
                    'chapter_id': chapter['id'], 'segment_id': segment['id'],
                    'start': start, 'end': end, 'fingerprint': fingerprint}
        return session, passage, narrator, identity, _hash(identity)

    def _path(self, book_id, asset_id):
        if not isinstance(book_id, str) or re.fullmatch(r'[a-zA-Z0-9_-]+', book_id) is None:
            raise KeyError('Listening audio not found')
        if not isinstance(asset_id, str) or re.fullmatch(r'[a-f0-9]{64}', asset_id) is None:
            raise KeyError('Listening audio not found')
        return self.store.root / 'listen-audio' / book_id / f'{asset_id}.wav'

    def asset_path(self, book_id, asset_id):
        self.store.book(book_id)
        target = self._path(book_id, asset_id)
        with self.store.lock, self.store.connect() as conn:
            found = conn.execute('SELECT 1 FROM listening_takes WHERE book_id=? AND asset_id=? LIMIT 1',
                                 (book_id, asset_id)).fetchone()
        if not found or not target.is_file():
            raise KeyError('Listening audio not found')
        return target

    def _present(self, book_id, metadata):
        return {**metadata, 'available': True, 'mode': 'simple',
                'url': f'/api/books/{quote(book_id, safe="")}/listen/audio/{metadata["asset_id"]}'}

    def cached(self, book_id, session_id, segment_id):
        session, passage, _, identity, recipe = self._inputs(book_id, session_id, segment_id)
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
            return {**self._present(book_id, {**metadata, 'duration': duration}), 'cache_hit': True}
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
            self._copy_asset(source_book_id, book_id, original['asset_id'])
            # The producer fingerprint remains the real original fingerprint.
            # The target source-bound recipe is recorded in source_anchor;
            # reuse explicitly points to the actual retained input take.
            reused = {key: value for key, value in original.items() if key != 'resource_usage'}
            reused.update(duration=duration, reuse={'schema_version': 1, 'take_id': take_id,
                          'book_id': source_book_id, 'session_id': original['session_id'],
                          'segment_id': original['segment_id'], 'recipe': original['recipe'],
                          'fingerprint': original['fingerprint']})
            metadata = self._retain(book_id, session_id, segment_id, identity, recipe, content_key, reused)
            return {**self._present(book_id, metadata), 'cache_hit': True}
        return None

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
        takes = []
        for segment in book['segments']:
            try:
                recipe = self._source_inputs(book_id, session, segment, chapters.get(segment['chapter_id']))[-1]
            except ValueError:
                continue
            for metadata in saved.get((segment['id'], recipe), []):
                # Listing a finished novel must not reread gigabytes of WAV
                # samples. Assets were fully validated when archived; the
                # per-passage cache path validates again before synthesis reuse.
                if self._path(book_id, metadata['asset_id']).is_file():
                    takes.append({'segment_id': segment['id'], 'audio': self._present(book_id, metadata)})
                    break
        return {'session': session, 'takes': takes}

    def render_passage(self, book_id, session_id, segment_id, api_key=None, *, synthesizer=None, check_cancel=lambda: None):
        check_cancel()
        existing = self.cached(book_id, session_id, segment_id)
        if existing:
            return existing
        session, passage, narrator, identity, recipe = self._inputs(book_id, session_id, segment_id)
        check_cancel()
        metadata = produce_take(passage, narrator, {}, session['provider'], session['model'], api_key,
                                self.store.root / 'listen-audio' / book_id, synthesizer=synthesizer)
        # Concurrent enhanced changes do not affect this recipe. A source change
        # must not publish a new take against the wrong passage coordinates.
        if self._inputs(book_id, session_id, segment_id)[-1] != recipe:
            raise ValueError('The source passage changed during narration. Select the passage again.')
        metadata = self._retain(book_id, session_id, segment_id, identity, recipe,
                                self._synthesis_key(passage, session), metadata)
        # Completed audio is retained even if Stop was pressed during the call.
        # The worker's cancellation check controls whether playback resumes.
        return self._present(book_id, metadata)
