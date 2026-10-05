"""RAN2 session schedules: real chair documents plus the rules they rely on."""
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from bs4 import BeautifulSoup
from docx import Document
import httpx
import pytest

from shared.renderer import generate_html
from shared.schedule import load_schedule, save_schedule, time_to_minutes
from working_groups.ran2 import document as docmod, lifecycle, pipeline, sessions, sources

FIXTURES = Path(__file__).parent / 'fixtures/ran2'
V11 = 'R2_135_Schedule_v11.docx'
BIS = 'R2_135b_Schedule_v00.docx'
OLD = 'R2_131bis_Schedule v19.docx'
META_135 = {'starts_on': '2026-08-24', 'ends_on': '2026-08-28', 'timezone': 'Europe/Amsterdam',
            'starts_at': '2026-08-24T09:00:00+02:00', 'ends_at': '2026-08-28T17:30:00+02:00'}
META_131BIS = {'starts_on': '2025-10-13', 'ends_on': '2025-10-17', 'timezone': 'Europe/Prague'}


def extract(name):
    return docmod.extract_document((FIXTURES / name).read_bytes(), name)


def agenda(name):
    return docmod.agenda_map((FIXTURES / name).read_bytes())


@pytest.fixture(scope='module')
def v11():
    return sessions.make_schedule(extract(V11), agenda('agenda_135.csv'), META_135,
                                  [V11, 'agenda.csv'], '2026-08-28 12:00')


def find(schedule, day, room, start):
    day = next(d for d in schedule.days if d.day_name == day)
    return next(s for s in day.sessions if s.room_ids == [room] and s.start_time == start)


def synthetic(cells, offline=(), slots=(('08:30', '10:30'), ('11:00', '13:00'))):
    """A document dict with Monday cells: (room, start, end, lines)."""
    rooms = [{'id': i, 'name': n, 'label': n} for i, n in
             [('main', 'Main'), ('brk1', 'Breakout 1'), ('brk2', 'Breakout 2'), ('brk3', 'Breakout 3')]]
    return {'meeting_id': 'ran2#135', 'title': 'RAN2-135 Session Schedule', 'rooms': rooms,
            'days': [{'day': 'Monday', 'slots': list(slots), 'notes': [],
                      'cells': [{'id': f'c{i}', 'rooms': [room], 'start': start, 'end': end, 'lines': lines}
                                for i, (room, start, end, lines) in enumerate(cells)]}],
            'breaks': [], 'offline': list(offline), 'paragraphs': []}


def blocks(document, agenda_items=None):
    return [(s['start'], s['end'], s['rooms'][0], s['name'], s['chair'], s['agenda_items'])
            for s in sessions.interpret(document, agenda_items)]


# ------------------------------------------------------------------ documents

@pytest.mark.parametrize('name, expected', [
    ('R2_135_Schedule_v11.docx', ('ran2#135', 11)),
    ('R2_135b_Schedule_v00.docx', ('ran2#135bis', 0)),
    ('R2_131bis_Schedule v19.docx', ('ran2#131bis', 19)),
    ('R2_132_Schedule v15.docx', ('ran2#132', 15)),
    ('R2_135_Agenda_v02.docx', None),
    ('R2-2605436.zip', None),
])
def test_schedule_filenames(name, expected):
    assert docmod.file_info(name) == expected


def test_real_table_rooms_days_breaks_and_offline_list():
    document = extract(V11)
    assert document['meeting_id'] == 'ran2#135'
    assert [r['name'] for r in document['rooms']] == ['Main', 'Breakout 1', 'Breakout 2', 'Breakout 3']
    assert [d['day'] for d in document['days']] == ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    thursday = document['days'][3]
    assert thursday['slots'] == [('08:30', '10:30'), ('10:50', '12:50'), ('14:15', '16:15'), ('16:40', '18:30')]
    assert 'Social event – end at 18:30 (times to be readjusted for this day)' in thursday['notes']
    assert [b['name'] for b in document['breaks']] == ['Morning coffee', 'Lunch', 'Afternoon coffee']
    first = document['offline'][1]
    assert first == {'numbers': ['004'], 'day': 'Tuesday', 'start': '11:00', 'end': '12:00', 'place': 'BO2',
                     'coordinator': 'Henning Wiemann (Ericsson)',
                     'title': 'Spectrum aggregation: Extract simulation results and observations based on submitted documents'}
    assert len(document['offline']) == 17


