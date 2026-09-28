"""Contract entries for the analysis pipeline route family (bardic/pipeline/api.py).

The pipeline is a list of named steps. A run of a step produces a *step
version* (a ``pipeline_step_runs`` record grouping one immutable result per
scope). The owner, or an ``auto`` gate, accepts or rejects step versions; only
acceptance changes the book. See docs/ANALYSIS-PIPELINE.md for the semantics.
"""
from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import Field

from .base import Op, View, internal, op
from .common import Job, PipelineRunLimits, PipelineStepConfigView

TAG = 'Analysis pipeline'

StepId = str
PipelineProviderId = Literal['gemini', 'openai', 'anthropic', 'local_llm', 'booknlp', 'novel_analyzer']
Gate = Literal['auto', 'review']
Origin = Literal['run', 'baseline', 'external']
DecisionMode = Literal['user', 'auto', 'baseline', 'external']
StepRunStatus = Literal['queued', 'running', 'completed', 'failed', 'budget_limited', 'cancelled', 'interrupted']
RunStatus = Literal['queued', 'running', 'completed', 'failed', 'budget_limited', 'cancelled', 'interrupted', 'quota_limited']
VersionState = Literal['running', 'empty', 'accepted', 'partly_accepted', 'rejected', 'same_as_accepted', 'superseded', 'candidate']
StatValue = Union[int, float, str, None]

STEP_IDS = ('`structure`, `census`, `discovery`, `quotes`, `profiles` and `directing` (in pipeline order; '
            'the list is defined by the server and may grow)')


# ------------------------------------------------------------------ definitions and settings

class PipelineStepSettingsView(View):
    """The effective provider, model and gate for one step: the saved choice, or a default."""
    provider: str = Field(description='Provider ID: `local` for plain (local) steps, otherwise one of the step\'s `providers`.')
    model: str | None = Field(description='Model ID, or null for local steps, service providers (`booknlp`, `novel_analyzer`) '
                                          'and an LLM step whose default provider has no configured model. A run with a '
                                          'null model for an LLM provider is not refused here; configure one first.')
    gate: Gate = Field(description='`auto` accepts a completed run of this step immediately; `review` waits for a person.')
    saved: bool = Field(description='True when the owner saved settings for this step. False means these are computed '
                                    'defaults (the preferred analysis provider and its configured analysis or scan model). '
                                    'A saved choice that no longer validates is silently replaced by the defaults '
                                    '(but `saved` stays true).')


class PipelineStepDefinition(View):
    """One registered step (its declarative contract) with its effective settings."""
    id: StepId = Field(description=f'Stable step ID, `[a-z][a-z0-9_]{{1,39}}`. Current steps: {STEP_IDS}.')
    label: str = Field(description='Display name.')
    summary: str = Field(description='One-paragraph display description of what the step does.')
    method: Literal['plain', 'llm', 'service'] = Field(
        description='`plain`: computed locally, free. `llm`: a prompt and schema sent to a chosen model (may be billed). '
                    '`service`: a self-hosted chapter service (free).')
    scope: Literal['book', 'chapter', 'character'] = Field(
        description='Granularity at which results are versioned and accepted. Scope IDs are `book`, a chapter ID, '
                    'or a book-local character ID respectively.')
    inputs: list[StepId] = Field(description='Upstream step IDs whose accepted results this step reads (recorded for staleness).')
    requires: list[StepId] = Field(description='The subset of `inputs` that must have an accepted result (or be in the '
                                               'same run) before this step can run.')
    owns: list[str] = Field(description='Book fields this step writes on acceptance, as `collection.field` '
                                        '(`collection[]` means it may add items). No two steps own one field.')
    version: int = Field(description='Step logic version. Bumped when prompts, schemas or assembly change; recorded '
                                     'on each version as `step_version`.')
    parallel: int = Field(description='Maximum concurrent units of this step (1–8); a run\'s `concurrency` also caps it.')
    default_gate: Gate = Field(description='Gate used when none is saved or sent.')
    providers: list[str] = Field(description='Provider IDs the owner may choose: `["local"]` for plain steps, otherwise '
                                             'IDs from the top-level `providers` list.')
    offline_providers: list[str] = Field(description='Providers this step reads accepted results from instead of '
                                                     'contacting (directing\'s `booknlp`), so a run needs no key or URL for them.')
    default_model_role: Literal['scan', 'analysis'] = Field(
        description='Which configured model a new LLM step uses by default: the economy `scan` model or the `analysis` model.')
    chapter_scoped: bool = Field(description='True when a run\'s `chapter_ids` selection narrows this step\'s work.')
    capturable: bool = Field(description='True when the server can rebuild this step\'s result from the book, which '
                                         'enables `baseline`/`external` versions and rollback to them.')
    accumulative: bool = Field(description='True when accepting only adds to the book (discovery): accepting applies '
                                           'just the scopes whose version changes.')
    settings: PipelineStepSettingsView = Field(description='Effective provider, model and gate for this step.')


class PipelineProviderModel(View):
    """A curated model entry for the self-hosted LLM. Listing it does not prove the server has it loaded."""
    id: str = Field(description='Model ID to send as `model`.')
    label: str = Field(description='Display name of the model.')
    tier: str = Field(description='Rough capability tier, for example `balanced`.')
    roles: list[str] = Field(description='Where the model is suggested: `preprocess` (scan) and/or `analysis`.')
    structured_output: bool = Field(description='Whether the model supports schema-constrained output.')
    context_tokens: int = Field(description='Context window in tokens.')
    max_output_tokens: int = Field(description='Largest output in tokens.')
    input_usd_per_million: float = Field(description='Price per million input tokens in USD (0 for a self-hosted server).')
    output_usd_per_million: float = Field(description='Price per million output tokens in USD (0 for a self-hosted server).')
    availability: str = Field(description='`unverified`: taken from a curated list, not from the server.')


class PipelineProvider(View):
    """A provider a pipeline step can use. Each step lists which of these it accepts."""
    id: PipelineProviderId = Field(description='Provider ID.')
    label: str = Field(description='Display name.')
    kind: Literal['model', 'service'] = Field(description='`model`: takes a model ID (prompt and schema). `service`: a '
                                                          'self-hosted chapter service without a model choice (send `model: null`).')
    self_hosted: bool = Field(description='True for a server on the owner\'s network (`local_llm`, `booknlp`, `novel_analyzer`).')
    needs: Literal['api_key', 'url'] = Field(description='What must be configured in Settings: an API key (cloud) or a server URL.')
    configured: bool = Field(description='A key or URL is set. It does not prove the server answers or the key works.')
    has_api_key: bool = Field(description='Older name for `configured` (also true when a URL is set). Same value.')
    models: list[PipelineProviderModel] | None = Field(
        None, description='Present only for `local_llm`: curated models for the self-hosted server.')


