// Copy lint for the primary surfaces: index.html text and the user-facing strings in app.js,
// listen.js, lifecycle.js and script.js. The Details tab (provenance and inspection) is exempt.
// Checks: banned terms, headings without a closing period, and step names that match the
// analysis step registry (bardic/pipeline/steps/*.py `label`).
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..');
const read = file => fs.readFileSync(path.join(ROOT, file), 'utf8');

// Terms that name UI that no longer exists, or internal jargon (docs/UI-GUIDE.md glossary).
const BANNED = [
  ['Studio voices', /\bStudio voices\b/i],
  ['Listening settings', /\bListening settings\b/i],
  ['Classic', /\bClassic\b/],
  ['Cast & voices', /\bCast & voices\b/i],
  ['Import ebook', /\bImport ebook\b/i],
  ['chunk', /\bchunks?\b/i],
  ['fingerprint', /\bfingerprints?\b/i],
  ['payload', /\bpayloads?\b/i],
];

// Each exception says why it is legitimate. Matching is by file and exact text fragment.
const ALLOW = [
  // Phase 4 moved the expert chunk controls to Settings (Narration, Advanced) and reworded
  // the listen.js status copy, so listen.js needs no exceptions. Add one here with a reason.
];

// ---- Extraction ------------------------------------------------------------------------
const ENTITIES = {amp:'&', lt:'<', gt:'>', quot:'"', '#39':"'", nbsp:' ', rarr:'→', larr:'←'};
const decode = text => text.replace(/&(#?\w+);/g, (all, name) => ENTITIES[name] ?? all);
const clean = text => decode(text).replace(/\$\{[^}]*\}/g, ' ').replace(/\s+/g, ' ').trim();
const human = text => /[A-Za-z]/.test(text) && (/\s/.test(text) || /^[A-Z]/.test(text)) && !/^[\w-]+(\s[\w-]+)*$/.test(text) || /^[A-Z][a-z]+(\s|$)/.test(text);

// Text a person reads in a markup fragment: text between tags plus aria-label, title and placeholder.
function markupText(fragment) {
  const out = [];
  for (const match of fragment.matchAll(/\b(?:aria-label|title|placeholder)="([^"]*)"/g)) out.push(match[1]);
  const text = fragment.replace(/<svg[\s\S]*?<\/svg>/g, ' ').replace(/<code>[\s\S]*?<\/code>/g, ' ').replace(/<[^>]*>/g, '\n');
  out.push(...text.split('\n'));
  return out.map(clean).filter(Boolean);
}

// The static parts of every string and template literal in a JavaScript source.
function jsStrings(source) {
  const strings = [];
  let i = 0;
  const templateStack = [];
  while (i < source.length) {
    const ch = source[i], next = source[i + 1];
    if (ch === '/' && next === '/') { i = source.indexOf('\n', i); if (i < 0) break; continue; }
    if (ch === '/' && next === '*') { i = source.indexOf('*/', i + 2) + 2; continue; }
    if (ch === "'" || ch === '"') {
      let j = i + 1, value = '';
      while (j < source.length && source[j] !== ch) { if (source[j] === '\\') { value += source[j + 1]; j += 2; continue; } value += source[j++]; }
      strings.push(value); i = j + 1; continue;
    }
    if (ch === '`' || (ch === '}' && templateStack.length && templateStack.at(-1) === 0)) {
      if (ch === '}') templateStack.pop();
      let j = i + 1, value = '';
      while (j < source.length) {
        if (source[j] === '\\') { value += source[j + 1]; j += 2; continue; }
        if (source[j] === '`') { j++; break; }
        if (source[j] === '$' && source[j + 1] === '{') { templateStack.push(0); j += 2; break; }
        value += source[j++];
      }
      strings.push(value); i = j; continue;
    }
    if (templateStack.length) {
      if (ch === '{') templateStack[templateStack.length - 1]++;
      else if (ch === '}') templateStack[templateStack.length - 1]--;
    }
    // A regular expression literal after an operator or bracket: skip it so its quotes do not count.
    if (ch === '/' && /[(,=:[!&|?{};]\s*$/.test(source.slice(Math.max(0, i - 3), i))) {
      let j = i + 1;
      while (j < source.length && source[j] !== '/' && source[j] !== '\n') { if (source[j] === '\\') j++; if (source[j] === '[') { while (j < source.length && source[j] !== ']') j++; } j++; }
      i = j + 1; continue;
    }
    i++;
  }
  return strings;
}

function without(html, id) {
  const start = html.indexOf(`<section id="${id}"`);
  if (start < 0) return html;
  let depth = 0, index = start;
  const tag = /<(\/?)section\b[^>]*>/g;
  tag.lastIndex = start;
  for (let match; (match = tag.exec(html));) {
    depth += match[1] ? -1 : 1;
    if (!depth) { index = tag.lastIndex; break; }
  }
  return html.slice(0, start) + html.slice(index);
}

const html = read('bardic/static/index.html');
const primaryHtml = without(html.replace(/<head>[\s\S]*?<\/head>/, ''), 'details-view');
const surfaces = [
  {file:'bardic/static/index.html', texts:markupText(primaryHtml)},
  ...['bardic/static/app.js', 'bardic/static/listen.js', 'bardic/static/lifecycle.js', 'bardic/static/script.js'].map(file => ({file,
    texts:jsStrings(read(file)).flatMap(value => value.includes('<') ? markupText(value) : [clean(value)]).filter(human)})),
];

test('the extractor finds real copy on every primary surface', () => {
  const find = (file, text) => surfaces.find(s => s.file === file).texts.some(item => item.includes(text));
  assert.ok(find('bardic/static/index.html', 'Script & record'), 'tab labels');
  assert.ok(find('bardic/static/index.html', 'Change narrator'), 'aria labels');
  assert.ok(!find('bardic/static/index.html', 'Provenance and inspection'), 'the Details tab is exempt');
  assert.ok(find('bardic/static/app.js', 'Choose a narrator to start listening.'), 'app.js strings');
  assert.ok(find('bardic/static/app.js', 'Nothing has been sent.'), 'app.js template text');
  assert.ok(find('bardic/static/listen.js', 'More listening options'), 'listen.js template text');
});

test('primary surfaces avoid banned terms (each exception says why)', () => {
  const problems = [];
  for (const {file, texts} of surfaces) {
    for (const text of texts) {
      for (const [name, pattern] of BANNED) {
        if (!pattern.test(text)) continue;
        if (ALLOW.some(entry => entry.file === file && text.includes(entry.text))) continue;
        problems.push(`${file}: "${name}" in “${text.slice(0, 140)}”`);
      }
    }
  }
  assert.deepEqual(problems, []);
  for (const entry of ALLOW) assert.ok(entry.why.length > 20, `explain the exception for ${entry.text}`);
});

test('headings name a job and do not end with a period', () => {
  // The empty-library welcome is the one place for the marketing voice (docs/UI-GUIDE.md).
  const welcome = html.slice(html.indexOf('<section id="welcome"'), html.indexOf('<section id="home-library"'));
  const headingSources = [
    {file:'bardic/static/index.html', source:primaryHtml.replace(welcome, '')},
    ...['bardic/static/app.js', 'bardic/static/listen.js', 'bardic/static/analysis-pipeline.js', 'bardic/static/script.js'].map(file => ({file, source:jsStrings(read(file)).join('\n')})),
  ];
  const problems = [];
  for (const {file, source} of headingSources) {
    for (const match of source.matchAll(/<h([1-4])\b[^>]*>([\s\S]*?)<\/h\1>/g)) {
      const text = clean(match[2].replace(/<[^>]*>/g, ''));
      if (/\.$/.test(text)) problems.push(`${file}: <h${match[1]}> “${text}”`);
    }
  }
  assert.deepEqual(problems, []);
});

test('step names shown in the UI are the step registry labels', () => {
  const registry = new Set(fs.readdirSync(path.join(ROOT, 'bardic/pipeline/steps')).filter(name => name.endsWith('.py'))
    .flatMap(name => [...read(`bardic/pipeline/steps/${name}`).matchAll(/^\s+label = ['"]([^'"]+)['"]/gm)].map(match => match[1])));
  assert.ok(registry.has('Character discovery') && registry.size >= 6, [...registry].join(', '));
  // lifecycle.js names the Analyze stage's required steps.
  const lifecycle = read('bardic/static/lifecycle.js');
  const required = [...lifecycle.slice(lifecycle.indexOf('REQUIRED_STEPS'), lifecycle.indexOf(']);')).matchAll(/\['\w+', '([^']+)'\]/g)].map(match => match[1]);
  assert.equal(required.length, 3);
  for (const label of required) assert.ok(registry.has(label), `${label} is not a registry label`);
  // Older names for the same steps must not reappear on primary surfaces.
  const aliases = /\b(Local census|Name census|Character scan|Scene direction|Understand the story|Plan analysis)\b/;
  const problems = surfaces.flatMap(({file, texts}) => texts.filter(text => aliases.test(text)).map(text => `${file}: “${text.slice(0, 120)}”`));
  assert.deepEqual(problems, []);
});
