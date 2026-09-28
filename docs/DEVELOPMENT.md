# Development guide

This guide describes the code in this checkout. Start with [README](../README.md) for the product workflow and [API](API.md) for route contracts. The [storage guide](ARTIFACTS-AND-STORAGE.md) explains retained data; the research documents describe design decisions and proposals, not necessarily implemented features.

## Reproducible setup

The application uses Python 3.11 or newer, FastAPI, SQLite, and browser JavaScript. Dependencies and version ranges are in [pyproject.toml](../pyproject.toml); resolved dependencies are in [uv.lock](../uv.lock). There is no `requirements.txt`, Node package installation, frontend bundler, or asset build step.

For a new checkout:

```sh
git clone https://github.com/NicholasHazen/bardic.git
cd bardic
```

From the repository root (use your existing checkout if you already have one):

```sh
uv sync --frozen --group dev
uv run --frozen python -m bardic
```

Open `http://127.0.0.1:8765`. Stop the process with Ctrl+C. Python dependencies must be available locally or downloaded during the first sync. The examples below use a POSIX shell; adapt environment assignment and virtual-environment paths for other shells.

Node.js is required to execute the JavaScript tests. The tests use Node built-ins; no npm dependencies are needed. Use a Node version that provides `node:test`, `structuredClone`, and `FormData` (Node 20+ is a practical baseline). The Python wrappers skip their JavaScript checks if `node` is absent, so a passing pytest run alone does not prove that the UI tests ran.

Device narration additionally requires macOS `say`, installed voices, and `ffmpeg`. Other operating systems can run the app and use Gemini narration, but do not gain a local speech backend automatically. Provider adapters and format validation live in [audio.py](../bardic/audio.py).

## Configuration and data isolation

[The entry point](../bardic/__main__.py) loads the `.env` beside `pyproject.toml` through [config.py](../bardic/config.py). It does not search parent directories. Existing process environment variables win, and dotenv interpolation is disabled. Make a new local configuration from [.env.example](../.env.example) only if you do not already have a `.env`.

