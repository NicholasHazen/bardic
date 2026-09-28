"""Contract entries for the voices route family: the library-wide voice library.

Routes live in ``bardic/voice_routes.py``; storage in ``bardic/voice_library.py``;
provider adapters in ``bardic/breeze.py`` and ``bardic/gemini_voices.py``.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import Op, View, op
from .books import Book

# ----------------------------------------------------------------- shared text

_FAMILY = (
    'Voices belong to the whole library, not to a book. Breeze requests are free but run on the owner\'s '
    'self-hosted GPU; every Gemini voice create is billed. Provider error text is never echoed: Gemini errors '
    'carry only the HTTP status and a fixed hint, Breeze errors keep only the server\'s error code.'
)

_SINGLE_VIEW_STATE = (
    '\n\nThe returned `LibraryVoice` is built without the saved provider checks, so each version\'s '
    '`server_state` is `unknown` (or `other_project` for a Gemini version made with another key) and '
    '`assignable`/`warnings` reflect only that. Call `GET /api/voices` for checked server states.'
)

_REVOICE = (
    '\n\nThis re-voices every character that follows the affected voice: selected takes made with the '
    'previous voice become out of date but stay in history, and rendering again after switching back reuses '
    'the archived WAV without a provider request. Refused with 409 while a `render`, `listen`, '
    '`listen_chapter` or `voice_preview` job is queued or running for a book with a character following '
    'the voice.'
)

_NARRATION_409 = ('A `render`, `listen`, `listen_chapter` or `voice_preview` job is queued or running for a book '
                  'whose characters follow an affected voice ("Narration is being prepared for a book that uses '
                  'this voice…").')

_DRAFT_404 = 'The draft does not exist ("Voice draft not found"), including a malformed draft ID.'
_VOICE_404 = 'The library voice does not exist ("Voice not found"), including a malformed voice ID.'
_CLAIM_409 = ('The draft is no longer open ("This voice draft is already finished."), or another request is '
              'working on it ("This voice draft is already working. Wait for it to finish.").')

# ----------------------------------------------------------------------- views


class VoiceCharacterContext(View):
    """The book character a draft or voice was made for."""
    book_id: str = Field(description='The book the character belongs to.')
    character_id: str = Field(description='Book-local character ID (not a series identity).')
    character_name: str | None = Field(description='The character\'s name when the draft or voice was made; '
                                                   'a snapshot, not updated by later renames. Null if the '
                                                   'character had no name.')


class LibraryVoiceRecipe(View):
    """How a voice version was made. Only keys with a non-null stored value are present."""
    description: str | None = Field(None, description='The voice description (Breeze design prompt, Gemini '
                                                      'prompted-voice input, or the clone/import description).')
    sample_text: str | None = Field(None, description='Breeze: the text the auditioned clip speaks (design '
                                                      'sample text or clone transcript). Absent for Gemini.')
    model: str | None = Field(None, description='Gemini design model used, for example `gemini-3.8-flash-tts`. '
                                                'Gemini only.')
    language_code: str | None = Field(None, description='Gemini language tag, for example `en-US`. Gemini only.')
    gender: str | None = Field(None, description='Gemini gender hint (`female`, `male` or `neutral`). Gemini '
                                                 'only; absent when none was given.')


class LibraryVoiceVersion(View):
    """One immutable version of a library voice: one fixed provider voice."""
    version: int = Field(description='Version number, starting at 1. Versions are append-only.')
    provider_voice_id: str = Field(description='The provider voice: a Breeze server voice ID (for example '
                                               '`bardic-1a2b3c4d`) or a Gemini `voice_…` ID.')
    revision: str | None = Field(description='Breeze: the pinned revision, an opaque 64-hex hash of the '
                                             'speech-affecting server state when the version was saved. '
                                             'Narration is refused if the live server voice no longer matches. '
                                             'Null for Gemini.')
    made: Literal['designed', 'cloned', 'imported'] = Field(
        description='How this version was made: saved from a design draft, cloned from an uploaded recording, '
                    'or imported from the Breeze server by a connection check.')
    created_at: str = Field(description='When the version was saved (ISO 8601 UTC).')
    expires_at: str | None = Field(description='Gemini: when Google deletes the stored voice (the provider\'s '
                                               'timestamp string; stored voices live about one year). Null for '
                                               'Breeze or when unknown.')
    server_state: Literal['ok', 'changed', 'missing', 'unknown', 'other_project'] = Field(
        description='Result of comparing this version with the last saved provider check, computed locally. '
                    '`ok`: present (Breeze: same revision). `changed`: Breeze only, the server voice changed since '
                    'it was saved. `missing`: not in the last check. `unknown`: no check to compare with (Breeze '
                    'never checked or checked against another URL; Gemini project voices not refreshed with the '
                    'current key; or a single-voice response, which never compares). `other_project`: Gemini '
                    'only, made with a different Google API key than the current one.')
    audition_url: str = Field(description='Root-relative URL of this version\'s audition WAV '
                                          '(`GET /api/voices/{voice_id}/versions/{version}/audition`).')
    recipe: LibraryVoiceRecipe = Field(description='How the version was made.')


class LibraryVoiceUsage(View):
    """One character that follows a voice."""
    book_id: str = Field(description='Book containing the character. Archived (removed) books are excluded.')
    book_title: str = Field(description='The book title, or "Untitled".')
    character_id: str = Field(description='Book-local character ID.')
    character_name: str = Field(description='Character name, or its ID when unnamed.')
    follows: Literal['assigned', 'default'] = Field(
        description='`assigned`: the character\'s cast entry names this voice (a library reference, or a direct '
                    'provider pin of a provider voice behind one of its versions). `default`: the character has '
                    'no Breeze choice and this is the Breeze default voice.')


class LibraryVoice(View):
    """A named library voice with its versions ("VoiceView")."""
    id: str = Field(description='Library voice ID, `vl_` followed by 16 hex digits.')
    provider: Literal['breeze', 'gemini'] = Field(description='The provider every version belongs to.')
    name: str = Field(description='Display name, 1–100 characters.')
    description: str = Field(description='Free-text description, up to 1,000 characters.')
    origin: Literal['designed', 'cloned', 'imported'] = Field(
        description='How the voice was first made: saved from a design draft, cloned from a recording, or '
                    'imported from the Breeze server.')
    current_version: int = Field(description='The version characters following this voice use now.')
    is_default: bool = Field(description='True when this is the provider\'s default voice (Breeze only).')
    deleted: bool = Field(description='True for a deleted (tombstoned) voice. `GET /api/voices` lists only live '
                                      'voices; single-voice responses never return a deleted one.')
    assignable: bool = Field(description='Whether the cast can use it now: the current version\'s '
                                         '`server_state` is `ok` or `unknown`, and for Gemini the selected speech '
                                         'model accepts designed voices.')
    versions: list[LibraryVoiceVersion] = Field(description='All versions, oldest first.')
    usage: list[LibraryVoiceUsage] = Field(description='Characters (in non-archived books) that follow this voice.')
    source: VoiceCharacterContext | None = Field(description='The character the voice was designed or cloned for, '
                                                             'or null.')
    warnings: list[str] = Field(description='Human-readable problems: the current version changed on the Breeze '
                                            'server (narration refused), is missing from the server, was made with '
                                            'another Google key, or the selected Gemini speech model accepts only '
                                            'built-in voices.')


class VoiceDraftCandidate(View):
    """One generated candidate in a design draft."""
    id: str = Field(description='Candidate ID, unique within the draft: `c1`, `c2`, … in creation order.')
    kind: Literal['breeze_preview', 'gemini_voice'] = Field(
        description='`breeze_preview`: a temporary Breeze voice preview (Bardic keeps its audio). `gemini_voice`: '
                    'a stored, billed voice in the Google project.')
    seed: int | None = Field(description='Breeze preview seed; null for Gemini.')
    provider_voice_id: str | None = Field(description='The Gemini `voice_…` ID; null for Breeze previews.')
    description: str = Field(description='The draft description the candidate was generated from.')
    sample_text: str | None = Field(description='Breeze: the text the preview speaks. Null for Gemini.')
    duration: float | None = Field(description='Length of the retained audio in seconds; null when no audio was '
                                               'retained (a Gemini sample that could not be stored).')
    created_at: str = Field(description='When the candidate was generated (ISO 8601 UTC).')
    expires_at: str | None = Field(description='Provider expiry timestamp string: the Breeze preview\'s expiry '
                                               '(about 24 hours; irrelevant to saving, which uploads the retained '
                                               'clip) or the Gemini stored voice\'s expiry. Null when not reported.')
    discarded: bool = Field(description='True once discarded (explicitly, or by abandoning the draft).')
    audio_url: str | None = Field(description='Root-relative URL of the retained audio '
                                              '(`GET /api/voices/drafts/{draft_id}/candidates/{candidate_id}/audio`), '
                                              'or null when none was retained.')


class VoiceDraft(View):
    """A voice design draft ("DraftView")."""
    id: str = Field(description='Draft ID, `vd_` followed by 16 hex digits.')
    provider: Literal['breeze', 'gemini'] = Field(description='The provider candidates are generated with.')
    base_voice_id: str | None = Field(description='Library voice this draft iterates on (enables `mode: "version"` '
                                                  'on save), or null.')
    base_voice_name: str | None = Field(description='Current name of the base voice; null when there is none or '
                                                    'it can no longer be read.')
    context: VoiceCharacterContext | None = Field(description='The character the draft was started from, or null.')
    name: str = Field(description='Working name, up to 100 characters; may be empty.')
    description: str = Field(description='Voice description, up to 1,000 characters. Generation requires at least '
                                         '3 non-space characters.')
    sample_text: str = Field(description='Text Breeze previews speak (up to 1,000 characters). Unused by Gemini, '
                                         'whose drafts start with an empty string.')
    status: Literal['open', 'saved', 'abandoned'] = Field(
        description='Only `open` drafts can be edited, generated, discarded, saved or abandoned. '
                    '`GET /api/voices` lists only open drafts.')
    busy: bool = Field(description='True while a generate, discard, abandon or save request is working on the '
                                   'draft in this server process (in memory; cleared by a restart).')
    created_at: str = Field(description='ISO 8601 UTC.')
    updated_at: str = Field(description='ISO 8601 UTC; changes on every edit, generation and state change.')
    candidates: list[VoiceDraftCandidate] = Field(description='Every candidate ever generated, including discarded '
                                                              'ones, oldest first.')


class VoiceLibraryDefaults(View):
    """Default library voice per provider. Only Breeze has a default."""
    breeze: str | None = Field(description='The Breeze default library voice ID, or null when none is set. '
                                           'Characters without a Breeze choice follow it.')


class VoiceLibraryBreezeServerVoice(View):
    """A voice on the Breeze server, as pinned by the last connection check."""
    id: str = Field(description='Breeze server voice ID (lowercase letters, digits, `_`, `-`; up to 64).')
    name: str = Field(description='Server display name (up to 200 characters); the ID when unnamed.')
    kind: str | None = Field(description='Server voice kind, for example `cloned` or `designed`; only `cloned` '
                                         'voices are stable enough to narrate.')
    description: str = Field(description='Server description (up to 500 characters); may be empty.')
    labels: dict[str, str] = Field(description='Server labels (up to 20). Bardic-made voices carry `bardic_voice` '
                                               '(library voice ID) and `bardic_version`.')
    usable: bool = Field(description='True when the voice can narrate: cloned, with a readable reference clip.')
    reason: str | None = Field(description='Why it is not usable, or null.')
    revision: str | None = Field(description='Pinned revision (opaque 64-hex hash of speech-affecting state), or '
                                             'null when not usable.')
    seed: int | None = Field(description='The server voice\'s own seed setting, if it has one.')


class VoiceLibraryBreezeStatus(View):
    """The last Breeze connection check, read locally without contacting the server.

    The same object appears as `breeze` in `GET /api/status` and is returned by
    `POST /api/narration/breeze/refresh`; it is described separately here.
    """
    configured: bool = Field(description='True when a Breeze server URL is set.')
    base_url: str = Field(description='The configured server root, or an empty string.')
    has_api_key: bool = Field(description='True when a Breeze API key is loaded (the key is never returned).')
    state: Literal['unconfigured', 'checking', 'unchecked', 'ready', 'loading', 'error', 'unreachable'] = Field(
        description='`unconfigured`: no URL. `checking`: a check is running now. `unchecked`: never checked for '
                    'this URL. Otherwise the last check\'s result: `ready`, `loading` (the model is still '
                    'loading), `error` (bad key, HTTP error, unreadable response or unsupported model) or '
                    '`unreachable`.')
    message: str = Field(description='Human-readable state explanation.')
    checked_at: str | None = Field(description='When the last check for this URL finished (ISO 8601 UTC), or null.')
    model: str | None = Field(description='Model the server reported, or null.')
    default_voice_id: str | None = Field(description='The server\'s own default voice ID (not Bardic\'s default), '
                                                     'or null.')
    voices: list[VoiceLibraryBreezeServerVoice] = Field(
        description='Server voices from the last check for this URL (at most 200). A failed check keeps the '
                    'previous voices. Voices Bardic creates or deletes are added or removed here immediately when the '
                    'saved check is for the current URL.')


class VoiceLibraryGeminiProjectVoice(View):
    """A stored voice in the Google project, from the last Gemini refresh."""
    id: str = Field(description='Gemini voice ID (`voice_…`).')
    display_name: str = Field(description='Display name (up to 200 characters); the ID when unnamed.')
    type: str = Field(description='`prompted` or `replicated` (the listing requests only these).')
    description: str = Field(description='Provider description (up to 500 characters); may be empty.')
    language_code: str = Field(description='Language tag, or empty.')
    in_library: bool = Field(description='True when some library voice version (including deleted voices) uses it.')
    draft_candidate: bool = Field(description='True when an undiscarded candidate of an open draft uses it.')


class VoiceLibraryGeminiStatus(View):
    """Gemini voice-design state and the last listing of the Google project's stored voices."""
    has_api_key: bool = Field(description='True when a Gemini API key is loaded (the key is never returned).')
    tts_model: str = Field(description='The selected Gemini speech model preference.')
    state: Literal['unchecked', 'ready', 'error'] = Field(
        description='`unchecked`: never refreshed with the current key (a listing made with another key is '
                    'ignored). `ready` or `error`: the last refresh result.')
    message: str = Field(description='Human-readable result, for example "3 stored voices in this Google '
                                     'project."; empty when unchecked.')
    checked_at: str | None = Field(description='When the last refresh with the current key finished (ISO 8601 '
                                                'UTC), or null.')
    design_models: list[str] = Field(description='Gemini speech models that accept designed voices.')
    designed_voices_supported: bool = Field(description='True when `tts_model` is one of `design_models`. '
                                                        'Otherwise Gemini library voices are not assignable.')
    stored_count: int | None = Field(description='Number of stored voices in the last listing; null when never '
                                                 'listed or the last refresh failed.')
    limit: int = Field(description='Google\'s stored-voice limit per project (200).')
    project_voices: list[VoiceLibraryGeminiProjectVoice] = Field(
        description='Stored voices from the last successful listing with the current key, newest first.')


