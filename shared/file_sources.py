"""Copies of a WG's input file found in several places, and the one a build uses.

A WG's file can be listed in more than one place at once: the live FTP sync
folder, the meeting's FTP folder, and the mailing list (a chair mails a new
version before uploading it, ``shared/email_sources.py``). Each place gives
entries of one shape: ``{'url', 'name', 'uploaded_at', 'listing'}``, plus
``'origin': 'email'`` and the mail's ``message_id`` and ``sent`` for a mailed
file. FTP entries have no origin.

What a file is (its meeting and version) is the WG's to say, from the name
or the content (``version``). Which copy wins is common:

1. The newer version wins, wherever it was found.
2. At equal version an FTP copy wins over a mailed one. The FTP folder is
   the record, so a page built from a mail returns to the uploaded file as
   soon as it is listed, even when the two differ.
3. FTP copies of one version are ordered by the WG (``ftp_order``; by
   default the live sync copy, then the later upload).
4. Mailed copies of one version: the earliest mail wins. A later mail with
   the same file is a reply that attached it again, possibly with comments.
   Anyone may mail the list; who sent it is not checked.
5. A mail's date (sender's clock) and an FTP upload time are never compared.
6. Within a meeting a build never steps back (``keep_built``): when the built
   file is no longer listed and nothing listed is newer, the build keeps it.

Bodies come from ``remote_files.fetch_file`` for both origins. A mailed
file is stored like a 3GPP file and, should the CI cache lose it, fetched
from the archive again and checked against the built file's sha256.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Iterable

import httpx

from shared import remote_files

FTP = 'ftp'
EMAIL = 'email'
# Kept in the manifest with a mailed file, so a later run can find it again.
MAIL_KEYS = ('origin', 'message_id', 'sent', 'listing')


def origin(entry: dict) -> str:
    return entry.get('origin') or FTP


def live_first(entry: dict) -> tuple:
    """FTP copies of one version: the sync folder's live copy, then the later upload."""
    return 'Meetings_3GPP_SYNC' in entry['url'], entry.get('uploaded_at') or '', entry['name']


def _sent(entry: dict) -> float:
    try:
        return datetime.fromisoformat(entry['sent']).timestamp()
    except (KeyError, TypeError, ValueError):
        return float('inf')


def rank(entry: dict, version: Callable[[dict], object]) -> tuple:
    """Version, then FTP over mail."""
    return version(entry), 1 if origin(entry) == FTP else 0


def order(entry: dict, version: Callable[[dict], object], ftp_order: Callable[[dict], tuple] = live_first) -> tuple:
    """The sort key of one copy; the largest wins (rules 1–5 above)."""
    if origin(entry) == FTP:
        return (*rank(entry, version), *ftp_order(entry))
    return (*rank(entry, version), -_sent(entry), entry['name'])


def choose(candidates: Iterable[dict], version: Callable[[dict], object],
           ftp_order: Callable[[dict], tuple] = live_first) -> dict:
    return max(candidates, key=lambda entry: order(entry, version, ftp_order))


def keep_built(built: dict | None, chosen: dict, candidates: list[dict], version: Callable[[dict], object]) -> bool:
    """Keep the built copy: it is no longer listed, and nothing listed has a higher rank.

    ``built`` is the built file as an entry (same meeting as ``chosen``; the
    caller checks). The rank is version and origin only: another upload of
    the built version (the meeting folder's copy of a sync file) does not
    replace it, while an FTP copy of a mailed version does.
    """
    if not built or any(c['url'] == built.get('url') for c in candidates):
        return False
    return rank(built, version) >= rank(chosen, version)


def built_body(built: dict, http=None) -> bytes | None:
    """The stored body of the built file, exactly as built; None when it cannot be had.

    A mailed file can be fetched from the archive again (it does not change);
    an FTP file that is no longer listed cannot.
    """
    body = remote_files.cached(built.get('url'), built.get('sha256'))
    if body is not None or origin(built) != EMAIL or not built.get('sha256'):
        return body
    try:
        return remote_files.fetch_file(http, built['url'], built)[0]
    except (httpx.HTTPError, ValueError, OSError):
        return None


def source_record(chosen: dict, info: dict) -> dict:
    """The manifest's record of the file used: where it was listed, and the fetched file's identity."""
    record = {key: chosen[key] for key in ('name', 'url', 'uploaded_at')}
    if origin(chosen) == EMAIL:
        record.update({key: chosen.get(key) for key in MAIL_KEYS})
    return {**record, **info}
