# UI guide

This page covers how new browser UI is built. The visual reference is the "Choose your narrator" listen sheet and the reader appearance panel. New screens should look like them: option cards, pill chips, one primary action and quiet help text.

**Copy from [kitchen-sink.html](../bardic/static/kitchen-sink.html) (served at `/static/kitchen-sink.html`), not from a sibling feature file.** Sibling files contain the drift this system replaces. For example, there are eleven error reds and five badge families.

## Files

| File | Holds |
| --- | --- |
| [tokens.css](../bardic/static/tokens.css) | Every raw colour and the type, space, radius, shadow, control-height, focus, layer and motion scales. It loads first. Old names such as `--green` and `--muted` are aliases; new code uses the semantic names. |
| [components.css](../bardic/static/components.css) | The primitives, built from tokens only. It loads second. Legacy stylesheets load after it, so a legacy contextual rule (`.x .button`) still wins inside legacy screens. |
| [ui.js](../bardic/static/ui.js) | `window.BardicUI`: string builders for the primitives, `fmt`, `statusTone`, `setMessage`, `openConsent` and `bindChoices`, plus the one `esc`. |
| [kitchen-sink.html](../bardic/static/kitchen-sink.html) | Every token and primitive, rendered by `BardicUI`. It is also the screenshot baseline. |

## Tokens

- **Colour:**
  - `--color-text` on `--color-bg`, `--color-bg-sunken` or `--color-surface`.
  - `--color-text-muted` for help and labels.
  - `--color-border` for decorative edges.
  - `--color-border-strong` for form-control edges.
  - `--color-accent` (plum) and `--color-accent-soft` for selection.
  - `--color-focus` for focus rings.
- **Status:** `--tone-{neutral,good,info,warn,bad,accent}-{text,bg}`. Never pick a status colour by hand.
- **Type:**
  - `--font-serif` / `--font-sans`.
  - `--text-2xs` (11px, the floor) through `--text-4xl` (38px), plus `--text-display`.
  - `--leading-*` and `--weight-*`.
- **Space:** `--space-1` = 4px, `--space-2` = 8px, and so on, with `--space-0-5` (2px) and `--space-1-5` (6px) as half steps.
- **Shape:**
  - `--radius-md` for buttons.
  - `--radius-xl` for option cards.
  - `--radius-pill` for chips.
  - `--shadow-1..3`.
- **Controls:** `--control-sm` (32px), `--control-md` (40px), `--control-touch` (44px), `--control-lg` (48px).

Breakpoints cannot be custom properties. Use 620, 850, 1100 and 1450px. Contrast is tested in `tests/test_ui_contrast.py`: text must reach 4.5:1, and borders, focus rings and selected fills must reach 3:1, including the four reader themes. Add a pair there when you add a token.

## Which primitive

| Need | Use |
| --- | --- |
| An action | `BardicUI.button({label, variant, size})`. Variants: `primary` (one per surface), `subtle`, `text` and `danger`. Sizes: `small` for dense rows, default, and `large` for the last action of a sheet or consent. A busy button is disabled and sets `aria-busy`. |
| A status | `BardicUI.statusBadge(state)` or `badge(text, statusTone(state))`. Use one badge per status. `statusTone` is the only map; add new states there. Cancelled, failed and interrupted stay distinct. |
| A block that stays (what happened, what is kept, what to do) | `BardicUI.callout({tone, title, text, actions})`. |
| A line that changes (saved, failed) | Render `BardicUI.message({id})` empty, then call `BardicUI.setMessage(node, text, {tone})`. |
| The top of a tab or panel | `BardicUI.sectionHead({title, lead, actions, next})`. |
| Workflow status | `BardicUI.steps({steps, next})` for the compact strip, or `{variant:'cards'}` for stage cards. Each step has exactly one state and one tone. |
| Anything that may cost money or send text off the computer | `BardicUI.consent({scope, sends, estimate, confirm, blocked})`, then `openConsent(node)`. The confirm label carries the cost. `estimate.cost` missing means **Cost unknown**, never $0. When a key or server is missing, pass `blocked:{reason, setupAttrs}`. The confirm is then disabled and a **Set up →** link is shown. |
| A choice among options | `BardicUI.choice({kind:'segmented'|'cards'|'chips', options, value})` with `bindChoices(container, onChange)`. Exclusive choices are radio groups: one tab stop, and arrow, Home and End keys move and select. Toggles use `mode:'pressed'`. |
| Numbers | `BardicUI.fmt.money` (`about $0.02`, `<$0.01`, `unknown`), `fmt.percent` (never rounds a partial value to 0% or 100%) and `fmt.plural` (`1 chapter`). |

Builders return strings for the existing `innerHTML` plus `data-*` delegation model. They escape every text argument. Options named `html`, `actions` or `iconHtml` take markup you have already built. The data table, card, panel, disclosure, field, empty state, modal and toast primitives are not in the kit yet. Migrate a screen to the kit when you rewrite it, and move the whole file in one change so that old and new escaping never mix.

## Rules

- **Serif for story, sans for operations.** Use serif for book text, chapter titles, character and voice names, and section heads. Use sans 600 for panels, runs, tables, settings and every control.
- **Headings.** The H2 names the job ("Analyze the story"), not a tagline. The lead has at most two sentences. A section has at most one **Next →**. Eyebrows are real context labels, uppercased by CSS, not typed in capitals.
- **Consent.** Nothing paid starts without an estimate and a confirm. Unknown is shown as unknown.
- **No new literals.** `tests/test_ui_budget.py` ratchets five counts: raw colours outside `tokens.css`, literal font sizes, `!important`, `*-badge|*-message|*-error|*-help` class names, and copies of the escape helper outside `ui.js`. A count may fall but never rise. When one falls, lower `tests/ui_budget.json`.
- **Cascade.** `style.css` still loads after nine feature stylesheets, and its later "Workspace shell" rules override equal-specificity rules above them. Do not move a rule earlier, or change the load order, without a cascade-equivalence check. The phase-1 fold was proved that way, both statically and with computed styles.
