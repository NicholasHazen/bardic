"""WCAG 2.x contrast of the design tokens and reader themes: text >= 4.5:1, controls and focus >= 3:1."""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / 'bardic' / 'static'


def tokens():
    text = (STATIC / 'tokens.css').read_text(encoding='utf-8')
    raw = dict(re.findall(r'--([\w-]+)\s*:\s*([^;]+);', text))

    def resolve(name):
        value = raw[name].strip()
        alias = re.fullmatch(r'var\(--([\w-]+)\)', value)
        return resolve(alias.group(1)) if alias else value
    return {name: resolve(name) for name in raw}


def reader_themes():
    text = (STATIC / 'reader.css').read_text(encoding='utf-8')
    base = dict(re.findall(r'--reader-([\w-]+):(#[0-9a-fA-F]{6})', re.search(r'body\.reader-mode \{(.*?)\}', text, re.S).group(1)))
    themes = {'paper': base}
    for name, body in re.findall(r'body\.reader-mode\[data-reader-theme=(\w+)\] \{(.*?)\}', text, re.S):
        themes[name] = {**base, **dict(re.findall(r'--reader-([\w-]+):(#[0-9a-fA-F]{6})', body))}
    return themes


def ratio(a, b):
    def luminance(color):
        channels = [int(color.lstrip('#')[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


T = tokens()
SURFACES = ['color-bg', 'color-bg-sunken', 'color-surface', 'color-hover', 'color-accent-soft']
TEXT_PAIRS = [(fg, bg) for fg in ('color-text', 'color-text-muted') for bg in SURFACES]
TEXT_PAIRS += [('color-accent', bg) for bg in ('color-bg', 'color-surface', 'color-accent-soft')]
TEXT_PAIRS += [('color-on-accent', 'color-accent'), ('color-on-accent', 'color-accent-hover'), ('color-inverse-text', 'color-inverse-bg')]
TONES = ('neutral', 'good', 'info', 'warn', 'bad')
TEXT_PAIRS += [(f'tone-{t}-text', bg) for t in TONES for bg in (f'tone-{t}-bg', 'color-bg', 'color-surface')]
TEXT_PAIRS += [('color-text', f'tone-{t}-bg') for t in TONES] + [('tone-accent-text', 'tone-accent-bg')]
UI_PAIRS = [('color-border-strong', bg) for bg in ('color-bg', 'color-bg-sunken', 'color-surface')]
UI_PAIRS += [('color-focus', bg) for bg in ('color-bg', 'color-bg-sunken', 'color-surface', 'color-accent-soft')]
UI_PAIRS += [('color-accent', 'color-surface')]   # selected chip or segment fill


@pytest.mark.parametrize('fg,bg', TEXT_PAIRS)
def test_text_tokens_meet_aa(fg, bg):
    assert ratio(T[fg], T[bg]) >= 4.5, f'{fg} on {bg}: {ratio(T[fg], T[bg]):.2f}'


@pytest.mark.parametrize('fg,bg', UI_PAIRS)
def test_control_and_focus_tokens_meet_3_to_1(fg, bg):
    assert ratio(T[fg], T[bg]) >= 3, f'{fg} on {bg}: {ratio(T[fg], T[bg]):.2f}'


@pytest.mark.parametrize('theme', sorted(reader_themes()))
def test_reader_themes(theme):
    t = reader_themes()[theme]
    assert ratio(t['ink'], t['bg']) >= 4.5
    assert ratio(t['muted'], t['bg']) >= 4.5
    assert ratio(t['accent'], t['bg']) >= 4.5            # accent is also link text and the focus ring
    assert ratio(t['accent-ink'], t['accent']) >= 4.5


def test_reader_focus_ring_follows_the_theme():
    assert re.search(r'body\.reader-mode :focus-visible:not\(dialog, dialog \*\) \{ outline-color:var\(--reader-accent\); \}',
                     (STATIC / 'reader.css').read_text(encoding='utf-8'))


def test_toasts_are_readable():
    style = (STATIC / 'style.css').read_text(encoding='utf-8')
    error_bg = re.search(r'\.toast\.error \{\s*background:(#[0-9a-fA-F]{6})', style).group(1)
    assert re.search(r'\.toast \{[^}]*color:var\(--paper\)', style)
    assert ratio(T['color-bg'], T['color-text']) >= 4.5 and ratio(T['color-bg'], error_bg) >= 4.5
