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

## 0.5.1 — 2026-09-29
<!-- contract-sha256: 96fd8f49f72684e7002f2df3c2978842904e3246550761fe1ef76c7acd3c8954 -->

Additive: a saved performance pins a fallback narrator (platform and voice) that reads any passage the main narration cannot, every such passage is noted, chosen sections can be re-recorded with another voice, and a detailed status reports per-chapter progress with an estimate.

- New saved setting `fallback_narrator` (`Status.fallback_narrator`: `{provider, voice}` or null; `SettingsRequest.fallback_narrator`, new request schema `FallbackNarratorUpdate`, `{provider: null}` clears it). New performances pin it. Codes on `updateSettings` (400): `fallback_unsupported`, `narrator_voice_invalid`.
- `PerformanceRequest.fallback` (new schema `FallbackChoice`) and an optional body on `preparePerformance` (`PerformancePrepare.fallback`) choose or change a performance's fallback narrator. New `Performance.fallback` and `PerformancePlan.fallback` (`PerformanceFallback`: `provider`, `voice`, `automatic`, `label`, `available`; null when none is usable). `createPerformance`, `previewPerformance` and `preparePerformance` gain the 400 code `narrator_voice_invalid` (an unknown provider is a 422 there).
- Behavior: the fallback narrator now reads a passage that cannot be narrated for either reason, in simple and in cast performances: Gemini blocked its text (`content_blocked`), or it kept failing after the bounded retries (`failed`: a cast or device or Breeze passage error, or a Gemini chunk that failed or was truncated past its bound). The take is linked to the performance and marked `substitute`. A daily quota, repeated rate limits, cancellation and shutdown still pause or stop the job and never swap a voice. A cast performance passage Gemini blocks no longer fails the job when a fallback narrator is usable (it still does, with `error_code: content_blocked`, when none is). Six passages in a row that fail for the main narrator stop the job with an error (the narrator looks unavailable, not the text); what the fallback already read is kept. A passage the fallback cannot read either stays unrecorded and is reported in the status `run.issues`.
- `JobChapterChunk.uncertain` (new, optional): true on a `failed` chunk whose request may have been processed and billed (a timeout or dropped connection). A saved performance requests a chunk that failed with an error response once more (reserved and paced like any request) before its fallback narrator reads the chunk's passages, and never resends an uncertain one. The fallback covers the failed chunk's passages, so its size is the granularity.
- `ListeningSubstitute.reason` gains `failed` and `rerecord`; new optional `ListeningSubstitute.override_id`. `PerformanceProgress` and `PerformanceChapterProgress` gain `passages_rerecorded`; `PerformanceProgress` gains `fallback_reasons`; `passages_fallback` now counts every automatic fallback, not only blocked text; `fallback_provider` may also be `gemini`. `JobFallbackNarrator.provider` may also be `gemini`; a `performance` job now carries `fallback` (also for a re-record).
- New operation `getPerformanceStatus` (`GET .../performances/{performance_id}/status`): per-chapter passages ready, read by a fallback narrator, re-recorded, blocked and remaining, sizes, chapter state and `eta_seconds`; the run (`PerformanceRun`, with `issues`); the whole-performance `eta` (`PerformanceEta`, with `basis` `measured`, `estimated`, `unknown` or `none`, and a quota pause reported as `paused_reason` and `resumes_at`); and `notes` for every passage another narrator reads. Local read.
- New operations `previewPerformanceRerecord` (`POST .../rerecord/preview`) and `rerecordPerformance` (`POST .../rerecord`, `may_charge`): read chosen passages (`passage_ids`, a `from_passage_id`..`to_passage_id` range, a `chapter_id`, or all, optionally `only: fallback`) with a chosen provider and voice. Earlier audio is kept. Request schema `PerformanceRerecordRequest`; response schema `PerformanceRerecordPlan`. Codes (400): `unknown_passage`, `unknown_chapter`, `range_incomplete`, `range_invalid`, `nothing_to_rerecord`, `scope_too_large`, `model_unsupported`, `narrator_voice_invalid`, `narrator_unavailable` and the provider problem codes; `book_archived`, `job_active`, `series_run_active` (409); `shutting_down` (503).
- New operations `listPerformanceTakes` (`GET .../takes`, response `PerformanceTakes` of `PerformanceTake`) and `restorePerformanceTake` (`POST .../takes/restore`, request `PerformanceRestoreRequest`): list the retained takes for a performance's passages and choose which one plays, or return to the performance's own audio. Nothing is deleted or rewritten. Refused with 409 `job_active` while a job is active. Codes: `unknown_passage`, `restore_ambiguous`, `take_stale`, `take_not_playable`, `take_missing`, `no_original` (returning to own audio a passage never had) (400); `take_not_found` (404); `book_archived`, `job_active`, `series_run_active` (409).

## 0.5.0 — 2026-09-29
<!-- contract-sha256: f696b3297b7b928c03a45e4830de77b21c2a71d2f865d19df17c46d325adf65f -->

**BREAKING.** One deliberate reshaping of the contract before any generated client, Rust server or external user exists, so that strongly typed generated code (Rust with typify/progenitor, TypeScript with openapi-typescript) is good and stays good. The browser UI is updated in the same change. Changes are grouped by kind; each says what clients must change. Response schemas stay open (ignore unknown fields), enumerations stay open sets (see "Compatibility rules for clients"), operation IDs do not change.

### Tagged unions (additive, except where marked)

Every union of objects is now a **named schema** that is a `oneOf` with a `discriminator` (property name and a one-to-one `mapping`; `Job` and `SeriesRunChild` write their members inline, so their discriminator has the property name only and the tags are the inline enums). Each member is its own schema with a **required single-value string `enum` tag** and its own tag value, so generators emit a real tagged union (TypeScript narrows on the tag; Rust decodes the tag, never guesses from field presence). A nullable union is `anyOf: [{$ref: Union}, {type: null}]`. There is no catch-all member and no nested `anyOf`; the union is a closed set that changes only in a new contract version.

