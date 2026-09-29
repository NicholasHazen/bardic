'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = name => fs.readFileSync(path.join(__dirname, '..', 'bardic', 'static', name), 'utf8');

test('the reader bar is fixed and never slides away', () => {
  const css = read('reader.css'), js = read('app.js');
  const bar = css.match(/\.reader-bar \{([^}]*)\}/)[1];
  assert.match(bar, /position:fixed/);
  assert.match(bar, /top:0/);
  assert.match(bar, /env\(safe-area-inset-top\)/);
  assert.doesNotMatch(css, /translateY\(-100%\)/);
  assert.doesNotMatch(css + js, /reader-chrome-hidden/);
});

test('the reading band and scroll margin start below the bar', () => {
  const css = read('reader.css'), js = read('app.js');
  assert.match(css, /body\.reader-mode \.passage \{[^}]*scroll-margin-top:calc\(90px \+ env\(safe-area-inset-top\)\)/);
  assert.match(js, /\$\('#reader-bar'\)\.getBoundingClientRect\(\)\.bottom/);
});
