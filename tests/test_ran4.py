"""RAN4 meeting schedules: real chair PPTX files plus the rules they rely on."""
from pathlib import Path

import pytest

from shared.agenda import agenda_map
from working_groups.ran4 import document as docmod, topics as topicmod

FIXTURES = Path(__file__).parent / 'fixtures/ran4'
THU = 'RAN4_120bis_meeting_schedule_THU_afternoon.pptx'
V11_119 = 'RAN4_119_meeting_schedule_v11.pptx'
TOPICS = 'List_Topics_moderators-RAN4_120bis_final.xlsx'


def extract(name):
    return docmod.extract_document((FIXTURES / name).read_bytes(), name)


def agenda():
    return agenda_map((FIXTURES / 'agenda_120bis.csv').read_bytes())


def day_of(document, name):
    return next(d for d in document['days'] if d['day'] == name)


def cell_at(day, room, start):
    return next(c for c in day['cells'] if c['rooms'][0] == room and c['start'] == start)


# ------------------------------------------------------------------ document

@pytest.mark.parametrize('name, expected', [
    ('RAN4_120bis_meeting_schedule_THU_afternoon.pptx', ('ran4#120bis', 'THU_afternoon')),
    ('RAN4_118bis_meeting_schedule_v11.pptx', ('ran4#118bis', 'v11')),
    ('RAN4_120_meeting_schedule_after Thursday.pptx', ('ran4#120', 'after Thursday')),
    ('RAN4_121_meeting_schedule.pptx', ('ran4#121', '')),
    ('~$RAN4_118bis_meeting_schedule_v11.pptx', None),
    ('List_Topics_moderators-RAN4_120bis_final.xlsx', None),
])
def test_schedule_filenames(name, expected):
    assert docmod.file_info(name) == expected


def test_weekday_slides_session_columns_and_break_rows():
    document = extract(THU)
    assert document['meeting_id'] == 'ran4#120bis'
    assert (document['revision'], document['modified']) == (4412, '2026-10-15T13:14:01Z')
    assert [d['day'] for d in document['days']] == ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    monday = day_of(document, 'Monday')
    # Columns are sessions named in the header, not rooms.
    assert [(r['id'], r['session']) for r in monday['rooms']] == [
        ('yang', 'Yang'), ('shan', 'Shan'), ('gene', 'Gene'), ('ad-hoc-room', None), ('offline-room', None)]
    assert monday['rooms'][1]['name'] == 'RAN4 Breakout 1(BR1) (Shan Session)'
    # Breaks are the rows whose time cell is filled FFC000, named in the venue's language.
    assert [(b['name'], b['start'], b['end']) for b in monday['breaks']] == [
        ('커피 타임', '10:30', '11:00'), ('점심시간', '13:00', '14:30'), ('커피 타임', '16:30', '17:00')]
    friday = day_of(document, 'Friday')
    assert [(s['start'], s['end'], s['eom']) for s in friday['slots']][-1] == ('14:00', None, True)
    # The note under Friday's table is kept for the page.
    assert {'type': 'heading', 'text': 'Friday'} in document['supplements']
    assert any('additional morning AH sessions' in b.get('text', '') for b in document['supplements'])


def test_cells_keep_blank_lines_and_drop_struck_text():
    document = extract(THU)
    thursday = day_of(document, 'Thursday')
    shan = cell_at(thursday, 'shan', '08:30')
    assert shan['lines'] == ['Rel-20 RRM', '[203] NR_LBCA_Sw_enh_RRM', '',
                             'R4-2621419 (Reply to RAN1 LS on scheduling restriction for RACH-related signals and channels)',
                             '[208] NR_NTN_GNSS_resilient_RRM']
    # The opening spans four columns.
    opening = cell_at(day_of(document, 'Monday'), 'yang', '09:00')
    assert opening['rooms'] == ['yang', 'shan', 'gene', 'ad-hoc-room']


def test_struck_paragraphs_are_deleted_and_blank_ones_kept():
    xml = ('<a:tc xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:txBody>'
           '<a:p><a:r><a:rPr strike="sngStrike"/><a:t>BR2 Ad-hoc: 6G Sensing feasibility</a:t></a:r></a:p>'
           '<a:p><a:r><a:rPr strike="noStrike"/><a:t>[362]  6G_Sensing</a:t></a:r>'
           '<a:r><a:rPr strike="sngStrike"/><a:t> (moved)</a:t></a:r></a:p>'
           '<a:p/><a:p/><a:p><a:r><a:t>[363] A</a:t></a:r></a:p><a:p/></a:txBody></a:tc>')
    from defusedxml import ElementTree
    assert docmod.paragraph_lines(ElementTree.fromstring(xml)) == ['[362] 6G_Sensing', '', '[363] A']


def test_rows_with_extra_words_and_a_dinner_row():
    document = extract(V11_119)
    wednesday = day_of(document, 'Wednesday')
    assert ('RAN4 Dinner and Social Event', '19:30', '22:00') in [
        (b['name'], b['start'], b['end']) for b in wednesday['breaks']]
    # An unnamed column keeps its place.
    assert [r['id'] for r in day_of(document, 'Tuesday')['rooms']][-1] == 'column-5'
    assert docmod.read_slot('15:00 (or earlier time) -16:00') == ('15:00', '16:00', False, '(or earlier time)')
    assert docmod.read_slot('14:00-EOM') == ('14:00', None, True, '')


def test_a_file_that_is_not_a_schedule_is_a_layout_error():
    with pytest.raises(docmod.LayoutError):
        docmod.extract_document(b'not a zip', 'RAN4_121_meeting_schedule_v01.pptx')


# -------------------------------------------------------------------- topics

def test_topic_list_maps_topics_to_listed_agenda_items():
    topics = topicmod.read_topics((FIXTURES / TOPICS).read_bytes(), agenda())
    assert topics['101-A'] == {'title': '6G system parameter (part I)', 'agenda_items': ['8.2.1', '8.2.2', '8.2.7']}
    assert topics['211']['agenda_items'] == ['7.9']
    assert topics['105-A']['agenda_items'] == ['8.6', '8.6.2.1', '8.6.2.2', '8.6.3']
    # Every agenda item kept is one the meeting's agenda.csv lists.
    assert all(item in agenda() for t in topics.values() for item in t['agenda_items'])
    assert topicmod.expand('105', topics) == ['105-A', '105-B']
    assert topicmod.expand('102-A/B', topics) == ['102-A', '102-B']
    assert topicmod.expand('999', topics) == []


