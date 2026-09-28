/* The Voices tab: drafts, candidates, billed-create confirmation, versions and
   destructive confirmations. Offline: fetch is a local fake; no audio plays. */
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(times = 20) { for (let i = 0; i < times; i++) await tick(); }
class Container {
  constructor() { this.innerHTML = ''; this.handlers = {}; }
  // Every handler for an event runs, as in the DOM (the BardicUI choice binds its own).
  addEventListener(name, handler) { (this.handlers[name] ||= []).push(handler); }
  get listeners() { return Object.fromEntries(Object.entries(this.handlers).map(([name, list]) => [name, event => list.forEach(handler => handler(event))])); }
  contains() { return false; }
  querySelector() { return null; }
}
const voice = (id, extra = {}) => ({id, provider:'breeze', name:id, description:'', origin:'designed', current_version:1, is_default:false,
  assignable:true, versions:[{version:1, provider_voice_id:id, made:'designed', created_at:'2026-09-27T00:00:00Z', audition:{url:`/api/voices/${id}/versions/1/audition`}, server_state:'ok'}],
  usage:[], source:null, warnings:[], ...extra});
function library(extra = {}) {
  return {voices:[
      voice('vl_narr', {name:'Narrator', origin:'imported', is_default:true, usage:[{book_id:'book-b', book_title:'Lantern', character_id:'narrator', character_name:'Narrator', follows:'default'}]}),
      voice('vl_mara', {name:'Mara <alto>', description:'A <warm> alto.', usage:[{book_id:'book-b', book_title:'Lantern & Co', character_id:'mara', character_name:'Mara', follows:'assigned'}]}),
      voice('vl_imp', {name:'Storyteller', origin:'imported'}),
    ],
    defaults:{breeze:'vl_narr'}, drafts:[],
    providers:{breeze:{state:'ready', message:'Connected.'}, gemini:{has_api_key:true, state:'ready', tts_model:'gemini-3.8-flash-tts', designed_voices_supported:true, stored_count:3, limit:200, project_voices:[]}},
    builtin:{gemini:['Kore'], system:[]}, ...extra};
}
const geminiDraft = (extra = {}) => ({id:'vd_gem', provider:'gemini', base_voice_id:null, base_voice_name:null,
  context:{book_id:'book-b', character_id:'mara', character_name:'Mara <Quinn>'}, name:'Mara', description:'A <warm> alto.',
  sample_text:'', status:'open', busy:false, candidates:[], ...extra});
const breezeDraft = (extra = {}) => ({id:'vd_brz', provider:'breeze', base_voice_id:'vl_mara', base_voice_name:'Mara <alto>',
  context:{book_id:'book-b', character_id:'mara', character_name:'Mara'}, name:'Mara', description:'Warmer.', sample_text:'Stay, she said.',
  status:'open', busy:false, candidates:[], ...extra});

function environment(responses = {}) {
  const calls = [];
  const scope = {window:{}, Audio:class { play() { return Promise.resolve(); } pause() {} },
    fetch:async (url, options = {}) => {
      const call = {url, method:options.method || 'GET', body:options.body && typeof options.body === 'string' ? JSON.parse(options.body) : null};
      calls.push(call);
      const key = `${call.method} ${url.split('?')[0]}`;
      const data = typeof responses[key] === 'function' ? responses[key](call) : responses[key] ?? {};
      if (data.status_code) return {ok:false, status:data.status_code, json:async () => ({detail:data.detail, code:data.code})};
      return {ok:true, status:200, json:async () => data};
    }};
  vm.createContext(scope);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../bardic/static/ui.js'), 'utf8'), scope);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../bardic/static/voices.js'), 'utf8'), scope);
  const container = new Container();
  const hooks = {changes:0, saved:[]};
  const options = extra => ({library:library(), book:{id:'book-b'}, onChange:() => { hooks.changes++; }, onSaved:(result, info) => { hooks.saved.push({result, info}); }, ...extra});
  const click = (action, dataset = {}) => container.listeners.click({preventDefault() {},
    target:{closest:selector => selector === '[data-voices-action]' ? {dataset:{voicesAction:action, ...dataset}, disabled:false} : null}});
  const change = (field, value, checked) => container.listeners.change({target:{dataset:{voicesField:field}, value, checked}});
  return {api:scope.window.BardicVoices, calls, container, hooks, options, click, change};
}

