# Bardic

A local audiobook studio for your fiction library. Import an EPUB or text file, build a cast, direct the performances, and listen while the exact passage is highlighted.

**For contributors:** start with [the documentation map](docs/README.md), [architecture](docs/ARCHITECTURE.md), [development guide](docs/DEVELOPMENT.md), and [roadmap](docs/ROADMAP.md). Humans and agents use the same contracts; [CONTRIBUTING.md](CONTRIBUTING.md) explains the workflow and [AGENTS.md](AGENTS.md) is the agent entry point.

## Run

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). For local Mac narration, install ffmpeg (`brew install ffmpeg`). Gemini works on other platforms without macOS voices.

For a new checkout:

```sh
git clone https://github.com/NicholasHazen/bardic.git
cd bardic
```

From the checkout root (including an existing `spin-tails` folder):

```sh
uv sync --frozen --group dev
uv run --frozen python -m bardic
```

Open **http://127.0.0.1:8765**. The server binds only to your computer. Stop it with Ctrl+C. On macOS, `./bardicctl install` instead runs it in the background under launchd, starting at login; `./bardicctl status`, `restart`, `stop`, `start` and `logs` manage it (see [run as a service](docs/OPERATIONS.md#run-as-a-service)). Set `BARDIC_PORT` to choose another port. To open it from a phone or another computer on your network as `http://bardic.local:8765`, set `BARDIC_LAN_NAME=bardic`; there is no login, so read [local-network access](docs/OPERATIONS.md#local-network-access) first. The Python virtual environment and lockfile keep dependencies reproducible.

