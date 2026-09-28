# Contract changelog

Changes to the published HTTP contract (`openapi.json`), newest first. The workflow is in [docs/API-WORKFLOW.md](../docs/API-WORKFLOW.md).

## Versioning rules

`info.version` is the version of the contract, not of the server. **Every change to `openapi.json` gets a new version and an entry here, even a documentation-only change.** That way one version always means exactly one document. Under each version heading, the generator records the SHA-256 of the `openapi.json` it describes. Tests fail if the contract changes after its version was recorded.

While the version is 0.x:

- **Additive changes bump the patch** (0.1.0 → 0.1.1): a new operation, a new optional request field or parameter, a new response field, a new documented error status, a new enum value, or improved documentation.
- **Breaking changes bump the minor** (0.1.x → 0.2.0), and the entry is marked **BREAKING** with what clients must change. Breaking means any of:
  - removing or renaming an operation, operation ID, field, parameter or schema;
  - adding a required request field or parameter, or tightening validation;
  - changing a type or the meaning of a value;
  - a response field that becomes optional or nullable;
  - removing an enum value;
  - changing a success status, media type or documented header;
  - removing a documented error status.

From 1.0, which comes with the first dedicated client release, additive changes bump the minor and breaking changes bump the major.

The generator records the version but does not classify the change: the author and the reviewer do. If two branches claim the same version, the changelog conflicts. Resolve it by giving the later change the next version: update `VERSION`, delete that entry's `contract-sha256` line, and regenerate.

## 0.3.0 — 2026-09-28
<!-- contract-sha256: 93c8c9b31a4ba7d9ed00ac473db4ba6bb33240c890b2e48e9999013a0e9a8e1e -->

**BREAKING.** Series memory on the step pipeline: later volumes read earlier volumes' accepted evidence, series runs use staged consent, and a review gate can pause a series run. Clients must change the following.

- **A series run can pause.** When a book completes with a discovery, profiles or directing version waiting for review (a `review` gate) and a later book of the run reads it, the parent `series` job stays `running` with the new `waiting_for_review` (`SeriesReviewWait`: `book_id`, `child_job_id`, `title`, `position`, `steps`, `since`) and nothing runs until the owner resumes it with the new `resumeSeriesProcessing` (`POST /api/series/{series_id}/runs/{job_id}/resume`) or cancels it. A client that only waits for a terminal status must now offer resume or cancel. Before, the run always continued past a waiting book. While paused, the waiting book accepts version decisions (`acceptAnalysisPipelineStepVersion` no longer gets 409 for it; its 409 description says so); every other reservation still holds.
- **Series context reads accepted evidence** (`getBookSeriesContext`, and profile prompts). Entries are earlier volumes' current character references (their accepted evidence) instead of retained observations, so accepting, rolling back or setting aside a version there changes them. `SeriesContextObservation` gains `step`, `version_id` and `origin`. Entry `id`s of pipeline evidence differ from any earlier observation ID.
- **Consent covers the unit set, not earlier-volume context.** `SeriesPlan.fingerprint` is now built from each book's new `consent_fingerprint` (its revision, `fresh`, and each step's version, provider, model and unit set, plus the exact requests of steps that are not context-pending). Before each book starts the worker compares `consent_fingerprint`, so earlier books' accepted results no longer stop the run at the next linked book. `plan_version` is 3; fingerprints from earlier previews are refused (409).

Additive in the same version:

- `SeriesPlanBook` gains `consent_fingerprint`, `context_pending` (step IDs whose prompts read earlier books of the run), `context_sources` and `up_to` (`SeriesBookEstimate`: every context-pending unit counted as a request, tokens from the current context). `SeriesPlan` gains `context_pending_books` and `up_to` (`SeriesEstimate`). An unknown price stays null.
- Series child jobs gain `consent_fingerprint`, `context_pending` and `context_sources`; the parent gains `context_pending_books`.
- New `listSeriesLinkSuggestions` (`GET /api/books/{book_id}/series/suggestions`, `BookSeriesSuggestions`): proposed identity links for unlinked characters by exact normalized name or alias match with linked characters of earlier volumes. Nothing is linked until confirmed with `linkSeriesCharacter`; namesakes are marked `ambiguous`.

