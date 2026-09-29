/* The performances panel: list, preview (local), create, resume, play. */
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(times = 30) { for (let i = 0; i < times; i++) await tick(); }
class Container {
  constructor() { this.innerHTML = ''; this.listeners = {}; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  contains() { return false; }
  querySelector() { return null; }
}
const book = {id:'book-q',title:'The Lantern',chapters:[{id:'front',title:'Title page',kind:'front_matter'},{id:'c1',title:'One',kind:'chapter'},{id:'c2',title:'Two',kind:'chapter'}],
  segments:[{id:'s0',chapter_id:'front'},{id:'s1',chapter_id:'c1'},{id:'s2',chapter_id:'c2'}]};
const listed = [{id:'pf_1',name:'Evening',mode:'simple',chapter_ids:['c1'],narrator_label:'Samantha · Device',
  job:{id:'job-1',status:'cancelled'},progress:{passages_total:2,passages_ready:1,seconds_ready:4,chapters:[]}}];

function environment(preview = {}, records = listed, container = new Container()) {
  const calls = [], played = [], jobs = [];
  const scope = {window:{},document:{activeElement:null},CSS:{escape:value => value},setTimeout:(fn,ms) => { if (!(ms >= 1000)) setImmediate(fn); return 0; },clearTimeout:() => {},
    fetch:async (url, options = {}) => {
      const call = {url,method:options.method || 'GET',body:options.body ? JSON.parse(options.body) : null};
      calls.push(call);
      let data = {};
      if (url === '/api/books/book-q/performances' && call.method === 'GET') data = {performances:records};
      else if (/\/performances\/pf_\d+\/preview$/.test(url)) data = {passages_total:6,passages_ready:1,passages_to_generate:call.body.chapter_ids.length ? 4 : 1,requests_estimate:call.body.chapter_ids.length ? 2 : 1,expected_seconds:60,chapter_ids:['c1'],added_chapter_ids:call.body.chapter_ids,chapters:[],problems:[],notes:[],quota:null,...preview};
      else if (url.endsWith('/chapters')) data = {performance:records[0],job:{id:'job-4',status:'queued'}};
      else if (url.endsWith('/performances/preview')) data = {passages_total:2,passages_ready:0,passages_to_generate:2,requests_estimate:2,expected_seconds:60,chapters:[],problems:[],notes:[],quota:null,...preview};
      else if (url === '/api/books/book-q/performances') data = {performance:{...listed[0],id:'pf_2'},job:{id:'job-2',status:'queued'}};
      else if (url.endsWith('/prepare')) data = {performance:listed[0],job:{id:'job-3',status:'queued'}};
      return {ok:true,status:200,json:async () => data};
    }};
  vm.createContext(scope);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../bardic/static/ui.js'),'utf8'),scope);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../bardic/static/performances.js'),'utf8'),scope);
  const listen = {getSelection:() => ({provider:'system'}),getPerformance:() => null,
    narratorOptions:(_book, provider) => ({provider,available:true,voice:provider === 'system' ? 'Samantha' : 'Kore',model:provider === 'gemini' ? 'tts-model' : null,
      voices:[{id:'Samantha',name:'Samantha',usable:true},{id:'Kore',name:'Kore',usable:true}]})};
  const options = {listen,onPlay:record => played.push(record.id),onJob:job => jobs.push(job)};
  const click = dataset => container.listeners.click({target:{closest:() => ({dataset,closest:() => dataset.card ? {dataset:{performance:dataset.card}} : null})}});
  return {api:scope.window.BardicPerformances,container,options,calls,played,jobs,click};
}

test('lists performances, plays one and resumes processing', async () => {
  const env = environment();
  env.api.render(env.container,book,env.options);
  await settle();
  assert.match(env.container.innerHTML,/Evening/);
  assert.match(env.container.innerHTML,/1 of 2 passages ready/);
  env.click({performanceAction:'play',card:'pf_1'});
  await settle();
  assert.deepEqual(env.played,['pf_1']);
  env.click({performanceAction:'resume',card:'pf_1'});
  await settle();
  assert.ok(env.calls.some(call => call.url === '/api/books/book-q/performances/pf_1/prepare' && call.method === 'POST'));
  assert.equal(env.jobs.at(-1).id,'job-3');
});

