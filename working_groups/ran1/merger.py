"""Collect and merge schedule data from multiple sources for unified parsing.

Instead of replacing cells before LLM parsing, this module collects raw
cell data from all schedule DOCX files (main + vice-chair) and groups
them by (day, time_block).  The combined data is then sent to the LLM
in a single call per time slot so it can cross-reference all sources and
produce the most detailed session list possible.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import main_history
from .models import CellData, RoomInfo
from .parser import parse_docx, build_room_list
from .rooms import online_rooms as _online_rooms
from .slot_state import hash_source_text, load_slot_state


MERGE_PROMPT_VERSION = 8


# ── Room name resolution ────────────────────────────────────


def _resolve_vc_room_names(
    vc_meta: list[dict],
    main_rooms_map: dict[str, list[RoomInfo]],
) -> None:
    """Resolve generic room names in vice-chair tables using LLM.

    When a vice-chair schedule table has RAN1 default room names ("Main",
    "Brk1", "Offline1": the document names none),
    this function uses the paragraph context (title/heading above the table)
    and LLM to determine which of the main schedule's rooms the table
    actually represents.

    Mutates vc_meta in place, updating day_rooms entries.
    """
    from .session_parser import detect_room_from_context

    room_hints = _build_room_hints(main_rooms_map)
    available_rooms = room_hints["all_rooms"]

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
        has_generic = meta.get("default_room_names", False)

        if not has_generic:
            continue  # Room names already resolved

        if not context_text:
            print(f"    Warning: Table {meta['table_index']} has generic rooms "
                  f"({sample_rooms}) but no context text for LLM resolution")
            continue

        num_rooms = len(sample_rooms)
        detected = detect_room_from_context(
            context_text,
            available_rooms,
            num_rooms,
            room_hints=room_hints,
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


def _build_room_hints(main_rooms_map: dict[str, list[RoomInfo]]) -> dict:
    """Build stable room-role hints from parsed main-schedule rooms.

    Returns:
        {
            "all_rooms": [...],
            "online_rooms": [...],
            "offline_rooms": [...],
            "main_room": "..." | None,
            "breakout_rooms": [...],
        }
    """
    # Room name -> best-known position tuple for stable ordering
    # (table_index, room_index_in_table)
    room_pos: dict[str, tuple[int, int]] = {}
    first_seen: dict[str, RoomInfo] = {}

    for rooms in main_rooms_map.values():
        for r in rooms:
            pos = (r.table_index, r.room_index_in_table)
            if r.name not in room_pos or pos < room_pos[r.name]:
                room_pos[r.name] = pos
                first_seen[r.name] = r

    if not room_pos:
        return {
            "all_rooms": [],
            "online_rooms": [],
            "offline_rooms": [],
            "main_room": None,
            "breakout_rooms": [],
        }

    ordered = sorted(room_pos.items(), key=lambda x: x[1])
    all_rooms = [name for name, _ in ordered]

    online_rooms = _online_rooms(first_seen[name] for name in all_rooms)
    offline_rooms = [name for name in all_rooms if name not in online_rooms]

    main_room = online_rooms[0] if online_rooms else (all_rooms[0] if all_rooms else None)
    breakout_rooms = online_rooms[1:] if len(online_rooms) > 1 else []

    return {
        "all_rooms": all_rooms,
        "online_rooms": online_rooms,
        "offline_rooms": offline_rooms,
        "main_room": main_room,
        "breakout_rooms": breakout_rooms,
    }


# ── Data structures ──────────────────────────────────────────


@dataclass
class SourceEntry:
    """One cell's data from a specific source."""

    room_label: str
    cell_text: str
    fallback_start_time: str | None = None
    # The id the LLM cites in ``source_cells``; derived from the source, room
    # and text so an unchanged cell keeps its id from run to run.
    cell_id: str = ""
    # Where the cell sits in its table's time block (see ``CellData``).
    table_index: int = 0
    block_row: int = 0
    row_span: int = 1
    row_starts: tuple[str | None, ...] = (None,)
    # Where an irregular cell ends, past its block (see ``CellData``).
    cell_end: str | None = None
    # Physical rooms the cell covers; empty when they are not target rooms.
    room_names: tuple[str, ...] = ()
    # Against the slot's cell ledger (set by _annotate_freshness): UNCHANGED,
    # CHANGED, NEW, or REMOVED for a cell that is gone. None when the slot
    # has no usable ledger, so nothing is known about what changed.
    change: str | None = None
    previous_text: str | None = None  # the cell's text before, for CHANGED


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
    # Incremental-merge metadata (populated by collect_time_slot_data).
    previous_merge: list[dict] | None = None
    source_freshness: dict[str, str] = field(default_factory=dict)
    current_hashes: dict[str, str] = field(default_factory=dict)
    all_stale: bool = False
    # The previous merge's cell ledger (None if it has none) and the cells
    # that were in it but are gone now, by source label.
    previous_entries: dict[str, list[dict]] | None = None
    removed_entries: dict[str, list[SourceEntry]] = field(default_factory=dict)
    # Ids of the ledger's cells that are not here unchanged any more: the
    # baseline sessions that cite one of them must be made again.
    stale_cell_ids: set[str] = field(default_factory=set)


