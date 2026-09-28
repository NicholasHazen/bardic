<!-- Generated from contract/openapi.json by `uv run --frozen python -m bardic.apispec`. Do not edit. -->

# Bardic 0.2.0

The local HTTP interface of Bardic, an ebook analysis, audiobook production
and read-along application. This document is the contract that clients are
written against. It is generated from the server and checked in as
`contract/openapi.json`; the prose guide is `docs/API.md` and a readable
reference is `contract/API-REFERENCE.md`.

## Transport and security

- The server binds to loopback (`127.0.0.1:8765` by default). An opt-in
  local-network mode adds a `.local` host name. There is **no
  authentication**: anyone who can reach the port is treated as the owner.
- Requests with an unexpected `Host` header are rejected on every route
  with status 400 and the plain-text (not JSON) body `Invalid host header`,
  before any operation runs. Writes (anything
  but GET, HEAD and OPTIONS) are rejected with 403 when they come from
  another browser origin (`Origin` differs from `Host`, or
  `Sec-Fetch-Site: cross-site`). Requests without an `Origin` header, such
  as command-line clients, are accepted. There is no CORS support.
- Every `/api/` response carries `Cache-Control: no-store`, except a
  successful cover image (`getBookCover`), which sets its own caching
  headers: a strong `ETag`, and `immutable` at its content-addressed `?v=`
  URL.
- URLs returned inside responses (audio, covers, auditions) are
  root-relative. Resolve them against the server's base URL.

## Conventions

- JSON requests use `Content-Type: application/json`; imports and voice
  clones use multipart form data. Request bodies reject unknown fields
  (422). Omitted fields take their documented defaults.
- IDs are opaque strings. URL-encode them and obtain them from responses.
- Source text offsets (`start`, `end`) are zero-based Unicode code-point
  offsets into the chapter text, with an exclusive end. They are not UTF-8
  byte offsets or JavaScript UTF-16 indices.
- "Passage" and "segment" name the same reader unit.
- GET requests never start paid generation and never create or change
  library records (books, jobs, runs, artifacts, decisions or resource
  measurements). A few write a disposable derived cache, such as the
  analysis census cache or the passage search index, which can be deleted
  without loss; those operations say so. `x-bardic-cost` on each operation
  says whether it can reach a provider: `none`, `network` (contacts a
  provider or self-hosted server without billed generation) or
  `may_charge`.
- Out-of-range paging parameters are clamped to the allowed range; the
  response reports the values used.

## Work and errors

- Long work is queued as a job and returned immediately. A queued or
  running job is not a result: poll `GET /api/jobs` until the job reaches a
  terminal status. Failures, cancellations and allowance stops appear in the
  job, while polling itself still returns 200. A terminal status is final.
- Statuses mean the same thing on every operation: 400 for a request that
  is well-formed but not acceptable, including an unknown ID inside a
  request body; 404 for an unknown resource named in the path; 409 for a
  conflict with current state (an active job or series run, a stale
  previewed plan, an archived book or series); 413 for a body that is too
  large; 429 for a request or quota limit; 502 when a provider or
  self-hosted server fails; 503 when the server is shutting down. Archiving
  or restoring something already in that state succeeds without change.
- Errors are JSON `{"detail": ..., "code": ...}`. `detail` is an English
  sentence, or a list of issues for 422 request validation. Display it; do
  not parse it. `code` is a stable snake_case identifier: branch on it.
  Each operation lists its codes per status (`x-bardic-error-codes`). Any
  operation can also return these global codes:
  - `validation_error` (422): the request failed validation.
  - `cross_origin_write` (403): the write guard rejected a browser write.
  - `internal_error` (500): an unexpected server defect.
  - `route_not_found` (404, 405): no route matches the method and path.

## Compatibility rules for clients

- Ignore response fields you do not know. New fields can appear in any
  new contract version.
- Treat enumerated string values (statuses, kinds, states) as open sets:
  handle an unknown value gracefully.
  Configure code generators to accept unknown enum values (for example
  openapi-generator's `enumUnknownDefaultCase=true`).
- A request field with a documented default is optional: omit it to get the
  default. Configure generators accordingly (openapi-typescript:
  `defaultNonNullable: false`). Response schemas carry no defaults; a response
  field is always present exactly when it is listed in `required`.
- Avoid any field marked `x-bardic-internal`: bookkeeping that a later
  version may remove. This version has none.
- `info.version` follows the rules in `contract/CHANGELOG.md`.

## Operations

- **System** — [`POST /api/account-checks/{provider}`](#checkprovideraccount), [`POST /api/models/{provider}/refresh`](#refreshprovidermodels), [`POST /api/narration/breeze/refresh`](#refreshbreeze), [`POST /api/settings`](#updatesettings), [`GET /api/status`](#getstatus)
- **Jobs** — [`GET /api/jobs`](#listjobs), [`POST /api/jobs/{job_id}/cancel`](#canceljob)
- **Diagnostics** — [`POST /api/diagnostics`](#recorddiagnostic), [`GET /api/diagnostics`](#listdiagnostics)
- **Library** — [`GET /api/books`](#listbooks), [`POST /api/books`](#importbook), [`POST /api/books/{book_id}/archive`](#archivebook), [`GET /api/books/{book_id}/cover`](#getbookcover), [`GET /api/books/{book_id}/export`](#exportaudiobook), [`PATCH /api/books/{book_id}/metadata`](#updatebookmetadata), [`POST /api/books/{book_id}/refresh-metadata`](#refreshbookmetadata), [`POST /api/books/{book_id}/restore`](#restorebook), [`POST /api/demo`](#createdemobook), [`GET /api/library`](#getlibrary)
- **Series** — [`GET /api/books/{book_id}/series`](#getbookseries), [`PUT /api/books/{book_id}/series`](#setbookseries), [`PUT /api/books/{book_id}/series/characters/{character_id}`](#linkseriescharacter), [`GET /api/books/{book_id}/series/context`](#getbookseriescontext), [`GET /api/series`](#listseries), [`POST /api/series`](#createseries), [`PATCH /api/series/{series_id}`](#renameseries), [`POST /api/series/{series_id}/archive`](#archiveseries), [`GET /api/series/{series_id}/characters`](#listseriescharacters), [`POST /api/series/{series_id}/characters`](#createseriescharacter), [`GET /api/series/{series_id}/map`](#getseriesmap), [`POST /api/series/{series_id}/plan`](#planseriesprocessing), [`POST /api/series/{series_id}/process`](#startseriesprocessing), [`POST /api/series/{series_id}/restore`](#restoreseries), [`GET /api/series/{series_id}/runs`](#listseriesruns), [`PUT /api/series/{series_id}/volumes`](#putseriesvolume), [`DELETE /api/series/{series_id}/volumes/{position}`](#deleteseriesvolume)
- **Books** — [`GET /api/books/{book_id}`](#getbook), [`POST /api/books/{book_id}/characters`](#addcharacter), [`PATCH /api/books/{book_id}/characters/{character_id}`](#editcharacter), [`GET /api/books/{book_id}/characters/{character_id}/references`](#listcharacterreferences), [`POST /api/books/{book_id}/repair-structure`](#repairbookstructure), [`PATCH /api/books/{book_id}/scenes/{scene_id}`](#editscene), [`PATCH /api/books/{book_id}/segments/{segment_id}`](#editpassage)
- **Pronunciations** — [`GET /api/books/{book_id}/pronunciations`](#listpronunciations), [`POST /api/books/{book_id}/pronunciations`](#addpronunciation), [`PATCH /api/books/{book_id}/pronunciations/{entry_id}`](#updatepronunciation), [`DELETE /api/books/{book_id}/pronunciations/{entry_id}`](#deletepronunciation)
- **Classic analysis** — [`GET /api/books/{book_id}/analysis`](#getanalysisstatus), [`POST /api/books/{book_id}/analysis-plan`](#previewclassicanalysis), [`POST /api/books/{book_id}/analyze`](#startclassicanalysis), [`GET /api/books/{book_id}/preprocessing`](#getanalysispreprocessing)
- **Analysis pipeline** — [`GET /api/analysis-pipeline`](#getanalysispipeline), [`PUT /api/analysis-pipeline/steps/{step_id}/settings`](#saveanalysispipelinestepsettings), [`GET /api/books/{book_id}/analysis-pipeline`](#getbookanalysispipeline), [`POST /api/books/{book_id}/analysis-pipeline/plan`](#planbookanalysispipelinerun), [`POST /api/books/{book_id}/analysis-pipeline/runs`](#startbookanalysispipelinerun), [`GET /api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions`](#listanalysispipelinestepversions), [`GET /api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions/{version_id}`](#getanalysispipelinestepversion), [`POST /api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions/{version_id}/accept`](#acceptanalysispipelinestepversion), [`POST /api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions/{version_id}/preview`](#previewanalysispipelinestepversion), [`POST /api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions/{version_id}/reject`](#rejectanalysispipelinestepversion)
- **Inspection** — [`GET /api/books/{book_id}/analysis-export`](#exportbookanalysis), [`GET /api/books/{book_id}/artifacts`](#listbookartifacts), [`GET /api/books/{book_id}/artifacts/{artifact_id}`](#getbookartifact), [`GET /api/books/{book_id}/pipeline`](#getpipelineinspector), [`GET /api/books/{book_id}/resources`](#getbookresourceusage), [`GET /api/books/{book_id}/search`](#searchbookpassages), [`GET /api/books/{book_id}/story-map`](#getstorymap)
- **Narration** — [`GET /api/audio/{book_id}/{segment_id}`](#getpassageaudio), [`GET /api/books/{book_id}/audio-assets/{asset_id}`](#getretainedaudioasset), [`POST /api/books/{book_id}/render`](#startenhancedrender)
- **Listening** — [`POST /api/books/{book_id}/listen`](#listentopassage), [`GET /api/books/{book_id}/listen/audio/{asset_id}`](#getlisteningaudio), [`POST /api/books/{book_id}/listen/chapter`](#startchapterlistening), [`POST /api/books/{book_id}/listen/chapter/preview`](#previewchapterlistening), [`GET /api/books/{book_id}/listen/takes`](#listlisteningtakes)
- **Performances** — [`GET /api/books/{book_id}/performances`](#listperformances), [`POST /api/books/{book_id}/performances`](#createperformance), [`POST /api/books/{book_id}/performances/preview`](#previewperformance), [`GET /api/books/{book_id}/performances/{performance_id}`](#getperformance), [`PATCH /api/books/{book_id}/performances/{performance_id}`](#updateperformance), [`GET /api/books/{book_id}/performances/{performance_id}/audio`](#getperformanceaudio), [`POST /api/books/{book_id}/performances/{performance_id}/prepare`](#prepareperformance)
- **Voice previews** — [`POST /api/books/{book_id}/voice-preview`](#startvoicepreview), [`GET /api/books/{book_id}/voice-preview/audio/{asset_id}`](#getvoicepreviewaudio)
- **Voices** — [`GET /api/voices`](#getvoicelibrary), [`POST /api/voices/breeze/clone`](#clonebreezevoice), [`POST /api/voices/defaults`](#setdefaultlibraryvoice), [`POST /api/voices/drafts`](#createvoicedraft), [`PATCH /api/voices/drafts/{draft_id}`](#updatevoicedraft), [`POST /api/voices/drafts/{draft_id}/abandon`](#abandonvoicedraft), [`GET /api/voices/drafts/{draft_id}/candidates/{candidate_id}/audio`](#getvoicedraftcandidateaudio), [`POST /api/voices/drafts/{draft_id}/candidates/{candidate_id}/discard`](#discardvoicedraftcandidate), [`POST /api/voices/drafts/{draft_id}/generate`](#generatevoicedraftcandidates), [`POST /api/voices/drafts/{draft_id}/save`](#savevoicedraft), [`POST /api/voices/gemini/refresh`](#refreshgeminivoices), [`PATCH /api/voices/{voice_id}`](#updatelibraryvoice), [`DELETE /api/voices/{voice_id}`](#deletelibraryvoice), [`POST /api/voices/{voice_id}/current`](#setlibraryvoicecurrentversion), [`GET /api/voices/{voice_id}/versions/{version}/audition`](#getlibraryvoiceaudition)

## System

Runtime status, settings, provider catalogs and explicit provider checks.

<a id="checkprovideraccount"></a>
### `POST /api/account-checks/{provider}`

**Check a cloud analysis account** · operation `checkProviderAccount` · cost `may_charge`

Sends one tiny text-generation request (at most 128 output tokens, no book content, never retried) to the selected analysis model of `provider` with the loaded key, and returns the classified result. **This can incur a small charge.** It is not a balance check: providers do not expose a balance through an inference key, so `balance` is always null; open `billing_url` instead.

The provider's answer, including a refusal (`invalid_key`, `billing_blocked`, `rate_limited`, …) or an unreachable provider (`network_error`), is the result of the check: it is returned in `state` with HTTP 200, never as an HTTP error. An identical check (same key and model) from the last 30 seconds is returned with `cached: true` and no request. Without a key it returns `missing_key` without a request. The result is remembered in memory for the current key and model and appears in `GET /api/status` under `account_checks`. Provider error text is never returned.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `provider` | path | string | yes | Cloud analysis provider: `gemini`, `openai` or `anthropic`. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [AccountCheck](#schema-accountcheck) | Success. |
| 400 | [Error](#schema-error) | - `cloud_provider_unknown`: The provider is not `gemini`, `openai` or `anthropic`. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 409 | [Error](#schema-error) | - `account_check_running`: A check for this provider is already running. - `settings_changed`: The key or analysis model changed while the check was running; its result was discarded. Check again. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="refreshprovidermodels"></a>
### `POST /api/models/{provider}/refresh`

**Refresh a provider model list** · operation `refreshProviderModels` · cost `network`

Asks the provider which models the loaded key can see (a model-listing request; no text is generated and no credit is established) and merges the result with the curated list. A failed or refused listing is reported in `state` and `message` with HTTP 200, never as an HTTP error: the response is still the usable (curated) catalog.

Results are cached in memory per key: a successful listing is reused for an hour and a failed one for 30 seconds (`cached: true`). Without a key, or with a key containing whitespace or non-ASCII characters, no request is sent. A second concurrent refresh returns `state: refreshing`. The latest result appears in `GET /api/status` under `model_catalogs`.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `provider` | path | string | yes | Cloud analysis provider: `gemini`, `openai` or `anthropic`. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [AnalysisModelCatalog](#schema-analysismodelcatalog) | Success. |
| 400 | [Error](#schema-error) | - `cloud_provider_unknown`: The provider is not `gemini`, `openai` or `anthropic`. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 409 | [Error](#schema-error) | - `settings_changed`: The key changed during the refresh; its result was discarded. Refresh again. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="refreshbreeze"></a>
### `POST /api/narration/breeze/refresh`

**Check the Breeze server** · operation `refreshBreeze` · cost `network`

Explicitly checks the configured Breeze server: its health, its voice list and the reference clip of each cloned voice, pinning each voice's revision. It never generates speech. The result is saved (it survives restarts, so pinned voices and cached audio resolve while the server is offline) and returned as the `breeze` status object.

An unreachable or failing server is the result of the check: it is reported as `state` and `message` with HTTP 200, not as an HTTP error, and a failed check keeps the previously saved voices for the same URL.

After a `ready` check, every usable server voice that no library voice version uses yet (including voices behind deleted library voices, which are not re-imported) is imported as a library voice, with its reference clip as the audition when it can be downloaded. If no Breeze default library voice exists, one is set: the server's default voice when it is in the library, otherwise the first Breeze library voice. The import is skipped when another import is already running.

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [VoiceLibraryBreezeStatus](#schema-voicelibrarybreezestatus) | Success. |
| 400 | [Error](#schema-error) | - `breeze_url_missing`: No Breeze server URL is configured (neither saved nor from the server's environment). |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 409 | [Error](#schema-error) | - `breeze_check_running`: Another Breeze check is running. - `settings_changed`: The Breeze URL or key changed during the check; its result was discarded. Check again. |

<a id="updatesettings"></a>
### `POST /api/settings`

**Update settings and credentials** · operation `updateSettings` · cost `none`

Applies a partial update and returns the new status (the same object as `GET /api/status`). Omitted fields stay unchanged; the request is idempotent.

**Persistence.** Preferences (models, analysis provider, speech limits, chunking, `breeze_url`, `local_service_urls`) are saved in the library. Only values sent in a request are saved: a Breeze or self-hosted server URL that comes from the server's environment is used but never saved. API keys (`api_key`, `api_keys`, `breeze_api_key`) are kept in memory only: they last until the server restarts, when keys come from the environment again. An empty key string clears that runtime key.

**Validation.** The whole request is checked before anything is saved; any 400 or 422 leaves every setting unchanged. `api_key` and `analysis_model` are compatibility aliases for the Gemini entries of `api_keys` and `analysis_models_by_provider`; sending an alias and its map entry with different values is refused. Analysis model IDs may be any syntactically valid ID (the provider decides support when used). TTS models are limited to `tts_models` from status. Speech limits out of range, of the wrong type or with an unknown name are request validation errors (422).

**Side effects.** A daily Gemini quota block recorded by this server (see `tts_rate.daily_block_seconds`) is lifted for a model only when that model's speech limits change, and for every model when the Gemini key changes (it may belong to another project). Other changes, and re-sending the current values, keep the blocks. Account-check results whose key or model changed are discarded. Model catalogs are keyed by key, so a new key shows the curated list until refreshed.

Request body (`application/json`): [SettingsRequest](#schema-settingsrequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [Status](#schema-status) | Success. |
| 400 | [Error](#schema-error) | - `gemini_key_conflict`: `api_key` and `api_keys.gemini` are both sent with different values. - `gemini_model_conflict`: `analysis_model` and `analysis_models_by_provider.gemini` are both sent with different values. - `cloud_provider_unknown`: A key of `api_keys`, `analysis_models_by_provider` or `preprocess_models_by_provider` is not `gemini`, `openai` or `anthropic`. - `model_id_invalid`: An analysis or preprocessing model ID is malformed. - `analysis_provider_unknown`: `analysis_provider` is not `local`, `gemini`, `openai` or `anthropic`. - `tts_model_unsupported`: `tts_model`, or a model key of `tts_limits`, is not one of `tts_models`. - `breeze_url_invalid`: `breeze_url` is not an http(s) server root without path, query or credentials. - `local_service_unknown`: A key of `local_service_urls` is not `local_llm`, `booknlp` or `novel_analyzer`. - `service_url_invalid`: A self-hosted server URL is not an http(s) server root without path, query or credentials. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="getstatus"></a>
### `GET /api/status`

**Get runtime status and settings** · operation `getStatus` · cost `none`

Runtime preferences, narration and analysis provider availability, whether keys are loaded (never their values), model catalogs, the last account-check state per provider, the last Breeze check, installed macOS voices, Gemini speech models and live rate-limiter state.

Read-only and local: it never contacts a provider or the Breeze server, and never writes. Prefer the lists it returns over assuming fixed model or voice lists; the analysis model choices per provider are in `model_catalogs`.

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [Status](#schema-status) | Success. |

## Jobs

Durable background work: listing, polling and cancellation.

<a id="listjobs"></a>
### `GET /api/jobs`

**List jobs** · operation `listJobs` · cost `none`

Returns jobs newest first, as a bare JSON array of full `Job` objects. Read-only.

Without `active`, at most the 100 most recent jobs are returned (after the `book_id` filter). With `active=true`, every `queued` or `running` job is returned, with no bound. There is no paging and no single-job GET: select a job from the list by `id` (or use the series runs route for series jobs).

Poll this route to follow queued work until the job reaches a terminal status, which is final. Failures, cancellations and allowance stops appear in the job while polling still returns 200. `./bardicctl` calls `GET /api/jobs?active=true` before stopping or restarting the server and relies on the bare-array shape and on `kind` and `status`.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | query | string \| null |  | Only jobs whose `book_id` equals this value: a book ID, or `series:<series id>` for series parent jobs (series child jobs use their own book IDs). |
| `active` | query | boolean |  | When true, return every queued or running job with no count limit. Default false. (default `false`) |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | list of [Job](#schema-job) | Success. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="canceljob"></a>
### `POST /api/jobs/{job_id}/cancel`

**Cancel a job** · operation `cancelJob` · cost `none`

Requests cancellation and returns the updated job. The body is ignored (send `{}` or nothing).

- A job that is already terminal is returned unchanged (idempotent).
- A `queued` job becomes `cancelled` immediately, and stays `cancelled`: its worker never starts it, and a later server shutdown does not turn it into `interrupted`.
- A `running` job keeps `status: running` with `cancel_requested: true` and stops at the next safe boundary; poll until it ends. Requests already sent to a provider can still finish and be billed; validated outputs and finished audio are kept.
- Cancelling a `series` parent also cancels its queued child `analyze` jobs and flags running ones.
- Cancelling a `performance` also cancels its queued `listen_chapter` child and flags a running one.

Cancelled work is resumed through the original start route, which creates a new job.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `job_id` | path | string | yes | Job ID from a job-starting response or `GET /api/jobs`. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [Job](#schema-job) | Success. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `job_not_found`: No job has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

## Diagnostics

Best-effort, allowlisted operational events for troubleshooting playback.

<a id="recorddiagnostic"></a>
### `POST /api/diagnostics`

**Record a playback diagnostic event** · operation `recordDiagnostic` · cost `none`

Stores one best-effort operational event for troubleshooting playback. It sends no model requests. The body is a strict allowlist: free-form messages, stacks, URLs, source text, credentials and unknown fields are refused with 422, and the rejected input is never echoed.

Use real IDs from API responses; identifiers are format-checked. `segment_id`, `session_id` and `job_id` belong to a book and require `book_id` (422 without it). Numeric fields are strict JSON numbers (no strings or booleans).

An identical event within 2 seconds is coalesced (`reason: "duplicate"`, with the earlier `id`). At most 120 client events are accepted per rolling minute across the server (`reason: "rate_limited"`). Storage failure returns `reason: "unavailable"`. Each accepted event keeps only the newest 5,000 events. A missing record does not mean playback succeeded; never retry paid work because of it. (The browser client additionally suppresses identical reports for ten seconds before sending.)

Clients cannot set the event `source` or the server-only fields (`status`, `provider`, and the `worker` and `submit` operations); those appear only on events recorded by job workers.

Request body (`application/json`): [DiagnosticRequest](#schema-diagnosticrequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [DiagnosticRecordResult](#schema-diagnosticrecordresult) | Success. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 422 | [Error](#schema-error) | - `validation_error`: The body failed validation: an unknown or free-form field, a malformed identifier, a number out of range or of the wrong JSON type, or `segment_id`, `session_id` or `job_id` without `book_id`. For this operation the body is always `{"detail": "Invalid diagnostic event fields.", "code": "validation_error"}` (a string, not a list): rejected input is never echoed. |

<a id="listdiagnostics"></a>
### `GET /api/diagnostics`

**List diagnostic events** · operation `listDiagnostics` · cost `none`

Newest stored events first (client and server), with the retention limit. Read-only. The Settings screen downloads `?limit=5000` as `bardic-diagnostics.json`.

Events contain only allowlisted fields. An empty result does not establish that playback had no errors: logging is best effort, older events are pruned, and unrecorded events cannot be recovered. This rotating log is separate from analysis provenance and resource accounting. Server events for listen and voice-preview worker failures, stops and submission failures carry the `job_id` (and book, passage and session IDs) so they can be correlated with the saved job; they never copy its error text.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | query | string \| null |  | Only events for this book. Must be a book UUID (lowercase hex with hyphens). |
| `limit` | query | integer |  | Maximum events to return. Default 100; values below 1 are treated as 1 and values above 5000 as 5000. (default `100`) |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [DiagnosticEvents](#schema-diagnosticevents) | Success. |
| 400 | [Error](#schema-error) | - `book_id_invalid`: `book_id` is not a book UUID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

## Library

Importing books, the library snapshot, metadata, covers, removal and restoration, and exports.

<a id="listbooks"></a>
### `GET /api/books`

**List active books** · operation `listBooks` · cost `none`

Summaries of every book that is not removed: counts, cover metadata, series membership and measured storage. This is not the prose projection; fetch `GET /api/books/{book_id}` for that. Books are ordered by import time, most recently imported first (`created_at` descending). Saving a book (a metadata edit, an analysis result, a manual edit) does not change its place. Books stored without `created_at` come last, in a stable order. Removed books are never listed here; use `GET /api/library?include_archived=true`.

Read-only. Each call measures the book's media folders on disk and its database payload, and checks which takes are still current (`audio_count`), so it is proportionally slower for large libraries. Counts distinguish narrative chapters (`chapter_count`) from other sections (`section_count`); see docs/STRUCTURE.md.

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | list of [LibraryBookSummary](#schema-librarybooksummary) | Book summaries, most recently imported first. |

<a id="importbook"></a>
### `POST /api/books`

**Import an EPUB or TXT book** · operation `importBook` · cost `none`

Imports a DRM-free EPUB or UTF-8 TXT uploaded as multipart form data with the single field `file`, and returns the new book as the full presented book document (the same shape as `GET /api/books/{book_id}`).

- The format is chosen by the uploaded **file name's extension** (`.epub` or `.txt`, any case); the part's content type is ignored. A part without a file name is treated as `book.txt`.
- Upload maximum is 30 MiB (31,457,280 bytes) of file content. A request whose `Content-Length` exceeds that limit plus 64 KiB of multipart framing is refused with 413 before its body is read; a body sent without `Content-Length` is read only up to that limit. EPUBs are additionally limited to 100 MB / 5,000 files when expanded.
- The title comes from EPUB metadata, or for TXT from the file name (without extension, underscores as spaces); the author from EPUB creators, or an empty string. An EPUB cover image becomes a JPEG thumbnail.
- The book starts with a free local draft (`analysis.provider` `local`, status `draft`): chapters, scenes and passages are split locally; dialogue passages are `unassigned` until analysis. The narrator and `unassigned` entries get installed device (macOS) voices when available. No provider is contacted.
- The original bytes are saved in the data directory (`originals/{book_id}/source.{ext}`) for later `refreshBookMetadata` and structure repair. A resource record (stage `import`) measures the import.
- A failed import leaves nothing behind: no book, no saved original and no resource record.
- Not idempotent and not deduplicated: every call creates a new book with a new ID, even for the same file.

Example (synthetic file): `curl --fail --request POST http://127.0.0.1:8765/api/books --form 'file=@/absolute/path/to/synthetic-story.txt;type=text/plain'`

Request body (`multipart/form-data`): [ImportBookForm](#schema-importbookform)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [Book](#schema-book) | The newly imported book, presented like `GET /api/books/{book_id}`. |
| 400 | [Error](#schema-error) | - `book_file_invalid`: The file could not be imported: empty file, unsupported extension, TXT not UTF-8 or containing binary data, unreadable, unsafe or encrypted EPUB, or no readable text. `detail` says which. - `cover_unreadable`: The EPUB's cover image could not be read safely. - `invalid_request`: The multipart body could not be parsed. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 413 | [Error](#schema-error) | - `upload_too_large`: The upload is larger than 30 MiB. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="archivebook"></a>
### `POST /api/books/{book_id}/archive`

**Remove a book from the library (reversibly)** · operation `archiveBook` · cost `none`

Reversibly removes the book from normal library views (`listBooks`, the default `getLibrary`, series `books` lists) and returns `{id, archived: true, retained: true}`. No request body.

There is no destructive book-delete endpoint. Removal (archiving) changes visibility only: the original upload, analysis, audio, series links and history are retained and remain readable (for example `GET /api/books/{book_id}`, the cover and the export keep working), and no disk space is reclaimed. Active processing and most edits reject a removed book with 409 `book_archived` until it is restored. Removal of a series member leaves the series intact; the book appears in the series' `volumes` with status `archived`.

Idempotent: removing an already removed book succeeds with the same response and changes nothing. A call that removes the book records a `library_state` artifact in its history. Refused while any job is queued or running for the book or an active series run has reserved it.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Opaque book ID. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [SeriesArchiveState](#schema-seriesarchivestate) | Success. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 409 | [Error](#schema-error) | - `job_active`: A job is queued or running for this book. - `series_run_active`: An active series run has reserved this book. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="getbookcover"></a>
### `GET /api/books/{book_id}/cover`

**Download the cover thumbnail** · operation `getBookCover` · cost `none`

The saved cover thumbnail bytes. Covers are always stored as JPEG (at most 240 x 360 pixels, at most 256 KiB), so the content type is `image/jpeg`. Works for removed books too. Read-only.

Caching: the response has a strong `ETag`, the quoted SHA-256 hex of the bytes (`"{sha256}"`, where `sha256` is `cover.sha256` in a summary). A request whose `If-None-Match` lists that tag (weak comparison, or `*`) gets 304 Not Modified with an empty body. With `?v=` equal to the current `sha256` (the summary's `cover.url`), the response carries `Cache-Control: private, max-age=31536000, immutable`, because that URL always names these bytes; any other URL gets `Cache-Control: private, no-cache` (revalidate with the `ETag`). Errors are `no-store` like every other `/api/` response. Other query parameters are ignored.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Opaque book ID. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | `image/jpeg` | JPEG image bytes. |
| 304 |  | Not modified: `If-None-Match` matched the current `ETag` (empty body). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `cover_not_found`: The book has no saved cover. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="exportaudiobook"></a>
### `GET /api/books/{book_id}/export`

**Download the audiobook ZIP** · operation `exportAudiobook` · cost `none`

Builds and downloads an audiobook ZIP from the book's valid enhanced (cast) takes. Requires at least one valid take; a take is valid when it still matches the passage's current text, speaker, voice, scene direction and provider/model, and its WAV file exists. Simple-listening and voice-example audio are not included. Works for removed books and does not require the book to be idle.

The response is `application/zip` with `Content-Disposition: attachment` and a file name derived from the title (characters other than letters, digits, underscore, space, `.` and `-` removed; at most 80 characters; `audiobook` if nothing remains) plus `.zip`. The archive is assembled synchronously in a temporary folder before the response starts, so a large book takes a while. Read-only: it records nothing and never contacts a provider; the temporary folder is deleted after the response.

Archive layout:

- `production.json`: the book exactly as `GET /api/books/{book_id}` presents it at export time (the `Book` schema, pretty-printed UTF-8), including `leading_text`/`trailing_text`; each passage's `audio` is its current take or null. Its audio `url` values point at this server, not into the archive; use `takes/`.
- `README.txt`: a short plain-text explanation.
- `takes/{segment_id}.wav`: one file per passage with a valid take, even when its chapter is incomplete.
- `chapters/NNN.txt`: the text of every section, numbered from `001` in book order (all sections, not only narrative chapters).
- `chapters/NNN.wav`: the concatenated takes of a section whose passages all have valid takes (mono 24 kHz 16-bit PCM). Incomplete sections get no chapter WAV; sections without passages get none either.
- `timeline.json`: `{title, timing_kind: "segment", complete, chapters, missing_segment_ids}`. `complete` is true when every passage has a valid take. Each `chapters` entry is `{id, title, complete, segments, audio?}` where `audio` is the chapter WAV path inside the archive (only for assembled sections) and `segments` lists `{segment_id, start, end}` in seconds within that WAV (empty for unassembled sections). Timings mark exact passage boundaries, not words. `missing_segment_ids` lists passages without a valid take.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Opaque book ID. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | `application/zip` | The audiobook ZIP archive. |
| 206 | `application/zip` | Partial content for a `Range` request (served from a file; see `Content-Range`). |
| 400 | [Error](#schema-error) | - `export_audio_missing`: No passage has a current enhanced take. - `take_unreadable`: A take file is not a readable mono 24 kHz 16-bit PCM WAV. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 416 |  | The requested `Range` cannot be satisfied (empty body; see `Content-Range`). |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="updatebookmetadata"></a>
### `PATCH /api/books/{book_id}/metadata`

**Edit display title and author** · operation `updateBookMetadata` · cost `none`

Sets the display title and author and returns the updated summary. Whitespace is normalized (runs of spaces, tabs and newlines become one space; leading and trailing whitespace is removed). Title is required (1–500 characters); author defaults to an empty string (maximum 500). A missing `title`, a value longer than 500 characters or an unknown field is a 422 validation error.

Each field whose value this edit changes is marked as reviewed: a later `refreshBookMetadata` never overwrites it. A field sent with its current value is not marked, and earlier marks are kept. An edit that changes a field increments the book's `revision` and retains the previous projection in history; an edit that changes nothing saves nothing. It does not rename the original file, change the text, or change the book's place in the library order.

Refused while any job is queued or running for the book, while an active series run has reserved it, or while the book is removed.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Opaque book ID. |

Request body (`application/json`): [BookMetadataRequest](#schema-bookmetadatarequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [LibraryBookSummary](#schema-librarybooksummary) | Success. |
| 400 | [Error](#schema-error) | - `metadata_invalid`: The normalized title is empty, or a value contains control characters. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 409 | [Error](#schema-error) | - `job_active`: A job is queued or running for this book. - `series_run_active`: An active series run has reserved this book. - `book_archived`: The book is removed (archived); restore it first. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="refreshbookmetadata"></a>
### `POST /api/books/{book_id}/refresh-metadata`

**Re-read title, author and cover from the saved original** · operation `refreshBookMetadata` · cost `none`

Re-parses the saved original EPUB or TXT (locally; it does not download metadata from the web) and returns the updated summary. No request body.

- Title and author are replaced by the values parsed from the original, except a field that an `updateBookMetadata` call changed (reviewed display metadata is preserved per field).
- If the original yields a cover thumbnail, it replaces the saved cover. A missing cover in the original does not remove an existing one.
- Chapters, passages, analysis and audio are not changed; use `POST /api/books/{book_id}/repair-structure` for structure.
- Always increments the book's `revision` and retains the previous projection in history, even when nothing changed.
- A resource record (stage `metadata_refresh`) measures each refresh that reaches the original, including one that fails to parse it. A refused request (unknown, removed or busy book) records nothing.

Refused while any job is queued or running for the book, while an active series run has reserved it, or while the book is removed.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Opaque book ID. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [LibraryBookSummary](#schema-librarybooksummary) | Success. |
| 400 | [Error](#schema-error) | - `original_unavailable`: The book has no readable saved original (for example the demo book). - `original_too_large`: The saved original is larger than the import limit. - `original_unreadable`: The saved original can no longer be parsed. `detail` says why. - `cover_unreadable`: The original's cover image could not be read safely. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 409 | [Error](#schema-error) | - `job_active`: A job is queued or running for this book. - `series_run_active`: An active series run has reserved this book. - `book_archived`: The book is removed (archived); restore it first. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="restorebook"></a>
### `POST /api/books/{book_id}/restore`

**Restore a removed book** · operation `restoreBook` · cost `none`

Restores a removed book to normal library views and returns `{id, archived: false, retained: true}`. No request body. Everything retained during removal becomes usable again.

Idempotent: restoring a book that is not removed succeeds with the same response and changes nothing. A call that restores the book records a `library_state` artifact in its history. Refused while a job is queued or running for the book, or while a run of the book's series is queued or running (even if the series itself is removed). Restoring a book does not restore its removed series.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Opaque book ID. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [SeriesArchiveState](#schema-seriesarchivestate) | Success. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 409 | [Error](#schema-error) | - `job_active`: A job is queued or running for this book. - `series_run_active`: A run of the book's series is queued or running. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="createdemobook"></a>
### `POST /api/demo`

**Create the demo book** · operation `createDemoBook` · cost `none`

Creates the built-in original sample story ("The Last Light") with a free local heuristic draft analysis (explicit speech tags only; `analysis.provider` `local`) and returns it as the full presented book document. No request body and no provider contact.

Not idempotent: every call creates another copy with a new ID. The demo has no saved original file, so `refreshBookMetadata` on it fails with 400 `original_unavailable` and its `storage.original_bytes` is 0.

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [Book](#schema-book) | The new demo book, presented like `GET /api/books/{book_id}`. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |

<a id="getlibrary"></a>
### `GET /api/library`

**Get the library snapshot** · operation `getLibrary` · cost `none`

The library-management view: `{books, series, storage}` with book summaries (the `listBooks` shape), series entries with their books and volume placeholders, and library-wide measured storage.

Books are ordered by import time, most recently imported first (`created_at` descending). Saving a book (a metadata edit, an analysis result, a manual edit) does not change its place. Books stored without `created_at` come last, in a stable order. With `include_archived=true`, removed books and removed series are included (each flagged `archived`); otherwise both are omitted. A series' `volumes` always include its removed books (status `archived`).

Read-only. Storage caveats: per-book `database_payload_bytes` does not apportion SQLite pages, indexes or free space exactly; the shared database, WAL and SHM file sizes are reported separately in `storage`. Removed items retain their files and data. Every call walks the data directory to measure sizes.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `include_archived` | query | boolean |  | Include removed (archived) books and series. Default false. (default `false`) |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [LibrarySnapshot](#schema-librarysnapshot) | Success. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

## Series

Series membership, volume placeholders, cross-book character identities and collection runs.

<a id="getbookseries"></a>
### `GET /api/books/{book_id}/series`

**Get a book's series placement** · operation `getBookSeries` · cost `none`

Returns `{membership, series, links, characters}`. `membership` and `series` are null when the book is in no series or when the book or its series is removed.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [BookSeries](#schema-bookseries) | Success. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="setbookseries"></a>
### `PUT /api/books/{book_id}/series`

**Set or clear a book's series** · operation `setBookSeries` · cost `none`

Places the book in a series at a reading order, moves it, or detaches it, and returns the refreshed `getBookSeries` envelope.

Send `{"series_id": "SERIES_ID", "position": 9}` with a JSON number, not a numeric string. Send `{"series_id": null}` (position null or omitted) to detach.

- Positions are finite numbers from 0 through 1,000,000; decimals support prequels and side stories. A position already used by another supplied book of the series is rejected.
- Assigning a book at a placeholder position replaces that placeholder.
- Detaching, or moving to another series, deletes the book's identity links. Moving within the same series keeps them.
- Removed (archived) membership and history are retained for restoration.

Refused while the book is removed, has an active job or is held by a series run, and while the target series is removed or has an active run. Each change is recorded in the book's series provenance.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |

Request body (`application/json`): [SeriesMembershipRequest](#schema-seriesmembershiprequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [BookSeries](#schema-bookseries) | Success. |
| 400 | [Error](#schema-error) | - `unknown_series`: No series has the `series_id` in the body. - `position_invalid`: `series_id` is given without a finite `position` from 0 through 1,000,000. - `position_without_series`: `position` is given without `series_id`. - `position_taken`: Another supplied book of the series already has that position. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 409 | [Error](#schema-error) | - `book_archived`: The book is removed (archived). Restore it first. - `job_active`: A job is working on this book. Wait for it or cancel it. - `series_run_active`: An active series run reserves this book, or the target series has an active run. - `series_archived`: The target series is removed (archived). Restore it first. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="linkseriescharacter"></a>
### `PUT /api/books/{book_id}/series/characters/{character_id}`

**Link or unlink a book character to a series identity** · operation `linkSeriesCharacter` · cost `none`

With `{"series_character_id": "ID"}`, confirms that the book character is that series identity (replacing any previous link for the character) and returns the link. Re-linking the same identity keeps the original `confirmed_at`. With `{"series_character_id": null}` (or an empty body `{}`), removes any link and returns `{character_id, linked: false}`; unlinking is idempotent and does not check that the character exists.

The book must be in a series, the series must not be removed (for unlinking too: removal retains links for restoration), and the identity must belong to that series. Narrator and unassigned cannot become series identities. Only confirmed links carry knowledge across books; names alone never do. Refused while the book is removed, has an active job or is held by a series run. Each change is recorded in the book's series provenance.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `character_id` | path | string | yes | Book-local character ID. |

Request body (`application/json`): [SeriesCharacterLinkRequest](#schema-seriescharacterlinkrequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [SeriesCharacterLinkState](#schema-seriescharacterlinkstate) \| [SeriesCharacterUnlinked](#schema-seriescharacterunlinked) | Success. |
| 400 | [Error](#schema-error) | - `character_not_linkable`: The character is `narrator` or `unassigned`. - `book_not_in_series`: Linking: the book is in no series. - `unknown_series_character`: No series character has the `series_character_id` in the body. - `series_character_mismatch`: The identity belongs to another series than the book's. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `character_not_found`: Linking: the book has no character with `character_id`. |
| 409 | [Error](#schema-error) | - `book_archived`: The book is removed (archived). Restore it first. - `job_active`: A job is working on this book. Wait for it or cancel it. - `series_run_active`: An active series run reserves this book, or the target series has an active run. - `series_archived`: The book's series is removed (archived). Restore it first. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="getbookseriescontext"></a>
### `GET /api/books/{book_id}/series/context`

**Preview earlier-volume context for a book** · operation `getBookSeriesContext` · cost `none`

Reads the bounded context that analysis of this book receives from strictly earlier volumes, without model calls. It includes only observations of characters with confirmed links, from active earlier books of the same active series, whose quotes still match the current source text; name mentions, later volumes, unconfirmed links, removed or unavailable volumes and invalidated evidence are excluded. Missing volumes contribute nothing.

The bound (at most 12,000 serialized characters and 8 observations per character) is an implementation choice, not a request parameter. `fingerprint` is stable while the inputs are unchanged. All historical observations remain retained even when omitted here.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [BookSeriesContext](#schema-bookseriescontext) | Success. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="listseries"></a>
### `GET /api/series`

**List active series** · operation `listSeries` · cost `none`

Returns every active (non-removed) series ordered by name (case-insensitive), then ID, each with its supplied books in reading order, all volume slots (supplied, missing and planned) and its character-identity count. Removed series are omitted; use `GET /api/library?include_archived=true` to list them. A removed series can still be read by ID through `getSeriesMap`, `listSeriesRuns` and `listSeriesCharacters`.

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | list of [Series](#schema-series) | Success. |

<a id="createseries"></a>
### `POST /api/series`

**Create a series** · operation `createSeries` · cost `none`

Creates an empty series. Whitespace in the name is collapsed. Names are unique ignoring case, including against removed series. Not idempotent: each call creates a new ID. The response is a shorter shape than `Series` (no `archived` or `volumes`).

Request body (`application/json`): [SeriesNameRequest](#schema-seriesnamerequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [SeriesCreated](#schema-seriescreated) | Success. |
| 400 | [Error](#schema-error) | - `name_invalid`: The name is blank after whitespace is trimmed, or longer than 200 characters. - `series_name_taken`: Another series (active or removed) already has this name, ignoring case. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="renameseries"></a>
### `PATCH /api/series/{series_id}`

**Rename a series** · operation `renameSeries` · cost `none`

Renames an active series when its work is idle: no active run of this series, no job on any of its books (removed ones included), and no series run holding one of its books. Whitespace is collapsed. The change is recorded in each member book's series provenance. Returns only `{id, name}`.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `series_id` | path | string | yes | Series ID. |

Request body (`application/json`): [SeriesNameRequest](#schema-seriesnamerequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [SeriesRenamed](#schema-seriesrenamed) | Success. |
| 400 | [Error](#schema-error) | - `name_invalid`: The name is blank after whitespace is trimmed, or longer than 200 characters. - `series_name_taken`: Another series (active or removed) already has this name, ignoring case. - `text_invalid`: The name contains control characters other than tab and newlines. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `series_not_found`: No series has this ID. |
| 409 | [Error](#schema-error) | - `series_archived`: The series is removed (archived). Restore it first; reads still work. - `series_run_active`: This series has an active processing run, or an active series run reserves one of its books. Wait for it or cancel it. - `job_active`: A job is working on one of its books (including a removed one). Wait for it or cancel it. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="archiveseries"></a>
### `POST /api/series/{series_id}/archive`

**Remove a series** · operation `archiveSeries` · cost `none`

Removes (archives) the series from normal views. Nothing is deleted: memberships, placeholders, identities and history are retained for restoration, and member books remain independently available in the library. While removed, series edits, processing and membership or identity-link changes are refused with 409 `series_archived`; reads (`getSeriesMap`, `listSeriesRuns`, `listSeriesCharacters`) still work. Removing requires the series to be idle (as for `renameSeries`) and records a library-visibility artifact on each member book. Idempotent: when the series is already in the requested state, the call returns that state and changes nothing, performs no checks and records nothing. No request body.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `series_id` | path | string | yes | Series ID. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [SeriesArchiveState](#schema-seriesarchivestate) | Success. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `series_not_found`: No series has this ID. |
| 409 | [Error](#schema-error) | - `series_run_active`: This series has an active processing run, or an active series run reserves one of its books. Wait for it or cancel it. - `job_active`: A job is working on one of its books (including a removed one). Wait for it or cancel it. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="listseriescharacters"></a>
### `GET /api/series/{series_id}/characters`

**List series character identities** · operation `listSeriesCharacters` · cost `none`

Returns the explicit cross-book identities of a series, ordered by name (case-insensitive) then ID, each with its confirmed book-character links. Works for removed series too.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `series_id` | path | string | yes | Series ID. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | list of [SeriesCharacter](#schema-seriescharacter) | Success. |
| 404 | [Error](#schema-error) | - `series_not_found`: No series has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="createseriescharacter"></a>
### `POST /api/series/{series_id}/characters`

**Create a series character identity** · operation `createSeriesCharacter` · cost `none`

Creates a series-level identity without linking or merging any book character; link book characters with `linkSeriesCharacter`. Duplicate names are allowed because a shared name is not a shared identity. Not idempotent. Refused for a removed series and while the series has an active run.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `series_id` | path | string | yes | Series ID. |

Request body (`application/json`): [SeriesNameRequest](#schema-seriesnamerequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [SeriesCharacter](#schema-seriescharacter) | Success. |
| 400 | [Error](#schema-error) | - `name_invalid`: The name is blank after whitespace is trimmed, or longer than 200 characters. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `series_not_found`: No series has this ID. |
| 409 | [Error](#schema-error) | - `series_archived`: The series is removed (archived). Restore it first; reads still work. - `series_run_active`: This series has an active processing run. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="getseriesmap"></a>
### `GET /api/series/{series_id}/map`

**Get the series map** · operation `getSeriesMap` · cost `none`

Returns `{series, characters, note}`: the series with its supplied, missing and planned volumes, and its explicit identities with confirmed links. Only confirmed identity links join characters across supplied titles; absent volumes contribute no inferred evidence. Works for removed series too (`series.archived` is then true).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `series_id` | path | string | yes | Series ID. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [SeriesMap](#schema-seriesmap) | Success. |
| 404 | [Error](#schema-error) | - `series_not_found`: No series has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="planseriesprocessing"></a>
### `POST /api/series/{series_id}/plan`

**Preview a series analysis run** · operation `planSeriesProcessing` · cost `none`

Previews staged analysis over the supplied, active books of an active series in reading order, without sending provider requests. Accepts `provider`, `phase`, `concurrency` and the same `limits` object used for per-book analysis. Providers must be cloud analysis providers (`gemini`, `openai` or `anthropic`); when omitted, the configured analysis provider is used, and a local provider setting is refused. Models come from runtime settings. Concurrency defaults to 2, is limited to 1 or 2, and applies to discovery only.

The response lists ordered supplied books with nested book plans, models, known requests and cost, volume slots, `limits_per_book`, notes and `plan_fingerprint`. Limits apply separately to each supplied book, so the possible collection-wide spend grows with the number of books. The plan can be empty when the series has no active books (starting it is then refused). Building the preview may fill disposable local caches; it creates no jobs or records.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `series_id` | path | string | yes | Series ID. |

Request body (`application/json`): [SeriesProcessingRequest](#schema-seriesprocessingrequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [SeriesPlan](#schema-seriesplan) | Success. |
| 400 | [Error](#schema-error) | - `provider_not_cloud`: The provider (or the configured analysis provider) is not `gemini`, `openai` or `anthropic`. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `series_not_found`: No series has this ID. |
| 409 | [Error](#schema-error) | - `series_archived`: The series is removed (archived). Restore it first; reads still work. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="startseriesprocessing"></a>
### `POST /api/series/{series_id}/process`

**Start a series analysis run** · operation `startSeriesProcessing` · cost `may_charge`

Queues a series run and returns its parent job immediately. Send the same body as the preview, adding the exact `plan_fingerprint` it returned as `expected_plan_fingerprint`.

The server recomputes the plan under its store lock and compares the supplied fingerprint **before creating jobs**. The fingerprint covers the plan, book revisions and source hashes, and relevant series context. A mismatch returns 409 `plan_stale` (as the step pipeline does) and queues no processing. Re-preview and review the new scope; do not silently replace the fingerprint and retry. The fingerprint is optional for direct API clients (omitting it skips the check), but the UI requires a nonempty accepted fingerprint and consumes its preview on dispatch. This is optimistic scope validation, not a reservation that freezes data between requests.

**Jobs.** The parent job has `kind: "series"` and `book_id: "series:SERIES_ID"`; `total` is the number of books. One child `analyze` job per supplied active book uses the real book ID and is created queued. Follow them with `GET /api/series/{series_id}/runs` or `GET /api/jobs`. While the run is active its books are reserved: edits to them and to the series are refused with 409. Cancelling the parent (`POST /api/jobs/{job_id}/cancel`) also stops its children.

**Execution.** Discovery (`scan`, and the first part of `full`) may run on two independent books at once; profiles and direction run one book at a time in reading order. Missing, planned and removed volumes do not run. A failed or allowance-limited book stops new work; queued or running children then end `interrupted` (or `cancelled`), and already finished outputs remain reusable. Full-run phases share each book's run request and token caps, while its dollar allowance includes earlier tracked spend. Outcomes appear in the jobs, not in this response. A run record is retained as a `series_run` artifact on each book.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `series_id` | path | string | yes | Series ID. |

Request body (`application/json`): [SeriesProcessingRequest](#schema-seriesprocessingrequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [Job](#schema-job) | The queued parent series job. |
| 400 | [Error](#schema-error) | - `provider_not_cloud`: The provider (or the configured analysis provider) is not `gemini`, `openai` or `anthropic`. - `series_empty`: The series has no supplied, active book. - `api_key_missing`: No API key is configured for the provider. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `series_not_found`: No series has this ID. |
| 409 | [Error](#schema-error) | - `series_archived`: The series is removed (archived). Restore it first; reads still work. - `plan_stale`: `expected_plan_fingerprint` does not match the recomputed plan. Nothing was queued. - `series_run_active`: This series already has an active run, or another series run holds one of its books. - `job_active`: A job is working on one of its supplied books. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |
| 503 | [Error](#schema-error) | - `shutting_down`: The series worker is not accepting work because the server is shutting down. The jobs just created are marked failed or interrupted and nothing runs. |

<a id="restoreseries"></a>
### `POST /api/series/{series_id}/restore`

**Restore a removed series** · operation `restoreSeries` · cost `none`

Restores a removed series. Restoring requires the series to be idle (as for `renameSeries`) and records a library-visibility artifact on each member book. Idempotent: when the series is already in the requested state, the call returns that state and changes nothing, performs no checks and records nothing. No request body.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `series_id` | path | string | yes | Series ID. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [SeriesArchiveState](#schema-seriesarchivestate) | Success. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `series_not_found`: No series has this ID. |
| 409 | [Error](#schema-error) | - `series_run_active`: This series has an active processing run, or an active series run reserves one of its books. Wait for it or cancel it. - `job_active`: A job is working on one of its books (including a removed one). Wait for it or cancel it. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="listseriesruns"></a>
### `GET /api/series/{series_id}/runs`

**List recent series runs** · operation `listSeriesRuns` · cost `none`

Returns `{"runs": [...]}` with up to 20 parent series jobs of this series, newest first, each with its child job records embedded as `children`. The parent uses `book_id: "series:SERIES_ID"`; children use real book IDs. Poll this route (or `GET /api/jobs`) to follow a run. Works for removed series too.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `series_id` | path | string | yes | Series ID. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [SeriesRuns](#schema-seriesruns) | Success. |
| 404 | [Error](#schema-error) | - `series_not_found`: No series has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="putseriesvolume"></a>
### `PUT /api/series/{series_id}/volumes`

**Add or update a volume placeholder** · operation `putSeriesVolume` · cost `none`

Creates a placeholder for a volume the library does not have, or replaces the title and status of the placeholder already at that position (upsert keyed by position). A placeholder holds reading order only; it never contributes text or knowledge, and missing or planned volumes do not block a series run. Assigning a real book to the same position later replaces the placeholder. Requires an active, idle series (as for `renameSeries`).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `series_id` | path | string | yes | Series ID. |

Request body (`application/json`): [SeriesVolumeRequest](#schema-seriesvolumerequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [SeriesVolumeSlot](#schema-seriesvolumeslot) | Success. |
| 400 | [Error](#schema-error) | - `position_taken`: A supplied book (including a removed one) already has this position. - `text_invalid`: The title contains control characters other than tab and newlines. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `series_not_found`: No series has this ID. |
| 409 | [Error](#schema-error) | - `series_archived`: The series is removed (archived). Restore it first; reads still work. - `series_run_active`: This series has an active processing run, or an active series run reserves one of its books. Wait for it or cancel it. - `job_active`: A job is working on one of its books (including a removed one). Wait for it or cancel it. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="deleteseriesvolume"></a>
### `DELETE /api/series/{series_id}/volumes/{position}`

**Remove a volume placeholder** · operation `deleteSeriesVolume` · cost `none`

Removes the placeholder at `position`. It never removes or detaches a supplied book. Idempotent: returns `removed: true` even when no placeholder was at that position. Requires an active, idle series (as for `renameSeries`).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `series_id` | path | string | yes | Series ID. |
| `position` | path | number | yes | Placeholder position as a decimal number, for example `3` or `2.5`. It must equal the stored number exactly. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [SeriesVolumeRemoval](#schema-seriesvolumeremoval) | Success. |
| 400 | [Error](#schema-error) | - `position_invalid`: The position is negative, above 1,000,000 or not finite. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `series_not_found`: No series has this ID. |
| 409 | [Error](#schema-error) | - `series_archived`: The series is removed (archived). Restore it first; reads still work. - `series_run_active`: This series has an active processing run, or an active series run reserves one of its books. Wait for it or cancel it. - `job_active`: A job is working on one of its books (including a removed one). Wait for it or cancel it. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

## Books

The book document and manual edits to its characters, passages and scenes.

<a id="getbook"></a>
### `GET /api/books/{book_id}`

**Get the full book document** · operation `getBook` · cost `none`

Returns the full reader projection: chapters with canonical text, scenes, passages with source offsets, the cast with voice choices, `revision`, and the analysis summary. Valid enhanced audio has a playback URL; an unavailable or stale selected take is presented as `null`. Server bookkeeping (edit locks, metadata locks, cache keys, single-provider voice fields of older versions) is not included. Read-only. Works for archived books. A book whose stored data is inconsistent (for example a passage naming a missing chapter) is a server defect (500 `internal_error`), never 404.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [Book](#schema-book) | Success. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="addcharacter"></a>
### `POST /api/books/{book_id}/characters`

**Add a character** · operation `addCharacter` · cost `none`

Adds a human-reviewed cast member with a new ID (`character-` + 12 hex). The body is the same `CharacterEdit` as editing, but `name` is required. Omitted fields start empty. Voices start as `{"gemini": {"id": "Kore"}}` plus a device (`system`) voice chosen from the installed voices the same way imported characters get one (none when no suitable voice is installed), then any `voices` sent are applied; sending `system: null` keeps the device choice at Default. Only the fields sent (always including `name`) are locked against generated analysis, so generated profile text may still fill the rest. Increments `revision`. Nothing is re-attributed; assign passages with the passage edit. Requires a non-archived, idle book (409). Returns the full, presented book.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |

Request body (`application/json`): [CharacterEdit](#schema-characteredit)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [Book](#schema-book) | Success. |
| 400 | [Error](#schema-error) | - `character_name_required`: `name` is missing. - `voice_provider_unknown`: `voices` names a provider other than `system`, `gemini` or `breeze`. - `library_voice_unavailable`: A `library` voice does not exist, is deleted, or belongs to another provider. - `breeze_voice_unavailable`: A Breeze `id` is not in the last Breeze voice check, or is not a usable (cloned) voice. - `seed_not_applicable`: A choice has a `seed` but is not a Breeze voice chosen by a nonblank `id`. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 409 | [Error](#schema-error) | - `job_active`: A job is queued or running for this book. - `series_run_active`: An active series run reserves this book. - `book_archived`: The book is archived; restore it first. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="editcharacter"></a>
### `PATCH /api/books/{book_id}/characters/{character_id}`

**Edit a character** · operation `editCharacter` · cost `none`

Updates any of `name`, `aliases`, `description`, `direction` and `voices` (body `CharacterEdit`). Renaming records the previous name in `former_names`, so later discovery still resolves it to this character. `voices` changes only the providers it names; see `CharacterEdit.voices` and `VoiceChoice` for the forms. For every provider, `null` or a blank `id` clears the choice, so Default applies. Choosing a Breeze voice by `id` pins it to the revision from the last Breeze check, from saved state only (no server request). Changing a voice or direction deselects that character's now-stale takes.

A manual edit locks each field whose value it actually changes, so generated analysis never overwrites it (editors may resend a whole form; unchanged values are not locked). Confirming a passage's current speaker (sending the same `speaker_id` while its `confidence` is below 1.0) is a change: it sets `confidence` to 1.0 and locks the speaker. The older phase-based analysis (Classic analysis) treats an item with any locked field as wholly reviewed. Lock state is server bookkeeping and is not part of the book document.

A request that changes nothing (an empty body, only omitted or `null` fields, or values equal to the current ones) is a no-op: nothing is saved or locked, `revision` does not change, and the current book is returned.

Edits require a book that is not archived (409 `book_archived`) and that no queued or running job (409 `job_active`) or active series run (409 `series_run_active`) holds. There is no optimistic concurrency check: the last write wins, and every edit that changes something increments the book `revision` by 1.

After a change, every passage's selected enhanced take is re-validated against its render recipe (passage text, speaker voice and direction, scene notes, provider, model). A take whose recipe no longer matches is deselected (the passage's `audio` becomes null). Its WAV bytes are kept, so restoring the previous values and rendering again reuses the archived take without a provider request. Every scene's `character_ids` is then recomputed (sorted) from its passages' speakers.

Omitted and `null` fields are ignored (except a passage `seed`, where `null` clears it); send an empty string or array to clear a value. Returns the full, presented book document.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `character_id` | path | string | yes | Book-local character ID. |

Request body (`application/json`): [CharacterEdit](#schema-characteredit)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [Book](#schema-book) | Success. |
| 400 | [Error](#schema-error) | - `voice_provider_unknown`: `voices` names a provider other than `system`, `gemini` or `breeze`. - `library_voice_unavailable`: A `library` voice does not exist, is deleted, or belongs to another provider. - `breeze_voice_unavailable`: A Breeze `id` is not in the last Breeze voice check, or is not a usable (cloned) voice. - `seed_not_applicable`: A choice has a `seed` but is not a Breeze voice chosen by a nonblank `id`. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `character_not_found`: No character in this book has this ID. |
| 409 | [Error](#schema-error) | - `job_active`: A job is queued or running for this book. - `series_run_active`: An active series run reserves this book. - `book_archived`: The book is archived; restore it first. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="listcharacterreferences"></a>
### `GET /api/books/{book_id}/characters/{character_id}/references`

**List source references to a character** · operation `listCharacterReferences` · cost `none`

Returns every source reference to this current cast member, unpaginated, in reading order (chapter, then offset): dialogue passages currently attributed to it, mentions of its name or aliases, and discovery evidence quotations, each with a source anchor. Dialogue and mentions are derived from the current book on every call, so manual edits, pipeline acceptance and analysis are all reflected at once. A name or alias that another cast member shares is not counted as a mention. Discovery evidence comes from the latest Classic analysis (including series runs) and is listed while its quote still matches the chapter text; other analyses record none. `narrator` and `unassigned` have no references. Read-only; nothing is written. Works for archived books.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `character_id` | path | string | yes | Book-local character ID. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | list of [CharacterReference](#schema-characterreference) | Success. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `character_not_found`: No character in the book's current cast has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="repairbookstructure"></a>
### `POST /api/books/{book_id}/repair-structure`

**Refresh structure metadata from the saved original** · operation `repairBookStructure` · cost `none`

Re-parses the saved original EPUB or TXT and replaces only chapter structure metadata (`title`, `kind`, `title_source`, `source_href`, `logical_sections`, `narrative_order`) and `structure_version`. IDs, text, offsets, passages, cast and annotations are kept. Automatic scene titles that began with the old chapter title are renamed; scene titles edited by hand are not. A saved analysis checkpoint is transformed to the new titles in the same transaction. Increments `revision`.

Refused, with existing work preserved, unless the re-parsed original has the same number of chapters with exactly the same text (400 `structure_mismatch`). Requires a known (404), non-archived and idle (409) book; these preconditions are checked first and a refused precondition records nothing. Runs locally with no provider request; every attempt that passes them, including one refused with 400, records a local `structure_repair` resource measurement. Returns the full, presented book.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [Book](#schema-book) | Success. |
| 400 | [Error](#schema-error) | - `original_missing`: The book has no saved original EPUB or TXT, or the saved file is missing. - `original_too_large`: The saved original is larger than 30 MiB (the import limit). - `original_unreadable`: The saved original could not be parsed (for example an unreadable EPUB). - `structure_mismatch`: The re-parsed source does not match the saved chapters or the saved analysis checkpoint. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 409 | [Error](#schema-error) | - `job_active`: A job is queued or running for this book. - `series_run_active`: An active series run reserves this book. - `book_archived`: The book is archived; restore it first. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="editscene"></a>
### `PATCH /api/books/{book_id}/scenes/{scene_id}`

**Edit a scene** · operation `editScene` · cost `none`

Updates any of `title`, `summary`, `tone` and `direction` of one scene (body `SceneEdit`). Scene tone and direction are part of every enhanced narration recipe of the scene's passages, so changing them deselects those takes. Scene boundaries cannot be edited here.

A manual edit locks each field whose value it actually changes, so generated analysis never overwrites it (editors may resend a whole form; unchanged values are not locked). Confirming a passage's current speaker (sending the same `speaker_id` while its `confidence` is below 1.0) is a change: it sets `confidence` to 1.0 and locks the speaker. The older phase-based analysis (Classic analysis) treats an item with any locked field as wholly reviewed. Lock state is server bookkeeping and is not part of the book document.

A request that changes nothing (an empty body, only omitted or `null` fields, or values equal to the current ones) is a no-op: nothing is saved or locked, `revision` does not change, and the current book is returned.

Edits require a book that is not archived (409 `book_archived`) and that no queued or running job (409 `job_active`) or active series run (409 `series_run_active`) holds. There is no optimistic concurrency check: the last write wins, and every edit that changes something increments the book `revision` by 1.

After a change, every passage's selected enhanced take is re-validated against its render recipe (passage text, speaker voice and direction, scene notes, provider, model). A take whose recipe no longer matches is deselected (the passage's `audio` becomes null). Its WAV bytes are kept, so restoring the previous values and rendering again reuses the archived take without a provider request. Every scene's `character_ids` is then recomputed (sorted) from its passages' speakers.

Omitted and `null` fields are ignored (except a passage `seed`, where `null` clears it); send an empty string or array to clear a value. Returns the full, presented book document.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `scene_id` | path | string | yes | Scene ID. |

Request body (`application/json`): [SceneEdit](#schema-sceneedit)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [Book](#schema-book) | Success. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `scene_not_found`: No scene in this book has this ID. |
| 409 | [Error](#schema-error) | - `job_active`: A job is queued or running for this book. - `series_run_active`: An active series run reserves this book. - `book_archived`: The book is archived; restore it first. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="editpassage"></a>
### `PATCH /api/books/{book_id}/segments/{segment_id}`

**Edit a passage** · operation `editPassage` · cost `none`

Updates any of `speaker_id`, `direction`, `cues` and `seed` of one passage (body `SegmentEdit`). Sending `speaker_id` sets `confidence` to 1.0; changing it also drops the passage's `speaker_check`. The text and offsets never change. A new `seed` makes seeded providers (Breeze) produce a new take, and `null` clears it so the speaker's voice seed applies; like other performance edits this deselects the current take while retaining its history.

A manual edit locks each field whose value it actually changes, so generated analysis never overwrites it (editors may resend a whole form; unchanged values are not locked). Confirming a passage's current speaker (sending the same `speaker_id` while its `confidence` is below 1.0) is a change: it sets `confidence` to 1.0 and locks the speaker. The older phase-based analysis (Classic analysis) treats an item with any locked field as wholly reviewed. Lock state is server bookkeeping and is not part of the book document.

A request that changes nothing (an empty body, only omitted or `null` fields, or values equal to the current ones) is a no-op: nothing is saved or locked, `revision` does not change, and the current book is returned.

Edits require a book that is not archived (409 `book_archived`) and that no queued or running job (409 `job_active`) or active series run (409 `series_run_active`) holds. There is no optimistic concurrency check: the last write wins, and every edit that changes something increments the book `revision` by 1.

After a change, every passage's selected enhanced take is re-validated against its render recipe (passage text, speaker voice and direction, scene notes, provider, model). A take whose recipe no longer matches is deselected (the passage's `audio` becomes null). Its WAV bytes are kept, so restoring the previous values and rendering again reuses the archived take without a provider request. Every scene's `character_ids` is then recomputed (sorted) from its passages' speakers.

Omitted and `null` fields are ignored (except a passage `seed`, where `null` clears it); send an empty string or array to clear a value. Returns the full, presented book document.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `segment_id` | path | string | yes | Passage (segment) ID. |

Request body (`application/json`): [SegmentEdit](#schema-segmentedit)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [Book](#schema-book) | Success. |
| 400 | [Error](#schema-error) | - `character_not_in_cast`: `speaker_id` is not a character in this book's cast. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `passage_not_found`: No passage in this book has this ID. |
| 409 | [Error](#schema-error) | - `job_active`: A job is queued or running for this book. - `series_run_active`: An active series run reserves this book. - `book_archived`: The book is archived; restore it first. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

## Pronunciations

Per-book respellings sent to narrators in place of a word; book text never changes.

<a id="listpronunciations"></a>
### `GET /api/books/{book_id}/pronunciations`

**List the book's pronunciations** · operation `listPronunciations` · cost `none`

Every entry with its use in the book: whole-word matches in chapter text, the passages containing it, how many of those have a current Studio take, and up to three examples. Nothing is generated or written; the usage is computed from the current text on each call.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PronunciationList](#schema-pronunciationlist) | Success. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="addpronunciation"></a>
### `POST /api/books/{book_id}/pronunciations`

**Add a pronunciation** · operation `addPronunciation` · cost `none`

Changing pronunciations requires an idle book. A render recipe records only the entries a passage used, so a change alters the audio identity of the passages containing that word and no others. Studio takes that no longer match are unselected (`retired_takes`); their WAVs stay archived and are reused without a request if the recipe returns. Simple listening, including simple saved performances, always uses the current entries: affected passages and chunks become uncached and are narrated again on demand. A cast performance keeps the entries it was created with (`pronunciation_count`); its plan notes when the book's entries have changed since, or that a performance made before pronunciations existed does not use them. Voice examples apply them too; `POST /api/books/{book_id}/voice-preview` can audition an unsaved respelling.

Limits: a multi-word term split across two passages is respelled in chapter chunks (one request spans both) but not in single-passage takes. Provider sentence timing (Breeze) stays in sent-text offsets; nothing maps it back to source offsets for clients yet.

Adds one entry; only `term` and `respelling` are required, and the server assigns `id` (an `id` in the body is ignored). At most 500 entries per book. Requires a non-archived, idle book (409).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |

Request body (`application/json`): [PronunciationEntry](#schema-pronunciationentry)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PronunciationSaved](#schema-pronunciationsaved) | Success. |
| 400 | [Error](#schema-error) | - `pronunciation_invalid`: The entry breaks a field rule (see the fields of `PronunciationEntry`). - `pronunciation_duplicate`: Another entry already has this term (under the case rules). - `pronunciation_limit_reached`: The book already has 500 entries. - `character_not_in_cast`: `character_id` is not a character in the book's current cast. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 409 | [Error](#schema-error) | - `job_active`: A job is queued or running for this book. - `series_run_active`: An active series run reserves this book. - `book_archived`: The book is archived; restore it first. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="updatepronunciation"></a>
### `PATCH /api/books/{book_id}/pronunciations/{entry_id}`

**Change a pronunciation** · operation `updatePronunciation` · cost `none`

Changing pronunciations requires an idle book. A render recipe records only the entries a passage used, so a change alters the audio identity of the passages containing that word and no others. Studio takes that no longer match are unselected (`retired_takes`); their WAVs stay archived and are reused without a request if the recipe returns. Simple listening, including simple saved performances, always uses the current entries: affected passages and chunks become uncached and are narrated again on demand. A cast performance keeps the entries it was created with (`pronunciation_count`); its plan notes when the book's entries have changed since, or that a performance made before pronunciations existed does not use them. Voice examples apply them too; `POST /api/books/{book_id}/voice-preview` can audition an unsaved respelling.

Limits: a multi-word term split across two passages is respelled in chapter chunks (one request spans both) but not in single-passage takes. Provider sentence timing (Breeze) stays in sent-text offsets; nothing maps it back to source offsets for clients yet.

The body has the same fields as for adding. Fields left out keep their saved values; `null` (or `{}` for `providers`) clears one. The merged entry must still have a `term` and a `respelling`, and is validated like a new one. A change that leaves the entry as it was saves nothing and does not change the book `revision`. Requires a non-archived, idle book (409).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `entry_id` | path | string | yes | Pronunciation entry ID (`pr_…`). |

Request body (`application/json`): [PronunciationEntry](#schema-pronunciationentry)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PronunciationSaved](#schema-pronunciationsaved) | Success. |
| 400 | [Error](#schema-error) | - `pronunciation_invalid`: The entry breaks a field rule (see the fields of `PronunciationEntry`). - `pronunciation_duplicate`: Another entry already has this term (under the case rules). - `pronunciation_limit_reached`: The book already has 500 entries. - `character_not_in_cast`: `character_id` is not a character in the book's current cast. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `pronunciation_not_found`: No entry in this book has this ID. |
| 409 | [Error](#schema-error) | - `job_active`: A job is queued or running for this book. - `series_run_active`: An active series run reserves this book. - `book_archived`: The book is archived; restore it first. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="deletepronunciation"></a>
### `DELETE /api/books/{book_id}/pronunciations/{entry_id}`

**Remove a pronunciation** · operation `deletePronunciation` · cost `none`

Changing pronunciations requires an idle book. A render recipe records only the entries a passage used, so a change alters the audio identity of the passages containing that word and no others. Studio takes that no longer match are unselected (`retired_takes`); their WAVs stay archived and are reused without a request if the recipe returns. Simple listening, including simple saved performances, always uses the current entries: affected passages and chunks become uncached and are narrated again on demand. A cast performance keeps the entries it was created with (`pronunciation_count`); its plan notes when the book's entries have changed since, or that a performance made before pronunciations existed does not use them. Voice examples apply them too; `POST /api/books/{book_id}/voice-preview` can audition an unsaved respelling.

Limits: a multi-word term split across two passages is respelled in chapter chunks (one request spans both) but not in single-passage takes. Provider sentence timing (Breeze) stays in sent-text offsets; nothing maps it back to source offsets for clients yet.

Removes the entry. Removing the last one removes the book's `pronunciations` field. Requires a non-archived, idle book (409).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `entry_id` | path | string | yes | Pronunciation entry ID (`pr_…`). |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PronunciationSaved](#schema-pronunciationsaved) | Success. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `pronunciation_not_found`: No entry in this book has this ID. |
| 409 | [Error](#schema-error) | - `job_active`: A job is queued or running for this book. - `series_run_active`: An active series run reserves this book. - `book_archived`: The book is archived; restore it first. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

## Classic analysis

The older phase-based story analysis, its plan preview and local preprocessing.

<a id="getanalysisstatus"></a>
### `GET /api/books/{book_id}/analysis`

**Get classic analysis progress** · operation `getAnalysisStatus` · cost `none`

Checkpoint summary of the classic analysis engine, including per-chapter progress. Returns `status: "not_started"` (with one pending row per chapter) when no checkpoint exists.

The checkpoint belongs to the latest run that saved progress and survives failures, cancellation and restarts (a server restart marks a running checkpoint `interrupted`). Follow a running analysis through its job (`GET /api/jobs`); use this for per-chapter detail. The step pipeline (`/analysis-pipeline`) does not write this checkpoint. This GET creates and changes no domain records: no artifacts, decisions, resource-ledger rows, jobs or book changes.

The classic engine may be retired in favor of the step pipeline.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID, from the library or the import response. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [AnalysisStatus](#schema-analysisstatus) | Success. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="previewclassicanalysis"></a>
### `POST /api/books/{book_id}/analysis-plan`

**Preview classic analysis work** · operation `previewClassicAnalysis` · cost `none`

Previews the work `POST /api/books/{book_id}/analyze` would do for the same `AnalysisRequest`, without provider inference. Returns the requested phase, provider and configured models, pending requests, cached units, token and cost estimates, requests by stage, coverage, notes, and the supplied `limits` (with defaults applied; the preview does not enforce them).

The estimate covers currently known work before retries and evidence repairs; `full` can discover more work. Cost is approximate (null when a model has no known price); the run's request guard reserves more conservatively. Unlike `analyze`, the preview works on archived books and while a job is running.

Side effects, all local and none of them changing the book: it caches and retains the census (a `census` artifact, and a `census` resource operation when computed fresh), imports validated discovery found only in an older checkpoint into the unit cache (with its artifacts), and retains `series_context` artifacts for linked earlier volumes.

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

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID, from the library or the import response. |

Request body (`application/json`): [AnalysisRequest](#schema-analysisrequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [AnalysisPlan](#schema-analysisplan) | Success. |
| 400 | [Error](#schema-error) | - `unknown_chapter`: The body's `chapter_id` is not a chapter of this book. - `unknown_provider`: The provider (from the body, or the saved default) is not `local`, `gemini`, `openai` or `anthropic`. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="startclassicanalysis"></a>
### `POST /api/books/{book_id}/analyze`

**Start or resume classic analysis** · operation `startClassicAnalysis` · cost `may_charge`

Queues an `analyze` job and returns it immediately. Poll `GET /api/jobs` until it is terminal; failures, cancellation and allowance stops (`budget_limited`) appear in the job, not as HTTP errors. Cancel with `POST /api/jobs/{job_id}/cancel`; a remote request already sent can still complete and be billed. To resume after a stop, failure or restart, call this endpoint again (an old job ID is never revived).

Send the same body to `POST /api/books/{book_id}/analysis-plan` first. Dispatching it with a cloud provider may incur charges; `provider: "local"` never contacts a provider. There is no server-enforced preview fingerprint for per-book runs (series runs have one); the UI invalidates its preview when local inputs change.

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

The provider and configured models are snapshotted when the job is queued; the job carries `provider`, `model`, `scan_model`, `phase` and `chapter_id`.

Resuming (`resume: true`, the default) reuses validated saved units instead of requesting them again. Every HTTP attempt, including retries and evidence repairs, is reserved against `limits` before it is sent. Accepted units and completed chapter work survive later failures. Human edits remain authoritative, affected enhanced takes become stale, and source text is never replaced by model output. Whole-book scan coverage and profile freshness are separate (see `GET /api/books/{book_id}/preprocessing`). The book is updated (new revision) as each chapter stage is published. The run retains the census and any validated discovery imported from an older checkpoint as artifacts.

The classic engine may be retired in favor of the step pipeline.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID, from the library or the import response. |

Request body (`application/json`): [AnalysisRequest](#schema-analysisrequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [Job](#schema-job) | The queued `analyze` job. |
| 400 | [Error](#schema-error) | - `unknown_chapter`: The body's `chapter_id` is not a chapter of this book. - `unknown_provider`: The provider (from the body, or the saved default) is not `local`, `gemini`, `openai` or `anthropic`. - `gemini_key_missing`: The provider is `gemini` and no Gemini API key is configured. - `api_key_missing`: The provider is `openai` or `anthropic` and no API key is configured for it. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 409 | [Error](#schema-error) | - `book_archived`: The book is archived. Restore it first. - `job_active`: A job is already queued or running for this book. - `series_run_active`: An active series run has reserved this book. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |
| 503 | [Error](#schema-error) | - `shutting_down`: The server is shutting down and accepts no new work. No job was started. |

<a id="getanalysispreprocessing"></a>
### `GET /api/books/{book_id}/preprocessing`

**Get the local census, discovery coverage and profile freshness** · operation `getAnalysisPreprocessing` · cost `none`

Free local census (names, speech tags, dialogue counts and heuristic priority per character; words and token estimates per chapter), semantic source coverage from validated cloud discovery, tracked analysis usage, and profile freshness/provisional state. No provider is contacted.

This GET creates and changes no domain records: no artifacts, decisions, resource-ledger rows, jobs or book changes. It may write one disposable derived cache: the census is computed and cached when the book's text, structure, cast or attributions changed since it was last cached. The cache can be deleted without loss and is rebuilt on demand. Validated discovery found only in an older checkpoint counts toward coverage but is not imported here, and the census is not retained as an artifact here; analysis runs and `POST /api/books/{book_id}/analysis-plan` do both.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID, from the library or the import response. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [AnalysisCoverage](#schema-analysiscoverage) | Success. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

## Analysis pipeline

The step pipeline: step settings, previewed runs, and versioned results to accept or reject.

<a id="getanalysispipeline"></a>
### `GET /api/analysis-pipeline`

**List pipeline steps, providers and saved step settings** · operation `getAnalysisPipeline` · cost `none`

Step definitions in pipeline order, each listing its allowed `providers` and its effective `settings` (`{provider, model, gate, saved, saved_invalid}`), and every provider with `kind` (`model` or `service`), `self_hosted`, `needs` (`api_key` or `url`) and `configured` (a key or URL is set; not a reachability check). The Local LLM entry lists curated `models`. Service providers take `model: null`.

Read-only; contacts no server.

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PipelineDefinitions](#schema-pipelinedefinitions) | Success. |

<a id="saveanalysispipelinestepsettings"></a>
### `PUT /api/analysis-pipeline/steps/{step_id}/settings`

**Save a step's provider, model and gate** · operation `saveAnalysisPipelineStepSettings` · cost `none`

Saves the library-wide default provider/model and gate (`auto` or `review`) for one step, replacing any previous choice. For a local (plain) step only `provider: "local"` with no model is accepted. A service provider (`booknlp`, `novel_analyzer`) takes no model. A model provider needs a model ID matching `[A-Za-z0-9][A-Za-z0-9._:-]{0,199}`. An omitted or null `gate` saves the step's `default_gate`. Idempotent. Never starts work and contacts no server. Returns the effective settings for the step.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `step_id` | path | string | yes | Step ID: one of `structure`, `census`, `discovery`, `quotes`, `profiles` and `directing` (in pipeline order; the list is defined by the server and may grow). An unknown ID returns 404 `step_not_found`. |

Request body (`application/json`): [StepSettings](#schema-stepsettings)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PipelineStepSettingsView](#schema-pipelinestepsettingsview) | Success. |
| 400 | [Error](#schema-error) | - `step_config_invalid`: The provider is not allowed for this step, a local step was given a model or another provider, a service provider was given a model, or the model ID is missing or malformed. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `step_not_found`: The step ID in the path is unknown. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="getbookanalysispipeline"></a>
### `GET /api/books/{book_id}/analysis-pipeline`

**Get the pipeline state of a book** · operation `getBookAnalysisPipeline` · cost `none`

Per-step accepted and total scopes, `has_accepted` (any accepted version), accepted origins, stale scopes, pending candidates and the latest version; the active run and the 5 most recent runs; and the chapter list.

Read-only: it records nothing and contacts no server. When the book changed outside the pipeline since the pipeline last recorded it, the accepted counts, origins and stale scopes already reflect those changes as the next plan, run, preview or accept will record them (as `baseline`/`external` versions). Those capture versions are not listed in `latest` or the version history until they are recorded.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PipelineBookOverview](#schema-pipelinebookoverview) | Success. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="planbookanalysispipelinerun"></a>
### `POST /api/books/{book_id}/analysis-pipeline/plan`

**Preview the work and cost of a run** · operation `planBookAnalysisPipelineRun` · cost `none`

Builds each requested step's units from the currently accepted inputs (steps run in pipeline order whatever the request order) and reports units, cached units, model `requests`, `service_calls` (free calls to self-hosted services, not counted as model requests), token and cost estimates, `inputs_pending`, `missing_inputs` (per step, and `{step: [inputs]}` overall) and a `fingerprint`. No model or service calls. Estimates cover known work before retries or evidence repairs; a step whose input is in the same request is estimated from the input's current accepted result.

Missing inputs do not fail the plan (they are reported); a run with them is refused. Omitted `configs` entries use the saved step settings, which are revalidated: an LLM step whose saved or default settings name no model is refused. A removed book can be planned.

The `fingerprint` covers the book revision, the chapter selection, `fresh`, and each step's version, provider, model and exact unit identities. It does not cover `mode`, `gates`, `concurrency`, `limits` or which units are cached. Send the same `steps`, `chapter_ids`, `configs` and `fresh` to the run, because they are part of the fingerprint.

Not purely read-only: Before answering, the server records outside changes (`projection.sync`): when the capturable content of the book no longer matches what the accepted versions explain, it stores the current state as new `baseline` (first time) or `external` versions and accepts them (decision modes `baseline`/`external`). The book itself is not changed. This is skipped cheaply when a digest of the captured content is unchanged. Building units may also store free local census caches.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |

Request body (`application/json`): [PlanRequest](#schema-planrequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PipelinePlan](#schema-pipelineplan) | Success. |
| 400 | [Error](#schema-error) | - `unknown_step`: `steps` names a step ID the server does not know. - `step_config_invalid`: A `configs` entry does not fit its step: a local step was given a provider other than `local` or a model, the provider is not one of the step's `providers`, a service provider was given a model, or the model ID is missing or malformed. - `step_model_missing`: A step without a `configs` entry uses its saved or default settings, and they name no model for an LLM provider. Save a model for the step, or send one in `configs`. - `chapter_ids_empty`: `chapter_ids` is an empty list (send null for every eligible chapter). - `unknown_chapter`: `chapter_ids` names a chapter that is not in this book. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="startbookanalysispipelinerun"></a>
### `POST /api/books/{book_id}/analysis-pipeline/runs`

**Queue a pipeline run** · operation `startBookAnalysisPipelineRun` · cost `may_charge`

Queues one job of kind `pipeline` running the requested steps and returns `{job, run}` immediately. Follow the job through `GET /api/jobs` and cancel it through the jobs API; the run record appears in this book's pipeline overview. A run never writes the book: it records candidate versions, and a step whose gate is `auto` is accepted when it completes (decision mode `auto`). Steps in the same run that require a step left for review, failed or without an accepted result are skipped. The returned `run` is a snapshot taken when the run was queued (`status: queued`, empty `step_run_ids`); poll for progress.

Checks, in order: every step ID must be known (400 `unknown_step`, before anything else); the worker must not be stopping; the book must exist, not be removed, have no active job and not be reserved by an active series run; `chapter_ids` and `configs` must be valid, and each step's saved or default settings must name a model when its provider needs one; every provider the run contacts must have an API key or server URL configured (local steps and `offline_providers` need none); every step's required inputs must have an accepted result or be in the same run; and the run must be authorized by either `expected_fingerprint` (a confirmed plan) or at least one explicit limit. When `expected_fingerprint` is sent, the plan is recomputed and must match.

Provider keys and server URLs, per-step provider/model and gates are snapshotted now; later settings changes do not affect queued work. `limits` is optional and uncapped by default: the confirmed plan is the authorization. Every paid attempt is reserved and recorded either way; each unit has at most four HTTP attempts (two transport attempts for each of at most two generations), and validated units are cached and reused unless `fresh`. Before answering, the server records outside changes (`projection.sync`): when the capturable content of the book no longer matches what the accepted versions explain, it stores the current state as new `baseline` (first time) or `external` versions and accepts them (decision modes `baseline`/`external`). The book itself is not changed. This is skipped cheaply when a digest of the captured content is unchanged.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |

Request body (`application/json`): [RunRequest](#schema-runrequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PipelineRunStarted](#schema-pipelinerunstarted) | The queued job and run. Not a result: poll the job until it is terminal. |
| 400 | [Error](#schema-error) | - `unknown_step`: `steps` names a step ID the server does not know. - `step_config_invalid`: A `configs` entry does not fit its step: a local step was given a provider other than `local` or a model, the provider is not one of the step's `providers`, a service provider was given a model, or the model ID is missing or malformed. - `step_model_missing`: A step without a `configs` entry uses its saved or default settings, and they name no model for an LLM provider. Save a model for the step, or send one in `configs`. - `chapter_ids_empty`: `chapter_ids` is an empty list (send null for every eligible chapter). - `unknown_chapter`: `chapter_ids` names a chapter that is not in this book. - `api_key_missing`: A cloud provider the run contacts has no API key configured (the detail lists every missing key and server URL). - `server_url_missing`: Only self-hosted providers are missing: a server URL the run contacts is not configured. - `step_inputs_missing`: A step's required input has no accepted result and is not in this run. - `run_unconfirmed`: Neither `expected_fingerprint` nor any limit was sent. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 409 | [Error](#schema-error) | - `book_archived`: The book is removed (archived). Restore it first. - `series_run_active`: An active series run reserves this book. - `job_active`: A job is already working on this book. - `plan_stale`: The plan changed since the preview (`expected_fingerprint` does not match). Preview again; nothing was queued. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |
| 503 | [Error](#schema-error) | - `shutting_down`: The local worker is stopping and accepts no new runs. |

<a id="listanalysispipelinestepversions"></a>
### `GET /api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions`

**List a step's versions and decisions** · operation `listAnalysisPipelineStepVersions` · cost `none`

Version history, newest first, with each version's review `state` (`candidate`, `accepted`, `partly_accepted`, `superseded`, `same_as_accepted`, `rejected`, `running`, `empty`) and the 50 most recent decisions. Includes recorded `baseline`/`external` captures. Read-only (does not record outside changes).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `step_id` | path | string | yes | Step ID: one of `structure`, `census`, `discovery`, `quotes`, `profiles` and `directing` (in pipeline order; the list is defined by the server and may grow). An unknown ID returns 404 `step_not_found`. |
| `limit` | query | integer |  | Maximum versions to return. Default 50; values are clamped to 1–200 (never an error). (default `50`) |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PipelineVersionHistory](#schema-pipelineversionhistory) | Success. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `step_not_found`: The step ID in the path is unknown. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="getanalysispipelinestepversion"></a>
### `GET /api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions/{version_id}`

**Get a version's result table, diffed against another version** · operation `getAnalysisPipelineStepVersion` · cost `none`

The step's generic result table (`stats`, `columns`, paged `rows`) for a version, diffed by row ID against `compare` (`accepted`, another version, or `none`), with `changed_only` and `scope` filters. `{version_id}` may be `accepted`. With a comparison, each row gains `_diff` (`added`, `changed` or `same`) and, unless added, `_changed` (changed column keys) and `_previous` (the compared values of those keys); rows only in the compared version are counted as `removed` but not returned. `diff` reports `same/changed/added/removed` and an `agreement` ratio, a cheap signal when comparing models. No comparison happens when `compare` is `none`, equals `{version_id}`, or resolves to no results (for example `accepted` when nothing is accepted); then `diff.compared_with` is null and rows carry no diff fields.

Rows are summarized against the book's current state (current names and passage text). A version still running has no scopes yet and returns an empty table. Paging is clamped, never an error. Read-only.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `step_id` | path | string | yes | Step ID: one of `structure`, `census`, `discovery`, `quotes`, `profiles` and `directing` (in pipeline order; the list is defined by the server and may grow). An unknown ID returns 404 `step_not_found`. |
| `version_id` | path | string | yes | A step version ID from the version history, or `accepted` to address the currently accepted version of every scope. |
| `compare` | query | string |  | What to diff against: `accepted` (default), another step version ID of this step, or `none`. (default `"accepted"`) |
| `scope` | query | string \| null |  | Return only rows of this scope (a chapter ID, character ID or `book`). Filters rows, not `diff` counts. |
| `changed_only` | query | boolean |  | When true, return only rows whose `_diff` is `changed` or `added` (none without a comparison). Default false. (default `false`) |
| `offset` | query | integer |  | Rows to skip (default 0). A negative value is treated as 0. (default `0`) |
| `limit` | query | integer |  | Page size (default 200), clamped to 1–1000. (default `200`) |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PipelineVersionDetail](#schema-pipelineversiondetail) | Success. |
| 400 | [Error](#schema-error) | - `unknown_version`: `compare` names a version that does not exist, or belongs to another book or another step. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `step_not_found`: The step ID in the path is unknown. - `step_version_not_found`: The version does not exist, or belongs to another book or another step. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="acceptanalysispipelinestepversion"></a>
### `POST /api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions/{version_id}/accept`

**Accept a version (also used to roll back)** · operation `acceptAnalysisPipelineStepVersion` · cost `none`

`{scopes?, expected_revision?}`. Accepting an older version is rollback. In one short transaction: records outside changes, computes the impact, appends a `user` decision and moves the accepted heads, applies the step's accepted versions to the book (manual edits are field-level locks and are kept, reported as `conflicts`), gives a default device voice only to characters this acceptance adds, clears only the narration takes that were valid before and are not after, increments the book revision and saves. Accepting a model step's run version also sets the book's analysis summary (provider, model, `partial`). Omitted `scopes` means every scope of the version. Accepting `accepted` re-applies the current versions and still bumps the revision.

Send `expected_revision` (from preview) to refuse the accept when the book changed after the preview. A running `pipeline` job on the book does not block accepting; other active jobs do.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `step_id` | path | string | yes | Step ID: one of `structure`, `census`, `discovery`, `quotes`, `profiles` and `directing` (in pipeline order; the list is defined by the server and may grow). An unknown ID returns 404 `step_not_found`. |
| `version_id` | path | string | yes | A step version ID from the version history, or `accepted` to address the currently accepted version of every scope. |

Request body (`application/json`): [DecisionRequest](#schema-decisionrequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PipelineAcceptResult](#schema-pipelineacceptresult) | Success. |
| 400 | [Error](#schema-error) | - `scopes_empty`: `scopes` is an empty list (send null for every scope of the version). - `unknown_scope`: `scopes` names a scope this version does not contain. - `version_empty`: The version has no results. - `version_incompatible`: A selected result does not fit the book. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `step_not_found`: The step ID in the path is unknown. - `step_version_not_found`: The version does not exist, or belongs to another book or another step. |
| 409 | [Error](#schema-error) | - `version_running`: The version is still running. - `book_archived`: The book is removed (archived). Restore it first. - `series_run_active`: An active series run reserves this book. - `job_active`: Another job (not a pipeline run) is changing this book. - `plan_stale`: The book revision differs from `expected_revision`: the book changed after the preview. Preview again. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="previewanalysispipelinestepversion"></a>
### `POST /api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions/{version_id}/preview`

**Preview what accepting a version would change** · operation `previewAnalysisPipelineStepVersion` · cost `none`

`{scopes?}` → changed and unchanged scopes, `conflicts` with manual edits (generated values that will not be applied because a person edited the field), narration takes that would be invalidated, downstream steps with accepted versions, and the book `revision` to send as `expected_revision` when accepting. `expected_revision` is ignored here. Omitted `scopes` means every scope of the version. For an accumulative step (discovery) only changed scopes are applied. Allowed on a running or empty version (the result is then empty) and on a removed book.

Not purely read-only: Before answering, the server records outside changes (`projection.sync`): when the capturable content of the book no longer matches what the accepted versions explain, it stores the current state as new `baseline` (first time) or `external` versions and accepts them (decision modes `baseline`/`external`). The book itself is not changed. This is skipped cheaply when a digest of the captured content is unchanged.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `step_id` | path | string | yes | Step ID: one of `structure`, `census`, `discovery`, `quotes`, `profiles` and `directing` (in pipeline order; the list is defined by the server and may grow). An unknown ID returns 404 `step_not_found`. |
| `version_id` | path | string | yes | A step version ID from the version history, or `accepted` to address the currently accepted version of every scope. |

Request body (`application/json`): [DecisionRequest](#schema-decisionrequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PipelineAcceptImpact](#schema-pipelineacceptimpact) | Success. |
| 400 | [Error](#schema-error) | - `scopes_empty`: `scopes` is an empty list (send null for every scope of the version). - `unknown_scope`: `scopes` names a scope this version does not contain. - `version_incompatible`: A selected result does not fit the book (for example a structure version for different chapters). |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `step_not_found`: The step ID in the path is unknown. - `step_version_not_found`: The version does not exist, or belongs to another book or another step. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="rejectanalysispipelinestepversion"></a>
### `POST /api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions/{version_id}/reject`

**Reject a candidate version** · operation `rejectAnalysisPipelineStepVersion` · cost `none`

`{scopes?}` → the appended `reject` decision (mode `user`). Records a decision only; the book, the accepted versions and retained results are unchanged, and the version remains inspectable (its state becomes `rejected`). The book must exist and not be removed. Omitted `scopes` means every scope of the version; `expected_revision` is ignored. Not refused while jobs run.

Acceptance is a decision, not content equality: a version that was accepted and is still current for a selected scope cannot be rejected (accept another version to replace it), but a never-accepted version whose results equal the accepted content (`same_as_accepted`) can be. Rejecting it declines that run; the identical accepted content stays accepted through the version that was accepted.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `step_id` | path | string | yes | Step ID: one of `structure`, `census`, `discovery`, `quotes`, `profiles` and `directing` (in pipeline order; the list is defined by the server and may grow). An unknown ID returns 404 `step_not_found`. |
| `version_id` | path | string | yes | A step version ID from the version history, or `accepted` to address the currently accepted version of every scope. |

Request body (`application/json`): [DecisionRequest](#schema-decisionrequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PipelineDecision](#schema-pipelinedecision) | Success. |
| 400 | [Error](#schema-error) | - `scopes_empty`: `scopes` is an empty list (send null for every scope of the version). - `unknown_scope`: `scopes` names a scope this version does not contain. - `version_empty`: The version has no results. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `step_not_found`: The step ID in the path is unknown. - `step_version_not_found`: The version does not exist, or belongs to another book or another step. |
| 409 | [Error](#schema-error) | - `version_running`: The version is still running. - `book_archived`: The book is removed (archived). Restore it first. - `version_accepted`: `{version_id}` is `accepted`, or this version was accepted and is still the accepted version of a selected scope. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

## Inspection

Read-only views of stages, artifacts, the story map, passage search and resource usage.

<a id="exportbookanalysis"></a>
### `GET /api/books/{book_id}/analysis-export`

**Download the portable analysis bundle** · operation `exportBookAnalysis` · cost `none`

Portable ZIP (format `spintails-analysis`, version 1: the identifier predates the product rename and is kept) of the source and reader projection, graph, observations, references, series links, attempts and events, immutable artifact history, transitive input dependencies, and saved resource and listening metadata where available. No generated audio is required. Sent as an attachment named `<book title>-analysis.zip` (title reduced to word characters, spaces, dots and hyphens, at most 80 characters; `book` if empty).

Contents:

| File | Content |
| --- | --- |
| `manifest.json` | `{schema_version: 2, format: "spintails-analysis", exported_at, book_id, artifact_count, external_book_dependencies, audio_files_included: false, source_text_included: true, word_alignment: false, coordinate_system, notes}`. Start here. |
| `README.txt` | Plain-text guide to the bundle. |
| `book.json` | The stored book document, including chapter text, passage IDs and stored take metadata (not the API presentation of `GET /api/books/{book_id}`). |
| `story-map.json` | Same body as `GET /api/books/{book_id}/story-map`. |
| `series.json` | `{membership, links, series_characters}` for the book's series (nulls/empty when none). |
| `observations.json` | Retained character observations of the book. |
| `references.json` | Saved character references (as in the story map). |
| `analysis-attempts.json` | Every recorded analysis HTTP attempt of the book, oldest first, each in the `PipelineAttempt` shape of `GET /api/books/{book_id}/pipeline` (the same field allowlist, with `validation_state`). |
| `artifacts.jsonl` | One artifact per line, as `GET …/artifacts/{artifact_id}` returns it (metadata, `dependency_links`, `payload`), oldest first. |
| `pipeline-events.jsonl` | Every analysis event of the book, one per line, oldest first. |
| `resource-operations.json`, `listening-sessions.json`, `listening-takes.json`, `listening-chunks.json` | The book's saved rows, when those tables exist (possibly empty arrays). |

The ZIP includes all retained versions belonging to the selected book and the transitive artifact dependencies needed by them, which can include source excerpts and observations from earlier books. Take metadata identifies separately stored audio assets; the ZIP excludes all audio binaries (enhanced takes, simple-listening WAVs and voice-preview audio), API keys and settings credentials. Voice-preview records are not included. Some legacy outputs lack original prompts or exact attempt provenance; the export marks that absence (`legacy_provenance`) rather than reconstructing it.

No provider is contacted. This GET creates and changes no domain records: no artifacts, decisions, resource-ledger rows, jobs or book changes. For audio, use the audiobook export (`GET /api/books/{book_id}/export`).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID, from the library or the import response. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | `application/zip` | The analysis bundle as a ZIP attachment. |
| 206 | `application/zip` | Partial content for a `Range` request (served from a file; see `Content-Range`). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 416 |  | The requested `Range` cannot be satisfied (empty body; see `Content-Range`). |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="listbookartifacts"></a>
### `GET /api/books/{book_id}/artifacts`

**List retained artifact versions** · operation `listBookArtifacts` · cost `none`

`{items, total, offset, limit}` page of artifact metadata owned by the book, newest first. Payloads are not included; fetch one version for its payload. Artifact metadata includes kind, logical key, stage, creation time, provider/model where recorded, `is_current`, schema version and legacy-provenance state. Historical or rejected outputs remain inspectable without becoming accepted knowledge.

`limit` and `offset` are clamped (to 1–200 and at least 0); an offset past the end returns an empty page. No provider is contacted. This GET creates and changes no domain records: no artifacts, decisions, resource-ledger rows, jobs or book changes. Legacy data is retained as artifacts (marked `legacy_provenance`) once, when the server starts.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID, from the library or the import response. |
| `kind` | query | string \| null |  | Only this artifact kind (exact match). Optional. |
| `stage` | query | string \| null |  | Only this artifact stage (exact match). Optional. |
| `current` | query | boolean \| null |  | `true` for current selections only, `false` for non-current versions only; omit for all versions. |
| `limit` | query | integer |  | Page size; default 30, clamped to 1–200. (default `30`) |
| `offset` | query | integer |  | Versions to skip; default 0, negative values become 0. (default `0`) |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [ArtifactPage](#schema-artifactpage) | Success. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="getbookartifact"></a>
### `GET /api/books/{book_id}/artifacts/{artifact_id}`

**Get one artifact version with its payload** · operation `getBookArtifact` · cost `none`

Metadata plus the literal `payload`, dependency IDs, and `dependency_links: [{id, book_id}]`. An artifact owned by another book returns 404 under this book's path: follow the recorded owner in `dependency_links` to inspect earlier-book inputs. No provider is contacted. This GET creates and changes no domain records: no artifacts, decisions, resource-ledger rows, jobs or book changes.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID, from the library or the import response. |
| `artifact_id` | path | string | yes | Artifact version ID, from a list, a dependency or an event. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [ArtifactDetail](#schema-artifactdetail) | Success. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `artifact_not_found`: This book owns no artifact with this ID (including an artifact owned by another book). |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="getpipelineinspector"></a>
### `GET /api/books/{book_id}/pipeline`

**Inspect the processing pipeline** · operation `getPipelineInspector` · cost `none`

Versioned envelope with stage IDs, status, counts and dependencies; retained artifact counts and kinds; the book's recent jobs; the newest 100 analysis attempts with their validation state; the newest 100 events; tracked usage; capabilities; and notes. Word alignment is explicitly planned (not implemented).

The pipeline is an inspector, not a generic dependency scheduler. Its stage counts have different units and must not be summed into a global completion percentage. An HTTP 200 attempt does not mean its output passed validation: use `validation_state`.

No provider is contacted. This GET creates and changes no domain records: no artifacts, decisions, resource-ledger rows, jobs or book changes. Like `GET /api/books/{book_id}/preprocessing`, it may write the disposable census cache. Legacy data from versions before artifacts existed is retained as artifacts (marked `legacy_provenance`) once, when the server starts, not by this request.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID, from the library or the import response. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PipelineInspector](#schema-pipelineinspector) | Success. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="getbookresourceusage"></a>
### `GET /api/books/{book_id}/resources`

**Get recorded resource usage** · operation `getBookResourceUsage` · cost `none`

Recorded work for the book: analysis HTTP attempts (the analysis ledger), local and narration operations (which supplement it without double-counting), and cache reuse. Returns `schema_version`, the book/run scope, `totals`, stage aggregates and run aggregates; a page of `operations`, `total_operations` and the effective `limit`/`offset`; `total_runs`, unmeasured-run counts, price-source URLs and interpretation notes.

`limit` and `offset` are clamped (to 1–200 and at least 0), as for every paged operation. Aggregates cover the entire selected scope, not just the current page. The run summary list is bounded to 100; the total run count is reported separately.

Operations distinguish request count, reported tokens and cache tokens, retained estimates and reservations, elapsed time, opted-in local Python thread CPU time, audio seconds, output bytes and cache reuse. Missing measurements stay null (unknown) and are accompanied by coverage counters. Historical runs can exist without measurements. Costs are dated estimates, not provider invoices or available credits. CPU excludes subprocesses, GPUs and remote machines. Cached work does not represent another provider call. Reads (GET requests, including searches and the analysis export) are not recorded. Never contacts a provider or backfills guessed usage. This GET creates and changes no domain records: no artifacts, decisions, resource-ledger rows, jobs or book changes.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID, from the library or the import response. |
| `limit` | query | integer |  | Page size for `operations`; default 100, clamped to 1–200. (default `100`) |
| `offset` | query | integer |  | Rows to skip in `operations`; default 0, negative values become 0. (default `0`) |
| `run_id` | query | string \| null |  | Only rows (and the run) with this job ID. Optional. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [ResourceSummary](#schema-resourcesummary) | Success. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="searchbookpassages"></a>
### `GET /api/books/{book_id}/search`

**Search passages by words** · operation `searchBookPassages` · cost `none`

`{available, query, scope, items, note}`. Each item identifies book, chapter and passage, the exact source text, range and chapter hash, and lexical rank.

Search words (runs of letters, digits and underscores) are combined with AND; this is not an exact-phrase or operator query language, or semantic embedding search. A query with no words returns no matches with a note. If SQLite lacks FTS5, `available: false` explains that limitation; it does not start a fallback model call. Lower lexical rank means a stronger text match, not identity or speaker confidence. Only passages whose text matches their source offsets are searchable.

No provider is contacted. This GET creates and changes no domain records: no artifacts, decisions, resource-ledger rows, jobs or book changes. It may write one disposable derived cache: a local full-text index of each searched book's passages, refreshed when the passages changed. The index can be deleted without loss and is rebuilt on demand.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID, from the library or the import response. |
| `q` | query | string | yes | Search text, 1–300 characters. Required. |
| `scope` | query | string |  | `book` (default) searches this book. `earlier` also searches strictly earlier active (not archived) volumes of the book's confirmed series; an archived series searches only this book. (default `"book"`) |
| `limit` | query | integer |  | Maximum results; default 20, clamped to 1–50. (default `20`) |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PassageSearchResult](#schema-passagesearchresult) | Success. |
| 400 | [Error](#schema-error) | - `search_query_invalid`: `q` is empty, whitespace-only or longer than 300 characters. - `search_scope_invalid`: `scope` is not `book` or `earlier`. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="getstorymap"></a>
### `GET /api/books/{book_id}/story-map`

**Get the story map graph** · operation `getStoryMap` · cost `none`

Versioned typed nodes and edges, chapters/scenes/passages, character IDs, source references and counts, and notes about interpretation. Node identities include the owning book, and every edge ends at a node. Verified source anchors refer to retained source artifacts; unavailable or unverified anchors remain null. Scene characters are attributed speakers, not verified physical presence; mentions and profile evidence remain separate references; scene boundaries may be local drafts. A dialogue passage whose speaker ID is no longer in the cast has no `attributed_speaker` edge and adds no scene character.

The response is unpaginated and grows with the book (every passage is a node). No provider is contacted. This GET creates and changes no domain records: no artifacts, decisions, resource-ledger rows, jobs or book changes.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID, from the library or the import response. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [StoryMap](#schema-storymap) | Success. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

## Narration

Enhanced (cast) narration takes and their audio.

<a id="getpassageaudio"></a>
### `GET /api/audio/{book_id}/{segment_id}`

**Download a passage's current enhanced take** · operation `getPassageAudio` · cost `none`

The WAV of the passage's current Studio (enhanced) take, only while it is valid: its recipe fingerprint
must still match the passage, speaker and scene with the current resolved cast, and the file must exist.
The book document gives valid takes a URL of this form with a `?v=` cache-busting query, which the
server ignores. Local read.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `segment_id` | path | string | yes | Passage (segment) ID. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | `audio/wav` | Mono 24 kHz 16-bit PCM WAV. |
| 206 | `audio/wav` | Partial content for a `Range` request (served from a file; see `Content-Range`). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `passage_not_found`: The book has no passage with this ID. - `audio_not_found`: The passage has no take, or its take is stale or its file is missing. |
| 416 |  | The requested `Range` cannot be satisfied (empty body; see `Content-Range`). |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="getretainedaudioasset"></a>
### `GET /api/books/{book_id}/audio-assets/{asset_id}`

**Download a retained enhanced audio asset** · operation `getRetainedAudioAsset` · cost `none`

Bytes of any retained enhanced audio asset of the book, current or historical, including cast
performance takes (their `url` points here). The asset is identified by its content hash (older takes
by recipe fingerprint). The file is served as stored; its integrity is not re-verified. Local read.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `asset_id` | path | string | yes | Asset ID: 32-128 lower-case hex characters. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | `audio/wav` | Mono 24 kHz 16-bit PCM WAV. |
| 206 | `audio/wav` | Partial content for a `Range` request (served from a file; see `Content-Range`). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `audio_not_found`: Malformed ID (32-128 lower-case hex) or no such file. |
| 416 |  | The requested `Range` cannot be satisfied (empty body; see `Content-Range`). |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="startenhancedrender"></a>
### `POST /api/books/{book_id}/render`

**Queue enhanced (cast) narration takes** · operation `startEnhancedRender` · cost `may_charge`

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
Refused with 503 while the server is shutting down.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |

Request body (`application/json`): [RenderRequest](#schema-renderrequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [Job](#schema-job) | The queued `render` job. |
| 400 | [Error](#schema-error) | - `provider_unsupported`: `provider` is not `system`, `gemini` or `breeze`. - `gemini_key_missing`: Gemini narration with no Gemini API key configured. - `device_narration_unavailable`: Device narration on a server without macOS `say` and `ffmpeg`. - `breeze_url_missing`: Breeze narration with no Breeze server URL configured. - `unknown_passage`: The body names a passage (`segment_id`) that is not in this book. - `unknown_scene`: The body names a scene (`scene_id`) that is not in this book. - `no_passages_selected`: The named passage is not in the named scene. - `cast_voice_unusable`: A selected speaker has no usable Breeze or Gemini voice; the detail names up to five speakers. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 409 | [Error](#schema-error) | - `book_archived`: The book is archived: restore it first. - `job_active`: A job is queued or running for this book. - `series_run_active`: An active series run reserves this book. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |
| 503 | [Error](#schema-error) | - `shutting_down`: The server is shutting down, or its narration worker refused the job (that job record is kept and marked `failed`). Nothing was sent to a provider. |

## Listening

Simple single-narrator listening: passage and chapter preparation and audio.

<a id="listentopassage"></a>
### `POST /api/books/{book_id}/listen`

**Get or queue simple narration for one passage** · operation `listenToPassage` · cost `may_charge`

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
   this book and retains a new source-bound take whose `reuse` points at the original take. A cache hit
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

This endpoint prepares only the requested passage; there is no streaming endpoint. Breeze and device
voices always use it (Gemini chapters use `POST /listen/chapter`). The browser coordinates device
warmup (about 10 listening seconds, at most three passages), lookahead (about 45 seconds, at most 12
future passages, staying in the chapter) and the explicit "Prepare rest of chapter" action by calling
this endpoint serially; those queues do not resume after a browser or server restart.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |

Request body (`application/json`): [ListenRequest](#schema-listenrequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [ListenCached](#schema-listencached) \| [ListenQueued](#schema-listenqueued) | `cached: true` with `audio`, or `cached: false` with a new or joined `listen` job. |
| 400 | [Error](#schema-error) | - `unknown_passage`: The body names a passage (`segment_id`) that is not in this book. - `passage_source_mismatch`: The passage text no longer matches its source coordinates. - `narrator_voice_invalid`: The narrator voice cannot be used: a `library:` voice for device narration, a deleted or wrong-provider library voice, no default Breeze voice, a Breeze voice not in the last voice check or not usable, or a custom voice with a model that needs a prebuilt voice (Gemini 3.1). - `model_unsupported`: The model does not match the provider: device narration uses `macos-say`, Breeze uses `breeze-tts-2`, and Gemini needs a supported TTS model. - `gemini_key_missing`: Only when synthesis is needed: Gemini narration with no Gemini API key configured. - `device_narration_unavailable`: Only when synthesis is needed: Device narration on a server without macOS `say` and `ffmpeg`. - `breeze_url_missing`: Only when synthesis is needed: Breeze narration with no Breeze server URL configured. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 409 | [Error](#schema-error) | - `book_archived`: The book is archived: restore it first. - `job_active`: Only when synthesis is needed: A job is queued or running for this book. - `series_run_active`: Only when synthesis is needed: An active series run reserves this book. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |
| 503 | [Error](#schema-error) | - `shutting_down`: The server is shutting down, or its narration worker refused the job (that job record is kept and marked `failed`). Nothing was sent to a provider. |

<a id="getlisteningaudio"></a>
### `GET /api/books/{book_id}/listen/audio/{asset_id}`

**Download a simple-listening WAV** · operation `getListeningAudio` · cost `none`

A retained simple-listening WAV (single-passage take or shared chunk), scoped to the book that retained
it. The file must be recorded for this book and exist; it is served without re-verifying its hash.
For a chunk clip, play from `clip_start` to `clip_end`. Local read.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `asset_id` | path | string | yes | Asset ID: 64 lower-case hex characters (SHA-256 of the WAV). |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | `audio/wav` | Mono 24 kHz 16-bit PCM WAV. |
| 206 | `audio/wav` | Partial content for a `Range` request (served from a file; see `Content-Range`). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `audio_not_found`: Malformed ID (64 lower-case hex), not retained for this book, or file missing. |
| 416 |  | The requested `Range` cannot be satisfied (empty body; see `Content-Range`). |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="startchapterlistening"></a>
### `POST /api/books/{book_id}/listen/chapter`

**Start or join Gemini chapter preparation** · operation `startChapterListening` · cost `may_charge`

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
`realtime_factor`, `epoch`, `status` `requesting`/`done`/`rate_limited`/`truncated`/`failed`,
`started_at`/`finished_at`, `error`, and for finished chunks `chunk_id`, `duration`, `latency`, `flags`,
`matched`/`boundaries`), `projection` (remaining planned chunks in request order), `calibration`,
`speech_limits`, `quota` (`requests_today`, `rpd`, `resets_at`, `scope: "this library"`), `waiting_seconds` and
the selected `chunking`. Terminal statuses include `quota_limited` with `resume_after` (for example when
other traffic uses up the daily count while the job runs). Finished chunks are kept on every outcome;
start the chapter again to resume.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |

Request body (`application/json`): [ChapterListenRequest](#schema-chapterlistenrequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [ChapterListenStarted](#schema-chapterlistenstarted) | Success. |
| 400 | [Error](#schema-error) | - `unknown_passage`: The body names a passage (`segment_id`) that is not in this book. - `narrator_voice_invalid`: The narrator voice cannot be used: a `library:` voice for device narration, a deleted or wrong-provider library voice, no default Breeze voice, a Breeze voice not in the last voice check or not usable, or a custom voice with a model that needs a prebuilt voice (Gemini 3.1). - `model_unsupported`: The model does not match the provider: device narration uses `macos-say`, Breeze uses `breeze-tts-2`, and Gemini needs a supported TTS model. - `gemini_key_missing`: Only when starting: no Gemini API key is configured. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 409 | [Error](#schema-error) | - `book_archived`: The book is archived: restore it first. - `job_active`: A job is queued or running for this book. - `series_run_active`: An active series run reserves this book. - `performance_active`: A saved performance's chapter job is preparing this book. - `chapter_listen_active`: A chapter job for another chapter or narrator is active. - `chapter_job_closing`: The matching chapter job is finishing; retry shortly. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |
| 429 | [Error](#schema-error) | - `daily_quota_reached`: Only when starting: a daily-quota block holds for the model, or this library's requests today have reached the configured requests per day. Nothing was queued or sent; `Retry-After` gives the seconds to wait. |
| 503 | [Error](#schema-error) | - `shutting_down`: The server is shutting down, or its narration worker refused the job (that job record is kept and marked `failed`). Nothing was sent to a provider. |

<a id="previewchapterlistening"></a>
### `POST /api/books/{book_id}/listen/chapter/preview`

**Plan chapter listening from a passage** · operation `previewChapterListening` · cost `none`

Local plan for a chapter job starting at the passage: the chunks that would be requested, requests
needed, expected audio, ready passages and seconds, effective chunk options, calibration, configured
limits and this library's daily request count for the model. Takes the same body as
`POST /listen/chapter` (`intent: "queue"` without explicit ramp steps plans full-size chunks only).
Creates the deterministic narrator session row only; no job, no provider request, no key required, and
it works while the book is busy. When `quota.requests_today` has reached `limits.rpd`, starting the
chapter is refused with 429.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |

Request body (`application/json`): [ChapterListenRequest](#schema-chapterlistenrequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [ChapterListenPlan](#schema-chapterlistenplan) | Success. |
| 400 | [Error](#schema-error) | - `unknown_passage`: The body names a passage (`segment_id`) that is not in this book. - `narrator_voice_invalid`: The narrator voice cannot be used: a `library:` voice for device narration, a deleted or wrong-provider library voice, no default Breeze voice, a Breeze voice not in the last voice check or not usable, or a custom voice with a model that needs a prebuilt voice (Gemini 3.1). - `model_unsupported`: The model does not match the provider: device narration uses `macos-say`, Breeze uses `breeze-tts-2`, and Gemini needs a supported TTS model. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 409 | [Error](#schema-error) | - `book_archived`: The book is archived: restore it first. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="listlisteningtakes"></a>
### `GET /api/books/{book_id}/listen/takes`

**List a listening session's playable audio** · operation `listListeningTakes` · cost `none`

Saved simple audio for the session, one entry per passage in book order: a chunk clip when one
applies, otherwise the newest single-passage take whose source recipe still matches the passage.
Passages whose source no longer matches, and takes whose file is missing, are omitted. To stay fast on
long books this does not re-read WAV samples; a chunk whose file is known to be damaged is excluded.
No generation and no stored change. Works for archived books.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `session_id` | query | string | yes | Listening session ID from a `session` object. |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [ListeningTakes](#schema-listeningtakes) | Success. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `listening_session_not_found`: The book has no listening session with this `session_id`. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

## Performances

Saved performances: named selections of chapters and narration settings over retained audio.

<a id="listperformances"></a>
### `GET /api/books/{book_id}/performances`

**List saved performances** · operation `listPerformances` · cost `none`

Performances of the book, newest first, each with its latest job summary and readiness. Archived records are included only with `archived=true`. Local read with no stored change; readiness uses file existence, not WAV validation.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `archived` | query | boolean |  | Include archived performances (default false). (default `false`) |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PerformanceList](#schema-performancelist) | Success. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="createperformance"></a>
### `POST /api/books/{book_id}/performances`

**Create a performance and start preparing it** · operation `createPerformance` · cost `may_charge`

Validate, record and start a performance. Simple performances pin a listening session (takes made
earlier by live listening with the same narrator count as ready); cast performances snapshot the
resolved cast now. Returns `{performance, job}`; `job` is null when every passage is already ready.
The record is saved before the job starts. When the preview would report `problems`, the request is
refused with 400: the code is the first problem's, and the detail joins every problem's sentence.

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
chunks kept. Cast performances render passage by passage (resource stage `narration`, `cached: true`
for reuse of another performance's or a Studio take with the identical recipe) and never change the
Studio's selected takes. Gemini cast requests share the per-minute rate limiter with other Gemini speech, and stop as
`quota_limited` at the provider's daily quota or
at this library's configured requests per day, and retry a per-minute 429 at most five consecutive
times. An uncertain request (timeout, dropped connection) is never resent; the job fails with completed
audio kept, and the failure names the passage. There is no dollar allowance for performances. Up to
three performances of different books run at once; one job per book still applies, counting every active
job for the book (including child jobs beyond the 100-job list bound).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |

Request body (`application/json`): [PerformanceRequest](#schema-performancerequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PerformanceStarted](#schema-performancestarted) | Success. |
| 400 | [Error](#schema-error) | - `unknown_chapter`: A chapter ID in `chapter_ids` is not in this book. - `model_unsupported`: The Gemini model is not supported, or the model does not match the device or Breeze fixed model. - `gemini_key_missing`: Gemini narration with no Gemini API key configured. - `device_narration_unavailable`: Device narration on a server without macOS `say` and `ffmpeg`. - `breeze_url_missing`: Breeze narration with no Breeze server URL configured. - `narrator_voice_invalid`: The simple narrator voice cannot be used (see `previewPerformance` problems). - `narrator_voice_missing`: A cast performance whose narrator has no usable voice for the provider. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 409 | [Error](#schema-error) | - `book_archived`: The book is archived: restore it first. - `job_active`: A job is queued or running for this book. - `series_run_active`: An active series run reserves this book. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |
| 503 | [Error](#schema-error) | - `shutting_down`: The server is shutting down, or its narration worker refused the job (that job record is kept and marked `failed`). Nothing was sent to a provider. |

<a id="previewperformance"></a>
### `POST /api/books/{book_id}/performances/preview`

**Estimate a performance without starting it** · operation `previewPerformance` · cost `none`

Local plan: readiness, passages to generate, request estimate, expected audio, blocking `problems` and
advisory `notes`, and for Gemini this library's daily request count. No provider calls and no job;
creating the deterministic listening session row is allowed. Narrator and provider conditions the user
can fix (missing key, unusable voice, narrator without a voice for a cast) are returned in `problems`
rather than as errors; `createPerformance` refuses them with the codes it lists.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |

Request body (`application/json`): [PerformanceRequest](#schema-performancerequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PerformancePlan](#schema-performanceplan) | Success. |
| 400 | [Error](#schema-error) | - `unknown_chapter`: A chapter ID in `chapter_ids` is not in this book. - `model_unsupported`: The Gemini model is not supported, or the model does not match the device or Breeze fixed model. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 409 | [Error](#schema-error) | - `book_archived`: The book is archived: restore it first. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="getperformance"></a>
### `GET /api/books/{book_id}/performances/{performance_id}`

**Get a performance** · operation `getPerformance` · cost `none`

The performance with its latest job summary and readiness. Local read with no stored change.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `performance_id` | path | string | yes | Performance ID (`pf_…`). |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PerformanceEnvelope](#schema-performanceenvelope) | Success. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `performance_not_found`: The book has no performance with this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="updateperformance"></a>
### `PATCH /api/books/{book_id}/performances/{performance_id}`

**Rename or archive a performance** · operation `updatePerformance` · cost `none`

Change label fields only: `name` (trimmed) and `archived`. Never deletes or changes audio, and is allowed while jobs run. Omitted or null fields are unchanged; with no fields the record is returned as is (and `updated_at` is not touched). An empty `name` string or one over 200 characters fails request validation (422).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `performance_id` | path | string | yes | Performance ID (`pf_…`). |

Request body (`application/json`): [PerformanceEdit](#schema-performanceedit)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PerformanceEnvelope](#schema-performanceenvelope) | Success. |
| 400 | [Error](#schema-error) | - `performance_name_required`: `name` is only whitespace. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `performance_not_found`: The book has no performance with this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="getperformanceaudio"></a>
### `GET /api/books/{book_id}/performances/{performance_id}/audio`

**List a performance's playable audio** · operation `getPerformanceAudio` · cost `none`

Audio for each selected passage that is ready against its current source, as JSON (not bytes); play
each `url`. Simple performances return the `/listen/takes` objects (single-passage takes or chunk clips
with `clip_start`/`clip_end`); cast performances return the newest retained take per passage
(`PerformanceCastAudio`, with `speaker_id`), served from `/audio-assets/`. Local read with no stored
change; file existence is checked but WAVs are not re-validated.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `performance_id` | path | string | yes | Performance ID (`pf_…`). |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PerformanceAudioMap](#schema-performanceaudiomap) | Success. |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `performance_not_found`: The book has no performance with this ID. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="prepareperformance"></a>
### `POST /api/books/{book_id}/performances/{performance_id}/prepare`

**Resume preparing a performance** · operation `preparePerformance` · cost `may_charge`

Generate what is missing with the same pinned narrator session or cast snapshot (settings changed since
creation do not apply, except that current credentials, limits and chunk options are used). Cast
resume validates retained WAVs, so a damaged file is narrated again; a regenerated file whose bytes
match the damaged one's content address is refused rather than overwritten (possible with
deterministic device voices). Chapters removed from the book are skipped. Returns `{performance, job}`
with `job` null when nothing is missing. Blocking problems are refused with 400 as for
`createPerformance`.

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
chunks kept. Cast performances render passage by passage (resource stage `narration`, `cached: true`
for reuse of another performance's or a Studio take with the identical recipe) and never change the
Studio's selected takes. Gemini cast requests share the per-minute rate limiter with other Gemini speech, and stop as
`quota_limited` at the provider's daily quota or
at this library's configured requests per day, and retry a per-minute 429 at most five consecutive
times. An uncertain request (timeout, dropped connection) is never resent; the job fails with completed
audio kept, and the failure names the passage. There is no dollar allowance for performances. Up to
three performances of different books run at once; one job per book still applies, counting every active
job for the book (including child jobs beyond the 100-job list bound).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `performance_id` | path | string | yes | Performance ID (`pf_…`). |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [PerformanceStarted](#schema-performancestarted) | Success. |
| 400 | [Error](#schema-error) | - `gemini_key_missing`: Gemini narration with no Gemini API key configured. - `device_narration_unavailable`: Device narration on a server without macOS `say` and `ffmpeg`. - `breeze_url_missing`: Breeze narration with no Breeze server URL configured. - `narrator_voice_missing`: A cast performance whose narrator has no usable voice for the provider. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `performance_not_found`: The book has no performance with this ID. |
| 409 | [Error](#schema-error) | - `book_archived`: The book is archived: restore it first. - `job_active`: A job is queued or running for this book. - `series_run_active`: An active series run reserves this book. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |
| 503 | [Error](#schema-error) | - `shutting_down`: The server is shutting down, or its narration worker refused the job (that job record is kept and marked `failed`). Nothing was sent to a provider. |

## Voice previews

Short explicit voice auditions against a book passage.

<a id="startvoicepreview"></a>
### `POST /api/books/{book_id}/voice-preview`

**Get or queue a short voice audition** · operation `startVoicePreview` · cost `may_charge`

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
in the library and in a full library backup, but are not included in the analysis or audiobook ZIP.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |

Request body (`application/json`): [VoicePreviewRequest](#schema-voicepreviewrequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [VoicePreviewCached](#schema-voicepreviewcached) \| [VoicePreviewQueued](#schema-voicepreviewqueued) | `cached: true` with `audio`, or `cached: false` with a new or joined `voice_preview` job. |
| 400 | [Error](#schema-error) | - `unknown_passage`: The body names a passage (`segment_id`) that is not in this book. - `unknown_character`: The body names a character (`character_id`) that is not in this book. - `passage_source_mismatch`: The passage text no longer matches its source coordinates. - `narrator_voice_invalid`: The narrator voice cannot be used: a `library:` voice for device narration, a deleted or wrong-provider library voice, no default Breeze voice, a Breeze voice not in the last voice check or not usable, or a custom voice with a model that needs a prebuilt voice (Gemini 3.1). - `model_unsupported`: The model does not match the provider: device narration uses `macos-say`, Breeze uses `breeze-tts-2`, and Gemini needs a supported TTS model. - `direction_requires_character`: `direction` without `character_id`. - `segment_direction_requires_passage`: `segment_direction` without both a passage and a character. - `pronunciation_invalid`: The `pronunciation` entry is not valid. - `gemini_key_missing`: Only when synthesis is needed: Gemini narration with no Gemini API key configured. - `device_narration_unavailable`: Only when synthesis is needed: Device narration on a server without macOS `say` and `ffmpeg`. - `breeze_url_missing`: Only when synthesis is needed: Breeze narration with no Breeze server URL configured. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. |
| 409 | [Error](#schema-error) | - `book_archived`: The book is archived: restore it first. - `job_active`: Only when synthesis is needed: A job is queued or running for this book. - `series_run_active`: Only when synthesis is needed: An active series run reserves this book. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |
| 503 | [Error](#schema-error) | - `shutting_down`: The server is shutting down, or its narration worker refused the job (that job record is kept and marked `failed`). Nothing was sent to a provider. |

<a id="getvoicepreviewaudio"></a>
### `GET /api/books/{book_id}/voice-preview/audio/{asset_id}`

**Download an audition WAV** · operation `getVoicePreviewAudio` · cost `none`

A retained audition WAV, scoped to its owning book. Every request verifies the content hash and WAV format before serving. Local read.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `book_id` | path | string | yes | Book ID. |
| `asset_id` | path | string | yes | Asset ID: 64 lower-case hex characters (SHA-256 of the WAV). |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | `audio/wav` | Mono 24 kHz 16-bit PCM WAV. |
| 206 | `audio/wav` | Partial content for a `Range` request (served from a file; see `Content-Range`). |
| 404 | [Error](#schema-error) | - `book_not_found`: No book has this ID. - `audio_not_found`: Malformed ID, not retained for this book, or the file is missing or damaged. |
| 416 |  | The requested `Range` cannot be satisfied (empty body; see `Content-Range`). |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

## Voices

The library-wide voice library: voices, versions, defaults, drafts and Breeze clones.

<a id="getvoicelibrary"></a>
### `GET /api/voices`

**Get the voice library** · operation `getVoiceLibrary` · cost `none`

Voices belong to the whole library, not to a book. Breeze requests are free but run on the owner's self-hosted GPU; every Gemini voice create is billed. Provider error text is never echoed: Gemini errors carry only the HTTP status and a fixed hint, Breeze errors keep only the server's error code.

Local only and read-only: reads SQLite and the saved Breeze and Gemini checks; never contacts a provider and never writes. Returns live library voices, the defaults, open drafts, provider state and built-in voices. Each version's `server_state` compares it with the last saved Breeze check (for this URL) and the last Gemini listing (for this key). When the last Gemini refresh failed, `providers.gemini.state` is `error` and this still returns 200.

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [VoiceLibraryOverview](#schema-voicelibraryoverview) | Success. |

<a id="clonebreezevoice"></a>
### `POST /api/voices/breeze/clone`

**Clone a Breeze voice from a recording** · operation `cloneBreezeVoice` · cost `network`

Multipart form upload. Creates a cloned voice on the Breeze server from a recording and its exact transcript (ID `bardic-` plus 8 hex digits, labelled with the new library voice ID), pins its revision, retains the server's reference clip as the audition (best effort; the voice is still created without it), and creates a library voice with `origin: "cloned"`. With both `book_id` and `character_id`, the voice records that character as its `source` and is assigned to it like a cast edit; a failed assignment is reported in `assignment_error`, never rolled back. With only one of the two, both are ignored. The Breeze server accepts recordings of 1–30 seconds (5–15 seconds of clean speech works best).

**Validated before upload.** The form, the book and the character are checked before the server voice is created. If the library record still cannot be written afterwards, or the server keeps the upload but its read-back fails, the server voice is deleted again, best effort, and the error detail says whether that worked.

Request body (`multipart/form-data`): [CloneBreezeVoiceForm](#schema-clonebreezevoiceform)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [BreezeVoiceCloned](#schema-breezevoicecloned) | Success. |
| 400 | [Error](#schema-error) | - `consent_required`: `consent` is not exactly `true`. - `voice_name_invalid`: `name` is blank. - `reference_text_missing`: `reference_text` is blank. - `recording_empty`: The recording is empty. - `unknown_book`: No book has the given `book_id`. - `unknown_character`: The book has no character with the given `character_id`. - `breeze_url_missing`: No Breeze server URL is configured. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 413 | [Error](#schema-error) | - `recording_too_large`: The recording is larger than 20 MB. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |
| 502 | [Error](#schema-error) | - `provider_error`: The Breeze server refused or failed the clone (for example unreadable, silent or wrong-length audio, or a bad API key), or was unreachable. |

<a id="setdefaultlibraryvoice"></a>
### `POST /api/voices/defaults`

**Set the Breeze default voice** · operation `setDefaultLibraryVoice` · cost `none`

Makes a live Breeze library voice the Breeze default. Characters without a Breeze choice follow it, and simple listening uses it for Breeze when no voice is chosen. Choosing the current default again is a no-op. A change is recorded in the voice history. Local only.

This re-voices every character that follows the previous or the new default: selected takes made with the previous voice become out of date but stay in history, and rendering again after switching back reuses the archived WAV without a provider request. Refused with 409 while a `render`, `listen`, `listen_chapter` or `voice_preview` job is queued or running for a book with a character following the voice. Both the previous and the new default's followers are checked.

Request body (`application/json`): [DefaultVoice](#schema-defaultvoice)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [VoiceLibraryDefaultsResult](#schema-voicelibrarydefaultsresult) | Success. |
| 400 | [Error](#schema-error) | - `unknown_voice`: No library voice has the given `voice_id`. - `default_voice_invalid`: The voice is deleted or is not a Breeze voice. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 409 | [Error](#schema-error) | - `narration_active`: A `render`, `listen`, `listen_chapter` or `voice_preview` job is queued or running for a book whose characters follow an affected voice. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="createvoicedraft"></a>
### `POST /api/voices/drafts`

**Start a voice design draft** · operation `createVoiceDraft` · cost `none`

Creates an open draft. Nothing is generated and no provider is contacted.

Fill order for `name`, `description` and `sample_text` (an explicit value, including an empty string, always wins):
1. With both `book_id` and `character_id`, the character fills them: name, description (the character profile description plus its direction, joined by a space, cut to 1,000 characters) and sample text (the character's first attributed passage as an exact prefix of at most 400 code points, cut at a sentence or word boundary, else the demo text). The draft's `context` records the character. With only one of the two IDs, both are ignored.
2. `base_voice_id` fills what is still unset from that voice: its name, its current version's recipe description (else the voice description) and recipe sample text. Because a character fills all three fields, a base voice fills nothing when a character is also given.
3. A Breeze draft with no sample text uses the built-in demo text; a Gemini draft keeps an empty sample text (Gemini does not use it).

Request body (`application/json`): [DraftCreate](#schema-draftcreate)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [VoiceDraft](#schema-voicedraft) | Success. |
| 400 | [Error](#schema-error) | - `unknown_book`: No book has the given `book_id`. - `unknown_character`: The book has no character with the given `character_id`. - `unknown_voice`: No library voice has the given `base_voice_id`. - `base_voice_unusable`: The base voice is deleted or belongs to the other provider. - `voice_name_invalid`: A filled-in name is longer than 100 characters. - `description_too_long`: A filled-in description is longer than 1,000 characters. - `sample_text_too_long`: A filled-in sample text is longer than 1,000 characters. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="updatevoicedraft"></a>
### `PATCH /api/voices/drafts/{draft_id}`

**Edit a voice draft** · operation `updateVoiceDraft` · cost `none`

Changes the working name, description and/or sample text of an open draft; an omitted or null field is unchanged, and values are trimmed. Existing candidates keep the text they were generated from. Allowed while a generation is running (the running generation uses the text it started with). Local only.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `draft_id` | path | string | yes | Draft ID (`vd_…`). |

Request body (`application/json`): [DraftEdit](#schema-draftedit)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [VoiceDraft](#schema-voicedraft) | Success. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `voice_draft_not_found`: No voice draft has this ID (including a malformed ID). |
| 409 | [Error](#schema-error) | - `draft_finished`: The draft is already saved or abandoned. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="abandonvoicedraft"></a>
### `POST /api/voices/drafts/{draft_id}/abandon`

**Abandon a voice draft** · operation `abandonVoiceDraft` · cost `network`

Deletes every undiscarded Gemini candidate's stored voice from the Google project, then marks every candidate discarded and the draft `abandoned`. Breeze previews are only marked. If any deletion fails, the draft stays open (some voices may already be deleted; retrying is safe). Returns the abandoned draft.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `draft_id` | path | string | yes | Draft ID (`vd_…`). |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [VoiceDraft](#schema-voicedraft) | Success. |
| 400 | [Error](#schema-error) | - `gemini_key_missing`: No Gemini API key is configured. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `voice_draft_not_found`: No voice draft has this ID (including a malformed ID). |
| 409 | [Error](#schema-error) | - `draft_finished`: The draft is already saved or abandoned. - `draft_busy`: Another generate, discard, abandon or save request is working on the draft. - `candidate_other_project`: A Gemini candidate that must be deleted or saved was made with a different Google API key than the current one. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |
| 502 | [Error](#schema-error) | - `provider_error`: Some stored Gemini candidates could not be deleted (the detail names the first failure); the draft stays open. |

<a id="getvoicedraftcandidateaudio"></a>
### `GET /api/voices/drafts/{draft_id}/candidates/{candidate_id}/audio`

**Play a draft candidate's audio** · operation `getVoiceDraftCandidateAudio` · cost `none`

Bardic's retained copy of a candidate's audio (24 kHz mono WAV). Available for discarded candidates and finished drafts too. Local only.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `draft_id` | path | string | yes | Draft ID (`vd_…`). |
| `candidate_id` | path | string | yes | Candidate ID within the draft (`c1`, `c2`, …). |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | `audio/wav` | Success. |
| 206 | `audio/wav` | Partial content for a `Range` request (served from a file; see `Content-Range`). |
| 404 | [Error](#schema-error) | - `voice_draft_not_found`: No voice draft has this ID (including a malformed ID). - `candidate_not_found`: The draft has no candidate with this ID. - `audio_not_found`: The candidate has no retained audio. |
| 416 |  | The requested `Range` cannot be satisfied (empty body; see `Content-Range`). |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="discardvoicedraftcandidate"></a>
### `POST /api/voices/drafts/{draft_id}/candidates/{candidate_id}/discard`

**Discard a draft candidate** · operation `discardVoiceDraftCandidate` · cost `network`

Marks the candidate discarded so it cannot be saved. A Gemini candidate's stored voice is first deleted from the Google project (already gone counts as deleted). A Breeze preview is only marked; it expires on the server by itself. Discarding an already-discarded candidate succeeds without contacting a provider. Returns the updated draft.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `draft_id` | path | string | yes | Draft ID (`vd_…`). |
| `candidate_id` | path | string | yes | Candidate ID within the draft (`c1`, `c2`, …). |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [VoiceDraft](#schema-voicedraft) | Success. |
| 400 | [Error](#schema-error) | - `gemini_key_missing`: No Gemini API key is configured. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `voice_draft_not_found`: No voice draft has this ID (including a malformed ID). - `candidate_not_found`: The draft has no candidate with this ID. |
| 409 | [Error](#schema-error) | - `draft_finished`: The draft is already saved or abandoned. - `draft_busy`: Another generate, discard, abandon or save request is working on the draft. - `candidate_other_project`: A Gemini candidate that must be deleted or saved was made with a different Google API key than the current one. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |
| 502 | [Error](#schema-error) | - `provider_error`: The Gemini delete failed; the candidate stays undiscarded. |

<a id="generatevoicedraftcandidates"></a>
### `POST /api/voices/drafts/{draft_id}/generate`

**Generate draft candidates** · operation `generateVoiceDraftCandidates` · cost `may_charge`

Synchronous: the request returns when generation finishes and returns the updated draft.

**Breeze** (free, self-hosted GPU): generates `count` (1–3, default 2) previews of the draft description speaking the draft sample text, and retains each preview's audio. Takes roughly the audio length times `count`. `language_code`, `gender` and `confirm_cost` are ignored. With `book_id`, the request is recorded in that book's resource ledger (stage `voice_design`, cost basis `self_hosted`, $0).

**Gemini** (billed): each call creates **one** stored, billed prompted voice in the Google project (`count` is ignored), named after the draft name (or "Bardic voice"), using the selected speech model when it is a design model, else the first design model. `confirm_cost: true` and `book_id` are required; the request is recorded in that book's resource ledger with an unknown cost (never recorded as $0). A create that times out or returns an unusable response is never resent and returns 502 `provider_outcome_unknown`: the voice may exist and be billed, so refresh Gemini voices (`POST /api/voices/gemini/refresh`) and look for it in `project_voices`. If the returned sample cannot be stored, the candidate is kept without audio.

Validation (400s) happens before the draft is claimed. While the request runs, the draft is `busy` and other generate, discard, abandon and save requests on it get 409.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `draft_id` | path | string | yes | Draft ID (`vd_…`). |

Request body (`application/json`): [GenerateRequest](#schema-generaterequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [VoiceDraft](#schema-voicedraft) | Success. |
| 400 | [Error](#schema-error) | - `description_too_short`: The draft description has fewer than 3 characters. - `cost_not_confirmed`: Gemini: `confirm_cost` is not true. - `book_id_required`: Gemini: no `book_id` was given. - `unknown_book`: No book has the given `book_id`. - `voice_design_invalid`: Gemini: the draft name, language tag or gender is not accepted. - `sample_text_missing`: Breeze: the draft has no sample text. - `gemini_key_missing`: No Gemini API key is configured. - `breeze_url_missing`: No Breeze server URL is configured. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `voice_draft_not_found`: No voice draft has this ID (including a malformed ID). |
| 409 | [Error](#schema-error) | - `draft_finished`: The draft is already saved or abandoned. Also when a concurrent request finished it during generation. - `draft_busy`: Another generate, discard, abandon or save request is working on the draft. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |
| 502 | [Error](#schema-error) | - `provider_error`: The provider refused or failed the request, or was unreachable. The detail is Bardic's own sentence; provider text is not echoed. Breeze also returns it when the preview audio cannot be converted locally. - `provider_outcome_unknown`: Gemini: a billed create timed out or returned an unusable response, so the voice may exist and be billed (see description). |

<a id="savevoicedraft"></a>
### `POST /api/voices/drafts/{draft_id}/save`

**Save a draft candidate** · operation `saveVoiceDraft` · cost `network`

Saves one undiscarded candidate as a new library voice (`mode: "new"`) or as a new current version of the draft's base voice (`mode: "version"`), and marks the draft `saved`.

**Breeze**: uploads the exact auditioned clip as a new cloned server voice (ID `bardic-` plus 8 hex digits, labelled with the library voice ID and version), so saving works after the preview expired. The server re-encodes the clip; the version pins the revision of what the server keeps, and the retained clip becomes the version's audition. **Gemini**: the candidate's stored voice becomes the version (no new billed request); unchosen undiscarded Gemini candidates made with the current key are deleted from the project, and failures are reported in `cleanup_error`.

`make_default: true` (Breeze only) then makes the voice the Breeze default. `assign` then assigns the voice to that character, exactly like a cast edit; a failed assignment is reported in `assignment_error` and never rolled back. `mode: "version"` re-voices every follower of the base voice, and `make_default` re-voices characters on Default, so both are refused with 409 while narration runs for affected books.

**Validated before upload.** Every check below runs before a Breeze server voice is uploaded. If the library record still cannot be written after the upload (for example the base voice was deleted in the meantime), the uploaded server voice is deleted again, best effort, and the error detail says whether that worked. The same happens when the server keeps the upload but its read-back fails. The draft stays open, so the save can be retried.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `draft_id` | path | string | yes | Draft ID (`vd_…`). |

Request body (`application/json`): [SaveRequest](#schema-saverequest)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [VoiceDraftSaved](#schema-voicedraftsaved) | Success. |
| 400 | [Error](#schema-error) | - `unknown_candidate`: The draft has no candidate with the given `candidate_id`. - `candidate_discarded`: The candidate was discarded. - `voice_name_invalid`: `name` is only whitespace. - `draft_has_no_base_voice`: `mode: "version"` for a draft not started from a base voice. - `default_breeze_only`: `make_default` for a Gemini draft. - `breeze_url_missing`: No Breeze server URL is configured. - `gemini_key_missing`: No Gemini API key is configured. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `voice_draft_not_found`: No voice draft has this ID (including a malformed ID). |
| 409 | [Error](#schema-error) | - `draft_finished`: The draft is already saved or abandoned. - `draft_busy`: Another generate, discard, abandon or save request is working on the draft. - `candidate_other_project`: A Gemini candidate that must be deleted or saved was made with a different Google API key than the current one. - `narration_active`: A `render`, `listen`, `listen_chapter` or `voice_preview` job is queued or running for a book whose characters follow an affected voice. - `base_voice_deleted`: `mode: "version"` and the draft's base voice is deleted, including when it was deleted during the save. - `candidate_audio_missing`: Breeze: the candidate's retained audio file is missing, so it cannot be uploaded. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |
| 502 | [Error](#schema-error) | - `provider_error`: The Breeze upload or its read-back failed. |

<a id="refreshgeminivoices"></a>
### `POST /api/voices/gemini/refresh`

**List the Google project's stored voices** · operation `refreshGeminiVoices` · cost `network`

Lists the Google project's stored `prompted` and `replicated` voices (metadata only; no generation, up to 5 pages of 100) and saves the listing with a hash of the key, so a listing made with another key is ignored. Returns `providers.gemini` of the library overview.

When the listing fails (an HTTP error, no connection, or an unreadable response), the failure is saved first, so that `GET /api/voices` reports `providers.gemini.state: "error"` with the message, and then this returns 502 `provider_error`.

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [VoiceLibraryGeminiStatus](#schema-voicelibrarygeministatus) | Success. |
| 400 | [Error](#schema-error) | - `gemini_key_missing`: No Gemini API key is configured. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 409 | [Error](#schema-error) | - `gemini_key_changed`: The Gemini API key changed during the check; nothing is saved. Refresh again. |
| 502 | [Error](#schema-error) | - `provider_error`: The provider refused or failed the request, or was unreachable. The detail is Bardic's own sentence; provider text is not echoed. |

<a id="updatelibraryvoice"></a>
### `PATCH /api/voices/{voice_id}`

**Rename or redescribe a voice** · operation `updateLibraryVoice` · cost `network`

Changes the name and/or description; an omitted or null field is unchanged. Values are trimmed. Versions and pinned revisions do not change. For a Breeze voice, when a Breeze URL is configured, each distinct server voice behind its versions is renamed on the server best effort (the untrimmed values are sent; failures are ignored because the library record is authoritative). Gemini voices change locally only.

The returned `LibraryVoice` compares each version with the same saved provider checks as `GET /api/voices` (read locally; this does not contact a provider to check).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `voice_id` | path | string | yes | Library voice ID (`vl_…`). |

Request body (`application/json`): [VoiceEdit](#schema-voiceedit)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [LibraryVoice](#schema-libraryvoice) | Success. |
| 400 | [Error](#schema-error) | - `voice_name_invalid`: The name is only whitespace. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `voice_not_found`: No library voice has this ID (including a malformed ID). Deleted voices also return it. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="deletelibraryvoice"></a>
### `DELETE /api/voices/{voice_id}`

**Delete a voice** · operation `deleteLibraryVoice` · cost `network`

Deletes the voice's provider voices (optionally), then marks the library voice deleted. The record is kept as a tombstone: it disappears from `GET /api/voices`, its versions and history stay, and a Breeze server voice behind a deleted voice is never re-imported by a connection check. Characters still assigned to it fail closed: narration is refused with "The voice … was deleted. Choose another voice in the cast."

**Provider deletion (`server`)**: when omitted, voices Bardic made (`origin` `designed` or `cloned`) are deleted on the provider, while voices imported from the Breeze server are removed from Bardic only. `server=true` deletes every distinct provider voice behind every version (Breeze server voices, or stored Gemini voices in the current Google project); `server=false` never touches the provider. A provider that says the voice is already gone (404) counts as deleted.

**Partial failure is recorded and resumable.** Provider voices are deleted one at a time, and each deletion is recorded before the next starts. If one fails, the request returns 502 `provider_error` (the detail says how many are deleted), and the library voice stays live: the already-deleted versions report `server_state: "missing"`, and `warnings` says the deletion is unfinished. Deleting again skips the recorded ones, deletes the rest, then marks the voice deleted. `server_deleted` lists every provider voice the deletion removed, across attempts.

Deleting an already-deleted voice returns 200 with an empty `server_deleted` and changes nothing. Refused while any narration job is active for a book that follows the voice.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `voice_id` | path | string | yes | Library voice ID (`vl_…`). |
| `server` | query | boolean \| null |  | Whether to delete the provider voices too. Omit for the default (true for designed or cloned voices, false for imported ones). Accepts `true`/`false` (also `1`/`0`, `yes`/`no`, `on`/`off`). |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [LibraryVoiceDeleted](#schema-libraryvoicedeleted) | Success. |
| 400 | [Error](#schema-error) | - `breeze_url_missing`: No Breeze server URL is configured. - `gemini_key_missing`: No Gemini API key is configured. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `voice_not_found`: No library voice has this ID (including a malformed ID). |
| 409 | [Error](#schema-error) | - `voice_is_default`: The voice is the Breeze default; choose another default first. - `voice_other_project`: Provider deletion was requested for a Gemini voice with a version made with a different Google API key. Retry with `server=false` to remove it from Bardic only. - `narration_active`: A `render`, `listen`, `listen_chapter` or `voice_preview` job is queued or running for a book whose characters follow an affected voice. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |
| 502 | [Error](#schema-error) | - `provider_error`: Deleting a provider voice failed. Earlier ones in this request are recorded as deleted; deleting again resumes. |

<a id="setlibraryvoicecurrentversion"></a>
### `POST /api/voices/{voice_id}/current`

**Switch the current version** · operation `setLibraryVoiceCurrentVersion` · cost `none`

Makes an existing version the current one. Choosing the version that is already current is a no-op that still returns 200. Local only.

This re-voices every character that follows the affected voice: selected takes made with the previous voice become out of date but stay in history, and rendering again after switching back reuses the archived WAV without a provider request. Refused with 409 while a `render`, `listen`, `listen_chapter` or `voice_preview` job is queued or running for a book with a character following the voice.

The returned `LibraryVoice` compares each version with the same saved provider checks as `GET /api/voices` (read locally; this does not contact a provider to check).

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `voice_id` | path | string | yes | Library voice ID (`vl_…`). |

Request body (`application/json`): [CurrentVersion](#schema-currentversion)

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | [LibraryVoice](#schema-libraryvoice) | Success. |
| 400 | [Error](#schema-error) | - `unknown_version`: The voice has no version with the given number. |
| 403 | [Error](#schema-error) | A browser write from another origin was rejected by the write guard (see Transport and security). |
| 404 | [Error](#schema-error) | - `voice_not_found`: No library voice has this ID (including a malformed ID). Deleted voices also return it. |
| 409 | [Error](#schema-error) | - `narration_active`: A `render`, `listen`, `listen_chapter` or `voice_preview` job is queued or running for a book whose characters follow an affected voice. |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |

<a id="getlibraryvoiceaudition"></a>
### `GET /api/voices/{voice_id}/versions/{version}/audition`

**Play a voice version's audition** · operation `getLibraryVoiceAudition` · cost `network`

Returns the version's retained audition WAV (Bardic's 24 kHz mono copy of the auditioned preview, recording or imported reference clip). When none is retained, fetches the Breeze reference clip or the Gemini stored voice's sample from the provider on every request (not cached) and returns those bytes labelled `audio/wav`. Works for deleted voices too.

| Parameter | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `voice_id` | path | string | yes | Library voice ID (`vl_…`). |
| `version` | path | integer | yes | Version number (integer, from 1). |

| Status | Body | Meaning |
| --- | --- | --- |
| 200 | `audio/wav` | Success. |
| 206 | `audio/wav` | Partial content for a `Range` request (served from a file; see `Content-Range`). |
| 400 | [Error](#schema-error) | - `breeze_url_missing`: A provider fetch is needed and no Breeze server URL is configured. - `gemini_key_missing`: A provider fetch is needed and no Gemini API key is configured. |
| 404 | [Error](#schema-error) | - `voice_not_found`: No library voice has this ID (including a malformed ID). - `voice_version_not_found`: The voice has no version with this number. - `audio_not_found`: Nothing is retained and the provider has no sample for this version. |
| 416 |  | The requested `Range` cannot be satisfied (empty body; see `Content-Range`). |
| 422 | [Error](#schema-error) | The request failed validation: a missing, extra or out-of-range field or parameter. |
| 502 | [Error](#schema-error) | - `provider_error`: The provider refused or failed the request, or was unreachable. The detail is Bardic's own sentence; provider text is not echoed. |

## Schemas

<a id="schema-accountcheck"></a>
### AccountCheck

The result of the last explicit account check for one cloud analysis provider.

A check sends one tiny text request (`Reply with only the word OK.`, at
most 128 output tokens) to the selected analysis model. It shows whether
that key and model can generate text now. It does not report a balance,
remaining credit or narration access.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | `"gemini"` \| `"openai"` \| `"anthropic"` | yes | Cloud analysis provider this check is for: `gemini`, `openai` or `anthropic`. |
| `model` | string | yes | The analysis model the check uses (the saved choice for this provider). |
| `state` | `"unchecked"` \| `"missing_key"` \| `"checking"` \| `"ready"` \| `"inconclusive"` \| `"invalid_key"` \| `"access_denied"` \| `"billing_blocked"` \| `"model_unavailable"` \| `"rate_limited"` \| `"provider_error"` \| `"network_error"` | yes | `unchecked`: no check for the current key and model. `missing_key`: no key. `checking`: a check is running now. `ready`: the request completed. `inconclusive`: the provider answered but the check did not complete (unknown readiness). `invalid_key`, `access_denied`, `billing_blocked`, `model_unavailable`, `rate_limited`, `provider_error`: classified provider refusals. `network_error`: the provider could not be reached. |
| `message` | string | yes | Fixed local explanation (never provider text). Display only. |
| `checked_at` | string \| null | yes | When the check finished, or null when never checked. ISO 8601 UTC timestamp with offset, for example `2026-09-28T17:04:05.123456+00:00`. |
| `usage` | [AccountCheckUsage](#schema-accountcheckusage) \| null | yes | Reported usage of the check request, or null. |
| `http_status` | integer \| null | yes | Provider HTTP status of the check request, or null when none was received. |
| `balance` | null | yes | Always null: an exact balance is not available through an inference key. |
| `balance_note` | string | yes | Explains why no balance is shown. |
| `cached` | boolean | yes | True when an identical check from the last 30 seconds was returned without a new request. |
| `billing_url` | string | yes | Provider billing dashboard URL. |
| `usage_url` | string | yes | Provider usage dashboard URL. |

<a id="schema-accountcheckusage"></a>
### AccountCheckUsage

Token usage the provider reported for the check request. Absent counts were not reported.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `input_tokens` | integer \| null |  | Input tokens. |
| `output_tokens` | integer \| null |  | Output tokens. |
| `total_tokens` | integer \| null |  | Total tokens (computed from input and output when not reported). |

<a id="schema-analysiscatalogmodel"></a>
### AnalysisCatalogModel

One analysis model choice for a cloud provider.

Curated entries carry dated published prices; models found only by a
refresh have unknown capabilities and prices (null). Prices are list
prices for standard text requests, not a bill.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Model ID to save in `analysis_models_by_provider` or `preprocess_models_by_provider`. |
| `label` | string | yes | Display name (the ID for models found only by a refresh). |
| `tier` | `"balanced"` \| `"economy"` \| `"deep"` \| `"other"` | yes | Rough capability/cost tier; `other` for models found only by a refresh. |
| `roles` | list of `"preprocess"` \| `"analysis"` | yes | Where the model may be chosen. |
| `structured_output` | boolean \| null | yes | Whether JSON-schema output is supported; null when unknown (a refreshed model that does not advertise it). |
| `context_tokens` | integer \| null | yes | Context window in tokens, or null when unknown. |
| `max_output_tokens` | integer \| null | yes | Largest output in tokens, or null when unknown. |
| `input_usd_per_million` | number \| null | yes | Published USD price per million input tokens, or null when unknown or not quoted (for example a model whose price depends on prompt length). |
| `output_usd_per_million` | number \| null | yes | Published USD price per million output tokens, or null. |
| `source_url` | string | yes | Provider documentation page for the model list. |
| `pricing_source_url` | string \| null | yes | Provider pricing page the prices came from, or null. |
| `price_date` | string \| null | yes | Date (YYYY-MM-DD) the prices were recorded, or null. |
| `availability` | `"unverified"` \| `"listed"` \| `"not_listed"` | yes | `unverified`: from the curated list, not checked (or the refresh listing was partial). `listed`: the provider listed it for this key in the last refresh. `not_listed`: a curated model the complete listing did not include. Listing proves visibility only, not working generation or credit. |
| `price_valid_until` | string \| null |  | Last date (YYYY-MM-DD) the quoted price applies; absent when open-ended. Cost estimates treat the price as unknown after it. |
| `price_input_token_limit` | integer \| null |  | Prompt size in tokens above which the quoted price does not apply; absent when none. |

<a id="schema-analysiscensus"></a>
### AnalysisCensus

Free, rules-based whole-book census. Cached per book input; recomputed when the text, structure, cast or
attributions change. Analysis runs and plan previews also retain it as a `census` artifact.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `version` | integer | yes | Census algorithm version (currently 1). |
| `local_complete` | boolean | yes | Always true: the local census covers every chapter. |
| `local_chapters_scanned` | integer | yes | Chapters scanned (all chapters, including front/back matter). |
| `eligible_chapter_ids` | list of string | yes | Chapters eligible for cloud discovery and direction, in book order. |
| `eligible_chapters` | integer | yes | Count of eligible chapters. |
| `chapters` | list of [CensusChapter](#schema-censuschapter) | yes | One row per chapter of the book in book order, including front and back matter (see `eligible`). |
| `characters` | list of [CensusCharacter](#schema-censuscharacter) | yes | Known characters and speech-tag candidates (excluding `narrator` and `unassigned`), highest priority first. |
| `words` | integer | yes | Total words across all chapters. |
| `estimated_source_tokens` | integer | yes | Rough token estimate for eligible chapters only. |
| `note` | string | yes | Interpretation caveat. Display only. |

<a id="schema-analysischapterprogress"></a>
### AnalysisChapterProgress

Per-chapter progress row of the classic analysis checkpoint.

Rows are created for every chapter of the book when a checkpoint is first
written; chapters outside the requested scope keep their earlier state.
Rows written by a newer cloud run start with only ``id``, ``title``,
``status``, ``discovery_complete`` and ``directing_complete``; the other
fields appear once the run reaches that chapter.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Chapter ID. |
| `title` | string \| null |  | Chapter title at the time the row was written. |
| `stage` | `"discovery"` \| `"directing"` \| `"complete"` \| null |  | Last stage this chapter reached. Absent until a run works on the chapter. |
| `status` | `"pending"` \| `"running"` \| `"completed"` \| `"failed"` \| `"interrupted"` \| `"budget_limited"` | yes | State of the chapter within its current stage. `interrupted` also covers a user cancellation and a server restart; `budget_limited` means a request/token/dollar allowance stopped the run. |
| `completed_units` | integer \| null |  | Requests (units) finished for this chapter in its current stage. |
| `total_units` | integer \| null |  | Requests (units) planned for this chapter in its current stage. |
| `error` | string \| null |  | Human-readable failure text for this chapter, or null. Display only. |
| `discovery_complete` | boolean \| null |  | True when character discovery covers this chapter (for newer cloud runs: validated discovery covers the whole chapter text). |
| `directing_complete` | boolean \| null |  | True when passage direction for this chapter is complete and still matches the current cast. |

<a id="schema-analysiscoverage"></a>
### AnalysisCoverage

Local census, semantic discovery coverage, tracked usage and profile freshness.

Whole-book discovery coverage and profile freshness are separate facts.
Only validated cloud discovery counts as semantic coverage; local drafts do not.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `local` | [AnalysisCensus](#schema-analysiscensus) | yes | The free local census. |
| `semantic_chapters_complete` | integer | yes | Eligible chapters whose full text is covered by validated cloud discovery. |
| `semantic_chapter_ids` | list of string | yes | Those chapter IDs, in book order. |
| `eligible_chapters` | integer | yes | Eligible chapters (same as `local.eligible_chapters`). |
| `whole_book_discovered` | boolean | yes | True when every eligible chapter is covered (false for a book with none). |
| `profiles_provisional` | boolean | yes | True unless the whole book is discovered and every profile is current or reviewed. |
| `usage` | [AnalysisUsage](#schema-analysisusage) | yes |  |
| `note` | string | yes | Interpretation caveat. Display only. |
| `characters` | list of [ProfileFreshness](#schema-profilefreshness) | yes | Profile freshness per cast member, excluding `narrator` and `unassigned`. |
| `profiles_current` | integer | yes | Profiles whose state is `current` or `reviewed`. |
| `profiles_total` | integer | yes | Profiles considered (cast members excluding `narrator` and `unassigned`). |

<a id="schema-analysislimits"></a>
### AnalysisLimits

Allowances reserved before every analysis HTTP attempt, including retries and evidence repairs. When one would be exceeded the run stops as `budget_limited` and keeps its validated work. Request and token caps apply to the run; the dollar guard includes prior tracked analysis for the book. Unknown prices, or earlier attempts of unknown cost, stop a run that has a dollar guard. These limits do not cap narration (TTS) spending and do not represent account credit. For a series run they apply separately to each book, so the possible collection-wide spend grows with the number of books.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `max_requests` | integer |  | Maximum HTTP attempts in this run, 1–1,000 (default 25). (≥ `1.0`; ≤ `1000.0`; default `25`) |
| `max_input_tokens` | integer |  | Maximum input tokens in this run, 1,000–10,000,000 (default 1,000,000). Reported usage counts; attempts without reported usage count their conservative reservation. (≥ `1000.0`; ≤ `10000000.0`; default `1000000`) |
| `max_output_tokens` | integer |  | Maximum output tokens in this run, 1,000–2,000,000 (default 100,000). Same accounting. (≥ `1000.0`; ≤ `2000000.0`; default `100000`) |
| `budget_usd` | number \| null |  | Tracked analysis allowance for the whole book in USD, including earlier runs: greater than 0 and at most 1,000 (default 1.0). Explicit null removes the dollar guard, leaving the request and token caps. (default `1.0`) |

<a id="schema-analysismodelcatalog"></a>
### AnalysisModelCatalog

Analysis model choices for one cloud provider: the curated list, or the last refresh for the current key.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | `"gemini"` \| `"openai"` \| `"anthropic"` | yes | Cloud analysis provider this catalog lists: `gemini`, `openai` or `anthropic`. |
| `state` | `"curated"` \| `"ready"` \| `"missing_key"` \| `"invalid_key"` \| `"refreshing"` \| `"access_denied"` \| `"rate_limited"` \| `"unavailable"` | yes | `curated`: documented choices only (never refreshed for this key). `ready`: the provider listing succeeded and is merged in. `missing_key` / `invalid_key`: no request was sent. `refreshing`: another refresh for this provider is running. `access_denied`, `rate_limited`, `unavailable`: the listing failed; the curated choices remain usable. |
| `catalog_date` | string | yes | Date (YYYY-MM-DD) of the curated list. |
| `message` | string | yes | Human-readable explanation. Display only. |
| `checked_at` | string \| null | yes | When the last refresh for this key finished, or null. ISO 8601 UTC timestamp with offset, for example `2026-09-28T17:04:05.123456+00:00`. |
| `cached` | boolean | yes | True when a refresh returned a recent result without contacting the provider (successful listings are reused for an hour, failures for 30 seconds). |
| `source_url` | string | yes | Provider documentation page for its models. |
| `models` | list of [AnalysisCatalogModel](#schema-analysiscatalogmodel) | yes | Curated models first (in curated order), then models only the listing found, sorted by ID. |
| `partial` | boolean \| null |  | Present after a successful refresh: true when the listing had more pages than were read, so unlisted curated models stay `unverified`. |

<a id="schema-analysisplan"></a>
### AnalysisPlan

Preview of currently known classic-analysis work. No provider is contacted.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `phase` | `"scan"` \| `"profiles"` \| `"direct"` \| `"full"` | yes | Requested phase. |
| `provider` | string | yes | Resolved provider: the request value, or the saved default analysis provider. |
| `scan_model` | string \| null | yes | Configured discovery (fast) model for this provider, or null when none is configured (the run then falls back to the detailed model or the built-in default). |
| `model` | string \| null | yes | Configured detailed model for this provider, or null (the run then uses the built-in default). |
| `requests` | integer | yes | Requests that are not already cached. Always 0 for `local`. Excludes retries and evidence repairs. |
| `cached_units` | integer | yes | Known units that validated cached output would satisfy (only when `resume` is true). |
| `estimated_input_tokens` | integer | yes | Approximate input tokens of the pending requests (0 for `local`). |
| `output_token_allowance` | integer | yes | Sum of the pending requests' output caps (0 for `local`). |
| `estimated_cost_usd` | number \| null | yes | Approximate USD cost of the pending requests; null when a model has no known price; 0 for `local`. Not an invoice. |
| `steps_by_stage` | [AnalysisPlanStageCounts](#schema-analysisplanstagecounts) | yes | Pending requests by stage. Computed for `local` too, although a local run does not send these requests. |
| `coverage` | [AnalysisCoverage](#schema-analysiscoverage) | yes | Same body as `GET /api/books/{book_id}/preprocessing`, with profile freshness computed against the plan's working cast. |
| `future_work_unknown` | boolean | yes | True for `full`: discovery can add profiles and change direction prompts, so the estimate is incomplete. |
| `note` | string | yes | Interpretation caveat. Display only. |
| `limits` | [AnalysisPlanLimits](#schema-analysisplanlimits) | yes |  |

<a id="schema-analysisplanlimits"></a>
### AnalysisPlanLimits

Echo of the request's `limits` with defaults applied. The preview does not enforce them.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `max_requests` | integer | yes | Per-run HTTP request cap. |
| `max_input_tokens` | integer | yes | Per-run input-token cap. |
| `max_output_tokens` | integer | yes | Per-run output-token cap. |
| `budget_usd` | number \| null | yes | Cumulative tracked analysis allowance for the book in USD; null means no dollar guard. |

<a id="schema-analysisplanstagecounts"></a>
### AnalysisPlanStageCounts

Pending (uncached) requests by stage.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `discovery` | integer | yes | Character-discovery requests (fast model). |
| `profiles` | integer | yes | Profile-refinement requests (detailed model). |
| `directing` | integer | yes | Passage/scene direction requests (detailed model). |

<a id="schema-analysisproviderstatus"></a>
### AnalysisProviderStatus

An analysis provider and whether it is usable.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `"local"` \| `"gemini"` \| `"openai"` \| `"anthropic"` | yes | Analysis provider ID: `local` (offline draft analysis, no key) or a cloud provider `gemini`, `openai`, `anthropic`. |
| `label` | string | yes | Display name. |
| `available` | boolean | yes | `local` is always available; a cloud provider is available when its key is loaded. |
| `has_api_key` | boolean | yes | True when a key is loaded (always false for `local`). The key is never returned. |
| `model` | string \| null | yes | The saved analysis model for this provider, or null for `local`. |
| `models` | list of string | yes | Curated model IDs (empty for `local`). A saved custom model may be absent from this list. |

<a id="schema-analysisrequest"></a>
### AnalysisRequest

A classic analysis request, used both to preview (`analysis-plan`) and to start (`analyze`). Send the same body to both.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | string \| null |  | `local`, `gemini`, `openai` or `anthropic`. Omitted or null uses the saved default analysis provider. `local` runs the free heuristic draft (no semantic discovery) and ignores `phase` and `limits`. |
| `chapter_id` | string \| null |  | Limit the run to this chapter of the book (must belong to it). Omitted or null: the whole book (cloud phases skip front and back matter; the local draft covers every chapter). Profile refinement is book-wide regardless. |
| `resume` | boolean |  | Reuse validated saved units and chapter progress (default true). False re-requests work that would otherwise be reused (retained history is kept). (default `true`) |
| `phase` | `"scan"` \| `"profiles"` \| `"direct"` \| `"full"` |  | Cloud phase: `scan` (default; character discovery with the fast model), `profiles` (refine profiles from retained evidence and confirmed earlier-series context), `direct` (annotate passages and scenes with the detailed model) or `full` (all three; its initial estimate is incomplete). (default `"scan"`) |
| `limits` | [AnalysisLimits](#schema-analysislimits) |  | Allowances checked before every HTTP attempt. Omitted: all defaults. |

<a id="schema-analysisstatus"></a>
### AnalysisStatus

Checkpoint summary of the classic analysis engine for one book.

When no checkpoint exists the server synthesizes `status: "not_started"`
with one pending row per chapter. A checkpoint is written by
`POST /api/books/{book_id}/analyze` (both the local draft and the cloud
phases) and by the per-book `analyze` children of a series run. It
survives failures, so it can describe an older run than the latest job.
Source text and model responses are never included.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | string \| null | yes | Analysis provider of the checkpoint: `local`, `gemini`, `openai` or `anthropic`; null when not started. |
| `model` | string \| null | yes | Detailed-analysis model ID used, or null (local drafts and not started). |
| `status` | `"not_started"` \| `"running"` \| `"completed"` \| `"failed"` \| `"interrupted"` \| `"budget_limited"` | yes | Overall state. `interrupted` covers cancellation and server restarts; `budget_limited` means a request/token/dollar allowance stopped the run. Validated work is kept in every case. |
| `stage` | `"discovery"` \| `"preprocessing"` \| `"profiles"` \| `"directing"` \| `"complete"` \| null |  | Stage in progress or last reached. `preprocessing` is the free local census at the start of a cloud run; `complete` after success. |
| `phase` | `"scan"` \| `"profiles"` \| `"direct"` \| `"full"` \| null |  | Requested cloud phase. Absent for local drafts and for checkpoints written before phases existed. |
| `scan_model` | string \| null |  | Fast model used for discovery in cloud runs. Absent for local drafts. |
| `completed_units` | integer \| null |  | Units (requests or batches) completed in the latest run. Absent only if a run failed before its first save. |
| `total_units` | integer \| null |  | Units known so far for the latest run. A `full` run discovers more work as it goes, so this can grow. |
| `current_chapter_id` | string \| null | yes | Chapter being worked on, or null between chapters, during profiles and when idle. |
| `scope_chapter_id` | string \| null |  | The `chapter_id` the run was limited to, or null for the whole book. Absent when not started. |
| `error` | string \| null |  | Human-readable failure text of the latest run, or null. Display only. |
| `updated_at` | string \| null |  | ISO 8601 UTC time the checkpoint was last saved. Absent when not started. |
| `chapters` | list of [AnalysisChapterProgress](#schema-analysischapterprogress) | yes | One row per chapter of the book (as of the checkpoint). |

<a id="schema-analysisusage"></a>
### AnalysisUsage

Tracked analysis request usage for a book, across all runs (classic and step pipeline).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `attempts` | integer | yes | Recorded analysis HTTP attempts, including failed and repair attempts. |
| `input_tokens` | integer | yes | Sum of reported input tokens. Attempts without reported usage add 0 here; see `unknown_usage_attempts`. |
| `output_tokens` | integer | yes | Sum of reported output tokens (same caveat). |
| `estimated_spend_usd` | number | yes | Sum of the conservative per-attempt estimates in USD (dated prices, guard uplift, reservations when usage is missing). Not an invoice. |
| `unknown_cost_attempts` | integer | yes | Attempts whose cost is unknown. Unknown is not zero. |
| `unknown_usage_attempts` | integer | yes | Attempts missing input or output token counts. |
| `note` | string | yes | Interpretation caveat. Display only. |

<a id="schema-artifactcounts"></a>
### ArtifactCounts

Retained artifact counts for one book.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `kinds` | list of string | yes | Distinct artifact kinds present, sorted. |
| `stages` | map of string → integer | yes | Version count by artifact stage. |
| `total` | integer | yes | All retained versions owned by the book. |
| `current` | integer | yes | Currently selected versions (heads). |

<a id="schema-artifactdependencylink"></a>
### ArtifactDependencyLink

A dependency and the book that owns it.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Dependency artifact ID. |
| `book_id` | string | yes | Owning book; fetch the dependency under this book's path. |

<a id="schema-artifactdetail"></a>
### ArtifactDetail

One artifact version with its payload.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Artifact version ID (`artifact_` + content hash). |
| `book_id` | string | yes | Owning book. |
| `kind` | string | yes | Artifact kind. Known kinds: `source` (chapter text), `structure` (book structure), `scene_map` (per-chapter scenes and passages), `character_profile`, `voice_assignment`, `audio_take` (passage audio metadata), `census`, `character_observation`, `series_context`, `series_run`, `library_state`, `analysis_input` (request recipe), `analysis_output` (validated result), `analysis_rejection` (rejected result with its validation error) and `step_output` (step-pipeline version). Treat as an open set. |
| `logical_key` | string | yes | Scope within the kind (e.g. chapter ID, character ID, unit key, `book`). One version per (book, kind, logical_key) can be current. |
| `label` | string | yes | Display label. |
| `stage` | string | yes | Producing stage, e.g. `import`, `structure`, `census`, `discovery`, `profiles`, `directing`, `voices`, `narration`, `series`, `library`, a pipeline step ID, or empty for some legacy records. |
| `provider` | string \| null | yes | Producing provider, where recorded. |
| `model` | string \| null | yes | Producing model, where recorded. |
| `schema_version` | integer | yes | Artifact record schema version (currently 1). |
| `legacy_provenance` | boolean | yes | True when retained after the fact or from an older version: original production inputs may be incomplete and are not reconstructed. |
| `payload_bytes` | integer | yes | Size of the stored JSON payload in UTF-8 bytes. |
| `dependencies` | list of string | yes | Artifact IDs this version was produced from (actual retained inputs; may belong to earlier books). |
| `created_at` | string | yes | ISO 8601 UTC time the version was first retained. |
| `is_current` | boolean | yes | True when this version is the current selection for its scope. Rejected, superseded and unselected candidates stay retained with false. |
| `dependency_links` | list of [ArtifactDependencyLink](#schema-artifactdependencylink) | yes | The dependencies with their owning books, ordered by ID. |
| `payload` | any | yes | The literal retained JSON, whose shape depends on `kind` (and for `step_output`, on the step and its version). Any JSON value. Payloads are historical records: shapes from older versions remain as retained. Examples: `source` is `{chapter_id, text, text_sha256}`; `analysis_output` is a validated unit with `result`; `analysis_rejection` is `{result, validation_error, unit_key, attempt_id?}`; `step_output` is `{schema_version, step_id, step_version, scope, origin, inputs, result}`. |

<a id="schema-artifactpage"></a>
### ArtifactPage

One page of artifact metadata, newest first.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `items` | list of [ArtifactSummary](#schema-artifactsummary) | yes | This page of artifact versions matching the filters, newest first (by creation time). Empty past the end. |
| `total` | integer | yes | Versions matching the filters. |
| `offset` | integer | yes | Effective offset after clamping to >= 0 (versions skipped). |
| `limit` | integer | yes | Effective page size after clamping to 1–200. |

<a id="schema-artifactsummary"></a>
### ArtifactSummary

Metadata of one immutable artifact version.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Artifact version ID (`artifact_` + content hash). |
| `book_id` | string | yes | Owning book. |
| `kind` | string | yes | Artifact kind. Known kinds: `source` (chapter text), `structure` (book structure), `scene_map` (per-chapter scenes and passages), `character_profile`, `voice_assignment`, `audio_take` (passage audio metadata), `census`, `character_observation`, `series_context`, `series_run`, `library_state`, `analysis_input` (request recipe), `analysis_output` (validated result), `analysis_rejection` (rejected result with its validation error) and `step_output` (step-pipeline version). Treat as an open set. |
| `logical_key` | string | yes | Scope within the kind (e.g. chapter ID, character ID, unit key, `book`). One version per (book, kind, logical_key) can be current. |
| `label` | string | yes | Display label. |
| `stage` | string | yes | Producing stage, e.g. `import`, `structure`, `census`, `discovery`, `profiles`, `directing`, `voices`, `narration`, `series`, `library`, a pipeline step ID, or empty for some legacy records. |
| `provider` | string \| null | yes | Producing provider, where recorded. |
| `model` | string \| null | yes | Producing model, where recorded. |
| `schema_version` | integer | yes | Artifact record schema version (currently 1). |
| `legacy_provenance` | boolean | yes | True when retained after the fact or from an older version: original production inputs may be incomplete and are not reconstructed. |
| `payload_bytes` | integer | yes | Size of the stored JSON payload in UTF-8 bytes. |
| `dependencies` | list of string | yes | Artifact IDs this version was produced from (actual retained inputs; may belong to earlier books). |
| `created_at` | string | yes | ISO 8601 UTC time the version was first retained. |
| `is_current` | boolean | yes | True when this version is the current selection for its scope. Rejected, superseded and unselected candidates stay retained with false. |

<a id="schema-assignment"></a>
### Assignment

A book character to assign a saved voice to, as a cast edit (the book must not be busy).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `book_id` | string | yes | The book. (max length `200`) |
| `character_id` | string | yes | Book-local character ID in that book. (max length `200`) |

<a id="schema-audiotakebreezeinfo"></a>
### AudioTakeBreezeInfo

Breeze request details retained with a take.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `request_id` | string \| null |  | The server's `x-request-id`, truncated to 80 characters; null or absent when the server sent none. |
| `timing_accepted` | boolean | yes | Whether the server-reported sentence timing validated against the sent text. |
| `vocal_event_markup` | list of string \| null |  | Lower-cased vocal event tags (for example `[laugh]`) found in the sent text; absent when none. |

<a id="schema-audiotakesentencespan"></a>
### AudioTakeSentenceSpan

One provider-reported sentence inside a take.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `char_start` | integer | yes | Start code-point offset into the text that was sent (not chapter coordinates). |
| `char_end` | integer | yes | Exclusive end code-point offset into the text that was sent. |
| `start` | number | yes | Start time in the take, seconds. |
| `end` | number | yes | End time in the take, seconds. |

<a id="schema-audiotakesentencetiming"></a>
### AudioTakeSentenceTiming

Sentence timing reported by the Breeze server, accepted only when every offset matched the sent text.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `schema_version` | integer | yes | Timing format version (1). |
| `kind` | `"sentence"` | yes | Timing granularity. |
| `source` | `"breeze"` | yes | Who measured the timing. |
| `offsets` | `"recipe_text_code_points"` | yes | What the character offsets index into. |
| `segments` | list of [AudioTakeSentenceSpan](#schema-audiotakesentencespan) | yes | Sentences in order. |

<a id="schema-audiotakeusage"></a>
### AudioTakeUsage

Provider usage measured for the request that produced a take.

Counts are reported by the provider, never inferred from audio length.
Absent or null values are unknown, not zero. Test synthesizers and older
takes may carry only some of these fields.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `input_tokens` | integer \| null |  | Reported input tokens (Gemini). |
| `output_tokens` | integer \| null |  | Reported output (audio) tokens (Gemini). |
| `cached_input_tokens` | integer \| null |  | Reported cached input tokens (Gemini). |
| `usage_source` | string \| null |  | Where the counts came from: `gemini_interactions`, `not_reported` or `breeze`. |
| `estimated_cost_usd` | number \| null |  | Standard paid-tier list-price estimate in USD, or null when it cannot be priced. Not an account balance or bill. 0 for self-hosted Breeze. |
| `cost_basis` | string \| null |  | How `estimated_cost_usd` was derived, for example `standard_paid_tier_usage_estimate`, `unknown` or `self_hosted`. |
| `price_as_of` | string \| null |  | Date of the price table used (Gemini). |
| `price_source` | string \| null |  | Source of the price table (Gemini). |
| `characters` | integer \| null |  | Characters the Breeze server reported synthesizing. |

<a id="schema-audiotakevoicelibrary"></a>
### AudioTakeVoiceLibrary

The voice-library voice and version that performed a take.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Voice library ID (`vl_…`). |
| `version` | integer \| null | yes | Library voice version number. |

<a id="schema-book"></a>
### Book

The full book document: the reader's projection of one imported book.

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

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Book UUID. |
| `title` | string | yes | Display title. |
| `author` | string | yes | Display author(s), comma-separated; may be empty. |
| `source_name` | string | yes | File name of the imported original (without directories), for example `story.epub`. |
| `created_at` | string | yes | ISO 8601 UTC import time. |
| `revision` | integer | yes | Projection revision; starts at 1 on import (the demo starts at 2) and increases by 1 per change. |
| `structure_version` | integer \| null |  | Version of the structure interpretation (currently 2). Absent on books imported before structure metadata; structure repair sets it. |
| `chapters` | list of [BookChapter](#schema-bookchapter) | yes | Source containers in reading order. |
| `scenes` | list of [BookScene](#schema-bookscene) | yes | Scenes in reading order. |
| `segments` | list of [BookPassage](#schema-bookpassage) | yes | All passages ("segments") in reading order. |
| `characters` | list of [BookCharacter](#schema-bookcharacter) | yes | The book-local cast, including `narrator` and `unassigned`. |
| `analysis` | [BookAnalysisSummary](#schema-bookanalysissummary) | yes | Who produced the current annotations. |
| `cover` | [BookCover](#schema-bookcover) \| null |  | Cover thumbnail metadata; absent when the original had no usable cover. |
| `pronunciations` | list of [BookPronunciation](#schema-bookpronunciation) \| null |  | The book's pronunciations, in saved order; absent when there are none. Managed with the Pronunciations operations, which also report where each term occurs. |

<a id="schema-bookanalysissummary"></a>
### BookAnalysisSummary

The current overall analysis summary for the book.

Only a short label of who produced the current projection. Detailed,
resumable progress is in `GET /api/books/{book_id}/analysis` and the
analysis pipeline routes.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | string | yes | Who produced the current annotations: `local` (free heuristic draft, also at import), `gemini`, `openai`, `anthropic`, `local_llm`, or a self-hosted service (`novel_analyzer`, `booknlp`) accepted through the analysis pipeline. Open set. |
| `model` | string \| null |  | Model ID used, or null/absent for local or service analysis. |
| `status` | `"draft"` \| `"partial"` | yes | `draft`: a complete draft awaiting review (import, local or completed classic analysis). `partial`: staged work in progress or a pipeline step accepted; other parts may be missing or older. |
| `notes` | string \| null |  | Human-readable explanation of the draft and what to review. Display only. The server writes it with every summary; treat an absent value as empty. |
| `phase` | string \| null |  | Classic analysis phase that published this state (`scan`, `profiles`, `direct`, `full`) or the pipeline step ID that was accepted (for example `discovery`, `profiles`, `directing`). Absent for import and local drafts. |
| `profiles_provisional` | boolean \| null |  | Classic progressive analysis only: true while character profiles still need whole-book discovery or refinement against current evidence. |

<a id="schema-bookbreezesettings"></a>
### BookBreezeSettings

Optional Breeze sampling overrides of a pinned choice. No current API or UI sets them.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `temperature` | number \| null |  | Sampling temperature override, 0.05-2.0. Absent or null keeps the Breeze voice's own setting. |
| `cfg_scale` | number \| null |  | Classifier-free guidance scale override, 0.5-10.0. Absent or null keeps the voice's own setting. |
| `top_p` | number \| null |  | Nucleus sampling probability override, 0.01-1.0. Absent or null keeps the voice's own setting. |
| `top_k` | integer \| null |  | Top-k sampling override, an integer 1-1024. Absent or null keeps the voice's own setting. |

<a id="schema-bookchapter"></a>
### BookChapter

One source container in reading order: an EPUB spine document or a TXT heading section.

It is not necessarily a narrative chapter; see `kind`. `text` is the
immutable canonical reading text. Structure fields are absent on books
imported before structure metadata existed (no `structure_version`);
`POST /api/books/{book_id}/repair-structure` adds them.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Opaque chapter ID, stable for the life of the book. |
| `index` | integer | yes | Zero-based position in import order. Sort by array order, not by this value; it can have gaps. |
| `title` | string | yes | Display title (from navigation, heading, landmark, or a fallback such as "Section 3"). |
| `text` | string | yes | Canonical reading text, never rewritten by analysis. Scene-break ornament lines (`***`, `---`, `•`, `⁂`) are replaced by the same number of spaces so offsets stay valid. All `start`/`end` offsets in the book are zero-based Unicode code-point offsets into this string, end-exclusive (not UTF-8 bytes, not UTF-16 indices). |
| `kind` | `"chapter"` \| `"section"` \| `"front_matter"` \| `"back_matter"` \| `"recap"` \| null |  | Structural classification. `chapter` is a narrative chapter; `section` an unlabeled or multi-entry container that stays eligible for analysis; `front_matter`/`back_matter` non-story material; `recap` a "story so far" section. |
| `title_source` | `"epub_nav"` \| `"epub_ncx"` \| `"heading"` \| `"landmark"` \| `"semantics"` \| `"fallback"` \| null |  | Where `title` came from. |
| `source_href` | string \| null |  | Path of the EPUB spine document inside the archive; null for TXT imports. |
| `logical_sections` | list of [BookLogicalSection](#schema-booklogicalsection) \| null |  | Table-of-contents entries inside this container (EPUB only; empty for TXT). |
| `narrative_order` | integer \| null |  | 1-based number among `kind: chapter` containers only; absent for other kinds. Never an invented chapter number. |
| `trailing_text` | string | yes | Presented only: the chapter text after its last passage (usually whitespace). Together with each passage's `leading_text` and `text` it rebuilds `text` exactly. |

<a id="schema-bookcharacter"></a>
### BookCharacter

A book-local cast member.

Character IDs are book-local, not series identities (series links are
separate). Every book has the reserved characters `narrator` and
`unassigned` (dialogue whose speaker needs review).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Opaque book-local character ID; `narrator` and `unassigned` are reserved. |
| `name` | string | yes | Display name (1–100 characters when set by hand). |
| `aliases` | list of string | yes | Other names that identify this character in the text. |
| `description` | string | yes | Voice and personality profile (draft or reviewed). |
| `direction` | string | yes | Standing performance direction for this character's voice. |
| `evidence` | list of string \| null |  | Exact source quotations supporting the profile (at most 12). Absent on some characters saved by older versions; treat as empty. |
| `voices` | map of string → [BookCharacterVoice](#schema-bookcharactervoice) | yes | Saved voice choice per narration provider, keyed by `system`, `gemini` or `breeze`. A missing provider means Default (Breeze: the library default voice; Gemini: Kore; device: the system voice). Library references are shown as references, not resolved. Choices saved by older versions in single-provider fields are included here. |
| `former_names` | list of string \| null |  | Names replaced by a manual rename. Discovery still resolves them to this character; they are not aliases. |
| `profile_refined` | boolean \| null |  | True once a profile refinement produced the description and direction. |
| `profile_provider` | string \| null |  | Provider of the refined profile. |
| `profile_model` | string \| null |  | Model of the refined profile. |
| `profile_priority` | `"deep"` \| `"standard"` \| `"basic"` \| null |  | Effort tier of the refinement, from the free census: `deep`, `standard` or `basic`. |
| `profile_state` | `"reviewed"` \| `"current"` \| `"stale"` \| `"draft"` \| null |  | Classic progressive analysis: `reviewed` (edited by hand), `current` (refined against current evidence), `stale` (refined, evidence changed since), `draft` (not refined). |
| `profile_provisional` | boolean \| null |  | Classic progressive analysis: true while the profile may still change. |

<a id="schema-bookcharactervoice"></a>
### BookCharacterVoice

A character's saved voice choice for one narration provider.

One of these forms: `{library}` follows a library voice's current version;
`{id}` is a direct provider voice (Gemini built-in or project voice, or a
device voice); `{id, revision, seed, settings?}` is a concrete Breeze pin.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `library` | string \| null |  | Library voice ID (`vl_` + 16 hex) whose current version is used. |
| `id` | string \| null |  | Direct provider voice ID. |
| `revision` | string \| null |  | Breeze pin: the voice revision from the last Breeze check. |
| `seed` | integer \| null |  | Breeze pin: default take seed (0–4294967295). |
| `settings` | [BookBreezeSettings](#schema-bookbreezesettings) \| null |  | Breeze pin: sampling overrides. |

<a id="schema-bookcover"></a>
### BookCover

Metadata of the book's cover thumbnail. The image bytes are served by `GET /api/books/{book_id}/cover`.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `media_type` | `"image/jpeg"` | yes | Media type of the stored thumbnail; always JPEG. |
| `width` | integer | yes | Thumbnail width in pixels (at most 240). |
| `height` | integer | yes | Thumbnail height in pixels (at most 360). |
| `sha256` | string | yes | Lowercase hex SHA-256 of the thumbnail bytes; changes when the cover changes. |
| `source` | `"epub"` | yes | Where the cover came from: the EPUB's own cover metadata. |

<a id="schema-booklogicalsection"></a>
### BookLogicalSection

A navigation (table of contents) entry inside one EPUB spine document.

Metadata only: logical sections are not separately scheduled or
analyzed. Offsets are into the containing chapter's `text`.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `title` | string | yes | Label from the EPUB navigation document (at most 300 characters). |
| `start` | integer | yes | Zero-based Unicode code-point offset into the chapter text where the section begins. |
| `end` | integer | yes | Exclusive end offset (code points): the next entry's start or the chapter length. |
| `kind` | `"chapter"` \| `"section"` \| `"front_matter"` \| `"back_matter"` \| `"recap"` | yes | Classification from the label and EPUB semantics. |
| `depth` | integer | yes | Nesting depth in the table of contents; 0 is top level. |
| `title_source` | `"epub_nav"` \| `"epub_ncx"` | yes | Navigation format the entry came from. |

<a id="schema-bookmetadatarequest"></a>
### BookMetadataRequest

New display metadata for a book. Both values are whitespace-normalized; control characters other than tab and newline are rejected with 400.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `title` | string | yes | Display title, 1–500 characters. Required. 400 if it is empty after whitespace normalization. (min length `1`; max length `500`) |
| `author` | string |  | Display author, at most 500 characters. Defaults to an empty string (unknown author) when omitted. (max length `500`; default `""`) |

<a id="schema-bookpassage"></a>
### BookPassage

A passage ("segment"): the reader and narration unit, anchored to exact source offsets.

`chapter.text[start:end] == text`, counting Unicode code points.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Opaque passage ID. |
| `chapter_id` | string | yes | Chapter containing the passage. |
| `scene_id` | string | yes | Scene containing the passage. |
| `start` | integer | yes | Zero-based Unicode code-point offset of the first character in the chapter text. |
| `end` | integer | yes | Exclusive end offset in code points. |
| `text` | string | yes | Exact source text of the passage (immutable). |
| `kind` | `"narration"` \| `"dialogue"` | yes | Whether the passage is quoted speech or narration. |
| `speaker_id` | string | yes | Character ID of the voice for this passage. Narration uses `narrator`; unattributed dialogue uses `unassigned`. |
| `confidence` | number | yes | Attribution confidence from 0 to 1. Analysis leaves dialogue below 0.65 unassigned; a reviewed speaker assignment is 1.0. |
| `direction` | string | yes | Performance direction for this passage (empty when none). |
| `cues` | list of string | yes | Short performance cue labels, for example `quiet` or `urgent`. |
| `evidence` | list of string \| null |  | Exact source quotations that justify the attribution (copied from the source, never model paraphrase). Absent until analysis sets it. |
| `seed` | integer \| null |  | Take seed for seeded providers (Breeze), 0–4294967295, set by a passage edit; absent when not set, in which case the speaker's Breeze voice seed applies. A new seed means a new take. Ignored by Gemini and device narration. |
| `speaker_check` | [BookSpeakerCheck](#schema-bookspeakercheck) \| null |  | BookNLP comparison; absent when not checked. |
| `analysis_provider` | string \| null |  | Provider whose annotation is current for this passage (`local`, an LLM provider, `novel_analyzer` or `booknlp`). Absent before analysis; kept from the last analysis after a manual edit. |
| `analysis_model` | string \| null |  | Model of that annotation; null for local or service providers. |
| `audio` | [BookTake](#schema-booktake) \| null | yes | Presented: the selected enhanced take if still valid, else null. |
| `leading_text` | string | yes | Presented only: chapter text between the previous passage (or the chapter start) and this passage, usually whitespace or a replaced scene-break ornament. |

<a id="schema-bookpronunciation"></a>
### BookPronunciation

A book pronunciation: how narrators should say a word.

Only the text sent to the narrator changes; chapter and passage text,
offsets, search and analysis are untouched. Matching is whole-word (a
letter, digit or underscore on either side prevents a match, so `Will`
does not match `Willow`), longest term first, and case-sensitive unless
`match_case` is false. A space in a term matches any whitespace, including
a line break; straight and curly apostrophes are interchangeable; and a
term matches text stored in either composed or decomposed Unicode form.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Entry ID assigned by the server: `pr_` followed by 12 lowercase hex digits. |
| `term` | string | yes | The word or phrase as written in the book: at most 80 characters, whitespace collapsed, with at least one letter or digit. |
| `respelling` | string | yes | How to say it, for example `Kaylor` for `Cthaelor`: at most 120 characters. Control characters, brackets, parentheses, braces and backslashes are refused, because narrators perform `(laugh)`, `<sigh>` and `[[…]]` instead of reading them. |
| `match_case` | boolean | yes | True: match the term's exact case. False: match any case. Two case-sensitive entries may differ only in case; otherwise a term appears once per book. |
| `providers` | map of string → string \| null |  | Per-narrator overrides of `respelling`, keyed by `system`, `gemini` or `breeze`; absent when there are none. An override equal to the term leaves that narrator reading the word unchanged. |
| `character_id` | string \| null |  | Book-local character the word belongs to (informational); absent when none. It had to be in the cast when the entry was added or changed; a link left by a character that analysis later removed stays until the entry is edited. |
| `note` | string \| null |  | Free-text note, at most 500 characters; absent when empty. |

<a id="schema-bookscene"></a>
### BookScene

A scene: a run of consecutive passages within one chapter, with performance notes.

Scenes start from scene-break ornaments at import and may be split by
analysis. A speaker listed in `character_ids` is attributed dialogue,
not proof that the character is physically present.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Opaque scene ID. |
| `chapter_id` | string | yes | The chapter containing every passage of this scene. |
| `title` | string \| null |  | Display title, for example "Chapter One · Scene 2". Rarely absent after a pipeline acceptance that proposed none. |
| `summary` | string \| null |  | Scene summary (draft or reviewed). Editable; at most 4,000 characters by hand. |
| `tone` | string \| null |  | Emotional tone notes; `Unreviewed` at import. Used in enhanced narration recipes. |
| `direction` | string \| null |  | Performance direction for the whole scene. Used in enhanced narration recipes. |
| `segment_ids` | list of string | yes | IDs of the scene's passages, in reading order. |
| `character_ids` | list of string | yes | IDs of characters attributed to its passages (including `narrator`/`unassigned`). |

<a id="schema-bookseries"></a>
### BookSeries

A book's series placement, its identity links and the series identities it can link to.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `membership` | [SeriesMembership](#schema-seriesmembership) \| null | yes | Null when the book is in no series, or when the book or its series is removed. |
| `series` | [Series](#schema-series) \| null | yes | The full series entry, or null when `membership` is null. |
| `links` | list of [SeriesCharacterLinkState](#schema-seriescharacterlinkstate) | yes | All retained identity links for this book's characters, ordered by character ID. Links survive while the book is removed, so this can be non-empty when `membership` is null. |
| `characters` | list of [SeriesCharacter](#schema-seriescharacter) | yes | The series' identities (with all their links), or empty when `membership` is null. |

<a id="schema-bookseriescontext"></a>
### BookSeriesContext

Bounded, source-validated knowledge from strictly earlier volumes, as analysis would receive it.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `series` | [SeriesContextSeries](#schema-seriescontextseries) \| null | yes | Null when the book is in no active series. |
| `membership` | [SeriesMembership](#schema-seriesmembership) \| null | yes | Null when the book is in no active series. |
| `characters` | list of [SeriesContextCharacter](#schema-seriescontextcharacter) | yes | Linked characters that received at least one observation. Characters are filled round-robin so one major character cannot use the whole allowance. |
| `available_observations` | integer | yes | Valid observations found before the per-character cap and the size bound. |
| `included_observations` | integer | yes | Observations included in `characters`. |
| `truncated` | boolean | yes | True when `included_observations` < `available_observations`. |
| `context_chars` | integer | yes | Length in characters of the JSON-serialized `characters` array as analysis receives it; at most 12,000 with the current server bound. Analysis also receives validation bookkeeping that this response omits, so the returned array serializes slightly shorter. |
| `fingerprint` | string | yes | Stable SHA-256 hex digest of the dependencies (membership, links, earlier sources) and the included context. It changes when anything that would change the context changes; it does not use timestamps. |

<a id="schema-bookspeakercheck"></a>
### BookSpeakerCheck

Comparison of a dialogue passage's speaker with the accepted BookNLP quote attribution.

Present only on dialogue passages the check covered; dropped when a
person changes the speaker. BookNLP is not trusted over the proposed
speaker; the check only adjusts confidence and records disagreement.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `source` | `"booknlp"` | yes | The checking method. |
| `result` | `"agrees"` \| `"differs"` \| `"suggests"` \| `"not_in_cast"` \| `"narrator"` \| `"no_quote"` | yes | `agrees`/`differs`: BookNLP named the same/another cast member. `suggests`: the passage is unassigned and BookNLP names someone. `not_in_cast`: BookNLP's speaker matches no cast member. `narrator`: BookNLP heard a first-person narrator not in the cast. `no_quote`: BookNLP found no quotation for this passage. |
| `speaker_id` | string \| null |  | Cast character ID BookNLP attributed, or null. |
| `speaker` | string \| null |  | BookNLP's own name for the speaker, or null. |
| `tag_conflict` | boolean \| null |  | True when BookNLP's own speech tag contradicts its speaker; then the comparison is only recorded. |

<a id="schema-booktake"></a>
### BookTake

The selected enhanced (cast) narration take of a passage.

Presented only when it is still valid: its recipe fingerprint matches the
passage's current speaker voice, directions, scene notes, provider and
model, and its WAV file exists. Otherwise the passage's `audio` is null.
Like every audio object, it has the common audio core, always present: `url`, `asset_id`, `duration`, `provider`, `model`, `voice` and `created_at`.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `url` | string | yes | Root-relative playback URL (`/api/audio/{book_id}/{segment_id}?v=…`; `audio/wav`). The `v` query changes when the selected audio changes, so the URL is safe to cache and compare. |
| `asset_id` | string \| null | yes | Hex SHA-256 of the WAV bytes (content address), also usable with `GET /api/books/{book_id}/audio-assets/{asset_id}`. Null for takes made before content addressing. |
| `duration` | number \| null | yes | Audio duration in seconds, measured from the WAV; null only for a take stored without it. |
| `provider` | string | yes | Narration provider: `system` (device), `gemini` or `breeze`. |
| `model` | string \| null | yes | Speech model ID (`macos-say` for device narration). |
| `voice` | string \| null | yes | Concrete provider voice that performed the take, or null when not recorded. |
| `created_at` | string \| null | yes | Always null for Studio takes: their retention time is not recorded on the take. |
| `voice_library` | [AudioTakeVoiceLibrary](#schema-audiotakevoicelibrary) \| null |  | Library voice that was followed, when the character used one; absent otherwise. |
| `voice_revision` | string \| null |  | Breeze only: the pinned server revision of the voice. |
| `provider_timing` | [AudioTakeSentenceTiming](#schema-audiotakesentencetiming) \| null |  | Breeze only: sentence timing, or null when the server's timing was not usable. |
| `breeze` | [AudioTakeBreezeInfo](#schema-audiotakebreezeinfo) \| null |  | Breeze only: request details. |

<a id="schema-breezevoicecloned"></a>
### BreezeVoiceCloned

Result of cloning a Breeze voice from a recording.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `voice` | [LibraryVoice](#schema-libraryvoice) | yes | The new cloned library voice. |
| `book` | [Book](#schema-book) \| null |  | The full book document after assignment; present only when `book_id` and `character_id` were given and the assignment succeeded. |
| `assignment_error` | string \| null |  | Present when the assignment failed. The voice is still created; nothing is rolled back. |

<a id="schema-censuschapter"></a>
### CensusChapter

Local census statistics for one chapter (section) of the book.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Chapter ID. |
| `title` | string | yes | Chapter title as stored in the book. |
| `kind` | string | yes | Section type: `chapter`, `recap`, `section`, `front_matter` or `back_matter` (older books may use other values; `section` when unrecorded). |
| `eligible` | boolean | yes | False for front and back matter, which cloud discovery and direction skip. |
| `words` | integer | yes | Whitespace-separated word count of the chapter text. |
| `estimated_tokens` | integer | yes | Rough token estimate (UTF-8 bytes / 3), for sizing only. |
| `passages` | integer | yes | Number of passages (segments) in the chapter. |
| `dialogue_turns` | integer | yes | Number of dialogue passages. |
| `unassigned_dialogue` | integer | yes | Dialogue passages whose speaker is `unassigned`. |

<a id="schema-censuscharacter"></a>
### CensusCharacter

Heuristic effort signals for one known or candidate character.

These count names and speech tags; they do not prove identity, presence
or narrative importance.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Book-local character ID for a known character, or `candidate_<hash>` for a name found only in speech tags. |
| `name` | string | yes | Cast name for a known character; for a candidate, the name as it appeared in the first matching speech tag. |
| `aliases` | list of string | yes | Cast aliases of a known character (possibly empty); always empty for a speech-tag candidate. |
| `known_character` | boolean | yes | True for a character already in the book cast; false for a speech-tag candidate. |
| `mentions` | integer | yes | Whole-word name/alias matches in eligible chapters (only unambiguous names are counted). |
| `explicit_speech_tags` | integer | yes | Speech tags such as `said Anna` naming this character. |
| `dialogue_turns` | integer | yes | Dialogue passages currently attributed to this character. |
| `dialogue_words` | integer | yes | Words in those dialogue passages. |
| `chapter_count` | integer | yes | Eligible chapters with at least one mention. |
| `chapter_mentions` | map of string → integer | yes | Mention count by chapter ID (chapters with none are omitted). |
| `uncertain_attributions` | integer | yes | Attributed dialogue passages with missing or below-0.8 confidence. |
| `ambiguous_aliases` | integer | yes | Names/aliases shared with another character. |
| `priority_score` | integer | yes | Heuristic score combining mentions, tags, dialogue and spread. Higher means more effort. |
| `priority` | `"deep"` \| `"standard"` \| `"basic"` | yes | Effort tier derived from the score and ambiguity. |
| `recommended_evidence_limit` | integer | yes | Evidence quotations a profile request uses for this tier (16, 10 or 5). |

<a id="schema-chapterlistencalibration"></a>
### ChapterListenCalibration

Speech rate and speed learned from this narrator session's finished chunks (carried across jobs).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `samples` | list of [ChapterListenCalibrationSample](#schema-chapterlistencalibrationsample) | yes | Up to 12 most recent measurements. |
| `truncated_chars_per_second` | number \| null | yes | Rate ceiling learned from a truncated response, or null. |
| `max_chars` | integer \| null | yes | Absolute chunk size ceiling learned from truncation, or null. |
| `chars_per_second` | number | yes | Expected code points per audio second (prior 14). |
| `chars_per_second_low` | number | yes | Conservative rate used to stay under the provider audio cap. |
| `realtime_factor` | number | yes | Audio seconds produced per wall-clock second (prior 2). |

<a id="schema-chapterlistencalibrationsample"></a>
### ChapterListenCalibrationSample

A finished chunk measurement.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `chars` | integer | yes | Code points of chapter text sent in the measured chunk. |
| `duration` | number | yes | Audio seconds. |
| `latency` | number | yes | Request seconds; 0 when unknown. |

<a id="schema-chapterlistenchunkplan"></a>
### ChapterListenChunkPlan

One planned chunk request, in request order.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `first_segment_id` | string | yes | Passage ID of the first passage in the chunk (chapter reading order). |
| `last_segment_id` | string | yes | Passage ID of the last passage in the chunk, inclusive; equals `first_segment_id` for a one-passage chunk. |
| `segment_count` | integer | yes | Consecutive passages in the chunk. |
| `chars` | integer | yes | Code points of the exact chapter slice sent. |
| `target_seconds` | number | yes | Audio length this step aimed for, seconds. |
| `expected_seconds` | number | yes | Audio length expected from the calibrated speech rate, seconds. |

<a id="schema-chapterlistenchunking"></a>
### ChapterListenChunking

Effective chunk options after merging the request over the saved `listen_chunking` preference.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `ramp_seconds` | list of number | yes | Target lengths for the first requests, seconds; empty means every request is full size. |
| `target_seconds` | number | yes | Full-size chunk target, seconds (30-470). |
| `concurrency` | integer | yes | Requests kept in flight at once (1-3). |

<a id="schema-chapterlistenlimits"></a>
### ChapterListenLimits

Configured Gemini speech limits for the model (Settings `tts_limits`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `rpm` | integer | yes | Requests per minute. |
| `tpm` | integer | yes | Input tokens per minute. |
| `rpd` | integer | yes | Requests per day (Pacific quota day). |

<a id="schema-chapterlistenplan"></a>
### ChapterListenPlan

Local projection of a chapter job starting at a passage. Nothing is queued or sent.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `session` | [ListeningSession](#schema-listeningsession) | yes |  |
| `chapter_id` | string | yes | Chapter containing the passage. |
| `chunks` | list of [ChapterListenChunkPlan](#schema-chapterlistenchunkplan) | yes | Remaining chunks in request order (listener position first, then earlier scope). |
| `requests_needed` | integer | yes | Number of provider requests the plan needs (`len(chunks)`). |
| `expected_seconds` | number | yes | Expected audio seconds still to generate. |
| `ready_seconds` | number | yes | Seconds of audio already available from the passage to the chapter end. |
| `passages_total` | integer | yes | Passages from the selected passage to the chapter end. |
| `passages_ready` | integer | yes | Of those, passages that already have audio. |
| `chunking` | [ChapterListenChunking](#schema-chapterlistenchunking) | yes |  |
| `calibration` | [ChapterListenCalibration](#schema-chapterlistencalibration) | yes |  |
| `limits` | [ChapterListenLimits](#schema-chapterlistenlimits) | yes |  |
| `quota` | [ChapterListenQuota](#schema-chapterlistenquota) | yes |  |

<a id="schema-chapterlistenquota"></a>
### ChapterListenQuota

This library's daily request count for the model. A lower bound: other apps sharing the project are not seen.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `requests_today` | integer | yes | Gemini speech requests recorded by this library since midnight Pacific. |
| `rpd` | integer | yes | Configured requests per day. |
| `resets_at` | string | yes | ISO 8601 UTC time of the next midnight Pacific reset. |
| `scope` | `"this library"` | yes | What `requests_today` counts. |

<a id="schema-chapterlistenrequest"></a>
### ChapterListenRequest

Gemini chapter listening from a passage (also the body of the chapter preview).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | `"gemini"` |  | Must be `gemini` (the default). Breeze and device voices use `POST /listen` per passage. (default `"gemini"`) |
| `voice` | string \| null |  | Gemini voice: a voice name or custom `voice_…` ID, `"library:vl_…"`, or empty/null for `Kore`. At most 256 characters. |
| `model` | string \| null |  | Gemini TTS model; omit or null for the configured one. At most 200 characters. |
| `segment_id` | string | yes | Required. The passage to start from (the listener position). At most 200 characters. (max length `200`) |
| `intent` | `"play"` \| `"queue"` |  | `play` (a listener is waiting; the first requests follow the short ramp) or `queue` (default; full-size chunks unless `chunking.ramp_seconds` is given). (default `"queue"`) |
| `chunking` | [ChunkingOptions](#schema-chunkingoptions) \| null |  | Optional per-request override of the saved `listen_chunking` preference, field by field. |

<a id="schema-chapterlistenstarted"></a>
### ChapterListenStarted

A started or joined `listen_chapter` job.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `session` | [ListeningSession](#schema-listeningsession) | yes |  |
| `job` | [Job](#schema-job) | yes | The `listen_chapter` job. Poll it for chunk progress; passage audio appears in `/listen/takes`. |
| `joined` | boolean | yes | True when the request joined an already active job for the same session and chapter. |

<a id="schema-characteredit"></a>
### CharacterEdit

Character fields to change (edit) or set (create). Omitted or null fields are ignored; send "" or [] to clear. Creation requires a nonempty `name` even though this DTO marks it optional.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string \| null |  | Display name, 1–100 characters. A changed name is remembered in `former_names`. |
| `aliases` | list of string \| null |  | Complete replacement list of other names for the character. |
| `description` | string \| null |  | Voice and personality profile, at most 3,000 characters. |
| `voices` | map of string → [VoiceChoice](#schema-voicechoice) \| null \| null |  | Per-provider voice choices: `{provider: VoiceChoice \| null}` where provider is `system`, `gemini` or `breeze` (another key is 400 `voice_provider_unknown`). Only the providers present change; `null`, or a choice whose `id` is empty or blank, removes that provider's choice, which means Default. This rule is the same for every provider. The stored map is returned in `characters[].voices` (library references stay references). |
| `voice` | string \| null |  | Compatibility alias for the Gemini choice: a voice ID (at most 200 characters) is treated as `voices.gemini = {id}`, with the same rules (an empty or blank string removes it). Ignored for Gemini when `voices` also names `gemini`. |
| `system_voice` | string \| null |  | Compatibility alias for the device (`system`) choice, with the same rules as `voice`. |
| `direction` | string \| null |  | Standing performance direction, at most 3,000 characters. |

<a id="schema-characterreference"></a>
### CharacterReference

One source-anchored reference to a character in the current book.

`chapter.text[start:end] == quote`. A mention is an explicit textual
reference, not proof that the character is present in the scene.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Stable hex ID derived from character, chapter, offsets and kind. |
| `character_id` | string | yes | The referenced character. |
| `chapter_id` | string | yes | Chapter whose text the offsets index. |
| `segment_id` | string \| null | yes | The first passage overlapping the span, or null when none does. |
| `start` | integer | yes | Zero-based Unicode code-point offset into the chapter text. |
| `end` | integer | yes | Exclusive end offset in code points. |
| `quote` | string | yes | The exact source text of the span. |
| `kind` | `"dialogue"` \| `"mention"` \| `"profile_evidence"` | yes | `dialogue`: a dialogue passage currently attributed to the character. `mention`: the character's name or an alias, unique within the cast, occurs in the text. `profile_evidence`: a quotation a discovery request cited as evidence. |
| `confidence` | number \| null |  | Attribution confidence for `dialogue` (0–1); null otherwise. |
| `provider` | string \| null |  | Who produced it: the analysis provider of the passage's attribution or of the evidence, `local` for mentions, or `reviewed` for a dialogue attribution a person set or confirmed; null when unknown. Evidence retained from older versions may omit `confidence`, `provider` and `model`. |
| `model` | string \| null |  | Model that produced it, or null. |
| `profile_description` | string \| null |  | `profile_evidence` only: the description proposed with this evidence. |
| `profile_direction` | string \| null |  | `profile_evidence` only: the direction proposed with this evidence. |

<a id="schema-chunkingoptions"></a>
### ChunkingOptions

Chunk sizing for chapter listening. Omitted or null fields use the saved preference (defaults: ramp `[30, 60]`, target 420, concurrency 2).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `ramp_seconds` | list of number \| null |  | Target audio seconds for the first requests, in order; at most six steps, each 10-470. `[]` means every request is full size. |
| `target_seconds` | number \| null |  | Full-size chunk target in audio seconds, 30-470. |
| `concurrency` | integer \| null |  | Requests in flight at once, 1-3 (1 until a full-size chunk has been measured). |

<a id="schema-clonebreezevoiceform"></a>
### CloneBreezeVoiceForm

Multipart form for cloning a Breeze voice from a recording.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | yes | Voice name, up to 100 characters; must not be blank. Trimmed. (max length `100`) |
| `reference_text` | string | yes | The exact words spoken in the recording, up to 2,000 characters; must not be blank. (max length `2000`) |
| `consent` | string | yes | Must be exactly `true`, confirming you have the speaker's consent to clone the voice. |
| `description` | string |  | Optional description, up to 1,000 characters. Default empty. (max length `1000`; default `""`) |
| `book_id` | string \| null |  | Optional book of a character to record as the source and assign the voice to. Used only together with `character_id`. |
| `character_id` | string \| null |  | Optional book-local character ID. Used only together with `book_id`. |
| `reference_audio` | string | yes | The recording (a common audio format, at most 20 MB; the server accepts 1–30 seconds). |

<a id="schema-currentversion"></a>
### CurrentVersion

The version to make current.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `version` | integer | yes | An existing version number of the voice (from 1). (≥ `1.0`) |

<a id="schema-decisionrequest"></a>
### DecisionRequest

Which scopes of a version to preview, accept or reject.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `scopes` | list of string \| null |  | Scope IDs of the version to act on (at most 5000). Omit or null for every scope of the version. An empty list (400 `scopes_empty`), or a scope the version does not contain (400 `unknown_scope`), is refused. |
| `expected_revision` | integer \| null |  | Accept only: the `revision` returned by preview. The accept is refused (409 `plan_stale`) when the book revision differs. Ignored by preview and reject. |

<a id="schema-defaultvoice"></a>
### DefaultVoice

The new default voice for a provider. Only Breeze has a default.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | `"breeze"` | yes | Must be `breeze`. |
| `voice_id` | string | yes | A live Breeze library voice (`vl_…`). (max length `40`) |

<a id="schema-diagnosticevent"></a>
### DiagnosticEvent

One stored diagnostic event. Only the fields that were sent (or set by the server) are present.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Event ID (32 hex). |
| `created_at` | string | yes | When the event was stored: ISO 8601 UTC timestamp with offset, for example `2026-09-28T17:04:05.123456+00:00`. |
| `source` | `"client"` \| `"server"` | yes | `client` events come from `POST /api/diagnostics`; `server` events from job workers. |
| `event` | `"listen_request_failed"` \| `"listen_poll_failed"` \| `"listen_job_failed"` \| `"buffer_failed"` \| `"cache_read_failed"` \| `"playback_media_error"` \| `"playback_play_rejected"` \| `"playback_waiting"` \| `"playback_resumed"` \| `"preview_failed"` \| `"listen_job_stopped"` \| `"listen_submit_failed"` \| `"voice_preview_failed"` \| `"voice_preview_stopped"` \| `"voice_preview_submit_failed"` | yes | Event code. Client codes are those accepted by `POST /api/diagnostics`; server codes are `listen_job_failed`, `listen_job_stopped`, `listen_submit_failed`, `voice_preview_failed`, `voice_preview_stopped` and `voice_preview_submit_failed`. |
| `book_id` | string \| null |  | Book ID. |
| `segment_id` | string \| null |  | Passage ID. |
| `session_id` | string \| null |  | Listening session ID. |
| `job_id` | string \| null |  | Job ID. |
| `playback_rate` | number \| null |  | Playback rate (0.1–8). |
| `http_status` | integer \| null |  | HTTP status the client saw (100–599). |
| `media_error_code` | integer \| null |  | HTML media error code (1–4). |
| `operation` | `"request"` \| `"poll"` \| `"play"` \| `"prefetch"` \| `"media"` \| `"prepare"` \| `"settle"` \| `"cache_read"` \| `"worker"` \| `"submit"` \| null |  | What was happening. `worker` and `submit` are server-only. |
| `status` | `"failed"` \| `"cancelled"` \| `"interrupted"` \| null |  | Server events: the job outcome. |
| `provider` | `"gemini"` \| `"system"` \| `"breeze"` \| null |  | Server events: the narration provider. |

<a id="schema-diagnosticevents"></a>
### DiagnosticEvents

Stored diagnostic events, newest first.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `events` | list of [DiagnosticEvent](#schema-diagnosticevent) | yes | Stored events, newest first: at most `limit` (default 100), and only those for `book_id` when that filter is given. Empty when none match. |
| `retention_limit` | integer | yes | Maximum events kept; older events are pruned (5000). |

<a id="schema-diagnosticrecordresult"></a>
### DiagnosticRecordResult

Whether a diagnostic event was stored. `recorded: false` is not an error; do not retry.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `recorded` | boolean | yes | True when a new event was stored; false when it was skipped (see `reason`). |
| `id` | string \| null |  | The stored event ID (32 hex). With `reason: duplicate`, the ID of the identical earlier event. |
| `reason` | `"duplicate"` \| `"rate_limited"` \| `"unavailable"` \| null |  | Why nothing was stored: `duplicate` (an identical event within 2 seconds), `rate_limited` (120 client events in the last minute), `unavailable` (storage failed). |

<a id="schema-diagnosticrequest"></a>
### DiagnosticRequest

One allowlisted diagnostic event. Unknown fields, including free-form text, are refused (422). `segment_id`, `session_id` and `job_id` each require `book_id` (`dependentRequired`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `event` | `"listen_request_failed"` \| `"listen_poll_failed"` \| `"listen_job_failed"` \| `"buffer_failed"` \| `"cache_read_failed"` \| `"playback_media_error"` \| `"playback_play_rejected"` \| `"playback_waiting"` \| `"playback_resumed"` \| `"preview_failed"` | yes | Client event code: `listen_request_failed`, `listen_poll_failed`, `listen_job_failed`, `buffer_failed`, `cache_read_failed`, `playback_media_error`, `playback_play_rejected`, `playback_waiting`, `playback_resumed` or `preview_failed`. |
| `book_id` | string \| null |  | Book UUID (lowercase hex with hyphens). Required when `segment_id`, `session_id` or `job_id` is sent. |
| `segment_id` | string \| null |  | Passage ID: `segment_` or `p_` followed by 12–32 lowercase hex characters. Requires `book_id`. |
| `session_id` | string \| null |  | Listening session ID: 64 lowercase hex characters. Requires `book_id`. |
| `job_id` | string \| null |  | Job ID: 32 lowercase hex characters. Requires `book_id`. |
| `playback_rate` | number \| null |  | Playback rate, a finite JSON number from 0.1 to 8 (strict: no strings or booleans). |
| `http_status` | integer \| null |  | HTTP status the client received, an integer 100–599 (strict). |
| `media_error_code` | integer \| null |  | HTML media error code, an integer 1–4 (strict). |
| `operation` | `"request"` \| `"poll"` \| `"play"` \| `"prefetch"` \| `"media"` \| `"prepare"` \| `"settle"` \| `"cache_read"` \| null |  | What the client was doing: `request`, `poll`, `play`, `prefetch`, `media`, `prepare`, `settle` or `cache_read`. |

<a id="schema-draftcreate"></a>
### DraftCreate

Start a voice design draft. See the operation description for how fields are filled in.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | `"breeze"` \| `"gemini"` | yes | `breeze` (free previews on the self-hosted server) or `gemini` (billed stored voices). |
| `base_voice_id` | string \| null |  | Library voice (`vl_…`) of the same provider to iterate on; fills unset fields from its current version and allows saving as a new version of it. |
| `book_id` | string \| null |  | Book of the character to design for. Used only together with `character_id`. |
| `character_id` | string \| null |  | Book-local character ID to design for. Used only together with `book_id`. |
| `name` | string \| null |  | Working name (up to 100 characters). Default: filled from the character or base voice, else empty. |
| `description` | string \| null |  | Voice description (up to 1,000 characters). Default: filled from the character or base voice, else empty. |
| `sample_text` | string \| null |  | Text Breeze previews speak (up to 1,000 characters). Default: filled from the character or base voice; else the demo text for Breeze and empty for Gemini. |

<a id="schema-draftedit"></a>
### DraftEdit

Changes to an open draft. Omitted or null fields are unchanged; values are trimmed.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string \| null |  | Working name, up to 100 characters. |
| `description` | string \| null |  | Voice description, up to 1,000 characters. |
| `sample_text` | string \| null |  | Text Breeze previews speak, up to 1,000 characters. |

<a id="schema-error"></a>
### Error

Error body for every non-2xx JSON response.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `detail` | string \| list of [ValidationIssue](#schema-validationissue) | yes | A human-readable English sentence, or for 422 request validation a list of issues. Display it; do not parse it. |
| `code` | string | yes | Stable, machine-readable error code in lower snake_case, for example `book_not_found` or `job_active`. Each operation lists the codes it returns for each status; every operation can also return the global codes listed in the contract introduction. Branch on `code`, not on `detail`. Treat an unknown code like any other failure with the same status. |

<a id="schema-generaterequest"></a>
### GenerateRequest

Options for generating draft candidates. Which fields apply depends on the draft provider.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `book_id` | string \| null |  | Book whose resource ledger records the request. Required for Gemini; optional for Breeze (nothing is recorded without it). |
| `count` | integer |  | Breeze: number of previews to generate, 1–3 (default 2). Ignored for Gemini, which always creates one voice per call. (≥ `1.0`; ≤ `3.0`; default `2`) |
| `language_code` | string |  | Gemini: language tag such as `en-US` (default) or `en-GB`. Ignored for Breeze. (max length `20`; default `"en-US"`) |
| `gender` | `"female"` \| `"male"` \| `"neutral"` \| null |  | Gemini: optional `female`, `male` or `neutral` hint. Ignored for Breeze. |
| `confirm_cost` | boolean |  | Gemini: must be true, confirming this creates a billed, stored voice. Default false. Ignored for Breeze. (default `false`) |

<a id="schema-importbookform"></a>
### ImportBookForm

Multipart form data for `importBook`.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `file` | string | yes | The ebook: a DRM-free `.epub` or UTF-8 `.txt`, at most 30 MiB. The format is taken from the file name's extension, which must therefore be sent. |

<a id="schema-job"></a>
### Job

A durable background job, as stored and returned by the server.

**Lifecycle.** A job starts `queued`, becomes `running` when a worker picks
it up, and ends in exactly one terminal status:

- `completed`: the work finished.
- `failed`: an error stopped it; `error` holds the reason. Validated work
  and finished audio are kept.
- `cancelled`: stopped by a cancel request (or never started).
- `interrupted`: the server stopped or restarted while the job was queued
  or running, or (series) the collection run stopped before this book.
- `budget_limited`: an analysis request allowance or dollar budget was
  reached; saved work is kept.
- `quota_limited`: the daily Gemini speech request quota was reached;
  `resume_after` says when it resets.

**A terminal status is final.** Once a job reaches a terminal status,
its `status`, `message`, `error` and `resume_after` never change again,
whatever happens later (a worker slot coming up for a job cancelled while
queued, or the server shutting down). Other fields may still be updated
as bookkeeping. Resuming work uses the original start route and creates a
new job; old job IDs are never revived. A series child that finished
discovery and waits for earlier volumes stays `running` without doing
work.

**Kinds and their extra fields** (a field not listed for a kind is absent):

| kind | started by | extra fields |
| --- | --- | --- |
| `render` | enhanced narration | none |
| `analyze` | classic analysis, or a series run (one per book) | `provider`, `model`, `scan_model`, `phase`; standalone: `chapter_id`; series child: `series_id`, `series_run_id`, `position` |
| `pipeline` | analysis pipeline run | `run_id`, `steps`, `scheduling` |
| `series` | series processing (parent) | `series_id`, `phase`, `provider`, `model`, `scan_model`, `concurrency`, `child_job_ids`, `book_ids`, `analysis_limits`, `finished_at` |
| `listen` | simple passage listening | `session_id`, `segment_id`, `provider`, `model`, `phase`, and `audio` once ready |
| `listen_chapter` | chapter listening, or a Gemini performance (with `parent_id`) | `session_id`, `chapter_id`, `provider`, `model`, `voice`, `intent`, `scope_start_segment_id`, `focus_segment_id`, `chunking`, `speech_limits`, `ramp_restart`, `joins`, `phase`, `chunks`, `calibration`; once the worker reports: `projection`, `quota`, `waiting_seconds`, `closing` |
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

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Job ID (32 hex characters). |
| `book_id` | string | yes | The book the job works on, or `series:<series id>` for a `series` parent job. Pass this value as `book_id` to `GET /api/jobs` to list a book's or series' jobs. |
| `kind` | `"render"` \| `"analyze"` \| `"pipeline"` \| `"series"` \| `"listen"` \| `"listen_chapter"` \| `"voice_preview"` \| `"performance"` | yes | What the job does; see the table above. |
| `status` | `"queued"` \| `"running"` \| `"completed"` \| `"failed"` \| `"cancelled"` \| `"interrupted"` \| `"budget_limited"` \| `"quota_limited"` | yes | `queued` and `running` are active; every other value is terminal. See the lifecycle above. |
| `progress` | integer | yes | Units completed so far (kind-specific units). |
| `total` | integer | yes | Units planned; 0 when not yet known. |
| `message` | string | yes | Human-readable progress or outcome text. Display it; do not parse it. |
| `error` | string \| null | yes | Human-readable failure text (at most 1,200 characters, credentials redacted), or null. Usually set with `failed`; a series child that stopped also carries it with `budget_limited` or `cancelled`. |
| `created_at` | string | yes | Creation time: ISO 8601 UTC timestamp with offset, for example `2026-09-28T17:04:05.123456+00:00`. |
| `updated_at` | string | yes | Time of the last change: ISO 8601 UTC timestamp with offset, for example `2026-09-28T17:04:05.123456+00:00`. |
| `cancel_requested` | boolean | yes | True after a cancel request. A running job stops at the next safe boundary; requests already sent to a provider can still finish and be billed. |
| `provider` | `"local"` \| `"gemini"` \| `"openai"` \| `"anthropic"` \| `"system"` \| `"breeze"` \| null |  | Analysis provider (`analyze`, `series`: local, gemini, openai, anthropic) or narration provider (`listen`, `voice_preview`, `performance`: system, gemini, breeze; `listen_chapter`: gemini). |
| `model` | string \| null |  | Model snapshotted when the job was queued: the analysis model (null for local analysis) or the speech model (`macos-say` for device narration). `render` jobs do not record it. |
| `scan_model` | string \| null |  | Preprocessing (scan) model for `analyze` and `series`; null for local analysis. |
| `phase` | `"scan"` \| `"profiles"` \| `"direct"` \| `"full"` \| `"simple_listen"` \| `"chapter_listen"` \| `"voice_preview"` \| `"performance"` \| null |  | `analyze`/`series`: the classic analysis phase (a series child switches to `scan` during discovery). Narration kinds carry a fixed label: `simple_listen`, `chapter_listen`, `voice_preview`, `performance`. |
| `mode` | `"simple"` \| `"cast"` \| null |  | `performance` only: `simple` (one narrator) or `cast` (character voices). |
| `chapter_id` | string \| null |  | `analyze`: the single chapter analyzed, or null for the whole book. `listen_chapter`: the chapter. |
| `segment_id` | string \| null |  | `listen`: the passage. `voice_preview`: the source passage, or null for demo text. |
| `session_id` | string \| null |  | `listen`, `listen_chapter`: the narrator session (64 hex). |
| `audio` | [ListeningPassageAudio](#schema-listeningpassageaudio) \| [ListeningChunkClipAudio](#schema-listeningchunkclipaudio) \| [VoicePreviewAudio](#schema-voicepreviewaudio) \| null |  | The finished audio, set just before a `listen` job (a passage take or chunk clip) or a `voice_preview` job (VoicePreviewAudio) completes. Absent until then and after a failure. |
| `resume_after` | string \| null |  | `quota_limited` only: when the daily quota resets (next midnight Pacific time), as ISO 8601 UTC timestamp with offset, for example `2026-09-28T17:04:05.123456+00:00`. |
| `run_id` | string \| null |  | `pipeline`: the pipeline run this job executes. |
| `steps` | list of string \| null |  | `pipeline`: step IDs in the run, including required upstream steps. |
| `scheduling` | `"serial"` \| `"parallel"` \| null |  | `pipeline` only: `serial` (one step at a time) or `parallel` (independent steps together), as requested by the run's `mode` field. |
| `series_id` | string \| null |  | `series` parent and its `analyze` children: the series. |
| `series_run_id` | string \| null |  | `analyze` series child: the parent `series` job ID. |
| `position` | number \| null |  | `analyze` series child: the book's reading-order position in the series. |
| `book_ids` | list of string \| null |  | `series`: the books processed, in reading order (missing volumes excluded). |
| `child_job_ids` | list of string \| null |  | `series`: one `analyze` job per book, in reading order. `performance`: the `listen_chapter` jobs started so far (Gemini simple performances only; empty otherwise). |
| `concurrency` | integer \| null |  | `series`: parallel discovery workers (1–2; 1 for phases without discovery). |
| `analysis_limits` | [SeriesJobLimits](#schema-seriesjoblimits) \| null |  | `series` only: the analysis allowance the run was started with, applied to each book. |
| `finished_at` | string \| null |  | `series`: when the collection run ended (set on completion, cancellation after start, or a start failure), as ISO 8601 UTC timestamp with offset, for example `2026-09-28T17:04:05.123456+00:00`. |
| `performance_id` | string \| null |  | `performance`: the saved performance being prepared. |
| `child_job_id` | string \| null |  | `performance`: the `listen_chapter` job currently running, or null between chapters. |
| `parent_id` | string \| null |  | `listen_chapter` started by a performance: the parent `performance` job. Such a job cannot be joined by live chapter listening; cancelling the parent cancels it. |
| `preview_id` | string \| null |  | `voice_preview`: the preview ID. |
| `preview` | [VoicePreview](#schema-voicepreview) \| null |  | `voice_preview`: the preview request being rendered. |
| `voice` | string \| null |  | `listen_chapter`: the Gemini voice. |
| `intent` | `"play"` \| `"queue"` \| null |  | `listen_chapter`: `play` (someone is waiting; the first requests are short) or `queue` (prepare ahead; every request is full size). Performances use `queue`. |
| `scope_start_segment_id` | string \| null |  | `listen_chapter`: first passage of the prepared range (to the chapter end). Joining at an earlier passage moves it back. |
| `focus_segment_id` | string \| null |  | `listen_chapter`: the passage the listener is at; generation proceeds from here first. |
| `chunking` | [ChapterListenChunking](#schema-chapterlistenchunking) \| null |  | `listen_chapter`: the chunk settings in use. |
| `speech_limits` | [ChapterListenLimits](#schema-chapterlistenlimits) \| null |  | `listen_chapter` only: the Gemini speech limits snapshotted for the model when the job was queued. |
| `ramp_restart` | integer \| null |  | `listen_chapter`: times a `play` join restarted the short first-request ramp. |
| `joins` | integer \| null |  | `listen_chapter`: times another request joined this job instead of starting one. |
| `chunks` | list of [JobChapterChunk](#schema-jobchapterchunk) \| null |  | `listen_chapter`: every request sent so far, in order, with its outcome. |
| `calibration` | [ChapterListenCalibration](#schema-chapterlistencalibration) \| null |  | `listen_chapter`: speech-rate calibration, carried over from the session's previous job and updated as chunks finish. |
| `projection` | list of [ChapterListenChunkPlan](#schema-chapterlistenchunkplan) \| null |  | `listen_chapter`: the remaining requests planned from the current state; empty when stopping or done. Absent until the worker first reports. |
| `quota` | [ChapterListenQuota](#schema-chapterlistenquota) \| null |  | `listen_chapter`: daily quota use at the last report. |
| `waiting_seconds` | number \| null |  | `listen_chapter`: seconds the next send waits for the per-minute rate limit, or null when not waiting. |
| `closing` | boolean \| null |  | `listen_chapter`: true once the worker decided to finish; a new chapter request then gets 409 until the job ends. |

<a id="schema-jobchapterchunk"></a>
### JobChapterChunk

One chunk request a ``listen_chapter`` job has sent (or is sending), in send order.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `n` | integer | yes | 1-based send order within the job. |
| `first_segment_id` | string | yes | First passage in the chunk. |
| `last_segment_id` | string | yes | Last passage in the chunk. |
| `segment_count` | integer | yes | Consecutive passages in the chunk. |
| `chars` | integer | yes | Code points of the exact chapter slice sent. |
| `target_seconds` | number | yes | Audio length in seconds the chunk was sized for. |
| `expected_seconds` | number | yes | Audio length in seconds expected from the speech-rate estimate at send time. |
| `expected_latency` | number | yes | Expected seconds until the response, from calibration. |
| `realtime_factor` | number | yes | Calibration realtime factor (audio seconds per waiting second) at send time. |
| `epoch` | integer | yes | Planning generation; increases after a truncation forces smaller re-planning. |
| `status` | `"requesting"` \| `"done"` \| `"rate_limited"` \| `"truncated"` \| `"failed"` | yes | `requesting` while in flight; `done` when its audio was retained; `rate_limited` when the provider refused it with HTTP 429 (nothing generated; its passages are planned again); `truncated` when the audio was cut short or far too short and was discarded; `failed` on any other error (the job then stops). |
| `started_at` | string | yes | When the request was sent: ISO 8601 UTC timestamp with offset, for example `2026-09-28T17:04:05.123456+00:00`. |
| `finished_at` | string \| null |  | When the request finished; absent while `requesting`. ISO 8601 UTC timestamp with offset, for example `2026-09-28T17:04:05.123456+00:00`. |
| `error` | string \| null |  | Human-readable reason for `rate_limited`, `truncated` or `failed` (at most 300 characters for `failed`). |
| `duration` | number \| null |  | Seconds of audio received (`done`, `truncated`). |
| `chunk_id` | string \| null |  | ID of the retained chunk audio (`done`). |
| `latency` | number \| null |  | Measured seconds from send to response (`done`). |
| `flags` | list of `"weak_alignment"` \| null |  | Quality flags (`done`). `weak_alignment`: fewer than 60% of passage boundaries matched, so passage clip times are rough. |
| `matched` | integer \| null |  | Passage boundaries the aligner matched in the audio (`done`). |
| `boundaries` | integer \| null |  | Passage boundaries the aligner tried to match (`done`). |

<a id="schema-librarybookcover"></a>
### LibraryBookCover

Metadata of the book's saved cover thumbnail (a JPEG of at most 240 x 360 pixels).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `width` | integer | yes | Thumbnail width in pixels. |
| `height` | integer | yes | Thumbnail height in pixels. |
| `sha256` | string | yes | Lowercase hex SHA-256 of the thumbnail bytes. Changes when the cover changes. |
| `url` | string | yes | Root-relative URL of the image, `/api/books/{book_id}/cover?v={sha256}`. Because the `v` value names these exact bytes, the server lets clients cache this URL indefinitely (see `getBookCover`); a changed cover gets a new URL. |

<a id="schema-librarybookstorage"></a>
### LibraryBookStorage

Measured storage attributed to one book.

File sizes are regular-file lengths measured on disk at request time
(symbolic links are not followed). Removing a book does not reclaim any of it.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `original_bytes` | integer | yes | Bytes of the saved original upload (EPUB or TXT). 0 for the demo book, which has no original. |
| `audio_bytes` | integer | yes | Bytes of retained enhanced (cast) narration audio, including superseded takes. |
| `simple_listen_bytes` | integer | yes | Bytes of retained simple-listening audio. |
| `voice_preview_bytes` | integer | yes | Bytes of retained voice-example audio. |
| `file_bytes` | integer | yes | Sum of the four file sizes above. |
| `database_payload_bytes` | integer | yes | Exact byte length of this book's rows' bodies in the shared SQLite database (book JSON, takes, analysis checkpoints, artifacts, cover, resource records, listening and preview records, ...). It is not a disk allocation: it excludes shared pages, indexes, free space and compression. |
| `note` | string | yes | Human-readable caveat about these measurements. Display only. |

<a id="schema-librarybooksummary"></a>
### LibraryBookSummary

A library row for one book: counts, cover, membership and measured storage.

This is not the book's content. Fetch `GET /api/books/{book_id}` for the
prose projection. Counting and file measurement happen on every request.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Opaque book ID. |
| `title` | string | yes | Display title. |
| `author` | string \| null | yes | Display author; an empty string when unknown. Null only for a stored book without the field. |
| `created_at` | string \| null | yes | ISO 8601 UTC import time (with a `+00:00` offset). Null for a stored book without the field. |
| `source_name` | string \| null | yes | File name of the original upload, without any directory (for the demo, `The Last Light.txt`). Null for a stored book without the field. |
| `analysis` | [BookAnalysisSummary](#schema-bookanalysissummary) \| null | yes | Who produced the current analysis projection; null for a stored book without it. |
| `archived` | boolean | yes | True when the book is removed from normal library views (see `archiveBook`). |
| `archived_at` | string \| null | yes | ISO 8601 UTC time the book was removed, or null when it is not removed. |
| `section_count` | integer | yes | Number of stored sections (chapters plus front/back matter and other non-narrative sections). |
| `chapter_count` | integer | yes | Number of narrative chapters (sections whose kind is `chapter`). When no section records a kind (older imports), equals `section_count`. |
| `word_count` | integer | yes | Whitespace-separated tokens across all section text. |
| `character_count` | integer | yes | Cast members, excluding the two built-in entries `narrator` and `unassigned`. Not a count of text characters (see `text_character_count`). |
| `text_character_count` | integer | yes | Length of all section text in Unicode code points. |
| `segment_count` | integer | yes | Number of passages (reader units; "passage" and "segment" name the same unit). |
| `scene_count` | integer | yes | Number of scenes. |
| `audio_count` | integer | yes | Passages whose selected enhanced (cast) take is current and playable: it still matches the passage's text, speaker, resolved voice, scene direction and provider/model, and its audio file exists. Equals the number of passages with a non-null `audio` in `GET /api/books/{book_id}`. Superseded or stale takes stay stored but are not counted. |
| `membership` | [SeriesMembership](#schema-seriesmembership) \| null | yes | Series membership, or null when the book is in no series. Reported even when the book or its series is removed. |
| `cover` | [LibraryBookCover](#schema-librarybookcover) \| null | yes | Saved cover thumbnail, or null when there is none (TXT imports, the demo, EPUBs without a usable cover). |
| `storage` | [LibraryBookStorage](#schema-librarybookstorage) | yes | Measured storage attributed to this book. |

<a id="schema-librarysnapshot"></a>
### LibrarySnapshot

The library-management view: books, series and storage in one response.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `books` | list of [LibraryBookSummary](#schema-librarybooksummary) | yes | Book summaries, most recently imported first (see `listBooks` for the exact order). Removed books are included only with `include_archived=true`. |
| `series` | list of [Series](#schema-series) | yes | Series sorted by name (ignoring case), then ID. Removed series are included only with `include_archived=true`. |
| `storage` | [LibraryStorage](#schema-librarystorage) | yes | Library-wide measured storage. |

<a id="schema-librarystorage"></a>
### LibraryStorage

Library-wide measured storage.

Sizes are regular-file lengths measured at request time; symbolic links are not followed.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `data_directory_bytes` | integer | yes | All files under the data directory, including the database, media, originals and backups. |
| `database_bytes` | integer | yes | Size of the main SQLite database file. |
| `database_wal_bytes` | integer | yes | Size of the SQLite write-ahead log file (0 when absent). |
| `database_shm_bytes` | integer | yes | Size of the SQLite shared-memory file (0 when absent). |
| `shared_database_bytes` | integer | yes | Sum of the three database file sizes. The database is shared by all books. |
| `backup_bytes` | integer | yes | Size of the `backups` folder in the data directory. |
| `note` | string | yes | Human-readable caveat about these measurements. Display only. |

<a id="schema-libraryvoice"></a>
### LibraryVoice

A named library voice with its versions ("VoiceView").

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Library voice ID, `vl_` followed by 16 hex digits. |
| `provider` | `"breeze"` \| `"gemini"` | yes | The provider every version belongs to. |
| `name` | string | yes | Display name, 1–100 characters. |
| `description` | string | yes | Free-text description, up to 1,000 characters. |
| `origin` | `"designed"` \| `"cloned"` \| `"imported"` | yes | How the voice was first made: saved from a design draft, cloned from a recording, or imported from the Breeze server. |
| `current_version` | integer | yes | The version characters following this voice use now. |
| `is_default` | boolean | yes | True when this is the provider's default voice (Breeze only). |
| `deleted` | boolean | yes | True for a deleted (tombstoned) voice. `GET /api/voices` lists only live voices; single-voice responses never return a deleted one. |
| `assignable` | boolean | yes | Whether the cast can use it now: the current version's `server_state` is `ok` or `unknown`, and for Gemini the selected speech model accepts designed voices. |
| `versions` | list of [LibraryVoiceVersion](#schema-libraryvoiceversion) | yes | All versions, oldest first. |
| `usage` | list of [LibraryVoiceUsage](#schema-libraryvoiceusage) | yes | Characters (in non-archived books) that follow this voice. |
| `source` | [VoiceCharacterContext](#schema-voicecharactercontext) \| null | yes | The character the voice was designed or cloned for, or null. |
| `warnings` | list of string | yes | Human-readable problems: an unfinished deletion already removed some of its provider voices (delete it again to finish), the current version changed on the Breeze server (narration refused), is missing from the server, was made with another Google key, or the selected Gemini speech model accepts only built-in voices. |

<a id="schema-libraryvoiceaudition"></a>
### LibraryVoiceAudition

A voice version's audition clip (`GET /api/voices/{voice_id}/versions/{version}/audition`).

Always present. When Bardic retained the clip (24 kHz mono WAV), `asset_id`, `duration` and `created_at` are
set. When it did not, they are null and the URL fetches the Breeze reference clip or Gemini sample from the
provider on each request. `voice` is the version's provider voice ID. `model` is the design model for a
designed version, and null for a cloned or imported version, whose clip is a recording.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `url` | string | yes | Root-relative URL of the audio bytes (WAV unless stated otherwise). Play this; do not build audio URLs from other fields. |
| `asset_id` | string \| null | yes | SHA-256 hex of the file the URL serves (content address), or null when the bytes are not content-addressed (takes recorded before content addressing). A different `asset_id` means different audio. |
| `duration` | number \| null | yes | Length of this audio in seconds, or null when unknown. |
| `provider` | string \| null | yes | Speech provider that produced the bytes (`system`, `gemini`, `breeze`), or null when unknown. |
| `model` | string \| null | yes | Speech model that produced the bytes, or null when unknown. |
| `voice` | string \| null | yes | Provider voice actually used, or null when unknown. |
| `created_at` | string \| null | yes | ISO 8601 UTC time the audio was retained, or null when it was not recorded. |

<a id="schema-libraryvoicedeleted"></a>
### LibraryVoiceDeleted

Result of deleting a library voice.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `deleted` | string | yes | The library voice ID. |
| `server_deleted` | list of string | yes | Provider voice IDs this deletion removed on the provider, sorted, including those removed by earlier attempts that failed part-way. Empty when removed from Bardic only, or when the voice was already deleted. |

<a id="schema-libraryvoicerecipe"></a>
### LibraryVoiceRecipe

How a voice version was made. Only keys with a non-null stored value are present.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `description` | string \| null |  | The voice description (Breeze design prompt, Gemini prompted-voice input, or the clone/import description). |
| `sample_text` | string \| null |  | Breeze: the text the auditioned clip speaks (design sample text or clone transcript). Absent for Gemini. |
| `model` | string \| null |  | Gemini design model used, for example `gemini-3.8-flash-tts`. Gemini only. |
| `language_code` | string \| null |  | Gemini language tag, for example `en-US`. Gemini only. |
| `gender` | string \| null |  | Gemini gender hint (`female`, `male` or `neutral`). Gemini only; absent when none was given. |

<a id="schema-libraryvoiceusage"></a>
### LibraryVoiceUsage

One character that follows a voice.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `book_id` | string | yes | Book containing the character. Archived (removed) books are excluded. |
| `book_title` | string | yes | The book title, or "Untitled". |
| `character_id` | string | yes | Book-local character ID. |
| `character_name` | string | yes | Character name, or its ID when unnamed. |
| `follows` | `"assigned"` \| `"default"` | yes | `assigned`: the character's cast entry names this voice (a library reference, or a direct provider pin of a provider voice behind one of its versions). `default`: the character has no Breeze choice and this is the Breeze default voice. |

<a id="schema-libraryvoiceversion"></a>
### LibraryVoiceVersion

One immutable version of a library voice: one fixed provider voice.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `version` | integer | yes | Version number, starting at 1. Versions are append-only. |
| `provider_voice_id` | string | yes | The provider voice: a Breeze server voice ID (for example `bardic-1a2b3c4d`) or a Gemini `voice_…` ID. |
| `revision` | string \| null | yes | Breeze: the pinned revision, an opaque 64-hex hash of the speech-affecting server state when the version was saved. Narration is refused if the live server voice no longer matches. Null for Gemini. |
| `made` | `"designed"` \| `"cloned"` \| `"imported"` | yes | How this version was made: saved from a design draft, cloned from an uploaded recording, or imported from the Breeze server by a connection check. |
| `created_at` | string | yes | When the version was saved (ISO 8601 UTC). |
| `expires_at` | string \| null | yes | Gemini: when Google deletes the stored voice (the provider's timestamp string; stored voices live about one year). Null for Breeze or when unknown. |
| `server_state` | `"ok"` \| `"changed"` \| `"missing"` \| `"unknown"` \| `"other_project"` | yes | Result of comparing this version with the last saved provider check, computed locally in every response that returns a voice. `ok`: present (Breeze: same revision). `changed`: Breeze only, the server voice changed since it was saved. `missing`: not in the last check, or already deleted on the provider by an unfinished deletion of this voice. `unknown`: no check to compare with (Breeze never checked or checked against another URL; Gemini project voices never listed with the current key, or the last listing failed). `other_project`: Gemini only, made with a different Google API key than the current one. |
| `audition` | [LibraryVoiceAudition](#schema-libraryvoiceaudition) | yes | This version's audition clip. |
| `recipe` | [LibraryVoiceRecipe](#schema-libraryvoicerecipe) | yes | How the version was made. |

<a id="schema-limits"></a>
### Limits

Optional caps for API callers, all null (uncapped) by default. The Analysis tab sends none: the confirmed plan (`expected_fingerprint`) is the authorization, and a run with neither is refused. Every attempt is reserved and recorded either way. Setting any limit authorizes a run without a fingerprint. A limit reached stops the run as `budget_limited` and keeps completed work.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `max_requests` | integer \| null |  | HTTP attempts allowed in this run, including retries and evidence repairs (1–1000). |
| `max_input_tokens` | integer \| null |  | Input tokens allowed in this run, counting reservations for attempts without reported usage (1000–10,000,000). |
| `max_output_tokens` | integer \| null |  | Output tokens allowed in this run, counting reservations (1000–2,000,000). |
| `budget_usd` | number \| null |  | Cumulative USD guard across every tracked attempt for this book, including earlier runs (greater than 0, at most 1000). Self-hosted models are priced at 0. |

<a id="schema-listencached"></a>
### ListenCached

A passage served from retained audio; nothing was queued.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `session` | [ListeningSession](#schema-listeningsession) | yes |  |
| `audio` | [ListeningPassageAudio](#schema-listeningpassageaudio) \| [ListeningChunkClipAudio](#schema-listeningchunkclipaudio) | yes | The retained audio for the passage: a Gemini chapter chunk clip for this session when one covers it, otherwise a single-passage take (possibly reused from an identical recipe elsewhere). Play its `url`. |
| `cached` | `true` | yes | Always true: served from retained audio. No job was queued and no provider was contacted. |

<a id="schema-listenqueued"></a>
### ListenQueued

A passage that needs synthesis: a new or joined `listen` job.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `session` | [ListeningSession](#schema-listeningsession) | yes |  |
| `job` | [Job](#schema-job) | yes | The `listen` job (new, or the already active one for this session and passage). Poll it; on completion its `audio` holds the take. |
| `cached` | `false` | yes | Always false: no retained audio matched, so the passage needs synthesis via `job`. |

<a id="schema-listenrequest"></a>
### ListenRequest

One passage with one simple narrator.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | `"system"` \| `"gemini"` \| `"breeze"` |  | Narration provider: `system` (default), `gemini` or `breeze`. (default `"system"`) |
| `voice` | string \| null |  | Narrator voice: `"library:vl_…"` (that library voice's current version), a direct provider voice ID, or empty/null for Default (Gemini `Kore`, the device default voice, or the Bardic default Breeze library voice). A direct Breeze ID must be in the last Breeze voice check. At most 256 characters. |
| `model` | string \| null |  | Speech model. Omit or null for the configured Gemini TTS model; device narration accepts only `macos-say` and Breeze only `breeze-tts-2`. At most 200 characters. |
| `segment_id` | string | yes | Required. The passage (segment) to narrate. |

<a id="schema-listeningchunkclipaudio"></a>
### ListeningChunkClipAudio

One passage's estimated clip inside a multi-passage chunk WAV (Gemini chapter listening).

Play ``url`` from ``clip_start`` to ``clip_end``. Consecutive clips of one
chunk share the same file and play gaplessly. Like every audio object, it has the common audio core, always present: `url`, `asset_id`, `duration`, `provider`, `model`, `voice` and `created_at`.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `url` | string | yes | Root-relative URL of the shared chunk WAV: `/api/books/{book_id}/listen/audio/{asset_id}`. |
| `asset_id` | string | yes | SHA-256 hex of the chunk WAV. |
| `duration` | number | yes | Clip length in seconds (`clip_end - clip_start`, rounded to ms). |
| `provider` | string | yes | Speech provider that produced the chunk, for example `gemini`. |
| `model` | string | yes | Speech model that produced the chunk, for example `gemini-3.8-flash-tts`. |
| `voice` | string | yes | Provider voice actually used for the chunk. |
| `created_at` | string | yes | ISO 8601 UTC time the chunk was retained. |
| `segment_id` | string | yes | Passage this clip narrates. |
| `chunk_id` | string | yes | ID of the retained chunk. |
| `clip_start` | number | yes | Clip start within the chunk WAV, seconds. |
| `clip_end` | number | yes | Clip end within the chunk WAV, seconds. |
| `chunk_duration` | number | yes | Length of the whole chunk WAV in seconds. |
| `timing` | `"estimated"` | yes | Clip boundaries are estimated from pauses, not measured. |
| `session_id` | string | yes | Listening session ID (64 hex) the chunk belongs to. |
| `flags` | list of string | yes | Quality flags of the chunk; currently `weak_alignment` (fewer than 60% of passage boundaries matched a pause). |

<a id="schema-listeningpassageaudio"></a>
### ListeningPassageAudio

A retained single-passage simple-listening take, ready to play.

Like every audio object, it has the common audio core, always present: `url`, `asset_id`, `duration`, `provider`, `model`, `voice` and `created_at`.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `url` | string | yes | Root-relative WAV URL: `/api/books/{book_id}/listen/audio/{asset_id}`. |
| `asset_id` | string | yes | SHA-256 hex of the WAV bytes (content address). |
| `duration` | number | yes | Audio length in seconds. |
| `provider` | string | yes | Provider that produced the bytes (`system`, `gemini` or `breeze`). |
| `model` | string | yes | Speech model that produced the bytes. |
| `voice` | string | yes | Provider voice actually used (the device voice name after resolution). |
| `created_at` | string | yes | ISO 8601 UTC time the take was retained (the first retention if it was saved concurrently). |
| `session_id` | string | yes | Listening session the take belongs to. |
| `segment_id` | string | yes | Passage the take narrates. |
| `reuse` | [ListeningReuse](#schema-listeningreuse) \| null |  | Present when the bytes were copied from an equivalent retained take instead of being generated. |
| `resource_usage` | [AudioTakeUsage](#schema-audiotakeusage) \| null |  | Usage of the generating request; absent for device takes and for reused bytes. |
| `provider_timing` | [AudioTakeSentenceTiming](#schema-audiotakesentencetiming) \| null |  | Breeze only: validated sentence timing, or null when the server timing did not validate. |
| `breeze` | [AudioTakeBreezeInfo](#schema-audiotakebreezeinfo) \| null |  | Breeze only: request details. |
| `voice_revision` | string \| null |  | Breeze only: voice revision that performed the take. |

<a id="schema-listeningreuse"></a>
### ListeningReuse

Pointer to the original retained take whose bytes were reused for this passage.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `schema_version` | integer | yes | Pointer format version (1). |
| `take_id` | string | yes | ID of the original retained take row. |
| `book_id` | string | yes | Book of the original take (reuse can cross books). |
| `session_id` | string | yes | Listening session ID (64 hex) of the original take; may differ from the current session. |
| `segment_id` | string | yes | Passage ID the original take narrated, in the original take's book; may differ from this passage when equivalent text was reused. |
| `recipe` | string | yes | Source-bound recipe hash of the original take. |
| `fingerprint` | string | yes | Producer fingerprint of the original take. |

<a id="schema-listeningsession"></a>
### ListeningSession

A simple-listening narrator choice for one book.

The ID is a deterministic hash of the fields below, so the same narrator
always maps to the same session and its retained takes. A Breeze voice
changed on the server (new revision) starts a new session and keeps old takes.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Session ID: a 64-character hex hash of the narrator configuration. |
| `schema_version` | integer | yes | Session format version (1). |
| `book_id` | string | yes | Book the session belongs to. |
| `provider` | `"system"` \| `"gemini"` \| `"breeze"` | yes | Narration provider. |
| `voice` | string | yes | Resolved provider voice ID. Empty string for the device default voice; Gemini defaults to `Kore`; a `library:` choice is stored as the provider voice it resolved to. |
| `model` | string | yes | Speech model: `macos-say`, `breeze-tts-2`, or a Gemini TTS model. |
| `voice_revision` | string \| null |  | Breeze only: the pinned voice revision from the last voice check. |
| `seed` | integer \| null |  | Breeze only: the pinned generation seed. |
| `settings` | object \| null |  | Breeze only, when set: pinned speech settings for the voice (provider-defined keys). |

<a id="schema-listeningtake"></a>
### ListeningTake

The playable simple audio for one passage.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `segment_id` | string | yes | Passage (segment) ID within the book that this audio narrates. |
| `audio` | [ListeningPassageAudio](#schema-listeningpassageaudio) \| [ListeningChunkClipAudio](#schema-listeningchunkclipaudio) | yes | A chunk clip when one applies (preferred), otherwise the newest valid single-passage take. |

<a id="schema-listeningtakes"></a>
### ListeningTakes

Saved simple audio for a session.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `session` | [ListeningSession](#schema-listeningsession) | yes |  |
| `takes` | list of [ListeningTake](#schema-listeningtake) | yes | At most one entry per passage, in book passage order. Passages without matching audio are omitted. |

<a id="schema-localserviceurls"></a>
### LocalServiceUrls

Resolved root URLs of the self-hosted analysis servers; an empty string means not configured.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `local_llm` | string | yes | OpenAI-compatible Local LLM server. |
| `booknlp` | string | yes | BookNLP quote-attribution server. |
| `novel_analyzer` | string | yes | Novel Analyzer chapter-script server. |

<a id="schema-narrationprovidercapabilities"></a>
### NarrationProviderCapabilities

Static capability flags of a narration provider.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `offline` | boolean | yes | Works without a network connection. |
| `performance_direction` | boolean | yes | Speaks character and passage direction (acting notes). |
| `timing` | `"passage"` | yes | Granularity of reported timing. |
| `chunked_listening` | boolean | yes | Supports multi-passage chapter listening (Gemini only). |
| `seeded_takes` | boolean | yes | A seed makes a take repeatable; a new seed makes a new take (Breeze only). |
| `cost` | `"local"` \| `"cloud"` \| `"self_hosted"` | yes | `local`: this computer. `cloud`: billed provider requests. `self_hosted`: the owner's server, no per-request charge. |
| `custom_voice_ids` | boolean \| null |  | Gemini only: accepts voice IDs beyond the prebuilt list. |
| `speakers_per_take` | integer \| null |  | Gemini only: speakers in one request. |

<a id="schema-narrationproviderinfo"></a>
### NarrationProviderInfo

The static contract of one narration provider (availability is reported in `providers`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `"system"` \| `"gemini"` \| `"breeze"` | yes | Narration provider ID: `system` (macOS device voices), `gemini` (cloud) or `breeze` (self-hosted server). |
| `label` | string | yes | Display name. |
| `default_model` | string | yes | Speech model used when none is chosen (`macos-say`, a Gemini TTS model, or `breeze-tts-2`). |
| `models` | list of string | yes | Accepted speech models. |
| `default_voice` | string \| null | yes | Voice used when none is chosen (`Kore` for Gemini), or null. |
| `requires` | `"none"` \| `"api_key"` \| `"server"` | yes | What must be configured before use. |
| `capabilities` | [NarrationProviderCapabilities](#schema-narrationprovidercapabilities) | yes |  |
| `voices` | list of [NarrationProviderVoice](#schema-narrationprovidervoice) \| null |  | Gemini only: the prebuilt voices. |

<a id="schema-narrationprovidervoice"></a>
### NarrationProviderVoice

A prebuilt provider voice.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Voice ID to send, for example `Kore`. |
| `name` | string | yes | Display name (same as the ID). |

<a id="schema-passagesearchhit"></a>
### PassageSearchHit

One matching passage.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `book_id` | string | yes | Book containing the passage (this book or an earlier series volume). |
| `book_title` | string | yes | Title of that book. |
| `chapter_id` | string | yes | Chapter ID (within `book_id`) whose text `start`/`end` index into. |
| `chapter_title` | string | yes | Title of that chapter. |
| `passage_id` | string | yes | Passage (segment) ID of the matching passage, within `book_id`. |
| `start` | integer | yes | Code-point offset into the chapter text (inclusive). |
| `end` | integer | yes | Code-point offset (exclusive). |
| `text` | string | yes | The exact passage text. |
| `source_hash` | string | yes | SHA-256 (hex) of the chapter text encoded as UTF-8. |
| `rank` | number | yes | SQLite FTS5 rank. Lower (more negative) is a stronger lexical match; not confidence or identity. |

<a id="schema-passagesearchresult"></a>
### PassageSearchResult

Literal word search over saved passages.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `items` | list of [PassageSearchHit](#schema-passagesearchhit) | yes | Matches, strongest first. |
| `available` | boolean | yes | False when this SQLite build lacks FTS5; then there are no results. |
| `query` | string | yes | The `q` parameter as sent. |
| `scope` | `"book"` \| `"earlier"` | yes | The effective scope. |
| `note` | string | yes | Interpretation text, or why there are no results. Display only. |

<a id="schema-performance"></a>
### Performance

A saved performance: a named chapter selection plus a narrator (`simple`) or the cast (`cast`).

This is the stored record without its internal `cast_snapshot` and `pronunciation_snapshot`, which responses
never include.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Performance ID (`pf_…`). |
| `book_id` | string | yes | ID of the book the performance belongs to. |
| `schema_version` | integer | yes | Record format version (1). |
| `name` | string | yes | Display name, at most 200 characters. Defaults to the narrator label plus the scope, for example `Kore · Gemini · 3 chapters`. |
| `mode` | `"simple"` \| `"cast"` | yes | `simple`: one narrator voice for every passage. `cast`: each speaker in their cast voice, with the narrator as fallback. |
| `chapter_ids` | list of string | yes | Selected chapters in book order at creation. Chapters later removed from the book are skipped. |
| `provider` | `"system"` \| `"gemini"` \| `"breeze"` | yes | Narration provider pinned at creation. |
| `model` | string | yes | Speech model pinned at creation. |
| `voice` | string \| null | yes | Simple: the voice value as requested (may be `library:…` or empty for Default). Cast: null. |
| `pronunciation_count` | integer \| null |  | Cast performances only: how many book pronunciations were pinned when it was created. Absent when none were (including performances made before pronunciations existed) and for simple performances, which always use the book's current pronunciations. |
| `session_id` | string \| null |  | Simple only: the pinned listening session. |
| `created_at` | string | yes | ISO 8601 UTC. |
| `updated_at` | string | yes | ISO 8601 UTC; changes on rename, archive and when a job starts. |
| `archived` | boolean | yes | True when hidden from the default list (listed only with `archived=true`). Its audio is kept. |
| `job_id` | string \| null | yes | Latest job ID, or null if no job was ever needed. |
| `cast` | list of [PerformanceCastMember](#schema-performancecastmember) \| null |  | Cast only: the narrator and each speaker in the chosen chapters. |
| `job` | [Job](#schema-job) \| null | yes | The latest `performance` job (a full `Job`), or null when no job was ever needed. |
| `progress` | [PerformanceProgress](#schema-performanceprogress) | yes |  |
| `narrator_label` | string | yes | Display label such as `Kore · Gemini` or `Full cast · Device voices`. |

<a id="schema-performanceaudiomap"></a>
### PerformanceAudioMap

Playable audio of a performance.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `performance_id` | string | yes | ID of the performance (`pf_…`). |
| `audio` | map of string → [ListeningPassageAudio](#schema-listeningpassageaudio) \| [ListeningChunkClipAudio](#schema-listeningchunkclipaudio) \| [PerformanceCastAudio](#schema-performancecastaudio) | yes | Keyed by passage (segment) ID; only passages ready against their current source. Simple performances give the `/listen/takes` objects; cast performances give `PerformanceCastAudio`. |

<a id="schema-performancecastaudio"></a>
### PerformanceCastAudio

A cast performance's retained passage take.

Like every audio object, it has the common audio core, always present: `url`, `asset_id`, `duration`, `provider`, `model`, `voice` and `created_at`.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `url` | string | yes | Root-relative WAV URL: `/api/books/{book_id}/audio-assets/{id}`. |
| `asset_id` | string \| null | yes | SHA-256 hex of the WAV (content address), or null for a reused Studio take recorded before content addressing (its URL then names the file by recipe). |
| `duration` | number \| null | yes | Audio length in seconds, or null when the retained record lacks it. |
| `provider` | string \| null | yes | Narration provider that produced the take (`system`, `gemini` or `breeze`); null when the retained take metadata does not record it. |
| `model` | string \| null | yes | Speech model that produced the take (for example `macos-say`, `breeze-tts-2` or a Gemini TTS model); null when the retained take metadata does not record it. |
| `voice` | string \| null | yes | Provider voice that performed the take, or null when not recorded. |
| `created_at` | string \| null | yes | ISO 8601 UTC time the take was retained for this performance. |
| `speaker_id` | string \| null | yes | The passage's speaker (a character ID, `narrator` or `unassigned`). |
| `character_id` | string \| null | yes | Character whose voice was used (`narrator` when falling back). |
| `fallback` | boolean | yes | True when the speaker had no usable voice and the narrator voice was used. |

<a id="schema-performancecastmember"></a>
### PerformanceCastMember

Who voices a speaker in a cast performance.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `character_id` | string | yes | Speaker ID (`narrator`, `unassigned` or a book character ID). |
| `name` | string | yes | Character name from the snapshot, or the ID. |
| `voice_label` | string | yes | Display label of the voice used (library voice name, provider voice ID, `Kore` or `Default voice`). |
| `fallback` | boolean | yes | True when the speaker has no usable voice and uses the narrator. |

<a id="schema-performancechapterprogress"></a>
### PerformanceChapterProgress

Readiness of one selected chapter.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Chapter ID. |
| `title` | string | yes | Chapter title; empty string when the chapter has none. |
| `passages_total` | integer | yes | Passages in the chapter. |
| `passages_ready` | integer | yes | Of those, passages with playable audio that matches their current source text. |

<a id="schema-performanceedit"></a>
### PerformanceEdit

Label changes. Omitted or null fields are unchanged.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string \| null |  | New name, 1-200 characters; trimmed, and whitespace-only gives 400. |
| `archived` | boolean \| null |  | Hide (true) or restore (false) the performance in the default list. Audio is never deleted. |

<a id="schema-performanceenvelope"></a>
### PerformanceEnvelope

One performance.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `performance` | [Performance](#schema-performance) | yes |  |

<a id="schema-performancelist"></a>
### PerformanceList

Performances of a book, newest first.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `performances` | list of [Performance](#schema-performance) | yes | Performances of the book, newest first. Archived ones only when requested with `archived=true`. |

<a id="schema-performanceplan"></a>
### PerformancePlan

Local estimate for a performance; nothing is recorded (except the deterministic session row) or sent.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `mode` | `"simple"` \| `"cast"` | yes | The requested performance mode (see `Performance.mode`). |
| `provider` | `"system"` \| `"gemini"` \| `"breeze"` | yes | The requested narration provider. |
| `model` | string | yes | Resolved speech model. |
| `chapter_ids` | list of string | yes | Requested chapters in book order. |
| `passages_total` | integer | yes | Passages in the requested chapters. |
| `passages_ready` | integer | yes | Passages with usable audio. A new cast performance always reports 0 even when Studio or other performance takes will be reused. |
| `passages_to_generate` | integer | yes | Passages without usable audio that a job would narrate (`passages_total - passages_ready`). |
| `requests_estimate` | integer | yes | Gemini simple: planned full-size chunk requests; otherwise passages to generate. |
| `expected_seconds` | number | yes | Missing text at 14 code points per second plus ready durations. |
| `chapters` | list of [PerformanceChapterProgress](#schema-performancechapterprogress) | yes | Readiness per requested chapter, in book order. |
| `problems` | list of string | yes | Blocking conditions, as sentences that state the condition; create refuses (400, with the first problem's code) while any exist. |
| `notes` | list of string | yes | Advisory notes: voiceless characters, unassigned passages, unanalyzed chapters, reuse, daily request budget, and for a cast performance whether its pinned pronunciations differ from the book's current ones or predate them. |
| `quota` | [PerformanceQuota](#schema-performancequota) \| null | yes | Gemini only; null otherwise. |
| `narrator_label` | string | yes | Display label such as `Kore · Gemini` or `Full cast · Device voices`; also the prefix of the default name. |

<a id="schema-performanceprogress"></a>
### PerformanceProgress

Readiness against each passage's current source.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `passages_total` | integer | yes | Passages in the selected chapters that are still in the book. |
| `passages_ready` | integer | yes | Of those, passages with playable audio that matches their current source text. |
| `seconds_ready` | number | yes | Audio seconds ready. |
| `chapters` | list of [PerformanceChapterProgress](#schema-performancechapterprogress) | yes | Selected chapters still in the book, in book order. |

<a id="schema-performancequota"></a>
### PerformanceQuota

This library's daily Gemini request count for the model (a lower bound).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `requests_today` | integer | yes | Gemini speech requests for this model that this library recorded since the last midnight Pacific reset. Other apps or libraries sharing the API key are not counted, so the real provider usage may be higher. |
| `rpd` | integer | yes | Configured requests per day. |
| `resets_at` | string | yes | ISO 8601 UTC time of the next midnight Pacific reset. |

<a id="schema-performancerequest"></a>
### PerformanceRequest

A performance to preview or create.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string \| null |  | Display name, at most 200 characters. Omit, null or blank for a default such as `Kore · Gemini · 3 chapters`. |
| `mode` | `"simple"` \| `"cast"` | yes | Required. `simple` (one narrator) or `cast` (each speaker in their cast voice, narrator fallback). |
| `chapter_ids` | list of string | yes | Required, 1-5000 chapter IDs of this book (each at most 200 characters); stored in book order. (min items `1`; max items `5000`) |
| `provider` | `"system"` \| `"gemini"` \| `"breeze"` | yes | Required. `system`, `gemini` or `breeze`. |
| `voice` | string \| null |  | Simple mode narrator: `"library:vl_…"` (that library voice's current version), a direct provider voice ID, or empty/null for Default (Gemini `Kore`, the device default voice, or the Bardic default Breeze library voice). A direct Breeze ID must be in the last Breeze voice check. At most 256 characters. Ignored for `cast`. |
| `model` | string \| null |  | Gemini: a supported TTS model, default the configured one. Device and Breeze use their fixed models (`macos-say`, `breeze-tts-2`) and reject any other value. At most 200 characters. |

<a id="schema-performancestarted"></a>
### PerformanceStarted

A created or resumed performance.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `performance` | [Performance](#schema-performance) | yes |  |
| `job` | [Job](#schema-job) \| null | yes | The queued `performance` job, or null when every passage is already ready. |

<a id="schema-pipelineacceptimpact"></a>
### PipelineAcceptImpact

What accepting the selected scopes changes (preview) or changed (accept).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `step_id` | string | yes | Step ID whose versions are previewed or accepted. |
| `changed_scopes` | list of string | yes | Selected scopes whose accepted version changes. |
| `unchanged_scopes` | list of string | yes | Selected scopes already accepted with this exact version. |
| `conflicts` | list of [PipelineConflict](#schema-pipelineconflict) | yes | Generated values not applied, mostly because a person edited the field. |
| `audio_takes_invalidated` | integer | yes | Narration takes valid before and not after (they are cleared on accept). |
| `downstream_steps_affected` | list of string | yes | Steps that read this one (transitively) and have accepted versions; they may become stale. Empty when nothing changes. |
| `revision` | integer | yes | Preview: the book's current revision (send it as `expected_revision`). Accept: the new revision. |

<a id="schema-pipelineacceptresult"></a>
### PipelineAcceptResult

The applied impact and the decision recorded.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `step_id` | string | yes | Step ID whose versions are previewed or accepted. |
| `changed_scopes` | list of string | yes | Selected scopes whose accepted version changes. |
| `unchanged_scopes` | list of string | yes | Selected scopes already accepted with this exact version. |
| `conflicts` | list of [PipelineConflict](#schema-pipelineconflict) | yes | Generated values not applied, mostly because a person edited the field. |
| `audio_takes_invalidated` | integer | yes | Narration takes valid before and not after (they are cleared on accept). |
| `downstream_steps_affected` | list of string | yes | Steps that read this one (transitively) and have accepted versions; they may become stale. Empty when nothing changes. |
| `revision` | integer | yes | Preview: the book's current revision (send it as `expected_revision`). Accept: the new revision. |
| `decision` | [PipelineDecision](#schema-pipelinedecision) | yes |  |

<a id="schema-pipelineattempt"></a>
### PipelineAttempt

One recorded analysis HTTP attempt (classic or step pipeline).

The pipeline inspector lists the newest 100 for the book; the analysis
export's `analysis-attempts.json` lists all of them in this same shape.
Fields come from the stored attempt through a fixed allowlist and may be
absent on records from older versions. Prompts, responses, credentials,
price rates and server process IDs are never included.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Attempt ID. |
| `run_id` | string \| null |  | Job ID of the run that sent it. |
| `stage` | string \| null |  | Classic stage (`discovery`, `profiles`, `directing`) or pipeline step ID. |
| `unit_key` | string \| null |  | Opaque cache key of the unit of work. |
| `chapter_id` | string \| null |  | Chapter the request was about, when recorded. |
| `provider` | string \| null |  | Provider ID. |
| `model` | string \| null |  | Model ID. |
| `status` | `"reserved"` \| `"received"` \| `"uncertain"` \| `"not_sent"` \| `"interrupted_unknown"` \| null |  | `reserved`: allowance reserved and request possibly in flight. `received`: an HTTP response arrived (any status code). `uncertain`: sent but no response (billing unknown). `not_sent`: the connection failed before sending. `interrupted_unknown`: still `reserved` but its run is not active, so the outcome is unknown. |
| `created_at` | string \| null |  | ISO 8601 UTC reservation time. |
| `completed_at` | string \| null |  | ISO 8601 UTC time the outcome was recorded; absent while reserved. |
| `http_status` | integer \| null |  | Provider HTTP status code. A 200 does not mean the output passed validation. |
| `input_tokens` | integer \| null |  | Reported input tokens; null when not reported. |
| `output_tokens` | integer \| null |  | Reported output tokens; null when not reported. |
| `cached_input_tokens` | integer \| null |  | Reported cached input tokens; null when not reported. |
| `cache_write_input_tokens` | integer \| null |  | Reported cache-write input tokens; null when not reported. |
| `reserved_input_tokens` | integer \| null |  | Input allowance reserved before sending (conservative). |
| `reserved_output_tokens` | integer \| null |  | Output allowance reserved before sending. |
| `charged_estimate_usd` | number \| null |  | Conservative USD estimate for this attempt; null when unknown. |
| `cost_basis` | string \| null |  | How `charged_estimate_usd` was made, e.g. `reservation`, `usage_estimate_with_guard_uplift`, `not_sent` or `unknown`. |
| `price_as_of` | string \| null |  | Date of the price table used for the estimate, or null. |
| `price_source` | string \| null |  | URL of the price source used, or null. |
| `elapsed_seconds` | number \| null |  | Measured wall time of the request in seconds; null when unknown. |
| `input_artifact_id` | string \| null |  | Artifact ID of the retained request recipe (`analysis_input`). |
| `validation_state` | `"accepted"` \| `"rejected"` \| `"unknown"` | yes | From retained events: `accepted` or `rejected` by output validation; `unknown` when no event links it. |

<a id="schema-pipelinebookoverview"></a>
### PipelineBookOverview

Per-step state, the active run and recent runs for one book.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `book_id` | string | yes | Book ID of the requested book. |
| `revision` | integer | yes | The book's current revision number. |
| `steps` | list of [PipelineBookStep](#schema-pipelinebookstep) | yes | Every step, in pipeline order. |
| `active_run` | [PipelineRun](#schema-pipelinerun) \| null | yes | A queued or running run among the 5 most recent, or null. |
| `recent_runs` | list of [PipelineRun](#schema-pipelinerun) | yes | Up to 5 most recent runs, newest first. |
| `chapters` | list of [PipelineChapterRef](#schema-pipelinechapterref) | yes | Every chapter in reading order (including ineligible front and back matter). |

<a id="schema-pipelinebookstep"></a>
### PipelineBookStep

One step's state on this book.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Step ID. |
| `settings` | [PipelineStepSettingsView](#schema-pipelinestepsettingsview) | yes |  |
| `accepted_scopes` | integer | yes | Current scopes (eligible chapters for a chapter-scoped step, non-reserved characters for a character step) that have an accepted version. |
| `has_accepted` | boolean | yes | True when any scope, current or not, has an accepted version. |
| `total_scopes` | integer | yes | Number of current scopes counted as for `accepted_scopes`. |
| `accepted_origins` | map of string → integer | yes | Count of accepted scope versions by origin (`run`, `baseline`, `external`). |
| `stale_scopes` | list of string | yes | Accepted scopes produced by a run whose recorded input versions are no longer the accepted ones (or an input gained scopes). Nothing re-runs automatically. |
| `pending_versions` | integer | yes | Step versions in state `candidate` among the 20 most recent. |
| `latest` | [PipelineStepVersion](#schema-pipelinestepversion) \| null | yes | The most recent step version, or null. |

<a id="schema-pipelinecapabilities"></a>
### PipelineCapabilities

Features the inspector reports as present.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `word_alignment` | boolean | yes | Always false: word-level alignment is not implemented. |

<a id="schema-pipelinecensusrow"></a>
### PipelineCensusRow

`census` row: one known character or name candidate (ID is a character ID or `candidate_<hash>`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Stable row ID used to match rows across versions (a chapter, character, passage or candidate identity, depending on the step). |
| `scope` | string | yes | The version scope the row belongs to (`book`, a chapter ID or a character ID). |
| `_diff` | `"added"` \| `"changed"` \| `"same"` \| null |  | `added` (no row with this ID in the compared version), `changed` or `same`. |
| `_changed` | list of string \| null |  | Column keys whose value differs from the compared row. Absent for `added` rows. |
| `_previous` | object \| null |  | The compared row's value for each changed column key (cell values are strings, numbers, booleans or null). Absent for `added` rows. |
| `name` | string | yes | For a cast character, its name when the census ran (not refreshed to the current name); for a candidate, the name as found in the text. |
| `priority` | string | yes | Heuristic profile effort: `deep`, `standard` or `basic`. |
| `mentions` | integer | yes | Name mentions in eligible chapters. |
| `speech_tags` | integer | yes | Explicit speech tags naming it. |
| `dialogue_turns` | integer | yes | Dialogue passages currently attributed to it. |
| `chapters` | integer | yes | Eligible chapters mentioning it. |
| `known` | `"yes"` \| `"candidate"` | yes | `yes` for a cast character, `candidate` for a name not in the cast. |

<a id="schema-pipelinechapterref"></a>
### PipelineChapterRef

A chapter of the book, for chapter selection.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Opaque chapter ID, stable for the life of the book. Use it in `chapter_ids` and as a chapter scope. |
| `title` | string | yes | The chapter's current display title. |
| `kind` | string | yes | Section kind from the structure step, for example `chapter`, `front_matter` or `section` (the default). |

<a id="schema-pipelineconflict"></a>
### PipelineConflict

A generated value that was not applied, or an input that could not be.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `scope` | string | yes | Scope the value belongs to: `book`, a chapter ID or a book-local character ID. |
| `item_id` | string | yes | The character, passage, scene or candidate name concerned. |
| `field` | string | yes | The field kept, for example `description`, `speaker_id`, `scene_breaks`, `identity`, `character`, `passage`. |
| `reason` | string | yes | Human-readable explanation. |

<a id="schema-pipelinedecision"></a>
### PipelineDecision

An append-only accept or reject record.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Opaque decision ID. |
| `book_id` | string | yes | Book ID the decision applies to. |
| `step_id` | string | yes | Step ID the decision applies to. |
| `action` | `"accept"` \| `"reject"` | yes | `accept`: the listed versions became the accepted versions of their scopes and were applied to the book. `reject`: a person declined them; nothing else changed. |
| `mode` | `"user"` \| `"auto"` \| `"baseline"` \| `"external"` | yes | Who decided: `user`, `auto` (the step's gate), or `baseline`/`external` (the server recorded the existing book state). |
| `step_run_id` | string \| null | yes | The step version decided on, or null when accepting through `versions/accepted`. |
| `versions` | map of string → string | yes | `{scope: artifact ID}` the decision covered. |
| `note` | string \| null | yes | Explanatory text on captures, otherwise null. |
| `created_at` | string | yes | ISO 8601 UTC. |

<a id="schema-pipelinedefinitions"></a>
### PipelineDefinitions

Every step in pipeline order, every provider, and the saved per-step settings.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `schema_version` | `1` | yes | Version of this response envelope. |
| `providers` | list of [PipelineProvider](#schema-pipelineprovider) | yes | Every pipeline provider, cloud and self-hosted. |
| `steps` | list of [PipelineStepDefinition](#schema-pipelinestepdefinition) | yes | Steps in pipeline (dependency and display) order. |

<a id="schema-pipelinedirectingrow"></a>
### PipelineDirectingRow

`directing` row: one passage (ID is the passage ID).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Stable row ID used to match rows across versions (a chapter, character, passage or candidate identity, depending on the step). |
| `scope` | string | yes | The version scope the row belongs to (`book`, a chapter ID or a character ID). |
| `_diff` | `"added"` \| `"changed"` \| `"same"` \| null |  | `added` (no row with this ID in the compared version), `changed` or `same`. |
| `_changed` | list of string \| null |  | Column keys whose value differs from the compared row. Absent for `added` rows. |
| `_previous` | object \| null |  | The compared row's value for each changed column key (cell values are strings, numbers, booleans or null). Absent for `added` rows. |
| `scene` | string | yes | Scene title, or empty. |
| `kind` | string | yes | Passage kind, for example `dialogue` or `narration`. |
| `text` | string | yes | Passage text truncated to 160 characters. |
| `speaker` | string | yes | Proposed speaker's name (or ID when not in the cast). |
| `confidence` | number \| null | yes | Proposed speaker confidence 0–1, or null. |
| `direction` | string | yes | Delivery note. |
| `cues` | string | yes | Comma-separated vocal cues. |
| `check` | string | yes | BookNLP check label (with BookNLP's speaker when it differs or suggests), or empty. |
| `edited` | string | yes | Comma-separated fields a person edited (`speaker`, `direction`, `cues`); those keep their value. |

<a id="schema-pipelinediscoveryrow"></a>
### PipelineDiscoveryRow

`discovery` row: one character found in one scanned range (ID `<chapter>:<range start>:<name key>`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Stable row ID used to match rows across versions (a chapter, character, passage or candidate identity, depending on the step). |
| `scope` | string | yes | The version scope the row belongs to (`book`, a chapter ID or a character ID). |
| `_diff` | `"added"` \| `"changed"` \| `"same"` \| null |  | `added` (no row with this ID in the compared version), `changed` or `same`. |
| `_changed` | list of string \| null |  | Column keys whose value differs from the compared row. Absent for `added` rows. |
| `_previous` | object \| null |  | The compared row's value for each changed column key (cell values are strings, numbers, booleans or null). Absent for `added` rows. |
| `chapter` | string | yes | Chapter title, or the chapter ID when the chapter no longer exists. |
| `name` | string | yes | Character name as the model reported it in this range. |
| `aliases` | string | yes | Comma-separated aliases. |
| `evidence` | integer | yes | Number of exact supporting quotations. |
| `description` | string | yes | Draft notes. |

<a id="schema-pipelineevent"></a>
### PipelineEvent

One retained analysis event (newest 100 for the book).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Event ID (32 hex characters). |
| `book_id` | string | yes | Book ID the event belongs to. |
| `run_id` | string \| null | yes | Job ID of the run. |
| `stage` | string \| null | yes | Classic stage or pipeline step ID. |
| `unit_key` | string \| null | yes | Opaque unit cache key. |
| `event` | `"started"` \| `"accepted"` \| `"cache_hit"` \| `"cache_rejected"` \| `"cache_superseded"` \| `"validation_rejected"` \| `"budget_limited"` \| `"failed"` \| `"cancelled"` | yes | What happened to the unit. |
| `created_at` | string | yes | ISO 8601 UTC. |
| `artifact_id` | string \| null |  | Related artifact: request recipe (`started`), accepted output, cached output or rejection record. |
| `attempt_id` | string \| null |  | Related HTTP attempt, when known. |
| `error` | string \| null |  | Redacted failure or rejection text. Display only. |
| `cached_unit_key` | string \| null |  | For `cache_rejected` in classic runs: the unit key of the rejected cache entry. |
| `repair` | boolean \| null |  | For step-pipeline `started`: true when this is an evidence-repair request. |

<a id="schema-pipelineinspector"></a>
### PipelineInspector

Read-only inspector envelope for a book's processing pipeline.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `schema_version` | integer | yes | Envelope version (currently 1). |
| `book_id` | string | yes | Book ID of the inspected book. |
| `stages` | list of [PipelineStage](#schema-pipelinestage) | yes | Stage cards in pipeline order. |
| `jobs` | list of [Job](#schema-job) | yes | The book's newest 100 jobs, newest first, as full `Job` objects (the same as `GET /api/jobs?book_id=…`). |
| `usage` | [AnalysisUsage](#schema-analysisusage) | yes |  |
| `attempts` | list of [PipelineAttempt](#schema-pipelineattempt) | yes | The newest 100 analysis attempts, oldest first. |
| `events` | list of [PipelineEvent](#schema-pipelineevent) | yes | The newest 100 analysis events, newest first. |
| `capabilities` | [PipelineCapabilities](#schema-pipelinecapabilities) | yes |  |
| `artifact_kinds` | list of string | yes | Same as `artifact_counts.kinds`. |
| `artifact_counts` | [ArtifactCounts](#schema-artifactcounts) | yes |  |
| `notes` | list of string | yes | Interpretation notes. Display only. |

<a id="schema-pipelineplan"></a>
### PipelinePlan

A read-only estimate and the fingerprint that confirms it.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `book_id` | string | yes | Book ID the plan is for. |
| `steps` | list of [PipelinePlanStep](#schema-pipelineplanstep) | yes | Requested steps in pipeline order. |
| `chapter_ids` | list of string \| null | yes | Sorted chapter selection, or null for all eligible chapters. |
| `requests` | integer | yes | Sum of the steps' `requests`: model requests to send, before retries or evidence repairs. |
| `cached_units` | integer | yes | Sum of the steps' `cached_units` (0 when `fresh`). |
| `service_calls` | integer | yes | Sum of the steps' `service_calls` (free calls to self-hosted services). |
| `estimated_input_tokens` | integer | yes | Sum of the steps' `estimated_input_tokens`. |
| `output_token_allowance` | integer | yes | Sum of the steps' `output_token_allowance` (output token caps). |
| `estimated_cost_usd` | number \| null | yes | Sum over steps rounded to 6 decimals, or null when any step's cost is unknown. |
| `fresh` | boolean | yes | Echo of the request's `fresh`: true when cached validated units were not counted as reusable. Part of the fingerprint. |
| `missing_inputs` | map of string → list of string | yes | `{step: [required inputs]}` lacking an accepted result and not requested. |
| `fingerprint` | string | yes | Opaque plan identity. Send it as `expected_fingerprint` to run exactly this plan. |
| `note` | string | yes | Display text: estimates exclude retries and repairs; provider invoices are authoritative. |

<a id="schema-pipelineplanstep"></a>
### PipelinePlanStep

Known work and estimates for one step of a plan.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `step_id` | string | yes | Step ID. |
| `label` | string | yes | The step's display name. |
| `method` | `"plain"` \| `"llm"` \| `"service"` | yes | `plain` (local, free), `llm` (model requests, may be billed) or `service` (self-hosted, free). |
| `provider` | string | yes | Provider ID the plan used: the request's `configs` entry, otherwise the saved or default step setting. `local` for plain steps. |
| `model` | string \| null | yes | Model ID the plan used, or null for plain steps and service providers (and for an LLM step whose default provider has no configured model). |
| `units` | integer | yes | Units planned from the currently accepted inputs. |
| `cached_units` | integer | yes | Model or service units with a cached validated result (0 when `fresh`). |
| `requests` | integer | yes | Model requests to send (uncached LLM units), before retries or evidence repairs. |
| `service_calls` | integer | yes | Free calls to self-hosted services, not counted as model requests. |
| `estimated_input_tokens` | integer | yes | Estimated input tokens of those requests. |
| `output_token_allowance` | integer | yes | Sum of the output caps of those requests. |
| `estimated_cost_usd` | number \| null | yes | Conservative USD estimate (input priced with a 1.25 cache-write uplift, output at its cap), or null when a price is unknown. |
| `inputs_pending` | list of string | yes | Inputs also requested in this plan: the estimate uses their currently accepted results, and the real work depends on what the run produces. |
| `missing_inputs` | list of string | yes | Required inputs with no accepted result that are not in this plan (a run would be refused). |
| `scopes` | integer | yes | Number of distinct scopes the units cover. |
| `note` | string \| null |  | Present when `inputs_pending` is not empty: display text explaining the caveat. |

<a id="schema-pipelineprofilesrow"></a>
### PipelineProfilesRow

`profiles` row: one character's profile (ID is the character ID).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Stable row ID used to match rows across versions (a chapter, character, passage or candidate identity, depending on the step). |
| `scope` | string | yes | The version scope the row belongs to (`book`, a chapter ID or a character ID). |
| `_diff` | `"added"` \| `"changed"` \| `"same"` \| null |  | `added` (no row with this ID in the compared version), `changed` or `same`. |
| `_changed` | list of string \| null |  | Column keys whose value differs from the compared row. Absent for `added` rows. |
| `_previous` | object \| null |  | The compared row's value for each changed column key (cell values are strings, numbers, booleans or null). Absent for `added` rows. |
| `name` | string | yes | The character's current name, or its character ID when it is no longer in the cast. |
| `priority` | string | yes | Profile effort tier, or empty. |
| `description` | string | yes | Character description (profile text) in this version, or empty. |
| `direction` | string | yes | Voice direction. |
| `evidence` | integer | yes | Number of supporting quotations. |
| `edited` | string | yes | Comma-separated fields a person edited (`description`, `direction`); those keep their value whichever version is accepted. |

<a id="schema-pipelineprovider"></a>
### PipelineProvider

A provider a pipeline step can use. Each step lists which of these it accepts.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `"gemini"` \| `"openai"` \| `"anthropic"` \| `"local_llm"` \| `"booknlp"` \| `"novel_analyzer"` | yes | Provider ID. |
| `label` | string | yes | Display name. |
| `kind` | `"model"` \| `"service"` | yes | `model`: takes a model ID (prompt and schema). `service`: a self-hosted chapter service without a model choice (send `model: null`). |
| `self_hosted` | boolean | yes | True for a server on the owner's network (`local_llm`, `booknlp`, `novel_analyzer`). |
| `needs` | `"api_key"` \| `"url"` | yes | What must be configured in Settings: an API key (cloud) or a server URL. |
| `configured` | boolean | yes | A key or URL is set. It does not prove the server answers or the key works. |
| `models` | list of [PipelineProviderModel](#schema-pipelineprovidermodel) \| null |  | Present only for `local_llm`: curated models for the self-hosted server. |

<a id="schema-pipelineprovidermodel"></a>
### PipelineProviderModel

A curated model entry for the self-hosted LLM. Listing it does not prove the server has it loaded.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Model ID to send as `model`. |
| `label` | string | yes | Display name of the model. |
| `tier` | string | yes | Rough capability tier, for example `balanced`. |
| `roles` | list of string | yes | Where the model is suggested: `preprocess` (scan) and/or `analysis`. |
| `structured_output` | boolean | yes | Whether the model supports schema-constrained output. |
| `context_tokens` | integer | yes | Context window in tokens. |
| `max_output_tokens` | integer | yes | Largest output in tokens. |
| `input_usd_per_million` | number | yes | Price per million input tokens in USD (0 for a self-hosted server). |
| `output_usd_per_million` | number | yes | Price per million output tokens in USD (0 for a self-hosted server). |
| `availability` | string | yes | `unverified`: taken from a curated list, not from the server. |

<a id="schema-pipelinequotesrow"></a>
### PipelineQuotesRow

`quotes` row: a quotation (ID is a passage ID) or a BookNLP character (ID `<chapter>:character:<n>`).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Stable row ID used to match rows across versions (a chapter, character, passage or candidate identity, depending on the step). |
| `scope` | string | yes | The version scope the row belongs to (`book`, a chapter ID or a character ID). |
| `_diff` | `"added"` \| `"changed"` \| `"same"` \| null |  | `added` (no row with this ID in the compared version), `changed` or `same`. |
| `_changed` | list of string \| null |  | Column keys whose value differs from the compared row. Absent for `added` rows. |
| `_previous` | object \| null |  | The compared row's value for each changed column key (cell values are strings, numbers, booleans or null). Absent for `added` rows. |
| `kind` | `"Quotation"` \| `"Character"` | yes | `Quotation`: a passage BookNLP attributed (row ID is the passage ID). `Character`: a character BookNLP found in the chapter. Capitalized display labels. |
| `text` | string | yes | Passage text truncated to 160 characters, or a character summary. |
| `booknlp` | string | yes | BookNLP's speaker or character name. |
| `current` | string | yes | The book's current speaker (or matched cast character) name. |
| `check` | string | yes | Display label of the comparison, for example `Agrees`, `Differs`, `In cast`. |
| `tag` | string | yes | The speech tag or action beat text beside the quotation, or empty. |
| `conflict` | boolean | yes | True when BookNLP's own tag contradicts its speaker. |

<a id="schema-pipelineresultcolumn"></a>
### PipelineResultColumn

A column of the result table. Render `rows[key]` under `label`.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `key` | string | yes | Row field name. |
| `label` | string | yes | Display heading. |

<a id="schema-pipelinerun"></a>
### PipelineRun

An orchestrated pipeline run (`pipeline_runs`), returned as stored.

Created with `status: queued`; the job worker adds `started_at`, then
`completed_at` and `outcomes` when it finishes.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Run ID. |
| `book_id` | string | yes | Book ID the run works on. |
| `job_id` | string | yes | The `pipeline` job executing it (poll and cancel through the jobs API). |
| `status` | `"queued"` \| `"running"` \| `"completed"` \| `"failed"` \| `"budget_limited"` \| `"cancelled"` \| `"interrupted"` \| `"quota_limited"` | yes | `queued`, `running`, then `completed`, `failed`, `budget_limited` (a limit stopped it), `cancelled`, or `interrupted` (server restart, or the job ended before the run settled). If the job ended before work began, the run takes the job's final status (so `quota_limited` is theoretically possible). |
| `steps` | list of string | yes | Requested steps, deduplicated, in pipeline order. |
| `mode` | `"serial"` \| `"parallel"` | yes | `serial`: steps run one after another in pipeline order. `parallel`: each step starts as soon as the in-run inputs it reads have finished, so independent steps overlap. |
| `chapter_ids` | list of string \| null | yes | Sorted chapter selection, or null for all eligible chapters. |
| `configs` | map of string → [PipelineStepConfigView](#schema-pipelinestepconfigview) | yes | Provider and model snapshotted per requested step. |
| `gates` | map of string → `"auto"` \| `"review"` | yes | Gate snapshotted per requested step. |
| `concurrency` | integer | yes | Maximum model requests in flight across the run (1–4). |
| `fresh` | boolean | yes | True when cached validated units were ignored and new samples requested. |
| `limits` | [PipelineRunLimits](#schema-pipelinerunlimits) | yes |  |
| `step_run_ids` | list of string | yes | Step versions created so far, in creation order. |
| `error` | string \| null | yes | Human-readable failure text, or null. Display only. |
| `created_at` | string | yes | ISO 8601 UTC. |
| `updated_at` | string | yes | ISO 8601 UTC. |
| `started_at` | string \| null |  | ISO 8601 UTC time the worker began. Absent while queued. |
| `completed_at` | string \| null |  | ISO 8601 UTC finish time. Absent until the worker finishes. |
| `outcomes` | map of string → [PipelineRunOutcome](#schema-pipelinerunoutcome) \| null |  | Per-step outcome keyed by step ID. Absent until the worker finishes. |

<a id="schema-pipelinerunlimits"></a>
### PipelineRunLimits

Caps a run was started with; null means uncapped.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `max_requests` | integer \| null | yes | HTTP attempts allowed in this run. |
| `max_input_tokens` | integer \| null | yes | Input tokens (reserved or reported) allowed in this run. |
| `max_output_tokens` | integer \| null | yes | Output tokens (reserved or reported) allowed in this run. |
| `budget_usd` | number \| null | yes | Cumulative USD guard across every tracked attempt for the book, including earlier runs. |

<a id="schema-pipelinerunoutcome"></a>
### PipelineRunOutcome

How one requested step ended within a run.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `status` | `"completed"` \| `"failed"` \| `"budget_limited"` \| `"cancelled"` \| `"skipped"` | yes | `skipped`: not started, because a required input did not complete, is waiting for review, has no accepted result, or the run stopped. |
| `reason` | string \| null |  | Present when `skipped`: human-readable reason. |
| `step_run_id` | string \| null |  | The step version created, or null if it failed before one was created. Absent when skipped. |
| `scopes` | integer \| null |  | Number of scope versions recorded. Absent when skipped. |
| `accepted` | boolean \| null |  | True when the `auto` gate accepted the result. Absent when skipped. |
| `error` | string \| null |  | Present on some failures: human-readable error text. |

<a id="schema-pipelinerunstarted"></a>
### PipelineRunStarted

The queued job and the run record. A queued job is not a result: poll the job.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `job` | [Job](#schema-job) | yes | The job of kind `pipeline` (with `run_id`, `steps` and `scheduling` added). |
| `run` | [PipelineRun](#schema-pipelinerun) | yes | The run as created (`status: queued`, empty `step_run_ids`). |

<a id="schema-pipelinestage"></a>
### PipelineStage

One stage card of the pipeline inspector. Counts have stage-specific units; never sum them.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `"import"` \| `"structure"` \| `"census"` \| `"series"` \| `"discovery"` \| `"profiles"` \| `"directing"` \| `"voices"` \| `"narration"` \| `"alignment"` \| `"export"` | yes | Stage ID, in pipeline order. |
| `label` | string | yes | Display name. |
| `status` | `"complete"` \| `"partial"` \| `"pending"` \| `"available"` \| `"not_started"` \| `"provisional"` \| `"planned"` \| `"ready"` \| `"queued"` \| `"running"` \| `"failed"` \| `"interrupted"` \| `"cancelled"` \| `"budget_limited"` | yes | `complete`/`partial`/`pending` from the counts. Fixed states: `series` is `available` (book is in a series) or `not_started`; `profiles` is `provisional` while whole-book discovery is incomplete and some profiles are current; `alignment` is `planned`; `export` is `ready`. An active `analyze` job whose checkpoint stage is this stage shows the job status (`queued`/`running`); likewise an active `render` job for `narration`. With no active job, a `failed`, `interrupted` or `budget_limited` classic checkpoint shows that status on its stage, and `cancelled` instead of `interrupted` when the job that wrote the checkpoint was cancelled. Checkpoints written before contract 0.2.0 do not name their job, so they show `interrupted` for a cancellation too. |
| `completed` | integer \| null | yes | Units done, or null where not counted (`series`, `export`). |
| `total` | integer \| null | yes | Units in scope, or null where not counted. |
| `unit_label` | string | yes | What the counts measure, e.g. `sections`, `eligible sections`, `profiles`, `passages`. |
| `dependencies` | list of string | yes | Stage IDs this stage conceptually depends on. Descriptive, not a scheduler. |
| `artifact_count` | integer | yes | Retained artifact versions whose stage equals this ID. |
| `note` | string | yes | Interpretation text. Display only. |

<a id="schema-pipelinestepconfigview"></a>
### PipelineStepConfigView

The provider and model a run snapshotted for one step.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | string | yes | Provider ID the run uses for this step: `local` for plain steps, otherwise a pipeline provider ID. |
| `model` | string \| null | yes | Model ID the run sends, or null for plain steps and service providers. |

<a id="schema-pipelinestepdefinition"></a>
### PipelineStepDefinition

One registered step (its declarative contract) with its effective settings.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Stable step ID, `[a-z][a-z0-9_]{1,39}`. Current steps: `structure`, `census`, `discovery`, `quotes`, `profiles` and `directing` (in pipeline order; the list is defined by the server and may grow). |
| `label` | string | yes | Display name. |
| `summary` | string | yes | One-paragraph display description of what the step does. |
| `method` | `"plain"` \| `"llm"` \| `"service"` | yes | `plain`: computed locally, free. `llm`: a prompt and schema sent to a chosen model (may be billed). `service`: a self-hosted chapter service (free). |
| `scope` | `"book"` \| `"chapter"` \| `"character"` | yes | Granularity at which results are versioned and accepted. Scope IDs are `book`, a chapter ID, or a book-local character ID respectively. |
| `inputs` | list of string | yes | Upstream step IDs whose accepted results this step reads (recorded for staleness). |
| `requires` | list of string | yes | The subset of `inputs` that must have an accepted result (or be in the same run) before this step can run. |
| `owns` | list of string | yes | Book fields this step writes on acceptance, as `collection.field` (`collection[]` means it may add items). No two steps own one field. |
| `version` | integer | yes | Step logic version. Bumped when prompts, schemas or assembly change; recorded on each version as `step_version`. |
| `parallel` | integer | yes | Maximum concurrent units of this step (1–8); a run's `concurrency` also caps it. |
| `default_gate` | `"auto"` \| `"review"` | yes | Gate used when none is saved or sent. |
| `providers` | list of string | yes | Provider IDs the owner may choose: `["local"]` for plain steps, otherwise IDs from the top-level `providers` list. |
| `offline_providers` | list of string | yes | Providers this step reads accepted results from instead of contacting (directing's `booknlp`), so a run needs no key or URL for them. |
| `default_model_role` | `"scan"` \| `"analysis"` | yes | Which configured model a new LLM step uses by default: the economy `scan` model or the `analysis` model. |
| `chapter_scoped` | boolean | yes | True when a run's `chapter_ids` selection narrows this step's work. |
| `capturable` | boolean | yes | True when the server can rebuild this step's result from the book, which enables `baseline`/`external` versions and rollback to them. |
| `accumulative` | boolean | yes | True when accepting only adds to the book (discovery): accepting applies just the scopes whose version changes. |
| `settings` | [PipelineStepSettingsView](#schema-pipelinestepsettingsview) | yes | Effective provider, model and gate for this step. |

<a id="schema-pipelinesteprun"></a>
### PipelineStepRun

A step version as stored (`pipeline_step_runs`): one execution of one step, or a captured outside state.

Returned raw: every field below is always present unless marked optional.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Step version ID (use as `{version_id}`). |
| `book_id` | string | yes | Book ID of the book this version belongs to. |
| `run_id` | string \| null | yes | The orchestrated run that produced it, or null for a `baseline`/`external` capture. |
| `step_id` | string | yes | Step ID of the step that produced this version. |
| `step_version` | integer | yes | The step's `version` when this was produced. |
| `origin` | `"run"` \| `"baseline"` \| `"external"` | yes | `run`: produced by a pipeline run. `baseline`: the book's state the first time the pipeline saw it. `external`: a later change made outside the pipeline (older analysis controls, series runs, structure repair). Captures have unknown producers. |
| `provider` | string \| null | yes | Provider ID used (`local` for plain steps), or null for captures. |
| `model` | string \| null | yes | Model ID, or null for plain steps, services and captures. |
| `status` | `"queued"` \| `"running"` \| `"completed"` \| `"failed"` \| `"budget_limited"` \| `"cancelled"` \| `"interrupted"` | yes | `running` while it works (`queued` is never written today). `interrupted`: the server restarted while it ran. Captures are created `completed`. |
| `inputs` | map of string → map of string → string | yes | The accepted input versions this run read: `{input step ID: {scope: artifact ID}}`. Empty for captures. |
| `chapter_ids` | list of string \| null | yes | Sorted chapter selection for a chapter-scoped step, or null for all eligible chapters. |
| `scopes` | map of string → string | yes | The result: `{scope: artifact ID}` of one immutable version per scope. Filled when the run finishes; only scopes whose every unit validated appear. |
| `unchanged_scopes` | list of string | yes | Scopes whose result is identical to the version already accepted when this run finished (content-addressed: same artifact ID). |
| `units` | [PipelineUnitCounts](#schema-pipelineunitcounts) | yes |  |
| `error` | string \| null | yes | Human-readable failure text (secrets redacted), or null. Display only. |
| `created_at` | string | yes | ISO 8601 UTC creation time. |
| `updated_at` | string | yes | ISO 8601 UTC time of the last change. |
| `completed_at` | string \| null | yes | ISO 8601 UTC time the run finished, or null (always null for captures). |
| `incomplete_scopes` | list of string \| null |  | Scopes with at least one unit that did not validate (so no version was recorded for them). Absent until the run finishes, on captures, on runs interrupted by a restart, and on runs that failed before their units were assembled. |

<a id="schema-pipelinestepsettingsview"></a>
### PipelineStepSettingsView

The effective provider, model and gate for one step: the saved choice, or a default.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | string | yes | Provider ID: `local` for plain (local) steps, otherwise one of the step's `providers`. |
| `model` | string \| null | yes | Model ID, or null for local steps, service providers (`booknlp`, `novel_analyzer`) and an LLM step whose default provider has no configured model. Planning or running an LLM step with a null model is refused (400 `step_model_missing`) unless the request sends a model in `configs`. |
| `gate` | `"auto"` \| `"review"` | yes | `auto` accepts a completed run of this step immediately; `review` waits for a person. A saved gate applies even when `saved_invalid` is true. |
| `saved` | boolean | yes | True when `provider` and `model` are the owner's saved choice for this step. False means they are computed defaults (the preferred analysis provider and its configured analysis or scan model). |
| `saved_invalid` | boolean | yes | True when a saved provider/model choice exists but no longer validates (for example, the step no longer offers that provider). It is ignored: `saved` is false and the defaults apply. Saving new settings replaces it. |

<a id="schema-pipelinestepversion"></a>
### PipelineStepVersion

A step version summarized for history lists, with its review state.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Step version ID. |
| `run_id` | string \| null | yes | The orchestrated run ID, or null for a capture. |
| `step_id` | string | yes | Step ID of the step that produced this version. |
| `step_version` | integer | yes | The step's logic `version` when this version was produced. Compare it with the step definition's current `version` to spot results from older prompts or schemas. |
| `origin` | `"run"` \| `"baseline"` \| `"external"` | yes | `run` (produced by a pipeline run), `baseline` (the book state the pipeline first saw) or `external` (a later change made outside the pipeline). See `PipelineStepRun.origin`. |
| `provider` | string \| null | yes | Provider ID used (`local` for plain steps), or null for `baseline`/`external` captures. |
| `model` | string \| null | yes | Model ID used, or null for plain steps, service providers and captures. |
| `status` | `"queued"` \| `"running"` \| `"completed"` \| `"failed"` \| `"budget_limited"` \| `"cancelled"` \| `"interrupted"` | yes | Execution status, as on the step run (`interrupted`: the server restarted while it ran). This is not the review `state`. |
| `units` | [PipelineUnitCounts](#schema-pipelineunitcounts) | yes |  |
| `error` | string \| null | yes | Human-readable failure or stop reason (secrets redacted), or null when there was none. Display only; do not parse. |
| `created_at` | string | yes | ISO 8601 UTC. |
| `completed_at` | string \| null | yes | ISO 8601 UTC, or null. |
| `chapter_ids` | list of string \| null | yes | Sorted chapter IDs the run was limited to, or null when it covered every eligible chapter (also null for captures). |
| `unchanged_scopes` | list of string | yes | Scopes whose result was identical (the same artifact ID) to the version already accepted when the run finished. Empty for captures and unfinished runs. |
| `incomplete_scopes` | list of string \| null | yes | As on the step run; null when not recorded. |
| `scope_count` | integer | yes | Number of scopes with a result in this version. |
| `accepted_scopes` | integer | yes | How many of those scopes are the currently accepted version. |
| `state` | `"running"` \| `"empty"` \| `"accepted"` \| `"partly_accepted"` \| `"rejected"` \| `"same_as_accepted"` \| `"superseded"` \| `"candidate"` | yes | Review state, derived in this order: `running` (queued or running); `empty` (no scope results); `accepted` (a person or policy accepted this version and every scope is still current); `partly_accepted` (accepted, but only some scopes are still current); `rejected`; `same_as_accepted` (never accepted, but every scope equals the accepted content); `superseded` (accepted earlier and since replaced, or its content was accepted through another version); `candidate` (waiting for review). Acceptance comes from decisions, never from coincidental equality. |

<a id="schema-pipelinestructurerow"></a>
### PipelineStructureRow

`structure` row: one chapter's title and kind.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Stable row ID used to match rows across versions (a chapter, character, passage or candidate identity, depending on the step). |
| `scope` | string | yes | The version scope the row belongs to (`book`, a chapter ID or a character ID). |
| `_diff` | `"added"` \| `"changed"` \| `"same"` \| null |  | `added` (no row with this ID in the compared version), `changed` or `same`. |
| `_changed` | list of string \| null |  | Column keys whose value differs from the compared row. Absent for `added` rows. |
| `_previous` | object \| null |  | The compared row's value for each changed column key (cell values are strings, numbers, booleans or null). Absent for `added` rows. |
| `title` | string | yes | Chapter title recorded in this version, or empty. |
| `kind` | string | yes | Section kind, for example `chapter` or `front_matter`. |
| `source` | string | yes | Where the title came from (the chapter's `title_source`), or empty. |

<a id="schema-pipelineunitcounts"></a>
### PipelineUnitCounts

Unit progress of one step version.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `total` | integer | yes | Units planned. |
| `done` | integer | yes | Units that produced a validated result (including cached ones). |
| `cached` | integer | yes | Of `done`, units reused from the validated-unit cache without a new request. |
| `failed` | integer | yes | Units that failed, were stopped by a limit or were cancelled. |

<a id="schema-pipelineversiondetail"></a>
### PipelineVersionDetail

A step version's result as a generic table, diffed by row ID against another version.

`stats`, `columns` and `rows` are produced by the step. Clients should
render generically: show `stats` as label/value pairs and each column's
`key` from every row; per-step row shapes are listed in `rows`.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `step_id` | string | yes | Step ID. |
| `version_id` | string | yes | The requested `{version_id}` (a step version ID or `accepted`). |
| `run` | [PipelineStepRun](#schema-pipelinesteprun) \| null | yes | The step version record, or null for `accepted`. |
| `stats` | map of string → integer \| number \| string \| null | yes | Step-defined summary numbers for display, keyed by name. structure: `sections`, one count per section kind, `structure_version`. census: `words`, `eligible_sections`, `name_candidates`, `estimated_source_tokens`. discovery: `sections`, `mentions`, `distinct_names`, `ranges`. quotes: `sections`, `quotations`, `agrees`, `differs`, `suggests_speaker`, `not_in_cast`, `tag_conflicts`, `unmatched_quotations`, `dialogue_without_quotation`, `agreement` (0–1 or null). profiles: `profiles`, `refined`. directing: `sections`, `scenes`, `passages`, `dialogue`, `unassigned_dialogue`, `attributed_dialogue`, `same_speaker_as_book`, and with an accepted BookNLP check `booknlp_agrees`, `booknlp_differs`, `booknlp_suggests`. |
| `columns` | list of [PipelineResultColumn](#schema-pipelineresultcolumn) | yes | Columns to display, in order. |
| `diff` | [PipelineVersionDiff](#schema-pipelineversiondiff) | yes |  |
| `total_rows` | integer | yes | Rows after the `scope` and `changed_only` filters, before paging. |
| `offset` | integer | yes | Rows skipped: the `offset` query parameter, raised to 0 when negative. |
| `limit` | integer | yes | The page size used: the `limit` query parameter clamped to 1–1000. |
| `rows` | list of [PipelineDirectingRow](#schema-pipelinedirectingrow) \| [PipelineQuotesRow](#schema-pipelinequotesrow) \| [PipelineProfilesRow](#schema-pipelineprofilesrow) \| [PipelineDiscoveryRow](#schema-pipelinediscoveryrow) \| [PipelineCensusRow](#schema-pipelinecensusrow) \| [PipelineStructureRow](#schema-pipelinestructurerow) | yes | The requested page of rows. Most cell values reflect the book's current names and passages; census rows use the names stored in the result and structure rows use the version's own titles. The row shape depends on the step (one variant per step); every row has `id` and `scope`. |
| `scopes` | list of [PipelineVersionScope](#schema-pipelineversionscope) | yes | Every scope of the displayed version. |
| `revision` | integer | yes | The book's current revision. |

<a id="schema-pipelineversiondiff"></a>
### PipelineVersionDiff

Row-level comparison counts over the whole table (before `scope`/`changed_only` filters).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `compared_with` | string \| null | yes | The `compare` value used, or null when nothing was compared (compare `none`, compare equal to the version, or the compared version is empty, for example when nothing is accepted yet). |
| `same` | integer | yes | Rows present in both versions with equal values in every column. 0 when nothing was compared. |
| `changed` | integer | yes | Rows present in both versions with at least one differing column value. 0 when nothing was compared. |
| `added` | integer | yes | Rows of this version with no row of the same ID in the compared version. 0 when nothing was compared. |
| `removed` | integer | yes | Rows of the compared version with no matching row ID here (not returned as rows). |
| `agreement` | number \| null |  | `same / (same + changed)` rounded to 4 decimals, or null when no rows matched. Absent when nothing was compared. |

<a id="schema-pipelineversionhistory"></a>
### PipelineVersionHistory

A step's version history and recent decisions.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `step_id` | string | yes | Step ID. |
| `items` | list of [PipelineStepVersion](#schema-pipelinestepversion) | yes | Newest first, at most `limit`. |
| `decisions` | list of [PipelineDecision](#schema-pipelinedecision) | yes | Up to 50 most recent decisions for this step, newest first. |

<a id="schema-pipelineversionscope"></a>
### PipelineVersionScope

One scope of the displayed version.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `scope` | string | yes | Scope ID: `book`, a chapter ID or a book-local character ID, depending on the step's `scope`. |
| `artifact_id` | string | yes | Immutable version artifact ID (inspectable through the artifacts API). |
| `accepted` | boolean | yes | True when this artifact is the currently accepted version of the scope. |

<a id="schema-planrequest"></a>
### PlanRequest

Which steps to estimate, over which chapters, with which providers.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `steps` | list of string | yes | Step IDs to plan (1–40). Order does not matter: steps are planned in pipeline order. Duplicates are ignored. An unknown ID is refused (400 `unknown_step`). (min items `1`; max items `40`) |
| `chapter_ids` | list of string \| null |  | Chapters to limit chapter-scoped steps to (1–2000 IDs of this book; other steps ignore it). Omit or null for every eligible (story) chapter. An empty list is refused (400 `chapter_ids_empty`). |
| `configs` | map of string → [StepConfig](#schema-stepconfig) \| null |  | `{step ID: StepConfig}` overriding the saved provider/model for this request. Entries for steps not requested are ignored. |
| `fresh` | boolean |  | When true, cached validated units are not reused: new samples are requested (for comparing a model with itself). Part of the plan fingerprint. Default false. (default `false`) |

<a id="schema-profilefreshness"></a>
### ProfileFreshness

Currency of one character's vocal profile.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `character_id` | string | yes | Book-local character ID. |
| `state` | `"reviewed"` \| `"current"` \| `"stale"` \| `"draft"` | yes | `reviewed`: manually edited (authoritative). `current`: refined from the current evidence set. `stale`: refined earlier but the evidence or settings changed. `draft`: never refined. |
| `provisional` | boolean | yes | True when the profile may still change: the whole book is not yet discovered, or the state is neither `current` nor `reviewed`. |

<a id="schema-pronunciationentry"></a>
### PronunciationEntry

A pronunciation entry. For adding, `term` and `respelling` are required. For changing, fields left out keep their saved values. Also used, with the `id` of the entry it edits, to audition an unsaved respelling in a voice example.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string \| null |  | Ignored when adding or changing (the path names the entry). In a voice example, the entry this unsaved version replaces; omit it for a new word. |
| `term` | string | yes | The word or phrase as written: at most 80 characters after collapsing whitespace, with at least one letter or digit (the request accepts up to 200 before normalization). (max length `200`) |
| `respelling` | string | yes | How to say it: at most 120 characters after collapsing whitespace. Control characters, brackets, parentheses, braces and backslashes are refused. (max length `300`) |
| `providers` | map of string → string \| null \| null |  | Per-narrator overrides keyed by `system`, `gemini` or `breeze`. An empty or null value drops that override; an override equal to the term leaves that narrator reading the word unchanged. |
| `match_case` | boolean |  | True (default): match exact case. False: match any case. (default `true`) |
| `character_id` | string \| null |  | Optional book-local character the word belongs to; must be in the current cast. |
| `note` | string \| null |  | Optional free-text note, at most 500 characters. |

<a id="schema-pronunciationexample"></a>
### PronunciationExample

One occurrence of a term in chapter text.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `chapter_id` | string | yes | Chapter whose text the offsets index. |
| `start` | integer | yes | Zero-based Unicode code-point offset of the match in the chapter text. |
| `end` | integer | yes | Exclusive end offset of the match, in code points. |
| `context` | string | yes | The match with up to 60 code points of chapter text on either side. |

<a id="schema-pronunciationlist"></a>
### PronunciationList

The book's pronunciations.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `pronunciations` | list of [PronunciationWithUsage](#schema-pronunciationwithusage) | yes | All entries, in saved order, each with its usage. |

<a id="schema-pronunciationsaved"></a>
### PronunciationSaved

The result of adding, changing or removing a pronunciation.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `book` | [Book](#schema-book) | yes | The updated book document; its `revision` has increased. |
| `pronunciations` | list of [PronunciationWithUsage](#schema-pronunciationwithusage) | yes | All entries after the change, each with its usage. |
| `retired_takes` | integer | yes | Studio takes that no longer match their recipe and were unselected. Their WAVs stay archived and are reused without a request if the recipe returns. |

<a id="schema-pronunciationusage"></a>
### PronunciationUsage

Where a term occurs, counted as narration applies entries.

Where terms overlap ("Tar Valon", "Valon") only the longer match counts.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `occurrences` | integer | yes | Whole-word matches in all chapter text. |
| `passages` | integer | yes | Passages containing at least one match. |
| `rendered_passages` | integer | yes | Of those, passages with a current Studio (enhanced) take. Changing the entry retires these takes. |
| `first_passage_id` | string \| null | yes | First passage containing the term, in reading order; null when none. |
| `examples` | list of [PronunciationExample](#schema-pronunciationexample) | yes | Up to three occurrences, in reading order. |

<a id="schema-pronunciationwithusage"></a>
### PronunciationWithUsage

A pronunciation entry with its use in the book.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Entry ID assigned by the server: `pr_` followed by 12 lowercase hex digits. |
| `term` | string | yes | The word or phrase as written in the book: at most 80 characters, whitespace collapsed, with at least one letter or digit. |
| `respelling` | string | yes | How to say it, for example `Kaylor` for `Cthaelor`: at most 120 characters. Control characters, brackets, parentheses, braces and backslashes are refused, because narrators perform `(laugh)`, `<sigh>` and `[[…]]` instead of reading them. |
| `match_case` | boolean | yes | True: match the term's exact case. False: match any case. Two case-sensitive entries may differ only in case; otherwise a term appears once per book. |
| `providers` | map of string → string \| null |  | Per-narrator overrides of `respelling`, keyed by `system`, `gemini` or `breeze`; absent when there are none. An override equal to the term leaves that narrator reading the word unchanged. |
| `character_id` | string \| null |  | Book-local character the word belongs to (informational); absent when none. It had to be in the cast when the entry was added or changed; a link left by a character that analysis later removed stays until the entry is edited. |
| `note` | string \| null |  | Free-text note, at most 500 characters; absent when empty. |
| `usage` | [PronunciationUsage](#schema-pronunciationusage) | yes | Where the term occurs, computed from the current book text. |

<a id="schema-renderrequest"></a>
### RenderRequest

Which passages to narrate with the cast, and how.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | string |  | Narration provider: `system` (default, macOS `say`), `gemini` or `breeze`. Other values give 400. (default `"system"`) |
| `scene_id` | string \| null |  | Only passages of this scene. Omit for no scene filter. |
| `segment_id` | string \| null |  | Only this passage. Omit for no passage filter. With `scene_id`, both must match. |
| `force` | boolean |  | Generate a new take even when a take with the same recipe exists (older bytes are kept). Default false. (default `false`) |

<a id="schema-resourceaggregate"></a>
### ResourceAggregate

Totals over a set of resource rows.

For each measured quantity, the value is the sum over rows that recorded it.
It is 0 when no row qualifies (an empty scope, or token quantities when every
row was local or cached), and null only when rows qualify but none recorded
the value. `unknown_<quantity>_operations` counts the qualifying rows that
did not record it. Token quantities only consider rows that may have made a provider
request. Unknown never means zero or free.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `operations` | integer | yes | Rows in the set. |
| `requests` | integer | yes | Sum of known provider request counts. |
| `cached_operations` | integer | yes | Rows that reused saved output. |
| `failed_operations` | integer | yes | Rows with status failed/uncertain/not_sent/interrupted or a rejected validation. |
| `running_operations` | integer | yes | Rows still running or reserved. |
| `elapsed_seconds` | number \| null | yes | Sum of recorded leaf elapsed time in seconds (not end-to-end job latency). |
| `cpu_seconds` | number \| null | yes | Sum of measured current-Python-thread CPU seconds. |
| `audio_seconds` | number \| null | yes | Sum of produced or reused audio duration in seconds. |
| `estimated_cost_usd` | number \| null | yes | Sum of known cost estimates in USD. Not an invoice. |
| `input_tokens` | number \| null | yes | Sum of reported input tokens. |
| `output_tokens` | number \| null | yes | Sum of reported output tokens. |
| `cached_input_tokens` | number \| null | yes | Sum of reported cached input tokens. |
| `cache_write_input_tokens` | number \| null | yes | Sum of reported cache-write input tokens. |
| `output_bytes` | number \| null | yes | Sum of recorded output sizes in bytes. |
| `unknown_elapsed_seconds_operations` | integer | yes | Rows with no recorded elapsed time (for example cache reuse or historical rows). Such rows are left out of `elapsed_seconds`; unknown is not zero. |
| `unknown_cpu_seconds_operations` | integer | yes | Rows with no measured CPU time (CPU is only measured for opted-in local work, so most cloud and cached rows count here). Unknown is not zero. |
| `unknown_audio_seconds_operations` | integer | yes | Rows with no recorded audio duration (including every non-audio row such as analysis requests). Unknown is not zero. |
| `unknown_estimated_cost_usd_operations` | integer | yes | Rows with no USD cost estimate. Such rows are left out of `estimated_cost_usd`; unknown cost is not free. |
| `unknown_input_tokens_operations` | integer | yes | Rows that may have made a provider request (request count not 0, including unknown) but reported no input-token count. Local and cached rows are not counted. |
| `unknown_output_tokens_operations` | integer | yes | Rows that may have made a provider request but reported no output-token count. Local and cached rows are not counted. |
| `unknown_cached_input_tokens_operations` | integer | yes | Rows that may have made a provider request but reported no cached-input-token count. Missing is unknown, not zero. |
| `unknown_cache_write_input_tokens_operations` | integer | yes | Rows that may have made a provider request but reported no cache-write input-token count. Missing is unknown, not zero. |
| `unknown_output_bytes_operations` | integer | yes | Rows with no recorded output size in bytes (most non-file rows, such as analysis requests). Unknown is not zero. |
| `reserved_cost_usd` | number | yes | Part of the estimate that is a reservation (usage never reported), in USD. |
| `unknown_request_count_operations` | integer | yes | Rows whose request count is unknown. |

<a id="schema-resourceoperation"></a>
### ResourceOperation

One recorded unit of work: an analysis HTTP attempt, a local or narration operation, or a cache reuse.

`kind` tells the three apart. `analysis_request` rows come from the
analysis attempt ledger; `cache_reuse` rows from cache-hit events (no
request, no measured duration); every other kind from the resource
ledger. Absent or null measurements are unknown, not zero.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Row ID (32 hex characters): the analysis attempt ID for `analysis_request`, the cache-hit event ID for `cache_reuse`, otherwise the resource-ledger operation ID. Unique within the response. |
| `book_id` | string | yes | Book ID the work was recorded for. |
| `run_id` | string \| null |  | Job ID, or null for work outside a job (e.g. imports). |
| `stage` | string \| null |  | What was measured, e.g. `discovery`, `discovery_validation`, `publication`, `census`, `local_analysis`, `narration`, `simple_listen`, `listen_chunk`, `voice_preview`, `voice_design`, `import`, `structure_repair`, `metadata_refresh`, `audio_export`, or a pipeline step ID. `source_search` and `analysis_export` rows were recorded by versions before contract 0.2.0 and remain. |
| `unit_key` | string \| null |  | Opaque unit key (cache key, passage ID, preview ID or census fingerprint). |
| `chapter_id` | string \| null |  | Chapter the work belongs to, when recorded. |
| `provider` | string \| null |  | Provider ID (`local` for local work). |
| `model` | string \| null |  | Model ID, when any. |
| `kind` | string | yes | `analysis_request`, `cache_reuse`, or a resource-ledger kind: `local`, `assembly`, `validation`, `narration`. |
| `cached` | boolean | yes | True when saved output was reused without a provider request. |
| `status` | `"reserved"` \| `"received"` \| `"uncertain"` \| `"not_sent"` \| `"running"` \| `"completed"` \| `"failed"` \| `"interrupted"` \| `"unknown"` | yes | Analysis requests: attempt status (`reserved`, `received`, `uncertain`, `not_sent`), `failed` when the HTTP status was >= 400 or a failure event names it, `unknown` for legacy rows. Other rows: `running`, `completed`, `failed`, `interrupted`. Rows left `running`/`reserved` by an earlier server process or a finished job are reported `interrupted`. |
| `created_at` | string \| null |  | ISO 8601 UTC start time. |
| `completed_at` | string \| null |  | ISO 8601 UTC end time; absent while running. |
| `request_count` | integer \| null |  | Provider requests made: 1 for analysis requests, 0 for local/cached work, null when unknown (e.g. a cloud narration that failed early). |
| `input_tokens` | integer \| null |  | Reported input tokens. |
| `output_tokens` | integer \| null |  | Reported output tokens. |
| `cached_input_tokens` | integer \| null |  | Reported cached input tokens. |
| `cache_write_input_tokens` | integer \| null |  | Reported cache-write input tokens. |
| `output_bytes` | integer \| null |  | Bytes written (audio, exports). |
| `elapsed_seconds` | number \| null |  | Measured wall time of this step in seconds. |
| `cpu_seconds` | number \| null |  | Measured CPU seconds of the current Python thread (local work only). |
| `audio_seconds` | number \| null |  | Audio duration produced or reused, in seconds. |
| `estimated_cost_usd` | number \| null |  | Estimated USD cost; 0 for local/cached/self-hosted work; null when unknown. |
| `cost_basis` | string \| null |  | How the estimate was made, e.g. `reservation`, `usage_estimate_with_guard_uplift`, `not_sent`, `no_provider_request`, `self_hosted`, `standard_paid_tier_usage_estimate`, `legacy_estimate`, `unknown`. |
| `price_as_of` | string \| null |  | Date of the price table used. |
| `price_source` | string \| null |  | URL of the price source used. |
| `usage_source` | string \| null |  | Where token usage came from (narration), e.g. `gemini_interactions` or `not_reported`. |
| `cpu_scope` | string \| null |  | `current_python_thread` when CPU was measured. |
| `artifact_id` | string \| null |  | Related retained artifact, when recorded. |
| `asset_id` | string \| null |  | Related stored audio asset, when recorded. |
| `http_status` | integer \| null |  | Provider HTTP status, when any. |
| `validation_state` | `"accepted"` \| `"rejected"` \| `"unknown"` \| null |  | Analysis requests only: output validation outcome from retained events. |

<a id="schema-resourcerunaggregate"></a>
### ResourceRunAggregate

Aggregate of one run (job) in scope.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `operations` | integer | yes | Rows in the set. |
| `requests` | integer | yes | Sum of known provider request counts. |
| `cached_operations` | integer | yes | Rows that reused saved output. |
| `failed_operations` | integer | yes | Rows with status failed/uncertain/not_sent/interrupted or a rejected validation. |
| `running_operations` | integer | yes | Rows still running or reserved. |
| `elapsed_seconds` | number \| null | yes | Sum of recorded leaf elapsed time in seconds (not end-to-end job latency). |
| `cpu_seconds` | number \| null | yes | Sum of measured current-Python-thread CPU seconds. |
| `audio_seconds` | number \| null | yes | Sum of produced or reused audio duration in seconds. |
| `estimated_cost_usd` | number \| null | yes | Sum of known cost estimates in USD. Not an invoice. |
| `input_tokens` | number \| null | yes | Sum of reported input tokens. |
| `output_tokens` | number \| null | yes | Sum of reported output tokens. |
| `cached_input_tokens` | number \| null | yes | Sum of reported cached input tokens. |
| `cache_write_input_tokens` | number \| null | yes | Sum of reported cache-write input tokens. |
| `output_bytes` | number \| null | yes | Sum of recorded output sizes in bytes. |
| `unknown_elapsed_seconds_operations` | integer | yes | Rows with no recorded elapsed time (for example cache reuse or historical rows). Such rows are left out of `elapsed_seconds`; unknown is not zero. |
| `unknown_cpu_seconds_operations` | integer | yes | Rows with no measured CPU time (CPU is only measured for opted-in local work, so most cloud and cached rows count here). Unknown is not zero. |
| `unknown_audio_seconds_operations` | integer | yes | Rows with no recorded audio duration (including every non-audio row such as analysis requests). Unknown is not zero. |
| `unknown_estimated_cost_usd_operations` | integer | yes | Rows with no USD cost estimate. Such rows are left out of `estimated_cost_usd`; unknown cost is not free. |
| `unknown_input_tokens_operations` | integer | yes | Rows that may have made a provider request (request count not 0, including unknown) but reported no input-token count. Local and cached rows are not counted. |
| `unknown_output_tokens_operations` | integer | yes | Rows that may have made a provider request but reported no output-token count. Local and cached rows are not counted. |
| `unknown_cached_input_tokens_operations` | integer | yes | Rows that may have made a provider request but reported no cached-input-token count. Missing is unknown, not zero. |
| `unknown_cache_write_input_tokens_operations` | integer | yes | Rows that may have made a provider request but reported no cache-write input-token count. Missing is unknown, not zero. |
| `unknown_output_bytes_operations` | integer | yes | Rows with no recorded output size in bytes (most non-file rows, such as analysis requests). Unknown is not zero. |
| `reserved_cost_usd` | number | yes | Part of the estimate that is a reservation (usage never reported), in USD. |
| `unknown_request_count_operations` | integer | yes | Rows whose request count is unknown. |
| `id` | string | yes | Run (job) ID. |
| `status` | string | yes | Status of the job with this ID, or `unrecorded` when no such job exists. |
| `kind` | string \| null | yes | Job kind, or null when no such job exists. |
| `created_at` | string \| null | yes | ISO 8601 UTC time of the oldest row, or the job creation time when no rows exist. |
| `has_measurements` | boolean | yes | False for a job with no recorded rows (historical or unmeasured). |

<a id="schema-resourcestageaggregate"></a>
### ResourceStageAggregate

Aggregate of every row in scope with one stage.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `operations` | integer | yes | Rows in the set. |
| `requests` | integer | yes | Sum of known provider request counts. |
| `cached_operations` | integer | yes | Rows that reused saved output. |
| `failed_operations` | integer | yes | Rows with status failed/uncertain/not_sent/interrupted or a rejected validation. |
| `running_operations` | integer | yes | Rows still running or reserved. |
| `elapsed_seconds` | number \| null | yes | Sum of recorded leaf elapsed time in seconds (not end-to-end job latency). |
| `cpu_seconds` | number \| null | yes | Sum of measured current-Python-thread CPU seconds. |
| `audio_seconds` | number \| null | yes | Sum of produced or reused audio duration in seconds. |
| `estimated_cost_usd` | number \| null | yes | Sum of known cost estimates in USD. Not an invoice. |
| `input_tokens` | number \| null | yes | Sum of reported input tokens. |
| `output_tokens` | number \| null | yes | Sum of reported output tokens. |
| `cached_input_tokens` | number \| null | yes | Sum of reported cached input tokens. |
| `cache_write_input_tokens` | number \| null | yes | Sum of reported cache-write input tokens. |
| `output_bytes` | number \| null | yes | Sum of recorded output sizes in bytes. |
| `unknown_elapsed_seconds_operations` | integer | yes | Rows with no recorded elapsed time (for example cache reuse or historical rows). Such rows are left out of `elapsed_seconds`; unknown is not zero. |
| `unknown_cpu_seconds_operations` | integer | yes | Rows with no measured CPU time (CPU is only measured for opted-in local work, so most cloud and cached rows count here). Unknown is not zero. |
| `unknown_audio_seconds_operations` | integer | yes | Rows with no recorded audio duration (including every non-audio row such as analysis requests). Unknown is not zero. |
| `unknown_estimated_cost_usd_operations` | integer | yes | Rows with no USD cost estimate. Such rows are left out of `estimated_cost_usd`; unknown cost is not free. |
| `unknown_input_tokens_operations` | integer | yes | Rows that may have made a provider request (request count not 0, including unknown) but reported no input-token count. Local and cached rows are not counted. |
| `unknown_output_tokens_operations` | integer | yes | Rows that may have made a provider request but reported no output-token count. Local and cached rows are not counted. |
| `unknown_cached_input_tokens_operations` | integer | yes | Rows that may have made a provider request but reported no cached-input-token count. Missing is unknown, not zero. |
| `unknown_cache_write_input_tokens_operations` | integer | yes | Rows that may have made a provider request but reported no cache-write input-token count. Missing is unknown, not zero. |
| `unknown_output_bytes_operations` | integer | yes | Rows with no recorded output size in bytes (most non-file rows, such as analysis requests). Unknown is not zero. |
| `reserved_cost_usd` | number | yes | Part of the estimate that is a reservation (usage never reported), in USD. |
| `unknown_request_count_operations` | integer | yes | Rows whose request count is unknown. |
| `id` | string | yes | Stage name of the rows, or `unrecorded`. |

<a id="schema-resourcesummary"></a>
### ResourceSummary

Recorded resource usage for a book, optionally narrowed to one run. Never contacts a provider.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `schema_version` | integer | yes | Envelope version (currently 1). |
| `book_id` | string | yes | Book ID the usage belongs to. |
| `run_id` | string \| null | yes | The `run_id` filter, or null. |
| `totals` | [ResourceAggregate](#schema-resourceaggregate) | yes | Aggregate over every row in scope (not just this page). |
| `stages` | list of [ResourceStageAggregate](#schema-resourcestageaggregate) | yes | Aggregates by stage over every row in scope. |
| `runs` | list of [ResourceRunAggregate](#schema-resourcerunaggregate) | yes | Per-run aggregates, newest first, at most 100. Includes jobs of the book without rows. |
| `total_runs` | integer | yes | Number of runs before the 100 cap. |
| `operations` | list of [ResourceOperation](#schema-resourceoperation) | yes | This page of rows, newest first. |
| `total_operations` | integer | yes | Rows in scope. |
| `unmeasured_runs` | integer | yes | Runs with no recorded rows. |
| `limit` | integer | yes | Effective page size after clamping to 1–200. |
| `offset` | integer | yes | Effective offset after clamping to >= 0. |
| `price_sources` | list of string | yes | Distinct price-source URLs referenced by rows in scope. |
| `notes` | list of string | yes | Interpretation notes. Display only. |

<a id="schema-runrequest"></a>
### RunRequest

A run to queue. Send the same `steps`, `chapter_ids`, `configs` and `fresh` as the confirmed plan.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `steps` | list of string | yes | Step IDs to run (1–40), executed in pipeline order. Duplicates are ignored. An unknown ID is refused (400 `unknown_step`). (min items `1`; max items `40`) |
| `chapter_ids` | list of string \| null |  | Chapters to limit chapter-scoped steps to (1–2000 IDs of this book). Omit or null for every eligible chapter. An empty list is refused (400 `chapter_ids_empty`). |
| `configs` | map of string → [StepConfig](#schema-stepconfig) \| null |  | `{step ID: StepConfig}` overriding the saved provider/model. Entries for steps not requested are ignored. |
| `fresh` | boolean |  | Request new samples instead of reusing cached validated units (default false). Part of the fingerprint. (default `false`) |
| `mode` | `"serial"` \| `"parallel"` |  | `serial` (default) runs steps one after another in pipeline order. `parallel` starts every step whose in-run inputs have finished, so independent steps overlap. (default `"serial"`) |
| `gates` | map of string → `"auto"` \| `"review"` \| null |  | `{step ID: "auto" \| "review"}` overriding the saved gate for this run. |
| `concurrency` | integer |  | Maximum model requests in flight across the run, 1–4 (default 2). Each step also has its own `parallel` cap. (≥ `1.0`; ≤ `4.0`; default `2`) |
| `limits` | [Limits](#schema-limits) |  | Optional caps; see Limits. Uncapped when omitted. |
| `expected_fingerprint` | string \| null |  | The `fingerprint` of the plan the owner confirmed (up to 64 characters). When sent, the plan is recomputed and a mismatch returns 409 `plan_stale`. Required unless a limit is set. |

<a id="schema-saverequest"></a>
### SaveRequest

Save one draft candidate as a library voice or version.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `candidate_id` | string | yes | An undiscarded candidate of the draft (`c1`, `c2`, …). (max length `20`) |
| `name` | string | yes | Voice name, 1–100 characters (trimmed; must not be only whitespace). For Breeze it is also the server voice name. With `mode: "version"` it is used for the server voice only; the library voice keeps its name. (min length `1`; max length `100`) |
| `mode` | `"new"` \| `"version"` |  | `new` (default) creates a new library voice. `version` adds a new current version to the draft's base voice (only for drafts started with `base_voice_id`). (default `"new"`) |
| `assign` | [Assignment](#schema-assignment) \| null |  | Optional character to assign the saved voice to afterwards. |
| `make_default` | boolean |  | Breeze only: also make the saved voice the Breeze default. Default false. (default `false`) |

<a id="schema-sceneedit"></a>
### SceneEdit

Scene fields to change. Omitted or null fields are ignored; send "" to clear.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `title` | string \| null |  | Display title, 1–200 characters. |
| `summary` | string \| null |  | Summary, at most 4,000 characters. |
| `tone` | string \| null |  | Emotional tone notes, at most 1,000 characters. |
| `direction` | string \| null |  | Performance direction for the scene, at most 3,000 characters. |

<a id="schema-segmentedit"></a>
### SegmentEdit

Passage fields to change. Omitted or null fields are ignored, except `seed`; send "" or [] to clear.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `speaker_id` | string \| null |  | Character ID from this book's cast (including `narrator` or `unassigned`); anything else is 400 `character_not_in_cast`. Sets `confidence` to 1.0. |
| `direction` | string \| null |  | Performance direction for the passage, at most 3,000 characters. |
| `cues` | list of string \| null |  | Complete replacement list of cue labels. |
| `seed` | integer \| null |  | Take seed 0–4294967295 for seeded providers such as Breeze; a new seed is a new take. `null` clears it, so the speaker's Breeze voice seed applies; omitting it keeps the saved seed. Gemini and device narration ignore it. |

<a id="schema-series"></a>
### Series

A series with its supplied books, all volume slots and its identity count.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Series ID (opaque, currently `series_<hex>`). |
| `name` | string | yes | Display name, 1–200 characters, whitespace-collapsed. Unique ignoring case. |
| `created_at` | string | yes | ISO 8601 UTC creation time. |
| `archived` | boolean | yes | True when the series is removed. `listSeries` lists only active series, so this is false there; `getSeriesMap` and the library snapshot with `include_archived=true` can return removed ones. |
| `books` | list of [SeriesBook](#schema-seriesbook) | yes | Supplied, non-removed books in reading order (position, then book ID). |
| `volumes` | list of [SeriesSuppliedVolume](#schema-seriessuppliedvolume) \| [SeriesVolumeSlot](#schema-seriesvolumeslot) | yes | Every slot in reading order: supplied books (including removed ones, with status `archived`) and placeholders. The two element shapes differ: select on `status`. |
| `character_count` | integer | yes | Number of series-level character identities. |

<a id="schema-seriesarchivestate"></a>
### SeriesArchiveState

Result of removing or restoring a series.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Series ID. |
| `archived` | boolean | yes | True after removal, false after restoration. |
| `retained` | boolean | yes | Always true: removal hides the entry but deletes nothing. |

<a id="schema-seriesbook"></a>
### SeriesBook

A supplied book (one with an ebook in the library) placed in a series.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `book_id` | string | yes | Book ID. |
| `position` | number | yes | Reading order within the series: a finite number from 0 through 1,000,000, sent and returned as a JSON number (decimals allow prequels and side stories). Unique among supplied books of one series. |
| `title` | string | yes | Current book title ("Untitled" when the book has none). |
| `author` | string | yes | Current book author; empty when unknown. |
| `archived` | boolean | yes | True when the book itself is removed (archived). Series listings omit removed books from `books`, so this is false there. |

<a id="schema-seriesbookanalysisplan"></a>
### SeriesBookAnalysisPlan

The per-book analysis preview inside a series plan.

Same fields as the classic per-book plan (`AnalysisPlan`) except that `limits` is absent: a series plan states
its limits once, in `limits_per_book`.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `phase` | `"scan"` \| `"profiles"` \| `"direct"` \| `"full"` | yes | Requested phase. |
| `provider` | string | yes | Resolved provider: the request value, or the saved default analysis provider. |
| `scan_model` | string \| null | yes | Configured discovery (fast) model for this provider, or null when none is configured (the run then falls back to the detailed model or the built-in default). |
| `model` | string \| null | yes | Configured detailed model for this provider, or null (the run then uses the built-in default). |
| `requests` | integer | yes | Requests that are not already cached. Always 0 for `local`. Excludes retries and evidence repairs. |
| `cached_units` | integer | yes | Known units that validated cached output would satisfy (only when `resume` is true). |
| `estimated_input_tokens` | integer | yes | Approximate input tokens of the pending requests (0 for `local`). |
| `output_token_allowance` | integer | yes | Sum of the pending requests' output caps (0 for `local`). |
| `estimated_cost_usd` | number \| null | yes | Approximate USD cost of the pending requests; null when a model has no known price; 0 for `local`. Not an invoice. |
| `steps_by_stage` | [AnalysisPlanStageCounts](#schema-analysisplanstagecounts) | yes | Pending requests by stage. Computed for `local` too, although a local run does not send these requests. |
| `coverage` | [AnalysisCoverage](#schema-analysiscoverage) | yes | Same body as `GET /api/books/{book_id}/preprocessing`, with profile freshness computed against the plan's working cast. |
| `future_work_unknown` | boolean | yes | True for `full`: discovery can add profiles and change direction prompts, so the estimate is incomplete. |
| `note` | string | yes | Interpretation caveat. Display only. |
| `limits` | [AnalysisPlanLimits](#schema-analysisplanlimits) \| null |  | Never present in a series plan; see `SeriesPlan.limits_per_book`. |

<a id="schema-seriescharacter"></a>
### SeriesCharacter

A series-level character identity. Names never establish identity; only its links do.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Series character ID (opaque, currently `series_character_<hex>`). |
| `series_id` | string | yes | Owning series ID. |
| `name` | string | yes | Display name, 1–200 characters. Duplicate names are allowed: a shared name is not a shared identity. |
| `created_at` | string | yes | ISO 8601 UTC creation time. |
| `links` | list of [SeriesIdentityLink](#schema-seriesidentitylink) | yes | Confirmed book-character links, ordered by book ID then character ID. May include links from removed books or to characters that no longer exist in their book. |

<a id="schema-seriescharacterlinkrequest"></a>
### SeriesCharacterLinkRequest

The series identity to link a book character to.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `series_character_id` | string \| null |  | A series character ID from the book's series, or null (the default) to unlink. |

<a id="schema-seriescharacterlinkstate"></a>
### SeriesCharacterLinkState

A book character's confirmed series identity.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `character_id` | string | yes | Book-local character ID. |
| `series_character_id` | string | yes | Linked series character ID. |
| `name` | string | yes | Series character name (not the book character name). |
| `confirmed_at` | string | yes | ISO 8601 UTC time the link was confirmed. |
| `stale` | boolean | yes | True when the book no longer has a character with `character_id` (for example after a merge). A stale link is ignored by series context. Always false in a link response. |

<a id="schema-seriescharacterunlinked"></a>
### SeriesCharacterUnlinked

Result of removing a book character's series identity link.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `character_id` | string | yes | Book-local character ID from the path. |
| `linked` | `false` | yes | Always false. |

<a id="schema-seriescontextcharacter"></a>
### SeriesContextCharacter

Earlier-volume evidence for one linked character of this book.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `character_id` | string | yes | Character ID in the requested book. |
| `series_character_id` | string | yes | Series identity the character is linked to. |
| `name` | string | yes | Series identity name. |
| `observations` | list of [SeriesContextObservation](#schema-seriescontextobservation) | yes | Included observations, ordered by reading order, book, chapter and offset. When a character has more than the per-character cap, an even sample of early, middle and late evidence is kept. |

<a id="schema-seriescontextobservation"></a>
### SeriesContextObservation

One source-validated observation of a linked character from an earlier volume.

Its quote was rechecked against the earlier book's current chapter text at
`start`/`end` (zero-based Unicode code-point offsets, exclusive end).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Stable observation ID (content hash). |
| `book_id` | string | yes | Earlier book the evidence comes from. |
| `character_id` | string | yes | Character ID local to that earlier book. |
| `chapter_id` | string | yes | Chapter ID in the earlier book. |
| `segment_id` | string \| null | yes | Passage containing the evidence, or null when none overlapped. |
| `start` | integer | yes | Chapter-local start offset in Unicode code points. |
| `end` | integer | yes | Chapter-local exclusive end offset in Unicode code points. |
| `quote` | string | yes | Exact source text at `start`..`end`. |
| `kind` | `"profile_evidence"` \| `"dialogue"` | yes | `profile_evidence`: evidence cited for a character profile. `dialogue`: a line attributed to the character. Name mentions are excluded: a mention is not proof of presence. |
| `description` | string | yes | Profile description recorded with the evidence; empty for dialogue. |
| `direction` | string | yes | Profile performance direction recorded with the evidence; empty for dialogue. |
| `provider` | string \| null | yes | Provider that produced the observation (`local` for local rules), or null. |
| `model` | string \| null | yes | Model that produced the observation, or null. |
| `confidence` | number \| null | yes | Attribution confidence from 0 to 1 when recorded, else null. |
| `book_title` | string | yes | Title of the earlier book. |
| `position` | number | yes | The earlier book's reading order. |
| `chapter_title` | string | yes | Title of the chapter in the earlier book. |
| `chapter_index` | integer | yes | Zero-based index of the chapter in the earlier book. |

<a id="schema-seriescontextseries"></a>
### SeriesContextSeries

The series a context was read from.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Series ID. |
| `name` | string | yes | Series name. |

<a id="schema-seriescreated"></a>
### SeriesCreated

A newly created series. Unlike `Series`, it carries no `archived` or `volumes` field.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | New series ID. |
| `name` | string | yes | Stored name after whitespace collapsing. |
| `created_at` | string | yes | ISO 8601 UTC creation time. |
| `books` | list of [SeriesBook](#schema-seriesbook) | yes | Always empty. |
| `character_count` | integer | yes | Always 0. |

<a id="schema-seriesidentitylink"></a>
### SeriesIdentityLink

A confirmed link from one book-local character to this series identity.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `book_id` | string | yes | Book containing the linked character. |
| `character_id` | string | yes | Book-local character ID. Book character IDs are not series identities. |
| `confirmed_at` | string | yes | ISO 8601 UTC time the link was confirmed. Re-linking the same pair keeps the original time. |

<a id="schema-seriesjoblimits"></a>
### SeriesJobLimits

The analysis allowance a series run was started with, applied to each book separately.

Request and token caps count that book's `analyze` child job (both of its
stages in a `full` run). The dollar ceiling counts every tracked attempt
for the book, including earlier runs. Reaching any cap stops the book with
`budget_limited` and stops the series.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `max_requests` | integer | yes | Maximum provider requests (1–1000). |
| `max_input_tokens` | integer | yes | Maximum input tokens (1,000–10,000,000). |
| `max_output_tokens` | integer | yes | Maximum output tokens (1,000–2,000,000). |
| `budget_usd` | number \| null | yes | Estimated USD ceiling (above 0, at most 1000), or null for no dollar cap. |

<a id="schema-seriesmap"></a>
### SeriesMap

A series with its volumes and explicit identities.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `series` | [Series](#schema-series) | yes | The series entry, including supplied, missing and planned volumes. |
| `characters` | list of [SeriesCharacter](#schema-seriescharacter) | yes | Series identities with their confirmed links. |
| `note` | string | yes | Fixed explanatory sentence about identity links and absent volumes. Display only. |

<a id="schema-seriesmembership"></a>
### SeriesMembership

Where a book sits in its series.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `series_id` | string | yes | Series ID. |
| `series_name` | string | yes | Series name. |
| `position` | number | yes | Reading order within the series: a finite number from 0 through 1,000,000, sent and returned as a JSON number (decimals allow prequels and side stories). Unique among supplied books of one series. |

<a id="schema-seriesmembershiprequest"></a>
### SeriesMembershipRequest

A book's series placement. Send both values null (or `{}`) to detach the book.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `series_id` | string \| null |  | Target series ID, or null to detach. |
| `position` | number \| null |  | Reading order as a JSON number (a numeric string is refused with 422): finite, 0 through 1,000,000; decimals allow prequels and side stories. Required when `series_id` is set; must be null when it is not. |

<a id="schema-seriesnamerequest"></a>
### SeriesNameRequest

A name for a series or a series character identity.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | yes | 1–200 characters. Runs of whitespace are collapsed to one space and the ends trimmed; a name that is blank after trimming is refused (400 `name_invalid`). (min length `1`; max length `200`) |

<a id="schema-seriesplan"></a>
### SeriesPlan

A preview of staged analysis over a series' supplied, active books.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `series_id` | string | yes | Series ID. |
| `name` | string | yes | Series name. |
| `provider` | `"gemini"` \| `"openai"` \| `"anthropic"` | yes | Cloud analysis provider that would run. |
| `model` | string | yes | Analysis model for profiles and direction, from runtime settings. |
| `scan_model` | string | yes | Discovery (scan) model, from runtime settings. |
| `phase` | `"scan"` \| `"profiles"` \| `"direct"` \| `"full"` | yes | Requested phase. |
| `concurrency` | integer | yes | Discovery workers that would run (1 or 2). Always 1 for `profiles` and `direct`, which run in reading order. |
| `books` | list of [SeriesPlanBook](#schema-seriesplanbook) | yes | Supplied, active books in reading order. May be empty. |
| `volumes` | list of [SeriesSuppliedVolume](#schema-seriessuppliedvolume) \| [SeriesVolumeSlot](#schema-seriesvolumeslot) | yes | The series' volume slots, as in `Series.volumes`. |
| `limits_per_book` | [AnalysisPlanLimits](#schema-analysisplanlimits) | yes | Limits applied separately to each book; total possible spend scales with the number of books. |
| `requests` | integer | yes | Sum of known pending requests across books (excludes retries, evidence repairs and work discovered during a full run). |
| `estimated_cost_usd` | number \| null | yes | Sum of per-book estimates in USD, or null when any book estimate is unknown. Approximate; not an invoice. |
| `notes` | list of string | yes | Human-readable caveats. Display only. |
| `plan_fingerprint` | string | yes | SHA-256 hex digest over the plan and each book's revision, source hash and series-context fingerprint. Send it as `expected_plan_fingerprint` to start exactly this scope. |

<a id="schema-seriesplanbook"></a>
### SeriesPlanBook

One supplied book in a series plan.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `book_id` | string | yes | Book ID. |
| `title` | string | yes | Book title. |
| `position` | number | yes | Reading order within the series: a finite number from 0 through 1,000,000, sent and returned as a JSON number (decimals allow prequels and side stories). Unique among supplied books of one series. |
| `plan` | [SeriesBookAnalysisPlan](#schema-seriesbookanalysisplan) | yes | The per-book analysis preview for this book, computed with `resume` true and no chapter scope. |

<a id="schema-seriesprocessingrequest"></a>
### SeriesProcessingRequest

Scope of a series analysis preview or run. Send the same body to preview and to start, adding the reviewed fingerprint when starting.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | string \| null |  | Cloud analysis provider: `gemini`, `openai` or `anthropic`. Null uses the configured analysis provider, which must itself be a cloud provider. Other values are refused (400 `provider_not_cloud`). |
| `phase` | `"scan"` \| `"profiles"` \| `"direct"` \| `"full"` |  | `scan` (default): discovery only. `profiles`: refine character profiles from retained evidence and confirmed earlier-series context. `direct`: performance direction. `full`: all three in order. (default `"scan"`) |
| `concurrency` | integer |  | Parallel discovery workers, 1 or 2 (default 2). Profiles and direction always run one book at a time in reading order. (≥ `1.0`; ≤ `2.0`; default `2`) |
| `limits` | [AnalysisLimits](#schema-analysislimits) |  | Per-book analysis limits, applied separately to each supplied book. |
| `expected_plan_fingerprint` | string \| null |  | The `plan_fingerprint` from the reviewed preview (at most 64 characters). Used only by the start route; when present and different from the recomputed plan, nothing is queued (409 `plan_stale`). Omitting it skips the check. Ignored by the preview route. |

<a id="schema-seriesrenamed"></a>
### SeriesRenamed

Result of renaming a series.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Series ID. |
| `name` | string | yes | Stored name after whitespace collapsing. |

<a id="schema-seriesrun"></a>
### SeriesRun

A series parent job with its child book jobs.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Job ID (32 hex characters). |
| `book_id` | string | yes | The book the job works on, or `series:<series id>` for a `series` parent job. Pass this value as `book_id` to `GET /api/jobs` to list a book's or series' jobs. |
| `kind` | `"render"` \| `"analyze"` \| `"pipeline"` \| `"series"` \| `"listen"` \| `"listen_chapter"` \| `"voice_preview"` \| `"performance"` | yes | What the job does; see the table above. |
| `status` | `"queued"` \| `"running"` \| `"completed"` \| `"failed"` \| `"cancelled"` \| `"interrupted"` \| `"budget_limited"` \| `"quota_limited"` | yes | `queued` and `running` are active; every other value is terminal. See the lifecycle above. |
| `progress` | integer | yes | Units completed so far (kind-specific units). |
| `total` | integer | yes | Units planned; 0 when not yet known. |
| `message` | string | yes | Human-readable progress or outcome text. Display it; do not parse it. |
| `error` | string \| null | yes | Human-readable failure text (at most 1,200 characters, credentials redacted), or null. Usually set with `failed`; a series child that stopped also carries it with `budget_limited` or `cancelled`. |
| `created_at` | string | yes | Creation time: ISO 8601 UTC timestamp with offset, for example `2026-09-28T17:04:05.123456+00:00`. |
| `updated_at` | string | yes | Time of the last change: ISO 8601 UTC timestamp with offset, for example `2026-09-28T17:04:05.123456+00:00`. |
| `cancel_requested` | boolean | yes | True after a cancel request. A running job stops at the next safe boundary; requests already sent to a provider can still finish and be billed. |
| `provider` | `"local"` \| `"gemini"` \| `"openai"` \| `"anthropic"` \| `"system"` \| `"breeze"` \| null |  | Analysis provider (`analyze`, `series`: local, gemini, openai, anthropic) or narration provider (`listen`, `voice_preview`, `performance`: system, gemini, breeze; `listen_chapter`: gemini). |
| `model` | string \| null |  | Model snapshotted when the job was queued: the analysis model (null for local analysis) or the speech model (`macos-say` for device narration). `render` jobs do not record it. |
| `scan_model` | string \| null |  | Preprocessing (scan) model for `analyze` and `series`; null for local analysis. |
| `phase` | `"scan"` \| `"profiles"` \| `"direct"` \| `"full"` \| `"simple_listen"` \| `"chapter_listen"` \| `"voice_preview"` \| `"performance"` \| null |  | `analyze`/`series`: the classic analysis phase (a series child switches to `scan` during discovery). Narration kinds carry a fixed label: `simple_listen`, `chapter_listen`, `voice_preview`, `performance`. |
| `mode` | `"simple"` \| `"cast"` \| null |  | `performance` only: `simple` (one narrator) or `cast` (character voices). |
| `chapter_id` | string \| null |  | `analyze`: the single chapter analyzed, or null for the whole book. `listen_chapter`: the chapter. |
| `segment_id` | string \| null |  | `listen`: the passage. `voice_preview`: the source passage, or null for demo text. |
| `session_id` | string \| null |  | `listen`, `listen_chapter`: the narrator session (64 hex). |
| `audio` | [ListeningPassageAudio](#schema-listeningpassageaudio) \| [ListeningChunkClipAudio](#schema-listeningchunkclipaudio) \| [VoicePreviewAudio](#schema-voicepreviewaudio) \| null |  | The finished audio, set just before a `listen` job (a passage take or chunk clip) or a `voice_preview` job (VoicePreviewAudio) completes. Absent until then and after a failure. |
| `resume_after` | string \| null |  | `quota_limited` only: when the daily quota resets (next midnight Pacific time), as ISO 8601 UTC timestamp with offset, for example `2026-09-28T17:04:05.123456+00:00`. |
| `run_id` | string \| null |  | `pipeline`: the pipeline run this job executes. |
| `steps` | list of string \| null |  | `pipeline`: step IDs in the run, including required upstream steps. |
| `scheduling` | `"serial"` \| `"parallel"` \| null |  | `pipeline` only: `serial` (one step at a time) or `parallel` (independent steps together), as requested by the run's `mode` field. |
| `series_id` | string \| null |  | `series` parent and its `analyze` children: the series. |
| `series_run_id` | string \| null |  | `analyze` series child: the parent `series` job ID. |
| `position` | number \| null |  | `analyze` series child: the book's reading-order position in the series. |
| `book_ids` | list of string \| null |  | `series`: the books processed, in reading order (missing volumes excluded). |
| `child_job_ids` | list of string \| null |  | `series`: one `analyze` job per book, in reading order. `performance`: the `listen_chapter` jobs started so far (Gemini simple performances only; empty otherwise). |
| `concurrency` | integer \| null |  | `series`: parallel discovery workers (1–2; 1 for phases without discovery). |
| `analysis_limits` | [SeriesJobLimits](#schema-seriesjoblimits) \| null |  | `series` only: the analysis allowance the run was started with, applied to each book. |
| `finished_at` | string \| null |  | `series`: when the collection run ended (set on completion, cancellation after start, or a start failure), as ISO 8601 UTC timestamp with offset, for example `2026-09-28T17:04:05.123456+00:00`. |
| `performance_id` | string \| null |  | `performance`: the saved performance being prepared. |
| `child_job_id` | string \| null |  | `performance`: the `listen_chapter` job currently running, or null between chapters. |
| `parent_id` | string \| null |  | `listen_chapter` started by a performance: the parent `performance` job. Such a job cannot be joined by live chapter listening; cancelling the parent cancels it. |
| `preview_id` | string \| null |  | `voice_preview`: the preview ID. |
| `preview` | [VoicePreview](#schema-voicepreview) \| null |  | `voice_preview`: the preview request being rendered. |
| `voice` | string \| null |  | `listen_chapter`: the Gemini voice. |
| `intent` | `"play"` \| `"queue"` \| null |  | `listen_chapter`: `play` (someone is waiting; the first requests are short) or `queue` (prepare ahead; every request is full size). Performances use `queue`. |
| `scope_start_segment_id` | string \| null |  | `listen_chapter`: first passage of the prepared range (to the chapter end). Joining at an earlier passage moves it back. |
| `focus_segment_id` | string \| null |  | `listen_chapter`: the passage the listener is at; generation proceeds from here first. |
| `chunking` | [ChapterListenChunking](#schema-chapterlistenchunking) \| null |  | `listen_chapter`: the chunk settings in use. |
| `speech_limits` | [ChapterListenLimits](#schema-chapterlistenlimits) \| null |  | `listen_chapter` only: the Gemini speech limits snapshotted for the model when the job was queued. |
| `ramp_restart` | integer \| null |  | `listen_chapter`: times a `play` join restarted the short first-request ramp. |
| `joins` | integer \| null |  | `listen_chapter`: times another request joined this job instead of starting one. |
| `chunks` | list of [JobChapterChunk](#schema-jobchapterchunk) \| null |  | `listen_chapter`: every request sent so far, in order, with its outcome. |
| `calibration` | [ChapterListenCalibration](#schema-chapterlistencalibration) \| null |  | `listen_chapter`: speech-rate calibration, carried over from the session's previous job and updated as chunks finish. |
| `projection` | list of [ChapterListenChunkPlan](#schema-chapterlistenchunkplan) \| null |  | `listen_chapter`: the remaining requests planned from the current state; empty when stopping or done. Absent until the worker first reports. |
| `quota` | [ChapterListenQuota](#schema-chapterlistenquota) \| null |  | `listen_chapter`: daily quota use at the last report. |
| `waiting_seconds` | number \| null |  | `listen_chapter`: seconds the next send waits for the per-minute rate limit, or null when not waiting. |
| `closing` | boolean \| null |  | `listen_chapter`: true once the worker decided to finish; a new chapter request then gets 409 until the job ends. |
| `children` | list of [Job](#schema-job) | yes | The child `analyze` jobs, one per supplied book, in reading order. They use real book IDs. |

<a id="schema-seriesruns"></a>
### SeriesRuns

Recent series runs.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `runs` | list of [SeriesRun](#schema-seriesrun) | yes | Up to 20 parent runs, newest first. |

<a id="schema-seriessuppliedvolume"></a>
### SeriesSuppliedVolume

A volume slot filled by a supplied book.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `book_id` | string | yes | Book ID of the supplied book. |
| `position` | number | yes | Reading order within the series: a finite number from 0 through 1,000,000, sent and returned as a JSON number (decimals allow prequels and side stories). Unique among supplied books of one series. |
| `title` | string | yes | Current book title. |
| `author` | string | yes | Current book author; empty when unknown. |
| `archived` | boolean | yes | True when the book is removed (archived). |
| `status` | `"available"` \| `"archived"` | yes | `available` for an active book, `archived` for a removed one. Removed books still appear in `volumes` (they keep their reading order) although they are omitted from `books`. |

<a id="schema-seriesvolumeremoval"></a>
### SeriesVolumeRemoval

Result of removing a volume placeholder.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `series_id` | string | yes | Series ID. |
| `position` | number | yes | The position that was cleared, as a number. |
| `removed` | boolean | yes | Always true, even when no placeholder was at that position. |

<a id="schema-seriesvolumerequest"></a>
### SeriesVolumeRequest

A placeholder for a volume the library does not have.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `position` | number | yes | Required reading order, a finite number from 0 through 1,000,000. Must not be used by a supplied book of the series (including a removed one). An existing placeholder at this position is replaced. (≥ `0.0`; ≤ `1000000.0`) |
| `title` | string |  | Optional label, at most 500 characters; default empty. Whitespace is collapsed; control characters other than tab and newlines are refused. (max length `500`; default `""`) |
| `status` | `"missing"` \| `"planned"` |  | `missing` (default): the volume exists but is not in the library. `planned`: not yet available. (default `"missing"`) |

<a id="schema-seriesvolumeslot"></a>
### SeriesVolumeSlot

A placeholder for a volume the library does not have. It holds a reading order, never text.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `series_id` | string | yes | Series ID. |
| `position` | number | yes | Reading order within the series: a finite number from 0 through 1,000,000, sent and returned as a JSON number (decimals allow prequels and side stories). Unique among supplied books of one series. Placeholders never share a position with a supplied book. |
| `title` | string | yes | Optional label; empty string when none was given. |
| `status` | `"missing"` \| `"planned"` | yes | `missing`: the volume exists but is not in the library. `planned`: not yet published or acquired. Neither contributes knowledge to analysis. |
| `book_id` | null | yes | Always null: a placeholder has no book. |

<a id="schema-settingsrequest"></a>
### SettingsRequest

A partial settings update. Every field is optional; omitted fields stay unchanged. Unknown fields are refused (422).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `api_key` | string \| null |  | Compatibility alias for `api_keys.gemini`. Runtime only, never saved; surrounding whitespace is removed and an empty string clears the key. Up to 500 characters. |
| `tts_model` | string \| null |  | Gemini speech model; must be one of `tts_models` from status. Saved. |
| `analysis_model` | string \| null |  | Compatibility alias for `analysis_models_by_provider.gemini`. Saved. |
| `api_keys` | map of string → string \| null |  | Runtime API keys by cloud provider (`gemini`, `openai`, `anthropic`), up to 500 characters each. Never saved: they last until restart. Whitespace is trimmed; an empty string clears that key; providers not included keep their key. A different Gemini key lifts every daily quota block. |
| `analysis_models_by_provider` | map of string → string \| null |  | Analysis model per cloud provider (`gemini`, `openai`, `anthropic`). IDs are 1–200 characters of letters, digits, `.`, `_`, `:` or `-`, starting with a letter or digit; they need not be in the curated list. Saved. |
| `preprocess_models_by_provider` | map of string → string \| null |  | Preprocessing (scan) model per cloud provider, same ID rules. Saved. |
| `analysis_provider` | string \| null |  | Default classic-analysis provider: `local`, `gemini`, `openai` or `anthropic`. Saved. |
| `tts_limits` | map of string → [TtsLimitsUpdate](#schema-ttslimitsupdate) \| null |  | Gemini speech limits by TTS model (each a key of `tts_models`): `{model: {rpm, tpm, rpd}}`. Each limit given replaces that limit; a limit left out (or null) keeps its current value. Models not included keep their limits. Changing a model's limits lifts its daily quota block. Saved. |
| `listen_chunking` | [ChunkingOptions](#schema-chunkingoptions) \| null |  | Default chapter-listening chunk settings. Fields given are merged over the saved values and the result is validated. Saved. |
| `breeze_url` | string \| null |  | Breeze server root: `http://` or `https://` host and optional port, without path, query, fragment or credentials, up to 500 characters; a trailing slash is removed. Saved. An empty string clears the saved URL, so the server's `BREEZE_TTS_URL` environment variable applies again (if set). The saved Breeze check stays valid only for the URL it was made with. |
| `breeze_api_key` | string \| null |  | Breeze API key. Runtime only, never saved; whitespace trimmed; an empty string clears it. |
| `local_service_urls` | map of string → string \| null |  | Self-hosted analysis server roots by service ID (`local_llm`, `booknlp`, `novel_analyzer`), each an http(s) root without path or credentials, up to 500 characters. An empty string clears it and also overrides its environment variable. Services not included keep their value; a service never set in Settings uses its environment variable, which is never saved. Saved. |

<a id="schema-status"></a>
### Status

Runtime status and preferences. Never contains key values and never contacts a provider.

Preference fields are those saved by `POST /api/settings`; the rest are
derived at request time.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `has_api_key` | boolean | yes | True when a Gemini API key is loaded (from Settings or the environment). |
| `tts_model` | string | yes | Selected Gemini speech model; one of `tts_models`. |
| `analysis_provider` | `"local"` \| `"gemini"` \| `"openai"` \| `"anthropic"` | yes | Default provider for classic analysis. |
| `analysis_model` | string | yes | Compatibility alias of `analysis_models_by_provider.gemini`. |
| `analysis_models_by_provider` | map of string → string | yes | Selected analysis model per cloud provider (always all three). |
| `preprocess_models_by_provider` | map of string → string | yes | Selected preprocessing (scan) model per cloud provider (always all three). |
| `tts_limits` | map of string → [ChapterListenLimits](#schema-chapterlistenlimits) | yes | Gemini speech limits per TTS model (one entry for each of `tts_models`). |
| `listen_chunking` | [ChapterListenChunking](#schema-chapterlistenchunking) | yes | Default chapter-listening chunk settings. |
| `breeze_url` | string | yes | The Breeze server root in use, or an empty string: the URL saved by `POST /api/settings` when it is not empty, otherwise the server's `BREEZE_TTS_URL` environment variable. The environment value is never saved. |
| `local_service_urls` | [LocalServiceUrls](#schema-localserviceurls) | yes | Resolved self-hosted server URLs: the Settings value when one was saved (even an empty string), otherwise the environment variable. |
| `narration_defaults` | [VoiceLibraryDefaults](#schema-voicelibrarydefaults) | yes | Default library voice per narration provider. |
| `providers` | list of [StatusNarrationAvailability](#schema-statusnarrationavailability) | yes | Narration providers in order system, gemini, breeze, with availability. |
| `narration_providers` | map of string → [NarrationProviderInfo](#schema-narrationproviderinfo) | yes | Static narration provider contracts keyed by provider ID. |
| `breeze` | [VoiceLibraryBreezeStatus](#schema-voicelibrarybreezestatus) | yes | The last Breeze server check, read without contacting the server. |
| `analysis_providers` | list of [AnalysisProviderStatus](#schema-analysisproviderstatus) | yes | Analysis providers in order local, gemini, openai, anthropic. |
| `account_checks` | map of string → [AccountCheck](#schema-accountcheck) | yes | Last account check per cloud provider, valid only for the current key and model; otherwise `unchecked` or `missing_key`. |
| `model_catalogs` | map of string → [AnalysisModelCatalog](#schema-analysismodelcatalog) | yes | Analysis model choices per cloud provider: the last refresh for the current key, else the curated list. |
| `system_voices` | list of [VoiceLibrarySystemVoice](#schema-voicelibrarysystemvoice) | yes | Installed macOS voices; empty when unavailable. |
| `tts_models` | list of string | yes | Supported Gemini speech models. |
| `tts_rate` | map of string → [TtsRateState](#schema-ttsratestate) | yes | Live rate-limiter state per Gemini speech model. |
| `timing_kind` | `"segment"` | yes | Granularity of read-along timing: per passage (segment). |

<a id="schema-statusnarrationavailability"></a>
### StatusNarrationAvailability

Whether a narration provider can be used right now.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | `"system"` \| `"gemini"` \| `"breeze"` | yes | Narration provider ID: `system` (macOS device voices), `gemini` (cloud) or `breeze` (self-hosted server). |
| `label` | string | yes | Display name. |
| `available` | boolean | yes | `system`: macOS voices and ffmpeg are installed. `gemini`: a Gemini key is loaded. `breeze`: a server URL is configured and the last check found at least one usable voice. |
| `reason` | string \| null |  | `breeze` only: why it is unavailable, or null when available. |

<a id="schema-stepconfig"></a>
### StepConfig

A provider and model for one step, overriding its saved settings for this request. For a local (plain) step only `provider: "local"` with no model is accepted. A service provider (`booknlp`, `novel_analyzer`) takes no model. A model provider needs a model ID matching `[A-Za-z0-9][A-Za-z0-9._:-]{0,199}`.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | string | yes | Provider ID: `local` for plain steps, otherwise one of the step's `providers` (1–40 characters). (min length `1`; max length `40`) |
| `model` | string \| null |  | Model ID for a model provider (up to 200 characters); omit or null for local steps and service providers. |

<a id="schema-stepsettings"></a>
### StepSettings

A step's saved provider, model and gate. For a local (plain) step only `provider: "local"` with no model is accepted. A service provider (`booknlp`, `novel_analyzer`) takes no model. A model provider needs a model ID matching `[A-Za-z0-9][A-Za-z0-9._:-]{0,199}`.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | string | yes | Provider ID: `local` for plain steps, otherwise one of the step's `providers`. (min length `1`; max length `40`) |
| `model` | string \| null |  | Model ID for a model provider; omit or null for local steps and service providers. |
| `gate` | `"auto"` \| `"review"` \| null |  | `auto` accepts a completed run immediately; `review` waits for a person. Omitted or null saves the step's `default_gate`. |

<a id="schema-storymap"></a>
### StoryMap

A typed graph of the book: book→chapters→scenes→passages, reading order and speaker attributions.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `schema_version` | integer | yes | Envelope version (currently 1). |
| `book_id` | string | yes | Book ID of the mapped book. |
| `chapters` | list of [StoryMapChapter](#schema-storymapchapter) | yes | Every chapter of the book in book order, with its scenes. |
| `characters` | list of [StoryMapCharacter](#schema-storymapcharacter) | yes | Every cast member, including `narrator` and `unassigned`. |
| `nodes` | list of [StoryMapNode](#schema-storymapnode) | yes | Graph nodes: one book node, then per chapter its chapter, scene and passage nodes, then one node per cast member. Unpaginated. |
| `edges` | list of [StoryMapEdge](#schema-storymapedge) | yes | Graph edges (`contains`, `next`, `attributed_speaker`) between node IDs. Unpaginated. |
| `references` | list of [StoryMapReference](#schema-storymapreference) | yes | All saved character references for the book, unpaginated. |
| `reference_counts` | [StoryMapReferenceCounts](#schema-storymapreferencecounts) | yes |  |
| `note` | string | yes | Interpretation caveat. Display only. |

<a id="schema-storymapchapter"></a>
### StoryMapChapter

A chapter with its source range and scenes.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Chapter ID. |
| `title` | string | yes | Chapter title as stored in the book. |
| `kind` | string | yes | `chapter`, `recap`, `section`, `front_matter` or `back_matter` (`section` when unrecorded). |
| `start` | integer | yes | Always 0. |
| `end` | integer | yes | Chapter length in code points. |
| `source_artifact_id` | string \| null | yes | Current `source` artifact for the chapter text, or null. |
| `logical_sections` | list of [StoryMapLogicalSection](#schema-storymaplogicalsection) | yes | EPUB contents entries that fall inside this chapter, in order; empty for books without EPUB navigation (for example plain-text imports). |
| `scenes` | list of [StoryMapScene](#schema-storymapscene) | yes | Scenes of this chapter in stored order; empty before scene analysis. |

<a id="schema-storymapcharacter"></a>
### StoryMapCharacter

A cast member.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Book-local character ID (not a series identity). |
| `name` | string | yes | Display name of the cast member. The cast includes the built-in `narrator` and `unassigned` entries. |

<a id="schema-storymapedge"></a>
### StoryMapEdge

A typed graph edge.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `from` | string | yes | Source node ID. |
| `to` | string | yes | Target node ID. Every edge ends at a node in `nodes`. |
| `type` | `"contains"` \| `"next"` \| `"attributed_speaker"` | yes | `contains`: book→chapter, chapter→scene, scene (or chapter)→passage. `next`: reading order between passages of a chapter. `attributed_speaker`: dialogue passage→character (an attribution, not presence); omitted when the passage names a speaker that is no longer in the cast. |
| `order` | integer \| null |  | `contains` edges: zero-based position within the parent. |
| `confidence` | number \| null |  | `attributed_speaker` edges: attribution confidence 0–1, or null. |

<a id="schema-storymaplogicalsection"></a>
### StoryMapLogicalSection

A contents-entry section inside one chapter (EPUB navigation).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `title` | string | yes | Contents-entry label from the EPUB navigation, non-empty, truncated to 300 characters. |
| `start` | integer | yes | Code-point offset into the chapter text (inclusive). |
| `end` | integer | yes | Code-point offset (exclusive). |
| `kind` | string | yes | Section type, as for chapters. |
| `depth` | integer | yes | Navigation nesting depth (0 = top). |
| `title_source` | string | yes | Where the title came from, e.g. `epub_nav` or `epub_ncx`. |

<a id="schema-storymapnode"></a>
### StoryMapNode

A typed graph node. IDs are `<book_id>:<type>:<local id>`, so they are unique across books.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Graph node ID `<book_id>:<type>:<local id>`, where the local ID is the book, chapter, scene, passage or book-local character ID. Referenced by edge `from`/`to`. |
| `type` | `"book"` \| `"chapter"` \| `"scene"` \| `"passage"` \| `"character"` | yes | Node type; decides which of the optional ID fields below are present. |
| `book_id` | string \| null |  | `book` nodes. |
| `chapter_id` | string \| null |  | `chapter` and `passage` nodes. |
| `source_artifact_id` | string \| null |  | `chapter` nodes: current source artifact, or null. |
| `scene_id` | string \| null |  | `scene` nodes. |
| `passage_id` | string \| null |  | `passage` nodes. |
| `source_anchor` | [StoryMapSourceAnchor](#schema-storymapsourceanchor) \| null |  | `passage` nodes: verified anchor, or null when the passage text does not match its offsets. |
| `character_id` | string \| null |  | `character` nodes. |
| `name` | string \| null |  | `character` nodes. |

<a id="schema-storymapreference"></a>
### StoryMapReference

A source reference to a character. Kinds are distinct evidence and must not be merged.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Stable reference ID (hash of character, chapter, range and kind). |
| `character_id` | string | yes | Book-local character ID the reference is about (not a series identity). |
| `chapter_id` | string | yes | Chapter ID whose text `start`/`end` index into. |
| `segment_id` | string \| null | yes | First passage overlapping the range, or null. |
| `start` | integer | yes | Code-point offset into the chapter text (inclusive). |
| `end` | integer | yes | Code-point offset (exclusive). |
| `quote` | string | yes | The exact source text of the range. |
| `kind` | `"mention"` \| `"dialogue"` \| `"profile_evidence"` | yes | `mention`: a name/alias match (not presence). `dialogue`: an attributed dialogue passage. `profile_evidence`: a quotation a model cited as evidence. |
| `confidence` | number \| null |  | Attribution confidence for `dialogue`; null otherwise. Current writers always include it; stored references from other sources may omit it. |
| `provider` | string \| null | yes | `local` for mentions, `reviewed` for reviewed dialogue, else the analysis provider. |
| `model` | string \| null | yes | Model that produced it, or null. |
| `profile_description` | string \| null |  | `profile_evidence`: the description the model gave with this evidence. |
| `profile_direction` | string \| null |  | `profile_evidence`: the direction the model gave. |

<a id="schema-storymapreferencecounts"></a>
### StoryMapReferenceCounts

Reference counts by kind.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `mention` | integer | yes | Number of `mention` references (name/alias matches) in `references`. |
| `dialogue` | integer | yes | Number of `dialogue` references (attributed dialogue passages). |
| `profile_evidence` | integer | yes | Number of `profile_evidence` references (model-cited evidence quotations). |

<a id="schema-storymapscene"></a>
### StoryMapScene

A scene with its passages and attributed speakers.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Scene ID. |
| `title` | string | yes | Scene title from analysis (local draft or model direction); empty string when the scene has none. |
| `start` | integer \| null | yes | Smallest passage start offset, or null when no passage has offsets. |
| `end` | integer \| null | yes | Largest passage end offset, or null. |
| `passage_ids` | list of string | yes | Passages in the scene, in order. |
| `character_ids` | list of string | yes | Attributed dialogue speakers that are cast members (excluding narrator/unassigned), sorted. Not proof of physical presence. |

<a id="schema-storymapsourceanchor"></a>
### StoryMapSourceAnchor

A verified location of a passage in a retained source artifact.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `artifact_id` | string \| null | yes | The chapter's `source` artifact. |
| `start` | integer | yes | Code-point offset (inclusive). |
| `end` | integer | yes | Code-point offset (exclusive). |

<a id="schema-ttslimitsupdate"></a>
### TtsLimitsUpdate

Gemini speech limits for one model. Each limit is optional: omitted or null keeps the current value. Whole JSON numbers only (strict: no strings or booleans); unknown names are refused (422).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `rpm` | integer \| null |  | Requests per minute, 1–10,000. Default 10. |
| `tpm` | integer \| null |  | Estimated input tokens per minute, 1–100,000,000. Default 10,000. |
| `rpd` | integer \| null |  | Requests per day (quota day ends at midnight Pacific time), 1–10,000,000. Default 100. |

<a id="schema-ttsratestate"></a>
### TtsRateState

Live state of this server's Gemini speech rate limiter for one model (memory only).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `recent_requests` | integer | yes | Requests sent in the last 61 seconds. |
| `recent_input_tokens` | integer | yes | Estimated input tokens sent in the last 61 seconds. |
| `cooldown_seconds` | number | yes | Seconds left in a cooldown after a provider rate-limit response; 0 when none. |
| `daily_block_seconds` | number | yes | Seconds until the daily quota block lifts (midnight Pacific time); 0 when not blocked. A settings change to this model's limits, or to the Gemini key, lifts it early; other settings changes keep it. |

<a id="schema-validationissue"></a>
### ValidationIssue

One FastAPI request-validation problem.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `loc` | list of string \| integer | yes | Location of the invalid value, for example ["body", "limits", "max_requests"]. |
| `msg` | string | yes | Human-readable reason. |
| `type` | string | yes | Stable Pydantic error type, for example "missing" or "extra_forbidden". |
| `input` | any |  | The rejected input value, echoed back. Absent for the diagnostics endpoint, which never echoes input. |
| `ctx` | object \| null |  | Error-specific context, for example `{"le": 1000}` for a bound. |

<a id="schema-voicecharactercontext"></a>
### VoiceCharacterContext

The book character a draft or voice was made for.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `book_id` | string | yes | The book the character belongs to. |
| `character_id` | string | yes | Book-local character ID (not a series identity). |
| `character_name` | string \| null | yes | The character's name when the draft or voice was made; a snapshot, not updated by later renames. Null if the character had no name. |

<a id="schema-voicechoice"></a>
### VoiceChoice

One provider's voice choice. Exactly one of `id` or `library` (otherwise 422).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string \| null |  | A direct provider voice ID, at most 200 characters, trimmed of surrounding spaces. Empty or blank (after trimming) clears the choice so Default applies, for every provider; it never selects a provider's own default voice. For Breeze a nonblank ID must be a usable (cloned) voice in the last Breeze check (400 `breeze_voice_unavailable`) and is stored pinned as `{id, revision, seed}`; for Gemini and device voices it is stored as `{id}`. |
| `library` | string \| null |  | A library voice ID (`vl_` + 16 lowercase hex) of the same provider, not deleted (400 `library_voice_unavailable`). Stored as `{library}`: the character follows that voice's current version. |
| `seed` | integer \| null |  | Take seed 0–4294967295 for a Breeze pin; only with a nonblank Breeze `id`. Defaults to the voice's own seed, else 42. With `library`, a blank `id` or another provider it is 400 `seed_not_applicable` (it is never silently ignored). |

<a id="schema-voicedraft"></a>
### VoiceDraft

A voice design draft ("DraftView").

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Draft ID, `vd_` followed by 16 hex digits. |
| `provider` | `"breeze"` \| `"gemini"` | yes | The provider candidates are generated with. |
| `base_voice_id` | string \| null | yes | Library voice this draft iterates on (enables `mode: "version"` on save), or null. |
| `base_voice_name` | string \| null | yes | Current name of the base voice; null when there is none or it can no longer be read. |
| `context` | [VoiceCharacterContext](#schema-voicecharactercontext) \| null | yes | The character the draft was started from, or null. |
| `name` | string | yes | Working name, up to 100 characters; may be empty. |
| `description` | string | yes | Voice description, up to 1,000 characters. Generation requires at least 3 non-space characters. |
| `sample_text` | string | yes | Text Breeze previews speak (up to 1,000 characters). Unused by Gemini, whose drafts start with an empty string. |
| `status` | `"open"` \| `"saved"` \| `"abandoned"` | yes | Only `open` drafts can be edited, generated, discarded, saved or abandoned. `GET /api/voices` lists only open drafts. |
| `busy` | boolean | yes | True while a generate, discard, abandon or save request is working on the draft in this server process (in memory; cleared by a restart). |
| `created_at` | string | yes | ISO 8601 UTC. |
| `updated_at` | string | yes | ISO 8601 UTC; changes on every edit, generation and state change. |
| `candidates` | list of [VoiceDraftCandidate](#schema-voicedraftcandidate) | yes | Every candidate ever generated, including discarded ones, oldest first. |

<a id="schema-voicedraftcandidate"></a>
### VoiceDraftCandidate

One generated candidate in a design draft.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Candidate ID, unique within the draft: `c1`, `c2`, … in creation order. |
| `kind` | `"breeze_preview"` \| `"gemini_voice"` | yes | `breeze_preview`: a temporary Breeze voice preview (Bardic keeps its audio). `gemini_voice`: a stored, billed voice in the Google project. |
| `seed` | integer \| null | yes | Breeze preview seed; null for Gemini. |
| `provider_voice_id` | string \| null | yes | The Gemini `voice_…` ID; null for Breeze previews. |
| `description` | string | yes | The draft description the candidate was generated from. |
| `sample_text` | string \| null | yes | Breeze: the text the preview speaks. Null for Gemini. |
| `duration` | number \| null | yes | Length of the retained audio in seconds; null when no audio was retained (a Gemini sample that could not be stored). |
| `created_at` | string | yes | When the candidate was generated (ISO 8601 UTC). |
| `expires_at` | string \| null | yes | Provider expiry timestamp string: the Breeze preview's expiry (about 24 hours; irrelevant to saving, which uploads the retained clip) or the Gemini stored voice's expiry. Null when not reported. |
| `discarded` | boolean | yes | True once discarded (explicitly, or by abandoning the draft). |
| `audio` | [VoiceDraftCandidateAudio](#schema-voicedraftcandidateaudio) \| null | yes | The retained audio, or null when none was retained (a Gemini sample that could not be stored). |

<a id="schema-voicedraftcandidateaudio"></a>
### VoiceDraftCandidateAudio

Bardic's retained copy of a candidate's audio (24 kHz mono WAV).

Served by `GET /api/voices/drafts/{draft_id}/candidates/{candidate_id}/audio`. `voice` is the Gemini `voice_…`
ID, or null for a Breeze preview, which is not a server voice. `model` is the Breeze model or the Gemini
design model used.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `url` | string | yes | Root-relative URL of the audio bytes (WAV unless stated otherwise). Play this; do not build audio URLs from other fields. |
| `asset_id` | string \| null | yes | SHA-256 hex of the file the URL serves (content address), or null when the bytes are not content-addressed (takes recorded before content addressing). A different `asset_id` means different audio. |
| `duration` | number \| null | yes | Length of this audio in seconds, or null when unknown. |
| `provider` | string \| null | yes | Speech provider that produced the bytes (`system`, `gemini`, `breeze`), or null when unknown. |
| `model` | string \| null | yes | Speech model that produced the bytes, or null when unknown. |
| `voice` | string \| null | yes | Provider voice actually used, or null when unknown. |
| `created_at` | string \| null | yes | ISO 8601 UTC time the audio was retained, or null when it was not recorded. |

<a id="schema-voicedraftsaved"></a>
### VoiceDraftSaved

Result of saving a draft candidate as a library voice or version.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `voice` | [LibraryVoice](#schema-libraryvoice) | yes | The new voice, or the base voice with its new current version. |
| `book` | [Book](#schema-book) \| null |  | The full book document after assignment; present only when `assign` was given and the assignment succeeded. |
| `assignment_error` | string \| null |  | Present when `assign` was given and failed (for example the book is busy or the character is missing). The voice is still saved; nothing is rolled back. |
| `cleanup_error` | string \| null |  | Gemini only. Present when an unchosen stored candidate could not be deleted (the first failure message), or some were made with another Google key and remain in that project. |

<a id="schema-voiceedit"></a>
### VoiceEdit

Changes to a library voice. Omitted or null fields are unchanged; values are trimmed.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string \| null |  | Display name, 1–100 characters (must not be only whitespace). |
| `description` | string \| null |  | Description, up to 1,000 characters; an empty string clears it. |

<a id="schema-voicelibrarybreezeservervoice"></a>
### VoiceLibraryBreezeServerVoice

A voice on the Breeze server, as pinned by the last connection check.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Breeze server voice ID (lowercase letters, digits, `_`, `-`; up to 64). |
| `name` | string | yes | Server display name (up to 200 characters); the ID when unnamed. |
| `kind` | string \| null | yes | Server voice kind, for example `cloned` or `designed`; only `cloned` voices are stable enough to narrate. |
| `description` | string | yes | Server description (up to 500 characters); may be empty. |
| `labels` | map of string → string | yes | Server labels (up to 20). Bardic-made voices carry `bardic_voice` (library voice ID) and `bardic_version`. |
| `usable` | boolean | yes | True when the voice can narrate: cloned, with a readable reference clip. |
| `reason` | string \| null | yes | Why it is not usable, or null. |
| `revision` | string \| null | yes | Pinned revision (opaque 64-hex hash of speech-affecting state), or null when not usable. |
| `seed` | integer \| null | yes | The server voice's own seed setting, if it has one. |

<a id="schema-voicelibrarybreezestatus"></a>
### VoiceLibraryBreezeStatus

The last Breeze connection check, read locally without contacting the server.

The same object appears as `breeze` in `GET /api/status` and is returned by
`POST /api/narration/breeze/refresh`; it is described separately here.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `configured` | boolean | yes | True when a Breeze server URL is set. |
| `base_url` | string | yes | The configured server root, or an empty string. |
| `has_api_key` | boolean | yes | True when a Breeze API key is loaded (the key is never returned). |
| `state` | `"unconfigured"` \| `"checking"` \| `"unchecked"` \| `"ready"` \| `"loading"` \| `"error"` \| `"unreachable"` | yes | `unconfigured`: no URL. `checking`: a check is running now. `unchecked`: never checked for this URL. Otherwise the last check's result: `ready`, `loading` (the model is still loading), `error` (bad key, HTTP error, unreadable response or unsupported model) or `unreachable`. |
| `message` | string | yes | Human-readable state explanation. |
| `checked_at` | string \| null | yes | When the last check for this URL finished (ISO 8601 UTC), or null. |
| `model` | string \| null | yes | Model the server reported, or null. |
| `default_voice_id` | string \| null | yes | The server's own default voice ID (not Bardic's default), or null. |
| `voices` | list of [VoiceLibraryBreezeServerVoice](#schema-voicelibrarybreezeservervoice) | yes | Server voices from the last check for this URL (at most 200). A failed check keeps the previous voices. Voices Bardic creates or deletes are added or removed here immediately when the saved check is for the current URL. |

<a id="schema-voicelibrarybuiltins"></a>
### VoiceLibraryBuiltins

Provider built-in voices that are not library voices.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `gemini` | list of string | yes | Gemini prebuilt voice names (for example `Kore`). |
| `system` | list of [VoiceLibrarySystemVoice](#schema-voicelibrarysystemvoice) | yes | Installed macOS voices; empty when `say` is unavailable. |

<a id="schema-voicelibrarydefaults"></a>
### VoiceLibraryDefaults

Default library voice per provider. Only Breeze has a default.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `breeze` | string \| null | yes | The Breeze default library voice ID, or null when none is set. Characters without a Breeze choice follow it. |

<a id="schema-voicelibrarydefaultsresult"></a>
### VoiceLibraryDefaultsResult

The defaults after a change.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `defaults` | [VoiceLibraryDefaults](#schema-voicelibrarydefaults) | yes |  |

<a id="schema-voicelibrarygeminiprojectvoice"></a>
### VoiceLibraryGeminiProjectVoice

A stored voice in the Google project, from the last Gemini refresh.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Gemini voice ID (`voice_…`). |
| `display_name` | string | yes | Display name (up to 200 characters); the ID when unnamed. |
| `type` | string | yes | `prompted` or `replicated` (the listing requests only these). |
| `description` | string | yes | Provider description (up to 500 characters); may be empty. |
| `language_code` | string | yes | Language tag, or empty. |
| `in_library` | boolean | yes | True when some library voice version (including deleted voices) uses it. |
| `draft_candidate` | boolean | yes | True when an undiscarded candidate of an open draft uses it. |

<a id="schema-voicelibrarygeministatus"></a>
### VoiceLibraryGeminiStatus

Gemini voice-design state and the last listing of the Google project's stored voices.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `has_api_key` | boolean | yes | True when a Gemini API key is loaded (the key is never returned). |
| `tts_model` | string | yes | The selected Gemini speech model preference. |
| `state` | `"unchecked"` \| `"ready"` \| `"error"` | yes | `unchecked`: never refreshed with the current key (a listing made with another key is ignored). `ready` or `error`: the last refresh result. After `error`, `stored_count` is null, `project_voices` is empty and Gemini versions report `server_state` `unknown` until a refresh succeeds. |
| `message` | string | yes | Human-readable result, for example "3 stored voices in this Google project."; empty when unchecked. |
| `checked_at` | string \| null | yes | When the last refresh with the current key finished (ISO 8601 UTC), or null. |
| `design_models` | list of string | yes | Gemini speech models that accept designed voices. |
| `designed_voices_supported` | boolean | yes | True when `tts_model` is one of `design_models`. Otherwise Gemini library voices are not assignable. |
| `stored_count` | integer \| null | yes | Number of stored voices in the last listing; null when never listed or the last refresh failed. |
| `limit` | integer | yes | Google's stored-voice limit per project (200). |
| `project_voices` | list of [VoiceLibraryGeminiProjectVoice](#schema-voicelibrarygeminiprojectvoice) | yes | Stored voices from the last refresh with the current key, newest first. Empty when never listed or when the last refresh failed. |

<a id="schema-voicelibraryoverview"></a>
### VoiceLibraryOverview

Everything the Voices screen needs, read from local state.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `voices` | list of [LibraryVoice](#schema-libraryvoice) | yes | Live (not deleted) library voices in creation order. |
| `defaults` | [VoiceLibraryDefaults](#schema-voicelibrarydefaults) | yes |  |
| `drafts` | list of [VoiceDraft](#schema-voicedraft) | yes | Open drafts, newest first. |
| `providers` | [VoiceLibraryProviders](#schema-voicelibraryproviders) | yes |  |
| `builtin` | [VoiceLibraryBuiltins](#schema-voicelibrarybuiltins) | yes |  |

<a id="schema-voicelibraryproviders"></a>
### VoiceLibraryProviders

Provider state for the voice library.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `breeze` | [VoiceLibraryBreezeStatus](#schema-voicelibrarybreezestatus) | yes |  |
| `gemini` | [VoiceLibraryGeminiStatus](#schema-voicelibrarygeministatus) | yes |  |

<a id="schema-voicelibrarysystemvoice"></a>
### VoiceLibrarySystemVoice

An installed macOS device voice.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Voice name as the `say` command knows it. |
| `name` | string | yes | Same as `id`. |
| `locale` | string | yes | Locale with a hyphen, for example `en-US`. English voices are listed first. |

<a id="schema-voicepreview"></a>
### VoicePreview

An immutable audition request.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Preview ID: a hash of the full audition recipe. |
| `schema_version` | integer | yes | Preview request format version (1). |
| `book_id` | string | yes | Book ID the audition belongs to. |
| `text` | string | yes | Exact source text sampled, at most 400 code points: a passage prefix; for a pronunciation audition, the sentence around the word; or a fixed demo or carrier sentence. Pronunciations are not applied here (see `spoken_text`). |
| `source` | `"passage"` \| `"demo"` | yes | `passage` when the text comes from the book; `demo` when no passage was chosen or found and a fixed demo or pronunciation carrier sentence is used. |
| `segment_id` | string \| null | yes | Passage ID sampled: the requested passage, or else the character's first attributed passage. Null for demo text. |
| `chapter_id` | string \| null | yes | Chapter ID of the sampled passage, or null for demo text. |
| `character_id` | string \| null | yes | Book-local character ID whose voice and direction were auditioned, or null for a narrator audition. |
| `character_name` | string \| null | yes | Character name at request time, or null. |
| `source_anchor` | [VoicePreviewSourceAnchor](#schema-voicepreviewsourceanchor) \| null | yes | Null for demo text. |
| `truncated` | boolean | yes | True when the passage was shortened to the sample. |
| `spoken_text` | string \| null |  | The text actually sent to the narrator when a pronunciation changed it; absent otherwise. |
| `pronunciation` | [VoicePreviewPronunciation](#schema-voicepreviewpronunciation) \| null |  | The unsaved pronunciation this audition tried; absent otherwise. |
| `provider` | `"system"` \| `"gemini"` \| `"breeze"` | yes | Speech provider: `system` (macOS device voice), `gemini` or `breeze` (self-hosted). |
| `model` | string | yes | Speech model: `macos-say` for system, `breeze-tts-2` for Breeze, or the Gemini model (default `gemini-3.8-flash-tts`). |
| `voice` | string | yes | Resolved provider voice (Gemini defaults to `Kore`; device default is empty). |

<a id="schema-voicepreviewaudio"></a>
### VoicePreviewAudio

A retained audition take.

Like every audio object, it has the common audio core, always present: `url`, `asset_id`, `duration`, `provider`, `model`, `voice` and `created_at`.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `url` | string | yes | Root-relative WAV URL: `/api/books/{book_id}/voice-preview/audio/{asset_id}`. |
| `asset_id` | string | yes | SHA-256 hex of the WAV bytes. |
| `duration` | number | yes | Seconds. |
| `provider` | string | yes | Provider that produced the bytes: `system`, `gemini` or `breeze`. |
| `model` | string | yes | Speech model that produced the bytes. |
| `voice` | string | yes | Provider voice actually used. |
| `created_at` | string | yes | ISO 8601 UTC time the take was retained. |
| `preview_id` | string | yes | ID of the audition request (`VoicePreview.id`) this take was retained for. |
| `schema_version` | integer | yes | Take record format version (1). |
| `reuse` | [VoicePreviewReuse](#schema-voicepreviewreuse) \| null |  | Present when bytes were reused from an equivalent audition (for example after a character rename). |
| `resource_usage` | [AudioTakeUsage](#schema-audiotakeusage) \| null |  | Usage of the generating request; absent for device takes and reused bytes. |
| `provider_timing` | [AudioTakeSentenceTiming](#schema-audiotakesentencetiming) \| null |  | Breeze only: validated sentence timing, or null. |
| `breeze` | [AudioTakeBreezeInfo](#schema-audiotakebreezeinfo) \| null |  | Breeze only: request details. |
| `voice_revision` | string \| null |  | Breeze only: voice revision used. |
| `voice_library` | [AudioTakeVoiceLibrary](#schema-audiotakevoicelibrary) \| null |  | When the voice was a voice-library voice: which one and which version. |

<a id="schema-voicepreviewcached"></a>
### VoicePreviewCached

An audition served from retained audio.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `preview` | [VoicePreview](#schema-voicepreview) | yes |  |
| `audio` | [VoicePreviewAudio](#schema-voicepreviewaudio) | yes |  |
| `cached` | `true` | yes | Always true: served from a retained audition take. No job was queued and no provider was contacted. |

<a id="schema-voicepreviewpronunciation"></a>
### VoicePreviewPronunciation

The unsaved pronunciation a voice example auditioned.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `term` | string | yes | The word as written in the book. |
| `spoken` | string | yes | What this example's narrator was asked to say: the provider override if any, else the respelling. |

<a id="schema-voicepreviewqueued"></a>
### VoicePreviewQueued

An audition that needs synthesis: a new or joined `voice_preview` job.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `preview` | [VoicePreview](#schema-voicepreview) | yes |  |
| `job` | [Job](#schema-job) | yes | The `voice_preview` job. On completion its `audio` holds the take. |
| `cached` | `false` | yes | Always false: no retained take matched, so the audition needs synthesis via `job`. |

<a id="schema-voicepreviewrequest"></a>
### VoicePreviewRequest

A voice audition. There is no transcript field: text comes from the stored book or the fixed demo.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `provider` | `"system"` \| `"gemini"` \| `"breeze"` |  | Narration provider: `system` (default), `gemini` or `breeze`. (default `"system"`) |
| `voice` | string \| null |  | Voice to audition: `"library:vl_…"` (that library voice's current version), a direct provider voice ID, or empty/null for Default (Gemini `Kore`, the device default voice, or the Bardic default Breeze library voice). A direct Breeze ID must be in the last Breeze voice check. At most 256 characters. |
| `model` | string \| null |  | Speech model; omit for the configured Gemini model or the provider's fixed model. Device and Breeze reject other values. At most 200 characters. |
| `segment_id` | string \| null |  | Passage to sample (an exact prefix of at most 400 code points). At most 200 characters. |
| `character_id` | string \| null |  | Character to audition. Without `segment_id`, their first attributed passage is used (demo text if none). At most 200 characters. |
| `direction` | string \| null |  | Unsaved character direction to use instead of the saved one. Requires `character_id`. At most 3000 characters. |
| `segment_direction` | string \| null |  | Unsaved passage direction. Requires both a passage and `character_id`. At most 3000 characters. |
| `pronunciation` | [PronunciationEntry](#schema-pronunciationentry) \| null |  | An unsaved pronunciation entry to audition in place of the saved entry with the same `id` (or in addition to the saved ones, without an `id`). Never stored in the book. Without `segment_id`, the sample is the sentence around the word's first occurrence, or a fixed carrier sentence when the book does not contain it. |

<a id="schema-voicepreviewreuse"></a>
### VoicePreviewReuse

Pointer to an equivalent earlier audition take whose bytes were reused.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `schema_version` | integer | yes | Pointer format version (1). |
| `take_id` | string | yes | ID of the original retained audition take whose bytes were reused (64 hex). |
| `preview_id` | string | yes | Preview ID of the original audition request that produced the reused bytes. |

<a id="schema-voicepreviewsourceanchor"></a>
### VoicePreviewSourceAnchor

Exact source prefix used as audition text.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `schema_version` | integer | yes | Anchor format version (1). |
| `book_id` | string | yes | Book ID the sampled passage belongs to. |
| `chapter_id` | string | yes | Chapter ID whose text `start` and `end` index. |
| `segment_id` | string | yes | Passage (segment) ID the sample was taken from. |
| `start` | integer | yes | Chapter-local code-point start of the passage. |
| `end` | integer | yes | Exclusive chapter-local code-point end of the sample (start + sample length). |
| `text_sha256` | string | yes | SHA-256 hex of the sample text. |