test('the new-performance form previews locally and creates with the chosen narrator and chapters', async () => {
  const env = environment();
  env.api.render(env.container,book,env.options);
  await settle();
  env.click({performanceAction:'new'});
  await settle();
  const previews = env.calls.filter(call => call.url.endsWith('/preview'));
  assert.equal(previews.length,1);
  assert.deepEqual(previews[0].body.chapter_ids,['c1','c2'],'front matter is left out by default');
  assert.equal(previews[0].body.mode,'simple');
  assert.equal(previews[0].body.voice,'Samantha');
  assert.match(env.container.innerHTML,/2 passages · 0 already saved · 2 to record/);
  env.click({performanceMode:'cast'});
  env.click({performanceProvider:'gemini'});
  await settle();
  const cast = env.calls.filter(call => call.url.endsWith('/preview')).at(-1).body;
  assert.equal(cast.mode,'cast');
  assert.equal(cast.provider,'gemini');
  assert.equal(cast.voice,null,'a cast performance takes voices from the cast');
  assert.match(env.container.innerHTML,/>Record performance · about 2 paid requests · cost unknown<\/button>/,'a paid record names its requests and unknown cost');
  env.container.listeners.submit({preventDefault(){}});
  await settle();
  const created = env.calls.find(call => call.url === '/api/books/book-q/performances' && call.method === 'POST');
  assert.equal(created.body.mode,'cast');
  assert.deepEqual(created.body.chapter_ids,['c1','c2']);
  assert.equal(env.jobs.at(-1).id,'job-2');
  assert.match(env.container.innerHTML,/data-performance-action="new">Create performance/,'returns to the list after creating');
  assert.ok(!env.calls.some(call => call.method === 'POST' && call.url.includes('/listen')),'the panel never requests narration directly');
});

test('a preview problem shows its detail and the hint keyed on its code, and blocks creating', async () => {
  const env = environment({problems:[{code:'gemini_key_missing',detail:'No Gemini API key is configured.'},
                                     {code:'something_new',detail:'A condition without a hint.'}]});
  env.api.render(env.container,book,env.options);
  await settle();
  env.click({performanceAction:'new'});
  await settle();
  assert.match(env.container.innerHTML,/No Gemini API key is configured\. Add a Gemini API key in Providers &amp; settings, or choose another narrator\./);
  assert.match(env.container.innerHTML,/<p class="inline-error">A condition without a hint\.<\/p>/);
  assert.doesNotMatch(env.container.innerHTML,/\[object Object\]/);
  assert.match(env.container.innerHTML,/<button type="submit" class="button primary" disabled/);
});

// Models what a browser does: replacing innerHTML makes a fresh chapter list at scrollTop 0 and
// clamps the sheet that scrolls the panel while the content is briefly empty.
class ScrollingContainer extends Container {
  constructor() {
    super();
    this.html = ''; this.scrollTop = 0; this.list = null;
    this.parentElement = {scrollTop:0, parentElement:{scrollTop:0, parentElement:null}};
  }
  get innerHTML() { return this.html; }
  set innerHTML(value) {
    this.html = value; this.list = /performance-chapters"/.test(value) ? {scrollTop:0} : null;
    for (let node = this.parentElement; node; node = node.parentElement) node.scrollTop = 0;
  }
  querySelector(selector) { return selector === '.performance-chapters' ? this.list : null; }
}

test('scroll offsets of the chapter list and its scrolling ancestors survive a check and a preview repaint', async () => {
  const many = {...book, chapters:Array.from({length:40}, (_, i) => ({id:`c${i}`,title:`Chapter ${i}`,kind:'chapter'})),
    segments:Array.from({length:40}, (_, i) => ({id:`s${i}`,chapter_id:`c${i}`}))};
  const container = new ScrollingContainer();
  const env = environment({}, listed, container);
  env.api.render(container, many, env.options);
  await settle();
  env.click({performanceAction:'new'});
  await settle();
  container.list.scrollTop = 620; container.parentElement.scrollTop = 140; container.parentElement.parentElement.scrollTop = 33;
  const before = container.html;
  container.listeners.change({target:{dataset:{performanceChapter:'c30'},checked:false}});
  assert.notEqual(container.html, before, 'the check repainted the form');
  assert.equal(container.list.scrollTop, 620, 'chapter list offset kept on a check');
  assert.equal(container.parentElement.scrollTop, 140, 'sheet offset kept on a check');
  assert.equal(container.parentElement.parentElement.scrollTop, 33);
  await settle();
  assert.equal(env.calls.filter(call => call.url.endsWith('/preview')).length, 2, 'the debounced preview ran');
  assert.equal(container.list.scrollTop, 620, 'chapter list offset kept when the preview lands');
  assert.equal(container.parentElement.scrollTop, 140, 'sheet offset kept when the preview lands');
});

const record = (extra = {}) => ({...listed[0], provider:'system', updated_at:'2026-09-29T10:00:00Z', ...extra,
  progress:{passages_total:2,passages_ready:1,seconds_ready:4,chapters:[{id:'c1',title:'One',passages_total:2,passages_ready:1}], ...extra.progress}});

