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
from urllib.parse import unquote
from dataclasses import dataclass, field
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
    tdocs: list[str] = field(default_factory=list)  # TDoc numbers of the newest reply, link targets included


def feed_url(list_name: str = LIST_NAME, limit: int = FEED_LIMIT) -> str:
    return f"{ARCHIVE}/scripts/wa.exe?RSS&L={list_name}&v=2.0&LIMIT={limit}"


def meeting_tag(meeting_id: str) -> str:
    """"ran1#126bis" -> "126bis", the tag thread subjects carry."""
    return meeting_id.split("#", 1)[-1].lower()


# A thread tag: meeting, then the rest ("126bis-R20-6GR-Waveform"). The
# meeting is written "126bis", "126b" or "126-bis"; "Post-126-…" is the
# discussion after meeting #126.
_TAG = re.compile(r"(post-)?(\d+)\s*-?\s*(bis|b|e)?\s*-\s*(\S.*)", re.IGNORECASE)
_SUBJECT_TAG = re.compile(r"\[([^\[\]]+)\]")


def _tag_parts(tag: str) -> tuple[bool, str, str] | None:
    """(after the meeting, meeting, rest) of a tag, the meeting as "126bis"."""
    m = _TAG.fullmatch(" ".join(tag.split()))
    if not m:
        return None
    suffix = (m[3] or "").lower()
    meeting = m[2] + ("bis" if suffix in {"b", "bis"} else suffix)
    return bool(m[1]), meeting, " ".join(m[4].split()).lower()


def is_meeting_thread(title: str, tag: str) -> bool:
    """A mail of this meeting: a "[126bis-…]" or "[126b-…]" thread, or "[RAN1#126bis] …"."""
    for subject_tag in _SUBJECT_TAG.findall(title):
        parts = _tag_parts(subject_tag)
        if parts and not parts[0] and parts[1] == tag:
            return True
        m = re.fullmatch(r"\s*RAN\s*1\s*#\s*(\d+)\s*(bis|b|e)?\s*", subject_tag, re.IGNORECASE)
        if m and m[1] + ("bis" if (m[2] or "").lower() in {"b", "bis"} else (m[2] or "").lower()) == tag:
            return True
    return False


def thread_index(threads: list[dict]) -> dict:
    """Email discussions the chairman note names, by subject tag.

    ``threads`` is ``email_threads`` of the agreements: one entry per line
    "[tag] Email discussion on … – FL (company)", with its agenda item.
    """
    by_rest = {}
    meeting = None
    for thread in threads:
        parts = _tag_parts(thread.get("tag") or "")
        if parts and not parts[0]:
            meeting = meeting or parts[1]
            by_rest.setdefault(parts[2], thread)
    return {"meeting": meeting, "by_rest": by_rest}


def thread_of(title: str, index: dict) -> dict | None:
    """The discussion a mail belongs to, by the first bracketed tag of its subject.

    Replies keep the tag and vary the rest of the subject ("Re:", "回复:", a
    subject cut to the tag, a subject the FL rewrote after the tag). The tag
    itself is compared after the meeting: "126b-" and "126bis-" are the same
    meeting. A tag that carries an earlier meeting with the rest of a thread
    of this meeting is that thread, copied from the last meeting's tag
    ("match": "earlier_meeting"). "Post-126-…" is never this meeting.
    """
    for tag in _SUBJECT_TAG.findall(title):
        parts = _tag_parts(tag)
        if not parts or parts[0]:
            continue
        thread = index["by_rest"].get(parts[2])
        if thread is None:
            continue
        if parts[1] == index["meeting"]:
            return {**thread, "match": "tag"}
        if index["meeting"] and parts[1].isdigit() and int(parts[1]) <= int(re.match(r"\d+", index["meeting"])[0]):
            return {**thread, "match": "earlier_meeting"}
    return None


# Agenda item numbers a mail states: "AI 8.2", "agenda item 9.4.1",
# "(Agenda 9.5.1)", "[9.3.3]", a dotted number on its own ("10.5.2.2",
# "9.3.1" on the first line), and the deepest numbered folder of a drafts
# path ("drafts/10.5.3 (CSI acquisition)/10.5.3.4 Beam Management/"). Only
# numbers on this meeting's agenda count, and a dotted number is not an
# agenda item when a word says what else it is ("Proposal 3.1.1-4",
# "Section 8.2", "Table 9.1", "v1.2", "TS 38.211").
_NUMBER = r"(\d+(?:\.\d+)*)"
_STATED = [
    re.compile(rf"\bAI\s*#?\s*{_NUMBER}(?![\d.]*\d)", re.IGNORECASE),
    re.compile(rf"\bagenda(?:\s+item)?\s*:?\s*{_NUMBER}", re.IGNORECASE),
    re.compile(rf"\[\s*{_NUMBER}\s*\]"),
]
_DOTTED = re.compile(r"(?<![\w.\-#/])(\d+(?:\.\d+)+)(?![\w\-]|\.\d)")
_NOT_AGENDA = re.compile(
    r"\b(?:proposals?|prop|observations?|obs|sections?|sec|clauses?|table|figure|fig|annex|step|options?"
    r"|alt|alternatives?|round|version|ver|v|ts|tr|rel|release|equation|eq|issue|question"
    r"|part|chapter|page)\.?\s*[#:]?\s*$",
    re.IGNORECASE,
)
_DRAFTS = re.compile(r"drafts/[^\n]*", re.IGNORECASE)
_FOLDER = re.compile(rf"/\s*{_NUMBER}(?=%20|[ (/>\"]|$)")


