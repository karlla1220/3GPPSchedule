"""Portal HTTP boundary and interpretation of captured public API fields."""
from datetime import date, datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from shared import portal_meetings as portal


@pytest.fixture
def rows():
    return json.loads((Path(__file__).parent / 'fixtures/portal_meetings.json').read_text())


@pytest.fixture(autouse=True)
def clear_cache():
    portal._fetch_meetings.cache_clear()
    yield
    portal._fetch_meetings.cache_clear()


def transport(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(portal.httpx, 'Client', lambda **kwargs: client(
        **kwargs, transport=httpx.MockTransport(handler)))


def test_actual_timezone_labels_map_to_regional_rules(rows):
    expected = {60799: 'Europe/Madrid', 60713: 'Europe/Amsterdam',
                60714: 'Asia/Seoul', 60715: 'America/Edmonton', 60712: 'Asia/Shanghai'}
    for row in rows:
        ref = portal.timezone_reference(row)
        assert ref['timezone'] == expected[row['Id']]
        assert ref['starts_on'] == row['StartDate'][:10]
    zone = ZoneInfo('Europe/Amsterdam')
    assert datetime(2026, 1, 1, tzinfo=zone).utcoffset().total_seconds() == 3600
    assert datetime(2026, 8, 24, tzinfo=zone).utcoffset().total_seconds() == 7200


def test_matches_exact_meeting_and_tb_including_bis_and_duplicate_rows(rows):
    assert portal.find_meeting(rows + rows, 'ran1', 'RAN1#126-bis')['Id'] == 60714
    assert portal.find_meeting(rows, 'ran1', 'ran1#126')['Id'] == 60713
    assert portal.find_meeting(rows, 'ran2', 'ran1#126') is None
    assert portal.find_meeting(rows, 'ran1', 'ran1#128') is None
    social = {**rows[0], 'TBId': 379, 'Title': '3GPPRAN1#126-Social event'}
    assert portal.find_meeting([social], 'ran1', 'ran1#126') is None
    conflict = {**portal.find_meeting(rows, 'ran1', 'ran1#126'), 'Id': 1}
    assert portal.find_meeting(rows + [conflict], 'ran1', 'ran1#126') is None


def test_unknown_labels_are_not_guessed_from_offset(rows):
    row = rows[0]
    assert portal.timezone_reference({**row, 'StartTimeZone': '(GMT+02:00) Unknown'}) is None
    assert portal.timezone_reference({**row, 'EndTimeZone': '(GMT+09:00) Seoul'}) is None
    assert portal.timezone_reference({**row, 'EndDate': 'bad'}) is None
    assert portal.timezone_from_label('Asia/Seoul') == 'Asia/Seoul'


def test_fetch_batches_wgs_and_follows_pages_without_dropping_null_ftp(monkeypatch, rows):
    requests = []
    page = [{**rows[0], 'Id': i} for i in range(100)]
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=page if len(requests) == 1 else rows)
    transport(monkeypatch, handle)
    result = portal._fetch_meetings(date(2026, 9, 15))
    assert len(result) == 100 + len(rows)
    assert any(r['MtgDocURL'] is None for r in result)
    first, second = [json.loads(r.content)['getMeetingsInput'] for r in requests]
    assert requests[0].method == 'POST' and str(requests[0].url) == portal.URL
    assert first['Tbs'] == [373, 379, 380, 381, 382]
    assert first['StartRow'] == 0 and second['StartRow'] == 100
    assert first['StartDate'] < '2026-09-15' < first['EndDate']
    assert portal._fetch_meetings(date(2026, 9, 15)) == result
    assert len(requests) == 2


@pytest.mark.parametrize('payload', [{}, [None]])
def test_bad_response_is_an_error_not_an_empty_meeting_list(monkeypatch, payload):
    transport(monkeypatch, lambda _: httpx.Response(200, json=payload))
    with pytest.raises(ValueError, match='response'):
        portal.get_meetings()


def test_http_failure_is_observable_and_timezone_lookup_can_fall_back(monkeypatch, capsys):
    transport(monkeypatch, lambda _: httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        portal.get_meetings()
    assert portal.lookup_timezone_reference('ran1', 'ran1#126') is None
    assert 'lookup failed' in capsys.readouterr().out


def test_repeated_full_page_is_rejected(monkeypatch, rows):
    transport(monkeypatch, lambda _: httpx.Response(200, json=rows * 20))
    with pytest.raises(ValueError, match='repeated'):
        portal.get_meetings()


def test_api_preserves_time_of_day_with_venue_offset(rows):
    row = portal.find_meeting(rows, 'ran1', 'ran1#126')
    ref = portal.timezone_reference(row)
    assert ref['starts_at'] == '2026-08-24T09:00:00+02:00'
    assert ref['ends_at'] == '2026-08-28T17:30:00+02:00'
    reversed_times = {**row, 'StartDate': '2026-08-24 17:30:00', 'EndDate': '2026-08-24 09:00:00'}
    assert portal.timezone_reference(reversed_times) is None


def test_api_instants_use_dst_rules_at_each_endpoint(rows):
    row = portal.find_meeting(rows, 'ran1', 'ran1#126')
    ref = portal.timezone_reference({**row, 'StartDate': '2026-10-23 09:00:00',
                                    'EndDate': '2026-10-26 17:30:00'})
    assert ref['starts_at'] == '2026-10-23T09:00:00+02:00'
    assert ref['ends_at'] == '2026-10-26T17:30:00+01:00'
