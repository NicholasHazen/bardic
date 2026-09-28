const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Container {
  constructor() { this.innerHTML = ''; this.listeners = {}; this.message = {textContent:'', setAttribute(){}}; this.controls = [{disabled:false}]; }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  setAttribute() {}
  querySelector(selector) { return selector === '[data-library-message]' ? this.message : null; }
  querySelectorAll() { return this.controls; }
}
const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle() { for (let i=0; i<8; i++) await tick(); }
function click(container, action, id, position) {
  const button = {dataset:{libraryAction:action,libraryId:id,libraryPosition:position}};
  container.listeners.click({target:{closest:selector => selector === '[data-library-action]' ? button : null}});
}
function submit(container, kind, id, values) {
  const form = {dataset:{libraryForm:kind,libraryId:id},elements:Object.fromEntries(Object.entries(values).map(([key,value]) => [key,{value}]))};
  let prevented = false;
  container.listeners.submit({target:{closest:selector => selector === '[data-library-form]' ? form : null}, preventDefault(){ prevented=true; }});
  assert.ok(prevented);
}

(async () => {
  const calls = [];
  const sample = {books:[{id:'book/1',title:'The <Lantern>',author:'A & B',word_count:4,text_character_count:20,chapter_count:1,section_count:2,passage_count:3,
    cover:{sha256:'cover'},storage:{original_bytes:100,audio_bytes:0,simple_listen_bytes:20,database_payload_bytes:800},archived:false}],
    series:[{id:'series/1',name:'The <Saga>',archived:false,character_count:0,volumes:[{position:1,title:'First',book_id:null,status:'missing'}]}],
    storage:{data_directory_bytes:4000,shared_database_bytes:3000,note:'Shared database <note>'}};
  let respond = (url, options) => {
    if (options.method === 'POST' && url.endsWith('/archive')) sample.books[0].archived = true;
    if (options.method === 'POST' && url.endsWith('/restore')) sample.books[0].archived = false;
    return {ok:true,status:200,json:async () => url.startsWith('/api/library') ? sample : {id:'new'}};
  };
  const context = {window:{},console,fetch:async (url,options) => { calls.push({url,...options}); return respond(url,options); }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../bardic/static/library.js'),'utf8'),context);
  const component = context.window.BardicLibrary;
  const container = new Container();
  let changed = 0, openedBook, openedSeries;
  const options = {onChange:() => changed++, onSelectBook:id => openedBook=id,onSelectSeries:id=>openedSeries=id};
  await component.render(container, options);
  assert.equal(calls.length,1);
  assert.equal(calls[0].method,'GET');
  assert.ok(container.innerHTML.includes('The &lt;Lantern&gt;'));
  assert.ok(container.innerHTML.includes('A &amp; B'));
  assert.ok(container.innerHTML.includes('/api/books/book%2F1/cover?v=cover'));
  assert.ok(!container.innerHTML.includes('<Lantern>'));
  assert.ok(container.innerHTML.includes('Create a series'));
  assert.ok(container.innerHTML.includes('Shared database &lt;note&gt;'));
  assert.ok(container.innerHTML.includes('Its source, audio, analysis and series links stay saved'));
  click(container,'open-book','book/1');
  click(container,'open-series','series/1');
  assert.equal(openedBook,'book/1'); assert.equal(openedSeries,'series/1');

  submit(container,'metadata','book/1',{title:'Revised',author:'Author'});
  await settle();
  let mutation = calls.find(call => call.method === 'PATCH');
  assert.equal(mutation.url,'/api/books/book%2F1/metadata');
  assert.deepEqual(JSON.parse(mutation.body),{title:'Revised',author:'Author'});
  assert.equal(changed,1);
  submit(container,'volume','series/1',{position:'1.5',title:'Novella',status:'planned'});
  await settle();
  mutation = calls.find(call => call.url.endsWith('/volumes'));
  assert.deepEqual(JSON.parse(mutation.body),{position:1.5,title:'Novella',status:'planned'});
  const before = calls.length;
  submit(container,'volume','series/1',{position:'',title:'Invalid',status:'missing'});
  await settle();
  assert.equal(calls.length,before);
  assert.match(container.message.textContent,/reading order/);

  click(container,'archive-book','book/1');
  await settle();
  assert.equal(sample.books[0].archived,true);
  assert.ok(!container.innerHTML.includes('The &lt;Lantern&gt;'));
  click(container,'removed');
  assert.ok(container.innerHTML.includes('Restore book'));
  assert.ok(container.innerHTML.includes('No disk space is reclaimed'));
  click(container,'restore-book','book/1');
  await settle();
  assert.equal(sample.books[0].archived,false);
  const beforeBusy = calls.length;
  await component.render(container,{...options,busy:true});
  assert.equal(container.controls[0].disabled,true);
  click(container,'archive-book','book/1');
  await settle();
  assert.equal(calls.length,beforeBusy);
  await component.render(container,options);

  // A late refresh cannot replace more recently loaded library state.
  let release;
  respond = () => new Promise(resolve => { release=resolve; });
  const old = component.refresh(container);
  await tick();
  respond = () => ({ok:true,status:200,json:async () => ({books:[],series:[],storage:{}})});
  await component.refresh(container);
  release({ok:true,status:200,json:async () => sample});
  await old;
  assert.ok(!container.innerHTML.includes('The &lt;Lantern&gt;'));
  click(container,'active');
  assert.ok(container.innerHTML.includes('Create a series'));
  assert.ok(container.innerHTML.includes('Import your first ebook'));
  console.log('Library UI behavior checks passed');
})().catch(error => { console.error(error); process.exitCode=1; });
