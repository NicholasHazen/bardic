/* Optional series continuity controls. Identity links always require an explicit save. */
(() => {
  'use strict';
  const panels = new WeakMap();
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
  const path = value => encodeURIComponent(value);
  const realCharacters = book => (book?.characters || []).filter(c => !['narrator', 'unassigned'].includes(c.id));
  const signature = book => JSON.stringify([book.id, realCharacters(book).map(c => [c.id, c.name])]);

  async function request(url, method = 'GET', body) {
    const options = {method, headers:{'Accept':'application/json'}};
    if (body !== undefined) { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
    const response = await fetch(url, options);
    let data;
    try { data = await response.json(); } catch { data = null; }
    if (!response.ok) {
      let detail = data?.detail;
      if (Array.isArray(detail)) detail = detail.map(item => item.msg || 'Invalid value').join('; ');
      throw new Error(typeof detail === 'string' ? detail : `Request failed (${response.status}). Try again.`);
    }
    return data;
  }

  function status(panel, message, error = false) {
    const node = panel.container.querySelector('[data-series-status]');
    if (!node) return;
    node.textContent = message;
    node.classList.toggle('series-error', error);
    node.setAttribute('role', error ? 'alert' : 'status');
  }

  function busy(panel, value) {
    panel.busy = value;
    panel.container.setAttribute('aria-busy', String(value));
    panel.container.querySelectorAll('input,select,button').forEach(node => { node.disabled = value; });
    if (!value) {
      const form = panel.container.querySelector('[data-series-membership]');
      if (form) form.elements.position.disabled = !form.elements.series_id.value;
    }
  }

  function order(form) {
    const raw = form.elements.position.value.trim();
    const value = Number(raw);
    if (!raw || !Number.isFinite(value) || value < 0 || value > 1000000) {
      throw new Error('Enter a reading order from 0 to 1,000,000. Decimals are allowed for side stories.');
    }
    return value;
  }

  function contextMarkup(context) {
    const items = context?.characters || [];
    const count = context?.included_observations || 0;
    if (!count) return '<p class="series-help">No earlier-book evidence is connected yet. Add earlier books, analyze their characters, and link matching identities here.</p>';
    return `<details class="series-context"><summary>${count} earlier-book observation${count === 1 ? '' : 's'} for ${items.length} character${items.length === 1 ? '' : 's'}</summary><p class="series-help">Only books earlier in the reading order are included. Observations remain tied to their source; differences may reflect changes over time.</p>${items.map(item => `<div class="series-context-character"><h4>${escape(item.name)}</h4><ul>${item.observations.map(observation => `<li><span class="series-source">${escape(observation.book_title)} · ${escape(observation.chapter_title)} · order ${escape(observation.position)}</span>${observation.description ? `<p>${escape(observation.description)}</p>` : ''}${observation.direction ? `<p class="series-help">Performance note: ${escape(observation.direction)}</p>` : ''}<blockquote>${escape(observation.quote)}</blockquote></li>`).join('')}</ul></div>`).join('')}${context.truncated ? `<p class="series-help">Showing a bounded selection of ${context.available_observations} available observations. All source observations remain saved.</p>` : ''}</details>`;
  }

  /** Proposed links for unlinked characters. Each needs an explicit confirmation; namesakes need a choice. */
  function suggestionsMarkup(panel) {
    const items = panel.suggestions?.suggestions || [];
    if (!items.length) return '';
    const uid = `series-${panel.book.id}`;
    const source = candidate => candidate.sources.map(item => `${item.character_name} in ${item.title}`).join('; ');
    return `<div class="series-suggestions"><h4>Suggested links</h4><p class="series-help">These characters share a name or alias with a linked character in an earlier book. A shared name is not proof: confirm only the ones you know are the same person.</p>${items.map(item => {
      const single = !item.ambiguous && item.candidates.length === 1 ? item.candidates[0] : null;
      const choice = single ? `<input type="hidden" name="series_character_id" value="${escape(single.series_character_id)}"><span class="series-link-state">Same as ${escape(single.name)}? (${escape(source(single))})</span>`
        : `<label class="series-sr-only" for="${escape(uid)}-suggest-${escape(item.character_id)}">Series identity for ${escape(item.character_name)}</label><select id="${escape(uid)}-suggest-${escape(item.character_id)}" name="series_character_id"><option value="">Choose one: several identities match</option>${item.candidates.map(candidate => `<option value="${escape(candidate.series_character_id)}">${escape(candidate.name)} · ${escape(source(candidate))}</option>`).join('')}</select>`;
      return `<form data-series-suggestion="${escape(item.character_id)}" class="series-character-form"><div class="series-character-heading"><h4>${escape(item.character_name)}</h4></div><div class="series-character-controls">${choice}<button class="button subtle" type="submit">Confirm link</button></div></form>`;
    }).join('')}</div>`;
  }

  function markup(panel) {
    const member = panel.data?.membership;
    const series = panel.series || [];
    const characters = panel.data?.characters || [];
    const links = new Map((panel.data?.links || []).map(link => [link.character_id, link]));
    const cast = realCharacters(panel.book);
    const uid = `series-${panel.book.id}`;
    return `<section class="series-panel" aria-labelledby="${escape(uid)}-heading"><div class="series-heading"><div><h3 id="${escape(uid)}-heading">Series continuity</h3><p class="series-help">Connect this book to earlier volumes for source-backed character context.</p></div><button type="button" class="button subtle" data-series-action="refresh">Refresh</button></div><p class="series-status" data-series-status role="status"></p><form data-series-membership class="series-membership-form"><div><label for="${escape(uid)}-select">Series</label><select id="${escape(uid)}-select" name="series_id"><option value="">Standalone book</option>${series.map(item => `<option value="${escape(item.id)}" ${member?.series_id === item.id ? 'selected' : ''}>${escape(item.name)}</option>`).join('')}</select></div><div><label for="${escape(uid)}-order">Reading order</label><input id="${escape(uid)}-order" name="position" type="number" min="0" max="1000000" step="any" value="${member ? escape(member.position) : ''}" placeholder="Book number" ${member ? '' : 'disabled'}></div><button class="button subtle" type="submit">Save series</button></form><p class="series-help">Use the intended reading order; decimals allow side stories. Changing series removes this book’s character links.</p><details class="series-create"><summary>Create a series</summary><form data-series-create class="series-membership-form"><div><label for="${escape(uid)}-new-name">Series name</label><input id="${escape(uid)}-new-name" name="name" maxlength="200" required placeholder="Name of the series"></div><div><label for="${escape(uid)}-new-order">This book’s order</label><input id="${escape(uid)}-new-order" name="position" type="number" min="0" max="1000000" step="any" required placeholder="Book number"></div><button class="button subtle" type="submit">Create &amp; add book</button></form></details>${member ? `<details class="series-identities" ${panel.identitiesOpen ? 'open' : ''}><summary>Link character identities · ${cast.length} in this book${(panel.suggestions?.suggestions || []).length ? ` · ${panel.suggestions.suggestions.length} suggested` : ''}</summary><p class="series-help">Choose the same series identity only when you know these are the same character. Matching names never link automatically. These links provide earlier-book evidence to future profile analysis.</p>${suggestionsMarkup(panel)}${cast.length ? cast.map(character => {
      const link = links.get(character.id);
      return `<form data-series-character="${escape(character.id)}" class="series-character-form"><div class="series-character-heading"><h4>${escape(character.name)}</h4><span class="series-link-state">${link ? `Linked to ${escape(link.name)}` : 'Not linked'}</span></div><div class="series-character-controls"><label class="series-sr-only" for="${escape(uid)}-identity-${escape(character.id)}">Series identity for ${escape(character.name)}</label><select id="${escape(uid)}-identity-${escape(character.id)}" name="series_character_id"><option value="">Not linked</option>${characters.map(identity => `<option value="${escape(identity.id)}" ${link?.series_character_id === identity.id ? 'selected' : ''}>${escape(identity.name)}${characters.filter(other => other.name === identity.name).length > 1 ? ` · ${escape(identity.id.slice(-6))}` : ''}</option>`).join('')}</select><button class="button subtle" type="submit">Save link</button></div><details class="series-new-identity"><summary>Create a separate identity</summary><div class="series-character-controls"><label class="series-sr-only" for="${escape(uid)}-new-${escape(character.id)}">New series identity name</label><input id="${escape(uid)}-new-${escape(character.id)}" name="identity_name" maxlength="200" value="${escape(character.name)}"><button type="button" class="button subtle" data-series-action="create-character">Create &amp; link</button></div></details></form>`;
    }).join('') : '<p class="series-help">Scan the book to discover characters before linking identities.</p>'}</details>${contextMarkup(panel.context)}` : ''}</section>`;
  }

  async function load(panel, message = '') {
    const version = ++panel.version;
    const bookId = panel.book.id;
    busy(panel, true);
    try {
      const [series, data, context, suggestions] = await Promise.all([
        request('/api/series'), request(`/api/books/${path(bookId)}/series`), request(`/api/books/${path(bookId)}/series/context`),
        request(`/api/books/${path(bookId)}/series/suggestions`)
      ]);
      if (version !== panel.version || panel.book?.id !== bookId) return;
      panel.series = Array.isArray(series) ? series : [];
      panel.data = data || {};
      panel.context = context;
      panel.suggestions = suggestions;
      panel.container.innerHTML = markup(panel);
      status(panel, message);
    } catch (error) {
      if (version !== panel.version || panel.book?.id !== bookId) return;
      if (!panel.data) panel.container.innerHTML = '<section class="series-panel"><h3>Series continuity</h3><p data-series-status role="alert"></p><button class="button subtle" type="button" data-series-action="refresh">Try again</button></section>';
      status(panel, `Could not load series: ${error.message}`, true);
    } finally {
      if (version === panel.version && panel.book?.id === bookId) busy(panel, false);
    }
  }

  async function mutate(panel, operation, message) {
    if (panel.busy) return;
    const bookId = panel.book.id;
    const version = panel.version;
    panel.identitiesOpen = Boolean(panel.container.querySelector('.series-identities[open]'));
    busy(panel, true);
    status(panel, 'Saving…');
    try {
      await operation(bookId);
      if (panel.book?.id !== bookId || panel.version !== version) return;
      await load(panel, message);
    } catch (error) {
      if (panel.book?.id === bookId && panel.version === version) status(panel, error.message, true);
    } finally {
      if (panel.book?.id === bookId && panel.version === version) busy(panel, false);
    }
  }

  function bind(panel) {
    panel.container.addEventListener('change', event => {
      const form = event.target.closest('[data-series-membership]');
      if (form && event.target.name === 'series_id') form.elements.position.disabled = !event.target.value;
    });
    panel.container.addEventListener('submit', event => {
      const form = event.target;
      if (!form.matches('[data-series-membership],[data-series-create],[data-series-character],[data-series-suggestion]')) return;
      event.preventDefault();
      if (panel.busy) return;
      if (form.matches('[data-series-membership]')) {
        let body;
        try { body = form.elements.series_id.value ? {series_id:form.elements.series_id.value, position:order(form)} : {series_id:null, position:null}; }
        catch (error) { status(panel, error.message, true); return; }
        void mutate(panel, id => request(`/api/books/${path(id)}/series`, 'PUT', body), body.series_id ? 'Series and reading order saved.' : 'Book removed from the series. Character links cleared.');
      } else if (form.matches('[data-series-create]')) {
        let position;
        const name = form.elements.name.value.trim();
        try { position = order(form); if (!name) throw new Error('Enter a series name.'); }
        catch (error) { status(panel, error.message, true); return; }
        void mutate(panel, async id => {
          const created = await request('/api/series', 'POST', {name});
          try { await request(`/api/books/${path(id)}/series`, 'PUT', {series_id:created.id, position}); }
          catch (error) { throw new Error(`Series created, but the book could not be added: ${error.message} Refresh and select the new series to retry.`); }
        }, 'Series created and book added.');
      } else if (form.matches('[data-series-suggestion]')) {
        // A suggestion links only on this explicit confirmation, through the ordinary link route.
        const characterId = form.dataset.seriesSuggestion;
        const identityId = form.elements.series_character_id.value;
        if (!identityId) { status(panel, 'Choose which series identity this character is first.', true); return; }
        void mutate(panel, id => request(`/api/books/${path(id)}/series/characters/${path(characterId)}`, 'PUT', {series_character_id:identityId}), 'Suggested link confirmed.');
      } else {
        const characterId = form.dataset.seriesCharacter;
        const identityId = form.elements.series_character_id.value || null;
        void mutate(panel, id => request(`/api/books/${path(id)}/series/characters/${path(characterId)}`, 'PUT', {series_character_id:identityId}), identityId ? 'Character identity linked.' : 'Character identity unlinked.');
      }
    });
    panel.container.addEventListener('click', event => {
      const button = event.target.closest('[data-series-action]');
      if (!button || panel.busy) return;
      if (button.dataset.seriesAction === 'refresh') {
        panel.identitiesOpen = Boolean(panel.container.querySelector('.series-identities[open]'));
        void load(panel);
      } else if (button.dataset.seriesAction === 'create-character') {
        const form = button.closest('[data-series-character]');
        const characterId = form?.dataset.seriesCharacter;
        const name = form?.elements.identity_name.value.trim();
        const seriesId = panel.data?.membership?.series_id;
        if (!name || !characterId || !seriesId) { status(panel, 'Enter a name for this character’s series identity.', true); return; }
        void mutate(panel, async id => {
          const identity = await request(`/api/series/${path(seriesId)}/characters`, 'POST', {name});
          try { await request(`/api/books/${path(id)}/series/characters/${path(characterId)}`, 'PUT', {series_character_id:identity.id}); }
          catch (error) { throw new Error(`Identity created, but could not be linked: ${error.message} Refresh and select it to retry.`); }
        }, 'Separate series identity created and linked.');
      }
    });
  }

  function render(container, book) {
    if (!container) return Promise.resolve();
    let panel = panels.get(container);
    if (!panel) {
      panel = {container, book:null, key:null, version:0, data:null, series:[], context:null, suggestions:null, busy:false, identitiesOpen:false};
      panels.set(container, panel);
      bind(panel);
    }
    if (!book) { panel.version++; panel.book = null; panel.key = null; container.innerHTML = ''; return Promise.resolve(); }
    const key = signature(book);
    if (panel.key === key) { panel.book = book; return Promise.resolve(); }
    panel.book = book;
    panel.key = key;
    panel.data = null;
    panel.identitiesOpen = false;
    container.innerHTML = '<section class="series-panel"><h3>Series continuity</h3><p data-series-status role="status">Loading series…</p></section>';
    return load(panel);
  }

  window.BardicSeries = {render};
})();
