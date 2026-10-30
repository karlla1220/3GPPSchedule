"""Helpers every WG shares, so the same rule looks the same on every page."""
from lxml import etree

from shared import llm_cells
from shared.agenda import describe, written_ranges
from shared.agreement_assets import agenda_section_ids
from shared.docx_text import W, paragraph_text
from shared.officials import full_name, join_chairs, split_chairs
from shared.remote_files import listing_entries
from shared.renderer import _cell_chair, _room_name_html
from shared.titles import clean


def test_clean_drops_round_brackets_and_the_space_they_leave():
    assert clean('Opening (1), Agenda (2), Reports (3)') == 'Opening, Agenda, Reports'
    assert clean('NR20 MIMO (if time allows)') == 'NR20 MIMO'
    assert clean('(e)RedCap') == '(e)RedCap'
    assert clean('6G Offline (CB#8)') == '6G Offline'
    assert clean('Topic [ ]') == 'Topic'


def test_describe_climbs_to_the_nearest_listed_item():
    agenda = {'10': '6G', '10.6': 'Mobility'}
    tree = describe('10.6.x', agenda)
    assert tree['matched_agenda_item'] == '10.6'
    assert [h['agenda_item'] for h in tree['hierarchy']] == ['10', '10.6']
    assert describe('9.1', agenda) is None


def paragraph(xml):
    return etree.fromstring(f'<w:p xmlns:w="{W[1:-1]}">{xml}</w:p>')


def test_paragraph_text_accepts_changes_and_collects_struck_text():
    p = paragraph('<w:r><w:t>Keep </w:t></w:r>'
                  '<w:ins><w:r><w:t>added </w:t></w:r></w:ins>'
                  '<w:del><w:r><w:delText>gone</w:delText></w:r></w:del>'
                  '<w:r><w:rPr><w:strike/></w:rPr><w:t>struck</w:t></w:r>')
    dropped = []
    assert paragraph_text(p, dropped) == 'Keep added '
    assert dropped == ['struck']
    assert paragraph_text(p, keep_struck=True) == 'Keep added struck'


def test_listing_entries_can_keep_only_the_folders_children():
    url = 'https://www.3gpp.org/ftp/tsg_sa/WG1_Serv/Inbox/'
    html = ('<table><tr><td><a href="../">Parent Directory</a></td></tr>'
            '<tr><td><a href="agenda.docx">agenda.docx</a></td><td>2026/10/14 9:05</td></tr></table>')
    assert [e['name'] for e in listing_entries(html, url)] == ['WG1_Serv', 'agenda.docx']
    entries = listing_entries(html, url, children_only=True)
    assert entries == [{'url': url + 'agenda.docx', 'name': 'agenda.docx',
                        'uploaded_at': '2026-10-14 09:05', 'listing': url}]


def test_written_errors_names_what_was_not_written():
    assert llm_cells.written_errors('c', ['107-B'], 'Rel-20 [107-A]', 'topic number') == [
        "c: topic number '107-B' is not written in its cited text"]
    assert llm_cells.written_errors('c', ['Erl'], 'Erlin', 'chair') == [
        "c: chair 'Erl' is not a name written in the text"]


def test_a_range_the_source_wrote_stays_one_range_on_the_ai_line():
    members = ['20.6.12', '20.6.13', '20.6.14', '20.6.15', '20.6.16']
    assert written_ranges(['20.6.18'] + members, [('20.6.12–20.6.16', members)]) == ['20.6.18', '20.6.12–20.6.16']
    # Numbers written one by one are not joined.
    assert written_ranges(members) == members
    assert agenda_section_ids('20.6.12–20.6.16, 1–8', ()) == members + [str(n) for n in range(1, 9)]


def test_the_cell_shows_the_chairs_given_name():
    assert _cell_chair('Lixiang XU') == 'Lixiang'
    assert _cell_chair('Yang TANG / Shan YANG') == 'Yang / Shan'
    assert _cell_chair('Hiroki') == 'Hiroki'
    assert _cell_chair('Ruixin Wang') == 'Ruixin Wang'
    assert full_name('QUN', 'WEI') == 'Qun WEI'
    assert full_name('JIN-YUP', 'KIM') == 'Jin-Yup KIM'