class PipelineDefinitions(View):
    """Every step in pipeline order, every provider, and the saved per-step settings."""
    schema_version: Literal[1] = Field(description='Version of this response envelope.')
    providers: list[PipelineProvider] = Field(description='Every pipeline provider, cloud and self-hosted.')
    steps: list[PipelineStepDefinition] = Field(description='Steps in pipeline (dependency and display) order.')


# ------------------------------------------------------------------ runs and step versions

class PipelineUnitCounts(View):
    """Unit progress of one step version."""
    total: int = Field(description='Units planned.')
    done: int = Field(description='Units that produced a validated result (including cached ones).')
    cached: int = Field(description='Of `done`, units reused from the validated-unit cache without a new request.')
    failed: int = Field(description='Units that failed, were stopped by a limit or were cancelled.')


class PipelineStepRun(View):
    """A step version as stored (`pipeline_step_runs`): one execution of one step, or a captured outside state.

    Returned raw: every field below is always present unless marked optional.
    """
    id: str = Field(description='Step version ID (use as `{version_id}`).')
    book_id: str = Field(description='Book ID of the book this version belongs to.')
    run_id: str | None = Field(description='The orchestrated run that produced it, or null for a `baseline`/`external` capture.')
    step_id: StepId = Field(description='Step ID of the step that produced this version.')
    step_version: int = Field(description='The step\'s `version` when this was produced.')
    origin: Origin = Field(description='`run`: produced by a pipeline run. `baseline`: the book\'s state the first time '
                                       'the pipeline saw it. `external`: a later change made outside the pipeline (older '
                                       'analysis controls, series runs, structure repair). Captures have unknown producers.')
    provider: str | None = Field(description='Provider ID used (`local` for plain steps), or null for captures.')
    model: str | None = Field(description='Model ID, or null for plain steps, services and captures.')
    status: StepRunStatus = Field(description='`running` while it works (`queued` is never written today). `interrupted`: '
                                              'the server restarted while it ran. Captures are created `completed`.')
    inputs: dict[StepId, dict[str, str]] = Field(
        description='The accepted input versions this run read: `{input step ID: {scope: artifact ID}}`. Empty for captures.')
    chapter_ids: list[str] | None = Field(description='Sorted chapter selection for a chapter-scoped step, or null for all eligible chapters.')
    scopes: dict[str, str] = Field(description='The result: `{scope: artifact ID}` of one immutable version per scope. '
                                               'Filled when the run finishes; only scopes whose every unit validated appear.')
    unchanged_scopes: list[str] = Field(description='Scopes whose result is identical to the version already accepted '
                                                    'when this run finished (content-addressed: same artifact ID).')
    units: PipelineUnitCounts
    conflicts: list[Any] = internal('Always an empty list today; conflicts are reported by preview and accept instead.')
    error: str | None = Field(description='Human-readable failure text (secrets redacted), or null. Display only.')
    created_at: str = Field(description='ISO 8601 UTC creation time.')
    updated_at: str = Field(description='ISO 8601 UTC time of the last change.')
    completed_at: str | None = Field(description='ISO 8601 UTC time the run finished, or null (always null for captures).')
    incomplete_scopes: list[str] | None = Field(
        None, description='Scopes with at least one unit that did not validate (so no version was recorded for them). '
                          'Absent until the run finishes, on captures, on runs interrupted by a restart, and on runs '
                          'that failed before their units were assembled.')


class PipelineStepVersion(View):
    """A step version summarized for history lists, with its review state."""
    id: str = Field(description='Step version ID.')
    run_id: str | None = Field(description='The orchestrated run ID, or null for a capture.')
    step_id: StepId = Field(description='Step ID of the step that produced this version.')
    step_version: int = Field(description="The step's logic `version` when this version was produced. Compare it with "
                                          "the step definition's current `version` to spot results from older prompts "
                                          'or schemas.')
    origin: Origin = Field(description='`run` (produced by a pipeline run), `baseline` (the book state the pipeline '
                                       'first saw) or `external` (a later change made outside the pipeline). See '
                                       '`PipelineStepRun.origin`.')
    provider: str | None = Field(description='Provider ID used (`local` for plain steps), or null for '
                                             '`baseline`/`external` captures.')
    model: str | None = Field(description='Model ID used, or null for plain steps, service providers and captures.')
    status: StepRunStatus = Field(description='Execution status, as on the step run (`interrupted`: the server '
                                              'restarted while it ran). This is not the review `state`.')
    units: PipelineUnitCounts
    error: str | None = Field(description='Human-readable failure or stop reason (secrets redacted), or null when there'
                                          ' was none. Display only; do not parse.')
    created_at: str = Field(description='ISO 8601 UTC.')
    completed_at: str | None = Field(description='ISO 8601 UTC, or null.')
    chapter_ids: list[str] | None = Field(description='Sorted chapter IDs the run was limited to, or null when it '
                                                      'covered every eligible chapter (also null for captures).')
    unchanged_scopes: list[str] = Field(description='Scopes whose result was identical (the same artifact ID) to the '
                                                    'version already accepted when the run finished. Empty for captures'
                                                    ' and unfinished runs.')
    incomplete_scopes: list[str] | None = Field(description='As on the step run; null when not recorded.')
    scope_count: int = Field(description='Number of scopes with a result in this version.')
    accepted_scopes: int = Field(description='How many of those scopes are the currently accepted version.')
    state: VersionState = Field(
        description='Review state, derived in this order: `running` (queued or running); `empty` (no scope results); '
                    '`accepted` (a person or policy accepted this version and every scope is still current); '
                    '`partly_accepted` (accepted, but only some scopes are still current); `rejected`; '
                    '`same_as_accepted` (never accepted, but every scope equals the accepted content); `superseded` '
                    '(accepted earlier and since replaced, or its content was accepted through another version); '
                    '`candidate` (waiting for review). Acceptance comes from decisions, never from coincidental equality.')


