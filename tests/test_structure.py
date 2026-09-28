from copy import deepcopy
import io
import re
import zipfile

import pytest

from bardic.importer import _resolve, _xhtml_content, parse_book
from bardic.structure import repair_structure


def epub(documents, *, nav=None, ncx=None, spine=None):
    files = {
        "META-INF/container.xml": '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>',
    }
    manifest = []
    for key, body in documents.items():
        manifest.append(f'<item id="{key}" href="text/{key}.xhtml" media-type="application/xhtml+xml"/>')
        files[f"OPS/text/{key}.xhtml"] = '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>' + body + '</body></html>'
    if nav is not None:
        manifest.append('<item id="nav" href="navigation/nav.xhtml" properties="nav" media-type="application/xhtml+xml"/>')
        files["OPS/navigation/nav.xhtml"] = '<html xmlns:epub="http://www.idpf.org/2007/ops"><body>' + nav + '</body></html>'
    if ncx is not None:
        manifest.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
        files["OPS/toc.ncx"] = '<ncx><navMap>' + ncx + '</navMap></ncx>'
    files["OPS/book.opf"] = '<package><manifest>' + ''.join(manifest) + '</manifest><spine toc="ncx">' + ''.join(
        f'<itemref idref="{key}"/>' for key in (spine or documents)) + '</spine></package>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def toc(links):
    return '<nav epub:type="toc"><ol>' + ''.join(f'<li><a href="../text/{target}">{title}</a></li>' for target, title in links) + '</ol></nav>'


def test_nav_names_real_chapters_and_recap_without_numbering_front_matter():
    data = epub({"title": '<p>The Book</p>', "recap": '<div id="recap">Old events.</div>',
                 "one": '<div id="one">New events.</div>', "after": '<p>Closing words.</p>'},
                nav=toc([("recap.xhtml#recap", "The Story Thus Far"), ("one.xhtml#one", "Chapter 1"), ("after.xhtml", "Afterword")]))
    book = parse_book("book.epub", data)
    assert [c["title"] for c in book["chapters"]] == ["Front matter 1", "The Story Thus Far", "Chapter 1", "Afterword"]
    assert [c["kind"] for c in book["chapters"]] == ["front_matter", "recap", "chapter", "back_matter"]
    assert book["chapters"][2]["narrative_order"] == 1
    assert book["chapters"][2]["source_href"] == "OPS/text/one.xhtml"
    assert book["chapters"][2]["title_source"] == "epub_nav"
    assert book["chapters"][1]["logical_sections"][0]["start"] == 0


def test_ncx_fallback_uses_navlabel_and_content_in_spine_order():
    ncx = '<navPoint><navLabel><text>Chapter 1</text></navLabel><content src="text/one.xhtml#one"/></navPoint>'
    ncx += '<navPoint><navLabel><text>Chapter 2</text></navLabel><content src="text/two.xhtml"/></navPoint>'
    book = parse_book("book.epub", epub({"two": '<p>Later.</p>', "one": '<p id="one">Earlier.</p>'},
                                       spine=["one", "two"], ncx=ncx))
    assert [c["title"] for c in book["chapters"]] == ["Chapter 1", "Chapter 2"]
    assert all(c["title_source"] == "epub_ncx" for c in book["chapters"])


def test_navigation_precedes_heading_and_ncx():
    book = parse_book("book.epub", epub({"one": '<h1>Old heading</h1><p>Text.</p>'},
        nav=toc([("one.xhtml", "Chapter 8")]),
        ncx='<navPoint><navLabel><text>Wrong title</text></navLabel><content src="text/one.xhtml"/></navPoint>'))
    assert book["chapters"][0]["title"] == "Chapter 8"
    assert book["chapters"][0]["text"].startswith("Old heading")


