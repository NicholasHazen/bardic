const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
class Container {
  constructor() { this.listeners = {}; this.nodes = Object.fromEntries(['content','message','run','refresh','previous','next'].map(key => [key,{innerHTML:'',textContent:'',disabled:false}])); }
  set innerHTML(value) { this.html = value; for (const node of Object.values(this.nodes)) { node.innerHTML = ''; node.textContent = ''; } }
  get innerHTML() { return this.html; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  querySelector(selector) { return this.nodes[selector.match(/^\[data-resource-([\w-]+)\]$/)?.[1] || selector.match(/^\[data-resource-action="([\w-]+)"\]$/)?.[1]] || null; }
}
const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle() { for (let i=0;i<6;i++) await tick(); }
const total = {operations:35, requests:2, cached_operations:1, failed_operations:1,
  estimated_cost_usd:.031, unknown_estimated_cost_usd_operations:1, reserved_cost_usd:.03,
  elapsed_seconds:3.2, unknown_elapsed_seconds_operations:2, cpu_seconds:null, unknown_cpu_seconds_operations:3,
  input_tokens:100, output_tokens:12, cached_input_tokens:null, unknown_cached_input_tokens_operations:2};
function data(offset=0, id='book') { return {book_id:id, totals:total, stages:[{id:'discovery', ...total}], total_operations:35,
  operations:Array.from({length:Math.min(30,35-offset)},(_,i) => ({id:'record-'+(i+offset), stage:'discovery',provider:'<provider>',model:'model & stuff', status:'received',
    input_tokens:100,output_tokens:12,estimated_cost_usd:null,cost_basis:'unknown',unit_key:'unit',run_id:'run',cached: i===0})),
  runs:[{id:'run',kind:'analyze',status:'completed',created_at:'2026-09-27'}], unmeasured_runs:1, notes:['No account balance <is> inferred.']}; }
function env(handler) {
  const calls = []; let now = 10000;
  const scope = {window:{},URLSearchParams,Date:class extends Date { static now(){ return now; } },fetch:async (url, options) => {
    calls.push({url,method:options.method});
    return handler ? handler(url) : {ok:true,json:async () => data(Number(new URL(url,'http://localhost').searchParams.get('offset')))};
  }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../spintails/static/resources.js'),'utf8'),scope);
  return {calls,render:scope.window.SpinTailsResources.render,advance:n=>{now+=n;}};
}
function click(container, action) { const node = {dataset:{resourceAction:action},disabled:false}; container.listeners.click({target:{closest:()=>node}}); }
(async () => {
  const app = env(), container = new Container(), book = {id:'book/9',revision:1};
  await app.render(container,book);
  assert.equal(app.calls.length,1);
  assert.ok(app.calls[0].url.startsWith('/api/books/book%2F9/resources?'));
  assert.equal(app.calls[0].method,'GET');
  let content = container.nodes.content.innerHTML;
  assert.ok(content.includes('1 unmeasured'));
  assert.ok(content.includes('CPU time'));
  assert.ok(content.includes('Current thread only'));
  assert.ok(content.includes('Unknown'));
  assert.ok(content.includes('Saved output reused'));
  assert.ok(content.includes('&lt;provider&gt;') && !content.includes('<provider>'));
  assert.ok(content.includes('No account balance &lt;is&gt; inferred.'));
  assert.ok(content.includes('usage is unknown'));
  assert.ok(container.nodes.previous.disabled && !container.nodes.next.disabled);
  await app.render(container,book,{busy:true});
  assert.equal(app.calls.length,1);
  app.advance(3001);
  await app.render(container,book,{busy:true});
  assert.equal(app.calls.length,2);
  await app.render(container,book,{busy:false});
  assert.equal(app.calls.length,3);
  click(container,'next'); await settle();
  assert.ok(app.calls.at(-1).url.includes('offset=30'));
  assert.ok(container.nodes.content.innerHTML.includes('31–35 of 35'));
  assert.ok(container.nodes.next.disabled);
  container.listeners.change({target:{matches:()=>true,value:'run'}}); await settle();
  assert.ok(app.calls.at(-1).url.includes('run_id=run') && app.calls.at(-1).url.includes('offset=0'));
  assert.ok(app.calls.every(c => c.method === 'GET'));

  let release;
  const stale = env(url => url.includes('old/') ? new Promise(resolve => { release = resolve; }) : Promise.resolve({ok:true,json:async()=>data(0,'new')}));
  const other = new Container();
  const pending = stale.render(other,{id:'old',revision:1});
  await stale.render(other,{id:'new',revision:1});
  const before = other.nodes.content.innerHTML;
  release({ok:true,json:async()=>({...data(),notes:['Stale response leaked']})});
  await pending;
  assert.equal(other.nodes.content.innerHTML,before);
  assert.ok(!other.nodes.content.innerHTML.includes('Stale response leaked'));
  await stale.render(other,null);
  assert.equal(other.innerHTML,'');
  const failure = env(async()=>({ok:false,status:503})), failed = new Container();
  await failure.render(failed,{id:'book'});
  assert.ok(failed.nodes.message.textContent.includes('HTTP 503'));
  assert.ok(!failed.nodes.refresh.disabled);
  console.log('Resource UI read-only, pagination, unknown accounting and isolation checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
