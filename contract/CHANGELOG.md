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
<!-- contract-sha256: 02819dfe3d912976bff46fc59ed6c6d9e159a6e2a5568be963cd4c8b87cd5de1 -->

**BREAKING.** The UI redesign and the removal of the Classic analysis engine, merged onto 0.2.0 as one release. Series runs now run the step pipeline and share its memory across volumes, the Classic engine's routes and schemas are removed, and several fields are added. Earlier drafts of this work were numbered 0.1.3 and 0.2.0–0.4.0 on a branch and never published; this entry replaces them. Every 0.2.0 convention applies to the new and changed operations: each error has a documented `code`, `detail` describes the condition, and GET routes record nothing. Clients must change the following.

### Classic analysis removed (BREAKING)

- **Removed operations.** These paths are no longer served, and their operation IDs are gone. As for any unknown path, a GET gets 404 and a POST gets 405 (`route_not_found`).
  - `startClassicAnalysis` (`POST /api/books/{book_id}/analyze`). Queue steps with `startBookAnalysisPipelineRun`; preview them with `planBookAnalysisPipelineRun`.
  - `previewClassicAnalysis` (`POST /api/books/{book_id}/analysis-plan`).
  - `getAnalysisPreprocessing` (`GET /api/books/{book_id}/preprocessing`). The census is the `census` step's accepted result; tracked usage is `usage` in `getPipelineInspector`.
  - `getAnalysisStatus` (`GET /api/books/{book_id}/analysis`). Follow runs through their `pipeline` job and `getBookAnalysisPipeline`.
- **Removed schemas.** `AnalysisRequest`, `AnalysisLimits`, `AnalysisStatus`, `AnalysisChapterProgress`, `AnalysisCoverage`, `AnalysisCensus`, `CensusChapter`, `CensusCharacter`, `ProfileFreshness`, `AnalysisPlan`, `AnalysisPlanBase`, `AnalysisPlanStageCounts`, `AnalysisPlanLimits` and `SeriesBookAnalysisPlan`. The `Classic analysis` tag is removed. `AnalysisUsage` stays.
- **`getPipelineInspector` stage cards** (`PipelineStage`) are rebuilt from the step pipeline.
  - The cards are `import`, `series`, one card per pipeline step in pipeline order (currently `structure`, `census`, `discovery`, `quotes`, `profiles`, `directing`), then `voices`, `narration`, `alignment` and `export`. `id` is now an open string instead of a fixed enumeration. A step card's counts are its accepted and total scopes, as in `getBookAnalysisPipeline`, and its dependencies are the step's declared inputs.
  - New always-sent fields: `stale_count` and `candidate_count`. They are integers on step cards and null on the others.
  - `status` gains `stale` and loses `provisional`, `failed`, `interrupted`, `cancelled` and `budget_limited`, which only a Classic checkpoint produced. An active `analyze` job no longer affects any card.
  - Like `getBookAnalysisPipeline`, the counts take outside changes into account without recording them. The operation no longer computes or caches the census.
- Retained historical data stays readable, and descriptions say which fields only the removed engine wrote: `analyze` jobs and their `phase`, `BookAnalysisSummary.phase` and `profiles_provisional`, and `BookCharacter.profile_state` and `profile_provisional`. `Status.analysis_provider` is the default provider for model steps without saved step settings.

### Series runs on the step pipeline (BREAKING)

The operation IDs and paths stay the same; the bodies change.

