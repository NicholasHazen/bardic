"""Audio, listening-session and chapter-plan views shared by jobs and the listening routes.

These shapes appear both inside :class:`~bardic.apispec.common.Job` and in
listening, performance and voice-preview responses, so they live below
``common`` in the import order: base -> media -> common -> route families.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import Discriminator, Field, Tag

from .base import View

Provider = Literal['system', 'gemini', 'breeze']


class AudioRef(View):
    """The common core of every playable audio object in a response.

    Every object that carries an audio ``url`` has these seven fields, always
    present, with these names and meanings; a kind-specific view may narrow a
    field to non-null when that kind always knows it. Kind-specific fields
    (clip bounds, reuse pointers, provider timing, ...) are added by the
    subclass. Audio objects carry no constant labels: an object is playable
    because it is present, and its kind is given by where it appears. Build
    them with ``bardic.audio_refs.audio_ref`` so the core stays identical.
    """
    url: str = Field(description='Root-relative URL of the audio bytes (WAV unless stated otherwise). Play this; do not '
                                 'build audio URLs from other fields.')
    asset_id: str | None = Field(description='SHA-256 hex of the file the URL serves (content address), or null when the '
                                             'bytes are not content-addressed (takes recorded before content addressing). '
                                             'A different `asset_id` means different audio.')
    duration: float | None = Field(description='Length of this audio in seconds, or null when unknown.')
    provider: str | None = Field(description='Speech provider that produced the bytes (`system`, `gemini`, `breeze`), or null when unknown.')
    model: str | None = Field(description='Speech model that produced the bytes, or null when unknown.')
    voice: str | None = Field(description='Provider voice actually used, or null when unknown.')
    created_at: str | None = Field(description='ISO 8601 UTC time the audio was retained, or null when it was not recorded.')

class AudioTakeSentenceSpan(View):
    """One provider-reported sentence inside a take."""
    char_start: int = Field(description='Start code-point offset into the text that was sent (not chapter coordinates).')
    char_end: int = Field(description='Exclusive end code-point offset into the text that was sent.')
    start: float = Field(description='Start time in the take, seconds.')
    end: float = Field(description='End time in the take, seconds.')

class AudioTakeSentenceTiming(View):
    """Sentence timing reported by the Breeze server, accepted only when every offset matched the sent text."""
    schema_version: int = Field(description='Timing format version (1).')
    kind: Literal['sentence'] = Field(description='Timing granularity.')
    source: Literal['breeze'] = Field(description='Who measured the timing.')
    offsets: Literal['recipe_text_code_points'] = Field(description='What the character offsets index into.')
    segments: list[AudioTakeSentenceSpan] = Field(description='Sentences in order.')

class AudioTakeBreezeInfo(View):
    """Breeze request details retained with a take."""
    request_id: str | None = Field(None, description="The server's `x-request-id`, truncated to 80 characters; null or absent when the server sent none.")
    timing_accepted: bool = Field(description='Whether the server-reported sentence timing validated against the sent text.')
    vocal_event_markup: list[str] | None = Field(None, description='Lower-cased vocal event tags (for example `[laugh]`) found in the sent text; absent when none.')

class AudioTakeVoiceLibrary(View):
    """The voice-library voice and version that performed a take."""
    id: str = Field(description='Voice library ID (`vl_…`).')
    version: int | None = Field(description='Library voice version number.')

class ListeningReuse(View):
    """Pointer to the original retained take whose bytes were reused for this passage."""
    schema_version: int = Field(description='Pointer format version (1).')
    take_id: str = Field(description='ID of the original retained take row.')
    book_id: str = Field(description='Book of the original take (reuse can cross books).')
    session_id: str = Field(description='Listening session ID (64 hex) of the original take; may differ from the current session.')
    segment_id: str = Field(description='Passage ID the original take narrated, in the original take\'s book; may differ from this passage when equivalent text was reused.')

class ListeningSubstitute(View):
    """Marks a take that stands in for a passage Gemini's content policy blocked."""
    reason: Literal['content_blocked'] = Field(description='Why Gemini did not narrate the passage: its content policy blocked the text.')
    for_provider: str = Field(description='The provider of the session this audio stands in for (`gemini`).')
    for_model: str = Field(description='The speech model of the session this audio stands in for.')

