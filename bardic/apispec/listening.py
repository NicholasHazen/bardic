"""Contract entries for the listening route family.

Enhanced (cast) narration, simple single-narrator listening (passages and
Gemini chapters), saved performances and voice previews.

Every audio object carrying a playback ``url`` subclasses ``AudioRef``
(``media.py``) and is built by ``bardic.audio_refs.audio_ref``:

- ``ListeningPassageAudio``: a retained single-passage simple take
  (``url`` under ``/listen/audio/``).
- ``ListeningChunkClipAudio``: one passage's estimated clip inside a shared
  chunk WAV (``chunk_id``, ``clip_start``, ``clip_end``).
- ``PerformanceCastAudio``: a cast performance take (``url`` under
  ``/audio-assets/``). Simple performances list the two listening shapes.
- ``VoicePreviewAudio``: an audition take (``url`` under
  ``/voice-preview/audio/``).

The enhanced Studio take on a book passage (``/api/audio/...``) belongs to the
book document (``BookTake`` in the Books family).
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import Discriminator, Field, Tag

from .base import Op, View, op
from .common import Job
from .media import (AudioRef, Provider, ListeningPassageAudio, ListeningChunkClipAudio, ListeningAudio, ChapterListenChunkPlan,
                    ChapterListenChunking, ChapterListenCalibration, ChapterListenLimits, ChapterListenQuota, VoicePreview,
                    VoicePreviewAudio)


_BOOK_ID = 'Book ID.'

# Error codes shared by the operations below (see bardic/errors.py). Each operation lists exactly the codes it returns.
_BOOK_404 = {'book_not_found': 'No book has this ID.'}
_ARCHIVED = {'book_archived': 'The book is archived: restore it first.'}
_BUSY = {'job_active': 'A job is queued or running for this book.',
         'series_run_active': 'An active series run reserves this book.'}
_STOPPING = {'shutting_down': 'The server is shutting down, or its narration worker refused the job (that job record is '
                              'kept and marked `failed`). Nothing was sent to a provider.'}
_NARRATOR = {'narrator_voice_invalid': 'The narrator voice cannot be used: a `library:` voice for device narration, a '
                                       'deleted or wrong-provider library voice, no default Breeze voice, a Breeze voice '
                                       'not in the last voice check or not usable, or a custom voice with a model that '
                                       'needs a prebuilt voice (Gemini 3.1).',
             'model_unsupported': 'The model does not match the provider: device narration uses `macos-say`, Breeze '
                                  'uses `breeze-tts-2`, and Gemini needs a supported TTS model.'}
_PROVIDER = {'gemini_key_missing': 'Gemini narration with no Gemini API key configured.',
             'device_narration_unavailable': 'Device narration on a server without macOS `say` and `ffmpeg`.',
             'breeze_url_missing': 'Breeze narration with no Breeze server URL configured.'}
_UNKNOWN_PASSAGE = {'unknown_passage': 'The body names a passage (`segment_id`) that is not in this book.'}
_SOURCE = {'passage_source_mismatch': 'The passage text no longer matches its source coordinates.'}
_PERFORMANCE_404 = {'book_not_found': 'No book has this ID.', 'performance_not_found': 'The book has no performance with this ID.'}
_PROBLEMS = {**_PROVIDER, 'narrator_voice_invalid': 'The simple narrator voice cannot be used (see `previewPerformance` problems).',
             'narrator_voice_missing': 'A cast performance whose narrator has no usable voice for the provider.'}


# ------------------------------------------------------------ shared take pieces


# ------------------------------------------------------------ simple listening

class ListeningSession(View):
    """A simple-listening narrator choice for one book.

    The ID is a deterministic hash of the fields below, so the same narrator
    always maps to the same session and its retained takes. A Breeze voice
    changed on the server (new revision) starts a new session and keeps old takes.
    """
    id: str = Field(description='Session ID: a 64-character hex hash of the narrator configuration.')
    schema_version: int = Field(description='Session format version (1).')
    book_id: str = Field(description='Book the session belongs to.')
    provider: Provider = Field(description='Narration provider.')
    voice: str = Field(description='Resolved provider voice ID. Empty string for the device default voice; '
                                   'Gemini defaults to `Kore`; a `library:` choice is stored as the provider voice it resolved to.')
    model: str = Field(description='Speech model: `macos-say`, `breeze-tts-2`, or a Gemini TTS model.')
    voice_revision: str | None = Field(None, description='Breeze only: the pinned voice revision from the last voice check.')
    seed: int | None = Field(None, description='Breeze only: the pinned generation seed.')
    settings: dict[str, Any] | None = Field(None, description='Breeze only, when set: pinned speech settings for the voice (provider-defined keys).')


class ListeningTake(View):
    """The playable simple audio for one passage."""
    segment_id: str = Field(description='Passage (segment) ID within the book that this audio narrates.')
    audio: ListeningAudio = Field(description='A chunk clip when one applies (preferred), otherwise the newest valid single-passage take.')


class ListeningTakes(View):
    """Saved simple audio for a session."""
    session: ListeningSession
    takes: list[ListeningTake] = Field(description='At most one entry per passage, in book passage order. Passages without matching audio are omitted.')


class ListenCached(View):
    """A passage served from retained audio; nothing was queued."""
    session: ListeningSession
    audio: ListeningAudio = Field(description='The retained audio for the passage: a Gemini chapter chunk clip for this session when one '
                                           'covers it, otherwise a single-passage take (possibly reused from an identical recipe '
                                           'elsewhere). Play its `url`.')
    cached: Literal[True] = Field(description='Always true: served from retained audio. No job was queued and no provider was contacted.')


class ListenQueued(View):
    """A passage that needs synthesis: a new or joined `listen` job."""
    session: ListeningSession
    job: Job = Field(description='The `listen` job (new, or the already active one for this session and passage). '
                                 'Poll it; on completion its `audio` holds the take.')
    cached: Literal[False] = Field(description='Always false: no retained audio matched, so the passage needs synthesis via `job`.')


class ChapterListenPlan(View):
    """Local projection of a chapter job starting at a passage. Nothing is queued or sent."""
    session: ListeningSession
    chapter_id: str = Field(description='Chapter containing the passage.')
    chunks: list[ChapterListenChunkPlan] = Field(description='Remaining chunks in request order (listener position first, then earlier scope).')
    requests_needed: int = Field(description='Number of provider requests the plan needs (`len(chunks)`).')
    expected_seconds: float = Field(description='Expected audio seconds still to generate.')
    ready_seconds: float = Field(description='Seconds of audio already available from the passage to the chapter end.')
    passages_total: int = Field(description='Passages from the selected passage to the chapter end.')
    passages_ready: int = Field(description='Of those, passages that already have audio.')
    chunking: ChapterListenChunking
    calibration: ChapterListenCalibration
    limits: ChapterListenLimits
    quota: ChapterListenQuota


class ChapterListenStarted(View):
    """A started or joined `listen_chapter` job."""
    session: ListeningSession
    job: Job = Field(description='The `listen_chapter` job. Poll it for chunk progress; passage audio appears in `/listen/takes`.')
    joined: bool = Field(description='True when the request joined an already active job for the same session and chapter.')


# ------------------------------------------------------------ performances

class PerformanceCastAudio(AudioRef):
    """A cast performance's retained passage take.

    Like every audio object, it has the common audio core, always present: `url`, `asset_id`, `duration`, `provider`, `model`, `voice` and `created_at`.
    """
    url: str = Field(description='Root-relative WAV URL: `/api/books/{book_id}/audio-assets/{id}`.')
    asset_id: str | None = Field(description='SHA-256 hex of the WAV (content address), or null for a reused Studio take '
                                             'recorded before content addressing (its URL then names the file by recipe).')
    duration: float | None = Field(description='Audio length in seconds, or null when the retained record lacks it.')
    provider: str | None = Field(description='Narration provider that produced the take (`system`, `gemini` or `breeze`); '
                                             'null when the retained take metadata does not record it.')
    model: str | None = Field(description='Speech model that produced the take (for example `macos-say`, `breeze-tts-2` or a '
                                          'Gemini TTS model); null when the retained take metadata does not record it.')
    voice: str | None = Field(description='Provider voice that performed the take, or null when not recorded.')
    created_at: str | None = Field(description='ISO 8601 UTC time the take was retained for this performance.')
    speaker_id: str | None = Field(description="The passage's speaker (a character ID, `narrator` or `unassigned`).")
    character_id: str | None = Field(description='Character whose voice was used (`narrator` when falling back).')
    fallback: bool = Field(description='True when the speaker had no usable voice and the narrator voice was used.')


def _performance_audio_kind(value: Any) -> str:
    if isinstance(value, dict) and 'chunk_id' in value:
        return 'clip'
    return 'cast' if isinstance(value, dict) and 'speaker_id' in value else 'passage'


PerformanceAudio = Annotated[Union[Annotated[ListeningPassageAudio, Tag('passage')],
                                   Annotated[ListeningChunkClipAudio, Tag('clip')],
                                   Annotated[PerformanceCastAudio, Tag('cast')]],
                             Discriminator(_performance_audio_kind)]


class PerformanceCastMember(View):
    """Who voices a speaker in a cast performance."""
    character_id: str = Field(description='Speaker ID (`narrator`, `unassigned` or a book character ID).')
    name: str = Field(description='Character name from the snapshot, or the ID.')
    voice_label: str = Field(description='Display label of the voice used (library voice name, provider voice ID, `Kore` or `Default voice`).')
    fallback: bool = Field(description='True when the speaker has no usable voice and uses the narrator.')


class PerformanceChapterProgress(View):
    """Readiness of one selected chapter."""
    id: str = Field(description='Chapter ID.')
    title: str = Field(description='Chapter title; empty string when the chapter has none.')
    passages_total: int = Field(description='Passages in the chapter.')
    passages_ready: int = Field(description='Of those, passages with playable audio that matches their current source text. Includes passages a fallback narrator read.')
    passages_fallback: int = Field(description='Of the ready passages, those read by a fallback narrator because Gemini blocked their text (audio marked `substitute`).')
    passages_blocked: int = Field(description='Passages Gemini blocked that have no audio at all, so they are not ready. They are not requested from Gemini again; they are not a failure.')
    blocked_passage_ids: list[str] = Field(description='The passages counted in `passages_blocked`, in reading order.')


class PerformanceChaptersAdded(View):
    """One extension of a performance's chapter selection."""
    at: str = Field(description='ISO 8601 UTC time the chapters were added.')
    chapter_ids: list[str] = Field(description='The chapters newly added, in book order. Chapters already selected are not repeated.')


