/* Playing a saved performance: reads its audio, waits for a running job,
   follows only its chapters and never asks for new narration. */
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(times = 40) { for (let i = 0; i < times; i++) await tick(); }
class Container { constructor() { this.innerHTML = ''; this.listeners = {}; } addEventListener(name, fn) { this.listeners[name] = fn; } }

// Three chapters of two passages; the performance covers chapters one and three.
const book = () => ({id:'book-p',revision:1,
  chapters:[{id:'c1',kind:'chapter'},{id:'c2',kind:'chapter'},{id:'c3',kind:'chapter'}],
  segments:['c1','c1','c2','c2','c3','c3'].map((chapter,i) => ({id:`p${i}`,chapter_id:chapter,start:i*10,end:i*10+8,text:`Passage ${i}.`}))});
const clip = id => ({url:`/api/books/book-p/audio-assets/${id}`,duration:2,asset_id:id});
const record = (job = {id:'job-p',status:'completed'}) => ({id:'pf_1',name:'Evening reading',mode:'simple',chapter_ids:['c1','c3'],
  narrator_label:'Kore · Gemini',job,progress:{passages_total:4,passages_ready:4,seconds_ready:8,chapters:[]}});

function environment({audio = () => ({p0:clip('p0'),p1:clip('p1'),p4:clip('p4'),p5:clip('p5')}), performance = () => record(), storage = new Map()} = {}) {
  const calls = [], container = new Container(), timers = [];
  const scope = {window:{},setTimeout:(fn,ms) => { if (ms >= 1000) timers.push(fn); else setImmediate(fn); return timers.length; },
    localStorage:{getItem:key => storage.get(key) ?? null,setItem:(key,value) => storage.set(key,value)},
    fetch:async (url, options = {}) => {
      const call = {url,method:options.method || 'GET'};
      calls.push(call);
      let data = {};
      if (url.endsWith('/performances/pf_1')) data = {performance:performance()};
      else if (url.endsWith('/performances/pf_1/audio')) data = {performance_id:'pf_1',audio:audio()};
      else if (url.includes('/listen/takes')) data = {takes:[]};
      else if (url.startsWith('/api/jobs')) data = [];
      return {ok:true,status:200,json:async () => data};
    }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../bardic/static/listen.js'),'utf8'),scope);
  const api = scope.window.BardicListen, value = book();
  const options = {chapterId:'c1',segmentId:'p0',playbackRate:1,status:{providers:[{id:'system',available:true}]}};
  const posts = () => calls.filter(call => call.method === 'POST');
  // Time passes for the performance poll: run the queued waits.
  async function advance() { for (const fn of timers.splice(0)) fn(); await settle(); }
  return {api,book:value,container,options,calls,posts,advance,storage,
    async init() { await api.render(container,value,options); await api.usePerformance(value,record()); await settle(); }};
}

test('a performance plays its own audio and never requests narration', async () => {
  const env = environment();
  await env.init();
  assert.equal(env.api.enabled(env.book),true,'the app plays it through the one-voice path');
  assert.equal(env.api.getPerformance(env.book).name,'Evening reading');
  assert.equal((await env.api.prepare(env.book,env.book.segments[0],{})).asset_id,'p0');
  assert.equal(env.api.resolve(env.book,env.book.segments[2]),null,'no audio outside the performance');
  await assert.rejects(env.api.prepare(env.book,env.book.segments[2],{}),/not part of “Evening reading”/);
  assert.equal(env.posts().length,0);
  assert.match(env.container.innerHTML,/<h3>Performance<\/h3><p>Evening reading/);
});

test('continuous playback skips chapters the performance leaves out and ends after its last chapter', async () => {
  const env = environment();
  await env.init();
  const [p0,p1,p2,p3,p4,p5] = env.book.segments;
  assert.equal(env.api.nextSegment(env.book,p0).id,'p1');
  assert.equal(env.api.nextSegment(env.book,p1).id,'p4','chapter two is skipped');
  assert.equal(env.api.nextSegment(env.book,p5),null,'the performance ends after its last chapter');
  assert.equal(env.api.allowsAdvance(env.book,p1,p2),false);
  // Crossing from chapter one to three keeps the listening intent.
  await env.api.prepare(env.book,p1,{});
  env.api.updatePlayback(env.book,p1,{playbackRate:1});
  assert.equal((await env.api.prepare(env.book,p4,{continuation:true})).asset_id,'p4');
  env.api.setContinuous(env.book,false);
  assert.equal(env.api.nextSegment(env.book,p1),null,'without continuous listening it stops at the chapter end');
  assert.equal(env.posts().length,0);
});

test('while the performance is processing, a missing passage waits for it; a stopped one explains how to finish', async () => {
  let ready = false, job = {id:'job-p',status:'running',message:'Chapter 2 of 2'};
  const env = environment({performance:() => record(job),
    audio:() => ({p0:clip('p0'),p1:clip('p1'),...(ready ? {p4:clip('p4')} : {})})});
  await env.init();
  const waiting = env.api.prepare(env.book,env.book.segments[4],{});
  await settle();
  assert.match(env.container.innerHTML,/Waiting for “Evening reading”/);
  ready = true;
  await env.advance();
  assert.equal((await waiting).asset_id,'p4');
  job = {id:'job-p',status:'quota_limited'};
  await env.api.refreshPerformance(env.book);
  await assert.rejects(env.api.prepare(env.book,env.book.segments[5],{}),/daily request limit/);
  assert.equal(env.posts().length,0);
});

test('the chosen performance is remembered for the book, and leaving it returns to one narrator', async () => {
  const storage = new Map();
  const first = environment({storage});
  await first.init();
  assert.equal(JSON.parse(storage.get('bardic:listen:book-p')).performanceId,'pf_1');
  const again = environment({storage});
  await again.api.render(again.container,again.book,again.options);
  await settle();
  assert.equal(again.api.getPerformance(again.book).loaded,true,'a reload restores the performance');
  assert.equal(again.api.resolve(again.book,again.book.segments[4]).asset_id,'p4');
  again.api.leavePerformance(again.book);
  assert.equal(again.api.getPerformance(again.book),null);
  assert.equal(again.api.getSelection(again.book).mode,'simple');
});

test('the panel Play button plays the performance and never switches to live narration', async () => {
  const env = environment();
  let toggles = 0, plays = 0;
  env.options.onToggle = () => { toggles++; };
  env.options.onPlay = () => { plays++; };
  await env.init();
  env.container.listeners.click({target:{closest:selector => selector === '[data-listen-action]' ? {dataset:{listenAction:'start'}} : null}});
  await settle();
  assert.equal(toggles,1);
  assert.equal(plays,0);
  assert.equal(env.api.getPerformance(env.book)?.id,'pf_1','still the performance');
  assert.equal(env.posts().length,0);
});

test('listening while it records only reads: it follows new passages and never sends anything but reads', async () => {
  const ready = new Set(['p0','p1']), job = {id:'job-p',status:'running',message:'Chapter 2 of 2'};
  const env = environment({performance:() => record(job),
    audio:() => Object.fromEntries([...ready].map(id => [id,clip(id)]))});
  await env.init();
  // Playing the ready passages, then reaching the frontier, then the recording lands the next one.
  await env.api.prepare(env.book,env.book.segments[0],{});
  env.api.updatePlayback(env.book,env.book.segments[0],{playbackRate:1});
  const waiting = env.api.prepare(env.book,env.book.segments[4],{continuation:true});
  await settle();
  assert.match(env.container.innerHTML,/Waiting for “Evening reading” to reach this passage… Everything recorded so far has played\./);
  assert.match(env.container.innerHTML,/Recording continues while you listen/);
  ready.add('p4');
  await env.advance();
  assert.equal((await waiting).asset_id,'p4');
  const sent = env.calls.filter(call => !(call.method === 'GET' && /\/performances\/pf_1(\/audio)?$|\/listen\/takes|^\/api\/jobs/.test(call.url)));
  assert.deepEqual(sent.map(call => `${call.method} ${call.url}`),[],'only reads of the performance, its audio and its job');
});
