"""The specDuck embed handshake at the end of schedule.js, run in Node with a fake window."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).parent.parent / 'templates/schedule.js').read_text(encoding='utf-8')
EMBED = SCRIPT[SCRIPT.index('// specDuck shows this site'):]

HARNESS = r"""
const vm = require('vm');
const [code, steps, framed, reduce] = JSON.parse(process.argv[1]);
const sent = [], styles = {}, listeners = {};
const parent = { postMessage: (msg, origin) => sent.push([msg, origin]) };
const window = {
  scrollY: 0,
  addEventListener: (type, fn) => { listeners[type] = fn; },
  matchMedia: () => ({ matches: reduce }),
};
window.parent = framed ? parent : window;
const context = {
  window, JSON, Number, isFinite, Math,
  sessionStorage: { getItem: () => null, setItem: () => {} },
  document: { documentElement: { style: { setProperty: (k, v) => { styles[k] = v; } } } },
};
vm.runInNewContext(code, context);
for (const step of steps) {
  if (step.insets) listeners.message && listeners.message({ source: step.from === 'other' ? {} : parent,
    origin: 'https://duck.example', data: { type: 'specduck:insets', top: 48, bottom: 56 } });
  if ('y' in step) { window.scrollY = step.y; listeners.scroll && listeners.scroll(); }
}
console.log(JSON.stringify({ sent, styles }));
"""


def run(steps, framed=True, reduce=False):
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed')
    out = subprocess.run([node, '-e', HARNESS, json.dumps([EMBED, steps, framed, reduce])],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_insets_set_the_variables_and_scrolling_reports_only_changes():
    result = run([{'insets': True}, {'y': 100}, {'y': 200}, {'y': 300}, {'y': 250}, {'y': 200}, {'y': 10}])
    assert result['styles'] == {'--embed-top': '48px', '--embed-bottom': '56px'}
    assert [m[0]['hidden'] for m in result['sent']] == [True, False]
    assert {m[1] for m in result['sent']} == {'https://duck.example'}


def test_small_moves_do_not_flip_the_state():
    result = run([{'insets': True}, {'y': 100}, {'y': 95}, {'y': 92}])
    assert [m[0]['hidden'] for m in result['sent']] == [True]


def test_nothing_is_sent_before_insets_or_from_another_window():
    assert run([{'y': 100}, {'insets': True, 'from': 'other'}, {'y': 300}]) == {'sent': [], 'styles': {}}


def test_standalone_and_reduced_motion_send_nothing():
    assert run([{'insets': True}, {'y': 300}], framed=False)['sent'] == []
    assert run([{'insets': True}, {'y': 300}], reduce=True)['sent'] == []
