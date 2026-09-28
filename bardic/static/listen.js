/* Single-voice listening: one serialized request at a time, with explicit playback
   or chapter-preparation intent. Rendering only reads saved local takes. */
(() => {
  'use strict';
  const panels = new WeakMap();
  const books = new Map();
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const encode = value => encodeURIComponent(value);
  const valid = audio => Boolean(audio?.url && !audio.stale && !audio.is_stale);
  // Server details state the condition; the hint says where to fix it.
  const HINTS = {gemini_key_missing:'Add a Gemini API key in Settings, or choose another narrator.',breeze_url_missing:'Add the Breeze server URL in Settings, or choose another narrator.'};
  const builtInVoices = ['Kore','Puck','Charon','Aoede','Fenrir','Leda','Orus','Zephyr','Callirrhoe','Autonoe','Enceladus','Iapetus','Umbriel','Algieba','Despina','Erinome','Algenib','Rasalgethi','Laomedeia','Achernar','Alnilam','Schedar','Gacrux','Pulcherrima','Achird','Zubenelgenubi','Vindemiatrix','Sadachbia','Sadaltager','Sulafat'];
  const sourceKey = segment => JSON.stringify([segment.id,segment.chapter_id,segment.start,segment.end,segment.text]);
  const PROVIDERS = ['system','gemini','breeze'];
  const PROVIDER_LABELS = {system:'Device',gemini:'Gemini',breeze:'Breeze'};
  // Only Gemini chooses among speech models; device voices use the macOS model
  // and Breeze resolves its single model on the server.
  const modelFor = state => state.provider === 'gemini' ? state.model : state.provider === 'system' ? 'macos-say' : null;
  const voiceFor = state => state.voices[state.provider] || (state.provider === 'gemini' ? 'Kore' : '');
  // Library voices are sent as "library:<id>"; the server resolves them to the
  // voice's current version. Breeze "" means the default voice.
  const LIBRARY = 'library:';
  const libraryVoice = (state, id) => (state.library?.voices || []).find(voice => voice.id === id) || null;
  function resolvedVoice(state) {
    const value = state.voices[state.provider] || '';
    const id = value.startsWith(LIBRARY) ? value.slice(LIBRARY.length) : !value && state.provider === 'breeze' ? state.library?.defaults?.breeze : null;
    const voice = id ? libraryVoice(state,id) : null;
    const version = voice && (voice.versions || []).find(item => item.version === voice.current_version);
    return voice ? {voice,version} : null;
  }
  // The concrete voice the server will use; it names listening jobs and sessions.
  function concreteVoice(state) {
    const resolved = resolvedVoice(state);
    return resolved ? resolved.version?.provider_voice_id ?? null : voiceFor(state);
  }
  // A session is pinned to a concrete voice version. A new current version (or a
  // changed default or revision) is a different narrator, so it must not reuse
  // the old session. Direct Breeze ids fall back to the last server check.
  function voiceIdentity(state) {
    const resolved = resolvedVoice(state);
    if (resolved) return [resolved.voice.id,resolved.voice.current_version,resolved.version?.provider_voice_id ?? null,resolved.version?.revision ?? null];
    if (state.provider !== 'breeze') return undefined;
    return (state.status?.breeze?.voices || []).find(voice => voice.id === state.voices.breeze)?.revision || null;
  }
  const configKey = state => JSON.stringify([state.provider,state.voices[state.provider],modelFor(state),
    ...(voiceIdentity(state) !== undefined ? [voiceIdentity(state)] : [])]);
  const storageKey = id => `bardic:listen:${id}`;
  const stateFor = book => book && books.get(book.id);
  // A saved performance plays through the same one-voice path, but never generates.
  const enabled = book => ['simple','performance'].includes(stateFor(book)?.mode);
  const performing = state => state?.mode === 'performance';
  const playbackRates = [.75,1,1.25,1.5,1.75,2,2.25,2.5];
  const selectedSegment = panel => panel.state.book.segments.find(segment => segment.id === panel.options.segmentId) ||
    panel.state.book.segments.find(segment => segment.chapter_id === panel.options.chapterId);

  function getSelection(book) {
    const state = stateFor(book);
    return state ? {mode:state.mode,provider:state.provider,voice:state.voices[state.provider],model:modelFor(state)} : null;
  }
  function getPerformance(book) {
    const state = stateFor(book);
    if (!performing(state)) return null;
    const performance = state.performance;
    return {id:state.performanceId,name:performance?.record.name || 'Saved performance',label:performance?.record.narrator_label || '',
      loaded:Boolean(performance),job:performance?.record.job || null,record:performance?.record || null};
  }

  function saved(id) {
    try {
      const value = localStorage.getItem(storageKey(id)) ?? localStorage.getItem(`spintails:listen:${id}`);
      return JSON.parse(value) || {};
    } catch { return {}; }
  }
  function save(state) {
    const value = {mode:state.mode,provider:state.provider,voices:state.voices,model:state.model,
      sessionId:state.sessionId,sessionKey:state.sessionKey,continuous:state.continuous,performanceId:state.performanceId || null};
    try { localStorage.setItem(storageKey(state.book.id),JSON.stringify(value)); } catch { /* Storage is optional. */ }
  }
  async function request(url, body) {
    const response = await fetch(url, body === undefined ? {headers:{Accept:'application/json'}} :
      {method:'POST',headers:{Accept:'application/json','Content-Type':'application/json'},body:JSON.stringify(body)});
    let result;
    try { result = await response.json(); } catch { result = null; }
    if (!response.ok) {
      const detail = typeof result?.detail === 'string' ? result.detail : `Listening request failed (${response.status}).`;
      const error = new Error(HINTS[result?.code] ? `${detail} ${HINTS[result.code]}` : detail);
      error.status = response.status;
      error.code = result?.code;
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
  const WARM_SECONDS = 10, BUFFER_SECONDS = 45, WARM_PASSAGES = 3, AHEAD_PASSAGES = 12, SCAN_SECONDS = 1200;
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
  // Chunked chapter jobs are a provider capability (quota-paced Gemini today).
  // Without capability metadata, fall back to the historical Gemini-only rule.
  function chunkCapable(state) {
    const capabilities = state?.status?.narration_providers?.[state.provider]?.capabilities;
    return capabilities ? capabilities.chunked_listening === true : state?.provider === 'gemini';
  }
  const chunked = state => state?.mode === 'simple' && chunkCapable(state);
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
  // keepAhead: the listener is moving within the book while still listening,
  // so an automatically started chapter job is still wanted.
  function stop(book, {keepAhead = false} = {}) {
    const state = stateFor(book);
    if (!state) return;
    invalidate(state);
    const cancelledAhead = !keepAhead && cancelAutomatic(state);
    state.message = cancelledAhead ? 'Stopped. Generation ahead of you was cancelled; finished audio is saved.'
      : chunked(state) && state.chapter.job && !CHAPTER_TERMINAL.has(state.chapter.job.status)
      ? 'Playback stopped. Chapter generation continues; use Stop generating to cancel it.'
      : 'Stopped. Finished simple takes are saved for the next listen.';
    paint(state.panel);
  }
  // A chapter job that continuous listening started on its own serves only the
  // listening that asked for it: Pause or Stop cancels it. Requests already sent
  // finish and are saved. A job the listener started or joined is left alone.
  function cancelAutomatic(state) {
    const id = state.chapter?.autoJobId;
    if (!id) return false;
    state.chapter.autoJobId = null;
    const job = state.chapter.job;
    if (job?.id !== id || CHAPTER_TERMINAL.has(job.status)) return false;
    cancelJob(id);
    state.chapter.pauseCancelled = id;
    return true;
  }
  function resolve(book, segment) {
    if (!segment) return null;
    const state = stateFor(book);
    if (performing(state)) {
      const audio = state.performance?.audio.get(segment.id);
      return valid(audio) ? audio : null;
    }
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
  // Book reading order: chapters in their listed order. Continuous listening
  // looks ahead through it; chapter preparation stays within one chapter.
  function readingOrder(state) {
    if (state.order?.book !== state.book) {
      const chapters = state.book.chapters;
      const list = chapters?.length ? chapters.flatMap(chapter => state.book.segments.filter(segment => segment.chapter_id === chapter.id)) : state.book.segments;
      const backMatter = new Set((chapters || []).filter(chapter => chapter.kind === 'back_matter').map(chapter => chapter.id));
      state.order = {book:state.book,list,backMatter,index:new Map(list.map((segment,i) => [segment.id,i]))};
    }
    return state.order;
  }
  // Continuous listening does not run on into back matter (notes, index,
  // acknowledgements) unless listening started there.
  function continuesInto(state, from, to) {
    if (from.chapter_id === to.chapter_id) return true;
    const {backMatter} = readingOrder(state);
    return !backMatter.has(to.chapter_id) || backMatter.has(from.chapter_id);
  }
  function upcoming(state, segmentId, limit = Infinity) {
    if (!state.continuous) return remaining(state,segmentId).slice(0,limit);
    const order = readingOrder(state), start = order.index.get(segmentId);
    if (start === undefined) return [];
    const result = [];
    for (let i = start; i < order.list.length && result.length < limit; i++) {
      if (result.length && !continuesInto(state,order.list[start],order.list[i])) break;
      result.push(order.list[i]);
    }
    return result;
  }
  function nextChapterId(state, segmentId) {
    const order = readingOrder(state), start = order.index.get(segmentId);
    if (start === undefined) return undefined;
    const chapter = order.list[start].chapter_id;
    return order.list.slice(start + 1).find(segment => segment.chapter_id !== chapter && inPerformance(state,segment))?.chapter_id;
  }
  // A performance plays only its chosen chapters; everything else is eligible.
  const inPerformance = (state, segment) => !performing(state) || Boolean(state.performance?.chapterIds.has(segment.chapter_id));
  // The passage playback moves to after this one, or null where it stops.
  function nextSegment(book, segment) {
    const state = stateFor(book);
    if (!state || !segment) return null;
    const order = readingOrder(state), start = order.index.get(segment.id);
    if (start === undefined) return null;
    for (let i = start + 1; i < order.list.length; i++) {
      const next = order.list[i];
      // Continuous performance playback skips chapters it does not include.
      if (!inPerformance(state,next)) { if (state.continuous) continue; return null; }
      return allowsAdvance(book,segment,next) ? next : null;
    }
    return null;
  }
  // A play intent continues into the next chapter only by automatic
  // continuation with continuous listening on; anything else starts afresh.
  const keepsIntent = (state, intent, segment, continuation) => intent?.type === 'play' &&
    (intent.chapterId === segment.chapter_id || (continuation && state.continuous));
  function buffer(state) {
    const intent = state.intent;
    const segmentId = intent?.segmentId || state.panel.options.segmentId;
    const rate = intent?.rate || rateOf(state.panel.options.playbackRate);
    let seconds = 0, readyPassages = 0;
    const ahead = upcoming(state,segmentId,chunked(state) ? Infinity : AHEAD_PASSAGES+1);
    for (const [index,segment] of ahead.entries()) {
      const audio = resolve(state.book,segment);
      if (!audio) break;
      seconds += Math.max(0,durationOf(audio)-(index === 0 ? intent?.offset || 0 : 0))/rate;
      readyPassages++;
      // Beyond the lookahead horizon the exact figure changes nothing; stop scanning.
      if (seconds*rate >= SCAN_SECONDS) break;
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
      model:modelFor(state),segment_id:segment.id});
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
    // Defence in depth: nothing queued may be sent while a performance plays.
    if (performing(state)) { for (const task of state.queue.splice(0)) task.resolve(null); return; }
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
    if (performing(state)) return resolve(book,segment);
    if (!state || state.mode !== 'simple') return resolve(book,segment);
    state.error = '';
    return enqueue(state,segment,true);
  }
  function schedule(state) {
    const intent = state.intent;
    // A saved performance never generates; its job is the only producer.
    if (performing(state)) return;
    if (chunked(state)) { queueAhead(state); return; }
    if (!intent || intent.blocked || intent.phase !== 'playing' || state.active || state.queue.length) return;
    const list = upcoming(state,intent.segmentId).slice(0,AHEAD_PASSAGES+1);
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
    if (performing(state)) return preparePerformance(state,segment,{playbackRate,offset,continuation});
    if (!state || state.mode !== 'simple') return resolve(book,segment);
    if (chunked(state)) return prepareFromChapter(state,segment,{playbackRate,offset,continuation});
    let intent = state.intent;
    if (!keepsIntent(state,intent,segment,continuation)) {
      if (intent) invalidate(state);
      state.error = '';
      intent = state.intent = {type:'play',phase:'warmup',chapterId:segment.chapter_id,segmentId:segment.id,
        offset:Math.max(0,Number(offset)||0),rate:rateOf(playbackRate),blocked:false};
    } else {
      intent.chapterId = segment.chapter_id;
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
    for (const next of upcoming(state,segment.id).slice(0,WARM_PASSAGES)) {
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
    // While a performance is still processing, pick up newly finished audio.
    if (performing(state) && state.performance && PERFORMANCE_ACTIVE.has(state.performance.record.job?.status) &&
        Date.now() - state.performance.loadedAt > 5000) void loadPerformance(state,state.performanceId).catch(() => {});
    if (intent.chapterId !== segment?.chapter_id) {
      if (!state.continuous || !segment) { stop(book); return; }
      intent.chapterId = segment.chapter_id;
    }
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

  // Saved performances -------------------------------------------------------
  // A performance is prepared ahead by a server job. Playing it only reads its
  // audio; a passage it lacks waits while that job runs, otherwise stops.
  const PERFORMANCE_ACTIVE = new Set(['queued','running']);
  const performanceBase = state => `/api/books/${encode(state.book.id)}/performances`;
  async function loadPerformance(state, id) {
    const [{performance}, {audio}] = await Promise.all([request(`${performanceBase(state)}/${encode(id)}`),
      request(`${performanceBase(state)}/${encode(id)}/audio`)]);
    if (state.performanceId !== id) return null;
    state.performance = {record:performance,chapterIds:new Set(performance.chapter_ids || []),
      audio:new Map(Object.entries(audio || {})),loadedAt:Date.now()};
    paint(state.panel); notify(state);
    return state.performance;
  }
  async function usePerformance(book, record) {
    const state = stateFor(book);
    if (!state?.panel || !record?.id) return null;
    invalidate(state);
    state.panel.options.onStop?.();
    state.mode = 'performance';
    state.performanceId = record.id;
    state.performance = null;
    state.error = ''; state.message = '';
    save(state);
    await loadPerformance(state,record.id);
    return state.performance;
  }
  function leavePerformance(book) {
    const state = stateFor(book);
    if (performing(state) && state.panel) change(state.panel,'mode','simple');
  }
  function refreshPerformance(book) {
    const state = stateFor(book);
    return performing(state) && state.performanceId ? loadPerformance(state,state.performanceId) : Promise.resolve(null);
  }
  async function preparePerformance(state, segment, {playbackRate=1,offset=0,continuation=false}) {
    let intent = state.intent;
    if (!keepsIntent(state,intent,segment,continuation)) {
      if (intent) invalidate(state);
      intent = state.intent = {type:'play',phase:'warmup',chapterId:segment.chapter_id,segmentId:segment.id,
        offset:Math.max(0,Number(offset)||0),rate:rateOf(playbackRate),blocked:false};
    } else Object.assign(intent,{chapterId:segment.chapter_id,segmentId:segment.id,offset:Math.max(0,Number(offset)||0),rate:rateOf(playbackRate)});
    state.error = '';
    const version = state.version;
    const fail = message => {
      if (state.intent === intent) state.intent = null;
      paint(state.panel);
      return new Error(message);
    };
    if (!state.performance) {
      try { await (state.performanceLoading || loadPerformance(state,state.performanceId)); }
      catch (error) { throw fail(`The saved performance could not be loaded: ${error.message}`); }
      if (!current(state,version) || state.intent !== intent) return null;
    }
    let failures = 0;
    for (;;) {
      const audio = resolve(state.book,segment);
      if (audio) { intent.phase = 'ready'; paint(state.panel); return audio; }
      const {record} = state.performance;
      const name = record.name || 'this performance';
      if (!state.performance.chapterIds.has(segment.chapter_id)) {
        const chapter = state.book.chapters?.find(item => item.id === segment.chapter_id)?.title || 'This chapter';
        throw fail(`${chapter} is not part of “${name}”. Choose one of its chapters, or listen with a narrator instead.`);
      }
      const job = record.job;
      if (!PERFORMANCE_ACTIVE.has(job?.status)) {
        throw fail(job?.status === 'quota_limited' ? `“${name}” paused at the daily request limit. Resume it after the reset; finished audio plays now.`
          : job?.status === 'failed' ? `“${name}” stopped on an error before this passage. Resume it to finish.`
          : `This passage of “${name}” has not been processed. Resume the performance to finish it.`);
      }
      state.message = `Waiting for “${name}” to reach this passage…`;
      paint(state.panel);
      await wait(2500);
      if (!current(state,version) || state.intent !== intent) return null;
      try { await loadPerformance(state,state.performanceId); failures = 0; }
      catch (error) { if (++failures > 3) throw fail(`The performance status could not be read: ${error.message}`); }
      if (!current(state,version) || state.intent !== intent) return null;
    }
  }
  // Voice menu and availability for any provider, for the performance form.
  function narratorOptions(book, provider) {
    const state = stateFor(book);
    if (!state || !PROVIDERS.includes(provider)) return null;
    const probe = {...state,provider};
    const {voices,selectedVoice,available,breezeVoiceReady} = narratorChoices(probe);
    return {provider,available,breezeVoiceReady,voice:selectedVoice,model:provider === 'gemini' ? state.model : null,
      voices:voices.map(voice => ({id:voice.id ?? voice.name,name:voice.name || voice.id,locale:voice.locale || '',usable:voice.usable !== false}))};
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
    return {provider:state.provider,voice:voiceFor(state),model:modelFor(state),segment_id:segment.id,intent};
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
  async function startChapter(state, segment, intent, automatic = false) {
    const sentKey = configKey(state);
    const result = await request(base(state) + '/chapter',chapterBody(state,segment,intent));
    if (sentKey !== configKey(state) || stateFor(state.book) !== state) {
      // The narrator changed while this was in flight: that audio is no longer wanted.
      if (result.job?.id && !result.joined) cancelJob(result.job.id);
      throw new Error('The narrator changed before generation started. Press Play again.');
    }
    adoptSession(state,result.session,sentKey);
    // Only a job this request created is automatic; joining keeps its owner.
    if (!automatic) state.chapter.autoJobId = null;
    else if (!result.joined) state.chapter.autoJobId = result.job?.id || null;
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
        if (!state.sessionId && job.voice === concreteVoice(state) && job.model === modelFor(state) &&
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
    // Stop generating also ends automatic queueing until the next explicit Play.
    state.chapter.hold = true;
    if (job && !CHAPTER_TERMINAL.has(job.status)) cancelJob(job.id);
    state.message = 'Stopping after the requests already sent. Their audio will be saved.';
    paint(state.panel);
  }
  const STOPPED = new Set(['failed','cancelled','interrupted','quota_limited','budget_limited']);
  const LIMITED = new Set(['quota_limited','budget_limited']);
  // A stopped job needs an explicit Resume, except one that Pause cancelled
  // because continuous listening had started it: nothing uncertain was lost.
  const stoppedJob = (state, job) => Boolean(job && STOPPED.has(job.status) && job.id !== state.chapter.pauseCancelled);
  // Continuous listening keeps about this much media ready ahead by queueing
  // the next chapter's job while playback runs. Only a playing intent queues.
  const QUEUE_AHEAD_SECONDS = 600;
  function queueAhead(state) {
    const intent = state.intent;
    if (!state.continuous || state.chapter.hold || state.chapter.autoStarting || !intent || intent.type !== 'play' ||
        intent.phase !== 'playing' || intent.blocked) return;
    const job = state.chapter.job;
    // One chapter job per book at a time; a quota or budget stop waits for an explicit Resume.
    if (job && (!CHAPTER_TERMINAL.has(job.status) || LIMITED.has(job.status))) return;
    let seconds = 0, target = null;
    for (const [index,segment] of upcoming(state,intent.segmentId).entries()) {
      const audio = resolve(state.book,segment);
      if (!audio) { target = segment; break; }
      seconds += Math.max(0,durationOf(audio)-(index === 0 ? intent.offset || 0 : 0));
      if (seconds >= QUEUE_AHEAD_SECONDS) return;
    }
    // A failed or stopped job may have sent an uncertain request; only Resume chapter retries it.
    if (!target || (stoppedJob(state,job) && job.chapter_id === target.chapter_id)) return;
    if (Date.now() < (state.chapter.retryAt || 0)) return;
    intent.queued ||= new Set();
    if (intent.queued.has(target.chapter_id)) return; // One automatic attempt per chapter per Play.
    intent.queued.add(target.chapter_id);
    state.chapter.autoStarting = startChapter(state,target,'queue',true)
      .then(() => {
        // Paused while the request was in flight: the new job is not wanted.
        if (state.intent !== intent) cancelAutomatic(state);
        else state.message = 'Generating the next chapter ahead of you.';
      })
      .catch(error => {
        report(state,'listen_request_failed',error,'request',target.id);
        // A busy book (409) sent nothing; try again shortly instead of giving up.
        if (error.status === 409) { intent.queued.delete(target.chapter_id); state.chapter.retryAt = Date.now() + 20000; }
        if (state.intent === intent) state.message = `The next chapter could not be queued: ${error.message}`;
      })
      .finally(() => { state.chapter.autoStarting = null; paint(state.panel); });
  }
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
    // Only a running Play crossing into the next chapter may start that chapter's job.
    const crossed = continuation && keepsIntent(state,intent,segment,continuation) && intent.chapterId !== segment.chapter_id;
    if (!keepsIntent(state,intent,segment,continuation)) {
      if (intent) invalidate(state);
      intent = state.intent = {type:'play',phase:'warmup',chapterId:segment.chapter_id,segmentId:segment.id,
        offset:Math.max(0,Number(offset)||0),rate:rateOf(playbackRate),blocked:false};
    } else Object.assign(intent,{chapterId:segment.chapter_id,segmentId:segment.id,offset:Math.max(0,Number(offset)||0),rate:rateOf(playbackRate)});
    if (!continuation) state.chapter.hold = false;
    // A lookahead request still in flight decides whether this chapter has a job.
    if (crossed && state.chapter.autoStarting) {
      try { await state.chapter.autoStarting; } catch { /* Reported by the lookahead. */ }
      if (state.intent !== intent) return null;
    }
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
    const stopped = sameChapter && stoppedJob(state,job);
    // Continuous listening: playback reaching a chapter with no job starts its
    // job, unless generation was stopped, limited or failed there.
    const carryOn = crossed && !state.chapter.hold && !stopped && !LIMITED.has(job?.status) && !active && !elsewhere;
    if (!ready && elsewhere) throw fail('Another chapter is being generated. Stop it there, or wait, before generating this one.');
    if (!ready && (continuation || stopped) && !active && !carryOn) {
      throw fail(stopped ? `${job.error || job.message || 'Chapter preparation stopped.'} Choose Resume chapter to continue generating.`
        : crossed && LIMITED.has(job?.status) ? `${job.error || job.message || 'The request limit was reached.'} Finished audio is saved; choose Resume chapter after the reset.`
        : crossed && state.chapter.hold ? 'Generation was stopped, so the next chapter was not started. Press Play to continue.'
        : 'The rest of this chapter has not been generated. Press Play or Queue chapter to continue.');
    }
    // A job queued ahead a moment ago may not have planned its chunks yet.
    const ownAhead = crossed && Boolean(job?.id) && job.id === state.chapter.autoJobId;
    if (!ready && continuation && active && !ownAhead && !jobCovers(state,job,segment)) {
      throw fail('This passage is outside the chapter job. Press Play here to include it.');
    }
    if (missing && (!continuation || (carryOn && !ready)) && !stopped && !elsewhere && (!active || !ready)) {
      // With enough audio ready ahead, skip the quick-start steps: full-size
      // requests stretch the daily quota further.
      const readyAhead = ready ? buffer({...state,intent:{...intent,phase:'playing'}}).seconds : 0;
      try { await startChapter(state,segment,readyAhead >= 90 ? 'queue' : 'play',carryOn); }
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
      if (continuation && !carryOn && !ownAhead && !jobCovers(state,latest,segment)) throw fail('This passage is outside the chapter job. Press Play here to include it.');
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
      const concurrency = job.chunking?.concurrency || 2, rpm = job.speech_limits?.rpm || 10;
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

  // Breeze and Gemini narrators come from the voice library (/api/voices).
  // Breeze "" is Default: the library's default Breeze voice, resolved by the
  // server. Gemini keeps its built-in names alongside library voices.
  function libraryOptions(state, provider) {
    const unsupported = provider === 'gemini' && state.library?.providers?.gemini?.designed_voices_supported === false;
    return (state.library?.voices || []).filter(voice => voice.provider === provider && !voice.deleted)
      .map(voice => ({id:LIBRARY + voice.id,name:voice.name,usable:!unsupported && voice.assignable !== false,
        reason:unsupported ? 'needs Gemini 3.8 TTS' : voice.assignable === false ? 'unavailable' : ''}));
  }
  function breezeDefaultName(state) {
    const id = state.library?.defaults?.breeze;
    return id ? libraryVoice(state,id)?.name || '' : '';
  }
  // Earlier saved choices named a Breeze server voice directly. Follow the
  // library voice whose current version is that server voice, when there is one.
  function adoptLibraryChoice(state) {
    const value = state.voices.breeze;
    if (!value || value.startsWith(LIBRARY) || !state.library) return false;
    const match = (state.library.voices || []).find(voice => voice.provider === 'breeze' && !voice.deleted &&
      (voice.versions || []).find(item => item.version === voice.current_version)?.provider_voice_id === value);
    if (!match) return false;
    state.voices.breeze = LIBRARY + match.id;
    return true;
  }
  function change(panel, field, value) {
    const state = panel.state;
    invalidate(state);
    panel.options.onStop?.();
    state.error = '';
    state.message = '';
    if (field === 'mode') {
      state.mode = value === 'simple' ? 'simple' : value === 'performance' && state.performanceId ? 'performance' : 'enhanced';
      if (state.mode !== 'performance') { state.performanceId = null; state.performance = null; }
    }
    else if (field === 'provider') {
      state.provider = PROVIDERS.includes(value) ? value : 'system';
      // Selecting an unchecked Breeze server asks the app to fetch its voices once.
      if (state.provider === 'breeze' && state.status?.breeze?.state === 'unchecked') panel.options.onRefreshBreeze?.();
    }
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
  // Narrator menu for the current provider, and whether new audio can be made.
  function narratorChoices(state, status = state.status || {}) {
    const system = status.providers?.find(provider => provider.id === 'system');
    const gemini = status.providers?.find(provider => provider.id === 'gemini');
    const breeze = status.providers?.find(provider => provider.id === 'breeze');
    // Breeze needs a chosen voice or a default voice before anything can be requested.
    const breezeVoiceReady = Boolean(state.voices.breeze || state.library?.defaults?.breeze);
    const availability = {system:system?.available !== false,breeze:breeze?.available === true,gemini:Boolean(status.has_api_key || gemini?.available)};
    const available = state.provider === 'breeze' ? availability.breeze && breezeVoiceReady : availability[state.provider];
    const defaultName = breezeDefaultName(state);
    const voices = state.provider === 'system'
      ? [{id:'',name:'Default device voice'},...(status.system_voices || [])]
      : state.provider === 'breeze'
        ? [{id:'',name:defaultName ? `Default (${defaultName})` : 'Default (none set yet)'},...libraryOptions(state,'breeze')]
        : [...builtInVoices.map(name => ({id:name,name})),...libraryOptions(state,'gemini')];
    const selectedVoice = state.voices[state.provider] || '';
    if (selectedVoice && !voices.some(voice => (voice.id ?? voice.name) === selectedVoice)) {
      voices.push({id:selectedVoice,name:selectedVoice.startsWith(LIBRARY) ? 'Deleted voice'
        : state.provider === 'breeze' ? `${selectedVoice} (not in library)` : selectedVoice});
    }
    return {breezeVoiceReady,available,availability,voices,selectedVoice};
  }
  // A compact view of the narrator choice for the app's listen sheet.
  function choices(book) {
    const state = stateFor(book);
    if (!state) return null;
    const {available,availability,voices,selectedVoice,breezeVoiceReady} = narratorChoices(state);
    return {mode:state.mode,provider:state.provider,providers:PROVIDERS.map(id => ({id,label:PROVIDER_LABELS[id],available:availability[id]})),
      voices:voices.map(voice => ({id:voice.id ?? voice.name,name:voice.name || voice.id,locale:voice.locale || '',usable:voice.usable !== false})),
      voice:selectedVoice,available,breezeVoiceReady,continuous:state.continuous,chunked:chunkCapable(state),
      saved:state.takes.size,error:state.error || state.chapter.error || '',message:state.message || ''};
  }
  // Changing narrator from the sheet uses the same path as the panel, so it
  // stops pending work and starts a new session exactly as the panel does.
  function choose(book, field, value) {
    const state = stateFor(book);
    if (!state?.panel || !['mode','provider','voice'].includes(field)) return;
    change(state.panel,field,value);
  }
  function paintPerformance(panel) {
    const state = panel.state, performance = state.performance, record = performance?.record;
    const playing = panel.options.playing, preparing = panel.options.preparing || state.intent?.phase === 'warmup';
    const progress = record?.progress;
    const job = record?.job;
    const status = !record ? 'Loading…' : PERFORMANCE_ACTIVE.has(job?.status) ? `Processing · ${job.message || ''}`
      : `${progress?.passages_ready ?? 0} of ${progress?.passages_total ?? 0} passages ready`;
    const html = `<section class="simple-listen" aria-label="Listening settings">
      <div class="simple-listen-heading">
        <div><h3>Saved performance</h3><p>${escape(record?.name || 'Saved performance')}${record?.narrator_label ? ` · ${escape(record.narrator_label)}` : ''}</p></div>
        <label>Playback source<select data-listen-field="mode" aria-label="Listening mode"><option value="performance" selected>Saved performance</option><option value="simple">One narrator</option><option value="enhanced">Studio voices</option></select></label>
      </div>
      <p class="simple-listen-note">${escape(status)}. Plays only audio this performance already has; nothing new is generated while you listen.</p>
      <div class="simple-listen-actions">
        <button type="button" class="button primary" data-listen-action="start" aria-label="${preparing ? 'Stop preparing narration' : playing ? 'Pause simple listening' : 'Play simple listening'}">${preparing ? 'Preparing…' : playing ? 'Pause' : 'Play'}</button>
        <button type="button" class="button subtle" data-listen-action="stop">Stop</button>
        <span>${state.continuous ? 'Continues through its chapters' : 'Stops at chapter end'}</span>
      </div>
      <p class="simple-listen-message ${state.error ? 'simple-listen-error' : ''}" role="${state.error ? 'alert' : 'status'}">${escape(state.error || state.message || '')}</p>
    </section>`;
    const summary = panel.container.closest?.('details')?.querySelector?.('#listening-summary');
    const summaryText = `Saved performance: ${record?.name || '…'}`;
    if (summary && summary.textContent !== summaryText) summary.textContent = summaryText;
    if (panel.container.innerHTML !== html) panel.container.innerHTML = html;
  }
  function paint(panel) {
    if (!panel?.state) return;
    if (performing(panel.state)) { paintPerformance(panel); return; }
    const state = panel.state;
    const status = panel.options.status || {};
    const breeze = status.providers?.find(provider => provider.id === 'breeze');
    const {breezeVoiceReady,available,voices,selectedVoice} = narratorChoices(state,status);
    const selected = selectedSegment(panel);
    const canUseCache = selected && state.takes.get(selected.id)?.source === sourceKey(selected);
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
    const chunkSettings = chunkCapable(state) ? `<label>First audio<select data-listen-field="chunk-preset" aria-label="How quickly the first chunk arrives">${CHUNK_PRESETS.map(item => `<option value="${item.id}" ${preset?.id === item.id ? 'selected' : ''}>${escape(item.label)}</option>`).join('')}${preset ? '' : `<option value="" selected>Custom · ${escape((chunking.ramp_seconds || []).join(', '))} s</option>`}</select></label><label>Full chunk length<select data-listen-field="chunk-length" aria-label="Audio per full-size request">${CHUNK_LENGTHS.map(([value,label]) => `<option value="${value}" ${Number(chunking.target_seconds) === value ? 'selected' : ''}>${escape(label)}</option>`).join('')}${CHUNK_LENGTHS.some(([value]) => value === Number(chunking.target_seconds)) || !chunking.target_seconds ? '' : `<option value="" selected>Custom · ${escape(span(chunking.target_seconds))}</option>`}</select></label>` : '';
    const chapterForSelection = chapterJob && chapterJob.chapter_id === selected?.chapter_id;
    const showProgress = state.mode === 'simple' && (state.intent || state.loading || playing || preparing || (chunked(state) && chapterForSelection));
    const chapterStatus = state.mode === 'simple' && chunked(state) ? chapterMarkup(state,panel) : '';
    const progressMarkup = chunked(state) ? (showProgress ? chapterStatus : '') : showProgress ? `<div class="simple-listen-buffer"><div><span role="status">${escape(progressText)}</span>${chapter ? '' : `<span>${buffered.readyPassages} passage${buffered.readyPassages === 1 ? '' : 's'}</span>`}</div><progress max="${maximum}" value="${progress}" aria-label="${chapter ? 'Chapter preparation' : 'Saved audio buffer'}"></progress></div>` : '';
    const drawer = panel.container.closest?.('details');
    const summary = drawer?.querySelector?.('#listening-summary');
    const narratorLabel = voices.find(voice => (voice.id ?? voice.name) === selectedVoice)?.name || selectedVoice || 'Default device voice';
    const providerLabel = PROVIDER_LABELS[state.provider] || state.provider;
    const sourceLabel = state.mode === 'simple' ? 'One narrator' : 'Studio voices selected · Narrator setup';
    const costLabel = state.provider === 'gemini' ? 'usage may incur charges' : state.provider === 'breeze' ? 'runs on your Breeze server' : 'free on this device';
    const selectionSummary = `${sourceLabel}: ${narratorLabel} / ${providerLabel} · ${costLabel}`;
    const providerNote = state.provider === 'gemini'
      ? 'Gemini receives the chapter text in large chunks paced to your request limits, and may incur charges, including examples. No narration spending cap is enforced.'
      : state.provider === 'breeze'
        ? 'Breeze narrates on your server over the local network, so passage text is sent there. There are no per-request charges. It generates at about real-time speed; prepare the chapter ahead for faster playback.'
        : 'Device narration stays on this computer and has no model charges.';
    const unavailableNote = state.provider === 'system' ? 'Device narration is unavailable here. Choose another provider to generate new audio.'
      : state.provider === 'breeze' ? (breeze?.available === true ? 'Choose a Breeze voice, or set a default voice in Voices, to generate new takes.'
        : breeze?.reason || status.breeze?.message || 'Connect your Breeze server in Settings to generate new takes.')
      : 'Add a Gemini API key in Settings to generate new takes.';
    const voiceOption = voice => {
      const id = voice.id ?? voice.name;
      const disabled = voice.usable === false && id !== selectedVoice;
      return `<option value="${escape(id)}" ${id === selectedVoice ? 'selected' : ''} ${disabled ? 'disabled' : ''}>${escape(voice.name || id)}${voice.locale ? ` · ${escape(voice.locale)}` : ''}${disabled && voice.reason ? ` · ${escape(voice.reason)}` : ''}</option>`;
    };
    if (summary) {
      const summaryText = `${state.error ? 'Preparation paused · ' : chapter ? `Preparing chapter: ${state.intent.completed} of ${state.intent.total} passages · ` : generating ? `Generating chapter: ${chapterJob.progress || 0} of ${chapterJob.total || '…'} passages · ` : ''}${selectionSummary}`;
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
        <label>Provider<select data-listen-field="provider" aria-label="Simple narration provider"><option value="system" ${state.provider === 'system' ? 'selected' : ''}>Device voices · free & local</option><option value="gemini" ${state.provider === 'gemini' ? 'selected' : ''}>Gemini · cloud</option><option value="breeze" ${state.provider === 'breeze' ? 'selected' : ''}>Breeze · local network</option></select></label>
        <div class="simple-listen-voice"><label>Narrator<select data-listen-field="voice" aria-label="Simple narrator voice">${voices.map(voiceOption).join('')}</select></label><button type="button" class="button subtle" data-listen-action="preview" title="${previewDescription}" ${panel.options.previewing || anyBusy || (state.provider === 'breeze' && !breezeVoiceReady) ? 'disabled' : ''}>Hear example</button></div>
      </div>
      <p class="simple-listen-note simple-listen-disclosure">${escape(providerNote)}</p>
      ${!available && !canUseCache ? `<p class="simple-listen-unavailable">${escape(unavailableNote)}</p>` : ''}
      <div class="simple-listen-actions">
        <button type="button" class="button primary" data-listen-action="start" ${startDisabled ? 'disabled' : ''} aria-label="${preparing ? 'Stop preparing narration' : playing ? 'Pause simple listening' : state.mode === 'simple' ? 'Play simple listening' : 'Start simple listening'}">${startLabel}</button>
        ${state.mode === 'simple' || state.loading ? '<button type="button" class="button subtle" data-listen-action="stop">Stop</button>' : ''}
        ${chunked(state) && generating ? '<button type="button" class="button subtle" data-listen-action="stop-generating">Stop generating</button>' : chunked(state) && resumable ? `<button type="button" class="button subtle" data-listen-action="prepare-chapter" ${busy || elsewhere || !available ? 'disabled' : ''}>Resume chapter</button>` : ''}
        <span>${state.mode === 'simple' ? state.continuous ? 'Continues into the next chapter' : 'Stops at chapter end' : 'No story analysis needed'}</span>
      </div>
      ${progressMarkup}
      <p class="simple-listen-message ${state.error || state.chapter.error ? 'simple-listen-error' : ''}" role="${state.error || state.chapter.error ? 'alert' : 'status'}">${escape(state.error || state.chapter.error || (chunked(state) && chapterForSelection && ['failed','quota_limited'].includes(chapterJob.status) ? chapterJob.error || chapterJob.message : '') || state.message || '')}</p>
      ${state.error ? '<button type="button" class="button subtle simple-listen-retry" data-listen-action="retry">Retry preparation</button>' : ''}
      <details class="simple-listen-options" data-listen-options ${panel.optionsOpen ? 'open' : ''}>
        <summary data-listen-summary>More listening options</summary>
        <div class="simple-listen-advanced">
          <div class="simple-listen-settings">
            <label>Playback speed<select data-listen-speed aria-label="Simple listening playback speed">${playbackRates.map(rate => `<option value="${rate}" ${rate === rateOf(panel.options.playbackRate) ? 'selected' : ''}>${rate}×</option>`).join('')}</select></label>
            ${state.provider === 'gemini' ? `<label>Speech model<select data-listen-field="model" aria-label="Simple speech model">${models.map(model => `<option value="${escape(model)}" ${model === state.model ? 'selected' : ''}>${escape(model)}</option>`).join('')}</select></label>` : ''}
            ${chunkSettings}
          </div>
          <label class="simple-listen-continuous"><input type="checkbox" data-listen-field="continuous" ${state.continuous ? 'checked' : ''}> Continue into the next chapter</label>
          <p class="simple-listen-note">${state.continuous ? `Simple playback keeps going through the book until you pause or stop, a request limit is reached, or the book ends.${chunked(state) ? ' While you listen, the next chapter is queued about 10 minutes ahead of you.' : ''}` : 'Simple playback stops at the end of this chapter.'} Highlighting follows each passage. Playback and speed are shared with the player below.</p>
          ${chunked(state) ? `<div class="simple-listen-preparation">
            <div><h4>Prepare before listening</h4><p>${generating ? 'This chapter is generating. Stop generating lets requests already sent finish; their audio stays saved.' : elsewhere ? 'Another chapter is being generated for this book. Stop it there, or wait, before queueing this one.' : `Queue ${remainingCount} passages from here to the chapter end in large chunks, without starting playback. Each chunk is one request.`}</p></div>
            ${generating ? '' : `<button type="button" class="button subtle" data-listen-action="prepare-chapter" ${busy || elsewhere || !remainingCount || !available ? 'disabled' : ''}>${resumable ? 'Resume chapter' : 'Queue chapter'}</button>`}
          </div>
          ${showProgress ? '' : chapterStatus}
          <p class="simple-listen-note">Play starts or joins the chapter queue: quick-start chunks first, then full chunks of up to about 7 minutes, paced to your request limits. Only Play, Queue chapter and Resume chapter send requests; Stop pauses playback while generation continues.</p>` : `<div class="simple-listen-preparation">
            <div><h4>Prepare before listening</h4><p>Save up to ${remainingCount} passages from here to the end of the chapter, without starting playback. Saved matching audio is reused.</p></div>
            <button type="button" class="button subtle" data-listen-action="prepare-chapter" ${warming || chapter || busy || !remainingCount || !available && !canUseCache ? 'disabled' : ''}>Prepare rest of chapter</button>
          </div>
          <p class="simple-listen-note">Playback starts after a short warmup of up to 3 passages, then prepares about 45 listening seconds ahead, with at most 12 future passages. Pause or Stop prevents new requests after the current one finishes.</p>`}
          ${state.mode === 'simple' && !showProgress && !chunked(state) ? `<p class="simple-listen-note">${escape(progressText)}</p>` : ''}
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
        if (field === 'continuous') { setContinuous(panel.state.book,event.target.checked); return; }
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
          // A performance plays through the same toggle; it must never switch to live narration here.
          if (enabled(panel.state.book) && panel.options.onToggle) { panel.options.onToggle(); return; }
          if (performing(panel.state)) return;
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
        chapter:{job:null,preview:null,previewKey:null,watching:false,waiters:[],takesMark:null,error:'',discovered:null,missing:0,
          autoJobId:null,autoStarting:null,pauseCancelled:null,retryAt:0,hold:false},
        mode:prior.mode === 'simple' || (prior.mode === 'performance' && prior.performanceId) ? prior.mode : 'enhanced',
        performanceId:prior.mode === 'performance' ? prior.performanceId : null,performance:null,provider:PROVIDERS.includes(prior.provider) ? prior.provider : systemAvailable ? 'system' : 'gemini',
        voices:{system:typeof prior.voices?.system === 'string' ? prior.voices.system : '',gemini:prior.voices?.gemini || 'Kore',
          breeze:typeof prior.voices?.breeze === 'string' ? prior.voices.breeze : ''},status,library:options.voiceLibrary || null,
        model:prior.model || status.tts_model || 'gemini-3.8-flash-tts',sessionId:prior.sessionId || null,sessionKey:prior.sessionKey || null,
        continuous:prior.continuous !== false};
      books.set(book.id,state);
    }
    // Playback running into the next chapter keeps its intent and queue; any
    // other chapter change (or one during warmup) invalidates it.
    const followsPlayback = state.intent?.type === 'play' && state.continuous && state.intent.phase === 'playing' &&
      options.chapterId === nextChapterId(state,state.intent.segmentId);
    if (state.intent && ((!followsPlayback && panel.options.chapterId !== options.chapterId) ||
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
    if (options.status) state.status = options.status;
    if (options.voiceLibrary) state.library = options.voiceLibrary;
    if (adoptLibraryChoice(state)) save(state);
    for (const [id,item] of state.takes) {
      const segment = book.segments.find(segment => segment.id === id);
      if (!segment || sourceKey(segment) !== item.source) state.takes.delete(id);
    }
    paint(panel);
    if (chunked(state) && state.chapter.job && !CHAPTER_TERMINAL.has(state.chapter.job.status)) watchChapter(state);
    if (performing(state) && !state.performance && !state.performanceLoading) {
      state.performanceLoading = loadPerformance(state,state.performanceId).catch(error => {
        // A performance that is gone or archived falls back to one-narrator listening.
        if (error.status === 404) { state.mode = 'simple'; state.performanceId = null; save(state); }
        state.error = error.status === 404 ? '' : `The saved performance could not be loaded: ${error.message}`;
        paint(state.panel); notify(state);
      }).finally(() => { state.performanceLoading = null; });
    }
    return loadSaved(state).then(() => {
      if (!chunked(state)) return;
      void discoverChapter(state);
      void loadPreview(state,panel);
    });
  }
  function allowsAdvance(book, currentSegment, nextSegment) {
    const state = stateFor(book);
    if (!enabled(book)) return true;
    if (!currentSegment || !nextSegment || !inPerformance(state,nextSegment)) return false;
    return currentSegment.chapter_id === nextSegment.chapter_id ||
      Boolean(state.continuous && (performing(state) || continuesInto(state,currentSegment,nextSegment)));
  }
  const isContinuous = book => Boolean(stateFor(book)?.continuous);
  function setContinuous(book, value) {
    const state = stateFor(book);
    if (!state) return;
    state.continuous = Boolean(value);
    // Takes effect at the next lookahead; the once-per-chapter guard stays.
    save(state);
    paint(state.panel);
  }
  window.BardicListen = {render,enabled,isSimple:enabled,take:resolve,resolve,ensure,prepare,updatePlayback,prepareChapter,getBuffer,getSelection,forgetAudio,stop,waitForStopped,allowsAdvance,
    chapterMarks,estimateChapter,getChapterJob:book => stateFor(book)?.chapter.job || null,choices,choose,isContinuous,setContinuous,
    nextSegment,usePerformance,leavePerformance,getPerformance,refreshPerformance,narratorOptions};
})();
