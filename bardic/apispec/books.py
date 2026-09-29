"""Contract entries for the books route family: the book document and manual edits.

``Book`` is the shared view of the full book document. It is returned by the
routes here and also by import/demo (Library) and by voice save/clone
(Voices), which import it from this module.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .base import Op, View, internal, op
from .media import AudioRef, AudioTakeBreezeInfo, AudioTakeSentenceTiming, AudioTakeVoiceLibrary

# ------------------------------------------------------------------ shared text

_EDIT_LOCKS = (
    'A manual edit locks each field whose value it actually changes, so generated analysis never overwrites it '
    '(editors may resend a whole form; unchanged values are not locked). Confirming a passage\'s current speaker '
    '(sending the same `speaker_id` while its `confidence` is below 1.0) is a change: it sets `confidence` to 1.0 '
    'and locks the speaker. Lock state is server bookkeeping and is not part of the book document.\n\n'
    'A request that changes nothing (an empty body, only omitted or `null` fields, or values equal to the current '
    'ones) is a no-op: nothing is saved or locked, `revision` does not change, and the current book is returned.')

_EDIT_COMMON = (
    '\n\nEdits require a book that is not archived (409 `book_archived`) and that no queued or running job '
    '(409 `job_active`) or active series run (409 `series_run_active`) holds. There is no optimistic concurrency '
    'check: the last write wins, and every edit that changes something increments the book `revision` by 1.\n\n'
    'After a change, every passage\'s selected enhanced take is re-validated against its render recipe '
    '(passage text, speaker voice and direction, scene notes, provider, model). A take whose recipe no '
    'longer matches is deselected (the passage\'s `audio` becomes null). Its WAV bytes are kept, so restoring '
    'the previous values and rendering again reuses the archived take without a provider request. Every '
    'scene\'s `character_ids` is then recomputed (sorted) from its passages\' speakers.\n\n'
    'Omitted and `null` fields are ignored (except a passage `seed`, where `null` clears it); send an empty '
    'string or array to clear a value. Returns the full, presented book document.\n\n'
    "Before it changes the book, the current projection is recorded in the step pipeline's version history (as `baseline` or `external` versions of the capturable steps) when the history does not already explain it, so the replaced state stays restorable.")

_BOOK_NOT_FOUND = {'book_not_found': 'No book has this ID.'}
_BOOK_ERRORS = {404: _BOOK_NOT_FOUND}
_BUSY = {
    'job_active': 'A job is queued or running for this book.',
    'series_run_active': 'An active series run reserves this book.',
    'book_archived': 'The book is archived; restore it first.',
}
_VOICE_ERRORS = {
    'voice_provider_unknown': '`voices` names a provider other than `system`, `gemini` or `breeze`.',
    'library_voice_unavailable': 'A `library` voice does not exist, is deleted, or belongs to another provider.',
    'breeze_voice_unavailable': 'A Breeze `id` is not in the last Breeze voice check, or is not a usable (cloned) '
                                'voice.',
    'seed_not_applicable': 'A choice has a `seed` but is not a Breeze voice chosen by a nonblank `id`.',
}
_PRONUNCIATION_INVALID = {
    'pronunciation_invalid': 'The entry breaks a field rule (see the fields of `PronunciationEntry`).',
    'pronunciation_duplicate': 'Another entry already has this term (under the case rules).',
    'pronunciation_limit_reached': 'The book already has 500 entries.',
    'character_not_in_cast': '`character_id` is not a character in the book\'s current cast.',
}
_PRONUNCIATION_NOT_FOUND = {'pronunciation_not_found': 'No entry in this book has this ID.'}


# ------------------------------------------------------------------ book views

class BookCover(View):
    """Metadata of the book's cover thumbnail. The image bytes are served by `GET /api/books/{book_id}/cover`."""
    media_type: Literal['image/jpeg'] = Field(description='Media type of the stored thumbnail; always JPEG.')
    width: int = Field(description='Thumbnail width in pixels (at most 240).')
    height: int = Field(description='Thumbnail height in pixels (at most 360).')
    sha256: str = Field(description='Lowercase hex SHA-256 of the thumbnail bytes; changes when the cover changes.')
    source: Literal['epub'] = Field(description='Where the cover came from: the EPUB\'s own cover metadata.')


class BookAnalysisSummary(View):
    """The current overall analysis summary for the book.

    Only a short label of who produced the current projection. Detailed,
    resumable progress is in the analysis pipeline routes.
    """
    provider: str = Field(
        description='Who produced the current annotations: `local` (free heuristic draft, also at import), '
                    '`gemini`, `openai`, `anthropic`, `local_llm`, or a self-hosted service (`novel_analyzer`, '
                    '`booknlp`) accepted through the analysis pipeline. Open set.')
    model: str | None = Field(default=None, description='Model ID used, or null/absent for local or service analysis.')
    status: Literal['draft', 'partial'] = Field(
        description='`draft`: a complete draft awaiting review (import, the local draft, or a completed run of the removed '
                    'Classic engine). '
                    '`partial`: staged work in progress or a pipeline step accepted; other parts may be missing '
                    'or older.')
    notes: str | None = Field(
        default=None,
        description='Human-readable explanation of the draft and what to review. Display only. The server writes it '
                    'with every summary; treat an absent value as empty.')
    phase: str | None = Field(
        default=None,
        description='The pipeline step ID that was accepted (for example `discovery`, `profiles`, `directing`), or, '
                    'on books last analyzed by the removed Classic engine, its phase (`scan`, `profiles`, `direct`, '
                    '`full`). Absent for import and local drafts.')
    profiles_provisional: bool | None = Field(
        default=None,
        description='Written only by the removed Classic engine: true while character profiles still needed whole-book '
                    'discovery or refinement against current evidence.')


