"""Contract entries for the inspection route family.

Two tags live here: 'Classic analysis' (the older phase-based analysis engine:
status, plan preview, start, and its free local preprocessing) and
'Inspection' (read-only views of stages, retained artifacts, the story map,
passage search, resource usage and the portable analysis export).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .base import Op, View, op
from .common import Job

BOOK_ID = 'Book ID, from the library or the import response.'
BOOK_404 = {'book_not_found': 'No book has this ID.'}
UNKNOWN_CHAPTER = 'The body\'s `chapter_id` is not a chapter of this book.'
UNKNOWN_PROVIDER = 'The provider (from the body, or the saved default) is not `local`, `gemini`, `openai` or `anthropic`.'
READ_ONLY = ('This GET creates and changes no domain records: no artifacts, decisions, resource-ledger rows, jobs or '
             'book changes.')


# --------------------------------------------------------------- classic analysis

class AnalysisChapterProgress(View):
    """Per-chapter progress row of the classic analysis checkpoint.

    Rows are created for every chapter of the book when a checkpoint is first
    written; chapters outside the requested scope keep their earlier state.
    Rows written by a newer cloud run start with only ``id``, ``title``,
    ``status``, ``discovery_complete`` and ``directing_complete``; the other
    fields appear once the run reaches that chapter.
    """
    id: str = Field(description='Chapter ID.')
    title: str | None = Field(default=None, description='Chapter title at the time the row was written.')
    stage: Literal['discovery', 'directing', 'complete'] | None = Field(
        default=None, description='Last stage this chapter reached. Absent until a run works on the chapter.')
    status: Literal['pending', 'running', 'completed', 'failed', 'interrupted', 'budget_limited'] = Field(
        description='State of the chapter within its current stage. `interrupted` also covers a user cancellation and '
                    'a server restart; `budget_limited` means a request/token/dollar allowance stopped the run.')
    completed_units: int | None = Field(default=None, description='Requests (units) finished for this chapter in its current stage.')
    total_units: int | None = Field(default=None, description='Requests (units) planned for this chapter in its current stage.')
    error: str | None = Field(default=None, description='Human-readable failure text for this chapter, or null. Display only.')
    discovery_complete: bool | None = Field(
        default=None, description='True when character discovery covers this chapter (for newer cloud runs: validated '
                                  'discovery covers the whole chapter text).')
    directing_complete: bool | None = Field(
        default=None, description='True when passage direction for this chapter is complete and still matches the current cast.')


class AnalysisStatus(View):
    """Checkpoint summary of the classic analysis engine for one book.

    When no checkpoint exists the server synthesizes `status: "not_started"`
    with one pending row per chapter. A checkpoint is written by
    `POST /api/books/{book_id}/analyze` (both the local draft and the cloud
    phases) and by the per-book `analyze` children of a series run. It
    survives failures, so it can describe an older run than the latest job.
    Source text and model responses are never included.
    """
    provider: str | None = Field(description='Analysis provider of the checkpoint: `local`, `gemini`, `openai` or `anthropic`; null when not started.')
    model: str | None = Field(description='Detailed-analysis model ID used, or null (local drafts and not started).')
    status: Literal['not_started', 'running', 'completed', 'failed', 'interrupted', 'budget_limited'] = Field(
        description='Overall state. `interrupted` covers cancellation and server restarts; `budget_limited` means a '
                    'request/token/dollar allowance stopped the run. Validated work is kept in every case.')
    stage: Literal['discovery', 'preprocessing', 'profiles', 'directing', 'complete'] | None = Field(
        default=None, description='Stage in progress or last reached. `preprocessing` is the free local census at the start '
                                  'of a cloud run; `complete` after success.')
    phase: Literal['scan', 'profiles', 'direct', 'full'] | None = Field(
        default=None, description='Requested cloud phase. Absent for local drafts and for checkpoints written before phases existed.')
    scan_model: str | None = Field(default=None, description='Fast model used for discovery in cloud runs. Absent for local drafts.')
    completed_units: int | None = Field(default=None, description='Units (requests or batches) completed in the latest run. '
                                                                  'Absent only if a run failed before its first save.')
    total_units: int | None = Field(default=None, description='Units known so far for the latest run. A `full` run discovers more '
                                                              'work as it goes, so this can grow.')
    current_chapter_id: str | None = Field(description='Chapter being worked on, or null between chapters, during profiles and when idle.')
    scope_chapter_id: str | None = Field(default=None, description='The `chapter_id` the run was limited to, or null for the whole book. '
                                                                   'Absent when not started.')
    error: str | None = Field(default=None, description='Human-readable failure text of the latest run, or null. Display only.')
    updated_at: str | None = Field(default=None, description='ISO 8601 UTC time the checkpoint was last saved. Absent when not started.')
    chapters: list[AnalysisChapterProgress] = Field(description='One row per chapter of the book (as of the checkpoint).')


class CensusChapter(View):
    """Local census statistics for one chapter (section) of the book."""
    id: str = Field(description='Chapter ID.')
    title: str = Field(description='Chapter title as stored in the book.')
    kind: str = Field(description='Section type: `chapter`, `recap`, `section`, `front_matter` or `back_matter` '
                                  '(older books may use other values; `section` when unrecorded).')
    eligible: bool = Field(description='False for front and back matter, which cloud discovery and direction skip.')
    words: int = Field(description='Whitespace-separated word count of the chapter text.')
    estimated_tokens: int = Field(description='Rough token estimate (UTF-8 bytes / 3), for sizing only.')
    passages: int = Field(description='Number of passages (segments) in the chapter.')
    dialogue_turns: int = Field(description='Number of dialogue passages.')
    unassigned_dialogue: int = Field(description='Dialogue passages whose speaker is `unassigned`.')


class CensusCharacter(View):
    """Heuristic effort signals for one known or candidate character.

    These count names and speech tags; they do not prove identity, presence
    or narrative importance.
    """
    id: str = Field(description='Book-local character ID for a known character, or `candidate_<hash>` for a name found only '
                                'in speech tags.')
    name: str = Field(description='Cast name for a known character; for a candidate, the name as it appeared in the first matching speech tag.')
    aliases: list[str] = Field(description='Cast aliases of a known character (possibly empty); always empty for a speech-tag candidate.')
    known_character: bool = Field(description='True for a character already in the book cast; false for a speech-tag candidate.')
    mentions: int = Field(description='Whole-word name/alias matches in eligible chapters (only unambiguous names are counted).')
    explicit_speech_tags: int = Field(description='Speech tags such as `said Anna` naming this character.')
    dialogue_turns: int = Field(description='Dialogue passages currently attributed to this character.')
    dialogue_words: int = Field(description='Words in those dialogue passages.')
    chapter_count: int = Field(description='Eligible chapters with at least one mention.')
    chapter_mentions: dict[str, int] = Field(description='Mention count by chapter ID (chapters with none are omitted).')
    uncertain_attributions: int = Field(description='Attributed dialogue passages with missing or below-0.8 confidence.')
    ambiguous_aliases: int = Field(description='Names/aliases shared with another character.')
    priority_score: int = Field(description='Heuristic score combining mentions, tags, dialogue and spread. Higher means more effort.')
    priority: Literal['deep', 'standard', 'basic'] = Field(description='Effort tier derived from the score and ambiguity.')
    recommended_evidence_limit: int = Field(description='Evidence quotations a profile request uses for this tier (16, 10 or 5).')


class AnalysisCensus(View):
    """Free, rules-based whole-book census. Cached per book input; recomputed when the text, structure, cast or
    attributions change. Analysis runs and plan previews also retain it as a `census` artifact."""
    version: int = Field(description='Census algorithm version (currently 1).')
    local_complete: bool = Field(description='Always true: the local census covers every chapter.')
    local_chapters_scanned: int = Field(description='Chapters scanned (all chapters, including front/back matter).')
    eligible_chapter_ids: list[str] = Field(description='Chapters eligible for cloud discovery and direction, in book order.')
    eligible_chapters: int = Field(description='Count of eligible chapters.')
    chapters: list[CensusChapter] = Field(description='One row per chapter of the book in book order, including front and back matter '
                                                        '(see `eligible`).')
    characters: list[CensusCharacter] = Field(description='Known characters and speech-tag candidates (excluding `narrator` and '
                                                          '`unassigned`), highest priority first.')
    words: int = Field(description='Total words across all chapters.')
    estimated_source_tokens: int = Field(description='Rough token estimate for eligible chapters only.')
    note: str = Field(description='Interpretation caveat. Display only.')


class AnalysisUsage(View):
    """Tracked analysis request usage for a book, across all runs (classic and step pipeline)."""
    attempts: int = Field(description='Recorded analysis HTTP attempts, including failed and repair attempts.')
    input_tokens: int = Field(description='Sum of reported input tokens. Attempts without reported usage add 0 here; see '
                                          '`unknown_usage_attempts`.')
    output_tokens: int = Field(description='Sum of reported output tokens (same caveat).')
    estimated_spend_usd: float = Field(description='Sum of the conservative per-attempt estimates in USD (dated prices, guard '
                                                   'uplift, reservations when usage is missing). Not an invoice.')
    unknown_cost_attempts: int = Field(description='Attempts whose cost is unknown. Unknown is not zero.')
    unknown_usage_attempts: int = Field(description='Attempts missing input or output token counts.')
    note: str = Field(description='Interpretation caveat. Display only.')


class ProfileFreshness(View):
    """Currency of one character's vocal profile."""
    character_id: str = Field(description='Book-local character ID.')
    state: Literal['reviewed', 'current', 'stale', 'draft'] = Field(
        description='`reviewed`: manually edited (authoritative). `current`: refined from the current evidence set. '
                    '`stale`: refined earlier but the evidence or settings changed. `draft`: never refined.')
    provisional: bool = Field(description='True when the profile may still change: the whole book is not yet discovered, '
                                          'or the state is neither `current` nor `reviewed`.')


