"""Session details from email discussion mails: an LLM reads, code decides.

A feature lead posts the plan of a session on the thread of their agenda
item: "Offline 17:00~18:00: 17:00~17:30 PDSCH, 17:30~18:00 RS for PDSCH",
"proposals for Wednesday morning online", "off-offline at 10:45 in front of
Yeongju A Hall". The code finds the thread, its agenda item and the grid
sessions of that item still to come (``email_sessions``); the LLM reads the
newest reply and says, per announcement, which of those sessions it names
and what it adds. The code then checks the answer against the grid:

- ``session``: an online or offline names a session of the grid. Both are
  held in a room, so they are always a cell; its detail goes to that cell.
  An online is in an online room (the main and breakout rooms, ``rooms``),
  an offline in an offline room.
- ``tentative``: an online at a time the grid has no session of the item
  for. The FL arranged it with the chair before the schedule caught up, in
  the online room the item's sessions are held in, so it goes to the cell of
  that room at that time as "maybe <item>" ("maybe 10.3.1" on the 8.1 cell).

The session is found again on every build from what the mail says (agenda
item, online or offline, day, part of day, time), not from the id the LLM
picked: the schedule changes after a mail, and FLs write from the chair's
forecast before it does (``resolve``).
- ``informal``: off-offline or an FL waiting somewhere, outside the grid:
  outside, in front of or near a room, or no room at all.

TDoc numbers come from the mail text by pattern, never from the LLM.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

EXTRACT_MODEL = "gemini-3.8-flash"
EXTRACT_THINKING = "low"
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
PARTS = ["morning", "afternoon", "evening"]


@dataclass(frozen=True)
class Cell:
    """A session of the grid as the mails see it."""

    id: str
    day: str
    start: str
    end: str
    room: str
    name: str
    agenda_item: str | None = None
    inferred_agenda_items: tuple[str, ...] = field(default_factory=tuple)
    online: bool | None = None  # in an online room; None when the grid does not tell
    part: str | None = None  # morning, afternoon or evening of its day (``part_of``)

    def items(self) -> list[str]:
        stated = re.split(r"[,/]\s*", self.agenda_item or "") + list(self.inferred_agenda_items)
        return [item.strip() for item in stated if item and item.strip()]


def covers(cell: Cell, ai: str) -> bool:
    """The cell is about ``ai``: one of its items, "10.6.x" for all of 10.6, a parent or a child."""
    for item in cell.items():
        root = item[:-2] if item.endswith(".x") else item
        if ai == root or ai.startswith(root + ".") or root.startswith(ai + "."):
            return True
    return False


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def day_parts(breaks: list[dict]) -> tuple[int, int, int | None] | None:
    """Where a day's morning ends and its afternoon and evening start, read from its breaks.

    Morning is before the lunch break, afternoon after it, evening after the
    last break that follows lunch (#126bis: 08:30-13:00, from 14:30, from
    17:00). No time of day is assumed: a day without a lunch break has no parts.
    """
    lunch = next((b for b in breaks if "lunch" in str(b.get("name", "")).lower()), None)
    if lunch is None:
        return None
    after = [b for b in breaks if _minutes(b["start"]) >= _minutes(lunch["end"])]
    return _minutes(lunch["start"]), _minutes(lunch["end"]), (_minutes(after[-1]["end"]) if after else None)


def part_of(start: str, parts: tuple[int, int, int | None] | None) -> str | None:
    """The part of the day a session starting at ``start`` is in, or None (unknown, or over lunch)."""
    if parts is None:
        return None
    minute, (lunch_start, lunch_end, evening) = _minutes(start), parts
    if minute < lunch_start:
        return "morning"
    if evening is not None and minute >= evening:
        return "evening"
    return "afternoon" if minute >= lunch_end else None


def cell_start(cell: Cell, first_day: date, tz: ZoneInfo) -> datetime:
    """When the cell starts. ``first_day`` is the date of the meeting's first day (a Monday)."""
    offset = (DAYS.index(cell.day) - first_day.weekday()) % 7
    day = first_day + timedelta(days=offset)
    return datetime.combine(day, datetime.min.time(), tz) + timedelta(minutes=_minutes(cell.start))


