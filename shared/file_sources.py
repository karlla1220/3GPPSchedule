"""Copies of a WG's input file found in several places: the common structure.

A WG's file can be listed in more than one place at once: the live FTP sync
folder, the meeting's FTP folder, and the mailing list (a chair mails a new
version before uploading it, ``shared/email_sources.py``). Each place gives
entries of one shape: ``{'url', 'name', 'uploaded_at', 'listing'}``, plus
``'origin': 'email'`` and the mail's ``message_id`` and ``sent`` for a mailed
file. FTP entries have no origin.

The flow is the same for every WG; the choices are each WG's own:

1. Gather the entries of every place (the WG lists its folders; mail comes
   from ``email_sources.for_wg``).
2. Choose one copy: the WG's rule, in its own ``sources.py`` (RAN1:
   ``downloader.py``). What a version is, how FTP copies of one version are
   ordered, and when a later file wins differ by WG (RAN3 orders dated
   names, SA2 later uploads of one revision, RAN4 the revision inside the
   file), so no common rule decides it.
3. Keep the built copy when it is no longer listed and the WG's rule says
   nothing listed replaces it (``hold_built``): within a meeting a build
   never steps back.
4. Fetch the body (``remote_files.fetch_file`` for both origins; the built
   copy through ``built_body``) and record where it came from
   (``source_record``).

Parts a WG may use in its rule, so mail is handled alike where a WG wants
it (``copy_order``):

- At equal version an FTP copy wins over a mailed one. The FTP folder is
  the record, so a page built from a mail returns to the uploaded file as
  soon as it is listed.
- Mailed copies of one version: the earliest mail wins. A later mail with the
  same file is a reply that attached it again, possibly with comments.
  Anyone may mail the list; who sent it is not checked.
- A mail's date (sender's clock) and an FTP upload time are never compared.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

import httpx

from shared import remote_files

FTP = 'ftp'
EMAIL = 'email'
# Kept in the manifest with a mailed file, so a later run can find it again.
MAIL_KEYS = ('origin', 'message_id', 'sent', 'listing')


def origin(entry: dict) -> str:
    return entry.get('origin') or FTP


def is_ftp(entry: dict) -> bool:
    return origin(entry) == FTP


def _sent(entry: dict) -> float:
    try:
        return datetime.fromisoformat(entry['sent']).timestamp()
    except (KeyError, TypeError, ValueError):
        return float('inf')


def copy_order(entry: dict, ftp_key: Callable[[dict], tuple]) -> tuple:
    """Copies of one version, the largest first: FTP (in the WG's ``ftp_key`` order), then the earliest mail.

    A WG puts its version before it: ``max(copies, key=lambda c: (version(c), *copy_order(c, ftp_key)))``.
    The two origins never reach each other's keys, so mail and upload times are not compared.
    """
    if is_ftp(entry):
        return (1, *ftp_key(entry))
    return (0, -_sent(entry), entry['name'])


def listed(entry: dict | None, candidates: list[dict]) -> bool:
    return bool(entry) and any(c['url'] == entry.get('url') for c in candidates)


def hold_built(built: dict | None, chosen: dict, candidates: list[dict],
               keep: Callable[[dict, dict], bool]) -> bool:
    """Keep the built copy instead of ``chosen``: it is no longer listed and the WG's ``keep`` says so.

    A built copy that is still listed takes part in the WG's normal choice;
    only a vanished one needs the WG's word that nothing listed replaces it.
    The caller checks that ``built`` is of the same meeting.
    """
    return bool(built) and not listed(built, candidates) and keep(built, chosen)


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
