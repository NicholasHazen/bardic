"""Contract entries for the system route family: status, settings, provider checks, diagnostics and jobs."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import Op, View, internal, op
from .common import TIME, Job
from .media import ChapterListenChunking, ChapterListenLimits, ChapterListenQuota
from .voices import VoiceLibraryBreezeStatus, VoiceLibraryDefaults, VoiceLibrarySystemVoice

CloudProvider = Literal['gemini', 'openai', 'anthropic']
AnalysisProviderId = Literal['local', 'gemini', 'openai', 'anthropic']


# ---------------------------------------------------------------- provider catalogs

class AnalysisCatalogModel(View):
    """One analysis model choice for a cloud provider.

    Curated entries carry dated published prices; models found only by a
    refresh have unknown capabilities and prices (null). Prices are list
    prices for standard text requests, not a bill.
    """
    id: str = Field(description='Model ID to save in `analysis_models_by_provider` or `preprocess_models_by_provider`.')
    label: str = Field(description='Display name (the ID for models found only by a refresh).')
    tier: Literal['balanced', 'economy', 'deep', 'other'] = Field(
        description='Rough capability/cost tier; `other` for models found only by a refresh.')
    roles: list[Literal['preprocess', 'analysis']] = Field(description='Where the model may be chosen.')
    structured_output: bool | None = Field(
        description='Whether JSON-schema output is supported; null when unknown (a refreshed model that does not advertise it).')
    context_tokens: int | None = Field(description='Context window in tokens, or null when unknown.')
    max_output_tokens: int | None = Field(description='Largest output in tokens, or null when unknown.')
    input_usd_per_million: float | None = Field(
        description='Published USD price per million input tokens, or null when unknown or not quoted '
                    '(for example a model whose price depends on prompt length).')
    output_usd_per_million: float | None = Field(description='Published USD price per million output tokens, or null.')
    source_url: str = Field(description='Provider documentation page for the model list.')
    pricing_source_url: str | None = Field(description='Provider pricing page the prices came from, or null.')
    price_date: str | None = Field(description='Date (YYYY-MM-DD) the prices were recorded, or null.')
    availability: Literal['unverified', 'listed', 'not_listed'] = Field(
        description='`unverified`: from the curated list, not checked (or the refresh listing was partial). '
                    '`listed`: the provider listed it for this key in the last refresh. `not_listed`: a curated model '
                    'the complete listing did not include. Listing proves visibility only, not working generation or credit.')
    price_valid_until: str | None = Field(
        None, description='Last date (YYYY-MM-DD) the quoted price applies; absent when open-ended. Cost estimates treat '
                          'the price as unknown after it.')
    price_input_token_limit: int | None = Field(
        None, description='Prompt size in tokens above which the quoted price does not apply; absent when none.')


class AnalysisModelCatalog(View):
    """Analysis model choices for one cloud provider: the curated list, or the last refresh for the current key."""
    provider: CloudProvider = Field(description='Cloud analysis provider this catalog lists: `gemini`, `openai` or `anthropic`.')
    state: Literal['curated', 'ready', 'missing_key', 'invalid_key', 'refreshing', 'access_denied', 'rate_limited',
                   'unavailable'] = Field(
        description='`curated`: documented choices only (never refreshed for this key). `ready`: the provider listing '
                    'succeeded and is merged in. `missing_key` / `invalid_key`: no request was sent. `refreshing`: '
                    'another refresh for this provider is running. `access_denied`, `rate_limited`, `unavailable`: '
                    'the listing failed; the curated choices remain usable.')
    catalog_date: str = Field(description='Date (YYYY-MM-DD) of the curated list.')
    message: str = Field(description='Human-readable explanation. Display only.')
    checked_at: str | None = Field(description='When the last refresh for this key finished, or null. ' + TIME)
    cached: bool = Field(
        description='True when a refresh returned a recent result without contacting the provider (successful '
                    'listings are reused for an hour, failures for 30 seconds).')
    source_url: str = Field(description='Provider documentation page for its models.')
    models: list[AnalysisCatalogModel] = Field(
        description='Curated models first (in curated order), then models only the listing found, sorted by ID.')
    partial: bool | None = Field(
        None, description='Present after a successful refresh: true when the listing had more pages than were read, '
                          'so unlisted curated models stay `unverified`.')


# ---------------------------------------------------------------- account checks

class AccountCheckUsage(View):
    """Token usage the provider reported for the check request. Absent counts were not reported."""
    input_tokens: int | None = Field(None, description='Input tokens.')
    output_tokens: int | None = Field(None, description='Output tokens.')
    total_tokens: int | None = Field(None, description='Total tokens (computed from input and output when not reported).')


class AccountCheck(View):
    """The result of the last explicit account check for one cloud analysis provider.

    A check sends one tiny text request (`Reply with only the word OK.`, at
    most 128 output tokens) to the selected analysis model. It shows whether
    that key and model can generate text now. It does not report a balance,
    remaining credit or narration access.
    """
    provider: CloudProvider = Field(description='Cloud analysis provider this check is for: `gemini`, `openai` or `anthropic`.')
    model: str = Field(description='The analysis model the check uses (the saved choice for this provider).')
    state: Literal['unchecked', 'missing_key', 'checking', 'ready', 'inconclusive', 'invalid_key', 'access_denied',
                   'billing_blocked', 'model_unavailable', 'rate_limited', 'provider_error', 'network_error'] = Field(
        description='`unchecked`: no check for the current key and model. `missing_key`: no key. `checking`: a check is '
                    'running now. `ready`: the request completed. `inconclusive`: the provider answered but the check '
                    'did not complete (unknown readiness). `invalid_key`, `access_denied`, `billing_blocked`, '
                    '`model_unavailable`, `rate_limited`, `provider_error`: classified provider refusals. '
                    '`network_error`: the provider could not be reached.')
    message: str = Field(description='Fixed local explanation (never provider text). Display only.')
    checked_at: str | None = Field(description='When the check finished, or null when never checked. ' + TIME)
    usage: AccountCheckUsage | None = Field(description='Reported usage of the check request, or null.')
    http_status: int | None = Field(description='Provider HTTP status of the check request, or null when none was received.')
    balance: None = Field(description='Always null: an exact balance is not available through an inference key.')
    balance_note: str = Field(description='Explains why no balance is shown.')
    cached: bool = Field(description='True when an identical check from the last 30 seconds was returned without a new request.')
    billing_url: str = Field(description='Provider billing dashboard URL.')
    usage_url: str = Field(description='Provider usage dashboard URL.')


# ---------------------------------------------------------------- status

class StatusNarrationAvailability(View):
    """Whether a narration provider can be used right now."""
    id: Literal['system', 'gemini', 'breeze'] = Field(description='Narration provider ID: `system` (macOS device voices), `gemini` (cloud) or `breeze` (self-hosted server).')
    label: str = Field(description='Display name.')
    available: bool = Field(
        description='`system`: macOS voices and ffmpeg are installed. `gemini`: a Gemini key is loaded. `breeze`: a '
                    'server URL is configured and the last check found at least one usable voice.')
    reason: str | None = Field(None, description='`breeze` only: why it is unavailable, or null when available.')


class NarrationProviderVoice(View):
    """A prebuilt provider voice."""
    id: str = Field(description='Voice ID to send, for example `Kore`.')
    name: str = Field(description='Display name (same as the ID).')


class NarrationProviderCapabilities(View):
    """Static capability flags of a narration provider."""
    offline: bool = Field(description='Works without a network connection.')
    performance_direction: bool = Field(description='Speaks character and passage direction (acting notes).')
    timing: Literal['passage'] = Field(description='Granularity of reported timing.')
    chunked_listening: bool = Field(description='Supports multi-passage chapter listening (Gemini only).')
    seeded_takes: bool = Field(description='A seed makes a take repeatable; a new seed makes a new take (Breeze only).')
    cost: Literal['local', 'cloud', 'self_hosted'] = Field(
        description='`local`: this computer. `cloud`: billed provider requests. `self_hosted`: the owner\'s server, no per-request charge.')
    custom_voice_ids: bool | None = Field(None, description='Gemini only: accepts voice IDs beyond the prebuilt list.')
    speakers_per_take: int | None = Field(None, description='Gemini only: speakers in one request.')


class NarrationProviderInfo(View):
    """The static contract of one narration provider (availability is reported in `providers`)."""
    id: Literal['system', 'gemini', 'breeze'] = Field(description='Narration provider ID: `system` (macOS device voices), `gemini` (cloud) or `breeze` (self-hosted server).')
    label: str = Field(description='Display name.')
    default_model: str = Field(description='Speech model used when none is chosen (`macos-say`, a Gemini TTS model, or `breeze-tts-2`).')
    models: list[str] = Field(description='Accepted speech models.')
    default_voice: str | None = Field(description='Voice used when none is chosen (`Kore` for Gemini), or null.')
    requires: Literal['none', 'api_key', 'server'] = Field(description='What must be configured before use.')
    capabilities: NarrationProviderCapabilities
    voices: list[NarrationProviderVoice] | None = Field(None, description='Gemini only: the prebuilt voices.')


class AnalysisProviderStatus(View):
    """An analysis provider and whether it is usable."""
    id: AnalysisProviderId = Field(description='Analysis provider ID: `local` (offline draft analysis, no key) or a cloud provider `gemini`, `openai`, `anthropic`.')
    label: str = Field(description='Display name.')
    available: bool = Field(description='`local` is always available; a cloud provider is available when its key is loaded.')
    has_api_key: bool = Field(description='True when a key is loaded (always false for `local`). The key is never returned.')
    model: str | None = Field(description='The saved analysis model for this provider, or null for `local`.')
    models: list[str] = Field(description='Curated model IDs (empty for `local`). A saved custom model may be absent from this list.')


class TtsRateState(View):
    """Live state of this server's Gemini speech rate limiter for one model (memory only)."""
    recent_requests: int = Field(description='Requests sent in the last 61 seconds.')
    recent_input_tokens: int = Field(description='Estimated input tokens sent in the last 61 seconds.')
    cooldown_seconds: float = Field(description='Seconds left in a cooldown after a provider rate-limit response; 0 when none.')
    daily_block_seconds: float = Field(
        description='Seconds until the daily quota block lifts (midnight Pacific time); 0 when not blocked. Saving '
                    'settings lifts it early.')


