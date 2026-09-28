/* Listening status model: one listener-level state for the player pill, the narrator
   sheet and the screen-reader announcements. Pure functions over a snapshot of the
   listening engine (BardicListen.statusInput) and the player (app.js updatePlayer);
   nothing here reads the DOM or sends a request. Exposes window.BardicListenStatus.

   Exactly one state applies at a time. A chapter job's end states keep their own
   names: cancelled by you (stopped-by-you), failed and interrupted are different
   events with different next steps (AGENTS.md), and a limit is not a failure. */
(root => {
  'use strict';
  const STATES = Object.freeze(['needs-narrator', 'preparing', 'playing', 'waiting-for-rate-limit', 'paused',
    'limit-reached', 'stopped-by-you', 'failed', 'interrupted', 'finished']);
  // One tone per state (BardicUI.statusTone names); playing turns warn when it will catch up.
  const TONES = Object.freeze({'needs-narrator':'neutral', preparing:'info', playing:'good', 'waiting-for-rate-limit':'warn',
    paused:'neutral', 'limit-reached':'warn', 'stopped-by-you':'neutral', failed:'bad', interrupted:'warn', finished:'good'});
  const ACTIVE_JOB = new Set(['queued', 'running']);
  const LIMITS = new Set(['quota_limited', 'budget_limited']);

  // "45 s", "3 min", "1 h 5 min". Unknown stays unknown.
  function span(seconds) {
    if (!Number.isFinite(seconds)) return 'unknown';
    const value = Math.max(0, Math.round(seconds));
    if (value < 60) return `${value} s`;
    if (value < 3600) return `${Math.round(value / 60)} min`;
    return `${Math.floor(value / 3600)} h ${Math.round(value % 3600 / 60)} min`;
  }
  const clock = iso => {
    const date = iso ? new Date(iso) : null;
    return date && !Number.isNaN(date.getTime()) ? date.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'}) : '';
  };
  const result = (state, label, detail = '', action = null, extra = {}) =>
    ({state, label, tone:extra.tone || TONES[state], detail, action, aheadSeconds:extra.aheadSeconds ?? null});

  /**
   * input: {
   *   mode: 'simple' | 'enhanced' | 'performance',
   *   available: new audio can be made with the chosen narrator,
   *   unavailableReason: why not (shown with needs-narrator),
   *   readyHere: the current passage has playable audio,
   *   playing: narration audio is playing; preparing: waiting for audio before it can play;
   *   buffering: the media element stalled while playing;
   *   aheadSeconds: listening seconds ready ahead of the current position, at the current speed;
   *   ended: 'chapter' | 'book' | '' (playback reached the end and stopped there);
   *   started: there is a listening position to resume;
   *   error: an engine error ('' when none);
   *   job: the chapter (or performance) job for the current chapter, or null;
   *   jobCancelledByPause: that job was cancelled by Pause (or the sleep timer);
   *   chapterPrep: {completed, total} while passages of this chapter are prepared without playing;
   *   remainingSeconds: listening still to prepare from here (null when unknown);
   *   estimate: {safe, firstAudioSeconds, catchUpSeconds, maxSafeRate, quotaBlocked} or null;
   *   rate: playback speed; sleepEnded: the sleep timer paused playback.
   * }
   */
  function compute(input = {}) {
    const job = input.job || null;
    const status = job?.status || '';
    const remaining = Number.isFinite(input.remainingSeconds) ? input.remainingSeconds : null;
    const rate = Number(input.rate) || 1;
    const ahead = Number.isFinite(input.aheadSeconds) ? Math.max(0, input.aheadSeconds) : null;
    const waiting = ACTIVE_JOB.has(status) && Number(job.waiting_seconds) > 0;
    const waitDetail = () => `Requests are paced to your per-minute limit; the next one starts in about ${span(Number(job.waiting_seconds))}.`;
    const reason = job?.error || job?.message || '';

    if (input.playing && !input.preparing && !input.buffering) {
      const estimate = input.estimate;
      const warn = estimate && estimate.safe === false && remaining;
      const aheadText = remaining === 0 ? 'Ready to the end of the chapter' : ahead === null ? '' : `${span(ahead)} ready ahead`;
      const detail = warn ? `At ${rate}× you reach unprepared audio${Number.isFinite(estimate.catchUpSeconds) ? ` in about ${span(estimate.catchUpSeconds)}` : ''}; playback pauses there until it is ready.`
        : aheadText ? `${aheadText}.` : '';
      return result('playing', 'Playing', detail, null, {aheadSeconds:ahead, tone:warn ? 'warn' : undefined});
    }
    if (input.preparing || input.buffering) {
      if (waiting) return result('waiting-for-rate-limit', 'Waiting for the request limit', waitDetail());
      const first = input.estimate?.firstAudioSeconds;
      return result('preparing', 'Preparing audio', Number.isFinite(first) && first > 0 ? `First audio in about ${span(first)}.` : 'Getting the next passages ready.');
    }
    if (input.ended) return result('finished', 'Finished', input.ended === 'book' ? 'The end of the book.' : 'The end of this chapter.');
    // A job that ended before this chapter was fully prepared. Once nothing is
    // left to prepare, how preparation ended no longer matters to the listener.
    const unfinished = remaining === null || remaining > 0;
    if (job && unfinished && !input.jobCancelledByPause) {
      const reset = clock(job.quota?.resets_at);
      if (status === 'quota_limited') return result('limit-reached', 'Daily limit reached', `Finished audio still plays. Preparation can resume after the daily reset${reset ? ` at ${reset}` : ''}.`, {id:'resume', label:'Resume'});
      if (status === 'budget_limited') return result('limit-reached', 'Spending limit reached', 'Finished audio still plays. Resume asks for more.', {id:'resume', label:'Resume'});
      if (status === 'cancelled') return result('stopped-by-you', 'Preparation stopped', 'You stopped preparing this chapter. Finished audio is saved.', {id:'resume', label:'Resume'});
      if (status === 'failed') return result('failed', 'Could not prepare audio', `${reason || 'Preparation failed.'} Finished audio is saved.`, {id:'resume', label:'Try again'});
      if (status === 'interrupted') return result('interrupted', 'Preparation interrupted', 'Bardic stopped while preparing this chapter. Finished audio is saved.', {id:'resume', label:'Resume'});
    }
    if (input.error) return result('failed', 'Could not prepare audio', input.error, {id:'retry', label:'Try again'});
    if (ACTIVE_JOB.has(status) && !LIMITS.has(status)) {
      if (waiting) return result('waiting-for-rate-limit', 'Waiting for the request limit', waitDetail());
      return result('preparing', 'Preparing ahead', remaining ? `About ${span(remaining / rate)} of listening still to prepare.` : 'Preparing this chapter.');
    }
    if (input.chapterPrep) {
      const {completed = 0, total = 0} = input.chapterPrep;
      return result('preparing', 'Preparing ahead', `${completed} of ${total} passages saved.`);
    }
    if (!input.readyHere && (input.mode === 'enhanced' || input.available === false)) {
      const detail = input.mode === 'enhanced' ? 'This passage has no full-cast recording. Choose one narrator to listen now.'
        : input.unavailableReason || 'This narrator cannot make new audio yet.';
      return result('needs-narrator', 'Choose a narrator', detail, {id:'narrator', label:'Choose narrator'});
    }
    const detail = input.sleepEnded ? 'The sleep timer paused playback.' : input.jobCancelledByPause ? 'Preparing ahead was stopped, so nothing more is requested.'
      : remaining === 0 ? 'This chapter is ready.' : '';
    return result('paused', input.started ? 'Paused' : 'Ready to listen', detail);
  }

  // The text a live region announces for a change of state, or '' when the
  // state did not change (counters and ready-ahead seconds never announce).
  function announcement(previous, next) {
    if (!next || previous?.state === next.state) return '';
    return [next.label, next.detail].filter(Boolean).join('. ').replace(/\.\./g, '.');
  }

  // Ready audio on the chapter timeline, as merged [from, to] fractions of its length.
  function readyRanges(entries = [], total = 0) {
    if (!(total > 0)) return [];
    const ranges = [];
    for (const entry of entries) {
      if (!entry.ready) continue;
      const from = Math.max(0, entry.start / total), to = Math.min(1, (entry.start + entry.seconds) / total);
      const last = ranges[ranges.length - 1];
      if (last && from - last[1] < 1e-6) last[1] = Math.max(last[1], to);
      else ranges.push([from, to]);
    }
    return ranges;
  }

  root.BardicListenStatus = Object.freeze({STATES, TONES, compute, announcement, readyRanges, span});
})(typeof window !== 'undefined' ? window : globalThis);
