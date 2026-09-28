"""Contract entries for the series route family.

A series groups supplied books in a reading order (``position``), adds
placeholders for volumes the library does not have, holds explicit
cross-book character identities, and runs staged analysis over its books.

The library snapshot's ``series`` entries are built by the same server code
as ``Series`` here (the library family describes them separately).
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field

from .base import Op, View, op
from .common import Job
from .inspection import AnalysisPlanBase, AnalysisPlanLimits

TAG = 'Series'

POSITION = ('Reading order within the series: a finite number from 0 through 1,000,000, sent and returned as a JSON '
            'number (decimals allow prequels and side stories). Unique among supplied books of one series.')


# ------------------------------------------------------------------ views

class SeriesBook(View):
    """A supplied book (one with an ebook in the library) placed in a series."""
    book_id: str = Field(description='Book ID.')
    position: float = Field(description=POSITION)
    title: str = Field(description='Current book title ("Untitled" when the book has none).')
    author: str = Field(description='Current book author; empty when unknown.')
    archived: bool = Field(description='True when the book itself is removed (archived). Series listings omit '
                                       'removed books from `books`, so this is false there.')


class SeriesSuppliedVolume(View):
    """A volume slot filled by a supplied book."""
    book_id: str = Field(description='Book ID of the supplied book.')
    position: float = Field(description=POSITION)
    title: str = Field(description='Current book title.')
    author: str = Field(description='Current book author; empty when unknown.')
    archived: bool = Field(description='True when the book is removed (archived).')
    status: Literal['available', 'archived'] = Field(
        description='`available` for an active book, `archived` for a removed one. Removed books still appear in '
                    '`volumes` (they keep their reading order) although they are omitted from `books`.')


class SeriesVolumeSlot(View):
    """A placeholder for a volume the library does not have. It holds a reading order, never text."""
    series_id: str = Field(description='Series ID.')
    position: float = Field(description=POSITION + ' Placeholders never share a position with a supplied book.')
    title: str = Field(description='Optional label; empty string when none was given.')
    status: Literal['missing', 'planned'] = Field(
        description='`missing`: the volume exists but is not in the library. `planned`: not yet published or '
                    'acquired. Neither contributes knowledge to analysis.')
    book_id: None = Field(description='Always null: a placeholder has no book.')


SeriesVolume = Annotated[Union[SeriesSuppliedVolume, SeriesVolumeSlot], Field(discriminator='status')]


class Series(View):
    """A series with its supplied books, all volume slots and its identity count."""
    id: str = Field(description='Series ID (opaque, currently `series_<hex>`).')
    name: str = Field(description='Display name, 1–200 characters, whitespace-collapsed. Unique ignoring case.')
    created_at: str = Field(description='ISO 8601 UTC creation time.')
    archived: bool = Field(description='True when the series is removed. `listSeries` lists only active series, so '
                                       'this is false there; `getSeriesMap` and the library snapshot with '
                                       '`include_archived=true` can return removed ones.')
    books: list[SeriesBook] = Field(
        description='Supplied, non-removed books in reading order (position, then book ID).')
    volumes: list[SeriesVolume] = Field(
        description='Every slot in reading order: supplied books (including removed ones, with status '
                    '`archived`) and placeholders. The two element shapes differ: select on `status`.')
    character_count: int = Field(description='Number of series-level character identities.')


class SeriesCreated(View):
    """A newly created series. Unlike `Series`, it carries no `archived` or `volumes` field."""
    id: str = Field(description='New series ID.')
    name: str = Field(description='Stored name after whitespace collapsing.')
    created_at: str = Field(description='ISO 8601 UTC creation time.')
    books: list[SeriesBook] = Field(description='Always empty.')
    character_count: int = Field(description='Always 0.')


class SeriesRenamed(View):
    """Result of renaming a series."""
    id: str = Field(description='Series ID.')
    name: str = Field(description='Stored name after whitespace collapsing.')


class SeriesArchiveState(View):
    """Result of removing or restoring a series."""
    id: str = Field(description='Series ID.')
    archived: bool = Field(description='True after removal, false after restoration.')
    retained: bool = Field(description='Always true: removal hides the entry but deletes nothing.')


class SeriesVolumeRemoval(View):
    """Result of removing a volume placeholder."""
    series_id: str = Field(description='Series ID.')
    position: float = Field(description='The position that was cleared, as a number.')
    removed: bool = Field(description='Always true, even when no placeholder was at that position.')


class SeriesIdentityLink(View):
    """A confirmed link from one book-local character to this series identity."""
    book_id: str = Field(description='Book containing the linked character.')
    character_id: str = Field(description='Book-local character ID. Book character IDs are not series identities.')
    confirmed_at: str = Field(description='ISO 8601 UTC time the link was confirmed. Re-linking the same pair keeps '
                                          'the original time.')


class SeriesCharacter(View):
    """A series-level character identity. Names never establish identity; only its links do."""
    id: str = Field(description='Series character ID (opaque, currently `series_character_<hex>`).')
    series_id: str = Field(description='Owning series ID.')
    name: str = Field(description='Display name, 1–200 characters. Duplicate names are allowed: a shared name is '
                                  'not a shared identity.')
    created_at: str = Field(description='ISO 8601 UTC creation time.')
    links: list[SeriesIdentityLink] = Field(
        description='Confirmed book-character links, ordered by book ID then character ID. May include links from '
                    'removed books or to characters that no longer exist in their book.')


class SeriesMembership(View):
    """Where a book sits in its series."""
    series_id: str = Field(description='Series ID.')
    series_name: str = Field(description='Series name.')
    position: float = Field(description=POSITION)


class SeriesCharacterLinkState(View):
    """A book character's confirmed series identity."""
    character_id: str = Field(description='Book-local character ID.')
    series_character_id: str = Field(description='Linked series character ID.')
    name: str = Field(description='Series character name (not the book character name).')
    confirmed_at: str = Field(description='ISO 8601 UTC time the link was confirmed.')
    stale: bool = Field(description='True when the book no longer has a character with `character_id` (for example '
                                    'after a merge). A stale link is ignored by series context. Always false in '
                                    'a link response.')