class LocalServiceUrls(View):
    """Resolved root URLs of the self-hosted analysis servers; an empty string means not configured."""
    local_llm: str = Field(description='OpenAI-compatible Local LLM server.')
    booknlp: str = Field(description='BookNLP quote-attribution server.')
    novel_analyzer: str = Field(description='Novel Analyzer chapter-script server.')


class StepPresetConfigView(View):
    """The step settings a saved set captures (version 1)."""
    provider: str = Field(description='Provider ID the step runs with (`local` for local steps).')
    model: str | None = Field(description='Model ID, or null for local and service providers.')
    custom_model: bool = Field(description='True when the model ID was typed by hand rather than chosen from the catalog.')
    gate: Literal['auto', 'review'] = Field(description="`auto` accepts a run's results; `review` holds them for review.")
    concurrency: int = Field(description='Requests at once, 1–4.')
    fresh: bool = Field(description='True to request fresh samples instead of reusing validated cached results.')
    chapter_id: str | None = Field(description='One story section to process, or null for all. Always null for steps '
                                               'that are not section-scoped.')


class StepPresetView(View):
    """An owner-authored saved step setting."""
    id: str = Field(description='Client-chosen ID, `[A-Za-z0-9_-]{1,40}`.')
    name: str = Field(description='Display name, 1–60 characters, whitespace collapsed. Unique per step, ignoring case.')
    step: str = Field(description='Analysis pipeline step ID the setting applies to.')
    config: StepPresetConfigView = Field(description='The captured step settings.')
    version: Literal[1] = Field(description='Saved-setting format version.')