def candidates(cells: list[Cell], agenda_items: list[str], sent: datetime,
               first_day: date, tz: ZoneInfo) -> list[Cell]:
    """Sessions of the mail's items that start after it was sent, in time order."""
    found = [
        cell for cell in cells
        if any(covers(cell, ai) for ai in agenda_items) and cell_start(cell, first_day, tz) > sent
    ]
    return sorted(found, key=lambda cell: cell_start(cell, first_day, tz))


EXTRACT_INSTRUCTION = """You read one mail from a 3GPP RAN1 email discussion thread, sent during the meeting by a delegate (usually the feature lead, FL) of the thread's agenda item. Find every announcement in it about a session or meeting that is still to come, and say what each one is.

Kinds
- "session": an online or offline session. Both are always held in a meeting room and are on the meeting's schedule. Material announced "for online", "for the online session", "for Wednesday's offline", "for upcoming online" names such a session too, even without a time.
- "informal": an off-offline (also written "off-off", "offoff") or an FL waiting to take questions. These are held outside, in front of or near a room, in a corridor or lobby, or with no room. "outside Sorour's session room", "in front of Yeongju A Hall", "near the bears on the 3rd floor", "I'll be in the main room" are informal.
- A mail can hold several announcements (an offline and an online, an online and an off-offline). Give each its own item.
- "offline/online on Wednesday", "for offline/online discussion tomorrow": two sessions, one item each. When the mail does not tell which listed session is the offline and which the online, give one item for each listed session of that day, in time order.
- An input deadline tied to a session is a "session" item of that session: "inputs until 1 hr before our offline session today", "deadline … 2 hours before Wednesday online". Put the deadline in input_due.
- Not announcements: deadlines tied to no session ("comments by 14:00 Wed"), sessions that are over ("thanks for the discussion in the offline"), uploads that name no session, and statements that something will not be discussed. A mail with none of these returns no items.

Sessions
- The user message lists the schedule's sessions of the thread's agenda items that start after the mail was sent. For a "session" item, give the id of the one it names, judged by day, time, online/offline and order ("next", "upcoming", "this afternoon"). Use exactly an id from the list.
- Each listed session says whether its room is an online room (the main room and the breakout rooms, where the chair or a vice-chair holds online sessions) or an offline room. An online is held in an online room, an offline in an offline room.
- When the mail states a time and no listed session of that day fits it, or names no session the list has, give session_id null and the day and time as stated. Do not move the item to another session.
- The user message may list earlier announcements on the same thread. A mail that refers to one of them ("the FL proposal for online" after "online session from 12:00") is about that one: give its session_id, or session_id null with its day and time when it had no session.
- Days: read "today", "tomorrow", "this morning" from the sending time given in meeting time. A mail sent after midnight but before the morning often still calls the coming day "tomorrow"; a weekday or a deadline in the mail decides.

Fields
- quote: the words of the mail the item comes from, verbatim, at most 200 characters.
- mode: "online", "offline" or "off-offline" as the mail says it, else null.
- day: the full weekday name in meeting time, else null. start, end: "HH:MM" in meeting time as stated, else null.
- part_of_day: "morning", "afternoon" or "evening" when the mail names it ("Friday morning online", "this afternoon", "evening session"), else null. Not worked out from a time.
- place: where an informal meeting is held, as written, else null.
- topics: what will be treated, short, from the mail: issues, proposal numbers, parts, sections ("Part 1 (PUSCH MIMO)", "Proposal 3.1.1-4", "Section 7.1"). Empty when the mail does not say.
- split: when the mail divides the session by time or by agenda item, one entry per part in the mail's order: start/end "HH:MM" only if the mail states them, minutes if it states a length, agenda_item if the part is an agenda item number, topic otherwise. Never work out a time the mail does not state. Empty when not divided.
- input_due: the input deadline for the session: at "HH:MM" when a clock time is stated, minutes_before when it is stated relative to the session ("1 hr before" is 60). Null when the mail sets none.
- summary: one line in English, at most 80 characters, of what the delegate needs to know, without day and time (shown beside it). Keep proposal and section numbers."""