test('a Gemini create needs a fresh cost confirmation for every click', async () => {
  const created = geminiDraft({candidates:[{id:'c1', kind:'gemini_voice', provider_voice_id:'voice_x', seed:null, description:'<b>bold</b>', sample_text:'',
    audio:{url:'/api/voices/drafts/vd_gem/candidates/c1/audio'}, duration:3.2, created_at:'2026-09-27T00:00:00Z', expires_at:null, discarded:false}]});
  const env = environment({'PATCH /api/voices/drafts/vd_gem':geminiDraft(), 'POST /api/voices/drafts/vd_gem/generate':created});
  env.api.render(env.container, env.options({library:library({drafts:[geminiDraft()]})}));
  env.api.openDraft(env.container, geminiDraft());
  const html = () => env.container.innerHTML;
  assert.match(html(), /For Mara &lt;Quinn&gt;/);
  assert.match(html(), /A &lt;warm&gt; alto\./);
  assert.doesNotMatch(html(), /<warm>/, 'User and server text is escaped');
  assert.match(html(), /billed, stored voice \(3 of 200 used\)/);
  assert.match(html(), /data-voices-action="generate" disabled>Create voice/);
  env.click('generate');
  await settle();
  assert.equal(env.calls.length, 0, 'Nothing is sent without confirming the cost');

  env.change('confirm-cost', 'on', true);
  assert.match(html(), /data-voices-action="generate" >Create voice/);
  env.click('generate');
  await settle();
  const posts = env.calls.filter(call => call.url.endsWith('/generate'));
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0].body, {book_id:'book-b', language_code:'en-US', gender:null, confirm_cost:true});
  assert.match(html(), /data-voices-action="generate" disabled>Create voice/, 'The confirmation resets after each create');
  env.click('generate');
  await settle();
  assert.equal(env.calls.filter(call => call.url.endsWith('/generate')).length, 1, 'A second create needs a second confirmation');

  assert.match(html(), /&lt;b&gt;bold&lt;\/b&gt;/, 'Candidate descriptions are escaped');
  assert.match(html(), /data-voices-action="save-assign" >Save &amp; assign to Mara &lt;Quinn&gt;/);
  env.click('discard', {id:'c1'});
  assert.match(html(), /Delete this stored voice from your Google project\?/, 'Discarding a stored Gemini voice is confirmed in the page');
  env.click('cancel-confirm');
  assert.doesNotMatch(html(), /Delete this stored voice/);
  env.click('abandon');
  assert.match(html(), /This deletes 1 stored Gemini voice from your project\./);
});

test('Breeze previews use the chosen count and save as a new version assigned to the character', async () => {
  const withCandidate = breezeDraft({candidates:[{id:'c1', kind:'breeze_preview', preview_id:'prv_1', seed:7, provider_voice_id:null, description:'Warmer.',
    sample_text:'Stay, she said.', audio:{url:'/api/voices/drafts/vd_brz/candidates/c1/audio'}, duration:2.5, created_at:'2026-09-27T00:00:00Z', expires_at:'2026-09-28T00:00:00Z', discarded:false}]});
  const saved = {voice:voice('vl_mara', {name:'Mara <alto>', current_version:2}), book:{id:'book-b'}};
  const env = environment({'POST /api/voices/drafts/vd_brz/generate':withCandidate, 'POST /api/voices/drafts/vd_brz/save':saved});
  env.api.render(env.container, env.options({library:library({drafts:[breezeDraft()]})}));
  env.api.openDraft(env.container, breezeDraft());
  assert.match(env.container.innerHTML, /New version of Mara &lt;alto&gt;/);
  assert.doesNotMatch(env.container.innerHTML, /confirm-cost/, 'Breeze previews are free and need no cost confirmation');
  env.change('count', '3');
  env.click('generate');
  await settle();
  const generate = env.calls.find(call => call.url.endsWith('/generate'));
  assert.deepEqual(generate.body, {book_id:'book-b', count:3});
  assert.match(env.container.innerHTML, /Sample c1 · seed 7/);
  assert.match(env.container.innerHTML, /Version 2 of “Mara &lt;alto&gt;” \(becomes current\)/);
  assert.match(env.container.innerHTML, /data-voices-action="save" >Save version 2/);
  env.click('save-assign');
  await settle();
  const save = env.calls.find(call => call.url.endsWith('/save'));
  assert.deepEqual(save.body, {candidate_id:'c1', name:'Mara', mode:'version', assign:{book_id:'book-b', character_id:'mara'}});
  assert.equal(env.hooks.saved.length, 1);
  assert.equal(env.hooks.saved[0].info.assigned, true);
  assert.equal(env.hooks.saved[0].info.provider, 'breeze');
  assert.ok(env.hooks.changes >= 1, 'The library is reloaded after saving');
});

