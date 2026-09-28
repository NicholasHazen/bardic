# Removing the legacy phase ("Classic") analysis engine

Status: stages 1–3 are done (2026-09-28). Stage 4 is pending. The owner approved the four stages, and stage 4 needs the owner's go before it runs on a real library.

"Classic" is the analysis engine that came before the step pipeline. It covered the progressive phase runner (`progressive.py`), the older chapter-checkpoint runner (`staged_analysis.py`), their unit cache and coverage reader (`legacy_phase.py`), the `analysis_checkpoints` record in [store.py](../bardic/store.py), and the book routes `POST /analyze`, `GET /preprocessing`, `POST /analysis-plan` and `GET /analysis`. `GET /pipeline` read its state too. The UI stopped offering the engine in UX phase 2, and stage 3 deleted its code and routes. The step pipeline ([pipeline/](../bardic/pipeline/), [the analysis pipeline](ANALYSIS-PIPELINE.md)) replaces it. Series runs already use the step pipeline ([decision](DECISIONS.md)).

## The four stages

Each stage is its own pull request.

| Stage | What changes | Behavior change | Gate |
| --- | --- | --- | --- |
| 1. Untangle (**done**) | Live code stops importing the engine. Shared code moves out of it. | None. Prompts and cache keys are byte-identical. | Full suite; [prompt identity test](../tests/test_prompt_identity.py); [isolation test](../tests/test_legacy_isolation.py) |
| 2. Evidence projection (**done**) | Accepted step evidence becomes `character_references` and series observations. The checkpoint stops being their only writer. | Cast references and series context come from accepted pipeline versions. | [Series memory plan](SERIES-MEMORY-PLAN.md) |
| 3. Delete code paths (**done**) | Delete the engine files, their routes and their tests. Rebuild the Details explorer from the step pipeline. | The Classic routes are no longer served. Existing data is still readable where noted below. | Stage 2 merged; contract 0.3.0 |
| 4. Drop data | A migration drops the legacy tables and legacy rows, after a verified backup and backfill. | Legacy rows are gone. Everything listed under "keep" is intact. | **Owner go**; [operations](OPERATIONS.md) backup |

## Stage 1: what moved

Nothing was renamed on the wire, and no table schema changed.

| From | To | Why |
| --- | --- | --- |
| `progressive.discovery_ranges`, `discovery_specs`, `spread`, `profile_specs`, `direction_cast`, `direction_specs` | [pipeline/prompts.py](../bardic/pipeline/prompts.py) | The Discovery, Profiles and Directing steps build their requests with these. `progressive` imports them back. |
| `staged_analysis._split_chapter` | [analysis_common.py](../bardic/analysis_common.py) `split_chapter` | The Directing step publishes scene boundaries with it. |
| `staged_analysis.fingerprint`, `PIPELINE_VERSION` | [analysis_common.py](../bardic/analysis_common.py) | Structure repair re-keys a saved checkpoint with it. It now uses `processing.digest`, which is the same SHA-256 of the same sorted JSON. |
| `ProcessingStore.unit`, `save_unit`, `units`, and creation of `analysis_units` | `legacy_phase.py` (deleted in stage 3) `LegacyProcessingStore` (a subclass) | [processing.py](../bardic/processing.py) now holds only shared request infrastructure. It neither creates nor reads `analysis_units`. |
| `preprocessing.coverage` | `legacy_phase.py` (deleted in stage 3) `coverage` | It reads `analysis_units` and `analysis_checkpoints`. The census stays in [preprocessing.py](../bardic/preprocessing.py). |

`progressive` and `staged_analysis` re-export the moved names, so the engine and its tests run unchanged. Test files changed only in their import lines. The legacy unit tests import `LegacyProcessingStore as ProcessingStore`.

`analysis_units` is created only when `LegacyProcessingStore` is constructed. Existing libraries already have the table. Everything else that reads it checks that it exists first: the artifact backfill and the library storage measurement. A new library got the table only if a Classic route ran; since stage 3, nothing creates it.

