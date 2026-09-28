# Local API guide

The API is implemented in [app.py](../spintails/app.py). It is a local application interface, not a hosted public service. Examples assume `http://127.0.0.1:8765`; use the alternate port of your isolated development server when testing. See [DEVELOPMENT](DEVELOPMENT.md) for launching with a temporary library and no cloud keys.

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
- The default process binds to loopback. Trusted hosts and same-origin write checks reject unexpected hosts/cross-origin writes; there is no account authentication layer. A command-line request without an `Origin` header is accepted locally. Browser writes must be same-origin.

### Errors and work state

Errors normally contain `{"detail":"human-readable message"}`. FastAPI validation errors use an array in `detail`.

| HTTP status | Typical meaning |
| --- | --- |
| `400` | Invalid domain operation, unsupported model, missing configured key/device capability, stale series plan, or archived target. |
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
| `POST /api/models/{provider}/refresh` | Explicit remote model-inventory request for `gemini`, `openai`, or `anthropic`. Returns catalog state/models/message/cache information. Does not generate text or establish credit. |
| `POST /api/account-checks/{provider}` | Explicit tiny text generation with the configured analysis model. Can incur a charge. Returns access/billing/quota state, timestamp, usage if reported, and dashboard links; not an exact balance. |

Settings accepts `tts_model`, `analysis_provider`, `analysis_models_by_provider`, `preprocess_models_by_provider`, and `api_keys`. `api_key` and `analysis_model` are compatibility aliases for the Gemini entries. Conflicting alias/map values are rejected. An empty key string clears that runtime key; omitted keys stay unchanged. Credentials are not persisted by this endpoint. Models/preferences are persisted.

A preference-only request:

```json
{
  "analysis_provider": "openai",
  "analysis_models_by_provider": {"openai": "gpt-6-sol"},
  "preprocess_models_by_provider": {"openai": "gpt-6-luna"}
}
```

Analysis model IDs may be custom syntactically valid IDs; the provider validates actual support when used. IDs contain 1–200 letters, digits, dots, underscores, colons, or hyphens, starting with a letter or digit. Key values are limited to 500 characters. TTS choices are restricted to the app's configured list. Prefer `/api/status` over assuming a fixed list. Identical account checks reuse an in-memory result for 30 seconds; checks are tied to the key/model configuration. Details: [provider setup](ANALYSIS-PROVIDERS.md), [account checks](ACCOUNT-CHECKS.md), [model catalog source](../spintails/model_catalog.py).

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

There is no destructive book-delete endpoint. Archiving does not reclaim disk space; saved assets remain readable. Active processing and most edits reject archived books. Counts distinguish narrative chapters from other sections. Library `database_payload_bytes` does not apportion SQLite pages/indexes/free space exactly; shared database/WAL/SHM bytes are reported separately. Sources: [library.py](../spintails/library.py), [structure guide](STRUCTURE.md), [library tests](../tests/test_library_api.py).

## Per-book analysis and review

| Route | Behavior |
| --- | --- |
| `GET /api/books/{book_id}/preprocessing` | Free local census, semantic source coverage, importance/priority signals, usage, and profile freshness/provisional state. |
| `POST /api/books/{book_id}/analysis-plan` | Preview currently known work with `AnalysisRequest`; no provider inference. Returns requested phase/provider/models, pending requests, cache counts, token/cost estimates, stage counts, coverage, notes, and the supplied limits. |
| `POST /api/books/{book_id}/analyze` | Starts/resumes a job using the same request shape. Returns a job. |
| `GET /api/books/{book_id}/analysis` | Checkpoint summary, including per-chapter progress; returns `not_started` when no checkpoint exists. |
| `GET /api/books/{book_id}/characters/{character_id}/references` | Unpaginated references for that current cast member, with kinds and source anchors. |
| `POST /api/books/{book_id}/characters` | Adds a human-reviewed cast member; `name` is required. Returns the full book. |
| `PATCH /api/books/{book_id}/characters/{character_id}` | Optional `name`, `aliases`, `description`, `voice`, `system_voice`, `direction`. Returns the full book. |
| `PATCH /api/books/{book_id}/segments/{segment_id}` | Optional `speaker_id`, `direction`, `cues`; marks the passage edited. A reviewed speaker assignment gets confidence 1.0. Returns the full book. |
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

