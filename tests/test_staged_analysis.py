"""Functional staged analysis tests; all provider requests are local fakes."""
from copy import deepcopy
import json

import pytest

from bardic import analysis
from bardic.importer import parse_book
from bardic.staged_analysis import analyze_staged, fingerprint
from bardic.store import Store


@pytest.fixture
def story():
    return parse_book("story.txt", (
        "Chapter One\n\n“Hello,” Mara whispered.\n\n"
        "Chapter Two\n\n“Again,” Mara whispered."
    ).encode())


class Provider:
    """Answer the actual requested excerpt/IDs rather than assuming call order."""
    def __init__(self, monkeypatch, book):
        self.book = book
        self.calls = []
        self.transform = lambda stage, chapter_id, result: result
        self.prefix = ""
        monkeypatch.setattr(analysis, "_openai_request", self.request)

    def request(self, client, model, key, prompt, schema, cancelled):
        if "BOOK EXCERPT:\n" in prompt:
            stage = "discovery"
            source = prompt.split("BOOK EXCERPT:\n", 1)[1]
            chapter = next(c for c in self.book["chapters"] if c["title"] in source)
            chapter_id = chapter["id"]
            name = "Captain Voss" if "Captain Voss" in source else "Mara"
            evidence = name + (" replied" if name == "Captain Voss" else " whispered")
            result = {"characters": [{"name": name, "aliases": [], "description": "Vocal qualities unknown.",
                                       "direction": "Restrained.", "evidence": [evidence]}]}
        elif "CANDIDATES:\n" in prompt:
            stage, chapter_id = "profiles", None
            candidates = json.JSONDecoder().raw_decode(prompt.split("CANDIDATES:\n", 1)[1])[0]
            result = {"characters": candidates}
        else:
            stage = "directing"
            passages = json.JSONDecoder().raw_decode(prompt.split("PASSAGES:\n", 1)[1])[0]
            chapter_id = next(s["chapter_id"] for s in self.book["segments"] if s["id"] == passages[0]["id"])
            cast = json.JSONDecoder().raw_decode(prompt.split("CAST:\n", 1)[1])[0]
            source = next(c["text"] for c in self.book["chapters"] if c["id"] == chapter_id)
            name = "Captain Voss" if "Captain Voss" in source else "Mara"
            speaker = next(c["id"] for c in cast if name == c["name"] or name in c["aliases"])
            evidence = name + (" replied" if name == "Captain Voss" else " whispered")
            result = {"summary": self.prefix + "A quiet exchange.", "tone": "Quiet", "direction": "Measured.",
                      "segments": [{"id": s["id"], "speaker_id": speaker if s["kind"] == "dialogue" else "narrator",
                                    "confidence": .92, "direction": self.prefix + "Understated.", "cues": [],
                                    "evidence": [evidence] if s["kind"] == "dialogue" else []} for s in passages],
                      "scene_starts": []}
        self.calls.append({"stage": stage, "chapter_id": chapter_id, "model": model, "prompt": prompt})
        return self.transform(stage, chapter_id, result)


def run(book, store, **kwargs):
    return analyze_staged(book, "openai", "fake-test-key", kwargs.pop("model", "test-model"),
                          kwargs.pop("progress", lambda *_: None), kwargs.pop("cancelled", lambda: False),
                          store=store, **kwargs)


def test_overlapping_aliases_are_one_mention_and_keep_exact_source():
    from bardic.staged_analysis import _references
    book = parse_book("aliases.txt", b"The Queen entered. The Queen left.")
    book["characters"].append({"id": "queen", "name": "The Queen", "aliases": ["Queen"]})
    refs = _references(book, {}, "openai", "test-model", [])
    assert [(r["quote"], r["start"]) for r in refs] == [("The Queen", 0), ("The Queen", 19)]


