# Architecture

This document describes the implementation in this repository as of 2026-09-27. It is a guide to the code, not a specification of unimplemented features. See [the data model](DATA-MODEL.md) for persistence contracts and [the README](../README.md) for setup.

## Purpose and boundaries

Spin Tails is a local web application for importing an ebook, developing an evidence-backed performance script, generating narration, and reading along with passage highlighting. It also supports simple single-narrator listening without completing story analysis first.

The server is Python with FastAPI and SQLite. The browser uses HTML, CSS, and plain JavaScript; no frontend build system or JavaScript framework is required. Original ebooks and WAV assets are files on the local machine. Text analysis can call Gemini, OpenAI, or Anthropic. Narration currently uses Gemini or installed macOS voices. Provider calls are explicit processing actions; browsing saved work does not generate narration or run cloud story analysis.

The application has a single local owner. It is not a multiuser service, hosted library, DRM-removal tool, or general workflow scheduler. The default server binds to loopback. Changing that deployment boundary would require additional authentication, authorization, and operational design.

## System map

```mermaid
flowchart TD
    UI[Browser: library, reader, cast, studio] --> API[FastAPI routes and Runtime]
    API --> Import[EPUB/TXT import and structure]
    API --> Worker[Durable jobs and bounded workers]
    Worker --> Analysis[Discovery, profiles, direction]
    Worker --> Listen[Simple listening]
    Worker --> Narration[Enhanced narration]
    Analysis --> Providers[Gemini / OpenAI / Anthropic text APIs]
    Listen --> TTS[Gemini TTS / macOS say]
    Narration --> TTS
    Import --> Store[SQLite current state and checkpoints]
    Analysis --> Store
    Listen --> Store
    Narration --> Store
    Store --> History[Immutable artifacts and dependencies]
    TTS --> Assets[Validated content-addressed WAV files]
    API --> Views[Pipeline, story map, search, resources, exports]
    Views --> Store
    Views --> Assets
```

SQLite is the authoritative application store; JSON book projections make reader and editor requests straightforward. Relational tables hold membership, links, observations, jobs, caches, and artifact dependencies where these need independent lifetimes or queries. SQLite FTS5 supplies rebuildable lexical search. There is no vector database or separate graph database.

## Module responsibilities