class AnalysisCoverage(View):
    """Local census, semantic discovery coverage, tracked usage and profile freshness.

    Whole-book discovery coverage and profile freshness are separate facts.
    Only validated cloud discovery counts as semantic coverage; local drafts do not.
    """
    local: AnalysisCensus = Field(description='The free local census.')
    semantic_chapters_complete: int = Field(description='Eligible chapters whose full text is covered by validated cloud discovery.')
    semantic_chapter_ids: list[str] = Field(description='Those chapter IDs, in book order.')
    eligible_chapters: int = Field(description='Eligible chapters (same as `local.eligible_chapters`).')
    whole_book_discovered: bool = Field(description='True when every eligible chapter is covered (false for a book with none).')
    profiles_provisional: bool = Field(description='True unless the whole book is discovered and every profile is current or reviewed.')
    usage: AnalysisUsage
    note: str = Field(description='Interpretation caveat. Display only.')
    characters: list[ProfileFreshness] = Field(description='Profile freshness per cast member, excluding `narrator` and `unassigned`.')
    profiles_current: int = Field(description='Profiles whose state is `current` or `reviewed`.')
    profiles_total: int = Field(description='Profiles considered (cast members excluding `narrator` and `unassigned`).')


class AnalysisPlanStageCounts(View):
    """Pending (uncached) requests by stage."""
    discovery: int = Field(description='Character-discovery requests (fast model).')
    profiles: int = Field(description='Profile-refinement requests (detailed model).')
    directing: int = Field(description='Passage/scene direction requests (detailed model).')


class AnalysisPlanLimits(View):
    """Echo of the request's `limits` with defaults applied. The preview does not enforce them."""
    max_requests: int = Field(description='Per-run HTTP request cap.')
    max_input_tokens: int = Field(description='Per-run input-token cap.')
    max_output_tokens: int = Field(description='Per-run output-token cap.')
    budget_usd: float | None = Field(description='Cumulative tracked analysis allowance for the book in USD; null means no dollar guard.')


class AnalysisPlanBase(View):
    """The fields shared by the classic per-book plan and each book of a series plan."""
    phase: Literal['scan', 'profiles', 'direct', 'full'] = Field(description='Requested phase.')
    provider: str = Field(description='Resolved provider: the request value, or the saved default analysis provider.')
    scan_model: str | None = Field(description='Configured discovery (fast) model for this provider, or null when none is configured '
                                               '(the run then falls back to the detailed model or the built-in default).')
    model: str | None = Field(description='Configured detailed model for this provider, or null (the run then uses the built-in default).')
    requests: int = Field(description='Requests that are not already cached. Always 0 for `local`. Excludes retries and evidence repairs.')
    cached_units: int = Field(description='Known units that validated cached output would satisfy (only when `resume` is true).')
    estimated_input_tokens: int = Field(description='Approximate input tokens of the pending requests (0 for `local`).')
    output_token_allowance: int = Field(description='Sum of the pending requests\' output caps (0 for `local`).')
    estimated_cost_usd: float | None = Field(description='Approximate USD cost of the pending requests; null when a model has no '
                                                         'known price; 0 for `local`. Not an invoice.')
    steps_by_stage: AnalysisPlanStageCounts = Field(description='Pending requests by stage. Computed for `local` too, although a local run '
                                                                'does not send these requests.')
    coverage: AnalysisCoverage = Field(description='Same body as `GET /api/books/{book_id}/preprocessing`, with profile freshness '
                                                   'computed against the plan\'s working cast.')
    future_work_unknown: bool = Field(description='True for `full`: discovery can add profiles and change direction prompts, '
                                                  'so the estimate is incomplete.')
    note: str = Field(description='Interpretation caveat. Display only.')


class AnalysisPlan(AnalysisPlanBase):
    """Preview of currently known classic-analysis work. No provider is contacted."""
    limits: AnalysisPlanLimits


# ------------------------------------------------------------------ inspection

class PipelineStage(View):
    """One stage card of the pipeline inspector. Counts have stage-specific units; never sum them."""
    id: Literal['import', 'structure', 'census', 'series', 'discovery', 'profiles', 'directing', 'voices',
                'narration', 'alignment', 'export'] = Field(description='Stage ID, in pipeline order.')
    label: str = Field(description='Display name.')
    status: Literal['complete', 'partial', 'pending', 'available', 'not_started', 'provisional', 'planned', 'ready',
                    'queued', 'running', 'failed', 'interrupted', 'cancelled', 'budget_limited'] = Field(
        description='`complete`/`partial`/`pending` from the counts. Fixed states: `series` is `available` (book is in a series) '
                    'or `not_started`; `profiles` is `provisional` while whole-book discovery is incomplete and some profiles '
                    'are current; `alignment` is `planned`; `export` is `ready`. An active `analyze` job whose checkpoint '
                    'stage is this stage shows the job status (`queued`/`running`); likewise an active `render` job for '
                    '`narration`. With no active job, a `failed`, `interrupted` or `budget_limited` classic checkpoint shows '
                    'that status on its stage, and `cancelled` instead of `interrupted` when the job that wrote the checkpoint '
                    'was cancelled. Checkpoints written before contract 0.2.0 do not name their job, so they show '
                    '`interrupted` for a cancellation too.')
    completed: int | None = Field(description='Units done, or null where not counted (`series`, `export`).')
    total: int | None = Field(description='Units in scope, or null where not counted.')
    unit_label: str = Field(description='What the counts measure, e.g. `sections`, `eligible sections`, `profiles`, `passages`.')
    dependencies: list[str] = Field(description='Stage IDs this stage conceptually depends on. Descriptive, not a scheduler.')
    artifact_count: int = Field(description='Retained artifact versions whose stage equals this ID.')
    note: str = Field(description='Interpretation text. Display only.')