def test_tracked_changes_are_read_in_accepted_form():
    friday = extract(V11)['days'][4]
    main = next(c for c in friday['cells'] if c['rooms'] == ['main'] and c['start'] == '08:30')
    assert 'All other UP CBs' in main['lines']   # inside <w:ins>; python-docx .text drops it
    old_friday = extract(OLD)['days'][4]
    closing = next(c for c in old_friday['cells'] if c['rooms'] == ['main'] and c['start'] == '11:00')
    assert closing['lines'][1].startswith('@11:00-12:00')   # the deleted "-1" is gone


def test_a_cell_merged_across_two_slots_keeps_the_whole_span():
    monday = extract(OLD)['days'][0]
    main = next(c for c in monday['cells'] if c['rooms'] == ['main'])
    assert (main['start'], main['end']) == ('09:00', '13:00')


def docx_bytes(rows):
    doc = Document()
    doc.add_paragraph('RAN2-135\tSession Schedule')
    table = doc.add_table(rows=1, cols=5)
    for cell, text in zip(table.rows[0].cells, ['', 'Main room', 'Brk 1 room', 'Brk 2 room', 'Brk 3 room*']):
        cell.text = text
    for row in rows:
        cells = table.add_row().cells
        if isinstance(row, str):
            merged = cells[0].merge(cells[4])
            merged.text = row
        else:
            for cell, text in zip(cells, row):
                cell.text = text
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def test_missing_weekday_label_is_inferred_from_the_clock_going_back():
    data = docx_bytes(['Monday', ['09:00 – 10:30', 'Opening', '', '', ''],
                       ['14:30-16:30', 'Afternoon', '', '', ''], '',
                       ['08:30 – 10:30', 'Next morning', '', '', '']])
    document = docmod.extract_document(data, 'R2_135_Schedule_v00.docx')
    assert [(d['day'], d['slots']) for d in document['days']] == [
        ('Monday', [('09:00', '10:30'), ('14:30', '16:30')]), ('Tuesday', [('08:30', '10:30')])]


def test_document_and_filename_must_agree():
    with pytest.raises(ValueError, match='disagrees'):
        docmod.extract_document(docx_bytes(['Monday', ['09:00-10:30', 'x', '', '', '']]), 'R2_136_Schedule_v01.docx')


# ---------------------------------------------------------------- cell rules

def test_prefix_markers_split_a_cell():
    document = synthetic([('brk1', '08:30', '10:30', [
        '@8:30-9:30', '[7.1] NR19 AI/ML PHY [0] (Erlin)', '@9:30-10:30', '[8.1] NR20 AI/M PHY [2] (Erlin)',
        '[8.1.1]', '[8.1.2] if time allows'])])
    assert blocks(document) == [
        ('08:30', '09:30', 'brk1', 'NR19 AI/ML PHY', 'Erlin', ['7.1']),
        ('09:30', '10:30', 'brk1', 'NR20 AI/M PHY', 'Erlin', ['8.1', '8.1.1', '8.1.2'])]


def test_a_header_written_above_its_marker_moves_with_it():
    # RAN2#133bis writes the next part's header before its time.
    document = synthetic([('brk1', '17:30', '19:30', [
        '[7.1] NR19 AI/ML PHY [0] (Erlin)', '@17:30-18:30', '[8.1] NR20 AI/M PHY [1] (Erlin)',
        '@18:30-19:30', '[8.1.1]', '[8.1.2]'])], slots=[('17:30', '19:30')])
    assert blocks(document) == [
        ('17:30', '18:30', 'brk1', 'NR19 AI/ML PHY', 'Erlin', ['7.1']),
        ('18:30', '19:30', 'brk1', 'NR20 AI/M PHY', 'Erlin', ['8.1', '8.1.1', '8.1.2'])]


