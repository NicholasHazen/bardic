// Exercise actual workspace navigation without starting narration or cloud work.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../bardic/static/app.js'), 'utf8');

function between(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a + start.length);
  assert.ok(a >= 0 && b > a, `${start} boundaries`);
  return source.slice(a, b);
}

function environment(respond) {
  const nodes = new Map();
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {
      dataset:{}, attributes:new Map(), classes:new Set(), listeners:{}, value:'', hidden:false,
      classList:{toggle(name, on) { const classes = nodes.get(selector).classes; if (on) classes.add(name); else classes.delete(name); }},
      setAttribute(name, value) { this.attributes.set(name, value); },
      removeAttribute(name) { this.attributes.delete(name); },
      addEventListener(name, handler) { this.listeners[name] = handler; },
      focus() { this.focused = true; }, scrollIntoView() { this.scrolled = true; },
      closest() { return this.details || null; }, showModal() { this.open = true; },
    });
    return nodes.get(selector);
  };
  const books = [
    {id:'one', title:'The <Lantern>', author:'A & B', chapters:[{id:'c1', title:'First'}], segments:[], characters:[]},
    {id:'two', title:'Winter road', author:'Hannah Snow', chapters:[{id:'c2', title:'Second'}], segments:[], characters:[]},
  ];
  const state = {book:books[0], books, chapterId:'c1', segmentId:'s1', tab:'read', libraryView:false, selectionVersion:0, loading:false, referenceCache:new Map(), referenceVersion:0};
  const tabs = ['read','cast','studio'].map(name => { const tab = node(`#${name}-tab`); tab.dataset.tab = name; tab.setAttribute('aria-current', 'false'); return tab; });
  const libraryItems = books.map(book => { const item = node(`[data-book="${book.id}"]`); item.dataset.book = book.id; return item; });
  const calls = {requests:0, stops:0, saves:0};
  const media = {plays:0, generations:0, paused:true, pause(){ this.paused = true; }, async play(){ this.plays++; }};
  const context = {state, document:{title:''}, window:{scrollTo(){}, BardicListen:{isSimple:() => false, resolve:() => null, stop(){}, prepare(){ media.generations++; }}}, $:node,
    $$:selector => selector === '.tab' ? tabs : selector === '.library-item' ? libraryItems : [],
    icon:() => '', clearTimeout,
    request:async url => { calls.requests++; if (respond) return respond(url); throw new Error('Navigation should not request content'); },
    stopAudio:() => calls.stops++, saveProgress:() => calls.saves++,
    safeRead:(_key, fallback) => fallback, progressKey:() => 'synthetic-progress', pollJobs:async() => {},
    audio:media, clearListeningPreloads(){}, updateHighlight(){},
    renderReader(){}, renderCast(){}, renderStudio(){}, renderJob(){}, updatePlayer(){},
    renderProduction(){ node('#progressive-production').rendered = true; },
    toast(message) { node('#toast').textContent = message; },
    clearKeyInputs(){}, fillSettings(){}, renderAccountCheck(){}, updateSettingsControls(){},
    cloudProviders:['gemini','openai','anthropic'], providerField:(name, provider) => node(`#${name}-${provider}`),
  };
  vm.runInNewContext([
    'let playGeneration = 0, preparingListen = false, previewEnhanced = false, mediaBuffering = false;',
    source.split('\n').find(line => line.startsWith('const escapeHTML =')),
    source.split('\n').filter(line => /^const (segmentById|playable|simpleActive|listeningAudio) =/.test(line)).join('\n'),
    between('function renderLibrary(', 'async function refreshStatus('),
    between('async function selectBook(', 'function applyBook('),
    between('function renderBook(', 'function renderReader('),
    between('function setTab(', 'function setChapter('),
    between('async function startSegment(', 'async function togglePlayback('),
    between('function openSettings(', 'function openImport('),
    between('// Navigation and delegated editor actions.', "$('#previous-chapter').addEventListener"),
    source.split('\n').find(line => line.startsWith("$('#analyze-button').addEventListener")),
    'globalThis.workspace = {renderLibrary,renderBook,selectBook,setTab,showLibrary,navigateTabs,openListeningSettings,openSettings,startSegment};',
  ].join('\n'), context);
  return {state, node, calls, media, tabs, libraryItems, workspace:context.workspace, document:context.document};
}

