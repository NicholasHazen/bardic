"""Analysis helpers that outlive the legacy phase engine.

``split_chapter`` publishes the scene boundaries a directing result proposes;
the step pipeline's Directing step uses it. ``fingerprint`` is the identity of
a saved ``analysis_checkpoints`` record, which structure repair re-keys while
that legacy table exists (docs/CLASSIC-REMOVAL.md, stage 4).

Both moved here unchanged from ``bardic/staged_analysis.py``.
"""
from __future__ import annotations

from . import analysis as a
from .processing import digest

PIPELINE_VERSION = 1


def fingerprint(book, provider, model):
    # Audio and automatic annotations are outputs, not reasons to lose a checkpoint.
    return digest({"version": PIPELINE_VERSION, "provider": provider, "model": model,
                   "chapters": book["chapters"],
                   "spans": [(s["id"], s["chapter_id"], s["start"], s["end"], s["kind"]) for s in book["segments"]],
                   "reviewed": {name: [{k: v for k, v in item.items() if k != "audio"}
                                        for item in book[name] if item.get("edited")]
                                for name in ("characters", "scenes", "segments")}})


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