class PerformanceProgress(View):
    """Readiness against each passage's current source."""
    passages_total: int = Field(description='Passages in the selected chapters that are still in the book.')
    passages_ready: int = Field(description='Of those, passages with playable audio that matches their current source text.')
    seconds_ready: float = Field(description='Audio seconds ready.')
    passages_fallback: int = Field(description='Of the ready passages, those read by a fallback narrator because Gemini blocked their text. Nonzero means the performance is not entirely Gemini audio.')
    passages_blocked: int = Field(description='Passages Gemini blocked that have no audio at all (no fallback narrator was available, or it failed). Counted neither as ready nor as a failure; they are not requested again.')
    fallback_provider: Literal['system', 'breeze'] | None = Field(description='The provider that read the `passages_fallback` passages, or null when there are none.')
    chapters: list[PerformanceChapterProgress] = Field(description='Selected chapters still in the book, in book order.')


class Performance(View):
    """A saved performance: a named chapter selection plus a narrator (`simple`) or the cast (`cast`).

    This is the stored record without its internal `cast_snapshot` and `pronunciation_snapshot`, which responses
    never include.
    """
    id: str = Field(description='Performance ID (`pf_…`).')
    book_id: str = Field(description='ID of the book the performance belongs to.')
    schema_version: int = Field(description='Record format version (1).')
    name: str = Field(description='Display name, at most 200 characters. Defaults to the narrator label plus the scope, '
                                   'for example `Kore · Gemini · 3 chapters`.')
    mode: Literal['simple', 'cast'] = Field(description='`simple`: one narrator voice for every passage. `cast`: each speaker in their '
                                                        'cast voice, with the narrator as fallback.')
    chapter_ids: list[str] = Field(description='Selected chapters in book order: those chosen at creation plus any added later (see `chapters_added`). Chapters later removed from the book are skipped.')
    provider: Provider = Field(description='Narration provider pinned at creation.')
    model: str = Field(description='Speech model pinned at creation.')
    voice: str | None = Field(description='Simple: the voice value as requested (may be `library:…` or empty for Default). Cast: null.')
    pronunciation_count: int | None = Field(
        None, description='Cast performances only: how many book pronunciations were pinned when it was created. Absent '
                          'when none were (including performances made before pronunciations existed) and for simple '
                          'performances, which always use the book\'s current pronunciations.')
    session_id: str | None = Field(None, description='Simple only: the pinned listening session.')
    created_at: str = Field(description='ISO 8601 UTC.')
    updated_at: str = Field(description='ISO 8601 UTC; changes on rename, archive, when chapters are added and when a job starts.')
    archived: bool = Field(description='True when hidden from the default list (listed only with `archived=true`). Its audio is kept.')
    job_id: str | None = Field(description='Latest job ID, or null if no job was ever needed.')
    cast: list[PerformanceCastMember] | None = Field(None, description='Cast only: the narrator and each speaker in the chosen chapters.')
    job: Job | None = Field(description='The latest `performance` job (a full `Job`), or null when no job was ever needed.')
    progress: PerformanceProgress
    narrator_label: str = Field(description='Display label such as `Kore · Gemini` or `Full cast · Device voices`.')
    chapters_added: list[PerformanceChaptersAdded] | None = Field(
        None, description='Retained history of chapters added after creation with `addPerformanceChapters`, oldest first. '
                          'Absent when none were added. `chapter_ids` already includes them.')


