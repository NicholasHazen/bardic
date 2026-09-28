# Changelog

## Unreleased

- Added `./bardicctl` to manage servers. `install` runs Bardic from the main checkout as the macOS LaunchAgent `local.bardic`: it starts at login, restarts after a crash, and is owned by launchd rather than the session that started it. `status`, `start`, `stop`, `restart`, `logs` and `uninstall` control it. `status` also identifies a hand-started or detached server on the port. Commands that interrupt the service:
  - refuse while jobs are active, or when the server cannot report its jobs, unless `--force` is given;
  - need `--yes` when run from a linked worktree;
  - return only after the server process has exited.

  `stop` signals only Bardic processes. `./bardicctl dev start|restart|stop|list|logs` runs a checkout's code on its own port (8770–8799) with a scratch library, loopback bind and blank provider keys, recorded so any session can find or stop it. `GET /api/jobs?active=true` lists every queued or running job without the 100-job bound. See [run as a service](docs/OPERATIONS.md#run-as-a-service) and decision D13 for why these are processes rather than containers.
- Added **Breeze**, a self-hosted narration server on the local network, as a third narration provider for enhanced narration, simple listening and voice examples. Configure `BREEZE_TTS_URL` (and optionally `BREEZE_API_KEY`) or the URL in Settings, then **Check connection** to load its voices; the check never generates audio. Voice choices are pinned to the server voice's revision and seed, checked live before each request, and saved so existing audio replays while the server is off. Generation streams from the server so Stop ends GPU work; a new passage seed makes a new take. Breeze uses the per-passage listening path, records requests as self-hosted with no charge, and retains validated sentence timing on each take (not yet used for highlighting). See [voice research](docs/RESEARCH-VOICE.md#breeze-tts-self-hosted).
- Narration providers now share one provider table and dispatch in `audio.py`, and cast voices are stored per provider (`voices: {provider: {id, ...}}`). Older `voice`/`system_voice` fields are still read, so existing Gemini and device takes, listening sessions and caches keep their identities (pinned by golden tests); editing a voice rewrites that character in the new form. Fixed the listen panel sending the device model for any non-Gemini provider.
- Gemini simple listening now prepares chapters in large chunks through a server job paced to the project's request limits (defaults 10 requests/minute, 10,000 input tokens/minute, 100 requests/day, editable per model in Settings). A median chapter needs about 3 requests instead of about 177. Quick-start steps begin playback within seconds; the panel shows progress, ETA, listening time ready versus remaining, daily requests and whether the current speed stays ahead of generation. Passages inside a chunk play gaplessly with estimated timing, and the reader marks ready, generating and queued text. Truncated output is rejected and retried smaller; uncertain requests are never resent. See [chunked chapter listening](docs/LIBRARY-LISTENING-RESOURCES.md#chunked-gemini-chapter-listening).
- Corrected the Gemini TTS audio token rate to the measured 32 tokens per second (about 512 seconds per request).
- Renamed the application, Python package, browser interfaces and current documentation to **Bardic**. The preferred launcher is `python -m bardic`, with `BARDIC_PORT` and `BARDIC_DATA_DIR` configuration.
- Kept the old module launcher and environment aliases, reused existing `.spintails/` libraries in place, and retained access to saved browser preferences. New libraries default to `.bardic/`; existing `.env` files need no changes.
- Preserved the version-1 `spintails-analysis` portable export identifier and existing library/media formats. See [upgrade details](docs/OPERATIONS.md#upgrading-from-spin-tails).

## 0.1.0 · Initial repository baseline · 2026-09-27

This is the first version-controlled development snapshot, not a claim of a published package or production release.

- Local FastAPI/SQLite app with a browser library, reader, cast editor and production studio.
- DRM-free EPUB/TXT import, retained originals, exact source spans, structural labels and local preprocessing.
- Gemini, OpenAI and Anthropic analysis adapters with separate discovery/detail models, bounded progressive phases, source-validated evidence, accepted-unit reuse and request allowances.
- Chapter observations, character references, reviewed identities, series membership/links, missing-volume records and bounded series scheduling.
- Immutable artifact history and dependencies, lexical source search, pipeline inspection and portable analysis exports.
- Gemini and macOS narration, retained alternative enhanced takes, measured passage highlighting, independent simple listening and enhanced audiobook ZIP export.
- Editable library metadata, EPUB covers, size accounting, reversible removal and restoration.
- Explicit account access checks, dated cost estimates, request/operation usage records and a resource dashboard.
- Human and agent contributor guides, current architecture/data/API/development/operations references, design decisions, roadmap and dated research/validation history.

See [validation](docs/VALIDATION.md) for test evidence and limitations, and [roadmap](docs/ROADMAP.md) for partial features and expected work. Credentials, personal books, runtime databases/media and local validation captures are not part of the repository.
