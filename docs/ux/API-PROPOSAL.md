# API proposal for the redesign

Status: **proposal, 2026-09-30.** Nothing here is implemented. `contract/openapi.json` (0.5.0) is unchanged. Each part is written so it can be turned into `bardic/apispec/` entries, following [API-WORKFLOW.md](../API-WORKFLOW.md), without further design.

The redesign (Library → Book page → Now Playing, see [FOUNDATIONS.md](FOUNDATIONS.md)) makes four assumptions (plus a profile decision that scopes the first) the backend does not meet today. This document defines each as a contract change, and records what to leave client-local.

| # | Design assumption | Today | Proposal | Contract bump |
|---|---|---|---|---|
| P | Several listeners share one library without sharing places; a dropdown, no auth | No notion of a listener | `Profile` resource + optional `X-Bardic-Profile` header | additive, 0.5.1 |
| A | Place and finished state follow the book across devices; Library shows "Continue" and progress | `bardic:progress:<bookId>` and `bardic:lastBook` in `localStorage` | Coarse server checkpoint per profile and book + `checkpoint` on `LibraryBookSummary`; granular playback time stays on the client | additive, 0.5.2 |
| B | Aurora palette derives from the cover, without decoding it in the browser on every render | Client would have to fetch and sample every thumbnail | `sample` colour on `LibraryBookCover` (computed once, stored) | additive, 0.5.3 |
| C | Importing a book already in the library warns instead of silently creating a copy | `importBook` is "not deduplicated" by contract | `source_sha256` on books + `findDuplicateBooks` | additive, 0.5.4 |
| D | Book page shows per-chapter "Saved audio" for the chosen narrator | Client must page `listListeningTakes` and derive coverage itself | `getBookAudioStatus` (read-only, per chapter) | additive, 0.5.5 |

All four are additive (new operations, new always-sent response fields, no changed meaning), so each is a patch bump under the 0.x rules. Ship them as separate commits in the order above; P then A unblock the most UI. Every new operation has cost class none (no `x-bardic-cost`): none contacts a provider.

Shared conventions: operation IDs are permanent, so the names below are the ones to keep. Wire names are snake_case. Always-sent fields have no default. Errors use the existing `Error {detail, code}` shape with the status classes in `bardic/errors.py`; `book_not_found` (404) and `book_archived` (409) are reused, not redefined.

---

## P. Profiles (prerequisite for A)

### Decision

Bardic gains **profiles**: named listeners who share one library and keep their own place in each book. Choosing one is a dropdown, with **no password, PIN or authentication**. This matches the app's model (loopback or trusted LAN, browser write-origin guard). A profile is a *bookmark namespace*, not a security boundary: any client can pick any profile, and the UI must not describe profiles as private or protected.

Profile-scoped state (this proposal): book checkpoints (part A). Anything else that later becomes per-listener (preferences, reading lists) joins the same scope.

Not profile-scoped, on purpose: the library and books, cast and voices, takes, performances, audio, provider keys, analysis budgets and every paid action. Everyone on the server spends the same allowance, and the cost rules (estimate, confirm, unknown is not zero) are unchanged. There are no per-profile permissions.

### Model

```
Profile { id: string, name: string, created_at: string, last_used_at: string | null }
```

`last_used_at` is the newest checkpoint write by that profile (null if none), so the picker can sort by recency.

| ID | Route | Purpose |
|---|---|---|
| `listProfiles` | `GET /api/profiles` | All profiles, oldest first. |
| `createProfile` | `POST /api/profiles` `{name}` | Add one; 201 with `Profile`. |
| `renameProfile` | `PATCH /api/profiles/{profile_id}` `{name}` | Rename. |
| `deleteProfile` | `DELETE /api/profiles/{profile_id}` | Delete the profile and **its checkpoints only**. 204. |

Rules: names are trimmed, 1 to 40 characters, unique case-insensitively. Errors: 400 `profile_name_invalid`, 409 `profile_name_taken`, 404 `profile_not_found`, 409 `last_profile` (the final profile cannot be deleted, so a library is never without one). Deleting a profile never touches books, audio or other profiles. The UI should confirm and say what is lost (their places and finished marks).

A fresh or upgraded data directory gets one profile named `Listener` (id opaque, generated), created idempotently at startup by the migration. The demo book and every existing book remain visible to it.

### Selecting a profile

The server holds **no "current profile"**, because two devices may be different people at the same moment. Each request names its profile with an optional header:

