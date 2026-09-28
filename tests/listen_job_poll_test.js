/* Exercise the app's actual job callback/scheduler without a browser or provider. */
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname,'../bardic/static/app.js'),'utf8');
const start = source.indexOf('async function pollJobs(');
const end = source.indexOf('async function startJob(',start);
const callbackStart = source.indexOf('function trackJob(');
const callbackEnd = source.indexOf('function previewNarrator(',callbackStart);
assert.ok(start >= 0 && end > start && callbackStart >= 0 && callbackEnd > callbackStart);
const pollSource = source.slice(start,end);
const callbackSource = source.slice(callbackStart,callbackEnd);

const job = (id,status,kind='listen',time=1) => ({id,book_id:'book-a',status,kind,
  created_at:`2026-09-27T00:00:${id === 'second' ? '02' : '01'}Z`,updated_at:`2026-09-27T00:01:${String(time).padStart(2,'0')}Z`});
function environment(handler) {
  const state = {book:{id:'book-a'},jobs:[],poll:null,selectionVersion:1,referenceCache:new Map(),referenceVersion:0};
  const calls = [], timers = new Map(), rendered = {jobs:0,reader:0,player:0,books:[],library:0,toasts:[]};
  let nextTimer = 1;
  const context = {state,Promise,encodeURIComponent,
    busyJob:() => state.jobs.find(item => ['queued','running'].includes(item.status)),
    request:async url => {calls.push(url);return handler(url,state);},
    setTimeout:(fn,delay) => {const id=nextTimer++;timers.set(id,{fn,delay});return id;},
    clearTimeout:id => timers.delete(id),
    renderJob:() => rendered.jobs++,renderReader:() => rendered.reader++,updatePlayer:() => rendered.player++,
    applyBook:book => rendered.books.push(book),
    refreshLibrary:async() => rendered.library++, $$:() => [],loadCharacterReferences:() => {},
    toast:message => rendered.toasts.push(message),
  };
  vm.runInNewContext(pollSource + `\n${callbackSource}\nglobalThis.api={pollJobs,onJob:trackJob};`,context);
  async function runNext() {
    const entry = timers.entries().next().value;
    assert.ok(entry,'Expected one scheduled poll');
    timers.delete(entry[0]);
    await entry[1].fn();
  }
  return {state,calls,timers,rendered,api:context.api,runNext};
}

test('stopped simple listening eventually settles the banner with jobs-only polling',async()=>{
  let checks=0;
  const env=environment(url => {
    assert.equal(url,'/api/jobs?book_id=book-a');
    return [job('first',++checks === 1 ? 'running' : 'cancelled','listen',checks+5)];
  });
  env.api.onJob(job('first','queued'));
  const timer=env.state.poll;
  env.api.onJob(job('first','running','listen',2));
  env.api.onJob(job('first','running','listen',3));
  assert.equal(env.state.poll,timer,'400ms progress callbacks must not postpone the fallback poll');
  assert.equal(env.timers.size,1);
  // Stop invalidates the component's intent, so it emits no further callbacks.
  await env.runNext();
  assert.equal(env.state.jobs[0].status,'running');
  assert.equal(env.timers.size,1);
  await env.runNext();
  assert.equal(env.state.jobs[0].status,'cancelled');
  assert.equal(env.timers.size,0);
  assert.equal(env.state.poll,null);
  assert.equal(env.state.jobPollToken,null);
  assert.equal(env.rendered.reader,1,'Settling jobs refreshes disabled listening controls');
  assert.equal(env.rendered.books.length,0);
  assert.equal(env.rendered.library,0);
});

test('a completed chapter callback still clears an older cancelled job that looks queued',async()=>{
  const env=environment(() => [job('second','completed','listen',8),job('first','cancelled','listen',7)]);
  env.state.jobs=[job('first','queued')];
  env.api.onJob(job('second','completed','listen',8));
  assert.equal(env.timers.size,1,'Any stale active job needs one status check');
  await env.runNext();
  assert.equal(env.state.jobs.some(item => ['queued','running'].includes(item.status)),false);
  assert.equal(env.rendered.reader,1);
  assert.equal(env.rendered.books.length,0);
});

test('pipeline completion refreshes the source projection and library, with no Classic progress request',async()=>{
  const env=environment(url => {
    if (url.startsWith('/api/jobs')) return [job('first','completed','pipeline',3)];
    assert.equal(url,'/api/books/book-a');
    return {id:'book-a',revision:2};
  });
  env.state.jobs=[job('first','running','pipeline')];
  await env.api.pollJobs(true);
  assert.deepEqual(env.calls,['/api/jobs?book_id=book-a','/api/books/book-a']);
  assert.equal(env.rendered.books[0].revision,2);
  assert.equal(env.rendered.library,1);
  assert.equal(env.timers.size,0);
});

test('initial general polling switches to jobs-only while simple narration remains active',async()=>{
  let checks=0;
  const env=environment(() => [job('first',++checks === 1 ? 'running' : 'completed','listen',checks)]);
  await env.api.pollJobs(false);
  await env.runNext();
  assert.deepEqual(env.calls,['/api/jobs?book_id=book-a','/api/jobs?book_id=book-a']);
  assert.equal(env.rendered.books.length,0);
});

test('a response started before switching books cannot overwrite the selected book jobs',async()=>{
  let release;
  const env=environment(url => url.includes('book-a') ? new Promise(resolve => {release=resolve;}) : []);
  env.state.jobs=[job('first','running')];
  const old=env.api.pollJobs(false,{jobsOnly:true});
  env.state.book={id:'book-b'};
  env.state.selectionVersion++;
  env.state.jobs=[];
  await env.api.pollJobs(false,{jobsOnly:true});
  release([job('first','cancelled','listen',5)]);
  await old;
  assert.equal(env.state.jobs.length,0);
  assert.equal(env.state.jobPollToken,null);
  assert.equal(env.timers.size,0);
});

test('a next-passage callback during an older GET remains tracked until it settles',async()=>{
  let release;
  const env=environment(() => new Promise(resolve => {release=resolve;}));
  env.state.jobs=[job('first','running')];
  const pending=env.api.pollJobs(false,{jobsOnly:true});
  env.api.onJob(job('second','queued','listen',4));
  assert.equal(env.timers.size,0,'An in-flight status request already owns scheduling');
  release([job('first','completed','listen',3)]);
  await pending;
  assert.equal(env.state.jobs.find(item => item.id === 'second').status,'queued');
  assert.equal(env.timers.size,1,'The newer request keeps the lightweight poll alive');
});

test('newer callback status is not regressed by an older GET snapshot',async()=>{
  let release;
  const env=environment(() => new Promise(resolve => {release=resolve;}));
  env.state.jobs=[job('first','queued')];
  const pending=env.api.pollJobs(false,{jobsOnly:true});
  env.api.onJob(job('first','completed','listen',5));
  release([job('first','running','listen',2)]);
  await pending;
  assert.equal(env.state.jobs[0].status,'completed');
  assert.equal(env.timers.size,0);
});

test('temporary status errors retry read-only polling without refreshing the book',async()=>{
  let checks=0;
  const env=environment(() => {if (++checks === 1) throw new Error('Test offline');return [job('first','cancelled','listen',5)];});
  env.state.jobs=[job('first','running')];
  await env.api.pollJobs(false,{jobsOnly:true});
  assert.equal([...env.timers.values()][0].delay,5000);
  await env.runNext();
  assert.equal(env.state.jobs[0].status,'cancelled');
  assert.equal(env.rendered.books.length,0);
  assert.equal(env.calls.length,2);
});
