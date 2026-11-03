"""Cell-level changes between a slot's previous and current parse results.

The incremental merge rebuilds only the areas whose cells changed. What
changed is decided from parse results alone (the slot's cell ledger against
the current cells), never from an LLM result. Terms are in GLOSSARY.md.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import working_groups.ran1.slot_state as slot_state
from working_groups.ran1.merger import (
    MERGE_PROMPT_VERSION,
    SlotSource,
    SourceEntry,
    TimeSlotData,
    _annotate_freshness,
    _assign_cell_ids,
    diff_source_entries,
    has_cell_changes,
    ledger_entries,
)
from working_groups.ran1.models import RoomInfo
from working_groups.ran1.session_parser import _build_time_slot_prompt, build_room_aliases
from working_groups.ran1.slot_state import SlotState, load_slot_state, save_slot_state


FIXTURE = Path(__file__).parent / "fixtures" / "ran1" / "slot_cells_126bis" / "friday_0830.json"
MEETING = "ran1#126bis"


def _entry(room_label: str, text: str, cell_id: str = "") -> dict:
    return {"room_label": room_label, "cell_text": text, "fallback_start_time": None, "cell_id": cell_id}


def _cells(*pairs: tuple[str, str]) -> list[SourceEntry]:
    return [SourceEntry(room_label=r, cell_text=t) for r, t in pairs]


def _fixture_slot(combo: str) -> TimeSlotData:
    data = json.loads(FIXTURE.read_text())
    slot = TimeSlotData(
        day=data["day"],
        time_block_index=data["time_block_index"],
        time_block_start="08:30",
        time_block_end="10:30",
        time_block_duration=120,
        main_rooms=[RoomInfo(**room) for room in data["main_rooms"]],
    )
    for source in data["slots"][combo]:
        entries = SlotSource(label=source["label"], entries=[SourceEntry(**e) for e in source["entries"]])
        _assign_cell_ids(entries)
        slot.sources.append(entries)
    return slot


def _changes(slot: TimeSlotData) -> dict[tuple[str, str], str]:
    """(source label, room label) → change, for every cell that is not UNCHANGED."""
    found = {}
    for source in slot.sources:
        for entry in source.entries:
            if entry.change != "UNCHANGED":
                found[(source.label, entry.room_label)] = entry.change
    for label, entries in slot.removed_entries.items():
        for entry in entries:
            found[(label, entry.room_label)] = "REMOVED"
    return found


class DiffSourceEntriesTests(unittest.TestCase):
    def test_same_cells_are_unchanged(self):
        current = _cells(("201", "Sorour (90) / 6GR / .10.5.2.2 (45)"), ("203", "Hiroki (120)"))
        removed = diff_source_entries(
            [_entry("201", "Sorour (90) / 6GR / .10.5.2.2 (45)"), _entry("203", "Hiroki (120)")],
            current,
        )
        self.assertEqual([e.change for e in current], ["UNCHANGED", "UNCHANGED"])
        self.assertEqual(removed, [])

    def test_whitespace_only_edit_is_unchanged(self):
        # The source hash ignores whitespace too, so the two never disagree.
        current = _cells(("203", "Hiroki (120)\n6GR  \n.10.5.4.x (60)"))
        diff_source_entries([_entry("203", "Hiroki (120) 6GR .10.5.4.x (60)")], current)
        self.assertEqual(current[0].change, "UNCHANGED")

    def test_changed_text_keeps_the_previous_text(self):
        current = _cells(("203", "Hiroki (120)\n.10.5.4.x (60)\n.10.8.x Sensing (60)"))
        removed = diff_source_entries(
            [_entry("203", "Hiroki (120)\n.10.8.x Sensing (60)\n10.5.4.x (60)")], current
        )
        self.assertEqual(current[0].change, "CHANGED")
        self.assertEqual(current[0].previous_text, "Hiroki (120)\n.10.8.x Sensing (60)\n10.5.4.x (60)")
        self.assertEqual(removed, [])

    def test_new_and_removed_room_labels(self):
        current = _cells(("201", "Sorour (120)"))
        removed = diff_source_entries([_entry("203", "Hiroki (120)")], current)
        self.assertEqual(current[0].change, "NEW")
        self.assertIsNone(current[0].previous_text)
        self.assertEqual([(e.room_label, e.cell_text, e.change) for e in removed],
                         [("203", "Hiroki (120)", "REMOVED")])

    def test_merged_cell_split_is_removed_plus_new(self):
        # "201 + 203" was one merged cell; now each room has its own cell.
        current = _cells(("201", "To be assigned by Sorour"), ("203", "To be assigned by Hiroki"))
        removed = diff_source_entries(
            [_entry("201 + 203", "To be assigned by Sorour / To be assigned by Hiroki")], current
        )
        self.assertEqual([e.change for e in current], ["NEW", "NEW"])
        self.assertEqual([e.room_label for e in removed], ["201 + 203"])

    def test_duplicate_room_labels_match_by_text(self):
        # Sorour's document has two tables that both name "Yeongju A (1F)".
        current = _cells(
            ("Yeongju A (1F)", "6GR (120) / AI 10.7.1 NTN (60)"),
            ("Yeongju A (1F)", "6GR (120) / .10.7.1 NTN (40) / .10.5.2.1 (80)"),
        )
        removed = diff_source_entries(
            [
                _entry("Yeongju A (1F)", "6GR (120) / .10.7.1 NTN (60) / .10.5.2.x (60)"),
                _entry("Yeongju A (1F)", "6GR (120) / AI 10.7.1 NTN (60)"),
            ],
            current,
        )
        self.assertEqual([e.change for e in current], ["UNCHANGED", "CHANGED"])
        self.assertEqual(current[1].previous_text, "6GR (120) / .10.7.1 NTN (60) / .10.5.2.x (60)")
        self.assertEqual(removed, [])

    def test_removed_cells_keep_their_old_id(self):
        removed = diff_source_entries([_entry("203", "Hiroki (120)", cell_id="c0ld203")], [])
        self.assertEqual([(e.cell_id, e.change) for e in removed], [("c0ld203", "REMOVED")])


class _SlotStateDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._orig_dir = slot_state.SLOT_STATE_DIR
        slot_state.SLOT_STATE_DIR = Path(self.tmp.name) / "slot_state"

    def tearDown(self):
        slot_state.SLOT_STATE_DIR = self._orig_dir

    def _save_merged(self, slot: TimeSlotData, merged_at: str, sessions=None,
                     version: int = MERGE_PROMPT_VERSION) -> None:
        """Record ``slot`` as merged, the way parse_time_slots does after a merge."""
        save_slot_state(
            SlotState(
                day=slot.day,
                time_block_index=slot.time_block_index,
                source_hashes=dict(slot.current_hashes),
                merged_sessions=sessions or [{"room_name": "RAN1_main", "name": "x"}],
                merged_at=merged_at,
                meeting_id=MEETING,
                source_entries=ledger_entries(slot),
                merge_prompt_version=version,
            )
        )


class RealUpdateTests(_SlotStateDir):
    """The two RAN1#126bis updates that rewrote rooms whose cells had not changed."""

    def _update(self, before: str, after: str) -> TimeSlotData:
        first = _fixture_slot(before)
        _annotate_freshness(first, meeting_id=MEETING)
        self._save_merged(first, "2026-10-15T10:41:00+00:00")
        second = _fixture_slot(after)
        _annotate_freshness(second, meeting_id=MEETING)
        return second

    def test_main_v05_changed_only_room_203(self):
        slot = self._update("Main v04 + Hiroki v07 + Sorour v01", "Main v05 + Hiroki v07 + Sorour v01")

        self.assertEqual(slot.source_freshness["Main Schedule"], "FRESH")
        self.assertEqual(slot.source_freshness["Hiroki's schedule"], "STALE")
        # Yeongju B Hall's Main cell did not change, so nothing may rebuild it.
        self.assertEqual(_changes(slot), {("Main Schedule", "203"): "CHANGED"})
        main_203 = next(e for e in slot.sources[0].entries if e.room_label == "203")
        self.assertIn(".10.8.x Sensing (60)\n10.5.4.x (60)", main_203.previous_text)
        self.assertTrue(has_cell_changes(slot))

    def test_hiroki_v08_changed_only_yeongju_b(self):
        slot = self._update("Main v05 + Hiroki v07 + Sorour v01", "Main v05 + Hiroki v08 + Sorour v01")

        self.assertEqual(slot.source_freshness["Main Schedule"], "STALE")
        self.assertEqual(slot.source_freshness["Hiroki's schedule"], "FRESH")
        # Hiroki's copies of an old Main table (Tamna, Yeongju A) did not change.
        self.assertEqual(_changes(slot), {("Hiroki's schedule", "Hiroki: Yeongju B Hall"): "CHANGED"})
        hiroki = next(s for s in slot.sources if s.label == "Hiroki's schedule")
        copies = {e.room_label: e.change for e in hiroki.entries}
        self.assertEqual(copies["Hiroki: Tamna BC Hall"], "UNCHANGED")
        self.assertEqual(copies["Hiroki: Yeongju A Hall"], "UNCHANGED")

    def test_incremental_prompt_shows_every_cell_with_its_change(self):
        slot = self._update("Main v04 + Hiroki v07 + Sorour v01", "Main v05 + Hiroki v07 + Sorour v01")
        name_to_alias, _ = build_room_aliases(slot.main_rooms)
        prompt = _build_time_slot_prompt(slot, name_to_alias, mode="incremental")

        off2 = name_to_alias["203"]
        brk2 = name_to_alias["Yeongju B Hall"]
        self.assertIn(f" — {off2}] CHANGED", prompt)
        self.assertIn(".10.8.x Sensing (60)\n10.5.4.x (60)", prompt)  # the text before
        self.assertIn(f" — {brk2}] UNCHANGED\nR20 (120)\nA-IoT Phase2 (120)", prompt)
        # Hiroki did not change, but its cells are what 203 is rebuilt from.
        self.assertIn(f" — Hiroki's schedule — Hiroki: {off2}] UNCHANGED", prompt)
        self.assertIn("10.5.4.1 (30)", prompt)
        self.assertIn(f" — Hiroki's schedule — Hiroki: {brk2}] UNCHANGED", prompt)
        self.assertIn("9.3.1(40), 9.3.2(40), 9.3.3(40)", prompt)
        # The baseline is still the starting point for every other room.
        self.assertIn('"name": "x"', prompt)

    def test_removed_cells_are_listed_with_their_old_text(self):
        first = _fixture_slot("Main v05 + Hiroki v07 + Sorour v01")
        _annotate_freshness(first, meeting_id=MEETING)
        self._save_merged(first, "2026-10-15T11:01:00+00:00")
        second = _fixture_slot("Main v05 + Hiroki v07 + Sorour v01")
        hiroki = next(s for s in second.sources if s.label == "Hiroki's schedule")
        hiroki.entries = [e for e in hiroki.entries if e.room_label != "Hiroki: 203"]
        _annotate_freshness(second, meeting_id=MEETING)

        self.assertEqual(_changes(second), {("Hiroki's schedule", "Hiroki: 203"): "REMOVED"})
        name_to_alias, _ = build_room_aliases(second.main_rooms)
        prompt = _build_time_slot_prompt(second, name_to_alias, mode="incremental")
        self.assertIn("## Removed cells", prompt)
        self.assertIn(f"[Hiroki's schedule — Hiroki: {name_to_alias['203']}] REMOVED", prompt)


