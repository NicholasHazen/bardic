// Book lifecycle (bardic/static/lifecycle.js): one state per stage from one data source,
// one Next, over synthetic fixture books: fresh, analyzed, partly voiced and recorded.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const STATIC = path.join(__dirname, '../bardic/static');
const scope = {window:{}};
vm.runInNewContext(fs.readFileSync(path.join(STATIC, 'ui.js'), 'utf8'), scope);
vm.runInNewContext(fs.readFileSync(path.join(STATIC, 'lifecycle.js'), 'utf8'), scope);
const life = scope.window.BardicLifecycle;
const ui = scope.window.BardicUI;

// An original synthetic book: a narrator, two characters and one unassigned line.
function book({voices = {}, audio = {}, unassigned = 1} = {}) {
  const characters = [
    {id:'narrator', name:'Narrator', voices:voices.narrator || {}},
    {id:'unassigned', name:'Unassigned dialogue', voices:voices.unassigned || {}},
    {id:'wren', name:'Wren', voices:voices.wren || {}},
    {id:'odo', name:'Odo', voices:voices.odo || {}},
    {id:'silent', name:'Named once, never speaks', voices:{}},
  ];
  const speakers = ['narrator', 'wren', 'odo', 'narrator', 'wren', 'odo'];
  for (let i = 0; i < unassigned; i++) speakers[speakers.length - 1 - i] = 'unassigned';
  const segments = speakers.map((speaker_id, index) => ({id:`s${index}`, chapter_id:'c1', text:`Line ${index} by the quay.`, speaker_id, audio:audio[`s${index}`] || null}));
  return {id:'b1', title:'Quay lanterns', chapters:[{id:'c1', title:'One'}], characters, passages:segments};
}
const step = (id, fields = {}) => ({id, has_accepted:false, accepted_scopes:0, total_scopes:1, pending_versions:0, stale_scopes:[], ...fields});
const overview = (fields = {}, extra = {}) => ({steps:['structure', 'census', 'discovery', 'quotes', 'profiles', 'directing'].map(id => step(id, fields[id])), active_run:null, ...extra});
const accepted = {has_accepted:true, accepted_scopes:1, total_scopes:1};
const analyzed = () => overview({discovery:accepted, profiles:{has_accepted:true, accepted_scopes:3, total_scopes:3}, directing:{...accepted, stale_scopes:['c1']}});
const plain = value => JSON.parse(JSON.stringify(value));
const byId = model => Object.fromEntries(model.stages.map(stage => [stage.id, stage]));
const all = {system:{id:'Samantha'}};

test('a fresh book: nothing analyzed, Next is Analyze the story', () => {
  const model = life.compute({book:book(), overview:overview(), recordProvider:'breeze', defaultVoice:false});
  const s = byId(model);
  assert.deepEqual(plain(model.stages.map(stage => stage.id)), ['analyze', 'cast', 'script', 'record'], 'Read & listen is not a stage');
  assert.equal(s.analyze.state, 'not_started');
  assert.match(s.analyze.detail, /Character discovery, Character profiles and Speakers & delivery/);
  assert.equal(s.cast.stateLabel, '0 of 4 voiced', 'speaking characters only: the one who never speaks is not counted');
  assert.equal(s.script.state, 'in_progress');
  assert.equal(s.script.stateLabel, '1 unassigned');
  assert.equal(s.record.state, 'not_started');
  assert.deepEqual(plain(model.next), {label:'Analyze the story', tab:'analysis'});
  assert.equal(model.current, 'analyze');
});

test('an analyzed book is done even when directing is stale; Next moves to Cast', () => {
  const model = life.compute({book:book(), overview:analyzed(), recordProvider:'breeze', defaultVoice:false});
  const s = byId(model);
  assert.equal(s.analyze.state, 'complete', 'directing staleness never means "not done"');
  assert.equal(s.analyze.stateLabel, 'Done');
  assert.equal(model.next.tab, 'cast');
  assert.equal(model.next.label, 'Choose voices');
});

test('partial analysis counts required steps; results waiting for review come first', () => {
  const partial = life.compute({book:book(), overview:overview({discovery:accepted, profiles:{has_accepted:true, accepted_scopes:1, total_scopes:3}})});
  assert.equal(byId(partial).analyze.stateLabel, '1 of 3 steps', 'a step accepted for only some characters is not done');
  assert.match(byId(partial).analyze.detail, /Still needed: Character profiles and Speakers & delivery/);
  // Next opens Analyze on the first missing step (BardicAnalysisPipeline.selectStep through shell.js).
  assert.deepEqual(plain(partial.next), {label:'Run Character profiles', tab:'analysis', step:'profiles'});
  assert.match(life.strip(partial), /data-lifecycle-go="analysis" data-lifecycle-step="profiles"/);
  const review = life.compute({book:book(), overview:overview({discovery:{...accepted, pending_versions:2}})});
  assert.equal(byId(review).analyze.state, 'needs_review');
  assert.equal(byId(review).analyze.stateLabel, '2 results to review');
  assert.deepEqual(plain(review.next), {label:'Review analysis results', tab:'analysis', step:'discovery'}, 'the first step with a version waiting');
  const running = life.compute({book:book(), overview:overview({}, {active_run:{id:'r1', status:'running'}})});
  assert.equal(byId(running).analyze.state, 'running');
  assert.equal(ui.statusTone(byId(running).analyze.state), 'info');
});

