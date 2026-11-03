"""Old Main Schedule copies inside vice-chair documents are left out of the merge.

A vice-chair cell whose text is a Main text seen for the same slot in any
version (``main_history``) is a copy that does not follow the chair's edits.
The fixture is RAN1#126bis: every Main version on FTP (Draft v00-v03,
v00-v05) and the last Hiroki and Sorour documents. Terms are in GLOSSARY.md.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import working_groups.ran1.main_history as main_history
import working_groups.ran1.merger as merger
import working_groups.ran1.slot_state as slot_state
from working_groups.ran1.models import TIME_BLOCKS, CellData, RoomInfo

FIXTURE = Path(__file__).parent / "fixtures" / "ran1" / "main_history_126bis"
MEETING = "ran1#126bis"


def _cell(day: str, tb: int, text: str, table_index: int = 0, rooms=(0,)) -> CellData:
    block = TIME_BLOCKS[tb]
    return CellData(
        text=text, day=day, room_indices=list(rooms), time_block_index=tb,
        time_block_start=block["start"], time_block_end=block["end"],
        time_block_duration=block["duration"], table_index=table_index,
    )


def _versions() -> list[dict]:
    return json.loads((FIXTURE / "main_versions.json").read_text())


def _vice_chair(name: str) -> list[dict]:
    docs = json.loads((FIXTURE / "vice_chair_cells.json").read_text())
    return next(doc["cells"] for doc in docs if name in doc["file"])


def _history(versions: list[dict]) -> dict[str, set[str]]:
    """The history a run that saw ``versions`` in order would have."""
    history: dict[str, set[str]] = {}
    for version in versions:
        cells = [_cell(*key.rsplit("_", 1)[:1], int(key.rsplit("_", 1)[1]), text)
                 for key, texts in version["slots"].items() for text in texts]
        history = main_history.add(history, cells)
    return history


def _dropped(cells: list[dict], history: dict[str, set[str]]) -> list[bool]:
    return [
        main_history.normalize(c["text"])
        in history.get(main_history.slot_key(c["day"], c["time_block_index"]), set())
        for c in cells
    ]


class _HistoryDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._orig = main_history.HISTORY_DIR
        main_history.HISTORY_DIR = Path(self.tmp.name) / "main_history"

    def tearDown(self):
        main_history.HISTORY_DIR = self._orig


class RecordTests(_HistoryDir):
    def test_keeps_every_distinct_text_across_runs(self):
        main_history.record(MEETING, [_cell("Friday", 0, "6GR (120)\n.10.5.1.1(50)")])
        history = main_history.record(MEETING, [
            _cell("Friday", 0, "6GR (120)  .10.5.1.1(50)"),  # same text, other spacing
            _cell("Friday", 0, "R20 (120) A-IoT Phase2 (120)"),
        ])
        self.assertEqual(history, {"Friday_00": {"6GR (120) .10.5.1.1(50)", "R20 (120) A-IoT Phase2 (120)"}})
        self.assertEqual(main_history.load(MEETING), history)

    def test_another_meeting_starts_empty(self):
        main_history.record(MEETING, [_cell("Friday", 0, "TBD")])
        self.assertEqual(main_history.load("ran1#127"), {})

    def test_without_a_meeting_id_nothing_is_stored(self):
        history = main_history.record(None, [_cell("Friday", 0, "TBD")])
        self.assertEqual(history, {"Friday_00": {"TBD"}})
        self.assertFalse(main_history.HISTORY_DIR.exists())

    def test_force_deploy_keeps_the_history(self):
        # The history records source documents, not our merges: wiping the
        # slot states for a cold rebuild must not take it with them.
        from working_groups.ran1 import lifecycle

        cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, cwd)
        main_history.HISTORY_DIR = Path("docs/ran1/main_history")
        main_history.record(MEETING, [_cell("Friday", 0, "TBD")])
        slot_state.save_slot_state(slot_state.SlotState(day="Friday", time_block_index=0))

        lifecycle.reset_cache()

        self.assertEqual(list(Path("docs/ran1/slot_state").glob("*.json")), [])
        self.assertEqual(main_history.load(MEETING), {"Friday_00": {"TBD"}})


class RealDocumentTests(unittest.TestCase):
    """RAN1#126bis, after Main v05, with every Main version recorded."""

    @classmethod
    def setUpClass(cls):
        cls.versions = _versions()
        cls.history = _history(cls.versions)

    def _kept(self, name: str, table: int) -> list[str]:
        cells = [c for c in _vice_chair(name) if c["table_index"] == table]
        return [main_history.normalize(c["text"])
                for c, gone in zip(cells, _dropped(cells, self.history)) if not gone]

    def test_versions_are_drafts_then_releases(self):
        self.assertEqual(
            [v["file"][:5] for v in self.versions], ["Draft"] * 4 + ["RAN1#"] * 6)

    def test_hiroki_copies_of_main_tables_are_all_left_out(self):
        # Hiroki v08 tables 2 and 3 are pasted Main tables. Among them are the
        # Friday 08:30 Tamna BC Hall and Yeongju A Hall cells that put Main
        # v02 back in an incremental merge.
        self.assertEqual(self._kept("Hiroki", 2), [])
        self.assertEqual(self._kept("Hiroki", 3), [])
        friday = [main_history.normalize(c["text"]) for c in _vice_chair("Hiroki")
                  if c["table_index"] == 2 and c["day"] == "Friday" and c["time_block_index"] == 0]
        self.assertIn("6GR (120) .10.5.1.1(50) .10.5.1.2(40) .10.5.1.3(30)", friday)
        self.assertIn("6GR (120) .10.7.1 NTN (60) .10.5.2.x (60)", friday)

    def test_hiroki_own_tables_are_kept(self):
        for table in (0, 1):
            cells = [c for c in _vice_chair("Hiroki") if c["table_index"] == table]
            self.assertEqual(len(self._kept("Hiroki", table)), len(cells))

    def test_sorour_edits_inside_her_copies_are_kept(self):
        # Everything else in Sorour's copies matches a Main version, including
        # the 10.5.4 sub-times (Draft text; Hiroki's own table has the real
        # ones) behind the empty Tuesday 08:30 chair.
        self.assertEqual(self._kept("Sorour", 0), [
            "R20 (60) AI/ML (60) AI 10.3.1 Channel coding (30) 6GR (30) .10.5.2.2(30)",
            "AI 10.3.1 Channel coding (30) R20 (90) AI/ML (50) MIMO (40)",
        ])
        self.assertEqual(self._kept("Sorour", 1), [
            "Sorouri (150) 6GR .10.5.2.1 (40) .10.7.1 NTN (40) R20 9.5 NTN (30) 9.4 NTN-NR (40)",
            "Xiaodong (30) 6GR .10.5.5 (30) Sorouri (60) 6GR .10.7.1 NTN (30) R20 9.4 NTN-NR (30)",
            "Sorour (150) 6GR .10.5.2.2 (60) .10.7.1 NTN (30) R20 .9.5 NTN (30) .9.4 NTN-NR (30)",
        ])
        dropped = {main_history.normalize(c["text"]) for c in _vice_chair("Sorour")
                   if c["table_index"] == 0} - set(self._kept("Sorour", 0))
        self.assertIn(
            "6GR (120) .10.5.4 (120) .10.5.4.1 (50) .10.5.4.2 (25) .10.5.4.3 (40) .10.5.4.4 (35)", dropped)

    def test_sorour_own_table_loses_only_tbd(self):
        cells = [c for c in _vice_chair("Sorour") if c["table_index"] == 2]
        gone = [main_history.normalize(c["text"]) for c, d in zip(cells, _dropped(cells, self.history)) if d]
        self.assertEqual(gone, ["TBD"])

    def test_missing_versions_only_keep_copies_in(self):
        # A run that never saw the earlier versions leaves their copies in
        # (as before this history existed); it never drops more.
        current_only = _history(self.versions[-1:])
        for name in ("Hiroki", "Sorour"):
            cells = _vice_chair(name)
            with_all = _dropped(cells, self.history)
            with_current = _dropped(cells, current_only)
            self.assertTrue(all(a or not b for a, b in zip(with_all, with_current)))
            self.assertGreater(sum(with_all), sum(with_current))