class PipelineDecision(View):
    """An append-only accept or reject record."""
    id: str = Field(description='Opaque decision ID.')
    book_id: str = Field(description='Book ID the decision applies to.')
    step_id: StepId = Field(description='Step ID the decision applies to.')
    action: Literal['accept', 'reject'] = Field(description='`accept`: the listed versions became the accepted versions'
                                                            ' of their scopes and were applied to the book. `reject`: a'
                                                            ' person declined them; nothing else changed.')
    mode: DecisionMode = Field(description='Who decided: `user`, `auto` (the step\'s gate), or `baseline`/`external` '
                                           '(the server recorded the existing book state).')
    step_run_id: str | None = Field(description='The step version decided on, or null when accepting through `versions/accepted`.')
    versions: dict[str, str] = Field(description='`{scope: artifact ID}` the decision covered.')
    note: str | None = Field(description='Explanatory text on captures, otherwise null.')
    created_at: str = Field(description='ISO 8601 UTC.')


class PipelineRunOutcome(View):
    """How one requested step ended within a run."""
    status: Literal['completed', 'failed', 'budget_limited', 'cancelled', 'skipped'] = Field(
        description='`skipped`: not started, because a required input did not complete, is waiting for review, has no '
                    'accepted result, or the run stopped.')
    reason: str | None = Field(None, description='Present when `skipped`: human-readable reason.')
    step_run_id: str | None = Field(None, description='The step version created, or null if it failed before one was created. Absent when skipped.')
    scopes: int | None = Field(None, description='Number of scope versions recorded. Absent when skipped.')
    accepted: bool | None = Field(None, description='True when the `auto` gate accepted the result. Absent when skipped.')
    error: str | None = Field(None, description='Present on some failures: human-readable error text.')


class PipelineRun(View):
    """An orchestrated pipeline run (`pipeline_runs`), returned as stored.

    Created with `status: queued`; the job worker adds `started_at`, then
    `completed_at` and `outcomes` when it finishes.
    """
    id: str = Field(description='Run ID.')
    book_id: str = Field(description='Book ID the run works on.')
    job_id: str = Field(description='The `pipeline` job executing it (poll and cancel through the jobs API).')
    status: RunStatus = Field(description='`queued`, `running`, then `completed`, `failed`, `budget_limited` (a limit '
                                          'stopped it), `cancelled`, or `interrupted` (server restart, or the job ended '
                                          'before the run settled). If the job ended before work began, the run takes '
                                          'the job\'s final status (so `quota_limited` is theoretically possible).')
    steps: list[StepId] = Field(description='Requested steps, deduplicated, in pipeline order.')
    mode: Literal['serial', 'parallel'] = Field(description='`serial`: steps run one after another in pipeline order. '
                                                            '`parallel`: each step starts as soon as the in-run inputs '
                                                            'it reads have finished, so independent steps overlap.')
    chapter_ids: list[str] | None = Field(description='Sorted chapter selection, or null for all eligible chapters.')
    configs: dict[StepId, PipelineStepConfigView] = Field(description='Provider and model snapshotted per requested step.')
    gates: dict[StepId, Gate] = Field(description='Gate snapshotted per requested step.')
    concurrency: int = Field(description='Maximum model requests in flight across the run (1–4).')
    fresh: bool = Field(description='True when cached validated units were ignored and new samples requested.')
    limits: PipelineRunLimits
    step_run_ids: list[str] = Field(description='Step versions created so far, in creation order.')
    error: str | None = Field(description='Human-readable failure text, or null. Display only.')
    created_at: str = Field(description='ISO 8601 UTC.')
    updated_at: str = Field(description='ISO 8601 UTC.')
    started_at: str | None = Field(None, description='ISO 8601 UTC time the worker began. Absent while queued.')
    completed_at: str | None = Field(None, description='ISO 8601 UTC finish time. Absent until the worker finishes.')
    outcomes: dict[StepId, PipelineRunOutcome] | None = Field(
        None, description='Per-step outcome keyed by step ID. Absent until the worker finishes.')
    series_run_id: str | None = Field(
        None, description='Present only on a run started by a series run: the parent `series` job ID. Absent on runs '
                          'started from the book.')


class PipelineRunStarted(View):
    """The queued job and the run record. A queued job is not a result: poll the job."""
    job: Job = Field(description='The job of kind `pipeline` (with `run_id`, `steps` and `mode` added).')
    run: PipelineRun = Field(description='The run as created (`status: queued`, empty `step_run_ids`).')


# ------------------------------------------------------------------ book overview

class PipelineChapterRef(View):
    """A chapter of the book, for chapter selection."""
    id: str = Field(description='Opaque chapter ID, stable for the life of the book. Use it in `chapter_ids` and as a '
                                'chapter scope.')
    title: str = Field(description="The chapter's current display title.")
    kind: str = Field(description='Section kind from the structure step, for example `chapter`, `front_matter` or `section` (the default).')


class PipelineBookStep(View):
    """One step's state on this book."""
    id: StepId = Field(description='Step ID.')
    settings: PipelineStepSettingsView
    accepted_scopes: int = Field(description='Current scopes (eligible chapters for a chapter-scoped step, non-reserved '
                                             'characters for a character step) that have an accepted version.')
    has_accepted: bool = Field(description='True when any scope, current or not, has an accepted version.')
    total_scopes: int = Field(description='Number of current scopes counted as for `accepted_scopes`.')
    accepted_origins: dict[str, int] = Field(description='Count of accepted scope versions by origin (`run`, `baseline`, `external`).')
    stale_scopes: list[str] = Field(description='Accepted scopes produced by a run whose recorded input versions are no '
                                                'longer the accepted ones (or an input gained scopes). Nothing re-runs automatically.')
    pending_versions: int = Field(description='Step versions in state `candidate` among the 20 most recent.')
    latest: PipelineStepVersion | None = Field(description='The most recent step version, or null.')


class PipelineBookOverview(View):
    """Per-step state, the active run and recent runs for one book."""
    book_id: str = Field(description='Book ID of the requested book.')
    revision: int = Field(description='The book\'s current revision number.')
    steps: list[PipelineBookStep] = Field(description='Every step, in pipeline order.')
    active_run: PipelineRun | None = Field(description='A queued or running run among the 5 most recent, or null.')
    recent_runs: list[PipelineRun] = Field(description='Up to 5 most recent runs, newest first.')
    chapters: list[PipelineChapterRef] = Field(description='Every chapter in reading order (including ineligible front and back matter).')


