# Local operations and recovery

Reviewed against the implementation on **September 27, 2026**. This is a guide to the source-based local application. [ROADMAP.md](ROADMAP.md) distinguishes unfinished features; [VALIDATION.md](VALIDATION.md) records dated checks rather than current provider/account guarantees.

Related references: [documentation index](README.md), [architecture](ARCHITECTURE.md), [data model](DATA-MODEL.md), [API reference](API.md), and [development workflow](DEVELOPMENT.md).

## Start and stop

Requirements are Python 3.11+ and [uv](https://docs.astral.sh/uv/). Installed macOS voices use `say`; their audio conversion also requires ffmpeg. The local heuristic analysis path needs neither an API key nor a language-model runtime. Gemini narration needs provider access and may need ffmpeg when returned audio requires conversion.

From the project directory:

```sh
uv sync --frozen --group dev
uv run --frozen python -m spintails
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). There is no frontend compilation step. The launcher binds to loopback; this version has no user account system and is intended for local use. The application checks request origins for writes. A publicly hosted deployment is not configured or promised by this setup.

Use **Stop** on active work when convenient, then Ctrl+C in the server terminal. Shutdown waits for running worker work to finish or reach a cancellation boundary. A request already sent to a provider can still complete and be charged. Do not start another server against the same data directory while the first is shutting down.

An operating-system lock on `server.lock` enforces one application instance per data directory. The file may remain after a clean exit; its mere presence is not an active lock. Do not delete it to bypass a running process. Open the existing app or stop the process using that directory.

An alternate launch can use another port and a separate data directory:

```sh
SPINTAILS_PORT=8766 SPINTAILS_DATA_DIR=/absolute/path/to/test-library uv run --frozen python -m spintails
```

This is useful for testing a restored **copy**. Changing the port alone does not allow two servers to share one library. Keep the primary database on a local filesystem; SQLite WAL relies on same-host coordination. See [SQLite WAL](https://sqlite.org/wal.html).

## Configuration and credentials

The supported launcher loads the `.env` beside `pyproject.toml`, through [config.py](../spintails/config.py), before importing the application. It does not search parent folders. An unrelated working directory does not change which `.env` file is loaded, although a **relative data path is still relative to the working directory**.

For a fresh installation, copy [`.env.example`](../.env.example) only if `.env` does not already exist, then edit it locally. Do not replace an existing file to add one setting.

| Setting | Meaning |
| --- | --- |
| `GEMINI_API_KEY` | Gemini analysis and narration credential. |
| `GOOGLE_API_KEY` | Fallback Gemini alias if `GEMINI_API_KEY` is unset or empty. |
| `OPENAI_API_KEY` | OpenAI text-analysis credential. |
| `ANTHROPIC_API_KEY` | Anthropic text-analysis credential. |
| `SPINTAILS_DATA_DIR` | Library root; defaults to `.spintails` relative to the launch working directory. Prefer an absolute path for alternate libraries. |
| `SPINTAILS_PORT` | Local port for `python -m spintails`; defaults to `8765`. |

Environment values already present in the launching shell win over the same `.env` entry, including intentionally empty values. File values are loaded literally without variable interpolation. For Gemini, the separate `GOOGLE_API_KEY` fallback can still supply a key if `GEMINI_API_KEY` is empty.

Restart the server after changing `.env`. A browser refresh does not reload credentials. If a new file value appears ignored, check whether the terminal or launcher already supplies that variable; remove the unwanted inherited value in that launcher and restart. Diagnose variable names/configuration status without printing credentials.

Keys entered in **Settings** replace the current server session's in-memory value only. They are not written to `.env`, SQLite or browser storage. Clearing a session key does not erase the file/environment value; it returns after restart. Provider/model preferences are saved in SQLite. A queued job captures its configuration so changing Settings does not reroute a request already scheduled.

`.env` and the default `.spintails/` directory are excluded by [`.gitignore`](../.gitignore). Git is for source and documentation, not library backup. If using another data directory inside the checkout, add its precise path to the ignore rules before staging files, or keep that directory outside the checkout. Exports and screenshots can contain private book content even when they contain no API keys.

### Model inventory versus account checks

**Refresh models** makes an explicit read-only provider inventory request and caches its result in memory. It does not generate text, check a balance, or prove the chosen model supports this app's complete structured-output workflow. Custom model IDs can be entered when the provider supports them; unknown capabilities and prices remain unknown.

**Check API** sends one tiny text-generation request with the selected detailed analysis model. **Check all accounts** performs that check for the configured providers. These actions may incur a small charge, send no book text, have no automatic retry, and cache the same configuration's result for 30 seconds. Results disappear on server restart and are invalidated by changed credentials/models.

A successful text check establishes that one request worked at that time. It does not establish remaining credit, full-book capacity or access to a separate TTS model. A quota/rate-limit error is not automatically exhausted credit. Exact billing and account-wide usage belong in the provider dashboards linked from Settings. See [account check semantics](ACCOUNT-CHECKS.md).

## Choose the workflow before generating

| Need | Workflow | What can call a provider |
| --- | --- | --- |
| Listen immediately | Read & listen → simple listening, choose one narrator, start playback. | Gemini requests one uncached passage at a time. Device voices stay local. |
| Build a character performance | Studio → free census → cheap discovery → profiles → chapter direction → review voices/notes → render a short scene. | Selected cloud analysis stages and Gemini narration. |
| Process supplied series volumes | Manage books & series → membership/order/placeholders → confirm character links → preview a series run. | Explicit series analysis; at most two discovery books concurrently, later phases in reading order. |
| Inspect existing work | Pipeline explorer, artifact inspection, source search, resource dashboard, analysis export. | None of these inspection actions generates model output. Local indices/artifact projections may be prepared as needed. |

The census is free local Python processing. It does not count as semantic model discovery. Whole-book discovery and profile currency are separate: a profile can be stale after new evidence or a changed identity link even when scanning is complete. References distinguish a named mention, attributed dialogue and profile evidence; none alone proves physical presence in a scene.

For a later volume, set an explicit reading order and supply any earlier books you want considered. Only confirmed character links and source-valid observations from strictly earlier, available books are used. Missing/planned placeholders contain no source evidence. Removing/changing series membership clears that book's current identity links while retaining historical observations. Review links again before relying on cross-book profiles.

Simple listening has a separate audio store and does not require or change enhanced analysis. Pause, Stop, switching books or changing narrator invalidate pending playback. An in-flight take can finish and remain cached. Automatic continuation stops at the current chapter boundary. There is no whole-book background preparation queue or simple-mode ZIP yet.

## Budget and resource semantics

The progressive analysis request limits are implemented in [processing.py](../spintails/processing.py). Defaults are:

| Allowance | Default | Scope |
| --- | ---: | --- |
| Requests | 25 | This analysis run; every HTTP attempt, including a transport retry or evidence repair. |
| Input tokens | 1,000,000 | This run, using reported usage when available and a conservative reservation otherwise. |
| Output tokens | 100,000 | This run; requests also have explicit output caps. |
| Estimated spend | US$1 | Cumulative tracked **analysis** attempts for this book, including earlier runs. |

A series child run shares its request/token limits across phases. The series preview shows the combined per-book allowances; it is not an independently enforced account-wide ceiling. One failure or exhausted allowance stops new scheduling, while a request already active in another worker may still complete.

Before sending a guarded analysis attempt, the worker reserves UTF-8 request bytes plus protocol allowance as a conservative input bound, and the requested output cap. Its estimated cost includes input-rate uplift to avoid assuming cache discounts. Reported usage can replace token reservations; missing usage keeps its conservative cost reservation. This is deliberately different from an exact provider invoice.

Preview estimates describe the known work at preview time. They exclude retries and can change as discovery reveals characters or scene work. A full-mode preview is not a guaranteed final price. Resume retains prior book spending; changing a model, running another phase or restarting does not erase it.

An unknown/custom model price prevents a dollar-guarded request. Earlier attempts with unknown cost also prevent claiming a reliable cumulative dollar allowance. Choose a documented priced model when possible, or explicitly select request/token-only limits after reviewing the uncertainty. Do not clear ledger rows to make an allowance appear unused.

**The analysis allowances do not currently cap narration, simple listening, account checks, or unrelated account spending.** Narration use is measured where possible, but no narration dollar ceiling is enforced. Use explicit short auditions and provider-side account controls when deciding how much narration to run. Extending guards is a [roadmap item](ROADMAP.md).

The resource dashboard combines analysis attempts with separate local/narration operations without charging one request twice:

- Input/output/cache counts are provider-reported when present. Cached input is included in total input, not added again. Missing counts stay unknown.
- Analysis cost is the spending-guard estimate, including reservations/uplift; Gemini TTS uses dated standard paid-tier rates only when the necessary text/audio modality and cache counts are reported. Discounts, free-tier adjustments, cache storage and taxes are not inferred.
- Elapsed totals sum measured steps. Concurrent steps overlap, so this is not end-to-end job latency. Retry backoff and uninstrumented historical work are not reconstructed.
- CPU is current Python-thread CPU only. It excludes macOS `say`, ffmpeg, other processes/threads, GPUs and remote model hardware.
- Cached output incurs no new provider request; its original usage is not recharged on every replay. Audio seconds/bytes can describe reused output more than once and are not physical disk occupancy.
- Unknown historical measurements, interrupted requests and unusable HTTP-success responses are visible as such. This ledger is neither a remaining-credit display nor an invoice.

Current price tables are dated research snapshots, not live pricing feeds. Recheck provider terms before updating assumptions. [Google pricing](https://ai.google.dev/gemini-api/docs/pricing) and [Interactions usage fields](https://ai.google.dev/api/interactions-api) support the TTS accounting; model price provenance is retained by the analysis catalog.

## Jobs, cancellation and recovery

The normal worker runs one book job at a time. Series discovery has its explicitly bounded two-book path. Busy-book and series reservations prevent conflicting edits/processing during a run. Long provider calls occur outside database write transactions.

Accepted analysis units and completed audio assets are persisted before later work proceeds. Chapter/profile publication is transactional. If later work fails, prior accepted results remain reusable. Cancellation stops between safe boundaries; it cannot reliably recall an already submitted request or refund it.

On startup, unfinished jobs/checkpoints become interrupted. No cloud work automatically resumes. Inspect the run/error and current provider/limits, preview the remaining work, then start the desired stage with resume enabled. It revalidates compatible cached units and charges only new attempted provider work. Invalid cache entries are retired from the derived cache; immutable accepted/rejected history remains inspectable.

Transport failures are deliberately bounded. Progressive analysis can perform one short retry of a recognized transient response; authentication/billing failures are not automatically retried. An uncertain connection/timeout stops rather than blindly repeating a potentially charged request. Invalid structured evidence gets at most one corrective response for that request. These are separate mechanisms and both consume allowance if another HTTP attempt is sent.

Changing source, effective model/request recipe, profile evidence or reviewed performance inputs may make downstream results stale. That is different from deleting their historical artifacts. Review a new preview before assuming everything will replay at zero cost. A force-render always permits a new provider take; retained WAVs are immutable and selected metadata chooses the current take.

## Data layout

Paths below are relative to the configured data directory:

| Path / store | Contents |
| --- | --- |
| `library.sqlite3` | Books/projections, jobs, settings preferences, checkpoints, references, series identities, covers, immutable artifacts/dependencies, listening metadata and resource records. |
| `library.sqlite3-wal`, `library.sqlite3-shm` when present | SQLite WAL state/coordination files. Do not discard these around a running database. |
| `originals/<book-id>/source.epub` or `source.txt` | Retained upload used for metadata/structure refresh and recovery. |
| `audio/<book-id>/` | Enhanced audio assets, including retained alternatives and readable legacy recipe-named files. |
| `listen-audio/<book-id>/` | Independent simple-listening assets. |
| `backups/` when present | Previously created backups; not an automatic scheduled full-library backup service. |
| `server.lock` | Operating-system instance lock file. |

Browser reading position and some UI state live in that browser's local storage, not the library database. Browser-origin changes, clearing site data, changing ports or using another browser may lose that local position without losing generated audio or analysis.

**Remove** in the library is an archive operation. Restore it from Removed items. Files, analysis history and storage use remain. Removing a series leaves its books independently available. There is no permanent purge/garbage-collection command in the current application.

## Full backup and safe restore

The simplest full backup is a copy of the **entire data directory after the server has stopped**. Preserve originals and both audio trees along with SQLite; a database-only copy is not a complete audiobook backup. Keep credentials separately from shareable data/source backups.

For the documented source launcher, this example uses the same configuration loader and creates a new timestamped directory outside the checkout. Run it from the project directory **only after server shutdown has completed**:

```sh
uv run --frozen python - <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import os
import shutil
from spintails.config import load_project_env

load_project_env()
source = Path(os.environ.get("SPINTAILS_DATA_DIR", ".spintails")).resolve()
if not (source / "library.sqlite3").is_file():
    raise SystemExit("No library.sqlite3 at the configured data directory")
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
destination = Path.home() / "Backups" / "SpinTails" / stamp
if destination.resolve().is_relative_to(source):
    raise SystemExit("Choose a backup destination outside the library directory")
destination.parent.mkdir(parents=True, exist_ok=True)
shutil.copytree(source, destination)  # Refuses to overwrite an existing backup.
print(destination)
PY
```

This copies private ebook/analysis/audio data to a local backup destination; it does not publish or upload it. If the library path was supplied only in the server launch command, supply that same `SPINTAILS_DATA_DIR` for this command. Do not accidentally back up an unused default folder.

For a running database, use [SQLite's backup API](https://sqlite.org/backup.html) for a consistent database snapshot instead of copying only `library.sqlite3`. That API does **not** snapshot the separate original/audio files. A consistent full live backup needs coordinated media retention and a manifest; no one-click implementation is present. A stopped whole-directory copy remains the recommended operational path.

Restore procedure:

1. Stop the application and preserve the current directory as a separate recovery copy. Do not restore over an open library or delete the only existing copy.
2. Copy the chosen complete backup into a fresh directory. Keep the backup itself unchanged.
3. Check the copied database for integrity and foreign-key issues before opening the app. For example, adapt the absolute path below:

   ```sh
   uv run --frozen python - <<'PY'
   from pathlib import Path
   import sqlite3
   database = Path("/absolute/path/to/restored-copy/library.sqlite3")
   with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
       print(connection.execute("PRAGMA integrity_check").fetchone()[0])
       print("Foreign-key issues:", len(connection.execute("PRAGMA foreign_key_check").fetchall()))
   PY
   ```

4. Launch against the restored copy on an alternate port. Startup can create/update schema and mark interrupted jobs, so test a copy rather than the sole backup. Open representative books, source references, covers, enhanced takes and simple-listening takes; verify file availability and selected-audio playback. Do not start paid analysis/narration just to inspect a restore.
5. Once satisfied, stop the test server and point the normal launch configuration at the restored directory. Keep the previous library until the restore is verified for your use.

An integrity result of `ok` and no foreign-key violations do not prove every WAV exists or every reference has correct source meaning; perform the representative application checks too. Do not assume an older application revision can safely open a newer database. Keep the application source revision/lockfile with backup notes. Lost pre-retention artifacts or media absent from every backup cannot be reconstructed by a schema migration.

### Exports are not a complete backup

**Export analysis files** includes canonical source, profiles, observations, story map, retained artifact versions and transitive earlier-book dependencies, attempts/events, resource operations and listening metadata. It excludes API keys and audio binaries. There is no bundle import/restore UI yet.

The **audiobook ZIP** contains current enhanced takes, production metadata, text, a timeline and assembled WAVs for complete chapters. An incomplete chapter still contributes its available individual takes and missing-passage information. Simple-listening audio is separate and is not included by this export. Neither export is a replacement for a full data-directory backup.

## Troubleshooting

| Symptom | What to inspect and do |
| --- | --- |
| Server says the data directory is already in use | Open the existing instance or stop its process. Confirm the resolved data path; changing only the port does not resolve the directory lock. Do not unlink the lock file to force concurrent access. |
| Address/port already in use | Stop the service using that port or choose `SPINTAILS_PORT`. A new port also means a different browser storage origin. |
| Library appears empty after restart | Check launch working directory and `SPINTAILS_DATA_DIR`; a relative path may have selected another folder. Preserve both folders while locating the intended `library.sqlite3`. |
| Updated `.env` key appears ignored | Restart the server; inspect inherited variable names and the Gemini alias precedence. Settings changes are session-only. Use an explicit small check only when you want to test inference. |
| Model refresh succeeds but generation fails | Visibility does not prove structured-output compatibility, permission for a separate TTS model, quota or balance. Read the actual failure and verify the selected model role. |
| “Invalid character evidence” / quoted text not in source | Inspect the rejected output and request recipe in Pipeline explorer. Evidence must be a contiguous passage in that request's supplied source, not a paraphrase or a quotation from a different chapter. One repair is automatic; a repeated failure stops safely. Resume the relevant stage/chapter after reviewing model/output/context; do not weaken source validation or repeatedly force the entire book. |
| Profile is provisional or stale | Check whole-book semantic coverage, current observations, confirmed series links and the profile's effective input/model. A completed scan is not a completed refinement. Run only the required stage and review its preview. |
| Chapter title is wrong | Use source-preserving structure repair when an original upload exists. The operation rejects source text/unit mismatches; do not manually rewrite canonical prose to make an old checkpoint fit. Metadata refresh and structure repair are different actions. |
| Book title/author/cover is wrong | Edit metadata or refresh from the saved original. Manual title/author edits are preserved. Some EPUBs have no usable cover; no online lookup is performed. |
| Job stopped at an allowance | Inspect saved work and tracked attempts. Raise the intended allowance or reduce the scope, then resume. A new run refreshes run-scoped request/token allowances but not cumulative book analysis spend. Unknown earlier cost requires an explicit decision about using token/request-only limits. |
| 401/403, billing failure, 429 or timeout | Follow the specific category. Authentication/billing errors are not repaired by repeated requests. Rate limits and billing limits differ. A timeout may have incurred usage; the app does not blindly retry it. Check the provider dashboard before resuming uncertain work. |
| Stop does not immediately silence a provider request | Cancellation prevents future scheduling; it cannot recall a submitted generation. An in-flight take may be saved, while pending playback is invalidated. |
| A series run stops before later books | Inspect the failed/budget-limited child run. Other already active discovery requests can finish; the coordinator stops starting new work. Make a fresh preview and resume after addressing the issue. |
| Audio is missing after editing voices/directions | The selected recipe changed. Earlier files remain retained, but old audio is not automatically treated as matching new instructions. Generate the affected take or restore its earlier recipe to reuse a compatible archived take. |
| Device narration is unavailable or silent | Confirm macOS `say`, an installed selected voice, ffmpeg and ordinary access to macOS speech services. A restricted tool sandbox can prevent audible output even when `say` exits successfully; validation rejects empty/silent results. |
| Gemini audio is rejected | Inspect safe status/model/voice information. The adapter rejects malformed, truncated, empty or silent audio; valid WAV structure still does not prove spoken-text fidelity. Retry only the selected passage when appropriate. |
| New simple-listening passages have gaps | Generation is on demand, one passage at a time; this is not a streaming or prefetching engine. Cached passages replay locally. Background preparation is planned. |
| Highlighting does not follow individual words | Current timing is passage-level. There is no word-alignment result to enable yet. |
| Search finds too few results | Queries are literal lexical terms, with all terms required in a passage. Try fewer terms; confirm reading-order scope and available books. Search does not resolve pronouns or infer character identity. Missing SQLite FTS5 is reported as unavailable. |
| Removed books still use disk | Removal is reversible archiving. Restore under Removed items; permanent purge is not implemented. Do not manually delete shared history/assets to simulate a supported purge. |
| Dashboard numbers disagree with billing | Check scope, historical unknowns, reservations, reported token completeness and dated rates. The dashboard tracks this app's recorded work; invoices include account-wide activity and provider adjustments. |

## Development checks and the meaning of “verified”

Routine offline suite:

```sh
uv run --frozen pytest -q
```

Node is needed for the JavaScript behavior harnesses invoked by the Python tests. Tests without Node can skip those checks; read the test summary. The default suite uses fake provider transports and temporary libraries, not paid API calls or the user's real library.

The real macOS speech smoke test is opt-in:

```sh
SPINTAILS_TEST_SYSTEM_AUDIO=1 uv run --frozen pytest -q tests/test_audio.py
```

It uses local installed voices and should run in a normal terminal with speech-service access. Live cloud checks are separate user-selected actions. Passing mocks does not prove the current endpoint, model availability, project quota, voice quality or a full-book production.

[VALIDATION.md](VALIDATION.md) contains earlier successful and failed account checks, a short Gemini narration audition, a real-chapter analysis, browser checks and offline regression results. Those are historical observations tied to their recorded configuration and time. They are not a live health dashboard; do not infer that a credential still works or still fails today. Larger cloud workloads, subjective performance quality, target-device support and complete-book consistency require their own explicit validation.
