"""Run switch-arrival.js: only an arrival from another WG's page holds its first frame for the grid."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from shared.renderer import _load_template

EXPECT = '<link rel="expect" href="#grid-end" blocking="render">'
SITE = 'https://karlla1220.github.io/3GPPSchedule/'


def arrive(location, referrer='', nav_type='navigate'):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required to execute browser logic')
    result = subprocess.run([node, str(Path(__file__).parent / 'fixtures/switch_arrival_browser.cjs')],
                            input=json.dumps({'script': _load_template('switch-arrival.js'), 'location': location,
                                              'referrer': referrer, 'navType': nav_type}),
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('location, referrer, nav_type', [
    (SITE + 'ran1/', SITE + 'ran2/', 'navigate'),             # The meeting switch.
    (SITE + 'ran1/', SITE + 'ran-plenary/', 'back_forward'),  # Back between WG pages.
    (SITE + 'ran1/index.html', SITE + 'ran2/', 'navigate'),
])
def test_arriving_from_another_wg_waits_for_the_grid(location, referrer, nav_type):
    assert arrive(location, referrer, nav_type) == [EXPECT]


@pytest.mark.parametrize('location, referrer, nav_type', [
    (SITE + 'ran1/', SITE, 'navigate'),                      # index.html sends every visitor in.
    (SITE + 'ran1/', SITE + 'index.html', 'navigate'),
    (SITE + 'ran1/', SITE + 'ran2/', 'reload'),               # Auto refresh keeps the old referrer.
    (SITE + 'ran1/', SITE + 'ran1/', 'navigate'),
    (SITE + 'ran1/', 'https://example.com/ran2/', 'navigate'),
    (SITE + 'ran1/', '', 'navigate'),
])
def test_any_other_arrival_draws_at_once(location, referrer, nav_type):
    assert arrive(location, referrer, nav_type) == []
