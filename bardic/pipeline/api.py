"""HTTP routes for the analysis pipeline view.

Global:  GET  /api/analysis-pipeline                      steps, saved per-step settings
         PUT  /api/analysis-pipeline/steps/{step}/settings provider/model/gate for a step
Book:    GET  /api/books/{id}/analysis-pipeline           per-step state, active run
         POST .../analysis-pipeline/plan                  estimate + plan fingerprint (read-only)
         POST .../analysis-pipeline/runs                  queue one or several steps
         GET  .../steps/{step}/versions                   version history
         GET  .../steps/{step}/versions/{id}              result table, diffed against another version
         POST .../steps/{step}/versions/{id}/preview      what accepting would change
         POST .../steps/{step}/versions/{id}/accept       accept (also used to roll back)
         POST .../steps/{step}/versions/{id}/reject

``{id}`` may be ``accepted`` to address the currently accepted versions.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..analysis import PIPELINE_LLM_LABELS, PROVIDER_LABELS
from ..local_services import SERVICES
from ..model_catalog import local_llm_catalog
from ..preprocessing import eligible_chapters
from . import projection
from .contract import LLM_PROVIDERS, SERVICE_PROVIDERS
from .registry import Registry
from .repository import ACTIVE, PipelineRepository
from .runner import RunExecutor, plan

LABELS = {**PIPELINE_LLM_LABELS, **{p: SERVICES[p]['label'] for p in SERVICE_PROVIDERS}}

SETTINGS_ID = 'analysis_pipeline'
MODEL_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}')
ROW_LIMIT = 1000


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class StepConfig(Strict):
    provider: str = Field(min_length=1, max_length=40)
    model: str | None = Field(default=None, max_length=200)


class StepSettings(StepConfig):
    gate: Literal['auto', 'review'] | None = None


class Limits(Strict):
    """Optional caps for API callers. The Analysis tab sends none: the confirmed plan
    is the authorization, and each unit's retries and evidence repairs are bounded."""
    max_requests: int | None = Field(default=None, ge=1, le=1000)
    max_input_tokens: int | None = Field(default=None, ge=1000, le=10000000)
    max_output_tokens: int | None = Field(default=None, ge=1000, le=2000000)
    budget_usd: float | None = Field(default=None, gt=0, le=1000, allow_inf_nan=False)


class PlanRequest(Strict):
    steps: list[str] = Field(min_length=1, max_length=40)
    chapter_ids: list[str] | None = Field(default=None, max_length=2000)
    configs: dict[str, StepConfig] | None = None
    # Request new samples even when an identical validated unit is cached.
    fresh: bool = False


class RunRequest(PlanRequest):
    mode: Literal['serial', 'parallel'] = 'serial'
    gates: dict[str, Literal['auto', 'review']] | None = None
    concurrency: int = Field(default=2, ge=1, le=4)
    limits: Limits = Field(default_factory=Limits)
    expected_fingerprint: str | None = Field(default=None, max_length=64)


class DecisionRequest(Strict):
    scopes: list[str] | None = Field(default=None, max_length=5000)
    expected_revision: int | None = None


def _saved(runtime):
    with runtime.store.lock, runtime.store.connect() as conn:
        row = conn.execute('SELECT body FROM settings WHERE id=?', (SETTINGS_ID,)).fetchone()
    return json.loads(row[0]) if row else {'steps': {}}


def _save(runtime, value):
    with runtime.store.lock, runtime.store.connect() as conn:
        conn.execute('INSERT OR REPLACE INTO settings(id,body) VALUES (?,?)', (SETTINGS_ID, json.dumps(value)))


