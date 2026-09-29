"""The library: listing, ordering, metadata, removal (archive) and restoration, search and exports."""
from __future__ import annotations

import io
import json
import re
import time
import zipfile

from . import helpers


def _ids(api, *, include_archived=False):
    return [book['id'] for book in api.call('getLibrary', query={'include_archived': include_archived}).json['books']]


# ---------------------------------------------------------------- listing

def test_list_books_is_newest_import_first(api):
    first = helpers.import_fresh_txt(api, 'older')
    time.sleep(1.1)  # import times may be kept to the second
    second = helpers.import_fresh_txt(api, 'newer')
    listed = [book['id'] for book in api.call('listBooks').json]
    assert listed.index(second['id']) < listed.index(first['id']), 'most recently imported first'
    assert _ids(api).index(second['id']) < _ids(api).index(first['id']), 'getLibrary orders books the same way'


def test_list_books_and_library_rows_are_the_same_shape_and_content(api, txt_book):
    listed = next(book for book in api.call('listBooks').json if book['id'] == txt_book['id'])
    assert listed == helpers.library_summary(api, txt_book['id'])


def test_library_snapshot_has_storage_measurements(api, txt_book):
    snapshot = api.call('getLibrary').json
    summary = next(book for book in snapshot['books'] if book['id'] == txt_book['id'])
    assert summary['storage']['original_bytes'] > 0, 'the original upload is kept'
    assert snapshot['storage']['data_directory_bytes'] > 0
    assert summary['archived'] is False and summary['archived_at'] is None


def test_demo_book_is_a_local_draft_without_an_original(api):
    demo = api.call('createDemoBook').json
    assert demo['analysis']['provider'] == 'local'
    helpers.assert_book_invariants(demo)
    summary = helpers.library_summary(api, demo['id'])
    assert summary['storage']['original_bytes'] == 0
    other = api.call('createDemoBook').json
    assert other['id'] != demo['id'], 'the demo is not idempotent'
    reply = api.call('refreshBookMetadata', path={'book_id': demo['id']}, expect=400)
    assert reply.code == 'original_unavailable'


# ---------------------------------------------------------------- metadata

def test_metadata_edit_normalises_whitespace_and_counts_a_revision(api, fresh_book):
    path = {'book_id': fresh_book['id']}
    summary = api.call('updateBookMetadata', path=path, json={'title': '  A   Renamed\t\nStory ', 'author': ' Some   One '}).json
    assert summary['title'] == 'A Renamed Story' and summary['author'] == 'Some One'
    book = api.call('getBook', path=path).json
    assert (book['title'], book['author']) == ('A Renamed Story', 'Some One')
    assert book['revision'] == fresh_book['revision'] + 1, 'a change increments the revision by 1'
    assert helpers.canonical_text(book) == helpers.canonical_text(fresh_book), 'metadata never changes the text'
    assert helpers.passage_anchors(book) == helpers.passage_anchors(fresh_book)


def test_metadata_edit_that_changes_nothing_saves_nothing(api, fresh_book):
    path = {'book_id': fresh_book['id']}
    api.call('updateBookMetadata', path=path, json={'title': fresh_book['title'], 'author': fresh_book['author']})
    assert api.call('getBook', path=path).json['revision'] == fresh_book['revision']


def test_metadata_edit_does_not_move_the_book_in_the_library(api):
    older = helpers.import_fresh_txt(api, 'stays_older')
    time.sleep(1.1)  # import times may be kept to the second
    newer = helpers.import_fresh_txt(api, 'stays_newer')
    api.call('updateBookMetadata', path={'book_id': older['id']}, json={'title': 'Edited later'})
    listed = [book['id'] for book in api.call('listBooks').json]
    assert listed.index(newer['id']) < listed.index(older['id']), 'saving a book does not change its place'


def test_metadata_validation(api, fresh_book):
    path = {'book_id': fresh_book['id']}
    api.call('updateBookMetadata', path=path, json={}, negative=True, expect=422)
    api.call('updateBookMetadata', path=path, json={'title': ''}, negative=True, expect=422)
    api.call('updateBookMetadata', path=path, json={'title': 'x' * 501}, negative=True, expect=422)
    api.call('updateBookMetadata', path=path, json={'title': 'ok', 'author': 'x' * 501}, negative=True, expect=422)
    api.call('updateBookMetadata', path=path, json={'title': 'ok', 'colour': 'blue'}, negative=True, expect=422)