class VoiceLibraryProviders(View):
    """Provider state for the voice library."""
    breeze: VoiceLibraryBreezeStatus
    gemini: VoiceLibraryGeminiStatus


class VoiceLibrarySystemVoice(View):
    """An installed macOS device voice."""
    id: str = Field(description='Voice name as the `say` command knows it.')
    name: str = Field(description='Same as `id`.')
    locale: str = Field(description='Locale with a hyphen, for example `en-US`. English voices are listed first.')


class VoiceLibraryBuiltins(View):
    """Provider built-in voices that are not library voices."""
    gemini: list[str] = Field(description='Gemini prebuilt voice names (for example `Kore`).')
    system: list[VoiceLibrarySystemVoice] = Field(description='Installed macOS voices; empty when `say` is unavailable.')


class VoiceLibraryOverview(View):
    """Everything the Voices screen needs, read from local state."""
    voices: list[LibraryVoice] = Field(description='Live (not deleted) library voices in creation order.')
    defaults: VoiceLibraryDefaults
    drafts: list[VoiceDraft] = Field(description='Open drafts, newest first.')
    providers: VoiceLibraryProviders
    builtin: VoiceLibraryBuiltins


class LibraryVoiceDeleted(View):
    """Result of deleting a library voice."""
    deleted: str = Field(description='The library voice ID.')
    server_deleted: list[str] = Field(description='Provider voice IDs deleted on the provider in this request, '
                                                  'sorted. Empty when removed from Bardic only, or when the voice '
                                                  'was already deleted.')


