"""Run the generated JavaScript to check where the filter is kept and restored from."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from shared.renderer import _generate_js

STATE_ID = 'ran1:RAN1#126'
KEY = '3gpp_schedule_filter:' + STATE_ID


def run_page(*, url_hash='', stored=None, steps=(), storage_error=False, state_id=STATE_ID):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required to execute browser logic')
    script = _generate_js('Europe/Amsterdam', auto_refresh_minutes=0, state_id=state_id)
    result = subprocess.run([node, str(Path(__file__).parent / 'fixtures/filter_state_browser.cjs')],
                            input=json.dumps({'script': script, 'hash': url_hash, 'stored': stored,
                                              'steps': steps, 'storageError': storage_error,
                                              'ais': ['10.1', '10.2']}),
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def test_link_without_a_filter_brings_back_the_saved_one():
    state, = run_page(stored={KEY: '#filter=a:10.1'})
    assert state['checked'] == ['10.1']
    assert state['hash'] == '#filter=a:10.1'  # Back in the URL, so it can be shared.


def test_filter_in_the_url_wins_and_leaves_the_saved_one_alone():
    state, = run_page(url_hash='#filter=a:10.2', stored={KEY: '#filter=a:10.1'})
    assert state['checked'] == ['10.2']
    assert state['stored'] == {KEY: '#filter=a:10.1'}


def test_changes_are_saved_and_clear_forgets_them():
    empty, picked, cleared = run_page(steps=[{'toggle': '10.2'}, {'clear': True}])
    assert empty['stored'] == {} and not empty['hash']
    assert picked['stored'] == {KEY: '#filter=a:10.2'} == {KEY: picked['hash']}
    assert cleared['stored'] == {} and not cleared['hash'] and not cleared['checked']


def test_filter_is_kept_per_wg_and_meeting():
    other = {'3gpp_schedule_filter:ran2:RAN2#135': '#filter=a:10.1',
             '3gpp_schedule_filter:ran1:RAN1#125': '#filter=a:10.2'}
    state, picked = run_page(stored=other, steps=[{'toggle': '10.2'}])
    assert not state['checked'] and not state['hash']
    assert picked['stored'] == {**other, KEY: '#filter=a:10.2'}


def test_storage_unavailable_keeps_the_filter_in_the_url():
    loaded, picked = run_page(url_hash='#filter=a:10.1', storage_error=True, steps=[{'toggle': '10.2'}])
    assert loaded['checked'] == ['10.1']
    assert picked['hash'] == '#filter=a:10.1,a:10.2'
