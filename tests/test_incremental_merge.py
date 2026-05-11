"""Tests for the incremental-merge slot-state pipeline."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import slot_state
from merger import SlotSource, SourceEntry, TimeSlotData, _annotate_freshness
from models import RoomInfo
from slot_state import (
    SlotState,
    clear_all_slot_states,
    hash_source_text,
    load_slot_state,
    save_slot_state,
)


def _build_slot(
    sources: dict[str, list[tuple[str, str]]],
    day: str = "Monday",
    tb_idx: int = 1,
) -> TimeSlotData:
    """Build a TimeSlotData from a {label: [(room_label, cell_text), ...]} map."""
    slot = TimeSlotData(
        day=day,
        time_block_index=tb_idx,
        time_block_start="11:00",
        time_block_end="13:00",
        time_block_duration=120,
        main_rooms=[
            RoomInfo(name="F1+F2+F3", table_index=0, room_index_in_table=0),
            RoomInfo(name="A1", table_index=0, room_index_in_table=1),
        ],
    )
    for label, entries in sources.items():
        slot.sources.append(
            SlotSource(
                label=label,
                entries=[SourceEntry(room_label=r, cell_text=t) for r, t in entries],
            )
        )
    return slot


class SlotStateFilesystemTests(unittest.TestCase):
    """save / load / clear round-trips on a temp directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._orig_dir = slot_state.SLOT_STATE_DIR
        slot_state.SLOT_STATE_DIR = Path(self.tmp.name) / "slot_state"

    def tearDown(self):
        slot_state.SLOT_STATE_DIR = self._orig_dir

    def test_round_trip(self):
        state = SlotState(
            day="Tuesday",
            time_block_index=3,
            source_hashes={"Main Schedule": "abc"},
            merged_sessions=[{"name": "x"}],
            merged_at="2026-05-11T00:00:00",
        )
        save_slot_state(state)
        loaded = load_slot_state("Tuesday", 3)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.source_hashes, {"Main Schedule": "abc"})
        self.assertEqual(loaded.merged_sessions, [{"name": "x"}])

    def test_missing_returns_none(self):
        self.assertIsNone(load_slot_state("Friday", 0))

    def test_clear_all_slot_states_preserves_directory(self):
        save_slot_state(SlotState(day="Monday", time_block_index=0))
        save_slot_state(SlotState(day="Monday", time_block_index=1))
        n = clear_all_slot_states()
        self.assertEqual(n, 2)
        self.assertTrue(slot_state.SLOT_STATE_DIR.exists())
        self.assertEqual(list(slot_state.SLOT_STATE_DIR.glob("*.json")), [])

    def test_hash_normalization(self):
        self.assertEqual(
            hash_source_text("foo  bar\n\nbaz"),
            hash_source_text("foo bar baz"),
        )


class FreshnessClassificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._orig_dir = slot_state.SLOT_STATE_DIR
        slot_state.SLOT_STATE_DIR = Path(self.tmp.name) / "slot_state"

    def tearDown(self):
        slot_state.SLOT_STATE_DIR = self._orig_dir

    def test_cold_start_marks_all_new(self):
        slot = _build_slot({"Main Schedule": [("F1+F2+F3", "AI/ML (120)")]})
        _annotate_freshness(slot)
        self.assertEqual(slot.source_freshness, {"Main Schedule": "NEW"})
        self.assertIsNone(slot.previous_merge)
        self.assertFalse(slot.all_stale)

    def test_all_stale_when_hashes_match(self):
        slot = _build_slot({"Main Schedule": [("F1+F2+F3", "AI/ML (120)")]})
        _annotate_freshness(slot)
        save_slot_state(
            SlotState(
                day=slot.day,
                time_block_index=slot.time_block_index,
                source_hashes=slot.current_hashes,
                merged_sessions=[{"name": "AI/ML", "room_name": "RAN1_main"}],
            )
        )
        slot2 = _build_slot({"Main Schedule": [("F1+F2+F3", "AI/ML (120)")]})
        _annotate_freshness(slot2)
        self.assertEqual(slot2.source_freshness, {"Main Schedule": "STALE"})
        self.assertTrue(slot2.all_stale)
        self.assertEqual(slot2.previous_merge, [{"name": "AI/ML", "room_name": "RAN1_main"}])

    def test_fresh_classification(self):
        slot1 = _build_slot({"Main": [("F1+F2+F3", "AI/ML (120)")]})
        _annotate_freshness(slot1)
        save_slot_state(
            SlotState(
                day=slot1.day,
                time_block_index=slot1.time_block_index,
                source_hashes=slot1.current_hashes,
                merged_sessions=[],
            )
        )
        slot2 = _build_slot({"Main": [("F1+F2+F3", "AI/ML (60) / AI 9.1.1 (60)")]})
        _annotate_freshness(slot2)
        self.assertEqual(slot2.source_freshness, {"Main": "FRESH"})
        self.assertFalse(slot2.all_stale)

    def test_removed_source(self):
        slot1 = _build_slot(
            {
                "Main": [("F1+F2+F3", "AI/ML (120)")],
                "Sorour": [("F1+F2+F3", "AI 9.1.1 (60) / AI 9.1.2 (60)")],
            }
        )
        _annotate_freshness(slot1)
        save_slot_state(
            SlotState(
                day=slot1.day,
                time_block_index=slot1.time_block_index,
                source_hashes=slot1.current_hashes,
                merged_sessions=[],
            )
        )
        slot2 = _build_slot({"Main": [("F1+F2+F3", "AI/ML (120)")]})
        _annotate_freshness(slot2)
        self.assertEqual(slot2.source_freshness["Sorour"], "REMOVED")
        self.assertEqual(slot2.source_freshness["Main"], "STALE")
        # Not all_stale because REMOVED is a change too.
        self.assertFalse(slot2.all_stale)

    def test_new_source_classification(self):
        slot1 = _build_slot({"Main": [("F1+F2+F3", "AI/ML (120)")]})
        _annotate_freshness(slot1)
        save_slot_state(
            SlotState(
                day=slot1.day,
                time_block_index=slot1.time_block_index,
                source_hashes=slot1.current_hashes,
                merged_sessions=[],
            )
        )
        slot2 = _build_slot(
            {
                "Main": [("F1+F2+F3", "AI/ML (120)")],
                "Hiroki": [("F1+F2+F3", "AI 9.2 (120)")],
            }
        )
        _annotate_freshness(slot2)
        self.assertEqual(slot2.source_freshness["Hiroki"], "NEW")
        self.assertEqual(slot2.source_freshness["Main"], "STALE")


class FakeGeminiResponse:
    def __init__(self, payload):
        self.text = json.dumps(payload)


class FakeGeminiClient:
    """Captures prompts and returns scripted JSON."""

    def __init__(self, scripted_response):
        self.scripted_response = scripted_response
        self.calls = []
        self.models = self

    def generate_content(self, *, model, contents, config):
        self.calls.append(
            {
                "model": model,
                "contents": contents,
                "system_instruction": config.system_instruction,
            }
        )
        return FakeGeminiResponse(self.scripted_response)