| Module | Responsibility and important boundary |
| --- | --- |
| [`__main__.py`](../spintails/__main__.py), [`config.py`](../spintails/config.py) | Load the project `.env`, choose the local port, start Uvicorn. |
| [`app.py`](../spintails/app.py) | Request validation, routes, local HTTP protections, `Runtime`, worker submission, cancellation, presentation, editing, playback, and export endpoints. |
| [`store.py`](../spintails/store.py) | SQLite connections and lock, current book/take/job/settings state, atomic analysis publication, startup interruption recovery, process lock. |
| [`importer.py`](../spintails/importer.py) | Safe EPUB/TXT ingestion, canonical text extraction, initial scenes/passages, bounded cover thumbnails. |
| [`structure.py`](../spintails/structure.py) | EPUB navigation/NCX/headings/section classification, metadata-only structure repair and checkpoint transformation. |
| [`preprocessing.py`](../spintails/preprocessing.py) | Local census, frequency/spread/ambiguity heuristics, processing priorities, source coverage. No LLM calls. |
| [`analysis.py`](../spintails/analysis.py) | Provider adapters, schemas, evidence validation, local draft rules, annotation application, dispatch to the appropriate pipeline. |
| [`staged_analysis.py`](../spintails/staged_analysis.py) | Older chapter-checkpoint implementation, local draft processing, and shared compatibility/reference helpers. Still used; not dead code. |
| [`progressive.py`](../spintails/progressive.py) | Current cloud scan/profile/direct/full planning and execution, request recipes, validated unit reuse, profile freshness, progressive publication. |
| [`processing.py`](../spintails/processing.py) | Accepted-unit cache, local census cache, attempt/event ledger, per-request reservations and budget enforcement. |
| [`model_catalog.py`](../spintails/model_catalog.py) | Documented model choices, dated pricing metadata, explicit account inventory refresh, custom model support. Listing a model does not prove generation compatibility. |
| [`account_checks.py`](../spintails/account_checks.py) | Explicit small text requests that test an account/model and classify errors. Does not retrieve a credit balance. |
| [`series.py`](../spintails/series.py) | Explicit series membership, confirmed character identities, retained observations, bounded evidence from earlier supplied volumes. |
| [`series_processing.py`](../spintails/series_processing.py) | Collection plan/fingerprint, parent and child jobs, bounded parallel discovery, ordered later phases, cancellation and failure settlement. |
| [`library.py`](../spintails/library.py) | Editable metadata, covers, measured storage, reversible removal/restoration, explicit missing/planned volume slots. |
| [`artifacts.py`](../spintails/artifacts.py) | Immutable content-addressed versions, mutable current heads, verified dependency edges, projection capture and honest legacy backfill. |
| [`pipeline_view.py`](../spintails/pipeline_view.py) | Pipeline progress, source-anchored story map, portable analysis export. These views expose implemented state rather than run a second pipeline. |
| [`search.py`](../spintails/search.py) | Local literal-word search with source coordinates, limited to the selected book or it plus earlier active series volumes. |
| [`audio.py`](../spintails/audio.py) | Narration recipes/providers, audio validation/normalization, and sample-accurate WAV assembly. |
| [`take_archive.py`](../spintails/take_archive.py) | Private rendering followed by validated, atomic, content-addressed audio publication. Distinguishes generation recipe from actual resulting bytes. |
| [`listening.py`](../spintails/listening.py) | Independent single-voice sessions and takes; intentionally excludes enhanced casting and performance directions. |
| [`resources.py`](../spintails/resources.py) | Durable local/narration measurements and combined resource summaries, preserving unknown measurements and avoiding double-counting analysis attempts. |

## Browser and API flow

[`static/index.html`](../spintails/static/index.html) defines the page and dialogs. [`static/app.js`](../spintails/static/app.js) coordinates selected book/chapter/passage, settings, cast and script editing, job polling, and audio playback. Feature scripts expose `window.SpinTails…` interfaces:

| Feature | Browser files | Main API families |
| --- | --- | --- |
| Library and removed items | `library.js`, `library.css` | `/api/library`, book metadata/cover/archive/restore, series and volume slots |
| Single-narrator listening | `listen.js`, `listen.css` | Book `/listen`, `/listen/takes`, `/listen/audio/{asset_id}` |
| Progressive production | `production.js`, `production.css` | Book `/preprocessing`, `/analysis-plan`, `/analyze`, `/analysis` |
| Series identity review | `series.js`, `series.css` | Book `/series`, character links, series characters/context |
| Collection processing | `series-processing.js`, `series-processing.css` | Series `/plan`, `/process`, `/runs`, `/map` |
| Pipeline inspection | `pipeline.js`, `pipeline.css` | Book `/pipeline`, `/artifacts`, `/story-map`, `/search`, `/analysis-export` |
| Resource usage | `resources.js`, `resources.css` | Book `/resources` |

The table summarizes endpoint families; inspect [route definitions](../spintails/app.py) for request models and exact paths. These are internal application APIs without a separate compatibility/versioning policy. The generated schema is available at `/openapi.json`; Swagger `/docs` and ReDoc are disabled.

An import posts an EPUB/TXT, saves its original bytes, creates canonical chapters and anchored passages, and makes the book readable immediately. Cast and scene data start as a local draft. The reader highlights a whole passage while that passage's audio plays. Position and playback speed are browser-local preferences; the browser does not save API keys in local storage.

