// Page glue (bardic/static/shell.js): step routes into Analyze, the lifecycle strip's Next opening a
// step, and the Show in text fallback to Read & listen. A small fake DOM; no network.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const STATIC = path.join(__dirname, '../bardic/static');

class Node {
  constructor(props = {}) { Object.assign(this, {dataset:{}, listeners:{}, value:'', ...props}); }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  dispatchEvent(event) { for (const fn of this.listeners[event.type] || []) fn(event); return true; }
  querySelector() { return null; }
  closest() { return null; }
}

function setup({chapterId = 'c1', steps = []} = {}) {
  const log = [];
  const passages = [];
  const renderChapter = id => {
    passages.length = 0;
    for (const segment of book.passages.filter(item => item.chapter_id === id)) {
      passages.push(new Node({dataset:{segment:segment.id}, scrollIntoView:options => log.push(['scroll', segment.id, options.block]), focus:() => log.push(['focus', segment.id])}));
    }
  };
  const book = {id:'b1', title:'Quay', passages:[{id:'s1', chapter_id:'c1'}, {id:'s2', chapter_id:'c1'}, {id:'s9', chapter_id:'c2'}], chapters:[{id:'c1'}, {id:'c2'}]};
  const state = {book, books:[book], tab:'read', chapterId, libraryView:false};
  const picker = new Node();
  picker.addEventListener('change', () => { log.push(['chapter', picker.value]); state.chapterId = picker.value; renderChapter(picker.value); });
  renderChapter(chapterId);
  const nodes = {'#reader-chapter':picker, '#book-lifecycle':new Node()};
  const timers = [];
  const document = new Node({readyState:'complete', activeElement:null,
    querySelector:selector => nodes[selector] || null,
    querySelectorAll:selector => selector === '#reader-text [data-segment]' ? [...passages] : []});
  const window = {
    BardicApp:{state, setTab:tab => { log.push(['tab', tab]); state.tab = tab; }, scrollMotion:() => 'auto', selectBook:async id => log.push(['book', id])},
    BardicAnalysisPipeline:{selectStep:step => { steps.push(step); return true; }},
    addEventListener() {},
  };
  class Event { constructor(type, init = {}) { this.type = type; this.bubbles = Boolean(init.bubbles); } }
  const scope = {window, document, Event, location:{hash:''}, history:{replaceState() {}, pushState() {}},
    localStorage:{getItem:() => null, setItem() {}}, setTimeout:fn => timers.push(fn), console, MutationObserver:undefined};
  vm.runInNewContext(fs.readFileSync(path.join(STATIC, 'shell.js'), 'utf8'), scope);
  const flush = () => { while (timers.length) timers.shift()(); };
  return {shell:window.BardicShell, document, log, state, steps, flush, passages};
}

test('#/book/<id>/analysis/<step> routes carry the step; other tabs ignore extra parts', () => {
  const {shell} = setup();
  assert.deepEqual({...shell.parse('#/book/b1/analysis/profiles')}, {book:'b1', tab:'analysis', step:'profiles'});
  assert.deepEqual({...shell.parse('#/book/b1/analysis')}, {book:'b1', tab:'analysis'});
  assert.deepEqual({...shell.parse('#/book/b1/cast/extra')}, {book:'b1', tab:'cast'});
  assert.deepEqual({...shell.parse('#/book/b%2F1/nope')}, {book:'b/1', tab:'read'});
});

test('the strip\'s Next opens Analyze on its step', () => {
  const env = setup();
  env.shell.go('analysis', undefined, 'profiles');
  assert.deepEqual([...env.steps], ['profiles'], 'the step is asked for before the tab shows');
  assert.deepEqual(env.log.at(-1), ['tab', 'analysis']);
  env.shell.go('cast', undefined, 'profiles');
  assert.deepEqual([...env.steps], ['profiles'], 'only Analyze takes a step');
});

test('Show in text opens Read & listen at a passage in the chapter on screen without moving your place', () => {
  const env = setup();
  env.document.dispatchEvent({type:'bardic:show-passage', detail:{bookId:'b1', segmentId:'s2', chapterId:'c1'}, defaultPrevented:false});
  assert.deepEqual(env.log, [], 'it waits until every listener has had its turn');
  env.flush();
  assert.deepEqual(env.log, [['tab', 'read'], ['scroll', 's2', 'center'], ['focus', 's2']]);
});

test('Show in text in another chapter picks that chapter in the reader first', () => {
  const env = setup();
  env.document.dispatchEvent({type:'bardic:show-passage', detail:{bookId:'b1', segmentId:'s9', chapterId:'c2'}, defaultPrevented:false});
  env.flush();
  assert.deepEqual(env.log, [['tab', 'read'], ['chapter', 'c2'], ['scroll', 's9', 'center'], ['focus', 's9']]);
});

test('a view that handles Show in text itself cancels the fallback; other books and passages are ignored', () => {
  const env = setup();
  const handled = {type:'bardic:show-passage', detail:{bookId:'b1', segmentId:'s2'}, defaultPrevented:false};
  env.document.dispatchEvent(handled);
  handled.defaultPrevented = true;   // Script & record called preventDefault()
  env.document.dispatchEvent({type:'bardic:show-passage', detail:{bookId:'other', segmentId:'s2'}, defaultPrevented:false});
  env.document.dispatchEvent({type:'bardic:show-passage', detail:{bookId:'b1', segmentId:'gone'}, defaultPrevented:false});
  env.flush();
  assert.deepEqual(env.log, []);
});
