/* Local, best-effort operational diagnostics. Never send messages, source text,
   credentials, URLs, or stack traces. Reporting must never block playback. */
(() => {
  'use strict';
  const events = new Set(['buffer_failed','cache_read_failed','listen_request_failed','listen_poll_failed','preview_failed',
    'playback_media_error','playback_play_rejected','playback_waiting','playback_resumed']);
  const operations = new Set(['prepare','poll','settle','cache_read','request','play','prefetch','media']);
  const recent = new Map();
  function record(event, details = {}) {
    try {
      if (!events.has(event)) return;
      const body = {event};
      for (const field of ['book_id','segment_id','session_id','job_id']) {
        const value = details[field];
        if (typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value)) body[field] = value;
      }
      if (operations.has(details.operation)) body.operation = details.operation;
      if (Number.isFinite(details.playback_rate) && details.playback_rate >= .25 && details.playback_rate <= 4) body.playback_rate = details.playback_rate;
      if (Number.isInteger(details.http_status) && details.http_status >= 100 && details.http_status <= 599) body.http_status = details.http_status;
      if (Number.isInteger(details.media_error_code) && details.media_error_code >= 1 && details.media_error_code <= 4) body.media_error_code = details.media_error_code;
      const signature = JSON.stringify(body), now = Date.now();
      if (recent.has(signature) && now - recent.get(signature) < 10000) return;
      recent.set(signature, now);
      if (recent.size > 100) recent.delete(recent.keys().next().value);
      void fetch('/api/diagnostics', {method:'POST',headers:{'Content-Type':'application/json'},
        body:signature,keepalive:true}).catch(() => {});
    } catch { /* A disabled network or optional logger cannot stop the reader. */ }
  }
  window.BardicDiagnostics = {record};
})();
