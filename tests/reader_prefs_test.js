// Reader appearance preferences: page-width choices, validation of saved values, and the CSS that backs each.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../bardic/static/app.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../bardic/static/reader.css'), 'utf8');

function load(saved) {
  const start = source.indexOf('const READER_THEMES'), end = source.indexOf('function renderReaderAppearance(');
  assert.ok(start >= 0 && end > start);
  const writes = [];
  const context = {state:{}, document:{body:{dataset:{}, style:{setProperty() {}}}}, $:() => null,
    safeRead:() => saved, safeWrite:(key, value) => writes.push([key, {...value}]), updateHighlight() {}, renderReaderAppearance() {}};
  vm.runInNewContext(`${source.slice(start, end)}\nglobalThis.api = {readerPrefs, applyReaderPrefs, setReaderPref, READER_CHOICES};`, context);
  return {...context.api, context, writes};
}

test('page width offers presets out to full width, in order', () => {
  const {READER_CHOICES} = load({});
  assert.deepEqual(Array.from(READER_CHOICES.width, ([id]) => id), ['narrow', 'normal', 'wide', 'wider', 'full']);
});

test('saved page width is validated on load and applied to the body', () => {
  for (const id of ['narrow', 'normal', 'wide', 'wider', 'full']) {
    const env = load({width:id});
    assert.equal(env.readerPrefs().width, id);
    env.applyReaderPrefs();
    assert.equal(env.context.document.body.dataset.readerWidth, id);
  }
  assert.equal(load({width:'huge'}).readerPrefs().width, 'normal');
  assert.equal(load({width:42}).readerPrefs().width, 'normal');
  assert.equal(load(null).readerPrefs().width, 'normal');
});

test('choosing a width persists it and leaves other preferences alone', () => {
  const env = load({theme:'night', size:26});
  env.setReaderPref('width', 'full');
  const [key, saved] = env.writes.at(-1);
  assert.equal(key, 'bardic:reader');
  assert.equal(saved.width, 'full');
  assert.equal(saved.theme, 'night');
  assert.equal(saved.size, 26);
});

test('every width has a measure in the reader stylesheet, and full is unbounded', () => {
  for (const id of ['narrow', 'wide', 'wider', 'full']) assert.match(css, new RegExp(`data-reader-width=${id}\\]`));
  assert.match(css, /data-reader-width=full\] \{ --reader-measure:none; \}/);
  assert.match(css, /max-width:var\(--reader-measure\)/);
  // Full width still sits inside the safe-area padding of main.
  assert.match(css, /body\.reader-mode main \{[^}]*safe-area-inset-left/);
});
