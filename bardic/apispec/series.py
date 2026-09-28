"""Contract entries for the series route family.

A series groups supplied books in a reading order (``position``), adds
placeholders for volumes the library does not have, holds explicit
cross-book character identities, and runs the step pipeline over its books
in reading order.

The library snapshot's ``series`` entries are built by the same server code
as ``Series`` here (the library family describes them separately).
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field

from .base import Op, View, internal, op
from .common import Job, PipelineStepConfigView
from .pipeline import PipelinePlan, PipelineProviderId, PipelineRunOutcome, RunStatus, StepId

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
    archived: bool = Field(description='True when the series is removed. Series routes list only active series, so '
                                       'this is false there; the library snapshot with `include_archived=true` can '
                                       'include removed ones.')
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
    source_hash: str = internal('SHA-256 of the chapter text the observation was validated against.')
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
    context_chars: int = Field(description='Length in characters of the JSON-serialized `characters` array; at most '
                                           '12,000 with the current server bound.')
    fingerprint: str = Field(
        description='Stable SHA-256 hex digest of the dependencies (membership, links, earlier sources) and the '
                    'included context. It changes when anything that would change the context changes; it does not '
                    'use timestamps.')


class SeriesPlanBook(View):
    """One supplied, active book in a series plan, with its own step-pipeline plan."""
    book_id: str = Field(description='Book ID.')
    title: str = Field(description='Book title ("Untitled" when the book has none).')
    position: float = Field(description=POSITION)
    fingerprint: str = Field(description="This book's plan `fingerprint` (the same as `plan.fingerprint`). The series "
                                         'worker recomputes it before the book starts and does not run the book when it '
                                         'differs.')
    plan: PipelinePlan = Field(description="The book's step-pipeline plan for the requested steps, `configs` and "
                                           '`fresh`, over every eligible chapter: exactly what '
                                           '`planBookAnalysisPipelineRun` returns for that book.')


class SeriesMissingCredential(View):
    """A provider the planned steps would contact that has no API key or server URL configured."""
    provider: PipelineProviderId = Field(description='Pipeline provider ID.')
    label: str = Field(description='Display name of the provider.')
    needs: Literal['api_key', 'url'] = Field(description='What to add in Settings: an API key (cloud provider) or a server '
                                                         'URL (self-hosted provider).')


class SeriesPlan(View):
    """A read-only preview of a series run: each supplied, active book's step-pipeline plan in reading order,
    the summed estimate and the fingerprint that confirms it."""
    series_id: str = Field(description='Series ID.')
    name: str = Field(description='Series name.')
    plan_version: int = Field(description='Version of the series plan format, part of the fingerprint. Currently 2.')
    steps: list[StepId] = Field(description='Requested step IDs, deduplicated, in pipeline order.')
    configs: dict[StepId, PipelineStepConfigView] = Field(
        description="`{step ID: {provider, model}}` resolved for every requested step: the request's `configs` entry, "
                    'otherwise the saved step setting. The same configuration applies to every book.')
    fresh: bool = Field(description="Echo of the request's `fresh`. Part of the fingerprint.")
    books: list[SeriesPlanBook] = Field(
        description='Supplied, active books in reading order (position, then book ID). Empty when the series has none; '
                    'starting it is then refused.')
    volumes: list[SeriesVolume] = Field(description="The series' volume slots, as in `Series.volumes`.")
    skipped_volumes: list[SeriesVolume] = Field(
        description='The slots that will not run: missing and planned placeholders and removed (archived) supplied '
                    'books, in reading order. Nothing is inferred about them.')
    requests: int = Field(description="Sum of the books' `requests`: model requests to send, before retries or evidence "
                                      'repairs.')
    cached_units: int = Field(description="Sum of the books' `cached_units`.")
    service_calls: int = Field(description="Sum of the books' `service_calls` (free calls to self-hosted services).")
    estimated_input_tokens: int = Field(description="Sum of the books' `estimated_input_tokens`.")
    output_token_allowance: int = Field(description="Sum of the books' `output_token_allowance` (output token caps).")
    estimated_cost_usd: float | None = Field(
        description="Sum of the books' `estimated_cost_usd` in USD, rounded to 6 decimals; null when any book's cost is "
                    'unknown (an unknown price is never counted as zero); 0 when `books` is empty. Approximate; not an '
                    'invoice.')
    known_cost_usd: float = Field(description="Sum of the books' known `estimated_cost_usd` in USD, rounded to 6 "
                                              'decimals. A lower bound when `unknown_cost_books` is not empty.')
    unknown_cost_books: list[str] = Field(description='Book IDs whose estimate is unknown (null), in reading order.')
    missing_inputs: dict[str, dict[StepId, list[StepId]]] = Field(
        description="`{book ID: {step: [required inputs]}}` for the books whose plan reports `missing_inputs`; books "
                    'without any are omitted. Starting the run is refused (400) while this is not empty.')
    missing_credentials: list[SeriesMissingCredential] = Field(
        description='Providers the requested steps would contact that have no API key or server URL configured, sorted '
                    'by provider ID. Local steps and offline providers need none. Starting the run is refused (400) '
                    'while this is not empty.')
    notes: list[str] = Field(description='Human-readable caveats. Display only.')
    fingerprint: str = Field(
        description='Opaque series plan identity (SHA-256 hex) over the plan version, series, steps, resolved `configs`, '
                    "`fresh` and every book's ID, position and plan fingerprint. Send it as `expected_fingerprint` to "
                    'start exactly this plan.')


class SeriesChildRun(View):
    """A summary of the pipeline run a series child executes."""
    id: str = Field(description='Pipeline run ID (the child job\'s `run_id`).')
    status: RunStatus = Field(description='The run status, as in `PipelineRun.status`.')
    outcomes: dict[StepId, PipelineRunOutcome] | None = Field(
        description='Per-step outcome keyed by step ID, or null until the run finishes.')
    error: str | None = Field(description='Human-readable failure text, or null. Display only.')


class SeriesRunChild(Job):
    """A child job of a series run, with a summary of its book's pipeline run."""
    run: SeriesChildRun | None = Field(
        None, description='Present once the child has a `run_id`: the run\'s `{id, status, outcomes, error}`, or null '
                          'when that run record no longer exists. Absent while the book has not started, and on '
                          'children that never started.')


