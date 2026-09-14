"""Run the generated JavaScript across actual meeting instants, not weekdays."""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess

from bs4 import BeautifulSoup
import pytest

from shared.renderer import _generate_js, generate_html
from shared.schedule import load_schedule, save_schedule
from working_groups.ran_plenary.pipeline import build_schedule

START = '2026-08-24T09:00:00+02:00'
END = '2026-08-28T17:30:00+02:00'


def run_page(*, now, starts_at=START, ends_at=END, stored=None, steps=(), storage_error=False, script=None):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required to execute browser logic')
    source = script if script is not None else _generate_js(
        'Europe/Amsterdam', auto_refresh_minutes=0, starts_at=starts_at, ends_at=ends_at)
    result = subprocess.run([node, str(Path(__file__).parent / 'fixtures/now_line_browser.cjs')],
                            input=json.dumps({'script': source, 'now': now, 'stored': stored,
                                              'steps': steps, 'storageError': storage_error}),
                            text=True, capture_output=True, check=True,
                            env={**os.environ, 'TZ': 'America/Los_Angeles'})
    return json.loads(result.stdout)


@pytest.mark.parametrize('now, active', [
    ('2026-08-24T06:59:59.999Z', False),
    ('2026-08-24T07:00:00Z', True),
    ('2026-08-28T15:30:00Z', True),
    ('2026-08-28T15:30:00.001Z', False),
    ('2026-08-31T07:00:00Z', False),  # Same weekday/time in the following week.
])
def test_now_uses_absolute_meeting_bounds_despite_browser_timezone(now, active):
    state, = run_page(now=now, stored='true')
    assert state['pressed'] == str(active).lower()
    assert state['disabled'] is not active
    assert state['lines'] == int(active)


def test_page_crosses_start_and_end_between_minute_ticks():
    before, start, end = run_page(now='2026-08-24T06:59:59Z', steps=[
        {'now': '2026-08-24T07:00:00Z', 'timerOnly': True},
        {'now': '2026-08-28T15:30:00.001Z', 'timerOnly': True},
    ])
    assert [s['pressed'] for s in [before, start, end]] == ['false', 'true', 'false']
    assert [s['lines'] for s in [before, start, end]] == [0, 1, 0]
    assert before['stored'] is None and end['stored'] is None


def test_manual_preference_only_applies_inside_the_meeting():
    off, on, ended, clicked = run_page(now='2026-08-24T08:00:00Z', stored='false', steps=[
        {'click': True}, {'now': '2026-08-28T16:00:00Z', 'focus': True}, {'click': True},
    ])
    assert off['pressed'] == 'false' and not off['disabled']
    assert on['pressed'] == 'true' and on['stored'] == 'true'
    assert ended['pressed'] == clicked['pressed'] == 'false'
    assert clicked['stored'] == 'true'  # Automatic OFF does not overwrite preference.


@pytest.mark.parametrize('start,end', [
    (None, None), (START, None), ('bad', END),
    ('2026-08-24T09:00:00', END), (END, START),
])
def test_unknown_or_invalid_bounds_keep_now_off(start, end):
    state, = run_page(now='2026-08-24T08:00:00Z', starts_at=start, ends_at=end, stored='true')
    assert state['pressed'] == 'false' and state['disabled'] and not state['lines']


def test_storage_unavailable_still_enables_now_during_meeting():
    state, = run_page(now='2026-08-24T08:00:00Z', storage_error=True)
    assert state['pressed'] == 'true' and state['lines'] == 1


def test_snapshot_and_html_preserve_instants_but_demo_never_enables_now(tmp_path):
    schedule = replace(build_schedule(), starts_at=START, ends_at=END)
    path = tmp_path / 'schedule.json'
    save_schedule(schedule, path)
    restored = load_schedule(path)
    assert restored.starts_at == START and restored.ends_at == END
    html = BeautifulSoup(generate_html(restored), 'html.parser')
    script = html.find_all('script')[-1].string
    # Demo remains OFF even if it is accidentally assigned live dates.
    state, = run_page(now='2026-08-24T08:00:00Z', script=script)
    assert state['pressed'] == 'false'
    real = BeautifulSoup(generate_html(replace(restored, is_demo=False)), 'html.parser')
    state, = run_page(now='2026-08-24T08:00:00Z', script=real.find_all('script')[-1].string)
    assert state['pressed'] == 'true'


def test_legacy_snapshots_without_instants_remain_readable(tmp_path):
    path = tmp_path / 'schedule.json'
    save_schedule(build_schedule(), path)
    data = json.loads(path.read_text())
    data.pop('starts_at'); data.pop('ends_at')
    path.write_text(json.dumps(data))
    schedule = load_schedule(path)
    assert schedule.starts_at is None and schedule.ends_at is None
