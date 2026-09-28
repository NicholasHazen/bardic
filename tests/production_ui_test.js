const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Container {
  constructor() {
    this.innerHTML = '';
    this.listeners = {};
    this.attributes = {};
    this.nodes = {
      message:{textContent:'', classList:{toggle(){}}, setAttribute(){}},
      coverage:{innerHTML:''}, plan:{innerHTML:''}, hint:{textContent:''},
      start:{disabled:true}, refresh:{disabled:false}, budget:{disabled:false}
    };
    this.controls = [this.nodes.start, this.nodes.budget, {disabled:false}];
  }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  querySelector(selector) {
    return ({'[data-production-message]':this.nodes.message, '[data-production-coverage]':this.nodes.coverage,
      '[data-production-plan]':this.nodes.plan, '[data-production-phase-hint]':this.nodes.hint,
      '[data-production-action="start"]':this.nodes.start, '[data-production-action="census"]':this.nodes.refresh,
      '[name="budget_usd"]':this.nodes.budget})[selector] || null;
  }
  querySelectorAll() { return this.controls; }
  setAttribute(name, value) { this.attributes[name] = value; }
}

const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle() { for (let i = 0; i < 7; i++) await tick(); }
function edit(container, name, value) {
  container.listeners.input({target:{name, value:typeof value === 'boolean' ? '' : value,
    checked:typeof value === 'boolean' ? value : false, closest:() => ({})}});
}
function preview(container) {
  container.listeners.submit({target:{matches:() => true}, preventDefault(){}});
}
function click(container, action) {
  const button = {dataset:{productionAction:action}};
  container.listeners.click({target:{closest:() => button}});
}

const book = {id:'book/9', revision:2, chapters:[{id:'chapter-1', title:'The <Gate>'}, {id:'chapter-2', title:'The Return'}]};
const coverage = {local:{local_chapters_scanned:3, chapters:[{},{},{}], eligible_chapters:2,
  characters:[{id:'mira', name:'Mira <script>x</script>', priority:'deep', known_character:true, mentions:20,
    chapter_count:2, dialogue_turns:6, recommended_evidence_limit:16, ambiguous_aliases:1}]},
  semantic_chapters_complete:1, eligible_chapters:2, profiles_provisional:true,
  profiles_current:0, profiles_total:1, characters:[{character_id:'mira', state:'stale', provisional:true}],
  usage:{attempts:3, input_tokens:100, output_tokens:20, estimated_spend_usd:.12, unknown_cost_attempts:0}};
const plan = {requests:9, cached_units:4, estimated_input_tokens:12000, output_token_allowance:8000,
  estimated_cost_usd:.3, future_work_unknown:false, coverage, note:'Approximate estimate <safe>'};

