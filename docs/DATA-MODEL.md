# Data model and persistence contracts

This document describes current storage as of 2026-09-27. Read [architecture](ARCHITECTURE.md) for execution flow. The database is SQLite, with selected normalized relations and JSON bodies for evolving domain objects. It is not an ORM model, a vector store, or an event-sourced reconstruction of every application mutation.

## Authority and lifetimes

| Data | Authority | Lifetime |
| --- | --- | --- |
| Imported source bytes | Saved EPUB/TXT file | Retained independently of analysis. |
| Canonical reading text and IDs | Chapters/passages in `books.body`; matching source artifacts retain versions | Analysis must not rewrite source. |
| Current cast, scenes, performance, metadata | `books.body` | Mutable projection, with captured artifact versions for supported scopes. |
| Current enhanced take selection | `takes` | Mutable selection; overlaid onto book JSON when read. |
| Simple listening take history | `listening_takes` and WAV files | Independent retained history keyed by source/session recipe. |
| Accepted model results for fast reuse | `analysis_units` | Replaceable/rejectable cache; original accepted outputs retained in artifacts. |
| Analysis progress | `analysis_checkpoints`, `jobs` | Mutable resumable/status state; not the complete historical output store. |
| Current character references | `character_references` | Replaced with a published checkpoint. |
| Validated observations | `character_observations` | Append-only by repository convention, deduplicated by content. |
| Output/provenance history | `artifact_versions`, `artifact_dependencies` | Immutable, including when no longer current. |
| Current artifact selections | `artifact_heads` | Mutable pointers; removable without deleting history. |
| Spend/usage | `analysis_attempts`, `resource_operations`, events | Durable tracked measurements/reservations, with explicit unknowns. |
| Search | `passage_search`, `search_books` | Rebuildable derived index. |

No single table replaces the others. In particular, a checkpoint is not an immutable archive, an artifact head is not proof of semantic correctness, and a reported HTTP success is not proof that the returned annotations passed validation.

## Identifiers and source coordinates

Every import gets a book UUID. Chapter, scene, and passage identifiers are generated at import; passages are named `segments` in Python/JSON and “passages” in the UI. Character IDs belong to a book even when another book has the same name or coincidentally matching ID. Cross-book references must include `book_id`.

All chapter-local `start`/`end` source offsets use **Python Unicode code points**, zero-based and end-exclusive:

```python
chapter["text"][passage["start"]:passage["end"]] == passage["text"]
chapter["text"][reference["start"]:reference["end"]] == reference["quote"]
```

These are not UTF-8 byte positions, EPUB file offsets, or JavaScript UTF-16 indices. Consumers doing their own slicing must account for supplementary Unicode characters. Source anchors always identify a chapter or source artifact in addition to the offset pair.

Canonical text is extracted reading text after import normalization, including same-width whitespace in place of scene-break ornaments. Saved original bytes remain available for re-extraction and metadata repair. A chapter hash used by observations is SHA-256 of that chapter's UTF-8 text. The processing `source_hash(book)` is a digest of the ordered `(chapter_id, text)` pairs; it is not the chapter hash or the original-file hash.

Book structure repair accepts only an equal chapter count and exact canonical text per chapter. It changes metadata and transforms the saved checkpoint consistently; it does not renumber IDs or migrate source offsets to different prose. There is no general arbitrary-source-edit/rebase mechanism.

## Current book projection

`books.body` stores a JSON object with these principal fields. Optional analysis/version fields evolve through processing; inspect [importer.py](../bardic/importer.py), [analysis.py](../bardic/analysis.py), and [progressive.py](../bardic/progressive.py) before changing their shape.

| Field | Meaning |
| --- | --- |
| `id`, `title`, `author`, `source_name`, `created_at`, `revision` | Book identity, editable display metadata, safe original filename, and projection revision. |
| `structure_version` | Structure interpretation version, separate from book revision and artifact schema. |
| `chapters[]` | Ordered source containers with `id`, `index`, `title`, `text`, `kind`, `title_source`, `source_href`, optional `narrative_order`, and `logical_sections`. |
| `characters[]` | Book-local cast, including `narrator` and `unassigned`, with name/aliases, description/direction, evidence, voice choices, edit flags, and profile freshness/provenance fields. |
| `scenes[]` | Chapter-scoped scene IDs, title, summary, tone, direction, passage IDs, attributed character IDs, and optional edit flag. |
| `segments[]` | Passage ID, chapter/scene IDs, exact source offsets and text, kind, speaker, confidence, direction/cues/evidence, optional edit/provenance data, and presented audio selection. |
| `analysis` | Current overall analysis summary; detailed resumable state lives in checkpoint/cache tables. |
| `cover` | Thumbnail metadata/hash. Image bytes live in `book_covers`, not book JSON. |