class VoiceLibraryDefaultsResult(View):
    """The defaults after a change."""
    defaults: VoiceLibraryDefaults


class VoiceDraftSaved(View):
    """Result of saving a draft candidate as a library voice or version."""
    voice: LibraryVoice = Field(description='The new voice, or the base voice with its new current version.')
    book: Book | None = Field(None, description='The full book document after assignment; present only when '
                                                '`assign` was given and the assignment succeeded.')
    assignment_error: str | None = Field(None, description='Present when `assign` was given and failed (for '
                                                           'example the book is busy or the character is missing). '
                                                           'The voice is still saved; nothing is rolled back.')
    cleanup_error: str | None = Field(None, description='Gemini only. Present when an unchosen stored candidate '
                                                        'could not be deleted (the first failure message), or some '
                                                        'were made with another Google key and remain in that '
                                                        'project.')


class BreezeVoiceCloned(View):
    """Result of cloning a Breeze voice from a recording."""
    voice: LibraryVoice = Field(description='The new cloned library voice.')
    book: Book | None = Field(None, description='The full book document after assignment; present only when '
                                                '`book_id` and `character_id` were given and the assignment '
                                                'succeeded.')
    assignment_error: str | None = Field(None, description='Present when the assignment failed. The voice is still '
                                                           'created; nothing is rolled back.')