```
X-Bardic-Profile: <profile_id>
```

- Declared as an optional header parameter on every profile-scoped operation: `listBooks`, `getLibrary` (their `checkpoint` field depends on it), and all checkpoint operations. Adding an optional parameter is additive.
- Missing header: the oldest profile is used, so existing clients and scripts keep working unchanged.
- Unknown ID: 404 `profile_not_found`, and the client falls back to the picker.
- The browser remembers its choice in `localStorage` (`bardic:profile`) per device and sends the header on every request. A first visit with more than one profile shows the picker before Library.
- CORS: the header must be added to the allowed request headers in `bardic/cors.py` (for the loopback/LAN origins it already allows).

### Storage

`profiles(id TEXT PRIMARY KEY, body TEXT NOT NULL)`, created idempotently in `Store` init with a `profile_version: 1` in the body. Excluded from the `spintails-analysis` export. Included in the recoverable backup (it is in the database).

### Tests

CRUD and the validation errors; default fallback with no header; unknown header 404; last-profile refusal; deleting a profile removes only its checkpoints; a fresh directory and an upgraded one both end with exactly one profile; header accepted by CORS preflight.

---

## A. Book checkpoints

### Model: coarse on the server, granular on the client

A **checkpoint** answers "where was this profile in this book, last time, on any device, and is the book finished". It is a *current projection* per profile and book (AGENTS.md: not history, not a cache, not an artifact). Moving it never touches canonical text, takes or performances. The current checkpoint is the truth; a short bounded history of earlier ones is kept for undo (below).

The split:

| Server (checkpoint, shared across devices) | Client (granular, per device) |
|---|---|
| Chapter, passage, fraction through the passage, text offset | Exact audio `currentTime` for the take being played |
| Book progress, finished state, mode, narrator source | Scroll position, reader appearance, speed, sleep timer |
| Written on events and at most every 30 s while playing | Written continuously to `localStorage` |

The durable server coordinate is the canonical text offset (zero-based Unicode code points into the chapter, the same coordinates as spans). It is deliberately not seconds: seconds differ per narrator and speed, and a reader-only position has no audio. The wire also carries the passage and a fraction through it, which any player can seek to on any source.

```
BookCheckpoint {
  chapter_id: string            // resolved from the stored offset
  passage_id: string            // passage containing char_offset now
  passage_fraction: number      // 0.0 <= x < 1.0, position inside that passage's text
  char_offset: integer          // stored anchor: code points from the start of the chapter text
  progress: number              // 0.0..1.0 of the narrative text before the position, by code points
  finished: boolean             // see "Finished" below
  finished_at: string | null    // ISO 8601 UTC; when it became finished (see rules)
  finished_reason: "marked" | "reached_end" | null   // open enumeration
  mode: "listening" | "reading" // how the reader last moved it; open enumeration
  source: ListenSource | null   // narrator the reader was using; null when reading only
  device_id: string | null      // opaque, client-generated, echoed back
  updated_at: string            // ISO 8601 UTC, set by the server
}

ListenSource  (named oneOf, discriminator `kind`; open enumeration)
  { kind: "session",     session_id: string }
  { kind: "cast" }                              // full-cast enhanced takes
  { kind: "performance", performance_id: string }
```

`progress` is computed over narrative chapters only, so front and back matter do not distort it. `ListenSource` mirrors what `listListeningTakes` and the performance routes already address, so "resume with the same narrator" needs no new identity. `session_id` is opaque and browser-minted; the server stores it verbatim and does not validate it (open question 1).

### Operations

All are profile-scoped through the `X-Bardic-Profile` header.

| ID | Route | Purpose |
|---|---|---|
| `getBookCheckpoint` | `GET /api/books/{book_id}/checkpoint` | Current checkpoint. |
| `setBookCheckpoint` | `PUT /api/books/{book_id}/checkpoint` | Replace the position (idempotent). |
| `setBookFinished` | `PUT /api/books/{book_id}/checkpoint/finished` | Mark finished or reopen. |
| `listBookCheckpointHistory` | `GET /api/books/{book_id}/checkpoint/history` | Earlier places, newest first. |
| `clearBookCheckpoint` | `DELETE /api/books/{book_id}/checkpoint` | "Mark as not started". |

`getBookCheckpoint` returns 200, or 404 `checkpoint_not_found` when this profile never opened the book (the typed form of "no checkpoint"; lists use the nullable field below).

`setBookCheckpoint` request (each field gets a `REQUEST_DOCS` entry):