class SeriesRun(Job):
    """A series parent job with its child jobs."""
    children: list[SeriesRunChild] = Field(
        description='The child jobs, one per supplied book, in reading order (`pipeline` jobs; `analyze` jobs in runs '
                    'recorded before contract 0.2.0). They use real book IDs.')


class SeriesRuns(View):
    """Recent series runs."""
    runs: list[SeriesRun] = Field(description='Up to 20 parent runs, newest first.')


class SeriesMap(View):
    """A series with its volumes and explicit identities."""
    series: Series = Field(description='The series entry, including supplied, missing and planned volumes.')
    characters: list[SeriesCharacter] = Field(description='Series identities with their confirmed links.')
    note: str = Field(description='Fixed explanatory sentence about identity links and absent volumes. Display only.')


# ------------------------------------------------------------------ shared error text

SERIES_404 = ('The series does not exist, or it is removed (archived): removed series are reported as not found '
              'here. Detail: "Series not found".')
SERIES_BUSY_409 = ('A job is active on one of its supplied books, a series run reserves one of its books, or this '
                   'series has an active run. Wait for it or cancel it.')

SERIES_ID = 'Series ID.'
BOOK_ID = 'Book ID.'

OPS: list[Op] = [
    op('GET', '/api/series', 'listSeries', TAG, 'List active series',
       'Returns every active (non-removed) series ordered by name (case-insensitive), then ID, each with its supplied '
       'books in reading order, all volume slots (supplied, missing and planned) and its character-identity count. '
       'Removed series are omitted; use `GET /api/library?include_archived=true` to see them.',
       response=list[Series]),

    op('POST', '/api/series', 'createSeries', TAG, 'Create a series',
       'Creates an empty series. Whitespace in the name is collapsed. Names are unique ignoring case, including '
       'against removed series. Not idempotent: each call creates a new ID. The response is a shorter shape than '
       '`Series` (no `archived` or `volumes`).',
       response=SeriesCreated,
       errors={400: 'The name is blank after whitespace is trimmed, or another series (active or removed) already '
                    'has that name ignoring case.'}),

    op('PATCH', '/api/series/{series_id}', 'renameSeries', TAG, 'Rename a series',
       'Renames the series when its work is idle: no job on any supplied active book, no series run holding one of '
       'its books, and no active run of this series. Whitespace is collapsed. The change is recorded in each '
       "member book's series provenance. Returns only `{id, name}`.",
       response=SeriesRenamed,
       params={'series_id': SERIES_ID},
       errors={400: 'The name is blank or contains control characters, another series already has it ignoring case, '
                    'or a removed member book still has an active job.',
               404: SERIES_404,
               409: SERIES_BUSY_409}),

    op('POST', '/api/series/{series_id}/archive', 'archiveSeries', TAG, 'Remove a series',
       'Removes (archives) the series from normal views. Nothing is deleted: memberships, placeholders, identities '
       'and history are retained for restoration, and member books remain independently available in the library. '
       'While removed, the series is refused by the series routes that look it up (404) and by membership changes '
       '(400). Records a library-visibility artifact on each member book. Removing an already removed series '
       'returns 404. No request body.',
       response=SeriesArchiveState,
       params={'series_id': SERIES_ID},
       errors={400: 'A member book (including a removed one) has an active job.',
               404: SERIES_404,
               409: SERIES_BUSY_409}),

    op('POST', '/api/series/{series_id}/restore', 'restoreSeries', TAG, 'Restore a removed series',
       'Restores a removed series. Idempotent: restoring an active series succeeds and changes nothing (a '
       'library-visibility artifact is still recorded on each member book). Unlike the other series edits it does '
       'not check for an active series run; it only requires that no member book has an active job. No request '
       'body.',
       response=SeriesArchiveState,
       params={'series_id': SERIES_ID},
       errors={400: 'A member book (including a removed one) has an active job.',
               404: 'The series does not exist.'}),

    op('PUT', '/api/series/{series_id}/volumes', 'putSeriesVolume', TAG, 'Add or update a volume placeholder',
       'Creates a placeholder for a volume the library does not have, or replaces the title and status of the '
       'placeholder already at that position (upsert keyed by position). A placeholder holds reading order only; it '
       'never contributes text or knowledge, and missing or planned volumes do not block a series run. Assigning a '
       'real book to the same position later replaces the placeholder. Requires the series to be idle.',
       response=SeriesVolumeSlot,
       params={'series_id': SERIES_ID},
       errors={400: 'A supplied book (including a removed one) already occupies that position, or the title '
                    'contains control characters.',
               404: SERIES_404,
               409: SERIES_BUSY_409}),

    op('DELETE', '/api/series/{series_id}/volumes/{position}', 'deleteSeriesVolume', TAG, 'Remove a volume placeholder',
       'Removes the placeholder at `position`. It never removes or detaches a supplied book. Idempotent: returns '
       '`removed: true` even when no placeholder was at that position. Requires the series to be idle.',
       response=SeriesVolumeRemoval,
       params={'series_id': SERIES_ID,
               'position': 'Placeholder position as a decimal number, for example `3` or `2.5`. It must equal the '
                           'stored number exactly.'},
       errors={400: 'The position is negative, above 1,000,000 or not finite.',
               404: SERIES_404,
               409: SERIES_BUSY_409}),

    op('GET', '/api/series/{series_id}/characters', 'listSeriesCharacters', TAG, 'List series character identities',
       'Returns the explicit cross-book identities of a series, ordered by name (case-insensitive) then ID, each '
       'with its confirmed book-character links. Works for removed series too.',
       response=list[SeriesCharacter],
       params={'series_id': SERIES_ID},
       errors={404: 'The series does not exist.'}),

    op('POST', '/api/series/{series_id}/characters', 'createSeriesCharacter', TAG, 'Create a series character identity',
       'Creates a series-level identity without linking or merging any book character; link book characters with '
       '`linkSeriesCharacter`. Duplicate names are allowed because a shared name is not a shared identity. Not '
       'idempotent. Not refused for removed series or during series runs.',
       response=SeriesCharacter,
       params={'series_id': SERIES_ID},
       errors={400: 'The name is blank after whitespace is trimmed.',
               404: 'The series does not exist.'}),

    op('GET', '/api/books/{book_id}/series', 'getBookSeries', TAG, "Get a book's series placement",
       'Returns `{membership, series, links, characters}`. `membership` and `series` are null when the book is in '
       'no series or when the book or its series is removed.',
       response=BookSeries,
       params={'book_id': BOOK_ID},
       errors={404: 'The book does not exist.'}),

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
       'Refused while the book has an active job, while a series run holds the book, or while the target series has '
       'an active run. Each change is recorded in the book\'s series provenance.',
       response=BookSeries,
       params={'book_id': BOOK_ID},
       errors={400: 'The book is removed; the target series is removed; `series_id` is given without a valid '
                    '`position`; `position` is given without `series_id`; or another supplied book already has that '
                    'position.',
               404: 'The book or the target series does not exist.',
               409: 'A job is active on this book, a series run holds it, or the target series has an active run.'}),

    op('PUT', '/api/books/{book_id}/series/characters/{character_id}', 'linkSeriesCharacter', TAG,
       'Link or unlink a book character to a series identity',
       'With `{"series_character_id": "ID"}`, confirms that the book character is that series identity '
       '(replacing any previous link for the character) and returns the link. Re-linking the same identity keeps the '
       'original `confirmed_at`. With `{"series_character_id": null}` (or an empty body `{}`), removes any link and '
       'returns `{character_id, linked: false}`; unlinking is idempotent and does not check that the character '
       'exists.\n\n'
       "The book must be in an active series and the identity must belong to that series. Narrator and unassigned "
       'cannot become series identities. Only confirmed links carry knowledge across books; names alone never do. '
       'Refused while the book has an active job or is held by a series run. Each change is recorded in the '
       "book's series provenance.",
       response=Union[SeriesCharacterLinkState, SeriesCharacterUnlinked],
       params={'book_id': BOOK_ID, 'character_id': 'Book-local character ID.'},
       errors={400: 'The book is removed; the character is `narrator` or `unassigned`; the book is in no active '
                    'series; or the identity belongs to another series.',
               404: 'The book, the book character (when linking) or the series character does not exist.',
               409: 'A job is active on this book, or a series run holds it.'}),

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
       errors={404: 'The book does not exist.'}),

    op('POST', '/api/series/{series_id}/plan', 'planSeriesProcessing', TAG, 'Preview a series analysis run',
       "Previews a series run: the step-pipeline plan of every supplied, active book of the series, in reading order, "
       'for the same `steps`, `configs` and `fresh`, and the summed estimate. No model or service calls.\n\n'
       "- Each book's `plan` is exactly what `planBookAnalysisPipelineRun` returns for that book over every eligible "
       'chapter, computed from that book\'s own accepted results. Accepted results in one book are not read by '
       'another.\n'
       "- Omitted `configs` entries use the saved step settings; the resolved `configs` apply to every book.\n"
       "- `estimated_cost_usd` is null when any book's cost is unknown; `known_cost_usd` and `unknown_cost_books` say "
       'what is priced. Estimates cover known work before retries or evidence repairs.\n'
       '- `missing_inputs` (per book) and `missing_credentials` do not fail the preview; starting the run is refused '
       'while either is not empty.\n'
       '- Missing and planned placeholders and removed books are listed in `skipped_volumes` and never run.\n'
       '- The `fingerprint` covers the plan version, series, steps, resolved `configs`, `fresh` and each book\'s ID, '
       'position and plan fingerprint (which covers the book revision and every unit\'s identity). It does not cover '
       '`mode`, `gates`, `concurrency` or `limits`.\n\n'
       'The plan can be empty when the series has no active books. Not purely read-only: for each book the server '
       'first records outside changes as the book pipeline overview does (`projection.sync`), and building units may '
       'store free local census caches.',
       response=SeriesPlan,
       params={'series_id': SERIES_ID},
       errors={400: '`steps` names an unknown step ID (400 here, where the book pipeline returns 404), or a `configs` '
                    'entry is invalid for its step.',
               404: SERIES_404}),

    op('POST', '/api/series/{series_id}/process', 'startSeriesProcessing', TAG, 'Start a series analysis run',
       'Queues a series run and returns its parent job immediately. Send the same `steps`, `configs` and `fresh` as '
       'the reviewed preview, with its `fingerprint` as `expected_fingerprint`.\n\n'
       '**Checks, in order.** The server recomputes the plan under its store lock, then refuses the run when:\n\n'
       '1. the series has no supplied active book (400);\n'
       '2. neither `expected_fingerprint` nor any `limits` value was sent (400);\n'
       '3. `expected_fingerprint` differs from the recomputed plan (409; preview again and review the new scope rather '
       'than replacing the fingerprint and retrying);\n'
       "4. any book's plan has `missing_inputs` (400; the message names the steps, inputs and books);\n"
       '5. a provider the steps contact has no API key or server URL configured (400, `Add in Settings first: …`);\n'
       '6. the series already has an active run (409);\n'
       '7. a book has an active job or is reserved by another series run (409).\n\n'
       'Nothing is queued when any check fails. The fingerprint is optimistic scope validation, not a reservation '
       'that freezes data between requests. `limits` are optional caps for API callers; the confirmed fingerprint is '
       'the authorization, and every paid HTTP attempt is still reserved and recorded by the pipeline runner.\n\n'
       '**Jobs.** The parent job has `kind: "series"`, `book_id: "series:SERIES_ID"` and `total` equal to the number '
       'of books. One child job of kind `pipeline` per book uses the real book ID and is created queued with '
       '`series_run_id`, `position`, `title`, `plan_fingerprint` (that book\'s confirmed plan) and `run_id: null`. '
       'Provider keys and server URLs, per-step provider/model and gates are snapshotted now. Follow the run with '
       '`listSeriesRuns` or `GET /api/jobs`. Until the parent ends every book is reserved: edits, membership changes, '
       'single-book runs and accepting versions on them get 409. Cancelling the parent '
       '(`POST /api/jobs/{job_id}/cancel`) cancels queued children at once and asks the running child to stop; '
       'cancelling a child stops the series at that book.\n\n'
       '**Execution.** Books run one at a time in reading order; `concurrency` is the number of model requests in '
       'flight inside the running book. Before each book starts, its plan is recomputed; if it no longer matches its '
       'confirmed fingerprint, that child fails with nothing sent and the series stops. Each book runs as one pipeline '
       'run (with `series_run_id` set), with the same candidates, gates and auto-accept as a run started from the book. '
       'The first child that does not complete stops the series with that status (`failed`, `budget_limited`, '
       '`quota_limited`, `cancelled` or `interrupted`); children that never started end `cancelled` (after a cancel) '
       'or `interrupted`, with `not_started: true`, and never start later. Validated units are cached per book, so '
       'running a stopped series again reuses paid work. A run record is retained as a `series_run` artifact on each '
       'book.',
       response=Job,
       response_description='The queued parent series job. Not a result: poll it until it is terminal.',
       params={'series_id': SERIES_ID},
       errors={400: '`steps` names an unknown step or a `configs` entry is invalid; the series has no supplied active '
                    'book; neither `expected_fingerprint` nor any limit was sent; a book lacks a required input that is '
                    'not in this run; a needed API key or server URL is missing; or the series worker could not start '
                    '(the jobs are then marked `failed`/`interrupted` and nothing runs).',
               404: SERIES_404,
               409: '`expected_fingerprint` does not match the recomputed plan (preview again); the series already has '
                    'an active run; or a book has an active job or is reserved by another series run.'},
       cost='may_charge'),

    op('GET', '/api/series/{series_id}/runs', 'listSeriesRuns', TAG, 'List recent series runs',
       'Returns `{"runs": [...]}` with up to 20 parent series jobs of this series, newest first. Each embeds its child '
       'jobs as `children`, in reading order; a child that has started its book also carries `run`, a summary of its '
       'pipeline run (`id`, `status`, per-step `outcomes`, `error`). The parent uses `book_id: "series:SERIES_ID"`; '
       'children use real book IDs. Poll this route (or `GET /api/jobs`) to follow a run. Read-only.',
       response=SeriesRuns,
       params={'series_id': SERIES_ID},
       errors={404: SERIES_404}),

    op('GET', '/api/series/{series_id}/map', 'getSeriesMap', TAG, 'Get the series map',
       'Returns `{series, characters, note}`: the series with its supplied, missing and planned volumes, and its '
       'explicit identities with confirmed links. Only confirmed identity links join characters across supplied '
       'titles; absent volumes contribute no inferred evidence.',
       response=SeriesMap,
       params={'series_id': SERIES_ID},
       errors={404: SERIES_404}),
]


