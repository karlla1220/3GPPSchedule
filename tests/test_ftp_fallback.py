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
    def quit(self): self.calls.append(('quit',))
    def close(self): pass
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
def ftp(monkeypatch, tmp_path):
    monkeypatch.setattr('shared.remote_files.CACHE_DIR', tmp_path / 'remote-cache')
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


# The real page is ~35 KB with the sentence near the end.
FIREWALL = (b'<html><head><style>' + b' ' * 34000 + b'</style></head><body>Web Application Firewall '
            b'This transfer is blocked by a Web Application Firewall.</body></html>')


def test_firewall_403_goes_to_ftp_without_a_second_https_request(ftp):
    from shared import ftp_transport
    seen = []
    def blocked(request):
        seen.append(request)
        return httpx.Response(403, content=FIREWALL)
    with httpx.Client(transport=httpx.MockTransport(blocked)) as http:
        response = ftp_transport.get(CHAIR, http=http, listing=True)
    assert response.headers['x-3gpp-transport'] == 'ftp'
    assert NAME in response.text and len(seen) == 1


def test_firewall_403_stream_goes_to_ftp(ftp, monkeypatch):
    from shared import ftp_transport
    @contextmanager
    def blocked(method, url, **kwargs):
        yield httpx.Response(403, stream=httpx.ByteStream(FIREWALL), request=httpx.Request(method, url))
    monkeypatch.setattr(httpx, 'stream', blocked)
    with ftp_transport.stream('GET', CHAIR + 'RAN%23113%20time%20plan%20v06.zip') as response:
        assert response.read() == PLAN


def test_firewall_403_with_failed_ftp_names_the_firewall(ftp, monkeypatch):
    from shared import ftp_transport
    monkeypatch.setattr(ftp, 'login', lambda _: (_ for _ in ()).throw(ftplib.error_perm('530 denied')))
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(403, content=FIREWALL))) as http:
        with pytest.raises(httpx.ConnectError, match='firewall blocked HTTPS.*FTP fallback failed.*530'):
            ftp_transport.get(CHAIR, http=http, listing=True)


def test_requests_without_a_client_name_the_project(monkeypatch):
    from shared import ftp_transport
    sent = []
    def record(url, **kwargs):
        sent.append(kwargs.get('headers') or {})
        return httpx.Response(200, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'get', record)
    ftp_transport.get(CHAIR, listing=True)
    ftp_transport.get(CHAIR, headers={'If-None-Match': '"v1"'})
    ftp_transport.get('https://list.etsi.org/file', headers={'User-Agent': 'Mozilla/5.0'})
    assert [h.get('User-Agent') for h in sent] == [ftp_transport.USER_AGENT] * 2 + ['Mozilla/5.0']
    assert sent[1]['If-None-Match'] == '"v1"'


def test_one_ftp_login_serves_every_fallback_of_a_run(ftp):
    from shared import ftp_transport
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(526))) as http:
        ftp_transport.get(CHAIR, http=http, listing=True)
        ftp_transport.get(CHAIR + 'RAN%23113%20time%20plan%20v06.zip', http=http)
        ftp_transport.get(sources.DEFAULTS['agenda_url'], http=http)
    assert [c[0] for c in ftp.calls].count('login') == 1
    ftp_transport.reset()
    assert ftp.calls[-1] == ('quit',)


def test_a_stale_ftp_session_is_replaced_once(ftp, monkeypatch):
    from shared import ftp_transport
    ftp_transport.ftp_response(CHAIR, listing=True)
    ftp_transport._session.retrlines = lambda *a: (_ for _ in ()).throw(EOFError())
    assert NAME in ftp_transport.ftp_response(CHAIR, listing=True).text
    assert [c[0] for c in ftp.calls].count('login') == 2


def test_https_that_failed_is_not_repeated_in_the_same_run(ftp):
    from shared import ftp_transport
    seen = []
    def handler(request):
        seen.append(str(request.url))
        if '/Chair/' in str(request.url):
            return httpx.Response(403, content=FIREWALL)
        return httpx.Response(200, text='ok')
    other = 'https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN1/Inbox/'
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        ftp_transport.get(CHAIR, http=http, listing=True)
        # A file in the blocked folder goes straight to FTP; another folder still uses HTTPS.
        ftp_transport.get(CHAIR + 'RAN%23113%20time%20plan%20v06.zip', http=http)
        assert ftp_transport.get(other, http=http).text == 'ok'
    assert seen == [CHAIR, other]
    ftp_transport.reset()
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        ftp_transport.get(CHAIR, http=http, listing=True)
    assert seen[-1] == CHAIR


def test_a_connection_failure_sends_the_rest_of_the_host_to_ftp(ftp):
    from shared import ftp_transport
    seen = []
    def down(request):
        seen.append(str(request.url))
        raise httpx.ConnectError('connection refused', request=request)
    with httpx.Client(transport=httpx.MockTransport(down)) as http:
        ftp_transport.get(CHAIR, http=http, listing=True)
        ftp_transport.get(sources.DEFAULTS['agenda_url'], http=http)
    assert seen == [CHAIR, CHAIR]


def test_a_run_without_failures_never_opens_ftp(ftp, monkeypatch, tmp_path):
    from shared import ftp_transport, remote_files
    monkeypatch.setattr(remote_files, 'CACHE_DIR', tmp_path / 'cache')
    def ok(request):
        if request.headers.get('if-none-match') == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=b'body', headers={'etag': '"v1"'})
    @contextmanager
    def streamed(method, url, **kwargs):
        yield httpx.Response(200, stream=httpx.ByteStream(b'body'), request=httpx.Request(method, url))
    monkeypatch.setattr(httpx, 'stream', streamed)
    with httpx.Client(transport=httpx.MockTransport(ok)) as http:
        remote_files.get_listing(CHAIR, http=http)
        remote_files.fetch_file(http, CHAIR + 'a.zip')
        remote_files.fetch_file(http, CHAIR + 'a.zip')  # revalidated: 304
        for status in (403, 404):  # an ordinary 403 is an answer, not a failure to route around
            with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status))) as other:
                assert ftp_transport.get(CHAIR + 'missing/', http=other).status_code == status
    with ftp_transport.stream('GET', CHAIR + 'b.zip') as response:
        assert response.read() == b'body'
    remote_files.new_run()
    ftp_transport.close_session()
    assert ftp.calls == []


def test_an_unrecognised_403_is_described_in_the_log(ftp, caplog):
    from shared import ftp_transport
    page = (b'<html><head><style>body { color: red }</style><script>var x = 1;</script></head>'
            b'<body><h1>Access denied</h1><p>Ray ID: 1234</p></body></html>')
    answer = httpx.Response(403, content=page, headers={'server': 'cloudflare', 'cf-ray': 'a48d-IAD'})
    with httpx.Client(transport=httpx.MockTransport(lambda _: answer)) as http:
        assert ftp_transport.get(CHAIR, http=http, listing=True).status_code == 403
    assert ftp.calls == []
    [record] = [r for r in caplog.records if '403 without the firewall page' in r.getMessage()]
    message = record.getMessage()
    assert CHAIR in message and "'server': 'cloudflare'" in message and "'cf-ray': 'a48d-IAD'" in message
    assert 'Access denied Ray ID: 1234' in message and 'color' not in message and 'var x' not in message
