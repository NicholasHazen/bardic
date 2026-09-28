/* Analysis tab: run pipeline steps, review each step's versions, accept or restore.
   Model work starts only from Confirm on a current plan preview; changing a setting
   saves it and never starts work. Result rows hold book and model text, which is
   untrusted: every value is escaped before it reaches innerHTML.
   Steps run one at a time from their own panel. Selections, previews and messages belong
   to one visit: leaving the tab discards them. */
(() => {
  'use strict';
  const panels = new WeakMap();
  // Finished runs already shown to the owner, by book: their summary is not shown again.
  const acknowledged = new Map();
  const ACTIVE = new Set(['queued', 'running']);
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const path = value => encodeURIComponent(value);
  const MODEL_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;
  const CUSTOM = '__custom__';
  const PAGE_SIZE = 200;
  const POLL_MS = 2000;
  const TIERS = [['economy', 'Economy · quick and inexpensive'], ['balanced', 'Balanced'], ['deep', 'Deep · most thorough'], ['other', 'Other models']];
  const VERSION_STATES = {running:'Running', candidate:'Waiting for review', accepted:'Accepted', partly_accepted:'Partly accepted',
    rejected:'Rejected', superseded:'Superseded', same_as_accepted:'Same as accepted', empty:'No results'};
  const RUN_STATUS = {queued:'Queued', running:'Running', completed:'Completed', failed:'Failed', cancelled:'Cancelled',
    interrupted:'Interrupted', budget_limited:'Allowance reached', skipped:'Skipped'};
  const PROVIDERS = {local:'Local', gemini:'Gemini', openai:'OpenAI', anthropic:'Anthropic', local_llm:'Local LLM',
    booknlp:'BookNLP', novel_analyzer:'Novel Analyzer'};
  const PROBLEM_STATUS = new Set(['failed', 'cancelled', 'interrupted', 'budget_limited']);
  const number = value => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('en-US') : '—';
  const money = value => typeof value === 'number' && Number.isFinite(value) ? `$${value.toFixed(value > 0 && value < .01 ? 4 : 2)}` : 'Unknown price';
  const plural = (count, one, many = `${one}s`) => `${number(count)} ${count === 1 ? one : many}`;
  const when = value => {
    if (!value) return 'Time not recorded';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? 'Time not recorded' : date.toLocaleString();
  };
  const clip = (text, size) => text.length > size ? `${text.slice(0, size)}…` : text;
  const providerName = id => PROVIDERS[id] || String(id || 'Unknown');

  // Generic result-table values: rows come from any step's summarize() output.
  function cell(value) {
    if (value === null || value === undefined || value === '') return '—';
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (typeof value === 'number') return number(value);
    if (Array.isArray(value)) return value.length ? value.map(cell).join(', ') : '—';
    if (typeof value === 'object') { try { return JSON.stringify(value); } catch { return String(value); } }
    return String(value);
  }

  async function call(url, {method = 'GET', body} = {}) {
    const headers = {Accept:'application/json'};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch(url, {method, headers, ...(body === undefined ? {} : {body:JSON.stringify(body)})});
    let value;
    try { value = await response.json(); } catch { value = null; }
    if (!response.ok) {
      const detail = value?.detail;
      // Server details describe the condition; where to fix it is keyed on the error code.
      const hint = {api_key_missing:' Add it in Settings.', server_url_missing:' Add it in Settings.'}[value?.code] || '';
      const text = typeof detail === 'string' ? detail + hint
        : Array.isArray(detail) ? detail.map(item => item?.msg || JSON.stringify(item)).join('; ')
        : `Request failed (${response.status}). Try again.`;
      const error = new Error(text);
      error.status = response.status;
      error.code = value?.code;
      throw error;
    }
    return value;
  }

  // --- panel state -------------------------------------------------------------------
  const freshView = () => ({compare:'accepted', changedOnly:false, scope:'', offset:0});
  const freshRun = () => ({concurrency:'2', fresh:false});
  // Everything the owner picked or opened during one visit to the tab.
  const visitState = () => ({selected:null, chapterId:'', run:freshRun(), custom:{}, versions:null, versionsKey:'',
    versionId:null, result:null, resultLoading:false, resultError:null, view:freshView(), plan:null, impact:null, working:null,
    message:'', messageError:false});

  function create(container) {
    const panel = {container, book:null, bookId:null, revision:null, busy:false, options:{}, status:null, shell:false,
      defs:null, overview:null, loaded:false, dirty:false, shown:false, ...visitState(), html:{}, timer:null, observer:null,
      seq:{defs:0, overview:0, versions:0, result:0, plan:0, impact:0, settings:{}}};
    // Load lazily: nothing is fetched until the Analysis tab is shown. The tab, or the
    // whole book workspace around it, can be hidden, so watch every ancestor.
    if (typeof MutationObserver === 'function') {
      panel.observer = new MutationObserver(() => {
        if (!panel.book) { stopTimer(panel); return; }
        if (hidden(panel)) { leave(panel); return; }
        panel.dirty = true;
        void render(container, panel.book, panel.options);
      });
      for (let node = container; node; node = node.parentElement) {
        panel.observer.observe(node, {attributes:true, attributeFilter:['hidden']});
      }
    }
    return panel;
  }

  // In-flight responses for the discarded selections are ignored.
  function clearVisit(panel) {
    for (const key of ['versions', 'result', 'plan', 'impact']) panel.seq[key]++;
    Object.assign(panel, visitState());
  }

  function resetBook(panel) {
    panel.seq.defs++; panel.seq.overview++;
    clearVisit(panel);
    Object.assign(panel, {overview:null, loaded:false, dirty:false});
    stopTimer(panel);
  }

  // Leaving the tab (or the book workspace) ends the visit: the next one starts clean.
  function leave(panel) {
    stopTimer(panel);
    if (!panel.shown) return;
    panel.shown = false;
    const last = panel.overview?.recent_runs?.[0];
    if (last && !ACTIVE.has(last.status) && !panel.overview?.active_run) acknowledged.set(panel.bookId, last.id);
    clearVisit(panel);
  }

  // Hidden either as a tab panel or because an ancestor (the whole book workspace in Library view) is hidden.
  const hidden = panel => Boolean(panel.observer) && (panel.container.hidden === true || Boolean(panel.container.closest?.('[hidden]')));
  const base = panel => `/api/books/${path(panel.bookId)}/analysis-pipeline`;
  const current = (panel, bookId, key, seq) => panel.bookId === bookId && panel.seq[key] === seq;
  const stepDef = (panel, id) => panel.defs?.steps?.find(step => step.id === id) || null;
  const stepState = (panel, id) => panel.overview?.steps?.find(step => step.id === id) || null;
  const versionItem = (panel, id) => panel.versions?.items?.find(item => item.id === id) || null;
  const chapters = panel => panel.overview?.chapters || panel.book?.chapters || [];
  const providerDef = (panel, id) => panel.defs?.providers?.find(p => p.id === id) || null;
  // Self-hosted providers are "ready" when a server URL is set; cloud ones when a key is.
  // Status is refreshed after Settings saves, so a newly entered URL counts without reloading the tab.
  const providerHasKey = (panel, id) => providerDef(panel, id)?.self_hosted
    ? Boolean(panel.status?.local_service_urls ? panel.status.local_service_urls[id] : providerDef(panel, id)?.configured)
    : panel.status?.analysis_providers?.find(p => p.id === id)?.has_api_key ?? providerDef(panel, id)?.configured ?? false;
  // A provider the step reads results from instead of calling (BookNLP on Speakers & delivery).
  const offline = (def, id) => Array.isArray(def?.offline_providers) && def.offline_providers.includes(id);
  // Chapter services (BookNLP, Novel Analyzer) run on the owner's server and take no model.
  const isService = (panel, id) => providerDef(panel, id)?.kind === 'service';
  const needsModel = (panel, def, provider) => def.method !== 'plain' && !isService(panel, provider);
  const missingWhat = (panel, id) => providerDef(panel, id)?.needs === 'url' ? 'no server URL' : 'no API key';
  const stepProviders = (panel, def) => (panel.defs?.providers || []).filter(p => !Array.isArray(def.providers) || def.providers.includes(p.id));

  function scopeLabel(panel, scope) {
    if (scope === 'book') return 'Whole book';
    const chapter = chapters(panel).find(item => item.id === scope);
    if (chapter) return chapter.title || scope;
    return panel.book?.characters?.find(item => item.id === scope)?.name || scope;
  }

  function versionSource(item) {
    if (!item) return '';
    if (item.origin === 'baseline') return 'Existing work · baseline';
    if (item.origin === 'external') return 'Outside change · external';
    if (!item.provider || item.provider === 'local') return 'Local · on this device';
    return `${providerName(item.provider)}${item.model ? ` · ${item.model}` : ''}`;
  }
  const isRestore = item => Boolean(item) && (item.state === 'superseded' || item.origin === 'baseline' || item.origin === 'external');
  const canAccept = item => Boolean(item) && !['running', 'empty', 'accepted', 'same_as_accepted'].includes(item.state) && (item.scope_count || 0) > 0;
  // Accepting rewrites the book, which the server refuses while another kind of job is changing it.
  const acceptBlocked = panel => panel.busy && !panel.overview?.active_run
    ? 'Another job is working on this book. Accept or restore after it finishes.' : null;

  function blocked(panel) {
    if (panel.overview?.active_run) return 'A pipeline run is in progress. Wait for it to finish, or cancel it from the job banner.';
    if (panel.busy) return 'Another job is working on this book. Wait for it to finish before starting a run.';
    return null;
  }

  // --- prerequisites -------------------------------------------------------------------
  // A step reads the accepted results of its required inputs; the server enforces the same rule.
  const requirements = def => Array.isArray(def?.requires) ? def.requires : def?.inputs || [];
  const hasAccepted = (panel, id) => {
    const state = stepState(panel, id);
    return Boolean(state?.has_accepted ?? state?.accepted_scopes);
  };
  const unmetInputs = (panel, def) => requirements(def).filter(id => !hasAccepted(panel, id));
  const stepLabel = (panel, id) => stepDef(panel, id)?.label || id;
  const joined = labels => labels.length > 1 ? `${labels.slice(0, -1).join(', ')} and ${labels.at(-1)}` : labels[0] || '';

  function unmetText(panel, def, missing) {
    const labels = joined(missing.map(id => stepLabel(panel, id)));
    const waiting = missing.filter(id => stepState(panel, id)?.pending_versions);
    const how = waiting.length === missing.length
      ? `${joined(waiting.map(id => stepLabel(panel, id)))} ${waiting.length === 1 ? 'has a version' : 'have versions'} waiting for your review: accept ${waiting.length === 1 ? 'it' : 'them'} first.`
      : `Run ${labels} and accept ${missing.length === 1 ? 'its' : 'their'} results first.`;
    return `${def.label} needs accepted results from ${labels}. ${how}`;
  }

  function unmetFor(panel, stepId) {
    const def = stepDef(panel, stepId);
    const missing = unmetInputs(panel, def);
    return missing.length ? unmetText(panel, def, missing) : null;
  }

  function missingKeys(panel, configs) {
    const providers = [...new Set(Object.entries(configs).filter(([stepId, config]) => !offline(stepDef(panel, stepId), config.provider))
      .map(([, config]) => config.provider).filter(id => id && id !== 'local'))];
    return providers.filter(id => !providerHasKey(panel, id))
      .map(id => `${providerName(id)} ${providerDef(panel, id)?.needs === 'url' ? 'server URL' : 'API key'}`);
  }

  // --- model choices -------------------------------------------------------------------
  function catalog(panel, provider) {
    const models = panel.status?.model_catalogs?.[provider]?.models || panel.status?.analysis_providers?.find(p => p.id === provider)?.models
      || providerDef(panel, provider)?.models || [];
    return models.map(item => typeof item === 'string' ? {id:item, label:item} : item)
      .filter(item => item?.id && (!Array.isArray(item.roles) || item.roles.some(role => role === 'analysis' || role === 'preprocess')));
  }

  function defaultModel(panel, def, provider) {
    const byRole = def.default_model_role === 'scan' ? panel.status?.preprocess_models_by_provider : panel.status?.analysis_models_by_provider;
    const preferred = byRole?.[provider];
    if (typeof preferred === 'string' && MODEL_ID.test(preferred)) return preferred;
    const models = catalog(panel, provider);
    return (models.find(item => item.tier === (def.default_model_role === 'scan' ? 'economy' : 'balanced')) || models[0])?.id || null;
  }

  const price = item => item.input_usd_per_million != null && item.output_usd_per_million != null
    ? ` · $${item.input_usd_per_million} in / $${item.output_usd_per_million} out per M tokens` : ' · price unknown';

  // --- painting ----------------------------------------------------------------------------
  function put(panel, name, html) {
    const node = panel.container.querySelector(`[data-ap-region="${name}"]`);
    if (!node || panel.html[name] === html) return node;
    const active = globalThis.document?.activeElement;
    const key = active && typeof node.contains === 'function' && node.contains(active) ? active.getAttribute?.('data-ap-key') : null;
    node.innerHTML = html;
    panel.html[name] = html;
    if (key) node.querySelector(`[data-ap-key="${key}"]`)?.focus?.({preventScroll:true});
    return node;
  }

  function focusRegion(panel, key) {
    panel.container.querySelector(`[data-ap-key="${key}"]`)?.focus?.({preventScroll:true});
  }

  // Messages belong to the current visit; a late response after leaving must not leave one behind.
  function say(panel, text, error = false) {
    if (!panel.shown) return;
    panel.message = text;
    panel.messageError = error;
    paintMessage(panel);
  }

  function paintMessage(panel) {
    put(panel, 'message', panel.message ? `<span class="${panel.messageError ? 'ap-error' : ''}" ${panel.messageError ? 'role="alert"' : ''}>${escapeHtml(panel.message)}</span>` : '');
  }

  function paintShell(panel) {
    panel.container.innerHTML = `<div class="ap"><div class="section-intro ap-intro"><div><span class="eyebrow">THE ANALYSIS PIPELINE</span><h2>How the story is understood.</h2><p>Each step reads the book, or results you accepted from earlier steps, and records a new version. Compare versions, accept one, or restore an earlier one. Nothing runs until you confirm a preview.</p></div></div><div class="ap-message" data-ap-region="message" role="status" aria-live="polite"></div><section class="ap-runs" data-ap-region="runs" aria-label="Runs"></section><div class="ap-layout"><nav class="ap-steps" aria-label="Analysis steps" data-ap-region="steps"></nav><div class="ap-main"><section class="ap-detail" data-ap-region="detail" aria-label="Step settings"></section><div data-ap-region="plan"></div><section class="ap-versions" data-ap-region="versions" aria-label="Version history"></section><section class="ap-result" data-ap-region="result" aria-label="Version results"></section></div></div></div>`;
    panel.html = {};
    panel.shell = true;
  }

  function paint(panel) {
    if (!panel.shell || !panel.bookId) return;
    paintMessage(panel); paintRuns(panel); paintPlan(panel); paintSteps(panel); paintDetail(panel); paintVersions(panel); paintResult(panel);
  }

  function runSummary(panel, run, active) {
    const labels = (run.steps || []).map(id => stepDef(panel, id)?.label || id).join(', ');
    const outcomes = Object.entries(run.outcomes || {}).filter(([, outcome]) => outcome && (outcome.reason || outcome.error || outcome.status !== 'completed'));
    return `<div class="${active ? 'ap-note ap-active-run' : 'ap-last-run'}">${active ? '' : '<button type="button" class="button subtle ap-dismiss" data-ap-action="dismiss-run" data-ap-key="dismiss-run" aria-label="Dismiss the latest run summary">Dismiss</button>'}<strong>${active ? 'Run in progress' : 'Latest run'}</strong> ·<span class="ap-chip ${escapeHtml(run.status === 'completed' ? 'accepted' : PROBLEM_STATUS.has(run.status) ? 'failed' : 'running')}">${escapeHtml(RUN_STATUS[run.status] || run.status || 'Unknown')}</span> · ${escapeHtml(labels)} · ${escapeHtml(when(run.created_at))}${active ? '<p class="ap-help">Cancel it from the job banner above. Validated units are kept and reused.</p>' : ''}${outcomes.length ? `<ul class="ap-outcomes">${outcomes.map(([id, outcome]) => `<li><strong>${escapeHtml(stepDef(panel, id)?.label || id)}</strong>: ${escapeHtml(RUN_STATUS[outcome.status] || outcome.status || '')}${outcome.reason ? ` · ${escapeHtml(outcome.reason)}` : ''}${outcome.error ? ` · ${escapeHtml(outcome.error)}` : ''}</li>`).join('')}</ul>` : ''}${run.error ? `<p class="ap-error">${escapeHtml(run.error)}</p>` : ''}</div>`;
  }

  // The active run, or the latest finished one until it is dismissed or the visit ends.
  function paintRuns(panel) {
    const active = panel.overview?.active_run;
    const recent = !active ? panel.overview?.recent_runs?.[0] : null;
    const last = recent && acknowledged.get(panel.bookId) !== recent.id ? recent : null;
    put(panel, 'runs', `<div class="ap-runs-head">${active ? runSummary(panel, active, true) : last ? runSummary(panel, last, false) : '<p class="ap-help">Choose a step, then run it. Each run shows a preview with the estimated requests and cost first.</p>'}<button type="button" class="button subtle" data-ap-action="refresh" data-ap-key="refresh">Refresh</button></div>`);
  }

  function paintPlan(panel) {
    const plan = panel.plan;
    if (!plan) { put(panel, 'plan', ''); return; }
    const labels = plan.body.steps.map(id => stepDef(panel, id)?.label || id);
    const chapterScoped = plan.body.steps.some(id => stepDef(panel, id)?.chapter_scoped);
    const sections = plan.body.chapter_ids ? scopeLabel(panel, plan.body.chapter_ids[0]) : 'All story sections';
    const head = `<h3 tabindex="-1" data-ap-key="plan-heading">Review before running</h3><p class="ap-help">${escapeHtml(labels.join(', '))}${chapterScoped ? ` · Sections: ${escapeHtml(sections)}` : ''}</p>`;
    let body = '';
    if (plan.loading) body = '<p class="ap-help" role="status">Estimating the work… No requests are sent.</p>';
    else if (plan.value) {
      const value = plan.value;
      const steps = value.steps || [];
      const units = steps.reduce((sum, step) => sum + (step.units || 0), 0);
      const missing = missingKeys(panel, plan.body.configs);
      // The server's answer wins over the overview, which may be older than the plan.
      const unmet = Object.entries(value.missing_inputs || {}).find(([, inputs]) => inputs?.length);
      const problem = blocked(panel) || (unmet ? unmetText(panel, stepDef(panel, unmet[0]) || {label:unmet[0]}, unmet[1]) : null)
        || (value.missing_inputs ? null : unmetFor(panel, plan.body.steps[0])) || (missing.length ? `Add in Providers & settings first: ${missing.join(', ')}.` : null)
        || (!units ? 'Nothing to run: these steps have no work for the current inputs.' : null);
      const cost = step => step.method === 'plain' || step.estimated_cost_usd === 0 ? 'Free' : money(step.estimated_cost_usd);
      const where = step => step.method === 'plain' ? 'Runs locally · free'
        : isService(panel, step.provider) ? `${escapeHtml(providerName(step.provider))} · your server · free`
        : `${escapeHtml(providerName(step.provider))} · ${escapeHtml(step.model || 'no model')}`;
      // Steps that call your own servers or the Local LLM: free, but they load that machine's GPU.
      const selfHosted = steps.some(step => providerDef(panel, step.provider)?.self_hosted && !offline(stepDef(panel, step.step_id), step.provider));
      const model = steps.some(step => step.method !== 'plain' && !isService(panel, step.provider));
      body = `<div class="ap-table-wrap"><table><caption class="sr-only">Estimated work per step</caption><thead><tr><th scope="col">Step</th><th scope="col">Units</th><th scope="col">Reused</th><th scope="col">Requests</th><th scope="col">Input tokens (est.)</th><th scope="col">Output allowance</th><th scope="col">Estimated cost</th></tr></thead><tbody>${steps.map(step => `<tr><th scope="row">${escapeHtml(step.label || step.step_id)}<small>${where(step)}</small>${step.note ? `<small class="ap-plan-step-note">${escapeHtml(step.note)}</small>` : ''}</th><td>${number(step.units)}</td><td>${number(step.cached_units)}</td><td>${step.service_calls ? escapeHtml(plural(step.service_calls, 'service call')) : number(step.requests)}</td><td>${number(step.estimated_input_tokens)}</td><td>${number(step.output_token_allowance)}</td><td>${escapeHtml(cost(step))}</td></tr>`).join('')}</tbody></table></div><dl class="ap-plan-totals"><div><dt>Model requests</dt><dd>${number(value.requests)}</dd></div>${value.service_calls ? `<div><dt>Calls to your servers</dt><dd>${number(value.service_calls)}</dd></div>` : ''}<div><dt>Reused results</dt><dd>${number(value.cached_units)}</dd></div><div><dt>Input tokens (est.)</dt><dd>${number(value.estimated_input_tokens)}</dd></div><div><dt>Estimated cost</dt><dd>${escapeHtml(value.requests && value.estimated_cost_usd !== 0 ? money(value.estimated_cost_usd) : 'Free')}</dd></div></dl>${value.estimated_cost_usd == null && value.requests ? '<p class="ap-note">A model in this plan has no known price, so the cost cannot be estimated.</p>' : ''}${value.note ? `<p class="ap-help">${escapeHtml(value.note)}</p>` : ''}${selfHosted ? '<p class="ap-note">Your own servers cost nothing per request, but they use that machine’s GPU. If Breeze narration runs there, listening may stall while this runs. Service calls are not counted as model requests.</p>' : ''}${model ? `<p class="ap-help">${escapeHtml(plural(Number(panel.run.concurrency), 'request'))} at once${panel.run.fresh ? ', with fresh samples' : ''}. There is no request or dollar cap. Each unit can take up to four requests (one retry after a transient error, and one evidence repair), so a run can send more requests than estimated. Cancel from the job banner at any time; validated work is kept.</p>` : ''}${problem ? `<p class="ap-error" role="alert">${escapeHtml(problem)}</p>` : ''}<div class="ap-actions"><button type="button" class="button subtle" data-ap-action="cancel-plan">Cancel</button><button type="button" class="button primary" data-ap-action="confirm-run" data-ap-key="confirm-run" ${problem || plan.starting ? 'disabled' : ''}>${plan.starting ? 'Starting…' : value.requests ? `Confirm and run · about ${escapeHtml(plural(value.requests, 'request'))}${value.estimated_cost_usd != null ? `, ${escapeHtml(money(value.estimated_cost_usd))}` : ''}` : value.service_calls ? `Confirm and run · ${escapeHtml(plural(value.service_calls, 'call'))} to your servers` : 'Confirm and run locally'}</button></div>`;
    }
    const error = plan.error ? `<p class="ap-error" role="alert">${escapeHtml(plan.error)}</p>${plan.stale ? '<div class="ap-actions"><button type="button" class="button subtle" data-ap-action="cancel-plan">Cancel</button><button type="button" class="button primary" data-ap-action="replan">Preview again</button></div>' : ''}` : '';
    put(panel, 'plan', `<section class="ap-plan" aria-label="Run preview">${head}${body}${error}${!plan.value && !plan.loading && !plan.stale ? '<div class="ap-actions"><button type="button" class="button subtle" data-ap-action="cancel-plan">Close</button></div>' : ''}</section>`);
  }

  function stepChips(panel, def) {
    const state = stepState(panel, def.id);
    if (!state) return panel.overview ? '' : '<span class="ap-chip idle">Loading</span>';
    const chips = [];
    const latest = state.latest;
    const missing = unmetInputs(panel, def);
    if (missing.length) chips.push(['blocked', `Needs ${joined(missing.map(id => stepLabel(panel, id)))}`]);
    if (latest?.state === 'running') chips.push(['running', 'Running']);
    else if (latest && PROBLEM_STATUS.has(latest.status)) chips.push(['failed', RUN_STATUS[latest.status] || 'Failed']);
    if (state.pending_versions) chips.push(['candidate', state.pending_versions === 1 ? 'Waiting for review' : `${number(state.pending_versions)} waiting for review`]);
    if (state.accepted_scopes) chips.push(['accepted', `Accepted ${number(state.accepted_scopes)}/${number(state.total_scopes)}`]);
    if (state.stale_scopes?.length) chips.push(['stale', `Stale (${number(state.stale_scopes.length)})`]);
    if (!chips.length) chips.push(['idle', 'Not run']);
    return chips.map(([kind, text]) => `<span class="ap-chip ${kind}">${escapeHtml(text)}</span>`).join('');
  }

  function paintSteps(panel) {
    if (!panel.defs) { put(panel, 'steps', '<p class="ap-help">Loading steps…</p>'); return; }
    put(panel, 'steps', `<ol class="ap-step-list">${panel.defs.steps.map((def, index) => {
      const selected = def.id === panel.selected;
      const id = escapeHtml(def.id);
      return `<li class="ap-step${selected ? ' selected' : ''}"><button type="button" data-ap-step="${id}" data-ap-key="step-${id}" aria-current="${selected ? 'true' : 'false'}"><span class="ap-step-name"><span class="ap-step-index" aria-hidden="true">${index + 1}</span>${escapeHtml(def.label)}</span><span class="ap-step-meta"><span class="ap-method ${def.method === 'plain' ? 'plain' : 'llm'}">${def.method === 'plain' ? 'Local' : isService(panel, def.settings?.provider) ? 'Service' : providerDef(panel, def.settings?.provider)?.self_hosted ? 'Your model' : 'Model'}</span>${stepChips(panel, def)}</span></button></li>`;
    }).join('')}</ol><p class="ap-help">Run steps in this order: each one reads the results you accepted from the steps before it.</p>`);
  }

  function modelField(panel, def) {
    const settings = def.settings || {};
    const pending = panel.custom[def.id];
    const provider = pending?.provider || settings.provider;
    const models = catalog(panel, provider);
    const inCatalog = provider === settings.provider && models.some(item => item.id === settings.model);
    const customOpen = Boolean(pending?.open) || !inCatalog;
    const groups = TIERS.map(([tier, label]) => [label, models.filter(item => (TIERS.some(([known]) => known === item.tier) ? item.tier : 'other') === tier)])
      .filter(([, items]) => items.length);
    const options = groups.map(([label, items]) => `<optgroup label="${escapeHtml(label)}">${items.map(item => `<option value="${escapeHtml(item.id)}" ${!customOpen && item.id === settings.model ? 'selected' : ''}>${escapeHtml(`${item.label || item.id}${price(item)}`)}</option>`).join('')}</optgroup>`).join('');
    const text = pending?.text ?? (inCatalog ? '' : settings.model || '');
    const id = escapeHtml(def.id);
    return `<div class="ap-field"><label for="ap-model-${id}">Model <span class="ap-help-inline">Model choice sets how thorough and costly this step is.</span></label><select id="ap-model-${id}" name="ap_model" data-ap-key="model">${options}<option value="${CUSTOM}" ${customOpen ? 'selected' : ''}>Custom model ID…</option></select>${customOpen ? `<label class="sr-only" for="ap-custom-${id}">Custom model ID</label><input id="ap-custom-${id}" name="ap_custom_model" data-ap-key="custom-model" maxlength="200" spellcheck="false" autocomplete="off" placeholder="Enter a model ID, then press Enter" value="${escapeHtml(text)}">${pending?.error ? `<p class="ap-error" role="alert">${escapeHtml(pending.error)}</p>` : '<p class="ap-help">Custom IDs are sent as typed; their price is unknown.</p>'}` : ''}</div>`;
  }

  function paintDetail(panel) {
    const def = stepDef(panel, panel.selected);
    if (!def) { put(panel, 'detail', panel.defs ? '<p class="ap-help">Choose a step.</p>' : ''); return; }
    const state = stepState(panel, def.id);
    const settings = def.settings || {};
    const id = escapeHtml(def.id);
    const inputs = def.inputs?.length ? `Reads accepted results from ${def.inputs.map(input => escapeHtml(stepDef(panel, input)?.label || input)).join(' and ')}.` : 'Reads the book text.';
    const scopes = {book:'one result for the whole book', chapter:'one result per section', character:'one result per character'}[def.scope] || 'results';
    const pendingProvider = panel.custom[def.id]?.provider || settings.provider;
    const origins = state?.accepted_origins || {};
    const recorded = (origins.baseline || 0) + (origins.external || 0);
    const stale = state?.stale_scopes || [];
    const reason = blocked(panel);
    const missing = panel.overview ? unmetInputs(panel, def) : [];
    const needs = missing.length ? `<div class="ap-note ap-needs" role="note"><p><strong>Not ready to run.</strong> ${escapeHtml(unmetText(panel, def, missing))}</p><div class="ap-actions">${missing.map(input => `<button type="button" class="button subtle" data-ap-step="${escapeHtml(input)}" data-ap-key="go-${escapeHtml(input)}">Go to ${escapeHtml(stepLabel(panel, input))}</button>`).join('')}</div></div>` : '';
    const method = def.method === 'plain'
      ? '<p class="ap-local">Runs locally, free. No model or API key is used.</p>'
      : `<div class="ap-field-row"><div class="ap-field"><label for="ap-provider-${id}">Provider</label><select id="ap-provider-${id}" name="ap_provider" data-ap-key="provider">${stepProviders(panel, def).map(provider => `<option value="${escapeHtml(provider.id)}" ${provider.id === pendingProvider ? 'selected' : ''}>${escapeHtml(provider.label || providerName(provider.id))}${offline(def, provider.id) ? ' · from Quote attribution' : provider.self_hosted ? ' · your server' : ''}${offline(def, provider.id) || providerHasKey(panel, provider.id) ? '' : ` · ${missingWhat(panel, provider.id)}`}</option>`).join('')}</select>${offline(def, pendingProvider) || providerHasKey(panel, pendingProvider) ? '' : `<p class="ap-note">This provider has ${escapeHtml(missingWhat(panel, pendingProvider))}. Add ${providerDef(panel, pendingProvider)?.needs === 'url' ? 'its server URL' : 'one'} in Providers &amp; settings before running this step.</p>`}</div>${needsModel(panel, def, pendingProvider) ? modelField(panel, def) : ''}</div><p class="ap-help">${offline(def, pendingProvider)
        ? 'Uses the accepted Quote attribution (BookNLP) results for speakers; nothing is sent. Chapters without them fail.'
        : providerDef(panel, pendingProvider)?.self_hosted
        ? 'Book text is sent to your own server. It is free per request, but shares that machine’s GPU (and Breeze narration’s, if it runs there).'
        : 'Book text is sent to the provider you choose.'}</p>`;
    const chapterField = def.chapter_scoped ? `<div class="ap-field"><label for="ap-chapter-${id}">Sections to process</label><select id="ap-chapter-${id}" name="ap_chapter" data-ap-key="chapter"><option value="">All story sections</option>${chapters(panel).map(chapter => `<option value="${escapeHtml(chapter.id)}" ${chapter.id === panel.chapterId ? 'selected' : ''}>${escapeHtml(chapter.title || chapter.id)}${chapter.kind && chapter.kind !== 'chapter' ? ` (${escapeHtml(String(chapter.kind).replaceAll('_', ' '))})` : ''}</option>`).join('')}</select></div>` : '';
    const gate = `<fieldset class="ap-gate"><legend>After a run</legend><label class="ap-check"><input type="radio" name="ap_gate" value="auto" data-ap-key="gate-auto" ${settings.gate !== 'review' ? 'checked' : ''}> Accept automatically</label><label class="ap-check"><input type="radio" name="ap_gate" value="review" data-ap-key="gate-review" ${settings.gate === 'review' ? 'checked' : ''}> Hold for my review</label></fieldset>`;
    const notes = [
      state ? `<p class="ap-help">Accepted ${number(state.accepted_scopes)} of ${plural(state.total_scopes, 'result')}${state.pending_versions ? ` · ${plural(state.pending_versions, 'version')} waiting for review` : ''}.</p>` : '',
      recorded ? `<p class="ap-note">${plural(recorded, 'accepted result')} ${recorded === 1 ? 'was' : 'were'} recorded from existing work; producer unknown.</p>` : '',
      stale.length ? `<p class="ap-note ap-stale">${plural(stale.length, 'accepted result')} ${stale.length === 1 ? 'was' : 'were'} accepted using inputs that have since changed: ${escapeHtml(stale.map(scope => scopeLabel(panel, scope)).join(', '))}. Run this step again to refresh ${stale.length === 1 ? 'it' : 'them'}.</p>` : '',
    ].join('');
    put(panel, 'detail', `<div class="ap-detail-head"><div><span class="eyebrow">${def.method === 'plain' ? 'LOCAL STEP' : 'MODEL STEP'}</span><h3 tabindex="-1" data-ap-key="detail-heading">${escapeHtml(def.label)}</h3></div></div>${needs}<p>${escapeHtml(def.summary || '')}</p><p class="ap-help">${inputs} Produces ${escapeHtml(scopes)}.</p>${notes}${method}${chapterField}${gate}${runControls(panel, def, reason, missing)}<details class="ap-technical"><summary>Technical details</summary><dl><div><dt>Step ID</dt><dd><code>${id}</code></dd></div><div><dt>Recipe version</dt><dd>${escapeHtml(def.version)}</dd></div><div><dt>Updates</dt><dd>${def.owns?.length ? def.owns.map(field => `<code>${escapeHtml(field)}</code>`).join(' ') : 'Nothing in the book'}</dd></div><div><dt>Units at once</dt><dd>${escapeHtml(def.parallel)}</dd></div></dl></details>`);
  }

  function runControls(panel, def, reason, missing) {
    const run = panel.run;
    const option = value => `<option value="${value}" ${String(run.concurrency) === String(value) ? 'selected' : ''}>${value}</option>`;
    const disabled = reason || missing.length || !panel.overview || panel.plan?.loading || panel.plan?.starting;
    // Local steps have nothing to send or reuse, so only model steps get these options.
    const options = def.method === 'plain' ? '' : `<label>Requests at once<select name="ap_concurrency" data-ap-key="concurrency">${[1, 2, 3, 4].map(option).join('')}</select></label><label class="ap-check"><input type="checkbox" name="ap_fresh" data-ap-key="fresh" ${run.fresh ? 'checked' : ''}> Fresh samples</label>`;
    const help = def.method === 'plain' ? '' : `<p class="ap-help">${run.fresh ? 'Fresh samples request new results even where an identical validated result is saved.' : 'Validated results from earlier identical requests are reused at no cost.'}</p>`;
    return `<div class="ap-run"><div class="ap-run-fields">${options}<button type="button" class="button primary" data-ap-action="plan-step" data-ap-key="plan-step" ${disabled ? 'disabled' : ''}>Run this step</button></div>${help}${reason && panel.overview ? `<p class="ap-help">${escapeHtml(reason)}</p>` : ''}</div>`;
  }

  function unitsText(item) {
    const units = item.units || {};
    if (item.origin !== 'run' || !units.total) return '';
    return `${number(units.done)}/${number(units.total)} units done · ${number(units.cached)} reused · ${number(units.failed)} failed`;
  }

  function paintVersions(panel) {
    const def = stepDef(panel, panel.selected);
    if (!def) { put(panel, 'versions', ''); return; }
    const items = panel.versions?.stepId === def.id ? panel.versions.items || [] : null;
    if (!items) { put(panel, 'versions', '<h4>Versions</h4><p class="ap-help">Loading versions…</p>'); return; }
    put(panel, 'versions', `<h4>Versions <span class="ap-help-inline">Newest first</span></h4>${items.length ? `<ol class="ap-version-list">${items.map(item => {
      const scopes = [`${plural(item.scope_count || 0, 'result')}`, item.accepted_scopes ? `${number(item.accepted_scopes)} accepted` : '',
        item.unchanged_scopes?.length ? `${number(item.unchanged_scopes.length)} unchanged` : '',
        item.incomplete_scopes?.length ? `${number(item.incomplete_scopes.length)} incomplete` : ''].filter(Boolean).join(' · ');
      return `<li><button type="button" data-ap-version="${escapeHtml(item.id)}" data-ap-key="version-${escapeHtml(item.id)}" aria-pressed="${item.id === panel.versionId}"><span class="ap-version-top"><span class="ap-chip ${escapeHtml(VERSION_STATES[item.state] ? item.state : 'idle')}">${escapeHtml(VERSION_STATES[item.state] || item.state || 'Unknown')}</span>${item.status && item.status !== 'completed' && item.state !== 'running' ? `<span class="ap-chip ${PROBLEM_STATUS.has(item.status) ? 'failed' : 'idle'}">${escapeHtml(RUN_STATUS[item.status] || item.status)}</span>` : ''}<strong>${escapeHtml(versionSource(item))}</strong></span><small>${escapeHtml(when(item.created_at))}${unitsText(item) ? ` · ${escapeHtml(unitsText(item))}` : ''} · ${escapeHtml(scopes)}</small>${item.origin !== 'run' ? '<small>Recorded from existing work; producer unknown.</small>' : ''}${item.error ? `<small class="ap-error">${escapeHtml(item.error)}</small>` : ''}</button></li>`;
    }).join('')}</ol>` : '<p class="ap-help">No versions yet. Run this step to create one.</p>'}`);
  }

  function impactHtml(panel, item) {
    const impact = panel.impact;
    if (!impact || impact.versionId !== item.id) return '';
    const verb = impact.restore ? 'Restore' : 'Accept';
    let body = '';
    if (impact.loading) body = '<p class="ap-help" role="status">Checking what this would change…</p>';
    else if (impact.value) {
      const value = impact.value;
      const changed = value.changed_scopes || [];
      const conflicts = value.conflicts || [];
      const downstream = value.downstream_steps_affected || [];
      body = `<ul class="ap-impact-list"><li>${changed.length ? `Changes ${plural(changed.length, 'result')}: ${escapeHtml(changed.map(scope => scopeLabel(panel, scope)).join(', '))}` : 'Changes nothing that is currently accepted.'}</li>${value.unchanged_scopes?.length ? `<li>${plural(value.unchanged_scopes.length, 'result')} already accepted and unchanged.</li>` : ''}<li>${conflicts.length ? `${plural(conflicts.length, 'conflict')}: your manual edits were kept.<ul>${conflicts.slice(0, 50).map(conflict => `<li>${escapeHtml(scopeLabel(panel, conflict.scope))} · <code>${escapeHtml(conflict.item_id)}</code> · ${escapeHtml(conflict.field)}${conflict.reason ? ` — ${escapeHtml(conflict.reason)}` : ''}</li>`).join('')}${conflicts.length > 50 ? `<li>…and ${number(conflicts.length - 50)} more.</li>` : ''}</ul>` : 'No conflicts with your manual edits.'}</li><li>${value.audio_takes_invalidated ? `${plural(value.audio_takes_invalidated, 'narrated take')} will need re-rendering.` : 'No narrated takes are affected.'}</li><li>${downstream.length ? `Later steps that used the current result will show as stale: ${escapeHtml(downstream.map(step => stepDef(panel, step)?.label || step).join(', '))}.` : 'No later steps are affected.'}</li></ul><div class="ap-actions"><button type="button" class="button subtle" data-ap-action="cancel-accept">Cancel</button><button type="button" class="button primary" data-ap-action="confirm-accept" data-ap-key="confirm-accept" ${impact.accepting ? 'disabled' : ''}>${impact.accepting ? 'Saving…' : `Confirm ${verb.toLowerCase()}`}</button></div>`;
    }
    const error = impact.error ? `<p class="ap-error" role="alert">${escapeHtml(impact.error)}</p>${impact.value ? '' : '<div class="ap-actions"><button type="button" class="button subtle" data-ap-action="cancel-accept">Cancel</button><button type="button" class="button primary" data-ap-action="accept">Review the impact again</button></div>'}` : '';
    return `<section class="ap-impact" aria-label="${verb} impact"><h5 tabindex="-1" data-ap-key="impact-heading">${verb} this version?</h5>${body}${error}</section>`;
  }

  function paintResult(panel) {
    const item = versionItem(panel, panel.versionId);
    if (!item || panel.versions?.stepId !== panel.selected) { put(panel, 'result', ''); return; }
    const restore = isRestore(item);
    const working = panel.working || panel.impact?.loading || panel.impact?.accepting;
    const blockedAccept = acceptBlocked(panel);
    const actions = `${canAccept(item) ? `<button type="button" class="button primary" data-ap-action="accept" data-ap-key="accept" ${working || blockedAccept ? 'disabled' : ''} ${blockedAccept ? `title="${escapeHtml(blockedAccept)}"` : ''}>${restore ? 'Restore this version' : 'Accept'}</button>` : ''}${item.state === 'candidate' ? `<button type="button" class="button subtle" data-ap-action="reject" data-ap-key="reject" ${working ? 'disabled' : ''}>Reject</button>` : ''}`;
    const view = panel.view;
    const others = (panel.versions.items || []).filter(other => other.id !== item.id && (other.scope_count || 0) > 0);
    const compare = `<label>Compare with<select name="ap_compare" data-ap-key="compare"><option value="accepted" ${view.compare === 'accepted' ? 'selected' : ''}>Accepted version</option>${others.map(other => `<option value="${escapeHtml(other.id)}" ${view.compare === other.id ? 'selected' : ''}>${escapeHtml(`${VERSION_STATES[other.state] || other.state} · ${versionSource(other)} · ${when(other.created_at)}`)}</option>`).join('')}<option value="none" ${view.compare === 'none' ? 'selected' : ''}>Nothing</option></select></label>`;
    const result = panel.result;
    const scopes = result?.scopes || [];
    const scopeField = `<label>Show<select name="ap_scope" data-ap-key="scope"><option value="">All results</option>${scopes.map(entry => `<option value="${escapeHtml(entry.scope)}" ${view.scope === entry.scope ? 'selected' : ''}>${escapeHtml(scopeLabel(panel, entry.scope))}${entry.accepted ? ' · accepted' : ''}</option>`).join('')}</select></label>`;
    const changedOnly = `<label class="ap-check"><input type="checkbox" name="ap_changed_only" data-ap-key="changed-only" ${view.changedOnly ? 'checked' : ''} ${view.compare === 'none' ? 'disabled' : ''}> Changed only</label>`;
    let table = '';
    if (panel.resultError) table = `<p class="ap-error" role="alert">Could not load these results: ${escapeHtml(panel.resultError)}</p>`;
    else if (!result) table = `<p class="ap-help" role="status">${item.state === 'running' ? 'Results appear when this step finishes.' : 'Loading results…'}</p>`;
    else {
      const columns = Array.isArray(result.columns) ? result.columns : [];
      const rows = Array.isArray(result.rows) ? result.rows : [];
      const diff = result.diff || {};
      const stats = Object.entries(result.stats || {});
      const compared = diff.compared_with ? `<p class="ap-diff-summary">Compared with ${escapeHtml(diff.compared_with === 'accepted' ? 'the accepted version' : 'the chosen version')}: ${number(diff.same)} same · ${number(diff.changed)} changed · ${number(diff.added)} new · ${number(diff.removed)} only in the other version${typeof diff.agreement === 'number' ? ` · ${Math.round(diff.agreement * 100)}% agreement` : ''}.</p>` : view.compare === 'none' ? '' : '<p class="ap-help">Nothing to compare with yet.</p>';
      const start = rows.length ? (result.offset || 0) + 1 : 0;
      const end = (result.offset || 0) + rows.length;
      table = `${stats.length ? `<dl class="ap-stats">${stats.map(([key, value]) => `<div><dt>${escapeHtml(key.replaceAll('_', ' '))}</dt><dd>${escapeHtml(cell(value))}</dd></div>`).join('')}</dl>` : ''}${compared}${rows.length && columns.length ? `<div class="ap-table-wrap ap-result-table"><table><caption class="sr-only">Results of this version</caption><thead><tr><th scope="col">Change</th><th scope="col">Result</th>${columns.map(column => `<th scope="col">${escapeHtml(column.label || column.key)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => {
        const changed = new Set(Array.isArray(row._changed) ? row._changed : []);
        const kind = row._diff === 'added' ? 'added' : row._diff === 'changed' ? 'changed' : '';
        return `<tr class="${kind ? `ap-row-${kind}` : ''}"><td>${kind ? `<span class="ap-chip ${kind === 'added' ? 'accepted' : 'stale'}">${kind === 'added' ? 'New' : 'Changed'}</span>` : ''}</td><td class="ap-scope">${escapeHtml(scopeLabel(panel, row.scope))}</td>${columns.map(column => {
          const value = cell(row[column.key]);
          if (!changed.has(column.key)) return `<td>${escapeHtml(value)}</td>`;
          const before = cell(row._previous?.[column.key]);
          return `<td class="ap-cell-changed" title="${escapeHtml(clip(`Previously: ${before}`, 400))}"><span class="sr-only">Changed. Now: </span>${escapeHtml(value)}<del class="ap-previous"><span class="sr-only">Previously: </span>${escapeHtml(before)}</del></td>`;
        }).join('')}</tr>`;
      }).join('')}</tbody></table></div>` : `<p class="ap-help">${view.changedOnly ? 'No changed rows.' : 'This version has no rows to show.'}</p>`}<div class="ap-pagination"><span>${number(start)}–${number(end)} of ${number(result.total_rows || 0)} rows</span><div><button type="button" class="button subtle" data-ap-action="prev-page" data-ap-key="prev-page" ${panel.resultLoading || !(result.offset > 0) ? 'disabled' : ''}>Previous</button><button type="button" class="button subtle" data-ap-action="next-page" data-ap-key="next-page" ${panel.resultLoading || end >= (result.total_rows || 0) ? 'disabled' : ''}>Next</button></div></div>`;
    }
    put(panel, 'result', `<div class="ap-result-head"><div><h4>${escapeHtml(VERSION_STATES[item.state] || 'Version')} · ${escapeHtml(versionSource(item))}</h4><p class="ap-help">${escapeHtml(when(item.created_at))}${item.state === 'accepted' ? ' · This is the version the book uses.' : restore ? ' · Restoring makes the book use this earlier version again.' : ''}</p>${item.origin !== 'run' ? '<p class="ap-note">Recorded from existing work; producer unknown.</p>' : ''}</div><div class="ap-actions">${actions}</div></div>${impactHtml(panel, item)}<div class="ap-result-filters">${compare}${scopeField}${changedOnly}</div>${table}`);
  }

  // --- loading -----------------------------------------------------------------------
  async function loadDefs(panel) {
    const seq = ++panel.seq.defs;
    try {
      const value = await call('/api/analysis-pipeline');
      if (panel.seq.defs !== seq) return;
      panel.defs = value;
    } catch (error) {
      if (panel.seq.defs === seq) say(panel, `Could not load the analysis steps: ${error.message}`, true);
    }
  }

  async function loadOverview(panel) {
    const bookId = panel.bookId;
    const seq = ++panel.seq.overview;
    try {
      const value = await call(base(panel));
      if (!current(panel, bookId, 'overview', seq)) return;
      panel.overview = value;
    } catch (error) {
      if (current(panel, bookId, 'overview', seq)) say(panel, `Could not load this book's analysis state: ${error.message}`, true);
    }
  }

  async function loadVersions(panel, {forceResult = false} = {}) {
    const bookId = panel.bookId;
    const stepId = panel.selected;
    if (!stepId || !bookId) return;
    const seq = ++panel.seq.versions;
    try {
      const value = await call(`${base(panel)}/steps/${path(stepId)}/versions?limit=50`);
      if (!current(panel, bookId, 'versions', seq) || panel.selected !== stepId) return;
      const items = Array.isArray(value.items) ? value.items : [];
      // Unit progress alone does not change a table: scopes are recorded when a step finishes.
      const key = items.map(item => `${item.id}:${item.state}:${item.status}:${item.accepted_scopes}:${item.scope_count}`).join('|');
      const changed = key !== panel.versionsKey;
      panel.versions = {stepId, items, decisions:value.decisions || []};
      panel.versionsKey = key;
      if (!items.some(item => item.id === panel.versionId)) {
        const chosen = items.find(item => item.state === 'candidate') || items.find(item => ['accepted', 'partly_accepted'].includes(item.state)) || items[0];
        panel.versionId = chosen?.id || null;
        panel.view = freshView();
        panel.result = null; panel.resultError = null; panel.impact = null; panel.seq.impact++;
        forceResult = true;
      }
      if (panel.view.compare !== 'accepted' && panel.view.compare !== 'none' && !items.some(item => item.id === panel.view.compare)) {
        panel.view = freshView();
        forceResult = true;
      }
      paint(panel);
      if (panel.versionId && (forceResult || changed || !panel.result)) await loadResult(panel);
    } catch (error) {
      if (current(panel, bookId, 'versions', seq)) say(panel, `Could not load versions: ${error.message}`, true);
    }
  }

  async function loadResult(panel) {
    const bookId = panel.bookId;
    const stepId = panel.selected;
    const versionId = panel.versionId;
    const seq = ++panel.seq.result;
    if (!versionId) { panel.result = null; paint(panel); return; }
    const view = panel.view;
    const query = new URLSearchParams({compare:view.compare, offset:String(view.offset), limit:String(PAGE_SIZE)});
    if (view.changedOnly && view.compare !== 'none') query.set('changed_only', 'true');
    if (view.scope) query.set('scope', view.scope);
    panel.resultLoading = true;
    paintResult(panel);
    try {
      const value = await call(`${base(panel)}/steps/${path(stepId)}/versions/${path(versionId)}?${query}`);
      if (!current(panel, bookId, 'result', seq) || panel.selected !== stepId || panel.versionId !== versionId) return;
      panel.result = value;
      panel.resultError = null;
    } catch (error) {
      if (!current(panel, bookId, 'result', seq)) return;
      panel.result = null;
      panel.resultError = error.message;
    } finally {
      if (current(panel, bookId, 'result', seq)) { panel.resultLoading = false; paintResult(panel); }
    }
  }

  // Open the step waiting for review, else the first step.
  function pickStep(panel) {
    if (panel.selected && stepDef(panel, panel.selected)) return false;
    const waiting = panel.overview?.steps?.find(step => step.pending_versions);
    panel.selected = waiting?.id || panel.defs?.steps?.[0]?.id || null;
    return true;
  }

  async function initial(panel) {
    paint(panel);
    await Promise.all([loadDefs(panel), loadOverview(panel)]);
    if (!panel.defs) { paint(panel); return; }
    pickStep(panel);
    paint(panel);
    await loadVersions(panel);
    schedule(panel);
  }

  async function refresh(panel, {result = false} = {}) {
    if (!panel.defs) { await initial(panel); return; }
    if (!panel.selected) {
      // A new visit: the overview decides which step to open.
      await loadOverview(panel);
      pickStep(panel);
      paint(panel);
      await loadVersions(panel);
    } else {
      await Promise.all([loadOverview(panel), loadVersions(panel, {forceResult:result})]);
    }
    paint(panel);
    schedule(panel);
  }

  function stopTimer(panel) {
    if (panel.timer) clearTimeout(panel.timer);
    panel.timer = null;
  }

  // Poll only while this book has pipeline work in progress and the tab is visible.
  function schedule(panel) {
    if (!panel.bookId || hidden(panel) || !panel.overview?.active_run) { stopTimer(panel); return; }
    if (panel.timer) return;
    const bookId = panel.bookId;
    panel.timer = setTimeout(() => {
      panel.timer = null;
      if (panel.bookId !== bookId || hidden(panel)) return;
      void refresh(panel);
    }, POLL_MS);
  }

  // --- actions -----------------------------------------------------------------------
  // A preview describes one exact request; any change to it closes the preview.
  function discardPlan(panel, note = 'Settings changed, so the preview was closed. Preview again to see the new estimate.') {
    // A preview being confirmed stays until the server answers, so a failed start is always reported.
    if (!panel.plan || panel.plan.starting) return false;
    panel.seq.plan++;
    panel.plan = null;
    if (note) say(panel, note);
    return true;
  }

  async function saveSettings(panel, stepId, changes) {
    const def = stepDef(panel, stepId);
    if (!def) return;
    const previous = def.settings || {};
    const plain = def.method === 'plain';
    const provider = plain ? 'local' : changes.provider ?? previous.provider;
    const next = {provider, model:needsModel(panel, def, provider) ? changes.model ?? previous.model : null,
      gate:changes.gate ?? previous.gate ?? def.default_gate};
    def.settings = {...previous, ...next};
    const closed = discardPlan(panel);
    paint(panel);
    const seq = (panel.seq.settings[stepId] || 0) + 1;
    panel.seq.settings[stepId] = seq;
    try {
      const saved = await call(`/api/analysis-pipeline/steps/${path(stepId)}/settings`, {method:'PUT', body:next});
      if (panel.seq.settings[stepId] !== seq) return;
      const latest = stepDef(panel, stepId);
      if (latest) latest.settings = saved;
      say(panel, `Saved settings for ${def.label}.${closed ? ' The open preview was closed; preview again for the new estimate.' : ''}`);
    } catch (error) {
      if (panel.seq.settings[stepId] !== seq) return;
      const latest = stepDef(panel, stepId);
      if (latest) latest.settings = previous;
      say(panel, `Could not save settings for ${def.label}: ${error.message}`, true);
    }
    paint(panel);
  }

  function changeProvider(panel, provider) {
    const def = stepDef(panel, panel.selected);
    if (!def || def.method === 'plain') return;
    if (isService(panel, provider)) {
      panel.custom[def.id] = null;
      void saveSettings(panel, def.id, {provider, model:null});
      return;
    }
    const model = defaultModel(panel, def, provider);
    if (!model) {
      panel.custom[def.id] = {open:true, provider, text:'', error:null};
      paint(panel);
      say(panel, `Enter a model ID for ${providerName(provider)} to use it for ${def.label}.`);
      focusRegion(panel, 'custom-model');
      return;
    }
    panel.custom[def.id] = null;
    void saveSettings(panel, def.id, {provider, model});
  }

  function changeModel(panel, value) {
    const def = stepDef(panel, panel.selected);
    if (!def || def.method === 'plain') return;
    const settings = def.settings || {};
    const pending = panel.custom[def.id];
    const provider = pending?.provider || settings.provider;
    if (value === CUSTOM) {
      const known = catalog(panel, provider).some(item => item.id === settings.model);
      panel.custom[def.id] = {open:true, provider, text:known || provider !== settings.provider ? '' : settings.model || '', error:null};
      paint(panel);
      focusRegion(panel, 'custom-model');
      return;
    }
    panel.custom[def.id] = null;
    void saveSettings(panel, def.id, {provider, model:value});
  }

  function saveCustom(panel, text) {
    const def = stepDef(panel, panel.selected);
    if (!def || def.method === 'plain') return;
    const pending = panel.custom[def.id] || {open:true, provider:def.settings?.provider};
    const model = String(text || '').trim();
    if (!MODEL_ID.test(model)) {
      panel.custom[def.id] = {...pending, open:true, text:model, error:'Use letters, numbers, dots, dashes, underscores or colons, starting with a letter or number (up to 200 characters).'};
      paint(panel);
      return;
    }
    const provider = pending.provider || def.settings?.provider;
    panel.custom[def.id] = {...pending, open:true, text:model, error:null};
    // Enter and the following blur both report the value; save it once.
    if (provider === def.settings?.provider && model === def.settings?.model) { paint(panel); return; }
    void saveSettings(panel, def.id, {provider, model});
  }

  async function preparePlan(panel, stepIds) {
    const reason = blocked(panel);
    if (reason) { say(panel, reason, true); return; }
    const defs = stepIds.map(id => stepDef(panel, id)).filter(Boolean);
    if (!defs.length) { say(panel, 'Tick at least one step to run.', true); return; }
    const unmet = unmetFor(panel, defs[0].id);
    if (unmet) { say(panel, unmet, true); return; }
    const noModel = defs.find(def => needsModel(panel, def, def.settings?.provider) && !(typeof def.settings?.model === 'string' && MODEL_ID.test(def.settings.model)));
    if (noModel) { say(panel, `Choose a model for ${noModel.label} first.`, true); return; }
    const body = {steps:defs.map(def => def.id), configs:{}, fresh:Boolean(panel.run.fresh)};
    const gates = {};
    for (const def of defs) {
      body.configs[def.id] = def.method === 'plain' ? {provider:'local', model:null}
        : {provider:def.settings.provider, model:needsModel(panel, def, def.settings.provider) ? def.settings.model : null};
      gates[def.id] = def.settings?.gate === 'review' ? 'review' : 'auto';
    }
    if (panel.chapterId && defs.some(def => def.chapter_scoped)) body.chapter_ids = [panel.chapterId];
    const bookId = panel.bookId;
    const seq = ++panel.seq.plan;
    panel.plan = {body, gates, value:null, loading:true, error:null, starting:false, stale:false};
    say(panel, '');
    paint(panel);
    focusRegion(panel, 'plan-heading');
    try {
      const value = await call(`${base(panel)}/plan`, {method:'POST', body});
      if (!current(panel, bookId, 'plan', seq)) return;
      panel.plan.value = value;
    } catch (error) {
      if (current(panel, bookId, 'plan', seq)) panel.plan.error = `Could not estimate this run: ${error.message}`;
    } finally {
      if (current(panel, bookId, 'plan', seq)) { panel.plan.loading = false; paint(panel); focusRegion(panel, panel.plan.value ? 'confirm-run' : 'plan-heading'); }
    }
  }

  async function confirmRun(panel) {
    const plan = panel.plan;
    if (!plan?.value || plan.loading || plan.starting) return;
    const problem = blocked(panel);
    const missing = missingKeys(panel, plan.body.configs);
    if (problem || missing.length) { plan.error = problem || `Add in Providers & settings first: ${missing.join(', ')}.`; paint(panel); return; }
    const body = {...plan.body, gates:plan.gates, mode:'serial',
      concurrency:Number(panel.run.concurrency) || 1, expected_fingerprint:plan.value.fingerprint};
    const bookId = panel.bookId;
    const seq = panel.seq.plan;
    plan.starting = true;
    plan.error = null;
    paint(panel);
    let result;
    try {
      result = await call(`${base(panel)}/runs`, {method:'POST', body});
    } catch (error) {
      if (panel.bookId !== bookId || panel.seq.plan !== seq || !panel.plan) return;
      panel.plan.starting = false;
      if (error.code === 'plan_stale') { panel.plan.value = null; panel.plan.stale = true; panel.plan.error = error.message; }
      else panel.plan.error = `Could not start the run: ${error.message}`;
      paint(panel);
      return;
    }
    if (panel.bookId !== bookId) return;
    if (panel.seq.plan === seq) { panel.seq.plan++; panel.plan = null; }
    if (panel.overview && result?.run) panel.overview = {...panel.overview, active_run:result.run};
    say(panel, 'Run started. Progress shows in the job banner and in each step.');
    paint(panel);
    try { await panel.options.onJobStarted?.(result?.job); } catch { /* The job banner catches up on its next poll. */ }
    if (panel.bookId === bookId && panel.shown) await refresh(panel);
  }

  async function previewAccept(panel) {
    const item = versionItem(panel, panel.versionId);
    if (!canAccept(item) || acceptBlocked(panel)) return;
    const bookId = panel.bookId;
    const stepId = panel.selected;
    const seq = ++panel.seq.impact;
    panel.impact = {stepId, versionId:item.id, restore:isRestore(item), value:null, loading:true, error:null, accepting:false};
    paintResult(panel);
    try {
      const value = await call(`${base(panel)}/steps/${path(stepId)}/versions/${path(item.id)}/preview`, {method:'POST', body:{}});
      if (!current(panel, bookId, 'impact', seq)) return;
      panel.impact.value = value;
    } catch (error) {
      if (current(panel, bookId, 'impact', seq)) panel.impact.error = `Could not check what this would change: ${error.message}`;
    } finally {
      if (current(panel, bookId, 'impact', seq)) { panel.impact.loading = false; paintResult(panel); focusRegion(panel, 'impact-heading'); }
    }
  }

  async function confirmAccept(panel) {
    const impact = panel.impact;
    if (!impact?.value || impact.accepting || impact.loading) return;
    const bookId = panel.bookId;
    const seq = panel.seq.impact;
    impact.accepting = true;
    impact.error = null;
    paintResult(panel);
    try {
      await call(`${base(panel)}/steps/${path(impact.stepId)}/versions/${path(impact.versionId)}/accept`,
        {method:'POST', body:{expected_revision:impact.value.revision}});
    } catch (error) {
      if (panel.bookId !== bookId || panel.seq.impact !== seq || !panel.impact) return;
      // Any failure (including a newer book revision) requires a fresh impact preview.
      Object.assign(panel.impact, {accepting:false, value:null, error:`Nothing was changed: ${error.message}`});
      paintResult(panel);
      return;
    }
    if (panel.bookId !== bookId) return;
    if (panel.seq.impact === seq) { panel.seq.impact++; panel.impact = null; }
    say(panel, impact.restore ? 'Restored. The book now uses this version.' : 'Accepted. The book now uses this version.');
    try { await panel.options.onBookChanged?.(); } catch (error) { say(panel, `Saved, but the book could not be reloaded: ${error.message}`, true); }
    if (panel.bookId === bookId && panel.shown) await refresh(panel, {result:true});
  }

  async function reject(panel) {
    const item = versionItem(panel, panel.versionId);
    if (!item || item.state !== 'candidate' || panel.working) return;
    const bookId = panel.bookId;
    panel.working = 'reject';
    paintResult(panel);
    try {
      await call(`${base(panel)}/steps/${path(panel.selected)}/versions/${path(item.id)}/reject`, {method:'POST', body:{}});
      if (panel.bookId !== bookId) return;
      say(panel, 'Rejected. The version stays in history and can still be accepted later.');
    } catch (error) {
      if (panel.bookId === bookId) say(panel, `Could not reject this version: ${error.message}`, true);
    } finally {
      if (panel.bookId === bookId) { panel.working = null; if (panel.shown) await refresh(panel, {result:true}); }
    }
  }

  function selectStep(panel, stepId) {
    if (!stepDef(panel, stepId) || stepId === panel.selected) return;
    // Messages and a preview belong to the step they were made for.
    discardPlan(panel, null);
    panel.message = '';
    panel.messageError = false;
    panel.selected = stepId;
    panel.seq.versions++; panel.seq.result++; panel.seq.impact++;
    Object.assign(panel, {versions:null, versionsKey:'', versionId:null, result:null, resultError:null, impact:null, view:freshView()});
    paint(panel);
    void loadVersions(panel);
  }

  function selectVersion(panel, versionId) {
    if (!versionItem(panel, versionId) || versionId === panel.versionId) return;
    panel.versionId = versionId;
    panel.seq.impact++;
    Object.assign(panel, {impact:null, result:null, resultError:null, view:freshView()});
    paint(panel);
    void loadResult(panel);
  }

  function reloadResult(panel, changes) {
    Object.assign(panel.view, changes);
    panel.seq.impact++;
    panel.impact = null;
    void loadResult(panel);
  }

  function onChange(panel, field) {
    const name = field?.name;
    if (!name?.startsWith('ap_')) return;
    const run = panel.run;
    switch (name) {
      case 'ap_provider': changeProvider(panel, field.value); return;
      case 'ap_model': changeModel(panel, field.value); return;
      case 'ap_custom_model': saveCustom(panel, field.value); return;
      case 'ap_gate': if (['auto', 'review'].includes(field.value)) void saveSettings(panel, panel.selected, {gate:field.value}); return;
      case 'ap_chapter':
        panel.chapterId = chapters(panel).some(chapter => chapter.id === field.value) ? field.value : '';
        discardPlan(panel);
        paint(panel);
        return;
      case 'ap_concurrency': run.concurrency = ['1', '2', '3', '4'].includes(String(field.value)) ? String(field.value) : '2'; break;
      case 'ap_fresh':
        run.fresh = Boolean(field.checked);
        // Fresh samples change which results are reused, and so the estimate.
        discardPlan(panel, 'Fresh samples changed, so the preview was closed. Preview again to see the new estimate.');
        paint(panel);
        return;
      case 'ap_compare': {
        const value = field.value === 'none' || field.value === 'accepted' || versionItem(panel, field.value) ? field.value : 'accepted';
        reloadResult(panel, {compare:value, offset:0, changedOnly:value === 'none' ? false : panel.view.changedOnly});
        return;
      }
      case 'ap_changed_only': reloadResult(panel, {changedOnly:Boolean(field.checked), offset:0}); return;
      case 'ap_scope': reloadResult(panel, {scope:String(field.value || ''), offset:0}); return;
      default: return;
    }
    paintDetail(panel); paintPlan(panel);
  }

  function onInput(panel, field) {
    const name = field?.name;
    // Typing updates state only; repainting the field would move the caret.
    if (name === 'ap_custom_model' && panel.custom[panel.selected]) panel.custom[panel.selected].text = String(field.value ?? '');
  }

  function bind(panel) {
    const {container} = panel;
    container.addEventListener('click', event => {
      const target = event.target;
      if (!target?.closest) return;
      const step = target.closest('[data-ap-step]');
      if (step) {
        selectStep(panel, step.dataset.apStep);
        // "Go to" links live in the detail region they replace; move focus to the new step.
        if (step.dataset.apKey?.startsWith('go-')) focusRegion(panel, 'detail-heading');
        return;
      }
      const version = target.closest('[data-ap-version]');
      if (version) { selectVersion(panel, version.dataset.apVersion); return; }
      const button = target.closest('[data-ap-action]');
      if (!button || button.disabled) return;
      switch (button.dataset.apAction) {
        case 'plan-step': void preparePlan(panel, [panel.selected]); break;
        case 'replan': if (panel.plan) void preparePlan(panel, panel.plan.body.steps); break;
        case 'confirm-run': void confirmRun(panel); break;
        case 'cancel-plan': panel.seq.plan++; panel.plan = null; paint(panel); break;
        case 'accept': void previewAccept(panel); break;
        case 'confirm-accept': void confirmAccept(panel); break;
        case 'cancel-accept': panel.seq.impact++; panel.impact = null; paintResult(panel); break;
        case 'reject': void reject(panel); break;
        case 'prev-page':
          if (!panel.resultLoading && panel.view.offset > 0) { panel.view.offset = Math.max(0, panel.view.offset - PAGE_SIZE); void loadResult(panel); }
          break;
        case 'next-page':
          if (!panel.resultLoading && panel.view.offset + PAGE_SIZE < (panel.result?.total_rows || 0)) { panel.view.offset += PAGE_SIZE; void loadResult(panel); }
          break;
        case 'refresh': void refresh(panel, {result:true}); break;
        case 'dismiss-run': {
          const last = panel.overview?.recent_runs?.[0];
          if (last) acknowledged.set(panel.bookId, last.id);
          paintRuns(panel);
          focusRegion(panel, 'refresh');
          break;
        }
        default: break;
      }
    });
    container.addEventListener('change', event => onChange(panel, event.target));
    container.addEventListener('input', event => onInput(panel, event.target));
    // Forms are not used, but Enter in the custom model field should save rather than do nothing.
    container.addEventListener('keydown', event => {
      if (event.key === 'Enter' && event.target?.name === 'ap_custom_model') { event.preventDefault?.(); saveCustom(panel, event.target.value); }
    });
  }

  function render(container, book, options = {}) {
    if (!container) return Promise.resolve();
    let panel = panels.get(container);
    if (!panel) { panel = create(container); panels.set(container, panel); bind(panel); }
    const status = options.status || null;
    const statusChanged = panel.status !== status;
    panel.options = options;
    panel.status = status;
    if (!book) {
      resetBook(panel);
      Object.assign(panel, {book:null, bookId:null, revision:null, busy:false, shell:false, html:{}});
      container.innerHTML = '';
      return Promise.resolve();
    }
    const fresh = panel.bookId !== book.id;
    const changed = !fresh && panel.revision !== book.revision;
    const busy = Boolean(options.busy);
    const busyChanged = panel.busy !== busy;
    if (fresh) { resetBook(panel); panel.bookId = book.id; }
    panel.book = book;
    panel.revision = book.revision;
    panel.busy = busy;
    if (changed || busyChanged) panel.dirty = true;
    if (hidden(panel)) { leave(panel); return Promise.resolve(); }
    panel.shown = true;
    if (!panel.shell) paintShell(panel);
    if (!panel.loaded) { panel.loaded = true; panel.dirty = false; return initial(panel); }
    if (panel.dirty) { panel.dirty = false; return refresh(panel, {result:changed}); }
    if (statusChanged) paint(panel);
    schedule(panel);
    return Promise.resolve();
  }

  window.BardicAnalysisPipeline = {render};
})();
