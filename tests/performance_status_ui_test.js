/* The performance detail: ETA and per-chapter status, fallback wording, the fallback narrator choice,
   re-recording, takes, and that leaving the view drops its timers. Mocked fetch; nothing leaves the process. */
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
const book = {id:'book-q', title:'The Lantern', chapters:[{id:'c1', title:'One', kind:'chapter'}, {id:'c2', title:'Two', kind:'chapter'}],
  passages:[{id:'s1', chapter_id:'c1', text:'The lamp went out.'}, {id:'s2', chapter_id:'c2', text:'A door opened.'}]};
const source = name => fs.readFileSync(path.join(__dirname, '../bardic/static', name), 'utf8');
const P = '/api/books/book-q/performances';

const fallback = {provider:'system', voice:'Samantha', automatic:true, label:'Samantha · Mac voice', available:true};
const record = (extra = {}) => ({id:'pf_1', name:'Evening', mode:'simple', provider:'gemini', chapter_ids:['c1', 'c2'], narrator_label:'Kore · Gemini', fallback,
  job:{id:'j1', status:'completed'}, updated_at:'2026-09-29T10:00:00Z', ...extra,
  progress:{passages_total:4, passages_ready:3, seconds_ready:30, passages_fallback:0, passages_rerecorded:0, passages_blocked:0, fallback_provider:null, chapters:[], ...extra.progress}});
const chapter = (id, extra = {}) => ({id, title:id === 'c1' ? 'One' : 'Two', passages_total:2, passages_ready:2, passages_fallback:0, passages_rerecorded:0, passages_blocked:0,
  blocked_passage_ids:[], state:'done', passages_remaining:0, chars_total:4200, chars_ready:4200, chars_remaining:0, seconds_ready:20, run_passages:null, run_done:null, eta_seconds:null, ...extra});
const eta = extra => ({seconds:null, finishes_at:null, basis:'none', chars_remaining:0, passages_remaining:0, rate_chars_per_second:null, requests_remaining:null, requests_left_today:null,
  paused_reason:null, resumes_at:null, note:null, ...extra});
const status = (extra = {}) => ({performance_id:'pf_1', generated_at:'2026-09-29T10:00:00Z', state:'partial', run:null, eta:eta(),
  totals:{passages_total:4, passages_ready:3, passages_fallback:0, passages_rerecorded:0, passages_blocked:0, passages_remaining:1, chars_total:8400, chars_ready:6300, chars_remaining:2100, seconds_ready:30},
  chapters:[chapter('c1'), chapter('c2', {passages_ready:1, passages_remaining:1, state:'partial'})], notes:[], ...extra});
const note = (extra = {}) => ({chapter_id:'c1', passage_id:'s1', reason:'content_blocked', provider:'system', model:null, voice:'Samantha', created_at:'2026-09-29T09:00:00Z', excerpt:'The lamp went out.', ...extra});

