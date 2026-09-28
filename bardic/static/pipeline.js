/* Read-only analysis explorer (a diagnostics surface). Saved results (artifacts),
   requests (transport attempts) and checks (validation) are intentionally presented
   as separate facts. No control starts model work. Escaping and money use BardicUI. */
(() => {
  'use strict';
  const panels = new WeakMap();
  const UI = window.BardicUI;
  const escape = UI.esc;
  const path = value => encodeURIComponent(value);
  const number = value => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('en-US') : '—';
  const money = value => UI.fmt.money(value, {precise:true, unknown:'Unknown'});
  const labels = {not_started:'Not started', pending:'Pending', partial:'Partial', current:'Current', complete:'Complete', completed:'Complete',
    stale:'Out of date', running:'Running', queued:'Queued', failed:'Failed', interrupted:'Interrupted', cancelled:'Cancelled',
    budget_limited:'Spending limit reached', planned:'Planned', ready:'Ready', available:'Available', blocked:'Waiting', unknown:'Unknown',
    reserved:'Reserved', received:'Response received', uncertain:'Outcome uncertain', not_sent:'Not sent (connection failed)', accepted:'Accepted', rejected:'Rejected',
    interrupted_unknown:'Interrupted · outcome unknown'};
  const status = value => labels[value] || String(value || 'Unknown').replaceAll('_', ' ');
  const statusClass = value => Object.hasOwn(labels, value) ? value : 'unknown';
  const when = value => {
    if (!value) return 'Time not recorded';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? 'Time not recorded' : date.toLocaleString();
  };
  // Step cards also report accepted results that are out of date and versions waiting in Analyze.
  const review = stage => [stage.stale_count ? `${number(stage.stale_count)} out of date` : '',
    stage.candidate_count ? `${number(stage.candidate_count)} waiting for review` : ''].filter(Boolean).join(' · ');
  const recent = values => [...values].sort((a, b) =>
    (Date.parse(b.updated_at || b.created_at) || 0) - (Date.parse(a.updated_at || a.created_at) || 0));
  const base = panel => `/api/books/${path(panel.book.id)}`;

  async function read(url) {
    const response = await fetch(url, {headers:{Accept:'application/json'}});
    let value;
    try { value = await response.json(); } catch { value = null; }
    if (!response.ok) {
      const detail = value?.detail;
      throw new Error(typeof detail === 'string' ? detail : `Request failed (${response.status}). Try again.`);
    }
    return value;
  }

  function message(panel, text, error = false) {
    const node = panel.container.querySelector('[data-pipeline-message]');
    if (!node) return;
    node.textContent = text;
    node.classList.toggle('pipeline-error', error);
    node.setAttribute('role', error ? 'alert' : 'status');
  }

  function controls(panel) {
    const refresh = panel.container.querySelector('[data-pipeline-action="refresh"]');
    if (refresh) refresh.disabled = panel.loading;
    const previous = panel.container.querySelector('[data-pipeline-action="previous"]');
    const next = panel.container.querySelector('[data-pipeline-action="next"]');
    if (previous) previous.disabled = panel.artifactLoading || panel.offset === 0;
    if (next) next.disabled = panel.artifactLoading || panel.offset + panel.limit >= (panel.artifacts?.total || 0);
    const search = panel.container.querySelector('[data-pipeline-action="search"]');
    if (search) search.disabled = panel.searchLoading;
  }

  function paintStages(panel) {
    const node = panel.container.querySelector('[data-pipeline-stages]');
    if (!node) return;
    if (!panel.snapshot) { node.innerHTML = '<p class="pipeline-help">Loading saved analysis status…</p>'; return; }
    const stages = panel.snapshot.stages || [];
    const byId = new Map(stages.map(stage => [stage.id, stage.label]));
    node.innerHTML = `<ol class="pipeline-stage-grid">${stages.map(stage => `<li class="pipeline-stage"><button type="button" data-pipeline-stage="${escape(stage.id)}" aria-pressed="${panel.stage === stage.id}"><span class="pipeline-stage-title">${escape(stage.label)}</span><span class="pipeline-badge ${statusClass(stage.status)}">${escape(status(stage.status))}</span><span class="pipeline-stage-count">${typeof stage.total === 'number' ? `${number(stage.completed ?? 0)} / ${number(stage.total)} ${escape(stage.unit_label || 'items')}` : typeof stage.completed === 'number' ? `${number(stage.completed)} ${escape(stage.unit_label || 'saved items')}` : 'Count not available'}</span>${review(stage) ? `<span class="pipeline-help">${escape(review(stage))}</span>` : ''}<span class="pipeline-help">${escape(UI.fmt.plural(stage.artifact_count ?? 0, 'saved result'))}</span></button>${stage.dependencies?.length ? `<p class="pipeline-dependencies"><span>Uses</span> ${stage.dependencies.map(id => `<button type="button" data-pipeline-stage="${escape(id)}">${escape(byId.get(id) || id)}</button>`).join(' · ')}</p>` : '<p class="pipeline-dependencies">Source step</p>'}${stage.note ? `<p class="pipeline-stage-note">${escape(stage.note)}</p>` : ''}</li>`).join('')}</ol>${panel.snapshot.capabilities?.word_alignment === false ? '<p class="pipeline-note">Word alignment is planned. Current read-along timing follows passage boundaries.</p>' : ''}${(panel.snapshot.notes || []).map(note => `<p class="pipeline-help">${escape(note)}</p>`).join('')}`;
  }

  function paintFilters(panel) {
    const node = panel.container.querySelector('[data-pipeline-filters]');
    if (!node) return;
    const kinds = [...new Set([...(panel.snapshot?.artifact_kinds || []), ...(panel.artifacts?.items || []).map(item => item.kind), panel.kind].filter(Boolean))].sort();
    const stages = panel.snapshot?.stages || [];
    node.innerHTML = `<label>Step<select name="pipeline_stage" aria-label="Step"><option value="">All steps</option>${stages.map(stage => `<option value="${escape(stage.id)}" ${stage.id === panel.stage ? 'selected' : ''}>${escape(stage.label)}</option>`).join('')}</select></label><label>Result type<select name="pipeline_kind" aria-label="Result type"><option value="">All types</option>${kinds.map(kind => `<option value="${escape(kind)}" ${kind === panel.kind ? 'selected' : ''}>${escape(kind.replaceAll('_', ' '))}</option>`).join('')}</select></label><label>Versions<select name="pipeline_current" aria-label="Versions"><option value="" ${panel.current === '' ? 'selected' : ''}>All saved versions</option><option value="true" ${panel.current === 'true' ? 'selected' : ''}>Current outputs</option><option value="false" ${panel.current === 'false' ? 'selected' : ''}>Historical outputs</option></select></label>`;
  }

  function paintArtifacts(panel) {
    const node = panel.container.querySelector('[data-pipeline-artifacts]');
    if (!node) return;
    if (!panel.artifacts) { node.innerHTML = '<p class="pipeline-help">Loading saved results…</p>'; return; }
    const items = panel.artifacts.items || [];
    const total = panel.artifacts.total || 0;
    node.innerHTML = `${items.length ? `<ol class="pipeline-artifact-list">${items.map(item => `<li><button type="button" data-pipeline-artifact="${escape(item.id)}" aria-pressed="${panel.selectedArtifact === item.id && panel.selectedArtifactBook === panel.book.id}"><span><strong>${escape(item.label || item.kind || 'Saved output')}</strong><small>${escape((item.kind || 'result').replaceAll('_', ' '))} · ${escape(item.provider || 'Local / saved')} ${escape(item.model || '')} · ${escape(when(item.created_at))}</small></span><span class="pipeline-badge ${item.is_current === true ? 'current' : item.is_current === false ? 'stale' : 'unknown'}">${item.is_current === true ? 'Current' : item.is_current === false ? 'Historical' : 'Currency unknown'}</span></button></li>`).join('')}</ol>` : '<p class="pipeline-help">No saved results match these filters. Each step saves its results as it finishes.</p>'}<div class="pipeline-pagination"><span>${items.length ? `${number(panel.offset + 1)}–${number(panel.offset + items.length)}` : '0'} of ${number(total)} saved versions</span><div><button type="button" class="button subtle" data-pipeline-action="previous" ${panel.offset === 0 ? 'disabled' : ''}>Previous</button><button type="button" class="button subtle" data-pipeline-action="next" ${panel.offset + panel.limit >= total ? 'disabled' : ''}>Next</button></div></div>`;
    controls(panel);
  }

  function paintInspector(panel, artifact) {
    const node = panel.container.querySelector('[data-pipeline-inspector]');
    if (!node) return;
    const encoded = JSON.stringify(artifact.payload ?? null, null, 2);
    const truncated = encoded.length > 200000;
    const owner = artifact.book_id || panel.selectedArtifactBook || panel.book.id;
    const dependencyOwners = new Map((artifact.dependency_links || []).map(link => [link.id, link.book_id]));
    const dependencyMarkup = artifact.dependencies?.length
      ? `<div class="pipeline-artifact-dependencies"><span>Recorded dependencies</span>${artifact.dependencies.map(id => {
        const bookId = dependencyOwners.get(id) || owner;
        return `<button type="button" data-pipeline-artifact="${escape(id)}" data-pipeline-artifact-book="${escape(bookId)}"><code>${escape(id)}</code>${bookId !== panel.book.id ? '<small>From another book</small>' : ''}</button>`;
      }).join('')}</div>`
      : '<p class="pipeline-help">No individual dependency IDs were recorded for this result.</p>';
    node.innerHTML = `<section class="pipeline-inspector"><div class="pipeline-inspector-heading"><h4>${escape(artifact.label || artifact.kind || 'Saved result')}</h4><button type="button" class="button text-button" data-pipeline-action="close-inspector">Close</button></div><dl class="pipeline-artifact-meta"><div><dt>Result ID</dt><dd><code>${escape(artifact.id)}</code></dd></div><div><dt>Book</dt><dd><code>${escape(owner)}</code>${owner !== panel.book.id ? ' · Related book' : ' · This book'}</dd></div><div><dt>Created</dt><dd>${escape(when(artifact.created_at))}</dd></div><div><dt>Origin</dt><dd>${escape(artifact.provider || 'Local / saved')}${artifact.model ? ` · ${escape(artifact.model)}` : ''}</dd></div><div><dt>Format version</dt><dd>${escape(artifact.schema_version ?? 'Not recorded')}</dd></div></dl>${artifact.legacy_provenance ? '<p class="pipeline-help">Older result: only the details recorded at the time are available.</p>' : ''}${dependencyMarkup}<details open><summary>Saved JSON output</summary><pre tabindex="0" data-pipeline-json></pre></details>${truncated ? '<p class="pipeline-help">Showing the first 200,000 characters. Export analysis files for the complete saved result.</p>' : ''}</section>`;
    const pre = node.querySelector('[data-pipeline-json]');
    if (pre) pre.textContent = truncated ? encoded.slice(0, 200000) + '\n… Truncated here.' : encoded;
  }

  function paintActivity(panel) {
    const node = panel.container.querySelector('[data-pipeline-activity]');
    if (!node || !panel.snapshot) return;
    const jobs = recent(panel.snapshot.jobs || []).slice(0, panel.jobsShown);
    const allAttempts = recent(panel.snapshot.attempts || []);
    const attempts = allAttempts.slice(0, panel.attemptsShown);
    const usage = panel.snapshot.usage || {};
    const allEvents = recent(panel.snapshot.events || []);
    const events = allEvents.slice(0, panel.eventsShown);
    const tokens = (actual, reserved) => typeof actual === 'number' ? number(actual) : typeof reserved === 'number' ? `${number(reserved)} reserved` : 'Unknown';
    node.innerHTML = `<p class="pipeline-help">${escape(UI.fmt.plural(usage.attempts ?? allAttempts.length, 'tracked request'))} · ${money(usage.estimated_spend_usd)} estimated spend${usage.unknown_cost_attempts ? ` · ${escape(UI.fmt.plural(usage.unknown_cost_attempts, 'request'))} with unknown cost` : ''}. ${escape(usage.note || 'Only recorded analysis usage is included; your provider’s bill is authoritative.')}</p><h4>Runs</h4>${jobs.length ? `<ol class="pipeline-run-list">${jobs.map(job => `<li><div><strong>${escape(job.kind || 'Processing')}${job.phase ? ` · ${escape(job.phase)}` : ''}</strong><span class="pipeline-badge ${statusClass(job.status)}">${escape(status(job.status))}</span></div><p>${escape(job.message || '')}</p><small>${escape(when(job.updated_at || job.created_at))}${typeof job.total === 'number' && job.total > 0 ? ` · ${number(job.progress ?? 0)} / ${number(job.total)} done` : ''}</small>${job.error ? `<p class="pipeline-error">${escape(job.error)}</p>` : ''}</li>`).join('')}</ol>` : '<p class="pipeline-help">No runs have been recorded.</p>'}${(panel.snapshot.jobs || []).length > jobs.length ? '<button type="button" class="button subtle" data-pipeline-action="more-jobs">Show more runs</button>' : ''}<h4>Recorded events</h4>${events.length ? `<ol class="pipeline-unit-events">${events.map(event => `<li><strong>${escape(event.event === 'cache_hit' ? 'Saved result reused' : status(event.event))}</strong><span>${escape(event.stage || 'Not recorded')} · ${escape(when(event.created_at))}</span>${event.artifact_id ? `<button type="button" data-pipeline-artifact="${escape(event.artifact_id)}">Inspect result</button>` : ''}${event.error ? `<p class="pipeline-error">${escape(event.error)}</p>` : ''}<details><summary>Recorded identifiers</summary><code>Run: ${escape(event.run_id || 'Not recorded')}<br>Unit: ${escape(event.unit_key || 'Not recorded')}</code></details></li>`).join('')}</ol>${events.length < allEvents.length ? '<button type="button" class="button subtle" data-pipeline-action="more-events">Show more events</button>' : ''}` : '<p class="pipeline-help">No events are recorded. Earlier runs may predate this history; reuse cannot be inferred from a request’s response.</p>'}<h4>Requests</h4><p class="pipeline-help">A response from a service is not the same as a checked, saved result. “Not recorded” means the check’s outcome was not recorded. Failed or uncertain requests can keep an amount held against the spending limit.</p>${attempts.length ? `<div class="pipeline-table-wrap"><table><thead><tr><th>Step / model</th><th>Request</th><th>Check</th><th>Input / output tokens</th><th>Estimated charge</th></tr></thead><tbody>${attempts.map(attempt => `<tr><th scope="row">${escape(attempt.stage || 'Not recorded')}<small>${escape(attempt.provider || '')} · ${escape(attempt.model || '')}</small><small>${escape(when(attempt.created_at))}</small><details><summary>Identifiers</summary><code>Run: ${escape(attempt.run_id || 'Not recorded')}<br>Unit: ${escape(attempt.unit_key || 'Not recorded')}<br>Attempt: ${escape(attempt.id || 'Not recorded')}</code></details></th><td>${escape(status(attempt.status))}${attempt.http_status != null ? `<small>HTTP ${escape(attempt.http_status)}</small>` : ''}${attempt.error ? `<small class="pipeline-error">${escape(attempt.error)}</small>` : ''}</td><td>${attempt.validation_state === 'accepted' ? 'Passed' : attempt.validation_state === 'rejected' ? 'Failed' : 'Not recorded'}</td><td>${tokens(attempt.input_tokens, attempt.reserved_input_tokens)} / ${tokens(attempt.output_tokens, attempt.reserved_output_tokens)}</td><td>${money(attempt.charged_estimate_usd)}${attempt.price_as_of ? `<small>Prices: ${escape(attempt.price_as_of)}</small>` : ''}</td></tr>`).join('')}</tbody></table></div><p class="pipeline-help">Showing ${number(attempts.length)} of ${number(allAttempts.length)} recorded requests.</p>${attempts.length < allAttempts.length ? '<button type="button" class="button subtle" data-pipeline-action="more-attempts">Show more requests</button>' : ''}` : '<p class="pipeline-help">No requests have been recorded for this book.</p>'}`;
  }

  function paintStory(panel) {
    const node = panel.container.querySelector('[data-pipeline-story]');
    if (!node) return;
    if (!panel.story) { node.innerHTML = '<p class="pipeline-help">Open this section to load the saved chapter and scene structure.</p>'; return; }
    const characters = new Map((panel.story.characters || []).map(character => [character.id, character.name]));
    const refs = panel.story.reference_counts || {};
    node.innerHTML = `<p class="pipeline-help">${escape(panel.story.note || 'This is the saved chapter and scene structure.')} Name mentions and attributed dialogue are source references, not proof of physical presence in a scene.</p><p class="pipeline-help">${number(refs.mention ?? 0)} name mentions · ${number(refs.dialogue ?? 0)} dialogue references · ${number(refs.profile_evidence ?? 0)} profile evidence references.</p><ol class="pipeline-story-list">${(panel.story.chapters || []).map(chapter => `<li><details><summary>${escape(chapter.title)} <span>${escape((chapter.kind || 'section').replaceAll('_', ' '))} · ${(chapter.scenes || []).length} scenes</span></summary><p class="pipeline-help">Source offsets ${number(chapter.start ?? 0)}–${number(chapter.end)}</p>${chapter.scenes?.length ? `<ol>${chapter.scenes.map(scene => `<li><strong>${escape(scene.title || 'Untitled scene')}</strong><p class="pipeline-help">Offsets ${number(scene.start)}–${number(scene.end)} · ${number((scene.passage_ids || []).length)} passages</p><p class="pipeline-help">Recorded cast IDs: ${(scene.character_ids || []).length ? scene.character_ids.map(id => escape(characters.get(id) || id)).join(', ') : 'None'}</p></li>`).join('')}</ol>` : '<p class="pipeline-help">No scenes have been saved in this section.</p>'}</details></li>`).join('')}</ol>`;
  }

  function paintSearch(panel, value) {
    const node = panel.container.querySelector('[data-pipeline-search-results]');
    if (!node) return;
    if (value.available === false) {
      node.innerHTML = `<p class="pipeline-note">Source search is unavailable in this local installation.</p><p class="pipeline-help">${escape(value.note || 'Saved results and source references can still be inspected above.')}</p>`;
      return;
    }
    const results = value.items || [];
    const scope = value.scope === 'earlier' ? 'this book and earlier series books' : 'this book';
    node.innerHTML = `<p class="pipeline-help">${number(results.length)} match${results.length === 1 ? '' : 'es'} shown for “${escape(value.query)}” in ${scope}. Search rank measures word matches, not identity or speaker confidence.</p>${value.note ? `<p class="pipeline-help">${escape(value.note)}</p>` : ''}${results.length ? `<ol class="pipeline-search-results">${results.map(result => `<li><div><strong>${escape(result.book_title)}</strong><span>${escape(result.chapter_title)}</span></div><blockquote>${escape(result.text)}</blockquote><p class="pipeline-help">Source offsets ${number(result.start)}–${number(result.end)}${typeof result.rank === 'number' && Number.isFinite(result.rank) ? ` · Search rank ${escape(result.rank)}` : ''}</p><details><summary>Source reference</summary><code>Book: ${escape(result.book_id)}<br>Chapter: ${escape(result.chapter_id)}<br>Passage: ${escape(result.passage_id)}</code></details></li>`).join('')}</ol>` : '<p class="pipeline-help">No passages matched. Try a character name or a shorter phrase. This search does not resolve pronouns or infer character identity.</p>'}`;
  }

  async function search(panel, form) {
    if (panel.searchLoading) return;
    const query = form.elements.query.value.trim();
    const scope = form.elements.scope.value;
    const node = panel.container.querySelector('[data-pipeline-search-results]');
    if (!node) return;
    if (!query) { node.innerHTML = '<p class="pipeline-error" role="alert">Enter a name or keywords to search.</p>'; return; }
    if (!['book', 'earlier'].includes(scope)) { node.innerHTML = '<p class="pipeline-error" role="alert">Choose this book or earlier series books.</p>'; return; }
    const bookId = panel.book.id;
    const version = ++panel.searchVersion;
    const params = new URLSearchParams({q:query, scope, limit:'20'});
    panel.searchLoading = true;
    node.innerHTML = '<p class="pipeline-help" role="status">Searching saved source passages…</p>';
    controls(panel);
    try {
      const value = await read(base(panel) + '/search?' + params);
      if (panel.book?.id !== bookId || panel.searchVersion !== version) return;
      paintSearch(panel, value);
    } catch (error) {
      if (panel.book?.id === bookId && panel.searchVersion === version) node.innerHTML = `<p class="pipeline-error" role="alert">Could not search passages: ${escape(error.message)}</p>`;
    } finally {
      if (panel.book?.id === bookId && panel.searchVersion === version) { panel.searchLoading = false; controls(panel); }
    }
  }

  function paint(panel) {
    panel.container.innerHTML = `<details class="pipeline-explorer" open><summary>Analysis explorer <span>Steps, what each uses, saved results &amp; requests</span></summary><div class="pipeline-explorer-body"><div class="pipeline-toolbar"><p class="pipeline-help">Browse what is saved and how each step uses earlier work. Nothing here sends requests.</p><div><a class="button subtle" href="${escape(base(panel) + '/analysis-export')}" download>Export analysis files</a><button type="button" class="button subtle" data-pipeline-action="refresh">Refresh</button></div></div><p data-pipeline-message class="pipeline-message" role="status"></p><section data-pipeline-stages aria-label="Analysis steps"></section><details class="pipeline-artifact-section" data-pipeline-artifact-section open><summary>Saved results</summary><div class="pipeline-filters" data-pipeline-filters></div><div data-pipeline-artifacts></div><div data-pipeline-inspector></div></details><details class="pipeline-activity-section"><summary>Runs, requests &amp; usage</summary><div data-pipeline-activity></div></details><details class="pipeline-search-section"><summary>Search source passages</summary><p class="pipeline-help">Look up names or keywords in saved text. All search words must match a passage. Earlier-series scope includes this book and strictly earlier books in its confirmed reading order.</p><form data-pipeline-search-form class="pipeline-search-form"><label>Search words<input type="search" name="query" maxlength="300" placeholder="Character name or keywords"></label><label>Search scope<select name="scope" aria-label="Search scope"><option value="book">This book</option><option value="earlier">This + earlier series books</option></select></label><button class="button subtle" type="submit" data-pipeline-action="search">Search</button></form><div data-pipeline-search-results aria-live="polite"><p class="pipeline-help">Search runs only when you submit. It matches words on the Bardic computer and sends no requests.</p></div></details><details class="pipeline-story-section" data-pipeline-story-section><summary>Chapter &amp; scene structure</summary><div data-pipeline-story></div></details></div></details>`;
    paintStages(panel); paintFilters(panel); paintArtifacts(panel); paintActivity(panel); paintStory(panel);
  }

  async function snapshot(panel, refreshArtifacts = false) {
    if (panel.loading) return;
    const bookId = panel.book.id;
    const version = ++panel.snapshotVersion;
    panel.loading = true;
    panel.lastSnapshotAt = Date.now();
    controls(panel);
    try {
      const value = await read(base(panel) + '/pipeline');
      if (panel.book?.id !== bookId || panel.snapshotVersion !== version) return;
      panel.snapshot = value;
      paintStages(panel); paintFilters(panel); paintActivity(panel);
      message(panel, '');
      if (refreshArtifacts) await artifacts(panel);
    } catch (error) {
      if (panel.book?.id === bookId && panel.snapshotVersion === version) message(panel, `Could not load analysis status: ${error.message}`, true);
    } finally {
      if (panel.book?.id === bookId && panel.snapshotVersion === version) { panel.loading = false; controls(panel); }
    }
  }

  async function artifacts(panel) {
    const bookId = panel.book.id;
    const version = ++panel.artifactVersion;
    const requestedOffset = panel.offset;
    const query = new URLSearchParams({limit:String(panel.limit), offset:String(panel.offset)});
    if (panel.stage) query.set('stage', panel.stage);
    if (panel.kind) query.set('kind', panel.kind);
    if (panel.current) query.set('current', panel.current);
    panel.artifactLoading = true;
    controls(panel);
    try {
      const value = await read(base(panel) + '/artifacts?' + query);
      if (panel.book?.id !== bookId || panel.artifactVersion !== version) return;
      panel.artifacts = value;
      panel.offset = value.offset ?? requestedOffset;
      paintArtifacts(panel); paintFilters(panel);
    } catch (error) {
      if (panel.book?.id === bookId && panel.artifactVersion === version) message(panel, `Could not load saved results: ${error.message}`, true);
    } finally {
      if (panel.book?.id === bookId && panel.artifactVersion === version) { panel.artifactLoading = false; controls(panel); }
    }
  }

  async function inspect(panel, id, ownerBookId = panel.book.id) {
    const bookId = panel.book.id;
    const version = ++panel.inspectVersion;
    panel.selectedArtifact = id;
    panel.selectedArtifactBook = ownerBookId;
    paintArtifacts(panel);
    const node = panel.container.querySelector('[data-pipeline-inspector]');
    if (node) node.innerHTML = '<p class="pipeline-help">Loading saved output…</p>';
    try {
      const value = await read(`/api/books/${path(ownerBookId)}/artifacts/${path(id)}`);
      if (panel.book?.id !== bookId || panel.inspectVersion !== version) return;
      paintInspector(panel, value);
    } catch (error) {
      if (panel.book?.id === bookId && panel.inspectVersion === version && node) {
        node.innerHTML = '<p class="pipeline-error" data-pipeline-inspect-error></p>';
        const failure = node.querySelector('[data-pipeline-inspect-error]');
        if (failure) failure.textContent = `Could not load this result: ${error.message}`;
      }
    }
  }

  async function story(panel, force = false) {
    if (panel.storyLoading || panel.story && !force) return;
    const bookId = panel.book.id;
    const version = ++panel.storyVersion;
    panel.storyLoading = true;
    try {
      const value = await read(base(panel) + '/story-map');
      if (panel.book?.id !== bookId || panel.storyVersion !== version) return;
      panel.story = value;
      paintStory(panel);
    } catch (error) {
      if (panel.book?.id === bookId && panel.storyVersion === version) message(panel, `Could not load story structure: ${error.message}`, true);
    } finally {
      if (panel.book?.id === bookId && panel.storyVersion === version) panel.storyLoading = false;
    }
  }

  function bind(panel) {
    panel.container.addEventListener('submit', event => {
      if (!event.target.matches('[data-pipeline-search-form]')) return;
      event.preventDefault();
      void search(panel, event.target);
    });
    panel.container.addEventListener('change', event => {
      const field = event.target;
      if (!['pipeline_stage','pipeline_kind','pipeline_current'].includes(field.name)) return;
      panel[{pipeline_stage:'stage', pipeline_kind:'kind', pipeline_current:'current'}[field.name]] = field.value;
      panel.offset = 0;
      paintStages(panel);
      void artifacts(panel);
    });
    panel.container.addEventListener('click', event => {
      const stage = event.target.closest('[data-pipeline-stage]');
      if (stage) {
        panel.stage = stage.dataset.pipelineStage;
        panel.offset = 0;
        const section = panel.container.querySelector('[data-pipeline-artifact-section]');
        if (section) section.open = true;
        paintStages(panel); paintFilters(panel);
        void artifacts(panel);
        return;
      }
      const artifact = event.target.closest('[data-pipeline-artifact]');
      if (artifact) { void inspect(panel, artifact.dataset.pipelineArtifact, artifact.dataset.pipelineArtifactBook || panel.book.id); return; }
      const button = event.target.closest('[data-pipeline-action]');
      if (!button) return;
      const action = button.dataset.pipelineAction;
      if (action === 'refresh') {
        void snapshot(panel, true);
        if (panel.container.querySelector('[data-pipeline-story-section]')?.open) void story(panel, true);
      } else if (action === 'previous' && !panel.artifactLoading && panel.offset > 0) {
        panel.offset = Math.max(0, panel.offset - panel.limit); void artifacts(panel);
      } else if (action === 'next' && !panel.artifactLoading && panel.offset + panel.limit < (panel.artifacts?.total || 0)) {
        panel.offset += panel.limit; void artifacts(panel);
      } else if (action === 'close-inspector') {
        panel.inspectVersion++; panel.selectedArtifact = null; panel.selectedArtifactBook = null;
        const node = panel.container.querySelector('[data-pipeline-inspector]');
        if (node) node.innerHTML = '';
        paintArtifacts(panel);
      } else if (action === 'more-jobs') { panel.jobsShown += 10; paintActivity(panel); }
      else if (action === 'more-attempts') { panel.attemptsShown += 30; paintActivity(panel); }
      else if (action === 'more-events') { panel.eventsShown += 30; paintActivity(panel); }
    });
    panel.container.addEventListener('toggle', event => {
      if (event.target.matches('[data-pipeline-story-section]') && event.target.open) void story(panel);
    }, true);
  }

  function render(container, book, options = {}) {
    if (!container) return Promise.resolve();
    let panel = panels.get(container);
    if (!panel) {
      panel = {container, book:null, busy:false, revision:null, snapshot:null, artifacts:null, story:null,
        snapshotVersion:0, artifactVersion:0, inspectVersion:0, storyVersion:0, searchVersion:0, searchLoading:false, loading:false, artifactLoading:false,
        storyLoading:false, stage:'', kind:'', current:'', offset:0, limit:30, selectedArtifact:null, selectedArtifactBook:null,
        jobsShown:10, attemptsShown:30, eventsShown:30, lastSnapshotAt:0};
      panels.set(container, panel);
      bind(panel);
    }
    if (!book) {
      panel.snapshotVersion++; panel.artifactVersion++; panel.inspectVersion++; panel.storyVersion++; panel.searchVersion++;
      panel.book = null; container.innerHTML = ''; return Promise.resolve();
    }
    const freshBook = panel.book?.id !== book.id;
    const changed = panel.revision !== book.revision;
    const finished = panel.busy && !options.busy;
    panel.book = book;
    panel.revision = book.revision;
    panel.busy = Boolean(options.busy);
    if (freshBook) {
      panel.snapshotVersion++; panel.artifactVersion++; panel.inspectVersion++; panel.storyVersion++; panel.searchVersion++;
      Object.assign(panel, {snapshot:null, artifacts:null, story:null, loading:false, artifactLoading:false, storyLoading:false, searchLoading:false,
        stage:'', kind:'', current:'', offset:0, selectedArtifact:null, selectedArtifactBook:null, jobsShown:10, attemptsShown:30, eventsShown:30, lastSnapshotAt:0});
      paint(panel);
      return Promise.all([snapshot(panel), artifacts(panel)]);
    }
    if (changed || finished || panel.busy && Date.now() - panel.lastSnapshotAt >= 3000) {
      if (changed || finished) {
        // A response started before publication cannot replace the final snapshot.
        panel.snapshotVersion++; panel.artifactVersion++;
        panel.loading = false; panel.artifactLoading = false;
      }
      if (changed) {
        panel.storyVersion++; panel.storyLoading = false; panel.story = null;
        if (panel.container.querySelector('[data-pipeline-story-section]')?.open) void story(panel, true);
      }
      return snapshot(panel, Boolean(panel.container.querySelector('[data-pipeline-artifact-section]')?.open));
    }
    return Promise.resolve();
  }

  window.BardicPipeline = {render};
})();
