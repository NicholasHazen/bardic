"""Durability and publication boundaries for chapter-by-chapter analysis."""
import copy
import sqlite3

import pytest

from bardic.store import Store


def book(book_id="book"):
    return {"id": book_id, "title": "Original", "segments": [
        {"id": "segment-1", "text": "Mira whispered.", "audio": {"duration": 1}},
    ]}


def reference(ref_id="ref-1", character_id="mira", chapter_id="chapter-1"):
    return {"id": ref_id, "character_id": character_id, "chapter_id": chapter_id,
            "segment_id": "segment-1", "start": 0, "end": 4, "quote": "Mira",
            "kind": "mention", "confidence": .9, "provider": "openai", "model": "test-model"}


def checkpoint(status="running", references=None):
    return {"fingerprint": "fingerprint", "provider": "openai", "model": "test-model",
            "status": status, "chapters": [
                {"id": "chapter-1", "title": "Chapter 1", "stage": "directing", "status": "running",
                 "completed_units": 1, "total_units": 2, "discovery_complete": True,
                 "directing_complete": False},
                {"id": "chapter-2", "title": "Chapter 2", "stage": "discovery", "status": "pending",
                 "completed_units": 0, "total_units": 2},
            ], "completed_units": 1, "total_units": 4, "stage": "directing",
            "current_chapter_id": "chapter-1", "scope_chapter_id": None,
            "working_book": book(), "units": {"chapter-1:discovery": {"characters": ["mira"]}},
            "references": [reference()] if references is None else references, "error": None}


def test_checkpoint_fingerprint_mismatch_is_not_resumable_and_does_not_mutate_book(tmp_path):
    store = Store(tmp_path)
    original = book()
    store.save_book(original)
    progress = checkpoint()
    progress["working_book"]["title"] = "Unpublished analysis"
    saved = store.save_analysis_checkpoint("book", "input-v1", progress)

    assert saved["fingerprint"] == "input-v1"
    assert saved["updated_at"]
    assert progress["fingerprint"] == "fingerprint"
    assert "updated_at" not in progress
    assert store.analysis_checkpoint("book", "input-v1") == saved
    assert store.analysis_checkpoint("book", "input-v2") is None
    assert store.analysis_checkpoint("missing", "input-v1") is None
    assert store.book("book") == original


def test_references_are_indexed_and_replaced_only_for_their_book(tmp_path):
    store = Store(tmp_path)
    refs = [reference(), reference("ref-2", "jon", "chapter-2")]
    refs[0].update(kind="profile_evidence", profile_description="Soft spoken", profile_direction="Quietly")
    store.save_analysis_checkpoint("book", "v1", checkpoint(references=refs))
    store.save_analysis_checkpoint("other-book", "v1", checkpoint(references=[reference()]))

    assert store.character_references("book") == refs
    assert store.character_references("book", "mira") == refs[:1]
    assert store.character_references("book", "missing") == []
    assert store.character_references("missing") == []
    with store.connect() as conn:
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(character_references)")}
    assert {"character_references_character", "character_references_chapter"} <= indexes

    store.save_analysis_checkpoint("book", "v2", checkpoint(references=refs[1:]))
    assert store.analysis_checkpoint("book", "v1") is None
    assert store.character_references("book") == refs[1:]
    assert store.character_references("other-book") == [reference()]


def test_status_only_exposes_progress_and_chapter_flags(tmp_path):
    store = Store(tmp_path)
    assert store.analysis_status("missing") is None
    progress = checkpoint()
    progress.update(api_key="secret-value", source_text="whole source", private_response={"text": "private"})
    progress["chapters"][0].update(source_text="chapter source", api_key="secret-value")
    store.save_analysis_checkpoint("book", "v1", progress)

    summary = store.analysis_status("book")
    assert set(summary) == {"fingerprint", "provider", "model", "status", "chapters",
                            "completed_units", "total_units", "stage", "current_chapter_id",
                            "scope_chapter_id", "error", "updated_at"}
    assert summary["chapters"][0]["discovery_complete"] is True
    assert summary["chapters"][0]["directing_complete"] is False
    assert "source_text" not in summary["chapters"][0]
    assert "api_key" not in summary["chapters"][0]
    assert "secret-value" not in str(summary)


