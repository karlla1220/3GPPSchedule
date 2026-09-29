"""Exercise the shared file interface across callers and fresh CI processes."""
import ftplib
from pathlib import Path
import shutil

import httpx
import pytest

from shared import remote_files

URL = 'https://www.3gpp.org/ftp/test/schedule.zip'


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('shared.ftp_transport.time.sleep', lambda _: None)


def test_fresh_caller_reuses_disk_body_after_http_304():
    requests = []
    def handler(request):
        requests.append(request)
        if request.headers.get('if-none-match') == 'v1':
            return httpx.Response(304)
        return httpx.Response(200, content=b'first', headers={'etag': 'v1'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        first, _ = remote_files.fetch_file(http, URL)
        second, info = remote_files.fetch_file(http, URL)
    assert first == second == b'first'
    assert info['size'] == 5
    assert len(requests) == 2
    assert requests[1].headers['if-none-match'] == 'v1'


def test_cache_restored_into_fresh_checkout_reuses_last_modified(monkeypatch, tmp_path):
    stamp = 'Tue, 15 Sep 2026 15:57:50 GMT'
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, content=b'first', headers={'last-modified': stamp}))) as http:
        remote_files.fetch_file(http, URL)
    checkout = tmp_path / 'fresh-checkout'
    shutil.copytree(Path('.cache'), checkout / '.cache')
    monkeypatch.chdir(checkout)
    calls = []
    def handler(request):
        calls.append(request)
        assert request.headers['if-modified-since'] == stamp
        return httpx.Response(304)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        assert remote_files.fetch_file(http, URL)[0] == b'first'
    assert len(calls) == 1


def test_same_url_update_and_corrupt_cache_require_new_body():
    version = 'one'
    requests = []
    def handler(request):
        requests.append(request)
        return (httpx.Response(304) if request.headers.get('if-none-match') == version
                else httpx.Response(200, content=version.encode(), headers={'etag': version}))
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        remote_files.fetch_file(http, URL)
        version = 'two'
        assert remote_files.fetch_file(http, URL)[0] == b'two'
        for path in remote_files.CACHE_DIR.rglob('*.bin'):
            path.write_bytes(b'corrupt')
        assert remote_files.fetch_file(http, URL)[0] == b'two'
    assert len(requests) == 3
    assert 'if-none-match' not in requests[-1].headers


class FTP:
    body = b'first'
    stamp = '213 20260915155750'
    downloads = 0
    def __init__(self, *a, **kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def login(self): pass
    def voidcmd(self, cmd): pass
    def size(self, path): return len(self.body)
    def sendcmd(self, cmd): return self.stamp
    def retrbinary(self, cmd, callback, **kw):
        type(self).downloads += 1
        callback(self.body)


def test_ftp_checks_size_and_date_before_retr_across_callers(monkeypatch):
    monkeypatch.setattr(ftplib, 'FTP', FTP)
    monkeypatch.setattr(FTP, 'downloads', 0)
    monkeypatch.setattr(FTP, 'body', b'first')
    monkeypatch.setattr(FTP, 'stamp', '213 20260915155750')
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as http:
        assert remote_files.fetch_file(http, URL)[0] == b'first'
        assert remote_files.fetch_file(http, URL)[0] == b'first'
        assert FTP.downloads == 1
        FTP.body = b'longer body'
        assert remote_files.fetch_file(http, URL)[0] == b'longer body'
        assert FTP.downloads == 2
        FTP.stamp = '213 20260915155751'
        assert remote_files.fetch_file(http, URL)[0] == b'longer body'
        assert FTP.downloads == 3


def test_failure_does_not_overwrite_successful_cache():
    with httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b'first', headers={'etag': 'v1'}))) as http:
        remote_files.fetch_file(http, URL)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        with pytest.raises(httpx.HTTPStatusError):
            remote_files.fetch_file(http, URL)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(304))) as http:
        assert remote_files.fetch_file(http, URL)[0] == b'first'


def test_ran1_and_plenary_share_one_body_and_revalidate_existing_destination(monkeypatch, tmp_path):
    from working_groups.ran1 import downloader
    from working_groups.ran1.agenda_descriptions import TdocXlsx, download_tdoc_xlsx
    from working_groups.ran_plenary import sources
    version = 'first'
    transfers = []
    def handler(request):
        if request.headers.get('if-none-match') == version:
            return httpx.Response(304)
        transfers.append(version)
        return httpx.Response(200, content=version.encode(), headers={'etag': version})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        monkeypatch.setattr(httpx, 'get', http.get)
        source = downloader.ScheduleSource(folder_name='Chair', person_name=None, is_main=True, file_info={'name': 'schedule.zip', 'url': URL})
        monkeypatch.setattr(downloader, 'download_and_resolve', downloader.download_file)
        path = downloader.download_schedule_source(source, tmp_path / 'downloads')
        assert sources.fetch_file(http, URL)[0] == path.read_bytes() == b'first'
        assert download_tdoc_xlsx(TdocXlsx('input.xlsx', URL), tmp_path / 'tdoc').read_bytes() == b'first'
        assert transfers == ['first']
        version = 'second'
        assert downloader.download_schedule_source(source, tmp_path / 'downloads').read_bytes() == b'second'
        assert transfers == ['first', 'second']


def test_outage_retry_budget_is_shared_and_error_page_is_not_cached(monkeypatch):
    from working_groups.ran1 import downloader
    calls = []
    def fail(*args, **kwargs):
        calls.append('ftp')
        raise ftplib.error_temp('421 unavailable')
    def handler(request):
        calls.append('http')
        return httpx.Response(200, content=b"Our services aren't available right now")
    monkeypatch.setattr(ftplib, 'FTP', fail)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        monkeypatch.setattr(httpx, 'get', http.get)
        with pytest.raises(httpx.ConnectError):
            downloader.list_remote_files(URL.rsplit('/', 1)[0])
    assert calls == ['http', 'http', 'ftp']
    assert not remote_files.CACHE_DIR.exists()


def test_3gpp_extra_file_revalidates_instead_of_trusting_committed_copy(monkeypatch, tmp_path):
    from working_groups.ran1 import downloader
    url = URL.replace('.zip', '.docx')
    cached = tmp_path / 'extra'
    cached.mkdir()
    (cached / 'schedule.docx').write_bytes(b'old')
    state = {'files': {url: {'filename': 'schedule.docx', 'sha256': remote_files._digest(b'old')}}}
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, content=b'new', headers={'etag': 'v2'}))) as http:
        monkeypatch.setattr(httpx, 'get', http.get)
        changed, new_state = downloader.check_external_files(
            [{'url': url}], state, cache_dir=cached, staging_dir=tmp_path / 'transfer')
    assert changed
    assert new_state['files'][url]['sha256'] == remote_files._digest(b'new')
    assert (tmp_path / 'transfer' / 'schedule.docx').read_bytes() == b'new'


def test_cached_stream_preserves_server_filename(monkeypatch):
    disposition = 'attachment; filename="chair-schedule.docx"'
    def handler(request):
        if request.headers.get('if-none-match'):
            return httpx.Response(304)
        return httpx.Response(200, content=b'document', headers={
            'etag': 'v1', 'content-disposition': disposition})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        monkeypatch.setattr(httpx, 'get', http.get)
        for _ in range(2):
            with remote_files.stream('GET', URL) as response:
                assert response.headers['content-disposition'] == disposition
                assert response.content == b'document'
