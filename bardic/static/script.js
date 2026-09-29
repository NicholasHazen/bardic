/* Script & record: the performance script, one chapter at a time. Exposes window.BardicScript.
   app.js attaches its functions once (attach) and calls render() from renderStudio.

   - Needs a look: toggle chips (Unassigned speaker, Low confidence, BookNLP disagrees, Your edits,
     Not recorded) with counts for this chapter and the whole book. A passage shows when it matches
     any chip that is on. A passage you edit, or opened from Analyze, stays shown until the chips
     or the chapter change, so a fix does not make its row jump away.
   - Saves on change: a speaker saves when chosen (after a short pause, so arrowing through the menu
     sends only the final choice, or at once when focus leaves it); a performance note or scene direction saves a
     moment after you stop typing and when you leave the field. Each save sends only the field that
     changed (the server locks edited fields per field, so an unchanged value never becomes locked).
     Saves run one at a time. Unsaved text is kept across re-renders and chapter changes, a failed
     save stays on its row with Try again, and leaving the page with unsaved changes asks first.
   - Bulk: select passages (Select shown selects what the chips show) and assign one speaker; this
     sends the existing passage edit once per passage, in order, and reports partial failure.
   - bardic:show-passage (Analyze → Show in text): opens this tab at the passage's chapter with the
     passage in view and its speaker menu focused. The chips stay as they were.

   Nothing here starts narration or any paid request: Hear example, Record and Narrate scene keep
   their existing handlers in app.js (Hear example is one explicit example; Narrate scene and a
   paid Record go through the estimate and confirm there). */
