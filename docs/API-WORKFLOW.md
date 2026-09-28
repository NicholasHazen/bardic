# API contract workflow

This document is for anyone, human or agent, who changes an HTTP route, a request field, a response field or an error. The rules apply to every such change, however small.

## Why the contract matters

Bardic's server will eventually be rewritten in another language, and dedicated clients will be built against its API. What must survive is the **contract**: the language-neutral description of every operation, field, error and behavior. It lives in [`contract/`](../contract/):

| File | What it is | Edited by |
| --- | --- | --- |
| [`openapi.json`](../contract/openapi.json) | The normative OpenAPI 3.1 contract. Clients generate code from it, and a future server must satisfy it. | Generated. Never edit by hand. |
| [`API-REFERENCE.md`](../contract/API-REFERENCE.md) | A readable rendering of `openapi.json`: every operation, parameter, response, error and schema. | Generated. Never edit by hand. |
| [`CHANGELOG.md`](../contract/CHANGELOG.md) | The version rules and one entry per contract change. | By hand, in the same change. |

The Python server produces the contract from [`bardic/apispec/`](../bardic/apispec/):

- Each route family has a module (`system.py`, `library.py`, `series.py`, `books.py`, `inspection.py`, `listening.py`, `voices.py`, `pipeline.py`). A module contains:
  - one `op(...)` entry per operation: operation ID, tag, summary, description, response view or media type, errors, parameter docs and cost class;
  - the `View` models that describe response bodies;
  - `REQUEST_DOCS` for request DTO fields.
- `common.py` holds views shared across families, currently `Job`.
- `spec.py` merges these entries into FastAPI's generated document. It also defines `VERSION`, the tag list and the general conventions text.
- Request DTOs stay next to their routes (`app.py`, `voice_routes.py`, `pipeline/api.py`). They are the request half of the contract.

[`docs/API.md`](API.md) explains cross-cutting concepts: transport, security, IDs and offsets, jobs, errors and cost. Per-route details belong in the operation's description in `bardic/apispec/`, not in API.md, so that they reach `openapi.json`.

## What the tests enforce

| Check | Where | Fails when |
| --- | --- | --- |
| Response conformance | [`tests/conftest.py`](../tests/conftest.py) wraps every `TestClient` send, including streams | Any of the following, for any `/api` response a test receives: <ul><li>A JSON body does not validate **strictly** against its operation's view; a string where a number or boolean belongs is a failure.</li><li>A field appears that no view declares, at any depth.</li><li>A success status other than 200 is returned, or 206 on a range-capable file.</li><li>An error status is not documented. The exceptions are 403 on writes (the write guard), FastAPI's own 422 validation list and 416 on range-capable files.</li><li>A binary response has the wrong media type.</li><li>A path matches no operation.</li></ul> |
| Operation coverage | `tests/conftest.py`, at the end of an unfiltered full run | Some operation never received a 2xx response in the whole suite. |
| Complete registry | [`tests/test_contract.py`](../tests/test_contract.py) | Any of: a route without an `op(...)` entry; an entry without a route; an `/api` route hidden with `include_in_schema=False`. |
| Everything described | `tests/test_contract.py` | A schema, schema field or parameter has no description. |
| Current snapshot | `tests/test_contract.py` | `contract/openapi.json`, `API-REFERENCE.md` or the recorded hash in `CHANGELOG.md` differs from what the code generates. |
| New version per change | `python -m bardic.apispec` and `tests/test_contract.py` | Either the newest changelog heading is not `VERSION`, or `openapi.json` changed after that version's hash was recorded. |
| Registry sanity | `bardic/apispec/spec.py` at build time | Any of: duplicate operation IDs or entries; a view name collision; a missing summary or description; an unknown tag; a documented parameter or request field that does not exist; a route whose FastAPI success status is not 200. |

**What is not enforced.** A reviewer has to check these:

- **Untested states.** Responses are validated only for states some test produces. A field a view declares but no test ever produces is unverified, and so is an error status no test triggers.
- **Removed fields.** A field declared with a default (optional) can disappear from responses without a failure. Declare fields that are always sent without a default, as `X | None = Field(description=...)` if nullable, so that removing them fails.
- **Untyped values.** `Any` and `dict[str, Any]` values are not checked below their top level.
- **Change classification.** The generator forces a new version for every change, but only a person decides whether the change is breaking.

## Changing the API: the checklist

