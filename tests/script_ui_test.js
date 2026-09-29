// Script & record (bardic/static/script.js): one chapter at a time, the Needs a look chips and counts,
// saves on change (one field per request, failures kept with Try again), bulk speaker assignment with
// partial failure, Analyze's Show in text, and flushing unsaved text on navigation.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const read = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle() { for (let i = 0; i < 12; i++) await tick(); }

function element(extra = {}) {
  const attributes = {};
  return {innerHTML:'', textContent:'', value:'', disabled:false, listeners:{}, dataset:{}, focused:0, scrolled:0,
    addEventListener(name, handler) { this.listeners[name] = handler; },
    setAttribute(name, value) { attributes[name] = String(value); }, removeAttribute(name) { delete attributes[name]; },
    getAttribute(name) { return attributes[name] ?? null; }, attributes,
    querySelector() { return null; }, contains() { return false; },
    focus() { this.focused++; }, scrollIntoView() { this.scrolled++; }, ...extra};
}

function story() {
  return {id:'book-1', revision:3,
    chapters:[{id:'c1', title:'The Quay'}, {id:'c2', title:'The Lamp'}, {id:'c3', title:'The Sea'}],
    characters:[{id:'narrator', name:'Narrator'}, {id:'unassigned', name:'Unassigned dialogue'}, {id:'ada', name:'Ada'}, {id:'ben', name:'Ben'}],
    scenes:[{id:'sc1', chapter_id:'c1', title:'Arrival', direction:'Slow'}, {id:'sc2', chapter_id:'c2', title:'Night'}, {id:'sc3', chapter_id:'c3', title:'Dawn'}],
    passages:[
      {id:'s1', chapter_id:'c1', scene_id:'sc1', kind:'narration', text:'The boat came in.', speaker_id:'narrator', confidence:1, direction:'', cues:[]},
      {id:'s2', chapter_id:'c1', scene_id:'sc1', kind:'dialogue', text:'“Who is there?”', speaker_id:'unassigned', confidence:0, direction:'', cues:[]},
      {id:'s3', chapter_id:'c1', scene_id:'sc1', kind:'dialogue', text:'“Only me.”', speaker_id:'ada', confidence:.65, direction:'', cues:[],
        speaker_check:{source:'booknlp', result:'differs', speaker_id:'ben'}},
      {id:'s4', chapter_id:'c1', scene_id:'sc1', kind:'dialogue', text:'“Good.”', speaker_id:'ben', confidence:.66, direction:'Warm', cues:[],
        manual_fields:['direction'], audio:{url:'/a.wav', duration:3, provider:'system'}},
      {id:'s5', chapter_id:'c2', scene_id:'sc2', kind:'dialogue', text:'“Light it.”', speaker_id:'unassigned', confidence:.1, direction:'', cues:[]},
      {id:'s6', chapter_id:'c2', scene_id:'sc2', kind:'narration', text:'She did.', speaker_id:'narrator', confidence:.2, direction:'', cues:[]},
      {id:'s7', chapter_id:'c3', scene_id:'sc3', kind:'narration', text:'Morning.', speaker_id:'narrator', confidence:1, direction:'', cues:[]},
    ]};
}