**Upgrading from Spin Tails:** keep your existing checkout and `.env`. The preferred launcher is now `python -m bardic`; `python -m spintails` remains a compatibility launcher. Existing `SPINTAILS_PORT` and `SPINTAILS_DATA_DIR` settings still work. If no data directory is configured, Bardic uses an existing `.spintails/` in place when `.bardic/` is absent. No library move or database migration is required for the rename. See [upgrade details](docs/OPERATIONS.md#upgrading-from-spin-tails).

## Try it

1. Import a DRM-free `.epub` / UTF-8 `.txt`, or choose **Try a sample** for an original short story.
2. In **Read & listen**, expand **Listen your way**, choose a provider and narrator, then choose **Start simple listening**. No story analysis is needed. **Hear example** auditions the selected voice first; device voices are free and local, while Gemini can incur charges.
3. Use the shared Play/Pause controls to listen with passage highlighting. Choose a chapter or select a passage to move through the book; your reading position is saved in this browser. **More listening options** holds speed, speech model, and chapter preparation controls.

For a directed performance with different character voices, use **Studio → Plan analysis** to open **Story analysis**. Its free census scans the whole book locally. Choose a cloud provider and **Scan whole book** for character discovery, then **Build profiles**, then **Direct selected chapter**. Preview the work and set request, token, and book spending allowances before starting; resume reuses accepted steps. In **Cast**, review evidence and profiles and choose each character's voice; make or refine voices in **Voices**; **Series & continuity** holds returning-character links. Back in the studio, review scene tone and passage directions, generate a scene to audition it, then narrate the book when satisfied. Choose **Studio voices** as the playback source to hear these takes. The audiobook export contains completed chapter WAV files, individual takes, source text, production notes, and a JSON timeline.

**Start listening** (above the chapter text) opens a sheet to choose the narrator, voice and speed. Its **Performances** tab lists saved performances: chosen chapters narrated ahead of time by one narrator or the full cast, played later without waiting or new requests (see [saved performances](docs/LIBRARY-LISTENING-RESOURCES.md#saved-performances)). It shows a short buffering spinner, then opens the **reader view**: the chapter text and the player, without the sidebar or tabs. **Reader view** opens it without playing. In the reader, **Aa** sets the colour (Paper, Sepia, Dusk, Night), text size, font, line spacing and page width; the headphones reopen the narrator sheet; **Exit** (or Escape) returns to the workspace. The page follows the narration; scroll away and it waits, with **Back to narration** to return. On iPad and iPhone the player respects the home indicator, shows the book cover, and lock-screen controls show the book and chapter; the screen stays awake while you listen in the reader. Use **Library** to return to your bookshelf. **Find a book** filters by title or author, and the chapter selector above the reader works at every screen size. The three book tabs support Left/Right arrow keys, Home, and End.

With **Gemini**, Play starts (or joins) a chapter job that sends large chunks of the exact chapter text, paced to your Google project's request limits (set them in **Settings → Narration request limits**; defaults match a 10 per minute / 100 per day tier). Quick-start chunks begin playback within seconds, and later chunks carry up to about 7 minutes of audio each, so a typical chapter needs a few requests instead of one per passage. **Queue chapter** prepares the rest of the chapter without playing. The panel shows passages ready, listening time ready and remaining at your speed, an ETA, requests used today and whether your current speed will stay ahead of generation; the text is underlined as passages become ready. Timing inside a chunk is estimated from pauses. See [chunked chapter listening](docs/LIBRARY-LISTENING-RESOURCES.md#chunked-gemini-chapter-listening).

With **device voices**, simple listening starts with a visible warmup aiming for 10 seconds of listening, preparing at most three passages before playback. While playing, the app prepares a rolling buffer of about 45 listening seconds, bounded to the current passage and at most 12 future passages in this chapter. The buffer accounts for playback speed, including 2.25× and 2.5×. Requests run one at a time, and the browser preloads the next two available clips locally. If generation cannot keep pace, open **More listening options → Prepare rest of chapter** to save the remaining passages before playing; it shows the passage count and does not start playback.

The simple-listening panel and bottom player share Play/Pause and playback speed. The bottom scrubber spans the whole chapter, with elapsed and total chapter time plus percent of chapter and book; a total marked `~` includes text-based estimates for passages that are not narrated yet. Dragging previews the position, and releasing seeks. While paused, seeking only moves your place and does not start narration. The player shows the selected narrator; **Voice & listening settings** opens its controls. The panel starts collapsed to leave more space for reading; its summary keeps the selected source, narrator, provider, charging status and chapter-preparation progress visible. A new preparation error opens the panel once so you can inspect it. With **Studio voices** selected, pressing Play on a passage with no take opens narrator setup, so you can choose how to listen before generation starts. Changing speed adjusts an active warmup or rolling buffer without restarting narration.

Choose **Hear example** beside a narrator voice, either cast voice, or a Studio passage speaker to audition that selection. Examples use a short prefix of the relevant book passage, capped at 400 Unicode characters, or original demo text when no passage is assigned. Cast examples include unsaved voice and direction edits; Studio examples include the selected speaker and unsaved passage direction. The example uses the shared player and speed, pauses reading, and preserves your reading position. Close it and press Play to resume the book. Each click requests one sample, matching saved examples are reused, and Gemini examples may incur charges. Auditioning does not save casting edits or replace a simple/enhanced take.

Simple listening needs no story analysis. It has its own saved audio and never changes cast voices, performance notes or enhanced recordings. Exact text with matching provider, model and voice can reuse saved simple audio across passages and books without another synthesis request. Playback highlights the current passage and continues into the next chapter until you pause or stop, a request limit is reached or the book ends; turn off **Continue into the next chapter** to stop at each chapter boundary. With Gemini, the next chapter is queued about 10 minutes ahead while you listen. Device voices are free; Gemini sends uncached passages to Google and can incur charges, including those prepared ahead. There is no narration spending cap. Pause or Stop prevents further requests after any in-flight passage finishes. Preparation errors preserve completed audio and offer an explicit retry; paid generation requests are not automatically repeated.

Playback and buffering problems now leave a bounded local troubleshooting log. In **Settings → Troubleshooting**, choose **Download troubleshooting log** to inspect the latest 5,000 events, with passage/job IDs, playback speed and error codes. It excludes book text, credentials and free-form error messages. Correlate job IDs with saved job details when investigating failures. Logging is best effort and never controls playback; it cannot reconstruct an earlier error that was never recorded. See [troubleshooting](docs/OPERATIONS.md#troubleshooting).

Open **Books & series** in the sidebar to edit titles and authors, extract covers from saved EPUBs, inspect content size and disk usage, create series, and assign reading order. Removed books and series are recoverable under **Removed items**; their files and analysis remain saved, so removal does not reclaim disk space. Removing a series leaves its books independently available. Add missing or planned volume placeholders when your collection is incomplete.

Choose **Open series** to preview and run discovery with up to two books in parallel, followed by profiles and direction in reading order. Only supplied volumes are processed. Character continuity uses explicitly confirmed identity links; missing volumes never supply guessed evidence. Request/token limits are per book and shared across phases; each book's dollar allowance includes its earlier tracked analysis spend. A failed or budget-limited book stops new work while completed outputs remain reusable.

In **Studio → Production details → Resource usage**, inspect requests, retries, cache reuse, reported tokens, step durations, local Python CPU time, output bytes, audio duration, and estimated cost by stage and run. Unknown historical measurements and unreported provider usage stay marked unknown. Cost estimates are not account balances or invoices; local CPU measurements exclude subprocesses, GPUs and provider hardware. See [library, listening and resource tracking](docs/LIBRARY-LISTENING-RESOURCES.md).

In **Studio → Production details → Pipeline explorer**, inspect every stage, saved output versions, dependency links, runs, request usage and validation. Search source passages locally, or choose **Export analysis files** to download source, cast, scene graph, observations and provenance before generating audio.

Analysis and narration are independent: for example, use Anthropic to direct the book and Gemini to perform it. The selected analysis provider receives the source excerpts and production notes needed for cast discovery, reconciliation, and scene direction. Only the chosen provider is used; there is no automatic fallback to another company.

The Mac narration provider speaks using installed voices and ignores expressive performance notes. It is useful for free local listening and testing the pipeline. Gemini narration interprets those directions and sends the selected text and notes to Google. Library files and generated audio remain local in both modes.

## Provider setup

Create a Gemini API key in [Google AI Studio](https://aistudio.google.com/apikey). Put provider keys in the project's `.env` file beside `pyproject.toml`; [`.env.example`](.env.example) is the template for a fresh setup:

```dotenv
GEMINI_API_KEY=your-gemini-key
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
```

`python -m bardic` automatically loads this project's `.env` at startup, including when launched with `uv run --frozen`. Restart the server after editing it. Existing shell environment variables override matching `.env` entries; values are read literally without variable interpolation. The loader does not search parent folders. `.env` is excluded from Git.

You can also open a provider in **Providers & settings** and enter keys for the current server session. Those changes are kept in memory, never written to `.env`, SQLite, or browser storage. Clearing a key affects only that session; a restart reloads any configured file or environment key. Gemini keys are also accepted as `GOOGLE_API_KEY`.

Settings has separate fast discovery and detailed analysis models for each provider, full dropdowns, and custom IDs. Explicit **Refresh models** uses the provider’s read-only inventory; it does not generate text or prove remaining credit. Preferences are saved locally. Cheap discovery defaults are Gemini `gemini-3.5-flash-lite`, OpenAI `gpt-6-luna`, and Anthropic `claude-haiku-4-5-20251001`. Suggested defaults are Gemini `gemini-3.8-flash`, OpenAI `gpt-6-sol`, and Anthropic `claude-sonnet-5`; you can enter another model ID that supports structured JSON outputs. Account access and quotas are checked by the provider when a request runs.

Default narration: `gemini-3.8-flash-tts`. Settings also offers Flash-Lite and the legacy `gemini-3.1-flash-tts-preview`. The newer API keeps delivery directions separate from the verbatim transcript; the legacy adapter uses a director prompt. Custom `voice_…` IDs already created in Google can be pasted into a character's Gemini voice field. Voice creation/cloning is not part of this version.

Cloud generation and analysis run only when you choose those actions. They can incur API charges. Test a short scene to check model access, voice quality, and quota before a full book. See the dated [voice research](docs/RESEARCH-VOICE.md) for pricing and official sources.

OpenAI and Anthropic currently cover the text analysis phases in this app. Narration choices are Gemini, installed Mac voices and a self-hosted Breeze server.

**Breeze** is a narration server you run on your own network. Set `BREEZE_TTS_URL=http://host.local:7860` (and `BREEZE_API_KEY` only if the server requires one) in `.env`, or enter the URL in **Settings → Breeze**, then press **Check connection**. The check lists the server's voices, imports them into the voice library and never generates audio. Only cloned voices can narrate. Choose a Breeze voice per character in **Cast** (or leave it on **Default**) or as the simple narrator. Breeze has no per-request charge, but it sends passage text and performance notes to that server, over plain HTTP unless the URL uses `https`. See [analysis providers](docs/ANALYSIS-PROVIDERS.md) for API contracts and sources.

**Voices** is a library of voices shared by every book. Design a Breeze voice from a description and sample line (free previews on your server), clone one from a recording you have consent to use, or design a Gemini voice (each create is billed and stored in your Google project, at most 200 per project for one year, and asks for confirmation every time). **Iterate** saves a new version of a voice; characters follow a voice's current version, and you can switch back. In **Cast**, a character's voice menu offers **Default**, your voices and **Create new voice…**, which opens Voices with that character's description and a line of their dialogue, then returns with the new voice assigned. See [voices and casting](docs/OPERATIONS.md#voices-and-casting).

In **Settings**, open a provider and use **Check API**, or open **Check provider access → Check all accounts**. Each check makes one tiny text request using that provider's selected analysis model, then reports access, billing/credit errors, quota/rate limits, and any token usage returned for that check. It may incur a small API charge; it never sends book text or starts narration. Checks are explicit, with no background polling or automatic retries, and identical checks reuse their result for 30 seconds.

These are access checks, not a remaining-dollar estimate: a successful request does not establish the budget for a whole book or access to a separate TTS model. Exact balances and account-wide usage are available through the linked provider dashboards. Results stay in server memory, show their model and time, and are invalidated when the corresponding key or model changes. See [account check details](docs/ACCOUNT-CHECKS.md).

## How the pipeline works

`EPUB / TXT → structure + free census → cheap discovery → evidence profiles → chapter direction → cached audio → read-along / export`

- EPUB uses the package spine's reading order and navigation/NCX labels, then headings and semantic fallbacks. Recaps, front matter, and back matter are identified separately; logical anchors retain exact source ranges. The original upload is retained; the reader displays extracted canonical prose, without the original ebook layout. Encrypted reading content is rejected; no DRM removal is performed.
- The model annotates source IDs; it never supplies replacement prose. Character descriptions cite source evidence. Human edits survive re-analysis.
- Local analysis is deliberately conservative: explicit speech tags identify speakers, while pronouns and ambiguous dialogue stay unassigned. Cloud analysis discovers characters chapter by chapter, builds profiles from the saved observations, then directs each selected chapter and proposes scene boundaries. Character references distinguish named mentions, attributed dialogue, and profile evidence; each links to an exact source location.
- One short passage per voice request permits arbitrary cast size despite provider speaker limits. This also makes failures and edits inexpensive to retry. Separate clips may have audible seams; there is no promise of identical timbre across cloud generations.
- Per-book processing checkpoints validated analysis requests and completed audio takes in SQLite. A series coordinator permits at most two independent discovery workers and orders its later phases. Cancelling stops after the current request. Restarting marks unfinished jobs as interrupted; analyzing again reuses matching saved steps, and generating again reuses matching audio. Chapter stages publish atomically, so a failed later chapter preserves earlier work. Evidence gets one repair attempt; unlocatable quotations remain errors. See [chapter analysis](docs/CHAPTER-ANALYSIS.md) for stages, reference records, and invalidation rules.
- Changing a voice or performance note invalidates affected takes. Prior audio remains in the content-addressed cache and can be reused if you restore the same settings.
- Highlighting is **passage-level**, measured from real clip boundaries. This version does not claim word timing or independently verify that a generative voice spoke every word. The [word-highlighting proposal](docs/WORD-HIGHLIGHTING.md) describes optional local alignment of saved audio, reusable word timings and quality checks.
- Exports only assemble complete chapters. Partial exports clearly identify missing passages and include completed individual takes.

## Reusable artifacts

SQLite stores immutable versions of source, structure, scene maps, profiles, voice assignments, observations and accepted analysis outputs. Current selections are separate from history. Request recipes and rejected structured responses are retained for inspection; rejected results never become accepted knowledge. Forced narration retains distinct WAV assets even when the generation instructions are identical. Restoring an earlier performance recipe can reuse its archived take.

The analysis ZIP contains portable JSON/JSONL, exact chapter-local source coordinates, typed graph edges, all retained versions and their transitive input dependencies, including earlier-book evidence. Audio binaries use the separate audiobook export. Legacy records disclose missing provenance; versions lost before this retention system cannot be recreated.

SQLite FTS5 provides lexical passage search over this book or this book plus earlier series volumes. No vector database or separate server is required. See [artifacts and storage](docs/ARTIFACTS-AND-STORAGE.md) for schemas, reuse, limitations and the researched path to optional semantic retrieval.

## Progressive processing and series memory

The [research and implementation plan](docs/PROGRESSIVE-ANALYSIS-PLAN.md) records the design and primary sources. [Structure details](docs/STRUCTURE.md) explain safe title repair.

- The free census counts known-name mentions, explicit speech tags, dialogue, chapter spread, and uncertainty. These allocate profile effort; they do not establish identity, physical presence, or literary importance.
- Whole-book discovery coverage and profile currency are separate. Scan results are provisional. Refinement uses bounded, varied evidence across the story; later evidence or linked series context can make a previously refined profile stale. Rare characters still receive basic analysis.
- Only explicit series membership, reading order, and character links enable cross-book context. Earlier volumes contribute source-validated observations. Namesakes are separate unless linked; later volumes are excluded. Contradictions and provenance are retained.
- Durable accepted units are independent of the active checkpoint. Discovery survives profile/model/voice changes. Direction and profiles use their actual prompt dependencies to determine reuse. Human-reviewed choices remain authoritative.
- Every paid analysis HTTP attempt reserves allowance before sending, including retries and evidence repairs. The default is 25 requests, 1 million input tokens, 100,000 output tokens per run, and a $1 cumulative tracked allowance per book. The dollar guard uses dated prices and conservative reservations, not an exact invoice; usage before tracking and other account spending are excluded. Resume does not reset tracked book spend. Unknown/custom prices require explicitly selecting request/token-only limits. These limits apply to the Studio phase controls and series runs; **Analysis** tab runs have no cap and are authorized by confirming their preview, which states the estimated requests and cost.
- Authentication and billing failures are not retried automatically. Uncertain network errors are retained as potentially charged and stop the run. Transient responses have at most one short retry; invalid evidence has at most one repair. Budget stops preserve accepted work. Preview estimates exclude future discoveries and retry costs, and full-mode work can change after discovery.
- Local heuristic drafts are free but do not count as semantic discovery. A future local model can implement the same structured request/result contract as the cloud adapters. No local runtime is assumed or installed. Provider batch executors and advanced NLP remain optional follow-ups.

## Data and development

New libraries default to `.bardic/` in the working directory: SQLite library/job history, original uploads, and audio. An existing `.spintails/` is reused when `.bardic/` is absent and no data directory is configured. Override with `BARDIC_DATA_DIR`. Back up the active directory with the app stopped. The UI remembers reading position in this browser's local storage and can read saved Spin Tails preferences.

```sh
uv run --frozen pytest -q
node --test tests/*_test.js tests/*.test.cjs
# Optional real macOS narration and assembly smoke test:
BARDIC_TEST_SYSTEM_AUDIO=1 uv run --frozen pytest -q tests/test_audio.py
```

Tests cover EPUB safety/spine order, Unicode source integrity, conservative attribution, model-result validation, audio contracts, cache reuse/invalidation, exports, and restart recovery. Cloud API behavior is tested with recorded-shape mocks; live paid inference needs your key. The local voice smoke test must run in a normal terminal with access to macOS speech services.

See the current [architecture](docs/ARCHITECTURE.md), [data model](docs/DATA-MODEL.md), [API guide](docs/API.md), [operations guide](docs/OPERATIONS.md), and [roadmap](docs/ROADMAP.md). [Design decisions](docs/DECISIONS.md) explain the tradeoffs; [pipeline research](docs/RESEARCH-PIPELINE.md) and [voice research](docs/RESEARCH-VOICE.md) preserve dated external findings. The application uses FastAPI, SQLite, plain JavaScript, and standard WAV files: no frontend build step, hosted database, or account system.

The [GitHub repository](https://github.com/NicholasHazen/bardic) contains source, tests, documentation and the dependency lockfile. The application is named **Bardic**, with the Python package `bardic`. Personal ebooks, database/media state, credentials, exports and local validation captures are excluded. No project license has been selected. Use synthetic/original excerpts for shared tests; see [contributing](CONTRIBUTING.md).