_NULLABLE_STRING = {"type": "string", "nullable": True}
EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string"},
                    "kind": {"type": "string", "enum": ["session", "informal"]},
                    "mode": {"type": "string", "enum": ["online", "offline", "off-offline"], "nullable": True},
                    "session_id": _NULLABLE_STRING,
                    "day": {"type": "string", "enum": DAYS[:5], "nullable": True},
                    "part_of_day": {"type": "string", "enum": PARTS, "nullable": True},
                    "start": _NULLABLE_STRING,
                    "end": _NULLABLE_STRING,
                    "place": _NULLABLE_STRING,
                    "topics": {"type": "array", "items": {"type": "string"}},
                    "split": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "start": _NULLABLE_STRING,
                                "end": _NULLABLE_STRING,
                                "minutes": {"type": "integer", "nullable": True},
                                "agenda_item": _NULLABLE_STRING,
                                "topic": _NULLABLE_STRING,
                            },
                        },
                    },
                    "input_due": {
                        "type": "object",
                        "nullable": True,
                        "properties": {
                            "at": _NULLABLE_STRING,
                            "minutes_before": {"type": "integer", "nullable": True},
                        },
                    },
                    "summary": {"type": "string"},
                },
                "required": ["quote", "kind", "session_id", "topics", "split", "summary"],
            },
        },
        "no_items_reason": {"type": "string"},
    },
    "required": ["items"],
}


def _announced(item: dict) -> str:
    when = " ".join(filter(None, [item.get("day"), "-".join(filter(None, [item.get("start"), item.get("end")]))]))
    where = {
        "session": f"session {item.get('session')}",
        "tentative": "no session of the item at that time on the schedule",
        "informal": item.get("place") or "informal",
    }[item["kind"]]
    return f'- {item.get("mode") or item["kind"]} {when or "(no time)"}, {where}: "{item.get("quote", "")[:120]}"'


def request_text(title: str, sent: datetime, agenda_items: list[str], agenda: dict[str, str],
                 cells: list[Cell], reply: str, earlier: list[dict] = ()) -> str:
    """The user message for one mail. ``earlier``: resolved items of earlier mails on the thread."""
    items = "\n".join(f"{ai}\t{agenda.get(ai, '')}" for ai in agenda_items) or "(unknown)"
    kind = {True: " (online room)", False: " (offline room)", None: ""}
    sessions = "\n".join(
        f'{cell.id}\t{cell.day} {cell.start}-{cell.end}\troom: {cell.room}{kind[cell.online]}\tcell: "{cell.name}"'
        for cell in cells
    ) or "(none)"
    before = ""
    if earlier:
        before = "Earlier announcements on this thread (oldest first):\n" + "\n".join(map(_announced, earlier)) + "\n\n"
    late = ""
    if sent.hour < 5:
        # Up after midnight, the writer still lives the day before: #126bis
        # 10.5.3.1 sent Wednesday 01:42 "PM session tomorrow" for Wednesday.
        before_day = (sent - timedelta(days=1)).strftime("%A")
        late = (f"Sent after midnight: to the writer, 'today' may still be {before_day} "
                f"and 'tomorrow' {sent:%A}.\n")
    return (
        f"Subject: {title}\n"
        f"Sent: {sent:%A %d %b %H:%M} meeting time\n"
        f"{late}"
        f"Agenda items of the thread:\n{items}\n\n"
        f"Sessions of these items after the mail was sent (id, day and time, room, cell title):\n{sessions}\n\n"
        f"{before}"
        f"Newest reply of the mail (quoted earlier mails removed):\n{reply}"
    )


def gemini_extract(text: str) -> dict:
    """Ask Gemini for the announcements of one mail."""
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    client = genai.Client(api_key=api_key, http_options={"timeout": 120_000})
    response = client.models.generate_content(
        model=EXTRACT_MODEL,
        contents=text,
        config=types.GenerateContentConfig(
            system_instruction=EXTRACT_INSTRUCTION,
            response_mime_type="application/json",
            response_json_schema=EXTRACT_SCHEMA,
            thinking_config=types.ThinkingConfig(thinking_level=EXTRACT_THINKING),
        ),
    )
    return json.loads(response.text.strip())