def test_multi_anchor_source_unit_keeps_prose_and_exposes_ranges():
    body = '<div id="first">First heading</div><p> First <i>part</i>. </p><div id="second">Second heading</div><p>Second part.</p>'
    nav = toc([("one.xhtml#second", "Chapter 2"), ("one.xhtml#first", "Chapter 1")])
    book = parse_book("book.epub", epub({"one": body}, nav=nav))
    chapter = book["chapters"][0]
    assert chapter["kind"] == "section"
    assert chapter["title"] == "Section 1 · 2 contents entries"
    first, second = chapter["logical_sections"]
    assert first["title"] == "Chapter 1" and second["title"] == "Chapter 2"
    assert first["end"] == second["start"]
    assert chapter["text"][second["start"]:second["end"]] == "Second heading\n\nSecond part."
    assert all(s["text"] == chapter["text"][s["start"]:s["end"]] for s in book["segments"])


def test_nested_toc_retains_depth_and_deduplicates_same_anchor():
    nav = '<nav epub:type="toc"><ol><li><a href="../text/one.xhtml#one">Part One</a><ol><li><a href="../text/one.xhtml#one">Chapter 1</a></li><li><a href="../text/one.xhtml#two">Chapter 2</a></li></ol></li></ol></nav>'
    book = parse_book("book.epub", epub({"one": '<p id="one">First.</p><p id="two">Second.</p>'}, nav=nav))
    assert [s["title"] for s in book["chapters"][0]["logical_sections"]] == ["Chapter 1", "Chapter 2"]
    assert [s["depth"] for s in book["chapters"][0]["logical_sections"]] == [1, 1]


@pytest.mark.parametrize("target", ["https://example.com/book", "../../../../escape.xhtml", "../text/missing.xhtml", "../text/one.xhtml#missing"])
def test_invalid_optional_navigation_cannot_replace_valid_source_or_title(target):
    nav = f'<nav epub:type="toc"><ol><li><a href="{target}">Wrong</a></li></ol></nav>'
    book = parse_book("book.epub", epub({"one": '<h1>Actual heading</h1><p>Words.</p>'}, nav=nav))
    assert book["chapters"][0]["title"] == "Actual heading"
    assert book["chapters"][0]["title_source"] == "heading"


def test_invalid_xml_in_optional_navigation_falls_back_to_ncx():
    book = parse_book("book.epub", epub({"one": '<p>Words.</p>'}, nav='<nav>',
        ncx='<navPoint><navLabel><text>Chapter 1</text></navLabel><content src="text/one.xhtml"/></navPoint>'))
    assert book["chapters"][0]["title"] == "Chapter 1"


def test_landmarks_and_semantics_classify_without_treating_page_list_as_chapters():
    nav = '<nav epub:type="page-list"><ol><li><a href="../text/one.xhtml">25</a></li></ol></nav>'
    nav += '<nav epub:type="landmarks"><ol><li><a epub:type="toc" href="../text/one.xhtml">Table of Content</a></li></ol></nav>'
    book = parse_book("book.epub", epub({"one": '<p>Contents here.</p>', "two": '<section epub:type="chapter"><h2>Arrival</h2><p>Story.</p></section>',
                                       "three": '<section role="doc-afterword"><p>End.</p></section>'}, nav=nav))
    assert [c["title"] for c in book["chapters"]] == ["Table of contents", "Arrival", "Afterword"]
    assert [c["kind"] for c in book["chapters"]] == ["front_matter", "chapter", "back_matter"]


def test_unknown_section_does_not_get_invented_chapter_label():
    for name, data in [("unknown.txt", b"Some prose."), ("unknown.epub", epub({"one": '<p>Some prose.</p>'}))]:
        chapter = parse_book(name, data)["chapters"][0]
        assert chapter["title"] == "Section 1"
        assert chapter["kind"] == "section" and chapter["title_source"] == "fallback"


def test_useful_landmark_label_survives_missing_semantic_type():
    nav = '<nav epub:type="landmarks"><ol><li><a epub:type="" href="../text/one.xhtml">Table of Content</a></li></ol></nav>'
    chapter = parse_book("book.epub", epub({"one": '<p>Contents here.</p>'}, nav=nav))["chapters"][0]
    assert chapter["title"] == "Table of Content"
    assert chapter["kind"] == "front_matter" and chapter["title_source"] == "landmark"


