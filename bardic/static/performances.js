/* Saved performances: chosen chapters narrated ahead by one narrator or the
   cast, then played later without new requests. The server job does the
   work; this panel lists, previews, starts, resumes and plays them. */
(() => {
  'use strict';
  const panels = new WeakMap();
  const ACTIVE = new Set(['queued','running']);
  const PROVIDER_LABELS = {system:'Device', gemini:'Gemini', breeze:'Breeze'};
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const encode = value => encodeURIComponent(value);
  const base = book => `/api/books/${encode(book.id)}/performances`;
  function span(seconds) {
    const value = Math.max(0, Math.round(Number(seconds) || 0));
    if (value < 3600) return `${Math.max(1, Math.round(value / 60))} min`;
    return `${Math.floor(value / 3600)} h ${Math.round(value % 3600 / 60)} min`;
  }
  // Server details state the condition; the hint says where to fix it.
  const HINTS = {gemini_key_missing:'Add a Gemini API key in Settings, or choose another narrator.',breeze_url_missing:'Add the Breeze server URL in Settings, or choose another narrator.',narrator_voice_missing:'Choose a narrator voice in Cast first.'};
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
    try {
      const result = await request(base(book));
      if (panel.book !== book) return;
      panel.list = result.performances || [];
      panel.error = '';
      for (const record of panel.list) if (ACTIVE.has(record.job?.status)) panel.options.onJob?.({...record.job, book_id:book.id, kind:'performance'});
    } catch (error) { panel.error = error.message; }
    paint(panel);
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

  function statusLine(record) {
    const job = record.job, progress = record.progress || {};
    const ready = `${progress.passages_ready ?? 0} of ${progress.passages_total ?? 0} passages ready`;
    if (ACTIVE.has(job?.status)) return `Processing · ${job.message || ready}`;
    if (job?.status === 'quota_limited') return `Paused at the daily request limit · ${ready}`;
    if (job?.status === 'budget_limited') return `Paused at the spending allowance · ${ready}`;
    if (job?.status === 'failed') return `Stopped on an error · ${ready}`;
    if (['cancelled','interrupted'].includes(job?.status)) return `Stopped · ${ready}`;
    return progress.passages_total && progress.passages_ready >= progress.passages_total ? `Ready · ${span(progress.seconds_ready)} of listening` : ready;
  }
  function cardMarkup(panel, record) {
    const progress = record.progress || {}, job = record.job;
    const complete = progress.passages_total && progress.passages_ready >= progress.passages_total;
    const running = ACTIVE.has(job?.status);
    const chapters = (record.chapter_ids || []).length;
    const renaming = panel.renaming === record.id, archiving = panel.archiving === record.id;
    const current = panel.options.listen?.getPerformance?.(panel.book)?.id === record.id;
    return `<article class="performance-card${current ? ' is-current' : ''}" data-performance="${escape(record.id)}">
      <div class="performance-card-head">${renaming
        ? `<label class="sr-only" for="rename-${escape(record.id)}">Performance name</label><input id="rename-${escape(record.id)}" data-performance-name value="${escape(record.name)}" maxlength="120"><button type="button" class="button subtle" data-performance-action="save-name">Save</button>`
        : `<div><strong>${escape(record.name)}</strong><small>${escape(record.narrator_label || '')} · ${chapters} ${chapters === 1 ? 'chapter' : 'chapters'}${current ? ' · playing now' : ''}</small></div>`}</div>
      <progress max="${progress.passages_total || 1}" value="${progress.passages_ready || 0}" aria-label="Passages ready"></progress>
      <p class="performance-status">${escape(statusLine(record))}${job?.error && !running ? ` <span class="performance-error">${escape(job.error)}</span>` : ''}</p>
      ${archiving ? `<div class="performance-confirm" role="group" aria-label="Archive ${escape(record.name)}"><span>Archive this performance? Its audio is kept.</span><button type="button" class="button subtle" data-performance-action="archive-confirm">Archive</button><button type="button" class="button subtle" data-performance-action="archive-cancel">Keep</button></div>`
        : `<div class="performance-actions">
        <button type="button" class="button primary" data-performance-action="play" ${progress.passages_ready ? '' : 'disabled'}>${current ? 'Continue' : 'Play'}</button>
        ${running ? '<button type="button" class="button subtle" data-performance-action="stop">Stop processing</button>'
          : complete ? '' : '<button type="button" class="button subtle" data-performance-action="resume">Resume processing</button>'}
        <button type="button" class="button text-button" data-performance-action="rename">Rename</button>
        <button type="button" class="button text-button" data-performance-action="archive">Archive</button>
      </div>`}
    </article>`;
  }
  function listMarkup(panel) {
    const list = panel.list;
    const body = list === null ? '<p class="field-help">Loading performances…</p>'
      : list.length ? list.map(record => cardMarkup(panel, record)).join('')
      : '<p class="performance-empty">No performances yet. Make one to narrate chosen chapters ahead of time, with one narrator or the whole cast, and listen later with no waiting.</p>';
    return `<div class="performance-list-head"><p>Narrated ahead of time. Playing one never makes new requests.</p><button type="button" class="button primary" data-performance-action="new">New performance</button></div>
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
      : `${result.passages_total} passages · ${result.passages_ready} already saved · ${result.passages_to_generate} to narrate${result.passages_to_generate ? ` · about ${result.requests_estimate} ${result.requests_estimate === 1 ? 'request' : 'requests'}` : ''} · about ${span(result.expected_seconds)} of listening`;
    const quota = result?.quota ? `This library has used ${result.quota.requests_today} of ${result.quota.rpd} Gemini requests today. Processing pauses at the limit; resume after the reset.` : '';
    return `<form class="performance-form" data-performance-form novalidate>
      <label class="field-label" for="performance-name">Name</label>
      <input id="performance-name" data-performance-field="name" maxlength="120" placeholder="${escape(book.title || 'This book')} · ${escape(form.mode === 'cast' ? 'Full cast' : voice || 'Narrator')}" value="${escape(form.name)}">
      <span class="field-label" id="performance-mode-label">Voices</span>
      <div class="performance-segmented" role="radiogroup" aria-labelledby="performance-mode-label">
        <button type="button" role="radio" data-performance-mode="simple" aria-checked="${form.mode === 'simple'}"><strong>One narrator</strong><small>A single voice reads everything</small></button>
        <button type="button" role="radio" data-performance-mode="cast" aria-checked="${form.mode === 'cast'}"><strong>Full cast</strong><small>Each character's voice from Cast; unanalysed chapters use the narrator</small></button>
      </div>
      <span class="field-label" id="performance-provider-label">Narration</span>
      <div class="performance-providers" role="radiogroup" aria-labelledby="performance-provider-label">${['system','gemini','breeze'].map(id => {
        const option = listen?.narratorOptions?.(book, id);
        return `<button type="button" role="radio" data-performance-provider="${id}" aria-checked="${id === provider}"><strong>${PROVIDER_LABELS[id]}</strong><small>${option?.available === false ? 'Not set up' : id === 'gemini' ? 'Cloud' : id === 'breeze' ? 'Your server' : 'Free'}</small></button>`;
      }).join('')}</div>
      ${form.mode === 'simple' ? `<label class="field-label" for="performance-voice">Voice</label><select id="performance-voice" data-performance-field="voice">${(narrator?.voices || []).map(item => `<option value="${escape(item.id)}" ${item.id === voice ? 'selected' : ''} ${item.usable || item.id === voice ? '' : 'disabled'}>${escape(item.name)}${item.locale ? ` · ${escape(item.locale)}` : ''}</option>`).join('')}</select>` : ''}
      <div class="performance-chapters-head"><span class="field-label" id="performance-chapters-label">Chapters</span><span><button type="button" class="button text-button" data-performance-action="all">All</button><button type="button" class="button text-button" data-performance-action="none">None</button></span></div>
      <div class="performance-chapters" role="group" aria-labelledby="performance-chapters-label">${book.chapters.map(chapter => {
        const ready = result?.chapters?.find(item => item.id === chapter.id);
        return `<label><input type="checkbox" data-performance-chapter="${escape(chapter.id)}" ${form.chapters.has(chapter.id) ? 'checked' : ''}><span>${escape(chapter.title || 'Untitled section')}</span><small>${ready ? `${ready.passages_ready}/${ready.passages_total} saved` : `${counts.get(chapter.id) || 0} passages`}</small></label>`;
      }).join('')}</div>
      <p class="performance-summary" role="status">${escape(summary)}</p>
      ${quota ? `<p class="field-help">${escape(quota)}</p>` : ''}
      ${(result?.notes || []).map(note => `<p class="field-help">${escape(note)}</p>`).join('')}
      ${(result?.problems || []).map(problem => `<p class="inline-error">${escape(problem)}</p>`).join('')}
      ${panel.formError ? `<p class="inline-error" role="alert">${escape(panel.formError)}</p>` : ''}
      <div class="performance-form-actions"><button type="button" class="button subtle" data-performance-action="cancel-new">Cancel</button><button type="submit" class="button primary" ${!form.chapters.size || panel.creating || result?.problems?.length || narrator?.available === false ? 'disabled' : ''}>${panel.creating ? 'Starting…' : 'Create performance'}</button></div>
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
    panel.html = html;
    panel.container.innerHTML = html;
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
      if (!record) return;
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
        if (!form) return;
        const chapter = event.target.dataset.performanceChapter;
        if (chapter) { if (event.target.checked) form.chapters.add(chapter); else form.chapters.delete(chapter); }
        else if (event.target.dataset.performanceField === 'voice') form.voices[form.provider] = event.target.value;
        else return;
        paint(panel); void preview(panel);
      });
      container.addEventListener('input', event => { if (event.target.dataset.performanceField === 'name' && panel.form) panel.form.name = event.target.value; });
      container.addEventListener('submit', event => { event.preventDefault(); void create(panel); });
    }
    const changedBook = panel.book?.id !== book?.id;
    panel.book = book; panel.options = options;
    if (!book) { clearTimeout(panel.timer); container.innerHTML = ''; panel.html = ''; return; }
    if (changedBook) { panel.list = null; panel.view = 'list'; panel.form = null; panel.html = ''; }
    paint(panel);
    void refresh(panel);
  }
  window.BardicPerformances = {render};
})();
