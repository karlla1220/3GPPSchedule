"""Newest replies on the RAN1 mailing list, for session details.

Feature leads post the plan of their online and offline sessions on the
email discussion thread ("Offline 11:30 ~ 12:30: Part 1 PUSCH MIMO"). The
plan is always in the newest reply at the top; what follows is quoted mail.

The list archive (LISTSERV at list.etsi.org) publishes an RSS feed whose
item text stops after about 100 words with "[...]". The feed has no option
for longer text. A message is fetched in full only when its preview is cut
before the newest reply ends, that is, when the preview ends with "[...]"
and shows no quote header. The full text is the message's first text/plain
part, linked from the message page (two requests per message).

The archive drops the connection, without an HTTP response, for Python's
default User-Agent, and now and then stalls a TLS handshake. Requests go
one at a time, a few seconds apart, with one later retry.
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from datetime import datetime
from typing import Callable

import httpx
from defusedxml import ElementTree as ET

from shared.remote_files import client

ARCHIVE = "https://list.etsi.org"
LIST_NAME = "3GPP_TSG_RAN_WG1"
FEED_LIMIT = 100
REQUEST_GAP = 3.0  # seconds between requests to the archive
RETRY_GAP = 15.0
MAX_BODIES = 30  # full-text fetches per run; the rest wait for the next run

TRUNCATED = "[...]"

# Start of the quoted earlier mail. Outlook (From:/发件人), Foxmail/Coremail
# (-----原始邮件-----), ZTE mail ("Original" alone on a line), Apple and
# Gmail ("On … wrote:"), and the underscore rule some clients draw.
QUOTE_HEADER = re.compile(
    r"(?:^|\n)[ \t>]*(?:From:|发件人|寄件者|보낸 사람|-{2,}\s*Original|-{2,}\s*原始邮件"
    r"|Original[ \t]*(?:\n|$)|On [^\n]{5,160}wrote:|_{8,})",
    re.IGNORECASE,
)

_BODY_LINK = re.compile(r'href="(/scripts/wa\.exe\?A3=[^"]*)"')
_MESSAGE_ID = re.compile(r";([0-9a-f]+\.\d{4}[a-z]?)", re.IGNORECASE)
_INLINE_URL = re.compile(r"<(?:https?|mailto):[^>\s]*>")


@dataclass
class Mail:
    id: str
    title: str
    author: str
    date: datetime | None
    link: str
    snippet: str  # feed preview as plain text, "[...]" kept
    reply: str | None = None  # newest reply; None when its body could not be read
    full_body: bool = False  # reply comes from the full text, not the preview


def feed_url(list_name: str = LIST_NAME, limit: int = FEED_LIMIT) -> str:
    return f"{ARCHIVE}/scripts/wa.exe?RSS&L={list_name}&v=2.0&LIMIT={limit}"


def meeting_tag(meeting_id: str) -> str:
    """"ran1#126bis" -> "126bis", the tag thread subjects carry."""
    return meeting_id.split("#", 1)[-1].lower()


def is_meeting_thread(title: str, tag: str) -> bool:
    """An email discussion of this meeting: "[126bis-R20-…]" or "[RAN1#126bis] …"."""
    lowered = title.lower()
    return f"[{tag}-" in lowered or f"#{tag}]" in lowered


def preview_text(description: str) -> str:
    """Feed item HTML as plain text. Inline link targets ("folder<https://…>") are dropped."""
    text = re.sub(r"<br\s*/?>", "\n", description or "", flags=re.IGNORECASE)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return _INLINE_URL.sub("", text)


def newest_reply(text: str) -> str:
    """Text above the first quote header, blank lines collapsed."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    match = QUOTE_HEADER.search(text)
    top = text[: match.start()] if match else text
    if top.rstrip().endswith(TRUNCATED):
        top = top.rstrip()[: -len(TRUNCATED)]
    return re.sub(r"\n[ \t]*(?:\n[ \t]*)+", "\n", top).strip()


def needs_full_body(snippet: str) -> bool:
    """The preview stops before the newest reply ends."""
    return snippet.rstrip().endswith(TRUNCATED) and not QUOTE_HEADER.search(snippet)


def parse_feed(xml: str) -> list[Mail]:
    mails = []
    for item in ET.fromstring(xml).findall("channel/item"):
        link = (item.findtext("link") or "").strip()
        match = _MESSAGE_ID.search(link)
        if not match:
            continue
        try:
            date = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            date = None
        mails.append(Mail(
            id=match.group(1).lower(),
            title=" ".join((item.findtext("title") or "").split()),
            author=(item.findtext("author") or "").strip(),
            date=date,
            link=link,
            snippet=preview_text(item.findtext("description") or ""),
        ))
    return mails


def body_url(message_page: str) -> str | None:
    """Raw text of the message's first text/plain part (else text/html) on the message page."""
    links = [html.unescape(href) for href in _BODY_LINK.findall(message_page)]
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


def _get(http: httpx.Client, url: str, sleep: Callable[[float], None]) -> str:
    try:
        response = http.get(url)
    except httpx.TransportError:
        sleep(RETRY_GAP)
        response = http.get(url)
    response.raise_for_status()
    return response.text


def collect(
    tag: str,
    seen: set[str] = frozenset(),
    *,
    http: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_bodies: int = MAX_BODIES,
) -> list[Mail]:
    """New mails on this meeting's threads, newest first, each with its newest reply.

    A mail whose full text is needed but could not be read (or is over this
    run's budget) has ``reply`` None and is not meant to be marked seen, so
    the next run tries it again. A feed failure raises.
    """
    own = http is None
    http = http or client(timeout=30)
    try:
        mails = [
            mail for mail in parse_feed(_get(http, feed_url(), sleep))
            if mail.id not in seen and is_meeting_thread(mail.title, tag)
        ]
        fetched = 0
        for mail in mails:
            if not needs_full_body(mail.snippet):
                mail.reply = newest_reply(mail.snippet)
                continue
            if fetched >= max_bodies:
                continue
            fetched += 1
            try:
                sleep(REQUEST_GAP)
                url = body_url(_get(http, mail.link, sleep))
                if url is None:
                    continue
                sleep(REQUEST_GAP)
                raw = _get(http, url, sleep)
            except httpx.HTTPError:
                continue
            mail.reply = newest_reply(body_text(raw, is_html="T=text%2Fhtml" in url))
            mail.full_body = True
        return mails
    finally:
        if own:
            http.close()
