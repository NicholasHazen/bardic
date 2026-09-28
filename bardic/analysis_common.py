"""Analysis helpers that outlived the removed Classic (phase) engine.

``split_chapter`` publishes the scene boundaries a directing result proposes;
the step pipeline's Directing step uses it. It moved here unchanged from that
engine's chapter runner (docs/CLASSIC-REMOVAL.md).
"""
from __future__ import annotations

from . import analysis as a


def split_chapter(book, chapter_id, boundaries):
    """Publish scene boundaries with their chapter, keeping later scene objects."""
    chapter = {"scenes": [s for s in book["scenes"] if s["chapter_id"] == chapter_id],
               "segments": [s for s in book["segments"] if s["chapter_id"] == chapter_id]}
    a._split_scenes(chapter, boundaries)
    result = []
    inserted = False
    for scene in book["scenes"]:
        if scene["chapter_id"] == chapter_id:
            if not inserted:
                result.extend(chapter["scenes"])
                inserted = True
        else:
            result.append(scene)
    book["scenes"] = result
