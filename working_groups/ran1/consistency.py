"""Checks a merged RAN1 schedule must pass before it is published.

A failed check rejects the build and discards the slot states involved, so an
operator sees the failure and the next build interprets those slots cold
instead of reusing the result.  Nothing is repaired automatically.

A duplicate that a cold reading gives too is in the source itself (RAN1#127
Main v01: Tuesday 14:30 10.3.2 in BMO 225-226 and BMO 221). It is published
as written, with a notice.
"""

from __future__ import annotations

import re
from collections import defaultdict

from shared.lifecycle import RejectedState
from shared.schedule import Session, time_to_minutes

from .models import TIME_BLOCKS
from .slot_state import slot_state_path

# Agenda numbers as they appear in ``agenda_item``: "10.5.4.1", "10.6.x", "9".
_AGENDA_NUMBER = re.compile(r"\d+(?:\.\d+)*(?:\.[xX])?")


def _agenda_numbers(session: Session) -> set[str]:
    # Only the numbers the schedule gives; inferred ones are a guess.
    return set(_AGENDA_NUMBER.findall(session.agenda_item or ""))


def _title_numbers(numbers) -> set[str]:
    """Numbers that head other items: "10.5.2.x", or "10.5.2" when "10.5.2.1"
    exists.  Two rooms can work under one title at the same time, each on
    its own sub-item."""
    numbers = set(numbers)
    titles = {n for n in numbers if n.lower().endswith(".x")}
    for number in numbers:
        parts = number.split(".")
        titles.update(".".join(parts[:i]) for i in range(1, len(parts)))
    return titles


def _overlaps(a: Session, b: Session) -> bool:
    return (time_to_minutes(a.start_time) < time_to_minutes(b.end_time)
            and time_to_minutes(b.start_time) < time_to_minutes(a.end_time))


def _time_block_index(session: Session, blocks=TIME_BLOCKS) -> int | None:
    start = time_to_minutes(session.start_time)
    for block in blocks:
        if time_to_minutes(block["start"]) <= start < time_to_minutes(block["end"]):
            return block["index"]
    return None


def concurrent_agenda_duplicates(
    sessions: list[Session],
    agenda_items=(),
) -> list[tuple[str, Session, Session]]:
    """Pairs of sessions in different rooms that overlap in time and share a
    detailed agenda number.

    RAN1 does not discuss one detailed agenda item in two rooms at once.  A
    pair like this means one source was placed in the wrong room (RAN1#126bis
    Thursday: Hiroki's 10.5.4.x in both Tamna BC Hall and Yeongju B Hall,
    which also dropped Tamna's own 10.5.3.x).  Title numbers ("10.5.2.x",
    "10.5.2") are not compared: sessions under one title can run in parallel
    on different sub-items.  ``agenda_items`` are this meeting's agenda
    numbers; the schedule's own numbers also tell which numbers are titles.
    """
    by_day: dict[str, list[Session]] = defaultdict(list)
    numbers = set(agenda_items)
    for session in sessions:
        if _agenda_numbers(session):
            by_day[session.day].append(session)
            numbers |= _agenda_numbers(session)
    titles = _title_numbers(numbers)
    pairs = []
    for day_sessions in by_day.values():
        for i, a in enumerate(day_sessions):
            for b in day_sessions[i + 1:]:
                if (a.room_col_start, a.room_col_end) == (b.room_col_start, b.room_col_end):
                    continue
                if not _overlaps(a, b):
                    continue
                shared = (_agenda_numbers(a) & _agenda_numbers(b)) - titles
                for number in sorted(shared):
                    pairs.append((number, a, b))
    return pairs


def _room(session: Session, day_rooms_map) -> str:
    rooms = (day_rooms_map or {}).get(session.day, [])
    # Grid column 1 is the time axis; room columns start at 2.
    names = [room.name for room in rooms[session.room_col_start - 2:session.room_col_end - 2]]
    return " + ".join(names) or f"room column {session.room_col_start}"


def _slot_paths(session: Session, blocks) -> set:
    index = _time_block_index(session, blocks)
    if index is None:
        # Unknown slot: the whole day rather than keep a bad one.
        return {slot_state_path(session.day, block["index"]) for block in blocks}
    return {slot_state_path(session.day, index)}


def reject_concurrent_agenda_duplicates(
    sessions: list[Session], day_rooms_map=None, agenda_items=(), blocks=TIME_BLOCKS,
    incremental=None, meeting_id: str | None = None,
) -> dict[str, list[str]]:
    """Fail the build when a detailed agenda item is in two rooms at the same time.

    The slot states of every slot involved are discarded, so the next build
    interprets them cold.  ``incremental`` names the slot states merged
    incrementally in this run; when given, a duplicate from slots read cold
    (or reused, so read cold and kept before) is what the document says: it
    is published and a notice names it.  Without it every duplicate fails.
    Returns the published duplicates as caveats by day, for the page.
    """
    pairs = concurrent_agenda_duplicates(sessions, agenda_items)
    caveats: dict[str, list[str]] = defaultdict(list)
    if not pairs:
        return caveats
    lines, written = [], []
    discard = set()
    for number, a, b in pairs:
        line = (
            f"{a.day} AI {number}: '{a.name}' {a.start_time}-{a.end_time} in "
            f"{_room(a, day_rooms_map)} and '{b.name}' {b.start_time}-{b.end_time} "
            f"in {_room(b, day_rooms_map)}"
        )
        slots = _slot_paths(a, blocks) | _slot_paths(b, blocks)
        if incremental is not None and not slots & set(incremental):
            written.append(line)
            caveats[a.day].append(
                f"AI {number} is in two rooms at the same time: {a.start_time}–{a.end_time} in "
                f"{_room(a, day_rooms_map)} and {b.start_time}–{b.end_time} in {_room(b, day_rooms_map)}. "
                "The schedule documents show it this way too, so this page keeps it as written. "
                "Check with the session chair before you go. Another schedule document or "
                "the next update will probably resolve it."
            )
            continue
        lines.append(line)
        discard |= slots
    if written:
        from shared import notices
        notices.add(
            "ran1", "source-duplicate",
            f"ran1:{meeting_id}:source-duplicate:" + "|".join(written),
            "RAN1: the schedule puts one agenda item in two rooms at the same time",
            [f"Meeting: {meeting_id or 'unknown'}", *written,
             "Read from scratch it comes out the same, so the page shows it as the document has it."],
        )
    if not lines:
        return caveats
    raise RejectedState(
        "Same agenda item in two rooms at the same time; discarded slot state "
        f"{', '.join(sorted(p.stem for p in discard))} for a cold retry:\n  "
        + "\n  ".join(lines),
        discard=sorted(discard),
    )
