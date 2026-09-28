"""Keep the reader's book projection and accepted step versions in agreement.

The book JSON remains the projection every reader/editor uses. Pipeline runs
never write it. Only :func:`accept` (user or auto policy) changes it, by
applying a step's accepted versions through the step's own projector, inside
one short transaction guarded by the book revision.

Anything that changes the projection outside the pipeline (older analysis
controls, series runs, structure repair) is recorded by :func:`sync` as an
``external`` version before the next pipeline decision, so it can be restored
later. The first sync of a book records ``baseline`` versions of its existing
state. Both are marked as legacy provenance: their producer is not known.
"""
from __future__ import annotations

from copy import deepcopy

from ..processing import digest
from .repository import PipelineRepository
from ..errors import Conflict, Invalid, NotFound


class RevisionConflict(ValueError):
    """The book changed after the impact the owner reviewed."""


def _accepted(repository, conn, book_id, step_id):
    return repository.accepted(conn, book_id, step_id)


def _captures(registry, book):
    return {step.id: {scope: step.capture(book, scope) for scope in step.scopes(book)}
            for step in registry if step.capturable}


def fingerprint(registry, book, captures=None):
    """Content identity of everything the pipeline can capture from a projection.

    Revision numbers are not a reliable change signal (some writers do not bump
    them), so the sync shortcut compares captured content instead.
    """
    return digest([[[s.id, s.version] for s in registry], captures if captures is not None else _captures(registry, book)])


def sync(repository: PipelineRepository, registry, conn, book, *, force=False):
    """Record capturable projection state that no accepted version explains."""
    book_id = book['id']
    state = repository.state(conn, book_id)
    captures = _captures(registry, book)
    current_fingerprint = fingerprint(registry, book, captures)
    if not force and state.get('fingerprint') == current_fingerprint:
        return []
    recorded = []
    for step in registry:
        if not step.capturable:
            continue
        accepted, heads = _accepted(repository, conn, book_id, step.id)
        expected = deepcopy(book)
        step.apply(expected, accepted)
        versions = {}
        for scope in step.scopes(book):
            current = captures[step.id][scope]
            if current is None:
                continue
            if scope in heads and step.capture(expected, scope) == current:
                continue
            origin = 'external' if scope in heads or state else 'baseline'
            versions[scope] = (origin, repository.record_version(conn, book_id, step, scope, current, origin=origin))
        for origin in ('baseline', 'external'):
            chosen = {scope: identifier for scope, (kind, identifier) in versions.items() if kind == origin}
            if not chosen:
                continue
            step_run = repository.create_step_run(book_id, step, origin=origin, status='completed', conn=conn,
                                                  scopes=chosen)
            repository.decide(conn, book_id, step.id, 'accept', chosen, mode=origin, step_run_id=step_run['id'],
                              note='Recorded the existing projection; its producer is unknown.')
            recorded.append({'step_id': step.id, 'origin': origin, 'scopes': sorted(chosen), 'step_run_id': step_run['id']})
    repository.set_state(conn, book_id, fingerprint=current_fingerprint)
    return recorded


def stale_scopes(repository, registry, conn, book_id):
    """{step_id: [scope, ...]} whose accepted version read superseded inputs."""
    heads = {step.id: repository.heads(conn, book_id, step.id) for step in registry}
    result = {}
    for step in registry:
        if not step.inputs:
            continue
        payloads = repository.payloads(conn, heads[step.id].values())
        stale = []
        for scope, identifier in heads[step.id].items():
            payload = payloads.get(identifier, {})
            if payload.get('origin') != 'run':
                continue
            for input_step, scopes in payload.get('inputs', {}).items():
                current = heads.get(input_step, {})
                if any(current.get(input_scope) != used for input_scope, used in scopes.items()) or \
                        set(current) - set(scopes):
                    stale.append(scope)
                    break
        if stale:
            result[step.id] = sorted(stale)
    return result