class SeriesCharacterUnlinked(View):
    """Result of removing a book character's series identity link."""
    character_id: str = Field(description='Book-local character ID from the path.')
    linked: Literal[False] = Field(description='Always false.')


class BookSeries(View):
    """A book's series placement, its identity links and the series identities it can link to."""
    membership: SeriesMembership | None = Field(
        description='Null when the book is in no series, or when the book or its series is removed.')
    series: Series | None = Field(description='The full series entry, or null when `membership` is null.')
    links: list[SeriesCharacterLinkState] = Field(
        description="All retained identity links for this book's characters, ordered by character ID. Links survive "
                    'while the book is removed, so this can be non-empty when `membership` is null.')
    characters: list[SeriesCharacter] = Field(
        description="The series' identities (with all their links), or empty when `membership` is null.")


class SeriesContextSeries(View):
    """The series a context was read from."""
    id: str = Field(description='Series ID.')
    name: str = Field(description='Series name.')


class SeriesContextObservation(View):
    """One source-validated observation of a linked character from an earlier volume.

    Its quote was rechecked against the earlier book's current chapter text at
    `start`/`end` (zero-based Unicode code-point offsets, exclusive end).
    """
    id: str = Field(description='Stable observation ID (content hash).')
    book_id: str = Field(description='Earlier book the evidence comes from.')
    character_id: str = Field(description="Character ID local to that earlier book.")
    chapter_id: str = Field(description='Chapter ID in the earlier book.')
    segment_id: str | None = Field(description='Passage containing the evidence, or null when none overlapped.')
    start: int = Field(description='Chapter-local start offset in Unicode code points.')
    end: int = Field(description='Chapter-local exclusive end offset in Unicode code points.')
    quote: str = Field(description='Exact source text at `start`..`end`.')
    kind: Literal['profile_evidence', 'dialogue'] = Field(
        description='`profile_evidence`: evidence cited for a character profile. `dialogue`: a line attributed to '
                    'the character. Name mentions are excluded: a mention is not proof of presence.')
    description: str = Field(description='Profile description recorded with the evidence; empty for dialogue.')
    direction: str = Field(description='Profile performance direction recorded with the evidence; empty for dialogue.')
    provider: str | None = Field(description='Provider that produced the observation (`local` for local rules), or null.')
    model: str | None = Field(description='Model that produced the observation, or null.')
    confidence: float | None = Field(description='Attribution confidence from 0 to 1 when recorded, else null.')
    book_title: str = Field(description='Title of the earlier book.')
    position: float = Field(description="The earlier book's reading order.")
    chapter_title: str = Field(description='Title of the chapter in the earlier book.')
    chapter_index: int = Field(description='Zero-based index of the chapter in the earlier book.')