| Union | Tag | Members (tag value: schema) | Used by |
| --- | --- | --- | --- |
| `ListeningAudio` | `kind` | `passage`: `ListeningPassageAudio`, `clip`: `ListeningChunkClipAudio` | `ListeningTake.audio`, `ListenCached.audio` |
| `PerformanceAudio` | `kind` | `passage`, `clip` and `cast`: `PerformanceCastAudio` | `PerformanceAudioMap.audio` values |
| `ListenResult` | `kind` | `cached`: `ListenCached`, `queued`: `ListenQueued` | `listenToPassage` |
| `VoicePreviewResult` | `kind` | `cached`: `VoicePreviewCached`, `queued`: `VoicePreviewQueued` | `startVoicePreview` |
| `SeriesCharacterLinkResult` | `kind` | `linked`: `SeriesCharacterLinkState`, `unlinked`: `SeriesCharacterUnlinked` | `linkSeriesCharacter` |
| `SeriesVolume` | `kind` | `supplied`: `SeriesSuppliedVolume`, `placeholder`: `SeriesVolumeSlot` | `Series.volumes`, `SeriesPlan.volumes`, `SeriesPlan.skipped_volumes` |
| `PipelineVersionRow` | `step` | `structure`, `census`, `discovery`, `quotes`, `profiles`, `directing`: the six row schemas | `PipelineVersionDetail.rows` |
| `StoryMapNode` | `type` | `book`, `chapter`, `scene`, `passage`, `character`: `StoryMapBookNode`, `StoryMapChapterNode`, `StoryMapSceneNode`, `StoryMapPassageNode`, `StoryMapCharacterNode` | `StoryMap.nodes` |
| `StoryMapEdge` | `type` | `contains`, `next`, `attributed_speaker`: `StoryMapContainsEdge`, `StoryMapNextEdge`, `StoryMapSpeakerEdge` | `StoryMap.edges` |

- **New always-sent tag fields**: `kind` on `ListeningPassageAudio` (`passage`), `ListeningChunkClipAudio` (`clip`), `VoicePreviewAudio` (`preview`) and `PerformanceCastAudio` (`cast`); `kind` on `ListenCached`/`VoicePreviewCached` (`cached`), `ListenQueued`/`VoicePreviewQueued` (`queued`), `SeriesCharacterLinkState` (`linked`, also inside `BookSeries.links`) and `SeriesCharacterUnlinked` (`unlinked`), `SeriesSuppliedVolume` (`supplied`) and `SeriesVolumeSlot` (`placeholder`); `step` on every pipeline result row (equal to the version's `step_id`). (The booleans `ListenCached.cached` and the like are removed below, under "Names and types": the tag replaces them.)
- **BREAKING: the volume discriminator is `kind`, not `status`.** `status` (`available`, `archived`, `missing`, `planned`) stays a plain field: a discriminator over four values and two schemas cannot be generated. Select on `kind`.
- **BREAKING: `SeriesVolumeSlot.book_id` is removed.** It was always null (a placeholder has no book), and a field typed `null` breaks generators. Clients: use `kind === "placeholder"` where `book_id === null` was tested.
- **`StoryMapNode` and `StoryMapEdge`** were one object each with optional fields that depended on `type`; they are unions now, and each member's fields are required. The wire JSON is unchanged.
- The audio, listen, preview and link unions used to be told apart by field presence or a boolean; none of that changes on the wire except the added tag.

### Nulls and requiredness

- **Fields that are always sent are required, nullable where they can be null.** The server now sends every one of these fields in every response, with `null` (or an empty list) where it used to omit the key. Clients can treat them as present; a generated client no longer merges "absent" and "null" for them.
  - `Book`: `structure_version`, `cover`, `pronunciations` (an empty list, not absent, when there are none), and for a book stored without them `created_at` and `analysis` (now nullable, as in `LibraryBookSummary`), `author` and `source_name` (empty strings) and `revision` (0); `BookChapter`: `kind`, `title_source`, `source_href`, `logical_sections`, `narrative_order` (null on a book imported before structure metadata); `BookScene`: `title`, `summary`, `tone`, `direction`; `BookAnalysisSummary`: `model`, `phase`, `profiles_provisional`; `BookCharacter`: `evidence`, `former_names` (empty lists), `profile_refined`, `profile_provider`, `profile_model`, `profile_priority`, `profile_state`, `profile_provisional`; `BookSpeakerCheck`: `speaker_id`, `speaker`, `tag_conflict`; `BookPronunciation` and `PronunciationWithUsage`: `providers`, `character_id`, `note`.
  - `CharacterReference`: `confidence`, `provider`, `model`, `step`, `version_id`, `origin`, `projection`.
  - `ResourceOperation`: `chapter_id`, `provider`, `model`, `completed_at`, `input_tokens`, `output_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_bytes`, `elapsed_seconds`, `cpu_seconds`, `audio_seconds`, `price_as_of`, `price_source`, `usage_source`, `cpu_scope`, `artifact_id`, `asset_id`, `http_status`, `validation_state`.
  - `PipelineAttempt` (in `getPipelineInspector`): all 22 optional fields. The analysis export bundle's `analysis-attempts.json` is unchanged (it still omits an attempt's fields that were never stored), so its `schema_version` stays 2.
  - `PipelineEvent`: `artifact_id`, `attempt_id`, `error`, `cached_unit_key`, `repair`; `PipelineRun`: `started_at`, `completed_at`, `outcomes`, `series_run_id`; `PipelineRunOutcome`: `reason`, `step_run_id`, `scopes`, `accepted`, `error`; `PipelineStepRun.incomplete_scopes`; `PipelinePlanStep.note`; `PipelineVersionDiff.agreement`; `PipelineProvider.models`.
  - `AccountCheckUsage`: `input_tokens`, `output_tokens`, `total_tokens`; `AnalysisCatalogModel`: `price_valid_until`, `price_input_token_limit`; `AnalysisModelCatalog.partial`; `StatusNarrationAvailability.reason`; `NarrationProviderCapabilities`: `custom_voice_ids`, `speakers_per_take`; `NarrationProviderInfo.voices`; `DiagnosticRecordResult`: `id`, `reason`.
  - `LibraryVoiceRecipe`: `sample_text`, `model`, `language_code`, `gender`; `VoiceDraftSaved`: `book`, `assignment_error`, `cleanup_error`; `BreezeVoiceCloned`: `book`, `assignment_error`; `Performance`: `pronunciation_count`, `session_id`, `cast`.
- **Left optional on purpose** (absent means "not applicable" or "not recorded", and sending null would cost more than it says): the per-passage extras `BookPassage.{evidence, seed, speaker_check, analysis_provider, analysis_model}` (one null each on every passage of a multi-megabyte document), provider-specific audio extras (`reuse`, `provider_timing`, `breeze`, `voice_revision`, `voice_library`), `BookCharacterVoice` (its three forms), `BookBreezeSettings` (a partial override), `ListeningSession.{voice_revision, seed, settings}`, `JobChapterChunk` (fields that depend on `status`), `DiagnosticEvent` (a sparse log record), `StoryMapReference` (the analysis export carries stored rows unchanged and its `story-map.json` equals `getStoryMap`), `CharacterReference.{profile_description, profile_direction, anchors}` (only `profile_evidence` rows), `VoicePreview.{spoken_text, pronunciation}` (a retained request), `Job` fields (stage 3 of this version).
- **BREAKING: `AccountCheck.balance` is removed.** It was always null, and a field typed `null` breaks generators; `balance_note` still explains that no balance is available.

