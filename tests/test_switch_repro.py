"""Open the meeting-switch reproduction (tests/fixtures/switch_repro/switch-repro.html)
in the bundled Chromium, hold the switch half-way and read the page's own measurements.

The file stands alone: downloaded and opened in a phone browser, "Switch (same
page)" runs the switch as a same-document view transition, whose snapshots are
sized by the same code as the cross-document switch of the real pages. Served
over HTTP, "Switch (new page)" runs the real cross-document one. Two probe
boxes halve in height on every switch: a browser that stretches old snapshots
to their box (Samsung Internet squashes the header and the title this way)
squashes the left one; the right one carries the site's fix. The read-out on
the page gives the same verdict from the pseudo-elements. Chromium here keeps
the captured size, which these tests pin.
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
        pytest.skip('Node.js with Playwright (and its Chromium) is required to open the page')
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=str(REPRO)))
    server.log_message = lambda *a: None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_port}/switch-repro.html'
    server.shutdown()


def hold(served, mode, variant, at=0.5, shot=''):
    result = subprocess.run(['node', str(REPRO / 'run.cjs'), served, mode, variant, str(at), shot],
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def px(value):
    return float(value.replace('px', ''))


@pytest.mark.parametrize('mode', ['same', 'cross'])
def test_the_switch_runs_and_the_probe_keeps_its_captured_size(served, mode, tmp_path):
    out = hold(served, mode, 'move', shot=str(tmp_path / f'{mode}.png'))
    assert out['state'] == 'paused' and out['mode'] == f'{mode}-document'
    assert out['verdict'].startswith('old snapshots keep their captured size')
    old, group = out['boxes']['::view-transition-old(probe-default)'], out['boxes']['::view-transition-group(probe-default)']
    assert px(old['height']) == out['captured']['probe'] == 92  # 90px tall plus its border
    assert 47 < px(group['height']) < 92  # held half-way between the two heights


def test_the_cross_document_switch_moves_the_frame_the_cells_and_the_axis(served):
    out = hold(served, 'cross', 'move')
    for name in ('grid-cells', 'day-tabs', 'time-540'):
        assert px(out['boxes'][f'::view-transition-group({name})']['width']) > 0, name
    old = out['boxes']['::view-transition-old(root)']
    assert (px(old['width']), px(old['height'])) == tuple(out['captured']['root'])


def test_the_fix_never_scales_the_page_or_the_cells(served):
    out = hold(served, 'cross', 'fix')
    for name in ('::view-transition-old(root)', '::view-transition-new(root)',
                 '::view-transition-old(grid-cells)', '::view-transition-new(grid-cells)'):
        assert out['boxes'][name]['objectFit'] == 'none', name
    # ... and does not touch what moves: the time axis still scales with its box.
    assert out['boxes']['::view-transition-old(time-540)']['objectFit'] == 'fill'
    assert out['boxes']['::view-transition-old(probe-fixed)']['objectFit'] == 'none'


def test_plain_leaves_only_the_root_and_the_probes(served):
    out = hold(served, 'cross', 'plain')
    assert px(out['boxes']['::view-transition-group(root)']['width']) > 0
    assert px(out['boxes']['::view-transition-group(probe-default)']['width']) > 0
    assert out['boxes']['::view-transition-group(grid-cells)']['width'] == 'auto'
