const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const formatTime = (seconds) => { const n = Math.max(0, Math.floor(Number(seconds) || 0)); return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`; };
const safeRead = (key, fallback = null) => {
  try {
    // Read old preferences without removing them; all subsequent writes use Bardic keys.
    const value = localStorage.getItem(key) ?? (key.startsWith('bardic:') ? localStorage.getItem(`spintails:${key.slice('bardic:'.length)}`) : null);
    return JSON.parse(value) ?? fallback;
  } catch { return fallback; }
};
const safeWrite = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* Reading remains usable without browser storage. */ } };
const state = {status:null, books:[], book:null, chapterId:null, segmentId:null, tab:'read', jobs:[], poll:null, selectionVersion:0, audioSegmentId:null, pendingOffset:0, lastSave:0, loading:false, settingsBusy:false, accountChecksPending:new Set(), analysisSummary:null, analysisError:null, referenceCache:new Map(), referenceVersion:0};
const audio = new Audio();
audio.preload = 'auto';
// Loading or clearing a media source resets playbackRate to defaultPlaybackRate.
audio.defaultPlaybackRate = audio.playbackRate = Number(safeRead('bardic:speed', 1)) || 1;
let toastTimer;
let playGeneration = 0;
let preparingListen = false;
let previewEnhanced = false;
let mediaBuffering = false;
const listeningPreloads = new Map();

function toast(message, error = false) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').classList.toggle('error', error);
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, error ? 8500 : 4800);
}
async function request(path, options = {}) {
  const headers = {...options.headers};
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {...options, headers});
  let body;
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('json')) body = await response.json();
  else body = await response.text();
  if (!response.ok) {
    const detail = body?.detail ?? body?.error ?? body;
    throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(x => x.msg || JSON.stringify(x)).join('; ') : JSON.stringify(detail) || `Request failed (${response.status})`);
  }
  return body;
}
const post = (path, body = {}) => request(path, {method:'POST', body:JSON.stringify(body)});
const patch = (path, body) => request(path, {method:'PATCH', body:JSON.stringify(body)});
const currentChapter = () => state.book?.chapters.find(c => c.id === state.chapterId);
const chapterSegments = () => state.book?.segments.filter(s => s.chapter_id === state.chapterId) || [];
const segmentById = (id) => state.book?.segments.find(s => s.id === id);
const characterById = (id) => state.book?.characters.find(c => c.id === id);
const playable = (segment) => Boolean(segment?.audio?.url && segment.audio.available !== false && !segment.audio.stale && !segment.audio.is_stale);
const simpleActive = () => !previewEnhanced && Boolean(window.BardicListen?.isSimple(state.book));
const listeningAudio = segment => previewEnhanced ? (playable(segment) ? segment.audio : null) : (window.BardicListen?.resolve(state.book, segment) || (!simpleActive() && playable(segment) ? segment.audio : null));
const listeningReady = segment => Boolean(listeningAudio(segment)?.url);
const busyJob = () => state.jobs.find(job => ['queued','running'].includes(job.status));
const progressKey = () => `bardic:progress:${state.book?.id}`;
const analysisLabels = {local:'Local draft', gemini:'Gemini', openai:'OpenAI', anthropic:'Anthropic'};
const cloudProviders = ['gemini','openai','anthropic'];
const providerField = (name, provider) => $(`#${name}${provider === 'gemini' ? '' : `-${provider}`}`);
const analysisProvider = (id) => state.status?.analysis_providers?.find(provider => provider.id === id);
const providerHasKey = (id) => analysisProvider(id)?.has_api_key ?? (id === 'gemini' && Boolean(state.status?.has_api_key));
function fillSettings() {
  $('#settings-analysis-provider').value = state.status?.analysis_provider || 'local';
  $('#tts-model').innerHTML = (state.status?.tts_models || []).map(model => { const id = typeof model === 'string' ? model : model.id; return `<option value="${escapeHTML(id)}">${escapeHTML(id)}</option>`; }).join('');
  $('#tts-model').value = state.status?.tts_model || '';
  fillNarrationLimits();
  for (const provider of cloudProviders) {
    const info = analysisProvider(provider);
    fillProviderModels(provider, {
      analysis: state.status?.analysis_models_by_provider?.[provider] || info?.model || (provider === 'gemini' ? state.status?.analysis_model : '') || '',
      preprocess: state.status?.preprocess_models_by_provider?.[provider] || '',
    });
  }
}
function fillNarrationLimits() {
  const limits = state.status?.tts_limits?.[$('#tts-model').value] || {rpm:10,tpm:10000,rpd:100};
  const chunking = state.status?.listen_chunking || {ramp_seconds:[30,60],target_seconds:420,concurrency:2};
  $('#tts-rpm').value = limits.rpm; $('#tts-tpm').value = limits.tpm; $('#tts-rpd').value = limits.rpd;
  $('#tts-concurrency').value = String(chunking.concurrency);
  $('#tts-ramp').value = (chunking.ramp_seconds || []).join(', ');
  $('#tts-target').value = chunking.target_seconds;
}
function narrationLimitValues() {
  const text = $('#tts-ramp').value.trim();
  const ramp = text ? text.split(/[,\s]+/).filter(Boolean).map(Number) : [];
  if (ramp.some(value => !Number.isFinite(value) || value < 10 || value > 470) || ramp.length > 6) throw new Error('Quick-start steps must be up to six numbers from 10 to 470 seconds, separated by commas.');
  const whole = id => Number($(id).value);
  return {tts_limits:{[$('#tts-model').value.trim()]:{rpm:whole('#tts-rpm'),tpm:whole('#tts-tpm'),rpd:whole('#tts-rpd')}},
    listen_chunking:{ramp_seconds:ramp,target_seconds:whole('#tts-target'),concurrency:whole('#tts-concurrency')}};
}
function modelPicker(role, provider) { return providerField(`${role}-model`, provider); }
function modelCustom(role, provider) { return $(`#${modelPicker(role, provider).id}-custom`); }
function modelValue(role, provider) {
  const select = modelPicker(role, provider);
  return (select.value === '__custom__' ? modelCustom(role, provider).value : select.value).trim();
}
function providerModels(provider) {
  return state.status?.model_catalogs?.[provider]?.models || analysisProvider(provider)?.models || (provider === 'gemini' ? state.status?.analysis_models : []) || [];
}
function modelPickerChanged(role, provider) {
  const select = modelPicker(role, provider);
  const custom = modelCustom(role, provider);
  custom.hidden = select.value !== '__custom__';
  custom.required = !custom.hidden;
  const entry = providerModels(provider).find(item => item.id === modelValue(role, provider));
  const details = $(`#${select.id}-details`);
  const parts = [];
  if (entry?.input_usd_per_million != null && entry?.output_usd_per_million != null) {
    parts.push(`Standard text: $${entry.input_usd_per_million} input / $${entry.output_usd_per_million} output per million tokens`);
  } else if (entry) parts.push('Price estimate unavailable');
  if (entry?.price_date) parts.push(`prices checked ${entry.price_date}${entry.price_valid_until ? `; valid through ${entry.price_valid_until}` : ''}`);
  if (!entry || entry.structured_output == null) parts.push('Structured output support is unverified');
  if (entry?.availability === 'not_listed') parts.push('Not in the latest account list');
  details.textContent = parts.join(' · ');
  if (role === 'analysis') renderAccountCheck(provider);
}
function fillProviderModels(provider, selected) {
  const models = providerModels(provider).map(item => typeof item === 'string' ? {id:item,label:item} : item);
  for (const role of ['analysis','preprocess']) {
    const select = modelPicker(role, provider);
    const value = selected[role];
    const compatible = models.filter(item => !item.roles || item.roles.includes(role));
    const known = compatible.filter(item => item.structured_output === true);
    const unverified = compatible.filter(item => item.structured_output !== true);
    const option = item => `<option value="${escapeHTML(item.id)}">${escapeHTML(item.label || item.id)}${item.tier === 'economy' ? ' · economy' : item.tier === 'deep' ? ' · deep analysis' : ''}${item.availability === 'listed' ? ' · listed' : ''}</option>`;
    select.innerHTML = `${known.length ? `<optgroup label="Documented structured output models">${known.map(option).join('')}</optgroup>` : ''}${unverified.length ? `<optgroup label="Other models · compatibility unverified">${unverified.map(option).join('')}</optgroup>` : ''}<option value="__custom__">Custom model ID…</option>`;
    select.value = compatible.some(item => item.id === value) ? value : '__custom__';
    modelCustom(role, provider).value = select.value === '__custom__' ? value : '';
    modelPickerChanged(role, provider);
  }
  const inventory = state.status?.model_catalogs?.[provider];
  $(`#model-catalog-${provider}`).textContent = inventory?.message || 'Documented choices; refresh to list models visible to this key.';
}
async function refreshModels(provider) {
  if (state.settingsBusy) return;
  const selected = {analysis:modelValue('analysis', provider),preprocess:modelValue('preprocess', provider)};
  const button = $(`[data-refresh-models="${provider}"]`);
  state.settingsBusy = true;
  $('#settings-error').hidden = true;
  button.textContent = 'Refreshing…';
  updateSettingsControls();
  try {
    const key = providerField('api-key', provider).value.trim();
    if (key) {
      state.status = await post('/api/settings', {api_keys:{[provider]:key}});
      providerField('api-key', provider).value = '';
      updateStatusUI({syncSettings:false});
    }
    const result = await post(`/api/models/${encodeURIComponent(provider)}/refresh`);
    state.status.model_catalogs = {...state.status.model_catalogs,[provider]:result};
    fillProviderModels(provider, selected);
  } catch (error) { showInlineError('#settings-error', error.message); }
  finally { state.settingsBusy = false; button.textContent = 'Refresh models'; updateSettingsControls(); }
}
function clearKeyInputs() { cloudProviders.forEach(provider => { providerField('api-key', provider).value = ''; }); }
const accountCheckLabels = {unchecked:'Not checked', checking:'Checking…', ready:'Request succeeded', missing_key:'No key configured', billing_blocked:'Billing blocked', rate_limited:'Rate limited', invalid_key:'Key rejected', access_denied:'Access denied', model_unavailable:'Model unavailable', network_error:'Connection failed', provider_error:'Provider error', inconclusive:'Could not confirm access'};
function dashboardLink(url, label) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === 'https:') return `<a href="${escapeHTML(parsed.href)}" target="_blank" rel="noopener noreferrer">${escapeHTML(label)} ↗</a>`;
  } catch { /* Omit missing or invalid dashboard links. */ }
  return escapeHTML(label);
}
function renderAccountCheck(provider) {
  const node = $(`#account-check-${provider}`);
  const result = state.status?.account_checks?.[provider] || {};
  const pending = state.accountChecksPending.has(provider);
  const status = pending ? 'checking' : (result.state || 'unchecked');
  const summary = $(`#account-summary-${provider}`);
  summary.dataset.state = status;
  $('span', summary).textContent = accountCheckLabels[status] || 'Could not confirm access';
  const model = result.model || state.status?.analysis_models_by_provider?.[provider] || '';
  const changed = !pending && Boolean(providerField('api-key', provider).value.trim() || modelValue('analysis', provider) !== model);
  const message = pending ? 'Sending one small text request to the selected analysis model…' : result.message || 'Run a check to see whether this model can respond.';
  const date = result.checked_at ? new Date(result.checked_at) : null;
  const checkedAt = date && Number.isFinite(date.getTime()) ? date.toLocaleString(undefined, {month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit'}) : '';
  const usage = pending ? null : result.usage;
  const tokenCounts = [['input_tokens','input'],['output_tokens','output'],['total_tokens','total']].flatMap(([key,label]) => Number.isFinite(usage?.[key]) && usage[key] >= 0 ? [`${Number(usage[key]).toLocaleString()} ${label}`] : []);
  node.dataset.state = status;
  node.innerHTML = `<strong class="account-check-state">${escapeHTML(accountCheckLabels[status] || 'Could not confirm access')}</strong><p class="account-check-message">${escapeHTML(message)}</p>${model ? `<p class="account-check-meta">${checkedAt ? 'Checked model' : 'Analysis model'}: <code>${escapeHTML(model)}</code>${checkedAt && !pending ? ` · ${escapeHTML(checkedAt)}${result.cached ? ' · Recent result' : ''}` : ''}</p>` : ''}${tokenCounts.length ? `<p class="account-check-usage">This check only: ${escapeHTML(tokenCounts.join(' · '))} tokens.</p>` : ''}${changed ? '<p class="account-check-changed">The key or model has unsaved changes. Check again to test them.</p>' : ''}<div class="account-check-links"><span>Balance: ${dashboardLink(result.billing_url, 'See billing dashboard')}</span>${result.usage_url ? dashboardLink(result.usage_url, 'Account usage') : ''}</div><p class="account-check-balance">${escapeHTML(result.balance_note || 'Exact balance is not available through this check.')}</p>`;
}
function updateSettingsControls() {
  $$('#settings-form input, #settings-form select, #settings-form button:not([data-close])').forEach(control => { control.disabled = state.settingsBusy; });
  cloudProviders.forEach(provider => {
    $(`[data-clear-key="${provider}"]`).disabled = state.settingsBusy || !providerHasKey(provider);
    $(`[data-check-account="${provider}"]`).textContent = state.accountChecksPending.has(provider) ? 'Checking…' : 'Check API';
  });
  $('#check-all-accounts').textContent = state.accountChecksPending.size ? 'Checking accounts…' : 'Check all accounts';
}
async function checkAccounts(providers) {
  if (state.settingsBusy) return;
  const values = {analysis_models_by_provider:{},api_keys:{}};
  for (const provider of providers) {
    const input = providerField('analysis-model', provider);
    if (!modelValue('analysis', provider)) { showInlineError('#settings-error', `Enter an analysis model for ${analysisLabels[provider]} before checking its API.`); input.focus(); return; }
    values.analysis_models_by_provider[provider] = modelValue('analysis', provider);
    const key = providerField('api-key', provider).value.trim();
    if (key) values.api_keys[provider] = key;
  }
  state.settingsBusy = true;
  state.accountChecksPending = new Set(providers);
  $('#settings-error').hidden = true;
  updateSettingsControls();
  providers.forEach(renderAccountCheck);
  try {
    state.status = await post('/api/settings', values);
    providers.forEach(provider => { providerField('api-key', provider).value = ''; });
    updateStatusUI({syncSettings:false});
    await Promise.all(providers.map(async provider => {
      try {
        const result = await post(`/api/account-checks/${encodeURIComponent(provider)}`);
        state.status.account_checks = {...state.status.account_checks, [provider]:result};
      } catch (error) {
        state.status.account_checks = {...state.status.account_checks, [provider]:{...state.status.account_checks?.[provider], model:values.analysis_models_by_provider[provider], state:'inconclusive', message:error.message, checked_at:null, usage:null, cached:false}};
      } finally {
        state.accountChecksPending.delete(provider);
        renderAccountCheck(provider);
        updateSettingsControls();
      }
    }));
  } catch (error) { showInlineError('#settings-error', error.message); }
  finally {
    state.settingsBusy = false;
    state.accountChecksPending.clear();
    updateSettingsControls();
    cloudProviders.forEach(renderAccountCheck);
  }
}
function saveProgress() {
  if (!state.book) return;
  const time = state.audioSegmentId === state.segmentId && Number.isFinite(audio.currentTime) ? passageTime() : state.pendingOffset || 0;
  safeWrite(progressKey(), {chapterId:state.chapterId, segmentId:state.segmentId, currentTime:time});
  safeWrite('bardic:lastBook', state.book.id);
}
function stopAudio({clear = false} = {}) {
  window.BardicVoicePreview?.stop();
  finishVoicePreview();
  playGeneration++; preparingListen = false; mediaBuffering = false;
  window.BardicListen?.stop(state.book);
  clearListeningPreloads();
  audio.pause();
  if (clear) { audio.removeAttribute('src'); audio.load(); state.audioSegmentId = null; state.pendingOffset = 0; previewEnhanced = false; }
  updatePlayer();
}
function setPlaybackRate(rate) {
  if (![.75,1,1.25,1.5,1.75,2,2.25,2.5].includes(Number(rate))) return;
  audio.defaultPlaybackRate = audio.playbackRate = Number(rate);
  $('#playback-speed').value = String(rate);
  safeWrite('bardic:speed', audio.playbackRate);
  updateListeningBuffer(); renderReader(); updatePlayer();
}
function beginVoicePreview() {
  const offset = state.voicePreview?.offset ?? (state.audioSegmentId === state.segmentId ? passageTime() : state.pendingOffset || 0);
  saveProgress();
  stopAudio({clear:true});
  state.pendingOffset = offset;
  state.voicePreview = {bookId:state.book.id, offset, audio:null};
  renderReader(); updatePlayer();
}
function finishVoicePreview() {
  const preview = state.voicePreview;
  if (!preview) return;
  state.voicePreview = null;
  playGeneration++;
  audio.pause(); audio.removeAttribute('src'); audio.load();
  state.audioSegmentId = null;
  if (state.book?.id === preview.bookId) state.pendingOffset = preview.offset;
  $('#voice-preview-panel').hidden = true;
  renderReader(); updatePlayer(); saveProgress();
}
async function playVoicePreview(take, preview) {
  const current = state.voicePreview;
  if (!current || current.bookId !== state.book?.id) return;
  current.audio = take; current.preview = preview;
  audio.src = take.url; audio.load();
  try { await audio.play(); }
  catch (error) {
    if (state.voicePreview === current && error.name !== 'AbortError') {
      reportPlaybackIssue('playback_play_rejected', {operation:'play'});
      toast('The voice example could not start. Press Play example to try again.', true);
    }
  }
  if (state.voicePreview === current) updatePlayer();
}
function startVoicePreview(config, label) {
  if (!state.book) return;
  void window.BardicVoicePreview?.start(state.book, config, label);
}
function auditionCharacter(form, provider) {
  const character = characterById(form.dataset.characterForm);
  if (!character) return;
  const chosen = segmentById(state.segmentId);
  const passage = chosen?.speaker_id === character.id ? chosen :
    chapterSegments().find(item => item.speaker_id === character.id);
  startVoicePreview({provider, character_id:character.id,
    ...(passage ? {segment_id:passage.id} : {}),
    voice:form.elements[provider === 'system' ? 'system_voice' : 'voice'].value,
    model:provider === 'system' ? 'macos-say' : state.status.tts_model,
    direction:form.elements.direction.value}, `${character.name} · ${provider === 'system' ? 'Device' : 'Gemini'}`);
}
function auditionPassage(form) {
  const character = characterById(form.elements.speaker_id.value);
  if (!character) return;
  const provider = $('#render-provider').value;
  startVoicePreview({provider, character_id:character.id, segment_id:form.dataset.segmentForm,
    voice:provider === 'system' ? character.system_voice || '' : character.voice || 'Kore',
    model:provider === 'system' ? 'macos-say' : state.status.tts_model,
    segment_direction:form.elements.direction.value}, `${character.name} · Passage example`);
}
function setupVoicePreviews() {
  window.BardicVoicePreview?.configure({
    onStart:beginVoicePreview, onReady:playVoicePreview,
    beforeRequest:book => window.BardicListen?.waitForStopped?.(book),
    onState:() => updatePlayer(), onStop:finishVoicePreview,
  });
}
function clearListeningPreloads() {
  for (const media of listeningPreloads.values()) { media.removeAttribute('src'); media.load(); }
  listeningPreloads.clear();
}
function updateListeningBuffer() {
  if (!state.book || state.voicePreview || !simpleActive() || audio.paused || preparingListen) return;
  const segment = segmentById(state.segmentId);
  if (!segment) return;
  window.BardicListen?.updatePlayback?.(state.book, segment, {playbackRate:audio.playbackRate, currentTime:passageTime()});
  // Warm only the next two locally generated files. Fetching these URLs cannot
  // start synthesis and never crosses a chapter or narrator selection. Chunk
  // clips share one file, so look ahead for the next two different files.
  const segments = chapterSegments();
  const index = segments.findIndex(item => item.id === segment.id);
  const urls = new Set();
  for (const item of segments.slice(index + 1)) {
    const url = listeningAudio(item)?.url;
    if (!url) break;
    if (url !== audio.getAttribute('src')) urls.add(url);
    if (urls.size >= 2) break;
  }
  for (const [url, media] of listeningPreloads) {
    if (!urls.has(url)) { media.removeAttribute('src'); media.load(); listeningPreloads.delete(url); }
  }
  for (const url of urls) {
    if (!listeningPreloads.has(url)) {
      const media = new Audio(); media.preload = 'auto'; media.src = url;
      listeningPreloads.set(url, media); media.load();
    }
  }
}
// A chunk WAV holds consecutive passages; each passage's audio names its clip
// inside that file. Single-passage takes have no clip and start at zero.
function clipStart(media) { const value = Number(media?.clip_start); return Number.isFinite(value) && value > 0 ? value : 0; }
function clipEnd(media) { const value = Number(media?.clip_end); return Number.isFinite(value) && value > 0 ? value : null; }
function playingMedia() { return state.audioSegmentId ? listeningAudio(segmentById(state.audioSegmentId)) : null; }
function passageTime() {
  const media = playingMedia();
  const time = Number(audio.currentTime) || 0;
  return media && audio.getAttribute('src') === media.url ? Math.max(0, time - clipStart(media)) : time;
}
function passageDuration(segment) {
  const media = listeningAudio(segment);
  if (clipEnd(media) !== null) return clipEnd(media) - clipStart(media);
  return state.audioSegmentId === segment?.id && Number.isFinite(audio.duration) ? audio.duration : media?.duration || 0;
}
function followClip() {
  // Move the highlight across contiguous clips without reloading or pausing.
  if (state.voicePreview || previewEnhanced || !simpleActive() || audio.paused || preparingListen) return;
  let media = playingMedia(), end = clipEnd(media);
  if (end === null || state.audioSegmentId !== state.segmentId || (Number(audio.currentTime) || 0) < end - .02) return;
  const segments = orderedSegments();
  let moved = false;
  while (end !== null && (Number(audio.currentTime) || 0) >= end - .02) {
    const index = segments.findIndex(item => item.id === state.segmentId);
    const next = segments[index + 1];
    const nextMedia = next && window.BardicListen?.allowsAdvance(state.book, segments[index], next) ? listeningAudio(next) : null;
    if (!nextMedia || nextMedia.url !== audio.getAttribute('src') || Math.abs(clipStart(nextMedia) - end) > .05) {
      // The next passage is elsewhere; finish this clip like a file end.
      if (!Number.isFinite(audio.duration) || end < audio.duration - .05) { audio.pause(); void finishClip(); }
      break;
    }
    state.segmentId = next.id; state.audioSegmentId = next.id; moved = true;
    media = nextMedia; end = clipEnd(media);
  }
  if (moved) { updateHighlight({scroll:true}); saveProgress(); }
}
async function finishClip() {
  if (state.voicePreview) { window.BardicVoicePreview?.stop(); return; }
  if (preparingListen || !state.audioSegmentId || state.audioSegmentId !== state.segmentId) return;
  if (previewEnhanced) { stopAudio({clear:true}); renderReader(); saveProgress(); return; }
  const segments = orderedSegments();
  const index = segments.findIndex(segment => segment.id === state.segmentId);
  if (index >= 0 && index < segments.length - 1 && (!simpleActive() || window.BardicListen.allowsAdvance(state.book, segments[index], segments[index+1]))) {
    await moveSegment(1,true,true);
  } else {
    window.BardicListen?.stop(state.book); clearListeningPreloads();
    updatePlayer(); saveProgress();
    toast(simpleActive() ? 'Chapter complete. Choose the next chapter when you are ready.' : 'The end. A good place to linger.');
  }
}
function reportPlaybackIssue(event, details = {}) {
  const example = state.voicePreview ? window.BardicVoicePreview?.getState() : null;
  window.BardicDiagnostics?.record(event, {book_id:state.book?.id,
    segment_id:state.voicePreview ? state.voicePreview.preview?.segment_id : state.segmentId,
    ...(example?.jobId ? {job_id:example.jobId} : {}),
    playback_rate:audio.playbackRate,operation:'media',...details});
}
function renderLibrary() {
  $('#library-list').innerHTML = state.books.length ? state.books.map(book => `<button class="library-item ${state.book?.id === book.id ? 'active' : ''}" data-book="${escapeHTML(book.id)}" ${state.book?.id === book.id ? 'aria-current="true"' : ''}><span class="cover-thumb" aria-hidden="true">${book.cover?.url ? `<img src="${escapeHTML(book.cover.url)}" alt="" loading="lazy">` : escapeHTML((book.title || 'B').charAt(0))}</span><span><strong>${escapeHTML(book.title || 'Untitled')}</strong><small>${escapeHTML(book.author || 'Personal edition')}</small></span></button>`).join('') : '<p class="sidebar-hint">Your next great listen starts here.</p>';
}
async function refreshStatus({syncSettings = true} = {}) {
  state.status = await request('/api/status');
  updateStatusUI({syncSettings});
}
function updateStatusUI({syncSettings = true} = {}) {
  for (const provider of cloudProviders) {
    const connected = providerHasKey(provider);
    providerField('key-status', provider).textContent = connected ? 'Key configured' : 'No key';
    providerField('key-status', provider).classList.toggle('connected', connected);
  }
  if (syncSettings) fillSettings();
  cloudProviders.forEach(renderAccountCheck);
  updateSettingsControls();
  $('#analysis-provider').value = state.status.analysis_provider || 'local';
  if (state.book) renderProduction();
  const providers = state.status.providers || [];
  const system = providers.find(p => p.id === 'system');
  $('#render-provider option[value="system"]').textContent = system?.available === false ? 'Device voices · unavailable' : 'Device voices · local';
  if (system?.available === false && !$('#render-provider').dataset.chosen) $('#render-provider').value = 'gemini';
  updateProviderHint();
  updateAnalysisHint();
}
async function refreshLibrary() {
  const response = await request('/api/books');
  state.books = Array.isArray(response) ? response : response.books || [];
  renderLibrary();
}
async function selectBook(id) {
  saveProgress();
  stopAudio({clear:true});
  clearTimeout(state.poll);
  const version = ++state.selectionVersion;
  state.loading = true;
  try {
    const book = await request(`/api/books/${encodeURIComponent(id)}`);
    if (version !== state.selectionVersion) return;
    state.book = book;
    state.analysisSummary = null;
    state.analysisError = null;
    state.referenceCache.clear();
    state.referenceVersion++;
    const progress = safeRead(progressKey(), {});
    state.chapterId = book.chapters.some(c => c.id === progress.chapterId) ? progress.chapterId : book.chapters[0]?.id;
    state.segmentId = book.segments.some(s => s.id === progress.segmentId && s.chapter_id === state.chapterId) ? progress.segmentId : book.segments.find(s => s.chapter_id === state.chapterId)?.id;
    state.pendingOffset = Number(progress.currentTime) || 0;
    state.jobs = [];
    renderBook();
    saveProgress();
    await pollJobs(false);
  } catch (error) { toast(error.message, true); } finally { state.loading = false; }
}
function applyBook(book) {
  if (book.id !== state.book?.id) return;
  const previous = segmentById(state.segmentId);
  state.book = book;
  const next = segmentById(state.segmentId);
  if (!simpleActive() && state.audioSegmentId && (!playable(next) || (next?.audio?.asset_id || next?.audio?.fingerprint) !== (previous?.audio?.asset_id || previous?.audio?.fingerprint))) stopAudio({clear:true});
  if (!book.chapters.some(c => c.id === state.chapterId)) state.chapterId = book.chapters[0]?.id;
  if (!next) state.segmentId = chapterSegments()[0]?.id;
  const index = state.books.findIndex(b => b.id === book.id);
  if (index >= 0) state.books[index] = book;
  renderBook();
}
function renderBook() {
  const book = state.book;
  if (!book) { $('#welcome').hidden = false; $('#book-workspace').hidden = true; $('#player').hidden = true; return; }
  $('#welcome').hidden = true;
  $('#book-workspace').hidden = false;
  $('#player').hidden = false;
  $('#book-title').textContent = book.title || 'Untitled';
  document.title = `${book.title || 'Untitled'} — Bardic`;
  const narrativeCount = book.chapters.filter(c => c.kind === 'chapter').length;
  const otherCount = book.chapters.length - narrativeCount;
  const structureLabel = narrativeCount ? `${narrativeCount} chapters${otherCount ? ` · ${otherCount} other sections` : ''}` : `${book.chapters.length} sections`;
  $('#book-byline').textContent = [book.author, structureLabel].filter(Boolean).join('  ·  ');
  $('#cast-count').textContent = book.characters.filter(c => !['narrator','unassigned'].includes(c.id)).length;
  const ready = book.segments.filter(playable).length;
  $('#book-status').textContent = ready ? `${ready} of ${book.segments.length} passages narrated` : 'Ready to find its voice';
  $('#export-link').href = `/api/books/${encodeURIComponent(book.id)}/export`;
  renderLibrary();
  renderReader();
  renderCast();
  renderStudio();
  renderJob();
  setTab(state.tab);
  updatePlayer();
}
function renderReader() {
  window.BardicListen?.render($('#simple-listen'), state.book, {
    status:state.status, chapterId:state.chapterId, segmentId:state.segmentId, playbackRate:audio.playbackRate, busy:Boolean(busyJob()), busyKind:busyJob()?.kind,
    onSettings:status => { state.status = status; },
    playing:!audio.paused && !state.voicePreview, preparing:preparingListen, previewing:Boolean(state.voicePreview),
    onChange:() => { renderReader(); updatePlayer(); },
    onStop:() => { stopAudio({clear:true}); previewEnhanced = false; },
    onPlay:id => startSegment(id || state.segmentId, {autoplay:true}),
    onToggle:() => { if (state.voicePreview) window.BardicVoicePreview?.stop(); return togglePlayback(); },
    onRateChange:setPlaybackRate,
    beforeChapterPrepare:() => window.BardicVoicePreview?.waitForStopped?.(),
    onPreview:config => startVoicePreview(config, `${config.voice || 'Default device voice'} · Narrator example`),
    onJob:job => {
      if (!job || job.book_id !== state.book?.id) return;
      const index = state.jobs.findIndex(j => j.id === job.id);
      if (index < 0) state.jobs.unshift(job); else state.jobs[index] = job;
      renderJob();
      // The component stops polling when playback intent is cancelled. Keep a
      // single lightweight status poll alive until all known jobs settle,
      // including an older cancelled request left in the banner.
      if (busyJob() && !state.poll && !state.jobPollToken) {
        state.poll = setTimeout(() => pollJobs(false, {jobsOnly:true}), 1600);
      }
    },
  });
  const chapter = currentChapter();
  if (!chapter) return;
  const chapterIndex = state.book.chapters.findIndex(c => c.id === chapter.id);
  const narrativeCount = state.book.chapters.filter(c => c.kind === 'chapter').length;
  $('#chapter-counter').textContent = chapter.kind === 'chapter' && chapter.narrative_order ? `CHAPTER ${chapter.narrative_order} OF ${narrativeCount}` : `${(chapter.kind || 'section').replaceAll('_',' ').toUpperCase()} · SECTION ${chapterIndex + 1} OF ${state.book.chapters.length}`;
  $('#chapter-title').textContent = chapter.title;
  const segments = chapterSegments();
  const sourceHasHeading = segments[0]?.text.trim() === chapter.title.trim();
  $('#chapter-title').hidden = sourceHasHeading;
  $('.chapter-ornament').hidden = sourceHasHeading;
  let cursor = 0;
  let content = '';
  const marks = window.BardicListen?.chapterMarks?.(state.book, chapter.id) || new Map();
  for (const segment of segments) {
    // Match stored source text, not code-point offsets: JavaScript uses UTF-16 indices.
    const match = chapter.text.indexOf(segment.text, cursor);
    const leading = segment.leading_text ?? (match >= cursor ? chapter.text.substring(cursor, match) : '');
    content += escapeHTML(leading);
    const mark = marks.get(segment.id);
    const markClass = !mark ? '' : mark.status === 'ready' ? `listen-ready listen-chunk-${mark.chunk}${mark.start ? ' listen-chunk-start' : ''}` : `listen-${mark.status}`;
    content += `<span class="passage ${sourceHasHeading && segment === segments[0] ? 'source-chapter-title' : ''} ${listeningReady(segment) ? 'rendered' : 'unrendered'} ${markClass} ${state.segmentId === segment.id ? 'active' : ''}" data-segment="${escapeHTML(segment.id)}" tabindex="0" role="button" aria-label="${escapeHTML(`${listeningReady(segment) ? 'Play' : 'Select'} passage, ${characterById(segment.speaker_id)?.name || 'Narrator'}: ${segment.text}`)}" ${state.segmentId === segment.id ? 'aria-current="true"' : ''}>${escapeHTML(segment.text)}</span>`;
    if (match >= cursor) cursor = match + segment.text.length;
  }
  content += escapeHTML(chapter.trailing_text ?? chapter.text.substring(cursor));
  $('#reader-text').innerHTML = content || escapeHTML(chapter.text);
  $('#chapter-list').innerHTML = state.book.chapters.map((item,index) => `<button class="chapter-link ${item.id === state.chapterId ? 'active' : ''}" data-chapter="${escapeHTML(item.id)}" ${item.id === state.chapterId ? 'aria-current="true"' : ''}><span class="chapter-num">${item.narrative_order ? String(item.narrative_order).padStart(2,'0') : '·'}</span><span>${escapeHTML(item.title)}</span></button>`).join('');
  $('#previous-chapter').disabled = chapterIndex === 0;
  $('#next-chapter').disabled = chapterIndex === state.book.chapters.length - 1;
  const ready = segments.filter(listeningReady).length;
  const chunkedListening = simpleActive() && window.BardicListen?.getSelection?.(state.book)?.provider === 'gemini';
  $('#reader-hint').textContent = chunkedListening ? 'Press play to start or join the chapter queue. Gemini prepares large chunks at your request limits; playback begins when your passage is ready. Underlines show ready, generating and queued text; timing inside a chunk is estimated.' : simpleActive() ? 'Press play to warm up a short buffer, then listen while the next passages prepare. For faster listening, prepare the rest of the chapter first. Playback stops at the chapter boundary; highlighting follows each passage.' : ready ? `${ready} of ${segments.length} passages in this chapter are ready. Tap a passage to listen. Highlighting follows each complete passage.` : 'Create a narration in the studio, then press play to follow each passage.';
  renderPassageDetail();
}
function renderPassageDetail() {
  const segment = segmentById(state.segmentId);
  const panel = $('#passage-detail');
  panel.hidden = !segment;
  if (!segment) return;
  const character = simpleActive() ? {name:'Simple narrator', direction:'One voice, with passage highlighting. Enhanced performance settings are preserved.'} : characterById(segment.speaker_id);
  panel.innerHTML = `<span class="eyebrow">CURRENT PASSAGE</span><div class="passage-speaker">${escapeHTML(character?.name || 'Unassigned')}</div><p class="passage-note">${escapeHTML((!simpleActive() && segment.direction) || character?.direction || 'A natural, unhurried reading.')}</p><span class="passage-state ${listeningReady(segment) ? '' : 'missing'}">${listeningReady(segment) ? `${formatTime(listeningAudio(segment)?.duration)} · ${escapeHTML(listeningAudio(segment)?.provider || 'Audio ready')}` : segment.audio ? 'Take needs regeneration' : 'Not narrated yet'}</span>`;
}
function systemVoiceOptions(selected) {
  const voices = state.status?.system_voices || [];
  let html = '<option value="">Device default</option>';
  if (selected && !voices.some(v => v.id === selected || v.name === selected)) html += `<option selected value="${escapeHTML(selected)}">${escapeHTML(selected)}</option>`;
  return html + voices.map(voice => { const id = voice.id || voice.name; return `<option value="${escapeHTML(id)}" ${id === selected || voice.name === selected ? 'selected' : ''}>${escapeHTML(voice.name || id)}${voice.locale ? ` · ${escapeHTML(voice.locale)}` : ''}</option>`; }).join('');
}
function referenceContent(character) {
  const entry = state.referenceCache.get(character.id);
  if (!entry || entry.loading) return '<p class="field-help">Loading source references…</p>';
  if (entry.error) return `<p class="field-help">${escapeHTML(entry.error)}</p><button type="button" class="button text-button" data-retry-references="${escapeHTML(character.id)}">Try again</button>`;
  if (!entry.references.length) {
    const evidence = character.evidence || [];
    return `<p class="field-help">No chapter references saved yet. Analyze a chapter to collect appearances and source evidence.</p>${evidence.length ? `<p class="field-help">Earlier profile evidence:</p>${evidence.map(item => `<blockquote>${escapeHTML(typeof item === 'string' ? item : item.quote || item.text || '')}</blockquote>`).join('')}` : ''}`;
  }
  const shown = Math.min(entry.references.length, entry.shown || 100);
  const chapters = new Map(state.book.chapters.map(chapter => [chapter.id, chapter]));
  const segments = new Map();
  const byChapter = new Map();
  for (const segment of state.book.segments) {
    const chapterItems = byChapter.get(segment.chapter_id) || [];
    segments.set(segment.id, {segment, index:chapterItems.length});
    chapterItems.push(segment);
    byChapter.set(segment.chapter_id, chapterItems);
  }
  return `<p class="field-help">Name mentions may describe someone who is not present in the scene. Profile observations record what analysis inferred from each quoted passage.</p><ol class="character-reference-list">${entry.references.slice(0, shown).map(reference => {
    const chapter = chapters.get(reference.chapter_id);
    const anchored = segments.get(reference.segment_id);
    const chapterItems = byChapter.get(reference.chapter_id) || [];
    const segment = anchored?.segment || chapterItems.find(item => Number.isFinite(reference.start) && item.start <= reference.start && item.end > reference.start);
    const passageIndex = anchored?.index ?? (segment ? chapterItems.indexOf(segment) : -1);
    const label = `${chapter?.title || 'Chapter'}${passageIndex >= 0 ? ` · Passage ${passageIndex + 1}` : ''}`;
    const meta = [reference.kind ? String(reference.kind).replaceAll('_',' ') : '', typeof reference.confidence === 'number' ? `${Math.round(reference.confidence * 100)}% confidence` : ''].filter(Boolean).join(' · ');
    const observation = reference.kind === 'profile_evidence' && reference.profile_description ? `<p class="reference-meta"><strong>Observation:</strong> ${escapeHTML(reference.profile_description)}</p>` : '';
    const direction = reference.kind === 'profile_evidence' && reference.profile_direction ? `<p class="reference-meta"><strong>Direction noted:</strong> ${escapeHTML(reference.profile_direction)}</p>` : '';
    return `<li><blockquote>${escapeHTML(reference.quote)}</blockquote>${chapter ? `<button type="button" class="reference-link" data-reference-chapter="${escapeHTML(chapter.id)}" data-reference-segment="${escapeHTML(segment?.id || '')}">${escapeHTML(label)} ↗</button>` : ''}${meta ? `<span class="reference-meta">${escapeHTML(meta)}</span>` : ''}${observation}${direction}</li>`;
  }).join('')}</ol><p class="field-help" role="status">Showing ${shown} of ${entry.references.length} references.</p>${shown < entry.references.length ? `<button type="button" class="button text-button" data-more-references="${escapeHTML(character.id)}">Show ${Math.min(100, entry.references.length - shown)} more</button>` : ''}`;
}
function renderCharacterReferences(character, open = false) {
  const entry = state.referenceCache.get(character.id);
  const count = entry?.references?.length;
  return `<details class="cast-evidence" data-character-references="${escapeHTML(character.id)}" ${open ? 'open' : ''}><summary>References &amp; appearances${Number.isInteger(count) ? ` · ${count}` : ''}</summary><div class="character-references-body">${open ? referenceContent(character) : ''}</div></details>`;
}
function updateCharacterReferences(characterId) {
  const character = characterById(characterId);
  const details = $$('[data-character-references]').find(node => node.dataset.characterReferences === characterId);
  if (!character || !details) return;
  const count = state.referenceCache.get(characterId)?.references?.length;
  $('summary', details).textContent = `References & appearances${Number.isInteger(count) ? ` · ${count}` : ''}`;
  const body = $('.character-references-body', details);
  const scrollTop = $('.character-reference-list', body)?.scrollTop || 0;
  body.innerHTML = details.open ? referenceContent(character) : '';
  const list = $('.character-reference-list', body);
  if (list) list.scrollTop = scrollTop;
}
async function loadCharacterReferences(characterId, {retry = false} = {}) {
  const bookId = state.book?.id;
  if (!bookId || !characterById(characterId)) return;
  if (state.referenceCache.has(characterId) && !retry) { updateCharacterReferences(characterId); return; }
  const version = state.referenceVersion;
  state.referenceCache.set(characterId, {loading:true});
  updateCharacterReferences(characterId);
  try {
    const response = await request(`/api/books/${encodeURIComponent(bookId)}/characters/${encodeURIComponent(characterId)}/references`);
    if (state.book?.id !== bookId || version !== state.referenceVersion) return;
    state.referenceCache.set(characterId, {references:Array.isArray(response) ? response : response.references || [], shown:100});
  } catch (error) {
    if (state.book?.id !== bookId || version !== state.referenceVersion) return;
    state.referenceCache.set(characterId, {error:`Could not load references: ${error.message}`});
  }
  updateCharacterReferences(characterId);
}
function renderCast() {
  window.BardicSeries?.render($("#series-panel"), state.book);
  const openReferences = new Set($$('[data-character-references][open]').map(node => node.dataset.characterReferences));
  const book = state.book;
  const notes = book.analysis?.notes;
  const noteText = Array.isArray(notes) ? notes.join(' ') : notes || '';
  const provider = book.analysis?.provider || 'local';
  const attribution = provider === 'local' ? 'Local draft' : `${analysisLabels[provider] || provider} analysis${book.analysis?.model ? ` · ${book.analysis.model}` : ''}`;
  $('#analysis-note').textContent = `${attribution} · ${book.analysis?.status === 'reviewed' ? 'Edited by you.' : 'Review the cast and speaker assignments before narration.'}${noteText ? ` ${noteText}` : ''}`;
  $('#cast-grid').innerHTML = book.characters.map(character => `<form class="cast-card" data-character-form="${escapeHTML(character.id)}"><div class="cast-card-top"><div class="character-avatar" aria-hidden="true">${escapeHTML((character.name || '?').charAt(0))}</div><div><h3>${escapeHTML(character.name)}</h3><div class="cast-role">${character.id === 'narrator' ? 'THE STORYTELLER' : character.id === 'unassigned' ? 'DIALOGUE TO REVIEW' : 'CHARACTER VOICE'}</div></div></div><label class="field-label" for="description-${escapeHTML(character.id)}">Character &amp; vocal profile</label><textarea id="description-${escapeHTML(character.id)}" name="description" maxlength="3000" rows="3" placeholder="What the text tells us about this voice…">${escapeHTML(character.description || '')}</textarea><div class="voice-fields"><div><label class="field-label" for="voice-${escapeHTML(character.id)}">Gemini voice</label><input id="voice-${escapeHTML(character.id)}" name="voice" list="gemini-voices" value="${escapeHTML(character.voice || 'Kore')}" placeholder="Kore"><button type="button" class="button subtle voice-example" data-preview-character="gemini" aria-label="Hear ${escapeHTML(character.name)} with Gemini voice">Hear example</button></div><div><label class="field-label" for="system-${escapeHTML(character.id)}">Device voice</label><select id="system-${escapeHTML(character.id)}" name="system_voice">${systemVoiceOptions(character.system_voice)}</select><button type="button" class="button subtle voice-example" data-preview-character="system" aria-label="Hear ${escapeHTML(character.name)} with device voice">Hear example</button></div></div><p class="voice-example-note">Examples use this character’s text, or demo text if none is assigned. Unsaved voice and direction are included. Gemini examples may incur charges.</p><label class="field-label" for="direction-${escapeHTML(character.id)}">Performance direction</label><textarea id="direction-${escapeHTML(character.id)}" name="direction" maxlength="3000" rows="2" placeholder="Warm, measured, with a dry sense of humor…">${escapeHTML(character.direction || '')}</textarea>${renderCharacterReferences(character, openReferences.has(character.id))}<div class="card-footer"><span class="save-state">${character.aliases?.length ? `Also: ${escapeHTML(character.aliases.join(', '))}` : 'Changes affect future takes'}</span><button type="submit" class="button subtle">Save voice ${icon('check')}</button></div></form>`).join('') + `<form class="cast-card new-character" id="add-character-form"><div class="cast-card-top"><div class="character-avatar">${icon('plus')}</div><div><h3>A missing voice?</h3><div class="cast-role">ADD TO THE CAST</div></div></div><p class="field-help">Add a character, then assign their dialogue in the production script.</p><label class="field-label" for="new-character-name">Character name</label><input id="new-character-name" name="name" required maxlength="100" placeholder="A name from your story"><div class="card-footer"><span></span><button class="button subtle" type="submit">Add character ${icon('plus')}</button></div></form>`;
}
function speakerOptions(selected) {
  return state.book.characters.map(character => `<option value="${escapeHTML(character.id)}" ${character.id === selected ? 'selected' : ''}>${escapeHTML(character.name)}</option>`).join('');
}
function renderAnalysisProgress() {
  if (!state.book) return;
  $('#analysis-scope-note').textContent = $('#analysis-scope').value === 'chapter' ? currentChapter()?.title || '' : `${state.book.chapters.length} chapters, processed one at a time`;
  const panel = $('#analysis-progress');
  const open = Boolean($('details', panel)?.open);
  const focusedChapter = document.activeElement?.dataset.analysisChapter;
  const focusedSummary = document.activeElement === $('summary', panel);
  const summary = state.analysisSummary;
  const stages = {discovery:'Discovering characters', profiles:'Building character profiles', directing:'Directing scenes', complete:'Analysis run complete'};
  const statusLabels = {pending:'Pending', queued:'Queued', running:'In progress', completed:'Complete', failed:'Stopped', interrupted:'Interrupted', cancelled:'Cancelled', budget_limited:'Allowance reached', not_started:'Not started'};
  const chapters = summary?.chapters || [];
  const current = state.book.chapters.find(chapter => chapter.id === summary?.current_chapter_id);
  const complete = chapters.filter(chapter => chapter.directing_complete || chapter.status === 'completed' && chapter.stage === 'complete').length;
  const completedUnits = Math.max(0, Number(summary?.completed_units) || 0);
  const totalUnits = Math.max(completedUnits, Number(summary?.total_units) || 0);
  const stopped = ['failed','interrupted','cancelled','budget_limited'].includes(summary?.status);
  const idle = !summary || summary.status === 'not_started';
  const title = idle ? 'Analysis, chapter by chapter' : `${stopped ? `${statusLabels[summary.status]} · ` : ''}${stages[summary.stage] || statusLabels[summary.status] || 'Chapter analysis'}${current ? ` · ${current.title}` : ''}`;
  const detail = idle ? 'Discover the cast, build profiles from source references, then direct each scene. Completed steps are saved so you can resume.' : `${complete} of ${state.book.chapters.length} source sections directed${totalUnits ? ` · ${completedUnits} of ${totalUnits} steps saved` : ''}${summary.provider ? ` · ${analysisLabels[summary.provider] || summary.provider}` : ''}`;
  const content = `<div class="analysis-progress-header" role="status" aria-live="polite"><div><h3>${escapeHTML(title)}</h3><p>${escapeHTML(detail)}</p></div>${totalUnits ? `<progress value="${completedUnits}" max="${totalUnits}" aria-label="Saved analysis steps"></progress>` : ''}</div>${state.analysisError || summary?.error ? `<p class="analysis-progress-error">${escapeHTML(state.analysisError || summary.error)}</p>` : ''}${chapters.length ? `<details ${open ? 'open' : ''}><summary>Section progress · ${chapters.length}</summary><ol class="analysis-chapter-list">${chapters.map(chapter => {
    const discoveryDone = chapter.discovery_complete ?? (chapter.stage === 'directing' || chapter.stage === 'complete');
    const directingDone = chapter.directing_complete ?? (chapter.stage === 'complete' && chapter.status === 'completed');
    const status = statusLabels[chapter.status] || chapter.status || 'Pending';
    return `<li data-analysis-status="${escapeHTML(chapter.status || 'pending')}"><button type="button" data-analysis-chapter="${escapeHTML(chapter.id)}">${escapeHTML(chapter.title || state.book.chapters.find(item => item.id === chapter.id)?.title || 'Chapter')}</button><span class="chapter-stage ${discoveryDone ? 'complete' : ''}">${discoveryDone ? '✓' : '○'} Discovery</span><span class="chapter-stage ${directingDone ? 'complete' : ''}">${directingDone ? '✓' : '○'} Direction</span><span class="chapter-analysis-status">${escapeHTML(status)}${chapter.status === 'running' ? ` · ${escapeHTML(stages[chapter.stage] || chapter.stage || '')}` : ''}</span>${chapter.error ? `<p class="chapter-analysis-error">${escapeHTML(chapter.error)}</p>` : ''}</li>`;
  }).join('')}</ol></details>` : ''}`;
  if (panel.innerHTML !== content) {
    panel.innerHTML = content;
    if (focusedChapter) $$('[data-analysis-chapter]', panel).find(node => node.dataset.analysisChapter === focusedChapter)?.focus({preventScroll:true});
    else if (focusedSummary) $('summary', panel)?.focus({preventScroll:true});
  }
}
function renderProduction() {
  window.BardicResources?.render($('#resource-usage'), state.book, {busy:Boolean(busyJob())});
  window.BardicPipeline?.render($('#pipeline-inspector'), state.book, {busy: Boolean(busyJob())});
  window.BardicProduction?.render($('#progressive-production'), state.book, {
    provider: $('#analysis-provider').value, chapterId: state.chapterId, busy: Boolean(busyJob()),
    scanModel: state.status?.preprocess_models_by_provider?.[$('#analysis-provider').value],
    model: state.status?.analysis_models_by_provider?.[$('#analysis-provider').value],
    onStart: async payload => {
      const started = await startJob('analyze', payload);
      if (!started) throw new Error('Processing did not start. Check the provider settings or job error, then preview again.');
    },
    onRefresh: async () => { await pollJobs(true); },
  });
}
function renderStudio() {
  const segments = chapterSegments();
  $('#studio-chapter').innerHTML = state.book.chapters.map(chapter => `<option value="${escapeHTML(chapter.id)}" ${chapter.id === state.chapterId ? 'selected' : ''}>${escapeHTML(chapter.title)}</option>`).join('');
  const scenes = state.book.scenes.filter(scene => scene.chapter_id === state.chapterId);
  $('#script-meta').textContent = `${scenes.length} ${scenes.length === 1 ? 'scene' : 'scenes'} · ${segments.length} passages`;
  $('#scene-list').innerHTML = scenes.map((scene, index) => {
    const items = segments.filter(s => s.scene_id === scene.id || scene.segment_ids?.includes(s.id));
    return `<section class="scene-card"><div class="scene-header"><div><span class="eyebrow">SCENE ${String(index + 1).padStart(2,'0')}${scene.tone ? ` · ${escapeHTML(scene.tone)}` : ''}</span><h3>${escapeHTML(scene.title || `Scene ${index + 1}`)}</h3>${scene.summary ? `<p>${escapeHTML(scene.summary)}</p>` : ''}</div><button class="button subtle render-action" data-render-scene="${escapeHTML(scene.id)}">${icon('play')} Narrate scene</button></div><form class="scene-direction" data-scene-form="${escapeHTML(scene.id)}"><div><label class="field-label" for="scene-direction-${escapeHTML(scene.id)}">SCENE DIRECTION</label><textarea id="scene-direction-${escapeHTML(scene.id)}" name="direction" maxlength="3000" rows="1" placeholder="The emotional setting, pacing, and subtext…">${escapeHTML(scene.direction || '')}</textarea></div><button class="button subtle" type="submit">Save</button></form><div class="scene-passages">${items.map((segment, segmentIndex) => `<form class="segment-row" data-segment-form="${escapeHTML(segment.id)}"><span class="segment-number">${String(segmentIndex + 1).padStart(2,'0')}</span><div><p class="segment-text">${escapeHTML(segment.text)}</p><div class="segment-toolbar"><label class="sr-only" for="speaker-${escapeHTML(segment.id)}">Passage speaker</label><select id="speaker-${escapeHTML(segment.id)}" name="speaker_id">${speakerOptions(segment.speaker_id)}</select><button type="button" class="button subtle" data-preview-speaker="${escapeHTML(segment.id)}" aria-label="Hear selected speaker on this passage">Hear example</button><label class="sr-only" for="segment-direction-${escapeHTML(segment.id)}">Passage performance direction</label><input id="segment-direction-${escapeHTML(segment.id)}" name="direction" maxlength="3000" value="${escapeHTML(segment.direction || '')}" placeholder="Performance note…"><button class="button subtle" type="submit" aria-label="Save passage changes">Save</button><button class="button subtle render-action" type="button" data-render-segment="${escapeHTML(segment.id)}" title="${playable(segment) ? 'Generate this passage again' : 'Generate this passage'}">${icon('spark')}${playable(segment) ? 'Retake' : 'Narrate'}</button>${playable(segment) ? `<button class="button subtle" type="button" data-play-segment="${escapeHTML(segment.id)}" aria-label="Preview passage">${icon('play')}</button>` : ''}</div><div class="segment-meta"><span class="clip-status ${playable(segment) ? '' : 'missing'}">${playable(segment) ? `Ready · ${formatTime(segment.audio.duration)} · ${escapeHTML(segment.audio.provider || '')}` : segment.audio ? 'Out of date · regenerate take' : 'Awaiting narration'}</span>${typeof segment.confidence === 'number' && segment.kind === 'dialogue' ? `<span>Speaker confidence ${Math.round(segment.confidence * 100)}%</span>` : ''}${segment.cues?.length ? `<span>${escapeHTML(segment.cues.map(c => typeof c === 'string' ? c : c.text || JSON.stringify(c)).join(' · '))}</span>` : ''}</div></div></form>`).join('')}</div></section>`;
  }).join('') || '<div class="empty-state">No scenes here yet. Analyze the story to draft a performance script.</div>';
  renderAnalysisProgress();
  updateBusyControls();
  renderProduction();
}
function setTab(tab) {
  state.tab = ['read','cast','studio'].includes(tab) ? tab : 'read';
  $$('.tab').forEach(button => { button.classList.toggle('active', button.dataset.tab === state.tab); button.setAttribute('aria-current', button.dataset.tab === state.tab ? 'page' : 'false'); });
  ['read','cast','studio'].forEach(name => { $(`#${name}-view`).hidden = name !== state.tab; });
}
function setChapter(id, {scroll = true} = {}) {
  if (!state.book?.chapters.some(c => c.id === id)) return;
  saveProgress();
  stopAudio({clear:true});
  state.chapterId = id;
  state.segmentId = chapterSegments()[0]?.id;
  state.pendingOffset = 0;
  renderReader(); renderStudio(); updatePlayer(); saveProgress();
  if (scroll) window.scrollTo({top:0, behavior:'smooth'});
}
function updateHighlight({scroll = false} = {}) {
  $$('.passage').forEach(el => { const active = el.dataset.segment === state.segmentId; el.classList.toggle('active', active); if (active) el.setAttribute('aria-current','true'); else el.removeAttribute('aria-current'); });
  if (scroll && state.tab === 'read') {
    const active = $$('.passage').find(el => el.dataset.segment === state.segmentId);
    if (active) { const box = active.getBoundingClientRect(); if (box.top < 80 || box.bottom > window.innerHeight - 135) active.scrollIntoView({behavior:'smooth', block:'center'}); }
  }
  renderPassageDetail();
}
async function startSegment(id, {autoplay = true, offset = 0, scroll = false, enhanced = false, continuation = false} = {}) {
  const segment = segmentById(id);
  if (!segment) return;
  window.BardicVoicePreview?.stop();
  // Repeated passage clicks share the pending request. Selecting another
  // passage or a studio preview stops that request's playback intent.
  if (preparingListen && state.segmentId === id && !enhanced) return;
  if (!continuation || enhanced) { window.BardicListen?.stop(state.book); clearListeningPreloads(); }
  preparingListen = false; mediaBuffering = false;
  const playToken = ++playGeneration;
  const bookVersion = state.selectionVersion;
  const bookId = state.book.id;
  previewEnhanced = enhanced;
  audio.pause();
  state.segmentId = id;
  if (segment.chapter_id !== state.chapterId) { state.chapterId = segment.chapter_id; renderReader(); renderStudio(); }
  else renderReader();
  updateHighlight({scroll});
  let selectedAudio = listeningAudio(segment);
  if (simpleActive() && autoplay) {
    preparingListen = true; updatePlayer();
    try {
      const settled = await window.BardicVoicePreview?.waitForStopped?.();
      if (settled === false || playToken !== playGeneration || bookVersion !== state.selectionVersion || state.book?.id !== bookId) return;
      const listen = window.BardicListen;
      selectedAudio = await (listen.prepare ? listen.prepare(state.book, segment, {playbackRate:audio.playbackRate, offset, continuation}) : listen.ensure(state.book, segment));
    }
    catch (error) { selectedAudio = null; if (playToken === playGeneration) toast(error.message, true); }
    if (playToken !== playGeneration || bookVersion !== state.selectionVersion || state.book?.id !== bookId || state.segmentId !== id) return;
    preparingListen = false;
    if (!selectedAudio) { updatePlayer(); renderReader(); return; }
    renderReader();
  }
  if (!selectedAudio?.url) {
    const simple = simpleActive();
    stopAudio({clear:true});
    updatePlayer(); saveProgress();
    if (!simple) toast(segment.audio ? 'This take is out of date. Regenerate it in the studio.' : 'This passage is not narrated yet. Open the studio to give it a voice.');
    return;
  }
  if (audio.getAttribute('src') !== selectedAudio.url) {
    state.audioSegmentId = id;
    state.pendingOffset = offset;
    audio.src = selectedAudio.url;
    audio.load();
  } else {
    // Same file (a repeated take or another clip of this chunk): seek only.
    state.audioSegmentId = id;
    if (Number.isFinite(audio.duration)) audio.currentTime = Math.min(clipStart(selectedAudio) + offset, Math.max(0, audio.duration - .01));
    else state.pendingOffset = offset;
  }
  if (autoplay) {
    try { await audio.play(); }
    catch (error) {
      if (playToken === playGeneration) {
        window.BardicListen?.stop(state.book); clearListeningPreloads();
        if (error.name !== 'AbortError') {
          reportPlaybackIssue('playback_play_rejected', {operation:'play'});
          toast('Playback could not start. Press play to try again.', true);
        }
      }
    }
  }
  if (playToken !== playGeneration || bookVersion !== state.selectionVersion || state.book?.id !== bookId) return;
  updatePlayer(); saveProgress(); updateListeningBuffer();
}
async function togglePlayback() {
  if (!state.book) return;
  if (state.voicePreview) {
    if (!state.voicePreview.audio) { window.BardicVoicePreview?.stop(); return; }
    if (!audio.paused) audio.pause();
    else {
      const preview = state.voicePreview;
      try { await audio.play(); }
      catch (error) {
        if (state.voicePreview === preview && error.name !== 'AbortError') {
          reportPlaybackIssue('playback_play_rejected', {operation:'play'});
          toast('The voice example could not play. Try the example again.', true);
        }
      }
    }
    updatePlayer(); return;
  }
  if (preparingListen) { stopAudio({clear:true}); return; }
  if (!audio.paused) { stopAudio(); saveProgress(); return; }
  const segment = segmentById(state.segmentId) || chapterSegments()[0];
  if (!segment) return;
  const offset = state.audioSegmentId === segment.id && audio.getAttribute('src') === listeningAudio(segment)?.url
    ? passageTime() : state.pendingOffset;
  await startSegment(segment.id, {offset});
}
function orderedSegments() {
  return state.book ? state.book.chapters.flatMap(chapter => state.book.segments.filter(segment => segment.chapter_id === chapter.id)) : [];
}
async function moveSegment(delta, autoplay = !audio.paused, continuation = false) {
  const segments = orderedSegments();
  const index = segments.findIndex(s => s.id === state.segmentId);
  const next = segments[index + delta];
  if (next) await startSegment(next.id, {autoplay, scroll:true, continuation});
}
function updatePlayer() {
  if (!state.book) return;
  if (state.voicePreview) {
    const preview = window.BardicVoicePreview?.getState() || {};
    const loading = preview.status === 'loading';
    const sample = preview.preview || state.voicePreview.preview;
    const hasAudio = Boolean(state.voicePreview.audio);
    $('#voice-preview-panel').hidden = false;
    $('#voice-preview-title').textContent = preview.label || 'Voice example';
    $('#voice-preview-message').textContent = preview.error || (loading ? 'Preparing a short voice example…' : 'Your reading is paused. Close the example to return to your place.');
    $('#voice-preview-source').textContent = sample ? `${sample.source === 'demo' ? 'Demo text · no assigned passage' : 'From your book'}${sample.truncated ? ' · Short excerpt' : ''}` : 'One short example. Matching saved audio is reused.';
    $('#voice-preview-text').textContent = sample?.text || '';
    $('#player-title').textContent = preview.label || 'Voice example';
    $('#player-subtitle').textContent = 'Voice example · Shared playback speed';
    $('#play-button').innerHTML = icon(loading || !audio.paused ? 'pause' : 'play');
    $('#play-button').disabled = !loading && !hasAudio;
    $('#play-button').setAttribute('aria-label', loading ? 'Stop preparing example' : audio.paused ? 'Play example' : 'Pause example');
    $('#play-button').classList.toggle('is-playing', loading || !audio.paused);
    $('#previous-segment').disabled = true; $('#next-segment').disabled = true;
    const duration = hasAudio && Number.isFinite(audio.duration) ? audio.duration : state.voicePreview.audio?.duration || 0;
    $('#elapsed').textContent = formatTime(hasAudio ? audio.currentTime : 0);
    $('#duration').textContent = formatTime(duration);
    $('#audio-progress').max = duration || 1;
    $('#audio-progress').value = hasAudio ? Math.min(audio.currentTime || 0, duration) : 0;
    $('#audio-progress').disabled = !hasAudio || !duration;
    return;
  }
  $('#voice-preview-panel').hidden = true;
  $('#play-button').disabled = false;
  const segment = segmentById(state.segmentId);
  const chapter = currentChapter();
  const isPlaying = preparingListen || !audio.paused;
  $('#play-button').innerHTML = icon(isPlaying ? 'pause' : 'play');
  $('#play-button').classList.toggle('is-playing', isPlaying);
  $('#play-button').setAttribute('aria-label', preparingListen ? 'Stop preparing narration' : isPlaying ? 'Pause' : 'Play');
  $('#player-title').textContent = chapter?.title || state.book.title;
  const index = chapterSegments().findIndex(s => s.id === state.segmentId);
  const buffer = simpleActive() ? window.BardicListen?.getBuffer?.(state.book) : null;
  const bufferLabel = preparingListen || mediaBuffering ? ' · Buffering…' : buffer && !audio.paused ? ` · ${Math.floor(buffer.seconds || 0)}s buffered` : '';
  const narrator = window.BardicListen?.getSelection?.(state.book);
  const voiceLabel = simpleActive() ? `Simple · ${narrator?.voice || 'Default device voice'}` : `Cast · ${characterById(segment?.speaker_id)?.name || 'Narrator'}`;
  $('#player-subtitle').textContent = segment ? `${voiceLabel} · Passage ${index + 1}${bufferLabel || (listeningReady(segment) ? '' : ' · Not narrated')}` : 'Choose a passage to begin';
  const duration = passageDuration(segment);
  const elapsed = state.audioSegmentId === state.segmentId ? passageTime() : state.pendingOffset || 0;
  $('#elapsed').textContent = formatTime(elapsed);
  $('#duration').textContent = formatTime(duration);
  $('#audio-progress').max = duration || 1;
  $('#audio-progress').value = Math.min(elapsed, duration || 0);
  $('#audio-progress').disabled = !listeningReady(segment) || !duration;
  const ordered = orderedSegments();
  $('#previous-segment').disabled = ordered.findIndex(s => s.id === state.segmentId) <= 0;
  $('#next-segment').disabled = ordered.findIndex(s => s.id === state.segmentId) >= ordered.length - 1;
}
function updateProviderHint() {
  $('#render-description').textContent = $('#render-provider').value === 'gemini' ? 'Expressive cloud narration. Sends text to Google; usage may be billed.' : 'Private, on-device narration. Performance notes are saved for Gemini.';
}
function updateAnalysisHint() {
  const provider = $('#analysis-provider').value;
  const model = state.status?.analysis_models_by_provider?.[provider] || analysisProvider(provider)?.model;
  $('#analysis-description').textContent = provider === 'local' ? 'A private draft of the cast and scenes, made on this device.' : `Analyze the cast, scenes, and performance with ${analysisLabels[provider] || provider}. Book text is sent to this provider.`;
  $('#analysis-model-note').textContent = provider === 'local' ? 'Choose narration independently.' : `${model || 'Choose a model in settings'}${providerHasKey(provider) ? '' : ' · API key needed'}`;
}
function updateBusyControls(forceBusy = false) {
  const busy = forceBusy || Boolean(busyJob());
  $$('#render-button, #analyze-button, #analyze-from-cast, #analysis-provider, #analysis-scope, .render-action, #cast-grid input, #cast-grid textarea, #cast-grid select, #cast-grid button[type="submit"], #scene-list input, #scene-list textarea, #scene-list select, #scene-list button[type="submit"]').forEach(control => { control.disabled = busy; });
}
function renderJob() {
  const banner = $('#job-banner');
  const job = busyJob() || state.jobs[0];
  if (!job) { banner.hidden = true; updateBusyControls(); return; }
  const active = ['running','queued'].includes(job.status);
  banner.hidden = false;
  banner.classList.toggle('failed', ['failed','interrupted','budget_limited','quota_limited'].includes(job.status));
  const labels = {queued:'Queued',running:job.kind === 'analyze' || job.kind === 'analysis' ? 'Analyzing the story' : job.kind === 'listen_chapter' ? 'Preparing chapter audio' : 'Recording your story',completed:'Ready for you',failed:'Job stopped',cancelled:'Cancelled',interrupted:'Interrupted · ready to resume',budget_limited:'Allowance reached · saved work retained',quota_limited:'Daily request quota reached · saved audio kept'};
  banner.innerHTML = `<span class="job-message"><span class="job-label">${escapeHTML(labels[job.status] || job.status)}</span>${job.error || job.message ? ` · ${escapeHTML(job.error || job.message)}` : ''}</span>${active ? `<progress value="${Number(job.progress) || 0}" max="${Number(job.total) || 1}" aria-label="Job progress"></progress><span>${Number(job.progress) || 0} / ${Number(job.total) || '…'}</span><button class="button subtle" data-cancel-job="${escapeHTML(job.id)}">Cancel</button>` : `<button class="icon-button small" data-dismiss-job aria-label="Dismiss job status">${icon('close')}</button>`}`;
  updateBusyControls();
  renderProduction();
}
async function pollJobs(refreshBookOnComplete = true, {jobsOnly = false} = {}) {
  clearTimeout(state.poll);
  state.poll = null;
  const id = state.book?.id;
  if (!id) return;
  const token = {id, selectionVersion:state.selectionVersion};
  state.jobPollToken = token;
  const current = () => state.jobPollToken === token && id === state.book?.id && token.selectionVersion === state.selectionVersion;
  const previous = state.jobs.slice();
  const wasBusy = Boolean(busyJob());
  const schedule = delay => {
    if (!current() || !busyJob()) return;
    const onlyListening = state.jobs.filter(job => ['queued','running'].includes(job.status)).every(job => ['listen','voice_preview','listen_chapter'].includes(job.kind));
    state.poll = setTimeout(() => pollJobs(!onlyListening, {jobsOnly:onlyListening}), delay);
  };
  try {
    const requests = [request(`/api/jobs?book_id=${encodeURIComponent(id)}`)];
    if (!jobsOnly) requests.push(request(`/api/books/${encodeURIComponent(id)}/analysis`));
    const [jobsResult, analysisResult] = await Promise.allSettled(requests);
    if (!current()) return;
    if (analysisResult) {
      if (analysisResult.status === 'fulfilled') { state.analysisSummary = analysisResult.value; state.analysisError = null; }
      else state.analysisError = `Could not load chapter progress: ${analysisResult.reason.message}`;
      renderAnalysisProgress();
    }
    if (jobsResult.status === 'rejected') throw jobsResult.reason;
    const response = jobsResult.value;
    const refreshed = [...(Array.isArray(response) ? response : response.jobs || [])];
    // A passage can start or finish through onJob while this GET is in flight.
    // Retain those newer callbacks rather than reviving an older status or
    // forgetting the next queued request when an earlier snapshot arrives.
    for (const local of state.jobs) {
      if (previous.find(job => job.id === local.id) === local) continue;
      const index = refreshed.findIndex(job => job.id === local.id);
      if (index < 0) refreshed.push(local);
      else if ((local.updated_at || '') > (refreshed[index].updated_at || '')) refreshed[index] = local;
    }
    state.jobs = refreshed.sort((a,b) => (b.created_at || '').localeCompare(a.created_at || ''));
    renderJob();
    if (Boolean(busyJob()) !== wasBusy) { renderReader(); updatePlayer(); }
    const transitioned = state.jobs.some(job => !['running','queued'].includes(job.status) && previous.some(old => old.id === job.id && ['running','queued'].includes(old.status)));
    if (!jobsOnly && refreshBookOnComplete && (transitioned || !busyJob())) {
      const book = await request(`/api/books/${encodeURIComponent(id)}`);
      if (current()) {
        state.referenceCache.clear(); state.referenceVersion++;
        applyBook(book);
        $$('[data-character-references][open]').forEach(node => loadCharacterReferences(node.dataset.characterReferences));
        await refreshLibrary();
      }
    }
    schedule(1600);
  } catch (error) {
    if (current()) { toast(`Could not check job status: ${error.message}`, true); schedule(5000); }
  } finally {
    if (state.jobPollToken === token) state.jobPollToken = null;
  }
}
async function startJob(kind, scope = {}) {
  if (!state.book || busyJob()) return;
  const provider = $(kind === 'analyze' ? '#analysis-provider' : '#render-provider').value;
  if (cloudProviders.includes(provider) && !providerHasKey(provider)) { openSettings(provider); toast(`Add ${provider === 'gemini' ? 'a' : 'an'} ${analysisLabels[provider]} API key to use ${kind === 'analyze' ? 'story analysis' : 'narration'}.`); return; }
  if (provider === 'system' && state.status?.providers?.find(p => p.id === 'system')?.available === false) { toast('Device narration is unavailable on this server. Connect Gemini in settings.', true); return; }
  const id = state.book.id;
  stopAudio({clear:true});
  updateBusyControls(true);
  try {
    const job = await post(`/api/books/${encodeURIComponent(id)}/${kind}`, {provider,...scope});
    if (id !== state.book?.id) return;
    state.jobs.unshift(job);
    renderJob();
    await pollJobs(true);
    return job;
  } catch (error) { toast(error.message, true); updateBusyControls(); return null; }
}
function openSettings(provider) {
  clearKeyInputs(); fillSettings(); cloudProviders.forEach(renderAccountCheck); updateSettingsControls(); $('#settings-error').hidden = true; $('#settings-dialog').showModal();
  if (cloudProviders.includes(provider)) providerField('api-key', provider).focus();
}
function openImport() { $('#import-error').hidden = true; $('#import-dialog').showModal(); }
function showInlineError(id, message) { const node = $(id); node.textContent = message; node.hidden = false; }
async function saveEditor(form, kind, id) {
  const bookId = state.book.id;
  const values = Object.fromEntries(new FormData(form));
  const button = $('button[type="submit"]', form);
  button.disabled = true;
  try {
    const book = await patch(`/api/books/${encodeURIComponent(bookId)}/${kind}/${encodeURIComponent(id)}`, values);
    applyBook(book);
    toast(kind === 'characters' ? 'Voice profile saved. Affected takes will need regeneration.' : 'Direction saved. Affected takes will need regeneration.');
  } catch (error) { toast(error.message, true); button.disabled = false; }
}

// Navigation and delegated editor actions.
$('#library-list').addEventListener('click', event => { const button = event.target.closest('[data-book]'); if (button && button.dataset.book !== state.book?.id) selectBook(button.dataset.book); });
$$('.tab').forEach(button => button.addEventListener('click', () => setTab(button.dataset.tab)));
$('#go-studio').addEventListener('click', () => setTab('studio'));
$('#chapter-list').addEventListener('click', event => { const button = event.target.closest('[data-chapter]'); if (button) setChapter(button.dataset.chapter); });
$('#previous-chapter').addEventListener('click', () => { const index = state.book.chapters.findIndex(c => c.id === state.chapterId); if (index > 0) setChapter(state.book.chapters[index - 1].id); });
$('#next-chapter').addEventListener('click', () => { const index = state.book.chapters.findIndex(c => c.id === state.chapterId); if (index < state.book.chapters.length - 1) setChapter(state.book.chapters[index + 1].id); });
$('#studio-chapter').addEventListener('change', event => setChapter(event.target.value, {scroll:false}));
$('#reader-text').addEventListener('click', event => { const passage = event.target.closest('[data-segment]'); if (passage) startSegment(passage.dataset.segment); });
$('#reader-text').addEventListener('keydown', event => { if (['Enter',' '].includes(event.key)) { const passage = event.target.closest('[data-segment]'); if (passage) { event.preventDefault(); startSegment(passage.dataset.segment); } } });
$('#cast-grid').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.target;
  if (form.dataset.characterForm) return saveEditor(form, 'characters', form.dataset.characterForm);
  if (form.id === 'add-character-form') {
    const button = $('button[type="submit"]', form); button.disabled = true;
    try { const book = await post(`/api/books/${encodeURIComponent(state.book.id)}/characters`, Object.fromEntries(new FormData(form))); applyBook(book); toast('Character added. Assign their passages in the studio.'); } catch (error) { toast(error.message, true); button.disabled = false; }
  }
});
$('#scene-list').addEventListener('submit', event => { event.preventDefault(); const form = event.target; if (form.dataset.sceneForm) saveEditor(form,'scenes',form.dataset.sceneForm); else if (form.dataset.segmentForm) saveEditor(form,'segments',form.dataset.segmentForm); });
$('#cast-grid').addEventListener('toggle', event => {
  if (event.target.matches('[data-character-references]') && event.target.open) loadCharacterReferences(event.target.dataset.characterReferences);
}, true);
$('#cast-grid').addEventListener('click', event => {
  const example = event.target.closest('[data-preview-character]');
  if (example) { auditionCharacter(example.closest('[data-character-form]'), example.dataset.previewCharacter); return; }
  const retry = event.target.closest('[data-retry-references]');
  if (retry) loadCharacterReferences(retry.dataset.retryReferences, {retry:true});
  const more = event.target.closest('[data-more-references]');
  if (more) {
    const characterId = more.dataset.moreReferences;
    const entry = state.referenceCache.get(characterId);
    if (entry?.references) {
      entry.shown = Math.min(entry.references.length, (entry.shown || 100) + 100);
      updateCharacterReferences(characterId);
      const details = $$('[data-character-references]').find(node => node.dataset.characterReferences === characterId);
      if (details) ($('[data-more-references]', details) || $('summary', details))?.focus({preventScroll:true});
    }
  }
  const reference = event.target.closest('[data-reference-chapter]');
  if (!reference) return;
  setChapter(reference.dataset.referenceChapter, {scroll:false});
  const segment = segmentById(reference.dataset.referenceSegment);
  if (segment) state.segmentId = segment.id;
  renderReader(); setTab('read'); updateHighlight({scroll:true}); updatePlayer(); saveProgress();
  const selected = $$('.passage').find(node => node.dataset.segment === state.segmentId);
  selected?.focus({preventScroll:true});
});
$('#analysis-progress').addEventListener('click', event => {
  const chapter = event.target.closest('[data-analysis-chapter]');
  if (chapter) { setChapter(chapter.dataset.analysisChapter, {scroll:false}); $('#studio-chapter').focus(); }
});
$('#scene-list').addEventListener('click', event => { const example = event.target.closest('[data-preview-speaker]'); if (example) { auditionPassage(example.closest('[data-segment-form]')); return; } const scene = event.target.closest('[data-render-scene]'); const segment = event.target.closest('[data-render-segment]'); const play = event.target.closest('[data-play-segment]'); if (scene) startJob('render',{scene_id:scene.dataset.renderScene}); else if (segment) startJob('render',{segment_id:segment.dataset.renderSegment,force:true}); else if (play) startSegment(play.dataset.playSegment, {enhanced:true}); });
for (const selector of ['#cast-grid','#scene-list']) {
  $(selector).addEventListener('input', event => {
    if (state.voicePreview && event.target.matches('input,textarea,select')) window.BardicVoicePreview?.stop();
  });
  $(selector).addEventListener('change', event => {
    if (state.voicePreview && event.target.matches('input,textarea,select')) window.BardicVoicePreview?.stop();
  });
}
$('#export-link').addEventListener('click', event => { const ready = state.book?.segments.filter(playable).length || 0; if (!ready) { event.preventDefault(); toast('Narrate at least one passage before exporting your audiobook.'); } else if (ready < state.book.segments.length) toast('Exporting available takes. Missing passages are listed in the export manifest.'); });
$('#analyze-button').addEventListener('click', () => { renderProduction(); $('#progressive-production').scrollIntoView({behavior:'smooth', block:'start'}); $('#progressive-production select')?.focus(); });
$('#analysis-scope').addEventListener('change', renderAnalysisProgress);
$('#analyze-from-cast').addEventListener('click', () => { setTab('studio'); $('#analysis-provider').focus(); });
$('#render-button').addEventListener('click', () => startJob('render'));
$('#render-provider').addEventListener('change', () => { $('#render-provider').dataset.chosen = 'true'; updateProviderHint(); });
$('#analysis-provider').addEventListener('change', async event => {
  const provider = event.target.value;
  event.target.disabled = true;
  updateAnalysisHint();
  try { state.status = await post('/api/settings', {analysis_provider:provider}); }
  catch (error) { $('#analysis-provider').value = state.status?.analysis_provider || 'local'; toast(error.message, true); }
  finally { event.target.disabled = false; updateAnalysisHint(); renderProduction(); }
});
$('#job-banner').addEventListener('click', async event => { const cancel = event.target.closest('[data-cancel-job]'); if (cancel) { cancel.disabled = true; try { await post(`/api/jobs/${encodeURIComponent(cancel.dataset.cancelJob)}/cancel`); await pollJobs(true); } catch (error) { toast(error.message,true); cancel.disabled = false; } } if (event.target.closest('[data-dismiss-job]')) $('#job-banner').hidden = true; });

