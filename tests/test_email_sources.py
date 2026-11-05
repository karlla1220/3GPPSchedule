import json
import shutil

from shared import email_sources, listserv
from tests.listserv_archive import LIST, Archive, when

NOW = when('2026-11-05T12:00:00+00:00')


def accept(name):
    return name.startswith('R2_') and '_Schedule_v' in name


def mails():
    return {
        'a1.2611a': {'title': 'Re: [Post135bis][404][AIoT] Running CR', 'date': '2026-11-05T09:00:00+00:00',
                     'files': {'38391 CR v12.docx': b'cr'}},
        'a2.2611a': {'title': 'Re: [POST135b][000][Organizational] Schedule and Agenda',
                     'date': '2026-11-05T10:00:00+00:00',
                     'files': {'R2_136_Agenda_v02.docx': b'agenda', 'R2_136_Schedule_v00.docx': b'v00'}},
        'a3.2611a': {'title': 'Lunch', 'date': '2026-11-05T11:00:00+00:00'},
    }


def scan(archive, **kwargs):
    """One run: pages read by an earlier run are asked for again."""
    listserv.new_run()
    with archive.client() as http:
        return email_sources.scan('ran2', LIST, accept, http, now=kwargs.pop('now', NOW), sleep=lambda _: None,
                                  **kwargs)


def test_each_new_mail_is_opened_once_and_only_accepted_files_are_kept():
    archive = Archive(mails())
    found = scan(archive)
    assert sorted(archive.opened()) == ['a1.2611a', 'a2.2611a', 'a3.2611a']
    [schedule] = found.files
    assert (schedule['name'], schedule['origin'], schedule['message_id']) == ('R2_136_Schedule_v00.docx', 'email', 'a2.2611a')
    assert schedule['sent'] == '2026-11-05T10:00:00+00:00' and schedule['listing'].endswith(';a2.2611a')
    assert found.checked_through == '2026-11-05T11:00:00+00:00'
    # The next run sees the same feed and opens nothing, while the file stays found.
    archive.seen.clear()
    again = scan(archive)
    assert archive.opened() == [] and [f['name'] for f in again.files] == ['R2_136_Schedule_v00.docx']


def test_mails_over_the_run_limit_wait_and_hold_the_reading_point():
    archive = Archive(mails())
    found = scan(archive, max_opens=1)
    assert archive.opened() == ['a1.2611a']
    # Read through just before the first mail left for the next run.
    assert found.checked_through == '2026-11-05T09:59:59+00:00'
    assert found.files == [] and any('left for the next run' in note for note in found.notes)
    archive.seen.clear()
    found = scan(archive, max_opens=5)
    assert sorted(archive.opened()) == ['a2.2611a', 'a3.2611a'] and len(found.files) == 1


def test_without_the_cache_reading_resumes_from_the_manifest(tmp_path):
    archive = Archive(mails())
    scan(archive)
    shutil.rmtree(email_sources.STATE_ROOT)
    archive.mails['a4.2611a'] = {'title': 'Updated Schedule (V01)', 'date': '2026-11-07T08:00:00+00:00',
                                 'files': {'R2_136_Schedule_v01.docx': b'v01'}}
    archive.seen.clear()
    # The manifest says the build had read through 05 Nov 11:00. A day before that
    # is looked at again (sender clocks), not the whole feed.
    found = scan(archive, checked_through='2026-11-05T11:00:00+00:00', now=when('2026-11-07T09:00:00+00:00'))
    assert sorted(archive.opened()) == ['a1.2611a', 'a2.2611a', 'a3.2611a', 'a4.2611a']
    archive.seen.clear()
    found = scan(archive, checked_through='2026-11-06T11:00:00+00:00', now=when('2026-11-07T09:00:00+00:00'))
    assert archive.opened() == [] and {f['name'] for f in found.files} == {
        'R2_136_Schedule_v00.docx', 'R2_136_Schedule_v01.docx'}


def test_mail_older_than_a_day_before_the_reading_point_is_not_opened_again():
    archive = Archive(mails())
    found = scan(archive, checked_through='2026-11-06T12:00:00+00:00')
    assert archive.opened() == [] and found.checked_through == '2026-11-06T12:00:00+00:00'


def test_a_gap_after_the_feed_is_read_from_the_weekly_indexes():
    archive = Archive(mails(), feed=['a3.2611a'])  # more mail arrived than the feed shows
    found = scan(archive, checked_through='2026-11-05T08:00:00+00:00')
    assert any('A1=ind2611A' in url for url in archive.seen)
    assert sorted(archive.opened()) == ['a1.2611a', 'a2.2611a', 'a3.2611a'] and len(found.files) == 1
    # A weekly index that cannot be read keeps the reading point where it was.
    email_sources.state_path('ran2').unlink()
    archive.down.add('https://list.etsi.org/scripts/wa.exe?A1=ind2611A&L=3GPP_TSG_RAN_WG2')
    archive.mails['a0.2611a'] = {'title': 'x', 'date': '2026-11-01T08:00:00+00:00'}
    found = scan(archive, checked_through='2026-11-01T07:00:00+00:00')
    assert found.checked_through == '2026-11-01T07:00:00+00:00'
    assert any('not listed' in note for note in found.notes)


def test_an_unreadable_list_keeps_the_files_found_before():
    archive = Archive(mails())
    scan(archive)
    archive.down.add('https://list.etsi.org/scripts/wa.exe?RSS&L=3GPP_TSG_RAN_WG2&v=2.0&LIMIT=100')
    found = scan(archive)
    assert [f['name'] for f in found.files] == ['R2_136_Schedule_v00.docx']
    assert found.checked_through == '2026-11-05T11:00:00+00:00' and 'unreadable' in found.notes[0]


def test_a_mail_whose_page_fails_is_opened_next_run():
    archive = Archive(mails())
    archive.down.add('https://list.etsi.org/scripts/wa.exe?A2=3GPP_TSG_RAN_WG2;a2.2611a')
    found = scan(archive)
    assert found.files == [] and found.checked_through == '2026-11-05T09:59:59+00:00'
    archive.down.clear()
    assert len(scan(archive).files) == 1


def test_the_cache_keeps_recent_mails_and_found_files_only():
    archive = Archive(mails())
    scan(archive)
    later = when('2026-11-08T12:00:00+00:00')
    archive.mails = {'b1.2611b': {'title': 'x', 'date': '2026-11-08T11:00:00+00:00'}}
    scan(archive, now=later)
    state = json.loads(email_sources.state_path('ran2').read_text())
    # Mails older than a day before the reading point are forgotten; the found file is not.
    assert list(state['opened']) == ['b1.2611b']
    assert [f['name'] for f in state['files']] == ['R2_136_Schedule_v00.docx']
    assert state['list'] == LIST and state['checked_through'] == '2026-11-08T11:00:00+00:00'
    # Another list starts afresh.
    assert email_sources.load('ran2', '3GPP_TSG_RAN_WG1')['files'] == []
