"""Helpers every WG shares, so the same rule looks the same on every page."""
from lxml import etree

from shared import llm_cells
from shared.agenda import describe
from shared.docx_text import W, paragraph_text
from shared.remote_files import listing_entries
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
