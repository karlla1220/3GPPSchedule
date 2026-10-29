"""Open the meeting-switch reproduction (tests/fixtures/switch_repro) in the
bundled Chromium, hold the switch mid-way and measure its snapshots.

The pages are a.html (five days, 08:30-18:00) and b.html (four days, 08:00-17:00)
with the switch's rules from schedule.css, picked by ?v=plain|move|fix. To look
at them in a phone browser, serve the folder over HTTP (file:// pages do not
transition) and open a.html?v=move&slow=1 on the phone:

    python3 -m http.server 8000 -d tests/fixtures/switch_repro

Samsung Internet squashes the header and the title vertically during the
cross-fade (Chrome for Android does not); ?v=fix carries the rule that keeps
the page's snapshot at its captured size. Chromium here never distorts it, so
these tests pin what a correct engine shows and that the fix changes nothing
visible in it.
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

REPRO = Path(__file__).parent / 'fixtures/switch_repro'


@pytest.fixture(scope='module')
def served():
    node = shutil.which('node')
    if not node or subprocess.run([node, '-e', "require('playwright')"], capture_output=True).returncode:
        pytest.skip('Node.js with Playwright (and its Chromium) is required to open the pages')
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=str(REPRO)))
    server.log_message = lambda *a: None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/'
    server.shutdown()


def hold(served, variant, times, tmp_path):
    result = subprocess.run(['node', str(REPRO / 'run.cjs'), served, variant, ','.join(map(str, times)), str(tmp_path)],
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def px(value):
    return float(value.replace('px', ''))


def test_the_pages_switch_with_a_view_transition(served, tmp_path):
    frame = hold(served, 'move', [100], tmp_path)['100']
    assert frame['state'] == 'paused' and frame['variant'] == 'move'
    # The frame, the cells and the time axis are in play, not only the root.
    assert px(frame['boxes']['::view-transition-group(grid-cells)']['width']) > 0
    assert px(frame['boxes']['::view-transition-group(time-540)']['width']) > 0


@pytest.mark.parametrize('variant', ['plain', 'move', 'fix'])
def test_the_old_page_keeps_its_size_while_it_fades(served, variant, tmp_path):
    # Samsung Internet draws the old page's snapshot squashed into the new
    # page's area. A correct engine keeps it at the size it was captured at.
    frame = hold(served, variant, [90], tmp_path)['90']
    old = frame['boxes']['::view-transition-old(root)']
    assert (px(old['width']), px(old['height'])) == (frame['before']['width'], frame['before']['height'])
    group = frame['boxes']['::view-transition-group(root)']
    assert (px(group['width']), px(group['height'])) == (frame['viewport']['width'], frame['viewport']['height'])


def test_the_fix_never_scales_the_page_or_the_cells(served, tmp_path):
    frame = hold(served, 'fix', [90], tmp_path)['90']
    for name in ('::view-transition-old(root)', '::view-transition-new(root)',
                 '::view-transition-old(grid-cells)', '::view-transition-new(grid-cells)'):
        assert frame['boxes'][name]['objectFit'] == 'none', name
    # ... and does not touch what moves: the time axis still scales with its box.
    assert frame['boxes']['::view-transition-old(time-540)']['objectFit'] == 'fill'


def test_plain_leaves_only_the_root(served, tmp_path):
    frame = hold(served, 'plain', [90], tmp_path)['90']
    assert px(frame['boxes']['::view-transition-group(root)']['width']) > 0
    assert frame['boxes']['::view-transition-group(grid-cells)']['width'] == 'auto'
