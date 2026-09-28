/* Read-only resource accounting (a diagnostics surface); never starts processing or
   provider requests. Escaping and money use the BardicUI kit. */
(() => {
  'use strict';
  const panels = new WeakMap();
  const UI = window.BardicUI;
  const escape = UI.esc;
  const numeric = value => typeof value === 'number' && Number.isFinite(value);
  const number = value => numeric(value) ? value.toLocaleString(undefined, {maximumFractionDigits:2}) : 'Unknown';
  const money = value => UI.fmt.money(value, {precise:true, unknown:'Unknown'});
  const seconds = value => numeric(value) ? (value < 60 ? `${number(value)} s` : `${number(value / 60)} min`) : 'Unknown';
  const bytes = value => !numeric(value) ? 'Unknown' : value < 1024 ? `${number(value)} B` : value < 1048576 ? `${number(value / 1024)} KB` : `${number(value / 1048576)} MB`;
  const name = value => String(value || 'Unrecorded').replaceAll('_', ' ');
  const stageName = value => ({import:'Import & structure', census:'Name & dialogue count', discovery:'Character search', profile:'Character profiles', directing:'Scene direction', narration:'Full-cast recording', simple_listen:'One-narrator listening', search:'Source search', export:'Export', structure:'Structure repair'}[value] || name(value));
  const measurement = (row, key, format = number) => `${escape(format(row[key]))}${row['unknown_' + key + '_operations'] ? `<small class="resources-unknown">${number(row['unknown_' + key + '_operations'])} unmeasured</small>` : ''}`;

  function paint(panel) {
    const data = panel.data, node = panel.container.querySelector('[data-resource-content]');
    if (!node || !data) return;
    const t = data.totals || {}, operations = data.operations || [];
    node.innerHTML = `<div class="resources-cards"><div><span>Requests to services</span><strong>${escape(number(t.requests))}</strong><small>${escape(number(t.cached_operations))} saved results reused${t.unknown_request_count_operations ? ` · ${number(t.unknown_request_count_operations)} request counts unknown` : ''}</small></div><div><span>Estimated cost</span><strong>${measurement(t, 'estimated_cost_usd', money)}</strong><small>${t.reserved_cost_usd ? `${escape(money(t.reserved_cost_usd))} held for requests not yet settled` : 'Recorded estimates; your bill may differ'}</small></div><div><span>Recorded elapsed time</span><strong>${measurement(t, 'elapsed_seconds', seconds)}</strong><small>Sum of measured operations</small></div><div><span>Local Python CPU time</span><strong>${measurement(t, 'cpu_seconds', seconds)}</strong><small>Current thread only; excludes subprocesses</small></div></div>
      ${data.unmeasured_runs ? `<p class="resources-note">${escape(UI.fmt.plural(data.unmeasured_runs, 'run'))} without resource measurements. Their usage is unknown.</p>` : ''}
      <div class="resources-table-wrap"><table><caption>Resource use by step</caption><thead><tr><th>Step</th><th>Requests / reuse</th><th>Elapsed / CPU</th><th>Input / output tokens</th><th>Cached input tokens</th><th>Estimated cost</th></tr></thead><tbody>${(data.stages || []).map(row => `<tr><th scope="row">${escape(stageName(row.id))}<small>${number(row.operations)} recorded operations${row.failed_operations ? ` · ${number(row.failed_operations)} failed or rejected` : ''}</small></th><td>${escape(number(row.requests))} / ${escape(number(row.cached_operations))}</td><td>${measurement(row,'elapsed_seconds', seconds)}<small>CPU ${measurement(row,'cpu_seconds', seconds)}</small></td><td>${measurement(row,'input_tokens')}<small>Output ${measurement(row,'output_tokens')}</small></td><td>${measurement(row,'cached_input_tokens')}</td><td>${measurement(row,'estimated_cost_usd', money)}</td></tr>`).join('') || '<tr><td colspan="6">Nothing has been measured yet.</td></tr>'}</tbody></table></div>
      <p class="resources-note">Audio referenced: ${measurement(t,'audio_seconds', seconds)} · Output file bytes referenced: ${measurement(t,'output_bytes', bytes)}. Reused audio can be referenced more than once; this is not disk occupancy.</p>
      <details class="resources-records"><summary>Individual operations and requests</summary><div class="resources-table-wrap"><table><thead><tr><th>Step / model</th><th>Outcome</th><th>Elapsed / CPU</th><th>Input / output / cached tokens</th><th>Estimated cost</th><th>Output</th></tr></thead><tbody>${operations.map(row => `<tr><th scope="row">${escape(stageName(row.stage))}<small>${escape(row.provider || 'Not recorded')}${row.model ? ` · ${escape(row.model)}` : ''}</small><details><summary>Identifiers</summary><code>Run: ${escape(row.run_id || 'Not recorded')}<br>Chapter: ${escape(row.chapter_id || 'Not recorded')}<br>Unit: ${escape(row.unit_key || 'Not recorded')}<br>Record: ${escape(row.id)}</code></details></th><td>${row.cached ? 'Saved result reused' : escape(name(row.status))}${row.validation_state && row.validation_state !== 'unknown' ? `<small>Validation: ${escape(name(row.validation_state))}</small>` : ''}${row.http_status ? `<small>HTTP ${escape(row.http_status)}</small>` : ''}</td><td>${escape(seconds(row.elapsed_seconds))}<small>CPU ${escape(seconds(row.cpu_seconds))}</small></td><td>${escape(number(row.input_tokens))} / ${escape(number(row.output_tokens))} / ${escape(number(row.cached_input_tokens))}</td><td>${escape(money(row.estimated_cost_usd))}<small>${escape(name(row.cost_basis))}</small></td><td>${escape(bytes(row.output_bytes))}<small>${escape(seconds(row.audio_seconds))} audio</small></td></tr>`).join('') || '<tr><td colspan="6">No records for this selection.</td></tr>'}</tbody></table></div><div class="resources-pagination"><button class="button subtle" type="button" data-resource-action="previous" ${panel.offset === 0 ? 'disabled' : ''}>Previous</button><span>${data.total_operations ? `${number(panel.offset + 1)}–${number(panel.offset + operations.length)} of ${number(data.total_operations)}` : 'No recorded operations'}</span><button class="button subtle" type="button" data-resource-action="next" ${panel.offset + operations.length >= data.total_operations ? 'disabled' : ''}>Next</button></div></details>
      <details class="resources-notes"><summary>What these measurements mean</summary><ul>${(data.notes || []).map(note => `<li>${escape(note)}</li>`).join('')}</ul><p>Unknown values are not zero. Cached input is part of input tokens, not additional input. No account balance is inferred.</p></details>`;
    const select = panel.container.querySelector('[data-resource-run]');
    if (select) select.innerHTML = `<option value="">All recorded work for this book</option>${[...panel.runs.values()].map(run => `<option value="${escape(run.id)}" ${panel.runId === run.id ? 'selected' : ''}>${escape(name(run.kind || 'Run'))} · ${escape(run.created_at || run.id)} · ${escape(name(run.status))}</option>`).join('')}`;
    controls(panel);
  }

  function controls(panel) {
    for (const action of ['refresh','previous','next']) {
      const button = panel.container.querySelector(`[data-resource-action="${action}"]`);
      if (button) button.disabled = panel.loading || (action === 'previous' && panel.offset === 0) || (action === 'next' && panel.offset + panel.limit >= (panel.data?.total_operations || 0));
    }
  }

  async function load(panel) {
    if (panel.loading || !panel.book) return;
    const version = ++panel.version, bookId = panel.book.id;
    panel.loading = true; panel.lastFetch = Date.now(); controls(panel);
    const query = new URLSearchParams({scope:'book', limit:String(panel.limit), offset:String(panel.offset)});
    if (panel.runId) query.set('run_id',panel.runId);
    try {
      const response = await fetch(`/api/books/${encodeURIComponent(bookId)}/resources?${query}`, {method:'GET', credentials:'same-origin'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (panel.version !== version || panel.book?.id !== bookId) return;
      panel.data = data;
      for (const run of data.runs || []) panel.runs.set(run.id, run);
      paint(panel);
      panel.container.querySelector('[data-resource-message]').textContent = '';
    } catch (error) {
      if (panel.version === version && panel.book?.id === bookId) panel.container.querySelector('[data-resource-message]').textContent = `Could not load resource measurements: ${error.message}`;
    } finally {
      if (panel.version === version) { panel.loading = false; controls(panel); }
    }
  }

  function render(container, book, options = {}) {
    if (!container) return Promise.resolve();
    let panel = panels.get(container);
    if (!panel) {
      panel = {container, book:null, version:0, loading:false, busy:false, revision:null, data:null, runs:new Map(), runId:'', offset:0, limit:30, lastFetch:0};
      panels.set(container, panel);
      container.addEventListener('click', event => {
        const button = event.target.closest('[data-resource-action]');
        if (!button || button.disabled || panel.loading) return;
        const action = button.dataset.resourceAction;
        if (action === 'next') panel.offset += panel.limit;
        else if (action === 'previous') panel.offset = Math.max(0, panel.offset - panel.limit);
        else if (action !== 'refresh') return;
        void load(panel);
      });
      container.addEventListener('change', event => {
        if (!event.target.matches('[data-resource-run]')) return;
        panel.runId = event.target.value; panel.offset = 0;
        panel.version++; panel.loading = false;
        void load(panel);
      });
    }
    if (!book) { panel.version++; panel.book = null; panel.loading = false; container.innerHTML = ''; return Promise.resolve(); }
    const fresh = panel.book?.id !== book.id, changed = panel.revision !== book.revision, finished = panel.busy && !options.busy;
    panel.book = book; panel.revision = book.revision; panel.busy = Boolean(options.busy);
    if (fresh) {
      Object.assign(panel, {data:null, runs:new Map(), runId:'', offset:0, loading:false}); panel.version++;
      container.innerHTML = '<details class="resources-panel" open><summary>Resource use <span>Time, tokens and estimated cost</span></summary><div class="resources-body"><div class="resources-toolbar"><label>Show<select data-resource-run><option>All recorded work for this book</option></select></label><button class="button subtle" type="button" data-resource-action="refresh">Refresh measurements</button></div><p data-resource-message class="resources-note" role="status">Loading recorded measurements…</p><div data-resource-content></div></div></details>';
      return load(panel);
    }
    if (changed || finished || panel.busy && Date.now() - panel.lastFetch >= 3000) {
      if (changed || finished) { panel.version++; panel.loading = false; }
      return load(panel);
    }
    return Promise.resolve();
  }
  window.BardicResources = {render};
})();
