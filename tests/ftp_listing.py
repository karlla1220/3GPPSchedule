"""A 3GPP folder listing for tests, in the shape www.3gpp.org/ftp serves (seen 2026-11-16)."""

from html import escape
from urllib.parse import quote, urlsplit


def listing(entries, base, stamp='2026/10/14 9:25', slashed=False):
    """Path links, sort links, then a row per entry.

    entries are names or (name, upload time) pairs; a name ending in '/' is a
    folder. Folders are linked without the '/' and rows without class="file"
    (since 2026-11); slashed keeps the '/' of earlier listings.
    """
    parts = urlsplit(base)
    root = f'{parts.scheme}://{parts.netloc}/'
    path = [p for p in parts.path.split('/') if p]
    crumbs = ''.join(f'<a href="{escape(root + "/".join(path[:i]) + ("/" if i else ""))}">{escape(name)}</a> / '
                     for i, name in enumerate([parts.netloc] + path))
    sort = ''.join(f'<th><a href="?sortby={key}">sort by {key}</a>/<a href="?sortby={key}rev">desc</a></th>'
                   for key in ('name', 'date', 'size'))
    rows = []
    for entry in entries:
        name, uploaded = entry if isinstance(entry, tuple) else (entry, stamp)
        folder = name.endswith('/')
        href = quote(base + (name if slashed or not folder else name.rstrip('/')), safe=':/%')
        kind = '' if folder else ' class="file"'
        link = f'<a{kind} href="{escape(href)}">{escape(name.rstrip("/"))}</a>'
        rows.append(f'<tr><td></td><td><img class="icon" src="/ftp/geticon.axd?file="></td><td>{link}</td>'
                    f'<td>{uploaded}</td><td>{"" if folder else "1,0 KB"}</td></tr>')
    return (f'<h2>{crumbs}</h2><table><thead><tr><th></th><th></th>{sort}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')