| Variable | Effect |
| --- | --- |
| `BARDIC_PORT` | HTTP port; default `8765`. |
| `BARDIC_HOST` | Bind address; default `127.0.0.1`, or `0.0.0.0` when `BARDIC_LAN_NAME` is set. |
| `BARDIC_LAN_NAME` | Opt-in network name such as `bardic`: binds the network, trusts `bardic.local` and advertises it through macOS `dns-sd`. No authentication; see [local-network access](OPERATIONS.md#local-network-access). |
| `BARDIC_ALLOWED_HOSTS` | Extra comma-separated Host names or addresses to trust, without ports or wildcards. |
| `BARDIC_DATA_DIR` | Data directory; default `.bardic` relative to the process working directory, or existing `.spintails` when `.bardic` is absent. |
| `GEMINI_API_KEY` | Gemini analysis and narration key. |
| `GOOGLE_API_KEY` | Gemini fallback when `GEMINI_API_KEY` is empty or absent. |
| `OPENAI_API_KEY` | OpenAI analysis key. |
| `ANTHROPIC_API_KEY` | Anthropic analysis key. |

The old `SPINTAILS_PORT` / `SPINTAILS_DATA_DIR` settings remain aliases. Shell settings win over file values even across prefixes; within one source the `BARDIC_` spelling wins. `bardic.config.data_directory()` resolves explicit configuration and the existing-library fallback without moving files. Use it in maintenance tools instead of hardcoding a default path. The old module launcher remains available; source imports and new scripts use `bardic`. See [upgrade details](OPERATIONS.md#upgrading-from-spin-tails).

Changing `.env` requires a server restart. Settings saves model preferences in SQLite but keeps changed API keys only in the current runtime's memory. Settings does not rewrite `.env`; restarting reloads configured environment/file keys. Never copy `.env`, user ebooks, generated audio, library databases, or provider response dumps into fixtures or documentation.

Use an isolated data directory and port for manual development. The following starts with cloud keys explicitly blank, even if the checkout has a configured `.env`:

```sh
scratch_dir=$(mktemp -d "${TMPDIR:-/tmp}/bardic-dev.XXXXXX")
BARDIC_DATA_DIR="$scratch_dir" BARDIC_PORT=8766 BARDIC_LAN_NAME='' BARDIC_HOST='' BARDIC_ALLOWED_HOSTS='' GEMINI_API_KEY='' GOOGLE_API_KEY='' OPENAI_API_KEY='' ANTHROPIC_API_KEY='' uv run --frozen python -m bardic
```

The empty network settings keep a development server on loopback even when the owner's `.env` enables [local-network access](OPERATIONS.md#local-network-access); empty shell values win over the file. If the chosen port already answers on `127.0.0.1`, the launcher refuses to start rather than sharing the port with a network-bound server. Choose another port.

Keep that shell's printed/assigned `scratch_dir` available if you want to inspect the scratch library later. Stop the process before removing or backing up its data. Neither the normal `.bardic` nor legacy `.spintails` library is a disposable test fixture.

The runtime uses an OS lock on `server.lock` before performing startup recovery. A second server using the same directory is rejected. Do not remove the lock or bypass it to run another worker against a live library. An import of `bardic.app` constructs the FastAPI application but starts its `Runtime` only when lifespan begins. Tests should use `TestClient(create_app(tmp_path))` as a context manager so the worker pool and lock close reliably. Network settings are read when `create_app()` runs; [test_lan.py](../tests/test_lan.py) covers them with a fake `dns-sd`, plus real-process checks that the helper cannot outlive a killed server.

The ordinary launch command deliberately has no reload flag. For Python edits, stop and restart the isolated server. Browser files are served directly; refresh the browser after edits. Launching `uvicorn bardic.app:app` directly bypasses the project dotenv loader unless you load that configuration yourself. Multiple uvicorn workers are inappropriate for the same data directory.

Sources: [Runtime and create_app](../bardic/app.py), [Store and InstanceLock](../bardic/store.py).

## Checks and test commands

Run from the repository root after dependency setup:

```sh
uv run --frozen pytest -q
node --test tests/*_test.js tests/*.test.cjs
rg --files bardic/static -g '*.js' | xargs -n 1 node --check
```

Run the Node command explicitly: the CommonJS model-picker suite and `browser_storage_test.js` are separate from pytest. Pytest invokes the `*_ui_test.js` and player harnesses through Python wrappers when Node is installed; the full Node command also reruns those harnesses.

For workspace navigation and the compact listening controls, run `node --test tests/workspace_ui_test.js tests/listen_ui_test.js tests/listen_player_test.js`. The workspace suite is part of the explicit Node command, not a pytest wrapper; it covers local search, home navigation without interrupting playback, keyboard tabs, shortcuts, provider setup focus, and Play opening narrator setup without generation when an enhanced take is absent. A browser check is still needed for the responsive layout and native disclosures.

For an already installed environment with no dependency resolution:

```sh
.venv/bin/python -m pytest -q
node --test tests/*_test.js tests/*.test.cjs
```

Useful targeted suites:

| Change | Focused command after `uv run --frozen` |
| --- | --- |
| EPUB/text extraction and structure | `pytest -q tests/test_importer.py tests/test_series_structure_api.py` |
| Provider configuration and model catalog | `pytest -q tests/test_model_catalog.py tests/test_catalog_settings_api.py tests/test_provider_settings.py tests/test_analysis_providers.py` |
| Progressive analysis, evidence and budgets | `pytest -q tests/test_progressive.py tests/test_progressive_api.py tests/test_evidence.py tests/test_processing.py tests/test_preprocessing.py` |
| Artifacts, graph, search and export | `pytest -q tests/test_artifacts.py tests/test_progressive_artifacts.py tests/test_pipeline_view_api.py tests/test_search.py` |
| Series identities and execution | `pytest -q tests/test_series.py tests/test_series_lifecycle.py tests/test_series_processing.py tests/test_series_processing_ui.py` |
| Simple listening and reader playback | `pytest -q tests/test_listening.py tests/test_listen_api.py tests/test_listen_ui.py tests/test_listen_player.py` |
| Voice examples and shared transport | `pytest -q tests/test_voice_previews.py tests/test_voice_preview_api.py`; also run `node --test tests/voice_preview_ui_test.js tests/listen_ui_test.js tests/listen_buffer_test.js tests/listen_player_test.js` |
| Local diagnostic storage/API | `pytest -q tests/test_diagnostics.py`; also run `node --test tests/diagnostics_ui_test.js` directly |
| Audio synthesis/cache/assembly | `pytest -q tests/test_audio.py tests/test_app.py` |
| Narration providers and Breeze | `pytest -q tests/test_narration_providers.py tests/test_audio.py tests/test_listen_api.py`; also run `node --test tests/breeze_ui_test.js tests/listen_player_test.js` |
| Library and resource use | `pytest -q tests/test_library_api.py tests/test_library_ui.py tests/test_resources.py tests/test_resources_ui.py` |

The default suites use synthetic prose, temporary stores, fake provider responses, and generated test WAVs. They do not need cloud keys or paid generation. [test_progressive.py](../tests/test_progressive.py) contains a structured fake provider; [test_listen_api.py](../tests/test_listen_api.py) explicitly blocks external HTTP while exercising jobs and reported usage. [listen_player_test.js](../tests/listen_player_test.js) executes the main player's actual functions against fake media, rather than merely asserting strings in a second implementation. Run [listen_buffer_test.js](../tests/listen_buffer_test.js), [listen_job_poll_test.js](../tests/listen_job_poll_test.js) and [diagnostics_ui_test.js](../tests/diagnostics_ui_test.js) through the complete Node command for buffering, cancellation-status recovery and best-effort browser logging.

The optional macOS integration test invokes actual local speech and assembly:

```sh
BARDIC_TEST_SYSTEM_AUDIO=1 uv run --frozen pytest -q tests/test_audio.py
```

Run that only on a machine with the speech services and ffmpeg available. It is a local audio test, not a Gemini test. A default test run should not inherit that opt-in unintentionally.

For manual checks, load the original story from the welcome screen, use a short synthetic TXT, or construct a temporary book through `parse_book`. Verify the relevant flow, errors, and keyboard/player behavior. Cloud account checks, cloud analysis, and Gemini narration are explicit provider actions and are not necessary to test the surrounding application.

## Code map and execution boundaries

| Responsibility | Main source |
| --- | --- |
| HTTP DTOs, runtime, job scheduling, UI presentation, downloads | [app.py](../bardic/app.py) |
| Exact source extraction, passage IDs, EPUB safety, cover extraction | [importer.py](../bardic/importer.py), [structure.py](../bardic/structure.py) |
| Mutable reader projection, selected enhanced takes, restart recovery | [store.py](../bardic/store.py) |
| Immutable versions, dependency edges, current artifact heads | [artifacts.py](../bardic/artifacts.py) |
| Local census and semantic coverage | [preprocessing.py](../bardic/preprocessing.py) |
| Discovery/profile/direction recipes and staged execution | [progressive.py](../bardic/progressive.py) |
| Structured provider adapters and evidence validation | [analysis.py](../bardic/analysis.py) |
| Local/legacy checkpoint execution | [staged_analysis.py](../bardic/staged_analysis.py) |
| Accepted unit cache, request reservations and usage | [processing.py](../bardic/processing.py) |
| Model inventory, roles, dated prices, explicit access checks | [model_catalog.py](../bardic/model_catalog.py), [account_checks.py](../bardic/account_checks.py) |
| Confirmed series identities and earlier-volume observations | [series.py](../bardic/series.py) |
| Series planning and parent/child execution | [series_processing.py](../bardic/series_processing.py) |
| Reversible library archive, metadata, covers, disk reporting | [library.py](../bardic/library.py) |
| Narration recipes, provider I/O, normalized audio | [audio.py](../bardic/audio.py), [take_archive.py](../bardic/take_archive.py) |
| Bounded audition inputs, immutable preview requests/takes | [voice_previews.py](../bardic/voice_previews.py), [static/voice-preview.js](../bardic/static/voice-preview.js) |
| Separate single-voice listening archive and equivalent-speech cache | [listening.py](../bardic/listening.py) |
| Pipeline view, typed story graph, portable analysis bundle | [pipeline_view.py](../bardic/pipeline_view.py) |
| Local lexical passage index | [search.py](../bardic/search.py) |
| Per-operation resource measurements | [resources.py](../bardic/resources.py) |
| Bounded operational diagnostics and redacted browser events | [diagnostics.py](../bardic/diagnostics.py), [static/diagnostics.js](../bardic/static/diagnostics.js) |

One runtime worker schedules normal analysis, enhanced narration, simple-listen and voice-preview jobs. Series execution is coordinated separately and allows at most two independent discovery workers; later interpretation follows reading order. Busy-book and active-series guards protect edits and membership changes. Cancellation is cooperative at request/unit boundaries; it is not a guarantee that a remote request or speech subprocess stops immediately.

SQLite uses WAL and foreign keys. Store operations share an `RLock`; connections and transactions are short-lived. Do not hold a database write transaction over a provider call. Accepted units and completed takes are durable before subsequent work proceeds. A server restart marks unfinished work interrupted rather than assuming it completed.

## Frontend namespace contracts

[app.js](../bardic/static/app.js) owns the selected book/chapter/passage, shared `Audio` element, reader highlighting, settings, and main job polling. [index.html](../bardic/static/index.html) loads the independent scripts before the application module. Each component owns only its mount container and scoped CSS.

The workspace separates the library landing view from the selected book. Returning to **Library** preserves playback; reopening the selected book does not create a new listening session. Title/author filtering is local. The **Read & listen**, **Cast & voices**, and **Studio** views use linked tab/tabpanel semantics, a single tab stop, and Left/Right/Home/End navigation. The chapter selector above the reader and the contents list share the same chapter-selection path. Keep these controls available on narrow layouts.

Use progressive disclosure for secondary controls: **Story analysis** holds stage planning, coverage and spending allowances; **Plan analysis** reveals it before scrolling and focusing the controls. Series membership/identity tools follow the cast in **Series & continuity**, and pipeline/resource inspectors follow the script in **Production details**. Each provider's settings and optional account checks also have their own disclosures. Opening settings for a missing provider key must reveal that provider before focusing its key field; native validation must also reveal the disclosure containing an invalid field. Disclosure changes must preserve existing feature mount IDs, unsaved input values and explicit generation boundaries.

Browser storage writes use the `bardic:` prefix and fall back to old `spintails:` values on reads. Preserve that compatibility when changing preference or reading-position storage. JavaScript feature interfaces use the `Bardic` prefix shown below.

| Namespace and source | Public interface |
| --- | --- |
| `BardicSeries` — [series.js](../bardic/static/series.js) | `render(container, book)` loads membership, explicit identity links, and prior context. |
| `BardicProduction` — [production.js](../bardic/static/production.js) | `render(container, book, {provider, chapterId, busy, scanModel, model, onStart, onRefresh})`; `onStart(payload)` dispatches a reviewed per-book plan. |
| `BardicPipeline` — [pipeline.js](../bardic/static/pipeline.js) | `render(container, book, {busy})`; reads stage status, history, graph, search and exports. It does not dispatch model work. |
| `BardicResources` — [resources.js](../bardic/static/resources.js) | `render(container, book, {busy})`; paged run/stage/operation measurements. |
| `BardicLibrary` — [library.js](../bardic/static/library.js) | `render(container, {busy, onChange, onSelectBook, onSelectSeries, books?, series?, storage?})`; `refresh(container)` explicitly reloads the snapshot. Selection callbacks receive IDs. |
| `BardicSeriesProcessing` — [series-processing.js](../bardic/static/series-processing.js) | `render(container, series, {status, onChange})`; `series` needs an ID, while name/books improve display. A new accepted fingerprinted preview is required for every dispatch. |
| `BardicListen` — [listen.js](../bardic/static/listen.js) | `render(container, book, {status, chapterId, segmentId, playbackRate, playing, preparing, previewing, busy, onChange, onPlay, onToggle, onRateChange, onPreview, onRefreshBreeze, beforeChapterPrepare, onStop, onJob})`, plus playback methods below. `onRefreshBreeze()` is called once when the user selects Breeze while its check state is `unchecked`; rendering never calls it. The panel keeps the latest `status` on its per-book state; chunked chapter mode is chosen from `status.narration_providers[provider].capabilities.chunked_listening`, falling back to Gemini-only when that metadata is absent. |
| `BardicVoicePreview` — [voice-preview.js](../bardic/static/voice-preview.js) | `configure({onStart,beforeRequest,onReady,onState,onStop})`, `start(book,config,label)`, `stop()`, `waitForStopped()`, `getState()`; explicit one-sample intent with independent job tracking. |
| `BardicDiagnostics` — [diagnostics.js](../bardic/static/diagnostics.js) | `record(event, details)` sends an allowlisted best-effort operational event; callers never await it to decide playback behavior. |

Listening exposes `isSimple(book)`/`enabled(book)`, `resolve(book, segment)`/`take(book, segment)`, `ensure(book, segment)`, `prepare(book, segment, {playbackRate, offset})`, `updatePlayback(book, segment, {playbackRate, currentTime})`, `prepareChapter(book, segment, {playbackRate})`, `getBuffer(book)`, `getSelection(book)`, `forgetAudio(book, segment)`, `stop(book)`, `waitForStopped(book)`, and `allowsAdvance(book, current, next)`.

The resolver reads the selected mode's saved audio. `ensure` serializes one passage request, deduplicates matching pending work and polls its job; it can cause paid generation. The main player uses `prepare` for a warmup of 10 listening seconds capped at three passages, then `updatePlayback` to drive a 45-listening-second rolling buffer capped at 12 future passages. These stay within the current chapter. `prepareChapter` is a separate explicit action for the remaining chapter and does not autoplay. `getBuffer` reports measured contiguous seconds at the current speed, passage count, target and preparation/error state. Merely rendering the panel loads saved metadata and must not start generation.

`getSelection` returns a copy of the effective mode/provider/voice/model. The simple panel delegates transport to `onToggle` (or `onPlay` for initial mode selection), validated speed changes to `onRateChange`, and explicit examples to `onPreview({provider,voice,model,segment_id})`. Speed changes preserve queue intent; a valid existing warmup can update its rate during render without starting rolling lookahead. `playing`/`preparing`/`previewing` come from the shared player. Rendering and dropdown changes never start an audition.

The outer **Listen your way** disclosure (`#listening-drawer`) starts closed to prioritize reading. Its summary keeps the selected playback mode, narrator setup, provider, charging status and chapter-preparation progress visible. A new preparation error opens the drawer once; later renders respect an explicit close while the same error remains. Normal playback updates do not open it. The footer's **Voice & listening settings** and reader's **Choose a narrator** actions open it and focus the voice selector. With enhanced mode selected, pressing Play with no take also reveals it without generating audio; a stale enhanced take keeps the regenerate-in-Studio guidance.

Inside, narrator selection, auditions and explicit playback are visible, while **More listening options** holds speed, speech model, remaining-chapter preparation and buffer details. The inner disclosure's open state survives parent renders; active preparation progress, retryable errors and provider cost/privacy information stay outside that inner disclosure. **One narrator** and **Studio voices** are display labels for the existing `simple` and `enhanced` modes. A fresh selection still defaults to enhanced mode; **Start simple listening** explicitly selects simple mode and starts playback. Opening either disclosure or rendering a compact panel must not generate audio.

Voice auditions use the shared audio element without moving the reader passage or persisting sample elapsed time as the book bookmark. Starting one pauses reading and cancels lookahead. Closing it restores the paused offset; finishing does not autoplay the book. The controller serializes sample requests, coalesces identical pending intent, waits for a known cancelled job to settle, calls `beforeRequest` before submitting the sample, and ignores late success/error after Stop or replacement. Cast requests read unsaved voice/direction form fields; Studio requests read the unsaved speaker and passage direction. Do not save/restore cast edits to implement an audition. Contextual source and the 400-code-point cap are enforced server-side. Cache lookup precedes provider availability; new synthesis is a single explicit request with no automatic POST retry.

`waitForStopped(book)` is a read-only barrier after Stop: it awaits a pending listening POST, then polls any known old job to a terminal state. It cannot queue synthesis or start playback. Stale book/intent changes return false; polling exhaustion retains its descriptive error. The audition controller uses it before its own POST so an in-flight listening request can finish cooperatively without a conflicting generation request. Its own `waitForStopped()` provides the reverse read-only barrier before normal narration resumes. Explicit chapter preparation and chapter retries await `beforeChapterPrepare()` after creating a cancellable chapter intent, before enqueueing their first passage. The parent connects this hook to the audition barrier; Stop, book/configuration changes or a newer intent invalidate the waiting chapter. A barrier failure is shown as a retryable preparation error and submits no narration. Keep this boundary specific to chapter preparation: a generic pre-synthesis hook could make opposing preview/listening barriers wait on each other.

`stop` invalidates pending intent, clears queued requests and asks to cancel the active job. A stale async task resolves to `null`; a POST with an uncertain outcome is never automatically resent. Read-only polling has bounded retries. The main player must also check its selection/play generation after awaiting preparation. It locally preloads only the next two available clips and clears those preloads when playback stops. A media error drops the failing browser cache entry and stops lookahead so a later explicit Play can recheck server state. Enhanced studio previews use the enhanced take and end after that passage; they must not trigger simple continuation.

Preserve component inputs during unrelated parent renders. Use generation/selection tokens to reject late successes **and** errors after book, series, provider, or model changes. Keep pending-control state separate from audio playback state. The fake-DOM tests exercise these contracts, but do not replace a browser layout check. Render imported prose, provider text, errors, and artifact JSON as escaped text or `textContent`.

Diagnostics deliberately accept event/operation enums and bounded operational identifiers/numbers, not arbitrary objects or exception strings. Add a code to both allowlists when introducing a supported event; do not loosen validation to pass messages, URLs, source text, stacks or credentials. Server callers use `record_safely` so a logging error cannot replace a real narration outcome. Verify duplicate/rate/retention bounds and privacy rejection with synthetic IDs. Diagnostic rows may be pruned; never use them as dependencies for immutable analysis or billing evidence.

## Extending the system

### Add or change an analysis provider

1. Implement the structured request adapter in [analysis.py](../bardic/analysis.py), preserving its `(client, model, key, prompt, schema, cancelled)` contract and semantic validators. The existing adapters return parsed objects, not replacement book prose.
2. Register labels/defaults/roles/inventory in [model_catalog.py](../bardic/model_catalog.py) and the runtime/settings interfaces in [app.py](../bardic/app.py). Review both discovery and detailed-analysis model roles. An inventory listing is not proof of structured-output support or account credit.
3. Route every analysis HTTP attempt through the request-budget context. Reserve before sending; retain uncertain charges; record usage only when reported. Add an explicit account-check adapter only if that operation is supported.
4. Update the progressive request dispatch and UI provider choices. Keep provider selection explicit; do not silently send text to another provider after failure.
5. Add fake success, malformed response, unavailable model, authentication, rate-limit, retry, cancellation, and redaction tests. Document any new environment variable and cost assumptions. Unknown prices must remain unknown.

A custom model ID can already be selected without adding a new provider. A local LLM provider, embeddings, and external batch execution are not implemented simply by entering such an ID.

### Add an analysis stage or change a recipe

Define the stage's source inputs, output schema, validator, and stable unit key in [progressive.py](../bardic/progressive.py). Include inputs that actually affect the result in the recipe identity; avoid invalidating unrelated work. Save the input recipe/dependencies and accepted output through [ProcessingStore](../bardic/processing.py). Keep rejected output distinguishable from accepted knowledge. Emit events tied to the precise attempt, so HTTP success does not imply validation success.

Update preview estimates, current/stale detection, pipeline dependency/status display, and export retention. Test a cold run, a cache hit, a changed dependency, failure after a durable unit, cancellation, resume, and a manually reviewed item. Use [test_progressive_artifacts.py](../tests/test_progressive_artifacts.py) and [test_pipeline_view_api.py](../tests/test_pipeline_view_api.py) for examples. The stage cards describe real saved state; there is no generic DAG executor to register a stage with automatically.

These instructions apply to semantic analysis stages. Local preprocessing and narration have their own execution modules; extend those modules and their resource/artifact hooks instead of routing every operation through `progressive.py`.

### Add a narration backend or change audio behavior

Keep transcript and performance metadata separate. Update the recipe and `render_fingerprint` whenever audible inputs change. A recipe hash identifies instructions; the content hash identifies a particular WAV asset. Use `produce_take` to publish validated immutable bytes and only then select the take. Do not overwrite an old asset on retake. Test model/voice/direction invalidation, restoring an earlier recipe, invalid audio, interrupted generation, and exact assembly timing.

Simple speech lookup additionally uses a versioned content key that excludes source IDs; the retained target take still needs exact source validation and an explicit reference to the reused input take. Never replace its real producer fingerprint with a hypothetical fingerprint for the new location. Test identical text across passages/books, changed voice/model/text, missing or corrupt WAVs, hash mismatch, legacy lazy indexing and cache playback with no key. API tests should verify duplicate active POSTs join one job while cancelled jobs cannot be revived. Player tests should cover warmup, rate-aware lookahead, chapter limits, Stop/pause/configuration changes, failure after a ready passage, explicit chapter preparation and media-error recovery.

Provider-specific code sits behind `audio.PROVIDERS`, `_RECIPES` and `_generate`, with voices read through `voice_selection()`/`voice_id()` rather than character fields. A new provider must keep the recorded fingerprints, listening session IDs and synthesis keys in [test_narration_providers.py](../tests/test_narration_providers.py) unchanged; if one moves, existing takes and caches silently stop matching. A provider whose server state can change under a stable ID (as Breeze voices can) must pin that state into the local choice so fingerprints never need the network, then verify it live before sending. Its tests there use `FakeBreeze`, an offline implementation of the documented HTTP/SSE contract, installed through the module seams `breeze._transport` (an `httpx.MockTransport`) and `breeze._sleep` (so retry waits take no time). App tests clear `BREEZE_TTS_URL`/`BREEZE_API_KEY`. A live check against a real server is manual and optional; use original synthetic text.

Simple listening must remain independent of character casting, scene direction, selected enhanced takes, and enhanced artifacts. Its own table and audio directory are intentional. A new simple-listening feature needs corresponding repository, API, and player-race tests; changing only the main `playable` helper can accidentally affect studio previews or audiobook export.

### Add measurements or a new UI component

Use one non-nested `ResourceLedger.operation` for a local/narration leaf operation. Analysis HTTP attempts already have a ledger; do not wrap them again and double-count. `publish_metrics` records provider-reported fields, not guessed values. CPU opt-in measures the current Python thread, excluding subprocesses and remote compute. Never put source text, prompts, keys, or exception dumps in metrics.

For UI work, add a standalone namespace, a stable mount, scoped styles, and deterministic fake-fetch/media tests. Keep source-display safety and stale-response guards. Prefer tests of user-visible behavior and request boundaries over assertions that duplicate the implementation.

## Invariants and known hazards

- Source coordinates are chapter-local Python Unicode character offsets with an exclusive end. JavaScript string offsets are UTF-16 and differ around some characters. Preserve stable IDs and exact source text; do not directly apply Python offsets to JS strings. The reader uses presented gaps/text matching, and graph references verify their anchors.
- A mention, an attributed speaker, and physical presence are different claims. Whole-book discovery completion does not prove every character profile is current or accurate. Local heuristic drafts do not count as semantic discovery.
- Human-reviewed profiles/passages are authoritative. Series identities join only through explicit links and confirmed order; names alone do not merge characters, and later volumes must not leak into earlier-book context.
- `ArtifactRepository` versions and dependency edges are immutable. Change current selections through supported APIs; do not update historical rows to make tests pass. Legacy records must disclose absent provenance.
- Read views can populate free local caches/indexes, retain legacy artifact snapshots, or measure operations. “No model work” does not mean “zero local writes.” Test source/projection preservation rather than assuming every GET leaves all database tables unchanged.
- Analysis limits cover analysis attempts. They are not global account balances and do not impose a separate Gemini narration spending cap. Simple listening requests one passage at a time for its bounded buffer or explicit chapter preparation; a submitted cloud passage can still finish after Stop.
- Archive is reversible removal from normal views, not file deletion or storage reclamation. Portable analysis ZIPs contain source/provenance and transitive earlier-book inputs; treat them as user content. They include saved resource operations and simple-listening session/take metadata when those tables exist, but exclude all audio binaries. Audiobook ZIPs contain enhanced production audio; separate simple-listening WAVs are not bundled by either export.
- The server is a local single-user app, with loopback binding, trusted-host checks, and same-origin write checks. It has no user authentication or hosted/multi-user deployment model. Keep the default local execution boundary.
- Word alignment, automatic transcript verification, voice cloning, and trained local neural narration are not implemented. Current highlighting follows complete passage audio boundaries.