class StaleRoomTests(_SlotStateDir):
    """A changed cell also rebuilds the rooms its old text fed (review of #66)."""

    def test_vice_chair_cell_moving_to_another_room_rebuilds_the_old_room(self):
        # Hiroki's "Room A" cell said 9.3.x (Yeongju B Hall) and now says
        # 10.2 (203). The baseline's Yeongju B Hall sessions cite the old
        # cell, so that room is rebuilt although no Main cell there changed.
        first = _fixture_slot("Main v05 + Hiroki v08 + Sorour v01")
        _annotate_freshness(first, meeting_id=MEETING)
        hiroki = next(s for s in first.sources if s.label == "Hiroki's schedule")
        old = next(e for e in hiroki.entries if e.room_label == "Hiroki: Yeongju B Hall")
        main_brk2 = next(e for e in first.sources[0].entries if e.room_label == "Yeongju B Hall")
        name_to_alias, _ = build_room_aliases(first.main_rooms)
        brk2, off2 = name_to_alias["Yeongju B Hall"], name_to_alias["203"]
        self._save_merged(first, "2026-10-15T22:41:00+00:00", sessions=[
            {"room_name": brk2, "name": "9.3.3", "source_cells": [main_brk2.cell_id, old.cell_id]},
            {"room_name": off2, "name": "10.5.4.1", "source_cells": [first.sources[0].entries[-1].cell_id]},
        ])

        second = _fixture_slot("Main v05 + Hiroki v08 + Sorour v01")
        moved = next(e for s in second.sources if s.label == "Hiroki's schedule"
                     for e in s.entries if e.room_label == "Hiroki: Yeongju B Hall")
        moved.cell_text = "10.2 (60) / 10.2.1 (60)"
        _assign_cell_ids(next(s for s in second.sources if s.label == "Hiroki's schedule"))
        _annotate_freshness(second, meeting_id=MEETING)

        self.assertEqual(moved.change, "CHANGED")
        self.assertEqual(second.stale_cell_ids, {old.cell_id})
        prompt = _build_time_slot_prompt(second, name_to_alias, mode="incremental")
        self.assertIn(f"## Rooms whose baseline cells changed (rebuild these)\n- {brk2}\n", prompt)
        self.assertNotIn(f"- {off2}\n", prompt.split("## Rooms whose baseline cells changed")[1].split("##")[0])

    def test_no_room_list_when_only_new_cells(self):
        first = _fixture_slot("Main v04 + Hiroki v07 + Sorour v01")
        _annotate_freshness(first, meeting_id=MEETING)
        self._save_merged(first, "2026-10-15T10:41:00+00:00")  # sessions cite nothing
        second = _fixture_slot("Main v05 + Hiroki v07 + Sorour v01")
        _annotate_freshness(second, meeting_id=MEETING)
        name_to_alias, _ = build_room_aliases(second.main_rooms)
        prompt = _build_time_slot_prompt(second, name_to_alias, mode="incremental")
        self.assertNotIn("Rooms whose baseline cells changed", prompt)