Accepted units and completed chapter work survive later failures. Human edits remain authoritative, affected enhanced takes become stale, and source text is not replaced by model output. Whole-book scan coverage and profile freshness are separate. See [progressive.py](../spintails/progressive.py), [processing.py](../spintails/processing.py), and [chapter analysis](CHAPTER-ANALYSIS.md).

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

Names alone never establish cross-book identity. Context excludes later volumes, unconfirmed links, unavailable/archived source volumes, and invalidated evidence. Its default bound is an implementation choice in [series.py](../spintails/series.py), not a request parameter on the HTTP route.

## Series preview and execution

`POST /api/series/{series_id}/plan` accepts `provider`, `phase`, `concurrency`, and the same `limits` object used for per-book analysis. Providers are cloud analysis providers; concurrency defaults to 2, is limited to 1 or 2, and applies to discovery. The response includes ordered supplied books with nested book plans, models, known requests/cost, volume slots, `limits_per_book`, notes, and `plan_fingerprint`. Limits apply separately to each supplied book, so the possible collection-wide spend grows with the number of books.

To start, send the same body to `POST /api/series/{series_id}/process`, adding the exact returned fingerprint:

```json
{
  "provider":"openai",
  "phase":"scan",
  "concurrency":2,
  "limits":{"max_requests":25,"max_input_tokens":1000000,"max_output_tokens":100000,"budget_usd":1},
  "expected_plan_fingerprint":"FINGERPRINT_FROM_THE_REVIEWED_PLAN"
}
```

The server recomputes the plan under its store lock and compares the supplied fingerprint **before creating jobs**. The fingerprint covers the plan, book revisions/source hashes, and relevant series context. A mismatch returns 400 and queues no processing. Re-preview and review the new scope; do not silently replace the fingerprint and retry. The DTO limits the fingerprint to 64 characters and currently permits omission for direct API clients, but the UI requires a nonempty accepted fingerprint and consumes its preview on dispatch. This is optimistic scope validation, not a reservation that freezes data between requests.

The start response is a parent series job. `GET /api/series/{series_id}/runs` returns `{"runs":[...]}` with up to 20 parent runs and their child job records. The parent uses `book_id: "series:SERIES_ID"`; child jobs use actual book IDs. Discovery may run on two independent books; profiles/direction use reading order. Missing/planned/archived books do not run. A failed or allowance-limited book stops new work; already finished outputs remain reusable. Full-run phases share each book's run request/token caps, while its dollar allowance includes earlier tracked spend.

Source: [series_processing.py](../spintails/series_processing.py). Contract tests: [test_series_processing.py](../tests/test_series_processing.py), [series UI tests](../tests/series_processing_ui_test.js).

## Jobs and cancellation

`GET /api/jobs` returns up to the 100 most recent jobs. Optional `book_id` filters to one book or the synthetic series-parent owner before that bound applies. The route exposes no limit/offset parameters and no single-job GET route: select the returned record by its `id`, or use the series run endpoint.

Common job fields are `id`, `book_id`, `kind`, `status`, `progress`, `total`, `message`, `error`, `created_at`, `updated_at`, and `cancel_requested`. Depending on kind, records include provider/model/phase, selected passage/session, series children, or a completed simple `audio` object. Progress units depend on the job and are not a universal percent.

Active statuses are `queued` and `running`; terminal outcomes include `completed`, `failed`, `cancelled`, `interrupted`, and `budget_limited`. `POST /api/jobs/{job_id}/cancel` takes no meaningful body (send `{}`) and returns the current updated record. Queued work cancels before starting. Running work receives a cancellation flag and stops at the next safe boundary. Cancelling a series parent also marks/cancels its children. Completed records are returned unchanged.

A remote request already sent can complete and be billed after cancellation. Validated outputs and completed audio are retained. On restart, incomplete jobs become interrupted. Resume uses the relevant analyze/render/listen/process endpoint rather than reviving an old job ID.

## Enhanced narration, simple listening, and audio downloads