class BookLogicalSection(View):
    """A navigation (table of contents) entry inside one EPUB spine document.

    Metadata only: logical sections are not separately scheduled or
    analyzed. Offsets are into the containing chapter's `text`.
    """
    title: str = Field(description='Label from the EPUB navigation document (at most 300 characters).')
    start: int = Field(description='Zero-based Unicode code-point offset into the chapter text where the section begins.')
    end: int = Field(description='Exclusive end offset (code points): the next entry\'s start or the chapter length.')
    kind: Literal['chapter', 'section', 'front_matter', 'back_matter', 'recap'] = Field(
        description='Classification from the label and EPUB semantics.')
    depth: int = Field(description='Nesting depth in the table of contents; 0 is top level.')
    title_source: Literal['epub_nav', 'epub_ncx'] = Field(description='Navigation format the entry came from.')


class BookChapter(View):
    """One source container in reading order: an EPUB spine document or a TXT heading section.

    It is not necessarily a narrative chapter; see `kind`. `text` is the
    immutable canonical reading text. Structure fields are absent on books
    imported before structure metadata existed (no `structure_version`);
    `POST /api/books/{book_id}/repair-structure` adds them.
    """
    id: str = Field(description='Opaque chapter ID, stable for the life of the book.')
    index: int = Field(description='Zero-based position in import order. Sort by array order, not by this value; it can have gaps.')
    title: str = Field(description='Display title (from navigation, heading, landmark, or a fallback such as "Section 3").')
    text: str = Field(
        description='Canonical reading text, never rewritten by analysis. Scene-break ornament lines (`***`, '
                    '`---`, `•`, `⁂`) are replaced by the same number of spaces so offsets stay valid. All '
                    '`start`/`end` offsets in the book are zero-based Unicode code-point offsets into this string, '
                    'end-exclusive (not UTF-8 bytes, not UTF-16 indices).')
    kind: Literal['chapter', 'section', 'front_matter', 'back_matter', 'recap'] | None = Field(
        default=None,
        description='Structural classification. `chapter` is a narrative chapter; `section` an unlabeled or '
                    'multi-entry container that stays eligible for analysis; `front_matter`/`back_matter` '
                    'non-story material; `recap` a "story so far" section.')
    title_source: Literal['epub_nav', 'epub_ncx', 'heading', 'landmark', 'semantics', 'fallback'] | None = Field(
        default=None, description='Where `title` came from.')
    source_href: str | None = Field(
        default=None, description='Path of the EPUB spine document inside the archive; null for TXT imports.')
    logical_sections: list[BookLogicalSection] | None = Field(
        default=None, description='Table-of-contents entries inside this container (EPUB only; empty for TXT).')
    narrative_order: int | None = Field(
        default=None, description='1-based number among `kind: chapter` containers only; absent for other kinds. '
                                  'Never an invented chapter number.')
    trailing_text: str = Field(
        description='Presented only: the chapter text after its last passage (usually whitespace). Together with '
                    'each passage\'s `leading_text` and `text` it rebuilds `text` exactly.')


class BookScene(View):
    """A scene: a run of consecutive passages within one chapter, with performance notes.

    Scenes start from scene-break ornaments at import and may be split by
    analysis. A speaker listed in `character_ids` is attributed dialogue,
    not proof that the character is physically present.
    """
    id: str = Field(description='Opaque scene ID.')
    chapter_id: str = Field(description='The chapter containing every passage of this scene.')
    title: str | None = Field(default=None, description='Display title, for example "Chapter One · Scene 2". Rarely absent after a pipeline acceptance that proposed none.')
    summary: str | None = Field(default=None, description='Scene summary (draft or reviewed). Editable; at most 4,000 characters by hand.')
    tone: str | None = Field(default=None, description='Emotional tone notes; `Unreviewed` at import. Used in enhanced narration recipes.')
    direction: str | None = Field(default=None, description='Performance direction for the whole scene. Used in enhanced narration recipes.')
    segment_ids: list[str] = Field(description='IDs of the scene\'s passages, in reading order.')
    character_ids: list[str] = Field(description='IDs of characters attributed to its passages (including `narrator`/`unassigned`).')


class BookSpeakerCheck(View):
    """Comparison of a dialogue passage's speaker with the accepted BookNLP quote attribution.

    Present only on dialogue passages the check covered; dropped when a
    person changes the speaker. BookNLP is not trusted over the proposed
    speaker; the check only adjusts confidence and records disagreement.
    """
    source: Literal['booknlp'] = Field(description='The checking method.')
    result: Literal['agrees', 'differs', 'suggests', 'not_in_cast', 'narrator', 'no_quote'] = Field(
        description='`agrees`/`differs`: BookNLP named the same/another cast member. `suggests`: the passage is '
                    'unassigned and BookNLP names someone. `not_in_cast`: BookNLP\'s speaker matches no cast member. '
                    '`narrator`: BookNLP heard a first-person narrator not in the cast. `no_quote`: BookNLP found no '
                    'quotation for this passage.')
    speaker_id: str | None = Field(default=None, description='Cast character ID BookNLP attributed, or null.')
    speaker: str | None = Field(default=None, description='BookNLP\'s own name for the speaker, or null.')
    tag_conflict: bool | None = Field(default=None, description='True when BookNLP\'s own speech tag contradicts its speaker; then the comparison is only recorded.')