def _normalize_cell(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _serialize_source(source: SlotSource) -> str:
    """Stable serialization of a source's entries for hashing."""
    entries = sorted(source.entries, key=lambda e: e.room_label)
    lines = [f"merge_prompt_v{MERGE_PROMPT_VERSION}"]
    for e in entries:
        cell = _normalize_cell(e.cell_text)
        timing = f"\tfallback={e.fallback_start_time}" if e.fallback_start_time else ""
        if e.cell_end:
            timing += f"\tend={e.cell_end}"
        lines.append(f"{e.room_label}{timing}\t{cell}")
    return "\n".join(lines)


# ── Cell changes ─────────────────────────────────────────────


def _cell_key(room_label: str, cell_text: str, fallback_start_time: str | None,
              cell_end: str | None = None) -> tuple:
    # The same fields, normalized the same way, as the source hash.
    return (room_label, _normalize_cell(cell_text), fallback_start_time or None, cell_end or None)


def diff_source_entries(
    previous: list[dict], current: list[SourceEntry],
) -> list[SourceEntry]:
    """Tag ``current`` cells against one source's cells in the ledger.

    A cell is UNCHANGED when the ledger has a cell with the same room label
    and text (whitespace ignored), CHANGED when the ledger's cell for that
    room label had another text (kept in ``previous_text``), and NEW when the
    room label is new. A merged cell that is split or joined gets new room
    labels. Room labels can repeat (a vice-chair document may have two tables
    naming the same room), so cells are matched one to one.

    Returns the ledger's cells that are gone, tagged REMOVED.
    """
    pool = list(previous)

    def take(match) -> dict | None:
        for i, cell in enumerate(pool):
            if match(cell):
                return pool.pop(i)
        return None

    unmatched = []
    for entry in current:
        key = _cell_key(entry.room_label, entry.cell_text, entry.fallback_start_time, entry.cell_end)
        same = take(lambda c: _cell_key(
            c.get("room_label", ""), c.get("cell_text", ""), c.get("fallback_start_time"),
            c.get("cell_end")) == key)
        if same is None:
            unmatched.append(entry)
        else:
            entry.change, entry.previous_text = "UNCHANGED", None
    for entry in unmatched:
        before = take(lambda c: c.get("room_label", "") == entry.room_label)
        if before is None:
            entry.change, entry.previous_text = "NEW", None
        else:
            entry.change, entry.previous_text = "CHANGED", before.get("cell_text", "")
    return [
        SourceEntry(
            room_label=cell.get("room_label", ""),
            cell_text=cell.get("cell_text", ""),
            fallback_start_time=cell.get("fallback_start_time"),
            cell_id=cell.get("cell_id", ""),
            change="REMOVED",
        )
        for cell in pool
    ]


def has_cell_changes(slot: TimeSlotData) -> bool:
    """True when the ledger shows a cell that changed, is new, or is gone.

    False when there is no ledger: nothing is known about what changed.
    """
    if slot.previous_entries is None:
        return False
    if any(slot.removed_entries.values()):
        return True
    return any(
        entry.change != "UNCHANGED" for source in slot.sources for entry in source.entries
    )


def ledger_entries(slot: TimeSlotData) -> dict[str, list[dict]]:
    """The slot's cells as the ledger records them after a merge."""
    return {
        source.label: [
            {
                "room_label": entry.room_label,
                "cell_text": entry.cell_text,
                "fallback_start_time": entry.fallback_start_time,
                "cell_id": entry.cell_id,
                **({"cell_end": entry.cell_end} if entry.cell_end else {}),
            }
            for entry in source.entries
        ]
        for source in slot.sources
    }


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


def _cell_rooms(cell: CellData, rooms_for_day: list[RoomInfo]) -> tuple[str, ...]:
    """Physical names of the target rooms a main-schedule cell covers."""
    return tuple(
        r.name for r in rooms_for_day
        if r.table_index == cell.table_index
        and r.room_index_in_table in cell.room_indices
    )


def _source_entry(
    cell: CellData, room_label: str, room_names: tuple[str, ...] = (),
) -> SourceEntry:
    return SourceEntry(
        room_label=room_label,
        cell_text=cell.text,
        fallback_start_time=cell.fallback_start_time,
        table_index=cell.table_index,
        block_row=cell.block_row,
        row_span=cell.row_span,
        row_starts=tuple(cell.row_starts),
        cell_end=cell.cell_end,
        room_names=room_names,
    )


def _assign_cell_ids(source: SlotSource) -> None:
    """Give each entry a short id that stays the same while its cell does.

    The id hashes the source, room and text, so the baseline of an
    incremental merge (and a reused slot state) still names the same cells.
    The same text twice in one room gets a running number.
    """
    seen: dict[str, int] = {}
    for entry in source.entries:
        text = re.sub(r"\s+", " ", entry.cell_text).strip()
        basis = f"{source.label}\0{entry.room_label}\0{text}"
        n = seen.get(basis, 0)
        seen[basis] = n + 1
        digest = hashlib.sha256(f"{basis}\0{n}".encode()).hexdigest()
        entry.cell_id = "c" + digest[:6]


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
    meeting_id: str | None = None,
    main_texts_seen: dict[str, set[str]] | None = None,
    blocks: list[dict] | None = None,
) -> list[TimeSlotData]:
    """Collect all schedule data grouped by (day, time_block).

    Parses the main schedule cells and ALL tables from each vice-chair
    schedule DOCX.  Returns a list of TimeSlotData objects, one per
    (day, time_block) combination found in the main schedule.

    Args:
        main_cells: CellData list from the main (Chair_notes) schedule.
        main_rooms_map: day → list[RoomInfo] from main schedule.
        vice_chair_paths: person_name → Path to their schedule DOCX.
        main_texts_seen: every Main text seen this meeting, by slot
            (``main_history``). A vice-chair cell that repeats one is an old
            copy of a Main table and is left out. Without it, only copies of
            the current Main are left out.
        blocks: The main schedule's time blocks (``document_time_blocks``).
            Vice-chair cells are placed in them.

    Returns:
        List of TimeSlotData, sorted by day order then time block index.
    """
    from .models import DAY_ORDER

    # 1. Group main cells by time slot
    main_groups = _group_cells_by_slot(main_cells)

    # 2. Parse vice-chair schedules (ALL tables) and group
    vc_data: dict[str, tuple[dict, dict]] = {}  # person → (cell_groups, rooms_map)
    for person, path in vice_chair_paths.items():
        print(f"  Parsing {person}'s schedule (all tables): {path.name}")
        try:
            vc_cells, vc_meta = parse_docx(path, max_tables=None, blocks=blocks)

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
        tb = {"start": m_cells[0].time_block_start, "end": m_cells[0].time_block_end,
              "duration": m_cells[0].time_block_duration}
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
                _source_entry(cell, room_label, _cell_rooms(cell, main_rooms))
            )
        _assign_cell_ids(main_source)
        slot.sources.append(main_source)

        # Add vice-chair sources
        main_room_names = {r.name for r in main_rooms}
        for person, (vc_groups, vc_rooms) in vc_data.items():
            key = (day, tb_idx)
            if key not in vc_groups:
                continue

            vc_cells = vc_groups[key]
            vc_day_rooms = vc_rooms.get(day, [])

            # A vice-chair cell that repeats a Main text of this slot, now or
            # in an earlier version, is a copy of a Main table: the current
            # Main already says it, or the chair has changed it since.
            main_texts = set(
                (main_texts_seen or {}).get(main_history.slot_key(day, tb_idx), ())
            ) | {main_history.normalize(cell.text) for cell in m_cells}

            vc_source = SlotSource(label=f"{person}'s schedule")
            for cell in vc_cells:
                if main_history.normalize(cell.text) in main_texts:
                    continue
                room_label = _room_label_for_cell(cell, vc_day_rooms)
                # Prefix VC room labels that match a main target room name
                # to prevent Gemini from blindly assigning sessions by label.
                # Content-based matching in the LLM handles correct placement.
                if room_label in main_room_names:
                    room_label = f"{person}: {room_label}"
                vc_source.entries.append(_source_entry(cell, room_label))

            if vc_source.entries:
                _assign_cell_ids(vc_source)
                slot.sources.append(vc_source)

        time_slots.append(slot)

    # Sort by day order, then time block
    time_slots.sort(
        key=lambda s: (
            DAY_ORDER.index(s.day) if s.day in DAY_ORDER else 99,
            s.time_block_index,
        )
    )

    # Classify each source's freshness relative to the previous run.
    for slot in time_slots:
        _annotate_freshness(slot, meeting_id=meeting_id)

    return time_slots