def test_same_document_fragment_resolves_to_document_not_parent():
    assert _resolve("OPS/text/one.xhtml", "#chapter") == "OPS/text/one.xhtml"


@pytest.mark.parametrize("body", [
    '<p>  First\n  line. </p><p id="next"> Next. </p>',
    '<p>She <b id="inline">really</b> knew.</p>',
    '<div> </div><section id="section"><p> \t Words.</p></section>',
    '<p>Alpha\u00a0\u2003Beta.</p><p id="end">End.</p>',
    '<p>Alpha&#13;Beta.</p><p id="end">End.</p>',
])
def test_anchor_extraction_preserves_legacy_whitespace(body):
    from defusedxml import ElementTree as ET
    from bardic.importer import BLOCKS, SKIP_TAGS, _tag
    data = ('<html><body>' + body + '</body></html>').encode()
    pieces = []
    def legacy_walk(element):
        tag = _tag(element)
        if tag in SKIP_TAGS or element.get("hidden") is not None or element.get("aria-hidden") == "true":
            return
        if tag in BLOCKS:
            pieces.append("\n\n")
        if element.text:
            pieces.append(element.text)
        for child in element:
            legacy_walk(child)
            if child.tail:
                pieces.append(child.tail)
        if tag in BLOCKS:
            pieces.append("\n\n")
    legacy_walk(ET.fromstring(data))
    legacy_raw = ''.join(pieces).replace("\r\n", "\n").replace("\r", "\n")
    expected = re.sub(r"\n{3,}", "\n\n", re.sub(r" *\n *", "\n", re.sub(r"[^\S\n]+", " ", legacy_raw))).strip()
    content = _xhtml_content(data, "test")
    assert content["text"] == expected
    assert all(content["text"][start:].strip() for start in content["anchors"].values())


def legacy_book(data):
    book = parse_book("book.epub", data)
    book.pop("structure_version")
    for index, chapter in enumerate(book["chapters"]):
        for key in tuple(chapter):
            if key not in {"id", "index", "title", "text"}:
                chapter.pop(key)
        chapter["title"] = f"Chapter {index + 1}"
    for scene in book["scenes"]:
        scene["title"] = f"Chapter 1 · Scene 1"
    book["segments"][0]["audio"] = {"fingerprint": "kept", "duration": 2.0}
    return book


def test_repair_is_metadata_only():
    data = epub({"one": '<p>Story so far.</p>'}, nav=toc([("one.xhtml", "The Story Thus Far")]))
    book = legacy_book(data)
    before = deepcopy(book)
    repaired = repair_structure(book, "book.epub", data)
    assert book == before
    assert repaired["segments"] == book["segments"]
    assert repaired["chapters"][0]["id"] == book["chapters"][0]["id"]
    assert repaired["chapters"][0]["text"] == book["chapters"][0]["text"]
    assert repaired["scenes"][0]["id"] == book["scenes"][0]["id"]
    assert repaired["scenes"][0]["title"] == "The Story Thus Far · Scene 1"


@pytest.mark.parametrize("change", ["text", "count"])
def test_repair_refuses_source_changes_before_modifying_original(change):
    data = epub({"one": '<p>Original.</p>'})
    book = legacy_book(data)
    before = deepcopy(book)
    changed = epub({"one": '<p>Changed.</p>'} if change == "text" else {"one": '<p>Original.</p>', "two": '<p>More.</p>'})
    with pytest.raises(ValueError, match="saved source text"):
        repair_structure(book, "book.epub", changed)
    assert book == before


def test_repair_retains_custom_scene_title():
    data = epub({"one": '<p>Original.</p>'}, nav=toc([("one.xhtml", "Chapter 8")]))
    book = legacy_book(data)
    book["scenes"][0].update(title="Reviewed title", edited=True)
    repaired = repair_structure(book, "book.epub", data)
    assert repaired["scenes"][0]["title"] == "Reviewed title"