### Numbers

- Every integer has a `format`: `int32` where its bounds keep it within 32 bits (for example `limit`, `concurrency`), `int64` for every counter, size, offset and seed (for example `payload_bytes`, `start`, `seed`, `input_tokens`). Generators that guess a width from an unformatted integer (openapi-generator picks 32 bits) now get the right one. Values do not change.

### Errors

- **BREAKING: a 416 on a range-capable file is a JSON `Error`** with code `range_not_satisfiable` (and `Content-Range: bytes */<size>`), no longer an empty body. It is declared in each operation's responses with the `Error` schema, so generated clients see one error type. Clients: read the `Error` body of a 416 (or ignore it, as before); the status and header are unchanged.

### Names and types (BREAKING)

- **"Passage" is the only name on the wire; "segment" is gone.** The server keeps `segment` internally and translates: responses are renamed on the way out, request fields accept only the new names (the old ones get 422). Clients must rename:
  - **Paths** (operation IDs do not change): `PATCH /api/books/{book_id}/segments/{segment_id}` is `PATCH /api/books/{book_id}/passages/{passage_id}` (`editPassage`; its body schema `SegmentEdit` is `PassageEdit`); `GET /api/audio/{book_id}/{segment_id}` is `GET /api/audio/{book_id}/{passage_id}` (`getPassageAudio`).
  - **Request fields**: `segment_id` is `passage_id` in `ListenRequest`, `ChapterListenRequest`, `RenderRequest`, `VoicePreviewRequest` and `DiagnosticRequest` (whose `dependentRequired` follows); `segment_direction` is `passage_direction` in `VoicePreviewRequest`. The error code `segment_direction_requires_passage` is `passage_direction_incomplete`.
  - **Response fields**: `Book.segments` is `Book.passages`; `BookScene.segment_ids` is `passage_ids`; `segment_id` is `passage_id` on `Job` (also `SeriesRun`, `SeriesRunChild`), `ListeningTake`, `ListeningPassageAudio`, `ListeningChunkClipAudio`, `ListeningReuse`, `VoicePreview`, `VoicePreviewSourceAnchor`, `CharacterReference`, `StoryMapReference`, `SeriesContextObservation` and `DiagnosticEvent`; `first_segment_id`, `last_segment_id` and `segment_count` are `first_passage_id`, `last_passage_id` and `passage_count` on `ChapterListenChunkPlan` and `JobChapterChunk`; `LibraryBookSummary.segment_count` is `passage_count`; `Job.scope_start_segment_id` and `focus_segment_id` are `scope_start_passage_id` and `focus_passage_id`.
  - `AudioTakeSentenceTiming.segments` (Breeze sentence timing) is `sentences`, and the `start` and `end` of its items (seconds) are `start_seconds` and `end_seconds`, so that the seconds of a sentence cannot be mistaken for the code-point offsets `start` and `end` of every other schema.
  - `Status.timing_kind` is removed (the same fact is `narration_providers[provider].capabilities.timing`, already `passage`). The audiobook export ZIP keeps its own file names and stored names (`takes/{segment_id}.wav`, `timeline.json`), except `production.json`, which is documented as the book exactly as `getBook` presents it and so now has `passages` (and `voice_revision`). The analysis export bundle is unchanged: its `book.json`, `references.json` and `story-map.json` `references` keep the stored names, and its `schema_version` stays 2.
- **One name for one type.** The same key no longer means different types in different schemas where that could confuse a client:
  - Breeze voice `revision` (a string hash) is `voice_revision`: `BookCharacterVoice`, `LibraryVoiceVersion`, `VoiceLibraryBreezeServerVoice`. `Book.revision`, `expected_revision` and the other integer revisions keep their names.
  - `PipelineUnitCounts.cached` (a count) is `cached_units`, like `PipelinePlanStep.cached_units`; `ListenCached`, `ListenQueued`, `VoicePreviewCached` and `VoicePreviewQueued` lose the boolean `cached` (their `kind` is the tag), so `cached` is a boolean only where a response says "served from a cache".
  - `PipelineDiscoveryRow.evidence` and `PipelineProfilesRow.evidence` (counts) are `evidence_count`, apart from the `evidence` lists of `BookCharacter` and `BookPassage`; `PipelineCensusRow.chapters` is `chapter_count`; `PipelineQuotesRow.current` is `current_speaker`; `PipelinePlanStep.units` and `scopes` (counts) are `unit_count` and `scope_count`; `PipelineRunOutcome.scopes` is `scope_count`; `AnalysisUsage.attempts` is `attempt_count`; `LibraryVoiceDeleted.deleted` is `voice_id`.
  - `SeriesCharacterUnlinked` loses `linked: false` (its `kind` is `unlinked`).
  - Not unified, on purpose: keys that share a short name across unrelated schemas without confusion inside any one schema (for example `usage`, `notes`, `progress`, `version`, `settings`, `steps`, `operations`, `projection`).
- **Result-table rows carry real lists and stable identifiers, not display strings.** `PipelineDiscoveryRow.aliases`, `PipelineDirectingRow.cues` and `edited`, and `PipelineProfilesRow.edited` are arrays (they were comma-separated strings). `PipelineQuotesRow.kind` is `quotation` or `character` (was `Quotation`, `Character`); `PipelineQuotesRow.check` and `PipelineDirectingRow.check` are identifiers (`agrees`, `differs`, `suggests`, `not_in_cast`, `narrator`, `no_quote`, and `in_cast` on a `character` row; `null` on a directing row that was not checked) instead of labels (`Agrees`, `Differs · BookNLP: Mira`), and `PipelineDirectingRow.check_speaker` holds BookNLP's speaker name. Show labels of your own. The row diff fields `_diff`, `_changed` and `_previous` are `diff_state`, `changed_keys` and `previous`, always sent (null unless compared). The `columns[].key` values follow the row keys.
- **Providers are declared once per domain.** New named enumerations replace 17 inline enumerations and about 25 plain strings: `NarrationProvider` (`system`, `gemini`, `breeze`), `AnalysisProvider` (`local`, `gemini`, `openai`, `anthropic`, `local_llm`, `booknlp`, `novel_analyzer`), `CloudProvider` (`gemini`, `openai`, `anthropic`; the providers that take a key, also the keys of the per-provider maps) and `VoiceLibraryProvider` (`breeze`, `gemini`). They type every audio object's `provider`, `BookTake`, `Performance*`, `ListeningSession`, `VoicePreview`, `DiagnosticEvent`, the step, run and plan `provider` fields, `PipelineAttempt`, `BookAnalysisSummary`, `BookPassage.analysis_provider`, `BookCharacter.profile_provider`, the provider status entries and the voice library. Fields that genuinely carry values of several domains stay strings and say why: `ResourceOperation.provider`, `ArtifactSummary.provider` and `ArtifactDetail.provider` (analysis or narration), `CharacterReference.provider`, `StoryMapReference.provider` and `SeriesContextObservation.provider` (`reviewed` marks a dialogue attribution a person set). `Job.provider` was a mixed enumeration and is typed per kind by the `Job` union below. Request fields keep their validation and error codes (`analysis_provider_unknown` and the others), so request `provider` strings that were strings stay strings.
- **Duplicated shapes are merged.** `PipelineProviderModel` is `AnalysisCatalogModel` (its `source_url` is nullable, and the self-hosted model's price fields are null); `LibraryVoiceAudition` and `VoiceDraftCandidateAudio` are one `VoiceAudition`; `StoryMapLogicalSection` is `BookLogicalSection`. Kept separate: `PipelineAttempt` and `ResourceOperation` (different fields and a wider row), `StoryMapReference` and `CharacterReference` (the analysis export's `story-map.json` carries stored rows unchanged), the audio objects' repeated Breeze extras (an `allOf` base would generate worse types), request and response pairs (`Limits` and `PipelineRunLimits`, `StepConfig` and `PipelineStepConfigView`).