`logical_sections` records navigation/headings within a retained EPUB container. Its existence does not mean each logical section is independently scheduled. Scene boundaries may be local drafts. A scene's attributed speakers and a character mention are not proof that the character is physically present.

`Store.book()` overlays `takes` onto each passage's `audio`. Reading raw `books.body` directly can therefore show an outdated embedded audio field. Use the repository's hydrated projection for production decisions. Import-only `_cover_data` is removed before JSON persistence and written as a bounded JPEG BLOB.

## SQLite table inventory

Definitions are in [store.py](../bardic/store.py), [series.py](../bardic/series.py), [library.py](../bardic/library.py), [processing.py](../bardic/processing.py), [artifacts.py](../bardic/artifacts.py), [listening.py](../bardic/listening.py), [resources.py](../bardic/resources.py), and [search.py](../bardic/search.py). `body`/`payload` columns below contain JSON unless otherwise stated. Most domain relationships are enforced in repository code; foreign-key enforcement being enabled does not imply every ID column has an SQL foreign-key constraint.

### Current state, jobs, and references

| Table | Key and columns | Contract |
| --- | --- | --- |
| `books` | PK `id`; `body` | Current domain projection. |
| `jobs` | PK `id`; `book_id`, `body` | Durable job status/progress/cancellation fields. Series parent jobs use a synthetic `series:<series_id>` scope and hold child IDs/book reservations. |
| `settings` | PK `id`; `body` | Saved preferences; excludes API keys. |
| `takes` | PK `(book_id, segment_id)`; `body` | Currently selected enhanced take metadata. Replacing a selection does not delete archived audio. |
| `analysis_checkpoints` | PK `book_id`; `fingerprint`, `body` | Current working book, stage/chapter completion and references, with compatibility unit state. One current checkpoint per book. |
| `character_references` | PK `(book_id, id)`; `character_id`, `chapter_id`, nullable `segment_id`, `body` | Current published references, indexed by book/character and book/chapter. |

Jobs can be queued, running, completed, failed, cancelled, interrupted, or budget-limited depending on the worker outcome. A restart interrupts active jobs/checkpoints rather than guessing that remote requests were never sent. Historical jobs remain useful status records but are not a substitute for attempt-level billing evidence.

### Progressive processing and measurements

| Table | Key and columns | Contract |
| --- | --- | --- |
| `analysis_units` | PK `(book_id, unit_key)`; `stage`, `source_hash`, `body` | Accepted result cache, indexed by book/stage/source. Payload includes actual provider/model and source range or character scope. |
| `analysis_attempts` | PK `id`; `book_id`, `run_id`, `body` | One row reserved before each HTTP attempt, then updated with response/uncertainty, usage, timing, and estimated cost. |
| `book_preprocessing` | PK `book_id`; `fingerprint`, `body` | Latest matching local census. Prior census artifacts can remain after replacement. |
| `pipeline_events` | PK `id`; `book_id`, `run_id`, `unit_key`, `stage`, `body` | Append events such as cache reuse/rejection and validation acceptance/rejection; connects validation state to attempts. |
| `resource_operations` | PK `id`; `book_id`, `run_id`, `stage`, `body` | Local and narration leaf-operation measurements, updated from running to completion/failure. Analysis requests remain in their own ledger to avoid double counting. |

An attempt's reserved tokens differ from reported tokens. `null` usage or cost is unknown, not zero. `cost_basis` distinguishes a conservative reservation from a usage estimate; pricing metadata records its date/source. Request and token allowances are counted per run, while the dollar guard includes all tracked analysis attempts for the book. New run IDs must not erase earlier spending.

### Series and library

| Table | Key and columns | Contract |
| --- | --- | --- |
| `series` | PK `id`; `name`, `created_at` | Named collection. |
| `series_books` | PK `book_id`; `series_id`, REAL `position`; unique `(series_id, position)` | One series per book and one supplied book per numeric position. Finite positions from 0 to 1,000,000; decimals allowed. |
| `series_characters` | PK `id`; `series_id`, `name`, `created_at` | Explicit series-wide identities. Names need not be unique. |
| `series_character_links` | PK `(book_id, character_id)`; `series_character_id`, `confirmed_at` | User-confirmed link from a book-local character to a series identity. |
| `character_observations` | PK `id`; `book_id`, `character_id`, `chapter_id`, `source_hash`, `body` | Retained exact evidence/interpretation records, indexed by book/character. |
| `library_archives` | PK `(kind, entity_id)`; `archived_at`; kind `book` or `series` | Reversible visibility/processing exclusion, not deletion. |
| `book_covers` | PK `book_id`; `media_type`, `width`, `height`, `sha256`, BLOB `body` | Current safe thumbnail, at most 240×360 pixels. |
| `series_volume_slots` | PK `(series_id, position)`; `title`, `status`, `created_at`; status `missing` or `planned` | Explicit absent volume marker; no book, source text, or synthetic observations. |

