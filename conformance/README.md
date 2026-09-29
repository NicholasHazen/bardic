# Conformance suite

A black-box test suite for the Bardic HTTP API. It drives a server over real HTTP and judges every response
by [`contract/openapi.json`](../contract/openapi.json) alone. It works against any server that implements the
contract: the Python server today, the Rust server later. It never imports the `bardic` package and knows nothing
about a server's internals ([docs/RUST-SERVER-PLAN.md](../docs/RUST-SERVER-PLAN.md), phase 2).

The default `uv run --frozen pytest -q` of this repository collects only `tests/`. This suite runs only when you
point pytest at `conformance`.

## Commands

Run from the repository root.

```sh
# Spawn this repository's Python server on a scratch library and a free port, run everything, stop it.
uv run --frozen pytest conformance

# Drive a server that is already running (a scratch server: the suite imports books and changes settings).
uv run --frozen pytest conformance --base-url http://127.0.0.1:8791

# Spawn any other server (see "Testing another server").
uv run --frozen pytest conformance --server-cmd "/path/to/server-binary" --contract /path/to/pinned/openapi.json

# Only the tests of the harness itself. No server is started.
uv run --frozen pytest conformance/selftest
```

Add `--require-coverage` to fail when an operation neither received a 2xx nor is listed in
[`needs_provider.txt`](needs_provider.txt). It is off by default so the suite can grow domain by domain.
`--verbose-coverage` also lists the operations that wait for fake providers. `-k`, `-x` and the other pytest options
work as usual.

| Option | Meaning |
| --- | --- |
| `--base-url URL` | Use a running server. Refused unless the host is loopback (`--allow-remote`) and the port is not 8765 (`--allow-owner-port`). |
| `--server-cmd CMD` | Spawn `CMD` as the server. Default when neither option is given: the Python server (`python -m bardic`, from the repository root). |
| `--contract PATH` | The `openapi.json` to judge by. Default: the repository's `contract/openapi.json`. Read fresh at the start of every session. |
| `--server-timeout S` | How long a spawned server may take to answer `GET /api/status` (default 60). |
| `--server-cwd DIR` | Working directory of a spawned server (default: the repository root). |
| `--server-env NAME=VALUE` | Extra environment for a spawned server. Repeatable. |
| `--allow-remote`, `--allow-owner-port` | Override the two guards above. Only for a server you own and can discard. |

The owner's service normally listens on 8765 with the owner's library. The guards exist so that a stray command
cannot write to it. A `--base-url` server must be disposable: the suite imports books, edits them and changes
saved settings (it restores the ones it changes, but not everything). Never point it at a server that has real
provider keys. Tests that could reach a provider skip themselves when the server has a key or a Breeze URL.

## Testing another server

A server under test needs only what the contract and the launch convention say:

1. Read `BARDIC_DATA_DIR`, `BARDIC_PORT` and `BARDIC_HOST` from the environment (or take `{data_dir}`, `{port}` and
   `{host}` from the command line: those three placeholders in `--server-cmd` are substituted).
2. Bind loopback only, answer `GET /api/status` with 200 when ready, and stop cleanly on SIGTERM.
3. Trust only loopback `Host` headers, since `BARDIC_ALLOWED_HOSTS` and `BARDIC_LAN_NAME` are passed blank.