def test_from_and_bare_clock_markers_and_an_end_cap():
    document = synthetic([
        ('brk2', '14:30', '16:30', ['14:30-15:30 [004] (Xiaomi)', '', 'From 15:30:',
                                    '[8.8] E-UTRA TN to NR NTN HO (Sergio)']),
        ('brk3', '11:00', '13:00', ['12:00 [9.3.2.5] CA offline (Mattias)']),
        ('brk1', '17:00', '19:00', ['[8.2] NR20 AIoT [2] (Nathan)', 'Overflow from afternoon session, end by 18:30']),
    ], slots=[('11:00', '13:00'), ('14:30', '16:30'), ('17:00', '19:00')])
    found = {(b[0], b[1], b[2]) for b in blocks(document)}
    assert found == {('14:30', '15:30', 'brk2'), ('15:30', '16:30', 'brk2'), ('12:00', '13:00', 'brk3'),
                     ('17:00', '18:30', 'brk1')}


def test_agenda_items_budgets_offline_numbers_and_ranges():
    line = sessions.analyze('[6.0.2.1] - [6.0.2.4], [6.0.2.14]', set(), set(), None)
    assert line.ais == ['6.0.2.1', '6.0.2.2', '6.0.2.3', '6.0.2.4', '6.0.2.14'] and line.lead
    line = sessions.analyze('[8.3] NR20 AI mobility [1.5] (Kyeongin)', {'Kyeongin'}, set(), None)
    assert (line.text, line.ais, line.chairs) == ('NR20 AI mobility', ['8.3'], ['Kyeongin'])
    line = sessions.analyze('[011] [9.4.1] Mobility offline (Jedrzej)', set(), set(), None)
    assert line.offline == ['011']
    line = sessions.analyze('[8.2.1 Organizational', set(), set(), None)   # unclosed in RAN2#134
    assert (line.text, line.ais) == ('Organizational', ['8.2.1'])
    line = sessions.analyze('[605] [SONMDT] Rel19 37.320 SON MDT corrections', set(), set(), None)
    assert line.ais == [] and line.offline == ['605'] and '[SONMDT]' in line.text
    line = sessions.analyze('6.0.2.4, 5.1.3.2, 6.0.2', set(), set(), {'6.0.2.4': 'a', '5.1.3.2': 'b', '6.0.2': 'c'})
    assert line.ais == ['6.0.2.4', '5.1.3.2', '6.0.2']
    line = sessions.analyze('(if time allows) [6.0.2.16] R18 XR (Dawid)', {'Dawid'}, set(), None)
    assert (line.text, line.ais, line.lead) == ('R18 XR', ['6.0.2.16'], True)
    assert sessions.analyze('[8.12.1] (e)RedCap Less than 5 MHz', set(), set(), None).text == '(e)RedCap Less than 5 MHz'


def test_chairs_are_learned_and_companies_are_not_chairs():
    document = synthetic([('main', '08:30', '10:30', ['Session report from Mattias', '[9.3.3] Common CP/UP']),
                          ('brk1', '08:30', '10:30', ['[7.10] NR19 SONMDT [0] (Mattias)']),
                          ('brk2', '08:30', '10:30', ['11:00-12:00 [004] (Ericsson, Nokia)']),
                          ('main', '11:00', '13:00', ['[9.3.1] 6GR Control Plane', 'CB Intersite spectrum aggregation'])])
    found = {b[3]: b[4] for b in blocks(document)}
    assert found['Common CP/UP'] is None
    assert found['NR19 SONMDT'] == 'Mattias'
    assert found['6GR Control Plane'] is None
    assert sessions.learn_names(document)[0] == {'Mattias'}


