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

function environment({book = story(), jobs, takes = () => [], preview, chapterPost, jobsGet} = {}) {
  const calls = [], container = new Container(), storage = new Map();
  const state = {jobs:jobs || [], takes};
  const scope = {window:{},setTimeout:fn => setImmediate(fn),localStorage:{getItem:key => storage.get(key) || null,setItem:(key,value) => storage.set(key,value)},
    fetch:async (url, options = {}) => {
      const call = {url,method:options.method || 'GET',body:options.body ? JSON.parse(options.body) : null};
      calls.push(call);
      let data;
      if (url.endsWith('/listen/chapter/preview')) data = preview || {session:{id:'session-g'},requests_needed:3,quota:{requests_today:34,rpd:100,resets_at:'2026-09-28T07:00:00+00:00'},chunks:[]};
      else if (url.endsWith('/listen/chapter') && chapterPost) {
        const result = await chapterPost(call,state);
        if (result?.statusCode) return {ok:false,status:result.statusCode,json:async () => ({detail:result.detail})};
        data = result;
      }
      else if (url.endsWith('/listen/chapter')) {
        const job = {id:'job-1',kind:'listen_chapter',status:'running',chapter_id:'chapter-a',session_id:'session-g',chunks:[],projection:[],
          chunking:{concurrency:2},limits:{rpm:10},quota:{requests_today:35,rpd:100},calibration:{chars_per_second:14,realtime_factor:2}};
        state.jobs = [job];
        data = {session:{id:'session-g'},job,joined:false};
      } else if (url.startsWith('/api/jobs?')) data = jobsGet ? await jobsGet(state) : state.jobs;
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
  assert.match(env.container.innerHTML,/Stop preparing/);
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
  assert.match(env.container.innerHTML,/Prepare rest of chapter · paid/);
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
  assert.match(env.container.innerHTML,/Preparing · 1 request in progress/);
  assert.match(env.container.innerHTML,/Still to prepare/);
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
  assert.match(env.container.innerHTML,/Daily request limit reached/);
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
  assert.match(env.container.innerHTML,/aria-label="Play with one narrator">Play/);
  assert.match(env.container.innerHTML,/Resume chapter/);
});

test('automatic continuation and stopped jobs never send generation requests', async () => {
  const stoppedJob = {id:'job-s',kind:'listen_chapter',status:'failed',chapter_id:'chapter-a',session_id:'session-g',
    error:'Gemini narration timed out. The request may still have been processed, so it was not resent.',chunks:[],projection:[]};
  const ready = [{segment_id:'p0',audio:clip('p0','chunk-a',0,3)},{segment_id:'p1',audio:clip('p1','chunk-a',3,6)}];
  // Continuation into an ungenerated passage with no job: no POST.
  const fresh = environment({takes:() => ready});
  await fresh.init();
  await assert.rejects(fresh.api.prepare(fresh.book,fresh.book.segments[2],{continuation:true}),/is not prepared yet/);
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

test('a narrator change during an in-flight Play cancels that job and never binds its session', async () => {
  let release;
  const env = environment({chapterPost:() => new Promise(resolve => { release = () => resolve({session:{id:'session-kore'},joined:false,
    job:{id:'job-kore',kind:'listen_chapter',status:'running',chapter_id:'chapter-a',session_id:'session-kore',chunks:[],projection:[]}}); })});
  await env.init();
  const playing = env.api.prepare(env.book,env.book.segments[0],{});
  await settle(10);
  env.change('voice','Puck');
  release();
  await assert.rejects(playing,/narrator changed/);
  assert.ok(env.calls.some(call => call.url === '/api/jobs/job-kore/cancel'));
  assert.notEqual(env.api.getChapterJob(env.book)?.id,'job-kore');
});

test('rejected Play clears the warmup; device voices get no chunk marks; onJob reaches the app', async () => {
  const rejected = environment({chapterPost:() => ({statusCode:409,detail:'A job is already working on this book.'})});
  await rejected.init();
  await assert.rejects(rejected.api.prepare(rejected.book,rejected.book.segments[0],{}),/already working/);
  assert.doesNotMatch(rejected.container.innerHTML,/Preparing…/);
  const jobs = [];
  const shared = environment({takes:() => [{segment_id:'p0',audio:clip('p0','chunk-a',0,3)}]});
  shared.options.onJob = job => jobs.push(job.id);
  await shared.init();
  shared.click('prepare-chapter');
  await settle();
  assert.ok(jobs.includes('job-1'),'the app shell learns about the chapter job');
  shared.state.jobs = [{...shared.state.jobs[0],status:'completed'}];
  await settle();
  shared.change('provider','system');
  assert.equal(shared.api.chapterMarks(shared.book,'chapter-a').size,0,'device voices have no chunk marks');
});

test('status polling re-renders only on change, stops on book switch and never overwrites a newer job', async () => {
  let renders = 0, hold = null;
  const running = {id:'job-1',kind:'listen_chapter',status:'running',chapter_id:'chapter-a',session_id:'session-g',
    chunks:[{status:'requesting',first_segment_id:'p0',last_segment_id:'p3'}],projection:[],chunking:{concurrency:2},limits:{rpm:10},quota:{requests_today:1,rpd:100}};
  const env = environment({jobs:[running],
    chapterPost:() => ({session:{id:'session-g'},joined:false,job:{...running,id:'job-2',chunks:[]}}),
    jobsGet:state => {
      if (state.hold && !hold) return new Promise(resolve => { hold = () => resolve([running]); });
      return state.jobs;
    }});
  env.options.onChange = () => { renders++; };
  await env.init();
  await settle(40);
  const before = renders;
  await settle(80);
  assert.equal(renders,before,'unchanged polls do not re-render the reader');
  // A status read for job-1 is in flight when job-2 starts; it must not win.
  env.state.hold = true;
  await settle(40);
  assert.ok(hold,'a job read is in flight');
  env.state.jobs = [{...running,status:'cancelled'}];
  await env.api.prepareChapter(env.book,env.book.segments[0]);
  assert.equal(env.api.getChapterJob(env.book).id,'job-2');
  env.state.hold = false;
  hold();
  await settle(20);
  assert.equal(env.api.getChapterJob(env.book).id,'job-2','the late job-1 read was ignored');
  env.state.jobs = [{...running,id:'job-2',status:'running'}];
  // Switching books stops polling for the hidden book.
  const other = {...story(3),id:'other-book'};
  await env.api.render(env.container,other,{...env.options,chapterId:'chapter-a',segmentId:'p0'});
  await settle(20);
  const polls = env.calls.filter(call => call.url === '/api/jobs?book_id=book-chunks').length;
  await settle(80);
  assert.equal(env.calls.filter(call => call.url === '/api/jobs?book_id=book-chunks').length,polls,'no polling for a hidden book');
  // The other book starts with one narrator and adopts this fake running job;
  // finish it so that book's watcher settles and the test can exit.
  env.state.jobs = [{...running,id:'job-2',status:'completed'}];
  await settle(20);
});

test('automatic continuation into a passage outside the running job fails immediately', async () => {
  const running = {id:'job-1',kind:'listen_chapter',status:'running',chapter_id:'chapter-a',session_id:'session-g',
    chunks:[{status:'requesting',first_segment_id:'p6',last_segment_id:'p9'}],projection:[]};
  const env = environment({jobs:[running]});
  await env.init();
  await settle();
  await assert.rejects(env.api.prepare(env.book,env.book.segments[2],{continuation:true}),/outside the chapter being prepared/);
  assert.equal(env.generation().length,0);
  env.state.jobs = [{...running,status:'completed'}];
  await settle();
});

// Continuous listening: while Play runs, the next chapter is queued ahead of
// the listener, once per chapter, and never after a quota stop or Stop generating.
const chapterA = () => Array.from({length:12},(_,i) => ({segment_id:`p${i}`,audio:clip(`p${i}`,'chunk-a',i*3,i*3+3)}));
const nextChapterJob = (status = 'running') => ({id:'job-b',kind:'listen_chapter',status,chapter_id:'chapter-b',session_id:'session-g',chunks:[],projection:[]});
const startsNext = (call, state) => { state.jobs = [nextChapterJob()]; return {session:{id:'session-g'},job:state.jobs[0],joined:false}; };
async function playing(env) {
  await env.api.prepare(env.book,env.book.segments[0],{playbackRate:1});
  env.api.updatePlayback(env.book,env.book.segments[0],{playbackRate:1,currentTime:1});
  await settle();
}

test('continuous: playing queues the next chapter once, with full-size chunks', async () => {
  const env = environment({takes:chapterA,chapterPost:startsNext});
  await env.init();
  assert.equal(env.generation().length,0,'rendering queues nothing');
  await playing(env);
  assert.equal(env.generation().length,1);
  assert.equal(env.generation()[0].body.segment_id,'p12','the first passage without audio, in the next chapter');
  assert.equal(env.generation()[0].body.intent,'queue');
  env.state.jobs = [nextChapterJob('completed')];
  await settle();
  env.api.updatePlayback(env.book,env.book.segments[1],{playbackRate:1});
  await settle();
  assert.equal(env.generation().length,1,'one automatic attempt per chapter per Play');
  env.api.stop(env.book);
  env.api.updatePlayback(env.book,env.book.segments[1],{playbackRate:1});
  await settle();
  assert.equal(env.generation().length,1,'no queueing after Stop');
});

test('continuous: queueing ahead waits for an explicit Resume after a quota stop or Stop generating', async () => {
  const limited = environment({takes:chapterA,jobs:[{...nextChapterJob('quota_limited'),chapter_id:'chapter-a',voice:'Kore',model:'gemini-3.8-flash-tts'}]});
  await limited.init();
  await settle();
  await playing(limited);
  assert.equal(limited.generation().length,0,'a quota stop is not retried automatically');

  const held = environment({takes:chapterA,jobs:[{...nextChapterJob('running'),chapter_id:'chapter-a',voice:'Kore',model:'gemini-3.8-flash-tts'}]});
  await held.init();
  await settle();
  held.click('stop-generating');
  await settle();
  held.state.jobs = [{...held.state.jobs[0],status:'cancelled'}];
  await settle();
  // Play with continuation keeps the hold; only a fresh explicit Play lifts it.
  await held.api.prepare(held.book,held.book.segments[0],{playbackRate:1,continuation:true});
  held.api.updatePlayback(held.book,held.book.segments[0],{playbackRate:1});
  await settle();
  assert.ok(!held.generation().some(call => call.url.endsWith('/listen/chapter')),'Stop generating holds automatic queueing');
});

test('continuous: playback crossing into an unqueued chapter starts that chapter; turning it off restores the stop', async () => {
  const env = environment({takes:chapterA,chapterPost:startsNext});
  await env.init();
  env.api.setContinuous(env.book,false);
  await env.api.prepare(env.book,env.book.segments[11],{playbackRate:1});
  await assert.rejects(env.api.prepare(env.book,env.book.segments[12],{continuation:true}),/is not prepared yet/);
  assert.equal(env.generation().length,0,'without continuous listening the chapter end stops playback');
  env.api.setContinuous(env.book,true);
  await env.api.prepare(env.book,env.book.segments[11],{playbackRate:1});
  const waiting = env.api.prepare(env.book,env.book.segments[12],{continuation:true});
  await settle();
  assert.equal(env.generation().length,1);
  assert.equal(env.generation()[0].body.segment_id,'p12');
  env.api.stop(env.book);
  assert.equal(await waiting,null);
  env.state.jobs = [nextChapterJob('completed')];
  await settle();
});

const cancels = env => env.calls.filter(call => call.url === '/api/jobs/job-b/cancel').length;
const chapterPosts = env => env.generation().filter(call => call.url.endsWith('/listen/chapter'));

test('continuous: Pause cancels a job it queued automatically, and the next Play may restart it', async () => {
  const env = environment({takes:chapterA,chapterPost:startsNext});
  await env.init();
  await playing(env);
  assert.equal(chapterPosts(env).length,1);
  env.api.stop(env.book,{keepAhead:true});
  assert.equal(cancels(env),0,'moving within the book keeps the job queued ahead');
  await playing(env);
  assert.equal(chapterPosts(env).length,1,'the running job is not queued twice');
  env.api.stop(env.book);
  assert.equal(cancels(env),1,'Pause or Stop cancels the automatic job');
  await settle();
  // The pause-cancelled job does not demand Resume chapter: explicit Play restarts it.
  const again = env.api.prepare(env.book,env.book.segments[12],{});
  await settle();
  assert.equal(chapterPosts(env).length,2);
  env.api.stop(env.book);
  assert.equal(await again,null);
  env.state.jobs = [nextChapterJob('completed')];
  await settle();
});

test('continuous: Pause while the automatic request is in flight cancels the job it creates', async () => {
  let release;
  const env = environment({takes:chapterA,chapterPost:(call,state) => new Promise(resolve => { release = () => resolve(startsNext(call,state)); })});
  await env.init();
  await playing(env);
  assert.ok(release,'lookahead request sent');
  env.api.stop(env.book);
  release();
  await settle();
  assert.equal(cancels(env),1);
  env.state.jobs = [nextChapterJob('cancelled')];
  await settle();
});

test('continuous: crossing while the lookahead request is in flight waits for it instead of sending another', async () => {
  let release;
  const env = environment({takes:chapterA,chapterPost:(call,state) => new Promise(resolve => { release = () => resolve(startsNext(call,state)); })});
  await env.init();
  await playing(env);
  const crossing = env.api.prepare(env.book,env.book.segments[12],{continuation:true});
  await settle();
  release();
  await settle();
  assert.deepEqual(chapterPosts(env).map(call => call.body.intent),['queue'],'one request, not a second quick-start');
  env.api.stop(env.book,{keepAhead:true});
  assert.equal(await crossing,null);
  env.state.jobs = [nextChapterJob('completed')];
  await settle();
});

test('continuous: listening does not run on into back matter', async () => {
  const book = {...story(),chapters:[{id:'chapter-a',kind:'chapter'},{id:'chapter-b',kind:'back_matter'}]};
  const env = environment({book,takes:chapterA,chapterPost:startsNext});
  await env.init();
  await playing(env);
  assert.equal(chapterPosts(env).length,0,'notes or an index are not narrated automatically');
  assert.equal(env.api.allowsAdvance(book,book.segments[11],book.segments[12]),false);
  env.api.stop(env.book);
});
