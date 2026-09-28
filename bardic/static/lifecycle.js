/* Book lifecycle: where a book stands and what to do next. Exposes window.BardicLifecycle.
   `compute` is pure: it reads the book object the client already has, the analysis-pipeline
   overview (GET /api/books/{id}/analysis-pipeline) when it has loaded, and the narration
   service chosen for recording. Each stage has one data source and exactly one state:

     Analyze  accepted results for the required steps (directing staleness is ignored:
              accepting any profile marks every directing chapter stale, so it cannot mean "not done")
     Cast     speaking characters with a voice for the record service (Default counts where
              that service has a default voice)
     Script   passages with a speaker other than Unassigned
     Record   passages with a current Studio take (Export packages only these; one-narrator
              listening audio and performances are not counted)

   Read & listen is always available, so it is not a stage. `strip` renders the model with
   BardicUI.steps: the compact strip, or stage cards when expanded. Tests: tests/lifecycle_test.js. */
(() => {
  'use strict';
  const root = typeof window !== 'undefined' ? window : globalThis;

  // Labels from the step registry (bardic/pipeline/steps/*.py); tests/copy_lint_test.js checks them.
  const REQUIRED_STEPS = Object.freeze([
    ['discovery', 'Character discovery'],
    ['profiles', 'Character profiles'],
    ['directing', 'Speakers & delivery'],
  ]);
  const SERVICE_NAMES = {system:'Mac', gemini:'Gemini', breeze:'Breeze'};
  const TAB_NAMES = {read:'Read & listen', analysis:'Analyze', cast:'Cast', studio:'Script & record', details:'Details'};

  const count = value => Number(value) || 0;
  const plural = (n, one, many = `${one}s`) => `${Number(n).toLocaleString('en-US')} ${n === 1 ? one : many}`;
  const joined = labels => labels.length > 1 ? `${labels.slice(0, -1).join(', ')} and ${labels.at(-1)}` : labels[0] || '';
  // The server presents an out-of-date take as null audio, so a take with a URL is playable.
  const playable = segment => Boolean(segment?.audio?.url);

  // The explicit voice a character has for one service (the same fields voices.js encodes). The server
  // folds the legacy single-provider fields into `voices` and does not present them.
  function explicitVoice(character, provider) {
    const selection = character?.voices?.[provider];
    return Boolean(selection && typeof selection === 'object' && (selection.library || selection.id));
  }

  function analyzeStage(overview) {
    const uses = REQUIRED_STEPS.map(([, label]) => label);
    const base = {id:'analyze', label:'Analyze', tab:'analysis', uses};
    if (overview?.error) {
      return {...base, state:'unknown', stateLabel:'Status unknown', done:false, known:true,
        detail:'The analysis status could not be read. Open Analyze to see it.', next:{label:'Open Analyze', tab:'analysis'}};
    }
    if (!overview || !Array.isArray(overview.steps)) {
      return {...base, state:'loading', stateLabel:'Checking…', done:false, known:false, detail:'Reading the analysis status for this book.'};
    }
    const byId = new Map(overview.steps.map(step => [step.id, step]));
    const done = REQUIRED_STEPS.filter(([id]) => {
      const step = byId.get(id);
      if (!step) return false;
      const total = count(step.total_scopes), accepted = count(step.accepted_scopes);
      return Boolean(step.has_accepted ?? accepted > 0) && (total === 0 || accepted >= total);
    });
    const started = overview.steps.some(step => step.has_accepted ?? count(step.accepted_scopes) > 0);
    const review = overview.steps.reduce((sum, step) => sum + count(step.pending_versions), 0);
    const missing = REQUIRED_STEPS.filter(item => !done.includes(item)).map(([, label]) => label);
    if (overview.active_run) {
      return {...base, state:'running', stateLabel:'Running', done:false, known:true, review,
        detail:'An analysis run is in progress. Its progress shows in the job banner.', next:{label:'See analysis progress', tab:'analysis'}};
    }
    if (review) {
      // Next opens Analyze on the first step with a version waiting (BardicAnalysisPipeline.selectStep).
      const waiting = overview.steps.find(step => count(step.pending_versions) > 0)?.id;
      return {...base, state:'needs_review', stateLabel:`${plural(review, 'result')} to review`, done:false, known:true, review,
        detail:`${plural(review, 'new version')} ${review === 1 ? 'is' : 'are'} waiting for you to accept or set aside.`,
        next:{label:'Review analysis results', tab:'analysis', ...(waiting ? {step:waiting} : {})}};
    }
    if (!missing.length) {
      return {...base, state:'complete', stateLabel:'Done', done:true, known:true, review:0,
        detail:`Accepted results from ${joined(uses)} are in use.`};
    }
    // "Run <step>" opens that step; a fresh book lets Analyze choose its first actionable step.
    const first = REQUIRED_STEPS.find(item => !done.includes(item));
    return {...base, state:started ? 'in_progress' : 'not_started', stateLabel:started ? `${done.length} of ${REQUIRED_STEPS.length} steps` : 'Not started',
      done:false, known:true, review:0, detail:`Still needed: ${joined(missing)}.`,
      next:started ? {label:`Run ${first[1]}`, tab:'analysis', step:first[0]} : {label:'Analyze the story', tab:'analysis'}};
  }

  function speakingCharacters(book) {
    const characters = book?.characters || [], segments = book?.segments || [];
    const speakers = new Set(segments.map(segment => segment.speaker_id).filter(Boolean));
    const speaking = characters.filter(character => speakers.has(character.id));
    return speaking.length || segments.length ? speaking : characters;
  }

  function castStage(book, provider, defaultVoice) {
    const service = SERVICE_NAMES[provider] || provider;
    const speaking = speakingCharacters(book);
    const explicit = speaking.filter(character => explicitVoice(character, provider)).length;
    const onDefault = defaultVoice ? speaking.length - explicit : 0;
    const voiced = explicit + onDefault;
    const base = {id:'cast', label:'Cast', tab:'cast', uses:[`${service} voices`], provider};
    const detail = `${voiced} of ${plural(speaking.length, 'speaking character')} ${voiced === 1 ? 'has' : 'have'} a ${service} voice${onDefault ? ` (${onDefault} on the default voice)` : ''}.`;
    if (!speaking.length) return {...base, state:'not_started', stateLabel:'No characters', done:false, known:true, detail:'No characters yet. Analyze the story to find them.', next:{label:'Choose voices', tab:'cast'}};
    if (voiced >= speaking.length) return {...base, state:'complete', stateLabel:`${speaking.length} voiced`, done:true, known:true, detail};
    return {...base, state:voiced ? 'in_progress' : 'not_started', stateLabel:`${voiced} of ${speaking.length} voiced`, done:false, known:true, detail,
      next:{label:'Choose voices', tab:'cast'}};
  }

  function scriptStage(book) {
    const segments = book?.segments || [];
    const known = new Set((book?.characters || []).map(character => character.id));
    const open = segments.filter(segment => !segment.speaker_id || segment.speaker_id === 'unassigned' || !known.has(segment.speaker_id)).length;
    const assigned = segments.length - open;
    const base = {id:'script', label:'Script', tab:'studio', target:'script', uses:['Speakers & delivery results', 'your speaker fixes']};
    if (!segments.length) return {...base, state:'not_started', stateLabel:'No passages', done:false, known:true, detail:'This book has no passages yet.'};
    const detail = `${assigned} of ${plural(segments.length, 'passage')} ${assigned === 1 ? 'has' : 'have'} a speaker.`;
    if (!open) return {...base, state:'complete', stateLabel:'Done', done:true, known:true, detail};
    return {...base, state:assigned ? 'in_progress' : 'not_started', stateLabel:`${open} unassigned`, done:false, known:true,
      detail:`${detail} ${plural(open, 'passage')} ${open === 1 ? 'needs' : 'need'} a speaker.`, next:{label:`Assign ${plural(open, 'speaker')}`, tab:'studio', target:'script'}};
  }

  function recordStage(book) {
    const segments = book?.segments || [];
    const recorded = segments.filter(playable).length;
    const base = {id:'record', label:'Record', tab:'studio', target:'record', uses:['Studio recordings (Export packages these)']};
    const detail = `${recorded} of ${plural(segments.length, 'passage')} ${recorded === 1 ? 'has' : 'have'} a Studio recording.`;
    if (segments.length && recorded >= segments.length) {
      return {...base, state:'complete', stateLabel:'Done', done:true, known:true, detail, next:{label:'Export the audiobook', tab:'studio', target:'export'}};
    }
    return {...base, state:recorded ? 'in_progress' : 'not_started', stateLabel:recorded ? `${recorded} of ${segments.length}` : 'Not started', done:false, known:true,
      detail, next:{label:recorded ? 'Record the rest' : 'Record the book', tab:'studio', target:'record'}};
  }

  /**
   * @param {object} input
   * @param {object} input.book            the book object from GET /api/books/{id}
   * @param {object|null} input.overview   GET /api/books/{id}/analysis-pipeline, or null while loading
   * @param {string} input.recordProvider  narration service chosen for recording (system, gemini or breeze)
   * @param {boolean} input.defaultVoice   whether that service has a usable default voice
   * @returns {{stages:object[], next:object|null, current:string|null}}
   */
  function compute({book, overview = null, recordProvider = 'system', defaultVoice = true} = {}) {
    const stages = [analyzeStage(overview), castStage(book, recordProvider, defaultVoice), scriptStage(book), recordStage(book)];
    // One Next: the earliest stage that is not done. Nothing is suggested while a stage is still loading,
    // so the suggestion never flips once the analysis status arrives.
    const pending = stages.find(stage => !stage.done);
    let next = null, current = null;
    if (stages.every(stage => stage.known)) {
      const source = pending || stages[stages.length - 1];
      next = source.next || null;
      current = pending ? pending.id : null;
    }
    return {stages, next, current};
  }

  function strip(model, {expanded = false, label = 'Book progress'} = {}) {
    const ui = root.BardicUI;
    if (!ui || !model) return '';
    const nextAttrs = next => ({'data-lifecycle-go':next.tab, 'data-lifecycle-target':next.target || false, 'data-lifecycle-step':next.step || false});
    const steps = model.stages.map(stage => ({
      id:stage.id, label:stage.label, state:stage.state, stateLabel:stage.stateLabel, current:stage.id === model.current,
      detail:stage.detail, uses:stage.uses,
      action:{label:`Open ${TAB_NAMES[stage.tab]}`, attrs:{'data-lifecycle-go':stage.tab, 'data-lifecycle-target':stage.target || false}},
    }));
    const next = model.next ? {label:`Next: ${model.next.label}`, attrs:nextAttrs(model.next)} : null;
    const toggle = `<button type="button" class="lifecycle-toggle" data-lifecycle-toggle aria-expanded="${expanded}">${expanded ? 'Hide stages' : 'Show stages'}</button>`;
    if (!expanded) return `<div class="lifecycle" data-expanded="false">${ui.steps({label, steps, next})}${toggle}</div>`;
    const nextButton = next ? ui.button({variant:'primary', size:'small', label:`${next.label} →`, attrs:next.attrs}) : '';
    return `<div class="lifecycle" data-expanded="true"><div class="lifecycle-bar"><p class="lifecycle-title">${ui.esc(label)}</p>${nextButton}${toggle}</div>${ui.steps({label, variant:'cards', steps})}</div>`;
  }

  root.BardicLifecycle = Object.freeze({REQUIRED_STEPS, TAB_NAMES, compute, strip, explicitVoice, playable});
})();