class PipelineAttempt(View):
    """One recorded analysis HTTP attempt (classic or step pipeline).

    The pipeline inspector lists the newest 100 for the book; the analysis
    export's `analysis-attempts.json` lists all of them in this same shape.
    Fields come from the stored attempt through a fixed allowlist and may be
    absent on records from older versions. Prompts, responses, credentials
    and server process IDs are never included.
    """
    id: str = Field(description='Attempt ID.')
    book_id: str | None = Field(default=None, description='Book the attempt was made for.')
    run_id: str | None = Field(default=None, description='Job ID of the run that sent it.')
    stage: str | None = Field(default=None, description='Classic stage (`discovery`, `profiles`, `directing`) or pipeline step ID.')
    unit_key: str | None = Field(default=None, description='Opaque cache key of the unit of work.')
    chapter_id: str | None = Field(default=None, description='Chapter the request was about, when recorded.')
    provider: str | None = Field(default=None, description='Provider ID.')
    model: str | None = Field(default=None, description='Model ID.')
    status: Literal['reserved', 'received', 'uncertain', 'not_sent', 'interrupted_unknown'] | None = Field(
        default=None, description='`reserved`: allowance reserved and request possibly in flight. `received`: an HTTP response '
                                  'arrived (any status code). `uncertain`: sent but no response (billing unknown). `not_sent`: '
                                  'the connection failed before sending. `interrupted_unknown`: still `reserved` but its run is not '
                                  'active, so the outcome is unknown.')
    created_at: str | None = Field(default=None, description='ISO 8601 UTC reservation time.')
    completed_at: str | None = Field(default=None, description='ISO 8601 UTC time the outcome was recorded; absent while reserved.')
    http_status: int | None = Field(default=None, description='Provider HTTP status code. A 200 does not mean the output passed validation.')
    input_tokens: int | None = Field(default=None, description='Reported input tokens; null when not reported.')
    output_tokens: int | None = Field(default=None, description='Reported output tokens; null when not reported.')
    cached_input_tokens: int | None = Field(default=None, description='Reported cached input tokens; null when not reported.')
    cache_write_input_tokens: int | None = Field(default=None, description='Reported cache-write input tokens; null when not reported.')
    reserved_input_tokens: int | None = Field(default=None, description='Input allowance reserved before sending (conservative).')
    reserved_output_tokens: int | None = Field(default=None, description='Output allowance reserved before sending.')
    charged_estimate_usd: float | None = Field(default=None, description='Conservative USD estimate for this attempt; null when unknown.')
    cost_basis: str | None = Field(default=None, description='How `charged_estimate_usd` was made, e.g. `reservation`, '
                                                            '`usage_estimate_with_guard_uplift`, `not_sent` or `unknown`.')
    input_rate: float | None = Field(default=None, description='Input price used for the estimate, in USD per million input '
                                                               'tokens; null when the model has no known price.')
    output_rate: float | None = Field(default=None, description='Output price used for the estimate, in USD per million output '
                                                                'tokens; null when the model has no known price. '
                                                                '`charged_estimate_usd` = (input tokens × `input_rate` × 1.25 + '
                                                                'output tokens × `output_rate`) / 1,000,000, using reported '
                                                                'usage when present and the reservation otherwise.')
    price_as_of: str | None = Field(default=None, description='Date of the price table used for the estimate, or null.')
    price_source: str | None = Field(default=None, description='URL of the price source used, or null.')
    elapsed_seconds: float | None = Field(default=None, description='Measured wall time of the request in seconds; null when unknown.')
    input_artifact_id: str | None = Field(default=None, description='Artifact ID of the retained request recipe (`analysis_input`).')
    validation_state: Literal['accepted', 'rejected', 'unknown'] = Field(
        description='From retained events: `accepted` or `rejected` by output validation; `unknown` when no event links it.')


class PipelineEvent(View):
    """One retained analysis event (newest 100 for the book)."""
    id: str = Field(description='Event ID (32 hex characters).')
    book_id: str = Field(description='Book ID the event belongs to.')
    run_id: str | None = Field(description='Job ID of the run.')
    stage: str | None = Field(description='Classic stage or pipeline step ID.')
    unit_key: str | None = Field(description='Opaque unit cache key.')
    event: Literal['started', 'accepted', 'cache_hit', 'cache_rejected', 'cache_superseded', 'validation_rejected',
                   'budget_limited', 'failed', 'cancelled'] = Field(description='What happened to the unit.')
    created_at: str = Field(description='ISO 8601 UTC.')
    artifact_id: str | None = Field(default=None, description='Related artifact: request recipe (`started`), accepted output, '
                                                             'cached output or rejection record.')
    attempt_id: str | None = Field(default=None, description='Related HTTP attempt, when known.')
    error: str | None = Field(default=None, description='Redacted failure or rejection text. Display only.')
    cached_unit_key: str | None = Field(default=None, description='For `cache_rejected` in classic runs: the unit key of the rejected cache entry.')
    repair: bool | None = Field(default=None, description='For step-pipeline `started`: true when this is an evidence-repair request.')


class PipelineCapabilities(View):
    """Features the inspector reports as present."""
    word_alignment: bool = Field(description='Always false: word-level alignment is not implemented.')


class ArtifactCounts(View):
    """Retained artifact counts for one book."""
    kinds: list[str] = Field(description='Distinct artifact kinds present, sorted.')
    stages: dict[str, int] = Field(description='Version count by artifact stage.')
    total: int = Field(description='All retained versions owned by the book.')
    current: int = Field(description='Currently selected versions (heads).')


class PipelineInspector(View):
    """Read-only inspector envelope for a book's processing pipeline."""
    schema_version: int = Field(description='Envelope version (currently 1).')
    book_id: str = Field(description='Book ID of the inspected book.')
    stages: list[PipelineStage] = Field(description='Stage cards in pipeline order.')
    jobs: list[Job] = Field(description='The book\'s newest 100 jobs, newest first, as full `Job` objects (the same as `GET /api/jobs?book_id=…`).')
    usage: AnalysisUsage
    attempts: list[PipelineAttempt] = Field(description='The newest 100 analysis attempts, oldest first.')
    events: list[PipelineEvent] = Field(description='The newest 100 analysis events, newest first.')
    capabilities: PipelineCapabilities
    artifact_kinds: list[str] = Field(description='Same as `artifact_counts.kinds`.')
    artifact_counts: ArtifactCounts
    notes: list[str] = Field(description='Interpretation notes. Display only.')


class ResourceAggregate(View):
    """Totals over a set of resource rows.

    For each measured quantity, the value is the sum over rows that recorded it.
    It is 0 when no row qualifies (an empty scope, or token quantities when every
    row was local or cached), and null only when rows qualify but none recorded
    the value. `unknown_<quantity>_operations` counts the qualifying rows that
    did not record it. Token quantities only consider rows that may have made a provider
    request. Unknown never means zero or free.
    """
    operations: int = Field(description='Rows in the set.')
    requests: int = Field(description='Sum of known provider request counts.')
    cached_operations: int = Field(description='Rows that reused saved output.')
    failed_operations: int = Field(description='Rows with status failed/uncertain/not_sent/interrupted or a rejected validation.')
    running_operations: int = Field(description='Rows still running or reserved.')
    elapsed_seconds: float | None = Field(description='Sum of recorded leaf elapsed time in seconds (not end-to-end job latency).')
    cpu_seconds: float | None = Field(description='Sum of measured current-Python-thread CPU seconds.')
    audio_seconds: float | None = Field(description='Sum of produced or reused audio duration in seconds.')
    estimated_cost_usd: float | None = Field(description='Sum of known cost estimates in USD. Not an invoice.')
    input_tokens: float | None = Field(description='Sum of reported input tokens.')
    output_tokens: float | None = Field(description='Sum of reported output tokens.')
    cached_input_tokens: float | None = Field(description='Sum of reported cached input tokens.')
    cache_write_input_tokens: float | None = Field(description='Sum of reported cache-write input tokens.')
    output_bytes: float | None = Field(description='Sum of recorded output sizes in bytes.')
    unknown_elapsed_seconds_operations: int = Field(description='Rows with no recorded elapsed time (for example cache reuse or historical rows). Such rows are left out of `elapsed_seconds`; unknown is not zero.')
    unknown_cpu_seconds_operations: int = Field(description='Rows with no measured CPU time (CPU is only measured for opted-in local work, so most cloud and cached rows count here). Unknown is not zero.')
    unknown_audio_seconds_operations: int = Field(description='Rows with no recorded audio duration (including every non-audio row such as analysis requests). Unknown is not zero.')
    unknown_estimated_cost_usd_operations: int = Field(description='Rows with no USD cost estimate. Such rows are left out of `estimated_cost_usd`; unknown cost is not free.')
    unknown_input_tokens_operations: int = Field(description='Rows that may have made a provider request (request count not 0, including unknown) but reported no input-token count. Local and cached rows are not counted.')
    unknown_output_tokens_operations: int = Field(description='Rows that may have made a provider request but reported no output-token count. Local and cached rows are not counted.')
    unknown_cached_input_tokens_operations: int = Field(description='Rows that may have made a provider request but reported no cached-input-token count. Missing is unknown, not zero.')
    unknown_cache_write_input_tokens_operations: int = Field(description='Rows that may have made a provider request but reported no cache-write input-token count. Missing is unknown, not zero.')
    unknown_output_bytes_operations: int = Field(description='Rows with no recorded output size in bytes (most non-file rows, such as analysis requests). Unknown is not zero.')
    reserved_cost_usd: float = Field(description='Part of the estimate that is a reservation (usage never reported), in USD.')
    unknown_request_count_operations: int = Field(description='Rows whose request count is unknown.')