# ------------------------------------------------------------------ operations

OPS: list[Op] = [
    op('GET', '/api/voices', 'getVoiceLibrary', 'Voices', 'Get the voice library',
       _FAMILY + '\n\nLocal only: reads SQLite and the saved Breeze and Gemini checks; never contacts a provider. '
       'Returns live library voices, the defaults, open drafts, provider state and built-in voices. Each '
       'version\'s `server_state` compares it with the last saved Breeze check (for this URL) and the last '
       'Gemini listing (for this key).\n\n'
       '**Known defect:** after a Gemini refresh that failed (`providers.gemini.state` would be `error`), this '
       'route fails with a plain-text HTTP 500 until a refresh succeeds or the Gemini key changes, because the '
       'failed listing is stored as null and then iterated.',
       response=VoiceLibraryOverview,
       errors={500: 'Known defect: the last Gemini refresh for the current key failed (plain-text body, not JSON).'},
       cost='none'),

    op('POST', '/api/voices/gemini/refresh', 'refreshGeminiVoices', 'Voices', "List the Google project's stored voices",
       'Lists the Google project\'s stored `prompted` and `replicated` voices (metadata only; no generation, up to '
       '5 pages of 100) and saves the listing with a hash of the key, so a listing made with another key is '
       'ignored. A provider failure is saved as `state: "error"` with a message rather than returned as an HTTP '
       'error. Returns `providers.gemini` of the library overview.\n\n'
       '**Known defect:** when the listing fails, the failure is saved and then this route (and '
       '`GET /api/voices`) fails with a plain-text HTTP 500 instead of returning `state: "error"`.',
       response=VoiceLibraryGeminiStatus,
       errors={400: 'No Gemini API key is loaded ("Add a Gemini API key in Settings first."), or Gemini returned '
                    'a body that is not JSON (the decoder message is the detail).',
               409: 'The Gemini key changed during the check ("Refresh again."); nothing is saved.',
               500: 'Known defect: the listing failed (see description; plain-text body).'},
       cost='network'),

    op('PATCH', '/api/voices/{voice_id}', 'updateLibraryVoice', 'Voices', 'Rename or redescribe a voice',
       'Changes the name and/or description; an omitted or null field is unchanged. Values are trimmed. '
       'Versions and pinned revisions do not change. For a Breeze voice, when a Breeze URL is configured, each '
       'distinct server voice behind its versions is renamed on the server best effort (the untrimmed values are '
       'sent; failures are ignored because the library record is authoritative). Gemini voices change locally '
       'only.' + _SINGLE_VIEW_STATE,
       response=LibraryVoice,
       errors={400: 'The name is only whitespace ("The voice name must be 1–100 characters.").',
               404: _VOICE_404 + ' Deleted voices also return 404.'},
       params={'voice_id': 'Library voice ID (`vl_…`).'},
       cost='network'),

    op('POST', '/api/voices/{voice_id}/current', 'setLibraryVoiceCurrentVersion', 'Voices',
       'Switch the current version',
       'Makes an existing version the current one. Choosing the version that is already current is a no-op that '
       'still returns 200. Local only.' + _REVOICE + _SINGLE_VIEW_STATE,
       response=LibraryVoice,
       errors={400: 'The version does not exist ("That version does not exist.").',
               404: _VOICE_404 + ' Deleted voices also return 404.',
               409: _NARRATION_409},
       params={'voice_id': 'Library voice ID (`vl_…`).'},
       cost='none'),

    op('DELETE', '/api/voices/{voice_id}', 'deleteLibraryVoice', 'Voices', 'Delete a voice',
       'Deletes the voice\'s provider voices (optionally), then marks the library voice deleted. The record is '
       'kept as a tombstone: it disappears from `GET /api/voices`, its versions and history stay, and a Breeze '
       'server voice behind a deleted voice is never re-imported by a connection check. Characters still '
       'assigned to it fail closed: narration is refused with "The voice … was deleted. Choose another voice in '
       'the cast."\n\n'
       '**Provider deletion (`server`)**: when omitted, voices Bardic made (`origin` `designed` or `cloned`) are '
       'deleted on the provider, while voices imported from the Breeze server are removed from Bardic only. '
       '`server=true` deletes every distinct provider voice behind every version (Breeze server voices, or '
       'stored Gemini voices in the current Google project); `server=false` never touches the provider. A '
       'provider that says the voice is already gone (404) counts as deleted. Deletion is not atomic: if one of '
       'several provider voices fails to delete, the earlier ones are already gone, the library voice is not '
       'marked deleted, and the request fails; retrying is safe.\n\n'
       'Deleting an already-deleted voice returns 200 with an empty `server_deleted` and changes nothing. '
       'Refused while any narration job is active for a book that follows the voice.',
       response=LibraryVoiceDeleted,
       errors={400: 'Provider deletion was requested and the provider is not usable: no Breeze server URL ("Add the '
                    'Breeze server URL in Settings first, or choose another narrator."), no Gemini key ("Add a '
                    'Gemini API key in Settings first."), or the provider request failed (a provider failure here '
                    'is 400, not 502; the detail is Bardic\'s own message such as "Breeze returned HTTP 500…" or '
                    '"Gemini returned HTTP 403 while deleting a voice…").',
               404: _VOICE_404,
               409: 'The voice is the Breeze default ("Choose another default before deleting it."); a Gemini '
                    'voice has a version made with a different Google API key and provider deletion was requested '
                    '("…or remove it from Bardic only." — retry with `server=false`); or ' + _NARRATION_409},
       params={'voice_id': 'Library voice ID (`vl_…`).',
               'server': 'Whether to delete the provider voices too. Omit for the default (true for designed or '
                         'cloned voices, false for imported ones). Accepts `true`/`false` (also `1`/`0`, '
                         '`yes`/`no`, `on`/`off`).'},
       cost='network'),

    op('POST', '/api/voices/defaults', 'setDefaultLibraryVoice', 'Voices', 'Set the Breeze default voice',
       'Makes a live Breeze library voice the Breeze default. Characters without a Breeze choice follow it, and '
       'simple listening uses it for Breeze when no voice is chosen. Choosing the current default again is a '
       'no-op. A change is recorded in the voice history. Local only.' + _REVOICE.replace(
           'the affected voice', 'the previous or the new default')
       + ' Both the previous and the new default\'s followers are checked.',
       response=VoiceLibraryDefaultsResult,
       errors={400: 'The voice is deleted or is not a Breeze voice ("Choose a Breeze voice from the library as the '
                    'default.").',
               404: _VOICE_404,
               409: _NARRATION_409},
       cost='none'),

    op('GET', '/api/voices/{voice_id}/versions/{version}/audition', 'getLibraryVoiceAudition', 'Voices',
       "Play a voice version's audition",
       'Returns the version\'s retained audition WAV (Bardic\'s 24 kHz mono copy of the auditioned preview, '
       'recording or imported reference clip). When none is retained, fetches the Breeze reference clip or the '
       'Gemini stored voice\'s sample from the provider on every request (not cached) and returns those bytes '
       'labelled `audio/wav`. Works for deleted voices too.',
       media='audio/wav', ranges=True,
       errors={400: 'A provider fetch is needed and the provider is not configured: no Breeze server URL, or no '
                    'Gemini API key; or Gemini returned a body that is not JSON.',
               404: _VOICE_404 + ' Also "Voice version not found", or "No audition audio is available for this '
                    'voice." when the provider has no sample.',
               502: 'The provider request failed (Bardic\'s own message; provider text is not echoed).'},
       params={'voice_id': 'Library voice ID (`vl_…`).', 'version': 'Version number (integer, from 1).'},
       cost='network'),

    op('POST', '/api/voices/drafts', 'createVoiceDraft', 'Voices', 'Start a voice design draft',
       'Creates an open draft. Nothing is generated and no provider is contacted.\n\n'
       'Fill order for `name`, `description` and `sample_text` (an explicit value, including an empty string, '
       'always wins):\n'
       '1. With both `book_id` and `character_id`, the character fills them: name, description (the character '
       'profile description plus its direction, joined by a space, cut to 1,000 characters) and sample text '
       '(the character\'s first attributed passage as an exact prefix of at most 400 code points, cut at a '
       'sentence or word boundary, else the demo text). The draft\'s `context` records the character. With only '
       'one of the two IDs, both are ignored.\n'
       '2. `base_voice_id` fills what is still unset from that voice: its name, its current version\'s recipe '
       'description (else the voice description) and recipe sample text. Because a character fills all three '
       'fields, a base voice fills nothing when a character is also given.\n'
       '3. A Breeze draft with no sample text uses the built-in demo text; a Gemini draft keeps an empty '
       'sample text (Gemini does not use it).',
       response=VoiceDraft,
       errors={400: 'The base voice is deleted or belongs to the other provider ("Iterate on an existing voice of '
                    'the same provider."), or a filled-in value is too long (for example a character name over '
                    '100 characters).',
               404: 'The book ("Book not found"), the character ("Character not found in this book") or the base '
                    'voice ("Voice not found") does not exist.'},
       cost='none'),

    op('PATCH', '/api/voices/drafts/{draft_id}', 'updateVoiceDraft', 'Voices', 'Edit a voice draft',
       'Changes the working name, description and/or sample text of an open draft; an omitted or null field is '
       'unchanged, and values are trimmed. Existing candidates keep the text they were generated from. Allowed '
       'while a generation is running (the running generation uses the text it started with). Local only.',
       response=VoiceDraft,
       errors={404: _DRAFT_404,
               409: 'The draft is finished ("This voice draft is already finished.").'},
       params={'draft_id': 'Draft ID (`vd_…`).'},
       cost='none'),

    op('POST', '/api/voices/drafts/{draft_id}/generate', 'generateVoiceDraftCandidates', 'Voices',
       'Generate draft candidates',
       'Synchronous: the request returns when generation finishes and returns the updated draft.\n\n'
       '**Breeze** (free, self-hosted GPU): generates `count` (1–3, default 2) previews of the draft description '
       'speaking the draft sample text, and retains each preview\'s audio. Takes roughly the audio length times '
       '`count`. `language_code`, `gender` and `confirm_cost` are ignored. With `book_id`, the request is '
       'recorded in that book\'s resource ledger (stage `voice_design`, cost basis `self_hosted`, $0).\n\n'
       '**Gemini** (billed): each call creates **one** stored, billed prompted voice in the Google project '
       '(`count` is ignored), named after the draft name (or "Bardic voice"), using the selected speech model '
       'when it is a design model, else the first design model. `confirm_cost: true` and `book_id` are required; '
       'the request is recorded in that book\'s resource ledger with an unknown cost (never recorded as $0). A '
       'create that times out or returns an unusable response is never resent and returns 502: the voice may '
       'exist and be billed, so refresh Gemini voices (`POST /api/voices/gemini/refresh`) and look for it in '
       '`project_voices`. If the returned sample cannot be stored, the candidate is kept without audio.\n\n'
       'Validation (400s) happens before the draft is claimed. While the request runs, the draft is `busy` and '
       'other generate, discard, abandon and save requests on it get 409.',
       response=VoiceDraft,
       errors={400: 'The description is shorter than 3 characters; Gemini without `confirm_cost: true` ("Confirm '
                    'that this creates a billed, stored Gemini voice."), without `book_id`, without a Gemini key, '
                    'or with an invalid name, language tag or gender; Breeze without sample text or without a '
                    'server URL; or the draft was finished by a concurrent request.',
               404: _DRAFT_404 + ' Also "Book not found" for an unknown `book_id`.',
               409: _CLAIM_409,
               502: 'The provider request failed, or a billed Gemini create may or may not have happened (see '
                    'description). Breeze also returns 502 when the preview audio cannot be converted locally.'},
       params={'draft_id': 'Draft ID (`vd_…`).'},
       cost='may_charge'),

    op('GET', '/api/voices/drafts/{draft_id}/candidates/{candidate_id}/audio', 'getVoiceDraftCandidateAudio',
       'Voices', "Play a draft candidate's audio",
       'Bardic\'s retained copy of a candidate\'s audio (24 kHz mono WAV). Available for discarded candidates '
       'and finished drafts too. Local only.',
       media='audio/wav', ranges=True,
       errors={404: _DRAFT_404 + ' Also "Preview audio not found" when the candidate does not exist or has no '
                    'retained audio.'},
       params={'draft_id': 'Draft ID (`vd_…`).', 'candidate_id': 'Candidate ID within the draft (`c1`, `c2`, …).'},
       cost='none'),

    op('POST', '/api/voices/drafts/{draft_id}/candidates/{candidate_id}/discard', 'discardVoiceDraftCandidate',
       'Voices', 'Discard a draft candidate',
       'Marks the candidate discarded so it cannot be saved. A Gemini candidate\'s stored voice is first deleted '
       'from the Google project (already gone counts as deleted). A Breeze preview is only marked; it expires on '
       'the server by itself. Discarding an already-discarded candidate succeeds without contacting a provider. '
       'Returns the updated draft.',
       response=VoiceDraft,
       errors={400: 'A Gemini candidate needs deleting and no Gemini key is loaded.',
               404: _DRAFT_404 + ' Also "Candidate not found".',
               409: _CLAIM_409 + ' Also a Gemini candidate made with a different Google API key ("This candidate '
                    'was made with a different Google API key.").',
               502: 'The Gemini delete failed; the candidate stays undiscarded.'},
       params={'draft_id': 'Draft ID (`vd_…`).', 'candidate_id': 'Candidate ID within the draft (`c1`, `c2`, …).'},
       cost='network'),

    op('POST', '/api/voices/drafts/{draft_id}/abandon', 'abandonVoiceDraft', 'Voices', 'Abandon a voice draft',
       'Deletes every undiscarded Gemini candidate\'s stored voice from the Google project, then marks every '
       'candidate discarded and the draft `abandoned`. Breeze previews are only marked. If any deletion fails, '
       'the draft stays open (some voices may already be deleted; retrying is safe). Returns the abandoned draft.',
       response=VoiceDraft,
       errors={400: 'Gemini candidates need deleting and no Gemini key is loaded.',
               404: _DRAFT_404,
               409: _CLAIM_409 + ' Also when some undiscarded Gemini candidates were made with a different Google '
                    'API key ("Switch back to that key to delete them before abandoning this draft.").',
               502: '"Some stored Gemini candidates could not be deleted: …" (the first failure).'},
       params={'draft_id': 'Draft ID (`vd_…`).'},
       cost='network'),

    op('POST', '/api/voices/drafts/{draft_id}/save', 'saveVoiceDraft', 'Voices', 'Save a draft candidate',
       'Saves one undiscarded candidate as a new library voice (`mode: "new"`) or as a new current version of '
       'the draft\'s base voice (`mode: "version"`), and marks the draft `saved`.\n\n'
       '**Breeze**: uploads the exact auditioned clip as a new cloned server voice (ID `bardic-` plus 8 hex '
       'digits, labelled with the library voice ID and version), so saving works after the preview expired. The '
       'server re-encodes the clip; the version pins the revision of what the server keeps, and the retained '
       'clip becomes the version\'s audition. **Gemini**: the candidate\'s stored voice becomes the version (no '
       'new billed request); unchosen undiscarded Gemini candidates made with the current key are deleted from '
       'the project, and failures are reported in `cleanup_error`.\n\n'
       '`make_default: true` (Breeze only) then makes the voice the Breeze default. `assign` then assigns the '
       'voice to that character, exactly like a cast edit; a failed assignment is reported in '
       '`assignment_error` and never rolled back. `mode: "version"` re-voices every follower of the base voice, '
       'and `make_default` re-voices characters on Default, so both are refused with 409 while narration runs '
       'for affected books.\n\n'
       '**Not atomic:** a Breeze server voice is uploaded before the library record is written. If the record '
       'then fails (for example a whitespace-only name, or a base voice deleted meanwhile), the request fails and '
       'the uploaded server voice is left on the server without a library voice.',
       response=VoiceDraftSaved,
       errors={400: 'The candidate does not exist or is discarded ("Choose a candidate that has not been '
                    'discarded."); `mode: "version"` without a base voice; `make_default` for Gemini ("Only Breeze '
                    'has a default voice."); no Breeze server URL or no Gemini key; or a whitespace-only `name` '
                    '(after the Breeze upload; see description).',
               404: _DRAFT_404 + ' Also "Voice not found" when the base voice was deleted before a version save.',
               409: _CLAIM_409 + ' Also a Gemini candidate made with a different Google API key, or ' + _NARRATION_409,
               502: 'The Breeze upload or its read-back failed (Bardic\'s own message).'},
       params={'draft_id': 'Draft ID (`vd_…`).'},
       cost='network'),

    op('POST', '/api/voices/breeze/clone', 'cloneBreezeVoice', 'Voices', 'Clone a Breeze voice from a recording',
       'Multipart form upload. Creates a cloned voice on the Breeze server from a recording and its exact '
       'transcript (ID `bardic-` plus 8 hex digits, labelled with the new library voice ID), pins its revision, '
       'retains the server\'s reference clip as the audition (best effort; the voice is still created without '
       'it), and creates a library voice with `origin: "cloned"`. With both `book_id` and `character_id`, the '
       'voice records that character as its `source` and is assigned to it like a cast edit; a failed assignment '
       'is reported in `assignment_error`, never rolled back. With only one of the two, both are ignored. The '
       'Breeze server accepts recordings of 1–30 seconds (5–15 seconds of clean speech works best).\n\n'
       '**Not atomic:** the server voice is created before the book and character are looked up, so an unknown '
       'book or character returns 404 after the server voice exists, and no library voice is created for it.',
       response=BreezeVoiceCloned,
       errors={400: '`consent` is not exactly `true` ("Confirm that you have the speaker\'s consent to clone this '
                    'voice."); a blank name or transcript; an empty recording or one over 20 MB ("Upload a '
                    'recording of at most 20 MB."); or no Breeze server URL.',
               404: '"Book not found" or "Character not found in this book" (after the server voice was created; '
                    'see description).',
               502: 'The Breeze server refused or failed the clone (for example unreadable, silent or wrong-length '
                    'audio, or a bad API key), or was unreachable.'},
       cost='network'),
]

