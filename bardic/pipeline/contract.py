"""The contract every analysis pipeline step implements.

A step is a small, declarative object. The runner, repository and API never
special-case a step ID; adding, removing or reordering steps only changes the
registry list in ``bardic.pipeline.steps``.

Lifecycle of one step run::

    units(ctx)            plan the work (LLM requests or local computations)
    execute/validate      produce one validated result per unit (runner caches)
    assemble(ctx, done)   group unit results into one payload per scope
    -> immutable candidate versions (one artifact per scope)
    apply(book, heads)    user or policy accepts: project accepted payloads
                          onto the reader's book, respecting manual edits

Projection rules a step must follow:

* ``apply`` writes only the fields declared in ``owns`` and is idempotent.
  A scope absent from ``payloads`` is left untouched.
* ``apply`` never changes source text, passage offsets or passage IDs, and
  never deletes a character (IDs are referenced by voices, takes and series).
* A field listed by :func:`locked` is a manual edit and is never overwritten;
  the difference is reported as a :class:`Conflict` instead.
* ``capture(book, scope)`` returns the payload that ``apply`` would need to
  reproduce the current projection for a scope, or ``None`` when that state
  cannot be reconstructed from the projection (for example discovery, whose
  per-chapter candidates are merged away). Captures record the existing state
  as a baseline or external version so that it can be restored later.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

SCOPES = ('book', 'chapter', 'character')
METHODS = ('plain', 'llm')
GATES = ('auto', 'review')
RESERVED_CHARACTERS = frozenset({'narrator', 'unassigned'})
ALL_FIELDS = '*'


def locked(item: dict, field_name: str) -> bool:
    """True when a person edited this field; generated output must not replace it.

    ``edited_fields`` lists edited field names. Items edited before per-field
    tracking only carry ``edited: true``; every field of those stays locked.
    """
    fields = item.get('edited_fields')
    if isinstance(fields, list):
        return ALL_FIELDS in fields or field_name in fields
    return bool(item.get('edited'))


@dataclass(frozen=True)
class LLMRequest:
    """One structured-output request. Its exact contents form the cache identity."""
    prompt: str
    schema: dict
    output_cap: int


@dataclass
class Unit:
    """One independently cacheable piece of step work.

    ``key`` locates the work inside the book (e.g. ``ch_1:0-24000``); two units
    of one step never share it. ``scope`` is the version scope its result
    belongs to. ``data`` is step-private planning state, never persisted.
    """
    key: str
    scope: str
    label: str
    request: LLMRequest | None = None
    data: dict = field(default_factory=dict)
    # Source chapter this unit reads; its retained source artifact becomes a dependency.
    chapter_id: str | None = None
    # Other retained artifact IDs this unit's request actually read (e.g. series context).
    dependencies: tuple[str, ...] = ()


@dataclass
class Conflict:
    """A generated value that was not applied, or an input that could not be."""
    scope: str
    item_id: str
    field: str
    reason: str

    def as_dict(self):
        return {'scope': self.scope, 'item_id': self.item_id, 'field': self.field, 'reason': self.reason}


@dataclass
class StepContext:
    """Read-only inputs for planning and assembling one step run."""
    book: dict
    store: Any
    # Accepted payloads of declared input steps: {step_id: {scope: payload}}.
    inputs: dict[str, dict[str, dict]]
    # Artifact IDs of those accepted payloads, recorded as version inputs.
    input_heads: dict[str, dict[str, str]]
    chapter_ids: frozenset[str] | None = None
    provider: str = 'local'
    model: str | None = None
    cancelled: Callable[[], bool] = lambda: False

    def selected_chapters(self, *, eligible_only=True):
        from ..preprocessing import eligible_chapters
        if self.chapter_ids is not None:
            return [c for c in self.book['chapters'] if c['id'] in self.chapter_ids]
        return eligible_chapters(self.book) if eligible_only else list(self.book['chapters'])


class Step:
    """Base class. Subclasses set the class attributes and override the hooks."""

    id: str = ''
    label: str = ''
    summary: str = ''
    method: str = 'plain'
    scope: str = 'book'
    # Upstream step IDs whose ACCEPTED payloads this step reads.
    inputs: tuple[str, ...] = ()
    # Projection fields this step overwrites, as 'collection.field'.
    owns: tuple[str, ...] = ()
    # Bump when prompts, schemas or logic change: new cache keys, old versions stay readable.
    version: int = 1
    # Maximum concurrent units within one run (the run's concurrency also caps it).
    parallel: int = 1
    default_gate: str = 'auto'
    # Which configured model a new LLM step uses by default: 'scan' (economy) or 'analysis'.
    default_model_role: str = 'analysis'
    # Whether a chapter selection narrows this step's work.
    chapter_scoped: bool = False
    # capture() can reconstruct this step's projection (enables baseline/external versions).
    capturable: bool = False
    # apply() only adds (never replaces); accepting applies just the changed scopes.
    accumulative: bool = False

    # --- planning and execution -------------------------------------------------
    def units(self, ctx: StepContext) -> list[Unit]:
        raise NotImplementedError

    def execute(self, ctx: StepContext, unit: Unit) -> dict:
        """Compute a plain unit. LLM steps leave this to the runner."""
        raise NotImplementedError

    def validate(self, ctx: StepContext, unit: Unit, result: dict) -> dict:
        """Validate an LLM result (raise EvidenceValidationError to allow one repair)."""
        return result

    def assemble(self, ctx: StepContext, done: list[tuple[Unit, dict]]) -> dict[str, dict]:
        """Group validated unit results into one payload per scope."""
        grouped: dict[str, list] = {}
        for unit, result in done:
            grouped.setdefault(unit.scope, []).append({'unit': unit.key, 'result': result})
        return {scope: {'units': items} for scope, items in grouped.items()}

    # --- projection ----------------------------------------------------------------
    def scopes(self, book: dict) -> list[str]:
        if self.scope == 'book':
            return ['book']
        if self.scope == 'chapter':
            return [c['id'] for c in book['chapters']]
        return [c['id'] for c in book['characters'] if c['id'] not in RESERVED_CHARACTERS]

    def capture(self, book: dict, scope: str) -> dict | None:
        return None

    def apply(self, book: dict, payloads: dict[str, dict]) -> list[Conflict]:
        return []

    # --- presentation ----------------------------------------------------------------
    def summarize(self, book: dict, payloads: dict[str, dict]) -> dict:
        """A generic table: {'stats': {...}, 'columns': [...], 'rows': [...]}.

        Every row needs a stable ``id`` (for diffs) and its ``scope``.
        """
        return {'stats': {'scopes': len(payloads)}, 'columns': [], 'rows': []}

    def describe(self) -> dict:
        return {'id': self.id, 'label': self.label, 'summary': self.summary, 'method': self.method,
                'scope': self.scope, 'inputs': list(self.inputs), 'owns': list(self.owns),
                'version': self.version, 'parallel': self.parallel, 'default_gate': self.default_gate,
                'default_model_role': self.default_model_role, 'chapter_scoped': self.chapter_scoped,
                'capturable': self.capturable, 'accumulative': self.accumulative}