class ResourceStageAggregate(ResourceAggregate):
    """Aggregate of every row in scope with one stage."""
    id: str = Field(description='Stage name of the rows, or `unrecorded`.')


class ResourceRunAggregate(ResourceAggregate):
    """Aggregate of one run (job) in scope."""
    id: str = Field(description='Run (job) ID.')
    status: str = Field(description='Status of the job with this ID, or `unrecorded` when no such job exists.')
    kind: str | None = Field(description='Job kind, or null when no such job exists.')
    created_at: str | None = Field(description='ISO 8601 UTC time of the oldest row, or the job creation time when no rows exist.')
    has_measurements: bool = Field(description='False for a job with no recorded rows (historical or unmeasured).')


class ResourceOperation(View):
    """One recorded unit of work: an analysis HTTP attempt, a local or narration operation, or a cache reuse.

    `kind` tells the three apart. `analysis_request` rows come from the
    analysis attempt ledger; `cache_reuse` rows from cache-hit events (no
    request, no measured duration); every other kind from the resource
    ledger. Absent or null measurements are unknown, not zero.
    """
    id: str = Field(description='Row ID (32 hex characters): the analysis attempt ID for `analysis_request`, the cache-hit event ID '
                                'for `cache_reuse`, otherwise the resource-ledger operation ID. Unique within the response.')
    book_id: str = Field(description='Book ID the work was recorded for.')
    run_id: str | None = Field(default=None, description='Job ID, or null for work outside a job (e.g. imports).')
    stage: str | None = Field(default=None, description='What was measured, e.g. `discovery`, `discovery_validation`, `publication`, '
                                                       '`census`, `local_analysis`, `narration`, `simple_listen`, `listen_chunk`, '
                                                       '`voice_preview`, `voice_design`, `import`, `structure_repair`, '
                                                       '`metadata_refresh`, `audio_export`, or a pipeline step ID. `source_search` and '
                                                       '`analysis_export` rows were recorded by versions before contract 0.2.0 and remain.')
    unit_key: str | None = Field(default=None, description='Opaque unit key (cache key, passage ID, preview ID or census fingerprint).')
    chapter_id: str | None = Field(default=None, description='Chapter the work belongs to, when recorded.')
    provider: str | None = Field(default=None, description='Provider ID (`local` for local work).')
    model: str | None = Field(default=None, description='Model ID, when any.')
    kind: str = Field(description='`analysis_request`, `cache_reuse`, or a resource-ledger kind: `local`, `assembly`, `validation`, `narration`.')
    cached: bool = Field(description='True when saved output was reused without a provider request.')
    status: Literal['reserved', 'received', 'uncertain', 'not_sent', 'running', 'completed', 'failed', 'interrupted', 'unknown'] = Field(
        description='Analysis requests: attempt status (`reserved`, `received`, `uncertain`, `not_sent`), `failed` when the HTTP '
                    'status was >= 400 or a failure event names it, `unknown` for legacy rows. Other rows: `running`, `completed`, '
                    '`failed`, `interrupted`. Rows left `running`/`reserved` by an earlier server process or a finished job are '
                    'reported `interrupted`.')
    created_at: str | None = Field(default=None, description='ISO 8601 UTC start time.')
    completed_at: str | None = Field(default=None, description='ISO 8601 UTC end time; absent while running.')
    request_count: int | None = Field(default=None, description='Provider requests made: 1 for analysis requests, 0 for local/cached '
                                                              'work, null when unknown (e.g. a cloud narration that failed early).')
    input_tokens: int | None = Field(default=None, description='Reported input tokens.')
    output_tokens: int | None = Field(default=None, description='Reported output tokens.')
    cached_input_tokens: int | None = Field(default=None, description='Reported cached input tokens.')
    cache_write_input_tokens: int | None = Field(default=None, description='Reported cache-write input tokens.')
    output_bytes: int | None = Field(default=None, description='Bytes written (audio, exports).')
    elapsed_seconds: float | None = Field(default=None, description='Measured wall time of this step in seconds.')
    cpu_seconds: float | None = Field(default=None, description='Measured CPU seconds of the current Python thread (local work only).')
    audio_seconds: float | None = Field(default=None, description='Audio duration produced or reused, in seconds.')
    estimated_cost_usd: float | None = Field(default=None, description='Estimated USD cost; 0 for local/cached/self-hosted work; null when unknown.')
    cost_basis: str | None = Field(default=None, description='How the estimate was made, e.g. `reservation`, `usage_estimate_with_guard_uplift`, '
                                                            '`not_sent`, `no_provider_request`, `self_hosted`, '
                                                            '`standard_paid_tier_usage_estimate`, `legacy_estimate`, `unknown`.')
    price_as_of: str | None = Field(default=None, description='Date of the price table used.')
    price_source: str | None = Field(default=None, description='URL of the price source used.')
    usage_source: str | None = Field(default=None, description='Where token usage came from (narration), e.g. `gemini_interactions` or `not_reported`.')
    cpu_scope: str | None = Field(default=None, description='`current_python_thread` when CPU was measured.')
    artifact_id: str | None = Field(default=None, description='Related retained artifact, when recorded.')
    asset_id: str | None = Field(default=None, description='Related stored audio asset, when recorded.')
    http_status: int | None = Field(default=None, description='Provider HTTP status, when any.')
    validation_state: Literal['accepted', 'rejected', 'unknown'] | None = Field(
        default=None, description='Analysis requests only: output validation outcome from retained events.')


class ResourceSummary(View):
    """Recorded resource usage for a book, optionally narrowed to one run. Never contacts a provider."""
    schema_version: int = Field(description='Envelope version (currently 1).')
    book_id: str = Field(description='Book ID the usage belongs to.')
    run_id: str | None = Field(description='The `run_id` filter, or null.')
    totals: ResourceAggregate = Field(description='Aggregate over every row in scope (not just this page).')
    stages: list[ResourceStageAggregate] = Field(description='Aggregates by stage over every row in scope.')
    runs: list[ResourceRunAggregate] = Field(description='Per-run aggregates, newest first, at most 100. Includes jobs of the book '
                                                         'without rows.')
    total_runs: int = Field(description='Number of runs before the 100 cap.')
    operations: list[ResourceOperation] = Field(description='This page of rows, newest first.')
    total_operations: int = Field(description='Rows in scope.')
    unmeasured_runs: int = Field(description='Runs with no recorded rows.')
    limit: int = Field(description='Effective page size after clamping to 1–200.')
    offset: int = Field(description='Effective offset after clamping to 0–9007199254740991 (2^53 − 1).')
    price_sources: list[str] = Field(description='Distinct price-source URLs referenced by rows in scope.')
    notes: list[str] = Field(description='Interpretation notes. Display only.')


