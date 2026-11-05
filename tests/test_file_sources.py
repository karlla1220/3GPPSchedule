"""The common structure for copies of an input file: origins, mail parts, the built copy."""

import httpx
import pytest

from shared import file_sources, remote_files
from tests.listserv_archive import attachment_url

SYNC = 'https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN2/Agenda/'
FOLDER = 'https://www.3gpp.org/ftp/tsg_ran/WG2_RL2/TSGR2_136/Agenda/'


def ftp(version, url=SYNC, uploaded='2026-11-16 08:00'):
    name = f'R2_136_Schedule_v{version:02d}.docx'
    return {'name': name, 'url': url + name, 'uploaded_at': uploaded, 'listing': url, 'version': version}


def mail(version, sent, message_id='m1.2611c'):
    name = f'R2_136_Schedule_v{version:02d}.docx'
    return {'name': name, 'url': attachment_url(message_id, name), 'uploaded_at': sent, 'listing': 'a2',
            'origin': 'email', 'message_id': message_id, 'sent': sent, 'version': version}


def by_upload(entry):
    return entry['uploaded_at'], entry['name']


def best(*entries):
    """A WG's rule built from the parts: version, then copy_order."""
    return max(entries, key=lambda e: (e['version'], *file_sources.copy_order(e, by_upload)))


def test_an_ftp_copy_comes_before_a_mailed_one_of_the_same_version_whatever_the_times():
    for uploaded in ('2026-11-17 09:00', '2026-11-17 23:00'):
        assert file_sources.is_ftp(best(mail(10, '2026-11-17T10:00:00+00:00'), ftp(10, uploaded=uploaded)))
    # The version is the WG's and comes first: a newer mail beats an older upload.
    assert best(ftp(8), mail(10, '2026-11-17T10:00:00+00:00'))['origin'] == 'email'


def test_the_first_mail_of_a_version_comes_before_a_reply_that_attached_it_again():
    first = mail(10, '2026-11-17T10:00:00+00:00', 'chair.2611c')
    later = mail(10, '2026-11-17T12:00:00+00:00', 'reply.2611c')
    earlier_moment = mail(10, '2026-11-17T11:30:00+08:00', 'reply.2611c')  # 03:30 UTC
    assert best(later, first)['message_id'] == 'chair.2611c'
    assert best(first, earlier_moment)['message_id'] == 'reply.2611c'  # moments, not text


@pytest.mark.parametrize('sent', [None, 'not a date'])
def test_a_mail_without_a_date_comes_after_a_dated_one(sent):
    dated = mail(10, '2026-11-17T10:00:00+00:00', 'dated.2611c')
    assert best(mail(10, sent, 'undated.2611c'), dated)['message_id'] == 'dated.2611c'


def test_ftp_copies_keep_the_wg_order():
    late, live = ftp(11, FOLDER, '2026-08-29 21:10'), ftp(11, SYNC, '2026-08-27 20:49')
    assert best(late, live) is late
    live_first = lambda e: ('Meetings_3GPP_SYNC' in e['url'],)
    assert max([late, live], key=lambda e: (e['version'], *file_sources.copy_order(e, live_first))) is live


def test_only_a_vanished_built_copy_is_held_and_only_with_the_wgs_word():
    built = mail(10, '2026-11-17T10:00:00+00:00')
    yes, no = (lambda b, c: True), (lambda b, c: False)
    assert file_sources.hold_built(built, ftp(8), [ftp(8)], yes)
    assert not file_sources.hold_built(built, ftp(8), [ftp(8)], no)
    # Still listed: the WG's normal choice decides, not the hold.
    assert not file_sources.hold_built(built, ftp(8), [ftp(8), built], yes)
    assert not file_sources.hold_built(None, ftp(8), [], yes)


def test_a_mailed_build_is_fetched_again_and_must_be_the_same_file(tmp_path, monkeypatch):
    monkeypatch.setattr(remote_files, 'CACHE_DIR', tmp_path / 'cache')
    built = mail(10, '2026-11-17T10:00:00+00:00')
    served = {built['url']: b'v10'}

    def handler(request):
        url = str(request.url)
        return httpx.Response(200, content=served[url]) if url in served else httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        body, info = remote_files.fetch_file(http, built['url'])
        assert info['name'] == 'R2_136_Schedule_v10.docx' and body == b'v10'
        built.update(sha256=info['sha256'])
        served.clear()
        assert file_sources.built_body(built, http) == b'v10'  # stored: no request
        (tmp_path / 'cache').rename(tmp_path / 'lost')
        served[built['url']] = b'v10'
        assert file_sources.built_body(built, http) == b'v10'  # cache lost: from the archive again
        (tmp_path / 'cache').rename(tmp_path / 'lost2')
        served[built['url']] = b'changed'
        assert file_sources.built_body(built, http) is None  # different bytes are not the built file
    assert file_sources.built_body({**ftp(10), 'sha256': 'x'}) is None  # an unlisted FTP file has no second source


def test_the_manifest_record_says_which_mail_a_file_came_from():
    chosen = mail(10, '2026-11-17T10:00:00+00:00')
    record = file_sources.source_record(chosen, {'url': chosen['url'], 'sha256': 'abc', 'size': 3})
    assert record['origin'] == 'email' and record['message_id'] == 'm1.2611c' and record['sha256'] == 'abc'
    assert 'origin' not in file_sources.source_record(ftp(10), {'sha256': 'abc'})
