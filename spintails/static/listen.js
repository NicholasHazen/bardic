/* Single-voice listening controls. Generation starts only from ensure(), called
   by an explicit Play or continuation of that playback. Enhanced data is read-only. */
(() => {
  'use strict';
  const panels = new WeakMap();
  const books = new Map();
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const encode = value => encodeURIComponent(value);
  const valid = audio => Boolean(audio?.url && audio.available !== false && !audio.stale && !audio.is_stale);
  const builtInVoices = ['Kore','Puck','Charon','Aoede','Fenrir','Leda','Orus','Zephyr','Callirrhoe','Autonoe','Enceladus','Iapetus','Umbriel','Algieba','Despina','Erinome','Algenib','Rasalgethi','Laomedeia','Achernar','Alnilam','Schedar','Gacrux','Pulcherrima','Achird','Zubenelgenubi','Vindemiatrix','Sadachbia','Sadaltager','Sulafat'];
  const sourceKey = segment => JSON.stringify([segment.id,segment.chapter_id,segment.start,segment.end,segment.text]);
  const configKey = state => JSON.stringify([state.provider,state.voices[state.provider],state.provider === 'gemini' ? state.model : 'macos-say']);
  const storageKey = id => `spintails:listen:${id}`;
  const stateFor = book => book && books.get(book.id);
  const enabled = book => stateFor(book)?.mode === 'simple';

  function saved(id) {
    try { return JSON.parse(localStorage.getItem(storageKey(id))) || {}; } catch { return {}; }
  }
  function save(state) {
    const value = {mode:state.mode,provider:state.provider,voices:state.voices,model:state.model,
      sessionId:state.sessionId,sessionKey:state.sessionKey};
    try { localStorage.setItem(storageKey(state.book.id),JSON.stringify(value)); } catch { /* Storage is optional. */ }
  }
  async function request(url, body) {
    const response = await fetch(url, body === undefined ? {headers:{Accept:'application/json'}} :
      {method:'POST',headers:{Accept:'application/json','Content-Type':'application/json'},body:JSON.stringify(body)});
    let result;
    try { result = await response.json(); } catch { result = null; }
    if (!response.ok) {
      const error = new Error(typeof result?.detail === 'string' ? result.detail : `Listening request failed (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return result;
  }
  const base = state => `/api/books/${encode(state.book.id)}/listen`;
  const wait = ms => new Promise(resolve => setTimeout(resolve,ms));
  const current = (state, version) => state.version === version && state.panel?.state === state;

  function notify(state) { state.panel?.options.onChange?.(); }
  function cancelJob(id) { if (id) void request(`/api/jobs/${encode(id)}/cancel`,{}).catch(() => {}); }
  function invalidate(state) {
    state.version++;
    cancelJob(state.job?.id);
    state.job = null;
    state.pending = null;
    state.loading = false;
  }
  function stop(book) {
    const state = stateFor(book);
    if (!state) return;
    invalidate(state);
    state.message = 'Stopped. Finished simple takes are saved for the next listen.';
    paint(state.panel);
  }
  function resolve(book, segment) {
    if (!segment) return null;
    const state = stateFor(book);
    if (!state || state.mode !== 'simple') return valid(segment.audio) ? segment.audio : null;
    const item = state.takes.get(segment.id);
    return item?.source === sourceKey(segment) && valid(item.audio) ? item.audio : null;
  }
  function remember(state, segment, audio) {
    if (!valid(audio)) throw new Error('The narration finished without a playable audio take. Try this passage again.');
    state.takes.set(segment.id,{source:sourceKey(segment),audio});
  }

  async function ensure(book, segment) {
    const state = stateFor(book);
    if (!state || state.mode !== 'simple') return resolve(book,segment);
    const available = resolve(book,segment);
    if (available) return available;
    if (state.pending?.segmentId === segment.id) return state.pending.promise;
    if (state.pending) invalidate(state);
    const version = state.version;
    const bookId = book.id;
    const signature = sourceKey(segment);
    state.loading = true;
    state.message = 'Preparing this passage with your narrator…';
    state.error = '';
    paint(state.panel);
    const promise = (async () => {
      try {
        const result = await request(base(state),{provider:state.provider,voice:state.voices[state.provider],
          model:state.provider === 'gemini' ? state.model : 'macos-say',segment_id:segment.id});
        if (!current(state,version)) { cancelJob(result?.job?.id); return null; }
        if (result.session?.id) {
          state.sessionId = result.session.id;
          state.sessionKey = configKey(state);
          save(state);
        }
        let audio = result.audio;
        if (!audio && result.job) {
          state.job = result.job;
          state.panel.options.onJob?.(result.job);
          let job = result.job;
          let missing = 0;
          while (['queued','running'].includes(job.status)) {
            if (!current(state,version)) { cancelJob(job.id); return null; }
            state.message = job.message || 'Preparing this passage…';
            paint(state.panel);
            await wait(400);
            if (!current(state,version)) { cancelJob(job.id); return null; }
            const jobs = await request(`/api/jobs?book_id=${encode(bookId)}`);
            if (!current(state,version)) { cancelJob(job.id); return null; }
            const next = jobs.find(item => item.id === job.id);
            if (!next) {
              if (++missing >= 3) throw new Error('The listening job could not be found. Press play to retry.');
              continue;
            }
            missing = 0;
            job = next;
            state.job = job;
            state.panel.options.onJob?.(job);
          }
          if (!current(state,version)) return null;
          if (job.status !== 'completed') throw new Error(job.error || job.message || 'Listening stopped before this passage was ready.');
          audio = job.audio;
          if (!audio && state.sessionId) {
            const cached = await request(base(state) + '/takes?session_id=' + encode(state.sessionId));
            audio = cached.takes?.find(item => item.segment_id === segment.id)?.audio;
          }
        }
        if (!current(state,version)) return null;
        const latest = state.book.segments.find(item => item.id === segment.id);
        if (!latest || sourceKey(latest) !== signature) return null;
        remember(state,segment,audio);
        state.message = result.cached ? 'Using the saved simple take.' : 'Passage ready. Listening continues within this chapter.';
        notify(state);
        return audio;
      } catch (error) {
        if (current(state,version)) {
          state.error = error.message;
          state.message = '';
          throw error;
        }
        return null;
      } finally {
        if (current(state,version)) {
          state.pending = null;
          state.loading = false;
          state.job = null;
          paint(state.panel);
        }
      }
    })();
    state.pending = {segmentId:segment.id,promise};
    return promise;
  }

  function change(panel, field, value) {
    const state = panel.state;
    invalidate(state);
    panel.options.onStop?.();
    state.error = '';
    state.message = '';
    if (field === 'mode') state.mode = value === 'simple' ? 'simple' : 'enhanced';
    else if (field === 'provider') state.provider = value === 'gemini' ? 'gemini' : 'system';
    else if (field === 'voice') state.voices[state.provider] = value;
    else if (field === 'model') state.model = value;
    if (field !== 'mode') {
      state.takes.clear();
      state.sessionId = null;
      state.sessionKey = null;
      state.loadedKey = null;
    }
    save(state);
    paint(panel);
    notify(state);
  }
  function paint(panel) {
    if (!panel?.state) return;
    const state = panel.state;
    const status = panel.options.status || {};
    const system = status.providers?.find(provider => provider.id === 'system');
    const gemini = status.providers?.find(provider => provider.id === 'gemini');
    const available = state.provider === 'system' ? system?.available !== false : Boolean(status.has_api_key || gemini?.available);
    const selected = state.book.segments.find(segment => segment.id === panel.options.segmentId);
    const canUseCache = selected && state.takes.get(selected.id)?.source === sourceKey(selected);
    const voices = state.provider === 'system'
      ? [{id:'',name:'Default device voice'},...(status.system_voices || [])]
      : builtInVoices.map(name => ({id:name,name}));
    const selectedVoice = state.voices[state.provider];
    if (selectedVoice && !voices.some(voice => (voice.id || voice.name) === selectedVoice)) voices.push({id:selectedVoice,name:selectedVoice});
    const models = (status.tts_models || []).map(item => typeof item === 'string' ? item : item.id);
    if (state.model && !models.includes(state.model)) models.push(state.model);
    const busy = Boolean(panel.options.busy && !state.job);
    panel.container.innerHTML = `<section class="simple-listen"><div class="simple-listen-heading"><div><h3>Listen your way</h3><p>Start with one narrator, or play your enhanced production.</p></div><label>Listening mode<select data-listen-field="mode" aria-label="Listening mode"><option value="enhanced" ${state.mode === 'enhanced' ? 'selected' : ''}>Enhanced production</option><option value="simple" ${state.mode === 'simple' ? 'selected' : ''}>Simple · one narrator</option></select></label></div><div class="simple-listen-settings"><label>Narration provider<select data-listen-field="provider" aria-label="Simple narration provider"><option value="system" ${state.provider === 'system' ? 'selected' : ''}>Device voices · local</option><option value="gemini" ${state.provider === 'gemini' ? 'selected' : ''}>Gemini · cloud</option></select></label><label>Narrator voice<select data-listen-field="voice" aria-label="Simple narrator voice">${voices.map(voice => { const id = voice.id ?? voice.name; return `<option value="${escape(id)}" ${id === selectedVoice ? 'selected' : ''}>${escape(voice.name || id)}${voice.locale ? ` · ${escape(voice.locale)}` : ''}</option>`; }).join('')}</select></label>${state.provider === 'gemini' ? `<label>Speech model<select data-listen-field="model" aria-label="Simple speech model">${models.map(model => `<option value="${escape(model)}" ${model === state.model ? 'selected' : ''}>${escape(model)}</option>`).join('')}</select></label>` : ''}</div><p class="simple-listen-note">${state.provider === 'gemini' ? 'Gemini sends each requested passage to Google and may incur charges. Generation happens as you listen.' : 'Device narration stays on this computer and has no model charges.'} Simple playback stops at the end of this chapter. Highlighting follows each passage.</p>${!available && !canUseCache ? `<p class="simple-listen-note">${state.provider === 'system' ? 'Device narration is unavailable here. Choose another provider to generate new takes.' : 'Add a Gemini API key in Settings to generate new takes.'}</p>` : ''}<div class="simple-listen-actions"><button type="button" class="button primary" data-listen-action="start" ${state.loading || busy || !available && !canUseCache ? 'disabled' : ''}>${state.loading ? 'Preparing passage…' : state.mode === 'simple' ? 'Listen from here' : 'Start simple listening'}</button><button type="button" class="button subtle" data-listen-action="stop">Stop</button><span>${state.takes.size} saved simple passage${state.takes.size === 1 ? '' : 's'}</span></div><p class="simple-listen-message ${state.error ? 'simple-listen-error' : ''}" role="${state.error ? 'alert' : 'status'}">${escape(state.error || state.message || '')}</p></section>`;
  }

  async function loadSaved(state) {
    if (!state.sessionId || state.sessionKey !== configKey(state)) return;
    const key = `${state.sessionId}:${state.book.revision}`;
    if (state.loadedKey === key) return;
    state.loadedKey = key;
    const version = state.version;
    try {
      const result = await request(base(state) + '/takes?session_id=' + encode(state.sessionId));
      if (!current(state,version)) return;
      for (const item of result.takes || []) {
        const segment = state.book.segments.find(segment => segment.id === item.segment_id);
        if (segment) remember(state,segment,item.audio);
      }
      paint(state.panel);
      notify(state);
    } catch (error) {
      if (current(state,version)) {
        if (error.status === 404) { state.sessionId = null; state.sessionKey = null; save(state); }
        else { state.error = `Saved simple takes could not be loaded: ${error.message}`; paint(state.panel); }
      }
    }
  }
  function render(container, book, options = {}) {
    if (!container) return Promise.resolve();
    let panel = panels.get(container);
    if (!panel) {
      panel = {container,state:null,options};
      panels.set(container,panel);
      container.addEventListener('change',event => {
        const field = event.target.dataset.listenField;
        if (['mode','provider','voice','model'].includes(field)) change(panel,field,event.target.value);
      });
      container.addEventListener('click',event => {
        const action = event.target.closest('[data-listen-action]')?.dataset.listenAction;
        if (action === 'stop') { stop(panel.state.book); panel.options.onStop?.(); }
        if (action === 'start') {
          if (panel.state.loading) return;
          if (panel.state.mode !== 'simple') change(panel,'mode','simple');
          const segment = panel.state.book.segments.find(item => item.id === panel.options.segmentId) ||
            panel.state.book.segments.find(item => item.chapter_id === panel.options.chapterId);
          if (segment) panel.options.onPlay?.(segment.id);
        }
      });
    }
    if (!book) {
      if (panel.state) invalidate(panel.state);
      panel.state = null;
      container.innerHTML = '';
      return Promise.resolve();
    }
    if (panel.state?.book.id !== book.id && panel.state) invalidate(panel.state);
    let state = books.get(book.id);
    if (!state) {
      const prior = saved(book.id);
      const status = options.status || {};
      const systemAvailable = status.providers?.find(provider => provider.id === 'system')?.available !== false;
      state = {book,version:0,takes:new Map(),pending:null,job:null,loading:false,error:'',message:'',loadedKey:null,
        mode:prior.mode === 'simple' ? 'simple' : 'enhanced',provider:['system','gemini'].includes(prior.provider) ? prior.provider : systemAvailable ? 'system' : 'gemini',
        voices:{system:typeof prior.voices?.system === 'string' ? prior.voices.system : '',gemini:prior.voices?.gemini || 'Kore'},
        model:prior.model || status.tts_model || 'gemini-3.8-flash-tts',sessionId:prior.sessionId || null,sessionKey:prior.sessionKey || null};
      books.set(book.id,state);
    }
    panel.options = options;
    panel.state = state;
    state.book = book;
    state.panel = panel;
    for (const [id,item] of state.takes) {
      const segment = book.segments.find(segment => segment.id === id);
      if (!segment || sourceKey(segment) !== item.source) state.takes.delete(id);
    }
    paint(panel);
    return loadSaved(state);
  }
  function allowsAdvance(book, currentSegment, nextSegment) {
    return !enabled(book) || Boolean(currentSegment && nextSegment && currentSegment.chapter_id === nextSegment.chapter_id);
  }
  window.SpinTailsListen = {render,enabled,isSimple:enabled,take:resolve,resolve,ensure,stop,allowsAdvance};
})();