ARTIFACT_KINDS = (
    '`source` (chapter text), `structure` (book structure), `scene_map` (per-chapter scenes and passages), '
    '`character_profile`, `voice_assignment`, `audio_take` (passage audio metadata), `census`, '
    '`character_observation`, `series_context`, `series_run`, `library_state`, `analysis_input` (request recipe), '
    '`analysis_output` (validated result), `analysis_rejection` (rejected result with its validation error) and '
    '`step_output` (step-pipeline version)')


class ArtifactSummary(View):
    """Metadata of one immutable artifact version."""
    id: str = Field(description='Artifact version ID (`artifact_` + content hash).')
    book_id: str = Field(description='Owning book.')
    kind: str = Field(description='Artifact kind. Known kinds: ' + ARTIFACT_KINDS + '. Treat as an open set.')
    logical_key: str = Field(description='Scope within the kind (e.g. chapter ID, character ID, unit key, `book`). One version per '
                                         '(book, kind, logical_key) can be current.')
    label: str = Field(description='Display label.')
    stage: str = Field(description='Producing stage, e.g. `import`, `structure`, `census`, `discovery`, `profiles`, `directing`, `voices`, '
                                   '`narration`, `series`, `library`, a pipeline step ID, or empty for some legacy records.')
    provider: str | None = Field(description='Producing provider, where recorded.')
    model: str | None = Field(description='Producing model, where recorded.')
    schema_version: int = Field(description='Artifact record schema version (currently 1).')
    legacy_provenance: bool = Field(description='True when retained after the fact or from an older version: original production '
                                                'inputs may be incomplete and are not reconstructed.')
    payload_bytes: int = Field(description='Size of the stored JSON payload in UTF-8 bytes.')
    dependencies: list[str] = Field(description='Artifact IDs this version was produced from (actual retained inputs; may belong '
                                                'to earlier books).')
    created_at: str = Field(description='ISO 8601 UTC time the version was first retained.')
    is_current: bool = Field(description='True when this version is the current selection for its scope. Rejected, superseded '
                                         'and unselected candidates stay retained with false.')


class ArtifactPage(View):
    """One page of artifact metadata, newest first."""
    items: list[ArtifactSummary] = Field(description='This page of artifact versions matching the filters, newest first (by creation time). '
                                                     'Empty past the end.')
    total: int = Field(description='Versions matching the filters.')
    offset: int = Field(description='Effective offset (versions skipped) after clamping to 0–9007199254740991 (2^53 − 1).')
    limit: int = Field(description='Effective page size after clamping to 1–200.')


class ArtifactDependencyLink(View):
    """A dependency and the book that owns it."""
    id: str = Field(description='Dependency artifact ID.')
    book_id: str = Field(description='Owning book; fetch the dependency under this book\'s path.')


class ArtifactDetail(ArtifactSummary):
    """One artifact version with its payload."""
    dependency_links: list[ArtifactDependencyLink] = Field(description='The dependencies with their owning books, ordered by ID.')
    payload: Any = Field(description='The literal retained JSON, whose shape depends on `kind` (and for `step_output`, on the step '
                                     'and its version). Any JSON value. Payloads are historical records: shapes from older versions '
                                     'remain as retained. Examples: `source` is `{chapter_id, text, text_sha256}`; `analysis_output` '
                                     'is a validated unit with `result`; `analysis_rejection` is `{result, validation_error, '
                                     'unit_key, attempt_id?}`; `step_output` is `{schema_version, step_id, step_version, scope, '
                                     'origin, inputs, result}`.')


class StoryMapLogicalSection(View):
    """A contents-entry section inside one chapter (EPUB navigation)."""
    title: str = Field(description='Contents-entry label from the EPUB navigation, non-empty, truncated to 300 characters.')
    start: int = Field(description='Code-point offset into the chapter text (inclusive).')
    end: int = Field(description='Code-point offset (exclusive).')
    kind: str = Field(description='Section type, as for chapters.')
    depth: int = Field(description='Navigation nesting depth (0 = top).')
    title_source: str = Field(description='Where the title came from, e.g. `epub_nav` or `epub_ncx`.')


class StoryMapScene(View):
    """A scene with its passages and attributed speakers."""
    id: str = Field(description='Scene ID.')
    title: str = Field(description='Scene title from analysis (local draft or model direction); empty string when the scene has none.')
    start: int | None = Field(description='Smallest passage start offset, or null when no passage has offsets.')
    end: int | None = Field(description='Largest passage end offset, or null.')
    passage_ids: list[str] = Field(description='Passages in the scene, in order.')
    character_ids: list[str] = Field(description='Attributed dialogue speakers that are cast members (excluding narrator/unassigned), '
                                                 'sorted. Not proof of physical presence.')


class StoryMapChapter(View):
    """A chapter with its source range and scenes."""
    id: str = Field(description='Chapter ID.')
    title: str = Field(description='Chapter title as stored in the book.')
    kind: str = Field(description='`chapter`, `recap`, `section`, `front_matter` or `back_matter` (`section` when unrecorded).')
    start: int = Field(description='Always 0.')
    end: int = Field(description='Chapter length in code points.')
    source_artifact_id: str | None = Field(description='Current `source` artifact for the chapter text, or null.')
    logical_sections: list[StoryMapLogicalSection] = Field(description='EPUB contents entries that fall inside this chapter, in order; '
                                                                           'empty for books without EPUB navigation (for example plain-text imports).')
    scenes: list[StoryMapScene] = Field(description='Scenes of this chapter in stored order; empty before scene analysis.')


class StoryMapCharacter(View):
    """A cast member."""
    id: str = Field(description='Book-local character ID (not a series identity).')
    name: str = Field(description='Display name of the cast member. The cast includes the built-in `narrator` and `unassigned` entries.')


class StoryMapSourceAnchor(View):
    """A verified location of a passage in a retained source artifact."""
    artifact_id: str | None = Field(description='The chapter\'s `source` artifact.')
    start: int = Field(description='Code-point offset (inclusive).')
    end: int = Field(description='Code-point offset (exclusive).')


class StoryMapNode(View):
    """A typed graph node. IDs are `<book_id>:<type>:<local id>`, so they are unique across books."""
    id: str = Field(description='Graph node ID `<book_id>:<type>:<local id>`, where the local ID is the book, chapter, scene, passage or '
                                'book-local character ID. Referenced by edge `from`/`to`.')
    type: Literal['book', 'chapter', 'scene', 'passage', 'character'] = Field(
        description='Node type; decides which of the optional ID fields below are present.')
    book_id: str | None = Field(default=None, description='`book` nodes.')
    chapter_id: str | None = Field(default=None, description='`chapter` and `passage` nodes.')
    source_artifact_id: str | None = Field(default=None, description='`chapter` nodes: current source artifact, or null.')
    scene_id: str | None = Field(default=None, description='`scene` nodes.')
    passage_id: str | None = Field(default=None, description='`passage` nodes.')
    source_anchor: StoryMapSourceAnchor | None = Field(default=None, description='`passage` nodes: verified anchor, or null when the '
                                                                                 'passage text does not match its offsets.')
    character_id: str | None = Field(default=None, description='`character` nodes.')
    name: str | None = Field(default=None, description='`character` nodes.')


class StoryMapEdge(View):
    """A typed graph edge."""
    from_: str = Field(alias='from', description='Source node ID.')
    to: str = Field(description='Target node ID. Every edge ends at a node in `nodes`.')
    type: Literal['contains', 'next', 'attributed_speaker'] = Field(
        description='`contains`: book→chapter, chapter→scene, scene (or chapter)→passage. `next`: reading order between passages '
                    'of a chapter. `attributed_speaker`: dialogue passage→character (an attribution, not presence); '
                    'omitted when the passage names a speaker that is no longer in the cast.')
    order: int | None = Field(default=None, description='`contains` edges: zero-based position within the parent.')
    confidence: float | None = Field(default=None, description='`attributed_speaker` edges: attribution confidence 0–1, or null.')


