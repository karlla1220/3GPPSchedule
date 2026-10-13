"""Checks a merged RAN1 schedule must pass before it is published.

A failed check rejects the build and discards the slot states involved, so an
operator sees the failure and the next build interprets those slots cold
instead of reusing the result.  Nothing is repaired automatically.
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


def _overlaps(a: Session, b: Session) -> bool:
    return (time_to_minutes(a.start_time) < time_to_minutes(b.end_time)
            and time_to_minutes(b.start_time) < time_to_minutes(a.end_time))


def _time_block_index(session: Session) -> int | None:
    start = time_to_minutes(session.start_time)
    for block in TIME_BLOCKS:
        if time_to_minutes(block["start"]) <= start < time_to_minutes(block["end"]):
            return block["index"]
    return None


def concurrent_agenda_duplicates(
    sessions: list[Session],
) -> list[tuple[str, Session, Session]]:
    """Pairs of sessions in different rooms that overlap in time and share an
    agenda number.

    RAN1 does not discuss one agenda item in two rooms at once.  A pair like
    this means one source was placed in the wrong room (RAN1#126bis Thursday:
    Hiroki's 10.5.4.x in both Tamna BC Hall and Yeongju B Hall, which also
    dropped Tamna's own 10.5.3.x).
    """
    by_day: dict[str, list[Session]] = defaultdict(list)
    for session in sessions:
        if _agenda_numbers(session):
            by_day[session.day].append(session)
    pairs = []
    for day_sessions in by_day.values():
        for i, a in enumerate(day_sessions):
            for b in day_sessions[i + 1:]:
                if (a.room_col_start, a.room_col_end) == (b.room_col_start, b.room_col_end):
                    continue
                if not _overlaps(a, b):
                    continue
                for number in sorted(_agenda_numbers(a) & _agenda_numbers(b)):
                    pairs.append((number, a, b))
    return pairs


def _room(session: Session, day_rooms_map) -> str:
    rooms = (day_rooms_map or {}).get(session.day, [])
    # Grid column 1 is the time axis; room columns start at 2.
    names = [room.name for room in rooms[session.room_col_start - 2:session.room_col_end - 2]]
    return " + ".join(names) or f"room column {session.room_col_start}"


def reject_concurrent_agenda_duplicates(sessions: list[Session], day_rooms_map=None) -> None:
    """Fail the build when an agenda item is in two rooms at the same time.

    The slot states of every slot involved are discarded, so the next build
    interprets them cold.  If the duplicate comes back, it fails again.
    """
    pairs = concurrent_agenda_duplicates(sessions)
    if not pairs:
        return
    lines = []
    discard = set()
    for number, a, b in pairs:
        lines.append(
            f"{a.day} AI {number}: '{a.name}' {a.start_time}-{a.end_time} in "
            f"{_room(a, day_rooms_map)} and '{b.name}' {b.start_time}-{b.end_time} "
            f"in {_room(b, day_rooms_map)}"
        )
        for session in (a, b):
            index = _time_block_index(session)
            if index is None:
                # Unknown slot: discard the whole day rather than keep a bad one.
                discard.update(slot_state_path(session.day, block["index"])
                               for block in TIME_BLOCKS)
            else:
                discard.add(slot_state_path(session.day, index))
    raise RejectedState(
        "Same agenda item in two rooms at the same time; discarded slot state "
        f"{', '.join(sorted(p.stem for p in discard))} for a cold retry:\n  "
        + "\n  ".join(lines),
        discard=sorted(discard),
    )
