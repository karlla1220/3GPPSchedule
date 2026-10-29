"""The Help button and its guide: markup in the header, scenes in help-guide.js."""
import json
from pathlib import Path
import re
import shutil
import subprocess

from bs4 import BeautifulSoup
import pytest

from shared.page import HELP_SCENES, render_header
from schedule_fixture import build_schedule

SCRIPT = Path(__file__).parent.parent / 'templates/help-guide.js'
KEYS = [key for key, _, _ in HELP_SCENES]


def header():
    return BeautifulSoup(render_header(build_schedule()), 'html.parser')


def test_the_help_button_comes_first_and_opens_the_guide():
    page = header()
    button = page.header.find(True)
    assert button['id'] == 'help-button' and button.get_text() == '?Help'
    assert button['aria-controls'] == 'help-guide' and button['aria-haspopup'] == 'dialog'
    guide = page.select_one('dialog#help-guide')
    assert guide.select_one('#help-guide-title').get_text() == 'How to use this page'
    assert guide.select_one('.help-guide-close')['aria-label'] == 'Close'


def test_each_scene_has_its_words_and_a_stage_kept_from_readers_and_focus():
    scenes = header().select('.help-scene')
    assert [scene['data-scene'] for scene in scenes] == KEYS
    for scene, (_, heading, text) in zip(scenes, HELP_SCENES):
        assert scene.h3.get_text() == heading and scene.p.get_text() == text
        stage = scene.select_one('.help-stage')
        assert stage['aria-hidden'] == 'true' and stage.has_attr('inert') and not stage.contents


def test_the_script_plays_every_scene_the_page_names_and_no_other():
    source = SCRIPT.read_text(encoding='utf-8')
    body = source[source.index('const scenes = {'):source.index('\n    };\n', source.index('const scenes = {'))]
    assert re.findall(r'^ {8}(\w+): \{', body, re.M) == KEYS


def run_guide(steps, *, dialog=True, agreements=False):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required to execute browser logic')
    script = header().select_one('#help-guide').find_next_sibling('script').string
    result = subprocess.run([node, str(Path(__file__).parent / 'fixtures/help_guide_browser.cjs')],
                            input=json.dumps({'script': script, 'scenes': KEYS, 'steps': steps,
                                              'dialog': dialog, 'agreements': agreements}),
                            text=True, capture_output=True, check=True, encoding='utf-8')
    return json.loads(result.stdout)


def test_without_dialogs_there_is_no_button():
    state, = run_guide(['nothing'], dialog=False)['states']
    assert state['buttonHidden']


def test_a_page_without_a_grid_hides_the_scenes_it_cannot_play():
    opened, = run_guide(['open'], agreements=True)['states']
    assert opened['open'] and opened['scrollTop'] == 0
    assert opened['hidden'] == {key: True for key in KEYS}


def test_closing_clears_the_stages_and_returns_to_the_button():
    for way in ('close', 'backdrop'):
        *_, closed = run_guide(['open', way])['states']
        assert not closed['open'] and closed['focused'] and closed['stages'] == [''] * len(KEYS)
    *_, inside = run_guide(['open', 'inside'])['states']
    assert inside['open']


def test_the_guide_never_touches_storage():
    assert run_guide(['open', 'close', 'open'])['touched'] == []
