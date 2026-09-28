/* Local library controls. Removal is reversible and never erases book work. */
(() => {
  'use strict';
  const panels = new WeakMap();
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const path = value => encodeURIComponent(value);
  const count = value => Number.isFinite(value) ? value.toLocaleString() : '—';
  const bytes = value => {
    if (!Number.isFinite(value)) return 'Not measured';
    if (value < 1024) return `${value} B`;
    const unit = Math.min(3, Math.floor(Math.log(value) / Math.log(1024)));
    return `${(value / 1024 ** unit).toFixed(1)} ${['B', 'KB', 'MB', 'GB'][unit]}`;
  };
  async function request(url, method='GET', body) {
    const options = {method, headers:{Accept:'application/json'}};
    if (body !== undefined) { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
    const response = await fetch(url, options);
    let data;
    try { data = await response.json(); } catch { data = null; }
    if (!response.ok) {
      const detail = data?.detail;
      throw new Error(typeof detail === 'string' ? detail : `Request failed (${response.status}). Try again.`);
    }
    return data;
  }
  function message(panel, value, error=false) {
    const node = panel.container.querySelector('[data-library-message]');
    if (node) { node.textContent = value; node.setAttribute('role', error ? 'alert' : 'status'); }
  }
  function lock(panel) {
    panel.container.setAttribute('aria-busy', String(panel.loading));
    panel.container.querySelectorAll('button,input,select').forEach(node => { node.disabled = Boolean(panel.loading || panel.options.busy); });
  }
  function order(form) {
    const value = form.elements.position.value.trim();
    const number = Number(value);
    if (!value || !Number.isFinite(number) || number < 0 || number > 1000000) throw new Error('Enter a reading order from 0 to 1,000,000.');
    return number;
  }
  function cover(book) {
    const initials = (book.title || 'Book').trim().split(/\s+/).slice(0,2).map(word => [...word][0]).join('').toUpperCase();
    // Derive the trusted same-origin route instead of accepting arbitrary image URLs.
    return book.cover ? `<img class="library-cover" src="/api/books/${path(book.id)}/cover?v=${path(book.cover.sha256 || '')}" alt="Cover of ${escape(book.title)}" loading="lazy">`
      : `<div class="library-cover library-cover-fallback" aria-label="No cover available"><span>${escape(initials)}</span><small>Spin Tails</small></div>`;
  }
  function removeControl(kind, id, archived) {
    return archived ? `<button type="button" class="button subtle" data-library-action="restore-${kind}" data-library-id="${escape(id)}">Restore ${kind}</button>`
      : `<details class="library-removal"><summary>Remove ${kind}</summary><p>${kind === 'book' ? 'Hide this book from the library. Its source, audio, analysis and series links stay saved. Restore it from Removed items at any time.' : 'Hide this series. Its books remain in the library; memberships, character links and observations stay saved. Restore the series to reconnect its continuity.'}</p><button type="button" class="button subtle" data-library-action="archive-${kind}" data-library-id="${escape(id)}">Remove from library</button></details>`;
  }
  function bookCard(panel, book) {
    const activeSeries = (panel.data.series || []).filter(s => !s.archived);
    const uid = `library-book-${path(book.id)}`;
    return `<article class="library-book-card">${cover(book)}<div class="library-book-detail"><div class="library-card-heading"><div><h3>${escape(book.title || 'Untitled book')}</h3><p class="library-author">${escape(book.author || 'Author not specified')}</p></div>${book.archived ? '<span class="library-badge">Removed</span>' : ''}</div><p class="library-content-size">${count(book.word_count)} words · ${count(book.text_character_count)} characters<br>${count(book.chapter_count)} chapters · ${count(book.section_count)} source sections · ${count(book.passage_count ?? book.segment_count)} passages</p>${book.membership ? `<p class="library-membership">${escape(book.membership.series_name)} · volume ${escape(book.membership.position)}</p>` : ''}<dl class="library-storage"><div><dt>Original</dt><dd>${bytes(book.storage?.original_bytes)}</dd></div><div><dt>Production audio</dt><dd>${bytes(book.storage?.audio_bytes)}</dd></div><div><dt>Simple listening</dt><dd>${bytes(book.storage?.simple_listen_bytes)}</dd></div><div><dt>Database payload</dt><dd>${bytes(book.storage?.database_payload_bytes)}</dd></div></dl><div class="library-card-actions">${!book.archived ? `<button type="button" class="button" data-library-action="open-book" data-library-id="${escape(book.id)}">Open book</button>` : ''}${removeControl('book', book.id, book.archived)}</div>${!book.archived ? `<details class="library-edit"><summary>Edit book details</summary><form data-library-form="metadata" data-library-id="${escape(book.id)}"><label for="${uid}-title">Title</label><input id="${uid}-title" name="title" maxlength="500" value="${escape(book.title)}" required><label for="${uid}-author">Author</label><input id="${uid}-author" name="author" maxlength="500" value="${escape(book.author)}"><div class="library-form-actions"><button type="submit" class="button subtle">Save details</button><button type="button" class="button subtle" data-library-action="refresh-metadata" data-library-id="${escape(book.id)}">Refresh cover from ebook</button></div><p class="library-help">Refreshing reads the saved original. Edited title and author are preserved.</p></form><form data-library-form="membership" data-library-id="${escape(book.id)}"><label for="${uid}-series">Series</label><select name="series_id" id="${uid}-series"><option value="">Standalone book</option>${activeSeries.map(s => `<option value="${escape(s.id)}" ${book.membership?.series_id === s.id ? 'selected' : ''}>${escape(s.name)}</option>`).join('')}</select><label for="${uid}-position">Reading order</label><input name="position" id="${uid}-position" type="number" min="0" max="1000000" step="any" value="${book.membership ? escape(book.membership.position) : ''}" placeholder="e.g. 9"><button type="submit" class="button subtle">Save series</button><p class="library-help">Changing series removes this book’s current character identity links. Saved observations remain.</p></form></details>` : ''}</div></article>`;
  }
  function seriesCard(panel, series) {
    const uid = `library-series-${path(series.id)}`;
    const candidates = (panel.data.books || []).filter(b => !b.archived && b.membership?.series_id !== series.id);
    return `<article class="library-series-card"><div class="library-card-heading"><div><h3>${escape(series.name)}</h3><p class="library-help">${count((series.volumes || []).filter(v => v.status === 'available').length)} available books · ${count(series.character_count)} linked character identities</p></div>${series.archived ? '<span class="library-badge">Removed</span>' : `<button type="button" class="button subtle" data-library-action="open-series" data-library-id="${escape(series.id)}">Open series</button>`}</div><ol class="library-volumes">${(series.volumes || []).map(volume => `<li><span class="library-volume-order">${escape(volume.position)}</span><span>${escape(volume.title || `Volume ${volume.position}`)}</span><span class="library-badge">${escape(volume.status)}</span>${!volume.book_id && !series.archived ? `<button type="button" class="library-text-button" data-library-action="remove-volume" data-library-id="${escape(series.id)}" data-library-position="${escape(volume.position)}" aria-label="Remove placeholder for volume ${escape(volume.position)}">Remove placeholder</button>` : ''}</li>`).join('') || '<li class="library-help">Add an ebook or a missing-volume placeholder to begin.</li>'}</ol>${!series.archived ? `<details class="library-edit"><summary>Manage series</summary><form data-library-form="rename-series" data-library-id="${escape(series.id)}"><label for="${uid}-name">Series name</label><input name="name" id="${uid}-name" maxlength="200" value="${escape(series.name)}" required><button class="button subtle" type="submit">Save name</button></form><form data-library-form="add-book" data-library-id="${escape(series.id)}"><label for="${uid}-book">Add a library book</label><select name="book_id" id="${uid}-book" required><option value="">Choose a book</option>${candidates.map(b => `<option value="${escape(b.id)}">${escape(b.title)}</option>`).join('')}</select><label for="${uid}-book-order">Reading order</label><input name="position" id="${uid}-book-order" type="number" min="0" max="1000000" step="any" required><button class="button subtle" type="submit">Add book</button><p class="library-help">Moving a book from another series removes its current identity links.</p></form><form data-library-form="volume" data-library-id="${escape(series.id)}"><h4>Track a volume without an ebook</h4><label for="${uid}-volume-order">Reading order</label><input name="position" id="${uid}-volume-order" type="number" min="0" max="1000000" step="any" required><label for="${uid}-volume-title">Title, if known</label><input name="title" id="${uid}-volume-title" maxlength="500"><label for="${uid}-volume-status">Availability</label><select name="status" id="${uid}-volume-status"><option value="missing">Missing from this library</option><option value="planned">Planned volume</option></select><button class="button subtle" type="submit">Save placeholder</button><p class="library-help">Placeholders make gaps explicit. They contain no text and are never sent for analysis.</p></form></details>` : ''}${removeControl('series', series.id, series.archived)}</article>`;
  }
  function paint(panel) {
    const books = (panel.data.books || []).filter(book => Boolean(book.archived) === panel.removed);
    const series = (panel.data.series || []).filter(item => Boolean(item.archived) === panel.removed);
    const storage = panel.data.storage || {};
    panel.container.innerHTML = `<section class="library-manager" aria-label="Manage library"><header class="library-manager-heading"><div><h2>Your library</h2><p class="library-help">Keep ebooks, series continuity and saved performances together.</p></div><button type="button" class="button subtle" data-library-action="refresh">Refresh</button></header><div class="library-toolbar"><div class="library-tabs" aria-label="Library visibility"><button type="button" data-library-action="active" aria-pressed="${!panel.removed}">Library</button><button type="button" data-library-action="removed" aria-pressed="${panel.removed}">Removed items</button></div><span class="library-help">${bytes(storage.data_directory_bytes)} in the data folder · ${bytes(storage.shared_database_bytes)} shared database</span></div><p class="library-message" data-library-message role="status"></p>${panel.removed ? '<p class="library-notice">Removed items keep their original files, audio, analysis and relationships. Restore an item to show it in the library again. No disk space is reclaimed.</p>' : `<form class="library-create-series" data-library-form="create-series"><div><label for="library-new-series">Create a series</label><input id="library-new-series" name="name" maxlength="200" placeholder="Series name" required></div><button type="submit" class="button">Create series</button></form>`}<h3 class="library-section-label">${panel.removed ? 'Removed books' : 'Books'} · ${books.length}</h3><div class="library-books">${books.map(book => bookCard(panel, book)).join('') || `<p class="library-empty">${panel.removed ? 'No removed books.' : 'Import your first ebook using Import ebook. You can create its series now.'}</p>`}</div><h3 class="library-section-label">${panel.removed ? 'Removed series' : 'Series'} · ${series.length}</h3><div class="library-series">${series.map(item => seriesCard(panel, item)).join('') || `<p class="library-empty">${panel.removed ? 'No removed series.' : 'Create a series above, then add books in reading order. Missing volumes can be tracked as placeholders.'}</p>`}</div><p class="library-help library-disk-note">${escape(storage.note || 'Database payload sizes are not separate disk allocations. The database is shared by all books.')}</p></section>`;
    lock(panel);
  }
  async function load(panel, success='') {
    const generation = ++panel.generation;
    panel.loading = true; lock(panel);
    try {
      const data = await request('/api/library?include_archived=true');
      if (generation !== panel.generation || panels.get(panel.container) !== panel) return;
      panel.data = data; paint(panel); message(panel, success);
    } catch (error) {
      if (generation === panel.generation) message(panel, error.message, true);
    } finally {
      if (generation === panel.generation) { panel.loading = false; lock(panel); }
    }
  }
  async function mutate(panel, url, method, body, success) {
    if (panel.loading || panel.options.busy) return;
    panel.loading = true; lock(panel);
    try {
      await request(url, method, body);
      await load(panel, success);
      await panel.options.onChange?.();
    } catch (error) { message(panel, error.message, true); }
    finally { panel.loading = false; lock(panel); }
  }
  function bind(panel) {
    panel.container.addEventListener('click', event => {
      const button = event.target.closest('[data-library-action]');
      if (!button || panel.loading || panel.options.busy) return;
      const {libraryAction: action, libraryId: id, libraryPosition: position} = button.dataset;
      if (action === 'active' || action === 'removed') { panel.removed = action === 'removed'; paint(panel); return; }
      if (action === 'refresh') { void load(panel); return; }
      if (action === 'open-book') { panel.options.onSelectBook?.(id); return; }
      if (action === 'open-series') { panel.options.onSelectSeries?.(id); return; }
      if (action === 'refresh-metadata') { void mutate(panel, `/api/books/${path(id)}/refresh-metadata`, 'POST', undefined, 'Metadata refreshed from the saved original.'); return; }
      if (action === 'remove-volume') { void mutate(panel, `/api/series/${path(id)}/volumes/${path(position)}`, 'DELETE', undefined, 'Volume placeholder removed.'); return; }
      const match = /^(archive|restore)-(book|series)$/.exec(action);
      if (match) void mutate(panel, `/api/${match[2] === 'book' ? 'books' : 'series'}/${path(id)}/${match[1]}`, 'POST', undefined,
                             match[1] === 'restore' ? 'Restored to the library.' : 'Removed from view. All files and saved work are retained.');
    });
    panel.container.addEventListener('submit', event => {
      const form = event.target.closest('[data-library-form]');
      if (!form) return;
      event.preventDefault();
      if (panel.loading || panel.options.busy) return;
      const kind = form.dataset.libraryForm, id = form.dataset.libraryId;
      const value = name => form.elements[name].value;
      try {
        if (kind === 'metadata') void mutate(panel, `/api/books/${path(id)}/metadata`, 'PATCH', {title:value('title'), author:value('author')}, 'Book details saved.');
        else if (kind === 'create-series') void mutate(panel, '/api/series', 'POST', {name:value('name')}, 'Series created. Add books or missing-volume placeholders below.');
        else if (kind === 'rename-series') void mutate(panel, `/api/series/${path(id)}`, 'PATCH', {name:value('name')}, 'Series name saved.');
        else if (kind === 'membership') void mutate(panel, `/api/books/${path(id)}/series`, 'PUT', {series_id:value('series_id') || null, position:value('series_id') ? order(form) : null}, 'Series membership saved.');
        else if (kind === 'add-book') { if (!value('book_id')) throw new Error('Choose a book to add.'); void mutate(panel, `/api/books/${path(value('book_id'))}/series`, 'PUT', {series_id:id, position:order(form)}, 'Book added to series.'); }
        else if (kind === 'volume') void mutate(panel, `/api/series/${path(id)}/volumes`, 'PUT', {position:order(form), title:value('title'), status:value('status')}, 'Volume placeholder saved.');
      } catch (error) { message(panel, error.message, true); }
    });
  }
  function render(container, options={}) {
    let panel = panels.get(container);
    if (!panel) {
      panel = {container, options, data:{books:options.books || [], series:options.series || [], storage:options.storage},
               removed:false, loading:false, generation:0};
      panels.set(container, panel); bind(panel); paint(panel);
      return load(panel);
    }
    panel.options = options;
    lock(panel);
    // Repeated parent renders must not discard in-progress metadata edits.
    return Promise.resolve();
  }
  function refresh(container) { const panel = panels.get(container); return panel ? load(panel) : Promise.resolve(); }
  window.SpinTailsLibrary = {render, refresh};
})();