test('library search matches titles and authors, escapes content, and explains no matches', () => {
  const {workspace, node} = environment();
  workspace.renderLibrary();
  assert.match(node('#library-list').innerHTML, /The &lt;Lantern&gt;/);
  assert.match(node('#home-books').innerHTML, /A &amp; B/);
  assert.equal(node('#home-library').hidden, false);
  node('#library-search').value = '  SNOW ';
  node('#library-search').listeners.input();
  assert.match(node('#library-list').innerHTML, /Winter road/);
  assert.doesNotMatch(node('#library-list').innerHTML, /Lantern/);
  assert.equal(node('#library-search-status').textContent, '1 book found');
  node('#library-search').value = '<unknown>';
  node('#library-search').listeners.input();
  assert.match(node('#library-list').innerHTML, /No books match “&lt;unknown&gt;”/);
  assert.equal(node('#library-search-status').textContent, '0 books found');
});

test('bookshelf and sidebar share search results and clearing search restores the whole library', () => {
  const {workspace, node} = environment();
  workspace.showLibrary();
  node('#library-search').value = 'SNOW';
  node('#library-search').listeners.input();
  assert.match(node('#home-books').innerHTML, /Winter road/);
  assert.doesNotMatch(node('#home-books').innerHTML, /Lantern/);
  assert.equal(node('#home-library-count').textContent, '1 of 2 books');
  node('#library-search').value = '<missing>';
  node('#library-search').listeners.input();
  assert.equal(node('#home-library').hidden, false);
  assert.match(node('#home-books').innerHTML, /No books match “&lt;missing&gt;”/);
  assert.equal(node('#home-library-count').textContent, '0 of 2 books');
  node('#library-search').value = '';
  node('#library-search').listeners.input();
  assert.match(node('#home-books').innerHTML, /Lantern/);
  assert.match(node('#home-books').innerHTML, /Winter road/);
  assert.equal(node('#home-library-count').textContent, '2 books');
});

test('home and reopening the selected book preserve listening and editor state', async () => {
  const {workspace, node, state, calls, libraryItems, document} = environment();
  workspace.setTab('cast');
  const book = state.book;
  node('#cast-grid').unsavedDraft = 'Keep this direction';
  let prevented = false;
  node('.brand').listeners.click({preventDefault(){ prevented = true; }});
  assert.equal(prevented, true);
  assert.equal(state.book, book);
  assert.equal(state.segmentId, 's1');
  assert.equal(node('#welcome').hidden, false);
  assert.equal(node('#book-workspace').hidden, true);
  assert.equal(node('#player').hidden, false);
  assert.equal(node('#library-home').attributes.get('aria-current'), 'page');
  assert.equal(document.title, 'Bardic — Your library');
  assert.equal(node('#welcome h1').focused, true);
  await workspace.selectBook('one');
  assert.equal(state.tab, 'cast');
  assert.equal(node('#cast-grid').unsavedDraft, 'Keep this direction');
  assert.equal(node('#welcome').hidden, true);
  assert.equal(node('#book-workspace').hidden, false);
  assert.equal(libraryItems[0].attributes.get('aria-current'), 'true');
  assert.equal(libraryItems[1].attributes.has('aria-current'), false);
  assert.deepEqual(calls, {requests:0, stops:0, saves:0});
});

