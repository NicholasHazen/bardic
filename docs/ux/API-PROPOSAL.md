# API proposal for the redesign

Status: **proposal, 2026-09-30.** Nothing here is implemented. `contract/openapi.json` (0.5.0) is unchanged. Each part is written so it can be turned into `bardic/apispec/` entries, following [API-WORKFLOW.md](../API-WORKFLOW.md), without further design.

The redesign (Library → Book page → Now Playing, see [FOUNDATIONS.md](FOUNDATIONS.md)) makes four assumptions the backend does not meet today. This document defines each as a contract change, and records what to leave client-local.

| # | Design assumption | Today | Proposal | Contract bump |
|---|---|---|---|---|
| A | Reading/listening position follows the book across devices; Library shows "Continue" and progress | `bardic:progress:<bookId>` and `bardic:lastBook` in `localStorage` | Position resource per book + `position` on `LibraryBookSummary` | additive, 0.5.1 |
| B | Aurora palette derives from the cover, without decoding it in the browser on every render | Client would have to fetch and sample every thumbnail | `sample` colour on `LibraryBookCover` (computed once, stored) | additive, 0.5.2 |
| C | Importing a book already in the library warns instead of silently creating a copy | `importBook` is "not deduplicated" by contract | `source_sha256` on books + `findDuplicateBooks` | additive, 0.5.3 |
| D | Book page shows per-chapter "Saved audio" for the chosen narrator | Client must page `listListeningTakes` and derive coverage itself | `getBookAudioStatus` (read-only, per chapter) | additive, 0.5.4 |

All four are additive (new operations, new always-sent response fields, no changed meaning), so each is a patch bump under the 0.x rules. Ship them as separate commits in the order above; A unblocks the most UI. Every new operation has cost class none (no `x-bardic-cost`): none contacts a provider.

Shared conventions: operation IDs are permanent, so the names below are the ones to keep. Wire names are snake_case. Always-sent fields have no default. Errors use the existing `Error {detail, code}` shape with the status classes in `bardic/errors.py`; `book_not_found` (404) and `book_archived` (409) are reused, not redefined.

---

## A. Book position

### Model

A position answers "where was I in this book, last time, on any device". It is a **current projection** (AGENTS.md: not history, not a cache, not an artifact). Moving it never touches canonical text, takes or performances.

Audio time is deliberately **not** the stored coordinate. Seconds differ per narrator, per speed and between reading and listening, and a reader-only position has no audio at all. The durable coordinate is the canonical text offset (zero-based Unicode code points into the chapter, the same coordinates as spans). The wire also carries the passage and a fraction through it, because that is what a player can seek to on any source.

```
BookPosition {
  chapter_id: string            // resolved from the stored offset
  passage_id: string            // passage containing char_offset now
  passage_fraction: number      // 0.0 <= x < 1.0, position inside that passage's text
  char_offset: integer          // stored anchor: code points from the start of the chapter text
  progress: number              // 0.0..1.0 of the book's text before the position, by code points
  completed: boolean            // true once the last passage has been reached (see rules)
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

`ListenSource` mirrors what `listListeningTakes` and the performance routes already address, so "resume with the same narrator" works without a new identity. `session_id` is opaque and currently browser-local; the server stores it verbatim and does not validate it against anything (see open question 3).

### Operations

| ID | Route | Purpose |
|---|---|---|
| `getBookPosition` | `GET /api/books/{book_id}/position` | Current position. |
| `setBookPosition` | `PUT /api/books/{book_id}/position` | Replace the position (idempotent). |
| `clearBookPosition` | `DELETE /api/books/{book_id}/position` | "Mark as not started". |

`getBookPosition` returns 200 with `BookPosition`, or 404 `position_not_found` when the book has never been opened. A 404 for a missing position is used instead of a nullable 200 body so "no position" stays a typed, documented state; `LibraryBookSummary.position` (below) is the nullable form for lists.

`setBookPosition` request (`REQUEST_DOCS` entries for each):

```
{ chapter_id: string, passage_id: string, passage_fraction: number,
  mode: "listening" | "reading", source?: ListenSource | null, device_id?: string | null }
