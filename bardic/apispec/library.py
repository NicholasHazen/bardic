"""Contract entries for the library route family.

Importing books, the library snapshot, display metadata, covers, reversible
removal and restoration, and the audiobook export. Summaries are built by
``bardic/library.py`` (``LibraryRepository.summary``/``snapshot``); the series
entries inside the snapshot come from ``bardic/series.py``
(``SeriesRepository.list_series``).
"""
from __future__ import annotations

from pydantic import Field

from .base import Op, View, op
from .books import Book, BookAnalysisSummary
from .series import Series, SeriesArchiveState, SeriesMembership


# ---------------------------------------------------------------- views


class LibraryBookCover(View):
    """Metadata of the book's saved cover thumbnail (a JPEG of at most 240 x 360 pixels)."""
    width: int = Field(description='Thumbnail width in pixels.')
    height: int = Field(description='Thumbnail height in pixels.')
    sha256: str = Field(description='Lowercase hex SHA-256 of the thumbnail bytes. Changes when the cover changes.')
    url: str = Field(description='Root-relative URL of the image, `/api/books/{book_id}/cover?v={sha256}`. The `v` query '
                                 'value is a cache-busting token only; the server ignores it.')


class LibraryBookStorage(View):
    """Measured storage attributed to one book.

    File sizes are regular-file lengths measured on disk at request time
    (symbolic links are not followed). Removing a book does not reclaim any of it.
    """
    original_bytes: int = Field(description='Bytes of the saved original upload (EPUB or TXT). 0 for the demo book, which has no original.')
    audio_bytes: int = Field(description='Bytes of retained enhanced (cast) narration audio, including superseded takes.')
    simple_listen_bytes: int = Field(description='Bytes of retained simple-listening audio.')
    voice_preview_bytes: int = Field(description='Bytes of retained voice-example audio.')
    file_bytes: int = Field(description='Sum of the four file sizes above.')
    database_payload_bytes: int = Field(
        description='Exact byte length of this book\'s rows\' bodies in the shared SQLite database (book JSON, takes, '
                    'analysis checkpoints, artifacts, cover, resource records, listening and preview records, ...). '
                    'It is not a disk allocation: it excludes shared pages, indexes, free space and compression.')
    note: str = Field(description='Human-readable caveat about these measurements. Display only.')


class LibraryBookSummary(View):
    """A library row for one book: counts, cover, membership and measured storage.

    This is not the book's content. Fetch `GET /api/books/{book_id}` for the
    prose projection. Counting and file measurement happen on every request.
    """
    id: str = Field(description='Opaque book ID.')
    title: str = Field(description='Display title.')
    author: str | None = Field(description='Display author; an empty string when unknown. Null only for a stored book without the field.')
    created_at: str | None = Field(description='ISO 8601 UTC import time (with a `+00:00` offset). Null for a stored book without the field.')
    source_name: str | None = Field(description='File name of the original upload, without any directory (for the demo, `The Last Light.txt`). '
                                                'Null for a stored book without the field.')
    analysis: BookAnalysisSummary | None = Field(description='Who produced the current analysis projection; null for a stored book without it.')
    archived: bool = Field(description='True when the book is removed from normal library views (see `archiveBook`).')
    archived_at: str | None = Field(description='ISO 8601 UTC time the book was removed, or null when it is not removed.')
    section_count: int = Field(description='Number of stored sections (chapters plus front/back matter and other non-narrative sections).')
    chapter_count: int = Field(description='Number of narrative chapters (sections whose kind is `chapter`). When no section '
                                           'records a kind (older imports), equals `section_count`.')
    word_count: int = Field(description='Whitespace-separated tokens across all section text.')
    character_count: int = Field(description='Cast members, excluding the two built-in entries `narrator` and `unassigned`. '
                                             'Not a count of text characters (see `text_character_count`).')
    text_character_count: int = Field(description='Length of all section text in Unicode code points.')
    segment_count: int = Field(description='Number of passages (reader units).')
    passage_count: int = Field(description='Same value as `segment_count`; "passage" and "segment" name the same unit.')
    scene_count: int = Field(description='Number of scenes.')
    audio_count: int = Field(description='Passages that have a stored selected enhanced take, whether or not that take is still '
                                         'current. The book document shows only current takes, so this can exceed the playable count.')
    membership: SeriesMembership | None = Field(
        description='Series membership, or null when the book is in no series. Reported even when the book or its series is removed.')
    cover: LibraryBookCover | None = Field(description='Saved cover thumbnail, or null when there is none (TXT imports, the demo, '
                                                       'EPUBs without a usable cover).')
    storage: LibraryBookStorage = Field(description='Measured storage attributed to this book.')