# ------------------------------------------------------------------ sessions

import io
import re
import subprocess
import sys
import zipfile
from types import SimpleNamespace

import httpx

from shared import officials
from shared.renderer import generate_html
from shared.schedule import load_schedule, save_schedule
from working_groups.ran4 import lifecycle, llm, pipeline, sessions, sources

META = {'starts_on': '2026-10-12', 'ends_on': '2026-10-16', 'timezone': 'Asia/Seoul',
        'starts_at': '2026-10-12T09:00:00+09:00', 'ends_at': '2026-10-16T17:30:00+09:00'}
CHAIRS = {'yang': 'Yang TANG', 'shan': 'Shan YANG', 'gene': 'Gene FONG'}


def topic_list():
    return topicmod.read_topics((FIXTURES / TOPICS).read_bytes(), agenda())


def context():
    return {'agenda': agenda(), 'topics': topic_list(), 'column_chairs': CHAIRS}


@pytest.fixture(scope='module')
def week():
    schedule, warnings = sessions.make_schedule(extract(THU), agenda(), topic_list(), META, [THU], {}, 'now',
                                                column_chairs=CHAIRS)
    return schedule, warnings


def block(schedule, day, start, end):
    day = next(d for d in schedule.days if d.day_name == day)
    return [(s.room_ids[0], s.start_time, s.end_time, s.part, s.name, s.agenda_item, s.chair, s.notes)
            for s in day.sessions if start <= s.start_time < end]


def test_monday_matches_the_report_grid(week):
    rows = block(week[0], 'Monday', '11:00', '13:00')
    assert ('yang', '11:00', '13:00', None, '[101-A] System Parameter', '8.2.1, 8.2.2, 8.2.7', 'Yang TANG',
            ['[101-A] System Parameter']) in rows
    # A category over one topic is the title; the topic is a note.
    assert ('shan', '11:00', '13:00', None, 'Rel-20 Demod', '7.9', 'Shan YANG',
            ['Rel-20 Demod', '[211] FS_NR_demod_SCM']) in rows
    # Two topics written without a blank line are one cell; its AI line is their union.
    gene = next(r for r in rows if r[0] == 'gene')
    assert gene[3:5] == (None, 'NTN') and gene[5].startswith('7.6.2') and gene[5].endswith('7.13.1, 4.6.1')
    # "—12:00—" splits the ad hoc column; a separate owner line joins the title.
    assert [(r[1], r[2], r[4], r[6]) for r in rows if r[0] == 'ad-hoc-room'] == [
        ('11:00', '12:00', 'Main Ad-hoc / [105-A] 6G RRM', 'Jerry'), ('12:00', '13:00', '[105-B] 6G RRM', 'Jin-Yup')]


def test_tuesday_written_times_and_a_session_in_the_break(week):
    rows = block(week[0], 'Tuesday', '14:30', '17:00')
    assert [(r[1], r[2], r[4]) for r in rows if r[0] == 'yang'] == [
        ('14:30', '16:00', '[104] Spectrum'), ('16:00', '16:30', '[111] 6G RAN4 operation efficiency'),
        ('16:30', '17:00', 'Coffee break AH: 105-B 6G RRM')]
    coffee = next(r for r in rows if r[1] == '16:30')
    assert (coffee[5], coffee[6]) == ('8.6.1, 8.6.4, 8.6.5', 'Jinyup')   # its own chair, not the column's
    # A timed part does not pass its heading to the next one.
    assert [(r[1], r[4]) for r in rows if r[0] == 'shan'] == [('14:30', 'Rel-20 RRM'), ('15:30', '[202] NR_UAV_req_RRM')]
    # An owner on the same line as the name stays in the title; its time bracket does not.
    assert [r[4] for r in rows if r[0] == 'ad-hoc-room'] == [
        'BR2 Ad-hoc: Ambient IoT D2T2', 'BR2 Ad-hoc: NTN HD-FDD and DL CA RRM']
    tuesday = next(d for d in week[0].days if d.day_name == 'Tuesday')
    assert {'name': '커피 타임', 'start': '16:30', 'end': '17:00', 'label_position': 'time-axis'} in tuesday.timeline.breaks


def test_thursday_blank_lines_split_but_lists_do_not(week):
    rows = block(week[0], 'Thursday', '08:30', '11:00')
    shan = [r for r in rows if r[0] == 'shan']
    assert [(r[3], r[4], r[5]) for r in shan] == [
        ([1, 2], 'Rel-20 RRM', '7.8.4'),
        ([2, 2], 'R4-2621419 / [208] NR_NTN_GNSS_resilient_RRM', '7.18.2, 7.18.4')]
    yang = next(r for r in rows if r[0] == 'yang')
    assert yang[3:5] == (None, '[112] TEI20 / [113] R18 maintenance: PC2 FDD HPUE duty cycle / EARLY RETURN TO')
    gene = [r for r in rows if r[0] == 'gene']
    assert [(r[1], r[4]) for r in gene] == [('08:30', '5G basket and spectrum WI'),
                                            ('10:30', 'Coffee break offline discussion on 1900 MHz TN bands')]
    assert len(gene[0][7]) == 10
    adhoc = next(r for r in rows if r[0] == 'ad-hoc-room')
    assert (adhoc[4], adhoc[6]) == ('BR1 Ad-hoc / Demod', None)   # no chair written: none shown


def test_the_seven_blocks_the_source_splits(week):
    split = sorted({(s.day, s.room_ids[0], s.start_time) for d in week[0].days for s in d.sessions if s.part})
    assert split == [('Friday', 'yang', '11:00'), ('Thursday', 'shan', '08:30'), ('Thursday', 'yang', '14:30'),
                     ('Tuesday', 'gene', '08:30'), ('Tuesday', 'gene', '11:00'), ('Wednesday', 'gene', '08:30'),
                     ('Wednesday', 'gene', '11:00')]
    friday = block(week[0], 'Friday', '11:00', '12:45')
    assert [(r[3], r[4]) for r in friday if r[0] == 'yang'] == [
        ([1, 2], '6G operation efficiency'), ([2, 2], '6G RRM / 13 Any other business / 14 Close of the meeting')]


def test_typos_in_topic_numbers_leave_the_agenda_items_out(week):
    schedule, warnings = week
    assert any("'[206] NR_Mob_Ph5_RRM" in w and 'topic 207' in w for w in warnings)
    assert any('[102-A/B] System Parameter' in w and 'topic 101-A' in w for w in warnings)
    typo = next(s for d in schedule.days if d.day_name == 'Thursday' for s in d.sessions
                if s.name.startswith('[206]'))
    assert typo.agenda_item is None and typo.chair == 'Hua Li'