```
{ chapter_id: string, passage_id: string, passage_fraction: number,
  mode: "listening" | "reading", source?: ListenSource | null, device_id?: string | null }
```

- The server converts `passage_id` + `passage_fraction` to `char_offset`, validating that the passage belongs to `chapter_id` and this book, and returns 200 with the stored `BookCheckpoint`.
- **Last write wins per profile and book.** `updated_at` is set by the server; a client-supplied time is not accepted, so a wrong device clock cannot pin the place. `device_id` is informational.
- **`updated_at` moves only when the checkpoint actually changes.** A write that stores the same `char_offset`, `mode` and `source` as the current checkpoint changes nothing and returns it as is. So a client heartbeat while paused on the same word does not restart the finish clock or reorder Continue.
- Errors: 404 `book_not_found`, `profile_not_found`; 409 `book_archived`; 400 `checkpoint_invalid` (chapter not in book, passage not in chapter, fraction outside `[0, 1)`), 400 `invalid_request`.

`setBookFinished` request `{finished: boolean}`:
- `true`: marks the book finished with `finished_reason: "marked"`, keeps the current place, or when there is no checkpoint creates one at the end of the last narrative chapter.
- `false`: reopens it (`finished: false`, place kept). This is a change, so it bumps `updated_at` and restarts the auto-finish clock if the place is still at or past 98%.

`clearBookCheckpoint` returns 204 and is idempotent. It removes only this profile's current checkpoint for the book.

### Finished

A book is finished for a profile when **either**:
1. **Marked** by that profile with `setBookFinished(true)`, immediately; or
2. **Left at the end**: `progress >= 0.98` and the checkpoint has not changed for more than **24 hours** (`finished_reason: "reached_end"`, `finished_at` = `updated_at` + 24 hours).

**Any change resets the clock.** A change is any write that alters the stored `char_offset`, `mode` or `source`, `setBookFinished(false)`, or a mark. Rule 2 therefore needs no extra stored state: `finished` is computed at read time from `progress` and `updated_at`, so there is no background job and no clock to drift. A replay of the last chapter restarts the day, and so does moving below 98%.

A *marked* finish is also undone by any change of place: listening again means the book is not finished. It is otherwise cleared by `setBookFinished(false)` or `clearBookCheckpoint`.

Consequence for the design: a book at 98 to 100% stays in Continue for a day so the last chapter can be finished, replayed or marked done. The UI can show "Almost done" from `progress >= 0.98 && !finished`. The "Done" status and Finished shelf use `finished`. Player states such as "The end of the book" remain a player concern and do not change library status by themselves.

### Checkpoint history

For "undo a mis-tap" and "where was I yesterday". The server keeps at most **10 earlier checkpoints per profile and book**, dropping the oldest.

- When a write changes the checkpoint, the *previous* checkpoint is pushed to history if it was **at least 30 minutes old** (a new sitting) or the new place is a **jump of 2% progress or more** in either direction (a seek or a chapter jump). Ordinary listening therefore does not flood the list, and a mis-tap seek is always recoverable.
- `listBookCheckpointHistory` returns `{items: [BookCheckpoint]}`, newest first, excluding the current one. `finished` is not evaluated for history items (always false, `finished_at` null) because they are past places.
- There is no restore operation: the client restores by sending an item's `chapter_id`, `passage_id` and `passage_fraction` to `setBookCheckpoint`, which is itself a recorded move, so a restore can be undone too.
- `clearBookCheckpoint` removes history as well. Errors: 404 `book_not_found`, `profile_not_found`; an empty list is 200.

### Library integration

Add to `LibraryBookSummary` (always sent, nullable, per requesting profile):

```
checkpoint: BookCheckpoint | null   // null: never opened, or cleared
```

The Continue row is `checkpoint != null && !checkpoint.finished`, sorted by `checkpoint.updated_at` descending; progress bars use `checkpoint.progress`. No separate endpoint is needed. Populating costs one indexed lookup per book.

### Storage and lifecycle

- `book_checkpoints(profile_id TEXT NOT NULL, book_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(profile_id, book_id))`, with `checkpoint_version: 1` in the body; the body holds the current checkpoint and its bounded history list. Created idempotently; writes are short Store-lock transactions, never held across requests.
- Archiving a book keeps checkpoints (restore resumes). Excluded from the analysis export.
- A structure repair that changes passage boundaries still resolves the stored offset to the containing passage. If the chapter is gone, the response resolves to the first passage of the nearest earlier surviving chapter (open question 2).