class Status(View):
    """Runtime status and preferences. Never contains key values and never contacts a provider.

    Preference fields are those saved by `POST /api/settings`; the rest are
    derived at request time.
    """
    has_api_key: bool = Field(description='True when a Gemini API key is loaded (from Settings or the environment).')
    # Saved preferences
    tts_model: str = Field(description='Selected Gemini speech model; one of `tts_models`.')
    analysis_provider: AnalysisProviderId = Field(description='Default provider for classic analysis.')
    analysis_model: str = Field(description='Compatibility alias of `analysis_models_by_provider.gemini`.')
    analysis_models_by_provider: dict[CloudProvider, str] = Field(
        description='Selected analysis model per cloud provider (always all three).')
    preprocess_models_by_provider: dict[CloudProvider, str] = Field(
        description='Selected preprocessing (scan) model per cloud provider (always all three).')
    tts_limits: dict[str, ChapterListenLimits] = Field(
        description='Gemini speech limits per TTS model (one entry for each of `tts_models`).')
    listen_chunking: ChapterListenChunking = Field(description='Default chapter-listening chunk settings.')
    breeze_url: str = Field(
        description='Configured Breeze server root, or an empty string. When never set in Settings it comes from the '
                    '`BREEZE_TTS_URL` environment variable (and is then saved with the next settings change).')
    local_service_urls: LocalServiceUrls = Field(
        description='Resolved self-hosted server URLs: the Settings value when one was saved (even an empty string), '
                    'otherwise the environment variable.')
    narration_defaults: VoiceLibraryDefaults = Field(description='Default library voice per narration provider.')
    # Derived
    providers: list[StatusNarrationAvailability] = Field(description='Narration providers in order system, gemini, breeze, with availability.')
    narration_providers: dict[Literal['system', 'gemini', 'breeze'], NarrationProviderInfo] = Field(
        description='Static narration provider contracts keyed by provider ID.')
    breeze: VoiceLibraryBreezeStatus = Field(description='The last Breeze server check, read without contacting the server.')
    analysis_providers: list[AnalysisProviderStatus] = Field(description='Analysis providers in order local, gemini, openai, anthropic.')
    account_checks: dict[CloudProvider, AccountCheck] = Field(
        description='Last account check per cloud provider, valid only for the current key and model; otherwise `unchecked` or `missing_key`.')
    model_catalogs: dict[CloudProvider, AnalysisModelCatalog] = Field(
        description='Analysis model choices per cloud provider: the last refresh for the current key, else the curated list.')
    system_voices: list[VoiceLibrarySystemVoice] = Field(description='Installed macOS voices; empty when unavailable.')
    tts_models: list[str] = Field(description='Supported Gemini speech models.')
    analysis_models: list[str] = Field(description='Curated Gemini analysis model IDs (compatibility; see `model_catalogs`).')
    tts_rate: dict[str, TtsRateState] = Field(description='Live rate-limiter state per Gemini speech model.')
    analysis_step_presets: list[StepPresetView] = Field(
        description='Saved step settings for the Analyze tab, in saved order; empty when none. Applying one never '
                    'starts work.')
    tts_quota: dict[str, ChapterListenQuota] = Field(
        description='This library\'s daily Gemini speech request count for the selected speech model: one entry '
                    'keyed by `tts_model`, the same count chapter-listening jobs use. `requests_today` is 0 before '
                    'any usage is recorded.')
    data_directory: str = internal('Absolute path of the server library directory.')
    timing_kind: Literal['segment'] = Field(description='Granularity of read-along timing: per passage (segment).')


