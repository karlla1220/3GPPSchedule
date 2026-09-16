"""Exercise fallback through the actual WG listing and download entry points."""
import ftplib
from contextlib import contextmanager

import httpx
import pytest

from working_groups.ran_plenary import sources
from working_groups.ran1 import downloader

CHAIR = sources.DEFAULTS['chair_url']
NAME = 'RAN#113 time plan v06.zip'
PLAN = b'PK-timeplan'
AGENDA = b'"1","Opening"\n'


class FakeFTP:
    calls = []

    def __init__(self, *args, **kwargs):
        self.calls.append(('connect', args, kwargs))
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def login(self): self.calls.append(('login',))
    def cwd(self, path): self.calls.append(('cwd', path))
    def voidcmd(self, cmd): return '200 OK'
    def retrlines(self, cmd, callback):
        callback(f'09-15-26  03:57PM {len(PLAN):20} {NAME}')
        callback('09-14-26  09:41AM       <DIR>          RAN113')
    def size(self, path): return len(AGENDA if path.endswith('.csv') else PLAN)
    def sendcmd(self, cmd): return '213 20260915155750'
    def retrbinary(self, cmd, callback, **kwargs):
        self.calls.append(('retr', cmd))
        callback(AGENDA if cmd.endswith('.csv') else PLAN)


@pytest.fixture
def ftp(monkeypatch):
    FakeFTP.calls = []
    monkeypatch.setattr(ftplib, 'FTP', FakeFTP)
    return FakeFTP


@pytest.mark.parametrize('failure', ['ssl', 526])
def test_plenary_bundle_recovers_https_failure_without_changing_source_identity(ftp, tmp_path, failure):
    def fail(req):
        if failure == 'ssl':
            raise httpx.ConnectError('[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate', request=req)
        return httpx.Response(failure)
    with httpx.Client(transport=httpx.MockTransport(fail)) as http:
        bundle = sources.fetch_bundle(sources.DEFAULTS, {}, http, tmp_path)
    assert bundle.timeplan == PLAN and bundle.agenda == AGENDA
    assert bundle.manifest['timeplan']['url'] == CHAIR + 'RAN%23113%20time%20plan%20v06.zip'
    assert bundle.manifest['timeplan']['uploaded_at'] == '2026-09-15 15:57'
    assert bundle.manifest['version'] == 6
    assert any(c[0] == 'retr' and NAME in c[1] for c in ftp.calls)


def test_ran1_listing_and_stream_download_recover(ftp, monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise httpx.ConnectError('SSL issuer missing')
    monkeypatch.setattr(httpx, 'get', fail)
    monkeypatch.setattr(httpx, 'stream', fail)
    monkeypatch.setattr(downloader.time, 'sleep', lambda _: None)
    files = downloader.list_remote_files(CHAIR.rstrip('/'))
    assert files[0]['name'] == NAME
    assert files[0]['uploaded_at'].isoformat() == '2026-09-15T15:57:00'
    folders = downloader.list_inbox_subfolders(CHAIR)
    assert [f['name'] for f in folders] == ['RAN113']
    # Raw download is enough here; archive extraction is tested independently.
    monkeypatch.setattr(downloader, '_validate_downloaded_file', lambda _: None)
    target = downloader.download_file(files[0]['url'], tmp_path / NAME)
    assert target.read_bytes() == PLAN


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    from shared import ftp_transport
    monkeypatch.setattr(ftp_transport.time, 'sleep', lambda _: None)


@pytest.mark.parametrize('url', [
    'https://portal.3gpp.org/api', 'https://example.org/ftp/file',
    'https://www.3gpp.org/ftp/file?token=secret',
    'https://user:secret@www.3gpp.org/ftp/file',
    'https://www.3gpp.org/ftp/file%0d%0aDELE%20other',
    'https://www.3gpp.org/ftp/../private',
    'http://www.3gpp.org/ftp/file',
])
def test_unrelated_or_unsafe_urls_never_fallback(ftp, url):
    from shared import ftp_transport
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(526))) as http:
        response = ftp_transport.get(url, http=http)
    assert response.status_code == 526
    assert ftp.calls == []


@pytest.mark.parametrize('status', [200, 304, 401, 403, 404, 429])
def test_ordinary_http_responses_do_not_use_ftp(ftp, status):
    from shared import ftp_transport
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status))) as http:
        response = ftp_transport.get(CHAIR, http=http, listing=True)
    assert response.status_code == status and ftp.calls == []


def test_https_retry_success_does_not_use_ftp(ftp):
    from shared import ftp_transport
    statuses = iter([526, 200])
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(next(statuses)))) as http:
        assert ftp_transport.get(CHAIR, http=http).status_code == 200
    assert ftp.calls == []


def test_failed_ftp_does_not_become_empty_success(ftp, monkeypatch):
    from shared import ftp_transport
    monkeypatch.setattr(ftp, 'login', lambda _: (_ for _ in ()).throw(ftplib.error_perm('530 denied')))
    with pytest.raises(httpx.ConnectError, match='FTP fallback failed.*530'):
        ftp_transport.ftp_response(CHAIR, listing=True)


@pytest.mark.parametrize('fault', ['short', 'oversize', 'changed', 'unknown_listing'])
def test_incomplete_or_unsupported_ftp_results_are_rejected(ftp, monkeypatch, fault):
    from shared import ftp_transport
    if fault == 'short':
        monkeypatch.setattr(ftp, 'retrbinary', lambda self, cmd, cb, **kw: cb(b'x'))
    elif fault == 'oversize':
        monkeypatch.setattr(ftp, 'size', lambda *args: 100_000_000)
    elif fault == 'changed':
        stamps = iter(['213 20260915155750', '213 20260915155751'])
        monkeypatch.setattr(ftp, 'sendcmd', lambda *args: next(stamps))
    else:
        monkeypatch.setattr(ftp, 'retrlines', lambda self, cmd, cb: cb('unrecognized listing'))
    with pytest.raises(httpx.ConnectError, match='FTP fallback failed'):
        ftp_transport.ftp_response(CHAIR + 'file.zip', listing=fault == 'unknown_listing')


def test_stream_closes_failed_http_response_and_preserves_bytes(ftp, monkeypatch):
    from shared import ftp_transport
    responses = []
    @contextmanager
    def failed_stream(*args, **kwargs):
        response = httpx.Response(526, request=httpx.Request('GET', CHAIR))
        responses.append(response)
        try: yield response
        finally: response.close()
    monkeypatch.setattr(httpx, 'stream', failed_stream)
    with ftp_transport.stream('GET', CHAIR + 'file.zip') as response:
        assert b''.join(response.iter_bytes()) == PLAN
    assert len(responses) == 2 and all(r.is_closed for r in responses)
    assert response.is_closed


def test_exception_inside_stream_consumer_does_not_trigger_ftp(ftp, monkeypatch):
    from shared import ftp_transport
    @contextmanager
    def good_stream(*args, **kwargs):
        yield httpx.Response(200, content=b'ok')
    monkeypatch.setattr(httpx, 'stream', good_stream)
    with pytest.raises(httpx.ReadError):
        with ftp_transport.stream('GET', CHAIR + 'file.zip'):
            raise httpx.ReadError('consumer failure')
    assert ftp.calls == []
