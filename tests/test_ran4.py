"""RAN4 meeting schedules: real chair PPTX files plus the rules they rely on."""
from pathlib import Path

import pytest

from working_groups.ran4 import document as docmod, topics as topicmod

FIXTURES = Path(__file__).parent / 'fixtures/ran4'
THU = 'RAN4_120bis_meeting_schedule_THU_afternoon.pptx'
V11_119 = 'RAN4_119_meeting_schedule_v11.pptx'
TOPICS = 'List_Topics_moderators-RAN4_120bis_final.xlsx'


def extract(name):
    return docmod.extract_document((FIXTURES / name).read_bytes(), name)


def agenda():
    return docmod.agenda_map((FIXTURES / 'agenda_120bis.csv').read_bytes())


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
