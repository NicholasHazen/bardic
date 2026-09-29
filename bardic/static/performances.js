/* Performances: chosen chapters recorded ahead by one narrator or the full
   cast, then played later without new requests. The server job does the
   work; this panel lists, estimates, starts, resumes and plays them.
   Markup escapes and formats numbers with the BardicUI kit (ui.js). */
(() => {
  'use strict';
  const panels = new WeakMap();
  const ACTIVE = new Set(['queued','running']);
  const PROVIDER_LABELS = {system:'Mac voices', gemini:'Gemini', breeze:'Breeze'};
  const UI = window.BardicUI;
  const escape = UI.esc, plural = UI.fmt.plural;
  const encode = value => encodeURIComponent(value);
  const base = book => `/api/books/${encode(book.id)}/performances`;
  function span(seconds) {
    const value = Math.max(0, Math.round(Number(seconds) || 0));
    if (value < 3600) return `${Math.max(1, Math.round(value / 60))} min`;
    return `${Math.floor(value / 3600)} h ${Math.round(value % 3600 / 60)} min`;
  }
  // Server details state the condition; the hint says where to fix it.
  const HINTS = {gemini_key_missing:'Add a Gemini API key in Providers & settings, or choose another narrator.',breeze_url_missing:'Add the Breeze server URL in Providers & settings, or choose another narrator.',narrator_voice_missing:'Choose a narrator voice in Cast first.'};
  async function request(url, {method = 'GET', body} = {}) {
    const response = await fetch(url, {method, headers:{Accept:'application/json', ...(body === undefined ? {} : {'Content-Type':'application/json'})},
      ...(body === undefined ? {} : {body:JSON.stringify(body)})});
    let result = null;
    try { result = await response.json(); } catch { /* No body. */ }
    if (!response.ok) {
      const detail = typeof result?.detail === 'string' ? result.detail : `Performance request failed (${response.status}).`;
      const error = new Error(HINTS[result?.code] ? `${detail} ${HINTS[result.code]}` : detail);
      error.status = response.status;
      error.code = result?.code;
      throw error;
    }
    return result;
  }
  // Narrative chapters by default; front and back matter are opt-in.
  const defaultChapters = book => new Set(book.chapters.filter(chapter => !['front_matter','back_matter'].includes(chapter.kind)).map(chapter => chapter.id));

  function freshForm(panel) {
    const {book, options} = panel;
    const listen = options.listen, selection = listen?.getSelection?.(book);
    const provider = ['system','gemini','breeze'].includes(selection?.provider) ? selection.provider : 'system';
    return {name:'', mode:'simple', provider, voices:{}, castProvider:options.castProvider || provider, chapters:defaultChapters(book)};
  }
  function formBody(panel) {
    const {form, book} = panel;
    const provider = form.mode === 'cast' ? form.castProvider : form.provider;
    const narrator = panel.options.listen?.narratorOptions?.(book, provider);
    const voice = form.mode === 'cast' ? null : form.voices[provider] ?? narrator?.voice ?? '';
    return {name:form.name.trim() || undefined, mode:form.mode, provider, voice,
      model:provider === 'gemini' ? narrator?.model || null : null,
      chapter_ids:book.chapters.filter(chapter => form.chapters.has(chapter.id)).map(chapter => chapter.id)};
  }

  async function refresh(panel) {
    const book = panel.book;
    panel.refreshing = true;
    try {
      const result = await request(base(book));
      if (panel.book !== book) return;
      panel.list = result.performances || [];
      panel.error = '';
      for (const record of panel.list) if (ACTIVE.has(record.job?.status)) panel.options.onJob?.({...record.job, book_id:book.id, kind:'performance'});
    } catch (error) { panel.error = error.message; }
    finally { panel.refreshing = false; }
    paint(panel);
    if (panel.view === 'detail') void previewDetail(panel);
    schedule(panel);
  }
  // Poll only while something is processing and the panel is on screen.
  function schedule(panel) {
    clearTimeout(panel.timer);
    if (!panel.list?.some(record => ACTIVE.has(record.job?.status))) return;
    panel.timer = setTimeout(() => { if (panel.options.visible?.() !== false) void refresh(panel); else schedule(panel); }, 3000);
  }
  async function preview(panel) {
    const body = formBody(panel), key = JSON.stringify(body);
    if (panel.previewKey === key) return;
    panel.previewKey = key;
    if (!body.chapter_ids.length) { panel.preview = null; paint(panel); return; }
    clearTimeout(panel.previewTimer);
    panel.previewTimer = setTimeout(async () => {
      try {
        const result = await request(`${base(panel.book)}/preview`, {method:'POST', body});
        if (panel.previewKey === key) { panel.preview = result; panel.formError = ''; }
      } catch (error) { if (panel.previewKey === key) { panel.preview = null; panel.formError = error.message; } }
      paint(panel);
    }, 250);
  }

  // What recording the rest would do, with any chapters chosen to add. Local: nothing is stored or sent.
  // Re-checked when the chosen chapters, the job or the ready count change, so it follows a running job.
  async function previewDetail(panel) {
    const detail = panel.detail, record = panel.list?.find(item => item.id === detail?.id);
    if (!detail || !record) return;
    const chapter_ids = panel.book.chapters.filter(chapter => detail.add.has(chapter.id)).map(chapter => chapter.id);
    const key = JSON.stringify([record.id, chapter_ids, record.progress?.passages_ready, record.progress?.passages_total, record.job?.status]);
    if (detail.previewKey === key) return;
    detail.previewKey = key;
    try {
      const result = await request(`${base(panel.book)}/${encode(record.id)}/preview`, {method:'POST', body:{chapter_ids}});
      if (detail.previewKey === key) { detail.preview = result; detail.error = ''; }
    } catch (error) { if (detail.previewKey === key) { detail.preview = null; detail.error = error.message; } }
    paint(panel);
  }

  // One state per performance: an active job wins, then a complete recording, then how the last job ended.
  function stateOf(record) {
    const job = record.job, progress = record.progress || {};
    const complete = progress.passages_total > 0 && progress.passages_ready >= progress.passages_total;
    if (job?.status === 'running') return {key:'running', label:'Recording'};
    if (job?.status === 'queued') return {key:'queued', label:'Queued'};
    if (complete) return {key:'complete', label:'Complete'};
    const ended = {quota_limited:['quota_limited','Paused · daily limit'], budget_limited:['budget_limited','Paused · spending limit'],
      failed:['failed','Stopped on an error'], cancelled:['cancelled','Stopped by you'], interrupted:['interrupted','Interrupted']}[job?.status];
    if (ended) return {key:ended[0], label:ended[1]};
    return progress.passages_ready ? {key:'partial', label:'Partly recorded'} : {key:'not_started', label:'Not recorded yet'};
  }
  function statusLine(record) {
    const job = record.job, progress = record.progress || {};
    const ready = `${progress.passages_ready ?? 0} of ${progress.passages_total ?? 0} passages ready`;
    if (ACTIVE.has(job?.status)) return `${ready}${job.message ? ` · ${job.message}` : ''}`;
    if (job?.status === 'quota_limited') return `Paused at the daily request limit · ${ready}`;
    if (job?.status === 'budget_limited') return `Paused at the spending limit · ${ready}`;
    if (job?.status === 'failed') return `Stopped on an error · ${ready}`;
    if (job?.status === 'cancelled') return `Stopped by you · ${ready}`;
    if (job?.status === 'interrupted') return `Interrupted when Bardic stopped · ${ready}`;
    return progress.passages_total && progress.passages_ready >= progress.passages_total ? `Ready · ${span(progress.seconds_ready)} of listening` : ready;
  }
  // "3 of 5 chapters complete" from the per-chapter coverage.
  function coverage(record) {
    const rows = record.progress?.chapters || [];
    const done = rows.filter(row => row.passages_total > 0 && row.passages_ready >= row.passages_total).length;
    return rows.length ? `${done} of ${plural(rows.length, 'chapter')} complete` : plural((record.chapter_ids || []).length, 'chapter');
  }
  function cardMarkup(panel, record) {
    const progress = record.progress || {}, job = record.job;
    const complete = progress.passages_total && progress.passages_ready >= progress.passages_total;
    const running = ACTIVE.has(job?.status);
    const renaming = panel.renaming === record.id, archiving = panel.archiving === record.id;
    const current = panel.options.listen?.getPerformance?.(panel.book)?.id === record.id;
    const state = stateOf(record);
    // Resuming a paid performance shows its request estimate first, in the detail view.
    const resume = record.provider === 'gemini' ? 'open' : 'resume';
    return `<article class="performance-card${current ? ' is-current' : ''}" data-performance="${escape(record.id)}" data-state="${state.key}">
      <div class="performance-card-head">${renaming
        ? `<label class="sr-only" for="rename-${escape(record.id)}">Performance name</label><input id="rename-${escape(record.id)}" data-performance-name value="${escape(record.name)}" maxlength="120"><button type="button" class="button subtle" data-performance-action="save-name">Save</button>`
        : `<div><strong>${escape(record.name)}</strong><small>${escape(record.narrator_label || '')} · ${escape(coverage(record))}${current ? ' · playing now' : ''}</small></div>${UI.statusBadge(state.key, state.label)}`}</div>
      <progress max="${progress.passages_total || 1}" value="${progress.passages_ready || 0}" aria-label="Passages ready"></progress>
      <p class="performance-status">${escape(statusLine(record))}${job?.error && !running ? ` <span class="performance-error">${escape(job.error)}</span>` : ''}</p>
      ${archiving ? `<div class="performance-confirm" role="group" aria-label="Remove ${escape(record.name)}"><span>Remove this performance from the list? Its audio is kept.</span><button type="button" class="button subtle" data-performance-action="archive-confirm">Remove</button><button type="button" class="button subtle" data-performance-action="archive-cancel">Cancel</button></div>`
        : `<div class="performance-actions">
        <button type="button" class="button primary" data-performance-action="play" ${progress.passages_ready ? '' : 'disabled'}>${current ? 'Continue' : running ? 'Listen now' : 'Play'}</button>
        <button type="button" class="button subtle" data-performance-action="open">Open</button>
        ${running ? '<button type="button" class="button subtle" data-performance-action="stop">Stop recording</button>'
          : complete ? '' : `<button type="button" class="button subtle" data-performance-action="${resume}">Resume recording</button>`}
        <button type="button" class="button text-button" data-performance-action="rename">Rename</button>
        <button type="button" class="button text-button" data-performance-action="archive">Remove</button>
      </div>`}
    </article>`;
  }
  // One performance, opened: status, what each chapter has, and what recording more would do.
  function detailMarkup(panel) {
    const detail = panel.detail, book = panel.book;
    const record = panel.list?.find(item => item.id === detail?.id);
    const back = '<button type="button" class="button text-button" data-performance-action="back">← All performances</button>';
    if (!record) return `<div class="performance-detail">${back}<p class="performance-empty">${panel.list ? 'This performance is no longer in the list.' : 'Loading performance…'}</p></div>`;
    const progress = record.progress || {}, job = record.job, running = ACTIVE.has(job?.status);
    const state = stateOf(record), result = detail.preview;
    const current = panel.options.listen?.getPerformance?.(book)?.id === record.id;
    const inside = new Map((progress.chapters || []).map(row => [row.id, row]));
    const counts = book.segments.reduce((map, segment) => map.set(segment.chapter_id, (map.get(segment.chapter_id) || 0) + 1), new Map());
    const chosen = detail.add.size;
    const rows = book.chapters.map(chapter => {
      const row = inside.get(chapter.id);
      const title = escape(chapter.title || 'Untitled chapter');
      if (row) {
        const done = row.passages_total > 0 && row.passages_ready >= row.passages_total;
        return `<label class="performance-chapter-row" data-included="true"><input type="checkbox" checked disabled aria-label="${title} is in this performance"><span>${title}</span><small>${done ? 'Complete' : `${row.passages_ready}/${row.passages_total} ready`}</small></label>`;
      }
      return `<label class="performance-chapter-row"><input type="checkbox" data-performance-chapter="${escape(chapter.id)}" ${detail.add.has(chapter.id) ? 'checked' : ''} ${running ? 'disabled' : ''}><span>${title}</span><small>${escape(plural(counts.get(chapter.id) || 0, 'passage'))} · not in this performance</small></label>`;
    }).join('');
    const remaining = book.chapters.some(chapter => !inside.has(chapter.id));
    const toRecord = result?.passages_to_generate ?? 0;
    const requests = result ? plural(result.requests_estimate, 'request') : '';
    const summary = running ? 'Recording is in progress. To add chapters, wait for it to finish or stop it first.'
      : !result ? 'Checking what is already saved…'
      : chosen ? `${plural(chosen, 'chapter')} to add · ${plural(toRecord, 'passage')} to record${toRecord ? ` · about ${requests}` : ''}`
      : toRecord ? `${plural(toRecord, 'passage')} left to record · about ${requests}` : 'Every passage of this performance is recorded.';
    const cost = record.provider === 'gemini' && toRecord ? ` · about ${plural(result.requests_estimate, 'paid request')} · cost unknown` : '';
    const label = chosen ? `Add ${plural(chosen, 'chapter')} and record` : toRecord ? `Record the remaining ${plural(toRecord, 'passage')}` : 'Everything is recorded';
    const disabled = running || !result || result.problems?.length || (!chosen && !toRecord) || panel.recording;
    const cast = (record.cast || []).map(member => `<li><strong>${escape(member.name)}</strong> <small>${escape(member.voice_label)}${member.fallback ? ' · uses the narrator' : ''}</small></li>`).join('');
    const updated = record.updated_at ? new Date(record.updated_at) : null;
    return `<div class="performance-detail" data-performance="${escape(record.id)}" data-state="${state.key}">
      <div class="performance-detail-head">${back}</div>
      <div class="performance-card-head"><div><strong>${escape(record.name)}</strong><small>${escape(record.narrator_label || '')} · ${escape(coverage(record))}${current ? ' · playing now' : ''}</small></div>${UI.statusBadge(state.key, state.label)}</div>
      <progress max="${progress.passages_total || 1}" value="${progress.passages_ready || 0}" aria-label="Passages ready"></progress>
      <p class="performance-status">${escape(statusLine(record))}${progress.seconds_ready ? ` · ${escape(span(progress.seconds_ready))} of listening ready` : ''}${updated && !Number.isNaN(updated.getTime()) ? ` · updated ${escape(updated.toLocaleString([], {dateStyle:'medium', timeStyle:'short'}))}` : ''}${job?.error && !running ? ` <span class="performance-error">${escape(job.error)}</span>` : ''}</p>
      ${running ? '<p class="field-help">Recording continues while you listen. Playing sends no requests of its own: it plays what is ready, follows new passages as they land, and waits at the frontier for the next one.</p>' : ''}
      <div class="performance-actions">
        <button type="button" class="button primary" data-performance-action="play" ${progress.passages_ready ? '' : 'disabled'}>${current ? 'Continue' : running ? 'Listen while it records' : 'Play'}</button>
        ${running ? '<button type="button" class="button subtle" data-performance-action="stop">Stop recording</button>' : ''}
      </div>
      <div class="performance-chapters-head"><span class="field-label" id="performance-detail-chapters">Chapters</span><span>${remaining && !running ? '<button type="button" class="button text-button" data-performance-action="add-rest">Add every remaining chapter</button>' : ''}${chosen && !running ? '<button type="button" class="button text-button" data-performance-action="add-none">Clear</button>' : ''}</span></div>
      <div class="performance-chapters" role="group" aria-labelledby="performance-detail-chapters">${rows}</div>
      ${cast ? `<details class="performance-cast"><summary>Cast for this performance</summary><ul>${cast}</ul></details>` : ''}
      <p class="performance-summary" role="status">${escape(summary)}</p>
      ${(result?.notes || []).map(note => `<p class="field-help">${escape(note)}</p>`).join('')}
      ${(result?.problems || []).map(problem => `<p class="inline-error">${escape(problem.detail)}${HINTS[problem.code] ? ` ${escape(HINTS[problem.code])}` : ''}</p>`).join('')}
      ${detail.error ? `<p class="inline-error" role="alert">${escape(detail.error)}</p>` : ''}
      <div class="performance-form-actions"><button type="button" class="button primary" data-performance-action="record-more" ${disabled ? 'disabled' : ''}>${escape(panel.recording ? 'Starting…' : label + cost)}</button></div>
    </div>`;
  }
  function listMarkup(panel) {
    if (panel.view === 'detail') return detailMarkup(panel);
    const list = panel.list;
    const body = list === null ? '<p class="field-help">Loading performances…</p>'
      : list.length ? list.map(record => cardMarkup(panel, record)).join('')
      : '<p class="performance-empty">No performances yet. Record chosen chapters ahead of time, with one narrator or the full cast, and listen later with no waiting.</p>';
    return `<div class="performance-list-head"><p>Recorded ahead of time. Playing one sends no requests, even while it is still recording.</p><button type="button" class="button primary" data-performance-action="new">Create performance</button></div>
      ${panel.error ? `<p class="inline-error" role="alert">${escape(panel.error)}</p>` : ''}
      <div class="performance-list">${body}</div>`;
  }
  function formMarkup(panel) {
    const {form, book} = panel, listen = panel.options.listen;
    const provider = form.mode === 'cast' ? form.castProvider : form.provider;
    const narrator = listen?.narratorOptions?.(book, provider);
    const voice = form.voices[provider] ?? narrator?.voice ?? '';
    const result = panel.preview;
    const counts = book.segments.reduce((map, segment) => map.set(segment.chapter_id, (map.get(segment.chapter_id) || 0) + 1), new Map());
    const summary = !form.chapters.size ? 'Choose at least one chapter.'
      : !result ? 'Checking what is already saved…'
      : `${plural(result.passages_total, 'passage')} · ${result.passages_ready} already saved · ${result.passages_to_generate} to record${result.passages_to_generate ? ` · about ${plural(result.requests_estimate, 'request')}` : ''} · about ${span(result.expected_seconds)} of listening`;
    const quota = result?.quota ? `This library has used ${result.quota.requests_today} of ${result.quota.rpd} Gemini requests today. Processing pauses at the limit; resume after the reset.` : '';
    return `<form class="performance-form" data-performance-form novalidate>
      <label class="field-label" for="performance-name">Name</label>
      <input id="performance-name" data-performance-field="name" maxlength="120" placeholder="${escape(book.title || 'This book')} · ${escape(form.mode === 'cast' ? 'Full cast' : voice || 'Narrator')}" value="${escape(form.name)}">
      <span class="field-label" id="performance-mode-label">Voices</span>
      <div class="performance-segmented" role="radiogroup" aria-labelledby="performance-mode-label">
        <button type="button" role="radio" data-performance-mode="simple" aria-checked="${form.mode === 'simple'}"><strong>One narrator</strong><small>A single voice reads everything</small></button>
        <button type="button" role="radio" data-performance-mode="cast" aria-checked="${form.mode === 'cast'}"><strong>Full cast</strong><small>Each character's voice from Cast; chapters not yet analyzed use the narrator</small></button>
      </div>
      <span class="field-label" id="performance-provider-label">Service</span>
      <div class="performance-providers" role="radiogroup" aria-labelledby="performance-provider-label">${['system','gemini','breeze'].map(id => {
        const option = listen?.narratorOptions?.(book, id);
        return `<button type="button" role="radio" data-performance-provider="${id}" aria-checked="${id === provider}"><strong>${PROVIDER_LABELS[id]}</strong><small>${option?.available === false ? 'Not set up' : id === 'gemini' ? 'Paid' : id === 'breeze' ? 'Your server · free' : 'Free'}</small></button>`;
      }).join('')}</div>
      ${form.mode === 'simple' ? `<label class="field-label" for="performance-voice">Voice</label><select id="performance-voice" data-performance-field="voice">${(narrator?.voices || []).map(item => `<option value="${escape(item.id)}" ${item.id === voice ? 'selected' : ''} ${item.usable || item.id === voice ? '' : 'disabled'}>${escape(item.name)}${item.locale ? ` · ${escape(item.locale)}` : ''}</option>`).join('')}</select>${provider === 'system' && (narrator?.hiddenVoices || narrator?.showAllVoices) ? `<label class="performance-all-voices"><input type="checkbox" data-performance-field="all-voices" ${narrator.showAllVoices ? 'checked' : ''}> Show all voices${narrator.showAllVoices ? '' : ` (${escape(narrator.hiddenVoices)} in other languages or novelty voices hidden)`}</label>` : ''}` : ''}
      <div class="performance-chapters-head"><span class="field-label" id="performance-chapters-label">Chapters</span><span><button type="button" class="button text-button" data-performance-action="all">All</button><button type="button" class="button text-button" data-performance-action="none">None</button></span></div>
      <div class="performance-chapters" role="group" aria-labelledby="performance-chapters-label">${book.chapters.map(chapter => {
        const ready = result?.chapters?.find(item => item.id === chapter.id);
        return `<label><input type="checkbox" data-performance-chapter="${escape(chapter.id)}" ${form.chapters.has(chapter.id) ? 'checked' : ''}><span>${escape(chapter.title || 'Untitled chapter')}</span><small>${ready ? `${ready.passages_ready}/${ready.passages_total} saved` : escape(plural(counts.get(chapter.id) || 0, 'passage'))}</small></label>`;
      }).join('')}</div>
      <p class="performance-summary" role="status">${escape(summary)}</p>
      ${quota ? `<p class="field-help">${escape(quota)}</p>` : ''}
      ${(result?.notes || []).map(note => `<p class="field-help">${escape(note)}</p>`).join('')}
      ${(result?.problems || []).map(problem => `<p class="inline-error">${escape(problem.detail)}${HINTS[problem.code] ? ` ${escape(HINTS[problem.code])}` : ''}</p>`).join('')}
      ${panel.formError ? `<p class="inline-error" role="alert">${escape(panel.formError)}</p>` : ''}
      <div class="performance-form-actions"><button type="button" class="button subtle" data-performance-action="cancel-new">Cancel</button><button type="submit" class="button primary" ${!form.chapters.size || panel.creating || result?.problems?.length || narrator?.available === false ? 'disabled' : ''}>${panel.creating ? 'Starting…' : provider === 'gemini' && result?.passages_to_generate ? `Record performance · about ${escape(plural(result.requests_estimate, 'paid request'))} · cost unknown` : 'Record performance'}</button></div>
    </form>`;
  }
  function paint(panel) {
    const html = panel.view === 'new' ? formMarkup(panel) : listMarkup(panel);
    // The browser reserializes innerHTML differently, so compare with what was written.
    if (panel.html === html) return;
    const focused = panel.container.contains(document.activeElement) ? document.activeElement : null;
    const selector = !focused ? null : focused.id ? `#${CSS.escape(focused.id)}`
      : ['performanceAction','performanceMode','performanceProvider','performanceChapter'].map(key => focused.dataset[key] !== undefined
        ? `[data-${key.replace(/[A-Z]/g, c => `-${c.toLowerCase()}`)}="${CSS.escape(focused.dataset[key])}"]` : null).find(Boolean);
    const card = focused?.closest?.('[data-performance]')?.dataset.performance;
    // Replacing the markup drops every scroll offset inside it and can clamp the ones around it
    // (the sheet or page that scrolls the panel), so read them first and put them back.
    const scrolls = [];
    for (let node = panel.container; node; node = node.parentElement) scrolls.push([node, node.scrollTop || 0]);
    const chapters = panel.container.querySelector?.('.performance-chapters');
    const listScroll = chapters ? chapters.scrollTop || 0 : 0;
    panel.html = html;
    panel.container.innerHTML = html;
    for (const [node, top] of scrolls) if (top && node.scrollTop !== top) node.scrollTop = top;
    const fresh = listScroll && panel.container.querySelector?.('.performance-chapters');
    if (fresh) fresh.scrollTop = listScroll;
    const scope = card ? panel.container.querySelector(`[data-performance="${CSS.escape(card)}"]`) || panel.container : panel.container;
    if (selector) scope.querySelector(selector)?.focus({preventScroll:true});
  }

  async function act(panel, action, record) {
    const book = panel.book;
    try {
      if (action === 'new') { panel.view = 'new'; panel.form = freshForm(panel); panel.preview = null; panel.previewKey = null; panel.formError = ''; paint(panel); void preview(panel); return; }
      if (action === 'cancel-new') { panel.view = 'list'; paint(panel); return; }
      if (action === 'all') { panel.form.chapters = new Set(book.chapters.map(chapter => chapter.id)); paint(panel); void preview(panel); return; }
      if (action === 'none') { panel.form.chapters = new Set(); paint(panel); void preview(panel); return; }
      if (action === 'back') { panel.view = 'list'; panel.detail = null; paint(panel); return; }
      if (action === 'add-rest' && panel.detail) {
        const inside = new Set(panel.list?.find(item => item.id === panel.detail.id)?.chapter_ids || []);
        panel.detail.add = new Set(book.chapters.filter(chapter => !inside.has(chapter.id) && !['front_matter','back_matter'].includes(chapter.kind)).map(chapter => chapter.id));
        paint(panel); void previewDetail(panel); return;
      }
      if (action === 'add-none' && panel.detail) { panel.detail.add = new Set(); paint(panel); void previewDetail(panel); return; }
      if (action === 'record-more' && panel.detail) { await recordMore(panel); return; }
      if (!record) return;
      if (action === 'open') { panel.view = 'detail'; panel.detail = {id:record.id, add:new Set(), preview:null, previewKey:null, error:''}; paint(panel); void previewDetail(panel); return; }
      if (action === 'play') { await panel.options.onPlay?.(record); return; }
      if (action === 'rename') { panel.renaming = record.id; paint(panel); panel.container.querySelector('[data-performance-name]')?.focus(); return; }
      if (action === 'archive') { panel.archiving = record.id; paint(panel); return; }
      if (action === 'archive-cancel') { panel.archiving = null; paint(panel); return; }
      if (action === 'save-name') {
        const name = panel.container.querySelector('[data-performance-name]')?.value.trim();
        if (name) await request(`${base(book)}/${encode(record.id)}`, {method:'PATCH', body:{name}});
        panel.renaming = null;
      }
      if (action === 'archive-confirm') {
        await request(`${base(book)}/${encode(record.id)}`, {method:'PATCH', body:{archived:true}});
        panel.archiving = null;
        if (panel.options.listen?.getPerformance?.(book)?.id === record.id) panel.options.onLeave?.();
      }
      if (action === 'resume') {
        const result = await request(`${base(book)}/${encode(record.id)}/prepare`, {method:'POST', body:{}});
        if (result.job) panel.options.onJob?.({...result.job, book_id:book.id, kind:'performance'});
      }
      if (action === 'stop' && record.job?.id) await request(`/api/jobs/${encode(record.job.id)}/cancel`, {method:'POST', body:{}});
      // The player waits for passages only while it knows the job is running.
      if (['resume','stop'].includes(action) && panel.options.listen?.getPerformance?.(book)?.id === record.id) void panel.options.listen.refreshPerformance(book).catch(() => {});
      await refresh(panel);
    } catch (error) { panel.error = error.message; paint(panel); }
  }
  // Record what is missing, first adding the chosen chapters to this same performance.
  async function recordMore(panel) {
    const detail = panel.detail, book = panel.book;
    if (panel.recording || !detail) return;
    const chapter_ids = book.chapters.filter(chapter => detail.add.has(chapter.id)).map(chapter => chapter.id);
    panel.recording = true; detail.error = ''; paint(panel);
    try {
      const url = `${base(book)}/${encode(detail.id)}`;
      const result = chapter_ids.length ? await request(`${url}/chapters`, {method:'POST', body:{chapter_ids}}) : await request(`${url}/prepare`, {method:'POST', body:{}});
      if (result.job) panel.options.onJob?.({...result.job, book_id:book.id, kind:'performance'});
      detail.add = new Set(); detail.previewKey = null;
      // A player following this performance learns its new chapters and that its job is running.
      if (panel.options.listen?.getPerformance?.(book)?.id === detail.id) void panel.options.listen.refreshPerformance(book).catch(() => {});
      await refresh(panel);
    } catch (error) { detail.error = error.message; }
    finally { panel.recording = false; paint(panel); }
  }
  async function create(panel) {
    if (panel.creating) return;
    panel.creating = true; panel.formError = ''; paint(panel);
    try {
      const result = await request(base(panel.book), {method:'POST', body:formBody(panel)});
      if (result.job) panel.options.onJob?.({...result.job, book_id:panel.book.id, kind:'performance'});
      panel.view = 'list';
      await refresh(panel);
    } catch (error) { panel.formError = error.message; }
    finally { panel.creating = false; paint(panel); }
  }

  function render(container, book, options = {}) {
    if (!container) return;
    let panel = panels.get(container);
    if (!panel) {
      panel = {container, book:null, list:null, view:'list', html:''};
      panels.set(container, panel);
      container.addEventListener('click', event => {
        const target = event.target.closest('[data-performance-action],[data-performance-mode],[data-performance-provider]');
        if (!target) return;
        const {form} = panel;
        if (target.dataset.performanceMode) { form.mode = target.dataset.performanceMode; paint(panel); void preview(panel); return; }
        if (target.dataset.performanceProvider) {
          if (form.mode === 'cast') form.castProvider = target.dataset.performanceProvider; else form.provider = target.dataset.performanceProvider;
          paint(panel); void preview(panel); return;
        }
        const id = target.closest('[data-performance]')?.dataset.performance;
        void act(panel, target.dataset.performanceAction, panel.list?.find(record => record.id === id));
      });
      container.addEventListener('change', event => {
        const {form} = panel;
        if (panel.view === 'detail' && panel.detail && event.target.dataset.performanceChapter) {
          const id = event.target.dataset.performanceChapter;
          if (event.target.checked) panel.detail.add.add(id); else panel.detail.add.delete(id);
          paint(panel); void previewDetail(panel); return;
        }
        if (!form) return;
        const chapter = event.target.dataset.performanceChapter;
        if (chapter) { if (event.target.checked) form.chapters.add(chapter); else form.chapters.delete(chapter); }
        else if (event.target.dataset.performanceField === 'voice') form.voices[form.provider] = event.target.value;
        else if (event.target.dataset.performanceField === 'all-voices') { panel.options.listen?.setShowAllVoices?.(panel.book, event.target.checked); paint(panel); return; }
        else return;
        paint(panel); void preview(panel);
      });
      container.addEventListener('input', event => { if (event.target.dataset.performanceField === 'name' && panel.form) panel.form.name = event.target.value; });
      container.addEventListener('submit', event => { event.preventDefault(); void create(panel); });
    }
    const changedBook = panel.book?.id !== book?.id;
    panel.book = book; panel.options = options;
    if (!book) { clearTimeout(panel.timer); container.innerHTML = ''; panel.html = ''; return; }
    if (changedBook) { panel.list = null; panel.view = 'list'; panel.form = null; panel.detail = null; panel.html = ''; }
    paint(panel);
    // Several places render the same panel in one turn; one read serves them all.
    if (!panel.refreshing) void refresh(panel);
  }
  window.BardicPerformances = {render};
})();
