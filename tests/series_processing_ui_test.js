// Series runs UI: step choice, plan → consent → confirm, per-book progress and cancel. No network, no DOM library.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Node {
  constructor(name = '') { this.name = name; this.innerHTML = ''; this.textContent = ''; this.disabled = false; this.hidden = false;
    this.listeners = {}; this.attrs = {}; this.dataset = {}; this.focused = 0; }
  addEventListener(event, handler) { this.listeners[event] = handler; }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  removeAttribute(key) { delete this.attrs[key]; }
  getAttribute(key) { return this.attrs[key] ?? null; }
  focus() { this.focused++; }
  scrollIntoView() {}
  querySelector() { return null; }
}
class PlanNode extends Node {
  constructor() { super('plan'); this.consent = new Node('consent'); }
  querySelector(selector) { return selector === '[data-consent]' && this.innerHTML.includes('data-consent') ? this.consent : null; }
}
class MapNode extends Node { constructor() { super('map-output'); this.pre = new Node('pre'); } querySelector() { return this.pre; } }
class Form extends Node {
  constructor() { super('form'); this.fields = {step:'', concurrency:'2'}; this.controls = [new Node('select'), new Node('input'), new Node('button')]; }
  querySelectorAll() { return this.controls; }
}
const SLOTS = ['step', 'model-options', 'step-note', 'preview', 'map', 'refresh', 'processing-message', 'runs'];
class Container {
  constructor() { this.html = ''; this.nodes = {}; }
  set innerHTML(html) {
    this.html = html;
    this.nodes = {form:new Form(), '[data-series-plan]':new PlanNode(), '[data-series-map-output]':new MapNode()};
    for (const name of SLOTS) this.nodes[`[data-series-${name}]`] = new Node(name);
    this.nodes['[data-series-model-options]'].hidden = true;
  }
  get innerHTML() { return this.html; }
  querySelector(selector) { return this.nodes[selector] || null; }
}

// Objects built inside the vm realm compare by value, not prototype.
const same = (actual, expected, message) => assert.equal(JSON.stringify(actual), JSON.stringify(expected), message);
const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle() { for (let i = 0; i < 10; i++) await tick(); }
const series = {id:'saga/one', name:'The <Lantern>', books:[{book_id:'book-1', title:'The first lamp', position:1}, {book_id:'book-9', title:'The ninth lamp', position:9}]};
const defs = {providers:[{id:'openai', label:'OpenAI', kind:'model', self_hosted:false}, {id:'booknlp', label:'BookNLP', kind:'service', self_hosted:true}],
  steps:[{id:'census', label:'Name & dialogue census', method:'plain', summary:'Counts names.', settings:{provider:'local', model:null}},
    {id:'discovery', label:'Character discovery', method:'llm', summary:'Finds characters.', settings:{provider:'openai', model:'fast-model'}},
    {id:'profiles', label:'Character profiles', method:'llm', summary:'Builds profiles.', settings:{provider:'openai', model:'deep-model'}}]};
const bookPlan = (requests, cost) => ({requests, estimated_cost_usd:cost, cached_units:0, service_calls:0, missing_inputs:{}});
const plan = {series_id:series.id, fingerprint:'reviewed-fingerprint', steps:['discovery'], configs:{discovery:{provider:'openai', model:'fast-model'}},
  requests:3, cached_units:0, service_calls:0, estimated_cost_usd:.02, known_cost_usd:.02, unknown_cost_books:[], missing_inputs:{}, missing_credentials:[],
  skipped_volumes:[{kind:'placeholder', series_id:'series-9', position:2, title:'Lost book', status:'missing'}], notes:['Books run one at a time & in order.'],
  books:[{book_id:'book-1', position:1, title:'The first lamp', plan:bookPlan(1, .01)}, {book_id:'book-9', position:9, title:'The <ninth> lamp', plan:bookPlan(2, .01)}]};
const running = {id:'run/1', kind:'series', status:'running', steps:['discovery'], message:'Book 1 of 2', children:[
  {id:'c1', book_id:'book-1', status:'running', run_id:'r1', message:'Character discovery · range 1'},
  {id:'c2', book_id:'book-9', status:'queued', run_id:null, message:'Waiting for earlier volumes'}]};

