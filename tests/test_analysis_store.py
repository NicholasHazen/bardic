"""Store boundaries that analysis relies on: reference reads and restart recovery of jobs.

The removed Classic engine's checkpoint (its save, commit, status and restart
recovery) went with its table in the Classic data drop (docs/CLASSIC-REMOVAL.md).
"""
import pytest

from bardic.store import Store
from classic_fixtures import write_references


def reference(ref_id="ref-1", character_id="mira", chapter_id="chapter-1"):
    return {"id": ref_id, "character_id": character_id, "chapter_id": chapter_id,
            "segment_id": "segment-1", "start": 0, "end": 4, "quote": "Mira",
            "kind": "mention", "confidence": .9, "provider": "openai", "model": "test-model"}


def test_references_are_indexed_and_read_only_for_their_book(tmp_path):
    store = Store(tmp_path)
    refs = [reference(), reference("ref-2", "jon", "chapter-2")]
    refs[0].update(kind="profile_evidence", profile_description="Soft spoken", profile_direction="Quietly")
    with store.connect() as conn:
        write_references(conn, "book", refs)
        write_references(conn, "other-book", [reference()])

    assert store.character_references("book") == refs
    assert store.character_references("book", "mira") == refs[:1]
    assert store.character_references("book", "missing") == []
    assert store.character_references("missing") == []
    assert store.character_references("other-book") == [reference()]
    with store.connect() as conn:
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(character_references)")}
    assert {"character_references_character", "character_references_chapter"} <= indexes


@pytest.mark.parametrize("status", ["running", "queued"])
def test_restart_marks_active_jobs_interrupted_with_a_neutral_message(tmp_path, status):
    store = Store(tmp_path)
    analysis_job = store.create_job("book", "analyze")
    pipeline_job = store.create_job("book", "pipeline")
    render_job = store.create_job("other-book", "render")
    for job in (analysis_job, pipeline_job):
        store.update_job(job["id"], status=status)

    restored = Store(tmp_path)
    for job in (analysis_job, pipeline_job, render_job):
        assert restored.job(job["id"])["status"] == "interrupted"
    # The messages describe the condition, not a button to press.
    assert restored.job(analysis_job["id"])["message"].startswith("The server restarted before this analysis finished.")
    assert restored.job(pipeline_job["id"])["message"].startswith("The server restarted before this analysis finished.")
    assert restored.job(render_job["id"])["message"].startswith("The server restarted before this job finished.")


def test_a_library_never_creates_the_checkpoint_table(tmp_path):
    store = Store(tmp_path)
    Store(tmp_path)
    with store.connect() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "analysis_checkpoints" not in tables and "character_references" in tables
