"""Resumable chapter analysis and source-anchored character observations.

Legacy chapter engine behind ``/analyze`` with a store and no phase (including
the local heuristic draft). Scheduled for deletion; see docs/CLASSIC-REMOVAL.md.
Live code must not import this module (tests/test_legacy_isolation.py).
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

import httpx

from . import analysis as a
# Moved to a neutral module for the step pipeline and structure repair; re-exported here.
from .analysis_common import PIPELINE_VERSION, fingerprint, split_chapter as _split_chapter  # noqa: F401
from .store import now


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _aggregate(candidates):
    result = {}
    for item in candidates:
        key = a._name_key(item["name"])
        if key not in result:
            result[key] = deepcopy(item)
            continue
        previous = result[key]
        evidence = list(dict.fromkeys([*previous["evidence"], *item["evidence"]]))
        # Retain early and late observations rather than only the first chapter.
        previous["evidence"] = evidence if len(evidence) <= 8 else evidence[:4] + evidence[-4:]
        previous["aliases"] = list(dict.fromkeys([*previous["aliases"], *item["aliases"]]))[:20]
        previous["description"] = (previous["description"] + " " + item["description"])[-1200:]
        previous["direction"] = (previous["direction"] + " " + item["direction"])[-1200:]
    if len(result) > 200:
        raise ValueError("More than 200 character candidates need review. Saved chapter discoveries are retained.")
    return result


def _references(book, units, provider, model, previous):
    chapters = {c["id"]: c for c in book["chapters"]}
    characters = {c["id"]: c for c in book["characters"] if c["id"] not in {"narrator", "unassigned"}}
    segments = {cid: [s for s in book["segments"] if s["chapter_id"] == cid] for cid in chapters}
    names = {}
    for c in characters.values():
        for name in [c["name"], *c.get("aliases", [])]:
            names.setdefault(a._name_key(name), set()).add(c["id"])
    refs = {}

    def add(cid, chapter_id, start, end, kind, **extra):
        chapter = chapters[chapter_id]
        if cid not in characters or not 0 <= start < end <= len(chapter["text"]):
            return
        segment = next((s for s in segments[chapter_id] if s["start"] < end and s["end"] > start), None)
        record = {"id": _hash([cid, chapter_id, start, end, kind]), "character_id": cid,
                  "chapter_id": chapter_id, "segment_id": segment["id"] if segment else None,
                  "start": start, "end": end, "quote": chapter["text"][start:end], "kind": kind,
                  "confidence": None, "provider": provider, "model": model, **extra}
        refs[record["id"]] = record

    for ref in previous:
        chapter = chapters.get(ref.get("chapter_id"))
        start, end = ref.get("start", -1), ref.get("end", -1)
        if (ref.get("kind") == "profile_evidence" and ref.get("character_id") in characters and chapter
                and 0 <= start < end <= len(chapter["text"]) and chapter["text"][start:end] == ref.get("quote")):
            refs[ref["id"]] = deepcopy(ref)
    for unit in units.values():
        if unit.get("stage") != "discovery":
            continue
        chapter_id = unit["chapter_id"]
        source = chapters[chapter_id]["text"][unit["start"]:unit["end"]]
        for profile in unit["result"]["characters"]:
            character = a._resolve_character_candidate(characters.values(), profile)
            if character is None:
                continue
            cid = character["id"]
            for span in a._evidence_spans(profile["evidence"], source, "character evidence"):
                add(cid, chapter_id, unit["start"] + span["start"], unit["start"] + span["end"],
                    "profile_evidence", profile_description=profile["description"], profile_direction=profile["direction"],
                    provider=unit.get("provider", provider), model=unit.get("model", model))
    # A name mention is an explicit textual reference, not a claim of scene presence.
    for name, ids in names.items():
        if len(ids) != 1 or not name:
            continue
        cid = next(iter(ids))
        actual = next(n for n in [characters[cid]["name"], *characters[cid].get("aliases", [])] if a._name_key(n) == name)
        pattern = re.compile(r"(?<!\w)" + re.escape(actual) + r"(?!\w)")
        for chapter in chapters.values():
            for match in pattern.finditer(chapter["text"]):
                add(cid, chapter["id"], match.start(), match.end(), "mention", provider="local", model=None)
    old_dialogue = {(r.get("character_id"), r.get("chapter_id"), r.get("start"), r.get("end")): r
                    for r in previous if r.get("kind") == "dialogue"}
    for s in book["segments"]:
        if s["kind"] == "dialogue" and s["speaker_id"] in characters:
            old = old_dialogue.get((s["speaker_id"], s["chapter_id"], s["start"], s["end"]), {})
            add(s["speaker_id"], s["chapter_id"], s["start"], s["end"], "dialogue",
                confidence=s.get("confidence"), provider="reviewed" if s.get("edited") else s.get("analysis_provider", old.get("provider")),
                model=None if s.get("edited") else s.get("analysis_model", old.get("model")))
    # A longer alias and its suffix at the same location are one mention.
    occupied = {}
    for ref in sorted((r for r in refs.values() if r["kind"] == "mention"),
                      key=lambda r: (r["character_id"], r["chapter_id"], r["start"], -r["end"])):
        group = (ref["character_id"], ref["chapter_id"])
        if ref["start"] < occupied.get(group, -1):
            refs.pop(ref["id"], None)
        else:
            occupied[group] = ref["end"]
    return list(refs.values())


def analyze_staged(book, provider, api_key, model, progress, cancelled, *, store, chapter_id=None, resume=True, prepare=None):
    """Checkpoint validated requests; publish only complete chapter stages."""
    a._validate_source(book)
    selected = [c for c in book["chapters"] if chapter_id is None or c["id"] == chapter_id]
    if not selected:
        raise ValueError("Choose a chapter in this book.")
    if provider != "local":
        if provider not in a.PROVIDER_LABELS or not api_key:
            raise ValueError("Configure the selected analysis provider's API key first.")
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", model):
            raise ValueError("Choose a valid analysis model ID.")
    key = fingerprint(book, provider, model)
    checkpoint = store.analysis_checkpoint(book["id"], key) if resume else None
    if checkpoint is None:
        checkpoint = {"provider": provider, "model": model, "working_book": deepcopy(book), "units": {},
                      "references": store.character_references(book["id"]), "chapters": []}
    baseline = deepcopy(checkpoint["working_book"])
    # Current reading position and takes survive reuse of a prior source snapshot.
    work = deepcopy(book)
    selected_ids = {c["id"] for c in selected}
    for collection in ("scenes", "segments"):
        # Replay the chosen chapters from a stable baseline without rolling back
        # previously published work in chapters outside this request.
        original = {c["id"]: [item for item in baseline[collection] if item["chapter_id"] == c["id"]]
                    for c in selected}
        work[collection] = [deepcopy(item) for c in book["chapters"]
                            for item in (original[c["id"]] if c["id"] in selected_ids else
                                         [item for item in book[collection] if item["chapter_id"] == c["id"]])]
    audio = {s["id"]: s.get("audio") for s in book["segments"]}
    for s in work["segments"]:
        s["audio"] = audio.get(s["id"])
    units = checkpoint["units"]
    chapter_batches = {c["id"]: list(a._batches([s for s in work["segments"] if s["chapter_id"] == c["id"]], limit=10000)) for c in selected}
    rows = {row["id"]: row for row in checkpoint["chapters"]}
    for c in book["chapters"]:
        rows.setdefault(c["id"], {"id": c["id"], "title": c["title"], "stage": "discovery", "status": "pending",
                                  "completed_units": 0, "total_units": 0, "discovery_complete": False, "directing_complete": False})
    checkpoint.update(status="running", stage="discovery", current_chapter_id=None, scope_chapter_id=chapter_id,
                      error=None, chapters=list(rows.values()), fingerprint=key)
    scenes = [(scene, list(a._batches([s for s in work["segments"] if s["scene_id"] == scene["id"]], limit=10000)))
              for scene in work["scenes"] if scene["chapter_id"] in selected_ids]
    total = sum(len(v) for v in chapter_batches.values()) + sum(len(v) for _, v in scenes) + (provider != "local")
    done = 0
    active_row = None
    published = deepcopy(book)

    def save(message, *, publish=False):
        checkpoint.update(completed_units=done, total_units=total, updated_at=now())
        if publish:
            snapshot = deepcopy(work)
            complete = all(row.get("directing_complete") for row in rows.values())
            snapshot["analysis"] = {"provider": provider, "model": model, "status": "draft" if checkpoint["status"] == "completed" and complete else "partial",
                                    "notes": "Chapter analysis is saved in stages. Profiles use recorded source evidence; review inferred identities and performance choices before narration."}
            snapshot["revision"] = book.get("revision", 1) + 1
            if prepare:
                prepare(snapshot)
            a._validate_source(snapshot)
            checkpoint["references"] = _references(snapshot, units, provider, model, checkpoint["references"])
            store.commit_analysis(snapshot, key, checkpoint)
            published.clear()
            published.update(snapshot)
        else:
            store.save_analysis_checkpoint(book["id"], key, checkpoint)
        progress(done, total, message)

    def unit_request(unit_key, stage, prompt, schema, validate, metadata=None):
        a._check_cancel(cancelled)
        if unit_key in units:
            return validate(deepcopy(units[unit_key]["result"]))
        def call(repair_instruction):
            if repair_instruction:
                save(f"{active_row['title'] if active_row else 'Cast profiles'} · repairing source evidence")
            return request(client, model, api_key, prompt + ("\n\n" + repair_instruction if repair_instruction else ""), schema, cancelled)
        result = a._repairable_request(call, validate, cancelled)
        units[unit_key] = {"stage": stage, "result": deepcopy(result), **(metadata or {})}
        return result

    try:
        a._check_cancel(cancelled)
        save("Preparing chapter analysis")
        if provider == "local":
            for c in selected:
                active_row = rows[c["id"]]
                checkpoint.update(current_chapter_id=c["id"], stage="discovery")
                subset = deepcopy(work)
                subset["chapters"] = [c]
                subset["segments"] = [s for s in subset["segments"] if s["chapter_id"] == c["id"]]
                subset["scenes"] = [s for s in subset["scenes"] if s["chapter_id"] == c["id"]]
                a._local(subset, lambda *_: a._check_cancel(cancelled), cancelled)
                for segment in subset["segments"]:
                    if not segment.get("edited"):
                        segment.update(analysis_provider="local", analysis_model=None)
                work["characters"] = subset["characters"]
                by_segment = {s["id"]: s for s in subset["segments"]}
                by_scene = {s["id"]: s for s in subset["scenes"]}
                work["segments"] = [by_segment.get(s["id"], s) for s in work["segments"]]
                work["scenes"] = [by_scene.get(s["id"], s) for s in work["scenes"]]
                amount = len(chapter_batches[c["id"]]) + sum(len(b) for s, b in scenes if s["chapter_id"] == c["id"])
                done += amount
                active_row.update(status="completed", stage="complete", discovery_complete=True, directing_complete=True, completed_units=amount, total_units=amount, error=None)
                save(f"{c['title']} · local draft saved", publish=True)
        else:
            request = {"gemini": a._request, "openai": a._openai_request, "anthropic": a._anthropic_request}[provider]
            with httpx.Client(timeout=httpx.Timeout(180, connect=15)) as client:
                for c in selected:
                    active_row = rows[c["id"]]
                    checkpoint.update(current_chapter_id=c["id"], stage="discovery")
                    active_row.update(stage="discovery", status="running", error=None, completed_units=0,
                                      total_units=len(chapter_batches[c["id"]]) + sum(len(b) for s, b in scenes if s["chapter_id"] == c["id"]))
                    for index, batch in enumerate(chapter_batches[c["id"]]):
                        save(f"{c['title']} · discovering characters · part {index + 1}/{len(chapter_batches[c['id']])}")
                        start, end = batch[0]["start"], batch[-1]["end"]
                        source = c["text"][start:end]
                        prompt = ("Identify named or distinctly identified speaking characters in this chapter excerpt. Non-story front matter may have no characters: return an empty list then. "
                                  "Include aliases only when explicit. Describe vocal traits only from textual evidence; unknown traits stay unknown. "
                                  "Use 1–8 SHORT, CONTIGUOUS quotations copied from this excerpt, at most 600 characters each. Preserve punctuation and words. "
                                  "Never paraphrase, combine separated phrases, or insert ellipses. Do not list the narrator or invent a character for a pronoun. Give restrained performance direction.\n\nBOOK EXCERPT:\n" + source)
                        def validate_cast(result):
                            a._cast_result(result, source)
                            return result
                        result = unit_request(f"discovery:{c['id']}:{index}", "discovery", prompt, a.CAST_SCHEMA, validate_cast,
                                              {"chapter_id": c["id"], "start": start, "end": end})
                        a._merge_cast(work, result["characters"])
                        done += 1
                        active_row["completed_units"] += 1
                        save(f"{c['title']} · character evidence saved")
                    active_row.update(discovery_complete=True, directing_complete=False, stage="directing", status="pending")
                    save(f"{c['title']} · character discovery saved", publish=True)

                active_row = None
                checkpoint.update(stage="profiles", current_chapter_id=None)
                candidates = [p for unit in units.values() if unit.get("stage") == "discovery" for p in unit["result"]["characters"]]
                aggregated = _aggregate(candidates)
                profile_key = "profiles:" + _hash(aggregated)
                save("Building character profiles from saved chapter evidence")
                if aggregated:
                    evidence_items = [q for c in aggregated.values() for q in c["evidence"]]
                    prompt = ("Reconcile these chapter observations into a consistent cast and vocal profiles. Merge aliases only when evidence establishes identity; keep uncertain identities separate. "
                              "Every candidate name must appear as a final name or alias. Preserve distinct characters. Every quotation must be copied exactly from the supplied candidate evidence. "
                              "Do not invent vocal traits or turn temporary emotion into a permanent trait. Describe uncertainty.\n\nCANDIDATES:\n" + json.dumps(list(aggregated.values()), ensure_ascii=False))
                    def validate_profiles(result):
                        cast = a._profile_result(result, evidence_items)
                        represented = {a._name_key(n) for p in cast for n in [p["name"], *p["aliases"]]}
                        if set(aggregated) - represented:
                            raise ValueError("Cast reconciliation omitted discovered characters.")
                        return result
                    cast = unit_request(profile_key, "profiles", prompt, a.CAST_SCHEMA, validate_profiles)["characters"]
                    # Include earlier cached chapters when applying global identity decisions.
                    a._merge_cast(work, candidates)
                    a._reconcile_known_aliases(work, cast)
                    a._merge_cast(work, cast)
                done += 1
                cast_context = [{k: c[k] for k in ("id", "name", "aliases", "description", "direction")} for c in work["characters"]]
                cast_hash = _hash(cast_context)
                for row in rows.values():
                    if row.get("cast_hash") != cast_hash and row.get("directing_complete"):
                        row.update(directing_complete=False, status="pending", stage="directing")
                save("Character profiles saved", publish=True)
                boundaries = {}
                for c in selected:
                    active_row = rows[c["id"]]
                    active_row.update(stage="directing", status="running")
                    checkpoint.update(stage="directing", current_chapter_id=c["id"])
                    for scene, batches in [entry for entry in scenes if entry[0]["chapter_id"] == c["id"]]:
                        if not scene.get("edited"):
                            scene["summary"] = ""
                        for index, batch in enumerate(batches):
                            save(f"{c['title']} · directing {scene['title']} · part {index + 1}/{len(batches)}")
                            source = [{k: s[k] for k in ("id", "text", "kind", "speaker_id")} for s in batch]
                            before, after = a._scene_context(work, batch)
                            prompt = ("Annotate EVERY supplied passage ID exactly once, without rewriting its text. Use narrator for narration; unassigned and low confidence for uncertain dialogue. "
                                      "Use cast IDs and SHORT contiguous exact quotations from the excerpt/context as speaker evidence. Do not paraphrase or combine quotations. "
                                      "Unresolved or narration passages may use []. Add concise direction for subtext, tone, pace and explicit laughter/grunts as metadata. "
                                      "Summarize the scene portion. Propose scene_starts only at unmistakable time/location/viewpoint changes; otherwise []. Respect reviewed assignments. "
                                      "Neighboring context is for interpretation, not additional IDs.\n\nCAST:\n" + json.dumps(cast_context, ensure_ascii=False) +
                                      "\n\nSCENE: " + scene["title"] + "\nCONTEXT BEFORE:\n" + before + "\nPASSAGES:\n" + json.dumps(source, ensure_ascii=False) + "\nCONTEXT AFTER:\n" + after)
                            def validate_annotations(result):
                                # Validation must never leave mutations from a rejected attempt.
                                a._apply_annotations(work, deepcopy(scene), deepcopy(batch), result, {})
                                return result
                            unit_key = "directing:" + _hash([scene["id"], [s["id"] for s in batch], cast_hash])
                            result = unit_request(unit_key, "directing", prompt, a.ANNOTATION_SCHEMA, validate_annotations,
                                                  {"chapter_id": c["id"], "cast_hash": cast_hash})
                            a._apply_annotations(work, scene, batch, result, boundaries)
                            for segment in batch:
                                if not segment.get("edited"):
                                    segment.update(analysis_provider=provider, analysis_model=model)
                            done += 1
                            active_row["completed_units"] += 1
                            save(f"{c['title']} · passage directions saved")
                    active_row.update(stage="complete", status="completed", directing_complete=True, cast_hash=cast_hash)
                    _split_chapter(work, c["id"], boundaries)
                    boundaries.clear()
                    a._scene_members(work)
                    save(f"{c['title']} · analysis saved", publish=True)
                a._scene_members(work)
        checkpoint.update(status="completed", stage="complete", current_chapter_id=None, error=None)
        done = total
        a._check_cancel(cancelled)
        save("Selected chapter analysis ready for review" if chapter_id else "Book analysis ready for review", publish=True)
        return published
    except Exception as exc:
        stopped = isinstance(exc, InterruptedError) or cancelled()
        message = str(exc).replace(api_key, "[redacted]") if api_key else str(exc)
        location = f"{active_row['title']} · {checkpoint['stage']}" if active_row else checkpoint["stage"]
        safe_error = f"{location}: {message[:700]}"[:1000]
        checkpoint.update(status="interrupted" if stopped else "failed", error=safe_error, updated_at=now())
        if active_row:
            active_row.update(status=checkpoint["status"], error=safe_error)
        # Save without the progress callback: it may itself be reporting cancellation.
        store.save_analysis_checkpoint(book["id"], key, checkpoint)
        if stopped:
            raise a.AnalysisCancelled("Analysis stopped. Validated chapter work is saved; analyze again to resume.") from exc
        raise ValueError(safe_error + " Validated chapter work is saved; analyze again to resume.") from exc
