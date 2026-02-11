"""Parse DOCX tables to extract raw schedule cell data."""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document

from models import TIME_BLOCKS, CellData, DAY_ORDER, RoomInfo


# Namespace for OpenXML
_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _get_grid_span(cell) -> int:
    """Get the horizontal grid span of a cell."""
    tc = cell._tc
    grid_span = tc.find(f".//{{{_NS}}}gridSpan")
    if grid_span is not None:
        return int(grid_span.get(f"{{{_NS}}}val"))
    return 1


def _dedupe_row_cells(row) -> list[tuple]:
    """Get deduplicated cells from a row with their grid positions.

    Returns list of (cell_text, col_start, col_end) tuples.
    col_start is 0-indexed, col_end is exclusive.

    python-docx's row.cells returns one entry per grid column position,
    repeating the same cell object for merged columns. We use enumerate
    to track the actual column index.

    Cells that are vertical-merge continuations (vMerge without
    val="restart") are emitted with empty text so that column positions
    remain correct while the content is not double-counted.
    """
    # Build a set of grid-column ranges that are vMerge continuations
    # by inspecting the raw XML <w:tc> elements in this row's <w:tr>.
    # python-docx's row.cells resolves merged cells to the original,
    # so we cannot compare tc element identity; we use column positions.
    tr = row._tr
    continuation_cols: set[int] = set()
    col_cursor = 0
    for tc_el in tr.findall(f"{{{_NS}}}tc"):
        tc_pr = tc_el.find(f"{{{_NS}}}tcPr")
        gs = 1
        is_continuation = False
        if tc_pr is not None:
            gs_el = tc_pr.find(f"{{{_NS}}}gridSpan")
            if gs_el is not None:
                gs = int(gs_el.get(f"{{{_NS}}}val"))
            vm_el = tc_pr.find(f"{{{_NS}}}vMerge")
            if vm_el is not None:
                val = vm_el.get(f"{{{_NS}}}val")
                if val != "restart":
                    is_continuation = True
        if is_continuation:
            for i in range(col_cursor, col_cursor + gs):
                continuation_cols.add(i)
        col_cursor += gs

    result = []
    row_seen = set()

    for col_pos, cell in enumerate(row.cells):
        tc_id = id(cell._tc)
        if tc_id in row_seen:
            continue
        row_seen.add(tc_id)

        span = _get_grid_span(cell)

        # Emit empty text for vMerge continuation cells
        if col_pos in continuation_cols:
            result.append(("", col_pos, col_pos + span))
        else:
            text = cell.text.strip()
            result.append((text, col_pos, col_pos + span))

    return result


def _is_break_row(cells: list[tuple]) -> bool:
    """Check if this row is a break (coffee/lunch).

    Real break rows span the entire table (1-2 cells).  Data rows may
    mention 'break' inside schedule content but have many more cells.
    """
    if not cells or len(cells) > 3:
        return False
    full_text = " ".join(c[0] for c in cells).lower()
    return "break" in full_text or "coffee" in full_text or "lunch" in full_text


def _is_footer_row(cells: list[tuple]) -> bool:
    """Check if this row is the end-of-day footer."""
    if not cells or len(cells) > 3:
        return False
    full_text = " ".join(c[0] for c in cells).lower()
    return "all sessions end" in full_text or "no exceptions" in full_text


def _is_metadata_row(cells: list[tuple]) -> bool:
    """Check if this row contains room metadata."""
    if not cells or len(cells) > 3:
        return False
    full_text = " ".join(c[0] for c in cells).lower()
    return "meeting rooms" in full_text


def _parse_day_header(cells: list[tuple]) -> dict[str, tuple[int, int]]:
    """Parse the header row to map day names to column ranges.

    Returns dict: day_name -> (col_start, col_end) inclusive/exclusive.
    """
    day_map = {}
    for text, col_start, col_end in cells:
        # Normalize day name
        for day in DAY_ORDER:
            if day.lower() in text.lower():
                if day not in day_map:
                    day_map[day] = (col_start, col_end)
                break
    return day_map