class ParseTimeSlotsIntegrationTests(unittest.TestCase):
    """End-to-end behaviour of parse_time_slots with a mocked Gemini client."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._orig_dir = slot_state.SLOT_STATE_DIR
        slot_state.SLOT_STATE_DIR = Path(self.tmp.name) / "slot_state"
        os.environ["GEMINI_API_KEY"] = "fake-key"

    def tearDown(self):
        slot_state.SLOT_STATE_DIR = self._orig_dir

    def _run(self, slots, fake_response):
        from session_parser import parse_time_slots

        fake_client = FakeGeminiClient(fake_response)
        day_rooms_map = {"Monday": slots[0].main_rooms}
        with patch("google.genai.Client", return_value=fake_client):
            with patch("time.sleep"):
                sessions = parse_time_slots(slots, day_rooms_map)
        return sessions, fake_client

    def test_all_stale_skips_llm(self):
        # First run: cold path populates state.
        from merger import _annotate_freshness

        slot = _build_slot({"Main": [("F1+F2+F3", "AI/ML (120)")]})
        _annotate_freshness(slot)
        fake_sessions = [
            {
                "room_name": "RAN1_main",
                "name": "AI/ML",
                "duration_minutes": 120,
                "specified_start_time": None,
                "chair": None,
                "group_header": "",
                "agenda_item": None,
            }
        ]
        _, client1 = self._run([slot], {"sessions": fake_sessions})
        self.assertEqual(len(client1.calls), 1)

        # Second run: same content → short-circuit, no LLM call.
        slot2 = _build_slot({"Main": [("F1+F2+F3", "AI/ML (120)")]})
        _annotate_freshness(slot2)
        self.assertTrue(slot2.all_stale)
        sessions, client2 = self._run([slot2], {"sessions": []})
        self.assertEqual(len(client2.calls), 0)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].name, "AI/ML")

    def test_incremental_path_excludes_stale_raw_text(self):
        from merger import _annotate_freshness
        from session_parser import MULTI_SOURCE_SYSTEM_INSTRUCTION_INCREMENTAL

        # Seed previous state for two sources.
        slot1 = _build_slot(
            {
                "Main": [("F1+F2+F3", "AI/ML (120)")],
                "Sorour": [("F1+F2+F3", "AI 9.1.1 (60) / AI 9.1.2 (60)")],
            }
        )
        _annotate_freshness(slot1)
        save_slot_state(
            SlotState(
                day=slot1.day,
                time_block_index=slot1.time_block_index,
                source_hashes=slot1.current_hashes,
                merged_sessions=[
                    {
                        "room_name": "RAN1_main",
                        "name": "AI 9.1.1",
                        "duration_minutes": 60,
                        "specified_start_time": None,
                        "chair": "Sorour",
                        "group_header": "R20",
                        "agenda_item": "9.1.1",
                    },
                    {
                        "room_name": "RAN1_main",
                        "name": "AI 9.1.2",
                        "duration_minutes": 60,
                        "specified_start_time": None,
                        "chair": "Sorour",
                        "group_header": "R20",
                        "agenda_item": "9.1.2",
                    },
                ],
            )
        )

        # Run 2: Main consolidates ("AI/ML 120"), Sorour unchanged → STALE.
        slot2 = _build_slot(
            {
                "Main": [("F1+F2+F3", "AI/ML CONSOLIDATED (120)")],
                "Sorour": [("F1+F2+F3", "AI 9.1.1 (60) / AI 9.1.2 (60)")],
            }
        )
        _annotate_freshness(slot2)
        self.assertEqual(slot2.source_freshness["Main"], "FRESH")
        self.assertEqual(slot2.source_freshness["Sorour"], "STALE")

        consolidated = {
            "sessions": [
                {
                    "room_name": "RAN1_main",
                    "name": "AI/ML",
                    "duration_minutes": 120,
                    "specified_start_time": None,
                    "chair": "Sorour",
                    "group_header": "R20",
                    "agenda_item": None,
                }
            ]
        }
        sessions, client = self._run([slot2], consolidated)

        self.assertEqual(len(client.calls), 1)
        call = client.calls[0]
        self.assertEqual(
            call["system_instruction"], MULTI_SOURCE_SYSTEM_INSTRUCTION_INCREMENTAL
        )
        # Stale source's raw text MUST NOT appear in the prompt.
        self.assertNotIn("AI 9.1.1 (60) / AI 9.1.2 (60)", call["contents"])
        # Fresh source's raw text MUST appear.
        self.assertIn("AI/ML CONSOLIDATED (120)", call["contents"])
        # Baseline JSON should be present.
        self.assertIn('"AI 9.1.1"', call["contents"])
        # Freshness summary must list both.
        self.assertIn("Main: FRESH", call["contents"])
        self.assertIn("Sorour: STALE", call["contents"])

        # Output reflects consolidation.
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].name, "AI/ML")

    def test_cold_path_when_no_previous_state(self):
        from merger import _annotate_freshness
        from session_parser import MULTI_SOURCE_SYSTEM_INSTRUCTION_COLD

        slot = _build_slot({"Main": [("F1+F2+F3", "AI/ML (120)")]})
        _annotate_freshness(slot)
        _, client = self._run([slot], {"sessions": []})
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(
            client.calls[0]["system_instruction"],
            MULTI_SOURCE_SYSTEM_INSTRUCTION_COLD,
        )

    def test_rebuild_slots_clears_directory(self):
        slot_state.SLOT_STATE_DIR.mkdir(parents=True, exist_ok=True)
        (slot_state.SLOT_STATE_DIR / "Mon_00.json").write_text("{}")
        (slot_state.SLOT_STATE_DIR / "Tue_01.json").write_text("{}")
        n = clear_all_slot_states()
        self.assertEqual(n, 2)
        self.assertTrue(slot_state.SLOT_STATE_DIR.exists())
        self.assertEqual(list(slot_state.SLOT_STATE_DIR.glob("*.json")), [])

    def test_single_file_deletion_forces_cold_for_that_slot_only(self):
        """Deleting one slot file forces cold rebuild for only that slot."""
        from merger import _annotate_freshness
        from session_parser import (
            MULTI_SOURCE_SYSTEM_INSTRUCTION_COLD,
            MULTI_SOURCE_SYSTEM_INSTRUCTION_INCREMENTAL,
        )

        slot_a = _build_slot(
            {"Main": [("F1+F2+F3", "Topic A (120)")]},
            day="Monday",
            tb_idx=0,
        )
        slot_b = _build_slot(
            {"Main": [("F1+F2+F3", "Topic B (120)")]},
            day="Monday",
            tb_idx=1,
        )
        _annotate_freshness(slot_a)
        _annotate_freshness(slot_b)
        # Seed both with previous merges.
        for s in (slot_a, slot_b):
            save_slot_state(
                SlotState(
                    day=s.day,
                    time_block_index=s.time_block_index,
                    source_hashes=s.current_hashes,
                    merged_sessions=[
                        {
                            "room_name": "RAN1_main",
                            "name": "Old",
                            "duration_minutes": 120,
                            "specified_start_time": None,
                            "chair": None,
                            "group_header": "",
                            "agenda_item": None,
                        }
                    ],
                )
            )

        # Delete only slot_a's state file.
        (slot_state.SLOT_STATE_DIR / "Monday_00.json").unlink()

        # Both sources change a bit so slot_b takes the incremental path.
        slot_a2 = _build_slot(
            {"Main": [("F1+F2+F3", "Topic A v2 (120)")]},
            day="Monday",
            tb_idx=0,
        )
        slot_b2 = _build_slot(
            {"Main": [("F1+F2+F3", "Topic B v2 (120)")]},
            day="Monday",
            tb_idx=1,
        )
        _annotate_freshness(slot_a2)
        _annotate_freshness(slot_b2)

        self.assertIsNone(slot_a2.previous_merge)
        self.assertIsNotNone(slot_b2.previous_merge)

        from session_parser import parse_time_slots

        fake_client = FakeGeminiClient(
            {
                "sessions": [
                    {
                        "room_name": "RAN1_main",
                        "name": "New",
                        "duration_minutes": 120,
                        "specified_start_time": None,
                        "chair": None,
                        "group_header": "",
                        "agenda_item": None,
                    }
                ]
            }
        )
        with patch("google.genai.Client", return_value=fake_client):
            with patch("time.sleep"):
                parse_time_slots(
                    [slot_a2, slot_b2],
                    {"Monday": slot_a2.main_rooms},
                )

        # Two LLM calls — one cold, one incremental.
        self.assertEqual(len(fake_client.calls), 2)
        instructions = [c["system_instruction"] for c in fake_client.calls]
        self.assertIn(MULTI_SOURCE_SYSTEM_INSTRUCTION_COLD, instructions)
        self.assertIn(MULTI_SOURCE_SYSTEM_INSTRUCTION_INCREMENTAL, instructions)


if __name__ == "__main__":
    unittest.main()
