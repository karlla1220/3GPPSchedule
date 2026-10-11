"""HTTPS first; anonymous FTP recovery for the public 3GPP file tree only.

FTP is unencrypted. Never forward HTTP credentials, headers or query strings.
The response retains the HTTPS identity so a transport change is not an update.
"""
import atexit
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
from email.utils import format_datetime
import ftplib
from html import escape
import logging
import re
import threading
import time
from urllib.parse import quote, unquote, urlsplit

import httpx

# Every request to 3GPP names this project and where to reach its maintainer,
# including calls made without the shared client (httpx would send its own).
USER_AGENT = '3GPPSchedule/1.0 (+https://github.com/karlla1220/3GPPSchedule)'
_RETRY_STATUS = {500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526}
# 3GPP's web application firewall answers some paths with 403 and its own page
# (Cloudflare in front of IIS). Retrying HTTPS only repeats the blocked request.
# Title and sentence of the block page seen on 2026-10-11 both carry this.
_FIREWALL_PAGE = b'web application firewall'
_FIREWALL_PAGE_BYTES = 256 * 1024
_MAX_BYTES = 64 * 1024 * 1024
# One anonymous FTP login serves every fallback in a run, instead of a new
# connection and login per file or folder.
_session = None
_session_lock = threading.Lock()
# URL prefixes whose HTTPS already failed in this run: a folder after a 5xx or
# a firewall 403, the whole host after a connection or TLS failure. Requests
# under them go straight to FTP instead of repeating what failed.
_https_failed = {}  # prefix -> why HTTPS failed
_FIREWALL = '3GPP firewall blocked HTTPS (403)'
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


def _firewall_blocked(response, url):
    """True for the firewall's 403 page only.

    An ordinary 403 stays an HTTP error: 3GPP also answers 403 for a folder
    that does not exist, and callers rely on that. Any other 403 is logged
    with its headers and text, so a changed block page can be recognised.
    """
    if response.status_code != 403:
        return False
    length = response.headers.get('content-length')
    if length and length.isdigit() and int(length) > _FIREWALL_PAGE_BYTES:
        body = b''
    else:
        try:
            body = response.read()[:_FIREWALL_PAGE_BYTES]
        except httpx.HTTPError:
            body = b''
    if _FIREWALL_PAGE in body.lower():
        return True
    logging.getLogger(__name__).warning('3GPP answered 403 without the firewall page: %s %s',
                                        url, _describe(response, body))
    return False


def _describe(response, body):
    """Headers that tell the edge from the origin, and the page's visible text."""
    text = re.sub(rb'<(style|script)\b.*?</\1\s*>', b' ', body, flags=re.S | re.I)
    text = re.sub(rb'<[^>]*>', b' ', text)
    text = ' '.join(text.decode('utf-8', 'replace').split())
    headers = {name: response.headers[name] for name in
               ('content-type', 'content-length', 'server', 'cf-ray', 'cf-mitigated', 'cf-cache-status')
               if name in response.headers}
    return f'{headers} body[{len(body)}]={text[:400]!r}'



def _folder(url):
    return url if url.endswith('/') else url.rsplit('/', 1)[0] + '/'


def _host(url):
    parts = urlsplit(url)
    return f'{parts.scheme}://{parts.netloc}/'


def _remember_failure(url, error):
    if isinstance(error, httpx.TransportError):
        _https_failed[_host(url)] = f'HTTPS failed ({type(error).__name__})'
    else:
        _https_failed[_folder(url)] = f'HTTPS failed ({error.response.status_code})'


def _https_failure(url):
    """Why HTTPS already failed for url in this run, or None."""
    return next((reason for prefix, reason in _https_failed.items() if url.startswith(prefix)), None)


def use_ftp_for(prefix, reason):
    """Read everything under prefix over FTP for the rest of this run.

    For a caller that knows more than the status code, such as a 403 from a
    folder that always exists.
    """
    _https_failed[prefix] = reason


def _ftp_because(url, reason, **kwargs):
    """FTP for a URL whose HTTPS failed; an FTP failure names both."""
    try:
        return ftp_response(url, **kwargs)
    except httpx.ConnectError as exc:
        raise httpx.ConnectError(f'{reason} and {exc}', request=exc.request) from exc


def _blocked_ftp(url, **kwargs):
    logging.getLogger(__name__).warning('%s: %s; trying FTP', _FIREWALL, url)
    _https_failed[_folder(url)] = _FIREWALL
    return _ftp_because(url, _FIREWALL, **kwargs)


def _identified(kwargs):
    headers = kwargs.get('headers') or {}
    if any(name.lower() == 'user-agent' for name in headers):
        return kwargs
    return {**kwargs, 'headers': {'User-Agent': USER_AGENT, **headers}}


def _check_status(response):
    # Keep 304 and other non-fallback responses for the existing callers.
    if response.status_code in _RETRY_STATUS:
        response.raise_for_status()
    # Azure sometimes serves an outage page with a successful status.
    if response.status_code == 200 and response.is_stream_consumed and any(pattern in response.content[:4096].lower()
            for pattern in (b"our services aren't available right now",
                            b"we're working to restore all services", b'service unavailable')):
        raise httpx.ConnectError('3GPP returned a service unavailable page', request=response.request)


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