- **`planSeriesProcessing` request.** `SeriesProcessingRequest` is replaced by `SeriesPlanRequest`: `{steps, configs?, fresh?}`, with the same step IDs and `StepConfig` as the book pipeline. `provider`, `phase`, `concurrency`, `limits` and `expected_plan_fingerprint` now get 422. An unknown step ID in `steps` or `configs` is 400 `unknown_step`; an invalid or missing step configuration is 400 `step_config_invalid` or `step_model_missing`; a removed series is 409 `series_archived`.
- **`planSeriesProcessing` response** (`SeriesPlan`).
  - Removed: `provider`, `model`, `scan_model`, `phase`, `concurrency`, `limits_per_book` and `plan_fingerprint`.
  - Added: `plan_version` (3), `steps`, `configs` (resolved per step), `fresh`, `skipped_volumes`, the summed `cached_units`, `service_calls`, `estimated_input_tokens` and `output_token_allowance`, `known_cost_usd`, `unknown_cost_books`, `missing_inputs` (`{book: {step: [inputs]}}`), `missing_credentials` (`SeriesMissingCredential`: `{provider, label, needs}`), `context_pending_books`, `up_to` (`SeriesEstimate`) and `fingerprint`.
  - Each `books[]` entry (`SeriesPlanBook`) has `plan`, now the book pipeline's `PipelinePlan`, plus `fingerprint`, `consent_fingerprint`, `context_pending` (step IDs whose prompts read earlier books of the run), `context_sources` and `up_to` (`SeriesBookEstimate`: every context-pending unit counted as a request). An unknown price stays null, never zero.
  - `estimated_cost_usd` is null when any book's cost is unknown, and 0 for a series with no active books.
  - **Consent covers the unit set, not earlier-volume context.** `fingerprint` is built from each book's `consent_fingerprint`: its revision, `fresh`, and each step's version, provider, model and unit set, plus the exact requests of steps that are not context-pending.
- **`startSeriesProcessing` request** (`SeriesRunRequest`): the plan fields plus `expected_fingerprint`, `scheduling` (`serial` or `parallel`, as in the book pipeline's `RunRequest`), `gates`, `concurrency` (1–4, model requests in flight inside the running book) and optional `limits` (the pipeline's `Limits`, all uncapped by default). Unknown step IDs in `steps`, `configs` or `gates` are 400 `unknown_step`.
- **`startSeriesProcessing` errors.** 503 `shutting_down`; 409 `series_archived`; 400 `series_empty`; 400 `run_unconfirmed` (neither `expected_fingerprint` nor a limit); 409 `plan_stale`; 400 `step_inputs_missing`; 400 `api_key_missing` or `server_url_missing`; 409 `series_run_active`; 409 `job_active`. Nothing is queued when any check fails.
- **Execution.** Books run one at a time in reading order. Before each book starts, its `consent_fingerprint` is recomputed; a changed book fails with nothing sent and stops the series. Each book runs as one pipeline run with `series_run_id` set.
- **Series jobs** (`Job`).
  - Series children are `pipeline` jobs, not `analyze` jobs. A child carries `series_id`, `series_run_id`, `position`, `title`, `steps`, `consent_fingerprint`, `context_pending`, `context_sources` and `run_id` (null until its book starts). A child that never started ends with `not_started: true`.
  - The parent carries `steps`, `configs`, `gates`, `scheduling`, `concurrency` (1–4), `fresh`, `analysis_limits` (now `PipelineRunLimits`, every value possibly null), `estimated_cost_usd`, `requests` and `context_pending_books` instead of `phase`, `provider`, `model` and `scan_model`. `Job.scheduling` now also applies to `series` parents; `Job.analysis_limits` is `PipelineRunLimits` or, for runs recorded before this version, `SeriesJobLimits`. Neither job carries `plan_fingerprint`.
  - `listSeriesRuns` children are `SeriesRunChild`: a job plus `run: {id, status, outcomes, error}` once its book has started. A dangling child job ID is skipped.
  - Series jobs recorded before this version keep their old fields, which stay described as legacy.
- `PipelineRun.series_run_id` is present on runs started by a series run.

### Series memory (BREAKING)

