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
  const HINTS = {content_blocked:'Gemini refused this text and does not say which passage. Record with One narrator instead: it retries in halves and has a device voice read what Gemini still refuses.',gemini_key_missing:'Add a Gemini API key in Providers & settings, or choose another narrator.',breeze_url_missing:'Add the Breeze server URL in Providers & settings, or choose another narrator.',narrator_voice_missing:'Choose a narrator voice in Cast first.',
    narrator_unavailable:'That narrator is not set up. Add its key or server in Providers & settings, or choose another.',narrator_voice_invalid:'Choose a voice from the list for that service.',
    fallback_unsupported:'Choose another fallback narrator.',nothing_to_rerecord:'Nothing in this choice can be re-recorded. Choose another chapter or passages.',
    scope_too_large:'Re-record a smaller part, such as one chapter.',model_unsupported:'That model cannot read with this voice. Choose another.',
    take_stale:'The passage has changed since that take was made. Reload the takes and choose another.',take_not_playable:'That take has no playable audio. Choose another.',
    take_missing:'The audio for that take is gone. Choose another.',restore_ambiguous:'Choose one passage at a time.',
    job_active:'Let the recording finish or stop it first.',book_archived:'The book is listed under Removed items.'};
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
    return {name:'', mode:'simple', provider, voices:{}, castProvider:options.castProvider || provider, chapters:defaultChapters(book), fallback:{provider:'', voices:{}}};
  }
  function formBody(panel) {
    const {form, book} = panel;
    const provider = form.mode === 'cast' ? form.castProvider : form.provider;
    const narrator = panel.options.listen?.narratorOptions?.(book, provider);
    const voice = form.mode === 'cast' ? null : form.voices[provider] ?? narrator?.voice ?? '';
    const body = {name:form.name.trim() || undefined, mode:form.mode, provider, voice,
      model:provider === 'gemini' ? narrator?.model || null : null,
      chapter_ids:book.chapters.filter(chapter => form.chapters.has(chapter.id)).map(chapter => chapter.id)};
    // The saved default is pinned unless the user picks another fallback here.
    if (form.fallback?.provider) body.fallback = pickedNarrator(panel, form.fallback.provider, form.fallback.voices);
    return body;
  }
  // The provider and voice a picker shows: the chosen voice, else that service's default.
  function pickedNarrator(panel, provider, voices = {}) {
    const narrator = panel.options.listen?.narratorOptions?.(panel.book, provider);
    return {provider, voice:voices[provider] ?? narrator?.voice ?? ''};
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
    // The open performance's status follows the same 3-second cycle as the list, so one timer serves both.
    if (panel.view === 'detail') { void previewDetail(panel); void loadStatus(panel); }
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

  // The detailed status of the open performance: per-chapter progress, the estimate and the notes. A local read.
  async function loadStatus(panel) {
    const detail = panel.detail, book = panel.book;
    if (!detail) return;
    const seq = detail.statusSeq = (detail.statusSeq || 0) + 1;
    try {
      const result = await request(`${base(book)}/${encode(detail.id)}/status`);
      if (panel.detail !== detail || detail.statusSeq !== seq) return;
      detail.status = Array.isArray(result?.chapters) ? result : null; detail.statusError = '';
    } catch (error) { if (panel.detail !== detail || detail.statusSeq !== seq) return; detail.statusError = error.message; }
    paint(panel);
  }
  // Leaving the detail view drops everything its late responses and timers could still touch.
  function leaveDetail(panel) {
    clearTimeout(panel.detail?.rerecord?.timer);
    stopTake(panel);
    panel.detail = null;
  }

  // Voices that read passages Gemini blocked, as the sentence "read by ...".
  const FALLBACK_VOICES = {system:'a Mac voice', breeze:'Breeze', gemini:'a Gemini voice'};
  // Nothing left to record: every passage is ready, or blocked by Gemini (which is not requested again).
  const settled = progress => progress.passages_total > 0 && progress.passages_ready + (progress.passages_blocked || 0) >= progress.passages_total;
  // What another narrator did for this performance, in words: '' when nothing was swapped or blocked.
  // Automatic fallbacks say why (Gemini blocked the text, or the main narrator could not produce it);
  // records without `fallback_reasons` predate the split and only ever meant blocked text.
  function blockedNote(progress = {}) {
    const read = progress.passages_fallback || 0, left = progress.passages_blocked || 0, again = progress.passages_rerecorded || 0, parts = [];
    if (read) {
      const reasons = progress.fallback_reasons, voice = FALLBACK_VOICES[progress.fallback_provider] || 'a fallback voice';
      const blocked = reasons ? reasons.content_blocked || 0 : read, failed = reasons ? reasons.failed || 0 : 0, other = Math.max(0, read - blocked - failed);
      const them = count => count === 1 ? 'it' : 'them';
      if (blocked) parts.push(`${plural(blocked, 'passage')} read by ${voice} because Gemini blocked ${them(blocked)}`);
      if (failed) parts.push(`${plural(failed, 'passage')} read by ${voice} because the main narrator could not produce ${them(failed)}`);
      if (other) parts.push(`${plural(other, 'passage')} read by ${voice}`);
    }
    if (again) parts.push(`${plural(again, 'passage')} re-recorded with another voice`);
    if (left) parts.push(`${plural(left, 'passage')} blocked by Gemini\u2019s content policy and not recorded`);
    return parts.join('. ');
  }
  // The failure text with the hint for its documented cause, when it has one.
  const failure = job => `${job.error}${HINTS[job.error_code] ? ` ${HINTS[job.error_code]}` : ''}`;
  // One state per performance: an active job wins, then a complete recording, then how the last job ended.
  function stateOf(record) {
    const job = record.job, progress = record.progress || {};
    const complete = progress.passages_total > 0 && progress.passages_ready >= progress.passages_total;
    if (job?.status === 'running') return {key:'running', label:'Recording'};
    if (job?.status === 'queued') return {key:'queued', label:'Queued'};
    // Complete is only plain Gemini-complete when no passage was read by another narrator or blocked.
    if (settled(progress) && progress.passages_blocked) return {key:'blocked', label:`Blocked by Gemini \u00b7 ${progress.passages_blocked}`};
    if (complete && progress.passages_fallback) return {key:'read_by_fallback', label:'Complete \u00b7 fallback voice'};
    if (complete) return {key:'complete', label:'Complete'};
    const ended = {quota_limited:['quota_limited','Paused · daily limit'], budget_limited:['budget_limited','Paused · spending limit'],
      failed:['failed','Stopped on an error'], cancelled:['cancelled','Stopped by you'], interrupted:['interrupted','Interrupted']}[job?.status];
    if (ended) return {key:ended[0], label:ended[1]};
    return progress.passages_ready ? {key:'partial', label:'Partly recorded'} : {key:'not_started', label:'Not recorded yet'};
  }
  function statusLine(record) {
    const job = record.job, progress = record.progress || {}, note = blockedNote(progress);
    const ready = `${progress.passages_ready ?? 0} of ${progress.passages_total ?? 0} passages ready`;
    const head = ACTIVE.has(job?.status) ? `${ready}${job.message ? ` · ${job.message}` : ''}`
      : job?.status === 'quota_limited' ? `Paused at the daily request limit · ${ready}`
      : job?.status === 'budget_limited' ? `Paused at the spending limit · ${ready}`
      : job?.status === 'failed' ? `Stopped on an error · ${ready}`
      : job?.status === 'cancelled' ? `Stopped by you · ${ready}`
      : job?.status === 'interrupted' ? `Interrupted when Bardic stopped · ${ready}`
      : progress.passages_total && progress.passages_ready >= progress.passages_total ? `Ready · ${span(progress.seconds_ready)} of listening` : ready;
    return note ? `${head} · ${note}` : head;
  }
  // "3 of 5 chapters complete" from the per-chapter coverage.
  function coverage(record) {
    const rows = record.progress?.chapters || [];
    const done = rows.filter(row => row.passages_total > 0 && row.passages_ready >= row.passages_total).length;
    return rows.length ? `${done} of ${plural(rows.length, 'chapter')} complete` : plural((record.chapter_ids || []).length, 'chapter');
  }
  function cardMarkup(panel, record) {
    const progress = record.progress || {}, job = record.job;
    const complete = settled(progress);
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
      <p class="performance-status">${escape(statusLine(record))}${job?.error && !running ? ` <span class="performance-error">${escape(failure(job))}</span>` : ''}</p>
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
  // ---- Detailed status: estimate, chapters, run problems, and the passages another narrator reads ----
  const CHAPTER_STATES = {done:'Done', active:'Recording', queued:'Queued', paused:'Paused', stopped:'Stopped', blocked:'Blocked', partial:'Partly recorded', not_started:'Not recorded', empty:'No text'};
  const NOTE_REASONS = {content_blocked:'Gemini blocked this text', failed:'The main narrator could not produce it', rerecord:'Re-recorded with another voice'};
  const ISSUE_REASONS = {content_blocked:'Gemini blocked this text', failed:'The narrators kept failing on it'};
  const NOTE_LIMIT = 50;
  const PICKABLE = ['system','gemini','breeze'];
  // A moment as a person reads it: the time alone when it is today, else with the date.
  function when(iso) {
    const date = iso ? new Date(iso) : null;
    if (!date || Number.isNaN(date.getTime())) return '';
    return date.toDateString() === new Date().toDateString() ? date.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'}) : date.toLocaleString([], {dateStyle:'medium', timeStyle:'short'});
  }
  function shortSpan(seconds) { const value = Math.round(Number(seconds) || 0); return value < 60 ? `${Math.max(1, value)} s` : span(value); }
  // The sentence under the overall bar. `basis` says how far to trust the number.
  function etaSentence(eta = {}) {
    const left = Number.isFinite(eta.seconds) ? span(eta.seconds) : '', resumes = when(eta.resumes_at);
    if (eta.paused_reason === 'quota') return `Paused at the daily Gemini limit${resumes ? ` · resumes ${resumes}` : ''}${left ? ` · about ${left} of recording left after that` : ''}`;
    if (eta.paused_reason === 'budget') return `Paused at the spending limit${left ? ` · about ${left} of recording left` : ''}`;
    if (eta.basis === 'none') return 'Nothing left to record';
    if (eta.basis === 'measured') return `${left ? `About ${left} left` : 'Time left unknown'} · measured from this run`;
    if (eta.basis === 'estimated') return `${left ? `About ${left} left` : 'Time left unknown'} · estimated from earlier chunks`;
    return `Speed not measured yet${eta.note ? ` · ${eta.note}` : ''}`;
  }
  const chapterName = (book, id) => book.chapters.find(chapter => chapter.id === id)?.title || 'Untitled chapter';
  const passageText = (book, id) => book.passages.find(segment => segment.id === id)?.text || '';
  const clip = (text, size = 140) => { const line = String(text || '').replace(/\s+/g, ' ').trim(); return line.length > size ? `${line.slice(0, size - 1)}…` : line; };
  const voiceOf = note => [note.provider ? PROVIDER_LABELS[note.provider] || note.provider : '', note.voice].filter(Boolean).join(' · ');
  // "3 of 5 ready · 2 read by a fallback voice · 1 re-recorded · 4,200 characters · done in about 12 min"
  function chapterCounts(row, run) {
    const again = run?.kind === 'rerecord' && row.run_passages != null;
    return [`${row.passages_ready} of ${row.passages_total} ready`,
      row.passages_fallback ? `${row.passages_fallback} read by a fallback voice` : '',
      row.passages_rerecorded ? `${row.passages_rerecorded} re-recorded` : '',
      row.passages_blocked ? `${row.passages_blocked} blocked by Gemini` : '',
      again ? `re-recording ${row.run_done ?? 0} of ${row.run_passages}` : '',
      `${UI.fmt.number(row.chars_total, {digits:0})} characters`,
      row.eta_seconds > 0 ? `done in about ${span(row.eta_seconds)}` : ''].filter(Boolean).join(' · ');
  }
  function chapterRow(row, book, run, running) {
    const title = row.title || chapterName(book, row.id);
    const label = row.state === 'active' && run?.kind === 'rerecord' ? 'Re-recording' : CHAPTER_STATES[row.state] || row.state;
    return `<div class="performance-progress-row" data-chapter-row="${escape(row.id)}" data-state="${escape(row.state)}">
      <div class="performance-progress-head"><strong>${escape(title)}</strong>${UI.statusBadge(row.state, label)}</div>
      <progress max="${row.passages_total || 1}" value="${row.passages_ready || 0}" aria-label="${escape(`${title}: ${row.passages_ready} of ${row.passages_total} passages ready`)}"></progress>
      <small>${escape(chapterCounts(row, run))}</small>
      ${row.passages_total ? `<button type="button" class="button text-button" data-performance-action="rerecord-chapter" data-chapter="${escape(row.id)}" ${running ? 'disabled' : ''}>Re-record…</button>` : ''}
    </div>`;
  }
  function issuesMarkup(panel, status, running) {
    const issues = status.run?.issues || [];
    if (!issues.length) return '';
    const items = issues.map(issue => {
      const text = clip(passageText(panel.book, issue.passage_id));
      return `<li><strong>${escape(chapterName(panel.book, issue.chapter_id))}</strong> · ${escape(ISSUE_REASONS[issue.reason] || issue.reason)}${issue.message ? `<small>${escape(issue.message)}</small>` : ''}${text ? `<small>“${escape(text)}”</small>` : ''}
        <button type="button" class="button text-button" data-performance-action="rerecord-note" data-segment="${escape(issue.passage_id)}" ${running ? 'disabled' : ''}>Record with another voice…</button></li>`;
    }).join('');
    return UI.callout({tone:'bad', title:`${plural(issues.length, 'passage')} no narrator could record`, html:`<ul class="performance-notes">${items}</ul>`});
  }
  function notesMarkup(panel, status, running) {
    const notes = status.notes || [];
    if (!notes.length) return '';
    const open = Boolean(panel.detail.notesOpen);
    const items = notes.slice(0, NOTE_LIMIT).map(note => `<li class="performance-note" data-note="${escape(note.passage_id)}">
      <p>“${escape(clip(note.excerpt))}”</p>
      <small>${escape([chapterName(panel.book, note.chapter_id), NOTE_REASONS[note.reason] || note.reason, voiceOf(note)].filter(Boolean).join(' · '))}</small>
      <span class="performance-actions">
        <button type="button" class="button subtle" data-performance-action="rerecord-note" data-segment="${escape(note.passage_id)}" ${running ? 'disabled' : ''}>Re-record…</button>
        <button type="button" class="button subtle" data-performance-action="restore-original" data-segment="${escape(note.passage_id)}" ${running ? 'disabled' : ''}>Use original</button>
        <button type="button" class="button text-button" data-performance-action="takes" data-segment="${escape(note.passage_id)}">Takes</button>
      </span></li>`).join('');
    return `<div class="performance-notes-section"><button type="button" class="button text-button" data-performance-action="toggle-notes" aria-expanded="${open}" aria-controls="performance-notes-list">Passages another narrator reads (${notes.length}) ${open ? '▾' : '▸'}</button>
      ${open ? `<div id="performance-notes-list"><ul class="performance-notes">${items}</ul>${notes.length > NOTE_LIMIT ? `<p class="field-help">Showing the first ${NOTE_LIMIT} of ${notes.length}.</p>` : ''}</div>` : ''}</div>`;
  }
  function statusMarkup(panel, running) {
    const detail = panel.detail, status = detail.status;
    const failed = detail.statusError ? `<p class="inline-error" role="alert">${escape(detail.statusError)}</p>` : '';
    if (!status) return failed || '<p class="field-help">Loading detailed status…</p>';
    const totals = status.totals || {}, run = status.run, eta = status.eta || {};
    const active = run && ACTIVE.has(run.status);
    const line = active ? `${run.kind === 'rerecord' ? 'Re-recording' : 'Recording'}${run.narrator_label ? ` with ${run.narrator_label}` : ''}${run.message ? ` · ${run.message}` : ''}${run.waiting_seconds > 0 ? ` · waiting ${shortSpan(run.waiting_seconds)} for the rate limit` : ''}` : '';
    const paid = Number.isFinite(eta.requests_remaining) ? `about ${plural(eta.requests_remaining, 'request')} left${Number.isFinite(eta.requests_left_today) ? ` · ${eta.requests_left_today} left today` : ''}` : '';
    return `<div class="performance-overall">
      <progress max="${totals.passages_total || 1}" value="${totals.passages_ready || 0}" aria-label="${escape(`All chapters: ${totals.passages_ready || 0} of ${totals.passages_total || 0} passages ready`)}"></progress>
      <p class="performance-eta" role="status" aria-live="polite" aria-atomic="true">${escape(etaSentence(eta))}</p>
      ${eta.note && eta.basis !== 'unknown' ? `<small>${escape(eta.note)}</small>` : ''}${paid ? `<small>${escape(paid)}</small>` : ''}
      ${line ? `<p class="performance-run">${escape(line)}</p>` : ''}</div>${failed}${issuesMarkup(panel, status, running)}`;
  }
  const voiceOptions = (narrator, voice) => (narrator?.voices || []).map(item => `<option value="${escape(item.id)}" ${item.id === voice ? 'selected' : ''} ${item.usable || item.id === voice ? '' : 'disabled'}>${escape(item.name)}${item.locale ? ` · ${escape(item.locale)}` : ''}</option>`).join('');

  // ---- Re-record: read chosen passages with another voice; earlier audio stays as a take ----
  function rerecordScope(panel, kind, id) {
    const {book} = panel;
    if (kind === 'chapter') return {chapter_id:id, label:`Chapter “${chapterName(book, id)}”`};
    if (kind === 'passage') {
      const note = panel.detail.status?.notes?.find(item => item.passage_id === id);
      return {passage_ids:[id], label:`One passage: “${clip(note?.excerpt || passageText(book, id), 80) || 'text'}”`};
    }
    return {only:'fallback', label:'Every passage another narrator reads'};
  }
  function openRerecord(panel, scope) {
    const detail = panel.detail, selection = panel.options.listen?.getSelection?.(panel.book);
    clearTimeout(detail.rerecord?.timer);
    detail.rerecord = {scope, provider:PICKABLE.includes(selection?.provider) ? selection.provider : 'system', voices:{}, preview:null, previewKey:'', error:'', starting:false, timer:0};
    detail.takes = null; detail.notice = ''; stopTake(panel);
    paint(panel); void previewRerecord(panel);
  }
  function rerecordBody(panel) {
    const rr = panel.detail.rerecord, narrator = panel.options.listen?.narratorOptions?.(panel.book, rr.provider);
    const {chapter_id, passage_ids, only} = rr.scope;
    return {provider:rr.provider, voice:rr.voices[rr.provider] ?? narrator?.voice ?? null, model:rr.provider === 'gemini' ? narrator?.model || null : null,
      ...(chapter_id ? {chapter_id} : {}), ...(passage_ids ? {passage_ids} : {}), only:only || 'all'};
  }
  // What re-recording would do. Local: nothing is stored or sent until the confirm.
  function previewRerecord(panel) {
    const detail = panel.detail, rr = detail?.rerecord;
    if (!rr) return;
    const body = rerecordBody(panel), key = JSON.stringify(body);
    if (rr.previewKey === key) return;
    rr.previewKey = key; rr.preview = null; rr.error = '';
    clearTimeout(rr.timer);
    rr.timer = setTimeout(async () => {
      try {
        const result = await request(`${base(panel.book)}/${encode(detail.id)}/rerecord/preview`, {method:'POST', body});
        if (detail.rerecord === rr && rr.previewKey === key) rr.preview = result;
      } catch (error) { if (detail.rerecord === rr && rr.previewKey === key) rr.error = error.message; }
      if (panel.detail === detail) paint(panel);
    }, 250);
    paint(panel);
  }
  async function startRerecord(panel) {
    const detail = panel.detail, rr = detail?.rerecord, book = panel.book;
    if (!rr || rr.starting || !rr.preview) return;
    rr.starting = true; rr.error = ''; paint(panel);
    try {
      const result = await request(`${base(book)}/${encode(detail.id)}/rerecord`, {method:'POST', body:rerecordBody(panel)});
      if (result.job) panel.options.onJob?.({...result.job, book_id:book.id, kind:'performance'});
      if (panel.detail === detail) detail.rerecord = null;
      if (panel.options.listen?.getPerformance?.(book)?.id === detail.id) void panel.options.listen.refreshPerformance(book).catch(() => {});
      await refresh(panel);
    } catch (error) { rr.error = error.message; rr.starting = false; if (panel.detail === detail) paint(panel); }
  }
  function rerecordMarkup(panel, running) {
    const rr = panel.detail.rerecord, {listen} = panel.options;
    if (!rr) return '';
    const narrator = listen?.narratorOptions?.(panel.book, rr.provider), result = rr.preview;
    const voice = rr.voices[rr.provider] ?? narrator?.voice ?? '';
    const paid = rr.provider === 'gemini' && result?.passages_total ? ` · about ${plural(result.requests_estimate, 'paid request')} · cost unknown` : '';
    const summary = running ? 'Recording is in progress. Re-record after it finishes or stop it first.' : rr.error && !result ? '' : !result ? 'Checking what would be recorded…'
      : `${plural(result.passages_total, 'passage')} · ${plural(result.characters, 'character')} · about ${span(result.expected_seconds)} of listening${result.passages_total && rr.provider !== 'gemini' ? ' · no charge' : ''}`;
    const quota = result?.quota ? `This library has used ${result.quota.requests_today} of ${result.quota.rpd} Gemini requests today.` : '';
    const label = rr.starting ? 'Starting…' : `Re-record${result ? ` ${plural(result.passages_total, 'passage')}` : ''}${paid}`;
    return `<section class="performance-panel" aria-labelledby="rerecord-title">
      <h3 id="rerecord-title">Re-record with another voice</h3>
      <p class="field-help">${escape(rr.scope.label)}. Earlier audio is kept; you can go back to it from Takes.</p>
      <span class="field-label" id="rerecord-provider-label">Service</span>
      <div class="performance-providers" role="radiogroup" aria-labelledby="rerecord-provider-label">${PICKABLE.map(id => `<button type="button" role="radio" data-performance-rr-provider="${id}" aria-checked="${id === rr.provider}"><strong>${PROVIDER_LABELS[id]}</strong><small>${listen?.narratorOptions?.(panel.book, id)?.available === false ? 'Not set up' : id === 'gemini' ? 'Paid' : id === 'breeze' ? 'Your server · free' : 'Free'}</small></button>`).join('')}</div>
      <label class="field-label" for="rerecord-voice">Voice</label>
      <select id="rerecord-voice" data-performance-field="rr-voice">${voiceOptions(narrator, voice)}</select>
      <p class="performance-summary" role="status">${escape(summary)}</p>
      ${quota ? `<p class="field-help">${escape(quota)}</p>` : ''}
      ${(result?.notes || []).map(note => `<p class="field-help">${escape(note)}</p>`).join('')}
      ${(result?.problems || []).map(problem => `<p class="inline-error">${escape(problem.detail)}${HINTS[problem.code] ? ` ${escape(HINTS[problem.code])}` : ''}</p>`).join('')}
      ${rr.error ? `<p class="inline-error" role="alert">${escape(rr.error)}</p>` : ''}
      <div class="performance-form-actions"><button type="button" class="button subtle" data-performance-action="rerecord-cancel">Cancel</button>
        <button type="button" class="button primary" data-performance-action="rerecord-start" ${running || !result || result.problems?.length || !result.passages_total || rr.starting || narrator?.available === false ? 'disabled' : ''}>${escape(label)}</button></div>
    </section>`;
  }

  // ---- Takes: every retained version of a passage, and which one plays ----
  const TAKE_REASONS = {content_blocked:'Read after Gemini blocked the text', failed:'Read after the main narrator failed', rerecord:'Re-recorded', restore:'Restored'};
  function stopTake(panel) {
    const audio = panel.audio;
    panel.audio = null;
    if (audio) { try { audio.pause(); } catch { /* Already stopped. */ } }
    if (panel.detail?.takes) panel.detail.takes.playing = '';
  }
  function playTake(panel, take) {
    const takes = panel.detail?.takes;
    if (!takes) return;
    const same = takes.playing === take.id;
    stopTake(panel);
    if (same || !take.audio?.url || typeof Audio === 'undefined') { paint(panel); return; }
    // A separate element: the performance player is neither started nor interrupted here.
    const audio = new Audio(take.audio.url);
    panel.audio = audio; takes.playing = take.id; takes.error = '';
    const done = message => { if (panel.audio !== audio) return; stopTake(panel); if (message) takes.error = message; paint(panel); };
    audio.addEventListener('ended', () => done(''));
    audio.addEventListener('error', () => done('This take could not be played.'));
    Promise.resolve(audio.play()).catch(() => done('This take could not be played.'));
    paint(panel);
  }
  async function loadTakes(panel, passage_id) {
    const detail = panel.detail;
    if (!detail) return;
    const takes = detail.takes = {passage_id, loading:true, list:[], error:'', playing:''};
    stopTake(panel); detail.rerecord = null; paint(panel);
    try {
      const result = await request(`${base(panel.book)}/${encode(detail.id)}/takes?passage_id=${encode(passage_id)}`);
      if (detail.takes === takes) takes.list = result.takes || [];
    } catch (error) { if (detail.takes === takes) takes.error = error.message; }
    if (detail.takes === takes) { takes.loading = false; if (panel.detail === detail) paint(panel); }
  }
  // Choose which take plays: a take id, or none for the performance's own audio.
  async function restore(panel, passage_ids, take_id) {
    const detail = panel.detail, book = panel.book;
    if (!detail || detail.restoring) return;
    detail.restoring = true; detail.error = ''; detail.notice = ''; paint(panel);
    try {
      await request(`${base(book)}/${encode(detail.id)}/takes/restore`, {method:'POST', body:take_id ? {passage_ids, take_id} : {passage_ids}});
      detail.notice = take_id ? 'That take now plays for this passage.' : 'This passage plays the performance’s own audio again.';
      if (panel.options.listen?.getPerformance?.(book)?.id === detail.id) void panel.options.listen.refreshPerformance(book).catch(() => {});
      if (detail.takes && passage_ids.includes(detail.takes.passage_id)) await loadTakes(panel, detail.takes.passage_id);
      await refresh(panel);
    } catch (error) { detail.error = error.message; }
    finally { detail.restoring = false; if (panel.detail === detail) paint(panel); }
  }
  function takesMarkup(panel, running) {
    const takes = panel.detail.takes;
    if (!takes) return '';
    const text = clip(panel.detail.status?.notes?.find(note => note.passage_id === takes.passage_id)?.excerpt || passageText(panel.book, takes.passage_id), 120);
    const original = !takes.list.some(take => take.current && take.action === 'use');
    const rows = takes.list.map(take => {
      const label = take.action === 'original' ? 'The performance’s own audio' : take.voice_label || voiceOf(take) || 'A take';
      const time = take.created_at ? new Date(take.created_at) : null;
      return `<li class="performance-note" data-take-row="${escape(take.id)}"><p><strong>${escape(label)}</strong>${take.current ? ` ${UI.statusBadge('current', 'Playing now')}` : ''}</p>
        <small>${escape([TAKE_REASONS[take.reason] || take.reason, time && !Number.isNaN(time.getTime()) ? time.toLocaleString([], {dateStyle:'medium', timeStyle:'short'}) : ''].filter(Boolean).join(' · '))}${take.available === false ? ' · audio not available' : ''}</small>
        <span class="performance-actions">
          ${take.action === 'use' && take.available !== false && take.audio?.url ? `<button type="button" class="button subtle" data-performance-action="take-play" data-take="${escape(take.id)}" aria-pressed="${takes.playing === take.id}">${takes.playing === take.id ? 'Stop preview' : 'Play take'}</button>` : ''}
          ${take.action === 'use' && !take.current && take.available !== false ? `<button type="button" class="button subtle" data-performance-action="take-use" data-segment="${escape(takes.passage_id)}" data-take="${escape(take.id)}" ${running ? 'disabled' : ''}>Use this take</button>` : ''}
        </span></li>`;
    }).join('');
    return `<section class="performance-panel" aria-labelledby="takes-title"><h3 id="takes-title">Takes for this passage</h3>
      ${text ? `<p class="field-help">“${escape(text)}”</p>` : ''}
      ${takes.loading ? '<p class="field-help">Loading takes…</p>' : rows ? `<ul class="performance-notes">${rows}</ul>` : '<p class="field-help">This passage has only its own audio.</p>'}
      ${takes.error ? `<p class="inline-error" role="alert">${escape(takes.error)}</p>` : ''}
      <div class="performance-form-actions"><button type="button" class="button subtle" data-performance-action="takes-close">Close</button>
        <button type="button" class="button primary" data-performance-action="take-original" data-segment="${escape(takes.passage_id)}" ${running || original ? 'disabled' : ''}>Use the original</button></div>
    </section>`;
  }

  // ---- The fallback narrator: a saved performance pins one; resuming can change it ----
  function narratorPicker(panel, {providerId, voiceId, current, disabled, keep}) {
    const {listen} = panel.options;
    const narrator = current?.provider ? listen?.narratorOptions?.(panel.book, current.provider) : null;
    const voice = current?.voices?.[current.provider] ?? narrator?.voice ?? '';
    return `<select id="${providerId}" data-performance-field="${providerId}" aria-label="Fallback service" ${disabled ? 'disabled' : ''}><option value="">${escape(keep)}</option>${PICKABLE.map(id => `<option value="${id}" ${current?.provider === id ? 'selected' : ''}>${escape(PROVIDER_LABELS[id])}${listen?.narratorOptions?.(panel.book, id)?.available === false ? ' (not set up)' : ''}</option>`).join('')}</select>
      ${current?.provider ? `<select id="${voiceId}" data-performance-field="${voiceId}" aria-label="Fallback voice" ${disabled ? 'disabled' : ''}>${voiceOptions(narrator, voice)}</select>` : ''}`;
  }
  function fallbackMarkup(panel, record, running, chosen) {
    const detail = panel.detail, fb = record.fallback;
    const line = fb ? `Fallback narrator: ${fb.label}${fb.automatic ? ' (chosen automatically)' : ''}` : 'No fallback narrator is available, so a passage the main narrator cannot read stays unrecorded.';
    return `<div class="performance-fallback"><p><strong>${escape(line)}</strong></p>
      ${fb && fb.available === false ? '<p class="inline-error">That narrator is not set up right now. Add its key or server in Providers &amp; settings, or choose another.</p>' : ''}
      <p class="field-help">Reads any passage the main narrator cannot: text Gemini blocks, or passages that keep failing. Each is marked so you can re-record it.</p>
      ${detail.fallbackOpen ? `<div class="performance-fallback-pick">${narratorPicker(panel, {providerId:'detail-fallback-provider', voiceId:'detail-fallback-voice', current:detail.fallback, disabled:running || chosen > 0, keep:'Keep the current fallback'})}</div>
        <p class="field-help">${chosen ? 'Record the remaining passages first to change the fallback; adding chapters keeps the current one.' : 'The change applies when you record the remaining passages.'}</p>`
        : `<button type="button" class="button text-button" data-performance-action="fallback-change" ${running ? 'disabled' : ''}>Change fallback narrator</button>`}</div>`;
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
    const counts = book.passages.reduce((map, segment) => map.set(segment.chapter_id, (map.get(segment.chapter_id) || 0) + 1), new Map());
    const chosen = detail.add.size;
    const status = detail.status, statusRows = new Map((status?.chapters || []).map(row => [row.id, row]));
    const rows = book.chapters.map(chapter => {
      const row = inside.get(chapter.id);
      const title = escape(chapter.title || 'Untitled chapter');
      if (statusRows.has(chapter.id)) return chapterRow(statusRows.get(chapter.id), book, status.run, running);
      if (row) {
        const done = row.passages_total > 0 && row.passages_ready >= row.passages_total;
        const extra = [row.passages_fallback ? `${row.passages_fallback} read by a fallback voice` : '', row.passages_blocked ? `${row.passages_blocked} blocked by Gemini` : ''].filter(Boolean).join(' · ');
        return `<label class="performance-chapter-row" data-included="true"><input type="checkbox" checked disabled aria-label="${title} is in this performance"><span>${title}</span><small>${escape((done ? 'Complete' : `${row.passages_ready}/${row.passages_total} ready`) + (extra ? ` · ${extra}` : ''))}</small></label>`;
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
    const newFallback = !chosen && detail.fallback?.provider;
    const label = chosen ? `Add ${plural(chosen, 'chapter')} and record` : toRecord ? `Record the remaining ${plural(toRecord, 'passage')}` : newFallback ? 'Save the fallback narrator' : 'Everything is recorded';
    const disabled = running || !result || result.problems?.length || (!chosen && !toRecord && !newFallback) || panel.recording;
    const readByOthers = (status?.notes || []).some(note => note.reason !== 'rerecord');
    const cast = (record.cast || []).map(member => `<li><strong>${escape(member.name)}</strong> <small>${escape(member.voice_label)}${member.fallback ? ' · uses the narrator' : ''}</small></li>`).join('');
    const updated = record.updated_at ? new Date(record.updated_at) : null;
    return `<div class="performance-detail" data-performance="${escape(record.id)}" data-state="${state.key}">
      <div class="performance-detail-head">${back}</div>
      <div class="performance-card-head"><div><strong>${escape(record.name)}</strong><small>${escape(record.narrator_label || '')} · ${escape(coverage(record))}${current ? ' · playing now' : ''}</small></div>${UI.statusBadge(state.key, state.label)}</div>
      ${status ? '' : `<progress max="${progress.passages_total || 1}" value="${progress.passages_ready || 0}" aria-label="Passages ready"></progress>`}
      <p class="performance-status">${escape(statusLine(record))}${progress.seconds_ready ? ` · ${escape(span(progress.seconds_ready))} of listening ready` : ''}${updated && !Number.isNaN(updated.getTime()) ? ` · updated ${escape(updated.toLocaleString([], {dateStyle:'medium', timeStyle:'short'}))}` : ''}${job?.error && !running ? ` <span class="performance-error">${escape(failure(job))}</span>` : ''}</p>
      ${running ? '<p class="field-help">Recording continues while you listen. Playing sends no requests of its own: it plays what is ready, follows new passages as they land, and waits at the frontier for the next one.</p>' : ''}
      <div class="performance-actions">
        <button type="button" class="button primary" data-performance-action="play" ${progress.passages_ready ? '' : 'disabled'}>${current ? 'Continue' : running ? 'Listen while it records' : 'Play'}</button>
        ${running ? '<button type="button" class="button subtle" data-performance-action="stop">Stop recording</button>' : ''}
        ${readByOthers ? `<button type="button" class="button subtle" data-performance-action="rerecord-fallback" ${running ? 'disabled' : ''}>Re-record passages read by the fallback voice\u2026</button>` : ''}
      </div>
      ${statusMarkup(panel, running)}
      <div class="performance-chapters-head"><span class="field-label" id="performance-detail-chapters">Chapters</span><span>${remaining && !running ? '<button type="button" class="button text-button" data-performance-action="add-rest">Add every remaining chapter</button>' : ''}${chosen && !running ? '<button type="button" class="button text-button" data-performance-action="add-none">Clear</button>' : ''}</span></div>
      <div class="performance-chapters" role="group" aria-labelledby="performance-detail-chapters">${rows}</div>
      ${status ? notesMarkup(panel, status, running) : ''}
      ${detail.notice ? `<p class="performance-summary" role="status">${escape(detail.notice)}</p>` : ''}
      ${rerecordMarkup(panel, running)}${takesMarkup(panel, running)}
      ${fallbackMarkup(panel, record, running, chosen)}
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
    // The plan reports the fallback it would pin; while nothing is overridden that is the saved default.
    if (!form.fallback?.provider && result) panel.savedFallback = result.fallback?.label || 'none available';
    const savedFallback = panel.savedFallback || 'automatic';
    const counts = book.passages.reduce((map, segment) => map.set(segment.chapter_id, (map.get(segment.chapter_id) || 0) + 1), new Map());
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
      <span class="field-label" id="performance-fallback-label">Fallback narrator</span>
      <div class="performance-fallback-pick" role="group" aria-labelledby="performance-fallback-label">${narratorPicker(panel, {providerId:'fallback-provider', voiceId:'fallback-voice', current:form.fallback, keep:`Saved default (${savedFallback})`})}</div>
      <p class="field-help">Reads any passage the main narrator cannot: text Gemini blocks, or passages that keep failing. Each is marked so you can re-record it.</p>
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
    // A note's or chapter's own buttons share one action name, so the row they belong to narrows the match.
    const own = ['segment','chapter','take'].map(key => focused?.dataset?.[key] !== undefined ? `[data-${key}="${CSS.escape(focused.dataset[key])}"]` : '').join('');
    const selector = !focused ? null : focused.id ? `#${CSS.escape(focused.id)}`
      : ['performanceAction','performanceMode','performanceProvider','performanceRrProvider','performanceChapter'].map(key => focused.dataset[key] !== undefined
        ? `[data-${key.replace(/[A-Z]/g, c => `-${c.toLowerCase()}`)}="${CSS.escape(focused.dataset[key])}"]${key === 'performanceAction' ? own : ''}` : null).find(Boolean);
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

  async function act(panel, action, record, data = {}) {
    const book = panel.book;
    try {
      if (action === 'new') { panel.view = 'new'; panel.form = freshForm(panel); panel.preview = null; panel.previewKey = null; panel.formError = ''; paint(panel); void preview(panel); return; }
      if (action === 'cancel-new') { panel.view = 'list'; paint(panel); return; }
      if (action === 'all') { panel.form.chapters = new Set(book.chapters.map(chapter => chapter.id)); paint(panel); void preview(panel); return; }
      if (action === 'none') { panel.form.chapters = new Set(); paint(panel); void preview(panel); return; }
      if (action === 'back') { panel.view = 'list'; leaveDetail(panel); paint(panel); return; }
      if (panel.detail) {
        const detail = panel.detail, running = ACTIVE.has(panel.list?.find(item => item.id === detail.id)?.job?.status);
        if (action === 'toggle-notes') { detail.notesOpen = !detail.notesOpen; paint(panel); return; }
        if (action === 'rerecord-chapter' && !running) { openRerecord(panel, rerecordScope(panel, 'chapter', data.chapter)); return; }
        if (action === 'rerecord-note' && !running) { openRerecord(panel, rerecordScope(panel, 'passage', data.segment)); return; }
        if (action === 'rerecord-fallback' && !running) { openRerecord(panel, rerecordScope(panel, 'fallback')); return; }
        if (action === 'rerecord-cancel') { clearTimeout(detail.rerecord?.timer); detail.rerecord = null; paint(panel); return; }
        if (action === 'rerecord-start') { await startRerecord(panel); return; }
        if (action === 'takes') { detail.notice = ''; await loadTakes(panel, data.segment); return; }
        if (action === 'takes-close') { stopTake(panel); detail.takes = null; paint(panel); return; }
        if (action === 'take-play') { const take = detail.takes?.list.find(item => item.id === data.take); if (take) playTake(panel, take); return; }
        if (action === 'take-use' && !running) { await restore(panel, [data.segment], data.take); return; }
        if (action === 'take-original' && !running) { await restore(panel, [data.segment]); return; }
        if (action === 'restore-original' && !running) { await restore(panel, [data.segment]); return; }
        if (action === 'fallback-change') { detail.fallbackOpen = true; detail.fallback = detail.fallback || {provider:'', voices:{}}; paint(panel); return; }
      }
      if (action === 'add-rest' && panel.detail) {
        const inside = new Set(panel.list?.find(item => item.id === panel.detail.id)?.chapter_ids || []);
        panel.detail.add = new Set(book.chapters.filter(chapter => !inside.has(chapter.id) && !['front_matter','back_matter'].includes(chapter.kind)).map(chapter => chapter.id));
        paint(panel); void previewDetail(panel); return;
      }
      if (action === 'add-none' && panel.detail) { panel.detail.add = new Set(); paint(panel); void previewDetail(panel); return; }
      if (action === 'record-more' && panel.detail) { await recordMore(panel); return; }
      if (!record) return;
      if (action === 'open') {
        leaveDetail(panel);
        panel.view = 'detail'; panel.detail = {id:record.id, add:new Set(), preview:null, previewKey:null, error:'', status:null, statusError:'', notesOpen:false, rerecord:null, takes:null, notice:'', fallback:null, fallbackOpen:false};
        paint(panel); void previewDetail(panel); void loadStatus(panel); return;
      }
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
      // Only /prepare can change the pinned fallback; adding chapters keeps it.
      const fallback = !chapter_ids.length && detail.fallback?.provider ? pickedNarrator(panel, detail.fallback.provider, detail.fallback.voices) : null;
      const result = chapter_ids.length ? await request(`${url}/chapters`, {method:'POST', body:{chapter_ids}}) : await request(`${url}/prepare`, {method:'POST', body:fallback ? {fallback} : {}});
      if (fallback) { detail.fallback = null; detail.fallbackOpen = false; }
      if (result.job) panel.options.onJob?.({...result.job, book_id:book.id, kind:'performance'});
      detail.add = new Set(); detail.previewKey = null; detail.status = null;
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
        const target = event.target.closest('[data-performance-action],[data-performance-mode],[data-performance-provider],[data-performance-rr-provider]');
        if (!target) return;
        const {form} = panel;
        if (target.dataset.performanceRrProvider) {
          const rr = panel.detail?.rerecord;
          if (rr) { rr.provider = target.dataset.performanceRrProvider; paint(panel); void previewRerecord(panel); }
          return;
        }
        if (target.dataset.performanceMode) { form.mode = target.dataset.performanceMode; paint(panel); void preview(panel); return; }
        if (target.dataset.performanceProvider) {
          if (form.mode === 'cast') form.castProvider = target.dataset.performanceProvider; else form.provider = target.dataset.performanceProvider;
          paint(panel); void preview(panel); return;
        }
        const id = target.closest('[data-performance]')?.dataset.performance;
        void act(panel, target.dataset.performanceAction, panel.list?.find(record => record.id === id), target.dataset);
      });
      container.addEventListener('change', event => {
        const {form} = panel, field = event.target.dataset.performanceField, detail = panel.detail;
        if (panel.view === 'detail' && detail && field) {
          const rr = detail.rerecord, value = event.target.value;
          if (field === 'rr-voice' && rr) { rr.voices[rr.provider] = value; paint(panel); void previewRerecord(panel); }
          else if (field === 'detail-fallback-provider' && detail.fallback) { detail.fallback.provider = value; paint(panel); }
          else if (field === 'detail-fallback-voice' && detail.fallback) { detail.fallback.voices[detail.fallback.provider] = value; paint(panel); }
          return;
        }
        if (panel.view === 'new' && form && (field === 'fallback-provider' || field === 'fallback-voice')) {
          if (field === 'fallback-provider') form.fallback.provider = event.target.value; else form.fallback.voices[form.fallback.provider] = event.target.value;
          paint(panel); void preview(panel); return;
        }
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
    if (!book) { clearTimeout(panel.timer); clearTimeout(panel.previewTimer); leaveDetail(panel); container.innerHTML = ''; panel.html = ''; return; }
    if (changedBook) { clearTimeout(panel.timer); leaveDetail(panel); panel.list = null; panel.view = 'list'; panel.form = null; panel.html = ''; }
    paint(panel);
    // Several places render the same panel in one turn; one read serves them all.
    if (!panel.refreshing) void refresh(panel);
  }
  window.BardicPerformances = {render};
})();
