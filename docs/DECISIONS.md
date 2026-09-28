# Design decisions

These decisions describe the implemented baseline as of September 27, 2026. They are lightweight architecture records: why the current system works this way, what it costs, and what would justify revisiting it. Proposed extensions belong in the [roadmap](ROADMAP.md).

## D01 · Local Python web application

**Decision:** Python/FastAPI serves JSON endpoints and a plain JavaScript/CSS interface on loopback. `uv` manages Python dependencies. The browser has no build step. The application runs without an account, hosted database or cloud inference.

**Reason:** EPUB/text parsing, SQLite, HTTP model adapters and audio processing fit Python. A browser reader provides portable controls and highlighting without committing the project to a desktop framework.

**Consequences:** The process must remain running for the app to work. macOS device narration depends on system speech and ffmpeg; cloud narration has different portability constraints. The code is not a packaged desktop application, public multi-user service or mobile reader.

**Revisit when:** Installation/updates become a measured usability obstacle, or offline/mobile requirements justify a desktop wrapper or another frontend. Preserve the local service/data contracts so packaging can evolve independently.

## D02 · SQLite plus ordinary files

**Decision:** Use embedded SQLite for identities, jobs, source projections, references, units, immutable artifacts and ledgers. Original uploads and WAV assets remain files under the data directory. Use relational columns for scope/joins and JSON bodies for evolving structured payloads.

**Reason:** The library belongs to one local user. Transactions, joins, indexed lookup and recursive dependency traversal do not require another service. Ordinary media files remain easy to play, inspect and back up.

**Consequences:** A DB copy alone is not a complete media backup. Current schema upgrades are additive initializers rather than a general migration framework. The Store lock, SQLite transactions and data-directory instance lock are part of the consistency design. Physical database size cannot be allocated exactly to individual books.

**Revisit when:** Work requires multiple writers/processes or schema changes exceed safe additive initialization. Do not add a server database solely because artifacts form a graph.

## D03 · Immutable source; derived annotations

**Decision:** Preserve extracted chapter text as the canonical coordinate space. Offsets use Python Unicode code points with exclusive ends. Structural labels, speaker assignments, profiles, scenes and performance notes are derived annotations.

**Reason:** Evidence, highlighting, retries and downstream alignment must all refer to the same prose. Models are useful interpreters but cannot be trusted to reproduce a whole book verbatim.

**Consequences:** The reader uses extracted prose rather than faithful original EPUB layout. An EPUB spine source section is not always a narrative chapter; logical headings/ranges can be represented without rewriting source units. Direct JavaScript slicing with stored Python offsets is incorrect for some Unicode text.

**Revisit when:** Rich EPUB rendering is required. Add portable source-location mappings; do not silently replace existing canonical coordinates.

## D04 · Progressive interpretation with bounded evidence

**Decision:** Run deterministic structure and local census first, cheap semantic discovery across eligible source sections second, evidence-based profiles next, then chapter scene/direction work. Use separate scan and detailed models. Save accepted units and publish completed chapter work incrementally.

**Reason:** Long books exceed practical request sizes. Whole-book character understanding develops over time, and a failed later request should not erase earlier paid work. Frequency, dialogue and chapter spread can allocate effort without pretending to determine literary importance.

**Consequences:** Profiles remain provisional until adequate coverage and refinement. Local drafts are conservative and do not count as semantic discovery. Previewed full-pipeline work can grow after discovery. Review is still needed for ambiguous identities and performances.

**Revisit when:** Evaluation shows a specific extraction or evidence-sampling failure. Compare a local model/NLP stage or alternative sampling strategy on a bounded corpus before increasing context or retrying entire books.

## D05 · Separate current projections, reusable units and history

**Decision:** Keep book JSON/current selections for the UI, compatible accepted units for resume, append-only observations/artifacts for retained knowledge, and attempts/events for execution provenance. Artifact heads can change; retained versions and their input dependencies do not.

**Reason:** A cache is replaceable; historical evidence and expensive accepted outputs should survive new models, edits and future consumers. Current presentation is not a sufficient archival record.

**Consequences:** More tables and explicit versioning are necessary. Invalid cache entries are rejected rather than trusted because they exist. Legacy outputs disclose missing provenance; no history is invented. Analysis ZIPs include transitive dependencies, but there is no bundle importer yet.

**Revisit when:** Storage growth or reuse correctness warrants compaction/import tooling. Any cleanup must understand heads, references and dependency reachability before deleting data.

## D06 · Explicit series identities and incomplete collections

**Decision:** Books have explicit series membership and numeric reading order. Series character IDs join confirmed book-local identities. Only bounded, source-validated evidence from strictly earlier supplied volumes enters later-book profiles. Missing/planned volume records contain no text.

**Reason:** Namesakes and aliases are not reliable automatic identity joins. Users may own volume nine without the previous eight. Earlier context should improve continuity without pretending absent volumes were analyzed or leaking later revelations into earlier interpretation.

