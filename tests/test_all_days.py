"""The All tab: every day of a meeting on one time axis."""
import json
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace

from bs4 import BeautifulSoup
import pytest

from schedule_fixture import build_schedule
from shared.renderer import _room_label, _render_template, _week_rooms, generate_html
from shared.schedule import RoomInfo, Timeline, time_to_minutes


def page(schedule=None):
    return BeautifulSoup(generate_html(schedule or build_schedule()), 'html.parser')


def rows(cell):
    start, end = re.search(r'grid-row:(\d+)/(\d+)', cell['style']).groups()
    return int(start), int(end)


def columns(cell):
    start, end = re.search(r'grid-column:(\d+)/(\d+)', cell['style']).groups()
    return int(start), int(end)


def test_the_all_tab_comes_after_the_days():
    schedule = build_schedule()
    tabs = page(schedule).select('.schedule-toolbar .tab')
    assert [t['data-day'] for t in tabs] == [d.day_name.lower() for d in schedule.days] + ['all-days']
    assert tabs[-1].text == 'All'


def test_a_meeting_of_one_day_has_no_all_tab():
    schedule = build_schedule()
    schedule.days = schedule.days[:1]
    html = page(schedule)
    assert not html.select('.tab-all') and not html.select('#all-days')


def test_every_cell_is_there_once_and_borrows_its_days_popup():
    html = page()
    cells = html.select('#all-days .session-block')
    days = html.select('.day-panel:not(.all-days) .session-block')
    assert len(cells) == len(days)
    # The popups are the largest part of a page: never written twice.
    assert not any(cell.has_attr('data-popup') for cell in cells)
    for cell in cells:
        day, index = cell['data-popup-of'].split(':')
        source = html.select(f'#{day} .session-block')[int(index)]
        assert source.has_attr('data-popup')
        # What the filter and the agreement panel read comes along.
        for name in ('data-name', 'data-group', 'data-ai', 'data-ai-scope', 'data-room-scope'):
            assert cell[name] == source[name]
        assert cell.select_one('.session-name').text == source.select_one('.session-name').text
        assert not cell.select('.session-details, .session-chair')


