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
