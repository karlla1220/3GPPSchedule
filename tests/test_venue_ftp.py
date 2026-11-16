"""A sync folder is read from the meeting's venue FTP first, and from the sync folder when that fails."""
import ftplib
import json
import logging

import httpx
import pytest

from shared import ftp_transport, remote_files

SYNC = 'https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN1/Inbox/Chair_notes/'
NAME = 'Chair notes v13.docx'
BODY = b'PK-venue'
PASSWORD = 'not-in-logs'


class Venue:
    """The venue's IIS FTP: TLS, an account, UTC DOS rows (RAN1#127)."""
    calls = []
    fail = None  # 'connect', 'login', 'missing'

    def __init__(self, host, timeout=None):
        self.calls.append(('connect', host))
        if self.fail == 'connect':
            raise OSError('timed out')
    def login(self, user, password):
        self.calls.append(('login', user))
        if self.fail == 'login':
            raise ftplib.error_perm('530 User cannot log in.')
    def prot_p(self): self.calls.append(('prot_p',))
    def quit(self): pass
    def close(self): pass
    def voidcmd(self, cmd): return '200 OK'
    def sendcmd(self, cmd):
        self.calls.append(('cmd', cmd))
        return '213 20261116112800' if cmd.startswith('MDTM') else '200 OK'
    def cwd(self, path):
        self.calls.append(('cwd', path))
        if self.fail == 'missing':
            raise ftplib.error_perm('550 The system cannot find the file specified.')
    def retrlines(self, cmd, callback):
        callback(f'11-16-26  11:28AM {len(BODY):20} {NAME}')
    def size(self, path): return len(BODY)
    def retrbinary(self, cmd, callback, **kwargs):
        self.calls.append(('retr', cmd))
        callback(BODY)


@pytest.fixture
def venue(monkeypatch, tmp_path):
    monkeypatch.setattr(remote_files, 'CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(ftplib, 'FTP_TLS', Venue)
    monkeypatch.setenv('VENUE_FTP', json.dumps(
        {'RAN1': {'host': 'venue.example', 'user': 'meeting', 'password': PASSWORD}}))
    Venue.calls, Venue.fail = [], None
    return Venue


def sync_folder(seen):
    def answer(request):
        seen.append(str(request.url))
        return httpx.Response(200, text=f'<table><tr><td><a class="file" href="{SYNC}old.docx">old.docx</a>'
                                        '</td><td>2026/11/16 8:50</td></tr></table>')
    return httpx.Client(transport=httpx.MockTransport(answer))


def test_venue_listing_and_file_come_first_under_the_sync_url(venue):
    seen = []
    with sync_folder(seen) as http:
        listing = remote_files.get_listing(SYNC, http=http)
        [entry] = remote_files.listing_entries(listing.text, SYNC)
        body, info = remote_files.fetch_file(http, entry['url'])
    assert seen == []
    assert entry['url'] == SYNC + 'Chair%20notes%20v13.docx' and entry['uploaded_at'] == '2026-11-16 11:28'
    assert body == BODY and info['url'] == entry['url']
    assert ('cwd', '/RAN/RAN1/Inbox/Chair_notes/') in venue.calls and ('prot_p',) in venue.calls
    assert ('cmd', 'OPTS UTF8 ON') in venue.calls
    assert [c for c in venue.calls if c[0] == 'connect'] == [('connect', 'venue.example')]


@pytest.mark.parametrize('fail', ['connect', 'login', 'missing'])
def test_venue_failure_reads_the_sync_folder(venue, fail, caplog):
    venue.fail = fail
    seen = []
    with caplog.at_level(logging.WARNING), sync_folder(seen) as http:
        first = ftp_transport.get(SYNC, http=http, listing=True)
        second = ftp_transport.get(SYNC + 'Sub/', http=http, listing=True)
    assert 'old.docx' in first.text and 'old.docx' in second.text and len(seen) == 2
    connects = [c for c in venue.calls if c[0] == 'connect']
    # A venue that cannot be reached or logged into is not asked again in the run; a missing path is per path.
    assert len(connects) == 1
    assert len([c for c in venue.calls if c[0] == 'cwd']) == (2 if fail == 'missing' else 0)
    assert PASSWORD not in caplog.text and 'reading the sync folder' in caplog.text


@pytest.mark.parametrize('url', [
    'https://www.3gpp.org/ftp/tsg_ran/WG1_RL1/TSGR1_127/Inbox/',
    'https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN2/Agenda/',
])
def test_other_folders_and_wgs_never_ask_the_venue(venue, url):
    seen = []
    with sync_folder(seen) as http:
        ftp_transport.get(url, http=http, listing=True)
    assert venue.calls == [] and seen == [url]


def test_root_and_bad_settings(venue, monkeypatch):
    monkeypatch.setenv('VENUE_FTP', json.dumps(
        {'RAN1': {'host': 'h', 'user': 'u', 'password': 'p', 'root': '/R1/'}}))
    assert ftp_transport._venue('/Meetings_3GPP_SYNC/RAN1/Inbox/x.zip')[1] == '/R1/Inbox/x.zip'
    for bad in ('not json', '[]', json.dumps({'RAN1': {'host': 'h'}})):
        monkeypatch.setenv('VENUE_FTP', bad)
        assert ftp_transport._venue('/Meetings_3GPP_SYNC/RAN1/Inbox/') is None