def test_eom_ends_at_the_portal_end_and_colours_follow_the_top_agenda_item(week):
    friday = next(d for d in week[0].days if d.day_name == 'Friday')
    assert {s.end_time for s in friday.sessions if s.start_time == '14:00'} == {'17:30'}
    groups = {s.group_header for d in week[0].days for s in d.sessions}
    assert {'6G study item [FS_6G_Radio]', 'TEI20'} <= groups
    late, warnings = sessions.make_schedule(extract(THU), agenda(), topic_list(), {**META, 'ends_at': None},
                                            [THU], {}, 'now', column_chairs=CHAIRS)
    assert any('EOM row' in w for w in warnings)


def synthetic(lines, start='08:30', end='10:30', room='gene', breaks=()):
    cell = {'id': 'c', 'rooms': [room], 'start': start, 'end': end, 'eom': False, 'row_extra': '', 'lines': lines}
    day = {'day': 'Monday', 'rooms': [], 'breaks': list(breaks), 'cells': [cell]}
    found, reasons = sessions.read_cell(cell, day, {'agenda': {}, 'topics': {}, 'column_chairs': CHAIRS})
    return [(s['start'], s['end'], s['part'], s['name'], s['chair']) for s in found], reasons


def test_written_times_split_a_cell():
    assert synthetic(['[101] A', '---3:00pm', '[102] B'], '14:30', '16:30')[0] == [
        ('14:30', '15:00', None, '[101] A', 'Gene FONG'), ('15:00', '16:30', None, '[102] B', 'Gene FONG')]
    # After a part with its own end, a blank line continues from that end.
    rows, _ = synthetic(['BR1 Ad-hoc:(8:30-9:30)', '[211] X, Chaired by Axel Mueller (Nokia)', '',
                         'BR2 Ad-hoc: NTN', '[323] Y, chaired by Daniel (Apple)'], room='ad-hoc-room')
    assert [(r[0], r[1], r[3], r[4]) for r in rows] == [('08:30', '09:30', 'BR1 Ad-hoc / [211] X', 'Axel Mueller'),
                                                        ('09:30', '10:30', 'BR2 Ad-hoc: NTN', 'Daniel')]
    assert synthetic(['Main Ad-hoc: (until 11:00)', '[111] Z by Aijun (CATT)'], '09:30', '10:30',
                     room='ad-hoc-room')[0] == [('09:30', '11:00', None, 'Main Ad-hoc / [111] Z', 'Aijun')]
    assert synthetic(['Return to (start at 8:30)', 'NTN [346], [347]'], '08:00')[0] == [
        ('08:30', '10:30', None, 'Return to', 'Gene FONG')]


def test_blank_line_groups_and_what_keeps_them_together():
    # Pieces of a block, a heading alone joining what it heads, an annotation its group.
    rows, _ = synthetic(['Maintenance', '', '[301] A', '[302] B', '(f time available)', '', 'NTN', '[346] C'])
    assert [(r[2], r[3]) for r in rows] == [([1, 2], 'Maintenance'), ([2, 2], 'NTN')]
    # "(cont)" is a part of its own; its title loses the bracket as RAN2's does.
    rows, _ = synthetic(['5G UE RF (cont)', '', 'NTN', '[346] C'], '11:00', '13:00')
    assert [(r[2], r[3]) for r in rows] == [([1, 2], '5G UE RF'), ([2, 2], 'NTN')]
    # A list of fallbacks is one cell across blank lines.
    rows, _ = synthetic(['Return to', '6G system parameter', '', '6G UE RF'])
    assert [(r[2], r[3]) for r in rows] == [(None, 'Return to')]
    # Pieces shorter than 30 minutes are not drawn: one cell instead.
    rows, _ = synthetic(['NTN', '[346] C', '', 'UAV', '[352] D', '', 'BSRF', '[345] E'], '17:00', '18:00')
    assert [(r[2], r[3]) for r in rows] == [(None, 'NTN / UAV / BSRF')]


def test_a_session_written_for_the_break_is_held_in_the_break():
    rows, reasons = synthetic(['[340] A', '', 'COEFEE BREAK AH[102-B] 6G RF, Chaired by Haijie (Xiaomi)'],
                              breaks=[{'name': 'Coffee', 'start': '10:30', 'end': '11:00'}])
    assert rows[-1] == ('10:30', '11:00', None, 'COEFEE BREAK AH[102-B] 6G RF', 'Haijie') and not reasons
    rows, reasons = synthetic(['[340] A', 'Coffee break AH: 105-B'], end='12:00', breaks=[])
    assert len(rows) == 1 and reasons == ["no break after the block for 'Coffee break AH: 105-B'"]


def test_cells_the_rules_are_unsure_about_say_why():
    def reasons(lines, **kwargs):
        return synthetic(lines, **kwargs)[1]
    assert reasons(['6G RRM (not earlier than 3pm)'], start='14:30', end='16:30') == [
        "time written inside a line: '6G RRM (not earlier than 3pm)'"]
    assert reasons(['14 Close of the meeting by 13:30pm'], start='11:00', end='12:45')
    assert reasons(['[105] AI general part charied by Jackson (Samsung)']) == [
        "chair not read: '[105] AI general part charied by Jackson (Samsung)'"]
    assert reasons(['---TBD---', '[101] A'])[0] == "unreadable time marker: '---TBD---'"
    # The time a written part takes leaves none for the next (#119 Wed BR2).
    assert reasons(['5G BSRF (9:00 – 10:30)', '[345] B', '', 'Ambient IoT', '[352] C'],
                   start='08:30') == ["no time left for 'Ambient IoT'"]
    # What the rules read is no reason.
    assert reasons(['BR2 Ad-hoc: 5G BSRF (14:30 – 15:30)', '[345] X', '', 'Coffee break offline'],
                   start='14:30', end='16:30') == ["no break after the block for 'Coffee break offline'"]


