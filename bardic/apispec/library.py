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
    url: str = Field(description='Root-relative URL of the image, `/api/books/{book_id}/cover?v={sha256}`. Because the '
                                 '`v` value names these exact bytes, the server lets clients cache this URL indefinitely '
                                 '(see `getBookCover`); a changed cover gets a new URL.')


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
                    'character references, artifacts, cover, resource records, listening and preview records, ...). '
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
    passage_count: int = Field(description='Number of passages (the reader units of the book).')
    scene_count: int = Field(description='Number of scenes.')
    audio_count: int = Field(description='Passages whose selected enhanced (cast) take is current and playable: it still matches '
                                         'the passage\'s text, speaker, resolved voice, scene direction and provider/model, and '
                                         'its audio file exists. Equals the number of passages with a non-null `audio` in '
                                         '`GET /api/books/{book_id}`. Superseded or stale takes stay stored but are not counted.')
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
        description='Book summaries, most recently imported first (see `listBooks` for the exact order). '
                    'Removed books are included only with `include_archived=true`.')
    series: list[Series] = Field(
        description='Series sorted by name (ignoring case), then ID. Removed series are included only with `include_archived=true`.')
    storage: LibraryStorage = Field(description='Library-wide measured storage.')


# ---------------------------------------------------------------- shared text

_BOOK_ID = 'Opaque book ID.'
_SUMMARY_ORDER = ('Books are ordered by import time, most recently imported first (`created_at` descending). Saving a '
                  'book (a metadata edit, an analysis result, a manual edit) does not change its place. Books stored '
                  'without `created_at` come last, in a stable order. ')
_REMOVAL = ('There is no destructive book-delete endpoint. Removal (archiving) changes visibility only: the original '
            'upload, analysis, audio, series links and history are retained and remain readable (for example '
            '`GET /api/books/{book_id}`, the cover and the export keep working), and no disk space is reclaimed. '
            'Active processing and most edits reject a removed book with 409 `book_archived` until it is restored.')
_NOT_FOUND = {'book_not_found': 'No book has this ID.'}
_BUSY = {'job_active': 'A job is queued or running for this book.',
         'series_run_active': 'An active series run has reserved this book.'}
_ARCHIVED = {'book_archived': 'The book is removed (archived); restore it first.'}