# ------------------------------------------------------------------ plan

class PipelinePlanStep(View):
    """Known work and estimates for one step of a plan."""
    step_id: StepId = Field(description='Step ID.')
    label: str = Field(description="The step's display name.")
    method: Literal['plain', 'llm', 'service'] = Field(description='`plain` (local, free), `llm` (model requests, may '
                                                                   'be billed) or `service` (self-hosted, free).')
    provider: str = Field(description="Provider ID the plan used: the request's `configs` entry, otherwise the saved or"
                                      ' default step setting. `local` for plain steps.')
    model: str | None = Field(description='Model ID the plan used, or null for plain steps and service providers (and '
                                          'for an LLM step whose default provider has no configured model).')
    units: int = Field(description='Units planned from the currently accepted inputs.')
    cached_units: int = Field(description='Model or service units with a cached validated result (0 when `fresh`).')
    requests: int = Field(description='Model requests to send (uncached LLM units), before retries or evidence repairs.')
    service_calls: int = Field(description='Free calls to self-hosted services, not counted as model requests.')
    estimated_input_tokens: int = Field(description='Estimated input tokens of those requests.')
    output_token_allowance: int = Field(description='Sum of the output caps of those requests.')
    estimated_cost_usd: float | None = Field(description='Conservative USD estimate (input priced with a 1.25 cache-write '
                                                         'uplift, output at its cap), or null when a price is unknown.')
    inputs_pending: list[StepId] = Field(description='Inputs also requested in this plan: the estimate uses their currently '
                                                     'accepted results, and the real work depends on what the run produces.')
    missing_inputs: list[StepId] = Field(description='Required inputs with no accepted result that are not in this plan (a run would be refused).')
    scopes: int = Field(description='Number of distinct scopes the units cover.')
    note: str | None = Field(None, description='Present when `inputs_pending` is not empty: display text explaining the caveat.')


class PipelinePlan(View):
    """A read-only estimate and the fingerprint that confirms it."""
    book_id: str = Field(description='Book ID the plan is for.')
    steps: list[PipelinePlanStep] = Field(description='Requested steps in pipeline order.')
    chapter_ids: list[str] | None = Field(description='Sorted chapter selection, or null for all eligible chapters.')
    requests: int = Field(description="Sum of the steps' `requests`: model requests to send, before retries or evidence"
                                      ' repairs.')
    cached_units: int = Field(description="Sum of the steps' `cached_units` (0 when `fresh`).")
    service_calls: int = Field(description="Sum of the steps' `service_calls` (free calls to self-hosted services).")
    estimated_input_tokens: int = Field(description="Sum of the steps' `estimated_input_tokens`.")
    output_token_allowance: int = Field(description="Sum of the steps' `output_token_allowance` (output token caps).")
    estimated_cost_usd: float | None = Field(description='Sum over steps rounded to 6 decimals, or null when any step\'s cost is unknown.')
    fresh: bool = Field(description="Echo of the request's `fresh`: true when cached validated units were not counted "
                                    'as reusable. Part of the fingerprint.')
    missing_inputs: dict[StepId, list[StepId]] = Field(description='`{step: [required inputs]}` lacking an accepted result and not requested.')
    fingerprint: str = Field(description='Opaque plan identity. Send it as `expected_fingerprint` to run exactly this plan.')
    note: str = Field(description='Display text: estimates exclude retries and repairs; provider invoices are authoritative.')


# ------------------------------------------------------------------ version detail table

class PipelineResultColumn(View):
    """A column of the result table. Render `rows[key]` under `label`."""
    key: str = Field(description='Row field name.')
    label: str = Field(description='Display heading.')


class PipelineResultRow(View):
    """Common fields of every result-table row.

    The diff fields are present only when the table was compared with another
    version (`diff.compared_with` not null).
    """
    id: str = Field(description='Stable row ID used to match rows across versions (a chapter, character, passage or '
                                'candidate identity, depending on the step).')
    scope: str = Field(description='The version scope the row belongs to (`book`, a chapter ID or a character ID).')
    diff_state: Literal['added', 'changed', 'same'] | None = Field(
        None, alias='_diff', description='`added` (no row with this ID in the compared version), `changed` or `same`.')
    changed_keys: list[str] | None = Field(
        None, alias='_changed', description='Column keys whose value differs from the compared row. Absent for `added` rows.')
    previous: dict[str, Any] | None = Field(
        None, alias='_previous', description='The compared row\'s value for each changed column key (cell values are '
                                             'strings, numbers, booleans or null). Absent for `added` rows.')


class PipelineStructureRow(PipelineResultRow):
    """`structure` row: one chapter's title and kind."""
    title: str = Field(description='Chapter title recorded in this version, or empty.')
    kind: str = Field(description='Section kind, for example `chapter` or `front_matter`.')
    source: str = Field(description='Where the title came from (the chapter\'s `title_source`), or empty.')


class PipelineCensusRow(PipelineResultRow):
    """`census` row: one known character or name candidate (ID is a character ID or `candidate_<hash>`)."""
    name: str = Field(description='For a cast character, its name when the census ran (not refreshed to the current '
                                  'name); for a candidate, the name as found in the text.')
    priority: str = Field(description='Heuristic profile effort: `deep`, `standard` or `basic`.')
    mentions: int = Field(description='Name mentions in eligible chapters.')
    speech_tags: int = Field(description='Explicit speech tags naming it.')
    dialogue_turns: int = Field(description='Dialogue passages currently attributed to it.')
    chapters: int = Field(description='Eligible chapters mentioning it.')
    known: Literal['yes', 'candidate'] = Field(description='`yes` for a cast character, `candidate` for a name not in the cast.')


class PipelineDiscoveryRow(PipelineResultRow):
    """`discovery` row: one character found in one scanned range (ID `<chapter>:<range start>:<name key>`)."""
    chapter: str = Field(description='Chapter title, or the chapter ID when the chapter no longer exists.')
    name: str = Field(description='Character name as the model reported it in this range.')
    aliases: str = Field(description='Comma-separated aliases.')
    evidence: int = Field(description='Number of exact supporting quotations.')
    description: str = Field(description='Draft notes.')


