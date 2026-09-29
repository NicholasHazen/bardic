# Development guide

This guide describes the code in this checkout. Start with [README](../README.md) for the product workflow and [API](API.md) for route contracts. The [storage guide](ARTIFACTS-AND-STORAGE.md) explains retained data; the research documents describe design decisions and proposals, not necessarily implemented features.

## Reproducible setup

The application uses Python 3.11 or newer, FastAPI, SQLite, and browser JavaScript. Dependencies and version ranges are in [pyproject.toml](../pyproject.toml); resolved dependencies are in [uv.lock](../uv.lock). There is no `requirements.txt`, frontend bundler, or asset build step. The app and its UI tests need no npm packages. [package.json](../package.json) pins development-only tools (openapi-typescript and TypeScript) for the contract code-generation check; install them with `npm ci`.

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

Open `http://127.0.0.1:8765`. Stop the process with Ctrl+C. To keep the owner's library running in the background instead, use the [service](OPERATIONS.md#run-as-a-service). Python dependencies must be available locally or downloaded during the first sync. The examples below use a POSIX shell; adapt environment assignment and virtual-environment paths for other shells.

Node.js is required to execute the JavaScript tests. The UI tests use Node built-ins and need no npm dependencies. Only `npm run contract:codegen` (and its pytest wrapper, which skips without `npm ci`) uses the pinned dev tools. Use a Node version that provides `node:test`, `structuredClone`, and `FormData` (Node 20+ is a practical baseline). The Python wrappers skip their JavaScript checks if `node` is absent, so a passing pytest run alone does not prove that the UI tests ran.

Device narration additionally requires macOS `say`, installed voices, and `ffmpeg`. Other operating systems can run the app and use Gemini narration, but do not gain a local speech backend automatically. Provider adapters and format validation live in [audio.py](../bardic/audio.py).

## Configuration and data isolation

[The entry point](../bardic/__main__.py) loads the `.env` beside `pyproject.toml` through [config.py](../bardic/config.py). It does not search parent directories. Existing process environment variables win, and dotenv interpolation is disabled. Make a new local configuration from [.env.example](../.env.example) only if you do not already have a `.env`.

