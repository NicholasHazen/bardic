"""UI drift ratchet: five counts over bardic/static may go down but never up.

When a count drops, lower it in tests/ui_budget.json (or run with BARDIC_UPDATE_UI_BUDGET=1).
When one rises, use a token from tokens.css or a primitive from components.css / ui.js instead;
see docs/UI-GUIDE.md and /static/kitchen-sink.html. Raise the baseline only with the owner's agreement.
"""
import json
import os
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / 'bardic' / 'static'
BASELINE = Path(__file__).with_name('ui_budget.json')
COLOR = re.compile(r'#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|hsla)\(')
FONT_SIZE = re.compile(r'font-size\s*:\s*(?!var\(|inherit|initial|unset)', re.I)
FAMILY = re.compile(r'\b[a-z0-9]+(?:-[a-z0-9]+)*-(?:badge|message|error|help)\b')
ESCAPE = re.compile(r"""\.replace\(/\[&<>"'\]/g""")


def css(name_filter=lambda name: True):
    for path in sorted(STATIC.glob('*.css')):
        if name_filter(path.name):
            yield path.name, re.sub(r'/\*.*?\*/', '', path.read_text(encoding='utf-8'), flags=re.S)


def rule_bodies(text):
    return re.findall(r'\{([^{}]*)\}', text)   # innermost blocks are declaration lists


def measure():
    families = set()
    for _, text in css(lambda name: name != 'components.css'):
        families |= {m.group(0) for sel in re.findall(r'([^{}]+)\{[^{}]*\}', text) for m in re.finditer(r'\.' + FAMILY.pattern, sel)}
    markup = [p.read_text(encoding='utf-8') for p in [*STATIC.glob('*.js'), *STATIC.glob('*.html')] if p.name not in ('ui.js', 'kitchen-sink.html')]
    for text in markup:
        for attr in re.findall(r'class="([^"]*)"', text):
            families |= set(FAMILY.findall(attr))
    return {
        'raw_colors_outside_tokens': sum(len(COLOR.findall(body)) for _, text in css(lambda n: n != 'tokens.css') for body in rule_bodies(text)),
        'font_size_literals': sum(len(FONT_SIZE.findall(body)) for _, text in css() for body in rule_bodies(text)),
        'important': sum(text.count('!important') for _, text in css()),
        'badge_message_error_help_classes': len({name.lstrip('.') for name in families}),
        'escape_helpers_outside_ui_js': sum(len(ESCAPE.findall(p.read_text(encoding='utf-8'))) for p in STATIC.glob('*.js') if p.name != 'ui.js'),
    }


def test_ui_budget_never_grows():
    current = measure()
    if os.environ.get('BARDIC_UPDATE_UI_BUDGET') == '1':
        BASELINE.write_text(json.dumps(current, indent=2) + '\n')
        return
    baseline = json.loads(BASELINE.read_text())
    grew = {k: (baseline[k], v) for k, v in current.items() if v > baseline.get(k, 0)}
    assert not grew, (f'UI drift grew (baseline, now): {grew}. Use tokens.css tokens and components.css / ui.js primitives; '
                      'copy from /static/kitchen-sink.html. See docs/UI-GUIDE.md.')
    shrank = {k: (baseline[k], v) for k, v in current.items() if v < baseline[k]}
    if shrank:
        print(f'UI drift shrank {shrank}: lower tests/ui_budget.json to lock it in '
              '(BARDIC_UPDATE_UI_BUDGET=1 uv run --frozen pytest tests/test_ui_budget.py).')


def test_new_ui_files_use_tokens_only():
    for name in ('components.css',):
        text = dict(css())[name]
        assert not COLOR.findall(text), f'{name} must use colour tokens'
        assert not FONT_SIZE.findall(text), f'{name} must use --text-* tokens'