def test_resume_after_later_chapter_failure_reuses_validated_requests(tmp_path, monkeypatch, story):
    store = Store(tmp_path)
    store.save_book(story)
    provider = Provider(monkeypatch, story)
    second = story["chapters"][1]["id"]

    def fail_second_direction(stage, chapter_id, result):
        if stage == "directing" and chapter_id == second:
            raise RuntimeError("Simulated connection failure")
        return result

    provider.transform = fail_second_direction
    with pytest.raises(ValueError, match="Validated chapter work is saved"):
        run(story, store)
    assert len(provider.calls) == 5
    partial = store.book(story["id"])
    first_dialogue = next(s for s in partial["segments"] if s["kind"] == "dialogue")
    assert first_dialogue["direction"] == "Understated."
    assert store.analysis_status(story["id"])["chapters"][0]["directing_complete"] is True
    assert store.analysis_status(story["id"])["status"] == "failed"

    provider.transform = lambda stage, chapter_id, result: result
    before = len(provider.calls)
    complete = run(partial, store)
    assert [(c["stage"], c["chapter_id"]) for c in provider.calls[before:]] == [("directing", second)]
    assert all(s["direction"] == "Understated." for s in complete["segments"])
    assert store.analysis_status(story["id"])["status"] == "completed"
    assert all(c["directing_complete"] for c in store.analysis_status(story["id"])["chapters"])


def test_completed_run_reuses_all_results_and_keeps_source(tmp_path, monkeypatch, story):
    store = Store(tmp_path)
    provider = Provider(monkeypatch, story)
    original = deepcopy(story)
    first = run(story, store)
    assert len(provider.calls) == 5
    assert story == original
    first["segments"][0]["audio"] = {"duration": 2, "fingerprint": "new-audio"}
    second = run(first, store)

    assert len(provider.calls) == 5
    assert second["chapters"] == original["chapters"]
    assert second["segments"] == first["segments"]
    assert second["scenes"] == first["scenes"]
    assert second["characters"] == first["characters"]
    assert second["segments"][0]["audio"]["duration"] == 2


def test_chapter_scope_preserves_annotations_and_scenes_in_other_chapters(tmp_path, monkeypatch, story):
    store = Store(tmp_path)
    provider = Provider(monkeypatch, story)
    full = run(story, store)
    selected, other = [c["id"] for c in story["chapters"]]
    old_segments = [s for s in full["segments"] if s["chapter_id"] == other]
    old_scenes = [s for s in full["scenes"] if s["chapter_id"] == other]
    result = run(full, store, chapter_id=selected)

    assert [s for s in result["segments"] if s["chapter_id"] == other] == old_segments
    assert [s for s in result["scenes"] if s["chapter_id"] == other] == old_scenes
    assert len(provider.calls) == 5
    assert store.analysis_status(story["id"])["scope_chapter_id"] == selected


def test_cached_scene_boundaries_replay_without_duplicate_scenes(tmp_path, monkeypatch, story):
    store = Store(tmp_path)
    provider = Provider(monkeypatch, story)

    def add_boundary(stage, chapter_id, result):
        if stage == "directing":
            last = result["segments"][-1]["id"]
            result["scene_starts"] = [{"segment_id": last, "title": "A new scene", "summary": "A change.",
                                        "tone": "Quiet", "direction": "Pause briefly."}]
        return result

    provider.transform = add_boundary
    first = run(story, store)
    assert len(first["scenes"]) == 4
    repeated = run(first, store)
    assert repeated["scenes"] == first["scenes"]
    assert repeated["segments"] == first["segments"]
    selected, other = [c["id"] for c in story["chapters"]]
    scoped = run(repeated, store, chapter_id=selected)
    assert [s for s in scoped["scenes"] if s["chapter_id"] == other] == [s for s in first["scenes"] if s["chapter_id"] == other]
    assert len(scoped["scenes"]) == 4
    assert len(provider.calls) == 5


def test_earlier_chapter_scene_splits_survive_later_failure_and_scoped_retry(tmp_path, monkeypatch, story):
    store = Store(tmp_path)
    store.save_book(story)
    provider = Provider(monkeypatch, story)
    first_id, second_id = [c["id"] for c in story["chapters"]]
    should_fail = True

    def split_or_fail(stage, chapter_id, result):
        if stage == "directing":
            if chapter_id == second_id and should_fail:
                raise RuntimeError("Later chapter temporarily unavailable")
            result["scene_starts"] = [{"segment_id": result["segments"][-1]["id"], "title": "A new scene",
                                        "summary": "A change.", "tone": "Quiet", "direction": "Pause briefly."}]
        return result

    provider.transform = split_or_fail
    with pytest.raises(ValueError, match="Later chapter temporarily unavailable"):
        run(story, store)
    saved = store.book(story["id"])
    first_scenes = [s for s in saved["scenes"] if s["chapter_id"] == first_id]
    first_segments = [s for s in saved["segments"] if s["chapter_id"] == first_id]
    assert len(first_scenes) == 2
    assert len({s["scene_id"] for s in first_segments}) == 2
    assert store.analysis_status(story["id"])["chapters"][0]["directing_complete"] is True
    before = len(provider.calls)

    should_fail = False
    finished = run(saved, store, chapter_id=second_id)
    assert [(c["stage"], c["chapter_id"]) for c in provider.calls[before:]] == [("directing", second_id)]
    assert [s for s in finished["scenes"] if s["chapter_id"] == first_id] == first_scenes
    assert [s for s in finished["segments"] if s["chapter_id"] == first_id] == first_segments
    assert len(finished["scenes"]) == 4
    assert all(c["directing_complete"] for c in store.analysis_status(story["id"])["chapters"])