The harness starts the command with a fresh temporary data directory, a free loopback port chosen by the OS, and
all provider keys and service URLs blank (`GEMINI_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`BREEZE_TTS_URL`, `BREEZE_API_KEY`, `BARDIC_LOCAL_LLM_URL`, `BARDIC_BOOKNLP_URL`, `BARDIC_NOVEL_ANALYZER_URL`,
`BARDIC_LAN_NAME`, `BARDIC_ALLOWED_HOSTS`). It puts the server in its own process group and ends that group, and only
that group, when the session ends: after a pass, after a failure, on Ctrl-C and on SIGTERM (`selftest/test_hygiene.py`
checks the last two). A SIGKILL of the test process cannot be intercepted and can leave the server running.
POSIX only.

From a Rust repository, build the release binary and run this suite from a checkout of this repository, with the
Rust repository's pinned copy of the contract:

```sh
cargo build --release                                  # in the Rust repository
uv run --frozen pytest conformance \                   # in a checkout of this repository
    --server-cmd /path/to/rust-repo/target/release/bardic-server \
    --contract /path/to/rust-repo/contract/openapi.json
```

A Rust repository that vendors `conformance/` runs the same command from its own root, with `uv` and this
repository's `pyproject.toml` dependency group (or `pip install httpx jsonschema pytest`).

Pin the contract: `--contract` should be the copy the server was built against, not this repository's moving
file. When the contract documents a handshake, `Status.contract.sha256` must equal the SHA-256 of that exact file
and the `Bardic-Contract-Version` header must equal its `info.version`. The suite needs Python 3.11+, `httpx`,
`jsonschema` and `pytest` and nothing from the Python server; `uv sync --frozen --group dev` installs them.

## What every response is checked against

Every call goes through `conformance.client.Api`, which builds the request from the contract, checks it against the
operation's schemas before sending it, sends it, and fails the test on any of these:

- the path matches no operation (the router's own 404/405 `route_not_found` is allowed where a test expects it);
- the status is not documented for that operation (exceptions: 403 on a write, from the write guard; 422 from request
  validation). A 416 on a range-capable file is documented: a JSON `Error` (code `range_not_satisfiable`) with
  `Content-Range: bytes */<size>`. A 500 is a defect and fails the test that received it;
- a success status other than 200 (206 on a range-capable file, 304 on a conditional one);
- an error body that is not `{detail, code}`, or a `code` that is not listed for that status (`x-bardic-error-codes`)
  and is not a global code of the contract introduction at its own status;
- a binary response with a media type other than the documented one, or a JSON response that is not `application/json`;
- a JSON body that does not validate against the operation's response schema. **Objects are closed:** any object
  schema that declares `properties` and no `additionalProperties` refuses undeclared fields, at any depth, reported with
  a path such as `$.chapters[0].foo`. Objects that declare `additionalProperties` (free-form maps) stay open. Integers
  must be integers (`3.0` is not), and a boolean is not a number;
- a missing `Cache-Control: no-store` on an `/api/` response (except a conditional file, which manages its own caching),
  and, when the contract documents it, a missing or wrong `Bardic-Contract-Version` header.

`Api.call(operation_id, path=..., query=..., json=..., form=..., files=..., expect=200)` is how tests talk to the
server. `expect` is the status the test requires (`None` accepts any documented status). `negative=True` skips the
request check for a deliberately invalid request; the response is still held to the contract. `skip=('cache-control',)`
leaves one header check out of one call, so a test about something else is not failed a second time for a deviation
that has its own test.

## What is tested

- `test_transport.py`: Host check, write guard, `Cache-Control`, router errors, request validation, the 413 refusal.
- `test_handshake.py`: the version header and `Status.contract`, when the contract has them.
- `test_system.py`: status, settings (partial, atomic, keys never returned), provider reads that need no key,
  diagnostics, the voice library's local reads and draft lifecycle.
- `test_import_and_read.py`: TXT and EPUB import, the projection's promises (code-point offsets, text rebuilt from
  `leading_text`, passages and `trailing_text`, scenes partition the passages), cover thumbnails and their caching.
- `test_library.py`: listing and order, metadata, refresh and repair, archive and restore, search, exports and ranges.
- `test_jobs_and_pipeline.py`, `test_pipeline_review.py`: the local analysis pipeline, plans, fingerprints, job polling,
  terminal states, review, accept and reject. Only free local steps run.
- `test_series.py`, `test_series_runs.py`: membership, placeholders, identity links, confirmed-link-only context, a
  series run over local steps.
- `test_book_edits.py`: cast, passage, scene and pronunciation edits; canonical text and anchors never change.
- `test_listening_plans.py`: local narration plans, and refusals that must queue nothing.
- `test_contract_sweeps.py`: checks generated from the contract itself, over every operation of a shape (unknown IDs,
  unknown fields, missing required fields, wrong scalar types). They name no operation.

All book text is invented for these tests (`synthetic.py`) and built in memory: a TXT and an EPUB with a PNG cover.

## The coverage report

At the end of a session that sent requests, the suite prints which operations received a 2xx, which never did, split into
"needs fake provider" (listed in [`needs_provider.txt`](needs_provider.txt), one `operationId  # reason` per line) and
"not yet covered", and the error codes exercised. The lists come from the contract, so a new operation shows up as
"not yet covered" until a test or the allowlist accounts for it. Operations in the allowlist wait for fake providers
(narration, audio files, the voice library, a series run paused on a model step); the suite exercises their refusals
but never their success, and never calls a real provider.

## The harness tests itself

`selftest/` runs without a server. It feeds the checker exchanges directly and through an in-memory transport, and
proves the suite can fail: it must refuse an undeclared nested field, a missing required field, a wrong type, an
undocumented status, an unlisted error code and a wrong media type, and accept a correct body. It also builds a valid
instance of every response schema of the real contract and checks that an undeclared field is refused at every closed
object of every one. It checks the target guards, that a spawned server gets a scratch library, a free port and no
keys, and that the server and its children are gone afterwards, including after Ctrl-C and SIGTERM.

## Not yet covered

Fake providers with recorded requests (reservation before every paid attempt, retries, never resending an uncertain
request), text vectors for evidence matching and case folding, provider endpoint overrides, and the invariants that need
them. Add fake-provider tests by removing the operation from `needs_provider.txt` when its 2xx is exercised.