def _extract_room_names_from_doc(doc: Document) -> list[str] | None:
    """Try to extract room names from the document metadata rows."""
    for table in doc.tables:
        for row in table.rows:
            cells = _dedupe_row_cells(row)
            for text, _, _ in cells:
                if "meeting rooms" in text.lower():
                    # Parse room names from lines like:
                    # "RAN1 Meeting Rooms:\n Main session (F1/2/3)\n RAN1_Brk#1 (A1)\n ..."
                    lines = [
                        line.strip()
                        for line in text.split("\n")
                        if line.strip() and "meeting rooms" not in line.lower()
                    ]
                    if lines:
                        return lines
    return None


def _is_schedule_table(table) -> bool:
    """Determine if a table is a schedule table (has day headers)."""
    if len(table.rows) < 5:
        return False
    first_row = _dedupe_row_cells(table.rows[0])
    text = " ".join(c[0] for c in first_row).lower()
    return any(day.lower() in text for day in DAY_ORDER)


def _count_actual_rooms_per_day(
    table, day_columns: dict[str, tuple[int, int]]
) -> dict[str, int]:
    """Determine the actual number of rooms per day by examining data rows.

    The DOCX grid may allocate more columns for a day than there are real
    rooms (extra columns are used for flexible cell merging).  The true
    room count equals the maximum number of distinct cells observed in any
    single data row for that day.
    """
    max_cells: dict[str, int] = {day: 1 for day in day_columns}

    for row in table.rows[1:]:
        cells = _dedupe_row_cells(row)
        if (
            _is_break_row(cells)
            or _is_footer_row(cells)
            or _is_metadata_row(cells)
        ):
            continue
        if not cells:
            continue
        if _determine_time_block_index(cells[0][0]) is None:
            continue

        for day, (day_col_start, day_col_end) in day_columns.items():
            count = sum(
                1
                for _, cs, ce in cells[1:]
                if cs >= day_col_start and ce <= day_col_end
            )
            if count > max_cells[day]:
                max_cells[day] = count

    return max_cells


def _determine_time_block_index(time_text: str) -> int | None:
    """Match a time label cell to a time block index."""
    # Extract start time from text like "08:30\n~\n10:30\n\n(120 min)"
    match = re.search(r"(\d{1,2}:\d{2})", time_text)
    if not match:
        return None

    start_time = match.group(1)
    # Zero-pad hour
    if len(start_time) == 4:
        start_time = "0" + start_time

    for block in TIME_BLOCKS:
        if block["start"] == start_time:
            return block["index"]
    return None


