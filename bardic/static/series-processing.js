/* Series processing is explicit: preview first, bounded scans, ordered interpretation. */
(() => {
  'use strict';
  const panels = new WeakMap();
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const path = id => encodeURIComponent(id);
  const count = value => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('en-US') : '—';
  const active = run => ['queued','running'].includes(run.status);
  const live = panel => panels.get(panel.container) === panel;
  const base = panel => `/api/series/${path(panel.series.id)}`;
  const labels = {queued:'Queued',running:'Running',completed:'Complete',failed:'Failed',cancelled:'Cancelled',
    interrupted:'Interrupted',budget_limited:'Allowance reached',scan:'Character discovery',profiles:'Character profiles',direct:'Scene direction',full:'Full analysis'};
  const label = value => labels[value] || String(value || 'Unknown').replaceAll('_',' ');
  // Server details describe the condition; where to fix it is the UI's business, keyed on the error code.
  const HINTS = {api_key_missing:'Add one in Settings.', series_archived:'Restore it from Removed items in the library.'};

  async function request(url, body) {
    const response = await fetch(url, body === undefined ? {headers:{Accept:'application/json'}} :
      {method:'POST',headers:{Accept:'application/json','Content-Type':'application/json'},body:JSON.stringify(body)});
    let value;
    try { value = await response.json(); } catch { value = null; }
    if (!response.ok) {
      const hint = HINTS[value?.code] ? ` ${HINTS[value.code]}` : '';
      throw new Error(typeof value?.detail === 'string' ? value.detail + hint : `Could not process the series request (${response.status}).`);
    }
    if (!value || typeof value !== 'object') throw new Error('The server returned an unreadable series response. Try again.');
    return value;
  }
  function values(panel) {
    const value = Object.fromEntries(new FormData(panel.form));
    return {provider:value.provider,phase:value.phase,concurrency:Number(value.concurrency),
      limits:{max_requests:Number(value.max_requests),max_input_tokens:1000000,max_output_tokens:100000,
              budget_usd:value.budget_usd.trim() === '' ? null : Number(value.budget_usd)}};
  }
  function context(series, options) {
    return JSON.stringify([series.id,series.updated_at,series.books,series.volumes,
      options.status?.analysis_models_by_provider,options.status?.preprocess_models_by_provider]);
  }
  function display(panel, text, error = false) {
    if (!live(panel)) return;
    const node = panel.container.querySelector('[data-series-processing-message]');
    node.textContent = text;
    node.classList.toggle('error',error);
    node.setAttribute('role',error ? 'alert' : 'status');
  }
  function controls(panel) {
    if (!live(panel)) return;
    panel.form.querySelectorAll('input,select,button[type="submit"]').forEach(node => {node.disabled = panel.starting;});
    panel.start.disabled = panel.starting || panel.planning || !panel.preview?.books?.length || !panel.preview?.plan_fingerprint;
  }
  function invalidate(panel, message = '') {
    panel.planVersion++;
    panel.preview = null;
    panel.body = null;
    panel.planning = false;
    panel.container.querySelector('[data-series-plan]').innerHTML = '';
    controls(panel);
    if (message) display(panel,message);
  }
  function rememberTitles(panel, books) {
    for (const book of books || []) if ((book.book_id || book.id) && book.title) panel.titles.set(book.book_id || book.id,book.title);
  }
  function paintRuns(panel, result) {
    rememberTitles(panel,panel.series.books);
    panel.container.querySelector('[data-series-runs]').innerHTML = (result.runs || []).slice(0,5).map(run =>
      `<article><h4>${esc(label(run.phase))} · ${esc(label(run.status))} · ${count(run.progress || 0)}/${count(run.total || 0)} books</h4><p>${esc(run.message)}</p>${run.error ? `<p class="error">${esc(run.error)}</p>` : ''}<ul>${(run.children || []).map(child => `<li>${esc(child.title || panel.titles.get(child.book_id) || child.book_id)} · ${esc(label(child.phase))} · ${esc(label(child.status))} — ${esc(child.message)}</li>`).join('')}</ul>${active(run) ? `<button type="button" class="button subtle" data-series-cancel="${esc(run.id)}" ${panel.cancelling.has(run.id) ? 'disabled' : ''}>${panel.cancelling.has(run.id) ? 'Stopping…' : 'Stop series run'}</button>` : ''}</article>`).join('') || '<p>No series runs yet.</p>';
  }
  async function refresh(panel) {
    if (!live(panel)) return;
    const version = ++panel.runsVersion;
    clearTimeout(panel.timer);
    panel.timer = null;
    try {
      const result = await request(base(panel)+'/runs');
      if (!live(panel) || version !== panel.runsVersion) return;
      panel.runs = result;
      paintRuns(panel,result);
      if ((result.runs || []).some(active)) panel.timer = setTimeout(()=>refresh(panel),2500);
    } catch (error) {
      if (live(panel) && version === panel.runsVersion) {
        display(panel,error.message,true);
        // Keep observing an already known active run after a temporary read failure.
        if ((panel.runs?.runs || []).some(active)) panel.timer = setTimeout(()=>refresh(panel),5000);
      }
    }
  }
  function paintPlan(panel, result, body) {
    const books = result.books || [];
    const estimate = typeof result.estimated_cost_usd === 'number' && Number.isFinite(result.estimated_cost_usd)
      ? '$'+result.estimated_cost_usd.toFixed(4)+' estimated' : 'Cost estimate unavailable';
    panel.container.querySelector('[data-series-plan]').innerHTML = `<p><strong>${count(books.length)} supplied books · ${count(result.requests)} currently known requests · ${estimate}</strong></p><p>${count(body.limits.max_requests*books.length)} maximum requests across this run. ${body.limits.budget_usd == null ? 'No dollar guard.' : '$'+(body.limits.budget_usd*books.length).toFixed(2)+' combined book allowances (includes prior tracked spend).'}</p><p>Discovery model: ${esc(result.scan_model || 'Not reported')} · Profile and direction model: ${esc(result.model || 'Not reported')}.</p><ol>${books.map(book=>`<li>Position ${esc(book.position)} · ${esc(book.title)} · ${count(book.plan?.requests)} requests</li>`).join('')}</ol>${(result.notes || []).map(note=>`<p>${esc(note)}</p>`).join('')}`;
  }
  async function preview(panel, event) {
    event.preventDefault();
    if (!live(panel) || panel.starting || panel.form.reportValidity?.() === false) return;
    invalidate(panel);
    const version = ++panel.planVersion;
    const body = values(panel);
    const signature = JSON.stringify(body);
    panel.planning = true;
    controls(panel);
    display(panel,'Building a local plan…');
    try {
      const result = await request(base(panel)+'/plan',body);
      if (!live(panel) || version !== panel.planVersion || signature !== JSON.stringify(values(panel))) return;
      if (result.series_id && result.series_id !== panel.series.id) throw new Error('The plan belongs to a different series. Preview this series again.');
      panel.preview = result;
      panel.body = body;
      rememberTitles(panel,result.books);
      paintPlan(panel,result,body);
      if (panel.runs) paintRuns(panel,panel.runs);
      display(panel,result.plan_fingerprint ? 'Plan ready. Review the scope and allowances before starting.' :
        'This plan has no scope fingerprint. Refresh the app and preview again before starting.',!result.plan_fingerprint);
    } catch (error) {
      if (live(panel) && version === panel.planVersion) display(panel,error.message,true);
    } finally {
      if (live(panel) && version === panel.planVersion) {panel.planning = false; controls(panel);}
    }
  }
  async function start(panel) {
    if (!live(panel) || panel.starting || panel.planning || !panel.preview?.books?.length || !panel.preview.plan_fingerprint) return;
    if (JSON.stringify(values(panel)) !== JSON.stringify(panel.body)) {
      invalidate(panel,'The inputs changed. Preview a new plan before starting.');
      return;
    }
    const body = {...panel.body,expected_plan_fingerprint:panel.preview.plan_fingerprint};
    panel.starting = true;
    invalidate(panel);
    display(panel,'Starting the reviewed series plan…');
    controls(panel);
    try {
      const run = await request(base(panel)+'/process',body);
      if (!live(panel)) return;
      display(panel,run.message || 'Series run started.');
      panel.options.onChange?.();
      await refresh(panel);
    } catch (error) {
      if (live(panel)) display(panel,error.message+' Preview again before retrying.',true);
    } finally {
      if (live(panel)) {panel.starting = false; controls(panel);}
    }
  }
  async function showMap(panel) {
    const version = ++panel.mapVersion;
    try {
      const result = await request(base(panel)+'/map');
      if (!live(panel) || version !== panel.mapVersion) return;
      rememberTitles(panel,result.series?.books);
      if (panel.runs) paintRuns(panel,panel.runs);
      const target = panel.container.querySelector('[data-series-map-output]');
      target.innerHTML = '<details open><summary>Supplied volumes, missing titles and confirmed character links</summary><pre></pre></details>';
      target.querySelector('pre').textContent = JSON.stringify(result,null,2);
    } catch (error) { if (live(panel) && version === panel.mapVersion) display(panel,error.message,true); }
  }
  async function cancel(panel, event) {
    const id = event.target.closest('[data-series-cancel]')?.dataset.seriesCancel;
    if (!id || !live(panel) || panel.cancelling.has(id)) return;
    panel.cancelling.add(id);
    if (panel.runs) paintRuns(panel,panel.runs);
    try {
      await request(`/api/jobs/${path(id)}/cancel`,{});
      if (!live(panel)) return;
      await refresh(panel);
      panel.options.onChange?.();
    } catch (error) { if (live(panel)) display(panel,error.message,true); }
    finally {
      if (live(panel)) {panel.cancelling.delete(id); if (panel.runs) paintRuns(panel,panel.runs);}
    }
  }
  function render(container, series, options={}) {
    if (!container) return Promise.resolve();
    let panel = panels.get(container);
    if (!series?.id) {
      if (panel) clearTimeout(panel.timer);
      panels.delete(container);
      container.innerHTML = '';
      return Promise.resolve();
    }
    const signature = context(series,options);
    if (panel?.series.id === series.id) {
      const changed = panel.context !== signature;
      panel.series = series; panel.options = options; panel.context = signature;
      rememberTitles(panel,series.books);
      if (changed) invalidate(panel,'Series membership or model settings changed. Preview a new plan before starting.');
      return refresh(panel);
    }
    if (panel) clearTimeout(panel.timer);
    panel = {container,series,options,context:signature,preview:null,body:null,timer:null,planVersion:0,runsVersion:0,mapVersion:0,
      starting:false,planning:false,titles:new Map(),cancelling:new Set(),runs:null};
    panels.set(container,panel);
    rememberTitles(panel,series.books);
    const provider = options.status?.analysis_provider;
    container.innerHTML = `<section class="series-processing"><h3>Process ${esc(series.name || 'this series')}</h3><p>Scan independent books concurrently, then interpret each supplied volume in reading order. Missing volumes remain gaps; only confirmed character links carry evidence across titles.</p><form><div class="series-processing-fields"><label>Analysis provider<select name="provider" aria-label="Series analysis provider">${['gemini','openai','anthropic'].map(value=>`<option ${value===provider?'selected':''} value="${value}">${value}</option>`).join('')}</select></label><label>Stage<select name="phase" aria-label="Series processing stage"><option value="scan">Discover characters in all supplied books</option><option value="profiles">Build profiles in reading order</option><option value="direct">Direct books in reading order</option><option value="full">Full analysis · scan, profiles, direction</option></select></label><label>Parallel scans<select name="concurrency" aria-label="Parallel discovery scans"><option value="2">2 books at a time</option><option value="1">1 book at a time</option></select></label><label>Requests per book<input name="max_requests" type="number" min="1" max="1000" value="25" required></label><label>Book allowance · USD<input name="budget_usd" type="number" min="0.01" max="1000" step="0.01" value="1"></label></div><p>Allowances are per book, including its earlier tracked analysis spend. Blank dollars uses request/token limits only. Model requests may incur charges; preview does not generate text.</p><div class="control-row"><button type="submit" class="button subtle">Preview series plan</button><button type="button" data-series-start class="button primary" disabled>Start / resume series</button><button type="button" data-series-map class="button subtle">Show series map</button><button type="button" data-series-refresh class="button subtle">Refresh runs</button></div></form><p role="status" data-series-processing-message></p><div data-series-plan></div><div data-series-map-output></div><div data-series-runs></div></section>`;
    panel.form = container.querySelector('form');
    panel.start = container.querySelector('[data-series-start]');
    panel.form.addEventListener('input',()=>invalidate(panel,'Inputs changed. Preview the updated plan.'));
    panel.form.addEventListener('change',()=>invalidate(panel,'Inputs changed. Preview the updated plan.'));
    panel.form.addEventListener('submit',event=>preview(panel,event));
    panel.start.addEventListener('click',()=>start(panel));
    container.querySelector('[data-series-map]').addEventListener('click',()=>showMap(panel));
    container.querySelector('[data-series-refresh]').addEventListener('click',()=>refresh(panel));
    container.querySelector('[data-series-runs]').addEventListener('click',event=>cancel(panel,event));
    return refresh(panel);
  }
  window.BardicSeriesProcessing = {render};
})();
