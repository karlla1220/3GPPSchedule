"""When a page moves to the next meeting, and what a run reads."""
from datetime import datetime

import pytest

from shared.meeting_turn import (ANYWHERE_ON_EARTH, Dates, current_meeting, going, has_started, holds, known_dates,
                                 to_read)
from working_groups.ran1 import config as ran1_config, downloader

SEOUL, CALGARY = 'Asia/Seoul', 'America/Edmonton'
DATES = {'ran1#126bis': Dates('2026-10-12', '2026-10-16', SEOUL),
         'ran1#127': Dates('2026-11-16', '2026-11-20', CALGARY),
         'ran1#128': Dates('2027-01-25', '2027-01-29', 'Asia/Tokyo')}


def at(moment):
    return datetime.fromisoformat(moment)


def rank(meeting):
    return float(meeting.split('#')[1].replace('bis', '.5'))


def pick(built, scheduled, moment):
    return current_meeting(built, set(scheduled), DATES, at(moment), rank)


def test_a_next_schedule_waits_until_the_meeting_has_ended():
    both = ['ran1#126bis', 'ran1#127']
    assert pick('ran1#126bis', both, '2026-10-14T12:00+09:00') == 'ran1#126bis'
    assert pick('ran1#126bis', both, '2026-10-16T23:59+09:00') == 'ran1#126bis'
    assert pick('ran1#126bis', both, '2026-10-17T00:00+09:00') == 'ran1#127'


def test_an_upcoming_built_meeting_also_holds():
    assert pick('ran1#127', ['ran1#127', 'ran1#128'], '2026-11-01T12:00+09:00') == 'ran1#127'


def test_the_end_is_the_venues_day_not_seouls():
    both = ['ran1#127', 'ran1#128']
    # Saturday in Seoul, the last morning in Calgary.
    assert pick('ran1#127', both, '2026-11-21T00:30+09:00') == 'ran1#127'
    assert pick('ran1#127', both, '2026-11-20T23:59-06:00') == 'ran1#127'
    assert pick('ran1#127', both, '2026-11-21T00:00-06:00') == 'ran1#128'


def test_without_a_next_schedule_the_page_stays():
    assert pick('ran1#126bis', ['ran1#126bis'], '2026-11-02T12:00+09:00') == 'ran1#126bis'


def test_the_earliest_open_meeting_with_a_schedule_is_next():
    assert pick('ran1#126bis', ['ran1#126bis', 'ran1#127', 'ran1#128'], '2026-11-02T12:00+09:00') == 'ran1#127'
    assert pick('ran1#126bis', ['ran1#126bis', 'ran1#128'], '2026-12-01T12:00+09:00') == 'ran1#128'


def test_a_held_meeting_with_no_file_listed_does_not_move_or_step_back():
    assert pick('ran1#127', ['ran1#126bis', 'ran1#128'], '2026-11-18T12:00-06:00') == 'ran1#127'
    assert pick('ran1#128', ['ran1#127'], '2027-02-01T12:00+09:00') == 'ran1#128'


def test_with_no_date_for_the_built_meeting_the_highest_stands():
    assert current_meeting('ran1#126', {'ran1#126', 'ran1#126bis'}, {}, at('2026-10-14T12:00+09:00'), rank) == 'ran1#126bis'


def test_an_unknown_venue_cannot_end_a_meeting_early():
    dates = {'ran1#127': Dates('2026-11-16', '2026-11-20')}
    assert Dates('a', 'b').timezone == ANYWHERE_ON_EARTH
    assert holds('ran1#127', dates, at('2026-11-21T10:00+00:00'))
    assert not holds('ran1#127', dates, at('2026-11-21T13:00+00:00'))


def test_a_run_reads_the_built_meeting_and_the_next_one_only():
    assert to_read('ran1#126bis', DATES, at('2026-10-14T12:00+09:00')) == ['ran1#126bis', 'ran1#127']
    assert to_read('ran1#126bis', DATES, at('2026-11-02T12:00+09:00')) == ['ran1#126bis', 'ran1#127']
    assert to_read('ran1#127', DATES, at('2026-11-18T12:00-06:00')) == ['ran1#127', 'ran1#128']
    # The meeting after the built one passed without a schedule.
    assert to_read('ran1#126bis', DATES, at('2026-12-01T12:00+09:00')) == ['ran1#126bis', 'ran1#128']
    assert to_read('ran1#128', DATES, at('2027-02-01T12:00+09:00')) == ['ran1#128']
    assert to_read('ran1#127', {}, at('2026-11-18T12:00-06:00')) == ['ran1#127']


def test_a_first_build_reads_the_last_meeting_and_the_next_one():
    assert to_read(None, DATES, at('2026-11-02T12:00+09:00')) == ['ran1#126bis', 'ran1#127']
    assert to_read(None, {}, at('2026-11-02T12:00+09:00')) == []