class LedgerTests(_SlotStateDir):
    def test_ledger_round_trips_through_slot_state(self):
        slot = _fixture_slot("Main v05 + Hiroki v08 + Sorour v01")
        _annotate_freshness(slot, meeting_id=MEETING)
        self._save_merged(slot, "2026-10-15T22:41:00+00:00")

        state = load_slot_state("Friday", 0, MEETING)
        self.assertEqual(state.merge_prompt_version, MERGE_PROMPT_VERSION)
        self.assertEqual(set(state.source_entries), {"Main Schedule", "Hiroki's schedule", "Sorour's schedule"})
        main = state.source_entries["Main Schedule"]
        self.assertEqual([e["room_label"] for e in main],
                         ["Tamna BC Hall", "Yeongju A Hall", "Yeongju B Hall", "201", "203"])
        self.assertEqual([e["cell_id"] for e in main], [e.cell_id for e in slot.sources[0].entries])

    def test_ledger_from_another_prompt_version_is_not_used(self):
        # The baseline was made under other rules; copying it would keep them.
        first = _fixture_slot("Main v04 + Hiroki v07 + Sorour v01")
        _annotate_freshness(first, meeting_id=MEETING)
        self._save_merged(first, "2026-10-15T10:41:00+00:00", version=MERGE_PROMPT_VERSION - 1)
        second = _fixture_slot("Main v05 + Hiroki v07 + Sorour v01")
        _annotate_freshness(second, meeting_id=MEETING)
        self.assertIsNone(second.previous_entries)
        self.assertFalse(has_cell_changes(second))
        self.assertFalse(second.all_stale)

    def test_cells_in_another_order_reuse_the_baseline(self):
        # Two cells with the same room label swap places: the source hash
        # changes, the cells do not.
        first = _fixture_slot("Main v05 + Hiroki v08 + Sorour v01")
        sorour = next(s for s in first.sources if s.label == "Sorour's schedule")
        sorour.entries.append(SourceEntry(room_label=sorour.entries[0].room_label, cell_text="TBD"))
        _assign_cell_ids(sorour)
        _annotate_freshness(first, meeting_id=MEETING)
        self._save_merged(first, "2026-10-15T22:41:00+00:00")

        second = _fixture_slot("Main v05 + Hiroki v08 + Sorour v01")
        sorour = next(s for s in second.sources if s.label == "Sorour's schedule")
        sorour.entries.insert(0, SourceEntry(room_label=sorour.entries[0].room_label, cell_text="TBD"))
        _assign_cell_ids(sorour)
        _annotate_freshness(second, meeting_id=MEETING)

        self.assertEqual(second.source_freshness["Sorour's schedule"], "FRESH")
        self.assertFalse(has_cell_changes(second))
        self.assertTrue(second.all_stale)

    def test_state_saved_before_the_ledger_existed_still_loads(self):
        slot_state.SLOT_STATE_DIR.mkdir(parents=True)
        (slot_state.SLOT_STATE_DIR / "Friday_00.json").write_text(json.dumps({
            "day": "Friday", "time_block_index": 0, "source_hashes": {"Main Schedule": "abc"},
            "merged_sessions": [{"name": "keep"}], "merged_at": "2026-10-15T10:41:00+00:00",
            "meeting_id": MEETING, "schema_version": slot_state.SCHEMA_VERSION,
        }))
        state = load_slot_state("Friday", 0, MEETING)
        self.assertEqual(state.merged_sessions, [{"name": "keep"}])
        self.assertIsNone(state.source_entries)

        slot = _fixture_slot("Main v05 + Hiroki v08 + Sorour v01")
        _annotate_freshness(slot, meeting_id=MEETING)
        self.assertIsNone(slot.previous_entries)
        self.assertFalse(has_cell_changes(slot))  # unknown, not "nothing changed"
        self.assertEqual({e.change for s in slot.sources for e in s.entries}, {None})


if __name__ == "__main__":
    unittest.main()