class PerformanceList(View):
    """Performances of a book, newest first."""
    performances: list[Performance] = Field(description='Performances of the book, newest first. Archived ones only when requested with `archived=true`.')


class PerformanceEnvelope(View):
    """One performance."""
    performance: Performance


class PerformanceStarted(View):
    """A created or resumed performance."""
    performance: Performance
    job: Job | None = Field(description='The queued `performance` job, or null when every passage is already ready.')


class PerformanceAudioMap(View):
    """Playable audio of a performance."""
    performance_id: str = Field(description='ID of the performance (`pf_…`).')
    audio: dict[str, PerformanceAudio] = Field(description='Keyed by passage (segment) ID; only passages ready against their '
                                                          'current source. Simple performances give the `/listen/takes` '
                                                          'objects; cast performances give `PerformanceCastAudio`.')


class PerformanceQuota(View):
    """This library's daily Gemini request count for the model (a lower bound)."""
    requests_today: int = Field(description='Gemini speech requests for this model that this library recorded since the last midnight '
                                            'Pacific reset. Other apps or libraries sharing the API key are not counted, so the real '
                                            'provider usage may be higher.')
    rpd: int = Field(description='Configured requests per day.')
    resets_at: str = Field(description='ISO 8601 UTC time of the next midnight Pacific reset.')


class PerformanceProblem(View):
    """One blocking condition of a performance plan."""
    code: str = Field(description='Stable error code of the condition, the same code `createPerformance` refuses with '
                                  'when this is the first problem: `gemini_key_missing`, `breeze_url_missing`, '
                                  '`device_narration_unavailable`, `narrator_voice_invalid` (simple mode) or '
                                  '`narrator_voice_missing` (cast mode). Key a fix-it hint on it.')
    detail: str = Field(description='A sentence that states the condition, for people.')


class PerformancePlan(View):
    """Local estimate for a performance; nothing is recorded (except the deterministic session row) or sent."""
    mode: Literal['simple', 'cast'] = Field(description='The requested performance mode (see `Performance.mode`).')
    provider: Provider = Field(description='The requested narration provider.')
    model: str = Field(description='Resolved speech model.')
    chapter_ids: list[str] = Field(description='Requested chapters in book order.')
    passages_total: int = Field(description='Passages in the requested chapters.')
    passages_ready: int = Field(description='Passages with usable audio. A new cast performance always reports 0 even when Studio or other performance takes will be reused.')
    passages_to_generate: int = Field(description='Passages without usable audio that a job would narrate (`passages_total - passages_ready`).')
    requests_estimate: int = Field(description='Gemini simple: planned full-size chunk requests; otherwise passages to generate.')
    expected_seconds: float = Field(description='Missing text at 14 code points per second plus ready durations.')
    chapters: list[PerformanceChapterProgress] = Field(description='Readiness per requested chapter, in book order.')
    problems: list[PerformanceProblem] = Field(description='Blocking conditions, each with a stable `code` and a '
                                                           '`detail` sentence; empty when nothing blocks. Create '
                                                           'refuses (400, with the first problem\'s code) while any '
                                                           'exist.')
    notes: list[str] = Field(description='Advisory notes: voiceless characters, unassigned passages, unanalyzed chapters, reuse, daily request budget, and for a cast performance whether its pinned pronunciations differ from the book\'s current ones or predate them.')
    quota: PerformanceQuota | None = Field(description='Gemini only; null otherwise.')
    narrator_label: str = Field(description='Display label such as `Kore · Gemini` or `Full cast · Device voices`; also the '
                                            'prefix of the default name.')
    added_chapter_ids: list[str] | None = Field(
        None, description='`previewPerformanceResume` only: the requested chapters that are not yet part of the performance, '
                          'in book order. Absent from the plan `previewPerformance` returns.')


# ------------------------------------------------------------ voice previews


class VoicePreviewCached(View):
    """An audition served from retained audio."""
    preview: VoicePreview
    audio: VoicePreviewAudio
    cached: Literal[True] = Field(description='Always true: served from a retained audition take. No job was queued and no provider was contacted.')


class VoicePreviewQueued(View):
    """An audition that needs synthesis: a new or joined `voice_preview` job."""
    preview: VoicePreview
    job: Job = Field(description='The `voice_preview` job. On completion its `audio` holds the take.')
    cached: Literal[False] = Field(description='Always false: no retained take matched, so the audition needs synthesis via `job`.')


# ------------------------------------------------------------ operations

_LISTEN_DESCRIPTION = """\
Play one passage with one simple narrator (no cast, no direction). Simple listening has its own narrator
session, recipe archive and audio directory; it never changes cast voices, scene notes, enhanced selected
takes or enhanced artifacts.

`voice` is `"library:vl_…"` (that library voice's current version), a direct provider voice ID, or
empty/null for Default. For Breeze, Default is the Bardic default library voice (not the server's
default), a direct ID must be in the last Breeze voice check, and the session pins the voice revision
and seed. For Gemini, `library:` resolves to the voice's current `voice_…` ID. Omit `model` to use the
configured Gemini TTS preference; device and Breeze use `macos-say` and `breeze-tts-2`.

Order of checks, all under the store lock:

1. **Cache.** A cache hit returns `{session, audio, cached: true}` immediately, even if the provider key or
   device is no longer available and even while another job holds the book. Lookup first uses chunk
   clips from Gemini chapter listening for this session, then the exact source/session recipe, then
   equivalent speech inputs (exact text, voice, provider/model and versioned recipe) across retained
   passages and books. Cross-passage reuse validates the WAV and its content hash, copies the file into
   this book and retains a new source-bound take whose `reuse` points at the original take. A candidate
   whose copy fails its integrity check (for example this book already holds a damaged file under that
   asset ID, which is never overwritten) is skipped, so the passage can be generated instead. A cache hit
   records a cached resource operation (stage `simple_listen`); it may also add the take to the
   equivalent-speech lookup index, a derived cache.
2. **Join.** If a `listen` job for the same session and passage is queued or running without a cancel
   request, returns `{session, job, cached: false}` with that job instead of starting another synthesis.
3. **Queue.** Otherwise requires an idle book (409), a server that is not shutting down (503) and an
   available provider (400), then queues a one-unit `listen` job and returns `{session, job, cached: false}`.

Poll the job: when completed, its `audio` is the take (a `ListeningPassageAudio`, or a chunk clip when
chapter listening finished the passage meanwhile). The job worker checks the cache again before
synthesis, so equivalent audio retained while the job waited is reused without a provider request; the
job does not report whether that happened (its resource operation is marked `cached`). Failure and
cancellation are job outcomes; audio finished during a Stop is still retained and can be found in
`/listen/takes`. There is no narration budget or dollar cap. Do not automatically repeat this POST
after an uncertain network response; retry only read-only polling.

If Gemini's content policy blocks the passage (HTTP 400 `content_blocked`), the job fails with
`error_code: "content_blocked"` and a fixed error sentence, and the passage is remembered as blocked: a
later request for it fails at once without a Gemini request. A fallback take made by chapter listening
(`substitute`) is returned as a cache hit.

This endpoint prepares only the requested passage; there is no streaming endpoint. Breeze and device
voices always use it (Gemini chapters use `POST /listen/chapter`). The browser coordinates device
warmup (about 10 listening seconds, at most three passages), lookahead (about 45 seconds, at most 12
future passages, staying in the chapter) and the explicit "Prepare rest of chapter" action by calling
this endpoint serially; those queues do not resume after a browser or server restart."""