**Evidence of no behavior change.**

- [test_prompt_identity.py](../tests/test_prompt_identity.py) pins SHA-256 digests of the discovery, resumed-discovery, profile and direction request specs for a synthetic four-chapter book. It also pins the step pipeline's unit cache keys for the Discovery, Profiles and Directing steps, and the checkpoint fingerprint. The digests were recorded from the pre-move code at `7b1f5b0`. A JSON dump of the specs was also byte-compared before and after the move.
- The existing suite passes with no assertion edits.

## Stage 3: what was removed

Stage 3 landed on 2026-09-28 with contract **0.3.0 (BREAKING)**. The owner answered the three open questions first:

1. **Keep the Details explorer.** `GET /api/books/{id}/pipeline` stays, with its stage cards rebuilt from the step pipeline.
2. **Delete the in-memory cloud path.** `analysis._cloud` and `_reconcile_known_aliases` had no caller left. The demo's no-store local draft stays.
3. **Keep Classic-era immutable artifacts.** They are retained history, and stage 4 keeps them.

### Code

| Removed | Notes |
| --- | --- |
| `bardic/progressive.py`, `bardic/staged_analysis.py`, `bardic/legacy_phase.py` | The phase runner, the chapter runner, and their unit cache and coverage reader. |
| The store and phase branches of `analysis.analyze_book`, plus `analysis._cloud`, `_reconcile_known_aliases` and `DEFAULT_MODELS` | `analyze_book(book, provider='local', progress, cancelled)` now runs only the local heuristic draft. `POST /api/demo` uses it. Any other provider raises `ValueError`. The shared request, evidence and annotation helpers that the pipeline imports stay. |
| `app.Runtime.analyze`, `AnalysisLimits`, `AnalysisRequest` | Only `POST /analyze` used them. |
| Routes `POST /api/books/{id}/analyze`, `GET …/preprocessing`, `POST …/analysis-plan`, `GET …/analysis` | They are no longer served. As for any unknown path, a GET gets 404 and a POST gets 405 (from the static-file mount). Their `op()` entries, their views and the `Classic analysis` tag are removed from `bardic/apispec/`. |
| `static/production.js`, `static/production.css` | They were already unloaded; `index.html` did not reference them. |
| `app.js`: `renderAnalysisProgress`, `updateAnalysisHint`, `state.analysisSummary` and `analysisError`, the `/analysis` request in `pollJobs`, and the `analyze` branch of `startJob` | Also removed: the dead `#analyze-button`, `#analysis-provider` and `#analysis-scope` selectors in `updateBusyControls`. `pollJobs` now makes one request, `GET /api/jobs`. |

### The Details explorer

`pipeline_view.pipeline()` no longer reads Classic coverage, `analysis_units` or the checkpoint. It shows these cards:

- `import` and `series`.
- One card per registered step, in pipeline order: `structure`, `census`, `discovery`, `quotes`, `profiles` and `directing`. The counts come from `pipeline.api.step_states`, which the Analyze overview (`GET /analysis-pipeline`) also uses, so both surfaces show the same numbers. `completed`/`total` are the accepted and total scopes. `stale_count` is the number of accepted scopes whose inputs changed. `candidate_count` is the number of versions waiting for review. Status is `queued`/`running` while the latest step version runs, `stale` when anything accepted is out of date, and otherwise follows the counts.
- `voices`, `narration`, `alignment` and `export`.

Like the overview, the explorer first records outside changes (`projection.sync`). The artifact counts, the artifact browser, attempts, events, usage, source search, the story map and the analysis export are unchanged. Artifact counts include Classic-era versions whose stage has the same name.

### Tests

Deleted: `test_progressive*.py`, `test_staged*.py`, `test_production_ui.py`, `production_ui_test.js`, seven coverage tests in `test_preprocessing.py` (two more became census tests), the unit-cache half of `test_processing.py`'s cache test, `test_resources.py`'s progressive validator test, and `test_app.py`'s local `/analyze` test. The step pipeline's manual-lock tests cover the guarantee that last test made.

