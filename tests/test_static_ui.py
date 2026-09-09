#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''
Tests for invariants in the served HTML that a Python test can still catch
'''

import re
from pathlib import Path

STATIC = (Path(__file__).resolve().parents[1] / 'src' / 'ibx_kentik_prepop'
          / 'web' / 'static')


def markup():
    return (STATIC / 'index.html').read_text(encoding='utf-8')


def hidden_classes(text):
    '''
    Every non-'hidden' class that appears on an element also carrying 'hidden'
    '''
    classes = set()
    for value in re.findall(r'class="([^"]*\bhidden\b[^"]*)"', text):
        for name in value.split():
            if name != 'hidden':
                classes.add(name)
    return classes


def rules_setting_display(text, class_name):
    '''
    Bodies of rules whose selector is exactly this one class and which set
    display
    '''
    pattern = re.compile(r'(?<![\w.-])\.' + re.escape(class_name) +
                         r'\s*\{([^}]*)\}')
    return [body for body in pattern.findall(text) if 'display:' in body]


def test_hidden_beats_any_display_on_the_same_element():
    '''
    .hidden and a single-class rule have equal specificity, so a class that
    sets display and is used together with 'hidden' needs its own
    .class.hidden override - without it the element shows on page load.
    '''
    text = markup()
    offenders = []

    for class_name in sorted(hidden_classes(text)):
        if not rules_setting_display(text, class_name):
            continue
        override = re.search(r'\.' + re.escape(class_name) +
                             r'\.hidden\s*\{[^}]*display:\s*none', text)
        if not override:
            offenders.append(class_name)

    assert offenders == [], (
        f'These classes set display and are used with "hidden" but have no '
        f'.<class>.hidden override, so they render on load: {offenders}')


def test_the_modal_starts_hidden():
    text = markup()
    modal = re.search(r'<div[^>]*id="confirm_modal"[^>]*>', text).group(0)
    assert 'hidden' in modal
    assert re.search(r'\.modal\.hidden\s*\{[^}]*display:\s*none', text)


def test_every_card_the_script_toggles_exists_in_the_markup():
    text = markup()
    script = (STATIC / 'app.js').read_text(encoding='utf-8')
    for element_id in sorted(set(re.findall(r"show\('([a-z_]+)'", script))):
        assert f'id="{element_id}"' in text, f'show() targets missing {element_id}'
