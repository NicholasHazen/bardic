"""The wire boundary: what a response carries, as opposed to what storage holds.

Storage keeps its own names and shapes (a stored book may lack fields an older version never wrote, and its
passages are ``segments``). The contract (``contract/openapi.json``) describes the API. The helpers here turn what
storage holds into what the contract says: they fill the fields the contract says are always sent, and they rename
what the wire names differently (the reader unit is a ``passage`` on the wire, a ``segment`` in storage; a Breeze
voice's ``revision`` is its ``voice_revision``). Nothing else in the code renames, and stored data is never rewritten.

Fill with :func:`complete`. A presenter takes a copy first.
"""
from __future__ import annotations

from typing import Any, Iterable

# The always-sent fields of the book document (contract: Book and its parts). A stored book may lack them: an older
# import, an unrefined character, a scene the pipeline gave no title. Each is filled with the value that means the same.
CHAPTER_FIELDS = ("kind", "title_source", "source_href", "logical_sections", "narrative_order")
SCENE_FIELDS = ("title", "summary", "tone", "direction")
CHARACTER_NULLS = ("profile_refined", "profile_provider", "profile_model", "profile_priority", "profile_state",
                   "profile_provisional")
CHARACTER_LISTS = ("evidence", "former_names")
PRONUNCIATION_FIELDS = ("providers", "character_id", "note")
ANALYSIS_FIELDS = ("model", "phase", "profiles_provisional")
SPEAKER_CHECK_FIELDS = ("speaker_id", "speaker", "tag_conflict")
USAGE_FIELDS = ("input_tokens", "output_tokens", "total_tokens")
EVENT_FIELDS = ("artifact_id", "attempt_id", "error", "cached_unit_key", "repair")
# Every ResourceOperation field the ledger, an analysis attempt or a cache event may leave out.
OPERATION_FIELDS = ("chapter_id", "provider", "model", "completed_at", "input_tokens", "output_tokens", "cached_input_tokens",
                    "cache_write_input_tokens", "output_bytes", "elapsed_seconds", "cpu_seconds", "audio_seconds",
                    "price_as_of", "price_source", "usage_source", "cpu_scope", "artifact_id", "asset_id", "http_status",
                    "validation_state")
RUN_FIELDS = ("started_at", "completed_at", "outcomes", "series_run_id")
OUTCOME_FIELDS = ("reason", "step_run_id", "scopes", "accepted", "error")
REFERENCE_FIELDS = ("confidence", "provider", "model", "step", "version_id", "origin", "projection")


def complete(item: dict[str, Any], names: Iterable[str], value: Any = None) -> dict[str, Any]:
    """``item`` with every name in ``names`` present: a missing one gets ``value`` (null by default). Changes ``item``."""
    for name in names:
        item.setdefault(name, value)
    return item


def analysis_summary(analysis: dict[str, Any] | None) -> dict[str, Any] | None:
    """The book's ``analysis`` summary with its always-sent fields."""
    return complete(analysis, ANALYSIS_FIELDS) if isinstance(analysis, dict) else analysis


def run(record: dict[str, Any]) -> dict[str, Any]:
    """A stored pipeline run as the API presents it: every field present (null while unknown or not applicable)."""
    result = complete(dict(record), RUN_FIELDS)
    if isinstance(result["outcomes"], dict):
        result["outcomes"] = {step: _outcome(outcome) for step, outcome in result["outcomes"].items()}
    return result


def _outcome(outcome: Any) -> Any:
    if not isinstance(outcome, dict):
        return outcome
    result = {("scope_count" if key == "scopes" else key): value for key, value in outcome.items()}
    return complete(result, tuple("scope_count" if name == "scopes" else name for name in OUTCOME_FIELDS))


def reference(row: dict[str, Any]) -> dict[str, Any]:
    """A stored character reference as the API presents it: every provenance field present (null when unknown)."""
    return complete(dict(row), REFERENCE_FIELDS)


# ---------------------------------------------------------------------------------------------- passage naming
# The wire says "passage"; storage and the Python code say "segment". Responses are translated on the way out
# (`WireResponse` in app.py calls `to_wire`); request models accept the wire name through a field alias.
KEY_RENAMES = {
    "segment_id": "passage_id",
    "segment_ids": "passage_ids",
    "first_segment_id": "first_passage_id",
    "last_segment_id": "last_passage_id",
    "segment_count": "passage_count",
    "scope_start_segment_id": "scope_start_passage_id",
    "focus_segment_id": "focus_passage_id",
}
# Keys whose value is retained JSON that is shown as stored (ArtifactDetail.payload): never renamed inside.
VERBATIM_KEYS = frozenset({"payload"})


def to_wire(value: Any) -> Any:
    """``value`` with the internal key names of the passage family replaced by their wire names, at any depth.

    Only keys are renamed, never values. A ``payload`` (an artifact's retained JSON) is left exactly as stored.
    """
    if isinstance(value, dict):
        return {KEY_RENAMES.get(key, key): value[key] if key in VERBATIM_KEYS else to_wire(value[key]) for key in value}
    if isinstance(value, list):
        return [to_wire(item) for item in value]
    return value


def book(presented: dict[str, Any]) -> dict[str, Any]:
    """A presented book (``Runtime.present``) as the API sends it: ``segments`` are ``passages``, and a character's
    Breeze voice pin names its revision ``voice_revision``. Changes and returns ``presented``, which is a copy."""
    result = {("passages" if key == "segments" else key): value for key, value in presented.items()}
    for character in result.get("characters", []):
        for choice in (character.get("voices") or {}).values():
            if isinstance(choice, dict) and "revision" in choice:
                choice["voice_revision"] = choice.pop("revision")
    return result


def provider_timing(timing: dict[str, Any] | None) -> dict[str, Any] | None:
    """Breeze sentence timing as the API sends it: ``sentences`` with ``start_seconds`` and ``end_seconds``."""
    if not isinstance(timing, dict):
        return timing
    result = {("sentences" if key == "segments" else key): value for key, value in timing.items()}
    result["sentences"] = [
        {("start_seconds" if key == "start" else "end_seconds" if key == "end" else key): value for key, value in span.items()}
        if isinstance(span, dict) else span for span in result.get("sentences", [])]
    return result


def units(counts: dict[str, Any] | None) -> dict[str, Any] | None:
    """A step version's unit counts as the API sends them: ``cached`` (a count) is ``cached_units``."""
    if not isinstance(counts, dict):
        return counts
    return {("cached_units" if key == "cached" else key): value for key, value in counts.items()}


def breeze_status(view: dict[str, Any]) -> dict[str, Any]:
    """The saved Breeze check as the API sends it: a server voice's ``revision`` is its ``voice_revision``."""
    result = dict(view)
    result["voices"] = [{("voice_revision" if key == "revision" else key): value for key, value in voice.items()}
                        for voice in view.get("voices", [])]
    return result
