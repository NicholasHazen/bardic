"""Explicit cross-book identities, durable provenance and spoiler-scoped retrieval."""
from copy import deepcopy
import json
import sqlite3

import pytest

from spintails.series import SeriesRepository, source_hash
from spintails.store import Store


def book(book_id, text="Mira spoke softly."):
    return {"id": book_id, "title": f"Book {book_id}", "author": "Test Author",
            "chapters": [{"id": "chapter-1", "title": "The gate", "text": text}],
            "characters": [{"id": "narrator", "name": "Narrator"}, {"id": "unassigned", "name": "Unassigned"},
                           {"id": "mira", "name": "Mira", "description": "Reviewed profile", "edited": True}],
            "segments": [{"id": "segment-1", "chapter_id": "chapter-1", "start": 0, "end": len(text),
                          "text": text, "audio": None}]}


def reference(text="Mira spoke softly.", description="A soft voice", **fields):
    return {"id": "reference-1", "character_id": "mira", "chapter_id": "chapter-1",
            "segment_id": "segment-1", "start": 0, "end": len(text), "quote": text,
            "kind": "profile_evidence", "profile_description": description, "profile_direction": "Softly",
            "provider": "test", "model": "test-model", "confidence": .9, **fields}


def checkpoint(refs):
    return {"status": "completed", "references": refs, "chapters": [], "units": {}}


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path)
    for bid in ("one", "nine", "ten", "unrelated"):
        store.save_book(book(bid))
    repo = SeriesRepository(store)
    saga = repo.create_series("The Lantern Books")
    for bid, position in (("one", 1), ("nine", 9), ("ten", 10)):
        repo.set_membership(bid, saga["id"], position)
    identity = repo.create_character(saga["id"], "Mira")
    return store, repo, saga, identity


def test_schema_is_additive_and_membership_is_explicit(setup):
    store, repo, saga, identity = setup
    assert repo.membership("unrelated") is None
    assert repo.links_for_book("nine") == []
    assert repo.context_for_book("nine")["characters"] == []
    listed = repo.list_series()
    assert [item["book_id"] for item in listed[0]["books"]] == ["one", "nine", "ten"]
    assert listed[0]["character_count"] == 1
    assert repo.list_characters(saga["id"])[0] == identity
    assert store.book("nine")["characters"][2]["edited"] is True


@pytest.mark.parametrize("position", [None, "9", True, float("inf"), float("nan"), -1, 1_000_001])
def test_invalid_reading_order_does_not_change_membership(setup, position):
    _, repo, saga, _ = setup
    before = repo.membership("nine")
    with pytest.raises(ValueError):
        repo.set_membership("nine", saga["id"], position)
    assert repo.membership("nine") == before


def test_duplicate_reading_order_rejected_and_decimal_side_stories_supported(setup):
    _, repo, saga, _ = setup
    with pytest.raises(ValueError, match="already has"):
        repo.set_membership("unrelated", saga["id"], 9)
    assert repo.membership("unrelated") is None
    assert repo.set_membership("unrelated", saga["id"], 1.5)["position"] == 1.5


def test_same_name_and_same_book_character_id_never_create_identity_links(setup):
    store, repo, _, identity = setup
    store.save_analysis_checkpoint("one", "v1", checkpoint([reference()]))
    assert repo.context_for_book("nine")["characters"] == []
    repo.link_character("one", "mira", identity["id"])
    assert repo.context_for_book("nine")["characters"] == []
    repo.link_character("nine", "mira", identity["id"])
    assert repo.context_for_book("nine")["included_observations"] == 1


def test_series_are_isolated_and_system_speakers_cannot_be_linked(setup):
    _, repo, saga, identity = setup
    other = repo.create_series("Other books")
    other_identity = repo.create_character(other["id"], "Mira")
    repo.set_membership("unrelated", other["id"], 1)
    with pytest.raises(ValueError, match="this book's series"):
        repo.link_character("nine", "mira", other_identity["id"])
    with pytest.raises(ValueError, match="cannot be linked"):
        repo.link_character("nine", "narrator", identity["id"])
    with pytest.raises(KeyError, match="Character not found"):
        repo.link_character("nine", "missing", identity["id"])
    with pytest.raises(KeyError, match="Series character"):
        repo.link_character("nine", "mira", "missing")
    assert repo.links_for_book("nine") == []
    assert len(repo.list_characters(saga["id"])) == 1


