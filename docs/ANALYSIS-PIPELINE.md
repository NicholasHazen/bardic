# Analysis pipeline

This page describes the step-based analysis pipeline behind the **Analysis** tab, as implemented on 2026-09-27, and the steps planned next. It is the authoritative reference for the step contract. The older phase controls (scan/profiles/direct/full in Studio and series runs) still work; see [how the two coexist](#coexistence-with-the-phase-controls).

## What it does

Text analysis is a list of named **steps**. For each step the owner chooses a provider and model, then runs it. The Analysis tab runs one step at a time (2026-09-28); the API can still run several steps together. Each run produces a **candidate version** that can be inspected, compared with another version, **accepted**, or **rejected**. Accepting an older version is a rollback. The reader, cast and studio only show accepted results.

Thoroughness is the model choice. There is no separate effort parameter: an economy model gives a fast, cheap pass; a deeper model gives a slower, more accurate one. Comparing versions of one step produced by different models is how a model is chosen for that step.

## Concepts

| Term | Meaning |
| --- | --- |
| Step | A declarative object implementing the [contract](#the-step-contract). Registered in pipeline order. |
| Scope | The granularity at which a step's output is versioned and accepted: `book`, `chapter` or `character`. |
| Unit | One cacheable piece of work inside a run: one LLM request or one local computation, belonging to one scope. |
| Version | An immutable `step_output` artifact for one step and scope. Artifact identity is content-addressed, so an identical result reuses the existing version. |
| Version state | `candidate` (waiting for review), `accepted`, `partly_accepted`, `superseded` (accepted earlier, since replaced), `same_as_accepted` (an unaccepted run whose content equals the accepted version), `rejected`, `running`, `empty`. Accepted states come from decisions, never from coincidental equality. |
| Step version (UI "version") | All scope versions produced by one execution of one step, with its provider/model, the accepted inputs it read and unit counts (`pipeline_step_runs`). |
| Accepted version | The artifact head for `step_output` / `<step>:<scope>`. Candidates are recorded without moving the head. |
| Decision | An append-only accept/reject record with its mode: `user`, `auto`, `baseline` or `external`. |
| Baseline / external | Versions captured from the existing book projection: `baseline` the first time the pipeline sees a book, `external` when something outside the pipeline changed it. Both carry legacy provenance: their producer is unknown. |
| Stale | An accepted version whose recorded input versions are no longer the accepted ones. Nothing re-runs automatically. |

## Current steps

| Step | Method | Scope | Reads | Writes to the book | Default model role |
| --- | --- | --- | --- | --- | --- |
| Chapters & titles (`structure`) | Local: re-reads the saved original's EPUB navigation/headings | book | — | chapter title, kind, title source, logical sections, narrative order | — |
| Name & dialogue census (`census`) | Local counts | book | — | nothing (informational; guides profile effort) | — |
| Character discovery (`discovery`) | LLM, ~24,000-character ranges, exact quotations | chapter | — | new characters, added aliases, evidence | economy (scan model) |
| Quote attribution (`quotes`) | Service: your BookNLP server, one request per section | chapter | discovery (cast names become aliases) | nothing (read by directing; its table compares with the current speakers) | — |
| Character profiles (`profiles`) | LLM per character, tiered evidence, earlier-series context via confirmed links | character | discovery | description, direction, profile metadata | analysis model |
| Speakers & delivery (`directing`) | LLM per scene batch (the existing fused request), or your Novel Analyzer server per section, or BookNLP (speakers only, from `quotes`) | chapter | discovery, profiles, quotes | speaker, confidence, evidence, delivery, cues, BookNLP check, scene breaks and notes | analysis model |

Every LLM step can also use the **Local LLM** provider, an OpenAI-compatible server on your network. See [self-hosted providers](#self-hosted-providers).

Import (canonical text, passage split and passage IDs) is not a step. Those identities are frozen after import because assignments, edits, takes and references are keyed by them.

## Execution

1. **Plan** (`POST …/analysis-pipeline/plan`) builds each requested step's units from the currently accepted inputs, marks cached units, estimates requests/tokens/cost and returns a fingerprint. A step whose inputs are also in the request is marked `inputs_pending`: its real work depends on what the earlier step produces. A step whose **required** inputs have no accepted result and are not in the request is listed in `missing_inputs`.
2. **Run** (`POST …/analysis-pipeline/runs`) creates one job of kind `pipeline`. It snapshots provider keys, per-step provider/model and gates. When `expected_fingerprint` is sent and the plan changed, the run is refused. A run with `missing_inputs` is refused (400) before anything is queued, and so is a run with neither `expected_fingerprint` nor explicit `limits`.
3. Each step starts by syncing outside changes, then reads its inputs' accepted versions and records them on the step version. Units run with bounded parallelism: `min(step.parallel, run concurrency)` workers, with at most `concurrency` model requests in flight across the whole run.
4. A scope becomes a candidate version only when every unit in it validated. A failure, budget limit or cancellation keeps the completed scopes as candidates.
5. With the step's gate set to **auto**, a completed step is accepted immediately (decision mode `auto`). With **review**, the step waits for the owner. Steps in the same run that **require** it are then skipped rather than built on an unreviewed result, as are those whose required input failed or ended without an accepted result. Inputs a step only records (directing's discovery) still run first but do not stop it.
6. `mode: serial` runs requested steps in pipeline order. `mode: parallel` starts every step whose in-run inputs have finished. Independent steps, such as structure and census, then overlap.

### The Analysis tab

- Steps run one at a time. Each step's panel ends with its run options (**Requests at once** and **Fresh samples**, for model steps only) and **Run this step**; the preview opens below it. There is no multi-step selection or run order in the UI. A status line at the top shows the active run, or the latest finished one.
- A step that cannot run yet shows **Needs …** in the list, a **Not ready to run** notice naming the missing step (or saying its version is waiting for review), and a link to it. **Run this step** stays disabled until the input has an accepted result.
- The preview is the only confirmation. It shows estimated requests and cost and has no limit fields.
- Selections and panels belong to one visit. Leaving the tab or the book workspace clears run options, the section choice, the open preview, the accept/restore impact panel, messages, custom-model drafts and result filters; the next visit reopens the step waiting for review (or the first step). Selecting another step closes the preview and clears messages; changing settings, sections or fresh samples closes the preview. A finished run's summary has **Dismiss** and is not shown again after the visit in which it was seen.

The book JSON is never written by a run. Only acceptance changes it, so concurrent units cannot race on the reader's projection.

### Cost, caching and provenance

Model requests use the same metered adapters as the rest of analysis:

- The run's `RequestBudget` reserves and records each HTTP attempt before it is sent.
- Runs have no request, token or dollar cap by default (changed 2026-09-28). Confirming the plan preview is the authorization; the preview states that retries and repairs can add requests and that dependent steps' work is only known once their inputs finish. A unit has at most four HTTP attempts (two transport attempts for each of at most two generations), so a run stays bounded by its work. API callers can still pass `limits`; the dollar guard, when set, is cumulative across all runs.
- A response gets at most one repair generation, for evidence that does not anchor to the source or for passage annotations that skip, repeat or invent passage IDs. The repair note names the affected IDs (only ID-shaped values; never book text or other response content).
- Rejected outputs are retained as `analysis_rejection` artifacts.
- Every request recipe is an `analysis_input` artifact with its verified source dependency.

A validated unit is cached in `pipeline_units` under a key hashing the step ID and version, provider, model, system instruction, prompt, schema, output cap, adapter version and unit locator. Re-running identical work reuses it for free; `fresh: true` requests new samples instead, for comparing a model with itself.

A candidate version records as dependencies the accepted input versions it read and the unit outputs it was assembled from.

### Self-hosted providers

Implemented 2026-09-28. Three optional servers on the owner's network are configured by their root URL (Settings → **Your analysis servers**, or `BARDIC_LOCAL_LLM_URL`, `BARDIC_BOOKNLP_URL`, `BARDIC_NOVEL_ANALYZER_URL`; a saved URL wins). A URL is configuration, not a credential: it is snapshotted with a run like a key, and it is never written to artifacts. Saving a URL does not check that the server answers. See [`local_services.py`](../bardic/local_services.py).

| Provider | Kind | Offered on | What it does |
| --- | --- | --- | --- |
| Local LLM (`local_llm`) | Model | discovery, profiles, directing | The same Responses request, JSON schema, validators and evidence repair as the OpenAI adapter, sent to `{url}/v1/responses` without a key. Metered like any model (request and token limits apply) at a known price of $0, so the dollar guard is satisfied. |
| BookNLP (`booknlp`) | Service | quotes; directing (speakers only) | Quotation offsets, speaker, the dialogue tag or beat beside it, a tag-conflict flag, pronoun-based gender and places, in about a second per chapter. |
| Novel Analyzer (`novel_analyzer`) | Service | directing | An LLM pipeline returning, per chapter, speakers, emotion, delivery (a TTS instruction), cues and scene breaks with a setting. |

**Service units.** A service step plans one unit per section with dialogue, carrying a `ServiceRequest` whose exact JSON body (chapter text plus BookNLP aliases or the analyzer's character sheet) forms the cache key, with the step ID and version. Service calls are free, so they are not reserved against the request budget; the plan reports them as `service_calls`. BookNLP and analyzer requests run one at a time per server host across every run in the process, since those services usually share one GPU. Local LLM requests follow the run's concurrency (vLLM batches them), and nothing coordinates with Breeze narration on the same machine. A connection failure or a 503 (loading or busy, no work done) is retried, up to three attempts in all. An analyzer 502 means its LLM ran and failed, so it is retried once. A section over the service's size limit (analyzer 250,000 characters) is refused before sending. The timeout grows with section length (BookNLP from 10 minutes, the analyzer from 15 up to 60; the Local LLM 15) and a timed-out request is not repeated. A request in flight cannot be cancelled; cancellation takes effect before the next request. The runner records the body as an `analysis_input`, the validated response as the unit output, and a response that fails validation as an `analysis_rejection`; a rejected service result is never repaired or retried automatically. The server's `/health` model string (not its backend address) is stored with each result. It is not part of the cache key, because planning never contacts the network. At run time, a cached result whose recorded model differs from what the server now reports is requested again, so a plan made after an upgrade can undercount service calls. The analyzer's `script`, which restates the chapter, is not retained.

**Mapping onto the source.** Every quotation a service returns must equal the chapter text at its offsets (Python code points), or the whole result is rejected. Quotations then map to dialogue passages by exact span, by the several passages a long quotation was split into, or by the passage containing it; the rest are counted as unmatched. Service tag text is not exact source (BookNLP joins tokens with spaces and may skip words). Evidence is therefore the longest leading run of the tag's words, of at least two, that appears contiguously in the same paragraph, copied from the source; otherwise a line has no evidence. Speaker names map to cast IDs through names, aliases and former names that identify exactly one character; an ambiguous name maps to nothing. A cast character named or aliased as the first-person narrator ("I", "Narrator (I)", "Miss Vance (Narrator)") is sent to BookNLP merged with its `NARRATOR` cluster and marked as the analyzer's narrator; if none or several match, first-person lines are left unmapped rather than guessed.

**Novel Analyzer as the directing provider.** The request carries a character sheet built from the cast, so speakers can only be cast characters; a line whose speaker is unknown stays unassigned. A result that labels fewer than 90% of a section's dialogue passages is rejected rather than used: the analyzer does not detect single-quoted or dash-introduced dialogue, which the importer does, and using it would silently unassign those lines. It is also rejected when a section has many dialogue passages but few blank-line paragraphs, because the analyzer gives one speaker per paragraph. Two lines landing on one passage with different speakers leave it unassigned. The version sets scene tone and direction to empty for the chapter, since it proposes none. Delivery is the analyzer's `instruction`; cues are its own words. Confidence, which the service does not report, is 0.85 for a line with a tag and 0.70 for an untagged line inferred from context. Its scene breaks are used only when every line's paragraph number matches Bardic's paragraph count, and then only to add breaks, as model directing does; a break's setting is stored as `Setting (unverified): …` because the service documents settings as unreliable. Narration passages get no delivery.

**BookNLP as the directing provider.** It reads the accepted `quotes` version and makes no request, so it needs no URL (`offline_providers`). A section without dialogue gives an empty result; a section with dialogue needs an accepted `quotes` version covering 90% of its dialogue passages. It sets speakers (0.85 with a speech tag, 0.75 beside an action beat, 0.70 untagged), leaves a tag-conflict line unassigned, and writes as delivery only what a speech tag states (a non-plain verb, manner adverbs and "with …" phrases, such as "Whispered softly."). Scenes and their notes are left as they are.

**The BookNLP check.** When `quotes` is accepted for a section, directing compares every provider's proposed speaker with it (except BookNLP's own proposal, which would count one opinion twice) and records `speaker_check` on each dialogue passage: `agrees`, `differs`, `suggests` (the proposal is unassigned), `not_in_cast`, `narrator` (first-person, not comparable) or `no_quote`. The book's narration voice speaking a first-person narrator's line counts as agreeing. Agreement raises confidence to at least 0.9 when BookNLP saw a tag or beat, and 0.8 when both inferred the speaker from turn-taking. A disagreement keeps the proposed speaker, caps its confidence at 0.65 (the lowest assigned value) and records BookNLP's speaker. When BookNLP's own tag contradicts its speaker (`tag_conflict`), the comparison is recorded without changing confidence. These values are rankings chosen to fit the 0.65 assignment rule, not measured probabilities. BookNLP is not trusted over the model, and an unassigned line is not filled from it. A manual speaker change drops `speaker_check`, as does the phase pipeline writing a speaker; the Quote attribution table always compares with current speakers. The Speakers & delivery table adds a **BookNLP check** column and `booknlp_agrees/differs/suggests` counts; the Quote attribution table compares BookNLP with the book's current speakers. Directing's `same_speaker_as_book` stat counts dialogue whose proposed speaker equals the current one. Use it to compare providers: the version diff counts every delivery wording change.

## Acceptance, rollback and manual edits

`accept` runs in one short transaction:

1. Record outside changes (sync).
2. Compute the impact.
3. Append the decision and move the heads.
4. Apply the step's accepted versions to the book through the step's projector.
5. Give a default device voice only to characters this acceptance adds (an explicit **Default** choice on an existing character is kept), clear only the takes that were valid before and are not after this acceptance, and save. Takes already hidden by a voice-library switch stay stored, so switching the voice back still restores them.

`expected_revision` (returned by preview) rejects an accept if the book changed after the preview. Preview reports:

- changed and unchanged scopes;
- **conflicts**: generated values not applied because a person edited the field;
- audio takes that will need re-rendering;
- steps downstream that have accepted versions.

Manual edits are field-level locks. Editing a character, passage or scene records in `edited_fields` the fields whose value actually changed (editors submit whole forms); the pipeline never overwrites a listed field. Result tables show a **Your edit (kept)** column for profiles and passages so a locked value is visible. For example, a voice-only character edit no longer freezes that character's profile, and a speaker correction keeps the speaker (and its confidence and evidence) while delivery notes can still update. Items edited before per-field tracking existed stay wholly locked. The older phase pipeline keeps its original all-or-nothing `edited` behavior.

Projectors never delete characters or passages, and never change source text or offsets. Rolling back discovery therefore does not remove a character someone may have voiced; it changes which evidence feeds profiles. Renaming a character records the replaced name in `former_names`. Discovery resolves later mentions of it to the same character instead of creating a duplicate, without showing it as an alias. A scene map version replaces a chapter's scene breaks unless a manually edited scene would be dropped, in which case the current breaks are kept and reported as a conflict. Rolling back does not yet reattach earlier audio takes whose recipe matches again; they remain retained on disk and in artifact history.

### Coexistence with the phase controls

The Studio phase controls, the local draft and series runs still write the book directly. Before any pipeline run or decision, and when the overview is read, `sync` compares each capturable step's accepted versions with the projection. It is skipped when a digest of the captured content is unchanged; revision numbers are not relied on. Scopes that differ are recorded and accepted as `external` versions. Outside work therefore appears in history and can be rolled back, and the pipeline never silently reverts it. Discovery and the census are not capturable (their per-chapter output cannot be recovered from the merged projection), so outside discovery work appears only through its effect on the cast.

## The step contract

A step subclasses `bardic.pipeline.Step` ([contract](../bardic/pipeline/contract.py)) and sets:

| Attribute | Purpose |
| --- | --- |
| `id`, `label`, `summary` | Stable identifier (`[a-z][a-z0-9_]{1,39}`) and UI text. |
| `method` | `plain` (local), `llm` (prompt + schema to a chosen model) or `service` (a self-hosted chapter service). |
| `providers` | Providers the owner may choose. Default: `local` for plain steps, the LLM providers (cloud and Local LLM) for llm steps. An llm step may add service providers; its `units()` then reads `ctx.provider` and plans service units (`Unit.service = ServiceRequest(body)`) or plain units instead of `LLMRequest`s. Service providers take no model. |
| `scope` | `book`, `chapter` or `character`. |
| `inputs` | Upstream step IDs whose **accepted** payloads it reads. Must be declared earlier in the registry. |
| `requires` | The inputs that must have an accepted result before the step can run (default: all `inputs`). Directing requires only profiles: it attributes passages to the cast in the book, and records discovery for staleness. An input requested in the same run counts. |
| `owns` | Projection fields it writes (`collection.field`). The registry rejects two steps claiming one field. |
| `version` | Bump when prompts, schemas or logic change. New cache keys; old versions stay readable. |
| `request_version` | Optional. When only assembly or projection changed, set it to the previous version so unit cache keys, and therefore paid results, stay valid. |
| `offline_providers` | Providers the step reads accepted results from instead of calling, so a run needs no key or URL for them. |
| `parallel`, `default_gate`, `default_model_role`, `chapter_scoped` | Execution defaults. |
| `capturable` | `capture()` can rebuild the step's projection; enables baseline/external versions and rollback to them. |
| `accumulative` | `apply()` only adds (discovery). Accepting applies just the scopes whose version changes, so unchanged scopes are not re-merged. |

and implements:

| Hook | Contract |
| --- | --- |
| `units(ctx)` | Plan the work from `ctx.book` (a copy of the projection) and `ctx.inputs` (accepted input payloads). LLM units carry an `LLMRequest(prompt, schema, output_cap)`, service units a `ServiceRequest(body)`, and either may set `chapter_id` (source dependency) and `dependencies` (other retained artifact IDs the request read). |
| `execute(ctx, unit)` | Units with neither a request nor a service call: compute the unit result. |
| `validate(ctx, unit, result)` | LLM units: raise a `RepairableValidationError` (`EvidenceValidationError`, `PassageIdError`) to allow one repair, any other `ValueError` to reject. Service units: any `ValueError` rejects. Must not mutate shared state. |
| `assemble(ctx, done)` | Group validated unit results into `{scope: payload}`. Payloads are JSON and self-contained. |
| `capture(book, scope)` | The payload that reproduces the current projection for a scope, or `None` if not recoverable. Must satisfy `apply(book, capture(book)) == book`. |
| `apply(book, payloads)` | Idempotently write owned fields for the given scopes, honor `locked(item, field)`, and return `Conflict`s. Scopes not given are untouched. |
| `summarize(book, payloads)` | `{'stats', 'columns', 'rows'}` for the generic results table. Each row needs a stable `id` and `scope`; the API diffs rows by `id` across versions. |

### Adding, changing or removing a step

- **Add**: create a module under [`bardic/pipeline/steps/`](../bardic/pipeline/steps/), then insert an instance in `builtin_steps()` after every step it reads. The API, UI list, settings, planning, caching, versioning and acceptance need no changes. Add a test that `apply(capture)` is the identity and a runner test with a fake provider.
- **Change**: bump `version` whenever the request, schema, validation or assembly changes meaning. Existing versions keep their recorded `step_version`.
- **Remove**: delete it from `builtin_steps()`. Its retained versions and decisions stay in artifact history and exports. Fields it owned stay as they are in the projection.
- **Split** (e.g. attribution out of directing): the new step claims a subset of fields and the old step drops them from `owns`. The registry enforces that no field has two owners.

## HTTP API

See [the API reference](../contract/API-REFERENCE.md#analysis-pipeline) and [the router](../bardic/pipeline/api.py). Routes live under `/api/analysis-pipeline` (definitions and saved per-step settings) and `/api/books/{id}/analysis-pipeline` (state, plan, runs, versions, preview, accept, reject). `versions/accepted` addresses the currently accepted versions. The version detail diff reports `same/changed/added/removed` and an agreement ratio, which is a cheap signal when comparing models.

## Planned steps

These recommendations came from reviewing the codebase and red-teaming the design on 2026-09-27. They are sequenced by audible impact per unit of cost. Model tiers are starting points; choose per step from compared versions on a representative evaluation set (ROADMAP R4).

| Step | Method | Scope | Suggested tier | Notes |
| --- | --- | --- | --- | --- |
| Cast identity (alias clustering, groups, unnamed speakers) | LLM | book | deep | One request over discovery candidates. Wrong splits give one person two voices across the whole book. Must redirect, never delete, character IDs, and treat confirmed series links and reviewed characters as locks. Review gate recommended. |
| Speaker attribution (split from directing) | Local tags and turn alternation first, then LLM | chapter | balanced; deep for a targeted re-fix of unassigned/low-confidence lines | The most audible error class. Re-running only uncertain lines must keep neighboring context. The BookNLP `quotes` step now provides the tag-and-alternation pass and a disagreement list; a targeted re-fix of `differs`/`suggests` lines is the natural next use. |
| Line delivery (emotion from a fixed vocabulary with intensity, subtext, vocal cues, pace/pauses/emphasis) | LLM, one fused request | chapter | balanced | Fusing these avoids 2–3 extra whole-book passes. Cues stay metadata; the source is never rewritten (see R2). |
| Pronunciation lexicon | Local candidate extraction, then LLM respellings, then review | book | economy | The lexicon, its rendering and a Cast-tab editor with auditions exist (2026-09-28); this step would only propose entries for review. A trial found natural-looking respellings reliable and capitalized stress unreliable ([research](RESEARCH-VOICE.md#name-pronunciation-respelling-trial)); prompts should ask for that style. Reusable across a series. |
| Utterance type (thought, written text, verse) | Local: retain italics/emphasis spans at import as source-coordinate metadata; LLM only for leftovers | chapter | economy | A label on existing passages, never a re-split. |
| Narrator & point of view | Local pronoun statistics first; LLM where ambiguous | chapter | balanced | Lets a first-person narrator use that character's voice. |
| Consistency checks | Local only | chapter | — | Alternation breaks, speaker absent from scene, unassigned rate. Feeds review and the targeted attribution re-fix. |

## Known limitations

- The pipeline does not reuse units cached by the phase controls, even where a prompt is identical, so work validated there is paid for again.
- Staleness compares whole input steps: accepting any new profile marks every directing chapter stale. It ignores manual edits made after a version was produced.
- Discovery rollback does not remove characters, and cast merges are not a step yet.
- Rollback does not reattach previously valid audio takes.
- A cancellation received while a response is in flight discards that response after it is paid for, as elsewhere in analysis.
- The same provider adapters are used for every step, with the shared director system instruction; a per-step system instruction would need an adapter change and a recipe version bump.
- Self-hosted services: a service upgrade is detected at run time from the server's reported model, not when planning; service calls are bounded per unit but not counted as model requests; the analyzer detects only double-quoted dialogue and at most 250,000 characters per chapter (a longer chapter is refused before sending); the analyzer can only name cast characters, so directing with it depends on discovery having found everyone, including a first-person narrator. In a live check the Local LLM's discovery named the narrator differently on each run and once omitted it.
- Adding the BookNLP check bumped directing to version 2 with `request_version = 1`: model requests did not change, so validated version-1 units are reused and not paid for again. A step sets `request_version` when only assembly or projection changes.
- A single failed section (timeout, size limit, rejected result) keeps the whole step from auto-accepting, as for model steps; accept the completed sections from the version view.
- The services' optional API keys are not supported; a server that requires one answers 401.
- Pipeline runs are uncapped by default while the Studio phase controls keep their request/token/dollar limits. A phase run's cumulative dollar guard counts pipeline spending too. Pipeline runs are not yet visible in the Studio production summary.
- The prerequisite check asks only whether an input has any accepted result. A chapter-scoped step can still run for sections whose input was never accepted; it then works from what the book already has.