// A fake page: nodes by selector, timers under test control, and the app's functions as spies.
function environment({book = story(), patch} = {}) {
  const nodes = new Map();
  const node = selector => { if (!nodes.has(selector)) nodes.set(selector, element()); return nodes.get(selector); };
  const timers = [];
  const calls = {patch:[], applied:0, chapters:[], tabs:[]};
  const docListeners = {};
  const byId = new Map();
  const document = {activeElement:null, addEventListener(name, handler) { docListeners[name] = handler; },
    getElementById:id => byId.get(id) || null};
  const windowListeners = {};
  const scope = {document, console,
    setTimeout:(fn, ms) => { const timer = {fn, ms, done:false}; timers.push(timer); return timer; },
    clearTimeout:timer => { if (timer) timer.done = true; },
    addEventListener(name, handler) { windowListeners[name] = handler; }};
  scope.window = scope;
  vm.createContext(scope);
  vm.runInContext(read('bardic/static/ui.js'), scope);
  vm.runInContext(read('bardic/static/script.js'), scope);
  const state = {book, chapterId:'c1'};
  const api = {$:node, state,
    patch:async (url, body) => {
      calls.patch.push({url, body:plain(body)});
      const result = patch ? await patch(url, body, state.book) : null;
      if (result) return result;
      // The server's answer: the whole book with the one field applied.
      const [, kind, id] = url.match(/\/(passages|scenes)\/([^/]+)$/);
      const next = JSON.parse(JSON.stringify(state.book));
      const item = next[kind].find(entry => entry.id === id);
      Object.assign(item, body);
      // Passages present the fields a person set as `manual_fields` (scenes carry no lock state).
      if (kind === 'passages') item.manual_fields = [...new Set([...(item.manual_fields || []), ...Object.keys(body)])].sort();
      if ('speaker_id' in body) item.confidence = 1;
      next.revision++;
      return next;
    },
    applyBook:next => { calls.applied++; state.book = next; scope.BardicScript.render(); },
    playable:segment => Boolean(segment?.audio?.url),
    setChapter:(id, options) => { calls.chapters.push({id, options:plain(options || {})}); state.chapterId = id; scope.BardicScript.render(); },
    setTab:tab => calls.tabs.push(tab),
    scrollMotion:() => 'auto', formatTime:seconds => `0:0${seconds}`, icon:() => '',
    busy:() => false, paidRender:() => false};
  scope.BardicScript.attach(api);
  const script = scope.BardicScript;
  const runTimers = () => { for (const timer of timers.splice(0)) if (!timer.done && timer.ms < 2000) timer.fn(); };
  const html = () => node('#scene-list').innerHTML;
  const shownRows = () => [...html().matchAll(/data-segment-form="([^"]+)"/g)].map(match => match[1]);
  // Events delegated to #scene-list and #script-review.
  const form = (kind, id) => ({dataset:kind === 'passages' ? {segmentForm:id} : {sceneForm:id}});
  const field = (kind, id, name, value) => ({value, dataset:{scriptField:name}, closest:selector =>
    selector === '[data-segment-form]' && kind === 'passages' ? form(kind, id) : selector === '[data-scene-form]' && kind === 'scenes' ? form(kind, id) : null});
  const change = target => node('#scene-list').listeners.change({target});
  const input = target => node('#scene-list').listeners.input({target});
  const reviewClick = attrs => node('#script-review').listeners.click({target:{closest:selector => {
    const name = selector.slice(1, -1).split('=')[0];
    const key = name.replace(/^data-/, '').replace(/-(\w)/g, (_, c) => c.toUpperCase());
    return key in attrs ? {dataset:{[key]:attrs[key]}} : null;
  }}});
  return {scope, script, state, api, node, calls, timers, runTimers, html, shownRows, field, change, input, reviewClick,
    docListeners, windowListeners, byId, document};
}

