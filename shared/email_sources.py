"""Input files a chair mails to the WG list before (or instead of) uploading them.

A schedule can reach the mailing list hours before the FTP folder. This is
one more place a WG's files are found, next to its FTP folders: the
attachments of new mails whose file name the WG's own name rule accepts
(``accept``). Subjects and texts are not read; people write them as they
like, while the file name is what the chair also uploads.

What is kept, and where (the only storage is the repository and the CI cache):

- ``.cache/<wg>/email_sources.json`` (CI cache, saved by every check, never
  committed): how far the list has been read (``checked_through``), the mails
  opened in the last day (so a mail is opened once), and the accepted
  attachments found (``files``; a few per meeting). Mails without such an
  attachment are kept only while a later run could see them again.
- The WG's manifest (``docs/<wg>/.schedule_state.json``, committed with each
  build): ``checked_through`` as of the build, and which mail and file the
  page was built from. When the cache is gone, reading resumes from there.

Listings name no attachment, so a new mail is opened once (one request),
at most ``MAX_OPENS`` per run; the others wait for the next run, which also
keeps ``checked_through`` before them. The feed holds the newest mails; when
more arrived since the last run than it shows, the weekly indexes fill the
gap. Mail dates come from the sender's clock, so mails up to ``OVERLAP``
before ``checked_through`` are looked at again (opened ones are remembered).

A failure here never stops a build: mail only brings files earlier. The
files found before stay usable, and the result carries a note that the
WG prints (a check error would fail CI while the FTP copy is fine).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import time
from typing import Callable
from xml.etree.ElementTree import ParseError

import httpx

from shared import listserv
from shared.json_files import read_json, write_json

STATE_ROOT = Path('.cache')
MAX_OPENS = 20
OVERLAP = timedelta(hours=24)
KEEP_FILES = timedelta(days=60)
ORIGIN = 'email'
LIST_NAME = re.compile(r'3GPP_[A-Z0-9_]+')
# Waits between requests to the archive (tests replace it).
pause = time.sleep


@dataclass
class Scan:
    """What the list holds for a WG, as of this run."""

    list_name: str
    files: list[dict] = field(default_factory=list)  # entries in listing shape (``entry``)
    checked_through: str | None = None
    opened: int = 0  # message pages read in this run
    notes: list[str] = field(default_factory=list)  # printed by the caller; never a check error


def state_path(wg: str) -> Path:
    return STATE_ROOT / wg / 'email_sources.json'


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(timezone.utc).isoformat(timespec='seconds') if moment else None


def _moment(text: str | None) -> datetime | None:
    try:
        value = datetime.fromisoformat(text) if text else None
    except ValueError:
        return None
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def entry(mail: listserv.Item, attachment: dict) -> dict:
    """An attachment as a listing entry: ``url``, ``name``, ``uploaded_at`` like an FTP file.

    ``uploaded_at`` is the mail's date (sender's clock) and ``listing`` the
    message page. ``origin`` says where it was found; ``sent`` orders
    copies of one file name (``shared/file_sources.py``).
    """
    sent = _iso(mail.date)
    return {'url': attachment['url'], 'name': attachment['name'], 'uploaded_at': sent or '',
            'listing': mail.link, 'origin': ORIGIN, 'message_id': mail.id, 'sent': sent,
            'subject': mail.title, 'author': mail.author}


def load(wg: str, list_name: str) -> dict:
    state = read_json(state_path(wg))
    if state.get('list') != list_name:
        return {'list': list_name, 'checked_through': None, 'opened': {}, 'files': []}
    state.setdefault('opened', {})
    state.setdefault('files', [])
    return state


def _later(*moments: str | None) -> str | None:
    known = [m for m in moments if _moment(m)]
    return max(known, key=_moment) if known else None


def _listed(list_name, since, http, sleep, notes) -> tuple[list[listserv.Item], bool]:
    """The mails a run looks at, and whether every one since ``since`` is among them.

    The feed, then the weekly indexes back to ``since`` when the feed starts
    after it (more mail arrived than the feed shows).
    """
    items = listserv.parse_feed(listserv.page(http, listserv.feed_url(list_name), sleep))
    dated = [item.date for item in items if item.date]
    if since is None or not dated or min(dated) <= since:
        return items, True
    known = {item.id for item in items}
    try:
        for week_id in listserv.weeks(since.date(), min(dated).date()):
            sleep(listserv.REQUEST_GAP)
            for item in listserv.parse_index(listserv.page(http, listserv.index_url(list_name, week_id), sleep),
                                              list_name):
                if item.id not in known:
                    known.add(item.id)
                    items.append(item)
    except (httpx.HTTPError, ValueError, ParseError) as exc:
        notes.append(f'{list_name}: mails between {_iso(since)} and the feed not listed ({exc}); '
                        'they are looked at again next run')
        return items, False
    return items, True


def scan(wg: str, list_name: str, accept: Callable[[str], bool], http, *, now: datetime | None = None,
         checked_through: str | None = None, max_opens: int = MAX_OPENS,
         sleep: Callable[[float], None] | None = None) -> Scan:
    """The accepted attachments on ``list_name``, reading only mails not read before.

    ``checked_through`` is the build's own record (the manifest), used when
    the cache is older or gone.
    """
    now = now or datetime.now(timezone.utc)
    sleep = sleep or pause
    state = load(wg, list_name)
    since_text = _later(state.get('checked_through'), checked_through)
    since = _moment(since_text)
    result = Scan(list_name, checked_through=since_text)
    files = {(f['message_id'], f['name']): f for f in state['files']}
    try:
        items, complete = _listed(list_name, since, http, sleep, result.notes)
    except (httpx.HTTPError, ValueError, ParseError) as exc:
        result.notes.append(f'{list_name}: list unreadable ({exc}); using the mails found before')
        result.files = list(files.values())
        return result

    opened = state['opened']
    floor = since - OVERLAP if since else None
    todo = sorted((item for item in items
                   if item.id not in opened and (floor is None or item.date is None or item.date >= floor)),
                  key=lambda item: item.date or now)
    waiting = []
    for item in todo:
        if result.opened >= max_opens:
            waiting.append(item)
            continue
        try:
            if result.opened:
                sleep(listserv.REQUEST_GAP)
            result.opened += 1
            found = listserv.attachments(listserv.page(http, item.link, sleep))
        except (httpx.HTTPError, ValueError, ParseError):
            waiting.append(item)
            continue
        opened[item.id] = {'date': _iso(item.date) or _iso(now), 'files': [a['name'] for a in found]}
        for attachment in found:
            if accept(attachment['name']):
                files[(item.id, attachment['name'])] = entry(item, attachment)
    if waiting:
        result.notes.append(f'{list_name}: {len(waiting)} new mails left for the next run')

    # Read through: up to the first mail still waiting, else the newest listed.
    dates = [item.date for item in items if item.date]
    if not complete:
        reached = since
    elif waiting:
        reached = min((item.date or now) for item in waiting) - timedelta(seconds=1)
    else:
        reached = max(dates) if dates else since
    if since and reached and reached < since:
        reached = since
    result.checked_through = _iso(reached) if reached else None

    floor = (reached - OVERLAP) if reached else None
    state['opened'] = {key: value for key, value in opened.items()
                       if floor is None or (_moment(value.get('date')) or now) >= floor}
    state['files'] = [f for f in files.values() if (_moment(f.get('sent')) or now) >= now - KEEP_FILES]
    state['checked_through'] = result.checked_through
    write_json(state_path(wg), state)
    result.files = list(state['files'])
    return result


def check_list_name(value) -> None:
    """A WG's ``email_list`` setting: null (no mail read) or a list name such as 3GPP_TSG_RAN_WG2."""
    if value is not None and not (isinstance(value, str) and LIST_NAME.fullmatch(value)):
        raise ValueError('email_list must be null or a list name such as 3GPP_TSG_RAN_WG2')


def for_wg(wg: str, list_name: str | None, accept: Callable[[str], bool], http, *, now=None,
           previous: dict | None = None, max_opens: int = MAX_OPENS) -> Scan | None:
    """A WG's scan: None when it reads no list; the notes go to the log, prefixed with the WG.

    ``previous`` is the WG's manifest; its ``email.checked_through`` is where
    reading resumes when the CI cache is gone. A build that follows a check
    passes ``max_opens=0``: only the check reads new mail, so the build uses
    what the check decided on (and nothing the check did not see).
    """
    if not list_name:
        return None
    found = scan(wg, list_name, accept, http, now=now, max_opens=max_opens,
                 checked_through=((previous or {}).get('email') or {}).get('checked_through'))
    for note in found.notes:
        print(f'[{wg}] {note}')
    return found


def manifest_record(scan_result: Scan | None) -> dict | None:
    """What the manifest keeps of a scan: the list and how far it was read."""
    if scan_result is None:
        return None
    return {'list': scan_result.list_name, 'checked_through': scan_result.checked_through}