function ordinary(call) {
  if (call.url === '/api/analysis-pipeline') return {data:defs};
  if (call.url.endsWith('/runs')) return {data:{runs:[]}};
  if (call.url.endsWith('/plan')) return {data:plan};
  if (call.url.endsWith('/process')) return {data:{id:'run/1', status:'queued', message:'Series queued.'}};
  if (call.url.endsWith('/map')) return {data:{series:{books:series.books}, characters:[{name:'<script>alert(1)</script>'}]}};
  if (call.url.endsWith('/cancel')) return {data:{status:'cancelled'}};
  throw new Error(`Unexpected URL ${call.url}`);
}
function environment(handler = ordinary) {
  const calls = [], timers = new Map(); let nextTimer = 1;
  const scope = {window:{}, console,
    FormData:class { constructor(form) { this.entries = Object.entries(form.fields).filter(([, v]) => v !== undefined); } [Symbol.iterator]() { return this.entries[Symbol.iterator](); } },
    setTimeout(fn, ms) { const id = nextTimer++; timers.set(id, {fn, ms}); return id; }, clearTimeout(id) { timers.delete(id); },
    fetch:async (url, options = {}) => {
      const call = {url, method:options.method || 'GET', body:options.body ? JSON.parse(options.body) : undefined}; calls.push(call);
      const result = await handler(call);
      return {ok:result.ok !== false, status:result.status || 200, json:async () => { if (result.unreadable) throw new Error('bad JSON'); return result.data; }};
    }};
  vm.createContext(scope);
  for (const file of ['ui.js', 'series-processing.js']) vm.runInContext(fs.readFileSync(path.join(__dirname, '../bardic/static', file), 'utf8'), scope);
  const posts = suffix => calls.filter(call => call.method === 'POST' && call.url.endsWith(suffix));
  return {calls, timers, posts, api:scope.window.BardicSeriesProcessing};
}
const node = (container, name) => container.querySelector(`[data-series-${name}]`);
const form = container => container.querySelector('form');
function choose(container, fields) { Object.assign(form(container).fields, fields); form(container).listeners.change({target:{}}); }
const submit = container => form(container).listeners.submit({preventDefault() {}});
const click = (container, name) => node(container, name).listeners.click({target:{closest:() => null}});
const planClick = (container, selector, dataset = {}) => node(container, 'plan').listeners.click({target:{closest:s => s === selector ? {dataset} : null}});
// The confirm handler starts work without returning it; let the dispatch settle.
const confirm = async container => { planClick(container, '[data-consent-confirm]'); await settle(); };
const cancelRun = (container, id) => node(container, 'runs').listeners.click({target:{closest:s => s === '[data-series-cancel]' ? {dataset:{seriesCancel:id}} : null}});
async function mounted(env, options = {}) {
  const container = new Container();
  await env.api.render(container, series, options);
  form(container).fields.step = 'discovery';
  return container;
}