_CHAPTER_DESCRIPTION = """\
Start, or join, the Gemini `listen_chapter` job that prepares a chapter in large chunk requests from the
selected passage onward. Chunks are exact chapter slices (whole consecutive passages); each finished
chunk is retained with estimated per-passage clips, which then appear in `/listen/takes` and in
`POST /listen` cache hits.

`intent` is `play` (the listener is waiting: early requests follow the short `ramp_seconds` steps) or
`queue` (default). `queue` without explicit `chunking.ramp_seconds` uses full-size chunks only.
`chunking` overrides the saved `listen_chunking` preference field by field.

**Join.** If a non-cancelled `listen_chapter` job is active for the book with the same session and
chapter, the request joins it and returns `{session, job, joined: true}`: `focus_segment_id` moves to
the passage, `scope_start_segment_id` extends backwards when needed, `joins` increments, and a `play`
request for a passage with no audio and no request in flight increments `ramp_restart` (the job
restarts its ramp). Joining never checks the key or quota and never starts a second job.

**Refusals (409).** The active chapter job belongs to a saved performance (`performance_active`); it is
for a different chapter or narrator (`chapter_listen_active`); it is closing (`chapter_job_closing`,
retry shortly); or other work holds the book.

**Start.** Otherwise requires a Gemini key (400) and a server that is not shutting down (503). It then
refuses with 429 `daily_quota_reached`, without queueing or sending anything, when either:

- a daily-quota block holds for the model in this process (set after a daily-quota 429 from Gemini or
  after a chapter job stopped at the configured requests per day; it lasts until midnight Pacific or
  until limits are saved in Settings); or
- this library's recorded Gemini speech requests for the model since midnight Pacific (the
  `quota.requests_today` of the chapter preview, counting every narration path) have reached the
  configured requests per day (`limits.rpd`).

The 429 response carries a `Retry-After` header: whole seconds until the block lifts or the quota day
resets at midnight Pacific.

The job paces requests with the shared per-minute limiter (`waiting_seconds` while waiting), recounts
this library's daily requests before every send, keeps up to `concurrency` requests in flight (1 until a
full-size chunk has been measured), retries a per-minute 429 up to 5 consecutive times, re-plans smaller
after a truncated response (at most 2 truncation rounds), and never resends an uncertain request. It
reports `progress`/`total` in passages of its scope, `chunks` (one entry per request: `n`, first/last
passage IDs, `segment_count`, `chars`, `target_seconds`, `expected_seconds`, `expected_latency`,
`realtime_factor`, `epoch`, `status` `requesting`/`done`/`rate_limited`/`truncated`/`blocked`/`failed`, `split`, `split_into`,
`started_at`/`finished_at`, `error`, and for finished chunks `chunk_id`, `duration`, `latency`, `flags`,
`matched`/`boundaries`), `projection` (remaining planned chunks in request order), `calibration`,
`speech_limits`, `quota` (`requests_today`, `rpd`, `resets_at`, `scope: "this library"`), `waiting_seconds` and
the selected `chunking`. Terminal statuses include `quota_limited` with `resume_after` (for example when
other traffic uses up the daily count while the job runs). Finished chunks are kept on every outcome;
start the chapter again to resume.

**Text Gemini blocks.** Gemini can refuse text under its content policy with HTTP 400 and the error code
`content_blocked`; it does not say which passage, and this is not a model, voice or length problem. The
provider's error text is never kept. The job handles a block with a fixed budget of requests:

1. The blocked chunk (`status: "blocked"`) is retained as a block for this session, chapter, exact text
   and recipe, and is never sent again.
2. A chunk of two or more passages is split once, at a passage boundary near its middle (preferring a
   scene change, then a paragraph break, then a sentence end), into two halves (`split: true`). Each half
   is requested like any chunk: reserved against the daily count and the per-minute limiter before it is
   sent, and counted in `quota`.
3. A half that is blocked again is not split further, and a blocked one-passage chunk is not split.
   Those passages are read one by one by the free local **fallback narrator** snapshotted in `fallback`
   (a device voice when `say` and ffmpeg are available, otherwise Breeze when configured), with no Gemini
   request. That audio is an ordinary immutable take of the fallback session, marked `substitute`, and plays
   with the rest. When no fallback narrator is available, or it fails, the passages stay unrecorded.

One blocked chunk therefore costs at most three Gemini requests: the original and its two halves. A block
is remembered durably, so starting the chapter again (or resuming a performance) never resends text
Gemini already blocked; a changed source text, voice, model or pronunciation invalidates that memory and
the text is requested again. Successful halves are kept as normal chunks. A block never fails the job: it
ends `completed`, `content_blocked` lists the passages read by the fallback narrator and the ones left
unrecorded, and `message` says so. Blocked passages are not counted in `progress` unless a fallback
narrator read them."""

