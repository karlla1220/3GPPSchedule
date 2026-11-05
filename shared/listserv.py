"""The 3GPP mailing lists at list.etsi.org (LISTSERV): what one list's archive shows.

Three views of a list, from the cheapest:

- the RSS feed (``RSS&L=…``): the newest mails, each with subject, author,
  date and the first ~100 words of text. It names no attachment.
- a weekly index (``A1=ind2611A``): every mail of one week, the same fields.
  Weeks are by day of month: A is 1–7, B 8–14, C 15–21, D 22–28, E 29–31.
- a message page (``A2=<list>;<id>``): the mail's header and one link per
  MIME part. Attachments are the only place their file names appear; the
  archive search does not index them either.

An attachment link (``A3=…&N=<file name>``) points into the archived week
and returns the file itself, without logging in. Archived mails do not
change, so neither does what the link returns.

The archive drops the connection, without an HTTP response, for Python's
default User-Agent (``remote_files.client`` sets ours), and now and then
stalls a TLS handshake: one later retry. The feed and message pages a run
has read are kept for the rest of the run, so several readers of one list
(RAN1's session details, schedule attachments) ask for each page once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
import html
import re
import time
from typing import Callable
from urllib.parse import parse_qs, unquote, urlsplit

from bs4 import BeautifulSoup
from defusedxml import ElementTree as ET
import httpx

ARCHIVE = "https://list.etsi.org"
FEED_LIMIT = 100
REQUEST_GAP = 3.0  # seconds between requests to the archive
RETRY_GAP = 15.0

_MESSAGE_ID = re.compile(r";([0-9a-f]+\.\d{4}[a-z]?)", re.IGNORECASE)
_PART_LINK = re.compile(r'href="(/scripts/wa\.exe\?A3=[^"]*)"')
_INLINE_URL = re.compile(r"<(?:https?|mailto):[^>\s]*>")
# Control characters XML does not allow. Subjects can carry them raw: an
# ISO-2022-JP subject left its ESC (0x1B) in the RAN2 feed of 5 Nov 2026,
# and the whole feed failed to parse.
_NOT_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_WEEKDAY_DATE = re.compile(r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{1,2} \w{3} \d{4} [\d:]+ [+-]\d{4}")

# Pages read in this run: url -> text. Cleared by new_run().
_PAGES: dict[str, str] = {}


@dataclass
class Item:
    """One mail as a listing (feed or weekly index) shows it."""

    id: str
    title: str
    author: str
    date: datetime | None
    link: str  # its message page
    description: str = ""  # the preview, HTML as the feed gives it ('' from an index)


def message_url(message_id: str, list_name: str) -> str:
    """The archive page of one mail, as the feed links it."""
    return f"{ARCHIVE}/scripts/wa.exe?A2={list_name};{message_id}"


def feed_url(list_name: str, limit: int = FEED_LIMIT) -> str:
    return f"{ARCHIVE}/scripts/wa.exe?RSS&L={list_name}&v=2.0&LIMIT={limit}"


def week(day: date) -> str:
    """The weekly index holding mails of ``day``: "2611A" for 1–7 Nov 2026."""
    return f"{day:%y%m}{'ABCDE'[(day.day - 1) // 7]}"


def weeks(start: date, end: date) -> list[str]:
    """The weekly indexes from ``start`` to ``end``, both included, oldest first."""
    found, day = [], start
    while day <= end:
        if week(day) not in found:
            found.append(week(day))
        day += timedelta(days=1)
    return found


def index_url(list_name: str, week_id: str) -> str:
    return f"{ARCHIVE}/scripts/wa.exe?A1=ind{week_id}&L={list_name}"


def message_id(link: str) -> str | None:
    match = _MESSAGE_ID.search(link or "")
    return match.group(1).lower() if match else None


def mail_date(text: str) -> datetime | None:
    try:
        return parsedate_to_datetime(text or "")
    except (TypeError, ValueError):
        return None


def preview_text(description: str, *, links: bool = False) -> str:
    """Feed item HTML as plain text. Inline link targets ("folder<https://…>") are dropped unless ``links``."""
    text = re.sub(r"<br\s*/?>", "\n", description or "", flags=re.IGNORECASE)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return text if links else _INLINE_URL.sub("", text)


def parse_feed(xml: str) -> list[Item]:
    """The feed's mails, newest first. Characters XML does not allow are dropped first."""
    items = []
    for entry in ET.fromstring(_NOT_XML.sub("", xml)).findall("channel/item"):
        link = (entry.findtext("link") or "").strip()
        found = message_id(link)
        if not found:
            continue
        items.append(Item(
            id=found,
            title=" ".join((entry.findtext("title") or "").split()),
            author=(entry.findtext("author") or "").strip(),
            date=mail_date(entry.findtext("pubDate") or ""),
            link=link,
            description=entry.findtext("description") or "",
        ))
    return items