class LibraryStorage(View):
    """Library-wide measured storage.

    Sizes are regular-file lengths measured at request time; symbolic links are not followed.
    """
    data_directory_bytes: int = Field(description='All files under the data directory, including the database, media, originals and backups.')
    database_bytes: int = Field(description='Size of the main SQLite database file.')
    database_wal_bytes: int = Field(description='Size of the SQLite write-ahead log file (0 when absent).')
    database_shm_bytes: int = Field(description='Size of the SQLite shared-memory file (0 when absent).')
    shared_database_bytes: int = Field(description='Sum of the three database file sizes. The database is shared by all books.')
    backup_bytes: int = Field(description='Size of the `backups` folder in the data directory.')
    note: str = Field(description='Human-readable caveat about these measurements. Display only.')


class LibrarySnapshot(View):
    """The library-management view: books, series and storage in one response."""
    books: list[LibraryBookSummary] = Field(
        description='Book summaries, most recently saved first (any save of a book, including a metadata edit, moves it to the front). '
                    'Removed books are included only with `include_archived=true`.')
    series: list[Series] = Field(
        description='Series sorted by name (ignoring case), then ID. Removed series are included only with `include_archived=true`.')
    storage: LibraryStorage = Field(description='Library-wide measured storage.')


# ---------------------------------------------------------------- shared text

_BOOK_ID = 'Opaque book ID.'
_SUMMARY_ORDER = ('Books are ordered most recently saved first: any save of the book (a metadata edit, an analysis '
                  'result, a manual edit) moves it to the front. ')
_REMOVAL = ('There is no destructive book-delete endpoint. Removal (archiving) changes visibility only: the original '
            'upload, analysis, audio, series links and history are retained and remain readable (for example '
            '`GET /api/books/{book_id}`, the cover and the export keep working), and no disk space is reclaimed. '
            'Active processing and most edits reject a removed book with 400 until it is restored.')