def _default_config(runtime, step):
    if step.method == 'plain':
        return {'provider': 'local', 'model': None}
    if step.method == 'service':
        return {'provider': step.allowed_providers()[0], 'model': None}
    preferred = runtime.preferences.get('analysis_provider')
    provider = preferred if preferred in PROVIDER_LABELS else next(
        (p for p in PROVIDER_LABELS if runtime.api_keys.get(p)), next(iter(PROVIDER_LABELS)))
    models = runtime.preferences['preprocess_models_by_provider' if step.default_model_role == 'scan' else 'analysis_models_by_provider']
    return {'provider': provider, 'model': models.get(provider)}


def _validate_config(step, config):
    provider, model = config.get('provider'), config.get('model')
    if step.method == 'plain':
        if provider != 'local' or model:
            raise HTTPException(400, f'{step.label} runs locally without a model.')
        return {'provider': 'local', 'model': None}
    allowed = step.allowed_providers()
    if provider not in allowed:
        raise HTTPException(400, f'Choose {", ".join(LABELS[p] for p in allowed)} for {step.label}.')
    if provider in SERVICE_PROVIDERS:
        if model:
            raise HTTPException(400, f'{LABELS[provider]} runs on your server without a model choice.')
        return {'provider': provider, 'model': None}
    if not isinstance(model, str) or not MODEL_ID.fullmatch(model):
        raise HTTPException(400, f'Choose a valid model ID for {step.label}.')
    return {'provider': provider, 'model': model}


def provider_views(runtime):
    """Every pipeline provider; each step lists which of these it accepts."""
    credentials = runtime.analysis_credentials()
    views = []
    for provider in (*LLM_PROVIDERS, *SERVICE_PROVIDERS):
        self_hosted = provider not in PROVIDER_LABELS
        view = {'id': provider, 'label': LABELS[provider], 'kind': 'service' if provider in SERVICE_PROVIDERS else 'model',
                'self_hosted': self_hosted, 'needs': 'url' if self_hosted else 'api_key',
                # A key or URL is set; it does not prove the server answers. has_api_key is the older name.
                'configured': bool(credentials.get(provider)), 'has_api_key': bool(credentials.get(provider))}
        if provider == 'local_llm':
            view['models'] = local_llm_catalog(credentials.get(provider))['models']
        views.append(view)
    return views


def step_settings(runtime, registry):
    saved = _saved(runtime).get('steps', {})
    result = {}
    for step in registry:
        value = saved.get(step.id) or {}
        config = _default_config(runtime, step)
        if value.get('provider'):
            try:
                config = _validate_config(step, value)
            except HTTPException:
                pass
        gate = value.get('gate') if value.get('gate') in {'auto', 'review'} else step.default_gate
        result[step.id] = {**config, 'gate': gate, 'saved': bool(value)}
    return result


def _versions_view(repository, registry, conn, book_id, step, step_runs):
    heads = repository.heads(conn, book_id, step.id)
    rejected, once_accepted, accepted_artifacts = {}, set(), set()
    for decision in repository.decisions(book_id, step.id, limit=5000):
        if decision['action'] == 'reject':
            rejected.setdefault(decision.get('step_run_id'), decision['created_at'])
        else:
            once_accepted.add(decision.get('step_run_id'))
            accepted_artifacts.update(decision.get('versions', {}).values())
    items = []
    for run in step_runs:
        scopes = run.get('scopes', {})
        accepted = [s for s, i in scopes.items() if heads.get(s) == i]
        # "Accepted" is a decision, not a coincidence: content-addressed versions
        # make an identical rerun share the accepted artifact without anyone accepting it.
        decided = run['id'] in once_accepted
        if run['status'] in ACTIVE:
            state = 'running'
        elif not scopes:
            state = 'empty'
        elif decided and len(accepted) == len(scopes):
            state = 'accepted'
        elif decided and accepted:
            state = 'partly_accepted'
        elif run['id'] in rejected:
            state = 'rejected'
        elif len(accepted) == len(scopes):
            state = 'same_as_accepted'
        elif decided or set(scopes.values()) <= accepted_artifacts:
            state = 'superseded'
        else:
            state = 'candidate'
        items.append({**{k: run.get(k) for k in ('id', 'run_id', 'step_id', 'step_version', 'origin', 'provider', 'model', 'status',
                                                   'units', 'error', 'created_at', 'completed_at', 'chapter_ids',
                                                   'unchanged_scopes', 'incomplete_scopes')},
                      'scope_count': len(scopes), 'accepted_scopes': len(accepted), 'state': state})
    return items