// `routes` maps "METHOD suffix" (the path after the performance) to a response; unknown routes answer {}.
// With `hold`, timers of 250 ms or less are kept (not run) so a test can see what is still pending.
function environment({records = [record()], routes = {}, hold = false, options: extra = {}} = {}) {
  const calls = [], jobs = [], pending = new Map(), cleared = [];
  let nextTimer = 1;
  const container = new Container();
  const scope = {window:{}, document:{activeElement:null}, CSS:{escape:value => value},
    setTimeout:(fn, ms) => {
      if (ms >= 1000) return nextTimer++;
      const id = nextTimer++;
      if (hold) pending.set(id, fn); else setImmediate(fn);
      return id;
    },
    clearTimeout:id => { cleared.push(id); pending.delete(id); },
    fetch:async (url, options = {}) => {
      const call = {url, method:options.method || 'GET', body:options.body ? JSON.parse(options.body) : null};
      calls.push(call);
      const suffix = url.startsWith(`${P}/pf_1`) ? url.slice(`${P}/pf_1`.length) : url === P ? '' : url;
      const key = `${call.method} ${suffix}`;
      let data = {};
      if (routes[key] !== undefined) data = typeof routes[key] === 'function' ? routes[key](call) : routes[key];
      else if (key === 'GET ' && url === P) data = {performances:records};
      else if (key === 'GET ') data = {performances:records};
      else if (suffix.endsWith('/rerecord')) data = {performance:records[0], job:{id:'job-rr', status:'queued'}};
      else if (suffix.endsWith('/prepare')) data = {performance:records[0], job:{id:'job-p', status:'queued'}};
      else if (suffix.endsWith('/preview')) data = {passages_total:4, passages_ready:3, passages_to_generate:1, requests_estimate:1, expected_seconds:60, chapters:[], problems:[], notes:[], quota:null};
      const fail = data && data.__status;
      return {ok:!fail, status:fail || 200, json:async () => data};
    }};
  vm.createContext(scope);
  vm.runInContext(source('ui.js'), scope);
  vm.runInContext(source('performances.js'), scope);
  const listen = {getSelection:() => ({provider:'system'}), getPerformance:() => null,
    narratorOptions:(_book, provider) => ({provider, available:true, voice:provider === 'system' ? 'Samantha' : 'Kore', model:provider === 'gemini' ? 'tts-model' : null,
      voices:[{id:provider === 'system' ? 'Samantha' : 'Kore', name:'Voice', usable:true}, {id:'Other', name:'Other', usable:true}]})};
  const options = {listen, onJob:job => jobs.push(job), onPlay() {}, ...extra};
  const click = dataset => container.listeners.click({target:{dataset, closest:() => ({dataset, closest:() => dataset.card ? {dataset:{performance:dataset.card}} : null})}});
  const change = (dataset, value = '', checked = false) => container.listeners.change({target:{dataset, value, checked}});
  const api = scope.window.BardicPerformances;
  return {api, container, options, calls, jobs, pending, cleared, click, change, scope,
    async open() { api.render(container, book, options); await settle(); click({performanceAction:'open', card:'pf_1'}); await settle(); return container.innerHTML; },
    posts: suffix => calls.filter(call => call.method === 'POST' && call.url.endsWith(suffix))};
}
const etaText = html => html.match(/<p class="performance-eta"[^>]*>([^<]*)<\/p>/)?.[1];

test('the estimate sentence says how far to trust the number, and what a pause is waiting for', async () => {
  const cases = [
    [eta({basis:'measured', seconds:2520}), /^About 42 min left · measured from this run$/],
    [eta({basis:'estimated', seconds:4200}), /^About 1 h 10 min left · estimated from earlier chunks$/],
    [eta({basis:'unknown', note:'No chunk has finished yet.'}), /^Speed not measured yet · No chunk has finished yet\.$/],
    [eta({basis:'none'}), /^Nothing left to record$/],
    [eta({basis:'measured', seconds:600, paused_reason:'quota', resumes_at:'2026-09-30T08:00:00Z'}), /^Paused at the daily Gemini limit · resumes .+ · about 10 min of recording left after that$/],
    [eta({basis:'estimated', paused_reason:'budget'}), /^Paused at the spending limit$/],
  ];
  for (const [value, expected] of cases) {
    const env = environment({routes:{'GET /status':status({eta:value})}});
    const html = await env.open();
    assert.match(etaText(html), expected, JSON.stringify(value));
    assert.match(html, /<p class="performance-eta" role="status" aria-live="polite" aria-atomic="true">/, 'a live region');
  }
});

