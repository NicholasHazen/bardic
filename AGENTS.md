# Working on Bardic

This file is the entry point for coding agents. Humans should start with [CONTRIBUTING.md](CONTRIBUTING.md). User instructions for the current task take precedence over this repository guidance.

## Read before changing code

1. Read [README.md](README.md) for product behavior and [docs/README.md](docs/README.md) for the documentation map.
2. Use [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) to locate the subsystem. Read [docs/DATA-MODEL.md](docs/DATA-MODEL.md) before storage, source, cache or series changes.
3. Read the relevant tests and module code. [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) maps change types to tests and describes extension points.
4. Check [docs/ROADMAP.md](docs/ROADMAP.md) for implemented versus proposed work. Research documents are dated design context, not current provider guarantees.

The app is Python/FastAPI/SQLite with plain JavaScript and CSS. It has no frontend build, ORM, vector database, provider SDK dependency or distributed task queue. Preserve that simplicity unless the task justifies a change.

## Protect the library and source contracts

- Treat the user's `.bardic/`, legacy `.spintails/`, `.env`, original ebooks, audio and exports as private runtime state. Do not print keys, commit them, use their text as fixtures, or erase them to fix a test. Use `tmp_path`, original synthetic prose and mocked provider responses.
- Before a migration or repair affecting an existing library, take a recoverable backup and verify source, reference and audio preservation. Follow [operations](docs/OPERATIONS.md); a database-only backup omits media.
- Canonical chapter text is immutable. Spans are zero-based Python Unicode code-point offsets, with exclusive ends. They are not bytes or JavaScript string indices. The browser uses exact passage text and IDs.
- LLMs annotate source IDs and return evidence. They must not rewrite the prose. Evidence must resolve to the supplied source and exact original coordinates. Preserve ambiguity; never fabricate an evidence quote to satisfy validation.
- Book-local character IDs are not series identities. Cross-book context requires confirmed links and strictly earlier reading order. Missing volumes contribute no invented knowledge. Mentions are not proof of physical scene presence.
- Preserve manual edits and retained history. Current book projections, resumable caches, immutable artifacts and request attempts serve different purposes. Do not collapse them into one mutable result.
- Artifact dependencies must reference actual retained inputs. Accepted, rejected, cached, interrupted and unknown states remain distinct. Historical provenance cannot be reconstructed by guessing.
- Rendering recipes and audio content hashes are different identities. Forcing a new performance must not overwrite old audio. Simple listening must remain independent of cast settings and enhanced takes.

## Keep the API contract current

The checked-in [`contract/openapi.json`](contract/openapi.json) is the normative, language-neutral API contract. Dedicated clients will be generated from it, and a future non-Python server must satisfy it. Follow [the API workflow](docs/API-WORKFLOW.md) for every change to a route, request field, response field, error or route behavior:

- Describe the change in `bardic/apispec/` in the same commit. That covers the operation's description, errors, parameters and cost, the response `View` fields with descriptions, and `REQUEST_DOCS` for request fields. A new route needs an `op(...)` entry.
- Regenerate with `uv run --frozen python -m bardic.apispec` and review the diff. Record the change in `contract/CHANGELOG.md` and bump the version according to its rules. Never hand-edit `openapi.json` or `API-REFERENCE.md`; after a merge conflict, regenerate them.
- Every operation needs a test that receives its 2xx response. The test suite validates every API response against the contract, and fails on undeclared fields and undocumented error statuses. Do not weaken that check to make a test pass; fix the description or the code.
- Operation IDs are permanent client method names. Removing or renaming anything a client can see is a breaking change: classify it in the changelog and say what clients must change.

## Work and cost boundaries

- Development verification is offline by default. Loading a key does not authorize a paid whole-book run. Use a bounded live request only when the user has authorized that scope; do not switch providers automatically after a failure.
- Reserve the applicable analysis allowance before each paid HTTP attempt, including retries and evidence repair. Reuse validated outputs before requesting new work. Keep repair/retry counts bounded and retain completed work on failure.
- Unknown cost or missing usage is not zero. Do not infer credit balances from a successful API call. The analysis dollar guard is not a global narration/account spending cap.
- Snapshot provider/model/key configuration for queued work. Respect book reservations, series parent jobs, preview fingerprints and cancellation. A queued child that was cancelled must never start later.
- Keep database mutations short and serialized through existing Store locks/transactions. Do not hold a SQLite transaction open during cloud requests. Preserve single-instance protection for a data directory.
- Do not expose the service publicly as part of routine development. The app binds to loopback by default and has no user authentication. The owner's `.env` may set `BARDIC_LAN_NAME` for trusted-network access, so development launches blank it as shown in [development](docs/DEVELOPMENT.md). Its browser write-origin guard is not a public-service security model.