def test_untimed_sub_row_fills_the_time_after_a_timed_offline():
    document = synthetic([('brk2', '10:50', '12:50', ['UP offline', '10:50-11:50 [009] (InterDigital)']),
                          ('brk2', '10:50', '12:50', ['CB Mattias', 'CB NR19 SONMDT [0] (Mattias)'])],
                         slots=[('10:50', '12:50')])
    assert [(b[0], b[1], b[3]) for b in blocks(document)] == [
        ('10:50', '11:50', 'UP offline'), ('11:50', '12:50', 'CB: NR19 SONMDT')]


def test_a_labelled_block_after_an_explicit_end_starts_there():
    document = synthetic([('brk1', '08:30', '10:30', [
        'CB Kyeongin', '@8:30-9:30', 'R20 AI Mob comebacks/ and continue [8.3]', '', 'CB Erlin',
        '[8.1] NR20 AI/M PHY [1] (Erlin)'])])
    assert [(b[0], b[1], b[3], b[4]) for b in blocks(document)] == [
        ('08:30', '09:30', 'CB: R20 AI Mob comebacks/ and continue', 'Kyeongin'),
        ('09:30', '10:30', 'CB: NR20 AI/M PHY', 'Erlin')]


def test_offline_list_names_numbered_blocks_and_fills_free_rooms():
    records = [
        {'numbers': ['004'], 'title': 'Spectrum aggregation', 'day': 'Monday', 'start': '11:00', 'end': '12:00',
         'place': 'BO2', 'coordinator': 'Henning Wiemann (Ericsson)'},
        {'numbers': ['203'], 'title': '[LPWUS] CN-based subgrouping', 'day': 'Monday', 'start': '10:30',
         'end': '11:00', 'place': 'BO3', 'coordinator': 'Alexey Kulakov (Vodafone)'},
    ]
    document = synthetic([('brk2', '11:00', '13:00', ['11:00-12:00 [004] (Ericsson, Nokia)'])], offline=records)
    assert [(b[0], b[1], b[2], b[3], b[4]) for b in blocks(document)] == [
        ('10:30', '11:00', 'brk3', '[203] [LPWUS] CN-based subgrouping', 'Alexey Kulakov'),
        ('11:00', '12:00', 'brk2', '[004] Spectrum aggregation', 'Henning Wiemann')]


def test_marker_like_agenda_numbers_are_content():
    document = synthetic([('main', '08:30', '10:30', ['8.10 NR20 MIMO', '@25:00 nonsense'])])
    assert [(b[0], b[1]) for b in blocks(document)] == [('08:30', '10:30')]


# ------------------------------------------------------ real schedule output

def test_real_schedule_days_breaks_and_key_blocks(v11):
    assert [(d.day_name, d.date) for d in v11.days] == [
        ('Monday', '2026-08-24'), ('Tuesday', '2026-08-25'), ('Wednesday', '2026-08-26'),
        ('Thursday', '2026-08-27'), ('Friday', '2026-08-28')]
    thursday = v11.days[3]
    assert [(b['name'], b['start'], b['end']) for b in thursday.timeline.breaks] == [
        ('Morning coffee', '10:30', '10:50'), ('Lunch', '12:50', '14:15'), ('Afternoon coffee', '16:15', '16:40')]
    assert [r.name for r in v11.days[0].rooms] == ['Main', 'Breakout 1', 'Breakout 2']

    opening = find(v11, 'Monday', 'main', '09:00')
    assert opening.chair == 'Diana' and opening.agenda_item.startswith('1, 2, 3, 6.0')
    assert opening.group_header == 'NR Rel-18'
    eutra = find(v11, 'Monday', 'brk1', '09:00')
    assert eutra.name == 'EUTRA&NR15161718' and eutra.chair == 'Mattias'
    assert {'6.0.2.1', '6.0.2.7', '6.0.2.12'} <= set(eutra.agenda_item.split(', '))
    offline = find(v11, 'Tuesday', 'brk2', '11:00')
    assert offline.name.startswith('[004] Spectrum aggregation: Extract simulation results')
    assert (offline.end_time, offline.chair, offline.group_header) == ('12:00', 'Henning Wiemann', 'Offline')
    cb = find(v11, 'Thursday', 'brk2', '11:50')
    assert cb.name == 'CB: EUTRA&NR15161718 / NR19 SONMDT …' and cb.end_time == '12:50'
    xr = find(v11, 'Wednesday', 'brk1', '17:00')
    assert xr.agenda_item == '7.7, 8.5' and xr.chair == 'Dawid'
    late = find(v11, 'Tuesday', 'brk1', '10:00')
    assert (late.name, late.end_time) == ('Offline disc on RRC details', '11:00')


