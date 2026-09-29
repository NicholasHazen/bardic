# UI/API decoupling: review and proposal

This proposal was written on September 28, 2026. Part of it is now implemented; see [the status](#direction-update-and-status). It reviews how the browser UI (`bardic/static/`) is coupled to the FastAPI server today. It then proposes an interface contract and a staged path that could end in separate client and server repositories. Feature work continues throughout. Evidence is cited as `file:line` at commit `1089a07`.

**Method.** Two independent code surveys covered the client and the server. A separate red-team pass then checked every count and citation. It also tested the proposed mechanisms against the installed FastAPI 0.141 and Pydantic 2.13, using throwaway scripts with no network and no real library. Their corrections are folded in below.

## Direction update and status

**Update, September 28, 2026:** the owner has answered the open question about what drives the split. Bardic's server will eventually move away from Python, and dedicated clients will be built. That changes three things:

- **The contract becomes normative.** The Python code generates `contract/openapi.json` today, but the checked-in file is the source of truth. It must outlive the Python server, which a replacement server has to satisfy. Every API change updates it in the same commit, as described in [the API workflow](API-WORKFLOW.md).
- **Same-origin applies only to the browser UI.** Dedicated clients that are not served by the Bardic server need authentication and a threat model. That work moves from "out of scope" to a prerequisite for shipping such a client. It is not designed yet. CORS for browser-based clients exists since contract 0.4.1 as an operator opt-in (`BARDIC_CORS_ORIGINS`, off by default, no credentials, no authentication; see [API.md](API.md#transport-and-security)).
- **A port needs a black-box conformance suite.** The current tests call the Python app in process. A suite that runs over real HTTP against any base URL, and checks the recorded contract, is the acceptance test for a replacement server. This is planned, not built.

**Implemented** (Phases 0–1 of the original plan, for the server side):
- `bardic/apispec/` describes every operation: 99 as of contract 0.4.0.
- `contract/` holds the generated spec, a readable reference and a changelog.
- The test suite validates every API response against the contract.
- The workflow rules are in AGENTS.md and [API-WORKFLOW.md](API-WORKFLOW.md).
- Stable error codes (0.2.0), the version handshake (`Status.contract` and the `Bardic-Contract-Version` header) and `GET /api/jobs/{job_id}` (0.4.0).
- A first audit for a strongly typed generated client (0.4.0): see [the audit](#contract-audit-for-generated-clients-contract-040).
- One deliberate breaking reshaping for generated clients (0.5.0), settling what the audit left open: tagged unions everywhere (including `Job`), the always-sent-fields pass, one name per concept, named provider enumerations. See [the resolution](#resolution-in-contract-050) and the changelog.

**Still open:**
- The client-side request-conformance helper for the Node tests.
- The `BardicApi` client module.
- Runtime enforcement of the views.
- Authentication for off-origin clients.
- The HTTP conformance suite.
- A breaking-change differ. Versions are forced per change, but a person classifies each one.
- The optional response fields that stay optional on purpose after 0.5.0 (the changelog lists them and why).

## Resolution in contract 0.5.0

The owner decided (2026-09-28) to make one breaking change before any generated client, Rust server or external user exists, and to update the browser UI in the same change. It was checked by generating code from the contract: typify 0.8.0 (Rust types), progenitor 0.15.0 (a Rust client), openapi-typescript (`npm run contract:codegen`) and the recorded real responses of the test and conformance suites (about 4,100 payloads) deserialized with the generated Rust types and serialized again. What it settled, against the audit below:

- **Unions.** Every union of objects is a named `oneOf` with a `discriminator`, one required single-value string `enum` tag per member and a one-to-one mapping: `ListeningAudio`, `PerformanceAudio`, `ListenResult`, `VoicePreviewResult`, `SeriesCharacterLinkResult`, `SeriesVolume` (tag `kind`, not the four-status `status`), `PipelineVersionRow` (tag `step`), `StoryMapNode` and `StoryMapEdge` (tag `type`). Where a tag did not exist it was added to the responses. `Job` and `SeriesRunChild` are `oneOf` unions whose branches are written inline, one branch per job kind (the table in the changelog), so typify generates a `#[serde(tag = "kind")]` enum for them. The audio unions no longer decode a chunk clip as a passage take. A build-time check and a test refuse an untagged or nested union, an `anyOf` of objects and a bare `null` type.
- **Always-sent fields.** 127 response fields of existing schemas became required (and every field a `Job` kind always has), nullable where the value can be null, with a presenter backfill (`bardic/wire.py`). The `PipelineAttempt` backfill happens only in the inspector, so the analysis export bundle is unchanged and its `schema_version` stays 2. Left optional and listed: sparse per-passage extras, Breeze-specific audio extras, kind-specific fields that `Job` now declares per kind, and the stored-rows story map references.
- **Shapes.** Two always-null fields were removed (`AccountCheck.balance`, `SeriesVolumeSlot.book_id`). Every integer has `int32` or `int64`. The 416 of the range-capable files is the JSON `Error`, which made progenitor fail on eight operations before. The advice on unknown enum values now says what generators actually do.
- **Names and types.** The wire says passage, never segment (the server translates at its boundary in `bardic/wire.py`; storage is unchanged); a Breeze voice's `revision` is `voice_revision`; sentence timing has `start_seconds` and `end_seconds`; counts have `*_count` or `*_units`; result-table lists are arrays and their labels are identifiers; providers are declared once per domain; five duplicated shapes are merged.
- **What generation still cannot do.** progenitor 0.15 does not generate the two multipart operations (`importBook`, `cloneBreezeVoice`), so a Rust client writes those by hand. typify emits closed enums, so a Rust client post-processes response enums to add an unrecognized variant (see "Compatibility rules for clients" in the contract). Optional-nullable response fields turn `null` into an absent key when a Rust client serializes a response again; a client that only reads is unaffected. Untyped JSON remains in `ArtifactDetail.payload` and `PipelineResultRow.previous`.

## Contract audit for generated clients (contract 0.4.0)

The audit below is the state at 0.4.0, kept as the record of what was found; [the resolution](#resolution-in-contract-050) says what 0.5.0 changed.

Written for the decision to fix the contract before a Rust server and generated clients exist. Method: every response schema was listed; the test suite was run with a temporary recorder of which optional properties each observed response carried; then each candidate was traced through the code that builds it, including stored legacy shapes, because the tests over-represent current data. The recorder is not kept.

**Unions.** The volume union of a series (`Series.volumes`, `SeriesPlan.volumes` and `SeriesPlan.skipped_volumes`) has a real string discriminator, `status`. No other union of objects has a literal tag that every variant always carries:

| Union (operation) | Why it cannot be tagged today |
| --- | --- |
| `ListenCached` or `ListenQueued` (`listenToPassage`), `VoicePreviewCached` or `VoicePreviewQueued` (`startVoicePreview`) | The tag is a boolean, `cached`. An OpenAPI discriminator maps string values, and Pydantic emits `"True"`/`"False"` keys that do not match JSON `true`/`false`, so a generated client would be wrong. Variants differ in required fields (`audio` or `job`), so an untagged union works. |
| `SeriesCharacterLinkState` or `SeriesCharacterUnlinked` (`linkSeriesCharacter`) | Only the unlinked variant has a tag (`linked: false`, a boolean). One PUT that links or unlinks is the underlying shape. |
| `ListeningPassageAudio`, `ListeningChunkClipAudio`, `VoicePreviewAudio`, `PerformanceCastAudio` (`Job.audio`, `ListenCached.audio`, `ListeningTake.audio`, `getPerformanceAudio`) | Audio objects carry no kind field (a 0.2.0 decision). A clip is told from a passage take by the presence of `chunk_id`; a job's audio kind follows `Job.kind`. |
| `PipelineVersionDetail.rows` (`getAnalysisPipelineStepVersion`) | The row shape follows the parent's `step_id` (an open set that can grow); rows carry no tag. The response is documented as a generic table (`columns` and rows of `id`, `scope` and cells). |
| Scalars: the values of `PipelineVersionDetail.stats` (integer, number, string or null), `Error.detail` (string or issue list), `ValidationIssue.loc` items | Not object unions; a client needs an untagged enum. |

Adding a string `kind` (audio) or `step` (rows) field is additive, but a closed tagged union then fails on a value it does not know, which conflicts with the rule that clients tolerate unknown values. That trade-off is the owner's.

**Response fields made required (0.4.0, 12).** `BookAnalysisSummary.notes`, `LibraryVoiceRecipe.description`, `AudioTakeBreezeInfo.request_id`, `ResourceOperation.{run_id, stage, unit_key, created_at, request_count, estimated_cost_usd, cost_basis}` and `PipelineAttempt.{book_id, run_id}`. Each is set by every code path (traced); `LibraryVoiceRecipe.description` needed a one-line presenter backfill (`""`) for a version that recorded no recipe, which no route creates. The shapes the tests never produced (a `cache_reuse` row, a sparse attempt, a recipe-less version) now have tests.

**Left optional although the tests always saw them, because a stored or code path can omit them:** `Book.structure_version` and `BookChapter.{kind, title_source, source_href, logical_sections}` (books stored before they existed; a structure rollback restores that shape), `BookSpeakerCheck.*` (the `no_quote` result stores only `source` and `result`), `BookScene.{title, summary, tone, direction}` (accepting a directing version pops a field the proposal leaves null), `CharacterReference.{confidence, provider, model}` and `StoryMapReference.confidence` (older evidence rows, modeled by test fixtures and named in the view; `StoryMapReference` already requires `provider` and `model`, which older rows may also lack), `PipelineStepRun.incomplete_scopes` (unset until the run finishes its units), `AccountCheckUsage.*` (a count is kept only when positive), `SeriesRun.{book_ids, child_job_ids}` (absent on a parent whose creation was interrupted), `PipelineAttempt.status` (set on every attempt the server records, but the presenter copies only keys the stored record has), and the other `ResourceOperation` and `PipelineAttempt` fields (a `cache_reuse` row, an in-flight attempt).

**Could become required with a presenter change (not done: needs a decision, and for some an export `schema_version`):** backfilling `null` for every allowlisted `PipelineAttempt` field in `public_attempt` (also used by the analysis export bundle, whose content would change) and every `ResourceOperation` key (about 20 fields); backfilling `BookSpeakerCheck` in the `no_quote` branch; backfilling `null` reference provenance in the presenter. Legacy chapter fields cannot be backfilled without guessing.

**Fixed as part of the same version (0.4.0, BREAKING, see the changelog):** the `Job.analysis_limits` union of two identically shaped objects, token and byte totals typed as floating point, and an untyped `settings` object.

**Not fixed, for the owner:** `Job` is one object with 52 optional fields whose presence depends on `kind` (`SeriesRun` and `SeriesRunChild` repeat them); a `oneOf` by `kind` would give each job kind a real type at the cost of a breaking schema change and unknown-kind failures. Also: pipeline result rows encode lists as comma-separated strings (`aliases`, `cues`, `edited`) and use display labels as values (`kind: "Quotation"`); the same key means different types (`revision` is an integer for books and a string for voices, `AudioTakeSentenceSpan.start` and `end` are seconds while `start` and `end` elsewhere are code-point offsets, `cached` is a boolean or a count, `evidence` a list or a count); `provider` is an enumeration in 17 schemas and a plain string in about 25 others; near-duplicate shapes (`PipelineProviderModel` and `AnalysisCatalogModel`, `ResourceOperation` and `PipelineAttempt` with `estimated_cost_usd` versus `charged_estimate_usd`, the audio objects' repeated `reuse`, `provider_timing`, `breeze` and `voice_revision`, two source-anchor and two logical-section shapes); `segment_id` versus `passage_id` naming. Genuinely arbitrary JSON remains in `ArtifactDetail.payload` and `PipelineResultRow.previous`.

**Superseded details.** Where the original text below disagrees with the implementation, the implementation wins:
- **Versioning** follows [the contract changelog](../contract/CHANGELOG.md): semantic 0.x versions, a new version for every change, and a hash recorded per version. There is no integer major or `api` block yet.
- **Enumerations** use `Literal` types in the views. They are checked only in tests, because the views are not applied at runtime. Before any route is enforced at runtime, its enumerations must be made tolerant.
- **Response validation** runs on every response the existing test suites produce, not on generated example files, which were not built.
- **Job progress.** `GET /api/jobs/{job_id}` (`getJob`) exists since contract 0.4.0, so finding #9 below is answered on the server side. The six UI pollers still list jobs; moving them is a client change that has not been made.

The review and reasoning below are otherwise unchanged from the original proposal.

## TL;DR

Build the contract first. Treat the repository split as a later, optional, mostly mechanical step.

- **Server side.** The server publishes a checked-in, generated description of what it accepts and returns. Every existing API test automatically checks real responses against that description.
- **Client side.** The UI's tests check that every request they make matches it.
- **Later.** A single client API module, stable error codes and a version handshake follow.
- **Runtime.** Keep one origin: the server keeps serving the client's files, so no CORS or security-model change is needed.
- **Payoff.** Contract drift shows up the moment it happens, and moving the client to its own repository becomes a history-preserving file move whenever there is a reason to do it.

## Key decision

**Code-first contract, same-origin runtime, split only when a driver and the numbers justify it.**

**Code-first.** Pydantic view models in the server generate the contract. The result is committed as `contract/openapi.json`, not written by hand.
- The server already has 35 strict request DTOs and a live `/openapi.json`.
- A hand-written spec would be a second source of truth, and neither side has a codegen toolchain to keep it honest.

**Same-origin.** After a split, the server still serves the client's published static files.
- There is no authentication. The write guard (`app.py:899-904`) and trusted-host checks (`app.py:897`) are the protection against CSRF and DNS rebinding.
- Browser storage is per-origin and already holds server IDs (`app.js:311-312`, `listen.js:73-75`).
- Separate repositories do not require separate origins.

**Split later, and only for a stated reason.** Phases 0–2 pay for themselves inside one repository.
- Parallel agent worktrees get reviewable contract diffs.
- Client tests stop passing against shapes the server no longer sends.
- Phase 4, the actual split, is deferred until the motivation is stated (see [open questions](#risks-and-open-questions)) and the [split gate](#how-we-know-it-is-working) is met.
- In the repository's whole history (17 non-trivial commits), 10 changed both `bardic/static/` and server Python. While features look like that, a split turns each one into two coordinated PRs.

---

## Review: where the coupling is today

### What is already in good shape

- **Every JSON write uses a strict request DTO** (`extra="forbid"`). There are 35 classes across `app.py`, `voice_routes.py` and `pipeline/api.py`, and no route reads raw `request.json()`. The request half of the contract already appears in OpenAPI.
- **The client uses only root-relative `/api/...` paths.** There are no hard-coded hosts or ports, and every path the JS calls exists on the server.
- **UI components already have namespaced, documented render interfaces** (`window.Bardic*`; see [frontend contracts](DEVELOPMENT.md#frontend-namespace-contracts)).
- **Long-running work uses polling only.** There is no SSE or WebSocket to specify.
- **The OpenAPI document is deterministic.** Its hash was unchanged across different `BARDIC_ALLOWED_HOSTS`, `BARDIC_LAN_NAME`, `BARDIC_DATA_DIR` and `PYTHONHASHSEED` values, and no host or path leaks into it. It can be snapshotted.

### Coupling points, ranked by how much they block separation

| # | Finding | Evidence | Why it matters |
|---|---|---|---|
| 1 | **No route declares a response shape.** All 96 operations have an empty 200 schema, and the 9 binary routes are documented as JSON. | `docs/API.md:7`; no `response_model` anywhere | The response half of the contract lives only in the code and in the UI's assumptions. |
| 2 | **Storage formats reach the wire unchanged.** `GET /api/books/{id}` is a deep copy of the stored book with some decoration. `/api/status` spreads the persisted preferences. `/api/jobs` is raw `jobs.body`, which grows through `update_job(**fields)`. | `app.py:638-656`, `app.py:942-968`, `store.py:215-221` | Any storage change is silently an API change. Internal fields such as `edited_fields`, `former_names`, `speaker_check`, take `fingerprint` and the absolute `data_directory` are public by accident. Most of them are nested. |
| 3 | **The same concept has several shapes.** There are three job shapes. Audio and media objects with a `url` are built in seven places. Some fields are aliased (`segment_count`/`passage_count`, search `items`/`results`, `configured`/`has_api_key`). The client also builds cover URLs itself. | `pipeline_view.py:163-165`, `performances.py:312`; `app.py:649`, `listening.py:246,376`, `performances.py:269`, `voice_previews.py:190`, `voice_routes.py:136,169`, `library.py:151`; `app.js:516`, `library.js:43` | There is no single definition to code against. |
| 4 | **Errors are English sentences only.** There are 144 `HTTPException` raises plus 268 bare `raise ValueError/KeyError`. Global handlers turn any `KeyError` into a 404 and any `ValueError` into a 400. Many messages refer to UI layout ("in Settings"). | `app.py:912-918`; e.g. `app.py:1617`, `store.py:81` | A client cannot branch without parsing strings. An incidental `KeyError` bug looks like "not found". |
| 5 | **There is no client HTTP layer.** There are 11 private wrappers and 2 raw `fetch` calls. Error parsing differs between them, and only 5 handle 422 arrays. There is no base URL. | `app.js:34`, `listen.js:77`, `voices.js:160`, … `resources.js:44` | There is nowhere to put a base URL, a version check or error codes. |
| 6 | **Client tests use hand-written payloads.** All 22 Node tests supply response objects as inline literals: 18 stub `fetch`, and 4 stub app.js's `request`. Nothing checks those literals, or the requests the tests make, against the server. Pytest runs only 13 of the 22. | e.g. `tests/library_ui_test.js:27-30`, `tests/listen_job_poll_test.js:16-17`; shims `tests/test_*_ui.py`, `tests/test_listen_player.py` | Client tests can pass against shapes the server no longer produces. After a split, this is what would decay first. |
| 7 | **The client duplicates domain knowledge.** It has its own copies of the job status vocabulary (about 7), the Gemini voice list (2), the `Kore` default, `macos-say`, model fallbacks, voice resolution and provider labels. The server already sends voices and defaults in `narration_providers`. | `listen.js:10,17,19-43,100`, `voices.js:11-12`, `app.js:372,1622`, `pipeline.js:10-13`; `audio.py:136-143` | Two sources of truth, which will drift across repositories. |
| 8 | **There are no wire versioning or concurrency semantics.** There is no API version, and the existing `schema_version` fields describe storage. The book `revision` is incremented but no PATCH checks it. A stale series plan returns 400 where a stale pipeline plan returns 409. | `app.py:1268`, `series_processing.py:52,68`, `pipeline/api.py:351-357` | Once client and server ship independently, a mismatch must be detectable. |
| 9 | **Job progress is found by listing.** There is no `GET /api/jobs/{id}`. Six UI call sites poll `GET /api/jobs?book_id=` and search the list. `bardicctl` also reads `/api/jobs?active=true` and expects a bare list. | `app.js:1644,1655`, `listen.js:287,354,677,743`, `voice-preview.js:59`; `bardic/service.py:223-248` | "Watch my job" is an implicit contract with three consumers, one of which is the operations tool. |
| 10 | **Some request-side rules are two-sided.** The diagnostics event allowlist and ID formats exist in both `diagnostics.js` and the server, and they differ: the client allows 9 events and a generic ID regex, the server 10 events and exact formats. The client sends best-effort, so drift silently drops events. The custom 422 body is not the advertised `HTTPValidationError`. | `diagnostics.js:5-15`; `app.py:162`, `diagnostics.py:28-33`, `app.py:922-926` | Enums and ID formats are contract, not opaque strings. |
| 11 | **The runtime assumes one origin.** The write guard compares `Origin` with `Host`, and there is no CORS (opt-in CORS has existed since contract 0.4.1; it is off by default). Media URLs built by the server are root-relative, and `index.html` loads `/static/...`. The middleware also overwrites the cover's `private, max-age=300` with `no-store`, so its `?v=` cache-buster does nothing. | `app.py:897-909`, `app.py:1508`, `index.html:12-33` | This is fine if the server keeps serving the UI, as proposed. It is a security design task if not. |
| 12 | **UI copy is persisted into storage.** Accepting a pipeline step writes `'{label} accepted in the Analysis tab.'` into the book's notes. | `pipeline/api.py:413-414` | Stored data depends on the UI's layout. |

These are client-internal costs, not contract problems:
- app.js is 2,112 lines with 189 top-level declarations.
- 7 Node tests cut it apart by searching for function names (`tests/listen_job_poll_test.js:8-11`).

They move with the client. Fix them as the code is touched.

---

## Target shape

```
TODAY                                 TARGET (one repo until a split is justified)
─────                                 ──────
static/*.js ──fetch──► handler        client ──► BardicApi ──► handler returns dict,
  11 wrappers          returns          (base URL, codes,        described by a View
  inline payloads      stored JSON       version check)              │
                                                                     ▼
            contract/openapi.json  ◄── generated, snapshotted, diff classified
                 │           ▲
   Node fetch stub asserts   │ pytest hook validates every JSON response in
   every (method, path)      │ the existing API tests against its View
   against it                │ (fails on undeclared fields at any depth)
```

The contract is the checked-in `contract/` directory. The server generates it, and tests on both sides read it. After a split, the server repository publishes it and the client pins a revision of it.

---

## The contract: what it consists of

### 1. Response views (server)

- **Add Pydantic view models** in a new `bardic/schemas/` package: `BookView`, `JobView`, `StatusView`, `LibraryView`, `VoiceLibraryView`, `PipelineOverviewView` and so on. Handlers keep building dicts. The views describe those dicts.
- **Declare views without changing runtime behavior.** Attach each view with `responses={200: {"model": View}}`. That puts the schema into OpenAPI and does nothing at runtime (verified).
- **Views use one base class with `extra="allow"`.** Test-time strictness comes from a helper that walks the validated model tree and fails on any non-empty `__pydantic_extra__`, reporting the path (for example `$.segments[0].speaker_check`).
  - A strict subclass with `extra="forbid"` is not enough, because Pydantic applies config per model, so nested extras would pass (verified).
- **Validate every real response, not just examples.**
  - A test-only hook, a `conftest` fixture wrapping `TestClient`, validates every JSON response in the existing API suites against the route's declared view. That covers 22 test files and the edited, audio, series, quota and interrupted states they already construct.
  - This is what proves a view complete. A demo book alone cannot: it has one chapter, no audio, no cover and no edits.
- **Enum-like fields stay `str` in the runtime views, with their known values documented.** The test-time walker checks those values against strict sets.
  - Enforced `Literal`s would turn a stored value from an older or newer version (job bodies, legacy `.spintails` books) into a 500 for the whole route.
  - `/api/jobs` returning a 500 would also break `bardicctl`'s restart safety check.
- **Enforce later, per route.** Switch a route to `response_model` only once its view is complete and permissive.
  - Enforcement *filters* undeclared fields, which is what finally stops internal fields leaking.
  - It also *validates*: any mismatch in stored data becomes a 500. Tolerant field types (optional, `str`, defaults) are therefore required first.
  - Pydantic's cost is not the obstacle: validating a synthetic 3.7 MB book with 6,000 segments took 14 ms, against 32 ms for the `deepcopy` that `present()` already does.
- **Separate the wire from storage by declaration first.** Every field the wire emits today goes into its view, marked either public or internal/deprecated (OpenAPI `deprecated: true`). The UI stops reading internal fields, and enforcement then removes them.
- **One shape per concept.**
  - `JobView` has common fields plus documented optional fields per `kind`.
  - `MediaRef` covers the seven URL builders.
  - Where a response really is a subset, use a named subset model.
- **Binary routes declare their media type.** Use `responses={200: {"content": {"audio/wav": {}}}}` (or zip, jpeg). Each states its intended caching. The cover should be cacheable, given its content-hash `?v=`.
- **Request-side enums and ID formats are contract too.** A test compares the diagnostics allowlists in `diagnostics.js` with the `DiagnosticRequest` schema, and the custom 422 body is declared.

### 2. Error envelope

```json
{"detail": "Human-readable message", "code": "book_busy", "fields": [...]}
```

- `detail` stays, for compatibility and display. `code` is a stable snake_case identifier. `fields` appears only on 422.
- Add an `ApiError(status, code, detail)` and migrate raises as they are touched. Do first the ones the UI acts on: busy book, missing key or URL, stale plan or revision, not found.
- Give the `KeyError`/`ValueError` handlers generic codes (`not_found`, `invalid`) now. Replace the bare catch-alls with domain exceptions over time, so a coding error becomes a 500, not a misleading 404.
- Every stale-precondition failure returns 409 with its own code.
- **Copy policy:**
  - The server may send human text that describes runtime facts: progress, provider failures, validation reasons.
  - Anything the client *acts on* also needs a code or an enum.
  - The client owns wording about UI places.
  - New code does not persist UI copy into storage.

### 3. Version handshake and compatibility policy

- **Advertise the version.** `GET /api/status` gains `api: {"version": 1, "revision": "<first 12 hex characters of the SHA-256 of the checked-in contract/openapi.json>"}`, and responses carry a `Bardic-Api: 1` header.
  - The revision comes from the committed file, so a FastAPI upgrade that reshapes the generated schema shows up as a reviewed contract diff.
  - There is no `/v1` path prefix.
- **The client declares the major version it supports.** On a mismatch it shows a blocking "UI and server versions do not match" notice.
- **Change rules:**
  - Adding a response field, an optional request field, an endpoint or a tolerated enum value is *minor*.
  - Removing, renaming, tightening or re-meaning anything is *major*.
  - Before a split, a major change lands in one PR and bumps the version.
  - After a split, deprecate first, ship the client that stops using the field, then remove it.
- **Consumers ignore unknown fields and render unknown enum values as "unknown."** This one rule makes additive server changes safe.
- **A small Python OpenAPI differ in the check command labels each snapshot diff minor or major.** This also makes the split gate measurable.
- **Contract consumers are: the browser client, `bardicctl` (`service.py:223-248`, `/api/jobs` as a bare list) and stored client state.**
  - localStorage keeps book, chapter, segment, session, performance and voice-library IDs, so ID stability is part of the contract.

### 4. Snapshot hygiene

- Generate stable operation IDs with `generate_unique_id_function`. Otherwise renaming a Python handler rewrites the contract: today's IDs look like `import_book_api_books_post`, and the multipart schema names follow them.
- Snapshot in a normalized, sorted form. It is about 6,200 lines as one file. If merge conflicts between parallel worktrees become common, write one file per operation. In either case, the documented rule is to resolve conflicts by regenerating, never by hand-merging.

### 5. Client side

- **`BardicApi`** is a new `static/api.js`, a classic script like the other components. It provides:
  - `request(method, path, {json|form})`;
  - an `ApiError` with `status`/`code`/`detail`/`fields`;
  - `url(serverPath)` for server-provided media URLs;
  - a `base` setting (default `''`);
  - the version check.
- **Request conformance.** A shared Node fetch-stub helper checks at runtime that every (method, path) a test issues matches an operation in `contract/openapi.json`.
  - This covers the 28 `base(...)` call sites, the template and regex-built paths, and methods, with no static allowlist.
  - `<a href>` links (`index.html:178`, `pipeline.js:161`) get one explicit check.
- **Response fixtures.** Existing inline payloads can stay at first. The shared helper can record the payloads tests serve to a JSONL file that a pytest validates against the views. Tests that need realistic data can load generated examples from `contract/examples/`.
- **Examples are deterministic by construction.** Seed ID and clock generation instead of normalizing afterward. Blank the provider environment variables, block `httpx` (as `tests/test_listen_api.py:19-24` does), and fake system voices and `ffmpeg` lookup. Use only the synthetic demo text. The offline local analyze job is a fine source of job examples.
- **Tests.** One command, or one Python shim, runs every Node test. Today pytest runs only 13 of the 22.

### 6. Transport and serving

- **Add `GET /api/jobs/{id}`** and move the six UI pollers onto it. `/api/jobs` stays a bare list for `bardicctl` and older checkouts.
- **Server-built media URLs stay root-relative**, documented as relative to the API base. The client resolves them through `BardicApi.url`.
- **A `BARDIC_UI_DIR` setting**, which defaults to the in-repository `bardic/static`, serves the client from elsewhere. It must follow these rules:
  - Point it at a dedicated published folder (the client's `public/` or a release copy), never a repository root. Starlette's `StaticFiles` serves `/.env` and `/.git/config` (verified), and would do so on the LAN when `BARDIC_LAN_NAME` is set.
  - Refuse dotfiles.
  - Show a minimal "UI not installed" page instead of failing `create_app` when the directory is missing.
  - The owner's service points at a release copy, not a working checkout. Files are read on every request, so a `git pull` would change the live UI without the merge-and-restart step that AGENTS.md requires.
  - `bardicctl status` reports the UI directory and its contract revision.
  - `./bardicctl dev start --ui <path>` serves a client checkout's published folder on a development server.

---

## Phased path

Each phase adds to the previous one and keeps behavior the same. Feature work continues throughout. Directories do not move until Phase 4: a mass rename would conflict with every in-flight worktree branch for no functional gain.

### Phase 0: snapshot and guardrails (1–2 days)

- **Rules.** Adopt these rules in AGENTS.md and DEVELOPMENT.md:
  - new or changed endpoints get a view;
  - new UI HTTP goes through `BardicApi` once it exists;
  - new errors the UI acts on get a code;
  - no UI copy is persisted.
- **Snapshot.** Add stable operation IDs and the media types for the 9 binary routes. Check in `contract/openapi.json` with a pytest that regenerates it and fails on an unreviewed difference. The snapshot describes paths and request DTOs only, which already exist.
- **Node tests.** Add the Node fetch-stub conformance helper and adopt it in the existing fetch-stubbing tests. Make pytest run all Node tests.
- **Consumers.** List `bardicctl` as a contract consumer and add a test that pins its `/api/jobs` expectations.

### Phase 1: views and response validation (3–5 days)

- Add the view base class, the extras walker and the conftest response-validation hook. The hook reports routes without a view and does not fail on them yet.
- Add views for `/api/status`, `/api/books/{id}` (and every edit route that returns it), `/api/jobs`, `/api/library`, `/api/voices`, `/api/books/{id}/analysis-pipeline`, and `/api/books/{id}/listen/chapter`. `/api/status` alone has 23 top-level keys with runtime-varying nested maps, so start them permissive.
- Add the minor/major differ and the `api` version block and header.
- Update API.md. This **reverses** its current statement that no checked-in OpenAPI snapshot is required: the snapshot becomes the reviewed contract, and the live document remains for debugging.

### Phase 2: client consolidation (3–5 days; can overlap Phase 1)

- Add `BardicApi` and move the 11 wrappers onto it file by file. Component render interfaces stay unchanged.
- Read Gemini voices, the default voice, model fallbacks and provider labels from `/api/status`, which already sends most of them. Move the status and kind vocabularies to one client module.
- Add the error envelope and `GET /api/jobs/{id}`, then move the six pollers.
- Fix the cover's caching, and route the client's cover URL construction through the server-provided `url`.

### Phase 3: full coverage and wire/storage separation (ongoing; piggybacks on feature work)

- Declare the remaining views. Make the conftest hook fail on routes that lack a view.
- Stop the UI reading internal fields. Make runtime types tolerant, then enforce `response_model` route by route.
- Unify the job and media shapes. Retire the aliases using the deprecation rule.
- Decide separately whether edit routes keep returning the whole book.

### Phase 4: split the repositories (1–2 days; deferred, gated)

- Use `git filter-repo` to extract a client repository with `bardic/static/` and the Node tests, keeping their history. Their Python shims are deleted from the server.
- The server owns and publishes `contract/`. The client pins a contract revision with a copy-and-update script, not a git submodule, and runs its tests against it. Each server PR that changes `contract/` carries the differ's minor or major label.
- The deployment stays same-origin. The owner's service points `BARDIC_UI_DIR` at a released client folder, and the version handshake catches any mismatch.

---

## How we know it is working

| Signal | Baseline (verified) | Target before a split |
|---|---|---|
| JSON routes used by the client with a declared view | 0 of 85 | all |
| Binary routes with a declared media type | 0 of 9 | all |
| Test responses that fail the view check (conftest hook) | not measured | 0, enforced |
| Test requests that match no contract operation (Node helper) | not measured | 0, enforced |
| Raw `fetch(` calls outside `BardicApi` | 13 | 0 |
| Node tests run by pytest | 13 of 22 | 22 of 22 |
| Snapshot diffs labelled *major* by the differ, over the last 20 merged PRs | not measured | a small minority (for example ≤ 2) |

**Split gate.** A split needs three things:
1. a stated driver, answering the first open question below;
2. the first six rows at target;
3. the differ showing that recent contract changes are mostly minor.

If most features still need synchronized breaking changes, a split costs more than it saves. Keep the single repository with its enforced boundary, which gives most of the benefit, and measure again later.

---

## Red-team view

- **The grumpy stickler.**
  - *"Ceremony for a single-owner local app."* The additions are generated files, one test hook and one differ. There is no runtime dependency, framework or build step. The value does not depend on a split: agents get a reviewable contract diff, and client tests stop passing against shapes the server no longer sends.
  - *"Declared-but-unenforced schemas are fiction."* They are enforced where it is safe: every real test response is validated at any depth. Runtime enforcement comes later because it converts drift into 500s.
  - *"Demo-book examples miss edge cases."* Agreed. Completeness comes from validating every response the existing suites already produce. Examples only feed the client tests.
- **The practical hacker.**
  - *"Just snapshot OpenAPI, validate responses in conftest, and assert requests in the Node fetch stub. Done."* That is Phases 0–1, and it is most of the value. Everything after that is a ratchet or waits for a reason.
  - *"Why not split now?"* The client's tests would be pinned to unchecked payloads. Each vertical feature would need two PRs and a version dance, with no tooling to catch mistakes.
  - *"Why not a TypeScript client with generated types?"* That is a reasonable client-repository decision after a split, and the snapshot makes it cheap. Adding a build step now contradicts [D01](DECISIONS.md) for no present gain.

---

## Risks and open questions

- **What is driving the split?** This is the biggest fork in the plan.
  - Possible drivers: a different UI stack, a native, mobile or remote client, other contributors, or isolating agents.
  - If a client will be served *off* the Bardic server's origin, same-origin fails. Authentication, CORS and a threat model become prerequisites.
  - Phases 0–3 still apply.
- **Enforced `response_model` filters and validates.** A stored value the view does not tolerate becomes a 500 for the whole route. The mitigation is permissive runtime types, the test-time strict walker, and per-route rollout.
- **`GET` routes that write** (the `projection.sync` in `analysis-pipeline`, `pipeline/api.py:272-273`) are documented as they are, not changed.
- **One large generated snapshot may cause frequent conflicts** across parallel worktrees. Regenerate to resolve. Split it per operation if conflicts become routine.
- **Is a blocking notice on a major-version mismatch acceptable** for the owner's other devices? Or should the server also keep the previous major's shape for a while?

## Out of scope

- Authentication, multiuser access and public hosting. These become necessary only if the client leaves the server's origin. (CORS is available as an opt-in server setting, contract 0.4.1; it grants access, it does not authenticate.)
- Rewriting the UI (a framework, TypeScript or a bundler), or splitting app.js into modules. These are client decisions and independent of the contract.
- Changing storage formats, the whole-book edit responses or job execution. The contract first describes current behavior.
- A `/v1` URL prefix, GraphQL, SSE and WebSockets.
