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

## 0.2.0 — 2026-09-28
<!-- contract-sha256: eb2e550d41bf9131b89142d29954ddafb90183fddd866e21b4343e9cb473460e -->

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
  - Removed from audio objects: `available`, `mode`, `cache_hit`, `performance_id`, `fingerprint`, `recipe`, `synthesis_key`, `source_anchor`, and `BookTake.resource_usage`. A present audio object is playable. To detect a new take, compare `url` or `asset_id`. `cached` on the POST envelope reports a cache hit.
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
  - `Status.analysis_models` (use `model_catalogs.gemini.models`).

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