Observation IDs hash their content, excluding the new recording timestamp. Replaying a checkpoint uses `INSERT OR IGNORE`; a changed interpretation becomes a distinct observation. Retention requires the current committed chapter, character, exact source span, and quote to match. Kinds are `profile_evidence`, `dialogue`, and `mention`.

Current references can be replaced or cleared without deleting observations. Later analysis queries observations only through confirmed identity links, earlier reading positions, active books/series, and a still-matching chapter hash and quotation. Mentions are excluded from earlier-volume profile context. Context is bounded and fingerprinted; it does not retrieve every historical observation indiscriminately.

Moving a book to another series clears its current character links, while historical observations/artifacts remain. Assigning an actual book to a missing/planned position removes that placeholder. Archiving a series retains books, membership, links, originals, and audio. Archiving a book hides it from ordinary library results and later-book context. Direct reads can still find removed items for restoration. Removing a placeholder deletes only that placeholder, not an actual book.

### Artifact history and current selections

| Table | Key and columns | Contract |
| --- | --- | --- |
| `artifact_versions` | PK `id`; `book_id`, `kind`, `logical_key`, `label`, `stage`, nullable `provider`/`model`, `schema_version`, `legacy_provenance`, `payload`, `payload_bytes`, JSON `dependencies`, `created_at` | Immutable retained version. |
| `artifact_heads` | PK `(book_id, kind, logical_key)`; `artifact_id` FK to versions, `updated_at` | Current version for a logical scope. |
| `artifact_dependencies` | PK `(artifact_id, dependency_id)`; both FKs to versions | Immutable verified lineage edges; indexed in reverse for input lookup. |

`record()` computes `artifact_<sha256>` from canonical JSON containing book/scope, kind, stage, schema, producer, legacy flag, payload content, and sorted unique dependency IDs. It excludes display label and operational timestamps (`created_at`, `updated_at`, `recorded_at`, `checked_at`, `completed_at`, `exported_at`) from content identity. Re-recording the same identity retains the first payload/metadata and selects that version as current.

SQL triggers reject update/delete of versions and dependency edges and replacement of an existing version. Only current-head pointers may change or disappear. Dependencies must already exist; normal writes consequently build an acyclic graph of retained inputs. Cross-book dependencies are permitted and exposed with their owner book IDs. The DAG describes known lineage, not a generic execution scheduler.

Principal artifact kinds are:

| Kind / logical scope | Payload and dependency meaning |
| --- | --- |
| `source` / chapter ID | Canonical chapter text and its hash; title metadata is separate. A title-only repair leaves source content identity stable. |
| `structure` / `book` | Book display metadata and chapter structure, depending on source versions. |
| `scene_map` / chapter ID | Scenes and passage IDs/spans/attributions/directions with verified source anchors. Passage text is referenced, not redundantly stored per passage artifact. |
| `character_profile` / character ID | Current profile, evidence and freshness fields. A generation dependency is attached only when the stored produced profile can be verified. Edited profiles do not claim unverified model lineage. |
| `voice_assignment` / character ID | Voice/system voice choice, separate from semantic profile. |
| `audio_take` / passage ID | Enhanced take metadata and a verified source dependency where possible. It does not claim today's mutable directions generated an old take. |
| `census` / `book` | Local preprocessing result. |
| `analysis_input` / unit key | Effective request recipe plus actual retained upstream artifact dependencies. |
| `analysis_output` / unit key | Accepted result, linked to the recorded recipe when known. |
| `analysis_rejection` / unit key | Rejected structured result, producing attempt ID, and safe validation context, linked to its actual request input. |
| `character_observation` / observation ID | Exact observation with a source dependency only when hash/span/quote can be verified. |
| `series_context` / `book` | Snapshot of explicit membership and confirmed links, including their removal. |
| `series_run` / parent job ID | Retained collection run state for each participating book. |
| `library_state` / book or series scope | Archive/restore visibility state, captured for affected books. |

Current snapshots and model results are different kinds on purpose. A scene-map snapshot can exist immediately after import without successful semantic directing. Artifact count therefore does not mean a stage is complete. Audio bytes and simple-listening take records also have their own storage; not every application datum is an artifact.