class PipelineQuotesRow(PipelineResultRow):
    """`quotes` row: a quotation (ID is a passage ID) or a BookNLP character (ID `<chapter>:character:<n>`)."""
    kind: Literal['Quotation', 'Character'] = Field(description='`Quotation`: a passage BookNLP attributed (row ID is '
                                                                'the passage ID). `Character`: a character BookNLP '
                                                                'found in the chapter. Capitalized display labels.')
    text: str = Field(description='Passage text truncated to 160 characters, or a character summary.')
    booknlp: str = Field(description='BookNLP\'s speaker or character name.')
    current: str = Field(description='The book\'s current speaker (or matched cast character) name.')
    check: str = Field(description='Display label of the comparison, for example `Agrees`, `Differs`, `In cast`.')
    tag: str = Field(description='The speech tag or action beat text beside the quotation, or empty.')
    conflict: bool = Field(description='True when BookNLP\'s own tag contradicts its speaker.')


class PipelineProfilesRow(PipelineResultRow):
    """`profiles` row: one character's profile (ID is the character ID)."""
    name: str = Field(description="The character's current name, or its character ID when it is no longer in the cast.")
    priority: str = Field(description='Profile effort tier, or empty.')
    description: str = Field(description='Character description (profile text) in this version, or empty.')
    direction: str = Field(description='Voice direction.')
    evidence: int = Field(description='Number of supporting quotations.')
    edited: str = Field(description='Comma-separated fields a person edited (`description`, `direction`); those keep their value whichever version is accepted.')


class PipelineDirectingRow(PipelineResultRow):
    """`directing` row: one passage (ID is the passage ID)."""
    scene: str = Field(description='Scene title, or empty.')
    kind: str = Field(description='Passage kind, for example `dialogue` or `narration`.')
    text: str = Field(description='Passage text truncated to 160 characters.')
    speaker: str = Field(description='Proposed speaker\'s name (or ID when not in the cast).')
    confidence: float | None = Field(description='Proposed speaker confidence 0–1, or null.')
    direction: str = Field(description='Delivery note.')
    cues: str = Field(description='Comma-separated vocal cues.')
    check: str = Field(description='BookNLP check label (with BookNLP\'s speaker when it differs or suggests), or empty.')
    edited: str = Field(description='Comma-separated fields a person edited (`speaker`, `direction`, `cues`); those keep their value.')


PipelineResultRowAny = Union[PipelineDirectingRow, PipelineQuotesRow, PipelineProfilesRow, PipelineDiscoveryRow,
                             PipelineCensusRow, PipelineStructureRow]


class PipelineVersionDiff(View):
    """Row-level comparison counts over the whole table (before `scope`/`changed_only` filters)."""
    compared_with: str | None = Field(description='The `compare` value used, or null when nothing was compared (compare '
                                                  '`none`, compare equal to the version, or the compared version is empty, '
                                                  'for example when nothing is accepted yet).')
    same: int = Field(description='Rows present in both versions with equal values in every column. 0 when nothing was '
                                  'compared.')
    changed: int = Field(description='Rows present in both versions with at least one differing column value. 0 when '
                                     'nothing was compared.')
    added: int = Field(description='Rows of this version with no row of the same ID in the compared version. 0 when '
                                   'nothing was compared.')
    removed: int = Field(description='Rows of the compared version with no matching row ID here (not returned as rows).')
    agreement: float | None = Field(None, description='`same / (same + changed)` rounded to 4 decimals, or null when no '
                                                      'rows matched. Absent when nothing was compared.')


class PipelineVersionScope(View):
    """One scope of the displayed version."""
    scope: str = Field(description='Scope ID: `book`, a chapter ID or a book-local character ID, depending on the '
                                   "step's `scope`.")
    artifact_id: str = Field(description='Immutable version artifact ID (inspectable through the artifacts API).')
    accepted: bool = Field(description='True when this artifact is the currently accepted version of the scope.')


class PipelineVersionDetail(View):
    """A step version's result as a generic table, diffed by row ID against another version.

    `stats`, `columns` and `rows` are produced by the step. Clients should
    render generically: show `stats` as label/value pairs and each column's
    `key` from every row; per-step row shapes are listed in `rows`.
    """
    step_id: StepId = Field(description='Step ID.')
    version_id: str = Field(description='The requested `{version_id}` (a step version ID or `accepted`).')
    run: PipelineStepRun | None = Field(description='The step version record, or null for `accepted`.')
    stats: dict[str, StatValue] = Field(
        description='Step-defined summary numbers for display, keyed by name. structure: `sections`, one count per '
                    'section kind, `structure_version`. census: `words`, `eligible_sections`, `name_candidates`, '
                    '`estimated_source_tokens`. discovery: `sections`, `mentions`, `distinct_names`, `ranges`. quotes: '
                    '`sections`, `quotations`, `agrees`, `differs`, `suggests_speaker`, `not_in_cast`, `tag_conflicts`, '
                    '`unmatched_quotations`, `dialogue_without_quotation`, `agreement` (0–1 or null). profiles: '
                    '`profiles`, `refined`. directing: `sections`, `scenes`, `passages`, `dialogue`, `unassigned_dialogue`, '
                    '`attributed_dialogue`, `same_speaker_as_book`, and with an accepted BookNLP check `booknlp_agrees`, '
                    '`booknlp_differs`, `booknlp_suggests`.')
    columns: list[PipelineResultColumn] = Field(description='Columns to display, in order.')
    diff: PipelineVersionDiff
    total_rows: int = Field(description='Rows after the `scope` and `changed_only` filters, before paging.')
    offset: int = Field(description='Echo of the `offset` query parameter: rows skipped.')
    limit: int = Field(description='Echo of the `limit` query parameter: the page size.')
    rows: list[PipelineResultRowAny] = Field(
        description='The requested page of rows. Most cell values reflect the book\'s current names and passages; census rows use the names stored in the result and structure rows use the version\'s own titles. The row '
                    'shape depends on the step (one variant per step); every row has `id` and `scope`.')
    scopes: list[PipelineVersionScope] = Field(description='Every scope of the displayed version.')
    revision: int = Field(description='The book\'s current revision.')


# ------------------------------------------------------------------ preview, accept, reject

class PipelineConflict(View):
    """A generated value that was not applied, or an input that could not be."""
    scope: str = Field(description='Scope the value belongs to: `book`, a chapter ID or a book-local character ID.')
    item_id: str = Field(description='The character, passage, scene or candidate name concerned.')
    field: str = Field(description='The field kept, for example `description`, `speaker_id`, `scene_breaks`, `identity`, `character`, `passage`.')
    reason: str = Field(description='Human-readable explanation.')


