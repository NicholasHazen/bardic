// Analysis tab behavior with mocked HTTP: settings never start work, runs need a
// confirmed plan, acceptance needs a reviewed impact, and model text is escaped.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const REGIONS = ['message', 'runs', 'plan', 'steps', 'detail', 'versions', 'result'];
class Region { constructor() { this.innerHTML = ''; } querySelector() { return null; } }
class Container {
  constructor({hidden = false} = {}) {
    this.hidden = hidden;
    this.listeners = {};
    this.regions = Object.fromEntries(REGIONS.map(name => [name, new Region()]));
    this.html = '';
  }
  set innerHTML(value) { this.html = value; for (const region of Object.values(this.regions)) region.innerHTML = ''; }
  get innerHTML() { return this.html; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  querySelector(selector) {
    const region = selector.match(/^\[data-ap-region="(\w+)"\]$/)?.[1];
    return region ? this.regions[region] || null : null;
  }
}

const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle() { for (let i = 0; i < 20; i++) await tick(); }
const camel = attr => attr.replace(/-(\w)/g, (_, c) => c.toUpperCase());
function click(container, attr, value) {
  const node = {dataset:{[camel(attr)]:value}, disabled:false};
  container.listeners.click({target:{closest:selector => selector === `[data-${attr}]` ? node : null}});
}
function change(container, name, value, {checked, dataset = {}} = {}) {
  container.listeners.change({target:{name, value, checked, dataset}});
}
function input(container, name, value) { container.listeners.input({target:{name, value}}); }

const MODELS = {
  openai:{models:[
    {id:'gpt-a', label:'GPT A', tier:'economy', roles:['preprocess', 'analysis'], input_usd_per_million:.1, output_usd_per_million:.4},
    {id:'gpt-b', label:'GPT B', tier:'deep', roles:['preprocess', 'analysis'], input_usd_per_million:2, output_usd_per_million:8},
    {id:'speech-only', label:'Speech', tier:'balanced', roles:['tts']},
  ]},
  gemini:{models:[{id:'gem-flash', label:'Gem Flash', tier:'economy', roles:['preprocess', 'analysis']}]},
};
const status = {
  analysis_providers:[{id:'gemini', has_api_key:false}, {id:'openai', has_api_key:true}, {id:'anthropic', has_api_key:false}],
  model_catalogs:MODELS, preprocess_models_by_provider:{openai:'gpt-a', gemini:'gem-flash'}, analysis_models_by_provider:{openai:'gpt-b'},
};
function definitions() {
  const step = (id, label, method, scope, extra = {}) => ({id, label, summary:`${label} summary`, method, scope, inputs:[], owns:[`x.${id}`],
    version:1, parallel:1, default_gate:'auto', default_model_role:'analysis', chapter_scoped:false,
    settings:method === 'plain' ? {provider:'local', model:null, gate:'auto', saved:false} : {provider:'openai', model:'gpt-b', gate:'auto', saved:true}, ...extra});
  return {schema_version:1,
    providers:[{id:'gemini', label:'Gemini', configured:false}, {id:'openai', label:'OpenAI', configured:true}, {id:'anthropic', label:'Anthropic', configured:false}],
    steps:[
      step('structure', 'Chapters & titles', 'plain', 'book'),
      step('census', 'Name census', 'plain', 'book'),
      step('discovery', 'Character discovery', 'llm', 'chapter', {chapter_scoped:true, default_model_role:'scan',
        settings:{provider:'openai', model:'gpt-a', gate:'auto', saved:true}}),
      step('profiles', 'Character profiles', 'llm', 'character', {inputs:['discovery'], settings:{provider:'openai', model:'gpt-b', gate:'review', saved:true}}),
      step('directing', 'Speakers <&> delivery', 'llm', 'chapter', {inputs:['discovery', 'profiles'], chapter_scoped:true}),
    ]};
}
function overview(bookId, extra = {}) {
  const state = (id, fields = {}) => ({id, settings:{}, accepted_scopes:0, total_scopes:1, accepted_origins:{}, stale_scopes:[], pending_versions:0, latest:null, ...fields});
  return {book_id:bookId, revision:4, active_run:null, recent_runs:[], chapters:[{id:'c1', title:'The <Gate>', kind:'chapter'}, {id:'front', title:'Contents', kind:'front_matter'}],
    steps:[
      state('structure', {accepted_scopes:1, accepted_origins:{baseline:1}}),
      state('census'),
      state('discovery', {total_scopes:2, pending_versions:1, latest:{id:'v2', state:'candidate', status:'completed'}}),
      state('profiles', {accepted_scopes:1, stale_scopes:['mira'], accepted_origins:{run:1}}),
      state('directing', {total_scopes:2, latest:{id:'d1', state:'candidate', status:'failed'}}),
    ], ...extra};
}
const versions = {items:[
  {id:'v2', origin:'run', provider:'openai', model:'gpt-a', status:'completed', state:'candidate', units:{total:2, done:2, cached:1, failed:0},
    scope_count:1, accepted_scopes:0, unchanged_scopes:[], incomplete_scopes:[], error:null, created_at:'2026-09-27T10:00:00Z', chapter_ids:null},
  {id:'v1', origin:'baseline', provider:null, model:null, status:'completed', state:'superseded', units:{total:0, done:0, cached:0, failed:0},
    scope_count:1, accepted_scopes:0, unchanged_scopes:[], incomplete_scopes:[], error:null, created_at:'2026-09-26T10:00:00Z', chapter_ids:null},
], decisions:[]};
const result = {stats:{mentions:2, distinct_names:2}, columns:[{key:'chapter', label:'Section'}, {key:'name', label:'Name'}, {key:'evidence', label:'Evidence'}],
  rows:[
    {id:'r1', scope:'c1', chapter:'The <Gate>', name:'Mira <script>alert(1)</script>', evidence:'"Stay" said <b>Mira</b>',
      _diff:'changed', _changed:['name'], _previous:{name:'Old <i>name</i>'}},
    {id:'r2', scope:'c1', chapter:'The <Gate>', name:'Elio', evidence:['one', 'two'], _diff:'added'},
  ], diff:{compared_with:'accepted', same:0, changed:1, added:1, removed:0, agreement:0}, total_rows:2, offset:0, limit:200,
  scopes:[{scope:'c1', artifact_id:'a1', accepted:false}], revision:4};
const plan = {steps:[
  {step_id:'structure', label:'Chapters & titles', method:'plain', provider:'local', model:null, units:1, cached_units:0, requests:0,
    estimated_input_tokens:0, output_token_allowance:0, estimated_cost_usd:0, inputs_pending:[], scopes:1},
  {step_id:'discovery', label:'Character discovery', method:'llm', provider:'openai', model:'gpt-a', units:2, cached_units:1, requests:1,
    estimated_input_tokens:5000, output_token_allowance:2000, estimated_cost_usd:null, inputs_pending:[], scopes:1},
], requests:1, cached_units:1, estimated_input_tokens:5000, output_token_allowance:2000, estimated_cost_usd:null, fingerprint:'fp-123', note:'Estimates <note>'};
const impact = {changed_scopes:['c1'], unchanged_scopes:[], conflicts:[{scope:'c1', item_id:'mira', field:'description', reason:'Edited <manually>'}],
  audio_takes_invalidated:3, downstream_steps_affected:['profiles'], revision:4};

function ordinary(call) {
  const url = new URL(call.url, 'http://localhost');
  const bookId = decodeURIComponent(url.pathname.split('/')[3] || '');
  if (call.method === 'GET' && url.pathname === '/api/analysis-pipeline') return {data:definitions()};
  if (call.method === 'PUT') return {data:{...call.body, saved:true}};
  if (call.method === 'GET' && url.pathname.endsWith('/analysis-pipeline')) return {data:overview(bookId)};
  if (call.method === 'GET' && url.pathname.endsWith('/versions')) return {data:versions};
  if (call.method === 'GET' && url.pathname.includes('/versions/')) return {data:result};
  if (call.method === 'POST' && url.pathname.endsWith('/plan')) return {data:plan};
  if (call.method === 'POST' && url.pathname.endsWith('/runs')) return {data:{job:{id:'job-1', book_id:bookId, kind:'pipeline', status:'queued'}, run:{id:'run-1', status:'queued', steps:call.body.steps}}};
  if (call.method === 'POST' && url.pathname.endsWith('/preview')) return {data:impact};
  if (call.method === 'POST' && url.pathname.endsWith('/accept')) return {data:{...impact, revision:5}};
  if (call.method === 'POST' && url.pathname.endsWith('/reject')) return {data:{action:'reject'}};
  throw new Error(`Unexpected ${call.method} ${call.url}`);
}

function environment(handler = ordinary, {observer = false, globals = {}} = {}) {
  const calls = [];
  const timers = [];
  const observers = [];
  const scope = {window:{}, URLSearchParams, console, ...globals,
    setTimeout:(fn, ms) => { timers.push({fn, ms, cleared:false}); return timers.length; },
    clearTimeout:id => { if (timers[id - 1]) timers[id - 1].cleared = true; },
    fetch:async (url, options = {}) => {
      const call = {url, method:options.method || 'GET', body:options.body ? JSON.parse(options.body) : undefined};
      calls.push(call);
      const value = await handler(call);
      return {ok:value.ok !== false, status:value.status || 200, json:async () => value.data};
    }};
  if (observer) scope.MutationObserver = class { constructor(fn) { this.fn = fn; observers.push(this); } observe() {} };
  // ui.js (the one escape helper and the primitives) loads first, as in index.html.
  const context = vm.createContext(scope);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../bardic/static/ui.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../bardic/static/analysis-pipeline.js'), 'utf8'), context);
  return {calls, timers, observers, render:scope.window.BardicAnalysisPipeline.render, api:scope.window.BardicAnalysisPipeline,
    writes:() => calls.filter(call => call.method !== 'GET')};
}
const book = {id:'book/1', revision:4, chapters:[{id:'c1', title:'The <Gate>'}], characters:[{id:'mira', name:'Mira & Co'}]};

test('renders steps, status chips and an escaped, diffed result table from GET requests only', async () => {
  const env = environment();
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  assert.ok(env.calls.every(call => call.method === 'GET'));
  assert.deepEqual(env.calls.slice(0, 2).map(call => call.url).sort(), ['/api/analysis-pipeline', '/api/books/book%2F1/analysis-pipeline']);
  assert.ok(env.calls.some(call => call.url === '/api/books/book%2F1/analysis-pipeline/steps/discovery/versions?limit=50'), 'the step waiting for review is selected');
  const read = env.calls.find(call => call.url.includes('/versions/v2?'));
  assert.ok(read.url.includes('compare=accepted') && read.url.includes('limit=200') && read.url.includes('offset=0'));
  const steps = container.regions.steps.innerHTML;
  assert.ok(steps.includes('Speakers &lt;&amp;&gt; delivery'));
  assert.ok(!steps.includes('Speakers <&>'));
  for (const text of ['Local', 'Model', 'Waiting for review', 'In use (from earlier work)', 'Failed', 'Not run']) assert.ok(steps.includes(text), text);
  // One state per step: staleness and accepted counts are not extra chips.
  assert.ok(!steps.includes('Stale (') && !steps.includes('Accepted 1/1') && !steps.includes('Needs Character discovery'));
  assert.equal((steps.match(/class="ap-chip /g) || []).length, 5, 'exactly one state chip per step');
  assert.ok(!steps.includes('Run steps in this order'), 'the list does not claim a fixed order');
  assert.ok(steps.includes('Steps do not have to run in order'));
  const detail = container.regions.detail.innerHTML;
  assert.ok(detail.includes('Character discovery'));
  assert.ok(detail.includes('Economy · quick and inexpensive') && detail.includes('Deep · most thorough'));
  assert.ok(detail.includes('Gemini · no API key'));
  assert.ok(!detail.includes('speech-only'), 'models without an analysis role are not offered');
  assert.ok(detail.includes('All story sections') && detail.includes('The &lt;Gate&gt;'));
  assert.ok(detail.includes('Hold for my review'));
  const history = container.regions.versions.innerHTML;
  assert.ok(history.indexOf('data-ap-version="v2"') < history.indexOf('data-ap-version="v1"'));
  assert.ok(history.includes('Recorded from existing work; producer unknown.'));
  assert.ok(history.includes('2/2 units done · 1 reused · 0 failed'));
  const table = container.regions.result.innerHTML;
  assert.ok(table.includes('Mira &lt;script&gt;alert(1)&lt;/script&gt;'));
  assert.ok(table.includes('&quot;Stay&quot; said &lt;b&gt;Mira&lt;/b&gt;'));
  assert.ok(!/<script|<b>|<i>/.test(table), 'model output is never interpreted as HTML');
  assert.ok(table.includes('ap-cell-changed') && table.includes('Old &lt;i&gt;name&lt;/i&gt;'));
  assert.ok(table.includes('title="Previously: Old &lt;i&gt;name&lt;/i&gt;"'));
  assert.ok(table.includes('>New<') && table.includes('one, two'));
  assert.ok(table.includes('>Accept<') && table.includes('>Set aside<') && !table.includes('>Reject<'));
  assert.ok(table.includes('1 changed · 1 new'));
  // The Section column already names each result, so there is no repeated Result column.
  assert.ok(!table.includes('>Result<') && table.includes('>Section<') && !table.includes('class="ap-group"'));

  // Selecting the steps reveals plain-step and stale notes.
  click(container, 'ap-step', 'structure');
  await settle();
  assert.ok(container.regions.detail.innerHTML.includes('Runs locally, free.'));
  assert.ok(container.regions.detail.innerHTML.includes('recorded from existing work; producer unknown'));
  click(container, 'ap-step', 'profiles');
  await settle();
  assert.ok(container.regions.detail.innerHTML.includes('accepted using inputs that have since changed: Mira &amp; Co'));
  assert.equal(env.writes().length, 0);
});

test('an unseen tab fetches nothing until it is shown', async () => {
  const env = environment(ordinary, {observer:true});
  const container = new Container({hidden:true});
  await env.render(container, book, {status});
  await settle();
  assert.equal(env.calls.length, 0);
  container.hidden = false;
  env.observers[0].fn();
  await settle();
  assert.ok(env.calls.some(call => call.url === '/api/books/book%2F1/analysis-pipeline'));
});

test('changing model, provider, custom model or gate saves settings and never starts work', async () => {
  const env = environment();
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  change(container, 'ap_model', 'gpt-b');
  await settle();
  let put = env.writes().at(-1);
  assert.equal(put.method, 'PUT');
  assert.equal(put.url, '/api/analysis-pipeline/steps/discovery/settings');
  assert.deepEqual(put.body, {provider:'openai', model:'gpt-b', gate:'auto'});
  change(container, 'ap_provider', 'gemini');
  await settle();
  assert.deepEqual(env.writes().at(-1).body, {provider:'gemini', model:'gem-flash', gate:'auto'}, 'the scan-role default model follows the provider');
  const before = env.writes().length;
  change(container, 'ap_model', '__custom__');
  await settle();
  assert.equal(env.writes().length, before, 'opening the custom field saves nothing');
  assert.ok(container.regions.detail.innerHTML.includes('name="ap_custom_model"'));
  change(container, 'ap_custom_model', 'bad model!');
  await settle();
  assert.equal(env.writes().length, before);
  assert.ok(container.regions.detail.innerHTML.includes('starting with a letter or number'));
  change(container, 'ap_custom_model', 'my-model:1');
  await settle();
  assert.deepEqual(env.writes().at(-1).body, {provider:'gemini', model:'my-model:1', gate:'auto'});
  change(container, 'ap_custom_model', 'my-model:1');
  await settle();
  assert.equal(env.writes().length, before + 1, 'the same custom value is saved once');
  change(container, 'ap_gate', 'review');
  await settle();
  assert.deepEqual(env.writes().at(-1).body, {provider:'gemini', model:'my-model:1', gate:'review'});
  // Plain steps always send the local provider without a model.
  click(container, 'ap-step', 'census');
  await settle();
  change(container, 'ap_gate', 'review');
  await settle();
  assert.deepEqual(env.writes().at(-1).body, {provider:'local', model:null, gate:'review'});
  assert.ok(env.writes().every(call => call.method === 'PUT'), 'no plan or run request was made');
});

test('a failed settings save restores the previous choice', async () => {
  const env = environment(call => call.method === 'PUT' ? {ok:false, status:400, data:{detail:'Choose a valid model ID.'}} : ordinary(call));
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  change(container, 'ap_model', 'gpt-b');
  await settle();
  assert.ok(container.regions.message.innerHTML.includes('Choose a valid model ID.'));
  assert.ok(container.regions.detail.innerHTML.includes('value="gpt-a" selected'));
});

test('Run this step previews a plan and runs only after Confirm, with the plan fingerprint', async () => {
  const env = environment();
  const container = new Container();
  const started = [];
  await env.render(container, book, {status, onJobStarted:async job => { started.push(job); }});
  await settle();
  assert.ok(!container.regions.steps.innerHTML.includes('type="checkbox"'), 'steps are run one at a time');
  for (const region of Object.values(container.regions)) assert.ok(!/plan-selected|"ap_mode"|ap_include/.test(region.innerHTML));
  const detail = container.regions.detail.innerHTML;
  assert.ok(detail.includes('name="ap_concurrency"') && detail.includes('name="ap_fresh"') && detail.includes('>Run this step<'));
  assert.ok(!/name="ap_(max_requests|budget|budget_on)"/.test(detail), 'no limit fields are offered');
  change(container, 'ap_chapter', 'c1');
  change(container, 'ap_concurrency', '3');
  click(container, 'ap-action', 'plan-step');
  await settle();
  const planned = env.writes();
  assert.equal(planned.length, 1);
  assert.equal(planned[0].url, '/api/books/book%2F1/analysis-pipeline/plan');
  assert.deepEqual(planned[0].body, {steps:['discovery'], chapter_ids:['c1'], fresh:false, configs:{discovery:{provider:'openai', model:'gpt-a'}}});
  const preview = container.regions.plan.innerHTML;
  assert.ok(preview.includes('Unknown price') && preview.includes('Estimates &lt;note&gt;'));
  assert.ok(preview.includes('Sections: The &lt;Gate&gt;'));
  assert.ok(preview.includes('Confirm and run · about 1 request'));
  assert.ok(preview.includes('3 requests at once') && preview.includes('There is no request or dollar cap'));
  assert.ok(!env.calls.some(call => call.url.endsWith('/runs')), 'previewing never starts work');

  change(container, 'ap_fresh', '', {checked:true});
  assert.equal(container.regions.plan.innerHTML, '', 'fresh samples change the estimate, so the preview closes');
  assert.ok(container.regions.message.innerHTML.includes('Fresh samples changed'));
  click(container, 'ap-action', 'plan-step');
  await settle();
  assert.equal(env.writes().at(-1).body.fresh, true, 'the estimate is made for fresh samples');
  click(container, 'ap-action', 'confirm-run');
  await settle();
  const run = env.calls.find(call => call.url.endsWith('/runs'));
  assert.equal(run.method, 'POST');
  assert.deepEqual(run.body, {steps:['discovery'], chapter_ids:['c1'], configs:{discovery:{provider:'openai', model:'gpt-a'}},
    gates:{discovery:'auto'}, scheduling:'serial', concurrency:3, fresh:true, expected_fingerprint:'fp-123'});
  assert.deepEqual(started.map(job => job.id), ['job-1']);
  assert.equal(container.regions.plan.innerHTML, '');
  assert.equal(env.calls.filter(call => call.url.endsWith('/runs')).length, 1);

  // Local steps have nothing to send or reuse: no request options.
  click(container, 'ap-step', 'census');
  await settle();
  assert.ok(!container.regions.detail.innerHTML.includes('name="ap_concurrency"') && container.regions.detail.innerHTML.includes('>Run this step<'));
});

test('a changed setting closes an open preview; a stale fingerprint asks for a new preview', async () => {
  const env = environment(call => call.url.endsWith('/runs') ? {ok:false, status:409, data:{detail:'The plan changed since the preview.', code:'plan_stale'}} : ordinary(call));
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  click(container, 'ap-action', 'plan-step');
  await settle();
  assert.deepEqual(env.writes()[0].body.steps, ['discovery']);
  change(container, 'ap_gate', 'review');
  await settle();
  assert.equal(container.regions.plan.innerHTML, '');
  click(container, 'ap-action', 'confirm-run');
  await settle();
  assert.ok(!env.calls.some(call => call.url.endsWith('/runs')), 'a closed preview cannot be confirmed');

  click(container, 'ap-action', 'plan-step');
  await settle();
  click(container, 'ap-action', 'confirm-run');
  await settle();
  assert.equal(env.calls.filter(call => call.url.endsWith('/runs')).length, 1);
  assert.ok(!('limits' in env.calls.find(call => call.url.endsWith('/runs')).body), 'runs carry no limits');
  assert.ok(container.regions.plan.innerHTML.includes('The plan changed since the preview.'));
  assert.ok(container.regions.plan.innerHTML.includes('Preview again'));
  assert.ok(!container.regions.plan.innerHTML.includes('confirm-run'));
});

test('runs are blocked while another job or a pipeline run is active; active runs poll', async () => {
  const env = environment();
  const container = new Container();
  await env.render(container, book, {status, busy:true});
  await settle();
  assert.ok(/data-ap-action="plan-step"[^>]*disabled/.test(container.regions.detail.innerHTML));
  click(container, 'ap-action', 'plan-step');
  await settle();
  assert.equal(env.writes().length, 0);

  let reads = 0;
  const active = environment(call => {
    if (call.method === 'GET' && call.url === '/api/books/book%2F1/analysis-pipeline') {
      reads++;
      return {data:overview('book/1', reads < 3 ? {active_run:{id:'run-1', status:'running', steps:['census'], created_at:'2026-09-27T10:00:00Z'}} : {})};
    }
    return ordinary(call);
  });
  const activeContainer = new Container();
  await active.render(activeContainer, book, {status});
  await settle();
  assert.ok(activeContainer.regions.runs.innerHTML.includes('Run in progress'));
  const pending = active.timers.filter(timer => !timer.cleared);
  assert.equal(pending.length, 1);
  assert.equal(pending[0].ms, 2000);
  pending[0].fn();
  await settle();
  assert.equal(reads, 2);
  active.timers.filter(timer => !timer.cleared).at(-1).fn();
  await settle();
  assert.equal(reads, 3);
  assert.ok(!activeContainer.regions.runs.innerHTML.includes('Run in progress'));
  const before = active.timers.length;
  await settle();
  assert.equal(active.timers.length, before, 'polling stops after the run');
});

test('accept previews impact, confirms with the expected revision and reloads the book', async () => {
  const env = environment();
  const container = new Container();
  let reloaded = 0;
  await env.render(container, book, {status, onBookChanged:async () => { reloaded++; }});
  await settle();
  click(container, 'ap-action', 'accept');
  await settle();
  const previewCall = env.writes().at(-1);
  assert.equal(previewCall.url, '/api/books/book%2F1/analysis-pipeline/steps/discovery/versions/v2/preview');
  assert.ok(!env.calls.some(call => call.url.endsWith('/accept')), 'the impact preview does not accept');
  const panel = container.regions.result.innerHTML;
  assert.ok(panel.includes('Kept your edits (1)'));
  assert.ok(panel.includes('Edited &lt;manually&gt;'));
  // Conflicts name the character, not its ID.
  assert.ok(panel.includes('<strong>Mira &amp; Co</strong> · your description') && !panel.includes('<code>mira</code>'));
  assert.ok(panel.includes('3 narrated takes will need re-rendering'));
  assert.ok(panel.includes('Character profiles'));
  assert.ok(panel.includes('The &lt;Gate&gt;'));
  click(container, 'ap-action', 'confirm-accept');
  await settle();
  const accepted = env.calls.find(call => call.url.endsWith('/accept'));
  assert.equal(accepted.url, '/api/books/book%2F1/analysis-pipeline/steps/discovery/versions/v2/accept');
  assert.deepEqual(accepted.body, {expected_revision:4});
  assert.equal(reloaded, 1);
  assert.ok(container.regions.message.innerHTML.includes('Accepted.'));

  // Earlier and baseline versions are restored, and cannot be rejected.
  click(container, 'ap-version', 'v1');
  await settle();
  const restore = container.regions.result.innerHTML;
  assert.ok(restore.includes('>Restore<'));
  assert.ok(!restore.includes('>Set aside<'));
  assert.ok(restore.includes('Recorded from existing work; producer unknown.'));
});

test('a failed acceptance changes nothing and requires a fresh impact review', async () => {
  const env = environment(call => call.url.endsWith('/accept') ? {ok:false, status:400, data:{detail:'The book changed since this preview.'}} : ordinary(call));
  const container = new Container();
  let reloaded = 0;
  await env.render(container, book, {status, onBookChanged:async () => { reloaded++; }});
  await settle();
  click(container, 'ap-action', 'accept');
  await settle();
  click(container, 'ap-action', 'confirm-accept');
  await settle();
  assert.equal(reloaded, 0);
  const panel = container.regions.result.innerHTML;
  assert.ok(panel.includes('Nothing was changed: The book changed since this preview.'));
  assert.ok(panel.includes('Review the impact again'));
  assert.ok(!panel.includes('confirm-accept'));
});

test('accept waits while another job changes the book, and identical versions are not offered', async () => {
  const env = environment();
  const container = new Container();
  // A listening or render job (not a pipeline run) is active: the server would refuse to accept.
  await env.render(container, book, {status, busy:true});
  await settle();
  const panel = container.regions.result.innerHTML;
  assert.match(panel, /data-ap-action="accept"[^>]*disabled/);
  assert.ok(panel.includes('Accept or restore after it finishes.'));
  click(container, 'ap-action', 'accept');
  await settle();
  assert.ok(!env.calls.some(call => call.url.endsWith('/preview')), 'no impact request while blocked');

  const same = environment(call => call.method === 'GET' && new URL(call.url, 'http://localhost').pathname.endsWith('/versions')
    ? {data:{items:[{...versions.items[0], state:'same_as_accepted'}], decisions:[]}} : ordinary(call));
  const other = new Container();
  await same.render(other, book, {status});
  await settle();
  assert.ok(!other.regions.result.innerHTML.includes('data-ap-action="accept"'));
  assert.ok(other.regions.result.innerHTML.includes('Same as accepted') || other.regions.versions?.innerHTML?.includes('Same as accepted'));
});

test('reject is offered for candidates and refreshes the history', async () => {
  const env = environment();
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  click(container, 'ap-action', 'reject');
  await settle();
  assert.equal(env.writes().at(-1).url, '/api/books/book%2F1/analysis-pipeline/steps/discovery/versions/v2/reject');
  assert.ok(container.regions.message.innerHTML.includes('Set aside. The version stays in history'));
});

test('filters and pagination request the right slice of rows', async () => {
  const env = environment(call => call.method === 'GET' && call.url.includes('/versions/v2?')
    ? {data:{...result, total_rows:450, offset:Number(new URL(call.url, 'http://x').searchParams.get('offset'))}} : ordinary(call));
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  click(container, 'ap-action', 'next-page');
  await settle();
  assert.ok(env.calls.at(-1).url.includes('offset=200'));
  change(container, 'ap_changed_only', '', {checked:true});
  await settle();
  let query = new URL(env.calls.at(-1).url, 'http://x').searchParams;
  assert.equal(query.get('changed_only'), 'true');
  assert.equal(query.get('offset'), '0');
  change(container, 'ap_scope', 'c1');
  await settle();
  assert.equal(new URL(env.calls.at(-1).url, 'http://x').searchParams.get('scope'), 'c1');
  change(container, 'ap_compare', 'none');
  await settle();
  query = new URL(env.calls.at(-1).url, 'http://x').searchParams;
  assert.equal(query.get('compare'), 'none');
  assert.equal(query.get('changed_only'), null);
  change(container, 'ap_compare', 'v1');
  await settle();
  assert.equal(new URL(env.calls.at(-1).url, 'http://x').searchParams.get('compare'), 'v1');
  assert.equal(env.writes().length, 0);
});

test('responses for a previous book are discarded after switching books', async () => {
  let releaseOld;
  const env = environment(call => {
    if (call.method === 'GET' && call.url === '/api/books/old/analysis-pipeline') {
      return new Promise(resolve => { releaseOld = () => resolve({data:overview('old', {recent_runs:[{id:'r', status:'failed', steps:['census'], error:'Old book <secret>'}]})}); });
    }
    return ordinary(call);
  });
  const container = new Container();
  const first = env.render(container, {id:'old', revision:1, chapters:[], characters:[]}, {status});
  await tick();
  await env.render(container, {id:'new', revision:1, chapters:[], characters:[]}, {status});
  await settle();
  releaseOld();
  await first;
  await settle();
  assert.ok(!container.regions.runs.innerHTML.includes('Old book'));
  assert.ok(container.regions.steps.innerHTML.includes('Waiting for review'));
  assert.ok(env.calls.some(call => call.url === '/api/books/new/analysis-pipeline'));

  // Clearing the book empties the view and discards pending work.
  await env.render(container, null, {status});
  assert.equal(container.innerHTML, '');
});

test('a step whose required inputs have no accepted result says so and cannot run', async () => {
  // Discovery has only a version waiting for review; profiles reads discovery.
  const env = environment();
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  // Profiles has accepted results from earlier work, so it is in use rather than "needs".
  assert.ok(container.regions.steps.innerHTML.includes('data-ap-state="in-use-earlier">In use (from earlier work)'));
  click(container, 'ap-step', 'profiles');
  await settle();
  const detail = container.regions.detail.innerHTML;
  assert.ok(detail.includes('Not ready to run.'));
  assert.ok(detail.includes('Character profiles needs accepted results from Character discovery.'));
  assert.ok(detail.includes('has a version waiting for your review: accept it first'));
  assert.match(detail, /data-ap-action="plan-step"[^>]*disabled/);
  assert.ok(detail.includes('data-ap-step="discovery"') && detail.includes('Go to Character discovery'));
  click(container, 'ap-action', 'plan-step');
  await settle();
  assert.equal(env.writes().length, 0, 'no plan is requested for a step that cannot run');
  // Going to the needed step opens it, and it can run.
  click(container, 'ap-step', 'discovery');
  await settle();
  assert.ok(container.regions.detail.innerHTML.includes('data-ap-key="detail-heading">Character discovery</h3>'));
  assert.doesNotMatch(container.regions.detail.innerHTML, /data-ap-action="plan-step"[^>]*disabled/);
});

test('the server decides missing inputs in a preview', async () => {
  // The overview says discovery is accepted; the server knows better by the time of the preview.
  const env = environment(call => {
    if (call.method === 'GET' && call.url === '/api/books/book%2F1/analysis-pipeline') {
      const value = overview('book/1');
      value.steps.find(step => step.id === 'discovery').has_accepted = true;
      return {data:value};
    }
    if (call.url.endsWith('/plan')) return {data:{...plan, missing_inputs:{profiles:['discovery']}}};
    return ordinary(call);
  });
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  click(container, 'ap-step', 'profiles');
  await settle();
  assert.ok(!container.regions.detail.innerHTML.includes('Not ready to run.'));
  click(container, 'ap-action', 'plan-step');
  await settle();
  const preview = container.regions.plan.innerHTML;
  assert.match(preview, /data-ap-action="confirm-run"[^>]*disabled/);
  assert.ok(preview.includes('Character profiles needs accepted results from Character discovery.'));
});

test('leaving the tab discards selections, previews and messages; the next visit starts clean', async () => {
  const lastRun = {id:'run-9', status:'failed', steps:['census'], created_at:'2026-09-27T10:00:00Z', error:'Census <broke>'};
  const env = environment(call => call.method === 'GET' && call.url === '/api/books/book%2F1/analysis-pipeline'
    ? {data:overview('book/1', {recent_runs:[lastRun]})} : ordinary(call), {observer:true});
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  assert.ok(container.regions.runs.innerHTML.includes('Census &lt;broke&gt;'), 'a finished run is reported');
  change(container, 'ap_fresh', '', {checked:true});
  change(container, 'ap_concurrency', '4');
  change(container, 'ap_chapter', 'c1');
  click(container, 'ap-action', 'plan-step');
  await settle();
  assert.ok(container.regions.plan.innerHTML.includes('Review before running'));
  click(container, 'ap-step', 'census');
  await settle();
  change(container, 'ap_gate', 'review');
  await settle();
  assert.ok(container.regions.message.innerHTML.includes('Saved settings'));

  container.hidden = true;
  env.observers[0].fn();
  await settle();
  const before = env.calls.length;
  container.hidden = false;
  env.observers[0].fn();
  await settle();
  assert.ok(env.calls.length > before, 'returning reloads the book state');
  assert.equal(container.regions.plan.innerHTML, '');
  assert.equal(container.regions.message.innerHTML, '');
  const detail = container.regions.detail.innerHTML;
  assert.ok(detail.includes('data-ap-key="detail-heading">Character discovery</h3>'), 'the step waiting for review opens again');
  assert.ok(!detail.includes('data-ap-key="fresh" checked') && detail.includes('<option value="2" selected>'));
  assert.ok(detail.includes('<option value="">All story sections</option>') && !detail.includes('value="c1" selected'));
  assert.ok(!container.regions.runs.innerHTML.includes('Census &lt;broke&gt;'), 'a run already shown is not reported again');
});

test('previews and messages close when their step changes; a finished run can be dismissed', async () => {
  const env = environment(call => call.method === 'GET' && call.url === '/api/books/book%2F1/analysis-pipeline'
    ? {data:overview('book/1', {recent_runs:[{id:'run-9', status:'completed', steps:['census'], created_at:'2026-09-27T10:00:00Z'}]})} : ordinary(call));
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  click(container, 'ap-action', 'plan-step');
  await settle();
  assert.ok(container.regions.plan.innerHTML.includes('Review before running'));
  change(container, 'ap_concurrency', '1');
  assert.ok(container.regions.plan.innerHTML.includes('1 request at once'), 'requests at once only relabels the preview');
  change(container, 'ap_fresh', '', {checked:true});
  assert.ok(container.regions.message.innerHTML.includes('Fresh samples changed'));
  click(container, 'ap-step', 'census');
  await settle();
  assert.equal(container.regions.plan.innerHTML, '');
  assert.equal(container.regions.message.innerHTML, '', 'a message does not follow you to another step');
  click(container, 'ap-action', 'plan-step');
  await settle();
  click(container, 'ap-step', 'structure');
  await settle();
  assert.equal(container.regions.plan.innerHTML, '', 'a preview belongs to its step');

  assert.ok(container.regions.runs.innerHTML.includes('Latest run'));
  click(container, 'ap-action', 'dismiss-run');
  assert.ok(!container.regions.runs.innerHTML.includes('Latest run'));
});

test('a preview being confirmed stays open, so a failed start is reported even after browsing', async () => {
  let reject;
  const env = environment(call => call.url.endsWith('/runs')
    ? new Promise(resolve => { reject = () => resolve({ok:false, status:500, data:{detail:'Worker <down>'}}); }) : ordinary(call));
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  click(container, 'ap-action', 'plan-step');
  await settle();
  click(container, 'ap-action', 'confirm-run');
  await settle();
  click(container, 'ap-step', 'census');
  await settle();
  assert.ok(container.regions.plan.innerHTML.includes('Starting…'), 'the starting preview is not discarded');
  reject();
  await settle();
  assert.ok(container.regions.plan.innerHTML.includes('Could not start the run: Worker &lt;down&gt;'));
});

test('responses arriving after the tab was left leave no message or preview behind', async () => {
  let release;
  const env = environment(call => call.url.endsWith('/plan')
    ? new Promise(resolve => { release = () => resolve({data:plan}); }) : ordinary(call), {observer:true});
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  click(container, 'ap-action', 'plan-step');
  await settle();
  container.hidden = true;
  env.observers[0].fn();
  release();
  await settle();
  change(container, 'ap_gate', 'review');
  await settle();
  container.hidden = false;
  env.observers[0].fn();
  await settle();
  assert.equal(container.regions.plan.innerHTML, '', 'the late estimate is discarded');
  assert.equal(container.regions.message.innerHTML, '');
});

test('self-hosted providers: per-step choices, no model for services, URL wording and free service calls', async () => {
  const selfHosted = () => {
    const value = definitions();
    value.providers.push({id:'local_llm', label:'Local LLM', kind:'model', self_hosted:true, needs:'url', configured:true,
                          models:[{id:'qwen-local', label:'Qwen <local>', tier:'balanced', roles:['analysis', 'preprocess'],
                                   input_usd_per_million:0, output_usd_per_million:0}]},
                         {id:'booknlp', label:'BookNLP', kind:'service', self_hosted:true, needs:'url', configured:true},
                         {id:'novel_analyzer', label:'Novel Analyzer', kind:'service', self_hosted:true, needs:'url', configured:false});
    for (const step of value.steps) if (step.method === 'llm') step.providers = ['gemini', 'openai', 'anthropic', 'local_llm'];
    const directing = value.steps.find(step => step.id === 'directing');
    directing.providers = [...directing.providers, 'novel_analyzer', 'booknlp'];
    directing.offline_providers = ['booknlp'];
    value.steps.splice(3, 0, {id:'quotes', label:'Quote attribution (BookNLP)', summary:'s', method:'service', scope:'chapter', inputs:['discovery'], requires:[],
      owns:[], version:1, parallel:1, default_gate:'auto', default_model_role:'analysis', chapter_scoped:true, providers:['booknlp'],
      settings:{provider:'booknlp', model:null, gate:'auto', saved:false}});
    return value;
  };
  const servicePlan = {steps:[{step_id:'quotes', label:'Quote attribution (BookNLP)', method:'service', provider:'booknlp', model:null,
    units:2, cached_units:0, requests:0, service_calls:2, estimated_input_tokens:0, output_token_allowance:0, estimated_cost_usd:0,
    inputs_pending:[], scopes:2}], requests:0, service_calls:2, cached_units:0, estimated_input_tokens:0, output_token_allowance:0,
    estimated_cost_usd:0, fingerprint:'fp-s', note:''};
  const env = environment(call => {
    if (call.method === 'GET' && call.url === '/api/analysis-pipeline') return {data:selfHosted()};
    if (call.method === 'POST' && call.url.endsWith('/plan')) return {data:servicePlan};
    return ordinary(call);
  });
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  click(container, 'ap-step', 'discovery');
  await settle();
  let detail = container.regions.detail.innerHTML;
  assert.ok(detail.includes('value="local_llm"') && !detail.includes('value="booknlp"'), 'model steps do not offer chapter services');
  click(container, 'ap-step', 'quotes');
  await settle();
  detail = container.regions.detail.innerHTML;
  assert.ok(detail.includes('value="booknlp"') && !detail.includes('value="openai"') && !detail.includes('name="ap_model"'));
  assert.ok(detail.includes('your own server'));
  click(container, 'ap-step', 'directing');
  await settle();
  assert.ok(container.regions.detail.innerHTML.includes('Novel Analyzer · your server · no server URL'));
  change(container, 'ap_provider', 'novel_analyzer');
  await settle();
  assert.deepEqual(env.writes().at(-1).body, {provider:'novel_analyzer', model:null, gate:'auto'}, 'a service is saved without a model');
  assert.ok(!container.regions.detail.innerHTML.includes('name="ap_model"'));
  assert.ok(container.regions.detail.innerHTML.includes('has no server URL'));
  change(container, 'ap_provider', 'local_llm');
  await settle();
  assert.deepEqual(env.writes().at(-1).body, {provider:'local_llm', model:'qwen-local', gate:'auto'});
  click(container, 'ap-step', 'quotes');
  await settle();
  click(container, 'ap-action', 'plan-step');
  await settle();
  const planned = env.calls.find(call => call.url.endsWith('/plan'));
  assert.deepEqual(planned.body.configs, {quotes:{provider:'booknlp', model:null}});
  const shown = container.regions.plan.innerHTML;
  assert.ok(shown.includes('BookNLP · your server · free') && shown.includes('2 service calls') && shown.includes('Calls to your servers'));
  assert.ok(shown.includes('Confirm and run · 2 calls to your servers') && !shown.includes('$0.00'));
  assert.ok(shown.includes('use that machine’s GPU') && shown.includes('Service calls are not counted as model requests'));

  // Readiness follows the refreshed status: a URL saved in Settings counts without reloading definitions.
  click(container, 'ap-action', 'cancel-plan');
  await env.render(container, book, {status:{...status, local_service_urls:{booknlp:'http://nlp:8100', novel_analyzer:'http://nlp:8200', local_llm:''}}});
  await settle();
  click(container, 'ap-step', 'directing');
  await settle();
  detail = container.regions.detail.innerHTML;
  assert.ok(!detail.includes('Novel Analyzer · your server · no server URL') && detail.includes('Local LLM · your server · no server URL'));
  // BookNLP on Speakers & delivery reads accepted results: it needs no URL and sends nothing.
  change(container, 'ap_provider', 'booknlp');
  await settle();
  detail = container.regions.detail.innerHTML;
  assert.ok(detail.includes('BookNLP · from Quote attribution') && detail.includes('nothing is sent') && !detail.includes('has no server URL'));
});

// --- one state per step, start/next step and formatting (pure helpers) -----------------------
function api() {
  const scope = {window:{}, URLSearchParams, console};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../bardic/static/analysis-pipeline.js'), 'utf8'), scope);
  return scope.window.BardicAnalysisPipeline;
}

test('every combination of step facts maps to exactly one state', () => {
  const {stepStatus} = api();
  const base = {accepted_scopes:0, total_scopes:2, has_accepted:false, accepted_origins:{}, stale_scopes:[], pending_versions:0, latest:null};
  const run = (latest, fields = {}) => ({...base, latest, ...fields});
  const inUse = {accepted_scopes:2, has_accepted:true, accepted_origins:{run:2}};
  const needs = [{id:'discovery', label:'Character discovery', waiting:false}];
  const needsWaiting = [{id:'discovery', label:'Character discovery', waiting:true}];
  const noKey = {id:'openai', label:'OpenAI', what:'API key', ready:false};
  const key = {id:'openai', label:'OpenAI', what:'API key', ready:true};
  const cases = [
    ['loading', {}, 'loading', 'Loading'],
    ['never run', {state:base}, 'not-run', 'Not run'],
    ['never run, key present', {state:base, provider:key}, 'not-run', 'Not run'],
    ['never run, no key', {state:base, provider:noKey}, 'needs-setup', 'Needs setup'],
    ['needs an input', {state:base, missing:needs}, 'blocked', 'Needs Character discovery'],
    ['needs an input and no key: the input comes first', {state:base, missing:needs, provider:noKey}, 'blocked', 'Needs Character discovery'],
    ['accepted + unmet requirements', {state:run(null, inUse), missing:needs}, 'in-use-earlier', 'In use (from earlier work)'],
    ['accepted, all inputs met', {state:run(null, inUse)}, 'in-use', 'In use'],
    ['accepted, no key: still in use', {state:run(null, inUse), provider:noKey}, 'in-use', 'In use'],
    ['partly accepted', {state:run(null, {...inUse, accepted_scopes:1})}, 'in-use-partial', 'In use · 1 of 2'],
    ['accepted from existing work only', {state:run(null, {...inUse, accepted_origins:{baseline:2}})}, 'in-use', 'In use (from earlier work)'],
    ['stale only', {state:run(null, {...inUse, stale_scopes:['c1', 'c2']})}, 'in-use', 'In use'],
    ['running', {state:run({state:'running', status:'running'})}, 'running', 'Running'],
    ['queued', {state:run({state:'running', status:'queued'})}, 'running', 'Queued'],
    ['in the active run', {state:run(null, inUse), running:true}, 'running', 'Running'],
    ['running beats unmet inputs', {state:run({state:'running', status:'running'}), missing:needs}, 'running', 'Running'],
    ['failed', {state:run({state:'empty', status:'failed'})}, 'failed', 'Failed'],
    ['interrupted', {state:run({state:'empty', status:'interrupted'})}, 'interrupted', 'Interrupted'],
    ['cancelled', {state:run({state:'empty', status:'cancelled'})}, 'cancelled', 'Cancelled'],
    ['stopped at allowance', {state:run({state:'empty', status:'budget_limited'})}, 'budget_limited', 'Stopped at allowance'],
    ['failed with earlier results in use', {state:run({state:'empty', status:'failed'}, inUse)}, 'failed', 'Failed'],
    ['needs review', {state:run({state:'candidate', status:'completed'}, {pending_versions:1})}, 'review', 'Waiting for review'],
    ['two versions need review', {state:run({state:'candidate', status:'completed'}, {pending_versions:2})}, 'review', '2 waiting for review'],
    ['needs review after a failed run', {state:run({state:'candidate', status:'failed'}, {pending_versions:1})}, 'review', 'Waiting for review'],
    ['needs review with unmet inputs', {state:run({state:'candidate', status:'completed'}, {pending_versions:1}), missing:needs}, 'review', 'Waiting for review'],
    ['ran, found nothing', {state:run({state:'empty', status:'completed'})}, 'empty', 'Nothing found'],
    ['latest set aside', {state:run({state:'rejected', status:'completed'})}, 'set-aside', 'Set aside'],
    ['earlier versions, none in use', {state:run({state:'superseded', status:'completed'})}, 'not-in-use', 'Not in use'],
  ];
  for (const [name, input, key, label] of cases) {
    const status = stepStatus(input);
    assert.equal(status.key, key, name);
    assert.equal(status.label, label, name);
    assert.equal(typeof status.tone, 'string', name);
  }
  // The detail for accepted + unmet requirements says how to re-run.
  assert.equal(stepStatus({state:run(null, inUse), missing:needs}).detail, 'To re-run, first run Character discovery.');
  assert.equal(stepStatus({state:run(null, inUse), missing:needsWaiting}).detail, 'To re-run, first accept the Character discovery version waiting for review.');
  assert.equal(stepStatus({state:run(null, inUse), missing:needs}).action.step, 'discovery');
  // Staleness is a low-emphasis note, never the state.
  const stale = stepStatus({state:run(null, {...inUse, stale_scopes:['c1', 'c2']})});
  assert.equal(stale.note, '2 results were made before an input changed.');
  assert.equal(stale.tone, 'accepted');
  // Cancelled, failed and interrupted stay distinct, and say whether earlier results are still used.
  assert.match(stepStatus({state:run({state:'empty', status:'failed'}, inUse)}).detail, /Earlier accepted results are still in use/);
  assert.match(stepStatus({state:run({state:'empty', status:'interrupted'})}).detail, /Nothing from this run is in use/);
  assert.deepEqual({...stepStatus({state:base, provider:noKey}).action}, {kind:'setup', provider:'openai'});
  assert.match(stepStatus({state:base, provider:noKey}).detail, /Add the OpenAI API key in Providers & settings/);
});

test('the tab opens on the first actionable step', () => {
  const {startStep} = api();
  const s = (key, ready = true, inUse = false) => ({key, ready, inUse});
  // A version waiting for review holds up later steps, so it comes first.
  assert.equal(startStep([{id:'a', status:s('in-use', true, true)}, {id:'b', status:s('not-run')}, {id:'c', status:s('review')}]), 'c');
  // Otherwise the first step that can run and has nothing in use.
  assert.equal(startStep([{id:'a', status:s('in-use', true, true)}, {id:'b', status:s('blocked', false)}, {id:'c', status:s('needs-setup')}, {id:'d', status:s('not-run')}]), 'c');
  assert.equal(startStep([{id:'a', status:s('in-use', true, true)}, {id:'b', status:s('running')}, {id:'c', status:s('failed')}]), 'c');
  // Accepted-but-blocked steps are not actionable; with nothing actionable, the first step.
  assert.equal(startStep([{id:'a', status:s('in-use', true, true)}, {id:'b', status:s('in-use-earlier', false, true)}]), 'a');
  assert.equal(startStep([]), null);
});

test('after a run, Next points at review, then at the step that reads it, then at the next actionable step', () => {
  const {nextStep} = api();
  const s = (key, ready = true, inUse = false) => ({key, ready, inUse});
  const steps = (discovery, profiles, directing) => [
    {id:'structure', inputs:[], status:s('in-use', true, true)},
    {id:'census', inputs:[], status:s('not-run')},
    {id:'discovery', inputs:[], status:discovery},
    {id:'profiles', inputs:['discovery'], status:profiles},
    {id:'directing', inputs:['discovery', 'profiles'], status:directing},
  ];
  const done = s('in-use', true, true);
  const plain = value => value && {...value};
  assert.deepEqual(plain(nextStep(steps(s('review'), s('blocked', false), s('blocked', false)), 'discovery')), {id:'discovery', review:true});
  assert.deepEqual(plain(nextStep(steps(done, done, done), 'discovery')), {id:'profiles', review:false});
  assert.deepEqual(plain(nextStep(steps(done, done, done), 'profiles')), {id:'directing', review:false});
  // Nothing reads directing: the next actionable step, wrapping round.
  assert.deepEqual(plain(nextStep(steps(done, done, done), 'directing')), {id:'census', review:false});
  // A reader whose other requirements are unmet is skipped.
  assert.deepEqual(plain(nextStep(steps(done, done, s('blocked', false)), 'profiles')), {id:'census', review:false});
  assert.equal(nextStep([{id:'a', inputs:[], status:done}], 'a'), null);
  assert.equal(nextStep([], 'missing'), null);
});

test('money, prices and percentages are formatted one way; unknown is never $0', () => {
  const {formatMoney, formatRate, formatPercent} = api();
  assert.equal(formatMoney(null), 'Unknown price');
  assert.equal(formatMoney(undefined, 'unknown'), 'unknown');
  assert.equal(formatMoney(Number.NaN), 'Unknown price');
  assert.equal(formatMoney(0), '$0.00');
  assert.equal(formatMoney(.0004), 'under $0.01');
  assert.equal(formatMoney(.02), '$0.02');
  assert.equal(formatMoney(1234.5), '$1,234.50');
  for (const value of [.0001, .004, .009]) assert.ok(!/\$0\.0{3}/.test(formatMoney(value)), `${value} never reads as $0.0000`);
  assert.equal(formatRate(.3), '$0.30');
  assert.equal(formatRate(.075), '$0.075');
  assert.equal(formatRate(15), '$15.00');
  assert.equal(formatRate(null), 'unknown');
  assert.equal(formatPercent(.88), '88%');
  assert.equal(formatPercent(1), '100%');
  assert.equal(formatPercent(0), '0%');
  assert.equal(formatPercent(null), '—');
});

// --- the tab uses them ------------------------------------------------------------------------------
function withOverview(change, fallback = ordinary) {
  return call => {
    if (call.method === 'GET' && call.url === '/api/books/book%2F1/analysis-pipeline') { const value = overview('book/1'); change(value); return {data:value}; }
    return fallback(call);
  };
}
const nothingToReview = value => { Object.assign(value.steps.find(step => step.id === 'discovery'), {pending_versions:0, latest:null}); value.steps.find(step => step.id === 'census').accepted_scopes = 1; };

test('with nothing to review, the tab opens on the first step that can run and has nothing in use', async () => {
  const env = environment(withOverview(value => { nothingToReview(value); value.steps.find(step => step.id === 'census').accepted_scopes = 1; }));
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  assert.ok(container.regions.detail.innerHTML.includes('data-ap-key="detail-heading">Character discovery</h3>'));
  // A step chosen during the visit stays selected across refreshes.
  click(container, 'ap-step', 'census');
  await settle();
  click(container, 'ap-action', 'refresh');
  await settle();
  assert.ok(container.regions.detail.innerHTML.includes('data-ap-key="detail-heading">Name census</h3>'));
});

test('Run this step is disabled with a reason and a Set up control when the provider has no key', async () => {
  const noKey = () => { const value = definitions(); value.steps.find(step => step.id === 'discovery').settings = {provider:'gemini', model:'gem-flash', gate:'auto', saved:true}; return value; };
  const definitionsWithoutKey = call => call.method === 'GET' && call.url === '/api/analysis-pipeline' ? {data:noKey()} : ordinary(call);
  const opened = [];
  const env = environment(withOverview(nothingToReview, definitionsWithoutKey));
  const container = new Container();
  await env.render(container, book, {status, onOpenSettings:provider => opened.push(provider)});
  await settle();
  const detail = container.regions.detail.innerHTML;
  assert.ok(detail.includes('data-ap-key="detail-heading">Character discovery</h3>'), 'a step that needs setup is actionable');
  assert.match(detail, /data-ap-action="plan-step"[^>]*disabled[^>]*aria-describedby="ap-run-reason"/);
  assert.ok(detail.includes('Gemini has no API key.') && detail.includes('data-ap-action="setup" data-ap-provider="gemini"'));
  assert.ok(container.regions.steps.innerHTML.includes('data-ap-state="needs-setup">Needs setup'));
  click(container, 'ap-action', 'plan-step');
  await settle();
  assert.equal(env.writes().length, 0, 'no plan is requested without a key');
  click(container, 'ap-action', 'setup');
  assert.deepEqual(opened, ['gemini'], 'Set up opens Providers & settings for this provider');

  // Without an app hook, the sidebar's Providers & settings button opens the dialog at the provider's section.
  const clicked = [];
  const section = {open:false, querySelector:() => ({focus:() => clicked.push('focus-key')})};
  const document = {activeElement:null, getElementById:id => id === 'settings-button' ? {click:() => clicked.push('settings')}
    : id === 'provider-settings-gemini' ? section : null};
  const fallback = environment(withOverview(nothingToReview, definitionsWithoutKey), {globals:{document}});
  const other = new Container();
  await fallback.render(other, book, {status});
  await settle();
  click(other, 'ap-action', 'setup');
  assert.deepEqual(clicked, ['settings', 'focus-key']);
  assert.equal(section.open, true);
});

test('a preview that finds a missing key offers Set up instead of plain text', async () => {
  const env = environment();
  const container = new Container();
  const opened = [];
  const options = {status, onOpenSettings:provider => opened.push(provider)};
  await env.render(container, book, options);
  await settle();
  click(container, 'ap-action', 'plan-step');
  await settle();
  // The key is removed while the preview is open.
  const keyless = {...status, analysis_providers:status.analysis_providers.map(p => p.id === 'openai' ? {...p, has_api_key:false} : p)};
  await env.render(container, book, {...options, status:keyless});
  await settle();
  const preview = container.regions.plan.innerHTML;
  assert.ok(preview.includes('Add this first: OpenAI API key.'));
  assert.ok(preview.includes('data-ap-action="setup" data-ap-provider="openai"'));
  assert.match(preview, /data-ap-action="confirm-run"[^>]*disabled/);
});

test('the run preview scrolls into view and takes focus on its heading, without motion when reduced', async () => {
  for (const reduce of [false, true]) {
    const heading = {scrolled:[], focused:[], scrollIntoView(options) { this.scrolled.push(options); }, focus(options) { this.focused.push(options); }};
    class Watched extends Container {
      querySelector(selector) { return selector === '[data-ap-key="plan-heading"]' ? heading : super.querySelector(selector); }
    }
    const env = environment(ordinary, {globals:{matchMedia:query => ({matches:reduce && query.includes('reduce')})}});
    const container = new Watched();
    await env.render(container, book, {status});
    await settle();
    click(container, 'ap-action', 'plan-step');
    await settle();
    // Once when it opens and again when the (taller) estimate arrives; focus never jumps the page.
    assert.deepEqual(heading.scrolled.map(value => ({...value})), [{block:'start', behavior:reduce ? 'auto' : 'smooth'}, {block:'start', behavior:reduce ? 'auto' : 'smooth'}]);
    assert.ok(heading.focused.length >= 1 && heading.focused.every(value => value.preventScroll === true));
  }
});

test('after a completed run, Next opens the step that reads its results', async () => {
  const completed = value => {
    value.recent_runs = [{id:'run-3', status:'completed', steps:['discovery'], created_at:'2026-09-27T10:00:00Z'}];
    Object.assign(value.steps.find(step => step.id === 'discovery'), {pending_versions:0, accepted_scopes:2, has_accepted:true, latest:{id:'v2', state:'accepted', status:'completed'}});
    value.steps.find(step => step.id === 'census').accepted_scopes = 1;
  };
  const env = environment(withOverview(completed));
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  const runs = container.regions.runs.innerHTML;
  assert.ok(runs.includes('data-ap-step="profiles" data-ap-key="go-next">Next: Character profiles →'), runs);
  click(container, 'ap-step', 'profiles');
  await settle();
  assert.ok(container.regions.detail.innerHTML.includes('data-ap-key="detail-heading">Character profiles</h3>'));
  assert.ok(!container.regions.runs.innerHTML.includes('go-next">Next: Character profiles'), 'Next hides once you are there');

  // A failed run offers no Next.
  const failed = environment(withOverview(value => { completed(value); value.recent_runs[0].status = 'failed'; }));
  const other = new Container();
  await failed.render(other, book, {status});
  await settle();
  assert.ok(!other.regions.runs.innerHTML.includes('go-next'));
});

test('directing results group rows by scene, show confidence as a percentage and small costs without $0.0000', async () => {
  const directingResult = {stats:{passages:3}, columns:[{key:'scene', label:'Scene'}, {key:'text', label:'Passage'}, {key:'speaker', label:'Speaker'}, {key:'confidence', label:'Confidence'}],
    rows:[
      {id:'s1', scope:'c1', scene:'The <Gate> · Scene 1', text:'Mara found the door.', speaker:'Narrator', confidence:1},
      {id:'s2', scope:'c1', scene:'The <Gate> · Scene 1', text:'"Elias?"', speaker:'Mara', confidence:.88},
      {id:'s3', scope:'c1', scene:'The <Gate> · Scene 2', text:'"Here."', speaker:'Elias', confidence:null},
    ], diff:{}, total_rows:3, offset:0, limit:200, scopes:[{scope:'c1', accepted:true}], revision:4};
  const env = environment(call => {
    if (call.method === 'GET' && call.url.includes('/versions/v2?')) return {data:directingResult};
    if (call.method === 'POST' && call.url.endsWith('/plan')) return {data:{...plan, estimated_cost_usd:.0004, steps:[{...plan.steps[1], estimated_cost_usd:.0004}]}};
    return ordinary(call);
  });
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  const table = container.regions.result.innerHTML;
  assert.ok(!table.includes('>Result<') && !table.includes('>Scene<'), 'no columns that repeat the section');
  assert.equal((table.match(/class="ap-group"/g) || []).length, 2, 'one heading per scene');
  assert.ok(table.includes('>The &lt;Gate&gt; · Scene 1</th>') && table.includes('>The &lt;Gate&gt; · Scene 2</th>'));
  assert.ok(table.includes('<td>100%</td>') && table.includes('<td>88%</td>') && !table.includes('<td>0.88</td>'));
  click(container, 'ap-action', 'plan-step');
  await settle();
  const preview = container.regions.plan.innerHTML;
  assert.ok(preview.includes('under $0.01') && !preview.includes('$0.0004'));
  // Model prices read like Settings: per million tokens, two decimals.
  assert.ok(container.regions.detail.innerHTML.includes('$0.10 input / $0.40 output per million tokens'));
});

test('kept edits on passages show the passage text, not its ID', async () => {
  const withSegments = {...book, segments:[{id:'seg-9', text:'"Stay <here>," she said.'}]};
  const env = environment(call => call.method === 'POST' && call.url.endsWith('/preview')
    ? {data:{...impact, conflicts:[{scope:'c1', item_id:'seg-9', field:'speaker_id'}, {scope:'c1', item_id:'gone', field:'direction'}]}} : ordinary(call));
  const container = new Container();
  await env.render(container, withSegments, {status});
  await settle();
  click(container, 'ap-action', 'accept');
  await settle();
  const panel = container.regions.result.innerHTML;
  assert.ok(panel.includes('<strong>“&quot;Stay &lt;here&gt;,&quot; she said.”</strong> · your speaker'));
  assert.ok(panel.includes('<small>The &lt;Gate&gt;</small>'));
  assert.ok(panel.includes('An item no longer in the book') && !panel.includes('seg-9') && !panel.includes('>gone<'));
});

// --- phase 3: the Analyze workflow ---------------------------------------------------------------------
// A click on any element carrying these data-* attributes (the plain `click` helper sets only one).
function press(container, dataset) {
  const node = {dataset, disabled:false};
  const attr = selector => selector.match(/^\[data-([\w-]+)\]$/)?.[1];
  container.listeners.click({target:{closest:selector => attr(selector) && camel(attr(selector)) in dataset ? node : null}});
}
const heading = container => container.regions.detail.innerHTML.match(/data-ap-key="detail-heading">([^<]*)<\/h3>/)?.[1];

test('the free local steps are grouped as Prep, and the tab opens on a required step first', async () => {
  const env = environment(withOverview(value => {
    nothingToReview(value);
    value.steps.find(step => step.id === 'census').accepted_scopes = 0;
  }));
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  const steps = container.regions.steps.innerHTML;
  const prep = steps.slice(steps.indexOf('Prep (free, optional)'), steps.indexOf('Characters &amp; speakers'));
  assert.ok(prep.includes('data-ap-step="structure"') && prep.includes('data-ap-step="census"'));
  assert.ok(!prep.includes('data-ap-step="discovery"'));
  assert.ok(steps.indexOf('data-ap-step="discovery"') > steps.indexOf('Characters &amp; speakers'));
  assert.equal(heading(container), 'Character discovery', 'an unrun optional Prep step does not come first');
  const {startStep} = env.api;
  const s = (key, ready = true, inUse = false) => ({key, ready, inUse});
  assert.equal(startStep([{id:'census', optional:true, status:s('not-run')}, {id:'discovery', status:s('not-run')}]), 'discovery');
  assert.equal(startStep([{id:'census', optional:true, status:s('not-run')}, {id:'discovery', status:s('in-use', true, true)}]), 'census');
});

const failedRun = {id:'run-9', status:'failed', steps:['discovery'], created_at:'2026-09-27T10:00:00Z', chapter_ids:['c1'],
  configs:{discovery:{provider:'openai', model:'gpt-b'}}, gates:{discovery:'review'}, concurrency:3, fresh:true,
  outcomes:{discovery:{status:'failed', error:'Rate limited'}}};
const withFailure = value => {
  nothingToReview(value);
  value.recent_runs = [failedRun];
  value.steps.find(step => step.id === 'discovery').latest = {id:'v3', state:'empty', status:'failed', provider:'openai', model:'gpt-b', chapter_ids:['c1']};
};

test('Try again opens the usual preview with the failed run\'s settings, and only Confirm starts work', async () => {
  const env = environment(withOverview(withFailure));
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  assert.equal(heading(container), 'Character discovery');
  const runs = container.regions.runs.innerHTML;
  assert.ok(runs.includes('data-ap-action="retry" data-ap-retry="discovery" data-ap-run="run-9"') && runs.includes('>Try again<'));
  assert.ok(!runs.includes('go-next'), 'a failed run offers no Next');
  assert.ok(!container.regions.detail.innerHTML.includes('data-ap-action="retry"'), 'one Try again while the summary shows it');
  click(container, 'ap-action', 'dismiss-run');
  assert.match(container.regions.detail.innerHTML, /data-ap-status="failed".*data-ap-action="retry"/s, 'after Dismiss the step panel offers it');
  press(container, {apAction:'retry', apRetry:'discovery', apRun:'run-9'});
  await settle();
  const planned = env.writes();
  assert.equal(planned.length, 1, 'only the estimate is requested');
  // The step's saved model is gpt-a; the failed run used gpt-b, one section and fresh samples.
  assert.deepEqual(planned[0].body, {steps:['discovery'], configs:{discovery:{provider:'openai', model:'gpt-b'}}, fresh:true, chapter_ids:['c1']});
  const preview = container.regions.plan.innerHTML;
  assert.ok(preview.includes('Trying again with the settings of the failed run (OpenAI · gpt-b)'));
  assert.ok(preview.includes('only the missing work is estimated') && preview.includes('Confirm and run'));
  assert.ok(!env.calls.some(call => call.url.endsWith('/runs')));
  click(container, 'ap-action', 'confirm-run');
  await settle();
  const run = env.calls.find(call => call.url.endsWith('/runs'));
  assert.deepEqual(run.body, {steps:['discovery'], configs:{discovery:{provider:'openai', model:'gpt-b'}}, fresh:true, chapter_ids:['c1'],
    gates:{discovery:'review'}, scheduling:'serial', concurrency:3, expected_fingerprint:'fp-123'});
});

test('Try again refuses sections that are gone and needs the provider\'s key', async () => {
  const gone = environment(withOverview(value => { withFailure(value); value.recent_runs = [{...failedRun, chapter_ids:['c-removed']}]; }));
  const container = new Container();
  await gone.render(container, book, {status});
  await settle();
  press(container, {apAction:'retry', apRetry:'discovery', apRun:'run-9'});
  await settle();
  assert.equal(gone.writes().length, 0);
  assert.ok(container.regions.message.innerHTML.includes('no longer in this book'));
  const keyless = environment(withOverview(value => { withFailure(value); value.recent_runs = [{...failedRun, configs:{discovery:{provider:'gemini', model:'gem-flash'}}}]; }));
  const other = new Container();
  await keyless.render(other, book, {status});
  await settle();
  press(other, {apAction:'retry', apRetry:'discovery', apRun:'run-9'});
  await settle();
  assert.equal(keyless.writes().length, 0, 'no estimate without the key');
  assert.ok(other.regions.message.innerHTML.includes('Gemini has no API key'));
});

// Speakers & delivery rows (bardic/pipeline/steps/directing.py summarize), with passages in the book.
const directingColumns = [{key:'scene', label:'Scene'}, {key:'text', label:'Passage'}, {key:'speaker', label:'Speaker'}, {key:'confidence', label:'Confidence'},
  {key:'direction', label:'Delivery'}, {key:'check', label:'BookNLP check'}, {key:'edited', label:'Your edit (kept)'}];
function directingRows(count) {
  return Array.from({length:count}, (_, i) => ({id:`s${i}`, scope:'c1', scene:'', kind:'dialogue', text:`Line ${i} <by> the quay.`,
    speaker:i % 10 === 0 ? 'Unassigned dialogue' : i % 10 === 1 ? '' : 'Mira & Co', confidence:[0, .2, .65, .66, .9, null][i % 6],
    direction:'', check:i % 7 === 0 ? 'Differs · BookNLP: Elio' : i % 7 === 1 ? 'Suggests a speaker · BookNLP: Elio' : 'Agrees', edited:i % 9 === 0 ? 'speaker' : ''}));
}
function directingHandler(rows, extra = {}) {
  return call => {
    const url = new URL(call.url, 'http://localhost');
    if (call.method === 'GET' && url.pathname.includes('/versions/v2')) {
      const offset = Number(url.searchParams.get('offset')), limit = Number(url.searchParams.get('limit'));
      return {data:{stats:{}, columns:directingColumns, rows:rows.slice(offset, offset + limit), diff:{}, total_rows:rows.length, offset, limit, scopes:[{scope:'c1'}], revision:4, ...extra}};
    }
    return ordinary(call);
  };
}
const passageBook = rows => ({...book, characters:[...book.characters, {id:'unassigned', name:'Unassigned dialogue'}, {id:'elio', name:'Elio'}],
  segments:rows.map(row => ({id:row.id, chapter_id:'c1', text:row.text, speaker_id:'mira', evidence:[]}))});
const rowCount = html => (html.match(/data-ap-action="show-passage"/g) || []).length;

test('result filters read every row and find no speaker, low confidence, BookNLP disagreements and your edits', async () => {
  const rows = directingRows(1205);
  const env = environment(directingHandler(rows));
  const container = new Container();
  await env.render(container, passageBook(rows), {status});
  await settle();
  let table = container.regions.result.innerHTML;
  for (const label of ['All rows', 'No speaker', 'Low confidence', 'BookNLP disagrees', 'Your edits']) assert.ok(table.includes(`>${label}`), label);
  assert.ok(table.includes('1–200 of 1,205 rows'), 'unfiltered rows are paged by the server');
  const count = keep => rows.filter(keep).length;
  const expected = {
    'no-speaker':count(row => row.speaker === '' || row.speaker === 'Unassigned dialogue'),
    'low-confidence':count(row => typeof row.confidence === 'number' && row.confidence <= .65),
    'booknlp-differs':count(row => row.check.startsWith('Differs')),
    edited:count(row => row.edited !== ''),
  };
  const before = env.calls.length;
  click(container, 'ap-filter', 'no-speaker');
  await settle();
  const pages = env.calls.slice(before).map(call => new URL(call.url, 'http://localhost').searchParams);
  assert.deepEqual(pages.map(query => [query.get('offset'), query.get('limit')]), [['0', '1000'], ['1000', '1000']], 'every row, in the largest pages');
  table = container.regions.result.innerHTML;
  assert.ok(table.includes(`of ${expected['no-speaker']} matching rows (of 1,205)`));
  assert.ok(table.includes(`No speaker (${expected['no-speaker']})`) && table.includes(`Your edits (${expected.edited})`));
  assert.match(table, /aria-checked="true"[^>]*data-value="no-speaker"/);
  for (const [filter, total] of Object.entries(expected)) {
    const calls = env.calls.length;
    click(container, 'ap-filter', filter);
    await settle();
    assert.equal(env.calls.length, calls, 'changing between filters reuses the loaded rows');
    const html = container.regions.result.innerHTML;
    assert.ok(html.includes(`of ${total.toLocaleString('en-US')} matching rows`), `${filter}: ${total}`);
    assert.equal(rowCount(html), Math.min(total, 200), `${filter} shows its rows, a page at a time`);
  }
  // The low-confidence boundary: 65% is low; 66% and unknown are not.
  click(container, 'ap-filter', 'low-confidence');
  await settle();
  const low = container.regions.result.innerHTML;
  assert.ok(low.includes('<td>65%</td>') && low.includes('<td>0%</td>') && !low.includes('<td>66%</td>') && !low.includes('<td>90%</td>'));
  assert.ok(low.includes('Confidence of 65% or less'));
  // Paging within the matching rows happens here, without requests.
  const calls = env.calls.length;
  click(container, 'ap-action', 'next-page');
  assert.equal(env.calls.length, calls);
  assert.ok(container.regions.result.innerHTML.includes(`201–${Math.min(400, expected['low-confidence'])} of`));
  click(container, 'ap-filter', 'all');
  await settle();
  assert.ok(container.regions.result.innerHTML.includes('1–200 of 1,205 rows'));
  assert.equal(env.writes().length, 0, 'filters only read');
});

test('the filter list is fixed and its low-confidence line is the 65% assignment floor', () => {
  const env = environment();
  assert.deepEqual([...env.api.FILTERS.map(filter => filter.id)], ['no-speaker', 'low-confidence', 'booknlp-differs', 'edited']);
  assert.equal(env.api.LOW_CONFIDENCE, .65);
});

test('filters appear only for steps whose table has those columns', async () => {
  const env = environment();
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  assert.ok(!container.regions.result.innerHTML.includes('data-ap-filter='), 'discovery rows have no speaker or confidence columns');
});

test('evidence shows exact quotes where they are recorded and says so where they are not', async () => {
  const row = (id, fields) => ({id, scope:'c1', text:`"${id}"`, speaker:'Mira & Co', confidence:.9, check:'', edited:'', ...fields});
  const rows = [
    row('q1', {evidence_quotes:['"Stay," said <b>Mira</b>']}),
    row('q2', {}),
    row('q3', {speaker:'Elio'}),
    row('q4', {edited:'speaker'}),
    row('q5', {evidence_quotes:[]}),
    row('q6', {}),
    row('q7', {}),
    row('q8', {kind:'narration', speaker:'Narrator'}),
    row('not-a-passage', {}),
  ];
  const segments = [
    {id:'q1', chapter_id:'c1', speaker_id:'mira', evidence:['book quote']},
    {id:'q2', chapter_id:'c1', speaker_id:'mira', evidence:['Mira <i>spoke</i> first', 42]},
    {id:'q3', chapter_id:'c1', speaker_id:'mira', evidence:['belongs to Mira']},
    {id:'q4', chapter_id:'c1', speaker_id:'mira', evidence:['old evidence'], manual_fields:['speaker_id']},
    {id:'q5', chapter_id:'c1', speaker_id:'mira', evidence:['ignored: the version has none']},
    {id:'q6', chapter_id:'c1', speaker_id:'mira', evidence:[]},
    {id:'q7', chapter_id:'c1', speaker_id:'mira'},
    {id:'q8', chapter_id:'c1', speaker_id:'narrator'},
  ];
  const env = environment(directingHandler(rows));
  const container = new Container();
  await env.render(container, {...book, characters:[...book.characters, {id:'elio', name:'Elio'}, {id:'narrator', name:'Narrator'}], segments}, {status});
  await settle();
  const table = container.regions.result.innerHTML;
  const cellOf = id => { const start = table.indexOf(`data-ap-segment="${id}"`); return start < 0 ? '' : table.slice(start, table.indexOf('</td>', start)); };
  assert.ok(table.includes('<th scope="col">Evidence</th>'));
  assert.ok(cellOf('q1').includes('<q>&quot;Stay,&quot; said &lt;b&gt;Mira&lt;/b&gt;</q>') && cellOf('q1').includes('From this version.') && !cellOf('q1').includes('book quote'));
  assert.ok(cellOf('q2').includes('<q>Mira &lt;i&gt;spoke&lt;/i&gt; first</q>') && cellOf('q2').includes('Recorded in the book for Mira &amp; Co.') && cellOf('q2').includes('Evidence (1)'));
  assert.ok(cellOf('q3').includes('not included in this table') && !cellOf('q3').includes('belongs to Mira'), 'the book\'s evidence justifies another speaker');
  assert.ok(cellOf('q4').includes('not included in this table') && !cellOf('q4').includes('old evidence'), 'a hand-changed speaker keeps stale evidence');
  assert.ok(cellOf('q5').includes('No evidence recorded.') && !cellOf('q5').includes('ignored'));
  assert.ok(cellOf('q6').includes('No evidence recorded.'));
  assert.ok(cellOf('q7').includes('No evidence recorded.'), 'a passage whose analysis stored no evidence field');
  assert.ok(cellOf('q8').includes('Show in text') && !cellOf('q8').includes('Evidence'), 'narration needs no speaker evidence');
  assert.ok(!table.includes('data-ap-segment="not-a-passage"'));
  assert.ok(!/<(b|i)>/.test(table), 'quotes are text, never markup');
});

test('Show in text announces the passage for other views to open', async () => {
  const rows = directingRows(3);
  const events = [];
  class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; this.cancelable = Boolean(init.cancelable); } }
  const document = {activeElement:null, getElementById:() => null, dispatchEvent:event => { events.push(event); return true; }};
  const env = environment(directingHandler(rows), {globals:{document, CustomEvent}});
  const container = new Container();
  await env.render(container, passageBook(rows), {status});
  await settle();
  assert.ok(container.regions.result.innerHTML.includes('data-ap-action="show-passage" data-ap-segment="s2"'));
  press(container, {apAction:'show-passage', apSegment:'s2'});
  press(container, {apAction:'show-passage', apSegment:'unknown'});
  assert.equal(events.length, 1, 'only passages in the book are announced');
  assert.equal(events[0].type, 'bardic:show-passage');
  assert.equal(events[0].cancelable, true, 'a view that handles it can cancel the fallback');
  assert.deepEqual({...events[0].detail}, {bookId:'book/1', segmentId:'s2', chapterId:'c1'});
  assert.equal(env.writes().length, 0);
});

const presets = [
  {id:'p-quick', name:'Quick <scan>', step:'discovery', version:1, config:{provider:'openai', model:'gpt-b', custom_model:false, gate:'review', concurrency:3, fresh:true, chapter_id:'c1'}},
  {id:'p-gone-model', name:'Retired model', step:'discovery', version:1, config:{provider:'openai', model:'gpt-retired', custom_model:false, gate:'auto', concurrency:2, fresh:false, chapter_id:null}},
  {id:'p-custom', name:'My custom', step:'discovery', version:1, config:{provider:'openai', model:'my-model:1', custom_model:true, gate:'auto', concurrency:1, fresh:false, chapter_id:null}},
  {id:'p-nokey', name:'Gemini flash', step:'discovery', version:1, config:{provider:'gemini', model:'gem-flash', custom_model:false, gate:'auto', concurrency:2, fresh:false, chapter_id:null}},
  {id:'p-other-book', name:'Chapter nine', step:'discovery', version:1, config:{provider:'openai', model:'gpt-a', custom_model:false, gate:'auto', concurrency:2, fresh:false, chapter_id:'c9'}},
  {id:'p-profiles', name:'Deep profiles', step:'profiles', version:1, config:{provider:'openai', model:'gpt-b', custom_model:false, gate:'review', concurrency:2, fresh:false, chapter_id:null}},
];
const presetStatus = () => ({...status, analysis_step_presets:JSON.parse(JSON.stringify(presets))});
const settingsEcho = call => call.method === 'POST' && call.url === '/api/settings' ? {data:{...status, analysis_step_presets:call.body.analysis_step_presets}} : ordinary(call);

test('a saved step setting applies to its step: settings are saved, run options set, nothing starts', async () => {
  const env = environment(withOverview(nothingToReview, settingsEcho));
  const container = new Container();
  await env.render(container, book, {status:presetStatus()});
  await settle();
  let detail = container.regions.detail.innerHTML;
  assert.ok(detail.includes('<option value="" selected>Custom</option>'), 'the panel as it is: Custom');
  assert.ok(detail.includes('Quick &lt;scan&gt;</option>') && !detail.includes('Deep profiles'), 'only this step\'s saved settings');
  assert.ok(detail.includes('Retired model · can’t apply here') && detail.includes('Gemini flash · can’t apply here') && detail.includes('Chapter nine · can’t apply here'));
  assert.ok(detail.includes('>My custom</option>'), 'a hand-typed model ID is kept as typed');
  change(container, 'ap_preset', 'p-quick');
  await settle();
  const writes = env.writes();
  assert.equal(writes.length, 1);
  assert.deepEqual(writes[0].body, {provider:'openai', model:'gpt-b', gate:'review'});
  assert.equal(writes[0].url, '/api/analysis-pipeline/steps/discovery/settings');
  detail = container.regions.detail.innerHTML;
  assert.ok(detail.includes('<option value="p-quick" selected>'), 'the panel now matches the saved set');
  assert.ok(detail.includes('<option value="3" selected>3</option>') && /name="ap_fresh"[^>]*checked/.test(detail));
  assert.ok(detail.includes('<option value="c1" selected>'));
  assert.ok(container.regions.message.innerHTML.includes('Applied “Quick &lt;scan&gt;”.'));
  // Changing any field makes it Custom again; no saved set is changed.
  change(container, 'ap_concurrency', '1');
  assert.ok(container.regions.detail.innerHTML.includes('<option value="" selected>Custom</option>'));
  assert.ok(!env.calls.some(call => call.url.endsWith('/plan') || call.url.endsWith('/runs') || call.url === '/api/settings'));
});

test('a saved step setting that cannot apply says why and changes nothing', async () => {
  const env = environment(withOverview(nothingToReview, settingsEcho));
  const container = new Container();
  await env.render(container, book, {status:presetStatus()});
  await settle();
  for (const [id, reason] of [['p-gone-model', 'The model “gpt-retired” is no longer in the OpenAI model list.'], ['p-nokey', 'Gemini has no API key.'],
    ['p-other-book', 'The section it was saved with is not in this book.']]) {
    change(container, 'ap_preset', id);
    await settle();
    const detail = container.regions.detail.innerHTML;
    assert.ok(detail.includes(reason) && detail.includes('Nothing was changed.'), reason);
    assert.ok(detail.includes('<option value="" selected>Custom</option>'));
  }
  change(container, 'ap_preset', 'p-nokey');
  await settle();
  assert.ok(container.regions.detail.innerHTML.includes('data-ap-provider="gemini" data-ap-key="setup-preset"'), 'a missing key offers Set up');
  assert.equal(env.writes().length, 0);
  change(container, 'ap_preset', 'p-custom');
  await settle();
  assert.deepEqual(env.writes()[0].body, {provider:'openai', model:'my-model:1', gate:'auto'});
});

test('Save these settings as… stores a named set with the settings API; same name replaces; remove deletes', async () => {
  const env = environment(withOverview(nothingToReview, settingsEcho));
  const container = new Container();
  const statusValue = presetStatus();
  await env.render(container, book, {status:statusValue});
  await settle();
  click(container, 'ap-action', 'preset-open');
  assert.ok(container.regions.detail.innerHTML.includes('name="ap_preset_name"'));
  press(container, {apAction:'preset-save'});
  await settle();
  assert.equal(env.writes().length, 0);
  assert.ok(container.regions.detail.innerHTML.includes('Use a name of 1–60 characters.'));
  change(container, 'ap_chapter', 'c1');
  click(container, 'ap-action', 'preset-open');
  input(container, 'ap_preset_name', '  Night   run ');
  press(container, {apAction:'preset-save'});
  await settle();
  const saved = env.writes().at(-1);
  assert.equal(saved.url, '/api/settings');
  const list = saved.body.analysis_step_presets;
  assert.equal(list.length, presets.length + 1, 'the whole list is sent, the others unchanged');
  assert.deepEqual(list.slice(0, presets.length), presets);
  const entry = list.at(-1);
  assert.match(entry.id, /^p_[a-z0-9]+$/);
  assert.deepEqual({...entry, id:null, config:{...entry.config}}, {id:null, name:'Night run', step:'discovery', version:1,
    config:{provider:'openai', model:'gpt-a', custom_model:false, gate:'auto', concurrency:2, fresh:false, chapter_id:'c1'}});
  assert.ok(container.regions.message.innerHTML.includes('Saved “Night run” for Character discovery.'));
  assert.ok(container.regions.detail.innerHTML.includes(`<option value="${entry.id}" selected>Night run</option>`), 'the new set matches the panel');
  assert.equal(statusValue.analysis_step_presets.length, presets.length, 'the app\'s status object is not mutated');
  // Saving again under the same name (any case) replaces it and keeps its ID.
  change(container, 'ap_concurrency', '4');
  click(container, 'ap-action', 'preset-open');
  input(container, 'ap_preset_name', 'NIGHT RUN');
  press(container, {apAction:'preset-save'});
  await settle();
  const replaced = env.writes().at(-1).body.analysis_step_presets;
  assert.equal(replaced.length, presets.length + 1);
  assert.equal(replaced.at(-1).id, entry.id);
  assert.equal(replaced.at(-1).config.concurrency, 4);
  press(container, {apAction:'preset-delete', apPreset:entry.id});
  await settle();
  assert.deepEqual(env.writes().at(-1).body.analysis_step_presets, presets);
  assert.ok(container.regions.message.innerHTML.includes('Removed “NIGHT RUN”.'));
  assert.ok(!env.calls.some(call => call.url.endsWith('/plan') || call.url.endsWith('/runs')));
});

test('a failed save keeps the form open with the reason', async () => {
  const env = environment(withOverview(nothingToReview, call => call.url === '/api/settings' ? {ok:false, status:400, data:{detail:'Two saved settings for Character discovery are both named “x”.'}} : ordinary(call)));
  const container = new Container();
  await env.render(container, book, {status:presetStatus()});
  await settle();
  click(container, 'ap-action', 'preset-open');
  input(container, 'ap_preset_name', 'x');
  press(container, {apAction:'preset-save'});
  await settle();
  const detail = container.regions.detail.innerHTML;
  assert.ok(detail.includes('Could not save: Two saved settings') && detail.includes('name="ap_preset_name"'));
});

test('after Speakers & delivery completes, Next hands off to the cast and the script', async () => {
  const done = value => {
    value.recent_runs = [{id:'run-5', status:'completed', steps:['directing'], created_at:'2026-09-27T10:00:00Z'}];
    Object.assign(value.steps.find(step => step.id === 'discovery'), {pending_versions:0, accepted_scopes:2, has_accepted:true, latest:null});
    Object.assign(value.steps.find(step => step.id === 'directing'), {accepted_scopes:2, has_accepted:true, latest:{id:'d2', state:'accepted', status:'completed'}});
  };
  const env = environment(withOverview(done));
  const container = new Container();
  await env.render(container, book, {status});
  await settle();
  const runs = container.regions.runs.innerHTML;
  assert.ok(runs.includes('href="#/book/book%2F1/cast"') && runs.includes('Review the cast →'));
  assert.ok(runs.includes('href="#/book/book%2F1/studio"') && runs.includes('Open the script →'));
  assert.ok(!runs.includes('go-next'), 'no further analysis step is suggested');
  // While its version waits for review, review comes first.
  const review = environment(withOverview(value => { done(value); Object.assign(value.steps.find(step => step.id === 'directing'), {pending_versions:1, latest:{id:'d3', state:'candidate', status:'completed'}}); }));
  const other = new Container();
  await review.render(other, book, {status});
  await settle();
  assert.ok(!other.regions.runs.innerHTML.includes('Review the cast'));
  assert.equal(heading(other), 'Speakers &lt;&amp;&gt; delivery', 'the version waiting for review opens');
});

test('selectStep opens a step from outside the tab: on the next visit, or at once while it shows', async () => {
  const env = environment(withOverview(nothingToReview), {observer:true});
  const container = new Container({hidden:true});
  await env.render(container, book, {status});
  assert.equal(env.api.selectStep('profiles'), true, 'remembered for the next visit');
  container.hidden = false;
  env.observers[0].fn();
  await settle();
  assert.equal(heading(container), 'Character profiles');
  assert.ok(env.calls.some(call => call.url.endsWith('/steps/profiles/versions?limit=50')));
  assert.equal(env.api.selectStep('census'), true);
  await settle();
  assert.equal(heading(container), 'Name census', 'switches at once while shown');
  assert.equal(env.api.selectStep('no-such-step'), false);
  assert.equal(env.api.selectStep(''), false);
  // Used once: the next visit opens on the first actionable step again.
  container.hidden = true;
  env.observers[0].fn();
  container.hidden = false;
  env.observers[0].fn();
  await settle();
  assert.equal(heading(container), 'Character discovery');
});

test('on a narrow screen, choosing a step brings its panel into view', async () => {
  for (const narrowScreen of [true, false]) {
    const target = {scrolled:0, scrollIntoView() { this.scrolled++; }, focus() {}};
    class Watched extends Container {
      querySelector(selector) { return selector === '[data-ap-key="detail-heading"]' ? target : super.querySelector(selector); }
    }
    const env = environment(ordinary, {globals:{matchMedia:query => ({matches:narrowScreen && query.includes('max-width')})}});
    const container = new Watched();
    await env.render(container, book, {status});
    await settle();
    click(container, 'ap-step', 'profiles');
    await settle();
    assert.equal(target.scrolled, narrowScreen ? 1 : 0);
  }
});

test('coming back after Show in text reopens the same step and filter, once', async () => {
  const rows = directingRows(30);
  class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } }
  const document = {activeElement:null, getElementById:() => null, dispatchEvent:() => true};
  const env = environment(directingHandler(rows), {observer:true, globals:{document, CustomEvent}});
  const container = new Container();
  await env.render(container, passageBook(rows), {status});
  await settle();
  click(container, 'ap-step', 'directing');
  await settle();
  click(container, 'ap-filter', 'no-speaker');
  await settle();
  press(container, {apAction:'show-passage', apSegment:'s10'});
  container.hidden = true;           // Read & listen opens
  env.observers[0].fn();
  container.hidden = false;          // Back
  env.observers[0].fn();
  await settle();
  assert.equal(heading(container), 'Speakers &lt;&amp;&gt; delivery');
  assert.match(container.regions.result.innerHTML, /aria-checked="true"[^>]*data-value="no-speaker"/);
  container.hidden = true;
  env.observers[0].fn();
  container.hidden = false;
  env.observers[0].fn();
  await settle();
  assert.equal(heading(container), 'Character discovery', 'a later visit starts fresh');
});