def test_separate_chapter_runs_keep_earlier_annotations_and_accumulate_cast(tmp_path, monkeypatch):
    story = parse_book("separate.txt", (
        "Chapter One\n\n“Hello,” Mara whispered.\n\n"
        "Chapter Two\n\n“Again,” Captain Voss replied."
    ).encode())
    store = Store(tmp_path)
    provider = Provider(monkeypatch, story)
    first_id, second_id = [c["id"] for c in story["chapters"]]
    first = run(story, store, chapter_id=first_id)
    first_segments = [s for s in first["segments"] if s["chapter_id"] == first_id]
    first_scenes = [s for s in first["scenes"] if s["chapter_id"] == first_id]
    mara = next(c for c in first["characters"] if c["name"] == "Mara")
    assert len(provider.calls) == 3
    assert first["analysis"]["status"] == "partial"

    second = run(first, store, chapter_id=second_id)
    assert len(provider.calls) == 6
    assert [s for s in second["segments"] if s["chapter_id"] == first_id] == first_segments
    assert [s for s in second["scenes"] if s["chapter_id"] == first_id] == first_scenes
    cast = {c["name"]: c for c in second["characters"]}
    assert {"Mara", "Captain Voss"} <= cast.keys()
    assert cast["Mara"]["id"] == mara["id"]
    assert cast["Mara"]["voice"] == mara["voice"]
    assert {r["chapter_id"] for r in store.character_references(story["id"]) if r["kind"] == "profile_evidence"} == {first_id, second_id}
    assert all(c["chapter_id"] != first_id for c in provider.calls[3:] if c["stage"] != "profiles")


def test_fresh_run_bypasses_all_cached_provider_results(tmp_path, monkeypatch, story):
    store = Store(tmp_path)
    provider = Provider(monkeypatch, story)
    first = run(story, store)
    provider.prefix = "New: "
    result = run(first, store, resume=False)
    assert len(provider.calls) == 10
    assert all(s["direction"].startswith("New: ") for s in result["segments"])


@pytest.mark.parametrize("change", ["source", "model", "reviewed_passage"])
def test_changed_inputs_invalidate_cached_requests(tmp_path, monkeypatch, story, change):
    store = Store(tmp_path)
    provider = Provider(monkeypatch, story)
    finished = run(story, store)
    prior_fingerprint = fingerprint(finished, "openai", "test-model")
    model = "test-model"
    if change == "model":
        model = "new-test-model"
    elif change == "source":
        finished["chapters"][0]["text"] = finished["chapters"][0]["text"].replace("Hello", "Howdy")
        for s in finished["segments"]:
            s["text"] = s["text"].replace("Hello", "Howdy")
    else:
        finished["segments"][0].update(edited=True, direction="My reviewed direction.")
    assert fingerprint(finished, "openai", model) != prior_fingerprint

    result = run(finished, store, model=model)
    assert len(provider.calls) == 10
    if change == "reviewed_passage":
        assert result["segments"][0]["direction"] == "My reviewed direction."
    if change == "source":
        assert result["chapters"][0]["text"] == finished["chapters"][0]["text"]