class PipelineAcceptImpact(View):
    """What accepting the selected scopes changes (preview) or changed (accept)."""
    step_id: StepId = Field(description='Step ID whose versions are previewed or accepted.')
    changed_scopes: list[str] = Field(description='Selected scopes whose accepted version changes.')
    unchanged_scopes: list[str] = Field(description='Selected scopes already accepted with this exact version.')
    conflicts: list[PipelineConflict] = Field(description='Generated values not applied, mostly because a person edited the field.')
    audio_takes_invalidated: int = Field(description='Narration takes valid before and not after (they are cleared on accept).')
    downstream_steps_affected: list[StepId] = Field(description='Steps that read this one (transitively) and have accepted '
                                                                'versions; they may become stale. Empty when nothing changes.')
    revision: int = Field(description='Preview: the book\'s current revision (send it as `expected_revision`). Accept: the new revision.')


class PipelineAcceptResult(PipelineAcceptImpact):
    """The applied impact and the decision recorded."""
    decision: PipelineDecision


class PipelineVersionHistory(View):
    """A step's version history and recent decisions."""
    step_id: StepId = Field(description='Step ID.')
    items: list[PipelineStepVersion] = Field(description='Newest first, at most `limit`.')
    decisions: list[PipelineDecision] = Field(description='Up to 50 most recent decisions for this step, newest first.')


# ------------------------------------------------------------------ operations

BOOK = 'Book ID.'
STEP = f'Step ID: one of {STEP_IDS}. An unknown ID returns 404.'
VERSION = ('A step version ID from the version history, or `accepted` to address the currently accepted version of '
           'every scope.')
NO_BOOK = 'The book does not exist (`Book not found`).'
NO_STEP = 'The step ID is unknown (`Unknown pipeline step: …`).'
NO_VERSION = ('The book or step is unknown, or the version does not exist, belongs to another book or belongs to another step.')

SYNC_NOTE = (
    'Before answering, the server records outside changes (`projection.sync`): when the capturable content of the '
    'book no longer matches what the accepted versions explain, it stores the current state as new `baseline` (first '
    'time) or `external` versions and accepts them (decision modes `baseline`/`external`). The book itself is not '
    'changed. This is skipped cheaply when a digest of the captured content is unchanged.')

VALIDATE_CONFIG = ('For a local (plain) step only `provider: "local"` with no model is accepted. A service provider '
                   '(`booknlp`, `novel_analyzer`) takes no model. A model provider needs a model ID matching '
                   '`[A-Za-z0-9][A-Za-z0-9._:-]{0,199}`.')

