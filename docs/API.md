# Local API guide

The API is implemented in [app.py](../bardic/app.py). It is a local application interface, not a hosted public service. Examples assume `http://127.0.0.1:8765`; use the alternate port of your isolated development server when testing. See [DEVELOPMENT](DEVELOPMENT.md) for launching with a temporary library and no cloud keys.

## Source of truth and conventions

The live FastAPI OpenAPI document is available at [`/openapi.json`](http://127.0.0.1:8765/openapi.json). Swagger and ReDoc pages are disabled. The schema describes request DTOs, allowed fields, path/query parameters, and validation bounds. Most responses are ordinary Python dictionaries rather than declared response models, so this guide and the route tests describe their response families more fully.

Inspect the schema directly from a running local instance:

```sh
curl --fail --silent http://127.0.0.1:8765/openapi.json
```

Do not commit a captured runtime/status response or populate a schema example with keys, private prose, or real account data. No checked-in generated OpenAPI snapshot is required; the live document follows the running code. Requests below use placeholder IDs, not actual library contents.

- JSON writes use `Content-Type: application/json`. Import uses multipart form data with the field `file`.
- Request models forbid unknown fields. Omitted fields take their DTO defaults; explicit `null` has meaning only where the field permits it.
- IDs are opaque. URL-encode them; obtain book, chapter, segment, character, series, job, session, and artifact IDs from API responses.
- `segment` and `passage` refer to the same source-level reader unit. Source offsets are Python Unicode character offsets, chapter-local, with an exclusive `end`; they are not UTF-8 byte offsets or JavaScript UTF-16 indices.
- `GET` routes do not trigger paid model generation. Some build local caches/indexes, retain legacy artifact snapshots, or record local resource measurements.
- Every `/api/` response is marked `Cache-Control: no-store` by middleware. Download endpoints return bytes instead of JSON.
- The default process binds to loopback. `BARDIC_LAN_NAME` opts into network binding and adds its `.local` name to the trusted hosts; `BARDIC_ALLOWED_HOSTS` adds exact names. Trusted hosts and same-origin write checks reject unexpected hosts/cross-origin writes; there is no account authentication layer, including on the network. A command-line request without an `Origin` header is accepted locally. Browser writes must be same-origin.

### Errors and work state

Errors normally contain `{"detail":"human-readable message"}`. FastAPI validation errors use an array in `detail`.

| HTTP status | Typical meaning |
| --- | --- |
| `400` | Invalid domain operation, unsupported model, missing configured key/device capability, or archived target. |
| `403` | Cross-origin or cross-site write rejected. |
| `404` | Missing book/item/job/session/artifact, wrong book scope, or unavailable audio. |
| `409` | Busy book, conflicting in-flight operation, or settings changed during a provider inventory/check operation. |
| `413` | Uploaded file exceeds the import limit. |
| `422` | Missing required field, wrong DTO/query type, forbidden field, or declared validation bound. |

A queued job response is not a completed result. Worker failures, cancellations, and allowance stops normally appear later in job JSON while polling still returns HTTP 200. Likewise, HTTP 200 from a provider does not prove its structured output passed evidence validation. Use the pipeline attempt's `validation_state` and retained events.

## Settings, provider capabilities, and access checks

| Route | Request / response |
| --- | --- |
| `GET /api/status` | Runtime settings, provider availability, configured-key booleans, model catalogs, prior account-check state, system voices, TTS choices, data directory, timing kind. Never returns key values. |
| `POST /api/settings` | Updates model preferences and/or in-memory credentials; returns the status family. |
| `POST /api/narration/breeze/refresh` | Explicit check of the configured Breeze server: health, voice list and reference clips of cloned voices, pinning each voice's revision. No generation. After a ready check it imports every usable server voice not already behind a library version (including deleted library voices) as a library voice with its reference clip as audition, and sets the Breeze default if none exists (the server's default voice when available). Returns the `breeze` status object; failures are reported as `state`/`message`, not errors. 400 without a URL; 409 if settings changed during the check or another check is running. |
| `POST /api/models/{provider}/refresh` | Explicit remote model-inventory request for `gemini`, `openai`, or `anthropic`. Returns catalog state/models/message/cache information. Does not generate text or establish credit. |
| `POST /api/account-checks/{provider}` | Explicit tiny text generation with the configured analysis model. Can incur a charge. Returns access/billing/quota state, timestamp, usage if reported, and dashboard links; not an exact balance. |

Settings accepts `tts_model`, `analysis_provider`, `analysis_models_by_provider`, `preprocess_models_by_provider`, `api_keys`, `breeze_url` (an http(s) server root without path or credentials; saved; `""` clears), `breeze_api_key` (runtime only; `""` clears) and `local_service_urls` (`{local_llm|booknlp|novel_analyzer: url}`, each an http(s) server root; saved; `""` clears it even if its environment variable is set; a key never saved uses the environment, which is never persisted). Status includes the resolved `local_service_urls`. Status adds `narration_providers` (per-provider label, models, default voice, `requires` and capabilities such as `chunked_listening`, `seeded_takes` and `cost`), a `breeze` provider entry whose `available` is true only after a check found a usable voice, and `breeze` (`configured`, `base_url`, `has_api_key`, `state`, `message`, `checked_at`, `model`, `default_voice_id`, `voices[]` with `usable`, `reason`, `revision`, `seed`). Status never contacts the server. `api_key` and `analysis_model` are compatibility aliases for the Gemini entries. Conflicting alias/map values are rejected. An empty key string clears that runtime key; omitted keys stay unchanged. Credentials are not persisted by this endpoint. Models/preferences are persisted.

A preference-only request:

```json
{
  "analysis_provider": "openai",
  "analysis_models_by_provider": {"openai": "gpt-6-sol"},
  "preprocess_models_by_provider": {"openai": "gpt-6-luna"}
}
```

Analysis model IDs may be custom syntactically valid IDs; the provider validates actual support when used. IDs contain 1–200 letters, digits, dots, underscores, colons, or hyphens, starting with a letter or digit. Key values are limited to 500 characters. TTS choices are restricted to the app's configured list. Prefer `/api/status` over assuming a fixed list. Identical account checks reuse an in-memory result for 30 seconds; checks are tied to the key/model configuration. Details: [provider setup](ANALYSIS-PROVIDERS.md), [account checks](ACCOUNT-CHECKS.md), [model catalog source](../bardic/model_catalog.py).

## Local troubleshooting diagnostics