test('the pure checks: unassigned, low confidence (≤ 65%, dialogue), BookNLP disagrees, your edits, not recorded', () => {
  const env = environment();
  const {flags, counts} = env.script;
  const book = env.state.book;
  const seg = id => book.passages.find(segment => segment.id === id);
  const playable = segment => Boolean(segment?.audio?.url);
  assert.deepEqual(plain(flags(seg('s1'), book, playable)), ['not-recorded']);
  assert.deepEqual(plain(flags(seg('s2'), book, playable)), ['unassigned', 'low-confidence', 'not-recorded']);
  assert.deepEqual(plain(flags(seg('s3'), book, playable)), ['low-confidence', 'booknlp', 'not-recorded'], '0.65 is low, as in Analyze');
  assert.deepEqual(plain(flags(seg('s4'), book, playable)), ['edited'], '0.66 is not low; recorded');
  assert.deepEqual(plain(flags(seg('s6'), book, playable)), ['not-recorded'], 'narration confidence is not a speaker doubt');
  assert.ok(env.script.flags({...seg('s1'), speaker_id:'gone'}, book, playable).includes('unassigned'), 'a speaker not in the cast is unassigned');
  assert.deepEqual(plain(counts(book, 'c1', playable)), {
    unassigned:{chapter:1, book:2}, 'low-confidence':{chapter:2, book:3}, booknlp:{chapter:1, book:1},
    edited:{chapter:1, book:1}, 'not-recorded':{chapter:3, book:6}});
  const unchecked = {...book, passages:book.passages.map(({speaker_check, ...rest}) => rest)};
  assert.ok(!('booknlp' in counts(unchecked, 'c1', playable)), 'no BookNLP chip without BookNLP checks');
});

test('a scene lists the passages its passage_ids name, even when a passage carries no scene_id', () => {
  const book = story();
  book.passages.push({id:'s8', chapter_id:'c1', kind:'narration', text:'The rope.', speaker_id:'narrator', confidence:1, direction:'', cues:[]});
  book.scenes[0].passage_ids = ['s1', 's2', 's3', 's4', 's8'];
  const env = environment({book});
  env.script.render();
  const html = env.html();
  const sceneAt = html.indexOf('data-scene-form="sc1"');
  assert.ok(sceneAt >= 0 && html.indexOf('data-segment-form="s8"') > sceneAt, 'the passage listed by the scene appears under it');
  assert.deepEqual(env.shownRows(), ['s1', 's2', 's3', 's4', 's8']);
});

test('one chapter at a time, with the chapter menu and previous/next', () => {
  const env = environment();
  env.script.render();
  assert.deepEqual(env.shownRows(), ['s1', 's2', 's3', 's4']);
  assert.match(env.node('#script-meta').textContent, /1 scene · 4 passages/);
  assert.match(env.node('#studio-chapter').innerHTML, /<option value="c1" selected>The Quay<\/option>/);
  assert.equal(env.node('#script-previous-chapter').disabled, true);
  assert.equal(env.node('#script-next-chapter').disabled, false);
  env.node('#script-next-chapter').listeners.click();
  assert.deepEqual(env.calls.chapters.map(call => call.id), ['c2'], 'next goes through setChapter');
  assert.deepEqual(env.shownRows(), ['s5', 's6']);
  assert.match(env.html(), /Passage 1|passage 1/);
  env.node('#script-next-chapter').listeners.click();
  assert.equal(env.state.chapterId, 'c3');
  assert.equal(env.node('#script-next-chapter').disabled, true);
  env.node('#script-previous-chapter').listeners.click();
  assert.equal(env.state.chapterId, 'c2');
});

