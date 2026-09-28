// Analysis tab behavior with mocked HTTP: settings never start work, runs need a
// confirmed plan, acceptance needs a reviewed impact, and model text is escaped.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const REGIONS = ['message', 'runbar', 'plan', 'steps', 'detail', 'versions', 'result'];
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
    providers:[{id:'gemini', label:'Gemini', has_api_key:false}, {id:'openai', label:'OpenAI', has_api_key:true}, {id:'anthropic', label:'Anthropic', has_api_key:false}],
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

function environment(handler = ordinary, {observer = false} = {}) {
  const calls = [];
  const timers = [];
  const observers = [];
  const scope = {window:{}, URLSearchParams, console,
    setTimeout:(fn, ms) => { timers.push({fn, ms, cleared:false}); return timers.length; },
    clearTimeout:id => { if (timers[id - 1]) timers[id - 1].cleared = true; },
    fetch:async (url, options = {}) => {
      const call = {url, method:options.method || 'GET', body:options.body ? JSON.parse(options.body) : undefined};
      calls.push(call);
      const value = await handler(call);
      return {ok:value.ok !== false, status:value.status || 200, json:async () => value.data};
    }};
  if (observer) scope.MutationObserver = class { constructor(fn) { this.fn = fn; observers.push(this); } observe() {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../bardic/static/analysis-pipeline.js'), 'utf8'), scope);
  return {calls, timers, observers, render:scope.window.BardicAnalysisPipeline.render,
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
  for (const text of ['Local', 'Model', 'Waiting for review', 'Accepted 1/1', 'Stale (1)', 'Failed', 'Not run']) assert.ok(steps.includes(text), text);
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
  assert.ok(table.includes('>Accept<') && table.includes('>Reject<'));
  assert.ok(table.includes('1 changed · 1 new'));

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

test('Run selected previews a plan and runs only after Confirm, with the plan fingerprint', async () => {
  const env = environment();
  const container = new Container();
  const started = [];
  await env.render(container, book, {status, onJobStarted:async job => { started.push(job); }});
  await settle();
  change(container, 'ap_include', '', {checked:true, dataset:{apInclude:'discovery'}});
  change(container, 'ap_include', '', {checked:true, dataset:{apInclude:'structure'}});
  assert.ok(container.regions.runbar.innerHTML.includes('Run selected (2)'));
  change(container, 'ap_chapter', 'c1');
  click(container, 'ap-action', 'plan-selected');
  await settle();
  const planned = env.writes();
  assert.equal(planned.length, 1);
  assert.equal(planned[0].url, '/api/books/book%2F1/analysis-pipeline/plan');
  assert.deepEqual(planned[0].body, {steps:['structure', 'discovery'], chapter_ids:['c1'],
    configs:{structure:{provider:'local', model:null}, discovery:{provider:'openai', model:'gpt-a'}}});
  const preview = container.regions.plan.innerHTML;
  assert.ok(preview.includes('Unknown price') && preview.includes('Estimates &lt;note&gt;'));
  assert.ok(preview.includes('Runs locally · free') && preview.includes('Sections: The &lt;Gate&gt;'));
  assert.ok(preview.includes('Confirm and run · up to 1 request'));
  assert.ok(!env.calls.some(call => call.url.endsWith('/runs')), 'previewing never starts work');

  change(container, 'ap_budget_on', '', {checked:false});
  input(container, 'ap_max_requests', '10');
  change(container, 'ap_mode', 'parallel');
  change(container, 'ap_fresh', '', {checked:true});
  assert.ok(container.regions.plan.innerHTML.includes('Fresh samples is on'));
  click(container, 'ap-action', 'confirm-run');
  await settle();
  const run = env.calls.find(call => call.url.endsWith('/runs'));
  assert.equal(run.method, 'POST');
  assert.deepEqual(run.body, {steps:['structure', 'discovery'], chapter_ids:['c1'],
    configs:{structure:{provider:'local', model:null}, discovery:{provider:'openai', model:'gpt-a'}},
    gates:{structure:'auto', discovery:'auto'}, mode:'parallel', concurrency:2, fresh:true,
    limits:{max_requests:10, budget_usd:null}, expected_fingerprint:'fp-123'});
  assert.deepEqual(started.map(job => job.id), ['job-1']);
  assert.equal(container.regions.plan.innerHTML, '');
  assert.equal(env.calls.filter(call => call.url.endsWith('/runs')).length, 1);
});

test('a changed setting closes an open preview; a stale fingerprint asks for a new preview', async () => {
  const env = environment(call => call.url.endsWith('/runs') ? {ok:false, status:409, data:{detail:'The plan changed since the preview.'}} : ordinary(call));
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
  input(container, 'ap_budget', '0');
  assert.ok(container.regions.plan.innerHTML.includes('Set a dollar budget'));
  click(container, 'ap-action', 'confirm-run');
  await settle();
  assert.ok(!env.calls.some(call => call.url.endsWith('/runs')), 'invalid limits never start work');
  input(container, 'ap_budget', '2.5');
  click(container, 'ap-action', 'confirm-run');
  await settle();
  assert.equal(env.calls.filter(call => call.url.endsWith('/runs')).length, 1);
  assert.equal(env.calls.find(call => call.url.endsWith('/runs')).body.limits.budget_usd, 2.5);
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
  change(container, 'ap_include', '', {checked:true, dataset:{apInclude:'structure'}});
  click(container, 'ap-action', 'plan-selected');
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
  assert.ok(activeContainer.regions.runbar.innerHTML.includes('Run in progress'));
  const pending = active.timers.filter(timer => !timer.cleared);
  assert.equal(pending.length, 1);
  assert.equal(pending[0].ms, 2000);
  pending[0].fn();
  await settle();
  assert.equal(reads, 2);
  active.timers.filter(timer => !timer.cleared).at(-1).fn();
  await settle();
  assert.equal(reads, 3);
  assert.ok(!activeContainer.regions.runbar.innerHTML.includes('Run in progress'));
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
  assert.ok(panel.includes('your manual edits were kept'));
  assert.ok(panel.includes('Edited &lt;manually&gt;'));
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
  assert.ok(restore.includes('Restore this version'));
  assert.ok(!restore.includes('>Reject<'));
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
  assert.ok(container.regions.message.innerHTML.includes('Rejected.'));
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
  assert.ok(!container.regions.runbar.innerHTML.includes('Old book'));
  assert.ok(container.regions.steps.innerHTML.includes('Waiting for review'));
  assert.ok(env.calls.some(call => call.url === '/api/books/new/analysis-pipeline'));

  // Clearing the book empties the view and discards pending work.
  await env.render(container, null, {status});
  assert.equal(container.innerHTML, '');
});