# ---------------------------------------------------------------- diagnostics

DiagnosticEventCode = Literal[
    'listen_request_failed', 'listen_poll_failed', 'listen_job_failed', 'buffer_failed', 'cache_read_failed',
    'playback_media_error', 'playback_play_rejected', 'playback_waiting', 'playback_resumed', 'preview_failed',
    'listen_job_stopped', 'listen_submit_failed', 'voice_preview_failed', 'voice_preview_stopped',
    'voice_preview_submit_failed']


class DiagnosticRecordResult(View):
    """Whether a diagnostic event was stored. `recorded: false` is not an error; do not retry."""
    recorded: bool = Field(description='True when a new event was stored; false when it was skipped (see `reason`).')
    id: str | None = Field(
        None, description='The stored event ID (32 hex). With `reason: duplicate`, the ID of the identical earlier event.')
    reason: Literal['duplicate', 'rate_limited', 'unavailable'] | None = Field(
        None, description='Why nothing was stored: `duplicate` (an identical event within 2 seconds), `rate_limited` '
                          '(120 client events in the last minute), `unavailable` (storage failed, or the fields broke a '
                          'rule checked after validation, such as a passage ID without a book ID).')


class DiagnosticEvent(View):
    """One stored diagnostic event. Only the fields that were sent (or set by the server) are present."""
    id: str = Field(description='Event ID (32 hex).')
    created_at: str = Field(description='When the event was stored: ' + TIME)
    source: Literal['client', 'server'] = Field(description='`client` events come from `POST /api/diagnostics`; `server` events from job workers.')
    event: DiagnosticEventCode = Field(
        description='Event code. Client codes are those accepted by `POST /api/diagnostics`; server codes are '
                    '`listen_job_failed`, `listen_job_stopped`, `listen_submit_failed`, `voice_preview_failed`, '
                    '`voice_preview_stopped` and `voice_preview_submit_failed`.')
    book_id: str | None = Field(None, description='Book ID.')
    segment_id: str | None = Field(None, description='Passage ID.')
    session_id: str | None = Field(None, description='Listening session ID.')
    job_id: str | None = Field(None, description='Job ID.')
    playback_rate: float | None = Field(None, description='Playback rate (0.1–8).')
    http_status: int | None = Field(None, description='HTTP status the client saw (100–599).')
    media_error_code: int | None = Field(None, description='HTML media error code (1–4).')
    operation: Literal['request', 'poll', 'play', 'prefetch', 'media', 'prepare', 'settle', 'cache_read', 'worker',
                       'submit'] | None = Field(
        None, description='What was happening. `worker` and `submit` are server-only.')
    status: Literal['failed', 'cancelled', 'interrupted'] | None = Field(None, description='Server events: the job outcome.')
    provider: Literal['gemini', 'system', 'breeze'] | None = Field(None, description='Server events: the narration provider.')


