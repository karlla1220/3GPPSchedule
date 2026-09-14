"""Data models for 3GPP schedule parsing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shared.schedule import (DaySchedule, RoomInfo, Schedule, Session, Timeline,
                             time_to_minutes, minutes_to_time)


# RAN1-specific time blocks
TIME_BLOCKS = [
    {"index": 0, "start": "08:30", "end": "10:30", "duration": 120},
    {"index": 1, "start": "11:00", "end": "13:00", "duration": 120},
    {"index": 2, "start": "14:30", "end": "16:30", "duration": 120},
    {"index": 3, "start": "17:00", "end": "19:30", "duration": 150},
]

BREAKS = [
    {"name": "Morning Coffee Break", "start": "10:30", "end": "11:00"},
    {"name": "Lunch Break", "start": "13:00", "end": "14:30"},
    {"name": "Afternoon Coffee Break", "start": "16:30", "end": "17:00"},
]

DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


@dataclass
class CellData:
    """Raw cell data extracted from DOCX table."""

    text: str
    day: str
    room_indices: list[int]  # which room columns this cell spans within its table group
    time_block_index: int
    time_block_start: str
    time_block_end: str
    time_block_duration: int
    table_index: int
    fallback_start_time: str | None = None


@dataclass
class ScheduleSource:
    """A discovered schedule source from the FTP server."""

    folder_name: str
    person_name: str | None  # e.g. "Hiroki" (None for main chair schedule)
    is_main: bool  # True for Chair_notes (main schedule)
    file_info: dict  # {"name": ..., "url": ..., "uploaded_at": ...}
    local_path: Path | None = None
    origin: str | None = None  # provenance stays stable after downloading

    def __post_init__(self):
        if self.origin is None:
            self.origin = "local" if self.local_path is not None else "remote"


def ran1_timeline() -> Timeline:
    return Timeline(start="08:30", end="19:45", slot_minutes=5,
                    label_minutes=30, breaks=[dict(b) for b in BREAKS],
                    blocks=[dict(b) for b in TIME_BLOCKS])
