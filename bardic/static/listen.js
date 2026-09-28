/* Single-voice listening: one serialized request at a time, with explicit playback
   or chapter-preparation intent. Rendering only reads saved local takes. */
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
  const storageKey = id => `bardic:listen:${id}`;
  const stateFor = book => book && books.get(book.id);
  const enabled = book => stateFor(book)?.mode === 'simple';
  const playbackRates = [.75,1,1.25,1.5,1.75,2,2.25,2.5];
  const selectedSegment = panel => panel.state.book.segments.find(segment => segment.id === panel.options.segmentId) ||
    panel.state.book.segments.find(segment => segment.chapter_id === panel.options.chapterId);

  function getSelection(book) {
    const state = stateFor(book);
    return state ? {mode:state.mode,provider:state.provider,voice:state.voices[state.provider],
      model:state.provider === 'gemini' ? state.model : 'macos-say'} : null;
  }

  function saved(id) {
    try {
      const value = localStorage.getItem(storageKey(id)) ?? localStorage.getItem(`spintails:listen:${id}`);
      return JSON.parse(value) || {};
    } catch { return {}; }
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
  const rateOf = value => Math.min(4,Math.max(.25,Number(value) || 1));
  const durationOf = audio => Number.isFinite(audio?.duration) && audio.duration > 0 ? audio.duration : 0;
  const WARM_SECONDS = 10, BUFFER_SECONDS = 45, WARM_PASSAGES = 3, AHEAD_PASSAGES = 12;

  function invalidate(state) {
    state.version++;
    state.intent = null;
    cancelJob(state.knownJob?.id || state.job?.id);
    state.job = null;
    for (const task of state.queue.splice(0)) task.resolve(null);
    state.loading = false;
    // An uncertain POST remains the sole active request until it settles. A new
    // explicit action can queue behind it, but cannot race it with another POST.
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
  function forgetAudio(book, segment) { stateFor(book)?.takes.delete(segment?.id); }
  function remember(state, segment, audio) {
    if (!valid(audio)) throw new Error('The narration finished without a playable audio take. Try this passage again.');
    state.takes.set(segment.id,{source:sourceKey(segment),audio});
  }
  function remaining(state, segmentId) {
    const start = state.book.segments.findIndex(item => item.id === segmentId);
    if (start < 0) return [];
    const chapter = state.book.segments[start].chapter_id;
    const result = [];
    for (const segment of state.book.segments.slice(start)) {
      if (segment.chapter_id !== chapter) break;
      result.push(segment);
    }
    return result;
  }
  function buffer(state) {
    const intent = state.intent;
    const segmentId = intent?.segmentId || state.panel.options.segmentId;
    const rate = intent?.rate || rateOf(state.panel.options.playbackRate);
    let seconds = 0, readyPassages = 0;
    for (const [index,segment] of remaining(state,segmentId).slice(0,AHEAD_PASSAGES+1).entries()) {
      const audio = resolve(state.book,segment);
      if (!audio) break;
      seconds += Math.max(0,durationOf(audio)-(index === 0 ? intent?.offset || 0 : 0))/rate;
      readyPassages++;
    }
    return {seconds,readyPassages,targetSeconds:intent?.phase === 'warmup' ? WARM_SECONDS : BUFFER_SECONDS,
      preparing:state.loading,error:state.error,chapterPreparation:intent?.type === 'chapter',rate};
  }
  function getBuffer(book) { const state = stateFor(book); return state ? buffer(state) : null; }
  function report(state,event,error,operation,segmentId) {
    window.BardicDiagnostics?.record(event,{book_id:state.book.id,segment_id:segmentId || state.intent?.segmentId,
      session_id:state.sessionId,job_id:state.job?.id || state.knownJob?.id,
      playback_rate:state.intent?.rate,http_status:error.status,operation});
  }
  function halt(state, error, task) {
    report(state,'buffer_failed',error,task?.operation || 'prepare',task?.segment?.id);
    state.error = error.message;
    state.message = 'Preparation paused. Saved audio remains playable. Retry is an explicit new request.';
    if (state.intent) state.intent.blocked = true;
    for (const task of state.queue.splice(0)) task.resolve(null);
  }

  async function settleKnownJob(state, task, showWaiting = true) {
    // Cancellation is cooperative: the old provider request can remain running.
    // Keep its identity after UI cancellation or polling failure, and use only
    // read-only checks until it settles before another request is submitted.
    let failures = 0, missing = 0;
    for (let attempt=0; state.knownJob && attempt<450; attempt++) {
      if (!current(state,task.version)) return false;
      const known = state.knownJob;
      if (showWaiting) {
        state.message = 'Waiting for the previous narration request to finish…';
        paint(state.panel);
      }
      let jobs;
      try {
        jobs = await request(`/api/jobs?book_id=${encode(state.book.id)}`);
        failures = 0;
      } catch (error) {
        if (!current(state,task.version)) return false;
        if ((!error.status || error.status === 429 || error.status >= 500) && ++failures <= 2) {
          await wait(400);
          continue;
        }
        throw error;
      }
      if (!current(state,task.version)) return false;
      const job = jobs.find(item => item.id === known.id);
      if (!job) {
        if (++missing >= 3) throw new Error('The previous listening job could not be found. Retry to check its status before requesting more audio.');
      } else {
        missing = 0;
        if (!['queued','running'].includes(job.status)) {
          if (state.knownJob === known) state.knownJob = null;
          return true;
        }
      }
      await wait(400);
    }
    if (state.knownJob) throw new Error('The previous narration is still running. Saved audio is available; retry preparation after it finishes.');
    return true;
  }
  async function waitForStopped(book) {
    const state = stateFor(book);
    if (!state) return true;
    const version = state.version;
    if (!current(state,version) || state.intent || state.queue.length) return false;
    // Stop already invalidated the old request. Let a pending POST disclose its
    // job ID before checking only that job's status; never submit another take.
    try { await state.active?.promise; } catch { /* Retain known job for settlement. */ }
    if (!current(state,version) || state.intent || state.queue.length) return false;
    return await settleKnownJob(state,{version},false) && current(state,version) && !state.intent && !state.queue.length;
  }
  async function generate(state, task) {
    const {segment,version,signature} = task;
    const bookId = state.book.id;
    task.operation = 'settle';
    if (!await settleKnownJob(state,task) || !current(state,version)) return null;
    task.operation = 'prepare';
    const result = await request(base(state),{provider:state.provider,voice:state.voices[state.provider],
      model:state.provider === 'gemini' ? state.model : 'macos-say',segment_id:segment.id});
    if (result.job?.id) state.knownJob = {id:result.job.id};
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
      let missing = 0, pollFailures = 0;
      task.operation = 'poll';
      while (['queued','running'].includes(job.status)) {
        if (!current(state,version)) { cancelJob(job.id); return null; }
        state.message = job.message || 'Preparing a passage…';
        paint(state.panel);
        await wait(400);
        if (!current(state,version)) { cancelJob(job.id); return null; }
        let jobs;
        try {
          jobs = await request(`/api/jobs?book_id=${encode(bookId)}`);
          pollFailures = 0;
        } catch (error) {
          if (!current(state,version)) return null;
          // Only read-only polling is retried. Never blindly repeat a POST that
          // might already have been accepted and charged by the provider.
          if ((!error.status || error.status === 429 || error.status >= 500) && ++pollFailures <= 2) continue;
          throw error;
        }
        if (!current(state,version)) { cancelJob(job.id); return null; }
        const next = jobs.find(item => item.id === job.id);
        if (!next) {
          if (++missing >= 3) throw new Error('The listening job could not be found. Retry preparation to check again.');
          continue;
        }
        missing = 0;
        job = next;
        state.job = job;
        state.panel.options.onJob?.(job);
      }
      if (!current(state,version)) return null;
      if (state.knownJob?.id === job.id) state.knownJob = null;
      if (job.status !== 'completed') throw new Error(job.error || job.message || 'Listening stopped before this passage was ready.');
      audio = job.audio;
      if (!audio && state.sessionId) {
        task.operation = 'cache_read';
        const cached = await request(base(state) + '/takes?session_id=' + encode(state.sessionId));
        audio = cached.takes?.find(item => item.segment_id === segment.id)?.audio;
      }
    }
    if (!current(state,version)) return null;
    const latest = state.book.segments.find(item => item.id === segment.id);
    if (!latest || sourceKey(latest) !== signature) return null;
    remember(state,segment,audio);
    state.message = result.cached ? 'Using saved audio from the local cache.' : 'Passage saved in the local audio cache.';
    notify(state);
    return current(state,version) ? audio : null;
  }
  function drain(state) {
    if (state.active || !state.queue.length) return;
    const task = state.queue.shift();
    if (!current(state,task.version)) { task.resolve(null); drain(state); return; }
    state.active = task;
    state.loading = true;
    state.message = 'Preparing audio with your narrator…';
    paint(state.panel);
    void (async () => {
      try {
        const audio = await generate(state,task);
        task.resolve(current(state,task.version) ? audio : null);
      }
      catch (error) {
        if (current(state,task.version)) { halt(state,error,task); task.reject(error); }
        else task.resolve(null);
      } finally {
        state.active = null;
        if (current(state,task.version)) { state.loading = false; state.job = null; }
        paint(state.panel);
        drain(state);
        schedule(state);
      }
    })();
  }
  function enqueue(state,segment,priority=false) {
    const available = resolve(state.book,segment);
    if (available) return Promise.resolve(available);
    const signature = sourceKey(segment);
    const matching = [state.active,...state.queue].find(task => task && task.version === state.version && task.signature === signature);
    if (matching) {
      if (priority && matching !== state.active) {
        state.queue.splice(state.queue.indexOf(matching),1);
        state.queue.unshift(matching);
      }
      return matching.promise;
    }
    const task = {segment,signature,version:state.version};
    task.promise = new Promise((resolve,reject) => { task.resolve = resolve; task.reject = reject; });
    if (priority) state.queue.unshift(task); else state.queue.push(task);
    drain(state);
    return task.promise;
  }
  async function ensure(book, segment) {
    const state = stateFor(book);
    if (!state || state.mode !== 'simple') return resolve(book,segment);
    state.error = '';
    return enqueue(state,segment,true);
  }
  function schedule(state) {
    const intent = state.intent;
    if (!intent || intent.blocked || intent.phase !== 'playing' || state.active || state.queue.length) return;
    const list = remaining(state,intent.segmentId).slice(0,AHEAD_PASSAGES+1);
    let seconds = 0;
    for (const [index,segment] of list.entries()) {
      if (seconds >= BUFFER_SECONDS) break;
      const audio = resolve(state.book,segment);
      if (!audio) { void enqueue(state,segment).catch(() => {}); return; }
      seconds += Math.max(0,durationOf(audio)-(index === 0 ? intent.offset : 0))/intent.rate;
    }
  }
  async function prepare(book,segment,{playbackRate=1,offset=0}={}) {
    const state = stateFor(book);
    if (!state || state.mode !== 'simple') return resolve(book,segment);
    let intent = state.intent;
    if (intent?.type !== 'play' || intent.chapterId !== segment.chapter_id) {
      if (intent) invalidate(state);
      state.error = '';
      intent = state.intent = {type:'play',phase:'warmup',chapterId:segment.chapter_id,segmentId:segment.id,
        offset:Math.max(0,Number(offset)||0),rate:rateOf(playbackRate),blocked:false};
    } else {
      intent.segmentId = segment.id;
      intent.offset = Math.max(0,Number(offset)||0);
      intent.rate = rateOf(playbackRate);
      if (resolve(book,segment) && intent.phase === 'playing') return resolve(book,segment);
      if (intent.blocked) {
        if (resolve(book,segment)) return resolve(book,segment);
        throw new Error(state.error || 'Preparation paused. Choose Retry preparation.');
      }
      intent.phase = 'warmup';
    }
    const version = state.version;
    for (const next of remaining(state,segment.id).slice(0,WARM_PASSAGES)) {
      if (!current(state,version) || state.intent !== intent) return null;
      if (buffer(state).seconds >= WARM_SECONDS) break;
      try { await enqueue(state,next,true); }
      catch (error) {
        if (!resolve(book,segment)) throw error;
        break; // A future failure cannot take a ready current passage away.
      }
    }
    if (!current(state,version) || state.intent !== intent) return null;
    intent.phase = 'ready';
    paint(state.panel);
    return resolve(book,segment);
  }
  function updatePlayback(book,segment,{playbackRate=1,currentTime=0}={}) {
    const state = stateFor(book), intent = state?.intent;
    if (!intent || intent.type !== 'play') return;
    if (intent.chapterId !== segment?.chapter_id) { stop(book); return; }
    intent.segmentId = segment.id;
    intent.rate = rateOf(playbackRate);
    intent.offset = Math.max(0,Number(currentTime)||0);
    intent.phase = 'playing';
    schedule(state);
    const value = buffer(state);
    const key = JSON.stringify([Math.floor(value.seconds),value.readyPassages,value.rate,state.loading,state.error]);
    if (state.bufferPaint !== key) { state.bufferPaint = key; paint(state.panel); }
  }
  async function prepareChapter(book,segment,{playbackRate=1}={}) {
    const state = stateFor(book);
    if (!state || state.mode !== 'simple') return;
    invalidate(state);
    state.error = '';
    const list = remaining(state,segment.id);
    const intent = state.intent = {type:'chapter',phase:'chapter',chapterId:segment.chapter_id,segmentId:segment.id,
      offset:0,rate:rateOf(playbackRate),blocked:false,total:list.length,completed:0};
    const version = state.version;
    // An audition can be cancelled while its provider request is still active.
    // Set chapter intent first so Stop/book changes invalidate this wait, then
    // settle the other producer before submitting any chapter passage.
    paint(state.panel);
    try {
      const ready = await state.panel.options.beforeChapterPrepare?.();
      if (!current(state,version) || state.intent !== intent) return;
      if (ready === false) {
        state.intent = null;
        state.message = 'Chapter preparation stopped before requesting audio.';
        paint(state.panel);
        return;
      }
    } catch (error) {
      if (current(state,version) && state.intent === intent) {
        halt(state,error,{operation:'settle',segment});
        paint(state.panel);
      }
      return;
    }
    for (const next of list) {
      if (!current(state,version) || state.intent !== intent) return;
      try { await enqueue(state,next); }
      catch { return; }
      if (!current(state,version) || state.intent !== intent) return;
      intent.completed++;
      paint(state.panel);
    }
    if (current(state,version) && state.intent === intent) {
      state.intent = null;
      state.message = 'The rest of this chapter is saved and ready. Press play whenever you like.';
      paint(state.panel);
    }
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
    const selected = selectedSegment(panel);
    const canUseCache = selected && state.takes.get(selected.id)?.source === sourceKey(selected);
    const voices = state.provider === 'system'
      ? [{id:'',name:'Default device voice'},...(status.system_voices || [])]
      : builtInVoices.map(name => ({id:name,name}));
    const selectedVoice = state.voices[state.provider];
    if (selectedVoice && !voices.some(voice => (voice.id || voice.name) === selectedVoice)) voices.push({id:selectedVoice,name:selectedVoice});
    const models = (status.tts_models || []).map(item => typeof item === 'string' ? item : item.id);
    if (state.model && !models.includes(state.model)) models.push(state.model);
    const busy = Boolean(panel.options.busy && !state.job);
    const buffered = buffer(state);
    const chapter = state.intent?.type === 'chapter';
    const warming = state.intent?.phase === 'warmup';
    const preparing = state.mode === 'simple' && (warming || panel.options.preparing);
    const playing = state.mode === 'simple' && panel.options.playing;
    const startDisabled = chapter || (!preparing && !playing && (busy || !available && !canUseCache));
    const startLabel = preparing ? 'Preparing…' : state.mode === 'simple' ? playing ? 'Pause' : 'Play' : 'Start simple listening';
    const previewDescription = selected ? 'Hear a short example from the selected passage.' : 'Hear a short example using demo text.';
    const remainingCount = selected ? remaining(state,selected.id).length : 0;
    const progress = chapter ? state.intent.completed : Math.min(buffered.seconds,buffered.targetSeconds);
    const maximum = chapter ? state.intent.total || 1 : buffered.targetSeconds;
    const progressText = chapter
      ? `Preparing chapter · ${state.intent.completed} of ${state.intent.total} passages saved`
      : `${warming ? 'Warming up' : 'Audio buffer'} · ${Math.floor(buffered.seconds)} seconds ready at ${buffered.rate}×`;
    const showProgress = state.mode === 'simple' && (state.intent || state.loading || playing || preparing);
    const progressMarkup = showProgress ? `<div class="simple-listen-buffer"><div><span role="status">${escape(progressText)}</span>${chapter ? '' : `<span>${buffered.readyPassages} passage${buffered.readyPassages === 1 ? '' : 's'}</span>`}</div><progress max="${maximum}" value="${progress}" aria-label="${chapter ? 'Chapter preparation' : 'Saved audio buffer'}"></progress></div>` : '';
    const drawer = panel.container.closest?.('details');
    const summary = drawer?.querySelector?.('#listening-summary');
    const narratorLabel = selectedVoice || 'Default device voice';
    const providerLabel = state.provider === 'gemini' ? 'Gemini' : 'Device';
    const sourceLabel = state.mode === 'simple' ? 'One narrator' : 'Studio voices selected · Narrator setup';
    const selectionSummary = `${sourceLabel}: ${narratorLabel} / ${providerLabel} · ${state.provider === 'gemini' ? 'usage may incur charges' : 'free on this device'}`;
    if (summary) {
      const summaryText = `${state.error ? 'Preparation paused · ' : chapter ? `Preparing chapter: ${state.intent.completed} of ${state.intent.total} passages · ` : ''}${selectionSummary}`;
      if (summary.textContent !== summaryText) summary.textContent = summaryText;
      summary.classList?.toggle('has-error', Boolean(state.error));
    }
    // Closed setup must not conceal a new background failure. Reveal it once,
    // then respect a deliberate close while the same error remains in view.
    const errorKey = state.error ? JSON.stringify([state.book.id,state.error]) : null;
    if (errorKey && errorKey !== panel.revealedError && drawer) drawer.open = true;
    panel.revealedError = errorKey;
    // Playback updates repaint this panel often. Preserve a user's disclosure
    // choice and keyboard focus while refreshing preparation status.
    const disclosure = panel.container.querySelector?.('[data-listen-options]');
    if (disclosure) panel.optionsOpen = disclosure.open;
    const html = `<section class="simple-listen" aria-label="Listening settings">
      <div class="simple-listen-heading">
        <div><h3>Listen your way</h3><p>${state.mode === 'simple' ? 'One narrator, ready when you are.' : 'Choose one narrator, or play your saved Studio voices.'}</p></div>
        <label>Playback source<select data-listen-field="mode" aria-label="Listening mode"><option value="simple" ${state.mode === 'simple' ? 'selected' : ''}>One narrator</option><option value="enhanced" ${state.mode === 'enhanced' ? 'selected' : ''}>Studio voices</option></select></label>
      </div>
      <div class="simple-listen-settings">
        <label>Provider<select data-listen-field="provider" aria-label="Simple narration provider"><option value="system" ${state.provider === 'system' ? 'selected' : ''}>Device voices · free & local</option><option value="gemini" ${state.provider === 'gemini' ? 'selected' : ''}>Gemini · cloud</option></select></label>
        <div class="simple-listen-voice"><label>Narrator<select data-listen-field="voice" aria-label="Simple narrator voice">${voices.map(voice => { const id = voice.id ?? voice.name; return `<option value="${escape(id)}" ${id === selectedVoice ? 'selected' : ''}>${escape(voice.name || id)}${voice.locale ? ` · ${escape(voice.locale)}` : ''}</option>`; }).join('')}</select></label><button type="button" class="button subtle" data-listen-action="preview" title="${previewDescription}" ${panel.options.previewing || busy ? 'disabled' : ''}>Hear example</button></div>
      </div>
      <p class="simple-listen-note simple-listen-disclosure">${state.provider === 'gemini' ? 'Gemini sends requested passages to Google and may incur charges, including examples and audio prepared ahead. No narration spending cap is enforced.' : 'Device narration stays on this computer and has no model charges.'}</p>
      ${!available && !canUseCache ? `<p class="simple-listen-unavailable">${state.provider === 'system' ? 'Device narration is unavailable here. Choose Gemini to generate new audio.' : 'Add a Gemini API key in Settings to generate new takes.'}</p>` : ''}
      <div class="simple-listen-actions">
        <button type="button" class="button primary" data-listen-action="start" ${startDisabled ? 'disabled' : ''} aria-label="${preparing ? 'Stop preparing narration' : playing ? 'Pause simple listening' : state.mode === 'simple' ? 'Play simple listening' : 'Start simple listening'}">${startLabel}</button>
        ${state.mode === 'simple' || state.loading ? '<button type="button" class="button subtle" data-listen-action="stop">Stop</button>' : ''}
        <span>${state.mode === 'simple' ? 'Stops at chapter end' : 'No story analysis needed'}</span>
      </div>
      ${progressMarkup}
      <p class="simple-listen-message ${state.error ? 'simple-listen-error' : ''}" role="${state.error ? 'alert' : 'status'}">${escape(state.error || state.message || '')}</p>
      ${state.error ? '<button type="button" class="button subtle simple-listen-retry" data-listen-action="retry">Retry preparation</button>' : ''}
      <details class="simple-listen-options" data-listen-options ${panel.optionsOpen ? 'open' : ''}>
        <summary data-listen-summary>More listening options</summary>
        <div class="simple-listen-advanced">
          <div class="simple-listen-settings">
            <label>Playback speed<select data-listen-speed aria-label="Simple listening playback speed">${playbackRates.map(rate => `<option value="${rate}" ${rate === rateOf(panel.options.playbackRate) ? 'selected' : ''}>${rate}×</option>`).join('')}</select></label>
            ${state.provider === 'gemini' ? `<label>Speech model<select data-listen-field="model" aria-label="Simple speech model">${models.map(model => `<option value="${escape(model)}" ${model === state.model ? 'selected' : ''}>${escape(model)}</option>`).join('')}</select></label>` : ''}
          </div>
          <p class="simple-listen-note">Simple playback stops at the end of this chapter. Highlighting follows each passage. Playback and speed are shared with the player below.</p>
          <div class="simple-listen-preparation">
            <div><h4>Prepare before listening</h4><p>Save up to ${remainingCount} passages from here to the end of the chapter, without starting playback. ${state.provider === 'gemini' ? 'Uncached passages can incur provider charges.' : 'Saved matching audio is reused.'}</p></div>
            <button type="button" class="button subtle" data-listen-action="prepare-chapter" ${warming || chapter || busy || !remainingCount || !available && !canUseCache ? 'disabled' : ''}>Prepare rest of chapter</button>
          </div>
          <p class="simple-listen-note">Playback starts after a short warmup of up to 3 passages, then prepares about 45 listening seconds ahead, with at most 12 future passages. Pause or Stop prevents new requests after the current one finishes.</p>
          ${state.mode === 'simple' && !showProgress ? `<p class="simple-listen-note">${escape(progressText)}</p>` : ''}
          <p class="simple-listen-saved">${state.takes.size} saved simple passage${state.takes.size === 1 ? '' : 's'}</p>
        </div>
      </details>
    </section>`;
    if (panel.container.innerHTML === html) return;
    const focused = typeof document === 'undefined' ? null : document.activeElement;
    const focusField = panel.container.contains?.(focused) && focused?.dataset;
    const focusSelector = focusField?.listenField ? `[data-listen-field="${focusField.listenField}"]` :
      focusField?.listenSpeed !== undefined ? '[data-listen-speed]' :
      focusField?.listenAction ? `[data-listen-action="${focusField.listenAction}"]` :
      focusField?.listenSummary !== undefined ? '[data-listen-summary]' : null;
    panel.container.innerHTML = html;
    if (focusSelector) panel.container.querySelector(focusSelector)?.focus({preventScroll:true});
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
        report(state,'cache_read_failed',error,'cache_read');
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
        if (event.target.dataset.listenSpeed !== undefined) {
          const rate = Number(event.target.value);
          if (playbackRates.includes(rate)) panel.options.onRateChange?.(rate);
          return;
        }
        const field = event.target.dataset.listenField;
        if (['mode','provider','voice','model'].includes(field)) change(panel,field,event.target.value);
      });
      container.addEventListener('click',event => {
        const action = event.target.closest('[data-listen-action]')?.dataset.listenAction;
        if (action === 'preview') {
          const selection = getSelection(panel.state.book);
          const segment = selectedSegment(panel);
          panel.options.onPreview?.({provider:selection.provider,voice:selection.voice,model:selection.model,segment_id:segment?.id || null});
          return;
        }
        if (action === 'stop') { stop(panel.state.book); panel.options.onStop?.(); }
        if (action === 'prepare-chapter' || action === 'retry') {
          const state = panel.state;
          const segment = state.book.segments.find(item => item.id === (state.intent?.segmentId || panel.options.segmentId)) ||
            state.book.segments.find(item => item.chapter_id === panel.options.chapterId);
          if (!segment) return;
          if (action === 'retry' && state.intent?.type === 'play' && state.intent.phase === 'playing') {
            state.error = '';
            state.intent.blocked = false;
            schedule(state);
            paint(panel);
            return;
          }
          const retryChapter = action === 'prepare-chapter' || state.intent?.type === 'chapter';
          panel.options.onStop?.();
          if (state.mode !== 'simple') change(panel,'mode','simple');
          if (retryChapter) void prepareChapter(state.book,segment,{playbackRate:panel.options.playbackRate});
          else { stop(state.book); state.error = ''; panel.options.onPlay?.(segment.id); }
        }
        if (action === 'start') {
          if (panel.state.mode === 'simple' && panel.options.onToggle) { panel.options.onToggle(); return; }
          if (['warmup','chapter'].includes(panel.state.intent?.phase)) return;
          if (panel.state.mode !== 'simple') change(panel,'mode','simple');
          const segment = selectedSegment(panel);
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
      state = {book,version:0,takes:new Map(),queue:[],active:null,intent:null,job:null,knownJob:null,loading:false,error:'',message:'',loadedKey:null,
        mode:prior.mode === 'simple' ? 'simple' : 'enhanced',provider:['system','gemini'].includes(prior.provider) ? prior.provider : systemAvailable ? 'system' : 'gemini',
        voices:{system:typeof prior.voices?.system === 'string' ? prior.voices.system : '',gemini:prior.voices?.gemini || 'Kore'},
        model:prior.model || status.tts_model || 'gemini-3.8-flash-tts',sessionId:prior.sessionId || null,sessionKey:prior.sessionKey || null};
      books.set(book.id,state);
    }
    if (state.intent && (panel.options.chapterId !== options.chapterId ||
        remaining(state,state.intent.segmentId).some(previous => {
          const next = book.segments.find(item => item.id === previous.id);
          return !next || sourceKey(next) !== sourceKey(previous);
        }))) invalidate(state);
    // A speed change during warmup affects its remaining target, but rendering
    // must not turn that intent into playback or begin the rolling queue.
    if (state.intent?.phase === 'warmup' && options.playbackRate !== undefined) {
      state.intent.rate = rateOf(options.playbackRate);
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
  window.BardicListen = {render,enabled,isSimple:enabled,take:resolve,resolve,ensure,prepare,updatePlayback,prepareChapter,getBuffer,getSelection,forgetAudio,stop,waitForStopped,allowsAdvance};
})();
