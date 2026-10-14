"""Email session details across runs: what was read, what was asked, what it means now.

Each mail is read and asked about once. The record of every mail the run
handled is kept per meeting in ``docs/ran1/email_sessions/<meeting>.jsonl``
(committed with the build) and in ``.cache/ran1/email_sessions/`` (the CI
cache, saved even when a build fails and ``docs/`` is rolled back), one JSON
line per mail, by LISTSERV message id. A mail with a record is never fetched
or asked again; to correct one, append a line with its id (the later line
wins).

What is not recorded is tried again: a mail whose body could not be read, a
call that failed, a run without an API key, a mail over the run's budget.
Mails no thread or agenda number points to are not recorded either: telling
that from the subject costs nothing, and a thread that the chairman note
names later still finds them.

No request goes out, and nothing is asked, outside the meeting (from
``LEAD_DAYS`` before it to its last day), before the chairman note names
the meeting's email discussions, or without this meeting's agenda. A mail
whose newest reply has no word of time or place ("online", "offline",
"today", "Wed", "11:30", "room") is recorded as skipped without a call.

The answers are checked against the grid on every build (``resolve``), so a
schedule change moves or drops a detail without asking again.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from shared.schedule import Schedule, Session

from .agenda_labels import _agenda_for_meeting
from .agenda_descriptions import DEFAULT_JSON_PATH
from .email_details import (
    DAYS,
    EXTRACT_INSTRUCTION,
    EXTRACT_MODEL,
    Cell,
    candidates,
    gemini_extract,
    request_text,
    resolve,
    times_in,
)
from .email_sessions import (
    Mail,
    agenda_of,
    collect,
    document_links,
    meeting_tag,
    newest_reply,
    stated_agenda_items,
    thread_index,
    thread_of,
)

RECORD_DIR = Path("docs/ran1/email_sessions")
CACHE_DIR = Path(".cache/ran1/email_sessions")
LEAD_DAYS = 3
MAX_ASKS = 40  # LLM calls per run; the rest wait for the next run
EARLIER = 8  # earlier announcements of the thread given with a mail
PROMPT_VERSION = __import__("hashlib").sha256(EXTRACT_INSTRUCTION.encode()).hexdigest()[:12]

# A word of time or place. A newest reply with none of them announces no
# session ("updated v33, please check"), so it is not asked about.
SCHEDULE_WORDS = re.compile(
    r"\b(?:on-?line|off-?line|off-?off|session|meet(?:ing)?|today|tonight|tomorrow|morning|afternoon|evening"
    r"|lunch|break|mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:rs(?:day)?)?|fri(?:day)?|room|hall"
    r"|lobby|outside|in front of|deadline|until|before|schedule|plan|slot)\b"
    r"|\b\d{1,2}[:.]\d{2}\b|\b\d{1,2}\s*[ap]\.?m\b",
    re.IGNORECASE,
)


def record_path(meeting_id: str, directory: Path | None = None) -> Path:
    return (directory or RECORD_DIR) / f"{meeting_id.lower().replace('#', '_')}.jsonl"


def _lists(directories):
    return directories if directories is not None else (RECORD_DIR, CACHE_DIR)


def load_records(meeting_id: str, directories=None) -> dict[str, dict]:
    """Records by message id, from the committed list and the CI cache. A later line wins."""
    records: dict[str, dict] = {}
    for directory in _lists(directories):
        try:
            lines = record_path(meeting_id, directory).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and isinstance(record.get("id"), str):
                records[record["id"]] = record
    return records


def append_records(meeting_id: str, new: list[dict], directories=None) -> None:
    """Add records to both lists, each brought up to date with the other first.

    A record only one list holds (asked by a build that failed, whose docs/
    was rolled back while the CI cache was saved) is carried over, so
    neither list asks for it again.
    """
    directories = _lists(directories)
    known = load_records(meeting_id, directories)
    fresh = {record["id"] for record in new}
    for directory in directories:
        have = load_records(meeting_id, (directory,))
        missing = [known[i] for i in known if i not in have and i not in fresh]
        if not missing and not new:
            continue
        path = record_path(meeting_id, directory)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as out:
            for record in missing + new:
                out.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def cells_of(schedule: Schedule) -> tuple[list[Cell], dict[str, Session]]:
    """Grid cells with ids "Wed 12:30-13:00 Yeongju A Hall", and their sessions.

    Rooms are the day's rooms from column 2 (column 1 is the time axis).
    """
    cells, sessions = [], {}
    for day in schedule.days:
        names = [room.name for room in day.rooms]
        for session in day.sessions:
            span = names[max(session.room_col_start - 2, 0):max(session.room_col_end - 2, 0)]
            room = " + ".join(span) if span else "?"
            cell_id = f"{day.day_name[:3]} {session.start_time}-{session.end_time} {room}"
            if cell_id in sessions:
                continue
            sessions[cell_id] = session
            cells.append(Cell(cell_id, day.day_name, session.start_time, session.end_time, room, session.name,
                              session.agenda_item, tuple(session.inferred_agenda_items)))
    return cells, sessions


def _meeting_days(schedule: Schedule) -> tuple[date, date] | None:
    try:
        return date.fromisoformat(schedule.starts_on), date.fromisoformat(schedule.ends_on)
    except (TypeError, ValueError):
        return None


def in_meeting(schedule: Schedule, now: datetime) -> bool:
    """From LEAD_DAYS before the meeting to its last day, in meeting time. Never for a demo."""
    days = _meeting_days(schedule)
    if schedule.is_demo or days is None:
        return False
    today = now.astimezone(ZoneInfo(schedule.timezone or "UTC")).date()
    return days[0] - timedelta(days=LEAD_DAYS) <= today <= days[1]


AGENDA_PATH = DEFAULT_JSON_PATH


def _context(schedule: Schedule):
    """Thread index and agenda of this meeting, or None when either is missing."""
    threads = (schedule.chairman_agreements or {}).get("email_threads") or []
    if (schedule.chairman_agreements or {}).get("meeting_id") != schedule.meeting_id.lower():
        return None
    agenda = _agenda_for_meeting(schedule.meeting_id.lower(), AGENDA_PATH)
    if not threads or not agenda:
        return None
    return thread_index(threads), agenda


def wanted(mail: Mail, index: dict, agenda) -> bool:
    """A thread the chairman note names, or an agenda number in the subject. Costs nothing."""
    return thread_of(mail.title, index) is not None or bool(stated_agenda_items(mail.title, "", set(agenda))["subject"])


def _sent(mail_date, tz: ZoneInfo) -> datetime | None:
    """A sending time in meeting time: a datetime, a record's ISO date or a mail's RFC 2822 date."""
    when = mail_date if isinstance(mail_date, datetime) else None
    for parse in (datetime.fromisoformat, parsedate_to_datetime):
        if when is None:
            try:
                when = parse(mail_date)
            except (TypeError, ValueError):
                pass
    return when.astimezone(tz) if when and when.tzinfo else None


def pending(schedule: Schedule, *, now: datetime | None = None, http=None) -> list[Mail] | None:
    """Unrecorded mails worth a build, from the feed alone (one request), for the check job.

    None when the meeting is not on or its threads are not known yet: no request is made.
    """
    now = now or datetime.now(ZoneInfo("UTC"))
    context = _context(schedule)
    if not in_meeting(schedule, now) or context is None:
        return None
    index, agenda = context
    records = load_records(schedule.meeting_id)
    first = _meeting_days(schedule)[0] - timedelta(days=LEAD_DAYS)
    tz = ZoneInfo(schedule.timezone or "UTC")
    from .email_sessions import feed_url, parse_feed
    from shared.remote_files import client

    own = http is None
    http = http or client(timeout=30)
    try:
        response = http.get(feed_url())
        response.raise_for_status()
        mails = parse_feed(response.text)
    finally:
        if own:
            http.close()
    return [
        mail for mail in mails
        if mail.id not in records and wanted(mail, index, agenda)
        and (_sent(mail.date, tz) or now).date() >= first
        and SCHEDULE_WORDS.search(newest_reply(mail.snippet))
    ]


def _history(records, cells: list[Cell], tz: ZoneInfo) -> dict[str, list[dict]]:
    """Resolved items of every asked record, by message id.

    A time counts when the mail states it or an earlier mail of its thread
    did (the FL proposal "for online" after "online from 12:00").
    """
    out, known = {}, {}
    for record in sorted(records, key=lambda r: r["date"]):
        if record.get("status") != "asked":
            continue
        by_id = {cell.id: cell for cell in cells}
        thread = record.get("thread") or record["id"]
        allowed = times_in(record.get("reply") or "") | known.get(thread, set())
        items = resolve(record["answer"], offered=[by_id[i] for i in record.get("offered", []) if i in by_id],
                        cells=cells, agenda_items=record["agenda_items"], sent=_sent(record["date"], tz),
                        tdocs=record.get("tdocs") or [], allowed_times=allowed)
        out[record["id"]] = items
        for item in items:
            known.setdefault(thread, set()).update(
                times_in(" ".join(filter(None, [item.get("start"), item.get("end")]))))
    return out


def update_records(schedule: Schedule, *, now: datetime | None = None, http=None,
                   extract=gemini_extract, sleep=None) -> list[dict]:
    """Read and ask about this meeting's new mails, and record them. Returns the new records."""
    now = now or datetime.now(ZoneInfo("UTC"))
    context = _context(schedule)
    if not in_meeting(schedule, now) or context is None:
        return []
    index, agenda = context
    meeting_id = schedule.meeting_id
    records = load_records(meeting_id)
    tz = ZoneInfo(schedule.timezone or "UTC")
    first = _meeting_days(schedule)[0] - timedelta(days=LEAD_DAYS)
    kwargs = {"http": http} if http is not None else {}
    if sleep is not None:
        kwargs["sleep"] = sleep
    mails = collect(meeting_tag(meeting_id), set(records),
                    wanted=lambda mail: wanted(mail, index, agenda), **kwargs)
    cells, _ = cells_of(schedule)
    can_ask = extract is not gemini_extract or bool(os.environ.get("GEMINI_API_KEY"))
    append_records(meeting_id, [])  # the two lists catch up before anything new
    new, asked = [], 0

    def keep(record):
        # Written at once: a run stopped half-way keeps every answer it paid for.
        new.append(record)
        append_records(meeting_id, [record])

    for mail in sorted(mails, key=lambda m: m.date or now):
        sent = _sent(mail.date, tz)
        if mail.reply is None or sent is None or sent.date() < first:
            continue  # unread body (tried again), or a mail from before the meeting
        found = agenda_of(mail.title, mail.reply, index, set(agenda))
        base = {"id": mail.id, "date": sent.isoformat(), "title": mail.title, "author": mail.author,
                "thread": found["thread"], "agenda_items": found["agenda_items"], "other": found["other"]}
        if not found["agenda_items"]:
            keep({**base, "status": "skipped", "reason": "no agenda item"})
            continue
        if not SCHEDULE_WORDS.search(mail.reply):
            keep({**base, "status": "skipped", "reason": "no word of time or place"})
            continue
        if not can_ask or asked >= MAX_ASKS:
            continue
        items = found["agenda_items"] + found["other"]
        offered = candidates(cells, items, sent, _meeting_days(schedule)[0], tz)
        earlier = []
        if found["thread"]:
            prior = [r for r in list(records.values()) + new if r.get("thread") == found["thread"]]
            for announced in _history(prior, cells, tz).values():
                earlier.extend(announced)
        text = request_text(mail.title, sent, items, agenda, offered, mail.reply, earlier[-EARLIER:])
        asked += 1
        try:
            answer = extract(text)
        except Exception as exc:  # noqa: BLE001 - one mail's failure is tried again next run
            print(f"  Email {mail.id}: extraction failed, will retry: {exc}")
            continue
        keep({**base, "status": "asked", "reply": mail.reply[:6000], "tdocs": mail.tdocs,
              "links": mail.links, "offered": [cell.id for cell in offered], "answer": answer,
              "model": EXTRACT_MODEL, "prompt_version": PROMPT_VERSION})
    return new