def _annotate_freshness(slot: TimeSlotData, meeting_id: str | None = None) -> None:
    """Compute current source hashes, compare against the previous snapshot,
    and tag each source as FRESH / STALE / NEW. Sources present in the
    previous snapshot but missing now are tagged REMOVED.

    When the snapshot has a cell ledger made under the current merge prompt
    version, every cell is also tagged against it (``diff_source_entries``);
    cells that are gone go to ``slot.removed_entries``. A ledger from another
    prompt version is not used: the baseline was made under other rules, so
    the slot is made again cold. When the ledger shows no changed cell, the
    cells are the same as the baseline's (only their order moved), so the
    baseline is reused as when every source is STALE.

    When ``meeting_id`` is given and differs from the snapshot's meeting, the
    snapshot is discarded and the slot is treated as brand-new (cold).
    """
    current_hashes = {s.label: hash_source_text(_serialize_source(s)) for s in slot.sources}
    slot.current_hashes = current_hashes
    slot.previous_entries = None
    slot.removed_entries = {}
    slot.stale_cell_ids = set()
    for source in slot.sources:
        for entry in source.entries:
            entry.change = entry.previous_text = None

    prev_state = load_slot_state(slot.day, slot.time_block_index, meeting_id)
    if prev_state is not None and prev_state.block() != (slot.time_block_start, slot.time_block_end):
        # The block moved (a break changed): its old session times are wrong.
        prev_state = None
    if prev_state is None:
        slot.previous_merge = None
        slot.source_freshness = {label: "NEW" for label in current_hashes}
        slot.all_stale = False
        return

    if (
        prev_state.source_entries is not None
        and prev_state.merge_prompt_version == MERGE_PROMPT_VERSION
    ):
        slot.previous_entries = prev_state.source_entries
        for source in slot.sources:
            removed = diff_source_entries(
                slot.previous_entries.get(source.label, []), source.entries
            )
            if removed:
                slot.removed_entries[source.label] = removed
        current_labels = {s.label for s in slot.sources}
        for label, cells in slot.previous_entries.items():
            if label not in current_labels and cells:
                slot.removed_entries[label] = diff_source_entries(cells, [])
        unchanged = {
            entry.cell_id
            for source in slot.sources
            for entry in source.entries
            if entry.change == "UNCHANGED"
        }
        slot.stale_cell_ids = {
            cell.get("cell_id", "")
            for cells in slot.previous_entries.values()
            for cell in cells
        } - unchanged - {""}

    prev_hashes = prev_state.source_hashes or {}
    freshness: dict[str, str] = {}
    for label, h in current_hashes.items():
        if label not in prev_hashes:
            freshness[label] = "NEW"
        elif prev_hashes[label] == h:
            freshness[label] = "STALE"
        else:
            freshness[label] = "FRESH"
    for label in prev_hashes:
        if label not in current_hashes:
            freshness[label] = "REMOVED"

    slot.source_freshness = freshness
    slot.previous_merge = prev_state.merged_sessions
    slot.all_stale = bool(freshness) and all(v == "STALE" for v in freshness.values())
    if slot.previous_entries is not None and not has_cell_changes(slot):
        slot.all_stale = True
