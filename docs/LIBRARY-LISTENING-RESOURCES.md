# Library, listening and resource tracking

Implemented September 27, 2026. SQLite remains the local source of truth; no additional database service is required.

## Library and incomplete series

The sidebar's **Manage books & series** opens metadata, storage and series controls. EPUB covers are decoded locally into bounded JPEG thumbnails using Pillow, with embedded metadata removed. No remote cover lookup occurs. Refreshing an existing book reads its retained original and preserves manually edited title/author fields, source IDs, prose, profiles, scene directions and audio. Unsupported or absent artwork uses a text cover. Title and author can also be edited directly.

Original files, enhanced audio and simple listening audio have separate byte counts. SQLite payload size is a logical per-book measure, not a separately allocated physical file. The overall storage count includes shared database files and retained backups. **Remove** hides a book or series and retains its data; **Removed items → Restore** reverses it. There is no permanent purge in this version.

Series use explicit numeric reading order, independent of the text in a book title. A supplied ninth volume can be assigned position 9 without having volumes 1–8. Missing/planned placeholders are optional records with no source text. Importing and assigning the actual book replaces the placeholder at that position. Earlier available volumes contribute evidence only through confirmed series character identity links. Later volumes and unavailable books are excluded from earlier-book interpretation. The app does not automatically establish that a collection or character profile is complete.

Changing a book's series removes its current identity links; retained observations and artifact history survive. Names alone do not merge characters. Review links in **Cast & voices** after discovery identifies the cast. Adding an earlier volume or changing linked evidence makes dependent profiles eligible for refinement while unrelated accepted work stays reusable.

## Series scheduling and recovery

**Open series** offers discovery, profiles, direction, or full analysis. Preview is local and shows supplied books, positions, known requests and estimated cost. Full analysis scans books independently, with concurrency capped at two, then interprets supplied books in reading order. Profiles and direction alone run in reading order. Missing placeholders are skipped.

Each book has a durable child run under a series run. Its request/token allowance is shared across discovery and later phases, rather than reset between phases. The dollar guard includes cumulative tracked analysis spend for that book. The combined ceiling is displayed in the preview. Unknown pricing requires selecting request/token limits explicitly. A plan fingerprint prevents starting a preview whose membership, models or other effective inputs have changed.

A failed or budget-limited book stops new scheduling; already active requests may finish and are accounted for. Cancellation propagates to child jobs and retains accepted outputs. Books stay reserved until their parent run ends so concurrent metadata/membership edits cannot change an active run. Restarting interrupts unfinished jobs; a fresh preview and resume reuses matching accepted work. The series run manifest is retained as a book-scoped artifact for each supplied book. There is no autonomous retry loop that can exhaust an allowance in the background.

## Independent simple listening

Simple mode renders the exact selected source passage using one narrator. Its identity includes provider, model, voice and source anchors. It does not use enhanced character directions, alter book annotations, or replace selected production takes. The existing reader supplies passage highlighting and reading position. Local device voices and Gemini are available; enhanced mode remains selectable.

Only explicit playback creates audio, one passage at a time. Cached takes replay without requiring a provider key. Pause, Stop, switching books or changing configuration invalidate pending playback; a request already in flight can finish and retain its take. Automatic continuation stops at the current chapter boundary. This limits unintended generation but may leave a pause between newly generated passages. Word timing, background whole-book pre-generation, and a separate simple-mode audiobook ZIP are not implemented.

`listening_sessions` and `listening_takes` store reusable metadata. WAV files live under `listen-audio/<book-id>/` with content-addressed filenames. Analysis exports include session/take metadata; the existing audiobook export packages enhanced production audio.

## Resource ledger

**Production studio → Resource usage** aggregates durable operations by stage and run, with a paginated operation list. Analysis attempts include retries and repairs. Narration tracks provider token reports where available, audio seconds and bytes. Import, local census, validation, publication, search, structure repair, metadata refresh and exports record local timings where instrumented. Cached work is labeled separately and does not inherit the historical charge of the original generation.

Elapsed time is the sum of measured operation durations, not total machine wall time; concurrent operations overlap. CPU time measures the current Python thread only, excluding `say`, ffmpeg, other subprocesses, GPU and provider compute. Old records and incomplete responses retain explicit unknown counts. Requests interrupted by restart are not silently reported as successful or free. HTTP success followed by unusable model output still counts as failure.

Costs are dated estimates computed from sufficiently complete provider usage. Missing usage or an unknown price does not mean zero cost. Gemini TTS estimates require the reported text/audio modality breakdown and cache counts needed by the rate calculation. The current research uses [Google's pricing](https://ai.google.dev/gemini-api/docs/pricing) and [Interactions usage fields](https://ai.google.dev/api/interactions-api); rate validity is retained with the estimate. Account-wide spending, balances, free-tier adjustments, discounts and invoice reconciliation are outside this ledger.

`resource_operations` stores operation status and measurements; existing `analysis_attempts` remain the source for analysis HTTP charges. The combined view avoids charging one request twice. `resource-operations.json` joins the portable analysis export; ledger rows contain no credential values or source prose. Polling the dashboard makes no model requests.
