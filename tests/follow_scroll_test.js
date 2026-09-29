// Follow-the-narration scrolling (bardic/static/follow-scroll.js): target geometry, the spring
// and the controller loop, over synthetic geometry.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const scope = {window:{}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../bardic/static/follow-scroll.js'), 'utf8'), scope);
const {geometry, spring, create, PAD} = scope.window.BardicFollow;

const band = {bandTop:70, bandBottom:880}; // 810 tall; the line is at 70 + 0.38 * 810
const LINE = 70 + 810 * .38;
const base = {...band, scrollTop:1000, maxScroll:100000, progress:0};

test('a passage that fits starts on the reading line', () => {
  const {target, tall} = geometry({...base, boxTop:600, boxHeight:200});
  assert.equal(tall, false);
  assert.ok(Math.abs(base.scrollTop + (600 - LINE) - target) < 1e-9);
});

test('a passage that would run off the band rises until its end is inside', () => {
  const {target} = geometry({...base, boxTop:600, boxHeight:700});
  const top = 600 - (target - base.scrollTop);
  assert.ok(Math.abs(top + 700 - (band.bandBottom - PAD)) < 1e-9);
});

test('a passage taller than the band is followed by progress', () => {
  const box = {...base, boxTop:300, boxHeight:2000};
  const at = p => geometry({...box, progress:p});
  assert.equal(at(0).tall, true);
  assert.ok(Math.abs(at(0).target - (base.scrollTop + 300 - LINE)) < 1e-9);
  assert.ok(Math.abs(at(.5).target - at(0).target - 1000) < 1e-9);
  assert.ok(Math.abs(at(2).target - at(1).target) < 1e-9, 'progress is clamped');
  assert.ok(Math.abs(geometry({...box, progress:null}).target - at(0).target) < 1e-9, 'unknown progress means the start');
});

test('the target stays inside the scrollable range (first and last passages)', () => {
  assert.equal(geometry({...base, scrollTop:10, boxTop:-500, boxHeight:100}).target, 0);
  assert.equal(geometry({...base, maxScroll:1050, boxTop:900, boxHeight:100}).target, 1050);
  assert.equal(geometry({...base, maxScroll:-5, boxTop:900, boxHeight:100}).target, 0);
});

test('a passage already on the line needs no scroll, whatever the text size', () => {
  for (const boxHeight of [120, 400]) assert.equal(geometry({...base, boxTop:LINE, boxHeight}).target, base.scrollTop);
});

test('spring is critically damped: no overshoot, ends at rest, frame rate independent', () => {
  const run = dt => {
    let s = {x:0, v:0}, peak = 0;
    for (let i = 0; i < Math.round(2 / dt); i++) { s = spring(s, 500, dt); peak = Math.max(peak, s.x); }
    return {s, peak};
  };
  for (const dt of [1 / 30, 1 / 60, 1 / 120]) {
    const {s, peak} = run(dt);
    assert.ok(peak <= 500 + 1e-6, 'no overshoot');
    assert.ok(Math.abs(s.x - 500) < .1 && Math.abs(s.v) < 1);
  }
  assert.ok(Math.abs(run(1 / 30).s.x - run(1 / 120).s.x) < 1e-6);
});

test('spring keeps its velocity when the target moves', () => {
  let s = {x:0, v:0};
  for (let i = 0; i < 10; i++) s = spring(s, 400, 1 / 60);
  const before = s.v;
  s = spring(s, 800, 1 / 600);
  assert.ok(Math.abs(s.v - before) / before < .1, 'no jerk from a retarget');
});

// A fake page: one passage box at a fixed document offset, scrolled by the controller.
function harness(over = {}) {
  const page = {y:0, docTop:2000, height:200, maxScroll:5000, playing:false, progress:0, held:false, reduced:false, writes:[], stopped:0, ...over};
  let now = 0, queue = [];
  const env = {
    sample: () => ({boxTop:page.docTop - page.y, boxHeight:page.height, ...band, scrollTop:page.y, maxScroll:page.maxScroll, progress:page.progress, playing:page.playing}),
    scrollTo: y => { page.writes.push(y); page.y = y; },
    held: () => page.held, reduced: () => page.reduced, onStop: () => { page.stopped++; },
    raf: fn => { queue.push(fn); return queue.length; }, caf: () => { queue = []; },
  };
  const follower = create(env);
  const frame = () => { const run = queue; queue = []; now += 1000 / 60; run.forEach(fn => fn(now)); };
  const settle = (max = 240) => { for (let i = 0; i < max && follower.running; i++) frame(); };
  return {page, follower, frame, settle};
}

