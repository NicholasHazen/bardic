# Series memory on the step pipeline (proposed follow-up)

Status: section 1 implemented 2026-09-28 ([evidence projection](ANALYSIS-PIPELINE.md#evidence-projection)), without its observation writes. Sections 2–4 are proposed. Sections 2 and 4 must land together (see below). Follows the series-run rebuild ([series runs](ANALYSIS-PIPELINE.md#series-runs)).

## The gap

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
- **Sections 2 and 4 must land together.** Section 2 makes later volumes read earlier volumes' accepted evidence. That changes later books' prompts during a series run, which only section 4's context-pending plans and staged consent make safe. The same follow-up decides whether to enable the observation append, as inspection history only, or drop it.

The original design:

Treat `character_references` as a **current-book projection** of accepted evidence, like the book's `characters` and `segments`. Accepting, rolling back or setting aside a version then changes what it shows.

- Add an `evidence` projection hook to the step contract, or one registry-level projector, for `discovery`, `profiles` and `directing`. It maps an accepted payload to reference rows `{character_id, chapter_id, segment_id?, start, end, quote, kind: dialogue|profile_evidence|mention, provider, model, confidence, step, version_id}`.
- On accept or rollback, inside the existing accept transaction, rebuild that book's `character_references` from the accepted versions of those steps. Every row must still pass `retain_observations`' validation: the source slice equals the quote and the offsets are in bounds. Invalid evidence is dropped, never repaired.
- Also append each new valid row to `character_observations` as retained history, using `retain_observations` (content-addressed, `INSERT OR IGNORE`). History is kept for inspection. It is not what prompts read (see 2). *(Deferred; see above.)*

### 2. Series context reads accepted evidence of earlier volumes

Change `context_for_book` to read earlier volumes' **current** `character_references` (their accepted evidence) instead of the append-only `character_observations`. Keep every existing rule: strictly earlier position, confirmed links only, archived books excluded, source hash and quote re-validated, even sampling, round-robin budget, and `mention` excluded.

- Why: if volume 1's profile is rolled back or set aside, volume 2 should stop being told the rejected version. The append-only table cannot express that.
- Dependencies: the profiles step's units already declare dependencies. Add the earlier volumes' accepted evidence artifact IDs, so that a change in volume 1 marks volume 2's profiles **stale** through the existing mechanism, and nothing re-runs automatically.

### 3. Suggested links (optional, cheap)

After discovery in volume N, propose links from its characters to existing series identities by exact normalized name or alias match. Suggestions are shown in the series/Cast UI for **confirmation**. They are never auto-applied, because book-local IDs are not series identities (AGENTS.md).

### 4. Series runs and consent (the hard part)

With memory, earlier books' results legitimately change later books' plans: volume 2's profile prompts include volume 1's accepted evidence. Today's series coordinator re-plans each book before starting it and stops on a fingerprint mismatch, so it would always stop at volume 2.

Proposed:
- The series plan marks later books' profile units **context-pending**, the same way `inputs_pending` works when two steps run in one request. Their estimate is "up to": unit count and request count don't depend on context, only prompt size does.
- The confirmed series fingerprint covers each book's unit set, provider, model, `fresh` and the step version, but **not** the context contents. The re-check before each book compares only those fields.
- Each book's step must have **accepted** results before the next book reads them. With a "Hold for my review" gate, the series pauses after that book ("Waiting for your review of Book 1") and resumes on accept. It never proceeds on unreviewed candidates.
- Token-based cost can grow with context. The consent copy must say so, and unknown stays unknown.

## Migration and data

- No new tables. `character_references` changes meaning from "last legacy checkpoint" to "accepted pipeline evidence". Rebuild it for every book at migration time from accepted versions. Books with no pipeline history keep an empty set.
- `character_observations` stays as retained history. As built, section 1 does not write to it; see section 1. Legacy rows can be deleted with the phase-engine data (owner decision D3: "kill it and build fresh").

## Tests (offline)

- Accepting discovery or profiles fills references. Rollback removes the rolled-back evidence. Invalid spans are dropped.
- Volume 2's profile context includes volume 1's accepted evidence only through confirmed links and only from earlier positions. Rollback in volume 1 removes it and marks volume 2 stale.
- A series run with memory completes across two books without a fingerprint stop. A review gate pauses and resumes. A cancelled queued child still never starts.
- Link suggestions never create links without confirmation.

## Size

About 2 agent-sessions. Session 1 covers sections 1 and 2 plus tests. Session 2 covers sections 3 and 4 plus the series UI (pause/resume on review). Section 1 is also a prerequisite for removing the legacy engine without breaking Cast references.
