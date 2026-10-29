"""Run wg-switch.js: the meeting menu, its one-time hint, and fetching ahead."""
from dataclasses import replace
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from shared.navigation import render_navigation
from schedule_fixture import build_schedule

SEEN_KEY = '3gpp_schedule_wg_switch_seen'
IDS = ['ran-plenary', 'ran1', 'ran2']
# The other meetings as the menu lists them: one in progress, one ended.
ITEMS = [{'href': '/ran2/'}, {'href': '/ran-plenary/', 'past': True}]


def switch_script(ids=IDS):
    meetings = {wg: replace(build_schedule(), wg_id=wg, meeting_id=wg, meeting_name=wg.upper(), is_demo=False,
                            starts_on='2000-01-01', ends_on='2000-01-02') for wg in ids}
    html = render_navigation(meetings[ids[1]], meetings, [{'id': wg} for wg in ids])
    return re.search(r'<script>(.*)</script>', html, re.S).group(1)


def run_menu(*, steps=(), stored=None, storage_error=False, rules=True, script=None):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required to execute browser logic')
    result = subprocess.run([node, str(Path(__file__).parent / 'fixtures/wg_switch_browser.cjs')],
                            input=json.dumps({'script': script or switch_script(), 'items': ITEMS,
                                              'stored': stored, 'storageError': storage_error,
                                              'rules': rules, 'steps': list(steps)}),
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def test_the_hint_shows_once_and_opening_the_menu_dismisses_it():
    first, opened = run_menu(steps=['open'])
    assert first['hint'] == 'shown'
    assert opened['hint'] == 'hidden' and json.loads(opened['stored'][SEEN_KEY]) == IDS
    later, = run_menu(stored={SEEN_KEY: json.dumps(IDS)})
    assert later['hint'] == 'hidden'


def test_got_it_dismisses_the_hint_without_opening_the_menu():
    _, dismissed = run_menu(steps=['got-it'])
    assert dismissed['hint'] == 'hidden' and not dismissed['open'] and SEEN_KEY in dismissed['stored']


def test_a_new_wg_brings_the_hint_back():
    state, = run_menu(stored={SEEN_KEY: json.dumps(['ran1', 'ran2'])})
    assert state['hint'] == 'shown'


def test_without_storage_the_hint_never_shows():
    # It could not be dismissed for good, so it would show on every visit.
    state, = run_menu(storage_error=True)
    assert state['hint'] == 'hidden'


def test_opening_the_menu_renders_active_meetings_ahead_and_fetches_ended_ones():
    before, opened = run_menu(steps=['open'])
    assert before['head'] == []
    assert opened['head'] == [{'type': 'speculationrules', 'rules': {
        'prerender': [{'urls': ['https://example.test/ran2/'], 'eagerness': 'immediate'}],
        'prefetch': [{'urls': ['https://example.test/ran-plenary/'], 'eagerness': 'immediate'}],
    }}]


def test_a_pointer_on_the_title_starts_it_and_it_happens_once():
    *_, last = run_menu(steps=['pointerenter', 'open', 'close', 'open'])
    assert len(last['head']) == 1


def test_without_speculation_rules_every_meeting_is_prefetched():
    _, opened = run_menu(rules=False, steps=['open'])
    assert opened['head'] == [{'rel': 'prefetch', 'href': 'https://example.test/ran2/'},
                              {'rel': 'prefetch', 'href': 'https://example.test/ran-plenary/'}]
    _, older = run_menu(rules=None, steps=['open'])  # No HTMLScriptElement.supports at all.
    assert [el['rel'] for el in older['head']] == ['prefetch', 'prefetch']


def test_a_page_rendered_ahead_drops_the_hint_the_visitor_dismissed_meanwhile():
    # Rendered on hover, before the menu on the previous page was opened.
    rendered, _, shown = run_menu(steps=[{'storeElsewhere': [SEEN_KEY, json.dumps(IDS)]}, 'activate'])
    assert rendered['hint'] == 'shown' and shown['hint'] == 'hidden'


def test_outside_click_and_escape_close_the_menu():
    _, _, inside, outside = run_menu(steps=['open', 'click-item', 'click-outside'])
    assert inside['open'] and not outside['open']
    _, _, escaped = run_menu(steps=['open', 'escape'])
    assert not escaped['open'] and escaped['focused']