OPS: list[Op] = [
    op('GET', '/api/analysis-pipeline', 'getAnalysisPipeline', TAG,
       'List pipeline steps, providers and saved step settings',
       'Step definitions in pipeline order, each listing its allowed `providers` and its effective `settings` '
       '(`{provider, model, gate, saved}`), and every provider with `kind` (`model` or `service`), `self_hosted`, '
       '`needs` (`api_key` or `url`) and `configured`/`has_api_key` (a key or URL is set; not a reachability check). '
       'The Local LLM entry lists curated `models`. Service providers take `model: null`.\n\n'
       'Read-only; contacts no server.',
       response=PipelineDefinitions),
    op('PUT', '/api/analysis-pipeline/steps/{step_id}/settings', 'saveAnalysisPipelineStepSettings', TAG,
       'Save a step\'s provider, model and gate',
       'Saves the library-wide default provider/model and gate (`auto` or `review`) for one step, replacing any '
       f'previous choice. {VALIDATE_CONFIG} An omitted or null `gate` saves the step\'s `default_gate`. Idempotent. '
       'Never starts work and contacts no server. Returns the effective settings for the step.',
       response=PipelineStepSettingsView,
       errors={400: 'The provider is not allowed for this step, a local step was given a model or another provider, '
                    'a service provider was given a model, or the model ID is missing or malformed.',
               404: NO_STEP},
       params={'step_id': STEP}),
    op('GET', '/api/books/{book_id}/analysis-pipeline', 'getBookAnalysisPipeline', TAG,
       'Get the pipeline state of a book',
       'Per-step accepted and total scopes, `has_accepted` (any accepted version), accepted origins, stale scopes, '
       'pending candidates and the latest version; the active run and the 5 most recent runs; and the chapter list.\n\n'
       f'**This GET writes.** {SYNC_NOTE} Contacts no server.',
       response=PipelineBookOverview,
       errors={404: NO_BOOK},
       params={'book_id': BOOK}),
    op('POST', '/api/books/{book_id}/analysis-pipeline/plan', 'planBookAnalysisPipelineRun', TAG,
       'Preview the work and cost of a run',
       'Builds each requested step\'s units from the currently accepted inputs (steps run in pipeline order '
       'whatever the request order) and reports units, cached units, model `requests`, `service_calls` (free calls '
       'to self-hosted services, not counted as model requests), token and cost estimates, `inputs_pending`, '
       '`missing_inputs` (per step, and `{step: [inputs]}` overall) and a `fingerprint`. No model or service '
       'calls. Estimates cover known work before retries or evidence repairs; a step whose input is in the same '
       'request is estimated from the input\'s current accepted result.\n\n'
       'Missing inputs do not fail the plan (they are reported); a run with them is refused. Omitted `configs` '
       'entries use the saved step settings.\n\n'
       'The `fingerprint` covers the book revision, the chapter selection, `fresh`, and each step\'s version, '
       'provider, model and exact unit identities. It does not cover `mode`, `gates`, `concurrency`, `limits` or '
       'which units are cached. Send the same `steps`, `chapter_ids`, `configs` and `fresh` to the run, because '
       'they are part of the fingerprint.\n\n'
       f'Not purely read-only: {SYNC_NOTE} Building units may also store free local census caches.',
       response=PipelinePlan,
       errors={400: 'A `configs` entry is invalid for its step, `chapter_ids` is empty or names a chapter not in this '
                    'book, or a step could not plan its units.',
               404: 'The book does not exist, or `steps` names an unknown step (an unknown step ID in the body is a 404, not a 400).'},
       params={'book_id': BOOK}),
    op('POST', '/api/books/{book_id}/analysis-pipeline/runs', 'startBookAnalysisPipelineRun', TAG,
       'Queue a pipeline run',
       'Queues one job of kind `pipeline` running the requested steps and returns `{job, run}` immediately. Follow '
       'the job through `GET /api/jobs` and cancel it through the jobs API; the run record appears in this book\'s '
       'pipeline overview. A run never writes the book: it records candidate versions, and a step whose gate is '
       '`auto` is accepted when it completes (decision mode `auto`). Steps in the same run that require a step '
       'left for review, failed or without an accepted result are skipped.\n\n'
       'Checks, in order: every step ID must be known (404 otherwise, before anything else); the book must exist, not be removed, and have no active job (and not be reserved by an '
       'active series run); `chapter_ids` and `configs` must be valid; every provider the run contacts must have an '
       'API key or server URL configured (local steps and `offline_providers` need none); every step\'s required '
       'inputs must have an accepted result or be in the same run; and the run must be authorized by either '
       '`expected_fingerprint` (a confirmed plan) or at least one explicit limit. When `expected_fingerprint` is '
       'sent, the plan is recomputed and must match.\n\n'
       'Provider keys and server URLs, per-step provider/model and gates are snapshotted now; later settings '
       'changes do not affect queued work. `limits` is optional and uncapped by default: the confirmed plan is the '
       'authorization. Every paid attempt is reserved and recorded either way; each unit has at most four HTTP '
       'attempts (two transport attempts for each of at most two generations), and validated units are cached and '
       'reused unless `fresh`. '
       f'{SYNC_NOTE}',
       response=PipelineRunStarted,
       response_description='The queued job and run. Not a result: poll the job until it is terminal.',
       errors={400: 'The book is removed (restore it first); `chapter_ids` is empty or names a chapter not in this book; '
                    'a `configs` entry is invalid; a needed API key or server URL is missing (`Add in Settings first: …`); '
                    'a step\'s required input has no accepted result and is not in this run; or neither '
                    '`expected_fingerprint` nor any limit was sent.',
               404: 'The book does not exist, or `steps` names an unknown step.',
               409: 'A job is already working on this book, the book is reserved by an active series run, or the plan '
                    'changed since the preview (`expected_fingerprint` does not match; preview again).'},
       params={'book_id': BOOK},
       cost='may_charge'),
    op('GET', '/api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions', 'listAnalysisPipelineStepVersions', TAG,
       'List a step\'s versions and decisions',
       'Version history, newest first, with each version\'s review `state` (`candidate`, `accepted`, '
       '`partly_accepted`, `superseded`, `same_as_accepted`, `rejected`, `running`, `empty`) and the 50 most recent '
       'decisions. Includes `baseline`/`external` captures. Read-only (does not record outside changes).',
       response=PipelineVersionHistory,
       errors={404: 'The book does not exist, or the step ID is unknown.'},
       params={'book_id': BOOK, 'step_id': STEP,
               'limit': 'Maximum versions to return. Default 50; values are clamped to 1–200 (never an error).'}),
    op('GET', '/api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions/{version_id}',
       'getAnalysisPipelineStepVersion', TAG,
       'Get a version\'s result table, diffed against another version',
       'The step\'s generic result table (`stats`, `columns`, paged `rows`) for a version, diffed by row ID against '
       '`compare` (`accepted`, another version, or `none`), with `changed_only` and `scope` filters. `{version_id}` '
       'may be `accepted`. With a comparison, each row gains `_diff` (`added`, `changed` or `same`) and, unless '
       'added, `_changed` (changed column keys) and `_previous` (the compared values of those keys); rows only in the '
       'compared version are counted as `removed` but not returned. `diff` reports `same/changed/added/removed` and '
       'an `agreement` ratio, a cheap signal when comparing models. No comparison happens when `compare` is `none`, '
       'equals `{version_id}`, or resolves to no results (for example `accepted` when nothing is accepted); then '
       '`diff.compared_with` is null and rows carry no diff fields.\n\n'
       'Rows are summarized against the book\'s current state (current names and passage text). A version still '
       'running has no scopes yet and returns an empty table. Read-only.',
       response=PipelineVersionDetail,
       errors={400: 'The offset is negative or the page size is outside 1–1000.',
               404: NO_VERSION + ' Also when `compare` names a version that does not exist or belongs to another step.'},
       params={'book_id': BOOK, 'step_id': STEP, 'version_id': VERSION,
               'compare': 'What to diff against: `accepted` (default), another step version ID of this step, or `none`.',
               'scope': 'Return only rows of this scope (a chapter ID, character ID or `book`). Filters rows, not `diff` counts.',
               'changed_only': 'When true, return only rows whose `_diff` is `changed` or `added` (none without a comparison). Default false.',
               'offset': 'Rows to skip (default 0, must be nonnegative).',
               'limit': 'Page size, 1–1000 (default 200).'}),
    op('POST', '/api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions/{version_id}/preview',
       'previewAnalysisPipelineStepVersion', TAG,
       'Preview what accepting a version would change',
       '`{scopes?}` → changed and unchanged scopes, `conflicts` with manual edits (generated values that will not '
       'be applied because a person edited the field), narration takes that would be invalidated, downstream steps '
       'with accepted versions, and the book `revision` to send as `expected_revision` when accepting. '
       '`expected_revision` is ignored here. Omitted `scopes` means every scope of the version. For an accumulative '
       'step (discovery) only changed scopes are applied. Allowed on a running or empty version (the result is then '
       'empty) and on a removed book.\n\n'
       f'Not purely read-only: {SYNC_NOTE}',
       response=PipelineAcceptImpact,
       errors={400: '`scopes` is empty or names a scope this version does not contain, or a selected result does not '
                    'fit the book (for example a structure version for different chapters).',
               404: NO_VERSION},
       params={'book_id': BOOK, 'step_id': STEP, 'version_id': VERSION}),
    op('POST', '/api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions/{version_id}/accept',
       'acceptAnalysisPipelineStepVersion', TAG,
       'Accept a version (also used to roll back)',
       '`{scopes?, expected_revision?}`. Accepting an older version is rollback. In one short transaction: records '
       'outside changes, computes the impact, appends a `user` decision and moves the accepted heads, applies the '
       'step\'s accepted versions to the book (manual edits are field-level locks and are kept, reported as '
       '`conflicts`), gives a default device voice only to characters this acceptance adds, clears only the '
       'narration takes that were valid before and are not after, increments the book revision and saves. Accepting '
       'a model step\'s run version also sets the book\'s analysis summary (provider, model, `partial`). Omitted '
       '`scopes` means every scope of the version. Accepting `accepted` re-applies the current versions and still '
       'bumps the revision.\n\n'
       'Send `expected_revision` (from preview) to refuse the accept when the book changed after the preview. A '
       'running `pipeline` job on the book does not block accepting; other active jobs do.',
       response=PipelineAcceptResult,
       errors={400: 'The book is removed (restore it first); the version has no results; `scopes` is empty or names a '
                    'scope this version does not contain; or a selected result does not fit the book.',
               404: NO_VERSION,
               409: 'The version is still running; another (non-pipeline) job is changing this book; the book is '
                    'reserved by an active series run (unless that run is paused waiting for your review of this '
                    'book); or the book revision differs from `expected_revision` (review the impact again).'},
       params={'book_id': BOOK, 'step_id': STEP, 'version_id': VERSION}),
    op('POST', '/api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions/{version_id}/reject',
       'rejectAnalysisPipelineStepVersion', TAG,
       'Reject a candidate version',
       '`{scopes?}` → the appended `reject` decision (mode `user`). Records a decision only; the book, accepted '
       'versions and retained results are unchanged, and the version remains inspectable. An accepted version '
       'cannot be rejected; accept another version to replace it. Because identical results share one artifact, a '
       'candidate whose selected scopes equal the accepted content (`same_as_accepted`) cannot be rejected either. '
       'Omitted `scopes` means every scope of the version; `expected_revision` is ignored. Not refused while jobs '
       'run or when the book is removed; the book\'s existence is not checked separately.',
       response=PipelineDecision,
       errors={400: '`{version_id}` is `accepted`; the version has no results; `scopes` is empty or names a scope '
                    'this version does not contain; or a selected scope is the currently accepted version.',
               404: 'The step is unknown, or the version does not exist, belongs to another book or belongs to another step.',
               409: 'The version is still running.'},
       params={'book_id': BOOK, 'step_id': STEP, 'version_id': VERSION}),
]