test('a partly voiced book counts voices for the record service, with Default only where it exists', () => {
  const voices = {narrator:{breeze:{library:'vl_1'}}, wren:{breeze:{id:'wren-clone'}}, odo:{gemini:{id:'Puck'}}};
  const breeze = life.compute({book:book({voices}), overview:analyzed(), recordProvider:'breeze', defaultVoice:false});
  assert.equal(byId(breeze).cast.stateLabel, '2 of 4 voiced');
  assert.equal(byId(breeze).cast.state, 'in_progress');
  assert.match(byId(breeze).cast.detail, /2 of 4 speaking characters have a Breeze voice\./);
  const withDefault = life.compute({book:book({voices}), overview:analyzed(), recordProvider:'breeze', defaultVoice:true});
  assert.equal(byId(withDefault).cast.state, 'complete');
  assert.match(byId(withDefault).cast.detail, /\(2 on the default voice\)/);
  assert.equal(withDefault.next.target, 'script', 'the next open stage is the script');
  // The presented book has no legacy single-provider voice fields: a stray one is not read.
  const legacy = life.compute({book:{...book(), characters:book().characters.map(c => ({id:c.id, name:c.name, voice:'Kore'}))}, overview:analyzed(), recordProvider:'gemini', defaultVoice:false});
  assert.notEqual(byId(legacy).cast.state, 'complete', 'the legacy Gemini voice field is not a contract field');
});

test('a recorded book: every stage done, a take presented as null does not count, Next is Export', () => {
  const take = {url:'/take.wav'};
  const everyVoice = {narrator:all, unassigned:all, wren:all, odo:all};
  const audio = Object.fromEntries(Array.from({length:6}, (_, i) => [`s${i}`, take]));
  const done = life.compute({book:book({voices:everyVoice, audio, unassigned:0}), overview:analyzed(), recordProvider:'system'});
  assert.ok(done.stages.every(stage => stage.state === 'complete'), JSON.stringify(done.stages.map(s => s.state)));
  assert.deepEqual(plain(done.next), {label:'Export the audiobook', tab:'studio', target:'export'});
  assert.equal(done.current, null);
  // The server presents an out-of-date take as null audio.
  const stale = life.compute({book:book({voices:everyVoice, audio:{...audio, s2:null}, unassigned:0}), overview:analyzed()});
  assert.equal(byId(stale).record.stateLabel, '5 of 6');
  assert.match(byId(stale).record.detail, /5 of 6 passages have a Studio recording\./);
  assert.deepEqual(plain(stale.next), {label:'Record the rest', tab:'studio', target:'record'});
});

test('while the analysis status loads there is no Next, so the suggestion never flips', () => {
  const model = life.compute({book:book(), overview:null});
  assert.equal(byId(model).analyze.state, 'loading');
  assert.equal(model.next, null);
});

test('an unreadable analysis status is shown as unknown, never as done or not started', () => {
  const model = life.compute({book:book(), overview:{error:true}});
  assert.equal(byId(model).analyze.state, 'unknown');
  assert.equal(byId(model).analyze.stateLabel, 'Status unknown');
  assert.deepEqual(plain(model.next), {label:'Open Analyze', tab:'analysis'});
});

test('the strip renders one state per stage, one Next, and expands in place into stage cards', () => {
  const model = life.compute({book:book(), overview:overview(), recordProvider:'system'});
  const compact = life.strip(model);
  assert.equal((compact.match(/class="step"/g) || []).length, 4);
  assert.equal((compact.match(/class="step-state"/g) || []).length, 4, 'exactly one state per stage');
  assert.equal((compact.match(/data-lifecycle-go=/g) || []).length, 1, 'one Next action');
  assert.match(compact, /Next: Analyze the story →/);
  assert.match(compact, /aria-expanded="false">Show stages</);
  const cards = life.strip(model, {expanded:true});
  assert.equal((cards.match(/class="step-card"/g) || []).length, 4);
  assert.match(cards, /Uses Character discovery, Character profiles, Speakers &amp; delivery/);
  assert.match(cards, /data-lifecycle-go="studio" data-lifecycle-target="script"/);
  assert.match(cards, /aria-expanded="true">Hide stages</);
  assert.match(cards, /aria-current="step"/);
});