Simple listening requests the chosen passage as needed and reuses a matching saved take. Enhanced production separately analyzes the story, lets the user review/edit cast and directions, and renders selected passages, scenes, or the book. The reader can use either mode; simple takes do not overwrite enhanced selections. Stale-response guards prevent a completed background response from continuing playback after the user changes selection or stops.

## Source and structure

EPUB import follows the package spine for reading order and uses EPUB navigation, NCX, headings, and semantic markers for names and section kinds. It does not invent chapter numbering from spine position. Front matter, recap, narrative chapters, and back matter remain distinguishable. Multiple anchor targets inside one spine document are represented by `logical_sections` metadata; the retained chapter container remains the processing/source unit. Full logical-section repartitioning is not implemented.

The importer rejects unsafe ZIP members, unsupported encrypted reading content, oversized archives, and malformed XML. Optional cover failures do not discard readable prose. Cover images are decoded and re-encoded as small JPEG thumbnails; embedded remote image URLs are not fetched.

Canonical chapter text is the extracted, normalized reading text, not byte-identical EPUB markup. Scene ornaments become same-width spaces during import. Once imported, analysis annotates that canonical text rather than rewriting it. Passage and evidence coordinates must continue to resolve to exact slices. Structure repair reparses the saved original and requires matching chapter count and exact canonical chapter texts before updating names/kinds/navigation metadata. It preserves IDs, source offsets, profiles, selected audio, and reusable accepted work. See [structure details](STRUCTURE.md).

## Progressive cloud analysis

The normal HTTP analysis request defaults to `phase="scan"`. `analysis.analyze_book()` dispatches cloud requests with an explicit phase and store to `progressive.run()`. Local draft work and compatibility calls without a phase still use the older staged implementation. Calls without a store use the original in-memory implementation; they do not acquire the progressive repository's durable budget/cache behavior.

The progressive phases are:

1. **Local census.** Scan source and current annotations without a model. Count appearances, spread, explicit speech tags, unresolved attribution, and ambiguity. These heuristics guide effort; they do not establish identity or scene presence.
2. **Scan / discovery.** Process eligible sections in bounded source ranges, normally up to 24,000 characters per range. Validate every quoted piece of evidence. Save each accepted result durably, recover its cast candidates, and expose completed source coverage.
3. **Profiles.** Build character requests from grounded observations with bounded evidence selection and effort tiers. Add bounded evidence from explicitly linked characters in earlier active series books. A profile remains provisional while relevant whole-book discovery is incomplete; complete coverage still does not guarantee correct interpretation.
4. **Direct.** Analyze bounded passage batches with neighboring source and relevant cast context. Apply speaker attribution, scene/performance annotations, and supported cues without changing the source text. Reviewed edits take precedence over generated replacements.
5. **Full.** Execute the above cloud phases in order, reusing accepted work where eligible. The initial plan cannot know all work introduced by newly discovered characters or retries.

Front/back matter are excluded from default semantic coverage; a specifically selected section can still be processed. The pipeline's `scan` model is independently configurable from the deeper analysis model. The word “preprocessing” in model settings refers to this cheap cloud discovery role; the local census remains a separate free step.

Evidence validation accepts only a short contiguous source quotation, with constrained typography/whitespace normalization mapped back to exact source coordinates. It does not accept paraphrases, invented spelling, or separated snippets joined with ellipses. One evidence-specific repair generation is allowed after a rejected response. Invalid results never become accepted annotations.

## Reuse, invalidation, and failure

There are three separate persistence mechanisms:

- The **current book projection** drives the reader and editors.
- **Checkpoints and accepted-unit caches** resume processing and avoid repeating accepted requests.
- **Immutable artifact versions** retain current and historical outputs, exact known input recipes, and verified dependency links.

An analysis unit key hashes the effective request and validation recipe: provider/model, prompt, system instruction, response schema, output cap, adapter/pipeline/validator versions, and chapter/range or character locator. Including the locator prevents identical prose in different chapters from sharing the wrong annotations. Dependency artifact IDs provide lineage; they are not substituted for the effective request input when determining equivalence. Validated discovery for the same current source interval may be reused across provider changes while retaining its actual original producer.