class ListeningPassageAudio(AudioRef):
    """A retained single-passage simple-listening take, ready to play.

    Like every audio object, it has the common audio core, always present: `url`, `asset_id`, `duration`, `provider`, `model`, `voice` and `created_at`.
    """
    url: str = Field(description='Root-relative WAV URL: `/api/books/{book_id}/listen/audio/{asset_id}`.')
    asset_id: str = Field(description='SHA-256 hex of the WAV bytes (content address).')
    duration: float = Field(description='Audio length in seconds.')
    provider: str = Field(description='Provider that produced the bytes (`system`, `gemini` or `breeze`).')
    model: str = Field(description='Speech model that produced the bytes.')
    voice: str = Field(description='Provider voice actually used (the device voice name after resolution).')
    created_at: str = Field(description='ISO 8601 UTC time the take was retained (the first retention if it was saved concurrently).')
    session_id: str = Field(description='Listening session the take belongs to. For a `substitute`, the fallback narrator\'s session, not the Gemini session it stands in for.')
    segment_id: str = Field(description='Passage the take narrates.')
    substitute: ListeningSubstitute | None = Field(None, description='Present when a fallback narrator (`provider` is not `gemini`) read this passage because Gemini blocked its text. It is a normal immutable take of the fallback narrator, never Gemini audio. Absent otherwise.')
    reuse: ListeningReuse | None = Field(None, description='Present when the bytes were copied from an equivalent retained take instead of being generated.')
    provider_timing: AudioTakeSentenceTiming | None = Field(None, description='Breeze only: validated sentence timing, or null when the server timing did not validate.')
    breeze: AudioTakeBreezeInfo | None = Field(None, description='Breeze only: request details.')
    voice_revision: str | None = Field(None, description='Breeze only: voice revision that performed the take.')

class ListeningChunkClipAudio(AudioRef):
    """One passage's estimated clip inside a multi-passage chunk WAV (Gemini chapter listening).

    Play ``url`` from ``clip_start`` to ``clip_end``. Consecutive clips of one
    chunk share the same file and play gaplessly. Like every audio object, it has the common audio core, always present: `url`, `asset_id`, `duration`, `provider`, `model`, `voice` and `created_at`.
    """
    url: str = Field(description='Root-relative URL of the shared chunk WAV: `/api/books/{book_id}/listen/audio/{asset_id}`.')
    asset_id: str = Field(description='SHA-256 hex of the chunk WAV.')
    duration: float = Field(description='Clip length in seconds (`clip_end - clip_start`, rounded to ms).')
    provider: str = Field(description='Speech provider that produced the chunk, for example `gemini`.')
    model: str = Field(description='Speech model that produced the chunk, for example `gemini-3.8-flash-tts`.')
    voice: str = Field(description='Provider voice actually used for the chunk.')
    created_at: str = Field(description='ISO 8601 UTC time the chunk was retained.')
    segment_id: str = Field(description='Passage this clip narrates.')
    chunk_id: str = Field(description='ID of the retained chunk.')
    clip_start: float = Field(description='Clip start within the chunk WAV, seconds.')
    clip_end: float = Field(description='Clip end within the chunk WAV, seconds.')
    chunk_duration: float = Field(description='Length of the whole chunk WAV in seconds.')
    timing: Literal['estimated'] = Field(description='Clip boundaries are estimated from pauses, not measured.')
    session_id: str = Field(description='Listening session ID (64 hex) the chunk belongs to.')
    flags: list[str] = Field(description='Quality flags of the chunk; currently `weak_alignment` (fewer than 60% of passage boundaries matched a pause).')

