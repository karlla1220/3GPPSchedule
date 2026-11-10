"""RAN1 time blocks are read from the Main schedule, not assumed.

The chair moves the breaks between meetings (RAN1#127 v00: 08:00-10:30,
lunch 12:30-14:00). Fixed RAN1#126bis blocks dropped every 08:00 and 14:00
row of that schedule.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from docx import Document

import working_groups.ran1.slot_state as slot_state
from working_groups.ran1.merger import TimeSlotData, _annotate_freshness
from working_groups.ran1.models import ran1_timeline
from working_groups.ran1.parser import (
    _determine_time_block_index,
    document_time_blocks,
    parse_docx,
)
from working_groups.ran1.slot_state import SlotState, save_slot_state

FIXTURES = Path(__file__).parent / "fixtures" / "ran1"
RAN1_127 = FIXTURES / "Draft RAN1#127 online and offline schedules - v00.docx"
RAN1_126BIS = FIXTURES / "RAN1#126b online and offline schedules - v05.docx"
MEETING = "ran1#127"

BLOCKS_127 = [
    {"index": 0, "start": "08:00", "end": "10:30", "duration": 150},
    {"index": 1, "start": "11:00", "end": "12:30", "duration": 90},
    {"index": 2, "start": "14:00", "end": "16:30", "duration": 150},
    {"index": 3, "start": "17:00", "end": "19:00", "duration": 120},
]


def _spans(blocks):
    return [(b["index"], b["start"], b["end"]) for b in blocks]


class DocumentBlocksTests(unittest.TestCase):
    def test_ran1_127_blocks_and_breaks_come_from_the_document(self):
        cells, meta = parse_docx(RAN1_127)
        blocks, breaks = document_time_blocks(meta)

        self.assertEqual(blocks, BLOCKS_127)
        self.assertEqual(
            [(b["name"], b["start"], b["end"]) for b in breaks],
            [("Morning coffee break", "10:30", "11:00"),
             ("Lunch break", "12:30", "14:00"),
             ("Afternoon coffee break", "16:30", "17:00")],
        )
        # Every block of every day has cells, the 08:00 and 14:00 rows included.
        # (Friday's 14:00 cell runs to the end of the day, over the coffee break.)
        slots = {(c.day, c.time_block_index) for c in cells}
        for day in ("Monday", "Tuesday", "Wednesday", "Thursday"):
            self.assertEqual({i for d, i in slots if d == day}, {0, 1, 2, 3}, day)
        self.assertEqual({i for d, i in slots if d == "Friday"}, {0, 1, 2})
        monday_morning = [c for c in cells if (c.day, c.time_block_index) == ("Monday", 0)]
        self.assertTrue(monday_morning[0].text.startswith("RAN1#127 commences at 09:00"))
        self.assertEqual((monday_morning[0].time_block_start, monday_morning[0].time_block_end),
                         ("08:00", "10:30"))

    def test_empty_room_text_boxes_give_ran1_room_roles(self):
        # v00 names no rooms: "RAN1 Meeting Rooms (Main, Brk#1, Brk#2)" with
        # empty text boxes, and "(Offline#1, Offline#2)".
        _, meta = parse_docx(RAN1_127)
        self.assertEqual(meta[0]["day_rooms"]["Monday"], ["Main", "Brk1", "Brk2"])
        self.assertEqual(meta[1]["day_rooms"]["Monday"], ["Offline1", "Offline2"])
        self.assertTrue(all(m["default_room_names"] for m in meta))
        _, meta = parse_docx(RAN1_126BIS)
        self.assertFalse(any(m["default_room_names"] for m in meta))

    def test_ran1_126bis_keeps_its_blocks(self):
        _, meta = parse_docx(RAN1_126BIS)
        blocks, _ = document_time_blocks(meta)

        self.assertEqual(_spans(blocks), [
            (0, "08:30", "10:30"), (1, "11:00", "13:00"),
            (2, "14:30", "16:30"), (3, "17:00", "19:30"),
        ])

    def test_vice_chair_label_goes_to_the_main_block_it_overlaps(self):
        # A vice-chair table still on RAN1#126bis times meets the RAN1#127 blocks.
        self.assertEqual(_determine_time_block_index("08:30\n~\n10:30", BLOCKS_127), 0)
        self.assertEqual(_determine_time_block_index("11:00 ~ 13:00", BLOCKS_127), 1)
        self.assertEqual(_determine_time_block_index("14:30 ~ 16:30", BLOCKS_127), 2)
        self.assertEqual(_determine_time_block_index("17:00 ~ 19:30", BLOCKS_127), 3)
        # A sub-row labelled with its start stays in its block.
        self.assertEqual(_determine_time_block_index("09:00", BLOCKS_127), 0)

    def test_a_table_with_fewer_blocks_keeps_the_block_indices(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "two-blocks.docx"
            document = Document()
            table = document.add_table(rows=5, cols=2)
            table.cell(0, 1).text = "Monday"
            table.cell(1, 0).text = "11:00 ~ 12:30 (90 min)"
            table.cell(1, 1).text = "late morning"
            table.cell(2, 0).merge(table.cell(2, 1)).text = "Lunch break: 12:30 ~ 14:00"
            table.cell(3, 0).text = "17:00 ~ 19:00 (120 min)"
            table.cell(3, 1).text = "evening"
            table.cell(4, 0).merge(table.cell(4, 1)).text = "All sessions end at 19:00 - no exceptions"
            document.save(path)

            cells, meta = parse_docx(path)

        self.assertEqual(_spans(document_time_blocks(meta)[0]),
                         [(1, "11:00", "12:30"), (3, "17:00", "19:00")])
        self.assertEqual({c.text: c.time_block_index for c in cells},
                         {"late morning": 1, "evening": 3})

    def test_ran1_127_friday_is_one_cell_from_14_00_to_19_00(self):
        # "Any other open issues" is merged from 14:00 down to 19:00 over a
        # coffee row that covers Monday-Thursday only. One irregular cell;
        # its "expected to close at 16:00" is for the slot merge to read.
        cells, _ = parse_docx(RAN1_127)
        friday = sorted((c.table_index, c.time_block_index, c.cell_end, c.text.split("\n")[0])
                        for c in cells if c.day == "Friday" and c.time_block_index >= 2)
        self.assertEqual(friday, [(0, 2, "19:00", "Any other open issues. Session reports"),
                                  (1, 2, "19:00", "To be assigned by Sorour")])
        self.assertEqual([c for c in cells if c.cell_end and c.day != "Friday"], [])

    def test_timeline_follows_the_blocks(self):
        timeline = ran1_timeline(BLOCKS_127, [])
        self.assertEqual((timeline.start, timeline.end), ("08:00", "19:15"))
        default = ran1_timeline()
        self.assertEqual((default.start, default.end), ("08:30", "19:45"))


class MovedBlockSlotStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved_dir = slot_state.SLOT_STATE_DIR
        slot_state.SLOT_STATE_DIR = Path(self._tmp.name)

    def tearDown(self):
        slot_state.SLOT_STATE_DIR = self._saved_dir
        self._tmp.cleanup()

    def _slot(self, start, end):
        return TimeSlotData(day="Monday", time_block_index=1, time_block_start=start,
                            time_block_end=end, time_block_duration=90, main_rooms=[])

    def _save(self, **block):
        save_slot_state(SlotState(day="Monday", time_block_index=1, source_hashes={},
                                  merged_sessions=[{"name": "x"}], meeting_id=MEETING, **block))

    def test_a_merge_for_other_block_times_is_not_a_baseline(self):
        self._save(block_start="11:00", block_end="12:30")
        same = self._slot("11:00", "12:30")
        _annotate_freshness(same, meeting_id=MEETING)
        self.assertIsNotNone(same.previous_merge)

        moved = self._slot("11:00", "13:00")
        _annotate_freshness(moved, meeting_id=MEETING)
        self.assertIsNone(moved.previous_merge)

    def test_a_state_saved_before_blocks_were_read_is_for_the_126bis_block(self):
        self._save()  # no block recorded: 11:00-13:00
        moved = self._slot("11:00", "12:30")
        _annotate_freshness(moved, meeting_id=MEETING)
        self.assertIsNone(moved.previous_merge)

        old = self._slot("11:00", "13:00")
        _annotate_freshness(old, meeting_id=MEETING)
        self.assertIsNotNone(old.previous_merge)


if __name__ == "__main__":
    unittest.main()


def _friday_table(path: Path, close_text: str = "", lunch_row: bool = False) -> None:
    """Two Friday rooms; room B's cell is merged from the 14:00 block to 19:00
    over a coffee row that covers room A only (RAN1#127 v00 layout)."""
    document = Document()
    table = document.add_table(rows=7, cols=3)
    table.cell(0, 1).merge(table.cell(0, 2)).text = "Friday"
    r = 1
    table.cell(r, 0).text = "11:00 ~ 12:30 (90 min)"
    table.cell(r, 1).text = "late morning A"
    table.cell(r, 2).text = "late morning B"
    r += 1
    if lunch_row:  # a Friday-only lunch row with no time label (RAN1#119)
        table.cell(r, 0).text = ""
        table.cell(r, 1).text = "Lunch"
        r += 1
    else:
        table.cell(r, 0).merge(table.cell(r, 2)).text = "Lunch break: 12:30 ~ 14:00"
        r += 1
    start = r - 1 if lunch_row else r
    table.cell(r, 0).text = "14:00 ~ 16:30 (150 min)"
    table.cell(r, 1).text = "afternoon A"
    table.cell(r + 1, 0).merge(table.cell(r + 1, 1)).text = "Afternoon coffee break: 16:30 ~ 17:00"
    table.cell(r + 2, 0).text = "17:00 ~ 19:00 (120 min)"
    table.cell(r + 2, 1).text = "evening A"
    table.cell(start, 2).merge(table.cell(r + 2, 2)).text = "Open issues" + close_text
    table.cell(r + 3, 0).merge(table.cell(r + 3, 2)).text = "All sessions end at 19:00 - no exceptions"
    document.save(path)


class MergedOverBlocksTests(unittest.TestCase):
    """A cell merged down over a break is one irregular cell in the block it
    starts in, ending where the merge ends."""

    def _cells_of(self, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "friday.docx"
            _friday_table(path, **kwargs)
            cells, _ = parse_docx(path)
        return [(c.time_block_index, c.cell_end) for c in cells if c.text.startswith("Open issues")]

    def test_a_cell_merged_over_a_break_ends_where_the_merge_ends(self):
        self.assertEqual(self._cells_of(), [(2, "19:00")])

    def test_the_parser_does_not_read_the_close_from_the_text(self):
        # The slot merge reads "expected to close at" by meaning.
        self.assertEqual(self._cells_of(close_text="\nRAN1#127expected to close at 16:00"),
                         [(2, "19:00")])

    def test_a_merge_starting_in_a_row_without_a_time_label_is_kept(self):
        # Before, the origin row was skipped and the cell was lost entirely.
        self.assertEqual(self._cells_of(lunch_row=True), [(2, "19:00")])

    def test_a_cell_in_its_own_block_has_no_cell_end(self):
        cells, _ = parse_docx(RAN1_126BIS)
        tuesday_dinner = [c for c in cells if c.text == "Early dinner (60)"]
        self.assertTrue(tuesday_dinner)
        self.assertEqual({c.cell_end for c in tuesday_dinner}, {None})


def test_ran1_127_friday_items_without_lengths_become_parts():
    # The recorded slot merge answer for Friday 11:00 (merge prompt v8).
    from working_groups.ran1.merger import collect_time_slot_data
    from working_groups.ran1.parser import build_room_list
    from working_groups.ran1.session_parser import _slot_result_to_sessions, build_room_aliases

    cells, meta = parse_docx(RAN1_127)
    rooms = build_room_list(meta)
    blocks, _ = document_time_blocks(meta)
    slot = next(s for s in collect_time_slot_data(cells, rooms, {}, meeting_id=MEETING, blocks=blocks)
                if (s.day, s.time_block_index) == ("Friday", 1))
    answer = json.loads((FIXTURES / "llm_127_friday_1100.json").read_text())
    _, alias_to_name = build_room_aliases(rooms["Friday"])
    sessions = _slot_result_to_sessions({"sessions": answer["sessions"]}, slot, rooms, alias_to_name)

    brk2 = [(s.name, s.start_time, s.end_time, s.part, s.agenda_item, s.group_header)
            for s in sessions if s.room_col_start == 4]
    assert brk2 == [
        ("10.8 Sensing", "11:00", "12:30", [1, 2], "10.8", "6GR"),
        ("A-IoT Phase2", "11:00", "12:30", [2, 2], None, "R20"),
    ]
