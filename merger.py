"""Collect and merge schedule data from multiple sources for unified parsing.

Instead of replacing cells before LLM parsing, this module collects raw
cell data from all schedule DOCX files (main + vice-chair) and groups
them by (day, time_block).  The combined data is then sent to the LLM
in a single call per time slot so it can cross-reference all sources and
produce the most detailed session list possible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from models import CellData, RoomInfo, TIME_BLOCKS
from parser import parse_docx, build_room_list


# ── Room name resolution ────────────────────────────────────


def _resolve_vc_room_names(
    vc_meta: list[dict],
    main_rooms_map: dict[str, list[RoomInfo]],
) -> None:
    """Resolve generic room names in vice-chair tables using LLM.

    When a vice-chair schedule table has fallback room names like "Room A",
    this function uses the paragraph context (title/heading above the table)
    and LLM to determine which of the main schedule's rooms the table
    actually represents.

    Mutates vc_meta in place, updating day_rooms entries.
    """
    from session_parser import detect_room_from_context

    # Collect all unique main room names
    all_main_rooms: set[str] = set()
    for rooms in main_rooms_map.values():
        for r in rooms:
            all_main_rooms.add(r.name)
    available_rooms = sorted(all_main_rooms)

    if not available_rooms:
        return

    for meta in vc_meta:
        context_text = meta.get("context_text", "")
        day_rooms = meta["day_rooms"]

        if not day_rooms:
            continue

        # Check if any room has a generic/fallback name
        sample_day = next(iter(day_rooms))
        sample_rooms = day_rooms[sample_day]
        has_generic = any(
            r.startswith("Room ") or r.startswith("Offline ")
            for r in sample_rooms
        )

        if not has_generic:
            continue  # Room names already resolved

        if not context_text:
            print(f"    Warning: Table {meta['table_index']} has generic rooms "
                  f"({sample_rooms}) but no context text for LLM resolution")
            continue

        num_rooms = len(sample_rooms)
        detected = detect_room_from_context(
            context_text, available_rooms, num_rooms,
        )

        if detected and len(detected) == num_rooms:
            # Update room names for all days
            for day in day_rooms:
                day_rooms[day] = list(detected)
            print(f"    Resolved table {meta['table_index']} rooms: "
                  f"{sample_rooms} → {detected}")
        elif detected:
            # Partial match — pad with originals
            padded = list(detected) + sample_rooms[len(detected):]
            for day in day_rooms:
                day_rooms[day] = padded[:num_rooms]
            print(f"    Partially resolved table {meta['table_index']} rooms: "
                  f"{sample_rooms} → {padded[:num_rooms]}")


# ── Data structures ──────────────────────────────────────────


@dataclass
class SourceEntry:
    """One cell's data from a specific source."""

    room_label: str
    cell_text: str


@dataclass
class SlotSource:
    """All cells from one source for a given time slot."""

    label: str  # e.g. "Main Schedule (online)", "Sorour (detail)"
    entries: list[SourceEntry] = field(default_factory=list)


@dataclass
class TimeSlotData:
    """Aggregated data for one (day, time_block) across all sources."""

    day: str
    time_block_index: int
    time_block_start: str
    time_block_end: str
    time_block_duration: int
    main_rooms: list[RoomInfo]        # rooms from main schedule
    sources: list[SlotSource] = field(default_factory=list)


# ── Collection logic ─────────────────────────────────────────


def _room_label_for_cell(
    cell: CellData, rooms_for_day: list[RoomInfo],
) -> str:
    """Build a human-readable room label for a cell."""
    if not rooms_for_day:
        indices = ", ".join(str(i) for i in cell.room_indices)
        return f"Room [{indices}]"

    matching = [
        r for r in rooms_for_day
        if r.table_index == cell.table_index
        and r.room_index_in_table in cell.room_indices
    ]
    if matching:
        return " + ".join(r.name for r in matching)

    # Fallback
    indices = ", ".join(str(i) for i in cell.room_indices)
    return f"Table {cell.table_index} Room [{indices}]"