1. **Find the entry.** Search `bardic/apispec/` for the path. For a new route, add an `op(...)` to the matching family module, or add a family (register it in `FAMILIES`, and its tag in `TAGS`, in `spec.py`).
2. **Classify the change** using the [versioning rules](../contract/CHANGELOG.md#versioning-rules): additive or breaking. A breaking change needs a stated reason and a note on what clients must change. Prefer an additive alternative, such as a new field alongside the old one, followed later by a deprecation.
3. **Change the code and the description together:**
   - **Response:** add, change or remove the `View` fields, each with a `Field(description=...)` that states its meaning, units, format and what null or absence means. Bookkeeping that clients should not use is declared with `internal(...)`.
   - **Request:** add or change the DTO field with its `Field` validation, and describe it in the family's `REQUEST_DOCS`.
   - **Errors:** list every new status in `errors`, with when it happens.
   - **Behavior:** update the description. Say whether it queues a job, whether it can charge (`cost`), and any caching, idempotency, preconditions or side effects.
4. **Test it.** At least one test must receive the operation's 2xx response. Test any error status a client might act on. Tests are offline, use `tmp_path`, synthetic text and mocked providers ([AGENTS.md](../AGENTS.md)).
5. **Regenerate:** `uv run --frozen python -m bardic.apispec`. Read the diff of `contract/openapi.json` and `contract/API-REFERENCE.md`. It should contain exactly the change you intended.
6. **Record it.** Every change to `openapi.json` needs a new version, including documentation-only changes:
   - bump `VERSION` in `bardic/apispec/spec.py`: patch for additive, minor for breaking while 0.x (see [the rules](../contract/CHANGELOG.md#versioning-rules));
   - add a `## <version> — <YYYY-MM-DD>` entry at the top of `contract/CHANGELOG.md` that describes the change for a client developer, marking breaking entries **BREAKING**;
   - regenerate. The generator writes the entry's `contract-sha256` line.
   If regeneration says the contract changed after the version was recorded, you skipped this step.
7. **Update the prose.** Update `docs/API.md` only if a cross-cutting concept changed. Update the UI's use of the field in the same change, if it uses it.
8. **Run the full suite:** `uv run --frozen pytest -q`. Also run the Node tests if the UI changed.
9. **Check code generation:** `npm ci` once, then `npm run contract:codegen`. It generates TypeScript types with openapi-typescript and compiles them strictly. It also asserts a few guarantees, including invalid values that must not compile. Its pytest wrapper skips when the tools are not installed, so run it explicitly for an API change.

## Rules

- **The contract changes in the same commit as the code.** Never leave `contract/` stale, and never regenerate it in a separate cleanup commit.
- **Do not edit generated files.** To resolve a merge conflict in `contract/openapi.json` or `API-REFERENCE.md`, merge the Python sources, then regenerate.
- **Resolve changelog conflicts by renumbering.** Two branches that claim the same version conflict in `CHANGELOG.md`. Keep both entries, give the later change the next version, update `VERSION`, delete the stale `contract-sha256` line, and regenerate.
- **Always-sent fields have no default.** A field that is always present, even if it is null, is declared without a default so that the contract marks it required. Reserve defaults for fields that are absent in some cases, and say in the description when they are absent.
- **Avoid untyped fields.** Use `Any` only for genuinely arbitrary JSON, and say why in the description.
- **Operation IDs are permanent names.** Generated clients use them as method names. Renaming one is a breaking change. Python handler names do not matter.
- **Describe reality.** A view documents what the server sends, including awkward or duplicated fields. Improving a shape is a separate, versioned change.
- **Enumerations are open.** Adding a value is additive, and clients must tolerate unknown values. Declare `Literal` values from the code, not from what the tests happen to produce.
- **Declare cost honestly.** Anything that can queue or send a billed provider request is `may_charge`. Contacting a provider or self-hosted server without billed generation is `network`.
- **Keep private data out of the contract.** Descriptions and examples use synthetic text. They never contain keys, library content, personal paths or account data.
- **Keep views descriptive, not enforcing.** Views are not applied at runtime. Handlers return dictionaries, and the tests check them. Enforcing a view at runtime (`response_model`) would filter fields and turn stored-data mismatches into 500 errors, so it is a deliberate later step per route.

## Review checklist

A reviewer of a change that touches routes should ask:

- Does `contract/openapi.json` change exactly as intended, with no accidental removal? Is the changelog entry classified correctly?
- Would a client developer who cannot read Python understand the new behavior from the operation description alone?
- Is every new field's meaning, unit and nullability stated? Are internal fields marked `internal`?
- Is there a test that produces each new response shape and relied-on error?
- Does a new `may_charge` operation respect the cost rules in [AGENTS.md](../AGENTS.md)?

## Clients and the future server

- **Dedicated clients** generate their API layer from `contract/openapi.json`. They should pin its version and follow the compatibility rules in its `info.description`.
  - The server has no authentication. Browser clients on another origin cannot write, because of the write guard, and there is no CORS support. A non-browser client sends no `Origin` header, so it can write from any address that can reach the port.
  - Authentication and CORS are prerequisites for off-origin or remote clients, and are not designed yet (see [the proposal](CLIENT-SERVER-CONTRACT.md)).
- **A replacement server** must accept every request and produce every response that `openapi.json` describes, with the same status codes and semantics. The current pytest suite exercises the Python app in process. A black-box conformance suite over HTTP, runnable against any base URL, is the planned acceptance test for a port. See [the proposal](CLIENT-SERVER-CONTRACT.md).
