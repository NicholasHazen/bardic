import io
import zipfile

import pytest

from bardic.importer import MAX_SEGMENT, make_demo_book, parse_book


def epub_file(*, chapters=None, spine=None, extras=None, metadata=""):
    chapters = chapters or {"one": "<h1>One</h1><p>First paragraph.</p>", "two": "<h1>Two</h1><p>Second paragraph.</p>"}
    spine = spine or list(chapters)
    files = {
        "META-INF/container.xml": '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>',
        "OPS/book.opf": '<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Spine Test</dc:title><dc:creator>A Writer</dc:creator>' + metadata + '</metadata><manifest>' + ''.join(f'<item id="{name}" href="{name}.xhtml" media-type="application/xhtml+xml"/>' for name in chapters) + '</manifest><spine>' + ''.join(f'<itemref idref="{name}"/>' for name in spine) + '</spine></package>',
    }
    files.update({f"OPS/{name}.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Unspoken metadata</title><style>p { color: red; }</style></head><body>' + content + '</body></html>' for name, content in chapters.items()})
    files.update(extras or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def assert_full_prose_coverage(book):
    for chapter in book["chapters"]:
        passages = [s for s in book["segments"] if s["chapter_id"] == chapter["id"]]
        position = 0
        for passage in passages:
            assert position <= passage["start"] < passage["end"]
            assert not chapter["text"][position:passage["start"]].strip()
            assert passage["text"] == chapter["text"][passage["start"]:passage["end"]]
            assert 0 < len(passage["text"]) <= MAX_SEGMENT
            position = passage["end"]
        assert not chapter["text"][position:].strip()
    assert {s["id"] for s in book["segments"]} == {sid for scene in book["scenes"] for sid in scene["segment_ids"]}


def test_txt_keeps_unicode_prose_and_explicit_scene_breaks():
    raw = 'Chapter One\r\n\r\nMara held the 🔥. “Stay,” she said.\r\n\r\n***\r\n\r\n“No,” Elias replied.\r\n'
    book = parse_book("story.txt", raw.encode())
    assert len(book["chapters"]) == 1
    assert len(book["scenes"]) == 2
    assert len([s for s in book["segments"] if s["kind"] == "dialogue"]) == 2
    assert "🔥" in book["chapters"][0]["text"]
    assert "***" not in book["chapters"][0]["text"]
    assert_full_prose_coverage(book)


def test_long_prose_and_unbroken_words_are_bounded_without_loss():
    text = "Chapter I\n\n" + ("A deliberately long sentence with commas, and detail. " * 80) + "\n\n“" + "x" * 1800 + "”"
    book = parse_book("long.txt", text.encode())
    assert book["chapters"][0]["text"] == text
    assert_full_prose_coverage(book)


def test_txt_headings_preserve_preamble_and_all_chapter_text():
    text = "An introduction.\n\nCHAPTER ONE\n\nStart here.\n\nChapter Two\n\nEnd here.\n"
    book = parse_book("Two_Chapters.txt", text.encode())
    assert len(book["chapters"]) == 3
    assert "".join(c["text"] for c in book["chapters"]) == text
    assert book["title"] == "Two Chapters"
    assert_full_prose_coverage(book)


def test_epub_uses_spine_not_archive_order_and_extracts_metadata():
    book = parse_book("order.epub", epub_file(spine=["two", "one"]))
    assert [c["title"] for c in book["chapters"]] == ["Two", "One"]
    assert book["title"] == "Spine Test"
    assert book["author"] == "A Writer"
    assert "Unspoken metadata" not in str(book["chapters"])
    assert_full_prose_coverage(book)


@pytest.mark.parametrize(("metadata", "expected"), [
    ("<dc:language>en-GB</dc:language>", "en-GB"),
    ("<dc:language> fr </dc:language><dc:language>de</dc:language>", "fr"),
    ("<dc:language>pt_BR</dc:language>", "pt-BR"),
    ("<dc:language>not a language!</dc:language>", None),
    ("", None),
])
def test_epub_language_is_metadata_only(metadata, expected):
    plain = parse_book("lang.epub", epub_file())
    book = parse_book("lang.epub", epub_file(metadata=metadata))
    assert book["language"] == expected
    # Language never changes the canonical text or its passage coordinates.
    assert [c["text"] for c in book["chapters"]] == [c["text"] for c in plain["chapters"]]
    assert [(s["start"], s["end"], s["text"]) for s in book["segments"]] == [(s["start"], s["end"], s["text"]) for s in plain["segments"]]


def test_txt_has_no_language():
    assert parse_book("story.txt", "Chapter One\n\nWords.\n".encode())["language"] is None


def test_epub_hr_preserves_inline_words_and_starts_scene():
    book = parse_book("inline.epub", epub_file(chapters={"one": "<p>She <em>really</em> meant it.</p><hr/><p>Later, <strong>at sea</strong>.</p>"}))
    assert len(book["scenes"]) == 2
    assert "She really meant it." in book["chapters"][0]["text"]
    assert "Later, at sea." in book["chapters"][0]["text"]
    assert_full_prose_coverage(book)


def test_epub_rejects_zip_traversal_and_xml_entities():
    with pytest.raises(ValueError, match="unsafe file path"):
        parse_book("bad.epub", epub_file(extras={"../escape": "data"}))
    bad_xml = '<!DOCTYPE package [<!ENTITY leak SYSTEM "file:///etc/passwd">]><package>&leak;</package>'
    with pytest.raises(ValueError, match="unsafe XML"):
        parse_book("bad.epub", epub_file(extras={"OPS/book.opf": bad_xml}))


def test_epub_rejects_encrypted_text_and_accepts_obfuscated_fonts():
    encryption = '<encryption><EncryptedData><EncryptionMethod Algorithm="{algorithm}"/><CipherData><CipherReference URI="{uri}"/></CipherData></EncryptedData></encryption>'
    with pytest.raises(ValueError, match="Encrypted EPUB content"):
        parse_book("drm.epub", epub_file(extras={"META-INF/encryption.xml": encryption.format(algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc", uri="OPS/one.xhtml")}))
    with pytest.raises(ValueError, match="Encrypted reading content"):
        parse_book("drm.epub", epub_file(extras={"META-INF/encryption.xml": encryption.format(algorithm="http://www.idpf.org/2008/embedding", uri="OPS/one.xhtml")}))
    assert parse_book("font.epub", epub_file(extras={"META-INF/encryption.xml": encryption.format(algorithm="http://www.idpf.org/2008/embedding", uri="OPS/font.otf")}))["chapters"]


@pytest.mark.parametrize("name,data", [("bad.pdf", b"pdf"), ("empty.txt", b""), ("blank.txt", b"  \n"), ("binary.txt", b"a\x00b"), ("bad.txt", b"\xff"), ("bad.epub", b"not zip")])
def test_unreadable_uploads_fail_with_value_error(name, data):
    with pytest.raises(ValueError):
        parse_book(name, data)


def test_demo_is_original_two_scene_work_with_traceable_text():
    book = make_demo_book()
    assert book["title"] == "The Last Light"
    assert len(book["scenes"]) == 2
    assert 300 <= sum(len(s["text"].split()) for s in book["segments"]) <= 400
    assert_full_prose_coverage(book)


@pytest.mark.parametrize("speech", ["‘I don’t think we’ve met,’", "'I don't think we've met,'", '“I don’t think we’ve met,”', '«I don’t think we’ve met,»'])
def test_dialogue_contractions_do_not_close_single_quoted_speech(speech):
    book = parse_book("quotes.txt", f"Mara waited. {speech} Elias said.".encode())
    dialogue = [s["text"] for s in book["segments"] if s["kind"] == "dialogue"]
    assert dialogue == [speech]
    assert_full_prose_coverage(book)


def test_narrative_apostrophes_are_not_quoted_speech():
    text = "Mara's coat wasn't dry. Elias didn't notice. The sailors' hats fell."
    book = parse_book("apostrophes.txt", text.encode())
    assert all(s["kind"] == "narration" for s in book["segments"])
    assert_full_prose_coverage(book)


@pytest.mark.parametrize("opening,closing", [('“', '”'), ('"', '"'), ('‘', '’'), ("'", "'")])
def test_continuing_multi_paragraph_speech_keeps_every_paragraph_dialogue(opening, closing):
    text = f"{opening}The first thing is patience.\n\n{opening}The second is courage.{closing} Mara said."
    book = parse_book("speech.txt", text.encode())
    dialogue = [s["text"] for s in book["segments"] if s["kind"] == "dialogue"]
    assert dialogue == [f"{opening}The first thing is patience.", f"{opening}The second is courage.{closing}"]
    assert_full_prose_coverage(book)
