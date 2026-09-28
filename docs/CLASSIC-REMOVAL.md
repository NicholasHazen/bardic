# Removing the legacy phase ("Classic") analysis engine

Status: stage 1 done (2026-09-28). Stages 2–4 are pending. The owner approved the four stages; stage 4 needs the owner's go before it runs on a real library.

"Classic" is the analysis engine that came before the step pipeline. It covers the progressive phase runner ([progressive.py](../bardic/progressive.py)), the older chapter-checkpoint runner ([staged_analysis.py](../bardic/staged_analysis.py)), their unit cache and coverage reader ([legacy_phase.py](../bardic/legacy_phase.py)), the `analysis_checkpoints` record in [store.py](../bardic/store.py), and the book routes `POST /analyze`, `GET /preprocessing`, `POST /analysis-plan`, `GET /analysis` and `GET /pipeline`. The UI stopped offering it in UX phase 2. The step pipeline ([pipeline/](../bardic/pipeline/), [the analysis pipeline](ANALYSIS-PIPELINE.md)) replaces it. Series runs already use the step pipeline ([decision](DECISIONS.md)).

## The four stages

Each stage is its own pull request.

| Stage | What changes | Behavior change | Gate |
| --- | --- | --- | --- |
| 1. Untangle | Live code stops importing the engine. Shared code moves out of it. | None. Prompts and cache keys are byte-identical. | Full suite; [prompt identity test](../tests/test_prompt_identity.py); [isolation test](../tests/test_legacy_isolation.py) |
| 2. Evidence projection | Accepted step evidence becomes `character_references` and series observations. The checkpoint stops being their only writer. | Cast references and series context come from accepted pipeline versions. | [Series memory plan](SERIES-MEMORY-PLAN.md) |
| 3. Delete code paths | Delete the engine files, their routes and their tests. Remove the references listed in `ALLOWED` in the isolation test. | The Classic routes return 404. Existing data is still readable where noted below. | Stage 2 merged; inventory below |
| 4. Drop data | A migration drops the legacy tables and legacy rows, after a verified backup and backfill. | Legacy rows are gone. Everything listed under "keep" is intact. | **Owner go**; [operations](OPERATIONS.md) backup |

## Stage 1: what moved

Nothing was renamed on the wire, and no table schema changed.

| From | To | Why |
| --- | --- | --- |
| `progressive.discovery_ranges`, `discovery_specs`, `spread`, `profile_specs`, `direction_cast`, `direction_specs` | [pipeline/prompts.py](../bardic/pipeline/prompts.py) | The Discovery, Profiles and Directing steps build their requests with these. `progressive` imports them back. |
| `staged_analysis._split_chapter` | [analysis_common.py](../bardic/analysis_common.py) `split_chapter` | The Directing step publishes scene boundaries with it. |
| `staged_analysis.fingerprint`, `PIPELINE_VERSION` | [analysis_common.py](../bardic/analysis_common.py) | Structure repair re-keys a saved checkpoint with it. It now uses `processing.digest`, which is the same SHA-256 of the same sorted JSON. |
| `ProcessingStore.unit`, `save_unit`, `units`, and creation of `analysis_units` | [legacy_phase.py](../bardic/legacy_phase.py) `LegacyProcessingStore` (a subclass) | [processing.py](../bardic/processing.py) now holds only shared request infrastructure. It neither creates nor reads `analysis_units`. |
| `preprocessing.coverage` | [legacy_phase.py](../bardic/legacy_phase.py) `coverage` | It reads `analysis_units` and `analysis_checkpoints`. The census stays in [preprocessing.py](../bardic/preprocessing.py). |

`progressive` and `staged_analysis` re-export the moved names, so the engine and its tests run unchanged. Test files changed only in their import lines. The legacy unit tests import `LegacyProcessingStore as ProcessingStore`.

`analysis_units` is created only when `LegacyProcessingStore` is constructed. Existing libraries already have the table. Everything else that reads it checks that it exists first: the artifact backfill and the library storage measurement. A new library gets the table only if a Classic route runs.

**Evidence of no behavior change.**

- [test_prompt_identity.py](../tests/test_prompt_identity.py) pins SHA-256 digests of the discovery, resumed-discovery, profile and direction request specs for a synthetic four-chapter book. It also pins the step pipeline's unit cache keys for the Discovery, Profiles and Directing steps, and the checkpoint fingerprint. The digests were recorded from the pre-move code at `7b1f5b0`. A JSON dump of the specs was also byte-compared before and after the move.
- The existing suite passes with no assertion edits.