class BookTake(AudioRef):
    """The selected enhanced (cast) narration take of a passage.

    Presented only when it is still valid: its recipe fingerprint matches the
    passage's current speaker voice, directions, scene notes, provider and
    model, and its WAV file exists. Otherwise the passage's `audio` is null.
    Like every audio object, it has the common audio core, always present: `url`, `asset_id`, `duration`, `provider`, `model`, `voice` and `created_at`.
    """
    url: str = Field(
        description='Root-relative playback URL (`/api/audio/{book_id}/{segment_id}?v=…`; `audio/wav`). The '
                    '`v` query changes when the selected audio changes, so the URL is safe to cache and compare.')
    asset_id: str | None = Field(
        description='Hex SHA-256 of the WAV bytes (content address), also usable with '
                    '`GET /api/books/{book_id}/audio-assets/{asset_id}`. Null for takes made before content '
                    'addressing.')
    duration: float | None = Field(description='Audio duration in seconds, measured from the WAV; null only for a take '
                                               'stored without it.')
    provider: str = Field(description='Narration provider: `system` (device), `gemini` or `breeze`.')
    model: str | None = Field(description='Speech model ID (`macos-say` for device narration).')
    voice: str | None = Field(description='Concrete provider voice that performed the take, or null when not recorded.')
    created_at: str | None = Field(description='Always null for Studio takes: their retention time is not recorded on the take.')
    voice_library: AudioTakeVoiceLibrary | None = Field(default=None, description='Library voice that was followed, when the character used one; absent otherwise.')
    voice_revision: str | None = Field(default=None, description='Breeze only: the pinned server revision of the voice.')
    provider_timing: AudioTakeSentenceTiming | None = Field(default=None, description='Breeze only: sentence timing, or null when the server\'s timing was not usable.')
    breeze: AudioTakeBreezeInfo | None = Field(default=None, description='Breeze only: request details.')


class BookPassage(View):
    """A passage ("segment"): the reader and narration unit, anchored to exact source offsets.

    `chapter.text[start:end] == text`, counting Unicode code points.
    """
    id: str = Field(description='Opaque passage ID.')
    chapter_id: str = Field(description='Chapter containing the passage.')
    scene_id: str = Field(description='Scene containing the passage.')
    start: int = Field(description='Zero-based Unicode code-point offset of the first character in the chapter text.')
    end: int = Field(description='Exclusive end offset in code points.')
    text: str = Field(description='Exact source text of the passage (immutable).')
    kind: Literal['narration', 'dialogue'] = Field(description='Whether the passage is quoted speech or narration.')
    speaker_id: str = Field(
        description='Character ID of the voice for this passage. Narration uses `narrator`; unattributed '
                    'dialogue uses `unassigned`.')
    confidence: float = Field(
        description='Attribution confidence from 0 to 1. Analysis leaves dialogue below 0.65 unassigned; a '
                    'reviewed speaker assignment is 1.0.')
    direction: str = Field(description='Performance direction for this passage (empty when none).')
    cues: list[str] = Field(description='Short performance cue labels, for example `quiet` or `urgent`.')
    evidence: list[str] | None = Field(
        default=None,
        description='Exact source quotations that justify the attribution (copied from the source, never model '
                    'paraphrase). Absent until analysis sets it.')
    seed: int | None = Field(
        default=None,
        description='Take seed for seeded providers (Breeze), 0–4294967295, set by a passage edit; absent when '
                    'not set, in which case the speaker\'s Breeze voice seed applies. A new seed means a new take. '
                    'Ignored by Gemini and device narration.')
    speaker_check: BookSpeakerCheck | None = Field(default=None, description='BookNLP comparison; absent when not checked.')
    analysis_provider: str | None = Field(
        default=None,
        description='Provider whose annotation is current for this passage (`local`, an LLM provider, '
                    '`novel_analyzer` or `booknlp`). Absent before analysis; kept from the last analysis after a '
                    'manual edit.')
    analysis_model: str | None = Field(default=None, description='Model of that annotation; null for local or service providers.')
    audio: BookTake | None = Field(description='Presented: the selected enhanced take if still valid, else null.')
    leading_text: str = Field(
        description='Presented only: chapter text between the previous passage (or the chapter start) and this '
                    'passage, usually whitespace or a replaced scene-break ornament.')
    manual_fields: list[Literal['speaker_id', 'direction', 'cues', 'seed']] = Field(
        description='Presented only: the passage fields a person set by hand (with `editPassage`), which analysis '
                    'never replaces, in alphabetical order; empty when none. A passage edited before per-field '
                    'tracking lists every field, and so does a speaker confirmed before contract 0.2.0. The '
                    'server\'s lock bookkeeping itself is not published.')


class BookBreezeSettings(View):
    """Optional Breeze sampling overrides of a pinned choice. No current API or UI sets them."""
    temperature: float | None = Field(None, description='Sampling temperature override, 0.05-2.0. Absent or null keeps the Breeze voice\'s own setting.')
    cfg_scale: float | None = Field(None, description='Classifier-free guidance scale override, 0.5-10.0. Absent or null keeps the voice\'s own setting.')
    top_p: float | None = Field(None, description='Nucleus sampling probability override, 0.01-1.0. Absent or null keeps the voice\'s own setting.')
    top_k: int | None = Field(None, description='Top-k sampling override, an integer 1-1024. Absent or null keeps the voice\'s own setting.')


