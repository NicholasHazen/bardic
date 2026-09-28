"""Progressive discovery, evidence profiles, and independently resumable direction.

Legacy phase ("Classic") engine behind ``/analyze``, ``/analysis-plan`` and
``/preprocessing``. Scheduled for deletion; see docs/CLASSIC-REMOVAL.md. Live
code must not import this module (tests/test_legacy_isolation.py).
"""
from __future__ import annotations

from copy import deepcopy
import json

import httpx

from . import analysis as a
from .analysis_common import fingerprint, split_chapter as _split_chapter
from .legacy_phase import LegacyProcessingStore as ProcessingStore, coverage
# The request builders moved to the step pipeline; re-exported for this engine and its tests.
from .pipeline.prompts import (direction_cast, direction_specs, discovery_ranges, discovery_specs,  # noqa: F401
                               profile_specs, spread)
from .preprocessing import census, eligible_chapters
from .processing import BudgetReached, RequestBudget, digest, source_hash, token_estimate, price_for
from .staged_analysis import _references
from .store import now
from .resources import ResourceLedger

VERSION = 2
RECIPE_SCHEMA_VERSION = 1
ADAPTER_VERSION = 1
VALIDATOR_VERSION = 2
PHASES = {'scan', 'profiles', 'direct', 'full'}


def checkpoint_for(book, store):
    with store.lock, store.connect() as conn:
        row = conn.execute('SELECT body FROM analysis_checkpoints WHERE book_id=?', (book['id'],)).fetchone()
    checkpoint = json.loads(row[0]) if row else None
    if checkpoint and source_hash(checkpoint['working_book']) == source_hash(book):
        return checkpoint
    return None


def discoveries(book, store, repository):
    """Reuse validated discoveries, including pre-upgrade work, with honest provenance."""
    source = source_hash(book)
    stored_keys = {u.get('unit_key') for u in repository.units(book['id'], 'discovery', source)}
    checkpoint = checkpoint_for(book, store)
    if checkpoint:
        for unit in checkpoint.get('units', {}).values():
            if unit.get('stage') != 'discovery':
                continue
            chapter = next((c for c in book['chapters'] if c['id'] == unit.get('chapter_id')), None)
            if not chapter:
                continue
            start, end = unit.get('start'), unit.get('end')
            if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(chapter['text']):
                continue
            candidate = deepcopy(unit)
            try:
                a._cast_result(candidate['result'], chapter['text'][start:end])
            except (ValueError, KeyError, TypeError):
                continue
            candidate.setdefault('provider', checkpoint.get('provider'))
            candidate.setdefault('model', checkpoint.get('model'))
            unit_key = candidate.get('unit_key') or 'imported:' + digest(candidate)
            candidate['unit_key'] = unit_key
            if unit_key not in stored_keys:
                repository.save_unit(book['id'], unit_key, 'discovery', source, candidate)
                stored_keys.add(unit_key)
    # One accepted observation set per identical source interval; latest wins.
    result = {}
    for unit in repository.units(book['id'], 'discovery', source):
        result[(unit['chapter_id'], unit['start'], unit['end'])] = unit
    return list(result.values())


def request_recipe(spec, provider, model):
    """Effective generation/validation inputs shared by identity and provenance.

    Lineage IDs and repair notes are recorded separately: equivalent source
    evidence can be reused without pretending it was produced by another model.
    """
    return {'schema_version': RECIPE_SCHEMA_VERSION, 'pipeline_version': VERSION,
            'adapter_version': ADAPTER_VERSION, 'validator_version': VALIDATOR_VERSION,
            'provider': provider, 'model': model, 'stage': spec['stage'],
            'system_instruction': a.DIRECTOR_INSTRUCTION, 'prompt': spec['prompt'],
            'response_schema': deepcopy(spec['schema']), 'output_token_limit': spec['output_cap'],
            'temperature': .2 if provider == 'gemini' else None,
            'source_locator': {k: spec[k] for k in ('chapter_id', 'start', 'end', 'character_id') if k in spec}}


def unit_key(spec, provider, model):
    return spec['stage'] + ':' + digest(request_recipe(spec, provider, model))