class SeriesContextCharacter(View):
    """Earlier-volume evidence for one linked character of this book."""
    character_id: str = Field(description='Character ID in the requested book.')
    series_character_id: str = Field(description='Series identity the character is linked to.')
    name: str = Field(description='Series identity name.')
    observations: list[SeriesContextObservation] = Field(
        description='Included observations, ordered by reading order, book, chapter and offset. When a character '
                    'has more than the per-character cap, an even sample of early, middle and late evidence is '
                    'kept.')


class BookSeriesContext(View):
    """Bounded, source-validated knowledge from strictly earlier volumes, as analysis would receive it."""
    series: SeriesContextSeries | None = Field(description='Null when the book is in no active series.')
    membership: SeriesMembership | None = Field(description='Null when the book is in no active series.')
    characters: list[SeriesContextCharacter] = Field(
        description='Linked characters that received at least one observation. Characters are filled round-robin so '
                    'one major character cannot use the whole allowance.')
    available_observations: int = Field(
        description='Valid observations found before the per-character cap and the size bound.')
    included_observations: int = Field(description='Observations included in `characters`.')
    truncated: bool = Field(description='True when `included_observations` < `available_observations`.')
    context_chars: int = Field(description='Length in characters of the JSON-serialized `characters` array as '
                                           'analysis receives it; at most 12,000 with the current server bound. '
                                           'Analysis also receives validation bookkeeping that this response omits, '
                                           'so the returned array serializes slightly shorter.')
    fingerprint: str = Field(
        description='Stable SHA-256 hex digest of the dependencies (membership, links, earlier sources) and the '
                    'included context. It changes when anything that would change the context changes; it does not '
                    'use timestamps.')


class SeriesBookAnalysisPlan(AnalysisPlanBase):
    """The per-book analysis preview inside a series plan.

    Same fields as the classic per-book plan (`AnalysisPlan`) except `limits`: a series plan states its limits
    once, in `limits_per_book`.
    """


class SeriesPlanBook(View):
    """One supplied book in a series plan."""
    book_id: str = Field(description='Book ID.')
    title: str = Field(description='Book title.')
    position: float = Field(description=POSITION)
    plan: SeriesBookAnalysisPlan = Field(description='The per-book analysis preview for this book, computed with '
                                                     '`resume` true and no chapter scope.')


class SeriesPlan(View):
    """A preview of staged analysis over a series' supplied, active books."""
    series_id: str = Field(description='Series ID.')
    name: str = Field(description='Series name.')
    provider: Literal['gemini', 'openai', 'anthropic'] = Field(description='Cloud analysis provider that would run.')
    model: str = Field(description='Analysis model for profiles and direction, from runtime settings.')
    scan_model: str = Field(description='Discovery (scan) model, from runtime settings.')
    phase: Literal['scan', 'profiles', 'direct', 'full'] = Field(description='Requested phase.')
    concurrency: int = Field(description='Discovery workers that would run (1 or 2). Always 1 for `profiles` and '
                                         '`direct`, which run in reading order.')
    books: list[SeriesPlanBook] = Field(description='Supplied, active books in reading order. May be empty.')
    volumes: list[SeriesVolume] = Field(description="The series' volume slots, as in `Series.volumes`.")
    limits_per_book: AnalysisPlanLimits = Field(
        description='Limits applied separately to each book; total possible spend scales with the number of books.')
    requests: int = Field(description='Sum of known pending requests across books (excludes retries, evidence '
                                      'repairs and work discovered during a full run).')
    estimated_cost_usd: float | None = Field(
        description='Sum of per-book estimates in USD, or null when any book estimate is unknown. Approximate; not '
                    'an invoice.')
    notes: list[str] = Field(description='Human-readable caveats. Display only.')
    plan_fingerprint: str = Field(
        description='SHA-256 hex digest over the plan and each book\'s revision, source hash and series-context '
                    'fingerprint. Send it as `expected_plan_fingerprint` to start exactly this scope.')


