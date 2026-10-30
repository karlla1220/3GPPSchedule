"""SA1 chair files: real schedules plus the rules they rely on (research/sa1/DECISIONS.md)."""
from datetime import date
import io
from pathlib import Path

from docx import Document
import pytest

from shared import officials
from shared.renderer import generate_html
from shared.schedule import load_schedule, save_schedule
from working_groups.sa1 import document as docmod, llm, sessions

FIXTURES = Path(__file__).parent / 'fixtures/sa1'
EOM = 'Agenda for SA1#115_Friday_1550_END_OF_MEETING.docx'
TIMETABLE = 'Breakout sessions schedule_Prague_WED_1730.docx'
M112 = 'Agenda_of_SA1#112_Friday_1610.docx'
M113 = 'S1-261408_Agenda_SA1#113_16_00_friday.docx'
META_115 = {'starts_on': '2026-08-24', 'ends_on': '2026-08-28', 'timezone': 'Europe/Prague',
            'starts_at': '2026-08-24T09:00:00+02:00', 'ends_at': '2026-08-28T17:30:00+02:00'}
META_112 = {'starts_on': '2025-11-17', 'ends_on': '2025-11-21', 'timezone': 'America/Chicago'}
HISTORY = officials.parse_history(
    (Path(__file__).parent / 'fixtures/officials/S1_officialsHistory.htm').read_text(encoding='utf-8'))


def extract(name):
    return docmod.extract_document((FIXTURES / name).read_bytes(), name)


def build(name, metadata=META_115, history=HISTORY):
    present = officials.in_office(history, date.fromisoformat(metadata['starts_on']))
    return sessions.make_schedule(extract(name), metadata, [name], {name: "Chair's Notes"}, '2026-08-28 16:00',
                                  resolve_chair=lambda n: officials.resolve(n, present))


@pytest.fixture(scope='module')
def eom():
    return build(EOM)


def find(schedule, day, start, room='plenary'):
    day = next(d for d in schedule.days if d.day_name == day)
    return next(s for s in day.sessions if s.start_time == start and s.room_ids == ([room] if room else []))


def synthetic(*cells, kind='slot', start='11:00', end='12:30', chairs=(), agenda=None):
    """A Monday with cells given as line lists, in the #115 rooms."""
    return {'meeting_id': 'sa1#115', 'title': '', 'rooms': [dict(r) for r in docmod.DEFAULT_ROOMS],
            'days': [{'day': 'Monday', 'slots': [(start, end)], 'breaks': [],
                      'cells': [{'id': f'c{i}', 'start': start, 'end': end, 'kind': kind, 'row': '',
                                 'lines': lines} for i, lines in enumerate(cells)]}],
            'chairs': list(chairs), 'agenda': agenda or {}, 'supplements': []}


def read(document):
    return [(s['start'], s['end'], s['room'], s['name'], s['agenda_items'], s['chair'], s['lines'])
            for s in sessions.interpret(document)[0]]


# ------------------------------------------------------------------ documents

def test_real_table_rooms_slots_breaks_and_note_chairs():
    document = extract(EOM)
    assert document['meeting_id'] == 'sa1#115'
    assert [(r['id'], r['name'], r['place']) for r in document['rooms']] == [
        ('plenary', 'Plenary/ Breakout 1', 'D3/D4, Floor 3'), ('breakout-2', 'Breakout 2', 'D9, Floor 3')]
    monday = document['days'][0]
    assert monday['slots'] == [('08:00', '09:00'), ('09:00', '10:30'), ('11:00', '12:30'), ('14:00', '15:30'),
                               ('16:00', '18:00'), ('18:10', '19:30')]
    # The unnamed 18:00-18:10 row is a gap, not a break.
    assert [(b['name'], b['start'], b['end']) for b in monday['breaks']] == [
        ('Coffee', '10:30', '11:00'), ('Lunch', '12:30', '14:00'), ('Coffee', '15:30', '16:00')]
    assert document['chairs'][1] == {'topic': 'AI, Computing', 'chair': 'Vasil Aleksiev'}
    assert len(document['chairs']) == 5
    lunch = next(c for d in document['days'] for c in d['cells'] if c['kind'] == 'break')
    assert (lunch['row'], lunch['start'], lunch['end'], lunch['lines']) == (
        'Lunch', '12:30', '14:00', ['13:00- 14:00h Drafting on UE sensing profile'])
    # Only the rooms and the NOTE go below the grid, not the agenda's other pages.
    assert [b['text'].split('\n')[0][:20] for b in document['supplements']] == [
        'MEETING ROOMS:', 'NOTE: Slots schedule', '6G System and Operat']


