from datetime import date
from pathlib import Path

from shared import listserv

FIXTURES = Path(__file__).parent / 'fixtures' / 'listserv'
RAN1_FEED = Path(__file__).parent / 'fixtures' / 'ran1' / 'listserv_126bis' / 'feed_rss2_limit50.xml'


def test_a_message_page_names_its_attachments_and_their_links():
    page = (FIXTURES / 'message_page.html').read_text()
    files = listserv.attachments(page)
    # The text and HTML body parts carry no file name; the two documents do.
    assert [f['name'] for f in files] == ['R2_136_Agenda_v02.docx', 'R2_136_Schedule_v00.docx']
    schedule = files[1]
    assert schedule['url'].startswith('https://list.etsi.org/scripts/wa.exe?A3=ind2611a&L=3GPP_TSG_RAN_WG2&')
    assert '&amp;' not in schedule['url'] and schedule['url'].endswith('&N=R2_136_Schedule_v00.docx&attachment=q&XSS=3')
    assert schedule['content_type'] == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    assert listserv.attachment_name(schedule['url']) == 'R2_136_Schedule_v00.docx'
    # The body link RAN1 reads is still the first text/plain part, without the header.
    assert listserv.body_url(page).endswith('T=text%2Fplain;%20charset=WINDOWS-1252')


def test_only_archive_attachment_links_have_a_file_name():
    assert listserv.attachment_name('https://www.3gpp.org/ftp/x/R2_136_Schedule_v00.docx') is None
    assert listserv.attachment_name(listserv.message_url('8ee902d7.2611a', '3GPP_TSG_RAN_WG2')) is None
    assert listserv.attachment_name('https://list.etsi.org/scripts/wa.exe?A3=ind2611a&L=X&T=text%2Fplain') is None


def test_a_weekly_index_lists_its_mails():
    items = listserv.parse_index((FIXTURES / 'week_index.html').read_text(), '3GPP_TSG_RAN_WG2')
    first = items[0]
    assert (first.id, first.title, first.author) == (
        '8ee902d7.2611a', 'Re: [POST135b][000][Organizational] Schedule and Agenda', 'Diana Pani')
    assert first.date.isoformat() == '2026-11-05T00:47:26+00:00'
    assert first.link == 'https://list.etsi.org/scripts/wa.exe?A2=3GPP_TSG_RAN_WG2;8ee902d7.2611a'
    assert len(items) == 3


def test_weeks_are_by_day_of_month():
    assert listserv.week(date(2026, 11, 1)) == listserv.week(date(2026, 11, 7)) == '2611A'
    assert listserv.week(date(2026, 10, 8)) == '2610B'
    assert listserv.week(date(2026, 10, 31)) == '2610E'
    assert listserv.weeks(date(2026, 10, 27), date(2026, 11, 2)) == ['2610D', '2610E', '2611A']


def test_the_feed_parses_like_before():
    items = listserv.parse_feed(RAN1_FEED.read_text(encoding='utf-8'))
    assert items and all(item.id and item.link.startswith(listserv.ARCHIVE) for item in items)
    assert all(item.date is not None for item in items)


def test_a_control_character_in_a_subject_does_not_break_the_feed():
    # RAN2's feed of 5 Nov 2026: an ISO-2022-JP subject kept its raw ESC.
    xml = ('<?xml version="1.0"?><rss version="2.0"><channel><item><title>回复: [内\x1b$BIt]'
           '[POST135bis][504][XR] Open issues</title><link>https://list.etsi.org/scripts/wa.exe?A2=L;0a1b.2611a</link>'
           '<pubDate>Thu, 5 Nov 2026 01:00:00 +0000</pubDate></item></channel></rss>')
    [item] = listserv.parse_feed(xml)
    assert item.id == '0a1b.2611a' and '[POST135bis][504][XR]' in item.title
