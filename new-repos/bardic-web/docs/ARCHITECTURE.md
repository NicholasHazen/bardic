# Client architecture (proposal)

Status: proposal for review; none of it is built. **decide** marks choices that need an owner decision before the milestone that depends on them.

## 1. Stack (decide)
| Concern | Proposal | Alternatives |
|---|---|---|
| Language | TypeScript, strict | |
| Build | Vite, single-page app, installable PWA | |
| UI | Svelte 5 (small output, simple reactive state for a player-heavy app) | Solid, Lit |
| API client | Types generated from the contract with `openapi-typescript`, `openapi-fetch` | hand-written fetch wrappers over the generated types |
| Audio | `HTMLAudioElement` with Media Session API for lock screen and headset controls | Web Audio only if needed |
| Offline storage | IndexedDB (an index of what is held, queued writes) plus Cache Storage or the origin private file system for audio and text blobs | |
| Tests | Vitest for logic, Playwright for flows (Chromium at `/opt/pw-browsers` in cloud sessions) | |

## 2. Structure
```
src/
  api/        generated schema.d.ts, client, error mapping, event stream
  state/      listener, server, library, book, place, plans, downloads (stores)
  player/     audio engine, line highlighter, chapter prefetch, media session
  sync/       place queue with revisions and conflict handling
  offline/    downloader, manifest, update check, storage accounting
  theme/      palette derivation from Cover.sample, tokens, glass components
  views/      Home, Library, Book, Now Playing, Settings, sheets
  lib/        code point text helpers, money and cost formatting, time
```

## 3. Identity
The device id is a UUID in `localStorage` and is sent on every mutating request (`X-Bardic-Device`). The selected listener is kept per device and sent as `X-Bardic-Listener`. A 404 `listener_not_found` returns the user to the chooser.

## 4. Place sync
A local copy of the place (exact audio time, scroll position, `revision`) is written continuously. The server is written on pause, chapter change, seek, close, backgrounding, and at most every 30 seconds while playing, with `keepalive` fetch on unload. Offline writes are coalesced into a queue and replayed on reconnect. On `409 place_conflict`, the client applies the listener's setting: ask (show both places), newest, or this device; the choice is written with the server's revision as the base.

## 5. Offline
- A download is a set of chapters of one audiobook: audio, timings and text, recorded in IndexedDB with `audio.id` and hashes.
- Verify files on open; repair or re-download on mismatch.
- `checkDownloads` compares what is held with the server and produces the **Out of date** comparison; nothing is replaced without the listener's choice.
- Request persistent storage; download in the foreground with clear progress, pause and resume by range.
- **Risk:** browsers limit and may evict stored data; iOS does not allow background downloads and is stricter for sites that are not installed. Mitigate with install-to-home-screen guidance, storage checks before a download, and verification on open. A native wrapper is a follow-on if the limits bite.

## 6. Player
One audio engine per page: loads the chapter audio, applies the listener's speed, exposes the four listening states, and emits the current line from the timings for Read mode. It prefetches the next chapter and asks the server to make it ahead. Switching audiobook keeps the text place and waits until the new audiobook has audio there.

## 7. Theming
Derive the palette from `Cover.sample` (see the UI guide), set CSS custom properties per book, and fall back to the default palette. Screens without a book use the default.

## 8. Money and cost display
Format `Money` from integer micros. Show ranges as "$1.80 to $2.60" with "most likely". A `Spend` with `unknown_items` shows the count beside the known total and never adds it as zero.

## 9. Risks
1. Offline storage limits on iOS and Safari.
2. Mapping code point offsets to DOM positions for highlighting (surrogate pairs, combining marks).
3. Keeping audio playing across backgrounding and lock screen on iOS.
4. Generating Media Session metadata and artwork from the cover.