// Import and provider settings.
['#sidebar-import','#import-button','#welcome-import','#header-import'].forEach(id => $(id).addEventListener('click', openImport));
async function libraryChanged() {
  await refreshLibrary();
  if (state.book && state.books.some(b => b.id === state.book.id)) {
    const current = await request(`/api/books/${encodeURIComponent(state.book.id)}`); applyBook(current);
  } else {
    stopAudio({clear:true}); state.selectionVersion++; state.book = null; state.jobs = []; clearTimeout(state.poll);
    if (state.books.length) await selectBook(state.books[0].id); else renderBook();
  }
}
$('#library-manage').addEventListener('click', async () => {
  $('#library-dialog').showModal();
  const mounted = Boolean($('#library-manager').children.length);
  await window.BardicLibrary?.render($('#library-manager'), {
    onChange:libraryChanged,
    onSelectBook:async id => { $('#library-dialog').close(); await selectBook(id); },
    onSelectSeries:series => window.BardicSeriesProcessing?.render($('#series-processing'), typeof series === 'string' ? {id:series} : series, {status:state.status,onChange:() => pollJobs(true)}),
  });
  if (mounted) await window.BardicLibrary?.refresh($('#library-manager'));
});
$('#settings-button').addEventListener('click', () => openSettings());
$$('[data-close]').forEach(button => button.addEventListener('click', () => document.getElementById(button.dataset.close).close()));
$$('dialog').forEach(dialog => dialog.addEventListener('click', event => { if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); } }));
$('#book-file').addEventListener('change', () => { $('#file-label').textContent = $('#book-file').files[0]?.name || 'Drop your book here'; });
['dragenter','dragover'].forEach(name => $('#drop-zone').addEventListener(name, event => { event.preventDefault(); $('#drop-zone').classList.add('dragging'); }));
['dragleave','drop'].forEach(name => $('#drop-zone').addEventListener(name, event => { event.preventDefault(); $('#drop-zone').classList.remove('dragging'); }));
$('#drop-zone').addEventListener('drop', event => { if (event.dataTransfer.files.length) { $('#book-file').files = event.dataTransfer.files; $('#file-label').textContent = event.dataTransfer.files[0].name; } });
$('#import-form').addEventListener('submit', async event => {
  event.preventDefault();
  const file = $('#book-file').files[0];
  if (!file) return;
  const button = $('#import-submit'); button.disabled = true; button.textContent = 'Opening your book…'; $('#import-error').hidden = true;
  try {
    const data = new FormData(); data.append('file', file);
    const book = await request('/api/books',{method:'POST',body:data});
    await refreshLibrary(); await selectBook(book.id || book.book?.id);
    $('#import-dialog').close(); $('#import-form').reset(); $('#file-label').textContent = 'Drop your book here';
    state.tab = 'read'; setTab('read'); toast('Your book is ready. Meet the cast, or start a local narration.');
  } catch (error) { showInlineError('#import-error', error.message); }
  finally { button.disabled = false; button.innerHTML = `Add to library ${icon('arrow')}`; }
});
$('#demo-button').addEventListener('click', async () => { const button = $('#demo-button'); button.disabled = true; button.textContent = 'Opening a story…'; try { const book = await post('/api/demo'); await refreshLibrary(); await selectBook(book.id || book.book?.id); } catch (error) { toast(error.message,true); } finally { button.disabled = false; button.innerHTML = `Try a short story ${icon('arrow')}`; } });
$('#settings-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (state.settingsBusy) return;
  state.settingsBusy = true; updateSettingsControls(); $('#settings-error').hidden = true;
  let values;
  try { values = {tts_model:$('#tts-model').value.trim(),analysis_provider:$('#settings-analysis-provider').value,analysis_models_by_provider:{},preprocess_models_by_provider:{},api_keys:{},...narrationLimitValues()}; }
  catch (error) { showInlineError('#settings-error', error.message); state.settingsBusy = false; updateSettingsControls(); return; }
  for (const provider of cloudProviders) {
    values.analysis_models_by_provider[provider] = modelValue('analysis', provider);
    values.preprocess_models_by_provider[provider] = modelValue('preprocess', provider);
    const key = providerField('api-key', provider).value.trim();
    if (key) values.api_keys[provider] = key;
  }
  try { await post('/api/settings', values); clearKeyInputs(); await refreshStatus(); if (state.book) { renderCast(); renderReader(); } $('#settings-dialog').close(); toast('Settings saved. Your studio is ready.'); } catch (error) { showInlineError('#settings-error',error.message); } finally { state.settingsBusy = false; updateSettingsControls(); }
});
$$('[data-clear-key]').forEach(button => button.addEventListener('click', async () => {
  const provider = button.dataset.clearKey;
  if (state.settingsBusy) return;
  state.settingsBusy = true; updateSettingsControls();
  $('#settings-error').hidden = true;
  try { await post('/api/settings',{api_keys:{[provider]:''}}); providerField('api-key', provider).value = ''; await refreshStatus({syncSettings:false}); toast(`${analysisLabels[provider]} key cleared for this session.`); }
  catch (error) { showInlineError('#settings-error',error.message); }
  finally { state.settingsBusy = false; updateSettingsControls(); }
}));
$$('[data-check-account]').forEach(button => button.addEventListener('click', () => checkAccounts([button.dataset.checkAccount])));
$('#check-all-accounts').addEventListener('click', () => checkAccounts(cloudProviders));
$('#tts-model').addEventListener('change', fillNarrationLimits);
cloudProviders.forEach(provider => {
  providerField('api-key', provider).addEventListener('input', () => renderAccountCheck(provider));
  for (const role of ['analysis','preprocess']) {
    modelPicker(role, provider).addEventListener('change', () => modelPickerChanged(role, provider));
    modelCustom(role, provider).addEventListener('input', () => modelPickerChanged(role, provider));
  }
});
$$('[data-refresh-models]').forEach(button => button.addEventListener('click', () => refreshModels(button.dataset.refreshModels)));
$('#settings-dialog').addEventListener('close', clearKeyInputs);

