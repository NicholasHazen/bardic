# Separate client and server, then replace the Python server with Rust

> **Moved.** The working copy of this plan is `docs/RUST-SERVER-PLAN.md` in the Rust repository, `bardic-server`, and it is authoritative from 2026-09-29. This copy is frozen at that date and kept as history.

This plan was written on September 28, 2026, at commit `24ebdfd` with contract 0.3.1. It is a proposal: nothing in it is implemented. It builds on [the client/server contract proposal](CLIENT-SERVER-CONTRACT.md), whose phases 0–1 are done, and on [the API workflow](API-WORKFLOW.md).

**Direction (owner, 2026-09-28).** The browser client and the API server become explicitly separate: first as separate directories in this repository, later as separate repositories. The Python/FastAPI server is replaced by a Rust server. The stated reasons are strong types, compilation, performance, portability and concurrency.

**Method and revisions.** Two independent red-team passes checked the first draft: one verified every count and citation against the code, the other ran experiments with real crates against Python 3.11.5. The owner then made two corrections. Estimates in time are meaningless with agentic coding, so cost is measured in surface, complexity and verification. And the code is two days old (first commit 2026-09-27 17:39, 128 commits, 104 of them on 2026-09-28), so Python is a fast-moving draft, not a mature system whose every quirk must be reproduced. The owner then decided that nothing in the current library needs to survive, so the rewrite starts from a clean slate. This version is built on all of it.

## TL;DR

- **Python is a draft, not the oracle.** The specification for the Rust server is the OpenAPI contract, the invariants in [AGENTS.md](../AGENTS.md) (source coordinates, immutable text, reserve-before-spend, never resend a billed request, retained history) and behavioral tests. It is not Python's byte-level behavior.
- **Clean slate.** Rust defines its own schema and its own canonical JSON and hash formats, with version identifiers. There is no importer and no migration. The Python library is never opened or modified by Rust and stays on disk, so the Python service keeps working until the owner switches, and going back is pointing the service at it.
- **Fix the contract before porting it.** No client has been generated yet and the contract is at 0.x, so breaking changes are cheap now and expensive after. Port the contract you want, not the one you have.
- **The main risk is churn, not compatibility.** The surface changes faster than any port can converge. Lock each domain from the start of its port until parity.
- **Directories now, repositories last.** Move the browser client to `clients/web/` first. Leave the Python package where it is until it is deleted.
- **Cost is surface, complexity and verification.** About 19,000 lines of Python to port, 98 operations and 42 tables. Agents make the code cheap. What limits the port is how well each piece can be verified and how much the owner must review.

---

## Key decisions

**1. Clean slate: Rust-native storage, no importer.** In-place compatibility would have to match Python's stored JSON, hash inputs, timestamp strings, trigger behavior and about 60 legacy-shape paths. The owner has decided that nothing in the current library must survive, so none of it is needed. Rust gets typed columns, one canonical JSON form (for example RFC 8785), version identifiers on every retained format and no legacy branches. Books are imported again from their original files, and Gemini and Breeze voices are re-listed from the providers through the existing refresh operations.

**2. Python's tests and code are a source of invariants, not of behavior to copy.** The port mines them for what must be true, and drops Python-specific accidents. Bugs found while doing so are fixed in Rust by design and recorded in the port ledger.

**3. Parallel server, one switchover.** A strangler (a Rust front process forwarding unported routes to Python) would mean two processes writing one library, and would need new cross-process locks. With separate libraries the question does not arise: Rust runs against its own new data directory until the owner switches.

**4. Lock a domain while it is ported.** From the start of a domain's port to parity, its operations, stored formats and hashes change only in both servers in one change (the port ledger), or not at all. A domain that changes faster than it can be verified is deferred, not chased. Client work is unaffected.

**5. Settle the contract first.** Before any Rust: retire the known warts in the contract and decide the open items (handshake, `GET /api/jobs/{id}`, authentication for off-origin clients). A breaking contract change is free until a client is generated from it.

