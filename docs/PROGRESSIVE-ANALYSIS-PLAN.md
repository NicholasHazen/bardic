# Progressive, series-aware analysis

> **Historical record.** The engine this page describes was removed in Classic removal stage 3 (2026-09-28): its modules, routes and panel are gone. Analysis now runs only on the step pipeline; see the [analysis pipeline](ANALYSIS-PIPELINE.md) and [Classic removal](CLASSIC-REMOVAL.md). File and route names below refer to code that no longer exists.

Research and implementation plan — September 27, 2026.

This is the historical design and milestone record. The opening findings describe defects before this update; the implementation section records their resolution. For today's module boundaries and remaining work, use [architecture](ARCHITECTURE.md), [development](DEVELOPMENT.md) and the [roadmap](ROADMAP.md).

## Findings

The model settings currently use HTML datalists: filtering by the already-selected model hides other suggestions. Use actual model selectors with a custom-ID escape hatch, a dated compatible catalog, and explicit authenticated model refresh. A provider inventory does not necessarily establish structured-output compatibility or account credit.

The importer incorrectly uses EPUB spine items as numbered chapters when XHTML has no heading tags. The real test EPUB has both navigation and NCX labels; its fourth imported item is The Story Thus Far. W3C distinguishes spine reading order from the navigation hierarchy. Prefer TOC labels/anchors, then semantic headings/landmarks, with a neutral fallback. Repair existing metadata without changing source text, IDs, offsets, references, audio, or valid analysis outputs. [EPUB reading order and navigation](https://www.w3.org/TR/epub-33/)

Use local source structure, speech tags, unique-name matching, paragraph/word statistics, and stable hashes first. These supply candidate names and occurrence counts, not definitive identities or physical appearances. A neural NLP dependency is optional later; the existing regex pipeline is sufficient to start. [spaCy rule and phrase matching](https://spacy.io/usage/rule-based-matching/), [SQLite FTS5](https://www.sqlite.org/fts5.html)

Current inexpensive structured-output choices verified in official documentation are Gemini 3.5 Flash-Lite, GPT-6 Luna, and Claude Haiku 4.5. Preserve saved detailed-analysis models and make the scan model a separate choice. Model metadata and prices must be dated, and unknown/custom models must not receive invented costs. [Gemini models](https://ai.google.dev/gemini-api/docs/models), [OpenAI models](https://developers.openai.com/api/docs/models), [Claude models](https://platform.claude.com/docs/en/models/overview)

Provider prompt caching is best-effort and can charge for cache writes; it does not replay a validated result. Durable local unit caching is the primary saving. The three providers also offer discounted asynchronous batch processing, with tradeoffs in completion time and cancellation. Record that as a later executor option after budgets and unit persistence are reliable. [OpenAI caching](https://developers.openai.com/api/docs/guides/prompt-caching), [OpenAI Batch](https://developers.openai.com/api/docs/guides/batch), [Gemini optimization](https://ai.google.dev/gemini-api/docs/optimization), [Claude batches](https://platform.claude.com/docs/en/build-with-claude/batch-processing)

## Implementation sequence and acceptance

1. **Real model choices.** Full selects for detailed analysis, cheap discovery, and narration, with custom IDs. Dated curated catalog available offline; explicit read-only account inventory refresh. Preserve existing preferences and keys.
2. **Book structure.** Deterministic EPUB3/NCX/heading/semantic labels and section kinds; front matter, recaps, chapters and back matter stay distinguishable. Safely repair the imported real book and checkpoint metadata. Preserve all source spans and audio. Expose structure confidence/source rather than silently inventing chapter numbering.
3. **Free whole-book preprocessing.** Build a cached local census before paid work: text sizes, eligible sections, dialogue density, explicit speech-tag candidates, known-name references, chapter spread, and unresolved speech. Provide useful results before any API request.
4. **Progressive stages.** Separate whole-book discovery, profile building/refinement, and chapter direction. Use the selected provider's cheap scan model for discovery and its detailed model for profiles/direction. Avoid redoing discovery when only a profile or voice changes. Mark character knowledge provisional until eligible whole-book semantic coverage is complete; completion is coverage, not guaranteed correctness.
5. **Targeted profiles.** Preserve all source observations, rank analysis effort using dialogue/mentions/chapter spread/uncertainty, and sample varied evidence across the story within a bounded prompt. Provide basic treatment for rare speakers; spend more on frequent or ambiguous characters. Report priority as a work-allocation heuristic, not literary importance. Avoid a single unbounded whole-cast response or first/last-only evidence selection.
6. **Budgeted execution.** Preview uncached requests and estimated tokens/cost before running. Persist request attempts and actual usage when returned. Reserve request/output/token budgets before every paid attempt, including retries and repairs. Stop gracefully at the limit; resume keeps accepted units. Treat unknown usage or pricing explicitly; never promise an exact provider invoice or remaining balance. Do not automatically rerun an entire book or switch companies/models after failure.
7. **Series memory.** Let the user group owned books and specify reading/volume order without assuming the test book's ordinal. Store independent series identities and explicit links to each book's characters. Carry bounded, source-validated observations from earlier linked volumes into profile work; exclude later volumes, preserve disagreements and provenance, and never blindly merge same-name characters.
8. **Usable controls and verification.** Show stage/coverage/priority, scan versus detailed models, budget preview and pause/resume, series membership/mapping, and source links. Test failures, restart, no-duplicate requests, budget limits, evidence integrity, cross-book isolation, and later-volume exclusion. Check the real EPUB and existing library offline; use mocked APIs for cost-sensitive regression tests. Validate UI in the local browser.

## Architecture decisions

Keep Python/FastAPI, SQLite, and the existing browser UI. Add small modules for catalogs, structural metadata, local census, unit cache/request accounting, and series memory. Keep provider transports behind a common interface so a future local model adapter can implement the same structured-result contract; do not assume a local runtime, model, URL, or install one without that information.

A stage result is identified by its immutable source slice, stage/prompt/schema version, model/provider, and only the dependencies it actually consumes. Store accepted units separately from active run progress. Profile context changes invalidate affected profiles and direction, not the underlying source discovery. Human-reviewed identities, directions, and voice assignments remain authoritative.

Series character IDs are independent IDs: existing per-book IDs derived from names are not global identity. Evidence records retain book, chapter, source offsets, quotation, observation, provider/model, and temporal order. Profiles are derived summaries of that evidence. Old contradictions remain inspectable rather than being overwritten into a confident single assertion.

No paid whole-book run is part of development validation by default. The app should make costs and incomplete coverage visible before the user chooses to run it. Local preprocessing and catalog/structure fixes can be verified without generation charges.


## Implementation and verification

Completed September 27, 2026. The local app now exposes full model menus, independently saved fast/detailed models, deterministic structure labels, free census/coverage/priority, progressive stages, per-character profile freshness, guarded request accounting, and explicit series memory.

Discovery uses contiguous prose chunks of at most 24,000 characters, independently of passage count, while preserving previously accepted source ranges. Direction keeps source and response batches small and bounds its cast context to 14,000 characters: relevant profiles receive detail; other identities use a compact roster. Profile requests sample observations across the book at effort-dependent limits, with bounded linked earlier-volume evidence.

Validation: **523 Python tests passed, 1 opt-in macOS audio integration test skipped**, plus **7 Node test entries** covering model settings and the series/production panels. Regression cases include duplicate excerpts at different source locations, crash-window cache ordering, incomplete/stale profiles, alias ambiguity, provenance, billing and transient failures, unknown usage/pricing, per-attempt budgets, later-volume exclusion, chunk coverage, and zero-call replay of old discoveries. Browser verification confirmed model menus, corrected chapter counts, census/freshness, series controls, and the cost preview; no console warnings/errors appeared.

The real EPUB repair preserved **656,638 source characters, 7,299 passages, every cast profile, 855 references, and all 33 library audio takes**, including the demo. It now identifies **36 chapters, 1 recap, 3 front-matter sections, and 2 back-matter sections**. Saved first-chapter discovery was reused without generation. The current scan preview has 36 pending requests and 3 reusable units; previews are approximate, include an output allowance, and may exceed the default per-run limits. No new cloud generation or account-check requests were made during this validation.

A pre-migration SQLite backup was retained locally at `.spintails/backups/before-progressive-analysis.sqlite3` (the legacy Spin Tails directory); runtime data is not included in Git. The app was verified at `http://127.0.0.1:8765/`. Optional provider batch executors, advanced NLP, and a concrete local model runtime remain future integrations; no runtime or endpoint was assumed.