_TIME = re.compile(r"([01]?\d|2[0-3])[:.]([0-5]\d)")


def _time(value) -> int | None:
    m = _TIME.fullmatch((value or "").strip())
    return int(m[1]) * 60 + int(m[2]) if m else None


_CLOCK = re.compile(r"(?<![\d.:])([01]?\d|2[0-3])(?:[:.]([0-5]\d))?\s*([ap])?\.?\s*m?\b", re.IGNORECASE)


def times_in(text: str) -> set[int]:
    """Clock times a text states, in minutes, both halves of the day ("3:30pm", "15:30", "12:00am").

    Used to keep only times the mail states: an LLM can put a time on an
    item that its mail never gave.
    """
    found = set()
    for m in _CLOCK.finditer(text or ""):
        if m[2] is None and m[3] is None:
            continue  # a bare number is not a time
        hour, minute = int(m[1]), int(m[2] or 0)
        for h in {hour % 12, hour % 12 + 12, hour}:
            if h < 24:
                found.add(h * 60 + minute)
    return found


def _overlaps(cell: Cell, start: int | None, end: int | None) -> bool:
    if start is None:
        return True
    end = end if end is not None and end > start else start + 1
    return start < _minutes(cell.end) and end > _minutes(cell.start)


def _home_room(cells: list[Cell], ai: str) -> str | None:
    """The online room most sessions stated for ``ai`` are held in."""
    rooms = Counter(cell.room for cell in cells
                    if cell.agenda_item and covers(cell, ai) and cell.online is not False)
    return rooms.most_common(1)[0][0] if rooms else None


def _split(parts: list[dict], allowed: set[int] | None = None) -> list[dict]:
    """Parts as the mail states them. A length stays a length: no clock time is assumed from it."""
    out = []
    for part in parts or []:
        start, end, minutes = _time(part.get("start")), _time(part.get("end")), part.get("minutes")
        if allowed is not None:
            start = start if start in allowed else None
            end = end if end in allowed else None
        entry = {k: part[k] for k in ("agenda_item", "topic") if part.get(k)}
        if start is not None:
            entry["start"] = _hhmm(start)
        if end is not None:
            entry["end"] = _hhmm(end)
        if isinstance(minutes, int) and minutes > 0:
            entry["minutes"] = minutes
        out.append(entry)
    return out


def _input_due(due) -> dict | None:
    """The input deadline as the mail states it: a clock time, or minutes before the session.

    A relative deadline stays relative: the session may not start on time.
    """
    if not isinstance(due, dict):
        return None
    at = _time(due.get("at"))
    if at is not None:
        return {"at": _hhmm(at)}
    before = due.get("minutes_before")
    return {"minutes_before": before} if isinstance(before, int) and before > 0 else None


_MODE_ONLINE = {"online": True, "offline": False}
_CELL_ID = re.compile(r"(\w{3}) (\d\d:\d\d)-(\d\d:\d\d) (.+)")


def _part_fits(stated: str | None, part: str | None) -> bool:
    # "this afternoon" also covers the evening block (17:00 is still afternoon to most).
    return stated is None or part == stated or (stated == "afternoon" and part == "evening")


