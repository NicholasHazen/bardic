const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const book = {id:'book-1', revision:1, characters:[{id:'mara', voice:'Kore'}], segments:[{id:'segment-1', text:'Mara opened the window.'}]};
const config = {provider:'gemini', voice:'Leda', model:'tts-model', segment_id:'segment-1', character_id:'mara', direction:'Warm.', segment_direction:'Quietly.'};
const preview = {id:'preview-1', source:'passage', text:'Mara opened the window.', segment_id:'segment-1', character_id:'mara', voice:'Leda'};
const audio = {url:'/api/books/book-1/voice-preview/audio/asset', asset_id:'asset', duration:2};
const tick = () => new Promise(resolve => setImmediate(resolve));
async function until(predicate) { for (let i=0;i<50;i++) { if (predicate()) return; await tick(); } assert.fail('Expected asynchronous step did not occur.'); }
function deferred() { let resolve, reject; const promise = new Promise((yes,no) => {resolve=yes; reject=no;}); return {promise, resolve, reject}; }
function environment(handler) {
  const calls = [], ready = [], states = [], lifecycle = [], diagnostics = [];
  const scope = {window:{BardicDiagnostics:{record:(event, details) => diagnostics.push({event, ...details})}}, setTimeout:fn => setImmediate(fn), fetch:async (url, options = {}) => {
    const call = {url, method:options.method || 'GET', body:options.body ? JSON.parse(options.body) : null};
    calls.push(call);
    const response = await handler(call, calls);
    return {ok:response.ok !== false, status:response.status || 200, json:async () => response.data};
  }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../bardic/static/voice-preview.js'), 'utf8'), scope);
  const api = scope.window.BardicVoicePreview;
  api.configure({onStart:() => lifecycle.push('start'), onStop:() => lifecycle.push('stop'),
    onState:state => {states.push(state); lifecycle.push(state.status);}, onReady:(...args) => ready.push(args)});
  return {api, calls, ready, states, lifecycle, diagnostics, setLogger:logger => {scope.window.BardicDiagnostics = logger;}};
}
const cached = () => ({data:{preview, audio, cached:true}});
const postCount = env => env.calls.filter(call => call.method === 'POST' && call.url.endsWith('/voice-preview')).length;

test('only explicit audition requests a sample, preserves inputs and uses the shared-player callbacks', async () => {
  const env = environment(cached), before = JSON.stringify(book);
  assert.equal(env.api.getState().status, 'idle');
  assert.equal(env.calls.length, 0);
  const result = await env.api.start(book, {...config, unused:'do not send'}, 'Mara · Leda');
  assert.deepEqual(env.calls[0], {url:'/api/books/book-1/voice-preview', method:'POST', body:config});
  assert.equal(env.lifecycle[0], 'start');
  assert.equal(env.lifecycle[1], 'loading');
  assert.equal(result.audio, audio);
  assert.equal(result.cached, true);
  assert.deepEqual(env.ready, [[audio, preview]]);
  assert.equal(env.api.getState().status, 'ready');
  assert.equal(env.api.getState().label, 'Mara · Leda');
  assert.equal(JSON.stringify(book), before, 'Auditioning never assigns the voice or modifies source.');
  assert.equal(postCount(env), 1);
});

test('a pronunciation draft is sent with only its own fields', async () => {
  const env = environment(cached);
  const draft = {id:'pr_0123456789ab', term:'Cthaelor', respelling:'Kaylor', match_case:false, providers:{breeze:'Kay-lor'},
    usage:{occurrences:3}, note:''};
  await env.api.start(book, {provider:'breeze', voice:'narrator', pronunciation:draft}, 'Cthaelor → Kaylor');
  assert.deepEqual(env.calls[0].body, {provider:'breeze', voice:'narrator', pronunciation:{id:'pr_0123456789ab',
    term:'Cthaelor', respelling:'Kaylor', match_case:false, providers:{breeze:'Kay-lor'}}});
});

test('main-player onStart may call stop without invalidating the new audition', async () => {
  const env = environment(cached);
  let stopped = 0, played = 0;
  env.api.configure({onStart:() => env.api.stop(), onStop:() => {stopped++; env.api.stop();}, onReady:() => played++});
  await env.api.start(book, config);
  assert.equal(played, 1);
  env.api.stop();
  env.api.stop();
  assert.equal(stopped, 1, 'Stop remains idempotent even through parent callbacks.');
  assert.equal(env.api.getState().status, 'idle');
});

test('a previous reader request must settle before the audition POST is sent', async () => {
  const previous = deferred(), env = environment(cached);
  let stopped = false, waited = false, played = 0;
  env.api.configure({onStart:() => {stopped = true;}, beforeRequest:async given => {
    assert.equal(stopped, true, 'The reader is stopped before awaiting its cancellation barrier.');
    assert.equal(given, book);
    waited = true;
    return previous.promise;
  }, onReady:() => played++});
  const pending = env.api.start(book, config);
  await until(() => waited);
  assert.equal(postCount(env), 0);
  previous.resolve(true);
  await pending;
  assert.equal(postCount(env), 1);
  assert.equal(played, 1);
});

test('stopping while the reader barrier is pending prevents synthesis after it settles', async () => {
  const previous = deferred(), env = environment(cached);
  let waited = false;
  env.api.configure({beforeRequest:() => {waited = true; return previous.promise;}});
  const pending = env.api.start(book, config);
  await until(() => waited);
  env.api.stop();
  previous.resolve(true);
  assert.equal(await pending, null);
  assert.equal(postCount(env), 0);
  assert.equal(env.api.getState().status, 'idle');
});

test('identical pending audition clicks join a single request', async () => {
  const response = deferred(), env = environment(() => response.promise);
  const first = env.api.start(book, config), second = env.api.start(book, config);
  assert.equal(first, second);
  await until(() => postCount(env) === 1);
  response.resolve(cached());
  await first;
  assert.equal(postCount(env), 1);
  assert.equal(env.ready.length, 1);
});

test('queued generation polls once and uses completed job metadata', async () => {
  const completedPreview = {...preview, truncated:true};
  const env = environment(call => call.method === 'POST' ? {data:{preview, job:{id:'job-1', kind:'voice_preview', status:'queued'}, cached:false}} :
    {data:[{id:'job-1', status:'completed', audio, preview:completedPreview}]});
  const result = await env.api.start(book, config);
  assert.equal(env.calls[1].url, '/api/jobs?book_id=book-1');
  assert.equal(result.preview, completedPreview);
  assert.equal(result.cached, false);
  assert.equal(env.api.getState().jobId, 'job-1');
  assert.equal(env.ready.length, 1);
});

test('Stop drops late cached responses without playback', async () => {
  const response = deferred(), env = environment(() => response.promise);
  const pending = env.api.start(book, config);
  await until(() => env.calls.length === 1);
  env.api.stop();
  response.resolve(cached());
  assert.equal(await pending, null);
  assert.equal(env.ready.length, 0);
  assert.equal(env.api.getState().status, 'idle');
});

test('a late accepted job is cancelled and a new audition waits for that job to settle', async () => {
  const response = deferred(), oldStatus = deferred();
  let reads = 0;
  const env = environment(call => {
    if (call.url.endsWith('/cancel')) return {data:{status:'running'}};
    if (call.method === 'GET') return ++reads === 1 ? {data:[{id:'old-job', status:'running'}]} : oldStatus.promise;
    if (call.body.voice === 'Leda') return response.promise;
    return cached();
  });
  const first = env.api.start(book, config);
  await until(() => postCount(env) === 1);
  const next = env.api.start({...book, id:'book-2'}, {...config, voice:'Puck'});
  response.resolve({data:{preview, job:{id:'old-job', status:'queued'}}});
  await until(() => reads === 2);
  assert.equal(await first, null);
  assert.equal(postCount(env), 1, 'No new synthesis may race the cancelled provider job.');
  assert.ok(env.calls.some(call => call.url === '/api/jobs/old-job/cancel'));
  assert.ok(env.calls.some(call => call.url === '/api/jobs?book_id=book-1'), 'Settle checks use the previous book identity.');
  oldStatus.resolve({data:[{id:'old-job', status:'cancelled'}]});
  await next;
  assert.equal(postCount(env), 2);
  assert.equal(env.calls.at(-1).url, '/api/books/book-2/voice-preview');
  assert.equal(env.ready.length, 1);
});

test('Stop during a status request cannot play the late successful job', async () => {
  const status = deferred();
  const env = environment(call => {
    if (call.url.endsWith('/cancel')) return {data:{status:'cancelled'}};
    if (call.method === 'GET') return status.promise;
    return {data:{preview, job:{id:'job-1', status:'queued'}}};
  });
  const pending = env.api.start(book, config);
  await until(() => env.calls.some(call => call.method === 'GET'));
  env.api.stop(); env.api.stop();
  status.resolve({data:[{id:'job-1', status:'completed', audio, preview}]});
  assert.equal(await pending, null);
  assert.equal(env.ready.length, 0);
  assert.equal(env.calls.filter(call => call.url.endsWith('/cancel')).length, 1);
});

test('the reader can await a cancelled preview including its late accepted POST', async () => {
  const response = deferred(), status = deferred();
  const env = environment(call => {
    if (call.url.endsWith('/cancel')) return {data:{status:'running'}};
    if (call.method === 'GET') return status.promise;
    return response.promise;
  });
  const pending = env.api.start(book, config);
  await until(() => postCount(env) === 1);
  const stopped = env.api.waitForStopped();
  response.resolve({data:{preview, job:{id:'job-1', status:'queued'}}});
  await until(() => env.calls.some(call => call.method === 'GET'));
  assert.equal(await pending, null);
  assert.equal(env.api.getState().status, 'idle');
  assert.equal(env.ready.length, 0);
  status.resolve({data:[{id:'job-1', status:'cancelled'}]});
  assert.equal(await stopped, true);
  assert.equal(postCount(env), 1);
});

test('a new audition supersedes the reader cancellation barrier without overlapping POSTs', async () => {
  const response = deferred(), status = deferred();
  let reads = 0;
  const env = environment(call => {
    if (call.url.endsWith('/cancel')) return {data:{status:'running'}};
    if (call.method === 'GET') return ++reads === 1 ? status.promise : {data:[{id:'job-1', status:'cancelled'}]};
    return postCount(env) === 1 ? response.promise : cached();
  });
  const first = env.api.start(book, config);
  await until(() => postCount(env) === 1);
  const stopped = env.api.waitForStopped();
  response.resolve({data:{preview, job:{id:'job-1', status:'queued'}}});
  await until(() => reads === 1);
  const next = env.api.start(book, {...config, voice:'Puck'});
  status.resolve({data:[{id:'job-1', status:'running'}]});
  assert.equal(await first, null);
  assert.equal(await stopped, false, 'The main reader must not resume after a newer audition starts.');
  await next;
  assert.equal(postCount(env), 2);
  assert.equal(reads, 2, 'The newer audition still waits for the old provider request to finish.');
  assert.equal(env.ready.length, 1);
});

test('at most two transient read retries recover without repeating synthesis', async () => {
  let reads = 0;
  const env = environment(call => {
    if (call.method === 'POST') return {data:{preview, job:{id:'job-1', status:'running'}}};
    if (++reads < 3) return {ok:false, status:503, data:{detail:'Temporarily unavailable.'}};
    return {data:[{id:'job-1', status:'completed', audio, preview}]};
  });
  await env.api.start(book, config);
  assert.equal(reads, 3);
  assert.equal(env.ready.length, 1);
  assert.equal(postCount(env), 1);
});

test('exhausted status retries preserve the job barrier for the next explicit audition', async () => {
  let reads = 0;
  const env = environment(call => {
    if (call.url.endsWith('/cancel')) return {data:{status:'running'}};
    if (call.method === 'POST') return postCount(env) === 1 ? {data:{preview, job:{id:'job-1', status:'running'}}} : cached();
    if (++reads <= 3) throw new Error('Network unavailable.');
    return {data:[{id:'job-1', status:'completed', audio, preview}]};
  });
  await env.api.start(book, config);
  assert.equal(reads, 3);
  assert.equal(env.api.getState().status, 'error');
  assert.equal(env.api.getState().jobId, 'job-1');
  assert.equal(postCount(env), 1);
  await env.api.start(book, {...config, voice:'Puck'});
  assert.equal(reads, 4, 'Explicit retry observes the original job before new synthesis.');
  assert.equal(postCount(env), 2);
  assert.equal(env.ready.length, 1);
});

test('an uncertain POST failure is surfaced without retry or provider fallback', async () => {
  const env = environment(() => {throw new Error('Network unavailable.');});
  assert.equal(await env.api.start(book, config), null);
  assert.equal(postCount(env), 1);
  assert.equal(env.api.getState().error, 'Network unavailable.');
  assert.equal(env.api.getState().status, 'error');
  assert.equal(env.ready.length, 0);
  assert.equal(env.diagnostics.length, 1);
  assert.equal(env.diagnostics[0].event, 'preview_failed');
  assert.equal(env.diagnostics[0].book_id, 'book-1');
  assert.equal(env.diagnostics[0].segment_id, 'segment-1');
  assert.equal(env.diagnostics[0].operation, 'request');
  assert.ok(!JSON.stringify(env.diagnostics).includes('Network unavailable'));
});

test('poll failures log operational identifiers only and a broken logger cannot reject the audition', async () => {
  const env = environment(call => call.method === 'POST' ? {data:{preview, job:{id:'job-1', status:'running'}}} :
    {ok:false, status:403, data:{detail:'Sensitive provider response.'}});
  await env.api.start(book, config);
  assert.equal(env.diagnostics.length, 1);
  assert.equal(env.diagnostics[0].operation, 'poll');
  assert.equal(env.diagnostics[0].job_id, 'job-1');
  assert.equal(env.diagnostics[0].http_status, 403);
  assert.ok(!JSON.stringify(env.diagnostics).includes('Sensitive'));
  const broken = environment(() => ({ok:false, status:503, data:{detail:'Unavailable.'}}));
  broken.setLogger({record:() => {throw new Error('Logging unavailable.');}});
  assert.equal(await broken.api.start(book, config), null);
  assert.equal(broken.api.getState().error, 'Unavailable.');
});

test('failed jobs never trigger a second sample', async () => {
  for (const outcome of [
    {status:'failed', error:'Quota exhausted.'},
  ]) {
    const env = environment(call => call.method === 'POST' ? {data:{preview, job:{id:'job-1', status:'queued'}}} :
      {data:[{id:'job-1', ...outcome}]});
    await env.api.start(book, config);
    assert.equal(env.api.getState().status, 'error');
    assert.equal(env.ready.length, 0);
    assert.equal(postCount(env), 1);
  }
});

test('generic examples omit source IDs and use server-selected demo text', async () => {
  const demo = {...preview, source:'demo', text:'A short original demo.', segment_id:null, character_id:null};
  const env = environment(() => ({data:{preview:demo, audio, cached:true}}));
  await env.api.start(book, {provider:'system', voice:'Samantha'});
  assert.deepEqual(env.calls[0].body, {provider:'system', voice:'Samantha'});
  assert.equal(env.ready[0][1].source, 'demo');
  assert.equal(env.api.getState().preview.text, 'A short original demo.');
});

test('superseded queued intent makes no request and cannot overwrite newer state', async () => {
  const response = deferred(), env = environment(call => call.body.voice === 'Leda' ? response.promise : cached());
  const first = env.api.start(book, config);
  await until(() => postCount(env) === 1);
  const second = env.api.start(book, {...config, voice:'Puck'});
  const third = env.api.start(book, {...config, voice:'Kore'}, 'Newest');
  response.resolve(cached());
  assert.equal(await first, null); assert.equal(await second, null);
  await third;
  assert.equal(postCount(env), 2);
  assert.equal(env.calls[1].body.voice, 'Kore');
  assert.equal(env.api.getState().label, 'Newest');
  assert.equal(env.ready.length, 1);
});
