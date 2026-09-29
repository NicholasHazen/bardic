/* Follow-the-narration scrolling. Exposes window.BardicFollow.

   The reader keeps the active passage on a fixed reading line instead of scrolling only when it
   leaves the screen. `geometry` and `spring` are pure (tests/follow_scroll_test.js); `create` is a
   small requestAnimationFrame controller around them that app.js drives with a sampler.

   Model. The reading band is the visible area between the reader bar and the player. The reading
   line sits at ANCHOR of the way down that band. A passage that fits the band starts on the line,
   pushed up only as far as needed to keep its end in the band. A passage taller than the band is
   followed inside: the point that has been spoken (estimated from audio time) stays on the line.
   The scroll target is recomputed from live geometry every frame, so text size, page width and
   layout changes retarget the motion instead of breaking it.

   Motion. A critically damped spring moves scrollTop: it starts and ends at rest, keeps its
   velocity when the target changes, and is never cut off by a new passage. Reduced motion jumps.
   The controller never fights the reader: a hold (manual scrolling, follow switched off) stops it
   before the next frame writes, and a finger down suspends it, so iOS momentum scrolling is left
   alone. */
(() => {
  'use strict';
  const root = typeof window !== 'undefined' ? window : globalThis;

  const ANCHOR = .38;          // reading line, as a fraction of the band from its top
  const PAD = 24;              // breathing room kept at each end of the band for a whole passage
  const OMEGA = 7;             // spring stiffness (rad/s): settles in about 0.7 s
  const MAX_SPEED = 2000;      // px/s, the peak scroll speed of a glide
  const MIN_OMEGA = 3.5;
  const SNAP_BANDS = 2.5;      // farther than this many bands away, jump instead of gliding
  const MAX_DT = 1 / 20;       // a stalled frame must not turn into a lurch
  const SETTLE_PX = .4, SETTLE_V = 3, EXTERNAL_PX = 2;

  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

  // Where scrollTop should be for this passage. Inputs are in CSS pixels; `boxTop` is the
  // passage's top edge in the viewport now. `progress` is 0..1 through the passage (audio
  // position) or null when unknown, and only matters for a passage taller than the band.
  function geometry({boxTop, boxHeight, bandTop, bandBottom, scrollTop, maxScroll, progress = 0, anchor = ANCHOR}) {
    const bandHeight = Math.max(1, bandBottom - bandTop);
    const line = bandTop + bandHeight * anchor;
    const tall = boxHeight > bandHeight - 2 * PAD;
    let desiredTop;
    if (tall) desiredTop = line - clamp(Number(progress) || 0, 0, 1) * boxHeight;
    else desiredTop = clamp(line, bandTop + PAD, Math.max(bandTop + PAD, bandBottom - PAD - boxHeight));
    const target = clamp(scrollTop + (boxTop - desiredTop), 0, Math.max(0, maxScroll));
    return {target, tall, bandHeight};
  }

  // Stiffness for a glide of this distance: the usual settle time, but slower over long
  // distances so the peak speed stays readable.
  const omegaFor = distance => clamp(MAX_SPEED * Math.E / Math.max(1, Math.abs(distance)), MIN_OMEGA, OMEGA);

  // One step of a critically damped spring toward `target`. Exact for any dt, so frame time
  // does not change the path. Returns the new {x, v}.
  function spring({x, v}, target, dt, omega = OMEGA) {
    const a = x - target, b = v + omega * a, decay = Math.exp(-omega * dt);
    return {x: target + (a + b * dt) * decay, v: (v - omega * b * dt) * decay};
  }

  // env: sample() -> null | {boxTop, boxHeight, bandTop, bandBottom, scrollTop, maxScroll,
  //   progress, playing}; scrollTo(y); held() -> true while following must not move the page;
  //   reduced() -> prefers-reduced-motion; onStop() when following ends; raf/caf optional.
  function create(env) {
    const raf = env.raf || (fn => requestAnimationFrame(fn)), caf = env.caf || (id => cancelAnimationFrame(id));
    let frame = 0, last = null, pos = 0, vel = 0, lastSet = null, suspended = false, pending = false, instant = false, omega = null;

    function stop() { if (frame) caf(frame); frame = 0; if (env.onStop) env.onStop(); last = null; vel = 0; lastSet = null; pending = false; instant = false; omega = null; }
    function write(y) { lastSet = y; env.scrollTo(y); }

    function tick(now) {
      frame = 0;
      const sample = env.sample();
      if (!sample || env.held()) { stop(); return; }
      const dt = last === null ? 0 : clamp((now - last) / 1000, 0, MAX_DT);
      last = now;
      if (suspended) { lastSet = null; vel = 0; frame = raf(tick); return; }
      // Something else moved the page (a layout shift, a native glide, the keyboard): start from there.
      if (lastSet === null || Math.abs(sample.scrollTop - lastSet) > EXTERNAL_PX) { pos = sample.scrollTop; vel = 0; }
      const geo = geometry(sample);
      const distance = geo.target - pos;
      const tracking = Boolean(sample.playing && geo.tall);
      const reduced = Boolean(env.reduced && env.reduced());
      if (instant || Math.abs(distance) > geo.bandHeight * SNAP_BANDS || (reduced && (pending || Math.abs(distance) > geo.bandHeight * .3))) {
        pos = geo.target; vel = 0;
        if (Math.abs(pos - sample.scrollTop) > .05) write(pos); else lastSet = pos;
        instant = false; pending = false;
      } else if (reduced) {
        pos = sample.scrollTop; lastSet = pos;
      } else if (Math.abs(distance) < SETTLE_PX && Math.abs(vel) < SETTLE_V) {
        pos = geo.target; vel = 0;
        if (Math.abs(pos - sample.scrollTop) > .05) write(pos); else lastSet = pos;
      } else {
        if (omega === null) omega = omegaFor(distance); // fixed for the whole glide, so it never kinks
        const next = spring({x: pos, v: vel}, geo.target, dt, omega);
        pos = next.x; vel = next.v;
        write(pos);
      }
      pending = false;
      const settled = reduced || Math.abs(geo.target - pos) < SETTLE_PX && Math.abs(vel) < SETTLE_V;
      if (!settled || tracking) frame = raf(tick); else stop();
    }

    return {
      // Glide toward the active passage's line; `jump` skips the glide.
      retarget({jump = false} = {}) {
        pending = true; omega = null; if (jump) instant = true;
        if (!frame) frame = raf(tick);
      },
      cancel: stop,
      // A finger is down: leave the page alone. Lifting it without a scroll resumes following.
      suspend() { suspended = true; },
      resume() { suspended = false; },
      get running() { return frame !== 0; },
    };
  }

  root.BardicFollow = Object.freeze({ANCHOR, PAD, OMEGA, geometry, spring, omegaFor, create});
})();