test('deleting asks in the page, lists usage, and keeps imported server voices unless chosen', async () => {
  const env = environment({'DELETE /api/voices/vl_imp':{deleted:'vl_imp'}, 'DELETE /api/voices/vl_mara':{deleted:'vl_mara'}});
  env.api.render(env.container, env.options());
  const html = () => env.container.innerHTML;
  assert.match(html(), /Mara &lt;alto&gt;/);
  assert.match(html(), /Used by Mara <span class="voice-muted">\(Lantern &amp; Co\)<\/span>/);
  assert.match(html(), /data-voices-action="delete" data-id="vl_narr" disabled title="Choose another default voice first"/, 'The default voice cannot be deleted');
  env.click('delete', {id:'vl_imp'});
  assert.match(html(), /Delete “Storyteller”\?/);
  assert.match(html(), /<input type="checkbox" data-voices-field="delete-server" > Also delete it on the Breeze server/, 'Imported voices stay on the server by default');
  env.click('confirm');
  await settle();
  assert.equal(env.calls.at(-1).url, '/api/voices/vl_imp?server=false');
  assert.equal(env.calls.at(-1).method, 'DELETE');
  env.click('delete', {id:'vl_mara'});
  assert.match(html(), /Those characters need another voice/);
  assert.match(html(), /data-voices-field="delete-server" checked>/, 'Voices Bardic made are deleted on the server by default');
  env.change('delete-server', 'on', false);
  env.click('confirm');
  await settle();
  assert.equal(env.calls.at(-1).url, '/api/voices/vl_mara?server=false');
});

test('unfinished drafts can be resumed or abandoned from the list, and version recipes are shown escaped', async () => {
  const stored = geminiDraft({id:'vd_old', name:'Old <try>', candidates:[{id:'c1', kind:'gemini_voice', provider_voice_id:'voice_old', discarded:false}]});
  const env = environment({'POST /api/voices/drafts/vd_old/abandon':{...stored, status:'abandoned'}});
  const withRecipe = library();
  withRecipe.voices[1].versions[0].recipe = {description:'A <warm> alto, 30s.', sample_text:'Hi.'};
  env.api.render(env.container, env.options({library:{...withRecipe, drafts:[stored, breezeDraft()]}}));
  env.api.openDraft(env.container, breezeDraft());
  const html = () => env.container.innerHTML;
  assert.match(html(), /<strong>Old &lt;try&gt;<\/strong>.*1 stored in your Google project/);
  assert.match(html(), /“A &lt;warm&gt; alto, 30s\.”/);
  env.click('abandon', {id:'vd_old'});
  assert.match(html(), /This deletes 1 stored Gemini voice from your project\./);
  env.click('confirm');
  await settle();
  assert.equal(env.calls.at(-1).url, '/api/voices/drafts/vd_old/abandon');
  assert.match(html(), /New version of Mara &lt;alto&gt;/, 'Abandoning another draft keeps the open designer');
  assert.doesNotMatch(html(), /Old &lt;try&gt;/);
});

test('switching versions and the default voice are confirmed and explain re-voicing', async () => {
  const two = voice('vl_mara', {name:'Mara', current_version:2, versions:[
    {version:1, provider_voice_id:'mara', made:'designed', server_state:'ok', audition:{url:'/a1'}},
    {version:2, provider_voice_id:'mara-v2', made:'designed', server_state:'changed', audition:{url:'/a2'}}]});
  const env = environment({'POST /api/voices/vl_mara/current':two, 'POST /api/voices/defaults':{defaults:{breeze:'vl_mara'}}});
  env.api.render(env.container, env.options({library:library({voices:[voice('vl_narr', {name:'Narrator', is_default:true,
    usage:[{book_id:'b', book_title:'B', character_id:'n', character_name:'Narrator', follows:'default'}]}), two]})}));
  assert.match(env.container.innerHTML, /Changed on the Breeze server since this version was saved\./);
  env.click('make-current', {id:'vl_mara', version:'1'});
  assert.match(env.container.innerHTML, /switch to version 1\. Their recordings become out of date/);
  env.click('confirm');
  await settle();
  assert.deepEqual(env.calls.at(-1).body, {version:1});
  env.click('set-default', {id:'vl_mara'});
  assert.match(env.container.innerHTML, /1 character left on Default switch to it/);
  env.click('confirm');
  await settle();
  assert.deepEqual(env.calls.at(-1).body, {provider:'breeze', voice_id:'vl_mara'});
});

test('errors show the server detail, a hint keyed on the code, and a recorded provider failure reloads the library', async () => {
  let refresh = {status_code:502, code:'provider_error', detail:'Gemini returned HTTP 500 while listing voices. Try again later.'};
  const env = environment({'POST /api/voices/gemini/refresh':() => refresh});
  env.api.render(env.container, env.options({library:library()}));
  env.click('refresh-gemini');
  await settle();
  assert.match(env.container.innerHTML, /Gemini returned HTTP 500 while listing voices/);
  assert.equal(env.hooks.changes, 1, 'The saved error state is reloaded');
  refresh = {status_code:400, code:'gemini_key_missing', detail:'No Gemini API key is configured.'};
  env.click('refresh-gemini');
  await settle();
  assert.match(env.container.innerHTML, /No Gemini API key is configured\. Add a Gemini API key in Providers &amp; settings first\./);
  assert.equal(env.hooks.changes, 1, 'A refused request changes nothing, so nothing is reloaded');
});
