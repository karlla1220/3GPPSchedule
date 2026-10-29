"""Venue timezones: Portal labels, the venue list, and the LLM asked once."""
from datetime import date
import json

from shared import portal_meetings as portal
from shared import timezones

PRAGUE = '(GMT+02.00)  Belgrade, Bratislava, Budapest, Ljubljana, Prague'


def asker(answer):
    calls = []

    def request(prompt, schema):
        calls.append(prompt)
        return answer
    return request, calls


def test_portal_labels_seen_on_sa_and_ran_rows_need_no_call():
    assert timezones.from_label(PRAGUE, 'CZ') == 'Europe/Prague'
    assert timezones.from_label('(GMT+05:30) Chennai, Kolkata, Mumbai, New Delhi', 'IN') == 'Asia/Kolkata'
    assert timezones.from_label('(GMT-07:00) Mountain Time (US & Canada)', 'CA') == 'America/Edmonton'
    assert timezones.from_label('Asia/Seoul') == 'Asia/Seoul'
    assert timezones.from_label('(GMT+02:00) Unknown') is None


def test_label_offsets_include_sign_and_minutes():
    assert timezones.label_offset(PRAGUE) == 120
    assert timezones.label_offset('(GMT+05:30) Chennai') == 330
    assert timezones.label_offset('(GMT-03:30) Newfoundland') == -210
    assert timezones.label_offset('Greenwich Mean Time') is None


def test_an_unknown_venue_is_asked_once_and_listed():
    request, calls = asker({'timezone': 'Asia/Kolkata', 'city': 'Bengaluru', 'country': 'India'})
    venue = ('(GMT+05:30) Somewhere new', 'IN', 'Bengaluru')
    assert timezones.resolve(*venue, on=date(2027, 8, 30), request=request) == 'Asia/Kolkata'
    assert timezones.resolve(*venue, on=date(2027, 8, 30), request=request) == 'Asia/Kolkata'
    assert len(calls) == 1 and 'Bengaluru' in calls[0]
    # Checks read the list without asking.
    assert timezones.known(*venue) == 'Asia/Kolkata'
    record = json.loads(timezones.MAP_PATH.read_text().splitlines()[0])
    assert record['location'] == 'Bengaluru' and record['raw']['city'] == 'Bengaluru'


def test_a_later_line_corrects_an_answer():
    venue = {'label': '(GMT+01:00) Somewhere', 'country': 'XX', 'location': 'Town'}
    timezones.MAP_PATH.write_text('\n'.join(json.dumps({**venue, 'timezone': zone})
                                            for zone in ('Europe/Paris', 'Europe/Berlin')) + '\n')
    assert timezones.known(venue['label'], venue['country'], venue['location']) == 'Europe/Berlin'


def test_an_answer_off_the_label_offset_or_invalid_is_not_listed():
    for answer in ({'timezone': 'Asia/Tokyo'}, {'timezone': 'Mars/Olympus'}, None):
        request, calls = asker(answer)
        assert timezones.resolve('(GMT+02.00) Somewhere', 'CZ', 'Prague', on=date(2026, 8, 24),
                                 request=request) is None
        assert len(calls) == 1
    assert not timezones.MAP_PATH.exists()


def test_daylight_saving_offset_in_the_label_is_accepted_either_season():
    # Portal shows +02.00 for Prague all year; the zone is +01:00 in winter.
    request, _ = asker({'timezone': 'Europe/Prague'})
    assert timezones.resolve('(GMT+02.00) Elsewhere', 'CZ', 'Prague', on=date(2026, 11, 16),
                             request=request) == 'Europe/Prague'


def test_a_failed_call_is_retried_next_time():
    def broken(prompt, schema):
        raise RuntimeError('503')
    assert timezones.resolve('(GMT+02:00) Somewhere', 'CZ', 'Prague', request=broken) is None
    request, calls = asker({'timezone': 'Europe/Prague'})
    assert timezones.resolve('(GMT+02:00) Somewhere', 'CZ', 'Prague', request=request) == 'Europe/Prague'
    assert len(calls) == 1


def test_nothing_to_ask_about_makes_no_call():
    request, calls = asker({'timezone': 'UTC'})
    assert timezones.resolve(None, None, None, request=request) is None
    assert timezones.resolve('', 'CZ', '', request=request) is None
    assert calls == []


def test_portal_rows_resolve_from_the_list_and_builds_may_ask():
    row = {'Id': 9, 'Title': '3GPPSA2#176', 'Country': 'CZ', 'Location': 'Prague',
           'StartTimeZone': '(GMT+02.00) Not in the table', 'EndTimeZone': '(GMT+02.00) Not in the table',
           'StartDate': '2026-08-24 09:00:00', 'EndDate': '2026-08-28 17:00:00'}
    assert portal.timezone_reference(row) is None
    request, calls = asker({'timezone': 'Europe/Prague'})
    ask = lambda *a, **k: timezones.resolve(*a, request=request, **k)
    ref = portal.timezone_reference(row, ask)
    assert ref['timezone'] == 'Europe/Prague' and ref['starts_at'] == '2026-08-24T09:00:00+02:00'
    assert len(calls) == 1
    # The default (check) lookup now answers from the list.
    assert portal.timezone_reference(row)['timezone'] == 'Europe/Prague'
