/* Bardic UI kit: string builders and small DOM helpers for the primitives in
   components.css. Exposes window.BardicUI. Every builder escapes text with the one
   `esc` below; options named `html`, `actions` or `iconHtml` take markup the caller
   already built (and escaped). kitchen-sink.html renders each builder; copy from it.
   This file is new code only: existing screens migrate when they are next rewritten. */
(() => {
  'use strict';
  const root = typeof window !== 'undefined' ? window : globalThis;

  // ---- Escaping and attributes -------------------------------------------------
  const ESCAPES = {'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'};
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ESCAPES[ch]);
  const ATTR_NAME = /^[a-zA-Z_:][-a-zA-Z0-9_:.]*$/;
  function attrs(map = {}) {
    let out = '';
    for (const [name, value] of Object.entries(map || {})) {
      if (!ATTR_NAME.test(name)) throw new Error(`Invalid attribute name: ${name}`);
      if (value === false || value === null || value === undefined) continue;
      out += value === true ? ` ${name}` : ` ${name}="${esc(value)}"`;
    }
    return out;
  }
  const cx = (...names) => names.flat().filter(Boolean).join(' ');
  let counter = 0;
  const uid = prefix => `${prefix}-${++counter}`;

  // ---- Formatting: unknown is never zero -----------------------------------------
  const isNumber = value => typeof value === 'number' && Number.isFinite(value);
  const fmt = {
    number(value, {digits = 2, unknown = 'unknown'} = {}) {
      return isNumber(value) ? value.toLocaleString('en-US', {maximumFractionDigits:digits}) : unknown;
    },
    /** US dollars. Missing, null or NaN is "unknown", never $0. Sub-cent amounts read "<$0.01". */
    money(value, {approx = false, precise = false, unknown = 'unknown'} = {}) {
      if (!isNumber(value)) return unknown;
      if (value < 0) return '-' + fmt.money(-value, {approx, precise, unknown});
      let text;
      if (value === 0) text = '$0.00';
      else if (value < 0.01) text = !precise ? '<$0.01' : value < 0.0001 ? '<$0.0001' : '$' + value.toFixed(4).replace(/0+$/, '');
      else text = '$' + value.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:precise ? 4 : 2});
      return approx && value !== 0 ? `about ${text}` : text;
    },
    /** A fraction (0-1) as a whole percentage. Never rounds a partial value to 0% or 100%. */
    percent(value, {fraction = true, unknown = 'unknown'} = {}) {
      if (!isNumber(value)) return unknown;
      const pct = fraction ? value * 100 : value;
      if (pct > 0 && pct < 1) return '<1%';
      if (pct > 99 && pct < 100) return '99%';
      return `${Math.round(pct)}%`;
    },
    /** "1 chapter", "2 chapters", "unknown chapters". */
    plural(count, singular, plural = `${singular}s`) {
      if (!isNumber(count)) return `unknown ${plural}`;
      return `${fmt.number(count, {digits:0})} ${count === 1 ? singular : plural}`;
    },
  };

  // ---- Status tones: one map for every status string --------------------------------
  const TONES = ['neutral', 'good', 'info', 'warn', 'bad', 'accent'];
  const TONE_STATES = {
    good:['accepted', 'current', 'complete', 'completed', 'ready', 'done', 'succeeded', 'success', 'finished', 'in_use', 'playing', 'saved'],
    info:['running', 'queued', 'preparing', 'in_progress', 'active', 'loading', 'buffering'],
    warn:['stale', 'partial', 'budget_limited', 'quota_limited', 'uncertain', 'provisional', 'interrupted_unknown', 'interrupted', 'blocked',
      'limit_reached', 'rate_limited', 'waiting_rate_limit', 'needs_review', 'out_of_date', 'unmeasured'],
    bad:['failed', 'rejected', 'error'],
    accent:['candidate', 'partly_accepted', 'proposed', 'draft'],
    neutral:['superseded', 'empty', 'idle', 'cancelled', 'canceled', 'stopped', 'stopped_by_you', 'paused', 'pending',
      'not_started', 'needs_narrator', 'skipped', 'unknown', 'none'],
  };
  const TONE_OF = new Map(Object.entries(TONE_STATES).flatMap(([tone, states]) => states.map(state => [state, tone])));
  const stateKey = state => String(state ?? '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  const statusTone = state => TONE_OF.get(stateKey(state)) || 'neutral';
  const statusLabel = state => { const text = stateKey(state).replace(/_/g, ' '); return text ? text[0].toUpperCase() + text.slice(1) : 'Unknown'; };
  const tone = value => TONES.includes(value) ? value : 'neutral';

  // ---- Buttons ----------------------------------------------------------------------
  const VARIANTS = {primary:'primary', subtle:'subtle', text:'text-button', danger:'danger', 'danger-primary':'primary danger'};
  function button({label, variant = 'subtle', size, type = 'button', disabled = false, busy = false, busyLabel, iconHtml = '', attrs:extra = {}} = {}) {
    const classes = cx('button', VARIANTS[variant] ?? VARIANTS.subtle, (size === 'small' || size === 'large') && size);
    return `<button${attrs({type, class:classes, disabled:disabled || busy, 'aria-busy':busy ? 'true' : false, ...extra})}>${iconHtml}${esc(busy && busyLabel ? busyLabel : label)}</button>`;
  }

  // ---- Badge ------------------------------------------------------------------------
  const badge = (text, toneName = 'neutral') => `<span class="badge" data-tone="${tone(toneName)}">${esc(text)}</span>`;
  const statusBadge = (state, label) => badge(label ?? statusLabel(state), statusTone(state));

  // ---- Callout and message ------------------------------------------------------------
  function callout({tone:toneName = 'neutral', title, text, html = '', actions = '', attrs:extra = {}} = {}) {
    return `<div${attrs({class:'callout', 'data-tone':tone(toneName), ...extra})}>${title ? `<p class="callout-title">${esc(title)}</p>` : ''}${text ? `<p>${esc(text)}</p>` : ''}${html}${actions ? `<div class="callout-actions">${actions}</div>` : ''}</div>`;
  }
  /** An empty live region. Fill it later with setMessage so screen readers announce the change. */
  const message = ({id, attrs:extra = {}} = {}) => `<p${attrs({class:'message', id, role:'status', ...extra})}></p>`;
  function setMessage(node, text, {tone:toneName} = {}) {
    if (!node) return node;
    const value = toneName ? tone(toneName) : '';
    node.setAttribute('role', value === 'bad' ? 'alert' : 'status');
    if (value) node.setAttribute('data-tone', value); else node.removeAttribute('data-tone');
    node.textContent = text ?? '';
    return node;
  }

  // ---- Section head -------------------------------------------------------------------
  const sentences = text => (String(text ?? '').trim().match(/[^.!?]+(?:[.!?]+(?=\s|$)|$)/g) || []).filter(part => part.trim()).length;
  function sectionHead({eyebrow, title, lead, actions = '', next, level = 2, id} = {}) {
    if (lead && sentences(lead) > 2) root.console?.warn?.(`BardicUI.sectionHead: keep the lead to two sentences (${title})`);
    const h = level === 3 ? 'h3' : 'h2';
    return `<header class="section-head"><div class="section-head-text">${eyebrow ? `<p class="section-head-eyebrow">${esc(eyebrow)}</p>` : ''}<${h}${attrs({id})}>${esc(title)}</${h}>${lead ? `<p class="section-head-lead">${esc(lead)}</p>` : ''}</div>${actions ? `<div class="section-head-actions">${actions}</div>` : ''}${next ? `<button${attrs({type:'button', class:'section-head-next', ...(next.attrs || {})})}>${esc(next.label)} <span aria-hidden="true">→</span></button>` : ''}</header>`;
  }

  // ---- Steps: one state per step, compact strip or stage cards ---------------------------
  function steps({label, variant = 'compact', steps:items = [], next} = {}) {
    const cards = variant === 'cards';
    const rows = items.map(step => {
      const toneName = statusTone(step.state), state = step.stateLabel ?? statusLabel(step.state);
      const common = {'data-tone':toneName, 'data-step':step.id, 'aria-current':step.current ? 'step' : false};
      if (!cards) return `<li${attrs({class:'step', ...common})}><span class="step-dot" aria-hidden="true"></span><span class="step-label">${esc(step.label)}</span><span class="step-state">${esc(state)}</span></li>`;
      const uses = step.uses?.length ? `<p class="step-card-uses">Uses ${esc(step.uses.join(', '))}</p>` : '';
      const action = step.action ? button({variant:'subtle', size:'small', ...step.action}) : '';
      return `<li${attrs({class:'step-card', ...common})}><div class="step-card-head"><p class="step-card-title">${esc(step.label)}</p>${badge(state, toneName)}</div>${step.detail ? `<p class="step-card-detail">${esc(step.detail)}</p>` : ''}${action}${uses}</li>`;
    }).join('');
    const nextItem = next && !cards ? `<li class="steps-next">${button({variant:'text', size:'small', label:`${next.label} →`, attrs:next.attrs})}</li>` : '';
    return `<ol${attrs({class:'steps', 'data-variant':cards ? 'cards' : 'compact', 'aria-label':label})}>${rows}${nextItem}</ol>`;
  }

  // ---- Consent: estimate before any paid work -------------------------------------------
  const DEFAULT_CAP_NOTE = 'Bardic does not cap this spending. Your provider bills your account directly, and its bill is authoritative.';
  function confirmLabel(label, estimate = {}) {
    if (estimate.free) return `${label} · no charge`;
    return isNumber(estimate.cost) ? `${label} · ${fmt.money(estimate.cost, {approx:true})}` : `${label} · cost unknown`;
  }
  function consent({id = uid('consent'), title = 'Review before running', scope, sends = [], estimate = {}, capNote = DEFAULT_CAP_NOTE,
    confirm = 'Run', cancel = 'Cancel', blocked, confirmAttrs = {}, cancelAttrs = {}} = {}) {
    const blockedId = `${id}-blocked`;
    const cost = estimate.free ? 'No charge' : isNumber(estimate.cost) ? esc(fmt.money(estimate.cost, {approx:true})) : '<span class="consent-unknown">Cost unknown</span>';
    const requests = estimate.requests === undefined ? '' : `${esc(fmt.plural(estimate.requests, 'request'))} · `;
    const sendList = sends.length ? `<ul>${sends.map(item => `<li>${esc(item.what)} → ${esc(item.where)}</li>`).join('')}</ul>` : 'Nothing leaves this computer.';
    const blockedHtml = blocked ? `<p class="consent-blocked" id="${esc(blockedId)}"><span>${esc(blocked.reason)}</span>${blocked.setupLabel === null ? '' : button({variant:'text', label:blocked.setupLabel || 'Set up →', attrs:blocked.setupAttrs || {}})}</p>` : '';
    return `<section${attrs({class:'consent', id, tabindex:'-1', 'aria-labelledby':`${id}-title`, 'data-consent':''})}><h3 class="consent-title" id="${esc(id)}-title">${esc(title)}</h3>`
      + `<dl class="consent-facts"><dt>Scope</dt><dd>${esc(scope)}</dd><dt>Sends</dt><dd>${sendList}</dd><dt>Estimate</dt><dd>${requests}${cost}${estimate.note ? `<br><small>${esc(estimate.note)}</small>` : ''}</dd></dl>`
      + `${capNote ? `<p class="consent-note">${esc(capNote)}</p>` : ''}${blockedHtml}`
      + `<div class="consent-actions">${button({variant:'subtle', label:cancel, attrs:{'data-consent-cancel':'', ...cancelAttrs}})}${button({variant:'primary', label:confirmLabel(confirm, estimate), disabled:Boolean(blocked), attrs:{'data-consent-confirm':'', 'aria-describedby':blocked ? blockedId : false, ...confirmAttrs}})}</div></section>`;
  }
  /** Bring a just-rendered consent block into view and move focus to it. */
  function openConsent(node) {
    if (!node) return node;
    const reduce = Boolean(root.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches);
    node.scrollIntoView?.({block:'nearest', behavior:reduce ? 'auto' : 'smooth'});
    node.focus?.({preventScroll:true});
    return node;
  }

  // ---- Choice: segmented, cards or chips ---------------------------------------------------
  function choice({kind = 'segmented', label, name, value, options = [], mode = 'radio'} = {}) {
    const radio = mode !== 'pressed';
    const selected = new Set([].concat(value ?? []).map(String));
    const focusable = radio ? (options.find(o => selected.has(String(o.value)) && !o.disabled) || options.find(o => !o.disabled)) : null;
    const buttons = options.map(option => {
      const on = selected.has(String(option.value));
      const body = kind === 'cards' ? `<strong>${esc(option.label)}</strong>${option.hint ? `<small>${esc(option.hint)}</small>` : ''}` : esc(option.label);
      const state = radio ? {role:'radio', 'aria-checked':String(on), tabindex:option === focusable ? '0' : '-1'} : {'aria-pressed':String(on)};
      return `<button${attrs({type:'button', ...state, 'data-value':option.value, disabled:Boolean(option.disabled), 'aria-label':option.ariaLabel})}>${body}</button>`;
    }).join('');
    return `<div${attrs({class:'choice', 'data-kind':['segmented', 'cards', 'chips'].includes(kind) ? kind : 'segmented', role:radio ? 'radiogroup' : 'group', 'aria-label':label, 'data-choice':name})}>${buttons}</div>`;
  }
  const choiceButtons = group => Array.from(group.children || []).filter(node => node.tagName === 'BUTTON');
  /** Index to move to for an arrow/Home/End key, skipping disabled options; -1 for other keys. */
  function nextChoiceIndex(key, current, disabled) {
    const count = disabled.length, enabled = disabled.map((off, index) => off ? -1 : index).filter(index => index >= 0);
    if (!enabled.length) return -1;
    if (key === 'Home') return enabled[0];
    if (key === 'End') return enabled[enabled.length - 1];
    const step = key === 'ArrowRight' || key === 'ArrowDown' ? 1 : key === 'ArrowLeft' || key === 'ArrowUp' ? -1 : 0;
    if (!step) return -1;
    for (let i = 1; i <= count; i++) { const index = ((current + step * i) % count + count) % count; if (!disabled[index]) return index; }
    return -1;
  }
  function selectChoice(group, target, {focus = false} = {}) {
    if (target.getAttribute('role') === 'radio') {
      for (const node of choiceButtons(group)) {
        node.setAttribute('aria-checked', String(node === target));
        node.setAttribute('tabindex', node === target ? '0' : '-1');
      }
    } else {
      target.setAttribute('aria-pressed', String(target.getAttribute('aria-pressed') !== 'true'));
    }
    if (focus) target.focus?.();
    return {name:group.dataset?.choice, value:target.dataset?.value, pressed:target.getAttribute('aria-pressed') === 'true', group, button:target};
  }
  /** Wire every .choice inside `container`: click selects, arrow keys move and select (radio groups). */
  function bindChoices(container, onChange = () => {}) {
    const groupOf = node => node?.closest?.('.choice');
    container.addEventListener('click', event => {
      const target = event.target.closest?.('.choice > button');
      const group = groupOf(target);
      if (!target || !group || target.disabled) return;
      onChange(selectChoice(group, target));
    });
    container.addEventListener('keydown', event => {
      const target = event.target.closest?.('.choice > [role=radio]');
      const group = groupOf(target);
      if (!target || !group) return;
      const buttons = choiceButtons(group);
      const index = nextChoiceIndex(event.key, buttons.indexOf(target), buttons.map(node => Boolean(node.disabled)));
      if (index < 0) return;
      event.preventDefault();
      onChange(selectChoice(group, buttons[index], {focus:true}));
    });
    return container;
  }

  root.BardicUI = Object.freeze({
    esc, attrs, cx, fmt, statusTone, statusLabel, TONES:Object.freeze([...TONES]),
    button, badge, statusBadge, callout, message, setMessage, sentences, sectionHead, steps,
    consent, confirmLabel, openConsent, choice, nextChoiceIndex, selectChoice, bindChoices,
  });
})();
