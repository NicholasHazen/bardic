/* Progressive analysis controls; previews and local census never invoke a model. */
(() => {
  'use strict';
  const panels = new WeakMap();
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
  const path = value => encodeURIComponent(value);
  const count = value => Number.isFinite(Number(value)) ? Math.round(Number(value)).toLocaleString('en-US') : '—';
  const money = value => typeof value === 'number' && Number.isFinite(value) ? `$${value.toFixed(value < .01 ? 4 : 2)}` : 'Unavailable';
  const defaults = () => ({phase:'scan', max_requests:'25', budget_usd:'1', token_only:false, max_input_tokens:'1000000', max_output_tokens:'100000'});
  const isLocal = panel => panel.options.provider === 'local';
  const optionsKey = options => JSON.stringify([options.provider, options.chapterId, options.scanModel, options.model]);
  const dataKey = book => JSON.stringify([book.id, book.revision ?? 0]);

  async function request(url, body) {
    const options = body === undefined ? {headers:{Accept:'application/json'}} :
      {method:'POST', headers:{Accept:'application/json', 'Content-Type':'application/json'}, body:JSON.stringify(body)};
    const response = await fetch(url, options);
    let data;
    try { data = await response.json(); } catch { data = null; }
    if (!response.ok) {
      let detail = data?.detail;
      if (Array.isArray(detail)) detail = detail.map(item => item.msg || 'Invalid value').join('; ');
      throw new Error(typeof detail === 'string' ? detail : `Request failed (${response.status}). Try again.`);
    }
    return data;
  }

  function message(panel, text, error = false) {
    const node = panel.container.querySelector('[data-production-message]');
    if (!node) return;
    node.textContent = text;
    node.classList.toggle('production-error', error);
    node.setAttribute('role', error ? 'alert' : 'status');
  }

  function controls(panel) {
    const locked = Boolean(panel.options.busy || panel.previewing || panel.starting);
    panel.container.querySelectorAll('[data-production-form] input,[data-production-form] select,[data-production-form] button').forEach(node => { node.disabled = locked; });
    const start = panel.container.querySelector('[data-production-action="start"]');
    if (start) start.disabled = locked || !panel.preview || typeof panel.options.onStart !== 'function';
    const refresh = panel.container.querySelector('[data-production-action="census"]');
    if (refresh) refresh.disabled = panel.loading;
    const dollar = panel.container.querySelector('[name="budget_usd"]');
    if (dollar) dollar.disabled = locked || panel.values.token_only;
    panel.container.setAttribute('aria-busy', String(panel.loading || panel.previewing || panel.starting));
  }

  function phaseText(panel) {
    const title = panel.book.chapters?.find(chapter => chapter.id === panel.options.chapterId)?.title;
    if (isLocal(panel)) return `${panel.values.phase === 'direct' ? `Selected chapter: ${title || 'choose a chapter below'}. ` : 'All book sections. '}Uses explicit speech tags and rules on this device. This is a heuristic draft; it does not provide semantic whole-book discovery.`;
    if (panel.values.phase === 'scan') return `Scan all eligible sections with ${panel.options.scanModel || 'the configured fast model'}. Save character observations first; refine profiles after the whole-book scan.`;
    if (panel.values.phase === 'profiles') return `Build profiles from saved whole-book observations with ${panel.options.model || 'the analysis model'}. Prioritize frequent speakers and uncertain identities; incomplete source coverage keeps profiles provisional.`;
    if (panel.values.phase === 'direct') return `Direct only ${title || 'the selected chapter'} with ${panel.options.model || 'the analysis model'}. Use saved profiles and preserve reviewed passages.`;
    return 'Scan, build profiles, and direct all eligible sections. Later stages depend on discoveries, so the initial estimate cannot include all future work.';
  }

  function usageMarkup(usage) {
    if (!usage) return '';
    return `<p class="production-help production-usage">Tracked book analysis: ${count(usage.attempts)} attempt${usage.attempts === 1 ? '' : 's'} · ${money(usage.estimated_spend_usd)} estimated · ${count(usage.input_tokens)} input / ${count(usage.output_tokens)} output tokens.${usage.unknown_cost_attempts ? ` ${count(usage.unknown_cost_attempts)} attempt(s) have unknown cost.` : ''}${usage.unknown_usage_attempts ? ` ${count(usage.unknown_usage_attempts)} attempt(s) have incomplete token usage.` : ''} Earlier untracked runs are excluded; provider billing is authoritative.</p>`;
  }

  function coverageMarkup(panel) {
    const coverage = panel.coverage;
    if (!coverage) return `<p class="production-help">${panel.loading ? 'Running the free local census…' : 'Refresh the free census to see source coverage and character priorities.'}</p>`;
    const local = coverage.local || {};
    const characters = local.characters || [];
    const shown = panel.showAllCharacters ? characters : characters.slice(0, 25);
    const freshness = new Map((coverage.characters || []).map(item => [item.character_id, item]));
    const wholeBook = coverage.whole_book_discovered ?? (coverage.semantic_chapters_complete === coverage.eligible_chapters && coverage.eligible_chapters > 0);
    return `<div class="production-coverage-grid"><div><span class="production-metric-label">Local census</span><strong>${count(local.local_chapters_scanned)} / ${count((local.chapters || []).length)} sections</strong><span class="production-help">${count(local.eligible_chapters)} eligible for story analysis · no model calls</span></div><div><span class="production-metric-label">Semantic discovery</span><strong>${count(coverage.semantic_chapters_complete)} / ${count(coverage.eligible_chapters)} eligible sections</strong><span class="production-help">${wholeBook ? 'Whole-book scan complete' : 'More source sections need scanning'}</span></div><div><span class="production-metric-label">Profile freshness</span><strong>${count(coverage.profiles_current ?? 0)} / ${count(coverage.profiles_total ?? 0)} current or reviewed</strong><span class="production-help">${coverage.profiles_provisional ? 'Profiles remain provisional' : 'Profiles reflect the saved whole-book evidence'}</span></div></div><p class="production-help">Scanning the whole book does not finish profile refinement. Current profiles must use the latest saved evidence; identities and vocal traits still need review.</p>${usageMarkup(coverage.usage)}<details class="production-character-stats" ${panel.statsOpen ? 'open' : ''}><summary>Character effort &amp; references · ${count(characters.length)} found</summary><p class="production-help">Counts guide processing effort. Mentions do not prove scene presence or narrative importance. Every speaking role gets basic treatment; uncertain identities receive more attention. Evidence caps limit each profile prompt, not saved source references.</p>${characters.length ? `<div class="production-table-wrap"><table><thead><tr><th>Character</th><th>Profile</th><th>Effort</th><th>Mentions</th><th>Sections</th><th>Dialogue turns</th><th>Evidence cap</th></tr></thead><tbody>${shown.map(character => `<tr><th scope="row">${escape(character.name)}${!character.known_character ? '<small>Local candidate</small>' : ''}${character.ambiguous_aliases || character.uncertain_attributions ? '<small>Identity / attribution needs review</small>' : ''}</th><td>${escape(freshness.get(character.id)?.state || (character.known_character ? 'draft' : 'candidate'))}${freshness.get(character.id)?.provisional ? '<small>Provisional</small>' : ''}</td><td><span class="production-priority ${['deep','standard','basic'].includes(character.priority) ? character.priority : 'basic'}">${escape(character.priority || 'basic')}</span></td><td>${count(character.mentions)}</td><td>${count(character.chapter_count)}</td><td>${count(character.dialogue_turns)}</td><td>${count(character.recommended_evidence_limit)} excerpts</td></tr>`).join('')}</tbody></table></div>${shown.length < characters.length ? `<button type="button" class="button subtle" data-production-action="show-characters">Show all ${count(characters.length)} characters</button>` : ''}` : '<p class="production-help">No named speech candidates found by local rules. A semantic scan can discover characters these rules miss.</p>'}</details>`;
  }

  function planMarkup(panel) {
    if (!panel.preview) return `<p class="production-help">${escape(panel.previewNote || 'Preview the plan to see pending requests and reusable work before starting.')}</p>`;
    const plan = panel.preview;
    const limits = panel.previewPayload.limits;
    const usage = plan.coverage?.usage || panel.coverage?.usage || {};
    const dollarGuard = limits.budget_usd !== null;
    const outOfAllowance = dollarGuard && typeof plan.estimated_cost_usd === 'number' && (usage.estimated_spend_usd || 0) + plan.estimated_cost_usd > limits.budget_usd;
    return `<div class="production-plan-grid"><div><span>Pending requests</span><strong>${count(plan.requests)}</strong></div><div><span>Reusable units</span><strong>${count(plan.cached_units)}</strong></div><div><span>Estimated input tokens</span><strong>${count(plan.estimated_input_tokens)}</strong></div><div><span>Estimated new cost</span><strong>${money(plan.estimated_cost_usd)}</strong></div></div><p class="production-help">Output allowance: ${count(plan.output_token_allowance)} tokens. ${isLocal(panel) ? 'Local rules make no paid provider requests.' : `Run caps: ${count(limits.max_requests)} attempts, ${count(limits.max_input_tokens)} input tokens and ${count(limits.max_output_tokens)} output tokens. ${dollarGuard ? `Book spending guard: ${money(limits.budget_usd)} including earlier tracked analysis.` : 'Dollar guard is off; request and token limits still apply.'}`}</p>${!isLocal(panel) && plan.requests > limits.max_requests ? '<p class="production-plan-note">This run may stop at its request cap. Validated units are saved for the next resume; retries also use attempts.</p>' : ''}${outOfAllowance ? '<p class="production-plan-note">Estimated work exceeds the remaining book allowance. Processing can stop before the next request and retain completed units.</p>' : ''}${!isLocal(panel) && dollarGuard && (plan.estimated_cost_usd === null || usage.unknown_cost_attempts) ? '<p class="production-plan-note">Price or earlier cost is unknown. The dollar guard will stop before an unpriced request. Choose a priced model or explicitly use request/token limits only.</p>' : ''}${plan.future_work_unknown ? '<p class="production-plan-note">Full-pipeline estimates cover currently known work. New discoveries can add profiles and change later direction requests.</p>' : ''}<p class="production-help">${escape(plan.note || 'Estimates exclude future retries. Saved work is reused.')} ${!isLocal(panel) ? 'The request guard reserves conservatively and may stop earlier than this estimate. Failed requests can retain estimated allowance.' : ''}</p>`;
  }

  function paintCoverage(panel) {
    const node = panel.container.querySelector('[data-production-coverage]');
    if (node) {
      panel.statsOpen = Boolean(panel.container.querySelector('.production-character-stats[open]')) || panel.statsOpen;
      node.innerHTML = coverageMarkup(panel);
    }
  }

  function paintPlan(panel) {
    const node = panel.container.querySelector('[data-production-plan]');
    if (node) node.innerHTML = planMarkup(panel);
    const hint = panel.container.querySelector('[data-production-phase-hint]');
    if (hint) hint.textContent = phaseText(panel);
    controls(panel);
  }

  function invalidate(panel, note = 'Processing settings changed. Preview the updated plan before starting.') {
    panel.previewVersion++;
    panel.preview = null;
    panel.previewPayload = null;
    panel.previewNote = note;
    panel.previewing = false;
    paintPlan(panel);
  }

  function paint(panel) {
    const uid = `production-${panel.book.id}`;
    const values = panel.values;
    const phases = isLocal(panel) ? [['full','Local heuristic draft · whole book'], ['direct','Local heuristic draft · selected chapter']] :
      [['scan','1 · Scan whole book with the fast model'], ['profiles','2 · Build book-wide character profiles'], ['direct','3 · Direct the selected chapter'], ['full','Full pipeline · all eligible sections']];
    panel.container.innerHTML = `<section class="production-pipeline" aria-labelledby="${escape(uid)}-title"><div class="production-pipeline-heading"><div><h3 id="${escape(uid)}-title">Process the story in stages</h3><p class="production-help">Scan broadly, refine with saved evidence, then direct the passages you need.</p></div><button type="button" class="button subtle" data-production-action="census">Refresh free census</button></div><div data-production-coverage>${coverageMarkup(panel)}</div><form data-production-form>${isLocal(panel) ? '' : `<p class="production-help production-model-roles"><strong>Fast scan:</strong> ${escape(panel.options.scanModel || 'Choose a model in Settings')} · <strong>Profiles &amp; direction:</strong> ${escape(panel.options.model || 'Choose a model in Settings')}</p>`}<div class="production-stage-field"><label for="${escape(uid)}-phase">Next stage</label><select id="${escape(uid)}-phase" name="phase">${phases.map(([value,label]) => `<option value="${value}" ${values.phase === value ? 'selected' : ''}>${escape(label)}</option>`).join('')}</select></div><p class="production-help" data-production-phase-hint>${escape(phaseText(panel))}</p>${isLocal(panel) ? '<p class="production-help">Choose a cloud provider above for the fast semantic scan and detailed analysis. A future local model can use the same stages.</p>' : `<div class="production-limit-row"><div><label for="${escape(uid)}-requests">Maximum attempts this run</label><input id="${escape(uid)}-requests" type="number" name="max_requests" min="1" max="1000" step="1" value="${escape(values.max_requests)}"></div><div><label for="${escape(uid)}-budget">Total tracked book allowance (USD)</label><input id="${escape(uid)}-budget" type="number" name="budget_usd" min="0.0001" max="1000" step="any" value="${escape(values.budget_usd)}" ${values.token_only ? 'disabled' : ''}></div></div><label class="production-token-only"><input type="checkbox" name="token_only" ${values.token_only ? 'checked' : ''}> Use request/token limits only — no dollar guard</label><details class="production-token-limits"><summary>Token limits for this run</summary><div class="production-limit-row"><div><label for="${escape(uid)}-input">Input-token allowance</label><input id="${escape(uid)}-input" type="number" name="max_input_tokens" min="1000" max="10000000" step="1" value="${escape(values.max_input_tokens)}"></div><div><label for="${escape(uid)}-output">Output-token allowance</label><input id="${escape(uid)}-output" type="number" name="max_output_tokens" min="1000" max="2000000" step="1" value="${escape(values.max_output_tokens)}"></div></div></details><p class="production-help">Dollar allowance covers this book’s tracked analysis across runs. Request and token caps reset for each run. Changing models keeps accepted source discovery and invalidates dependent work where needed.</p>`}<div data-production-plan aria-live="polite">${planMarkup(panel)}</div><p class="production-message" data-production-message role="status"></p><div class="production-actions"><button type="submit" class="button subtle" data-production-action="preview">Preview plan</button><button type="button" class="button primary" data-production-action="start" disabled>Start / resume stage</button></div></form></section>`;
    controls(panel);
  }

  function integer(value, min, max, label) {
    const parsed = Number(value);
    if (!String(value).trim() || !Number.isSafeInteger(parsed) || parsed < min || parsed > max) throw new Error(`${label} must be a whole number from ${count(min)} to ${count(max)}.`);
    return parsed;
  }

  function payload(panel) {
    const values = isLocal(panel) ? {...defaults(), phase:panel.values.phase} : panel.values;
    const limits = {
      max_requests:integer(values.max_requests, 1, 1000, 'Request cap'),
      max_input_tokens:integer(values.max_input_tokens, 1000, 10000000, 'Input-token allowance'),
      max_output_tokens:integer(values.max_output_tokens, 1000, 2000000, 'Output-token allowance'),
      budget_usd:values.token_only ? null : Number(values.budget_usd)
    };
    if (limits.budget_usd !== null && (!String(values.budget_usd).trim() || !Number.isFinite(limits.budget_usd) || limits.budget_usd <= 0 || limits.budget_usd > 1000)) throw new Error('Set a book dollar allowance greater than zero and no more than $1,000, or explicitly choose request/token limits only.');
    const body = {provider:panel.options.provider || 'local', phase:values.phase, resume:true, limits};
    if (values.phase === 'direct') {
      if (!panel.options.chapterId || !panel.book.chapters?.some(chapter => chapter.id === panel.options.chapterId)) throw new Error('Select a chapter in the performance script before directing it.');
      body.chapter_id = panel.options.chapterId;
    }
    return body;
  }

  async function census(panel, notify = false) {
    const version = ++panel.loadVersion;
    const bookId = panel.book.id;
    panel.loading = true;
    paintCoverage(panel);
    controls(panel);
    try {
      const coverage = await request(`/api/books/${path(bookId)}/preprocessing`);
      if (panel.book?.id !== bookId || panel.loadVersion !== version) return;
      panel.coverage = coverage;
      paintCoverage(panel);
      if (notify) {
        invalidate(panel, 'Census refreshed. Preview the plan using the latest saved work.');
        message(panel, 'Free local census refreshed. No model requests were made.');
        if (typeof panel.options.onRefresh === 'function') await panel.options.onRefresh();
      }
    } catch (error) {
      if (panel.book?.id === bookId && panel.loadVersion === version) message(panel, `Could not load the local census: ${error.message}`, true);
    } finally {
      if (panel.book?.id === bookId && panel.loadVersion === version) { panel.loading = false; controls(panel); }
    }
  }

  async function preview(panel) {
    if (panel.options.busy || panel.previewing || panel.starting) return;
    let body;
    try { body = payload(panel); } catch (error) { message(panel, error.message, true); return; }
    const version = ++panel.previewVersion;
    const bookId = panel.book.id;
    panel.previewing = true;
    panel.preview = null;
    controls(panel);
    message(panel, 'Preparing a plan from saved work…');
    try {
      const result = await request(`/api/books/${path(bookId)}/analysis-plan`, body);
      if (panel.book?.id !== bookId || panel.previewVersion !== version) return;
      panel.preview = result;
      panel.previewPayload = body;
      panel.previewNote = '';
      if (result.coverage) { panel.coverage = result.coverage; paintCoverage(panel); }
      message(panel, 'Plan ready. Start when the stage and limits look right.');
      paintPlan(panel);
    } catch (error) {
      if (panel.book?.id === bookId && panel.previewVersion === version) message(panel, `Could not preview this stage: ${error.message}`, true);
    } finally {
      if (panel.book?.id === bookId && panel.previewVersion === version) { panel.previewing = false; controls(panel); }
    }
  }

  async function start(panel) {
    if (panel.options.busy || panel.starting || panel.previewing || !panel.preview || typeof panel.options.onStart !== 'function') return;
    let body;
    try { body = payload(panel); } catch (error) { message(panel, error.message, true); return; }
    if (JSON.stringify(body) !== JSON.stringify(panel.previewPayload)) { invalidate(panel); return; }
    const bookId = panel.book.id;
    panel.starting = true;
    controls(panel);
    try {
      await panel.options.onStart(body);
      if (panel.book?.id === bookId) {
        invalidate(panel, 'Preview again before the next run to account for newly saved units.');
        message(panel, 'Processing started. Validated work is saved for resuming.');
      }
    } catch (error) {
      if (panel.book?.id === bookId) message(panel, error.message, true);
    } finally {
      if (panel.book?.id === bookId) { panel.starting = false; controls(panel); }
    }
  }

  function bind(panel) {
    const changed = event => {
      const field = event.target;
      if (!field.closest('[data-production-form]') || !Object.hasOwn(panel.values, field.name)) return;
      panel.values[field.name] = field.name === 'token_only' ? field.checked : field.value;
      invalidate(panel);
      message(panel, '');
    };
    panel.container.addEventListener('input', changed);
    panel.container.addEventListener('change', changed);
    panel.container.addEventListener('submit', event => {
      if (!event.target.matches('[data-production-form]')) return;
      event.preventDefault();
      void preview(panel);
    });
    panel.container.addEventListener('click', event => {
      const button = event.target.closest('[data-production-action]');
      if (!button) return;
      if (button.dataset.productionAction === 'census' && !panel.loading) void census(panel, true);
      if (button.dataset.productionAction === 'start') void start(panel);
      if (button.dataset.productionAction === 'show-characters') { panel.showAllCharacters = true; panel.statsOpen = true; paintCoverage(panel); }
    });
  }

  function render(container, book, options = {}) {
    if (!container) return Promise.resolve();
    let panel = panels.get(container);
    if (!panel) {
      panel = {container, book:null, options:{provider:'local'}, values:defaults(), dataKey:null, optionsKey:null,
        loadVersion:0, previewVersion:0, coverage:null, loading:false, previewing:false, starting:false,
        preview:null, previewPayload:null, previewNote:'', showAllCharacters:false, statsOpen:false};
      panels.set(container, panel);
      bind(panel);
    }
    if (!book) { panel.loadVersion++; panel.previewVersion++; panel.book = null; panel.dataKey = null; container.innerHTML = ''; return Promise.resolve(); }
    const nextOptions = {...options, provider:options.provider || 'local'};
    const freshBook = panel.book?.id !== book.id;
    const sourceChanged = panel.dataKey !== dataKey(book);
    const settingsChanged = panel.optionsKey !== optionsKey(nextOptions);
    const previousLocal = isLocal(panel);
    panel.book = book;
    panel.options = nextOptions;
    panel.dataKey = dataKey(book);
    panel.optionsKey = optionsKey(nextOptions);
    if (freshBook) {
      panel.values = defaults();
      panel.coverage = null;
      panel.showAllCharacters = false;
      panel.statsOpen = false;
      panel.starting = false;
      if (isLocal(panel)) panel.values.phase = 'full';
    } else if (settingsChanged && previousLocal !== isLocal(panel)) {
      panel.values.phase = isLocal(panel) ? (panel.values.phase === 'direct' ? 'direct' : 'full') : 'scan';
    }
    if (sourceChanged || settingsChanged) {
      invalidate(panel, freshBook ? '' : 'Book, provider, model, or selected chapter changed. Preview the updated plan.');
      paint(panel);
    } else controls(panel);
    if (sourceChanged) return census(panel);
    return Promise.resolve();
  }

  window.SpinTailsProduction = {render};
})();