_VOICE_PREVIEW_DESCRIPTION = """\
Audition a voice on a short, exact excerpt of the book (or the fixed demo text). `voice` accepts the
simple-listening values (`"library:vl_…"`, a direct ID, or empty for Default; a direct Breeze ID must
be in the last Breeze check). There is no caller-supplied transcript: the text is always resolved from
the stored book or the fixed demo.

Text selection: an explicit `segment_id` is used even when auditioning an unsaved speaker assignment.
Otherwise a selected `character_id` uses that character's first attributed passage, falling back to
the demo when none exists; neither selector means demo text. References or name mentions never
substitute for attributed speech. A passage sample is an exact original prefix of at most 400 Python
code points, preferably ending at a sentence or word boundary, with a validated chapter-local
`source_anchor`; `truncated` marks a shortened sample. (The browser prefers the currently selected
passage for the character, then its first passage in the current chapter.)

With a character, the recipe includes its effective direction (or `direction`) plus the saved scene
tone/direction, the passage direction (or `segment_direction`) and cues. Without a character, the
sample omits enhanced performance inputs. Requests never modify cast, source, reading position or
selected simple/enhanced takes.

A cache hit returns `{preview, audio, cached: true}` before provider availability or busy checks.
Reuse is book-scoped and covers source and the effective performance recipe (a character rename can
reuse speech); it is independent of the enhanced and simple caches. Otherwise an active non-cancelled
`voice_preview` job for the same preview is joined (`{preview, job, cached: false}`); otherwise, after
the busy, shutdown and provider checks, a one-unit `voice_preview` job is queued. The job retains its
`preview` and, when completed, `audio` (a `VoicePreviewAudio`). The worker checks the cache again
before synthesis. Credentials are snapshotted at queue time and cancellation is checked before
synthesis; a take finished in flight is still retained after Stop. There is no retry of this POST and
no dollar cap; Gemini auditions can incur charges (resource stage `voice_preview`, which keeps reported
usage and records an unknown cost as unknown, not zero).

Book pronunciations apply to every example. An optional `pronunciation` object (the entry fields, plus the `id`
of the entry it edits) auditions an unsaved respelling in place of the saved one; it is never stored in the
book. With it and no `segment_id`, the first passage containing the word is used, and the sample is that word's
whole sentence from the chapter (exact source coordinates in `source_anchor`, at most 400 code points). A word
the book does not contain is read in a fixed original carrier sentence (`source: "demo"`). When a respelling
applies, `preview.spoken_text` shows the text sent, and a draft adds `preview.pronunciation: {term, spoken}`.
The retained request keeps only the matching entries' speech fields (`term`, `respelling`, `providers`,
`match_case`), never IDs, notes or unrelated entries, so hearing the same unsaved spelling twice reuses the
first example.

Preview records and WAVs are kept
in the library and in a full library backup, but are not included in the analysis or audiobook ZIP."""

_PERFORMANCE_JOB = """\
The `performance` job carries `performance_id`, `mode`, `provider`, `model`, `total` (passages missing at
start), `progress` and a message such as `Chapter 2 of 5 · passage 14 of 40`; it completes with
`Performance ready`. Credentials, limits and chunk options are snapshotted at start; cancellation is
checked between passages. Device and Breeze simple performances render one passage at a time
(resource stage `simple_listen`). Gemini simple performances run each chapter with missing audio as a
child `listen_chapter` job (`parent_id`, `intent: "queue"`, full-size chunks) listed in the parent's
`child_job_ids` (`child_job_id` is the running one); the parent's `progress` advances only when each
chapter's child job settles. A live listener's chapter request is refused
(409) rather than joining it. A child that stops for the daily quota, a budget, cancellation or an
error ends the parent the same way (`quota_limited` with `resume_after`, and so on), with finished
chunks kept. Text Gemini blocks is handled inside each child job (see `startChapterListening`): the parent still
completes, its message names how many passages a fallback narrator read or were left unrecorded, and
`Performance.progress` reports them as `passages_fallback` and `passages_blocked`. Cast performances render passage by passage (resource stage `narration`, `cached: true`
for reuse of another performance's or a Studio take with the identical recipe) and never change the
Studio's selected takes. Gemini cast requests share the per-minute rate limiter with other Gemini speech, and stop as
`quota_limited` at the provider's daily quota or
at this library's configured requests per day, and retry a per-minute 429 at most five consecutive
times. An uncertain request (timeout, dropped connection) is never resent; the job fails with completed
audio kept, and the failure names the passage. A passage Gemini's content policy blocks fails a cast
performance the same way, with `error_code: "content_blocked"`: cast performances have no fallback narrator
or splitting (use one narrator to get them). There is no dollar allowance for performances. Up to
three performances of different books run at once; one job per book still applies, counting every active
job for the book (including child jobs beyond the 100-job list bound)."""