def _file(ftp, path, deadline, cached=None):
    ftp.voidcmd('TYPE I')
    size = ftp.size(path)
    stamp = ftp.sendcmd('MDTM ' + path)
    if size is None or size <= 0 or size > _MAX_BYTES:
        raise ValueError('Empty or excessive FTP file')
    modified = datetime.strptime(stamp.removeprefix('213 ').split('.')[0], '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
    headers = {'content-type': 'application/octet-stream',
               'last-modified': format_datetime(modified, usegmt=True), 'x-3gpp-mtime': stamp}
    if cached is not None:
        old, body = cached
        same_date = (old.get('ftp_modified') == stamp if old.get('ftp_modified')
                     else old.get('last_modified') == headers['last-modified'])
        if len(body) == size and same_date:
            return body, {**headers, 'x-3gpp-revalidated': 'true'}
    data = bytearray()
    def collect(chunk):
        if time.monotonic() > deadline or len(data) + len(chunk) > min(size, _MAX_BYTES):
            raise ValueError('FTP download exceeds time or size limit')
        data.extend(chunk)
    ftp.retrbinary('RETR ' + path, collect, blocksize=65536)
    if len(data) != size or ftp.size(path) != size or ftp.sendcmd('MDTM ' + path) != stamp:
        raise ValueError('FTP file changed or was truncated during download')
    return bytes(data), headers


def _connect():
    ftp = ftplib.FTP('ftp.3gpp.org', timeout=20)
    try:
        ftp.login()
    except BaseException:
        ftp.close()
        raise
    return ftp


def _drop():
    global _session
    if _session is not None:
        try:
            _session.close()
        except Exception:  # closing never fails a run
            pass
    _session = None


def close_session():
    """Log out of the shared FTP session, if one is open."""
    global _session
    if _session is not None:
        try:
            _session.quit()
        except Exception:  # closing never fails a run
            pass
    _drop()


atexit.register(close_session)


def reset():
    """Start a new run: forget HTTPS failures and close the FTP session."""
    _https_failed.clear()
    close_session()


def _over_ftp(read):
    """Run read(ftp) on the shared session.

    A session the server closed while idle (421, EOF, reset) is replaced
    once. After any other failure the session is closed and the error kept.
    """
    global _session
    with _session_lock:
        reused = _session is not None
        if _session is None:
            _session = _connect()
        try:
            return read(_session)
        except (EOFError, OSError, ftplib.error_temp):
            _drop()
            if not reused:
                raise
        except BaseException:
            # Unknown state after a partial command: never reuse it.
            _drop()
            raise
        _session = _connect()
        try:
            return read(_session)
        except BaseException:
            _drop()
            raise


def ftp_response(url, *, listing=False, cached=None):
    """Read a bounded FTP response. Also used by the network diagnostic."""
    path = ftp_path(url)
    if path is None:
        raise ValueError('FTP fallback requires a public 3GPP HTTPS file URL')
    request = httpx.Request('GET', url)
    deadline = time.monotonic() + _MAX_SECONDS
    try:
        body, headers = _over_ftp(lambda ftp: _directory(ftp, path, url, deadline) if listing
                                  else _file(ftp, path, deadline, cached))
    except (ftplib.Error, OSError, EOFError, ValueError) as exc:
        raise httpx.ConnectError(f'FTP fallback failed for {url}: {exc}', request=request) from exc
    action = 'revalidated without RETR' if headers.get('x-3gpp-revalidated') else 'received'
    logging.getLogger(__name__).warning('3GPP FTP fallback: %s (%s, %d bytes)', url, action, len(body))
    return httpx.Response(200, content=body, headers={**headers, 'x-3gpp-transport': 'ftp'}, request=request)


def get(url, *, listing=False, http=None, cached=None, **kwargs):
    """Retry eligible HTTPS once, then use FTP; preserve normal HTTP semantics.

    A firewall 403 goes to FTP at once, without a second HTTPS request.
    """
    request = http.get if http is not None else httpx.get
    if ftp_path(url) is None:
        return request(url, **kwargs)
    reason = _https_failure(url)
    if reason:
        return _ftp_because(url, f'{reason} earlier in this run', listing=listing, cached=cached)
    if http is None:
        kwargs = _identified(kwargs)
    for attempt in range(2):
        try:
            response = request(url, **kwargs)
            try:
                _check_status(response)
            except (httpx.HTTPStatusError, httpx.TransportError):
                response.close()
                raise
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if not _retryable(exc):
                raise
            if attempt == 0:
                time.sleep(1)
            else:
                _remember_failure(url, exc)
            continue
        if not _firewall_blocked(response, url):
            return response
        response.close()
        return _blocked_ftp(url, listing=listing, cached=cached)
    return ftp_response(url, listing=listing, cached=cached)


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
    reason = _https_failure(url)
    if reason:
        response = _ftp_because(url, f'{reason} earlier in this run')
        try:
            yield response
        finally:
            response.close()
        return
    kwargs = _identified(kwargs)
    stack = None
    blocked = False
    for attempt in range(2):
        stack = ExitStack()
        try:
            response = stack.enter_context(httpx.stream(method, url, **kwargs))
            _check_status(response)
            blocked = _firewall_blocked(response, url)
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            stack.close()
            stack = None
            if not _retryable(exc):
                raise
            if attempt == 0:
                time.sleep(1)
            else:
                _remember_failure(url, exc)
        except BaseException:
            stack.close()
            raise
        else:
            if blocked:
                stack.close()
                stack = None
            break
    if stack is not None:
        with stack:
            yield response
    else:
        response = _blocked_ftp(url) if blocked else ftp_response(url)
        try:
            yield response
        finally:
            response.close()