| Variable | Effect |
| --- | --- |
| `BARDIC_PORT` | HTTP port; default `8765`. |
| `BARDIC_HOST` | Bind address; default `127.0.0.1`, or `0.0.0.0` when `BARDIC_LAN_NAME` is set. |
| `BARDIC_LAN_NAME` | Opt-in network name such as `bardic`: binds the network, trusts `bardic.local` and advertises it through macOS `dns-sd`. No authentication; see [local-network access](OPERATIONS.md#local-network-access). |
| `BARDIC_ALLOWED_HOSTS` | Extra comma-separated Host names or addresses to trust, without ports or wildcards. |
| `BARDIC_CORS_ORIGINS` | Opt-in CORS for browser clients on other origins; unset or empty (the default) sends no CORS header and keeps the cross-origin write guard. `*` allows every origin and prints a startup warning; otherwise a comma-separated list of exact origins such as `https://app.example,http://localhost:5173` (scheme, host, optional port). A malformed value stops startup. No credentials, no authentication, and the trusted-host check is unchanged. See [API.md](API.md#transport-and-security) and [cors.py](../bardic/cors.py). |
| `BARDIC_DATA_DIR` | Data directory; default `.bardic` relative to the process working directory, or existing `.spintails` when `.bardic` is absent. |
| `GEMINI_API_KEY` | Gemini analysis and narration key. |
| `GOOGLE_API_KEY` | Gemini fallback when `GEMINI_API_KEY` is empty or absent. |
| `OPENAI_API_KEY` | OpenAI analysis key. |
| `ANTHROPIC_API_KEY` | Anthropic analysis key. |

The old `SPINTAILS_PORT` / `SPINTAILS_DATA_DIR` settings remain aliases. Shell settings win over file values even across prefixes; within one source the `BARDIC_` spelling wins. `bardic.config.data_directory()` resolves explicit configuration and the existing-library fallback without moving files. Use it in maintenance tools instead of hardcoding a default path. The old module launcher remains available; source imports and new scripts use `bardic`. See [upgrade details](OPERATIONS.md#upgrading-from-spin-tails).

Changing `.env` requires a server restart. Settings saves model preferences in SQLite but keeps changed API keys only in the current runtime's memory. Settings does not rewrite `.env`; restarting reloads configured environment/file keys. Never copy `.env`, user ebooks, generated audio, library databases, or provider response dumps into fixtures or documentation.

Use an isolated development server for manual checks. From the checkout or worktree you are changing:

```sh
./bardicctl dev start      # prints http://127.0.0.1:87xx
./bardicctl dev start --keys   # the same, with the service checkout's provider keys for a live test
./bardicctl dev restart    # after Python edits; keeps the port and library, drops keys unless --keys
./bardicctl dev list       # development servers from every checkout
./bardicctl dev logs -f
./bardicctl dev stop
```

It runs this checkout's code in the background on the first free port from 8770 to 8799, with a scratch library in `~/.cache/bardic-dev/<name>/library`. The name defaults to the checkout's directory name; `--name` runs a second server, and `BARDIC_DEV_HOME` moves the root. If another checkout already uses that name, the command stops and asks for `--name`. Provider keys, the Breeze and analysis server URLs and the network settings are blank and the bind is loopback, even when the checkout has a `.env`. `--keys` fills `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `BREEZE_TTS_URL`, `BREEZE_API_KEY` and the self-hosted analysis server URLs (`BARDIC_LOCAL_LLM_URL`, `BARDIC_BOOKNLP_URL`, `BARDIC_NOVEL_ANALYZER_URL`) from the `.env` of the checkout the service runs (normally the main checkout). No other setting comes from that file: the data directory, port and network settings stay the development server's own. Shell values for those keys are ignored, since the calling agent's own `ANTHROPIC_API_KEY` is not a Bardic key. The start prints which names it passed (never the values), `dev list` marks the server `live`, and a restart without `--keys` blanks them again. Copying `.env` into a worktree does not give a development server keys, and it would point a hand launch there at the owner's library. A keyed server makes real requests on the owner's accounts: they may be billed, and they share rate limits and daily quotas with the service. See the cost boundaries in [AGENTS.md](../AGENTS.md#work-and-cost-boundaries). `--library` points at a restored copy. A restored copy keeps its own saved settings, including a Breeze server URL. A library that is, contains or lies inside the service's library is refused, as is the service's port. `dev start` and `dev restart` reuse the previous port and library, so the browser origin and its saved reading position stay the same. The start succeeds only when its own process holds the port and answers; otherwise it stops that process and prints the log. Starts are serialized, so concurrent sessions get distinct ports. The library and `server.log` remain after `stop` for inspection; delete that directory when you are finished. Any session can list or stop any development server. Each record includes the process start time, so a reused process ID is never signalled.

The owner's library is served separately by the [service](OPERATIONS.md#run-as-a-service) from the main checkout. `./bardicctl status` is read-only and shows what serves its port, who owns the process and whether jobs are active.

Development servers are processes, not containers. Worktrees already isolate code, and the port, library and blank keys isolate runtime state. A Linux container would lose macOS `say` voices and `dns-sd`. With Docker Desktop, the library would also cross a VM boundary. SQLite WAL needs every process to share memory on one host, and `server.lock` is not guaranteed to exclude a host process across that boundary, so a container mounting the real library could run beside the service. Reconsider this if the project gains Linux-only dependencies or a CI image.

The manual equivalent starts with cloud keys explicitly blank, even if the checkout has a configured `.env`:

```sh
scratch_dir=$(mktemp -d "${TMPDIR:-/tmp}/bardic-dev.XXXXXX")
BARDIC_DATA_DIR="$scratch_dir" BARDIC_PORT=8766 BARDIC_LAN_NAME='' BARDIC_HOST='' BARDIC_ALLOWED_HOSTS='' BARDIC_CORS_ORIGINS='' GEMINI_API_KEY='' GOOGLE_API_KEY='' OPENAI_API_KEY='' ANTHROPIC_API_KEY='' uv run --frozen python -m bardic
```

The empty network settings keep a development server on loopback even when the owner's `.env` enables [local-network access](OPERATIONS.md#local-network-access); empty shell values win over the file. If the chosen port already answers on `127.0.0.1`, the launcher refuses to start rather than sharing the port with a network-bound server. Choose another port.

Keep that shell's printed/assigned `scratch_dir` available if you want to inspect the scratch library later. Stop the process before removing or backing up its data. Neither the normal `.bardic` nor legacy `.spintails` library is a disposable test fixture.

The runtime uses an OS lock on `server.lock` before performing startup recovery. A second server using the same directory is rejected. Do not remove the lock or bypass it to run another worker against a live library. An import of `bardic.app` constructs the FastAPI application but starts its `Runtime` only when lifespan begins. Tests should use `TestClient(create_app(tmp_path))` as a context manager so the worker pool and lock close reliably. Network settings are read when `create_app()` runs; [test_lan.py](../tests/test_lan.py) covers them with a fake `dns-sd`, plus real-process checks that the helper cannot outlive a killed server.

The ordinary launch command deliberately has no reload flag. For Python edits, restart the isolated server (`./bardicctl dev restart`). Browser files are served directly; refresh the browser after edits. Launching `uvicorn bardic.app:app` directly bypasses the project dotenv loader unless you load that configuration yourself. Multiple uvicorn workers are inappropriate for the same data directory.

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

Every `/api` response a pytest test receives is validated against the published contract by [conftest.py](../tests/conftest.py). An undeclared response field or an undocumented error status fails the test. After changing a route, follow [the API workflow](API-WORKFLOW.md): describe the change in `bardic/apispec/`, then run `uv run --frozen python -m bardic.apispec`. The wire says passage, provider enumerations are named once, and every union of objects is a named tagged `oneOf`; `bardic/wire.py` translates storage names to wire names at the boundary. `--check` only reports whether `contract/` is stale. To list every contract problem in a run without failing tests, set `BARDIC_CONTRACT_REPORT=<file>`; each checked call is appended to that file as a JSON line.

### Conformance suite and contract pin

[`conformance/`](../conformance/README.md) is a black-box suite that drives any server over HTTP and validates every response against `contract/openapi.json` alone, with undeclared fields refused at any depth. It never imports `bardic`, so the same suite is the acceptance test for a replacement server. It is not part of the default `pytest -q` run.

```sh
uv run --frozen pytest conformance/selftest   # the checker itself; no server
uv run --frozen pytest conformance            # spawns this checkout's server on a scratch library, blank keys
uv run --frozen pytest conformance --base-url http://127.0.0.1:PORT    # a disposable server you started
```

It refuses a non-loopback URL without `--allow-remote` and port 8765 (the owner's service) without `--allow-owner-port`. Operations that need a paid provider are listed in `conformance/needs_provider.txt` until fake providers exist. The Rust port plan is in [RUST-SERVER-PLAN.md](RUST-SERVER-PLAN.md).

`tools/contract_pin.py` copies the contract into another repository and checks it later (standard library only, so it can be copied along): `pin --from SRC --to DEST` writes `contract/PIN.json` with the version and file hashes; `check --dest DEST [--from SRC]` fails on a hand-edited or stale pin; `log --dest DEST --from SRC` prints the changelog entries added since the pin. It refuses a source whose `openapi.json` changed after its version was recorded.

Useful targeted suites:

| Change | Focused command after `uv run --frozen` |
| --- | --- |
| HTTP contract (`bardic/apispec/`, `contract/`) | `pytest -q tests/test_contract.py tests/test_contract_*.py`, then the suites for the routes you changed |
| EPUB/text extraction and structure | `pytest -q tests/test_importer.py tests/test_series_structure_api.py` |
| Provider configuration and model catalog | `pytest -q tests/test_model_catalog.py tests/test_catalog_settings_api.py tests/test_provider_settings.py tests/test_analysis_providers.py` |
| Evidence, request builders, census and budgets | `pytest -q tests/test_evidence.py tests/test_evidence_projection.py tests/test_prompt_identity.py tests/test_processing.py tests/test_preprocessing.py tests/test_analysis.py` |
| Analysis pipeline steps, versions and acceptance | `pytest -q tests/test_analysis_pipeline.py tests/test_analysis_pipeline_ui.py`; also run `node tests/analysis_pipeline_ui_test.js` |
| Classic removal (the engine stays gone; the startup migration retains, then drops, its data) | `pytest -q tests/test_legacy_isolation.py tests/test_classic_data_drop.py tests/test_artifacts.py` |
| Artifacts, graph, search and export | `pytest -q tests/test_artifacts.py tests/test_pipeline_view_api.py tests/test_search.py`; also run `node --test tests/pipeline_ui_test.js` |
| Series identities and execution | `pytest -q tests/test_series.py tests/test_series_lifecycle.py tests/test_series_processing.py tests/test_series_processing_ui.py` |
| Simple listening and reader playback | `pytest -q tests/test_listening.py tests/test_listen_api.py tests/test_listen_ui.py tests/test_listen_player.py` |
| Voice examples and shared transport | `pytest -q tests/test_voice_previews.py tests/test_voice_preview_api.py`; also run `node --test tests/voice_preview_ui_test.js tests/listen_ui_test.js tests/listen_buffer_test.js tests/listen_player_test.js` |
| Local diagnostic storage/API | `pytest -q tests/test_diagnostics.py`; also run `node --test tests/diagnostics_ui_test.js` directly |
| Audio synthesis/cache/assembly | `pytest -q tests/test_audio.py tests/test_app.py` |
| Narration providers and Breeze | `pytest -q tests/test_narration_providers.py tests/test_audio.py tests/test_listen_api.py`; also run `node --test tests/breeze_ui_test.js tests/listen_player_test.js` |
| Voice library, Cast tab and Voices page | `pytest -q tests/test_voice_library.py tests/test_narration_providers.py`; also run `node --test tests/voices_ui_test.js tests/breeze_ui_test.js tests/workspace_ui_test.js tests/listen_player_test.js` |
| Design tokens, UI primitives and CSS | `pytest -q tests/test_ui_budget.py tests/test_ui_contrast.py tests/test_ui_kit.py`; also run `node --test tests/ui_kit_test.js`, and check `/static/kitchen-sink.html` in a browser |
| Reader follow-the-narration scrolling | `node --test tests/follow_scroll_test.js`; then check in a dev server at an iPad viewport with large text: record `scrollY` per frame during playback (no step over ~20 px per frame, no frame gaps) and confirm the active passage sits near 38% of the band |
| Book tabs, lifecycle strip, routes and copy | `pytest -q tests/test_ui_structure.py`; also run `node --test tests/lifecycle_test.js tests/copy_lint_test.js tests/workspace_ui_test.js`, and check each tab at 1440 and 390 px wide |
| Library and resource use | `pytest -q tests/test_library_api.py tests/test_library_ui.py tests/test_resources.py tests/test_resources_ui.py` |

The default suites use synthetic prose, temporary stores, fake provider responses, and generated test WAVs. They do not need cloud keys or paid generation. [test_analysis_pipeline.py](../tests/test_analysis_pipeline.py) contains a structured fake provider; [test_listen_api.py](../tests/test_listen_api.py) explicitly blocks external HTTP while exercising jobs and reported usage. [listen_player_test.js](../tests/listen_player_test.js) executes the main player's actual functions against fake media, rather than merely asserting strings in a second implementation. Run [listen_buffer_test.js](../tests/listen_buffer_test.js), [listen_job_poll_test.js](../tests/listen_job_poll_test.js) and [diagnostics_ui_test.js](../tests/diagnostics_ui_test.js) through the complete Node command for buffering, cancellation-status recovery and best-effort browser logging.

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
| Local census | [preprocessing.py](../bardic/preprocessing.py) |
| Discovery/profile/direction request builders (prompts, schemas, source locators) | [pipeline/prompts.py](../bardic/pipeline/prompts.py) |
| Analysis steps, runner, versions, acceptance and evidence projection | [pipeline/](../bardic/pipeline/) ([steps](../bardic/pipeline/steps/)) |
| Structured provider adapters and evidence validation | [analysis.py](../bardic/analysis.py) |
| Request reservations, attempts, events and usage | [processing.py](../bardic/processing.py) |
| Model inventory, roles, dated prices, explicit access checks | [model_catalog.py](../bardic/model_catalog.py), [account_checks.py](../bardic/account_checks.py) |
| Confirmed series identities, earlier volumes' accepted evidence (series memory) and link suggestions | [series.py](../bardic/series.py) |
| Series planning and parent/child execution | [series_processing.py](../bardic/series_processing.py) |
| Reversible library archive, metadata, covers, disk reporting | [library.py](../bardic/library.py) |
| Narration recipes, provider I/O, normalized audio | [audio.py](../bardic/audio.py), [take_archive.py](../bardic/take_archive.py) |
| Bounded audition inputs, immutable preview requests/takes | [voice_previews.py](../bardic/voice_previews.py), [static/voice-preview.js](../bardic/static/voice-preview.js) |
| Separate single-voice listening archive and equivalent-speech cache | [listening.py](../bardic/listening.py) |
| Pipeline view, typed story graph, portable analysis bundle | [pipeline_view.py](../bardic/pipeline_view.py) |
| Local lexical passage index | [search.py](../bardic/search.py) |
| Per-operation resource measurements | [resources.py](../bardic/resources.py) |
| Bounded operational diagnostics and redacted browser events | [diagnostics.py](../bardic/diagnostics.py), [static/diagnostics.js](../bardic/static/diagnostics.js) |
| launchd service and development-server control for `./bardicctl`; not imported by the app | [service.py](../bardic/service.py), [bardicctl](../bardicctl) |

One runtime worker schedules normal analysis, enhanced narration, simple-listen and voice-preview jobs. Series execution is coordinated separately: one book at a time in reading order, each as a step-pipeline run. Busy-book and active-series guards protect edits and membership changes. Cancellation is cooperative at request/unit boundaries; it is not a guarantee that a remote request or speech subprocess stops immediately.

SQLite uses WAL and foreign keys. Store operations share an `RLock`; connections and transactions are short-lived. Do not hold a database write transaction over a provider call. Accepted units and completed takes are durable before subsequent work proceeds. A server restart marks unfinished work interrupted rather than assuming it completed.

## Frontend namespace contracts

[app.js](../bardic/static/app.js) owns the selected book/chapter/passage, shared `Audio` element, reader highlighting, settings, and main job polling. [index.html](../bardic/static/index.html) loads the independent scripts before the application module. Each component owns only its mount container and scoped CSS.

New UI uses the design tokens ([tokens.css](../bardic/static/tokens.css), loaded first), the primitives in [components.css](../bardic/static/components.css) and `window.BardicUI` ([ui.js](../bardic/static/ui.js), the first deferred script). [UI-GUIDE.md](UI-GUIDE.md) says when to use each; `/static/kitchen-sink.html` shows them all. `tests/test_ui_budget.py` ratchets raw colours, literal font sizes, `!important`, `*-badge|*-message|*-error|*-help` class names and escape-helper copies against `tests/ui_budget.json`; `tests/test_ui_contrast.py` checks the token and reader-theme contrast pairs.

The workspace separates the library landing view from the selected book. Returning to **Library** preserves playback; reopening the selected book does not create a new listening session. Title/author filtering is local. The **Read & listen**, **Analyze**, **Cast**, **Script & record** and **Details** views (IDs `read`, `analysis`, `cast`, `studio`, `details`) use linked tab/tabpanel semantics, a single tab stop, and Left/Right/Home/End navigation. **Voices** is an app-level page (`#voices-view`, sidebar **Voices**) that `setTab('voices')` opens with or without a book. `shell.js` keeps the hash route (`#/library`, `#/voices`, `#/book/<id>/<tab>`), the breadcrumb and the lifecycle strip in step with `setTab`/`selectBook`; app.js exposes `window.BardicApp` for it and calls it through optional hooks. The chapter selector above the reader and the contents list share the same chapter-selection path. Keep these controls available on narrow layouts.

Reader scrolling follows the narration through `follow-scroll.js`, not `scrollIntoView`. Each frame it recomputes, from live geometry, where the active passage should sit (its start on a line 38% down the band between the reader bar and the player, kept wholly in the band; a passage taller than the band is tracked by audio progress) and moves `scrollY` there with a critically damped spring, so a new passage, a text-size or width change or a layout shift retargets the glide instead of restarting it. A manual scroll, a finger down, or the follow toggle holds it before the next write, so iOS momentum is not fought; `force` (entering the reader, **Back to narration**) overrides the hold once. Distances over 2.5 bands and reduced motion jump. The 12 s manual hold (`MANUAL_SCROLL_MS`) is unchanged and now also starts on Page Up/Down, Home/End and arrow-key passage navigation.

The **Analyze** tab is the only way to run text analysis from a book; Cast's **Analyze the story** and the lifecycle strip's **Next** open it. The older phase runner ("Classic") was removed in [stage 3](CLASSIC-REMOVAL.md), with its routes and its panel. Use progressive disclosure for secondary controls: series membership/identity tools follow the cast in **Series & continuity**, and the pipeline and resource inspectors live in the **Details** tab. Each provider's settings and optional account checks also have their own disclosures. Opening settings for a missing provider key must reveal that provider before focusing its key field; native validation must also reveal the disclosure containing an invalid field. Disclosure changes must preserve existing feature mount IDs, unsaved input values and explicit generation boundaries.

Browser storage writes use the `bardic:` prefix and fall back to old `spintails:` values on reads. Preserve that compatibility when changing preference or reading-position storage. JavaScript feature interfaces use the `Bardic` prefix shown below.

| Namespace and source | Public interface |
| --- | --- |
| `BardicSeries` — [series.js](../bardic/static/series.js) | `render(container, book)` loads membership, explicit identity links, and prior context. |
| `BardicAnalysisPipeline` — [analysis-pipeline.js](../bardic/static/analysis-pipeline.js) | `render(container, book, {busy, status, onJobStarted, onBookChanged})` paints the Analysis tab. It loads nothing until its panel is visible, and polls only while a pipeline run is active and the panel is shown. Dropdown changes save settings and never start work; runs need a previewed plan and Confirm (with its fingerprint); Accept needs an impact preview and Confirm (with `expected_revision`), then calls `onBookChanged`, which reloads the book, open references, the library and the voice library. `selectStep(stepId)` opens a step from outside the tab (now if shown, else on the next visit). Result-table cells that hold stable identifiers (`kind` and `check` of Quotes rows, `check` with `check_speaker` of Directing rows) are shown through `ROW_LABELS`, the UI's own words for them; the server sends identifiers, never labels. **Show in text** dispatches a cancelable `bardic:show-passage` `{bookId, segmentId, chapterId}` event on `document`; a view that opens the passage itself calls `preventDefault()` (Script & record's `BardicScript` does, for a passage of the open book), otherwise `shell.js` falls back to Read & listen. |
| `BardicScript` — [script.js](../bardic/static/script.js) | `attach({$, state, patch, applyBook, playable, setChapter, setTab, scrollMotion, icon, formatTime, busy, paidRender})` once from app.js; `render()` paints the chapter's script into `#script-review`, `#scene-list`, `#studio-chapter` and `#script-meta` (called from `renderStudio`). Edits save on change with `PATCH /api/books/{id}/passages|scenes/{id}` carrying only the changed field, serialized; `flush()` saves waiting text now, `hasUnsaved()` reports unsaved or failed edits, `submit(form)` saves a row (Enter), `assign(ids, speakerId)` sets one speaker on several passages (one request each, in order, failures stay selected). It handles `bardic:show-passage` and never starts narration: Hear example, Record and Narrate scene keep app.js's handlers and estimate→confirm. Pure helpers (`flags`, `counts`, `matches`, `visibleSegments`, `chapterMatches`, `bulkText`) are tested in `tests/script_ui_test.js`. |
| `BardicPipeline` — [pipeline.js](../bardic/static/pipeline.js) | `render(container, book, {busy})`; reads stage status, history, graph, search and exports. It does not dispatch model work. |
| `BardicResources` — [resources.js](../bardic/static/resources.js) | `render(container, book, {busy})`; paged run/stage/operation measurements. |
| `BardicLibrary` — [library.js](../bardic/static/library.js) | `render(container, {busy, onChange, onSelectBook, onSelectSeries, books?, series?, storage?})`; `refresh(container)` explicitly reloads the snapshot. Selection callbacks receive IDs. |
| `BardicSeriesProcessing` — [series-processing.js](../bardic/static/series-processing.js) | `render(container, series, {status, onChange, onOpenSettings})`; `series` needs an ID, while name/books improve display. Reads step definitions from `GET /api/analysis-pipeline`; one step per run. Every dispatch needs a fresh, unblocked, fingerprinted preview confirmed through `BardicUI.consent`. `consentFor` and `childState` are exported pure helpers. |
| `BardicListen` — [listen.js](../bardic/static/listen.js) | `render(container, book, {status, chapterId, segmentId, playbackRate, playing, preparing, previewing, busy, onChange, onPlay, onToggle, onRateChange, onPreview, onRefreshBreeze, beforeChapterPrepare, onStop, onJob})`, plus playback methods below. `onRefreshBreeze()` is called once when the user selects Breeze while its check state is `unchecked`; rendering never calls it. A `voiceLibrary` option (the `/api/voices` snapshot) supplies Breeze **Default (<name>)** and library voices (`library:<id>`) for Breeze and Gemini; the remembered session key includes the resolved voice identity from `BardicVoices.cast.identity`, so a new current version or default never reuses a stale session. The panel keeps the latest `status` on its per-book state; chunked chapter mode is chosen from `status.narration_providers[provider].capabilities.chunked_listening`, falling back to Gemini-only when that metadata is absent. The panel renders only listening-level controls (the sheet owns the narrator choice); a `visible()` option gates the chapter preview POST to when the sheet is open. `choices(book, draft)` describes a narrator draft (`{mode, provider, voices}`) without changing anything, `apply(book, draft)` switches with one stop and resolves after the new narrator's saved audio is read, `statusInput(book)` returns the engine facts for `BardicListenStatus`, and `act(book, 'retry' or 'resume')` runs a pill action through the paid-consent path. Each narrator configuration's session ID is remembered (`sessions`, at most 12) so switching back reads its takes instead of requesting them. |
| `BardicListenStatus` — [listen-status.js](../bardic/static/listen-status.js) | Pure: `compute(input)` returns exactly one of `STATES` (`needs-narrator`, `preparing`, `playing`, `waiting-for-rate-limit`, `paused`, `limit-reached`, `stopped-by-you`, `failed`, `interrupted`, `finished`) with `label`, `tone` (the `BardicUI.statusTone` tone), `detail` and `action`; `announcement(previous, next)` is non-empty only for a change of state; `readyRanges(entries, total)` merges prepared audio on the chapter timeline. Tested in `tests/listen_status_test.js`. |
| `BardicPlayer` — [player.js](../bardic/static/player.js) | `attach({$, audio, timeline, position, seek, playing, pause, statusInput, openNarrator, act, chapterStep})` once from app.js; `update(facts)` paints the status pill, live-region announcement, ready range and Media Session position; `skip(seconds)`, `seekTo(time)`, `chapter(delta)`; `setSleep('off' or '15'…'60' or 'chapter')`, `stopAtChapterEnd()` (called by app.js before crossing a chapter) and `sleepState()`. `pause` must be the Pause button's own path. |
| `BardicVoices` — [voices.js](../bardic/static/voices.js) | `render(container, {library, status, book, busy, provider, onChange, onRefreshBreeze, onBeforeAudition, onBackToCast, onSaved})` paints the Voices tab from the `/api/voices` snapshot; `openDraft(container, draft)` shows a draft (for example one created from a Cast card); `stop()` stops the tab's own audition player. `cast` exposes the pure helpers the Cast tab and listen panel use: choice encoding (`""` Default, `library:<id>`, `id:<provider voice>`, `__create__`), `castOptions`, `castWarnings`, `requestVoice` (the listen/example `voice` string) and `identity` (the resolved voice, version, provider voice and revision behind a choice). Rendering never generates audio; previews, Gemini creates, clones and deletes are explicit clicks, and destructive actions use an in-page confirmation. |
| `BardicVoicePreview` — [voice-preview.js](../bardic/static/voice-preview.js) | `configure({onStart,beforeRequest,onReady,onState,onStop})`, `start(book,config,label)`, `stop()`, `waitForStopped()`, `getState()`; explicit one-sample intent with independent job tracking. |
| `BardicDiagnostics` — [diagnostics.js](../bardic/static/diagnostics.js) | `record(event, details)` sends an allowlisted best-effort operational event; callers never await it to decide playback behavior. |

Listening exposes `isSimple(book)`/`enabled(book)`, `resolve(book, segment)`/`take(book, segment)`, `ensure(book, segment)`, `prepare(book, segment, {playbackRate, offset})`, `updatePlayback(book, segment, {playbackRate, currentTime})`, `prepareChapter(book, segment, {playbackRate})`, `getBuffer(book)`, `getSelection(book)`, `forgetAudio(book, segment)`, `stop(book)`, `waitForStopped(book)`, and `allowsAdvance(book, current, next)`.

The resolver reads the selected mode's saved audio. `ensure` serializes one passage request, deduplicates matching pending work and polls its job; it can cause paid generation. The main player uses `prepare` for a warmup of 10 listening seconds capped at three passages, then `updatePlayback` to drive a 45-listening-second rolling buffer capped at 12 future passages. These stay within the current chapter. `prepareChapter` is a separate explicit action for the remaining chapter and does not autoplay. `getBuffer` reports measured contiguous seconds at the current speed, passage count, target and preparation/error state. Merely rendering the panel loads saved metadata and must not start generation.

`getSelection` returns a copy of the effective mode/provider/voice/model. The simple panel delegates transport to `onToggle` (or `onPlay` for initial mode selection), validated speed changes to `onRateChange`, and explicit examples to `onPreview({provider,voice,model,segment_id})` (the option keeps the UI's internal name; the request body it produces says `passage_id`). Speed changes preserve queue intent; a valid existing warmup can update its rate during render without starting rolling lookahead. `playing`/`preparing`/`previewing` come from the shared player. Rendering and dropdown changes never start an audition.

The panel's disclosure (`#listening-drawer`, labelled **More options**) lives inside the **Choose your narrator** sheet and starts closed. Its summary (`#listening-summary`) keeps the selected playback mode, narrator, provider, charging status and chapter-preparation progress visible; `shell.js` mirrors that text into the one-line narrator summary on Read & listen (`#narrator-line-text`, with **Change**). A new preparation error opens the disclosure once; later renders respect an explicit close while the same error remains. Normal playback updates do not open it. The footer's **Narrator & speed**, the reader's **Choose a narrator** and **Change** open the sheet (`openListeningSettings` → `openListenSheet`). With full cast selected, pressing Play with no take also opens it without generating audio; a stale take points to Script & record.

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
4. Update the pipeline runner's adapter dispatch ([pipeline/runner.py](../bardic/pipeline/runner.py)), the steps' allowed providers and the UI provider choices. Keep provider selection explicit; do not silently send text to another provider after failure.
5. Add fake success, malformed response, unavailable model, authentication, rate-limit, retry, cancellation, and redaction tests. Document any new environment variable and cost assumptions. Unknown prices must remain unknown.

A custom model ID can already be selected without adding a new provider. A self-hosted OpenAI-compatible server is its own provider (Local LLM, step pipeline only; see [self-hosted providers](ANALYSIS-PIPELINE.md#self-hosted-providers)), not a custom ID on a cloud provider. Embeddings and external batch execution are not implemented.

### Add an analysis stage or change a recipe

New analysis work is a pipeline step: follow [adding, changing or removing a step](ANALYSIS-PIPELINE.md#adding-changing-or-removing-a-step) and the built-in steps in [pipeline/steps/](../bardic/pipeline/steps/). The registry, runner, caching, versioning, acceptance, API and UI are generic, and the Details explorer gets a card for every registered step.

Include inputs that actually affect the result in the request identity; avoid invalidating unrelated work. Request builders for the model steps live in [pipeline/prompts.py](../bardic/pipeline/prompts.py), and [test_prompt_identity.py](../tests/test_prompt_identity.py) pins them, so a prompt change is deliberate and raises the step's `request_version` where cached results must not be reused. Keep rejected output distinguishable from accepted knowledge. Emit events tied to the precise attempt, so HTTP success does not imply validation success.

Test a cold run, a cache hit, a changed dependency, failure after a durable unit, cancellation, resume, and a manually reviewed item. Use [test_analysis_pipeline.py](../tests/test_analysis_pipeline.py) and [test_pipeline_view_api.py](../tests/test_pipeline_view_api.py) for examples.

These instructions apply to semantic analysis. Local preprocessing and narration have their own execution modules; extend those modules and their resource/artifact hooks instead.

### Add a narration backend or change audio behavior

Keep transcript and performance metadata separate. Update the recipe and `render_fingerprint` whenever audible inputs change. A recipe hash identifies instructions; the content hash identifies a particular WAV asset. Use `produce_take` to publish validated immutable bytes and only then select the take. Do not overwrite an old asset on retake. Test model/voice/direction invalidation, restoring an earlier recipe, invalid audio, interrupted generation, and exact assembly timing.

Simple speech lookup additionally uses a versioned content key that excludes source IDs; the retained target take still needs exact source validation and an explicit reference to the reused input take. Never replace its real producer fingerprint with a hypothetical fingerprint for the new location. Test identical text across passages/books, changed voice/model/text, missing or corrupt WAVs, hash mismatch, legacy lazy indexing and cache playback with no key. API tests should verify duplicate active POSTs join one job while cancelled jobs cannot be revived. Player tests should cover warmup, rate-aware lookahead, chapter limits, Stop/pause/configuration changes, failure after a ready passage, explicit chapter preparation and media-error recovery.

Provider-specific code sits behind `audio.PROVIDERS`, `_RECIPES` and `_generate`, with voices read through `voice_selection()`/`voice_id()` rather than character fields. A new provider must keep the recorded fingerprints, listening session IDs and synthesis keys in [test_narration_providers.py](../tests/test_narration_providers.py) unchanged; if one moves, existing takes and caches silently stop matching. A provider whose server state can change under a stable ID (as Breeze voices can) must pin that state into the local choice so fingerprints never need the network, then verify it live before sending. Its tests there use `FakeBreeze`, an offline implementation of the documented HTTP/SSE contract (speech streaming, voice listing, previews, clone upload, rename and delete), installed through the module seams `breeze._transport` (an `httpx.MockTransport`) and `breeze._sleep` (so retry waits take no time). [test_voice_library.py](../tests/test_voice_library.py) adds `FakeGemini` for the Gemini Voices API through `gemini_voices._transport`; no test calls a real Gemini endpoint, and billed voice creation must never be exercised by the default suite. App tests clear `BREEZE_TTS_URL`/`BREEZE_API_KEY`. A live check against a real server is manual and optional; use original synthetic text.

Cast assignments may reference library voices or rely on the Breeze default. Resolve them with `Runtime.resolved_cast()`/`effective_character()` (once per request, or once at render-job creation) before any `render_fingerprint`, `valid_audio` or `produce_take` call on cast characters; `voice_selection()` deliberately raises on an unresolved `{"library": …}` entry instead of falling back to a default voice. Listen, example and chapter routes resolve their `voice` value through `Runtime.narrator_choice()`. Changes that re-voice followers (current version, default, deletion) must keep the running-narration 409 guard and append to `voice_library_events`.

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
