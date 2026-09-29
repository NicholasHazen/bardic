/* Minimal DOM boundary: test rendered safety, requests, mutations and stale results. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Container {
  constructor() {
    this.innerHTML = '';
    this.listeners = {};
    this.attributes = {};
    this.controls = [{disabled:false}, {disabled:false}];
    this.status = {textContent:'', classList:{toggle(){}}, setAttribute(){}};
  }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  querySelector(selector) {
    if (selector === '[data-series-status]') return this.status;
    if (selector === '[data-series-membership]') return this.membershipForm || null;
    if (selector === '.series-identities[open]') return this.identitiesOpen || null;
    return null;
  }
  querySelectorAll() { return this.controls; }
  setAttribute(name, value) { this.attributes[name] = value; }
}

function form(marker, fields, dataset = {}) {
  return {dataset, elements:Object.fromEntries(Object.entries(fields).map(([key, value]) => [key, {value}])),
    matches:selectors => selectors.split(',').includes(`[${marker}]`)};
}

function submit(container, target) {
  let prevented = false;
  container.listeners.submit({target, preventDefault(){ prevented = true; }});
  assert.ok(prevented);
}

const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle() { for (let i = 0; i < 6; i++) await tick(); }

function environment(handler) {
  const calls = [];
  const scope = {window:{}, fetch:async (url, options) => {
    const call = {url, method:options.method, body:options.body ? JSON.parse(options.body) : undefined};
    calls.push(call);
    const result = await handler(call);
    return {ok:result.ok !== false, status:result.status || 200, json:async () => result.data};
  }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../bardic/static/series.js'), 'utf8'), scope);
  return {render:scope.window.BardicSeries.render, calls};
}

const book = {id:'book/9', characters:[{id:'narrator', name:'Narrator'}, {id:'mira', name:'Mira <script>x</script>'}]};
const saga = {id:'series-1', name:'The <Lantern> & Books'};
const identity = {id:'identity-1', name:'Mira', links:[]};
function reads(call, membership = {series_id:saga.id, series_name:saga.name, position:9}, context = null, suggestions = null) {
  if (call.url === '/api/series') return {data:[saga]};
  if (call.url.endsWith('/series/suggestions')) return {data:suggestions || {book_id:book.id, series_id:membership?.series_id ?? null, suggestions:[]}};
  if (call.url.endsWith('/series/context')) return {data:context || {characters:[], included_observations:0}};
  if (call.url.endsWith('/series')) return {data:{membership, links:[], characters:[identity]}};
  throw new Error(`Unexpected request ${call.method} ${call.url}`);
}

(async () => {
  // Rendering and polling never create links or initiate processing; strings escape.
  const first = environment(call => reads(call));
  const container = new Container();
  await first.render(container, book);
  assert.equal(first.calls.length, 4);
  assert.ok(first.calls.every(call => call.method === 'GET'));
  assert.ok(container.innerHTML.includes('The &lt;Lantern&gt; &amp; Books'));
  assert.ok(container.innerHTML.includes('Mira &lt;script&gt;x&lt;/script&gt;'));
  assert.ok(!container.innerHTML.includes('<script>x'));
  assert.ok(!container.innerHTML.includes('Linked to Mira'));
  assert.ok(container.innerHTML.includes('Matching names never link automatically'));
  await first.render(container, {...book});
  assert.equal(first.calls.length, 4, 'Repeated parent renders must preserve unsaved panel edits');

  // Explicit selected identity creates one link request; empty value unlinks.
  const writes = [];
  const linked = environment(call => {
    if (call.method === 'PUT') { writes.push(call); return {data:{kind:call.body.series_character_id ? 'linked' : 'unlinked'}}; }
    return reads(call);
  });
  const linkedContainer = new Container();
  await linked.render(linkedContainer, book);
  submit(linkedContainer, form('data-series-character', {series_character_id:'identity-1'}, {seriesCharacter:'mira'}));
  assert.ok(linkedContainer.controls.every(control => control.disabled));
  await settle();
  assert.equal(writes.length, 1);
  assert.equal(writes[0].url, '/api/books/book%2F9/series/characters/mira');
  assert.deepEqual(writes[0].body, {series_character_id:'identity-1'});
  assert.equal(linkedContainer.status.textContent, 'Character identity linked.');
  submit(linkedContainer, form('data-series-character', {series_character_id:''}, {seriesCharacter:'mira'}));
  await settle();
  assert.deepEqual(writes[1].body, {series_character_id:null});

  // Invalid order has a readable local error and does not write; decimal orders work.
  const before = writes.length;
  submit(linkedContainer, form('data-series-membership', {series_id:'series-1', position:''}));
  await settle();
  assert.equal(writes.length, before);
  assert.match(linkedContainer.status.textContent, /Enter a reading order/);
  submit(linkedContainer, form('data-series-membership', {series_id:'series-1', position:'8.5'}));
  await settle();
  assert.deepEqual(writes.at(-1).body, {series_id:'series-1', position:8.5});

  // Creation never guesses volume nine: it uses the explicit input, then adds book.
  const createCalls = [];
  const created = environment(call => {
    if (call.method !== 'GET') { createCalls.push(call); return {data:call.method === 'POST' ? {id:'new-saga'} : {}}; }
    return reads(call, null);
  });
  const createContainer = new Container();
  await created.render(createContainer, book);
  submit(createContainer, form('data-series-create', {name:'A new saga', position:'9'}));
  await settle();
  assert.deepEqual(createCalls.map(call => call.method), ['POST', 'PUT']);
  assert.deepEqual(createCalls[0].body, {name:'A new saga'});
  assert.deepEqual(createCalls[1].body, {series_id:'new-saga', position:9});

  // New identities are created only by their explicit button, then linked.
  const identityCalls = [];
  const identityEnv = environment(call => {
    if (call.method !== 'GET') { identityCalls.push(call); return {data:call.method === 'POST' ? {id:'new-person'} : {}}; }
    return reads(call);
  });
  const identityContainer = new Container();
  await identityEnv.render(identityContainer, book);
  const identityForm = form('data-series-character', {identity_name:'Mira of the north'}, {seriesCharacter:'mira'});
  const button = {dataset:{seriesAction:'create-character'}, closest:() => identityForm};
  identityContainer.listeners.click({target:{closest:() => button}});
  await settle();
  assert.deepEqual(identityCalls.map(call => call.method), ['POST', 'PUT']);
  assert.equal(identityCalls[0].url, '/api/series/series-1/characters');
  assert.deepEqual(identityCalls[0].body, {name:'Mira of the north'});
  assert.deepEqual(identityCalls[1].body, {series_character_id:'new-person'});

  // Prior-volume evidence stays source-labelled and HTML-safe.
  const withContext = environment(call => reads(call, undefined, {included_observations:1, available_observations:3, truncated:true,
    characters:[{name:'Mira', observations:[{book_title:'Earlier <book>', chapter_title:'Gate & Key', position:1,
      description:'A soft <voice>', direction:'Steady', quote:'She said <hello>.'}]}]}));
  const contextContainer = new Container();
  await withContext.render(contextContainer, book);
  assert.ok(contextContainer.innerHTML.includes('Earlier &lt;book&gt; · Gate &amp; Key · order 1'));
  assert.ok(contextContainer.innerHTML.includes('A soft &lt;voice&gt;'));
  assert.ok(contextContainer.innerHTML.includes('She said &lt;hello&gt;.'));
  assert.ok(contextContainer.innerHTML.includes('selection of 3 available observations'));

  // Suggestions are shown, never applied: only an explicit confirmation writes, through the link route.
  {
    const suggestionWrites = [];
    const proposals = {book_id:book.id, series_id:saga.id, suggestions:[
      {character_id:'mira', character_name:'Mira <b>', ambiguous:false, candidates:[{series_character_id:'identity-1', name:'Mira', matched_names:['Mira'],
        sources:[{book_id:'book-1', title:'Earlier <book>', position:1, character_id:'m1', character_name:'Mira'}]}]},
      {character_id:'tomas', character_name:'Tomas', ambiguous:true, candidates:[
        {series_character_id:'identity-2', name:'Tomas', matched_names:['Tomas'], sources:[{book_id:'book-1', title:'One', position:1, character_id:'t1', character_name:'Tomas'}]},
        {series_character_id:'identity-3', name:'Tomas', matched_names:['Tomas'], sources:[{book_id:'book-2', title:'Two', position:2, character_id:'t2', character_name:'Tomas'}]}]}]};
    const env = environment(call => {
      if (call.method === 'PUT') { suggestionWrites.push(call); return {data:{}}; }
      return reads(call, undefined, null, proposals);
    });
    const box = new Container();
    await env.render(box, book);
    assert.equal(suggestionWrites.length, 0, 'showing suggestions links nothing');
    assert.ok(box.innerHTML.includes('Suggested links') && box.innerHTML.includes('2 suggested'));
    assert.ok(box.innerHTML.includes('Earlier &lt;book&gt;') && box.innerHTML.includes('Mira &lt;b&gt;'));
    assert.ok(box.innerHTML.includes('<option value="">Choose one: several identities match</option>'), 'namesakes are never preselected');
    submit(box, form('data-series-suggestion', {series_character_id:''}, {seriesSuggestion:'tomas'}));
    await settle();
    assert.equal(suggestionWrites.length, 0);
    assert.match(box.status.textContent, /Choose which series identity/);
    submit(box, form('data-series-suggestion', {series_character_id:'identity-1'}, {seriesSuggestion:'mira'}));
    await settle();
    assert.equal(suggestionWrites.length, 1);
    assert.equal(suggestionWrites[0].url, '/api/books/book%2F9/series/characters/mira');
    assert.deepEqual(suggestionWrites[0].body, {series_character_id:'identity-1'});
    assert.equal(box.status.textContent, 'Suggested link confirmed.');
  }

  // Failure keeps the panel recoverable, shows server detail, and unlocks controls.
  const failed = environment(call => call.method === 'PUT' ? {ok:false, status:409, data:{detail:'Wait for analysis to finish.'}} : reads(call));
  const failedContainer = new Container();
  await failed.render(failedContainer, book);
  submit(failedContainer, form('data-series-character', {series_character_id:'identity-1'}, {seriesCharacter:'mira'}));
  await settle();
  assert.equal(failedContainer.status.textContent, 'Wait for analysis to finish.');
  assert.ok(failedContainer.controls.every(control => !control.disabled));

  // Pending reads cannot replace the newly selected book or a cleared panel.
  const gates = [];
  const pending = environment(call => new Promise(resolve => gates.push(() => resolve(reads(call)))));
  const staleContainer = new Container();
  const old = pending.render(staleContainer, book);
  await pending.render(staleContainer, null);
  gates.splice(0).forEach(resolve => resolve());
  await old;
  assert.equal(staleContainer.innerHTML, '');

  console.log('Series UI behavior checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
