"""Contracts between independent WG pipelines, the site builder and renderer."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
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
from schedule_fixture import build_schedule as plenary_schedule


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


def test_plenary_lifecycle_import_is_isolated():
    result = subprocess.run([sys.executable, '-c', """
import sys
from working_groups.registry import get_working_group
get_working_group('ran-plenary')
assert not any(name.startswith(('working_groups.ran1', 'google.genai', 'docx')) for name in sys.modules)
"""], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.fixture(autouse=True)
def stub_network_pipeline(monkeypatch):
    monkeypatch.setattr(build, 'build_schedule', lambda wg, options: plenary_schedule())


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
    # A room without a place keeps the one-line header.
    assert not monday.select('.room-header.has-place, .room-place')
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
        assert html.select_one('.wg-switch-item:not([aria-current])')['href'] == f'../{sibling}/'
        assert html.select_one('.wg-switch summary h1') is not None
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


def test_navigation_uses_venue_dates_and_keeps_latest_ended_meeting():
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
    link = html.select_one('.wg-switch-item:not([aria-current])')
    assert link['href'] == '../ran1/'
    assert link.find_previous('p', class_='wg-switch-group').text == 'Ended'
    assert link.select_one('.wg-switch-dates').text == '1\u20132 Jan'


def _meeting(wg_id, name, starts_on, ends_on):
    return replace(plenary_schedule(), wg_id=wg_id, meeting_id=wg_id, meeting_name=name, is_demo=False,
                   starts_on=starts_on, ends_on=ends_on, timezone='Asia/Seoul')


def test_switch_menu_lists_active_meetings_first_in_3gpp_order():
    today = datetime.now(timezone.utc).date()
    on = (str(today - timedelta(days=1)), str(today + timedelta(days=1)))
    soon = (str(today + timedelta(days=20)), str(today + timedelta(days=24)))
    past = ('2000-01-30', '2000-02-02')
    schedules = {
        'sa2': _meeting('sa2', 'SA2#1', *soon), 'ran-plenary': _meeting('ran-plenary', 'RAN#1', *past),
        'ran2': _meeting('ran2', 'RAN2#1', *on), 'ran1': _meeting('ran1', 'RAN1#1', *on),
        'ct1': _meeting('ct1', 'CT1#1', *past), 'ran4': _meeting('ran4', 'RAN4#1', *soon),
    }
    groups = [{'id': wg} for wg in schedules]
    html = BeautifulSoup(generate_html(schedules['ran2'], schedules=schedules, groups=groups), 'html.parser')
    menu = html.select_one('.wg-switch-menu')
    assert [el.text for el in menu.select('.wg-switch-group, .wg-switch-item > span:first-child')] == [
        'In progress', 'RAN1#1', 'RAN2#1', 'Upcoming', 'RAN4#1', 'SA2#1', 'Ended', 'RAN#1', 'CT1#1']
    assert menu.select_one('[aria-current]')['href'] == '../ran2/'
    assert menu.select('.wg-switch-dates')[-1].text == '30 Jan \u2013 2 Feb'
    assert 'RAN1#1, RAN4#1, SA2#1, RAN#1, CT1#1' in html.select_one('#wg-switch-hint').text
    assert html.select_one('#wg-switch-hint').has_attr('hidden')


def test_single_meeting_has_a_plain_title():
    schedule = _meeting('ran1', 'RAN1#1', '2000-01-01', '2000-01-02')
    html = BeautifulSoup(generate_html(schedule, schedules={'ran1': schedule}, groups=[{'id': 'ran1'}]), 'html.parser')
    assert html.select_one('.wg-title h1').text.endswith('RAN1#1')
    assert html.select_one('.wg-switch') is None and html.select_one('#wg-switch-hint') is None


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


@pytest.fixture(autouse=True)
def no_live_portal_lookup(monkeypatch):
    monkeypatch.setattr(build, "fetch_site_meetings", lambda: None)


def test_default_prefers_matching_ongoing_meeting_with_stable_ties():
    from shared.navigation import select_default_wg
    cfg = {'default_wg': 'ran1', 'working_groups': [{'id': 'ran1'}, {'id': 'ran-plenary'}]}
    schedules = {
        'ran1': replace(plenary_schedule(), wg_id='ran1', meeting_id='ran1#126', is_demo=False),
        'ran-plenary': replace(plenary_schedule(), meeting_id='ran#113', is_demo=False),
    }
    seoul = '(GMT+09:00) Seoul'
    rows = [{'Id': 1, 'TBId': 379, 'Title': '3GPPRAN1#126', 'StartTimeZone': seoul,
             'StartDate': '2026-08-24 09:00:00', 'EndDate': '2026-08-28 17:30:00'},
            {'Id': 2, 'TBId': 373, 'Title': '3GPPRAN#113', 'StartTimeZone': seoul,
             'StartDate': '2026-09-14 09:00:00', 'EndDate': '2026-09-17 17:00:00'}]
    now = datetime(2026, 9, 15, 3, tzinfo=timezone.utc)
    assert select_default_wg(cfg, schedules, rows, now=now) == 'ran-plenary'
    rows[0].update(StartDate='2026-09-14 09:00:00', EndDate='2026-09-18 17:00:00')
    assert select_default_wg(cfg, schedules, rows, now=now) == 'ran1'
    # Portal's MeetingPeriod is not trusted: it calls meetings months away ONGOING.
    rows[0].update(StartDate='2027-01-25 09:00:00', EndDate='2027-01-29 17:00:00', MeetingPeriod='ONGOING')
    rows[1]['MeetingPeriod'] = 'UNKNOWN'
    assert select_default_wg(cfg, schedules, rows, now=now) == 'ran-plenary'


def test_ongoing_is_the_venue_calendar_day():
    from shared.navigation import select_default_wg
    cfg = {'default_wg': 'ran1', 'working_groups': [{'id': 'ran1'}, {'id': 'ran-plenary'}]}
    schedules = {'ran1': replace(plenary_schedule(), wg_id='ran1', meeting_id='ran1#126', is_demo=False),
                 'ran-plenary': replace(plenary_schedule(), meeting_id='ran#113', is_demo=False,
                                        timezone='America/Edmonton')}
    rows = [{'Id': 2, 'TBId': 373, 'Title': '3GPPRAN#113', 'Country': 'CA',
             'StartTimeZone': '(GMT-07:00) Mountain Time (US & Canada)',
             'StartDate': '2026-11-16 08:30:00', 'EndDate': '2026-11-20 17:00:00'}]
    # Monday 16 Nov already in Seoul but still Sunday in Calgary: not yet.
    assert select_default_wg(cfg, schedules, rows, now=datetime(2026, 11, 16, 2, tzinfo=timezone.utc)) == 'ran1'
    # Monday early morning in Calgary, before the opening: the meeting's first day counts.
    assert select_default_wg(cfg, schedules, rows, now=datetime(2026, 11, 16, 13, tzinfo=timezone.utc)) == 'ran-plenary'
    # Friday evening in Calgary is Saturday in UTC: still the last day there.
    assert select_default_wg(cfg, schedules, rows, now=datetime(2026, 11, 21, 4, tzinfo=timezone.utc)) == 'ran-plenary'
    # A label the resolver does not know falls back to the saved page's zone.
    rows[0]['StartTimeZone'] = '(GMT-07:00) Somewhere'
    assert select_default_wg(cfg, schedules, rows, now=datetime(2026, 11, 16, 13, tzinfo=timezone.utc)) == 'ran-plenary'


def test_default_cannot_promote_demo_missing_or_different_meeting():
    from shared.navigation import select_default_wg
    cfg = {'default_wg': 'ran1', 'working_groups': [{'id': 'ran1'}, {'id': 'ran-plenary'}]}
    ran1 = replace(plenary_schedule(), wg_id='ran1', meeting_id='ran1#126', is_demo=False)
    rows = [{'Id': 2, 'TBId': 373, 'Title': '3GPPRAN#113', 'MeetingPeriod': 'ONGOING'}]
    for other in [plenary_schedule(), replace(plenary_schedule(), meeting_id='ran#112', is_demo=False)]:
        assert select_default_wg(cfg, {'ran1': ran1, 'ran-plenary': other}, rows) == 'ran1'
    assert select_default_wg(cfg, {'ran1': ran1}, rows) == 'ran1'


def test_offline_default_uses_saved_venue_dates_and_preserves_query_and_hash(tmp_path):
    from shared.navigation import select_default_wg
    cfg = {'default_wg': 'ran-plenary', 'working_groups': [{'id': 'ran1'}, {'id': 'ran-plenary'}]}
    live = replace(plenary_schedule(), wg_id='ran1', is_demo=False, timezone='Asia/Seoul',
                   starts_on='2026-09-15', ends_on='2026-09-15')
    schedules = {'ran1': live, 'ran-plenary': plenary_schedule()}
    now = datetime(2026, 9, 14, 16, tzinfo=timezone.utc)
    assert select_default_wg(cfg, schedules, now=now) == 'ran1'
    # An authoritative empty API result must not infer ONGOING from dates.
    assert select_default_wg(cfg, schedules, [], now=now) == 'ran-plenary'
    root = build.render_root(cfg, schedules, [])
    assert 'location.search + location.hash' in root


def test_day_is_chosen_before_its_grid_is_parsed():
    html = generate_html(plenary_schedule())
    # The script after the tabs shows a day by a rule, so the grid is drawn in place as it arrives.
    assert html.index("style.id = 'initial-day'") < html.index('class="day-panel"')
    assert html.index('class="tab"') < html.index("style.id = 'initial-day'")


def test_switching_meetings_cross_fades_but_entering_from_the_root_does_not(tmp_path):
    opts = options(tmp_path)
    seed_ran1(opts)
    opts.render_only = True
    save_schedule(plenary_schedule(), opts.output_dir / 'ran-plenary/schedule.json')
    build.build_site(opts)
    assert '@view-transition { navigation: auto; }' in (opts.output_dir / 'ran1/index.html').read_text()
    assert '@view-transition' not in (opts.output_dir / 'index.html').read_text()


def test_the_grid_end_follows_the_day_panels_and_the_arrival_check_is_in_the_head():
    html = generate_html(plenary_schedule())
    head, body = html.split('</head>', 1)
    assert 'blocking="render"' in head  # switch-arrival.js, parser-run in <head>.
    panels = BeautifulSoup(body, 'html.parser')
    end = panels.select_one('#grid-end')
    assert end is not None and end.has_attr('hidden')
    assert not end.find_all_next(class_='day-panel')
    assert end.find_all_previous(class_='day-panel')


def test_what_both_pages_have_is_named_to_move_on_a_meeting_switch():
    html = BeautifulSoup(generate_html(plenary_schedule()), 'html.parser')
    # One name per day tab and, within a day, per time: each moves to its partner on the next page.
    assert [t['style'] for t in html.select('.schedule-toolbar .tab')] == [
        f"view-transition-name:day-tab-{t['data-day']};view-transition-class:grid-move"
        for t in html.select('.schedule-toolbar .tab')]
    for panel in html.select('.day-panel'):
        names = [label['style'].split('view-transition-name:')[1].split(';')[0] for label in panel.select('.time-label')]
        assert names and len(set(names)) == len(names)
        assert names[0] == f"time-{panel.select_one('.schedule-grid')['data-start']}"