OPS: list[Op] = [
    op('GET', '/api/books', 'listBooks', 'Library', 'List active books',
       'Summaries of every book that is not removed: counts, cover metadata, series membership and measured '
       'storage. This is not the prose projection; fetch `GET /api/books/{book_id}` for that. '
       + _SUMMARY_ORDER +
       'Removed books are never listed here; use `GET /api/library?include_archived=true`.\n\n'
       'Read-only. Each call measures the book\'s media folders on disk and its database payload, and checks '
       'which takes are still current (`audio_count`), so it is proportionally slower for large libraries. Counts '
       'distinguish narrative chapters (`chapter_count`) from other sections (`section_count`); see docs/STRUCTURE.md.',
       response=list[LibraryBookSummary], response_description='Book summaries, most recently imported first.'),

    op('POST', '/api/books', 'importBook', 'Library', 'Import an EPUB or TXT book',
       'Imports a DRM-free EPUB or UTF-8 TXT uploaded as multipart form data with the single field `file`, and '
       'returns the new book as the full presented book document (the same shape as `GET /api/books/{book_id}`).\n\n'
       '- The format is chosen by the uploaded **file name\'s extension** (`.epub` or `.txt`, any case); the part\'s '
       'content type is ignored. A part without a file name is treated as `book.txt`.\n'
       '- Upload maximum is 30 MiB (31,457,280 bytes) of file content. A request whose `Content-Length` exceeds that '
       'limit plus 64 KiB of multipart framing is refused with 413 before its body is read; a body sent without '
       '`Content-Length` is read only up to that limit. EPUBs are additionally limited to 100 MB / 5,000 files when '
       'expanded.\n'
       '- The title comes from EPUB metadata, or for TXT from the file name (without extension, underscores as spaces); '
       'the author from EPUB creators, or an empty string. An EPUB cover image becomes a JPEG thumbnail.\n'
       '- The book starts with a free local draft (`analysis.provider` `local`, status `draft`): chapters, scenes '
       'and passages are split locally; dialogue passages are `unassigned` until analysis. The narrator and '
       '`unassigned` entries get installed device (macOS) voices when available. No provider is contacted.\n'
       '- The original bytes are saved in the data directory (`originals/{book_id}/source.{ext}`) for later '
       '`refreshBookMetadata` and structure repair. A resource record (stage `import`) measures the import.\n'
       '- A failed import leaves nothing behind: no book, no saved original and no resource record.\n'
       '- Not idempotent and not deduplicated: every call creates a new book with a new ID, even for the same file.\n\n'
       'Example (synthetic file): `curl --fail --request POST http://127.0.0.1:8765/api/books '
       '--form \'file=@/absolute/path/to/synthetic-story.txt;type=text/plain\'`',
       response=Book, response_description='The newly imported book, presented like `GET /api/books/{book_id}`.',
       errors={400: {'book_file_invalid': 'The file could not be imported: empty file, unsupported extension, TXT not '
                                          'UTF-8 or containing binary data, unreadable, unsafe or encrypted EPUB, or no '
                                          'readable text. `detail` says which.',
                     'cover_unreadable': 'The EPUB\'s cover image could not be read safely.',
                     'invalid_request': 'The multipart body could not be parsed.'},
               413: {'upload_too_large': 'The upload is larger than 30 MiB.'}}),

    op('POST', '/api/demo', 'createDemoBook', 'Library', 'Create the demo book',
       'Creates the built-in original sample story ("The Last Light") with a free local heuristic draft analysis '
       '(explicit speech tags only; `analysis.provider` `local`) and returns it as the full presented book document. '
       'No request body and no provider contact.\n\n'
       'Not idempotent: every call creates another copy with a new ID. The demo has no saved original file, so '
       '`refreshBookMetadata` on it fails with 400 `original_unavailable` and its `storage.original_bytes` is 0.',
       response=Book, response_description='The new demo book, presented like `GET /api/books/{book_id}`.'),

    op('GET', '/api/library', 'getLibrary', 'Library', 'Get the library snapshot',
       'The library-management view: `{books, series, storage}` with book summaries (the `listBooks` shape), '
       'series entries with their books and volume placeholders, and library-wide measured storage.\n\n'
       + _SUMMARY_ORDER +
       'With `include_archived=true`, removed books and removed series are included (each flagged `archived`); '
       'otherwise both are omitted. A series\' `volumes` always include its removed books (status `archived`).\n\n'
       'Read-only. Storage caveats: per-book `database_payload_bytes` does not apportion SQLite pages, indexes or '
       'free space exactly; the shared database, WAL and SHM file sizes are reported separately in `storage`. '
       'Removed items retain their files and data. Every call walks the data directory to measure sizes.',
       response=LibrarySnapshot,
       params={'include_archived': 'Include removed (archived) books and series. Default false.'}),

    op('PATCH', '/api/books/{book_id}/metadata', 'updateBookMetadata', 'Library', 'Edit display title and author',
       'Sets the display title and author and returns the updated summary. Whitespace is normalized (runs of '
       'spaces, tabs and newlines become one space; leading and trailing whitespace is removed). Title is required '
       '(1–500 characters); author defaults to an empty string (maximum 500). A missing `title`, a value longer '
       'than 500 characters or an unknown field is a 422 validation error.\n\n'
       'Each field whose value this edit changes is marked as reviewed: a later `refreshBookMetadata` never '
       'overwrites it. A field sent with its current value is not marked, and earlier marks are kept. An edit that '
       'changes a field increments the book\'s `revision` and retains the previous projection in history; an edit '
       'that changes nothing saves nothing. It does not rename the original file, change the text, or change the '
       'book\'s place in the library order.\n\n'
       'Refused while any job is queued or running for the book, while an active series run has reserved it, '
       'or while the book is removed.',
       response=LibraryBookSummary, params={'book_id': _BOOK_ID},
       errors={400: {'metadata_invalid': 'The normalized title is empty, or a value contains control characters.'},
               404: _NOT_FOUND,
               409: {**_BUSY, **_ARCHIVED}}),

    op('POST', '/api/books/{book_id}/archive', 'archiveBook', 'Library', 'Remove a book from the library (reversibly)',
       'Reversibly removes the book from normal library views (`listBooks`, the default `getLibrary`, series '
       '`books` lists) and returns `{id, archived: true, retained: true}`. No request body.\n\n'
       + _REMOVAL + ' Removal of a series member leaves the series intact; the book appears in the series\' '
       '`volumes` with status `archived`.\n\n'
       'Idempotent: removing an already removed book succeeds with the same response and changes nothing. A call '
       'that removes the book records a `library_state` artifact in its history. Refused while any job is queued or '
       'running for the book or an active series run has reserved it.',
       response=SeriesArchiveState, params={'book_id': _BOOK_ID},
       errors={404: _NOT_FOUND, 409: _BUSY}),

    op('POST', '/api/books/{book_id}/restore', 'restoreBook', 'Library', 'Restore a removed book',
       'Restores a removed book to normal library views and returns `{id, archived: false, retained: true}`. '
       'No request body. Everything retained during removal becomes usable again.\n\n'
       'Idempotent: restoring a book that is not removed succeeds with the same response and changes nothing. A '
       'call that restores the book records a `library_state` artifact in its history. Refused while a job is queued '
       'or running for the book, or while a run of the book\'s series is queued or running (even if the series '
       'itself is removed). Restoring a book does not restore its removed series.',
       response=SeriesArchiveState, params={'book_id': _BOOK_ID},
       errors={404: _NOT_FOUND,
               409: {'job_active': 'A job is queued or running for this book.',
                     'series_run_active': 'A run of the book\'s series is queued or running.'}}),

    op('POST', '/api/books/{book_id}/refresh-metadata', 'refreshBookMetadata', 'Library',
       'Re-read title, author and cover from the saved original',
       'Re-parses the saved original EPUB or TXT (locally; it does not download metadata from the web) and '
       'returns the updated summary. No request body.\n\n'
       '- Title and author are replaced by the values parsed from the original, except a field that an '
       '`updateBookMetadata` call changed (reviewed display metadata is preserved per field).\n'
       '- If the original yields a cover thumbnail, it replaces the saved cover. A missing cover in the original '
       'does not remove an existing one.\n'
       '- Chapters, passages, analysis and audio are not changed; use `POST /api/books/{book_id}/repair-structure` '
       'for structure.\n'
       '- Always increments the book\'s `revision` and retains the previous projection in history, even when nothing changed.\n'
       '- A resource record (stage `metadata_refresh`) measures each refresh that reaches the original, including one '
       'that fails to parse it. A refused request (unknown, removed or busy book) records nothing.\n\n'
       'Refused while any job is queued or running for the book, while an active series run has reserved it, or '
       'while the book is removed.',
       response=LibraryBookSummary, params={'book_id': _BOOK_ID},
       errors={400: {'original_unavailable': 'The book has no readable saved original (for example the demo book).',
                     'original_too_large': 'The saved original is larger than the import limit.',
                     'original_unreadable': 'The saved original can no longer be parsed. `detail` says why.',
                     'cover_unreadable': 'The original\'s cover image could not be read safely.'},
               404: _NOT_FOUND,
               409: {**_BUSY, **_ARCHIVED}}),

    op('GET', '/api/books/{book_id}/cover', 'getBookCover', 'Library', 'Download the cover thumbnail',
       'The saved cover thumbnail bytes. Covers are always stored as JPEG (at most 240 x 360 pixels, at most '
       '256 KiB), so the content type is `image/jpeg`. Works for removed books too. Read-only.\n\n'
       'Caching: the response has a strong `ETag`, the quoted SHA-256 hex of the bytes (`"{sha256}"`, where '
       '`sha256` is `cover.sha256` in a summary). A request whose `If-None-Match` lists that tag (weak comparison, '
       'or `*`) gets 304 Not Modified with an empty body. With `?v=` equal to the current `sha256` (the summary\'s '
       '`cover.url`), the response carries `Cache-Control: private, max-age=31536000, immutable`, because that URL '
       'always names these bytes; any other URL gets `Cache-Control: private, no-cache` (revalidate with the '
       '`ETag`). Errors are `no-store` like every other `/api/` response. Other query parameters are ignored.',
       media='image/jpeg', response_description='JPEG image bytes.', params={'book_id': _BOOK_ID}, conditional=True,
       errors={404: {**_NOT_FOUND, 'cover_not_found': 'The book has no saved cover.'}}),

    op('GET', '/api/books/{book_id}/export', 'exportAudiobook', 'Library', 'Download the audiobook ZIP',
       'Builds and downloads an audiobook ZIP from the book\'s valid enhanced (cast) takes. Requires at least one '
       'valid take; a take is valid when it still matches the passage\'s current text, speaker, voice, scene '
       'direction and provider/model, and its WAV file exists. Simple-listening and voice-example audio are not '
       'included. Works for removed books and does not require the book to be idle.\n\n'
       'The response is `application/zip` with `Content-Disposition: attachment` and a file name derived from the '
       'title (characters other than letters, digits, underscore, space, `.` and `-` removed; at most 80 characters; '
       '`audiobook` if nothing remains) plus `.zip`. The archive is assembled synchronously in a temporary folder '
       'before the response starts, so a large book takes a while. Read-only: it records nothing and never contacts '
       'a provider; the temporary folder is deleted after the response.\n\n'
       'Archive layout:\n\n'
       '- `production.json`: the book exactly as `GET /api/books/{book_id}` presents it at export time (the `Book` '
       'schema, pretty-printed UTF-8), including `leading_text`/`trailing_text`; each passage\'s `audio` is its '
       'current take or null. Its audio `url` values point at this server, not into the archive; use `takes/`.\n'
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
       errors={400: {'export_audio_missing': 'No passage has a current enhanced take.',
                     'take_unreadable': 'A take file is not a readable mono 24 kHz 16-bit PCM WAV.'},
               404: _NOT_FOUND}),
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