Accepted units are saved before chapter publication or progress callbacks. If publication fails, resuming can recover the accepted result instead of paying for it again. Cached data is revalidated; a rejected cache entry is removed from the fast cache, while immutable history remains. Changed profile/cast inputs make dependent directing recipes stale. Changing voice or performance instructions changes the audio recipe; earlier WAV assets remain retained. This is specific, recipe-based freshness logic, not a general graph engine that automatically propagates every kind of invalidation.

`Store.commit_analysis()` commits book projection, checkpoint, current references, retained observations, and associated artifacts in one SQLite transaction. Durable state is published between bounded units. Cancellation is cooperative between calls/units; an in-flight provider request may finish and incur cost. Restart marks active jobs/checkpoints interrupted. A new explicit run resumes reusable work; the server does not automatically resume remote generation on startup.

## Cost controls and measurements

Each progressive HTTP attempt reserves a request count, input allowance, output allowance, and conservative estimated cost **before** it is sent. Per-run request/token limits differ from the dollar allowance, which applies cumulatively to tracked analysis attempts for the book, including earlier runs. Defaults are defined by `AnalysisLimits` in [app.py](../spintails/app.py).

In the metered path, a transient HTTP error permits at most two total HTTP attempts per provider-adapter invocation, subject to the remaining allowance. Authentication/billing failures are not retried. An uncertain network outcome is recorded and not automatically repeated. A single evidence-repair generation may invoke the adapter a second time, so one logical analysis unit can have up to four HTTP attempts when both invocations need their permitted transport retry. Every attempt consumes the same guards.

Unknown pricing blocks use of a dollar guard; an explicit request/token-only configuration can proceed without that guard. Missing provider usage keeps a conservative reservation. Displayed planning estimates are smaller forecasting estimates, not reservations, invoices, or guaranteed final totals. Account checks only test a small text request; they do not expose an actual credit balance.

Resource views combine the analysis attempt ledger, local/narration operations, and cache events. Unknown historical usage stays unknown. Local CPU measurement covers the current Python thread, excluding subprocesses, other threads, GPUs, and remote compute. Summed operation time is not whole-job wall-clock latency when work overlaps. Narration has resource telemetry but does not currently share the progressive analysis request/token/dollar guard. See [resource and listening details](LIBRARY-LISTENING-RESOURCES.md).

## Series processing and identity

Books remain independent sources with independent chapter IDs, profiles, coverage, takes, and budgets. Adding a volume does not concatenate novels into one giant prompt or flatten their evidence.

A book can belong to one series at an explicit numeric reading position. Decimal positions support side stories. Series characters are separate identities; a matching name is not enough to link two book characters. User-confirmed links select which earlier-volume observations can influence a later book. Context excludes later books, archived sources, stale source hashes, and mere mention records. Its bounded selection does not imply complete knowledge of every earlier observation.

Missing/planned volume slots are explicit library metadata with no source text. A collection run skips them and processes only supplied active books; gaps remain visible and no knowledge of an absent volume is inferred. A scan does not automatically confirm newly discovered cross-book identities. Reviewing links between scan and profiles is useful when building series continuity.

Collection discovery can use one or two workers. Profiles and direction run in reading order. A parent job reserves all child books until the collection run ends, preventing edits, membership changes, or restoration of an archived member from changing its scope mid-run. The UI submits a plan fingerprint; a changed effective plan is rejected before queuing work. Limits apply per book, so collection cost can scale with the number of supplied books. A failure stops scheduling new books; already running bounded work settles cooperatively.

## Audio production

An enhanced narration recipe combines the exact passage, provider/model, selected voice, character and scene/performance instructions, and audio format version. Gemini and macOS narration are adapters behind this recipe. Device narration uses `say` and `ffmpeg`; it does not provide Gemini-style expressive steering.