## Dependency check

[test_legacy_isolation.py](../tests/test_legacy_isolation.py) keeps stage 1 true:

- **Static.** It parses every module under `bardic/`, including imports inside functions and `from . import x`. Only the modules in `ALLOWED` may import `progressive`, `staged_analysis` or `legacy_phase`. None of `bardic/pipeline/` or the shared modules may import them.
- **Runtime.** Importing `bardic.app`, the pipeline router, runner, projection, prompts and steps, `pipeline_view`, `series_processing`, `processing` and `preprocessing`, then building the step registry, loads none of the three legacy modules.
- **Storage.** The shared `ProcessingStore` does not create `analysis_units`. On an existing library, the table's DDL and rows are unchanged, and `LegacyProcessingStore` reads them.

```sh
uv run --frozen pytest -q tests/test_legacy_isolation.py tests/test_prompt_identity.py
# Quick manual view of what still references the engine:
grep -rnE "(progressive|staged_analysis|legacy_phase)( |$|,)" bardic --include='*.py' | grep import
```

After stage 1, the only references are the three `ALLOWED` modules:

| Module | Reference | Stage 3 action |
| --- | --- | --- |
| `bardic/app.py` | `GET /preprocessing` (`legacy_phase`, `progressive`), `POST /analysis-plan` (`progressive.plan`) | Delete the routes |
| `bardic/analysis.py` | `analyze_book()` sends calls with a store to `progressive.run` or `staged_analysis.analyze_staged` | Remove the store branches |
| `bardic/pipeline_view.py` | `pipeline()`, the `GET /pipeline` readout | Rewrite or delete (a decision, below) |

## Stage 3 inventory: code

Delete means removing the code in stage 3. Keep means it stays live. Line numbers are from stage 1.

### Backend modules and functions

| Item | Action | Why |
| --- | --- | --- |
| `bardic/progressive.py` (whole file) | Delete | The phase engine. The builders it re-exports live in `pipeline/prompts.py`. |
| `bardic/staged_analysis.py` (whole file) | Delete | The chapter engine, including its local draft path. `_references` is the checkpoint's reference builder; stage 2's projector replaces it. If stage 2 reuses it, move it before deleting the file. |
| `bardic/legacy_phase.py` (whole file) | Delete | `LegacyProcessingStore` and `coverage` only serve the engine, `/preprocessing` and `/pipeline`. |
| `bardic/pipeline/prompts.py` | Keep | Live request builders. Update the docstring's mention of `progressive`. |
| `bardic/analysis_common.py` `split_chapter` | Keep | Used by the Directing step. |
| `bardic/analysis_common.py` `fingerprint` | Keep until stage 4 | Structure repair re-keys an existing checkpoint. It goes with `analysis_checkpoints`. |
| `bardic/processing.py` | Keep | Shared: `RequestBudget`, `BudgetReached`, attempts, events, census cache, `digest`, `source_hash`, `token_estimate`, `price_for`, `request_context`. |
| `bardic/preprocessing.py` (`census`, `eligible_chapters`) | Keep | The Census step, prompts and pipeline planning use them. |
| `analysis.analyze_book()` store/phase branches (lines 778–786) and the `phase`, `scan_model`, `limits`, `run_id`, `chapter_id`, `resume`, `prepare` parameters | Delete | Only `Runtime.analyze` passes a store. `POST /api/demo` calls `analyze_book(book, "local")` with no store, which runs `_local`. Keep that path. |
| `analysis._cloud` (the in-memory cloud path) and `_reconcile_known_aliases` | Decide | Nothing calls `_cloud` once the store branches go, and `_reconcile_known_aliases` is used only by `_cloud` and `staged_analysis`. `tests/test_analysis.py` and `test_analysis_providers.py` test the path directly. Delete both with those tests, or keep them as a documented in-memory fallback. |
| `analysis._evidence_spans` | Keep | Evidence validation uses it. It maps quotes to exact spans, which stage 2's projection also needs. |
| `analysis._local`, `_scene_members`, `_check_cancel`, `_validate_source` | Keep | The demo and in-memory local path use them. |
| `app.Runtime.analyze()` (lines 833–877), `AnalysisLimits`, `AnalysisRequest` | Delete | Only `POST /analyze` uses them. |
| `app` imports of `transform_checkpoint_structure` and the `analysis_status` / `analysis_checkpoint` / `commit_analysis` branch in `repair_book_structure` | Keep until stage 4 | The branch runs only when a checkpoint exists. Without it, a repair would leave a stale checkpoint and stale `character_references`. It goes with the table. |
| `store.Store`: `analysis_checkpoints` DDL, startup recovery loop, `analysis_checkpoint`, `save_analysis_checkpoint`, `_save_analysis_checkpoint`, `commit_analysis`, `delete_analysis_checkpoint`, `analysis_status` | Keep until stage 4 | Structure repair, `GET /analysis` and test fixtures still use them. `_save_analysis_checkpoint` writes `character_references` and calls `series.retain_observations`. Until stage 2 lands, it is the only writer of both. |
| `store.character_references()`, the `character_references` DDL | Keep | Stage 2 makes this table a pipeline projection. The Cast references route and the analysis export read it. |
| `structure.transform_checkpoint_structure` | Keep until stage 4 | Used only by the repair branch above. |
| `artifacts.backfill` branches for `analysis_units` and `analysis_checkpoints` (lines 385–398) | Keep until stage 4, then delete | They turn legacy rows into immutable artifacts. Stage 4 must run them before dropping the tables. They check that each table exists, so they are safe to leave in place until then. |
| `library._payload_bytes` table list entries `analysis_units`, `analysis_checkpoints` | Keep until stage 4 | Measured only when the table exists. Remove the names after the drop. |
| `series.py` migration that seeds `character_observations` from `character_references` | Stage 2 owns this | Not Classic engine code. |
| Job kind `analyze` in `store` restart recovery and UI labels | Keep | Historical jobs still display. |

