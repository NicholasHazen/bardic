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
from .audio import AudioError, DEFAULT_TTS_MODEL, SYSTEM_MODEL, render_fingerprint, validate_audio
from .chunking import CHUNKING_VERSION, OUTPUT_TOKEN_CAP, PROVIDER_AUDIO_CAP_SECONDS
from .store import now
from .take_archive import produce_take


VERSION = 1
SYNTHESIS_CACHE_VERSION = 1
# Version 1 multi-passage takes: one WAV for an exact chapter slice, with
# estimated passage clips from pause alignment. See docs/DATA-MODEL.md.
CHUNK_VERSION = 1
# Faster than this many code points per audio second suggests skipped text.
_SUSPICIOUS_CHARS_PER_SECOND = 26.0
# Integrity results per asset file state: (root, book, asset, size, mtime_ns) -> ok.
_asset_checks: dict[tuple, bool] = {}


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
            for table in ('listening_takes', 'listening_chunks'):
                for operation in ('UPDATE', 'DELETE'):
                    name = f'{table}_no_{operation.lower()}'
                    conn.execute(f'''CREATE TRIGGER IF NOT EXISTS {name}
                        BEFORE {operation} ON {table} BEGIN
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
            found = (conn.execute('SELECT 1 FROM listening_takes WHERE book_id=? AND asset_id=? LIMIT 1',
                                  (book_id, asset_id)).fetchone() or
                     conn.execute('SELECT 1 FROM listening_chunks WHERE book_id=? AND asset_id=? LIMIT 1',
                                  (book_id, asset_id)).fetchone())
        if not found or not target.is_file():
            raise KeyError('Listening audio not found')
        return target

    def _present(self, book_id, metadata):
        return {**metadata, 'available': True, 'mode': 'simple',
                'url': f'/api/books/{quote(book_id, safe="")}/listen/audio/{metadata["asset_id"]}'}

    def cached(self, book_id, session_id, segment_id):
        session, passage, _, identity, recipe = self._inputs(book_id, session_id, segment_id)
        clip = self.chunk_clips(book_id, session_id, verify=True).get(segment_id)
        if clip:
            return {**clip, 'cache_hit': True}
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
        # Chunk clips come first: consecutive clips in one WAV play gaplessly.
        clips = self._chunk_clips(book, session)
        takes = []
        for segment in book['segments']:
            if segment['id'] in clips:
                takes.append({'segment_id': segment['id'], 'audio': clips[segment['id']]})
                continue
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
    def _chunk_recipe(chapter_id, start, end, text, session):
        source_id = 'chunk-' + _hash([chapter_id, start, end])[:24]
        return ListeningRepository._audio_recipe({'id': source_id, 'text': text}, session)

    @classmethod
    def _chunk_valid(cls, chunk, chapters, segments, session):
        """A chunk applies only while its source slice, anchors and recipe are unchanged."""
        chapter = chapters.get(chunk.get('chapter_id'))
        if chunk.get('schema_version') != CHUNK_VERSION or chunk.get('session_id') != session['id'] or not chapter:
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
            # A recipe version or narrator change invalidates chunks exactly as it does single takes.
            if cls._chunk_recipe(chapter['id'], start, end, text, session)[-1] != chunk.get('fingerprint'):
                return False
        except AudioError:
            return False
        return bool(chunk.get('segments'))

    def _present_clip(self, chunk, clip):
        return {'mode': 'simple', 'available': True, 'segment_id': clip['segment_id'],
                'url': f'/api/books/{quote(chunk["book_id"], safe="")}/listen/audio/{chunk["asset_id"]}',
                'asset_id': chunk['asset_id'], 'chunk_id': chunk['id'],
                'clip_start': clip['start'], 'clip_end': clip['end'],
                'duration': round(clip['end'] - clip['start'], 3), 'chunk_duration': chunk['duration'],
                'timing': 'estimated', 'provider': chunk['provider'], 'model': chunk['model'],
                'voice': chunk['voice'], 'session_id': chunk['session_id'], 'created_at': chunk['created_at'],
                'flags': chunk.get('flags', [])}

    def _chunk_clips(self, book, session, *, verify=False):
        with self.store.lock, self.store.connect() as conn:
            rows = conn.execute('''SELECT body FROM listening_chunks WHERE book_id=? AND session_id=?
                ORDER BY rowid DESC''', (book['id'], session['id'])).fetchall()
        chapters = {chapter['id']: chapter for chapter in book['chapters']}
        segments = {segment['id']: segment for segment in book['segments']}
        clips = {}
        for (body,) in rows:
            chunk = json.loads(body)
            if not self._chunk_valid(chunk, chapters, segments, session):
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
            raise KeyError('Chapter not found in this book')
        return chapter, [segment for segment in book['segments'] if segment['chapter_id'] == chapter_id]

    def render_chunk(self, book_id, session_id, chapter_id, segment_ids, api_key=None, *,
                     synthesizer=None, check_cancel=lambda: None, request=None):
        """Synthesize one exact chapter slice and retain it with estimated passage clips."""
        check_cancel()
        session = self.get_session(book_id, session_id)
        book = self.store.book(book_id)
        chapter, ordered = self.chapter_segments(book, chapter_id)
        positions = {segment['id']: index for index, segment in enumerate(ordered)}
        indexes = [positions.get(segment_id) for segment_id in segment_ids]
        if not segment_ids or None in indexes or indexes != list(range(indexes[0], indexes[0] + len(indexes))):
            raise ValueError('A chunk must be consecutive passages from one chapter.')
        selected = [ordered[index] for index in indexes]
        for segment in selected:
            self._source_inputs(book_id, session, segment, chapter)
        start, end = selected[0]['start'], selected[-1]['end']
        text = chapter['text'][start:end]
        passage, narrator, fingerprint = self._chunk_recipe(chapter_id, start, end, text, session)
        identity = {'schema_version': CHUNK_VERSION, 'chunking_version': CHUNKING_VERSION,
                    'book_id': book_id, 'session_id': session['id'], 'chapter_id': chapter_id,
                    'start': start, 'end': end, 'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
                    'segments': [[s['id'], s['start'], s['end']] for s in selected], 'fingerprint': fingerprint}
        alignment_input = [{'id': segment['id'], 'text': segment['text'],
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
                                     {segment['id']: segment for segment in latest_ordered}, session):
                raise ValueError('The source passages changed during narration. Prepare the chapter again.')
            timing = align_file(path, alignment_input)
            edges = [clip['start'] for clip in timing['clips']] + [timing['clips'][-1]['end']]
            if (edges[0] != 0 or any(b < a for a, b in zip(edges, edges[1:])) or edges[-1] > duration + 0.01 or
                    [clip['segment_id'] for clip in timing['clips']] != [s['id'] for s in selected]):
                raise AudioError('Passage timing for this chunk could not be estimated consistently.')
            return {'timing': timing}

        check_cancel()
        metadata = produce_take(passage, narrator, {}, session['provider'], session['model'], api_key,
                                self.store.root / 'listen-audio' / book_id, synthesizer=synthesizer, accept=accept)
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