def test_older_layouts_one_row_chair_table_and_repeated_time_columns():
    m112, m113 = extract(M112), extract(M113)
    assert [r['aliases'] for r in m112['rooms']] == [['drafting 1', 'plenary'], ['breakout 1', 'drafting 2']]
    assert {'topic': 'Massive Com + Verticals', 'chair': 'Yusuke Nakano'} in m112['chairs']
    # #113 repeats a time column between weekdays; only weekdays are days.
    assert [d['day'] for d in m113['days']] == ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    assert {'topic': 'Sensing + Immersive', 'chair': 'Feifei Lou'} in m113['chairs']


def test_agenda_is_the_heading_tree_with_own_lists_and_hand_numbers():
    agenda = extract(M112)['agenda']
    # Word numbering: a list of its own is one level down, a hand number stays as typed.
    assert agenda['8.1.2'] == 'System and Operation Aspects'
    assert agenda['8.1.2.1'] == 'Editor’s notes solving'
    assert (agenda['8.1.3'], agenda['8.1.3.1'], agenda['8.1.3.2']) == (
        'Artificial Intelligence', 'Editor’s notes solving', 'Resubmission of Use Cases and others')
    assert agenda['8.1.10'] == 'Consolidation of service and performance requirements'
    assert extract(EOM)['agenda']['7.3.3'].startswith('Artificial Intelligence capabilities')


def test_the_timetable_only_file_has_neither_meeting_nor_agenda():
    document = extract(TIMETABLE)
    assert document['meeting_id'] is None and document['agenda'] == {}
    assert len(document['chairs']) == 5


def test_a_document_without_a_schedule_table_is_a_layout_error():
    buffer = io.BytesIO()
    Document().save(buffer)
    assert not docmod.has_schedule(buffer.getvalue())
    with pytest.raises(docmod.LayoutError):
        docmod.extract_document(buffer.getvalue(), 'S1-263000.docx')


# ------------------------------------------------------------------- cells

def test_breakout_cells_are_split_by_room_and_take_the_note_chair(eom):
    ai, ubiquitous = find(eom, 'Tuesday', '09:00'), find(eom, 'Tuesday', '09:00', 'breakout-2')
    assert (ai.name, ai.agenda_item, ai.chair, ai.part) == ('7.3.3 AI', '7.3.3', 'Vasil ALEKSIEV', None)
    assert (ubiquitous.name, ubiquitous.chair) == ('7.3.5 Ubiquitous', 'QUN WEI')
    joint = find(eom, 'Wednesday', '14:00', 'breakout-2')
    assert (joint.name, joint.agenda_item, joint.chair) == ('7.3.10 IMS+7.3.6 Immersive', '7.3.10, 7.3.6', 'Erik Guttman')
    # Not on the officials page: as the NOTE writes it.
    assert find(eom, 'Tuesday', '16:00').chair == 'Lola Awoniyi-Oteri'


def test_plenary_cells_keep_the_cell_shape(eom):
    """Title "Plenary", items as notes, nothing split without a written time."""
    q1 = find(eom, 'Monday', '09:00')
    assert (q1.name, q1.end_time, q1.agenda_item, q1.notes, q1.chair) == (
        'Plenary', '10:30', '1, 2, 3', ['1. Opening', '2. Reports', '3. LSs'], None)
    q4 = find(eom, 'Monday', '16:00')
    assert (q4.name, q4.notes) == ('Plenary', ['7.3.1 General'])
    revisions = find(eom, 'Friday', '14:00')
    assert (revisions.name, revisions.end_time, revisions.agenda_item, revisions.notes) == (
        'Plenary', '16:00', None, ['Revisions'])   # "Meeting ends 16:00"
    assert not any(s.part for d in eom.days for s in d.sessions)


def test_cells_without_a_room_span_all_rooms(eom):
    q0 = find(eom, 'Tuesday', '08:00', None)
    assert (q0.name, q0.room_scope, q0.agenda_item) == ('7.2 FS_AddCapEm_REQ / 7.1 FS_SIMP', 'unassigned', '7.2, 7.1')
    drafting = find(eom, 'Wednesday', '13:00', None)
    assert (drafting.name, drafting.end_time, drafting.agenda_item, drafting.chair) == (
        'Drafting on UE sensing profile', '14:00', None, None)
    wednesday = next(d for d in eom.days if d.day_name == 'Wednesday')
    assert {b['name']: b.get('label_position') for b in wednesday.timeline.breaks}['Lunch'] == 'time-axis'
    mms = find(eom, 'Tuesday', '18:30', None)
    assert (mms.name, mms.end_time) == ('MMS', '20:00')


