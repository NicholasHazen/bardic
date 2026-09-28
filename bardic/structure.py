"""Deterministic navigation metadata over immutable imported source units.

EPUB spine entries are storage/reading-order units, not necessarily chapters.
Navigation anchors describe logical sections without rewriting source offsets.
"""
from __future__ import annotations

from copy import deepcopy
import re
from urllib.parse import unquote, urlsplit

from .errors import Invalid


STRUCTURE_VERSION = 2
CHAPTER_FIELDS = ("title", "kind", "title_source", "source_href", "logical_sections", "narrative_order")
_FRONT = {"cover", "titlepage", "halftitlepage", "copyright-page", "dedication", "toc", "foreword", "preface", "frontmatter"}
_BACK = {"afterword", "appendix", "bibliography", "index", "glossary", "backmatter", "colophon", "acknowledgments"}
_LABELS = {"cover": "Cover", "titlepage": "Title page", "halftitlepage": "Half title", "copyright-page": "Copyright",
           "dedication": "Dedication", "toc": "Table of contents", "foreword": "Foreword", "preface": "Preface",
           "afterword": "Afterword", "appendix": "Appendix", "bibliography": "Bibliography", "index": "Index",
           "glossary": "Glossary", "colophon": "Colophon", "acknowledgments": "Acknowledgments"}


def _kind(title, types=()):
    types = set(types)
    label = " ".join(title.lower().split()).strip(" .:—–-")
    if "recap" in types or re.fullmatch(r"(?:the )?story (?:so|thus) far|recap|previously(?: in .*)?", label):
        return "recap"
    if types & _BACK or re.match(r"^(?:afterword|also by|about the author|acknowledg(?:e)?ments|appendix|bibliography|glossary|index)(?:\b|$)", label):
        return "back_matter"
    if types & _FRONT or re.match(r"^(?:table of contents?|contents|copyright|title page|dedication|foreword|preface)(?:\b|$)", label):
        return "front_matter"
    if types & {"chapter", "prologue", "epilogue"} or re.match(r"^(?:chapter\s+\S|prologue\b|epilogue\b)", label):
        return "chapter"
    return "section"


def _safe_target(base, href, documents):
    from .importer import _resolve
    try:
        path = _resolve(base, href)
        fragment = unquote(urlsplit(href).fragment)
    except (ValueError, TypeError):
        return None
    document = documents.get(path)
    if document is None or fragment and fragment not in document["anchors"]:
        return None
    return path, document["anchors"].get(fragment, 0)


def _navigation(package, package_path, manifest, read, documents):
    from .importer import _resolve, _tag, _xml
    entries, landmarks = [], []

    def add(collection, base, href, label, depth=0, types=()):
        target = _safe_target(base, href, documents)
        if target and label:
            collection.append({"path": target[0], "start": target[1], "title": label[:300],
                               "depth": depth, "types": list(types)})

    nav_items = [item for item in manifest.values() if "nav" in item.get("properties", "").split()]
    for item in nav_items:
        try:
            path = _resolve(package_path, item.get("href", ""))
            root = _xml(read(path), "EPUB navigation")
        except ValueError:
            continue
        for nav in (n for n in root.iter() if _tag(n) == "nav"):
            types = nav.get("{http://www.idpf.org/2007/ops}type", "").split()
            role = nav.get("role", "")
            destination = entries if "toc" in types or role == "doc-toc" else landmarks if "landmarks" in types else None
            if destination is None:
                continue

            def walk(node, depth=0):
                if _tag(node) == "a" and node.get("href"):
                    label = " ".join("".join(node.itertext()).split())
                    add(destination, path, node.get("href"), label, max(0, depth - 1),
                        node.get("{http://www.idpf.org/2007/ops}type", "").split())
                for child in node:
                    walk(child, depth + (_tag(child) == "ol"))

            walk(nav)
    if entries:
        return entries, landmarks, "epub_nav"

    spine = next((element for element in package.iter() if _tag(element) == "spine"), None)
    ncx_id = spine.get("toc") if spine is not None else None
    ncx = [item for item in manifest.values() if item.get("media-type") == "application/x-dtbncx+xml"]
    ncx.sort(key=lambda item: item.get("id") != ncx_id)
    for item in ncx:
        try:
            path = _resolve(package_path, item.get("href", ""))
            root = _xml(read(path), "EPUB NCX")
        except ValueError:
            continue

        def walk_ncx(node, depth=0):
            if _tag(node) == "navpoint":
                label = next((" ".join("".join(n.itertext()).split()) for n in node if _tag(n) == "navlabel"), "")
                content = next((n for n in node if _tag(n) == "content"), None)
                if content is not None:
                    add(entries, path, content.get("src", ""), label, depth)
                depth += 1
            for child in node:
                walk_ncx(child, depth)

        walk_ncx(root)
        if entries:
            break
    return entries, landmarks, "epub_ncx"


