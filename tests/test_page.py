"""Common page parts can be reused without the Gantt renderer."""
from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess

from bs4 import BeautifulSoup
import pytest

from shared.page import normalize_presentation, render_header
from schedule_fixture import build_schedule


def test_header_escapes_site_metadata_and_keeps_demo_disclosure():
    schedule = replace(build_schedule(), contact_email='old@example.com')
    html = render_header(schedule, presentation={
        'creator': '<script>bad()</script>', 'contact_name': '<Support>',
        'contact_email': 'new@example.com', 'notice': 'Official'})
    assert '<script>' not in html and '&lt;Support&gt;' in html
    assert 'mailto:new@example.com' in html and 'old@example.com' not in html
    assert 'Demo schedule' in html and 'Official' not in html
    assert 'id="tz-now"' in html


def test_empty_site_contact_does_not_restore_legacy_snapshot_contact():
    assert 'mailto:' not in render_header(replace(build_schedule(), contact_email='old@example.com'))


def sources(**changes):
    line = BeautifulSoup(render_header(replace(build_schedule(), **changes)), 'html.parser').select_one('p.meta')
    return line.get_text().split('\xa0|\xa0')[0], [span['title'] for span in line.select('span[title]')]


def test_sources_are_short_labels_with_the_version_and_the_file_name_on_hover():
    files = ['Draft RAN1#126b online and offline schedules - v03.docx',
             'RAN1#126bis schedule for Hiroki Adhoc2 sessions_v01.docx',
             'RAN1#126b Sorour sessions online and offline schedules - v00.docx']
    assert sources(source_files=files, source_labels=dict(zip(files, ['Main', 'Hiroki', 'Sorour']))) == (
        'Sources: Main\xa0v03 · Hiroki\xa0v01 · Sorour\xa0v00 ', files)
    plenary = ['RAN#113 time plan v09.zip', 'agenda.csv']
    assert sources(source_files=plenary, source_labels=dict(zip(plenary, ['Time plan', 'Agenda'])))[0] == (
        'Sources: Time\xa0plan\xa0v09 · Agenda ')


@pytest.mark.parametrize('name, shown', [
    ('R2_135b_Schedule_V01.docx', 'Main\xa0v01'),
    ('Schedule v02 for NR V2X sessions v3.docx', 'Main\xa0v3'),   # The last one; V2X is a topic.
    ('Schedule (rev2).docx', 'Main'),
])
def test_source_version_is_read_from_the_file_name(name, shown):
    assert sources(source_files=[name], source_labels={name: 'Main'})[0] == f'Sources: {shown} '


def test_source_without_a_label_keeps_its_file_name():
    # Snapshots saved before labels existed, and the demo schedule.
    name = 'Draft <RAN1#126b> schedules - v03.docx'
    header = render_header(replace(build_schedule(), source_files=[name, 'agenda.csv']))
    assert 'Sources: Draft &lt;RAN1#126b&gt; schedules - v03.docx · agenda.csv &nbsp;|' in header
    assert sources(source_files=[])[0] == 'Sources: Fixed demonstration schedule '
    labelled = render_header(replace(build_schedule(), source_files=[name + '"x'], source_labels={name + '"x': '<Main>'}))
    assert 'title="Draft &lt;RAN1#126b&gt; schedules - v03.docx&quot;x">&lt;Main&gt;&nbsp;v03</span>' in labelled


@pytest.mark.parametrize('value', [[], {'creator': None}, {'contact_emali': 'typo'},
    {'contact_email': 'name@example.com?subject=unexpected'},
    {'contact_email': 'name@example.com\nBcc:other@example.com'}])
def test_invalid_site_metadata_is_rejected(value):
    with pytest.raises(ValueError):
        normalize_presentation(value)


SITE = {'creator': 'Site Author', 'creator_url': 'https://example.com/in/author',
        'creator_bio': 'RAN1 delegate <LGE>', 'disclaimer': 'Personal & unofficial',
        'feedback_url': 'https://example.com/issues', 'support_title': 'Proposal 1: <Coffee>', 'support_links': [
            {'label': 'Patreon', 'url': 'https://example.com/support?a=1&b=2'},
            {'label': 'GitHub <Sponsors>', 'url': 'https://example.com/sponsors'}]}