@pytest.mark.parametrize('name, agenda_name, metadata', [
    (V11, 'agenda_135.csv', META_135),
    (BIS, 'agenda_135bis.csv', {'starts_on': '2026-10-12', 'ends_on': '2026-10-16', 'timezone': 'Asia/Seoul'}),
    (OLD, None, META_131BIS),
])
def test_every_room_shows_one_block_at_a_time(name, agenda_name, metadata):
    schedule = sessions.make_schedule(extract(name), agenda(agenda_name) if agenda_name else None, metadata,
                                      [name], '2026-10-05 12:00')
    for day in schedule.days:
        start, end = time_to_minutes(day.timeline.start), time_to_minutes(day.timeline.end)
        for room in day.rooms:
            spans = sorted((time_to_minutes(s.start_time), time_to_minutes(s.end_time))
                           for s in day.sessions if room.id in s.room_ids)
            assert all(a_end <= b_start for (_, a_end), (b_start, _) in zip(spans, spans[1:])), (day.day_name, room.id)
            assert all(start <= a < b <= end and a % 5 == 0 and b % 5 == 0 for a, b in spans)
    html = BeautifulSoup(generate_html(schedule), 'html.parser')
    assert len(html.select('.session-block')) == sum(len(d.sessions) for d in schedule.days)


def test_render_roundtrip_and_popup_keeps_source_lines(v11, tmp_path):
    save_schedule(v11, tmp_path / 'schedule.json')
    assert load_schedule(tmp_path / 'schedule.json') == v11
    html = BeautifulSoup(generate_html(v11), 'html.parser')
    block = html.select_one('#monday [data-name="R17/18 NR / IoT NTN"]')
    popup = block['data-popup']
    assert 'Note: [7.8] NR19 NR NTN [0] (Sergio)' in popup
    assert 'Note: [7.8.1], [7.8.2]' not in popup   # repeated by the AI field
    assert '7.8: NTN for NR Ph3' in popup.replace('<strong>', '').replace('</strong>', '')


def test_meeting_dates_must_cover_the_weekdays():
    with pytest.raises(ValueError, match='outside the meeting dates'):
        sessions.make_schedule(extract(V11), None, {**META_135, 'ends_on': '2026-08-27'}, [V11], 'now')


# ------------------------------------------------------------------- sources

def listing(names, base='https://www.3gpp.org/ftp/x/'):
    rows = ''.join(f'<tr><td><a href="{base}{n}">{n}</a></td><td>2026/09/30 8:32</td></tr>' for n in names)
    return f'<table>{rows}</table>'


def test_listing_parses_names_times_and_meeting_folders():
    entries = sources.listing_entries(listing(['TSGR2_135bis/', 'TSGR2_133b/', 'TSGR2_99/', 'Invitation/']),
                                      'https://www.3gpp.org/ftp/tsg_ran/WG2_RL2/')
    assert entries[0]['uploaded_at'] == '2026-09-30 08:32'
    assert sources.archive_folders(entries) == {
        'ran2#135bis': 'https://www.3gpp.org/ftp/x/TSGR2_135bis/',
        'ran2#133bis': 'https://www.3gpp.org/ftp/x/TSGR2_133b/',
        'ran2#99': 'https://www.3gpp.org/ftp/x/TSGR2_99/'}
    assert sources.rank('ran2#135') < sources.rank('ran2#135bis') < sources.rank('ran2#136')


