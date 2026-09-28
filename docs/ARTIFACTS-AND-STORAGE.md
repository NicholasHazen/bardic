# Reusable artifacts and local storage

Implemented 2026-09-27. The purpose is to preserve expensive interpretation as reusable, inspectable data, separate from the currently selected reader presentation.

## Storage decision

Keep embedded SQLite as the authoritative store. It already holds books, jobs, analysis units, references, series identities and request accounting. Added immutable artifact versions, dependency edges, current selections and unit events. Keep original ebooks and WAV files on disk; reference them from the database. Add SQLite FTS5 for lexical passage retrieval, without model calls or a second service.

Relational columns handle identities, scopes, stages and joins; JSON payloads hold evolving analysis formats. A separate graph database is unnecessary for the present chapter/scene containment graph and artifact dependency graph. SQLite can traverse edge tables with recursive CTEs. The export uses that to include transitive inputs from earlier books.

A vector index is a future derived index, not an authority for character identity or evidence. Add it when measured semantic retrieval needs exceed lexical search and the existing bounded evidence sampler. Scope by series, book reading order and source version before top-k retrieval. Store embedding model/version/dimensions/text hash with vectors; a model change requires a separate index or re-embedding. Keep exact source references for every retrieved result. Do not interpret similarity as factual confidence.

`sqlite-vec` could keep vectors in the embedded database, but it is explicitly pre-v1 and can introduce breaking changes. LanceDB is another embedded option if a larger vector workload justifies maintaining a second store. Neither is installed or needed for this release.

## Outputs and consumers

| Artifact | Stored identity and provenance | Reuse |
| --- | --- | --- |
| Chapter source | Chapter ID, exact Unicode text, SHA-256 | All evidence validation, search, highlighting, future alignment |
| Structure | Original section IDs, order, corrected titles, section types, logical headings | Reader navigation, planning, export |
| Local census | Version, input fingerprint, frequency/spread/uncertainty metrics | Effort allocation and inexpensive coverage estimates |
| Character observation | Book/character/chapter IDs, exact span and quotation, source hash, observation type, model | Profiles across books, contradiction inspection, retrieval |
| Request recipe | Exact prompt/schema/system instruction, provider/model, limits, adapter/validator versions, recorded dependency IDs | Audit, cache identity, repair diagnosis |
| Accepted output | Validated structured result and producing attempt | Resumable discovery, profiles and directing |
| Rejected structured output | Candidate result and validation error, linked to its input | Diagnosis and repair only; never accepted knowledge |
| Character profile | Derived description/direction, aliases, evidence, currency/provisional state | Casting and performance direction |
| Series context | Membership, reading order and explicitly confirmed identity links | Earlier-volume context, historical link decisions |
| Scene map | Scene/passages, source spans, attributed speakers, directions, cues | Performance planning, reader, narration, future word alignment |
| Voice assignment | Book-local character ID and provider voice choices | Casting independent of character interpretation |
| Audio take | Render recipe hash plus separate WAV byte hash | Reuse valid takes, retain alternative performances, audiobook export |

The importer’s chapter text remains the canonical coordinate space. Offsets are zero-based Python Unicode character positions with an exclusive end, not UTF-8 bytes or JavaScript UTF-16 indices. Every reusable source anchor includes its chapter source artifact or source hash. A title repair does not rewrite the source text.

## Series knowledge is additive

Book-local character IDs stay book-local. A confirmed series identity joins them across books. Processing a second volume appends its observations with its own source locations; it does not replace the first volume’s evidence. A profile is a versioned interpretation of selected observations, not the sole record of what was learned.

Earlier-book context is bounded, sampled across evidence, and limited to explicitly linked characters in strictly earlier reading-order positions. Future volumes are excluded. Source changes invalidate observations for current retrieval without deleting the historic observation. Conflicting observations stay separate. This leaves room for later temporal traits and explicit contradiction resolution without reconstructing lost evidence.

Mentions, attributed dialogue and profile evidence are different reference types. None alone establishes physical presence. The current scene map exposes attributed speakers; it does not claim a fully resolved world model of people, places and time. Richer scene-presence and timeline analysis will require its own evidence-backed artifacts.

## Versioning and retries

`artifact_versions` is append-only; SQL triggers reject UPDATE, DELETE and replacement. Content-addressed IDs deduplicate identical payloads/producer/dependency versions; operational timestamps do not create new versions. `artifact_heads` records the selected version for a logical object. Removing a current scene or changing an assignment retires its head, not its history.