### The Job is a tagged union (BREAKING)

`Job` was one object with 52 optional fields whose presence depended on `kind`, repeated in `SeriesRun` and `SeriesRunChild`. It is now a **named `oneOf` discriminated by `kind`** with one branch per job kind, each written inline with every field that kind declares (so a generator emits a tagged enum with a real type per kind). A field that does not mean anything for a kind is not declared, and not sent, for it. The wire objects have the same shape as before for each kind; what changes is which fields exist on which kind, and that the fields a kind always has are now required.

| `kind` | Fields of its own (all kinds also have `id`, `book_id`, `kind`, `status`, `progress`, `total`, `message`, `error`, `created_at`, `updated_at`, `cancel_requested`, and `resume_after` when `quota_limited`) |
| --- | --- |
| `render` | none |
| `analyze` (historical) | optional: `provider`, `model`, `scan_model`, `phase`, `chapter_id`, and on a child recorded before 0.3.0 `series_id`, `series_run_id`, `position` |
| `pipeline` | required: `run_id` (null until the run exists), `steps`; optional: `scheduling`, and on a series child `series_id`, `series_run_id`, `position`, `title`, `consent_fingerprint`, `context_pending`, `context_sources`, `not_started`, `finished_at` |
| `series` | required: `series_id`, `book_ids`, `child_job_ids`, `waiting_for_review` (null unless paused); optional: `steps`, `configs`, `gates`, `scheduling`, `concurrency`, `fresh`, `analysis_limits`, `estimated_cost_usd`, `requests`, `context_pending_books`, `finished_at`, and on a run recorded before 0.3.0 `phase`, `provider`, `model`, `scan_model` |
| `listen` | required: `session_id`, `passage_id`, `provider`, `model`, `audio` (null until ready) |
| `listen_chapter` | required: `session_id`, `chapter_id`, `provider`, `model`, `voice`, `intent`, `scope_start_passage_id`, `focus_passage_id`, `chunking`, `speech_limits`, `ramp_restart`, `joins`, `chunks`, `calibration`, `parent_id`, `projection`, `quota`, `waiting_seconds`, `closing` (the last five null until they apply) |
| `voice_preview` | required: `preview_id`, `preview`, `passage_id` (null for demo text), `provider`, `model`, `audio` (null until ready) |
| `performance` | required: `performance_id`, `mode`, `provider`, `model`, `child_job_ids`, `child_job_id` |

- `Job.audio` is typed per kind: `ListeningAudio` on `listen`, `VoicePreviewAudio` on `voice_preview`. `JobAudio` is removed. `provider` is a `NarrationProvider` on the narration kinds and an `AnalysisProvider` on the analysis kinds.
- **BREAKING: the narration kinds lose `phase`.** `listen`, `listen_chapter`, `voice_preview` and `performance` jobs carried a fixed label (`simple_listen`, `chapter_listen`, `voice_preview`, `performance`) that only repeated `kind`, and the label's enumeration collided across the union branches in generated code. `phase` remains only on the historical `analyze` and pre-0.3.0 `series` jobs (`scan`, `profiles`, `direct`, `full`). Clients: use `kind`.
- **Always sent now, null until they apply** (the server used to leave the key out): `audio` on `listen` and `voice_preview`; `parent_id`, `projection`, `quota`, `waiting_seconds` and `closing` on `listen_chapter`; `child_job_id` and `child_job_ids` on `performance`; `run_id` and `steps` on `pipeline`; `series_id` (derived from `book_id` `series:<id>` when an old record lacks it), `book_ids`, `child_job_ids` and `waiting_for_review` on `series`.
- **`SeriesRun`** is the `series` branch plus `children`. **`SeriesRunChild`** is a union on `kind` of a `pipeline` child (the `pipeline` fields with the series fields required, plus `run`) and, for runs recorded before 0.3.0, an `analyze` job. `SeriesRun`, `SeriesRunChild` and `Job` write their branches inline.
- **Operations that always return one kind name it**: `ListenQueued.job` and `PerformanceStarted.job`, `Performance.job`, `PipelineRunStarted.job`, `ChapterListenStarted.job` and `VoicePreviewQueued.job` are `ListenJob`, `PerformanceJob`, `PipelineJob`, `ListenChapterJob` and `VoicePreviewJob` (each with the fields of its `Job` branch), `startEnhancedRender` returns `RenderJob`, and `startSeriesProcessing` and `resumeSeriesProcessing` return `SeriesJob`. `listJobs`, `getJob`, `cancelJob` and the pipeline inspector's `jobs` return the `Job` union.
- The union is closed: a new job kind is a new branch in a new contract version. A client that only follows a job reads the fields every kind has (`status`, `progress`, `total`, `message`, `error`) and can treat an unknown kind like a job of unknown work.
- Clients: select on `kind`. A field you read from a `Job` of a kind that does not declare it (for example `session_id` on a `pipeline` job) is now absent from the type and from the response. `Job.kind` is the closed tag, not an open enumeration.

### Documentation

