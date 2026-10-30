"""SA1 TDoc conclusions: the chair file's TDoc table in the agreement panel below the grid."""
import copy
from types import SimpleNamespace

from bs4 import BeautifulSoup
import pytest

from shared.agreement_assets import agreement_assets, has_panel, package_agreements
from shared.renderer import generate_html
from test_sa1 import EOM, FIXTURES, M112, META_112, build, find
from test_sa1_sources import offline_config  # noqa: F401  (fixture)
from working_groups.sa1 import conclusions, pipeline

FILE = {'name': EOM, 'sha256': 'abc', 'uploaded_at': '2026-08-28 13:52'}


@pytest.fixture(scope='module')
def sections():
    return conclusions.read_sections((FIXTURES / EOM).read_bytes())


def test_each_agenda_heading_lists_its_tdoc_rows_as_one_table(sections):
    isac = sections['7.3.4']
    assert isac['title'] == 'Integrated Sensing and Communications' and len(isac['blocks']) > 40
    table = BeautifulSoup(isac['html'], 'html.parser')
    # The legend's column names head the table; every TDoc row is one unit the change marks.
    assert [th.text for th in table.find_all('th')] == [
        'Doc Type', 'Tdoc number', 'Sourcing company(ies)', 'Document Title', 'Conclusion', 'Comments']
    assert len(table.select('td[data-unit]')) == len(isac['blocks'])
    # A heading with only subsections has no rows of its own.
    assert sections['6']['blocks'] == [] and 'html' not in sections['6']


def test_conclusion_shading_is_softened_and_tdocs_link_to_the_archive(sections):
    html = sections['7.3.4']['html']
    assert 'background:#00FFFF' not in html.upper().replace('BDEEF3', '')
    assert 'background:#bdeef3' in html and 'background:#c6f0bd' in html   # Revised/Noted, Agreed
    assert conclusions.soften('CCFFCC') == 'ccffcc' and conclusions.soften('FF0000') == 'ffb2b2'
    # The chair's link to a file on their own disk is the TDoc in the meeting folder.
    assert 'href="https://www.3gpp.org/ftp/tsg_sa/WG1_Serv/TSGS1_115_Prague/Docs/S1-263194.zip"' in html
    assert 'file:' not in html


def test_rows_new_since_the_last_build_are_marked_added():
    data = (FIXTURES / EOM).read_bytes()
    first = conclusions.build(data, FILE, 'sa1#115', None)
    assert {s['change'] for s in first['sections'].values()} == {'initial'}
    assert first['sections']['7.3.4']['changed_at'] == '2026-08-28T13:52:00+00:00'
    previous = copy.deepcopy(first)
    del previous['sections']['7.3.4']['blocks'][3]
    del previous['sections']['7.3.9']
    later = conclusions.build(data, {**FILE, 'name': 'later.docx', 'sha256': 'def'}, 'sa1#115', previous)
    isac, computing, ai = (later['sections'][k] for k in ('7.3.4', '7.3.9', '7.3.3'))
    assert (isac['change'], isac['added'], isac['changed_in']) == ('updated', [3], 'later.docx')
    assert computing['change'] == 'new' and computing['added'] == list(range(len(computing['blocks'])))
    assert (ai['change'], ai['changed_in']) == ('initial', EOM)   # unchanged: as before
    stored, fragments = package_agreements(later, previous)
    assert 'class="agreement-added"' in fragments[stored['sections']['7.3.4']['file']]


def test_another_reading_of_the_same_table_is_no_edit():
    data = (FIXTURES / EOM).read_bytes()
    previous = conclusions.build(data, FILE, 'sa1#115', None)
    previous['parser_version'] = 0
    previous['sections']['7.3.4']['blocks'] = []
    again = conclusions.build(data, FILE, 'sa1#115', previous)
    assert again['sections']['7.3.4']['change'] == 'initial'


def test_the_panel_opens_from_written_and_found_agenda_items(tmp_path):
    schedule = build(M112, META_112)
    data = conclusions.build((FIXTURES / M112).read_bytes(), {**FILE, 'name': M112}, schedule.meeting_id, None)
    schedule.chairman_agreements, schedule.agreement_fragments = package_agreements(data)
    assert has_panel(schedule)
    page = BeautifulSoup(generate_html(schedule), 'html.parser')
    panel = page.find(id='agreement-panel')
    assert 'tdoc-conclusions' in panel['class'] and panel.h2.text == 'TDoc conclusions for'
    blocks = page.select('.session-block')
    written = next(b for b in blocks if (b['data-name'], b['data-ai']) == ('8.1.3 AI', '8.1.3'))
    assert written['data-agreement-ai'].split('|')[0] == '8.1.3'
    # "Plenary ⏎ Immersive ⏎ Massive ⏎ Verticals": numbers found by name, filter and panel only.
    found = [b for b in blocks if b['data-name'] == 'Plenary' and b['data-ai'] == '' and b['data-agreement-ai']]
    assert len(found) == 1 and found[0]['data-agreement-ai'].startswith('8.1.6|')
    manifest, _ = agreement_assets(schedule)
    assert manifest['text']['heading'] == 'TDoc conclusions for' and manifest['sections']['8.1.6']['url']


def test_the_local_build_publishes_conclusions_and_keeps_them_on_failure(offline_config, tmp_path, monkeypatch):  # noqa: F811
    options = SimpleNamespace(local=str(FIXTURES / EOM), no_download=True, output_dir=tmp_path / 'out')
    schedule = pipeline.build_schedule(options)
    data = schedule.chairman_agreements
    assert (data['wg_id'], data['status'], data['kind']) == ('sa1', 'ready', 'tdoc-conclusions')
    assert schedule.agreement_fragments and all('html' not in s for s in data['sections'].values())
    from shared.schedule import save_schedule
    save_schedule(schedule, tmp_path / 'out/sa1/schedule.json')
    monkeypatch.setattr(conclusions, 'build', lambda *a, **k: (_ for _ in ()).throw(ValueError('broken')))
    again = pipeline.build_schedule(options)
    # The schedule is still published, with what was last published for the meeting.
    assert again.chairman_agreements['sections'].keys() == data['sections'].keys()
    assert find(again, 'Tuesday', '09:00').name == '7.3.3 AI'
