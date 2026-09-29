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

function environment(preview = {}, container = new Container()) {
  const calls = [], played = [], jobs = [];
  const scope = {window:{},document:{activeElement:null},CSS:{escape:value => value},setTimeout:fn => setImmediate(fn),clearTimeout:() => {},
    fetch:async (url, options = {}) => {
      const call = {url,method:options.method || 'GET',body:options.body ? JSON.parse(options.body) : null};
      calls.push(call);
      let data = {};
      if (url === '/api/books/book-q/performances' && call.method === 'GET') data = {performances:listed};
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
  const env = environment({}, container);
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
