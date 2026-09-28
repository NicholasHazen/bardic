# API known issues

Tracked in [issue #17](https://github.com/NicholasHazen/bardic/issues/17). Recorded September 28, 2026, while the complete HTTP contract was written (contract 0.1.0). Describing every operation from the code surfaced these defects and inconsistencies. None of them was changed: the contract describes the current behavior, and each item that affects clients is also noted in its operation's description.

Each item needs a decision, and many would be a breaking contract change. Before a replacement server is built, decide for each whether it keeps the behavior or fixes it. When an item is fixed, remove it here, tick it in the issue, and record the fix in [the contract changelog](../contract/CHANGELOG.md). Line numbers refer to commit `1089a07` and may drift.

## Defects

| Area | Issue | Where |
| --- | --- | --- |
| Voices | After a failed Gemini voice refresh, the saved `voices: null` makes `GET /api/voices` and the refresh itself return a plain-text 500 until a later refresh succeeds or the key changes. | `bardic/voice_routes.py:305`, `:124`, `:199` |
| Voices | Deleting a library voice is not atomic. If one provider voice fails to delete, the earlier ones are already gone and the library voice is not tombstoned. | `bardic/voice_routes.py:359-373` |
| Voices | Draft save and Breeze clone upload the server voice before the library record is validated or created. A failure afterwards (blank name, deleted base voice, unknown book or character) leaves an orphaned server voice, which a later Breeze check imports. | `bardic/voice_routes.py:642-660`, `:720` |
| Voices | Single-voice responses never compare against provider checks, so their `server_state` is always `unknown` or `other_project`. Only `GET /api/voices` reports real states. | `bardic/voice_routes.py:330,340,681,728` |
| Narration | `POST /render` does not check for shutdown. A pool submission during shutdown is an undocumented 500; the listening routes return 503 in the same case. | `bardic/app.py:843` |
| Listening | The chapter-start 429 covers only an in-process daily block, not a library already over its daily request limit. That case is accepted, and the job then ends `quota_limited`. | `bardic/app.py` (chapter listen start) |
| Listening | `cache_hit` is meant to be transient but is persisted into the job's `audio`. | `bardic/app.py` listen job update |
| Settings | Any `POST /api/settings` resets every in-process Gemini daily quota block. `tts_limits` replaces a model's limits as a whole, so omitted limits reset to their defaults. | `bardic/app.py:1051,1073`, `bardic/tts_limits.py:95-100` |
| Settings | A `BREEZE_TTS_URL` from the environment is persisted to the library on the next settings save. | `bardic/app.py:299,1071` |
| Diagnostics | Passage, session or job IDs sent without a `book_id` pass validation, then are silently dropped (`recorded:false`) instead of being rejected. The custom 422 message is also applied to a bad `limit` on the GET. | `bardic/app.py:923,936`, `bardic/diagnostics.py:47` |
| Library | `POST /books/{id}/refresh-metadata` and `POST /books/{id}/repair-structure` write a resource-ledger row before checking that the book exists, even for unknown IDs. | `bardic/app.py:1132,1500` |
| Library | A metadata edit locks both title and author against refresh, even when only one changed. | `bardic/library.py` `update_book` |
| Library | `audio_count` counts stored takes that are no longer current, so it can exceed the number of playable passages. | `bardic/library.py` |
| Library | The cover `ETag` is not quoted, `If-None-Match` is ignored, and the middleware overrides the route's `Cache-Control` with `no-store`, so the content-hash `?v=` does not help caching. | `bardic/app.py:906-909,1504-1510` |
| Library | The 413 size check runs after the whole upload is received. A failed save after the original is written leaves an orphaned `originals/{id}/` directory. | `bardic/app.py:1089-1103` |
| Books | Any edit request, even an empty or unchanged one, marks the item edited and increments `revision`. | `bardic/app.py:1263` |
| Books | A blank Breeze voice ID falls back to the default voice, while Gemini and device store the blank. `VoiceChoice.seed` is ignored for library choices and non-Breeze providers. `SegmentEdit.seed` cannot be cleared. | `bardic/app.py:684`, `bardic/breeze.py:246` |
| Books | `addCharacter` never assigns a device voice, unlike imported characters. | `bardic/app.py` add character |
| Books | A book whose stored data has a dangling reference returns 404, via the global `KeyError` handler, instead of a server error. | `bardic/app.py:912-914` |
| Series | Most series routes on a removed series answer 404 "Series not found", so the intended 400 "Restore this series" is unreachable and archiving is not idempotent. | `bardic/app.py:1511`, `bardic/series_processing.py:13`, `bardic/library.py:127` |
| Series | Listing and creating series characters ignore removal, and creating one ignores an active series run. `restoreSeries` does not check for active series runs. | `bardic/app.py:1192-1198,1535` |
| Pipeline | An unknown step ID in a request body returns 404, not 400. Saved or default step settings are not revalidated, so an LLM step can run with a null model. A saved config that no longer validates falls back to defaults while `saved` stays true. | `bardic/pipeline/api.py:148-154,299-306,319,332` |
| Pipeline | Reject does not check that the book exists or is active. A `same_as_accepted` candidate cannot be rejected. | `bardic/pipeline/api.py:531-543` |
| Pipeline | The returned `run` is the object the worker mutates, so a response can be serialized mid-change. | `bardic/pipeline/api.py:392` |
| Jobs | A job cancelled while queued is settled again when its worker slot comes up. In a shutdown race, `cancelled` can become `interrupted`. | `bardic/app.py:697,714` |
| Inspection | Story-map `attributed_speaker` edges can point at a missing character node. | `bardic/pipeline_view.py:74` |
| Inspection | The analysis export dumps raw attempt rows, while the pipeline view filters them through an allowlist. | `bardic/pipeline_view.py:142,198` |

## Inconsistencies a client will notice

- **Status codes for the same condition differ:**
  - A stale series plan fingerprint and "series already running" are 400; the pipeline's stale fingerprint is 409 (`bardic/series_processing.py:74,80`).
  - Restoring a book with an active job is 400; archive, metadata and refresh return 409 for the same case.
  - Provider failures on voice deletion or Gemini refresh are 400, from the global `ValueError` handler, not 502.
  - Out-of-range artifact paging is 400; resources and search clamp instead.
- **The same concept has several shapes:**
  - Audio objects with a `url` are built in about seven places, with different optional fields (`bardic/app.py:649`, `bardic/listening.py:246,376`, `bardic/performances.py:269`, `bardic/voice_previews.py:190`, `bardic/voice_routes.py:136,169`).
  - Jobs appear in full and as two subsets.
  - Some fields have aliases: search `items`/`results`, library `segment_count`/`passage_count`, pipeline `configured`/`has_api_key`, and `Status.analysis_models`, which duplicates `model_catalogs.gemini`.
  - `Job.limits` has two shapes. `Job.mode` means serial/parallel for pipeline jobs and simple/cast for performances.
- **Internal data reaches the wire.** Examples: take fingerprints, recipes, synthesis keys and `resource_usage`; edit tracking; `data_directory`; census and context hashes; `process_id`. The contract marks each with `x-bardic-internal`. The audiobook export's `production.json` is the raw stored book.
- **Some GET routes write local state:**
  - census caches and artifacts (preprocessing, pipeline, and the plan POST);
  - the search index;
  - resource-ledger rows (search, export);
  - analysis-pipeline decisions (`GET /api/books/{id}/analysis-pipeline`);
  - a table initializer on each pipeline request.
- **Error bodies.** There are no machine-readable error codes, and many `detail` sentences refer to UI locations ("in Settings").
- **Ordering.** Library lists are ordered by most recent save, not by import time, because a save replaces the row.