def _simple_audio_kind(value: Any) -> str:
    return 'clip' if isinstance(value, dict) and 'chunk_id' in value else 'passage'

ListeningAudio = Annotated[Union[Annotated[ListeningPassageAudio, Tag('passage')],
                                 Annotated[ListeningChunkClipAudio, Tag('clip')]],
                           Discriminator(_simple_audio_kind)]

class ChapterListenChunkPlan(View):
    """One planned chunk request, in request order."""
    first_segment_id: str = Field(description='Passage ID of the first passage in the chunk (chapter reading order).')
    last_segment_id: str = Field(description='Passage ID of the last passage in the chunk, inclusive; equals `first_segment_id` for a one-passage chunk.')
    segment_count: int = Field(description='Consecutive passages in the chunk.')
    chars: int = Field(description='Code points of the exact chapter slice sent.')
    target_seconds: float = Field(description='Audio length this step aimed for, seconds.')
    expected_seconds: float = Field(description='Audio length expected from the calibrated speech rate, seconds.')

class ChapterListenChunking(View):
    """Effective chunk options after merging the request over the saved `listen_chunking` preference."""
    ramp_seconds: list[float] = Field(description='Target lengths for the first requests, seconds; empty means every request is full size.')
    target_seconds: float = Field(description='Full-size chunk target, seconds (30-470).')
    concurrency: int = Field(description='Requests kept in flight at once (1-3).')

class ChapterListenCalibrationSample(View):
    """A finished chunk measurement."""
    chars: int = Field(description='Code points of chapter text sent in the measured chunk.')
    duration: float = Field(description='Audio seconds.')
    latency: float = Field(description='Request seconds; 0 when unknown.')

class ChapterListenCalibration(View):
    """Speech rate and speed learned from this narrator session's finished chunks (carried across jobs)."""
    samples: list[ChapterListenCalibrationSample] = Field(description='Up to 12 most recent measurements.')
    truncated_chars_per_second: float | None = Field(description='Rate ceiling learned from a truncated response, or null.')
    max_chars: int | None = Field(description='Absolute chunk size ceiling learned from truncation, or null.')
    chars_per_second: float = Field(description='Expected code points per audio second (prior 14).')
    chars_per_second_low: float = Field(description='Conservative rate used to stay under the provider audio cap.')
    realtime_factor: float = Field(description='Audio seconds produced per wall-clock second (prior 2).')

class ChapterListenLimits(View):
    """Configured Gemini speech limits for the model (Settings `tts_limits`)."""
    rpm: int = Field(description='Requests per minute.')
    tpm: int = Field(description='Input tokens per minute.')
    rpd: int = Field(description='Requests per day (Pacific quota day).')

class ChapterListenQuota(View):
    """This library's daily request count for the model. A lower bound: other apps sharing the project are not seen."""
    requests_today: int = Field(description='Gemini speech requests recorded by this library since midnight Pacific.')
    rpd: int = Field(description='Configured requests per day.')
    resets_at: str = Field(description='ISO 8601 UTC time of the next midnight Pacific reset.')
    scope: Literal['this library'] = Field(description='What `requests_today` counts.')

class VoicePreviewSourceAnchor(View):
    """Exact source prefix used as audition text."""
    schema_version: int = Field(description='Anchor format version (1).')
    book_id: str = Field(description='Book ID the sampled passage belongs to.')
    chapter_id: str = Field(description='Chapter ID whose text `start` and `end` index.')
    segment_id: str = Field(description='Passage (segment) ID the sample was taken from.')
    start: int = Field(description='Chapter-local code-point start of the passage.')
    end: int = Field(description='Exclusive chapter-local code-point end of the sample (start + sample length).')
    text_sha256: str = Field(description='SHA-256 hex of the sample text.')