def describe_epub_structure(package, package_path, manifest, read, chapters):
    documents = {chapter["source_href"]: chapter for chapter in chapters}
    entries, landmarks, navigation_source = _navigation(package, package_path, manifest, read, documents)
    result = []
    for index, chapter in enumerate(chapters):
        path, text = chapter["source_href"], chapter["text"]
        linked = [entry for entry in entries if entry["path"] == path]
        marks = [entry for entry in landmarks if entry["path"] == path]
        types = [kind for marker in chapter["semantics"] if marker["start"] == 0 for kind in marker["types"]]
        types.extend(kind for mark in marks if mark["start"] == 0 for kind in mark["types"])
        # Preserve TOC hierarchy in metadata; two entries at the same anchor
        # refer to the same text range, so the more specific label wins.
        targets = {}
        for entry in sorted(linked, key=lambda entry: entry["depth"]):
            targets[entry["start"]] = entry
        targets = sorted(targets.values(), key=lambda entry: entry["start"])
        source = navigation_source if targets else "heading" if chapter["heading"] else "fallback"
        title = targets[0]["title"] if len(targets) == 1 else chapter["heading"]
        if len(targets) > 1:
            title = f"Section {index + 1} · {len(targets)} contents entries"
        if not title and types:
            title = next((_LABELS[kind] for kind in types if kind in _LABELS), "")
            if title:
                source = "landmark" if marks else "semantics"
        if not title:
            # Some converted EPUBs retain useful landmark labels while losing
            # epub:type. Accept recognizable labels, not "Start of content".
            title = next((mark["title"] for mark in marks if mark["start"] == 0
                          and _kind(mark["title"], mark["types"]) != "section"), "")
            if title:
                source = "landmark"
        if not title:
            title = f"Section {index + 1}"
        logical = []
        for offset, entry in enumerate(targets):
            end = targets[offset + 1]["start"] if offset + 1 < len(targets) else len(text)
            if end > entry["start"]:
                section_types = entry["types"] + [kind for marker in chapter["semantics"]
                                                 if marker["start"] == entry["start"] for kind in marker["types"]]
                logical.append({"title": entry["title"], "start": entry["start"], "end": end,
                                "kind": _kind(entry["title"], section_types), "depth": entry["depth"],
                                "title_source": navigation_source})
        kind = "section" if len(targets) > 1 else _kind(title, types)
        result.append({"title": title, "text": text, "kind": kind, "title_source": source,
                       "source_href": path, "logical_sections": logical})
    # Reading-order position is only a fallback classification, never an
    # invented chapter number. An unlabeled body stays eligible for analysis.
    first_narrative = next((i for i, item in enumerate(result) if item["kind"] in {"chapter", "recap"}), None)
    narrative_order = 0
    for index, item in enumerate(result):
        if first_narrative is not None and index < first_narrative and item["kind"] == "section" and item["title_source"] == "fallback":
            item["kind"] = "front_matter"
            item["title"] = f"Front matter {index + 1}"
        if item["kind"] == "chapter":
            narrative_order += 1
            item["narrative_order"] = narrative_order
    return result


def describe_text_structure(chapters):
    result = []
    narrative_order = 0
    for title, text in chapters:
        source = "fallback" if title in {"Section 1", "Opening"} else "heading"
        item = {"title": title, "text": text, "kind": _kind(title), "title_source": source,
                "source_href": None, "logical_sections": []}
        if item["kind"] == "chapter":
            narrative_order += 1
            item["narrative_order"] = narrative_order
        result.append(item)
    return result


def _apply_structure(target, source):
    old_titles = {chapter["id"]: chapter["title"] for chapter in target["chapters"]}
    by_id = {chapter["id"]: chapter for chapter in source["chapters"]}
    if old_titles.keys() != by_id.keys():
        raise Invalid("structure_mismatch", "Structure refresh cannot match the saved chapter identities. Existing work was preserved.")
    for chapter in target["chapters"]:
        updated = by_id.get(chapter["id"])
        if updated is None or chapter["text"] != updated["text"]:
            raise Invalid("structure_mismatch", "Structure refresh cannot match the saved source text. Existing work was preserved.")
        for field in CHAPTER_FIELDS:
            chapter.pop(field, None)
            if field in updated:
                chapter[field] = deepcopy(updated[field])
    for scene in target.get("scenes", []):
        old_title = old_titles[scene["chapter_id"]]
        fields = scene.get("edited_fields")
        title_edited = ("*" in fields or "title" in fields) if isinstance(fields, list) else bool(scene.get("edited"))
        if not title_edited and re.fullmatch(re.escape(old_title) + r" · Scene \d+", scene.get("title", "")):
            scene["title"] = by_id[scene["chapter_id"]]["title"] + scene["title"][len(old_title):]
    target["structure_version"] = STRUCTURE_VERSION


def repair_structure(book, filename, data):
    """Return a metadata-only repair; reject any source/layout disagreement."""
    from .importer import parse_book
    parsed = parse_book(filename, data)
    if len(parsed["chapters"]) != len(book["chapters"]) or any(
        old["text"] != new["text"] for old, new in zip(book["chapters"], parsed["chapters"])
    ):
        raise Invalid("structure_mismatch", "Structure refresh cannot match the saved source text. Existing work was preserved.")
    result = deepcopy(book)
    for old, new in zip(book["chapters"], parsed["chapters"]):
        new["id"] = old["id"]
    _apply_structure(result, parsed)
    return result
