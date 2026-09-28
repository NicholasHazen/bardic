// BardicUI (bardic/static/ui.js): escaping, formatting where unknown is never zero,
// the single status-tone map, consent labels that carry the cost, and choice keyboard rules.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const STATIC = path.join(__dirname, '../bardic/static');
function load(windowExtras = {}) {
  const scope = {window:{...windowExtras}};
  vm.runInNewContext(fs.readFileSync(path.join(STATIC, 'ui.js'), 'utf8'), scope);
  return scope.window.BardicUI;
}
const ui = load();
const HOSTILE = `<img src=x onerror="alert('x')">&`;

test('the kit is frozen and exposes the eight primitives', () => {
  assert.ok(Object.isFrozen(ui));
  for (const name of ['fmt', 'button', 'badge', 'statusTone', 'callout', 'setMessage', 'sectionHead', 'steps', 'consent', 'choice', 'bindChoices']) assert.ok(ui[name], name);
});

test('every builder escapes text, including attributes', () => {
  assert.equal(ui.esc(HOSTILE), '&lt;img src=x onerror=&quot;alert(&#39;x&#39;)&quot;&gt;&amp;');
  assert.equal(ui.esc(null), '');
  const outputs = [
    ui.button({label:HOSTILE, attrs:{'data-x':HOSTILE}}), ui.badge(HOSTILE), ui.statusBadge('failed', HOSTILE),
    ui.callout({title:HOSTILE, text:HOSTILE}), ui.sectionHead({eyebrow:HOSTILE, title:HOSTILE, lead:HOSTILE, next:{label:HOSTILE}}),
    ui.steps({label:HOSTILE, steps:[{id:HOSTILE, label:HOSTILE, state:'running', uses:[HOSTILE]}]}),
    ui.steps({variant:'cards', steps:[{id:'a', label:HOSTILE, state:HOSTILE, detail:HOSTILE, uses:[HOSTILE]}]}),
    ui.consent({scope:HOSTILE, sends:[{what:HOSTILE, where:HOSTILE}], estimate:{note:HOSTILE}, blocked:{reason:HOSTILE}}),
    ui.choice({kind:'cards', label:HOSTILE, name:HOSTILE, options:[{value:HOSTILE, label:HOSTILE, hint:HOSTILE}]}),
  ];
  for (const html of outputs) {
    assert.doesNotMatch(html, /<img/, html);
    assert.doesNotMatch(html, /"alert\(/, html);
  }
  assert.throws(() => ui.attrs({'on click':'x'}), /Invalid attribute name/);
  assert.equal(ui.attrs({a:true, b:false, c:null, d:0}), ' a d="0"');
});

test('only ui.js defines the escape helper for new code', () => {
  const source = fs.readFileSync(path.join(STATIC, 'ui.js'), 'utf8');
  assert.equal((source.match(/\.replace\(\/\[&<>"'\]\/g/g) || []).length, 1);
});

test('money: unknown is never $0, sub-cent is not rounded to zero', () => {
  for (const missing of [undefined, null, NaN, Infinity, '0.5']) assert.equal(ui.fmt.money(missing), 'unknown');
  assert.equal(ui.fmt.money(null, {unknown:'price unknown'}), 'price unknown');
  assert.equal(ui.fmt.money(0), '$0.00');
  assert.equal(ui.fmt.money(0.004), '<$0.01');
  assert.equal(ui.fmt.money(0.004, {precise:true}), '$0.004');
  assert.equal(ui.fmt.money(0.00001, {precise:true}), '<$0.0001');
  assert.equal(ui.fmt.money(0.02), '$0.02');
  assert.equal(ui.fmt.money(1234.5), '$1,234.50');
  assert.equal(ui.fmt.money(0.021, {approx:true}), 'about $0.02');
  assert.equal(ui.fmt.money(0, {approx:true}), '$0.00');
});

test('percent and plural', () => {
  assert.equal(ui.fmt.percent(0.88), '88%');
  assert.equal(ui.fmt.percent(1), '100%');
  assert.equal(ui.fmt.percent(0.001), '<1%');
  assert.equal(ui.fmt.percent(0.996), '99%');
  assert.equal(ui.fmt.percent(88, {fraction:false}), '88%');
  assert.equal(ui.fmt.percent(undefined), 'unknown');
  assert.equal(ui.fmt.plural(1, 'chapter'), '1 chapter');
  assert.equal(ui.fmt.plural(2, 'chapter'), '2 chapters');
  assert.equal(ui.fmt.plural(0, 'passage'), '0 passages');
  assert.equal(ui.fmt.plural(1200, 'request'), '1,200 requests');
  assert.equal(ui.fmt.plural(null, 'request'), 'unknown requests');
  assert.equal(ui.fmt.plural(3, 'entry', 'entries'), '3 entries');
});

test('statusTone is one map that agrees with both legacy badge palettes', () => {
  // The Pipeline explorer and the Analysis tab colour badges by class name; each colour is a tone.
  const colours = {'#e5eddd':'good', '#e2eaf2':'info', '#f3e7d1':'warn', '#f4dfd9':'bad', '#ecebe5':'neutral', 'var(--green-light, #efebf1)':'accent'};
  let checked = 0;
  for (const [file, prefix] of [['pipeline.css', 'pipeline-badge'], ['analysis-pipeline.css', 'ap-chip']]) {
    const css = fs.readFileSync(path.join(STATIC, file), 'utf8');
    for (const [, selectors, background] of css.matchAll(/^([^{]+)\{[^}]*background:\s*([^;]+);/gm)) {
      const expected = colours[background.trim()];
      if (!expected) continue;
      for (const [, state] of selectors.matchAll(new RegExp(`\\.${prefix}\\.([a-z_]+)`, 'g'))) {
        assert.equal(ui.statusTone(state), expected, `${file}: ${state}`);
        checked++;
      }
    }
  }
  assert.ok(checked >= 20, `checked ${checked} legacy states`);
  assert.equal(ui.statusTone('Budget-limited'), 'warn');
  assert.equal(ui.statusTone('something new'), 'neutral');
  // Cancelled, failed and interrupted stay distinct.
  assert.deepEqual(['cancelled', 'failed', 'interrupted'].map(ui.statusTone), ['neutral', 'bad', 'warn']);
  assert.equal(ui.statusLabel('budget_limited'), 'Budget limited');
  assert.match(ui.statusBadge('running'), /data-tone="info">Running</);
  assert.match(ui.badge('x', 'not-a-tone'), /data-tone="neutral"/);
});

test('buttons: variants, sizes and busy state', () => {
  assert.equal(ui.button({label:'Go', variant:'primary', size:'large'}), '<button type="button" class="button primary large">Go</button>');
  assert.match(ui.button({label:'Delete', variant:'danger', size:'small'}), /class="button danger small"/);
  assert.match(ui.button({label:'Delete', variant:'danger-primary'}), /class="button primary danger"/);
  const busy = ui.button({label:'Run', busy:true, busyLabel:'Running…'});
  assert.match(busy, / disabled aria-busy="true">Running…</);
  assert.match(ui.button({label:'x', size:'huge'}), /class="button subtle"/);
});

test('section head keeps one H2, a short lead and one Next', () => {
  const warnings = [];
  const quiet = load({console:{warn:text => warnings.push(text)}});
  const html = quiet.sectionHead({title:'Analyze the story', lead:'Find the characters. Then review.', next:{label:'Review results', attrs:{'data-next':'review'}}});
  assert.equal((html.match(/<h2/g) || []).length, 1);
  assert.equal((html.match(/section-head-next/g) || []).length, 1);
  assert.match(html, /data-next="review"/);
  assert.equal(warnings.length, 0);
  quiet.sectionHead({title:'Too long', lead:'One. Two. Three.'});
  assert.equal(warnings.length, 1);
  assert.equal(ui.sentences('Costs $0.02 per 1.5 min. Nothing else.'), 2);
});

test('steps: one tone and one state per step; compact and card variants', () => {
  const items = [
    {id:'analyze', label:'Analyze', state:'complete'},
    {id:'cast', label:'Cast', state:'partial', stateLabel:'9 of 12 voiced', current:true, uses:['Analyze']},
    {id:'record', label:'Record', state:'not_started', action:{label:'Record', attrs:{'data-go':'record'}}},
  ];
  const compact = ui.steps({label:'Book progress', steps:items, next:{label:'Choose voices', attrs:{'data-next':'cast'}}});
  assert.match(compact, /^<ol class="steps" data-variant="compact" aria-label="Book progress">/);
  assert.equal((compact.match(/class="step"/g) || []).length, 3);
  assert.match(compact, /data-tone="warn" data-step="cast" aria-current="step"/);
  assert.match(compact, /9 of 12 voiced/);
  assert.match(compact, /Choose voices →/);
  const cards = ui.steps({variant:'cards', steps:items});
  assert.equal((cards.match(/class="badge"/g) || []).length, 3, 'exactly one badge per step');
  assert.match(cards, /Uses Analyze/);
  assert.match(cards, /data-go="record"/);
});

test('consent: the confirm label carries the cost and unknown is first-class', () => {
  const known = ui.consent({id:'c1', scope:'Chapter 1', sends:[{what:'Chapter text', where:'Gemini (Google)'}], estimate:{requests:3, cost:0.021}, confirm:'Record chapter'});
  assert.match(known, /tabindex="-1"/);
  assert.match(known, /3 requests · about \$0\.02/);
  assert.match(known, /data-consent-confirm="">Record chapter · about \$0\.02</);
  const unknown = ui.consent({id:'c2', scope:'Book', estimate:{requests:null}, confirm:'Run'});
  assert.match(unknown, /unknown requests/);
  assert.match(unknown, /class="consent-unknown">Cost unknown/);
  assert.match(unknown, />Run · cost unknown</);
  assert.doesNotMatch(unknown, /\$0/);
  assert.match(ui.consent({scope:'x', estimate:{free:true}, confirm:'Run'}), />Run · no charge</);
  assert.match(ui.consent({scope:'x'}), /Nothing leaves this computer/);
  assert.match(ui.consent({scope:'x'}), /does not cap this spending/);
  const blocked = ui.consent({id:'c3', scope:'x', blocked:{reason:'Add a Gemini key first.', setupAttrs:{'data-open-settings':'gemini'}}});
  assert.match(blocked, /<button type="button" class="button primary" disabled data-consent-confirm="" aria-describedby="c3-blocked">/);
  assert.match(blocked, /id="c3-blocked"><span>Add a Gemini key first\.<\/span><button type="button" class="button text-button" data-open-settings="gemini">Set up →</);
});

test('openConsent scrolls the block into view and focuses it, respecting reduced motion', () => {
  const calls = [];
  const node = {scrollIntoView:options => calls.push(['scroll', options]), focus:options => calls.push(['focus', options])};
  load({matchMedia:() => ({matches:true})}).openConsent(node);
  assert.equal(JSON.stringify(calls), JSON.stringify([['scroll', {block:'nearest', behavior:'auto'}], ['focus', {preventScroll:true}]]));
});

test('setMessage writes text, tone and the right live-region role', () => {
  const attrs = {};
  const node = {textContent:'', setAttribute:(k, v) => { attrs[k] = v; }, removeAttribute:k => { delete attrs[k]; }};
  ui.setMessage(node, 'Saved.', {tone:'good'});
  assert.deepEqual([node.textContent, attrs.role, attrs['data-tone']], ['Saved.', 'status', 'good']);
  ui.setMessage(node, 'Could not save.', {tone:'bad'});
  assert.deepEqual([attrs.role, attrs['data-tone']], ['alert', 'bad']);
  ui.setMessage(node, '');
  assert.deepEqual([node.textContent, attrs.role, attrs['data-tone']], ['', 'status', undefined]);
  assert.equal(ui.message({id:'m'}), '<p class="message" id="m" role="status"></p>');
});

test('choice: radiogroup markup with one tab stop, or pressed toggles', () => {
  const html = ui.choice({kind:'chips', label:'Speed', name:'speed', value:1, options:[{value:0.75, label:'0.75×'}, {value:1, label:'1×'}, {value:1.5, label:'1.5×', disabled:true}]});
  assert.match(html, /^<div class="choice" data-kind="chips" role="radiogroup" aria-label="Speed" data-choice="speed">/);
  assert.equal((html.match(/tabindex="0"/g) || []).length, 1);
  assert.match(html, /role="radio" aria-checked="true" tabindex="0" data-value="1">1×/);
  assert.match(html, /data-value="1.5" disabled>/);
  const none = ui.choice({options:[{value:'a', label:'A', disabled:true}, {value:'b', label:'B'}]});
  assert.match(none, /aria-checked="false" tabindex="0" data-value="b"/);
  const pressed = ui.choice({kind:'chips', mode:'pressed', value:['breeze'], options:[{value:'breeze', label:'Breeze'}, {value:'gemini', label:'Gemini'}]});
  assert.match(pressed, /role="group"/);
  assert.match(pressed, /aria-pressed="true" data-value="breeze"/);
  assert.doesNotMatch(pressed, /tabindex/);
  const cards = ui.choice({kind:'cards', options:[{value:'device', label:'Device', hint:'Free'}]});
  assert.match(cards, /<strong>Device<\/strong><small>Free<\/small>/);
});

test('choice keys: arrows wrap and skip disabled options; Home and End', () => {
  const disabled = [false, true, false, false];
  assert.equal(ui.nextChoiceIndex('ArrowRight', 0, disabled), 2);
  assert.equal(ui.nextChoiceIndex('ArrowDown', 3, disabled), 0);
  assert.equal(ui.nextChoiceIndex('ArrowLeft', 2, disabled), 0);
  assert.equal(ui.nextChoiceIndex('ArrowUp', 0, disabled), 3);
  assert.equal(ui.nextChoiceIndex('Home', 3, [true, false, false]), 1);
  assert.equal(ui.nextChoiceIndex('End', 0, [false, false, true]), 1);
  assert.equal(ui.nextChoiceIndex('Enter', 0, disabled), -1);
  assert.equal(ui.nextChoiceIndex('ArrowRight', 0, [true, true]), -1);
});

test('bindChoices: click and arrow keys move selection and the tab stop', () => {
  class Button {
    constructor(value, disabled = false) { this.tagName = 'BUTTON'; this.dataset = {value}; this.disabled = disabled; this.attrs = {role:'radio'}; this.focused = false; }
    getAttribute(name) { return this.attrs[name] ?? null; }
    setAttribute(name, value) { this.attrs[name] = value; }
    focus() { this.focused = true; }
    closest(selector) { return selector.includes('button') || selector.includes('radio') ? this : null; }
  }
  const buttons = [new Button('a'), new Button('b', true), new Button('c')];
  const group = {children:buttons, dataset:{choice:'mode'}};
  for (const button of buttons) button.closest = selector => selector === '.choice' ? group : button;
  const listeners = {}, changes = [];
  ui.bindChoices({addEventListener:(name, fn) => { listeners[name] = fn; }}, change => changes.push(`${change.name}=${change.value}`));
  listeners.click({target:buttons[2]});
  assert.deepEqual(buttons.map(b => [b.attrs['aria-checked'], b.attrs.tabindex]), [['false', '-1'], ['false', '-1'], ['true', '0']]);
  let prevented = false;
  listeners.keydown({target:buttons[2], key:'ArrowRight', preventDefault:() => { prevented = true; }});
  assert.ok(prevented);
  assert.equal(buttons[0].attrs['aria-checked'], 'true');
  assert.ok(buttons[0].focused);
  listeners.click({target:buttons[1]});
  assert.deepEqual(changes, ['mode=c', 'mode=a']);
});
