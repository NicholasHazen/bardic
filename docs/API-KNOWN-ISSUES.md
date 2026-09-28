# API known issues

This file records issues found in the HTTP contract, and the keep-or-fix decision for each one. Record new issues here before a replacement server is built: the port must know whether to reproduce a behavior or fix it.

## Resolved in contract 0.2.0 (issue #17)

Describing every route for contract 0.1.0 surfaced 28 defects and six groups of inconsistencies ([issue #17](https://github.com/NicholasHazen/bardic/issues/17)). The owner decided to fix all of them in one breaking release, contract 0.2.0. The [changelog](../contract/CHANGELOG.md) lists every client-visible change. A regression test covers each fixed defect.

### Defects

| Area | Issue in 0.1.0 | Resolution in 0.2.0 |
| --- | --- | --- |
| Voices | A failed Gemini voice refresh left `GET /api/voices` and the refresh answering a plain-text 500. | The refresh returns 502 `provider_error`. The listing returns 200 with `providers.gemini.state: "error"`. |
| Voices | Deleting a library voice was not atomic. | Each provider deletion is recorded as it happens. A partial failure is 502, and a retry resumes. |
| Voices | Draft save and Breeze clone uploaded before validating, which could orphan a server voice. | Everything is validated before the upload. A record failure after the upload removes the uploaded voice. One case remains: a request that times out after the server created the voice is not cleaned up. |
| Voices | Single-voice responses never compared against provider checks. | Every voice response uses the saved Breeze and Gemini checks, with no network call. |
| Narration | `POST /render` during shutdown gave an undocumented 500. | 503 `shutting_down`, and nothing is queued. |
| Listening | The chapter-start 429 ignored a library already over its daily limit. | 429 `daily_quota_reached` with `Retry-After`, and nothing is queued. |
| Listening | `cache_hit` was persisted into the job's audio. | Never stored. The POST envelope's `cached` reports it. |
| Settings | Every settings save lifted all daily quota blocks, and `tts_limits` replaced a model's limits as a whole. | A block is lifted only when that model's limits or the key change. `tts_limits` merges per field. |
| Settings | An environment `BREEZE_TTS_URL` was saved to the library. | Only values the client sends are saved. The environment value stays a runtime fallback. |
| Diagnostics | IDs without `book_id` were silently dropped, and the custom 422 applied to the GET. | 422 on the POST. The GET uses the standard 422 and clamps `limit`. |
| Library | Refresh-metadata and repair-structure recorded a ledger row before checking the book. | Preconditions come first, and a refused request records nothing. |
| Library | A metadata edit locked both title and author. | Only the fields that changed are locked. |
| Library | `audio_count` counted takes that were no longer current. | Counts current, playable takes, on every route. |
| Library | The cover `ETag`, `If-None-Match` and caching were broken. | Quoted strong `ETag`, 304, and `immutable` at the content-addressed URL. |
| Library | The 413 check ran after the whole upload, and a failed save left an orphaned original. | Refused before reading, and cleaned up on any failure. |
| Books | Any edit, even an empty one, marked the item edited and bumped `revision`. | A no-op edit saves nothing, and a real edit marks only the changed fields. The Classic engine still treats any edited item as reviewed (see Kept). |
| Books | Blank voice IDs and `seed` behaved differently per provider, and a passage seed could not be cleared. | One rule for every provider: blank clears the choice, an inapplicable `seed` is 400, and `seed: null` clears it. |
| Books | `addCharacter` never assigned a device voice. | Assigned the same way import does. |
| Books | Character references went stale after manual edits and pipeline acceptance. | Derived from the current book on every read. |
| Books | A dangling stored reference answered 404. | 500 `internal_error`. Only a resource named by the request can be 404. |
| Series | A removed series answered 404, and archiving was not idempotent. | 409 `series_archived` for changes, reads still work, and archive and restore are idempotent. |
| Series | Series characters and restore ignored removal and active runs. | 409 `series_archived` or `series_run_active`. |
| Pipeline | An unknown body step was 404, settings were not revalidated, and `saved` was misleading. | 400 `unknown_step`, 400 `step_model_missing`, and `saved: false` with `saved_invalid`. |
| Pipeline | Reject skipped the book check, and a `same_as_accepted` candidate could not be rejected. | Both are checked, and rejecting that candidate declines it. |
| Pipeline | The returned `run` was the object the worker mutates. | A snapshot is returned. |
| Jobs | A job cancelled while queued was settled again, and could become `interrupted`. | A terminal status is final at the storage level. |
| Inspection | Cancelled stages showed `interrupted`, and story-map edges could dangle. | Cancelled Classic runs show `cancelled`, and edges always end at a node. |
| Inspection | The analysis export dumped raw attempt rows. | One allowlist for the inspector and the export (export `schema_version: 2`). |

### Inconsistencies

| Issue in 0.1.0 | Resolution in 0.2.0 |
| --- | --- |
| Status codes differed for the same condition. | One meaning per status, stated in the contract introduction and [API.md](API.md#errors). State conflicts are 409, unknown body IDs 400, provider failures 502, shutdown 503, and paging clamps. |
| The same concept had several shapes. | One audio core (`AudioRef`, built by `bardic/audio_refs.py`), one `Job`, unambiguous job fields, and no aliases. |
| Internal data reached the wire. | Every `x-bardic-internal` field is removed. The export's `production.json` is the presented book. |
| Some GET routes wrote local state. | GETs write no records. Only disposable caches (census, search index) may be written. Schema setup and legacy retention run at startup. |
| Error bodies had no machine-readable codes and named UI locations. | Every error has a documented `code`, and `detail` describes the condition. |
| Library lists were ordered by the most recent save. | Ordered by import time. A save keeps its row. |

## Kept, by decision

- **Classic analysis treats an edited item as wholly reviewed.** A real manual edit still stops the Classic engine from changing that item, even though `edited_fields` now names the changed fields. The owner decided to remove the Classic engine (2026-09-28), so per-field locking is not built for it. The step pipeline uses its own review state.
- **Provider-state checks return 200.** Account checks, model refresh and Breeze refresh report a provider's failure inside a 200 body, because reporting that state is their purpose.
- **Pipeline history is captured at write time, not by viewing.** Before 0.2.0, opening the Analysis tab (a GET) recorded the current projection as a pipeline version. Now every outside writer (Classic analysis, series runs, structure repair and manual edits) records the projection it replaces before it writes. States within a single run, between its chapter stages, are not captured.

## Open

None known. Record new issues here with a keep-or-fix decision.
