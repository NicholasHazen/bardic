# API guide

This guide explains the concepts that apply across Bardic's HTTP API: transport and security, conventions, errors, jobs, cost and compatibility. It does not list routes.

## Where the contract is

| Document | Use it for |
| --- | --- |
| [`contract/openapi.json`](../contract/openapi.json) | The **normative** OpenAPI 3.1 contract. It covers every operation, parameter, request and response field, error status and cost class. Generate client code from it. A replacement server must satisfy it. |
| [`contract/API-REFERENCE.md`](../contract/API-REFERENCE.md) | The same contract rendered for reading: each operation's behavior, fields and errors, and every schema. |
| [`contract/CHANGELOG.md`](../contract/CHANGELOG.md) | The contract's version rules and change history. |
| [API workflow](API-WORKFLOW.md) | How a change to the API updates the contract, and what the tests enforce. |
| [API known issues](API-KNOWN-ISSUES.md) | Defects and inconsistencies the contract documents as they are, pending a keep-or-fix decision. |

The contract is generated from the server's request DTOs and from the descriptions in [`bardic/apispec/`](../bardic/apispec/). The test suite checks every API response it receives against it. A running server serves the same document at `/openapi.json`; the Swagger and ReDoc pages are disabled. Examples in this guide assume the default `http://127.0.0.1:8765`. Use your isolated development server's port when testing ([development](DEVELOPMENT.md)).

The semantics behind several families are explained in their own guides:

- [analysis pipeline](ANALYSIS-PIPELINE.md): steps, versions, acceptance and rollback;
- [provider setup](ANALYSIS-PROVIDERS.md) and [account checks](ACCOUNT-CHECKS.md);
- [classic chapter analysis](CHAPTER-ANALYSIS.md): resume and authority rules;
- [artifacts and storage](ARTIFACTS-AND-STORAGE.md), including exports;
- [book structure](STRUCTURE.md);
- [library, listening and resources](LIBRARY-LISTENING-RESOURCES.md);
- [troubleshooting](OPERATIONS.md#troubleshooting).

The reference groups operations by family:

| Family | What it covers |
| --- | --- |
| [System](../contract/API-REFERENCE.md#system) | Status, settings, provider catalogs, explicit provider and Breeze checks |
| [Jobs](../contract/API-REFERENCE.md#jobs) | Listing and cancelling background jobs |
| [Diagnostics](../contract/API-REFERENCE.md#diagnostics) | Allowlisted playback troubleshooting events |
| [Library](../contract/API-REFERENCE.md#library) | Import, the library snapshot, metadata, covers, removal and restoration, audiobook export |
| [Series](../contract/API-REFERENCE.md#series) | Membership, volume placeholders, cross-book identities, collection runs |
| [Books](../contract/API-REFERENCE.md#books) | The book document and manual edits |
| [Pronunciations](../contract/API-REFERENCE.md#pronunciations) | Per-book respellings sent to narrators, with where each word occurs |
| [Classic analysis](../contract/API-REFERENCE.md#classic-analysis) | The older phase-based analysis, its plan preview and local preprocessing |
| [Analysis pipeline](../contract/API-REFERENCE.md#analysis-pipeline) | Step settings, previewed runs, versioned results to accept or reject |
| [Inspection](../contract/API-REFERENCE.md#inspection) | Stages, artifacts, the story map, passage search, resource usage, the analysis export |
| [Narration](../contract/API-REFERENCE.md#narration) | Enhanced (cast) takes and their audio |
| [Listening](../contract/API-REFERENCE.md#listening) | Simple single-narrator listening for passages and chapters |
| [Performances](../contract/API-REFERENCE.md#performances) | Saved named selections over retained audio |
| [Voice previews](../contract/API-REFERENCE.md#voice-previews) | Short explicit voice auditions |
| [Voices](../contract/API-REFERENCE.md#voices) | The library-wide voice library, drafts and Breeze clones |

## Transport and security

- **Binding.** The server binds to loopback by default. `BARDIC_LAN_NAME` opts into network binding and adds its `.local` name to the trusted hosts. `BARDIC_ALLOWED_HOSTS` adds exact names. See [operations](OPERATIONS.md).
- **No authentication.** There is no account or token layer, including on the network. Anyone who can reach the port is the owner.
- **Trusted hosts.** A request with an unexpected `Host` header is rejected before any route runs, with status 400 and the plain-text body `Invalid host header`. Apart from this and unexpected server errors (500, which have plain-text bodies), error bodies are JSON.
- **Write guard.** A write (anything other than GET, HEAD or OPTIONS) is rejected with 403 `{"detail":"Cross-origin writes are not allowed"}` in either of two cases: the browser sends `Sec-Fetch-Site: cross-site`, or `Origin` names a host other than `Host`. A request without an `Origin` header, such as from `curl` or a native client, is accepted. There is no CORS support, so a browser page served from another origin cannot write.
- **No caching, except covers.** Every `/api/` response carries `Cache-Control: no-store`, plus `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`. The exception is a successful cover image (`getBookCover`): it has a strong `ETag`, answers a matching `If-None-Match` with 304, and may be cached indefinitely at its content-addressed `?v=` URL.
- **Returned URLs.** Media URLs returned in responses (takes, clips, covers, auditions) are root-relative. Resolve them against the base URL you called.

Serving a client from another origin, or reaching the server beyond a trusted network, needs authentication, CORS and a threat model that do not exist yet. See [the client/server proposal](CLIENT-SERVER-CONTRACT.md).

## Conventions

- **Content types.** JSON writes use `Content-Type: application/json`. Book import and Breeze voice cloning use multipart form data.
- **Request bodies.** Unknown fields are rejected with 422. An omitted field takes its documented default. An explicit `null` has meaning only where the field allows it.
- **IDs.** IDs are opaque. URL-encode them, and obtain book, chapter, passage, character, series, job, session, artifact and voice IDs from responses. The diagnostics endpoint is the exception: it validates ID formats.
- **Passage and segment** name the same source-level reader unit.
- **Source offsets** (`start`, `end`) are chapter-local, zero-based Python Unicode code-point offsets with an exclusive end. They are not UTF-8 byte offsets or JavaScript UTF-16 indices. Canonical chapter text never changes.
- **GET routes never start paid generation.** Some build local caches or indexes, record local resource measurements, or record analysis-pipeline decisions. The reference says so on each such operation.
- **Private data.** Do not commit a captured status or other response, and do not put keys, private prose or account data into examples or fixtures. Use placeholder IDs and synthetic text.

To inspect the live contract of a running server:

```sh
curl --fail --silent http://127.0.0.1:8765/openapi.json
```

## Errors

An error body is JSON `{"detail": ...}`, except for the trusted-host rejection above. `detail` is an English sentence, or for 422 request validation a list of `{loc, msg, type, input, ctx}` issues. The diagnostics endpoint never echoes input; it returns the string `Invalid diagnostic event fields.` instead. Display `detail`, but do not parse it. There are no machine-readable error codes yet. Each operation in the reference lists the statuses it can return, and when.

| Status | Typical meaning |
| --- | --- |
| `400` | The domain operation is invalid: unsupported model, missing key or device capability, stale series plan, archived target. The trusted-host rejection is also a 400, in plain text. |
| `403` | A cross-origin or cross-site write was rejected. |
| `404` | Missing book, item, job, session, artifact or voice; wrong book scope; unavailable audio. |
| `409` | Busy book, conflicting in-flight operation, stale pipeline fingerprint or revision, or settings changed during a check. |
| `413` | The uploaded file exceeds the import limit. An oversized import is refused before its body is read. |
| `422` | Missing required field, wrong type, forbidden extra field, or a violated validation bound. |
| `429`, `502`, `503` | Provider quota, provider failure, or a busy or unavailable self-hosted server or worker, where the operation documents them. |

Validation of a provider's output is separate from HTTP status. HTTP 200 from a provider does not prove that its structured output passed evidence validation: check the pipeline attempt's `validation_state` and retained events.

## Jobs

Long work is queued as a durable job, and the response returns immediately with the job. **A queued job is not a result.**

- **Following a job.** Poll `GET /api/jobs` (optionally with `book_id`) and select your job by `id`. There is no single-job route; series runs also have their own runs endpoint. `active=true` returns every queued or running job with no bound. `./bardicctl` relies on it, and on the response being a bare list.
- **Statuses.** `queued` and `running` are active. Terminal statuses are `completed`, `failed`, `cancelled`, `interrupted`, `budget_limited`, and `quota_limited` (with `resume_after`). Worker failures, cancellations and allowance stops appear in the job while polling still returns HTTP 200.
- **Progress.** `progress`/`total` are in units specific to the job's `kind`. They are not a universal percentage.
- **Cancellation.** Cancellation is cooperative. A queued job cancels before starting. A running job receives a flag and stops at the next safe boundary. A remote request that was already sent can still complete, and be billed, after cancellation. Validated outputs and completed audio are retained.
- **Restart.** On a server restart, incomplete jobs become `interrupted`. To resume, call the relevant analyze, render, listen, pipeline or process operation again. Do not try to revive an old job ID.

The `Job` schema in the reference documents every job field, and which kinds set it.

## Cost

Each operation carries `x-bardic-cost`:

- `none`: no outbound request.
- `network`: contacts a provider or self-hosted server without billed generation, such as a model inventory, health check or voice list.
- `may_charge`: may send billed provider requests, directly or through the job it queues.

Rules that apply to every `may_charge` operation:

- **The analysis allowances bound analysis only.** An analysis run's allowances (requests, tokens, estimated dollars) are reserved before each paid attempt. They are not a global spending cap and do not limit narration.
- **Cost figures are estimates.** Missing usage or pricing is reported as unknown, never as zero. Estimates are dated and are not provider invoices or available credit.
- **Account checks spend a little.** An explicit account check sends a tiny billed request. It reports access, not a balance.
- **Paid runs need authorization.** Loading a key does not authorize a paid run. See [AGENTS.md](../AGENTS.md) for the development cost rules.

## Compatibility for client authors

- **Ignore unknown response fields.** New fields are added without notice.
- **Treat enumerated values as open sets.** Handle an unknown status, kind or state gracefully.
- **Avoid internal fields.** Fields marked `x-bardic-internal` are storage bookkeeping (edit tracking, fingerprints, server paths) that reach the wire today. Do not depend on them; a later version may remove them.
- **Operation IDs are stable.** Renaming one is a breaking change.
- **Generate with defaults optional.** A request field with a documented default may be omitted. Configure generators accordingly: `defaultNonNullable: false` for openapi-typescript, which `npm run contract:codegen` uses and verifies. Response schemas carry no defaults; `required` states which response fields are always present.
- **Pin the contract version.** `info.version` follows the rules in [`contract/CHANGELOG.md`](../contract/CHANGELOG.md). Pin it, and read the changelog before upgrading.
