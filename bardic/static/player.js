/* Player extras around app.js's audio element: the listening status pill and its
   announcements, the ready range on the scrubber, ±15 s skips, the sleep timer and
   the lock-screen position. app.js attaches its functions once (attach) and calls
   update() whenever the player repaints. Exposes window.BardicPlayer.

   Nothing here starts narration on its own: skips and chapter keys go through the
   same seek and Play paths as the player's own controls, and the sleep timer
   pauses exactly as the Pause button does (which also cancels preparation that
   continuous listening started automatically). */
(root => {
  'use strict';
  const SKIP_SECONDS = 15;
  const SLEEP_CHOICES = ['off', '15', '30', '45', '60', 'chapter'];
  let api = null;
  let last = null;            // the last status painted
  let lastPosition = '';      // the last lock-screen position sent
  const sleep = {mode:'off', remaining:0, at:0, timer:null, ended:false};

  const now = () => (api?.now || Date.now)();
  const status = () => api && root.BardicListenStatus ? last : null;
  const node = selector => api?.$?.(selector) || null;

  function attach(options) {
    api = options;
    const select = node('#sleep-timer');
    if (select?.addEventListener) select.addEventListener('change', event => setSleep(event.target.value));
    node('#skip-back')?.addEventListener?.('click', () => skip(-SKIP_SECONDS));
    node('#skip-forward')?.addEventListener?.('click', () => skip(SKIP_SECONDS));
    node('#player-status-action')?.addEventListener?.('click', event => {
      const action = event.currentTarget?.dataset?.action || event.target?.dataset?.action;
      if (action === 'narrator') api.openNarrator?.();
      else if (action) api.act?.(action);
    });
    return root.BardicPlayer;
  }

  // ---- Status pill, announcements and the ready range -------------------------------
  function update(facts = {}) {
    if (!api) return null;
    const pill = node('#player-status');
    if (facts.preview || !root.BardicListenStatus) {
      if (pill) pill.hidden = true;
      return null;
    }
    if (facts.playing) sleep.ended = false;
    const engine = api.statusInput?.() || {};
    const next = root.BardicListenStatus.compute({...engine, ...facts, sleepEnded:sleep.ended});
    paintStatus(next);
    const message = root.BardicListenStatus.announcement(last, next);
    // Only a change of state is announced; the first state after loading is not.
    if (message && last) announce(message);
    last = next;
    paintReady();
    updatePosition();
    paintSleep();
    return next;
  }
  function announce(text) {
    const region = node('#player-announcer');
    if (region) region.textContent = text;
  }
  function paintStatus(next) {
    const pill = node('#player-status'), label = node('#player-status-label'), detail = node('#player-status-detail'), action = node('#player-status-action');
    if (!pill || !label) return;
    pill.hidden = false;
    if (pill.dataset) pill.dataset.state = next.state;
    label.setAttribute?.('data-tone', next.tone);
    if (label.textContent !== next.label) label.textContent = next.label;
    if (detail && detail.textContent !== next.detail) detail.textContent = next.detail;
    pill.title = next.detail || next.label;
    if (action) {
      action.hidden = !next.action;
      if (next.action) {
        if (action.textContent !== next.action.label) action.textContent = next.action.label;
        if (action.dataset) action.dataset.action = next.action.id;
      }
    }
  }
  function paintReady() {
    const layer = node('#player-ready');
    if (!layer || !api.timeline) return;
    const timeline = api.timeline();
    const ranges = root.BardicListenStatus.readyRanges(timeline.entries.map(entry => ({start:entry.start, seconds:entry.seconds, ready:entry.ready})), timeline.total);
    const html = ranges.map(([from, to]) => `<span style="left:${(from * 100).toFixed(2)}%;width:${((to - from) * 100).toFixed(2)}%"></span>`).join('');
    if (layer.innerHTML !== html) layer.innerHTML = html;
  }

  // ---- Seeking ------------------------------------------------------------------------
  function skip(seconds) {
    if (!api?.timeline || !api.position) return;
    const timeline = api.timeline();
    if (!(timeline.total > 0)) return;
    const from = api.position(timeline).time;
    seekTo(Math.min(Math.max(0, from + Number(seconds || 0)), Math.max(0, timeline.total - .25)));
  }
  function seekTo(time) {
    if (!api?.seek || !Number.isFinite(Number(time))) return;
    api.seek(Math.max(0, Number(time)));
  }
  // Lock-screen previous/next move by chapter. Previous restarts this chapter first
  // when you are more than a few seconds into it, as audiobook players do.
  function chapter(delta) {
    if (!api?.chapterStep) return;
    if (delta < 0 && api.position && api.timeline && api.position(api.timeline()).time > 3) { seekTo(0); return; }
    api.chapterStep(delta < 0 ? -1 : 1);
  }
  function updatePosition() {
    const session = api.mediaSession?.() ?? (typeof navigator !== 'undefined' ? navigator : root.navigator)?.mediaSession;
    if (!session?.setPositionState || !api.timeline || !api.position) return;
    const timeline = api.timeline();
    const duration = timeline.total;
    if (!(duration > 0)) return;
    const position = Math.min(duration, Math.max(0, api.position(timeline).time));
    const playbackRate = Number(api.audio?.playbackRate) || 1;
    const key = `${Math.round(duration)}:${Math.floor(position)}:${playbackRate}`;
    if (key === lastPosition) return;
    lastPosition = key;
    try { session.setPositionState({duration, position, playbackRate}); } catch { /* Unsupported or out of range. */ }
  }

  // ---- Sleep timer --------------------------------------------------------------------
  // Time counts only while narration plays. When it runs out, or at the end of the
  // chapter, playback pauses through the same path as the Pause button.
  function setSleep(value) {
    const choice = SLEEP_CHOICES.includes(String(value)) ? String(value) : 'off';
    if (sleep.timer !== null) (api?.clearInterval || clearInterval)(sleep.timer);
    sleep.timer = null; sleep.ended = false;
    if (choice === 'off') sleep.mode = 'off';
    else if (choice === 'chapter') sleep.mode = 'chapter';
    else {
      sleep.mode = 'time';
      sleep.remaining = Number(choice) * 60;
      sleep.at = now();
      sleep.timer = (api?.setInterval || setInterval)(tickSleep, 1000);
    }
    paintSleep();
    return sleep.mode;
  }
  function tickSleep() {
    if (sleep.mode !== 'time') return;
    const time = now();
    if (api?.playing?.()) sleep.remaining -= (time - sleep.at) / 1000;
    sleep.at = time;
    if (sleep.remaining <= 0) fireSleep();
    else paintSleep();
  }
  function fireSleep() {
    setSleep('off');
    sleep.ended = true;
    const select = node('#sleep-timer');
    if (select) select.value = 'off';
    // The pause itself is announced as a change of state ("Paused. The sleep timer paused playback.").
    api?.pause?.();
  }
  // Called by app.js when playback is about to cross into the next chapter.
  function stopAtChapterEnd() {
    if (sleep.mode !== 'chapter') return false;
    setSleep('off');
    sleep.ended = true;
    const select = node('#sleep-timer');
    if (select) select.value = 'off';
    return true;
  }
  function paintSleep() {
    const label = node('#sleep-remaining');
    if (!label) return;
    const text = sleep.mode === 'time' ? `${Math.max(1, Math.ceil(sleep.remaining / 60))} min left` : sleep.mode === 'chapter' ? 'At chapter end' : '';
    if (label.textContent !== text) label.textContent = text;
  }
  const sleepState = () => ({mode:sleep.mode, remaining:Math.max(0, sleep.remaining), ended:sleep.ended});

  root.BardicPlayer = {attach, update, status, skip, seekTo, chapter, setSleep, stopAtChapterEnd, sleepState, tickSleep, SKIP_SECONDS};
})(typeof window !== 'undefined' ? window : globalThis);