def test_context_uses_only_earlier_explicitly_linked_books_and_preserves_provenance(setup):
    store, repo, saga, identity = setup
    for bid in ("one", "nine", "ten"):
        repo.link_character(bid, "mira", identity["id"])
        store.save_analysis_checkpoint(bid, "v1", checkpoint([reference(description=f"Observation in {bid}")]))
    context = repo.context_for_book("nine")
    assert context["included_observations"] == context["available_observations"] == 1
    item = context["characters"][0]
    assert item["character_id"] == "mira"
    assert item["series_character_id"] == identity["id"]
    observation = item["observations"][0]
    assert observation["book_id"] == "one"
    assert observation["character_id"] == "mira"
    assert observation["book_title"] == "Book one"
    assert observation["chapter_id"] == "chapter-1"
    assert observation["chapter_title"] == "The gate"
    assert observation["source_hash"] == source_hash("Mira spoke softly.")
    assert observation["quote"] == "Mira spoke softly."
    assert observation["provider"] == "test" and observation["model"] == "test-model"
    assert "Observation in ten" not in json.dumps(context)
    assert "Observation in nine" not in json.dumps(context)
    assert repo.context_for_book("one")["included_observations"] == 0


def test_membership_move_or_removal_clears_only_that_books_identity_links(setup):
    store, repo, _, identity = setup
    for bid in ("one", "nine"):
        repo.link_character(bid, "mira", identity["id"])
    store.save_analysis_checkpoint("nine", "v1", checkpoint([reference()]))
    other = repo.create_series("Another saga")
    repo.set_membership("nine", other["id"], 1)
    assert repo.links_for_book("nine") == []
    assert len(repo.links_for_book("one")) == 1
    assert len(repo.observations("nine")) == 1
    assert repo.set_membership("nine") is None
    assert repo.membership("nine") is None
    assert repo.observations("nine")


def test_repeat_link_is_idempotent_unlink_changes_context_fingerprint(setup):
    store, repo, _, identity = setup
    store.save_analysis_checkpoint("one", "v1", checkpoint([reference()]))
    for bid in ("one", "nine"):
        repo.link_character(bid, "mira", identity["id"])
    first = repo.context_for_book("nine")
    before = repo.links_for_book("nine")
    repo.link_character("nine", "mira", identity["id"])
    assert repo.links_for_book("nine") == before
    assert repo.context_for_book("nine") == first
    repo.unlink_character("one", "mira")
    after = repo.context_for_book("nine")
    assert after["characters"] == []
    assert after["fingerprint"] != first["fingerprint"]


def test_reading_order_and_source_changes_invalidate_context_and_stale_anchors_are_excluded(setup):
    store, repo, saga, identity = setup
    for bid in ("one", "nine"):
        repo.link_character(bid, "mira", identity["id"])
    store.save_analysis_checkpoint("one", "v1", checkpoint([reference()]))
    first = repo.context_for_book("nine")
    repo.set_membership("one", saga["id"], 11)
    later = repo.context_for_book("nine")
    assert later["included_observations"] == 0
    assert later["fingerprint"] != first["fingerprint"]
    repo.set_membership("one", saga["id"], 1)
    changed = store.book("one")
    changed["chapters"][0]["text"] += " Additional source."
    store.save_book(changed)
    stale = repo.context_for_book("nine")
    assert stale["included_observations"] == 0
    assert stale["fingerprint"] != first["fingerprint"]
    assert len(repo.observations("one")) == 1


def test_observations_survive_checkpoint_replacement_deletion_and_restart(setup):
    store, repo, _, _ = setup
    before = deepcopy(store.book("one"))
    saved = checkpoint([reference()])
    store.save_analysis_checkpoint("one", "v1", saved)
    store.save_analysis_checkpoint("one", "v1", saved)
    assert len(repo.observations("one")) == 1
    store.save_analysis_checkpoint("one", "v2", checkpoint([reference(description="A strained voice")]))
    observations = repo.observations("one")
    assert {o["description"] for o in observations} == {"A soft voice", "A strained voice"}
    store.delete_analysis_checkpoint("one")
    assert store.character_references("one") == []
    assert repo.observations("one") == observations
    assert store.book("one") == before
    restored = SeriesRepository(Store(store.root))
    assert restored.observations("one") == observations


def test_invalid_references_do_not_become_observations(setup):
    store, repo, _, _ = setup
    refs = [reference(quote="Invented evidence"), reference(id="bad-span", start=-1),
            reference(id="unknown", character_id="unknown"), reference(id="unknown-chapter", chapter_id="missing"),
            reference(id="bad-kind", kind="unsupported"), reference(id="bad-bool", start=False)]
    store.save_analysis_checkpoint("one", "v1", checkpoint(refs))
    assert repo.observations("one") == []