def test_refresh_replaces_unreviewed_metadata_but_keeps_reviewed_fields(api, fresh_book):
    path = {'book_id': fresh_book['id']}
    api.call('updateBookMetadata', path=path, json={'title': 'My Own Title', 'author': fresh_book['author']})
    refreshed = api.call('refreshBookMetadata', path=path).json
    assert refreshed['title'] == 'My Own Title', 'a title changed by hand is never overwritten'
    book = api.call('getBook', path=path).json
    assert book['revision'] == fresh_book['revision'] + 2, 'a refresh always increments the revision'
    assert helpers.canonical_text(book) == helpers.canonical_text(fresh_book)


def test_refresh_restores_the_title_of_an_unedited_book(api, fresh_book):
    path = {'book_id': fresh_book['id']}
    before = api.call('getBook', path=path).json
    refreshed = api.call('refreshBookMetadata', path=path).json
    assert refreshed['title'] == before['title']
    assert api.call('getBook', path=path).json['revision'] == before['revision'] + 1


def test_repair_structure_keeps_text_ids_and_offsets(api, fresh_book):
    path = {'book_id': fresh_book['id']}
    repaired = api.call('repairBookStructure', path=path).json
    assert helpers.canonical_text(repaired) == helpers.canonical_text(fresh_book)
    assert helpers.passage_anchors(repaired) == helpers.passage_anchors(fresh_book)
    assert [s['id'] for s in repaired['scenes']] == [s['id'] for s in fresh_book['scenes']]
    assert repaired['revision'] == fresh_book['revision'] + 1, 'a repair increments the revision'
    helpers.assert_book_invariants(repaired)


def test_repair_structure_needs_a_saved_original(api):
    demo = api.call('createDemoBook').json
    assert api.call('repairBookStructure', path={'book_id': demo['id']}, expect=400).code == 'original_missing'


# ---------------------------------------------------------------- archive and restore

def test_archive_hides_a_book_without_deleting_it_and_restore_brings_it_back(api, fresh_book):
    path = {'book_id': fresh_book['id']}
    archived = api.call('archiveBook', path=path).json
    assert archived == {'id': fresh_book['id'], 'archived': True, 'retained': True}
    assert fresh_book['id'] not in [book['id'] for book in api.call('listBooks').json]
    assert fresh_book['id'] not in _ids(api)
    assert fresh_book['id'] in _ids(api, include_archived=True)
    row = helpers.library_summary(api, fresh_book['id'], include_archived=True)
    assert row['archived'] is True and row['archived_at']
    # Everything stays readable.
    assert api.call('getBook', path=path).json == fresh_book
    api.call('exportBookAnalysis', path=path)
    restored = api.call('restoreBook', path=path).json
    assert restored == {'id': fresh_book['id'], 'archived': False, 'retained': True}
    assert fresh_book['id'] in [book['id'] for book in api.call('listBooks').json]
    row = helpers.library_summary(api, fresh_book['id'])
    assert row['archived'] is False and row['archived_at'] is None


def test_archive_and_restore_are_idempotent(api, fresh_book):
    path = {'book_id': fresh_book['id']}
    assert api.call('restoreBook', path=path).json['archived'] is False, 'restoring a book that is not removed succeeds'
    first = api.call('archiveBook', path=path).json
    assert api.call('archiveBook', path=path).json == first
    assert api.call('restoreBook', path=path).json['archived'] is False
    assert api.call('restoreBook', path=path).json['archived'] is False


def test_a_removed_book_refuses_edits_until_restored(api, fresh_book):
    path = {'book_id': fresh_book['id']}
    api.call('archiveBook', path=path)
    for reply in (
        api.call('updateBookMetadata', path=path, json={'title': 'Nope'}, expect=409),
        api.call('refreshBookMetadata', path=path, expect=409),
        api.call('repairBookStructure', path=path, expect=409),
        api.call('addCharacter', path=path, json={'name': 'Nobody'}, expect=409),
    ):
        assert reply.code == 'book_archived', reply.summary()
    api.call('restoreBook', path=path)
    api.call('updateBookMetadata', path=path, json={'title': 'Fine now'})


def test_archive_and_restore_unknown_book_is_404(api):
    assert api.call('archiveBook', path={'book_id': 'no-such-book'}, expect=404).code == 'book_not_found'
    assert api.call('restoreBook', path={'book_id': 'no-such-book'}, expect=404).code == 'book_not_found'