```

- Server converts `passage_id` + `passage_fraction` to `char_offset` and stores the offset. It validates that the passage belongs to `chapter_id` and to this book.
- **Last write wins.** There is one owner and no accounts. The server sets `updated_at`; a client-supplied time is not accepted, so a device with a wrong clock cannot pin the position. `device_id` is informational: a client that reads a position whose `device_id` differs from its own and whose `updated_at` is newer than its last local write shows the "Continue from your other device" prompt from the design. The server takes no action on it.
- Returns 200 with the stored `BookPosition` (resolved fields), so the client need not re-read.
- `completed` becomes true when the offset is in the final passage of the last narrative chapter with `passage_fraction >= 0.98`. It goes back to false on any later `set` that is earlier. Front/back matter (non-narrative sections) does not count as the end.
- Errors: 404 `book_not_found`; 409 `book_archived`; 400 `position_invalid` (chapter not in book, passage not in chapter, fraction outside `[0, 1)`), 400 `invalid_request` for malformed bodies.

`clearBookPosition` returns 204, and is idempotent (204 when there was none). It removes only the current projection.

### Library integration

Add to `LibraryBookSummary` (always sent, nullable):

```
position: BookPosition | null   // null: never opened, or cleared
```

The Library "Continue" row is `position != null && !completed`, sorted by `position.updated_at` descending, and the progress bars use `position.progress`. No separate "continue" endpoint is needed; the summary already carries everything and `getLibrary` is what the Library screen loads. Cost of populating: one indexed lookup per book (a `book_positions` table keyed by `book_id`), no scan of book bodies.

### Storage and lifecycle

- New table `book_positions(book_id TEXT PRIMARY KEY, body TEXT NOT NULL)` created idempotently in `Store` init, like `settings`. This is a schema change, so it needs the version identifier and migration notes required by [DATA-MODEL.md](../DATA-MODEL.md): add `position_version: 1` in the body.
- Archiving a book keeps its position (restore should resume). Positions are excluded from the analysis export (`spintails-analysis` format is unchanged; it is book content, not personal state).
- If a structure repair changes passage boundaries, the stored `char_offset` still resolves to the passage containing it. If the chapter itself is gone, `getBookPosition` returns the first passage of the nearest earlier surviving chapter and the response is otherwise normal (open question 4).

### Client behaviour

- While playing, PUT at most once per 5 s and on pause, on chapter change, on seek release, and on `visibilitychange` to hidden, using `fetch(..., {keepalive: true})` (`sendBeacon` only issues POST, so it is not used).
- Failure to write is silent and retried on the next tick; the local `bardic:progress:<bookId>` copy remains as the offline fallback, always written first.
- **Migration:** on opening a book, if the server returns `position_not_found` and a local `bardic:progress:<bookId>` exists, convert `segmentId` to `passage_id` and PUT it with `passage_fraction` 0 (the local `currentTime` is narrator-specific and is not carried over). `bardic:lastBook` becomes redundant once the summary carries `position`; keep reading it for one release as a fallback.

### Tests

Contract-conformance test receiving a 2xx from all three operations, and `LibraryBookSummary.position` populated and null. Unit tests: offset round-trip through a passage split; invalid passage/chapter; archived book 409; completion rule and reset; structure repair keeps the offset; `clear` is idempotent; concurrent PUTs serialize through the Store lock.

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
- Errors: 404 `book_not_found`; 404 `listening_source_not_found` when the source ID does not exist for the book (for `performance`) — sessions cannot be checked (open question 3), so an unknown session ID returns a valid, all-`none` status; a removed (archived) book still returns 200, because reading its status is harmless. 400 `invalid_request` for a missing or inconsistent `source_id`.
- Cost: none, no provider. Each call resolves currency by checking take metadata against current passages, so it costs about as much as `listListeningTakes`; document that, and cache nothing server-side in the first version.

### Optional follow-up: `listBookAudioSources`

`GET /api/books/{book_id}/audio-sources` would return each source that has any saved audio (with its voice snapshot and counts), feeding "Ways to listen" and the narrator sheet's "already recorded" hints without the client remembering session IDs. It depends on making listening sessions a server-held resource, which is a larger change (open question 3), so it is **not** part of this proposal's patch series.

### Tests

Complete/partial/stale/none chapters from constructed takes; a text edit turns ready into stale; performance source; unknown performance 404; unknown session is all `none`; unknown durations give `seconds_total: null`; archived book 200; response validated against the contract.

---

## Left client-local on purpose

| State | Where | Why |
|---|---|---|
| Playback speed (`bardic:speed`) | device | Depends on the device and headphones; the design already treats speed as a per-device pill. |
| Reader appearance (size, theme, width) | device | Phone and iPad want different values. |
| Paid-run consent | `sessionStorage` | Deliberately not persisted (cost rules). |
| Narrator/session choice | inside `BookPosition.source` (A) | The one server-held slice worth sharing across devices. |
| Draft listening sessions `bardic:listen:<id>` | device, until open question 3 | See below. |

If a settings sync is wanted later it should be one `getPreferences`/`setPreferences` pair, not more per-feature routes; nothing above needs it.

## Open questions

1. **`completed` threshold.** 98% of the last narrative passage is a guess. Alternative: explicit `finished: true` from the client when playback reaches "The end". Recommend explicit-plus-derived: accept `finished` in the request and also derive it; confirm with the owner.
2. **Multiple readers on one server.** The app has no users; a family LAN would share one position per book. That matches "no user authentication" today. If per-person state is ever needed, the position resource gains a profile scope and this becomes a breaking change, so decide before shipping A.
3. **Server-held listening sessions.** `session_id` is minted in the browser and unknown to the server until takes exist. Making sessions a first-class resource (`createListeningSession`, list, snapshot of voice settings) would make `ListenSource.session`, part D's validation and `listBookAudioSources` clean, but it is a data-model change. Proposal: ship A–D as above with sessions unvalidated, and do sessions as a separate design.
4. **Repaired structure.** Position resolves by offset, but a chapter deleted by repair silently moves the reader. Should the response carry `relocated: true` so the UI can say "your place moved"? Cheap to add before release, awkward to add after.
5. **Should A's `progress` be text-based or audio-based?** Text-based is source-independent and always known; audio-based can show 0 for a book with no audio. Chosen: text-based.
6. **Version cadence.** Four patch releases (0.5.1–0.5.4) versus one. Separate commits keep each reviewable; a single release is also valid if the owner prefers.

## Suggested implementation order

1. A (position + summary field): unblocks Continue, progress and cross-device resume.
2. B (cover sample): tiny, self-contained, unblocks the palette without client image decoding.
3. D (audio status): unblocks the Book page badges.
4. C (duplicates): needs the backfill path, and is the only part with a client-side hash step.

For each: `bardic/apispec/` (Views with field descriptions, `op(...)`, `REQUEST_DOCS`, errors) in the same commit as the route, `uv run --frozen python -m bardic.apispec`, changelog entry with the new patch version, test receiving each 2xx, and an update to [DATA-MODEL.md](../DATA-MODEL.md) and [ROADMAP.md](../ROADMAP.md).