def test_titles_drop_round_brackets_and_chairs_as_ran2_does():
    assert sessions.clean('NTN (cont)') == 'NTN'
    assert sessions.clean('R4-2621419 (Reply to RAN1 LS on scheduling)') == 'R4-2621419'
    assert sessions.clean('[346] NR_IoT_NTN_Ph2 (40)') == '[346] NR_IoT_NTN_Ph2'
    assert sessions.clean('(e)RedCap bands') == '(e)RedCap bands'
    assert sessions.written_chairs('[102-A/B] System Parameter, Chaired by Leo (Huawei)/Jinqiang (OPPO)') == (
        ['Leo', 'Jinqiang'], '[102-A/B] System Parameter')
    assert sessions.written_chairs('Chaired by Ling (Huawei) and/or Huiping (Xiaomi)')[0] == ['Ling', 'Huiping']
    assert sessions.written_chairs('[362] 6G_Sensing coex, req, test, chaired by Fei (ZTE(')[0] == ['Fei']
    assert sessions.written_chairs('14 Close of the meeting by 13:30pm') == ([], '14 Close of the meeting by 13:30pm')
    assert sessions.topic_numbers(['Rel-20 NTN (346, 347)', '[214] [215] X', 'AH: 105-B 6G RRM', '[102-A/B] Y',
                                   '[345] NR_BS_RF_Ph2 (15)']) == ['346', '347', '214', '215', '105-B', '102-A/B', '345']


def test_older_meeting_pm_markers_and_bracketed_topic_lists():
    schedule, _ = sessions.make_schedule(extract(V11_119), agenda(), topic_list(),
                                         {'starts_on': '2026-05-18', 'ends_on': '2026-05-22', 'timezone': 'Asia/Shanghai',
                                          'ends_at': '2026-05-22T17:30:00+08:00'}, [V11_119], {}, 'now',
                                         column_chairs=CHAIRS)
    monday = block(schedule, 'Monday', '14:30', '16:30')
    assert [(r[1], r[2]) for r in monday if r[0] == 'yang'] == [('14:30', '15:30'), ('15:30', '16:30')]
    friday = next(d for d in schedule.days if d.day_name == 'Friday')
    listed = next(s for s in friday.sessions if s.room_ids == ['gene'] and s.start_time == '08:30')
    # "(346, 347, …)" under "Return to" give agenda items, for the filter only.
    assert listed.agenda_item is None and '7.6.2' in listed.inferred_agenda_items
    # "Maintenance" over six topics is what the session takes up, not a list.
    thursday = next(d for d in schedule.days if d.day_name == 'Thursday')
    maintenance = next(s for s in thursday.sessions if s.room_ids == ['yang'] and s.start_time == '11:00')
    assert maintenance.name == 'Maintenance' and maintenance.inferred_agenda_items == []
    assert len(maintenance.notes) == 7
    # With its topics in the topic list, their AIs are the cell's AI line (the #120bis list lacks them).
    topics = {f'{n}': {'title': f'T{n}', 'agenda_items': [f'4.{i}']} for i, n in enumerate(range(113, 119))}
    cell = {'id': 'c', 'rooms': ['yang'], 'start': '11:00', 'end': '13:00', 'lines': maintenance.notes[:7]}
    day = {'day': 'Thursday', 'rooms': [], 'breaks': [], 'cells': [cell]}
    found, _ = sessions.read_cell(cell, day, {'agenda': {}, 'topics': topics, 'column_chairs': {}})
    assert found[0]['agenda_items'] == ['4.0', '4.1', '4.2', '4.3', '4.4'] and found[0]['candidate_items'] == []


def test_candidate_lists_match_the_filter_but_show_no_agenda_item(week):
    def at(day, room, start):
        return next(s for d in week[0].days if d.day_name == day for s in d.sessions
                    if s.room_ids == [room] and s.start_time == start)
    for day, start, count in (('Friday', '08:30', 73), ('Friday', '11:00', 26), ('Thursday', '08:30', 47)):
        session = at(day, 'gene', start)
        # No AI line, no AI tree in the popup, no colour; the topic lines stay as notes.
        assert (session.agenda_item, session.group_header, session.agenda_descriptions) == (None, '', [])
        assert len(session.inferred_agenda_items) == count and session.notes
    assert '5.2.1' in at('Friday', 'gene', '08:30').inferred_agenda_items
    # A list under the cell's own topics leaves their agenda items in place.
    tei = at('Thursday', 'yang', '08:30')
    assert (tei.agenda_item, tei.group_header, tei.inferred_agenda_items) == ('4.2, 10.2.1', 'TEI20', [])
    # A category over a few topics is the session's own.
    assert at('Monday', 'gene', '11:00').agenda_item.startswith('7.6.2')


def test_which_lines_are_a_list():
    five = ['[320] A', '[321] B', '[322] C', '[323] D', '[324] E']
    assert sessions.listed(['5G basket and spectrum WI'] + five) == [True] * 6
    assert sessions.listed(['5G Basket and spectrum (cont)', '[326] F']) == [True, True]
    # Topics a session takes up, however many, are its own; so is an ad hoc on basket items.
    assert sessions.listed(['Maintenance'] + five) == [False] * 6
    assert sessions.listed(['BR2 Ad-hoc: Spectrum basket work items', '[324] X, chaired by Y (Z)']) == [False, False]
    assert sessions.listed(['[112] TEI20', 'EARLY RETURN TO', '6G UE RF']) == [False, True, True]
    assert sessions.listed(['BDaT Early Return To', 'NTN']) == [True, True]
    # Only the basket's own lines: a topic after it is the session's.
    assert sessions.listed(['5G basket'] + five + ['', 'NTN', '[346] F']) == [True] * 6 + [False] * 3


# ----------------------------------------------------------------------- llm

def doubtful_item(lines, start='14:30', end='16:30', breaks=()):
    cell = {'id': 'c', 'rooms': ['yang'], 'start': start, 'end': end, 'lines': lines}
    return {'cell': cell, 'day': {'day': 'Thursday', 'rooms': [], 'breaks': list(breaks)}, 'break': None,
            'reasons': ['x'], 'rules': []}


def answer(*sessions_):
    return llm.CellAnswer(id='c', sessions=[llm.CellSession(**s) for s in sessions_])