When a scene/chapter/profile/audio selection disappears from the current projection, capture removes the corresponding current head only. Historic versions and dependencies remain. Legacy backfill preserves currently available book projections, takes, accepted units/checkpoint units, observations, census, and membership. It marks unknown provenance and never reconstructs prompts or outputs that were already lost.

### Listening and search

| Table | Key and columns | Contract |
| --- | --- | --- |
| `listening_sessions` | PK `(book_id, id)`; `body` | Deterministic single-narrator configuration: schema, book, provider, voice, model. |
| `listening_takes` | PK `id`; `book_id`, `session_id`, `segment_id`, `recipe`, `asset_id`, `body` | Retained take metadata indexed by book/session/passage/recipe; update/delete triggers guard existing rows. |
| `search_books` | PK `book_id`; `fingerprint` | Fingerprint of the currently indexed source/passages. |
| `passage_search` | FTS5: unindexed `book_id`, `chapter_id`, `passage_id`; indexed `text`; tokenizer `unicode61` | Rebuildable literal-word passage index. SQLite also creates its internal FTS shadow tables. |

A simple listening recipe includes the source/session/passage identity and underlying audio fingerprint. It deliberately excludes enhanced cast and stage direction. Before rendering or reuse, the exact source slice must match. Audio results may be retained after Stop if the in-flight generation completed, but that does not automatically resume playback.

Search only indexes valid anchored passages. Queries become quoted literal words joined with AND; result rank is lexical relevance, not identity confidence. The index is rebuilt when its source/passage fingerprint changes. If SQLite lacks FTS5, search reports unavailable while the rest of the library remains usable.

## Dependency and freshness contracts

An accepted analysis unit's cache key includes the effective prompt, schema, output cap, system instruction, provider/model, adapter/pipeline/validator versions, and source locator. Its row also carries the whole-book source fingerprint. Current discovery ranges can reuse a previously validated result even when the selected provider changes, retaining original producer metadata. Profile and direction reuse depends on the concrete evidence/cast/context used in their generated request recipes.

Lineage IDs and effective-input equality answer different questions: two equivalent inputs may be reusable even when their history differs, but reused output must not be relabeled as produced by the new provider. Artifact dependencies record the actual known source of a saved result.

| Change | Current effect |
| --- | --- |
| Chapter title/kind repair with unchanged canonical text | Preserves source IDs/spans and existing results; updates structure/checkpoint metadata and may change default eligible section selection. |
| Additional discovery evidence or changed profile inputs | Alters affected profile requests/freshness; old outputs remain retained. |
| Cast/profile/performance change | Can make direction/audio recipes stale; reviewed edits remain authoritative. |
| Voice/provider/model change | Changes enhanced audio recipe; existing byte-addressed assets remain on disk. |
| Enhanced cast/direction edit | Does not alter a simple single-narrator session recipe. |
| Membership/identity link/earlier evidence change | Changes bounded series context and downstream requests that consume it. |
| Invalid saved unit | Rejects reuse and removes its fast-cache row; immutable output history remains. |
| Archive/remove | Changes active visibility/context and processing eligibility; retains data and files. |

There is no automatic universal invalidation traversal over every artifact edge. Each pipeline stage computes its relevant recipe or freshness state. Any new cache must specify effective inputs, source validation, producer provenance, and failure retention explicitly.

## Transactions, migrations, and concurrency

`Store.connect()` enables SQL foreign keys; initialization sets WAL. An application `RLock` serializes mutation sections. Multiple connections are used, with a 30-second busy timeout. A process-level data-root lock is acquired by the server before initializing the store. It prevents a second server from interpreting another process's active work as a restart.

Important write boundaries:

- `Store._save_book()` captures missing legacy state before replacement, persists cover data, updates book/current takes, then captures new projections in one transaction.
- `Store.commit_analysis()` saves projection and checkpoint/current references together; observation retention and artifacts participate in that publication.
- `ProcessingStore.save_unit()` saves the input/output artifacts and accepted fast-cache row together, before publication callbacks.
- `Store.save_take()` preserves previous/new selected take metadata without recapturing every passage for each audio write.
- Provider requests run outside long SQLite transactions. Reservations are committed before the request.
- Audio file publication is atomic separately from SQLite. A validated asset can survive without a selected database pointer if a later step fails; there is no filesystem/database distributed transaction or automatic orphan cleanup.

Ordinary worker jobs, bounded parallel discovery, and collection reservations are coordinated by `Runtime` and the series coordinator. Mutation endpoints check active work; the database alone does not enforce these scheduling rules. Cancellation and restart preserve completed units and assets, while uncertain remote work remains visible rather than silently replayed.

