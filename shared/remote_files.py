"""Shared 3GPP access: revalidate files, persist bodies, and bound retries.

WG code selects URLs and interprets documents. This module owns transport and
cache identity. Cache entries are disposable and never mark a WG build as
successful. They can be restored on any runner, independently of WG state.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

from shared import ftp_transport

CACHE_DIR = Path('.cache/3gpp')
MAX_BYTES = 30_000_000


def client(**kwargs):
    return httpx.Client(**{'follow_redirects': True, 'timeout': 45,
                          'headers': {'User-Agent': '3GPPSchedule/1.0', 'Accept': '*/*'},
                          **kwargs})


def is_3gpp_file(url):
    return ftp_transport.ftp_path(url) is not None


def _digest(body):
    return hashlib.sha256(body).hexdigest()


def _load(url):
    directory = CACHE_DIR / _digest(url.encode())
    try:
        info = json.loads((directory / 'metadata.json').read_text())
        body = (directory / 'body.bin').read_bytes()
        if (info.get('url') == url and info.get('sha256') == _digest(body)
                and info.get('size') == len(body) and 0 < len(body) <= MAX_BYTES):
            return info, body
    except (OSError, ValueError, AttributeError):
        pass
    return {}, None


def _save(url, body, info):
    directory = CACHE_DIR / _digest(url.encode())
    directory.mkdir(parents=True, exist_ok=True)
    # Metadata is the commit marker. A process interruption leaves a hash
    # mismatch, which _load treats as a miss instead of trusting partial data.
    for name, data in [('body.bin', body), ('metadata.json', json.dumps(info).encode())]:
        temporary = directory / (name + '.tmp')
        temporary.write_bytes(data)
        temporary.replace(directory / name)


def get_listing(url, *, http=None, **kwargs):
    response = ftp_transport.get(url, listing=True, http=http,
                                 **({'timeout': 30, 'follow_redirects': True, **kwargs} if http is None else kwargs))
    response.raise_for_status()
    return response


def fetch_file(http, url, old=None, local=None):
    """Return (body, metadata); reuse only a verified, revalidated body.

    HTTP uses ETag/Last-Modified. FTP compares path, exact size and MDTM before
    RETR. A missing/corrupt body is fetched even when old metadata is available.
    The optional old/local pair supports migration from WG-owned caches.
    """
    old = old or {}
    public = is_3gpp_file(url)
    if public:
        cached, body = _load(url)
        if body is not None:
            old, local = cached, body
        elif local is None or _digest(local) != old.get('sha256'):
            old, local = {}, None
    valid = old.get('url') == url and local is not None and _digest(local) == old.get('sha256')
    headers = {}
    if old.get('url') == url:
        if old.get('etag'):
            headers['If-None-Match'] = old['etag']
        if old.get('last_modified'):
            headers['If-Modified-Since'] = old['last_modified']
    kwargs = {} if http is not None else {'follow_redirects': True, 'timeout': 45}
    response = ftp_transport.get(url, http=http, headers=headers,
                                 cached=(old, local) if valid else None, **kwargs)
    if response.status_code == 304:
        response.close()
        if valid:
            info = {**old, 'size': len(local)}
            if public:
                _save(url, local, info)
            return local, info
        response = ftp_transport.get(url, http=http, **kwargs)
    try:
        response.raise_for_status()
        body = response.content
        if not body or len(body) > MAX_BYTES:
            raise ValueError(f'Empty or excessive input: {url}')
        info = {'url': url, 'name': unquote(urlsplit(url).path.rsplit('/', 1)[-1]),
                'size': len(body), 'sha256': _digest(body),
                'etag': response.headers.get('etag'),
                'last_modified': response.headers.get('last-modified'),
                'ftp_modified': response.headers.get('x-3gpp-mtime'),
                'content_type': response.headers.get('content-type'),
                'content_disposition': response.headers.get('content-disposition')}
        if public:
            _save(url, body, info)
        return body, info
    finally:
        response.close()


def download(url, destination):
    body, _ = fetch_file(None, url)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.tmp')
    temporary.write_bytes(body)
    temporary.replace(destination)
    return destination


@contextmanager
def stream(method, url, **kwargs):
    """External hosts retain streaming semantics; public 3GPP files share cache."""
    if method.upper() == 'GET' and is_3gpp_file(url):
        body, info = fetch_file(None, url)
        headers = {'content-length': str(info['size'])}
        for header, key in [('content-type', 'content_type'), ('content-disposition', 'content_disposition')]:
            if info.get(key):
                headers[header] = info[key]
        response = httpx.Response(200, content=body, request=httpx.Request('GET', url),
                                  headers=headers)
        try:
            yield response
        finally:
            response.close()
    else:
        with ftp_transport.stream(method, url, **kwargs) as response:
            yield response