def test_llm_answers_are_checked_against_the_cell():
    item = doubtful_item(['6G spectrum', '', '6G RRM (not earlier than 3pm)', '6G AI'])
    good = answer({'lines': [0], 'start': '14:30', 'end': '15:00', 'title': '6G spectrum'},
                  {'lines': [2, 3], 'start': '15:00', 'end': '16:30', 'title': '6G RRM / 6G AI'})
    assert llm.check_cell(good, item) == []
    pieces = answer({'lines': [0], 'start': '14:30', 'end': '16:30', 'part': [1, 2], 'title': '6G spectrum'},
                    {'lines': [2, 3], 'start': '14:30', 'end': '16:30', 'part': [2, 2], 'title': '6G RRM'})
    assert llm.check_cell(pieces, item) == []
    invented = answer({'lines': [0], 'start': '14:30', 'end': '15:30', 'title': '6G spectrum'},
                      {'lines': [2, 3], 'start': '15:30', 'end': '16:30', 'title': '6G RRM'})
    assert any('15:30 is not written' in e for e in llm.check_cell(invented, item))
    across = answer({'lines': [0, 2], 'start': '14:30', 'end': '16:30', 'part': [1, 2], 'title': '6G spectrum'},
                    {'lines': [3], 'start': '14:30', 'end': '16:30', 'part': [2, 2], 'title': '6G AI'})
    assert any('across a blank line' in e for e in llm.check_cell(across, item))
    renamed = answer({'lines': [0, 2, 3], 'start': '14:30', 'end': '16:30', 'title': 'Spectrum and RRM',
                      'chair': 'Yang', 'topics': ['107-B']})
    errors = llm.check_cell(renamed, item)
    assert any('title words' in e for e in errors) and any("'Yang'" in e for e in errors)
    assert any("'107-B'" in e for e in errors)


def test_checked_answers_replace_the_rules_and_failures_keep_them(tmp_path):
    document = extract(THU)
    target = cell_at(day_of(document, 'Thursday'), 'yang', '14:30')['id']
    calls = []

    def request(message, schema, model, thinking='medium'):
        calls.append(message)
        cells = []
        for item in json_cells(message):
            if item['id'] != target:   # Thursday Main 14:30: "(not earlier than 3pm)"
                continue
            cells.append({'id': item['id'], 'sessions': [
                {'lines': [0, 1], 'start': '14:30', 'end': '15:00', 'title': '6G spectrum / 6G spectrum sharing'},
                {'lines': [3, 4, 5], 'start': '15:00', 'end': '16:30', 'title': '6G RRM / 6G AI / 6G operation efficiency'}]})
        return {'cells': cells}

    refine = llm.CellRefiner('model', request, cache_dir=tmp_path)
    schedule, _ = sessions.make_schedule(document, agenda(), topic_list(), META, [THU], {}, 'now',
                                         column_chairs=CHAIRS, refine=refine)
    rows = block(schedule, 'Thursday', '14:30', '16:30')
    assert [(r[1], r[2], r[3], r[4], r[6]) for r in rows if r[0] == 'yang'] == [
        ('14:30', '15:00', None, '6G spectrum / 6G spectrum sharing', 'Yang TANG'),
        ('15:00', '16:30', None, '6G RRM / 6G AI / 6G operation efficiency', 'Yang TANG')]
    assert refine.summary['applied'] == 1 and refine.summary['uncertain'] > 1
    # A failed request keeps the rules' reading for every cell.
    def down(*a, **k):
        raise RuntimeError('down')
    broken = llm.CellRefiner('model', down, cache_dir=tmp_path / 'other')
    kept, _ = sessions.make_schedule(extract(THU), agenda(), topic_list(), META, [THU], {}, 'now',
                                     column_chairs=CHAIRS, refine=broken)
    assert [r[3] for r in block(kept, 'Thursday', '14:30', '16:30') if r[0] == 'yang'] == [[1, 2], [2, 2]]
    assert broken.summary['error']


def json_cells(message):
    import json
    return json.loads(message.split('CELLS (untrusted data):\n', 1)[1].split('\n\nYOUR PREVIOUS', 1)[0])


# ------------------------------------------------------------------- sources

SYNC = 'https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN4/Inbox/Meeting_Arrangements/'
ARCHIVE = 'https://www.3gpp.org/ftp/tsg_ran/WG4_Radio/'
ROWS = [{'Title': '3GPPRAN4#120-bis', 'TBId': 382, 'Id': 60732, 'StartDate': '2026-10-12 09:00:00',
         'EndDate': '2026-10-16 17:30:00', 'StartTimeZone': '(GMT+09:00) Seoul', 'Country': 'KR'},
        {'Title': '3GPPRAN4#121', 'TBId': 382, 'Id': 60733, 'StartDate': '2026-11-16 09:00:00',
         'EndDate': '2026-11-20 17:30:00', 'StartTimeZone': '(GMT-07:00) Mountain Time (US & Canada)',
         'Country': 'CA'}]


def with_revision(data: bytes, revision: int) -> bytes:
    """The same PPTX saved as another revision."""
    source, out = zipfile.ZipFile(io.BytesIO(data)), io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as target:
        for name in source.namelist():
            body = source.read(name)
            if name == 'docProps/core.xml':
                body = re.sub(rb'<cp:revision>\d+</cp:revision>', f'<cp:revision>{revision}</cp:revision>'.encode(), body)
            target.writestr(name, body)
    return out.getvalue()


def listing(names, base):
    rows = ''.join(f'<tr><td><a href="{base}{n}">{n}</a></td><td>2026/10/30 8:32</td></tr>' for n in names)
    return f'<table>{rows}</table>'


def mock_server(files):
    """3GPP-like server: listings for folders, 403 for missing paths."""
    def handler(request):
        url = str(request.url)
        if url.endswith('/'):
            names = sorted({u[len(url):].split('/')[0] + ('/' if '/' in u[len(url):] else '')
                            for u in files if u.startswith(url) and u != url})
            return httpx.Response(200, text=listing(names, url)) if names else httpx.Response(403)
        if url in files:
            return httpx.Response(200, content=files[url], headers={'etag': f'"{len(files[url])}"'})
        return httpx.Response(403)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_versions_are_ordered_by_revision_not_by_name(tmp_path, monkeypatch):
    monkeypatch.setattr(sources.remote_files, 'CACHE_DIR', tmp_path / 'cache')
    data = (FIXTURES / THU).read_bytes()
    folder = ARCHIVE + 'TSGR4_120bis/Inbox/Meeting_Arrangements/'
    files = {folder + 'RAN4_120bis_meeting_schedule_v04.pptx': with_revision(data, 4371),
             folder + 'RAN4_120bis_meeting_schedule_THU_morning.pptx': with_revision(data, 4405),
             folder + 'RAN4_120bis_meeting_schedule_THU_afternoon.pptx': data,
             folder + '~$RAN4_120bis_meeting_schedule_THU_afternoon.pptx': b'lock',
             folder + TOPICS: (FIXTURES / TOPICS).read_bytes(),
             folder + 'List_Topics_moderators-RAN4_120bis_v1.xlsx': b'older',
             ARCHIVE + 'TSGR4_120bis/Agenda/agenda.csv': (FIXTURES / 'agenda_120bis.csv').read_bytes(),
             ARCHIVE + 'TSGR4_121/Invitation/x.doc': b'invitation'}
    with mock_server(files) as http:
        bundle = sources.fetch_bundle(sources.DEFAULTS, {}, http, rows=ROWS, today='2026-10-30',
                                      cache_dir=tmp_path / 'inputs')
    manifest = bundle.manifest
    assert (manifest['meeting_id'], manifest['version']) == ('ran4#120bis', 4412)
    assert manifest['schedule']['name'] == 'RAN4_120bis_meeting_schedule_THU_afternoon.pptx'
    assert manifest['topics']['name'] == TOPICS and manifest['agenda']['name'] == 'agenda.csv'
    assert manifest['portal_metadata']['timezone'] == 'Asia/Seoul'
    # The sync copy of a newer save wins during the meeting.
    sources.remote_files.new_run()
    files[SYNC + 'RAN4_120bis_meeting_schedule_FRI.pptx'] = with_revision(data, 4420)
    with mock_server(files) as http:
        bundle = sources.fetch_bundle(sources.DEFAULTS, {}, http, rows=ROWS, today='2026-10-16',
                                      cache_dir=tmp_path / 'inputs')
    assert bundle.manifest['schedule']['url'] == SYNC + 'RAN4_120bis_meeting_schedule_FRI.pptx'
    assert bundle.manifest['version'] == 4420