| Route | Request / response |
| --- | --- |
| `POST /api/diagnostics` | Best-effort local operational event; strict allowlisted body below. Returns `{recorded:true,id}` or `{recorded:false,reason,...}`. No model requests. |
| `GET /api/diagnostics?book_id=...&limit=100` | Newest events first: `{events:[...],retention_limit:5000}`. Optional valid book UUID filters results; `limit` defaults to 100 and accepts 1–5000. |

Settings downloads `/api/diagnostics?limit=5000` as `bardic-diagnostics.json`. Events contain their ID, timestamp, `source` (`client` or `server`), `event` code and validated operational fields. Empty results do not establish that playback had no errors: logging is best effort, older events are pruned, and past unrecorded messages cannot be recovered.

A minimal synthetic report:

```json
{"event":"playback_play_rejected","operation":"play","playback_rate":2.5}
```

Client `event` accepts `listen_request_failed`, `listen_poll_failed`, `listen_job_failed`, `buffer_failed`, `cache_read_failed`, `playback_media_error`, `playback_play_rejected`, `playback_waiting`, or `playback_resumed`. Optional fields are `book_id`, `segment_id`, `session_id`, `job_id`, `playback_rate`, `http_status`, `media_error_code`, and `operation`. Use real application IDs from responses: identifiers are format-validated; passage/session/job IDs require a book ID. Numeric fields are strict: finite playback rate 0.1–8, HTTP status 100–599, and media error code 1–4. `operation` accepts `request`, `poll`, `play`, `prefetch`, `media`, `prepare`, `settle`, or `cache_read`.

Free-form messages, stacks, URLs, source text, credentials and unknown fields are forbidden. Invalid DTOs receive a generic HTTP 422 response that does not echo rejected inputs. Server records may additionally contain bounded provider/job-status values; clients cannot set the event source or these server-only fields. Listen-worker failure/stop and submission-failure events correlate to saved jobs without copying their error strings.