def parse_index(page: str, list_name: str) -> list[Item]:
    """The mails of a weekly index page, in the page's order.

    Each row links the message page from its subject, then gives the
    author and the date in the next cells.
    """
    items = []
    for row in BeautifulSoup(page, "html.parser").find_all("tr"):
        anchor = row.find("a", href=re.compile(r"A2=" + re.escape(list_name) + ";", re.IGNORECASE))
        if anchor is None:
            continue
        found = message_id(anchor["href"])
        if not found:
            continue
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
        stamp = next((m[0] for cell in cells for m in [_WEEKDAY_DATE.search(cell)] if m), "")
        author = cells[1] if len(cells) > 2 else ""
        items.append(Item(id=found, title=" ".join(anchor.get_text(" ", strip=True).split()),
                          author=author, date=mail_date(stamp), link=message_url(found, list_name)))
    return items


def part_links(page: str) -> list[str]:
    """Every MIME part a message page links, as absolute URLs."""
    return [ARCHIVE + html.unescape(href) for href in _PART_LINK.findall(page)]


def attachments(page: str) -> list[dict]:
    """The files attached to a mail: ``{'name', 'url', 'content_type'}`` each, once.

    A part with a file name (``N=``) is a file; the text and HTML body parts
    have none. Inline images carry a name too and are returned like any file:
    the caller decides by the name which files it wants.
    """
    found = {}
    for url in part_links(page):
        query = parse_qs(urlsplit(url).query, keep_blank_values=True)
        name = (query.get("N") or [""])[0].strip()
        if not name or name in found:
            continue
        kind = (query.get("T") or [""])[0].split(";", 1)[0].strip()
        found[name] = {"name": name, "url": url, "content_type": kind}
    return list(found.values())


def attachment_name(url: str) -> str | None:
    """The file name an attachment link carries (``N=``), None for other URLs."""
    parts = urlsplit(url)
    if parts.hostname != urlsplit(ARCHIVE).hostname or not parts.query.startswith("A3="):
        return None
    name = (parse_qs(parts.query, keep_blank_values=True).get("N") or [""])[0].strip()
    return unquote(name) or None


def body_url(message_page: str) -> str | None:
    """Raw text of the message's first text/plain part (else text/html) on the message page."""
    links = [html.unescape(href) for href in _PART_LINK.findall(message_page)]
    for kind in ("T=text%2Fplain", "T=text%2Fhtml"):
        for link in links:
            if kind in link:
                return ARCHIVE + link.replace("&header=1", "")
    return None


def body_text(raw: str, *, is_html: bool = False) -> str:
    """Body part as plain text. Masked addresses arrive as links; their text is kept."""
    if is_html:
        raw = re.sub(r"<(style|script)\b.*?</\1>", "", raw, flags=re.IGNORECASE | re.DOTALL)
        raw = re.sub(r"<br\s*/?>|</(?:p|div|li|tr)>", "\n", raw, flags=re.IGNORECASE)
    return html.unescape(re.sub(r"<[^>]+>", "", raw))


def get(http: httpx.Client, url: str, sleep: Callable[[float], None] = time.sleep) -> str:
    """One page of the archive, with one later retry when the connection fails."""
    try:
        response = http.get(url)
    except httpx.TransportError:
        sleep(RETRY_GAP)
        response = http.get(url)
    response.raise_for_status()
    return response.text


def page(http: httpx.Client, url: str, sleep: Callable[[float], None] = time.sleep) -> str:
    """``get``, once per run: a page read earlier in this run is not asked for again."""
    if url not in _PAGES:
        _PAGES[url] = get(http, url, sleep)
    return _PAGES[url]


def read_this_run(url: str) -> bool:
    return url in _PAGES


def new_run():
    """Forget the pages this process read."""
    _PAGES.clear()