OPS: list[Op] = [
    op('GET', '/api/books', 'listBooks', 'Library', 'List active books',
       'Summaries of every book that is not removed: counts, cover metadata, series membership and measured '
       'storage. This is not the prose projection; fetch `GET /api/books/{book_id}` for that. '
       + _SUMMARY_ORDER +
       'Removed books are never listed here; use `GET /api/library?include_archived=true`.\n\n'
       'Each call measures the book\'s media folders on disk and its database payload, so it is proportionally '
       'slower for large libraries. Counts distinguish narrative chapters (`chapter_count`) from other sections '
       '(`section_count`); see docs/STRUCTURE.md.',
       response=list[LibraryBookSummary], response_description='Book summaries, most recently saved first.'),

    op('POST', '/api/books', 'importBook', 'Library', 'Import an EPUB or TXT book',
       'Imports a DRM-free EPUB or UTF-8 TXT uploaded as multipart form data with the single field `file`, and '
       'returns the new book as the full presented book document (the same shape as `GET /api/books/{book_id}`).\n\n'
       '- The format is chosen by the uploaded **file name\'s extension** (`.epub` or `.txt`, any case); the part\'s '
       'content type is ignored. A part without a file name is treated as `book.txt`.\n'
       '- Upload maximum is 30 MiB (31,457,280 bytes); a larger upload is rejected with 413 (the whole request body is '
       'still received first). EPUBs are additionally limited to 100 MB / 5,000 files when expanded.\n'
       '- The title comes from EPUB metadata, or for TXT from the file name (without extension, underscores as spaces); '
       'the author from EPUB creators, or an empty string. An EPUB cover image becomes a JPEG thumbnail.\n'
       '- The book starts with a free local draft (`analysis.provider` `local`, status `draft`): chapters, scenes '
       'and passages are split locally; dialogue passages are `unassigned` until analysis. The narrator and '
       '`unassigned` entries get installed device (macOS) voices when available. No provider is contacted.\n'
       '- The original bytes are saved in the data directory (`originals/{book_id}/source.{ext}`) for later '
       '`refreshBookMetadata` and structure repair. A resource record (stage `import`) is written.\n'
       '- Not idempotent and not deduplicated: every call creates a new book with a new ID, even for the same file.\n\n'
       'Example (synthetic file): `curl --fail --request POST http://127.0.0.1:8765/api/books '
       '--form \'file=@/absolute/path/to/synthetic-story.txt;type=text/plain\'`',
       response=Book, response_description='The newly imported book, presented like `GET /api/books/{book_id}`.',
       errors={400: 'The file could not be imported: empty file, unsupported extension, TXT not UTF-8 or containing binary '
                    'data, unreadable/unsafe/encrypted EPUB, or no readable text. `detail` explains which.',
               413: 'The upload is larger than 30 MiB.'}),

    op('POST', '/api/demo', 'createDemoBook', 'Library', 'Create the demo book',
       'Creates the built-in original sample story ("The Last Light") with a free local heuristic draft analysis '
       '(explicit speech tags only; `analysis.provider` `local`) and returns it as the full presented book document. '
       'No request body and no provider contact.\n\n'
       'Not idempotent: every call creates another copy with a new ID. The demo has no saved original file, so '
       '`refreshBookMetadata` on it fails with 400 and its `storage.original_bytes` is 0.',
       response=Book, response_description='The new demo book, presented like `GET /api/books/{book_id}`.'),

    op('GET', '/api/library', 'getLibrary', 'Library', 'Get the library snapshot',
       'The library-management view: `{books, series, storage}` with book summaries (the `listBooks` shape), '
       'series entries with their books and volume placeholders, and library-wide measured storage.\n\n'
       + _SUMMARY_ORDER +
       'With `include_archived=true`, removed books and removed series are included (each flagged `archived`); '
       'otherwise both are omitted. A series\' `volumes` always include its removed books (status `archived`).\n\n'
       'Storage caveats: per-book `database_payload_bytes` does not apportion SQLite pages, indexes or free space '
       'exactly; the shared database, WAL and SHM file sizes are reported separately in `storage`. Removed items '
       'retain their files and data. Every call walks the data directory to measure sizes.',
       response=LibrarySnapshot,
       params={'include_archived': 'Include removed (archived) books and series. Default false.'}),

    op('PATCH', '/api/books/{book_id}/metadata', 'updateBookMetadata', 'Library', 'Edit display title and author',
       'Sets the display title and author and returns the updated summary. Whitespace is normalized (runs of '
       'spaces, tabs and newlines become one space; leading and trailing whitespace is removed). Title is required '
       '(1–500 characters); author defaults to an empty string (maximum 500).\n\n'
       'Both fields are then marked as reviewed: a later `refreshBookMetadata` never overwrites either of them, '
       'even the author when it was sent empty. The edit increments the book\'s `revision`, retains the previous '
       'projection in history, and moves the book to the front of the library order. It does not rename the '
       'original file or change the text.\n\n'
       'Refused while any job is queued or running for the book, while an active series run has reserved it, '
       'or while the book is removed.',
       response=LibraryBookSummary, params={'book_id': _BOOK_ID},
       errors={400: 'The book is removed (restore it first), or the normalized title is empty, or a value contains control characters.',
               404: 'No book has this ID.',
               409: 'A job is working on this book, or an active series run has reserved it.',
               422: 'Missing `title`, a title or author longer than 500 characters, or an unknown field.'}),

    op('POST', '/api/books/{book_id}/archive', 'archiveBook', 'Library', 'Remove a book from the library (reversibly)',
       'Reversibly removes the book from normal library views (`listBooks`, the default `getLibrary`, series '
       '`books` lists) and returns `{id, archived: true, retained: true}`. No request body.\n\n'
       + _REMOVAL + ' Removal of a series member leaves the series intact; the book appears in the series\' '
       '`volumes` with status `archived`.\n\n'
       'Each successful call records a `library_state` artifact in the book\'s history. Removing an already '
       'removed book is refused with 400 (not idempotent). Refused while any job is queued or running for the '
       'book or an active series run has reserved it.',
       response=SeriesArchiveState, params={'book_id': _BOOK_ID},
       errors={400: 'The book is already removed.',
               404: 'No book has this ID.',
               409: 'A job is working on this book, or an active series run has reserved it.'}),

    op('POST', '/api/books/{book_id}/restore', 'restoreBook', 'Library', 'Restore a removed book',
       'Restores a removed book to normal library views and returns `{id, archived: false, retained: true}`. '
       'No request body. Everything retained during removal becomes usable again.\n\n'
       'Idempotent in effect: restoring a book that is not removed succeeds with the same response (and, like '
       'every successful call, records a `library_state` artifact in the book\'s history). Respects series-run '
       'guards: refused while a run of the book\'s series is queued or running (even if the series itself is '
       'removed). Restoring a book does not restore its removed series.',
       response=SeriesArchiveState, params={'book_id': _BOOK_ID},
       errors={400: 'A job is queued or running for this book.',
               404: 'No book has this ID.',
               409: 'A run of the book\'s series is queued or running.'}),

    op('POST', '/api/books/{book_id}/refresh-metadata', 'refreshBookMetadata', 'Library',
       'Re-read title, author and cover from the saved original',
       'Re-parses the saved original EPUB or TXT (locally; it does not download metadata from the web) and '
       'returns the updated summary. No request body.\n\n'
       '- Title and author are replaced by the values parsed from the original, except a field that was set by '
       '`updateBookMetadata` (reviewed display metadata is preserved; that edit marks both fields).\n'
       '- If the original yields a cover thumbnail, it replaces the saved cover. A missing cover in the original '
       'does not remove an existing one.\n'
       '- Chapters, passages, analysis and audio are not changed; use `POST /api/books/{book_id}/repair-structure` '
       'for structure.\n'
       '- Always increments the book\'s `revision` and retains the previous projection in history, even when nothing changed.\n'
       '- A resource record (stage `metadata_refresh`) is written for every attempt, including failed ones (also for an unknown ID).\n\n'
       'Refused while any job is queued or running for the book, while an active series run has reserved it, or '
       'while the book is removed.',
       response=LibraryBookSummary, params={'book_id': _BOOK_ID},
       errors={400: 'The book is removed; the saved original is unavailable (for example the demo book, which has '
                    'none) or larger than the import limit; or it can no longer be parsed or its cover read.',
               404: 'No book has this ID.',
               409: 'A job is working on this book, or an active series run has reserved it.'}),

    op('GET', '/api/books/{book_id}/cover', 'getBookCover', 'Library', 'Download the cover thumbnail',
       'The saved cover thumbnail bytes. Covers are always stored as JPEG (at most 240 x 360 pixels, at most '
       '256 KiB), so the content type is `image/jpeg`. Works for removed books too. Use the `cover.url` from a '
       'summary, which appends `?v={sha256}` as a cache-busting token; the server ignores any query parameters.\n\n'
       'Caching, as actually sent: the response has an `ETag` header whose value is the SHA-256 hex of the bytes '
       '**without the quotes** HTTP requires, and `Cache-Control: no-store` (the route asks for '
       '`private, max-age=300`, but the `/api/` middleware overrides it). Conditional requests are not supported: '
       '`If-None-Match` is ignored and the full image is always returned with 200.',
       media='image/jpeg', response_description='JPEG image bytes.', params={'book_id': _BOOK_ID},
       errors={404: 'No book has this ID (`Book not found`), or the book has no saved cover (`Cover not found`).'}),

    op('GET', '/api/books/{book_id}/export', 'exportAudiobook', 'Library', 'Download the audiobook ZIP',
       'Builds and downloads an audiobook ZIP from the book\'s valid enhanced (cast) takes. Requires at least one '
       'valid take; a take is valid when it still matches the passage\'s current text, speaker, voice, scene '
       'direction and provider/model, and its WAV file exists. Simple-listening and voice-example audio are not '
       'included. Works for removed books and does not require the book to be idle.\n\n'
       'The response is `application/zip` with `Content-Disposition: attachment` and a file name derived from the '
       'title (characters other than letters, digits, underscore, space, `.` and `-` removed; at most 80 characters; '
       '`audiobook` if nothing remains) plus `.zip`. The archive is assembled synchronously in a temporary folder '
       'before the response starts, so a large book takes a while. A resource record (stage `audio_export`) is written: '
       'this GET writes local bookkeeping but never contacts a provider.\n\n'
       'Archive contents:\n\n'
       '- `production.json`: the raw stored book JSON (pretty-printed UTF-8), not the presented document: no '
       '`leading_text`/`trailing_text` or audio URLs, and each passage\'s `audio` is its stored take metadata '
       '(or null), including takes that are no longer current. Treat its fields as storage, not as a contract.\n'
       '- `README.txt`: a short plain-text explanation.\n'
       '- `takes/{segment_id}.wav`: one file per passage with a valid take, even when its chapter is incomplete.\n'
       '- `chapters/NNN.txt`: the text of every section, numbered from `001` in book order (all sections, not only '
       'narrative chapters).\n'
       '- `chapters/NNN.wav`: the concatenated takes of a section whose passages all have valid takes (mono '
       '24 kHz 16-bit PCM). Incomplete sections get no chapter WAV; sections without passages get none either.\n'
       '- `timeline.json`: `{title, timing_kind: "segment", complete, chapters, missing_segment_ids}`. `complete` '
       'is true when every passage has a valid take. Each `chapters` entry is `{id, title, complete, segments, audio?}` '
       'where `audio` is the chapter WAV path inside the archive (only for assembled sections) and `segments` lists '
       '`{segment_id, start, end}` in seconds within that WAV (empty for unassembled sections). Timings mark exact '
       'passage boundaries, not words. `missing_segment_ids` lists passages without a valid take.',
       media='application/zip', ranges=True, response_description='The audiobook ZIP archive.', params={'book_id': _BOOK_ID},
       errors={400: 'No passage has a valid enhanced take (`Generate some audio before exporting`), or a take '
                    'file is not a readable mono 24 kHz 16-bit PCM WAV.',
               404: 'No book has this ID.'}),
]

REQUEST_DOCS: dict[str, dict[str, str]] = {
    'BookMetadataRequest': {
        '__doc__': 'New display metadata for a book. Both values are whitespace-normalized; control characters other '
                   'than tab and newline are rejected with 400.',
        'title': 'Display title, 1–500 characters. Required. 400 if it is empty after whitespace normalization.',
        'author': 'Display author, at most 500 characters. Defaults to an empty string (unknown author) when omitted.',
    },
    'ImportBookForm': {
        '__doc__': 'Multipart form data for `importBook`.',
        'file': 'The ebook: a DRM-free `.epub` or UTF-8 `.txt`, at most 30 MiB. The format is taken from the file '
                'name\'s extension, which must therefore be sent.',
    },
}
