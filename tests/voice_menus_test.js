/* Narrator and cast voice menus: Mac voices follow the book's language and hide
   novelty voices until "Show all voices"; Voices actions that cannot work say why
   and link to Settings. Offline: fetch is a local fake and nothing is played. */
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = name => fs.readFileSync(path.join(__dirname, `../bardic/static/${name}`), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(times = 10) { for (let i = 0; i < times; i++) await tick(); }

const macVoices = [
  {id:'Samantha', name:'Samantha', locale:'en-US'},
  {id:'Daniel', name:'Daniel', locale:'en-GB'},
  {id:'Zarvox', name:'Zarvox', locale:'en-US'},
  {id:'Bad News', name:'Bad News', locale:'en-US'},
  {id:'Eddy (French (France))', name:'Eddy (French (France))', locale:'fr-FR'},
  {id:'Amélie', name:'Amélie', locale:'fr-CA'},
];

function listenEnvironment({language} = {}) {
  const calls = [];
  const scope = {window:{}, setTimeout:fn => setImmediate(fn), clearTimeout:() => {},
    localStorage:{getItem:() => null, setItem() {}},
    fetch:async url => { calls.push(url); return {ok:true, status:200, json:async () => (url.includes('/takes?') ? {takes:[]} : {})}; }};
  if (language) scope.navigator = {language};
  vm.createContext(scope);
  vm.runInContext(source('listen.js'), scope);
  return {api:scope.window.BardicListen, calls};
}
class Container {
  constructor() { this.innerHTML = ''; this.handlers = {}; }
  addEventListener(name, handler) { (this.handlers[name] ||= []).push(handler); }
  dispatch(name, event) { for (const handler of this.handlers[name] || []) handler(event); }
  closest() { return null; }
  querySelector() { return null; }
  contains() { return false; }
}
const book = {id:'book-v', revision:1, chapters:[{id:'c1'}], passages:[{id:'s1', chapter_id:'c1', start:0, end:5, text:'Hello'}], characters:[]};
const status = {providers:[{id:'system', available:true}], system_voices:macVoices, tts_models:[]};

test('macVoices keeps the book language, hides novelty voices and never hides the current choice', () => {
  const {api} = listenEnvironment();
  const names = result => result.shown.map(voice => voice.name);
  const english = api.macVoices(macVoices, {language:'en-US'});
  assert.deepEqual(names(english), ['Samantha', 'Daniel']);
  assert.equal(english.hidden, 4);
  assert.deepEqual(names(api.macVoices(macVoices, {language:'fr'})), ['Eddy (French (France))', 'Amélie']);
  assert.ok(names(api.macVoices(macVoices, {language:'en', keep:'Zarvox'})).includes('Zarvox'), 'a chosen novelty voice stays listed');
  assert.equal(api.macVoices(macVoices, {language:'en', showAll:true}).shown.length, macVoices.length);
  assert.deepEqual(names(api.macVoices(macVoices, {language:'ja'})), ['Samantha', 'Daniel', 'Eddy (French (France))', 'Amélie'],
    'a language with no voices falls back to every non-novelty voice, never an empty menu');
});

test('the narrator menu follows the browser language and Show all voices reveals the rest without new requests', async () => {
  const env = listenEnvironment({language:'en-GB'});
  const container = new Container();
  await env.api.render(container, book, {status, chapterId:'c1', segmentId:'s1'});
  await settle();
  const requests = env.calls.length;
  const names = () => env.api.choices(book).voices.map(voice => `${voice.name}${voice.locale ? ` · ${voice.locale}` : ''}`);
  assert.ok(names().includes('Samantha · en-US'));
  assert.ok(!names().some(name => /Zarvox|Amélie/.test(name)), 'novelty and other-language voices start hidden');
  assert.equal(env.api.choices(book).hiddenVoices, 4, 'the narrator sheet offers Show all voices (4 hidden)');
  env.api.setShowAllVoices(book, true);
  assert.ok(names().some(name => name.startsWith('Zarvox')));
  assert.ok(names().includes('Amélie · fr-CA'));
  assert.equal(env.api.choices(book).showAllVoices, true);
  assert.equal(env.api.getSelection(book).voice, '', 'showing more voices does not change the narrator');
  assert.equal(env.calls.length, requests, 'the toggle sends nothing');
});

test('cast menus list the book language first and keep other Mac voices reachable', () => {
  const scope = {window:{}, navigator:{language:'en-US'}};
  vm.createContext(scope);
  vm.runInContext(source('ui.js'), scope);
  vm.runInContext(source('listen.js'), scope);
  vm.runInContext(source('voices.js'), scope);
  const html = scope.window.BardicVoices.cast.castOptions('system', 'id:Amélie', {builtin:{system:macVoices}}, {});
  const main = html.slice(html.indexOf('<optgroup label="Mac voices">'), html.indexOf('</optgroup>') + 11);
  assert.match(main, /Samantha · en-US/);
  assert.match(main, /value="id:Amélie" selected/, 'the assigned voice stays in the main group');
  assert.doesNotMatch(main, /Zarvox/);
  assert.match(html, /<optgroup label="More Mac voices \(other languages and novelty voices\)">.*Zarvox/);
});

function voicesEnvironment(library) {
  const clicks = [];
  const settingsButton = {click:() => clicks.push('settings')};
  const sections = {'provider-settings-gemini':{open:false}};
  const scope = {window:{}, Audio:class { play() { return Promise.resolve(); } pause() {} },
    document:{activeElement:null, getElementById:id => id === 'settings-button' ? settingsButton : sections[id] || null},
    fetch:async url => { throw new Error(`Unexpected request ${url}`); }};
  vm.createContext(scope);
  vm.runInContext(source('ui.js'), scope);
  vm.runInContext(source('voices.js'), scope);
  const container = new Container();
  const click = (action, dataset = {}) => container.dispatch('click', {preventDefault() {},
    target:{closest:selector => selector === '[data-voices-action]' ? {dataset:{voicesAction:action, ...dataset}, disabled:false} : null}});
  return {api:scope.window.BardicVoices, container, click, clicks, sections, library};
}
const libraryWithout = providers => ({voices:[], defaults:{}, drafts:[], builtin:{gemini:['Kore'], system:[]},
  providers:{breeze:{state:'ready', configured:true, message:'Connected.'}, gemini:{has_api_key:true, stored_count:0, limit:200, project_voices:[]}, ...providers}});

test('Voices disables actions that cannot work, says why, and Set up opens Settings', async () => {
  const env = voicesEnvironment();
  const library = libraryWithout({breeze:{state:'unconfigured', configured:false, message:''}, gemini:{has_api_key:false}});
  env.api.render(env.container, {library, onChange:() => {}});
  const html = () => env.container.innerHTML;
  assert.match(html(), /<div class="callout" data-tone="warn" id="voices-setup-reason"><p>Add your Breeze server in Settings/);
  assert.match(html(), /<button type="button" class="button primary" disabled data-voices-action="new" aria-describedby="voices-setup-reason">Design a Breeze voice<\/button>/);
  assert.match(html(), /data-voices-action="setup" data-provider="breeze">Set up →/);
  assert.match(html(), /role="radiogroup" aria-label="Voice service"/, 'the service switch is a BardicUI choice');
  env.click('new');
  await settle();
  env.container.dispatch('click', {target:{closest:selector => selector === '.choice > button'
    ? {dataset:{value:'gemini'}, disabled:false, getAttribute:name => name === 'role' ? 'radio' : null, closest:() => ({dataset:{choice:'voices-provider'}, children:[]})} : null}});
  assert.match(html(), /Add a Gemini API key in Settings to list or design Gemini voices\./);
  assert.match(html(), /disabled data-voices-action="new" aria-describedby="voices-setup-reason">Design a Gemini voice/);
  env.click('setup', {provider:'gemini'});
  assert.deepEqual(env.clicks, ['settings'], 'Set up uses the sidebar Settings button');
  assert.equal(env.sections['provider-settings-gemini'].open, true, 'and opens the Gemini section');
});

test('a ready provider keeps its actions and an app hook can open Settings directly', () => {
  const env = voicesEnvironment();
  const opened = [];
  env.api.render(env.container, {library:libraryWithout({}), onChange:() => {}, onOpenSettings:provider => opened.push(provider)});
  assert.match(env.container.innerHTML, /<button type="button" class="button primary" data-voices-action="new">Design a Breeze voice<\/button>/);
  assert.doesNotMatch(env.container.innerHTML, /voices-setup-reason/);
  env.click('setup', {provider:'breeze'});
  assert.deepEqual(opened, ['breeze']);
});