def test_written_relative_times_and_events_that_do_not_fit():
    m113 = build(M113, META_112)
    verticals, massive = (find(m113, 'Wednesday', t, 'drafting-2') for t in ('16:00', '17:30'))
    assert (verticals.name, verticals.end_time) == ('8.1.8 Verticals', '17:30')
    assert (massive.name, massive.end_time, massive.chair) == ('8.1.7 Massive+8.1.9 Others', '18:00', 'Erik Guttman')
    m112 = build(M112, META_112)
    below = next(b for b in m112.supplements if b['type'] == 'table')['rows']
    # A start with no end, and elections that would cover a Plenary cell: below the grid.
    assert below == [['Day', 'Schedule text'], ['Tuesday', 'MMS (18:30)'],
                     ['Tuesday', '1st round VC elections (12:00–13:30)'],
                     ['Wednesday', '2nd round VC elections (12:00–13:30)']]
    consent = find(m112, 'Wednesday', '13:15', None)
    assert (consent.name, consent.end_time) == ('Drafting on user consent', '14:00')


def test_agenda_numbers_are_checked_against_the_meeting_agenda():
    m112 = build(M112, META_112)
    massive = find(m112, 'Monday', '18:10', 'breakout-1')
    # "8.8 Verticals" is not on the agenda; one item is named Verticals.
    assert (massive.name, massive.agenda_item, massive.chair) == ('8.1.7 Massive + 8.8 Verticals', '8.1.7, 8.1.8',
                                                                 'Yusuke NAKANO')
    assert find(m112, 'Wednesday', '11:00').agenda_item is None   # "8.10 Consolidation of KPIs": no item named so
    # Plenary items named without a number take the number the table writes with that name.
    assert find(m112, 'Friday', '08:00').agenda_item == '8.1.6, 8.1.7, 8.1.8'
    # Two items run together on one line are both read.
    document = synthetic(['Plenary', '7.2 FS_AddCapEm_REQ 7.3.1 General', '7.3.2 System and Operation'])
    assert read(document)[0][4] == ['7.2', '7.3.1', '7.3.2']


def test_cell_colour_is_the_top_agenda_item(eom):
    assert find(eom, 'Tuesday', '09:00').group_header == 'Rel-21 contributions'
    assert find(eom, 'Monday', '11:00').group_header == 'Liaison Statements (including related contributions)'
    assert find(eom, 'Wednesday', '08:00', None).group_header == ''


def test_titles_drop_round_brackets_and_the_notes_keep_them():
    document = synthetic(['Breakout 2:', '7.3.9 Computing (cont)'],
                         chairs=[{'topic': 'AI, Computing', 'chair': 'Vasil Aleksiev'}])
    assert read(document) == [('11:00', '12:30', 'breakout-2', '7.3.9 Computing', ['7.3.9'], 'Vasil Aleksiev',
                               ['7.3.9 Computing (cont)'])]


def test_a_chair_needs_exactly_one_note_line():
    chairs = [{'topic': 'AI', 'chair': 'A One'}, {'topic': 'Computing', 'chair': 'B Two'}]
    both = synthetic(['Breakout 1: 7.3.3 AI+7.3.9 Computing'], chairs=chairs)
    none = synthetic(['Breakout 1:', 'On S1-254291'], chairs=chairs)
    assert read(both)[0][5] is None and read(none)[0][5] is None


@pytest.mark.parametrize('lines, kind, reason', [
    (['Plenary', '7.3.1 General end by 11:45'], 'slot', 'time written inside a line'),
    (['Breakout 3:', '7.3.4 ISAC'], 'slot', 'room head not in MEETING ROOMS'),
    (['Coffee chat', 'Breakout 1:', '7.3.3 AI'], 'slot', 'lines before the first room head'),
    (['Drafting on ISAC'], 'break', 'without a time in the break row'),
])
def test_uncertain_cells(lines, kind, reason):
    document = synthetic(lines, kind=kind)
    assert any(reason in r for r in sessions.uncertainty(document['days'][0]['cells'][0], document['rooms']))


@pytest.mark.parametrize('name', [EOM, TIMETABLE, M112, M113])
def test_the_real_schedules_have_no_uncertain_cell(name):
    document = extract(name)
    assert not [c['id'] for d in document['days'] for c in d['cells']
                if sessions.uncertainty(c, document['rooms'])]