test('the overall bar and every chapter row are labelled, carry a state word, counts, size and an estimate', async () => {
  const chapters = [chapter('c1', {passages_fallback:1, passages_rerecorded:2, passages_blocked:1}),
    chapter('c2', {passages_ready:1, passages_remaining:1, state:'active', run_passages:1, run_done:0, eta_seconds:720})];
  const env = environment({routes:{'GET /status':status({chapters, run:{job_id:'j1', kind:'record', status:'running', message:'Chapter 2 of 2', waiting_seconds:45, issues:[], narrator_label:'Kore', passages_total:1, passages_done:0}})},
    records:[record({job:{id:'j1', status:'running'}})]});
  const html = await env.open();
  assert.match(html, /<progress max="4" value="3" aria-label="All chapters: 3 of 4 passages ready">/);
  const rowOf = id => html.match(new RegExp(`data-chapter-row="${id}"[\\s\\S]*?</div>\\s*<progress[^>]*>[\\s\\S]*?</div>`))[0].replace(/\n\s*/g, '');
  const one = rowOf('c1'), two = rowOf('c2');
  assert.match(one, /data-state="done"/);
  assert.match(one, /class="badge" data-tone="good">Done</);
  assert.match(one, /aria-label="One: 2 of 2 passages ready"/);
  assert.match(one, /2 of 2 ready · 1 read by a fallback voice · 2 re-recorded · 1 blocked by Gemini · 4,200 characters/);
  assert.match(two, /class="badge" data-tone="info">Recording</);
  assert.match(two, /done in about 12 min/);
  assert.match(html, /Recording with Kore · Chapter 2 of 2 · waiting 45 s for the rate limit/);
  assert.match(html, /data-performance-action="rerecord-chapter" data-chapter="c1" disabled/, 'no re-record while a job runs');
  const states = ['done', 'active', 'queued', 'paused', 'stopped', 'blocked', 'partial', 'not_started', 'empty'];
  const label = {done:'Done', active:'Recording', queued:'Queued', paused:'Paused', stopped:'Stopped', blocked:'Blocked', partial:'Partly recorded', not_started:'Not recorded', empty:'No text'};
  const tone = {done:'good', active:'info', queued:'info', paused:'neutral', stopped:'neutral', blocked:'warn', partial:'warn', not_started:'neutral', empty:'neutral'};
  for (const state of states) {
    const shown = await environment({routes:{'GET /status':status({chapters:[chapter('c1', {state}), chapter('c2')]})}}).open();
    assert.match(shown, new RegExp(`data-chapter-row="c1" data-state="${state}"`));
    assert.match(shown, new RegExp(`class="badge" data-tone="${tone[state]}">${label[state]}<`), state);
  }
});

test('run issues are listed as an alert and the notes list is a collapsible with per-passage actions', async () => {
  const issue = {chapter_id:'c2', passage_id:'s2', reason:'failed', outcome:'unrecorded', provider:null, voice:null, at:'2026-09-29T09:30:00Z', message:'Both narrators failed.'};
  const env = environment({routes:{'GET /status':status({run:{job_id:'j1', kind:'record', status:'completed', message:'', waiting_seconds:null, issues:[issue]},
    notes:[note(), note({passage_id:'s2', chapter_id:'c2', reason:'failed', excerpt:'A <door> opened.'})]})}});
  let html = await env.open();
  assert.match(html, /1 passage no narrator could record/);
  assert.match(html, /Both narrators failed\./);
  assert.match(html, /class="callout" data-tone="bad"/);
  assert.match(html, /aria-expanded="false"[^>]*>Passages another narrator reads \(2\)/);
  assert.doesNotMatch(html, /id="performance-notes-list"/, 'collapsed until opened');
  env.click({performanceAction:'toggle-notes'});
  html = env.container.innerHTML;
  assert.match(html, /aria-expanded="true"/);
  assert.match(html, /Gemini blocked this text · Mac voices · Samantha/);
  assert.match(html, /The main narrator could not produce it/);
  assert.match(html, /A &lt;door&gt; opened\./, 'excerpts are escaped');
  for (const action of ['rerecord-note', 'restore-original', 'takes']) assert.match(html, new RegExp(`data-performance-action="${action}" data-segment="s1"`));
  assert.match(html, /Re-record passages read by the fallback voice/);
});

