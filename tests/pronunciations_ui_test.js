/* Pronunciation panel: requests, escaping, auditions and saves through a minimal DOM boundary. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class Container {
  constructor() {
    this.innerHTML = '';
    this.listeners = {};
    this.attributes = {};
    this.status = {textContent:'', classes:new Set(), classList:{toggle:(name, on) => on ? this.status.classes.add(name) : this.status.classes.delete(name)}, attributes:{}, setAttribute(name, value) { this.attributes[name] = value; }};
    this.newForm = null;
  }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  querySelector(selector) {
    if (selector === '[data-pronunciation-status]') return this.status;
    if (selector === '[data-pronunciation-new]') return this.newForm;
    return null;
  }
  setAttribute(name, value) { this.attributes[name] = value; }
}

function form(fields, dataset = {}) {
  const elements = Object.fromEntries(Object.entries(fields).map(([key, value]) =>
    [key, typeof value === 'boolean' ? {checked:value} : {value, focus(){}}]));
  return {dataset, elements};
}
function target(data, owner) {
  const node = {dataset:data};
  node.closest = selector => selector === 'form' ? owner : node;
  return node;
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
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../bardic/static/ui.js'), 'utf8'), scope);
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../bardic/static/pronunciations.js'), 'utf8'), scope);
  return {api:scope.window.BardicPronunciations, calls};
}

const entry = {id:'pr_0123456789ab', term:'Cthaelor', respelling:'Kaylor', match_case:true,
  usage:{occurrences:3, passages:2, rendered_passages:1, examples:[]}};
const book = {id:'book/1', revision:4, characters:[{id:'narrator', name:'Narrator'}, {id:'c1', name:'Cthaelor <b>'},
  {id:'c2', name:'Eilidh Voss', aliases:['Lidh']}]};

test('loading lists entries safely and suggests cast names without an entry', async () => {
  const env = environment(() => ({data:{pronunciations:[{...entry, respelling:'<img src=x>'}]}}));
  const container = new Container();
  env.api.render(container, book, {});
  await settle();
  assert.deepEqual(env.calls.map(call => [call.method, call.url]), [['GET', '/api/books/book%2F1/pronunciations']]);
  assert.match(container.innerHTML, /&lt;img src=x&gt;/);
  assert.doesNotMatch(container.innerHTML, /<img/);
  assert.match(container.innerHTML, /3 mentions in 2 passages/);
  for (const name of ['Eilidh', 'Voss', 'Lidh']) assert.match(container.innerHTML, new RegExp(`data-pronunciation-suggest="${name}"`));
  assert.doesNotMatch(container.innerHTML, /data-pronunciation-suggest="Cthaelor"/, 'already has an entry');
  assert.doesNotMatch(container.innerHTML, /data-pronunciation-suggest="Narrator"/);
  // Re-rendering the same book revision does not reload.
  env.api.render(container, book, {});
  await settle();
  assert.equal(env.calls.length, 1);
});

test('adding posts only entry fields, updates the book and reports retired takes', async () => {
  const saved = {id:'book/1', revision:5};
  const env = environment(call => call.method === 'GET' ? {data:{pronunciations:[]}} :
    {data:{pronunciations:[entry], book:saved, retired_takes:2}});
  const container = new Container();
  const books = [];
  env.api.render(container, book, {onBook:value => books.push(value)});
  await settle();
  const add = form({term:' Cthaelor ', respelling:'Kaylor', match_case:true, provider_breeze:'', provider_gemini:'Kay-lor'}, {pronunciationNew:''});
  let prevented = false;
  container.listeners.submit({target:add, preventDefault(){ prevented = true; }});
  await settle();
  assert.ok(prevented);
  assert.deepEqual(env.calls[1], {url:'/api/books/book%2F1/pronunciations', method:'POST',
    body:{term:'Cthaelor', respelling:'Kaylor', match_case:true, providers:{gemini:'Kay-lor'}}});
  assert.deepEqual(books, [saved]);
  assert.match(container.status.textContent, /Added Cthaelor\. 2 recorded takes will be re-recorded/);
});

test('editing patches by id and removing deletes; errors are shown, not thrown', async () => {
  const env = environment(call => {
    if (call.method === 'GET') return {data:{pronunciations:[entry]}};
    if (call.method === 'DELETE') return {ok:false, status:409, data:{detail:'A job is already working on this book.'}};
    return {data:{pronunciations:[entry], retired_takes:0}};
  });
  const container = new Container();
  env.api.render(container, book, {});
  await settle();
  const row = form({term:'Cthaelor', respelling:'Kay-lor', match_case:false}, {pronunciationId:entry.id});
  container.listeners.submit({target:row, preventDefault(){}});
  await settle();
  assert.deepEqual(env.calls[1], {url:`/api/books/book%2F1/pronunciations/${entry.id}`, method:'PATCH',
    body:{term:'Cthaelor', respelling:'Kay-lor', match_case:false}});
  assert.equal(container.status.textContent, 'Saved Cthaelor.');
  container.listeners.click({target:target({pronunciationDelete:''}, row)});
  await settle();
  assert.equal(env.calls[2].method, 'DELETE');
  assert.equal(container.status.textContent, 'A job is already working on this book.');
  assert.equal(container.status.attributes['data-tone'], 'bad');
});

test('hearing sends the unsaved draft to the audition hook and never saves', async () => {
  const env = environment(() => ({data:{pronunciations:[entry]}}));
  const container = new Container();
  const auditions = [];
  env.api.render(container, book, {audition:draft => auditions.push(draft)});
  await settle();
  const row = form({term:'Cthaelor', respelling:'KAY-lor', match_case:true, provider_system:'Kaylor'}, {pronunciationId:entry.id});
  container.listeners.click({target:target({pronunciationHear:''}, row)});
  assert.deepEqual(JSON.parse(JSON.stringify(auditions)), [{term:'Cthaelor', respelling:'KAY-lor', match_case:true, providers:{system:'Kaylor'}, id:entry.id}]);
  assert.match(container.status.textContent, /Capital letters/);
  const empty = form({term:'', respelling:'', match_case:true}, {pronunciationNew:''});
  container.listeners.click({target:target({pronunciationHear:''}, empty)});
  assert.equal(auditions.length, 1);
  assert.match(container.status.textContent, /Type the word/);
  assert.equal(env.calls.length, 1, 'no request beyond the initial list');
});

test('a successful add clears the Add form; a saved row sends empty narrator spellings to clear them', async () => {
  const env = environment(call => call.method === 'GET' ? {data:{pronunciations:[]}} :
    {data:{pronunciations:[entry], book:{id:'book/1', revision:6}, retired_takes:0}});
  const container = new Container();
  env.api.render(container, book, {});
  await settle();
  const add = form({term:'Cthaelor', respelling:'Kaylor', match_case:true}, {pronunciationNew:''});
  container.newForm = add; // the live DOM still holds the typed values when the panel redraws
  container.listeners.submit({target:add, preventDefault(){}});
  await settle();
  assert.match(container.innerHTML, /data-pronunciation-new><div class="pronunciation-fields"><label>Word<input name="term" required maxlength="80" value=""/);
  const row = form({term:'Cthaelor', respelling:'Kaylor', match_case:true, provider_breeze:'', provider_gemini:'', provider_system:''},
    {pronunciationId:entry.id});
  assert.deepEqual(JSON.parse(JSON.stringify(env.api.entryFrom(row))),
    {term:'Cthaelor', respelling:'Kaylor', match_case:true, providers:{}, id:entry.id});
});

test('respelling advice follows the listening trial', () => {
  const {api} = environment(() => ({data:{pronunciations:[]}}));
  assert.equal(api.advice('Kaylor'), '');
  assert.match(api.advice('ay-lee'), /own word/);
  assert.match(api.advice('NY-oh-var'), /initials/);
  assert.equal(api.usageText({occurrences:0}), 'Not found in this book’s text yet.');
});
