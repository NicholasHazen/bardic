/* Gemini simple listening through server chapter jobs: estimates, queueing and marks. */
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(times = 80) { for (let i = 0; i < times; i++) await tick(); }
class Container { constructor() { this.innerHTML = ''; this.listeners = {}; } addEventListener(name, fn) { this.listeners[name] = fn; } }

function story(count = 12) {
  return {id:'book-chunks',revision:1,segments:Array.from({length:count+1},(_,i)=>({
    id:`p${i}`,chapter_id:i < count ? 'chapter-a' : 'chapter-b',start:i*30,end:i*30+28,text:`Passage ${i} of the harbor story.`,
  }))};
}
const clip = (id, chunk, start, end) => ({url:`/api/books/book-chunks/listen/audio/${chunk}`,asset_id:chunk,chunk_id:chunk,
  clip_start:start,clip_end:end,duration:end-start,chunk_duration:40,timing:'estimated',mode:'simple',available:true});

function environment({book = story(), jobs, takes = () => [], preview} = {}) {
  const calls = [], container = new Container(), storage = new Map();
  const state = {jobs:jobs || [], takes};
  const scope = {window:{},setTimeout:fn => setImmediate(fn),localStorage:{getItem:key => storage.get(key) || null,setItem:(key,value) => storage.set(key,value)},
    fetch:async (url, options = {}) => {
      const call = {url,method:options.method || 'GET',body:options.body ? JSON.parse(options.body) : null};
      calls.push(call);
      let data;
      if (url.endsWith('/listen/chapter/preview')) data = preview || {session:{id:'session-g'},requests_needed:3,quota:{requests_today:34,rpd:100,resets_at:'2026-09-28T07:00:00+00:00'},chunks:[]};
      else if (url.endsWith('/listen/chapter')) {
        const job = {id:'job-1',kind:'listen_chapter',status:'running',chapter_id:'chapter-a',session_id:'session-g',chunks:[],projection:[],
          chunking:{concurrency:2},limits:{rpm:10},quota:{requests_today:35,rpd:100},calibration:{chars_per_second:14,realtime_factor:2}};
        state.jobs = [job];
        data = {session:{id:'session-g'},job,joined:false};
      } else if (url.startsWith('/api/jobs?')) data = state.jobs;
      else if (url.includes('/listen/takes')) data = {session:{id:'session-g'},takes:state.takes()};
      else if (url.endsWith('/cancel')) { state.jobs = state.jobs.map(job => ({...job,status:'cancelled'})); data = state.jobs[0]; }
      else if (url === '/api/settings') data = {listen_chunking:{...call.body.listen_chunking}};
      else data = {};
      return {ok:true,status:200,json:async () => data};
    }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../bardic/static/listen.js'),'utf8'),scope);
  const api = scope.window.BardicListen;
  const options = {chapterId:'chapter-a',segmentId:'p0',playbackRate:1.5,
    status:{has_api_key:true,providers:[{id:'system',available:false},{id:'gemini',available:true}],tts_model:'gemini-3.8-flash-tts',
      tts_models:['gemini-3.8-flash-tts'],listen_chunking:{ramp_seconds:[30,60],target_seconds:420,concurrency:2}}};
  const click = action => container.listeners.click({target:{closest:() => ({dataset:{listenAction:action}})}});
  const change = (field, value) => container.listeners.change({target:{dataset:{listenField:field},value}});
  // The app re-renders on onChange; do that explicitly after configuration.
  async function init() { await api.render(container,book,options); change('mode','simple'); change('provider','gemini'); await api.render(container,book,options); await settle(); }
  const generation = () => calls.filter(call => call.method === 'POST' && !call.url.endsWith('/preview') && call.url !== '/api/settings');
  return {api,book,container,options,calls,state,click,change,init,generation};
}

test('estimate: measured ready time, safe pace, catch-up warning and quota blocking', () => {
  const book = story(6);
  const segments = book.segments.slice(0,6);
  const ready = new Map([['p0',{duration:30}],['p1',{duration:30}]]);
  const now = 1000;
  const running = {status:'running',chunking:{concurrency:1},limits:{rpm:10},quota:{requests_today:10,rpd:100},
    calibration:{chars_per_second:1,realtime_factor:1},
    chunks:[{status:'requesting',first_segment_id:'p2',last_segment_id:'p3',started_at:new Date((now-10)*1000).toISOString(),expected_latency:40}],
    projection:[{first_segment_id:'p4',last_segment_id:'p5',expected_seconds:56}]};
  const {estimateChapter} = environment().api;
  const slow = estimateChapter({segments,audioFor:s => ready.get(s.id),job:running,rate:1,now});
  assert.equal(slow.readySeconds,60);
  assert.equal(slow.remainingSeconds,112, 'unready passages use text length at the calibrated rate');
  assert.equal(slow.readyPassages,2);
  // p2 is ready at now+30; playback reaches it at now+60. p4 finishes at now+30+59, reached at now+116.
  assert.equal(slow.safe,true);
  const fast = estimateChapter({segments,audioFor:s => ready.get(s.id),job:running,rate:2.5,now});
  assert.equal(fast.safe,false);
  assert.ok(fast.waitSeconds > 0 && fast.maxSafeRate < 2.5 && fast.maxSafeRate > 1);
  assert.equal(fast.catchUp.index,2,'at 2.5x the listener reaches the running chunk 24 s in, before it lands');
  assert.equal(Math.round(fast.catchUp.seconds),24);
  const blocked = estimateChapter({segments,audioFor:s => ready.get(s.id),job:{...running,quota:{requests_today:100,rpd:100}},rate:1,now});
  assert.equal(blocked.quotaBlocked,true);
  const idle = estimateChapter({segments,audioFor:s => ready.get(s.id),job:null,rate:1,now});
  assert.equal(idle.generating,false);
  assert.equal(idle.etaSeconds,Infinity);
});

test('Gemini Play starts one chapter job, waits for its chunk, and never requests single passages', async () => {
  let delivered = false;
  const env = environment({takes:() => delivered ? [{segment_id:'p0',audio:clip('p0','chunk-a',0,3.2)},{segment_id:'p1',audio:clip('p1','chunk-a',3.2,7)}] : []});
  await env.init();
  const waiting = env.api.prepare(env.book,env.book.segments[0],{playbackRate:1.5});
  await settle();
  const posts = env.generation();
  assert.equal(posts.length,1);
  assert.equal(posts[0].url,'/api/books/book-chunks/listen/chapter');
  assert.deepEqual(posts[0].body,{provider:'gemini',voice:'Kore',model:'gemini-3.8-flash-tts',segment_id:'p0',intent:'play'});
  assert.match(env.container.innerHTML,/Stop generating/);
  env.state.jobs = [{...env.state.jobs[0],chunks:[{status:'done'}]}];
  delivered = true;
  const audio = await waiting;
  assert.equal(audio.clip_end,3.2);
  assert.equal(env.api.resolve(env.book,env.book.segments[1]).clip_start,3.2);
  env.api.updatePlayback(env.book,env.book.segments[0],{playbackRate:1.5,currentTime:1});
  await settle();
  assert.equal(env.generation().length,1,'the rolling buffer does not add per-passage requests');
  assert.equal(env.calls.filter(call => /\/listen$/.test(call.url)).length,0);
  env.state.jobs = [{...env.state.jobs[0],status:'completed'}];
  await settle();
});

test('queue, stop generating, marks and chunk presets', async () => {
  const env = environment({takes:() => [{segment_id:'p0',audio:clip('p0','chunk-a',0,3)},{segment_id:'p1',audio:clip('p1','chunk-a',3,6)},
    {segment_id:'p2',audio:clip('p2','chunk-b',0,4)}]});
  await env.init();
  assert.match(env.container.innerHTML,/Queue chapter/);
  assert.match(env.container.innerHTML,/34 of 100 daily Gemini requests/, 'the local preview reports the library count');
  assert.match(env.container.innerHTML,/3 more requests for this chapter/);
  env.click('prepare-chapter');
  await settle();
  const queued = env.generation().at(-1);
  assert.equal(queued.body.intent,'queue');
  env.state.jobs = [{...env.state.jobs[0],chunks:[{status:'done'},{status:'requesting',first_segment_id:'p3',last_segment_id:'p5'}],
    projection:[{first_segment_id:'p6',last_segment_id:'p8',expected_seconds:20}]}];
  await settle(40);
  const marks = env.api.chapterMarks(env.book,'chapter-a');
  assert.deepEqual({...marks.get('p0')},{status:'ready',chunk:0,start:true});
  assert.deepEqual({...marks.get('p1')},{status:'ready',chunk:0,start:false});
  assert.deepEqual({...marks.get('p2')},{status:'ready',chunk:1,start:true});
  assert.equal(marks.get('p4').status,'generating');
  assert.equal(marks.get('p7').status,'queued');
  assert.equal(marks.has('p10'),false);
  assert.match(env.container.innerHTML,/Generating 1 chunk/);
  assert.match(env.container.innerHTML,/Left to generate/);
  env.click('stop-generating');
  await settle(40);
  assert.ok(env.calls.some(call => call.url === '/api/jobs/job-1/cancel'));
  env.change('chunk-preset','largest');
  await settle();
  assert.deepEqual(env.calls.at(-1).body,{listen_chunking:{ramp_seconds:[],target_seconds:420,concurrency:2}});
  env.change('chunk-length','999');
  await settle();
  assert.notEqual(env.calls.at(-1).body?.listen_chunking?.target_seconds,999,'unknown lengths are ignored');
});

test('quota-limited job offers resume and explains the reset', async () => {
  const job = {id:'job-q',kind:'listen_chapter',status:'quota_limited',chapter_id:'chapter-a',session_id:'session-g',
    message:'The daily Gemini request quota for this model is used up.',chunks:[],projection:[],quota:{requests_today:100,rpd:100,resets_at:'2026-09-28T07:00:00+00:00'}};
  const env = environment({jobs:[job]});
  await env.init();
  await settle();
  assert.match(env.container.innerHTML,/Daily request quota reached/);
  assert.match(env.container.innerHTML,/Resume chapter/);
  assert.match(env.container.innerHTML,/daily Gemini request quota/);
  assert.equal(env.generation().length,0,'discovering a finished job starts nothing');
});

test('a chapter job that stops before the selected passage clears the warmup and offers Play again', async () => {
  const env = environment();
  await env.init();
  const waiting = env.api.prepare(env.book,env.book.segments[5],{playbackRate:1});
  await settle();
  assert.match(env.container.innerHTML,/Preparing…/);
  env.state.jobs = [{...env.state.jobs[0],status:'quota_limited',message:'The daily Gemini request quota for this model is used up.'}];
  await assert.rejects(waiting,/daily Gemini request quota/);
  await settle();
  assert.doesNotMatch(env.container.innerHTML,/Preparing…/);
  assert.match(env.container.innerHTML,/aria-label="Play simple listening">Play/);
  assert.match(env.container.innerHTML,/Resume chapter/);
});

test('automatic continuation and stopped jobs never send generation requests', async () => {
  const stoppedJob = {id:'job-s',kind:'listen_chapter',status:'failed',chapter_id:'chapter-a',session_id:'session-g',
    error:'Gemini narration timed out. The request may still have been processed, so it was not resent.',chunks:[],projection:[]};
  const ready = [{segment_id:'p0',audio:clip('p0','chunk-a',0,3)},{segment_id:'p1',audio:clip('p1','chunk-a',3,6)}];
  // Continuation into an ungenerated passage with no job: no POST.
  const fresh = environment({takes:() => ready});
  await fresh.init();
  await assert.rejects(fresh.api.prepare(fresh.book,fresh.book.segments[2],{continuation:true}),/has not been generated/);
  assert.equal(fresh.generation().length,0);
  // A failed (possibly billed) job: continuation and explicit Play both stop; Resume is explicit.
  const failed = environment({jobs:[{...stoppedJob,voice:'Kore',model:'gemini-3.8-flash-tts'}],takes:() => ready});
  await failed.init();
  await settle();
  await assert.rejects(failed.api.prepare(failed.book,failed.book.segments[2],{continuation:true}),/Resume chapter/);
  await assert.rejects(failed.api.prepare(failed.book,failed.book.segments[2],{}),/Resume chapter/);
  const readyAudio = await failed.api.prepare(failed.book,failed.book.segments[0],{});
  assert.equal(readyAudio.clip_end,3,'saved audio still plays');
  assert.equal(failed.generation().length,0,'no request is resent without Resume');
  assert.doesNotMatch(failed.container.innerHTML,/Preparing…/);
  failed.click('prepare-chapter');
  await settle();
  assert.equal(failed.generation().length,1);
  assert.equal(failed.generation()[0].body.intent,'queue','Resume is an explicit queue request');
  failed.state.jobs = [{...failed.state.jobs[0],status:'completed'}];
  await settle();
});
