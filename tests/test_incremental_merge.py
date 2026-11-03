"""Tests for the incremental-merge slot-state pipeline."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import working_groups.ran1.slot_state as slot_state
from working_groups.ran1.merger import SlotSource, SourceEntry, TimeSlotData, _annotate_freshness, _assign_cell_ids
from working_groups.ran1.models import RoomInfo
from working_groups.ran1.session_parser import _build_time_slot_prompt, _enforce_main_room_chair_evidence
from working_groups.ran1.slot_state import (
    SCHEMA_VERSION,
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
        source = SlotSource(
            label=label,
            entries=[SourceEntry(room_label=r, cell_text=t) for r, t in entries],
        )
        _assign_cell_ids(source)
        slot.sources.append(source)
    return slot


class MainRoomChairEvidenceTests(unittest.TestCase):
    def test_clears_vice_chair_inference_from_main_room(self):
        slot = _build_slot(
            {
                "Main Schedule": [("F1+F2+F3", "6GR / 10.5.1 (120)")],
                "Sorour's schedule": [
                    ("F1+F2+F3", "10.5.1.1 (60) / 10.5.1.2 (60)")
                ],
            }
        )
        parsed = {
            "sessions": [
                {"room_name": "RAN1_main", "name": "10.5.1.1", "chair": "Sorour"},
                {"room_name": "RAN1_brk1", "name": "10.5.1.2", "chair": "Sorour"},
            ]
        }

        _enforce_main_room_chair_evidence(
            parsed,
            slot,
            {"F1+F2+F3": "RAN1_main", "A1": "RAN1_brk1"},
        )

        self.assertIsNone(parsed["sessions"][0]["chair"])
        self.assertEqual(parsed["sessions"][1]["chair"], "Sorour")

    def test_keeps_explicit_main_schedule_chair_header(self):
        slot = _build_slot(
            {"Main Schedule": [("F1+F2+F3", "Xiaodong (120) / 6GR / 10.5.1 (120)")]}
        )
        parsed = {
            "sessions": [
                {"room_name": "RAN1_main", "name": "10.5.1", "chair": "Xiaodong"}
            ]
        }

        _enforce_main_room_chair_evidence(
            parsed,
            slot,
            {"F1+F2+F3": "RAN1_main", "A1": "RAN1_brk1"},
        )

        self.assertEqual(parsed["sessions"][0]["chair"], "Xiaodong")

    def test_struck_main_schedule_header_is_not_evidence(self):
        slot = _build_slot(
            {"Main Schedule": [("F1+F2+F3", "~~Sorour (120)~~ / 6GR / 10.5.1 (120)")]}
        )
        parsed = {
            "sessions": [
                {"room_name": "RAN1_main", "name": "10.5.1", "chair": "Sorour"}
            ]
        }

        _enforce_main_room_chair_evidence(
            parsed,
            slot,
            {"F1+F2+F3": "RAN1_main", "A1": "RAN1_brk1"},
        )

        self.assertIsNone(parsed["sessions"][0]["chair"])


class InferredCellTimingPromptTests(unittest.TestCase):
    def test_inferred_start_time_is_not_exposed_to_prompt(self):
        slot = _build_slot({"Main Schedule": []}, day="Thursday", tb_idx=3)
        slot.time_block_start = "17:00"
        slot.time_block_end = "19:30"
        slot.time_block_duration = 150
        slot.sources[0].entries.append(
            SourceEntry(
                room_label="F1+F2+F3 + A1",
                cell_text="Early dinner (60)",
                fallback_start_time="18:30",
            )
        )

        cold_prompt = _build_time_slot_prompt(slot)
        slot.previous_merge = [
            {
                "room_name": "ALL_ONLINE",
                "name": "Early dinner",
                "duration_minutes": 60,
                "specified_start_time": None,
                "fallback_start_time": "18:30",
            }
        ]
        slot.source_freshness = {"Main Schedule": "FRESH"}
        incremental_prompt = _build_time_slot_prompt(slot, mode="incremental")

        for prompt in (cold_prompt, incremental_prompt):
            self.assertNotIn("fallback_start_time", prompt)
            self.assertNotIn("18:30", prompt)
            self.assertIn("Early dinner (60)", prompt)


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

    def test_unchanged_merge_keeps_its_time(self):
        def merge(at, sessions):
            save_slot_state(SlotState(day="Tuesday", time_block_index=3,
                                      source_hashes={"Main Schedule": "abc"},
                                      merged_sessions=sessions, merged_at=at))
            return json.loads(slot_state.slot_state_path("Tuesday", 3).read_text())["merged_at"]

        self.assertEqual(merge("2026-10-28T06:01:17", [{"name": "x"}]), "2026-10-28T06:01:17")
        self.assertEqual(merge("2026-10-29T06:01:30", [{"name": "x"}]), "2026-10-28T06:01:17")
        self.assertEqual(merge("2026-10-30T06:01:30", [{"name": "y"}]), "2026-10-30T06:01:30")

    def test_missing_returns_none(self):
        self.assertIsNone(load_slot_state("Friday", 0))

    def test_old_semantics_version_is_not_loaded_as_baseline(self):
        save_slot_state(
            SlotState(
                day="Friday",
                time_block_index=0,
                merged_sessions=[
                    {
                        "name": "poisoned",
                        "specified_start_time": "09:50",
                    }
                ],
                schema_version=SCHEMA_VERSION - 1,
            )
        )

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

    def test_meeting_change_forces_cold_rebuild(self):
        # Snapshot saved for meeting M1; next run targets meeting M2 with
        # identical source content. The slot must be treated as brand-new
        # (no baseline) because meeting content cannot be incrementally
        # carried over.
        slot1 = _build_slot({"Main": [("F1+F2+F3", "AI/ML (120)")]})
        _annotate_freshness(slot1)
        save_slot_state(
            SlotState(
                day=slot1.day,
                time_block_index=slot1.time_block_index,
                source_hashes=slot1.current_hashes,
                merged_sessions=[{"name": "AI/ML", "room_name": "RAN1_main"}],
                meeting_id="M1",
            )
        )
        slot2 = _build_slot({"Main": [("F1+F2+F3", "AI/ML (120)")]})
        _annotate_freshness(slot2, meeting_id="M2")
        self.assertEqual(slot2.source_freshness, {"Main": "NEW"})
        self.assertIsNone(slot2.previous_merge)
        self.assertFalse(slot2.all_stale)

    def test_same_meeting_id_still_incremental(self):
        slot1 = _build_slot({"Main": [("F1+F2+F3", "AI/ML (120)")]})
        _annotate_freshness(slot1)
        save_slot_state(
            SlotState(
                day=slot1.day,
                time_block_index=slot1.time_block_index,
                source_hashes=slot1.current_hashes,
                merged_sessions=[{"name": "AI/ML", "room_name": "RAN1_main"}],
                meeting_id="M1",
            )
        )
        slot2 = _build_slot({"Main": [("F1+F2+F3", "AI/ML (120)")]})
        _annotate_freshness(slot2, meeting_id="M1")
        self.assertEqual(slot2.source_freshness, {"Main": "STALE"})
        self.assertTrue(slot2.all_stale)

    def test_legacy_snapshot_without_meeting_id_invalid(self):
        # Pre-upgrade snapshots have meeting_id=""; any current meeting id
        # must force a cold path on the first run after upgrading.
        slot1 = _build_slot({"Main": [("F1+F2+F3", "AI/ML (120)")]})
        _annotate_freshness(slot1)
        save_slot_state(
            SlotState(
                day=slot1.day,
                time_block_index=slot1.time_block_index,
                source_hashes=slot1.current_hashes,
                merged_sessions=[{"name": "AI/ML", "room_name": "RAN1_main"}],
            )
        )
        slot2 = _build_slot({"Main": [("F1+F2+F3", "AI/ML (120)")]})
        _annotate_freshness(slot2, meeting_id="M1")
        self.assertEqual(slot2.source_freshness, {"Main": "NEW"})
        self.assertIsNone(slot2.previous_merge)

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
        # Sessions cite the first cell headed in the prompt unless scripted.
        import copy
        import re as _re
        response = copy.deepcopy(self.scripted_response)
        cited = _re.findall(r"\[cell (c[0-9a-f]{6}) — ", contents)
        for session in response.get("sessions", []):
            session.setdefault("source_cells", cited[:1])
        return FakeGeminiResponse(response)


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
        from working_groups.ran1.session_parser import parse_time_slots

        fake_client = FakeGeminiClient(fake_response)
        day_rooms_map = {"Monday": slots[0].main_rooms}
        with patch("google.genai.Client", return_value=fake_client):
            with patch("time.sleep"):
                sessions = parse_time_slots(slots, day_rooms_map)
        return sessions, fake_client

    def test_all_stale_skips_llm(self):
        # First run: cold path populates state.
        from working_groups.ran1.merger import _annotate_freshness

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

    def test_incremental_prompt_sends_every_cell_with_its_change(self):
        from working_groups.ran1.merger import _annotate_freshness
        from working_groups.ran1.session_parser import MULTI_SOURCE_SYSTEM_INSTRUCTION_INCREMENTAL

        detailed = [
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
        ]
        # Run 1 (cold) records the merge and the cells it was made from.
        slot1 = _build_slot(
            {
                "Main": [("F1+F2+F3", "AI/ML (120)"), ("A1", "MIMO (120)")],
                "Sorour": [("F1+F2+F3", "AI 9.1.1 (60) / AI 9.1.2 (60)")],
            }
        )
        _annotate_freshness(slot1)
        self._run([slot1], {"sessions": detailed})

        # Run 2: Main changes only its A1 cell; Sorour is STALE.
        slot2 = _build_slot(
            {
                "Main": [("F1+F2+F3", "AI/ML (120)"), ("A1", "MIMO (60) / NTN (60)")],
                "Sorour": [("F1+F2+F3", "AI 9.1.1 (60) / AI 9.1.2 (60)")],
            }
        )
        _annotate_freshness(slot2)
        self.assertEqual(slot2.source_freshness["Main"], "FRESH")
        self.assertEqual(slot2.source_freshness["Sorour"], "STALE")

        _, client = self._run([slot2], {"sessions": detailed})

        self.assertEqual(len(client.calls), 1)
        call = client.calls[0]
        self.assertEqual(
            call["system_instruction"], MULTI_SOURCE_SYSTEM_INSTRUCTION_INCREMENTAL
        )
        prompt = call["contents"]
        # Every cell goes in, tagged with whether it changed.
        main, sorour = slot2.sources
        self.assertIn(f"[cell {main.entries[0].cell_id} — RAN1_main] UNCHANGED\nAI/ML (120)", prompt)
        self.assertIn(f"[cell {main.entries[1].cell_id} — RAN1_brk1] CHANGED\nMIMO (60) / NTN (60)", prompt)
        self.assertIn("was:\nMIMO (120)", prompt)  # the A1 text before
        self.assertIn(
            f"[cell {sorour.entries[0].cell_id} — Sorour — RAN1_main] UNCHANGED\n"
            "AI 9.1.1 (60) / AI 9.1.2 (60)", prompt)
        # The baseline is the starting point.
        self.assertIn('"AI 9.1.1"', prompt)

    def test_state_without_cell_ledger_is_rebuilt_cold(self):
        """A baseline saved before cell ledgers cannot say which cells changed."""
        from working_groups.ran1.merger import _annotate_freshness
        from working_groups.ran1.session_parser import MULTI_SOURCE_SYSTEM_INSTRUCTION_COLD

        slot1 = _build_slot({"Main": [("F1+F2+F3", "AI/ML (120)")]})
        _annotate_freshness(slot1)
        save_slot_state(
            SlotState(
                day=slot1.day,
                time_block_index=slot1.time_block_index,
                source_hashes=slot1.current_hashes,
                merged_sessions=[{"room_name": "RAN1_main", "name": "Old"}],
            )
        )
        slot2 = _build_slot({"Main": [("F1+F2+F3", "AI/ML v2 (120)")]})
        _annotate_freshness(slot2)
        self.assertIsNotNone(slot2.previous_merge)

        _, client = self._run([slot2], {"sessions": []})

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["system_instruction"], MULTI_SOURCE_SYSTEM_INSTRUCTION_COLD)
        # The cold merge starts the ledger for the next run.
        state = load_slot_state(slot2.day, slot2.time_block_index)
        self.assertEqual(
            [e["cell_text"] for e in state.source_entries["Main"]], ["AI/ML v2 (120)"]
        )

    def test_new_merge_prompt_version_is_rebuilt_cold(self):
        """A baseline made under an older merge prompt is not copied forward.

        Both cases go cold: only the hash changed (the version is in it), and
        the version changed in the same run as a cell. Copying the baseline
        in the second case would keep the old rules in every other room.
        """
        from working_groups.ran1.merger import MERGE_PROMPT_VERSION, _annotate_freshness, ledger_entries
        from working_groups.ran1.session_parser import MULTI_SOURCE_SYSTEM_INSTRUCTION_COLD

        for second_text in ("AI/ML (120)", "AI/ML (60) / NTN (60)"):
            with self.subTest(second_text=second_text):
                slot1 = _build_slot({"Main": [("F1+F2+F3", "AI/ML (120)"), ("A1", "MIMO (120)")]})
                _annotate_freshness(slot1)
                save_slot_state(
                    SlotState(
                        day=slot1.day,
                        time_block_index=slot1.time_block_index,
                        source_hashes={"Main": "hash-under-the-previous-prompt-version"},
                        merged_sessions=[{"room_name": "RAN1_main", "name": "Old"}],
                        source_entries=ledger_entries(slot1),
                        merge_prompt_version=MERGE_PROMPT_VERSION - 1,
                    )
                )
                slot2 = _build_slot({"Main": [("F1+F2+F3", second_text), ("A1", "MIMO (120)")]})
                _annotate_freshness(slot2)
                self.assertEqual(slot2.source_freshness, {"Main": "FRESH"})

                _, client = self._run([slot2], {"sessions": []})

                self.assertEqual(len(client.calls), 1)
                self.assertEqual(client.calls[0]["system_instruction"], MULTI_SOURCE_SYSTEM_INSTRUCTION_COLD)
                state = load_slot_state(slot2.day, slot2.time_block_index)
                self.assertEqual(state.merge_prompt_version, MERGE_PROMPT_VERSION)

    def test_after_a_wipe_the_cold_merge_starts_a_new_ledger(self):
        """force-deploy wipes every slot state: the next run is cold and
        knows nothing of before; the run after it is incremental again."""
        from working_groups.ran1.merger import _annotate_freshness
        from working_groups.ran1.session_parser import (
            MULTI_SOURCE_SYSTEM_INSTRUCTION_COLD,
            MULTI_SOURCE_SYSTEM_INSTRUCTION_INCREMENTAL,
        )

        sessions = [{"room_name": "RAN1_main", "name": "AI/ML", "duration_minutes": 120,
                     "specified_start_time": None, "chair": None, "group_header": "",
                     "agenda_item": None}]
        cells = {"Main": [("F1+F2+F3", "AI/ML (120)"), ("A1", "MIMO (120)")]}
        slot = _build_slot(cells)
        _annotate_freshness(slot)
        self._run([slot], {"sessions": sessions})
        clear_all_slot_states()

        slot = _build_slot(cells)
        _annotate_freshness(slot)
        self.assertIsNone(slot.previous_merge)
        self.assertEqual({e.change for s in slot.sources for e in s.entries}, {None})
        _, client = self._run([slot], {"sessions": sessions})
        self.assertEqual(client.calls[0]["system_instruction"], MULTI_SOURCE_SYSTEM_INSTRUCTION_COLD)
        self.assertNotIn("UNCHANGED", client.calls[0]["contents"])

        slot = _build_slot({"Main": [("F1+F2+F3", "AI/ML (120)"), ("A1", "MIMO (60) / NTN (60)")]})
        _annotate_freshness(slot)
        _, client = self._run([slot], {"sessions": sessions})
        self.assertEqual(client.calls[0]["system_instruction"], MULTI_SOURCE_SYSTEM_INSTRUCTION_INCREMENTAL)

    def test_cold_path_when_no_previous_state(self):
        from working_groups.ran1.merger import _annotate_freshness
        from working_groups.ran1.session_parser import MULTI_SOURCE_SYSTEM_INSTRUCTION_COLD

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
        from working_groups.ran1.merger import _annotate_freshness
        from working_groups.ran1.session_parser import (
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
        from working_groups.ran1.merger import MERGE_PROMPT_VERSION, ledger_entries

        for s in (slot_a, slot_b):
            save_slot_state(
                SlotState(
                    day=s.day,
                    time_block_index=s.time_block_index,
                    source_hashes=s.current_hashes,
                    source_entries=ledger_entries(s),
                    merge_prompt_version=MERGE_PROMPT_VERSION,
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

        from working_groups.ran1.session_parser import parse_time_slots

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


class SourceCellRetryTests(unittest.TestCase):
    """An answer whose source_cells are unusable is asked again, then fails."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._orig_dir = slot_state.SLOT_STATE_DIR
        slot_state.SLOT_STATE_DIR = Path(self.tmp.name) / "slot_state"
        os.environ["GEMINI_API_KEY"] = "fake-key"
        from shared import notices
        self._orig_notices = notices.PATH
        notices.PATH = Path(self.tmp.name) / "notices.json"

    def tearDown(self):
        from shared import notices
        slot_state.SLOT_STATE_DIR = self._orig_dir
        notices.PATH = self._orig_notices

    def _run(self, answers):
        from working_groups.ran1.merger import _annotate_freshness
        from working_groups.ran1.session_parser import parse_time_slots

        slot = _build_slot({"Main": [("F1+F2+F3", "AI/ML (120)")]})
        _annotate_freshness(slot)
        client = FakeGeminiClient({})
        replies = iter(answers)
        client.generate_content = lambda **kw: (
            client.calls.append(kw["contents"]) or FakeGeminiResponse(next(replies))
        )
        with patch("google.genai.Client", return_value=client):
            with patch("time.sleep"):
                sessions = parse_time_slots([slot], {"Monday": slot.main_rooms})
        return slot, sessions, client.calls

    @staticmethod
    def _answer(cells):
        return {"sessions": [{
            "room_name": "RAN1_main", "name": "AI/ML", "duration_minutes": 120,
            "specified_start_time": None, "chair": None, "group_header": "",
            "agenda_item": None, "source_cells": cells,
        }]}

    def test_asks_again_with_the_problems(self):
        good = _build_slot({"Main": [("F1+F2+F3", "AI/ML (120)")]}).sources[0].entries[0].cell_id
        _, sessions, calls = self._run([self._answer(["cdead00"]), self._answer([good])])
        self.assertEqual(len(calls), 2)
        self.assertIn("Fix these problems with source_cells", calls[1])
        self.assertIn("no cell cdead00", calls[1])
        self.assertEqual((sessions[0].start_time, sessions[0].end_time), ("11:00", "13:00"))

    def test_gives_up_with_a_notice(self):
        from shared import notices

        with self.assertRaises(RuntimeError):
            self._run([self._answer([])] * 3)
        recorded = json.loads(notices.PATH.read_text())
        self.assertEqual(recorded[0]["kind"], "source-cells")
        self.assertTrue(any("source_cells is empty" in line for line in recorded[0]["lines"]))


if __name__ == "__main__":
    unittest.main()
