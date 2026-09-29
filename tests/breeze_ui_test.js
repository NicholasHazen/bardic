/* Breeze and library voices in the browser: per-provider models and voices, capability
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
const book = {id:'book-b',revision:1,chapters:[{id:'chapter-1'}],passages:[
  {id:'segment-1',chapter_id:'chapter-1',start:0,end:11,text:'Mara spoke.'},
  {id:'segment-2',chapter_id:'chapter-1',start:12,end:25,text:'Elio replied.'},
],characters:[],scenes:[]};
const capabilities = {
  system:{performance_direction:false,chunked_listening:false,seeded_takes:false,cost:'local'},
  gemini:{performance_direction:true,chunked_listening:true,seeded_takes:false,cost:'cloud'},
  breeze:{performance_direction:true,chunked_listening:false,seeded_takes:true,cost:'self_hosted'},
};
function status(extra = {}) {
  return {tts_model:'gemini-3.8-flash-tts',tts_models:['gemini-3.8-flash-tts'],system_voices:[],
    providers:[{id:'system',available:true},{id:'gemini',available:true},{id:'breeze',available:true,reason:null}],
    narration_providers:Object.fromEntries(Object.entries(capabilities).map(([id, value]) => [id,{id,capabilities:value}])),
    breeze:{configured:true,state:'ready',message:'Connected',default_voice_id:'storyteller',voices:[
      {id:'narrator',name:'Narrator',kind:'cloned',usable:true,voice_revision:'r1',seed:null},
      {id:'storyteller',name:'Story <teller>',kind:'cloned',usable:true,voice_revision:'r2',seed:null},
      {id:'sailor',name:'Old Sailor',kind:'designed',usable:false,reason:'Designed voices drift between segments'},
    ]},
    ...extra};
}
const audio = {url:'/api/books/book-b/listen/audio/take',duration:2,asset_id:'take'};
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
// The narrator menu the sheet shows (BardicListen.choices), as [id, name, usable, reason, selected].
const menu = (api, draft) => { const choice = api.choices(book, draft); return choice.voices.map(voice => [voice.id,voice.name,voice.usable,voice.reason,voice.id === choice.voice]); };
const has = (api, entry, draft) => menu(api, draft).some(item => JSON.stringify(item) === JSON.stringify(entry));

// The voice library snapshot (/api/voices) the listen panel and cast read.
function library(extra = {}) {
  return {
    voices:[
      {id:'vl_narr',provider:'breeze',name:'Narrator',origin:'imported',current_version:1,assignable:true,is_default:false,
        versions:[{version:1,provider_voice_id:'narrator',voice_revision:'r1',made:'imported',server_state:'ok'}],usage:[],warnings:[]},
      {id:'vl_story',provider:'breeze',name:'Story <teller>',origin:'designed',current_version:1,assignable:true,is_default:true,
        versions:[{version:1,provider_voice_id:'storyteller',voice_revision:'r2',made:'designed',server_state:'ok'}],usage:[],warnings:[]},
      {id:'vl_gone',provider:'breeze',name:'Old Sailor',origin:'imported',current_version:1,assignable:false,
        versions:[{version:1,provider_voice_id:'sailor',voice_revision:'r3',made:'imported',server_state:'missing'}],usage:[],warnings:[]},
      {id:'vl_gem',provider:'gemini',name:'Astronomer',origin:'designed',current_version:1,assignable:true,
        versions:[{version:1,provider_voice_id:'voice_abc',made:'designed',server_state:'ok'}],usage:[],warnings:[]},
    ],
    defaults:{breeze:'vl_story'},drafts:[],
    providers:{breeze:{state:'ready',message:'Connected.'},
      gemini:{has_api_key:true,state:'ready',tts_model:'gemini-3.8-flash-tts',designed_voices_supported:true,stored_count:3,limit:200,
        project_voices:[{id:'voice_studio',display_name:'Studio <pick>',type:'prompted',in_library:false,draft_candidate:false},
          {id:'voice_abc',display_name:'Astronomer',type:'prompted',in_library:true,draft_candidate:false},
          {id:'voice_draft',display_name:'Unsaved',type:'prompted',in_library:false,draft_candidate:true}]}},
    builtin:{gemini:['Kore','Puck'],system:[{id:'Samantha',name:'Samantha',locale:'en-US'}]},
    ...extra};
}

test('Breeze narration defaults to the library default voice, lists library voices and sends no model', async () => {
  const env = environment();
  let refreshes = 0;
  const hooks = options({voiceLibrary:library(),onRefreshBreeze:() => refreshes++});
  await env.api.render(env.container,book,hooks);
  env.change('mode','simple');
  env.change('provider','breeze');
  await env.api.render(env.container,book,hooks);
  assert.equal(refreshes,0,'A checked server is not refreshed again');
  assert.deepEqual({...env.api.getSelection(book)},{mode:'simple',provider:'breeze',voice:'',model:null},
    'Default is an empty choice the server resolves; Breeze sends no model');
  assert.ok(has(env.api,['','Default (Story <teller>)',true,'',true]));
  assert.ok(has(env.api,['library:vl_narr','Narrator',true,'',false]));
  assert.ok(has(env.api,['library:vl_gone','Old Sailor',false,'unavailable',false]),'Unassignable voices are listed but disabled');
  assert.ok(!menu(env.api).some(([, name]) => name === 'Astronomer'),'Gemini voices are not offered for Breeze');
  assert.match(env.container.drawer.summary.textContent,/One narrator: Default \(Story <teller>\) \/ Breeze · runs on your Breeze server/);
  assert.doesNotMatch(env.container.innerHTML,/data-listen-field/,'Narrator and expert settings are not in More options');
  const result = await env.api.ensure(book,book.passages[0]);
  assert.equal(result.asset_id,'take');
  const posts = env.calls.filter(call => call.method === 'POST');
  assert.equal(posts.length,1);
  assert.deepEqual(posts[0].body,{provider:'breeze',voice:'',model:null,passage_id:'segment-1'});
  assert.equal(env.calls.filter(call => call.url.includes('/listen/chapter')).length,0,'No chapter job for a non-chunked provider');
  const saved = JSON.parse(env.storage.get('bardic:listen:book-b'));
  assert.equal(saved.provider,'breeze');
  assert.equal(saved.voices.breeze,'');
  env.change('voice','library:vl_narr');
  assert.equal(env.api.getSelection(book).voice,'library:vl_narr','A library voice is sent by reference');
});

test('an earlier Breeze server-voice choice follows its library voice; an unknown one stays visible', async () => {
  const adopted = environment({mode:'simple',provider:'breeze',voices:{system:'',gemini:'Leda',breeze:'storyteller'},model:'gemini-3.8-flash-tts'});
  await adopted.api.render(adopted.container,book,options({voiceLibrary:library()}));
  assert.equal(adopted.api.getSelection(book).voice,'library:vl_story','The saved server voice maps to the library voice whose current version it is');
  assert.equal(JSON.parse(adopted.storage.get('bardic:listen:book-b')).voices.breeze,'library:vl_story');

  const env = environment({mode:'simple',provider:'breeze',voices:{system:'',gemini:'Leda',breeze:'retired'},model:'gemini-3.8-flash-tts'});
  await env.api.render(env.container,book,options({voiceLibrary:library()}));
  assert.deepEqual({...env.api.getSelection(book)},{mode:'simple',provider:'breeze',voice:'retired',model:null});
  assert.ok(has(env.api,['retired','retired (not in library)',true,'',true]));
  env.change('provider','gemini');
  assert.deepEqual({...env.api.getSelection(book)},{mode:'simple',provider:'gemini',voice:'Leda',model:'gemini-3.8-flash-tts'});
  env.change('provider','system');
  assert.equal(env.api.getSelection(book).model,'macos-say');
  env.change('provider','unknown-provider');
  assert.equal(env.api.getSelection(book).provider,'system','Unknown providers fall back to device voices');
});

test('Gemini narrators include library voices, disabled when the speech model cannot use them', async () => {
  const env = environment({mode:'simple',provider:'gemini',voices:{system:'',gemini:'Kore',breeze:''},model:'gemini-3.8-flash-tts'});
  await env.api.render(env.container,book,options({voiceLibrary:library()}));
  assert.ok(has(env.api,['Kore','Kore',true,'',true]));
  assert.ok(has(env.api,['library:vl_gem','Astronomer',true,'',false]));
  const legacy = library();
  legacy.providers.gemini.designed_voices_supported = false;
  await env.api.render(env.container,book,options({voiceLibrary:legacy}));
  assert.ok(has(env.api,['library:vl_gem','Astronomer',false,'needs Gemini 3.8 TTS',false]));
});

test('a new current version or default voice never reuses the earlier listening session', async () => {
  const env = environment({mode:'simple',provider:'breeze',voices:{system:'',gemini:'Kore',breeze:'library:vl_story'},model:'gemini-3.8-flash-tts'});
  const first = library();
  await env.api.render(env.container,book,options({voiceLibrary:first}));
  await env.api.ensure(book,book.passages[0]);
  const saved = JSON.parse(env.storage.get('bardic:listen:book-b'));
  assert.equal(saved.sessionId,'session-b');
  // A fresh book state with the same library reloads that session's saved takes.
  const again = environment(saved);
  await again.api.render(again.container,book,options({voiceLibrary:library()}));
  assert.equal(again.calls.filter(call => call.url.includes('/takes?session_id=session-b')).length,1);
  // The same saved session with a new current version is a different narrator.
  const switched = environment(saved);
  const next = library();
  const story = next.voices.find(voice => voice.id === 'vl_story');
  story.versions.push({version:2,provider_voice_id:'storyteller-v2',voice_revision:'r9',made:'designed',server_state:'ok'});
  story.current_version = 2;
  await switched.api.render(switched.container,book,options({voiceLibrary:next}));
  assert.equal(switched.calls.filter(call => call.url.includes('/takes?')).length,0,'Saved takes of the old version are not loaded as current');
  // Default follows the library default; changing it changes the narrator too.
  const onDefault = environment({...saved,voices:{...saved.voices,breeze:''}});
  const moved = library({defaults:{breeze:'vl_narr'}});
  await onDefault.api.render(onDefault.container,book,options({voiceLibrary:moved}));
  assert.equal(onDefault.calls.filter(call => call.url.includes('/takes?')).length,0);
});

test('a new voice_revision makes a different narrator, for a library version and for a direct server voice', async () => {
  const takesReads = env => env.calls.filter(call => call.url.includes('/takes?')).length;
  const remembered = async (choice, voiceLibrary, serverStatus) => {
    const env = environment({mode:'simple',provider:'breeze',voices:{system:'',gemini:'Leda',breeze:choice},model:null});
    await env.api.render(env.container,book,options({voiceLibrary,status:serverStatus}));
    await env.api.ensure(book,book.passages[0]);
    return JSON.parse(env.storage.get('bardic:listen:book-b'));
  };
  // A library voice whose version keeps its provider voice ID but moves to a new revision.
  const saved = await remembered('library:vl_story',library(),status());
  assert.equal(saved.sessionId,'session-b');
  const same = environment(saved);
  await same.api.render(same.container,book,options({voiceLibrary:library()}));
  assert.equal(takesReads(same),1,'The same revision reads the saved takes back');
  const moved = library();
  moved.voices.find(voice => voice.id === 'vl_story').versions[0].voice_revision = 'r2-retrained';
  const other = environment(saved);
  await other.api.render(other.container,book,options({voiceLibrary:moved}));
  assert.equal(takesReads(other),0,'A new revision of the same library voice never reuses the old session');

  // A server voice outside the library, tracked by the last Breeze check.
  const serverVoices = revision => status({breeze:{configured:true,state:'ready',message:'Connected',default_voice_id:'retired',
    voices:[{id:'retired',name:'Retired',kind:'cloned',usable:true,voice_revision:revision,seed:null}]}});
  const direct = await remembered('retired',library({voices:[],defaults:{breeze:null}}),serverVoices('a1'));
  assert.equal(direct.sessionId,'session-b');
  const unchanged = environment(direct);
  await unchanged.api.render(unchanged.container,book,options({voiceLibrary:library({voices:[],defaults:{breeze:null}}),status:serverVoices('a1')}));
  assert.equal(takesReads(unchanged),1);
  const retrained = environment(direct);
  await retrained.api.render(retrained.container,book,options({voiceLibrary:library({voices:[],defaults:{breeze:null}}),status:serverVoices('a2')}));
  assert.equal(takesReads(retrained),0,'A server voice with a new voice_revision is a different narrator');
});

test('selecting an unchecked Breeze server asks the app to refresh once; rendering never does', async () => {
  const env = environment();
  let refreshes = 0;
  const unchecked = status({breeze:{configured:true,state:'unchecked',message:'Not checked',voices:[]},
    providers:[{id:'system',available:true},{id:'gemini',available:true},{id:'breeze',available:false,reason:'Check the Breeze connection in Settings.'}]});
  const hooks = options({status:unchecked,voiceLibrary:library({voices:[],defaults:{breeze:null}}),onRefreshBreeze:() => refreshes++});
  await env.api.render(env.container,book,hooks);
  await env.api.render(env.container,book,hooks);
  assert.equal(refreshes,0);
  env.change('mode','simple');
  env.change('provider','breeze');
  assert.equal(refreshes,1);
  await env.api.render(env.container,book,hooks);
  assert.equal(refreshes,1);
  assert.ok(menu(env.api).some(([, name]) => name === 'Default (none set yet)'));
  assert.match(env.container.innerHTML,/Check the Breeze connection in Settings\./,'The unavailable reason is shown');
  assert.match(env.container.innerHTML,/data-listen-action="start" disabled/,'Nothing can be generated without a connection');
  assert.equal(env.api.choices(book).breezeVoiceReady,false,'No example without a Breeze voice (the sheet disables Hear example)');
  assert.equal(env.api.choices(book).unavailableReason,'Check the Breeze connection in Settings.');
  assert.equal(env.calls.filter(call => call.method === 'POST').length,0);
});

test('chunked chapter listening follows the provider capability, not its name', async () => {
  const gated = environment();
  const noChunks = status();
  noChunks.narration_providers.gemini.capabilities = {...capabilities.gemini,chunked_listening:false};
  await gated.api.render(gated.container,book,options({status:noChunks}));
  gated.change('mode','simple');
  gated.change('provider','gemini');
  await gated.api.ensure(book,book.passages[0]);
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
  assert.equal(legacy.api.choices(book).chunked,true);
  assert.match(legacy.container.innerHTML,/Prepare rest of chapter · paid/,'Chunked Gemini preparation is offered');
});

// App-level cast helpers, executed from the real sources.
const appSource = fs.readFileSync(path.join(__dirname,'../bardic/static/app.js'),'utf8');
const voicesSource = fs.readFileSync(path.join(__dirname,'../bardic/static/voices.js'),'utf8');
const uiSource = fs.readFileSync(path.join(__dirname,'../bardic/static/ui.js'),'utf8');
function between(start, end) {
  const a = appSource.indexOf(start), b = appSource.indexOf(end, a + start.length);
  assert.ok(a >= 0 && b > a, `${start} boundaries`);
  return appSource.slice(a, b);
}
function castHelpers(appStatus = status(), voiceLibrary = library()) {
  const toasts = [];
  const context = {state:{status:appStatus,voiceLibrary},window:{},toast:message => toasts.push(message)};
  vm.createContext(context);
  vm.runInContext(uiSource, context);
  vm.runInContext(voicesSource, context);
  vm.runInNewContext([
    appSource.split('\n').find(line => line.startsWith('const escapeHTML =')),
    between('// One cast voice per narration provider.', 'function auditionCharacter('),
    between('// A cast card edits the voice of the provider it shows.', 'async function saveEditor('),
    'globalThis.helpers = {narrationModel, chunkedProvider, characterVoice, voiceRequest, characterVoicePatch, cast:window.BardicVoices.cast};',
  ].join('\n'), context);
  return {...context.helpers, toasts};
}

test('cast voice options: Default names the default voice, library and direct voices, and Create new', () => {
  const {cast} = castHelpers();
  const breeze = cast.castOptions('breeze','',library(),status());
  assert.match(breeze,/^<option value="" selected >Default \(Story &lt;teller&gt;\)<\/option>/);
  assert.match(breeze,/<optgroup label="Your voices"><option value="library:vl_narr"  >Narrator<\/option>/);
  assert.match(breeze,/<option value="library:vl_gone"  disabled>Old Sailor · unavailable<\/option>/);
  assert.match(breeze,/<option value="__create__">Create new voice…<\/option>$/);
  assert.doesNotMatch(breeze,/Astronomer/);
  const deleted = cast.castOptions('breeze','library:vl_removed',library(),status());
  assert.match(deleted,/<option value="library:vl_removed" selected >Deleted voice<\/option>/,'A deleted assignment stays visible and selected');
  const pinned = cast.castOptions('breeze','id:narrator',library(),status());
  assert.match(pinned,/narrator \(server voice outside the library\)/);
  assert.match(cast.castOptions('breeze','',library({defaults:{breeze:null}}),status()),/Default \(none set yet\)/);

  const gemini = cast.castOptions('gemini','id:Puck',library(),status());
  assert.match(gemini,/<option value=""  >Default \(Kore\)<\/option>/);
  assert.match(gemini,/<optgroup label="Your voices"><option value="library:vl_gem"  >Astronomer<\/option><\/optgroup>/);
  assert.match(gemini,/<optgroup label="Project voices"><option value="id:voice_studio"  >Studio &lt;pick&gt;<\/option><\/optgroup>/,
    'Project voices already in the library or belonging to an open draft are not offered twice');
  assert.match(gemini,/<option value="id:Puck" selected >Puck<\/option>/);
  assert.match(gemini,/Create new voice…/);
  const old = library();
  old.providers.gemini.designed_voices_supported = false;
  assert.match(cast.castOptions('gemini','',old,status()),/value="library:vl_gem"  disabled>Astronomer · needs Gemini 3.8 TTS/);

  const device = cast.castOptions('system','',library(),status());
  assert.match(device,/<option value="" selected >Default Mac voice<\/option><optgroup label="Mac voices"><option value="id:Samantha"  >Samantha · en-US<\/option>/);
  assert.doesNotMatch(device,/Create new voice/,'Mac voices cannot be created');
});

test('cast choices encode the per-provider map and decode to assignments', () => {
  const {cast, characterVoice} = castHelpers();
  const unset = {id:'mara',voices:{}};
  assert.equal(characterVoice(unset,'breeze'),'','No Breeze choice means Default');
  const mapped = {id:'mara',voices:{gemini:{id:'Leda'},breeze:{library:'vl_narr',id:'narrator',voice_revision:'r1'},system:{id:''}}};
  assert.equal(characterVoice(mapped,'gemini'),'id:Leda');
  assert.equal(characterVoice(mapped,'breeze'),'library:vl_narr','A library reference wins over its resolved concrete voice');
  assert.equal(characterVoice(mapped,'system'),'');
  assert.equal(JSON.stringify(cast.decodeChoice('library:vl_narr')),JSON.stringify({library:'vl_narr'}));
  assert.equal(JSON.stringify(cast.decodeChoice('id:Kore')),JSON.stringify({id:'Kore'}));
  assert.equal(cast.decodeChoice(''),null,'Default is sent as null');
  assert.equal(cast.decodeChoice(cast.CREATE),null);
  assert.equal(cast.requestVoice('gemini',''),'Kore');
  assert.equal(cast.requestVoice('breeze',''),'');
  assert.equal(cast.requestVoice('breeze','library:vl_narr'),'library:vl_narr');
  assert.equal(cast.requestVoice('system','id:Samantha'),'Samantha');
});

test('cast saves send only the shown provider, and only when its choice changed', () => {
  const {characterVoicePatch, narrationModel, chunkedProvider} = castHelpers();
  const character = {id:'mara',voices:{gemini:{id:'Leda'},breeze:{library:'vl_narr'}}};
  const unchanged = characterVoicePatch(character,{description:'Kind.',voice_choice:'library:vl_narr',direction:'Soft.'},'breeze');
  assert.deepEqual(JSON.parse(JSON.stringify(unchanged)),{description:'Kind.',direction:'Soft.'},'An unchanged voice is not re-sent');
  const changed = characterVoicePatch(character,{voice_choice:'library:vl_story'},'breeze');
  assert.deepEqual(JSON.parse(JSON.stringify(changed)),{voices:{breeze:{library:'vl_story'}}});
  const toDefault = characterVoicePatch(character,{voice_choice:''},'breeze');
  assert.deepEqual(JSON.parse(JSON.stringify(toDefault)),{voices:{breeze:null}},'Default clears the provider choice');
  const gemini = characterVoicePatch(character,{voice_choice:'id:Kore'},'gemini');
  assert.deepEqual(JSON.parse(JSON.stringify(gemini)),{voices:{gemini:{id:'Kore'}}});
  const create = characterVoicePatch(character,{voice_choice:'__create__'},'breeze');
  assert.deepEqual(JSON.parse(JSON.stringify(create)),{},'Create new voice is an action, never an assignment');

  assert.equal(narrationModel('gemini'),'gemini-3.8-flash-tts');
  assert.equal(narrationModel('system'),'macos-say');
  assert.equal(narrationModel('breeze'),null);
  assert.equal(chunkedProvider('gemini'),true);
  assert.equal(chunkedProvider('breeze'),false);
  assert.equal(castHelpers({}).chunkedProvider('gemini'),true,'Without capabilities only Gemini is chunked');
});

test('cast warnings explain deleted, changed and unsupported voices and a missing default', () => {
  const {cast, voiceRequest, toasts} = castHelpers(status(), library({defaults:{breeze:null}}));
  assert.deepEqual([...cast.castWarnings('breeze','',library({defaults:{breeze:null}}),status())],
    ['No default Breeze voice yet. Check the Breeze connection in Settings, or choose a default in Voices.']);
  assert.deepEqual([...cast.castWarnings('breeze','library:vl_removed',library(),status())],['This voice was deleted. Choose another voice.']);
  assert.deepEqual([...cast.castWarnings('breeze','library:vl_gone',library(),status())],['Missing from the server, so it cannot record new audio.']);
  const changed = library();
  changed.voices[1].versions[0].server_state = 'changed';
  assert.deepEqual([...cast.castWarnings('breeze','',changed,status())],['Default voice: Changed on the Breeze server since this version was saved.']);
  const old = library();
  old.providers.gemini.designed_voices_supported = false;
  old.providers.gemini.tts_model = 'gemini-3.1-flash-tts-preview';
  assert.match(cast.castWarnings('gemini','library:vl_gem',old,status())[0],/need Gemini 3\.8 TTS; the current speech model is gemini-3\.1-flash-tts-preview/);
  assert.deepEqual([...cast.castWarnings('gemini','id:Kore',old,status())],[],'Built-in voices always work');
  assert.equal(voiceRequest({name:'Mara'},'breeze',''),null,'Breeze Default without a default voice requests nothing');
  assert.match(toasts.at(-1),/no default Breeze voice yet/);
  assert.equal(voiceRequest({name:'Mara'},'breeze','library:vl_narr'),'library:vl_narr');
  assert.deepEqual([...cast.identity('breeze','',library())],['vl_story',1,'storyteller','r2'],'Default resolves to the default voice identity');
  assert.equal(cast.identity('gemini','id:Kore',library()),null);
});