def candidate(meeting, version, url='https://www.3gpp.org/ftp/tsg_ran/x', uploaded='2026-09-30 08:32'):
    return {'meeting_id': meeting, 'version': version, 'url': url, 'uploaded_at': uploaded, 'name': f'{meeting}-{version}'}


def test_the_current_meeting_wins_over_an_early_next_one():
    dates = {'ran2#135': ('2026-08-24', '2026-08-28'), 'ran2#135bis': ('2026-10-12', '2026-10-16'),
             'ran2#136': ('2026-11-16', '2026-11-20')}
    found = [candidate('ran2#135', 11), candidate('ran2#135bis', 0), candidate('ran2#136', 0)]
    assert sources.choose_schedule(found, dates, '2026-10-05')['meeting_id'] == 'ran2#135bis'
    assert sources.choose_schedule(found, dates, '2026-10-14')['meeting_id'] == 'ran2#135bis'
    assert sources.choose_schedule(found, dates, '2026-10-17')['meeting_id'] == 'ran2#136'
    # Nothing current yet: keep the latest meeting rather than an undated one.
    assert sources.choose_schedule(found[:2], dates, '2026-10-17')['meeting_id'] == 'ran2#135bis'
    assert sources.choose_schedule(found[:2], {}, '2026-10-17')['meeting_id'] == 'ran2#135bis'


def test_newest_version_wins_and_the_live_copy_breaks_a_tie():
    sync = 'https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN2/Agenda/R2_135_Schedule_v11.docx'
    found = [candidate('ran2#135', 10), candidate('ran2#135', 11, uploaded='2026-08-29 21:10'),
             candidate('ran2#135', 11, url=sync, uploaded='2026-08-27 20:49')]
    assert sources.choose_schedule(found, {}, '2026-08-27')['url'] == sync
    with pytest.raises(ValueError, match='No RAN2 session schedule'):
        sources.choose_schedule([], {}, '2026-08-27')


def test_portal_titles_with_and_without_space():
    rows = [{'Title': '3GPPRAN2#135-bis', 'TBId': 380, 'Id': 1, 'StartDate': '2026-10-12 09:00:00',
             'EndDate': '2026-10-16 17:30:00', 'StartTimeZone': '(GMT+09:00) Seoul', 'Country': 'KR',
             'Location': 'South Korea'},
            {'Title': '3GPP RAN2#132', 'TBId': 380, 'Id': 2, 'StartDate': '2025-11-17 09:00:00',
             'EndDate': '2025-11-21 17:30:00', 'StartTimeZone': '(GMT-06:00) Central Time (US & Canada)',
             'Country': 'US', 'Location': 'Dallas'}]
    bis = sources.portal_metadata(rows, 'ran2#135bis')
    assert (bis['timezone'], bis['starts_at'], bis['ends_on']) == ('Asia/Seoul', '2026-10-12T09:00:00+09:00', '2026-10-16')
    assert sources.portal_metadata(rows, 'ran2#132')['timezone'] == 'America/Chicago'
    assert sources.portal_metadata(rows, 'ran2#136') is None