test('fallback wording says why: blocked, could not be produced, or re-recorded; failure never claims Gemini blocked it', async () => {
  const cards = async progress => {
    const env = environment({records:[record({progress})]});
    env.api.render(env.container, book, env.options);
    await settle();
    return env.container.innerHTML;
  };
  const both = await cards({passages_fallback:3, fallback_provider:'system', fallback_reasons:{content_blocked:2, failed:1}, passages_ready:4});
  assert.match(both, /2 passages read by a Mac voice because Gemini blocked them/);
  assert.match(both, /1 passage read by a Mac voice because the main narrator could not produce it/);
  const failed = await cards({passages_fallback:2, fallback_provider:'breeze', fallback_reasons:{content_blocked:0, failed:2}, passages_ready:4});
  assert.match(failed, /2 passages read by Breeze because the main narrator could not produce them/);
  assert.doesNotMatch(failed, /Gemini blocked/);
  const again = await cards({passages_rerecorded:2, passages_ready:4});
  assert.match(again, /2 passages re-recorded with another voice/);
  assert.match(again, /data-state="complete"/);
  const legacy = await cards({passages_fallback:1, fallback_provider:'system', passages_ready:4});
  assert.match(legacy, /1 passage read by a Mac voice because Gemini blocked it/, 'records from before reasons were split meant blocked text');
});

test('a create request carries a fallback only when the user overrides the saved default', async () => {
  const env = environment({routes:{[`POST ${P}/preview`]:{passages_total:2, passages_ready:0, passages_to_generate:2, requests_estimate:2, expected_seconds:60, chapters:[], problems:[], notes:[], quota:null, fallback:{...fallback, label:'Saved voice', automatic:false}}}});
  env.api.render(env.container, book, env.options);
  await settle();
  env.click({performanceAction:'new'});
  await settle();
  assert.match(env.container.innerHTML, /<option value="">Saved default \((?:Saved voice|automatic)\)<\/option>/);
  assert.match(env.container.innerHTML, /Reads any passage the main narrator cannot: text Gemini blocks, or passages that keep failing\./);
  env.container.listeners.submit({preventDefault() {}});
  await settle();
  const first = env.calls.find(call => call.method === 'POST' && call.url === P);
  assert.ok(!('fallback' in first.body), 'the saved default is pinned by the server');
  assert.ok(!env.calls.filter(call => call.url.endsWith('/preview')).some(call => 'fallback' in call.body));
  env.click({performanceAction:'new'});
  await settle();
  env.change({performanceField:'fallback-provider'}, 'gemini');
  await settle();
  assert.deepEqual(env.calls.filter(call => call.url.endsWith('/preview')).at(-1).body.fallback, {provider:'gemini', voice:'Kore'}, 'the preview plans the override');
  env.change({performanceField:'fallback-voice'}, 'Other');
  await settle();
  env.container.listeners.submit({preventDefault() {}});
  await settle();
  const second = env.calls.filter(call => call.method === 'POST' && call.url === P).at(-1);
  assert.deepEqual(second.body.fallback, {provider:'gemini', voice:'Other'});
});

test('the detail shows the pinned fallback and sends a changed one with the resume', async () => {
  const env = environment({routes:{'GET /status':status()}, records:[record({fallback:{...fallback, available:false}})]});
  let html = await env.open();
  assert.match(html, /Fallback narrator: Samantha · Mac voice \(chosen automatically\)/);
  assert.match(html, /That narrator is not set up right now\./);
  env.click({performanceAction:'fallback-change'});
  env.change({performanceField:'detail-fallback-provider'}, 'breeze');
  env.change({performanceField:'detail-fallback-voice'}, 'Other');
  html = env.container.innerHTML;
  assert.match(html, /data-performance-action="record-more" >Record the remaining 1 passage/);
  env.click({performanceAction:'record-more'});
  await settle();
  assert.deepEqual(env.posts('/prepare')[0].body, {fallback:{provider:'breeze', voice:'Other'}});
  assert.equal(env.jobs.at(-1).id, 'job-p');
});