test('Needs a look chips filter the chapter (any chip on), with chapter and whole-book counts', () => {
  const env = environment();
  env.script.render();
  const review = () => env.node('#script-review').innerHTML;
  assert.match(review(), /Unassigned speaker · 1/);
  assert.match(review(), /aria-label="Unassigned speaker: 1 passage in this chapter, 2 in the book"/);
  assert.match(review(), /In the whole book: 2 unassigned · 3 low confidence · 1 BookNLP disagrees · 1 your edits · 6 not recorded\./);
  env.reviewClick({scriptFilter:'unassigned'});
  assert.deepEqual(env.shownRows(), ['s2']);
  assert.match(review(), /aria-pressed="true"[^>]*data-value="unassigned"/);
  assert.match(review(), /Showing 1 of 4 passages in this chapter\./);
  assert.match(env.node('#studio-chapter').innerHTML, /The Quay · 1 to check/, 'the chapter menu shows where the work is');
  assert.match(env.node('#studio-chapter').innerHTML, /The Lamp · 1 to check/);
  assert.match(review(), /data-script-chapter="c2"/, 'a link to the next chapter with matches');
  env.reviewClick({scriptFilter:'booknlp'});
  assert.deepEqual(env.shownRows(), ['s2', 's3'], 'chips combine as "any of"');
  env.reviewClick({scriptFilter:'unassigned'});
  env.reviewClick({scriptFilter:'booknlp'});
  env.reviewClick({scriptFilter:'edited'});
  assert.deepEqual(env.shownRows(), ['s4']);
  env.reviewClick({scriptFilter:'not-recorded'});
  assert.deepEqual(env.shownRows(), ['s1', 's2', 's3', 's4']);
  env.reviewClick({scriptClear:''});
  assert.deepEqual(env.shownRows(), ['s1', 's2', 's3', 's4']);
  assert.doesNotMatch(review(), /Showing/);
  env.reviewClick({scriptFilter:'low-confidence'});
  assert.deepEqual(env.shownRows(), ['s2', 's3']);
  env.reviewClick({scriptChapter:'c2'});
  assert.equal(env.state.chapterId, 'c2');
  assert.deepEqual(env.shownRows(), ['s5'], 'the chips stay on in the next chapter');
});

test('a speaker saves on change: one field, no Save button, the row stays in view and says Saved', async () => {
  const env = environment();
  env.script.render();
  assert.doesNotMatch(env.html(), />Save</, 'no per-row or per-scene Save');
  env.reviewClick({scriptFilter:'unassigned'});
  // Arrowing through the menu fires change per option; only the final choice is sent.
  env.change(env.field('passages', 's2', 'speaker_id', 'ben'));
  env.change(env.field('passages', 's2', 'speaker_id', 'ada'));
  await settle();
  assert.equal(env.calls.patch.length, 0);
  env.runTimers();
  await settle();
  assert.deepEqual(env.calls.patch, [{url:'/api/books/book-1/passages/s2', body:{speaker_id:'ada'}}], 'only the speaker is sent, once');
  assert.equal(env.calls.applied, 1);
  assert.equal(env.state.book.passages.find(segment => segment.id === 's2').speaker_id, 'ada');
  assert.deepEqual(env.shownRows(), ['s2'], 'a fixed row stays until the chips change');
  assert.equal(env.node('#script-status-passages-s2').textContent, 'Saved');
  assert.equal(env.node('#script-status-passages-s2').getAttribute('data-tone'), 'good');
  assert.match(env.node('#script-review').innerHTML, /Unassigned speaker · 0/);
  assert.equal(env.script.hasUnsaved(), false);
  // The same value again sends nothing (an unchanged field never becomes locked).
  env.change(env.field('passages', 's2', 'speaker_id', 'ada'));
  env.runTimers();
  await settle();
  assert.equal(env.calls.patch.length, 1);
  // Leaving the menu saves at once, without waiting.
  const menu = env.field('passages', 's3', 'speaker_id', 'ben');
  env.change(menu);
  env.node('#scene-list').listeners.focusout({target:menu});
  await settle();
  assert.deepEqual(env.calls.patch[1], {url:'/api/books/book-1/passages/s3', body:{speaker_id:'ben'}});
});