def step_states(store, repository, registry, book):
    """Per-step accepted, stale and candidate state of one book, in pipeline order.

    Captures outside changes first, so the counts describe the current book.
    Shared by the Analyze overview and the Details explorer's stage cards.
    """
    book_id = book['id']
    with store.lock:
        # Commit outside-change capture before other connections read the history.
        with store.connect() as conn:
            projection.sync(repository, registry, conn, book)
        eligible = {c['id'] for c in eligible_chapters(book)}
        with store.connect() as conn:
            stale = projection.stale_scopes(repository, registry, conn, book_id)
            steps = []
            for step in registry:
                heads = repository.heads(conn, book_id, step.id)
                payloads = repository.payloads(conn, heads.values())
                origins = {}
                for identifier in heads.values():
                    origin = payloads.get(identifier, {}).get('origin', 'run')
                    origins[origin] = origins.get(origin, 0) + 1
                versions = _versions_view(repository, registry, conn, book_id, step, repository.step_runs(book_id, step.id, 20))
                scopes = [s for s in step.scopes(book) if not step.chapter_scoped or s in eligible]
                steps.append({'id': step.id,
                              'accepted_scopes': sum(s in heads for s in scopes), 'has_accepted': bool(heads),
                              'total_scopes': len(scopes), 'accepted_origins': origins,
                              'stale_scopes': stale.get(step.id, []),
                              'pending_versions': sum(v['state'] == 'candidate' for v in versions),
                              'latest': versions[0] if versions else None})
    return steps


def unmet_message(registry, step_id, inputs):
    labels = ' and '.join(registry.get(i).label for i in inputs)
    return (f'{registry.get(step_id).label} needs accepted results from {labels}. '
            f"Run and accept {labels} first, or include {'them' if len(inputs) > 1 else 'it'} in this run.")


def audio_checker(runtime):
    """``valid_audio(book, segment)`` that resolves each book's voice cast only once.

    Casts resolve through the voice library (a database read), so resolving it
    per passage would make previews scale with passages × library size.
    """
    casts = {}

    def check(book, segment):
        entry = casts.get(id(book))
        if entry is None or entry[0] is not book:
            entry = casts[id(book)] = (book, runtime.resolved_cast(book))
        return runtime.valid_audio(book, segment, entry[1])
    return check


def new_character_voices(runtime):
    """Give only characters this acceptance adds a default device voice.

    Existing characters keep their choices, including an explicit Default
    (no device voice), which the owner may have set in Cast.
    """
    def prepare(work, before):
        existing = {c['id'] for c in before['characters']}
        added = [c for c in work['characters'] if c['id'] not in existing]
        if added:
            runtime.assign_local_voices({'characters': added})
    return prepare


def resolve_configs(runtime, registry, steps, overrides=None):
    """Each step's provider/model: a validated override, else the saved step settings."""
    settings = step_settings(runtime, registry)
    configs = {}
    for step in steps:
        override = (overrides or {}).get(step.id)
        configs[step.id] = _validate_config(step, override.model_dump()) if override else \
            {'provider': settings[step.id]['provider'], 'model': settings[step.id]['model']}
    return configs


def credentials_needed(runtime, registry, configs):
    """(providers a run must call, every configured credential, the providers still missing one)."""
    needed = {c['provider'] for step_id, c in configs.items()
              if c['provider'] != 'local' and c['provider'] not in registry.get(step_id).offline_providers}
    credentials = runtime.analysis_credentials()
    return needed, credentials, [p for p in sorted(needed) if not credentials.get(p)]