- **A series run can pause.** When a book completes with a discovery, profiles or directing version waiting for review (a `review` gate) and a later book of the run reads it, the parent `series` job stays `running` with the new `waiting_for_review` (`SeriesReviewWait`: `book_id`, `child_job_id`, `title`, `position`, `steps`, `since`), and nothing runs until the owner resumes it or cancels it. A client that only waits for a terminal status must now offer resume or cancel. While paused, the waiting book accepts version decisions (`acceptAnalysisPipelineStepVersion` does not return `series_run_active` for it); every other reservation holds.
- New **`resumeSeriesProcessing`** (`POST /api/series/{series_id}/runs/{job_id}/resume`), returning the parent job. Errors: 404 `series_not_found` or `series_run_not_found`; 409 `series_run_not_waiting`, `series_run_not_resumable` (the server restarted since the pause) or `review_pending`; 503 `shutting_down`.
- **Series context reads accepted evidence** (`getBookSeriesContext`, and profile prompts). Entries are earlier volumes' current character references instead of retained observations, so accepting, rolling back or setting aside a version there changes them. `SeriesContextObservation` gains `step`, `version_id` and `origin`. Entry `id`s of pipeline evidence differ from any earlier observation ID. A row the removed Classic engine wrote counts only while the observation retained with it still exists.
- New **`listSeriesLinkSuggestions`** (`GET /api/books/{book_id}/series/suggestions`, `BookSeriesSuggestions`): proposed identity links for unlinked characters, by exact normalized name or alias match with linked characters of earlier volumes. Nothing is linked until confirmed with `linkSeriesCharacter`; namesakes are marked `ambiguous`. Read-only; 404 `book_not_found`.

### Character references from accepted evidence

- `listCharacterReferences` returns the projection of the accepted analysis-pipeline versions onto the current book: discovery and profile quotations (`profile_evidence`), attributed dialogue and cast-name mentions, in reading order. It is computed from the current book on every call, as the next pipeline write would record it, and records nothing, so manual edits and acceptance show at once. Rows the removed Classic engine wrote are carried until a discovery version is accepted. A book with no accepted evidence step lists its stored rows unchanged.
- `CharacterReference` gains optional provenance fields on projected rows: `step`, `version_id`, `origin` (`run`, `baseline`, `external`, `manual`, `book` or `cast_names`), `projection` and, for profile quotations, `anchors` (how many locations match). Rows the removed Classic engine wrote omit them.
- A speaker confirmed before contract 0.2.0 (edited, with confidence 1.0) counts as reviewed (`provider: "reviewed"`).

### Additive

- `Status` gains `tts_quota`: `{<tts_model>: ChapterListenQuota}` with `requests_today`, `rpd`, `resets_at` and `scope`, the same count chapter-listening jobs report. `requests_today` is 0 before any usage is recorded.
- `Status` gains `analysis_step_presets` (`StepPresetView[]`, empty when none), and `updateSettings` accepts `analysis_step_presets` (`StepPreset` / `StepPresetConfig`): a whole-list replacement of at most 50 owner-authored step settings. New 400 codes on `updateSettings`: `unknown_step`, `step_config_invalid` and `step_preset_invalid` (an empty name, a repeated `id`, or a repeated name for one step).
- The presented `Book` gains `language`: a BCP 47 tag from the EPUB's `dc:language`, or null.
- Each presented passage (`BookPassage`) gains `manual_fields`: the passage fields a person set by hand (`speaker_id`, `direction`, `cues`, `seed`), which analysis never replaces. The lock bookkeeping removed from the book document in 0.2.0 stays unpublished; this is the one public view of it.
- The `planBookAnalysisPipelineRun`, `startBookAnalysisPipelineRun` and `previewAnalysisPipelineStepVersion` descriptions now say that their sync also rebuilds the book's character references when their inputs changed.
- `StoryMapReference` (`getStoryMap`) gains the same optional provenance fields as `CharacterReference` (`step`, `version_id`, `origin`, `projection`, `anchors`). The story map lists the stored rows as the last pipeline write recorded them.

## 0.2.0 — 2026-09-28
<!-- contract-sha256: 07db8d051e0f51b00e8f34a123ca1a1824a55fe5cc3c3e3e4c5a08630076a1be -->