test('re-recording a chapter previews locally, then starts with the chosen voice; only:fallback covers the whole performance', async () => {
  const env = environment({routes:{'GET /status':status({notes:[note()]}),
    'POST /rerecord/preview':call => ({performance_id:'pf_1', provider:call.body.provider, model:'m', voice:call.body.voice, narrator_label:'x', chapter_ids:['c1'], passages_total:2, characters:900, requests_estimate:1, expected_seconds:120, chapters:[], problems:[], notes:['Earlier audio is kept.'], quota:{requests_today:3, rpd:100, resets_at:'2026-09-30T00:00:00Z'}})}});
  await env.open();
  env.click({performanceAction:'rerecord-chapter', chapter:'c1'});
  await settle();
  let html = env.container.innerHTML;
  assert.match(html, /Chapter “One”\. Earlier audio is kept/);
  let preview = env.posts('/rerecord/preview').at(-1).body;
  assert.deepEqual(preview, {provider:'system', voice:'Samantha', model:null, chapter_id:'c1', only:'all'});
  assert.match(html, /2 passages · 900 characters · about 2 min of listening · no charge/);
  assert.match(html, /Earlier audio is kept\.<\/p>/);
  env.click({performanceRrProvider:'gemini'});
  await settle();
  preview = env.posts('/rerecord/preview').at(-1).body;
  assert.deepEqual(preview, {provider:'gemini', voice:'Kore', model:'tts-model', chapter_id:'c1', only:'all'});
  env.change({performanceField:'rr-voice'}, 'Other');
  await settle();
  assert.equal(env.posts('/rerecord/preview').at(-1).body.voice, 'Other');
  assert.match(env.container.innerHTML, />Re-record 2 passages · about 1 paid request · cost unknown<\/button>/);
  assert.equal(env.posts('/rerecord').length, 0, 'nothing is sent before the confirm');
  env.click({performanceAction:'rerecord-start'});
  await settle();
  assert.deepEqual(env.posts('/rerecord')[0].body, {provider:'gemini', voice:'Other', model:'tts-model', chapter_id:'c1', only:'all'});
  assert.equal(env.jobs.at(-1).id, 'job-rr');
  assert.doesNotMatch(env.container.innerHTML, /rerecord-title/, 'the form closes once started');

  env.click({performanceAction:'rerecord-fallback'});
  await settle();
  assert.deepEqual(env.posts('/rerecord/preview').at(-1).body, {provider:'system', voice:'Samantha', model:null, only:'fallback'});
  env.click({performanceAction:'rerecord-start'});
  await settle();
  assert.deepEqual(env.posts('/rerecord').at(-1).body, {provider:'system', voice:'Samantha', model:null, only:'fallback'});
  env.click({performanceAction:'rerecord-note', segment:'s1'});
  await settle();
  assert.deepEqual(env.posts('/rerecord/preview').at(-1).body.passage_ids, ['s1']);
});

test('a re-record problem or refusal shows its message with the hint, and the confirm stays disabled', async () => {
  const env = environment({routes:{'GET /status':status(),
    'POST /rerecord/preview':{performance_id:'pf_1', provider:'gemini', model:'m', voice:'Kore', narrator_label:'x', chapter_ids:[], passages_total:0, characters:0, requests_estimate:0, expected_seconds:0, chapters:[], notes:[],
      problems:[{code:'narrator_unavailable', detail:'Gemini has no API key.'}], quota:null},
    'POST /rerecord':{__status:409, detail:'A job is already running.', code:'job_active'}}});
  await env.open();
  env.click({performanceAction:'rerecord-chapter', chapter:'c1'});
  await settle();
  assert.match(env.container.innerHTML, /Gemini has no API key\. That narrator is not set up\. Add its key or server in Providers &amp; settings/);
  assert.match(env.container.innerHTML, /data-performance-action="rerecord-start" disabled/);
});