OPS: list[Op] = [
    # -------------------------------------------------------------- Narration
    op('POST', '/api/books/{book_id}/render', 'startEnhancedRender', 'Narration',
       'Queue enhanced (cast) narration takes',
       """\
Queue a `render` job that narrates the selected passages with each passage's cast voice, scene and
performance metadata, and makes each result the passage's current Studio take. Omitted selectors mean
the whole book; if both `scene_id` and `segment_id` are given they are intersected. The TTS model comes
from settings for Gemini (`breeze-tts-2` for Breeze, `macos-say` for device). Resolved cast voices are
snapshotted when queued, so a voice change during the job does not mix voices.

Without `force`, each passage first reuses, without a provider request, the current take with the same
recipe fingerprint, then an archived take with that fingerprint (so reverting a voice edit reuses the old
WAV), then a legacy recipe-named WAV. `force: true` always generates another take; older bytes are
preserved. Model, voice and performance edits change the fingerprint and therefore reuse. Device
narration ignores expressive metadata; Gemini and Breeze interpret it. No renderer guarantees
word-perfect speech.

Returns the queued job; poll it. Progress counts passages; the message reports reused passages. For
Breeze and Gemini every selected speaker must have a usable voice, otherwise 400 before queueing.
Refused with 503 while the server is shutting down.""",
       response=Job, response_description='The queued `render` job.',
       errors={400: {'provider_unsupported': '`provider` is not `system`, `gemini` or `breeze`.', **_PROVIDER,
                     **_UNKNOWN_PASSAGE, 'unknown_scene': 'The body names a scene (`scene_id`) that is not in this book.',
                     'no_passages_selected': 'The named passage is not in the named scene.',
                     'cast_voice_unusable': 'A selected speaker has no usable Breeze or Gemini voice; the detail '
                                            'names up to five speakers.'},
               404: _BOOK_404, 409: {**_ARCHIVED, **_BUSY}, 503: _STOPPING},
       params={'book_id': _BOOK_ID}, cost='may_charge'),
    op('GET', '/api/audio/{book_id}/{segment_id}', 'getPassageAudio', 'Narration',
       "Download a passage's current enhanced take",
       """\
The WAV of the passage's current Studio (enhanced) take, only while it is valid: its recipe fingerprint
must still match the passage, speaker and scene with the current resolved cast, and the file must exist.
The book document gives valid takes a URL of this form with a `?v=` cache-busting query, which the
server ignores. Local read.""",
       media='audio/wav', ranges=True, response_description='Mono 24 kHz 16-bit PCM WAV.',
       errors={404: {**_BOOK_404, 'passage_not_found': 'The book has no passage with this ID.',
                     'audio_not_found': 'The passage has no take, or its take is stale or its file is missing.'}},
       params={'book_id': _BOOK_ID, 'segment_id': 'Passage (segment) ID.'}),
    op('GET', '/api/books/{book_id}/audio-assets/{asset_id}', 'getRetainedAudioAsset', 'Narration',
       'Download a retained enhanced audio asset',
       """\
Bytes of any retained enhanced audio asset of the book, current or historical, including cast
performance takes (their `url` points here). The asset is identified by its content hash (older takes
by recipe fingerprint). The file is served as stored; its integrity is not re-verified. Local read.""",
       media='audio/wav', ranges=True, response_description='Mono 24 kHz 16-bit PCM WAV.',
       errors={404: {**_BOOK_404, 'audio_not_found': 'Malformed ID (32-128 lower-case hex) or no such file.'}},
       params={'book_id': _BOOK_ID, 'asset_id': 'Asset ID: 32-128 lower-case hex characters.'}),

    # -------------------------------------------------------------- Listening
    op('POST', '/api/books/{book_id}/listen', 'listenToPassage', 'Listening',
       'Get or queue simple narration for one passage', _LISTEN_DESCRIPTION,
       response=Union[ListenCached, ListenQueued],
       response_description='`cached: true` with `audio`, or `cached: false` with a new or joined `listen` job.',
       errors={400: {**_UNKNOWN_PASSAGE, **_SOURCE, **_NARRATOR,
                     **{code: f'Only when synthesis is needed: {text}' for code, text in _PROVIDER.items()}},
               404: _BOOK_404,
               409: {**_ARCHIVED, **{code: f'Only when synthesis is needed: {text}' for code, text in _BUSY.items()}},
               503: _STOPPING},
       params={'book_id': _BOOK_ID}, cost='may_charge'),
    op('GET', '/api/books/{book_id}/listen/takes', 'listListeningTakes', 'Listening',
       "List a listening session's playable audio",
       """\
Saved simple audio for the session, one entry per passage in book order: a chunk clip when one
applies, otherwise the newest single-passage take whose source recipe still matches the passage. For a
Gemini session, a passage Gemini's content policy blocked that has no Gemini audio is listed with the
fallback narrator's take, whose `substitute` marks it (see `startChapterListening`); a blocked passage with no
fallback take is omitted.
Passages whose source no longer matches, and takes whose file is missing, are omitted. To stay fast on
long books this does not re-read WAV samples; a chunk whose file is known to be damaged is excluded.
No generation and no stored change. Works for archived books.""",
       response=ListeningTakes,
       errors={404: {**_BOOK_404, 'listening_session_not_found': 'The book has no listening session with this `session_id`.'}},
       params={'book_id': _BOOK_ID, 'session_id': 'Listening session ID from a `session` object.'}),
    op('GET', '/api/books/{book_id}/listen/audio/{asset_id}', 'getListeningAudio', 'Listening',
       'Download a simple-listening WAV',
       """\
A retained simple-listening WAV (single-passage take or shared chunk), scoped to the book that retained
it. The file must be recorded for this book and exist; it is served without re-verifying its hash.
For a chunk clip, play from `clip_start` to `clip_end`. Local read.""",
       media='audio/wav', ranges=True, response_description='Mono 24 kHz 16-bit PCM WAV.',
       errors={404: {**_BOOK_404, 'audio_not_found': 'Malformed ID (64 lower-case hex), not retained for this book, or file missing.'}},
       params={'book_id': _BOOK_ID, 'asset_id': 'Asset ID: 64 lower-case hex characters (SHA-256 of the WAV).'}),
    op('POST', '/api/books/{book_id}/listen/chapter/preview', 'previewChapterListening', 'Listening',
       'Plan chapter listening from a passage',
       """\
Local plan for a chapter job starting at the passage: the chunks that would be requested, requests
needed, expected audio, ready passages and seconds, effective chunk options, calibration, configured
limits and this library's daily request count for the model. Takes the same body as
`POST /listen/chapter` (`intent: "queue"` without explicit ramp steps plans full-size chunks only).
Creates the deterministic narrator session row only; no job, no provider request, no key required, and
it works while the book is busy. When `quota.requests_today` has reached `limits.rpd`, starting the
chapter is refused with 429.""",
       response=ChapterListenPlan,
       errors={400: {**_UNKNOWN_PASSAGE, **_NARRATOR}, 404: _BOOK_404, 409: _ARCHIVED},
       params={'book_id': _BOOK_ID}),
    op('POST', '/api/books/{book_id}/listen/chapter', 'startChapterListening', 'Listening',
       'Start or join Gemini chapter preparation', _CHAPTER_DESCRIPTION,
       response=ChapterListenStarted,
       errors={400: {**_UNKNOWN_PASSAGE, **_NARRATOR,
                     'gemini_key_missing': 'Only when starting: no Gemini API key is configured.'},
               404: _BOOK_404,
               409: {**_ARCHIVED, **_BUSY,
                     'performance_active': 'A saved performance\'s chapter job is preparing this book.',
                     'chapter_listen_active': 'A chapter job for another chapter or narrator is active.',
                     'chapter_job_closing': 'The matching chapter job is finishing; retry shortly.'},
               429: {'daily_quota_reached': 'Only when starting: a daily-quota block holds for the model, or this '
                                            'library\'s requests today have reached the configured requests per day. '
                                            'Nothing was queued or sent; `Retry-After` gives the seconds to wait.'},
               503: _STOPPING},
       params={'book_id': _BOOK_ID}, cost='may_charge'),

    # -------------------------------------------------------------- Performances
    op('GET', '/api/books/{book_id}/performances', 'listPerformances', 'Performances',
       'List saved performances',
       'Performances of the book, newest first, each with its latest job summary and readiness. Archived '
       'records are included only with `archived=true`. Local read with no stored change; readiness uses file '
       'existence, not WAV validation.',
       response=PerformanceList,
       errors={404: _BOOK_404},
       params={'book_id': _BOOK_ID, 'archived': 'Include archived performances (default false).'}),
    op('POST', '/api/books/{book_id}/performances/preview', 'previewPerformance', 'Performances',
       'Estimate a performance without starting it',
       """\
Local plan: readiness, passages to generate, request estimate, expected audio, blocking `problems` and
advisory `notes`, and for Gemini this library's daily request count. No provider calls and no job;
creating the deterministic listening session row is allowed. Narrator and provider conditions the user
can fix (missing key, unusable voice, narrator without a voice for a cast) are returned in `problems`
rather than as errors; `createPerformance` refuses them with the codes it lists.""",
       response=PerformancePlan,
       errors={400: {'unknown_chapter': 'A chapter ID in `chapter_ids` is not in this book.',
                     'model_unsupported': 'The Gemini model is not supported, or the model does not match the '
                                          'device or Breeze fixed model.'},
               404: _BOOK_404, 409: _ARCHIVED},
       params={'book_id': _BOOK_ID}),
    op('POST', '/api/books/{book_id}/performances', 'createPerformance', 'Performances',
       'Create a performance and start preparing it',
       f"""\
Validate, record and start a performance. Simple performances pin a listening session (takes made
earlier by live listening with the same narrator count as ready); cast performances snapshot the
resolved cast now. Returns `{{performance, job}}`; `job` is null when every passage is already ready.
The record is saved before the job starts. When the preview would report `problems`, the request is
refused with 400: the code is the first problem's, and the detail joins every problem's sentence.

{_PERFORMANCE_JOB}""",
       response=PerformanceStarted,
       errors={400: {'unknown_chapter': 'A chapter ID in `chapter_ids` is not in this book.',
                     'model_unsupported': 'The Gemini model is not supported, or the model does not match the '
                                          'device or Breeze fixed model.', **_PROBLEMS},
               404: _BOOK_404, 409: {**_ARCHIVED, **_BUSY}, 503: _STOPPING},
       params={'book_id': _BOOK_ID}, cost='may_charge'),
    op('GET', '/api/books/{book_id}/performances/{performance_id}', 'getPerformance', 'Performances',
       'Get a performance',
       'The performance with its latest job summary and readiness. Local read with no stored change.',
       response=PerformanceEnvelope,
       errors={404: _PERFORMANCE_404},
       params={'book_id': _BOOK_ID, 'performance_id': 'Performance ID (`pf_…`).'}),
    op('GET', '/api/books/{book_id}/performances/{performance_id}/audio', 'getPerformanceAudio', 'Performances',
       "List a performance's playable audio",
       """\
Audio for each selected passage that is ready against its current source, as JSON (not bytes); play
each `url`. Simple performances return the `/listen/takes` objects (single-passage takes or chunk clips
with `clip_start`/`clip_end`); cast performances return the newest retained take per passage
(`PerformanceCastAudio`, with `speaker_id`), served from `/audio-assets/`. Local read with no stored
change; file existence is checked but WAVs are not re-validated.""",
       response=PerformanceAudioMap,
       errors={404: _PERFORMANCE_404},
       params={'book_id': _BOOK_ID, 'performance_id': 'Performance ID (`pf_…`).'}),
    op('POST', '/api/books/{book_id}/performances/{performance_id}/prepare', 'preparePerformance', 'Performances',
       'Resume preparing a performance',
       f"""\
Generate what is missing with the same pinned narrator session or cast snapshot (settings changed since
creation do not apply, except that current credentials, limits and chunk options are used). Cast
resume validates retained WAVs, so a damaged file is narrated again; a regenerated file whose bytes
match the damaged one's content address is refused rather than overwritten (possible with
deterministic device voices). Chapters removed from the book are skipped. Returns `{{performance, job}}`
with `job` null when nothing is missing. Blocking problems are refused with 400 as for
`createPerformance`.

{_PERFORMANCE_JOB}""",
       response=PerformanceStarted,
       errors={400: {**_PROVIDER, 'narrator_voice_missing': _PROBLEMS['narrator_voice_missing']},
               404: _PERFORMANCE_404,
               409: {**_ARCHIVED, **_BUSY}, 503: _STOPPING},
       params={'book_id': _BOOK_ID, 'performance_id': 'Performance ID (`pf_…`).'}, cost='may_charge'),
    op('POST', '/api/books/{book_id}/performances/{performance_id}/preview', 'previewPerformanceResume', 'Performances',
       'Estimate recording the rest of a performance',
       """\
Local plan for an existing performance with its pinned narrator session or cast snapshot: what is ready,
what a job would narrate, the request estimate, blocking `problems` and advisory `notes`. `chapter_ids`
lists chapters to add first; leave it empty to plan the performance as it is. Chapters already selected are
ignored (`added_chapter_ids` lists the new ones). Nothing is stored, no job starts and no provider is
contacted. Allowed while a job runs.""",
       response=PerformancePlan,
       errors={400: {'unknown_chapter': 'A chapter ID in `chapter_ids` is not in this book.'},
               404: _PERFORMANCE_404, 409: _ARCHIVED},
       params={'book_id': _BOOK_ID, 'performance_id': 'Performance ID (`pf_…`).'}),
    op('POST', '/api/books/{book_id}/performances/{performance_id}/chapters', 'addPerformanceChapters', 'Performances',
       'Add chapters to a performance and record them',
       f"""\
Extend the performance's chapter selection and start recording what is missing, without creating a new
performance. The pinned narrator session or cast snapshot, model and pronunciations stay as they were, so
audio already retained is reused and only passages without current audio are narrated (a simple
performance also counts takes made by live listening with the same narrator). The added chapters are
saved to `chapter_ids` and appended to `chapters_added` before the job starts; retained audio is never
rewritten. Returns `{{performance, job}}`; `job` is null when every passage is already ready. Refused with 409
while any job is active for the book, including this performance's own: stop it, or wait, first. Blocking
problems are refused with 400 as for `createPerformance`.

{_PERFORMANCE_JOB}""",
       response=PerformanceStarted,
       errors={400: {'unknown_chapter': 'A chapter ID in `chapter_ids` is not in this book.',
                     'no_chapters_selected': '`chapter_ids` is empty.',
                     **_PROVIDER, 'narrator_voice_missing': _PROBLEMS['narrator_voice_missing']},
               404: _PERFORMANCE_404, 409: {**_ARCHIVED, **_BUSY}, 503: _STOPPING},
       params={'book_id': _BOOK_ID, 'performance_id': 'Performance ID (`pf_…`).'}, cost='may_charge'),
    op('PATCH', '/api/books/{book_id}/performances/{performance_id}', 'updatePerformance', 'Performances',
       'Rename or archive a performance',
       'Change label fields only: `name` (trimmed) and `archived`. Never deletes or changes audio, and is '
       'allowed while jobs run. Like every other write to a book, it is refused with 409 `book_archived` while '
       'the book is archived. Omitted or null fields are unchanged; with no fields the record is returned '
       'as is (and `updated_at` is not touched). An empty `name` string or one over 200 characters fails '
       'request validation (422).',
       response=PerformanceEnvelope,
       errors={400: {'performance_name_required': '`name` is only whitespace.'},
               404: _PERFORMANCE_404, 409: _ARCHIVED},
       params={'book_id': _BOOK_ID, 'performance_id': 'Performance ID (`pf_…`).'}),

    # -------------------------------------------------------------- Voice previews
    op('POST', '/api/books/{book_id}/voice-preview', 'startVoicePreview', 'Voice previews',
       'Get or queue a short voice audition', _VOICE_PREVIEW_DESCRIPTION,
       response=Union[VoicePreviewCached, VoicePreviewQueued],
       response_description='`cached: true` with `audio`, or `cached: false` with a new or joined `voice_preview` job.',
       errors={400: {**_UNKNOWN_PASSAGE, 'unknown_character': 'The body names a character (`character_id`) that is not in this book.',
                     **_SOURCE, **_NARRATOR,
                     'direction_requires_character': '`direction` without `character_id`.',
                     'segment_direction_requires_passage': '`segment_direction` without both a passage and a character.',
                     'pronunciation_invalid': 'The `pronunciation` entry is not valid.',
                     **{code: f'Only when synthesis is needed: {text}' for code, text in _PROVIDER.items()}},
               404: _BOOK_404,
               409: {**_ARCHIVED, **{code: f'Only when synthesis is needed: {text}' for code, text in _BUSY.items()}},
               503: _STOPPING},
       params={'book_id': _BOOK_ID}, cost='may_charge'),
    op('GET', '/api/books/{book_id}/voice-preview/audio/{asset_id}', 'getVoicePreviewAudio', 'Voice previews',
       'Download an audition WAV',
       'A retained audition WAV, scoped to its owning book. Every request verifies the content hash and '
       'WAV format before serving. Local read.',
       media='audio/wav', ranges=True, response_description='Mono 24 kHz 16-bit PCM WAV.',
       errors={404: {**_BOOK_404, 'audio_not_found': 'Malformed ID, not retained for this book, or the file is '
                                                     'missing or damaged.'}},
       params={'book_id': _BOOK_ID, 'asset_id': 'Asset ID: 64 lower-case hex characters (SHA-256 of the WAV).'}),
]

