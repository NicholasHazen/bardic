// Exercise actual reader startup and progress persistence across the product rename.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../bardic/static/app.js'), 'utf8');
function between(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a + start.length);
  assert.ok(a >= 0 && b > a, `${start} boundaries`);
  return source.slice(a, b);
}
function environment(prior = {}, unavailable = false) {
  const storage = new Map(Object.entries(prior));
  const books = ['first', 'legacy', 'current'].map(id => ({id,
    chapters:[{id:'c1'}, {id:'c2'}],
    segments:[{id:'s1', chapter_id:'c1'}, {id:'s2', chapter_id:'c2'}],
  }));
  const nodes = new Map();
  const scope = {
    document:{querySelector:selector => {
      if (!nodes.has(selector)) nodes.set(selector, {listeners:{}, addEventListener(event, fn) { this.listeners[event] = fn; }});
      return nodes.get(selector);
    }},
    Audio:class { constructor() { this.currentTime = 0; } },
    localStorage:{
      getItem:key => { if (unavailable) throw new Error('Storage disabled'); return storage.get(key) ?? null; },
      setItem:(key, value) => { if (unavailable) throw new Error('Storage disabled'); storage.set(key, value); },
    },
    clearTimeout, stopAudio:()=>{}, renderBook:()=>{}, renderReader:()=>{}, updatePlayer:()=>{}, updateListeningBuffer:()=>{}, pollJobs:async()=>{}, refreshStatus:async()=>{},
    refreshLibrary:async()=>{ scope.reader.state.books = books; },
    request:async url => books.find(book => url === `/api/books/${book.id}`),
    toast:message => { throw new Error(message); },
  };
  vm.runInNewContext([
    between('const $ =', 'function toast('),
    source.split('\n').find(line => line.startsWith('const progressKey =')),
    between('function saveProgress(', 'function stopAudio('),
    between('function setPlaybackRate(', 'function beginVoicePreview('),
    between('async function selectBook(', 'function applyBook('),
    source.split('\n').find(line => line.startsWith("$('#playback-speed').addEventListener")),
    between('async function init(', '\nsetupVoicePreviews();'),
    'globalThis.reader = {state,audio,init,saveProgress,safeRead};',
  ].join('\n'), scope);
  return {reader:scope.reader, storage, nodes};
}
const legacyProgress = {chapterId:'c2', segmentId:'s2', currentTime:12.5};

test('legacy last book, reading position, and speed survive the rename', async () => {
  const prior = {
    'spintails:lastBook':JSON.stringify('legacy'),
    'spintails:progress:legacy':JSON.stringify(legacyProgress),
    'spintails:speed':JSON.stringify(1.5),
  };
  const {reader, storage, nodes} = environment(prior);
  await reader.init();
  assert.equal(reader.state.book.id, 'legacy');
  assert.equal(reader.state.chapterId, 'c2');
  assert.equal(reader.state.segmentId, 's2');
  assert.equal(reader.state.pendingOffset, 12.5);
  assert.equal(reader.audio.playbackRate, 1.5);
  assert.equal(reader.audio.defaultPlaybackRate, 1.5, 'Media load must retain the selected rate');
  assert.deepEqual(JSON.parse(storage.get('bardic:progress:legacy')), legacyProgress);
  assert.equal(JSON.parse(storage.get('bardic:lastBook')), 'legacy');
  nodes.get('#playback-speed').listeners.change({target:{value:'2'}});
  assert.equal(reader.audio.playbackRate, 2);
  assert.equal(reader.audio.defaultPlaybackRate, 2);
  assert.equal(JSON.parse(storage.get('bardic:speed')), 2);
  for (const [key, value] of Object.entries(prior)) assert.equal(storage.get(key), value);
});

test('Bardic preferences take precedence over legacy keys, including zero offsets', async () => {
  const newerProgress = {chapterId:'c1', segmentId:'s1', currentTime:0};
  const {reader} = environment({
    'spintails:lastBook':JSON.stringify('legacy'), 'bardic:lastBook':JSON.stringify('current'),
    'spintails:progress:current':JSON.stringify(legacyProgress), 'bardic:progress:current':JSON.stringify(newerProgress),
    'spintails:speed':JSON.stringify(1.5), 'bardic:speed':JSON.stringify(1),
  });
  await reader.init();
  assert.equal(reader.state.book.id, 'current');
  assert.equal(reader.state.chapterId, 'c1');
  assert.equal(reader.state.segmentId, 's1');
  assert.equal(reader.state.pendingOffset, 0);
  assert.equal(reader.audio.playbackRate, 1);
});

test('malformed new preferences fall back to defaults without resurrecting old choices', async () => {
  const {reader} = environment({
    'spintails:lastBook':JSON.stringify('legacy'), 'bardic:lastBook':'invalid JSON',
    'spintails:speed':JSON.stringify(1.5), 'bardic:speed':'invalid JSON',
  });
  await reader.init();
  assert.equal(reader.state.book.id, 'first');
  assert.equal(reader.audio.playbackRate, 1);
});

test('reader still initializes when browser storage is unavailable', async () => {
  const {reader} = environment({}, true);
  await reader.init();
  assert.equal(reader.state.book.id, 'first');
  assert.equal(reader.audio.playbackRate, 1);
  assert.doesNotThrow(() => reader.saveProgress());
});
