/* Gemini content-policy blocks in the UI: their own states in the performances panel and the listening status. */
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
const book = {id:'book-q', title:'The Lantern', chapters:[{id:'c1', title:'One', kind:'chapter'}], passages:[{id:'s1', chapter_id:'c1'}]};
const source = name => fs.readFileSync(path.join(__dirname, '../bardic/static', name), 'utf8');

async function render(records) {
  const container = new Container();
  const scope = {window:{}, document:{activeElement:null}, CSS:{escape:value => value}, setTimeout:(fn, ms) => { if (!(ms >= 1000)) setImmediate(fn); return 0; }, clearTimeout:() => {},
    fetch:async () => ({ok:true, status:200, json:async () => ({performances:records})})};
  vm.createContext(scope);
  vm.runInContext(source('ui.js'), scope);
  vm.runInContext(source('performances.js'), scope);
  scope.window.BardicPerformances.render(container, book, {listen:{getPerformance:() => null}, onJob() {}, onPlay() {}});
  await settle();
  return container.innerHTML;
}

test('Gemini content-policy blocks show as their own states, never as plain complete or failed', async () => {
  const progress = extra => ({passages_total:10, passages_ready:10, seconds_ready:60, passages_fallback:0, passages_blocked:0, fallback_provider:null,
    chapters:[{id:'c1', title:'One', passages_total:10, passages_ready:10, passages_fallback:0, passages_blocked:0, blocked_passage_ids:[]}], ...extra});
  const record = (id, extra) => ({id, name:id, mode:'simple', chapter_ids:['c1'], narrator_label:'Kore · Gemini', chapters_added:[], job:{id:'j', kind:'performance', status:'completed'}, progress:progress(extra)});
  const html = await render([
    record('pf_plain', {}),
    record('pf_fallback', {passages_fallback:4, fallback_provider:'system'}),
    record('pf_blocked', {passages_ready:7, passages_blocked:3}),
    {...record('pf_error', {passages_ready:2}), job:{id:'j', kind:'performance', status:'failed', error:'Gemini blocked it.', error_code:'content_blocked'}},
  ]);
  const card = id => html.split('<article').find(part => part.includes(`data-performance="${id}"`));
  assert.match(card('pf_plain'), /data-state="complete"/);
  assert.match(card('pf_fallback'), /data-state="read_by_fallback"/);
  assert.match(card('pf_fallback'), /4 passages read by a Mac voice because Gemini blocked them/);
  assert.match(card('pf_blocked'), /data-state="blocked"/);
  assert.match(card('pf_blocked'), /3 passages blocked by Gemini’s content policy and not recorded/);
  assert.doesNotMatch(card('pf_blocked'), /Resume recording/, 'nothing more can be recorded');
  assert.match(card('pf_error'), /data-state="failed"/);
  assert.match(card('pf_error'), /Record with One narrator instead/, 'the hint is keyed on the error code');
});

test('the status tones for the new states come from the one map', () => {
  const ui = {};
  vm.runInNewContext(source('ui.js'), ui);
  assert.equal(ui.BardicUI.statusTone('read_by_fallback'), 'warn');
  assert.equal(ui.BardicUI.statusTone('blocked'), 'warn');
});

test('a chapter Gemini partly blocked says how each passage ended', () => {
  const scope = {};
  vm.runInNewContext(source('listen-status.js'), scope);
  const {compute} = scope.BardicListenStatus;
  const base = {mode:'simple', available:true, readyHere:true, aheadSeconds:0, rate:1};
  const job = (status, extra = {}) => ({id:'job-1', kind:'listen_chapter', status, chapter_id:'c1', fallback:null, content_blocked:null, ...extra});
  const blocked = {content_blocked:{fallback:{session_id:'f'.repeat(64), provider:'system', model:'macos-say', voice:''}, fallback_passage_ids:['a', 'b'], blocked_passage_ids:['c'], fallback_error:null}};
  const ready = compute({...base, started:true, remainingSeconds:0, job:job('completed', blocked)});
  assert.equal(ready.state, 'paused');
  assert.match(ready.detail, /2 passages read by a device voice because Gemini blocked them\./);
  assert.match(ready.detail, /1 passage blocked by Gemini’s content policy and not recorded\./);
  assert.equal(compute({...base, job:job('completed')}).detail, '');
  const failed = compute({...base, remainingSeconds:5, job:job('failed', {error:'Blocked.', error_code:'content_blocked'})});
  assert.equal(failed.state, 'failed');
  assert.match(failed.detail, /Blocked\. Try another narrator/);
});