def test_cells_stand_at_their_time_on_the_one_axis():
    schedule = build_schedule()
    html = page(schedule)
    grid = html.select_one('#all-days .schedule-grid')
    start, slot, first = (int(grid[f'data-{k}']) for k in ('start', 'slot', 'first-row'))
    assert start == min(time_to_minutes(d.timeline.start) for d in schedule.days)
    by_day = {d.day_name.lower(): d for d in schedule.days}
    for cell in grid.select('.session-block'):
        day, index = cell['data-popup-of'].split(':')
        day_cells = html.select(f'#{day} .session-block')
        session = [s for s in by_day[day].sessions if s.duration_minutes > 0][int(index)]
        assert day_cells[int(index)].select_one('.session-name').text == session.name
        if not session.part:
            assert rows(cell) == (
                (time_to_minutes(session.start_time) - start) // slot + first,
                (time_to_minutes(session.end_time) - start) // slot + first)


def test_each_day_keeps_its_own_columns():
    html = page()
    grid = html.select_one('#all-days .schedule-grid')
    days = grid.select('.week-day')
    spans = [columns(day) for day in days]
    # A gutter between two days, none after the last.
    assert [end + 1 for _, end in spans[:-1]] == [start for start, _ in spans[1:]]
    assert len(grid.select('.week-gutter')) == len(days) - 1
    for day, (start, end) in zip(days, spans):
        cells = [c for c in grid.select('.session-block') if c['data-popup-of'].startswith(day['data-day'] + ':')]
        assert all(start <= columns(c)[0] and columns(c)[1] <= end for c in cells)


def test_a_session_past_its_days_timeline_is_on_the_axis():
    # RAN1's timeline ends at 19:45 and a Tuesday can run until 21:00.
    schedule = build_schedule()
    day = schedule.days[0]
    day.timeline = Timeline(start=day.timeline.start, end=day.timeline.end, slot_minutes=5, label_minutes=30)
    session = day.sessions[-1]
    late = time_to_minutes(max(d.timeline.end for d in schedule.days)) + 60
    session.end_time = f'{late // 60:02d}:{late % 60:02d}'
    html = page(schedule)
    grid = html.select_one('#all-days .schedule-grid')
    assert int(grid['data-end']) == late


def test_a_time_name_is_drawn_once_in_the_view_it_belongs_to():
    grid = page().select_one('#all-days .schedule-grid')
    names = [label['style'].split('view-transition-name:')[1].split(';')[0] for label in grid.select('.time-label')]
    assert names and len(set(names)) == len(names)


def test_each_day_name_opens_that_day():
    html = page()
    tabs = [t['data-day'] for t in html.select('.tab')][:-1]
    assert [d['data-day'] for d in html.select('.week-day')] == tabs


def test_a_room_box_stands_for_a_room_across_the_days():
    # Rooms without an id (RAN1) are their names, without the hours and case.
    schedule = build_schedule()
    schedule.days[0].rooms = [RoomInfo(name='Breakout: Madrid 1-2'), RoomInfo(name='Offline Room')]
    schedule.days[1].rooms = [RoomInfo(name='Breakout: Madrid 1-2 (08:00–12:30)'), RoomInfo(name='Offline room'),
                              RoomInfo(name='Neptuno')]
    assert _room_label('Breakout: Madrid 1-2 (08:00–12:30)') == 'Breakout: Madrid 1-2'
    rooms = _week_rooms(schedule.days[:2])
    assert [r.name for r in rooms] == ['Breakout: Madrid 1-2', 'Offline Room', 'Neptuno']
    assert [r.key for r in rooms] == ['breakout: madrid 1-2', 'offline room', 'neptuno']


def test_a_room_renamed_by_the_day_is_one_box():
    # RAN4 #120bis: Shan's column is BR1 on Monday, RRM_Demod_OTA from Tuesday, "shan" all week.
    monday = [RoomInfo(id='yang', name='RAN4 Main (Yang Session)'),
              RoomInfo(id='shan', name='RAN4 Breakout 1(BR1) (Shan Session)'),
              RoomInfo(id='offline-room', name='Offline Room')]
    later = [RoomInfo(id='yang', name='RAN4 Main (Yang Session)'),
             RoomInfo(id='shan', name='RAN4 RRM_Demod_OTA (Shan Session)'),
             RoomInfo(id='offline-room', name='Offline room')]
    rooms = _week_rooms([SimpleNamespace(rooms=monday)] + [SimpleNamespace(rooms=later)] * 4)
    assert [r.key for r in rooms] == ['yang', 'shan', 'offline-room']
    # The box shows the name most days use; its title gives the others.
    assert rooms[1].names == ['RAN4 RRM_Demod_OTA (Shan Session)', 'RAN4 Breakout 1(BR1) (Shan Session)']
    assert rooms[2].names == ['Offline Room']

    schedule = build_schedule()
    schedule.days[0].rooms, schedule.days[1].rooms = monday, later
    for day in schedule.days:
        for session in day.sessions:
            session.room_ids, session.room_col_start, session.room_col_end = [], 2, 3
    html = page(schedule)
    boxes = html.select('.room-picks input')
    assert [b['data-key'] for b in boxes] == ['yang', 'shan', 'offline-room']
    # Two days, one name each: the first day's name, the other in the title.
    assert boxes[1].find_next('span').text == 'RAN4 Breakout 1(BR1) (Shan Session)'
    assert boxes[1].parent['title'] == 'RAN4 Breakout 1(BR1) (Shan Session)\nRAN4 RRM_Demod_OTA (Shan Session)'
    # Both days' Shan columns answer to the one box.
    assert len(html.select('#all-days .room-header.wr-1')) == 2


def test_a_meeting_of_one_room_has_no_room_boxes():
    schedule = build_schedule()
    for day in schedule.days:
        day.rooms = [RoomInfo(name='Main')]
        for session in day.sessions:
            session.room_ids, session.room_col_start, session.room_col_end = [], 2, 3
    html = page(schedule)
    assert html.select('.tab-all') and not html.select('.room-picks')


def test_room_boxes_follow_the_all_tab_and_mark_the_grid():
    html = page()
    boxes = html.select_one('.room-picks')
    assert boxes.find_previous_sibling(class_='tabs') and boxes.find_next_sibling(class_='now-toggle')
    data = json.loads(boxes['data-week'])
    names = [b['data-key'] for b in boxes.select('input')]
    grid = html.select_one('#all-days .schedule-grid')
    days = grid.select('.week-day')
    assert len(data['days']) == len(days)
    for i, (day, rooms) in enumerate(zip(days, data['days'])):
        start, end = columns(day)
        assert end - start == len(rooms) and f'wd-{i}' in day['class']
    # A cell shows in any room it spans; a header in its own.
    for cell in grid.select('.week-cell, .room-header:not(.time-col)'):
        marks = [c for c in cell['class'] if c.startswith('wr-')]
        assert marks and all(int(m[3:]) < len(names) for m in marks)


def run_picks(hidden=(), uncheck=(), stored=True):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required to execute browser logic')
    return json.loads(subprocess.run(
        [node, str(Path(__file__).parent / 'fixtures/room_picks_browser.cjs')],
        input=json.dumps({
            'script': _render_template('room-picks.js', {'STATE_ID_JSON': json.dumps('ran1:M')}),
            'saved': json.dumps(list(hidden)) if stored else None, 'uncheck': list(uncheck),
            # Two rooms on Monday; on Tuesday the second and a third.
            'keys': ['A', 'B', 'C'], 'week': {'days': [[0, 1], [1, 2]], 'room': 52, 'gutter': 6, 'axis': 36},
        }), text=True, capture_output=True, check=True).stdout)


def test_every_room_needs_no_rule():
    state = run_picks()
    # B is a column on both days: four room columns and one gutter.
    assert state['rule'] == '' and state['checked'] == [True, True, True] and state['width'] == '250'


def test_a_saved_choice_draws_before_the_grid_with_its_width():
    state = run_picks(hidden=['B'])
    assert state['checked'] == [True, False, True]
    assert 'grid-template-columns: var(--axis-col-width) minmax(0, 1fr) 0px 6px 0px minmax(0, 1fr) !important' in state['rule']
    # The zoom is fitted to this width: two rooms and the gutter between their days.
    assert '--grid-min-width: 146px' in state['rule'] and state['width'] == '146'
    assert ':not(.wr-0,.wr-2) { display: none; }' in state['rule']


def test_a_day_without_a_chosen_room_leaves_with_its_gutter():
    state = run_picks(hidden=['B', 'C'])
    assert 'minmax(0, 1fr) 0px 0px 0px 0px' in state['rule']
    assert ':is(.wg-0,.wd-1) { display: none; }' in state['rule'] and state['width'] == '88'


def test_the_last_room_cannot_be_unticked():
    assert run_picks(hidden=['B', 'C'])['disabled'] == [True, False, False]


def test_rooms_gone_from_this_meeting_show_every_room():
    assert run_picks(hidden=['A', 'B', 'C'])['checked'] == [True, True, True]


def test_unticking_saves_the_room_and_tells_the_page():
    state = run_picks(uncheck=['C'])
    assert state['stored'] == ['C'] and state['events'] == ['room-picks-change']
    assert state['width'] == '198'  # Three room columns left, and the gutter.
