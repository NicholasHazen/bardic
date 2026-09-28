# Roadmap and open decisions

Implementation inventory reviewed **September 27, 2026**. This document separates working features from unfinished capabilities and research options. Priorities describe a proposed order of work, not release dates or fixed commitments. The implementation and [validation record](VALIDATION.md) take precedence over an older planning document.

Start with the [documentation index](README.md), [architecture](ARCHITECTURE.md), [data model](DATA-MODEL.md), and [decision record](DECISIONS.md) for the implemented design. Contributor workflows are in [DEVELOPMENT.md](DEVELOPMENT.md).

The project goal remains a local application that turns supplied fiction ebooks into reusable analysis, directed character performances, and a synchronized reading experience. Immediate single-narrator listening is also supported. Neither workflow requires making a complete enhanced audiobook first.

## Status vocabulary

| Status | Meaning |
| --- | --- |
| Implemented | A code path and user/API workflow exist. This does not establish live access to every provider or quality on every book. |
| Partial | Useful foundations exist, but the broader capability has a specific limitation below. |
| Planned | Discussed follow-up that has not been implemented. It still needs a scoped design and acceptance tests. |
| Optional research | An alternative to evaluate when evidence justifies it; no selected dependency or commitment. |
| Engineering follow-up | A proposed reliability or maintenance improvement derived from current limits, rather than an additional user requirement. |

## What is already available