(async () => {
  // Pure helpers: unknown cost is never zero; blockers name the books; child states stay distinct.
  {
    const env = environment();
    const ui = (() => { const s = {window:{}}; vm.createContext(s); vm.runInContext(fs.readFileSync(path.join(__dirname, '../bardic/static/ui.js'), 'utf8'), s); return s.window.BardicUI; })();
    assert.ok(ui);
    const unknown = env.api.consentFor({...plan, estimated_cost_usd:null, known_cost_usd:.01, unknown_cost_books:['book-9']}, defs);
    assert.equal(unknown.estimate.cost, null);
    assert.match(unknown.estimate.note, /cost unknown for 1 of 2 books; the priced books come to about \$0\.01/);
    const blocked = env.api.consentFor({...plan, steps:['profiles'], configs:{profiles:{provider:'openai', model:'deep-model'}},
      missing_inputs:{'book-9':{profiles:['discovery']}}}, defs);
    assert.match(blocked.blocked.reason, /Character profiles needs accepted Character discovery results in The <ninth> lamp/);
    const keyless = env.api.consentFor({...plan, missing_credentials:[{provider:'openai', label:'OpenAI'}]}, defs);
    same(keyless.blocked.setupAttrs, {'data-series-setup':'openai'});
    const local = env.api.consentFor({...plan, steps:['census'], configs:{census:{provider:'local', model:null}}, requests:0, estimated_cost_usd:0}, defs);
    assert.equal(local.estimate.free, true); same(local.sends, []); assert.equal(local.capNote, null);
    const service = env.api.consentFor({...plan, configs:{discovery:{provider:'booknlp', model:null}}, requests:0, service_calls:4, estimated_cost_usd:0}, defs);
    assert.equal(service.estimate.free, true); assert.match(service.estimate.note, /4 calls to your server/);
    assert.equal(env.api.consentFor({...plan, fingerprint:null}, defs).blocked.reason, 'This plan has no fingerprint. Preview it again.');
    assert.equal(env.api.consentFor({...plan, books:[]}, defs).blocked.reason, 'Add a book to this series before processing it.');
    assert.equal(env.api.childState({status:'cancelled', run_id:null}), 'not_started');
    assert.equal(env.api.childState({status:'interrupted', not_started:true, run_id:null}), 'not_started');
    assert.equal(env.api.childState({status:'cancelled', run_id:'r'}), 'cancelled');
    assert.equal(env.api.childState({status:'completed', run_id:'r', run:{outcomes:{discovery:{status:'completed', scope_count:2, accepted:false}}}}), 'needs_review');
    assert.equal(env.api.childState({status:'completed', run_id:'r', run:{outcomes:{discovery:{status:'completed', scope_count:2, accepted:true}}}}), 'completed');
  }

  // Rendering reads definitions and runs only; the step list mirrors the Analysis tab.
  let changed = 0;
  const env = environment(), container = await mounted(env, {onChange:() => changed++});
  assert.deepEqual(env.calls.map(call => call.method), ['GET', 'GET']);
  assert.ok(container.innerHTML.includes('Process The &lt;Lantern&gt;'));
  assert.ok(node(container, 'step').innerHTML.includes('Character discovery · Model'));
  assert.ok(node(container, 'step').innerHTML.includes('Name &amp; dialogue census · Local'));
  choose(container, {step:'discovery'});
  assert.equal(node(container, 'model-options').hidden, false);
  assert.ok(node(container, 'step-note').innerHTML.includes('Uses OpenAI · fast-model'));
  choose(container, {step:'census'});
  assert.equal(node(container, 'model-options').hidden, true);
  choose(container, {step:'discovery'});

  // Preview plans only; nothing reaches /process before the consent is confirmed.
  await submit(container);
  assert.deepEqual(env.posts('/plan').map(call => [call.url, call.body]), [['/api/series/saga%2Fone/plan', {steps:['discovery'], fresh:false}]]);
  assert.equal(env.posts('/process').length, 0);
  const html = node(container, 'plan').innerHTML;
  assert.ok(html.includes('data-consent-confirm') && html.includes('Run on 2 books · about $0.02'));
  assert.ok(html.includes('2 books in reading order; 1 missing or planned volume skipped'));
  assert.ok(html.includes('Text from each book → OpenAI · fast-model'));
  assert.ok(html.includes('The &lt;ninth&gt; lamp') && !html.includes('The <ninth> lamp'));
  assert.ok(html.includes('Retries and evidence repairs can add requests.'));
  assert.equal(node(container, 'plan').consent.focused, 1, 'the consent takes focus');

  // Confirm dispatches the reviewed plan exactly once, with its fingerprint and run options.
  const first = confirm(container); const second = confirm(container); await first; await second;
  assert.equal(env.posts('/process').length, 1);
  assert.deepEqual(env.posts('/process')[0].body, {steps:['discovery'], fresh:false, concurrency:2, expected_fingerprint:'reviewed-fingerprint'});
  assert.equal(changed, 1);
  assert.equal(node(container, 'plan').innerHTML, '', 'a confirmed plan is consumed');
  await confirm(container);
  assert.equal(env.posts('/process').length, 1);

  // Fresh samples and requests-at-once travel with the plan; a change after preview discards it.
  choose(container, {fresh:'on', concurrency:'4'});
  await submit(container);
  assert.deepEqual(env.posts('/plan').at(-1).body, {steps:['discovery'], fresh:true});
  choose(container, {concurrency:'1'});
  assert.equal(node(container, 'plan').innerHTML, '');
  await confirm(container);
  assert.equal(env.posts('/process').length, 1);
  await submit(container);
  form(container).fields.step = 'profiles';   // changed without an event: still refused
  await confirm(container);
  assert.equal(env.posts('/process').length, 1);
  assert.ok(node(container, 'processing-message').textContent.includes('Preview the series again'));

  // Cancel closes the consent without starting anything.
  form(container).fields.step = 'discovery';
  await submit(container);
  await planClick(container, '[data-consent-cancel]');
  assert.equal(node(container, 'plan').innerHTML, '');
  await confirm(container);
  assert.equal(env.posts('/process').length, 1);

  // Unknown price reads as unknown, never $0.
  {
    const unknown = environment(call => call.url.endsWith('/plan') ? {data:{...plan, estimated_cost_usd:null, known_cost_usd:0, unknown_cost_books:['book-9'],
      books:[plan.books[0], {...plan.books[1], plan:bookPlan(2, null)}]}} : ordinary(call));
    const box = await mounted(unknown); await submit(box);
    const text = node(box, 'plan').innerHTML;
    assert.ok(text.includes('Cost unknown') && text.includes('Run on 2 books · cost unknown') && text.includes('2 requests · cost unknown'));
    assert.ok(!text.includes('$0.00'));
  }

  // Blocked plans cannot be confirmed; a missing key offers setup instead.
  for (const [response, reason] of [
    [{...plan, missing_inputs:{'book-9':{discovery:['structure']}}}, 'needs accepted'],
    [{...plan, missing_credentials:[{provider:'openai', label:'OpenAI', needs:'api_key'}]}, 'Add the OpenAI API key in Providers &amp; settings'],
    [{...plan, fingerprint:null}, 'no fingerprint'],
    [{...plan, books:[]}, 'Add a book']]) {
    let opened = null;
    const blockedEnv = environment(call => call.url.endsWith('/plan') ? {data:response} : ordinary(call));
    const box = await mounted(blockedEnv, {onOpenSettings:provider => { opened = provider; }});
    await submit(box);
    assert.ok(node(box, 'plan').innerHTML.includes(reason), reason);
    assert.match(node(box, 'plan').innerHTML, /<button[^>]* disabled[^>]*data-consent-confirm/);
    await confirm(box);
    assert.equal(blockedEnv.posts('/process').length, 0, reason);
    if (response.missing_credentials?.length) { await planClick(box, '[data-series-setup]', {seriesSetup:'openai'}); assert.equal(opened, 'openai'); }
  }

  // A refused start (plan changed) is shown, consumes the plan and never retries by itself.
  {
    const refused = environment(call => call.url.endsWith('/process') ? {ok:false, status:409, data:{detail:'The series plan changed since the preview.'}} : ordinary(call));
    const box = await mounted(refused); await submit(box); await confirm(box);
    assert.equal(refused.posts('/process').length, 1);
    assert.ok(node(box, 'processing-message').textContent.includes('plan changed'));
    assert.equal(node(box, 'processing-message').getAttribute('role'), 'alert');
    assert.equal(node(box, 'plan').innerHTML, '');
    assert.ok(form(box).controls.every(control => !control.disabled));
  }

  // A late plan for a previously selected series is ignored.
  {
    let resolvePlan;
    const stale = environment(call => call.url.endsWith('/plan') ? new Promise(resolve => { resolvePlan = resolve; }) : ordinary(call));
    const box = await mounted(stale);
    const waiting = submit(box); await tick();
    await stale.api.render(box, {id:'second', name:'Second', books:[]});
    resolvePlan({data:plan}); await waiting;
    assert.equal(node(box, 'plan').innerHTML, '');
    await confirm(box);
    assert.equal(stale.posts('/process').length, 0);
  }

  // Runs: one state per book, polling while active, stop is coalesced and never starts work.
  {
    let cancelled = false, resolveCancel;
    const done = {...running, status:'cancelled', message:'Series cancelled.', children:[
      {...running.children[0], status:'cancelled', run:{status:'cancelled', outcomes:{}}},
      {...running.children[1], status:'cancelled', run_id:null, message:'Series cancelled before this book started.'}]};
    const runs = environment(call => {
      if (call.url.endsWith('/runs')) return {data:{runs:[cancelled ? done : running]}};
      if (call.url.endsWith('/cancel')) return new Promise(resolve => { resolveCancel = value => { cancelled = true; resolve(value); }; });
      return ordinary(call);
    });
    const box = await mounted(runs);
    const html = node(box, 'runs').innerHTML;
    assert.ok(html.includes('The first lamp') && html.includes('The ninth lamp') && !html.includes('book-9'));
    assert.ok(html.includes('>Running<') && html.includes('>Waiting<') && html.includes('0 of 2 books done'));
    assert.ok(html.includes('data-series-cancel="run/1"'));
    assert.equal([...runs.timers.values()][0].ms, 2500);
    const stop = cancelRun(box, 'run/1'); await tick(); await cancelRun(box, 'run/1');
    assert.equal(runs.calls.filter(call => call.url === '/api/jobs/run%2F1/cancel').length, 1);
    assert.ok(node(box, 'runs').innerHTML.includes('Stopping…'));
    resolveCancel({data:{status:'cancelled'}}); await stop;
    const after = node(box, 'runs').innerHTML;
    assert.ok(after.includes('>Cancelled<') && after.includes('>Not started<'));
    assert.equal(runs.timers.size, 0);
    assert.equal(runs.posts('/process').length, 0);
    await runs.api.render(box, null);
    assert.equal(box.innerHTML, '');
  }

  // Series memory: later books read earlier books, so consent shows "up to" and never turns unknown into $0.
  {
    const memory = {...plan, steps:['profiles'], configs:{profiles:{provider:'openai', model:'deep-model'}}, cached_units:1,
      context_pending_books:['book-9'], up_to:{requests:4, estimated_input_tokens:900, output_token_allowance:9000, estimated_cost_usd:.05, known_cost_usd:.05},
      books:[{...plan.books[0], context_pending:[], up_to:{requests:1, estimated_cost_usd:.01}},
        {...plan.books[1], context_pending:['profiles'], up_to:{requests:3, estimated_cost_usd:.04}}]};
    const env = environment();
    const consent = env.api.consentFor(memory, defs);
    assert.equal(consent.estimate.requests, 4);
    assert.equal(consent.estimate.cost, .05);
    assert.match(consent.estimate.note, /Up to 4 requests\. One later book reads the results earlier books accept during this run, so the cost can be higher/);
    assert.ok(!consent.estimate.note.includes('saved result'), 'reuse is not promised for context-pending work');
    const unknown = env.api.consentFor({...memory, unknown_cost_books:['book-9'], up_to:{...memory.up_to, estimated_cost_usd:null, known_cost_usd:.01}}, defs);
    assert.equal(unknown.estimate.cost, null);
    const shown = environment(call => call.url.endsWith('/plan') ? {data:memory} : ordinary(call));
    const box = await mounted(shown); await submit(box);
    const text = node(box, 'plan').innerHTML;
    assert.ok(text.includes('up to 3 requests') && text.includes('Reads earlier books') && text.includes('Run on 2 books · about $0.05'));
  }

  // A run paused for review: say which book, link to its Analyze step, resume or stop.
  {
    const paused = {...running, message:'Waiting for your review of The first lamp.', waiting_for_review:{book_id:'book-1', child_job_id:'c1',
      title:'The first lamp', position:1, steps:['profiles'], since:'2026-09-28T00:00:00+00:00'}, children:[
      {...running.children[0], status:'completed', message:'Waiting for your review: Character profiles',
        run:{status:'completed', outcomes:{profiles:{status:'completed', scope_count:1, accepted:false}}}},
      {...running.children[1], message:'Waiting for your review of The first lamp.'}]};
    let resumed = false;
    const env = environment(call => {
      if (call.url.endsWith('/runs')) return {data:{runs:[resumed ? {...paused, waiting_for_review:null, message:'Resuming after your review.'} : paused]}};
      if (call.url.endsWith('/resume')) { resumed = true; return {data:{...paused, waiting_for_review:null}}; }
      return ordinary(call);
    });
    same(env.api.reviewWait(paused), {bookId:'book-1', title:'The first lamp', steps:['profiles'], href:'#/book/book-1/analysis/profiles'});
    assert.equal(env.api.reviewWait({...paused, status:'cancelled'}), null);
    const box = await mounted(env);
    const html = node(box, 'runs').innerHTML;
    assert.ok(html.includes('Waiting for your review of The first lamp') && html.includes('href="#/book/book-1/analysis/profiles"'));
    assert.ok(html.includes('data-series-resume="run/1"') && html.includes('data-series-cancel="run/1"'));
    assert.ok(html.includes('>Waiting for review<'), 'the reviewed book reads as waiting for review');
    await node(box, 'runs').listeners.click({target:{closest:s => s === '[data-series-resume]' ? {dataset:{seriesResume:'run/1'}} : null}});
    assert.equal(env.calls.filter(call => call.method === 'POST' && call.url === '/api/series/saga%2Fone/runs/run%2F1/resume').length, 1);
    assert.ok(!node(box, 'runs').innerHTML.includes('data-series-resume'));
    assert.equal(env.posts('/process').length, 0);
  }

  // Map JSON stays literal text.
  {
    const maps = environment(), box = await mounted(maps);
    await click(box, 'map');
    assert.ok(node(box, 'map-output').pre.textContent.includes('<script>'));
    assert.ok(!node(box, 'map-output').innerHTML.includes('<script>'));
  }
  console.log('Series processing UI checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