### Client behaviour

- **Local granular first.** Playback continuously updates `bardic:progress:<bookId>` (chapterId, segmentId, exact `currentTime`, plus the `updated_at` of the last server checkpoint this device wrote). This is what makes same-device resume exact and instant, and offline.
- **Server checkpoints on events**, not on a tight timer: pause, chapter change, seek release, closing the book, `visibilitychange` to hidden (use `fetch(..., {keepalive: true})`; `sendBeacon` is POST only), and at most every 30 s while playing. A failed write is silent and retried at the next event.
- **Resume decision on opening a book:** fetch the checkpoint. If the local record exists and carries the server's current `updated_at`, this device made the last move, so use the local exact time. If the server checkpoint is newer or local state is absent, use the server's passage and `passage_fraction`, and show the "Continue from your other device" prompt when `device_id` differs from ours.
- **Migration:** if the server returns `checkpoint_not_found` for the chosen profile and a local `bardic:progress:<bookId>` exists, PUT it once (segment as `passage_id`, fraction 0). `bardic:lastBook` is redundant once the summary carries `checkpoint`; read it as a fallback for one release.

### Tests

Contract test receiving a 2xx from every operation, and `LibraryBookSummary.checkpoint` both populated and null. Unit tests: offset round-trip through a passage split; invalid passage or chapter; archived book 409; two profiles with independent checkpoints for one book; finish by marking; auto-finish exactly at the 24 h boundary using an injected clock (not finished at 23 h 59 m, finished after 24 h; an identical write does not reset it; a write that changes the offset, even at 0.99, does; a write below 0.98 does); a marked finish is cleared by moving; `setBookFinished(false)` reopens and restarts the clock; history keeps 10, applies the 30-minute and 2% rules, and restores as a recorded move; `clear` idempotent; structure repair keeps the offset; concurrent PUTs serialize.

---

## B. Cover colour sample

### Model

Add an always-sent nullable field to `LibraryBookCover`, and to `BookCover` wherever that shape is served:

```
sample: CoverSample | null

CoverSample {
  hex: string          // "#rrggbb", the dominant chromatic colour of the thumbnail
  hue: number          // 0..360
  saturation: number   // 0..1
  lightness: number    // 0..1
  vivid: boolean       // false when the cover is effectively grey/monochrome
  version: integer     // algorithm version, currently 1
}
```

Null when the book has no cover (TXT, demo). The client then uses the design's coral/amber fallback. When `vivid` is false the client also uses the fallback, so a black-and-white cover does not produce a grey UI. The design system derives everything else (base, glows, accent with ≥ 7:1 contrast against the base) from `hue` and `saturation` in the client, so the palette rules can change without a contract change. That split is deliberate: the server owns a **measurement**, the client owns the **theme**.

### Algorithm (specified so all servers agree)

Computed from the stored 240×360 JPEG thumbnail bytes, not the original:

1. Downsample to at most 32×48, convert to HSL.
2. Discard pixels with lightness < 0.12 or > 0.92 or saturation < 0.18.
3. Bucket the rest into 24 hue bins weighted by `saturation × (1 − |lightness − 0.5|)` and a centre weight (edges count half).
4. `hue` is the weighted mean of the winning bin; `saturation` and `lightness` are its weighted means.
5. `vivid = (weight of kept pixels) >= 5% of all pixels`. Otherwise `sample` is still returned with `vivid: false` and the best-effort values.

Implementation uses Pillow, which the importer already depends on for thumbnails; no new dependency. Deterministic, no randomness, no network.

### Storage

Add nullable columns `sample_json TEXT` and `sample_version INTEGER` to `book_covers`, next to `sha256`. `persist_cover` computes it when it writes the thumbnail. Books that already have a cover get it lazily: `getLibrary` fills a missing or old-version sample from `body` on first read and writes it back inside a short Store transaction. Because it is derived only from the bytes named by `sha256`, a changed cover (`refreshBookMetadata`) recomputes it, and it is never a manual-edit surface.

### Tests

Fixture thumbnails generated in the test (solid red, teal gradient, grey, near-white) assert hue ranges and `vivid`; determinism; lazy backfill for a pre-existing cover row; `refreshBookMetadata` changes `sample` along with `sha256`; null for TXT.

---

## C. Duplicate detection on import

### Why not just fail `importBook`

