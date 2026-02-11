"""Data models for 3GPP schedule parsing."""

from __future__ import annotations

from dataclasses import dataclass, field


# Time blocks in the standard 3GPP meeting day
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


@dataclass
class RoomInfo:
    """A room across all tables for a given day."""

    name: str
    table_index: int
    room_index_in_table: int  # position within the table's rooms for this day


@dataclass
class Session:
    """A parsed session with calculated times."""

    name: str
    duration_minutes: int
    start_time: str  # "HH:MM"
    end_time: str  # "HH:MM"
    day: str
    room_col_start: int  # 1-indexed grid column (within a day's rooms)
    room_col_end: int  # exclusive
    chair: str | None = None
    agenda_item: str | None = None
    group_header: str = ""


@dataclass
class DaySchedule:
    """All sessions for one day across all rooms."""

    day_name: str
    rooms: list[RoomInfo]
    sessions: list[Session] = field(default_factory=list)


@dataclass
class Schedule:
    """Complete parsed schedule."""

    meeting_name: str
    days: list[DaySchedule]
    source_file: str
    generated_at: str
    contact_name: str = "Duckhyun Bae"
    contact_email: str = "duckhyun.bae@lge.com"


def time_to_minutes(t: str) -> int:
    """Convert 'HH:MM' to minutes since midnight."""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def minutes_to_time(m: int) -> str:
    """Convert minutes since midnight to 'HH:MM'."""
    return f"{m // 60:02d}:{m % 60:02d}"


def time_to_grid_row(t: str) -> int:
    """Convert 'HH:MM' to CSS grid row number (1-indexed, row 1 = header)."""
    mins = time_to_minutes(t)
    base = time_to_minutes("08:30")
    slot = (mins - base) // 5
    return slot + 2  # row 1 is header


GROUP_COLORS = [
    {"bg": "#DBEAFE", "border": "#2563EB", "text": "#1E40AF"},  # Blue
    {"bg": "#FCE7F3", "border": "#DB2777", "text": "#9D174D"},  # Pink
    {"bg": "#D1FAE5", "border": "#059669", "text": "#065F46"},  # Green
    {"bg": "#FED7AA", "border": "#EA580C", "text": "#9A3412"},  # Orange
    {"bg": "#E9D5FF", "border": "#7C3AED", "text": "#5B21B6"},  # Purple
    {"bg": "#CCFBF1", "border": "#0D9488", "text": "#115E59"},  # Teal
    {"bg": "#FEF08A", "border": "#CA8A04", "text": "#854D0E"},  # Yellow
    {"bg": "#C7D2FE", "border": "#4F46E5", "text": "#3730A3"},  # Indigo
    {"bg": "#FECACA", "border": "#DC2626", "text": "#991B1B"},  # Red
    {"bg": "#BBF7D0", "border": "#16A34A", "text": "#166534"},  # Emerald
    {"bg": "#BFDBFE", "border": "#3B82F6", "text": "#1D4ED8"},  # Sky
    {"bg": "#FDE68A", "border": "#D97706", "text": "#92400E"},  # Amber
    {"bg": "#DDD6FE", "border": "#7C3AED", "text": "#5B21B6"},  # Violet
    {"bg": "#A7F3D0", "border": "#10B981", "text": "#047857"},  # Emerald light
    {"bg": "#FECDD3", "border": "#E11D48", "text": "#9F1239"},  # Rose
    {"bg": "#E0E7FF", "border": "#6366F1", "text": "#4338CA"},  # Indigo light
]


