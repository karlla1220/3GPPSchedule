"""The schedule exchanged between independent WG pipelines and the renderer.

Times use venue-local HH:MM; dates use ISO YYYY-MM-DD. A day has its own
rooms and timeline. New pipelines identify rooms by id; grid columns remain
available for the existing RAN1 parser during migration.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
import json
from pathlib import Path


@dataclass
class Timeline:
    start: str = "09:00"
    end: str = "18:00"
    slot_minutes: int = 5
    label_minutes: int = 30
    breaks: list[dict] = field(default_factory=list)
    blocks: list[dict] = field(default_factory=list)

    def __post_init__(self):
        if self.slot_minutes <= 0 or self.label_minutes <= 0:
            raise ValueError("Timeline intervals must be positive")
        duration = time_to_minutes(self.end) - time_to_minutes(self.start)
        if duration <= 0 or duration % self.slot_minutes:
            raise ValueError("Timeline range must contain whole positive slots")
        if self.label_minutes % self.slot_minutes:
            raise ValueError("Labels must align with timeline slots")

    @property
    def rows(self) -> int:
        return (time_to_minutes(self.end) - time_to_minutes(self.start)) // self.slot_minutes

    def row(self, value: str) -> int:
        return (time_to_minutes(value) - time_to_minutes(self.start)) // self.slot_minutes + 2


@dataclass
class RoomInfo:
    """A room across all tables for a given day."""

    name: str
    table_index: int = 0
    room_index_in_table: int = 0  # legacy RAN1 table position
    id: str = ""


@dataclass
class Session:
    """A parsed session with calculated times."""

    name: str
    duration_minutes: int
    start_time: str  # "HH:MM"
    end_time: str  # "HH:MM"
    day: str
    room_col_start: int = 2  # 1-indexed grid column (within a day's rooms)
    room_col_end: int = 3  # exclusive
    chair: str | None = None
    agenda_item: str | None = None
    group_header: str = ""
    description: str | None = None
    agenda_descriptions: list[dict] = field(default_factory=list)
    room_ids: list[str] = field(default_factory=list)
    room_scope: str = "assigned"  # assigned, shared, or unassigned (display span only)
    notes: list[str] = field(default_factory=list)


@dataclass
class DaySchedule:
    """All sessions for one day across all rooms."""

    day_name: str
    rooms: list[RoomInfo]
    sessions: list[Session] = field(default_factory=list)
    date: str | None = None
    timeline: Timeline = field(default_factory=Timeline)


@dataclass
class Schedule:
    """Complete parsed schedule."""

    meeting_name: str
    days: list[DaySchedule]
    source_file: str
    generated_at: str
    # Legacy snapshot fields; site presentation is configured independently.
    contact_name: str = ""
    contact_email: str = ""
    source_files: list[str] = field(default_factory=list)
    timezone: str = "UTC"  # IANA timezone of the meeting venue
    wg_id: str = ""
    meeting_id: str = ""
    starts_on: str | None = None
    ends_on: str | None = None
    is_demo: bool = False
    topic_references: list[dict] = field(default_factory=list)


def time_to_minutes(t: str) -> int:
    """Convert 'HH:MM' to minutes since midnight."""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def minutes_to_time(m: int) -> str:
    """Convert minutes since midnight to 'HH:MM'."""
    return f"{m // 60:02d}:{m % 60:02d}"


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


def save_schedule(schedule: Schedule, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"schema_version": 1, **asdict(schedule)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def load_schedule(path: Path) -> Schedule:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.pop("schema_version", None) != 1:
        raise ValueError(f"Unsupported schedule schema: {path}")
    days = []
    for raw in data.pop("days"):
        raw["rooms"] = [RoomInfo(**room) for room in raw["rooms"]]
        raw["sessions"] = [Session(**session) for session in raw["sessions"]]
        raw["timeline"] = Timeline(**raw["timeline"])
        days.append(DaySchedule(**raw))
    return Schedule(days=days, **data)
