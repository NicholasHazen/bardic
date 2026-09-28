/* Page glue around the book workspace. Exposes window.BardicShell; app.js calls it through
   optional hooks (renderLifecycle, navigated, initialRoute, start) and exposes its own state
   and navigation as window.BardicApp. This file owns:
     - the lifecycle strip under the book tabs (lifecycle.js computes it),
     - hash routes: #/library, #/voices and #/book/<id>/<tab>, with back and forward,
     - the breadcrumb, the Voices page in the sidebar, and
     - the one-line narrator summary on Read & listen, which mirrors the sheet's More options.
   Nothing here sends a paid request. The only fetch is the read-only analysis overview. */
(() => {
  'use strict';
  const BOOK_TABS = ['read', 'analysis', 'cast', 'studio', 'details'];
  const $ = (selector, root = document) => root.querySelector(selector);
  const app = () => window.BardicApp;
  const ui = () => window.BardicUI;
  const esc = value => (ui()?.esc || (text => String(text ?? '')))(value);
  const safeGet = key => { try { return localStorage.getItem(key); } catch { return null; } };
  const safeSet = (key, value) => { try { localStorage.setItem(key, value); } catch { /* A convenience only. */ } };

  // ---- Lifecycle strip -----------------------------------------------------------------
  const life = {bookId:null, key:'', overview:null, loading:false, seq:0, html:'', expanded:safeGet('bardic:lifecycle-expanded') === 'true', staleAfterAnalysis:false};

  // Jobs that can change analysis results or pending versions refresh the overview when they settle.
  const ANALYSIS_KINDS = new Set(['pipeline', 'analysis', 'analyze']);
  function overviewKey(state) {
    const jobs = (state.jobs || []).filter(job => ANALYSIS_KINDS.has(job.kind)).map(job => `${job.id}:${job.status}`).join(',');
    return `${state.book.id}|${state.book.revision ?? ''}|${jobs}`;
  }

  async function loadOverview(bookId) {
    const seq = ++life.seq;
    life.loading = true;
    try {
      const response = await fetch(`/api/books/${encodeURIComponent(bookId)}/analysis-pipeline`, {headers:{Accept:'application/json'}});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const value = await response.json();
      if (seq === life.seq && app()?.state.book?.id === bookId) life.overview = value;
    } catch {
      // Unknown stays unknown: the Analyze stage says so and points to Analyze; nothing is guessed.
      if (seq === life.seq) life.overview = {error:true};
    } finally {
      if (seq === life.seq) { life.loading = false; paintLifecycle(); }
    }
  }

  function recordProvider() {
    return $('#render-provider')?.value || 'system';
  }
  // Default counts as a voice only where the service has one to use.
  function defaultVoice(state, provider) {
    if (provider === 'breeze') return Boolean(state.voiceLibrary?.defaults?.breeze);
    if (provider === 'system') return state.status?.providers?.find(item => item.id === 'system')?.available !== false;
    return true;
  }

  function paintLifecycle() {
    const node = $('#book-lifecycle'), state = app()?.state;
    const lifecycle = window.BardicLifecycle;
    if (!node || !state?.book || !lifecycle) return;
    const provider = recordProvider();
    const model = lifecycle.compute({book:state.book, overview:life.overview, recordProvider:provider, defaultVoice:defaultVoice(state, provider)});
    const html = lifecycle.strip(model, {expanded:life.expanded});
    if (html === life.html && node.childElementCount) return;
    const focused = node.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = focused?.dataset?.lifecycleToggle !== undefined ? '[data-lifecycle-toggle]'
      : focused?.dataset?.lifecycleGo ? `[data-lifecycle-go="${focused.dataset.lifecycleGo}"]` : null;
    life.html = html;
    node.innerHTML = html;
    if (focusKey) node.querySelector(focusKey)?.focus({preventScroll:true});
  }

  function renderLifecycle() {
    const state = app()?.state;
    if (!state?.book) return;
    if (state.book.id !== life.bookId) { life.bookId = state.book.id; life.overview = null; life.key = ''; life.html = ''; }
    const key = overviewKey(state);
    if (key !== life.key || life.staleAfterAnalysis) {
      life.key = key; life.staleAfterAnalysis = false;
      void loadOverview(state.book.id);
    }
    paintLifecycle();
  }

  function go(tab, target) {
    const shell = app();
    if (!shell || !BOOK_TABS.includes(tab)) return;
    shell.setTab(tab);
    const element = target === 'script' ? $('#script-heading') : target === 'record' ? $('#render-button') : target === 'export' ? $('#export-link') : null;
    const panel = element || $(`#${tab}-view`);
    if (element) {
      element.scrollIntoView?.({behavior:shell.scrollMotion?.() || 'auto', block:'center'});
      element.focus?.({preventScroll:true});
    } else {
      panel?.querySelector('h2')?.setAttribute('tabindex', '-1');
      panel?.querySelector('h2')?.focus?.({preventScroll:true});
    }
  }

  // ---- Routes ----------------------------------------------------------------------------
  const nav = {started:false, applying:false, last:''};
  function parse(hash = location.hash) {
    const parts = String(hash || '').replace(/^#\/?/, '').split('/').filter(Boolean).map(part => { try { return decodeURIComponent(part); } catch { return part; } });
    if (parts[0] === 'library') return {page:'library'};
    if (parts[0] === 'voices') return {page:'voices'};
    if (parts[0] === 'book' && parts[1]) return {book:parts[1], tab:BOOK_TABS.includes(parts[2]) ? parts[2] : 'read'};
    return null;
  }
  function currentRoute(state) {
    if (!state) return '';
    if (state.tab === 'voices' && !state.libraryView) return '#/voices';
    if (state.libraryView || !state.book) return '#/library';
    return `#/book/${encodeURIComponent(state.book.id)}/${BOOK_TABS.includes(state.tab) ? state.tab : 'read'}`;
  }

  function showVoices() {
    const shell = app();
    if (!shell) return;
    shell.renderVoices();
    shell.setTab('voices', {focus:true});
  }

  async function apply(route) {
    const shell = app();
    if (!shell || !route) return;
    nav.applying = true;
    try {
      if (route.page === 'library') shell.showLibrary();
      else if (route.page === 'voices') showVoices();
      else if (route.book) {
        if (!shell.state.books.some(book => book.id === route.book)) return;
        if (route.book !== shell.state.book?.id) {
          shell.state.tab = route.tab; shell.state.bookTab = route.tab;
          await shell.selectBook(route.book);
        }
        // Revealing from Library or Voices repaints the page; within a book only the tab changes.
        shell.setTab(route.tab);
      }
    } finally {
      nav.applying = false;
      sync(true);
    }
  }

  // Record the current place in the address bar: a new entry for navigation, a replacement otherwise.
  function sync(replace = false) {
    const state = app()?.state;
    const route = currentRoute(state);
    if (!nav.started || nav.applying || !route || route === location.hash) { nav.last = route || nav.last; return; }
    try {
      if (replace || !nav.last) history.replaceState(null, '', route); else history.pushState(null, '', route);
    } catch { /* Some embedded browsers refuse history changes; navigation still works. */ }
    nav.last = route;
  }

  function onRouteChange() {
    const state = app()?.state;
    if (!nav.started || nav.applying || location.hash === currentRoute(state)) return;
    const route = parse();
    if (route) void apply(route); else sync(true);
  }

  // ---- Breadcrumb, sidebar, narrator line -------------------------------------------------
  function paintBreadcrumb(state) {
    const node = $('#breadcrumb');
    if (!node || !state) return;
    const divider = '<span class="breadcrumb-divider" aria-hidden="true">/</span>';
    const voices = state.tab === 'voices' && !state.libraryView;
    const home = !voices && (state.libraryView || !state.book);
    const crumbs = home ? ['<span aria-current="page">Library</span>'] : ['<a href="#/library">Library</a>'];
    if (voices) crumbs.push('<span aria-current="page">Voices</span>');
    else if (!home) {
      const tab = window.BardicLifecycle?.TAB_NAMES?.[state.tab] || '';
      crumbs.push(`<a href="#/book/${esc(encodeURIComponent(state.book.id))}/read">${esc(state.book.title || 'Untitled')}</a>`, `<span aria-current="page">${esc(tab)}</span>`);
    }
    const html = crumbs.join(divider);
    if (node.innerHTML !== html) node.innerHTML = html;
  }

  function navigated() {
    const state = app()?.state;
    if (!state) return;
    paintBreadcrumb(state);
    const voices = state.tab === 'voices' && !state.libraryView;
    document.body.dataset.view = voices ? 'voices' : state.libraryView || !state.book ? 'library' : 'book';
    const back = $('#voices-back');
    if (back) back.hidden = !state.book;
    // Leaving Analyze may have accepted or set aside versions without changing the book.
    if (life.lastTab === 'analysis' && state.tab !== 'analysis') life.staleAfterAnalysis = true;
    life.lastTab = state.tab;
    if (state.book && life.staleAfterAnalysis) renderLifecycle();
    sync();
  }

  function mirrorNarrator() {
    const source = $('#listening-summary'), target = $('#narrator-line-text');
    if (!source || !target) return;
    const copy = () => {
      const text = source.textContent.trim();
      if (target.textContent !== text) target.textContent = text;
      target.classList.toggle('has-error', source.classList.contains('has-error'));
    };
    copy();
    if (typeof MutationObserver === 'function') new MutationObserver(copy).observe(source, {childList:true, characterData:true, subtree:true, attributes:true, attributeFilter:['class']});
  }

  function bind() {
    $('#book-lifecycle')?.addEventListener('click', event => {
      const toggle = event.target.closest('[data-lifecycle-toggle]');
      if (toggle) {
        life.expanded = !life.expanded;
        safeSet('bardic:lifecycle-expanded', String(life.expanded));
        paintLifecycle();
        $('#book-lifecycle [data-lifecycle-toggle]')?.focus({preventScroll:true});
        return;
      }
      const target = event.target.closest('[data-lifecycle-go]');
      if (target) go(target.dataset.lifecycleGo, target.dataset.lifecycleTarget);
    });
    $('#render-provider')?.addEventListener('change', paintLifecycle);
    $('#sidebar-voices')?.addEventListener('click', showVoices);
    $('#voices-back')?.addEventListener('click', () => app()?.setTab('cast', {focus:true}));
    $('#narrator-change')?.addEventListener('click', () => app()?.openListenSheet('live'));
    window.addEventListener('popstate', onRouteChange);
    window.addEventListener('hashchange', onRouteChange);
    mirrorNarrator();
  }

  let initial;
  function initialRoute() {
    if (initial === undefined) initial = parse();
    return initial;
  }

  // Called by app.js once the library and the first book have loaded.
  function start() {
    if (nav.started) return;
    nav.started = true;
    const route = initialRoute();
    if (route?.page) void apply(route);
    else sync(true);
  }

  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind); else bind();
  }
  window.BardicShell = {renderLifecycle, navigated, initialRoute, start, parse, currentRoute, showVoices};
})();