# The meeting's file server mirrored by the sync folder: the venue's local
# server ("http://10.10.10.10/ftp/RAN/RAN1/Inbox/…") and the sync folder itself.
_MIRRORED = re.compile(r"https?://[^/]+/ftp/RAN/RAN1/|https?://www\.3gpp\.org/ftp/Meetings_3GPP_SYNC/RAN1/",
                       re.IGNORECASE)


def inbox_tdocs(sync: str) -> set[str] | None:
    """TDoc files in the sync folder's Inbox; None when it cannot be listed.

    The build lists this folder already (session notes), so it is read
    from that run's listings without another request.
    """
    from shared import remote_files

    try:
        listing = remote_files.get_listing(sync + "Inbox/")
    except Exception:  # noqa: BLE001 - links fall back to the Inbox
        return None
    return set(re.findall(r"(?<![\w-])(R1-\d{7})\.zip\b", unquote(listing.text)))


def tdoc_links(record: dict, sync: str | None, inbox: set[str] | None = None) -> dict[str, str]:
    """Where each TDoc of a mail opens: the file the mail links, else the TDoc in the sync folder.

    A link to the venue's server or the sync folder is read on the sync
    folder (``meeting_sync``), which mirrors it and is reachable from
    anywhere: a draft FL summary has no TDoc of its own yet, only the draft
    the FL linked. Another 3GPP link is kept; a link elsewhere is not.
    Older records have no ``links``; their reply still holds bare links.

    A TDoc no link names is in the Inbox: what a mail announces during the
    meeting is uploaded there; Docs holds the contributions made before it.
    So it opens in Docs only when ``inbox`` (the Inbox's TDocs) is known
    and does not have it.
    """
    urls = list(record.get("links") or []) + document_links(record.get("reply") or "")
    out = {}
    for tdoc in record.get("tdocs") or []:
        for url in urls:
            if tdoc not in unquote(url.rsplit("/", 1)[-1]):
                continue
            mirrored = _MIRRORED.match(url)
            if mirrored and sync:
                out[tdoc] = sync + url[mirrored.end():]
                break
            if not mirrored and re.match(r"https?://www\.3gpp\.org/", url, re.IGNORECASE):
                out[tdoc] = "https" + url[url.index(":"):]
                break
        else:
            if sync:
                folder = "Docs" if inbox is not None and tdoc not in inbox else "Inbox"
                out[tdoc] = f"{sync}{folder}/{tdoc}.zip"
    return out


