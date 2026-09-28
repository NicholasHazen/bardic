/* Book pronunciations: respellings the narrator reads in place of a word.
   The book's text never changes. Hearing a respelling sends one voice example
   through the shared preview player; saving never starts narration. */
(() => {
  'use strict';
  const panels = new WeakMap();
  const PROVIDERS = [['breeze', 'Breeze'], ['gemini', 'Gemini'], ['system', 'Device']];
  const escape = value => window.BardicUI.esc(value);
  const path = value => encodeURIComponent(value);
  const plural = (count, word) => `${count} ${word}${count === 1 ? '' : 's'}`;

  async function request(url, method = 'GET', body) {
    const options = {method, headers:{Accept:'application/json'}};
    if (body !== undefined) { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
    const response = await fetch(url, options);
    let data;
    try { data = await response.json(); } catch { data = null; }
    if (!response.ok) {
      let detail = data?.detail;
      if (Array.isArray(detail)) detail = detail.map(item => item.msg || 'Invalid value').join('; ');
      const hint = globalThis.BardicErrorHints?.[data?.code];
      const text = typeof detail === 'string' ? (hint ? `${detail} ${hint}` : detail) : `Request failed (${response.status}). Try again.`;
      throw Object.assign(new Error(text), {code:data?.code ?? null, status:response.status});
    }
    return data;
  }

  // Read a row or the add form into an entry. Empty narrator-specific fields fall back to the shared respelling.
  function entryFrom(form) {
    const field = name => (form.elements[name]?.value || '').trim();
    const entry = {term:field('term'), respelling:field('respelling'), match_case:form.elements.match_case?.checked !== false};
    const providers = {};
    for (const [provider] of PROVIDERS) {
      const value = field(`provider_${provider}`);
      if (value) providers[provider] = value;
    }
    // A saved row always sends its narrator spellings, so clearing the last one removes it.
    if (Object.keys(providers).length || form.elements.provider_breeze) entry.providers = providers;
    if (form.dataset.pronunciationId) entry.id = form.dataset.pronunciationId;
    return entry;
  }
  const sameEntry = (a, b) => a.term === b.term && a.respelling === b.respelling &&
    (a.match_case !== false) === (b.match_case !== false) && JSON.stringify(a.providers || {}) === JSON.stringify(b.providers || {});

  // The same guidance the listening trial produced (docs/RESEARCH-VOICE.md).
  function advice(respelling) {
    if (/[A-Z]{2,}/.test(respelling)) return 'Capital letters can be read as initials or shouted. Try lower case.';
    if (/-/.test(respelling)) return 'Each hyphenated piece is read as its own word; check that none sounds like a different word.';
    return '';
  }

  function usageText(usage) {
    if (!usage) return '';
    if (!usage.occurrences) return 'Not found in this book’s text yet.';
    return `${plural(usage.occurrences, 'mention')} in ${plural(usage.passages, 'passage')}.`;
  }

  function providerFields(entry, open) {
    const overrides = entry?.providers || {};
    const count = Object.keys(overrides).length;
    return `<details class="pronunciation-providers"${open || count ? ' open' : ''}><summary>Different spelling for one narrator${count ? ` · ${count}` : ''}</summary><p class="field-help pronunciation-note">Only if a narrator still says it wrong. Leave blank to use the spelling above; type the original word to let that narrator read it unchanged.</p><div class="pronunciation-provider-grid">${PROVIDERS.map(([provider, label]) => `<label>${label}<input name="provider_${provider}" maxlength="120" value="${escape(overrides[provider] || '')}" autocomplete="off" spellcheck="false"></label>`).join('')}</div></details>`;
  }

  function rowMarkup(entry) {
    const tip = advice(entry.respelling);
    return `<form class="pronunciation-row" data-pronunciation-id="${escape(entry.id)}"><div class="pronunciation-fields"><label>Word<input name="term" required maxlength="80" value="${escape(entry.term)}" autocomplete="off" spellcheck="false"></label><span class="pronunciation-arrow" aria-hidden="true">→</span><label>Say it as<input name="respelling" required maxlength="120" value="${escape(entry.respelling)}" autocomplete="off" spellcheck="false"></label><label class="pronunciation-case"><input type="checkbox" name="match_case"${entry.match_case === false ? '' : ' checked'}> Match capitals</label></div><p class="pronunciation-usage">${escape(usageText(entry.usage))}${tip ? ` <span class="pronunciation-tip">${escape(tip)}</span>` : ''}</p>${providerFields(entry, false)}<div class="pronunciation-actions"><button type="button" class="button subtle" data-pronunciation-hear>Hear it</button><button type="submit" class="button subtle">Save</button><button type="button" class="button text-button" data-pronunciation-delete>Remove</button></div></form>`;
  }

  function suggestions(panel) {
    const known = new Set((panel.entries || []).map(entry => entry.term.toLowerCase()));
    const names = [];
    for (const character of panel.book?.characters || []) {
      if (['narrator', 'unassigned'].includes(character.id)) continue;
      for (const name of [character.name, ...(character.aliases || [])]) {
        for (const word of String(name || '').split(/\s+/)) {
          const clean = word.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, '');
          if (clean.length > 1 && !known.has(clean.toLowerCase()) && !names.includes(clean)) names.push(clean);
        }
      }
    }
    return names.slice(0, 24);
  }

  function markup(panel) {
    const names = suggestions(panel);
    const draft = panel.draft || {};
    const list = panel.entries === null ? '<p class="field-help pronunciation-note">Loading pronunciations…</p>' :
      panel.entries.length ? panel.entries.map(rowMarkup).join('') :
        '<p class="field-help pronunciation-note">No pronunciations yet.</p>';
    return `<p class="field-help pronunciation-note">Narrators guess unusual names. Write how a word should sound as an ordinary-looking word (<em>Kaylor</em> for Cthaelor), then hear it in the narrator’s voice for the provider chosen above. Only the words sent to the narrator change; the book’s text stays as written. Saving retires recorded takes that contain the word; they are re-recorded the next time you narrate.</p>${names.length ? `<div class="pronunciation-suggestions" role="group" aria-label="Names from your cast"><span>From your cast:</span>${names.map(name => `<button type="button" class="chip" data-pronunciation-suggest="${escape(name)}">${escape(name)}</button>`).join('')}</div>` : ''}<form class="pronunciation-row pronunciation-new" data-pronunciation-new><div class="pronunciation-fields"><label>Word<input name="term" required maxlength="80" value="${escape(draft.term || '')}" placeholder="Cthaelor" autocomplete="off" spellcheck="false"></label><span class="pronunciation-arrow" aria-hidden="true">→</span><label>Say it as<input name="respelling" required maxlength="120" value="${escape(draft.respelling || '')}" placeholder="Kaylor" autocomplete="off" spellcheck="false"></label><label class="pronunciation-case"><input type="checkbox" name="match_case"${draft.match_case === false ? '' : ' checked'}> Match capitals</label></div><div class="pronunciation-actions"><button type="button" class="button subtle" data-pronunciation-hear>Hear it</button><button type="submit" class="button primary">Add</button></div></form><div class="pronunciation-list">${list}</div><p class="message pronunciation-status" data-pronunciation-status role="status"></p>`;
  }

  function status(panel, message, error = false) {
    const node = panel.container.querySelector('[data-pronunciation-status]');
    if (!node) return;
    node.textContent = message;
    node.setAttribute('data-tone', error ? 'bad' : 'neutral');
    node.setAttribute('role', error ? 'alert' : 'status');
  }

  // Redraw, keeping the Add form and any row the owner changed but has not saved (except the one just saved).
  function draw(panel, except, keepDraft = true) {
    const form = panel.container.querySelector('[data-pronunciation-new]');
    if (form && keepDraft) panel.draft = entryFrom(form);
    const rows = () => [...(panel.container.querySelectorAll?.('form[data-pronunciation-id]') || [])];
    const unsaved = new Map();
    for (const row of rows()) {
      const id = row.dataset.pronunciationId, shown = (panel.shown || []).find(entry => entry.id === id);
      if (id !== except && shown && !sameEntry(entryFrom(row), shown)) unsaved.set(id, entryFrom(row));
    }
    panel.container.innerHTML = markup(panel);
    panel.shown = panel.entries || [];
    for (const row of rows()) {
      const edit = unsaved.get(row.dataset.pronunciationId);
      if (!edit) continue;
      row.elements.term.value = edit.term;
      row.elements.respelling.value = edit.respelling;
      row.elements.match_case.checked = edit.match_case;
      for (const [provider] of PROVIDERS) row.elements[`provider_${provider}`].value = edit.providers?.[provider] || '';
    }
  }

  async function load(panel) {
    const bookId = panel.book.id;
    try {
      const data = await request(`/api/books/${path(bookId)}/pronunciations`);
      if (panel.book.id !== bookId) return;
      panel.entries = data.pronunciations || [];
    } catch (error) {
      panel.entries = [];
      draw(panel);
      status(panel, error.message, true);
      return;
    }
    draw(panel);
  }

  async function save(panel, form, method, url, body, done) {
    panel.container.setAttribute('aria-busy', 'true');
    try {
      const data = await request(url, method, body);
      panel.entries = data.pronunciations || [];
      if (method === 'POST') panel.draft = {};
      panel.revision = data.book?.revision;
      if (data.book) panel.hooks.onBook?.(data.book);
      draw(panel, form.dataset.pronunciationId, method !== 'POST');
      const retired = data.retired_takes || 0;
      status(panel, `${done}${retired ? ` ${plural(retired, 'recorded take')} will be re-recorded with the new pronunciation when you narrate.` : ''}`);
    } catch (error) {
      status(panel, error.message, true);
    } finally {
      panel.container.setAttribute('aria-busy', 'false');
    }
  }

  function bind(panel) {
    const {container} = panel;
    container.addEventListener('submit', event => {
      const form = event.target;
      event.preventDefault();
      const entry = entryFrom(form);
      const {id, ...body} = entry;
      const base = `/api/books/${path(panel.book.id)}/pronunciations`;
      if (form.dataset.pronunciationNew !== undefined) void save(panel, form, 'POST', base, body, `Added ${entry.term}.`);
      else void save(panel, form, 'PATCH', `${base}/${path(id)}`, body, `Saved ${entry.term}.`);
    });
    container.addEventListener('click', event => {
      const target = event.target?.closest?.('[data-pronunciation-hear],[data-pronunciation-delete],[data-pronunciation-suggest]');
      if (!target) return;
      if (target.dataset.pronunciationSuggest !== undefined) {
        const form = container.querySelector('[data-pronunciation-new]');
        if (form) {
          form.elements.term.value = target.dataset.pronunciationSuggest;
          form.elements.respelling.focus?.();
        }
        return;
      }
      const form = target.closest('form');
      if (!form) return;
      const entry = entryFrom(form);
      if (target.dataset.pronunciationHear !== undefined) {
        if (!entry.term || !entry.respelling) {
          status(panel, 'Type the word and how to say it first.', true);
          return;
        }
        status(panel, advice(entry.respelling));
        panel.hooks.audition?.(entry);
        return;
      }
      void save(panel, form, 'DELETE', `/api/books/${path(panel.book.id)}/pronunciations/${path(entry.id)}`, undefined,
        `Removed ${entry.term}.`);
    });
  }

  function render(container, book, hooks = {}) {
    if (!container || !book) return;
    let panel = panels.get(container);
    if (!panel) {
      panel = {container, book, hooks, entries:null, draft:{}, revision:null};
      panels.set(container, panel);
      bind(panel);
    }
    const changed = panel.book.id !== book.id || panel.revision !== book.revision;
    panel.hooks = hooks;
    if (panel.book.id !== book.id) { panel.entries = null; panel.draft = {}; }
    panel.book = book;
    if (!changed && panel.entries !== null) return;
    panel.revision = book.revision;
    draw(panel);
    void load(panel);
  }

  window.BardicPronunciations = {render, entryFrom, advice, usageText};
})();