def test_references_anchor_unicode_source_and_resolve_valid_characters(tmp_path, monkeypatch, story):
    store = Store(tmp_path)
    Provider(monkeypatch, story)
    result = run(story, store)
    refs = store.character_references(story["id"])
    assert {r["kind"] for r in refs} == {"mention", "dialogue", "profile_evidence"}
    assert len({r["id"] for r in refs}) == len(refs)
    chapters = {c["id"]: c["text"] for c in result["chapters"]}
    characters = {c["id"] for c in result["characters"]}
    segments = {s["id"]: s for s in result["segments"]}
    for ref in refs:
        assert chapters[ref["chapter_id"]][ref["start"]:ref["end"]] == ref["quote"]
        assert ref["character_id"] in characters
        assert segments[ref["segment_id"]]["chapter_id"] == ref["chapter_id"]
        if ref["kind"] == "profile_evidence":
            assert ref["profile_description"] == "Vocal qualities unknown."
    assert any(r["quote"].startswith("“") for r in refs if r["kind"] == "dialogue")


def test_bad_evidence_gets_one_repair_then_only_validated_response_is_cached(tmp_path, monkeypatch, story):
    store = Store(tmp_path)
    provider = Provider(monkeypatch, story)
    attempts = []

    def bad_first_evidence(stage, chapter_id, result):
        if stage == "discovery":
            attempts.append(chapter_id)
            if len(attempts) == 1:
                result["characters"][0]["evidence"] = ["This quotation is invented."]
        return result

    provider.transform = bad_first_evidence
    result = run(story, store)
    assert len(provider.calls) == 6
    assert "SOURCE EVIDENCE CORRECTION" in provider.calls[1]["prompt"]
    assert "This quotation is invented." not in provider.calls[1]["prompt"]
    checkpoint = store.analysis_checkpoint(story["id"], fingerprint(result, "openai", "test-model"))
    assert "This quotation is invented." not in str(checkpoint)
    run(result, store)
    assert len(provider.calls) == 6


def test_second_bad_evidence_stops_without_caching_invalid_result(tmp_path, monkeypatch, story):
    store = Store(tmp_path)
    store.save_book(story)
    provider = Provider(monkeypatch, story)

    def always_bad(stage, chapter_id, result):
        result["characters"][0]["evidence"] = ["Invented evidence."]
        return result

    provider.transform = always_bad
    with pytest.raises(ValueError, match="single evidence repair attempt also failed"):
        run(story, store)
    assert len(provider.calls) == 2
    saved = store.analysis_checkpoint(story["id"], fingerprint(story, "openai", "test-model"))
    assert saved["units"] == {}
    assert store.book(story["id"]) == story
    assert store.character_references(story["id"]) == []


def test_cancel_after_validated_unit_keeps_checkpoint_for_resume(tmp_path, monkeypatch, story):
    store = Store(tmp_path)
    provider = Provider(monkeypatch, story)
    cancelled = False

    def progress(done, total, message):
        nonlocal cancelled
        if "character evidence saved" in message:
            cancelled = True

    with pytest.raises(analysis.AnalysisCancelled):
        run(story, store, progress=progress, cancelled=lambda: cancelled)
    assert len(provider.calls) == 1
    checkpoint = store.analysis_checkpoint(story["id"], fingerprint(story, "openai", "test-model"))
    assert checkpoint["status"] == "interrupted"
    assert len(checkpoint["units"]) == 1
    run(store.book(story["id"]), store)
    assert len(provider.calls) == 5


def test_profile_aliases_unify_character_ids_and_reference_history(tmp_path, monkeypatch):
    story = parse_book("aliases.txt", (
        "Chapter One\n\n“Hello,” Mara whispered.\n\n"
        "Chapter Two\n\n“Again,” Captain Voss replied. Mara was Captain Voss."
    ).encode())
    store = Store(tmp_path)
    provider = Provider(monkeypatch, story)

    def reconcile(stage, chapter_id, result):
        if stage == "profiles":
            result["characters"] = [{"name": "Mara", "aliases": ["Captain Voss"],
                                      "description": "Both names identify one person.", "direction": "Restrained.",
                                      "evidence": ["Mara whispered", "Captain Voss replied"]}]
        return result

    provider.transform = reconcile
    result = run(story, store)
    cast = [c for c in result["characters"] if c["id"] not in {"narrator", "unassigned"}]
    assert len(cast) == 1
    assert cast[0]["aliases"] == ["Captain Voss"]
    assert {s["speaker_id"] for s in result["segments"] if s["kind"] == "dialogue"} == {cast[0]["id"]}
    refs = store.character_references(story["id"])
    assert {r["character_id"] for r in refs} == {cast[0]["id"]}
    assert {r["chapter_id"] for r in refs if r["kind"] == "profile_evidence"} == {c["id"] for c in story["chapters"]}
