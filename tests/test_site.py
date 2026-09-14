"""Contracts between independent WG pipelines, the site builder and renderer."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from bs4 import BeautifulSoup
import pytest

import build
from shared.navigation import meeting_status
from shared.renderer import generate_html
from shared.schedule import Timeline, load_schedule, save_schedule
from working_groups.ran1.models import ran1_timeline
from working_groups.ran_plenary.pipeline import build_schedule as plenary_schedule


def options(tmp_path, **overrides):
    config = tmp_path / 'site.json'
    config.write_text(json.dumps({
        'default_wg': 'ran1',
        'working_groups': [{'id': 'ran1', 'label': 'RAN1'}, {'id': 'ran-plenary', 'label': 'RAN Plenary'}],
    }))
    return SimpleNamespace(site_config=config, output_dir=tmp_path / 'docs', wg='all',
                           render_only=False, **overrides)


def seed_ran1(opts):
    schedule = replace(plenary_schedule(), wg_id='ran1', meeting_id='ran1-test', meeting_name='RAN1 test', is_demo=False)
    schedule.days[0].timeline = ran1_timeline()
    save_schedule(schedule, opts.output_dir / 'ran1' / 'schedule.json')
    return schedule


def test_plenary_pipeline_needs_neither_ran1_nor_llm_credentials():
    result = subprocess.run([sys.executable, '-c', '''
import sys
from working_groups.registry import build_schedule
schedule = build_schedule('ran-plenary', None)
assert schedule.is_demo and len(schedule.days) == 2
assert not any(name.startswith(('working_groups.ran1', 'google.genai', 'docx')) for name in sys.modules)
'''], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_plenary_uses_daily_rooms_and_timeline_without_ran1_breaks():
    html = BeautifulSoup(generate_html(plenary_schedule()), 'html.parser')
    monday = html.select_one('#monday .schedule-grid')
    tuesday = html.select_one('#tuesday .schedule-grid')
    assert monday['data-start'] == '540'
    assert monday['data-slot'] == '15'
    assert '--time-rows:32' in monday['style']
    assert tuesday['data-start'] == '570'
    assert '--time-rows:22' in tuesday['style']
    assert len(monday.select('.room-header')) == 3  # time + 2 rooms
    assert len(tuesday.select('.room-header')) == 2
    reports = monday.select_one('[data-name="Working group reports"]')
    assert 'grid-row:5/8;' in reports['style']
    discussion = monday.select_one('[data-name="Rapporteur discussion"]')
    assert 'grid-column:3/4;' in discussion['style']
    assert [b.get_text() for b in monday.select('.break-bar')] == ['Coffee break', 'Lunch']
    assert html.select_one('.demo-notice') is not None
    assert [t.get_text() for t in monday.select('.time-label')][:2] == ['09:00', '10:00']


def test_schedule_roundtrip_preserves_room_identity_and_daily_time_axes(tmp_path):
    schedule = plenary_schedule()
    save_schedule(schedule, tmp_path / 'schedule.json')
    assert load_schedule(tmp_path / 'schedule.json') == schedule


def test_building_one_wg_preserves_other_snapshot_and_connects_real_pages(tmp_path):
    opts = options(tmp_path)
    ran1 = seed_ran1(opts)
    before = (opts.output_dir / 'ran1/schedule.json').read_bytes()
    opts.wg = 'ran-plenary'
    build.build_site(opts)
    assert (opts.output_dir / 'ran1/schedule.json').read_bytes() == before
    assert './ran1/' in (opts.output_dir / 'index.html').read_text()
    for wg, sibling in [('ran1', 'ran-plenary'), ('ran-plenary', 'ran1')]:
        html = BeautifulSoup((opts.output_dir / wg / 'index.html').read_text(), 'html.parser')
        assert html.select_one('[aria-current="page"]')['href'] == f'../{wg}/'
        assert html.select_one('.wg-link')['href'] == f'../{sibling}/'
        assert html.select_one('.wg-current h1') is not None
    assert load_schedule(opts.output_dir / 'ran1/schedule.json') == ran1


def test_render_only_and_configurable_default_never_call_pipeline(tmp_path, monkeypatch):
    opts = options(tmp_path)
    seed_ran1(opts)
    save_schedule(plenary_schedule(), opts.output_dir / 'ran-plenary/schedule.json')
    cfg = json.loads(opts.site_config.read_text())
    cfg['default_wg'] = 'ran-plenary'
    opts.site_config.write_text(json.dumps(cfg))
    opts.render_only = True
    monkeypatch.setattr(build, 'build_schedule', lambda *_: pytest.fail('Render-only invoked a parser'))
    build.build_site(opts)
    assert './ran-plenary/' in (opts.output_dir / 'index.html').read_text()


def test_failed_wg_does_not_replace_last_successful_schedule(tmp_path, monkeypatch):
    opts = options(tmp_path)
    seed_ran1(opts)
    before = (opts.output_dir / 'ran1/schedule.json').read_bytes()
    def run(wg, options):
        if wg == 'ran1':
            raise ValueError('bad source document')
        return plenary_schedule()
    monkeypatch.setattr(build, 'build_schedule', run)
    with pytest.raises(RuntimeError, match='ran1'):
        build.build_site(opts)
    assert (opts.output_dir / 'ran1/schedule.json').read_bytes() == before
    assert (opts.output_dir / 'ran-plenary/index.html').exists()


def test_navigation_uses_venue_dates_and_keeps_selected_ended_meeting():
    ran1 = replace(plenary_schedule(), wg_id='ran1', meeting_name='RAN1', is_demo=False,
                   starts_on='2026-09-15', ends_on='2026-09-15', timezone='Asia/Seoul')
    assert meeting_status(ran1, datetime(2026, 9, 14, 16, tzinfo=timezone.utc)) == 'In progress'
    assert meeting_status(ran1, datetime(2026, 9, 15, 16, tzinfo=timezone.utc)) == 'Ended'
    ended = replace(ran1, starts_on='2000-01-01', ends_on='2000-01-02')
    demo = plenary_schedule()
    groups = [{'id': 'ran1'}, {'id': 'ran-plenary'}]
    html = BeautifulSoup(generate_html(ended, schedules={'ran1': ended, 'ran-plenary': demo}, groups=groups), 'html.parser')
    assert html.select_one('[aria-current]')['href'] == '../ran1/'
    html = BeautifulSoup(generate_html(demo, schedules={'ran1': ended, 'ran-plenary': demo}, groups=groups), 'html.parser')
    assert html.select_one('.wg-link') is None


@pytest.mark.parametrize('kwargs', [{'slot_minutes': 0}, {'label_minutes': -1}, {'start': '18:00'}, {'slot_minutes': 7}, {'slot_minutes': 15, 'label_minutes': 20}])
def test_invalid_time_axes_fail_early(kwargs):
    with pytest.raises(ValueError):
        Timeline(**kwargs)


def test_unknown_room_reference_is_not_silently_rendered_in_first_room():
    schedule = plenary_schedule()
    schedule.days[0].sessions[0].room_ids = ['missing-room']
    with pytest.raises(KeyError):
        generate_html(schedule)


def test_browser_state_is_scoped_to_wg_and_meeting():
    html = generate_html(plenary_schedule())
    assert '3gpp_schedule_state:' in html
    assert 'ran-plenary:ran-plenary-demo' in html


def test_corrupt_snapshot_can_be_rebuilt_without_blocking_other_wgs(tmp_path):
    opts = options(tmp_path)
    seed_ran1(opts)
    path = opts.output_dir / 'ran-plenary/schedule.json'
    path.parent.mkdir()
    path.write_text('{broken')
    opts.wg = 'ran-plenary'
    build.build_site(opts)
    assert load_schedule(path) == plenary_schedule()


def test_ran1_caches_and_sources_have_their_own_namespace():
    from working_groups.ran1 import downloader, slot_state, session_parser, config
    assert downloader.DOWNLOADS_DIR == Path('downloads/ran1')
    assert downloader.REF_IN_MANUAL_DIR == Path('ref_in_manual/ran1')
    assert slot_state.SLOT_STATE_DIR == Path('docs/ran1/slot_state')
    assert session_parser.CACHE_DIR == Path('.cache/ran1')
    assert config.DEFAULT_CONFIG_PATH.parent.name == 'ran1'