def test_nothing_to_build_from_is_said_clearly(tmp_path, monkeypatch):
    monkeypatch.setattr(sources.remote_files, 'CACHE_DIR', tmp_path / 'cache')
    with mock_server({ARCHIVE + 'TSGR4_121/Invitation/x.doc': b'x'}) as http, \
            pytest.raises(ValueError, match='No RAN4 meeting schedule'):
        sources.fetch_bundle(sources.DEFAULTS, {}, http, rows=ROWS, today='2026-10-30', cache_dir=tmp_path / 'inputs')


def test_topic_list_names_and_ranks():
    entries = [{'name': n, 'url': 'https://x/' + n, 'uploaded_at': ''} for n in (
        'List_Topics_moderators-RAN4_120bis_final.xlsx', 'List_Topics_moderators-RAN4_120bis_v3.xlsx',
        'RAN4_120 list of topics 6G FL and 5G moderators v1.xlsx', 'R4-2620002_RAN4#120bis meeting arrangements.pptx')]
    found = sources.topic_candidates(entries)
    assert [(c['name'][:22], c['meeting_id']) for c in found] == [
        ('List_Topics_moderators', 'ran4#120bis'), ('List_Topics_moderators', 'ran4#120bis'),
        ('RAN4_120 list of topic', 'ran4#120')]
    assert max(found[:2], key=sources.topic_rank)['name'].endswith('_final.xlsx')


def test_config_rejects_unknown_keys(tmp_path, monkeypatch):
    path = tmp_path / 'config.json'
    monkeypatch.setattr(sources, 'CONFIG_PATH', path)
    path.write_text('{"unknown": 1}')
    with pytest.raises(ValueError, match='Unknown RAN4 configuration'):
        sources.load_config()
    path.write_text('{"meetings": {"x": {}}}')
    with pytest.raises(ValueError, match='Invalid RAN4 meeting override'):
        sources.load_config()
    path.write_text('{}')
    assert sources.load_config()['officials'] == 'R4'


# ------------------------------------------------------------------ pipeline

HISTORY = officials.parse_history((FIXTURES / 'R4_officialsHistory.htm').read_text(encoding='utf-8'))


def test_column_chairs_resolve_through_the_officials_of_the_meeting_date():
    document = extract(THU)
    chairs, warnings = pipeline.column_chairs(document, sources.DEFAULTS, META, HISTORY)
    # "Yang" fits TANG Yang and YANG Shan: the alias table decides.
    assert chairs == {'yang': 'Yang TANG', 'shan': 'Shan YANG', 'gene': 'Gene FONG'} and not warnings
    unknown, warnings = pipeline.column_chairs(document, {**sources.DEFAULTS, 'chair_aliases': {}}, META, HISTORY)
    assert unknown['yang'] == 'Yang' and warnings


@pytest.fixture
def offline_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = {**sources.DEFAULTS, 'local_topics': str(FIXTURES / TOPICS),
           'local_agenda': str(FIXTURES / 'agenda_120bis.csv'), 'meetings': {'120bis': META}}
    monkeypatch.setattr(pipeline, 'load_config', lambda: cfg)
    monkeypatch.setattr(sources, 'load_config', lambda: cfg)
    monkeypatch.setattr(pipeline, 'portal_rows', lambda: pytest.fail('Portal queried offline'))
    monkeypatch.setattr(pipeline, 'has_key', lambda: False)   # never reach Gemini from tests
    monkeypatch.setattr(pipeline.officials, 'fetch_history', lambda code: HISTORY)
    return cfg


def test_local_build_needs_no_network_and_records_state(offline_config, tmp_path, monkeypatch):
    monkeypatch.setattr(httpx.Client, 'send', lambda *a, **k: pytest.fail('Offline network request'))
    schedule = pipeline.build_schedule(SimpleNamespace(local=str(FIXTURES / THU), output_dir=tmp_path / 'out'))
    assert schedule.wg_id == 'ran4' and schedule.meeting_name == 'RAN4#120bis'
    assert schedule.source_labels == {THU: 'Schedule THU afternoon', TOPICS: 'Topics', 'agenda_120bis.csv': 'Agenda'}
    state = sources.read_json(tmp_path / 'out/ran4/.schedule_state.json')
    assert state['meeting_id'] == 'ran4#120bis' and state['version'] == 4412
    assert state['llm'] == {'used': False} and len(state['warnings']) == 2
    again = pipeline.build_schedule(SimpleNamespace(no_download=True, output_dir=tmp_path / 'out'))
    assert again.days == schedule.days
    # The snapshot round-trips and renders with its pieces and notes.
    save_schedule(schedule, tmp_path / 'schedule.json')
    html = generate_html(load_schedule(tmp_path / 'schedule.json'))
    assert '1/2' in html and 'R4-2621419 (Reply to RAN1 LS' in html


# ---------------------------------------------------------- document fallback

def without_monday_title():
    """The #120bis file with Monday's weekday title gone: the rules cannot place that table."""
    data = (FIXTURES / THU).read_bytes()
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            body = source.read(item.filename)
            if item.filename == 'ppt/slides/slide2.xml':
                assert b'<a:t>Monday</a:t>' in body
                body = body.replace(b'<a:t>Monday</a:t>', b'<a:t>Schedule</a:t>')
            target.writestr(item, body)
    return out.getvalue()


