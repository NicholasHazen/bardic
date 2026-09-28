# Series memory on the step pipeline

Status: all four sections implemented 2026-09-28. Section 1 ([evidence projection](ANALYSIS-PIPELINE.md#evidence-projection)) landed first, without its observation writes; sections 2, 3 and 4 landed together in the follow-up ([series memory](ANALYSIS-PIPELINE.md#series-memory), contract 0.3.0). Each section notes where the build differs from the design. Follows the series-run rebuild ([series runs](ANALYSIS-PIPELINE.md#series-runs)).

## The gap (as it was before this work)

A later volume can use what earlier volumes established about a character. `SeriesService.context_for_book` ([series](../bardic/series.py)) builds that context for the **profiles** step: observations from strictly earlier volumes, for characters with a **confirmed** link, validated against the current source text. Nothing is invented for missing volumes.

`context_for_book` reads the `character_observations` table. Its only writer is the legacy phase engine: `Store._save_analysis_checkpoint` → `series.retain_observations(references)`. Step-pipeline runs write no observations. So once the legacy engine and its data are removed, series context is empty.

The same root cause empties the Cast tab's **References & appearances** panel. It reads `character_references`, which also has only one writer, the legacy checkpoint.

**Root cause:** accepted step outputs carry exact evidence (discovery quotations, profile evidence, directing attributions), but no step turns that evidence into the reference tables other features read.

## Design

### 1. Evidence projection (fixes Cast references; prerequisite for series memory)

**Implemented 2026-09-28** as one registry-level projector, [evidence.py](../bardic/pipeline/evidence.py). What was built differs from the sketch below in these ways:

- Dialogue rows come from the book's current attributions, with the chapter's accepted directing version as provenance. A manual speaker choice wins (`provider: reviewed`), as in directing's own projection. Mentions come from the current cast names.
- Profile quotations are located inside the same character's discovery evidence from the discovery versions the profile run recorded. Every matching location is kept (`anchors`). Earlier-volume quotations are dropped as `unanchored`.
- The rebuild runs in `projection.sync`, so every decision (including set aside and baseline/external capture) and every read path refreshes it. A digest of its inputs and of the stored row IDs skips unchanged rebuilds and repairs rows a phase checkpoint replaced.
- Migration is lazy: each book is rebuilt at its first sync and recorded in `pipeline_state`. A book with no accepted discovery, profiles or directing version keeps its references. Legacy `profile_evidence` rows are carried until discovery has an accepted version.
- **No observation writes (owner decision, 2026-09-28).** The bullet below about appending to `character_observations` is **not** implemented. `context_for_book` still reads that table. Appending accepted evidence would change later volumes' profile prompts during a series run, and the series would stop at book 2 on a fingerprint mismatch. That was measured in a test before the append was removed. `evidence.retain_history` does the append and is switched off by `RETAIN_OBSERVATIONS = False`.
- **Sections 2 and 4 must land together.** Section 2 makes later volumes read earlier volumes' accepted evidence. That changes later books' prompts during a series run, which only section 4's context-pending plans and staged consent make safe. The same follow-up decides whether to enable the observation append, as inspection history only, or drop it. *(Decided: kept off; see section 2.)*

The original design:

Treat `character_references` as a **current-book projection** of accepted evidence, like the book's `characters` and `segments`. Accepting, rolling back or setting aside a version then changes what it shows.

- Add an `evidence` projection hook to the step contract, or one registry-level projector, for `discovery`, `profiles` and `directing`. It maps an accepted payload to reference rows `{character_id, chapter_id, segment_id?, start, end, quote, kind: dialogue|profile_evidence|mention, provider, model, confidence, step, version_id}`.
- On accept or rollback, inside the existing accept transaction, rebuild that book's `character_references` from the accepted versions of those steps. Every row must still pass `retain_observations`' validation: the source slice equals the quote and the offsets are in bounds. Invalid evidence is dropped, never repaired.
- Also append each new valid row to `character_observations` as retained history, using `retain_observations` (content-addressed, `INSERT OR IGNORE`). History is kept for inspection. It is not what prompts read (see 2). *(Deferred; see above.)*

### 2. Series context reads accepted evidence of earlier volumes

**Implemented 2026-09-28** in `SeriesRepository.context_for_book` ([series.py](../bardic/series.py)). Differences from the design below:

- **Source re-validation.** Reference rows carry no source hash. The evidence projection now records the chapter hashes it validated against (`pipeline_state.body.evidence.sources`), and a row whose chapter text changed since is left out until the next rebuild. A row the phase engine wrote counts only while the observation that the same checkpoint retained has the current chapter hash; after the Stage 4 data drop such rows are ignored.
- **Staleness.** A profile version records `series_inputs`: per earlier linked volume, a digest of the characters linked there, that volume's accepted discovery and directing versions (all chapters) and those characters' accepted profiles (`series.evidence_inputs`). `projection.stale_scopes` compares it with the current value, so a rollback, a new acceptance or a changed link in volume 1 marks volume 2's profile stale. The existing note shows it; nothing re-runs. The digest covers every chapter, so it over-fires like staleness within one book. A manual speaker edit in volume 1 changes its references but no version, so it is not detected, and a link added after a profile was produced is not detected either.
- **Dependencies.** Each profile unit declares the accepted `step_output` versions its earlier-volume entries came from, a `character_observation` artifact for each entry it sends (with its verified source as a dependency) and the `series_context` link snapshots, as before.
- **Prompt.** An earlier-volume entry in the prompt shows only book title, reading order, chapter title, kind, quote, description and direction. IDs, offsets, hashes and producers stay in the recipe (`prior_observations`) and artifacts, so a new version with the same reading does not become a new paid request. A profile prompt whose context is empty is byte-identical to before (`tests/test_prompt_identity.py` is unchanged).
- **Unit set.** Profiles in the step pipeline now skip a character with no accepted discovery evidence in its own book, even when earlier volumes have evidence for it (`profile_specs(require_current_evidence=True)`). Earlier volumes then change what a unit says, never which units exist, which section 4 relies on. The phase engine keeps its old rule.
- **Observations (decided).** `RETAIN_OBSERVATIONS` stays off and nothing reads `character_observations` for prompts. History is the step_output versions and the `character_observation` artifacts recorded when an entry is sent. The legacy table and its rows stay until the owner-gated Stage 4 data drop; nothing deletes them.

The design:

Change `context_for_book` to read earlier volumes' **current** `character_references` (their accepted evidence) instead of the append-only `character_observations`. Keep every existing rule: strictly earlier position, confirmed links only, archived books excluded, source hash and quote re-validated, even sampling, round-robin budget, and `mention` excluded.

- Why: if volume 1's profile is rolled back or set aside, volume 2 should stop being told the rejected version. The append-only table cannot express that.
- Dependencies: the profiles step's units already declare dependencies. Add the earlier volumes' accepted evidence artifact IDs, so that a change in volume 1 marks volume 2's profiles **stale** through the existing mechanism, and nothing re-runs automatically.

### 3. Suggested links (optional, cheap)

**Implemented 2026-09-28**: `GET /api/books/{id}/series/suggestions` (`listSeriesLinkSuggestions`) and a **Suggested links** list in the book's Series continuity panel. Suggestions are computed on request from the current cast, not stored. A character matches when one of its names or aliases equals (ignoring case and repeated spaces) a name or alias of a character with a confirmed link in a strictly earlier, active volume. Namesakes, and one identity matched by two characters, are `ambiguous`: the panel shows a choice with nothing preselected. Confirming uses the existing link route. There is no dismiss action; a suggestion disappears once the character is linked.

After discovery in volume N, propose links from its characters to existing series identities by exact normalized name or alias match. Suggestions are shown in the series/Cast UI for **confirmation**. They are never auto-applied, because book-local IDs are not series identities (AGENTS.md).

### 4. Series runs and consent (the hard part)

**Implemented 2026-09-28** in [series_processing.py](../bardic/series_processing.py). Differences from the design below:

- **Context-pending.** A step declares `reads_series_context` (today only profiles). In a book whose linked characters have a confirmed link in an earlier book of the run, that step is `context_pending`. The book's `up_to` estimate is its plan with that step planned `fresh` (every unit a request; tokens from the current context). The consent shows "Up to N requests" and says the cost can be higher. Unknown prices stay unknown.
- **Consent fingerprint.** Each book's `consent_fingerprint` covers its revision, `fresh`, and each step's ID, version, provider, model and unit locators, plus the cache keys of steps that are not context-pending. The series `fingerprint` is built from these; the per-book plan `fingerprint` is still reported.
- **Review pause.** The series pauses after a book only when a later book of the run reads it (`context_sources`) and one of its evidence-step versions (discovery, profiles or directing) from this run is still a candidate. A review gate on a book no later book reads leaves its version waiting and the run continues, as before. The pause is explicit: the parent job stays `running` with `waiting_for_review`, and no worker thread waits. **Resume** is an explicit owner action (`resumeSeriesProcessing`), refused while anything waits. Accepting does not resume by itself, because resuming starts paid work and should be its own decision.
- **Reservations while paused.** Every book stays reserved, except that the waiting book accepts version decisions (accept, roll back, set aside); the reject route never checked reservations. Edits, link changes and single-book runs stay refused on every book, including the waiting one, so nothing else can change a later book's unit set before it starts.
- **Cancellation and restarts.** Cancelling a paused parent settles it at once (no worker is running): queued children end `cancelled` with `not_started`. A child cancelled while paused is never started by resume; the series then ends `cancelled`. Resume needs the provider keys snapshotted at confirmation, which are held in memory only, so a server restart interrupts a paused run like any active job. While paused, the run counts as an active job, so `./bardicctl restart` refuses without `--force`.

The design:

With memory, earlier books' results legitimately change later books' plans: volume 2's profile prompts include volume 1's accepted evidence. Today's series coordinator re-plans each book before starting it and stops on a fingerprint mismatch, so it would always stop at volume 2.

Proposed:
- The series plan marks later books' profile units **context-pending**, the same way `inputs_pending` works when two steps run in one request. Their estimate is "up to": unit count and request count don't depend on context, only prompt size does.
- The confirmed series fingerprint covers each book's unit set, provider, model, `fresh` and the step version, but **not** the context contents. The re-check before each book compares only those fields.
- Each book's step must have **accepted** results before the next book reads them. With a "Hold for my review" gate, the series pauses after that book ("Waiting for your review of Book 1") and resumes on accept. It never proceeds on unreviewed candidates.
- Token-based cost can grow with context. The consent copy must say so, and unknown stays unknown.

## Migration and data

As built: no new tables and no migration step. The evidence state gains `sources` (rebuilt once per book at its next sync), a profile version payload gains `series_inputs` only in a series, and series jobs gain `consent_fingerprint`, `context_pending`, `context_sources`, `context_pending_books` and `waiting_for_review`.

The design:

- No new tables. `character_references` changes meaning from "last legacy checkpoint" to "accepted pipeline evidence". Rebuild it for every book at migration time from accepted versions. Books with no pipeline history keep an empty set.
- `character_observations` stays as retained history. As built, section 1 does not write to it; see section 1. Legacy rows can be deleted with the phase-engine data (owner decision D3: "kill it and build fresh").

## Tests (offline)

As built: [test_series_memory.py](../tests/test_series_memory.py) covers the list below; [test_evidence_projection.py](../tests/test_evidence_projection.py) covers section 1.

- Accepting discovery or profiles fills references. Rollback removes the rolled-back evidence. Invalid spans are dropped.
- Volume 2's profile context includes volume 1's accepted evidence only through confirmed links and only from earlier positions. Rollback in volume 1 removes it and marks volume 2 stale.
- A series run with memory completes across two books without a fingerprint stop. A review gate pauses and resumes. A cancelled queued child still never starts.
- Link suggestions never create links without confirmation.

## Size

Estimated at about 2 agent-sessions; sections 2–4 took one. Session 1 covers sections 1 and 2 plus tests. Session 2 covers sections 3 and 4 plus the series UI (pause/resume on review). Section 1 is also a prerequisite for removing the legacy engine without breaking Cast references.