test('a performance note saves after a pause, sends only the note, and keeps other fields and their locks', async () => {
  const env = environment();
  env.script.render();
  env.input(env.field('passages', 's3', 'direction', 'Quietly'));
  env.input(env.field('passages', 's3', 'direction', 'Quietly, afraid'));
  await settle();
  assert.equal(env.calls.patch.length, 0, 'typing does not send each keystroke');
  assert.match(env.node('#script-save-status').textContent, /save when you pause/);
  env.script.render();
  assert.match(env.html(), /id="segment-direction-s3"[^>]*value="Quietly, afraid"/, 'unsaved text survives a re-render');
  env.runTimers();
  await settle();
  assert.deepEqual(env.calls.patch, [{url:'/api/books/book-1/passages/s3', body:{direction:'Quietly, afraid'}}]);
  const saved = env.state.book.passages.find(segment => segment.id === 's3');
  assert.equal(saved.speaker_id, 'ada', 'the speaker was not sent, so it is not re-locked');
  assert.equal(saved.confidence, .65, 'nor is its confidence reset');
  // Scene direction: the same model.
  env.change(env.field('scenes', 'sc1', 'direction', 'Slow, then urgent'));
  await settle();
  assert.deepEqual(env.calls.patch[1], {url:'/api/books/book-1/scenes/sc1', body:{direction:'Slow, then urgent'}});
  assert.equal(env.node('#script-status-scenes-sc1').textContent, 'Saved');
  // Leaving a field unchanged sends nothing.
  env.change(env.field('passages', 's4', 'direction', 'Warm'));
  await settle();
  assert.equal(env.calls.patch.length, 2);
});

test('a failed save stays on its row with the chosen value and Try again; nothing is lost', async () => {
  let fail = true;
  const env = environment({patch:async () => { if (fail) throw new Error('A job is already working on this book.'); return null; }});
  env.script.render();
  env.change(env.field('passages', 's2', 'speaker_id', 'ben'));
  env.runTimers();
  await settle();
  const status = env.node('#script-status-passages-s2');
  assert.equal(status.textContent, 'Not saved: A job is already working on this book.');
  assert.equal(status.getAttribute('data-tone'), 'bad');
  assert.equal(status.getAttribute('role'), 'alert');
  assert.equal(env.script.hasUnsaved(), true);
  assert.match(env.node('#script-save-status').textContent, /1 change not saved/);
  assert.match(env.html(), /<option value="ben" selected>Ben<\/option>/, 'the row keeps your choice');
  assert.match(env.html(), /data-script-retry="passages:s2"/);
  fail = false;
  env.node('#scene-list').listeners.click({target:{closest:selector => selector === '[data-script-retry]' ? {dataset:{scriptRetry:'passages:s2'}} : null}});
  await settle();
  assert.equal(env.calls.patch.length, 2);
  assert.deepEqual(env.calls.patch[1].body, {speaker_id:'ben'});
  assert.equal(env.script.hasUnsaved(), false);
  assert.equal(env.state.book.passages.find(segment => segment.id === 's2').speaker_id, 'ben');
  assert.doesNotMatch(env.html(), /data-script-retry/);
});

test('saves run one at a time, in order', async () => {
  const order = [];
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const env = environment({patch:async (url, body) => { order.push(`start ${Object.keys(body)[0]}`); if ('speaker_id' in body) await gate; order.push(`end ${Object.keys(body)[0]}`); return null; }});
  env.script.render();
  env.change(env.field('passages', 's2', 'speaker_id', 'ada'));
  env.runTimers();
  env.change(env.field('passages', 's1', 'direction', 'Low'));
  await settle();
  assert.deepEqual(order, ['start speaker_id']);
  release();
  await settle();
  assert.deepEqual(order, ['start speaker_id', 'end speaker_id', 'start direction', 'end direction']);
});

