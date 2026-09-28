# Chapter analysis and character references

Implemented September 27, 2026 after a real-book analysis stopped on an invalid evidence quotation in its first discovery request. The original failed response was not retained, so its exact mismatch cannot be reconstructed. The former pipeline required a literal substring match and saved book annotations only after the complete run.

## Current processing

In **Production studio → Process the story in stages**, preview and run `scan`, `profiles`, `direct` or `full`. The API defaults to `scan`; it does not silently run the entire pipeline. Whole-book scans use eligible narrative/recap sections, while explicit source-section selection is supported. Discovery saves source-anchored observations before profile refinement. Profile effort uses bounded, varied evidence gathered across the supplied book and explicitly linked earlier volumes. Directing uses known cast IDs and bounded source/passage batches; a completed chapter publishes its scene map and annotations together. No analysis stage generates audio automatically.

The progressive runner in [progressive.py](../bardic/progressive.py) separates cheap discovery from detailed profiles/direction. Discovery uses contiguous source chunks capped at 24,000 characters. Direction uses its own smaller batches and bounded cast context. Read [the progressive design](PROGRESSIVE-ANALYSIS-PLAN.md), [data model](DATA-MODEL.md) and [API guide](API.md) for the current contracts. Complete coverage is separate from current profile refinement and is not a guarantee of perfect interpretation.

The earlier [staged runner](../bardic/staged_analysis.py) remains for compatibility and local chapter drafts. Its approximately 10,000-character/70-passage batches and combined discovery/profile/direction flow explain historical checkpoint records; they are not the current progressive scan settings.

The local heuristic provider also respects chapter selection and saves chapter results. It makes no model requests and does not perform the cloud profile reconciliation pass.

## Evidence and profiles

Quotes must anchor to immutable source text. Comparison tolerates whitespace, canonical Unicode, curly quotation marks, dash typography, ellipsis typography, and soft hyphens. It also tolerates one quotation mark the model added at either edge of an excerpt that starts or ends inside dialogue, provided the rest is at least two words and matches exactly; the added mark is not kept. Accepted quotes are replaced with the exact original source slice; this never edits book text. Spelling changes, paraphrases, inserted ellipses, and concatenated quotations are rejected. Reconciled profile quotes must fit within one supplied evidence excerpt, not across artificial joins between snippets.

An evidence validation failure triggers at most one corrective model response for that request. If it still fails, the job stops with chapter/stage and quotation diagnostics. Unsupported evidence is never silently accepted or dropped. Transport retries retain the existing provider adapter's bounded behavior; evidence correction is a separate, single attempt.

Character references are stored in SQLite with stable character/chapter/segment IDs, Python Unicode source offsets, the exact quote, kind, provider/model, and attribution confidence where available. There are three kinds:

- **Profile evidence:** a source quotation supporting a chapter observation, with its proposed character description and delivery note.
- **Mention:** an explicit name/alias match. This does not establish that the character is physically present in a scene. Ambiguous shared aliases are skipped.
- **Dialogue:** a passage assigned to that character, carrying speaker confidence and provenance.

Name mentions can be indexed across the local book once a character is known, without sending those other chapters to a provider. References are source locations, not a promise that every pronoun, appearance, or character trait has been identified. Open **References & appearances** on a cast card to jump to the corresponding passage. Profiles remain editable drafts.

## Persistence and recovery

`analysis_checkpoints` stores the latest input fingerprint, provider/model, baseline snapshot, validated unit outputs, and chapter progress. `character_references` stores indexed reference records by book, character, and chapter. Since 2026-09-28 the step pipeline rebuilds it from accepted evidence at its next sync, replacing what a phase checkpoint wrote ([evidence projection](ANALYSIS-PIPELINE.md#evidence-projection)). Existing library tables are migrated additively on startup. A published chapter and its checkpoint/references commit in one transaction.

The active checkpoint includes source spans/text, provider/model, pipeline version, and reviewed annotations. Resume reuses validated discoveries and compatible profile/direction outputs. Changing a profile can invalidate dependent direction without discarding discovery. The current progressive runner also stores accepted units independently in `analysis_units`, observations in series tables and immutable artifact versions with actual input dependencies. Replacing the latest checkpoint does not erase that reusable history. `resume:false` bypasses applicable reuse for an explicit new run; it does not reset cumulative tracked book spending. See [artifacts and storage](ARTIFACTS-AND-STORAGE.md) for the current retention design.

Cancellation, provider failure, and server restart preserve validated requests and already-published chapter work. The current invalid or unfinished request is not cached. The progress panel distinguishes discoveries from completed direction. Book analysis remains `partial` until every chapter is directed under the current profile context.

API:

- `POST /api/books/{book_id}/analysis-plan` previews the chosen phase without generation.
- `POST /api/books/{book_id}/analyze {provider?, chapter_id?, resume?, phase?, limits?}`; omitted chapter uses whole-book scope for the chosen phase, `resume` defaults true and `phase` defaults `scan`.
- `GET /api/books/{book_id}/analysis` returns progress only, without source snapshots or model responses.
- `GET /api/books/{book_id}/characters/{character_id}/references` returns the character's recorded source references.

See [validation](VALIDATION.md) for regression coverage and dated live results. Personal-library preview captures are local validation files and are intentionally excluded from Git.