def preview(repository, registry, conn, book, step, versions, valid_audio=None, prepare=None):
    """What accepting ``versions`` ({scope: artifact_id}) would change.

    ``prepare(work, book)`` applies the same side effects accept will (e.g. a
    device voice for a newly added character) before takes are compared.
    """
    accepted, heads = _accepted(repository, conn, book['id'], step.id)
    payloads = repository.payloads(conn, versions.values())
    for scope, identifier in versions.items():
        payload = payloads.get(identifier)
        if not payload or payload.get('step_id') != step.id or payload.get('scope') != scope:
            raise Invalid('version_incompatible', 'A selected version does not belong to this step and scope.')
        accepted[scope] = payload['result']
    if step.accumulative:
        # Additive steps (discovery) only apply what changes; re-merging every
        # accepted scope would resurrect names the owner has since edited away.
        accepted = {scope: accepted[scope] for scope in versions if heads.get(scope) != versions[scope]}
    work = deepcopy(book)
    conflicts = step.apply(work, accepted)
    if prepare:
        prepare(work, book)
    changed = sorted(scope for scope, identifier in versions.items() if heads.get(scope) != identifier)
    # Only takes this acceptance invalidates. A take already hidden by a voice
    # switch stays stored so switching the voice back restores it.
    invalidated = []
    if valid_audio:
        for before, after in zip(book['segments'], work['segments']):
            if before.get('audio') and valid_audio(book, before) and not valid_audio(work, after):
                invalidated.append(after['id'])
    downstream = [s for s in registry.downstream(step.id) if repository.heads(conn, book['id'], s)] if changed else []
    return {'step_id': step.id, 'changed_scopes': changed, 'unchanged_scopes': sorted(set(versions) - set(changed)),
            'conflicts': [c.as_dict() for c in conflicts], 'audio_takes_invalidated': len(invalidated),
            'downstream_steps_affected': downstream, 'book': work, 'invalidated_segment_ids': invalidated}


def accept(store, repository, registry, book_id, step, versions, *, mode='user', step_run_id=None,
           valid_audio=None, prepare=None, expected_revision=None):
    """Select versions and republish the projection in one transaction."""
    with store.lock, store.connect() as conn:
        book = store._hydrate(_book_row(conn, book_id), conn)
        if expected_revision is not None and book.get('revision', 0) != expected_revision:
            raise RevisionConflict('The book changed since this preview (its revision differs from `expected_revision`).')
        sync(repository, registry, conn, book)
        impact = preview(repository, registry, conn, book, step, versions, valid_audio, prepare)
        work = impact.pop('book')
        invalidated = set(impact.pop('invalidated_segment_ids'))
        for segment in work['segments']:
            if segment['id'] in invalidated:
                segment['audio'] = None
        decision = repository.decide(conn, book_id, step.id, 'accept', versions, mode=mode, step_run_id=step_run_id)
        work['revision'] = book.get('revision', 0) + 1
        store._save_book(conn, work)
        # Record any newly capturable scopes (e.g. draft profiles of new characters).
        sync(repository, registry, conn, work)
    return {**impact, 'decision': decision, 'revision': work['revision']}


def reject(store, repository, book_id, step, versions, *, step_run_id=None):
    """Decline a step version. Only the decision log changes: heads, the book and retained results do not.

    Acceptance is a decision, not content equality. A version whose results merely equal the
    accepted content (``same_as_accepted``, content-addressed to the same artifact) can be
    declined; the identical accepted content stays accepted through the version that was
    accepted. Refused only for scopes this very version made current.
    """
    with store.lock, store.connect() as conn:
        heads = repository.heads(conn, book_id, step.id)
        current = [scope for scope, identifier in versions.items() if heads.get(scope) == identifier]
        if current and repository.was_accepted(conn, book_id, step.id, step_run_id):
            raise Conflict('version_accepted', 'This version is the accepted one for a selected scope. '
                                               'Accept another version to replace it.')
        return repository.decide(conn, book_id, step.id, 'reject', versions, mode='user', step_run_id=step_run_id)


def _book_row(conn, book_id):
    import json
    row = conn.execute('SELECT body FROM books WHERE id=?', (book_id,)).fetchone()
    if not row:
        raise NotFound('book_not_found', 'Book not found')
    return json.loads(row[0])