test('each performance shows one clear status and its chapter coverage', async () => {
  const records = [record({id:'pf_a',job:{id:'j',status:'running',message:'Chapter 1 of 2'}}),
    record({id:'pf_b',job:{id:'j',status:'quota_limited'}}),
    record({id:'pf_c',job:{id:'j',status:'failed',error:'Provider said no'}}),
    record({id:'pf_d',job:{id:'j',status:'completed'},progress:{passages_ready:2,chapters:[{id:'c1',title:'One',passages_total:2,passages_ready:2}]}}),
    record({id:'pf_e',job:null,progress:{passages_ready:0}})];
  const env = environment({}, records);
  env.api.render(env.container,book,env.options);
  await settle();
  const html = env.container.innerHTML;
  const badge = id => html.match(new RegExp(`data-performance="${id}"[\\s\\S]*?class="badge" data-tone="(\\w+)">([^<]+)<`)).slice(1);
  assert.deepEqual(badge('pf_a'),['info','Recording']);
  assert.deepEqual(badge('pf_b'),['warn','Paused · daily limit']);
  assert.deepEqual(badge('pf_c'),['bad','Stopped on an error']);
  assert.deepEqual(badge('pf_d'),['good','Complete']);
  assert.deepEqual(badge('pf_e'),['neutral','Not recorded yet']);
  assert.match(html,/0 of 1 chapter complete/);
  assert.match(html,/1 of 2 passages ready · Chapter 1 of 2/);
  assert.match(html,/>Listen now<\/button>/,'a recording performance can be listened to at once');
});

test('opening a performance adds chapters to it and records the rest, without creating another', async () => {
  const env = environment({}, [record()]);
  env.api.render(env.container,book,env.options);
  await settle();
  env.click({performanceAction:'open',card:'pf_1'});
  await settle();
  const first = env.calls.filter(call => call.url.endsWith('/pf_1/preview'));
  assert.deepEqual(first.at(-1).body,{chapter_ids:[]},'first it plans the performance as it is');
  assert.match(env.container.innerHTML,/1 passage left to record/);
  assert.match(env.container.innerHTML,/>Record the remaining 1 passage<\/button>/);
  assert.match(env.container.innerHTML,/1\/2 ready/,'per-chapter coverage');
  assert.match(env.container.innerHTML,/Two<\/span><small>1 passage · not in this performance/);
  env.container.listeners.change({target:{dataset:{performanceChapter:'c2'},checked:true}});
  await settle();
  assert.deepEqual(env.calls.filter(call => call.url.endsWith('/pf_1/preview')).at(-1).body,{chapter_ids:['c2']});
  assert.match(env.container.innerHTML,/1 chapter to add · 4 passages to record · about 2 requests/);
  assert.match(env.container.innerHTML,/>Add 1 chapter and record<\/button>/);
  env.click({performanceAction:'record-more'});
  await settle();
  const added = env.calls.find(call => call.url === '/api/books/book-q/performances/pf_1/chapters');
  assert.deepEqual(added.body,{chapter_ids:['c2']});
  assert.equal(env.jobs.at(-1).id,'job-4');
  assert.ok(!env.calls.some(call => call.method === 'POST' && call.url === '/api/books/book-q/performances'),'no new performance is created');
  env.click({performanceAction:'back'});
  assert.match(env.container.innerHTML,/data-performance-action="new">Create performance/);
});

test('a paid performance shows its request estimate before it resumes, and a running one cannot take more chapters', async () => {
  const gemini = record({provider:'gemini'});
  const env = environment({}, [gemini]);
  env.api.render(env.container,book,env.options);
  await settle();
  env.click({performanceAction:'open',card:'pf_1'});
  await settle();
  assert.match(env.container.innerHTML,/>Record the remaining 1 passage · about 1 paid request · cost unknown<\/button>/);
  const running = environment({}, [record({job:{id:'j',status:'running',message:'Chapter 1 of 1'}})]);
  running.api.render(running.container,book,running.options);
  await settle();
  running.click({performanceAction:'open',card:'pf_1'});
  await settle();
  const html = running.container.innerHTML;
  assert.match(html,/Listen while it records/);
  assert.match(html,/Playing sends no requests of its own/);
  assert.match(html,/data-performance-chapter="c2"\s+disabled/);
  assert.match(html,/data-performance-action="record-more" disabled/);
  assert.match(html,/Stop recording/);
});

test('resuming a paid performance from the list opens it for review instead of sending requests', async () => {
  const env = environment({}, [record({provider:'gemini'})]);
  env.api.render(env.container,book,env.options);
  await settle();
  assert.doesNotMatch(env.container.innerHTML,/data-performance-action="resume"/);
  assert.match(env.container.innerHTML,/data-performance-action="open">Resume recording/);
  env.click({performanceAction:'open',card:'pf_1'});
  await settle();
  assert.ok(!env.calls.some(call => call.url.endsWith('/prepare')));
  assert.match(env.container.innerHTML,/performance-detail/);
});
