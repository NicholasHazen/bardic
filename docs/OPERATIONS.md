# Local operations and recovery

Reviewed against the implementation on **September 27, 2026**. This is a guide to the source-based local application. [ROADMAP.md](ROADMAP.md) distinguishes unfinished features; [VALIDATION.md](VALIDATION.md) records dated checks rather than current provider/account guarantees.

Related references: [documentation index](README.md), [architecture](ARCHITECTURE.md), [data model](DATA-MODEL.md), [API reference](API.md), and [development workflow](DEVELOPMENT.md).

## Start and stop

Requirements are Python 3.11+ and [uv](https://docs.astral.sh/uv/). Installed macOS voices use `say`; their audio conversion also requires ffmpeg. The local heuristic analysis path needs neither an API key nor a language-model runtime. Gemini narration needs provider access and may need ffmpeg when returned audio requires conversion.

From the project directory:

```sh
uv sync --frozen --group dev
uv run --frozen python -m bardic
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). There is no frontend compilation step. The launcher binds to loopback unless [local-network access](#local-network-access) is enabled; this version has no user account system and is intended for a single owner. The application checks request origins for writes. A publicly hosted deployment is not configured or promised by this setup.

### Run as a service

On macOS, `./bardicctl` runs Bardic as a LaunchAgent. It then starts at login, restarts after a crash and belongs to launchd rather than to whichever terminal or agent session started it:

```sh
./bardicctl install    # once, from any checkout of this repository
./bardicctl status     # what serves the port, who owns it, active jobs
./bardicctl restart    # after pulling code or editing .env
./bardicctl stop       # until `start` or the next login
./bardicctl start
./bardicctl logs -f    # ~/Library/Logs/bardic.log
./bardicctl uninstall  # stop, and no longer start at login
```

- `install` writes `~/Library/LaunchAgents/local.bardic.plist` for the repository's **main checkout**, never a linked worktree. `install`, `start` and `restart` run `uv sync --frozen` there first; a failed sync stops nothing. launchd then runs the checkout's `.venv/bin/python -m bardic` directly, so its signals reach the server itself rather than a `uv` wrapper. That launch reads the checkout's `.env` as usual. launchd passes no shell variables, so keep the port, library and network settings in `.env`. The job's PATH includes Homebrew, so ffmpeg is found; `say` and `dns-sd` are in `/usr/bin`.
- The service runs whatever the main checkout contains. Work in another worktree is not served until it is merged there and the service restarts.
- `stop`, `restart`, `install` (when reloading) and `uninstall` refuse while jobs are queued or running and name them, or when the server cannot report its jobs; `--force` proceeds. Shutdown waits for a safe boundary, which lets a request already sent finish. After 5 minutes launchd sends SIGKILL, which cuts off a long Gemini chapter chunk that can take up to 15 minutes. A request already sent can still be charged, and unfinished jobs are marked interrupted. The commands return only after the server process has exited.
- Run from a linked worktree, those four commands also require `--yes`, so an agent cannot interrupt the owner's server by confusing `stop` with `dev stop`.
- A server started by hand (for example with `nohup`) is not managed. `status` lists its parent processes; when they lead straight to launchd, whatever started it has exited and no session owns it. `install`, `start` and `restart` refuse while it holds the port. `./bardicctl stop` sends it SIGTERM, but only when its command line is a Bardic launch; then run `./bardicctl install`.
- After a crash or failed start, launchd tries again every 30 seconds. A port held by another program therefore adds a log line every 30 seconds until it is freed or the service is stopped. The log records every request, about 2 MB a day with the app open. When it passes 10 MB, the next `install`, `start` or `restart` moves it to `bardic.log.1`; it is not rotated while the server runs.
- The service commands need macOS. The [development servers](DEVELOPMENT.md#configuration-and-data-isolation) work on any POSIX system.

For a server started in a terminal, use **Stop** on active work when convenient, then Ctrl+C in the server terminal. Shutdown waits for running worker work to finish or reach a cancellation boundary. A request already sent to a provider can still complete and be charged. Do not start another server against the same data directory while the first is shutting down.

An operating-system lock on `server.lock` enforces one application instance per data directory. The file may remain after a clean exit; its mere presence is not an active lock. Do not delete it to bypass a running process. Open the existing app or stop the process using that directory.

An alternate launch can use another port and a separate data directory:

```sh
BARDIC_PORT=8766 BARDIC_DATA_DIR=/absolute/path/to/test-library BARDIC_LAN_NAME='' uv run --frozen python -m bardic
```

The empty `BARDIC_LAN_NAME` keeps a test copy on loopback when `.env` enables local-network access. `./bardicctl dev start --library /absolute/path/to/test-library` does the same in the background with provider keys blank (`--keys` passes the service checkout's provider keys, and nothing else from its `.env`, for a live test); it refuses the service's own library.

This is useful for testing a restored **copy**. Changing the port alone does not allow two servers to share one library. Keep the primary database on a local filesystem; SQLite WAL relies on same-host coordination. See [SQLite WAL](https://sqlite.org/wal.html).

## Upgrading from Spin Tails

The project is now **Bardic**, with source in `bardic/` and the preferred launcher `uv run --frozen python -m bardic`. Update the checkout and run `uv sync --frozen --group dev`, then restart the app with the new command when active work has stopped. An existing checkout named `spin-tails` can keep that directory name. The rename does not require replacing `.env`, moving a library or migrating its database/media formats.

- `python -m spintails` remains a compatibility launcher. New code and scripts should import `bardic`; the compatibility launcher is not a second implementation of the application.
- `BARDIC_PORT` and `BARDIC_DATA_DIR` are the preferred settings. `SPINTAILS_PORT` and `SPINTAILS_DATA_DIR` remain accepted aliases. Existing provider-key names are unchanged.
- Shell values take precedence over `.env` even across old/new aliases. Within the same source, the `BARDIC_` setting takes precedence over its `SPINTAILS_` alias. For example, a shell `SPINTAILS_DATA_DIR` overrides a file `BARDIC_DATA_DIR`, while two file settings select `BARDIC_DATA_DIR`.
- An explicit data-directory setting selects that path. Otherwise, use `.bardic/` relative to the working directory; if it is absent and `.spintails/` is an existing directory, reuse `.spintails/` in place. If both exist, `.bardic/` is selected. No directory is automatically copied, merged, renamed or removed. Set `BARDIC_DATA_DIR` explicitly when both libraries exist and you want the older one.
- Browser preferences use `bardic:` keys for new writes, with fallback reads from existing `spintails:` keys when no new value exists. Keep the same browser origin to retain reading position and listening preferences; changing ports creates a different origin.
- Portable analysis exports retain the version-1 `spintails-analysis` format identifier. Existing schema, source coordinates, artifact identities and audio files retain their contracts.

Use the regular full-library backup procedure before any separate data move or repair. A renamed display/package alone is not a reason to alter retained source or paid outputs.

## Configuration and credentials

The supported launcher loads the `.env` beside `pyproject.toml`, through [config.py](../bardic/config.py), before importing the application. It does not search parent folders. An unrelated working directory does not change which `.env` file is loaded, although a **relative data path is still relative to the working directory**.

For a fresh installation, copy [`.env.example`](../.env.example) only if `.env` does not already exist, then edit it locally. Do not replace an existing file to add one setting.

| Setting | Meaning |
| --- | --- |
| `GEMINI_API_KEY` | Gemini analysis and narration credential. |
| `GOOGLE_API_KEY` | Fallback Gemini alias if `GEMINI_API_KEY` is unset or empty. |
| `OPENAI_API_KEY` | OpenAI text-analysis credential. |
| `ANTHROPIC_API_KEY` | Anthropic text-analysis credential. |
| `BREEZE_TTS_URL` | Breeze narration server root, for example `http://host.local:7860`. Used when no URL has been saved in Settings. |
| `BARDIC_LOCAL_LLM_URL`, `BARDIC_BOOKNLP_URL`, `BARDIC_NOVEL_ANALYZER_URL` | Self-hosted analysis server roots, for example `http://host.local:8100`. Used when no URL has been saved in Settings → Your analysis servers; a URL cleared there stays cleared (it does not fall back). The environment value is never saved. |
| `BREEZE_API_KEY` | Optional bearer key, only if the Breeze server requires one. |
| `BARDIC_DATA_DIR` | Library root; defaults to `.bardic` relative to the launch working directory, with existing `.spintails` fallback described above. Prefer an absolute path for alternate libraries. |
| `BARDIC_PORT` | Port for `python -m bardic`; defaults to `8765`. |
| `BARDIC_LAN_NAME` | Opt-in name for other devices, such as `bardic` for `bardic.local`. See [local-network access](#local-network-access). |
| `BARDIC_HOST` | Bind address. Defaults to `127.0.0.1`, or `0.0.0.0` when `BARDIC_LAN_NAME` is set. |
| `BARDIC_ALLOWED_HOSTS` | Extra comma-separated Host names or addresses to trust, such as this computer's IP. No ports or wildcards. |

Environment values already present in the launching shell win over the same `.env` entry, including intentionally empty values. File values are loaded literally without variable interpolation. For Gemini, the separate `GOOGLE_API_KEY` fallback can still supply a key if `GEMINI_API_KEY` is empty.

Restart the server after changing `.env`. A browser refresh does not reload credentials. If a new file value appears ignored, check whether the terminal or launcher already supplies that variable; remove the unwanted inherited value in that launcher and restart. Diagnose variable names/configuration status without printing credentials.

Keys entered in **Settings** replace the current server session's in-memory value only. They are not written to `.env`, SQLite or browser storage. Clearing a session key does not erase the file/environment value; it returns after restart. Provider/model preferences are saved in SQLite. A queued job captures its configuration so changing Settings does not reroute a request already scheduled.

The Breeze server URL entered in **Settings → Breeze** is saved in SQLite and takes precedence over `BREEZE_TTS_URL`; clearing it falls back to the environment value after restart. The optional Breeze key follows the key rules above (memory only). **Check connection** reads the server's health, voice list and each cloned voice's reference clip; it never generates audio. The result, including pinned voice revisions, is saved so existing Breeze audio stays playable while the server is off. It also imports each usable server voice into the voice library and, the first time, makes the server's default voice the Bardic default. Only `cloned` voices can narrate. Breeze narration and voice design send passage text, descriptions and performance notes to that server over the local network, in plain HTTP unless the URL uses `https`.

### Voices and casting

The **Voices** tab holds voices shared by every book; the **Cast** tab assigns them. In Cast, choose the provider at the top, then each character's voice: **Default** (for Breeze, the Bardic default voice; for Gemini, Kore), one of your library voices, a Gemini built-in/project voice or device voice, or **Create new voice…**. Create new voice opens a Voices draft filled with the character's name, profile and delivery notes and one of their lines; **Save & assign** returns to Cast with the voice set. Narrator and Unassigned dialogue are listed first.

- **Breeze, describe:** write a description and 5–15 seconds of sample text, **Generate previews** (1–3, free, roughly the audio length × count on the server), listen, adjust and generate again, then save one under a name. Bardic keeps a copy of every preview; the saved voice is uploaded from that copy, so it does not depend on the server's 24-hour preview expiry.
- **Breeze, clone from a recording:** upload 5–15 seconds of clean speech (at most 20 MB) with its exact transcript. You must confirm that you have the speaker's consent.
- **Gemini, describe:** each **Create** is a billed request that stores one voice in your Google project (at most 200 stored voices per project, each kept for one year). Tick the confirmation for every create; the Voices tab shows how many of the 200 are used after **Refresh Gemini voices**. Discarding a candidate, abandoning the draft or saving another candidate deletes those stored voices. A Gemini voice made with one API key cannot be used or deleted with another. Gemini voice creation has not been exercised against a live account by this project.
- **Iterate** on a voice to save a new version of it. Characters follow a voice's current version, so saving a version, **Make current** on an older one, or **Set as default** re-voices the characters that follow it; their existing takes become out of date but are kept, and switching back then rendering reuses the old audio without a request. These actions are refused while narration is being prepared for an affected book.
- **Delete** a voice Bardic made deletes it on the provider too. An imported Breeze server voice is removed from Bardic only unless **Also delete on the server** is ticked; a removed imported voice is not imported again by later checks. The Breeze default cannot be deleted until another default is chosen. Characters still assigned to a deleted voice show a warning and refuse to render until reassigned.

**Pronunciations** (Cast tab, below the cards) fix how the narrator says a name or invented word:

- Type the word and how it should sound, then **Hear it**. The narrator for the provider selected at the top of Cast reads the word's first sentence in the book with your spelling. Nothing is saved, and a Gemini example can be charged like any voice example.
- Write the sound as an ordinary-looking word: `Kaylor`, `Aylee`, `Zosahree`. Use hyphens only when a syllable goes missing (`ny-oh-var`), and check that no piece sounds like another word (`ay` is read as "aye"). Avoid capital letters, which narrators may read as initials or shout.
- **Match capitals** (on by default) keeps a name like `Will` from changing the verb `will`. Turn it off for a word that also appears in capitals, such as shouted dialogue.
- **Different spelling for one narrator** helps when one provider still gets the word wrong. Typing the original word there lets that narrator read it unchanged.
- Add the pronunciations before narrating a whole book. Saving one retires recorded Studio takes that contain the word, and they are narrated again (with provider charges or quota for Gemini) the next time you narrate. Changing the entry back reuses the archived audio. A saved cast performance keeps the pronunciations it started with; create a new one to use later changes.

`.env`, `.bardic/` and legacy `.spintails/` directories are excluded by [`.gitignore`](../.gitignore). Git is for source and documentation, not library backup. If using another data directory inside the checkout, add its precise path to the ignore rules before staging files, or keep that directory outside the checkout. Exports and screenshots can contain private book content even when they contain no API keys.

### Model inventory versus account checks

**Refresh models** makes an explicit read-only provider inventory request and caches its result in memory. It does not generate text, check a balance, or prove the chosen model supports this app's complete structured-output workflow. Custom model IDs can be entered when the provider supports them; unknown capabilities and prices remain unknown.

**Check API** sends one tiny text-generation request with the selected detailed analysis model. **Check all accounts** performs that check for the configured providers. These actions may incur a small charge, send no book text, have no automatic retry, and cache the same configuration's result for 30 seconds. Results disappear on server restart and are invalidated by changed credentials/models.

A successful text check establishes that one request worked at that time. It does not establish remaining credit, full-book capacity or access to a separate TTS model. A quota/rate-limit error is not automatically exhausted credit. Exact billing and account-wide usage belong in the provider dashboards linked from Settings. See [account check semantics](ACCOUNT-CHECKS.md).

## Local-network access

The launcher binds to loopback unless you opt in. To use Bardic from a phone, tablet or another computer on the same network, add this to `.env` and restart:

```sh
BARDIC_LAN_NAME=bardic
```

Then open `http://bardic.local:8765` on the other device, using your `BARDIC_PORT`. The setting:

- binds every IPv4 interface (`0.0.0.0`) unless `BARDIC_HOST` names one address;
- trusts `bardic.local` as a Host name. DNS-rebinding and same-origin write protection still apply;
- runs macOS `dns-sd -P` from [lan.py](../bardic/lan.py), so this computer's mDNSResponder answers `bardic.local` with its current IPv4 address. The address is rechecked every 30 seconds. The name is withdrawn when the server stops, including after a forced kill.

**There is no authentication.** Any device that can reach the port can read the library, change settings and session provider keys, edit or remove work, and start paid analysis or narration with the configured keys. Enable this only on a network you trust. On a laptop the setting also applies to every other network it joins, including public Wi-Fi. Unset it, or turn on the macOS firewall, before travelling.

- `http://bardic.local:PORT` and `http://127.0.0.1:PORT` are different browser origins. Reading positions and listening preferences saved in one browser origin do not appear in the other. The library itself is shared.
- Apple devices and Windows 10+ resolve `.local` names. Android support varies by version and browser, and Linux needs Avahi with nss-mdns. Where the name does not resolve, add this computer's address to `BARDIC_ALLOWED_HOSTS` and open that address instead. The address can change unless the router reserves it.
- Only one device can own a name. If another device already uses `bardic.local`, the server prints `Name in use`, keeps serving and retries with backoff. Choose another name. Two Bardic instances on one network need different names.
- The advertised address follows the default route. A full-tunnel VPN can make that the VPN address. In that case set `BARDIC_HOST` to the LAN address, which also stops loopback access; use the name locally too.
- The computer must be awake. The name is unavailable while it sleeps.
- `dns-sd` ships with macOS. On other systems the server still binds, but no name is advertised.

## Choose the workflow before generating

| Need | Workflow | What can call a provider |
| --- | --- | --- |
| Listen immediately | Read & listen → simple listening, choose one narrator, start playback. Optionally prepare the rest of the chapter first. | Gemini requests uncached passages serially for warmup/lookahead or the selected chapter preparation. Breeze sends uncached passages to the configured server the same way. Device voices stay local. |
| Build a character performance | Studio → free census → cheap discovery → profiles → chapter direction → review voices/notes → render a short scene. | Selected cloud analysis stages and Gemini narration. |
| Process supplied series volumes | Manage books & series → membership/order/placeholders → confirm character links → preview a series run. | Explicit series analysis; at most two discovery books concurrently, later phases in reading order. |
| Inspect existing work | Pipeline explorer, artifact inspection, source search, resource dashboard, analysis export. | None of these inspection actions generates model output. Local indices/artifact projections may be prepared as needed. |

The census is free local Python processing. It does not count as semantic model discovery. Whole-book discovery and profile currency are separate: a profile can be stale after new evidence or a changed identity link even when scanning is complete. References distinguish a named mention, attributed dialogue and profile evidence; none alone proves physical presence in a scene.

For a later volume, set an explicit reading order and supply any earlier books you want considered. Only confirmed character links and source-valid observations from strictly earlier, available books are used. Missing/planned placeholders contain no source evidence. Removing/changing series membership clears that book's current identity links while retaining historical observations. Review links again before relying on cross-book profiles.

Simple listening has a separate audio store and does not require or change enhanced analysis. Its buffer meter reports saved listening seconds at the selected speed. Pause, Stop, switching books or changing narrator invalidate pending playback/preparation. An in-flight take can finish and remain cached. Automatic continuation stops at the current chapter boundary. There is no whole-book background preparation queue or simple-mode ZIP yet.

## Budget and resource semantics

The progressive analysis request limits are implemented in [processing.py](../bardic/processing.py). Defaults are:

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

**The analysis allowances do not currently cap narration, simple listening, voice examples, account checks, or unrelated account spending.** Narration use is measured where possible, but no narration dollar ceiling is enforced. Use explicit short auditions and provider-side account controls when deciding how much narration to run. Extending guards is a [roadmap item](ROADMAP.md).

Simple Play can prepare audio ahead of the currently heard passage: a warmup aims for 10 listening seconds with at most three passages, then a rolling buffer aims for 45 listening seconds within the chapter and at most 12 future passages. **Prepare rest of chapter** explicitly saves every remaining passage from the selected position without autoplay. The UI shows the remaining passage count and cloud-charge warning, not a dollar estimate. Stop/pause prevents further scheduling; already submitted work may finish and be billed. Replaying matching saved audio makes no new provider request.

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

Simple listening similarly does not automatically resend a failed or uncertain generation POST. Its browser polls jobs with bounded read-only retries and joins an already active identical session/passage request when the API receives it again. Preparation failures preserve saved clips and expose **Retry preparation**. After a restart/reload, the saved single-passage jobs/takes remain, but the browser's chapter/lookahead queue requires another explicit action. Do not assume that pressing Play after a failure is always free: it checks the saved cache and can request new narration if no valid match is available.

Voice examples use the same one-unit job/cancellation rules. **Hear example** explicitly requests at most 400 source characters (or fixed demo text); a matching saved preview is reused. It snapshots unsaved voice/direction choices without saving them, pauses the book, and keeps the reader bookmark. Close the example to return to that paused place. Failed or uncertain generation is not automatically resubmitted. An example waits for a previously cancelled listening request to settle before submitting its own request. A cancelled in-flight sample may finish and remain cached; its late response must not resume playback. Preview usage is shown separately as stage `voice_preview`, with no enforced dollar allowance.

Changing source, effective model/request recipe, profile evidence or reviewed performance inputs may make downstream results stale. That is different from deleting their historical artifacts. Review a new preview before assuming everything will replay at zero cost. A force-render always permits a new provider take; retained WAVs are immutable and selected metadata chooses the current take.

## Data layout

Paths below are relative to the configured data directory:

| Path / store | Contents |
| --- | --- |
| `library.sqlite3` | Books/projections, jobs, settings preferences, checkpoints, references, series identities, covers, immutable artifacts/dependencies, listening and voice-example metadata and resource records. |
| `library.sqlite3-wal`, `library.sqlite3-shm` when present | SQLite WAL state/coordination files. Do not discard these around a running database. |
| `originals/<book-id>/source.epub` or `source.txt` | Retained upload used for metadata/structure refresh and recovery. |
| `audio/<book-id>/` | Enhanced audio assets, including retained alternatives and readable legacy recipe-named files. |
| `listen-audio/<book-id>/` | Independent simple-listening assets. |
| `voice-previews/<book-id>/` | Independent retained voice-example WAVs, matched to immutable preview request/take metadata in SQLite. |
| `voice-library/` | Library-wide audition clips and design candidates for the voice library, content-addressed by SHA-256. |
| `backups/` when present | Previously created backups; not an automatic scheduled full-library backup service. |
| `server.lock` | Operating-system instance lock file. |

Browser reading position and some UI state live in that browser's local storage, not the library database. Browser-origin changes, clearing site data, changing ports or using another browser may lose that local position without losing generated audio or analysis.

**Remove** in the library is an archive operation. Restore it from Removed items. Files, analysis history and storage use remain. Removing a series leaves its books independently available. There is no permanent purge/garbage-collection command in the current application.

## Full backup and safe restore

The simplest full backup is a copy of the **entire data directory after the server has stopped**. Preserve originals and all four audio trees (enhanced, simple listening, voice examples and voice library) along with SQLite; a database-only copy is not a complete audiobook backup. Keep credentials separately from shareable data/source backups.

For the documented source launcher, this example uses the same configuration loader and creates a new timestamped directory outside the checkout. Run it from the project directory **only after server shutdown has completed**:

```sh
uv run --frozen python - <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import shutil
from bardic.config import data_directory, load_project_env

load_project_env()
source = data_directory().resolve()
if not (source / "library.sqlite3").is_file():
    raise SystemExit("No library.sqlite3 at the configured data directory")
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
destination = Path.home() / "Backups" / "Bardic" / stamp
if destination.resolve().is_relative_to(source):
    raise SystemExit("Choose a backup destination outside the library directory")
destination.parent.mkdir(parents=True, exist_ok=True)
shutil.copytree(source, destination)  # Refuses to overwrite an existing backup.
print(destination)
PY
```

This copies private ebook/analysis/audio data to a local backup destination; it does not publish or upload it. If the library path was supplied only in the server launch command, supply that same `BARDIC_DATA_DIR` for this command. Do not accidentally back up an unused default folder.

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

4. Launch against the restored copy on an alternate port. Startup can create/update schema and mark interrupted jobs, so test a copy rather than the sole backup. Open representative books, source references, covers, enhanced takes, simple-listening takes and saved voice examples; verify file availability and selected-audio playback. Do not start paid analysis/narration just to inspect a restore.
5. Once satisfied, stop the test server and point the normal launch configuration at the restored directory. Keep the previous library until the restore is verified for your use.

An integrity result of `ok` and no foreign-key violations do not prove every WAV exists or every reference has correct source meaning; perform the representative application checks too. Do not assume an older application revision can safely open a newer database. Keep the application source revision/lockfile with backup notes. Lost pre-retention artifacts or media absent from every backup cannot be reconstructed by a schema migration.

### Exports are not a complete backup

**Export analysis files** includes canonical source, profiles, observations, story map, retained artifact versions and transitive earlier-book dependencies, attempts/events, resource operations and listening metadata. It excludes API keys and audio binaries. There is no bundle import/restore UI yet.

The **audiobook ZIP** contains current enhanced takes, production metadata, text, a timeline and assembled WAVs for complete chapters. An incomplete chapter still contributes its available individual takes and missing-passage information. Simple-listening audio is separate and is not included by this export. Voice-example request/take archives and WAVs are not included in either ZIP. Neither export is a replacement for a full data-directory backup.

## Troubleshooting

Open **Settings → Troubleshooting → Download troubleshooting log** after a playback or buffering problem. The JSON contains the newest 5,000 local events across books, newest first; use `GET /api/diagnostics?book_id=<book-id>&limit=5000` for one book. The normal download uses `/api/diagnostics?limit=5000`. Match `job_id`, `segment_id`, `session_id` and timestamps to saved job details. Event codes distinguish request/poll/preparation/cache issues from media errors and waiting/resumption. Numeric HTTP/media codes and playback rate are included when known; server events identify failed or stopped listening jobs.

Logs exclude book text, API keys, URLs, stack traces and free-form browser messages. They remain local in SQLite, with duplicate suppression, at most 120 accepted client events per minute, and retention of the newest 5,000 events. The table is created lazily and additively. Browser/server reporting is best effort: a disabled network or logging error must not stop narration, so a missing event does not prove nothing happened. This is a troubleshooting window, not a complete audit trail, and it cannot recover an earlier toast that was never recorded. Export promptly when preserving a particular incident matters; it is separate from the analysis export and resource ledger.

| Symptom | What to inspect and do |
| --- | --- |
| Server says the data directory is already in use | Open the existing instance or stop its process. Confirm the resolved data path; changing only the port does not resolve the directory lock. Do not unlink the lock file to force concurrent access. |
| `bardic.local` does not open on another device | Check that the server printed `Advertising http://…` without a later `Name in use` line. Both devices must be on the same network; guest Wi-Fi and access-point client isolation block both mDNS and device-to-device traffic. `Invalid host header` means that name is not trusted: use the configured name or add it to `BARDIC_ALLOWED_HOSTS`. A device that looked up the name while the server was stopped can take a short time to resolve it again. |
| Address/port already in use | Stop the service using that port or choose `BARDIC_PORT`. The launcher checks `127.0.0.1:PORT` first because macOS would otherwise let a network-bound and a loopback-bound server share a port. A new port also means a different browser storage origin. |
| Library appears empty after restart | Check launch working directory, `BARDIC_DATA_DIR` / `SPINTAILS_DATA_DIR` and whether both `.bardic/` and `.spintails/` exist. A relative path or the default selection may have chosen another folder. Preserve both folders while locating the intended `library.sqlite3`. |
| Updated `.env` key appears ignored | Restart the server; inspect inherited variable names and the Gemini alias precedence. Settings changes are session-only. Use an explicit small check only when you want to test inference. |
| Model refresh succeeds but generation fails | Visibility does not prove structured-output compatibility, permission for a separate TTS model, quota or balance. Read the actual failure and verify the selected model role. |
| “Invalid character evidence” / quoted text not in source | Inspect the rejected output and request recipe in Pipeline explorer. Evidence must be a contiguous passage in that request's supplied source, not a paraphrase or a quotation from a different chapter. One repair is automatic; a repeated failure stops safely. Resume the relevant stage/chapter after reviewing model/output/context; do not weaken source validation or repeatedly force the entire book. |
| “Missing, duplicate, or unknown source IDs” | The model skipped a passage, annotated one twice, or mistyped an ID (small models do this on 30-passage batches). One repair naming the IDs is automatic; a repeated failure fails only that unit, and its section gets no version. Run the step again **without Fresh samples** to reuse every validated unit and request only the failed ones, or use a larger model for those sections. |
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
| New simple-listening passages have gaps | Watch the saved-audio buffer at the selected speed. Warmup and lookahead prepare serially within chapter/passage bounds; they cannot make a consistently slow provider outrun 2.5× listening. Use Prepare rest of chapter, wait until it finishes, then press Play. Completed audio stays cached. |
| Simple preparation stops with an error | Saved clips remain playable. Inspect the message, then choose Retry preparation when ready. No paid POST is automatically repeated; a current request may have completed after a lost response, so the API checks cache and matching active work first. |
| A voice example stops or will not play | Read its inline error and saved `voice_preview` job. A sample uses the selected provider and shared playback speed; generation may need a key/device, while an intact matching cache does not. Close the example to return to the paused book. Retry only when ready for a possible new charge. |
| Saved simple audio fails to play | Playback stops further lookahead. Explicit Play rechecks local server cache before any new synthesis. A malformed WAV or content-hash mismatch cannot be reused; a new uncached request may incur charges. |
| Highlighting does not follow individual words | Current timing is passage-level. There is no word-alignment result to enable yet. |
| Search finds too few results | Queries are literal lexical terms, with all terms required in a passage. Try fewer terms; confirm reading-order scope and available books. Search does not resolve pronouns or infer character identity. Missing SQLite FTS5 is reported as unavailable. |
| Removed books still use disk | Removal is reversible archiving. Restore under Removed items; permanent purge is not implemented. Do not manually delete shared history/assets to simulate a supported purge. |
| Dashboard numbers disagree with billing | Check scope, historical unknowns, reservations, reported token completeness and dated rates. The dashboard tracks this app's recorded work; invoices include account-wide activity and provider adjustments. |

## Development checks and the meaning of “verified”

Routine offline suite:

```sh
uv run --frozen pytest -q
node --test tests/*_test.js tests/*.test.cjs
```

Node is needed for the JavaScript behavior harnesses. Run the Node command explicitly: the model-picker CommonJS suite and `browser_storage_test.js` are outside pytest. Python tests invoke the remaining UI/player harnesses when Node is installed and can skip those checks when it is missing; read the test summary. The default suite uses fake provider transports and temporary libraries, not paid API calls or the user's real library.

The real macOS speech smoke test is opt-in:

```sh
BARDIC_TEST_SYSTEM_AUDIO=1 uv run --frozen pytest -q tests/test_audio.py
```

It uses local installed voices and should run in a normal terminal with speech-service access. Live cloud checks are separate user-selected actions. Passing mocks does not prove the current endpoint, model availability, project quota, voice quality or a full-book production.

[VALIDATION.md](VALIDATION.md) contains earlier successful and failed account checks, a short Gemini narration audition, a real-chapter analysis, browser checks and offline regression results. Those are historical observations tied to their recorded configuration and time. They are not a live health dashboard; do not infer that a credential still works or still fails today. Larger cloud workloads, subjective performance quality, target-device support and complete-book consistency require their own explicit validation.