test('background book rendering keeps Library home visible and empty libraries have no shelf', () => {
  const {workspace, node, state} = environment();
  workspace.showLibrary();
  workspace.renderBook();
  assert.equal(state.libraryView, true);
  assert.equal(node('#welcome').hidden, false);
  assert.equal(node('#book-workspace').hidden, true);
  state.book = null; state.books = [];
  workspace.renderBook();
  assert.equal(node('#player').hidden, true);
  assert.equal(node('#home-library').hidden, true);
  assert.match(node('#library-list').innerHTML, /Your next great listen starts here/);
});

test('failed book opening keeps Home visible and the previous book can be reopened', async () => {
  const {workspace, state, node} = environment(async() => { throw new Error('Temporary connection failure'); });
  workspace.showLibrary();
  const pending = workspace.selectBook('two');
  assert.equal(state.libraryView, true, 'Keep the current destination while loading');
  await pending;
  assert.equal(state.book.id, 'one');
  assert.equal(state.loading, false);
  assert.equal(state.libraryView, true);
  assert.equal(node('#welcome').hidden, false);
  assert.match(node('#toast').textContent, /Temporary connection failure/);
  await workspace.selectBook('one');
  assert.equal(node('#welcome').hidden, true);
  assert.equal(node('#book-workspace').hidden, false);
});

test('reopening the selected book supersedes an unfinished request for another book', async () => {
  let finish;
  const {workspace, state, node} = environment(() => new Promise(resolve => { finish = resolve; }));
  workspace.showLibrary();
  const pending = workspace.selectBook('two');
  await workspace.selectBook('one');
  finish(state.books[1]);
  await pending;
  assert.equal(state.book.id, 'one');
  assert.equal(state.loading, false);
  assert.equal(node('#book-workspace').hidden, false);
  assert.equal(node('#welcome').hidden, true);
});

test('returning Home supersedes a pending book selection and suppresses its late error', async () => {
  let reject;
  const {workspace, state, node} = environment(() => new Promise((_resolve, fail) => { reject = fail; }));
  const pending = workspace.selectBook('two');
  workspace.showLibrary();
  reject(new Error('Late failure from a superseded request'));
  await pending;
  assert.equal(state.book.id, 'one');
  assert.equal(state.libraryView, true);
  assert.equal(state.loading, false);
  assert.equal(node('#welcome').hidden, false);
  assert.equal(node('#toast').textContent, undefined);
});

test('a stale failure cannot clear loading or report an error over a newer selection', async () => {
  const pendingRequests = [];
  const {workspace, state, node} = environment(() => new Promise((resolve, reject) => { pendingRequests.push({resolve,reject}); }));
  const old = workspace.selectBook('two');
  const current = workspace.selectBook('three');
  pendingRequests[0].reject(new Error('Superseded failure'));
  await old;
  assert.equal(state.loading, true);
  assert.equal(node('#toast').textContent, undefined);
  pendingRequests[1].resolve({...state.books[1], id:'three'});
  await current;
  assert.equal(state.book.id, 'three');
  assert.equal(state.loading, false);
  assert.equal(node('#book-workspace').hidden, false);
});

test('workspace tabs use one tab stop and Arrow, Home, End select and focus panels', () => {
  const {workspace, state, tabs, node} = environment();
  workspace.setTab('read');
  const key = (index, value) => {
    let prevented = false;
    tabs[index].listeners.keydown({currentTarget:tabs[index], key:value, preventDefault(){ prevented = true; }});
    return prevented;
  };
  assert.equal(key(0, 'ArrowLeft'), true);
  assert.equal(state.tab, 'studio');
  assert.equal(tabs[2].focused, true);
  assert.equal(node('#studio-view').hidden, false);
  assert.equal(node('#read-view').hidden, true);
  assert.deepEqual(tabs.map(tab => tab.tabIndex), [-1,-1,0]);
  assert.deepEqual(tabs.map(tab => tab.attributes.get('aria-selected')), ['false','false','true']);
  assert.equal(tabs.some(tab => tab.attributes.has('aria-current')), false);
  assert.equal(key(2, 'ArrowRight'), true);
  assert.equal(state.tab, 'read');
  key(0, 'End'); assert.equal(state.tab, 'studio');
  key(2, 'Home'); assert.equal(state.tab, 'read');
  assert.equal(key(0, 'Tab'), false);
});