def recover_cast(book, accepted):
    """Restore candidates accepted before an interrupted chapter publication."""
    original = {c['id']: deepcopy(c) for c in book['characters']}
    for unit in accepted:
        a._merge_cast(book, unit['result']['characters'])
    for character in book['characters']:
        previous = original.get(character['id'])
        if previous:
            for field in ('name', 'description', 'direction'):
                character[field] = previous[field]
            if previous.get('edited'):
                character['aliases'] = previous['aliases']


def cast_signature(book):
    return digest([{k: c[k] for k in ('id', 'name', 'aliases', 'description', 'direction')} for c in book['characters']])


def profile_status(book, store, accepted=None, discovered=None):
    """Discovery coverage and profile currency are separate facts."""
    if accepted is None:
        accepted = discoveries(book, store, ProcessingStore(store))
    if discovered is None:
        discovered = coverage(book, store)
    specs = {s['character_id']: s for s in profile_specs(book, store, accepted)}
    states = []
    for c in book['characters']:
        if c['id'] in {'narrator', 'unassigned'}:
            continue
        spec = specs.get(c['id'])
        current = bool(spec and c.get('profile_input_key') == unit_key(spec, c.get('profile_provider'), c.get('profile_model')))
        state = ('reviewed' if c.get('edited') else 'current' if current else 'stale' if c.get('profile_refined') else 'draft')
        provisional = discovered['profiles_provisional'] or state not in {'current', 'reviewed'}
        states.append({'character_id': c['id'], 'state': state, 'provisional': provisional})
    return {'characters': states, 'profiles_provisional': discovered['profiles_provisional'] or any(s['provisional'] for s in states),
            'profiles_current': sum(s['state'] in {'current', 'reviewed'} for s in states), 'profiles_total': len(states)}


def direction_baseline(book, checkpoint, selected):
    """Replay original scene partition while retaining current reviewed edits and takes."""
    work = deepcopy(book)
    if not checkpoint:
        return work
    selected_ids = {c['id'] for c in selected}
    baseline = checkpoint['working_book']
    for name in ('scenes', 'segments'):
        current = {i['id']: i for i in book[name]}
        # A reviewed scene boundary is authoritative; do not replace that chapter.
        locked = {s['chapter_id'] for s in book['scenes'] if s.get('edited')}
        replacement = []
        for c in book['chapters']:
            items = baseline[name] if c['id'] in selected_ids - locked else book[name]
            for item in items:
                if item['chapter_id'] != c['id']:
                    continue
                latest = current.get(item['id'], {})
                value = deepcopy(latest if latest.get('edited') else item)
                if name == 'segments':
                    value['audio'] = latest.get('audio')
                replacement.append(value)
        work[name] = replacement
    return work


def plan(book, store, provider, model, scan_model, phase='scan', chapter_id=None, resume=True):
    if phase not in PHASES:
        raise ValueError('Choose scan, profiles, direct, or full processing.')
    repository = ProcessingStore(store)
    accepted = discoveries(book, store, repository)
    selected = [c for c in eligible_chapters(book) if chapter_id is None or c['id'] == chapter_id]
    if chapter_id:
        selected = [c for c in book['chapters'] if c['id'] == chapter_id]
    work = direction_baseline(book, checkpoint_for(book, store), selected)
    recover_cast(work, accepted)
    stages = []
    if phase in {'scan', 'full'}:
        stages.extend(discovery_specs(book, selected, accepted if resume else ()))
    if phase in {'profiles', 'full'}:
        stages.extend(profile_specs(work, store, accepted))
    if phase in {'direct', 'full'}:
        stages.extend(direction_specs(work, selected))
    intervals = {(u['chapter_id'], u['start'], u['end']) for u in accepted}
    estimates = []
    for spec in stages:
        chosen = scan_model if spec['stage'] == 'discovery' else model
        key = unit_key(spec, provider, chosen)
        cached = resume and (repository.unit(book['id'], key) is not None or
                            spec['stage'] == 'discovery' and (spec['chapter_id'], spec['start'], spec['end']) in intervals)
        estimates.append({'stage': spec['stage'], 'model': chosen, 'cached': bool(cached),
                          'input_tokens': token_estimate(spec['prompt']) + token_estimate(json.dumps(spec['schema'])),
                          'output_allowance': spec['output_cap']})
    pending = [s for s in estimates if not s['cached']]
    cost = 0.0
    for step in pending:
        price = price_for(provider, step['model'])
        if price.get('input_usd_per_million') is None or price.get('output_usd_per_million') is None:
            cost = None
            break
        cost += (step['input_tokens'] * price['input_usd_per_million'] * 1.25 + step['output_allowance'] * price['output_usd_per_million']) / 1e6
    knowledge = coverage(book, store)
    knowledge.update(profile_status(work, store, accepted, knowledge))
    return {'phase': phase, 'provider': provider, 'scan_model': scan_model, 'model': model,
            'requests': len(pending) if provider != 'local' else 0, 'cached_units': len(estimates) - len(pending),
            'estimated_input_tokens': sum(s['input_tokens'] for s in pending) if provider != 'local' else 0,
            'output_token_allowance': sum(s['output_allowance'] for s in pending) if provider != 'local' else 0,
            'estimated_cost_usd': cost if provider != 'local' else 0, 'steps_by_stage': {stage: sum(s['stage'] == stage for s in pending) for stage in ('discovery', 'profiles', 'directing')},
            'coverage': knowledge, 'future_work_unknown': phase == 'full',
            'note': 'Preview covers currently known work, before retries or evidence repairs. Full processing can discover new profiles and change direction prompts. Token and cost estimates are approximate; the request guard reserves more conservatively. Existing accepted discovery can come from another model; provenance is retained.'}