Rewritten to drive the pipeline:

| Test | Now |
| --- | --- |
| `test_provider_settings.py`, 5 tests | Key and model isolation, explicit override, no fallback when a key is missing, queued-run snapshot and key redaction after rotation. Each runs a Discovery run with the real plan and fingerprint and a fake adapter per provider. |
| `test_library_api.py` archive test | An archived book refuses `POST …/analysis-pipeline/runs` (400). |
| `test_analysis_pipeline.py` outside change | The local draft written straight to the store is recorded as `external`, and accepting `baseline` restores the speakers. New tests: a manual passage edit is a lock, not an outside change; the explorer's step cards match the overview; stale and waiting counts. |
| `test_identity_resolution.py`, 2 tests | Against `pipeline.prompts.profile_specs` and stage 2's `evidence.refresh`. |
| `test_analysis.py`, `test_analysis_providers.py` | The cloud-path tests became direct `_apply_annotations` checks (invented IDs, speakers and evidence are rejected; reviewed passages and scenes are kept) and an adapter-level transport redaction test. |
| `test_pipeline_view_api.py` | Classic coverage tests replaced by: a local draft is never counted as discovery. |
| `test_artifacts.py` | Legacy-unit fixtures create the old `analysis_units` table directly. The backfill tests stay. |
| `test_legacy_isolation.py` | Now proves the engine is gone: no module, no importer, no load at runtime, no served route, no browser call to a removed route. A new library never creates `analysis_units`, and an existing library's rows survive until `ArtifactRepository.backfill` retains them. |
| `listen_job_poll_test.js`, `pipeline_ui_test.js` | Polling no longer requests `/analysis`. The cards render the out-of-date and waiting counts. |
| `tools/contract-codegen-check.mjs` | The smoke body uses `startBookAnalysisPipelineRun`. |

### What still references Classic data (stage 4)

A grep for `progressive`, `staged_analysis`, `legacy_phase`, `analysis_units` and `/analyze` in `bardic/` finds only these:

- `artifacts.backfill` reads `analysis_units` and `analysis_checkpoints` if they exist.
- `library._payload_bytes` lists `analysis_units` and `analysis_checkpoints`.
- `/v1/analyze` (in `local_services.py` and `pipeline/runner.py`) is the self-hosted service path and is unrelated.
- "progressive-disclosure" in `style.css` names a UI pattern and is unrelated.

## Stage 4 inventory: code

Delete these in the same pull request as the data migration, after it runs:

| Item | Why it waited |
| --- | --- |
| `store.Store`: the `analysis_checkpoints` DDL, the startup recovery loop, and `analysis_checkpoint`, `save_analysis_checkpoint`, `_save_analysis_checkpoint`, `commit_analysis`, `delete_analysis_checkpoint` and `analysis_status` | Structure repair and test fixtures still use them. Startup recreates the table if the DDL stays. |
| `app.repair_book_structure`'s checkpoint branch, `structure.transform_checkpoint_structure`, `analysis_common.fingerprint` and `PIPELINE_VERSION` | They re-key an existing checkpoint during structure repair. `test_prompt_identity.py`'s checkpoint fingerprint test goes with them. |
| The `artifacts.backfill` branches for `analysis_units` and `analysis_checkpoints` | The migration runs them first. Keep `test_artifacts.py`'s legacy backfill tests and `test_legacy_isolation.py`'s backfill test until then. |
| The `analysis_units` and `analysis_checkpoints` entries in `library._payload_bytes` | They are measured only while the tables exist. |
| Stage 2's carry-over of legacy `profile_evidence` rows in `pipeline/evidence.py` | Those rows live in `character_references`, not in the dropped tables. Remove the carry-over only if the migration rebuilds the table from accepted versions (see the Drop table). |
| Checkpoint fixtures in `test_analysis_store.py`, `test_series.py`, `test_library.py`, `test_library_api.py`, `test_artifacts.py`, `test_evidence_projection.py`, `test_pipeline_view_api.py`, `test_series_structure_api.py` and `test_contract_series.py` | Move each one to the stage 2 reference writer, or delete it with the table. |
| Contract descriptions of Classic-only book fields (`profile_state`, `profile_provisional`, `profiles_provisional`, a Classic `phase`) and `analyze` jobs | Keep them while stored books and jobs can still carry the values. Stage 4 does not rewrite books, so these descriptions probably stay. |

