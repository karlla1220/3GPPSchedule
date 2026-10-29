"""The Help button and its tour: markup and steps in the header, the walk in help-guide.js."""
import json
from pathlib import Path
import re
import shutil
import subprocess

from bs4 import BeautifulSoup
import pytest

from shared.page import HELP_STEPS, render_header
from schedule_fixture import build_schedule

SCRIPT = Path(__file__).parent.parent / 'templates/help-guide.js'
TITLES = {step['target']: step['title'] for step in HELP_STEPS}


def header():
    return BeautifulSoup(render_header(build_schedule()), 'html.parser')


def guide_script():
    return header().select_one('#help-guide').find_next_sibling('script').string


def test_the_help_button_comes_first_and_opens_the_tour():
    page = header()
    button = page.header.find(True)
    # The glyph alone; the name is for screen readers, the title for a pointer resting on it.
    assert button['id'] == 'help-button' and button.get_text() == '?' and button.span['aria-hidden'] == 'true'
    assert button['aria-label'] == 'Help' and button['title'] == 'How to use this page'
    assert button['aria-controls'] == 'help-guide' and button['aria-haspopup'] == 'dialog'
    guide = page.select_one('dialog#help-guide')
    assert guide['aria-labelledby'] == 'help-title' and guide['aria-describedby'] == 'help-text'
    assert guide.select_one('.help-close')['aria-label'] == 'Close the tour'
    stage = guide.select_one('.help-stage')
    assert stage['aria-hidden'] == 'true' and stage.has_attr('inert')


def test_the_steps_go_into_the_script_as_written():
    steps = re.search(r'const STEPS = (\[.*?\]);\n', guide_script(), re.S).group(1)
    assert json.loads(steps) == list(HELP_STEPS)
    assert '{{' not in guide_script()


def test_every_scene_a_step_names_is_one_the_script_plays():
    source = SCRIPT.read_text(encoding='utf-8')
    body = source[source.index('const scenes = {'):source.index('\n    };\n', source.index('const scenes = {'))]
    played = re.findall(r'^ {8}(\w+): \{', body, re.M)
    assert sorted({step['scene'] for step in HELP_STEPS if 'scene' in step}) == sorted(played)
    assert all(step['title'] and step['text'] and step['target'] for step in HELP_STEPS)


def run_tour(steps, *, targets, dialog=True, touch=False, grid=False, scroll_y=300, width=1000):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required to execute browser logic')
    result = subprocess.run([node, str(Path(__file__).parent / 'fixtures/help_guide_browser.cjs')],
                            input=json.dumps({'script': guide_script(), 'steps': steps, 'targets': targets,
                                              'dialog': dialog, 'touch': touch, 'grid': grid,
                                              'scrollY': scroll_y, 'width': width}),
                            text=True, capture_output=True, check=True, encoding='utf-8')
    return json.loads(result.stdout)


RAN2 = {'#wg-switch summary': 'shown', '.schedule-toolbar': 'shown', '.filter-toggle': 'shown',
        '.supplements': 'shown', '#help-button': 'shown'}


def test_without_dialogs_there_is_no_button():
    state, = run_tour(['nothing'], targets=RAN2, dialog=False)['states']
    assert state['buttonHidden']


def test_the_tour_has_a_step_for_each_part_the_page_shows_in_page_order():
    # No agreement panel or topics on this page, an empty off-offline list, no grid.
    targets = RAN2 | {'.email-informal': 'hidden'}
    *_, = states = run_tour(['start', 'next', 'next', 'next', 'next'], targets=targets)['states']
    assert [s['title'] for s in states] == [TITLES[t] for t in (
        '#wg-switch summary', '.schedule-toolbar', '.filter-toggle', '.supplements', '#help-button')]
    assert [s['count'] for s in states] == ['1 / 5', '2 / 5', '3 / 5', '4 / 5', '5 / 5']
    assert states[0]['backDisabled'] and not states[1]['backDisabled']
    assert states[-1]['next'] == 'Done' and states[0]['next'] == 'Next'


def test_back_and_the_arrow_keys_move_between_steps():
    states = run_tour(['start', 'right', 'right', 'back'], targets=RAN2)['states']
    assert [s['count'] for s in states] == ['1 / 5', '2 / 5', '3 / 5', '2 / 5']


def test_pinch_is_a_step_only_on_touch_screens_with_a_grid():
    assert run_tour(['start'], targets=RAN2, grid=True)['states'][0]['count'] == '1 / 5'
    titles = [s['title'] for s in run_tour(['start', 'next', 'next', 'next'], targets=RAN2, grid=True, touch=True)['states']]
    assert titles[3] == TITLES['grid']
    assert run_tour(['start'], targets=RAN2, touch=True)['states'][0]['count'] == '1 / 5'


def test_every_way_out_ends_the_tour_where_the_visitor_was():
    for way in (['next'] * 5, ['close'], ['escape'], ['outside']):
        result = run_tour(['start', *way], targets=RAN2, scroll_y=300)
        last = result['states'][-1]
        assert not last['open'] and last['focused'] and last['scrollY'] == 300
        assert result['scrolls'][-1] == {'left': 0, 'top': 300, 'behavior': 'auto'}


def test_the_tour_never_touches_storage():
    assert run_tour(['start', 'next', 'close', 'start', 'close'], targets=RAN2)['touched'] == []


def test_a_narrow_target_covered_above_and_below_keeps_its_side_free():
    # The Filter tab on a short phone: the card narrows to the other side of the screen.
    states = run_tour(['start', 'next', 'next'], targets=RAN2 | {'.filter-toggle': 'fixed'}, width=400)['states']
    assert states[2]['title'] == TITLES['.filter-toggle']
    assert states[2]['card'] == {'top': False, 'side': True, 'left': '8px', 'width': '274px'}
    # The next step lays the card out afresh.
    states = run_tour(['start', 'next', 'next', 'next'], targets=RAN2 | {'.filter-toggle': 'fixed'}, width=400)['states']
    assert states[3]['card'] == {'top': False, 'side': False, 'left': '', 'width': ''}