def _bind(item: dict, *, day: str | None, start: int | None, end: int | None, offered: list[Cell],
          asked: set[str], day_parts: dict) -> Cell | None:
    """The session of the grid as it is now that the item names, or None.

    A session fits what the mail says: online or offline room, day, part of
    day, time. The id the LLM picked counts while it fits. When it no longer
    does (the schedule changed) or the mail was read before the schedule had
    the session (the chair's forecast), the one session that fits is taken;
    of several, the one on the day and part of day of the picked id if the
    grid changed under it. Without an id, a day or a time, nothing is taken.
    """
    part = item.get("part_of_day") if item.get("part_of_day") in PARTS else None
    online = _MODE_ONLINE.get(item.get("mode"))

    def fits(cell: Cell) -> bool:
        return ((day is None or cell.day == day) and _part_fits(part, cell.part) and _overlaps(cell, start, end)
                and (online is None or cell.online is None or cell.online == online))

    picked = item.get("session_id") if item.get("session_id") in asked else None
    hint = next((cell for cell in offered if cell.id == picked), None)
    if hint is not None and fits(hint):
        return hint
    if picked is None and day is None and start is None:
        return None
    found = [cell for cell in offered if fits(cell)]
    if len(found) == 1:
        return found[0]
    # The day and part of day of the picked id break a tie only when the grid
    # changed under it. A pick that is still there but does not fit (an
    # online room for an "offline") contradicts the mail, and its time says
    # nothing about which session the mail meant.
    when = _CELL_ID.fullmatch(picked or "") if hint is None else None
    if when:
        on = next((d for d in DAYS if d.startswith(when[1])), None)
        then = part_of(when[2], day_parts.get(on))
        found = [cell for cell in found if cell.day == on and (then is None or cell.part == then)]
        if len(found) == 1:
            return found[0]
    return None


def resolve(answer: dict, *, offered: list[Cell], cells: list[Cell], agenda_items: list[str],
            sent: datetime, tdocs: list[str] = (), allowed_times: set[int] | None = None,
            asked: list[str] | None = None, day_parts: dict | None = None) -> list[dict]:
    """The LLM's items checked against the grid: session, tentative or informal.

    ``offered`` are the item's sessions after the mail in the grid as it is
    now, ``asked`` the ids the LLM was shown (``offered`` when it was asked
    just now), ``day_parts`` the parts of each day (``day_parts``).

    A session item goes to the session that fits what the mail says
    (``_bind``). Otherwise an online with a stated time is placed in the
    item's online room at that time (tentative), and one that cannot be
    placed is dropped rather than shown on a guess. Times are only those the
    mail states: lengths and "1 hr before" are kept as written, and with
    ``allowed_times`` (the times of the mail and the earlier announcements
    it was given) a time from anywhere else is dropped.

    Only an online goes to another item's slot. An "offline" at a time the
    schedule does not have is an off-offline: online sessions need the
    chair, offline is what FLs also call their off-offlines ("start the
    offline at 3:30pm", then "same place as yesterday").
    """
    asked = set(asked if asked is not None else (cell.id for cell in offered))
    day_parts = day_parts or {}
    out = []
    for item in answer.get("items") or []:
        if not isinstance(item, dict):
            continue
        start, end = _time(item.get("start")), _time(item.get("end"))
        if allowed_times is not None:
            start = start if start in allowed_times else None
            end = end if end in allowed_times else None
        day = item.get("day") if item.get("day") in DAYS else None
        base = {
            "quote": item.get("quote") or "",
            "mode": item.get("mode"),
            "summary": item.get("summary") or "",
            "topics": [t for t in item.get("topics") or [] if isinstance(t, str) and t.strip()],
        }
        if start is not None:
            base["start"] = _hhmm(start)
        if end is not None:
            base["end"] = _hhmm(end)
        if day:
            base["day"] = day
        if tdocs:
            base["tdocs"] = list(tdocs)
        if item.get("kind") == "informal":
            out.append({"kind": "informal", **base, **({"place": item["place"]} if item.get("place") else {})})
            continue
        cell = _bind(item, day=day, start=start, end=end, offered=offered, asked=asked, day_parts=day_parts)
        if cell is not None:
            due = _input_due(item.get("input_due"))
            out.append({"kind": "session", "session": cell.id, **base, **({"input_due": due} if due else {}),
                        "split": _split(item.get("split"), allowed_times)})
            continue
        if start is None or not agenda_items:
            continue
        if item.get("mode") != "online":
            out.append({"kind": "informal", **base})
            continue
        ai = agenda_items[0]
        room = _home_room(cells, ai)
        on = day or DAYS[sent.weekday()]
        host = next((c for c in cells if c.room == room and c.day == on and _overlaps(c, start, end)), None)
        if host is None or covers(host, ai):
            continue
        out.append({"kind": "tentative", "session": host.id, "agenda_item": ai, **base,
                    "split": _split(item.get("split"), allowed_times)})
    return out