### Routes

| Route | Action | Notes |
| --- | --- | --- |
| `POST /api/books/{id}/analyze` | Delete | The Classic worker. With `provider: "local"` it ran the staged local draft. The pipeline's own local and service providers replace it. |
| `GET /api/books/{id}/preprocessing` | Delete | Classic coverage and profile status. |
| `POST /api/books/{id}/analysis-plan` | Delete | Classic phase preview. |
| `GET /api/books/{id}/analysis` | Delete, or return a static "not started" body until stage 4 | Checkpoint progress summary. `app.js` still polls it; see the frontend section. |
| `GET /api/books/{id}/pipeline` | **Decision** | `pipeline_view.pipeline()` builds its stage cards from Classic coverage, `analysis_units` and the checkpoint. It also returns attempts, events, jobs and usage, which are shared. The **Details** explorer (`pipeline.js`) renders it. Either rebuild the stage cards from `pipeline_*` state, or drop them and keep the shared parts. |
| `GET /story-map`, `/artifacts`, `/search`, `/analysis-export`, `/characters/{id}/references`, `/repair-structure`, `/api/demo` | Keep | Live features. Their shared helpers are in `pipeline_view.py` (`prepare`, `story_map`, `write_analysis_export`). |

`POST /api/demo` must keep working. It uses provider `local` with no store, so it runs `analysis._local`, not the phase engine.

On `main` (not on `ux-phase-2` at stage 1), `bardic/apispec/` and `contract/openapi.json` describe these routes. Remove them there too and follow the contract changelog.

### Frontend (stage 2 owns these files until it merges)

| File and item | Action | Why |
| --- | --- | --- |
| `static/production.js`, `static/production.css` | Delete | The Classic panel. `index.html` no longer loads it. |
| `app.js` `renderAnalysisProgress()` (line 1234) and `state.analysisSummary` / `analysisError` | Delete | `#analysis-progress` is no longer in the page, so it returns early. |
| `app.js` `pollJobs` request to `/analysis` (line 1761) | Delete | It feeds only the dead progress renderer. |
| `app.js` `startJob` branch for kind `analyze` and `#analysis-provider` | Delete | No caller passes `analyze`, and the element no longer exists. |
| `static/pipeline.js` stage cards from `GET /pipeline` | Follows the `/pipeline` decision | Story map, artifacts, search and export stay. |
| `index.html` note "The explorer below reads the older phase runs" (line 337) | Update | Follows the `/pipeline` decision. |
| `shell.js` `ANALYSIS_KINDS` and `app.js` job labels containing `analyze` | Keep | Historical job display. |

### Tests

