/* Explicit voice auditions. Audio playback belongs to the shared reader player;
   this controller only requests one sample and protects it from stale results. */
(() => {
  'use strict';
  const encode = value => encodeURIComponent(value);
  const wait = () => new Promise(resolve => setTimeout(resolve, 400));
  const busy = job => ['queued', 'running'].includes(job?.status);
  const playable = audio => Boolean(audio?.url && audio.available !== false && !audio.stale && !audio.is_stale);
  const idle = () => ({status:'idle', label:'', preview:null, error:'', jobId:null, bookId:null});
  let hooks = {}, state = idle(), active = null, knownJob = null;
  let serial = Promise.resolve();

  function getState() {
    return {...state, preview:state.preview ? {...state.preview} : null};
  }
  function publish(changes) {
    state = {...state, ...changes};
    hooks.onState?.(getState());
  }
  function configure(callbacks = {}) { hooks = {...callbacks}; }
  const current = task => active === task;

  async function request(url, body) {
    const response = await fetch(url, body === undefined ? {headers:{Accept:'application/json'}} :
      {method:'POST', headers:{Accept:'application/json', 'Content-Type':'application/json'}, body:JSON.stringify(body)});
    let data;
    try { data = await response.json(); } catch { data = null; }
    if (!response.ok) {
      const error = new Error(typeof data?.detail === 'string' ? data.detail : `Voice example request failed (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return data;
  }
  function cancelKnown() {
    if (!knownJob || knownJob.cancelRequested) return;
    knownJob.cancelRequested = true;
    void request(`/api/jobs/${encode(knownJob.id)}/cancel`, {}).catch(() => {});
  }
  function stop() {
    cancelKnown();
    if (!active && state.status === 'idle') return;
    active = null;
    state = idle();
    hooks.onState?.(getState());
    hooks.onStop?.();
  }

  async function observeJob(task, known, settling = false, isCurrent = () => current(task)) {
    task.operation = 'poll';
    task.observedJob = known;
    let failures = 0, missing = 0;
    for (let attempt = 0; attempt < 450; attempt++) {
      if (!isCurrent()) return null;
      await wait();
      if (!isCurrent()) return null;
      let jobs;
      try {
        jobs = await request(`/api/jobs?book_id=${encode(known.bookId)}`);
        if (!Array.isArray(jobs)) throw new Error('The voice example’s status could not be read.');
        failures = 0;
      } catch (error) {
        if (!isCurrent()) return null;
        // GET status checks are safe to repeat. An uncertain synthesis POST is
        // never repeated automatically, because it may already have been paid.
        if ((!error.status || error.status === 429 || error.status >= 500) && ++failures <= 2) continue;
        throw error;
      }
      if (!isCurrent()) return null;
      const job = jobs.find(item => item.id === known.id);
      if (!job) {
        if (++missing >= 3) throw new Error('The previous voice example could not be found. Try again to check its status before requesting another.');
        continue;
      }
      missing = 0;
      if (!busy(job)) {
        if (knownJob === known) knownJob = null;
        return job;
      }
      if (!settling) publish({jobId:job.id, preview:job.preview || state.preview});
    }
    throw new Error('The voice example is still being made. Try again later to check its status before requesting another.');
  }

  function waitForStopped() {
    stop();
    const expected = active;
    const barrier = serial.catch(() => {}).then(async () => {
      if (active !== expected) return false;
      if (knownJob) await observeJob({}, knownJob, true, () => active === expected);
      return active === expected && !knownJob;
    });
    // Later auditions also wait for this read-only barrier, so the normal reader
    // and example controller cannot race their cooperative cancellation checks.
    serial = barrier.catch(() => {});
    return barrier;
  }

  async function generate(task) {
    if (!current(task)) return null;
    // A cancelled request may still be running on the provider. Keep a single
    // serialized barrier until it is terminal, even when the book changes.
    if (knownJob) {
      await observeJob(task, knownJob, true);
      if (!current(task)) return null;
    }
    task.operation = 'poll';
    task.observedJob = null;
    const prepared = await hooks.beforeRequest?.(task.book);
    if (!current(task)) return null;
    if (prepared === false) throw new Error('The previous narration has not stopped. Try the example again after it finishes.');
    task.operation = 'request';
    task.observedJob = null;
    const result = await request(`/api/books/${encode(task.bookId)}/voice-preview`, task.body);
    if (result?.job?.id && busy(result.job)) {
      knownJob = {id:result.job.id, bookId:task.bookId, cancelRequested:false};
    }
    if (!current(task)) { cancelKnown(); return null; }
    let preview = result?.preview || null, audio = result?.audio;
    publish({preview, jobId:result?.job?.id || null});
    if (!audio && result?.job?.id) {
      const initial = result.job;
      const job = busy(initial) ? await observeJob(task, knownJob) : initial;
      if (!current(task)) return null;
      if (!job || job.status !== 'completed') {
        throw new Error(job?.error || job?.message || 'The voice example stopped before it was ready.');
      }
      audio = job.audio;
      preview = job.preview || preview;
    }
    if (!current(task)) return null;
    if (!playable(audio) || !preview) throw new Error('The voice example finished without playable audio.');
    publish({status:'ready', preview, error:''});
    if (!current(task)) return null;
    await hooks.onReady?.(audio, preview);
    return current(task) ? {audio, preview, cached:result.cached === true} : null;
  }

  function start(book, config = {}, label = '') {
    const body = {};
    for (const name of ['provider', 'voice', 'model', 'segment_id', 'character_id', 'direction', 'segment_direction']) {
      if (typeof config[name] === 'string') body[name] = config[name];
    }
    // An unsaved pronunciation to hear in place of the saved one; only its own fields are sent.
    const draft = config.pronunciation;
    if (draft && typeof draft === 'object') {
      body.pronunciation = {};
      for (const name of ['id', 'term', 'respelling', 'match_case', 'providers']) {
        if (draft[name] !== undefined && draft[name] !== null && draft[name] !== '') body.pronunciation[name] = draft[name];
      }
    }
    const bookId = book?.id;
    const signature = JSON.stringify([bookId, book?.revision, body]);
    if (active?.signature === signature && state.status === 'loading') return active.promise;
    stop();
    // The shared player's stop path may call stop() here. Establish the new
    // active request only after that callback has completed.
    hooks.onStart?.();
    const task = {book, bookId, body, signature, promise:null};
    active = task;
    publish({status:'loading', label:label || config.voice || 'Voice example', bookId:bookId || null,
      preview:null, error:'', jobId:null});
    task.promise = serial.catch(() => {}).then(async () => {
      if (!current(task)) return null;
      try {
        if (!bookId) throw new Error('Open a book before hearing a voice example.');
        return await generate(task);
      } catch (error) {
        if (current(task)) {
          try {
            const bookId = task.observedJob?.bookId || task.bookId;
            window.BardicDiagnostics?.record('preview_failed', {book_id:bookId,
              segment_id:bookId === task.bookId ? task.body.segment_id : undefined,
              job_id:task.observedJob?.id || state.jobId, operation:task.operation || 'request', http_status:error.status});
          } catch { /* Optional operational logging never changes playback. */ }
          publish({status:'error', error:error.message || 'The voice example could not be made.'});
        }
        return null;
      }
    });
    serial = task.promise;
    return task.promise;
  }

  window.BardicVoicePreview = {configure, start, stop, waitForStopped, getState};
})();