def test_dates_come_from_the_config_portal_and_the_last_build():
    saved = {'meeting_id': 'ran1#126bis', 'metadata': {'starts_on': '2026-10-12', 'ends_on': '2026-10-16',
                                                       'timezone': SEOUL}}
    rows = [{'Title': '3GPPRAN1#127', 'Id': 1, 'StartDate': '2026-11-16 09:00:00', 'EndDate': '2026-11-20 17:30:00',
             'StartTimeZone': '(GMT-07:00) Mountain Time (US & Canada)', 'Country': 'CA'},
            {'Title': '3GPPRAN1#126-bis', 'Id': 3, 'StartDate': '2026-10-12 09:00:00', 'EndDate': '2026-10-17 12:00:00'}]
    dates = known_dates(rows, saved, {'ran1#128': {'starts_on': '2027-01-25', 'ends_on': '2027-01-29'}})
    # A Portal row without a venue keeps the zone of the last build.
    assert dates['ran1#126bis'] == Dates('2026-10-12', '2026-10-17', SEOUL)
    assert dates['ran1#127'] == Dates('2026-11-16', '2026-11-20', CALGARY)
    assert dates['ran1#128'] == Dates('2027-01-25', '2027-01-29', ANYWHERE_ON_EARTH)
    assert known_dates(None, {}) == {}


def test_sync_files_without_a_meeting_count_from_its_first_day():
    assert not has_started('ran1#127', DATES, at('2026-11-15T23:59-06:00'))
    assert has_started('ran1#127', DATES, at('2026-11-16T00:00-06:00'))
    assert not has_started('ran1#999', DATES, at('2026-11-16T00:00-06:00'))


def test_going_lists_open_meetings_earliest_first():
    assert going(DATES, at('2026-10-14T12:00+09:00')) == ['ran1#126bis', 'ran1#127', 'ran1#128']
    assert going(DATES, at('2026-12-01T12:00+09:00')) == ['ran1#128']


# ------------------------------------------------------------------------ RAN1

ROWS_RAN1 = [{'Title': '3GPPRAN1#127', 'TBId': 379, 'Id': 1, 'StartDate': '2026-11-16 09:00:00',
              'EndDate': '2026-11-20 17:30:00', 'StartTimeZone': '(GMT-07:00) Mountain Time (US & Canada)',
              'Country': 'CA', 'MtgDocURL': 'https://www.3gpp.org/ftp/tsg_ran/WG1_RL1/TSGR1_127/'},
             {'Title': '3GPPRAN1#128', 'TBId': 379, 'Id': 2, 'StartDate': '2027-01-25 09:00:00',
              'EndDate': '2027-01-29 17:30:00', 'StartTimeZone': '(GMT+09:00) Osaka, Sapporo, Tokyo',
              'Country': 'JP', 'MtgDocURL': 'https://www.3gpp.org/ftp/tsg_ran/WG1_RL1/TSGR1_128/'}]


@pytest.fixture
def ran1_cfg(tmp_path, monkeypatch):
    for name in ('SCHEDULE_MEETING_SPECIFIC', 'SCHEDULE_MEETING_SPECIFIC_OVERRIDE', 'SCHEDULE_MEETING_SYNC'):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / 'config.json'
    path.write_text('{"meeting_specific": []}')
    return ran1_config.load_config(path)


def test_ran1_holds_the_built_meeting_and_still_reads_the_next_folder(ran1_cfg):
    during = ran1_config.with_meeting_folders(ran1_cfg, 'ran1#127', rows=ROWS_RAN1, now=at('2026-11-18T12:00-06:00'))
    assert during['held_meeting'] == 'ran1#127'
    assert [url.split('/')[-3] for url in during['inbox_urls'][1:]] == ['TSGR1_127', 'TSGR1_128']
    # Seoul's Saturday is still Calgary's Friday.
    assert ran1_config.with_meeting_folders(ran1_cfg, 'ran1#127', rows=ROWS_RAN1,
                                            now=at('2026-11-21T00:30+09:00'))['held_meeting'] == 'ran1#127'
    assert ran1_config.with_meeting_folders(ran1_cfg, 'ran1#127', rows=ROWS_RAN1,
                                            now=at('2026-11-21T00:30-06:00'))['held_meeting'] is None


def test_ran1_holds_on_the_saved_dates_when_portal_has_none(ran1_cfg):
    saved = {'type': 'portal', 'starts_on': '2026-11-16', 'ends_on': '2026-11-20', 'timezone': CALGARY}
    found = ran1_config.with_meeting_folders(ran1_cfg, 'ran1#127', rows=[], now=at('2026-11-18T12:00-06:00'),
                                             saved_dates=saved)
    assert found['held_meeting'] == 'ran1#127'
    assert ran1_config.with_meeting_folders(ran1_cfg, 'ran1#127', rows=[], now=at('2026-11-18T12:00-06:00'))[
        'held_meeting'] is None


def test_a_held_ran1_meeting_ignores_the_next_meetings_draft(monkeypatch):
    uploaded = datetime(2026, 11, 18)
    listing = {'Chair_notes': [{'name': n, 'url': 'u/' + n, 'uploaded_at': uploaded} for n in (
        'RAN1#127 online and offline schedules - v05.docx', 'Draft RAN1#128 online and offline schedules - v00.docx')]}
    monkeypatch.setattr(downloader, 'list_inbox_subfolders', lambda url: [{'name': 'Chair_notes', 'url': 'Chair_notes'}])
    monkeypatch.setattr(downloader, 'list_remote_files', lambda url: listing.get(url, []))
    held = downloader.discover_schedule_sources(urls=['inbox'], preferred_meeting_id='ran1#127',
                                                locked_meeting_id='ran1#127')
    assert [s.file_info['name'] for s in held] == ['RAN1#127 online and offline schedules - v05.docx']
    free = downloader.discover_schedule_sources(urls=['inbox'], preferred_meeting_id='ran1#127')
    assert [s.file_info['name'] for s in free] == ['Draft RAN1#128 online and offline schedules - v00.docx']
