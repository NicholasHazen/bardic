/* Breeze narration in the browser: per-provider models and voices, capability
   gating of chunked listening, and cast voice selection. No audio is generated. */
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(times = 40) { for (let i = 0; i < times; i++) await tick(); }
class Container {
  constructor() {
    this.innerHTML = ''; this.listeners = {};
    this.drawer = {open:false,summary:{textContent:''},querySelector(selector) { return selector === '#listening-summary' ? this.summary : null; }};
  }
  closest(selector) { return selector === 'details' ? this.drawer : null; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
}
const book = {id:'book-b',revision:1,chapters:[{id:'chapter-1'}],segments:[
  {id:'segment-1',chapter_id:'chapter-1',start:0,end:11,text:'Mara spoke.'},
  {id:'segment-2',chapter_id:'chapter-1',start:12,end:25,text:'Elio replied.'},
],characters:[],scenes:[]};
const capabilities = {
  system:{performance_direction:false,chunked_listening:false,seeded_takes:false,cost:'local'},
  gemini:{performance_direction:true,chunked_listening:true,seeded_takes:false,cost:'cloud'},
  breeze:{performance_direction:true,chunked_listening:false,seeded_takes:true,cost:'self_hosted'},
};
function status(extra = {}) {
  return {has_api_key:true,tts_model:'gemini-3.8-flash-tts',tts_models:['gemini-3.8-flash-tts'],system_voices:[],
    providers:[{id:'system',available:true},{id:'gemini',available:true},{id:'breeze',available:true,reason:null}],
    narration_providers:Object.fromEntries(Object.entries(capabilities).map(([id, value]) => [id,{id,capabilities:value}])),
    breeze:{configured:true,state:'ready',message:'Connected',default_voice_id:'storyteller',voices:[
      {id:'narrator',name:'Narrator',kind:'cloned',usable:true,revision:'r1',seed:null},
      {id:'storyteller',name:'Story <teller>',kind:'cloned',usable:true,revision:'r2',seed:null},
      {id:'sailor',name:'Old Sailor',kind:'designed',usable:false,reason:'Designed voices drift between segments'},
    ]},
    ...extra};
}
const audio = {url:'/api/books/book-b/listen/audio/take',duration:2,available:true,asset_id:'take',mode:'simple'};
function environment(prior) {
  const calls = [], storage = new Map(prior ? [['bardic:listen:book-b',JSON.stringify(prior)]] : []);
  const scope = {window:{},setTimeout:fn => setImmediate(fn),
    localStorage:{getItem:key => storage.get(key) || null,setItem:(key, value) => storage.set(key,value)},
    fetch:async (url, options = {}) => {
      const call = {url,method:options.method || 'GET',body:options.body ? JSON.parse(options.body) : null};
      calls.push(call);
      let data = {};
      if (call.method === 'POST' && url.endsWith('/listen')) data = {session:{id:'session-b'},job:{id:'job-b',status:'queued'}};
      else if (url.startsWith('/api/jobs?')) data = [{id:'job-b',status:'completed',audio}];
      else if (url.includes('/takes?')) data = {session:{id:'session-b'},takes:[]};
      else if (url.endsWith('/listen/chapter/preview')) data = {session:{id:'session-g'},chunks:[],quota:{requests_today:0,rpd:100}};
      else if (url.startsWith('/api/jobs')) data = [];
      return {ok:true,status:200,json:async () => data};
    }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../bardic/static/listen.js'),'utf8'),scope);
  const container = new Container();
  const change = (field, value) => container.listeners.change({target:{dataset:{listenField:field},value}});
  const click = action => container.listeners.click({target:{closest:selector => selector === '[data-listen-action]' ? {dataset:{listenAction:action}} : null}});
  return {api:scope.window.BardicListen,calls,storage,container,change,click};
}
const options = (extra = {}) => ({status:status(),chapterId:'chapter-1',segmentId:'segment-1',...extra});

test('Breeze uses per-passage listening with its default voice and no model', async () => {
  const env = environment();
  let refreshes = 0;
  const hooks = options({onRefreshBreeze:() => refreshes++});
  await env.api.render(env.container,book,hooks);
  env.change('mode','simple');
  env.change('provider','breeze');
  await env.api.render(env.container,book,hooks);
  assert.equal(refreshes,0,'A checked server is not refreshed again');
  assert.deepEqual({...env.api.getSelection(book)},{mode:'simple',provider:'breeze',voice:'storyteller',model:null},
    'The server default voice is chosen visibly; Breeze sends no model');
  assert.match(env.container.innerHTML,/value="storyteller" selected >Story &lt;teller&gt;/);
  assert.match(env.container.innerHTML,/value="sailor"  disabled>Old Sailor · Designed voices drift/,'Designed voices cannot narrate');
  assert.match(env.container.drawer.summary.textContent,/One narrator: Story <teller> \/ Breeze · runs on your Breeze server/);
  assert.match(env.container.innerHTML,/over the local network, so passage text is sent there/);
  assert.doesNotMatch(env.container.innerHTML,/data-listen-field="chunk-preset"/,'Breeze has no Gemini chunk settings');
  assert.doesNotMatch(env.container.innerHTML,/data-listen-field="model"/,'Breeze has no speech model choice');
  const result = await env.api.ensure(book,book.segments[0]);
  assert.equal(result.asset_id,'take');
  const posts = env.calls.filter(call => call.method === 'POST');
  assert.equal(posts.length,1);
  assert.equal(posts[0].url,'/api/books/book-b/listen');
  assert.deepEqual(posts[0].body,{provider:'breeze',voice:'storyteller',model:null,segment_id:'segment-1'});
  assert.equal(env.calls.filter(call => call.url.includes('/listen/chapter')).length,0,'No chapter job for a non-chunked provider');
  const saved = JSON.parse(env.storage.get('bardic:listen:book-b'));
  assert.equal(saved.provider,'breeze');
  assert.equal(saved.voices.breeze,'storyteller');
});

test('saved Breeze selection is restored, and a missing pinned voice stays visible', async () => {
  const env = environment({mode:'simple',provider:'breeze',voices:{system:'',gemini:'Leda',breeze:'retired'},model:'gemini-3.8-flash-tts'});
  await env.api.render(env.container,book,options());
  assert.deepEqual({...env.api.getSelection(book)},{mode:'simple',provider:'breeze',voice:'retired',model:null});
  assert.match(env.container.innerHTML,/value="retired" selected >retired \(not on server\)/);
  env.change('provider','gemini');
  assert.deepEqual({...env.api.getSelection(book)},{mode:'simple',provider:'gemini',voice:'Leda',model:'gemini-3.8-flash-tts'});
  env.change('provider','system');
  assert.equal(env.api.getSelection(book).model,'macos-say');
  env.change('provider','unknown-provider');
  assert.equal(env.api.getSelection(book).provider,'system','Unknown providers fall back to device voices');
});

test('selecting an unchecked Breeze server asks the app to refresh once; rendering never does', async () => {
  const env = environment();
  let refreshes = 0;
  const unchecked = status({breeze:{configured:true,state:'unchecked',message:'Not checked',voices:[]},
    providers:[{id:'system',available:true},{id:'gemini',available:true},{id:'breeze',available:false,reason:'Check the Breeze connection in Settings.'}]});
  const hooks = options({status:unchecked,onRefreshBreeze:() => refreshes++});
  await env.api.render(env.container,book,hooks);
  await env.api.render(env.container,book,hooks);
  assert.equal(refreshes,0);
  env.change('mode','simple');
  env.change('provider','breeze');
  assert.equal(refreshes,1);
  await env.api.render(env.container,book,hooks);
  assert.equal(refreshes,1);
  assert.match(env.container.innerHTML,/Choose a Breeze voice/);
  assert.match(env.container.innerHTML,/Check the Breeze connection in Settings\./,'The unavailable reason is shown');
  assert.match(env.container.innerHTML,/data-listen-action="start" disabled/,'Nothing can be generated without a connection');
  assert.equal(env.calls.filter(call => call.method === 'POST').length,0);
});

test('chunked chapter listening follows the provider capability, not its name', async () => {
  const gated = environment();
  const noChunks = status();
  noChunks.narration_providers.gemini.capabilities = {...capabilities.gemini,chunked_listening:false};
  await gated.api.render(gated.container,book,options({status:noChunks}));
  gated.change('mode','simple');
  gated.change('provider','gemini');
  await gated.api.ensure(book,book.segments[0]);
  const posts = gated.calls.filter(call => call.method === 'POST');
  assert.equal(posts.length,1);
  assert.equal(posts[0].url,'/api/books/book-b/listen','Without the capability Gemini uses single passages');
  assert.equal(posts[0].body.model,'gemini-3.8-flash-tts');

  // Status from an older server without capability metadata keeps Gemini chunked.
  const legacy = environment();
  const oldStatus = status();
  delete oldStatus.narration_providers;
  await legacy.api.render(legacy.container,book,options({status:oldStatus}));
  legacy.change('mode','simple');
  legacy.change('provider','gemini');
  await legacy.api.render(legacy.container,book,options({status:oldStatus}));
  assert.match(legacy.container.innerHTML,/data-listen-field="chunk-preset"/);
});

// App-level cast helpers, executed from the real source.
const appSource = fs.readFileSync(path.join(__dirname,'../bardic/static/app.js'),'utf8');
function between(start, end) {
  const a = appSource.indexOf(start), b = appSource.indexOf(end, a + start.length);
  assert.ok(a >= 0 && b > a, `${start} boundaries`);
  return appSource.slice(a, b);
}
function castHelpers(appStatus = status()) {
  const context = {state:{status:appStatus}};
  vm.runInNewContext([
    appSource.split('\n').find(line => line.startsWith('const escapeHTML =')),
    between('// One cast voice per narration provider.', 'function auditionCharacter('),
    between('// Only cloned voices narrate consistently;', '// Refresh Breeze choices in place'),
    between('// Cast cards edit one voice per provider.', 'async function saveEditor('),
    'globalThis.helpers = {narrationModel, chunkedProvider, characterVoice, breezeVoiceOptions, characterVoicePatch};',
  ].join('\n'), context);
  return context.helpers;
}

test('cast Breeze selector lists usable voices, disables others, keeps a missing pin and escapes names', () => {
  const {breezeVoiceOptions} = castHelpers();
  const empty = breezeVoiceOptions('');
  assert.match(empty,/<option value="" selected>No Breeze voice<\/option>/);
  assert.match(empty,/<option value="narrator"  >Narrator<\/option>/);
  assert.match(empty,/Story &lt;teller&gt;/);
  assert.match(empty,/<option value="sailor"  disabled>Old Sailor · Designed voices drift between segments<\/option>/);
  const chosen = breezeVoiceOptions('narrator');
  assert.match(chosen,/<option value="" >No Breeze voice/);
  assert.match(chosen,/value="narrator" selected/);
  const missing = breezeVoiceOptions('retired');
  assert.match(missing,/<option selected value="retired">retired \(not on server\)<\/option>/);
  assert.equal(castHelpers({}).breezeVoiceOptions(''),'<option value="" selected>No Breeze voice</option>',
    'Before a connection check only the empty choice exists');
});

test('cast voices prefer the per-provider map, fall back to legacy fields and patch only changes', () => {
  const {characterVoice, characterVoicePatch, narrationModel, chunkedProvider} = castHelpers();
  const legacy = {id:'mara',voice:'Puck',system_voice:'Samantha'};
  assert.equal(characterVoice(legacy,'gemini'),'Puck');
  assert.equal(characterVoice(legacy,'system'),'Samantha');
  assert.equal(characterVoice(legacy,'breeze'),'');
  const mapped = {id:'mara',voice:'Old',voices:{gemini:{id:'Leda'},system:{id:''},breeze:{id:'narrator',revision:'r1',seed:42}}};
  assert.equal(characterVoice(mapped,'gemini'),'Leda','The normalized map wins over legacy fields');
  assert.equal(characterVoice(mapped,'system'),'');
  assert.equal(characterVoice(mapped,'breeze'),'narrator');

  const unchanged = characterVoicePatch(mapped,{description:'Kind.',voice:'Leda',system_voice:'',breeze_voice:'narrator',direction:'Soft.'});
  assert.deepEqual({...unchanged},{description:'Kind.',direction:'Soft.'},'Unchanged voices are not re-sent, so Breeze is not re-pinned');
  const changed = characterVoicePatch(mapped,{voice:'Kore',system_voice:'Alex',breeze_voice:''});
  assert.deepEqual(JSON.parse(JSON.stringify(changed)),{voices:{gemini:{id:'Kore'},system:{id:'Alex'},breeze:null}});
  const picked = characterVoicePatch(legacy,{voice:'Puck',system_voice:'Samantha',breeze_voice:'storyteller'});
  assert.deepEqual(JSON.parse(JSON.stringify(picked)),{voices:{breeze:{id:'storyteller'}}});

  assert.equal(narrationModel('gemini'),'gemini-3.8-flash-tts');
  assert.equal(narrationModel('system'),'macos-say');
  assert.equal(narrationModel('breeze'),null);
  assert.equal(chunkedProvider('gemini'),true);
  assert.equal(chunkedProvider('breeze'),false);
  assert.equal(castHelpers({}).chunkedProvider('gemini'),true,'Without capabilities only Gemini is chunked');
});