class SeriesRun(Job):
    """A series parent job with its child book jobs."""
    children: list[Job] = Field(
        description='The child `analyze` jobs, one per supplied book, in reading order. They use real book IDs.')


class SeriesRuns(View):
    """Recent series runs."""
    runs: list[SeriesRun] = Field(description='Up to 20 parent runs, newest first.')


class SeriesMap(View):
    """A series with its volumes and explicit identities."""
    series: Series = Field(description='The series entry, including supplied, missing and planned volumes.')
    characters: list[SeriesCharacter] = Field(description='Series identities with their confirmed links.')
    note: str = Field(description='Fixed explanatory sentence about identity links and absent volumes. Display only.')


# ------------------------------------------------------------------ shared error codes

SERIES_ID = 'Series ID.'
BOOK_ID = 'Book ID.'

NO_SERIES = {'series_not_found': 'No series has this ID.'}
NO_BOOK = {'book_not_found': 'No book has this ID.'}
ARCHIVED = {'series_archived': 'The series is removed (archived). Restore it first; reads still work.'}
RUN_ACTIVE = {'series_run_active': 'This series has an active processing run, or an active series run reserves one '
                                   'of its books. Wait for it or cancel it.'}
MEMBER_BUSY = {'job_active': 'A job is working on one of its books (including a removed one). Wait for it or cancel it.'}
BOOK_BUSY = {'book_archived': 'The book is removed (archived). Restore it first.',
             'job_active': 'A job is working on this book. Wait for it or cancel it.',
             'series_run_active': 'An active series run reserves this book, or the target series has an active run.'}
NAME_INVALID = {'name_invalid': 'The name is blank after whitespace is trimmed, or longer than 200 characters.'}
NAME_TAKEN = {'series_name_taken': 'Another series (active or removed) already has this name, ignoring case.'}
IDEMPOTENT = ('Idempotent: when the series is already in the requested state, the call returns that state and changes '
              'nothing, performs no checks and records nothing.')