**BREAKING.** Resolves every known issue recorded with 0.1.0 ([issue #17](https://github.com/NicholasHazen/bardic/issues/17); decisions in [docs/API-KNOWN-ISSUES.md](../docs/API-KNOWN-ISSUES.md)). Regenerate clients and review each section below. Stored libraries need no migration: old stored shapes are read and presented in the new form.

### Errors and statuses (all operations)

- **BREAKING** Every JSON error body is `{"detail", "code"}`. `code` is a stable snake_case identifier. Each operation lists its codes per status (`x-bardic-error-codes`, and in each response description). The global codes are `validation_error` (422), `cross_origin_write` (403), `internal_error` (500) and `route_not_found` (404, 405). Clients branch on `code`, never on `detail`.
- **BREAKING** An unexpected server defect is a JSON 500 `internal_error`, not plain text. Every operation now documents it. A dangling reference inside stored data is a 500, no longer a 404.
- **BREAKING** `detail` sentences describe the condition and no longer name UI locations ("No Gemini API key is configured.", not "Add a Gemini API key in Settings first").
- **BREAKING** One meaning per status (see the contract introduction):
  - A conflict with current state is 409: `job_active`, `series_run_active`, `plan_stale`, `book_archived`, `series_archived`. This replaces 400 for archived books and series everywhere, for restoring a book with an active job, and for stale series plans and running series.
  - An unknown ID inside a request body is 400 with a specific code (`unknown_step`, `unknown_book`, `unknown_character`, `unknown_voice`, `unknown_passage`, `unknown_chapter`, `unknown_scene`, `unknown_series`, `unknown_series_character`, `unknown_version`), not 404.
  - A provider failure on voice deletion or a Gemini voice refresh is 502 `provider_error`, not 400.
  - Paid starts return 503 `shutting_down` when the server is stopping.
  - Out-of-range paging is clamped, not 400 (`listBookArtifacts`, pipeline step versions, `listDiagnostics`).
  - Archiving or restoring a book or series that is already in that state returns 200 and records nothing.

### Shapes: one form per concept

- **BREAKING** Audio objects. Every object that points at playable audio (`BookTake`, `ListeningPassageAudio`, `ListeningChunkClipAudio`, `PerformanceCastAudio`, `VoicePreviewAudio`, `LibraryVoiceAudition`, `VoiceDraftCandidateAudio`) has the same core: `url`, `asset_id`, `duration`, `provider`, `model`, `voice` and `created_at`, always present and nullable when unknown.
  - Removed from audio objects: `available`, `mode`, `cache_hit`, `performance_id`, `fingerprint`, `recipe`, `synthesis_key`, `source_anchor` and `resource_usage` (usage is served by the resources routes), and `VoicePreviewAudio.schema_version`. The `ListeningReuse` pointer keeps `schema_version`, `take_id`, `book_id`, `session_id` and `segment_id`, and drops `recipe` and `fingerprint`. The `AudioTakeUsage` schema is removed. A present audio object is playable. To detect a new take, compare `url` or `asset_id`. `cached` on the POST envelope reports a cache hit.
  - `LibraryVoiceVersion.audition_url` (string) became `audition` (an audio object, always present). `VoiceDraftCandidate.audio_url` became `audio` (an audio object, or null).
  - Removed schemas: `PerformancePassageAudio`, `PerformanceChunkClipAudio` (performance audio uses the listening schemas), `ListeningSourceAnchor` and `BookTakeVoiceLibrary` (use `AudioTakeVoiceLibrary`).
  - Unions of audio objects are `anyOf`, not `oneOf`, because their variants are open objects that can overlap.
- **BREAKING** Jobs.
  - `PipelineJobSummary` and `PerformanceJobSummary` are removed. The pipeline inspector's `jobs` and `Performance.job` are full `Job` objects, whose optional fields are absent rather than null when unset.
  - `Job.mode` on `pipeline` jobs became `scheduling` (serial or parallel); `mode` now means only simple or cast, on `performance` jobs.
  - `Job.limits` split into `analysis_limits` (`series`) and `speech_limits` (`listen_chapter`).
  - `Job.plan_fingerprint` is removed. Series plan responses keep their own `plan_fingerprint`.
  - Jobs stored by earlier versions are read with the new names, and their audio is presented through the new audio shape.
- **BREAKING** Aliases removed:
  - `RunRequest.mode` and `PipelineRun.mode` (renamed `scheduling`, matching `Job.scheduling`; runs stored earlier are presented with it);
  - `CharacterEdit.voice` and `CharacterEdit.system_voice` (send `voices: {gemini|system: {id}}`);
  - `SeriesBookAnalysisPlan.limits`, which was never present (use `SeriesPlan.limits_per_book`);
  - search `results` (use `items`);
  - library `passage_count` (use `segment_count`);
  - pipeline provider `has_api_key` (use `configured`);
  - `Status.analysis_models` (use `model_catalogs.gemini.models`);
  - `Status.has_api_key` (use `analysis_providers[id=gemini].has_api_key`) and `Status.analysis_model` (use `analysis_models_by_provider.gemini`);
  - the `updateSettings` request aliases `api_key` and `analysis_model`, which are now rejected with 422 (send `api_keys.gemini` and `analysis_models_by_provider.gemini`); the codes `gemini_key_conflict` and `gemini_model_conflict` are gone.
  - `AnalysisProviderStatus.available` and `has_api_key` both stay: they differ for `local`, which is available without a key.

### Bookkeeping removed from the wire

- **BREAKING** The `Book` document no longer has `edited` or `edited_fields` (characters, scenes, passages), `profile_input_key`, the legacy `voice`/`system_voice` (already folded into `voices`), or `metadata_edited`. The `BookMetadataEdits` schema is removed. For a character, `profile_state: "reviewed"` still shows a manual edit.
- **BREAKING** Also removed:
  - `Status.data_directory`;
  - `AnalysisStatus.fingerprint`;
  - `AnalysisCensus.fingerprint` and `source_hash` (also on the series plan's coverage);
  - `ResourceOperation.process_id`;
  - `SeriesContextObservation.source_hash`;
  - `PipelineStepRun.conflicts` (always empty).
- **BREAKING** The audiobook export's `production.json` is the presented `Book` (as `GET /api/books/{id}` returns it), not the raw stored book.
- **BREAKING** The analysis export (`exportBookAnalysis`) manifest is `schema_version: 2`: `analysis-attempts.json` holds the allowlisted `PipelineAttempt` shape with `validation_state`, not raw stored rows.

### GET routes write no records

- GET routes create or change no library records: no artifacts, analysis-pipeline decisions, resource measurements, jobs or books.
  - `getBookAnalysisPipeline` is read-only. It reports the state the next POST would record, and the first pipeline POST captures the baseline.
  - The preprocessing and pipeline inspector views write only the census cache, and search writes only its full-text index. Both are disposable caches.
  - Search, the audiobook export and the analysis export record no resource measurements.
  - Retaining legacy artifacts happens once per book at server start.
- Additive: `PipelineAttempt` gains `book_id`, `chapter_id`, `cached_input_tokens`, `cache_write_input_tokens`, `input_rate` and `output_rate` (USD per million tokens), `cost_basis`, `price_as_of`, `price_source` and `elapsed_seconds`, all optional. The analysis export includes them.
- Paging `offset` values are clamped to at most 2^53−1 instead of failing (`listBookArtifacts`, `getBookResourceUsage`, pipeline step versions).

### Library

- **BREAKING** Library lists are ordered by import time (`created_at`, newest first), not by the most recent save. Books without `created_at` come last.
- **BREAKING** `audio_count` counts only current, playable enhanced takes, in both `listBooks` and `getLibrary`.
- **BREAKING** `getBookCover`: the `ETag` is quoted, a matching `If-None-Match` returns 304 (documented), and `Cache-Control` is `private, max-age=31536000, immutable` at the `?v={sha256}` URL, `private, no-cache` otherwise.
- **BREAKING** `updateBookMetadata` locks only the fields that changed against refresh. An unchanged edit does not bump `revision`.
- `importBook`: an oversized upload is refused with 413 `upload_too_large` before its body is read, and a failed import leaves no original file or resource record.
- `refreshBookMetadata` and `repairBookStructure` record nothing for an unknown, archived or busy book.

### Books and edits

- **BREAKING** An edit that changes nothing (`editCharacter`, `editPassage`, `editScene`, `updatePronunciation`) returns the current book without saving and without bumping `revision`. A real edit locks only the fields it changed.
- **BREAKING** Voice choices follow one rule for every provider: a blank `id` clears the choice, so Default applies. A blank Breeze `id` no longer pins the server's default voice. A `seed` where it does not apply is 400 `seed_not_applicable`, instead of being ignored. `SegmentEdit.seed: null` clears the seed.
- `addCharacter` assigns a device voice the same way import does.
- **BREAKING** `updatePronunciation` takes `PronunciationPatch`: every field is optional and `id` is not accepted. Omitted fields keep their saved values, and `null` clears a field (400 `pronunciation_invalid` for `term` or `respelling`).
- `listCharacterReferences` is derived from the current book on every call, so manual edits and pipeline acceptance show at once.

### Series and analysis pipeline

- **BREAKING** Series routes that change a removed series return 409 `series_archived`, not 404. Reads (map, runs, characters) work on a removed series. `restoreSeries` and `createSeriesCharacter` return 409 `series_run_active` during a run.
- **BREAKING** Pipeline:
  - plan and run refuse an LLM step without a configured model (400 `step_model_missing`);
  - a saved step choice that no longer validates reports `saved: false` with the new `saved_invalid: true`;
  - reject returns 404 for an unknown book and 409 for an archived one, and can decline a `same_as_accepted` candidate; rejecting an accepted version is 409 `version_accepted`;
  - an accept with a stale `expected_revision` is 409 `plan_stale`.
- **BREAKING** An unknown step ID in `steps`, `configs` or `gates` is 400 `unknown_step` on plan and run. Known steps that were not requested are still ignored. A plan for an unknown book is 404 before its configs are validated.
- **BREAKING** `previewClassicAnalysis` on an archived book is 409 `book_archived`, like the other previews. `createSeries` refuses control characters in a name (400 `text_invalid`), like rename.
- Classic analysis, series runs, structure repair and manual edits record the projection they are about to replace as a pipeline `baseline` or `external` version first, so it can be restored from the pipeline history even if the Analysis tab was never opened.
- The run-start response is a snapshot, never the object the worker is changing.

### Listening, narration and voices

- **BREAKING** `startChapterListening` returns 429 `daily_quota_reached` with `Retry-After` when this library has already used today's request limit, instead of accepting the request and ending the job `quota_limited`.
- **BREAKING** `startEnhancedRender` returns 503 `shutting_down` while the server stops, instead of an undocumented 500.
- **BREAKING** `getVoiceLibrary` always returns 200 and reports a failed Gemini listing as `providers.gemini.state: "error"`; the plain-text 500 is gone.
- **BREAKING** `deleteLibraryVoice` records each provider deletion. After a partial failure (502), a retry resumes, and `server_deleted` lists what was removed.
- **BREAKING** `saveVoiceDraft` and `cloneBreezeVoice` validate everything before uploading a server voice. If the library record still fails, the upload is removed. New codes: `voice_name_invalid`, `base_voice_deleted`, `candidate_audio_missing`, `recording_too_large` (413).
- Single-voice responses report checked `server_state` values, from the saved provider checks.
- **BREAKING** `PerformancePlan.problems` is a list of `{code, detail}` objects (`PerformanceProblem`), not sentences, so clients can offer a fix for each code. `updatePerformance` on an archived book is 409 `book_archived`.
- `refreshBreeze` imports server voice names longer than the library's 100-character limit by shortening them, instead of failing with an undocumented 400. A Breeze health or voice-list body that is not a JSON object is reported as `state: "error"`, not a 500.
- `listenToPassage` skips an equivalent retained take whose copy into this book fails its integrity check, and generates the passage instead of failing.
- `cancelJob` and `listSeriesRuns` ignore a missing child job ID. A dangling listening session or chapter reference in stored data is a 500 `internal_error`, not a 404.
- Job errors, voice warnings and Breeze and Gemini error details describe the condition and no longer name UI locations.

### System, settings, jobs and diagnostics

- **BREAKING** A terminal job's `status`, `message`, `error` and `resume_after` never change. A job cancelled while queued stays `cancelled`.
- **BREAKING** `updateSettings`:
  - `tts_limits` values are a typed `TtsLimitsUpdate` that merges per field (bad values are 422);
  - saving settings no longer lifts daily quota blocks unless that model's limits or the key change;
  - a `BREEZE_TTS_URL` from the environment is never saved, and `Status.breeze_url` is the URL in use.
- **BREAKING** `recordDiagnostic`: a `segment_id`, `session_id` or `job_id` without `book_id` is 422, instead of `recorded: false`. `listDiagnostics` clamps `limit` to 1–5000, and a bad `limit` gets the standard 422 list.

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
