"""Run day-select.js and schedule.js: the day is chosen before its grid is parsed."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from shared.renderer import _generate_js, _render_template

STATE_ID = 'ran1:RAN1#126bis'
STATE_KEY = '3gpp_schedule_state:' + STATE_ID
DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']
# Wednesday 23:30 UTC is already Thursday at the venue.
WED_NIGHT_UTC = '2026-10-14T23:30:00Z'


def run_page(*, now=WED_NIGHT_UTC, saved=None, storage_error=False, page=False, clicks=()):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required to execute browser logic')
    day_select = _render_template('day-select.js', {
        'STATE_ID_JSON': json.dumps(STATE_ID), 'MEETING_TIMEZONE_JSON': json.dumps('Asia/Seoul')})
    script = _generate_js('Asia/Seoul', auto_refresh_minutes=0, state_id=STATE_ID) if page else None
    result = subprocess.run([node, str(Path(__file__).parent / 'fixtures/day_tabs_browser.cjs')],
                            input=json.dumps({'daySelect': day_select, 'page': script, 'now': now, 'days': DAYS,
                                              'session': {STATE_KEY: json.dumps(saved)} if saved else {},
                                              'storageError': storage_error, 'clicks': list(clicks)}),
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def test_today_at_the_venue_is_shown_before_the_grids_arrive():
    state, = run_page()
    assert state['activeTabs'] == ['thursday']
    # A rule, not a class: the panel is not parsed yet when this runs.
    assert state['rule'] == '#thursday.day-panel { display: block; }'


def test_the_day_left_in_this_tab_comes_back():
    state, = run_page(saved={'activeDay': 'tuesday'})
    assert state['activeTabs'] == ['tuesday']


def test_a_saved_day_this_meeting_lacks_falls_back_to_the_first():
    state, = run_page(saved={'activeDay': 'sunday'})
    assert state['activeTabs'] == ['monday']


def test_a_day_without_sessions_falls_back_to_the_first():
    state, = run_page(now='2026-10-17T03:00:00Z')  # Saturday at the venue.
    assert state['activeTabs'] == ['monday']


def test_without_session_storage_today_is_shown():
    state, = run_page(storage_error=True)
    assert state['activeTabs'] == ['thursday']


def test_schedule_js_takes_over_the_day_and_drops_the_rule():
    before, after, friday = run_page(page=True, clicks=['friday'])
    assert after['activeTabs'] == after['activePanels'] == ['thursday']
    assert after['rule'] is None
    # Left behind, the rule would keep Thursday showing under Friday.
    assert friday['activeTabs'] == friday['activePanels'] == ['friday'] and friday['rule'] is None


def test_scroll_comes_back_only_with_the_day_it_was_left_on():
    _, kept = run_page(page=True, saved={'activeDay': 'tuesday', 'scrollX': 0, 'scrollY': 400})
    assert kept['activeTabs'] == ['tuesday'] and kept['scrolls'] == [[0, 400]]
    _, lost = run_page(page=True, saved={'activeDay': 'sunday', 'scrollX': 0, 'scrollY': 400})
    assert lost['activeTabs'] == ['monday'] and lost['scrolls'] == []