| Capability | Status and current boundary | Evidence / implementation |
| --- | --- | --- |
| Bardic naming | Implemented: `bardic` package, Bardic browser branding and `BARDIC_` configuration. Legacy launcher, settings, library path and browser preferences remain usable; retained export schemas keep their identifiers. | [Upgrade behavior](OPERATIONS.md#upgrading-from-spin-tails) |
| Local application | Implemented: Python/FastAPI, browser UI, SQLite and local files. Opt-in `.local` access from the owner's other devices on a trusted network, without authentication. `./bardicctl` runs it as a macOS LaunchAgent and starts isolated development servers; no container image. No hosted account or frontend build system. Desktop packaging is not implemented. | [Operations](OPERATIONS.md), [app](../bardic/app.py) |
| Reading and studio workspace | Implemented: minimalist responsive shell, searchable bookshelf, keyboard-operated book tabs in lifecycle order (**Read & listen · Analyze · Cast · Script & record · Details**, 2026-09-28) with a lifecycle strip (one state per stage, one Next, expandable stage cards), hash routes with back/forward, a live breadcrumb, **Voices** as an app-level page, a reader chapter selector, one narrator surface (the **Choose your narrator** sheet: choosing is a draft until **Use this narrator**; **More options** holds only Play, Prepare ahead and chapter status; a bottom sheet on phones) and secondary controls behind disclosures. The older phase runner is no longer shown; the Studio's analysis provider select (which rewrote the app-wide default) is gone. Play with **Full cast (Studio takes)** selected reveals setup when a take is absent. New books start with one narrator. A text tap or Enter moves the reading place only (it jumps while playing); paid narration is labelled and asks once per book per browser session; Studio **Narrate book/scene** with Gemini or Breeze shows an estimate before confirm (2026-09-28). Reading and optional directed production have separate entry paths. Target-device support still needs the platform validation described in R11. | [Try it](../README.md#try-it), [frontend contracts](DEVELOPMENT.md#frontend-namespace-contracts), [workspace](../bardic/static/index.html) |
| UI foundation | Implemented foundation (2026-09-28): `tokens.css` (semantic colour, tone, type, space, radius and control tokens; legacy names aliased), eight primitives in `components.css` and `ui.js` (button sizes, badge and `statusTone`, callout and message, section head, steps, consent, choice, `fmt`), `/static/kitchen-sink.html`, a five-count drift ratchet and token contrast tests. Section heads replace the tab taglines, and a copy lint (`tests/copy_lint_test.js`) checks banned terms, heading punctuation and step names on primary surfaces. Most existing screens have not moved to the primitives yet; they migrate when rewritten. The Analyze workflow (phase 3a, 2026-09-28) groups the free Prep steps, offers **Try again** after a run that ended early, filters results (no speaker, low confidence, BookNLP disagrees, your edits) with an Evidence column and **Show in text**, stores owner-authored saved step settings with the app settings, hands off to Cast and the script after Speakers & delivery, and opens the step the lifecycle strip names. The listening status model, player pill and the move of expert narration controls to Settings → Narration → Advanced shipped with phase 4 (2026-09-28). Script & record (phase 3b, 2026-09-28) shows one chapter at a time with **Needs a look** chips and counts (chapter and book), saves edits on change (one field per request, failures kept with Try again), assigns one speaker to several passages, and opens at a passage from Analyze's **Show in text**. Still open from the redesign plan: evidence quotes in the Speakers & delivery rows of a candidate version, a bulk passage-edit endpoint (bulk assignment sends one request per passage) and keyboard-only review (next/previous flagged passage). | [UI guide](UI-GUIDE.md) |
| EPUB/TXT library | Implemented: retained originals, canonical prose, source spans, metadata, local cover extraction, reversible removal and restore. No DRM removal, permanent purge, or original EPUB layout renderer. | [Library behavior](LIBRARY-LISTENING-RESOURCES.md), [importer](../bardic/importer.py) |
| Real chapter labels | Implemented: EPUB navigation/NCX, headings, semantic section kinds, logical anchors and source-preserving structure repair. Complex books can still require review. | [Structure](STRUCTURE.md) |
| Multiple analysis providers | Implemented: Gemini, OpenAI and Anthropic adapters; separate discovery/detailed model preferences, dropdowns, custom IDs and explicit model inventory refresh. Inventory visibility does not prove inference compatibility or credit. | [Providers](ANALYSIS-PROVIDERS.md), [catalog](../bardic/model_catalog.py) |
| Account check | Implemented: explicit small text inference check with safe error categories. No universal credit-balance API or automatic account polling. | [Account checks](ACCOUNT-CHECKS.md) |
| Non-LLM preprocessing | Implemented: structure, speech tags, exact-name indexing, counts, chapter spread, uncertainty and heuristic analysis priority. These are not a learned coreference or physical-presence model. | [Preprocessing](../bardic/preprocessing.py) |
| Cheap initial discovery | Implemented: separate economical model per provider, bounded chapter excerpts, whole-book coverage and durable accepted units. In the step pipeline, a self-hosted OpenAI-compatible server (Local LLM) can run discovery, profiles and directing at no per-request cost (2026-09-28); the phase controls remain cloud-only. | [Progressive analysis](PROGRESSIVE-ANALYSIS-PLAN.md), [pipeline](../bardic/progressive.py) |
| Character evidence and profiles | Implemented: exact source references, aliases, bounded evidence sampling, effort guided by frequency/dialogue/spread/uncertainty, provisional/current/stale profile states and human edit preservation. Profile accuracy still requires review. | [Chapter analysis](CHAPTER-ANALYSIS.md), [artifacts](ARTIFACTS-AND-STORAGE.md) |
| Scenes and performance directions | Implemented: scene structure, attributed speakers, tone, subtext/summary, passage directions and cues. Unknown speakers remain explicit. Full world-state and physical scene presence are not inferred. | [Analysis](../bardic/analysis.py), [story graph](../bardic/pipeline_view.py) |
| Cross-book memory | Partial: explicit series membership, reading order, confirmed identity links, source-validated earlier-volume observations and profile invalidation. There is no automatically authoritative series-wide character biography or contradiction-resolution editor. | [Series memory](ARTIFACTS-AND-STORAGE.md#series-knowledge-is-additive), [series](../bardic/series.py) |
| Incomplete collections | Implemented: missing/planned placeholders and supplied-volume scheduling. Missing books contribute no invented knowledge. Archived books are excluded from current earlier-book context. | [Series/library behavior](LIBRARY-LISTENING-RESOURCES.md) |
| Series processing | Implemented on the step pipeline (2026-09-28): one step across every supplied book, one book at a time in reading order, an aggregated estimate (unknown price stays unknown) and one series fingerprint as consent. Each book is re-checked against its confirmed plan before it starts; child pipeline runs, stop-on-failure, reusable validated units. Not yet: pipeline-native series memory (accepted results from earlier volumes do not feed later ones), per-book parallelism. The phase engine it used is still present for the Studio controls and will be removed separately. | [Coordinator](../bardic/series_processing.py), [design](ANALYSIS-PIPELINE.md#series-runs) |
| Directed narration | Implemented: Gemini, installed macOS voices and a self-hosted Breeze server, one speaker passage per take, per-provider cast voices, editable voice/direction, forced alternate takes (a new passage seed for Breeze) and content-addressed WAV retention. Breeze voices are pinned by server revision and checked live before each request. Device voices ignore acting directions; cloud and Breeze timbre and delivery still need auditioning. | [Voice research](RESEARCH-VOICE.md), [audio](../bardic/audio.py), [take archive](../bardic/take_archive.py) |
| Voice library | Implemented: library-wide voices with immutable versions, Breeze design/clone/rename/delete and server-voice import, Gemini design/list/delete (unverified live), Breeze default voice, Cast assignment with Default and Create new voice…, append-only voice event log. | [Voices and casting](OPERATIONS.md#voices-and-casting), [voice library](../bardic/voice_library.py) |
| Voice examples | Implemented: contextual or original demo samples beside simple narrator, cast voice choice (Gemini, device or Breeze, including library voices) and passage speaker controls; 400-character source-prefix cap, unsaved-choice snapshots, independent retained cache, shared player/speed and preserved reader bookmark. No narration monetary allowance or multi-take comparison UI. | [Examples](LIBRARY-LISTENING-RESOURCES.md#contextual-voice-examples), [preview archive](../bardic/voice_previews.py) |
| Name pronunciation | Implemented (2026-09-28): per-book respellings applied to the text sent to every narrator (Studio, simple listening and chunks, cast performances, voice examples), recipe-scoped so only passages containing the word change, with a Cast-tab editor, cast-name suggestions and auditions of unsaved respellings in the word's own sentence. No speak-to-respell, automatic proposals, series sharing or Gemini trial. | [Pronunciations API](API.md#pronunciations), [respelling trial](RESEARCH-VOICE.md#name-pronunciation-respelling-trial) |
| Simple listening | Implemented: one narrator, shared panel/footer playback and speed, equivalent-speech cache across passages/books, passage highlighting, pause/stop protection and continuous listening across chapters (optional chapter-boundary stop). Device voices and Breeze use a warmup, rate-aware lookahead and chapter preparation of single passages. Gemini uses a server chapter job with quota-paced multi-passage chunks (quick-start steps, up to three requests at once), gapless chunk playback with estimated passage timing, progress/ETA/safe-speed estimates and reader marks. Gemini queues the next chapter about 10 minutes ahead while playback runs. A reader view (full-screen text, themes, text size, follow-the-narration, opt-out screen wake lock) and a listen sheet (narrator, voice, speed, buffering) are implemented. Phase 4 (2026-09-28): one listening status (`listen-status.js`) drives a player pill in the workspace and reader view and announces changes of state only; the scrubber shows prepared audio; ±15 s, a sleep timer (minutes or end of chapter, pausing exactly as Pause does) and lock-screen seek/position/chapter controls; narrator changes in the sheet send nothing until **Use this narrator**, and each narrator's session is remembered so switching back reuses its audio; the reader text is one tab stop with arrow-key passage navigation; EPUB `dc:language` is saved as `book.language` and picks the Mac voice menu. Saved performances (chosen chapters, one narrator or the full cast, prepared ahead by a server job and played without new requests) are implemented. No automatic resume after the daily reset, streaming, audio ZIP for simple or performance audio, first-run narrator wizard, per-chapter readiness in the contents list, server-side reading position, or a money estimate for Gemini narration. | [Listening](LIBRARY-LISTENING-RESOURCES.md#independent-simple-listening), [listening module](../bardic/listening.py) |
| Synchronization and audio QA | Partial: sample-based passage boundaries and checks for corrupt, truncated, empty or all-zero audio. No word alignment, independent speech verification, pronunciation scoring or performance-quality guarantee. | [Synchronization research](RESEARCH-PIPELINE.md#passage-synchronization-now-word-alignment-later) |
| Durable analysis and replay | Implemented: bounded transport/evidence retries, per-attempt reservations, accepted-unit caches, checkpoint recovery, rejected-response inspection and immutable artifact lineage. Lost pre-history outputs cannot be reconstructed. | [Storage](ARTIFACTS-AND-STORAGE.md), [processing](../bardic/processing.py) |
| Search and graphs | Partial: literal lexical FTS5 search, earlier-volume filtering before result limits, typed story graph and artifact dependency graph. No vector index, learned semantic retrieval, interactive world timeline or separate graph database. | [Search](../bardic/search.py), [storage decision](ARTIFACTS-AND-STORAGE.md#storage-decision) |
| Step pipeline with review | Implemented foundation (2026-09-27): **Analysis** tab with steps for chapters & titles, census, discovery, profiles and speakers & delivery; per-step provider/model (the thoroughness choice) and review gate; plan preview with fingerprint; one step per run in the UI (the API also runs several steps, serially or side by side) with bounded parallel units; candidate versions per scope, diff/agreement between versions, accept, reject and rollback; per-field manual edit locks; outside changes captured as versions. Self-hosted providers (2026-09-28): a Local LLM for every model step, a read-only BookNLP **Quote attribution** step whose result checks every directing provider's speakers (raising or capping confidence), and a Novel Analyzer or BookNLP as alternative directing providers. Status coherence (2026-09-28): each step shows one state from a tested function (staleness is a note, not a state), the tab opens on the first actionable step, a disabled **Run this step** says why and links to Providers & settings, the preview scrolls into view, a completed run offers **Next**, and versions read Accept / Set aside / Restore. Planned steps are listed in R14. | [Analysis pipeline](ANALYSIS-PIPELINE.md), [pipeline package](../bardic/pipeline/) |
| Legacy phase ("Classic") engine removal | In progress (2026-09-28): stage 1 of 4 done. The step pipeline and shared modules no longer import the phase engine, and the request builders moved to `pipeline/prompts.py` byte for byte. Stage 2 (evidence projection), stage 3 (delete code paths) and stage 4 (drop data, needs the owner's go) remain. | [Classic removal](CLASSIC-REMOVAL.md), [isolation test](../tests/test_legacy_isolation.py), [prompt identity test](../tests/test_prompt_identity.py) |
| Resource visibility | Implemented: per-stage/run attempts, retries, cache reuse, reported tokens, measured elapsed/local-thread CPU, audio/file volume, estimates and explicit unknowns. No account-wide invoice reconciliation or narration spending guard. | [Resource ledger](LIBRARY-LISTENING-RESOURCES.md#resource-ledger), [resources](../bardic/resources.py) |
| Playback troubleshooting | Implemented: allowlisted browser/server events, job/passage correlation, bounded local retention and a Settings JSON download. Best-effort diagnostics are a rotating log, not a complete immutable audit. Older unrecorded errors remain unknown. | [Playback diagnostics](LIBRARY-LISTENING-RESOURCES.md#playback-diagnostics), [diagnostics](../bardic/diagnostics.py) |
| Portable output | Partial: analysis JSON/JSONL ZIP with lineage and source, plus enhanced-audio ZIP with takes, complete chapter WAVs and timeline. No M4B, EPUB Media Overlays, analysis-bundle import or full take-comparison editor. | [Export contents](ARTIFACTS-AND-STORAGE.md#visibility-and-portability), [operations](OPERATIONS.md#exports-are-not-a-complete-backup) |

## Priority 1: trustworthy listening and controlled generation

### R1. Word alignment and independent speech verification — planned

This is the largest gap between highlighting the passage assigned to a clip and verifying the words actually spoken. Treat transcription comparison and forced alignment as separate stages: a forced aligner can assign times to supplied text without proving that every word was spoken correctly.

Acceptance criteria:

- Evaluate candidate aligners on short narration, dialogue, names, numerals, Unicode punctuation and intentionally omitted/repeated/substituted words. Record platform, models, elapsed time and resource use.
- Align each immutable source passage against a specific audio **asset ID**, retaining engine/model/version and source hash. Replacing a take invalidates its alignment without deleting the old result.
- Keep timestamps ordered and bounded by measured audio duration. Unaligned words remain explicitly unaligned; do not interpolate convincing-looking timestamps and call them measured.
- Preserve canonical reading text and offsets. A verification transcript is another artifact, never replacement book prose.
- Show reviewable speech discrepancies; require a bounded, explicit rerender decision. Failed verification must not trigger an unlimited paid retry loop.
- Reader and exports use word timing only when valid; otherwise retain passage highlighting.

The dated [word-highlighting proposal](WORD-HIGHLIGHTING.md) recommends benchmarking a local alignment-only worker before choosing a dependency. It documents source/audio-hash contracts, Apple Silicon uncertainties, and why the current Gemini TTS output contract is insufficient by itself. WhisperX and Montreal Forced Aligner are candidates; stable-ts is now archived and needs a maintenance assessment. No aligner is installed, and passage fallback remains required.

### R2. Performance quality, auditions and voice consistency — partial / planned

Character voices, performance notes and bounded **Hear example** auditions work now. Examples snapshot unsaved selections, use an exact source prefix or demo text, reuse independent cached audio and leave the reader bookmark/cast selections intact. Each Gemini sample can incur charges; this is a per-click text bound, not a dollar allowance. The remaining goal is a production workflow that helps the user judge consistency and acting quality across long fiction.

Acceptance criteria:

- Extend the implemented single-example auditions to a scene/model/voice comparison workflow under an explicit small monetary allowance. Exact preview recipes/takes and reported usage already persist; comparison, alternate-take selection and a narration spending guard remain open.
- Offer a take-comparison/selection UI without replacing original files or losing earlier performance notes.
- Evaluate longer contextual takes against current short passages for seams, pacing, fidelity and retry cost. Keep passage-to-audio mapping explicit if chunk size changes.
- Voice design retains the provider ID, creation recipe, audition, creation/expiry metadata and user selection, and handles a deleted, changed or unresolvable voice without silently substituting one (implemented 2026-09-27 in the voice library; keep this rule for later providers).
- Pronunciation overrides are implemented (2026-09-28) under this rule: a per-book lexicon of respellings changes only the text sent to the narrator, recipes record the entries used, and `pronunciation.to_source` maps sent-text offsets back to source code points. Non-speech events, if added, must follow the same rule. Do not silently add inline tags or spoken words to the source.
- Pronunciation follow-ups (not implemented): speak a name and have it turned into a respelling (a local phoneme recognizer, or one bounded audio-understanding request, then an audition beside the recording); the planned lexicon analysis step that proposes names and respellings; sharing entries across a series; per-occurrence exceptions for a word that is sometimes a name ("Rose"/"rose" at a sentence start); a Gemini listening trial; and using `to_source` once Breeze sentence timing or word highlighting is consumed.

Breeze, a self-hosted narration server, is implemented for enhanced narration, simple listening and voice examples. Its voices are pinned by revision and checked live before each request; only cloned voices narrate. A library-wide **voice library** (Voices tab) is implemented as of 2026-09-27: Breeze voices can be designed from a description and sample line (free previews, auditioned and saved as a cloned voice), cloned from an uploaded recording with a consent confirmation, renamed, iterated into new versions and deleted; server voices are imported on check. Gemini prompted-voice design, listing and deletion are implemented behind a per-create cost confirmation, but have been tested only against a fake API, never a live account. The Cast tab assigns voices per provider with a **Default** option and a **Create new voice…** path that carries the character's text into the Voices tab. Other TTS adapters, including the previously researched ElevenLabs option, remain optional. OpenAI/Anthropic support for analysis does not imply an implemented narration adapter.

Remaining voice-library work (not implemented as of 2026-09-27):

- **Character descriptions from analysis and alias merging.** Build voice descriptions from analysed evidence and merge characters whose nicknames analysis missed, before creating voices. This is separate analysis work; today a draft uses the saved character description, direction and first attributed line.
- **Multiple narrators.** Only the built-in Narrator (plus Unassigned dialogue) is treated specially; additional narrators are ordinary characters.
- **Gemini voice replication** (cloning from reference and consent audio) and a live Gemini design check.
- **Adopting a server-side change** to an imported Breeze voice as a new version; today a changed server voice is reported and refused at render time.
- **Restore takes after switching back.** After a version or default switch and an intervening edit, the earlier take returns only after an explicit render (which reuses the archived WAV without a request); a one-click restore is not implemented.
- **Voice design as background jobs.** Preview generation and Gemini creates are synchronous requests; closing the page does not stop Breeze preview work, and there is no cancel control.

Remaining Breeze work, in intended order (none implemented as of 2026-09-27):

- **Chunked chapter listening with sentence-anchored timing.** Send several passages per request (the server accepts up to 10,000 characters) and place passage boundaries from the server's validated sentence timestamps, falling back to pause alignment only inside a sentence. Benchmark 10 s, 60 s and 180 s requests first to set chunk sizes and the playback speed generation can sustain; current measurements are one or two samples each.
- **Server jobs for bulk rendering.** Whole-chapter or whole-book work through the server's background job queue, polled (Bardic is loopback-only, so webhooks cannot reach it), with an idempotency key derived from the recipe and reattachment after restart.
- **Streaming playback.** The server streams first audio within about 0.2 s; using it for playback needs a new player path and an explicit policy for audio that has not yet been validated and published.
- **Vocal events.** The server performs inline markup such as `(sigh)`. It needs the separate performance representation required above; literal markup already present in source text is flagged on the take, not rewritten. (Respelling for pronunciation is implemented for every provider; see R2.)

### R3. Narration allowances and preparation — partial / engineering follow-up

Current analysis guards do **not** cap enhanced narration, simple listening or account checks. Simple listening now has a visible short warmup and bounded speed-aware lookahead for device voices, and quota-paced chunked chapter jobs for Gemini that stop at the configured daily request count. Matching speech is reused locally, duplicate active passage requests join one job, and preparation stops on error/cancellation. These bounds are not a monetary ceiling, and sustained 2.5× playback has no provider-throughput guarantee.

Acceptance criteria:

- Add an explicit narration estimate/allowance covering every attempted take, including force regeneration, failed or uncertain requests, and warmup/lookahead/chapter preparation.
- Decide whether allowances are per action, book, series or account before presenting a “total budget.” Explain which historical spend is included and how unknown prices stop guarded work.
- Retain successful takes when a limit is reached. Cache reuse must add no new provider charge, and request uncertainty must not become zero cost.
- Chapter preparation already shows its passage scope/provider, requires an explicit start, uses one request at a time, and preserves completed takes when stopped. A durable preparation plan with restart-resume state, whole-book scope and monetary estimates remains future work; the current browser queue does not start itself after restart.
- Define whether simple-mode audio has a separate export or joins a generalized export selector. It must remain independent of enhanced cast assignments and takes.

### R4. Representative long-book validation — engineering follow-up

Past short auditions and one-chapter analysis are useful evidence, not a whole-book quality claim. Build a repeatable evaluation set from original/public-domain or otherwise permitted test material; do not commit private ebooks or excerpts.

Acceptance criteria: measure source coverage, alias errors, speaker ambiguity, evidence repairs, profile staleness, narration fidelity, voice consistency, latency and cost; test interruption and restart at realistic scale; distinguish automated checks from human listening judgments. Live paid tests remain explicitly selected, small and bounded until the user chooses a larger run.

## Priority 2: stronger interpretation without brute-force processing

### R5. Local model adapter — implemented in the step pipeline (2026-09-28)

The owner's runtime is vLLM serving Qwen3.6-35B-A3B through the OpenAI Responses API with strict JSON schemas. It is the **Local LLM** provider for discovery, profiles and directing in the Analysis tab, with the cloud adapters' validators, evidence repair, cancellation, recipe versioning, caching and request/token metering (at a $0 price). A structured-output capability check, local resource metrics and a benchmark on representative fixtures (R4) remain open, as does use from the phase controls. Series runs use the pipeline, so they can use it. The **Local draft** option is still deterministic heuristics, not an LLM.

Acceptance criteria:

- Select the user's actual runtime, endpoint, model and hardware constraints; do not assume or install a runtime just because another adapter supports a similar API.
- Implement the same structured-result, evidence-validation, cancellation, recipe-versioning and cache contracts as cloud providers.
- Expose local model discovery/configuration separately from cloud credentials, with an explicit structured-output capability check.
- Benchmark cheap discovery first against the same source/evidence fixtures. Record inference time and supported local resource metrics; zero provider charge does not mean zero compute.
- Preserve unknown attribution rather than accepting malformed or unsupported evidence to make a smaller model pass.

### R14. Analysis pipeline steps beyond the foundation — planned

Partly started 2026-09-28: BookNLP quote attribution supplies the local tag-and-alternation pass for speaker attribution and a disagreement list (see [self-hosted providers](ANALYSIS-PIPELINE.md#self-hosted-providers)). A targeted re-fix of the lines it disputes is not implemented.

The step contract makes each of these an independent, versioned step. The recommended order, method and model tier for each are in [planned steps](ANALYSIS-PIPELINE.md#planned-steps): cast identity, split speaker attribution with a targeted low-confidence re-fix, fused line delivery with a fixed emotion vocabulary, pronunciation lexicon, utterance type from retained italics, narrator/POV, and local consistency checks.

Acceptance criteria:

- Each step declares disjoint owned fields, versions its recipe, satisfies `apply(capture) == identity`, and is tested with a fake provider for a cold run, cache hit, failure after a durable unit, cancellation and a manual edit lock.
- Cast identity redirects character IDs rather than deleting them, and never merges confirmed series links or reviewed characters.
- Model tier choices per step are justified by version comparisons on the R4 evaluation set, not assumed.
- Rollback reattaches retained audio takes whose recipe matches again. Staleness narrows from whole input steps to the scopes a version actually read.

### R6. More effective low-cost preprocessing — partial / optional research

Separate cheap discovery models, deterministic structure, names/speech tags, priority metrics and accepted-unit reuse are already implemented. The next question is whether additional NLP materially improves recall or reduces expensive work.

Acceptance criteria: compare an optional NER/coreference or richer quotation parser with the current baseline; include rare speakers, ambiguous aliases, pronouns and misleading capitalized phrases; preserve exact source anchors; measure total model requests and repair rates, not only speed of the first pass. A new dependency should show a useful gain before becoming required. Heuristic “importance” remains an allocation signal, not a claim about literary importance.

### R7. Retrieval and provider execution optimizations — optional research

Keep SQLite authoritative. FTS5 and bounded evidence sampling are available today. A vector index, neural retriever or separate database is not a prerequisite for processing this library.

Acceptance criteria for semantic retrieval:

- Establish a lexical baseline and a measured failure it cannot handle. Evaluate relevance, source fidelity, cost and latency on that workload.
- Filter by allowed books, confirmed identities, reading order and current source versions **before** top-k selection. Preserve exact spans and distinguish similarity from confidence.
- Treat embeddings as rebuildable derived data. Record model/version/dimensions/text hash; model changes require an explicit compatible migration or re-embedding.
- If choosing `sqlite-vec`, assess its pre-v1 compatibility risk and pin/test the selected version. Use an additional embedded store only when its benefits justify lifecycle and backup complexity.

Acceptance criteria for provider caching/batch execution:

- Benchmark provider prompt caching without confusing it with local reuse of already validated output; retain cache read/write usage and pricing assumptions.
- Add batch submission IDs, durable reconciliation, result validation and bounded failed-unit retries. Reopening the app must not submit the same batch again.
- Account for accepted/submitted work and cancellation races before presenting a budget guarantee. Batch jobs may finish or incur charges after a cancellation request.
- Keep synchronous execution as a selectable path and avoid automatic cross-provider fallback.

See [storage research](ARTIFACTS-AND-STORAGE.md#primary-references) and the dated [caching/batch research](PROGRESSIVE-ANALYSIS-PLAN.md). None of these options is currently installed as a new execution engine.

### R8. Series profiles, contradictions and richer story maps — partial / planned

Earlier linked observations already improve book-local profiles, with missing/later volumes excluded. A complete series biography, temporal character evolution and a full scene-presence map remain broader work.

**Gap (2026-09-28):** only the older phase engine writes those observations and the Cast references they come from; step-pipeline runs, including series runs, write none. The planned follow-up — project accepted step evidence into references, read earlier volumes' accepted evidence for series context, and staged consent for series runs — is designed in [series memory plan](SERIES-MEMORY-PLAN.md). Its evidence projection is also stage 2 of removing the phase engine. **Implemented 2026-09-28:** accepted discovery, profiles and directing evidence (and cast-name mentions) now rebuild `character_references` in each pipeline decision's transaction ([evidence projection](ANALYSIS-PIPELINE.md#evidence-projection)). That fixes the Cast references. **Implemented 2026-09-28 (plan sections 2–4, contract 0.3.0):** later volumes' profiles read earlier volumes' accepted evidence through confirmed links, and a rollback there marks the later profile stale ([series memory](ANALYSIS-PIPELINE.md#series-memory)). Series runs mark such profiles context-pending with an "up to" estimate and a consent fingerprint that leaves out earlier-volume context, so they complete across linked books; with "Hold for my review", a series pauses after a book a later book reads, until the owner reviews it and resumes. Identity-link suggestions (exact name or alias matches with earlier linked characters) are listed for confirmation and never applied automatically. The observation append stays off; history is in artifacts. Still open below: contradictions and temporal evolution, a scanned-coverage view, and richer story maps.

Acceptance criteria:

- Provide an explicit review flow for identity-link suggestions; namesakes never merge automatically. Preserve the evidence and the user's link decision as versioned inputs. *(Suggestions and confirmation implemented 2026-09-28; there is no dismiss action, and suggestions match exact names or aliases only.)*
- Show how much of the supplied collection has been scanned and which volumes are missing. “All supplied books processed” must not mean “complete series knowledge.”
- Represent enduring traits, temporary scene emotions and traits that change over time separately. Retain contradictory evidence and its reading-order scope rather than overwriting it with one confident sentence.
- Let readers inspect which observations informed a profile and why it became stale after a source/link/earlier-book change.
- Extend the current chapter/scene/passage and dependency graphs only with evidence-backed types. Mention, attributed speech and physical presence remain distinct; locations, relationships and timeline events need their own uncertainty and provenance.
- Export these additional artifacts without requiring narration or a separate graph service.

## Priority 3: portability, library lifecycle and maintainability

### R9. Exports, importable bundles and backup tooling — partial / planned

Acceptance criteria:

- Add M4B chapters/metadata and EPUB Media Overlays against measured passage or validated word timing. Round-trip source IDs, chapter order and audio duration; label partial output explicitly.
- Design an analysis-bundle import with schema/version checks, content hashes, dependency closure, identity-collision handling and previewable changes. Imported history must not invent missing provenance or execute instructions from payloads.
- Provide a consistent full-library backup/restore workflow that includes SQLite, originals and all audio stores, including voice examples. An analysis ZIP alone is not a complete media backup.
- Preserve the independent simple-listening mode in exports and restores; its audio must never masquerade as an enhanced character performance.

[EPUB 3.3](https://www.w3.org/TR/epub-33/) and [SQLite's backup API](https://sqlite.org/backup.html) are the existing reference points. Exact portable-reader/device support remains to be tested.

### R10. Permanent purge and storage management — planned

Removal is currently reversible and reclaims no storage. Automatic cache pruning and permanent deletion are not implemented.

Acceptance criteria: preview exact affected books/artifacts/assets and estimated reclaimed bytes; require explicit destructive intent; preserve assets/dependencies still used elsewhere; account for earlier-book evidence retained by later profiles; maintain transaction/recovery behavior; protect current and alternate takes according to a documented retention rule. The append-only artifact model needs a designed retention/migration mechanism, not ad hoc SQL deletion. Keep archival restoration separate from destructive purge.

### R11. Packaging, platform support and operational hardening — engineering follow-up

The development application runs locally from source. macOS device narration has a host-specific implementation; cloud-only paths and mobile layouts do not establish a tested release matrix.

Acceptance criteria: choose supported operating systems and installation/update/uninstall behavior; verify packaged startup, data-directory selection, `.env`/credential behavior, audio dependencies, shutdown and migrations on each target; retain a recoverable backup before data changes; test long libraries and concurrent series runs with bounded UI payloads. A desktop shell, bundled Python runtime and browser-only distribution remain choices, not selected dependencies.

### R12. Accounting and quality-of-service improvements — engineering follow-up

Current resource tracking is useful local evidence, not provider billing. Future work may include narration guards from R3, versioned price maintenance, invoice reconciliation where a provider exposes it, and more complete local resource measurements.

Acceptance criteria: preserve known/unknown distinctions; avoid counting requests in both analysis attempts and generic operations; document whether new CPU/memory measurements cover a thread, process, subprocess or GPU; never infer a credit balance from a quota error. Optional organization administration APIs require their own selected scope and credentials. Do not expand a normal API key check into an unrequested account-wide integration.

### R13. Reproducible maintenance and CI — engineering follow-up

Acceptance criteria: keep locked dependencies and offline regression tests reproducible; ensure CI fixtures use original/synthetic material and no credentials; test migrations/recovery on temporary libraries; link changes to the current architecture, operations and validation records. Separate opt-in live/model/device checks from the default suite, with explicit resource limits. Git tracks source and documentation; it is not a backup of the excluded library and credentials.

### R15. Published API contract, dedicated clients and a portable server — partial (2026-09-28)

The owner intends to move the server off Python eventually and to build dedicated clients. Implemented so far:
- a complete, checked-in OpenAPI contract ([contract/](../contract/)) covering all 100 operations as of contract 0.1.2;
- a readable reference and a versioned changelog;
- test-suite validation of every API response against the contract;
- agent rules for keeping it current ([API workflow](API-WORKFLOW.md)).

The design and staging are in [the client/server proposal](CLIENT-SERVER-CONTRACT.md). Defects found while writing the contract are in [API known issues](API-KNOWN-ISSUES.md), tracked in [issue #17](https://github.com/NicholasHazen/bardic/issues/17). A development-only check (`npm run contract:codegen`) verifies that openapi-typescript output compiles strictly.

Acceptance criteria for the remaining work:
- machine-readable error codes;
- a contract-version handshake in `/api/status`;
- `GET /api/jobs/{id}`;
- a single client HTTP module, with Node tests that check requests against the contract;
- authentication, CORS and a threat model before any client is served from another origin;
- a black-box HTTP conformance suite, runnable against any base URL, as the acceptance test for a replacement server;
- a decision on each known issue (keep or fix) before the port;
- consolidation of duplicated response shapes through the changelog's breaking-change rules.

## Decisions deliberately left open

| Decision | Current position | Evidence needed before selecting |
| --- | --- | --- |
| Repository visibility and application hosting | Source remote selected: [NicholasHazen/bardic on GitHub](https://github.com/NicholasHazen/bardic). The owner requested the initial `main` push. No application hosting target is selected. | Owner preference for visibility and deployment, handling of project assets and intended collaboration. |
| License | No project license selected. Do not infer a license from dependency licenses or source availability. | Owner's distribution goals and dependency compatibility review. |
| CI platform | Not selected. Local tests exist. | Repository host, required OS/device checks, secret-free default workflow and optional paid-test policy. |
| Packaging and support matrix | Source-based local app; no committed desktop installer/platform matrix. | Target devices, update strategy and actual platform validation. |
| Local model/runtime | vLLM with Qwen3.6-35B-A3B on the owner's DGX Spark, plus BookNLP and a Novel Analyzer on the same machine (2026-09-28). | A structured-output benchmark against R4 fixtures, and whether the phase controls should offer it. |
| Alignment engine | Several researched candidates; none integrated. | R1 fidelity/resource/platform evaluation. |
| Semantic index | FTS5 now; vectors optional. | R7 retrieval benchmark and maintenance cost. |
| Additional narration providers | Gemini, macOS and self-hosted Breeze now; alternatives optional. | Provider access, fidelity, timing, cost and adapter tests. |
| Unified spending ceiling | Analysis allowances implemented; total narration/account ceiling absent. | R3 allowance scope and unknown-cost policy. |

Keep these decisions explicit when turning roadmap items into work. Recheck dated external APIs, model IDs, prices and package compatibility at implementation time. Historical live checks in [VALIDATION.md](VALIDATION.md) describe their recorded run only; they do not establish current key access, credits, whole-book quality or a release support guarantee.