def parse_docx(filepath: str | Path) -> tuple[list[CellData], list[dict]]:
    """Parse a schedule DOCX file and extract all cell data.

    Returns:
        (cells, tables_meta) where:
        - cells: list of CellData for each non-empty schedule cell
        - tables_meta: list of dicts with table metadata (rooms per day)
    """
    doc = Document(str(filepath))

    # Try to get room names from document
    room_names = _extract_room_names_from_doc(doc)

    # Find all schedule tables
    schedule_tables = []
    for idx, table in enumerate(doc.tables):
        if _is_schedule_table(table):
            schedule_tables.append((idx, table))

    if not schedule_tables:
        raise ValueError("No schedule tables found in the document")

    # If 3+ tables found, take the 2 largest (by column count)
    # to skip summary/duplicate tables
    if len(schedule_tables) > 2:
        schedule_tables.sort(key=lambda x: len(x[1].columns), reverse=True)
        schedule_tables = schedule_tables[:2]
        # Re-sort by original index
        schedule_tables.sort(key=lambda x: x[0])

    all_cells = []
    tables_meta = []

    for table_idx, (orig_idx, table) in enumerate(schedule_tables):
        rows = table.rows
        if len(rows) < 2:
            continue

        # Parse header row for day -> column mapping
        header_cells = _dedupe_row_cells(rows[0])
        day_columns = _parse_day_header(header_cells)

        if not day_columns:
            continue

        # Determine actual rooms per day (header span may exceed real rooms)
        actual_rooms = _count_actual_rooms_per_day(table, day_columns)

        day_rooms: dict[str, list[str]] = {}
        for day, (col_start, col_end) in day_columns.items():
            num_rooms = actual_rooms[day]
            if room_names and table_idx == 0 and num_rooms <= len(room_names):
                day_rooms[day] = room_names[:num_rooms]
            else:
                prefix = "Offline" if table_idx > 0 else "Room"
                day_rooms[day] = [
                    f"{prefix} {chr(65 + i)}" for i in range(num_rooms)
                ]

        tables_meta.append(
            {
                "table_index": table_idx,
                "day_columns": day_columns,
                "day_rooms": day_rooms,
            }
        )

        # Parse data rows
        for row_idx in range(1, len(rows)):
            row_cells = _dedupe_row_cells(rows[row_idx])

            if _is_break_row(row_cells) or _is_footer_row(row_cells) or _is_metadata_row(row_cells):
                continue

            # First cell should be the time label
            if not row_cells:
                continue

            time_cell_text = row_cells[0][0]
            tb_index = _determine_time_block_index(time_cell_text)

            if tb_index is None:
                continue

            time_block = TIME_BLOCKS[tb_index]

            # Process cells grouped by day with ordinal room mapping
            for day, (day_col_start, day_col_end) in day_columns.items():
                grid_cols = day_col_end - day_col_start
                num_actual = actual_rooms[day]

                # Collect cells for this day, sorted by column position
                day_data_cells = sorted(
                    [
                        (t, cs, ce)
                        for t, cs, ce in row_cells[1:]
                        if cs >= day_col_start and ce <= day_col_end
                    ],
                    key=lambda x: x[1],
                )

                if not day_data_cells:
                    continue

                running_room = 0
                for text, col_start, col_end in day_data_cells:
                    cell_span = col_end - col_start

                    # Determine room indices for this cell
                    if cell_span >= grid_cols:
                        # Cell spans all grid columns → all rooms
                        room_indices = list(range(num_actual))
                        running_room = num_actual
                    elif len(day_data_cells) == num_actual:
                        # Exactly one cell per room → sequential
                        room_indices = [running_room]
                        running_room += 1
                    else:
                        # Proportional assignment based on grid span
                        rooms_count = max(
                            1, round(cell_span * num_actual / grid_cols)
                        )
                        rooms_count = min(
                            rooms_count, max(1, num_actual - running_room)
                        )
                        room_indices = list(
                            range(running_room, running_room + rooms_count)
                        )
                        running_room += rooms_count

                    if not text.strip():
                        continue

                    cell_data = CellData(
                        text=text,
                        day=day,
                        room_indices=room_indices,
                        time_block_index=tb_index,
                        time_block_start=time_block["start"],
                        time_block_end=time_block["end"],
                        time_block_duration=time_block["duration"],
                        table_index=table_idx,
                    )
                    all_cells.append(cell_data)

    return all_cells, tables_meta


def build_room_list(tables_meta: list[dict]) -> dict[str, list[RoomInfo]]:
    """Build a unified room list per day from all tables.

    Returns dict: day_name -> list of RoomInfo (in order for grid columns).
    """
    day_rooms: dict[str, list[RoomInfo]] = {}

    for meta in tables_meta:
        for day, rooms in meta["day_rooms"].items():
            if day not in day_rooms:
                day_rooms[day] = []
            for ri, room_name in enumerate(rooms):
                day_rooms[day].append(
                    RoomInfo(
                        name=room_name,
                        table_index=meta["table_index"],
                        room_index_in_table=ri,
                    )
                )

    return day_rooms


def compute_room_global_col(
    cell: CellData,
    day_rooms: list[RoomInfo],
) -> tuple[int, int]:
    """Compute global grid column range for a cell's rooms.

    Returns (col_start, col_end) as 1-indexed grid columns
    (col 1 = time label, col 2 = first room).
    """
    matching_indices = []
    for global_idx, room_info in enumerate(day_rooms):
        if room_info.table_index == cell.table_index and room_info.room_index_in_table in cell.room_indices:
            matching_indices.append(global_idx)

    if not matching_indices:
        return (2, 3)  # fallback: first room

    col_start = min(matching_indices) + 2  # +2: col 1 = time label
    col_end = max(matching_indices) + 3  # exclusive
    return (col_start, col_end)