class StoryMapReference(View):
    """A source reference to a character. Kinds are distinct evidence and must not be merged."""
    id: str = Field(description='Stable reference ID (hash of character, chapter, range and kind).')
    character_id: str = Field(description='Book-local character ID the reference is about (not a series identity).')
    chapter_id: str = Field(description='Chapter ID whose text `start`/`end` index into.')
    segment_id: str | None = Field(description='First passage overlapping the range, or null.')
    start: int = Field(description='Code-point offset into the chapter text (inclusive).')
    end: int = Field(description='Code-point offset (exclusive).')
    quote: str = Field(description='The exact source text of the range.')
    kind: Literal['mention', 'dialogue', 'profile_evidence'] = Field(
        description='`mention`: a name/alias match (not presence). `dialogue`: an attributed dialogue passage. `profile_evidence`: a '
                    'quotation a model cited as evidence.')
    confidence: float | None = Field(default=None, description='Attribution confidence for `dialogue`; null otherwise. Current '
                                                               'writers always include it; stored references from other '
                                                               'sources may omit it.')
    provider: str | None = Field(description='`local` for mentions, `reviewed` for reviewed dialogue, else the analysis provider.')
    model: str | None = Field(description='Model that produced it, or null.')
    profile_description: str | None = Field(default=None, description='`profile_evidence`: the description the model gave with this evidence.')
    profile_direction: str | None = Field(default=None, description='`profile_evidence`: the direction the model gave.')


class StoryMapReferenceCounts(View):
    """Reference counts by kind."""
    mention: int = Field(description='Number of `mention` references (name/alias matches) in `references`.')
    dialogue: int = Field(description='Number of `dialogue` references (attributed dialogue passages).')
    profile_evidence: int = Field(description='Number of `profile_evidence` references (model-cited evidence quotations).')


class StoryMap(View):
    """A typed graph of the book: book→chapters→scenes→passages, reading order and speaker attributions."""
    schema_version: int = Field(description='Envelope version (currently 1).')
    book_id: str = Field(description='Book ID of the mapped book.')
    chapters: list[StoryMapChapter] = Field(description='Every chapter of the book in book order, with its scenes.')
    characters: list[StoryMapCharacter] = Field(description='Every cast member, including `narrator` and `unassigned`.')
    nodes: list[StoryMapNode] = Field(description='Graph nodes: one book node, then per chapter its chapter, scene and passage nodes, '
                                                   'then one node per cast member. Unpaginated.')
    edges: list[StoryMapEdge] = Field(description='Graph edges (`contains`, `next`, `attributed_speaker`) between node IDs. Unpaginated.')
    references: list[StoryMapReference] = Field(description='All saved character references for the book, unpaginated.')
    reference_counts: StoryMapReferenceCounts
    note: str = Field(description='Interpretation caveat. Display only.')


class PassageSearchHit(View):
    """One matching passage."""
    book_id: str = Field(description='Book containing the passage (this book or an earlier series volume).')
    book_title: str = Field(description='Title of that book.')
    chapter_id: str = Field(description='Chapter ID (within `book_id`) whose text `start`/`end` index into.')
    chapter_title: str = Field(description='Title of that chapter.')
    passage_id: str = Field(description='Passage (segment) ID of the matching passage, within `book_id`.')
    start: int = Field(description='Code-point offset into the chapter text (inclusive).')
    end: int = Field(description='Code-point offset (exclusive).')
    text: str = Field(description='The exact passage text.')
    source_hash: str = Field(description='SHA-256 (hex) of the chapter text encoded as UTF-8.')
    rank: float = Field(description='SQLite FTS5 rank. Lower (more negative) is a stronger lexical match; not confidence or identity.')


class PassageSearchResult(View):
    """Literal word search over saved passages."""
    items: list[PassageSearchHit] = Field(description='Matches, strongest first.')
    available: bool = Field(description='False when this SQLite build lacks FTS5; then there are no results.')
    query: str = Field(description='The `q` parameter as sent.')
    scope: Literal['book', 'earlier'] = Field(description='The effective scope.')
    note: str = Field(description='Interpretation text, or why there are no results. Display only.')


# ---------------------------------------------------------------- operations

ANALYSIS_BODY_NOTE = """\
The request body is `AnalysisRequest`. `provider` defaults to the saved analysis provider. Models come from
runtime settings (`analysis_models_by_provider` for the detailed model, `preprocess_models_by_provider` for
the fast discovery model), not from this request. A typical body:

```json
{"provider": "openai", "phase": "scan", "resume": true,
 "limits": {"max_requests": 25, "max_input_tokens": 1000000, "max_output_tokens": 100000, "budget_usd": 1.0}}
```

Cloud phases (`provider` = `gemini`, `openai` or `anthropic`):

| `phase` | Scope |
| --- | --- |
| `scan` | Discover characters over eligible source sections using the fast model. |
| `profiles` | Refine character profiles from retained evidence and confirmed earlier-series context. |
| `direct` | Annotate passages/scenes with the detailed model; set `chapter_id` to select a chapter. |
| `full` | Discovery, profile refinement, and direction. Newly discovered work makes the initial estimate incomplete. |

`provider: "local"` runs the free heuristic draft instead, without semantic model discovery; `phase` and
`limits` do not apply to it; it drafts every chapter, or only `chapter_id`. `chapter_id` is optional; when
supplied it must belong to the book. Cloud phases skip front and back matter unless `chapter_id` names
such a section. The main UI uses whole-book scan/profiles and selected-chapter direction.
"""

