"""Which rooms of a RAN1 schedule hold online sessions.

The main schedule's first table is the chair's rooms: the main room, then the
breakout rooms (RAN1#126bis: Tamna BC Hall, Yeongju A Hall, Yeongju B Hall).
An online session is held in one of them. The rooms of the other tables are
offline rooms (201, 203). This rests on the layout of the chair's document:
if a meeting orders its tables differently, it is wrong for that meeting.
"""

from __future__ import annotations

from collections.abc import Iterable


def online_rooms(rooms: Iterable[tuple[str, int]]) -> list[str]:
    """Names of the online rooms among ``(name, table_index)`` pairs, in their order."""
    rooms = list(rooms)
    if not rooms:
        return []
    first = min(table for _, table in rooms)
    return [name for name, table in rooms if table == first]