OPS: list[Op] = [
    op('GET', '/api/series', 'listSeries', TAG, 'List active series',
       'Returns every active (non-removed) series ordered by name (case-insensitive), then ID, each with its supplied '
       'books in reading order, all volume slots (supplied, missing and planned) and its character-identity count. '
       'Removed series are omitted; use `GET /api/library?include_archived=true` to list them. A removed series can '
       'still be read by ID through `getSeriesMap`, `listSeriesRuns` and `listSeriesCharacters`.',
       response=list[Series]),

    op('POST', '/api/series', 'createSeries', TAG, 'Create a series',
       'Creates an empty series. Whitespace in the name is collapsed. The name is validated as for a rename '
       '(control characters other than tab and newlines are refused). Names are unique ignoring case, including '
       'against removed series. Not idempotent: each call creates a new ID. The response is a shorter shape than '
       '`Series` (no `archived` or `volumes`).',
       response=SeriesCreated,
       errors={400: {**NAME_INVALID, **NAME_TAKEN,
                     'text_invalid': 'The name contains control characters other than tab and newlines.'}}),

    op('PATCH', '/api/series/{series_id}', 'renameSeries', TAG, 'Rename a series',
       'Renames an active series when its work is idle: no active run of this series, no job on any of its books '
       '(removed ones included), and no series run holding one of its books. Whitespace is collapsed. The change is '
       "recorded in each member book's series provenance. Returns only `{id, name}`.",
       response=SeriesRenamed,
       params={'series_id': SERIES_ID},
       errors={400: {**NAME_INVALID, **NAME_TAKEN,
                     'text_invalid': 'The name contains control characters other than tab and newlines.'},
               404: NO_SERIES,
               409: {**ARCHIVED, **RUN_ACTIVE, **MEMBER_BUSY}}),

    op('POST', '/api/series/{series_id}/archive', 'archiveSeries', TAG, 'Remove a series',
       'Removes (archives) the series from normal views. Nothing is deleted: memberships, placeholders, identities '
       'and history are retained for restoration, and member books remain independently available in the library. '
       'While removed, series edits, processing and membership or identity-link changes are refused with 409 '
       '`series_archived`; reads (`getSeriesMap`, `listSeriesRuns`, `listSeriesCharacters`) still work. Removing '
       'requires the series to be idle (as for `renameSeries`) and records a library-visibility artifact on each '
       f'member book. {IDEMPOTENT} No request body.',
       response=SeriesArchiveState,
       params={'series_id': SERIES_ID},
       errors={404: NO_SERIES,
               409: {**RUN_ACTIVE, **MEMBER_BUSY}}),

    op('POST', '/api/series/{series_id}/restore', 'restoreSeries', TAG, 'Restore a removed series',
       'Restores a removed series. Restoring requires the series to be idle (as for `renameSeries`) and records a '
       f'library-visibility artifact on each member book. {IDEMPOTENT} No request body.',
       response=SeriesArchiveState,
       params={'series_id': SERIES_ID},
       errors={404: NO_SERIES,
               409: {**RUN_ACTIVE, **MEMBER_BUSY}}),

    op('PUT', '/api/series/{series_id}/volumes', 'putSeriesVolume', TAG, 'Add or update a volume placeholder',
       'Creates a placeholder for a volume the library does not have, or replaces the title and status of the '
       'placeholder already at that position (upsert keyed by position). A placeholder holds reading order only; it '
       'never contributes text or knowledge, and missing or planned volumes do not block a series run. Assigning a '
       'real book to the same position later replaces the placeholder. Requires an active, idle series (as for '
       '`renameSeries`).',
       response=SeriesVolumeSlot,
       params={'series_id': SERIES_ID},
       errors={400: {'position_taken': 'A supplied book (including a removed one) already has this position.',
                     'text_invalid': 'The title contains control characters other than tab and newlines.'},
               404: NO_SERIES,
               409: {**ARCHIVED, **RUN_ACTIVE, **MEMBER_BUSY}}),

    op('DELETE', '/api/series/{series_id}/volumes/{position}', 'deleteSeriesVolume', TAG, 'Remove a volume placeholder',
       'Removes the placeholder at `position`. It never removes or detaches a supplied book. Idempotent: returns '
       '`removed: true` even when no placeholder was at that position. Requires an active, idle series (as for '
       '`renameSeries`).',
       response=SeriesVolumeRemoval,
       params={'series_id': SERIES_ID,
               'position': 'Placeholder position as a decimal number, for example `3` or `2.5`. It must equal the '
                           'stored number exactly.'},
       errors={400: {'position_invalid': 'The position is negative, above 1,000,000 or not finite.'},
               404: NO_SERIES,
               409: {**ARCHIVED, **RUN_ACTIVE, **MEMBER_BUSY}}),

    op('GET', '/api/series/{series_id}/characters', 'listSeriesCharacters', TAG, 'List series character identities',
       'Returns the explicit cross-book identities of a series, ordered by name (case-insensitive) then ID, each '
       'with its confirmed book-character links. Works for removed series too.',
       response=list[SeriesCharacter],
       params={'series_id': SERIES_ID},
       errors={404: NO_SERIES}),

    op('POST', '/api/series/{series_id}/characters', 'createSeriesCharacter', TAG, 'Create a series character identity',
       'Creates a series-level identity without linking or merging any book character; link book characters with '
       '`linkSeriesCharacter`. Duplicate names are allowed because a shared name is not a shared identity. Not '
       'idempotent. Refused for a removed series and while the series has an active run.',
       response=SeriesCharacter,
       params={'series_id': SERIES_ID},
       errors={400: NAME_INVALID,
               404: NO_SERIES,
               409: {**ARCHIVED, 'series_run_active': 'This series has an active processing run.'}}),

    op('GET', '/api/books/{book_id}/series', 'getBookSeries', TAG, "Get a book's series placement",
       'Returns `{membership, series, links, characters}`. `membership` and `series` are null when the book is in '
       'no series or when the book or its series is removed.',
       response=BookSeries,
       params={'book_id': BOOK_ID},
       errors={404: NO_BOOK}),

    op('PUT', '/api/books/{book_id}/series', 'setBookSeries', TAG, "Set or clear a book's series",
       "Places the book in a series at a reading order, moves it, or detaches it, and returns the refreshed "
       '`getBookSeries` envelope.\n\n'
       'Send `{"series_id": "SERIES_ID", "position": 9}` with a JSON number, not a numeric string. Send '
       '`{"series_id": null}` (position null or omitted) to detach.\n\n'
       '- Positions are finite numbers from 0 through 1,000,000; decimals support prequels and side stories. A '
       'position already used by another supplied book of the series is rejected.\n'
       '- Assigning a book at a placeholder position replaces that placeholder.\n'
       "- Detaching, or moving to another series, deletes the book's identity links. Moving within the same series "
       'keeps them.\n'
       '- Removed (archived) membership and history are retained for restoration.\n\n'
       'Refused while the book is removed, has an active job or is held by a series run, and while the target '
       "series is removed or has an active run. Each change is recorded in the book's series provenance.",
       response=BookSeries,
       params={'book_id': BOOK_ID},
       errors={400: {'unknown_series': 'No series has the `series_id` in the body.',
                     'position_invalid': '`series_id` is given without a finite `position` from 0 through 1,000,000.',
                     'position_without_series': '`position` is given without `series_id`.',
                     'position_taken': 'Another supplied book of the series already has that position.'},
               404: NO_BOOK,
               409: {**BOOK_BUSY, 'series_archived': 'The target series is removed (archived). Restore it first.'}}),

    op('PUT', '/api/books/{book_id}/series/characters/{character_id}', 'linkSeriesCharacter', TAG,
       'Link or unlink a book character to a series identity',
       'With `{"series_character_id": "ID"}`, confirms that the book character is that series identity '
       '(replacing any previous link for the character) and returns the link. Re-linking the same identity keeps the '
       'original `confirmed_at`. With `{"series_character_id": null}` (or an empty body `{}`), removes any link and '
       'returns `{character_id, linked: false}`; unlinking is idempotent and does not check that the character '
       'exists.\n\n'
       "The book must be in a series, the series must not be removed (for unlinking too: removal retains links "
       'for restoration), and the identity must belong to that series. Narrator and unassigned cannot become series '
       'identities. Only confirmed links carry knowledge across books; names alone never do. Refused while the book '
       "is removed, has an active job or is held by a series run. Each change is recorded in the book's series "
       'provenance.',
       response=Union[SeriesCharacterLinkState, SeriesCharacterUnlinked],
       params={'book_id': BOOK_ID, 'character_id': 'Book-local character ID.'},
       errors={400: {'character_not_linkable': 'The character is `narrator` or `unassigned`.',
                     'book_not_in_series': 'Linking: the book is in no series.',
                     'unknown_series_character': 'No series character has the `series_character_id` in the body.',
                     'series_character_mismatch': "The identity belongs to another series than the book's."},
               404: {**NO_BOOK, 'character_not_found': 'Linking: the book has no character with `character_id`.'},
               409: {**BOOK_BUSY, 'series_archived': "The book's series is removed (archived). Restore it first."}}),

    op('GET', '/api/books/{book_id}/series/context', 'getBookSeriesContext', TAG,
       'Preview earlier-volume context for a book',
       'Reads the bounded context that analysis of this book receives from strictly earlier volumes, without model '
       'calls. It includes only observations of characters with confirmed links, from active earlier books of the '
       'same active series, whose quotes still match the current source text; name mentions, later volumes, '
       'unconfirmed links, removed or unavailable volumes and invalidated evidence are excluded. Missing volumes '
       'contribute nothing.\n\n'
       'The bound (at most 12,000 serialized characters and 8 observations per character) is an implementation '
       'choice, not a request parameter. `fingerprint` is stable while the inputs are unchanged. All historical '
       'observations remain retained even when omitted here.',
       response=BookSeriesContext,
       params={'book_id': BOOK_ID},
       errors={404: NO_BOOK}),

    op('POST', '/api/series/{series_id}/plan', 'planSeriesProcessing', TAG, 'Preview a series analysis run',
       'Previews staged analysis over the supplied, active books of an active series in reading order, without '
       'sending provider requests. Accepts `provider`, `phase`, `concurrency` and the same `limits` object used for '
       'per-book analysis. Providers must be cloud analysis providers (`gemini`, `openai` or `anthropic`); when '
       'omitted, the configured analysis provider is used, and a local provider setting is refused. Models come from '
       'runtime settings. Concurrency defaults to 2, is limited to 1 or 2, and applies to discovery only.\n\n'
       'The response lists ordered supplied books with nested book plans, models, known requests and cost, volume '
       'slots, `limits_per_book`, notes and `plan_fingerprint`. Limits apply separately to each supplied book, so '
       'the possible collection-wide spend grows with the number of books. The plan can be empty when the series '
       'has no active books (starting it is then refused). The preview creates no jobs.\n\n'
       'Side effects, all local and none of them changing a book, the same as '
       '`POST /api/books/{book_id}/analysis-plan` for each planned book: it caches and retains the census (a '
       '`census` artifact, and a `census` resource operation when computed fresh), imports validated discovery '
       'found only in an older checkpoint into the unit cache (with its artifacts), and retains `series_context` '
       'artifacts for linked earlier volumes.',
       response=SeriesPlan,
       params={'series_id': SERIES_ID},
       errors={400: {'provider_not_cloud': 'The provider (or the configured analysis provider) is not `gemini`, '
                                           '`openai` or `anthropic`.'},
               404: NO_SERIES,
               409: ARCHIVED}),

    op('POST', '/api/series/{series_id}/process', 'startSeriesProcessing', TAG, 'Start a series analysis run',
       'Queues a series run and returns its parent job immediately. Send the same body as the preview, adding the '
       'exact `plan_fingerprint` it returned as `expected_plan_fingerprint`.\n\n'
       'The server recomputes the plan under its store lock and compares the supplied fingerprint **before creating '
       'jobs**. The fingerprint covers the plan, book revisions and source hashes, and relevant series context. A '
       'mismatch returns 409 `plan_stale` (as the step pipeline does) and queues no processing. Re-preview and '
       'review the new scope; do not silently replace the fingerprint and retry. The fingerprint is optional for '
       'direct API clients (omitting it skips the check), but the UI requires a nonempty accepted fingerprint and '
       'consumes its preview on dispatch. This is optimistic scope validation, not a reservation that freezes data '
       'between requests.\n\n'
       '**Jobs.** The parent job has `kind: "series"` and `book_id: "series:SERIES_ID"`; `total` is the number of '
       'books. One child `analyze` job per supplied active book uses the real book ID and is created queued. '
       'Follow them with `GET /api/series/{series_id}/runs` or `GET /api/jobs`. While the run is active its books '
       'are reserved: edits to them and to the series are refused with 409. Cancelling the parent '
       '(`POST /api/jobs/{job_id}/cancel`) also stops its children.\n\n'
       '**Execution.** Discovery (`scan`, and the first part of `full`) may run on two independent books at once; '
       'profiles and direction run one book at a time in reading order. Missing, planned and removed volumes do not '
       'run. A failed or allowance-limited book stops new work; queued or running children then end `interrupted` '
       '(or `cancelled`), and already finished outputs remain reusable. Full-run phases share each book\'s run '
       'request and token caps, while its dollar allowance includes earlier tracked spend. Outcomes appear in the '
       'jobs, not in this response. A run record is retained as a `series_run` artifact on each book. When the run '
       "is queued, each supplied book's current projection is recorded in the step pipeline's version history (as "
       '`baseline` or `external` versions) when the history does not already explain it, so the replaced state '
       'stays restorable.',
       response=Job,
       response_description='The queued parent series job.',
       params={'series_id': SERIES_ID},
       errors={400: {'provider_not_cloud': 'The provider (or the configured analysis provider) is not `gemini`, '
                                           '`openai` or `anthropic`.',
                     'series_empty': 'The series has no supplied, active book.',
                     'api_key_missing': 'No API key is configured for the provider.'},
               404: NO_SERIES,
               409: {**ARCHIVED,
                     'plan_stale': '`expected_plan_fingerprint` does not match the recomputed plan. Nothing was queued.',
                     'series_run_active': 'This series already has an active run, or another series run holds one of '
                                          'its books.',
                     'job_active': 'A job is working on one of its supplied books.'},
               503: {'shutting_down': 'The series worker is not accepting work because the server is shutting down. '
                                      'The jobs just created are marked failed or interrupted and nothing runs.'}},
       cost='may_charge'),

    op('GET', '/api/series/{series_id}/runs', 'listSeriesRuns', TAG, 'List recent series runs',
       'Returns `{"runs": [...]}` with up to 20 parent series jobs of this series, newest first, each with its child '
       'job records embedded as `children`. The parent uses `book_id: "series:SERIES_ID"`; children use real book '
       'IDs. Poll this route (or `GET /api/jobs`) to follow a run. Works for removed series too.',
       response=SeriesRuns,
       params={'series_id': SERIES_ID},
       errors={404: NO_SERIES}),

    op('GET', '/api/series/{series_id}/map', 'getSeriesMap', TAG, 'Get the series map',
       'Returns `{series, characters, note}`: the series with its supplied, missing and planned volumes, and its '
       'explicit identities with confirmed links. Only confirmed identity links join characters across supplied '
       'titles; absent volumes contribute no inferred evidence. Works for removed series too (`series.archived` is '
       'then true).',
       response=SeriesMap,
       params={'series_id': SERIES_ID},
       errors={404: NO_SERIES}),
]