_VOICE_VALUES = ('`"library:vl_…"` (that library voice\'s current version), a direct provider voice ID, or empty/null '
                 'for Default (Gemini `Kore`, the device default voice, or the Bardic default Breeze library voice). '
                 'A direct Breeze ID must be in the last Breeze voice check. At most 256 characters.')

REQUEST_DOCS: dict[str, dict[str, str]] = {
    'RenderRequest': {
        '__doc__': 'Which passages to narrate with the cast, and how.',
        'provider': 'Narration provider: `system` (default, macOS `say`), `gemini` or `breeze`. Other values give 400.',
        'scene_id': 'Only passages of this scene. Omit for no scene filter.',
        'segment_id': 'Only this passage. Omit for no passage filter. With `scene_id`, both must match.',
        'force': 'Generate a new take even when a take with the same recipe exists (older bytes are kept). Default false.',
    },
    'ListenRequest': {
        '__doc__': 'One passage with one simple narrator.',
        'provider': 'Narration provider: `system` (default), `gemini` or `breeze`.',
        'voice': 'Narrator voice: ' + _VOICE_VALUES,
        'model': 'Speech model. Omit or null for the configured Gemini TTS model; device narration accepts only '
                 '`macos-say` and Breeze only `breeze-tts-2`. At most 200 characters.',
        'segment_id': 'Required. The passage (segment) to narrate.',
    },
    'ChapterListenRequest': {
        '__doc__': 'Gemini chapter listening from a passage (also the body of the chapter preview).',
        'provider': 'Must be `gemini` (the default). Breeze and device voices use `POST /listen` per passage.',
        'voice': 'Gemini voice: a voice name or custom `voice_…` ID, `"library:vl_…"`, or empty/null for `Kore`. At most 256 characters.',
        'model': 'Gemini TTS model; omit or null for the configured one. At most 200 characters.',
        'segment_id': 'Required. The passage to start from (the listener position). At most 200 characters.',
        'intent': '`play` (a listener is waiting; the first requests follow the short ramp) or `queue` (default; '
                  'full-size chunks unless `chunking.ramp_seconds` is given).',
        'chunking': 'Optional per-request override of the saved `listen_chunking` preference, field by field.',
    },
    'ChunkingOptions': {
        '__doc__': 'Chunk sizing for chapter listening. Omitted or null fields use the saved preference '
                   '(defaults: ramp `[30, 60]`, target 420, concurrency 2).',
        'ramp_seconds': 'Target audio seconds for the first requests, in order; at most six steps, each 10-470. '
                        '`[]` means every request is full size.',
        'target_seconds': 'Full-size chunk target in audio seconds, 30-470.',
        'concurrency': 'Requests in flight at once, 1-3 (1 until a full-size chunk has been measured).',
    },
    'PerformanceRequest': {
        '__doc__': 'A performance to preview or create.',
        'name': 'Display name, at most 200 characters. Omit, null or blank for a default such as `Kore · Gemini · 3 chapters`.',
        'mode': 'Required. `simple` (one narrator) or `cast` (each speaker in their cast voice, narrator fallback).',
        'chapter_ids': 'Required, 1-5000 chapter IDs of this book (each at most 200 characters); stored in book order.',
        'provider': 'Required. `system`, `gemini` or `breeze`.',
        'voice': 'Simple mode narrator: ' + _VOICE_VALUES + ' Ignored for `cast`.',
        'model': 'Gemini: a supported TTS model, default the configured one. Device and Breeze use their fixed '
                 'models (`macos-say`, `breeze-tts-2`) and reject any other value. At most 200 characters.',
    },
    'PerformanceChapters': {
        '__doc__': 'Chapters to add to an existing performance.',
        'chapter_ids': 'Chapter IDs of this book to add, at most 5000 (each at most 200 characters). Already-selected chapters '
                       'are ignored. Empty is allowed for `previewPerformanceResume` (plan the performance as it is) and '
                       'refused by `addPerformanceChapters`.',
    },
    'PerformanceEdit': {
        '__doc__': 'Label changes. Omitted or null fields are unchanged.',
        'name': 'New name, 1-200 characters; trimmed, and whitespace-only gives 400.',
        'archived': 'Hide (true) or restore (false) the performance in the default list. Audio is never deleted.',
    },
    'VoicePreviewRequest': {
        '__doc__': 'A voice audition. There is no transcript field: text comes from the stored book or the fixed demo.',
        'provider': 'Narration provider: `system` (default), `gemini` or `breeze`.',
        'voice': 'Voice to audition: ' + _VOICE_VALUES,
        'model': 'Speech model; omit for the configured Gemini model or the provider\'s fixed model. Device and '
                 'Breeze reject other values. At most 200 characters.',
        'segment_id': 'Passage to sample (an exact prefix of at most 400 code points). At most 200 characters.',
        'character_id': 'Character to audition. Without `segment_id`, their first attributed passage is used (demo '
                        'text if none). At most 200 characters.',
        'direction': 'Unsaved character direction to use instead of the saved one. Requires `character_id`. At most 3000 characters.',
        'segment_direction': 'Unsaved passage direction. Requires both a passage and `character_id`. At most 3000 characters.',
        'pronunciation': 'An unsaved pronunciation entry to audition in place of the saved entry with the same `id` (or '
                         'in addition to the saved ones, without an `id`). Never stored in the book. Without '
                         '`segment_id`, the sample is the sentence around the word\'s first occurrence, or a fixed '
                         'carrier sentence when the book does not contain it.',
    },
}
