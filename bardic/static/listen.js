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
  // Gemini narration is prepared by a server chapter job in large chunks paced
  // to the project's request limits; the browser only watches and plays.
  const CHAPTER_TERMINAL = new Set(['completed','failed','cancelled','interrupted','quota_limited','budget_limited']);
  const CHUNK_PRESETS = [
    {id:'quick',label:'Quick start · 30 s, 1 min, then full',ramp:[30,60]},
    {id:'balanced',label:'Balanced · 1 min, then full',ramp:[60]},
    {id:'largest',label:'Largest requests only',ramp:[]},
  ];
  const CHUNK_LENGTHS = [[180,'3 min'],[300,'5 min'],[420,'7 min · recommended'],[470,'7.8 min · maximum']];
  const SAFETY_MARGIN = 5;
  const chunked = state => state?.mode === 'simple' && state.provider === 'gemini';
  const presetFor = ramp => CHUNK_PRESETS.find(preset => JSON.stringify(preset.ramp) === JSON.stringify((ramp || []).map(Number))) || null;
  function span(seconds) {
    if (!Number.isFinite(seconds)) return 'unknown';
    const value = Math.max(0,Math.round(seconds));
    if (value < 60) return `${value} s`;
    if (value < 3600) return `${Math.round(value/60)} min`;
    return `${Math.floor(value/3600)} h ${Math.round(value%3600/60)} min`;
  }

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
    state.message = chunked(state) && state.chapter.job && !CHAPTER_TERMINAL.has(state.chapter.job.status)
      ? 'Playback stopped. Chapter generation continues; use Stop generating to cancel it.'
      : 'Stopped. Finished simple takes are saved for the next listen.';
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
    const ahead = chunked(state) ? remaining(state,segmentId) : remaining(state,segmentId).slice(0,AHEAD_PASSAGES+1);
    for (const [index,segment] of ahead.entries()) {
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
    if (chunked(state)) return;
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
  async function prepare(book,segment,{playbackRate=1,offset=0,continuation=false}={}) {
    const state = stateFor(book);
    if (!state || state.mode !== 'simple') return resolve(book,segment);
    if (chunked(state)) return prepareFromChapter(state,segment,{playbackRate,offset,continuation});
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
    if (chunked(state)) { await queueChapter(state,segment); return; }
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

  // Chapter jobs ------------------------------------------------------------
  const chapterSegmentsOf = (state, chapterId) => state.book.segments.filter(segment => segment.chapter_id === chapterId);
  // Adopt a session only for the narrator configuration the request was sent
  // with; a late response for a previous voice/model must not bind its audio.
  function adoptSession(state, session, sentKey) {
    if (!session?.id || sentKey !== configKey(state)) return false;
    state.sessionId = session.id;
    state.sessionKey = sentKey;
    save(state);
    return true;
  }
  function chapterBody(state, segment, intent) {
    return {provider:'gemini',voice:state.voices.gemini || 'Kore',model:state.model,segment_id:segment.id,intent};
  }
  function settleWaiters(state) {
    for (const resolveWaiter of state.chapter.waiters.splice(0)) resolveWaiter();
  }
  const nextChapterUpdate = state => new Promise(resolveWaiter => state.chapter.waiters.push(resolveWaiter));
  const jobSignature = job => JSON.stringify([job?.id,job?.status,(job?.chunks || []).map(entry => [entry.status,entry.first_segment_id]),
    (job?.projection || []).map(entry => [entry.first_segment_id,entry.last_segment_id])]);
  function shareJob(state, job) {
    // The app shell shows the job banner and locks editing while it runs.
    if (job) state.panel?.options.onJob?.(job);
  }
  async function refreshChapter(state) {
    const job = state.chapter.job;
    if (!job) return;
    const jobs = await request(`/api/jobs?book_id=${encode(state.book.id)}`);
    // A newer job started while this read was in flight; never overwrite it.
    if (state.chapter.job?.id !== job.id) return;
    const latest = jobs.find(item => item.id === job.id);
    if (!latest) {
      if (++state.chapter.missing >= 3) throw new Error('The chapter job could not be found.');
      return;
    }
    state.chapter.missing = 0;
    const done = (latest.chunks || []).filter(entry => entry.status === 'done').length;
    const mark = `${latest.status}:${done}`;
    const changed = jobSignature(latest) !== jobSignature(state.chapter.job);
    state.chapter.job = latest;
    shareJob(state,latest);
    if (state.chapter.takesMark !== mark) {
      state.chapter.takesMark = mark;
      await loadSaved(state,true);
    }
    // Re-render the reader only when marks can change, not on every tick.
    if (changed) notify(state);
    paint(state.panel);
  }
  function watchChapter(state) {
    if (state.chapter.watching) return;
    state.chapter.watching = true;
    let failures = 0;
    const tick = async () => {
      // Stop when this book is no longer shown; render() resumes watching.
      if (!state.chapter.job || state.panel?.state !== state) { state.chapter.watching = false; settleWaiters(state); return; }
      try { await refreshChapter(state); failures = 0; state.chapter.error = ''; }
      catch (error) {
        // Read-only status polling can retry; it never resubmits generation.
        if (++failures > 5 || state.chapter.missing >= 3) { state.chapter.error = `Chapter status could not be read: ${error.message}`; state.chapter.watching = false; settleWaiters(state); paint(state.panel); return; }
      }
      settleWaiters(state);
      if (CHAPTER_TERMINAL.has(state.chapter.job?.status)) { state.chapter.watching = false; state.chapter.preview = null; state.chapter.previewKey = null; paint(state.panel); notify(state); return; }
      setTimeout(tick,1000);
    };
    setTimeout(tick,0);
  }
  async function startChapter(state, segment, intent) {
    const sentKey = configKey(state);
    const result = await request(base(state) + '/chapter',chapterBody(state,segment,intent));
    if (sentKey !== configKey(state) || stateFor(state.book) !== state) {
      // The narrator changed while this was in flight: that audio is no longer wanted.
      if (result.job?.id && !result.joined) cancelJob(result.job.id);
      throw new Error('The narrator changed before generation started. Press Play again.');
    }
    adoptSession(state,result.session,sentKey);
    state.chapter.job = result.job;
    state.chapter.takesMark = null;
    state.chapter.error = '';
    state.chapter.missing = 0;
    shareJob(state,result.job);
    watchChapter(state);
    notify(state);
    return result.job;
  }
  async function discoverChapter(state) {
    if (!chunked(state) || state.chapter.job || state.chapter.discovered === state.book.id) return;
    state.chapter.discovered = state.book.id;
    const sentKey = configKey(state);
    try {
      const jobs = await request(`/api/jobs?book_id=${encode(state.book.id)}`);
      if (sentKey !== configKey(state)) { state.chapter.discovered = null; return; }
      const job = jobs.find(item => item.kind === 'listen_chapter' && (!state.sessionId || item.session_id === state.sessionId));
      if (job && !state.chapter.job) {
        state.chapter.job = job;
        // A fresh browser learns the narrator session from a matching job so
        // saved chunks play without sending anything.
        if (!state.sessionId && job.voice === (state.voices.gemini || 'Kore') && job.model === state.model &&
            adoptSession(state,{id:job.session_id},sentKey)) await loadSaved(state,true);
        if (!CHAPTER_TERMINAL.has(job.status)) { shareJob(state,job); watchChapter(state); }
        paint(state.panel);
        notify(state);
      }
    } catch { state.chapter.discovered = null; /* Status is optional; Play or Queue report problems. */ }
  }
  async function queueChapter(state, segment) {
    state.error = '';
    try {
      await startChapter(state,segment,'queue');
      state.message = 'Chapter queued. Audio appears in the text as each chunk finishes; press Play any time.';
    } catch (error) {
      report(state,'listen_request_failed',error,'request',segment.id);
      state.error = error.message;
    }
    paint(state.panel);
  }
  function stopChapter(state) {
    const job = state.chapter.job;
    if (job && !CHAPTER_TERMINAL.has(job.status)) cancelJob(job.id);
    state.message = 'Stopping after the requests already sent. Their audio will be saved.';
    paint(state.panel);
  }
  const STOPPED = new Set(['failed','cancelled','interrupted','quota_limited','budget_limited']);
  function jobCovers(state, job, segment) {
    // Whether a running job is generating or will generate this passage.
    const segments = chapterSegmentsOf(state,segment.chapter_id);
    const position = new Map(segments.map((item,i) => [item.id,i]));
    const at = position.get(segment.id);
    return [...(job.chunks || []).filter(entry => entry.status === 'requesting'),...(job.projection || [])]
      .some(entry => position.get(entry.first_segment_id) <= at && at <= position.get(entry.last_segment_id));
  }
  async function prepareFromChapter(state, segment, {playbackRate=1,offset=0,continuation=false}) {
    let intent = state.intent;
    if (intent?.type !== 'play' || intent.chapterId !== segment.chapter_id) {
      if (intent) invalidate(state);
      intent = state.intent = {type:'play',phase:'warmup',chapterId:segment.chapter_id,segmentId:segment.id,
        offset:Math.max(0,Number(offset)||0),rate:rateOf(playbackRate),blocked:false};
    } else Object.assign(intent,{segmentId:segment.id,offset:Math.max(0,Number(offset)||0),rate:rateOf(playbackRate)});
    state.error = '';
    state.message = '';
    const version = state.version;
    const fail = message => {
      // Leave no warmup behind: the panel must offer Play/Resume again.
      if (state.intent === intent) state.intent = null;
      paint(state.panel);
      return new Error(message);
    };
    const ready = resolve(state.book,segment);
    const missing = remaining(state,segment.id).some(item => !resolve(state.book,item));
    const job = state.chapter.job;
    const sameChapter = job?.chapter_id === segment.chapter_id;
    const running = job && !CHAPTER_TERMINAL.has(job.status);
    const active = running && sameChapter;
    const elsewhere = running && !sameChapter;
    // Only an explicit Play starts or joins generation. Automatic continuation
    // never sends a request, and a job that failed (possibly after an uncertain,
    // billed request), was stopped or hit its quota restarts only through an
    // explicit Resume chapter.
    const stopped = sameChapter && STOPPED.has(job?.status);
    if (!ready && elsewhere) throw fail('Another chapter is being generated. Stop it there, or wait, before generating this one.');
    if (!ready && (continuation || stopped) && !active) {
      throw fail(stopped ? `${job.error || job.message || 'Chapter preparation stopped.'} Choose Resume chapter to continue generating.`
        : 'The rest of this chapter has not been generated. Press Play or Queue chapter to continue.');
    }
    if (!ready && continuation && active && !jobCovers(state,job,segment)) {
      throw fail('This passage is outside the chapter job. Press Play here to include it.');
    }
    if (missing && !continuation && !stopped && !elsewhere && (!active || !ready)) {
      // With enough audio ready ahead, skip the quick-start steps: full-size
      // requests stretch the daily quota further.
      const readyAhead = ready ? buffer({...state,intent:{...intent,phase:'playing'}}).seconds : 0;
      try { await startChapter(state,segment,readyAhead >= 90 ? 'queue' : 'play'); }
      catch (error) {
        if (!ready) throw fail(error.message);
        state.error = error.message;
      }
    }
    if (!current(state,version) || state.intent !== intent) return null;
    if (ready) { intent.phase = 'ready'; paint(state.panel); return ready; }
    paint(state.panel);
    for (;;) {
      await nextChapterUpdate(state);
      if (!current(state,version) || state.intent !== intent) return null;
      const audio = resolve(state.book,segment);
      if (audio) { intent.phase = 'ready'; paint(state.panel); return audio; }
      const latest = state.chapter.job;
      if (state.chapter.error) throw fail(state.chapter.error);
      if (!latest || CHAPTER_TERMINAL.has(latest.status) || !state.chapter.watching) {
        throw fail(latest?.status === 'completed' ? 'The chapter job finished without this passage. Press Play here to generate it.'
          : latest?.error || latest?.message || 'Chapter preparation stopped before this passage was ready.');
      }
      // After an explicit join the first poll may predate the new focus, so only
      // automatic continuation treats an uncovered passage as final.
      if (continuation && !jobCovers(state,latest,segment)) throw fail('This passage is outside the chapter job. Press Play here to include it.');
    }
  }

  /* Pure estimate of listening time and generation pace for one chapter.
     Durations are measured for generated passages and estimated from text for
     the rest. Generation finish times simulate the job's remaining chunks with
     its concurrency, per-minute limit and the library's daily request count. */
  function estimateChapter({segments, audioFor, job, fromIndex=0, offset=0, rate=1, now=Date.now()/1000}) {
    const calibration = job?.calibration || {};
    const charsPerSecond = calibration.chars_per_second || 14;
    const realtime = calibration.realtime_factor || 2;
    const latency = seconds => 3 + seconds/realtime;
    const scope = segments.slice(Math.max(0,fromIndex));
    const index = new Map(scope.map((segment,i) => [segment.id,i]));
    const durations = scope.map(segment => { const audio = audioFor(segment); return audio ? {ready:true,seconds:durationOf(audio)} : {ready:false,seconds:Math.max(.5,(segment.end-segment.start || segment.text.length)/charsPerSecond)}; });
    const finish = new Array(scope.length).fill(Infinity);
    const active = job && !CHAPTER_TERMINAL.has(job.status);
    let quotaBlocked = false, requestsLeft = null;
    if (active) {
      const concurrency = job.chunking?.concurrency || 2, rpm = job.limits?.rpm || 10;
      const sends = (job.chunks || []).map(entry => Date.parse(entry.started_at)/1000).filter(Number.isFinite);
      const running = (job.chunks || []).filter(entry => entry.status === 'requesting');
      const slots = running.map(entry => Math.max(now+1,Date.parse(entry.started_at)/1000 + (entry.expected_latency || latency(entry.expected_seconds))));
      while (slots.length < concurrency) slots.push(now + (job.waiting_seconds || 0));
      const assign = (entry, time) => {
        const first = index.get(entry.first_segment_id), last = index.get(entry.last_segment_id);
        if (first === undefined && last === undefined) return;
        for (let i = first ?? 0; i <= (last ?? scope.length-1); i++) finish[i] = Math.min(finish[i],time);
      };
      running.forEach((entry,i) => assign(entry,slots[i]));
      requestsLeft = Math.max(0,(job.quota?.rpd ?? Infinity) - (job.quota?.requests_today ?? 0));
      let left = requestsLeft;
      for (const entry of job.projection || []) {
        slots.sort((a,b) => a-b);
        let start = Math.max(slots[0],now);
        const recent = sends.filter(time => time > start-61).sort((a,b) => a-b);
        if (recent.length >= rpm) start = Math.max(start,recent[recent.length-rpm]+61);
        if (left <= 0) { quotaBlocked = true; continue; }
        left--;
        sends.push(start);
        const end = start + latency(entry.expected_seconds);
        slots[0] = end;
        assign(entry,end);
      }
    }
    let readySeconds = 0, remainingSeconds = 0, reach = -offset, wait = 0, maxRate = Infinity, catchUp = null, completion = now;
    durations.forEach((item,i) => {
      if (item.ready) { readySeconds += item.seconds; finish[i] = now; }
      else { remainingSeconds += item.seconds; completion = Math.max(completion,finish[i]); }
      if (!item.ready) {
        const reachAt = now + Math.max(0,reach)/rate;
        const deficit = finish[i] - reachAt + SAFETY_MARGIN;
        if (deficit > 0 && catchUp === null) catchUp = {index:i + Math.max(0,fromIndex),seconds:Math.max(0,reach)/rate};
        wait = Math.max(wait,deficit);
        maxRate = Math.min(maxRate,reach > 0 ? reach/Math.max(1e-6,finish[i]-now+SAFETY_MARGIN) : 0);
      }
      reach += item.seconds;
    });
    const firstAudio = durations[0] && !durations[0].ready ? finish[0] - now : 0;
    return {readySeconds,remainingSeconds,readyPassages:durations.filter(item => item.ready).length,totalPassages:scope.length,firstAudioSeconds:firstAudio,
      etaSeconds:remainingSeconds ? completion-now : 0,safe:wait <= 0,waitSeconds:Math.max(0,wait),
      maxSafeRate:Number.isFinite(maxRate) ? maxRate : Infinity,catchUp,quotaBlocked,requestsLeft,generating:Boolean(active)};
  }
  function chapterEstimate(state, panel) {
    const selected = selectedSegment(panel);
    if (!selected) return null;
    const segments = chapterSegmentsOf(state,selected.chapter_id);
    const job = state.chapter.job?.chapter_id === selected.chapter_id ? state.chapter.job : null;
    const intent = state.intent;
    const from = segments.findIndex(segment => segment.id === (intent?.segmentId || selected.id));
    return estimateChapter({segments,audioFor:segment => resolve(state.book,segment),job,fromIndex:Math.max(0,from),
      offset:intent?.segmentId ? intent.offset || 0 : 0,rate:rateOf(panel.options.playbackRate)});
  }
  function chapterMarks(book, chapterId) {
    const state = stateFor(book), marks = new Map();
    if (!chunked(state)) return marks;
    const segments = chapterSegmentsOf(state,chapterId);
    const chunkOrder = new Map();
    let previous = null;
    for (const segment of segments) {
      const audio = resolve(book,segment);
      if (!audio) { previous = null; continue; }
      const key = audio.chunk_id || audio.asset_id || segment.id;
      if (!chunkOrder.has(key)) chunkOrder.set(key,chunkOrder.size);
      marks.set(segment.id,{status:'ready',chunk:chunkOrder.get(key)%2,start:key !== previous && Boolean(audio.chunk_id)});
      previous = key;
    }
    const job = state.chapter.job;
    if (job && !CHAPTER_TERMINAL.has(job.status) && job.chapter_id === chapterId) {
      const position = new Map(segments.map((segment,i) => [segment.id,i]));
      const mark = (entry, status) => {
        const first = position.get(entry.first_segment_id), last = position.get(entry.last_segment_id);
        if (first === undefined || last === undefined) return;
        for (let i = first; i <= last; i++) if (!marks.has(segments[i].id)) marks.set(segments[i].id,{status});
      };
      (job.chunks || []).filter(entry => entry.status === 'requesting').forEach(entry => mark(entry,'generating'));
      (job.projection || []).forEach(entry => mark(entry,'queued'));
    }
    return marks;
  }
  async function saveChunking(panel, changes) {
    const current = panel.options.status?.listen_chunking || {};
    try {
      const status = await request('/api/settings',{listen_chunking:{...current,...changes}});
      if (panel.options.status) panel.options.status.listen_chunking = status.listen_chunking;
      panel.state.chapter.previewKey = null;
      panel.options.onSettings?.(status);
    } catch (error) { panel.state.error = error.message; }
    paint(panel);
  }
  async function loadPreview(state, panel) {
    const selected = selectedSegment(panel);
    if (!chunked(state) || !selected || (state.chapter.job && !CHAPTER_TERMINAL.has(state.chapter.job.status))) return;
    const key = JSON.stringify([state.book.id,state.book.revision,selected.id,configKey(state),state.takes.size,panel.options.status?.listen_chunking]);
    if (state.chapter.previewKey === key) return;
    state.chapter.previewKey = key;
    const sentKey = configKey(state);
    try {
      const preview = await request(base(state) + '/chapter/preview',chapterBody(state,selected,'queue'));
      if (state.chapter.previewKey !== key || sentKey !== configKey(state)) return;
      state.chapter.preview = preview;
      if (preview.session?.id && preview.session.id !== state.sessionId && adoptSession(state,preview.session,sentKey)) {
        await loadSaved(state,true);
        notify(state);
      }
      paint(panel);
    } catch { state.chapter.preview = null; }
  }
  function chapterMarkup(state, panel) {
    const estimate = chapterEstimate(state,panel);
    if (!estimate) return '';
    const rate = rateOf(panel.options.playbackRate);
    const job = state.chapter.job?.chapter_id === selectedSegment(panel)?.chapter_id ? state.chapter.job : null;
    const active = job && !CHAPTER_TERMINAL.has(job.status);
    const preview = state.chapter.preview;
    const quota = (active ? job.quota : preview?.quota) || job?.quota;
    const lines = [];
    let headline;
    if (active) {
      const running = (job.chunks || []).filter(entry => entry.status === 'requesting').length;
      headline = job.waiting_seconds ? `Waiting ${Math.ceil(job.waiting_seconds)} s for the per-minute limit` :
        running ? `Generating ${running} chunk${running === 1 ? '' : 's'}` : 'Planning the next chunk';
      headline += !estimate.remainingSeconds ? '' : estimate.quotaBlocked ? ' · the rest needs requests after the daily reset'
        : Number.isFinite(estimate.etaSeconds) ? ` · done in ~${span(estimate.etaSeconds)}` : ' · some passages here are outside this job';
    } else if (job?.status === 'quota_limited') headline = 'Daily request quota reached · finished audio is saved';
    else if (job && ['failed','cancelled','interrupted'].includes(job.status) && estimate.remainingSeconds) headline = 'Chapter preparation stopped · finished audio is saved';
    else headline = estimate.remainingSeconds ? 'Not queued yet' : 'Chapter ready';
    lines.push(`<div><span>${escape(headline)}</span><span>${estimate.readyPassages} of ${estimate.totalPassages} passages from here ready</span></div>`);
    lines.push(`<progress max="${estimate.readySeconds + estimate.remainingSeconds || 1}" value="${estimate.readySeconds}" aria-label="Chapter audio generated"></progress>`);
    lines.push(`<div class="chapter-queue-times"><span>Ready: ${escape(span(estimate.readySeconds/rate))} of listening at ${rate}×</span><span>Still to generate: ~${escape(span(estimate.remainingSeconds/rate))} of listening</span></div>`);
    if (estimate.remainingSeconds) {
      let safety;
      if (!active && job?.status === 'quota_limited') safety = {tone:'idle',text:'Generation is paused until the daily request quota resets. Choose Resume chapter after the reset; finished audio plays now.'};
      else if (!active) safety = {tone:'idle',text:'Queue the chapter to keep generating ahead of you.'};
      else if (estimate.quotaBlocked) safety = {tone:'warn',text:'The remaining chunks need more requests than are left today. Playback will stop where generation stops.'};
      else if (estimate.safe) safety = {tone:'safe',text:`Safe to listen at ${rate}× — generation should stay ahead of you.`};
      else if (!Number.isFinite(estimate.waitSeconds)) safety = {tone:'warn',text:'Some passages from here are not in this chapter job. Press Play at the first one to include them.'};
      else if (estimate.firstAudioSeconds > 0) {
        safety = {tone:'warn',text:`First audio here in ~${span(estimate.firstAudioSeconds)}. For gap-free listening at ${rate}×, start in ~${span(estimate.waitSeconds)}.`};
      } else {
        const slower = playbackRates.filter(value => value <= estimate.maxSafeRate).pop();
        const catchUp = estimate.catchUp ? ` in ~${span(estimate.catchUp.seconds)}` : '';
        safety = {tone:'warn',text:`At ${rate}× you would reach unfinished audio${catchUp}. For gap-free listening, wait ~${span(estimate.waitSeconds)}${slower ? ` or listen at ${slower}× or slower` : ''}.`};
      }
      lines.push(`<p class="chapter-queue-safety ${safety.tone}" role="status">${escape(safety.text)}</p>`);
    }
    const needed = active ? (job.projection || []).length : preview?.requests_needed;
    if (quota) {
      const reset = quota.resets_at ? new Date(quota.resets_at) : null;
      const resetText = reset && !Number.isNaN(reset.getTime()) ? ` Resets ${reset.toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})}.` : '';
      lines.push(`<p>${needed !== undefined ? `${needed} more request${needed === 1 ? '' : 's'} for this chapter. ` : ''}${quota.requests_today} of ${quota.rpd} daily Gemini requests used by this library.${escape(resetText)}</p>`);
    }
    const uncertain = (job?.chunks || []).filter(entry => entry.status === 'done' && (entry.flags || []).includes('weak_alignment')).length;
    if (uncertain) lines.push(`<p>Passage highlighting in ${uncertain} chunk${uncertain === 1 ? '' : 's'} is less certain (few clear pauses); the audio itself plays normally.</p>`);
    lines.push('<p class="chapter-queue-legend"><span class="legend-ready">ready</span><span class="legend-alt">next chunk</span><span class="legend-generating">generating</span><span class="legend-queued">queued</span> Passage timing inside a chunk is estimated from pauses.</p>');
    return `<div class="simple-listen-buffer chapter-queue" aria-live="polite">${lines.join('')}</div>`;
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
    // Any active job reserves the book (examples and device narration would be
    // rejected). Gemini Play/Queue stay usable during this book's chapter job.
    const anyBusy = Boolean(panel.options.busy && !state.job);
    const busy = anyBusy && !(chunked(state) && panel.options.busyKind === 'listen_chapter');
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
    const chapterJob = state.chapter.job;
    const jobRunning = chapterJob && !CHAPTER_TERMINAL.has(chapterJob.status);
    const generating = jobRunning && chapterJob.chapter_id === selected?.chapter_id;
    const elsewhere = jobRunning && !generating;
    const resumable = chapterJob && chapterJob.chapter_id === selected?.chapter_id && STOPPED.has(chapterJob.status) &&
      selected && remaining(state,selected.id).some(item => !resolve(state.book,item));
    const chunking = status.listen_chunking || {};
    const preset = presetFor(chunking.ramp_seconds);
    const chunkSettings = state.provider === 'gemini' ? `<label>First audio<select data-listen-field="chunk-preset" aria-label="How quickly the first chunk arrives">${CHUNK_PRESETS.map(item => `<option value="${item.id}" ${preset?.id === item.id ? 'selected' : ''}>${escape(item.label)}</option>`).join('')}${preset ? '' : `<option value="" selected>Custom · ${escape((chunking.ramp_seconds || []).join(', '))} s</option>`}</select></label><label>Full chunk length<select data-listen-field="chunk-length" aria-label="Audio per full-size request">${CHUNK_LENGTHS.map(([value,label]) => `<option value="${value}" ${Number(chunking.target_seconds) === value ? 'selected' : ''}>${escape(label)}</option>`).join('')}${CHUNK_LENGTHS.some(([value]) => value === Number(chunking.target_seconds)) || !chunking.target_seconds ? '' : `<option value="" selected>Custom · ${escape(span(chunking.target_seconds))}</option>`}</select></label>` : '';
    const progressMarkup = state.mode === 'simple' && chunked(state) ? chapterMarkup(state,panel) : state.mode === 'simple' ? `<div class="simple-listen-buffer" aria-live="polite"><div><span>${escape(progressText)}</span><span>${buffered.readyPassages} consecutive passage${buffered.readyPassages === 1 ? '' : 's'}</span></div><progress max="${maximum}" value="${progress}" aria-label="${chapter ? 'Chapter preparation' : 'Saved audio buffer'}"></progress><p>${chapter ? 'Preparing only the remainder of this chapter, one request at a time. Stop cancels future requests.' : warming ? 'A short warmup prepares up to 3 passages before playback. Saved durations determine progress.' : 'While playing, aim for 45 listening seconds ahead, with at most 12 future passages. If generation cannot keep pace, prepare the chapter before listening.'}</p></div>` : '';
    const html = `<section class="simple-listen"><div class="simple-listen-heading"><div><h3>Listen your way</h3><p>Voice here; playback and speed are shared with the player below.</p></div><label>Listening mode<select data-listen-field="mode" aria-label="Listening mode"><option value="enhanced" ${state.mode === 'enhanced' ? 'selected' : ''}>Enhanced production</option><option value="simple" ${state.mode === 'simple' ? 'selected' : ''}>Simple · one narrator</option></select></label></div><div class="simple-listen-settings"><label>Narration provider<select data-listen-field="provider" aria-label="Simple narration provider"><option value="system" ${state.provider === 'system' ? 'selected' : ''}>Device voices · local</option><option value="gemini" ${state.provider === 'gemini' ? 'selected' : ''}>Gemini · cloud</option></select></label><div class="simple-listen-voice"><label>Narrator voice<select data-listen-field="voice" aria-label="Simple narrator voice">${voices.map(voice => { const id = voice.id ?? voice.name; return `<option value="${escape(id)}" ${id === selectedVoice ? 'selected' : ''}>${escape(voice.name || id)}${voice.locale ? ` · ${escape(voice.locale)}` : ''}</option>`; }).join('')}</select></label><button type="button" class="button subtle" data-listen-action="preview" title="${previewDescription}" ${panel.options.previewing || anyBusy ? 'disabled' : ''}>Hear example</button></div>${state.provider === 'gemini' ? `<label>Speech model<select data-listen-field="model" aria-label="Simple speech model">${models.map(model => `<option value="${escape(model)}" ${model === state.model ? 'selected' : ''}>${escape(model)}</option>`).join('')}</select></label>` : ''}${chunkSettings}<label>Playback speed<select data-listen-speed aria-label="Simple listening playback speed">${playbackRates.map(rate => `<option value="${rate}" ${rate === rateOf(panel.options.playbackRate) ? 'selected' : ''}>${rate}×</option>`).join('')}</select></label></div><p class="simple-listen-note">${state.provider === 'gemini' ? 'Gemini receives the chapter text in large chunks, paced to your request limits, and may incur charges. Play starts or joins the chapter queue and begins when your passage is ready.' : 'Device narration stays on this computer and has no model charges.'} Simple playback stops at the end of this chapter. Highlighting follows each passage.</p>${progressMarkup}${!available && !canUseCache ? `<p class="simple-listen-note">${state.provider === 'system' ? 'Device narration is unavailable here. Choose another provider to generate new takes.' : 'Add a Gemini API key in Settings to generate new takes.'}</p>` : ''}<div class="simple-listen-actions"><button type="button" class="button primary" data-listen-action="start" ${startDisabled ? 'disabled' : ''} aria-label="${preparing ? 'Stop preparing narration' : playing ? 'Pause simple listening' : state.mode === 'simple' ? 'Play simple listening' : 'Start simple listening'}">${startLabel}</button>${chunked(state) ? (generating ? '<button type="button" class="button subtle" data-listen-action="stop-generating">Stop generating</button>' : `<button type="button" class="button subtle" data-listen-action="prepare-chapter" ${busy || elsewhere || !remainingCount || !available ? 'disabled' : ''} ${elsewhere ? 'title="Another chapter is being generated"' : ''}>${resumable ? 'Resume chapter' : 'Queue chapter'}</button>`) : `<button type="button" class="button subtle" data-listen-action="prepare-chapter" ${warming || chapter || busy || !remainingCount || !available && !canUseCache ? 'disabled' : ''}>Prepare rest of chapter</button>`}<button type="button" class="button subtle" data-listen-action="stop">Stop</button>${state.error ? '<button type="button" class="button subtle" data-listen-action="retry">Retry preparation</button>' : ''}<span>${state.takes.size} saved simple passage${state.takes.size === 1 ? '' : 's'}</span></div><p class="simple-listen-note">${chunked(state) && generating ? 'Stop generating lets requests already sent finish; their audio stays saved. Playback continues with what is ready.' : chunked(state) && elsewhere ? 'Another chapter is being generated for this book. Stop it in that chapter, or wait, before queueing this one.' : chunked(state) ? `Queue chapter prepares ${remainingCount} passages from here to the chapter end without starting playback. Each chunk is one request; no narration spending cap is enforced.` : `Prepare rest of chapter saves up to ${remainingCount} passages from here without starting playback. ${state.provider === 'gemini' ? 'Uncached passages can incur provider charges; no narration spending cap is enforced.' : 'Saved matching audio is reused.'}`}</p><p class="simple-listen-message ${state.error || state.chapter.error ? 'simple-listen-error' : ''}" role="${state.error || state.chapter.error ? 'alert' : 'status'}">${escape(state.error || state.chapter.error || (chunked(state) && chapterJob?.chapter_id === selected?.chapter_id && ['failed','quota_limited'].includes(chapterJob.status) ? chapterJob.error || chapterJob.message : '') || state.message || '')}</p></section>`;
    if (panel.container.innerHTML === html) return;
    const focused = typeof document === 'undefined' ? null : document.activeElement;
    const focusField = panel.container.contains?.(focused) && focused?.dataset;
    const focusSelector = focusField?.listenField ? `[data-listen-field="${focusField.listenField}"]` :
      focusField?.listenSpeed !== undefined ? '[data-listen-speed]' :
      focusField?.listenAction ? `[data-listen-action="${focusField.listenAction}"]` : null;
    panel.container.innerHTML = html;
    if (focusSelector) panel.container.querySelector(focusSelector)?.focus({preventScroll:true});
  }

  async function loadSaved(state, force = false) {
    if (!state.sessionId || state.sessionKey !== configKey(state)) return;
    const key = `${state.sessionId}:${state.book.revision}`;
    if (!force && state.loadedKey === key) return;
    state.loadedKey = key;
    const version = state.version;
    try {
      const result = await request(base(state) + '/takes?session_id=' + encode(state.sessionId));
      // A result for a book no longer shown is dropped; reload it on return.
      if (!current(state,version)) { if (state.panel?.state !== state && state.loadedKey === key) state.loadedKey = null; return; }
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
        if (field === 'chunk-preset') {
          const preset = CHUNK_PRESETS.find(item => item.id === event.target.value);
          if (preset) void saveChunking(panel,{ramp_seconds:preset.ramp});
          return;
        }
        if (field === 'chunk-length') {
          const seconds = Number(event.target.value);
          if (CHUNK_LENGTHS.some(([value]) => value === seconds)) void saveChunking(panel,{target_seconds:seconds});
          return;
        }
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
        if (action === 'stop-generating') { stopChapter(panel.state); return; }
        if (action === 'prepare-chapter' || action === 'retry') {
          const state = panel.state;
          const segment = state.book.segments.find(item => item.id === (state.intent?.segmentId || panel.options.segmentId)) ||
            state.book.segments.find(item => item.chapter_id === panel.options.chapterId);
          if (!segment) return;
          if (action === 'prepare-chapter' && chunked(state)) { void queueChapter(state,segment); return; }
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
        chapter:{job:null,preview:null,previewKey:null,watching:false,waiters:[],takesMark:null,error:'',discovered:null,missing:0},
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
    if (chunked(state) && state.chapter.job && !CHAPTER_TERMINAL.has(state.chapter.job.status)) watchChapter(state);
    return loadSaved(state).then(() => {
      if (!chunked(state)) return;
      void discoverChapter(state);
      void loadPreview(state,panel);
    });
  }
  function allowsAdvance(book, currentSegment, nextSegment) {
    return !enabled(book) || Boolean(currentSegment && nextSegment && currentSegment.chapter_id === nextSegment.chapter_id);
  }
  window.BardicListen = {render,enabled,isSimple:enabled,take:resolve,resolve,ensure,prepare,updatePlayback,prepareChapter,getBuffer,getSelection,forgetAudio,stop,waitForStopped,allowsAdvance,
    chapterMarks,estimateChapter,getChapterJob:book => stateFor(book)?.chapter.job || null};
})();