Migration currently uses additive table/index/trigger initialization and targeted legacy backfill. Some optional tables are initialized on first repository use. There is no migration-number table, `PRAGMA user_version` protocol, or tested downgrade path. Artifact schema versions and request recipe versions are payload/behavior versioning, not database migration numbers. A maintenance reader must also know that constructing `Store` updates interrupted jobs/checkpoints; use a deliberate read-only SQLite connection for a truly non-mutating inspection.

## Files, exports, and backups

New libraries default to `.bardic/`, configurable with `BARDIC_DATA_DIR` (legacy alias `SPINTAILS_DATA_DIR`). With neither variable configured, an existing `.spintails/` directory is reused in place if `.bardic/` is absent. The entry point loads the project-root `.env`; an existing shell environment takes precedence even across these aliases. A relative data root resolves from the process working directory. The product rename moves no files and changes no database schema; see [upgrade details](OPERATIONS.md#upgrading-from-spin-tails).

```text
<data-root>/
  library.sqlite3
  library.sqlite3-wal       # May exist while SQLite is using WAL
  library.sqlite3-shm       # May exist while SQLite is using WAL
  server.lock              # Process lock
  originals/<book-id>/source.epub  # Or source.txt
  audio/<book-id>/<asset-id>.wav
  listen-audio/<book-id>/<asset-id>.wav
```

Older enhanced audio may use a recipe fingerprint as its filename; current byte-addressed takes use a SHA-256 asset ID. Thumbnail bytes are inside SQLite. Export ZIPs are built in temporary directories and removed after their response. A manually created `backups/` folder may exist, but the application has no automatic backup scheduler.

Library storage reports measure original/enhanced/simple-audio files. Per-book database figures are JSON/BLOB payload attribution, not physical SQLite page allocation. Shared database/WAL/SHM size is reported separately; indexes, free pages, and shared metadata cannot honestly be divided into exact book file sizes.

### Current projections and artifact snapshots

An artifact snapshot captures a supported scope at a point in its content history. A collection of snapshots is useful for reuse and inspection but is not guaranteed to reconstruct every job, preference, thumbnail, or filesystem object. Current reader JSON and immutable historical inputs must both be retained when exporting reusable knowledge.

### Portable analysis bundle

The manifest's format identifier remains `spintails-analysis` with schema version `1`. It is a retained interchange contract, independent of the Bardic display/package name. Existing exports and consumers do not require a format migration for the rename.

`/api/books/{book_id}/analysis-export` includes:

- `manifest.json`: format/schema, coordinate system, scope, and external book dependencies.
- `book.json` and `story-map.json`: current canonical source/projection and typed containment/attribution graph.
- `artifacts.jsonl`: every retained version for the selected book plus transitive dependency artifacts from other books.
- `observations.json`, `references.json`, `series.json`: evidence and explicit identity context.
- `analysis-attempts.json`, `pipeline-events.jsonl`: processing provenance and validation events.
- Resource operations and listening session/take metadata when their tables exist, plus a format README.

The bundle contains source text and may contain earlier-book input evidence through dependencies. It excludes credentials, settings, original EPUB/TXT binaries, cover BLOBs, and all WAV files. Its audio asset IDs refer to separately stored media. There is no implemented bundle-import/restore workflow, so this is an interoperable analysis export, not a complete restorable backup.

### Audiobook bundle

The separate audiobook export contains current selected enhanced takes, `production.json`, source chapter text, and `timeline.json`. It assembles a chapter WAV only when every required passage has valid audio and includes individually completed takes for partial chapters. Timings identify passage boundaries; missing passage IDs are explicit. It does not export the independent simple-listening library, perform word alignment, or package M4B.

### Full backup boundary

A recoverable local backup needs a consistent SQLite snapshot **and** the originals and both audio trees. A raw copy of only `library.sqlite3` while WAL is active may omit committed work; use SQLite's backup facility or stop the server before taking a consistent filesystem copy. Preserve project environment configuration separately if needed; it is not in the library or portable export. Restoration procedures must be exercised independently because the application does not yet provide a restore tool.

## Constraints for future changes

Preserve code-point offsets and exact quotes; do not silently substitute regenerated text for imported source. Scope character IDs by book and require explicit cross-book identity. Preserve accepted paid results before changing projection logic. Do not delete artifact history to make a cache appear fresh. Keep unknown cost/provenance visibly unknown. Version changed request and audio recipes, and record only dependency links that can be established from actual saved inputs. Add explicit migration/backup handling before introducing destructive schema or source transformations.
