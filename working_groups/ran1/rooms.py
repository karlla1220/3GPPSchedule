"""Which rooms of a RAN1 schedule hold online sessions.

The chair's schedule document heads each table with what it holds:
"RAN1#126b Online Session Schedule" over the main room and the breakout
rooms (Tamna BC Hall, Yeongju A Hall, Yeongju B Hall), "RAN1#126b Offline
Session Schedule" over the offline rooms (201, 203). The parser reads that
heading into ``RoomInfo.kind`` (``table_kind``). A schedule whose headings
say neither (or one saved before the kind was read) falls back to the order
of the tables, which does not change during a meeting: the first table is
the online rooms.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from shared.schedule import RoomInfo

_KIND = re.compile(r"\b(online|offline)\b", re.IGNORECASE)


def table_kind(heading: str) -> str:
    """"online" or "offline" from the text above a table, else "".

    The text runs from the previous table and holds the room text boxes
    first, so the last word is the table's own heading.
    """
    words = _KIND.findall(heading or "")
    return words[-1].lower() if words else ""


def online_rooms(rooms: Iterable[RoomInfo]) -> list[str]:
    """Names of the online rooms, in their order."""
    rooms = list(rooms)
    if not rooms:
        return []
    if any(room.kind for room in rooms):
        return [room.name for room in rooms if room.kind == "online"]
    first = min(room.table_index for room in rooms)
    return [room.name for room in rooms if room.table_index == first]


def tells_kinds(rooms: Iterable[RoomInfo]) -> bool:
    """Whether the rooms tell online from offline: headings, or more than one table."""
    rooms = list(rooms)
    return any(room.kind for room in rooms) or len({room.table_index for room in rooms}) > 1