`importBook` is documented as "not idempotent and not deduplicated" and users legitimately re-import (a fixed edition, a repaired file). Making the server refuse would be a breaking change and the `Error` shape cannot carry the existing book's ID. The design instead **warns before upload finishes and lets the user choose**: "Already in your library — Open existing / Import another copy".

### Model

Add to `LibraryBookSummary` and the `Book` document:

```
source_sha256: string | null    // SHA-256 (lowercase hex) of the originally uploaded bytes
```

Null for the demo and for books whose original is missing. It is computed at import from the same bytes already written to `originals/{book_id}/source.{ext}`. Existing books are backfilled lazily by hashing the retained original in a background thread the first time it is needed (not on library load), and cached in the book body. Books imported before the retained original existed stay null and can never produce an exact match; that is correct, not a guess.

New operation:

| ID | Route | Purpose |
|---|---|---|
| `findDuplicateBooks` | `GET /api/books/duplicates?sha256=…&title=…&author=…` | Find likely copies of a file before or after choosing it. |

```
DuplicateBooks {
  exact:   [DuplicateBook]   // same source_sha256
  similar: [DuplicateBook]   // same normalised title and author, different bytes
}
DuplicateBook { book_id, title, author, created_at, archived: boolean, cover: LibraryBookCover | null }
```

- `sha256` is required (64 lowercase hex chars), `title` and `author` optional. Title match is case- and punctuation-insensitive after trimming, and only runs when `title` is given. `similar` never includes anything already in `exact`.
- Includes removed (archived) books with `archived: true`, so the UI can offer **Restore** instead of a second copy.
- Read-only; errors: 400 `invalid_request` for a malformed digest. An empty result is two empty arrays, 200.
- Route ordering: `/api/books/duplicates` must be registered before `/api/books/{book_id}` so the literal segment wins; add a test that a book with ID `duplicates` cannot exist (IDs are opaque generated values, so this is a documentation note rather than a conflict).

### Client flow

The browser hashes the chosen file with `crypto.subtle.digest('SHA-256', …)` (files are ≤ 30 MiB, so this is quick and needs no chunking), calls `findDuplicateBooks`, and shows the design's duplicate sheet if `exact` is non-empty (and a lighter "similar title" note for `similar`). Only then does it `POST /api/books`. Nothing changes for other clients: they can ignore the endpoint, and `importBook` keeps working as documented. Update the `importBook` description bullet from "Not … deduplicated" to say that the server never blocks a duplicate and point to `findDuplicateBooks`.

### Tests

Exact match after import; archived match flagged; similar-title match; empty result; malformed digest 400; a re-import of the same file creates a second book (behaviour unchanged) with the same `source_sha256`; backfill of a book with a retained original; null for the demo.

---

## D. Saved-audio status

### Model

The Book page needs, per chapter, "how much of this chapter is already recorded for this listening source", to show Ready / Partial / Not yet, and the total for the book. Today that requires fetching every take and joining it against passages in the browser. Add one read-only summary. It is a **derived projection**: it reports what `listListeningTakes` (or the cast/performance equivalents) would return, and never a separate store.

| ID | Route |
|---|---|
| `getBookAudioStatus` | `GET /api/books/{book_id}/audio-status?source_kind=…&source_id=…` |

Query: `source_kind` is `session` | `cast` | `performance`; `source_id` is required for `session` and `performance` and must be absent for `cast`. Same identities as `ListenSource` in part A.

```
BookAudioStatus {
  source: ListenSource
  chapters: [ChapterAudioStatus]      // narrative chapters, in reading order
  passages_total: integer
  passages_ready: integer
  seconds_ready: number               // sum of playable take durations
  seconds_total: number | null        // null when any missing passage has no duration estimate
}

ChapterAudioStatus {
  chapter_id: string
  passages_total: integer
  passages_ready: integer             // takes that still match the current text and voice
  passages_stale: integer             // a take exists but its text or voice changed since
  seconds_ready: number
  state: "none" | "partial" | "complete" | "stale"   // open enumeration
}
```

- `state` is `complete` when every passage is ready, `stale` when nothing is ready but some passages have stale takes, `partial` when some are ready, `none` otherwise. Stale audio stays a separate count (AGENTS.md: rejected, cached, stale and unknown states remain distinct) and is never counted as ready.
- `seconds_total` is null rather than 0 when durations for the missing passages are unknown (unknown is not zero). The design shows "n of m chapters saved" from the passage counts, never from a guessed total.
- Errors: 404 `book_not_found`; 404 `listening_source_not_found` when the source ID does not exist for the book (for `performance`) — sessions cannot be checked (open question 1), so an unknown session ID returns a valid, all-`none` status; a removed (archived) book still returns 200, because reading its status is harmless. 400 `invalid_request` for a missing or inconsistent `source_id`.
- Cost: none, no provider. Each call resolves currency by checking take metadata against current passages, so it costs about as much as `listListeningTakes`; document that, and cache nothing server-side in the first version.