test('takes list a passage’s versions, and restoring sends the take or, with none, the original', async () => {
  const take = (id, extra) => ({id, passage_id:'s1', chapter_id:'c1', action:'use', reason:'rerecord', created_at:'2026-09-29T09:00:00Z', restored_from:null, provider:'gemini', model:null, voice:'Kore', voice_label:'Kore · Gemini', error:null, available:true, current:false, audio:{url:`/audio/${id}.wav`}, ...extra});
  const env = environment({routes:{'GET /status':status({notes:[note({reason:'rerecord'})]}),
    'GET /takes?passage_id=s1':{performance_id:'pf_1', takes:[take('t2', {current:true}), take('t1', {reason:'content_blocked', voice_label:'Samantha'})]}}});
  await env.open();
  env.click({performanceAction:'takes', segment:'s1'});
  await settle();
  const html = env.container.innerHTML;
  assert.match(html, /Takes for this passage/);
  assert.match(html, /<strong>Kore · Gemini<\/strong> <span class="badge" data-tone="good">Playing now<\/span>/);
  assert.match(html, /Read after Gemini blocked the text/);
  assert.match(html, /data-performance-action="take-play" data-take="t1"/);
  assert.match(html, /data-performance-action="take-use" data-segment="s1" data-take="t1"/);
  assert.doesNotMatch(html, /data-performance-action="take-use" data-segment="s1" data-take="t2"/, 'the current take is not offered again');
  env.click({performanceAction:'take-use', segment:'s1', take:'t1'});
  await settle();
  assert.deepEqual(env.posts('/takes/restore').at(-1).body, {passage_ids:['s1'], take_id:'t1'});
  assert.match(env.container.innerHTML, /That take now plays for this passage\./);
  env.click({performanceAction:'take-original', segment:'s1'});
  await settle();
  assert.deepEqual(env.posts('/takes/restore').at(-1).body, {passage_ids:['s1']}, 'no take_id returns to the performance’s own audio');
  env.click({performanceAction:'restore-original', segment:'s1'});
  await settle();
  assert.deepEqual(env.posts('/takes/restore').at(-1).body, {passage_ids:['s1']});
  assert.equal(env.calls.filter(call => call.url.includes('/listen')).length, 0, 'the performance player is not started');
});

test('leaving the detail view clears its timers and drops late responses', async () => {
  const env = environment({hold:true, routes:{'GET /status':status({notes:[note()]})}});
  env.api.render(env.container, book, env.options);
  await settle();
  env.click({performanceAction:'open', card:'pf_1'});
  await settle();
  env.click({performanceAction:'rerecord-chapter', chapter:'c1'});
  assert.equal(env.pending.size, 1, 'the debounced preview is pending');
  const [timer] = [...env.pending.keys()];
  env.click({performanceAction:'back'});
  assert.ok(env.cleared.includes(timer), 'clearTimeout ran for the preview timer');
  assert.equal(env.pending.size, 0);
  await settle();
  assert.equal(env.posts('/rerecord/preview').length, 0, 'no preview request is sent after leaving');
  assert.match(env.container.innerHTML, /data-performance-action="new">Create performance/);
  // A book that goes away tears the panel down as well.
  env.click({performanceAction:'open', card:'pf_1'});
  await settle();
  env.click({performanceAction:'rerecord-chapter', chapter:'c2'});
  const second = [...env.pending.keys()][0];
  env.api.render(env.container, null, env.options);
  assert.ok(env.cleared.includes(second));
  assert.equal(env.container.innerHTML, '');
});

test('the status is re-read with each list refresh, so it follows a running job', async () => {
  let running = true;
  const routes = {'GET /status':() => status({eta:eta({basis:running ? 'measured' : 'none', seconds:running ? 120 : null})})};
  const env = environment({routes, records:[record({job:{id:'j1', status:'running'}})]});
  await env.open();
  assert.match(etaText(env.container.innerHTML), /About 2 min left/);
  const reads = () => env.calls.filter(call => call.url.endsWith('/status')).length;
  const before = reads();
  running = false;
  env.click({performanceAction:'stop', card:'pf_1'});
  await settle();
  assert.ok(reads() > before, 'a refresh re-reads the status');
});
