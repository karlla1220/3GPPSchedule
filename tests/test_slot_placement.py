"""Session times come from where each cell sits in the document.

RAN1#126bis Tuesday 17:00-19:30 (v05): every room has one cell in the
block's first row and "Early dinner (60)" fills the second row, once in the
online table and once in the offline table.  A re-merge listed the dinner
between the online and offline rooms, and the old room cursors pushed the
offline sessions from 17:00-18:30 to 19:30-21:00.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from shared import notices
from shared.lifecycle import RejectedState
from working_groups.ran1.merger import SlotSource, SourceEntry, TimeSlotData, _assign_cell_ids
from working_groups.ran1.models import RoomInfo
from working_groups.ran1.session_parser import (
    SlotPlacementError,
    _build_time_slot_prompt,
    _reject_misplaced,
    _slot_result_to_sessions,
    build_room_aliases,
    source_cell_problems,
)

ROOMS = [
    RoomInfo(name="Tamna BC Hall", table_index=0, room_index_in_table=0, kind="online"),
    RoomInfo(name="Yeongju A Hall", table_index=0, room_index_in_table=1, kind="online"),
    RoomInfo(name="201", table_index=1, room_index_in_table=0, kind="offline"),
    RoomInfo(name="203", table_index=1, room_index_in_table=1, kind="offline"),
]
TWO_ROWS = ("17:00", None)


def _cell(cell_id, rooms, text, table, row=0, span=1, starts=TWO_ROWS, fallback=None):
    return SourceEntry(
        room_label=" + ".join(rooms), cell_text=text, cell_id=cell_id,
        table_index=table, block_row=row, row_span=span, row_starts=starts,
        room_names=tuple(rooms), fallback_start_time=fallback,
    )


def _slot(entries, start="17:00", end="19:30", extra_sources=()):
    slot = TimeSlotData(
        day="Tuesday", time_block_index=3, time_block_start=start,
        time_block_end=end, time_block_duration=150, main_rooms=ROOMS,
    )
    slot.sources = [SlotSource(label="Main Schedule", entries=list(entries)), *extra_sources]
    return slot


def _tuesday_cells(dinner_text="Early dinner (60)", fallback="18:30"):
    return [
        _cell("cmain0", ["Tamna BC Hall"], "6GR (90) / .10.5.3.2(30) / .10.5.3.3(30) / .10.5.3.4(30)", 0),
        _cell("cbrk10", ["Yeongju A Hall"], "6GR (90) / .10.3.1 (60) / .10.3.2 (30)", 0),
        _cell("cdin00", ["Tamna BC Hall", "Yeongju A Hall"], dinner_text, 0, row=1, fallback=fallback),
        _cell("coff10", ["201"], "Xiaodong (90) / 6GR / .10.5.1.3(30) / .10.5.1.1(60)", 1),
        _cell("coff20", ["203"], "Xiaodong (30) / .10.5.5 (30) / Sorouri (60) / .10.7.1 (30) / .9.4 (30)", 1),
        _cell("cdin01", ["201", "203"], dinner_text, 1, row=1, fallback=fallback),
    ]


def _s(room, name, minutes, cells, start=None):
    return {"room_name": room, "name": name, "duration_minutes": minutes,
            "specified_start_time": start, "source_cells": cells,
            "agenda_item": name.split()[0]}


ONLINE = [
    _s("RAN1_main", "10.5.3.2", 30, ["cmain0"]),
    _s("RAN1_main", "10.5.3.3", 30, ["cmain0"]),
    _s("RAN1_main", "10.5.3.4", 30, ["cmain0"]),
    _s("RAN1_brk1", "10.3.1", 60, ["cbrk10"]),
    _s("RAN1_brk1", "10.3.2", 30, ["cbrk10"]),
]
OFFLINE = [
    _s("RAN1_off1", "10.5.1.3", 30, ["coff10"]),
    _s("RAN1_off1", "10.5.1.1", 60, ["coff10"]),
    _s("RAN1_off2", "10.5.5", 30, ["coff20"]),
    _s("RAN1_off2", "10.7.1 NTN", 30, ["coff20"]),
    _s("RAN1_off2", "9.4 NTN-NR", 30, ["coff20"]),
]


def _dinner(minutes=60):
    return _s("ALL_ROOMS", "Early dinner", minutes, ["cdin00", "cdin01"])


def _times(sessions_list, slot):
    _, alias_to_name = build_room_aliases(ROOMS)
    sessions = _slot_result_to_sessions(
        {"sessions": sessions_list}, slot, {"Tuesday": ROOMS}, alias_to_name
    )
    return {(s.name, s.room_col_start): (s.start_time, s.end_time) for s in sessions}


TUESDAY = {
    ("10.5.3.2", 2): ("17:00", "17:30"), ("10.5.3.4", 2): ("18:00", "18:30"),
    ("10.3.2", 3): ("18:00", "18:30"),
    ("Early dinner", 2): ("18:30", "19:30"),
    ("10.5.1.3", 4): ("17:00", "17:30"), ("10.5.1.1", 4): ("17:30", "18:30"),
    ("10.5.5", 5): ("17:00", "17:30"), ("10.7.1 NTN", 5): ("17:30", "18:00"),
    ("9.4 NTN-NR", 5): ("18:00", "18:30"),
}


@pytest.mark.parametrize("order", ["dinner between", "dinner first", "dinner last"])
def test_answer_order_does_not_move_sessions(order):
    sessions = {
        "dinner between": ONLINE + [_dinner()] + OFFLINE,
        "dinner first": [_dinner()] + ONLINE + OFFLINE,
        "dinner last": ONLINE + OFFLINE + [_dinner()],
    }[order]
    times = _times(sessions, _slot(_tuesday_cells()))
    for key, expected in TUESDAY.items():
        assert times[key] == expected, key


@pytest.mark.parametrize("guessed", [60, 30])
def test_dinner_without_duration_starts_where_the_first_row_ends(guessed):
    # "Early dinner" with no "(60)": no parser-derived start.  The 90-minute
    # cells of the first row end at 18:30, so the second row starts there,
    # whatever duration the model gave the dinner.
    slot = _slot(_tuesday_cells(dinner_text="Early dinner", fallback=None))
    times = _times([_dinner(guessed)] + ONLINE + OFFLINE, slot)
    assert times[("Early dinner", 2)][0] == "18:30"
    assert times[("10.5.1.3", 4)] == ("17:00", "17:30")


def test_empty_first_row_leaves_the_room_empty_until_its_cell():
    # The offline room has no cell in the first row; its only cell is in the
    # second row, so its session starts there and 17:00-18:30 stays empty.
    cells = [c for c in _tuesday_cells() if c.cell_id in ("cmain0", "cbrk10")]
    cells.append(_cell("clate1", ["201"], "10.5.1.1 (60)", 1, row=1))
    sessions = ONLINE + [_s("RAN1_off1", "10.5.1.1", 60, ["clate1"])]
    times = _times(sessions, _slot(cells))
    assert times[("10.5.1.1", 4)] == ("18:30", "19:30")


def test_cell_spanning_both_rows_starts_at_the_block_start():
    cells = _tuesday_cells()
    cells.append(_cell("cwhole", ["203"], "6GR (150)", 1, span=2))
    cells = [c for c in cells if c.cell_id not in ("coff20", "cdin01")]
    sessions = ONLINE + [_s("ALL_ONLINE", "Early dinner", 60, ["cdin00"]),
                         _s("RAN1_off2", "10.5.4", 150, ["cwhole"])]
    times = _times(sessions, _slot(cells))
    assert times[("10.5.4", 5)] == ("17:00", "19:30")


def test_row_with_its_own_time_label_starts_there():
    starts = ("08:30", "09:00")
    cells = [
        _cell("copen0", ["Tamna BC Hall"], "Opening (10)", 0, starts=starts),
        _cell("clater", ["Tamna BC Hall"], "10.5.0 (90)", 0, row=1, starts=starts),
    ]
    sessions = [_s("RAN1_main", "10.5.0", 90, ["clater"]),
                _s("RAN1_main", "Opening", 10, ["copen0"])]
    times = _times(sessions, _slot(cells, start="08:30", end="10:30"))
    assert times[("Opening", 2)] == ("08:30", "08:40")
    assert times[("10.5.0", 2)] == ("09:00", "10:30")


def test_explicit_time_in_the_cell_wins():
    cells = [_cell("cmain0", ["Tamna BC Hall"], "Joint 18:00 ~ 18:30", 0, span=2)]
    sessions = [_s("RAN1_main", "Joint", 30, ["cmain0"], start="18:00")]
    times = _times(sessions, _slot(cells))
    assert times[("Joint", 2)] == ("18:00", "18:30")


def test_vice_chair_detail_follows_the_main_cell():
    vice = SlotSource(label="Sorour's schedule", entries=[
        _cell("cvice0", ["Room A"], "Sorouri (60) / .10.7.1 (30) / .9.4 (30)", 0, row=1),
    ])
    slot = _slot(_tuesday_cells(), extra_sources=[vice])
    sessions = ONLINE + [_dinner()] + [
        dict(s, source_cells=s["source_cells"] + (["cvice0"] if "9.4" in s["name"] or "10.7" in s["name"] else []))
        for s in OFFLINE
    ]
    times = _times(sessions, slot)
    # Placed by the Main Schedule cell (first row), not the vice-chair row.
    assert times[("9.4 NTN-NR", 5)] == ("18:00", "18:30")


def test_session_past_the_end_of_its_cell_is_refused():
    cells = _tuesday_cells()
    sessions = ONLINE + [_dinner()] + OFFLINE + [_s("RAN1_off1", "Extra", 30, ["coff10"])]
    with pytest.raises(SlotPlacementError) as caught:
        _times(sessions, _slot(cells))
    assert "Extra" in caught.value.problems[0]
    assert "past the end of its cell at 18:30" in caught.value.problems[0]


def test_session_without_cells_is_refused():
    sessions = ONLINE + [dict(_dinner(), source_cells=[])]
    with pytest.raises(SlotPlacementError, match="names no cell"):
        _times(sessions, _slot(_tuesday_cells()))


def test_source_cell_problems():
    slot = _slot(_tuesday_cells())
    _, alias_to_name = build_room_aliases(ROOMS)
    parsed = {"sessions": [
        _s("RAN1_main", "ok", 30, ["cmain0"]),
        _s("RAN1_main", "empty", 30, []),
        _s("RAN1_main", "unknown", 30, ["czzzzzz"]),
        _s("RAN1_off1", "wrong room", 30, ["cmain0"]),
        _s("ALL_ROOMS", "Early dinner", 60, ["cdin00", "cdin01"]),
    ]}
    problems = source_cell_problems(parsed, slot, ROOMS, alias_to_name)
    assert len(problems) == 3
    assert "'empty'" in problems[0] and "empty" in problems[0]
    assert "no cell czzzzzz" in problems[1]
    assert "do not cover 201" in problems[2]


def test_prompt_heads_each_cell_with_its_id():
    slot = _slot(_tuesday_cells())
    name_to_alias, _ = build_room_aliases(ROOMS)
    prompt = _build_time_slot_prompt(slot, name_to_alias)
    assert "[cell cmain0 — RAN1_main]" in prompt
    assert "[cell cdin01 — RAN1_off1 + RAN1_off2]" in prompt
    # The rows' times stay out of the prompt.
    assert "18:30" not in prompt


def test_incremental_prompt_lists_unchanged_cells_by_id():
    vice = SlotSource(label="Hiroki's schedule", entries=[
        _cell("cvice0", ["Room A"], "Hiroki (90) / 9.3.1 (30) / 9.3.3 (60)", 0)])
    slot = _slot(_tuesday_cells(), extra_sources=[vice])
    slot.previous_merge = ONLINE
    slot.source_freshness = {"Main Schedule": "STALE", "Hiroki's schedule": "FRESH"}
    name_to_alias, _ = build_room_aliases(ROOMS)
    prompt = _build_time_slot_prompt(slot, name_to_alias, mode="incremental")
    assert "- cell cmain0 — Main Schedule — RAN1_main" in prompt
    assert "6GR (90)" not in prompt  # stale text stays omitted
    assert "[cell cvice0 — Room A]" in prompt


def test_cell_ids_follow_the_cell_not_its_position():
    def source(*texts):
        s = SlotSource(label="Main Schedule", entries=[
            SourceEntry(room_label="201", cell_text=t) for t in texts])
        _assign_cell_ids(s)
        return [e.cell_id for e in s.entries]

    first = source("10.5.1.1 (60)", "Early dinner (60)")
    second = source("Early dinner (60)", "10.5.1.1 (60)")
    assert first == second[::-1]
    assert len(set(source("Sweep", "Sweep"))) == 2


def test_misplaced_slot_fails_the_build_and_leaves_a_notice(tmp_path, monkeypatch):
    monkeypatch.setattr(notices, "PATH", tmp_path / "notices.json")
    slot = SimpleNamespace(day="Tuesday", time_block_index=3,
                           time_block_start="17:00", time_block_end="19:30")
    with pytest.raises(RejectedState) as caught:
        _reject_misplaced([(slot, ["Tuesday 17:00-19:30: 'Extra' runs past its cell"])],
                          "ran1#126bis")
    assert [p.name for p in caught.value.discard] == ["Tuesday_03.json"]
    recorded = json.loads((tmp_path / "notices.json").read_text())
    assert recorded[0]["kind"] == "slot-placement"
    assert recorded[0]["title"] == "RAN1: session times do not fit the schedule document"
    assert any("'Extra'" in line for line in recorded[0]["lines"])
