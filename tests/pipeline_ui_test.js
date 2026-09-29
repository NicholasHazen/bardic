const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Node {
  constructor() { this.innerHTML = ''; this.textContent = ''; this.disabled = false; this.open = false; }
  setAttribute() {}
  classList = {toggle(){}};
  querySelector(selector) { return this.children?.[selector] || null; }
}
class Container {
  constructor() {
    this.listeners = {};
    this.nodes = Object.fromEntries(['stages','filters','artifacts','inspector','activity','story','message','refresh','previous','next','artifact-section','story-section','search-results','search'].map(name => [name,new Node()]));
    this.nodes['artifact-section'].open = true;
    this.nodes.inspector.children = {'[data-pipeline-json]':new Node(), '[data-pipeline-inspect-error]':new Node()};
  }
  set innerHTML(value) {
    this.html = value;
    for (const node of Object.values(this.nodes)) node.innerHTML = '';
    for (const node of Object.values(this.nodes.inspector.children)) node.textContent = '';
  }
  get innerHTML() { return this.html; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  querySelector(selector) {
    const field = selector.match(/^\[data-pipeline-([\w-]+)\]$/)?.[1];
    if (field) return this.nodes[field] || null;
    const action = selector.match(/^\[data-pipeline-action="([\w-]+)"\]$/)?.[1];
    return action ? this.nodes[action] || null : null;
  }
}

const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle() { for (let i = 0; i < 8; i++) await tick(); }
function click(container, type, value, ownerBookId) {
  const node = {dataset:{[`pipeline${type.charAt(0).toUpperCase() + type.slice(1)}`]:value,
    ...(ownerBookId ? {pipelineArtifactBook:ownerBookId} : {})}};
  container.listeners.click({target:{closest:selector => selector === `[data-pipeline-${type}]` ? node : null}});
}
function filter(container, name, value) { container.listeners.change({target:{name:`pipeline_${name}`, value}}); }
function search(container, query, scope = 'book') {
  let prevented = false;
  container.listeners.submit({target:{matches:selector => selector === '[data-pipeline-search-form]',
    elements:{query:{value:query},scope:{value:scope}}}, preventDefault(){ prevented = true; }});
  assert.ok(prevented);
}

const book = {id:'book/9', revision:2};
const snapshot = {schema_version:1, book_id:book.id, stages:[
  {id:'import', label:'Imported source', status:'completed', completed:1,total:1,unit_label:'book',dependencies:[],artifact_count:1},
  {id:'discovery', label:'Character <scan>', status:'partial', completed:1,total:3,unit_label:'sections',dependencies:['import'],artifact_count:2,stale_count:0,candidate_count:2,note:'Saved evidence & references.'},
  {id:'voices', label:'Voice assignments', status:'ready', completed:2,total:2,unit_label:'voices',dependencies:['discovery'],artifact_count:1},
  {id:'directing', label:'Scene direction', status:'stale', completed:3,total:3,unit_label:'eligible sections',dependencies:['discovery'],artifact_count:1,stale_count:1,candidate_count:0},
  {id:'alignment', label:'Word alignment', status:'planned', completed:null,total:null,unit_label:'words',dependencies:['narration'],artifact_count:0}
], artifact_kinds:['discovery','profiles'], capabilities:{word_alignment:false}, notes:['Historical calls may be untracked.'],
  jobs:[{id:'run-1',kind:'analyze',phase:'scan',status:'failed',progress:1,total:3,message:'Saved <one> section',error:'Bad <evidence>',created_at:'2026-09-27T16:00:00Z'}],
  usage:{attempt_count:1,estimated_spend_usd:null,unknown_cost_attempts:1},
  attempts:[{id:'attempt-1',run_id:'run-1',unit_key:'discovery:old',stage:'discovery',provider:'test',model:'fast',
    status:'received',http_status:200,input_tokens:null,output_tokens:null,reserved_input_tokens:4000,reserved_output_tokens:1000,
    charged_estimate_usd:null,validation_state:'unknown',created_at:'2026-09-27T16:00:00Z'},
    {id:'attempt-2',status:'interrupted_unknown',validation_state:'unknown'}],
  events:[{run_id:'run-2',stage:'discovery',unit_key:'discovery:old',event:'cache_hit',artifact_id:'artifact-0',created_at:'2026-09-27T16:01:00Z'}]
};
const allArtifacts = Array.from({length:35}, (_,i) => ({id:`artifact-${i}`,kind:'discovery',logical_key:`discovery:${i}`,
  label:`Saved <output> ${i}`,stage:'discovery',created_at:'2026-09-27T16:00:00Z',provider:'test',model:'fast',
  is_current:i % 2 === 0,dependencies:['artifact-0'],schema_version:1,legacy_provenance:false}));
const story = {chapters:[{id:'chapter-1',title:'The <Gate>',kind:'chapter',start:0,end:300,
  scenes:[{id:'scene-1',title:'At the gate',start:10,end:300,passage_ids:['passage-1','passage-2'],character_ids:['mira']}]}],
  characters:[{id:'mira',name:'Mira & Co'}],reference_counts:{mention:3,dialogue:2,profile_evidence:1},note:'Saved source structure.'};

function ordinary(call) {
  if (call.url.endsWith('/pipeline')) return {data:snapshot};
  if (call.url.endsWith('/story-map')) return {data:story};
  if (call.url.includes('/search?')) {
    const params = new URL(call.url,'http://localhost').searchParams;
    return {data:{available:true,query:params.get('q'),scope:params.get('scope'),items:[{
      book_id:'earlier-book',book_title:'Earlier <Book>',chapter_id:'chapter-1',chapter_title:'The & Gate',
      passage_id:'passage-1',start:12,end:55,text:'Mira said <script>alert("x")</script>.',rank:-1.234
    }],note:'Lexical passage matches only.'}};
  }
  if (call.url.includes('/artifacts?')) {
    const query = new URL(call.url, 'http://localhost').searchParams;
    const offset = Number(query.get('offset'));
    const limit = Number(query.get('limit'));
    const current = query.get('current');
    const items = allArtifacts.filter(item => current === null || String(item.is_current) === current);
    return {data:{items:items.slice(offset,offset+limit),total:items.length,offset,limit}};
  }
  if (call.url.includes('/artifacts/')) return {data:{...allArtifacts[0],payload:{source:'<script>alert("x")</script>',nested:{evidence:'Mira said <hello>'}}}};
  throw new Error(`Unexpected URL ${call.url}`);
}

function environment(handler = ordinary) {
  const calls = [];
  let clock = 10000;
  class TestDate extends Date { static now() { return clock; } }
  const scope = {window:{}, URLSearchParams, Date:TestDate, fetch:async (url, options) => {
    const call = {url, method:options.method || 'GET'};
    calls.push(call);
    const result = await handler(call);
    return {ok:result.ok !== false,status:result.status || 200,json:async () => result.data};
  }};
  vm.createContext(scope);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../bardic/static/ui.js'),'utf8'), scope);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../bardic/static/pipeline.js'),'utf8'), scope);
  return {calls,render:scope.window.BardicPipeline.render,advance:ms => { clock += ms; }};
}