class CollectTests(_HistoryDir):
    """collect_time_slot_data leaves old copies out of the vice-chair source."""

    def _collect(self, history):
        rooms = {"Friday": [RoomInfo(name="Tamna BC Hall", table_index=0, room_index_in_table=0),
                            RoomInfo(name="Yeongju B Hall", table_index=0, room_index_in_table=1)]}
        main = [_cell("Friday", 0, "6GR (120) .10.5.1 (120)", rooms=(0,)),
                _cell("Friday", 0, "R20 (120) A-IoT Phase2 (120)", rooms=(1,))]
        vice = [_cell("Friday", 0, "6GR (120) .10.5.1.1(50) .10.5.1.2(40) .10.5.1.3(30)", 2, (0,)),
                _cell("Friday", 0, "R20 (120) A-IoT Phase2 (120)", 2, (1,)),
                _cell("Friday", 0, "9.3.1(40), 9.3.2(40), 9.3.3(40)", 0, (0,))]
        self.addCleanup(setattr, slot_state, "SLOT_STATE_DIR", slot_state.SLOT_STATE_DIR)
        slot_state.SLOT_STATE_DIR = Path(self.tmp.name) / "slot_state"
        with patch.object(merger, "parse_docx", return_value=(vice, [])), \
             patch.object(merger, "_resolve_vc_room_names"), \
             patch.object(merger, "build_room_list", return_value={}):
            slots = merger.collect_time_slot_data(
                main, rooms, {"Hiroki": Path("hiroki.docx")}, meeting_id=MEETING,
                main_texts_seen=history,
            )
        hiroki = next(s for s in slots[0].sources if s.label == "Hiroki's schedule")
        return [e.cell_text for e in hiroki.entries]

    def test_an_old_main_text_is_left_out(self):
        history = main_history.add({}, [_cell("Friday", 0, "6GR (120) .10.5.1.1(50) .10.5.1.2(40) .10.5.1.3(30)")])
        self.assertEqual(self._collect(history), ["9.3.1(40), 9.3.2(40), 9.3.3(40)"])

    def test_without_history_only_current_copies_are_left_out(self):
        self.assertEqual(self._collect(None), [
            "6GR (120) .10.5.1.1(50) .10.5.1.2(40) .10.5.1.3(30)",
            "9.3.1(40), 9.3.2(40), 9.3.3(40)",
        ])


if __name__ == "__main__":
    unittest.main()