## 0.2.3 — 2026-09-28
<!-- contract-sha256: dab500d3243131d0bc6e4ababa688e65e98950905e3298dc7fda4530163883bb -->

Book language metadata (UI redesign phase 4). Additive.

- The presented `Book` (from `getBook`, `importBook`, `createDemoBook` and every other operation returning the full book) gains `language`: a BCP 47 tag from the EPUB's `dc:language`, or null. The browser uses it to filter Mac voices to the book's language.

## 0.2.2 — 2026-09-28
<!-- contract-sha256: 060b4323a8fda3dfa6232f5789ceac37771cb19a3115473485656930e727e17c -->

Saved step settings for the Analyze tab (UI redesign phase 3a). Additive.

- `updateSettings` accepts `analysis_step_presets` (`StepPreset` / `StepPresetConfig`): a whole-list replacement of at most 50 owner-authored step settings. New 400 cases: unknown step, provider or model the step does not take, duplicate `id`, duplicate name for one step.
- `Status` (from `getStatus` and `updateSettings`) gains `analysis_step_presets`: `StepPresetView[]`, empty when none.

## 0.2.1 — 2026-09-28
<!-- contract-sha256: 170713696e79319554b6440547a4ec864703d4ecdef0bb454427df4a90281e1c -->

Character references can come from accepted step-pipeline evidence (Classic removal, stage 2). Additive.

- `CharacterReference` (`listCharacterReferences`) gains optional provenance fields on rows projected from accepted pipeline versions: `step`, `version_id`, `origin`, `projection` and, for profile quotations, `anchors`. Rows written by the older phase engine omit them.

## 0.2.0 — 2026-09-28
<!-- contract-sha256: 964700e2e8614b5c6f838ba9f9e066c80268a0617cd3708b12963c477ae9e958 -->

**BREAKING.** Series runs now run the step pipeline instead of the classic phase engine. The owner replaced the engine, so the old shapes cannot be kept alongside the new ones. The operation IDs and paths stay the same. Clients must change the following.

- **`planSeriesProcessing` request** (`POST /api/series/{series_id}/plan`). `SeriesProcessingRequest` is replaced by `SeriesPlanRequest`: `{steps, configs?, fresh?}`, using the same step IDs and `StepConfig` as the book pipeline. `provider`, `phase`, `concurrency`, `limits` and `expected_plan_fingerprint` now get 422. An unknown step ID gets 400 here, where the book pipeline returns 404.
- **`planSeriesProcessing` response** (`SeriesPlan`).
  - Removed: `provider`, `model`, `scan_model`, `phase`, `concurrency`, `limits_per_book` and `plan_fingerprint`.
  - Added: `plan_version`, `steps`, `configs` (resolved per step), `fresh`, `skipped_volumes`, the summed `cached_units`, `service_calls`, `estimated_input_tokens` and `output_token_allowance`, `known_cost_usd`, `unknown_cost_books`, `missing_inputs` (`{book: {step: [inputs]}}`), `missing_credentials` (`[{provider, label, needs}]`, the new `SeriesMissingCredential`) and `fingerprint`.
  - Each `books[]` entry (`SeriesPlanBook`) gains `fingerprint`. Its `plan` is now the book pipeline's `PipelinePlan` instead of the classic `SeriesBookAnalysisPlan`, which is removed.
  - `estimated_cost_usd` is still null when any book's cost is unknown, and 0 for a series with no active books.