(root => {
  'use strict';
  const ui = () => root.BardicUI;
  const esc = value => ui().esc(value);
  // Speaker attributions below 0.65 stay unassigned, and a BookNLP disagreement caps confidence at 0.65
  // (docs/ANALYSIS-PIPELINE.md). "Low" is at or below that floor, as in Analyze's result filters.
  const LOW_CONFIDENCE = .65;
  const SAVE_DELAY_MS = 1000;
  // A speaker menu can fire change on every arrow key (Windows, Linux): wait this long, or until focus leaves it.
  const SPEAKER_DELAY_MS = 400;
  const SAVED_FOR_MS = 4000;
  const UNASSIGNED = 'unassigned';

  let api = null;
  const view = {bookId:null, chapterId:null, filters:new Set(), pinned:new Set(), selected:new Set(), target:null,
    bulkSpeaker:'', bulk:null, open:new Set()};
  const edits = new Map();       // `${bookId}|${kind}|${id}|${field}` → {bookId, kind, id, field, value, state, error, timer}
  const rows = new Map();        // `${kind}:${id}` → {tone, text, retry, at}
  let queue = Promise.resolve();

  const node = selector => api?.$?.(selector) || null;
  const doc = () => root.document || null;
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const plural = (count, one, many = `${one}s`) => ui().fmt.plural(count, one, many);

  // ---- Checks ------------------------------------------------------------------------------------
  const isUnassigned = (segment, book) => !segment.speaker_id || segment.speaker_id === UNASSIGNED
    || !(book.characters || []).some(character => character.id === segment.speaker_id);
  const editedFields = segment => Array.isArray(segment.manual_fields) ? segment.manual_fields : [];
  const FILTERS = [
    {id:'unassigned', label:'Unassigned speaker', short:'unassigned', test:(segment, book) => isUnassigned(segment, book)},
    {id:'low-confidence', label:'Low confidence', short:'low confidence',
      test:segment => segment.kind === 'dialogue' && finite(segment.confidence) && segment.confidence <= LOW_CONFIDENCE},
    // Shown only when some passage in the book carries a BookNLP check.
    {id:'booknlp', label:'BookNLP disagrees', short:'BookNLP disagrees', test:segment => segment.speaker_check?.result === 'differs',
      available:book => (book.passages || []).some(segment => segment.speaker_check)},
    {id:'edited', label:'Your edits', short:'your edits', test:segment => editedFields(segment).length > 0},
    {id:'not-recorded', label:'Not recorded', short:'not recorded', test:(segment, book, playable) => !playable(segment)},
  ];
  const playableOf = () => api?.playable || (segment => Boolean(segment?.audio?.url));
  const filtersFor = book => FILTERS.filter(filter => !filter.available || filter.available(book));
  /** The chips a passage matches, by id. */
  function flags(segment, book, playable = playableOf()) {
    return FILTERS.filter(filter => filter.test(segment, book, playable)).map(filter => filter.id);
  }
  /** Per chip: how many passages match in the chapter and in the whole book. */
  function counts(book, chapterId, playable = playableOf()) {
    const out = {};
    for (const filter of filtersFor(book)) out[filter.id] = {chapter:0, book:0};
    for (const segment of book.passages || []) {
      for (const filter of filtersFor(book)) {
        if (!filter.test(segment, book, playable)) continue;
        out[filter.id].book++;
        if (segment.chapter_id === chapterId) out[filter.id].chapter++;
      }
    }
    return out;
  }
  /** Whether a passage shows under the active chips (any chip matches), or is pinned. */
  function matches(segment, book, active, playable = playableOf(), pinned = new Set()) {
    if (!active.size || pinned.has(segment.id)) return true;
    return FILTERS.some(filter => active.has(filter.id) && filter.test(segment, book, playable));
  }
  const chapterSegments = (book, chapterId) => (book.passages || []).filter(segment => segment.chapter_id === chapterId);
  function visibleSegments(book, chapterId, active = view.filters, pinned = view.pinned, playable = playableOf()) {
    return chapterSegments(book, chapterId).filter(segment => matches(segment, book, active, playable, pinned));
  }
  /** Passages matching the active chips in each chapter (for the chapter menu and "next chapter"). */
  function chapterMatches(book, active = view.filters, playable = playableOf()) {
    const out = new Map();
    if (!active.size) return out;
    for (const segment of book.passages || []) {
      if (matches(segment, book, active, playable)) out.set(segment.chapter_id, (out.get(segment.chapter_id) || 0) + 1);
    }
    return out;
  }

  // ---- Saving ------------------------------------------------------------------------------------
  const editKey = (bookId, kind, id, field) => `${bookId}|${kind}|${id}|${field}`;
  const rowKey = (kind, id) => `${kind}:${id}`;
  const bookEdits = bookId => [...edits.values()].filter(entry => entry.bookId === bookId);
  function draft(bookId, kind, id, field) { return edits.get(editKey(bookId, kind, id, field)); }
  function enqueue(work) {
    const run = queue.then(work);
    queue = run.catch(() => {});
    return run;
  }
  function setRow(kind, id, status) {
    if (status) rows.set(rowKey(kind, id), {...status, at:Date.now()}); else rows.delete(rowKey(kind, id));
    paintRow(kind, id);
    paintSaveStatus();
  }
  function itemOf(book, kind, id) { return (book?.[kind] || []).find(item => item.id === id); }
  const sameValue = (a, b) => String(a ?? '') === String(b ?? '');

  /** Record an edit. `now` saves at once (leaving a field, Enter); otherwise after `delay` ms. */
  function edit(kind, id, field, value, {now = false, delay = SAVE_DELAY_MS} = {}) {
    const book = api?.state?.book;
    if (!book) return Promise.resolve(false);
    const key = editKey(book.id, kind, id, field);
    const entry = edits.get(key) || {key, bookId:book.id, kind, id, field};
    entry.value = value;
    entry.state = 'dirty';
    entry.error = null;
    edits.set(key, entry);
    if (kind === 'passages' && view.filters.size) view.pinned.add(id);
    clearTimeout(entry.timer);
    entry.timer = null;
    if (now) return save(entry);
    entry.timer = setTimeout(() => { void save(entry); }, delay);
    paintSaveStatus();
    return Promise.resolve(true);
  }
  /** Send one field of one item. Resolves true when saved (or nothing needed saving). */
  function save(entry) {
    clearTimeout(entry.timer);
    entry.timer = null;
    if (entry.pending) return entry.pending;
    const run = enqueue(async () => {
      entry.pending = null;
      if (edits.get(entry.key) !== entry) return true;
      const current = api.state.book?.id === entry.bookId ? itemOf(api.state.book, entry.kind, entry.id) : null;
      if (current && sameValue(current[entry.field], entry.value)) {
        // Nothing changed (for example a field left as it was): no request, no new lock.
        edits.delete(entry.key);
        if (rows.get(rowKey(entry.kind, entry.id))?.tone === 'bad') setRow(entry.kind, entry.id, null); else paintSaveStatus();
        return true;
      }
      const value = entry.value;
      entry.state = 'saving';
      setRow(entry.kind, entry.id, {tone:'info', text:'Saving…'});
      try {
        const book = await api.patch(`/api/books/${encodeURIComponent(entry.bookId)}/${entry.kind}/${encodeURIComponent(entry.id)}`, {[entry.field]:value});
        if (sameValue(entry.value, value) && edits.get(entry.key) === entry) edits.delete(entry.key);
        else if (entry.state === 'saving') entry.state = 'dirty';
        rows.set(rowKey(entry.kind, entry.id), {tone:'good', text:'Saved', at:Date.now()});
        const clear = setTimeout(() => { const row = rows.get(rowKey(entry.kind, entry.id)); if (row?.text === 'Saved' && Date.now() - row.at >= SAVED_FOR_MS - 50) setRow(entry.kind, entry.id, null); }, SAVED_FOR_MS);
        clear?.unref?.();
        if (book && typeof book === 'object') api.applyBook?.(book);
        paintRow(entry.kind, entry.id);
        paintSaveStatus();
        return true;
      } catch (error) {
        entry.state = 'failed';
        entry.error = error?.message || 'Could not save.';
        rows.set(rowKey(entry.kind, entry.id), {tone:'bad', text:`Not saved: ${entry.error}`, retry:true, at:Date.now()});
        // Re-render so the row shows your value and Try again (focus and caret are kept).
        render();
        return false;
      }
    });
    entry.pending = run;
    return run;
  }
  /** Save every waiting edit now (chapter change, leaving the page). Resolves true when all saved. */
  async function flush(bookId) {
    const waiting = [...edits.values()].filter(entry => (!bookId || entry.bookId === bookId) && (entry.state === 'dirty' || entry.pending));
    const results = await Promise.all(waiting.map(entry => save(entry)));
    return results.every(Boolean);
  }
  function hasUnsaved(bookId) { return [...edits.values()].some(entry => !bookId || entry.bookId === bookId); }
  function retry(kind, id) {
    const bookId = api?.state?.book?.id;
    return Promise.all([...edits.values()].filter(entry => entry.bookId === bookId && entry.kind === kind && entry.id === id).map(entry => save(entry)));
  }
  function retryAll() {
    const bookId = api?.state?.book?.id;
    return Promise.all(bookEdits(bookId).filter(entry => entry.state === 'failed').map(entry => save(entry)));
  }

  // ---- Bulk speaker ------------------------------------------------------------------------------
  /** Assign one speaker to several passages: one existing passage edit each, in order. */
  async function assign(ids, speakerId) {
    const book = api?.state?.book;
    if (!book || view.bulk?.running || !speakerId || !ids.length) return null;
    const speaker = (book.characters || []).find(character => character.id === speakerId);
    if (!speaker) return null;
    const bookId = book.id;
    const todo = ids.filter(id => itemOf(book, 'passages', id));
    const result = {running:true, total:todo.length, done:0, saved:0, unchanged:0, failed:[], speaker:speaker.name};
    view.bulk = result;
    paintBulk();
    let latest = null;
    for (const id of todo) {
      const segment = itemOf(latest || api.state.book, 'passages', id);
      // A waiting speaker edit for this passage is replaced by this assignment.
      const pendingDraft = draft(bookId, 'passages', id, 'speaker_id');
      if (pendingDraft) { clearTimeout(pendingDraft.timer); edits.delete(pendingDraft.key); }
      if (segment && segment.speaker_id === speakerId) { result.unchanged++; result.done++; paintBulk(); continue; }
      setRow('passages', id, {tone:'info', text:'Saving…'});
      try {
        latest = await enqueue(() => api.patch(`/api/books/${encodeURIComponent(bookId)}/passages/${encodeURIComponent(id)}`, {speaker_id:speakerId}));
        result.saved++;
        view.pinned.add(id);
        rows.set(rowKey('passages', id), {tone:'good', text:`Saved · ${speaker.name}`, at:Date.now()});
      } catch (error) {
        result.failed.push({id, error:error?.message || 'Could not save.'});
        rows.set(rowKey('passages', id), {tone:'bad', text:`Not saved: ${error?.message || 'Could not save.'}`});
      }
      result.done++;
      paintBulk();
    }
    result.running = false;
    // The rows that did not save stay selected, so Assign again retries exactly those.
    view.selected = new Set(result.failed.map(item => item.id));
    if (latest && typeof latest === 'object') api.applyBook?.(latest); else render();
    return result;
  }
  function bulkText(result) {
    if (!result) return {text:'', tone:''};
    if (result.running) return {text:`Assigning ${result.done} of ${plural(result.total, 'passage')} to ${result.speaker}…`, tone:'info'};
    const done = result.saved + result.unchanged;
    if (!result.failed.length) return {text:`${plural(done, 'passage')} now ${done === 1 ? 'has' : 'have'} ${result.speaker} as the speaker.`, tone:'good'};
    return {text:`${done} of ${plural(result.total, 'passage')} assigned to ${result.speaker}. ${plural(result.failed.length, 'passage')} did not save (${result.failed[0].error}); they stay selected, so Assign tries them again.`, tone:'bad'};
  }

  // ---- Markup ------------------------------------------------------------------------------------
  const speakerOptions = (book, selected) => (book.characters || []).map(character =>
    `<option value="${esc(character.id)}"${character.id === selected ? ' selected' : ''}>${esc(character.name)}</option>`).join('')
    + (selected && !(book.characters || []).some(character => character.id === selected) ? `<option value="${esc(selected)}" selected>Unknown speaker</option>` : '');
  const characterName = (book, id) => (book.characters || []).find(character => character.id === id)?.name || '';
  const pad = number => String(number).padStart(2, '0');

  function flagsHtml(segment, book, playable) {
    const out = [];
    if (isUnassigned(segment, book)) out.push(ui().badge('Unassigned speaker', 'warn'));
    else if (segment.kind === 'dialogue' && finite(segment.confidence)) {
      out.push(segment.confidence <= LOW_CONFIDENCE ? ui().badge(`Low confidence · ${ui().fmt.percent(segment.confidence)}`, 'warn')
        : `<span>Speaker confidence ${esc(ui().fmt.percent(segment.confidence))}</span>`);
    }
    const check = segment.speaker_check;
    const other = check ? characterName(book, check.speaker_id) || check.speaker || '' : '';
    if (check?.result === 'differs') out.push(ui().badge(`BookNLP disagrees${other ? ` · ${other}` : ''}`, 'info'));
    else if (check?.result === 'suggests' && other) out.push(ui().badge(`BookNLP suggests ${other}`, 'info'));
    const fields = editedFields(segment);
    if (fields.length) out.push(`<span class="badge" data-tone="accent" title="Analysis keeps what you changed by hand">${esc(fields.length > 1 ? 'Your edits' : fields[0] === 'speaker_id' ? 'Your speaker' : fields[0] === 'direction' ? 'Your note' : 'Your edit')}</span>`);
    const take = playable(segment) ? `Recorded · ${api?.formatTime ? api.formatTime(segment.audio.duration) : ''}${segment.audio.provider ? ` · ${segment.audio.provider}` : ''}`
      : 'Not recorded';  // An out-of-date take is presented as null audio.
    out.push(`<span class="script-take" data-recorded="${playable(segment) ? 'true' : 'false'}">${esc(take)}</span>`);
    if (segment.cues?.length) out.push(`<span>${esc(segment.cues.map(cue => typeof cue === 'string' ? cue : cue?.text || '').filter(Boolean).join(' · '))}</span>`);
    return out.join('');
  }

  function rowHtml(segment, number, book, {playable, paid, busy}) {
    const id = segment.id;
    const speaker = draft(book.id, 'passages', id, 'speaker_id')?.value ?? segment.speaker_id;
    const direction = draft(book.id, 'passages', id, 'direction')?.value ?? segment.direction ?? '';
    const recorded = playable(segment);
    const record = ui().button({label:recorded ? 'Record again' : 'Record', size:'small', disabled:busy,
      attrs:{class:'button subtle small render-action', 'data-render-segment':id, 'data-key':`record-${id}`,
        title:`${recorded ? 'Record this passage again' : 'Record this passage'}${paid ? ' · one paid Gemini request' : ''}`}});
    const play = recorded ? ui().button({label:'Play the recording', size:'small', attrs:{'data-play-segment':id, 'data-key':`play-${id}`}}) : '';
    const hear = ui().button({label:paid ? 'Hear example · paid' : 'Hear example', size:'small',
      attrs:{'data-preview-speaker':id, 'data-key':`hear-${id}`, 'aria-label':`Hear the chosen speaker on passage ${number}${paid ? ' (paid request)' : ''}`}});
    const status = rows.get(rowKey('passages', id));
    const classes = ['segment-row', 'script-row', view.target === id ? 'is-target' : '', view.selected.has(id) ? 'is-selected' : ''].filter(Boolean).join(' ');
    return `<form class="${classes}" id="script-row-${esc(id)}" data-segment-form="${esc(id)}">`
      + `<input type="checkbox" class="script-pick" id="script-pick-${esc(id)}" data-script-pick="${esc(id)}" data-key="pick-${esc(id)}" aria-label="Select passage ${number}"${view.selected.has(id) ? ' checked' : ''}>`
      + `<div class="script-row-body"><p class="segment-text"><span class="segment-number">${pad(number)}</span>${esc(segment.text)}</p>`
      + `<div class="segment-meta">${flagsHtml(segment, book, playable)}</div>`
      + `<div class="segment-toolbar"><label class="sr-only" for="speaker-${esc(id)}">Speaker for passage ${number}</label>`
      + `<select id="speaker-${esc(id)}" name="speaker_id" data-script-field="speaker_id" data-key="speaker-${esc(id)}">${speakerOptions(book, speaker)}</select>${hear}`
      + `<label class="sr-only" for="segment-direction-${esc(id)}">Performance note for passage ${number}</label>`
      + `<input id="segment-direction-${esc(id)}" name="direction" maxlength="3000" data-script-field="direction" data-key="direction-${esc(id)}" value="${esc(direction)}" placeholder="Performance note…" autocomplete="off">`
      + `<details class="script-row-more" data-script-more="${esc(id)}"${view.open.has(id) ? ' open' : ''}><summary data-key="more-${esc(id)}" aria-label="More for passage ${number}">More</summary><div class="script-row-menu">${record}${play}</div></details></div>`
      + `<div class="script-row-status">${ui().message({id:`script-status-passages-${id}`})}${status?.retry ? ui().button({label:'Try again', variant:'text', size:'small', attrs:{'data-script-retry':`passages:${id}`, 'data-key':`retry-${id}`}}) : ''}</div>`
      + '</div></form>';
  }

  function sceneHtml(scene, index, items, numbers, book, options) {
    const direction = draft(book.id, 'scenes', scene.id, 'direction')?.value ?? scene.direction ?? '';
    const status = rows.get(rowKey('scenes', scene.id));
    return `<section class="scene-card" data-scene="${esc(scene.id)}"><div class="scene-header"><div><span class="eyebrow">Scene ${pad(index + 1)}${scene.tone ? ` · ${esc(scene.tone)}` : ''}</span>`
      + `<h3>${esc(scene.title || `Scene ${index + 1}`)}</h3>${scene.summary ? `<p>${esc(scene.summary)}</p>` : ''}</div>`
      + `<button class="button subtle render-action" data-render-scene="${esc(scene.id)}" data-key="scene-${esc(scene.id)}"${options.busy ? ' disabled' : ''}>${options.icon ? options.icon('play') : ''} Narrate scene</button></div>`
      + `<div class="render-confirm" data-render-confirm-host="${esc(scene.id)}" hidden></div>`
      + `<form class="scene-direction" data-scene-form="${esc(scene.id)}"><label class="field-label" for="scene-direction-${esc(scene.id)}">Direction for this scene</label>`
      + `<textarea id="scene-direction-${esc(scene.id)}" name="direction" maxlength="3000" rows="1" data-script-field="direction" data-key="scene-direction-${esc(scene.id)}" placeholder="The emotional setting, pacing, and subtext…">${esc(direction)}</textarea>`
      + `<div class="script-row-status">${ui().message({id:`script-status-scenes-${scene.id}`})}${status?.retry ? ui().button({label:'Try again', variant:'text', size:'small', attrs:{'data-script-retry':`scenes:${scene.id}`, 'data-key':`retry-scene-${scene.id}`}}) : ''}</div></form>`
      + `<div class="scene-passages">${items.map(segment => rowHtml(segment, numbers.get(segment.id), book, options)).join('')}</div></section>`;
  }

  function reviewHtml(book, chapterId, shown, total, options) {
    const tally = counts(book, chapterId, options.playable);
    const available = filtersFor(book);
    const chips = ui().choice({kind:'chips', mode:'pressed', label:'Needs a look', name:'script-filter', value:[...view.filters],
      options:available.map(filter => ({value:filter.id, label:`${filter.label} · ${tally[filter.id].chapter}`,
        ariaLabel:`${filter.label}: ${plural(tally[filter.id].chapter, 'passage')} in this chapter, ${tally[filter.id].book} in the book`}))})
      .replace(/data-value="([\w-]+)"/g, 'data-value="$1" data-script-filter="$1" data-key="filter-$1"');
    const clear = view.filters.size ? ui().button({label:'Show all passages', variant:'text', size:'small', attrs:{'data-script-clear':'', 'data-key':'filter-clear'}}) : '';
    const multi = (book.chapters || []).length > 1;
    const bookLine = multi ? available.filter(filter => tally[filter.id].book).map(filter => `${tally[filter.id].book} ${filter.short}`).join(' · ') : '';
    let elsewhere = '';
    if (view.filters.size && multi) {
      const byChapter = chapterMatches(book, view.filters, options.playable);
      const order = book.chapters.map(chapter => chapter.id);
      const here = order.indexOf(chapterId);
      const next = [...order.slice(here + 1), ...order.slice(0, Math.max(0, here))].find(id => byChapter.get(id));
      if (next) {
        const chapter = book.chapters.find(item => item.id === next);
        elsewhere = ui().button({label:`${chapter.title || 'Next chapter'} · ${byChapter.get(next)} →`, variant:'text', size:'small',
          attrs:{'data-script-chapter':next, 'data-key':'next-match', 'aria-label':`Go to ${chapter.title || 'the next chapter'}, ${plural(byChapter.get(next), 'passage')} to check`}});
      }
    }
    const showing = view.filters.size ? `<p class="script-showing">Showing ${shown} of ${plural(total, 'passage')} in this chapter.${elsewhere ? ' Next:' : ''} ${elsewhere}</p>` : '';
    const shownIds = options.shownIds;
    const pickedShown = shownIds.filter(id => view.selected.has(id)).length;
    const selectedCount = view.selected.size;
    const running = Boolean(view.bulk?.running);
    const speakerSelect = `<label class="sr-only" for="script-bulk-speaker">Speaker for the selected passages</label><select id="script-bulk-speaker" data-key="bulk-speaker"${running || options.busy ? ' disabled' : ''}><option value="">Choose a speaker</option>${(book.characters || []).map(character => `<option value="${esc(character.id)}"${character.id === view.bulkSpeaker ? ' selected' : ''}>${esc(character.name)}</option>`).join('')}</select>`;
    const assignButton = ui().button({label:selectedCount ? `Assign to ${plural(selectedCount, 'passage')}` : 'Assign', size:'small', busy:running, busyLabel:'Assigning…',
      disabled:!selectedCount || !view.bulkSpeaker || options.busy, attrs:{'data-script-assign':'', 'data-key':'bulk-assign'}});
    const bulk = `<div class="script-bulk" role="group" aria-label="Change the speaker of several passages">`
      + `<label class="script-select-shown"><input type="checkbox" id="script-select-shown" data-key="select-shown"${shownIds.length && pickedShown === shownIds.length ? ' checked' : ''}${!shownIds.length ? ' disabled' : ''}> Select shown</label>`
      + `<span class="script-selected">${selectedCount ? `${selectedCount} selected` : 'None selected'}</span>${speakerSelect}${assignButton}</div>`
      + ui().message({id:'script-bulk-status'});
    return `<div class="script-filters"><span class="script-filters-label" id="script-filters-label">Needs a look</span>${chips}${clear}</div>`
      + (bookLine ? `<p class="script-book-line">In the whole book: ${esc(bookLine)}.</p>` : '')
      + showing + bulk + ui().message({id:'script-save-status'});
  }

  // ---- Rendering ---------------------------------------------------------------------------------
  function sync(book, chapterId) {
    if (view.bookId !== book.id) {
      view.bookId = book.id;
      view.pinned.clear(); view.selected.clear(); view.open.clear();
      view.target = null; view.bulk = null; view.bulkSpeaker = '';
      view.chapterId = chapterId;
    } else if (view.chapterId !== chapterId) {
      view.chapterId = chapterId;
      view.pinned.clear(); view.selected.clear(); view.open.clear();
      view.target = null;
      if (!view.bulk?.running) view.bulk = null;
    }
    // Selection only covers passages that still exist in this chapter.
    const here = new Set(chapterSegments(book, chapterId).map(segment => segment.id));
    for (const id of view.selected) if (!here.has(id)) view.selected.delete(id);
  }

  // Keep focus, the caret and open menus across a re-render (saves re-render the book).
  function keepFocus(host, paint) {
    const active = doc()?.activeElement;
    const inside = active && host?.contains?.(active) ? active : null;
    const key = inside?.dataset?.key;
    const caret = inside && typeof inside.selectionStart === 'number' ? [inside.selectionStart, inside.selectionEnd] : null;
    paint();
    if (!key) return;
    const again = host.querySelector?.(`[data-key="${String(key).replace(/["\\]/g, '\\$&')}"]`);
    if (!again) return;
    again.focus?.({preventScroll:true});
    if (caret && typeof again.setSelectionRange === 'function') { try { again.setSelectionRange(caret[0], caret[1]); } catch { /* not a text field */ } }
  }

  function render() {
    const book = api?.state?.book;
    if (!book) return;
    const chapterId = api.state.chapterId;
    sync(book, chapterId);
    const playable = playableOf();
    const options = {playable, paid:Boolean(api.paidRender?.()), busy:Boolean(api.busy?.()), icon:api.icon};
    const all = chapterSegments(book, chapterId);
    const numbers = new Map(all.map((segment, index) => [segment.id, index + 1]));
    const shown = visibleSegments(book, chapterId, view.filters, view.pinned, playable);
    const shownSet = new Set(shown.map(segment => segment.id));
    options.shownIds = shown.map(segment => segment.id);
    const scenes = (book.scenes || []).filter(scene => scene.chapter_id === chapterId);
    const byChapter = chapterMatches(book, view.filters, playable);
    const select = node('#studio-chapter');
    if (select) select.innerHTML = (book.chapters || []).map(chapter => {
      const count = byChapter.get(chapter.id);
      return `<option value="${esc(chapter.id)}"${chapter.id === chapterId ? ' selected' : ''}>${esc(chapter.title || 'Untitled chapter')}${count ? ` · ${count} to check` : ''}</option>`;
    }).join('');
    const index = (book.chapters || []).findIndex(chapter => chapter.id === chapterId);
    const previous = node('#script-previous-chapter'), next = node('#script-next-chapter');
    if (previous) previous.disabled = index <= 0;
    if (next) next.disabled = index < 0 || index >= book.chapters.length - 1;
    const meta = node('#script-meta');
    if (meta) meta.textContent = `${plural(scenes.length, 'scene')} · ${plural(all.length, 'passage')}`;
    const review = node('#script-review');
    if (review) keepFocus(review, () => { review.innerHTML = reviewHtml(book, chapterId, shown.length, all.length, options); });
    const list = node('#scene-list');
    if (list) keepFocus(list, () => {
      const cards = scenes.map((scene, sceneIndex) => {
        const items = all.filter(segment => (segment.scene_id === scene.id || scene.passage_ids?.includes(segment.id)) && shownSet.has(segment.id));
        return items.length || !view.filters.size ? sceneHtml(scene, sceneIndex, items, numbers, book, options) : '';
      }).join('');
      list.innerHTML = cards || (scenes.length ? `<div class="empty-state">Nothing in this chapter matches. Turn a chip off, or pick another chapter.</div>`
        : '<div class="empty-state">No scenes here yet. Analyze the story to find scenes and speakers.</div>');
    });
    paintBulk();
    for (const key of rows.keys()) { const [kind, id] = key.split(/:(.*)/s); paintRow(kind, id); }
    paintSaveStatus();
  }

  function paintRow(kind, id) {
    const target = doc()?.getElementById?.(`script-status-${kind}-${id}`) || node(`#script-status-${kind}-${id}`);
    if (!target) return;
    const status = rows.get(rowKey(kind, id));
    ui().setMessage(target, status?.text || '', {tone:status?.tone || undefined});
  }
  function paintBulk() {
    const target = doc()?.getElementById?.('script-bulk-status') || node('#script-bulk-status');
    if (!target) return;
    const {text, tone} = bulkText(view.bulk);
    ui().setMessage(target, text, {tone:tone || undefined});
  }
  function saveSummary(bookId) {
    const list = bookEdits(bookId);
    const failed = list.filter(entry => entry.state === 'failed').length;
    const saving = list.filter(entry => entry.state === 'saving').length;
    const waiting = list.filter(entry => entry.state === 'dirty').length;
    if (failed) return {text:`${plural(failed, 'change')} not saved. Use Try again on the passage, or edit it again.`, tone:'bad'};
    if (saving) return {text:'Saving…', tone:'info'};
    if (waiting) return {text:'Changes save when you pause typing.', tone:''};
    return {text:'', tone:''};
  }
  function paintSaveStatus() {
    const target = doc()?.getElementById?.('script-save-status') || node('#script-save-status');
    if (!target || !api?.state?.book) return;
    const {text, tone} = saveSummary(api.state.book.id);
    ui().setMessage(target, text, {tone:tone || undefined});
  }

  // ---- Events ------------------------------------------------------------------------------------
  const kindOf = element => element?.closest?.('[data-segment-form]') ? ['passages', element.closest('[data-segment-form]').dataset.segmentForm]
    : element?.closest?.('[data-scene-form]') ? ['scenes', element.closest('[data-scene-form]').dataset.sceneForm] : [null, null];

  function onChange(event) {
    const target = event.target;
    if (target?.dataset?.scriptPick !== undefined) {
      if (target.checked) view.selected.add(target.dataset.scriptPick); else view.selected.delete(target.dataset.scriptPick);
      target.closest?.('.script-row')?.classList?.toggle?.('is-selected', Boolean(target.checked));
      renderReview();
      return;
    }
    const field = target?.dataset?.scriptField;
    if (!field) return;
    const [kind, id] = kindOf(target);
    if (!kind) return;
    if (field === 'speaker_id') void edit(kind, id, field, target.value, {delay:SPEAKER_DELAY_MS});
    else void edit(kind, id, field, target.value, {now:true});
  }
  // Leaving a field saves what it holds now.
  function onFocusOut(event) {
    const target = event.target;
    const field = target?.dataset?.scriptField;
    if (!field) return;
    const [kind, id] = kindOf(target);
    const entry = kind && api?.state?.book ? draft(api.state.book.id, kind, id, field) : null;
    if (entry?.state === 'dirty') void save(entry);
  }
  function onInput(event) {
    const target = event.target;
    const field = target?.dataset?.scriptField;
    if (!field || field === 'speaker_id') return;
    const [kind, id] = kindOf(target);
    if (kind) void edit(kind, id, field, target.value);
  }
  function onListClick(event) {
    const retryButton = event.target?.closest?.('[data-script-retry]');
    if (retryButton) { const [kind, id] = retryButton.dataset.scriptRetry.split(/:(.*)/s); void retry(kind, id); }
  }
  function onToggle(event) {
    const details = event.target;
    const id = details?.dataset?.scriptMore;
    if (!id) return;
    if (details.open) view.open.add(id); else view.open.delete(id);
  }
  function renderReview() {
    const book = api?.state?.book;
    if (!book) return;
    render();
  }
  function onReviewClick(event) {
    const chip = event.target?.closest?.('[data-script-filter]');
    if (chip) { toggleFilter(chip.dataset.scriptFilter); return; }
    if (event.target?.closest?.('[data-script-clear]')) { view.filters.clear(); view.pinned.clear(); view.target = null; pruneSelection(); render(); return; }
    const chapter = event.target?.closest?.('[data-script-chapter]');
    if (chapter) { void goToChapter(chapter.dataset.scriptChapter); return; }
    if (event.target?.closest?.('[data-script-assign]')) { void assign([...view.selected], view.bulkSpeaker); }
  }
  function onReviewChange(event) {
    const target = event.target;
    if (target?.id === 'script-bulk-speaker') { view.bulkSpeaker = target.value; render(); return; }
    if (target?.id === 'script-select-shown') {
      const book = api?.state?.book;
      if (!book) return;
      const shown = visibleSegments(book, api.state.chapterId).map(segment => segment.id);
      if (target.checked) shown.forEach(id => view.selected.add(id)); else shown.forEach(id => view.selected.delete(id));
      render();
    }
  }
  function pruneSelection() {
    const book = api?.state?.book;
    if (!book) return;
    const shown = new Set(visibleSegments(book, api.state.chapterId).map(segment => segment.id));
    for (const id of view.selected) if (!shown.has(id)) view.selected.delete(id);
  }
  function toggleFilter(id) {
    if (!FILTERS.some(filter => filter.id === id)) return;
    if (view.filters.has(id)) view.filters.delete(id); else view.filters.add(id);
    // A new view of the chapter: edited rows and the opened passage no longer need holding in place.
    view.pinned.clear();
    view.target = null;
    pruneSelection();
    render();
  }
  async function goToChapter(chapterId) {
    if (!api?.state?.book?.chapters?.some(chapter => chapter.id === chapterId)) return;
    void flush();
    api.setChapter?.(chapterId, {scroll:false});
    node('#script-heading')?.scrollIntoView?.({block:'start', behavior:api.scrollMotion?.() || 'auto'});
  }
  function stepChapter(delta) {
    const book = api?.state?.book;
    const index = book?.chapters?.findIndex(chapter => chapter.id === api.state.chapterId) ?? -1;
    const target = book?.chapters?.[index + delta];
    if (target) void goToChapter(target.id);
  }
  /** The row form for Enter in a text field: save that row now. */
  function submit(form) {
    const id = form?.dataset?.segmentForm || form?.dataset?.sceneForm;
    const kind = form?.dataset?.segmentForm ? 'passages' : form?.dataset?.sceneForm ? 'scenes' : null;
    if (!kind) return Promise.resolve(false);
    for (const element of Array.from(form.elements || [])) {
      const field = element?.dataset?.scriptField;
      if (field && field !== 'speaker_id') {
        const saved = itemOf(api.state.book, kind, id)?.[field];
        if (!sameValue(saved, element.value) || draft(api.state.book.id, kind, id, field)) void edit(kind, id, field, element.value, {now:true});
      }
    }
    return flush(api.state.book.id);
  }

  // Analyze → Show in text. Runs during dispatch, so preventDefault reaches shell.js's fallback in time.
  function showPassage(event) {
    const {bookId, segmentId} = event?.detail || {};
    const book = api?.state?.book;
    if (!book || book.id !== bookId) return false;
    const segment = itemOf(book, 'passages', segmentId);
    if (!segment) return false;
    event.preventDefault?.();
    void flush(book.id);
    if (segment.chapter_id !== api.state.chapterId) api.setChapter?.(segment.chapter_id, {scroll:false});
    // A fresh visit: only the opened passage is held in view beside what the chips show.
    view.pinned.clear();
    view.target = segment.id;
    view.pinned.add(segment.id);
    api.setTab?.('studio');
    render();
    reveal(segment.id);
    return true;
  }
  function reveal(segmentId) {
    const row = doc()?.getElementById?.(`script-row-${segmentId}`) || node(`#script-row-${segmentId}`);
    if (!row) return false;
    row.scrollIntoView?.({block:'center', behavior:api.scrollMotion?.() || 'auto'});
    const select = doc()?.getElementById?.(`speaker-${segmentId}`) || node(`#speaker-${segmentId}`);
    (select || row).focus?.({preventScroll:true});
    return true;
  }

  function beforeUnload(event) {
    if (!hasUnsaved()) return;
    void flush();
    event.preventDefault?.();
    event.returnValue = '';
  }

  function attach(options) {
    api = options;
    const list = node('#scene-list'), review = node('#script-review');
    list?.addEventListener?.('change', onChange);
    list?.addEventListener?.('input', onInput);
    list?.addEventListener?.('click', onListClick);
    list?.addEventListener?.('focusout', onFocusOut);
    list?.addEventListener?.('toggle', onToggle, true);
    review?.addEventListener?.('click', onReviewClick);
    review?.addEventListener?.('change', onReviewChange);
    node('#script-previous-chapter')?.addEventListener?.('click', () => stepChapter(-1));
    node('#script-next-chapter')?.addEventListener?.('click', () => stepChapter(1));
    // The chapter menu itself is app.js's (setChapter); this only saves waiting text first.
    node('#studio-chapter')?.addEventListener?.('change', () => { void flush(); });
    doc()?.addEventListener?.('bardic:show-passage', showPassage);
    root.addEventListener?.('beforeunload', beforeUnload);
    root.addEventListener?.('pagehide', () => { void flush(); });
    return root.BardicScript;
  }

  root.BardicScript = {
    attach, render, flush, hasUnsaved, submit, edit, assign, retry, retryAll, showPassage, toggleFilter,
    // Pure helpers, exported for tests.
    FILTERS, LOW_CONFIDENCE, flags, counts, matches, visibleSegments, chapterMatches, bulkText,
    _view:view, _edits:edits,
  };
})(typeof window !== 'undefined' ? window : globalThis);