class BookCharacterVoice(View):
    """A character's saved voice choice for one narration provider.

    One of these forms: `{library}` follows a library voice's current version;
    `{id}` is a direct provider voice (Gemini built-in or project voice, or a
    device voice); `{id, revision, seed, settings?}` is a concrete Breeze pin.
    """
    library: str | None = Field(default=None, description='Library voice ID (`vl_` + 16 hex) whose current version is used.')
    id: str | None = Field(default=None, description='Direct provider voice ID.')
    revision: str | None = Field(default=None, description='Breeze pin: the voice revision from the last Breeze check.')
    seed: int | None = Field(default=None, description='Breeze pin: default take seed (0–4294967295).')
    settings: BookBreezeSettings | None = Field(default=None, description='Breeze pin: sampling overrides.')


class BookCharacter(View):
    """A book-local cast member.

    Character IDs are book-local, not series identities (series links are
    separate). Every book has the reserved characters `narrator` and
    `unassigned` (dialogue whose speaker needs review).
    """
    id: str = Field(description='Opaque book-local character ID; `narrator` and `unassigned` are reserved.')
    name: str = Field(description='Display name (1–100 characters when set by hand).')
    aliases: list[str] = Field(description='Other names that identify this character in the text.')
    description: str = Field(description='Voice and personality profile (draft or reviewed).')
    direction: str = Field(description='Standing performance direction for this character\'s voice.')
    evidence: list[str] | None = Field(
        default=None,
        description='Exact source quotations supporting the profile (at most 12). Absent on some characters saved '
                    'by older versions; treat as empty.')
    voices: dict[str, BookCharacterVoice] = Field(
        description='Saved voice choice per narration provider, keyed by `system`, `gemini` or `breeze`. A '
                    'missing provider means Default (Breeze: the library default voice; Gemini: Kore; device: '
                    'the system voice). Library references are shown as references, not resolved. Choices saved '
                    'by older versions in single-provider fields are included here.')
    former_names: list[str] | None = Field(
        default=None,
        description='Names replaced by a manual rename. Discovery still resolves them to this character; they are '
                    'not aliases.')
    profile_refined: bool | None = Field(default=None, description='True once a profile refinement produced the description and direction.')
    profile_provider: str | None = Field(default=None, description='Provider of the refined profile.')
    profile_model: str | None = Field(default=None, description='Model of the refined profile.')
    profile_priority: Literal['deep', 'standard', 'basic'] | None = Field(
        default=None, description='Effort tier of the refinement, from the free census: `deep`, `standard` or `basic`.')
    profile_state: Literal['reviewed', 'current', 'stale', 'draft'] | None = Field(
        default=None,
        description='Written only by the removed Classic engine: `reviewed` (edited by hand), `current` (refined against current '
                    'evidence), `stale` (refined, evidence changed since), `draft` (not refined).')
    profile_provisional: bool | None = Field(default=None, description='Written only by the removed Classic engine: true while the profile could still change.')


# ------------------------------------------------------------------ pronunciations

class BookPronunciation(View):
    """A book pronunciation: how narrators should say a word.

    Only the text sent to the narrator changes; chapter and passage text,
    offsets, search and analysis are untouched. Matching is whole-word (a
    letter, digit or underscore on either side prevents a match, so `Will`
    does not match `Willow`), longest term first, and case-sensitive unless
    `match_case` is false. A space in a term matches any whitespace, including
    a line break; straight and curly apostrophes are interchangeable; and a
    term matches text stored in either composed or decomposed Unicode form.
    """
    id: str = Field(description='Entry ID assigned by the server: `pr_` followed by 12 lowercase hex digits.')
    term: str = Field(description='The word or phrase as written in the book: at most 80 characters, whitespace collapsed, '
                                  'with at least one letter or digit.')
    respelling: str = Field(description='How to say it, for example `Kaylor` for `Cthaelor`: at most 120 characters. '
                                        'Control characters, brackets, parentheses, braces and backslashes are refused, '
                                        'because narrators perform `(laugh)`, `<sigh>` and `[[…]]` instead of reading them.')
    match_case: bool = Field(description='True: match the term\'s exact case. False: match any case. Two case-sensitive '
                                         'entries may differ only in case; otherwise a term appears once per book.')
    providers: dict[str, str] | None = Field(
        None, description='Per-narrator overrides of `respelling`, keyed by `system`, `gemini` or `breeze`; absent when '
                          'there are none. An override equal to the term leaves that narrator reading the word unchanged.')
    character_id: str | None = Field(
        None, description='Book-local character the word belongs to (informational); absent when none. It had to be in '
                          'the cast when the entry was added or changed; a link left by a character that analysis later '
                          'removed stays until the entry is edited.')
    note: str | None = Field(None, description='Free-text note, at most 500 characters; absent when empty.')


