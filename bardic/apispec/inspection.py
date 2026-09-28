"""Contract entries for the inspection route family.

Read-only views of pipeline stages, retained artifacts, the story map,
passage search, resource usage and the portable analysis export.
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


class AnalysisUsage(View):
    """Tracked analysis request usage for a book, across all runs, including runs of the removed Classic engine."""
    attempts: int = Field(description='Recorded analysis HTTP attempts, including failed and repair attempts.')
    input_tokens: int = Field(description='Sum of reported input tokens. Attempts without reported usage add 0 here; see '
                                          '`unknown_usage_attempts`.')
    output_tokens: int = Field(description='Sum of reported output tokens (same caveat).')
    estimated_spend_usd: float = Field(description='Sum of the conservative per-attempt estimates in USD (dated prices, guard '
                                                   'uplift, reservations when usage is missing). Not an invoice.')
    unknown_cost_attempts: int = Field(description='Attempts whose cost is unknown. Unknown is not zero.')
    unknown_usage_attempts: int = Field(description='Attempts missing input or output token counts.')
    note: str = Field(description='Interpretation caveat. Display only.')


# ------------------------------------------------------------------ inspection

class PipelineStage(View):
    """One stage card of the pipeline inspector. Counts have stage-specific units; never sum them.

    Cards come in pipeline order: `import` and `series`, then one card per
    analysis step (the step IDs of `GET /api/analysis-pipeline`, currently
    `structure`, `census`, `discovery`, `quotes`, `profiles`, `directing`),
    then `voices`, `narration`, `alignment` and `export`. A step card counts
    accepted results of the step pipeline, the same numbers as
    `GET /api/books/{book_id}/analysis-pipeline`.
    """
    id: str = Field(description='Stage ID: `import`, `series`, a pipeline step ID, `voices`, `narration`, `alignment` or '
                                '`export`. Clients must tolerate new step IDs.')
    label: str = Field(description='Display name. For a step card, the step\'s label.')
    status: Literal['complete', 'partial', 'pending', 'stale', 'available', 'not_started', 'planned', 'ready',
                    'queued', 'running'] = Field(
        description='`complete`/`partial`/`pending` from the counts. A step card is `queued`/`running` while its latest '
                    'step version is, and otherwise `stale` when some accepted result is out of date with its inputs. '
                    '`narration` shows an active `render` job\'s status. Fixed states: `series` is `available` (book is in '
                    'a series) or `not_started`; `alignment` is `planned`; `export` is `ready`.')
    completed: int | None = Field(description='Units done, or null where not counted (`series`, `export`). For a step card, '
                                              'accepted scopes.')
    total: int | None = Field(description='Units in scope, or null where not counted. For a step card, its scopes; '
                                          'chapter-scoped steps count only eligible sections.')
    unit_label: str = Field(description='What the counts measure, e.g. `sections`, `eligible sections`, `book result`, '
                                        '`profiles`, `passages`.')
    dependencies: list[str] = Field(description='Stage IDs this stage conceptually depends on. For a step card, the step\'s '
                                                'declared inputs, or `import` when it has none. Descriptive, not a scheduler.')
    artifact_count: int = Field(description='Retained artifact versions whose stage equals this ID, including versions from '
                                            'older engines that used the same stage name.')
    stale_count: int | None = Field(description='Step cards: accepted scopes whose inputs changed since acceptance. Null for '
                                                'other cards.')
    candidate_count: int | None = Field(description='Step cards: recent step versions (of the newest 20) waiting for review. '
                                                    'Null for other cards.')
    note: str = Field(description='Interpretation text. Display only.')


class PipelineAttempt(View):
    """One recorded analysis HTTP attempt, newest 100 for the book (step pipeline, or the removed Classic engine).

    The pipeline inspector lists the newest 100 for the book; the analysis
    export's `analysis-attempts.json` lists all of them in this same shape.
    Fields come from the stored attempt through a fixed allowlist and may be
    absent on records from older versions. Prompts, responses, credentials
    and server process IDs are never included.
    """
    id: str = Field(description='Attempt ID.')
    book_id: str | None = Field(default=None, description='Book the attempt was made for.')
    run_id: str | None = Field(default=None, description='Job ID of the run that sent it.')
    stage: str | None = Field(default=None, description='Pipeline step ID, or a stage of the removed Classic engine (`discovery`, `profiles`, `directing`) on older records.')
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
    stage: str | None = Field(description='Pipeline step ID, or a stage of the removed Classic engine on older records.')
    unit_key: str | None = Field(description='Opaque unit cache key.')
    event: Literal['started', 'accepted', 'cache_hit', 'cache_rejected', 'cache_superseded', 'validation_rejected',
                   'budget_limited', 'failed', 'cancelled'] = Field(description='What happened to the unit.')
    created_at: str = Field(description='ISO 8601 UTC.')
    artifact_id: str | None = Field(default=None, description='Related artifact: request recipe (`started`), accepted output, '
                                                             'cached output or rejection record.')
    attempt_id: str | None = Field(default=None, description='Related HTTP attempt, when known.')
    error: str | None = Field(default=None, description='Redacted failure or rejection text. Display only.')
    cached_unit_key: str | None = Field(default=None, description='For `cache_rejected` in older Classic runs: the unit key of the rejected cache entry.')
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
    references: list[StoryMapReference] = Field(description='All saved character references for the book, unpaginated. '
                                                             'These are the stored rows as the last pipeline write '
                                                             'recorded them; `listCharacterReferences` shows the '
                                                             'projection of the current book.')
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

OPS: list[Op] = [
    op('GET', '/api/books/{book_id}/pipeline', 'getPipelineInspector', 'Inspection',
       'Inspect the processing pipeline',
       'Versioned envelope with stage IDs, status, counts and dependencies; retained artifact counts and kinds; '
       'the book\'s recent jobs; the newest 100 analysis attempts with their validation state; the newest 100 '
       'events; tracked usage; capabilities; and notes. Word alignment is explicitly planned (not implemented).\n\n'
       'The pipeline is an inspector, not a generic dependency scheduler. Its stage counts have different units '
       'and must not be summed into a global completion percentage. An HTTP 200 attempt does not mean its output '
       'passed validation: use `validation_state`.\n\n'
       'Step cards count the step pipeline\'s accepted, out-of-date (`stale_count`) and waiting (`candidate_count`) '
       'results, as `GET /api/books/{book_id}/analysis-pipeline` does: outside changes are taken into account as the '
       'next pipeline write would record them, but nothing is recorded. Artifact counts include retained versions '
       'from the removed Classic engine.\n\n'
       'No provider is contacted. ' + READ_ONLY + ' Legacy data from versions before artifacts existed is retained as '
       'artifacts (marked `legacy_provenance`) once, when the server starts, not by this request.',
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


REQUEST_DOCS: dict[str, dict[str, str]] = {}