SUPPORT_URLS = [link['url'] for link in SITE['support_links']]


def real_meeting(**changes):
    return replace(build_schedule(), **{
        'is_demo': False, 'wg_id': 'ran1', 'meeting_id': 'ran1#126bis', 'meeting_name': 'RAN1#126bis',
        'timezone': 'Asia/Seoul', 'starts_on': '2026-10-12', 'ends_on': '2026-10-16'} | changes)


def test_header_links_creator_feedback_and_support():
    header = BeautifulSoup(render_header(real_meeting(), presentation=SITE), 'html.parser')
    created, about = [p for p in header.select('p.meta') if p.find('a')]
    assert created.get_text() == 'Created by Site Author, RAN1 delegate <LGE>'
    assert created.a['href'] == SITE['creator_url']
    assert about.get_text() == 'Personal & unofficial · Feedback'
    assert about.a['href'] == SITE['feedback_url']
    support = header.select_one('p.support-links')
    assert support.find_previous_sibling().name == 'h1'  # Beside the titles, above the meta lines.
    assert support.get_text() == '☕Proposal 1: <Coffee> on Patreon or GitHub <Sponsors>'
    assert [a['href'] for a in support.select('a')] == SUPPORT_URLS
    assert all(a['target'] == '_blank' and a['rel'] == ['noopener'] for a in header.select('a'))


def test_creator_email_follows_the_name_but_another_contact_keeps_its_line():
    def lines(**site):
        header = BeautifulSoup(render_header(real_meeting(), presentation=SITE | site), 'html.parser')
        return [p.get_text() for p in header.select('p.meta')][-3:], header.select_one('a[href^="mailto:"]')['href']
    for name in ('', 'Site Author'):
        texts, href = lines(contact_email='author@example.com', contact_name=name)
        assert texts[1:] == ['Created by Site Author (author@example.com), RAN1 delegate <LGE>',
                             'Personal & unofficial · Feedback']
        assert href == 'mailto:author@example.com'
    texts, href = lines(contact_email='help@example.com', contact_name='Help Desk')
    assert texts[0] == 'Created by Site Author, RAN1 delegate <LGE>'
    assert texts[2] == 'Contact: Help Desk (help@example.com) for reports or feature requests.'
    assert lines(contact_email='help@example.com', creator='')[0][-1].startswith('Contact:  (help@example.com)')


def test_header_without_links_keeps_plain_creator_line():
    header = render_header(real_meeting(), presentation={'creator': 'Site Author'})
    assert '<p class="meta">Created by Site Author</p>' in header
    assert '<a' not in header and 'support-note' not in header and 'support-links' not in header


@pytest.mark.parametrize('key', ['creator_url', 'feedback_url'])
@pytest.mark.parametrize('url', ['http://example.com', 'javascript:alert(1)', 'https://a b',
                                 'https://example.com/"onmouseover="x', 'example.com'])
def test_site_links_must_be_plain_https(key, url):
    with pytest.raises(ValueError, match=key):
        normalize_presentation({key: url})


@pytest.mark.parametrize('links', [
    'https://example.com', ['https://example.com'], [{'label': 'Patreon'}],
    [{'label': '', 'url': 'https://example.com'}], [{'label': 'Patreon', 'url': 'http://example.com'}],
    [{'label': 'Patreon', 'url': 'https://example.com', 'title': 'extra'}],
    [{'label': 1, 'url': 'https://example.com'}]])
def test_support_links_must_be_labelled_https_links(links):
    with pytest.raises(ValueError, match='support_links'):
        normalize_presentation({'support_links': links})