def _group_cells_by_slot(
    cells: list[CellData],
) -> dict[tuple[str, int], list[CellData]]:
    """Group cells by (day, time_block_index)."""
    groups: dict[tuple[str, int], list[CellData]] = {}
    for cell in cells:
        key = (cell.day, cell.time_block_index)
        groups.setdefault(key, []).append(cell)
    return groups


def collect_time_slot_data(
    main_cells: list[CellData],
    main_rooms_map: dict[str, list[RoomInfo]],
    vice_chair_paths: dict[str, Path],
) -> list[TimeSlotData]:
    """Collect all schedule data grouped by (day, time_block).

    Parses the main schedule cells and ALL tables from each vice-chair
    schedule DOCX.  Returns a list of TimeSlotData objects, one per
    (day, time_block) combination found in the main schedule.

    Args:
        main_cells: CellData list from the main (Chair_notes) schedule.
        main_rooms_map: day → list[RoomInfo] from main schedule.
        vice_chair_paths: person_name → Path to their schedule DOCX.

    Returns:
        List of TimeSlotData, sorted by day order then time block index.
    """
    from models import DAY_ORDER

    # 1. Group main cells by time slot
    main_groups = _group_cells_by_slot(main_cells)

    # 2. Parse vice-chair schedules (ALL tables) and group
    vc_data: dict[str, tuple[dict, dict]] = {}  # person → (cell_groups, rooms_map)
    for person, path in vice_chair_paths.items():
        print(f"  Parsing {person}'s schedule (all tables): {path.name}")
        try:
            vc_cells, vc_meta = parse_docx(path, max_tables=None)

            # Resolve generic room names using LLM + document context
            _resolve_vc_room_names(vc_meta, main_rooms_map)

            vc_rooms = build_room_list(vc_meta)
            vc_groups = _group_cells_by_slot(vc_cells)
            vc_data[person] = (vc_groups, vc_rooms)
            total = sum(len(v) for v in vc_groups.values())
            print(f"    → {total} cells across {len(vc_groups)} time slots")
        except Exception as e:
            print(f"    → Failed: {e}")

    # 3. Build TimeSlotData for each (day, time_block)
    time_slots: list[TimeSlotData] = []

    for (day, tb_idx), m_cells in main_groups.items():
        tb = TIME_BLOCKS[tb_idx]
        main_rooms = main_rooms_map.get(day, [])

        slot = TimeSlotData(
            day=day,
            time_block_index=tb_idx,
            time_block_start=tb["start"],
            time_block_end=tb["end"],
            time_block_duration=tb["duration"],
            main_rooms=main_rooms,
        )

        # Add main schedule source
        main_source = SlotSource(label="Main Schedule")
        for cell in m_cells:
            room_label = _room_label_for_cell(cell, main_rooms)
            main_source.entries.append(
                SourceEntry(room_label=room_label, cell_text=cell.text)
            )
        slot.sources.append(main_source)

        # Add vice-chair sources
        main_room_names = {r.name for r in main_rooms}
        for person, (vc_groups, vc_rooms) in vc_data.items():
            key = (day, tb_idx)
            if key not in vc_groups:
                continue

            vc_cells = vc_groups[key]
            vc_day_rooms = vc_rooms.get(day, [])

            # De-duplicate: skip VC entries whose text is identical to a main entry
            main_texts = {cell.text.strip() for cell in m_cells}

            vc_source = SlotSource(label=f"{person}'s schedule")
            for cell in vc_cells:
                if cell.text.strip() in main_texts:
                    continue  # Skip duplicate content
                room_label = _room_label_for_cell(cell, vc_day_rooms)
                # Prefix VC room labels that match a main target room name
                # to prevent Gemini from blindly assigning sessions by label.
                # Content-based matching in the LLM handles correct placement.
                if room_label in main_room_names:
                    room_label = f"{person}: {room_label}"
                vc_source.entries.append(
                    SourceEntry(room_label=room_label, cell_text=cell.text)
                )

            if vc_source.entries:
                slot.sources.append(vc_source)

        time_slots.append(slot)

    # Sort by day order, then time block
    time_slots.sort(
        key=lambda s: (
            DAY_ORDER.index(s.day) if s.day in DAY_ORDER else 99,
            s.time_block_index,
        )
    )

    return time_slots