REQUEST_DOCS: dict[str, dict[str, str]] = {
    'StepConfig': {
        '__doc__': f'A provider and model for one step, overriding its saved settings for this request. {VALIDATE_CONFIG}',
        'provider': 'Provider ID: `local` for plain steps, otherwise one of the step\'s `providers` (1–40 characters).',
        'model': 'Model ID for a model provider (up to 200 characters); omit or null for local steps and service providers.',
    },
    'StepSettings': {
        '__doc__': f'A step\'s saved provider, model and gate. {VALIDATE_CONFIG}',
        'provider': 'Provider ID: `local` for plain steps, otherwise one of the step\'s `providers`.',
        'model': 'Model ID for a model provider; omit or null for local steps and service providers.',
        'gate': '`auto` accepts a completed run immediately; `review` waits for a person. Omitted or null saves the step\'s `default_gate`.',
    },
    'Limits': {
        '__doc__': 'Optional caps for API callers, all null (uncapped) by default. The Analysis tab sends none: the '
                   'confirmed plan (`expected_fingerprint`) is the authorization, and a run with neither is refused. '
                   'Every attempt is reserved and recorded either way. Setting any limit authorizes a run without a '
                   'fingerprint. A limit reached stops the run as `budget_limited` and keeps completed work.',
        'max_requests': 'HTTP attempts allowed in this run, including retries and evidence repairs (1–1000).',
        'max_input_tokens': 'Input tokens allowed in this run, counting reservations for attempts without reported usage (1000–10,000,000).',
        'max_output_tokens': 'Output tokens allowed in this run, counting reservations (1000–2,000,000).',
        'budget_usd': 'Cumulative USD guard across every tracked attempt for this book, including earlier runs '
                      '(greater than 0, at most 1000). Self-hosted models are priced at 0.',
    },
    'PlanRequest': {
        '__doc__': 'Which steps to estimate, over which chapters, with which providers.',
        'steps': 'Step IDs to plan (1–40). Order does not matter: steps are planned in pipeline order. Duplicates are '
                 'ignored. An unknown ID returns 404.',
        'chapter_ids': 'Chapters to limit chapter-scoped steps to (1–2000 IDs of this book; other steps ignore it). '
                       'Omit or null for every eligible (story) chapter. An empty list is refused.',
        'configs': '`{step ID: StepConfig}` overriding the saved provider/model for this request. Entries for steps not '
                   'requested are ignored.',
        'fresh': 'When true, cached validated units are not reused: new samples are requested (for comparing a model '
                 'with itself). Part of the plan fingerprint. Default false.',
    },
    'RunRequest': {
        '__doc__': 'A run to queue. Send the same `steps`, `chapter_ids`, `configs` and `fresh` as the confirmed plan.',
        'steps': 'Step IDs to run (1–40), executed in pipeline order. Duplicates are ignored. An unknown ID returns 404.',
        'chapter_ids': 'Chapters to limit chapter-scoped steps to (1–2000 IDs of this book). Omit or null for every '
                       'eligible chapter. An empty list is refused.',
        'configs': '`{step ID: StepConfig}` overriding the saved provider/model. Entries for steps not requested are ignored.',
        'fresh': 'Request new samples instead of reusing cached validated units (default false). Part of the fingerprint.',
        'mode': '`serial` (default) runs steps one after another in pipeline order. `parallel` starts every step whose '
                'in-run inputs have finished, so independent steps overlap.',
        'gates': '`{step ID: "auto" | "review"}` overriding the saved gate for this run.',
        'concurrency': 'Maximum model requests in flight across the run, 1–4 (default 2). Each step also has its own `parallel` cap.',
        'limits': 'Optional caps; see Limits. Uncapped when omitted.',
        'expected_fingerprint': 'The `fingerprint` of the plan the owner confirmed (up to 64 characters). When sent, the '
                                'plan is recomputed and a mismatch returns 409. Required unless a limit is set.',
    },
    'DecisionRequest': {
        '__doc__': 'Which scopes of a version to preview, accept or reject.',
        'scopes': 'Scope IDs of the version to act on (at most 5000). Omit or null for every scope of the version. An '
                  'empty list, or a scope the version does not contain, is refused (400).',
        'expected_revision': 'Accept only: the `revision` returned by preview. The accept is refused (409) when the book '
                             'revision differs. Ignored by preview and reject.',
    },
}