Rejected:

- **Rust core inside Python through PyO3.** It adds a native build to the Python app ([D01](DECISIONS.md)) and glue that is thrown away.
- **Generating the whole server from OpenAPI.** Server generators for axum are immature. Generating **types** from the schemas is worth evaluating (see Phase 3), with handlers written by hand.
- **Importing or sharing the Python library.** Decided against: nothing in it needs to survive.

---

## What the Rust server must satisfy

### The wire contract

`contract/openapi.json` (98 operations, 242 schemas). Only 39 of its schemas set `additionalProperties: false`; "no undeclared fields" is enforced by the Python test walker, not by the file. The conformance suite closes every object when it validates. Source spans are zero-based **Python Unicode code-point offsets** with exclusive ends on the wire. That is a contract, not an accident, and Rust needs a code-point index over each chapter rather than byte slicing.

### Invariants from AGENTS.md and the architecture

These are requirements. Each becomes a black-box test against fake providers or fixture libraries:

- **Source and evidence.** Canonical chapter text is immutable. Evidence resolves to the exact original slice; no fabricated quotes; ambiguity is preserved.
- **Money.** An allowance is reserved before every paid attempt, including retries and repair. Unknown cost is not zero. At most two transport attempts, one repair, and `not_sent` for failed connections. An uncertain billed request (Gemini voice creation) is never resent. A cancelled queued child never starts. Rust may choose its own reservation arithmetic, but it must be at least as conservative as today's (the input reservation is the request's UTF-8 length plus 2,048, priced with a 1.25 cache-write uplift; `processing.py:136-166`), and the guard blocks on any unknown.
- **Quota.** The Gemini daily request count uses the Pacific-time day and the same shared 100-a-day limit (`tts_limits.py:59-86`).
- **History.** Manual edits, retained artifacts, decisions, rejected and cached states stay distinct. Forcing a new performance does not overwrite old audio. Simple listening stays independent of cast and enhanced takes.
- **Identity.** Cross-book context uses only confirmed links and strictly earlier reading order.
- **Local safety.** Loopback default, host check, cross-origin write guard, one server per data directory, and no secrets in artifacts or exports.
- **Semantics chosen by tests.** Text handling (case folding, whitespace, word boundaries) is defined by vectors that the owner accepts, not by reproducing Python's regular expressions. Where a rule feeds evidence acceptance or canonical text, its vectors are strict.

### Not carried over