def test_observations_are_atomic_with_book_and_checkpoint_transaction(setup):
    store, repo, _, _ = setup
    original = store.book("one")
    changed = deepcopy(original)
    changed["title"] = "Must roll back"
    with pytest.raises(sqlite3.IntegrityError):
        store.commit_analysis(changed, "v1", checkpoint([reference(), reference()]))
    assert store.book("one") == original
    assert repo.observations("one") == []
    assert store.analysis_status("one") is None


def test_mentions_are_retained_but_not_sent_as_profile_context(setup):
    store, repo, _, identity = setup
    for bid in ("one", "nine"):
        repo.link_character(bid, "mira", identity["id"])
    store.save_analysis_checkpoint("one", "v1", checkpoint([reference(kind="mention")]))
    assert len(repo.observations("one")) == 1
    assert repo.context_for_book("nine")["included_observations"] == 0


def test_context_limits_are_real_and_sampling_keeps_early_and_late_observations(setup):
    store, repo, _, identity = setup
    for bid in ("one", "nine"):
        repo.link_character(bid, "mira", identity["id"])
    text = "".join(f"Mira line {i}. " for i in range(12))
    store.save_book(book("one", text))
    refs = []
    for i in range(12):
        quote = f"Mira line {i}."
        start = text.index(quote)
        refs.append(reference(id=f"ref-{i}", start=start, end=start + len(quote), quote=quote))
    store.save_analysis_checkpoint("one", "v1", checkpoint(refs))
    context = repo.context_for_book("nine", max_observations_per_character=3)
    assert context["available_observations"] == 12 and context["included_observations"] == 3
    assert context["truncated"] is True
    selected = context["characters"][0]["observations"]
    assert selected[0]["quote"] == "Mira line 0." and selected[-1]["quote"] == "Mira line 11."
    small = repo.context_for_book("nine", max_chars=800)
    assert len(json.dumps(small["characters"], ensure_ascii=False)) <= 800
    assert small["context_chars"] <= 800 and small["truncated"]
    assert repo.context_for_book("nine", max_chars=256)["characters"] == []


def test_removed_book_character_is_visible_as_stale_link_but_not_used_in_context(setup):
    store, repo, _, identity = setup
    for bid in ("one", "nine"):
        repo.link_character(bid, "mira", identity["id"])
    store.save_analysis_checkpoint("one", "v1", checkpoint([reference()]))
    source = store.book("one")
    source["characters"] = source["characters"][:2]
    store.save_book(source)
    assert repo.links_for_book("one")[0]["stale"] is True
    assert repo.context_for_book("nine")["included_observations"] == 0


def test_upgrade_backfills_valid_existing_references_once(tmp_path):
    store = Store(tmp_path)
    store.save_book(book("one"))
    with store.connect() as conn:
        conn.execute("DROP TABLE character_observations")
        conn.execute("INSERT INTO character_references VALUES (?,?,?,?,?,?)",
                     ("one", "reference-1", "mira", "chapter-1", "segment-1", json.dumps(reference())))
    upgraded = SeriesRepository(Store(tmp_path))
    assert len(upgraded.observations("one")) == 1
    again = SeriesRepository(Store(tmp_path))
    assert again.observations("one") == upgraded.observations("one")


@pytest.mark.parametrize("name", ["", "  ", None, "x" * 201])
def test_names_are_validated(setup, name):
    _, repo, saga, _ = setup
    with pytest.raises(ValueError):
        repo.create_series(name)
    with pytest.raises(ValueError):
        repo.create_character(saga["id"], name)


def test_duplicate_series_names_are_rejected_but_namesake_characters_are_separate(setup):
    _, repo, saga, identity = setup
    with pytest.raises(ValueError, match="already exists"):
        repo.create_series("the lantern books")
    other = repo.create_character(saga["id"], "Mira")
    assert other["id"] != identity["id"]
    assert len(repo.list_characters(saga["id"])) == 2


@pytest.mark.parametrize("method,args", [("membership", ("missing",)), ("context_for_book", ("missing",)),
                                        ("list_characters", ("missing",)), ("links_for_book", ("missing",))])
def test_missing_resources_are_not_silently_created(setup, method, args):
    _, repo, _, _ = setup
    with pytest.raises(KeyError):
        getattr(repo, method)(*args)
