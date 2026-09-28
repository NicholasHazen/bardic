/* Series runs: choose one analysis step, preview every supplied volume's plan, confirm, then run
   the books one at a time in reading order. Nothing is sent before the consent is confirmed. */
(() => {
  'use strict';
  const ui = () => window.BardicUI;
  const panels = new WeakMap();
  const path = id => encodeURIComponent(id);
  const active = run => ['queued','running'].includes(run.status);
  /** The book a paused series run waits on, with where to review it; null when it is not paused. */
  function reviewWait(run) {
    const wait = active(run) ? run.waiting_for_review : null;
    if (!wait?.book_id) return null;
    const step = (wait.steps || [])[0];
    return {bookId:wait.book_id, title:wait.title || 'this book', steps:wait.steps || [],
      href:`#/book/${path(wait.book_id)}/analysis${step ? `/${path(step)}` : ''}`};
  }
  const live = panel => panels.get(panel.container) === panel;
  const base = panel => `/api/series/${path(panel.series.id)}`;
  const STATES = {queued:'Waiting', running:'Running', completed:'Done', failed:'Failed', cancelled:'Cancelled',
    interrupted:'Interrupted', budget_limited:'Stopped at allowance', quota_limited:'Quota reached',
    not_started:'Not started', needs_review:'Waiting for review'};
  const stateLabel = value => STATES[value] || ui().statusLabel(value);

  async function request(url, body) {
    const response = await fetch(url, body === undefined ? {headers:{Accept:'application/json'}} :
      {method:'POST',headers:{Accept:'application/json','Content-Type':'application/json'},body:JSON.stringify(body)});
    let value;
    try { value = await response.json(); } catch { value = null; }
    if (!response.ok) throw new Error(typeof value?.detail === 'string' ? value.detail : `Could not process the series request (${response.status}).`);
    if (!value || typeof value !== 'object') throw new Error('The server returned an unreadable series response. Try again.');
    return value;
  }

  // ---- Pure helpers (exported for tests) --------------------------------------------------------
  const stepDef = (defs, id) => (defs?.steps || []).find(step => step.id === id) || null;
  const providerDef = (defs, id) => (defs?.providers || []).find(provider => provider.id === id) || null;
  const providerLabel = (defs, id) => providerDef(defs, id)?.label || (id === 'local' ? 'This computer' : String(id || 'Unknown'));
  const isModelStep = def => def?.method === 'llm';
  function kindLabel(defs, def) {
    if (!def) return '';
    if (def.method === 'plain') return 'Local';
    const provider = providerDef(defs, def.settings?.provider);
    return provider?.kind === 'service' ? 'Service' : provider?.self_hosted ? 'Your model' : 'Model';
  }
  /** One state per child book: not started, waiting for review, or its job status. */
  function childState(child) {
    if (child.not_started || (!child.run_id && ['cancelled','interrupted'].includes(child.status))) return 'not_started';
    const outcomes = Object.values(child.run?.outcomes || {});
    if (child.status === 'completed' && outcomes.some(o => o.status === 'completed' && o.scopes > 0 && !o.accepted)) return 'needs_review';
    return child.status;
  }
  /** Consent arguments for a series plan: scope, what is sent where, the estimate and any blocker. */
  function consentFor(plan, defs) {
    const fmt = ui().fmt;
    const books = plan.books || [];
    const stepId = (plan.steps || [])[0];
    const def = stepDef(defs, stepId);
    const labelOf = id => stepDef(defs, id)?.label || id;
    const config = (plan.configs || {})[stepId] || {};
    const service = providerDef(defs, config.provider)?.kind === 'service';
    const local = def?.method === 'plain' || config.provider === 'local';
    const sending = !local && (plan.requests > 0 || plan.service_calls > 0);
    const where = providerLabel(defs, config.provider) + (config.model ? ` · ${config.model}` : '');
    const skipped = (plan.skipped_volumes || []).length;
    const scope = `${fmt.plural(books.length, 'book')} in reading order${skipped ? `; ${fmt.plural(skipped, 'missing or planned volume')} skipped` : ''}`;
    const notes = [];
    const pending = (plan.context_pending_books || []).length;
    const upTo = pending ? plan.up_to || {} : null;
    if (plan.cached_units > 0 && !pending) notes.push(`${fmt.plural(plan.cached_units, 'saved result')} reused.`);
    let estimate;
    if (!sending) estimate = {free:true, note:[...notes, local ? 'Runs on this computer.' : 'Nothing new to request.'].join(' ')};
    else if (service) estimate = {free:true, note:[`${fmt.plural(plan.service_calls, 'call')} to your server.`, ...notes].join(' ')};
    else {
      const unknown = (plan.unknown_cost_books || []).length;
      const known = pending ? upTo.known_cost_usd : plan.known_cost_usd;
      if (unknown) notes.push(`No catalog price for ${config.model || 'this model'}: cost unknown for ${unknown} of ${fmt.plural(books.length, 'book')}`
        + (known > 0 ? `; the priced books come to ${fmt.money(known, {approx:true})}.` : '.'));
      // Later books read what earlier books accept during the run: count their work as new, and say prompts can grow.
      if (pending) notes.push(`Up to ${fmt.plural(upTo.requests, 'request')}. ${pending === 1 ? 'One later book reads' : `${pending} later books read`} the results earlier books accept during this run, so the cost can be higher than this estimate.`);
      notes.push('Retries and evidence repairs can add requests.');
      estimate = pending ? {requests:upTo.requests, cost:upTo.estimated_cost_usd, note:notes.join(' ')}
        : {requests:plan.requests, cost:plan.estimated_cost_usd, note:notes.join(' ')};
    }
    let blocked = null;
    const titles = Object.fromEntries(books.map(book => [book.book_id, book.title]));
    const missing = Object.entries(plan.missing_inputs || {});
    if (!books.length) blocked = {reason:'Add a book to this series before processing it.', setupLabel:null};
    else if (missing.length) {
      const inputs = [...new Set(missing.flatMap(([, steps]) => Object.values(steps).flat()))].map(labelOf);
      blocked = {reason:`${labelOf(stepId)} needs accepted ${inputs.join(' and ')} results in ${missing.map(([id]) => titles[id] || id).join(', ')}. Run ${inputs.join(' and ')} for the series first.`, setupLabel:null};
    } else if ((plan.missing_credentials || []).length) {
      const first = plan.missing_credentials[0];
      const needs = plan.missing_credentials.map(item => `the ${item.label} ${item.needs === 'url' ? 'server URL' : 'API key'}`);
      blocked = {reason:`Add ${needs.join(' and ')} in Providers & settings first.`,
        setupAttrs:{'data-series-setup':first.provider}};
    } else if (!plan.fingerprint) blocked = {reason:'This plan has no fingerprint. Preview it again.', setupLabel:null};
    return {title:`Run ${labelOf(stepId)} on this series`, scope, sends:sending ? [{what:'Text from each book', where}] : [],
      estimate, capNote:sending && !estimate.free ? undefined : null,
      confirm:`Run on ${fmt.plural(books.length, 'book')}`, blocked};
  }

  // ---- Painting ------------------------------------------------------------------------------------
  function display(panel, text, tone) {
    if (!live(panel)) return;
    ui().setMessage(panel.container.querySelector('[data-series-processing-message]'), text, tone ? {tone} : {});
  }
  function controls(panel) {
    if (!live(panel)) return;
    panel.form.querySelectorAll('input,select,button').forEach(node => { node.disabled = panel.starting; });
    panel.previewButton.disabled = panel.starting || panel.planning || !panel.defs;
  }
  function invalidate(panel, message = '') {
    panel.planVersion++;
    panel.preview = null;
    panel.body = null;
    panel.planning = false;
    panel.container.querySelector('[data-series-plan]').innerHTML = '';
    controls(panel);
    if (message) display(panel, message);
  }
  function values(panel) {
    const value = Object.fromEntries(new FormData(panel.form));
    const model = isModelStep(stepDef(panel.defs, value.step));
    return {steps:value.step ? [value.step] : [], fresh:model && value.fresh === 'on', concurrency:model ? Number(value.concurrency) || 2 : 2};
  }
  function paintStep(panel) {
    if (!live(panel)) return;
    const {esc} = ui();
    const id = values(panel).steps[0];
    const def = stepDef(panel.defs, id);
    panel.container.querySelector('[data-series-model-options]').hidden = !isModelStep(def);
    const note = panel.container.querySelector('[data-series-step-note]');
    if (!def) { note.innerHTML = ''; return; }
    const settings = def.settings || {};
    const uses = def.method === 'plain' ? 'Runs on this computer.'
      : `Uses ${esc(providerLabel(panel.defs, settings.provider))}${settings.model ? ` · ${esc(settings.model)}` : ''}. Choose the provider and model in the Analysis tab.`;
    const hold = settings.gate === 'review' ? ' Results are held for your review. A later book that reads a book waits until you review it.' : '';
    note.innerHTML = `${esc(def.summary || '')} ${uses}${hold}`;
  }
  function paintSteps(panel) {
    const {esc} = ui();
    const select = panel.container.querySelector('[data-series-step]');
    const steps = panel.defs?.steps || [];
    select.innerHTML = steps.length ? steps.map(step => `<option value="${esc(step.id)}">${esc(step.label)} · ${esc(kindLabel(panel.defs, step))}</option>`).join('')
      : '<option value="">No analysis steps available</option>';
    paintStep(panel);
  }
  function rememberTitles(panel, books) {
    for (const book of books || []) if ((book.book_id || book.id) && book.title) panel.titles.set(book.book_id || book.id, book.title);
  }
  function paintPlan(panel, plan) {
    const {esc, fmt, consent, badge} = ui();
    const target = panel.container.querySelector('[data-series-plan]');
    const labelOf = id => stepDef(panel.defs, id)?.label || id;
    const rows = (plan.books || []).map(book => {
      const pending = (book.context_pending || []).length > 0;
      const p = pending ? {...book.plan, ...book.up_to, cached_units:0} : book.plan || {};
      const missing = Object.values((plan.missing_inputs || {})[book.book_id] || {}).flat();
      const cost = p.service_calls > 0 && !p.requests ? fmt.plural(p.service_calls, 'server call')
        : p.requests ? `${pending ? 'up to ' : ''}${fmt.plural(p.requests, 'request')} · ${p.estimated_cost_usd === null ? 'cost unknown' : fmt.money(p.estimated_cost_usd, {approx:true})}` : 'nothing new to request';
      return `<li><span class="series-processing-position">${esc(book.position)}</span><span class="series-processing-title">${esc(book.title)}</span><span class="series-processing-detail">${esc(cost)}${p.cached_units ? ` · ${esc(fmt.plural(p.cached_units, 'saved result'))} reused` : ''}</span>${pending ? badge('Reads earlier books', 'info') : ''}${missing.length ? badge(`Needs ${missing.map(labelOf).join(', ')}`, 'warn') : ''}</li>`;
    }).join('');
    target.innerHTML = consent({id:`series-consent-${panel.consentCount = (panel.consentCount || 0) + 1}`, ...consentFor(plan, panel.defs)})
      + (rows ? `<ol class="series-processing-books" aria-label="Books in reading order">${rows}</ol>` : '')
      + `<details class="series-processing-notes"><summary>How series runs work</summary>${(plan.notes || []).map(note => `<p>${esc(note)}</p>`).join('')}</details>`;
    ui().openConsent(target.querySelector('[data-consent]'));
  }
  function paintRuns(panel, result) {
    const {esc, fmt, statusBadge, button, callout} = ui();
    rememberTitles(panel, panel.series.books);
    const labelOf = id => stepDef(panel.defs, id)?.label || id;
    panel.container.querySelector('[data-series-runs]').innerHTML = (result.runs || []).slice(0, 5).map(run => {
      const kids = run.children || [];
      const done = kids.filter(child => child.status === 'completed').length;
      const children = kids.map(child => {
        const state = childState(child);
        return `<li><span class="series-processing-title">${esc(child.title || panel.titles.get(child.book_id) || child.book_id)}</span>${statusBadge(state, stateLabel(state))}<span class="series-processing-detail">${esc(child.message || '')}</span></li>`;
      }).join('');
      const stop = active(run) ? button({label:'Stop series run', busyLabel:'Stopping…', busy:panel.cancelling.has(run.id), size:'small', attrs:{'data-series-cancel':run.id}}) : '';
      const wait = reviewWait(run);
      const title = wait ? panel.titles.get(wait.bookId) || wait.title : '';
      const paused = wait ? callout({tone:'warn', title:`Waiting for your review of ${title}`,
        text:'A later book reads its results. Accept or set aside what waits in its Analyze tab, then resume. Later books only read accepted results.',
        html:`<p><a href="${esc(wait.href)}" data-series-review="${esc(wait.bookId)}">Review ${esc(title)} in Analyze →</a></p>`,
        actions:button({label:'Resume series run', busyLabel:'Resuming…', busy:panel.resuming.has(run.id), variant:'primary', size:'small', attrs:{'data-series-resume':run.id}})}) : '';
      const state = wait ? statusBadge('needs_review', 'Waiting for your review') : statusBadge(run.status, stateLabel(run.status));
      return `<article class="series-processing-run"><header><h4>${esc((run.steps || []).map(labelOf).join(', ') || 'Series run')}</h4>${state}<span class="series-processing-detail">${esc(fmt.number(done, {digits:0}))} of ${esc(fmt.plural(kids.length, 'book'))} done</span></header><p>${esc(run.message || '')}</p>${run.error ? `<p class="series-processing-failure">${esc(run.error)}</p>` : ''}${paused}<ol>${children}</ol>${stop}</article>`;
    }).join('') || '<p class="series-processing-detail">No series runs yet.</p>';
  }

  // ---- Actions -------------------------------------------------------------------------------------
  async function loadDefinitions(panel) {
    try {
      const defs = await request('/api/analysis-pipeline');
      if (!live(panel)) return;
      panel.defs = defs;
      paintSteps(panel);
    } catch (error) {
      if (live(panel)) display(panel, error.message, 'bad');
    } finally {
      if (live(panel)) controls(panel);
    }
  }
  async function refresh(panel) {
    if (!live(panel)) return;
    const version = ++panel.runsVersion;
    clearTimeout(panel.timer);
    panel.timer = null;
    try {
      const result = await request(base(panel) + '/runs');
      if (!live(panel) || version !== panel.runsVersion) return;
      panel.runs = result;
      paintRuns(panel, result);
      if ((result.runs || []).some(active)) panel.timer = setTimeout(() => refresh(panel), 2500);
    } catch (error) {
      if (live(panel) && version === panel.runsVersion) {
        display(panel, error.message, 'bad');
        // Keep observing an already known active run after a temporary read failure.
        if ((panel.runs?.runs || []).some(active)) panel.timer = setTimeout(() => refresh(panel), 5000);
      }
    }
  }
  async function preview(panel, event) {
    event.preventDefault();
    if (!live(panel) || panel.starting || !panel.defs) return;
    invalidate(panel);
    const version = ++panel.planVersion;
    const body = values(panel);
    if (!body.steps.length) { display(panel, 'Choose a step to run.', 'warn'); return; }
    const signature = JSON.stringify(body);
    panel.planning = true;
    controls(panel);
    display(panel, 'Planning every book on this computer. Nothing is sent.');
    try {
      const result = await request(base(panel) + '/plan', {steps:body.steps, fresh:body.fresh});
      if (!live(panel) || version !== panel.planVersion || signature !== JSON.stringify(values(panel))) return;
      if (result.series_id && result.series_id !== panel.series.id) throw new Error('The plan belongs to a different series. Preview this series again.');
      panel.preview = result;
      panel.body = body;
      rememberTitles(panel, result.books);
      paintPlan(panel, result);
      if (panel.runs) paintRuns(panel, panel.runs);
      display(panel, '');
    } catch (error) {
      if (live(panel) && version === panel.planVersion) display(panel, error.message, 'bad');
    } finally {
      if (live(panel) && version === panel.planVersion) { panel.planning = false; controls(panel); }
    }
  }
  async function start(panel) {
    const plan = panel.preview;
    if (!live(panel) || panel.starting || panel.planning || !plan?.books?.length || !plan.fingerprint || consentFor(plan, panel.defs).blocked) return;
    if (JSON.stringify(values(panel)) !== JSON.stringify(panel.body)) {
      invalidate(panel, 'The choices changed. Preview the series again before running.');
      return;
    }
    const body = {...panel.body, expected_fingerprint:plan.fingerprint};
    panel.starting = true;
    invalidate(panel);
    display(panel, 'Starting the confirmed series run…');
    controls(panel);
    try {
      const run = await request(base(panel) + '/process', body);
      if (!live(panel)) return;
      display(panel, run.message || 'Series run started.', 'good');
      panel.options.onChange?.();
      await refresh(panel);
    } catch (error) {
      if (live(panel)) display(panel, `${error.message} Nothing was started; preview again before retrying.`, 'bad');
    } finally {
      if (live(panel)) { panel.starting = false; controls(panel); }
    }
  }
  function openSettings(panel, provider) {
    if (panel.options.onOpenSettings) panel.options.onOpenSettings(provider);
    else if (typeof document !== 'undefined') document.getElementById('settings-button')?.click();
  }
  function planClick(panel, event) {
    const target = event.target;
    if (target.closest?.('[data-consent-confirm]')) void start(panel);
    else if (target.closest?.('[data-consent-cancel]')) invalidate(panel, 'Preview closed. Nothing was started.');
    else {
      const setup = target.closest?.('[data-series-setup]');
      if (setup) openSettings(panel, setup.dataset?.seriesSetup);
    }
  }
  async function showMap(panel) {
    const version = ++panel.mapVersion;
    try {
      const result = await request(base(panel) + '/map');
      if (!live(panel) || version !== panel.mapVersion) return;
      rememberTitles(panel, result.series?.books);
      if (panel.runs) paintRuns(panel, panel.runs);
      const target = panel.container.querySelector('[data-series-map-output]');
      target.innerHTML = '<details open><summary>Supplied volumes, missing titles and confirmed character links</summary><pre></pre></details>';
      target.querySelector('pre').textContent = JSON.stringify(result, null, 2);
    } catch (error) { if (live(panel) && version === panel.mapVersion) display(panel, error.message, 'bad'); }
  }
  async function cancel(panel, event) {
    const id = event.target.closest?.('[data-series-cancel]')?.dataset?.seriesCancel;
    if (!id || !live(panel) || panel.cancelling.has(id)) return;
    panel.cancelling.add(id);
    if (panel.runs) paintRuns(panel, panel.runs);
    try {
      await request(`/api/jobs/${path(id)}/cancel`, {});
      if (!live(panel)) return;
      await refresh(panel);
      panel.options.onChange?.();
    } catch (error) { if (live(panel)) display(panel, error.message, 'bad'); }
    finally {
      if (live(panel)) { panel.cancelling.delete(id); if (panel.runs) paintRuns(panel, panel.runs); }
    }
  }
  async function resume(panel, id) {
    if (!id || !live(panel) || panel.resuming.has(id)) return;
    panel.resuming.add(id);
    if (panel.runs) paintRuns(panel, panel.runs);
    try {
      await request(`${base(panel)}/runs/${path(id)}/resume`, {});
      if (!live(panel)) return;
      display(panel, 'Series run resumed.', 'good');
      panel.options.onChange?.();
    } catch (error) { if (live(panel)) display(panel, error.message, 'bad'); }
    finally {
      if (live(panel)) { panel.resuming.delete(id); await refresh(panel); }
    }
  }
  function runsClick(panel, event) {
    const target = event.target;
    const resumeId = target.closest?.('[data-series-resume]')?.dataset?.seriesResume;
    if (resumeId) return resume(panel, resumeId);
    return cancel(panel, event);
  }
  function context(series, options) {
    return JSON.stringify([series.id, series.updated_at, series.books, series.volumes,
      options.status?.analysis_models_by_provider, options.status?.preprocess_models_by_provider]);
  }
  function render(container, series, options = {}) {
    if (!container) return Promise.resolve();
    let panel = panels.get(container);
    if (!series?.id) {
      if (panel) clearTimeout(panel.timer);
      panels.delete(container);
      container.innerHTML = '';
      return Promise.resolve();
    }
    const signature = context(series, options);
    if (panel?.series.id === series.id) {
      const changed = panel.context !== signature;
      panel.series = series; panel.options = options; panel.context = signature;
      rememberTitles(panel, series.books);
      if (changed) invalidate(panel, 'Series books or model settings changed. Preview again before running.');
      return refresh(panel);
    }
    if (panel) clearTimeout(panel.timer);
    panel = {container, series, options, context:signature, defs:null, preview:null, body:null, timer:null,
      planVersion:0, runsVersion:0, mapVersion:0, starting:false, planning:false, titles:new Map(), cancelling:new Set(), resuming:new Set(), runs:null};
    panels.set(container, panel);
    rememberTitles(panel, series.books);
    const {sectionHead, button, message} = ui();
    container.innerHTML = `<section class="series-processing">${sectionHead({level:3, title:`Process ${series.name || 'this series'}`,
      lead:'Run one analysis step on every supplied volume, one book at a time in reading order. Missing volumes are skipped, and nothing is inferred about them.'})}`
      + `<form class="series-processing-form"><label>Step<select name="step" data-series-step aria-label="Step to run on every book"><option value="">Loading steps…</option></select></label>`
      + `<div class="series-processing-options" data-series-model-options hidden><label>Requests at once<select name="concurrency" aria-label="Model requests at once"><option value="1">1</option><option value="2" selected>2</option><option value="3">3</option><option value="4">4</option></select></label>`
      + `<label class="series-processing-check"><input type="checkbox" name="fresh"> Fresh samples (ignore saved results)</label></div>`
      + `<p class="series-processing-detail" data-series-step-note></p>`
      + `<div class="series-processing-actions">${button({label:'Preview series run', type:'submit', variant:'primary', attrs:{'data-series-preview':''}})}${button({label:'Show series map', attrs:{'data-series-map':''}})}${button({label:'Refresh runs', attrs:{'data-series-refresh':''}})}</div></form>`
      + `${message({attrs:{'data-series-processing-message':''}})}<div data-series-plan></div><div data-series-map-output></div><div class="series-processing-runs" data-series-runs></div></section>`;
    panel.form = container.querySelector('form');
    panel.previewButton = container.querySelector('[data-series-preview]');
    const changed = () => { paintStep(panel); invalidate(panel, panel.preview ? 'Choices changed. Preview again before running.' : ''); };
    panel.form.addEventListener('change', changed);
    panel.form.addEventListener('submit', event => preview(panel, event));
    container.querySelector('[data-series-plan]').addEventListener('click', event => planClick(panel, event));
    container.querySelector('[data-series-map]').addEventListener('click', () => showMap(panel));
    container.querySelector('[data-series-refresh]').addEventListener('click', () => refresh(panel));
    container.querySelector('[data-series-runs]').addEventListener('click', event => runsClick(panel, event));
    controls(panel);
    return Promise.all([loadDefinitions(panel), refresh(panel)]).then(() => undefined);
  }
  window.BardicSeriesProcessing = {render, consentFor, childState, reviewWait};
})();