- **The advice on unknown enum values was wrong for Rust generators and is corrected** ("Compatibility rules for clients"): write clients to tolerate unknown values; generators differ (openapi-typescript emits closed literal unions, `enumUnknownDefaultCase` exists only for some openapi-generator generators and not for its Rust one, typify and progenitor emit closed enums); a Rust client needs a post-processing step that adds an unrecognized variant to every response enum. The contract keeps response enumerations as enums. Tagged unions are closed and described as such.
- `INFO` now describes the tagged-union shape and the rule that a nullable union is `anyOf` with `null`.

### Merge reconciliation: 0.3.2 and 0.3.3 landed on main while 0.4.0 to 0.5.0 were built

Contract 0.3.2 (extend a performance with more chapters) and 0.3.3 (text Gemini blocks) were merged on `main` while this overhaul was being built on 0.3.1. Both are additive and part of this version: `previewPerformanceResume`, `addPerformanceChapters`, `PerformanceChapters`, `PerformanceChaptersAdded`, `Job.error_code`, `JobFallbackNarrator`, `JobContentBlocked`, `JobChapterChunk.status` `blocked` with `split` and `split_into`, `ListeningSubstitute`, and the `passages_fallback`, `passages_blocked`, `blocked_passage_ids` and `fallback_provider` fields of the performance progress. Their entries below (0.3.3, then 0.3.2) are kept as written on `main`. Nothing outside this repository had consumed them, so where they broke a 0.5.0 convention the merge brought them into line. Read the 0.3.2 and 0.3.3 entries together with these differences:

- **Passage naming.** Every 0.3.2 and 0.3.3 field uses the wire names of this version: `ListeningPassageAudio.passage_id` (so a `substitute` take reports its passage as `passage_id`), `JobChapterChunk.first_passage_id`, `last_passage_id` and `passage_count`, `Book.passages`. The new operations' bodies and paths are unchanged (`chapter_ids` only).
- **The new `Job` fields sit on the right branch of the `Job` union.** `error_code` is declared on every `Job` branch, beside `resume_after`: optional, present only on a job that ended `failed` for a documented cause. `fallback` and `content_blocked` exist only on the `listen_chapter` branch (`ListenChapterJob`).
- **`ListenChapterJob.fallback` and `content_blocked` are always sent** (0.3.3 described them as optional): `fallback` is `null` when no fallback narrator was available and on a job queued before contract 0.3.3, and `content_blocked` is `null` until Gemini blocks text of the chapter. Clients: test for `null`, not for absence.
- **`Performance.chapters_added` is always sent** (0.3.2 described it as absent when nothing was added): an empty list until `addPerformanceChapters` adds chapters. **`PerformancePlan.added_chapter_ids` is always sent**: `null` in the plan `previewPerformance` returns, a list (empty when nothing is new) from `previewPerformanceResume`.
- **Providers are named.** `JobFallbackNarrator.provider` and `PerformanceProgress.fallback_provider` are `NarrationProvider` (the value is `system` or `breeze`, never `gemini`) instead of an inline `system`/`breeze` enumeration, and `ListeningSubstitute.for_provider` is a `NarrationProvider` instead of a plain string.
- **Left optional on purpose, like the other per-passage extras:** `ListeningPassageAudio.substitute` (absent on a take Gemini made), `JobChapterChunk.split` and `split_into` (depend on the chunk's status).
- **Operation IDs and paths of the two new operations are final**: `previewPerformanceResume` (`POST /api/books/{book_id}/performances/{performance_id}/preview`) and `addPerformanceChapters` (`POST /api/books/{book_id}/performances/{performance_id}/chapters`); `addPerformanceChapters` returns `PerformanceStarted`, whose `job` is a `PerformanceJob`.

## 0.4.1 — 2026-09-28
<!-- contract-sha256: 9ac0e208caa33bf2e966eb1532163d8ab477f8de734c34a0685e4315cb01051e -->

Additive: opt-in CORS, described in the conventions. No operation, field, parameter, status or error code changes, and a server with the new setting unset behaves exactly as 0.4.0 does.

- **New server setting `BARDIC_CORS_ORIGINS`** (a server-operator setting, not visible to clients and not reported by `getStatus`). Unset or empty, which is the default: no CORS header is sent, a preflight `OPTIONS` gets the 405 `route_not_found` it always did, and a cross-origin browser write is refused with 403 `cross_origin_write`. Set to `*` or to a comma-separated list of exact origins, it turns on the behavior below for `/api/` requests. The conventions replace "There is no CORS support" with "CORS is opt-in".
  - `Access-Control-Allow-Origin` is `*` for `*`. For a list it is the request's `Origin` when listed (with `Vary: Origin`) and absent otherwise. `Access-Control-Expose-Headers` names `Bardic-Contract-Version`, `ETag`, `Content-Range`, `Content-Length`, `Accept-Ranges`, `Content-Disposition` and `Content-Type`.
  - An `OPTIONS` request to any `/api/` path from an allowed origin is answered with 204, no body, `Access-Control-Allow-Methods: GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS`, `Access-Control-Allow-Headers: Content-Type, Range, If-None-Match, If-Match, Authorization` and `Access-Control-Max-Age: 600`. One from an origin that is not allowed keeps getting 405.
  - The write guard lets a write through when its `Origin` is allowed and refuses every other cross-origin write as before. `*` allows every `Origin`, `null` included; a list allows `null` only when it contains the entry `null`.
  - `Access-Control-Allow-Credentials` is never sent. The trusted `Host` check is unchanged, so a request or preflight with an untrusted `Host` is still refused with 400 `Invalid host header`. Every response, a preflight included, keeps `Cache-Control: no-store` and `Bardic-Contract-Version`.
  - There is still no authentication. `*` lets any web page in a browser that can reach the server read and change the library and start paid work.
- The `cross_origin_write` global code now says the origin is one the CORS setting does not allow. Clients: nothing to change. A browser client on another origin needs the server's operator to allow its origin; a CORS failure in the browser means the server has not.

## 0.4.0 — 2026-09-28
<!-- contract-sha256: e0d1d37aaf60e20a08a1e2a6b69c67f57bb4e1669644339f52985d434779ef22 -->

**BREAKING** (three small shape changes, marked below). The contract is settled before a replacement server and generated clients are written against it ([docs/RUST-SERVER-PLAN.md](../docs/RUST-SERVER-PLAN.md), phase 0): a version handshake, a single-job route, response fields that are always sent now required, and three cleanups a strongly typed generated client would otherwise inherit. Everything else is additive. Response schemas stay open: clients still ignore unknown fields.

### Version handshake and single job (additive)

- `Status.contract` (`ContractInfo`) on `getStatus` and `updateSettings`: `version` (the contract's `info.version`) and `sha256` (the SHA-256 of the exact bytes of `contract/openapi.json`, the value recorded under that version below). A server implementation embeds the two values of the document it was built against.
- Every `/api/` response now carries the header `Bardic-Contract-Version` (the same version), including errors, the 403 write-guard rejection, the plain-text 400 invalid host, 404 `route_not_found` and 500 `internal_error`. It is documented in the conventions (Transport and security, and "Version handshake"), not as a per-response OpenAPI `headers` entry. The 403 write-guard rejection and a 500 from an exception that escapes the handlers now also carry `Cache-Control: no-store`, `X-Content-Type-Options` and `Referrer-Policy` like every other `/api/` response, as the conventions already said for `Cache-Control`.
- New `getJob` (`GET /api/jobs/{job_id}`): the same `Job` that `listJobs` returns, read-only. 404 `job_not_found` when no job has the ID (the code `cancelJob` already used). `listJobs` and the conventions now point at it for following one job.

### Fields now required (additive for clients that ignore unknown fields)

Each was declared optional and is always sent by the server (the value may be null where stated). Clients can treat them as present.

- `BookAnalysisSummary.notes` (string, may be empty; no longer nullable).
- `LibraryVoiceRecipe.description` (string, may be empty; no longer nullable). The presenter now sends `""` for a version that recorded no description, which no route creates.
- `AudioTakeBreezeInfo.request_id` (string or null; never absent).
- `ResourceOperation`: `run_id`, `stage`, `unit_key`, `created_at`, `request_count`, `estimated_cost_usd` and `cost_basis` (each nullable as before).
- `PipelineAttempt`: `book_id` and `run_id` (nullable as before). `status` stays optional: it is set on every attempt the server records, but the presenter does not backfill it on a stored record that lacks it.

The remaining optional response fields are absent in some legitimate case (a kind-specific job field, a legacy stored record, a provider-specific value); the descriptions say which.

### BREAKING

- **`Job.analysis_limits` is `PipelineRunLimits` or null; `SeriesJobLimits` is removed** (also on `SeriesRun` and `SeriesRunChild`). Reason: the two shapes have the same four field names and cannot be told apart by any tag, so a generated union was ambiguous, and every `SeriesJobLimits` value is a valid `PipelineRunLimits`. Clients: read `analysis_limits` as `PipelineRunLimits`. A series run recorded before contract 0.3.0 has the same fields with `max_requests`, `max_input_tokens` and `max_output_tokens` set (its per-book allowance).
- **`ResourceAggregate` token and byte totals are integers, not numbers**: `input_tokens`, `output_tokens`, `cached_input_tokens`, `cache_write_input_tokens` and `output_bytes` on `ResourceSummary.totals`, each `ResourceStageAggregate` and each `ResourceRunAggregate`. Reason: they are sums of integer counts and were never fractional, so a generated client should not use a floating-point type. The server now sums only integer counts (a non-integer count is unknown, like a missing one). Seconds and USD stay numbers.
- **`ListeningSession.settings` is `BookBreezeSettings`** (`temperature`, `cfg_scale`, `top_p`, `top_k`, each optional) instead of an open object with provider-defined keys. Reason: the server has only ever accepted and stored those four keys. It is absent unless a Breeze voice pinned settings, which no current API sets. `BookBreezeSettings` (also used by `BookCharacterVoice.settings`) is unchanged.

## 0.3.3 — 2026-09-29
<!-- contract-sha256: f24c55e562d710342a317f527eef7eda5a8c1ca4d59723fafc42d867b8e2c986 -->

Additive: text Gemini blocks under its content policy is recognised, retained, split once, and read by a fallback narrator, instead of failing a chapter or performance with a generic "check the model, voice and passage length" error.

- New optional `Job.error_code` (`content_blocked`): set on a `failed` job whose cause is Gemini's content policy refusing text (HTTP 400, error code `content_blocked`) where no fallback path exists (a cast performance passage, a single-passage `listen` job). `error` is a fixed sentence; the provider's error text is never kept. Clients can add a hint keyed on the code.
- `Job` of kind `listen_chapter`: new optional `fallback` (`JobFallbackNarrator`: `session_id`, `provider` `system`/`breeze`, `model`, `voice`; the free local narrator snapshotted at queue time, or null) and `content_blocked` (`JobContentBlocked`: `fallback`, `fallback_passage_ids`, `blocked_passage_ids`, `fallback_error`; present once Gemini blocked text of the chapter). A chapter with blocked text still ends `completed`; its `message` says how many passages a fallback narrator read or were left unrecorded.
- `JobChapterChunk.status` gains `blocked`; new optional `split` (a half of a blocked chunk) and `split_into`. A blocked chunk of two or more passages is split once into halves; a blocked half or one-passage chunk is not split again, and its passages go to the fallback narrator. At most three Gemini requests per blocked chunk (the original and two halves), each reserved against the per-minute limiter and the daily count. Blocks are retained per session, exact text and recipe, so no later job resends them.
- New optional `ListeningPassageAudio.substitute` (`ListeningSubstitute`: `reason` `content_blocked`, `for_provider`, `for_model`): marks a take of the fallback narrator standing in for a blocked passage. It is returned by `listListeningTakes`, `POST /listen` cache hits and the performance audio map like any passage take; for such a take `session_id` is the fallback narrator's session.
- `PerformanceProgress` gains `passages_fallback`, `passages_blocked` and `fallback_provider`; `PerformanceChapterProgress` gains `passages_fallback`, `passages_blocked` and `blocked_passage_ids`. `passages_ready` includes passages a fallback narrator read (they play). A blocked passage with no audio is counted in `passages_blocked`, not as a failure. `PerformancePlan` `notes` mention blocked passages, and blocked passages without an available fallback narrator are excluded from `passages_to_generate` and `requests_estimate`.
- Descriptions of `startChapterListening`, `startListening`, `listListeningTakes` and the performance job say how blocks are handled.

## 0.3.2 — 2026-09-29
<!-- contract-sha256: 5adb59cbd70e985aea8dbe5e49e4d23a7ff722b246b31a267da142b2356dc40c -->

Additive: a performance can be extended with more chapters without recreating it.

- New operation `previewPerformanceResume` (`POST /api/books/{book_id}/performances/{performance_id}/preview`): the local plan (readiness, passages to record, request estimate, `problems`, `notes`) for recording the rest of an existing performance, optionally with more chapters. Stores nothing and sends nothing; allowed while a job runs.
- New operation `addPerformanceChapters` (`POST /api/books/{book_id}/performances/{performance_id}/chapters`, `may_charge`): adds chapters to the performance and starts recording what is missing, reusing retained audio and keeping the pinned narrator session or cast snapshot. Refused with 409 `job_active` while a job is active for the book. Codes: `unknown_chapter`, `no_chapters_selected`, the provider problem codes and `narrator_voice_missing` (400); `book_archived`, `job_active`, `series_run_active` (409); `shutting_down` (503).
- New request schema `PerformanceChapters` (`chapter_ids`).
- New response schema `PerformanceChaptersAdded`; new optional `Performance.chapters_added` (history of chapters added after creation, absent when none) and optional `PerformancePlan.added_chapter_ids` (only from `previewPerformanceResume`).
- `Performance.chapter_ids` now includes chapters added later; `updated_at` also changes when chapters are added. `preparePerformance` is unchanged and still records only what the selection lacks.

## 0.3.1 — 2026-09-28
<!-- contract-sha256: f73d1d60f6928bb4abde88034d9a50acfa90dc7124bce5e182fae80664ca41be -->

Documentation only: descriptions updated for the Classic data drop (stage 4 of the Classic removal). No field, parameter, status or error code changes.

- `repairBookStructure` no longer mentions a saved analysis checkpoint: none exists any more, so none is transformed, and `structure_mismatch` means only that the re-parsed source does not match the saved chapters.
- `ArtifactSummary.kind` lists `analysis_checkpoint`, a saved checkpoint of the removed Classic engine retained when its table was dropped. The kinds remain an open set.
- `LibraryBookStorage.database_payload_bytes` no longer counts analysis checkpoints (the table is gone).
- In the `exportBookAnalysis` bundle, `observations.json` is empty for books analyzed by the removed Classic engine: their observations are `character_observation` artifacts in the same bundle.
- `SeriesContextObservation`: a reference the removed Classic engine wrote counts while its observation is retained as a `character_observation` artifact, which is the same check as before on the same content hash.

## 0.3.0 — 2026-09-28
<!-- contract-sha256: c28bd7cd1cef4f73c647f5e94526f74310e9bbc5b69ac47541e8c3b16c4a4448 -->

**BREAKING.** The UI redesign and the removal of the Classic analysis engine, merged onto 0.2.0 as one release. Series runs now run the step pipeline and share its memory across volumes, the Classic engine's routes and schemas are removed, and several fields are added. Earlier drafts of this work were numbered 0.1.3 and 0.2.0–0.4.0 on a branch and never published; this entry replaces them. Every 0.2.0 convention applies to the new and changed operations: each error has a documented `code`, `detail` describes the condition, and GET routes record nothing. Clients must change the following.

### Classic analysis removed (BREAKING)

- **Removed operations.** These paths are no longer served, and their operation IDs are gone. As for any unknown path, a GET gets 404 and a POST gets 405 (`route_not_found`).
  - `startClassicAnalysis` (`POST /api/books/{book_id}/analyze`). Queue steps with `startBookAnalysisPipelineRun`; preview them with `planBookAnalysisPipelineRun`.
  - `previewClassicAnalysis` (`POST /api/books/{book_id}/analysis-plan`).
  - `getAnalysisPreprocessing` (`GET /api/books/{book_id}/preprocessing`). The census is the `census` step's accepted result; tracked usage is `usage` in `getPipelineInspector`.
  - `getAnalysisStatus` (`GET /api/books/{book_id}/analysis`). Follow runs through their `pipeline` job and `getBookAnalysisPipeline`.
- **Removed schemas.** `AnalysisRequest`, `AnalysisLimits`, `AnalysisStatus`, `AnalysisChapterProgress`, `AnalysisCoverage`, `AnalysisCensus`, `CensusChapter`, `CensusCharacter`, `ProfileFreshness`, `AnalysisPlan`, `AnalysisPlanStageCounts`, `AnalysisPlanLimits`, `SeriesBookAnalysisPlan` and `SeriesProcessingRequest`. The `Classic analysis` tag is removed. `AnalysisUsage` stays.
- **`getPipelineInspector` stage cards** (`PipelineStage`) are rebuilt from the step pipeline.
  - The cards are `import`, `series`, one card per pipeline step in pipeline order (currently `structure`, `census`, `discovery`, `quotes`, `profiles`, `directing`), then `voices`, `narration`, `alignment` and `export`. `id` is now an open string instead of a fixed enumeration. A step card's counts are its accepted and total scopes, as in `getBookAnalysisPipeline`, and its dependencies are the step's declared inputs.
  - New always-sent fields: `stale_count` and `candidate_count`. They are integers on step cards and null on the others.
  - `status` gains `stale` and loses `provisional`, `failed`, `interrupted`, `cancelled` and `budget_limited`, which only a Classic checkpoint produced. An active `analyze` job no longer affects any card.
  - Like `getBookAnalysisPipeline`, the counts take outside changes into account without recording them. The operation no longer computes or caches the census.
- Retained historical data stays readable, and descriptions say which fields only the removed engine wrote: `analyze` jobs and their `phase`, `BookAnalysisSummary.phase` and `profiles_provisional`, and `BookCharacter.profile_state` and `profile_provisional`. `Status.analysis_provider` is the default provider for model steps without saved step settings.

### Series runs on the step pipeline (BREAKING)

The operation IDs and paths stay the same; the bodies change.

- **`planSeriesProcessing` request.** `SeriesProcessingRequest` is replaced by `SeriesPlanRequest`: `{steps, configs?, fresh?}`, with the same step IDs and `StepConfig` as the book pipeline. `provider`, `phase`, `concurrency`, `limits` and `expected_plan_fingerprint` now get 422, so 400 `provider_not_cloud` is gone from both series routes. An unknown step ID in `steps` or `configs` is 400 `unknown_step`; an invalid or missing step configuration is 400 `step_config_invalid` or `step_model_missing`; a removed series is 409 `series_archived`.
- **`planSeriesProcessing` response** (`SeriesPlan`).
  - Removed: `provider`, `model`, `scan_model`, `phase`, `concurrency`, `limits_per_book` and `plan_fingerprint`.
  - Added: `plan_version` (3), `steps`, `configs` (resolved per step), `fresh`, `skipped_volumes`, the summed `cached_units`, `service_calls`, `estimated_input_tokens` and `output_token_allowance`, `known_cost_usd`, `unknown_cost_books`, `missing_inputs` (`{book: {step: [inputs]}}`), `missing_credentials` (`SeriesMissingCredential`: `{provider, label, needs}`), `context_pending_books`, `up_to` (`SeriesEstimate`) and `fingerprint`.
  - Each `books[]` entry (`SeriesPlanBook`) has `plan`, now the book pipeline's `PipelinePlan`, plus `fingerprint`, `consent_fingerprint`, `context_pending` (step IDs whose prompts read earlier books of the run), `context_sources` and `up_to` (`SeriesBookEstimate`: every context-pending unit counted as a request). An unknown price stays null, never zero.
  - `estimated_cost_usd` is null when any book's cost is unknown, and 0 for a series with no active books.
  - **Consent covers the unit set, not earlier-volume context.** `fingerprint` is built from each book's `consent_fingerprint`: its revision, `fresh`, and each step's version, provider, model and unit set, plus the exact requests of steps that are not context-pending.
- **`startSeriesProcessing` request** (`SeriesRunRequest`): the plan fields plus `expected_fingerprint`, `scheduling` (`serial` or `parallel`, as in the book pipeline's `RunRequest`), `gates`, `concurrency` (1–4, model requests in flight inside the running book) and optional `limits` (the pipeline's `Limits`, all uncapped by default). Unknown step IDs in `steps`, `configs` or `gates` are 400 `unknown_step`.
- **`startSeriesProcessing` errors.** 503 `shutting_down`; 409 `series_archived`; 400 `unknown_step`, `step_config_invalid` or `step_model_missing`; 400 `series_empty`; 400 `run_unconfirmed` (neither `expected_fingerprint` nor a limit); 409 `plan_stale`; 400 `step_inputs_missing`; 400 `api_key_missing` or `server_url_missing`; 409 `series_run_active`; 409 `job_active`. Nothing is queued when any check fails.
- **Execution.** Books run one at a time in reading order. Before each book starts, its `consent_fingerprint` is recomputed; a changed book fails with nothing sent and stops the series. Each book runs as one pipeline run with `series_run_id` set.
- **Series jobs** (`Job`).
  - Series children are `pipeline` jobs, not `analyze` jobs. A child carries `series_id`, `series_run_id`, `position`, `title`, `steps`, `consent_fingerprint`, `context_pending`, `context_sources` and `run_id` (null until its book starts). A child that never started ends with `not_started: true` (also when it is cancelled on its own while queued or interrupted by a restart while queued). `consent_fingerprint` is null on a child recorded before the fingerprint existed.
  - The parent carries `steps`, `configs`, `gates`, `scheduling`, `concurrency` (1–4), `fresh`, `analysis_limits` (now `PipelineRunLimits`, every value possibly null), `estimated_cost_usd`, `requests` and `context_pending_books` instead of `phase`, `provider`, `model` and `scan_model`. `Job.scheduling` now also applies to `series` parents; `Job.analysis_limits` is `PipelineRunLimits` or, for runs recorded before this version, `SeriesJobLimits`. Neither job carries `plan_fingerprint`.
  - `listSeriesRuns` children are `SeriesRunChild`: a job plus `run` (`SeriesChildRun`: `{id, status, outcomes, error}`) once its book has started. A dangling child job ID is skipped.
  - Series jobs recorded before this version keep their old fields, which stay described as legacy.
- `PipelineRun.series_run_id` is present on runs started by a series run.
- Jobs interrupted by a server restart describe the condition ("The server restarted before this analysis finished. …") instead of naming a removed action.

### Series memory (BREAKING)

- **A series run can pause.** When a book completes with a discovery, profiles or directing version waiting for review (a `review` gate) and a later book of the run reads it, the parent `series` job stays `running` with the new `waiting_for_review` (`SeriesReviewWait`: `book_id`, `child_job_id`, `title`, `position`, `steps`, `since`), and nothing runs until the owner resumes it or cancels it. A client that only waits for a terminal status must now offer resume or cancel. While paused, the waiting book accepts version decisions (`acceptAnalysisPipelineStepVersion` does not return `series_run_active` for it); every other reservation holds.
- New **`resumeSeriesProcessing`** (`POST /api/series/{series_id}/runs/{job_id}/resume`), returning the parent job. Errors: 404 `series_not_found` or `series_run_not_found`; 409 `series_run_not_waiting`, `series_run_not_resumable` (marked paused but its worker state is gone) or `review_pending`; 503 `shutting_down`. A server restart ends a paused run `interrupted` and clears its `waiting_for_review`.
- **Series context reads accepted evidence** (`getBookSeriesContext`, and profile prompts). Entries are earlier volumes' current character references instead of retained observations, so accepting, rolling back or setting aside a version there changes them. `SeriesContextObservation` gains the always-sent (nullable) fields `step`, `version_id` and `origin`. Entry `id`s of pipeline evidence differ from any earlier observation ID. A row the removed Classic engine wrote counts only while the observation retained with it still exists.
- New **`listSeriesLinkSuggestions`** (`GET /api/books/{book_id}/series/suggestions`, `BookSeriesSuggestions` with `SeriesLinkSuggestion`, `SeriesLinkCandidate` and `SeriesLinkSource`): proposed identity links for unlinked characters, by exact normalized name or alias match with linked characters of earlier volumes. Nothing is linked until confirmed with `linkSeriesCharacter`; namesakes are marked `ambiguous`. Read-only; 404 `book_not_found`.

### Character references from accepted evidence

- `listCharacterReferences` returns the projection of the accepted analysis-pipeline versions onto the current book: discovery and profile quotations (`profile_evidence`), attributed dialogue and cast-name mentions, in reading order. It is computed from the current book on every call, as the next pipeline write would record it, and records nothing, so manual edits and acceptance show at once. Rows the removed Classic engine wrote are carried until a discovery version is accepted. A book with no accepted evidence step lists its stored rows unchanged.
- `CharacterReference` gains optional provenance fields on projected rows: `step`, `version_id`, `origin` (`run`, `baseline`, `external`, `manual`, `book` or `cast_names`), `projection` and, for profile quotations, `anchors` (how many locations match). Rows the removed Classic engine wrote omit them.
- A speaker confirmed before contract 0.2.0 (edited, with confidence 1.0) counts as reviewed (`provider: "reviewed"`).

### Additive

- `Status` gains `tts_quota`: `{<tts_model>: ChapterListenQuota}` with `requests_today`, `rpd`, `resets_at` and `scope`, the same count chapter-listening jobs report. `requests_today` is 0 before any usage is recorded.
- `Status` gains `analysis_step_presets` (`StepPresetView[]`, empty when none), and `updateSettings` accepts `analysis_step_presets` (`StepPreset` / `StepPresetConfig`): a whole-list replacement of at most 50 owner-authored step settings. New 400 codes on `updateSettings`: `unknown_step`, `step_config_invalid` and `step_preset_invalid` (an empty name, a repeated `id`, or a repeated name for one step).
- The presented `Book` gains the always-sent `language`: a BCP 47 tag from the EPUB's `dc:language`, or null.
- Each presented passage (`BookPassage`) gains the always-sent `manual_fields`: the passage fields a person set by hand (`speaker_id`, `direction`, `cues`, `seed`), which analysis never replaces. The lock bookkeeping removed from the book document in 0.2.0 stays unpublished; this is the one public view of it.
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
