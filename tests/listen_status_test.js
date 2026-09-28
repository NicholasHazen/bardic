/* The listening status model (listen-status.js): every engine state maps to exactly
   one listener-level state, and cancelled, failed and interrupted stay distinct. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const scope = {};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../bardic/static/listen-status.js'), 'utf8'), scope);
const {compute, STATES, TONES, announcement, readyRanges} = scope.BardicListenStatus;
const base = {mode:'simple', available:true, readyHere:true, aheadSeconds:0, rate:1};
const job = (status, extra = {}) => ({id:'job-1', status, chapter_id:'c1', ...extra});

test('the model names ten states, each with one tone', () => {
  assert.deepEqual([...STATES], ['needs-narrator', 'preparing', 'playing', 'waiting-for-rate-limit', 'paused',
    'limit-reached', 'stopped-by-you', 'failed', 'interrupted', 'finished']);
  for (const state of STATES) assert.ok(['neutral', 'good', 'info', 'warn', 'bad'].includes(TONES[state]), state);
});

test('the pill tones agree with the UI kit\'s one status map', () => {
  const ui = {};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../bardic/static/ui.js'), 'utf8'), ui);
  // ui.js names the rate-limit wait "waiting_rate_limit".
  for (const state of STATES) assert.equal(TONES[state], ui.BardicUI.statusTone(state === 'waiting-for-rate-limit' ? 'waiting_rate_limit' : state), state);
});

test('every engine state maps to exactly one pill state', () => {
  const cases = [
    [{available:false, readyHere:false, unavailableReason:'Add a Gemini API key in Settings.'}, 'needs-narrator'],
    [{mode:'enhanced', readyHere:false}, 'needs-narrator'],
    [{preparing:true}, 'preparing'],
    [{buffering:true, playing:true}, 'preparing'],
    [{preparing:true, job:job('running', {waiting_seconds:40})}, 'waiting-for-rate-limit'],
    [{job:job('running', {waiting_seconds:12}), remainingSeconds:200}, 'waiting-for-rate-limit'],
    [{job:job('running'), remainingSeconds:200}, 'preparing'],
    [{job:job('queued'), remainingSeconds:200}, 'preparing'],
    [{chapterPrep:{completed:2, total:9}}, 'preparing'],
    [{playing:true, aheadSeconds:95}, 'playing'],
    [{playing:true, job:job('running', {waiting_seconds:30}), remainingSeconds:300}, 'playing'],
    [{}, 'paused'],
    [{started:true}, 'paused'],
    [{job:job('cancelled'), jobCancelledByPause:true, remainingSeconds:100, started:true}, 'paused'],
    [{job:job('quota_limited', {quota:{resets_at:'2026-09-29T07:00:00+00:00'}}), remainingSeconds:100}, 'limit-reached'],
    [{job:job('budget_limited'), remainingSeconds:100}, 'limit-reached'],
    [{job:job('cancelled'), remainingSeconds:100}, 'stopped-by-you'],
    [{job:job('failed', {error:'Gemini narration timed out.'}), remainingSeconds:100}, 'failed'],
    [{error:'The narration request could not be found.'}, 'failed'],
    [{job:job('interrupted'), remainingSeconds:100}, 'interrupted'],
    [{ended:'book'}, 'finished'],
    [{ended:'chapter'}, 'finished'],
    // Once the chapter is fully prepared, how preparation ended no longer matters.
    [{job:job('failed'), remainingSeconds:0}, 'paused'],
    [{job:job('completed'), remainingSeconds:0}, 'paused'],
  ];
  for (const [input, expected] of cases) {
    const result = compute({...base, ...input});
    assert.equal(result.state, expected, JSON.stringify(input));
    assert.ok(STATES.includes(result.state));
    assert.ok(result.label && typeof result.detail === 'string');
    assert.ok(['neutral', 'good', 'info', 'warn', 'bad'].includes(result.tone));
  }
  // Every one of the ten states is reachable.
  assert.deepEqual(new Set(cases.map(([, state]) => state)).size, STATES.length);
});

test('cancelled, failed and interrupted keep their own state, label and next step', () => {
  const ended = status => compute({...base, job:job(status, {error:'Timed out; not resent.'}), remainingSeconds:60});
  const cancelled = ended('cancelled'), failed = ended('failed'), interrupted = ended('interrupted');
  assert.deepEqual([cancelled.state, failed.state, interrupted.state], ['stopped-by-you', 'failed', 'interrupted']);
  assert.equal(new Set([cancelled.label, failed.label, interrupted.label]).size, 3);
  assert.equal(failed.tone, 'bad');
  assert.notEqual(cancelled.tone, 'bad', 'your own stop is not an error');
  assert.match(failed.detail, /Timed out; not resent\./);
  for (const result of [cancelled, failed, interrupted]) assert.match(result.detail, /Finished audio is saved/);
  assert.equal(failed.action.id, 'resume');
  assert.equal(ended('quota_limited').state, 'limit-reached', 'a limit is not a failure');
});

test('playing reports the ready-ahead time and warns when preparation will fall behind', () => {
  const fine = compute({...base, playing:true, aheadSeconds:130, remainingSeconds:400});
  assert.equal(fine.tone, 'good');
  assert.equal(fine.aheadSeconds, 130);
  assert.match(fine.detail, /2 min ready ahead/);
  assert.match(compute({...base, playing:true, remainingSeconds:0}).detail, /Ready to the end of the chapter/);
  const behind = compute({...base, playing:true, rate:2, aheadSeconds:20, remainingSeconds:400, estimate:{safe:false, catchUpSeconds:180}});
  assert.equal(behind.state, 'playing');
  assert.equal(behind.tone, 'warn');
  assert.match(behind.detail, /At 2× you reach unprepared audio in about 3 min/);
});

test('paused says when Pause stopped preparation ahead, and the sleep timer', () => {
  assert.match(compute({...base, started:true, job:job('cancelled'), jobCancelledByPause:true, remainingSeconds:50}).detail, /nothing more is requested/);
  assert.match(compute({...base, started:true, sleepEnded:true}).detail, /sleep timer/);
  assert.equal(compute({...base}).label, 'Ready to listen');
  assert.equal(compute({...base, started:true}).label, 'Paused');
});

test('unknown remaining time is not treated as ready', () => {
  const unknown = compute({...base, job:job('failed'), remainingSeconds:null});
  assert.equal(unknown.state, 'failed');
  assert.doesNotMatch(compute({...base, playing:true, aheadSeconds:null}).detail, /ready ahead/);
});

test('announcements are for changes of state only, never for counters', () => {
  const playing = compute({...base, playing:true, aheadSeconds:30});
  const later = compute({...base, playing:true, aheadSeconds:90});
  assert.equal(announcement(playing, later), '', 'ready-ahead seconds changing is not announced');
  const waiting1 = compute({...base, preparing:true, job:job('running', {waiting_seconds:40})});
  const waiting2 = compute({...base, preparing:true, job:job('running', {waiting_seconds:39})});
  assert.equal(announcement(waiting1, waiting2), '', 'a countdown is not announced');
  assert.match(announcement(playing, compute({...base, started:true})), /^Paused/);
  assert.match(announcement(null, playing), /^Playing/);
});

test('ready ranges merge adjacent prepared audio on the chapter timeline', () => {
  const entries = [{start:0, seconds:10, ready:true}, {start:10, seconds:10, ready:true}, {start:20, seconds:20, ready:false}, {start:40, seconds:10, ready:true}];
  assert.deepEqual(JSON.parse(JSON.stringify(readyRanges(entries, 50))), [[0, .4], [.8, 1]]);
  assert.deepEqual(JSON.parse(JSON.stringify(readyRanges(entries, 0))), []);
});