class Book(View):
    """The full book document: the reader's projection of one imported book.

    This is the stored book, decorated for presentation: each passage gets
    `leading_text`, each chapter `trailing_text` (together they rebuild the
    chapter text exactly), each passage's selected enhanced take is shown
    with a playback `url` only while it is valid (else null), and each
    character's `voices` map is normalized.

    `revision` increases with every change to the projection (edits,
    analysis publication, structure repair, metadata edits, pipeline
    acceptance). Clients can compare it to detect a newer document. There is
    no conditional update; edits are last-writer-wins.

    Imports include every passage of the book, so documents can be large (a
    novel is several megabytes).
    """
    id: str = Field(description='Book UUID.')
    title: str = Field(description='Display title.')
    author: str = Field(description='Display author(s), comma-separated; may be empty.')
    language: str | None = Field(
        description='BCP 47 language tag from the EPUB\'s first usable `dc:language` (at most 35 characters, `_` read '
                    'as `-`), or null for TXT files, EPUBs without one, the demo book and books imported before the '
                    'field existed. Metadata only; it never changes text or spans.')
    source_name: str = Field(description='File name of the imported original (without directories), for example `story.epub`.')
    created_at: str = Field(description='ISO 8601 UTC import time.')
    revision: int = Field(description='Projection revision; starts at 1 on import (the demo starts at 2) and increases by 1 per change.')
    structure_version: int | None = Field(
        default=None,
        description='Version of the structure interpretation (currently 2). Absent on books imported before '
                    'structure metadata; structure repair sets it.')
    chapters: list[BookChapter] = Field(description='Source containers in reading order.')
    scenes: list[BookScene] = Field(description='Scenes in reading order.')
    segments: list[BookPassage] = Field(description='All passages ("segments") in reading order.')
    characters: list[BookCharacter] = Field(description='The book-local cast, including `narrator` and `unassigned`.')
    analysis: BookAnalysisSummary = Field(description='Who produced the current annotations.')
    cover: BookCover | None = Field(default=None, description='Cover thumbnail metadata; absent when the original had no usable cover.')
    pronunciations: list[BookPronunciation] | None = Field(
        None, description='The book\'s pronunciations, in saved order; absent when there are none. Managed with the '
                          'Pronunciations operations, which also report where each term occurs.')


class PronunciationExample(View):
    """One occurrence of a term in chapter text."""
    chapter_id: str = Field(description='Chapter whose text the offsets index.')
    start: int = Field(description='Zero-based Unicode code-point offset of the match in the chapter text.')
    end: int = Field(description='Exclusive end offset of the match, in code points.')
    context: str = Field(description='The match with up to 60 code points of chapter text on either side.')


class PronunciationUsage(View):
    """Where a term occurs, counted as narration applies entries.

    Where terms overlap ("Tar Valon", "Valon") only the longer match counts.
    """
    occurrences: int = Field(description='Whole-word matches in all chapter text.')
    passages: int = Field(description='Passages containing at least one match.')
    rendered_passages: int = Field(description='Of those, passages with a current Studio (enhanced) take. Changing the '
                                               'entry retires these takes.')
    first_passage_id: str | None = Field(description='First passage containing the term, in reading order; null when none.')
    examples: list[PronunciationExample] = Field(description='Up to three occurrences, in reading order.')


class PronunciationWithUsage(BookPronunciation):
    """A pronunciation entry with its use in the book."""
    usage: PronunciationUsage = Field(description='Where the term occurs, computed from the current book text.')


class PronunciationList(View):
    """The book's pronunciations."""
    pronunciations: list[PronunciationWithUsage] = Field(description='All entries, in saved order, each with its usage.')


class PronunciationSaved(View):
    """The result of adding, changing or removing a pronunciation."""
    book: Book = Field(description='The updated book document; its `revision` has increased.')
    pronunciations: list[PronunciationWithUsage] = Field(description='All entries after the change, each with its usage.')
    retired_takes: int = Field(description='Studio takes that no longer match their recipe and were unselected. Their WAVs '
                                           'stay archived and are reused without a request if the recipe returns.')


# ------------------------------------------------------------------ references

class CharacterReference(View):
    """One source-anchored reference to a character in the current book.

    `chapter.text[start:end] == quote`. A mention is an explicit textual
    reference, not proof that the character is present in the scene.
    """
    id: str = Field(description='Stable hex ID derived from character, chapter, offsets and kind.')
    character_id: str = Field(description='The referenced character.')
    chapter_id: str = Field(description='Chapter whose text the offsets index.')
    segment_id: str | None = Field(description='The first passage overlapping the span, or null when none does.')
    start: int = Field(description='Zero-based Unicode code-point offset into the chapter text.')
    end: int = Field(description='Exclusive end offset in code points.')
    quote: str = Field(description='The exact source text of the span.')
    kind: Literal['dialogue', 'mention', 'profile_evidence'] = Field(
        description='`dialogue`: a dialogue passage currently attributed to the character. `mention`: the '
                    'character\'s name or an alias, unique within the cast, occurs in the text. `profile_evidence`: '
                    'a quotation a discovery request cited as evidence.')
    confidence: float | None = Field(default=None, description='Attribution confidence for `dialogue` (0–1); null otherwise.')
    provider: str | None = Field(
        default=None,
        description='Who produced it: the analysis provider of the passage\'s attribution or of the evidence, '
                    '`local` for mentions, or `reviewed` for a dialogue attribution a person set or confirmed; null '
                    'when unknown. Evidence retained from older versions may omit `confidence`, `provider` and '
                    '`model`.')
    model: str | None = Field(default=None, description='Model that produced it, or null.')
    profile_description: str | None = Field(default=None, description='`profile_evidence` only: the description proposed with this evidence.')
    profile_direction: str | None = Field(default=None, description='`profile_evidence` only: the direction proposed with this evidence.')
    step: Literal['discovery', 'profiles', 'directing'] | None = Field(
        default=None, description='Analysis step whose accepted version supplied this row; absent or null for '
                                  'mentions and for rows written by the removed Classic engine.')
    version_id: str | None = Field(
        default=None, description='Accepted step-output artifact the row was projected from; absent or null when '
                                  'there is none (mentions, manual attributions, older rows).')
    origin: str | None = Field(
        default=None, description='How the source result came to be: `run`, `baseline` or `external` (from the '
                                  'step version), `manual` (a hand-edited attribution), `book` (dialogue with no '
                                  'accepted directing version), `cast_names` (mentions); null when unknown.')
    projection: int | None = Field(
        default=None, description='Version of the evidence projection that wrote the row (1). Absent on rows '
                                  'written by the removed Classic engine.')
    anchors: int | None = Field(
        default=None, description='Profiles evidence only: how many exact locations the quotation matched within '
                                  'the discovery evidence it came from. Every location is listed; none is chosen.')