def test_removing_and_restoring_a_book_records_history_without_touching_its_text(api, fresh_book):
    path = {'book_id': fresh_book['id']}
    api.call('archiveBook', path=path)
    api.call('restoreBook', path=path)
    book = api.call('getBook', path=path).json
    assert helpers.canonical_text(book) == helpers.canonical_text(fresh_book)
    kinds = {item['kind'] for item in api.call('listBookArtifacts', path=path, query={'limit': 200}).json['items']}
    assert 'library_state' in kinds, 'a call that removes or restores a book records a library_state artifact'


# ---------------------------------------------------------------- search

def test_search_finds_passages_by_words_combined_with_and(api, txt_book):
    path = {'book_id': txt_book['id']}
    result = api.call('searchBookPassages', path=path, query={'q': 'harbour clock'}).json
    assert result['query'] == 'harbour clock' and result['scope'] == 'book'
    assert result['items'], 'a passage containing both words matches'
    by_id = {passage['id']: passage for passage in txt_book['passages']}
    for item in result['items']:
        assert item['passage_id'] in by_id
        passage = by_id[item['passage_id']]
        assert passage['text'] == item['text'], 'a hit carries the exact source text'
        chapter = helpers.chapter_by_id(txt_book, passage['chapter_id'])
        assert chapter['text'][item['start']:item['end']] == item['text']
        assert 'harbour' in item['text'].lower() and 'clock' in item['text'].lower()
    none = api.call('searchBookPassages', path=path, query={'q': 'harbour zebra'}).json
    assert none['items'] == [], 'every word must match'


def test_search_is_case_insensitive_and_word_based(api, txt_book):
    path = {'book_id': txt_book['id']}
    lower = api.call('searchBookPassages', path=path, query={'q': 'twelve'}).json['items']
    upper = api.call('searchBookPassages', path=path, query={'q': 'TWELVE'}).json['items']
    assert lower and [i['passage_id'] for i in lower] == [i['passage_id'] for i in upper]


def test_search_query_without_words_returns_nothing_with_a_note(api, txt_book):
    result = api.call('searchBookPassages', path={'book_id': txt_book['id']}, query={'q': '!!! ???'}).json
    assert result['items'] == [] and result['note']


def test_search_limit_is_clamped_and_reported(api, txt_book):
    path = {'book_id': txt_book['id']}
    many = api.call('searchBookPassages', path=path, query={'q': 'the', 'limit': 1}).json
    assert len(many['items']) <= 1
    huge = api.call('searchBookPassages', path=path, query={'q': 'the', 'limit': 100000}).json
    assert len(huge['items']) <= len(txt_book['passages'])


def test_search_errors(api, txt_book):
    path = {'book_id': txt_book['id']}
    assert api.call('searchBookPassages', path={'book_id': 'no-such-book'}, query={'q': 'x'}, expect=404).code == 'book_not_found'
    api.call('searchBookPassages', path=path, query={}, negative=True, expect=422)
    reply = api.call('searchBookPassages', path=path, query={'q': 'harbour', 'scope': 'no-such-scope'}, expect=400)
    assert reply.code == 'search_scope_invalid'


# ---------------------------------------------------------------- exports

def test_analysis_export_is_a_zip_with_a_manifest(api, txt_book):
    reply = api.call('exportBookAnalysis', path={'book_id': txt_book['id']})
    assert reply.content_type == 'application/zip'
    assert 'attachment' in reply.headers.get('content-disposition', '').lower()
    with zipfile.ZipFile(io.BytesIO(reply.content)) as archive:
        names = set(archive.namelist())
        assert {'manifest.json', 'README.txt', 'book.json', 'story-map.json'} <= names
        manifest = json.loads(archive.read('manifest.json'))
        book = json.loads(archive.read('book.json'))
        story_map = json.loads(archive.read('story-map.json'))
    assert manifest['format'] == 'spintails-analysis' and manifest['schema_version'] == 2
    assert manifest['book_id'] == txt_book['id']
    assert manifest['audio_files_included'] is False and manifest['source_text_included'] is True
    assert [chapter['text'] for chapter in book['chapters']] == [chapter['text'] for chapter in txt_book['chapters']]
    assert story_map == api.call('getStoryMap', path={'book_id': txt_book['id']}).json, 'story-map.json is the getStoryMap body'