`artifact_dependencies` records existing artifact IDs. Retaining a current projection does not invent the historic prompt that produced it. Legacy outputs are marked when provenance is incomplete. Lost outputs from versions of the app before this change cannot be recovered.

The derived `pipeline_units` table is the fast resumable cache; libraries the removed Classic engine wrote to also keep its `analysis_units` cache until [stage 4](CLASSIC-REMOVAL.md), whose rows the legacy backfill retains as artifacts. Saving a cache entry also appends an immutable output version. Request inputs are retained before the provider call; transport attempts, validation outcomes and cache reuse are separately recorded. A successful HTTP response is not an accepted analysis result. Rejected structured results are inspectable but not used as facts. Invalid cached results are retired from the cache and regenerated; each new attempt is reserved and recorded first.

New WAV files are identified by their bytes independently of the generation recipe. Forcing a different performance with the same voice and instructions therefore preserves the previous take. Legacy recipe-named WAVs remain readable. History records selected-take metadata; automatic storage pruning and a full take-comparison editor are future work. Copies of the database alone are not a complete media backup: preserve the original and audio directories too.

## Visibility and portability

Details → Analysis explorer shows source import, series memory, one card per analysis step (accepted and total scopes, out-of-date results and versions waiting for review, the same numbers as the Analyze tab), voice assignments, narration, alignment and export readiness. Counts are stage-specific, and each card's saved-result count includes Classic-era history. Word alignment is visibly planned. Current timing follows passage boundaries.

The explorer includes stage/type/current/history filters, paginated artifact inspection, dependency IDs, jobs, provider attempts and validation, per-unit cache/retry events, chapter/scene navigation and explicit lexical passage search. These controls do not start paid work. The activity preview is bounded to recent records; the analysis export includes all recorded events/attempts.

`GET /api/books/{id}/analysis-export` produces a ZIP with:

- `manifest.json`: format/schema, coordinate conventions and limitations.
- `book.json`: complete current source/reader projection, including chapters, passages, scenes and cast.
- `story-map.json`: typed nodes, edges, attributed speakers and source references.
- `artifacts.jsonl`: every retained version for this book plus transitive input artifacts from other books.
- `observations.json`, `references.json`, `series.json`: append-only observations, current references and explicit identity mappings.
- `analysis-attempts.json`, `pipeline-events.jsonl`: usage and execution/validation history.
- `resource-operations.json`, `listening-sessions.json`, `listening-takes.json`, `listening-chunks.json`: per-book operation and simple-listening metadata when those tables exist; these do not include the simple audio files.

No narration is required. The bundle excludes credentials and audio binaries; the separate audiobook export includes current playable takes. There is no bundle import/restore UI yet. Consumers can use the documented JSON directly, and the standalone source/graph remains usable if providers change.

The domain graph (book → chapter → scene → passage, references and speakers) differs from the execution graph (request → input artifacts → validated output). Both are exported. Ordinary book jobs use the local single-worker runner; series runs use a separate coordinator that runs one book's pipeline run at a time, in reading order. This is not a distributed workflow engine and does not automatically schedule every invalidated descendant. See [architecture](ARCHITECTURE.md) for the current execution model.

## Primary references

- [SQLite as an application file format](https://sqlite.org/appfileformat.html) — embedded storage and portability.
- [SQLite FTS5](https://sqlite.org/fts5.html) — lexical matching, BM25 ranking and index maintenance. Lower BM25 rank is better; it is not a confidence score.
- [SQLite recursive CTEs](https://sqlite.org/lang_with.html) — graph traversal without a separate graph service.
- [SQLite JSON functions](https://sqlite.org/json1.html) — flexible payload storage; JSONB does not replace indexed relational keys.
- [SQLite backup API](https://sqlite.org/backup.html) — consistent snapshots while an app is running.
- [W3C PROV overview](https://www.w3.org/TR/prov-overview/) — conceptual distinction between data entities, production activities and derivation. This implementation borrows that distinction; it does not claim PROV serialization compliance.
- [sqlite-vec status](https://github.com/asg017/sqlite-vec) and [filtering restrictions](https://alexgarcia.xyz/sqlite-vec/features/vec0.html) — evaluate before committing to an extension.
- [LanceDB embedded database](https://docs.lancedb.com/faq/faq-oss) and [filtering](https://docs.lancedb.com/search/filtering) — possible later derived vector store.
- [Embedding model migration](https://www.mongodb.com/docs/voyageai/tutorials/migrate-embedding-model/) — model compatibility and index migration must be explicit.