# ------------------------------------------------------------------ operations

_PRONUNCIATION_EFFECTS = (
    "Changing pronunciations requires an idle book. A render recipe records only the entries a passage used, so a "
    "change alters the audio identity of the passages containing that word and no others. Studio takes that no "
    "longer match are unselected (`retired_takes`); their WAVs stay archived and are reused without a request if "
    "the recipe returns. Simple listening, including simple saved performances, always uses the current entries: "
    "affected passages and chunks become uncached and are narrated again on demand. A cast performance keeps the "
    "entries it was created with (`pronunciation_count`); its plan notes when the book's entries have changed since, "
    "or that a performance made before pronunciations existed does not use them. Voice examples apply them too; "
    "`POST /api/books/{book_id}/voice-preview` can audition an unsaved respelling.\n\n"
    "Limits: a multi-word term split across two passages is respelled in chapter chunks (one request spans both) "
    "but not in single-passage takes. Provider sentence timing (Breeze) stays in sent-text offsets; nothing maps it "
    "back to source offsets for clients yet."
)



OPS: list[Op] = [
    op('GET', '/api/books/{book_id}', 'getBook', 'Books', 'Get the full book document',
       'Returns the full reader projection: chapters with canonical text, scenes, passages with source offsets, '
       'the cast with voice choices, `revision`, and the analysis summary. Valid enhanced audio has a playback '
       'URL; an unavailable or stale selected take is presented as `null`. Server bookkeeping (edit locks, '
       'metadata locks, cache keys, single-provider voice fields of older versions) is not included. Read-only. '
       'Works for archived books. A book whose stored data is inconsistent (for example a passage naming a '
       'missing chapter) is a server defect (500 `internal_error`), never 404.',
       response=Book, errors=_BOOK_ERRORS, params={'book_id': 'Book ID.'}),

    op('POST', '/api/books/{book_id}/repair-structure', 'repairBookStructure', 'Books',
       'Refresh structure metadata from the saved original',
       'Re-parses the saved original EPUB or TXT and replaces only chapter structure metadata (`title`, `kind`, '
       '`title_source`, `source_href`, `logical_sections`, `narrative_order`) and `structure_version`. IDs, '
       'text, offsets, passages, cast and annotations are kept. Automatic scene titles that began with the old '
       'chapter title are renamed; scene titles edited by hand are not. Increments `revision`.\n\n'
       'Refused, with existing work preserved, unless the re-parsed original has the same number of chapters with '
       'exactly the same text (400 `structure_mismatch`). Requires a known (404), non-archived and idle (409) book; '
       'these preconditions are checked first and a refused precondition records nothing. Runs locally with no '
       'provider request; every attempt that passes them, including one refused with 400, records a local '
       '`structure_repair` resource measurement. Returns the full, presented book.\n\n'
       "Before it changes the book, the current projection is recorded in the step pipeline's version history (as `baseline` or `external` versions of the capturable steps) when the history does not already explain it, so the replaced state stays restorable.",
       response=Book, params={'book_id': 'Book ID.'},
       errors={400: {'original_missing': 'The book has no saved original EPUB or TXT, or the saved file is missing.',
                     'original_too_large': 'The saved original is larger than 30 MiB (the import limit).',
                     'original_unreadable': 'The saved original could not be parsed (for example an unreadable EPUB).',
                     'structure_mismatch': 'The re-parsed source does not match the saved chapters.'},
               404: _BOOK_NOT_FOUND, 409: _BUSY}),

    op('PATCH', '/api/books/{book_id}/characters/{character_id}', 'editCharacter', 'Books', 'Edit a character',
       'Updates any of `name`, `aliases`, `description`, `direction` and `voices` (body `CharacterEdit`). '
       'Renaming records the previous name in `former_names`, so later discovery still resolves it to this '
       'character. `voices` changes only the providers it names; see `CharacterEdit.voices` and `VoiceChoice` '
       'for the forms. For every provider, `null` or a blank `id` clears the choice, so Default applies. '
       'Choosing a Breeze voice by `id` pins it to the revision from the last Breeze check, from saved state '
       'only (no server request). Changing a voice or direction deselects that character\'s now-stale takes.\n\n'
       + _EDIT_LOCKS + _EDIT_COMMON,
       response=Book, params={'book_id': 'Book ID.', 'character_id': 'Book-local character ID.'},
       errors={400: _VOICE_ERRORS,
               404: {**_BOOK_NOT_FOUND, 'character_not_found': 'No character in this book has this ID.'},
               409: _BUSY}),

    op('POST', '/api/books/{book_id}/characters', 'addCharacter', 'Books', 'Add a character',
       'Adds a human-reviewed cast member with a new ID (`character-` + 12 hex). The body is the same '
       '`CharacterEdit` as editing, but `name` is required. Omitted fields start empty. Voices start as '
       '`{"gemini": {"id": "Kore"}}` plus a device (`system`) voice chosen from the installed voices the same way '
       'imported characters get one (none when no suitable voice is installed), then any `voices` sent are '
       'applied; sending `system: null` keeps the device choice at Default. Only the fields sent (always '
       'including `name`) are locked against generated analysis, so generated profile text may still fill the '
       'rest. Increments `revision`. Nothing is re-attributed; assign passages with the passage edit. Requires a '
       'non-archived, idle book (409). Returns the full, presented book.\n\n'
       "Before it changes the book, the current projection is recorded in the step pipeline's version history (as "
       '`baseline` or `external` versions of the capturable steps) when the history does not already explain it, '
       'so the replaced state stays restorable.',
       response=Book, params={'book_id': 'Book ID.'},
       errors={400: {'character_name_required': '`name` is missing.', **_VOICE_ERRORS},
               404: _BOOK_NOT_FOUND, 409: _BUSY}),

    op('PATCH', '/api/books/{book_id}/segments/{segment_id}', 'editPassage', 'Books', 'Edit a passage',
       'Updates any of `speaker_id`, `direction`, `cues` and `seed` of one passage (body `SegmentEdit`). Sending '
       '`speaker_id` sets `confidence` to 1.0; changing it also drops the passage\'s `speaker_check`. The text '
       'and offsets never change. A new `seed` makes seeded providers (Breeze) produce a new take, and `null` '
       'clears it so the speaker\'s voice seed applies; like other performance edits this deselects the current '
       'take while retaining its history.\n\n' + _EDIT_LOCKS + _EDIT_COMMON,
       response=Book, params={'book_id': 'Book ID.', 'segment_id': 'Passage (segment) ID.'},
       errors={400: {'character_not_in_cast': '`speaker_id` is not a character in this book\'s cast.'},
               404: {**_BOOK_NOT_FOUND, 'passage_not_found': 'No passage in this book has this ID.'},
               409: _BUSY}),

    op('PATCH', '/api/books/{book_id}/scenes/{scene_id}', 'editScene', 'Books', 'Edit a scene',
       'Updates any of `title`, `summary`, `tone` and `direction` of one scene (body `SceneEdit`). Scene tone and '
       'direction are part of every enhanced narration recipe of the scene\'s passages, so changing them '
       'deselects those takes. Scene boundaries cannot be edited here.\n\n' + _EDIT_LOCKS + _EDIT_COMMON,
       response=Book, params={'book_id': 'Book ID.', 'scene_id': 'Scene ID.'},
       errors={404: {**_BOOK_NOT_FOUND, 'scene_not_found': 'No scene in this book has this ID.'}, 409: _BUSY}),

    op('GET', '/api/books/{book_id}/characters/{character_id}/references', 'listCharacterReferences', 'Books',
       'List source references to a character',
       'Returns every source reference to this current cast member, unpaginated, in reading order (chapter, then '
       'offset): dialogue passages currently attributed to it, mentions of its name or aliases, and evidence '
       'quotations, each with a source anchor. The references are the projection of the accepted analysis-pipeline '
       'versions (discovery, profiles, directing) onto the current book, computed on every call as the next pipeline '
       'write would record it, so manual edits and acceptance show at once. A name or alias that another cast member '
       'shares is not counted as a mention. Rows retained from the removed Classic engine are carried until a '
       'discovery version is accepted. A book with no accepted discovery, profiles or directing version (and none that '
       'the current book implies) lists its stored rows unchanged, which may be empty. `narrator` and `unassigned` '
       'have no references. Read-only; nothing is recorded. Works for archived books.',
       response=list[CharacterReference], params={'book_id': 'Book ID.', 'character_id': 'Book-local character ID.'},
       errors={404: {**_BOOK_NOT_FOUND, 'character_not_found': 'No character in the book\'s current cast has this ID.'}}),
    op('GET', '/api/books/{book_id}/pronunciations', 'listPronunciations', 'Pronunciations', "List the book's pronunciations",
       "Every entry with its use in the book: whole-word matches in chapter text, the passages containing it, how many "
       "of those have a current Studio take, and up to three examples. Nothing is generated or written; the usage is "
       "computed from the current text on each call.",
       response=PronunciationList, params={'book_id': 'Book ID.'},
       errors=_BOOK_ERRORS),
    op('POST', '/api/books/{book_id}/pronunciations', 'addPronunciation', 'Pronunciations', 'Add a pronunciation',
       _PRONUNCIATION_EFFECTS + "\n\nAdds one entry; only `term` and `respelling` are required, and the server assigns "
       "`id` (an `id` in the body is ignored). At most 500 entries per book. Requires a non-archived, idle book (409).",
       response=PronunciationSaved, params={'book_id': 'Book ID.'},
       errors={400: _PRONUNCIATION_INVALID, 404: _BOOK_NOT_FOUND, 409: _BUSY}),
    op('PATCH', '/api/books/{book_id}/pronunciations/{entry_id}', 'updatePronunciation', 'Pronunciations',
       'Change a pronunciation',
       _PRONUNCIATION_EFFECTS + "\n\nA partial update (body `PronunciationPatch`): every field is optional, fields "
       "left out keep their saved values, and `null` (or `{}` for `providers`) clears one. The merged entry must "
       "still have a `term` and a `respelling` (so `null` for either is 400), and is validated like a new one. A "
       "change that leaves the entry as it was saves nothing and does not change the book `revision`. Requires a "
       "non-archived, idle book (409).",
       response=PronunciationSaved, params={'book_id': 'Book ID.', 'entry_id': 'Pronunciation entry ID (`pr_…`).'},
       errors={400: _PRONUNCIATION_INVALID, 404: {**_BOOK_NOT_FOUND, **_PRONUNCIATION_NOT_FOUND}, 409: _BUSY}),
    op('DELETE', '/api/books/{book_id}/pronunciations/{entry_id}', 'deletePronunciation', 'Pronunciations',
       'Remove a pronunciation',
       _PRONUNCIATION_EFFECTS + "\n\nRemoves the entry. Removing the last one removes the book's `pronunciations` "
       "field. Requires a non-archived, idle book (409).",
       response=PronunciationSaved, params={'book_id': 'Book ID.', 'entry_id': 'Pronunciation entry ID (`pr_…`).'},
       errors={404: {**_BOOK_NOT_FOUND, **_PRONUNCIATION_NOT_FOUND}, 409: _BUSY}),
]