def outline_texts(data):
    from shared import office_outline
    return office_outline.texts(office_outline.blocks(data, THU))


def document_answer(texts):
    """What a correct reading of Monday 9:00–13:00 and Friday looks like."""
    lines = lambda ref: texts[ref].split('\n')
    fri_gene = lines('s6:sh2:r1c3')
    coffee = next(i for i, line in enumerate(fri_gene) if line.startswith('Coffee break'))
    fri_main = lines('s6:sh2:r3c1')
    def session(day, start, end, rooms, title, refs, **more):
        return {'day': day, 'start': start, 'end': end, 'room_ids': rooms, 'title': title, 'refs': refs, **more}
    rooms = lambda *names: [{'id': f'c{n}', 'name': texts[f'{slide}:sh2:r0c{n}']} for n in names for slide in [rooms.slide]]
    rooms.slide = 's2'
    monday = rooms(1, 2, 3, 4, 5)
    rooms.slide = 's6'
    friday = rooms(1, 2, 3, 4)
    return {
        'days': [{'day': 'Monday', 'rooms': monday, 'breaks': [{'name': '커피 타임', 'start': '10:30', 'end': '11:00'}]},
                 {'day': 'Friday', 'rooms': friday, 'breaks': [{'name': '커피 타임', 'start': '10:30', 'end': '11:00'},
                                                               {'name': '점심시간', 'start': '12:45', 'end': '13:45'}]}],
        'sessions': [
            session('Monday', '09:00', '09:30', ['c1', 'c2', 'c3', 'c4'], '1. Opening of the meeting', ['s2:sh2:r1c1']),
            session('Monday', '11:00', '13:00', ['c1'], '[101-A] System Parameter', ['s2:sh2:r4c1'], topics=['101-A']),
            session('Monday', '11:00', '12:00', ['c4'], 'Main Ad-hoc / [105-A] 6G RRM', ['s2:sh2:r4c4'],
                    topics=['105-A'], chair='Jerry', line_numbers=[0, 1]),
            session('Monday', '12:00', '13:00', ['c4'], '[105-B] 6G RRM', ['s2:sh2:r4c4'], topics=['105-B'],
                    chair='Jin-Yup', line_numbers=[3]),
            session('Friday', '08:30', '10:30', ['c3'], 'Return to', ['s6:sh2:r1c3'],
                    line_numbers=[i for i, line in enumerate(fri_gene[:coffee]) if line]),
            session('Friday', '10:30', '11:00', ['c3'], 'Coffee break ad-hoc: 6G Sensing feasibility / Finalize the WF',
                    ['s6:sh2:r1c3'], chair='Pierpaolo', in_break=True,
                    line_numbers=[i for i, line in enumerate(fri_gene) if line and i >= coffee]),
            session('Friday', '11:00', '12:45', ['c1'], '6G operation efficiency', ['s6:sh2:r3c1'], part=[1, 2],
                    line_numbers=[0]),
            session('Friday', '11:00', '12:45', ['c1'], '6G RRM / 13 Any other business / 14 Close of the meeting',
                    ['s6:sh2:r3c1'], part=[2, 2], line_numbers=[i for i, line in enumerate(fri_main) if line and i > 1]),
            session('Friday', '14:00', 'EOM', ['c3'], 'Return to', ['s6:sh2:r5c3']),
        ]}


class FakeGemini:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, message, schema, model, thinking='medium'):
        self.calls.append(message)
        return self.answers.pop(0)


def test_a_file_the_rules_cannot_read_is_read_whole_by_the_llm(offline_config, tmp_path, monkeypatch):
    data = without_monday_title()
    with pytest.raises(docmod.LayoutError, match='no weekday title'):
        docmod.extract_document(data, THU)
    local = tmp_path / THU
    local.write_bytes(data)
    gemini = FakeGemini(document_answer(outline_texts(data)))
    schedule = pipeline.build_schedule(SimpleNamespace(local=str(local), output_dir=tmp_path / 'out'), request=gemini)
    assert len(gemini.calls) == 1 and [d.day_name for d in schedule.days] == ['Monday', 'Friday']
    monday, friday = schedule.days
    # Rooms keep the rules' ids, so the column's chair and the topic list apply as they do for the rules.
    assert [r.id for r in monday.rooms] == ['yang', 'shan', 'gene', 'ad-hoc-room']
    rows = [(s.start_time, s.end_time, s.room_ids, s.name, s.agenda_item, s.chair) for s in monday.sessions]
    assert ('11:00', '13:00', ['yang'], '[101-A] System Parameter', '8.2.1, 8.2.2, 8.2.7', 'Yang TANG') in rows
    assert ('11:00', '12:00', ['ad-hoc-room'], 'Main Ad-hoc / [105-A] 6G RRM', '8.6, 8.6.2.1, 8.6.2.2, 8.6.3', 'Jerry') in rows
    assert ('12:00', '13:00', ['ad-hoc-room'], '[105-B] 6G RRM', '8.6.1, 8.6.4, 8.6.5', 'Jin-Yup') in rows
    opening = monday.sessions[0]
    assert (opening.room_ids, opening.chair, opening.agenda_item) == (['yang', 'shan', 'gene', 'ad-hoc-room'], None, '8.11.4')
    gene = {s.start_time: s for s in friday.sessions if s.room_ids == ['gene']}
    # A candidate list stays one for the LLM's reading (R1); "EOM" ends at the meeting's end.
    assert gene['08:30'].agenda_item is None and '5.2.1' in gene['08:30'].inferred_agenda_items
    assert (gene['10:30'].chair, gene['14:00'].end_time) == ('Pierpaolo', '17:30')
    assert [(s.part, s.name) for s in friday.sessions if s.room_ids == ['yang']] == [
        ([1, 2], '6G operation efficiency'), ([2, 2], '6G RRM / 13 Any other business / 14 Close of the meeting')]
    assert schedule.supplements[0]['text'].startswith('The schedule slides were not in their usual layout')
    assert {'type': 'heading', 'text': 'Slide 6'} in schedule.supplements
    assert sources.read_json(tmp_path / 'out/ran4/.schedule_state.json')['llm'] == {'model': sources.DEFAULTS['model'],
                                                                                  'document': True}
    # Without the LLM the layout error stands, and the last page stays.
    with pytest.raises(docmod.LayoutError):
        pipeline.build_schedule(SimpleNamespace(local=str(local), output_dir=tmp_path / 'out2'))