test('listening and Studio shortcuts open their destination without starting work', () => {
  const {workspace, state, node, calls} = environment();
  const details = {open:false};
  node('#simple-listen').details = details;
  workspace.showLibrary();
  node('#reader-listen-setup').listeners.click();
  assert.equal(state.libraryView, false);
  assert.equal(state.tab, 'read');
  assert.equal(details.open, true);
  assert.equal(node('#simple-listen [data-listen-field="voice"]').focused, true);
  node('#studio-cast-link').listeners.click();
  assert.equal(state.tab, 'cast');
  assert.equal(node('#cast-tab').focused, true);
  node('#studio-script-link').listeners.click();
  assert.equal(node('#script-heading').scrolled, true);
  assert.equal(node('#script-heading').focused, true);
  assert.deepEqual(calls, {requests:0, stops:0, saves:0});
});

test('provider setup reveals the requested provider before focusing its key', () => {
  const {workspace, node, calls} = environment();
  const details = {open:false};
  node('#api-key-openai').details = details;
  workspace.openSettings('openai');
  assert.equal(node('#settings-dialog').open, true);
  assert.equal(details.open, true);
  assert.equal(node('#api-key-openai').focused, true);
  assert.equal(calls.requests, 0);
});

test('Plan analysis opens the budgeting disclosure before scrolling and focusing its controls', () => {
  const {node, calls, media} = environment();
  const disclosure = {open:false};
  const panel = node('#progressive-production');
  panel.details = disclosure;
  panel.scrollIntoView = () => { assert.equal(disclosure.open, true); panel.scrolled = true; };
  const control = node('#progressive-production select');
  control.focus = () => { assert.equal(disclosure.open, true); control.focused = true; };
  node('#analyze-button').listeners.click();
  assert.equal(panel.rendered, true);
  assert.equal(panel.scrolled, true);
  assert.equal(control.focused, true);
  assert.equal(calls.requests, 0);
  assert.equal(media.generations, 0);
});

test('playing an unnarrated passage opens narrator setup without generating audio', async () => {
  const {workspace, state, node, calls, media} = environment();
  state.book.segments = [{id:'s1', chapter_id:'c1', text:'An original short line.'}];
  const details = {open:false};
  node('#simple-listen').details = details;
  await workspace.startSegment('s1');
  assert.equal(details.open, true);
  assert.equal(node('#simple-listen [data-listen-field="voice"]').focused, true);
  assert.match(node('#toast').textContent, /Choose a narrator in Listening settings/);
  assert.equal(media.generations, 0);
  assert.equal(media.plays, 0);
  assert.equal(calls.requests, 0);
});

test('passive unnarrated selection keeps setup closed; stale takes retain Studio guidance', async () => {
  const {workspace, state, node, media} = environment();
  const segment = {id:'s1', chapter_id:'c1', text:'An original short line.'};
  state.book.segments = [segment];
  const details = {open:false};
  node('#simple-listen').details = details;
  await workspace.startSegment('s1', {autoplay:false});
  assert.equal(details.open, false);
  segment.audio = {url:'/saved-take.wav', stale:true};
  await workspace.startSegment('s1');
  assert.equal(details.open, false);
  assert.match(node('#toast').textContent, /out of date.*studio/);
  assert.equal(media.generations, 0);
  assert.equal(media.plays, 0);
});

test('book byline uses singular chapter and section counts', () => {
  const {workspace, state, node} = environment();
  state.book.chapters[0].kind = 'chapter';
  workspace.renderBook();
  assert.equal(node('#book-byline').textContent, 'A & B  ·  1 chapter');
  state.book.chapters.push({id:'c2', title:'Afterword', kind:'afterword'});
  workspace.renderBook();
  assert.equal(node('#book-byline').textContent, 'A & B  ·  1 chapter · 1 other section');
});