| Test | Action |
| --- | --- |
| `test_progressive.py`, `test_progressive_api.py`, `test_progressive_artifacts.py`, `test_staged_analysis.py`, `test_staged_api.py` | Delete |
| `test_production_ui.py`, `production_ui_test.js` | Delete |
| `test_preprocessing.py`: the 9 `coverage()` tests | Delete. Keep the census tests (`test_census_*`, `test_cache_reuses_exact_inputs_*` and the others that do not call `coverage`). |
| `test_processing.py::test_unit_and_preprocessing_caches_require_matching_book_stage_and_source` | Split. Delete the unit-cache half and keep the census-cache half. Then import the shared `ProcessingStore` again. |
| `test_identity_resolution.py::test_alias_resolver_keeps_later_canonical_evidence_in_profiles_and_references` | Rewrite against `pipeline.prompts.profile_specs` and stage 2's projector, or delete. |
| `test_identity_resolution.py::test_ambiguous_candidate_evidence_is_not_attributed_to_either_shared_alias` | Retarget to stage 2's projector (it tests `_references`). |
| `test_resources.py::test_progressive_validators_and_publication_measure_local_work_and_cache_validation` | Delete, or re-express on a pipeline run. |
| `test_pipeline_view_api.py`: the 4 `/pipeline` tests | Follow the `/pipeline` decision. Keep the story-map test. |
| `test_analysis_pipeline.py::test_outside_changes_are_recorded_as_external_versions` | Rewrite. It uses `POST /analyze` (local) to make an outside change; use a passage `PATCH` instead. |
| `test_app.py::test_local_analysis_creates_cast_and_preserves_manual_corrections` | Delete, or move the manual-edit guarantee to a pipeline test. |
| `test_provider_settings.py`: the 5 `/analyze` tests | Re-express the key and model isolation guarantees on pipeline runs if they are not already covered, then delete. |
| `test_library_api.py::test_book_archive_restore_*` | Replace the `/analyze` 400 assertion with a pipeline-run refusal. |
| `test_local_analysis_services.py` | Keep. `/v1/analyze` is the service path, not the book route. |
| `test_analysis_store.py`, and fixtures that call `save_analysis_checkpoint` in `test_series.py`, `test_library.py`, `test_library_api.py`, `test_artifacts.py`, `test_pipeline_view_api.py`, `test_series_structure_api.py` | Keep until stage 4. Then move them to stage 2's reference writer or delete them with the table. |
| `test_artifacts.py` legacy backfill tests (`legacy_unit`, checkpoint backfill) | Keep until stage 4. They prove the backfill that stage 4 depends on. |
| `test_prompt_identity.py` | Keep |
| `test_legacy_isolation.py` | Keep until the modules are gone. Delete it in the stage 3 PR, or empty `LEGACY`. |

### Documentation

Update [ARCHITECTURE.md](ARCHITECTURE.md), [API.md](API.md), [DATA-MODEL.md](DATA-MODEL.md), [DEVELOPMENT.md](DEVELOPMENT.md) (test map and "adding a stage", which still point at `progressive.py`), [OPERATIONS.md](OPERATIONS.md) (request limits section), [CHAPTER-ANALYSIS.md](CHAPTER-ANALYSIS.md), [ARTIFACTS-AND-STORAGE.md](ARTIFACTS-AND-STORAGE.md) and [ROADMAP.md](ROADMAP.md). Mark [PROGRESSIVE-ANALYSIS-PLAN.md](PROGRESSIVE-ANALYSIS-PLAN.md) and [CHAPTER-ANALYSIS.md](CHAPTER-ANALYSIS.md) as historical.

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
| Legacy `character_observations` rows | Delete only rows that stage 2's writer did not produce. Stage 1 cannot say how to recognise them: stage 2 defines the provenance fields its rows carry. Stage 4 must write that predicate from stage 2's merged code, count the matches and show the count to the owner before deleting. Rows that series context still reads must not be deleted unless stage 2 has replaced them. |
| `character_references` rows written by checkpoints | Only if stage 2 did not already rebuild the table from accepted versions on accept. Rebuild from accepted versions rather than leaving the table empty. |

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

## Open decisions for the owner

1. **`GET /pipeline` and the Details explorer.** Rebuild the stage cards from step-pipeline state, or drop them and keep attempts, events, usage, story map and export.
2. **The in-memory cloud path** (`analysis._cloud`). Delete it in stage 3 with its tests, or keep it.
3. **Classic-era artifacts in stage 4.** This plan keeps them as immutable history. The owner earlier chose to "discard the phase engine and its data"; confirm that this means the live tables and not the retained provenance.