## Stage 4 inventory: data

Run this as a one-time maintenance migration with the owner's go. It is not part of routine startup.

### Before dropping anything

1. Stop the service with `./bardicctl`, not by signalling it, and take a full recoverable backup as described in [operations](OPERATIONS.md). A database-only copy omits media.
2. For **every** book, including archived ones, run `ArtifactRepository(store).backfill(book_id)`. It is idempotent. It copies `analysis_units` rows, checkpoint units and `character_observations` into immutable `analysis_output` / `analysis_input` / `character_observation` artifacts. Without this step, legacy results that were never backfilled are lost, not archived.
3. Record the row counts per table and per book. Check that every `analysis_units` row now has an `analysis_output` head, and that Cast references and series context for linked books come from stage 2's writer.

### Drop

| Data | How |
| --- | --- |
| `analysis_units` and index `analysis_units_stage` | `DROP TABLE` / `DROP INDEX` |
| `analysis_checkpoints` | `DROP TABLE`. Delete the `Store` DDL, the restart-recovery loop and the checkpoint methods in the same PR, or startup recreates an empty table. |
| Legacy `character_observations` rows | **Do not delete them in stage 4 as merged.** Stage 2 writes nothing to this table (`evidence.RETAIN_OBSERVATIONS` is off, an owner decision), so every row is Classic-era, and series context (`SeriesRepository.context_for_book`, read by the Profiles step) still reads them. Delete them only after the series-memory follow-up gives series context another source. Then count the rows and show the count to the owner first. |
| `character_references` rows written by checkpoints | Stage 2 rebuilds a book's rows on sync once it has an accepted `discovery`, `profiles` or `directing` version. It carries legacy `profile_evidence` rows until discovery is accepted. A book with none of those versions keeps its checkpoint rows. Leave them: the table is the current projection, and emptying it would blank Cast references. |

### Keep

| Data | Why |
| --- | --- |
| `character_observations` table and its stage 2 rows; `series`, `series_books`, `series_characters`, `series_character_links`, `series_volume_slots` | Series memory and confirmed identities |
| `character_references` | Stage 2's current-book projection of accepted evidence |
| `analysis_attempts` (including Classic-era rows) | Usage history. `RequestBudget` sums every tracked attempt for a book, so deleting old rows would silently reset a book's spending guard. |
| `pipeline_events` (including Classic-era rows) | Validation state for displayed attempts, and resource summaries |
| `pipeline_*` tables, `artifact_versions`, `artifact_dependencies` and heads, including Classic-era `analysis_input`, `analysis_output` and `analysis_rejection` artifacts | Immutable provenance. Retained history is not erased (AGENTS.md). |
| `book_preprocessing` | The Census step cache |
| `books.body` fields that Classic wrote (`profile_refined`, `profile_input_key`, `profile_state`, `analysis_provider` and others) | Part of the current book projection. Removing them is a separate projection change. |
| Jobs of kind `analyze` | Job history |
| The `preprocess_models_by_provider` preference | The pipeline's `scan` model role reads it. |

## Owner decisions

1. **`GET /pipeline` and the Details explorer.** Keep it, and rebuild the stage cards from step-pipeline state. Done in stage 3.
2. **The in-memory cloud path** (`analysis._cloud`). Delete it. Done in stage 3.
3. **Classic-era artifacts in stage 4.** Keep them as immutable history. "Discard the phase engine and its data" means the live tables, not the retained provenance.