Identical persisted events are coalesced within two seconds; no more than 120 client events are accepted per rolling minute across the instance. The browser also suppresses identical reports for ten seconds. Suppression returns `recorded:false` with `reason: "duplicate"` or `"rate_limited"`; logging/storage failure can return `"unavailable"`. Each accepted insert retains only the newest 5,000 events. Callers must not treat missing diagnostic persistence as a narration failure or retry paid work because of it. This rotating log is separate from retained analysis provenance and resource accounting. Sources: [diagnostics repository](../bardic/diagnostics.py), [browser reporter](../bardic/static/diagnostics.js), [operations](OPERATIONS.md#troubleshooting).

## Books and library lifecycle

| Route | Behavior |
| --- | --- |
| `GET /api/books` | Active book summaries, including counts, cover metadata, membership, and measured storage fields. This is not the complete prose projection. |
| `POST /api/books` | Import a DRM-free EPUB or UTF-8 TXT through multipart `file`; returns the presented full book. Upload maximum is 30 MiB. |
| `POST /api/demo` | Creates the built-in original sample with a free local heuristic draft; returns its full book. |
| `GET /api/books/{book_id}` | Full reader projection: chapters, scenes, characters, segments, revision, and analysis metadata. Valid enhanced audio has a playback URL; unavailable/stale selected audio is presented as `null`. |
| `GET /api/library?include_archived=false` | `{books, series, storage, ...}` library-management snapshot. Include removed entries with `include_archived=true`. |
| `PATCH /api/books/{book_id}/metadata` | `{title, author}`; edits display metadata. Title is required (1–500 characters); author defaults to an empty string (maximum 500). Returns a summary. |
| `POST /api/books/{book_id}/refresh-metadata` | Refreshes metadata/cover from the saved original where supported, preserving reviewed display metadata. Returns a summary; does not download metadata from the web. |
| `GET /api/books/{book_id}/cover` | Saved image bytes, or 404 if none is available. |
| `POST /api/books/{book_id}/repair-structure` | Re-examines the saved original's structure. Requires idle/active book and a compatible source mapping; returns the updated presented book. Unsafe repair is rejected with work retained. |
| `POST /api/books/{book_id}/archive` | Reversibly removes the book from normal library views. Retains original, analysis, audio, and history. Returns `{id,archived:true,retained:true}`. |
| `POST /api/books/{book_id}/restore` | Restores the removed entry; respects series-run guards. Returns `{id,archived:false,retained:true}`. |

A synthetic import:

```sh
curl --fail --request POST http://127.0.0.1:8765/api/books \
  --form 'file=@/absolute/path/to/synthetic-story.txt;type=text/plain'
```

There is no destructive book-delete endpoint. Archiving does not reclaim disk space; saved assets remain readable. Active processing and most edits reject archived books. Counts distinguish narrative chapters from other sections. Library `database_payload_bytes` does not apportion SQLite pages/indexes/free space exactly; shared database/WAL/SHM bytes are reported separately. Sources: [library.py](../bardic/library.py), [structure guide](STRUCTURE.md), [library tests](../tests/test_library_api.py).

## Per-book analysis and review

| Route | Behavior |
| --- | --- |
| `GET /api/books/{book_id}/preprocessing` | Free local census, semantic source coverage, importance/priority signals, usage, and profile freshness/provisional state. |
| `POST /api/books/{book_id}/analysis-plan` | Preview currently known work with `AnalysisRequest`; no provider inference. Returns requested phase/provider/models, pending requests, cache counts, token/cost estimates, stage counts, coverage, notes, and the supplied limits. |
| `POST /api/books/{book_id}/analyze` | Starts/resumes a job using the same request shape. Returns a job. |
| `GET /api/books/{book_id}/analysis` | Checkpoint summary, including per-chapter progress; returns `not_started` when no checkpoint exists. |
| `GET /api/books/{book_id}/characters/{character_id}/references` | Unpaginated references for that current cast member, with kinds and source anchors. Rows projected from accepted pipeline evidence add `step`, `version_id`, `origin`, `projection` and, for profile quotations, `anchors` ([evidence projection](ANALYSIS-PIPELINE.md#evidence-projection)). |
| `POST /api/books/{book_id}/characters` | Adds a human-reviewed cast member; `name` is required. Returns the full book. |
| `PATCH /api/books/{book_id}/characters/{character_id}` | Optional `name`, `aliases`, `description`, `voices`, `direction`; `voice`/`system_voice` remain accepted as the Gemini/device entries. `voices` is `{provider: {library} | {id, seed?} | null}` with exactly one of `library` (`vl_…`, follow that library voice's current version; 400 if deleted or of another provider) or `id` (a direct provider voice; a Breeze `id` is pinned to the revision from the last Breeze check, 400 if unknown or not cloned). `null` means Default. Presented books show the stored `voices` map (library references stay references). Returns the full book. |
| `PATCH /api/books/{book_id}/segments/{segment_id}` | Optional `speaker_id`, `direction`, `cues`, `seed` (0–4294967295, used by seeded providers such as Breeze; a new seed is a new take); marks the passage edited. A reviewed speaker assignment gets confidence 1.0. Returns the full book. |
| `PATCH /api/books/{book_id}/scenes/{scene_id}` | Optional `title`, `summary`, `tone`, `direction`. Returns the full book. |

Typical analysis body:

```json
{
  "provider": "openai",
  "phase": "scan",
  "resume": true,
  "limits": {
    "max_requests": 25,
    "max_input_tokens": 1000000,
    "max_output_tokens": 100000,
    "budget_usd": 1.0
  }
}
```

Send that first to `/analysis-plan`. Dispatching it to `/analyze` with a cloud provider may incur charges. A body with `provider: "local"` runs the heuristic draft, without semantic model discovery. The cloud phases are:

| `phase` | Scope |
| --- | --- |
| `scan` | Discover characters over eligible source sections using the fast model. |
| `profiles` | Refine character profiles from retained evidence and confirmed earlier-series context. |
| `direct` | Annotate passages/scenes with the detailed model; set `chapter_id` to select a chapter. |
| `full` | Discovery, profile refinement, and direction. Newly discovered work makes the initial estimate incomplete. |

`chapter_id` is optional; when supplied it must belong to the book. The main UI uses whole-book scan/profiles and selected-chapter direction. Models come from runtime settings, not from this request. `resume` defaults to true. Per-book preview currently has no server-enforced preview-fingerprint field; the UI invalidates its preview when local inputs change. Series dispatch has the stronger fingerprint contract below.

Limits default to 25 requests, 1,000,000 input tokens, 100,000 output tokens, and a $1 tracked book allowance. Request cap: 1–1,000. Input cap: 1,000–10,000,000. Output cap: 1,000–2,000,000. Dollar cap: greater than zero and at most 1,000, or explicit `null` to use request/token caps without a dollar guard. The dollar guard includes prior tracked analysis for this book; request/token limits apply to the run. Unknown prices can prevent a guarded run. These limits do not cap TTS spending or represent account credit.

Accepted units and completed chapter work survive later failures. Human edits remain authoritative, affected enhanced takes become stale, and source text is not replaced by model output. Whole-book scan coverage and profile freshness are separate. See [progressive.py](../bardic/progressive.py), [processing.py](../bardic/processing.py), and [chapter analysis](CHAPTER-ANALYSIS.md).

Review field bounds: character names are 1–100 characters, descriptions/directions at most 3,000, and voice IDs at most 200. Scene titles are 1–200, summaries at most 4,000, tone at most 1,000, and directions at most 3,000. Passage directions are at most 3,000; `aliases` and `cues` are string arrays. These review endpoints ignore omitted or `null` fields; use an empty string or array to clear an allowed value. Character creation enforces a nonempty `name` even though it shares the optional-field edit DTO.

## Series membership, identities, and volume placeholders

| Route | Request / response |
| --- | --- |
| `GET /api/series` | Active series array with supplied books/order, volume slots, and character counts. |
| `POST /api/series` | `{name}` (1–200 characters); returns the created series object. |
| `PATCH /api/series/{series_id}` | `{name}`; renames it when relevant work is idle. Returns `{id,name}`. |
| `POST /api/series/{series_id}/archive` | Removes the series from normal views; its books remain independently available. Returns `{id,archived:true,retained:true}`. |
| `POST /api/series/{series_id}/restore` | Restores the series entry. Returns `{id,archived:false,retained:true}`. |
| `GET /api/books/{book_id}/series` | `{membership, series, links, characters}`; membership/selected series can be null. |
| `PUT /api/books/{book_id}/series` | `{series_id, position}`; returns the refreshed envelope. Use both values `null` to detach. |
| `GET /api/series/{series_id}/characters` | Explicit cross-book identity array. |
| `POST /api/series/{series_id}/characters` | `{name}`; creates a series-level identity without merging any book characters. |
| `PUT /api/books/{book_id}/series/characters/{character_id}` | `{series_character_id}` to link; `null` to unlink. Narrator/unassigned cannot become series identities. |
| `GET /api/books/{book_id}/series/context` | Bounded, source-validated earlier-volume context, provenance, included/available counts, truncation flag, and a stable fingerprint. |
| `PUT /api/series/{series_id}/volumes` | Required `position`, optional `title` (default empty, maximum 500 characters), and `status` (`missing` by default, or `planned`). Returns `{series_id,position,title,status,book_id:null}` for the placeholder. |
| `DELETE /api/series/{series_id}/volumes/{position}` | Removes a placeholder only; does not delete a supplied book. Returns `{series_id,position,removed:true}`. |
| `GET /api/series/{series_id}/map` | `{series, characters, note}` for supplied/missing/planned volumes and explicit identities. |

A membership request uses a JSON number, not a numeric string:

```json
{"series_id":"SERIES_ID","position":9}
```

Positions are finite numbers from 0 through 1,000,000; decimals support prequels and side stories. Duplicate supplied-book positions are rejected. Assigning a real book at a placeholder position replaces that placeholder. Detaching or moving to another series removes that book's active identity links. Archived membership/history is retained for restoration.

Names alone never establish cross-book identity. Context excludes later volumes, unconfirmed links, unavailable/archived source volumes, and invalidated evidence. Its default bound is an implementation choice in [series.py](../bardic/series.py), not a request parameter on the HTTP route.

## Series preview and execution

Series runs use the [step pipeline](ANALYSIS-PIPELINE.md#series-runs) (changed 2026-09-28; the earlier `provider`/`phase`/`expected_plan_fingerprint` body is gone and is rejected with 422).

`POST /api/series/{series_id}/plan` is read-only and sends nothing. It accepts `{steps, configs?, fresh?}` with the same meaning as the [book plan](#analysis-pipeline). `steps` holds 1–40 step IDs; the series panel sends one. `configs` optionally overrides `{provider, model}` per step, otherwise each step's saved settings apply to every book. The response contains:

- `steps`, the resolved `configs` and `fresh`.
- `books`: active supplied books in reading order, each `{book_id, title, position, fingerprint, plan}`, where `plan` is exactly that book's pipeline plan.
- `volumes` and `skipped_volumes` (missing, planned or archived slots that will not run).
- Summed `requests`, `cached_units`, `service_calls`, `estimated_input_tokens` and `output_token_allowance`.
- `estimated_cost_usd`: `null` when any book's price is unknown. `known_cost_usd` and `unknown_cost_books` say what is priced; unknown is never counted as zero.
- `missing_inputs` (`{book_id: {step: [inputs]}}`), `missing_credentials` (`[{provider, label, needs: "api_key" | "url"}]`), `notes`, and `fingerprint`: 64 hex characters over every book's plan fingerprint and position, the steps, configs and `fresh`.

To start, send `POST /api/series/{series_id}/process` with the same fields plus the confirmed fingerprint:

```json
{"steps":["discovery"],"fresh":false,"concurrency":2,"expected_fingerprint":"FINGERPRINT_FROM_THE_REVIEWED_PLAN"}
```

Optional fields are `mode` (`serial`/`parallel`, for several steps inside each book), `gates` (per-step `auto`/`review` overrides) and `limits` (the [pipeline limits](#analysis-pipeline) object, applied to each book's run; `budget_usd` counts that book's earlier tracked spend too). `concurrency` (1–4) is the number of model requests in flight inside the running book; books run one at a time.

The server re-plans under its store lock before creating jobs:

| Status | When |
| --- | --- |
| `400` | No `expected_fingerprint` and no limits; no active books; a required step input has no accepted result in some book (the message names the books); a key or server URL is missing; the worker could not start. |
| `409` | The fingerprint differs from the current plan; the series already has an active run; a book has an active job or is reserved. |
| `422` | Invalid body (unknown field, `concurrency` outside 1–4, empty `steps`). An unknown step ID returns 400. |

Nothing is queued on any refusal. Re-preview and review the new estimate rather than retrying with a replaced fingerprint.

The start response is the parent job (`kind: "series"`, `book_id: "series:SERIES_ID"`). It carries `steps`, `configs`, `gates`, `concurrency`, `fresh`, `limits`, `book_ids`, `child_job_ids`, `plan_fingerprint`, `requests` and `estimated_cost_usd`. Children are `pipeline` jobs on the real book IDs, with `series_run_id`, `position`, `title`, `plan_fingerprint` and `run_id` (null until the book starts).

Before each book starts, the server re-plans it. If the plan no longer matches, the child fails with nothing sent and the series stops. The first child that does not complete stops the series. Children that never started end `cancelled` (after a cancel) or `interrupted`, with `not_started: true`, and are never started later. Cancelling the parent cancels queued children immediately and stops the running one after its current request.

`GET /api/series/{series_id}/runs` returns `{"runs":[...]}`: up to 20 parent runs with their `children`. A child that started also has `run: {id, status, outcomes, error}` from its pipeline run. A step outcome that is `completed`, has scopes and is not `accepted` is waiting for review in that book's Analysis tab.

Source: [series_processing.py](../bardic/series_processing.py). Contract tests: [test_series_processing.py](../tests/test_series_processing.py), [series UI tests](../tests/series_processing_ui_test.js).

## Jobs and cancellation

`GET /api/jobs` returns up to the 100 most recent jobs. Optional `book_id` filters to one book or the synthetic series-parent owner before that bound applies. `active=true` returns every `queued` or `running` job instead, with no bound; `./bardicctl` uses it before stopping a server. The route exposes no limit/offset parameters and no single-job GET route: select the returned record by its `id`, or use the series run endpoint.

Common job fields are `id`, `book_id`, `kind`, `status`, `progress`, `total`, `message`, `error`, `created_at`, `updated_at`, and `cancel_requested`. Depending on kind, records include provider/model/phase, selected passage/session, series children, or a completed simple `audio` object. Progress units depend on the job and are not a universal percent.

Active statuses are `queued` and `running`; terminal outcomes include `completed`, `failed`, `cancelled`, `interrupted`, `budget_limited`, and `quota_limited` (with `resume_after`). `POST /api/jobs/{job_id}/cancel` takes no meaningful body (send `{}`) and returns the current updated record. Queued work cancels before starting. Running work receives a cancellation flag and stops at the next safe boundary. Cancelling a series parent also marks/cancels its children; cancelling a `performance` job also marks its active `listen_chapter` child (`parent_id`). Completed records are returned unchanged.

A remote request already sent can complete and be billed after cancellation. Validated outputs and completed audio are retained. On restart, incomplete jobs become interrupted. Resume uses the relevant analyze/render/listen/process endpoint rather than reviving an old job ID.

## Enhanced narration, simple listening, voice examples, and audio downloads

| Route | Behavior |
| --- | --- |
| `POST /api/books/{book_id}/render` | Enhanced production job. `{provider:"system"|"gemini"|"breeze", scene_id?, segment_id?, force:false}`. Breeze requires a configured URL and a Breeze voice for every selected speaker; otherwise 400 before queueing. Omitted selectors select the whole book; both selectors, if given, are intersected. |
| `GET /api/audio/{book_id}/{segment_id}` | Current valid enhanced passage WAV. Stale/unavailable selection returns 404. |
| `GET /api/books/{book_id}/audio-assets/{asset_id}` | Retained enhanced audio asset bytes for that book. |
| `POST /api/books/{book_id}/voice-preview` | Explicit bounded voice sample from an exact source prefix or the original demo. See request/response contract below. |
| `GET /api/books/{book_id}/voice-preview/audio/{asset_id}` | Retained preview WAV, scoped to its owning book and verified against a saved take. |
| `POST /api/books/{book_id}/listen` | One simple narrator for one selected passage. See request and response shapes below. |
| `POST /api/books/{book_id}/listen/chapter/preview` | Local plan for a chapter job from a passage: chunks, requests needed, expected audio, ready passages/seconds, chunk options, calibration, limits and this library's daily request count. Creates the narrator session row only; no generation. |
| `POST /api/books/{book_id}/listen/chapter` | Start or join the Gemini chapter job for simple listening. See below. |
| `GET /api/books/{book_id}/listen/takes?session_id=...` | `{session, takes:[{segment_id,audio}]}` for saved matching simple takes. No generation. |
| `GET /api/books/{book_id}/listen/audio/{asset_id}` | Retained simple-listening WAV, scoped to the owning book. |
| `GET /api/books/{book_id}/export` | Audiobook ZIP from valid enhanced takes; requires at least one valid take. Complete chapters are assembled; partial output lists missing passages. |

Enhanced rendering uses each segment's selected character, scene, and performance metadata. TTS model comes from settings. `force:true` generates another take and preserves older bytes. Model/voice/performance edits affect reuse. Device narration ignores expressive metadata; Gemini can interpret it. Neither renderer guarantees word-perfect generative speech.

A simple-listening request:

```json
{"provider":"system","voice":"Samantha","model":"macos-say","segment_id":"SEGMENT_ID"}
```

For Gemini, select `provider: "gemini"`, a supported voice/model, or omit `model` to use the configured TTS preference. For Breeze, send `provider: "breeze"` and omit `model` or send `null`. `voice` is `"library:vl_…"` (that library voice's current version), a server voice ID from the last check, or empty/`null` for Default (the Bardic default library voice, not the server's default); the session pins the resolved voice's revision and seed. For Gemini, `voice` may also be `"library:vl_…"`, resolved to its current `voice_…` ID; the chapter endpoint accepts the same values. Breeze passages always use this endpoint; the chapter endpoint is Gemini-only. `segment_id` is required; `provider` defaults to `system`. Optional `voice` and `model` strings are limited to 256 and 200 characters respectively. Simple listening has its own narrator session, recipe archive, and audio directory. It does not overwrite cast voices, scene notes, enhanced selected takes, or enhanced artifacts.

A cache hit returns `{"session":{...},"audio":{...},"cached":true}` immediately, even if the provider key/device is no longer available. Lookup first checks the exact source/session recipe, then equivalent simple speech inputs across retained passages/books. The latter includes exact text, voice, provider/model and versioned synthesis recipe; it validates WAV integrity and the actual content hash before retaining a new source-bound reuse record. Audio may include `synthesis_key` and a version-1 `reuse` pointer to the original retained take. The original producer fingerprint stays unchanged; the target `recipe` and `source_anchor` describe its new source binding. `cache_hit` is a transient result flag.

New work returns `{"session":{...},"job":{...},"cached":false}`. A duplicate request for the same active session and passage returns the existing queued/running job with this same shape instead of starting another synthesis. A job with cancellation requested is never joined; conflicting busy-book work remains a conflict. Poll that job; when completed its `audio` includes `url`, duration, provider/model/voice, asset/recipe identity, and `mode: "simple"`. Failure/cancellation remains a job outcome. Finished simple audio can be recovered through `/listen/takes` after cancellation. Do not automatically repeat a generation POST following an uncertain network response; the browser retries only bounded read-only status polling.

This endpoint creates only the requested passage; Gemini chapter preparation uses the chapter endpoint below, and there is no streaming endpoint. For device voices, the browser coordinates warmup, bounded lookahead and the explicit Prepare rest of chapter action by serially calling this endpoint. Warmup aims for 10 listening seconds with at most three passages; lookahead aims for 45 listening seconds with at most 12 future passages. Speed and current clip position affect the target, and automatic continuation stays in the chapter. Chapter preparation starts at the selected passage, saves the remainder without autoplay, and stops scheduling on cancellation/error. These queues do not resume themselves after a browser/server restart.

A chapter request is `{"provider":"gemini","voice":"Kore","model":"gemini-3.8-flash-tts","segment_id":"...","intent":"play","chunking":{"ramp_seconds":[30,60],"target_seconds":420,"concurrency":2}}`. `intent` is `play` or `queue` (default); `chunking` is optional and overrides the saved `listen_chunking` preference (ramp steps 10–470 s, at most six; target 30–470 s; concurrency 1–3). `queue` without explicit ramp steps uses full-size chunks only. It returns `{session, job, joined}`. If a non-cancelled chapter job is active for the book with the same session and chapter, the request joins it: `focus_segment_id` moves to the passage, `scope_start_segment_id` extends backwards when needed, and a `play` request for a passage without audio increments `ramp_restart`. A different chapter or narrator returns 409, as does other active work on the book or a job that is closing (retry shortly). While a daily-quota block holds for the model (after a daily 429 or reaching the configured requests per day, until midnight Pacific or until limits are saved again), starting a job returns 429 without sending anything.

A `listen_chapter` job reports `progress`/`total` in passages of its scope, `chunks` (one entry per request: `n`, first/last passage IDs, `segment_count`, `chars`, `target_seconds`, `expected_seconds`, `expected_latency`, `status` `requesting`/`done`/`rate_limited`/`truncated`/`failed`, timestamps, and for finished chunks `chunk_id`, `duration`, `latency`, `flags`, matched/total boundaries), `projection` (remaining planned chunks in request order), `calibration`, `limits`, `quota` (`requests_today`, `rpd`, `resets_at`, `scope:"this library"`), `waiting_seconds` while the per-minute window is full, and the selected `chunking`. Terminal statuses include `quota_limited` with `resume_after`. Passage audio from chunks appears in `/listen/takes` and cache hits with `clip_start`, `clip_end`, `chunk_id`, `chunk_duration` and `timing:"estimated"`; the URL is the shared chunk WAV.

`POST /api/settings` also accepts `tts_limits` (`{model: {rpm, tpm, rpd}}` for supported speech models) and `listen_chunking` (as above); `/api/status` returns both plus `tts_rate` (recent requests, recent input tokens, any per-minute cooldown and remaining daily block per model in this process) and `tts_quota` (`{<selected speech model>: {requests_today, rpd, resets_at, scope:"this library"}}`, the same count chapter jobs use; added 2026-09-28, additive).

Simple listening has no separate TTS budget field or narration spending cap. The UI shows chapter passage scope and a cloud-charge warning, not a monetary estimate. Analysis ZIPs include saved simple-listening session/take metadata when those tables exist; neither export includes the separate simple-listening WAVs. Audiobook ZIPs package enhanced production audio. Sources: [audio.py](../bardic/audio.py), [take archive](../bardic/take_archive.py), [listening.py](../bardic/listening.py), [listening API tests](../tests/test_listen_api.py).

### Voice example requests

```json
{"provider":"gemini","voice":"Leda","segment_id":"segment-id","character_id":"character-id","direction":"Warm and measured.","segment_direction":"Quietly."}
```

`provider` is `system` (default), `gemini` or `breeze`; `voice` accepts the same values as simple listening (`"library:vl_…"`, a direct ID, or empty for Default; a direct Breeze ID must be in the last Breeze check). Optional fields: `voice` (256 characters), `model`, `segment_id`, `character_id` (200 each), and `direction`/`segment_direction` (3,000 each). Omit `model` to use the configured Gemini TTS preference or `macos-say` for device narration. Device requests reject a different model. There is no caller-supplied transcript field: text is always resolved from the stored book or fixed demo. `direction` requires `character_id`; `segment_direction` requires both a passage and character. Unknown fields are rejected.

An explicit passage is used even when auditioning an unsaved speaker assignment. Otherwise a selected character uses its first attributed passage, falling back to the original demo when none exists; neither selector means demo text. The browser prefers the currently selected passage for that character, then its first passage in the current chapter, before leaving the fallback to the server. References or name mentions do not substitute for attributed speech. A passage sample is an exact original prefix of at most 400 Python Unicode code points, preferentially ending at a sentence/word boundary. It includes a validated chapter-local `source_anchor`; source text is never rewritten. `truncated` identifies a shortened sample.

With a character, the recipe includes its effective direction plus the saved scene tone/direction, passage direction and cues. Optional direction fields snapshot unsaved editor choices. Without a character, simple-narrator samples omit enhanced performance inputs. Requests do not modify cast, source, reading position or selected simple/enhanced takes.

A cache hit returns `{preview, audio, cached:true}` before checking provider availability. New work returns `{preview, job, cached:false}`; matching active non-cancelled preview requests join the same job. Other active book/series work prevents new synthesis. The `voice_preview` job has one unit and retains its `preview` and completed `audio`; preview metadata includes ID, exact sample text, source/demo label, source anchor, character/passage/chapter IDs, provider/model/voice and truncation state. Audio has `mode:"preview"`, content asset ID, duration and a local URL. Matching reuse is book-scoped and includes source and effective performance recipe; it is independent of both enhanced and simple caches.

The worker snapshots configuration/key before queueing and checks cancellation before synthesis. A successfully completed in-flight take can be saved after Stop while stale playback stays cancelled. There is no generation POST retry or narration dollar cap. Gemini examples can incur charges; resource operations use stage `voice_preview`, preserving reported usage and unknown costs. Saved request/take rows and WAVs remain in the full library backup; these preview archives are not currently included in the analysis or audiobook ZIP. Source: [preview repository](../bardic/voice_previews.py), [API tests](../tests/test_voice_preview_api.py).

## Saved performances

A performance names a set of chapters and a narrator (`simple`) or the cast (`cast`), prepares them in a job, and then plays at any later time without new processing. Source: [performances.py](../bardic/performances.py); tests: [test_performances.py](../tests/test_performances.py).

| Route | Behavior |
| --- | --- |
| `GET /api/books/{book_id}/performances[?archived=true]` | `{performances:[performance]}`, newest first. Archived records are included only with `archived=true`. |
| `POST /api/books/{book_id}/performances/preview` | Local plan; no provider calls or job (creating the deterministic listening session row is allowed). Returns the fields below. |
| `POST /api/books/{book_id}/performances` | Validate, record and start the job. `{performance, job}`; `job` is `null` when nothing is missing. 400 for problems, unknown chapters or an unavailable provider; 409 while any job or series run holds the book. |
| `GET /api/books/{book_id}/performances/{id}` | `{performance}`. |
| `GET /api/books/{book_id}/performances/{id}/audio` | `{performance_id, audio:{segment_id: audio}}` for passages ready against their current source. Local read; no WAV re-validation. |
| `POST /api/books/{book_id}/performances/{id}/prepare` | Resume missing work with the same narrator session or cast snapshot. `{performance, job}` (`job` null if nothing is missing); 409 if the book is busy. |
| `PATCH /api/books/{book_id}/performances/{id}` | `{name?, archived?}` returns `{performance}`. Label change only; never deletes audio. |

Create and preview take `{"name":null,"mode":"simple","chapter_ids":["CHAPTER_ID"],"provider":"system","voice":"Samantha","model":null}`. `mode` (`simple` or `cast`) and `provider` (`system`, `gemini` or `breeze`) are required, and `chapter_ids` is non-empty (stored in book order). `voice` accepts the simple-listening values (`"library:vl_…"`, a direct ID, empty for Default) and is ignored for `cast`. `model` defaults to the configured Gemini speech model; device and Breeze use their fixed models. `name` defaults to a label such as `Kore · Gemini · 3 chapters`.

Preview returns `passages_total`, `passages_ready`, `passages_to_generate`, `requests_estimate` (Gemini simple: planned full-size chunk requests per chapter; otherwise passages to generate), `expected_seconds` (missing text at 14 characters per second plus ready durations), `chapters:[{id,title,passages_total,passages_ready}]`, blocking `problems`, advisory `notes` (for example characters without a voice for the provider, passages with no identified speaker, chapters without speaker assignments, the daily request budget), `quota` (`{requests_today, rpd, resets_at}` for Gemini, else `null`), `narrator_label` (`Kore · Gemini`, `Full cast · Device voices`) plus the resolved `mode`, `provider`, `model` and `chapter_ids`. A new cast performance reports 0 ready passages even when Studio or other performance takes will be reused.

A `performance` object is the stored record without `cast_snapshot`: `id`, `book_id`, `schema_version`, `name`, `mode`, `chapter_ids`, `provider`, `model`, `voice`, `session_id` (simple), `created_at`, `updated_at`, `archived`, `job_id`, plus `cast` (cast only: `[{character_id, name, voice_label, fallback}]` for the narrator and speakers in the chosen chapters), `job` (`{id, status, progress, total, message, error, resume_after, child_job_id}` of the latest job, or null), `progress` (`{passages_total, passages_ready, seconds_ready, chapters:[…]}`) and `narrator_label`.

Every audio object has `mode:"performance"` and `performance_id`. Simple audio is the `/listen/takes` object (`url` under `/listen/audio/`, `duration`, and for chunk clips `clip_start`, `clip_end`, `chunk_id`). Cast audio is `{url:"/api/books/{book_id}/audio-assets/{asset_id}", duration, asset_id, fingerprint, provider, model, voice, speaker_id, character_id, fallback, created_at, available:true}`.

The `performance` job carries `performance_id`, `mode`, `provider`, `model`, `total` (passages missing at start), `progress` and a message such as `Chapter 2 of 5 · passage 14 of 40`; it completes with `Performance ready`. Credentials and limits are snapshotted at start, and cancellation is checked between passages. Device and Breeze simple performances render one passage at a time (resource stage `simple_listen`). Gemini simple performances run each chapter with missing audio as a child `listen_chapter` job (`parent_id`, `intent:"queue"`, full-size chunks) inside the performance job; its progress is reported per chapter. A live listener's chapter request is refused (409) rather than joining the child, so live listening cannot stop the performance. A child that stops for the daily quota, budget, cancellation or an error ends the parent the same way (`quota_limited` with `resume_after`, and so on) with finished chunks kept. Cast performances render passage by passage (resource stage `narration`, `cached:true` for reuse). Gemini cast requests use the shared rate limiter, stop as `quota_limited` at the provider's daily quota or this library's configured requests per day, and retry a per-minute 429 at most five consecutive times. An uncertain request (timeout, dropped connection) is never resent; the job fails with completed audio kept. There is no dollar allowance for performances. A cast performance never changes the Studio's selected takes. A failure names the passage that could not be narrated. Resume validates cast WAVs, so a damaged file is narrated again; a regenerated file whose bytes match the damaged one's content address is refused rather than overwritten (the take-archive rule), which can happen with deterministic device voices. Up to three performances of different books run at once; one job per book still applies, counting every active job however many child jobs a run has made.

## Voice library

Voices are shared by every book. Routes are defined in [voice_routes.py](../bardic/voice_routes.py); request models reject unknown fields. Errors use `{"detail": ...}`: 400 validation, 404 missing, 409 busy/conflict, 502 provider failure (the provider's own text is never echoed; Breeze errors keep only the error code). Breeze requests are free but run on the owner's GPU; each Gemini create is billed.

| Route | Request / response |
| --- | --- |
| `GET /api/voices` | Local only. `{voices:[VoiceView], defaults:{breeze}, drafts:[open DraftView], providers:{breeze:<status breeze object>, gemini:{has_api_key, tts_model, state, message, checked_at, design_models, designed_voices_supported, stored_count, limit:200, project_voices:[{id, display_name, type, description, language_code, in_library, draft_candidate}]}}, builtin:{gemini:[names], system:[device voices]}}`. |
| `POST /api/voices/gemini/refresh` | Lists the Google project's stored prompted/replicated voices (metadata; no generation) and saves the listing with the key hash. Returns `providers.gemini`. 400 without a key; 409 if the key changed meanwhile. |
| `POST /api/voices/drafts` | `{provider: breeze|gemini, base_voice_id?, book_id?, character_id?, name?, description?, sample_text?}`. A book/character fills the name, description (profile plus direction) and sample text (the character's first attributed passage as an exact prefix of at most 400 code points, else the demo text). `base_voice_id` fills from that voice's current version recipe. Returns a DraftView. |
| `PATCH /api/voices/drafts/{id}` | `{name?, description?, sample_text?}`; 409 when the draft is finished. |
| `POST /api/voices/drafts/{id}/generate` | Breeze: `{book_id?, count: 1–3 (default 2)}`, synchronous, roughly audio length × count. Gemini: `{book_id, language_code: "en-US", gender?, confirm_cost: true}`; `confirm_cost` and a `book_id` (for the resource ledger) are required, and each call creates one stored, billed voice. 409 while the draft is already working. Returns the DraftView. |
| `GET /api/voices/drafts/{id}/candidates/{cid}/audio` | Bardic's retained copy of a candidate's audio (WAV). |
| `POST /api/voices/drafts/{id}/candidates/{cid}/discard` | Marks a candidate discarded; a Gemini candidate's stored voice is deleted (409 if made with another key). |
| `POST /api/voices/drafts/{id}/save` | `{candidate_id, name, mode: new|version, assign?:{book_id, character_id}, make_default?}` → `{voice, book?, assignment_error?, cleanup_error?}`. `version` requires a draft started from `base_voice_id` and becomes that voice's current version. Breeze uploads the auditioned clip as a cloned server voice; unchosen Gemini candidates are deleted. A failed assignment is reported, never rolled back. |
| `POST /api/voices/drafts/{id}/abandon` | Deletes the draft's stored Gemini candidates and marks it abandoned. |
| `POST /api/voices/breeze/clone` | Multipart: `name`, `reference_text` (exact transcript), `reference_audio` (≤ 20 MB), `consent: "true"` (required), `description?`, `book_id?` and `character_id?` (assign). → `{voice, book?, assignment_error?}`. |
| `PATCH /api/voices/{id}` | `{name?, description?}`. Breeze server names/descriptions are updated best effort; they do not change pinned revisions. Gemini is local only. |
| `POST /api/voices/{id}/current` | `{version}`: switch the current version. |
| `DELETE /api/voices/{id}?server=true|false` | Deletes the provider voices (default for designed/cloned voices; imported voices default to removal from Bardic only), then marks the library voice deleted. 409 for the Breeze default, or a Gemini voice made with another key. |
| `POST /api/voices/defaults` | `{provider: "breeze", voice_id}`. |
| `GET /api/voices/{id}/versions/{n}/audition` | The version's retained audition WAV, else the Breeze reference clip or Gemini sample fetched from the provider. |

VoiceView: `{id, provider, name, description, origin, current_version, is_default, deleted, assignable, versions:[{version, provider_voice_id, revision, made, created_at, expires_at, audition_url, server_state: ok|changed|missing|unknown|other_project, recipe}], usage:[{book_id, book_title, character_id, character_name, follows: assigned|default}], source, warnings}`. DraftView: `{id, provider, base_voice_id, base_voice_name, context, name, description, sample_text, status, busy, created_at, updated_at, candidates:[{id, kind, seed, provider_voice_id, description, sample_text, duration, created_at, expires_at, discarded, audio_url}]}`.

Switching a current version, saving a new version, changing the Breeze default and deleting a voice return 409 while a render, listen, chapter-listen or voice-example job is active for a book with a character following that voice. They re-voice followers: selected takes made with the previous voice become out of date but stay in history, and rendering again after switching back reuses the archived WAV without a provider request.

## Analysis pipeline

Step-based analysis with versioned, reviewable outputs. The contract and semantics are in [the analysis pipeline guide](ANALYSIS-PIPELINE.md); the router is [bardic/pipeline/api.py](../bardic/pipeline/api.py).

| Method and path | Purpose |
| --- | --- |
| `GET /api/analysis-pipeline` | Step definitions in pipeline order (each lists its allowed `providers`), every provider with `kind` (`model` or `service`), `self_hosted`, `needs` (`api_key` or `url`) and `has_api_key` (a key or URL is set; not a reachability check), the Local LLM's `models`, and saved per-step `{provider, model, gate}`. Service providers take `model: null`. |
| `PUT /api/analysis-pipeline/steps/{step}/settings` | Save a step's provider/model and gate (`auto` or `review`). Local steps accept only `provider: "local"`. Never starts work. |
| `GET /api/books/{id}/analysis-pipeline` | Per-step accepted/total scopes, `has_accepted` (any accepted version), origins, stale scopes, pending candidates, latest version, active and recent runs. Records outside changes first. |
| `POST /api/books/{id}/analysis-pipeline/plan` | `{steps, chapter_ids?, configs?, fresh?}` → units, cached units, requests, `service_calls` (free calls to self-hosted services, not counted as model requests), token/cost estimates, `inputs_pending`, `missing_inputs` (per step and `{step: [inputs]}` overall), `fingerprint`. No model calls. Send the same `fresh` value as the run, because it is part of the fingerprint. |
| `POST /api/books/{id}/analysis-pipeline/runs` | `{steps, mode: serial|parallel, chapter_ids?, configs?, gates?, concurrency: 1–4, fresh, limits?, expected_fingerprint?}` → `{job, run}`. Job kind `pipeline`; cancel through the jobs API. 400 when a step's required input has no accepted result and is not in the same run, or when neither `expected_fingerprint` nor any limit is sent (an uncapped run must come from a confirmed preview). 409 when the plan fingerprint changed or a job is active. |
| `GET …/steps/{step}/versions` | Version history with state (`candidate`, `accepted`, `partly_accepted`, `superseded`, `same_as_accepted`, `rejected`, `running`, `empty`) and recent decisions. |
| `GET …/steps/{step}/versions/{id}` | Generic result table (`stats`, `columns`, paged `rows`) diffed by row ID against `compare` (`accepted`, another version, or `none`), with `changed_only` and `scope` filters. `{id}` may be `accepted`. |
| `POST …/versions/{id}/preview` | `{scopes?}` → changed scopes, conflicts with manual edits, audio takes invalidated, downstream steps affected, `revision`. |
| `POST …/versions/{id}/accept` | `{scopes?, expected_revision?}`. Accepting an older version is rollback. 409 while another non-pipeline job changes the book. |
| `POST …/versions/{id}/reject` | Reject a candidate. An accepted version cannot be rejected. |

`limits` is optional and uncapped by default: the Analysis tab sends none, because the confirmed plan (its `expected_fingerprint`) is the authorization. A run with neither is refused. An API caller may still set `max_requests` (1–1,000), `max_input_tokens`, `max_output_tokens` or `budget_usd` (the cumulative book dollar guard) with the same bounds as `AnalysisLimits`. Every attempt is reserved and recorded either way.

## Pipeline inspection, artifacts, graph, search, and portable export

| Route | Response family |
| --- | --- |
| `GET /api/books/{book_id}/pipeline` | Versioned envelope with stage IDs/status/counts/dependencies, retained artifact counts/kinds, recent jobs, attempts, events, usage, capabilities, and notes. Word alignment is explicitly planned. |
| `GET /api/books/{book_id}/artifacts` | `{items,total,offset,limit}` metadata page. Filters: `kind`, `stage`, optional `current=true|false`; omit `current` for all versions. Default limit 30; valid limit 1–200 and nonnegative offset. |
| `GET /api/books/{book_id}/artifacts/{artifact_id}` | Metadata plus literal `payload`, dependency IDs, and `dependency_links:[{id,book_id}]`. A foreign artifact under the wrong book path returns 404. Follow the recorded owner to inspect earlier-book inputs. |
| `GET /api/books/{book_id}/story-map` | Versioned typed nodes/edges, chapters/scenes/passages, character IDs, source references/counts, and notes about interpretation. Node identities include the owning book. Verified source anchors refer to retained source artifacts; unavailable/unverified anchors remain null. |
| `GET /api/books/{book_id}/search?q=...&scope=book&limit=20` | `{available,query,scope,results,note,...}`. Each result identifies book/chapter/passage, exact source text/range/hash, and lexical rank. `items` remains as an alias in the current response. |
| `GET /api/books/{book_id}/analysis-export` | Portable ZIP of source/projection, graph, observations, references, series links, attempts/events, immutable artifact history, transitive input dependencies, and saved resource/listening metadata where available. No generated audio prerequisite. |

Search accepts 1–300 characters and scopes `book` or `earlier`. The latter includes the current book plus strictly earlier active volumes in its confirmed active series. Search words are combined with AND; this is not an exact-phrase/operator query language or semantic embedding search. Limit defaults to 20 and is clamped to 1–50. A punctuation-only query returns no matches with a note. If SQLite lacks FTS5, `available:false` explains that limitation; it does not start a fallback model call. Lower lexical rank means a stronger text match, not identity or speaker confidence.

Artifact metadata includes kind/logical key/stage, creation time, provider/model where recorded, `is_current`, schema version, and legacy-provenance state. Historical or rejected outputs remain inspectable without becoming accepted knowledge. The pipeline is an inspector, not a generic dependency scheduler. Its stage counts have different units and must not be summed into a global completion percentage.

The analysis ZIP includes all retained versions belonging to the selected book and the transitive artifact dependencies needed by them, which can include source excerpts/observations from earlier books. When their tables exist, `resource-operations.json`, `listening-sessions.json`, `listening-takes.json`, and `listening-chunks.json` contain the selected book's saved metadata (possibly empty arrays). Take metadata identifies separately stored audio assets; the ZIP excludes all audio binaries, API keys, and settings credentials. Some legacy outputs lack original prompts or exact attempt provenance; the export marks that absence rather than reconstructing it. See [storage/export details](ARTIFACTS-AND-STORAGE.md), [export implementation](../bardic/pipeline_view.py), and [pipeline API tests](../tests/test_pipeline_view_api.py).

## Resource accounting

`GET /api/books/{book_id}/resources?limit=100&offset=0&run_id=...` returns:

- `schema_version`, book/run scope, `totals`, stage aggregates, and run aggregates;
- paged `operations`, `total_operations`, and the effective `limit`/`offset`;
- `total_runs`, unmeasured-run counts, price-source URLs, and interpretation notes.

`run_id` is optional. Limits are clamped to 1–200 and offset to at least zero, unlike the artifact endpoint's strict out-of-range rejection. Aggregates cover the entire selected scope, not just the current operation page. The run summary list is bounded to 100; total run count is reported separately.

Operations distinguish request count, reported tokens/cache tokens, retained estimates/reservations, elapsed time, opted-in local Python thread CPU time, audio seconds, output bytes, and cache reuse. Missing measurements stay `null`/unknown and are accompanied by coverage counters. Historical runs can exist without measurements. Recorded analysis attempts are the analysis ledger; local/narration operations supplement them without double-counting.

Costs are dated estimates, not provider invoices or available credits. CPU excludes subprocesses, GPUs, and remote machines. Cached work does not represent another provider call. Source: [resources.py](../bardic/resources.py); tests: [test_resources.py](../tests/test_resources.py).
