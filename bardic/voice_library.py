"""Named voices with immutable versions, shared by every book in the library.

The cast is assigned to a library voice. Each version is one fixed provider
voice: a Breeze server voice pinned to its revision, or a Gemini ``voice_...``
id. Characters follow a voice's current version, so saving a new version or
switching back re-voices them, while older versions stay re-renderable and
their takes stay in history. Versions are append-only; names, descriptions
and the current-version pointer are the only mutable voice fields.

Nothing here contacts a provider. Resolution of an assignment to a concrete
provider voice reads only SQLite, so saved takes validate offline.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from .audio import _LEGACY_VOICE_FIELDS, PROVIDERS, SAMPLE_RATE, AudioError, _normalize, _wave_info
from .store import now
from .errors import NotFound

VERSION = 1
LIBRARY_PROVIDERS = ("breeze", "gemini")
MAX_NAME = 100
MAX_DESCRIPTION = 1000
MAX_SAMPLE_TEXT = 1000
_VOICE_ID = re.compile(r"vl_[a-f0-9]{16}")
_DRAFT_ID = re.compile(r"vd_[a-f0-9]{16}")
_ASSET_ID = re.compile(r"[a-f0-9]{64}")


def library_reference(selection) -> str | None:
    """The library voice id an assignment follows, if any."""
    value = selection.get("library") if isinstance(selection, dict) else None
    return value if isinstance(value, str) and _VOICE_ID.fullmatch(value) else None


def assignments(character: dict) -> dict:
    """A character's stored voice choices per provider, as the cast shows them.

    Library references stay references (they follow a voice's current
    version); earlier single-provider fields appear as direct choices. A
    provider with no entry means Default.
    """
    stored = character.get("voices") if isinstance(character.get("voices"), dict) else {}
    result = {}
    for provider in PROVIDERS:
        selection = stored.get(provider)
        if isinstance(selection, dict):
            result[provider] = copy.deepcopy(selection)
            continue
        legacy = _LEGACY_VOICE_FIELDS.get(provider)
        value = character.get(legacy) if legacy else None
        if isinstance(value, str) and value:
            result[provider] = {"id": value}
    return result


def concrete_selection(voice: dict | None, provider: str) -> dict:
    """The provider voice a library voice means now: its current version. Local only."""
    if not voice:
        raise AudioError("The assigned voice no longer exists in the voice library. Choose another voice.")
    if voice.get("deleted_at"):
        raise AudioError(f"The voice “{voice['name']}” was deleted. Choose another voice in the cast.")
    if voice["provider"] != provider:
        raise AudioError(f"“{voice['name']}” is a {voice['provider'].title()} voice, not a {provider.title()} voice.")
    version = next((entry for entry in voice["versions"] if entry["version"] == voice["current_version"]), None)
    if version is None:
        raise AudioError(f"The voice “{voice['name']}” has no current version.")
    selection = {"id": version["provider_voice_id"], "library": voice["id"], "version": version["version"]}
    if provider == "breeze":
        selection.update(revision=version["revision"], seed=version["seed"])
    return selection


class VoiceLibrary:
    def __init__(self, store):
        self.store = store
        with store.lock, store.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS voice_library (id TEXT PRIMARY KEY, provider TEXT NOT NULL, body TEXT NOT NULL)')
            conn.execute('''CREATE TABLE IF NOT EXISTS voice_library_versions (
                voice_id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY(voice_id, version), FOREIGN KEY(voice_id) REFERENCES voice_library(id))''')
            conn.execute('CREATE TABLE IF NOT EXISTS voice_drafts (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            # History of the mutable pointers (current version, default, deletion);
            # take metadata and artifacts only see the resolved voice.
            conn.execute('CREATE TABLE IF NOT EXISTS voice_library_events (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL)')
            for table, message in (('voice_library_versions', 'Voice versions are immutable'),
                                   ('voice_library_events', 'Voice history is append-only')):
                for operation in ('UPDATE', 'DELETE'):
                    conn.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()}
                        BEFORE {operation} ON {table} BEGIN
                        SELECT RAISE(ABORT, '{message}'); END''')
            conn.execute('''CREATE TRIGGER IF NOT EXISTS voice_library_no_delete
                BEFORE DELETE ON voice_library BEGIN
                SELECT RAISE(ABORT, 'Deleted voices are kept as records'); END''')

    # Audio assets ---------------------------------------------------------

    @property
    def asset_dir(self) -> Path:
        return self.store.root / 'voice-library'

    def asset_path(self, asset_id: str) -> Path:
        if not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id):
            raise NotFound('audio_not_found', 'Voice audio not found')
        return self.asset_dir / f'{asset_id}.wav'

    def store_audio(self, data: bytes) -> dict:
        """Keep a validated 24 kHz mono copy of an audition clip; returns {asset_id, duration}."""
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.voice-', dir=self.asset_dir) as directory:
            source, normalized = Path(directory) / 'source', Path(directory) / 'normalized.wav'
            source.write_bytes(data)
            try:
                channels, width, rate, _ = _wave_info(source)
                if (channels, width, rate) == (1, 2, SAMPLE_RATE):
                    shutil.copyfile(source, normalized)
                else:
                    _normalize(source, normalized)
            except AudioError:
                _normalize(source, normalized)
            _, _, rate, frames = _wave_info(normalized, normalized=True)
            with normalized.open('rb') as handle:
                asset_id = hashlib.file_digest(handle, 'sha256').hexdigest()
                os.fsync(handle.fileno())
            try:
                os.link(normalized, self.asset_path(asset_id))
            except FileExistsError:
                pass
        return {'asset_id': asset_id, 'duration': round(frames / rate, 3)}

    # Voices ----------------------------------------------------------------

    def _rows(self, conn, voice_id=None):
        query = 'SELECT body FROM voice_library' + (' WHERE id=?' if voice_id else ' ORDER BY rowid')
        voices = [json.loads(row[0]) for row in conn.execute(query, (voice_id,) if voice_id else ())]
        for voice in voices:
            voice['versions'] = [json.loads(row[0]) for row in conn.execute(
                'SELECT body FROM voice_library_versions WHERE voice_id=? ORDER BY version', (voice['id'],))]
        return voices

    def voices(self, *, include_deleted=False) -> list[dict]:
        with self.store.lock, self.store.connect() as conn:
            voices = self._rows(conn)
        return [voice for voice in voices if include_deleted or not voice.get('deleted_at')]

    def voice(self, voice_id: str) -> dict:
        if not isinstance(voice_id, str) or not _VOICE_ID.fullmatch(voice_id):
            raise NotFound('voice_not_found', 'Voice not found')
        with self.store.lock, self.store.connect() as conn:
            voices = self._rows(conn, voice_id)
        if not voices:
            raise NotFound('voice_not_found', 'Voice not found')
        return voices[0]

    @staticmethod
    def _text(value, limit, label, *, required=False) -> str:
        if value is None:
            value = ''
        if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
            raise ValueError(f'{label} must be {"1" if required else "0"}–{limit} characters.')
        return value.strip()

    @staticmethod
    def new_id() -> str:
        return f'vl_{uuid4().hex[:16]}'

    @staticmethod
    def _event(conn, kind: str, **fields):
        conn.execute('INSERT INTO voice_library_events(body) VALUES (?)',
                     (json.dumps({'kind': kind, 'at': now(), **fields}),))

    def record(self, kind: str, **fields):
        with self.store.lock, self.store.connect() as conn:
            self._event(conn, kind, **fields)

    def events(self, voice_id: str | None = None) -> list[dict]:
        with self.store.lock, self.store.connect() as conn:
            rows = conn.execute('SELECT body FROM voice_library_events ORDER BY id').fetchall()
        events = [json.loads(row[0]) for row in rows]
        return [event for event in events if voice_id is None or event.get('voice_id') == voice_id]

    def create(self, provider: str, *, name: str, description: str, origin: str, version: dict,
               source: dict | None = None, voice_id: str | None = None) -> dict:
        if provider not in LIBRARY_PROVIDERS:
            raise ValueError('Library voices are Breeze or Gemini voices.')
        if voice_id is not None and not _VOICE_ID.fullmatch(voice_id):
            raise ValueError('Invalid library voice id.')
        voice = {'schema_version': VERSION, 'id': voice_id or self.new_id(), 'provider': provider,
                 'name': self._text(name, MAX_NAME, 'The voice name', required=True),
                 'description': self._text(description, MAX_DESCRIPTION, 'The description'),
                 'origin': origin, 'current_version': 1, 'created_at': now(), 'updated_at': now(),
                 'deleted_at': None, 'source': source}
        entry = self._version(voice, 1, version)
        with self.store.lock, self.store.connect() as conn:
            conn.execute('INSERT INTO voice_library VALUES (?,?,?)', (voice['id'], provider, json.dumps(voice)))
            conn.execute('INSERT INTO voice_library_versions VALUES (?,?,?)', (voice['id'], 1, json.dumps(entry)))
            self._event(conn, 'created', voice_id=voice['id'], provider=provider, origin=origin,
                        provider_voice_id=entry['provider_voice_id'])
        return self.voice(voice['id'])

    @staticmethod
    def _version(voice, number, fields) -> dict:
        provider_voice_id = fields.get('provider_voice_id')
        if not isinstance(provider_voice_id, str) or not provider_voice_id:
            raise ValueError('A voice version needs its provider voice id.')
        if voice['provider'] == 'breeze' and not re.fullmatch(r'[a-f0-9]{64}', fields.get('revision') or ''):
            raise ValueError('A Breeze voice version needs its pinned revision.')
        return {'schema_version': VERSION, 'version': number, 'provider': voice['provider'],
                'provider_voice_id': provider_voice_id, 'revision': fields.get('revision'),
                'seed': fields.get('seed'), 'made': fields.get('made', 'designed'),
                'recipe': fields.get('recipe') or {}, 'audition': fields.get('audition'),
                'project': fields.get('project'), 'created_at': now(), 'expires_at': fields.get('expires_at')}

    def add_version(self, voice_id: str, fields: dict, *, make_current=True) -> dict:
        with self.store.lock, self.store.connect() as conn:
            voices = self._rows(conn, voice_id)
            if not voices or voices[0].get('deleted_at'):
                raise NotFound('voice_not_found', 'Voice not found')
            voice = voices[0]
            number = max(version['version'] for version in voice['versions']) + 1
            entry = self._version(voice, number, fields)
            conn.execute('INSERT INTO voice_library_versions VALUES (?,?,?)', (voice_id, number, json.dumps(entry)))
            self._event(conn, 'version_added', voice_id=voice_id, version=number,
                        provider_voice_id=entry['provider_voice_id'])
            if make_current:
                self._save(conn, voice, current_version=number)
                self._event(conn, 'current_changed', voice_id=voice_id, version=number,
                            previous=voice['current_version'])
        return self.voice(voice_id)

    def _save(self, conn, voice, **fields):
        body = {key: value for key, value in voice.items() if key != 'versions'}
        body.update(fields, updated_at=now())
        conn.execute('UPDATE voice_library SET body=? WHERE id=?', (json.dumps(body), voice['id']))

    def update(self, voice_id: str, *, name=None, description=None) -> dict:
        fields = {}
        if name is not None:
            fields['name'] = self._text(name, MAX_NAME, 'The voice name', required=True)
        if description is not None:
            fields['description'] = self._text(description, MAX_DESCRIPTION, 'The description')
        with self.store.lock, self.store.connect() as conn:
            voices = self._rows(conn, voice_id)
            if not voices or voices[0].get('deleted_at'):
                raise NotFound('voice_not_found', 'Voice not found')
            if fields:
                self._save(conn, voices[0], **fields)
                self._event(conn, 'updated', voice_id=voice_id, fields=sorted(fields))
        return self.voice(voice_id)

    def set_current(self, voice_id: str, version: int) -> dict:
        with self.store.lock, self.store.connect() as conn:
            voices = self._rows(conn, voice_id)
            if not voices or voices[0].get('deleted_at'):
                raise NotFound('voice_not_found', 'Voice not found')
            if version not in {entry['version'] for entry in voices[0]['versions']}:
                raise ValueError('That version does not exist.')
            if version != voices[0]['current_version']:
                self._save(conn, voices[0], current_version=version)
                self._event(conn, 'current_changed', voice_id=voice_id, version=version,
                            previous=voices[0]['current_version'])
        return self.voice(voice_id)

    def tombstone(self, voice_id: str, *, server_deleted: list[str] | None = None) -> dict:
        with self.store.lock, self.store.connect() as conn:
            voices = self._rows(conn, voice_id)
            if not voices:
                raise NotFound('voice_not_found', 'Voice not found')
            self._save(conn, voices[0], deleted_at=now())
            self._event(conn, 'deleted', voice_id=voice_id, server_deleted=server_deleted or [])
        return self.voice(voice_id)

    def find_provider_voice(self, provider: str, provider_voice_id: str) -> list[tuple[str, int]]:
        """Every (library voice, version) built on one provider voice, including deleted voices."""
        with self.store.lock, self.store.connect() as conn:
            rows = conn.execute('''SELECT v.voice_id, v.version FROM voice_library_versions v
                JOIN voice_library l ON l.id=v.voice_id
                WHERE l.provider=? AND json_extract(v.body,'$.provider_voice_id')=?''',
                                (provider, provider_voice_id)).fetchall()
        return [(voice_id, version) for voice_id, version in rows]

    # Resolution ------------------------------------------------------------

    def current(self, voice_id: str) -> tuple[dict, dict]:
        voice = self.voice(voice_id)
        version = next(entry for entry in voice['versions'] if entry['version'] == voice['current_version'])
        return voice, version

    def concrete(self, provider: str, voice_id: str) -> dict:
        """The provider selection an assignment to ``voice_id`` means right now. Local only."""
        try:
            voice = self.voice(voice_id)
        except KeyError:
            voice = None
        return concrete_selection(voice, provider)

    # Drafts ----------------------------------------------------------------

    def create_draft(self, provider: str, *, name='', description='', sample_text='', base_voice_id=None,
                     context=None) -> dict:
        if provider not in LIBRARY_PROVIDERS:
            raise ValueError('Voices can be created for Breeze or Gemini.')
        if base_voice_id is not None:
            base = self.voice(base_voice_id)
            if base.get('deleted_at') or base['provider'] != provider:
                raise ValueError('Iterate on an existing voice of the same provider.')
        draft = {'schema_version': VERSION, 'id': f'vd_{uuid4().hex[:16]}', 'provider': provider,
                 'base_voice_id': base_voice_id, 'context': context,
                 'name': self._text(name, MAX_NAME, 'The voice name'),
                 'description': self._text(description, MAX_DESCRIPTION, 'The description'),
                 'sample_text': self._text(sample_text, MAX_SAMPLE_TEXT, 'The sample text'),
                 'status': 'open', 'candidates': [], 'created_at': now(), 'updated_at': now()}
        with self.store.lock, self.store.connect() as conn:
            conn.execute('INSERT INTO voice_drafts VALUES (?,?)', (draft['id'], json.dumps(draft)))
        return draft

    def draft(self, draft_id: str) -> dict:
        if not isinstance(draft_id, str) or not _DRAFT_ID.fullmatch(draft_id):
            raise NotFound('voice_draft_not_found', 'Voice draft not found')
        with self.store.lock, self.store.connect() as conn:
            row = conn.execute('SELECT body FROM voice_drafts WHERE id=?', (draft_id,)).fetchone()
        if not row:
            raise NotFound('voice_draft_not_found', 'Voice draft not found')
        return json.loads(row[0])

    def drafts(self, *, status='open') -> list[dict]:
        with self.store.lock, self.store.connect() as conn:
            rows = conn.execute('SELECT body FROM voice_drafts ORDER BY rowid DESC').fetchall()
        return [draft for draft in (json.loads(row[0]) for row in rows) if status is None or draft['status'] == status]

    def change_draft(self, draft_id: str, change) -> dict:
        """Apply ``change(draft)`` atomically; it may raise to refuse the change."""
        with self.store.lock, self.store.connect() as conn:
            draft = self.draft(draft_id)
            result = change(draft)
            draft = result if isinstance(result, dict) else draft
            draft['updated_at'] = now()
            conn.execute('UPDATE voice_drafts SET body=? WHERE id=?', (json.dumps(draft), draft_id))
        return draft

    def edit_draft(self, draft_id: str, *, name=None, description=None, sample_text=None) -> dict:
        fields = {}
        if name is not None:
            fields['name'] = self._text(name, MAX_NAME, 'The voice name')
        if description is not None:
            fields['description'] = self._text(description, MAX_DESCRIPTION, 'The description')
        if sample_text is not None:
            fields['sample_text'] = self._text(sample_text, MAX_SAMPLE_TEXT, 'The sample text')

        def apply(draft):
            if draft['status'] != 'open':
                raise ValueError('This voice draft is already finished.')
            draft.update(fields)
        return self.change_draft(draft_id, apply)

    def add_candidates(self, draft_id: str, candidates: list[dict]) -> dict:
        def apply(draft):
            if draft['status'] != 'open':
                raise ValueError('This voice draft is already finished.')
            for candidate in candidates:
                draft['candidates'].append({'id': f'c{len(draft["candidates"]) + 1}', 'discarded': False,
                                            'created_at': now(), **copy.deepcopy(candidate)})
        return self.change_draft(draft_id, apply)

    # Usage -----------------------------------------------------------------

    def usage(self, default_voice_ids: dict[str, str | None]) -> dict[str, list[dict]]:
        """Which characters follow each voice, read with SQLite JSON functions (no book hydration)."""
        with self.store.lock, self.store.connect() as conn:
            rows = conn.execute("""SELECT id, json_extract(body,'$.title'), json_extract(body,'$.characters') FROM books
                WHERE NOT EXISTS(SELECT 1 FROM library_archives a WHERE a.kind='book' AND a.entity_id=books.id)""").fetchall()
        # Direct provider pins (for example a Breeze {id, revision} choice) also
        # depend on the provider voices behind library versions.
        by_provider_voice: dict[tuple[str, str], str] = {}
        for voice in self.voices():
            for version in voice['versions']:
                by_provider_voice.setdefault((voice['provider'], version['provider_voice_id']), voice['id'])
        usage: dict[str, list[dict]] = {}
        for book_id, title, characters in rows:
            for character in json.loads(characters or '[]'):
                voices = character.get('voices') if isinstance(character.get('voices'), dict) else {}
                for provider in LIBRARY_PROVIDERS:
                    selection = voices.get(provider)
                    follows, voice_id = 'assigned', library_reference(selection)
                    if voice_id is None and isinstance(selection, dict) and isinstance(selection.get('id'), str):
                        voice_id = by_provider_voice.get((provider, selection['id']))
                    if selection is None and provider == 'breeze':
                        # No Breeze choice means the character follows the Breeze default.
                        follows, voice_id = 'default', default_voice_ids.get(provider)
                    if voice_id:
                        usage.setdefault(voice_id, []).append({
                            'book_id': book_id, 'book_title': title or 'Untitled', 'character_id': character.get('id'),
                            'character_name': character.get('name') or character.get('id'), 'follows': follows})
        return usage