`take_archive.produce_take()` renders into a private temporary file, checks the returned recipe fingerprint, validates the WAV, hashes its final bytes, and publishes without replacing an existing asset. The recipe fingerprint identifies the requested performance; the asset hash identifies a particular result. Two generations of one recipe can therefore coexist. WAV assembly uses real sample counts, and export joins only complete chapters.

Simple listening instead hashes a narrator session and exact passage identity. It excludes inferred speaker, character traits, scene directions, and cues. Cast edits therefore do not invalidate simple listening audio. Its sessions, takes, and audio directory are separate from enhanced production.

Current highlighting/timelines are passage-level. Word alignment, verification that every word was actually spoken, continuous scene-level acting synthesis, and automated audio quality scoring are not implemented.

## Concurrency, migration, and operational boundaries

The normal server owns one data root, protected by `InstanceLock`. SQLite uses WAL, a 30-second connection timeout, foreign-key enforcement, and an application `RLock`. Ordinary book jobs use one worker. A separate series coordinator owns its bounded discovery pool, so the application as a whole is no longer strictly single-worker. Editing guards protect reserved books and active series scope.

Schema setup is additive `CREATE TABLE/INDEX/TRIGGER IF NOT EXISTS`, with targeted legacy data conversion and idempotent artifact backfill. There is no numbered migration framework, schema downgrade mechanism, or general restore wizard. Backfill preserves data that still exists and labels incomplete provenance; it cannot recreate overwritten pre-history. Constructing a `Store` also performs restart recovery, so maintenance scripts must not casually instantiate a second live store.

Settings loaded from `.env` remain environment configuration; keys entered through the UI live in runtime memory. Saved preferences omit keys. Provider requests carry credentials directly to the selected provider, never into analysis artifact payloads or exports. Source text and exact prompts do belong in local artifacts and analysis exports. See [data ownership and backup](DATA-MODEL.md#files-exports-and-backups).

## Implementation limits and extension points

| Area | Current boundary |
| --- | --- |
| Import | EPUB/TXT only; retained EPUB containers can have multiple logical sections; no PDF/MOBI/DRM workflow. |
| Semantic analysis | Structured cloud annotations plus local rules. No local LLM adapter, automatic identity reconciliation, or certainty guarantee after full coverage. |
| Search | Literal-word FTS5 retrieval. No embeddings, vector ranking, or retrieval-driven automatic character linking. |
| Provenance | Exact inputs are retained for the current progressive path. Legacy data is explicitly incomplete; snapshot edges are not fabricated generation lineage. |
| Scheduling | Explicit bounded jobs with reusable work. No automatic restart continuation or distributed queue. |
| Audio | Gemini and macOS voices, passage production, separate simple listening. No OpenAI/Anthropic TTS adapters, voice cloning, word alignment, or M4B packaging. |
| Budgets | Conservative progressive text-analysis guards. No shared hard narration budget or exact provider balance API. |
| Lifecycle | Soft removal and restoration. No permanent-delete UI, automatic garbage collection, backup scheduler, or analysis-bundle import. |

When extending the system, preserve exact source coordinates, separate current selections from retained history, meter every new paid analysis attempt at its transport boundary, and version effective request/audio recipes when their behavior changes. New graph edges must identify actual known inputs. A new provider, index, or UI view must not silently turn an inspection request into paid generation.

Relevant deeper references: [data model](DATA-MODEL.md), [progressive analysis plan](PROGRESSIVE-ANALYSIS-PLAN.md), [chapter analysis](CHAPTER-ANALYSIS.md), [analysis providers](ANALYSIS-PROVIDERS.md), [artifact/storage notes](ARTIFACTS-AND-STORAGE.md), and [validation](VALIDATION.md). Plans describe intent and historical decisions; the code and this implementation map determine what currently exists.
