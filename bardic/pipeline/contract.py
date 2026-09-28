"""The contract every analysis pipeline step implements.

A step is a small, declarative object. The runner, repository and API never
special-case a step ID; adding, removing or reordering steps only changes the
registry list in ``bardic.pipeline.steps``.

Lifecycle of one step run::

    units(ctx)            plan the work (LLM requests, service calls or local computations)
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
# plain: computed locally. llm: prompt + schema to a chosen model. service: a self-hosted analysis service.
METHODS = ('plain', 'llm', 'service')
# Prompt-and-schema providers (each takes a model ID). The self-hosted LLM needs a server URL, not a key.
LLM_PROVIDERS = ('gemini', 'openai', 'anthropic', 'local_llm')
# Chapter services (no model choice): see bardic.local_services.
SERVICE_PROVIDERS = ('booknlp', 'novel_analyzer')
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


@dataclass(frozen=True)
class ServiceRequest:
    """One call to a self-hosted chapter service. ``body`` is the exact JSON sent; it forms the cache identity."""
    body: dict


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
    # A service call instead of a model request (steps whose provider is a SERVICE_PROVIDER).
    service: ServiceRequest | None = None
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
    # Providers the owner may choose. Empty means the method's default: ('local',)
    # for plain steps, LLM_PROVIDERS for llm steps. A step may add service providers;
    # its units() then reads ctx.provider to plan service or plain units instead.
    providers: tuple[str, ...] = ()
    # Providers this step uses without contacting them (it reads their accepted results instead),
    # so running it needs no key or URL for them.
    offline_providers: tuple[str, ...] = ()
    # Upstream step IDs whose ACCEPTED payloads this step reads.
    inputs: tuple[str, ...] = ()
    # Inputs that must have an accepted result before this step can run (None: all inputs).
    # Others are only recorded, e.g. for staleness. A requirement also requested in the same run counts.
    requires: tuple[str, ...] | None = None
    # Projection fields this step overwrites, as 'collection.field'.
    owns: tuple[str, ...] = ()
    # Bump when prompts, schemas or logic change: new cache keys, old versions stay readable.
    version: int = 1
    # Set (to the old version) when only assembly or projection changed: validated unit results
    # stay reusable under this key, so unchanged model requests are not paid for again.
    request_version: int | None = None
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
        """Compute a unit that has neither a model request nor a service call."""
        raise NotImplementedError

    def validate(self, ctx: StepContext, unit: Unit, result: dict) -> dict:
        """Validate an LLM or service result. For an LLM, EvidenceValidationError allows one
        repair; a service result is rejected outright on any ValueError."""
        return result

    def allowed_providers(self) -> tuple[str, ...]:
        if self.providers:
            return self.providers
        return ('local',) if self.method == 'plain' else LLM_PROVIDERS

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

    @property
    def required_inputs(self) -> tuple[str, ...]:
        return self.inputs if self.requires is None else self.requires

    def describe(self) -> dict:
        return {'id': self.id, 'label': self.label, 'summary': self.summary, 'method': self.method,
                'scope': self.scope, 'inputs': list(self.inputs), 'requires': list(self.required_inputs), 'owns': list(self.owns),
                'version': self.version, 'parallel': self.parallel, 'default_gate': self.default_gate,
                'providers': list(self.allowed_providers()), 'offline_providers': list(self.offline_providers),
                'default_model_role': self.default_model_role, 'chapter_scoped': self.chapter_scoped,
                'capturable': self.capturable, 'accumulative': self.accumulative}
