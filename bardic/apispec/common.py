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
    status: Literal['requesting', 'done', 'rate_limited', 'truncated', 'failed'] = Field(
        description='`requesting` while in flight; `done` when its audio was retained; `rate_limited` when the '
                    'provider refused it with HTTP 429 (nothing generated; its passages are planned again); '
                    '`truncated` when the audio was cut short or far too short and was discarded; `failed` on any '
                    'other error (the job then stops).')
    started_at: str = Field(description='When the request was sent: ' + TIME)
    finished_at: str | None = Field(None, description='When the request finished; absent while `requesting`. ' + TIME)
    error: str | None = Field(None, description='Human-readable reason for `rate_limited`, `truncated` or `failed` '
                                                '(at most 300 characters for `failed`).')
    duration: float | None = Field(None, description='Seconds of audio received (`done`, `truncated`).')
    chunk_id: str | None = Field(None, description='ID of the retained chunk audio (`done`).')
    latency: float | None = Field(None, description='Measured seconds from send to response (`done`).')
    flags: list[Literal['weak_alignment']] | None = Field(
        None, description='Quality flags (`done`). `weak_alignment`: fewer than 60% of passage boundaries matched, '
                          'so passage clip times are rough.')
    matched: int | None = Field(None, description='Passage boundaries the aligner matched in the audio (`done`).')
    boundaries: int | None = Field(None, description='Passage boundaries the aligner tried to match (`done`).')


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

    Treat the first terminal status you observe as final. The server may still
    rewrite `message` afterwards (a job cancelled while queued is settled
    again when its worker slot comes up, and in a shutdown race that can turn
    `cancelled` into `interrupted`). Resuming work uses the original start
    route and creates a new job; old job IDs are never revived. A queued
    series child that is cancelled or passed over never starts later.

    **Kinds and their extra fields** (a field not listed for a kind is absent):

    | kind | started by | extra fields |
    | --- | --- | --- |
    | `render` | enhanced narration | none |
    | `analyze` | the removed Classic engine (historical jobs only) | `provider`, `model`, `scan_model`, `phase`, `chapter_id`; a series child recorded before contract 0.2.0 has `series_id`, `series_run_id`, `position` instead of `chapter_id` |
    | `pipeline` | analysis pipeline run, or a series run (one child per book) | from the book: `run_id`, `steps`, `mode`; series child: `run_id` (null until its book starts), `steps`, `series_id`, `series_run_id`, `position`, `title`, `plan_fingerprint`, and in some end states `not_started` or `finished_at` |
    | `series` | series processing (parent) | `series_id`, `steps`, `configs`, `gates`, `mode`, `concurrency`, `fresh`, `limits` (PipelineRunLimits), `book_ids`, `child_job_ids`, `plan_fingerprint`, `estimated_cost_usd`, `requests`, `finished_at`. A run recorded before contract 0.2.0 has `phase`, `provider`, `model`, `scan_model`, `concurrency`, `limits` (SeriesJobLimits), `book_ids`, `child_job_ids`, `plan_fingerprint` and `finished_at`, with `analyze` children |
    | `listen` | simple passage listening | `session_id`, `segment_id`, `provider`, `model`, `phase`, and `audio` once ready |
    | `listen_chapter` | chapter listening, or a Gemini performance (with `parent_id`) | `session_id`, `chapter_id`, `provider`, `model`, `voice`, `intent`, `scope_start_segment_id`, `focus_segment_id`, `chunking`, `limits` (speech), `ramp_restart`, `joins`, `phase`, `chunks`, `calibration`; once the worker reports: `projection`, `quota`, `waiting_seconds`, `closing` |
    | `voice_preview` | voice preview | `preview_id`, `preview`, `segment_id`, `provider`, `model`, `phase`, and `audio` once ready |
    | `performance` | saved performance preparation | `performance_id`, `mode`, `provider`, `model`, `phase`, `child_job_ids`, `child_job_id` |

    `resume_after` appears on any job that ended `quota_limited`.

    **Progress.** `progress` and `total` are counts in kind-specific units, not
    a percentage, and `total` may change while running: passages for
    `render`, `performance` and `listen_chapter` (passages ready from the
    scope start to the chapter end); 0/1 for `listen` and `voice_preview`;
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
        None, description='Analysis provider (`analyze`, and `series` runs recorded before contract 0.2.0: local, '
                          'gemini, openai, anthropic) or narration provider '
                          '(`listen`, `voice_preview`, `performance`: system, gemini, breeze; `listen_chapter`: gemini).')
    model: str | None = Field(
        None, description='Model snapshotted when the job was queued: the analysis model (null for local analysis) or '
                          'the speech model (`macos-say` for device narration). `render` jobs do not record it.')
    scan_model: str | None = Field(
        None, description='Preprocessing (scan) model for `analyze` (and `series` runs recorded before contract 0.2.0); '
                          'null for local analysis.')
    phase: Literal['scan', 'profiles', 'direct', 'full', 'simple_listen', 'chapter_listen', 'voice_preview', 'performance'] | None = Field(
        None, description='`analyze` (historical) and `series` runs recorded before contract 0.2.0: the Classic analysis phase. '
                          'Narration kinds carry a fixed label: `simple_listen`, `chapter_listen`, `voice_preview`, '
                          '`performance`.')
    mode: Literal['serial', 'parallel', 'simple', 'cast'] | None = Field(
        None, description='`pipeline` started from the book, and `series` (inside each book\'s run): `serial` or '
                          '`parallel` step scheduling. `performance`: `simple` (one narrator) or `cast` (character '
                          'voices).')
    chapter_id: str | None = Field(
        None, description='`analyze`: the single chapter analyzed, or null for the whole book. `listen_chapter`: the chapter.')
    segment_id: str | None = Field(
        None, description='`listen`: the passage. `voice_preview`: the source passage, or null for demo text.')
    session_id: str | None = Field(None, description='`listen`, `listen_chapter`: the narrator session (64 hex).')
    audio: ListeningAudio | VoicePreviewAudio | None = Field(
        None, description='The finished audio, set just before a `listen` job (a passage take or chunk clip) or a '
                          '`voice_preview` job (VoicePreviewAudio) completes; it may carry `cache_hit` when retained '
                          'audio was found by the worker. Absent until then and after a failure.')
    resume_after: str | None = Field(
        None, description='`quota_limited` only: when the daily quota resets (next midnight Pacific time), as ' + TIME)

    # pipeline
    run_id: str | None = Field(
        None, description='`pipeline`: the pipeline run this job executes. A series child carries null until the '
                          'series worker starts its book, and keeps null if the book never starts.')
    steps: list[str] | None = Field(
        None, description='`pipeline` and `series`: the requested step IDs, deduplicated, in pipeline order.')

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
                          'recorded before contract 0.2.0). `performance`: the `listen_chapter` jobs started so far '
                          '(Gemini simple performances only; empty otherwise).')
    concurrency: int | None = Field(
        None, description='`series`: maximum model requests in flight inside the running book (1–4); books run one at a '
                          'time. Runs recorded before contract 0.2.0: parallel discovery workers (1–2).')
    configs: dict[str, PipelineStepConfigView] | None = Field(
        None, description='`series`: `{step ID: {provider, model}}` resolved when the run was queued and applied to '
                          'every book.')
    gates: dict[str, Literal['auto', 'review']] | None = Field(
        None, description='`series`: `{step ID: gate}` resolved when the run was queued (the request\'s `gates`, else '
                          'the saved step setting).')
    fresh: bool | None = Field(
        None, description='`series`: true when every book requests new samples instead of reusing cached validated units.')
    limits: PipelineRunLimits | SeriesJobLimits | ChapterListenLimits | None = Field(
        None, description='`series`: the optional caps applied to each book\'s run (PipelineRunLimits; every value is '
                          'null when none were sent), or the analysis allowance of a run recorded before contract 0.2.0 '
                          '(SeriesJobLimits). `listen_chapter`: the Gemini speech limits snapshotted for the model '
                          '(ChapterListenLimits).')
    plan_fingerprint: str | None = Field(
        None, description='`series`: the series plan `fingerprint` this run was confirmed against. Series child '
                          '(`pipeline`): the book plan `fingerprint` confirmed for that book; the book is not run when '
                          'its recomputed plan differs.')
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


class SeriesJobLimits(View):
    """The analysis allowance of a series run recorded before contract 0.2.0, applied to each book separately.

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