def test_support_links_are_joined_as_a_sentence():
    link = {'label': 'Patreon', 'url': 'https://example.com'}
    def support(count):
        header = render_header(real_meeting(), presentation={'support_links': [link] * count})
        return BeautifulSoup(header, 'html.parser').select_one('.support-text > span').get_text()
    assert support(1) == 'on Patreon'
    assert '<strong>Support this tool</strong>' in render_header(real_meeting(), presentation={'support_links': [link]})
    assert support(3) == 'on Patreon, Patreon or Patreon'


def support_note(schedule, **site):
    header = BeautifulSoup(render_header(schedule, presentation=SITE | site), 'html.parser')
    return header.select_one('#support-note'), header.find('script')


def run_note(schedule, now, **options):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required to execute browser logic')
    result = subprocess.run([node, str(Path(__file__).parent / 'fixtures/support_note_browser.cjs')],
                            input=json.dumps({'script': support_note(schedule)[1].string, 'now': now, **options}),
                            text=True, capture_output=True, check=True, encoding='utf-8')
    return json.loads(result.stdout)


def test_support_note_is_hidden_markup_below_the_notice():
    header = BeautifulSoup(render_header(real_meeting(), presentation=SITE | {'notice': 'Notice'}), 'html.parser')
    note = header.select_one('#support-note')
    assert note.has_attr('hidden')
    assert note.find_previous_sibling('p').get_text() == 'Notice'
    assert note.find_next_sibling('p').get_text().startswith('Created by')
    assert note.span.get_text() == ('Hope this helped you through RAN1#126bis. I build and maintain it on my own time. '
                                    'If it saved you some time, you can buy me a coffee on Patreon or GitHub <Sponsors>\xa0☕. '
                                    'Or just say hi at the next meeting!')
    assert [a['href'] for a in note.select('a')] == SUPPORT_URLS
    assert note.button['aria-label'] == 'Dismiss'


@pytest.mark.parametrize('schedule, site', [
    (real_meeting(), {'support_links': []}),        # Nothing to link to.
    (real_meeting(is_demo=True), {}),              # Demo data is not a meeting.
    (real_meeting(ends_on=None), {}),               # Undated: the end is unknown.
    (real_meeting(ends_on='soon'), {}),
    (real_meeting(timezone='Nowhere/None'), {}),
])
def test_support_note_needs_a_link_and_a_dated_real_meeting(schedule, site):
    assert support_note(schedule, **site) == (None, None)


@pytest.mark.parametrize('now, shown', [
    ('2026-10-12T00:00:00Z', False),          # In progress.
    ('2026-10-16T02:59:59Z', False),          # Last morning in Seoul.
    ('2026-10-16T03:00:00Z', True),           # Noon of the last day in Seoul.
    ('2026-10-17T00:00:00Z', True),           # Ended.
    ('2027-01-01T00:00:00Z', True),           # Still the latest schedule.
])
def test_support_note_shows_from_the_last_afternoon_at_the_venue(now, shown):
    assert run_note(real_meeting(), now)['shown'] is shown


def test_support_note_is_dismissed_once_per_meeting():
    schedule, now = real_meeting(), '2026-10-17T00:00:00Z'
    first = run_note(schedule, now, dismiss=True)
    assert first['shown'] and first['hiddenAfter']
    assert first['stored'] == {'3gpp_schedule_support_dismissed:ran1:ran1#126bis': 'true'}
    assert not run_note(schedule, now, stored=first['stored'])['shown']
    following = real_meeting(meeting_id='ran1#127', meeting_name='RAN1#127')
    assert run_note(following, now, stored=first['stored'])['shown']


def test_support_note_works_without_storage():
    state = run_note(real_meeting(), '2026-10-17T00:00:00Z', storageError=True, dismiss=True)
    assert state['shown'] and state['hiddenAfter']


def test_meeting_id_cannot_close_the_support_script():
    schedule = real_meeting(meeting_id='</script><b>')
    note, script = support_note(schedule)
    assert r'\u003c/script>' in script.string
    assert run_note(schedule, '2026-10-17T00:00:00Z', dismiss=True)['stored'] == {
        '3gpp_schedule_support_dismissed:ran1:</script><b>': 'true'}