function environment(handler) {
  const calls = [];
  const scope = {window:{}, fetch:async (url, options) => {
    const call = {url, method:options.method || 'GET', body:options.body ? JSON.parse(options.body) : undefined};
    calls.push(call);
    const result = await handler(call);
    return {ok:result.ok !== false, status:result.status || 200, json:async () => result.data};
  }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../spintails/static/production.js'), 'utf8'), scope);
  return {calls, render:scope.window.SpinTailsProduction.render};
}

function ordinary(call) { return {data:call.method === 'GET' ? coverage : plan}; }
const cloud = {provider:'anthropic', chapterId:'chapter-1', scanModel:'fast-model', model:'deep-model', busy:false};

(async () => {
  // A read-only local census is the only automatic request. Same revision renders cache it.
  const starts = [];
  const env = environment(ordinary);
  const container = new Container();
  const options = {...cloud, onStart:async body => starts.push(JSON.parse(JSON.stringify(body)))};
  await env.render(container, book, options);
  assert.deepEqual(env.calls.map(call => [call.method,call.url]), [['GET','/api/books/book%2F9/preprocessing']]);
  assert.ok(container.nodes.coverage.innerHTML.includes('1 / 2 eligible sections'));
  assert.ok(container.nodes.coverage.innerHTML.includes('Profiles remain provisional'));
  assert.ok(container.nodes.coverage.innerHTML.includes('0 / 1 current or reviewed'));
  assert.ok(container.nodes.coverage.innerHTML.includes('stale'));
  assert.ok(container.nodes.coverage.innerHTML.includes('Mira &lt;script&gt;x&lt;/script&gt;'));
  assert.ok(container.nodes.coverage.innerHTML.includes('16 excerpts'));
  assert.ok(container.innerHTML.includes('<strong>Fast scan:</strong> fast-model'));
  assert.ok(container.innerHTML.includes('<strong>Profiles &amp; direction:</strong> deep-model'));
  assert.equal(container.nodes.start.disabled, true);
  await env.render(container, {...book}, options);
  assert.equal(env.calls.length, 1);
  click(container, 'start');
  await settle();
  assert.equal(starts.length, 0, 'Starting requires a concrete preview');

  // Default fast scan covers the whole book; never sends the selected chapter.
  preview(container);
  await settle();
  const firstPlan = env.calls.at(-1);
  assert.equal(firstPlan.method, 'POST');
  assert.equal(firstPlan.body.phase, 'scan');
  assert.ok(!Object.hasOwn(firstPlan.body, 'chapter_id'));
  assert.deepEqual(firstPlan.body.limits, {max_requests:25, max_input_tokens:1000000, max_output_tokens:100000, budget_usd:1});
  assert.equal(firstPlan.body.resume, true);
  assert.equal(container.nodes.start.disabled, false);
  assert.ok(container.nodes.plan.innerHTML.includes('$0.30'));
  assert.ok(container.nodes.plan.innerHTML.includes('Approximate estimate &lt;safe&gt;'));
  click(container, 'start');
  await settle();
  assert.deepEqual(starts[0], firstPlan.body);
  assert.equal(container.nodes.start.disabled, true, 'Next run needs a refreshed preview');

  // Explicit token-only mode sends null; direct stage alone includes a chapter.
  edit(container, 'phase', 'direct');
  edit(container, 'token_only', true);
  edit(container, 'max_requests', '4');
  assert.equal(container.nodes.budget.disabled, true);
  preview(container);
  await settle();
  assert.equal(env.calls.at(-1).body.chapter_id, 'chapter-1');
  assert.equal(env.calls.at(-1).body.phase, 'direct');
  assert.equal(env.calls.at(-1).body.limits.budget_usd, null);
  assert.equal(env.calls.at(-1).body.limits.max_requests, 4);
  assert.ok(container.nodes.plan.innerHTML.includes('Dollar guard is off'));
  assert.ok(container.nodes.plan.innerHTML.includes('stop at its request cap'));

  // Every edit invalidates a preview; invalid input never reaches the plan endpoint.
  edit(container, 'max_requests', '0');
  assert.equal(container.nodes.start.disabled, true);
  const requestsBefore = env.calls.length;
  preview(container);
  await settle();
  assert.equal(env.calls.length, requestsBefore);
  assert.match(container.nodes.message.textContent, /Request cap must be/);
  edit(container, 'max_requests', '25');
  edit(container, 'phase', 'profiles');
  preview(container);
  await settle();
  assert.ok(!Object.hasOwn(env.calls.at(-1).body, 'chapter_id'));
  assert.equal(env.calls.at(-1).body.phase, 'profiles');

  // Model/provider/chapter/revision changes invalidate, while busy-only refreshes do not refetch census.
  await env.render(container, book, {...options, model:'other-deep-model'});
  assert.equal(container.nodes.start.disabled, true);
  assert.equal(env.calls.filter(call => call.method === 'GET').length, 1);
  await env.render(container, {...book, revision:3}, {...options, model:'other-deep-model'});
  assert.equal(env.calls.filter(call => call.method === 'GET').length, 2);
  await env.render(container, {...book, revision:3}, {...options, model:'other-deep-model', busy:true});
  assert.ok(container.controls.every(control => control.disabled));
  assert.equal(container.nodes.refresh.disabled, false, 'Free census remains available while a job runs');
  const busyRequests = env.calls.length;
  preview(container);
  await settle();
  assert.equal(env.calls.length, busyRequests);

  // Local controls describe rules rather than semantic analysis and ignore hidden invalid dollar edits.
  const localStarts = [];
  const localEnv = environment(call => ({data:call.method === 'GET' ? coverage : {...plan, requests:0, estimated_cost_usd:0}}));
  const localContainer = new Container();
  await localEnv.render(localContainer, book, {...cloud, provider:'local', onStart:body => localStarts.push(body)});
  assert.ok(localContainer.innerHTML.includes('Local heuristic draft'));
  assert.ok(localContainer.innerHTML.includes('does not provide semantic whole-book discovery'));
  assert.ok(!localContainer.innerHTML.includes('Total tracked book allowance'));
  preview(localContainer);
  await settle();
  assert.equal(localEnv.calls.at(-1).body.phase, 'full');
  assert.ok(!Object.hasOwn(localEnv.calls.at(-1).body, 'chapter_id'));
  assert.ok(localContainer.nodes.plan.innerHTML.includes('no paid provider requests'));

  // Unknown prices, current spend and full-pipeline unknown work are explicit.
  const unknownEnv = environment(call => ({data:call.method === 'GET' ? coverage : {...plan,
    estimated_cost_usd:null, future_work_unknown:true}}));
  const unknownContainer = new Container();
  await unknownEnv.render(unknownContainer, book, options);
  edit(unknownContainer, 'phase', 'full');
  preview(unknownContainer);
  await settle();
  assert.ok(unknownContainer.nodes.plan.innerHTML.includes('Price or earlier cost is unknown'));
  assert.ok(unknownContainer.nodes.plan.innerHTML.includes('New discoveries can add profiles'));
  assert.ok(!Object.hasOwn(unknownEnv.calls.at(-1).body, 'chapter_id'));

  // Completed discovery and unfinished profile refinement remain distinct.
  const scannedEnv = environment(() => ({data:{...coverage, semantic_chapters_complete:2, whole_book_discovered:true}}));
  const scannedContainer = new Container();
  await scannedEnv.render(scannedContainer, book, options);
  assert.ok(scannedContainer.nodes.coverage.innerHTML.includes('Whole-book scan complete'));
  assert.ok(scannedContainer.nodes.coverage.innerHTML.includes('0 / 1 current or reviewed'));
  assert.ok(scannedContainer.nodes.coverage.innerHTML.includes('Profiles remain provisional'));

  // Failed previews cannot start and surface the server's useful error.
  const failed = environment(call => call.method === 'GET' ? {data:coverage} : {ok:false, status:400, data:{detail:'Choose a chapter first.'}});
  const failedContainer = new Container();
  await failed.render(failedContainer, book, options);
  preview(failedContainer);
  await settle();
  assert.equal(failedContainer.nodes.start.disabled, true);
  assert.match(failedContainer.nodes.message.textContent, /Choose a chapter first/);

  // A pending plan for an old model cannot become actionable after a settings change.
  let resolvePlan;
  const pending = environment(call => call.method === 'GET' ? {data:coverage} : new Promise(resolve => { resolvePlan = resolve; }));
  const pendingContainer = new Container();
  await pending.render(pendingContainer, book, options);
  preview(pendingContainer);
  await tick();
  await pending.render(pendingContainer, book, {...options, scanModel:'changed-fast-model'});
  resolvePlan({data:plan});
  await settle();
  assert.equal(pendingContainer.nodes.start.disabled, true);

  // Refresh remains a local GET and signals the parent only once.
  let refreshed = 0;
  await env.render(container, {...book, revision:3}, {...options, onRefresh:() => refreshed++});
  click(container, 'census');
  await settle();
  assert.equal(env.calls.at(-1).method, 'GET');
  assert.equal(refreshed, 1);
  assert.match(container.nodes.message.textContent, /No model requests were made/);

  console.log('Production UI behavior checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
