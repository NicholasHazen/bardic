/* Voice library: make, audition, iterate and manage voices shared by every book.
   Rendering only reads the /api/voices snapshot. Every request that can run a
   provider (samples, billed Gemini creates, clones, deletes) is an explicit click.
   Markup uses the BardicUI kit (ui.js), including its one escape helper. */
(() => {
  'use strict';
  const UI = window.BardicUI;
  const escape = UI.esc;
  const encode = value => encodeURIComponent(value);
  const LIBRARY_PROVIDERS = ['breeze','gemini'];
  const LABELS = {breeze:'Breeze', gemini:'Gemini', system:'Mac voices'};
  const ORIGINS = {designed:'Designed', cloned:'Cloned', imported:'From server'};
  const GEMINI_DEFAULT = 'Kore';
  const GEMINI_BUILT_IN = ['Kore','Puck','Charon','Aoede','Fenrir','Leda','Orus','Zephyr','Callirrhoe','Autonoe','Enceladus','Iapetus','Umbriel','Algieba','Despina','Erinome','Algenib','Rasalgethi','Laomedeia','Achernar','Alnilam','Schedar','Gacrux','Pulcherrima','Achird','Zubenelgenubi','Vindemiatrix','Sadachbia','Sadaltager','Sulafat'];
  const LIB = 'library:', DIRECT = 'id:', CREATE = '__create__';
  const SERVER_STATES = {
    changed:'Changed on the Breeze server since this version was saved.',
    missing:'Missing from the server, so it cannot record new audio.',
    other_project:'Stored in a different Google project than the current API key.',
  };

  // Cast assignment helpers ---------------------------------------------------
  // Cast selects encode a character's choice as "" (Default), "library:<id>"
  // (follow a library voice's current version) or "id:<provider voice>".
  function libraryVoices(library, provider) {
    return (library?.voices || []).filter(voice => voice.provider === provider && !voice.deleted);
  }
  function findVoice(library, id) { return (library?.voices || []).find(voice => voice.id === id) || null; }
  function currentVersion(voice) {
    return (voice?.versions || []).find(version => version.version === voice.current_version) || null;
  }
  function defaultVoice(library, provider) {
    const id = library?.defaults?.[provider];
    return id ? findVoice(library, id) : null;
  }
  function encodeSelection(provider, character) {
    const selection = character?.voices?.[provider];
    if (!selection || typeof selection !== 'object') return '';
    if (typeof selection.library === 'string' && selection.library) return LIB + selection.library;
    if (typeof selection.id === 'string' && selection.id) return DIRECT + selection.id;
    return '';
  }
  function decodeChoice(value) {
    if (typeof value !== 'string' || !value || value === CREATE) return null;
    if (value.startsWith(LIB)) return {library:value.slice(LIB.length)};
    if (value.startsWith(DIRECT)) return {id:value.slice(DIRECT.length)};
    return null;
  }
  // The `voice` string sent with listen and example requests.
  function requestVoice(provider, value) {
    if (!value) return provider === 'gemini' ? GEMINI_DEFAULT : '';
    if (value.startsWith(LIB)) return value;
    if (value.startsWith(DIRECT)) return value.slice(DIRECT.length);
    return value;
  }
  function designedSupported(library) { return library?.providers?.gemini?.designed_voices_supported !== false; }
  function defaultLabel(provider, library) {
    if (provider === 'gemini') return `Default (${GEMINI_DEFAULT})`;
    if (provider === 'system') return 'Default Mac voice';
    const voice = defaultVoice(library, provider);
    return voice ? `Default (${voice.name})` : 'Default (none set yet)';
  }
  function choiceLabel(provider, value, library) {
    if (!value) return defaultLabel(provider, library);
    if (value.startsWith(LIB)) return findVoice(library, value.slice(LIB.length))?.name || 'Deleted voice';
    if (value.startsWith(DIRECT)) {
      const id = value.slice(DIRECT.length);
      if (provider === 'gemini') return library?.providers?.gemini?.project_voices?.find(voice => voice.id === id)?.display_name || id;
      if (provider === 'system') return (library?.builtin?.system || []).find(voice => voice.id === id)?.name || id;
      return id;
    }
    return value;
  }
  // Menus follow the book's language when the book records one, else the browser's.
  const macLanguage = options => options.language || (typeof navigator !== 'undefined' && navigator.language) || 'en';
  function castOptions(provider, value, library, status, options = {}) {
    value = value || '';
    const seen = new Set();
    const option = (optionValue, label, {disabled = false, reason = ''} = {}) => {
      seen.add(optionValue);
      const off = disabled && optionValue !== value;
      return `<option value="${escape(optionValue)}" ${optionValue === value ? 'selected' : ''} ${off ? 'disabled' : ''}>${escape(label)}${off && reason ? ` · ${escape(reason)}` : ''}</option>`;
    };
    const group = (label, items) => items.length ? `<optgroup label="${escape(label)}">${items.join('')}</optgroup>` : '';
    let html = option('', defaultLabel(provider, library));
    if (provider === 'system') {
      const voices = library?.builtin?.system || status?.system_voices || [];
      const macOption = voice => option(DIRECT + (voice.id || voice.name), `${voice.name || voice.id}${voice.locale ? ` · ${voice.locale}` : ''}`);
      // The book's language first; other languages and novelty voices stay reachable below.
      const split = window.BardicListen?.macVoices?.(voices, {language:macLanguage(options), showAll:Boolean(options.showAll), keep:value.slice(DIRECT.length)});
      const shown = split ? split.shown : voices;
      html += group('Mac voices', shown.map(macOption));
      html += group('More Mac voices (other languages and novelty voices)', voices.filter(voice => !shown.includes(voice)).map(macOption));
    } else {
      const unsupported = provider === 'gemini' && !designedSupported(library);
      const reason = unsupported ? 'needs Gemini 3.8 TTS' : '';
      html += group('Your voices', libraryVoices(library, provider).map(voice => option(LIB + voice.id, voice.name,
        {disabled:unsupported || voice.assignable === false, reason:reason || (voice.assignable === false ? 'unavailable' : '')})));
      if (provider === 'gemini') {
        const project = (library?.providers?.gemini?.project_voices || []).filter(voice => !voice.in_library && !voice.draft_candidate);
        html += group('Project voices', project.map(voice => option(DIRECT + voice.id, voice.display_name || voice.id, {disabled:unsupported, reason})));
        html += group('Built-in voices', (library?.builtin?.gemini || GEMINI_BUILT_IN).map(name => option(DIRECT + name, name)));
      }
    }
    if (value && !seen.has(value)) {
      const label = value.startsWith(LIB) ? 'Deleted voice' : value.startsWith(DIRECT) && provider === 'breeze'
        ? `${value.slice(DIRECT.length)} (server voice outside the library)` : `${choiceLabel(provider, value, library)} (not listed)`;
      html += option(value, label);
    }
    if (LIBRARY_PROVIDERS.includes(provider)) html += `<option value="${CREATE}">Create new voice…</option>`;
    return html;
  }
  function voiceWarnings(voice) {
    const warnings = [...(voice?.warnings || [])];
    const state = currentVersion(voice)?.server_state;
    if (SERVER_STATES[state] && !warnings.includes(SERVER_STATES[state])) warnings.push(SERVER_STATES[state]);
    return warnings;
  }
  function castWarnings(provider, value, library, status) {
    value = value || '';
    if (provider === 'system') return [];
    if (!value && provider === 'breeze') {
      const voice = defaultVoice(library, 'breeze');
      if (!voice) return ['No default Breeze voice yet. Check the Breeze connection in Settings, or choose a default in Voices.'];
      return voiceWarnings(voice).map(text => `Default voice: ${text}`);
    }
    const warnings = [];
    if (value.startsWith(LIB)) {
      const voice = findVoice(library, value.slice(LIB.length));
      if (!voice || voice.deleted) return ['This voice was deleted. Choose another voice.'];
      warnings.push(...voiceWarnings(voice));
    } else if (value.startsWith(DIRECT) && provider === 'breeze') {
      warnings.push('Pinned to a server voice outside the library. Choose a library voice to follow its versions.');
    }
    const designed = value.startsWith(LIB) || (value.startsWith(DIRECT) && value.slice(DIRECT.length).startsWith('voice_'));
    if (provider === 'gemini' && designed && !designedSupported(library)) {
      warnings.push(`Designed voices need Gemini 3.8 TTS; the current speech model is ${library?.providers?.gemini?.tts_model || status?.tts_model || 'different'}.`);
    }
    return warnings;
  }
  // The concrete voice identity behind a choice, so listening sessions change
  // when a voice's current version (or the Breeze default) changes.
  function identity(provider, value, library) {
    const id = value?.startsWith?.(LIB) ? value.slice(LIB.length) : !value && provider === 'breeze' ? library?.defaults?.breeze : null;
    const voice = id ? findVoice(library, id) : null;
    const version = currentVersion(voice);
    return voice ? [voice.id, voice.current_version, version?.provider_voice_id ?? null, version?.revision ?? null] : null;
  }
  const cast = {LIB, DIRECT, CREATE, encodeSelection, decodeChoice, requestVoice, castOptions, castWarnings, choiceLabel,
    defaultLabel, defaultVoice, findVoice, libraryVoices, currentVersion, identity};

  // Voices tab ------------------------------------------------------------------
  const panels = new WeakMap();
  const player = typeof Audio === 'function' ? new Audio() : null;
  let playingKey = null, playingPanel = null;
  if (player) {
    player.addEventListener?.('ended', () => { playingKey = null; if (playingPanel) paint(playingPanel); });
    player.addEventListener?.('error', () => {
      playingKey = null;
      if (playingPanel) { playingPanel.error = 'That example could not be played.'; paint(playingPanel); }
    });
  }

  async function request(url, {method = 'GET', body, form} = {}) {
    const options = {method, headers:{Accept:'application/json'}};
    if (form) options.body = form;
    else if (body !== undefined) { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
    const response = await fetch(url, options);
    let data;
    try { data = await response.json(); } catch { data = null; }
    if (!response.ok) {
      const detail = data?.detail;
      const message = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(item => item.msg || String(item)).join('; ') : `Voice request failed (${response.status}).`;
      const hint = ERROR_HINTS[data?.code];
      const error = new Error(hint ? `${message} ${hint}` : message);
      error.code = data?.code;
      throw error;
    }
    return data;
  }
  // The server's details describe the condition; where to fix it is the UI's to say.
  const ERROR_HINTS = {
    gemini_key_missing:'Add a Gemini API key in Providers & settings first.',
    breeze_url_missing:'Add the Breeze server URL in Providers & settings first.',
    narration_active:'Stop that narration before changing which voice characters follow.',
  };

  const drafts = panel => panel.options.library?.drafts || [];
  const activeDraft = panel => drafts(panel).find(draft => draft.id === panel.draftId) || null;
  const bookId = panel => panel.options.book?.id || undefined;
  function draftValue(panel, draft, field) {
    const edited = panel.edits[draft.id]?.[field];
    return edited !== undefined ? edited : draft[field] || '';
  }
  const liveCandidates = draft => (draft?.candidates || []).filter(candidate => !candidate.discarded);
  const storedGemini = draft => draft?.provider === 'gemini' ? liveCandidates(draft).filter(candidate => candidate.provider_voice_id) : [];
  const fmtSeconds = value => Number.isFinite(Number(value)) && value !== null ? `${Number(value).toFixed(1)} s` : '';
  const fmtDate = value => { const date = value ? new Date(value) : null; return date && !Number.isNaN(date.getTime()) ? date.toLocaleDateString() : ''; };

  function playButton(panel, key, url, label = 'Hear example') {
    if (!url) return '<span class="voice-muted">No example saved</span>';
    const playing = playingKey === key && playingPanel === panel;
    return `<button type="button" class="button subtle voice-play" data-voices-action="play" data-key="${escape(key)}" data-url="${escape(url)}" aria-pressed="${playing}">${playing ? 'Stop' : escape(label)}</button>`;
  }
  function confirmRow(panel, kind, id, message, confirmLabel, extra = '') {
    const confirm = panel.confirm;
    if (!confirm || confirm.kind !== kind || confirm.id !== id) return '';
    return `<div class="voice-confirm" role="group" aria-label="Confirm">${message}${extra}<div class="voice-confirm-actions"><button type="button" class="button primary" data-voices-action="confirm">${escape(confirmLabel)}</button><button type="button" class="button subtle" data-voices-action="cancel-confirm">Cancel</button></div></div>`;
  }
  function usageText(voice) {
    const usage = voice.usage || [];
    if (!usage.length) return 'Not assigned to any character yet.';
    return `Used by ${usage.slice(0, 8).map(item => `${escape(item.character_name)} <span class="voice-muted">(${escape(item.book_title)}${item.follows === 'default' ? ', as default' : ''})</span>`).join(', ')}${usage.length > 8 ? ` and ${usage.length - 8} more` : ''}.`;
  }

  function voiceCard(panel, voice) {
    const current = currentVersion(voice);
    const warnings = voiceWarnings(voice);
    const editing = panel.editing === voice.id;
    // Characters left on Default are listed under the current default voice.
    const currentDefault = defaultVoice(panel.options.library, voice.provider);
    const defaultFollowers = (currentDefault?.usage || []).filter(item => item.follows === 'default').length;
    const serverChoice = panel.confirm?.kind === 'delete' && panel.confirm.id === voice.id ? panel.confirm.server : voice.origin !== 'imported';
    const versions = (voice.versions || []).slice().sort((a, b) => b.version - a.version).map(version => {
      const isCurrent = version.version === voice.current_version;
      const state = SERVER_STATES[version.server_state] ? `<span class="voice-state">${escape(SERVER_STATES[version.server_state])}</span>` : '';
      const recipe = version.recipe?.description ? `<span class="voice-recipe">“${escape(version.recipe.description)}”</span>` : '';
      return `<li class="${isCurrent ? 'current' : ''}"><span class="voice-version-label">v${escape(version.version)}${isCurrent ? ' · current' : ''}</span><span class="voice-muted">${escape(ORIGINS[version.made] || version.made || '')}${fmtDate(version.created_at) ? ` · ${escape(fmtDate(version.created_at))}` : ''}${version.expires_at ? ` · expires ${escape(fmtDate(version.expires_at))}` : ''}</span>${state}${recipe}<span class="voice-version-actions">${playButton(panel, `${voice.id}:${version.version}`, version.audition?.url)}${isCurrent ? '' : `<button type="button" class="button text-button" data-voices-action="make-current" data-id="${escape(voice.id)}" data-version="${escape(version.version)}">Restore</button>`}</span>${confirmRow(panel, 'current', `${voice.id}:${version.version}`, `<p>Characters using “${escape(voice.name)}” switch to version ${escape(version.version)}. Their recordings become out of date (kept in history) until you record them again.</p>`, `Restore version ${version.version}`)}</li>`;
    }).join('');
    return `<article class="voice-card${voice.is_default ? ' is-default' : ''}" data-voice-card="${escape(voice.id)}">
      <div class="voice-card-top"><div><h3>${escape(voice.name)}</h3><div class="voice-meta">${escape(LABELS[voice.provider] || voice.provider)} · ${escape(ORIGINS[voice.origin] || voice.origin || 'Voice')} · v${escape(voice.current_version)}${voice.versions?.length > 1 ? ` of ${voice.versions.length}` : ''}</div></div>${voice.is_default ? UI.badge('Default', 'good') : ''}</div>
      ${editing ? `<form class="voice-edit" data-voice-edit="${escape(voice.id)}"><label class="field-label" for="voice-name-${escape(voice.id)}">Name</label><input id="voice-name-${escape(voice.id)}" name="name" maxlength="100" required value="${escape(voice.name)}"><label class="field-label" for="voice-description-${escape(voice.id)}">Description</label><textarea id="voice-description-${escape(voice.id)}" name="description" maxlength="1000" rows="3">${escape(voice.description || '')}</textarea><p class="field-help">Renaming never changes how the voice sounds.</p><div class="voice-confirm-actions"><button type="submit" class="button primary">Save</button><button type="button" class="button subtle" data-voices-action="cancel-edit">Cancel</button></div></form>`
        : `${voice.description ? `<p class="voice-description">${escape(voice.description)}</p>` : ''}`}
      ${warnings.length ? `<ul class="voice-warnings">${warnings.map(text => `<li>${escape(text)}</li>`).join('')}</ul>` : ''}
      <div class="voice-current">${playButton(panel, `${voice.id}:${current?.version ?? voice.current_version}`, current?.audition?.url)}${voice.source?.character_name ? `<span class="voice-muted">Made for ${escape(voice.source.character_name)}</span>` : ''}</div>
      <details class="voice-versions"${panel.openVersions.has(voice.id) ? ' open' : ''} data-voice-versions="${escape(voice.id)}"><summary>Versions (${escape(voice.versions?.length || 0)})</summary><ol>${versions}</ol></details>
      <p class="voice-usage">${usageText(voice)}</p>
      <div class="voice-actions">
        <button type="button" class="button subtle" data-voices-action="iterate" data-id="${escape(voice.id)}"${setupNeeded(panel, voice.provider) ? ' disabled aria-describedby="voices-setup-reason"' : ''}>New version</button>
        ${editing ? '' : `<button type="button" class="button subtle" data-voices-action="edit" data-id="${escape(voice.id)}">Edit</button>`}
        ${voice.provider === 'breeze' && !voice.is_default ? `<button type="button" class="button subtle" data-voices-action="set-default" data-id="${escape(voice.id)}">Set as default</button>` : ''}
        <button type="button" class="button subtle voice-danger" data-voices-action="delete" data-id="${escape(voice.id)}"${voice.is_default ? ' disabled title="Choose another default voice first"' : ''}>Delete</button>
      </div>
      ${confirmRow(panel, 'default', voice.id, `<p>Make “${escape(voice.name)}” the Breeze default? ${defaultFollowers ? `${defaultFollowers} character${defaultFollowers === 1 ? '' : 's'} left on Default switch to it, and their recordings become out of date (kept in history).` : 'Characters left on Default will use it.'}</p>`, 'Set as default')}
      ${confirmRow(panel, 'delete', voice.id, `<p>Delete “${escape(voice.name)}”? ${(voice.usage || []).length ? `${usageText(voice)} Those characters need another voice before they can be recorded again; saved audio is kept.` : 'No character uses it.'}</p>`, 'Delete voice',
        `<label class="voice-check"><input type="checkbox" data-voices-field="delete-server" ${serverChoice ? 'checked' : ''}> ${voice.provider === 'gemini' ? 'Also delete it from my Google project' : 'Also delete it on the Breeze server'}</label>`)}
    </article>`;
  }

  function cloneForm(panel, draft) {
    const context = draft?.context;
    const blocked = setupNeeded(panel, 'breeze');
    return `<details class="voice-clone"${panel.cloneOpen ? ' open' : ''} data-voices-clone><summary>Clone from a recording</summary>
      <form data-voices-form="clone">
        <label class="field-label" for="clone-name">Voice name</label><input id="clone-name" name="name" maxlength="100" required value="${escape(draft ? draftValue(panel, draft, 'name') : '')}">
        <label class="field-label" for="clone-audio">Recording <span>5–15 s of clean speech is best; up to 30 s and 20 MB</span></label><input id="clone-audio" name="reference_audio" type="file" accept="audio/*" required>
        <label class="field-label" for="clone-text">Exact transcript</label><textarea id="clone-text" name="reference_text" rows="2" required placeholder="Every word spoken in the recording, including repetitions"></textarea>
        <label class="field-label" for="clone-description">Description <span>optional</span></label><textarea id="clone-description" name="description" rows="2" maxlength="1000">${escape(draft ? draftValue(panel, draft, 'description') : '')}</textarea>
        <label class="voice-check"><input type="checkbox" name="consent" value="true" required> I have the speaker's permission to clone this voice.</label>
        <p class="field-help">The recording is sent to your Breeze server, which keeps it as the voice's reference clip.</p>
        <div class="voice-confirm-actions"><button type="submit" class="button primary" ${panel.pending || blocked ? 'disabled' : ''}${blocked ? ' aria-describedby="voices-setup-reason"' : ''}>${panel.pending === 'clone' ? 'Cloning…' : context ? `Clone & assign to ${escape(context.character_name)}` : 'Clone voice'}</button></div>
      </form></details>`;
  }

  function designer(panel, draft) {
    const library = panel.options.library;
    const gemini = library?.providers?.gemini || {};
    const base = draft.base_voice_id ? findVoice(library, draft.base_voice_id) : null;
    const nextVersion = base ? Math.max(0, ...(base.versions || []).map(version => version.version)) + 1 : null;
    const candidates = draft.candidates || [];
    const live = liveCandidates(draft);
    const chosen = live.some(candidate => candidate.id === panel.choice[draft.id]) ? panel.choice[draft.id] : live.at(-1)?.id || '';
    const mode = base && panel.mode[draft.id] !== 'new' ? 'version' : 'new';
    const busy = Boolean(panel.pending || draft.busy);
    const context = draft.context;
    const sampleSeconds = Math.max(3, Math.round((draftValue(panel, draft, 'sample_text').length || 60) / 14));
    const heading = base ? `New version of ${base.name}` : `New ${LABELS[draft.provider] || draft.provider} voice`;
    const blocked = setupNeeded(panel, draft.provider);
    const generateArea = draft.provider === 'breeze'
      ? `<div class="designer-generate"><label>Samples<select data-voices-field="count" aria-label="Number of samples">${[1,2,3].map(count => `<option value="${count}" ${panel.count === count ? 'selected' : ''}>${count}</option>`).join('')}</select></label><button type="button" class="button primary" data-voices-action="generate" ${busy || blocked ? 'disabled' : ''}${blocked ? ' aria-describedby="voices-setup-reason"' : ''}>${panel.pending === 'generate' || draft.busy ? 'Making samples…' : 'Make samples'}</button><p class="field-help">Free: runs on your Breeze server for about ${escape(sampleSeconds * panel.count)} seconds. Samples last 24 hours on the server; Bardic keeps a copy of each.</p></div>`
      : `<div class="designer-generate gemini"><div class="designer-row"><label>Language<input data-voices-field="language_code" value="${escape(panel.gemini.language_code)}" maxlength="20" aria-label="Language tag"></label><label>Gender<select data-voices-field="gender" aria-label="Voice gender">${[['','Not set'],['female','Female'],['male','Male'],['neutral','Neutral']].map(([value, label]) => `<option value="${value}" ${panel.gemini.gender === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label></div>
        <p class="voice-cost">Paid: each Create voice is a billed Gemini request (cost unknown to Bardic) that stores a voice in your Google project (limit ${escape(gemini.limit || 200)}, kept for one year). Discarded candidates are deleted from the project.</p>
        <label class="voice-check"><input type="checkbox" data-voices-field="confirm-cost" ${panel.gemini.confirmed ? 'checked' : ''} ${busy || !gemini.has_api_key ? 'disabled' : ''}> I understand this creates a billed, stored voice (${Number.isInteger(gemini.stored_count) ? `${escape(gemini.stored_count)} of ${escape(gemini.limit || 200)} used` : 'current count unknown; refresh Gemini voices to check'}).</label>
        <button type="button" class="button primary" data-voices-action="generate" ${busy || !panel.gemini.confirmed || !gemini.has_api_key ? 'disabled' : ''}${blocked ? ' aria-describedby="voices-setup-reason"' : ''}>${panel.pending === 'generate' || draft.busy ? 'Creating…' : 'Create voice · paid'}</button></div>`;
    const candidateList = candidates.length ? `<ol class="candidate-list">${candidates.map(candidate => {
      const label = candidate.kind === 'gemini_voice' ? `Voice ${candidate.id}` : `Sample ${candidate.id}${candidate.seed !== null && candidate.seed !== undefined ? ` · seed ${candidate.seed}` : ''}`;
      if (candidate.discarded) return `<li class="discarded"><span>${escape(label)}</span><span class="voice-muted">Discarded</span></li>`;
      const stored = candidate.kind === 'gemini_voice' && candidate.provider_voice_id;
      return `<li class="${candidate.id === chosen ? 'chosen' : ''}"><label class="candidate-choice"><input type="radio" name="candidate-${escape(draft.id)}" data-voices-field="candidate" value="${escape(candidate.id)}" ${candidate.id === chosen ? 'checked' : ''}> ${escape(label)}</label><span class="voice-muted">${escape(fmtSeconds(candidate.duration))}${candidate.expires_at ? ` · server copy until ${escape(fmtDate(candidate.expires_at))}` : ''}</span>${candidate.description ? `<p class="candidate-description">${escape(candidate.description)}</p>` : ''}<span class="candidate-actions">${playButton(panel, `${draft.id}:${candidate.id}`, candidate.audio?.url)}<button type="button" class="button text-button" data-voices-action="discard" data-id="${escape(candidate.id)}" ${busy ? 'disabled' : ''}>Discard</button></span>${confirmRow(panel, 'discard', candidate.id, `<p>${stored ? 'Delete this stored voice from your Google project?' : 'Discard this sample?'}</p>`, stored ? 'Delete from project' : 'Discard')}</li>`;
    }).join('')}</ol>` : `<p class="voice-muted designer-empty">No candidates yet. ${draft.provider === 'breeze' ? 'Make samples, hear them, then adjust the description and make more.' : 'Create a voice, hear its example, then keep or discard it.'}</p>`;
    const saveArea = live.length ? `<div class="designer-save">
        ${base ? `<fieldset class="designer-mode"><legend class="field-label">Save as</legend><label class="voice-check"><input type="radio" name="mode-${escape(draft.id)}" data-voices-field="mode" value="version" ${mode === 'version' ? 'checked' : ''}> Version ${escape(nextVersion)} of “${escape(base.name)}” (becomes current)</label><label class="voice-check"><input type="radio" name="mode-${escape(draft.id)}" data-voices-field="mode" value="new" ${mode === 'new' ? 'checked' : ''}> A new voice</label></fieldset>` : ''}
        ${draft.provider === 'breeze' ? `<label class="voice-check"><input type="checkbox" data-voices-field="make-default" ${panel.makeDefault ? 'checked' : ''}> Make it the Breeze default</label>` : ''}
        ${draft.provider === 'gemini' && live.length > 1 ? `<p class="field-help">Saving keeps the chosen voice and deletes the other ${live.length - 1} stored candidate${live.length === 2 ? '' : 's'} from your project.</p>` : ''}
        <div class="voice-confirm-actions">${context ? `<button type="button" class="button primary" data-voices-action="save-assign" ${busy || !chosen ? 'disabled' : ''}>Save &amp; assign to ${escape(context.character_name)}</button>` : ''}<button type="button" class="button ${context ? 'subtle' : 'primary'}" data-voices-action="save" ${busy || !chosen ? 'disabled' : ''}>${panel.pending === 'save' ? 'Saving…' : mode === 'version' ? `Save version ${escape(nextVersion)}` : 'Save voice'}</button></div></div>` : '';
    const gems = storedGemini(draft).length;
    return `<section class="voice-designer" aria-label="Voice designer" data-draft="${escape(draft.id)}">
      <div class="designer-heading"><div><span class="eyebrow">${escape(LABELS[draft.provider] || draft.provider)} voice design</span><h3>${escape(heading)}</h3>${context ? `<p>For ${escape(context.character_name)}${context.book_id && context.book_id !== bookId(panel) ? ' in another book' : ''}</p>` : ''}</div>${context ? `<button type="button" class="button subtle" data-voices-action="back-to-cast">Back to Cast</button>` : ''}</div>
      <div class="designer-fields">
        <label class="field-label" for="draft-name-${escape(draft.id)}">Name</label><input id="draft-name-${escape(draft.id)}" data-voices-field="draft" data-draft-field="name" maxlength="100" value="${escape(draftValue(panel, draft, 'name'))}" placeholder="What you will call this voice">
        <label class="field-label" for="draft-description-${escape(draft.id)}">Description <span>${draft.provider === 'gemini' ? 'permanent traits: age, timbre, accent, baseline delivery' : 'what the voice should sound like'}</span></label><textarea id="draft-description-${escape(draft.id)}" data-voices-field="draft" data-draft-field="description" maxlength="1000" rows="3">${escape(draftValue(panel, draft, 'description'))}</textarea>
        ${draft.provider === 'breeze' ? `<label class="field-label" for="draft-sample-${escape(draft.id)}">Sample text <span>5–15 seconds of speech; it becomes the saved voice's reference</span></label><textarea id="draft-sample-${escape(draft.id)}" data-voices-field="draft" data-draft-field="sample_text" maxlength="1000" rows="3">${escape(draftValue(panel, draft, 'sample_text'))}</textarea>` : ''}
      </div>
      ${generateArea}
      <h4 class="designer-subheading">Candidates</h4>
      ${candidateList}
      ${saveArea}
      ${draft.provider === 'breeze' ? cloneForm(panel, draft) : ''}
      <div class="designer-footer"><button type="button" class="button text-button voice-danger" data-voices-action="abandon" ${busy ? 'disabled' : ''}>Discard this draft</button></div>
      ${confirmRow(panel, 'abandon', draft.id, `<p>Discard this draft?${gems ? ` This deletes ${UI.fmt.plural(gems, 'stored Gemini voice')} from your project.` : ' Its samples are not saved as voices.'}</p>`, gems ? 'Discard and delete' : 'Discard draft')}
    </section>`;
  }

  // Why a provider's voice actions cannot work yet, or '' when they can. Nothing is
  // requested to find out: this reads the last saved status only.
  function setupNeeded(panel, provider) {
    const providers = panel.options.library?.providers || {};
    if (provider === 'breeze') {
      const breeze = providers.breeze || panel.options.status?.breeze || {};
      return breeze.configured === false || breeze.state === 'unconfigured' ? 'Add your Breeze server in Settings to design, clone or check Breeze voices.' : '';
    }
    if (provider === 'gemini') return providers.gemini?.has_api_key === false ? 'Add a Gemini API key in Settings to list or design Gemini voices.' : '';
    return '';
  }
  // Settings is the app's dialog; the app may pass onOpenSettings, otherwise the
  // sidebar's Settings button opens it and this provider's section is expanded.
  function openSetup(panel, provider) {
    if (typeof panel.options.onOpenSettings === 'function') { panel.options.onOpenSettings(provider); return; }
    const doc = typeof document === 'undefined' ? null : document;
    const button = doc?.getElementById?.('settings-button');
    if (!button) { panel.error = 'Open Settings from the sidebar to set this up.'; paint(panel); return; }
    button.click();
    const section = doc.getElementById(`provider-settings-${provider}`);
    if (section) section.open = true;
    doc.getElementById(provider === 'gemini' ? 'api-key' : 'breeze-url')?.focus?.();
  }
  function providerStatus(panel) {
    const providers = panel.options.library?.providers || {};
    const blocked = setupNeeded(panel, panel.filter);
    if (blocked) return UI.callout({tone:'warn', text:blocked, attrs:{id:'voices-setup-reason'},
      actions:UI.button({label:'Set up →', variant:'text', attrs:{'data-voices-action':'setup', 'data-provider':panel.filter}})});
    if (panel.filter === 'breeze') {
      const breeze = providers.breeze || panel.options.status?.breeze || {};
      return `<p class="voices-provider-status">${escape(breeze.message || (breeze.configured ? 'Check the Breeze connection to load server voices.' : 'Add your Breeze server in Settings.'))}${breeze.checked_at ? ` · checked ${escape(fmtDate(breeze.checked_at))}` : ''}</p><button type="button" class="button subtle" data-voices-action="refresh-breeze" ${panel.pending ? 'disabled' : ''}>Check Breeze voices</button>`;
    }
    const gemini = providers.gemini || {};
    return `<p class="voices-provider-status">${escape(gemini.message || 'Refresh to list the voices stored in your Google project.')}${Number.isInteger(gemini.stored_count) ? ` · ${escape(gemini.stored_count)} of ${escape(gemini.limit || 200)} stored voices used` : ''}</p><button type="button" class="button subtle" data-voices-action="refresh-gemini" ${panel.pending || gemini.has_api_key === false ? 'disabled' : ''}>Refresh Gemini voices</button>`;
  }

  function paint(panel) {
    const library = panel.options.library;
    const draft = activeDraft(panel);
    if (panel.draftId && !draft && library) panel.draftId = null;
    const voices = libraryVoices(library, panel.filter);
    const others = drafts(panel).filter(item => item.id !== panel.draftId);
    const blocked = setupNeeded(panel, panel.filter);
    const html = `<div class="voices-toolbar">${UI.choice({kind:'segmented', label:'Voice service', name:'voices-provider', value:panel.filter,
        options:LIBRARY_PROVIDERS.map(provider => ({value:provider, label:LABELS[provider]}))})}${UI.button({label:`Design a ${LABELS[panel.filter]} voice`, variant:'primary',
        disabled:Boolean(panel.pending || blocked), attrs:{'data-voices-action':'new', 'aria-describedby':blocked ? 'voices-setup-reason' : false}})}</div>
      ${blocked ? providerStatus(panel) : `<div class="voices-provider">${providerStatus(panel)}</div>`}
      <p class="message" data-tone="${panel.error ? 'bad' : 'neutral'}" role="${panel.error ? 'alert' : 'status'}">${escape(panel.error || panel.message || '')}</p>
      ${!library ? '<p class="voice-muted">Loading voices…</p>' : ''}
      ${draft ? designer(panel, draft) : ''}
      ${others.length ? `<section class="voices-drafts" aria-label="Unfinished voices"><h4>Unfinished voices</h4><ul>${others.map(item => {
        const gems = storedGemini(item).length;
        return `<li><span><strong>${escape(item.name || 'Untitled voice')}</strong> <span class="voice-muted">${escape(LABELS[item.provider] || item.provider)} · ${escape(UI.fmt.plural(liveCandidates(item).length, 'candidate'))}${item.context?.character_name ? ` · for ${escape(item.context.character_name)}` : ''}${gems ? ` · ${gems} stored in your Google project` : ''}</span></span><span class="voice-confirm-actions"><button type="button" class="button text-button" data-voices-action="resume" data-id="${escape(item.id)}">Resume</button><button type="button" class="button text-button voice-danger" data-voices-action="abandon" data-id="${escape(item.id)}" ${panel.pending || item.busy ? 'disabled' : ''}>Discard</button></span>${confirmRow(panel, 'abandon', item.id, `<p>Discard this draft?${gems ? ` This deletes ${UI.fmt.plural(gems, 'stored Gemini voice')} from your project.` : ' Its samples are not saved as voices.'}</p>`, gems ? 'Discard and delete' : 'Discard draft')}</li>`;
      }).join('')}</ul></section>` : ''}
      ${panel.filter === 'breeze' && !draft ? cloneForm(panel, null) : ''}
      <div class="voice-grid">${voices.length ? voices.map(voice => voiceCard(panel, voice)).join('') : library ? `<p class="empty-state">${panel.filter === 'breeze' ? `No Breeze voices yet.${blocked ? '' : ' Check Breeze voices to import the ones on your server, or design a new one.'}` : 'No Gemini voices in your library yet. Built-in Gemini voices are always available in Cast.'}</p>` : ''}</div>`;
    if (panel.container.innerHTML === html) return;
    const focused = typeof document === 'undefined' ? null : document.activeElement;
    const within = focused && panel.container.contains?.(focused);
    const focusSelector = within ? (focused.dataset?.draftField ? `[data-draft-field="${focused.dataset.draftField}"]` :
      focused.dataset?.voicesAction ? `[data-voices-action="${focused.dataset.voicesAction}"]${focused.dataset.id ? `[data-id="${focused.dataset.id}"]` : ''}` : null) : null;
    panel.container.innerHTML = html;
    if (focusSelector) panel.container.querySelector?.(focusSelector)?.focus?.({preventScroll:true});
  }

  async function run(panel, action, operation, {refresh = true} = {}) {
    if (panel.pending) return null;
    panel.pending = action;
    panel.error = '';
    panel.message = '';
    paint(panel);
    try {
      const result = await operation();
      if (refresh) await panel.options.onChange?.();
      return result;
    } catch (error) {
      panel.error = error.message;
      // A failed Gemini refresh or a partly failed deletion is recorded; reload so the library shows it.
      if (refresh && error.code === 'provider_error') {
        try { await panel.options.onChange?.(); } catch { /* keep the original error */ }
      }
      return null;
    } finally {
      panel.pending = null;
      paint(panel);
    }
  }
  async function syncDraft(panel, draft) {
    const edits = panel.edits[draft.id] || {};
    const changed = Object.fromEntries(Object.entries(edits).filter(([field, value]) => value !== (draft[field] || '')));
    if (!Object.keys(changed).length) return draft;
    const updated = await request(`/api/voices/drafts/${encode(draft.id)}`, {method:'PATCH', body:changed});
    delete panel.edits[draft.id];
    replaceDraft(panel, updated);
    return updated;
  }
  function replaceDraft(panel, draft) {
    const library = panel.options.library;
    if (!library || !draft) return;
    const list = library.drafts || (library.drafts = []);
    const index = list.findIndex(item => item.id === draft.id);
    if (draft.status && draft.status !== 'open') { if (index >= 0) list.splice(index, 1); return; }
    if (index >= 0) list[index] = draft; else list.unshift(draft);
  }
  function stopAudition() {
    if (!player) return;
    player.pause?.();
    playingKey = null;
  }
  function toggleAudition(panel, key, url) {
    if (!player) return;
    if (playingKey === key && playingPanel === panel) { stopAudition(); paint(panel); return; }
    panel.options.onBeforeAudition?.();
    playingKey = key; playingPanel = panel;
    player.src = url;
    const started = player.play?.();
    if (started?.catch) started.catch(() => { if (playingKey === key) { playingKey = null; panel.error = 'The example could not start.'; paint(panel); } });
    paint(panel);
  }

  async function createDraft(panel, body) {
    const draft = await request('/api/voices/drafts', {method:'POST', body:{...body, ...(bookId(panel) && !body.book_id ? {book_id:bookId(panel)} : {})}});
    replaceDraft(panel, draft);
    panel.draftId = draft.id;
    panel.filter = draft.provider;
    panel.confirm = null;
    panel.gemini.confirmed = false;
    return draft;
  }

  async function act(panel, target) {
    const action = target.dataset.voicesAction;
    const library = panel.options.library;
    const id = target.dataset.id;
    const draft = activeDraft(panel);
    if (action === 'setup') { openSetup(panel, target.dataset.provider || panel.filter); return; }
    if (action === 'play') { toggleAudition(panel, target.dataset.key, target.dataset.url); return; }
    if (action === 'cancel-confirm') { panel.confirm = null; paint(panel); return; }
    if (action === 'edit') { panel.editing = id; panel.confirm = null; paint(panel); return; }
    if (action === 'cancel-edit') { panel.editing = null; paint(panel); return; }
    if (action === 'back-to-cast') { panel.options.onBackToCast?.(draft?.context || null); return; }
    if (action === 'resume') { panel.draftId = id; panel.filter = drafts(panel).find(item => item.id === id)?.provider || panel.filter; panel.confirm = null; paint(panel); return; }
    if (action === 'make-current') { panel.confirm = {kind:'current', id:`${id}:${target.dataset.version}`, voiceId:id, version:Number(target.dataset.version)}; paint(panel); return; }
    if (action === 'set-default') { panel.confirm = {kind:'default', id}; paint(panel); return; }
    if (action === 'delete') { const voice = findVoice(library, id); panel.confirm = {kind:'delete', id, server:voice?.origin !== 'imported'}; paint(panel); return; }
    if (action === 'discard') { panel.confirm = {kind:'discard', id, draftId:draft?.id}; paint(panel); return; }
    if (action === 'abandon') { const target = id || draft?.id; if (target) { panel.confirm = {kind:'abandon', id:target}; paint(panel); } return; }
    if (action === 'confirm') { await confirmAction(panel); return; }
    if (action === 'new') { if (!setupNeeded(panel, panel.filter)) await run(panel, 'new', () => createDraft(panel, {provider:panel.filter})); return; }
    if (action === 'iterate') {
      const voice = findVoice(library, id);
      if (voice && !setupNeeded(panel, voice.provider)) await run(panel, 'iterate', () => createDraft(panel, {provider:voice.provider, base_voice_id:voice.id}));
      return;
    }
    if (action === 'refresh-breeze') { await run(panel, 'refresh', async () => { await panel.options.onRefreshBreeze?.(); }, {refresh:false}); return; }
    if (action === 'refresh-gemini') { await run(panel, 'refresh', () => request('/api/voices/gemini/refresh', {method:'POST', body:{}})); return; }
    if (!draft) return;
    if (action === 'generate') {
      if (draft.provider === 'gemini' && !panel.gemini.confirmed) return;
      const body = draft.provider === 'breeze' ? {book_id:bookId(panel), count:panel.count}
        : {book_id:bookId(panel), language_code:panel.gemini.language_code || 'en-US', gender:panel.gemini.gender || null, confirm_cost:true};
      // A billed create needs a fresh confirmation for every click.
      panel.gemini.confirmed = false;
      await run(panel, 'generate', async () => {
        const current = await syncDraft(panel, draft);
        const updated = await request(`/api/voices/drafts/${encode(current.id)}/generate`, {method:'POST', body});
        replaceDraft(panel, updated);
        const newest = liveCandidates(updated).at(-1);
        if (newest) panel.choice[updated.id] = newest.id;
        panel.message = draft.provider === 'breeze' ? 'Samples ready. Hear them, then save one, or adjust the description and make more.' : 'Voice created. Hear its example, then keep or discard it.';
      });
      return;
    }
    if (action === 'save' || action === 'save-assign') {
      const live = liveCandidates(draft);
      const candidate = live.find(item => item.id === panel.choice[draft.id]) || live.at(-1);
      if (!candidate) return;
      const base = draft.base_voice_id ? findVoice(library, draft.base_voice_id) : null;
      const mode = base && panel.mode[draft.id] !== 'new' ? 'version' : 'new';
      const assign = action === 'save-assign' && draft.context ? {book_id:draft.context.book_id, character_id:draft.context.character_id} : null;
      const provider = draft.provider, context = draft.context;
      const result = await run(panel, 'save', async () => {
        const current = await syncDraft(panel, draft);
        const name = (current.name || '').trim() || base?.name || context?.character_name || 'New voice';
        const saved = await request(`/api/voices/drafts/${encode(current.id)}/save`, {method:'POST', body:{candidate_id:candidate.id, name, mode,
          ...(assign ? {assign} : {}), ...(provider === 'breeze' && panel.makeDefault ? {make_default:true} : {})}});
        panel.draftId = null;
        panel.makeDefault = false;
        replaceDraft(panel, {id:current.id, status:'saved'});
        panel.message = saved?.assignment_error ? `Saved “${saved.voice?.name || name}”, but it was not assigned: ${saved.assignment_error}` : `Saved “${saved?.voice?.name || name}”.`;
        if (saved?.assignment_error) panel.error = panel.message;
        return saved;
      });
      if (result) await panel.options.onSaved?.(result, {provider, assigned:Boolean(assign && !result.assignment_error), context});
    }
  }

  async function confirmAction(panel) {
    const confirm = panel.confirm;
    if (!confirm) return;
    panel.confirm = null;
    if (confirm.kind === 'current') {
      await run(panel, 'current', () => request(`/api/voices/${encode(confirm.voiceId)}/current`, {method:'POST', body:{version:confirm.version}}));
    } else if (confirm.kind === 'default') {
      await run(panel, 'default', () => request('/api/voices/defaults', {method:'POST', body:{provider:'breeze', voice_id:confirm.id}}));
    } else if (confirm.kind === 'delete') {
      await run(panel, 'delete', async () => {
        await request(`/api/voices/${encode(confirm.id)}?server=${confirm.server ? 'true' : 'false'}`, {method:'DELETE'});
        panel.message = 'Voice deleted.';
      });
    } else if (confirm.kind === 'discard') {
      await run(panel, 'discard', async () => {
        const updated = await request(`/api/voices/drafts/${encode(confirm.draftId)}/candidates/${encode(confirm.id)}/discard`, {method:'POST', body:{}});
        replaceDraft(panel, updated);
      });
    } else if (confirm.kind === 'abandon') {
      await run(panel, 'abandon', async () => {
        const updated = await request(`/api/voices/drafts/${encode(confirm.id)}/abandon`, {method:'POST', body:{}});
        replaceDraft(panel, {...(updated || {}), id:confirm.id, status:'abandoned'});
        if (panel.draftId === confirm.id) panel.draftId = null;
        panel.message = 'Draft discarded.';
      });
    }
  }

  async function submitClone(panel, form) {
    const draft = activeDraft(panel);
    const data = new FormData(form);
    data.set('consent', form.elements.consent?.checked ? 'true' : '');
    const context = draft?.context?.character_id ? draft.context : null;
    // A character context assigns the new voice; a book alone only attributes the request.
    if (context) { data.set('book_id', context.book_id); data.set('character_id', context.character_id); }
    else if (bookId(panel)) data.set('book_id', bookId(panel));
    const result = await run(panel, 'clone', async () => {
      const saved = await request('/api/voices/breeze/clone', {method:'POST', form:data});
      if (draft) {
        // The recording replaced this draft's previews; close it without deleting anything.
        try { await request(`/api/voices/drafts/${encode(draft.id)}/abandon`, {method:'POST', body:{}}); } catch { /* It stays listed. */ }
        replaceDraft(panel, {id:draft.id, status:'abandoned'});
        panel.draftId = null;
      }
      panel.cloneOpen = false;
      panel.message = saved?.assignment_error ? `Cloned “${saved.voice?.name || ''}”, but it was not assigned: ${saved.assignment_error}` : `Cloned “${saved?.voice?.name || ''}”.`;
      if (saved?.assignment_error) panel.error = panel.message;
      return saved;
    });
    if (result) await panel.options.onSaved?.(result, {provider:'breeze', assigned:Boolean(context && !result.assignment_error), context});
  }

  function render(container, options = {}) {
    if (!container) return;
    let panel = panels.get(container);
    if (!panel) {
      panel = {container, options, filter:options.provider && LIBRARY_PROVIDERS.includes(options.provider) ? options.provider : 'breeze',
        draftId:null, edits:{}, choice:{}, mode:{}, count:2, makeDefault:false, pending:null, error:'', message:'', confirm:null,
        editing:null, cloneOpen:false, openVersions:new Set(), gemini:{language_code:'en-US', gender:'', confirmed:false}};
      panels.set(container, panel);
      // The service switch is a BardicUI choice: click and arrow keys select it.
      UI.bindChoices(container, choice => {
        if (choice.name !== 'voices-provider' || !LIBRARY_PROVIDERS.includes(choice.value)) return;
        panel.filter = choice.value; panel.confirm = null; paint(panel);
      });
      container.addEventListener('click', event => {
        const target = event.target.closest?.('[data-voices-action]');
        if (target && !target.disabled) { event.preventDefault?.(); void act(panel, target); }
      });
      container.addEventListener('input', event => {
        const field = event.target.dataset?.voicesField;
        if (field === 'draft' && panel.draftId) {
          (panel.edits[panel.draftId] ||= {})[event.target.dataset.draftField] = event.target.value;
        } else if (field === 'language_code') panel.gemini.language_code = event.target.value.trim();
      });
      container.addEventListener('change', event => {
        const field = event.target.dataset?.voicesField;
        if (field === 'count') { panel.count = Math.min(3, Math.max(1, Number(event.target.value) || 2)); paint(panel); }
        else if (field === 'gender') panel.gemini.gender = event.target.value;
        else if (field === 'confirm-cost') { panel.gemini.confirmed = Boolean(event.target.checked); paint(panel); }
        else if (field === 'candidate' && panel.draftId) { panel.choice[panel.draftId] = event.target.value; paint(panel); }
        else if (field === 'mode' && panel.draftId) { panel.mode[panel.draftId] = event.target.value; paint(panel); }
        else if (field === 'make-default') panel.makeDefault = Boolean(event.target.checked);
        else if (field === 'delete-server' && panel.confirm?.kind === 'delete') panel.confirm.server = Boolean(event.target.checked);
      });
      container.addEventListener('toggle', event => {
        const versions = event.target.dataset?.voiceVersions;
        if (versions) { if (event.target.open) panel.openVersions.add(versions); else panel.openVersions.delete(versions); }
        if (event.target.dataset?.voicesClone !== undefined) panel.cloneOpen = event.target.open;
      }, true);
      container.addEventListener('submit', event => {
        event.preventDefault?.();
        const form = event.target;
        if (form.dataset?.voicesForm === 'clone') { void submitClone(panel, form); return; }
        const voiceId = form.dataset?.voiceEdit;
        if (voiceId) {
          const body = {name:form.elements.name.value, description:form.elements.description.value};
          void run(panel, 'edit', async () => {
            await request(`/api/voices/${encode(voiceId)}`, {method:'PATCH', body});
            panel.editing = null;
            panel.message = 'Voice updated.';
          });
        }
      });
    }
    panel.options = options;
    paint(panel);
  }
  // Open a draft (for example one created from a Cast card) and show it.
  function openDraft(container, draft) {
    const panel = panels.get(container);
    if (!panel || !draft) return;
    replaceDraft(panel, draft);
    panel.draftId = draft.id;
    if (LIBRARY_PROVIDERS.includes(draft.provider)) panel.filter = draft.provider;
    panel.confirm = null;
    panel.error = '';
    panel.message = draft.context?.character_name ? `Designing a voice for ${draft.context.character_name}. Their description and one of their lines are filled in.` : '';
    paint(panel);
  }
  function stop() { stopAudition(); }
  window.BardicVoices = {render, openDraft, stop, cast};
})();
