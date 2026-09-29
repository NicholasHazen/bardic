# Data model and persistence contracts

This document describes current storage as of 2026-09-28. Read [architecture](ARCHITECTURE.md) for execution flow. The database is SQLite, with selected normalized relations and JSON bodies for evolving domain objects. It is not an ORM model, a vector store, or an event-sourced reconstruction of every application mutation.

## Authority and lifetimes

| Data | Authority | Lifetime |
| --- | --- | --- |
| Imported source bytes | Saved EPUB/TXT file | Retained independently of analysis. |
| Canonical reading text and IDs | Chapters/passages in `books.body`; matching source artifacts retain versions | Analysis must not rewrite source. |
| Current cast, scenes, performance, metadata | `books.body` | Mutable projection, with captured artifact versions for supported scopes. |
| Current enhanced take selection | `takes` | Mutable selection; overlaid onto book JSON when read. |
| Simple listening take history | `listening_takes` and WAV files | Independent retained history keyed by source/session recipe. |
| Voice example history | `voice_preview_requests`, `voice_preview_takes` and WAV files | Immutable book-scoped effective requests and independent audition takes. |
| Equivalent simple speech lookup | `listening_synthesis_cache` | Rebuildable content-key index pointing to actual retained takes; source identity remains in take history. |
| Simple listening multi-passage chunks | `listening_chunks` and WAV files | Immutable version-1 records of one request covering an exact chapter slice, with estimated passage clips. Passage audio is projected from chunks whose source still matches. |
| Saved performances | `performances` | Mutable label records (name, chapters, archive flag, latest job) that point at retained audio; never the audio itself. |
| Cast performance take history | `performance_takes` and WAV files | Immutable passage takes keyed by performance, passage and source key. Independent of the Studio `takes` selection. |
| Validated step results for fast reuse | `pipeline_units` | Replaceable cache keyed by exact request identity; outputs retained in artifacts. The removed Classic engine's `analysis_units` cache was retained as artifacts and dropped by migration `classic_removal_v1` ([stage 4](CLASSIC-REMOVAL.md#stage-4-what-was-dropped)). |
| Analysis progress | `pipeline_runs`, `pipeline_step_runs`, `jobs` | Mutable resumable/status state; not the complete historical output store. |
| Current character references | `character_references` | Current-book projection of accepted discovery, profiles and directing evidence plus cast-name mentions, rebuilt by pipeline sync and decisions ([evidence projection](ANALYSIS-PIPELINE.md#evidence-projection)). Later volumes' series context reads it ([series memory](ANALYSIS-PIPELINE.md#series-memory)). Rows the removed Classic engine wrote stay for a book with no accepted evidence. The references API computes the same projection from the current book on each read, in a transaction it rolls back. |
| Validated observations | `character_observations` | Append-only history table, deduplicated by content, written only when `RETAIN_OBSERVATIONS` is on (it is off). The removed Classic engine's rows were retained as `character_observation` artifacts and deleted by migration `classic_removal_v1`; the artifact is now the proof that a Classic-written reference matches its chapter text. |
| One-time data migrations | `schema_migrations` | One row per migration ID with its status and counts; see [transactions and migrations](#transactions-migrations-and-concurrency). |
| Output/provenance history | `artifact_versions`, `artifact_dependencies` | Immutable, including when no longer current. |
| Current artifact selections | `artifact_heads` | Mutable pointers; removable without deleting history. |
| Spend/usage | `analysis_attempts`, `resource_operations`, events | Durable tracked measurements/reservations, with explicit unknowns. |
| Search | `passage_search`, `search_books` | Rebuildable derived index. |
| Operational diagnostics | `diagnostic_events` | Rotating local troubleshooting log; newest 5,000 events, not retained analysis/take provenance. |

No single table replaces the others. In particular, a resumable cache is not an immutable archive, an artifact head is not proof of semantic correctness, and a reported HTTP success is not proof that the returned annotations passed validation.

## Identifiers and source coordinates

Every import gets a book UUID. Chapter, scene, and passage identifiers are generated at import; passages are named `segments` in Python/JSON and “passages” in the UI. Character IDs belong to a book even when another book has the same name or coincidentally matching ID. Cross-book references must include `book_id`.

All chapter-local `start`/`end` source offsets use **Python Unicode code points**, zero-based and end-exclusive:

```python
chapter["text"][passage["start"]:passage["end"]] == passage["text"]
chapter["text"][reference["start"]:reference["end"]] == reference["quote"]
```

These are not UTF-8 byte positions, EPUB file offsets, or JavaScript UTF-16 indices. Consumers doing their own slicing must account for supplementary Unicode characters. Source anchors always identify a chapter or source artifact in addition to the offset pair.

Canonical text is extracted reading text after import normalization, including same-width whitespace in place of scene-break ornaments. Saved original bytes remain available for re-extraction and metadata repair. A chapter hash used by observations is SHA-256 of that chapter's UTF-8 text. The processing `source_hash(book)` is a digest of the ordered `(chapter_id, text)` pairs; it is not the chapter hash or the original-file hash.

Book structure repair accepts only an equal chapter count and exact canonical text per chapter. It changes metadata only; it does not renumber IDs or migrate source offsets to different prose. There is no general arbitrary-source-edit/rebase mechanism.

## Current book projection

`books.body` stores a JSON object with these principal fields. Optional analysis/version fields evolve through processing; inspect [importer.py](../bardic/importer.py), [analysis.py](../bardic/analysis.py), and the steps in [pipeline/steps/](../bardic/pipeline/steps/) before changing their shape. Books last analyzed by the removed Classic engine can also carry fields only it wrote, such as `profile_state` and `profile_provisional`.

| Field | Meaning |
| --- | --- |
| `id`, `title`, `author`, `source_name`, `created_at`, `revision` | Book identity, editable display metadata, safe original filename, and projection revision. |
| `language` | BCP 47 tag from the EPUB's first well-formed `dc:language` (`_` read as `-`), or `null` for TXT, an EPUB without one, and books imported before it existed. Metadata only: it chooses the default Mac voice menu and never changes canonical text or spans. Not refreshed by **Refresh metadata** yet. |
| `structure_version` | Structure interpretation version, separate from book revision and artifact schema. |
| `chapters[]` | Ordered source containers with `id`, `index`, `title`, `text`, `kind`, `title_source`, `source_href`, optional `narrative_order`, and `logical_sections`. |
| `characters[]` | Book-local cast, including `narrator` and `unassigned`, with name/aliases, description/direction, evidence, per-provider voice choices (`voices`), edit flags, and profile freshness/provenance fields. |
| `scenes[]` | Chapter-scoped scene IDs, title, summary, tone, direction, passage IDs, attributed character IDs, and optional edit flag. |
| `segments[]` | Passage ID, chapter/scene IDs, exact source offsets and text, kind, speaker, confidence, direction/cues/evidence, optional `speaker_check` (a dialogue passage's comparison with accepted BookNLP quote attribution: `source`, `result`, BookNLP's `speaker_id`/`speaker`, `tag_conflict`; absent when not checked, dropped when a person changes the speaker), optional `seed` for seeded narration providers, optional edit/provenance data, and presented audio selection. |
| `analysis` | Current overall analysis summary; detailed resumable state lives in the pipeline tables. |
| `former_names` (on characters) | Names replaced by a manual rename. Discovery resolves them to the same character; they are not aliases. |
| `edited_fields` (on characters, scenes, passages) | Field names whose value an edit through the review endpoints actually changed; an edit that changes nothing records nothing and keeps the revision. The [analysis pipeline](ANALYSIS-PIPELINE.md#acceptance-rollback-and-manual-edits) never overwrites a listed field. `"*"` marks an item edited before per-field tracking, which stays wholly locked. The demo's local draft reads only the boolean `edited`. Stored only: the book document the API presents omits `edited`, `edited_fields`, `metadata_edited`, `profile_input_key` and the legacy `voice`/`system_voice` fields. Each presented passage carries `manual_fields` instead: the passage fields a person set by hand, with `"*"` expanded. |
| `cover` | Thumbnail metadata/hash. Image bytes live in `book_covers`, not book JSON. |
| `pronunciations[]` | Optional pronunciation lexicon (absent when empty): `id` (`pr_<12 hex>`), `term`, `respelling`, `match_case`, optional `providers` (per-narrator respellings), `character_id` and `note`. Edited only through the pronunciation endpoints; analysis carries it through unchanged. It is a narration input, never a text change: the respelled text exists only in render recipes. |

`logical_sections` records navigation/headings within a retained EPUB container. Its existence does not mean each logical section is independently scheduled. Scene boundaries may be local drafts. A scene's attributed speakers and a character mention are not proof that the character is physically present.

`Store.book()` overlays `takes` onto each passage's `audio`. Reading raw `books.body` directly can therefore show an outdated embedded audio field. Use the repository's hydrated projection for production decisions. Import-only `_cover_data` is removed before JSON persistence and written as a bounded JPEG BLOB.

### Narration voice choices and seeds

Since 2026-09-27 a character stores `voices: {provider: {id, ...}}`, one entry per narration provider (`gemini`, `system`, `breeze`). New characters from import, analysis and the cast editor start with `{"gemini": {"id": ...}}`; the device-voice assignment adds `system`. Characters saved earlier keep their `voice` (Gemini) and `system_voice` (macOS) fields, and `audio.voice_selection()` reads them whenever the map has no entry for that provider. Editing any voice choice through the API rewrites the character with a `voices` map and removes both legacy fields, leaving one stored source per provider. Presented books always include the normalized map. Because Gemini and device recipes contain only the voice string, both layouts produce identical fingerprints; `tests/test_narration_providers.py` pins pre-change fingerprints, listening session IDs and synthesis keys.

Each provider entry takes one of these forms:

| Value | Meaning |
| --- | --- |
| `{"library": "vl_…"}` | Follow a [library voice](#voice-library)'s **current** version. The Cast tab writes this form for Breeze and Gemini library voices. |
| `{"id": …}` | A direct provider voice: a Gemini built-in or project voice, a device voice, or a legacy field. |
| `{"id", "revision", "seed", "settings"?}` | A concrete Breeze pin made before the voice library existed; still accepted and resolved as-is. |
| absent | **Default**. Breeze: the Bardic default library voice in `narration_defaults.breeze`. Gemini: Kore. Device: the system default voice. |

Library references and the Breeze default are resolved to concrete provider voices by `Runtime.resolved_cast()` from SQLite only; recipes never see an unresolved reference and refuse one (`voice_selection()` raises) rather than falling back to a default voice. Resolved selections carry `library` and `version`; enhanced takes and Breeze voice examples made with a library voice record `voice_library: {id, version}`, and every Breeze take (including simple listening) records `voice_revision`. Simple-listening sessions pin the concrete voice only, so their takes do not name the library voice. The `voice_assignment` artifact captures the stored `voices` map (references, not their resolution); current-version and default changes are recorded in `voice_library_events` instead.

A concrete Breeze choice is `{id, revision, seed, settings?}`. `revision` is a SHA-256 over schema version, voice ID, kind, `created_at`, `instruction`, voice `settings`, reference transcript and the SHA-256 of the reference clip bytes. Labels, name, description and `updated_at` are excluded, so relabelling a voice does not invalidate audio. Only `cloned` voices are pinned; `designed` voices are listed as unusable because they change between requests. `seed` defaults to the voice's own seed, else 42. A passage `seed` overrides it for seeded providers; Gemini and device recipes ignore it. Setting a new passage seed is how a Breeze retake differs from the previous take, and like other performance edits it retires the currently selected take while retaining history.

A Breeze recipe adds `voice_revision`, `seed`, `settings` (validated temperature/cfg_scale/top_p/top_k overrides; no current API or UI sets them, so this is empty), the explicit `segmentation` sent to the server, `style` (the Breeze instruction built from performance notes) and `adapter_version` (1) to the shared recipe fields. Breeze take metadata may include `provider_timing` (`{schema_version:1, kind:"sentence", source:"breeze", offsets:"recipe_text_code_points", segments:[{char_start,char_end,start,end}]}`, kept only when every server offset resolves to the exact sent text, else `null`) and a `breeze` block with the server `request_id`, `timing_accepted` and any `vocal_event_markup` found in the source text.

Settings preferences store `breeze_url` and `breeze_catalog`, the last voice check (state, message, model, default voice, voices with revisions, checked URL and time). The catalog is persisted so pinned sessions, previews and cached audio still resolve after a restart while the server is offline; a failed check keeps the previous voices for the same URL. Voices Bardic creates or deletes are added to or removed from this saved catalog immediately. The optional Breeze key is held only in process memory or the environment. `narration_defaults` holds `{"breeze": "vl_…"}`; `gemini_voice_catalog` holds the last listing of the Google project's stored voices with `key_hash` (the first 12 hex digits of the key's SHA-256, never the key), state, message and time.

### Voice library

Voices belong to the whole data directory, not to a book. Tables are defined in [voice_library.py](../bardic/voice_library.py).

| Table | Key and columns | Contract |
| --- | --- | --- |
| `voice_library` | PK `id`; `provider`, `body` | Mutable voice record `{id: "vl_<16 hex>", provider: breeze|gemini, name, description, origin: designed|cloned|imported, current_version, created_at, updated_at, deleted_at, source: {book_id, character_id, character_name}|null, server_deleted?: [provider voice id]}`. Rows are never deleted (a trigger refuses DELETE); deletion sets `deleted_at`. `server_deleted` (absent in older records, read as empty) lists provider voices already deleted by a deletion that has not finished; the voice reports them as `missing`, and a retried deletion skips them. |
| `voice_library_versions` | PK `(voice_id, version)`; FK `voice_id`; `body` | Immutable version `{version, provider, provider_voice_id, revision, seed, made: designed|cloned|imported, recipe: {description, sample_text, preview_id, preview_seed, model?, language_code?, gender?}, audition: {asset_id, duration}|null, project, created_at, expires_at}`. UPDATE/DELETE triggers reject changes. |
| `voice_library_events` | PK autoincrement `id`; `body` | Append-only history: `created`, `version_added`, `current_changed` (with `previous`), `updated`, `server_voice_deleted` (one per provider voice, with `provider_voice_id`), `deleted` (with `server_deleted`), `default_changed`. UPDATE/DELETE triggers reject changes. |
| `voice_drafts` | PK `id`; `body` | Mutable design working state `{id: "vd_<16 hex>", provider, base_voice_id, context, name, description, sample_text, status: open|saved|abandoned, candidates[], saved?}`. Candidates hold `kind` (`breeze_preview`/`gemini_voice`), preview or provider voice ID, seed, description, sample text, local `asset_id`/duration, server expiry, `discarded`, and for Gemini the creating `project` key hash and model. |

Audition and candidate audio is copied to `voice-library/<sha256>.wav`, validated and normalized to mono 16-bit 24 kHz WAV, and never overwritten. This keeps what was heard after Breeze previews expire (24 hours) and after Gemini voices expire (one year).

Breeze versions are separate server voices with generated IDs `bardic-<8 hex>` and labels `bardic_voice` (the library ID) and `bardic_version`. Saving a Breeze candidate uploads the locally kept auditioned clip through the server's clone endpoint with the preview text as its transcript. The server re-encodes the stored reference (verified live on 2026-09-27: the reference bytes differ from the upload), so the version's `revision` hashes the reference the server actually keeps, read back after creation. Imported versions (`made: imported`) pin an existing server voice at its checked revision; a server-side change afterwards shows as `changed` and is refused at render time, and adopting it as a new version is not implemented. Gemini versions record the provider voice ID, the server's expiry time and `project`, the hash of the key that created them; a different key can neither use nor delete them.

## SQLite table inventory

Definitions are in [store.py](../bardic/store.py), [series.py](../bardic/series.py), [library.py](../bardic/library.py), [processing.py](../bardic/processing.py), [pipeline/repository.py](../bardic/pipeline/repository.py), [artifacts.py](../bardic/artifacts.py), [listening.py](../bardic/listening.py), [voice_previews.py](../bardic/voice_previews.py), [performances.py](../bardic/performances.py), [resources.py](../bardic/resources.py), [diagnostics.py](../bardic/diagnostics.py), [voice_library.py](../bardic/voice_library.py), and [search.py](../bardic/search.py). The inventory below covers 37 application tables, including the FTS5 virtual table and excluding SQLite's internal FTS shadow tables. `body`/`payload` columns below contain JSON unless otherwise stated. Most domain relationships are enforced in repository code; foreign-key enforcement being enabled does not imply every ID column has an SQL foreign-key constraint.

### Current state, jobs, and references

| Table | Key and columns | Contract |
| --- | --- | --- |
| `books` | PK `id`; `body` | Current domain projection. |
| `jobs` | PK `id`; `book_id`, `body` | Durable job status/progress/cancellation fields. Series parent jobs use a synthetic `series:<series_id>` scope and hold child IDs/book reservations. A parent paused for review stays `running` with `waiting_for_review`; its children carry `consent_fingerprint`, `context_pending` and `context_sources`. Once `status` is terminal, `status`, `message`, `error` and `resume_after` are never rewritten (`Store.update_job` drops them). Documents written before contract 0.2.0 use `mode` (pipeline), `limits` (series, listen_chapter), and series parents written by pipeline series runs before contract 0.3.0 use `mode` and `limits`; they are read as `scheduling`, `analysis_limits` and `speech_limits`, and rewritten with the new names on their next update. The series `plan_fingerprint` is stored but not returned by the API. |
| `settings` | PK `id`; `body` | Saved preferences; excludes API keys and server URLs that come only from the environment (`breeze_url` is the URL saved in Settings, `""` when none). |
| `takes` | PK `(book_id, segment_id)`; `body` | Currently selected enhanced take metadata. Replacing a selection does not delete archived audio. |
| `character_references` | PK `(book_id, id)`; `character_id`, `chapter_id`, nullable `segment_id`, `body` | Current references, indexed by book/character and book/chapter. Rows written by the evidence projection carry `step`, `version_id` (the accepted `step_output` artifact), `origin` and `projection`; their ID hashes their content. Each row passes the same exact-span check as observations. The rebuild digest, drop counts and the chapter hashes the rows were validated against (`sources`) live in `pipeline_state.body.evidence`. |

Jobs can be queued, running, completed, failed, cancelled, interrupted, or budget-limited depending on the worker outcome. A restart interrupts active jobs and pipeline runs rather than guessing that remote requests were never sent. Historical jobs remain useful status records but are not a substitute for attempt-level billing evidence.

### Analysis processing and measurements

| Table | Key and columns | Contract |
| --- | --- | --- |
| `analysis_attempts` | PK `id`; `book_id`, `run_id`, `body` | One row reserved before each HTTP attempt, then updated with response/uncertainty, usage, timing, and estimated cost. |
| `book_preprocessing` | PK `book_id`; `fingerprint`, `body` | Latest matching local census: a disposable derived cache that read-only views may write. Analysis runs and plan previews also retain it as a `census` artifact; prior census artifacts can remain after replacement. |
| `pipeline_events` | PK `id`; `book_id`, `run_id`, `unit_key`, `stage`, `body` | Append events such as cache reuse/rejection and validation acceptance/rejection; connects validation state to attempts. |
| `resource_operations` | PK `id`; `book_id`, `run_id`, `stage`, `body` | Local and narration leaf-operation measurements, updated from running to completion/failure. Analysis requests remain in their own ledger to avoid double counting. |

An attempt's reserved tokens differ from reported tokens. `null` usage or cost is unknown, not zero. `cost_basis` distinguishes a conservative reservation from a usage estimate; pricing metadata records its date/source. Request and token allowances are counted per run, while the dollar guard includes all tracked analysis attempts for the book. New run IDs must not erase earlier spending.

### Operational diagnostics

| Table | Key and columns | Contract |
| --- | --- | --- |
| `diagnostic_events` | PK `id`; `created_at`, `source`, `event`, nullable `book_id`, `body`; indexes by book and source/time | Newest 5,000 allowlisted local browser/server events. Rows are deliberately pruned; this is not an immutable provenance archive. |

The table and indexes are created additively when `DiagnosticRepository` is first instantiated; existing libraries need no source/take rewrite or destructive migration. Event bodies contain only validated operational IDs, operation enums, playback rate, HTTP/media error codes and, for server records, bounded provider/status enums. They exclude free-form messages, traces, URLs, source prose and credentials. Actual job records remain the place to inspect retained job errors; diagnostics link by IDs rather than copying error text.

Identical persisted source/event/body records are suppressed within two seconds. At most 120 client events are persisted per rolling minute across the local instance; server events are not subject to that client rate limit. The browser also suppresses identical submissions for ten seconds. Each accepted insert prunes rows beyond the newest 5,000. These events are available through the separate diagnostics API/download, not the portable analysis provenance bundle. Failed logging is best effort and cannot alter job or playback outcomes. Unrecorded or pruned events remain unavailable.

### Series and library

| Table | Key and columns | Contract |
| --- | --- | --- |
| `series` | PK `id`; `name`, `created_at` | Named collection. |
| `series_books` | PK `book_id`; `series_id`, REAL `position`; unique `(series_id, position)` | One series per book and one supplied book per numeric position. Finite positions from 0 to 1,000,000; decimals allowed. |
| `series_characters` | PK `id`; `series_id`, `name`, `created_at` | Explicit series-wide identities. Names need not be unique. |
| `series_character_links` | PK `(book_id, character_id)`; `series_character_id`, `confirmed_at` | User-confirmed link from a book-local character to a series identity. |
| `character_observations` | PK `id`; `book_id`, `character_id`, `chapter_id`, `source_hash`, `body` | Exact evidence/interpretation records, indexed by book/character. Empty unless `RETAIN_OBSERVATIONS` is on; the Classic engine's rows are `character_observation` artifacts since the Classic data drop. |
| `library_archives` | PK `(kind, entity_id)`; `archived_at`; kind `book` or `series` | Reversible visibility/processing exclusion, not deletion. |
| `book_covers` | PK `book_id`; `media_type`, `width`, `height`, `sha256`, BLOB `body` | Current safe thumbnail, at most 240×360 pixels. |
| `series_volume_slots` | PK `(series_id, position)`; `title`, `status`, `created_at`; status `missing` or `planned` | Explicit absent volume marker; no book, source text, or synthetic observations. |

Observation IDs hash their content, excluding the new recording timestamp. Writing the same observation again uses `INSERT OR IGNORE`; a changed interpretation becomes a distinct observation. Retention requires the current committed chapter, character, exact source span, and quote to match. Kinds are `profile_evidence`, `dialogue`, and `mention`.

Current references can be replaced or cleared without deleting observations. Since 2026-09-28 series context reads earlier volumes' **current references** (their accepted evidence), not this table, so a rollback in volume 1 changes what volume 2 reads ([series memory](ANALYSIS-PIPELINE.md#series-memory)). The evidence projection writes no observations (`RETAIN_OBSERVATIONS` is off); the removed Classic engine was the only writer, and its rows were retained as `character_observation` artifacts and deleted by the Classic data drop. A Classic-written reference (no `projection` field) counts in context only while its book has a current `character_observation` artifact with the same observation ID, source hash, quotation and chapter: the proof that it still matches the chapter text it was produced from. Each earlier-volume entry a profile request sends is retained as a `character_observation` artifact instead. Context uses only confirmed identity links, earlier reading positions, active books/series, and a still-matching chapter source and quotation. Mentions are excluded from earlier-volume profile context. Context is bounded and fingerprinted; it does not retrieve every accepted reference indiscriminately. See [stage 4](CLASSIC-REMOVAL.md#stage-4-what-was-dropped) for the drop.

Moving a book to another series clears its current character links, while historical observations/artifacts remain. Assigning an actual book to a missing/planned position removes that placeholder. Archiving a series retains books, membership, links, originals, and audio. Archiving a book hides it from ordinary library results and later-book context. Direct reads can still find removed items for restoration. Removing a placeholder deletes only that placeholder, not an actual book.

### Artifact history and current selections

| Table | Key and columns | Contract |
| --- | --- | --- |
| `artifact_versions` | PK `id`; `book_id`, `kind`, `logical_key`, `label`, `stage`, nullable `provider`/`model`, `schema_version`, `legacy_provenance`, `payload`, `payload_bytes`, JSON `dependencies`, `created_at` | Immutable retained version. |
| `artifact_heads` | PK `(book_id, kind, logical_key)`; `artifact_id` FK to versions, `updated_at` | Current version for a logical scope. |
| `artifact_dependencies` | PK `(artifact_id, dependency_id)`; both FKs to versions | Immutable verified lineage edges; indexed in reverse for input lookup. |
| `artifact_backfills` | PK `book_id`; `version`, `completed_at` | Books whose legacy data has been retained as artifacts, and at which backfill version. |

`record()` computes `artifact_<sha256>` from canonical JSON containing book/scope, kind, stage, schema, producer, legacy flag, payload content, and sorted unique dependency IDs. It excludes display label and operational timestamps (`created_at`, `updated_at`, `recorded_at`, `checked_at`, `completed_at`, `exported_at`) from content identity. Re-recording the same identity retains the first payload/metadata and selects that version as current.

SQL triggers reject update/delete of versions and dependency edges and replacement of an existing version. Only current-head pointers may change or disappear. Dependencies must already exist; normal writes consequently build an acyclic graph of retained inputs. Cross-book dependencies are permitted and exposed with their owner book IDs. The DAG describes known lineage, not a generic execution scheduler.

Principal artifact kinds are:

| Kind / logical scope | Payload and dependency meaning |
| --- | --- |
| `source` / chapter ID | Canonical chapter text and its hash; title metadata is separate. A title-only repair leaves source content identity stable. |
| `structure` / `book` | Book display metadata and chapter structure, depending on source versions. |
| `scene_map` / chapter ID | Scenes and passage IDs/spans/attributions/directions with verified source anchors. Passage text is referenced, not redundantly stored per passage artifact. |
| `character_profile` / character ID | Current profile, evidence and freshness fields. A generation dependency is attached only when the stored produced profile can be verified. Edited profiles do not claim unverified model lineage. |
| `voice_assignment` / character ID | Stored per-provider voice choices (`voices`, plus legacy `voice`/`system_voice` when present), separate from semantic profile. Library references are captured as references, not resolved versions. |
| `audio_take` / passage ID | Enhanced take metadata and a verified source dependency where possible. It does not claim today's mutable directions generated an old take. |
| `census` / `book` | Local preprocessing result. |
| `analysis_input` / unit key | Effective request recipe plus actual retained upstream artifact dependencies. |
| `analysis_output` / unit key | Accepted result, linked to the recorded recipe when known. |
| `analysis_checkpoint` / `book` | A saved checkpoint of the removed Classic engine (status, chapter progress, working copy, references) exactly as stored, retained by the Classic data drop before it dropped the table. Legacy provenance, no dependencies. |
| `analysis_rejection` / unit key | Rejected structured result, producing attempt ID, and safe validation context, linked to its actual request input. |
| `character_observation` / observation ID | Exact observation with a source dependency only when hash/span/quote can be verified. Also recorded (in the earlier book) for each earlier-volume entry a profile request sends, carrying its `step`/`version_id`/`origin`; the profile unit depends on it. |
| `series_context` / `book` | Snapshot of explicit membership and confirmed links, including their removal. |
| `series_run` / parent job ID | Retained collection run state for each participating book. |
| `library_state` / book or series scope | Archive/restore visibility state, captured for affected books. |
| `step_output` / `<step>:<scope>` | One analysis pipeline step's result for a book, chapter or character scope, with the accepted input versions it read. Candidates are recorded **without** selecting them (`record(select=False)`); the head is the accepted version and moves only on an accept decision. `origin` distinguishes model/local runs from `baseline`/`external` captures of existing work (legacy provenance). A profile version produced in a series book also records `series_inputs` (`{earlier book ID: digest of its accepted evidence versions and linked characters}`) for cross-book staleness; the key is absent otherwise, so other versions keep their identity. |

Current snapshots and model results are different kinds on purpose. A scene-map snapshot can exist immediately after import without successful semantic directing. Artifact count therefore does not mean a stage is complete. Audio bytes and simple-listening take records also have their own storage; not every application datum is an artifact.

When a scene/chapter/profile/audio selection disappears from the current projection, capture removes the corresponding current head only. Historic versions and dependencies remain. Legacy backfill preserves currently available book projections, takes, census, and membership. The removed Classic engine's units, checkpoint units, whole checkpoints (`analysis_checkpoint`) and observations were retained by migration `classic_removal_v1` before it dropped their tables. It marks unknown provenance and never reconstructs prompts or outputs that were already lost. Backfill runs once per book at app startup (recorded in `artifact_backfills`), never from a read-only view: GET routes create no artifacts.

### Analysis pipeline

| Table | Key and columns | Contract |
| --- | --- | --- |
| `pipeline_runs` | PK `id`; `book_id`, JSON body | One orchestrated request: steps, mode, per-step provider/model/gate snapshot, chapter scope, limits, job ID, outcomes. |
| `pipeline_step_runs` | PK `id`; `book_id`, `run_id`, `step_id`, JSON body | One execution of one step (the UI's "version"): configuration, accepted inputs read, unit counts, status, scope → `step_output` artifact IDs. Baseline/external captures are step runs with that origin. |
| `pipeline_decisions` | PK `id`; `book_id`, `step_id`, JSON body | Append-only accept/reject log (`user`, `auto`, `baseline`, `external`). Triggers reject update/delete. |
| `pipeline_units` | PK `(book_id, unit_key)`; `step_id`, JSON body | Replaceable validated-unit cache keyed by exact request identity; each entry is also retained as an `analysis_output` artifact. |
| `pipeline_state` | PK `book_id`; JSON body | The book revision and step signature last reconciled, used to detect outside changes cheaply, and the evidence projection's state (`evidence`: input digest, row digest, chapter `sources` hashes, counts). |

Pipeline runs never write `books.body`; acceptance does, in one transaction with the decision. See [the analysis pipeline](ANALYSIS-PIPELINE.md).

### Voice examples

| Table | Key and columns | Contract |
| --- | --- | --- |
| `voice_preview_requests` | PK `id`; `book_id`, JSON `body` | Retained version-1 effective request: preview metadata, exact bounded passage/demo text, performer, scene inputs and underlying audio fingerprint. When pronunciations apply, the passage adds `pronunciations` with only the matching entries' `term`, `respelling`, `providers` and `match_case`, and the preview adds `spoken_text` (and `pronunciation` for an unsaved draft). |
| `voice_preview_takes` | PK `id`; `preview_id` FK to requests, `book_id`, `asset_id`, JSON `body`; index by book/preview | Immutable independent take metadata and the actual WAV asset identity. |

Both tables reject UPDATE and DELETE with SQLite triggers. A Breeze request's performer snapshot includes the pinned `voices.breeze` selection. Request IDs hash the canonical versioned recipe before the ID is added; the recipe includes book/source identity, exact text, provider/model/voice, effective character/scene/passage directions and audio recipe version through its fingerprint. Repeated equivalent requests retain the original row. Credentials and reader state are excluded; cast inputs are immutable snapshots rather than pointers to the mutable projection. The archive is separate from selected enhanced takes and simple-listening sessions; it does not support cross-book synthesis reuse or force-rerender selection.

A source sample is an exact prefix capped at 400 Python Unicode code points. Its version-1 `source_anchor` stores book/chapter/passage IDs, exact start/end and SHA-256 of the prefix. The full original passage is validated against canonical text before extracting it. Demo samples use a versioned original fixed transcript and a null source anchor; they never claim invented book coordinates. Character-associated inputs include saved scene/passage cues and explicit unsaved direction overrides. Simple/generic narrator samples omit enhanced direction.

Take IDs hash `[preview_id, asset_id]`. Metadata retains its producer fingerprint, preview ID, source anchor and creation time; WAV bytes live at `voice-previews/<book-id>/<asset-id>.wav`. Reuse checks both SHA-256 identity and valid WAV content. A character display-name edit retains a new request with the current name but can reuse an existing same-book take when every other source and performance input matches. An indexed fingerprint lookup narrows candidates; the new immutable take records version-1 `reuse` provenance with the actual retained `take_id` and `preview_id`, and omits the original provider usage so it does not imply a new charge. Existing version-1 requests remain readable and unchanged. Damaged/missing assets are skipped without deleting prior history; a new explicit request can generate a distinct retained replacement. Valid audio completed during cancellation stays saved. Library storage attributes preview request/take payloads and reports `voice_preview_bytes` separately. Full backups must include this third media tree; current analysis/audiobook exports do not include the preview archive or WAVs.

### Listening and search

| Table | Key and columns | Contract |
| --- | --- | --- |
| `listening_sessions` | PK `(book_id, id)`; `body` | Deterministic single-narrator configuration: schema, book, provider, voice, model. Breeze sessions also include `voice_revision`, `seed` and any `settings`, so a voice changed on the server starts a new session; Gemini/device configurations are unchanged. |
| `listening_takes` | PK `id`; `book_id`, `session_id`, `segment_id`, `recipe`, `asset_id`, `body` | Retained take metadata indexed by book/session/passage/recipe; update/delete triggers guard existing rows. |
| `listening_synthesis_cache` | PK `(content_key, take_id)`; FK `take_id` to `listening_takes.id` | Rebuildable index of equivalent synthesis inputs. New takes and validated exact-source legacy cache hits populate it. |
| `listening_chunks` | PK `id`; `book_id`, `session_id`, `chapter_id`, `asset_id`, `body` | Version-1 chunk: chapter slice `start`/`end` (code points), `text_sha256`, passage anchors `[id,start,end]`, recipe fingerprint, provider/model/voice, content asset and duration, `timing` (`pause_alignment` v1 clips plus match quality), flags, request/job details and usage. Update/delete triggers guard rows. |
| `search_books` | PK `book_id`; `fingerprint` | Fingerprint of the currently indexed source/passages. |
| `passage_search` | FTS5: unindexed `book_id`, `chapter_id`, `passage_id`; indexed `text`; tokenizer `unicode61` | Rebuildable literal-word passage index. SQLite also creates its internal FTS shadow tables. |

A chunk applies to a passage only while the chapter slice hash, every passage anchor and text, the session and the recomputed recipe fingerprint still match; a source or recipe change leaves the chunk retained but unused. Update/delete triggers and a skip-on-duplicate insert trigger keep rows immutable (REPLACE cannot overwrite). Chunk clips take precedence over single-passage takes so consecutive passages play from one file. Chunks are not entered in `listening_synthesis_cache`, which describes single-passage speech. A simple listening recipe includes the source/session/passage identity and underlying audio fingerprint. It deliberately excludes enhanced cast and stage direction. Before rendering or reuse, the exact source slice must match. Audio results may be retained after Stop if the in-flight generation completed, but that does not automatically resume playback.

Synthesis-cache version 1 uses the effective audio fingerprint with a constant lookup-only passage ID, plus its cache schema version. It therefore includes exact text, provider/model, voice and the audio recipe version while excluding book/session/source location. The constant lookup ID is never sent to synthesis or published as a generated source identity. A valid cache hit checks both the WAV and the actual SHA-256 asset ID. Reuse across books copies the immutable bytes to the target book's directory, without overwriting an existing asset. A copy that fails its integrity check (the source changed, or the target book already holds a damaged file under that asset ID) skips that candidate, so the passage can be generated; a file-system error such as a full disk fails the request instead of starting a paid fallback. Original source/take history remains unchanged.

New take bodies may include `synthesis_key`. A reused take additionally stores `reuse = {schema_version:1, take_id, book_id, session_id, segment_id, recipe, fingerprint}`, pointing to an actual retained input take. Its own `recipe`, `source_anchor`, session and passage identify the new source binding; its top-level `fingerprint` remains the original generated performance fingerprint. Historical `resource_usage` is not copied into the new reuse event. A cache hit is never recorded on the take, the job or the audio object: the listen response's `cached` field and the resource operation's `cached` flag report it. API responses present takes through a whitelist (the contract `AudioRef` core plus public extras), so `recipe`, `source_anchor`, `synthesis_key`, the producer `fingerprint` and `resource_usage` stay in storage; the presented `reuse` pointer carries only `schema_version`, `take_id`, `book_id`, `session_id` and `segment_id`, without the original take's `recipe` and `fingerprint`. Provider usage is served by the resources routes. The index can be repopulated from known current take keys and lazily validated exact-source legacy hits; old take bodies are not rewritten.

The browser's warmup/lookahead/chapter-preparation intent is transient. Each requested passage has its own persisted job and take, but there is no durable chapter queue that starts itself after restart. Source/voice changes invalidate the relevant browser intent; completed reusable takes survive. See [listening behavior](LIBRARY-LISTENING-RESOURCES.md#independent-simple-listening) for scheduling bounds.

### Saved performances

| Table | Key and columns | Contract |
| --- | --- | --- |
| `performances` | PK `(book_id, id)`; `body` | Schema-1 label: `id` (`pf_<hex>`), `name`, `mode` (`simple`/`cast`), `chapter_ids` (book order), `provider`, `model`, `voice` (simple: the requested value), `session_id` (simple) or `cast_snapshot` and `pronunciation_snapshot` (cast; the book's pronunciations at creation, absent on records made before pronunciations existed, which then render without them), `archived`, `job_id`, timestamps. Renaming, archiving and starting a job update only this row. |
| `performance_takes` | PK `(performance_id, segment_id, source_key, asset_id)`; `book_id`, `body` | One retained cast take: render metadata (fingerprint, content `asset_id`, duration, provider/model/voice, any provider usage), `speaker_id`, `character_id` actually used, `fallback`, source anchor and `created_at`; reused takes add a `reuse` pointer (`performance_take` or `studio_take`). Update/delete triggers and a skip-on-duplicate insert trigger keep rows immutable. |

A **simple** performance owns no audio rows. Its `session_id` names a deterministic `listening_sessions` row, so its audio is that session's `listening_takes` and chunk clips for the selected chapters, including takes made earlier by live listening with the same narrator. A **cast** performance stores the resolved cast at creation (`Runtime.resolved_cast`, without evidence quotes) and renders from that snapshot. Later Cast edits, library voice versions or Breeze default changes do not alter it; passage speaker assignments and scene/passage directions are read from the current book when a passage is generated. Unknown or `unassigned` speakers, and characters without their own renderable voice for the provider, use the snapshot `narrator` (`fallback: true`). A narrator without a renderable voice blocks creation.

`source_key` is a SHA-256 of `{schema_version:1, chapter_id, start, end, text}`. A take counts only while its key equals the passage's current key and its file exists; a changed passage shows as not ready, and resume regenerates only it. Old rows stay. Before generating, a cast job reuses a validated WAV from any performance of the book with the same render fingerprint, then the current or archived Studio take with that fingerprint (read only), and only then synthesizes. Cast WAVs are content-addressed under `audio/<book-id>/` beside Studio takes and are served by the book's audio-asset route; they never enter the `takes` table or `audio_take` artifacts. The asset is part of the key so a damaged file can be replaced by a new asset for the same source.

Search only indexes valid anchored passages. Queries become quoted literal words joined with AND; result rank is lexical relevance, not identity confidence. The index is rebuilt when its source/passage fingerprint changes. If SQLite lacks FTS5, search reports unavailable while the rest of the library remains usable.

## Dependency and freshness contracts

An accepted analysis unit's cache key includes the effective prompt, schema, output cap, system instruction, provider/model, adapter/pipeline/validator versions, and source locator. Its row also carries the whole-book source fingerprint. Current discovery ranges can reuse a previously validated result even when the selected provider changes, retaining original producer metadata. Profile and direction reuse depends on the concrete evidence/cast/context used in their generated request recipes.

Lineage IDs and effective-input equality answer different questions: two equivalent inputs may be reusable even when their history differs, but reused output must not be relabeled as produced by the new provider. Artifact dependencies record the actual known source of a saved result.

| Change | Current effect |
| --- | --- |
| Chapter title/kind repair with unchanged canonical text | Preserves source IDs/spans and existing results; updates structure metadata and may change default eligible section selection. |
| Additional discovery evidence or changed profile inputs | Alters affected profile requests/freshness; old outputs remain retained. |
| Cast/profile/performance change | Can make direction/audio recipes stale; reviewed edits remain authoritative. |
| Voice/provider/model change | Changes enhanced audio recipe; existing byte-addressed assets remain on disk. |
| Enhanced cast/direction edit | Does not alter a simple single-narrator session recipe. |
| Cast/voice edit after a cast performance was created | Does not alter the performance's snapshot or its retained takes. |
| Passage source change | Hides that passage's simple and cast performance audio until it is prepared again; rows are retained. |
| Pronunciation add/edit/remove | Changes the audio recipe (sent `text` plus a `pronunciation: {version:1, applied:[[term, spoken]…]}` field) only of passages, chunks and previews containing that word, for Studio takes (retired, archived WAVs kept) and simple listening. Passages without the word keep identical fingerprints. Cast performances keep their `pronunciation_snapshot`. |
| Membership/identity link/earlier evidence change | Changes bounded series context and downstream requests that consume it. |
| Invalid saved unit | Rejects reuse and removes its fast-cache row; immutable output history remains. |
| Archive/remove | Changes active visibility/context and processing eligibility; retains data and files. |

There is no automatic universal invalidation traversal over every artifact edge. Each pipeline stage computes its relevant recipe or freshness state. Any new cache must specify effective inputs, source validation, producer provenance, and failure retention explicitly.

## Transactions, migrations, and concurrency

`Store.connect()` enables SQL foreign keys; initialization sets WAL. An application `RLock` serializes mutation sections. Multiple connections are used, with a 30-second busy timeout. A process-level data-root lock is acquired by the server before initializing the store. It prevents a second server from interpreting another process's active work as a restart.

Important write boundaries:

- `Store._save_book()` captures missing legacy state before replacement, persists cover data, updates book/current takes, then captures new projections in one transaction.
- A pipeline decision applies the accepted version to the projection, rebuilds character references and records the decision in one revision-guarded transaction. `PipelineRepository.save_unit()` saves a validated unit and its artifact together, before the version is recorded.
- `Store.save_take()` preserves previous/new selected take metadata without recapturing every passage for each audio write.
- Provider requests run outside long SQLite transactions. Reservations are committed before the request.
- Audio file publication is atomic separately from SQLite. A validated asset can survive without a selected database pointer if a later step fails; there is no filesystem/database distributed transaction or automatic orphan cleanup.

Ordinary worker jobs and series reservations are coordinated by `Runtime` and the series coordinator, which runs one book's pipeline run at a time (`pipeline_runs.series_run_id` names the parent job). Mutation endpoints check active work; the database alone does not enforce these scheduling rules. Cancellation and restart preserve completed units and assets, while uncertain remote work remains visible rather than silently replayed.

Schema changes use additive table/index/trigger initialization and targeted legacy backfill. Some optional tables are initialized on first repository use. One-time data migrations ([migrations.py](../bardic/migrations.py)) run at server start, holding the instance lock, and are recorded in `schema_migrations` (`id`, `status` `completed` or `failed`, `updated_at`, and a JSON `body` with counts). The first, `classic_removal_v1`, retains the removed Classic engine's data as artifacts, verifies it, deletes the `character_observations` rows and drops `analysis_units` and `analysis_checkpoints` ([stage 4](CLASSIC-REMOVAL.md#stage-4-what-was-dropped)); a failed run changes nothing it cannot redo and runs again at the next start. There is no `PRAGMA user_version` protocol or tested downgrade path. Artifact schema versions and request recipe versions are payload/behavior versioning, not database migration numbers. A maintenance reader must also know that constructing `Store` updates interrupted jobs, and starting the app runs pending migrations; use a deliberate read-only SQLite connection for a truly non-mutating inspection.

## Files, exports, and backups

New libraries default to `.bardic/`, configurable with `BARDIC_DATA_DIR` (legacy alias `SPINTAILS_DATA_DIR`). With neither variable configured, an existing `.spintails/` directory is reused in place if `.bardic/` is absent. The entry point loads the project-root `.env`; an existing shell environment takes precedence even across these aliases. A relative data root resolves from the process working directory. The product rename moves no files and changes no database schema; see [upgrade details](OPERATIONS.md#upgrading-from-spin-tails).

```text
<data-root>/
  library.sqlite3
  library.sqlite3-wal       # May exist while SQLite is using WAL
  library.sqlite3-shm       # May exist while SQLite is using WAL
  server.lock              # Process lock
  originals/<book-id>/source.epub  # Or source.txt
  audio/<book-id>/<asset-id>.wav      # Studio takes and cast performance takes
  listen-audio/<book-id>/<asset-id>.wav
  voice-previews/<book-id>/<asset-id>.wav
  voice-library/<asset-id>.wav     # Library-wide voice auditions and design candidates
```

Older enhanced audio may use a recipe fingerprint as its filename; current byte-addressed takes use a SHA-256 asset ID. Thumbnail bytes are inside SQLite. Export ZIPs are built in temporary directories and removed after their response. A manually created `backups/` folder may exist, but the application has no automatic backup scheduler.

Library storage reports measure original/enhanced/simple-audio/voice-preview files. Per-book database figures are JSON/BLOB payload attribution, not physical SQLite page allocation. Shared database/WAL/SHM size is reported separately; indexes, free pages, and shared metadata cannot honestly be divided into exact book file sizes.

### Current projections and artifact snapshots

An artifact snapshot captures a supported scope at a point in its content history. A collection of snapshots is useful for reuse and inspection but is not guaranteed to reconstruct every job, preference, thumbnail, or filesystem object. Current reader JSON and immutable historical inputs must both be retained when exporting reusable knowledge.

### Portable analysis bundle

The manifest's format identifier remains `spintails-analysis`. It is a retained interchange contract, independent of the Bardic display/package name, so the rename required no format migration. Schema version `2` (contract 0.2.0) changed `analysis-attempts.json`: it holds the allowlisted attempt shape that the pipeline inspector shows (`PipelineAttempt`, with `validation_state`), not raw stored rows, so process IDs and unknown stored fields are no longer exported. The price rates (`input_rate`, `output_rate`) and `book_id` are kept, because the rates explain `charged_estimate_usd`. Version `1` exports remain readable as before; nothing rewrites them.

`/api/books/{book_id}/analysis-export` includes:

- `manifest.json`: format/schema, coordinate system, scope, and external book dependencies.
- `book.json` and `story-map.json`: current canonical source/projection and typed containment/attribution graph.
- `artifacts.jsonl`: every retained version for the selected book plus transitive dependency artifacts from other books.
- `observations.json`, `references.json`, `series.json`: evidence and explicit identity context.
- `analysis-attempts.json`, `pipeline-events.jsonl`: processing provenance and validation events.
- Resource operations and listening session/take metadata when their tables exist, plus a format README. Saved performance records and `performance_takes` are not currently included.

The bundle contains source text and may contain earlier-book input evidence through dependencies. It excludes credentials, settings, original EPUB/TXT binaries, cover BLOBs, and all WAV files. Its audio asset IDs refer to separately stored media. There is no implemented bundle-import/restore workflow, so this is an interoperable analysis export, not a complete restorable backup.

### Audiobook bundle

The separate audiobook export contains current selected enhanced takes, `production.json` (the book as `GET /api/books/{id}` presents it, not the stored row), source chapter text, and `timeline.json`. It is a download only: it records no resource operation or other history. It assembles a chapter WAV only when every required passage has valid audio and includes individually completed takes for partial chapters. Timings identify passage boundaries; missing passage IDs are explicit. It does not export the independent simple-listening library or saved performances, perform word alignment, or package M4B.

### Full backup boundary

A recoverable local backup needs a consistent SQLite snapshot **and** the originals and all four audio trees (including `voice-library/`). A raw copy of only `library.sqlite3` while WAL is active may omit committed work; use SQLite's backup facility or stop the server before taking a consistent filesystem copy. Preserve project environment configuration separately if needed; it is not in the library or portable export. Restoration procedures must be exercised independently because the application does not yet provide a restore tool.

## Constraints for future changes

Preserve code-point offsets and exact quotes; do not silently substitute regenerated text for imported source. Scope character IDs by book and require explicit cross-book identity. Preserve accepted paid results before changing projection logic. Do not delete artifact history to make a cache appear fresh. Keep unknown cost/provenance visibly unknown. Version changed request and audio recipes, and record only dependency links that can be established from actual saved inputs. Add explicit migration/backup handling before introducing destructive schema or source transformations.
