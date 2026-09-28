/* Consent and listening correctness: tapping text never spends, paid narration
   asks once per book per browser session, whole-book/scene narration shows an
   estimate before anything is posted, and status labels stay truthful.
   Runs the app's real functions (sliced from app.js) with the real listening
   module, fake DOM nodes and a fetch spy. No provider is contacted. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const appSource = fs.readFileSync(path.join(__dirname, '../bardic/static/app.js'), 'utf8');
const listenSource = fs.readFileSync(path.join(__dirname, '../bardic/static/listen.js'), 'utf8');
const previewSource = fs.readFileSync(path.join(__dirname, '../bardic/static/voice-preview.js'), 'utf8');
const uiSource = fs.readFileSync(path.join(__dirname, '../bardic/static/ui.js'), 'utf8');
const statusSource = fs.readFileSync(path.join(__dirname, '../bardic/static/listen-status.js'), 'utf8');
const playerSource = fs.readFileSync(path.join(__dirname, '../bardic/static/player.js'), 'utf8');
function between(start, end) {
  const a = appSource.indexOf(start), b = appSource.indexOf(end, a + start.length);
  assert.ok(a >= 0 && b > a, `${start} boundaries`);
  return appSource.slice(a, b);
}
const line = prefix => appSource.split('\n').find(text => text.startsWith(prefix));
const tick = () => new Promise(resolve => setImmediate(resolve));
// Objects made inside a vm context have that context's prototypes.
const plain = value => JSON.parse(JSON.stringify(value));
async function settle(times = 30) { for (let i = 0; i < times; i++) await tick(); }
// Endpoints that can make audio (and so cost money or GPU time).
const PAID = /\/listen(\/chapter)?$|\/render$|\/voice-preview|\/performances$/;

function fakeNodes() {
  const nodes = new Map();
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {selector, innerHTML:'', textContent:'', hidden:false, disabled:false, value:'', dataset:{}, listeners:{}, style:{},
      addEventListener(name, handler) { this.listeners[name] = handler; }, classList:{toggle(){}, add(){}, remove(){}},
      setAttribute(name, value) { this.attributes = {...this.attributes, [name]:String(value)}; }, removeAttribute(){}, getAttribute(name) { return this.attributes?.[name] ?? null; },
      focus(){ this.focused = true; }, scrollIntoView(){}, contains() { return false; }, childElementCount:0,
      open:false, showModal() { this.open = true; }, close() { this.open = false; }});
    return nodes.get(selector);
  };
  return {nodes, node};
}
const story = id => ({id, title:'Harbor lights', revision:1,
  chapters:[{id:'c1', title:'One', kind:'chapter', narrative_order:1, text:'First line. Second line. Third line.'}],
  characters:[{id:'narrator', name:'Narrator'}], scenes:[],
  segments:[{id:'s1', chapter_id:'c1', speaker_id:'narrator', text:'First line.', start:0, end:11},
    {id:'s2', chapter_id:'c1', speaker_id:'narrator', text:'Second line.', start:12, end:24},
    {id:'s3', chapter_id:'c1', speaker_id:'narrator', text:'Third line.', start:25, end:36}]});

// The reader and player with the real listening module in Gemini one-narrator mode.
function listening({bookId = 'book-t', session = new Map(), provider = 'gemini', saved = true, prior = {}, takes = () => [], navigator, book} = {}) {
  const {nodes, node} = fakeNodes();
  const storage = new Map();
  if (saved) storage.set(`bardic:listen:${bookId}`, JSON.stringify({mode:'simple', provider, voices:{system:'', gemini:'Kore', breeze:''}, continuous:false, ...prior}));
  const calls = [], toasts = [];
  const job = {id:'job-1', kind:'listen_chapter', status:'running', chapter_id:'c1', session_id:'sess', chunks:[], projection:[]};
  const status = {providers:[{id:'system', available:provider === 'system'}, {id:'gemini', available:true}],
    tts_model:'gemini-3.8-flash-tts', tts_models:['gemini-3.8-flash-tts'], listen_chunking:{ramp_seconds:[30,60], target_seconds:420, concurrency:2}};
  const clock = {now:0};
  const state = {book:book || story(bookId), chapterId:'c1', segmentId:'s1', audioSegmentId:null, pendingOffset:0, selectionVersion:1, jobs:[], tab:'read', status, lastSave:0};
  const audio = {src:'', paused:true, currentTime:0, duration:2, playbackRate:1, defaultPlaybackRate:1, plays:0,
    pause() { this.paused = true; }, play() { this.plays++; this.paused = false; return Promise.resolve(); }, load() {},
    removeAttribute(name) { if (name === 'src') this.src = ''; }, getAttribute(name) { return name === 'src' ? this.src : null; }, addEventListener() {}};
  const context = {state, audio, $:node, $$:() => [], icon:name => name, formatTime:value => String(value),
    window:{}, console, clock, document:{activeElement:null, visibilityState:'visible'}, ...(navigator ? {navigator} : {}),
    localStorage:{getItem:key => storage.get(key) ?? null, setItem:(key, value) => storage.set(key, value)},
    sessionStorage:{getItem:key => session.get(key) ?? null, setItem:(key, value) => session.set(key, value)},
    // Timers never fire: status watching stays idle, so every request is explicit.
    setTimeout:() => 0, clearTimeout() {}, setInterval:() => 0, clearInterval() {},
    fetch:async (url, options = {}) => {
      const call = {url, method:options.method || 'GET', body:options.body ? JSON.parse(options.body) : null};
      calls.push(call);
      let data = {};
      if (url.startsWith('/api/jobs?')) data = [];
      else if (url.includes('/listen/takes')) data = {takes:takes(url)};
      // One-passage narration (Mac voices): each POST makes one passage of audio.
      else if (/\/listen$/.test(url)) { const voice = call.body.voice || 'mac'; data = {session:{id:`sess-${voice}`}, audio:{url:`/${voice}-${call.body.segment_id}.wav`, duration:2, available:true}}; }
      else if (url.endsWith('/listen/chapter/preview')) data = {session:{id:'sess'}, requests_needed:1, quota:{requests_today:1, rpd:100}};
      else if (url.endsWith('/listen/chapter')) data = {session:{id:'sess'}, job, joined:false};
      return {ok:true, status:200, json:async () => data};
    },
    Audio:class { load() {} removeAttribute() {} },
    safeRead:(_key, fallback) => fallback, safeWrite() {},
    toast:message => toasts.push(message),
    updateHighlight() {}, renderStudio() {}, renderJob() {}, pollJobs() {}, renderProduction() {},
  };
  vm.runInNewContext(previewSource, context);
  vm.runInNewContext(uiSource, context);
  vm.runInNewContext(listenSource, context);
  vm.runInNewContext(statusSource, context);
  vm.runInNewContext(playerSource, context);
  const helpers = appSource.split('\n').filter(text => /^const (escapeHTML|currentChapter|chapterSegments|segmentById|characterById|playable|simpleActive|listeningAudio|listeningReady|busyJob|progressKey) =/.test(text)).join('\n');
  vm.runInNewContext([
    'let playGeneration = 0, preparingListen = false, previewEnhanced = false, mediaBuffering = false; const listeningPreloads = new Map();',
    helpers,
    between('function saveProgress(', 'function stopAudio('),
    between('function stopAudio(', 'function renderLibrary('),
    between('function renderBookStatus(', 'function renderReader('),
    between('function renderReader(', 'function castVoiceBlock('),
    between('function setTab(', 'function updateHighlight('),
    between('async function startSegment(', 'function updateProviderHint('),
    between("$('#reader-text').addEventListener('click'", "$('#cast-grid').addEventListener('submit'"),
    'globalThis.app = {startSegment,togglePlayback,renderReader,updatePlayer,confirmPaidConsent,closePaidConsent,goToPassage,scrollMotion,renderListenSheet,chooseInSheet,startListening,finishClip,setupMediaSession,readerPrefs,',
    '  chapterTimeline,chapterPosition,seekChapter,narrationPlaying,get preparing() { return preparingListen; }};',
    'window.BardicPlayer.attach({$, audio, timeline:chapterTimeline, position:chapterPosition, seek:seekChapter, playing:narrationPlaying,',
    '  statusInput:() => window.BardicListen.statusInput(state.book), pause:() => { if (narrationPlaying()) void togglePlayback(); }, setInterval:() => 0,',
    '  now:() => clock.now});',
  ].join('\n'), context);
  context.app.renderReader();
  const paid = () => calls.filter(call => call.method === 'POST' && PAID.test(call.url));
  const tap = id => node('#reader-text').listeners.click({target:{closest:() => ({dataset:{segment:id}})}});
  const key = (id, name) => node('#reader-text').listeners.keydown({key:name, preventDefault() {}, target:{closest:() => ({dataset:{segment:id}})}});
  const drawer = action => node('#simple-listen').listeners.click({target:{closest:() => ({dataset:{listenAction:action}})}});
  return {state, audio, calls, paid, tap, key, drawer, node, nodes, session, toasts, clock, app:context.app, listen:context.window.BardicListen, player:context.window.BardicPlayer};
}

test('tapping or pressing Enter on text moves the reading place and never requests narration', async () => {
  const env = listening();
  await settle();
  env.tap('s2');
  await settle();
  assert.equal(env.state.segmentId, 's2', 'a tap moves the place');
  env.key('s3', 'Enter');
  await settle();
  assert.equal(env.state.segmentId, 's3', 'Enter moves the place');
  assert.deepEqual(env.paid(), [], 'no narration request without Play');
  assert.equal(env.audio.plays, 0);
  assert.equal(env.node('#paid-consent').innerHTML, '', 'a tap never asks to spend');
  // Device voices behave the same way: a tap is never Play.
  const device = listening({provider:'system'});
  await settle();
  device.tap('s2'); device.key('s3', 'Enter');
  await settle();
  assert.deepEqual(device.paid(), []);
  assert.equal(device.audio.plays, 0);
});

test('a tap while narration is playing jumps there; Space starts Play from the focused passage', async () => {
  const env = listening();
  await settle();
  env.session.set('bardic:paid-listening:book-t', 'gemini');
  void env.app.togglePlayback();
  await settle();
  assert.equal(env.paid().length, 1, 'Play with consent requests the chapter');
  assert.equal(env.app.preparing, true);
  env.tap('s3');
  await settle();
  assert.equal(env.state.segmentId, 's3');
  assert.equal(env.paid().length, 2, 'while playing, a tap jumps and plays from the new place');
  assert.equal(env.paid()[1].body.segment_id, 's3');
  // Space on a passage is Play, so it goes through the same consent.
  const spaced = listening({bookId:'book-space'});
  await settle();
  spaced.key('s2', ' ');
  await settle();
  assert.deepEqual(spaced.paid(), [], 'Space asks for paid consent first');
  assert.match(spaced.node('#paid-consent').innerHTML, /Gemini · paid/);
  spaced.app.confirmPaidConsent();
  await settle();
  assert.equal(spaced.paid().length, 1);
  assert.equal(spaced.paid()[0].body.segment_id, 's2');
});

test('the first paid Play per book per browser session asks first; the second Play does not', async () => {
  const session = new Map();
  const env = listening({session});
  await settle();
  void env.app.togglePlayback();
  await settle();
  assert.deepEqual(env.paid(), [], 'nothing is sent before Confirm');
  const panel = env.node('#paid-consent');
  assert.equal(panel.hidden, false);
  assert.match(panel.innerHTML, /Kore · Gemini · paid narration/);
  assert.match(panel.innerHTML, /Google bills each request/);
  assert.match(panel.innerHTML, /cannot show a cost/, 'an unknown price is never shown as free');
  assert.match(panel.innerHTML, /Play with Gemini · paid/);
  env.app.confirmPaidConsent();
  await settle();
  assert.equal(env.paid().length, 1);
  assert.equal(env.paid()[0].url, '/api/books/book-t/listen/chapter');
  assert.equal(session.get('bardic:paid-listening:book-t'), 'gemini', 'consent lasts for this browser session');
  assert.equal(panel.hidden, true);
  // Stop, then Play again: no second question.
  void env.app.togglePlayback();
  await settle();
  void env.app.togglePlayback();
  await settle();
  assert.equal(env.paid().length, 2, 'the second Play proceeds');
  assert.equal(panel.hidden, true);
  // Same session, page reloaded: still consented. New session: asks again.
  const reloaded = listening({session});
  await settle();
  void reloaded.app.togglePlayback();
  await settle();
  assert.equal(reloaded.paid().length, 1, 'a reload in the same tab keeps consent');
  const fresh = listening();
  await settle();
  void fresh.app.togglePlayback();
  await settle();
  assert.deepEqual(fresh.paid(), [], 'a new browser session asks again');
  // Another book in the same session asks for itself.
  const other = listening({session, bookId:'book-other'});
  await settle();
  void other.app.togglePlayback();
  await settle();
  assert.deepEqual(other.paid(), [], 'consent is per book');
  // Cancel leaves nothing behind; device voices never ask.
  other.app.closePaidConsent();
  assert.equal(other.node('#paid-consent').hidden, true);
  assert.deepEqual(other.paid(), []);
  const device = listening({provider:'system', bookId:'book-device'});
  await settle();
  void device.app.togglePlayback();
  await settle();
  assert.equal(device.node('#paid-consent').innerHTML, '', 'free narration needs no consent');
});

test('Queue chapter with a paid narrator waits for the same session consent', async () => {
  const env = listening();
  await settle();
  env.drawer('prepare-chapter');
  await settle();
  assert.deepEqual(env.paid(), [], 'Queue chapter sends nothing before Confirm');
  assert.match(env.node('#paid-consent').innerHTML, /Gemini · paid/);
  env.app.confirmPaidConsent();
  await settle();
  assert.equal(env.paid().length, 1);
  assert.equal(env.paid()[0].body.intent, 'queue');
});

test('Pause says Paused; the player names the voice and marks a paid narrator', async () => {
  const env = listening();
  await settle();
  const stops = [];
  const realStop = env.listen.stop;
  env.listen.stop = (book, options) => { stops.push(options); return realStop(book, options); };
  env.audio.paused = false;
  void env.app.togglePlayback();
  assert.equal(stops.at(-1).pause, true, 'Pause is reported as a pause');
  assert.match(env.node('#simple-listen').innerHTML, /Paused\. Finished audio is saved/);
  assert.doesNotMatch(env.node('#simple-listen').innerHTML, /Stopped\./);
  env.app.updatePlayer();
  assert.match(env.node('#player-subtitle').textContent, /^Kore · Gemini · paid · Passage 1/);
  env.app.renderListenSheet();
  assert.match(env.node('#listen-sheet-body').innerHTML, /Hear example · paid/, 'the paid example is labelled in the narrator sheet');
});

test('the narrator label resolves library voices and names the Breeze default, never a raw id', async () => {
  const {node} = fakeNodes();
  const context = {window:{}, setTimeout:() => 0, localStorage:{getItem:() => JSON.stringify({mode:'simple', provider:'breeze', voices:{system:'', gemini:'library:vl_0123456789abcdef', breeze:''}}), setItem() {}},
    fetch:async () => ({ok:true, status:200, json:async () => ([])})};
  vm.runInNewContext(listenSource, context);
  const api = context.window.BardicListen;
  const book = story('book-voices');
  const library = {voices:[{id:'vl_0123456789abcdef', provider:'gemini', name:'Harbor Keeper', current_version:1, versions:[{version:1, provider_voice_id:'x'}]},
    {id:'vl_fedcba9876543210', provider:'breeze', name:'Storyteller', current_version:1, versions:[{version:1, provider_voice_id:'story'}]}],
    defaults:{breeze:'vl_fedcba9876543210'}};
  await api.render(node('#panel'), book, {chapterId:'c1', segmentId:'s1', status:{providers:[{id:'breeze', available:true}]}, voiceLibrary:library});
  assert.equal(api.describe(book).voiceName, 'Default (Storyteller)');
  assert.equal(api.describe(book).paid, false);
  api.choose(book, 'provider', 'gemini');
  assert.equal(api.describe(book).voiceName, 'Harbor Keeper');
  assert.equal(api.describe(book).paid, true);
  assert.doesNotMatch(JSON.stringify(api.describe(book)), /library:|vl_/);
});

test('a new book starts with one narrator; saved Full cast and Studio takes are kept', async () => {
  const {node} = fakeNodes();
  const make = saved => {
    const context = {window:{}, setTimeout:() => 0, localStorage:{getItem:() => saved ? JSON.stringify(saved) : null, setItem() {}},
      fetch:async () => ({ok:true, status:200, json:async () => ([])})};
    vm.runInNewContext(listenSource, context);
    return context.window.BardicListen;
  };
  const options = {chapterId:'c1', segmentId:'s1', status:{providers:[{id:'system', available:true}]}};
  const fresh = make(null), book = story('b1');
  await fresh.render(node('#a'), book, options);
  assert.equal(fresh.getSelection(book).mode, 'simple');
  assert.equal(fresh.getSelection(book).provider, 'system', 'device voices stay the default where available');
  const kept = make({mode:'enhanced', provider:'system'});
  await kept.render(node('#b'), book, options);
  assert.equal(kept.getSelection(book).mode, 'enhanced', 'a saved Full cast choice is not changed');
  const recorded = make(null), studio = story('b2');
  studio.segments[0].audio = {url:'/take.wav', duration:1};
  await recorded.render(node('#c'), studio, options);
  assert.equal(recorded.getSelection(studio).mode, 'enhanced', 'a book with Studio takes plays them');
  assert.equal(recorded.choices(studio).hasFullCast, true, 'the sheet offers Full cast');
  assert.match(node('#c').innerHTML, /<h3>Full cast<\/h3>/);
});

// Studio Narrate book / Narrate scene: estimate, then confirm.
function studio({provider = 'gemini', quota = {requests_today:95, rpd:100, resets_at:'2026-09-29T07:00:00+00:00'}, segments} = {}) {
  const {nodes, node} = fakeNodes();
  const posts = [], toasts = [];
  const book = story('book-s');
  book.scenes = [{id:'scene-1', chapter_id:'c1', title:'The quay'}];
  book.segments.forEach(segment => { segment.scene_id = 'scene-1'; });
  if (segments) segments(book.segments);
  node('#render-provider').value = provider;
  const sceneHost = node('[scene host]');
  sceneHost.dataset.renderConfirmHost = 'scene-1';
  const state = {book, jobs:[], status:{providers:[{id:'system', available:true}, {id:'breeze', available:true}], tts_model:'gemini-3.8-flash-tts',
    tts_limits:{'gemini-3.8-flash-tts':{rpm:10, tpm:10000, rpd:100}}, tts_rate:{'gemini-3.8-flash-tts':{daily_block_seconds:0}}}};
  let statusReads = 0;
  const context = {state, $:node, $$:selector => selector === '[data-render-confirm-host]' ? [sceneHost] : [],
    busyJob:() => state.jobs.find(job => ['queued','running'].includes(job.status)),
    cloudProviders:['gemini','openai','anthropic'], analysisLabels:{gemini:'Gemini'}, providerHasKey:() => true,
    openSettings() {}, toast:message => toasts.push(message), stopAudio() {}, updateBusyControls() {}, renderJob() {}, pollJobs:async () => {},
    post:async (url, body) => { posts.push({url, body}); return {id:`job-${posts.length}`, book_id:book.id, kind:'render', status:'queued'}; },
    refreshStatus:async () => { statusReads++; state.status = {...state.status, tts_quota:quota ? {'gemini-3.8-flash-tts':quota} : undefined}; },
    playable:segment => Boolean(segment?.audio?.url && !segment.audio.stale),
    narrationModel:value => value === 'gemini' ? 'gemini-3.8-flash-tts' : value === 'system' ? 'macos-say' : null,
    NARRATION_LABELS:{system:'Mac voices', gemini:'Gemini', breeze:'Breeze'}, scrollMotion:() => 'auto',
    renderStudio() {}, updateProviderHint() {}, refreshBreeze() {}, auditionPassage() {}, retakeSegment() {}, startSegment() {},
  };
  vm.runInNewContext([
    line('const escapeHTML ='),
    between('async function startJob(', '// A seeded provider (Breeze)'),
    between("$('#render-button').addEventListener('click'", "$('#job-banner').addEventListener('click'"),
    line("$('#scene-list').addEventListener('click'"),
    'globalThis.studio = {startJob, renderEstimate};',
  ].join('\n'), context);
  const act = action => node('#studio-view').listeners.click({target:{closest:selector => selector === '[data-render-confirm-action]' ? {dataset:{renderConfirmAction:action}} : null}});
  const narrateScene = () => node('#scene-list').listeners.click({target:{closest:selector => selector === '[data-render-scene]' ? {dataset:{renderScene:'scene-1'}} : null}});
  return {state, posts, toasts, node, nodes, sceneHost, act, narrateScene, statusReads:() => statusReads, api:context.studio};
}

test('Narrate book with Gemini shows an estimate and posts nothing until Confirm', async () => {
  const env = studio({segments:list => { list[0].audio = {url:'/take.wav', provider:'gemini', model:'gemini-3.8-flash-tts'}; }});
  env.node('#render-button').listeners.click();
  await settle();
  assert.deepEqual(env.posts, [], 'Narrate book never posts before Confirm');
  assert.equal(env.statusReads(), 1, "today's request count is read fresh");
  const panel = env.node('#render-confirm');
  assert.equal(panel.hidden, false);
  const html = panel.innerHTML;
  assert.match(html, /Whole book · Harbor lights · Gemini · paid/);
  assert.match(html, /<dt>Passages in scope<\/dt><dd>3<\/dd>/);
  assert.match(html, /<dt>Already have audio \(reused\)<\/dt><dd>about 1<\/dd>/);
  assert.match(html, /<dt>Requests needed<\/dt><dd>up to 2<\/dd>/);
  assert.match(html, /<dt>Cost<\/dt><dd>Unknown<\/dd>/, 'an unknown price is shown as unknown');
  assert.doesNotMatch(html, /\$0/, 'unknown is never shown as $0');
  assert.match(html, /used 95 of 100 daily Gemini speech requests today, so 5 requests are left/);
  assert.match(html, /Narrate whole book · up to 2 Gemini requests/, 'the confirm label carries scope and count');
  env.act('confirm');
  await settle();
  assert.equal(env.posts.length, 1);
  assert.deepEqual(plain(env.posts[0]), {url:'/api/books/book-s/render', body:{provider:'gemini'}});
  assert.equal(panel.hidden, true, 'the estimate closes once the job starts');
});

test('more requests than remain today is called out; cancel posts nothing', async () => {
  const env = studio({quota:{requests_today:99, rpd:100, resets_at:'2026-09-29T07:00:00+00:00'}});
  env.node('#render-button').listeners.click();
  await settle();
  assert.match(env.node('#render-confirm').innerHTML, /needs more requests than remain today/);
  env.act('cancel');
  assert.equal(env.node('#render-confirm').hidden, true);
  assert.deepEqual(env.posts, []);
  const unknown = studio({quota:null});
  unknown.node('#render-button').listeners.click();
  await settle();
  assert.match(unknown.node('#render-confirm').innerHTML, /request count could not be read/);
  assert.deepEqual(unknown.posts, []);
});

test('Narrate scene with Breeze shows passages and time, no price, inside the scene', async () => {
  const env = studio({provider:'breeze'});
  env.narrateScene();
  await settle();
  assert.deepEqual(env.posts, [], 'Narrate scene never posts before Confirm');
  assert.equal(env.statusReads(), 0, 'Breeze needs no quota read');
  assert.equal(env.node('#render-confirm').hidden, true, 'a scene estimate appears in its scene');
  const html = env.sceneHost.innerHTML;
  assert.match(html, /Scene “The quay” · One · Breeze · your GPU/);
  assert.match(html, /<dt>Cost<\/dt><dd>No charge<\/dd>/);
  assert.match(html, /GPU/);
  assert.match(html, /Narrate scene · 3 passages on Breeze/);
  // Changing the provider closes the estimate; a stale Confirm sends nothing.
  env.node('#render-provider').value = 'gemini';
  env.node('#render-provider').listeners.change();
  assert.equal(env.sceneHost.innerHTML, '');
  env.act('confirm');
  await settle();
  assert.deepEqual(env.posts, []);
  // Confirm on a fresh estimate posts the scene.
  env.node('#render-provider').value = 'breeze';
  env.narrateScene();
  await settle();
  env.act('confirm');
  await settle();
  assert.deepEqual(plain(env.posts), [{url:'/api/books/book-s/render', body:{provider:'breeze', scene_id:'scene-1'}}]);
});

test('device narration and single passages start without an estimate', async () => {
  const env = studio({provider:'system'});
  env.node('#render-button').listeners.click();
  await settle();
  assert.deepEqual(plain(env.posts), [{url:'/api/books/book-s/render', body:{provider:'system'}}], 'free device narration needs no confirm');
  const passage = studio();
  await passage.api.startJob('render', {segment_id:'s1', force:true});
  assert.equal(passage.posts.length, 1, 'one passage is one request and starts directly');
});

// Book header, Cast badge and job banner.
function workspace() {
  const {node} = fakeNodes();
  const state = {jobs:[], book:null};
  const listen = {mode:'simple', ready:new Set()};
  let lifecycle = 0;
  const context = {state, $:node, $$:() => [], icon:() => '', window:{BardicShell:{renderLifecycle:() => { lifecycle++; }}, BardicListen:{getSelection:() => ({mode:listen.mode}), resolve:(_book, segment) => listen.ready.has(segment.id) ? {url:'/a.wav'} : null}},
    busyJob:() => state.jobs.find(job => ['queued','running'].includes(job.status)), updateBusyControls() {}, renderProduction() {},
    syncWorkspaceNavigation() {}, renderLibrary() {}, renderReader() {}, renderCast() {}, renderStudio() {}, renderVoices() {}, setTab() {}, updatePlayer() {}};
  vm.runInNewContext([
    line('const escapeHTML ='), line('const playable ='),
    between('function renderBook(', 'function renderReader('),
    between('const LISTENING_JOB_KINDS', 'async function pollJobs('),
    between("$('#job-banner').addEventListener('click'", '\n'),
    'globalThis.ui = {renderBook, renderBookStatus, renderJob};',
  ].join('\n'), context);
  return {state, node, listen, ui:context.ui, lifecycleRenders:() => lifecycle};
}

test('the Cast badge counts the cards shown and the book status is the lifecycle strip', () => {
  const env = workspace();
  env.state.book = {...story('book-w'), author:'', characters:[{id:'narrator', name:'Narrator'}, {id:'unassigned', name:'Unassigned'}, {id:'mara', name:'Mara'}, {id:'ivo', name:'Ivo'}]};
  env.ui.renderBook();
  assert.equal(env.node('#cast-count').textContent, 4, 'Narrator and unassigned dialogue have cards too');
  // The strip (lifecycle.js, rendered by shell.js; tests/lifecycle_test.js) replaced the #book-status line.
  assert.equal(env.lifecycleRenders(), 1, 'rendering the book repaints the lifecycle strip once');
  env.ui.renderBookStatus();
  assert.equal(env.lifecycleRenders(), 2);
  assert.ok(!env.node('#book-status').textContent, 'nothing writes the old status line');
});

test('the job banner shows running jobs and unacknowledged outcomes, with distinct stop reasons', () => {
  const env = workspace();
  const banner = env.node('#job-banner');
  env.state.jobs = [{id:'old', kind:'listen', status:'completed', message:'Ready to listen'}];
  env.ui.renderJob();
  assert.equal(banner.hidden, true, 'an old finished job does not claim the banner');
  env.state.jobs = [{id:'r1', kind:'render', status:'running', progress:1, total:3}];
  env.ui.renderJob();
  assert.equal(banner.hidden, false);
  assert.match(banner.innerHTML, /Recording your story/);
  env.state.jobs = [{id:'r1', kind:'render', status:'completed', message:'Ready to listen'}];
  env.ui.renderJob();
  assert.equal(banner.hidden, false, 'a job you watched finish stays until acknowledged');
  banner.listeners.click({target:{closest:selector => selector === '[data-dismiss-job]' ? {dataset:{dismissJob:'r1'}} : null}});
  assert.equal(banner.hidden, true, 'dismissing acknowledges it');
  env.state.jobs = [{id:'l1', kind:'listen', status:'running'}];
  env.ui.renderJob();
  env.state.jobs = [{id:'l1', kind:'listen', status:'completed', message:'Ready to listen'}];
  env.ui.renderJob();
  assert.equal(banner.hidden, true, 'a finished listening job leaves the banner to the player');
  const labels = ['failed','cancelled','interrupted'].map(status => {
    env.state.jobs = [{id:`x-${status}`, kind:'render', status:'running'}];
    env.ui.renderJob();
    env.state.jobs = [{id:`x-${status}`, kind:'render', status}];
    env.ui.renderJob();
    return banner.innerHTML.match(/<span class="job-label">([^<]*)<\/span>/)[1];
  });
  assert.deepEqual(labels, ['Failed', 'Cancelled', 'Interrupted · ready to resume']);
});

test('cancelled, failed, interrupted and budget-limited chapter jobs keep distinct headlines', async () => {
  const headlines = {};
  for (const status of ['failed','cancelled','interrupted','budget_limited','quota_limited']) {
    const {node} = fakeNodes();
    const job = {id:`job-${status}`, kind:'listen_chapter', status, chapter_id:'c1', session_id:'sess', chunks:[], projection:[], error:'', message:''};
    const context = {window:{}, setTimeout:fn => setImmediate(fn),
      localStorage:{getItem:() => JSON.stringify({mode:'simple', provider:'gemini', voices:{gemini:'Kore'}, sessionId:'sess', sessionKey:JSON.stringify(['gemini','Kore','gemini-3.8-flash-tts'])}), setItem() {}},
      fetch:async url => ({ok:true, status:200, json:async () => url.startsWith('/api/jobs?') ? [job] : url.includes('/takes') ? {takes:[]} : {session:{id:'sess'}, requests_needed:1}})};
    vm.runInNewContext(listenSource, context);
    const api = context.window.BardicListen, book = story('book-h'), panel = node('#panel');
    const options = {chapterId:'c1', segmentId:'s1', status:{providers:[{id:'system', available:false}, {id:'gemini', available:true}], tts_model:'gemini-3.8-flash-tts', tts_models:['gemini-3.8-flash-tts']}};
    await api.render(panel, book, options);
    await settle();
    await api.render(panel, book, options);
    headlines[status] = panel.innerHTML.match(/<div class="simple-listen-buffer chapter-queue"[^>]*><div><span>([^<]*)<\/span>/)?.[1];
  }
  assert.equal(headlines.failed, 'Chapter preparation failed · finished audio is saved');
  assert.equal(headlines.cancelled, 'Chapter preparation cancelled · finished audio is saved');
  assert.equal(headlines.interrupted, 'Chapter preparation interrupted when Bardic stopped · finished audio is saved');
  assert.equal(headlines.budget_limited, 'Spending limit reached · finished audio is saved', 'budget_limited is not "Not prepared yet"');
  assert.equal(headlines.quota_limited, 'Daily request limit reached · finished audio is saved');
  assert.equal(new Set(Object.values(headlines)).size, 5);
});

test('script-driven scrolling respects reduced motion', () => {
  const run = reduce => {
    const context = {matchMedia:query => ({matches:reduce && query === '(prefers-reduced-motion: reduce)'})};
    vm.runInNewContext(`${between('function scrollMotion(', 'function openListeningSettings(')}\nglobalThis.value = scrollMotion();`, context);
    return context.value;
  };
  assert.equal(run(true), 'auto');
  assert.equal(run(false), 'smooth');
});

// ---- Phase 4: one narrator surface, player basics ------------------------------------
const macTakes = session => url => url.includes(`session_id=${session}`)
  ? ['s1', 's2', 's3'].map(id => ({segment_id:id, audio:{url:`/${session}-${id}.wav`, duration:2, available:true}})) : [];
const macSession = {sessionId:'sess-mac', sessionKey:JSON.stringify(['system', '', 'macos-say'])};
const narrationPosts = env => env.calls.filter(call => call.method === 'POST' && /\/listen(\/chapter)?$/.test(call.url));

test('changing narrator in the sheet sends nothing and keeps playing until Use; switching back reuses saved audio', async () => {
  const env = listening({provider:'system', prior:macSession, takes:macTakes('sess-mac')});
  await settle();
  assert.equal(env.listen.resolve(env.state.book, env.state.book.segments[0])?.url, '/sess-mac-s1.wav', 'the saved narrator audio is loaded');
  await env.app.startSegment('s1');
  await settle();
  assert.equal(env.audio.paused, false);
  const sheet = env.node('#listen-sheet');
  sheet.open = true;
  const before = env.calls.length, plays = env.audio.plays;
  env.app.chooseInSheet({name:'voice', value:'Samantha'});
  env.app.chooseInSheet({name:'provider', value:'gemini'});
  env.app.chooseInSheet({name:'provider', value:'system'});
  await settle();
  assert.equal(env.calls.length, before, 'browsing services and voices sends no request at all');
  assert.equal(env.audio.paused, false, 'playback continues while you browse');
  assert.equal(env.audio.plays, plays);
  assert.equal(env.listen.getSelection(env.state.book).voice, '', 'the narrator is unchanged until Use');
  const html = env.node('#listen-sheet-body').innerHTML;
  assert.equal(env.listen.choices(env.state.book, env.state.narratorDraft).changed, true, 'the primary action reads Use this narrator');
  assert.match(html, /Default Mac voice keeps its saved audio\. Switching back plays it again without new requests\./);
  assert.match(html, /data-sheet-live="continues"/, 'while playing, the sheet adds that playback continues until Use');
  // Use: one stop, then the new narrator plays from the same place.
  await env.app.startListening();
  await settle();
  assert.equal(env.listen.getSelection(env.state.book).voice, 'Samantha');
  const samantha = narrationPosts(env);
  assert.ok(samantha.length >= 1 && samantha.every(call => call.body.voice === 'Samantha'), 'Use is the explicit start of the new narrator');
  assert.equal(env.audio.paused, false);
  // Switch back: its session is remembered, so saved passages are read, not requested.
  sheet.open = true;
  const posts = narrationPosts(env).length;
  env.app.chooseInSheet({name:'voice', value:''});
  await settle();
  assert.equal(narrationPosts(env).length, posts, 'choosing the earlier narrator again sends nothing');
  await env.app.startListening();
  await settle();
  assert.equal(env.listen.getSelection(env.state.book).voice, '');
  assert.equal(narrationPosts(env).length, posts, 'already narrated passages need no new request');
  assert.ok(env.calls.some(call => call.url.includes('/listen/takes?session_id=sess-mac')), 'the saved audio is read back');
  assert.equal(env.audio.src, '/sess-mac-s1.wav');
  assert.equal(env.audio.paused, false);
});

test('the sheet uses BardicUI radio groups and offers Set up for an unavailable narrator', async () => {
  const env = listening({provider:'system'});
  await settle();
  env.state.status.providers = [{id:'system', available:true}, {id:'gemini', available:false}];
  env.node('#listen-sheet').open = true;
  env.app.chooseInSheet({name:'provider', value:'gemini'});
  const html = env.node('#listen-sheet-body').innerHTML;
  assert.match(html, /class="choice" data-kind="cards" role="radiogroup" aria-label="Narration service" data-choice="provider"/);
  assert.match(html, /class="choice" data-kind="chips" role="radiogroup" aria-label="Speed" data-choice="speed"/);
  const group = html.slice(html.indexOf('data-choice="provider"'), html.indexOf('</div>', html.indexOf('data-choice="provider"')));
  assert.equal((group.match(/tabindex="0"/g) || []).length, 1, 'one tab stop per group');
  assert.match(html, /Add a Gemini API key in Settings/);
  assert.match(html, /data-sheet-action="setup" data-provider="gemini">Set up →/);
  assert.match(html, /data-sheet-action="start" data-sheet-live="action" disabled/, 'Start cannot run a narrator that cannot make audio');
  assert.deepEqual(env.paid(), []);
});

test('Pause and the sleep timer stop playback through the same path, cancelling automatic preparation', async () => {
  const env = listening({provider:'system', prior:macSession, takes:macTakes('sess-mac')});
  await settle();
  const stops = [];
  const realStop = env.listen.stop;
  env.listen.stop = (book, options) => { stops.push(JSON.parse(JSON.stringify(options || {}))); return realStop(book, options); };
  await env.app.startSegment('s1');
  await settle();
  void env.app.togglePlayback();
  const pause = stops.at(-1);
  assert.deepEqual(pause, {keepAhead:false, pause:true}, 'Pause cancels what continuous listening started');
  await env.app.startSegment('s1');
  await settle();
  const player = env.player;
  env.clock.now = 0;
  assert.equal(player.setSleep('15'), 'time');
  env.clock.now += 14 * 60 * 1000; player.tickSleep();
  assert.equal(env.audio.paused, false, 'not yet');
  env.audio.paused = true; env.clock.now += 30 * 60 * 1000; player.tickSleep();
  assert.ok(player.sleepState().remaining > 0, 'time paused by you does not count');
  env.audio.paused = false; env.clock.now += 61 * 1000; player.tickSleep();
  assert.equal(env.audio.paused, true, 'the timer paused playback');
  assert.deepEqual(stops.at(-1), pause, 'exactly as the Pause button does');
  assert.equal(player.sleepState().mode, 'off');
  assert.match(env.node('#player-announcer').textContent, /^Paused\. The sleep timer paused playback/, 'the change of state is announced once');
  env.app.updatePlayer();
  assert.equal(env.node('#player-status-label').textContent, 'Paused');
  assert.match(env.node('#player-status-detail').textContent, /sleep timer/);
});

test('sleep at the end of the chapter pauses there and leaves your place at the next chapter', async () => {
  const book = story('book-t');
  book.chapters.push({id:'c2', title:'Two', kind:'chapter', narrative_order:2, text:'Fourth line.'});
  book.segments.push({id:'s4', chapter_id:'c2', speaker_id:'narrator', text:'Fourth line.', start:0, end:12});
  const env = listening({provider:'system', prior:{...macSession, continuous:true}, takes:macTakes('sess-mac'), book});
  await settle();
  const stops = [];
  const realStop = env.listen.stop;
  env.listen.stop = (b, options) => { stops.push(JSON.parse(JSON.stringify(options || {}))); return realStop(b, options); };
  await env.app.startSegment('s3');
  await settle();
  env.player.setSleep('chapter');
  const sent = narrationPosts(env).length;
  await env.app.finishClip();
  await settle();
  assert.deepEqual(stops.at(-1), {keepAhead:false, pause:true});
  assert.equal(env.state.segmentId, 's4', 'your place moves to the next chapter');
  assert.equal(env.state.chapterId, 'c2');
  assert.equal(env.audio.paused, true);
  assert.equal(narrationPosts(env).length, sent, 'nothing more is requested after the pause');
});

test('±15 s, lock-screen seek and position use the chapter timeline', async () => {
  const handlers = {}, positions = [];
  const navigator = {mediaSession:{setActionHandler:(action, handler) => { handlers[action] = handler; }, setPositionState:value => positions.push(value)}};
  const env = listening({provider:'system', prior:macSession, takes:macTakes('sess-mac'), navigator});
  await settle();
  env.app.setupMediaSession();
  for (const action of ['play', 'pause', 'seekbackward', 'seekforward', 'seekto', 'previoustrack', 'nexttrack']) assert.equal(typeof handlers[action], 'function', action);
  // Three saved passages of 2 s: a 6 s chapter.
  env.app.updatePlayer();
  assert.equal(positions.at(-1).duration, 6);
  handlers.seekforward({seekOffset:3});
  await settle();
  assert.equal(env.state.segmentId, 's2', 'forward 3 s from the start lands in the second passage');
  assert.equal(env.state.pendingOffset, 1);
  env.player.skip(15);
  await settle();
  assert.equal(env.state.segmentId, 's3', 'a skip past the end stops at the end of the chapter');
  handlers.seekbackward({});
  await settle();
  assert.equal(env.state.segmentId, 's1', 'back 15 s returns to the start');
  handlers.seekto({seekTime:4.5});
  await settle();
  assert.equal(env.state.segmentId, 's3');
  assert.equal(env.state.pendingOffset, .5);
  assert.deepEqual(narrationPosts(env), [], 'moving while paused never requests narration');
  env.app.updatePlayer();
  assert.ok(positions.every(item => item.position <= item.duration && item.playbackRate === 1));
  assert.equal(env.node('#player-ready').innerHTML, '<span style="left:0.00%;width:100.00%"></span>', 'the whole chapter is ready');
});

test('reader view keeps the screen on while listening unless turned off in Aa', async () => {
  let requests = 0, releases = 0;
  const navigator = {wakeLock:{request:async () => { requests++; return {release:async () => { releases++; }, addEventListener() {}}; }}};
  const env = listening({provider:'system', prior:macSession, takes:macTakes('sess-mac'), navigator});
  await settle();
  env.state.readerMode = true;
  await env.app.startSegment('s1');
  await settle();
  env.app.updatePlayer();
  await settle();
  assert.ok(requests >= 1, 'on by default in reader view');
  const requested = requests, released = releases;
  env.app.readerPrefs().awake = false;
  env.app.updatePlayer();
  await settle();
  assert.equal(releases, released + 1, 'turning it off releases the lock');
  env.app.updatePlayer();
  await settle();
  assert.equal(requests, requested, 'and nothing asks for it again');
});