test('glides to the line without overshoot and then stops the loop', () => {
  const h = harness({docTop:1200});
  h.follower.retarget();
  const start = h.page.y, goal = h.page.docTop - LINE;
  h.settle();
  assert.equal(h.follower.running, false);
  assert.ok(Math.abs(h.page.y - goal) < .5, `ended at ${h.page.y}, want ${goal}`);
  assert.ok(h.page.writes.every(y => y <= goal + .001 && y >= start), 'monotonic, never past the goal');
  const steps = h.page.writes.map((y, i, all) => y - (i ? all[i - 1] : start));
  assert.ok(Math.max(...steps) < 40, 'per-frame step stays small');
  assert.ok(h.page.writes.length > 20, 'takes many frames, not a jump');
});

test('a retarget mid-glide keeps moving smoothly instead of restarting', () => {
  const h = harness();
  h.follower.retarget();
  for (let i = 0; i < 12; i++) h.frame();
  const at = h.page.y, lastStep = h.page.writes.at(-1) - h.page.writes.at(-2);
  h.page.docTop += 300; // the next passage is lower down
  h.follower.retarget();
  h.frame();
  const nextStep = h.page.y - at;
  assert.ok(nextStep >= lastStep * .8, 'no braking to a stop');
  h.settle();
  assert.ok(Math.abs(h.page.y - (h.page.docTop - LINE)) < .5);
});

test('a hold stops the loop before it writes', () => {
  const h = harness();
  h.follower.retarget();
  h.frame(); h.frame();
  const writes = h.page.writes.length;
  h.page.held = true;
  h.frame();
  assert.equal(h.page.writes.length, writes);
  assert.equal(h.follower.running, false);
  assert.ok(h.page.stopped >= 1);
});

test('suspend leaves the page alone while a finger is down, resume continues', () => {
  const h = harness();
  h.follower.retarget();
  h.frame(); h.frame();
  h.follower.suspend();
  const writes = h.page.writes.length;
  h.page.y += 80; // the finger drags
  for (let i = 0; i < 5; i++) h.frame();
  assert.equal(h.page.writes.length, writes);
  h.follower.resume();
  h.settle();
  assert.ok(Math.abs(h.page.y - (h.page.docTop - LINE)) < .5);
});

test('an outside scroll (layout shift) becomes the new start without a lurch', () => {
  const h = harness();
  h.follower.retarget();
  for (let i = 0; i < 6; i++) h.frame();
  h.page.y += 120;
  const before = h.page.y;
  h.frame();
  assert.ok(Math.abs(h.page.y - before) < 40, 'continues from where the page is');
});

test('a far jump (chapter change) does not glide across the whole book', () => {
  const h = harness({docTop:60000, maxScroll:70000});
  h.follower.retarget();
  h.frame();
  assert.equal(h.page.writes.length, 1);
  assert.ok(Math.abs(h.page.y - (60000 - LINE)) < .01);
});

test('reduced motion jumps once and does not animate', () => {
  const h = harness({reduced:true});
  h.follower.retarget();
  h.frame();
  assert.equal(h.page.writes.length, 1);
  assert.ok(Math.abs(h.page.y - (h.page.docTop - LINE)) < .01);
  assert.equal(h.follower.running, false);
});

test('a tall passage keeps tracking while it plays, and stops when it does not', () => {
  const h = harness({height:2400, playing:true, progress:0});
  h.follower.retarget();
  h.settle(400);
  assert.equal(h.follower.running, true, 'still tracking');
  const before = h.page.y;
  h.page.progress = .5;
  for (let i = 0; i < 200; i++) h.frame();
  assert.ok(Math.abs(h.page.y - before - 1200) < 1, 'follows the spoken part');
  h.page.playing = false;
  h.settle();
  assert.equal(h.follower.running, false);
});

test('the last passage of a chapter settles as far as the page allows', () => {
  const h = harness({docTop:5200, maxScroll:4700});
  h.follower.retarget();
  h.settle();
  assert.equal(Math.round(h.page.y), 4700);
  assert.equal(h.follower.running, false);
});

test('long glides are slower so the peak speed stays readable', () => {
  const {omegaFor, OMEGA} = scope.window.BardicFollow;
  assert.equal(omegaFor(300), OMEGA);
  assert.ok(omegaFor(1500) < OMEGA && omegaFor(1500) >= 3.5);
  const peak = d => omegaFor(d) * d / Math.E;
  assert.ok(peak(1500) <= 2100 && peak(2400) < 3200, 'peak speed (px/s) is capped');
});
