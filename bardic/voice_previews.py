"""Bounded voice auditions with independent, immutable recipes and WAV takes.

Previewing never edits casting, canonical text, or a reader's selected take.
Retained requests snapshot their exact excerpt and delivery inputs before queueing.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from urllib.parse import quote

from .audio import (AudioError, BREEZE_MODEL, DEFAULT_TTS_MODEL, PROVIDERS, SYSTEM_MODEL, TTS_MODELS, render_fingerprint,
                    validate_audio)
from .store import now
from . import pronunciation
from .take_archive import produce_take
from . import wire
from .audio_refs import audio_ref
from .errors import Invalid, NotFound


VERSION = 1
MAX_PREVIEW_CHARACTERS = 400
# Carrier for auditioning a word the book does not contain (yet): original text, not book prose.
NAME_DEMO_TEXT = 'The next morning, {} crossed the square and asked for the ferryman.'
DEMO_TEXT = 'The lantern glowed beside the open book. “Shall we begin?” she asked. Beyond the window, the quiet town was waiting for a story.'


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def _excerpt(text):
    if len(text) <= MAX_PREVIEW_CHARACTERS:
        return text
    prefix = text[:MAX_PREVIEW_CHARACTERS]
    # Keep the exact original prefix, including its punctuation and whitespace.
    sentences = list(re.finditer(r'[.!?][”’\"\']?(?:\s|$)', prefix))
    if sentences and sentences[-1].end() >= MAX_PREVIEW_CHARACTERS // 2:
        return prefix[:sentences[-1].end()]
    boundaries = list(re.finditer(r'\s+', prefix))
    if boundaries and boundaries[-1].start() > 0:
        return prefix[:boundaries[-1].start()]
    return prefix


# Optional public extras of an audition take, copied when the stored record has them.
# Provider usage is served by the resources routes, not with the audio.
_TAKE_EXTRAS = ('reuse', 'provider_timing', 'breeze', 'voice_revision', 'voice_library')


def present_take(book_id, metadata):
    """The public audio object (contract ``VoicePreviewAudio``) for a retained audition take.

    A whitelist: the recipe fingerprint, the record format version, provider
    usage, the copied source anchor and any transient marker stay in storage. Idempotent, so it also cleans an audio
    object presented by an earlier version (for example inside a stored job).
    """
    extras = {key: metadata[key] for key in _TAKE_EXTRAS if key in metadata}
    if 'provider_timing' in extras:
        extras['provider_timing'] = wire.provider_timing(extras['provider_timing'])
    return audio_ref(f'/api/books/{quote(book_id, safe="")}/voice-preview/audio/{metadata["asset_id"]}',
                     asset_id=metadata['asset_id'], duration=metadata.get('duration'),
                     provider=metadata.get('provider'), model=metadata.get('model'), voice=metadata.get('voice'),
                     created_at=metadata.get('created_at'), preview_id=metadata.get('preview_id'), kind='preview',
                     **extras)


def _speech_inputs(recipe):
    # A display-name edit does not alter speech. Keep the recorded label in the
    # immutable request while comparing every other retained input for reuse.
    result = copy.deepcopy(recipe)
    result['preview'].pop('id', None)
    result['preview'].pop('character_name', None)
    return result


class VoicePreviewRepository:
    def __init__(self, store):
        self.store = store
        # Schema setup runs once per store, never on each request.
        if getattr(store, '_voice_preview_schema_ready', False):
            return
        with store.lock, store.connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS voice_preview_requests (
                id TEXT PRIMARY KEY, book_id TEXT NOT NULL, body TEXT NOT NULL)''')
            conn.execute('''CREATE TABLE IF NOT EXISTS voice_preview_takes (
                id TEXT PRIMARY KEY, preview_id TEXT NOT NULL, book_id TEXT NOT NULL,
                asset_id TEXT NOT NULL, body TEXT NOT NULL,
                FOREIGN KEY(preview_id) REFERENCES voice_preview_requests(id))''')
            conn.execute('CREATE INDEX IF NOT EXISTS voice_preview_recipe ON voice_preview_takes(book_id,preview_id)')
            conn.execute("""CREATE INDEX IF NOT EXISTS voice_preview_speech
                ON voice_preview_requests(book_id,json_extract(body,'$.fingerprint'))""")
            for table in ('voice_preview_requests', 'voice_preview_takes'):
                for operation in ('UPDATE', 'DELETE'):
                    conn.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()}
                        BEFORE {operation} ON {table} BEGIN
                        SELECT RAISE(ABORT, 'Voice previews are immutable'); END''')
        store._voice_preview_schema_ready = True

    def prepare(self, book_id, provider, voice=None, model=None, *, segment_id=None,
                character_id=None, direction=None, segment_direction=None, selection=None, pronunciation_draft=None):
        """``selection`` is the pinned Breeze voice; it is retained in the recipe.

        The book's pronunciations apply to every preview. ``pronunciation_draft`` is an unsaved entry
        auditioned in place of the one it edits; without a chosen passage, the first passage using
        its word is read from that word's sentence.
        """
        book = self.store.book(book_id)
        lexicon, draft = pronunciation.book_lexicon(book), None
        if pronunciation_draft is not None:
            try:
                draft = pronunciation.normalize_entry(pronunciation_draft)
            except ValueError as error:
                raise Invalid('pronunciation_invalid', str(error)) from None
            lexicon = pronunciation.merged(lexicon, draft)
        if provider not in PROVIDERS:
            raise Invalid('provider_unsupported', 'The narration provider must be system, gemini or breeze.')
        if direction is not None and not character_id:
            raise Invalid('direction_requires_character', 'A performance direction needs a selected character.')
        if segment_direction is not None and not (segment_id and character_id):
            raise Invalid('passage_direction_incomplete',
                          'A passage direction needs both a selected passage and a selected character.')
        if voice is not None and (not isinstance(voice, str) or len(voice) > 256):
            raise Invalid('narrator_voice_invalid', 'The narrator voice is not a valid voice value.')
        if direction is not None and (not isinstance(direction, str) or len(direction) > 3000):
            raise Invalid('direction_invalid', 'The performance direction must be text of at most 3000 characters.')
        if segment_direction is not None and (not isinstance(segment_direction, str) or len(segment_direction) > 3000):
            raise Invalid('direction_invalid', 'The passage direction must be text of at most 3000 characters.')
        voice = voice.strip() if voice else ''
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
        else:
            voice, model = voice or 'Kore', model or DEFAULT_TTS_MODEL
            if model not in TTS_MODELS:
                raise Invalid('model_unsupported', 'The Gemini speech model is not a supported TTS model.')
        segment = next((s for s in book['segments'] if s['id'] == segment_id), None)
        if segment_id and segment is None:
            raise Invalid('unknown_passage', 'No passage with this ID is in the book.')
        character = next((c for c in book['characters'] if c['id'] == character_id), None)
        if character_id and character is None:
            raise Invalid('unknown_character', 'No character with this ID is in the book.')
        if draft is not None and segment is None:
            segment = next((s for s in book['segments'] if pronunciation.first_match(s['text'], draft)), None)
        if character is not None and segment is None:
            segment = next((s for s in book['segments'] if s.get('speaker_id') == character_id), None)
        anchor, scene = None, {}
        if segment is not None:
            chapter = next((c for c in book['chapters'] if c['id'] == segment['chapter_id']), None)
            start, end = segment.get('start'), segment.get('end')
            if (chapter is None or type(start) is not int or type(end) is not int or
                    not 0 <= start < end <= len(chapter['text']) or chapter['text'][start:end] != segment['text']):
                raise Invalid('passage_source_mismatch', 'The passage text does not match its original source coordinates.')
            match = pronunciation.first_match(segment['text'], draft) if draft is not None else None
            if match:
                # The whole sentence from the chapter: a passage can be a lone speech tag (" Eilidh said.").
                begin, finish = pronunciation.sentence_around(chapter['text'], start + match[0], start + match[1],
                                                              MAX_PREVIEW_CHARACTERS)
                offset, text = begin - start, chapter['text'][begin:finish]
            else:
                offset, text = 0, _excerpt(segment['text'])
            anchor = {'schema_version': VERSION, 'book_id': book_id, 'chapter_id': chapter['id'],
                      'segment_id': segment['id'], 'start': start + offset, 'end': start + offset + len(text),
                      'text_sha256': hashlib.sha256(text.encode()).hexdigest()}
            passage = {'id': segment['id'], 'text': text}
            if character is not None:
                passage.update({k: copy.deepcopy(segment[k]) for k in ('direction', 'cues') if k in segment})
                if segment_direction is not None:
                    passage['direction'] = segment_direction
                selected_scene = next((s for s in book['scenes'] if s['id'] == segment.get('scene_id')), {})
                scene = {k: selected_scene[k] for k in ('tone', 'direction') if k in selected_scene}
        elif draft is not None:
            passage = {'id': 'pronunciation-demo-v1', 'text': NAME_DEMO_TEXT.format(draft['term'])}
        else:
            passage = {'id': 'voice-preview-demo-v1', 'text': DEMO_TEXT}
        spoken = pronunciation.apply(passage['text'], lexicon, provider)[0]
        # The retained request keeps only matching entries' speech fields: no IDs, notes or unrelated words.
        passage = pronunciation.with_lexicon(passage, pronunciation.speech_entries(passage['text'], lexicon))
        performer = {'id': character_id or 'preview-narrator', 'voice': voice or 'Kore', 'system_voice': voice}
        if provider == 'breeze':
            performer['voices'] = {'breeze': copy.deepcopy(selection)}
        if character is not None:
            performer['direction'] = character.get('direction', '') if direction is None else direction
        try:
            fingerprint = render_fingerprint(passage, performer, scene, provider, model)
        except AudioError as error:
            raise Invalid('narrator_voice_invalid', str(error)) from None
        preview = {'schema_version': VERSION, 'book_id': book_id, 'text': passage['text'],
                   'source': 'passage' if segment is not None else 'demo',
                   'segment_id': segment['id'] if segment is not None else None,
                   'chapter_id': segment['chapter_id'] if segment is not None else None,
                   'character_id': character_id, 'character_name': character.get('name') if character is not None else None,
                   'source_anchor': anchor, 'truncated': segment is not None and len(passage['text']) < len(segment['text']),
                   'provider': provider, 'model': model, 'voice': voice}
        # Added only when they apply, so earlier preview identities are unchanged.
        if spoken != passage['text']:
            preview['spoken_text'] = spoken
        if draft is not None:
            preview['pronunciation'] = {'term': draft['term'], 'spoken': pronunciation.spoken_form(draft, provider)}
        recipe = {'schema_version': VERSION, 'preview': preview, 'passage': passage,
                  'character': performer, 'scene': scene, 'fingerprint': fingerprint}
        preview['id'] = _hash(recipe)
        # No key, live device state, mutable cast projection, or selected audio
        # is included. Identical requests keep their original retained recipe.
        with self.store.lock, self.store.connect() as conn:
            conn.execute('INSERT OR IGNORE INTO voice_preview_requests VALUES (?,?,?)',
                         (preview['id'], book_id, json.dumps(recipe, ensure_ascii=False)))
        return preview

    def _request(self, book_id, preview_id):
        self.store.book(book_id)
        with self.store.lock, self.store.connect() as conn:
            row = conn.execute('SELECT body FROM voice_preview_requests WHERE book_id=? AND id=?',
                               (book_id, preview_id)).fetchone()
        if not row:
            raise NotFound('voice_preview_not_found', 'Voice preview not found')
        return json.loads(row[0])

    def _path(self, book_id, asset_id):
        if (not isinstance(book_id, str) or re.fullmatch(r'[A-Za-z0-9_-]+', book_id) is None or
                not isinstance(asset_id, str) or re.fullmatch(r'[a-f0-9]{64}', asset_id) is None):
            raise NotFound('audio_not_found', 'Voice preview audio not found')
        return self.store.root / 'voice-previews' / book_id / f'{asset_id}.wav'

    def _validated_asset(self, book_id, asset_id):
        path = self._path(book_id, asset_id)
        with path.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != asset_id:
                raise AudioError('Saved preview failed its content integrity check.')
        return validate_audio(path)

    def asset_path(self, book_id, asset_id):
        self.store.book(book_id)
        path = self._path(book_id, asset_id)
        with self.store.lock, self.store.connect() as conn:
            found = conn.execute('SELECT 1 FROM voice_preview_takes WHERE book_id=? AND asset_id=? LIMIT 1',
                                 (book_id, asset_id)).fetchone()
        if not found:
            raise NotFound('audio_not_found', 'Voice preview audio not found')
        try:
            self._validated_asset(book_id, asset_id)
        except (OSError, EOFError, ValueError):
            raise NotFound('audio_not_found', 'Voice preview audio is missing or damaged') from None
        return path

    def cached(self, book_id, preview_id):
        """A retained audition take for the preview (public shape), or None. Never contacts a provider."""
        recipe = self._request(book_id, preview_id)
        with self.store.lock, self.store.connect() as conn:
            rows = conn.execute('SELECT body FROM voice_preview_takes WHERE book_id=? AND preview_id=? ORDER BY rowid DESC',
                                (book_id, preview_id)).fetchall()
        for row in rows:
            metadata = json.loads(row[0])
            try:
                duration = self._validated_asset(book_id, metadata['asset_id'])
            except (OSError, EOFError, ValueError, KeyError):
                continue
            return present_take(book_id, {**metadata, 'duration': duration})
        # Request IDs retain the historical character name. A rename may reuse
        # the same speech, but source identity and every performance input must
        # still match. The expression index narrows lookup before decoding.
        with self.store.lock, self.store.connect() as conn:
            rows = conn.execute('''SELECT t.id,t.body,r.body FROM voice_preview_requests r
                JOIN voice_preview_takes t ON t.preview_id=r.id
                WHERE r.book_id=? AND json_extract(r.body,'$.fingerprint')=? AND r.id<>?
                ORDER BY t.rowid DESC''', (book_id, recipe['fingerprint'], preview_id)).fetchall()
        inputs = _speech_inputs(recipe)
        for take_id, body, original_recipe in rows:
            if _speech_inputs(json.loads(original_recipe)) != inputs:
                continue
            original = json.loads(body)
            try:
                duration = self._validated_asset(book_id, original['asset_id'])
            except (OSError, EOFError, ValueError, KeyError):
                continue
            reused = {key: value for key, value in original.items() if key != 'resource_usage'}
            reused.update(duration=duration, reuse={'schema_version': 1, 'take_id': take_id,
                                                    'preview_id': original['preview_id']})
            metadata = self._retain(book_id, recipe['preview'], reused)
            return present_take(book_id, metadata)
        return None

    def _retain(self, book_id, preview, audio):
        preview_id = preview['id']
        metadata = {**audio, 'schema_version': VERSION, 'preview_id': preview_id,
                    'source_anchor': preview['source_anchor'], 'created_at': now()}
        identifier = _hash([preview_id, audio['asset_id']])
        with self.store.lock, self.store.connect() as conn:
            conn.execute('INSERT OR IGNORE INTO voice_preview_takes VALUES (?,?,?,?,?)',
                         (identifier, preview_id, book_id, audio['asset_id'], json.dumps(metadata)))
            return json.loads(conn.execute('SELECT body FROM voice_preview_takes WHERE id=?', (identifier,)).fetchone()[0])

    def render(self, book_id, preview_id, api_key=None, *, synthesizer=None, check_cancel=lambda: None):
        """A retained take for the preview, generating one only when none is retained. Public shape."""
        check_cancel()
        cached = self.cached(book_id, preview_id)
        if cached:
            return cached
        return self.generate(book_id, preview_id, api_key, synthesizer=synthesizer, check_cancel=check_cancel)

    def generate(self, book_id, preview_id, api_key=None, *, synthesizer=None, check_cancel=lambda: None):
        """Synthesize and retain a new audition take without consulting the cache. Public shape."""
        recipe = self._request(book_id, preview_id)
        preview = recipe['preview']
        check_cancel()
        audio = produce_take(recipe['passage'], recipe['character'], recipe['scene'], preview['provider'],
                             preview['model'], api_key, self.store.root / 'voice-previews' / book_id,
                             synthesizer=synthesizer)
        metadata = self._retain(book_id, preview, audio)
        # Keep successfully completed audio even when cancellation arrived during
        # the provider call; Runtime performs the final playback cancellation.
        return present_take(book_id, metadata)