def setup_message(missing):
    return 'Add in Settings first: ' + ', '.join(
        f"the {LABELS[p]} server URL" if p not in PROVIDER_LABELS else f"{'an' if LABELS[p][0] in 'AEIOU' else 'a'} {LABELS[p]} API key"
        for p in missing)


def accept_versions(runtime, registry, book_id, step, versions, step_run_id, *, mode, expected_revision=None):
    repository = PipelineRepository(runtime.store)
    run = repository.step_run(book_id, step_run_id) if step_run_id else None
    voices = new_character_voices(runtime)

    def prepare(work, before):
        voices(work, before)
        if step.method == 'llm' and run and run.get('origin') == 'run':
            # The reader and Cast summarize who produced the current analysis.
            work['analysis'] = {'provider': run['provider'], 'model': run['model'], 'status': 'partial',
                                'phase': step.id, 'notes': f'{step.label} accepted in the Analysis tab.'}
    return projection.accept(runtime.store, repository, registry, book_id, step, versions,
                             mode=mode, step_run_id=step_run_id, valid_audio=audio_checker(runtime),
                             prepare=prepare, expected_revision=expected_revision)


def execute_run(runtime, registry, job, run, secrets, *, limits, concurrency, fresh, cancelled=None):
    """Run a queued pipeline run inside its job, then settle the run record.

    ``limits`` must be a full ``Limits`` dump: ``None`` values mean uncapped,
    while a missing key would fall back to ``RequestBudget``'s default caps.
    """
    repository = PipelineRepository(runtime.store)
    book_id = run['book_id']
    stop = cancelled or (lambda: runtime.cancelled(job['id']))

    def accept(step, versions, step_run_id):
        accept_versions(runtime, registry, book_id, step, versions, step_run_id, mode='auto')

    def work():
        def progress(done, total, message):
            runtime.check_cancel(job['id'])
            runtime.store.update_job(job['id'], progress=done, total=total, message=message)
        RunExecutor(runtime.store, registry, run, secrets=secrets, cancelled=stop,
                    progress=progress, accept=accept, limits=limits,
                    concurrency=concurrency, fresh=fresh).execute()

    try:
        runtime.run(job, work, tuple(secrets.values()))
    finally:
        # A job cancelled while queued never reaches work(); settle its run too.
        settled = repository.run(run['id'])
        if settled['status'] in ACTIVE:
            final = runtime.store.job(job['id'])['status']
            repository.update_run(run['id'], status=final if final not in ACTIVE else 'interrupted',
                                  error=None if final == 'completed' else 'The job ended before this run finished.')