OPS: list[Op] = [
    op('GET', '/api/books/{book_id}/analysis', 'getAnalysisStatus', 'Classic analysis',
       'Get classic analysis progress',
       'Checkpoint summary of the classic analysis engine, including per-chapter progress. Returns '
       '`status: "not_started"` (with one pending row per chapter) when no checkpoint exists.\n\n'
       'The checkpoint belongs to the latest run that saved progress and survives failures, cancellation and '
       'restarts (a server restart marks a running checkpoint `interrupted`). Follow a running analysis through '
       'its job (`GET /api/jobs`); use this for per-chapter detail. The step pipeline (`/analysis-pipeline`) does '
       'not write this checkpoint. ' + READ_ONLY + '\n\n'
       'The classic engine may be retired in favor of the step pipeline.',
       response=AnalysisStatus, errors={404: BOOK_404}, params={'book_id': BOOK_ID}),

    op('POST', '/api/books/{book_id}/analyze', 'startClassicAnalysis', 'Classic analysis',
       'Start or resume classic analysis',
       'Queues an `analyze` job and returns it immediately. Poll `GET /api/jobs` until it is terminal; '
       'failures, cancellation and allowance stops (`budget_limited`) appear in the job, not as HTTP errors. '
       'Cancel with `POST /api/jobs/{job_id}/cancel`; a remote request already sent can still complete and be '
       'billed. To resume after a stop, failure or restart, call this endpoint again (an old job ID is never '
       'revived).\n\n'
       'Send the same body to `POST /api/books/{book_id}/analysis-plan` first. Dispatching it with a cloud '
       'provider may incur charges; `provider: "local"` never contacts a provider. There is no server-enforced '
       'preview fingerprint for per-book runs (series runs have one); the UI invalidates its preview when local '
       'inputs change.\n\n'
       + ANALYSIS_BODY_NOTE +
       '\nThe provider and configured models are snapshotted when the job is queued; the job carries `provider`, '
       '`model`, `scan_model`, `phase` and `chapter_id`.\n\n'
       'Resuming (`resume: true`, the default) reuses validated saved units instead of requesting them again. '
       'Every HTTP attempt, including retries and evidence repairs, is reserved against `limits` before it is '
       'sent. Accepted units and completed chapter work survive later failures. Human edits remain authoritative, '
       'affected enhanced takes become stale, and source text is never replaced by model output. Whole-book scan '
       'coverage and profile freshness are separate (see `GET /api/books/{book_id}/preprocessing`). The book is '
       'updated (new revision) as each chapter stage is published. The run retains the census and any validated '
       'discovery imported from an older checkpoint as artifacts. When the job is queued, the current projection is '
       "recorded in the step pipeline's version history (as `baseline` or `external` versions) when the history "
       'does not already explain it, so the state the analysis replaces stays restorable.\n\n'
       'The classic engine may be retired in favor of the step pipeline.',
       response=Job, response_description='The queued `analyze` job.',
       errors={404: BOOK_404,
               400: {'unknown_chapter': UNKNOWN_CHAPTER,
                     'unknown_provider': UNKNOWN_PROVIDER,
                     'gemini_key_missing': 'The provider is `gemini` and no Gemini API key is configured.',
                     'api_key_missing': 'The provider is `openai` or `anthropic` and no API key is configured for it.'},
               409: {'book_archived': 'The book is archived. Restore it first.',
                     'job_active': 'A job is already queued or running for this book.',
                     'series_run_active': 'An active series run has reserved this book.'},
               503: {'shutting_down': 'The server is shutting down and accepts no new work. No job was started.'}},
       params={'book_id': BOOK_ID}, cost='may_charge'),

    op('GET', '/api/books/{book_id}/preprocessing', 'getAnalysisPreprocessing', 'Classic analysis',
       'Get the local census, discovery coverage and profile freshness',
       'Free local census (names, speech tags, dialogue counts and heuristic priority per character; words and '
       'token estimates per chapter), semantic source coverage from validated cloud discovery, tracked analysis '
       'usage, and profile freshness/provisional state. No provider is contacted.\n\n'
       + READ_ONLY + ' It may write one disposable derived cache: the census is computed and cached when the '
       'book\'s text, structure, cast or attributions changed since it was last cached. The cache can be deleted '
       'without loss and is rebuilt on demand. Validated discovery found only in an older checkpoint counts toward '
       'coverage but is not imported here, and the census is not retained as an artifact here; analysis runs and '
       '`POST /api/books/{book_id}/analysis-plan` do both.',
       response=AnalysisCoverage, errors={404: BOOK_404}, params={'book_id': BOOK_ID}),

    op('POST', '/api/books/{book_id}/analysis-plan', 'previewClassicAnalysis', 'Classic analysis',
       'Preview classic analysis work',
       'Previews the work `POST /api/books/{book_id}/analyze` would do for the same `AnalysisRequest`, without '
       'provider inference. Returns the requested phase, provider and configured models, pending requests, '
       'cached units, token and cost estimates, requests by stage, coverage, notes, and the supplied `limits` '
       '(with defaults applied; the preview does not enforce them).\n\n'
       'The estimate covers currently known work before retries and evidence repairs; `full` can discover more '
       'work. Cost is approximate (null when a model has no known price); the run\'s request guard reserves more '
       'conservatively. Unlike `analyze`, the preview works while a job is running. Because it retains records '
       '(below), an archived book is refused (409 `book_archived`).\n\n'
       'Side effects, all local and none of them changing the book: it caches and retains the census (a `census` '
       'artifact, and a `census` resource operation when computed fresh), imports validated discovery found only '
       'in an older checkpoint into the unit cache (with its artifacts), and retains `series_context` artifacts for '
       'linked earlier volumes.\n\n'
       + ANALYSIS_BODY_NOTE,
       response=AnalysisPlan,
       errors={404: BOOK_404,
               400: {'unknown_chapter': UNKNOWN_CHAPTER, 'unknown_provider': UNKNOWN_PROVIDER},
               409: {'book_archived': 'The book is archived. Restore it first.'}},
       params={'book_id': BOOK_ID}),

    op('GET', '/api/books/{book_id}/pipeline', 'getPipelineInspector', 'Inspection',
       'Inspect the processing pipeline',
       'Versioned envelope with stage IDs, status, counts and dependencies; retained artifact counts and kinds; '
       'the book\'s recent jobs; the newest 100 analysis attempts with their validation state; the newest 100 '
       'events; tracked usage; capabilities; and notes. Word alignment is explicitly planned (not implemented).\n\n'
       'The pipeline is an inspector, not a generic dependency scheduler. Its stage counts have different units '
       'and must not be summed into a global completion percentage. An HTTP 200 attempt does not mean its output '
       'passed validation: use `validation_state`.\n\n'
       'No provider is contacted. ' + READ_ONLY + ' Like `GET /api/books/{book_id}/preprocessing`, it may write '
       'the disposable census cache. Legacy data from versions before artifacts existed is retained as artifacts '
       '(marked `legacy_provenance`) once, when the server starts, not by this request.',
       response=PipelineInspector, errors={404: BOOK_404}, params={'book_id': BOOK_ID}),

    op('GET', '/api/books/{book_id}/resources', 'getBookResourceUsage', 'Inspection',
       'Get recorded resource usage',
       'Recorded work for the book: analysis HTTP attempts (the analysis ledger), local and narration '
       'operations (which supplement it without double-counting), and cache reuse. Returns `schema_version`, the '
       'book/run scope, `totals`, stage aggregates and run aggregates; a page of `operations`, '
       '`total_operations` and the effective `limit`/`offset`; `total_runs`, unmeasured-run counts, price-source '
       'URLs and interpretation notes.\n\n'
       '`limit` and `offset` are clamped (to 1–200 and 0–2^53 − 1), as for every paged operation. Aggregates cover '
       'the entire selected scope, not just the current page. The run summary list is bounded to 100; the total '
       'run count is reported separately.\n\n'
       'Operations distinguish request count, reported tokens and cache tokens, retained estimates and '
       'reservations, elapsed time, opted-in local Python thread CPU time, audio seconds, output bytes and cache '
       'reuse. Missing measurements stay null (unknown) and are accompanied by coverage counters. Historical runs '
       'can exist without measurements. Costs are dated estimates, not provider invoices or available credits. '
       'CPU excludes subprocesses, GPUs and remote machines. Cached work does not represent another provider '
       'call. Reads (GET requests, including searches and the analysis export) are not recorded. Never contacts '
       'a provider or backfills guessed usage. ' + READ_ONLY,
       response=ResourceSummary, errors={404: BOOK_404},
       params={'book_id': BOOK_ID,
               'limit': 'Page size for `operations`; default 100, clamped to 1–200.',
               'offset': 'Rows to skip in `operations`; default 0, clamped to 0–9007199254740991 (2^53 − 1).',
               'run_id': 'Only rows (and the run) with this job ID. Optional.'}),

    op('GET', '/api/books/{book_id}/artifacts', 'listBookArtifacts', 'Inspection',
       'List retained artifact versions',
       '`{items, total, offset, limit}` page of artifact metadata owned by the book, newest first. Payloads are '
       'not included; fetch one version for its payload. Artifact metadata includes kind, logical key, stage, '
       'creation time, provider/model where recorded, `is_current`, schema version and legacy-provenance state. '
       'Historical or rejected outputs remain inspectable without becoming accepted knowledge.\n\n'
       '`limit` and `offset` are clamped (to 1–200 and 0–2^53 − 1); an offset past the end returns an empty page. '
       'No provider is contacted. ' + READ_ONLY + ' Legacy data is retained as artifacts (marked '
       '`legacy_provenance`) once, when the server starts.',
       response=ArtifactPage,
       errors={404: BOOK_404},
       params={'book_id': BOOK_ID,
               'kind': 'Only this artifact kind (exact match). Optional.',
               'stage': 'Only this artifact stage (exact match). Optional.',
               'current': '`true` for current selections only, `false` for non-current versions only; omit for all versions.',
               'limit': 'Page size; default 30, clamped to 1–200.',
               'offset': 'Versions to skip; default 0, clamped to 0–9007199254740991 (2^53 − 1).'}),

    op('GET', '/api/books/{book_id}/artifacts/{artifact_id}', 'getBookArtifact', 'Inspection',
       'Get one artifact version with its payload',
       'Metadata plus the literal `payload`, dependency IDs, and `dependency_links: [{id, book_id}]`. An artifact '
       'owned by another book returns 404 under this book\'s path: follow the recorded owner in '
       '`dependency_links` to inspect earlier-book inputs. No provider is contacted. ' + READ_ONLY,
       response=ArtifactDetail,
       errors={404: {**BOOK_404,
                     'artifact_not_found': 'This book owns no artifact with this ID (including an artifact owned by '
                                           'another book).'}},
       params={'book_id': BOOK_ID, 'artifact_id': 'Artifact version ID, from a list, a dependency or an event.'}),

    op('GET', '/api/books/{book_id}/story-map', 'getStoryMap', 'Inspection',
       'Get the story map graph',
       'Versioned typed nodes and edges, chapters/scenes/passages, character IDs, source references and counts, and '
       'notes about interpretation. Node identities include the owning book, and every edge ends at a node. '
       'Verified source anchors refer to retained source artifacts; unavailable or unverified anchors remain null. '
       'Scene characters are attributed speakers, not verified physical presence; mentions and profile evidence '
       'remain separate references; scene boundaries may be local drafts. A dialogue passage whose speaker ID is no '
       'longer in the cast has no `attributed_speaker` edge and adds no scene character.\n\n'
       'The response is unpaginated and grows with the book (every passage is a node). No provider is contacted. '
       + READ_ONLY,
       response=StoryMap, errors={404: BOOK_404}, params={'book_id': BOOK_ID}),

    op('GET', '/api/books/{book_id}/search', 'searchBookPassages', 'Inspection',
       'Search passages by words',
       '`{available, query, scope, items, note}`. Each item identifies book, chapter and passage, the exact '
       'source text, range and chapter hash, and lexical rank.\n\n'
       'Search words (runs of letters, digits and underscores) are combined with AND; this is not an exact-phrase or '
       'operator query language, or semantic embedding search. A query with no words returns no matches with a '
       'note. If SQLite lacks FTS5, `available: false` explains that limitation; it does not start a fallback model '
       'call. Lower lexical rank means a stronger text match, not identity or speaker confidence. Only passages '
       'whose text matches their source offsets are searchable.\n\n'
       'No provider is contacted. ' + READ_ONLY + ' It may write one disposable derived cache: a local full-text '
       'index of each searched book\'s passages, refreshed when the passages changed. The index can be deleted '
       'without loss and is rebuilt on demand.',
       response=PassageSearchResult,
       errors={404: BOOK_404,
               400: {'search_query_invalid': '`q` is empty, whitespace-only or longer than 300 characters.',
                     'search_scope_invalid': '`scope` is not `book` or `earlier`.'}},
       params={'book_id': BOOK_ID,
               'q': 'Search text, 1–300 characters. Required.',
               'scope': '`book` (default) searches this book. `earlier` also searches strictly earlier active (not archived) '
                        'volumes of the book\'s confirmed series; an archived series searches only this book.',
               'limit': 'Maximum results; default 20, clamped to 1–50.'}),

    op('GET', '/api/books/{book_id}/analysis-export', 'exportBookAnalysis', 'Inspection',
       'Download the portable analysis bundle',
       'Portable ZIP (format `spintails-analysis`, version 1: the identifier predates the product rename and is kept) '
       'of the source and reader projection, graph, observations, references, series links, attempts and events, '
       'immutable artifact history, transitive input dependencies, and saved resource and listening metadata where '
       'available. No generated audio is required. Sent as an attachment named `<book title>-analysis.zip` (title '
       'reduced to word characters, spaces, dots and hyphens, at most 80 characters; `book` if empty).\n\n'
       'Contents:\n\n'
       '| File | Content |\n| --- | --- |\n'
       '| `manifest.json` | `{schema_version: 2, format: "spintails-analysis", exported_at, book_id, artifact_count, '
       'external_book_dependencies, audio_files_included: false, source_text_included: true, word_alignment: false, '
       'coordinate_system, notes}`. Start here. |\n'
       '| `README.txt` | Plain-text guide to the bundle. |\n'
       '| `book.json` | The stored book document, including chapter text, passage IDs and stored take metadata '
       '(not the API presentation of `GET /api/books/{book_id}`). |\n'
       '| `story-map.json` | Same body as `GET /api/books/{book_id}/story-map`. |\n'
       '| `series.json` | `{membership, links, series_characters}` for the book\'s series (nulls/empty when none). |\n'
       '| `observations.json` | Retained character observations of the book. |\n'
       '| `references.json` | Saved character references (as in the story map). |\n'
       '| `analysis-attempts.json` | Every recorded analysis HTTP attempt of the book, oldest first, each in the '
       '`PipelineAttempt` shape of `GET /api/books/{book_id}/pipeline` (the same field allowlist, with '
       '`validation_state`). |\n'
       '| `artifacts.jsonl` | One artifact per line, as `GET …/artifacts/{artifact_id}` returns it (metadata, '
       '`dependency_links`, `payload`), oldest first. |\n'
       '| `pipeline-events.jsonl` | Every analysis event of the book, one per line, oldest first. |\n'
       '| `resource-operations.json`, `listening-sessions.json`, `listening-takes.json`, `listening-chunks.json` | The '
       'book\'s saved rows, when those tables exist (possibly empty arrays). |\n\n'
       'The ZIP includes all retained versions belonging to the selected book and the transitive artifact '
       'dependencies needed by them, which can include source excerpts and observations from earlier books. Take '
       'metadata identifies separately stored audio assets; the ZIP excludes all audio binaries (enhanced takes, '
       'simple-listening WAVs and voice-preview audio), API keys and settings credentials. Voice-preview records are '
       'not included. Some legacy outputs lack original prompts or exact attempt provenance; the export marks that '
       'absence (`legacy_provenance`) rather than reconstructing it.\n\n'
       'No provider is contacted. ' + READ_ONLY + ' For audio, use the audiobook export '
       '(`GET /api/books/{book_id}/export`).',
       media='application/zip', ranges=True, response_description='The analysis bundle as a ZIP attachment.',
       errors={404: BOOK_404}, params={'book_id': BOOK_ID}),
]