REQUEST_DOCS: dict[str, dict[str, str]] = {
    'DraftCreate': {
        '__doc__': 'Start a voice design draft. See the operation description for how fields are filled in.',
        'provider': '`breeze` (free previews on the self-hosted server) or `gemini` (billed stored voices).',
        'base_voice_id': 'Library voice (`vl_…`) of the same provider to iterate on; fills unset fields from its '
                         'current version and allows saving as a new version of it.',
        'book_id': 'Book of the character to design for. Used only together with `character_id`.',
        'character_id': 'Book-local character ID to design for. Used only together with `book_id`.',
        'name': 'Working name (up to 100 characters). Default: filled from the character or base voice, else empty.',
        'description': 'Voice description (up to 1,000 characters). Default: filled from the character or base '
                       'voice, else empty.',
        'sample_text': 'Text Breeze previews speak (up to 1,000 characters). Default: filled from the character or '
                       'base voice; else the demo text for Breeze and empty for Gemini.',
    },
    'DraftEdit': {
        '__doc__': 'Changes to an open draft. Omitted or null fields are unchanged; values are trimmed.',
        'name': 'Working name, up to 100 characters.',
        'description': 'Voice description, up to 1,000 characters.',
        'sample_text': 'Text Breeze previews speak, up to 1,000 characters.',
    },
    'GenerateRequest': {
        '__doc__': 'Options for generating draft candidates. Which fields apply depends on the draft provider.',
        'book_id': 'Book whose resource ledger records the request. Required for Gemini; optional for Breeze '
                   '(nothing is recorded without it).',
        'count': 'Breeze: number of previews to generate, 1–3 (default 2). Ignored for Gemini, which always '
                 'creates one voice per call.',
        'language_code': 'Gemini: language tag such as `en-US` (default) or `en-GB`. Ignored for Breeze.',
        'gender': 'Gemini: optional `female`, `male` or `neutral` hint. Ignored for Breeze.',
        'confirm_cost': 'Gemini: must be true, confirming this creates a billed, stored voice. Default false. '
                        'Ignored for Breeze.',
    },
    'Assignment': {
        '__doc__': 'A book character to assign a saved voice to, as a cast edit (the book must not be busy).',
        'book_id': 'The book.',
        'character_id': 'Book-local character ID in that book.',
    },
    'SaveRequest': {
        '__doc__': 'Save one draft candidate as a library voice or version.',
        'candidate_id': 'An undiscarded candidate of the draft (`c1`, `c2`, …).',
        'name': 'Voice name, 1–100 characters (trimmed; must not be only whitespace). For Breeze it is also the '
                'server voice name. With `mode: "version"` it is used for the server voice only; the library '
                'voice keeps its name.',
        'mode': '`new` (default) creates a new library voice. `version` adds a new current version to the draft\'s '
                'base voice (only for drafts started with `base_voice_id`).',
        'assign': 'Optional character to assign the saved voice to afterwards.',
        'make_default': 'Breeze only: also make the saved voice the Breeze default. Default false.',
    },
    'VoiceEdit': {
        '__doc__': 'Changes to a library voice. Omitted or null fields are unchanged; values are trimmed.',
        'name': 'Display name, 1–100 characters (must not be only whitespace).',
        'description': 'Description, up to 1,000 characters; an empty string clears it.',
    },
    'CurrentVersion': {
        '__doc__': 'The version to make current.',
        'version': 'An existing version number of the voice (from 1).',
    },
    'DefaultVoice': {
        '__doc__': 'The new default voice for a provider. Only Breeze has a default.',
        'provider': 'Must be `breeze`.',
        'voice_id': 'A live Breeze library voice (`vl_…`).',
    },
    'CloneBreezeVoiceForm': {
        '__doc__': 'Multipart form for cloning a Breeze voice from a recording.',
        'name': 'Voice name, up to 100 characters; must not be blank. Trimmed.',
        'reference_text': 'The exact words spoken in the recording, up to 2,000 characters; must not be blank.',
        'consent': 'Must be exactly `true`, confirming you have the speaker\'s consent to clone the voice.',
        'description': 'Optional description, up to 1,000 characters. Default empty.',
        'book_id': 'Optional book of a character to record as the source and assign the voice to. Used only '
                   'together with `character_id`.',
        'character_id': 'Optional book-local character ID. Used only together with `book_id`.',
        'reference_audio': 'The recording (a common audio format, at most 20 MB; the server accepts 1–30 seconds).',
    },
}