## Running servers

- The owner's library is served by the launchd service `local.bardic` from the main checkout. `./bardicctl status` is read-only and always safe: it shows what serves the port, which process owns it, and whether jobs are active.
- Do not stop, restart, install or uninstall the owner's service, or signal a server you did not start, unless the user asks in the current task. The owner may be listening, and a job may be spending requests. When asked, restart only with `./bardicctl restart`. From a worktree it needs `--yes`, which states that the user asked. It refuses while jobs are active; pass `--force` only with the user's agreement. Never relaunch the owner's library with `nohup`, a background shell or a second server.
- Changes in a worktree are not live in the service until they are merged into the main checkout and it restarts.
- To run your change, use `./bardicctl dev start` from your checkout (own port, scratch library, loopback, blank keys). Use `./bardicctl dev restart` after Python edits and `./bardicctl dev stop` before handing off. `./bardicctl dev list` also shows other agents' servers; leave those alone. See [development](docs/DEVELOPMENT.md#configuration-and-data-isolation) and [operations](docs/OPERATIONS.md#run-as-a-service).
- Use `./bardicctl dev start --keys` when you need to test against real providers. It gives your server the provider keys, the Breeze URL and the self-hosted analysis server URLs from the main checkout's `.env`, and none of its other settings. The analysis servers are free per request but share the owner's GPU with Breeze narration; keep live checks on them small too. Do not copy `.env` into a worktree: that copy would also bring the owner's absolute `BARDIC_DATA_DIR`, service port and network name. Those are the owner's accounts. Requests are billed and share rate limits and daily quotas (for example, the Gemini TTS daily requests) with the service. Use a keyed server only for the bounded live check the user authorized (see the cost boundaries above), with original synthetic text. When the live check is done, restart without `--keys` or stop the server. The flag lasts only until the next restart; pass it again if you still need keys.

## Implementation workflow

1. Inspect `git status --short` and preserve unrelated changes. Keep work scoped to the requested behavior.
2. Trace the existing path from UI/API through service/storage and tests before introducing abstractions.
3. For parallel agents, divide file ownership explicitly. Shared module edits require coordination; do not independently rewrite the same file.
4. Change the smallest cohesive set of contracts. New retained formats need version identifiers and compatibility/reuse decisions, not just new fields.
5. Verify the relevant invariants with focused tests. Run the full suite for changes spanning processing, storage or player integration.
6. Update the applicable current docs and roadmap status in the same change. For an API change, this includes the contract (see above). Record actual validation and limitations in the handoff.

Useful commands, from the repository root:

```sh
uv sync --frozen --group dev
uv run --frozen pytest -q
uv run --frozen python -m bardic.apispec          # regenerate contract/ after an API change
uv run --frozen python -m bardic.apispec --check  # exit 1 if contract/ is stale
npm ci && npm run contract:codegen                 # dev-only: clients can generate strict TypeScript types
node --test tests/*_test.js tests/*.test.cjs
for script in bardic/static/*.js; do node --check "$script" || exit 1; done
```

Python 3.11+ is supported by project metadata; the recorded development environment is Python 3.12 and Node 22. Node behavior tests do not install browser packages. The optional real Mac speech test is opt-in; routine tests use fake audio and do not call cloud providers. Node-dependent pytest wrappers skip if Node is missing, so run the Node command explicitly for frontend work.

Use `./bardicctl dev start` rather than a hand launch for a development server; [development](docs/DEVELOPMENT.md) also shows the equivalent key-free command. `python -m bardic` loads only the repository-root `.env`; existing shell variables win. Running the ASGI app directly does not invoke that loader.

The old `python -m spintails` launcher and `SPINTAILS_PORT` / `SPINTAILS_DATA_DIR` settings remain compatibility paths. New libraries use `.bardic/`; an existing `.spintails/` is reused in place when no directory is configured and `.bardic/` is absent. Use `bardic.config.data_directory()` when resolving the default in tooling. Preserve the version-1 `spintails-analysis` export format identifier; a product rename is not a schema change. See [upgrade details](docs/OPERATIONS.md#upgrading-from-spin-tails).

## Completion and review

- Explain the resulting behavior, why it changed, what passed, and any unverified external behavior.
- Distinguish a test with mocks from a live provider/device test. A passing test suite does not establish acting quality or word-perfect TTS.
- Check the staged diff and file list before committing. `.gitignore` is protection against mistakes, not permission to add personal data with `git add -f`.
- Do not introduce a license, remote, deployment, CI service or paid dependency as an implicit part of an unrelated task. These remain owner decisions.
- Avoid speculative framework rewrites. The roadmap records extension opportunities; it does not authorize doing all of them in one change.