REQUEST_DOCS: dict[str, dict[str, str]] = {
    'PronunciationEntry': {
        '__doc__': 'A pronunciation entry. For adding, `term` and `respelling` are required. Also used, with the `id` '
                   'of the entry it edits, to audition an unsaved respelling in a voice example. Changing a saved '
                   'entry uses `PronunciationPatch`.',
        'id': 'Ignored when adding (the server assigns one). In a voice example, the entry this unsaved version '
              'replaces; omit it for a new word.',
        'term': 'The word or phrase as written: at most 80 characters after collapsing whitespace, with at least one '
                'letter or digit (the request accepts up to 200 before normalization).',
        'respelling': 'How to say it: at most 120 characters after collapsing whitespace. Control characters, brackets, '
                      'parentheses, braces and backslashes are refused.',
        'providers': 'Per-narrator overrides keyed by `system`, `gemini` or `breeze`. An empty or null value drops '
                     'that override; an override equal to the term leaves that narrator reading the word unchanged.',
        'match_case': 'True (default): match exact case. False: match any case.',
        'character_id': 'Optional book-local character the word belongs to; must be in the current cast.',
        'note': 'Optional free-text note, at most 500 characters.',
    },
    'PronunciationPatch': {
        '__doc__': 'Changes to a saved pronunciation. Every field is optional: a field left out keeps its saved value, '
                   'and `null` clears it. The path names the entry, so there is no `id`.',
        'term': 'New word or phrase, with the same rules as when adding. `null` is refused: an entry needs a term.',
        'respelling': 'New respelling, with the same rules as when adding. `null` is refused: an entry needs one.',
        'providers': 'Replacement per-narrator overrides keyed by `system`, `gemini` or `breeze`; `{}` or `null` removes '
                     'them all, and an empty or null value drops that override.',
        'match_case': 'True: match exact case. False: match any case. `null` restores the default (true).',
        'character_id': 'Book-local character the word belongs to; must be in the current cast. `null` removes the link.',
        'note': 'Free-text note, at most 500 characters. `null` or an empty string removes it.',
    },
    'CharacterEdit': {
        '__doc__': 'Character fields to change (edit) or set (create). Omitted or null fields are ignored; send "" '
                   'or [] to clear. Creation requires a nonempty `name` even though this DTO marks it optional.',
        'name': 'Display name, 1–100 characters. A changed name is remembered in `former_names`.',
        'aliases': 'Complete replacement list of other names for the character.',
        'description': 'Voice and personality profile, at most 3,000 characters.',
        'voices': 'Per-provider voice choices: `{provider: VoiceChoice | null}` where provider is `system`, `gemini` '
                  'or `breeze` (another key is 400 `voice_provider_unknown`). Only the providers present change; '
                  '`null`, or a choice whose `id` is empty or blank, removes that provider\'s choice, which means '
                  'Default. This rule is the same for every provider. The stored map is returned in '
                  '`characters[].voices` (library references stay references).',
        'direction': 'Standing performance direction, at most 3,000 characters.',
    },
    'VoiceChoice': {
        '__doc__': 'One provider\'s voice choice. Exactly one of `id` or `library` (otherwise 422).',
        'id': 'A direct provider voice ID, at most 200 characters, trimmed of surrounding spaces. Empty or blank '
              '(after trimming) clears the choice so Default applies, for every provider; it never selects a '
              'provider\'s own default voice. For Breeze a nonblank ID must be a usable (cloned) voice in the last '
              'Breeze check (400 `breeze_voice_unavailable`) and is stored pinned as `{id, revision, seed}`; for '
              'Gemini and device voices it is stored as `{id}`.',
        'library': 'A library voice ID (`vl_` + 16 lowercase hex) of the same provider, not deleted (400 '
                   '`library_voice_unavailable`). Stored as `{library}`: the character follows that voice\'s '
                   'current version.',
        'seed': 'Take seed 0–4294967295 for a Breeze pin; only with a nonblank Breeze `id`. Defaults to the voice\'s '
                'own seed, else 42. With `library`, a blank `id` or another provider it is 400 '
                '`seed_not_applicable` (it is never silently ignored).',
    },
    'SegmentEdit': {
        '__doc__': 'Passage fields to change. Omitted or null fields are ignored, except `seed`; send "" or [] to clear.',
        'speaker_id': 'Character ID from this book\'s cast (including `narrator` or `unassigned`); anything else is '
                      '400 `character_not_in_cast`. Sets `confidence` to 1.0.',
        'direction': 'Performance direction for the passage, at most 3,000 characters.',
        'cues': 'Complete replacement list of cue labels.',
        'seed': 'Take seed 0–4294967295 for seeded providers such as Breeze; a new seed is a new take. `null` '
                'clears it, so the speaker\'s Breeze voice seed applies; omitting it keeps the saved seed. Gemini '
                'and device narration ignore it.',
    },
    'SceneEdit': {
        '__doc__': 'Scene fields to change. Omitted or null fields are ignored; send "" to clear.',
        'title': 'Display title, 1–200 characters.',
        'summary': 'Summary, at most 4,000 characters.',
        'tone': 'Emotional tone notes, at most 1,000 characters.',
        'direction': 'Performance direction for the scene, at most 3,000 characters.',
    },
}