class DiagnosticEvents(View):
    """Stored diagnostic events, newest first."""
    events: list[DiagnosticEvent] = Field(description='Stored events, newest first: at most `limit` (default 100), and only those for `book_id` when that filter is given. Empty when none match.')
    retention_limit: int = Field(description='Maximum events kept; older events are pruned (5000).')


# ---------------------------------------------------------------- operations

_422_DIAGNOSTICS = ('The request failed validation. For this path the body is always '
                    '`{"detail": "Invalid diagnostic event fields."}` (a string, not a list): rejected input is never echoed.')

OPS: list[Op] = [
    op('GET', '/api/status', 'getStatus', 'System', 'Get runtime status and settings',
       'Runtime preferences, narration and analysis provider availability, whether keys are loaded (never their '
       'values), model catalogs, the last account-check state per provider, the last Breeze check, installed macOS '
       'voices, Gemini speech models and live rate-limiter state.\n\n'
       'Read-only and local: it never contacts a provider or the Breeze server. Prefer the lists it returns over '
       'assuming fixed model or voice lists.',
       response=Status),
    op('POST', '/api/settings', 'updateSettings', 'System', 'Update settings and credentials',
       'Applies a partial update and returns the new status (the same object as `GET /api/status`). Omitted '
       'fields stay unchanged; the request is idempotent.\n\n'
       '**Persistence.** Preferences (models, analysis provider, speech limits, chunking, `breeze_url`, '
       '`local_service_urls`) are saved in the library. API keys (`api_key`, `api_keys`, `breeze_api_key`) are kept '
       'in memory only: they last until the server restarts, when keys come from the environment again. An empty '
       'key string clears that runtime key.\n\n'
       '**Validation.** The whole request is checked before anything is saved; any 400 leaves every setting '
       'unchanged. `api_key` and `analysis_model` are compatibility aliases for the Gemini entries of `api_keys` '
       'and `analysis_models_by_provider`; sending an alias and its map entry with different values is refused. '
       'Analysis model IDs may be any syntactically valid ID (the provider decides support when used). TTS models '
       'are limited to `tts_models` from status.\n\n'
       '**Side effects.** Every successful call re-applies the speech limits, which also lifts any daily Gemini '
       'quota block recorded by this server (see `tts_rate.daily_block_seconds`). Account-check results whose key or '
       'model changed are discarded. Model catalogs are keyed by key, so a new key shows the curated list until '
       'refreshed.',
       response=Status, cost='none',
       errors={400: 'A value is invalid: conflicting alias and map values ("Conflicting Gemini API key fields", '
                    '"Conflicting Gemini analysis model fields"), a provider key other than gemini/openai/anthropic in '
                    'a map ("Unknown cloud analysis provider"), a malformed model ID, an unknown `analysis_provider`, '
                    'an unsupported `tts_model` or `tts_limits` model, a speech limit outside its range, an invalid '
                    'Breeze or self-hosted server URL, or an unknown self-hosted service ID.'}),
    op('POST', '/api/account-checks/{provider}', 'checkProviderAccount', 'System', 'Check a cloud analysis account',
       'Sends one tiny text-generation request (at most 128 output tokens, no book content, never retried) to the '
       'selected analysis model of `provider` with the loaded key, and returns the classified result. **This can '
       'incur a small charge.** It is not a balance check: providers do not expose a balance through an inference '
       'key, so `balance` is always null; open `billing_url` instead.\n\n'
       'An identical check (same key and model) from the last 30 seconds is returned with `cached: true` and no '
       'request. Without a key it returns `missing_key` without a request. The result is remembered in memory for '
       'the current key and model and appears in `GET /api/status` under `account_checks`. Provider error text is '
       'never returned.',
       response=AccountCheck, cost='may_charge',
       params={'provider': 'Cloud analysis provider: `gemini`, `openai` or `anthropic`.'},
       errors={400: 'The provider is not gemini, openai or anthropic ("Choose gemini, openai, or anthropic").',
               409: 'A check for this provider is already running, or the key or model changed in Settings while '
                    'the check was running (its result is discarded; check again).'}),
    op('POST', '/api/models/{provider}/refresh', 'refreshProviderModels', 'System', 'Refresh a provider model list',
       'Asks the provider which models the loaded key can see (a model-listing request; no text is generated and no '
       'credit is established) and merges the result with the curated list. Failures are reported in `state` and '
       '`message` with HTTP 200, and the curated choices remain usable.\n\n'
       'Results are cached in memory per key: a successful listing is reused for an hour and a failed one for 30 '
       'seconds (`cached: true`). Without a key, or with a key containing whitespace or non-ASCII characters, no '
       'request is sent. A second concurrent refresh returns `state: refreshing`. The latest result appears in '
       '`GET /api/status` under `model_catalogs`.',
       response=AnalysisModelCatalog, cost='network',
       params={'provider': 'Cloud analysis provider: `gemini`, `openai` or `anthropic`.'},
       errors={400: 'The provider is not gemini, openai or anthropic ("Choose gemini, openai, or anthropic").',
               409: 'The key changed in Settings during the refresh ("The key changed during the refresh…"); refresh again.'}),
    op('POST', '/api/narration/breeze/refresh', 'refreshBreeze', 'System', 'Check the Breeze server',
       'Explicitly checks the configured Breeze server: its health, its voice list and the reference clip of each '
       'cloned voice, pinning each voice\'s revision. It never generates speech. The result is saved (it survives '
       'restarts, so pinned voices and cached audio resolve while the server is offline) and returned as the '
       '`breeze` status object.\n\n'
       'Check failures are reported as `state` and `message` with HTTP 200, not as errors; a failed check keeps the '
       'previously saved voices for the same URL.\n\n'
       'After a `ready` check, every usable server voice that no library voice version uses yet (including voices '
       'behind deleted library voices, which are not re-imported) is imported as a library voice, with its '
       'reference clip as the audition when it can be downloaded. If no Breeze default library voice exists, one is '
       'set: the server\'s default voice when it is in the library, otherwise the first Breeze library voice. The '
       'import is skipped when another import is already running.',
       response=VoiceLibraryBreezeStatus, cost='network',
       errors={400: 'No Breeze server URL is configured ("Add the Breeze server URL in Settings first.").',
               409: 'Another Breeze check is running, or the Breeze URL or key changed during the check ("Breeze '
                    'settings changed during the check…"); check again.'}),
    op('POST', '/api/diagnostics', 'recordDiagnostic', 'Diagnostics', 'Record a playback diagnostic event',
       'Stores one best-effort operational event for troubleshooting playback. It sends no model requests. The body '
       'is a strict allowlist: free-form messages, stacks, URLs, source text, credentials and unknown fields are '
       'refused with 422, and the rejected input is never echoed.\n\n'
       'Use real IDs from API responses; identifiers are format-checked. `segment_id`, `session_id` and `job_id` '
       'require `book_id`; without it the event is dropped with `recorded: false, reason: "unavailable"` (HTTP 200). '
       'Numeric fields are strict JSON numbers (no strings or booleans).\n\n'
       'An identical event within 2 seconds is coalesced (`reason: "duplicate"`, with the earlier `id`). At most 120 '
       'client events are accepted per rolling minute across the server (`reason: "rate_limited"`). Storage failure '
       'returns `reason: "unavailable"`. Each accepted event keeps only the newest 5,000 events. A missing record '
       'does not mean playback succeeded; never retry paid work because of it. (The browser client additionally '
       'suppresses identical reports for ten seconds before sending.)\n\n'
       'Clients cannot set the event `source` or the server-only fields (`status`, `provider`, and the `worker` and '
       '`submit` operations); those appear only on events recorded by job workers.',
       response=DiagnosticRecordResult, cost='none',
       errors={422: _422_DIAGNOSTICS}),
    op('GET', '/api/diagnostics', 'listDiagnostics', 'Diagnostics', 'List diagnostic events',
       'Newest stored events first (client and server), with the retention limit. The Settings screen downloads '
       '`?limit=5000` as `bardic-diagnostics.json`.\n\n'
       'Events contain only allowlisted fields. An empty result does not establish that playback had no errors: '
       'logging is best effort, older events are pruned, and unrecorded events cannot be recovered. This rotating '
       'log is separate from analysis provenance and resource accounting. Server events for listen and voice-preview '
       'worker failures, stops and submission failures carry the `job_id` (and book, passage and session IDs) so they '
       'can be correlated with the saved job; they never copy its error text.',
       response=DiagnosticEvents, cost='none',
       params={'book_id': 'Only events for this book. Must be a book UUID (lowercase hex with hyphens).',
               'limit': 'Maximum events to return, 1–5000. Default 100.'},
       errors={400: 'Malformed `book_id` ("Invalid diagnostic identifier.") or `limit` outside 1–5000 ("Choose a '
                    'diagnostic limit from 1 to 5000.").',
               422: _422_DIAGNOSTICS.replace('The request failed validation', '`limit` is not an integer')}),
    op('GET', '/api/jobs', 'listJobs', 'Jobs', 'List jobs',
       'Returns jobs newest first, as a bare JSON array.\n\n'
       'Without `active`, at most the 100 most recent jobs are returned (after the `book_id` filter). With '
       '`active=true`, every `queued` or `running` job is returned, with no bound. There is no paging and no '
       'single-job GET: select a job from the list by `id` (or use the series runs route for series jobs).\n\n'
       'Poll this route to follow queued work until the job reaches a terminal status. Failures, cancellations and '
       'allowance stops appear in the job while polling still returns 200. `./bardicctl` calls '
       '`GET /api/jobs?active=true` before stopping or restarting the server and relies on the bare-array shape and '
       'on `kind` and `status`.',
       response=list[Job], cost='none',
       params={'book_id': 'Only jobs whose `book_id` equals this value: a book ID, or `series:<series id>` for series '
                          'parent jobs (series child jobs use their own book IDs).',
               'active': 'When true, return every queued or running job with no count limit. Default false.'}),
    op('POST', '/api/jobs/{job_id}/cancel', 'cancelJob', 'Jobs', 'Cancel a job',
       'Requests cancellation and returns the updated job. The body is ignored (send `{}` or nothing).\n\n'
       '- A job that is already terminal is returned unchanged (idempotent).\n'
       '- A `queued` job becomes `cancelled` immediately.\n'
       '- A `running` job keeps `status: running` with `cancel_requested: true` and stops at the next safe '
       'boundary; poll until it ends. Requests already sent to a provider can still finish and be billed; '
       'validated outputs and finished audio are kept.\n'
       '- Cancelling a `series` parent also cancels its queued child jobs and flags a running one.\n'
       '- Cancelling a `performance` also cancels its queued `listen_chapter` child and flags a running one.\n\n'
       'Cancelled work is resumed through the original start route, which creates a new job.',
       response=Job, cost='none',
       params={'job_id': 'Job ID from a job-starting response or `GET /api/jobs`.'},
       errors={404: 'No job has this ID ("Job not found").'}),
]