def build_router(registry: Registry):
    router = APIRouter()

    def runtime_of(request):
        return request.app.state.runtime

    def step_of(step_id):
        try:
            return registry.get(step_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'")) from exc

    @router.get('/api/analysis-pipeline')
    def definitions(request: Request):
        runtime = runtime_of(request)
        settings = step_settings(runtime, registry)
        return {'schema_version': 1, 'providers': provider_views(runtime),
                'steps': [{**step.describe(), 'settings': settings[step.id]} for step in registry]}

    @router.put('/api/analysis-pipeline/steps/{step_id}/settings')
    def save_settings(step_id: str, body: StepSettings, request: Request):
        runtime = runtime_of(request)
        step = step_of(step_id)
        config = _validate_config(step, body.model_dump())
        with runtime.store.lock:
            value = _saved(runtime)
            value.setdefault('steps', {})[step.id] = {**config, 'gate': body.gate or step.default_gate}
            _save(runtime, value)
        return step_settings(runtime, registry)[step.id]

    @router.get('/api/books/{book_id}/analysis-pipeline')
    def overview(book_id: str, request: Request):
        runtime = runtime_of(request)
        store = runtime.store
        repository = PipelineRepository(store)
        settings = step_settings(runtime, registry)
        with store.lock:
            book = store.book(book_id)
            steps = [{'id': item['id'], 'settings': settings[item['id']], **{k: v for k, v in item.items() if k != 'id'}}
                     for item in step_states(store, repository, registry, book)]
        runs = repository.runs(book_id, 5)
        active = next((r for r in runs if r['status'] in ACTIVE), None)
        return {'book_id': book_id, 'revision': book.get('revision', 0), 'steps': steps, 'active_run': active,
                'recent_runs': runs, 'chapters': [{'id': c['id'], 'title': c['title'], 'kind': c.get('kind', 'section')}
                                                  for c in book['chapters']]}

    def configs_for(runtime, body, steps):
        return resolve_configs(runtime, registry, steps, body.configs)

    def chapters_for(book, body):
        if body.chapter_ids is None:
            return None
        known = {c['id'] for c in book['chapters']}
        if not body.chapter_ids or any(c not in known for c in body.chapter_ids):
            raise HTTPException(400, 'Choose chapters in this book.')
        return sorted(set(body.chapter_ids))

    @router.post('/api/books/{book_id}/analysis-pipeline/plan')
    def plan_run(book_id: str, body: PlanRequest, request: Request):
        runtime = runtime_of(request)
        steps = [step_of(s) for s in body.steps]
        configs = configs_for(runtime, body, steps)
        # Read and sync under one lock: a stale snapshot would be recorded as an outside change.
        with runtime.store.lock:
            book = runtime.store.book(book_id)
            with runtime.store.connect() as conn:
                projection.sync(PipelineRepository(runtime.store), registry, conn, book)
        return plan(runtime.store, registry, book_id, [s.id for s in steps], configs, chapter_ids=chapters_for(book, body),
                    fresh=body.fresh)

    @router.post('/api/books/{book_id}/analysis-pipeline/runs')
    def start_run(book_id: str, body: RunRequest, request: Request):
        runtime = runtime_of(request)
        steps = [step_of(s) for s in body.steps]
        with runtime.store.lock:
            runtime.require_idle(book_id)
            book = runtime.store.book(book_id)
            chapter_ids = chapters_for(book, body)
            configs = configs_for(runtime, body, steps)
            providers_needed, credentials, missing = credentials_needed(runtime, registry, configs)
            if missing:
                raise HTTPException(400, setup_message(missing))
            repository = PipelineRepository(runtime.store)
            with runtime.store.connect() as conn:
                projection.sync(repository, registry, conn, book)
                unmet = registry.missing_inputs(lambda step_id: repository.heads(conn, book_id, step_id), [s.id for s in steps])
            if unmet:
                raise HTTPException(400, ' '.join(unmet_message(registry, step_id, inputs) for step_id, inputs in unmet.items()))
            if not body.expected_fingerprint and all(v is None for v in body.limits.model_dump().values()):
                raise HTTPException(400, 'Preview the run and confirm it (send expected_fingerprint), or set limits.')
            if body.expected_fingerprint:
                current = plan(runtime.store, registry, book_id, [s.id for s in steps], configs, chapter_ids=chapter_ids,
                               fresh=body.fresh)
                if current['fingerprint'] != body.expected_fingerprint:
                    raise HTTPException(409, 'The plan changed since the preview. Review the new estimate before running.')
            settings = step_settings(runtime, registry)
            gates = {s.id: (body.gates or {}).get(s.id) or settings[s.id]['gate'] for s in steps}
            # Snapshot provider credentials and server URLs now; a later settings change must not alter queued work.
            secrets = {p: credentials[p] for p in providers_needed}
            job = runtime.store.create_job(book_id, 'pipeline')
            run = repository.create_run(book_id, job_id=job['id'], steps=[s.id for s in registry.closure(body.steps)],
                                        mode=body.mode, chapter_ids=chapter_ids, configs=configs, gates=gates,
                                        concurrency=body.concurrency, fresh=body.fresh, limits=body.limits.model_dump())
            job = runtime.store.update_job(job['id'], run_id=run['id'], steps=run['steps'], mode=body.mode,
                                           message='Waiting for the local worker')

            limits = body.limits.model_dump()
            runtime.pool.submit(lambda: execute_run(runtime, registry, job, run, secrets, limits=limits,
                                                    concurrency=body.concurrency, fresh=body.fresh))
            return {'job': job, 'run': run}

    def version_scopes(runtime, book_id, step, version_id):
        repository = PipelineRepository(runtime.store)
        if version_id == 'accepted':
            with runtime.store.lock, runtime.store.connect() as conn:
                return repository.heads(conn, book_id, step.id), None
        run = repository.step_run(book_id, version_id)
        if run['step_id'] != step.id:
            raise HTTPException(404, 'Step version not found')
        return dict(run.get('scopes', {})), run

    def require_decidable(runtime, book_id):
        runtime.store.require_active(book_id)
        for job in runtime.store.jobs(book_id):
            if job['status'] in ACTIVE and job['kind'] != 'pipeline':
                raise HTTPException(409, 'Another job is changing this book. Let it finish before accepting a version.')
        # A series run paused for the owner's review of this book lets it be decided;
        # it holds every other reservation (bardic.series_processing).
        if any(j['kind'] == 'series' and j['status'] in ACTIVE and book_id in j.get('book_ids', [])
               and (j.get('waiting_for_review') or {}).get('book_id') != book_id for j in runtime.store.jobs(limit=None)):
            raise HTTPException(409, 'This book is reserved by an active series run.')

    def selected(scopes, body):
        if body.scopes is None:
            return scopes
        unknown = set(body.scopes) - set(scopes)
        if unknown or not body.scopes:
            raise HTTPException(400, 'Choose scopes that this version contains.')
        return {s: scopes[s] for s in body.scopes}

    @router.get('/api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions')
    def versions(book_id: str, step_id: str, request: Request, limit: int = 50):
        runtime = runtime_of(request)
        step = step_of(step_id)
        repository = PipelineRepository(runtime.store)
        with runtime.store.lock:
            runtime.store.book(book_id)
            with runtime.store.connect() as conn:
                items = _versions_view(repository, registry, conn, book_id, step,
                                       repository.step_runs(book_id, step.id, max(1, min(200, limit))))
        return {'step_id': step.id, 'items': items, 'decisions': repository.decisions(book_id, step.id, 50)}

    @router.get('/api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions/{version_id}')
    def version(book_id: str, step_id: str, version_id: str, request: Request, compare: str = 'accepted',
                scope: str | None = None, changed_only: bool = False, offset: int = 0, limit: int = 200):
        runtime = runtime_of(request)
        step = step_of(step_id)
        if offset < 0 or not 1 <= limit <= ROW_LIMIT:
            raise HTTPException(400, f'Choose a nonnegative offset and a page size of 1–{ROW_LIMIT}.')
        repository = PipelineRepository(runtime.store)
        scopes, run = version_scopes(runtime, book_id, step, version_id)
        other = {}
        if compare != 'none' and compare != version_id:
            other, _ = version_scopes(runtime, book_id, step, compare)
        with runtime.store.lock:
            book = runtime.store.book(book_id)
            with runtime.store.connect() as conn:
                payloads = repository.payloads(conn, [*scopes.values(), *other.values()])
        table = step.summarize(book, {s: payloads[i]['result'] for s, i in scopes.items() if i in payloads})
        baseline = step.summarize(book, {s: payloads[i]['result'] for s, i in other.items() if i in payloads}) if other else None
        rows = table['rows']
        diff = {'compared_with': compare if other else None, 'same': 0, 'changed': 0, 'added': 0, 'removed': 0}
        if baseline is not None:
            before = {row['id']: row for row in baseline['rows']}
            keys = [c['key'] for c in table['columns']]
            for row in rows:
                previous = before.pop(row['id'], None)
                if previous is None:
                    row['_diff'] = 'added'
                    diff['added'] += 1
                    continue
                changed = [k for k in keys if row.get(k) != previous.get(k)]
                row['_diff'] = 'changed' if changed else 'same'
                row['_changed'] = changed
                row['_previous'] = {k: previous.get(k) for k in changed}
                diff['changed' if changed else 'same'] += 1
            diff['removed'] = len(before)
            compared = diff['same'] + diff['changed']
            diff['agreement'] = round(diff['same'] / compared, 4) if compared else None
        if scope:
            rows = [row for row in rows if row['scope'] == scope]
        if changed_only:
            rows = [row for row in rows if row.get('_diff') in {'changed', 'added'}]
        heads = {}
        with runtime.store.lock, runtime.store.connect() as conn:
            heads = repository.heads(conn, book_id, step.id)
        return {'step_id': step.id, 'version_id': version_id, 'run': run, 'stats': table['stats'], 'columns': table['columns'],
                'diff': diff, 'total_rows': len(rows), 'offset': offset, 'limit': limit, 'rows': rows[offset:offset + limit],
                'scopes': [{'scope': s, 'artifact_id': i, 'accepted': heads.get(s) == i} for s, i in scopes.items()],
                'revision': book.get('revision', 0)}

    @router.post('/api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions/{version_id}/preview')
    def preview(book_id: str, step_id: str, version_id: str, body: DecisionRequest, request: Request):
        runtime = runtime_of(request)
        step = step_of(step_id)
        scopes, _ = version_scopes(runtime, book_id, step, version_id)
        chosen = selected(scopes, body)
        repository = PipelineRepository(runtime.store)
        with runtime.store.lock:
            book = runtime.store.book(book_id)
            with runtime.store.connect() as conn:
                projection.sync(repository, registry, conn, book)
                impact = projection.preview(repository, registry, conn, book, step, chosen, audio_checker(runtime),
                                            new_character_voices(runtime))
        impact.pop('book')
        impact.pop('invalidated_segment_ids')
        return {**impact, 'revision': book.get('revision', 0)}

    @router.post('/api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions/{version_id}/accept')
    def accept(book_id: str, step_id: str, version_id: str, body: DecisionRequest, request: Request):
        runtime = runtime_of(request)
        step = step_of(step_id)
        scopes, run = version_scopes(runtime, book_id, step, version_id)
        if run and run['status'] in ACTIVE:
            raise HTTPException(409, 'This version is still running.')
        chosen = selected(scopes, body)
        if not chosen:
            raise HTTPException(400, 'This version has no results to accept.')
        with runtime.store.lock:
            require_decidable(runtime, book_id)
            try:
                return accept_versions(runtime, registry, book_id, step, chosen, run['id'] if run else None, mode='user',
                                       expected_revision=body.expected_revision)
            except projection.RevisionConflict as exc:
                raise HTTPException(409, str(exc)) from exc

    @router.post('/api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions/{version_id}/reject')
    def reject(book_id: str, step_id: str, version_id: str, body: DecisionRequest, request: Request):
        runtime = runtime_of(request)
        step = step_of(step_id)
        if version_id == 'accepted':
            raise HTTPException(400, 'Accept another version to replace the accepted one.')
        scopes, run = version_scopes(runtime, book_id, step, version_id)
        if run['status'] in ACTIVE:
            raise HTTPException(409, 'This version is still running.')
        chosen = selected(scopes, body)
        if not chosen:
            raise HTTPException(400, 'This version has no results to reject.')
        return projection.reject(runtime.store, PipelineRepository(runtime.store), book_id, step, chosen, step_run_id=run['id'],
                                 registry=registry)

    return router