// Clip-boundary synchronization: no fabricated word timing.
$('#play-button').addEventListener('click', togglePlayback);
$('#close-voice-preview').addEventListener('click', () => window.BardicVoicePreview?.stop());
$('#player-listen-settings').addEventListener('click', () => {
  setTab('read');
  $('#simple-listen').scrollIntoView({behavior:'smooth',block:'center'});
  $('#simple-listen [data-listen-field="voice"]')?.focus({preventScroll:true});
});
$('#previous-segment').addEventListener('click', () => moveSegment(-1));
$('#next-segment').addEventListener('click', () => moveSegment(1));
$('#playback-speed').value = String(audio.playbackRate);
$('#playback-speed').addEventListener('change', event => setPlaybackRate(event.target.value));
$('#audio-progress').addEventListener('input', event => {
  const time = Number(event.target.value);
  if (state.voicePreview) { if (state.voicePreview.audio && Number.isFinite(audio.duration)) audio.currentTime = time; updatePlayer(); return; }
  if (state.audioSegmentId === state.segmentId && Number.isFinite(audio.duration)) { audio.currentTime = clipStart(playingMedia()) + time; saveProgress(); }
  else if (listeningReady(segmentById(state.segmentId))) { state.pendingOffset = time; startSegment(state.segmentId,{autoplay:false,offset:time}); }
  updatePlayer();
});
audio.addEventListener('loadedmetadata', () => {
  const start = state.voicePreview ? 0 : clipStart(playingMedia());
  if (!state.voicePreview && (state.pendingOffset > 0 || start > 0)) { audio.currentTime = Math.min(start + state.pendingOffset, Math.max(0,audio.duration - .01)); state.pendingOffset = 0; }
  updatePlayer();
});
audio.addEventListener('timeupdate', () => { followClip(); updatePlayer(); updateListeningBuffer(); if (Date.now() - state.lastSave > 1000) { saveProgress(); state.lastSave = Date.now(); } });
audio.addEventListener('play', () => { updatePlayer(); renderReader(); });
audio.addEventListener('waiting', () => { mediaBuffering = true; reportPlaybackIssue('playback_waiting'); updatePlayer(); });
audio.addEventListener('stalled', () => { if (!audio.paused) { mediaBuffering = true; reportPlaybackIssue('playback_waiting'); updatePlayer(); } });
audio.addEventListener('playing', () => { if (mediaBuffering) reportPlaybackIssue('playback_resumed'); mediaBuffering = false; updatePlayer(); updateListeningBuffer(); });
audio.addEventListener('pause', () => { updatePlayer(); saveProgress(); renderReader(); });
audio.addEventListener('ended', () => finishClip());
audio.addEventListener('error', () => {
  if (!audio.getAttribute('src')) return;
  if (state.voicePreview) {
    reportPlaybackIssue('playback_media_error', {media_error_code:audio.error?.code});
    window.BardicVoicePreview?.stop();
    toast('The voice example could not be loaded. Your reading position is saved; try the example again.', true);
    return;
  }
  const simple = simpleActive();
  const segment = segmentById(state.segmentId);
  const offset = passageTime() || state.pendingOffset || 0;
  reportPlaybackIssue('playback_media_error', {media_error_code:audio.error?.code});
  stopAudio({clear:true});
  state.pendingOffset = offset;
  if (simple) window.BardicListen?.forgetAudio?.(state.book, segment);
  updatePlayer(); saveProgress();
  toast(simple ? 'Saved narration could not be loaded. Press Play to check the local cache and try again.' : 'This audio take could not be loaded. Try regenerating it in the studio.',true);
});
window.addEventListener('pagehide', () => { saveProgress(); stopAudio(); });
document.addEventListener('visibilitychange', () => { if (document.hidden) saveProgress(); });
document.addEventListener('keydown', event => { if (event.code === 'Space' && !event.altKey && !event.ctrlKey && !event.metaKey && !event.repeat && !event.target.closest('input,textarea,select,button,[role="button"],a,dialog') && state.book) { event.preventDefault(); togglePlayback(); } });

async function init() {
  try {
    await Promise.all([refreshStatus(), refreshLibrary()]);
    const saved = safeRead('bardic:lastBook');
    const book = state.books.find(item => item.id === saved) || state.books[0];
    if (book) await selectBook(book.id); else renderBook();
  } catch (error) {
    $('#fatal-error').hidden = false;
    $('#fatal-error').textContent = `The local studio could not connect: ${error.message}. Check that the Bardic server is running, then reload this page.`;
  }
}
setupVoicePreviews();
init();
