/* Progressive analysis controls (the older phase runner); estimates and the local
   name & dialogue count never invoke a model. Escaping and money use BardicUI. */
(() => {
  'use strict';
  const panels = new WeakMap();
  const UI = window.BardicUI;
  const escape = UI.esc;
  const path = value => encodeURIComponent(value);
  const count = value => Number.isFinite(Number(value)) ? Math.round(Number(value)).toLocaleString('en-US') : '—';
  const money = value => UI.fmt.money(value, {precise:true});
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
    if (start) {
      start.disabled = locked || !panel.preview || typeof panel.options.onStart !== 'function';
      // The run button carries the estimate's cost; unknown is never shown as free.
      start.textContent = !panel.preview ? 'Run' : isLocal(panel) ? 'Run · no charge' : UI.confirmLabel('Run', {cost:panel.preview.estimated_cost_usd ?? undefined});
    }
    const refresh = panel.container.querySelector('[data-production-action="census"]');
    if (refresh) refresh.disabled = panel.loading;
    const dollar = panel.container.querySelector('[name="budget_usd"]');
    if (dollar) dollar.disabled = locked || panel.values.token_only;
    panel.container.setAttribute('aria-busy', String(panel.loading || panel.previewing || panel.starting));
  }

  function phaseText(panel) {
    const title = panel.book.chapters?.find(chapter => chapter.id === panel.options.chapterId)?.title;
    if (isLocal(panel)) return `${panel.values.phase === 'direct' ? `Selected chapter: ${title || 'choose a chapter below'}. ` : 'All book sections. '}Uses speech tags and simple rules on the Bardic computer. It is a rough draft and does not search the whole book for characters.`;
    if (panel.values.phase === 'scan') return `Scan all eligible sections with ${panel.options.scanModel || 'the configured fast model'}. Save character observations first; refine profiles after the whole-book scan.`;
    if (panel.values.phase === 'profiles') return `Build profiles from saved whole-book observations with ${panel.options.model || 'the analysis model'}. Frequent speakers and uncertain characters come first; profiles stay provisional until the whole book is scanned.`;
    if (panel.values.phase === 'direct') return `Direct only ${title || 'the selected chapter'} with ${panel.options.model || 'the analysis model'}. Use saved profiles and preserve reviewed passages.`;
    return 'Scan, build profiles, and direct all eligible sections. Later steps depend on discoveries, so the first estimate cannot include all future work.';
  }

  function usageMarkup(usage) {
    if (!usage) return '';
    return `<p class="production-help production-usage">Tracked book analysis: ${escape(UI.fmt.plural(usage.attempts, 'request'))} · ${money(usage.estimated_spend_usd)} estimated · ${count(usage.input_tokens)} input / ${count(usage.output_tokens)} output tokens.${usage.unknown_cost_attempts ? ` ${escape(UI.fmt.plural(usage.unknown_cost_attempts, 'request'))} with unknown cost.` : ''}${usage.unknown_usage_attempts ? ` ${escape(UI.fmt.plural(usage.unknown_usage_attempts, 'request'))} with incomplete token usage.` : ''} Earlier untracked runs are excluded; your provider’s bill is authoritative.</p>`;
  }

  function coverageMarkup(panel) {
    const coverage = panel.coverage;
    if (!coverage) return `<p class="production-help">${panel.loading ? 'Counting names and dialogue on the Bardic computer (free)…' : 'Refresh the free name & dialogue count to see coverage and character priorities.'}</p>`;
    const local = coverage.local || {};
    const characters = local.characters || [];
    const shown = panel.showAllCharacters ? characters : characters.slice(0, 25);
    const freshness = new Map((coverage.characters || []).map(item => [item.character_id, item]));
    const wholeBook = coverage.whole_book_discovered ?? (coverage.semantic_chapters_complete === coverage.eligible_chapters && coverage.eligible_chapters > 0);
    return `<div class="production-coverage-grid"><div><span class="production-metric-label">Name &amp; dialogue count</span><strong>${count(local.local_chapters_scanned)} / ${count((local.chapters || []).length)} sections</strong><span class="production-help">${count(local.eligible_chapters)} eligible for analysis · no requests</span></div><div><span class="production-metric-label">Character search</span><strong>${count(coverage.semantic_chapters_complete)} / ${count(coverage.eligible_chapters)} eligible sections</strong><span class="production-help">${wholeBook ? 'Whole-book scan complete' : 'More sections need scanning'}</span></div><div><span class="production-metric-label">Profiles up to date</span><strong>${count(coverage.profiles_current ?? 0)} / ${count(coverage.profiles_total ?? 0)} current or reviewed</strong><span class="production-help">${coverage.profiles_provisional ? 'Profiles remain provisional' : 'Profiles use the saved whole-book quotes'}</span></div></div><p class="production-help">Scanning the whole book does not finish the profiles. Up-to-date profiles use the latest saved quotes; who is who and vocal traits still need review.</p>${usageMarkup(coverage.usage)}<details class="production-character-stats" ${panel.statsOpen ? 'open' : ''}><summary>Character effort &amp; references · ${count(characters.length)} found</summary><p class="production-help">Counts guide processing effort. Mentions do not prove scene presence or narrative importance. Every speaking role gets basic treatment; uncertain characters get more attention. Quote limits cap each profile request, not saved source references.</p>${characters.length ? `<div class="production-table-wrap"><table><thead><tr><th>Character</th><th>Profile</th><th>Effort</th><th>Mentions</th><th>Sections</th><th>Dialogue turns</th><th>Quote limit</th></tr></thead><tbody>${shown.map(character => `<tr><th scope="row">${escape(character.name)}${!character.known_character ? '<small>Local candidate</small>' : ''}${character.ambiguous_aliases || character.uncertain_attributions ? '<small>Who this is or who speaks needs review</small>' : ''}</th><td>${escape(freshness.get(character.id)?.state || (character.known_character ? 'draft' : 'candidate'))}${freshness.get(character.id)?.provisional ? '<small>Provisional</small>' : ''}</td><td><span class="production-priority ${['deep','standard','basic'].includes(character.priority) ? character.priority : 'basic'}">${escape(character.priority || 'basic')}</span></td><td>${count(character.mentions)}</td><td>${count(character.chapter_count)}</td><td>${count(character.dialogue_turns)}</td><td>${count(character.recommended_evidence_limit)} excerpts</td></tr>`).join('')}</tbody></table></div>${shown.length < characters.length ? `<button type="button" class="button subtle" data-production-action="show-characters">Show all ${count(characters.length)} characters</button>` : ''}` : '<p class="production-help">No named speakers found by the local rules. A character search with a model can find characters these rules miss.</p>'}</details>`;
  }

  function planMarkup(panel) {
    if (!panel.preview) return `<p class="production-help">${escape(panel.previewNote || 'Estimate to see the requests needed and the work already saved before running.')}</p>`;
    const plan = panel.preview;
    const limits = panel.previewPayload.limits;
    const usage = plan.coverage?.usage || panel.coverage?.usage || {};
    const dollarGuard = limits.budget_usd !== null;
    const overLimit = dollarGuard && typeof plan.estimated_cost_usd === 'number' && (usage.estimated_spend_usd || 0) + plan.estimated_cost_usd > limits.budget_usd;
    return `<div class="production-plan-grid"><div><span>Requests needed</span><strong>${count(plan.requests)}</strong></div><div><span>Already saved</span><strong>${count(plan.cached_units)}</strong></div><div><span>Estimated input tokens</span><strong>${count(plan.estimated_input_tokens)}</strong></div><div><span>Estimated new cost</span><strong>${money(plan.estimated_cost_usd)}</strong></div></div><p class="production-help">Output token limit: ${count(plan.output_token_allowance)}. ${isLocal(panel) ? 'Local rules make no paid provider requests.' : `Limits for this run: ${count(limits.max_requests)} requests, ${count(limits.max_input_tokens)} input tokens and ${count(limits.max_output_tokens)} output tokens. ${dollarGuard ? `Book spending limit: ${money(limits.budget_usd)}, including earlier tracked analysis.` : 'No dollar limit; request and token limits still apply.'}`}</p>${!isLocal(panel) && plan.requests > limits.max_requests ? '<p class="production-plan-note">This run may stop at its request limit. Checked results are saved for the next run; retries also count as requests.</p>' : ''}${overLimit ? '<p class="production-plan-note">Estimated work exceeds what is left of the book spending limit. The run can stop before the next request and keeps finished work.</p>' : ''}${!isLocal(panel) && dollarGuard && (plan.estimated_cost_usd === null || usage.unknown_cost_attempts) ? '<p class="production-plan-note">Price or earlier cost is unknown. The dollar limit stops before a request with an unknown price. Choose a model with a known price, or use request and token limits only.</p>' : ''}${plan.future_work_unknown ? '<p class="production-plan-note">An estimate for all steps covers the work known now. New discoveries can add profiles and change later direction requests.</p>' : ''}<p class="production-help">${escape(plan.note || 'Estimates exclude future retries. Saved work is reused.')} ${!isLocal(panel) ? 'The limits hold back a safety margin, so a run may stop earlier than this estimate. Failed requests can keep their estimated cost counted.' : ''}</p>`;
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

  function invalidate(panel, note = 'Settings changed. Estimate again before running.') {
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
    const phases = isLocal(panel) ? [['full','Local draft (rules only) · whole book'], ['direct','Local draft (rules only) · selected chapter']] :
      [['scan','1 · Scan whole book with the fast model'], ['profiles','2 · Build book-wide character profiles'], ['direct','3 · Direct the selected chapter'], ['full','All steps · all eligible sections']];
    panel.container.innerHTML = `<section class="production-pipeline" aria-labelledby="${escape(uid)}-title"><div class="production-pipeline-heading"><div><h3 id="${escape(uid)}-title">Analyze in steps</h3><p class="production-help">Scan the whole book, build profiles from saved quotes, then direct the passages you need.</p></div><button type="button" class="button subtle" data-production-action="census">Refresh free count</button></div><div data-production-coverage>${coverageMarkup(panel)}</div><form data-production-form>${isLocal(panel) ? '' : `<p class="production-help production-model-roles"><strong>Fast scan:</strong> ${escape(panel.options.scanModel || 'Choose a model in Settings')} · <strong>Profiles &amp; direction:</strong> ${escape(panel.options.model || 'Choose a model in Settings')}</p>`}<div class="production-stage-field"><label for="${escape(uid)}-phase">Next step</label><select id="${escape(uid)}-phase" name="phase">${phases.map(([value,label]) => `<option value="${value}" ${values.phase === value ? 'selected' : ''}>${escape(label)}</option>`).join('')}</select></div><p class="production-help" data-production-phase-hint>${escape(phaseText(panel))}</p>${isLocal(panel) ? '<p class="production-help">Set a cloud analysis service in Settings to search the whole book for characters and to analyze in detail.</p>' : `<div class="production-limit-row"><div><label for="${escape(uid)}-requests">Most requests this run</label><input id="${escape(uid)}-requests" type="number" name="max_requests" min="1" max="1000" step="1" value="${escape(values.max_requests)}"></div><div><label for="${escape(uid)}-budget">Book spending limit (USD)</label><input id="${escape(uid)}-budget" type="number" name="budget_usd" min="0.0001" max="1000" step="any" value="${escape(values.budget_usd)}" ${values.token_only ? 'disabled' : ''}></div></div><label class="production-token-only"><input type="checkbox" name="token_only" ${values.token_only ? 'checked' : ''}> No dollar limit: use request and token limits only</label><details class="production-token-limits"><summary>Token limits for this run</summary><div class="production-limit-row"><div><label for="${escape(uid)}-input">Input token limit</label><input id="${escape(uid)}-input" type="number" name="max_input_tokens" min="1000" max="10000000" step="1" value="${escape(values.max_input_tokens)}"></div><div><label for="${escape(uid)}-output">Output token limit</label><input id="${escape(uid)}-output" type="number" name="max_output_tokens" min="1000" max="2000000" step="1" value="${escape(values.max_output_tokens)}"></div></div></details><p class="production-help">The dollar limit covers this book’s tracked analysis across runs. Request and token limits reset for each run. Changing models keeps accepted character search results and marks dependent work out of date.</p>`}<div data-production-plan aria-live="polite">${planMarkup(panel)}</div><p class="production-message" data-production-message role="status"></p><div class="production-actions"><button type="submit" class="button subtle" data-production-action="preview">Estimate</button><button type="button" class="button primary" data-production-action="start" disabled>Run</button></div></form></section>`;
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
      max_requests:integer(values.max_requests, 1, 1000, 'Request limit'),
      max_input_tokens:integer(values.max_input_tokens, 1000, 10000000, 'Input token limit'),
      max_output_tokens:integer(values.max_output_tokens, 1000, 2000000, 'Output token limit'),
      budget_usd:values.token_only ? null : Number(values.budget_usd)
    };
    if (limits.budget_usd !== null && (!String(values.budget_usd).trim() || !Number.isFinite(limits.budget_usd) || limits.budget_usd <= 0 || limits.budget_usd > 1000)) throw new Error('Set a book spending limit above $0 and no more than $1,000, or choose request and token limits only.');
    const body = {provider:panel.options.provider || 'local', phase:values.phase, resume:true, limits};
    if (values.phase === 'direct') {
      if (!panel.options.chapterId || !panel.book.chapters?.some(chapter => chapter.id === panel.options.chapterId)) throw new Error('Select a chapter in the script before directing it.');
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
        invalidate(panel, 'Count refreshed. Estimate again to use the latest saved work.');
        message(panel, 'Name & dialogue count refreshed on the Bardic computer. No model requests were made.');
        if (typeof panel.options.onRefresh === 'function') await panel.options.onRefresh();
      }
    } catch (error) {
      if (panel.book?.id === bookId && panel.loadVersion === version) message(panel, `Could not load the name & dialogue count: ${error.message}`, true);
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
    message(panel, 'Estimating from saved work…');
    try {
      const result = await request(`/api/books/${path(bookId)}/analysis-plan`, body);
      if (panel.book?.id !== bookId || panel.previewVersion !== version) return;
      panel.preview = result;
      panel.previewPayload = body;
      panel.previewNote = '';
      if (result.coverage) { panel.coverage = result.coverage; paintCoverage(panel); }
      message(panel, 'Estimate ready. Run when the step and limits look right.');
      paintPlan(panel);
    } catch (error) {
      if (panel.book?.id === bookId && panel.previewVersion === version) message(panel, `Could not estimate this step: ${error.message}`, true);
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
        invalidate(panel, 'Estimate again before the next run to count newly saved work.');
        message(panel, 'Run started. Checked results are saved as they finish.');
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
      invalidate(panel, freshBook ? '' : 'Book, service, model or chapter changed. Estimate again.');
      paint(panel);
    } else controls(panel);
    if (sourceChanged) return census(panel);
    return Promise.resolve();
  }

  window.BardicProduction = {render};
})();