test('bulk assign: selected passages, one edit each in order, unchanged ones skipped, partial failure reported', async () => {
  const env = environment({patch:async url => { if (url.endsWith('/s3')) throw new Error('Choose a character in this book’s cast'); return null; }});
  env.script.render();
  const review = env.node('#script-review');
  // Select shown selects the four rows of this chapter; the speaker menu enables Assign.
  review.listeners.change({target:{id:'script-select-shown', checked:true}});
  assert.match(review.innerHTML, /4 selected/);
  assert.match(review.innerHTML, /data-script-assign=""[^>]*disabled|disabled[^>]*data-script-assign/, 'no speaker chosen yet');
  review.listeners.change({target:{id:'script-bulk-speaker', value:'ben'}});
  assert.match(review.innerHTML, />Assign to 4 passages</);
  env.reviewClick({scriptAssign:''});
  await settle();
  assert.deepEqual(env.calls.patch.map(call => [call.url.split('/').pop(), call.body.speaker_id]), [['s1', 'ben'], ['s2', 'ben'], ['s3', 'ben']],
    's4 already has Ben, so it is not sent');
  assert.equal(env.calls.applied, 1, 'the book is applied once, at the end');
  assert.equal(env.state.book.passages.find(segment => segment.id === 's2').speaker_id, 'ben');
  const message = env.node('#script-bulk-status');
  assert.match(message.textContent, /^3 of 4 passages assigned to Ben\. 1 passage did not save \(Choose a character/);
  assert.equal(message.getAttribute('data-tone'), 'bad');
  assert.match(review.innerHTML, /1 selected/, 'the failed passage stays selected so Assign retries it');
  assert.match(env.html(), /id="script-pick-s3"[^>]*checked/);
  assert.match(env.node('#script-status-passages-s3').textContent, /^Not saved/);
  assert.match(env.node('#script-status-passages-s1').textContent, /Saved · Ben/);
});

test('bulk text for a clean run and while running', () => {
  const {bulkText} = environment().script;
  assert.deepEqual(plain(bulkText({running:false, total:2, saved:2, unchanged:0, failed:[], speaker:'Ada'})), {text:'2 passages now have Ada as the speaker.', tone:'good'});
  assert.deepEqual(plain(bulkText({running:true, total:5, done:2, saved:2, unchanged:0, failed:[], speaker:'Ada'})), {text:'Assigning 2 of 5 passages to Ada…', tone:'info'});
});

test('Show in text opens Script & record at the passage: preventDefault, chapter, tab, scroll and focus, chips kept', () => {
  const env = environment();
  env.script.render();
  env.reviewClick({scriptFilter:'unassigned'});
  const row = element(), select = element();
  env.byId.set('script-row-s5', row);
  env.byId.set('speaker-s5', select);
  let prevented = 0;
  const handled = env.docListeners['bardic:show-passage']({detail:{bookId:'book-1', segmentId:'s5', chapterId:'c2'}, preventDefault() { prevented++; }});
  assert.equal(handled, true);
  assert.equal(prevented, 1, 'shell.js does not fall back to Read & listen');
  assert.deepEqual(env.calls.chapters, [{id:'c2', options:{scroll:false}}]);
  assert.deepEqual(env.calls.tabs, ['studio']);
  assert.equal(row.scrolled, 1);
  assert.equal(select.focused, 1, 'the speaker menu takes focus, ready for the fix');
  assert.match(env.html(), /class="segment-row script-row is-target"[^>]*id="script-row-s5"/);
  assert.match(env.node('#script-review').innerHTML, /aria-pressed="true"[^>]*data-value="unassigned"/, 'Needs a look is unchanged');
  // A passage that no chip matches is still shown when opened.
  env.docListeners['bardic:show-passage']({detail:{bookId:'book-1', segmentId:'s6', chapterId:'c2'}, preventDefault() { prevented++; }});
  assert.deepEqual(env.shownRows(), ['s5', 's6']);
  // Another book, or an unknown passage, is left to the fallback.
  assert.equal(env.docListeners['bardic:show-passage']({detail:{bookId:'other', segmentId:'s1'}, preventDefault() { prevented++; }}), false);
  assert.equal(env.docListeners['bardic:show-passage']({detail:{bookId:'book-1', segmentId:'nope'}, preventDefault() { prevented++; }}), false);
  assert.equal(prevented, 2);
});

test('navigation never loses an edit: a chapter change saves waiting text now, and leaving asks first', async () => {
  const env = environment();
  env.script.render();
  env.input(env.field('passages', 's1', 'direction', 'Softly'));
  env.node('#script-next-chapter').listeners.click();
  await settle();
  assert.deepEqual(env.calls.patch, [{url:'/api/books/book-1/passages/s1', body:{direction:'Softly'}}], 'saved at once, not after the pause');
  assert.equal(env.state.chapterId, 'c2');
  // The chapter menu (app.js's setChapter) also saves first.
  env.input(env.field('passages', 's5', 'direction', 'Hushed'));
  env.node('#studio-chapter').listeners.change();
  await settle();
  assert.deepEqual(env.calls.patch[1].body, {direction:'Hushed'});
  // Leaving the page with an unsaved (for example failed) change asks first.
  const leave = {returnValue:undefined, prevented:0, preventDefault() { this.prevented++; }};
  env.windowListeners.beforeunload(leave);
  assert.equal(leave.prevented, 0, 'nothing unsaved: no prompt');
  env.input(env.field('passages', 's6', 'direction', 'Quick'));
  env.windowListeners.beforeunload(leave);
  assert.equal(leave.prevented, 1);
  assert.equal(leave.returnValue, '');
  await settle();
  assert.deepEqual(env.calls.patch[2].body, {direction:'Quick'}, 'and saves what it can');
});

test('Enter in a note saves that row now (the row form submit)', async () => {
  const env = environment();
  env.script.render();
  const inputField = {value:'Loud', dataset:{scriptField:'direction'}};
  const speaker = {value:'ada', dataset:{scriptField:'speaker_id'}};
  await env.script.submit({dataset:{segmentForm:'s3'}, elements:[speaker, inputField]});
  assert.deepEqual(env.calls.patch, [{url:'/api/books/book-1/passages/s3', body:{direction:'Loud'}}]);
});

test('rows are compact: no Save buttons, Hear example visible, Record and play in the More menu', () => {
  const env = environment();
  env.script.render();
  const html = env.html();
  assert.equal((html.match(/data-preview-speaker=/g) || []).length, 4);
  assert.match(html, /<details class="script-row-more"[^>]*><summary[^>]*>More<\/summary><div class="script-row-menu"><button[^>]*data-render-segment="s1"[^>]*>Record<\/button><\/div>/);
  assert.match(html, /data-render-segment="s4"[^>]*>Record again<\/button><button[^>]*data-play-segment="s4"/);
  assert.match(html, /Recorded · 0:03 · system/);
  assert.match(html, /Low confidence · 65%/);
  assert.match(html, /BookNLP disagrees · Ben/);
  assert.match(html, /Your note/);
  assert.match(html, /data-render-scene="sc1"[^>]*> Narrate scene/, 'Narrate scene keeps its estimate→confirm handler in app.js');
  assert.match(html, /data-render-confirm-host="sc1"/);
  assert.doesNotMatch(html, /type="submit"/);
});

test('app.js and index.html wire the script: one script tag, attach, render and the row submit', () => {
  const app = read('bardic/static/app.js');
  const html = read('bardic/static/index.html');
  assert.ok(html.indexOf('/static/script.js') > 0 && html.indexOf('/static/script.js') < html.indexOf('/static/app.js'), 'script.js loads before app.js');
  for (const id of ['script-review', 'script-previous-chapter', 'script-next-chapter', 'studio-chapter', 'scene-list']) assert.ok(html.includes(`id="${id}"`), id);
  assert.match(app, /window\.BardicScript\?\.attach\(\{\$, state, patch, applyBook, playable, setChapter, setTab/);
  assert.match(app, /function renderStudio\(\) \{\n[^\n]*\n  window\.BardicScript\?\.render\(\);/);
  assert.match(app, /\$\('#scene-list'\)\.addEventListener\('submit', event => \{ event\.preventDefault\(\); void window\.BardicScript\?\.submit\(event\.target\); \}\);/);
  assert.doesNotMatch(app, /saveEditor\(form,\s*'(?:segments|passages)'|saveEditor\(form,\s*'scenes'/);
});