REQUEST_DOCS: dict[str, dict[str, str]] = {
    'SeriesNameRequest': {
        '__doc__': 'A name for a series or a series character identity.',
        'name': '1–200 characters. Runs of whitespace are collapsed to one space and the ends trimmed; a name that is '
                'blank after trimming is refused with 400.',
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
    'SeriesPlanRequest': {
        '__doc__': 'Which steps to preview across the series, with which providers. Send the same `steps`, `configs` and '
                   '`fresh` to start the run: they are part of the fingerprint.',
        'steps': 'Step IDs to plan in every book (1–40). Order does not matter: steps are planned in pipeline order. '
                 'Duplicates are ignored. An unknown ID is refused with 400.',
        'configs': '`{step ID: StepConfig}` overriding the saved provider/model for this request, applied to every book. '
                   'Entries for steps not requested are ignored.',
        'fresh': 'When true, cached validated units are not reused in any book: new samples are requested. Part of the '
                 'fingerprint. Default false.',
    },
    'SeriesRunRequest': {
        '__doc__': 'A series run to queue: the reviewed preview\'s `steps`, `configs` and `fresh`, its `fingerprint`, and '
                   'run options applied to each book\'s pipeline run. There is no chapter selection; every eligible '
                   'chapter of each book is planned.',
        'steps': 'Step IDs to run in every book (1–40), executed in pipeline order. Duplicates are ignored. An unknown ID '
                 'is refused with 400.',
        'configs': '`{step ID: StepConfig}` overriding the saved provider/model, applied to every book. Entries for steps '
                   'not requested are ignored.',
        'fresh': 'Request new samples instead of reusing cached validated units (default false). Part of the fingerprint.',
        'mode': '`serial` (default) runs each book\'s steps one after another in pipeline order. `parallel` starts every '
                'step whose in-run inputs have finished. Books always run one at a time.',
        'gates': '`{step ID: "auto" | "review"}` overriding the saved gate for this run. With `review`, each book\'s '
                 'version waits in that book\'s Analysis tab.',
        'concurrency': 'Maximum model requests in flight inside the running book, 1–4 (default 2). Books run one at a '
                       'time. Each step also has its own `parallel` cap.',
        'limits': 'Optional caps applied separately to each book\'s run; see Limits. Uncapped when omitted. A limit '
                  'reached stops that book as `budget_limited` and stops the series.',
        'expected_fingerprint': 'The series plan `fingerprint` the owner confirmed (up to 64 characters). A mismatch '
                                'with the recomputed plan returns 409 and queues nothing. Required unless a limit is set.',
    },
}