**Consequences:** Identity review is necessary. A full series scan does not automatically resolve every recurring character. New earlier-volume evidence can make dependent profiles stale. Contradictions are retained, but an explicit temporal-trait/contradiction resolver is future work.

**Revisit when:** There is an evaluated identity-suggestion workflow with evidence and review, or explicit user control over later-volume knowledge. Suggestions must remain separate from confirmed links.

## D07 · Bounded parallel discovery; ordered interpretation

**Decision:** A series coordinator schedules at most two independent discovery workers, then profiles/direction in supplied reading order. Parent runs reserve their books through completion. Plans have fingerprints; the UI sends the reviewed fingerprint when starting. Child allowances span phases.

**Reason:** Independent scans can overlap, but later interpretation needs the earlier work available. Unbounded concurrency risks rate limits, budget exhaustion and inconsistent context.

**Consequences:** A failure/budget stop stops new scheduling; already in-flight calls may finish. Cancellation is cooperative. A fresh preview/resume reuses matching work, rather than restarting all paid stages automatically. This is a local scheduler, not a distributed workflow engine.

**Amended 2026-09-28 (series on the step pipeline):** The owner decided to discard the phase engine and to run series on the step pipeline under the Analysis tab's consent-only policy. A series run now applies the chosen step(s) to one book at a time in reading order; discovery no longer overlaps across books. The confirmed series fingerprint, covering every book's pipeline plan, authorizes the run, and optional limits apply per book. Each book is re-planned and compared with its confirmed fingerprint before it starts. A cancelled or unstarted child is never started later. Pipeline results are not yet shared between volumes; see [series runs](ANALYSIS-PIPELINE.md#series-runs).

**Revisit when:** Real usage demonstrates that provider queues, batch APIs or additional parallelism would help. Preserve durable run identity, provider ownership, dependency order and bounded spending.

## D08 · Small voice requests and independent simple listening

**Decision:** Enhanced narration uses a single source passage/speaker per request. Simple listening uses the same canonical passages with its own narrator configuration, session/take records and cache. It generates only on explicit playback and stops automatic continuation at a chapter boundary.

**Reason:** Small takes support arbitrary cast size, targeted retakes and cheap recovery. Immediate listening should not require literary analysis or change the user's production casting.

**Consequences:** Fresh simple passages may have a generation gap between them. Separately generated enhanced clips can have audible seams and inconsistent timbre. Simple-mode audio is not currently included in the enhanced audiobook ZIP. A generation recipe identifies instructions; the WAV content hash identifies a particular performance.

**Revisit when:** Quality testing supports larger scene-aware takes, controlled prefetch, simple-mode chapter preparation/export or voice design. Retain source-span mapping and both old and replacement takes.

**Amended 2026-09-28 (continuous listening):** At the owner's request, simple listening now keeps going through the book by default. Play is the explicit action that authorizes generation until the listener pauses or stops, a request limit is reached, generation fails, or the book ends. Device voices and Breeze extend the bounded lookahead (at most 12 future passages, about 45 listening seconds) across chapter boundaries. Gemini queues the next chapter's job while playback runs, at most once per chapter per Play, when less than about 10 minutes of generated audio is ahead. It never does so after a quota or budget stop, after a failed, cancelled or interrupted job for that chapter, or after **Stop generating**; those still need an explicit **Resume chapter** or Play. A job started this way belongs to the listening that asked for it: Pause, Stop or leaving the page cancels it (requests already sent finish and are kept), while moving to another passage or chapter keeps it. Continuous listening stops before back matter (notes, index) unless listening started there. Uncertain requests are still never resent. A per-book **Continue into the next chapter** setting restores the chapter-boundary stop.

## D09 · Honest synchronization and audio validation

**Decision:** Highlight the current passage using measured clip boundaries. Validate audio format/frame completeness and reject empty/silent output. Do not claim word-level alignment or transcript fidelity.

**Reason:** A valid WAV does not prove every source word was spoken. Invented word timestamps would make the reader look precise while hiding errors.

**Consequences:** Word highlighting, independent transcription checks, pronunciation QA and performance scoring remain separate work. The current boundaries describe the recorded clips, not a verified word-for-word performance.

**Revisit when:** A tested optional forced aligner and transcript-verification stage preserve source coordinates, record confidence/method, and degrade gracefully to passage highlighting.

## D10 · Cost accounting with explicit unknowns

**Decision:** Persist analysis attempts, pre-request allowance reservations and per-operation resource measurements. Distinguish measured usage, estimates, potentially charged failures, cache reuse and missing reports. Dated prices are applied only when the inputs needed for a calculation are available.

**Reason:** A retry can incur a charge even when it yields no accepted artifact. Returned usage, reserved limits, actual provider invoices and account balances are different things.

**Consequences:** Historical gaps cannot be reconstructed. CPU timing is the current Python thread, not provider/GPU/subprocess utilization. Account checks are small explicit inference probes. The analysis dollar guard does not currently cap narration or all account spending; the resource ledger is not a universal budget engine.

**Revisit when:** Narration budget planning, series-wide allowances or invoice reconciliation are implemented with explicit scopes and uncertain-outcome handling.

**Amended 2026-09-28 (Analysis tab):** At the owner's request, step-pipeline runs no longer take a request limit or dollar guard up front. The confirmed plan preview, which shows the estimated requests and cost and says retries can add requests, is the authorization. Attempts are still reserved and recorded before sending, unknown cost stays unknown, and each unit's attempts stay bounded. API callers may still pass limits. Phase controls keep their allowances. Series runs followed on 2026-09-28 (D07 amendment).

## D11 · Lexical retrieval first

**Decision:** Use SQLite FTS5 for local passage search with bounded results. Keep source hashes and book/reading-order scope. If FTS5 is unavailable, report that limitation and keep source artifacts accessible. A vector database is not installed.

**Reason:** Exact names, phrases and evidence passages are useful without paying for embeddings or maintaining a second authoritative store. Existing bounded evidence sampling covers the first profile workflow.

**Consequences:** Semantic paraphrase retrieval remains limited. Search ranks are relevance scores, not evidence confidence. Domain edges and execution dependencies are stored/exported without a separate graph database.

**Revisit when:** A retrieval evaluation demonstrates concrete misses. An optional embedding index must be rebuildable, model/version/hash scoped, respect series order before retrieval limits, and return exact source anchors.

## D12 · Local credentials and explicit network actions

**Decision:** Load keys from the project `.env` at startup or hold Settings changes in server memory. Save only non-secret preferences. Analysis, narration, model inventory refresh and account checks are explicit actions. Failed providers do not trigger another provider automatically.

**Reason:** Users should know which company receives text and when spending can occur. Provider-specific access is not interchangeable.

**Consequences:** Restarting reloads `.env`/environment values and clears session-only key edits/check results. The local app has no authentication and must not be treated as a public hosted product. Keys are not the only private data: retained prompts, source text, artifacts and exports also belong to the user.

**Revisit when:** Distribution requires an OS keychain, shared server access or a different threat model. Public hosting, licensing and multi-user authentication require explicit product/owner decisions.

## D13 · launchd supervision; development servers are processes

**Decision:** On macOS the owner's server runs as the LaunchAgent `local.bardic` from the repository's main checkout, controlled by `./bardicctl`. Agents test changes on development servers: background processes that run their own checkout on a separate port with a scratch library, loopback bind and blank provider keys, recorded where any session can list or stop them. There is no container image.

**Reason:** A server launched from a terminal or agent session outlives that session with nothing responsible for it, and the next session cannot tell who owns it. launchd provides login start, crash restart and one owner. Worktrees already isolate code; the port, library and key settings isolate runtime state. A Linux container would lose macOS voices and `dns-sd`, and Docker Desktop's VM boundary defeats SQLite WAL shared memory and is not guaranteed to honour the `server.lock` exclusion against a host process.

**Consequences:** The job runs the checkout's virtual-environment Python directly after `bardicctl` syncs it, so launchd's stop and kill reach the server rather than a wrapper. The service serves the main checkout, so worktree changes are live only after merging and restarting. Service commands are macOS-specific; other systems need their own supervisor. The service log is not rotated. Development servers share the host's tools and Python, not a pinned image.

**Revisit when:** Bardic gains Linux-only dependencies, needs a CI image, or runs somewhere other than the owner's Mac. Any container must never mount the owner's live library.

## D14 · Saved performances are labels over retained audio

**Decision:** A performance is a named, mutable label: chosen chapters plus either one narrator (a listening session) or a snapshot of the resolved cast and a provider. A server job prepares it ahead of time. One-narrator audio is that session's immutable takes and chunks; cast audio is recorded in an immutable `performance_takes` table that points at content-addressed WAV assets. Playing a performance only reads audio. A passage the performance lacks waits while its job runs, and otherwise stops playback with an explanation. Cast performances never change the Studio's current takes. Unanalysed chapters, unassigned speakers and characters without a voice for the chosen provider use the narrator.

**Reason:** The owner wants to process chosen chapters once, with a narrator or the cast, and listen later with no waiting and no new requests. Sessions already pin a narrator; the Studio's current takes follow cast edits, so a performance needs its own record of which audio it used.

**Consequences:** Later cast or voice edits do not change a finished performance; resume uses its cast snapshot. A source change drops only the affected passages, which resume regenerates. Gemini one-narrator performances run chapter jobs one after another, paced by the usual request limits, and stop at the daily limit for a manual resume. There is no dollar cap in v1: the owner caps spending on the provider platform. One job per book still applies, so other narration for that book waits while a performance processes, and live listening does not join a performance's chapter job; saved audio always plays. Up to three performances of different books process at once. Archiving hides a performance and keeps its audio.

**Revisit when:** Exports should include performance audio, performances should resume automatically after the daily reset, or listening and processing need to run side by side for the same book.

