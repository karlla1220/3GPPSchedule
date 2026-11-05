"""RAN1 schedules mailed to the list: which document a mail stands in for, and what is kept."""

import json
from datetime import datetime, timezone

import httpx

from shared import email_sources, remote_files
from tests.listserv_archive import attachment_url
from working_groups.ran1 import downloader, main_history
from working_groups.ran1.models import ScheduleSource

SYNC = 'https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN1/Inbox/'
MAIN = 'Draft RAN1#126b online and offline schedules - v03.docx'
SOROUR = 'RAN1#126b Sorour sessions online and offline schedules - v02.docx'
HIROKI = 'RAN1#126bis schedule for Hiroki Adhoc2 sessions_v01.docx'


def ftp(folder, name, person=None, main=False):
    return ScheduleSource(folder_name=folder, person_name=person, is_main=main,
                          file_info={'name': name, 'url': SYNC + folder + '/' + name,
                                     'uploaded_at': datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc)})


def mail(name, sent='2026-10-13T09:00:00+00:00', message_id='a1.2610b'):
    return {'url': attachment_url(message_id, name), 'name': name, 'uploaded_at': sent,
            'listing': f'https://list.etsi.org/scripts/wa.exe?A2=3GPP_TSG_RAN_WG1;{message_id}',
            'origin': 'email', 'message_id': message_id, 'sent': sent}


def sources():
    return [ftp('Chair_notes', MAIN, main=True), ftp('Sorour_notes', SOROUR, 'Sorour'),
            ftp('Hiroki_notes', HIROKI, 'Hiroki')]


def test_a_newer_mailed_version_stands_in_for_its_document():
    chosen = downloader._with_mail(sources(), [
        mail('RAN1#126b online and offline schedules - v00.docx'),            # Main, released after the draft
        mail('RAN1#126b Sorour sessions online and offline schedules - v03.docx', message_id='b2.2610b'),
        mail('RAN1#126bis schedule for Hiroki Adhoc2 sessions_v01.docx', message_id='c3.2610b'),  # same version
    ])
    main, sorour, hiroki = chosen
    assert (main.is_main, main.folder_name, main.file_info['origin']) == (True, 'Chair_notes', 'email')
    assert main.file_info['uploaded_at'] == datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)
    # A name that says no person still lands on Sorour's document: by its name without the version.
    assert (sorour.person_name, sorour.file_info['name']) == ('Sorour', 'RAN1#126b Sorour sessions online and offline schedules - v03.docx')
    # At equal version the FTP copy stays.
    assert 'origin' not in hiroki.file_info and hiroki.file_info['name'] == HIROKI


def test_a_mail_that_matches_no_document_is_not_used():
    chosen = downloader._with_mail(sources(), [
        mail('RAN1#126b schedule for Younsun sessions - v05.docx'),       # nobody's document on FTP yet
        mail('RAN1#127 online and offline schedules - v09.docx'),          # another meeting
        mail('RAN1#126b online and offline schedules - v00 comments.docx'),  # not the same document
    ])
    assert all('origin' not in s.file_info for s in chosen)


def test_a_local_reference_is_never_replaced():
    local = ftp('extra_files', MAIN, main=True)
    local.local_path, local.origin = None, 'local'
    [kept] = downloader._with_mail([local], [mail('RAN1#126b online and offline schedules - v00.docx')])
    assert 'origin' not in kept.file_info


def test_the_state_keeps_the_mail_and_the_next_run_uses_it_again(tmp_path):
    [main] = downloader._with_mail([ftp('Chair_notes', MAIN, main=True)],
                                   [mail('RAN1#126b online and offline schedules - v00.docx')])
    path = tmp_path / 'state.json'
    downloader.save_schedule_state([main], path, email={'list': '3GPP_TSG_RAN_WG1', 'checked_through': '2026-10-13T10:00:00+00:00'})
    state = json.loads(path.read_text())
    [entry] = state['files']
    assert entry['uploaded_at'] == '2026-10-13T09:00:00+00:00' and entry['origin'] == 'email'
    assert entry['message_id'] == 'a1.2610b' and state['email']['checked_through'] == '2026-10-13T10:00:00+00:00'
    # The check job records the same triple, so nothing looks changed.
    assert downloader._state_entry(main) == entry
    # The list is not read (no feed, no cache): the built mail is still a candidate.
    files, record = downloader.mailed_schedule_files({'email_list': None}, state)
    assert (files, record) == ([], None)
    feedless = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    files, record = downloader.mailed_schedule_files({'email_list': '3GPP_TSG_RAN_WG1'}, state, http=feedless)
    assert [f['name'] for f in files] == ['RAN1#126b online and offline schedules - v00.docx']
    assert record['checked_through'] == '2026-10-13T10:00:00+00:00'  # resumes where the build had read
    [again] = downloader._with_mail([ftp('Chair_notes', MAIN, main=True)], files)
    assert again.file_info['origin'] == 'email'


def test_a_mailed_schedule_is_downloaded_through_the_shared_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(remote_files, 'CACHE_DIR', tmp_path / 'cache')
    entry = mail('RAN1#126b online and offline schedules - v00.docx')
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=b'PK mailed')
    monkeypatch.setattr(remote_files, 'client', lambda **k: httpx.Client(transport=httpx.MockTransport(handler)))
    source = ScheduleSource(folder_name='Chair_notes', person_name=None, is_main=True, file_info=entry)
    path = downloader.download_schedule_source(source, tmp_path / 'downloads')
    assert path.read_bytes() == b'PK mailed' and path.name == entry['name']
    # Again: from the cache, not the archive, even though the file is on disk.
    assert downloader.download_schedule_source(source, tmp_path / 'downloads') == path and len(calls) == 1


def test_only_schedules_of_a_known_meeting_are_taken_from_the_list():
    assert downloader.is_schedule_name('RAN1#126b online and offline schedules - v00.docx')
    assert not downloader.is_schedule_name('online and offline schedules - v00.docx')
    assert not downloader.is_schedule_name('RAN1#126b schedule.txt')
    assert not downloader.is_schedule_name('Chair notes RAN1#126b v01.docx')


def test_a_mailed_main_is_not_kept_in_the_main_history(tmp_path, monkeypatch):
    monkeypatch.setattr(main_history, 'HISTORY_DIR', tmp_path)

    class Cell:
        day, time_block_index, text = 'Monday', 0, '9.1'
    seen = main_history.record('ran1#126bis', [Cell()], store=False)
    assert seen == {'Monday_00': {'9.1'}} and not list(tmp_path.iterdir())
    main_history.record('ran1#126bis', [Cell()])
    assert list(tmp_path.iterdir())


def test_mail_state_is_per_wg():
    assert email_sources.state_path('ran1') != email_sources.state_path('ran2')
