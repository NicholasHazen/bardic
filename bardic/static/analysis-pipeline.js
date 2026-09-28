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
  // The one escape helper lives in ui.js (loaded first); every value below goes through it.
  const ui = () => window.BardicUI;
  const escapeHtml = value => ui().esc(value);
  const path = value => encodeURIComponent(value);
  const MODEL_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;
  const CUSTOM = '__custom__';
  const PAGE_SIZE = 200;
  const POLL_MS = 2000;
  // Filters read every row, fetched in pages of the server's largest size, up to this many pages.
  const ALL_ROWS_PAGE = 1000;
  const ALL_ROWS_PAGES = 20;
  // Speaker attributions below 0.65 stay unassigned, and a BookNLP disagreement caps confidence at 0.65
  // (docs/ANALYSIS-PIPELINE.md): "low" is at or below that floor, so assigned-but-doubtful lines show too.
  const LOW_CONFIDENCE = .65;
  // After this step's run the script has speakers: the run summary hands off to Cast and Script & record.
  const HANDOFF_STEP = 'directing';
  // Below this width the step list stacks above the step panel (analysis-pipeline.css).
  const NARROW = '(max-width: 900px)';
  const PRESET_VERSION = 1;
  const PROBLEM_KEYS = new Set(['failed', 'interrupted', 'cancelled', 'budget_limited']);
  const TIERS = [['economy', 'Economy · quick and inexpensive'], ['balanced', 'Balanced'], ['deep', 'Deep · most thorough'], ['other', 'Other models']];
  // Visible names only: "Set aside" is the reject decision, which keeps the version in history.
  const VERSION_STATES = {running:'Running', candidate:'Waiting for review', accepted:'Accepted', partly_accepted:'Partly accepted',
    rejected:'Set aside', superseded:'Superseded', same_as_accepted:'Same as accepted', empty:'No results'};
  const RUN_STATUS = {queued:'Queued', running:'Running', completed:'Completed', failed:'Failed', cancelled:'Cancelled',
    interrupted:'Interrupted', budget_limited:'Allowance reached', skipped:'Skipped'};
  const PROVIDERS = {local:'Local', gemini:'Gemini', openai:'OpenAI', anthropic:'Anthropic', local_llm:'Local LLM',
    booknlp:'BookNLP', novel_analyzer:'Novel Analyzer'};
  const PROBLEM_STATUS = new Set(['failed', 'cancelled', 'interrupted', 'budget_limited']);
  const number = value => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('en-US') : '—';
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  // Money: always two decimals; a positive amount below a cent never reads as $0.00 or $0.0000.
  // An unknown amount stays unknown: it is never shown as $0.
  function formatMoney(value, unknown = 'Unknown price') {
    if (!finite(value)) return unknown;
    if (value > 0 && value < .005) return 'under $0.01';
    return `$${value.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})}`;
  }
  // Prices per million tokens keep a third decimal ($0.075) rather than rounding it away.
  const formatRate = value => finite(value) ? `$${value.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:3})}` : 'unknown';
  // A 0–1 fraction (confidence, agreement) as a whole percentage.
  const formatPercent = value => finite(value) ? `${Math.round(value * 100)}%` : '—';
  const money = value => formatMoney(value);
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
      const hint = {api_key_missing:' Add it in Providers & settings.', server_url_missing:' Add it in Providers & settings.'}[value?.code] || '';
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
  const freshView = () => ({compare:'accepted', changedOnly:false, scope:'', offset:0, filter:''});
  const freshRun = () => ({concurrency:'2', fresh:false});
  // Everything the owner picked or opened during one visit to the tab.
  const visitState = () => ({selected:null, chapterId:'', run:freshRun(), custom:{}, versions:null, versionsKey:'',
    versionId:null, result:null, resultLoading:false, resultError:null, view:freshView(), plan:null, impact:null, working:null,
    message:'', messageError:false, presetForm:null, presetNote:null, presetSaving:false});
  // A step asked for from outside the tab (the lifecycle strip, a #/book/<id>/analysis/<step> link):
  // the next visit opens on it. The panel that last rendered handles a request while it is shown.
  let requestedStep = null;
  let lastPanel = null;
  // Saved step settings from the last successful save, until the app passes a newer status object.
  let presetCache = null;
  // Where Show in text left the results table, so the next visit returns to it.
  let returnTo = null;

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

  // --- one state per step (pure) ---------------------------------------------------------
  // Every step shows exactly one primary state, from these facts alone:
  //   state    the overview entry for the step (null while loading)
  //   missing  required inputs with no accepted result: [{id, label, waiting}] (waiting: a version awaits review)
  //   provider {id, label, what, ready} for a step that calls a provider, else null
  //   running  the step is part of the active run
  // Returns {key, label, tone, detail?, note?, action?, inUse, ready}. `note` is low-emphasis context
  // (staleness), never the state: accepting any profile marks every directing chapter stale.
  const PROBLEM_LABELS = {failed:'Failed', interrupted:'Interrupted', cancelled:'Cancelled', budget_limited:'Stopped at allowance'};
  function stepStatus({state = null, missing = [], provider = null, running = false} = {}) {
    if (!state) return {key:'loading', label:'Loading', tone:'idle', inUse:false, ready:false};
    const latest = state.latest || null;
    const accepted = Number(state.accepted_scopes) || 0;
    const total = Number(state.total_scopes) || 0;
    const inUse = Boolean(state.has_accepted ?? accepted > 0);
    const ready = !missing.length;
    const pending = Number(state.pending_versions) || 0;
    const staleCount = Array.isArray(state.stale_scopes) ? state.stale_scopes.length : 0;
    const needs = joined(missing.map(item => item.label));
    const prereq = !missing.length ? ''
      : missing.every(item => item.waiting) ? `first accept the ${needs} ${missing.length === 1 ? 'version' : 'versions'} waiting for review`
      : `first run ${needs}`;
    const note = staleCount && inUse ? `${plural(staleCount, 'result')} ${staleCount === 1 ? 'was' : 'were'} made before an input changed.` : undefined;
    const setupNeeded = provider && provider.ready === false;
    const setup = setupNeeded ? {kind:'setup', provider:provider.id} : null;
    const goto = missing.length ? {kind:'goto', step:missing[0].id} : null;
    const make = fields => Object.fromEntries(Object.entries({...fields, note, inUse, ready}).filter(([, value]) => value !== undefined));
    if (running || latest?.state === 'running') {
      return make({key:'running', label:latest?.status === 'queued' ? 'Queued' : 'Running', tone:'running', detail:'Progress shows in the job banner.'});
    }
    const problem = latest && PROBLEM_STATUS.has(latest.status) ? latest.status : null;
    if (pending) {
      return make({key:'review', label:pending === 1 ? 'Waiting for review' : `${number(pending)} waiting for review`, tone:'candidate',
        detail:problem ? `The latest run ended early (${PROBLEM_LABELS[problem].toLowerCase()}); its completed results can still be accepted.`
          : 'Check the new version, then accept it or set it aside.',
        action:{kind:'review'}});
    }
    if (problem) {
      const kept = inUse ? 'Earlier accepted results are still in use.' : 'Nothing from this run is in use.';
      const next = !ready ? `To run it again, ${prereq}.` : setupNeeded ? `To run it again, add the ${provider.label} ${provider.what}.`
        : 'Running it again reuses the work that was validated.';
      return make({key:problem, label:PROBLEM_LABELS[problem], tone:problem === 'cancelled' ? 'idle' : problem === 'budget_limited' ? 'stale' : 'failed',
        detail:`${kept} ${next}`, action:goto || setup || {kind:'run'}});
    }
    if (inUse) {
      const origins = state.accepted_origins || {};
      const recorded = (origins.baseline || 0) + (origins.external || 0);
      const onlyRecorded = recorded > 0 && recorded === Object.values(origins).reduce((sum, count) => sum + (Number(count) || 0), 0);
      const partial = total > 0 && accepted < total;
      if (!ready) {
        return make({key:'in-use-earlier', label:'In use (from earlier work)', tone:'accepted', detail:`To re-run, ${prereq}.`, action:goto});
      }
      return make({key:partial ? 'in-use-partial' : 'in-use', label:onlyRecorded ? 'In use (from earlier work)' : partial ? `In use · ${number(accepted)} of ${number(total)}` : 'In use',
        tone:'accepted', detail:partial ? `${number(accepted)} of ${plural(total, 'result')} accepted.` : undefined});
    }
    if (latest?.state === 'empty') return make({key:'empty', label:'Nothing found', tone:'idle', detail:'The last run produced no results.', action:goto || setup || {kind:'run'}});
    if (latest?.state === 'rejected') return make({key:'set-aside', label:'Set aside', tone:'idle', detail:'The last version was set aside; it stays in the history below.', action:goto || setup || {kind:'run'}});
    if (!ready) return make({key:'blocked', label:`Needs ${needs}`, tone:'blocked', detail:`To run it, ${prereq}.`, action:goto});
    if (setupNeeded) return make({key:'needs-setup', label:'Needs setup', tone:'blocked', detail:`Add the ${provider.label} ${provider.what} in Providers & settings to run it.`, action:setup});
    if (latest) return make({key:'not-in-use', label:'Not in use', tone:'idle', detail:'Earlier versions are in the history below.', action:{kind:'run'}});
    return make({key:'not-run', label:'Not run', tone:'idle', action:{kind:'run'}});
  }

  // Something the owner can do now: a version to review, or a step that can run and has nothing in use.
  const actionable = status => status.key === 'review' || (status.ready && !status.inUse && !['running', 'loading'].includes(status.key));

  // The step the tab opens on: a version waiting for review first (it holds up later steps), then the first
  // step whose requirements are met and which has nothing in use, else the first step. Optional steps
  // (the free Prep group) come after required ones. entries: [{id, status, optional?}] in pipeline order.
  function startStep(entries) {
    return (entries.find(entry => entry.status.key === 'review') || entries.find(entry => actionable(entry.status) && !entry.optional)
      || entries.find(entry => actionable(entry.status)) || entries[0])?.id || null;
  }

  // After a run of `afterId` completes: review it if it is waiting, else the next step that reads its
  // results, else the next actionable step (wrapping round). entries: [{id, inputs, status}].
  function nextStep(entries, afterId) {
    const index = entries.findIndex(entry => entry.id === afterId);
    if (index < 0) return null;
    if (entries[index].status.key === 'review') return {id:afterId, review:true};
    const later = entries.slice(index + 1);
    const reader = later.find(entry => (entry.inputs || []).includes(afterId) && entry.status.ready && !['running', 'loading'].includes(entry.status.key));
    if (reader) return {id:reader.id, review:reader.status.key === 'review'};
    const other = [...later, ...entries.slice(0, index)].find(entry => actionable(entry.status));
    return other ? {id:other.id, review:other.status.key === 'review'} : null;
  }

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

  // Whether the step's chosen provider has its key or server URL. Local steps and results read
  // from another step (BookNLP on Speakers & delivery) need none.
  function providerReadiness(panel, def) {
    if (!def || def.method === 'plain') return null;
    const id = panel.custom[def.id]?.provider || def.settings?.provider;
    if (!id || id === 'local' || offline(def, id)) return null;
    return {id, label:providerName(id), what:providerDef(panel, id)?.needs === 'url' ? 'server URL' : 'API key', ready:Boolean(providerHasKey(panel, id))};
  }

  function statusOf(panel, def) {
    if (!panel.overview) return stepStatus();
    const active = panel.overview.active_run;
    return stepStatus({state:stepState(panel, def.id),
      missing:unmetInputs(panel, def).map(id => ({id, label:stepLabel(panel, id), waiting:Boolean(stepState(panel, id)?.pending_versions)})),
      provider:providerReadiness(panel, def),
      running:Boolean(active && ACTIVE.has(active.status) && (active.steps || []).includes(def.id))});
  }

  // Local steps are free and optional preparation: the list groups them as "Prep".
  const isPrep = def => def?.method === 'plain';
  const statusEntries = panel => (panel.defs?.steps || []).map(def => ({id:def.id, inputs:def.inputs || [], optional:isPrep(def), status:statusOf(panel, def)}));

  function missingProviders(panel, configs) {
    return [...new Set(Object.entries(configs).filter(([stepId, config]) => !offline(stepDef(panel, stepId), config.provider))
      .map(([, config]) => config.provider).filter(id => id && id !== 'local'))].filter(id => !providerHasKey(panel, id));
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

  const price = item => {
    const input = item.input_usd_per_million;
    const output = item.output_usd_per_million;
    if (!finite(input) || !finite(output)) return ' · price unknown';
    if (input === 0 && output === 0) return ' · free';
    return ` · ${formatRate(input)} input / ${formatRate(output)} output per million tokens`;
  };

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

  const reducedMotion = () => Boolean(globalThis.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches);
  const narrow = () => Boolean(globalThis.matchMedia?.(NARROW)?.matches);

  // Scroll a region's heading into view and focus it; no smooth scrolling when motion is reduced.
  function reveal(panel, key) {
    const node = panel.container.querySelector(`[data-ap-key="${key}"]`);
    if (!node) return;
    node.scrollIntoView?.({block:'start', behavior:reducedMotion() ? 'auto' : 'smooth'});
    node.focus?.({preventScroll:true});
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
    panel.container.innerHTML = `<div class="ap"><header class="section-head ap-intro"><div class="section-head-text"><h2>Analyze the story</h2><p class="section-head-lead">Each step reads the book, or results you accepted from earlier steps, and records a new version you can accept, compare or restore. Nothing runs until you confirm its estimate.</p></div></header><div class="ap-message" data-ap-region="message" role="status" aria-live="polite"></div><section class="ap-runs" data-ap-region="runs" aria-label="Runs"></section><div class="ap-layout"><nav class="ap-steps" aria-label="Analysis steps" data-ap-region="steps"></nav><div class="ap-main"><section class="ap-detail" data-ap-region="detail" aria-label="Step settings"></section><div data-ap-region="plan"></div><section class="ap-versions" data-ap-region="versions" aria-label="Version history"></section><section class="ap-result" data-ap-region="result" aria-label="Version results"></section></div></div></div>`;
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
    return `<div class="${active ? 'ap-note ap-active-run' : 'ap-last-run'}">${active ? '' : '<button type="button" class="button subtle ap-dismiss" data-ap-action="dismiss-run" data-ap-key="dismiss-run" aria-label="Dismiss the latest run summary">Dismiss</button>'}<strong>${active ? 'Run in progress' : 'Latest run'}</strong> ·<span class="ap-chip ${escapeHtml(run.status === 'completed' ? 'accepted' : PROBLEM_STATUS.has(run.status) ? 'failed' : 'running')}">${escapeHtml(RUN_STATUS[run.status] || run.status || 'Unknown')}</span> · ${escapeHtml(labels)} · ${escapeHtml(when(run.created_at))}${active ? '<p class="ap-help">Cancel it from the job banner above. Validated units are kept and reused.</p>' : ''}${outcomes.length ? `<ul class="ap-outcomes">${outcomes.map(([id, outcome]) => `<li><strong>${escapeHtml(stepDef(panel, id)?.label || id)}</strong>: ${escapeHtml(RUN_STATUS[outcome.status] || outcome.status || '')}${outcome.reason ? ` · ${escapeHtml(outcome.reason)}` : ''}${outcome.error ? ` · ${escapeHtml(outcome.error)}` : ''}</li>`).join('')}</ul>` : ''}${run.error ? `<p class="ap-error">${escapeHtml(run.error)}</p>` : ''}${active ? '' : nextHtml(panel, run) + retryHtml(panel, run)}</div>`;
  }

  // After a completed run: one action that opens the step to do next. Once Speakers & delivery is in use,
  // analysis has done its job: hand off to the cast and the script instead of suggesting more steps.
  function nextHtml(panel, run) {
    if (run.status !== 'completed' || !panel.defs || !panel.overview) return '';
    const last = (run.steps || []).at(-1);
    if (last === HANDOFF_STEP && stepDef(panel, last)) {
      const status = statusOf(panel, stepDef(panel, last));
      if (status.key !== 'review' && status.inUse) return handoffHtml(panel);
    }
    const next = last ? nextStep(statusEntries(panel), last) : null;
    if (!next || next.id === panel.selected) return '';
    const label = stepLabel(panel, next.id);
    return `<div class="ap-next"><button type="button" class="button primary" data-ap-step="${escapeHtml(next.id)}" data-ap-key="go-next">${escapeHtml(next.review ? `Next: review ${label}` : `Next: ${label}`)} →</button></div>`;
  }

  // Links, not buttons: the routes (shell.js) open the tab, and Back returns here.
  function handoffHtml(panel) {
    const route = tab => `#/book/${encodeURIComponent(panel.bookId)}/${tab}`;
    return `<div class="ap-next ap-handoff"><p>${escapeHtml(stepLabel(panel, HANDOFF_STEP))} results are in the book. Next:</p><div class="ap-handoff-actions"><a class="button primary" href="${escapeHtml(route('cast'))}" data-ap-key="handoff-cast">Review the cast →</a><a class="button subtle" href="${escapeHtml(route('studio'))}" data-ap-key="handoff-script">Open the script →</a></div></div>`;
  }

  // The newest run of a step, if it ended early (failed, interrupted, cancelled or stopped at the allowance).
  function problemRun(panel, stepId) {
    const run = (panel.overview?.recent_runs || []).find(item => (item.steps || []).includes(stepId));
    return run && PROBLEM_STATUS.has(run.status) ? run : null;
  }

  // Whether the run summary above already offers Try again for this step (one control, not two).
  function summaryRetries(panel, stepId) {
    const recent = panel.overview?.active_run ? null : panel.overview?.recent_runs?.[0];
    return Boolean(recent) && acknowledged.get(panel.bookId) !== recent.id && PROBLEM_STATUS.has(recent.status) && retryStepOf(panel, recent) === stepId;
  }

  // The step a finished run should retry: the first that did not complete.
  function retryStepOf(panel, run) {
    const steps = (run.steps || []).filter(id => stepDef(panel, id));
    return steps.find(id => run.outcomes?.[id] && run.outcomes[id].status !== 'completed') || steps[0] || null;
  }

  // A run that ended early: Try again opens the usual preview with that run's settings; validated
  // results are reused, so the estimate covers only what is missing. Consent is unchanged.
  function retryHtml(panel, run) {
    if (!PROBLEM_STATUS.has(run.status) || !panel.defs || panel.overview?.active_run) return '';
    const stepId = retryStepOf(panel, run);
    if (!stepId) return '';
    return `<div class="ap-next ap-retry"><button type="button" class="button primary" data-ap-action="retry" data-ap-retry="${escapeHtml(stepId)}" data-ap-run="${escapeHtml(run.id || '')}" data-ap-key="retry-run">Try again</button><p class="ap-help">Opens a new estimate first. Validated results are reused, so only the missing work is counted.</p></div>`;
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
    const ids = plan.body.chapter_ids || [];
    const sections = !ids.length ? 'All story sections' : ids.length === 1 ? scopeLabel(panel, ids[0]) : plural(ids.length, 'section');
    const retry = plan.retry ? `<p class="ap-help" data-ap-retry-note>Trying again with the settings of the ${escapeHtml((PROBLEM_LABELS[plan.retry.status] || 'earlier').toLowerCase())} run${plan.retry.where ? ` (${escapeHtml(plan.retry.where)})` : ''}. Validated results are reused, so only the missing work is estimated.</p>` : '';
    const head = `<h3 tabindex="-1" data-ap-key="plan-heading">Review before running</h3><p class="ap-help">${escapeHtml(labels.join(', '))}${chapterScoped ? ` · Sections: ${escapeHtml(sections)}` : ''}</p>${retry}`;
    let body = '';
    if (plan.loading) body = '<p class="ap-help" role="status">Estimating the work… No requests are sent.</p>';
    else if (plan.value) {
      const value = plan.value;
      const steps = value.steps || [];
      const units = steps.reduce((sum, step) => sum + (step.units || 0), 0);
      const missing = missingKeys(panel, plan.body.configs);
      const setupFor = missingProviders(panel, plan.body.configs)[0];
      // The server's answer wins over the overview, which may be older than the plan.
      const unmet = Object.entries(value.missing_inputs || {}).find(([, inputs]) => inputs?.length);
      const problem = blocked(panel) || (unmet ? unmetText(panel, stepDef(panel, unmet[0]) || {label:unmet[0]}, unmet[1]) : null)
        || (value.missing_inputs ? null : unmetFor(panel, plan.body.steps[0])) || (missing.length ? `Add ${missing.length === 1 ? 'this' : 'these'} first: ${missing.join(', ')}.` : null)
        || (!units ? 'Nothing to run: these steps have no work for the current inputs.' : null);
      const cost = step => step.method === 'plain' || step.estimated_cost_usd === 0 ? 'Free' : money(step.estimated_cost_usd);
      const where = step => step.method === 'plain' ? 'Runs locally · free'
        : isService(panel, step.provider) ? `${escapeHtml(providerName(step.provider))} · your server · free`
        : `${escapeHtml(providerName(step.provider))} · ${escapeHtml(step.model || 'no model')}`;
      // Steps that call your own servers or the Local LLM: free, but they load that machine's GPU.
      const selfHosted = steps.some(step => providerDef(panel, step.provider)?.self_hosted && !offline(stepDef(panel, step.step_id), step.provider));
      const model = steps.some(step => step.method !== 'plain' && !isService(panel, step.provider));
      body = `<div class="ap-table-wrap"><table><caption class="sr-only">Estimated work per step</caption><thead><tr><th scope="col">Step</th><th scope="col">Units</th><th scope="col">Reused</th><th scope="col">Requests</th><th scope="col">Input tokens (est.)</th><th scope="col">Output allowance</th><th scope="col">Estimated cost</th></tr></thead><tbody>${steps.map(step => `<tr><th scope="row">${escapeHtml(step.label || step.step_id)}<small>${where(step)}</small>${step.note ? `<small class="ap-plan-step-note">${escapeHtml(step.note)}</small>` : ''}</th><td>${number(step.units)}</td><td>${number(step.cached_units)}</td><td>${step.service_calls ? escapeHtml(plural(step.service_calls, 'service call')) : number(step.requests)}</td><td>${number(step.estimated_input_tokens)}</td><td>${number(step.output_token_allowance)}</td><td>${escapeHtml(cost(step))}</td></tr>`).join('')}</tbody></table></div><dl class="ap-plan-totals"><div><dt>Model requests</dt><dd>${number(value.requests)}</dd></div>${value.service_calls ? `<div><dt>Calls to your servers</dt><dd>${number(value.service_calls)}</dd></div>` : ''}<div><dt>Reused results</dt><dd>${number(value.cached_units)}</dd></div><div><dt>Input tokens (est.)</dt><dd>${number(value.estimated_input_tokens)}</dd></div><div><dt>Estimated cost</dt><dd>${escapeHtml(value.requests && value.estimated_cost_usd !== 0 ? money(value.estimated_cost_usd) : 'Free')}</dd></div></dl>${value.estimated_cost_usd == null && value.requests ? '<p class="ap-note">A model in this plan has no known price, so the cost cannot be estimated.</p>' : ''}${value.note ? `<p class="ap-help">${escapeHtml(value.note)}</p>` : ''}${selfHosted ? '<p class="ap-note">Your own servers cost nothing per request, but they use that machine’s GPU. If Breeze narration runs there, listening may stall while this runs. Service calls are not counted as model requests.</p>' : ''}${model ? `<p class="ap-help">${escapeHtml(plural(Number(panel.run.concurrency), 'request'))} at once${panel.run.fresh ? ', with fresh samples' : ''}. There is no request or dollar cap. Each unit can take up to four requests (one retry after a transient error, and one evidence repair), so a run can send more requests than estimated. Cancel from the job banner at any time; validated work is kept.</p>` : ''}${problem ? `<p class="ap-error" role="alert">${escapeHtml(problem)}${missing.length && setupFor && problem.startsWith('Add ') ? ` ${setupButton(setupFor, 'setup-plan')}` : ''}</p>` : ''}<div class="ap-actions"><button type="button" class="button subtle" data-ap-action="cancel-plan">Cancel</button><button type="button" class="button primary" data-ap-action="confirm-run" data-ap-key="confirm-run" ${problem || plan.starting ? 'disabled' : ''}>${plan.starting ? 'Starting…' : value.requests ? `Confirm and run · about ${escapeHtml(plural(value.requests, 'request'))}${value.estimated_cost_usd != null ? `, ${escapeHtml(money(value.estimated_cost_usd))}` : ''}` : value.service_calls ? `Confirm and run · ${escapeHtml(plural(value.service_calls, 'call'))} to your servers` : 'Confirm and run locally'}</button></div>`;
    }
    const error = plan.error ? `<p class="ap-error" role="alert">${escapeHtml(plan.error)}${plan.setup ? ` ${setupButton(plan.setup, 'setup-plan-error')}` : ''}</p>${plan.stale ? '<div class="ap-actions"><button type="button" class="button subtle" data-ap-action="cancel-plan">Cancel</button><button type="button" class="button primary" data-ap-action="replan">Preview again</button></div>' : ''}` : '';
    put(panel, 'plan', `<section class="ap-plan" aria-label="Run preview">${head}${body}${error}${!plan.value && !plan.loading && !plan.stale ? '<div class="ap-actions"><button type="button" class="button subtle" data-ap-action="cancel-plan">Close</button></div>' : ''}</section>`);
  }

  // One chip: the step's single state. Where it runs (Local / Model / Service) is a separate, neutral label.
  const statusChip = status => `<span class="ap-chip ${escapeHtml(status.tone)}" data-ap-state="${escapeHtml(status.key)}">${escapeHtml(status.label)}</span>`;
  const kindLabel = (panel, def) => def.method === 'plain' ? 'Local' : isService(panel, def.settings?.provider) ? 'Service'
    : providerDef(panel, def.settings?.provider)?.self_hosted ? 'Your model' : 'Model';

  // Two groups: free local preparation, then the steps that find characters and speakers.
  function paintSteps(panel) {
    if (!panel.defs) { put(panel, 'steps', '<p class="ap-help">Loading steps…</p>'); return; }
    const prep = panel.defs.steps.filter(isPrep);
    const main = panel.defs.steps.filter(def => !isPrep(def));
    const item = (def, index) => {
      const selected = def.id === panel.selected;
      const id = escapeHtml(def.id);
      return `<li class="ap-step${selected ? ' selected' : ''}"><button type="button" data-ap-step="${id}" data-ap-key="step-${id}" aria-current="${selected ? 'true' : 'false'}"><span class="ap-step-name"><span class="ap-step-index" aria-hidden="true">${index}</span>${escapeHtml(def.label)}</span><span class="ap-step-meta"><span class="ap-method ${def.method === 'plain' ? 'plain' : 'llm'}">${kindLabel(panel, def)}</span>${statusChip(statusOf(panel, def))}</span></button></li>`;
    };
    const group = (key, title, defs, offset) => defs.length ? `<section class="ap-step-group" aria-labelledby="ap-group-${key}"><h3 class="ap-step-group-title" id="ap-group-${key}">${escapeHtml(title)}</h3><ol class="ap-step-list" start="${offset + 1}">${defs.map((def, index) => item(def, offset + index + 1)).join('')}</ol></section>` : '';
    put(panel, 'steps', `${group('prep', 'Prep (free, optional)', prep, 0)}${group('story', 'Characters & speakers', main, prep.length)}<p class="ap-help">Steps do not have to run in order. A step that needs another step’s accepted results says so and waits for them; the others can run at any time.</p>`);
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
    // Say what is true: `requires` must be accepted before a run; other inputs are used when present.
    const required = requirements(def);
    const optional = (def.inputs || []).filter(input => !required.includes(input));
    const names = ids => escapeHtml(joined(ids.map(input => stepLabel(panel, input))));
    const inputs = [required.length ? `Needs accepted results from ${names(required)}.` : '',
      optional.length ? `Also uses accepted ${names(optional)} results when there are any.` : '',
      !required.length && !optional.length ? 'Reads the book text.' : ''].filter(Boolean).join(' ');
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
      : `<div class="ap-field-row"><div class="ap-field"><label for="ap-provider-${id}">Provider</label><select id="ap-provider-${id}" name="ap_provider" data-ap-key="provider">${stepProviders(panel, def).map(provider => `<option value="${escapeHtml(provider.id)}" ${provider.id === pendingProvider ? 'selected' : ''}>${escapeHtml(provider.label || providerName(provider.id))}${offline(def, provider.id) ? ' · from Quote attribution' : provider.self_hosted ? ' · your server' : ''}${offline(def, provider.id) || providerHasKey(panel, provider.id) ? '' : ` · ${missingWhat(panel, provider.id)}`}</option>`).join('')}</select></div>${needsModel(panel, def, pendingProvider) ? modelField(panel, def) : ''}</div><p class="ap-help">${offline(def, pendingProvider)
        ? 'Uses the accepted Quote attribution (BookNLP) results for speakers; nothing is sent. Chapters without them fail.'
        : providerDef(panel, pendingProvider)?.self_hosted
        ? 'Book text is sent to your own server. It is free per request, but shares that machine’s GPU (and Breeze narration’s, if it runs there).'
        : 'Book text is sent to the provider you choose.'}</p>`;
    const chapterField = def.chapter_scoped ? `<div class="ap-field"><label for="ap-chapter-${id}">Sections to process</label><select id="ap-chapter-${id}" name="ap_chapter" data-ap-key="chapter"><option value="">All story sections</option>${chapters(panel).map(chapter => `<option value="${escapeHtml(chapter.id)}" ${chapter.id === panel.chapterId ? 'selected' : ''}>${escapeHtml(chapter.title || chapter.id)}${chapter.kind && chapter.kind !== 'chapter' ? ` (${escapeHtml(String(chapter.kind).replaceAll('_', ' '))})` : ''}</option>`).join('')}</select></div>` : '';
    const gate = `<fieldset class="ap-gate"><legend>After a run</legend><label class="ap-check"><input type="radio" name="ap_gate" value="auto" data-ap-key="gate-auto" ${settings.gate !== 'review' ? 'checked' : ''}> Accept automatically</label><label class="ap-check"><input type="radio" name="ap_gate" value="review" data-ap-key="gate-review" ${settings.gate === 'review' ? 'checked' : ''}> Hold for my review</label></fieldset>`;
    const status = statusOf(panel, def);
    // Staleness is context, not a state: list a few sections, count the rest.
    const staleNames = stale.slice(0, 5).map(scope => scopeLabel(panel, scope)).join(', ') + (stale.length > 5 ? `, and ${number(stale.length - 5)} more` : '');
    const notes = [
      state ? `<div class="ap-status" data-ap-status="${escapeHtml(status.key)}">${statusChip(status)}${status.detail && !missing.length ? `<span>${escapeHtml(status.detail)}</span>` : ''}${PROBLEM_KEYS.has(status.key) && status.action?.kind === 'run' && !reason && !summaryRetries(panel, def.id) ? ui().button({label:'Try again', size:'small', attrs:{'data-ap-action':'retry', 'data-ap-retry':def.id, 'data-ap-run':problemRun(panel, def.id)?.id || '', 'data-ap-key':'retry-step'}}) : ''}</div>` : '',
      recorded ? `<p class="ap-help">${plural(recorded, 'accepted result')} ${recorded === 1 ? 'was' : 'were'} recorded from existing work; producer unknown.</p>` : '',
      stale.length ? `<p class="ap-help ap-stale">${plural(stale.length, 'accepted result')} ${stale.length === 1 ? 'was' : 'were'} accepted using inputs that have since changed: ${escapeHtml(staleNames)}. Staleness compares whole steps, so the change may not affect ${stale.length === 1 ? 'it' : 'them'}; run this step again to refresh.</p>` : '',
    ].join('');
    put(panel, 'detail', `<div class="ap-detail-head"><div><span class="eyebrow">${def.method === 'plain' ? 'LOCAL STEP' : 'MODEL STEP'}</span><h3 tabindex="-1" data-ap-key="detail-heading">${escapeHtml(def.label)}</h3></div></div>${notes}${needs}<p>${escapeHtml(def.summary || '')}</p><p class="ap-help">${inputs} Produces ${escapeHtml(scopes)}.</p>${presetsHtml(panel, def)}${method}${chapterField}${gate}${runControls(panel, def, reason, missing)}<details class="ap-technical"><summary>Technical details</summary><dl><div><dt>Step ID</dt><dd><code>${id}</code></dd></div><div><dt>Recipe version</dt><dd>${escapeHtml(def.version)}</dd></div><div><dt>Updates</dt><dd>${def.owns?.length ? def.owns.map(field => `<code>${escapeHtml(field)}</code>`).join(' ') : 'Nothing in the book'}</dd></div><div><dt>Units at once</dt><dd>${escapeHtml(def.parallel)}</dd></div></dl></details>`);
  }

  function runControls(panel, def, reason, missing) {
    const run = panel.run;
    const option = value => `<option value="${value}" ${String(run.concurrency) === String(value) ? 'selected' : ''}>${value}</option>`;
    const provider = providerReadiness(panel, def);
    const noProvider = provider && !provider.ready ? provider : null;
    const disabled = reason || missing.length || noProvider || !panel.overview || panel.plan?.loading || panel.plan?.starting;
    // Local steps have nothing to send or reuse, so only model steps get these options.
    const options = def.method === 'plain' ? '' : `<label>Requests at once<select name="ap_concurrency" data-ap-key="concurrency">${[1, 2, 3, 4].map(option).join('')}</select></label><label class="ap-check"><input type="checkbox" name="ap_fresh" data-ap-key="fresh" ${run.fresh ? 'checked' : ''}> Fresh samples</label>`;
    const help = def.method === 'plain' ? '' : `<p class="ap-help">${run.fresh ? 'Fresh samples request new results even where an identical validated result is saved.' : 'Validated results from earlier identical requests are reused at no cost.'}</p>`;
    // A disabled Run always says why, next to the button.
    const reasons = !panel.overview ? [] : [
      reason ? escapeHtml(reason) : '',
      missing.length ? `Needs accepted results from ${escapeHtml(joined(missing.map(id => stepLabel(panel, id))))} first.` : '',
      noProvider ? `${escapeHtml(noProvider.label)} has no ${escapeHtml(noProvider.what)}. ${setupButton(noProvider.id, 'setup-run')}` : '',
    ].filter(Boolean);
    const why = reasons.length ? `<div class="ap-run-reason" id="ap-run-reason">${reasons.map(text => `<p>${text}</p>`).join('')}</div>` : '';
    return `<div class="ap-run"><div class="ap-run-fields">${options}<button type="button" class="button primary" data-ap-action="plan-step" data-ap-key="plan-step" ${disabled ? 'disabled' : ''} ${why ? 'aria-describedby="ap-run-reason"' : ''}>Run this step</button></div>${why}${help}</div>`;
  }

  const setupButton = (provider, key) => `<button type="button" class="button subtle ap-setup" data-ap-action="setup" data-ap-provider="${escapeHtml(provider)}" data-ap-key="${escapeHtml(key)}">Set up in Providers &amp; settings →</button>`;

  // --- saved step settings (owner-authored presets) -------------------------------------------
  // A saved set is {id, name, step, config:{provider, model, custom_model, gate, concurrency, fresh, chapter_id}, version:1},
  // stored with the app settings (POST /api/settings analysis_step_presets) so every browser sees it. "Custom" is
  // simply the panel as it is: the select shows a saved set only while the panel matches it exactly, and nothing
  // changes until a set is applied or saved.
  const PRESET_KEYS = ['provider', 'model', 'gate', 'concurrency', 'fresh', 'chapter_id'];
  function presetList(panel) {
    const list = presetCache && presetCache.status === panel.status ? presetCache.list : panel.status?.analysis_step_presets;
    return Array.isArray(list) ? list.filter(item => item && typeof item === 'object' && item.config && typeof item.config === 'object') : [];
  }
  const stepPresets = (panel, def) => presetList(panel).filter(item => item.step === def.id);

  // The panel's settings in saved-set form.
  function currentConfig(panel, def) {
    const settings = def.settings || {};
    const withModel = needsModel(panel, def, settings.provider);
    const model = withModel && typeof settings.model === 'string' ? settings.model : null;
    return {provider:settings.provider || null, model, custom_model:Boolean(model) && !catalog(panel, settings.provider).some(item => item.id === model),
      gate:settings.gate === 'review' ? 'review' : 'auto', concurrency:Number(panel.run.concurrency) || 2, fresh:Boolean(panel.run.fresh),
      chapter_id:def.chapter_scoped && panel.chapterId ? panel.chapterId : null};
  }
  const sameConfig = (a, b) => PRESET_KEYS.every(key => (a?.[key] ?? null) === (b?.[key] ?? null));

  // Why a saved set cannot be applied to this step in this book now, or null. Nothing is guessed or substituted.
  function presetProblem(panel, def, preset) {
    const config = preset?.config || {};
    if (preset?.version !== PRESET_VERSION) return {text:'It was saved by a different version of Bardic.'};
    if (preset.step !== def.id) return {text:`It is for ${stepLabel(panel, preset.step)}, not ${def.label}.`};
    if (!stepProviders(panel, def).some(provider => provider.id === config.provider)) return {text:`${providerName(config.provider)} is not offered for ${def.label}.`};
    if (config.provider !== 'local' && !offline(def, config.provider) && !providerHasKey(panel, config.provider)) {
      return {text:`${providerName(config.provider)} has no ${providerDef(panel, config.provider)?.needs === 'url' ? 'server URL' : 'API key'}.`, setup:config.provider};
    }
    if (needsModel(panel, def, config.provider)) {
      if (typeof config.model !== 'string' || !MODEL_ID.test(config.model)) return {text:'It has no valid model.'};
      if (!config.custom_model && !catalog(panel, config.provider).some(item => item.id === config.model)) {
        return {text:`The model “${config.model}” is no longer in the ${providerName(config.provider)} model list.`};
      }
    }
    if (def.chapter_scoped && config.chapter_id && !chapters(panel).some(chapter => chapter.id === config.chapter_id)) {
      return {text:'The section it was saved with is not in this book.'};
    }
    if (config.concurrency !== undefined && ![1, 2, 3, 4].includes(Number(config.concurrency))) return {text:'Its requests at once are out of range.'};
    return null;
  }

  function presetsHtml(panel, def) {
    if (def.method === 'plain' || !panel.overview) return '';
    const id = escapeHtml(def.id);
    const saved = stepPresets(panel, def);
    const current = currentConfig(panel, def);
    const matching = saved.find(item => sameConfig(item.config, current));
    const options = saved.map(item => `<option value="${escapeHtml(item.id)}" ${item === matching ? 'selected' : ''}>${escapeHtml(item.name)}${presetProblem(panel, def, item) ? ' · can’t apply here' : ''}</option>`).join('');
    const form = panel.presetForm?.stepId === def.id ? panel.presetForm : null;
    const note = panel.presetNote?.stepId === def.id ? panel.presetNote : null;
    const canSave = !needsModel(panel, def, current.provider) || Boolean(current.model);
    const select = saved.length ? `<label for="ap-preset-${id}">Saved settings</label><select id="ap-preset-${id}" name="ap_preset" data-ap-key="preset"><option value="" ${matching ? '' : 'selected'}>Custom</option>${options}</select>` : '';
    const actions = form ? '' : `<div class="ap-preset-actions">${ui().button({label:'Save these settings as…', variant:'text', size:'small', disabled:!canSave || panel.presetSaving, attrs:{'data-ap-action':'preset-open', 'data-ap-key':'preset-open'}})}${matching ? ui().button({label:`Remove “${matching.name}”`, variant:'text', size:'small', disabled:panel.presetSaving, attrs:{'data-ap-action':'preset-delete', 'data-ap-preset':matching.id, 'data-ap-key':'preset-delete'}}) : ''}</div>`;
    const formHtml = form ? `<div class="ap-preset-form"><label for="ap-preset-name-${id}">Name these settings</label><input id="ap-preset-name-${id}" name="ap_preset_name" data-ap-key="preset-name" maxlength="60" autocomplete="off" spellcheck="false" placeholder="For example: Quick scan" value="${escapeHtml(form.name || '')}"><p class="ap-help">Saves the provider, model, review choice, requests at once, fresh samples${def.chapter_scoped ? ' and sections' : ''} for ${escapeHtml(def.label)}. A set with the same name is replaced.</p>${form.error ? `<p class="ap-error" role="alert">${escapeHtml(form.error)}</p>` : ''}<div class="ap-actions">${ui().button({label:'Cancel', size:'small', attrs:{'data-ap-action':'preset-cancel'}})}${ui().button({label:'Save', variant:'primary', size:'small', busy:panel.presetSaving, busyLabel:'Saving…', attrs:{'data-ap-action':'preset-save', 'data-ap-key':'preset-save'}})}</div></div>` : '';
    const noteHtml = note ? ui().callout({tone:note.tone || 'warn', title:note.title, text:note.text, actions:note.setup ? setupButton(note.setup, 'setup-preset') : ''}) : '';
    return `<div class="ap-presets"${saved.length || form || note ? '' : ' data-empty'}>${select}${actions}${formHtml}${noteHtml}</div>`;
  }

  // Applying a set saves the step's provider, model and review choice (as picking them by hand does) and sets
  // this visit's requests at once, fresh samples and sections. It never starts work.
  function applyPreset(panel, presetId) {
    const def = stepDef(panel, panel.selected);
    if (!def) return;
    const preset = stepPresets(panel, def).find(item => item.id === presetId);
    if (!preset) { panel.presetNote = null; paint(panel); return; }
    const problem = presetProblem(panel, def, preset);
    if (problem) {
      panel.presetNote = {stepId:def.id, tone:'warn', title:`“${preset.name}” can’t be applied`, text:`${problem.text} Nothing was changed.`, setup:problem.setup};
      paint(panel);
      focusRegion(panel, 'preset');
      return;
    }
    const config = preset.config;
    panel.presetNote = null;
    panel.custom[def.id] = null;
    panel.run.concurrency = String(Number(config.concurrency) || 2);
    panel.run.fresh = Boolean(config.fresh);
    if (def.chapter_scoped) panel.chapterId = config.chapter_id || '';
    const provider = config.provider;
    const model = needsModel(panel, def, provider) ? config.model : null;
    const settings = def.settings || {};
    if (settings.provider === provider && (settings.model ?? null) === model && (settings.gate === 'review' ? 'review' : 'auto') === config.gate) {
      const closed = discardPlan(panel, null);
      paint(panel);
      say(panel, `Applied “${preset.name}”.${closed ? ' The open preview was closed; preview again for the new estimate.' : ''}`);
      return;
    }
    void saveSettings(panel, def.id, {provider, model, gate:config.gate}, {note:`Applied “${preset.name}”.`});
  }

  async function storePresets(panel, list) {
    panel.presetSaving = true;
    paint(panel);
    const status = panel.status;
    try {
      const value = await call('/api/settings', {method:'POST', body:{analysis_step_presets:list}});
      const saved = Array.isArray(value?.analysis_step_presets) ? value.analysis_step_presets : list;
      presetCache = {status, list:saved};
      return saved;
    } finally {
      panel.presetSaving = false;
    }
  }

  const presetId = () => `p_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;

  async function savePreset(panel) {
    const def = stepDef(panel, panel.selected);
    const form = panel.presetForm;
    if (!def || !form || form.stepId !== def.id || panel.presetSaving) return;
    const name = String(form.name || '').replace(/\s+/g, ' ').trim();
    if (!name || name.length > 60) { form.error = 'Use a name of 1–60 characters.'; paint(panel); focusRegion(panel, 'preset-name'); return; }
    const config = currentConfig(panel, def);
    const list = presetList(panel);
    const same = list.find(item => item.step === def.id && String(item.name).toLowerCase() === name.toLowerCase());
    const entry = {id:same?.id || presetId(), name, step:def.id, config, version:PRESET_VERSION};
    const next = same ? list.map(item => item === same ? entry : item) : [...list, entry];
    const bookId = panel.bookId;
    try {
      await storePresets(panel, next);
      if (panel.bookId !== bookId) return;
      panel.presetForm = null;
      panel.presetNote = null;
      paint(panel);
      say(panel, `${same ? 'Replaced' : 'Saved'} “${name}” for ${def.label}. Choose it from Saved settings to use it again.`);
      focusRegion(panel, 'preset');
    } catch (error) {
      if (panel.bookId !== bookId || !panel.presetForm) return;
      panel.presetForm.error = `Could not save: ${error.message}`;
      paint(panel);
    }
  }

  async function deletePreset(panel, id) {
    const def = stepDef(panel, panel.selected);
    const list = presetList(panel);
    const preset = list.find(item => item.id === id);
    if (!def || !preset || panel.presetSaving) return;
    const bookId = panel.bookId;
    try {
      await storePresets(panel, list.filter(item => item.id !== id));
      if (panel.bookId !== bookId) return;
      panel.presetNote = null;
      paint(panel);
      say(panel, `Removed “${preset.name}”. The step’s current settings are unchanged.`);
    } catch (error) {
      if (panel.bookId === bookId) { paint(panel); say(panel, `Could not remove “${preset.name}”: ${error.message}`, true); }
    }
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
      body = `<ul class="ap-impact-list"><li>${changed.length ? `Changes ${plural(changed.length, 'result')}: ${escapeHtml(changed.map(scope => scopeLabel(panel, scope)).join(', '))}` : 'Changes nothing that is currently accepted.'}</li>${value.unchanged_scopes?.length ? `<li>${plural(value.unchanged_scopes.length, 'result')} already accepted and unchanged.</li>` : ''}<li>${conflicts.length ? `Kept your edits (${number(conflicts.length)}): these fields keep the values you set.<ul>${conflicts.slice(0, 50).map(conflict => `<li>${conflictHtml(panel, conflict)}</li>`).join('')}${conflicts.length > 50 ? `<li>…and ${number(conflicts.length - 50)} more.</li>` : ''}</ul>` : 'No conflicts with your manual edits.'}</li><li>${value.audio_takes_invalidated ? `${plural(value.audio_takes_invalidated, 'narrated take')} will need re-rendering.` : 'No narrated takes are affected.'}</li><li>${downstream.length ? `Later steps that used the current result will show as stale: ${escapeHtml(downstream.map(step => stepDef(panel, step)?.label || step).join(', '))}.` : 'No later steps are affected.'}</li></ul><div class="ap-actions"><button type="button" class="button subtle" data-ap-action="cancel-accept">Cancel</button><button type="button" class="button primary" data-ap-action="confirm-accept" data-ap-key="confirm-accept" ${impact.accepting ? 'disabled' : ''}>${impact.accepting ? 'Saving…' : `Confirm ${verb.toLowerCase()}`}</button></div>`;
    }
    const error = impact.error ? `<p class="ap-error" role="alert">${escapeHtml(impact.error)}</p>${impact.value ? '' : '<div class="ap-actions"><button type="button" class="button subtle" data-ap-action="cancel-accept">Cancel</button><button type="button" class="button primary" data-ap-action="accept">Review the impact again</button></div>'}` : '';
    return `<section class="ap-impact" aria-label="${verb} impact"><h5 tabindex="-1" data-ap-key="impact-heading">${verb} this version?</h5>${body}${error}</section>`;
  }

  const FIELD_LABELS = {speaker_id:'speaker', cues:'cues', description:'description', name:'name', aliases:'aliases', priority:'effort'};

  // A kept manual edit, named by what a person recognizes: the character's name or the passage text.
  function conflictHtml(panel, conflict) {
    const book = panel.book || {};
    const character = (book.characters || []).find(item => item.id === conflict.item_id);
    const segment = character ? null : (book.segments || []).find(item => item.id === conflict.item_id);
    const field = conflict.field === 'direction' ? (segment ? 'delivery' : 'voice direction')
      : FIELD_LABELS[conflict.field] || String(conflict.field || 'value').replaceAll('_', ' ');
    const what = character ? escapeHtml(character.name || 'Unnamed character')
      : segment ? `“${escapeHtml(clip(String(segment.text || '').trim(), 140))}”`
      : 'An item no longer in the book';
    const where = scopeLabel(panel, conflict.scope);
    const place = where && where !== character?.name ? ` <small>${escapeHtml(where)}</small>` : '';
    return `<strong>${what}</strong> · your ${escapeHtml(field)}${conflict.reason ? ` — ${escapeHtml(conflict.reason)}` : ''}${place}`;
  }

  // --- result review: filters, evidence and Show in text ---------------------------------------------
  // Each filter needs its column in the step's table (only Speakers & delivery has them all today).
  const FILTERS = [
    {id:'no-speaker', label:'No speaker', column:'speaker', test:(row, ctx) => {
      const speaker = String(row.speaker ?? '').trim();
      return !speaker || speaker.toLowerCase() === 'unassigned' || speaker === ctx.unassigned;
    }},
    {id:'low-confidence', label:'Low confidence', column:'confidence', test:row => finite(row.confidence) && row.confidence <= LOW_CONFIDENCE},
    // The BookNLP check column reads "Differs · BookNLP: <name>" (bardic/pipeline/steps/quotes.py CHECK_LABELS).
    {id:'booknlp-differs', label:'BookNLP disagrees', column:'check', test:row => /^Differs\b/.test(String(row.check ?? ''))},
    {id:'edited', label:'Your edits', column:'edited', test:row => Boolean(String(row.edited ?? '').trim())},
  ];
  const filtersFor = columns => FILTERS.filter(filter => columns.some(column => column.key === filter.column));
  const filterContext = panel => ({unassigned:(panel.book?.characters || []).find(item => item.id === 'unassigned')?.name || null});

  // Passages by ID, rebuilt when the app passes a new book object.
  function segmentIndex(panel) {
    if (panel.segments?.book !== panel.book) {
      panel.segments = {book:panel.book, map:new Map((panel.book?.segments || []).filter(item => item?.id).map(item => [item.id, item]))};
    }
    return panel.segments.map;
  }
  const characterName = (panel, id) => (panel.book?.characters || []).find(item => item.id === id)?.name || id || '';
  const speakerEdited = segment => Array.isArray(segment.manual_fields) && segment.manual_fields.includes('speaker_id');
  const quotesOf = value => Array.isArray(value) ? value.filter(item => typeof item === 'string' && item.trim()) : [];

  // Exact evidence quotes, never reconstructed. A row may carry its version's own quotes (evidence_quotes). Otherwise
  // the book's stored evidence is shown only where it justifies this row's speaker: the passage has that speaker and
  // no one changed it by hand (a manual speaker change keeps the old evidence). Anything else is unknown here.
  function evidenceFor(panel, row, segment) {
    if (Array.isArray(row.evidence_quotes)) return {quotes:quotesOf(row.evidence_quotes), known:true, source:'From this version.'};
    const speaker = characterName(panel, segment.speaker_id);
    if ((segment.evidence === undefined || Array.isArray(segment.evidence)) && !speakerEdited(segment) && speaker && row.speaker !== undefined && speaker === String(row.speaker ?? '')) {
      return {quotes:quotesOf(segment.evidence), known:true, source:`Recorded in the book for ${speaker}.`};
    }
    return {quotes:[], known:false, source:''};
  }

  function evidenceCell(panel, row, segment) {
    if (!segment) return '<td>—</td>';
    const evidence = evidenceFor(panel, row, segment);
    const show = ui().button({label:'Show in text', variant:'text', size:'small', attrs:{'data-ap-action':'show-passage', 'data-ap-segment':row.id, 'data-ap-key':`show-${row.id}`}});
    const body = evidence.quotes.length
      ? `<p class="ap-evidence-source">${escapeHtml(evidence.source)}</p><ul>${evidence.quotes.map(quote => `<li><q>${escapeHtml(quote)}</q></li>`).join('')}</ul>`
      : `<p>${evidence.known ? 'No evidence recorded.' : 'This version’s evidence is not included in this table.'}</p>`;
    if (row.kind === 'narration' && !evidence.quotes.length) return `<td class="ap-passage-cell">${show}</td>`;
    return `<td class="ap-passage-cell">${show}<details class="ap-evidence"><summary>${evidence.quotes.length ? `Evidence (${number(evidence.quotes.length)})` : 'Evidence'}</summary>${body}</details></td>`;
  }

  // The event another view (Script & record) can handle and cancel; shell.js falls back to Read & listen.
  function showPassage(panel, segmentId) {
    const segment = segmentIndex(panel).get(segmentId);
    const doc = globalThis.document;
    if (!segment || typeof doc?.dispatchEvent !== 'function' || typeof globalThis.CustomEvent !== 'function') return;
    // Coming back (Back, or the Analyze tab) reopens this step and version with the same filter, once.
    requestedStep = panel.selected;
    returnTo = {bookId:panel.bookId, step:panel.selected, versionId:panel.versionId, filter:panel.view.filter || ''};
    doc.dispatchEvent(new globalThis.CustomEvent('bardic:show-passage', {cancelable:true,
      detail:{bookId:panel.bookId, segmentId, chapterId:segment.chapter_id || null}}));
  }

  function takeReturnFilter(panel, stepId, versionId) {
    const back = returnTo;
    returnTo = null;
    return back && back.bookId === panel.bookId && back.step === stepId && back.versionId === versionId ? back.filter : '';
  }

  function filtersHtml(panel, columns, result) {
    const available = filtersFor(columns);
    if (!available.length) return '';
    const rows = result?.all ? result.rows || [] : null;
    const ctx = filterContext(panel);
    const count = filter => rows ? ` (${number(rows.filter(row => filter.test(row, ctx)).length)})` : '';
    const options = [{value:'all', label:'All rows'}, ...available.map(filter => ({value:filter.id, label:`${filter.label}${count(filter)}`}))];
    const chips = ui().choice({kind:'chips', label:'Show rows', name:'ap-filter', value:panel.view.filter || 'all', options})
      .replace(/data-value="([\w-]+)"/g, 'data-value="$1" data-ap-filter="$1" data-ap-key="filter-$1"');
    const help = panel.view.filter === 'low-confidence' ? '<p class="ap-help">Confidence of 65% or less. Below 65% a speaker stays unassigned, and a BookNLP disagreement caps confidence at 65%.</p>'
      : panel.view.filter === 'booknlp-differs' ? '<p class="ap-help">Lines where BookNLP’s quote attribution names a different speaker.</p>' : '';
    return `<div class="ap-filters">${chips}${help}</div>`;
  }

  // Choosing a filter reads every row once (in pages); later filter changes reuse them.
  function setFilter(panel, value) {
    const filter = value === 'all' ? '' : FILTERS.some(item => item.id === value) ? value : '';
    if (filter === panel.view.filter) return;
    panel.view.filter = filter;
    panel.view.offset = 0;
    if (panel.result?.all) { paintResult(panel); return; }
    panel.seq.impact++;
    panel.impact = null;
    void loadResult(panel);
  }

  // Rows the pagination can reach: every matching row when rows are paged here, else the server's count.
  function shownTotal(panel) {
    const result = panel.result;
    if (!result) return 0;
    if (!result.all) return result.total_rows || 0;
    const filter = filtersFor(result.columns || []).find(item => item.id === panel.view.filter);
    const rows = result.rows || [];
    return filter ? rows.filter(row => filter.test(row, filterContext(panel))).length : rows.length;
  }
  const pageResult = panel => { if (panel.result?.all) paintResult(panel); else void loadResult(panel); };

  // Confidence columns read as percentages, like the rest of the app.
  const valueText = (column, value) => /confidence/i.test(`${column.key} ${column.label || ''}`) && finite(value) && value >= 0 && value <= 1 ? formatPercent(value) : cell(value);

  // Result rows are grouped under their section (and scene) instead of repeating it in every row.
  // A column that already names the result (Section, Character) replaces the grouping.
  // Rows that are passages (Speakers & delivery) get an Evidence column with Show in text.
  function resultRows(panel, columns, rows) {
    const segments = segmentIndex(panel);
    const passages = rows.some(row => segments.has(row.id));
    const name = row => scopeLabel(panel, row.scope);
    const named = columns.some(column => rows.every(row => cell(row[column.key]) === name(row)));
    const scene = named ? null : columns.find(column => column.key === 'scene');
    // A changed scene stays visible as a column, with its previous value.
    const foldScene = Boolean(scene) && !rows.some(row => Array.isArray(row._changed) && row._changed.includes('scene'));
    const shown = columns.filter(column => !(foldScene && column === scene));
    const group = row => {
      const section = name(row);
      const title = foldScene ? cell(row.scene) : '—';
      if (title === '—') return section;
      return title.startsWith(section) ? title : `${section} · ${title}`;
    };
    let previous = null;
    const body = rows.map(row => {
      const heading = named ? '' : group(row);
      const head = !named && heading !== previous ? `<tr class="ap-group"><th scope="colgroup" colspan="${shown.length + 1 + (passages ? 1 : 0)}">${escapeHtml(heading)}</th></tr>` : '';
      previous = heading;
      const changed = new Set(Array.isArray(row._changed) ? row._changed : []);
      const kind = row._diff === 'added' ? 'added' : row._diff === 'changed' ? 'changed' : '';
      return `${head}<tr class="${kind ? `ap-row-${kind}` : ''}"><td>${kind ? `<span class="ap-chip ${kind === 'added' ? 'accepted' : 'stale'}">${kind === 'added' ? 'New' : 'Changed'}</span>` : ''}</td>${shown.map(column => {
        const value = valueText(column, row[column.key]);
        if (!changed.has(column.key)) return `<td>${escapeHtml(value)}</td>`;
        const before = valueText(column, row._previous?.[column.key]);
        return `<td class="ap-cell-changed" title="${escapeHtml(clip(`Previously: ${before}`, 400))}"><span class="sr-only">Changed. Now: </span>${escapeHtml(value)}<del class="ap-previous"><span class="sr-only">Previously: </span>${escapeHtml(before)}</del></td>`;
      }).join('')}${passages ? evidenceCell(panel, row, segments.get(row.id)) : ''}</tr>`;
    }).join('');
    return `<thead><tr><th scope="col">Change</th>${shown.map(column => `<th scope="col">${escapeHtml(column.label || column.key)}</th>`).join('')}${passages ? '<th scope="col">Evidence</th>' : ''}</tr></thead><tbody>${body}</tbody>`;
  }

  function paintResult(panel) {
    const item = versionItem(panel, panel.versionId);
    if (!item || panel.versions?.stepId !== panel.selected) { put(panel, 'result', ''); return; }
    const restore = isRestore(item);
    const working = panel.working || panel.impact?.loading || panel.impact?.accepting;
    const blockedAccept = acceptBlocked(panel);
    const actions = `${canAccept(item) ? `<button type="button" class="button primary" data-ap-action="accept" data-ap-key="accept" ${working || blockedAccept ? 'disabled' : ''} ${blockedAccept ? `title="${escapeHtml(blockedAccept)}"` : ''}>${restore ? 'Restore' : 'Accept'}</button>` : ''}${item.state === 'candidate' ? `<button type="button" class="button subtle" data-ap-action="reject" data-ap-key="reject" ${working ? 'disabled' : ''} title="Keeps this version in history without using it">Set aside</button>` : ''}`;
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
      const loaded = Array.isArray(result.rows) ? result.rows : [];
      // Every row is loaded while a filter is (or was) on: page in the browser. Otherwise the server pages.
      const local = result.all === true;
      const filter = filtersFor(columns).find(item => item.id === view.filter) || null;
      const ctx = filterContext(panel);
      const matching = local && filter ? loaded.filter(row => filter.test(row, ctx)) : loaded;
      const rows = local ? matching.slice(view.offset, view.offset + PAGE_SIZE) : loaded;
      const offset = local ? view.offset : result.offset || 0;
      const total = local ? matching.length : result.total_rows || 0;
      const diff = result.diff || {};
      const stats = Object.entries(result.stats || {});
      const compared = diff.compared_with ? `<p class="ap-diff-summary">Compared with ${escapeHtml(diff.compared_with === 'accepted' ? 'the accepted version' : 'the chosen version')}: ${number(diff.same)} same · ${number(diff.changed)} changed · ${number(diff.added)} new · ${number(diff.removed)} only in the other version${finite(diff.agreement) ? ` · ${formatPercent(diff.agreement)} agreement` : ''}.</p>` : view.compare === 'none' ? '' : '<p class="ap-help">Nothing to compare with yet.</p>';
      const start = rows.length ? offset + 1 : 0;
      const end = offset + rows.length;
      const counted = filter ? `${number(total)} matching ${total === 1 ? 'row' : 'rows'} (of ${number(result.total_rows || 0)})` : `${number(total)} rows`;
      const partial = local && result.complete === false ? `<p class="ap-help">Filters cover the first ${number(loaded.length)} of ${number(result.total_rows || 0)} rows. Choose one section under Show to filter the rest.</p>` : '';
      const empty = filter ? `No rows match “${filter.label}”.` : view.changedOnly ? 'No changed rows.' : 'This version has no rows to show.';
      table = `${stats.length ? `<dl class="ap-stats">${stats.map(([key, value]) => `<div><dt>${escapeHtml(key.replaceAll('_', ' '))}</dt><dd>${escapeHtml(cell(value))}</dd></div>`).join('')}</dl>` : ''}${compared}${filtersHtml(panel, columns, result)}${partial}${rows.length && columns.length ? `<div class="ap-table-wrap ap-result-table"><table><caption class="sr-only">Results of this version</caption>${resultRows(panel, columns, rows)}</table></div>` : `<p class="ap-help">${escapeHtml(empty)}</p>`}<div class="ap-pagination"><span>${number(start)}–${number(end)} of ${counted}</span><div><button type="button" class="button subtle" data-ap-action="prev-page" data-ap-key="prev-page" ${panel.resultLoading || !(offset > 0) ? 'disabled' : ''}>Previous</button><button type="button" class="button subtle" data-ap-action="next-page" data-ap-key="next-page" ${panel.resultLoading || end >= total ? 'disabled' : ''}>Next</button></div></div>`;
    }
    put(panel, 'result', `<div class="ap-result-head"><div><h4>${escapeHtml(VERSION_STATES[item.state] || 'Version')} · ${escapeHtml(versionSource(item))}</h4><p class="ap-help">${escapeHtml(when(item.created_at))}${item.state === 'accepted' ? ' · This is the version the book uses.' : restore ? ' · Restoring makes the book use this earlier version again.' : ''}</p>${item.origin !== 'run' ? '<p class="ap-help">Recorded from existing work; producer unknown.</p>' : ''}</div><div class="ap-actions">${actions}</div></div>${impactHtml(panel, item)}<div class="ap-result-filters">${compare}${scopeField}${changedOnly}</div>${table}`);
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
        panel.view = {...freshView(), filter:takeReturnFilter(panel, stepId, panel.versionId)};
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
    // A filter reads every row: pages of the largest size the server allows, up to ALL_ROWS_PAGES of them.
    const all = Boolean(view.filter);
    const url = (offset, limit) => {
      const query = new URLSearchParams({compare:view.compare, offset:String(offset), limit:String(limit)});
      if (view.changedOnly && view.compare !== 'none') query.set('changed_only', 'true');
      if (view.scope) query.set('scope', view.scope);
      return `${base(panel)}/steps/${path(stepId)}/versions/${path(versionId)}?${query}`;
    };
    const stillCurrent = () => current(panel, bookId, 'result', seq) && panel.selected === stepId && panel.versionId === versionId;
    panel.resultLoading = true;
    paintResult(panel);
    try {
      let value = await call(all ? url(0, ALL_ROWS_PAGE) : url(view.offset, PAGE_SIZE));
      if (!stillCurrent()) return;
      if (all) {
        const rows = Array.isArray(value.rows) ? [...value.rows] : [];
        const total = Number(value.total_rows) || 0;
        for (let page = 1; rows.length < total && page < ALL_ROWS_PAGES; page++) {
          const more = await call(url(rows.length, ALL_ROWS_PAGE));
          if (!stillCurrent()) return;
          if (!Array.isArray(more.rows) || !more.rows.length) break;
          rows.push(...more.rows);
        }
        value = {...value, rows, offset:0, all:true, complete:rows.length >= total};
      }
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

  // A new visit opens on the first actionable step (see startStep); a step chosen during the visit stays.
  // A step requested from outside the tab wins; it is used once.
  function pickStep(panel) {
    const requested = requestedStep;
    requestedStep = null;
    if (requested && stepDef(panel, requested) && requested !== panel.selected) {
      if (panel.selected) {
        panel.seq.versions++; panel.seq.result++; panel.seq.impact++;
        Object.assign(panel, {versions:null, versionsKey:'', versionId:null, result:null, resultError:null, impact:null, view:freshView()});
      }
      panel.selected = requested;
      return 'requested';
    }
    if (panel.selected && stepDef(panel, panel.selected)) return false;
    panel.selected = startStep(statusEntries(panel));
    return true;
  }

  async function initial(panel) {
    paint(panel);
    await Promise.all([loadDefs(panel), loadOverview(panel)]);
    if (!panel.defs) { paint(panel); return; }
    const picked = pickStep(panel);
    paint(panel);
    if (picked === 'requested') reveal(panel, 'detail-heading');
    await loadVersions(panel);
    schedule(panel);
  }

  async function refresh(panel, {result = false} = {}) {
    if (!panel.defs) { await initial(panel); return; }
    if (!panel.selected || (requestedStep && requestedStep !== panel.selected)) {
      // A new visit: the overview decides which step to open, unless one was asked for.
      await loadOverview(panel);
      const picked = pickStep(panel);
      paint(panel);
      if (picked === 'requested') reveal(panel, 'detail-heading');
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

  async function saveSettings(panel, stepId, changes, {note} = {}) {
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
      say(panel, `${note || `Saved settings for ${def.label}.`}${closed ? ' The open preview was closed; preview again for the new estimate.' : ''}`);
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

  // overrides (Try again): {configs:{step:{provider, model}}, chapterIds, gates, retry:{status, where}} replace the
  // panel's settings for this one preview; the server validates the plan exactly as for any other run.
  async function preparePlan(panel, stepIds, overrides = {}) {
    const reason = blocked(panel);
    if (reason) { say(panel, reason, true); return; }
    const defs = stepIds.map(id => stepDef(panel, id)).filter(Boolean);
    if (!defs.length) { say(panel, 'Tick at least one step to run.', true); return; }
    const unmet = unmetFor(panel, defs[0].id);
    if (unmet) { say(panel, unmet, true); return; }
    const configOf = def => overrides.configs?.[def.id] || def.settings || {};
    const unready = defs.map(def => {
      const provider = configOf(def).provider;
      if (def.method === 'plain' || !provider || provider === 'local' || offline(def, provider)) return null;
      return providerHasKey(panel, provider) ? null : {label:providerName(provider), what:providerDef(panel, provider)?.needs === 'url' ? 'server URL' : 'API key'};
    }).find(Boolean);
    if (unready) { say(panel, `${unready.label} has no ${unready.what}. Add it in Providers & settings first.`, true); return; }
    const noModel = defs.find(def => needsModel(panel, def, configOf(def).provider) && !(typeof configOf(def).model === 'string' && MODEL_ID.test(configOf(def).model)));
    if (noModel) { say(panel, `Choose a model for ${noModel.label} first.`, true); return; }
    const body = {steps:defs.map(def => def.id), configs:{}, fresh:Boolean(panel.run.fresh)};
    const gates = {};
    for (const def of defs) {
      const config = configOf(def);
      body.configs[def.id] = def.method === 'plain' ? {provider:'local', model:null}
        : {provider:config.provider, model:needsModel(panel, def, config.provider) ? config.model : null};
      const gate = overrides.gates?.[def.id] ?? def.settings?.gate;
      gates[def.id] = gate === 'review' ? 'review' : 'auto';
    }
    const known = new Set(chapters(panel).map(chapter => chapter.id));
    const chapterIds = Array.isArray(overrides.chapterIds) ? overrides.chapterIds.filter(id => known.has(id)) : null;
    if (chapterIds?.length && defs.some(def => def.chapter_scoped)) body.chapter_ids = chapterIds;
    else if (!chapterIds && panel.chapterId && defs.some(def => def.chapter_scoped)) body.chapter_ids = [panel.chapterId];
    const bookId = panel.bookId;
    const seq = ++panel.seq.plan;
    panel.plan = {body, gates, value:null, loading:true, error:null, starting:false, stale:false, retry:overrides.retry || null};
    say(panel, '');
    paint(panel);
    // The preview opens below the step settings, often below the fold: bring it into view.
    reveal(panel, 'plan-heading');
    try {
      const value = await call(`${base(panel)}/plan`, {method:'POST', body});
      if (!current(panel, bookId, 'plan', seq)) return;
      panel.plan.value = value;
    } catch (error) {
      if (current(panel, bookId, 'plan', seq)) panel.plan.error = `Could not estimate this run: ${error.message}`;
    } finally {
      // Focus stays on the heading: the reader meets the estimate before the Confirm button.
      // The loaded estimate is taller, so a short page can now scroll far enough: bring it into view again,
      // unless the owner has moved on from the heading.
      if (current(panel, bookId, 'plan', seq)) {
        panel.plan.loading = false;
        paint(panel);
        const active = globalThis.document?.activeElement;
        if (!active || active.getAttribute?.('data-ap-key') === 'plan-heading') reveal(panel, 'plan-heading');
      }
    }
  }

  async function confirmRun(panel) {
    const plan = panel.plan;
    if (!plan?.value || plan.loading || plan.starting) return;
    const problem = blocked(panel);
    const missing = missingKeys(panel, plan.body.configs);
    if (problem || missing.length) {
      plan.error = problem || `Add ${missing.length === 1 ? 'this' : 'these'} first: ${missing.join(', ')}.`;
      plan.setup = problem ? null : missingProviders(panel, plan.body.configs)[0] || null;
      paint(panel);
      return;
    }
    const body = {...plan.body, gates:plan.gates, scheduling:'serial',
      concurrency:Number(panel.run.concurrency) || 1, expected_fingerprint:plan.value.fingerprint};
    const bookId = panel.bookId;
    const seq = panel.seq.plan;
    plan.starting = true;
    plan.error = null;
    plan.setup = null;
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
      say(panel, 'Set aside. The version stays in history and can still be accepted later.');
    } catch (error) {
      if (panel.bookId === bookId) say(panel, `Could not set this version aside: ${error.message}`, true);
    } finally {
      if (panel.bookId === bookId) { panel.working = null; if (panel.shown) await refresh(panel, {result:true}); }
    }
  }

  // Opens Providers & settings at this provider's section. The app can pass onOpenSettings; otherwise
  // the sidebar's own Providers & settings button opens the dialog, and its section is expanded here.
  function openSetup(panel, provider) {
    if (typeof panel.options.onOpenSettings === 'function') { panel.options.onOpenSettings(provider); return; }
    const doc = globalThis.document;
    const button = doc?.getElementById?.('settings-button');
    if (!button) { say(panel, 'Open Providers & settings from the sidebar to add it.', true); return; }
    button.click();
    if (!provider) return;
    const selfHosted = providerDef(panel, provider)?.self_hosted;
    const section = doc.getElementById(selfHosted ? 'provider-settings-local-analysis' : `provider-settings-${provider}`);
    if (section) section.open = true;
    const field = selfHosted ? doc.getElementById(`local-service-${provider}`)
      : doc.getElementById(`api-key-${provider}`) || (section ? section.querySelector?.('input[type="password"]') : null);
    field?.focus?.();
  }

  function selectStep(panel, stepId) {
    if (!stepDef(panel, stepId) || stepId === panel.selected) return;
    // Messages and a preview belong to the step they were made for.
    discardPlan(panel, null);
    panel.message = '';
    panel.messageError = false;
    panel.presetForm = null;
    panel.presetNote = null;
    panel.selected = stepId;
    panel.seq.versions++; panel.seq.result++; panel.seq.impact++;
    Object.assign(panel, {versions:null, versionsKey:'', versionId:null, result:null, resultError:null, impact:null, view:freshView()});
    paint(panel);
    void loadVersions(panel);
  }

  // Try again after a run that ended early: the same step, provider, model, sections, review choice, requests at
  // once and fresh samples as that run (or, when the run has aged out of the recent list, as its latest version).
  // It opens the usual preview; Confirm is still the only thing that starts work.
  function retry(panel, stepId, runId) {
    const def = stepDef(panel, stepId);
    if (!def) return;
    if (stepId !== panel.selected) selectStep(panel, stepId);
    const run = (panel.overview?.recent_runs || []).find(item => item.id === runId && (item.steps || []).includes(stepId)) || problemRun(panel, stepId);
    const latest = stepState(panel, stepId)?.latest || null;
    const recorded = run?.configs?.[stepId] || (latest?.provider ? {provider:latest.provider, model:latest.model ?? null} : null);
    const config = recorded && typeof recorded.provider === 'string' && (def.method === 'plain' || stepProviders(panel, def).some(p => p.id === recorded.provider)) ? recorded : null;
    const chapterIds = run ? run.chapter_ids : latest?.chapter_ids;
    const known = new Set(chapters(panel).map(chapter => chapter.id));
    if (Array.isArray(chapterIds) && chapterIds.some(id => !known.has(id))) {
      say(panel, 'The sections that run covered are no longer in this book. Choose sections and run the step again.', true);
      return;
    }
    if (run && [1, 2, 3, 4].includes(Number(run.concurrency))) panel.run.concurrency = String(Number(run.concurrency));
    if (run && typeof run.fresh === 'boolean') panel.run.fresh = run.fresh;
    panel.chapterId = Array.isArray(chapterIds) && chapterIds.length === 1 ? chapterIds[0] : '';
    const where = config && def.method !== 'plain' ? `${providerName(config.provider)}${config.model ? ` · ${config.model}` : ''}` : '';
    void preparePlan(panel, [stepId], {configs:config ? {[stepId]:config} : null, chapterIds:Array.isArray(chapterIds) ? chapterIds : null,
      gates:run?.gates || null, retry:{status:run?.status || latest?.status || null, where, runId:run?.id || null}});
  }

  // Open a step from outside the tab. While the tab is showing it switches at once; otherwise the next visit opens on it.
  function requestStep(stepId) {
    const id = typeof stepId === 'string' ? stepId : '';
    if (!id) return false;
    const panel = lastPanel;
    if (panel?.shown && panel.defs) {
      if (!stepDef(panel, id)) return false;
      selectStep(panel, id);
      reveal(panel, 'detail-heading');
      return true;
    }
    requestedStep = id;
    return true;
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
      case 'ap_preset': applyPreset(panel, String(field.value || '')); return;
      default: return;
    }
    paintDetail(panel); paintPlan(panel);
  }

  function onInput(panel, field) {
    const name = field?.name;
    // Typing updates state only; repainting the field would move the caret.
    if (name === 'ap_custom_model' && panel.custom[panel.selected]) panel.custom[panel.selected].text = String(field.value ?? '');
    if (name === 'ap_preset_name' && panel.presetForm) panel.presetForm.name = String(field.value ?? '');
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
        // On a narrow screen the step panel sits below the list: bring it into view.
        else if (narrow()) reveal(panel, 'detail-heading');
        return;
      }
      const chip = target.closest('[data-ap-filter]');
      if (chip) { if (!chip.disabled) setFilter(panel, chip.dataset.apFilter); return; }
      const version = target.closest('[data-ap-version]');
      if (version) { selectVersion(panel, version.dataset.apVersion); return; }
      const button = target.closest('[data-ap-action]');
      if (!button || button.disabled) return;
      switch (button.dataset.apAction) {
        case 'plan-step': void preparePlan(panel, [panel.selected]); break;
        // A retried preview keeps the failed run's settings when it is estimated again.
        case 'replan': if (panel.plan) void preparePlan(panel, panel.plan.body.steps, panel.plan.retry
          ? {configs:panel.plan.body.configs, chapterIds:panel.plan.body.chapter_ids || null, gates:panel.plan.gates, retry:panel.plan.retry} : {}); break;
        case 'confirm-run': void confirmRun(panel); break;
        case 'cancel-plan': panel.seq.plan++; panel.plan = null; paint(panel); break;
        case 'accept': void previewAccept(panel); break;
        case 'confirm-accept': void confirmAccept(panel); break;
        case 'cancel-accept': panel.seq.impact++; panel.impact = null; paintResult(panel); break;
        case 'reject': void reject(panel); break;
        case 'setup': openSetup(panel, button.dataset.apProvider || providerReadiness(panel, stepDef(panel, panel.selected))?.id); break;
        case 'prev-page':
          if (!panel.resultLoading && panel.view.offset > 0) { panel.view.offset = Math.max(0, panel.view.offset - PAGE_SIZE); pageResult(panel); }
          break;
        case 'next-page':
          if (!panel.resultLoading && panel.view.offset + PAGE_SIZE < shownTotal(panel)) { panel.view.offset += PAGE_SIZE; pageResult(panel); }
          break;
        case 'retry': retry(panel, button.dataset.apRetry, button.dataset.apRun); break;
        case 'show-passage': showPassage(panel, button.dataset.apSegment); break;
        case 'preset-open':
          panel.presetForm = {stepId:panel.selected, name:'', error:null};
          panel.presetNote = null;
          paintDetail(panel);
          focusRegion(panel, 'preset-name');
          break;
        case 'preset-cancel': panel.presetForm = null; paintDetail(panel); focusRegion(panel, 'preset-open'); break;
        case 'preset-save': void savePreset(panel); break;
        case 'preset-delete': void deletePreset(panel, button.dataset.apPreset); break;
        case 'refresh': void refresh(panel, {result:true}); break;
        case 'dismiss-run': {
          const last = panel.overview?.recent_runs?.[0];
          if (last) acknowledged.set(panel.bookId, last.id);
          paintRuns(panel);
          paintDetail(panel);
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
      if (event.key === 'Enter' && event.target?.name === 'ap_preset_name') {
        event.preventDefault?.();
        if (panel.presetForm) panel.presetForm.name = String(event.target.value ?? '');
        void savePreset(panel);
      }
      if (event.key === 'Escape' && event.target?.name === 'ap_preset_name') { panel.presetForm = null; paintDetail(panel); focusRegion(panel, 'preset-open'); }
      // The row filters are one radio group: arrow keys, Home and End move and choose (as BardicUI.bindChoices does).
      const chip = event.target?.closest?.('[data-ap-filter]');
      if (chip) {
        const chips = Array.from(chip.parentElement?.children || []).filter(node => node.dataset?.apFilter);
        const index = ui().nextChoiceIndex(event.key, chips.indexOf(chip), chips.map(node => Boolean(node.disabled)));
        if (index < 0) return;
        event.preventDefault?.();
        const value = chips[index].dataset.apFilter;
        chips[index].focus?.();
        setFilter(panel, value);
        focusRegion(panel, `filter-${value}`);
      }
    });
  }

  function render(container, book, options = {}) {
    if (!container) return Promise.resolve();
    let panel = panels.get(container);
    if (!panel) { panel = create(container); panels.set(container, panel); bind(panel); }
    lastPanel = panel;
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

  // selectStep(stepId) opens a step from outside the tab (lifecycle strip, #/book/<id>/analysis/<step>); it returns
  // false for a step the loaded registry does not have. The pure helpers are exported for tests and other views.
  window.BardicAnalysisPipeline = {render, selectStep:requestStep, stepStatus, startStep, nextStep, formatMoney, formatRate, formatPercent,
    FILTERS:FILTERS.map(({id, label, column}) => ({id, label, column})), LOW_CONFIDENCE};
})();
