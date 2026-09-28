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
      setAttribute(){}, removeAttribute(){}, getAttribute(){ return null; }, focus(){ this.focused = true; }, scrollIntoView(){}});
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
function listening({bookId = 'book-t', session = new Map(), provider = 'gemini', saved = true} = {}) {
  const {nodes, node} = fakeNodes();
  const storage = new Map();
  if (saved) storage.set(`bardic:listen:${bookId}`, JSON.stringify({mode:'simple', provider, voices:{system:'', gemini:'Kore', breeze:''}, continuous:false}));
  const calls = [], toasts = [];
  const job = {id:'job-1', kind:'listen_chapter', status:'running', chapter_id:'c1', session_id:'sess', chunks:[], projection:[]};
  const status = {has_api_key:true, providers:[{id:'system', available:provider === 'system'}, {id:'gemini', available:true}],
    tts_model:'gemini-3.8-flash-tts', tts_models:['gemini-3.8-flash-tts'], listen_chunking:{ramp_seconds:[30,60], target_seconds:420, concurrency:2}};
  const state = {book:story(bookId), chapterId:'c1', segmentId:'s1', audioSegmentId:null, pendingOffset:0, selectionVersion:1, jobs:[], tab:'read', status, lastSave:0};
  const audio = {src:'', paused:true, currentTime:0, duration:2, playbackRate:1, defaultPlaybackRate:1, plays:0,
    pause() { this.paused = true; }, play() { this.plays++; this.paused = false; return Promise.resolve(); }, load() {},
    removeAttribute(name) { if (name === 'src') this.src = ''; }, getAttribute(name) { return name === 'src' ? this.src : null; }, addEventListener() {}};
  const context = {state, audio, $:node, $$:() => [], icon:name => name, formatTime:value => String(value),
    window:{}, console,
    localStorage:{getItem:key => storage.get(key) ?? null, setItem:(key, value) => storage.set(key, value)},
    sessionStorage:{getItem:key => session.get(key) ?? null, setItem:(key, value) => session.set(key, value)},
    // Timers never fire: status watching stays idle, so every request is explicit.
    setTimeout:() => 0, clearTimeout() {}, setInterval:() => 0, clearInterval() {},
    fetch:async (url, options = {}) => {
      const call = {url, method:options.method || 'GET', body:options.body ? JSON.parse(options.body) : null};
      calls.push(call);
      let data = {};
      if (url.startsWith('/api/jobs?')) data = [];
      else if (url.includes('/listen/takes')) data = {takes:[]};
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
  vm.runInNewContext(listenSource, context);
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
    'globalThis.app = {startSegment,togglePlayback,renderReader,updatePlayer,confirmPaidConsent,closePaidConsent,goToPassage,scrollMotion,get preparing() { return preparingListen; }};',
  ].join('\n'), context);
  context.app.renderReader();
  const paid = () => calls.filter(call => call.method === 'POST' && PAID.test(call.url));
  const tap = id => node('#reader-text').listeners.click({target:{closest:() => ({dataset:{segment:id}})}});
  const key = (id, name) => node('#reader-text').listeners.keydown({key:name, preventDefault() {}, target:{closest:() => ({dataset:{segment:id}})}});
  const drawer = action => node('#simple-listen').listeners.click({target:{closest:() => ({dataset:{listenAction:action}})}});
  return {state, audio, calls, paid, tap, key, drawer, node, nodes, session, toasts, app:context.app, listen:context.window.BardicListen};
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
  assert.match(env.node('#simple-listen').innerHTML, /Paused\. Finished simple takes are saved/);
  assert.doesNotMatch(env.node('#simple-listen').innerHTML, /Stopped\./);
  env.app.updatePlayer();
  assert.match(env.node('#player-subtitle').textContent, /^Kore · Gemini · paid · Passage 1/);
  assert.match(env.node('#simple-listen').innerHTML, /Hear example · paid/, 'the paid example is labelled');
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
  assert.match(node('#c').innerHTML, /Full cast \(Studio takes\)/);
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
    NARRATION_LABELS:{system:'Device', gemini:'Gemini', breeze:'Breeze'}, scrollMotion:() => 'auto',
    renderStudio() {}, updateProviderHint() {}, refreshBreeze() {}, auditionPassage() {}, retakeSegment() {}, startSegment() {},
  };
  vm.runInNewContext([
    line('const escapeHTML ='),
    between('async function startJob(', '// A seeded provider (Breeze)'),
    between("$('#render-button').addEventListener('click'", "$('#analysis-provider').addEventListener('change'"),
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
  const context = {state, $:node, $$:() => [], icon:() => '', window:{BardicListen:{getSelection:() => ({mode:listen.mode}), resolve:(_book, segment) => listen.ready.has(segment.id) ? {url:'/a.wav'} : null}},
    busyJob:() => state.jobs.find(job => ['queued','running'].includes(job.status)), updateBusyControls() {}, renderProduction() {},
    syncWorkspaceNavigation() {}, renderLibrary() {}, renderReader() {}, renderCast() {}, renderStudio() {}, renderVoices() {}, setTab() {}, updatePlayer() {}};
  vm.runInNewContext([
    line('const escapeHTML ='), line('const playable ='),
    between('function renderBook(', 'function renderReader('),
    between('const LISTENING_JOB_KINDS', 'async function pollJobs('),
    between("$('#job-banner').addEventListener('click'", '\n'),
    'globalThis.ui = {renderBook, renderBookStatus, renderJob};',
  ].join('\n'), context);
  return {state, node, listen, ui:context.ui};
}

test('the Cast badge counts the cards shown and the book status counts listening audio', () => {
  const env = workspace();
  env.state.book = {...story('book-w'), author:'', characters:[{id:'narrator', name:'Narrator'}, {id:'unassigned', name:'Unassigned'}, {id:'mara', name:'Mara'}, {id:'ivo', name:'Ivo'}]};
  env.ui.renderBook();
  assert.equal(env.node('#cast-count').textContent, 4, 'Narrator and unassigned dialogue have cards too');
  assert.equal(env.node('#book-status').textContent, 'Not narrated yet');
  env.listen.ready = new Set(['s1','s2','s3']);
  env.ui.renderBookStatus();
  assert.equal(env.node('#book-status').textContent, '3 of 3 passages ready with your narrator', 'a fully listened book says so');
  env.state.book.segments[0].audio = {url:'/studio.wav'};
  env.ui.renderBookStatus();
  assert.equal(env.node('#book-status').textContent, '3 of 3 passages ready with your narrator · 1 of 3 recorded in the Studio');
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
    const options = {chapterId:'c1', segmentId:'s1', status:{has_api_key:true, providers:[{id:'system', available:false}, {id:'gemini', available:true}], tts_model:'gemini-3.8-flash-tts', tts_models:['gemini-3.8-flash-tts']}};
    await api.render(panel, book, options);
    await settle();
    await api.render(panel, book, options);
    headlines[status] = panel.innerHTML.match(/<div class="simple-listen-buffer chapter-queue"[^>]*><div><span>([^<]*)<\/span>/)?.[1];
  }
  assert.equal(headlines.failed, 'Chapter preparation failed · finished audio is saved');
  assert.equal(headlines.cancelled, 'Chapter preparation cancelled · finished audio is saved');
  assert.equal(headlines.interrupted, 'Chapter preparation interrupted when Bardic stopped · finished audio is saved');
  assert.equal(headlines.budget_limited, 'Spending allowance reached · finished audio is saved', 'budget_limited is not "Not queued yet"');
  assert.equal(headlines.quota_limited, 'Daily request quota reached · finished audio is saved');
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