- **`startSeriesProcessing` request** (`POST /api/series/{series_id}/process`). `SeriesRunRequest`: the plan fields plus `expected_fingerprint` (renamed from `expected_plan_fingerprint`), `mode`, `gates`, `concurrency` (now 1–4, requests in flight inside the running book) and optional `limits` (the pipeline's `Limits`, all uncapped by default).
- **`startSeriesProcessing` errors.**
  - A changed fingerprint now returns **409** instead of 400, and so does an active run of the same series.
  - New 400s: neither `expected_fingerprint` nor any limit sent, a book lacking a required input, and a missing API key or server URL (`Add in Settings first: …`).
  - Books still run one at a time in reading order. Each book's plan is checked again before it starts, and a changed book fails with nothing sent and stops the series.
- **Series jobs** (`Job`, `SeriesRun`).
  - Series children are `pipeline` jobs, not `analyze` jobs. A child carries `series_id`, `series_run_id`, `position`, `title`, `steps`, `plan_fingerprint` and `run_id` (null until its book starts). A child that never started ends with `not_started: true`.
  - The parent carries `steps`, `configs`, `gates`, `mode`, `concurrency` (1–4), `fresh`, `limits` (now `PipelineRunLimits`, every value possibly null), `estimated_cost_usd` and `requests` instead of `phase`, `provider`, `model` and `scan_model`.
  - `plan_fingerprint` is no longer marked internal.
  - `listSeriesRuns` children are the new `SeriesRunChild`: a job plus `run: {id, status, outcomes, error}` once its book has started.
  - Series jobs recorded before this version keep their old fields, which stay described as legacy. `SeriesJobLimits` remains for those records.

Additive and documentation changes in the same version:

- `PipelineRun.series_run_id` is present on runs started by a series run. It is returned by `getBookAnalysisPipeline`, which previously failed contract validation after a series run.
- `PipelineStepConfigView` and `PipelineRunLimits` are unchanged but are now shared by jobs. The descriptions of `cancelJob`, `AnalysisStatus`, `startClassicAnalysis` and `AnalysisLimits` no longer say that series runs use classic `analyze` jobs.

## 0.1.3 — 2026-09-28
<!-- contract-sha256: 540a52e82a8f8041918ec5db29114c679637e3abba5d0bad3415deb9c60bc9c1 -->

Adds the daily Gemini speech count to the status response (UI redesign phase 1). Additive.

- `GET /api/status` gains `tts_quota`: `{<tts_model>: ChapterListenQuota}` with `requests_today`, `rpd`, `resets_at` and `scope`, the same count chapter-listening jobs report. The browser uses it to warn before a Studio recording would exceed today's remaining requests.

## 0.1.2 — 2026-09-28
<!-- contract-sha256: 3be45c18b27d3dd84ab1dc0eef9f2cd9b0f7c1da48b0e3f2def9dbd0fed52778 -->

Describes the pronunciations feature (merged from `main`). All changes are additive.

- New **Pronunciations** operations: `listPronunciations`, `addPronunciation`, `updatePronunciation` and `deletePronunciation` under `/api/books/{book_id}/pronunciations`, with the `PronunciationEntry` request and the `BookPronunciation`, `PronunciationWithUsage`, `PronunciationList` and `PronunciationSaved` responses.
- `Book.pronunciations`, which is absent when a book has none.
- `Performance.pronunciation_count`, for cast performances that pinned entries. Cast plans can add a note when the pinned entries differ from the book's.
- Voice previews: an optional `pronunciation` in the request to audition an unsaved respelling, plus `VoicePreview.spoken_text` and `VoicePreview.pronunciation`.

## 0.1.1 — 2026-09-28
<!-- contract-sha256: 23e40e3b213a5dd56e97155088f9ac415d52217160dbf4d3755396cd037a3b5c -->

- Response schemas no longer carry `default` keywords. The server never applies defaults to responses, and generators such as openapi-typescript read a default as "always present". Whether a response field is always present is stated only by `required`, which is unchanged.
- The compatibility rules in `info.description` now explain request defaults and the matching generator setting (openapi-typescript `defaultNonNullable: false`).
- Verified with a strict TypeScript compile of openapi-typescript 7.13 output (`npm run contract:codegen`).

## 0.1.0 — 2026-09-28
<!-- contract-sha256: 44474856678f54acf0938bfa9e4920a910c503b4f95af786e94710e49202fdb3 -->

First published contract. It describes the existing API without changing server behavior.

- All 96 operations have an operation ID, a summary and description, and a response schema or media type. Each also lists its documented error statuses and a cost class (`x-bardic-cost`).
- Every schema, field and parameter is described. Storage bookkeeping that reaches the wire is marked `x-bardic-internal`.
- Behaviors that clients may want fixed are listed in [docs/API-KNOWN-ISSUES.md](../docs/API-KNOWN-ISSUES.md).