def _on_date(item: dict, sent: datetime) -> str:
    """The date an informal meeting is on: its weekday's next date from the mail, else the mail's day."""
    if item.get("day") in DAYS:
        ahead = (DAYS.index(item["day"]) - sent.weekday()) % 7
        return (sent.date() + timedelta(days=ahead)).isoformat()
    return sent.date().isoformat()


def apply_records(schedule: Schedule, sync: str | None = None, inbox: set[str] | None = None) -> None:
    """Put every recorded answer on the grid as it is now.

    Sessions and tentative slots get ``email_details``, oldest first;
    off-offline goes to ``schedule.email_informal`` by agenda item. With
    ``sync`` (the meeting_sync folder), each detail's TDoc numbers get
    their links (``tdoc_links``, ``inbox`` the TDocs listed in its Inbox).
    """
    records = load_records(schedule.meeting_id)
    cells, sessions = cells_of(schedule)
    tz = ZoneInfo(schedule.timezone or "UTC")
    for session in sessions.values():
        session.email_details = []
    schedule.email_informal = {}
    # A plan (a stated time or a split) of a later mail replaces the plan
    # of an earlier mail of the thread for the same session; anything else
    # is added. #36 replaced the times of #38; #3 ("FL proposal for
    # online") adds to the 12:00 online of #15.
    plans: dict[tuple, list[dict]] = {}
    notes: dict[tuple, list[dict]] = {}
    resolved = _history(records.values(), cells, tz)
    for record in sorted(records.values(), key=lambda r: r["date"]):
        if record.get("status") != "asked":
            continue
        source = {"mail": record["id"], "author": record.get("author", ""), "sent": record["date"]}
        links = tdoc_links(record, sync, inbox)
        mine: dict[tuple, list[dict]] = {}
        for item in resolved[record["id"]]:
            item = {**item, **source}
            if any(t in links for t in item.get("tdocs") or []):
                item["tdoc_links"] = {t: links[t] for t in item["tdocs"] if t in links}
            if item["kind"] == "informal":
                item["date"] = _on_date(item, _sent(record["date"], tz))
                schedule.email_informal.setdefault(record["agenda_items"][0], []).append(item)
                continue
            key = (record.get("thread") or record["id"], item["session"])
            if item.get("start") or item.get("end") or item.get("split"):
                mine.setdefault(key, []).append(item)
            else:
                notes.setdefault(key, []).append(item)
        plans.update(mine)
    by_session: dict[str, list[dict]] = {}
    for key in set(plans) | set(notes):
        by_session.setdefault(key[1], []).extend(plans.get(key, []) + notes.get(key, []))
    for session_id, items in by_session.items():
        # Oldest first across threads; the order of a set must not reach the page.
        sessions[session_id].email_details.extend(sorted(items, key=lambda item: (item["sent"], item["mail"])))


def run(schedule: Schedule, *, offline: bool, sync: str | None = None) -> None:
    """The build's step: read new mails unless offline, then show what is recorded. Never fails the build."""
    try:
        if not offline:
            new = update_records(schedule)
            if new:
                asked = sum(r["status"] == "asked" for r in new)
                print(f"  Email discussions: {len(new)} new mail(s) recorded, {asked} asked")
        apply_records(schedule, sync, inbox_tdocs(sync) if sync and not offline else None)
    except Exception as exc:  # noqa: BLE001 - the schedule is published without email details
        print(f"Warning: email session details unavailable: {exc}")