REQUEST_DOCS: dict[str, dict[str, str]] = {
    'SettingsRequest': {
        '__doc__': 'A partial settings update. Every field is optional; omitted fields stay unchanged. Unknown '
                   'fields are refused (422).',
        'api_key': 'Compatibility alias for `api_keys.gemini`. Runtime only, never saved; surrounding whitespace is '
                   'removed and an empty string clears the key. Up to 500 characters.',
        'tts_model': 'Gemini speech model; must be one of `tts_models` from status. Saved.',
        'analysis_step_presets': 'Saved step settings for the Analyze tab, at most 50. Replaces the saved list; `[]` '
                                 'clears it. Validated as a whole: an unknown step, a provider or model the step does '
                                 'not take, a duplicate `id`, or a duplicate name for one step is refused (400) and '
                                 'nothing is saved. Saved.',
        'analysis_model': 'Compatibility alias for `analysis_models_by_provider.gemini`. Saved.',
        'api_keys': 'Runtime API keys by cloud provider (`gemini`, `openai`, `anthropic`), up to 500 characters each. '
                    'Never saved: they last until restart. Whitespace is trimmed; an empty string clears that key; '
                    'providers not included keep their key.',
        'analysis_models_by_provider': 'Analysis model per cloud provider (`gemini`, `openai`, `anthropic`). IDs are '
                                       '1–200 characters of letters, digits, `.`, `_`, `:` or `-`, starting with a '
                                       'letter or digit; they need not be in the curated list. Saved.',
        'preprocess_models_by_provider': 'Preprocessing (scan) model per cloud provider, same ID rules. Saved.',
        'analysis_provider': 'Default classic-analysis provider: `local`, `gemini`, `openai` or `anthropic`. Saved.',
        'tts_limits': 'Gemini speech limits by TTS model: `{model: {rpm, tpm, rpd}}`. Each given model\'s limits are '
                      'replaced as a whole: a limit left out of the object is reset to its default (rpm 10, tpm '
                      '10,000, rpd 100), not kept. Whole numbers: rpm 1–10,000, tpm 1–100,000,000, rpd 1–10,000,000. '
                      'Models not included keep their limits. Saved.',
        'listen_chunking': 'Default chapter-listening chunk settings. Fields given are merged over the saved values '
                           'and the result is validated. Saved.',
        'breeze_url': 'Breeze server root: `http://` or `https://` host and optional port, without path, query, '
                      'fragment or credentials, up to 500 characters; a trailing slash is removed. An empty string '
                      'clears it. Saved. The saved Breeze check stays valid only for the URL it was made with.',
        'breeze_api_key': 'Breeze API key. Runtime only, never saved; whitespace trimmed; an empty string clears it.',
        'local_service_urls': 'Self-hosted analysis server roots by service ID (`local_llm`, `booknlp`, '
                              '`novel_analyzer`), each an http(s) root without path or credentials, up to 500 '
                              'characters. An empty string clears it and also overrides its environment variable. '
                              'Services not included keep their value; a service never set in Settings uses its '
                              'environment variable, which is never saved. Saved.',
    },
    'StepPreset': {
        '__doc__': 'One saved step setting (format version 1).',
        'id': 'Client-chosen ID, `[A-Za-z0-9_-]{1,40}`; unique within the list.',
        'name': 'Display name, 1–60 characters; whitespace is collapsed on save. Unique per step, ignoring case.',
        'step': 'A registered analysis pipeline step ID.',
        'config': 'The step settings to capture.',
        'version': 'Format version; must be 1.',
    },
    'StepPresetConfig': {
        '__doc__': 'Step settings a saved set captures. Unknown fields are refused (422).',
        'provider': 'Provider the step accepts (1–40 characters); validated like the step settings route.',
        'model': 'Model ID up to 200 characters, or null. Must be null for local and service providers.',
        'custom_model': 'True when the model ID was typed by hand; forced false when `model` is null.',
        'gate': '`auto` (default) or `review`.',
        'concurrency': 'Requests at once, a strict integer 1–4 (default 2).',
        'fresh': 'Request fresh samples instead of reusing validated results (default false).',
        'chapter_id': 'One story section (1–200 characters), or null for all; forced null for steps that are not '
                      'section-scoped.',
    },
    'DiagnosticRequest': {
        '__doc__': 'One allowlisted diagnostic event. Unknown fields, including free-form text, are refused (422).',
        'event': 'Client event code: `listen_request_failed`, `listen_poll_failed`, `listen_job_failed`, '
                 '`buffer_failed`, `cache_read_failed`, `playback_media_error`, `playback_play_rejected`, '
                 '`playback_waiting`, `playback_resumed` or `preview_failed`.',
        'book_id': 'Book UUID (lowercase hex with hyphens). Required when `segment_id`, `session_id` or `job_id` is sent.',
        'segment_id': 'Passage ID: `segment_` or `p_` followed by 12–32 lowercase hex characters.',
        'session_id': 'Listening session ID: 64 lowercase hex characters.',
        'job_id': 'Job ID: 32 lowercase hex characters.',
        'playback_rate': 'Playback rate, a finite JSON number from 0.1 to 8 (strict: no strings or booleans).',
        'http_status': 'HTTP status the client received, an integer 100–599 (strict).',
        'media_error_code': 'HTML media error code, an integer 1–4 (strict).',
        'operation': 'What the client was doing: `request`, `poll`, `play`, `prefetch`, `media`, `prepare`, '
                     '`settle` or `cache_read`.',
    },
}
