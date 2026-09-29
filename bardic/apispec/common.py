"""Views shared by several route families.

Owned by the System/Jobs family. Other families import from here and do not
edit it; report missing fields to the owner instead.

``Job`` is the durable job document embedded by every job-starting route and
listed by ``GET /api/jobs``. Its kind-specific parts reuse the audio, voice-preview
and chapter-plan views in ``media`` (import order: base -> media -> common ->
route families).
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import View
from .media import (ChapterListenCalibration, ChapterListenChunking, ChapterListenChunkPlan, ChapterListenLimits,
                    ChapterListenQuota, ListeningAudio, VoicePreview, VoicePreviewAudio)

TIME = 'ISO 8601 UTC timestamp with offset, for example `2026-09-28T17:04:05.123456+00:00`.'

JobKind = Literal['render', 'analyze', 'pipeline', 'series', 'listen', 'listen_chapter', 'voice_preview', 'performance']
JobStatus = Literal['queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted', 'budget_limited', 'quota_limited']


class JobChapterChunk(View):
    """One chunk request a ``listen_chapter`` job has sent (or is sending), in send order."""
    n: int = Field(description='1-based send order within the job.')
    first_segment_id: str = Field(description='First passage in the chunk.')
    last_segment_id: str = Field(description='Last passage in the chunk.')
    segment_count: int = Field(description='Consecutive passages in the chunk.')
    chars: int = Field(description='Code points of the exact chapter slice sent.')
    target_seconds: float = Field(description='Audio length in seconds the chunk was sized for.')
    expected_seconds: float = Field(description='Audio length in seconds expected from the speech-rate estimate at send time.')
    expected_latency: float = Field(description='Expected seconds until the response, from calibration.')
    realtime_factor: float = Field(description='Calibration realtime factor (audio seconds per waiting second) at send time.')
    epoch: int = Field(description='Planning generation; increases after a truncation forces smaller re-planning.')
    status: Literal['requesting', 'done', 'rate_limited', 'truncated', 'blocked', 'failed'] = Field(
        description='`requesting` while in flight; `done` when its audio was retained; `rate_limited` when the '
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
    """The free local narrator a job snapshotted for passages Gemini blocks."""
    session_id: str = Field(description='The narrator session (64 hex) whose takes read the blocked passages.')
    provider: Literal['system', 'breeze'] = Field(description='`system` (a device voice) or `breeze`.')
    model: str = Field(description='Speech model of the fallback narrator (`macos-say` or the Breeze model).')
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
    provider: str = Field(description='Provider ID the run uses for this step: `local` for plain steps, otherwise a '
                                      'pipeline provider ID.')
    model: str | None = Field(description='Model ID the run sends, or null for plain steps and service providers.')


class PipelineRunLimits(View):
    """Caps a run was started with; null means uncapped."""
    max_requests: int | None = Field(description='HTTP attempts allowed in this run.')
    max_input_tokens: int | None = Field(description='Input tokens (reserved or reported) allowed in this run.')
    max_output_tokens: int | None = Field(description='Output tokens (reserved or reported) allowed in this run.')
    budget_usd: float | None = Field(description='Cumulative USD guard across every tracked attempt for the book, including earlier runs.')


# ----------------------------------------------------------------------- job

class Job(View):
    """A durable background job, as stored and returned by the server.

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

    **Kinds and their extra fields** (a field not listed for a kind is absent):

    | kind | started by | extra fields |
    | --- | --- | --- |
    | `render` | enhanced narration | none |
    | `analyze` | the removed Classic engine (historical jobs only) | `provider`, `model`, `scan_model`, `phase`, `chapter_id`; a series child recorded before contract 0.3.0 has `series_id`, `series_run_id`, `position` instead of `chapter_id` |
    | `pipeline` | analysis pipeline run, or a series run (one child per book) | from the book: `run_id`, `steps`, `scheduling`; series child: `run_id` (null until its book starts), `steps`, `series_id`, `series_run_id`, `position`, `title`, `consent_fingerprint`, `context_pending`, `context_sources`, and in some end states `not_started` or `finished_at` |
    | `series` | series processing (parent) | `series_id`, `steps`, `configs`, `gates`, `scheduling`, `concurrency`, `fresh`, `analysis_limits` (PipelineRunLimits), `book_ids`, `child_job_ids`, `estimated_cost_usd`, `requests`, `context_pending_books`, `finished_at`, and `waiting_for_review` once it has paused. A run recorded before contract 0.3.0 has `phase`, `provider`, `model`, `scan_model`, `concurrency`, `analysis_limits` (SeriesJobLimits), `book_ids`, `child_job_ids` and `finished_at`, with `analyze` children |
    | `listen` | simple passage listening | `session_id`, `segment_id`, `provider`, `model`, `phase`, and `audio` once ready |
    | `listen_chapter` | chapter listening, or a Gemini performance (with `parent_id`) | `session_id`, `chapter_id`, `provider`, `model`, `voice`, `intent`, `scope_start_segment_id`, `focus_segment_id`, `chunking`, `speech_limits`, `ramp_restart`, `joins`, `phase`, `chunks`, `calibration`, `fallback`; once the worker reports: `projection`, `quota`, `waiting_seconds`, `closing`; once Gemini blocked text: `content_blocked` |
    | `voice_preview` | voice preview | `preview_id`, `preview`, `segment_id`, `provider`, `model`, `phase`, and `audio` once ready |
    | `performance` | saved performance preparation | `performance_id`, `mode`, `provider`, `model`, `phase`, `child_job_ids`, `child_job_id` |

    `resume_after` appears on any job that ended `quota_limited`.

    **Progress.** `progress` and `total` are counts in kind-specific units, not
    a percentage, and `total` may change while running: passages for
    `render`, `performance` and `listen_chapter` (passages ready from the
    scope start to the chapter end; a Gemini simple `performance` advances
    only when each chapter's child job settles); 0/1 for `listen` and
    `voice_preview`;
    analyzer work units for `analyze` and `pipeline`; books for `series`.
    """
    id: str = Field(description='Job ID (32 hex characters).')
    book_id: str = Field(
        description='The book the job works on, or `series:<series id>` for a `series` parent job. '
                    'Pass this value as `book_id` to `GET /api/jobs` to list a book\'s or series\' jobs.')
    kind: JobKind = Field(description='What the job does; see the table above.')
    status: JobStatus = Field(
        description='`queued` and `running` are active; every other value is terminal. See the lifecycle above.')
    progress: int = Field(description='Units completed so far (kind-specific units).')
    total: int = Field(description='Units planned; 0 when not yet known.')
    message: str = Field(description='Human-readable progress or outcome text. Display it; do not parse it.')
    error: str | None = Field(
        description='Human-readable failure text (at most 1,200 characters, credentials redacted), or null. Usually '
                    'set with `failed`; a series child that stopped also carries it with `budget_limited` or `cancelled`.')
    created_at: str = Field(description='Creation time: ' + TIME)
    updated_at: str = Field(description='Time of the last change: ' + TIME)
    cancel_requested: bool = Field(
        description='True after a cancel request. A running job stops at the next safe boundary; requests already '
                    'sent to a provider can still finish and be billed.')

    # Shared optional fields
    provider: Literal['local', 'gemini', 'openai', 'anthropic', 'system', 'breeze'] | None = Field(
        None, description='Analysis provider (`analyze`, and `series` runs recorded before contract 0.3.0: local, '
                          'gemini, openai, anthropic) or narration provider '
                          '(`listen`, `voice_preview`, `performance`: system, gemini, breeze; `listen_chapter`: gemini).')
    model: str | None = Field(
        None, description='Model snapshotted when the job was queued: the analysis model (null for local analysis) or '
                          'the speech model (`macos-say` for device narration). `render` jobs do not record it.')
    scan_model: str | None = Field(
        None, description='Preprocessing (scan) model for `analyze` (and `series` runs recorded before contract 0.3.0); '
                          'null for local analysis.')
    phase: Literal['scan', 'profiles', 'direct', 'full', 'simple_listen', 'chapter_listen', 'voice_preview', 'performance'] | None = Field(
        None, description='`analyze` (historical) and `series` runs recorded before contract 0.3.0: the Classic analysis '
                          'phase. Narration kinds carry a fixed label: `simple_listen`, `chapter_listen`, '
                          '`voice_preview`, `performance`.')
    mode: Literal['simple', 'cast'] | None = Field(
        None, description='`performance` only: `simple` (one narrator) or `cast` (character voices).')
    chapter_id: str | None = Field(
        None, description='`analyze`: the single chapter analyzed, or null for the whole book. `listen_chapter`: the chapter.')
    segment_id: str | None = Field(
        None, description='`listen`: the passage. `voice_preview`: the source passage, or null for demo text.')
    session_id: str | None = Field(None, description='`listen`, `listen_chapter`: the narrator session (64 hex).')
    audio: ListeningAudio | VoicePreviewAudio | None = Field(
        None, description='The finished audio, set just before a `listen` job (a passage take or chunk clip) or a '
                          '`voice_preview` job (VoicePreviewAudio) completes. Absent until then and after a failure.')
    resume_after: str | None = Field(
        None, description='`quota_limited` only: when the daily quota resets (next midnight Pacific time), as ' + TIME)
    error_code: Literal['content_blocked'] | None = Field(
        None, description='`failed` only, when `error` has a documented cause the UI can explain: `content_blocked` is '
                          'Gemini\'s content policy (HTTP 400 with error code `content_blocked`) refusing text that '
                          'has no fallback path (a full-cast performance passage or a single-passage `listen` job). '
                          'The provider\'s own error text is never kept: `error` is a fixed sentence. Absent otherwise. '
                          'The set of values is open.')

    # pipeline
    run_id: str | None = Field(
        None, description='`pipeline`: the pipeline run this job executes. A series child carries null until the '
                          'series worker starts its book, and keeps null if the book never starts.')
    steps: list[str] | None = Field(
        None, description='`pipeline` and `series`: the requested step IDs with their required upstream steps, '
                          'deduplicated, in pipeline order.')
    scheduling: Literal['serial', 'parallel'] | None = Field(
        None, description='`pipeline` and `series` (inside each book\'s run): `serial` (one step at a time) or '
                          '`parallel` (independent steps together), as requested by the run\'s `scheduling` field.')

    # series parent and children
    series_id: str | None = Field(None, description='`series` parent and its children: the series.')
    series_run_id: str | None = Field(None, description='Series child: the parent `series` job ID.')
    position: float | None = Field(None, description='Series child: the book\'s reading-order position in the series.')
    title: str | None = Field(None, description='Series child (`pipeline`): the book title when the run was queued.')
    not_started: bool | None = Field(
        None, description='Series child (`pipeline`): true when the child ended without starting, either because the '
                          'series was cancelled (status `cancelled`) or because it stopped at an earlier book, failed or '
                          'could not start (status `interrupted`). Absent otherwise.')
    book_ids: list[str] | None = Field(None, description='`series`: the books processed, in reading order (missing volumes excluded).')
    child_job_ids: list[str] | None = Field(
        None, description='`series`: one child job per book, in reading order (`pipeline` jobs; `analyze` jobs in runs '
                          'recorded before contract 0.3.0). `performance`: the `listen_chapter` jobs started so far '
                          '(Gemini simple performances only; empty otherwise).')
    concurrency: int | None = Field(
        None, description='`series`: maximum model requests in flight inside the running book (1–4); books run one at a '
                          'time. Runs recorded before contract 0.3.0: parallel discovery workers (1–2).')
    configs: dict[str, PipelineStepConfigView] | None = Field(
        None, description='`series`: `{step ID: {provider, model}}` resolved when the run was queued and applied to '
                          'every book.')
    gates: dict[str, Literal['auto', 'review']] | None = Field(
        None, description='`series`: `{step ID: gate}` resolved when the run was queued (the request\'s `gates`, else '
                          'the saved step setting).')
    fresh: bool | None = Field(
        None, description='`series`: true when every book requests new samples instead of reusing cached validated units.')
    analysis_limits: PipelineRunLimits | SeriesJobLimits | None = Field(
        None, description='`series` only: the optional caps applied to each book\'s run (PipelineRunLimits; every value '
                          'is null when none were sent), or the analysis allowance of a run recorded before contract '
                          '0.3.0 (SeriesJobLimits).')
    consent_fingerprint: str | None = Field(
        None, description='Series child (`pipeline`): the `consent_fingerprint` confirmed for that book '
                          '(`SeriesPlanBook`). Before the book starts it is recomputed; the book is not run when it '
                          'differs. It covers the unit set, providers, models, `fresh` and step versions, but not '
                          'the earlier-volume context in context-pending prompts.')
    context_pending: list[str] | None = Field(
        None, description='Series child (`pipeline`): step IDs whose prompts read earlier books of this run '
                          '(see `SeriesPlanBook.context_pending`). Empty when none.')
    context_sources: list[str] | None = Field(
        None, description='Series child (`pipeline`): earlier books of this run whose accepted results this book '
                          'reads, in no particular order. The series pauses after such a book while it has results '
                          'waiting for review.')
    context_pending_books: list[str] | None = Field(
        None, description='`series`: books whose estimate was "up to" because a step reads earlier books of the run.')
    waiting_for_review: SeriesReviewWait | None = Field(
        None, description='`series`: set while the run is paused for the owner\'s review of one book (the parent '
                          'stays `running`). Null after it resumes or ends. Absent on runs that never paused.')
    estimated_cost_usd: float | None = Field(
        None, description='`series`: the confirmed plan\'s `estimated_cost_usd` in USD, or null when any book\'s cost '
                          'was unknown. Approximate; not an invoice.')
    requests: int | None = Field(
        None, description='`series`: the confirmed plan\'s total model requests, before retries or evidence repairs.')
    finished_at: str | None = Field(
        None, description='`series`: when the series worker settled the run (a parent cancelled while queued gains it '
                          'when its worker slot comes up). Series child: set only when the child failed because its '
                          'book changed after the preview. As ' + TIME)

    # performance
    performance_id: str | None = Field(None, description='`performance`: the saved performance being prepared.')
    child_job_id: str | None = Field(
        None, description='`performance`: the `listen_chapter` job currently running, or null between chapters.')
    parent_id: str | None = Field(
        None, description='`listen_chapter` started by a performance: the parent `performance` job. Such a job cannot be '
                          'joined by live chapter listening; cancelling the parent cancels it.')

    # voice preview
    preview_id: str | None = Field(None, description='`voice_preview`: the preview ID.')
    preview: VoicePreview | None = Field(None, description='`voice_preview`: the preview request being rendered.')

    # chapter listening
    voice: str | None = Field(None, description='`listen_chapter`: the Gemini voice.')
    intent: Literal['play', 'queue'] | None = Field(
        None, description='`listen_chapter`: `play` (someone is waiting; the first requests are short) or `queue` '
                          '(prepare ahead; every request is full size). Performances use `queue`.')
    scope_start_segment_id: str | None = Field(
        None, description='`listen_chapter`: first passage of the prepared range (to the chapter end). Joining at an '
                          'earlier passage moves it back.')
    focus_segment_id: str | None = Field(
        None, description='`listen_chapter`: the passage the listener is at; generation proceeds from here first.')
    chunking: ChapterListenChunking | None = Field(None, description='`listen_chapter`: the chunk settings in use.')
    speech_limits: ChapterListenLimits | None = Field(
        None, description='`listen_chapter` only: the Gemini speech limits snapshotted for the model when the job was queued.')
    ramp_restart: int | None = Field(
        None, description='`listen_chapter`: times a `play` join restarted the short first-request ramp.')
    joins: int | None = Field(None, description='`listen_chapter`: times another request joined this job instead of starting one.')
    chunks: list[JobChapterChunk] | None = Field(
        None, description='`listen_chapter`: every request sent so far, in order, with its outcome.')
    calibration: ChapterListenCalibration | None = Field(
        None, description='`listen_chapter`: speech-rate calibration, carried over from the session\'s previous job and '
                          'updated as chunks finish.')
    projection: list[ChapterListenChunkPlan] | None = Field(
        None, description='`listen_chapter`: the remaining requests planned from the current state; empty when stopping '
                          'or done. Absent until the worker first reports.')
    quota: ChapterListenQuota | None = Field(None, description='`listen_chapter`: daily quota use at the last report.')
    waiting_seconds: float | None = Field(
        None, description='`listen_chapter`: seconds the next send waits for the per-minute rate limit, or null when not waiting.')
    closing: bool | None = Field(
        None, description='`listen_chapter`: true once the worker decided to finish; a new chapter request then gets 409 '
                          'until the job ends.')
    fallback: JobFallbackNarrator | None = Field(
        None, description='`listen_chapter`: the free local narrator snapshotted when the job was queued for passages '
                          'Gemini blocks, or null when none was available. Absent on jobs queued before contract 0.3.3.')
    content_blocked: JobContentBlocked | None = Field(
        None, description='`listen_chapter`: present once Gemini blocked text of this chapter, with how each blocked '
                          'passage ended. Absent when nothing was blocked.')


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


class SeriesJobLimits(View):
    """The analysis allowance of a series run recorded before contract 0.3.0, applied to each book separately.

    Newer series runs record `PipelineRunLimits` instead. Request and token
    caps counted that book's `analyze` child job (both of its stages in a
    `full` run). The dollar ceiling counted every tracked attempt for the
    book, including earlier runs. Reaching any cap stopped the book with
    `budget_limited` and stopped the series.
    """
    max_requests: int = Field(description='Maximum provider requests (1–1000).')
    max_input_tokens: int = Field(description='Maximum input tokens (1,000–10,000,000).')
    max_output_tokens: int = Field(description='Maximum output tokens (1,000–2,000,000).')
    budget_usd: float | None = Field(description='Estimated USD ceiling (above 0, at most 1000), or null for no dollar cap.')



# Job refers to views defined after it in this module.
Job.model_rebuild()