(async () => {
  const env = environment();
  const container = new Container();
  await env.render(container,book,{busy:false});
  assert.equal(env.calls.length,2);
  assert.ok(env.calls.every(call => call.method === 'GET'));
  assert.ok(container.innerHTML.includes('/api/books/book%2F9/analysis-export'));
  assert.ok(container.nodes.stages.innerHTML.includes('Character &lt;scan&gt;'));
  assert.ok(container.nodes.stages.innerHTML.includes('Word alignment is planned'));
  assert.ok(container.nodes.stages.innerHTML.includes('data-pipeline-stage="import"'));
  assert.ok(container.nodes.stages.innerHTML.includes('Voice assignments'));
  assert.ok(container.nodes.stages.innerHTML.includes('Out of date'));
  assert.ok(container.nodes.stages.innerHTML.includes('1 out of date'));
  assert.ok(container.nodes.stages.innerHTML.includes('2 waiting for review'));
  assert.ok(!container.nodes.stages.innerHTML.includes('0 out of date'));
  assert.ok(container.nodes.filters.innerHTML.includes('aria-label="Result type"'));
  assert.ok(container.innerHTML.includes('aria-label="Search scope"'));
  assert.ok(container.nodes.artifacts.innerHTML.includes('Saved &lt;output&gt; 0'));
  assert.ok(container.nodes.artifacts.innerHTML.includes('1–30 of 35 saved versions'));
  assert.ok(container.nodes.activity.innerHTML.includes('Response received'));
  assert.ok(container.nodes.activity.innerHTML.includes('1 tracked request'), 'the count is usage.attempt_count, not the length of the recent list');
  assert.ok(container.nodes.activity.innerHTML.includes('HTTP 200'));
  assert.ok(container.nodes.activity.innerHTML.includes('<td>Not recorded</td>'));
  assert.ok(container.nodes.activity.innerHTML.includes('4,000 reserved / 1,000 reserved'));
  assert.ok(container.nodes.activity.innerHTML.includes('Saved result reused'));
  assert.ok(container.nodes.activity.innerHTML.includes('Bad &lt;evidence&gt;'));
  assert.ok(container.nodes.activity.innerHTML.includes('Interrupted · outcome unknown'));

  // Parent renders do not reset state or poll repeatedly; active work refreshes at ~3s.
  await env.render(container,book,{busy:true});
  assert.equal(env.calls.length,2);
  env.advance(2900);
  await env.render(container,book,{busy:true});
  assert.equal(env.calls.length,2);
  env.advance(200);
  await env.render(container,book,{busy:true});
  assert.equal(env.calls.length,4);
  await env.render(container,book,{busy:false});
  assert.equal(env.calls.length,6,'Finishing refreshes even without a book revision change');

  // Server-side pagination and current/history filtering maintain meaningful totals.
  click(container,'action','next');
  await settle();
  assert.ok(env.calls.at(-1).url.includes('offset=30'));
  assert.ok(container.nodes.artifacts.innerHTML.includes('31–35 of 35 saved versions'));
  filter(container,'current','false');
  await settle();
  assert.ok(env.calls.at(-1).url.includes('current=false'));
  assert.ok(env.calls.at(-1).url.includes('offset=0'));
  assert.ok(container.nodes.artifacts.innerHTML.includes('1–17 of 17 saved versions'));
  click(container,'stage','discovery');
  await settle();
  assert.ok(env.calls.at(-1).url.includes('stage=discovery'));
  assert.ok(container.nodes.stages.innerHTML.includes('data-pipeline-stage="discovery" aria-pressed="true"'));

  // Artifact output is assigned to textContent, never interpreted as HTML.
  click(container,'artifact','artifact-2');
  await settle();
  assert.ok(container.nodes.inspector.children['[data-pipeline-json]'].textContent.includes('<script>alert'));
  assert.ok(!container.nodes.inspector.innerHTML.includes('<script>alert'));
  assert.ok(container.nodes.inspector.innerHTML.includes('data-pipeline-artifact="artifact-0"'));
  assert.ok(container.nodes.inspector.innerHTML.includes('Recorded dependencies'));

  // Recorded cross-book dependencies fetch using their owner while retaining this panel's book.
  const cross = environment(call => {
    if (call.url.endsWith('/artifacts/current')) return {data:{id:'current',book_id:book.id,
      dependencies:['earlier-output'],dependency_links:[{id:'earlier-output',book_id:'earlier/book'}],payload:{}}};
    if (call.url.endsWith('/artifacts/earlier-output')) return {data:{id:'earlier-output',book_id:'earlier/book',
      dependencies:['earlier-source'],dependency_links:[{id:'earlier-source',book_id:'earlier/book'}],payload:{earlier:true}}};
    return ordinary(call);
  });
  const crossContainer = new Container();
  await cross.render(crossContainer,book,{});
  click(crossContainer,'artifact','current');
  await settle();
  assert.ok(crossContainer.nodes.inspector.innerHTML.includes('data-pipeline-artifact-book="earlier/book"'));
  assert.ok(crossContainer.nodes.inspector.innerHTML.includes('From another book'));
  click(crossContainer,'artifact','earlier-output','earlier/book');
  await settle();
  assert.equal(cross.calls.at(-1).url,'/api/books/earlier%2Fbook/artifacts/earlier-output');
  assert.ok(crossContainer.nodes.inspector.innerHTML.includes('Related book'));
  assert.ok(crossContainer.innerHTML.includes('/api/books/book%2F9/analysis-export'));
  assert.ok(crossContainer.nodes.inspector.children['[data-pipeline-json]'].textContent.includes('"earlier": true'));

  // Story structure loads on demand once, preserving honest reference semantics.
  assert.ok(!env.calls.some(call => call.url.endsWith('/story-map')));
  container.nodes['story-section'].open = true;
  container.listeners.toggle({target:{matches:() => true,open:true}});
  await settle();
  assert.ok(env.calls.at(-1).url.endsWith('/story-map'));
  assert.ok(container.nodes.story.innerHTML.includes('The &lt;Gate&gt;'));
  assert.ok(container.nodes.story.innerHTML.includes('Mira &amp; Co'));
  assert.ok(container.nodes.story.innerHTML.includes('not proof of physical presence'));
  const storyReads = env.calls.filter(call => call.url.endsWith('/story-map')).length;
  container.listeners.toggle({target:{matches:() => true,open:true}});
  await settle();
  assert.equal(env.calls.filter(call => call.url.endsWith('/story-map')).length, storyReads);

  // Lexical search is explicit, scoped, encoded and never interpreted as HTML.
  assert.ok(!env.calls.some(call => call.url.includes('/search?')));
  search(container,'  ');
  await settle();
  assert.ok(container.nodes['search-results'].innerHTML.includes('Enter a name or keywords'));
  assert.ok(!env.calls.some(call => call.url.includes('/search?')));
  search(container,'Mira <north>','earlier');
  await settle();
  const searched = new URL(env.calls.at(-1).url,'http://localhost').searchParams;
  assert.equal(searched.get('q'),'Mira <north>');
  assert.equal(searched.get('scope'),'earlier');
  assert.equal(searched.get('limit'),'20');
  assert.ok(container.nodes['search-results'].innerHTML.includes('Earlier &lt;Book&gt;'));
  assert.ok(container.nodes['search-results'].innerHTML.includes('&lt;script&gt;alert'));
  assert.ok(!container.nodes['search-results'].innerHTML.includes('<script>'));
  assert.ok(container.nodes['search-results'].innerHTML.includes('this book and earlier series books'));
  assert.ok(container.nodes['search-results'].innerHTML.includes('not identity or speaker confidence'));
  assert.equal(container.nodes.search.disabled,false);

  // Empty, unavailable and failed search outcomes are distinct and recoverable.
  for (const [result, expected] of [
    [{data:{available:true,query:'missing',scope:'book',items:[]}}, 'No passages matched'],
    [{data:{available:false,query:'Mira',scope:'book',items:[],note:'FTS5 is unavailable.'}}, 'Source search is unavailable'],
    [{ok:false,status:400,data:{detail:'Unsupported query syntax.'}}, 'Unsupported query syntax.']
  ]) {
    const searchEnv = environment(call => call.url.includes('/search?') ? result : ordinary(call));
    const searchContainer = new Container();
    await searchEnv.render(searchContainer,book,{});
    assert.ok(!searchEnv.calls.some(call => call.url.includes('/search?')));
    search(searchContainer,'Mira');
    await settle();
    assert.ok(searchContainer.nodes['search-results'].innerHTML.includes(expected));
    assert.equal(searchContainer.nodes.search.disabled,false);
  }

  // Failed inspection keeps the explorer usable and displays readable errors.
  const fail = environment(call => call.url.includes('/artifacts/') ? {ok:false,status:404,data:{detail:'Artifact not found.'}} : ordinary(call));
  const failedContainer = new Container();
  await fail.render(failedContainer,book,{});
  click(failedContainer,'artifact','gone');
  await settle();
  assert.match(failedContainer.nodes.inspector.children['[data-pipeline-inspect-error]'].textContent,/Artifact not found/);

  // Selecting another book prevents a pending old artifact from appearing there.
  let resolveOld;
  const race = environment(call => call.url.includes('/artifacts/') ? new Promise(resolve => { resolveOld = resolve; }) : ordinary(call));
  const raceContainer = new Container();
  await race.render(raceContainer,book,{});
  click(raceContainer,'artifact','old','earlier/book');
  await tick();
  await race.render(raceContainer,{id:'other',revision:1},{});
  resolveOld({data:{id:'old',payload:{secret:'old book source'}}});
  await settle();
  assert.ok(!raceContainer.nodes.inspector.children['[data-pipeline-json]'].textContent.includes('old book source'));
  assert.ok(race.calls.every(call => call.method === 'GET'));

  // An earlier book's pending source search cannot appear after book selection changes.
  let oldSearch;
  const searchRace = environment(call => call.url.includes('/search?') ? new Promise(resolve => { oldSearch = resolve; }) : ordinary(call));
  const searchRaceContainer = new Container();
  await searchRace.render(searchRaceContainer,book,{});
  search(searchRaceContainer,'Mira');
  await tick();
  await searchRace.render(searchRaceContainer,{id:'different-book',revision:1},{});
  oldSearch({data:{available:true,query:'old source',scope:'book',items:[],note:'Wrong book results.'}});
  await settle();
  assert.ok(!searchRaceContainer.nodes['search-results'].innerHTML.includes('Wrong book results.'));

  // Publication of a new revision supersedes a snapshot requested before it.
  let oldSnapshot;
  let pipelineReads = 0;
  const revisionEnv = environment(call => {
    if (call.url.endsWith('/pipeline')) {
      pipelineReads++;
      if (pipelineReads === 1) return new Promise(resolve => { oldSnapshot = resolve; });
      return {data:{...snapshot,notes:['Fresh published revision.']}};
    }
    return ordinary(call);
  });
  const revisionContainer = new Container();
  const firstRevision = revisionEnv.render(revisionContainer,book,{busy:true});
  await tick();
  await revisionEnv.render(revisionContainer,{...book,revision:3},{busy:false});
  oldSnapshot({data:{...snapshot,notes:['Stale snapshot must be ignored.']}});
  await firstRevision;
  assert.ok(revisionContainer.nodes.stages.innerHTML.includes('Fresh published revision.'));
  assert.ok(!revisionContainer.nodes.stages.innerHTML.includes('Stale snapshot must be ignored.'));

  // Clearing while a snapshot is pending is also safe.
  const gates = [];
  const clear = environment(call => new Promise(resolve => gates.push(() => resolve(ordinary(call)))));
  const clearContainer = new Container();
  const pending = clear.render(clearContainer,book,{});
  await clear.render(clearContainer,null,{});
  gates.forEach(resolve => resolve());
  await pending;
  assert.equal(clearContainer.innerHTML,'');

  assert.ok(env.calls.every(call => call.method === 'GET'),'No browse action starts paid work');
  console.log('Pipeline UI behavior checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