class VoicePreviewPronunciation(View):
    """The unsaved pronunciation a voice example auditioned."""
    term: str = Field(description='The word as written in the book.')
    spoken: str = Field(description='What this example\'s narrator was asked to say: the provider override if any, else the respelling.')


class VoicePreview(View):
    """An immutable audition request."""
    id: str = Field(description='Preview ID: a hash of the full audition recipe.')
    schema_version: int = Field(description='Preview request format version (1).')
    book_id: str = Field(description='Book ID the audition belongs to.')
    text: str = Field(description='Exact source text sampled, at most 400 code points: a passage prefix; for a pronunciation audition, the sentence around the word; or a fixed demo or carrier sentence. Pronunciations are not applied here (see `spoken_text`).')
    source: Literal['passage', 'demo'] = Field(description='`passage` when the text comes from the book; `demo` when no passage was chosen or found and a fixed demo or pronunciation carrier sentence is used.')
    segment_id: str | None = Field(description='Passage ID sampled: the requested passage, or else the character\'s first attributed passage. Null for demo text.')
    chapter_id: str | None = Field(description='Chapter ID of the sampled passage, or null for demo text.')
    character_id: str | None = Field(description='Book-local character ID whose voice and direction were auditioned, or null for a narrator audition.')
    character_name: str | None = Field(description='Character name at request time, or null.')
    source_anchor: VoicePreviewSourceAnchor | None = Field(description='Null for demo text.')
    truncated: bool = Field(description='True when the passage was shortened to the sample.')
    spoken_text: str | None = Field(None, description='The text actually sent to the narrator when a pronunciation changed it; absent otherwise.')
    pronunciation: VoicePreviewPronunciation | None = Field(None, description='The unsaved pronunciation this audition tried; absent otherwise.')
    provider: Provider = Field(description='Speech provider: `system` (macOS device voice), `gemini` or `breeze` (self-hosted).')
    model: str = Field(description='Speech model: `macos-say` for system, `breeze-tts-2` for Breeze, or the Gemini model (default `gemini-3.8-flash-tts`).')
    voice: str = Field(description='Resolved provider voice (Gemini defaults to `Kore`; device default is empty).')

class VoicePreviewReuse(View):
    """Pointer to an equivalent earlier audition take whose bytes were reused."""
    schema_version: int = Field(description='Pointer format version (1).')
    take_id: str = Field(description='ID of the original retained audition take whose bytes were reused (64 hex).')
    preview_id: str = Field(description='Preview ID of the original audition request that produced the reused bytes.')

class VoicePreviewAudio(AudioRef):
    """A retained audition take.

    Like every audio object, it has the common audio core, always present: `url`, `asset_id`, `duration`, `provider`, `model`, `voice` and `created_at`.
    """
    url: str = Field(description='Root-relative WAV URL: `/api/books/{book_id}/voice-preview/audio/{asset_id}`.')
    asset_id: str = Field(description='SHA-256 hex of the WAV bytes.')
    duration: float = Field(description='Seconds.')
    provider: str = Field(description='Provider that produced the bytes: `system`, `gemini` or `breeze`.')
    model: str = Field(description='Speech model that produced the bytes.')
    voice: str = Field(description='Provider voice actually used.')
    created_at: str = Field(description='ISO 8601 UTC time the take was retained.')
    preview_id: str = Field(description='ID of the audition request (`VoicePreview.id`) this take was retained for.')
    reuse: VoicePreviewReuse | None = Field(None, description='Present when bytes were reused from an equivalent audition (for example after a character rename).')
    provider_timing: AudioTakeSentenceTiming | None = Field(None, description='Breeze only: validated sentence timing, or null.')
    breeze: AudioTakeBreezeInfo | None = Field(None, description='Breeze only: request details.')
    voice_revision: str | None = Field(None, description='Breeze only: voice revision used.')
    voice_library: AudioTakeVoiceLibrary | None = Field(None, description='When the voice was a voice-library voice: which one and which version.')