@pytest.mark.parametrize('change, problem', [
    (lambda a: a['sessions'][1].update(title='[101-A] System Parameters overview'), 'title words'),
    (lambda a: a['sessions'][1].update(topics=['101-B']), "topic number '101-B'"),
    (lambda a: a['sessions'][1].update(start='11:15'), '11:15 is not written'),
    (lambda a: a['sessions'][2].update(chair='Jerry Smith'), "chair 'Jerry Smith'"),
    (lambda a: a['sessions'][3].update(line_numbers=None), 'each must give its own line_numbers'),
    (lambda a: a['sessions'][3].update(start='11:00', line_numbers=[3]), 'overlap in the same room'),
    (lambda a: (a['sessions'][6].update(line_numbers=[2], title='6G RRM'),
                a['sessions'][7].update(line_numbers=[3, 4], title='13 Any other business')),
     'does not split pieces 1 and 2'),
    (lambda a: a['sessions'][5].update(start='10:00', end='10:30'), "takes that break's times"),
    (lambda a: a['days'][0]['rooms'][0].update(name='Main room'), "room name 'Main room'"),
    (lambda a: a['sessions'][0].update(refs=['s2:sh9:r1c1']), 'refs must cite block ids'),
])
def test_document_answers_are_checked_against_the_slides(change, problem):
    data = without_monday_title()
    texts = {k: v for k, v in outline_texts(data).items() if v.strip()}
    answer = document_answer(texts)
    assert llm.check_document(llm.DocResult.model_validate(answer), texts) == []
    change(answer)
    errors = llm.check_document(llm.DocResult.model_validate(answer), texts)
    assert any(problem in e for e in errors), errors


def test_a_blank_line_among_a_sessions_lines_is_left_out():
    data = without_monday_title()
    texts = {k: v for k, v in outline_texts(data).items() if v.strip()}
    answer = document_answer(texts)
    session = next(s for s in answer['sessions'] if s.get('part') and '' in texts[s['refs'][0]].split('\n'))
    blanks = [i for i, line in enumerate(texts[session['refs'][0]].split('\n')) if not line]
    session['line_numbers'] = sorted(session['line_numbers'] + blanks)
    assert llm.check_document(llm.DocResult.model_validate(answer), texts) == []
    session['line_numbers'] = blanks
    assert any('not only blank ones' in e for e in llm.check_document(llm.DocResult.model_validate(answer), texts))


def test_a_shortened_topic_number_stands_for_both_numbers():
    source = llm.spelled_topics('[102-A/B] System Parameter')
    assert llm.written_errors('s', ['102-A', '102-B', '102-A/B'], source, 'topic number') == []
    assert llm.written_errors('s', ['102-C'], source, 'topic number')


def test_a_document_session_takes_the_rules_title():
    result = llm.DocResult.model_validate({
        'days': [{'day': 'Tuesday', 'rooms': [{'id': 'r1', 'name': 'Ad hoc room'}], 'breaks': []}],
        'sessions': [{'day': 'Tuesday', 'start': '17:00', 'end': '18:00', 'room_ids': ['r1'], 'part': None,
                      'title': 'Main Ad-hoc: / [108] Spectrum sharing', 'topics': ['108'], 'chair': None,
                      'in_break': False, 'refs': ['c'], 'line_numbers': None}]})
    reading = llm.DocumentReading(result, {'c': 'Main Ad-hoc: \n[108] Spectrum sharing'}, {'Tuesday': {'r1': 'ad-hoc'}})
    found, _ = reading({'topics': {}, 'agenda': {}, 'column_chairs': {}}, {})
    assert found[0]['name'] == 'Main Ad-hoc / [108] Spectrum sharing'


def test_a_document_session_leaves_out_its_time_marks():
    assert llm.without_times(['---12:00---', '[105-B] 6G RRM', '----9.30----', '8:30 – 10:30', 'Start at 8:00',
                              'Main Ad-hoc:  (until 11:00) ']) == ['[105-B] 6G RRM', 'Main Ad-hoc: (until 11:00)']


def test_an_unchecked_document_answer_fails_the_build(tmp_path):
    data = without_monday_title()
    invented = document_answer(outline_texts(data))
    invented['sessions'][1]['title'] = 'Six G system parameters'
    gemini = FakeGemini(invented, invented)
    with pytest.raises(docmod.LayoutError, match='could not read the schedule either'):
        llm.read_document(data, THU, 'test-model', gemini, tmp_path)
    assert len(gemini.calls) == 2 and 'title words not in its cited text' in gemini.calls[1]
    # A file that is not a slide deck at all is not sent.
    with pytest.raises(docmod.LayoutError, match='cannot be outlined'):
        llm.read_document(b'not a zip', THU, 'test-model', FakeGemini(), tmp_path)


def test_lifecycle_check_stages_without_advancing_state(offline_config, tmp_path, monkeypatch):
    bundle = sources.local_bundle(FIXTURES / THU, offline_config)
    monkeypatch.setattr(sources, 'fetch_bundle', lambda *a, **k: bundle)
    monkeypatch.setattr(sources, 'portal_rows', lambda: [])
    before = {'meeting_id': 'ran4#120'}
    sources.write_json(sources.OUTPUT / '.schedule_state.json', before)
    result = lifecycle.check_updates()
    assert result.changed and 'meeting_id changed' in result.reasons and not result.errors
    assert sources.read_json(sources.OUTPUT / '.schedule_state.json') == before
    lifecycle.prepare_build()
    assert sources.Bundle.load(sources.DOWNLOADS / 'prepared').schedule == bundle.schedule
    sources.write_json(sources.OUTPUT / '.schedule_state.json', {**bundle.manifest, 'metadata': META})
    assert not lifecycle.check_updates().changed
    monkeypatch.setattr(sources, 'fetch_bundle', lambda *a, **k: (_ for _ in ()).throw(ValueError('down')))
    assert lifecycle.check_updates().errors == ['RAN4 source check failed: down']
    lifecycle.reset_cache()
    assert (sources.OUTPUT / '.schedule_state.json').exists() and not sources.DOWNLOADS.exists()


def test_lifecycle_import_is_isolated():
    result = subprocess.run([sys.executable, '-c', """
import sys
from working_groups.registry import get_working_group
get_working_group('ran4')
assert not any(name.startswith(('working_groups.ran1', 'working_groups.ran2', 'google.genai', 'openpyxl'))
               for name in sys.modules)
"""], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
