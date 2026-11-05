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


def version(entry):
    return entry['version']


def choose(*entries):
    return file_sources.choose(entries, version)


def test_a_newer_mailed_version_wins_over_an_older_upload():
    assert choose(ftp(8), mail(10, '2026-11-17T10:00:00+00:00'))['origin'] == 'email'


def test_at_equal_version_the_upload_wins_whatever_the_times():
    # The mail came first and the upload later, or the other way round: FTP is the record.
    for uploaded in ('2026-11-17 09:00', '2026-11-17 23:00'):
        chosen = choose(mail(10, '2026-11-17T10:00:00+00:00'), ftp(10, uploaded=uploaded))
        assert file_sources.origin(chosen) == 'ftp'


def test_the_first_mail_of_a_version_wins_over_a_reply_that_attached_it_again():
    first = mail(10, '2026-11-17T10:00:00+00:00', 'chair.2611c')
    reply = mail(10, '2026-11-17T11:30:00+08:00', 'reply.2611c')  # 03:30 UTC: earlier on the clock face only
    later = mail(10, '2026-11-17T12:00:00+00:00', 'reply.2611c')
    assert choose(later, first)['message_id'] == 'chair.2611c'
    assert choose(first, reply)['message_id'] == 'reply.2611c'  # compared as moments, not as text


def test_ftp_copies_keep_the_live_copy_first():
    assert choose(ftp(11, FOLDER, '2026-08-29 21:10'), ftp(11, SYNC, '2026-08-27 20:49'))['listing'] == SYNC


def test_a_wg_can_order_ftp_copies_its_own_way():
    later = ftp(11, FOLDER, '2026-08-29 21:10')
    by_time = file_sources.choose([later, ftp(11, SYNC, '2026-08-27 20:49')], version,
                                  lambda e: (e['uploaded_at'], e['name']))
    assert by_time is later


def test_the_built_copy_stays_until_something_listed_outranks_it():
    built_mail = mail(10, '2026-11-17T10:00:00+00:00')
    # The mail fell out of the feed; only the older upload is listed.
    assert file_sources.keep_built(built_mail, ftp(8), [ftp(8)], version)
    # The upload of the same version replaces it.
    assert not file_sources.keep_built(built_mail, ftp(10), [ftp(10)], version)
    # A built upload is not replaced by the mailed copy of its version, nor by another upload of it.
    built_ftp = ftp(10)
    assert file_sources.keep_built(built_ftp, mail(10, '2026-11-17T10:00:00+00:00'), [], version)
    assert file_sources.keep_built(built_ftp, ftp(10, FOLDER), [ftp(10, FOLDER)], version)
    # Still listed: the normal choice applies.
    assert not file_sources.keep_built(built_ftp, ftp(10), [ftp(10)], version)
    assert not file_sources.keep_built(None, ftp(10), [], version)


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
        # Stored: no request.
        served.clear()
        assert file_sources.built_body(built, http) == b'v10'
        # The CI cache lost it: fetched again from the archive.
        (tmp_path / 'cache').rename(tmp_path / 'lost')
        served[built['url']] = b'v10'
        assert file_sources.built_body(built, http) == b'v10'
        # Different bytes are not the built file.
        (tmp_path / 'cache').rename(tmp_path / 'lost2')
        served[built['url']] = b'changed'
        assert file_sources.built_body(built, http) is None
    # An FTP file that is no longer listed has no second source.
    assert file_sources.built_body({**ftp(10), 'sha256': 'x'}) is None


def test_the_manifest_record_says_which_mail_a_file_came_from():
    chosen = mail(10, '2026-11-17T10:00:00+00:00')
    record = file_sources.source_record(chosen, {'url': chosen['url'], 'sha256': 'abc', 'size': 3})
    assert record['origin'] == 'email' and record['message_id'] == 'm1.2611c' and record['sha256'] == 'abc'
    assert 'origin' not in file_sources.source_record(ftp(10), {'sha256': 'abc'})


@pytest.mark.parametrize('sent', [None, 'not a date'])
def test_a_mail_without_a_date_loses_to_a_dated_one(sent):
    dated = mail(10, '2026-11-17T10:00:00+00:00', 'dated.2611c')
    assert choose(mail(10, sent, 'undated.2611c'), dated)['message_id'] == 'dated.2611c'


def test_a_later_upload_of_the_built_version_replaces_it_when_the_wg_says_so():
    # SA2: "-r14 - Friday plan" follows "-r14". Without a tiebreak another upload of the
    # built version never replaces it (RAN2's archive copy of a sync file).
    built = ftp(14, SYNC, '2026-11-17 08:00')
    later = {**ftp(14, FOLDER, '2026-11-17 18:00'), 'name': 'S2-x - r14 - Friday plan.docx'}
    uploaded = lambda entry: entry['uploaded_at']
    assert file_sources.keep_built(built, later, [later], version)
    assert not file_sources.keep_built(built, later, [later], version, uploaded)
    assert file_sources.keep_built({**built, 'uploaded_at': '2026-11-17 19:00'}, later, [later], version, uploaded)
    # Mail never takes part in the tiebreak: the rank decides.
    assert file_sources.keep_built(built, mail(14, '2026-11-17T20:00:00+00:00'), [], version, uploaded)
