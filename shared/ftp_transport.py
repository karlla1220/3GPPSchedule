"""HTTPS first; anonymous FTP recovery for the public 3GPP file tree only.

FTP is unencrypted. Never forward HTTP credentials, headers or query strings.
The response retains the HTTPS identity so a transport change is not an update.
"""
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
from email.utils import format_datetime
import ftplib
from html import escape
import logging
import re
import time
from urllib.parse import quote, unquote, urlsplit

import httpx

_RETRY_STATUS = {500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526}
_MAX_BYTES = 64 * 1024 * 1024
_MAX_SECONDS = 180
# The live Microsoft FTP server advertises SIZE/MDTM, but not MLSD.
_IIS_ROW = re.compile(r'^(\d{2}-\d{2}-\d{2,4})\s+(\d{2}:\d{2}[AP]M)\s+(<DIR>|\d+)\s+(.+)$')


def ftp_path(url):
    """Return a decoded, command-safe public FTP path, or None."""
    parsed = urlsplit(str(url))
    if (parsed.scheme != 'https' or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.query or parsed.fragment):
        return None
    if parsed.hostname == 'www.3gpp.org' and parsed.path.startswith('/ftp/'):
        path = unquote(parsed.path[4:])
    elif parsed.hostname == 'ftp.3gpp.org':
        path = unquote(parsed.path)
    else:
        return None
    if not path.startswith('/') or any(ord(c) < 32 or ord(c) == 127 for c in path):
        return None
    if any(part in ('.', '..') for part in path.split('/')):
        return None
    return path


def _retryable(error):
    return (isinstance(error, httpx.TransportError)
            or isinstance(error, httpx.HTTPStatusError)
            and error.response.status_code in _RETRY_STATUS)


def _check_status(response):
    # Keep 304 and other non-fallback responses for the existing callers.
    if response.status_code in _RETRY_STATUS:
        response.raise_for_status()


def _directory(ftp, path, url, deadline):
    ftp.cwd(path)
    rows = []
    def collect(line):
        if time.monotonic() > deadline or len(rows) >= 10000:
            raise ValueError('FTP listing exceeds time or entry limit')
        match = _IIS_ROW.fullmatch(line)
        if not match:
            raise ValueError(f'Unsupported FTP listing row: {line[:120]}')
        date, clock, size, name = match.groups()
        if name in ('.', '..'):
            return
        if '/' in name or '\\' in name or any(ord(c) < 32 for c in name):
            raise ValueError('Unsafe FTP listing name')
        fmt = '%m-%d-%Y %I:%M%p' if len(date) == 10 else '%m-%d-%y %I:%M%p'
        modified = datetime.strptime(f'{date} {clock}', fmt).strftime('%Y/%m/%d %H:%M')
        href = str(url).rstrip('/') + '/' + quote(name, safe='') + ('/' if size == '<DIR>' else '')
        # Matches both existing RAN1 table readers and plenary link selection.
        rows.append(f'<tr><td></td><td></td><td><a href="{escape(href, quote=True)}">'
                    f'{escape(name)}</a></td><td>{modified}</td><td>{escape(size)}</td></tr>')
    ftp.retrlines('LIST', collect)
    return ('<table>' + ''.join(rows) + '</table>').encode(), {'content-type': 'text/html; charset=utf-8'}


def _file(ftp, path, deadline):
    ftp.voidcmd('TYPE I')
    size = ftp.size(path)
    stamp = ftp.sendcmd('MDTM ' + path)
    if size is None or size <= 0 or size > _MAX_BYTES:
        raise ValueError('Empty or excessive FTP file')
    data = bytearray()
    def collect(chunk):
        if time.monotonic() > deadline or len(data) + len(chunk) > min(size, _MAX_BYTES):
            raise ValueError('FTP download exceeds time or size limit')
        data.extend(chunk)
    ftp.retrbinary('RETR ' + path, collect, blocksize=65536)
    if len(data) != size or ftp.size(path) != size or ftp.sendcmd('MDTM ' + path) != stamp:
        raise ValueError('FTP file changed or was truncated during download')
    modified = datetime.strptime(stamp.removeprefix('213 ').split('.')[0], '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
    return bytes(data), {'content-type': 'application/octet-stream',
                         'last-modified': format_datetime(modified, usegmt=True)}


def ftp_response(url, *, listing=False):
    """Read a bounded FTP response. Also used by the network diagnostic."""
    path = ftp_path(url)
    if path is None:
        raise ValueError('FTP fallback requires a public 3GPP HTTPS file URL')
    request = httpx.Request('GET', url)
    deadline = time.monotonic() + _MAX_SECONDS
    try:
        with ftplib.FTP('ftp.3gpp.org', timeout=20) as ftp:
            ftp.login()
            body, headers = (_directory(ftp, path, url, deadline) if listing
                             else _file(ftp, path, deadline))
    except (ftplib.Error, OSError, ValueError) as exc:
        raise httpx.ConnectError(f'FTP fallback failed for {url}: {exc}', request=request) from exc
    logging.getLogger(__name__).warning('3GPP FTP fallback: %s (%d bytes)', url, len(body))
    return httpx.Response(200, content=body, headers={**headers, 'x-3gpp-transport': 'ftp'}, request=request)


def get(url, *, listing=False, http=None, **kwargs):
    """Retry eligible HTTPS once, then use FTP; preserve normal HTTP semantics."""
    request = http.get if http is not None else httpx.get
    if ftp_path(url) is None:
        return request(url, **kwargs)
    for attempt in range(2):
        try:
            response = request(url, **kwargs)
            try:
                _check_status(response)
            except httpx.HTTPStatusError:
                response.close()
                raise
            return response
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if not _retryable(exc):
                raise
            if attempt == 0:
                time.sleep(1)
    return ftp_response(url, listing=listing)


@contextmanager
def stream(method, url, **kwargs):
    """Fallback on connection/status failure, never on exceptions from callers.

    Mid-body HTTPS failures are left to the downloader's existing retry loop.
    FTP completes and validates the bounded body before exposing any bytes.
    """
    if method.upper() != 'GET' or ftp_path(url) is None:
        with httpx.stream(method, url, **kwargs) as response:
            yield response
        return
    stack = None
    for attempt in range(2):
        stack = ExitStack()
        try:
            response = stack.enter_context(httpx.stream(method, url, **kwargs))
            _check_status(response)
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            stack.close()
            stack = None
            if not _retryable(exc):
                raise
            if attempt == 0:
                time.sleep(1)
        except BaseException:
            stack.close()
            raise
        else:
            break
    if stack is not None:
        with stack:
            yield response
    else:
        response = ftp_response(url)
        try:
            yield response
        finally:
            response.close()