def test_render_roundtrip_and_supplements(eom, tmp_path):
    path = tmp_path / 'schedule.json'
    save_schedule(eom, path)
    assert load_schedule(path).days == eom.days
    html = generate_html(eom)
    assert 'Plenary/ Breakout 1' in html and 'Room unassigned' in html and 'MEETING ROOMS:' in html
    # The room's place from the MEETING ROOMS lines is in the popup Room line.
    assert 'Room: Breakout 2 · D9, Floor 3' in html
    assert '<span class="room-name">Breakout 2</span><span class="room-place">D9, Floor 3</span>' in html


# --------------------------------------------------------------------- LLM

class FakeGemini:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, message, schema, model, thinking='medium'):
        self.calls.append(message)
        if not self.answers:
            raise RuntimeError('unexpected call')
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


UNSURE = ['Breakout 1:', '7.3.3 AI', 'end by 11:45 =====', 'Breakout 2:', '7.3.5 Ubiquitous']


def unsure_document():
    return synthetic(UNSURE, ['Plenary', '4. WIDs'],
                     chairs=[{'topic': 'AI', 'chair': 'Vasil Aleksiev'}, {'topic': 'Ubiquitous', 'chair': 'Qun Wei'}])


def session(room, start, end, title, items, lines):
    return {'room': room, 'start': start, 'end': end, 'title': title, 'agenda_items': items, 'lines': lines}


def test_checked_llm_answers_replace_the_rules_for_uncertain_cells(tmp_path):
    document = unsure_document()
    gemini = FakeGemini({'cells': [{'id': 'c0', 'sessions': [
        session('plenary', '11:00', '11:45', '7.3.3 AI', ['7.3.3'], [0, 1, 2]),
        session('breakout-2', '11:00', '12:30', '7.3.5 Ubiquitous', ['7.3.5'], [3, 4])]}]})
    refine = llm.CellRefiner(document, 'test-model', gemini, tmp_path)
    found = [(s['start'], s['end'], s['room'], s['name'], s['chair']) for s in sessions.interpret(document, refine)[0]]
    assert found == [('11:00', '11:45', 'plenary', '7.3.3 AI', 'Vasil Aleksiev'),
                     ('11:00', '12:30', 'breakout-2', '7.3.5 Ubiquitous', 'Qun Wei'),
                     ('11:00', '12:30', 'plenary', 'Plenary', None)]
    assert refine.summary == {'model': 'test-model', 'uncertain': 1, 'applied': 1, 'kept_rules': 0}
    assert len(gemini.calls) == 1 and '"c0"' in gemini.calls[0] and '"c1"' not in gemini.calls[0]


@pytest.mark.parametrize('bad, problem', [
    (session('breakout-2', '11:00', '11:45', '7.3.3 AI', ['7.3.3'], [0, 1, 2]), 'another room'),
    (session('plenary', '11:00', '12:00', '7.3.3 AI', ['7.3.3'], [0, 1, 2]), 'not written in the source'),
    (session('plenary', '11:00', '11:45', 'Artificial Intelligence', ['7.3.3'], [0, 1, 2]), 'title words'),
    (session('plenary', '11:00', '11:45', '7.3.3 AI', ['7.3.4'], [0, 1, 2]), 'agenda item'),
    (session('plenary', '11:00', '11:45', '7.3.3 AI', ['7.3.3'], [0, 1]), 'not cited'),
])
def test_unchecked_answers_are_retried_once_then_the_rules_stand(tmp_path, bad, problem, capsys):
    document = unsure_document()
    wrong = {'cells': [{'id': 'c0', 'sessions': [bad, session('breakout-2', '11:00', '12:30', '7.3.5 Ubiquitous',
                                                                  ['7.3.5'], [3, 4])]}]}
    gemini = FakeGemini(wrong, wrong)
    refine = llm.CellRefiner(document, 'test-model', gemini, tmp_path)
    found = sessions.interpret(document, refine)[0]
    assert [s['name'] for s in found if s['room'] == 'plenary'][0] == '7.3.3 AI / end by 11:45'
    assert found[0]['end'] == '12:30'   # the rules' reading
    assert len(gemini.calls) == 2 and problem in gemini.calls[1] and refine.summary['kept_rules'] == 1
    assert problem in capsys.readouterr().out


def test_an_llm_outage_never_breaks_the_build(tmp_path):
    refine = llm.CellRefiner(unsure_document(), 'test-model', FakeGemini(RuntimeError('down')), tmp_path)
    assert len(sessions.interpret(unsure_document(), refine)[0]) == 3
    assert refine.summary['error'] == 'down' and refine.summary['applied'] == 0