### Optional follow-up: `listBookAudioSources`

`GET /api/books/{book_id}/audio-sources` would return each source that has any saved audio (with its voice snapshot and counts), feeding "Ways to listen" and the narrator sheet's "already recorded" hints without the client remembering session IDs. It depends on making listening sessions a server-held resource, which is a larger change (open question 1), so it is **not** part of this proposal's patch series.

### Tests

Complete/partial/stale/none chapters from constructed takes; a text edit turns ready into stale; performance source; unknown performance 404; unknown session is all `none`; unknown durations give `seconds_total: null`; archived book 200; response validated against the contract.

---

## Left client-local on purpose

| State | Where | Why |
|---|---|---|
| Exact audio time, scroll position | device (`bardic:progress:<bookId>`) | Granular and narrator-specific; the server keeps only the coarse checkpoint (part A). |
| Chosen profile | device (`bardic:profile`) | The server has no "current profile" (part P). |
| Playback speed (`bardic:speed`) | device | Depends on the device and headphones. |
| Reader appearance (size, theme, width) | device | Phone and iPad want different values. |
| Paid-run consent | `sessionStorage` | Deliberately not persisted (cost rules). |
| Narrator/session choice | `BookCheckpoint.source` (A) | The one server-held slice worth sharing across devices. |
| Draft listening sessions `bardic:listen:<id>` | device, until open question 1 | See below. |

If per-profile preferences (speed, appearance) are wanted later, add one `getPreferences`/`setPreferences` pair under the profile scope, not more per-feature routes.

## Decisions recorded (2026-09-30)

1. Profiles exist, picked from a dropdown, with no authentication (part P).
2. The server keeps coarse listening checkpoints; the client keeps granular playback data (part A).
3. A book is finished when marked, or when progress is at least 98% and the checkpoint has not changed for more than 24 hours; any change resets that clock (part A, "Finished").
4. Spending limits stay universal across profiles; profiles do not separate cost (part P).
5. Checkpoint history is kept, bounded to 10 per profile and book (part A).

## Open questions

1. **Server-held listening sessions.** `session_id` is minted in the browser and unknown to the server until takes exist. Making sessions a first-class resource (create, list, voice snapshot) would make `ListenSource.session`, part D's validation and a `listBookAudioSources` route clean, but it is a data-model change. Proposal: ship P, A to D as above with sessions unvalidated, and design sessions separately. If sessions become server-held, decide whether they are profile-scoped.
2. **Repaired structure.** A chapter deleted by a structure repair silently moves the reader. Add `relocated: true` to the checkpoint so the UI can say "your place moved"? Cheap before release, awkward after.
3. **Version cadence.** Five patch releases (0.5.1 to 0.5.5) versus fewer. Separate commits keep each reviewable; a single release is also valid.

## Suggested implementation order

1. P (profiles) and A (checkpoints): unblock the profile picker, Continue, progress, finished state and cross-device resume. P must land first because A's routes and the `checkpoint` summary field are profile-scoped.
2. B (cover sample): tiny, self-contained, unblocks the palette without client image decoding.
3. D (audio status): unblocks the Book page badges.
4. C (duplicates): needs the backfill path, and is the only part with a client-side hash step.

For each: `bardic/apispec/` (Views with field descriptions, `op(...)`, `REQUEST_DOCS`, errors) in the same commit as the route, `uv run --frozen python -m bardic.apispec`, changelog entry with the new patch version, test receiving each 2xx, and an update to [DATA-MODEL.md](../DATA-MODEL.md) and [ROADMAP.md](../ROADMAP.md).

## Design consequences to carry back to the canvas

- A profile picker: a dropdown in Library's header (and a first-run chooser when more than one profile exists), plus manage profiles (add, rename, delete with a "loses places and finished marks" confirm) under Settings.
- Continue row and progress bars read `checkpoint`. Add an "Almost done" state for `progress >= 0.98 && !finished`, and the "Mark as finished" / "Mark as not started" items in the book menu.
- The "Continue from your other device" prompt shows the other device's passage, not a time.
- Settings copy must not promise privacy for profiles.