def _dotted(text: str) -> list[str]:
    return [m[1] for m in _DOTTED.finditer(text) if not _NOT_AGENDA.search(text[max(0, m.start() - 16):m.start()])]


def stated_agenda_items(subject: str, body: str, agenda: set[str]) -> dict[str, list[str]]:
    """Numbers of this meeting's agenda the subject and the body state, in order."""
    def keep(found):
        return list(dict.fromkeys(n.rstrip(".") for n in found if n.rstrip(".") in agenda))

    def stated(text):
        return [m[1] for rx in _STATED for m in rx.finditer(text)] + _dotted(text)

    found = stated(body)
    for path in _DRAFTS.findall(body):
        numbers = keep(_FOLDER.findall("/" + path[len("drafts/"):]))
        if numbers:
            found.append(max(numbers, key=lambda n: n.count(".")))
    return {"subject": keep(stated(subject)), "body": keep(found)}


def _under(item: str, parent: str) -> bool:
    return item == parent or item.startswith(parent + ".")


def _deepest(items: list[str]) -> list[str]:
    return [i for i in items if not any(o != i and _under(o, i) for o in items)]


def agenda_of(title: str, reply: str, index: dict, agenda: set[str]) -> dict:
    """Agenda items of a mail: its thread's, narrowed by the numbers it states.

    - A mail on a thread of the chairman note takes the thread's item. A
      thread over several items (9.3 A-IoT) names its sub-item in the mail
      ("[9.3.3]" on the first line); a stated number under the thread's item
      narrows it. A stated number outside it is a reference to another item
      and is reported in "other", not used.
    - A mail on no known thread ("Updated tdoc - AI 10.5.2.2") takes the
      numbers its subject states, else those its body states ("match":
      "stated").
    - Without this meeting's agenda no number is trusted.
    """
    thread = thread_of(title, index)
    stated = stated_agenda_items(title, reply, agenda) if agenda else {"subject": [], "body": []}
    numbers = list(dict.fromkeys(stated["subject"] + stated["body"]))
    if thread is not None:
        own = thread["agenda_item"]
        inside = [n for n in numbers if _under(n, own)]
        return {
            "agenda_items": _deepest(inside) if inside else [own],
            "thread": thread["tag"],
            "match": thread["match"],
            "other": [n for n in numbers if not _under(n, own) and not _under(own, n)],
        }
    chosen = stated["subject"] or stated["body"]
    return {
        "agenda_items": _deepest(chosen),
        "thread": None,
        "match": "stated" if chosen else None,
        "other": [],
    }


def preview_text(description: str, *, links: bool = False) -> str:
    """Feed item HTML as plain text. Inline link targets ("folder<https://…>") are dropped unless ``links``."""
    text = re.sub(r"<br\s*/?>", "\n", description or "", flags=re.IGNORECASE)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return text if links else _INLINE_URL.sub("", text)


_TDOC = re.compile(r"(?<![\w-])R1-\d{7}(?!\d)")


def tdoc_numbers(text: str) -> list[str]:
    """TDoc numbers a text names, in order: "R1-2608628", also inside links and draft file names."""
    return list(dict.fromkeys(_TDOC.findall(unquote(text or ""))))


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
            tdocs=tdoc_numbers(newest_reply(preview_text(item.findtext("description") or "", links=True))),
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
    wanted: Callable[[Mail], bool] | None = None,
) -> list[Mail]:
    """New mails on this meeting's threads, newest first, each with its newest reply.

    ``wanted`` picks the mails by their feed entry; by default a subject
    tagged for this meeting (``is_meeting_thread``).

    A mail whose full text is needed but could not be read (or is over this
    run's budget) has ``reply`` None and is not meant to be marked seen, so
    the next run tries it again. A feed failure raises.
    """
    own = http is None
    http = http or client(timeout=30)
    try:
        mails = [
            mail for mail in parse_feed(_get(http, feed_url(), sleep))
            if mail.id not in seen and (wanted(mail) if wanted else is_meeting_thread(mail.title, tag))
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
            mail.tdocs = list(dict.fromkeys(mail.tdocs + tdoc_numbers(mail.reply)))
        return mails
    finally:
        if own:
            http.close()