def test_analysis_commit_publishes_book_checkpoint_and_refs_together(tmp_path):
    store = Store(tmp_path)
    store.save_book(book())
    updated = book()
    updated["title"] = "Analyzed"
    updated["segments"][0]["audio"] = None
    progress = checkpoint(status="completed")

    assert store.commit_analysis(updated, "v1", progress) == updated
    assert store.book("book") == updated
    assert store.analysis_checkpoint("book", "v1")["status"] == "completed"
    assert store.character_references("book") == [reference()]


@pytest.mark.parametrize("commit_book", [False, True])
def test_invalid_reference_rolls_back_entire_update(tmp_path, commit_book):
    store = Store(tmp_path)
    original = book()
    store.save_book(original)
    previous = store.save_analysis_checkpoint("book", "v1", checkpoint())
    invalid = checkpoint(references=[reference(), reference()])
    changed = copy.deepcopy(original)
    changed["title"] = "Must not publish"
    changed["segments"][0]["audio"] = None

    with pytest.raises(sqlite3.IntegrityError):
        if commit_book:
            store.commit_analysis(changed, "v2", invalid)
        else:
            store.save_analysis_checkpoint("book", "v2", invalid)

    assert store.book("book") == original
    assert store.analysis_checkpoint("book", "v1") == previous
    assert store.analysis_checkpoint("book", "v2") is None
    assert store.character_references("book") == [reference()]


def test_deleting_checkpoint_removes_refs_without_touching_book_or_other_run(tmp_path):
    store = Store(tmp_path)
    original = book()
    store.save_book(original)
    store.save_analysis_checkpoint("book", "v1", checkpoint())
    store.save_analysis_checkpoint("other-book", "v1", checkpoint())
    store.delete_analysis_checkpoint("book")
    store.delete_analysis_checkpoint("missing")

    assert store.analysis_checkpoint("book", "v1") is None
    assert store.analysis_status("book") is None
    assert store.character_references("book") == []
    assert store.book("book") == original
    assert store.analysis_checkpoint("other-book", "v1") is not None
    assert store.character_references("other-book") == [reference()]


@pytest.mark.parametrize("status", ["running", "queued"])
def test_restart_marks_active_analysis_interrupted_and_preserves_work(tmp_path, status):
    store = Store(tmp_path)
    progress = checkpoint(status=status)
    store.save_analysis_checkpoint("book", "v1", progress)
    analysis_job = store.create_job("book", "analyze")
    render_job = store.create_job("other-book", "render")
    store.update_job(analysis_job["id"], status=status)

    restored = Store(tmp_path)
    recovered = restored.analysis_checkpoint("book", "v1")
    assert recovered["status"] == "interrupted"
    assert recovered["chapters"][0]["status"] == "interrupted"
    assert recovered["chapters"][1]["status"] == "pending"
    assert recovered["completed_units"] == 1
    assert recovered["working_book"] == progress["working_book"]
    assert recovered["units"] == progress["units"]
    assert recovered["references"] == restored.character_references("book") == progress["references"]
    # The messages describe the condition, not a button to press.
    assert restored.job(analysis_job["id"])["message"].startswith("The server restarted before this analysis finished.")
    assert restored.job(render_job["id"])["message"].startswith("The server restarted before this job finished.")


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "interrupted"])
def test_restart_keeps_finished_checkpoint_status_and_error(tmp_path, status):
    store = Store(tmp_path)
    progress = checkpoint(status=status)
    progress["error"] = "Original diagnostic" if status != "completed" else None
    saved = store.save_analysis_checkpoint("book", "v1", progress)

    restored = Store(tmp_path)
    assert restored.analysis_checkpoint("book", "v1") == saved