REQUEST_DOCS: dict[str, dict[str, str]] = {
    'SeriesNameRequest': {
        '__doc__': 'A name for a series or a series character identity.',
        'name': '1–200 characters. Runs of whitespace are collapsed to one space and the ends trimmed; a name that is '
                'blank after trimming is refused (400 `name_invalid`).',
    },
    'SeriesVolumeRequest': {
        '__doc__': 'A placeholder for a volume the library does not have.',
        'position': 'Required reading order, a finite number from 0 through 1,000,000. Must not be used by a supplied '
                    'book of the series (including a removed one). An existing placeholder at this position is '
                    'replaced.',
        'title': 'Optional label, at most 500 characters; default empty. Whitespace is collapsed; control characters '
                 'other than tab and newlines are refused.',
        'status': '`missing` (default): the volume exists but is not in the library. `planned`: not yet available.',
    },
    'SeriesMembershipRequest': {
        '__doc__': "A book's series placement. Send both values null (or `{}`) to detach the book.",
        'series_id': 'Target series ID, or null to detach.',
        'position': 'Reading order as a JSON number (a numeric string is refused with 422): finite, 0 through '
                    '1,000,000; decimals allow prequels and side stories. Required when `series_id` is set; must be '
                    'null when it is not.',
    },
    'SeriesCharacterLinkRequest': {
        '__doc__': 'The series identity to link a book character to.',
        'series_character_id': "A series character ID from the book's series, or null (the default) to unlink.",
    },
    'SeriesProcessingRequest': {
        '__doc__': 'Scope of a series analysis preview or run. Send the same body to preview and to start, adding the '
                   'reviewed fingerprint when starting.',
        'provider': 'Cloud analysis provider: `gemini`, `openai` or `anthropic`. Null uses the configured analysis '
                    'provider, which must itself be a cloud provider. Other values are refused (400 '
                    '`provider_not_cloud`).',
        'phase': '`scan` (default): discovery only. `profiles`: refine character profiles from retained evidence and '
                 'confirmed earlier-series context. `direct`: performance direction. `full`: all three in order.',
        'concurrency': 'Parallel discovery workers, 1 or 2 (default 2). Profiles and direction always run one book '
                       'at a time in reading order.',
        'limits': 'Per-book analysis limits, applied separately to each supplied book.',
        'expected_plan_fingerprint': 'The `plan_fingerprint` from the reviewed preview (at most 64 characters). '
                                     'Used only by the start route; when present and different from the recomputed '
                                     'plan, nothing is queued (409 `plan_stale`). Omitting it skips the check. '
                                     'Ignored by the preview route.',
    },
}
