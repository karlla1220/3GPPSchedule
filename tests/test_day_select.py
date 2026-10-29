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


def run_page(*, now=WED_NIGHT_UTC, saved=None, storage_error=False, page=False, clicks=(),
             toolbar_width=396, zoom_supported=True):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required to execute browser logic')
    day_select = _render_template('day-select.js', {
        'STATE_ID_JSON': json.dumps(STATE_ID), 'MEETING_TIMEZONE_JSON': json.dumps('Asia/Seoul')})
    script = _generate_js('Asia/Seoul', auto_refresh_minutes=0, state_id=STATE_ID) if page else None
    result = subprocess.run([node, str(Path(__file__).parent / 'fixtures/day_tabs_browser.cjs')],
                            input=json.dumps({'daySelect': day_select, 'page': script, 'now': now, 'days': DAYS,
                                              'session': {STATE_KEY: json.dumps(saved)} if saved else {},
                                              'storageError': storage_error, 'clicks': list(clicks),
                                              'toolbarWidth': toolbar_width, 'zoomSupported': zoom_supported}),
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def test_today_at_the_venue_is_shown_before_the_grids_arrive():
    state, = run_page()
    assert state['activeTabs'] == ['thursday']
    # A rule, not a class: the panel is not parsed yet when this runs.
    assert state['rule'].startswith('#thursday.day-panel { display: block; }')
    # The same names as .day-panel.active gets (schedule.css): the switch animates this grid.
    assert '#thursday.day-panel .grid-wrapper { view-transition-name: grid-frame; view-transition-class: grid-move; }' in state['rule']
    assert '#thursday.day-panel .schedule-grid { view-transition-name: grid-cells; }' in state['rule']


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


def test_the_saved_grid_zoom_comes_back_before_the_grid_is_drawn():
    # A phone left at the zoom that fits the day: no 100% grid first, no shrink at DOMContentLoaded.
    state, = run_page(saved={'activeDay': 'tuesday', 'gridZoom': 0.66})
    assert state['vars'] == {'--grid-zoom': '0.66', '--grid-width': '600px'}


def test_the_zoomed_grid_keeps_the_width_of_its_box_when_wider_than_the_minimum():
    state, = run_page(saved={'activeDay': 'tuesday', 'gridZoom': 1.5}, toolbar_width=778)
    assert state['vars'] == {'--grid-zoom': '1.5', '--axis-unzoom': str(1 / 1.5), '--grid-width': '778px'}


def test_a_zoom_below_what_fits_the_day_is_raised_to_it():
    # Saved on a wider screen (the phone has turned since).
    state, = run_page(saved={'activeDay': 'tuesday', 'gridZoom': 0.5}, toolbar_width=396)
    assert state['vars']['--grid-zoom'] == str(396 / 600)


def test_the_zoom_comes_back_only_with_its_day_and_where_zoom_works():
    for kwargs in ({'saved': {'activeDay': 'sunday', 'gridZoom': 0.66}},   # Its day is gone: first day at 100%.
                   {'saved': {'activeDay': 'tuesday', 'gridZoom': 1}},
                   {'saved': {'activeDay': 'tuesday', 'gridZoom': 0.66}, 'zoom_supported': False}):
        state, = run_page(**kwargs)
        assert state['vars'] == {}, kwargs
