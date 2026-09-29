"""Views shared by several route families.

Owned by the System/Jobs family. Other families import from here and do not
edit it; report missing fields to the owner instead.

``Job`` is the durable job document embedded by every job-starting route and
listed by ``GET /api/jobs``: a union of eight kinds, each its own schema with only
the fields that mean something for it. Its kind-specific parts reuse the audio,
voice-preview and chapter-plan views in ``media`` (import order: base -> media ->
common -> route families).
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field
from typing_extensions import TypeAliasType

from .base import View
from .enums import AnalysisProvider, NarrationProvider
from .media import (ChapterListenCalibration, ChapterListenChunking, ChapterListenChunkPlan, ChapterListenLimits,
                    ChapterListenQuota, ListeningAudio, VoicePreview, VoicePreviewAudio)

TIME = 'ISO 8601 UTC timestamp with offset, for example `2026-09-28T17:04:05.123456+00:00`.'

JobStatus = Literal['queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted', 'budget_limited', 'quota_limited']


class JobChapterChunk(View):
    """One chunk request a ``listen_chapter`` job has sent (or is sending), in send order."""
    n: int = Field(description='1-based send order within the job.')
    first_passage_id: str = Field(description='First passage in the chunk.')
    last_passage_id: str = Field(description='Last passage in the chunk.')
    passage_count: int = Field(description='Consecutive passages in the chunk.')
    chars: int = Field(description='Code points of the exact chapter slice sent.')
    target_seconds: float = Field(description='Audio length in seconds the chunk was sized for.')
    expected_seconds: float = Field(description='Audio length in seconds expected from the speech-rate estimate at send time.')
    expected_latency: float = Field(description='Expected seconds until the response, from calibration.')
    realtime_factor: float = Field(description='Calibration realtime factor (audio seconds per waiting second) at send time.')
    epoch: int = Field(description='Planning generation; increases after a truncation forces smaller re-planning.')
    status: Literal['requesting', 'done', 'rate_limited', 'truncated', 'blocked', 'failed'] = Field(
        description='`requesting` while in flight (on a job that is no longer running: in flight when it stopped, and '
                    'its outcome unknown); `done` when its audio was retained; `rate_limited` when the '
                    'provider refused it with HTTP 429 (nothing generated; its passages are planned again); '
                    '`truncated` when the audio was cut short or far too short and was discarded; `blocked` when '
                    'Gemini refused the text under its content policy (HTTP 400 `content_blocked`; the block is '
                    'retained and this text is never sent again; the job continues, see `Job.content_blocked`); '
                    '`failed` on any other error (the job then stops).')
    split: bool | None = Field(
        None, description='True for a half of a chunk Gemini blocked. A half is requested once and never split '
                          'again. Absent otherwise.')
    split_into: int | None = Field(
        None, description='`blocked` only: the number of halves the chunk was split into (2), or absent when it was '
                          'not split (a one-passage chunk or a half, whose passages go to the fallback narrator).')
    uncertain: bool | None = Field(
        None, description='`failed` only: true when the request may have been processed and billed (a timeout or dropped '
                          'connection), so it is never resent. Absent when the provider answered with an error, and on jobs '
                          'recorded before contract 0.5.1. A saved performance retries a chunk that failed with an error '
                          'response once before its fallback narrator reads it; it never retries an uncertain one.')
    started_at: str = Field(description='When the request was sent: ' + TIME)
    finished_at: str | None = Field(None, description='When the request finished; absent while `requesting`. ' + TIME)
    error: str | None = Field(None, description='Human-readable reason for `rate_limited`, `truncated`, `blocked` or `failed` '
                                                '(at most 300 characters for `failed`).')
    duration: float | None = Field(None, description='Seconds of audio received (`done`, `truncated`).')
    chunk_id: str | None = Field(None, description='ID of the retained chunk audio (`done`).')
    latency: float | None = Field(None, description='Measured seconds from send to response (`done`).')
    flags: list[Literal['weak_alignment']] | None = Field(
        None, description='Quality flags (`done`). `weak_alignment`: fewer than 60% of passage boundaries matched, '
                          'so passage clip times are rough.')
    matched: int | None = Field(None, description='Passage boundaries the aligner matched in the audio (`done`).')
    boundaries: int | None = Field(None, description='Passage boundaries the aligner tried to match (`done`).')


class JobFallbackNarrator(View):
    """The fallback narrator a job snapshotted for passages the main narration cannot read."""
    session_id: str = Field(description='The narrator session (64 hex) whose takes read the blocked passages.')
    provider: NarrationProvider = Field(description='`system` (a device voice), `gemini` or `breeze`.')
    model: str = Field(description='Speech model of the fallback narrator (`macos-say`, the Breeze model or a Gemini TTS model).')
    voice: str = Field(description='Voice choice of the fallback session; empty for the default device voice.')


class JobContentBlocked(View):
    """What Gemini's content policy did to a job's text, and how each blocked passage ended.

    Present once Gemini blocked any text of the job's chapter. The job itself still ends `completed`: the rest of
    the chapter was prepared, and its `message` says how many passages were read by the fallback narrator or left
    unrecorded. A saved performance reports the same outcome per chapter in `Performance.progress`.
    """
    fallback: JobFallbackNarrator | None = Field(
        description='The fallback narrator that reads blocked passages, or null when none is available (a device '
                    'voice needs macOS `say` and ffmpeg; Breeze needs a configured server with a usable default voice).')
    fallback_passage_ids: list[str] = Field(
        description='Blocked passages the fallback narrator read, in reading order. Their audio plays with the rest, is '
                    'marked `substitute` and is not Gemini audio.')
    blocked_passage_ids: list[str] = Field(
        description='Blocked passages with no audio at all (no fallback narrator, or it failed), in reading order. They '
                    'are not requested from Gemini again.')
    fallback_error: str | None = Field(
        description='Why the fallback narrator could not read a passage (at most 300 characters), or null.')


# ----------------------------------------------------------------------- pipeline run settings
# Defined here because `pipeline` and `series` jobs embed them; the pipeline family re-uses them.

class PipelineStepConfigView(View):
    """The provider and model a run snapshotted for one step."""
    provider: AnalysisProvider = Field(description='Provider ID the run uses for this step: `local` for plain steps, otherwise a '
                                      'pipeline provider ID.')
    model: str | None = Field(description='Model ID the run sends, or null for plain steps and service providers.')


class PipelineRunLimits(View):
    """Caps a run was started with; null means uncapped.

    A `series` run recorded before contract 0.3.0 has the same four fields
    with every value but `budget_usd` set (its allowance: 1–1000 requests,
    1,000–10,000,000 input tokens, 1,000–2,000,000 output tokens, and an
    optional dollar ceiling above 0 and at most 1000). Its caps applied to
    each book separately and counted that book's `analyze` child job (both
    stages of a `full` run); reaching any cap stopped the book with
    `budget_limited` and stopped the series.
    """
    max_requests: int | None = Field(description='HTTP attempts allowed in this run.')
    max_input_tokens: int | None = Field(description='Input tokens (reserved or reported) allowed in this run.')
    max_output_tokens: int | None = Field(description='Output tokens (reserved or reported) allowed in this run.')
    budget_usd: float | None = Field(description='Cumulative USD guard across every tracked attempt for the book, including earlier runs.')


class SeriesReviewWait(View):
    """The book a paused series run waits on.

    Decide the book's waiting versions (accept them or set them aside), then
    call `resumeSeriesProcessing`. While paused, that book accepts
    version decisions; every other reservation still holds.
    """
    book_id: str = Field(description='Book waiting for review.')
    child_job_id: str = Field(description='The completed series child job of that book.')
    title: str = Field(description='Book title when the run was queued; empty when unknown.')
    position: float = Field(description='The book\'s reading-order position.')
    steps: list[str] = Field(description='Step IDs whose version from this run is waiting for a decision.')
    since: str = Field(description='When the run paused: ' + TIME)


# ----------------------------------------------------------------------- job
#
# A job is one of eight kinds. Each kind is its own schema (a branch of the ``Job`` union) that declares only the
# fields that mean something for it. ``finalize`` in spec.py writes the branches into ``Job`` inline, so a generated
# client gets one tagged enum with a variant per kind; the branch schemas stay as components for the operations
# that always return one kind (``ListenQueued.job`` is a ``ListenJob``).

class JobBase(View):
    """The fields every job has, whatever its kind."""
    id: str = Field(description='Job ID (32 hex characters).')
    book_id: str = Field(
        description='The book the job works on, or `series:<series id>` for a `series` parent job. '
                    'Pass this value as `book_id` to `GET /api/jobs` to list a book\'s or series\' jobs.')
    status: JobStatus = Field(
        description='`queued` and `running` are active; every other value is terminal. See the lifecycle above.')
    progress: int = Field(description='Units completed so far (kind-specific units).')
    total: int = Field(description='Units planned; 0 when not yet known.')
    message: str = Field(description='Human-readable progress or outcome text. Display it; do not parse it.')
    error: str | None = Field(
        description='Human-readable failure text (at most 1,200 characters, credentials redacted), or null. Set with '
                    '`failed`; the other end states describe themselves in `message`.')
    created_at: str = Field(description='Creation time: ' + TIME)
    updated_at: str = Field(description='Time of the last change: ' + TIME)
    cancel_requested: bool = Field(
        description='True after a cancel request. A running job stops at the next safe boundary; requests already '
                    'sent to a provider can still finish and be billed.')
    resume_after: str | None = Field(
        None, description='Only on a job that ended `quota_limited`: when the daily quota resets (next midnight Pacific '
                          'time), as ' + TIME + ' Absent on every other job.')
    error_code: Literal['content_blocked'] | None = Field(
        None, description='Only on a job that ended `failed` for a documented cause the UI can explain: `content_blocked` is '
                          'Gemini\'s content policy (HTTP 400 with error code `content_blocked`) refusing text that has no '
                          'fallback path (for example a full-cast performance passage or a single-passage `listen` job). '
                          'The provider\'s own error text is never kept: `error` is a fixed sentence. '
                          'Absent on every other job. The set of values is open.')


class RenderJob(JobBase):
    """`render`: enhanced (cast) narration of selected passages. It has no fields of its own.

    `progress` and `total` count passages. It does not record the provider or model.
    """
    kind: Literal['render'] = Field(description='Tag of `Job`: `render`.')


class AnalyzeJob(JobBase):
    """`analyze`: a job of the removed Classic analysis engine. Historical jobs only; every field of its own is optional."""
    kind: Literal['analyze'] = Field(description='Tag of `Job`: `analyze`.')
    provider: AnalysisProvider | None = Field(None, description='The analysis provider (`local`, `gemini`, `openai` or `anthropic`).')
    model: str | None = Field(None, description='The analysis model snapshotted when the job was queued, or null for local analysis.')
    scan_model: str | None = Field(None, description='The preprocessing (scan) model, or null for local analysis.')
    phase: Literal['scan', 'profiles', 'direct', 'full'] | None = Field(None, description='The Classic analysis phase.')
    chapter_id: str | None = Field(None, description='The single chapter analyzed, or null for the whole book.')
    series_id: str | None = Field(None, description='A series child recorded before contract 0.3.0: the series.')
    series_run_id: str | None = Field(None, description='A series child recorded before contract 0.3.0: the parent `series` job ID.')
    position: float | None = Field(None, description='A series child recorded before contract 0.3.0: the book\'s reading-order position.')


class PipelineJob(JobBase):
    """`pipeline`: an analysis pipeline run of one book, or a series run's child job for one of its books.

    `progress` and `total` count analyzer work units. A series child also carries the series fields below; a run
    started from the book has none of them.
    """
    kind: Literal['pipeline'] = Field(description='Tag of `Job`: `pipeline`.')
    run_id: str | None = Field(
        description='The pipeline run this job executes. A series child carries null until the series worker starts '
                    'its book, and keeps null if the book never starts.')
    steps: list[str] = Field(
        description='The requested step IDs with their required upstream steps, deduplicated, in pipeline order.')
    scheduling: Literal['serial', 'parallel'] | None = Field(
        None, description='`serial` (one step at a time) or `parallel` (independent steps together), as requested by the '
                          'run\'s `scheduling` field. Absent on a series child (its book\'s run has it).')
    series_id: str | None = Field(None, description='Series child: the series.')
    series_run_id: str | None = Field(None, description='Series child: the parent `series` job ID.')
    position: float | None = Field(None, description='Series child: the book\'s reading-order position in the series.')
    title: str | None = Field(None, description='Series child: the book title when the run was queued.')
    consent_fingerprint: str | None = Field(
        None, description='Series child: the `consent_fingerprint` confirmed for that book (`SeriesPlanBook`). Before the '
                          'book starts it is recomputed; the book is not run when it differs. It covers the unit set, '
                          'providers, models, `fresh` and step versions, but not the earlier-volume context in '
                          'context-pending prompts.')
    context_pending: list[str] | None = Field(
        None, description='Series child: step IDs whose prompts read earlier books of this run (see '
                          '`SeriesPlanBook.context_pending`). Empty when none.')
    context_sources: list[str] | None = Field(
        None, description='Series child: earlier books of this run whose accepted results this book reads, in no particular '
                          'order. The series pauses after such a book while it has results waiting for review.')
    not_started: bool | None = Field(
        None, description='Series child: true when the child ended without starting, either because the series was '
                          'cancelled (status `cancelled`) or because it stopped at an earlier book, failed or could not '
                          'start (status `interrupted`). Absent otherwise.')
    finished_at: str | None = Field(
        None, description='Series child: set only when the child failed because its book changed after the preview. As ' + TIME)


class SeriesJob(JobBase):
    """`series`: a series run (the parent). One `pipeline` child job runs per supplied book, in reading order.

    `progress` and `total` count books. It can stay `running` while it waits for the owner's review
    (`waiting_for_review`), and continues only when resumed (`resumeSeriesProcessing`) or ends when cancelled.
    A run recorded before contract 0.3.0 has `phase`, `provider`, `model` and `scan_model` instead of the settings
    fields, and `analyze` children.
    """
    kind: Literal['series'] = Field(description='Tag of `Job`: `series`.')
    series_id: str = Field(description='The series.')
    book_ids: list[str] = Field(description='The books processed, in reading order (missing volumes excluded).')
    child_job_ids: list[str] = Field(
        description='One child job per book, in reading order (`pipeline` jobs; `analyze` jobs in runs recorded before '
                    'contract 0.3.0).')
    steps: list[str] | None = Field(
        None, description='The requested step IDs with their required upstream steps, deduplicated, in pipeline order. '
                          'Absent on a run recorded before contract 0.3.0.')
    configs: dict[str, PipelineStepConfigView] | None = Field(
        None, description='`{step ID: {provider, model}}` resolved when the run was queued and applied to every book.')
    gates: dict[str, Literal['auto', 'review']] | None = Field(
        None, description='`{step ID: gate}` resolved when the run was queued (the request\'s `gates`, else the saved '
                          'step setting).')
    scheduling: Literal['serial', 'parallel'] | None = Field(
        None, description='`serial` or `parallel` inside each book\'s run, as requested by the run\'s `scheduling` field.')
    concurrency: int | None = Field(
        None, description='Maximum model requests in flight inside the running book (1–4); books run one at a time. Runs '
                          'recorded before contract 0.3.0: parallel discovery workers (1–2).')
    fresh: bool | None = Field(
        None, description='True when every book requests new samples instead of reusing cached validated units.')
    analysis_limits: PipelineRunLimits | None = Field(
        None, description='The optional caps applied to each book\'s run (every value is null when none were sent). A run '
                          'recorded before contract 0.3.0 holds its analysis allowance in the same fields (see '
                          '`PipelineRunLimits`).')
    estimated_cost_usd: float | None = Field(
        None, description='The confirmed plan\'s `estimated_cost_usd` in USD, or null when any book\'s cost was unknown. '
                          'Approximate; not an invoice.')
    requests: int | None = Field(None, description='The confirmed plan\'s total model requests, before retries or evidence repairs.')
    context_pending_books: list[str] | None = Field(
        None, description='Books whose estimate was "up to" because a step reads earlier books of the run.')
    waiting_for_review: SeriesReviewWait | None = Field(
        description='Set while the run is paused for the owner\'s review of one book (the parent stays `running`). '
                    'Null when it is not paused, after it resumes or ends, and on runs that never paused.')
    finished_at: str | None = Field(
        None, description='When the series worker settled the run (a parent cancelled while queued gains it when its '
                          'worker slot comes up). As ' + TIME)
    phase: Literal['scan', 'profiles', 'direct', 'full'] | None = Field(
        None, description='Runs recorded before contract 0.3.0 only: the Classic analysis phase.')
    provider: AnalysisProvider | None = Field(
        None, description='Runs recorded before contract 0.3.0 only: the analysis provider.')
    model: str | None = Field(None, description='Runs recorded before contract 0.3.0 only: the analysis model.')
    scan_model: str | None = Field(None, description='Runs recorded before contract 0.3.0 only: the preprocessing model.')


class ListenJob(JobBase):
    """`listen`: simple listening to one passage with one narrator. `progress` and `total` are 0 and 1."""
    kind: Literal['listen'] = Field(description='Tag of `Job`: `listen`.')
    session_id: str = Field(description='The narrator session (64 hex).')
    passage_id: str = Field(description='The passage.')
    provider: NarrationProvider = Field(description='The narration provider snapshotted when the job was queued.')
    model: str = Field(description='The speech model snapshotted when the job was queued (`macos-say` for device narration).')
    audio: ListeningAudio | None = Field(
        description='The finished audio (a passage take or a chunk clip), set just before the job completes. Null until '
                    'then and after a failure.')


class ListenChapterJob(JobBase):
    """`listen_chapter`: Gemini chapter listening, or one chapter of a Gemini performance (`parent_id` set).

    `progress` and `total` count the passages ready from the scope start to the chapter end.
    """
    kind: Literal['listen_chapter'] = Field(description='Tag of `Job`: `listen_chapter`.')
    session_id: str = Field(description='The narrator session (64 hex).')
    chapter_id: str = Field(description='The chapter.')
    provider: NarrationProvider = Field(description='The narration provider: `gemini`.')
    model: str = Field(description='The speech model snapshotted when the job was queued.')
    voice: str = Field(description='The Gemini voice.')
    intent: Literal['play', 'queue'] = Field(
        description='`play` (someone is waiting; the first requests are short) or `queue` (prepare ahead; every request '
                    'is full size). Performances use `queue`.')
    scope_start_passage_id: str = Field(
        description='First passage of the prepared range (to the chapter end). Joining at an earlier passage moves it back.')
    focus_passage_id: str = Field(description='The passage the listener is at; generation proceeds from here first.')
    chunking: ChapterListenChunking = Field(description='The chunk settings in use.')
    speech_limits: ChapterListenLimits = Field(
        description='The Gemini speech limits snapshotted for the model when the job was queued.')
    ramp_restart: int = Field(description='Times a `play` join restarted the short first-request ramp.')
    joins: int = Field(description='Times another request joined this job instead of starting one.')
    chunks: list[JobChapterChunk] = Field(description='Every request sent so far, in order, with its outcome.')
    calibration: ChapterListenCalibration = Field(
        description='Speech-rate calibration, carried over from the session\'s previous job and updated as chunks finish.')
    parent_id: str | None = Field(
        description='The parent `performance` job when a performance started this one; null otherwise. Such a job '
                    'cannot be joined by live chapter listening; cancelling the parent cancels it.')
    projection: list[ChapterListenChunkPlan] | None = Field(
        description='The remaining requests planned from the current state; empty when stopping or done. Null until the '
                    'worker first reports.')
    quota: ChapterListenQuota | None = Field(description='Daily quota use at the last report; null until the worker reports.')
    waiting_seconds: float | None = Field(
        description='Seconds the next send waits for the per-minute rate limit, or null when not waiting (and before the '
                    'worker reports).')
    closing: bool | None = Field(
        description='True once the worker decided to finish; a new chapter request then gets 409 until the job ends. Null '
                    'until the worker reports.')
    fallback: JobFallbackNarrator | None = Field(
        description='The free local narrator snapshotted when the job was queued for passages Gemini blocks, or null when '
                    'none was available (and on a job queued before contract 0.3.3).')
    content_blocked: JobContentBlocked | None = Field(
        description='Present once Gemini blocked text of this chapter, with how each blocked passage ended. Null when '
                    'nothing was blocked.')


class VoicePreviewJob(JobBase):
    """`voice_preview`: an explicit voice audition. `progress` and `total` are 0 and 1."""
    kind: Literal['voice_preview'] = Field(description='Tag of `Job`: `voice_preview`.')
    preview_id: str = Field(description='The preview ID.')
    preview: VoicePreview = Field(description='The preview request being rendered.')
    passage_id: str | None = Field(description='The source passage, or null for demo text.')
    provider: NarrationProvider = Field(description='The narration provider snapshotted when the job was queued.')
    model: str = Field(description='The speech model snapshotted when the job was queued.')
    audio: VoicePreviewAudio | None = Field(
        description='The finished audition take, set just before the job completes. Null until then and after a failure.')


class PerformanceJob(JobBase):
    """`performance`: preparation of a saved performance. `progress` and `total` count passages (a Gemini simple
    performance advances only when each chapter's child job settles)."""
    kind: Literal['performance'] = Field(description='Tag of `Job`: `performance`.')
    performance_id: str = Field(description='The saved performance being prepared.')
    mode: Literal['simple', 'cast'] = Field(description='`simple` (one narrator) or `cast` (character voices).')
    provider: NarrationProvider = Field(description='The narration provider snapshotted when the job was queued.')
    model: str = Field(description='The speech model snapshotted when the job was queued.')
    child_job_ids: list[str] = Field(
        description='The `listen_chapter` jobs started so far (Gemini simple performances only; empty otherwise).')
    child_job_id: str | None = Field(description='The `listen_chapter` job currently running, or null between chapters.')
    fallback: JobFallbackNarrator | None = Field(
        None, description='The fallback narrator snapshotted when the job was queued for passages the main narration cannot '
                          'read (blocked or failing), or the narrator of a re-record job; null when none was usable. Absent on '
                          'jobs queued before contract 0.5.1.')


JOB_LIFECYCLE = """\
A background job, as stored and returned by the server: one of eight kinds, selected by `kind`.

**Lifecycle.** A job starts `queued`, becomes `running` when a worker picks
it up, and ends in exactly one terminal status:

- `completed`: the work finished.
- `failed`: an error stopped it; `error` holds the reason. Validated work
  and finished audio are kept.
- `cancelled`: stopped by a cancel request (or never started).
- `interrupted`: the server stopped or restarted while the job was queued
  or running, or (series child) the series run stopped before this book
  started.
- `budget_limited`: an analysis request allowance or dollar budget was
  reached; saved work is kept.
- `quota_limited`: the daily Gemini speech request quota was reached;
  `resume_after` says when it resets.

A `series` parent can also stay `running` while it waits for the owner's
review (`waiting_for_review` is set and no book is running); it continues
only when resumed (`resumeSeriesProcessing`) and ends when cancelled.

**A terminal status is final.** Once a job reaches a terminal status,
its `status`, `message`, `error` and `resume_after` never change again,
whatever happens later (a worker slot coming up for a job cancelled while
queued, or the server shutting down). Other fields may still be updated
as bookkeeping. Resuming work uses the original start route and creates a
new job; old job IDs are never revived. A queued series child that is
cancelled or passed over never starts later.

**Kinds.** Every kind has the fields `id`, `book_id`, `kind`, `status`, `progress`, `total`, `message`, `error`,
`created_at`, `updated_at`, `cancel_requested` (and `resume_after` on a job that ended `quota_limited`, `error_code` on
a job that failed for a documented cause) and only the
fields of its own kind below; a field that a kind does not declare is not sent for it. A route that always returns
one kind names that kind's schema (`ListenJob`, `ListenChapterJob`, `VoicePreviewJob`, `PerformanceJob`, `PipelineJob`,
`SeriesJob`, `RenderJob`), which has the same fields as the branch here.

| kind | started by | own fields |
| --- | --- | --- |
| `render` | enhanced narration | none |
| `analyze` | the removed Classic engine (historical jobs only) | `provider`, `model`, `scan_model`, `phase`, `chapter_id`; a series child recorded before contract 0.3.0 has `series_id`, `series_run_id`, `position` |
| `pipeline` | an analysis pipeline run, or a series run (one child per book) | `run_id`, `steps`, `scheduling`; a series child also `series_id`, `series_run_id`, `position`, `title`, `consent_fingerprint`, `context_pending`, `context_sources`, and in some end states `not_started` or `finished_at` |
| `series` | series processing (the parent) | `series_id`, `book_ids`, `child_job_ids`, the run settings (`steps`, `configs`, `gates`, `scheduling`, `concurrency`, `fresh`, `analysis_limits`, `estimated_cost_usd`, `requests`, `context_pending_books`), `finished_at`, `waiting_for_review` |
| `listen` | simple passage listening | `session_id`, `passage_id`, `provider`, `model`, `audio` |
| `listen_chapter` | chapter listening, or a Gemini performance (with `parent_id`) | `session_id`, `chapter_id`, `provider`, `model`, `voice`, `intent`, `scope_start_passage_id`, `focus_passage_id`, `chunking`, `speech_limits`, `ramp_restart`, `joins`, `chunks`, `calibration`, `parent_id`, `projection`, `quota`, `waiting_seconds`, `closing`, `fallback`, `content_blocked` |
| `voice_preview` | voice preview | `preview_id`, `preview`, `passage_id`, `provider`, `model`, `audio` |
| `performance` | saved performance preparation, or a re-record of some of its passages | `performance_id`, `mode`, `provider`, `model`, `child_job_ids`, `child_job_id`, `fallback` |

**Progress.** `progress` and `total` are counts in kind-specific units, not
a percentage, and `total` may change while running: passages for
`render`, `performance` and `listen_chapter` (passages ready from the
scope start to the chapter end; a Gemini simple `performance` advances
only when each chapter's child job settles); 0/1 for `listen` and
`voice_preview`;
analyzer work units for `analyze` and `pipeline`; books for `series`.

The union is closed: a new job kind is a new member in a new contract version. Clients that only follow a job read
the fields every kind has (`status`, `progress`, `total`, `message`, `error`).
"""

Job = TypeAliasType('Job', Annotated[
    Union[RenderJob, AnalyzeJob, PipelineJob, SeriesJob, ListenJob, ListenChapterJob, VoicePreviewJob, PerformanceJob],
    Field(discriminator='kind', description=JOB_LIFECYCLE)])