def test_range_requests_on_a_file_download(api, txt_book):
    """A file served from disk honours `Range`: 206 with Content-Range, or 416 (JSON `Error`) with `bytes */size`."""
    # The bundle is generated per request (it records its export time), so its size can differ by a byte
    # between requests: check each reply against its own Content-Range, not against another download.
    def span(reply):
        start, end, total = map(int, re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', reply.headers['content-range']).groups())
        assert end - start + 1 == len(reply.content), 'the body is exactly the range named in Content-Range'
        return start, end, total

    path = {'book_id': txt_book['id']}
    head = api.call('exportBookAnalysis', path=path, headers={'Range': 'bytes=0-9'}, expect=206)
    assert head.content[:4] == b'PK\x03\x04', 'the first bytes of a ZIP'
    start, end, total = span(head)
    assert (start, end) == (0, 9) and total > 10
    tail = api.call('exportBookAnalysis', path=path, headers={'Range': 'bytes=-16'}, expect=206)
    start, end, total = span(tail)
    assert end == total - 1 and start == total - 16, 'a suffix range is the last 16 bytes'
    open_ended = api.call('exportBookAnalysis', path=path, headers={'Range': 'bytes=20-'}, expect=206)
    start, end, total = span(open_ended)
    assert start == 20 and end == total - 1, 'an open range runs to the end'
    refused = api.call('exportBookAnalysis', path=path, headers={'Range': 'bytes=999999999-'}, expect=416)
    assert refused.code == 'range_not_satisfiable' and re.fullmatch(r'bytes \*/\d+', refused.headers['content-range'])


def test_audiobook_export_needs_audio(api, txt_book):
    reply = api.call('exportAudiobook', path={'book_id': txt_book['id']}, expect=400)
    assert reply.code == 'export_audio_missing'
    assert api.call('exportAudiobook', path={'book_id': 'no-such-book'}, expect=404).code == 'book_not_found'


# ---------------------------------------------------------------- read-only inspection

def test_inspection_views_are_read_only(api, fresh_book):
    """GET requests never create or change library records: the book's revision and the job list stay put."""
    path = {'book_id': fresh_book['id']}
    jobs_before = api.call('listJobs').json
    for operation in ('getPipelineInspector', 'getStoryMap', 'getBookResourceUsage', 'getBookAnalysisPipeline',
                      'listBookArtifacts', 'listPronunciations', 'listPerformances', 'getBookSeries'):
        api.call(operation, path=path)
    api.call('searchBookPassages', path=path, query={'q': 'harbour'})
    assert api.call('getBook', path=path).json == fresh_book
    assert api.call('listJobs').json == jobs_before


def test_story_map_nodes_and_edges_are_consistent(api, txt_book):
    story = api.call('getStoryMap', path={'book_id': txt_book['id']}).json
    node_ids = [node['id'] for node in story['nodes']]
    assert len(node_ids) == len(set(node_ids)), 'node identities are unique'
    for edge in story['edges']:
        assert edge['from'] in node_ids and edge['to'] in node_ids, 'every edge ends at a node'


def test_artifact_pages_clamp_paging_and_open_by_id(api, txt_book):
    path = {'book_id': txt_book['id']}
    page = api.call('listBookArtifacts', path=path, query={'limit': 100000, 'offset': -5}).json
    assert 1 <= page['limit'] <= 200 and page['offset'] == 0, 'limit and offset are clamped and reported'
    past = api.call('listBookArtifacts', path=path, query={'offset': 10 ** 9}).json
    assert past['items'] == [] and past['total'] == page['total']
    if page['items']:
        detail = api.call('getBookArtifact', path={**path, 'artifact_id': page['items'][0]['id']}).json
        assert detail['id'] == page['items'][0]['id']
    missing = api.call('getBookArtifact', path={**path, 'artifact_id': 'no-such-artifact'}, expect=404)
    assert missing.code == 'artifact_not_found'


def test_resource_usage_reports_the_import_and_clamps_paging(api, txt_book):
    path = {'book_id': txt_book['id']}
    usage = api.call('getBookResourceUsage', path=path, query={'limit': 100000, 'offset': -1}).json
    assert 1 <= usage['limit'] <= 200 and usage['offset'] == 0
    assert any(operation['stage'] == 'import' for operation in usage['operations']), 'an import records a resource measurement'