def mock_server(files):
    """3GPP-like server: listings for folders, 403 for missing paths."""
    def handler(request):
        url = str(request.url)
        if url.endswith('/'):
            names = sorted({u[len(url):].split('/')[0] + ('/' if '/' in u[len(url):] else '')
                            for u in files if u.startswith(url) and u != url})
            if not names:
                return httpx.Response(403)
            return httpx.Response(200, text=listing(names, base=url))
        if url in files:
            return httpx.Response(200, content=files[url], headers={'etag': f'"{len(files[url])}"'})
        return httpx.Response(403)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_bundle_reads_the_upcoming_archive_folder_and_its_agenda(tmp_path, monkeypatch):
    monkeypatch.setattr(sources.remote_files, 'CACHE_DIR', tmp_path / 'cache')
    sync = 'https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN2/Agenda/'
    archive = 'https://www.3gpp.org/ftp/tsg_ran/WG2_RL2/'
    files = {sync + V11: (FIXTURES / V11).read_bytes(),
             archive + 'TSGR2_135bis/Agenda/' + BIS: (FIXTURES / BIS).read_bytes(),
             archive + 'TSGR2_135bis/Agenda/agenda.csv': (FIXTURES / 'agenda_135bis.csv').read_bytes(),
             archive + 'TSGR2_136/Invitation/x.doc': b'invitation'}
    rows = [{'Title': '3GPPRAN2#135-bis', 'TBId': 380, 'Id': 1, 'StartDate': '2026-10-12 09:00:00',
             'EndDate': '2026-10-16 17:30:00', 'StartTimeZone': '(GMT+09:00) Seoul', 'Country': 'KR'},
            {'Title': '3GPPRAN2#136', 'TBId': 380, 'Id': 2, 'StartDate': '2026-11-16 09:00:00',
             'EndDate': '2026-11-20 17:30:00', 'StartTimeZone': '(GMT-07:00) Mountain Time (US & Canada)',
             'Country': 'CA'}]
    with mock_server(files) as http:
        bundle = sources.fetch_bundle(sources.DEFAULTS, {}, http, rows=rows, today='2026-10-05',
                                      cache_dir=tmp_path / 'inputs')
    assert (bundle.manifest['meeting_id'], bundle.manifest['version']) == ('ran2#135bis', 0)
    assert bundle.schedule == files[archive + 'TSGR2_135bis/Agenda/' + BIS]
    assert bundle.manifest['agenda']['url'].endswith('TSGR2_135bis/Agenda/agenda.csv')
    assert bundle.manifest['portal_metadata']['timezone'] == 'Asia/Seoul'
    # A real access error is not mistaken for an empty future folder.
    with mock_server({}) as http, pytest.raises(httpx.HTTPStatusError):
        sources.fetch_bundle(sources.DEFAULTS, {}, http, rows=rows, today='2026-10-05', cache_dir=tmp_path / 'inputs')


def test_bundle_cache_rejects_corruption(tmp_path):
    bundle = sources.Bundle({'schedule': {'sha256': sources.digest(b'docx')}, 'agenda': None}, b'docx', b'')
    bundle.save(tmp_path)
    assert sources.Bundle.load(tmp_path).schedule == b'docx'
    (tmp_path / 'agenda.csv').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='agenda'):
        sources.Bundle.load(tmp_path)


def test_config_rejects_unknown_keys_and_bad_meetings(tmp_path, monkeypatch):
    path = tmp_path / 'config.json'
    monkeypatch.setattr(sources, 'CONFIG_PATH', path)
    path.write_text(json.dumps({'model': 'x'}))
    with pytest.raises(ValueError, match='Unknown'):
        sources.load_config()
    path.write_text(json.dumps({'meetings': {'135bis': {'city': 'Seoul'}}}))
    with pytest.raises(ValueError, match='override'):
        sources.load_config()
    path.write_text(json.dumps({'meetings': {'135bis': {'timezone': 'Asia/Seoul'}}}))
    assert sources.override_for(sources.load_config(), 'ran2#135bis') == {'timezone': 'Asia/Seoul'}


# ----------------------------------------------------------- pipeline and CI

@pytest.fixture
def offline_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = {**sources.DEFAULTS, 'local_agenda': str(FIXTURES / 'agenda_135.csv'),
           'meetings': {'135': {k: v for k, v in META_135.items()}}}
    monkeypatch.setattr(pipeline, 'load_config', lambda: cfg)
    monkeypatch.setattr(sources, 'load_config', lambda: cfg)
    monkeypatch.setattr(pipeline, 'portal_rows', lambda: pytest.fail('Portal queried offline'))
    monkeypatch.setattr(sources, 'portal_rows', lambda: [])
    return cfg