REQUEST_DOCS: dict[str, dict[str, str]] = {
    'AnalysisRequest': {
        '__doc__': 'A classic analysis request, used both to preview (`analysis-plan`) and to start (`analyze`). Send the '
                   'same body to both.',
        'provider': '`local`, `gemini`, `openai` or `anthropic`. Omitted or null uses the saved default analysis provider. '
                    '`local` runs the free heuristic draft (no semantic discovery) and ignores `phase` and `limits`.',
        'chapter_id': 'Limit the run to this chapter of the book (must belong to it). Omitted or null: the whole book '
                      '(cloud phases skip front and back matter; the local draft covers every chapter). Profile '
                      'refinement is book-wide regardless.',
        'resume': 'Reuse validated saved units and chapter progress (default true). False re-requests work that would '
                  'otherwise be reused (retained history is kept).',
        'phase': 'Cloud phase: `scan` (default; character discovery with the fast model), `profiles` (refine profiles '
                 'from retained evidence and confirmed earlier-series context), `direct` (annotate passages and scenes '
                 'with the detailed model) or `full` (all three; its initial estimate is incomplete).',
        'limits': 'Allowances checked before every HTTP attempt. Omitted: all defaults.',
    },
    'AnalysisLimits': {
        '__doc__': 'Allowances reserved before every analysis HTTP attempt, including retries and evidence repairs. When '
                   'one would be exceeded the run stops as `budget_limited` and keeps its validated work. Request and '
                   'token caps apply to the run; the dollar guard includes prior tracked analysis for the book. Unknown '
                   'prices, or earlier attempts of unknown cost, stop a run that has a dollar guard. These limits do not '
                   'cap narration (TTS) spending and do not represent account credit. For a series run they apply '
                   'separately to each book, so the possible collection-wide spend grows with the number of books.',
        'max_requests': 'Maximum HTTP attempts in this run, 1–1,000 (default 25).',
        'max_input_tokens': 'Maximum input tokens in this run, 1,000–10,000,000 (default 1,000,000). Reported usage '
                            'counts; attempts without reported usage count their conservative reservation.',
        'max_output_tokens': 'Maximum output tokens in this run, 1,000–2,000,000 (default 100,000). Same accounting.',
        'budget_usd': 'Tracked analysis allowance for the whole book in USD, including earlier runs: greater than 0 and '
                      'at most 1,000 (default 1.0). Explicit null removes the dollar guard, leaving the request and '
                      'token caps.',
    },
}
