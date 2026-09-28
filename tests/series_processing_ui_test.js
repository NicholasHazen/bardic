const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

class Node {
  constructor(){this.innerHTML='';this.textContent='';this.disabled=false;this.listeners={};this.children={};this.attrs={};}
  addEventListener(event,handler){this.listeners[event]=handler;}
  querySelector(selector){return this.children[selector] || null;}
  setAttribute(key,value){this.attrs[key]=value;}
  classList={toggle(){}};
}
class Form extends Node {
  constructor(provider='gemini'){super();this.fields={provider,phase:'scan',concurrency:'2',max_requests:'25',budget_usd:'1'};this.controls=Array.from({length:6},()=>new Node());}
  reportValidity(){return true;}
  querySelectorAll(){return this.controls;}
}
class Container {
  constructor(){this.html='';this.nodes={};}
  set innerHTML(html){
    this.html=html;
    const provider=html.match(/<option selected value="(gemini|openai|anthropic)"/)?.[1] || 'gemini';
    this.nodes={'form':new Form(provider)};
    for(const name of ['start','map','refresh','runs','plan','map-output','processing-message'])this.nodes[`[data-series-${name}]`]=new Node();
    this.nodes['[data-series-start]'].disabled=true;
    this.nodes['[data-series-map-output]'].children.pre=new Node();
  }
  get innerHTML(){return this.html;}
  querySelector(selector){return this.nodes[selector] || null;}
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function settle(){for(let i=0;i<8;i++)await tick();}
const series={id:'saga/one',name:'The <Lantern>',books:[{book_id:'book-1',title:'The first lamp',position:1},{book_id:'book-9',title:'The ninth lamp',position:9}]};
const options={status:{analysis_provider:'openai',analysis_models_by_provider:{openai:'deep-model'},preprocess_models_by_provider:{openai:'fast-model'}}};
const plan={series_id:series.id,plan_fingerprint:'reviewed-fingerprint',requests:3,estimated_cost_usd:.02,model:'deep-model',scan_model:'fast-model',books:[
  {book_id:'book-1',position:1,title:'The first lamp',plan:{requests:1}},
  {book_id:'book-9',position:9,title:'The <ninth> lamp',plan:{requests:2}}],notes:['Future work & retries are unknown.']};
const running={id:'run/1',phase:'scan',status:'running',progress:0,total:2,message:'Discovering characters',children:[
  {book_id:'book-1',phase:'scan',status:'running',message:'Reading a chapter'},
  {book_id:'book-9',phase:'scan',status:'queued',message:'Waiting'}]};
function ordinary(call){
  if(call.url.endsWith('/runs'))return {data:{runs:[]}};
  if(call.url.endsWith('/plan'))return {data:plan};
  if(call.url.endsWith('/process'))return {data:{id:'run/1',message:'Series queued'}};
  if(call.url.endsWith('/map'))return {data:{series:{books:series.books},characters:[{name:'<script>alert(1)</script>'}]}};
  if(call.url.endsWith('/cancel'))return {data:{status:'cancelled'}};
  throw new Error(`Unexpected URL ${call.url}`);
}
function environment(handler=ordinary){
  const calls=[],timers=new Map();let nextTimer=1;
  const scope={window:{},FormData:class {constructor(form){this.entries=Object.entries(form.fields);}[Symbol.iterator](){return this.entries[Symbol.iterator]();}},
    setTimeout(fn,ms){const id=nextTimer++;timers.set(id,{fn,ms});return id;},clearTimeout(id){timers.delete(id);},fetch:async(url,options={})=>{
      const call={url,method:options.method || 'GET',body:options.body ? JSON.parse(options.body):undefined};calls.push(call);
      const result=await handler(call);return {ok:result.ok!==false,status:result.status || 200,json:async()=>{if(result.unreadable)throw new Error('bad JSON');return result.data;}};
    }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../spintails/static/series-processing.js'),'utf8'),scope);
  return {calls,timers,render:scope.window.SpinTailsSeriesProcessing.render};
}
const node=(container,name)=>container.querySelector(`[data-series-${name}]`);
function submit(container){return container.querySelector('form').listeners.submit({preventDefault(){}});}
function click(container,name){return node(container,name).listeners.click({});}
function input(container,name,value){const form=container.querySelector('form');form.fields[name]=value;form.listeners.input({target:{name,value}});}
function cancel(container,id){return node(container,'runs').listeners.click({target:{closest:selector=>selector==='[data-series-cancel]'?{dataset:{seriesCancel:id}}:null}});}

(async()=>{
  let changed=0;
  const env=environment(),container=new Container();
  await env.render(container,series,{...options,onChange:()=>changed++});
  assert.equal(env.calls.length,1);assert.equal(env.calls[0].method,'GET');
  assert.ok(container.innerHTML.includes('The &lt;Lantern&gt;'));
  assert.ok(container.innerHTML.includes('aria-label="Series processing stage"'));
  assert.equal(node(container,'start').disabled,true);
  await click(container,'start');
  assert.ok(!env.calls.some(call=>call.url.endsWith('/process')),'No process without an accepted preview');
  await submit(container);
  assert.equal(env.calls.at(-1).url,'/api/series/saga%2Fone/plan');
  assert.deepEqual(env.calls.at(-1).body,{provider:'openai',phase:'scan',concurrency:2,limits:{max_requests:25,max_input_tokens:1000000,max_output_tokens:100000,budget_usd:1}});
  assert.equal(node(container,'start').disabled,false);
  assert.ok(node(container,'plan').innerHTML.includes('50 maximum requests'));
  assert.ok(node(container,'plan').innerHTML.includes('The &lt;ninth&gt; lamp'));
  assert.ok(node(container,'plan').innerHTML.includes('deep-model'));
  await click(container,'start');
  const dispatched=env.calls.find(call=>call.url.endsWith('/process'));
  assert.equal(dispatched.body.expected_plan_fingerprint,'reviewed-fingerprint');
  assert.equal(changed,1);assert.equal(node(container,'start').disabled,true);
  await click(container,'start');assert.equal(env.calls.filter(call=>call.url.endsWith('/process')).length,1);
  input(container,'budget_usd','');await submit(container);
  assert.equal(env.calls.at(-1).body.limits.budget_usd,null);
  assert.ok(node(container,'plan').innerHTML.includes('No dollar guard.'));
  input(container,'max_requests','4');
  assert.equal(node(container,'start').disabled,true);assert.equal(node(container,'plan').innerHTML,'');

  // Model/membership changes invalidate a reviewed scope without resetting the user's controls.
  await submit(container);assert.equal(node(container,'start').disabled,false);
  await env.render(container,series,{...options,status:{...options.status,analysis_models_by_provider:{openai:'new-model'}}});
  assert.equal(container.querySelector('form').fields.max_requests,'4');
  assert.equal(node(container,'start').disabled,true);
  assert.ok(node(container,'processing-message').textContent.includes('settings changed'));

  // A replacement plan atomically retires the previous one and stale input results never enable Start.
  let delayed;
  let planReads=0;
  const changing=environment(call=>call.url.endsWith('/plan') && ++planReads>1 ? new Promise(resolve=>{delayed=resolve;}) : ordinary(call));
  const changingContainer=new Container();await changing.render(changingContainer,series,options);await submit(changingContainer);
  const replacement=submit(changingContainer);await tick();
  await click(changingContainer,'start');
  assert.equal(changing.calls.filter(call=>call.url.endsWith('/process')).length,0);
  input(changingContainer,'phase','profiles');
  delayed({data:plan});await replacement;
  assert.equal(node(changingContainer,'start').disabled,true);
  assert.equal(node(changingContainer,'plan').innerHTML,'');

  // Series selection protects against old plan results AND old error messages.
  for(const result of [{data:plan},{ok:false,status:400,data:{detail:'Old series error'}}]){
    let resolvePlan;
    const stale=environment(call=>call.url.endsWith('/plan')?new Promise(resolve=>{resolvePlan=resolve;}):ordinary(call));
    const staleContainer=new Container();await stale.render(staleContainer,series,options);
    const waiting=submit(staleContainer);await tick();
    await stale.render(staleContainer,{id:'second',name:'Second series',books:[]},options);
    resolvePlan(result);await waiting;
    assert.equal(node(staleContainer,'start').disabled,true);
    assert.equal(node(staleContainer,'plan').innerHTML,'');
    assert.ok(!node(staleContainer,'processing-message').textContent.includes('Old series'));
  }

  // Starting consumes the reviewed plan before awaiting the server; double clicks cannot submit twice.
  let resolveStart;
  const double=environment(call=>call.url.endsWith('/process')?new Promise(resolve=>{resolveStart=resolve;}):ordinary(call));
  const doubleContainer=new Container();await double.render(doubleContainer,series,options);await submit(doubleContainer);
  const starting=click(doubleContainer,'start');await tick();await click(doubleContainer,'start');
  assert.equal(double.calls.filter(call=>call.url.endsWith('/process')).length,1);
  assert.ok(doubleContainer.querySelector('form').controls.every(control=>control.disabled));
  resolveStart({ok:false,status:409,data:{detail:'The plan scope changed.'}});await starting;
  assert.ok(node(doubleContainer,'processing-message').textContent.includes('scope changed'));
  assert.equal(node(doubleContainer,'start').disabled,true);
  assert.ok(doubleContainer.querySelector('form').controls.every(control=>!control.disabled));

  // A dispatched old series may finish, but its result must not update the newly selected panel.
  let lateStart,oldChanges=0;
  const startRace=environment(call=>call.url.endsWith('/process')?new Promise(resolve=>{lateStart=resolve;}):ordinary(call));
  const startContainer=new Container();await startRace.render(startContainer,series,{...options,onChange:()=>oldChanges++});
  await submit(startContainer);const oldStart=click(startContainer,'start');await tick();
  await startRace.render(startContainer,{id:'new-series',name:'New series'},options);
  lateStart({ok:false,status:409,data:{detail:'Old start failed'}});await oldStart;
  assert.equal(oldChanges,0);
  assert.ok(!node(startContainer,'processing-message').textContent.includes('Old start'));

  // A DOM value changed without its input event still cannot dispatch a mismatched reviewed plan.
  await submit(doubleContainer);doubleContainer.querySelector('form').fields.phase='full';
  await click(doubleContainer,'start');
  assert.equal(double.calls.filter(call=>call.url.endsWith('/process')).length,1);

  // Only a fingerprinted, nonempty scope can start; unreadable/error previews remain recoverable.
  for(const [response,message] of [
    [{data:{...plan,plan_fingerprint:null}},'no scope fingerprint'],
    [{data:{...plan,books:[]}},'Plan ready'],
    [{ok:false,status:400,data:{detail:'No positioned books.'}},'No positioned books'],
    [{unreadable:true},'unreadable series response']
  ]){
    const invalid=environment(call=>call.url.endsWith('/plan')?response:ordinary(call));const invalidContainer=new Container();
    await invalid.render(invalidContainer,series,options);await submit(invalidContainer);await click(invalidContainer,'start');
    assert.equal(node(invalidContainer,'start').disabled,true);
    assert.ok(node(invalidContainer,'processing-message').textContent.includes(message));
    assert.ok(!invalid.calls.some(call=>call.url.endsWith('/process')));
  }

  // Latest run refresh wins, known titles appear before any preview, and polling stops on completion.
  const runGates=[];
  const runs=environment(call=>call.url.endsWith('/runs')?new Promise(resolve=>runGates.push(resolve)):ordinary(call));
  const runsContainer=new Container();const firstRuns=runs.render(runsContainer,series,options);await tick();
  const newerRuns=click(runsContainer,'refresh');await tick();
  runGates[1]({data:{runs:[running]}});await newerRuns;
  assert.ok(node(runsContainer,'runs').innerHTML.includes('The first lamp'));
  assert.ok(!node(runsContainer,'runs').innerHTML.includes('book-1'));
  assert.equal(runs.timers.size,1);
  runGates[0]({data:{runs:[]}});await firstRuns;
  assert.ok(node(runsContainer,'runs').innerHTML.includes('Discovering characters'));
  const timer=[...runs.timers.values()][0];assert.equal(timer.ms,2500);
  const polling=timer.fn();await tick();
  runGates[2]({data:{runs:[{...running,status:'completed',message:'All saved'}]}});await polling;
  assert.equal(runs.timers.size,0);
  assert.ok(node(runsContainer,'runs').innerHTML.includes('Complete'));

  // Cancel only affects the selected run, is coalesced, refreshes status, and never starts processing.
  let resolveCancel,cancelled=false;
  const stopping=environment(call=>{
    if(call.url.endsWith('/cancel'))return new Promise(resolve=>{resolveCancel=value=>{cancelled=true;resolve(value);};});
    if(call.url.endsWith('/runs'))return {data:{runs:[cancelled?{...running,status:'cancelled'}:running]}};
    return ordinary(call);
  });
  const stoppingContainer=new Container();await stopping.render(stoppingContainer,series,options);
  const stop=cancel(stoppingContainer,'run/1');await tick();await cancel(stoppingContainer,'run/1');
  assert.equal(stopping.calls.filter(call=>call.url==='/api/jobs/run%2F1/cancel').length,1);
  assert.ok(node(stoppingContainer,'runs').innerHTML.includes('Stopping…'));
  resolveCancel({data:{status:'cancelled'}});await stop;
  assert.equal(stopping.timers.size,0);
  assert.ok(node(stoppingContainer,'runs').innerHTML.includes('Cancelled'));
  assert.ok(!stopping.calls.some(call=>call.url.endsWith('/process')));

  // A failed cancel is visible and retryable; a temporary polling failure backs off without losing a known run.
  let readCount=0,cancelCount=0;
  const retry=environment(call=>{
    if(call.url.endsWith('/cancel')){cancelCount++;return {ok:false,status:503,data:{detail:'Cancellation could not reach the worker.'}};}
    if(call.url.endsWith('/runs'))return ++readCount===2 ? {ok:false,status:503,data:{detail:'Status temporarily unavailable.'}} : {data:{runs:[running]}};
    return ordinary(call);
  });
  const retryContainer=new Container();await retry.render(retryContainer,series,options);
  await cancel(retryContainer,'run/1');
  assert.ok(node(retryContainer,'processing-message').textContent.includes('Cancellation could not'));
  assert.ok(!node(retryContainer,'runs').innerHTML.includes('Stopping…'));
  await cancel(retryContainer,'run/1');assert.equal(cancelCount,2);
  await click(retryContainer,'refresh');
  assert.ok(node(retryContainer,'processing-message').textContent.includes('Status temporarily'));
  assert.equal([...retry.timers.values()][0].ms,5000);
  assert.ok(node(retryContainer,'runs').innerHTML.includes('Discovering characters'));

  // Map JSON is literal text, and late results cannot replace a newly selected series.
  const maps=environment(),mapsContainer=new Container();await maps.render(mapsContainer,series,options);await click(mapsContainer,'map');
  assert.ok(node(mapsContainer,'map-output').querySelector('pre').textContent.includes('<script>'));
  assert.ok(!node(mapsContainer,'map-output').innerHTML.includes('<script>'));
  let lateMap;
  const mapRace=environment(call=>call.url.endsWith('/map')?new Promise(resolve=>{lateMap=resolve;}):ordinary(call));
  const mapContainer=new Container();await mapRace.render(mapContainer,series,options);const waitingMap=click(mapContainer,'map');await tick();
  await mapRace.render(mapContainer,{id:'other',name:'Other'},options);
  lateMap({ok:false,status:500,data:{detail:'Wrong series failure'}});await waitingMap;
  assert.ok(!node(mapContainer,'processing-message').textContent.includes('Wrong series'));

  // Clearing the panel disposes outstanding reads and polling without creating work.
  await stopping.render(stoppingContainer,null,options);assert.equal(stoppingContainer.innerHTML,'');
  assert.equal(stopping.timers.size,0);
  console.log('Series processing UI checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