def test_a_room_name_breaks_before_its_last_bracket():
    assert _room_name_html('RAN4 Breakout 2 (BR2)(Gene Session)') == (
        '<span class="room-name-text">RAN4 Breakout 2 (BR2)<wbr><span class="room-word">(Gene Session)</span></span>')
    assert _room_name_html('RAN4 RF,NTN,A-IOT (Gene Session)') == (
        '<span class="room-name-text">RAN4 <span class="room-word">RF,NTN,A-IOT</span> '
        '<span class="room-word">(Gene Session)</span></span>')
    assert _room_name_html('Main') == '<span class="room-name-text">Main</span>'


def test_several_chairs_are_joined_by_a_slash():
    # A comma would read as "FAMILY, Given", one person.
    assert join_chairs(['Leo', 'Jinqiang', 'Leo']) == 'Leo / Jinqiang'
    assert join_chairs([]) is None
    assert split_chairs('Erlin, Kyeongin') == split_chairs('Erlin / Kyeongin') == ['Erlin', 'Kyeongin']


def test_a_note_listing_only_the_ai_lines_numbers_is_left_out():
    from shared.renderer import _cell_notes
    from shared.schedule import Session
    session = Session(name='AIML RAN', duration_minutes=60, start_time='09:00', end_time='10:00', day='Monday',
                      agenda_item='12, 32, 6', notes=['AIML RAN: AI 12', 'AI32, AI6', 'AI 33', 'L'])
    # The head line stays: the title may have lost words in the cleaning.
    assert _cell_notes(session) == ['AIML RAN: AI 12', 'AI 33', 'L']


def test_every_wg_infers_agenda_items_for_cells_without_one(tmp_path, monkeypatch):
    from shared import agenda_labels
    from shared.schedule import DaySchedule, Schedule, Session, Timeline

    def cell(name, ai=None):
        return Session(name=name, duration_minutes=60, start_time='09:00', end_time='10:00', day='Monday',
                       agenda_item=ai)

    schedule = Schedule(meeting_id='ran4#121', meeting_name='RAN4#121', source_file='', generated_at='', days=[DaySchedule(
        day_name='Monday', rooms=[], timeline=Timeline(start='09:00', end='10:00'),
        sessions=[cell('6G system parameter'), cell('Return to'), cell('NTN', '7.6'), cell('Basket')])])
    # Items a WG's rules found stand; the LLM is not asked about that cell.
    schedule.days[0].sessions[3].inferred_agenda_items = ['7.6']
    agenda = {'7.6': 'NTN', '8': '6G', '8.2': 'System parameters', '8.2.1': 'Bandwidth'}
    asked = []

    def judge(labels, items):
        asked.append(labels)
        return {'6G system parameter': {'items': [{'agenda_item': '8.2'}, {'agenda_item': '8.2.1'}], 'note': ''},
                'Return to': {'items': [], 'note': 'running order'}}

    # Offline (or with the LLM off) nothing is asked and nothing recorded.
    agenda_labels.infer_for_schedule(schedule, 'ran4', 'RAN4', agenda, enabled=False)
    assert not (tmp_path / 'agenda_label_map').exists()
    monkeypatch.setattr(agenda_labels, 'gemini_judge', lambda labels, items, **k: judge(labels, items))
    agenda_labels.infer_for_schedule(schedule, 'ran4', 'RAN4', agenda, enabled=True)
    sessions = schedule.days[0].sessions
    assert [s.inferred_agenda_items for s in sessions] == [['8.2'], [], [], ['7.6']]
    assert asked == [['6G system parameter', 'Return to']]
    # A recorded label is not asked again, even offline.
    sessions[0].inferred_agenda_items = []
    agenda_labels.infer_for_schedule(schedule, 'ran4', 'RAN4', agenda, enabled=False)
    assert sessions[0].inferred_agenda_items == ['8.2'] and len(asked) == 1