| Route | Behavior |
| --- | --- |
| `POST /api/books/{book_id}/render` | Enhanced production job. `{provider:"system"|"gemini", scene_id?, segment_id?, force:false}`. Omitted selectors select the whole book; both selectors, if given, are intersected. |
| `GET /api/audio/{book_id}/{segment_id}` | Current valid enhanced passage WAV. Stale/unavailable selection returns 404. |
| `GET /api/books/{book_id}/audio-assets/{asset_id}` | Retained enhanced audio asset bytes for that book. |
| `POST /api/books/{book_id}/listen` | One simple narrator for one selected passage. See request and response shapes below. |
| `GET /api/books/{book_id}/listen/takes?session_id=...` | `{session, takes:[{segment_id,audio}]}` for saved matching simple takes. No generation. |
| `GET /api/books/{book_id}/listen/audio/{asset_id}` | Retained simple-listening WAV, scoped to the owning book. |
| `GET /api/books/{book_id}/export` | Audiobook ZIP from valid enhanced takes; requires at least one valid take. Complete chapters are assembled; partial output lists missing passages. |

Enhanced rendering uses each segment's selected character, scene, and performance metadata. TTS model comes from settings. `force:true` generates another take and preserves older bytes. Model/voice/performance edits affect reuse. Device narration ignores expressive metadata; Gemini can interpret it. Neither renderer guarantees word-perfect generative speech.

A simple-listening request:

```json
{"provider":"system","voice":"Samantha","model":"macos-say","segment_id":"SEGMENT_ID"}
```

For Gemini, select `provider: "gemini"`, a supported voice/model, or omit `model` to use the configured TTS preference. `segment_id` is required; `provider` defaults to `system`. Optional `voice` and `model` strings are limited to 256 and 200 characters respectively. Simple listening has its own narrator session, recipe archive, and audio directory. It does not overwrite cast voices, scene notes, enhanced selected takes, or enhanced artifacts.

A cache hit returns `{"session":{...},"audio":{...},"cached":true}` immediately, even if the provider key/device is no longer available. New work returns `{"session":{...},"job":{...},"cached":false}`. Poll that job; when completed its `audio` includes `url`, duration, provider/model/voice, asset/recipe identity, and `mode: "simple"`. Failure/cancellation remains a job outcome. Finished simple audio can be recovered through `/listen/takes` after cancellation.

The API creates only the requested passage. The browser controls sequential playback and stops simple autoplay at the chapter boundary. Simple listening has no separate TTS budget field in its request. Analysis ZIPs include saved simple-listening session/take metadata when those tables exist; neither export includes the separate simple-listening WAVs. Audiobook ZIPs package enhanced production audio. Sources: [audio.py](../spintails/audio.py), [take archive](../spintails/take_archive.py), [listening.py](../spintails/listening.py), [listening API tests](../tests/test_listen_api.py).

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

The analysis ZIP includes all retained versions belonging to the selected book and the transitive artifact dependencies needed by them, which can include source excerpts/observations from earlier books. When their tables exist, `resource-operations.json`, `listening-sessions.json`, and `listening-takes.json` contain the selected book's saved metadata (possibly empty arrays). Take metadata identifies separately stored audio assets; the ZIP excludes all audio binaries, API keys, and settings credentials. Some legacy outputs lack original prompts or exact attempt provenance; the export marks that absence rather than reconstructing it. See [storage/export details](ARTIFACTS-AND-STORAGE.md), [export implementation](../spintails/pipeline_view.py), and [pipeline API tests](../tests/test_pipeline_view_api.py).

## Resource accounting

`GET /api/books/{book_id}/resources?limit=100&offset=0&run_id=...` returns:

- `schema_version`, book/run scope, `totals`, stage aggregates, and run aggregates;
- paged `operations`, `total_operations`, and the effective `limit`/`offset`;
- `total_runs`, unmeasured-run counts, price-source URLs, and interpretation notes.

`run_id` is optional. Limits are clamped to 1–200 and offset to at least zero, unlike the artifact endpoint's strict out-of-range rejection. Aggregates cover the entire selected scope, not just the current operation page. The run summary list is bounded to 100; total run count is reported separately.

Operations distinguish request count, reported tokens/cache tokens, retained estimates/reservations, elapsed time, opted-in local Python thread CPU time, audio seconds, output bytes, and cache reuse. Missing measurements stay `null`/unknown and are accompanied by coverage counters. Historical runs can exist without measurements. Recorded analysis attempts are the analysis ledger; local/narration operations supplement them without double-counting.

Costs are dated estimates, not provider invoices or available credits. CPU excludes subprocesses, GPUs, and remote machines. Cached work does not represent another provider call. Source: [resources.py](../spintails/resources.py); tests: [test_resources.py](../tests/test_resources.py).