def test_local_build_needs_no_network_and_records_state(offline_config, tmp_path, monkeypatch):
    monkeypatch.setattr(httpx.Client, 'send', lambda *a, **k: pytest.fail('Offline network request'))
    options = SimpleNamespace(local=str(FIXTURES / V11), no_download=True, output_dir=tmp_path / 'out')
    schedule = pipeline.build_schedule(options)
    assert schedule.wg_id == 'ran2' and schedule.meeting_name == 'RAN2#135'
    assert schedule.source_files == [V11, 'agenda.csv']
    assert schedule.starts_at == '2026-08-24T09:00:00+02:00'
    state = sources.read_json(tmp_path / 'out/ran2/.schedule_state.json')
    assert state['meeting_id'] == 'ran2#135' and state['metadata']['timezone'] == 'Europe/Amsterdam'
    # The next offline build reuses the downloaded inputs.
    again = pipeline.build_schedule(SimpleNamespace(no_download=True, output_dir=tmp_path / 'out'))
    assert again.days == schedule.days


def test_a_new_meeting_without_dates_fails_clearly(offline_config, tmp_path, monkeypatch):
    offline_config['meetings'] = {}
    monkeypatch.setattr(pipeline, 'portal_rows', lambda: None)
    with pytest.raises(ValueError, match='Add them under "meetings"'):
        pipeline.build_schedule(SimpleNamespace(local=str(FIXTURES / V11), output_dir=tmp_path / 'out'))
    assert not (tmp_path / 'out/ran2/.schedule_state.json').exists()


def test_override_dates_drop_stale_portal_instants():
    cfg = {**sources.DEFAULTS, 'meetings': {'135': {'starts_on': '2026-08-25'}}}
    live = {**META_135}
    result = pipeline.meeting_metadata('ran2#135', cfg, {}, live)
    assert result['starts_on'] == '2026-08-25' and 'starts_at' not in result and result['ends_at']


def test_lifecycle_check_stages_without_advancing_state(offline_config, tmp_path, monkeypatch):
    bundle = sources.local_bundle(FIXTURES / V11, offline_config)
    monkeypatch.setattr(sources, 'fetch_bundle', lambda *a, **k: bundle)
    before = {'meeting_id': 'ran2#134'}
    sources.write_json(sources.OUTPUT / '.schedule_state.json', before)
    result = lifecycle.check_updates()
    assert result.changed and 'meeting_id changed' in result.reasons and not result.errors
    assert sources.read_json(sources.OUTPUT / '.schedule_state.json') == before
    lifecycle.prepare_build()
    assert sources.Bundle.load(sources.DOWNLOADS / 'prepared').schedule == bundle.schedule
    sources.write_json(sources.OUTPUT / '.schedule_state.json', {**bundle.manifest, 'metadata': META_135})
    assert not lifecycle.check_updates().changed
    monkeypatch.setattr(sources, 'fetch_bundle', lambda *a, **k: (_ for _ in ()).throw(ValueError('down')))
    assert lifecycle.check_updates().errors == ['RAN2 source check failed: down']


def test_prepared_bundle_is_consumed_once_and_checked_against_config(offline_config, tmp_path):
    bundle = sources.local_bundle(FIXTURES / V11, offline_config)
    bundle.manifest['config_hash'] = 'stale'
    bundle.save(sources.DOWNLOADS / 'prepared')
    with pytest.raises(ValueError, match='Prepared inputs'):
        pipeline.build_schedule(SimpleNamespace(output_dir=tmp_path / 'out'))
    assert not (sources.DOWNLOADS / 'prepared').exists()


def test_reset_keeps_the_last_successful_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sources.write_json(sources.OUTPUT / '.schedule_state.json', {'meeting_id': 'ran2#135'})
    sources.write_json(sources.DOWNLOADS / 'inputs/manifest.json', {})
    lifecycle.reset_cache()
    assert (sources.OUTPUT / '.schedule_state.json').exists() and not sources.DOWNLOADS.exists()


def test_lifecycle_import_is_isolated():
    result = subprocess.run([sys.executable, '-c', """
import sys
from working_groups.registry import get_working_group
get_working_group('ran2')
assert not any(name.startswith(('working_groups.ran1', 'working_groups.ran_plenary', 'google.genai', 'docx'))
               for name in sys.modules)
"""], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