Nothing needs to be reproduced from Python's storage: its hash inputs and `json.dumps` formats, the `+00:00` timestamp strings, trigger and rowid behavior, `flock` file semantics (Rust uses its own lock file), legacy job and reference shapes, `.spintails` fallbacks, `analysis_units` tables, recipe-named WAV paths, Python's pydantic coercions, its `==` in projection sync, and its 422 pydantic type strings (the contract's stable error codes remain).

### Starting clean

- **The Python library stays untouched.** [AGENTS.md](../AGENTS.md) forbids erasing it, and there is no reason to. Rust uses a different default data directory and **refuses to open** a directory that holds a Python-format library, so neither server can damage the other's data.
- **State that lives outside the library survives.** Original ebook files are the owner's own files. Gemini project voices and Breeze server voices live with the providers; Rust re-lists them through the same operations the Voices page uses today (Gemini refresh, Breeze server-voice import).
- **State that does not survive** and is regenerated: analysis results, manual edits, generated audio, listening history, series links and settings. The owner has accepted this. The Gemini TTS limit (100 requests a day, shared) still applies when audio is regenerated.
- **The first Rust storage format** is designed in Phase 4a and versioned from its first commit.

---

## Target layout

In this repository, while both servers exist:

```
contract/          openapi.json, CHANGELOG, API-REFERENCE (as today)
conformance/       black-box HTTP harness, fixture libraries,
                   fake providers, invariant tests                   (new)
clients/web/       browser client, moved from bardic/static          (moved)
server/            Rust cargo workspace                              (new)
bardic/, tests/    Python server and tests, unmoved until deleted
tools/, bardicctl  operations tooling
```

**The Rust repository starts when the first Rust code is written (Phase 3), not at the end.** Nothing is shared with Python, the Rust repository is where the server ends up anyway, and a repository that can see only the contract and the conformance suite keeps the contract as the specification. Until Python is retired, `contract/` and `conformance/` are authored in this repository. The Rust repository holds a **pinned copy** of the contract (`tools/contract_pin.py`) and runs this repository's conformance suite against its binary, so it needs a sibling checkout of this repository while the port is under way. Agents porting a domain need both trees: the Python code, contract and tests as reference, and the Rust repository to write in. After Python is retired, the Rust repository authors the contract and conformance suite, and this repository keeps the web client. A separate contract repository is worth it only when another server or several clients need the contract without the server.

---

## Phases

Each phase leaves the app working and ends with exit criteria. Phases are ordered by dependency. Within a phase, work on disjoint files can proceed in parallel, with explicit file ownership per agent ([AGENTS.md](../AGENTS.md)). The critical path is the specification: a domain can be ported as soon as its conformance tests exist, and is finished only when they pass.

### Phase 0 — Settle the contract (small surface, lowest cost now)

The contract is settled when a Rust server and a generated client can be written against it without expecting a breaking change soon. That means retiring what would force one:

- **Version handshake.** `GET /api/status` reports the contract version and hash the server implements.
- **`GET /api/jobs/{id}`**, so clients stop listing jobs to watch one.
- **Tagged unions.** Where a union can be told apart by a literal field, declare the discriminator, so generated Rust (serde tagged enums) and TypeScript get a real union.
- **Required fields.** A response field that is always sent is `required`, so generated types do not use `Option` for it. This pass is driven by the responses the tests produce plus reading the handlers.
- **Other shapes** that would be painful for a strongly typed client (stringly-typed numbers, one concept with several shapes, untyped fields that are really structured), fixed when small and listed when not.
- **Responses stay open.** The contract tells clients to ignore unknown response fields, so the contract does not close response objects. The conformance harness closes them when it validates, which catches undeclared fields in either server.
- **Authentication and CORS for off-origin clients** are designed now, even if they ship later, so the Rust server is built for them. They change the contract (a security scheme, a 401 code, preflight and the write guard) and are an owner decision.
- Pin the Python version.

**Exit:** the contract contains what the Rust server will implement. Every change follows the [API workflow](API-WORKFLOW.md).

**Owner decisions (2026-09-28).**

- **Authentication:** none for now, since the app has no users. **CORS:** an extremely permissive policy is available but opt-in, through `BARDIC_CORS_ORIGINS` (`*` or a list of origins), off by default (contract 0.4.1). With `*`, any web page in a browser that can reach the server can read and change the library and start paid work; the server prints a warning at startup. The Host check stays, and dev and conformance servers blank the setting. Chromium's private-network rules for public pages calling loopback are not handled (no `Access-Control-Allow-Private-Network`) and are unverified.
- **Shapes:** one deliberate breaking change, contract 0.5.0, made before any client is generated (below).
- **Python bugs found by the harness** stay as strict expected failures in `conformance/` and are fixed during the port.

**Done in contract 0.4.0:** the handshake (`Status.contract` and the `Bardic-Contract-Version` header on every `/api` response), `getJob`, and 12 always-sent fields made required. Three changes were breaking: `Job.analysis_limits` lost its indistinguishable second shape, `ResourceAggregate` totals became integers, and `ListeningSession.settings` is typed.

**What the Rust generator experiment showed** (typify 0.8.0, progenitor 0.15.0, openapi-generator 7.25.0 and openapi-typescript 7.13.0, run against 615 recorded real payloads from 73 operations):

- typify generated all 242 schemas, compiled, and deserialized all 615 payloads; it is the tool to build on. progenitor generated 89 of 99 operations after a documented 3.1-to-3.0 transform, and 97 once `416` carries the JSON error body; the two multipart operations stay hand-written. openapi-generator's Rust output lost 103 of 605 payloads (17%).
- **Untagged unions are the real problem.** A chunk clip deserializes silently as a passage clip and drops `clip_start`, `clip_end` and `chunk_id`. typify ignores `const` and `discriminator`; a required single-value `enum` string discriminates correctly. Nested `anyOf` breaks newer typify. `Job` becomes a proper tagged enum only as inline `oneOf` branches with the base fields expanded.
- **Open enums are not tolerant in Rust.** An unknown value fails the whole response (121 enum sites reachable from responses), and `enumUnknownDefaultCase` does not fix it. Keep enums in the contract for TypeScript and post-process the generated Rust to add an unrecognized variant (a regex prototype touched 149 enums and cut the reject sites from 121 to 1).
- **Contract rules that follow:** tag every union member with a single-value enum; never nest `anyOf`; never add a catch-all `kind: string` branch; write nullable unions as `anyOf[{$ref}, null]`; make always-sent nulls required-nullable; add `format: int64` to counters; declare `416` with the JSON error body.

**Done in contract 0.5.0 (breaking, 2026-09-29).** The rules above were applied, and the UI, its tests and the conformance suite were updated in the same change.

- **Unions.** Every union of objects is a named `oneOf` with one required single-value string tag per member: `ListeningAudio`, `PerformanceAudio`, `ListenResult`, `VoicePreviewResult`, `SeriesCharacterLinkResult`, `SeriesVolume` (`kind`), `PipelineVersionRow` (`step`), `StoryMapNode` and `StoryMapEdge` (`type`). `Job` is a `oneOf` on `kind` with eight inline branches (`render`, `analyze`, `pipeline`, `series`, `listen`, `listen_chapter`, `voice_preview`, `performance`) and no catch-all. The `Job` discriminator has a `propertyName` but no `mapping`, because a mapping needs `$ref` targets. A build-time check and guard tests in `tests/test_contract.py` refuse untagged or nested unions, an `anyOf` of objects, a bare `null` type and an integer without a `format`.
- **Nulls, numbers, ranges.** About 130 always-sent fields are required (nullable where they can be null), backfilled in `bardic/wire.py`. All integers carry `int32` or `int64`. `416` carries the JSON error body. `AccountCheck.balance` and `SeriesVolumeSlot.book_id` (always null) were removed.
- **Names.** The wire says `passage` only: `Book.passages`, `/passages/{passage_id}`, `/api/audio/{book_id}/{passage_id}`, `PassageEdit`, and seven renamed keys. Storage keeps its internal names and is translated at the boundary. A few other renames (`voice_revision`, `start_seconds` and `end_seconds`, `cached_units`) removed keys that meant different things in different schemas.
- **Types.** `NarrationProvider`, `AnalysisProvider`, `CloudProvider` and `VoiceLibraryProvider` replace 17 inline enums and about 25 plain strings. Result-table lists are arrays and display labels are identifiers. Five duplicate schemas were merged away.
- **Size.** 242 schemas became 265 (28 added, 5 removed), with 241 properties removed or renamed on the schemas that remain. There are 99 operations and two paths changed. 13 UI scripts and 22 Node tests were updated.
- **Generator check.** typify 0.8.0 generates all 265 schemas with no failures, and `Job` and `SeriesRunChild` come out as `#[serde(tag = "kind")]` enums. progenitor generates 97 of 99 operations; the two multipart operations stay hand-written. Of 3,973 recorded 2xx responses, 3,962 deserialize and round-trip exactly. The 11 that do not are nine test-fabricated job payloads, one `getStoryMap` response that loses optional reference fields on re-serialization, and one `cancelJob` response that loses a `null`.
- **Left optional or untyped on purpose, and listed in the changelog:** per-passage extras, provider-specific audio extras, `StoryMapReference` fields, `ArtifactDetail.payload` and `PipelineResultRow.previous`. About ten routes return raw job dicts rather than the public job view; no internal field reaches the wire today.

**Verification (2026-09-29, at `b07881f`).** 1398 Python tests pass (1 skipped: the opt-in Mac speech test). 281 Node tests pass, every static script passes `node --check`, and the contract check reports it current. The conformance self-tests pass (107) and the conformance suite gives 313 passed and 2 expected failures (the known Python bugs). The strict TypeScript codegen check passes. In a browser against a scratch server, the sample book loads with `passages`, all five book tabs render, every request returns 200, the console shows no errors, and a speaker edit made in the Script tab persisted with `manual_fields` naming only the field that changed. Not exercised: audio playback in the browser, live providers, and legacy stored data over HTTP.

**Still open:** the header as an OpenAPI `headers` entry (not planned; the conventions text documents it), and Rust-side tolerance for unknown enum values, which is a generation step for the Rust repository (optional-nullable response fields also lose `null` when a Rust client re-serializes them).

### Phase 1 — Separate the client (mechanical move of about 14,000 lines of client code)

- Move `bardic/static/` to `clients/web/` with `git mv`, with its Node tests and `package.json` tooling. The server serves it through a `BARDIC_UI_DIR` setting that defaults to that directory, refuses dotfiles and never points at a repository root (Starlette's `StaticFiles` would serve `.env`, see [the proposal](CLIENT-SERVER-CONTRACT.md#6-transport-and-serving)).
- One `BardicApi` module for every request, and a Node fetch-stub helper that fails any request matching no contract operation.
- Land the move as one mechanical change when few branches are open. Restarting the owner's service afterwards needs the owner's approval.
- Later, off the critical path: type-check the JavaScript against a `.d.ts` generated from the contract with `tsc --noEmit --checkJs`. No build step.

**Exit:** no `fetch(` outside `BardicApi`; every Node test checks its requests; the server imports nothing from the client directory.

### Phase 2 — Conformance harness (test infrastructure first, then grows per domain)

1. **External-server mode for the test client.** The `TestClient` fixture can start any server command on a scratch data directory and send real HTTP. Tests that only use HTTP run unchanged against either server. 63 of 79 test files import `bardic` internals, so the rest are mined for invariants and rewritten as black-box tests, domain by domain.
2. **Closed-schema validation** of every response against `contract/openapi.json` itself.
3. **Fixture libraries** built through the API, from synthetic EPUB/TXT text.
4. **Provider endpoint overrides**, development-only and loopback-only, for every hard-coded provider URL (`analysis.py:531, 550, 601`, `audio.py:41`, `gemini_voices.py:20`, `account_checks.py:142-150`, `model_catalog.py:226-228`). **Fake providers** that record requests and can fail, stall or return bad evidence. Recorded requests make reservations, retries and "never resend" testable from outside.
5. **Text vectors** for code-point offsets, evidence matching, canonical text of a synthetic EPUB corpus, and case folding. The owner reviews and accepts them.

**Status (2026-09-28).** The harness (`conformance/`, see [development](DEVELOPMENT.md#conformance-suite-and-contract-pin)) runs against any base URL or spawns a server command, validates every response against `contract/openapi.json` with closed objects, and reports operation coverage: 72 of 99 operations have a 2xx, 27 need a fake provider and are listed with reasons, and 185 of 700 documented error-code triples are exercised. Its 107 self-tests show the checker fails on each kind of violation, and three seeded faults in Python (a UTF-16 offset, an extra status field, a wrong 404 code) were all caught. Still to build: fake providers with provider endpoint overrides, text vectors, and per-domain seeded-fault checks.

**Found by the harness in the Python server (contract 0.4.0), left unfixed:**

- After a TXT or EPUB import, `scenes[].character_ids` is empty, though the contract says it lists the passages' speakers, `narrator` and `unassigned` included. The demo book and an edit fill it.
- `editPassage` with only `direction` on a narration passage returns `manual_fields: ["direction", "speaker_id"]`, locking a field that was not sent. The contract says an edit locks only the fields whose values it changes.

Both fail in `conformance/` today, on purpose. Per the plan's policy, they are fixed in Python as their own versioned changes, then the tests pass.

**Exit, per domain:** each of its operations receives a 2xx response; the error codes a client branches on are covered; each invariant above that it touches has a test. Seeded faults in Python (wrong offset, missing reservation, a resend after an uncertain outcome) are caught, which shows the tests can fail.

### Phase 3 — Walking skeleton (transport only, no domain logic)

- **Create the Rust repository** once Phases 0 and 2 have their first exit met. Pin the contract into it with `python tools/contract_pin.py pin --from <this repo> --to <rust repo>`; `check --from` reports a stale or hand-edited pin and `log` prints the changelog entries added since the pin. Give it an `AGENTS.md` that carries the invariants from [What the Rust server must satisfy](#what-the-rust-server-must-satisfy) and the rule that the pinned contract is never edited by hand.

- A cargo workspace in `server/`. Use axum and tokio, rusqlite with bundled SQLite, one writer thread in place of the Store lock, reqwest and serde. Evaluate generating request and response **types** from the contract's schemas; if the output is clean, this makes the compiler enforce the contract. Split crates only when compile times or ownership call for it.
- The skeleton serves the client, `/openapi.json` and `/api/status`. It implements the host check, the cross-origin write guard, `no-store`, the error envelope, a data-directory lock, and its own schema-version and migration record.

**Exit:** the transport tests pass against Rust; it refuses to open a directory that another server holds or that contains a Python-format library.

### Phase 4 — Port by domain (about 19,000 lines, the bulk of the surface)

Order follows dependencies: storage first, then read paths before writes, and local work before paid work. Steps that do not depend on each other (for example 4b, 4d and 4f once 4a exists) can proceed in parallel, each module owned by one agent. Each step is done when its conformance subset passes against both servers. Under the recommended path, the storage step is design work, not compatibility work, so its complexity is lower than the first draft rated it.

| Step | Domain | Python lines | Complexity | Notes |
| --- | --- | --- | --- | --- |
| 4a | Storage core: books, artifacts, jobs, settings, ledgers, locks | 1,131 | Medium | Typed schema. The artifact model keeps immutability and dependency edges, with its own canonical hash. |
| 4b | Text core: evidence, chunking, census, pronunciation, alignment, search | 1,391 | Medium | Code-point index per chapter. Eight look-around regular expressions become explicit boundary checks. |
| 4c | Read side: pipeline views, resources, exports, covers, audio files with ranges | 932 plus routes | Low | GETs write nothing, and a test says so. The pipeline views compute outside changes in a transaction they roll back; Rust can compute them in memory instead. |
| 4d | Import, structure repair, library edits, series membership | 1,012 plus routes | Medium | Synthetic EPUB corpus. Keep the hostile-archive limits. Covers need an image crate in place of Pillow. |
| 4e | Jobs framework and simple listening: Gemini, macOS `say`/`ffmpeg`, Breeze streaming | 2,305 | High | State machine, restart interruption, cancellation, quota count. |
| 4f | Voice library, previews, performances, Studio narration, export | 2,584 | Medium | Never resend a billed voice creation. |
| 4g | Analysis pipeline, providers and metering, catalog, account checks, series | 6,376 | Highest | The largest surface and the most cost-sensitive: reservations, retries and repair, all checked against fake-provider request logs. |
| 4h | Operations: `bardicctl` service and development servers | 784 | Low | The service runs the binary; `bardicctl` stays a thin wrapper. |

Route handlers and the `Runtime` (`app.py`, 2,404 lines) are spread across the steps. The 6,611 lines of `apispec/` stay in the contract and are not ported.

### Phase 5 — Trial, switch, retire

1. **Differential replay.** Both servers start on new, empty data directories and are driven through the same request sequences with the fake providers, building the same synthetic library through the API. Diff the responses. Explained differences go in the ledger. This tests the port, not any storage compatibility.
2. **Dogfooding** with the owner's own books imported into the Rust library, until the owner is satisfied. The exit is coverage: every operation exercised, every replay difference explained, and a bounded live check with keys the owner authorizes.
3. **Switch.** With the owner's approval, the service runs the Rust binary against its own data directory. The Python library and service definition stay in place, so going back is restarting the Python service. Anything the owner did in Rust is not in the Python library; that is the price of not sharing one.
4. **Retire** when the owner chooses, once the switch has been exercised and the Python fallback has not been needed. Delete the Python server and its in-process tests, and decide where the contract is authored from then on: generated from Rust types, or hand-maintained and checked by the response validator. Port the reference generator. The old library is kept or archived by the owner; the repository does not touch it.

### Phase 6 — Split the client out, and hand over the contract

The Rust repository already exists (from Phase 3). What is left:

- Extract the web client with `git filter-repo`, keeping its history. It pins a contract version with the same pin script.
- Move authorship of `contract/` and `conformance/` to the Rust repository when Python is retired, and delete the Python server and its in-process tests from this one.
- Not before Phase 5, unless a second client exists. While most features change both sides (10 of 17 commits in the earlier survey), a client split turns each feature into two coordinated changes.

---

## Keeping up with a moving target

- **Per-domain locking** (decision 4) is the main tool. Pick the most stable domains to port first, and defer the ones still being reshaped (analysis, voices).
- **A port ledger** marks each operation `python-only`, `rust-parity` or `rust-owned`, and lists deliberate differences and Python bugs not carried over. Changing a `rust-parity` operation changes both servers in the same change.
- **New or changed operations need a black-box test** from Phase 2 on. This goes into AGENTS.md and the API workflow once Phase 2 exists.

---

## How we know it is working

| Signal | Now | Before switching over |
| --- | --- | --- |
| Operations with a 2xx black-box test passing on Python | 0 of 98 | 98 of 98 |
| Invariants in this plan with a passing black-box test | 0 | all |
| Seeded faults caught, per domain | not measured | all |
| Operations at `rust-parity` | 0 | 98 |
| Imported book: every span resolves to its exact source quote | not measured | all, on the same EPUB corpus in both servers |
| Unexplained response differences in replay | not measured | 0 |
| Failures on first attempt when a domain is ported, and owner review size | not measured | recorded per domain, to decide where to lock or defer |
| First-audio latency (fake provider), `GET` of a 6,000-passage book, import time, cold start, memory | not measured | recorded for both servers |

Stop-or-continue checkpoints: after Phase 0 (is the contract settled?), after Phase 3, and after the first ported domain (do the first-attempt failures show that agent-written Rust converges cheaply?).

---

## Risks and open questions

- **Regeneration cost.** Starting clean means paid analysis and narration are repeated for any book the owner wants in Rust. Re-keying prompts is acceptable in development. Gemini TTS remains limited to 100 requests a day across all servers using the project.
- **A young codebase moves.** Domains still being reshaped (analysis, voices, series) may change under a port. Lock, defer or port them last.
- **What the port buys.** Most waiting is on providers (Gemini TTS allows 10 requests a minute) and on one SQLite writer. Rust improves startup, memory, packaging (one binary, no `uv` for the service), types and cancellation. It will not make narration or analysis much faster. The measurements above make this a result rather than an assumption.
- **Portability is partial.** Device narration still needs macOS `say`. Audio conversion needs `ffmpeg` until it is replaced. `.local` names use `dns-sd`.
- **Owner decisions:**
  - the Rust data directory name and default location;
  - the visibility of the Rust repository, provisionally named `bardic-server` and created at Phase 3 (creating a remote is an owner decision, [AGENTS.md](../AGENTS.md));
  - which domains to lock first, and which to defer because they change too fast to converge;
  - what evidence counts as enough dogfooding;
  - when Phase 1's directory move lands (best when few branches are open);
  - whether a dedicated client is needed before the switch, which pulls authentication forward;
  - where the contract is authored after Python is retired.

## Out of scope

- Moving the Python package. It stays at `bardic/` until it is deleted.
- Changing prompts or storage formats in Python to suit Rust. The one exception is a contract change decided in Phase 0.
- A frontend framework or bundler.
- Replacing `ffmpeg` or `say`. Authentication implementation, CI, a license and hosting remain separate owner decisions.

---

## Appendix A: findings that apply to any Rust implementation

Evidence is from two red-team passes; line numbers are at `24ebdfd`. In-place compatibility with Python's library is not planned. If that ever changes, the items that would matter most are Python's artifact identity (`artifacts.py:22-34, 107-111`, permanent through the immutability triggers at `artifacts.py:72-78`), the `==` comparison in projection sync on write paths (`pipeline/projection.py:64-97`), the two `json.dumps` hash forms (compact in `artifacts.py:25`, `audio.py:303`, `listening.py:51`, `breeze.py:168`, `performances.py:51`, `voice_previews.py:31`; default separators in `processing.py:28`, `series.py:35`, `series_processing.py:90`), the schema with its triggers and rowid ordering, and about 60 legacy-shape paths.

**Money and quota: arithmetic worth deliberately keeping or improving.** The input reservation is the request's UTF-8 length plus 2,048, priced with a 1.25 cache-write uplift, with the price tier chosen by reservation size (`processing.py:136-166`). The guard sums every earlier attempt for the book and blocks on any unknown. Price expiry is a string comparison against the local date (`model_catalog.py:102`). The Gemini quota count compares `created_at` strings against the start of the Pacific-time day, and only counts rows whose `request_count` is an `int` (`tts_limits.py:59-86`). Rust should use typed timestamps and counts, which removes the failure modes of a `Z`-suffixed string or a float count.

**Numeric and text semantics to decide on purpose.**

- **Floats.** Use serde_json with `float_roundtrip`. Without it, 73,959 of 299,908 random floats parsed one unit in the last place differently. If Rust ever needs Python-style float text, a formatter built on `ryu` digits with Python's exponent rules matched all 299,908.
- **Rounding.** Python's `round()` rounds half to even; Rust's `f64::round` rounds half away from zero. It matters where rounding picks prompt and series-memory samples (`pipeline/prompts.py:64`, `series.py:450`) and clip timings (`alignment.py:212`). Choose one rule and test it.
- **Regular expressions.** There are 71 `re` call sites and 7 pydantic `pattern=` constraints. Eight use look-around, which Rust's `regex` rejects: `pronunciation.py:196, 307`, `preprocessing.py:88`, `pipeline/evidence.py:168`, `pipeline/prompts.py:186`, `alignment.py:90`, `local_services.py:283`, `importer.py:357`. Rewrite them as explicit boundary checks or use `fancy-regex`. Confirmed divergences to decide on: `\w` (Python matches `½`, Rust matches combining marks), `\s` (Python treats U+001C–001F as whitespace), `$` (Python matches before a trailing newline), `(?i)[a-z]` (Python matches `İ`).
- **Case folding and normalization.** icu's `fold_string` matched `str.casefold` on characters assigned before Unicode 15 and differed on newer ones. Pick a Unicode version deliberately and record it.
- **Configuration files.** python-dotenv runs without interpolation; Rust's `dotenvy` expands `$` by default and would corrupt a key that contains it.
- **SQLite.** rusqlite with bundled SQLite 3.50.2 read and wrote an FTS5 table made by SQLite 3.41.2 with identical results. There is no compatibility constraint under a clean slate.
- **A Python bug worth fixing now.** `(?<!\w)cafe(?!\w)` matches inside a decomposed `Café`, and pronunciation matching deliberately builds NFD forms, so a rule for "Cafe" can fire inside "Café".