def run(book, provider, api_key, model, progress, cancelled, *, store, phase, scan_model, chapter_id=None,
        resume=True, prepare=None, limits=None, run_id=None):
    if phase not in PHASES:
        raise ValueError('Choose scan, profiles, direct, or full processing.')
    a._validate_source(book)
    if provider not in a.PROVIDER_LABELS or not api_key:
        raise ValueError('Configure the selected analysis provider first.')
    selected = [c for c in eligible_chapters(book) if chapter_id is None or c['id'] == chapter_id]
    if chapter_id:
        selected = [c for c in book['chapters'] if c['id'] == chapter_id]
    if not selected:
        raise ValueError('No eligible story sections were selected.')
    repository = ProcessingStore(store)
    accepted = discoveries(book, store, repository)
    old = checkpoint_for(book, store)
    from .artifacts import ArtifactRepository, capture_book, output_head, record
    artifacts = ArtifactRepository(store)
    backfilled_prior_books = set()
    with store.lock, store.connect() as conn:
        capture_book(conn, book)
    work = direction_baseline(book, old, selected) if phase in {'direct', 'full'} else deepcopy(book)
    recover_cast(work, accepted)
    baseline = deepcopy(old['working_book'] if old else book)
    rows = {r['id']: deepcopy(r) for r in (old or {}).get('chapters', [])}
    semantic_ids = set(coverage(book, store)['semantic_chapter_ids'])
    for c in book['chapters']:
        rows.setdefault(c['id'], {'id': c['id'], 'discovery_complete': False, 'directing_complete': False, 'status': 'pending'})
        rows[c['id']]['title'] = c['title']
        rows[c['id']]['discovery_complete'] = c['id'] in semantic_ids
    units = {u['unit_key']: u for u in accepted}
    checkpoint = {'provider': provider, 'model': model, 'scan_model': scan_model, 'phase': phase, 'working_book': baseline,
                  'units': units, 'chapters': list(rows.values()), 'references': store.character_references(book['id']),
                  'status': 'running', 'stage': 'preprocessing', 'current_chapter_id': None, 'scope_chapter_id': chapter_id}
    checkpoint_key = fingerprint(book, provider, model)
    budget = RequestBudget(repository, book['id'], run_id, **(limits or {}))
    resources = ResourceLedger(store)
    done, total = 0, 0
    active_row = None
    published = deepcopy(book)
    published_cast = cast_signature(book)
    request = {'gemini': a._request, 'openai': a._openai_request, 'anthropic': a._anthropic_request}[provider]

    def save(message, publish=False):
        nonlocal published_cast
        checkpoint.update(completed_units=done, total_units=total, updated_at=now())
        if publish:
            with resources.operation(book['id'], 'publication', run_id=budget.run_id,
                                     chapter_id=checkpoint.get('current_chapter_id'), measure_cpu=True, kind='assembly'):
                snapshot = deepcopy(work)
                current_cast = cast_signature(snapshot)
                if current_cast != published_cast:
                    for row in rows.values():
                        if row.get('directing_complete'):
                            row.update(directing_complete=False, stage='directing', status='pending')
                    published_cast = current_cast
                discovered = coverage(snapshot, store)
                latest = {(u['chapter_id'], u['start'], u['end']): u for u in units.values() if u['stage'] == 'discovery'}
                freshness = profile_status(snapshot, store, list(latest.values()), discovered)
                character_states = {c['character_id']: c for c in freshness['characters']}
                for c in snapshot['characters']:
                    if c['id'] not in {'narrator', 'unassigned'}:
                        c['profile_provisional'] = character_states[c['id']]['provisional']
                        c['profile_state'] = character_states[c['id']]['state']
                snapshot['analysis'] = {'provider': provider, 'model': model, 'status': 'partial', 'phase': phase,
                                        'profiles_provisional': freshness['profiles_provisional'],
                                        'notes': 'Profiles need whole-book discovery and refinement against current evidence. Source observations and reviewed choices remain authoritative.'}
                snapshot['revision'] = book.get('revision', 1) + 1
                if prepare:
                    prepare(snapshot)
                a._validate_source(snapshot)
                checkpoint['references'] = _references(snapshot, units, provider, model, checkpoint['references'])
                store.commit_analysis(snapshot, checkpoint_key, checkpoint)
                published.clear()
                published.update(snapshot)
        else:
            store.save_analysis_checkpoint(book['id'], checkpoint_key, checkpoint)
        progress(done, total, message)

    def execute(spec, validate):
        a._check_cancel(cancelled)
        chosen = scan_model if spec['stage'] == 'discovery' else model
        key = unit_key(spec, provider, chosen)
        cached = repository.unit(book['id'], key) if resume else None
        if not cached and resume and spec['stage'] == 'discovery':
            cached = next((u for u in accepted if (u['chapter_id'], u['start'], u['end']) ==
                           (spec['chapter_id'], spec['start'], spec['end'])), None)
        if cached:
            with store.lock, store.connect() as conn:
                artifact_id = output_head(conn, book['id'], 'analysis_output', cached['unit_key'])
            try:
                with resources.operation(book['id'], spec['stage'] + '_validation', run_id=budget.run_id,
                                         unit_key=key, chapter_id=spec.get('chapter_id'), measure_cpu=True, kind='validation'):
                    result = validate(deepcopy(cached['result']))
            except (ValueError, KeyError, TypeError) as exc:
                repository.event(book['id'], budget.run_id, spec['stage'], key, 'cache_rejected',
                                 artifact_id=artifact_id, cached_unit_key=cached['unit_key'],
                                 error=str(exc).replace(api_key, '[redacted]')[:800])
                # Derived cache state must not count as accepted coverage after
                # validation fails. Immutable output history remains inspectable.
                with store.lock, store.connect() as conn:
                    conn.execute('DELETE FROM analysis_units WHERE book_id=? AND unit_key=?', (book['id'], cached['unit_key']))
                units.pop(cached['unit_key'], None)
            else:
                units[cached['unit_key']] = cached
                repository.event(book['id'], budget.run_id, spec['stage'], key, 'cache_hit', artifact_id=artifact_id)
                return result
        for prior_book_id in sorted({o['book_id'] for o in spec.get('prior_observations', [])}):
            if prior_book_id not in backfilled_prior_books:
                artifacts.backfill(prior_book_id)
                backfilled_prior_books.add(prior_book_id)
        dependencies = list(spec.get('series_context_artifact_ids', []))
        with store.lock, store.connect() as conn:
            candidates = [('source', spec['chapter_id'])] if spec.get('chapter_id') else []
            candidates += [('analysis_output', k) for k in spec.get('input_unit_keys', [])]
            candidates += [('character_profile', cid) for cid in spec.get('input_character_ids', [])]
            for kind, logical_key in candidates:
                dependency = output_head(conn, book['id'], kind, logical_key)
                if dependency:
                    dependencies.append(dependency)
            for observation in spec.get('prior_observations', []):
                dependency = output_head(conn, observation['book_id'], 'character_observation', observation['id'])
                if not dependency:
                    raise ValueError('An earlier-volume observation could not be retained as an input artifact.')
                dependencies.append(dependency)
        recipe = request_recipe(spec, provider, chosen)
        recipe.update(input_unit_keys=spec.get('input_unit_keys', []), prior_observations=spec.get('prior_observations', []))
        input_id = None
        attempts_before_call = set()
        def last_attempt():
            matches = [attempt for attempt in repository.attempts(book['id'], budget.run_id)
                       if attempt['unit_key'] == key and attempt['id'] not in attempts_before_call
                       and attempt.get('input_artifact_id') == input_id]
            return matches[-1]['id'] if matches else None
        def call(repair):
            nonlocal input_id, attempts_before_call
            attempts_before_call = {attempt['id'] for attempt in repository.attempts(book['id'], budget.run_id)}
            if repair:
                save('Repairing one invalid source-evidence response')
            recipe['repair_instruction'] = repair
            with store.lock, store.connect() as conn:
                input_id = record(conn, book['id'], 'analysis_input', key, recipe, label=spec['stage'] + ' request recipe',
                                  stage=spec['stage'], provider=provider, model=chosen, dependencies=dependencies)
            repository.event(book['id'], budget.run_id, spec['stage'], key, 'started', artifact_id=input_id)
            with budget.context(spec['stage'], key, spec['output_cap'], chapter_id=spec.get('chapter_id')):
                from .processing import request_context
                request_context()['input_artifact_id'] = input_id
                return request(client, chosen, api_key, spec['prompt'] + ('\n\n' + repair if repair else ''), spec['schema'], cancelled)
        def tracked_validate(result):
            received = deepcopy(result)
            try:
                with resources.operation(book['id'], spec['stage'] + '_validation', run_id=budget.run_id,
                                         unit_key=key, chapter_id=spec.get('chapter_id'), measure_cpu=True, kind='validation'):
                    return validate(result)
            except Exception as exc:
                error = str(exc).replace(api_key, '[redacted]')[:800]
                attempt_id = last_attempt()
                with store.lock, store.connect() as conn:
                    rejection_id = record(conn, book['id'], 'analysis_rejection', key,
                                          {'result': received, 'validation_error': error, 'unit_key': key, 'attempt_id': attempt_id},
                                          label=spec['stage'] + ' rejected result', stage=spec['stage'], provider=provider,
                                          model=chosen, dependencies=[input_id] if input_id else [])
                repository.event(book['id'], budget.run_id, spec['stage'], key, 'validation_rejected',
                                 artifact_id=rejection_id, attempt_id=attempt_id, error=error)
                raise
        try:
            result = a._repairable_request(call, tracked_validate, cancelled)
        except Exception as exc:
            repository.event(book['id'], budget.run_id, spec['stage'], key, 'budget_limited' if isinstance(exc, BudgetReached) else 'failed',
                             attempt_id=None if isinstance(exc, BudgetReached) else last_attempt(),
                             error=str(exc).replace(api_key, '[redacted]')[:800])
            raise
        unit = {k: spec[k] for k in ('stage', 'chapter_id', 'start', 'end', 'character_id', 'priority') if k in spec}
        unit.update(unit_key=key, provider=provider, model=chosen, result=deepcopy(result), created_at=now())
        unit.update(input_recipe=recipe, dependency_artifact_ids=dependencies, producing_attempt_id=last_attempt())
        # Durable before callbacks, publication, or another request can fail.
        artifact_id = repository.save_unit(book['id'], key, spec['stage'], source_hash(book), unit)
        repository.event(book['id'], budget.run_id, spec['stage'], key, 'accepted', artifact_id=artifact_id, attempt_id=last_attempt())
        units[key] = unit
        return result

    try:
        census(book, store)
        save('Free whole-book preprocessing complete')
        with httpx.Client(timeout=httpx.Timeout(180, connect=15)) as client:
            if phase in {'scan', 'full'}:
                specs = discovery_specs(work, selected, accepted if resume else ())
                total += len(specs)
                for c in selected:
                    active_row = rows[c['id']]
                    checkpoint.update(stage='discovery', current_chapter_id=c['id'])
                    chapter_specs = [s for s in specs if s['chapter_id'] == c['id']]
                    active_row.update(stage='discovery', status='running', error=None, total_units=len(chapter_specs), completed_units=0)
                    for spec in chapter_specs:
                        save(c['title'] + ' · discovering characters')
                        def validate(result):
                            a._cast_result(result, spec['source'])
                            return result
                        result = execute(spec, validate)
                        # Discovery can enrich evidence without overwriting an existing refined/reviewed profile.
                        profiles = {c['id']: deepcopy(c) for c in work['characters']}
                        a._merge_cast(work, result['characters'])
                        for character in work['characters']:
                            previous = profiles.get(character['id'])
                            if previous and (previous.get('profile_refined') or previous.get('edited')):
                                for field in ('name', 'description', 'direction'):
                                    character[field] = previous[field]
                                if previous.get('edited'):
                                    character['aliases'] = previous['aliases']
                        done += 1
                        active_row['completed_units'] += 1
                        save(c['title'] + ' · character evidence saved')
                    active_row.update(discovery_complete=True, stage='discovery', status='completed')
                    save(c['title'] + ' · discovery saved', publish=True)
                accepted = discoveries(work, store, repository)
            active_row = None
            if phase in {'profiles', 'full'}:
                checkpoint.update(stage='profiles', current_chapter_id=None)
                specs = profile_specs(work, store, accepted)
                total += len(specs)
                for spec in specs:
                    save(spec['name'] + ' · ' + spec['priority'] + ' profile refinement')
                    def validate(result):
                        cast = a._profile_result(result, spec['evidence'])
                        if len(cast) != 1 or cast[0]['name'] != spec['name'] or set(cast[0]['aliases']) != set(spec['aliases']):
                            raise ValueError('Profile refinement must preserve the selected character identity and aliases.')
                        return result
                    result = execute(spec, validate)
                    a._merge_cast(work, result['characters'])
                    character = next(c for c in work['characters'] if c['id'] == spec['character_id'])
                    character.update(profile_refined=True, profile_model=model, profile_provider=provider, profile_priority=spec['priority'],
                                     profile_input_key=unit_key(spec, provider, model))
                    done += 1
                    save(spec['name'] + ' · profile saved', publish=True)
            if phase in {'direct', 'full'}:
                checkpoint.update(stage='directing')
                specs = direction_specs(work, selected)
                total += len(specs)
                for c in selected:
                    active_row = rows[c['id']]
                    chapter_specs = [s for s in specs if s['chapter_id'] == c['id']]
                    active_row.update(stage='directing', status='running', error=None, total_units=len(chapter_specs), completed_units=0)
                    checkpoint['current_chapter_id'] = c['id']
                    boundaries = {}
                    for spec in chapter_specs:
                        scene, batch = spec['scene'], spec['batch']
                        save(c['title'] + ' · directing scene passages')
                        def validate(result):
                            a._apply_annotations(work, deepcopy(scene), deepcopy(batch), result, {})
                            return result
                        result = execute(spec, validate)
                        a._apply_annotations(work, scene, batch, result, boundaries)
                        for segment in batch:
                            if not segment.get('edited'):
                                segment.update(analysis_provider=provider, analysis_model=model)
                        done += 1
                        active_row['completed_units'] += 1
                        save(c['title'] + ' · direction saved')
                    _split_chapter(work, c['id'], boundaries)
                    a._scene_members(work)
                    active_row.update(directing_complete=True, status='completed', stage='complete')
                    save(c['title'] + ' · chapter direction published', publish=True)
        checkpoint.update(status='completed', stage='complete', current_chapter_id=None, error=None)
        save('Selected stage complete; saved work is ready for review', publish=True)
        return published
    except Exception as exc:
        stopped = isinstance(exc, InterruptedError) or cancelled()
        safe_error = str(exc).replace(api_key, '[redacted]')[:900]
        checkpoint.update(status='budget_limited' if isinstance(exc, BudgetReached) else 'interrupted' if stopped else 'failed',
                          error=safe_error, updated_at=now())
        if active_row:
            active_row.update(status=checkpoint['status'], error=safe_error)
        store.save_analysis_checkpoint(book['id'], checkpoint_key, checkpoint)
        if isinstance(exc, BudgetReached):
            raise
        if stopped:
            raise a.AnalysisCancelled('Stopped. Accepted analysis steps are saved for resume.') from exc
        raise ValueError(safe_error + ' Accepted analysis steps are saved for resume.') from exc
