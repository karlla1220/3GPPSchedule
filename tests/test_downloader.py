"""Tests for downloader meeting-ID extraction and schedule selection."""

import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from downloader import (
    _extract_meeting_id,
    _meeting_rank,
    _extract_version_from_name,
    _pick_latest_in_meeting_group,
    discover_schedule_sources,
    find_latest_chair_notes,
    find_latest_schedule,
    get_all_remote_schedule_info,
    get_latest_chair_notes_info,
    load_schedule_state,
    save_schedule_state,
)


def _f(name: str, uploaded_at: datetime | None = None, url: str = "") -> dict:
    """Helper to build a file dict for testing."""
    return {"name": name, "url": url, "uploaded_at": uploaded_at}


class ExtractMeetingIdTests(unittest.TestCase):
    """Tests for _extract_meeting_id."""

    def test_basic_meeting_number(self):
        self.assertEqual(
            _extract_meeting_id("RAN1#124 online and offline schedules - v02.docx"),
            "ran1#124",
        )

    def test_bis_suffix_no_separator(self):
        self.assertEqual(
            _extract_meeting_id("RAN1#124bis schedule_v07.docx"),
            "ran1#124bis",
        )

    def test_bis_suffix_with_hyphen(self):
        self.assertEqual(
            _extract_meeting_id("RAN1#124-bis schedule - v01.docx"),
            "ran1#124bis",
        )

    def test_bis_suffix_with_space(self):
        self.assertEqual(
            _extract_meeting_id("RAN1#124 bis schedule - v01.docx"),
            "ran1#124bis",
        )

    def test_e_suffix(self):
        self.assertEqual(
            _extract_meeting_id("RAN1#120e online schedules - v03.docx"),
            "ran1#120e",
        )

    def test_e_suffix_with_hyphen(self):
        self.assertEqual(
            _extract_meeting_id("RAN1#120-e online schedules - v03.docx"),
            "ran1#120e",
        )

    def test_adhoc_suffix(self):
        self.assertEqual(
            _extract_meeting_id("RAN1#120adhoc schedule_v01.docx"),
            "ran1#120adhoc",
        )

    def test_adhoc_suffix_with_space(self):
        self.assertEqual(
            _extract_meeting_id("RAN1#120 adhoc schedule_v01.docx"),
            "ran1#120adhoc",
        )

    def test_case_insensitive(self):
        self.assertEqual(
            _extract_meeting_id("ran1#124BIS schedule - v01.docx"),
            "ran1#124bis",
        )

    def test_ran2_meeting(self):
        self.assertEqual(
            _extract_meeting_id("RAN2#99 online schedules - v01.docx"),
            "ran2#99",
        )

    def test_no_meeting_id_returns_none(self):
        self.assertIsNone(_extract_meeting_id("custom schedule name.docx"))

    def test_no_meeting_id_for_plain_name(self):
        self.assertIsNone(_extract_meeting_id("schedule_v01.docx"))

    def test_draft_prefix_does_not_affect_extraction(self):
        self.assertEqual(
            _extract_meeting_id("Draft RAN1#124bis online and offline schedules - v01.docx"),
            "ran1#124bis",
        )


class PickLatestInMeetingGroupTests(unittest.TestCase):
    """Tests for _pick_latest_in_meeting_group."""

    def test_meeting_rank_orders_regular_meetings(self):
        self.assertLess(_meeting_rank("ran1#124"), _meeting_rank("ran1#124bis"))
        self.assertLess(_meeting_rank("ran1#124bis"), _meeting_rank("ran1#125"))
        self.assertIsNone(_meeting_rank("ran1#124adhoc"))

    def test_same_meeting_picks_highest_version(self):
        """Scenario A: within the same meeting, pick the highest version."""
        files = [
            _f("RAN1#124bis schedule - v02.docx", datetime(2026, 4, 14, 8, 0)),
            _f("Draft RAN1#124bis schedule - v01.docx", datetime(2026, 4, 14, 13, 0)),
        ]
        result = _pick_latest_in_meeting_group(files)
        self.assertIn("v02", result["name"])

    def test_same_meeting_picks_highest_version_with_copy_suffix(self):
        """Version suffixes like v04_3 should sort after the base v04 file."""
        files = [
            _f("RAN1#125 schedule for Hiroki Adhoc2 sessions_v04.docx", datetime(2026, 5, 16, 12, 57)),
            _f("RAN1#125 schedule for Hiroki Adhoc2 sessions_v04_1.docx", datetime(2026, 5, 16, 12, 57)),
            _f("RAN1#125 schedule for Hiroki Adhoc2 sessions_v04_2.docx", datetime(2026, 5, 16, 12, 57)),
            _f("RAN1#125 schedule for Hiroki Adhoc2 sessions_v04_3.docx", datetime(2026, 5, 18, 3, 21)),
        ]
        result = _pick_latest_in_meeting_group(files)
        self.assertEqual(
            result["name"],
            "RAN1#125 schedule for Hiroki Adhoc2 sessions_v04_3.docx",
        )

    def test_old_meeting_high_version_not_selected(self):
        """Scenario B: old meeting's high-version file should not win."""
        files = [
            _f("RAN1#123 schedule - v09.docx", datetime(2026, 1, 15, 10, 0)),
            _f("RAN1#124bis schedule - v01.docx", datetime(2026, 4, 14, 8, 0)),
        ]
        result = _pick_latest_in_meeting_group(files)
        self.assertIn("124bis", result["name"])
        self.assertIn("v01", result["name"])

    def test_multiple_files_same_meeting(self):
        """Multiple versions in the current meeting: pick highest version."""
        files = [
            _f("RAN1#123 schedule - v09.docx", datetime(2026, 1, 15, 10, 0)),
            _f("RAN1#124bis schedule - v01.docx", datetime(2026, 4, 14, 8, 0)),
            _f("RAN1#124bis schedule - v02.docx", datetime(2026, 4, 14, 9, 0)),
            _f("RAN1#124bis schedule - v03.docx", datetime(2026, 4, 14, 12, 0)),
        ]
        result = _pick_latest_in_meeting_group(files)
        self.assertIn("v03", result["name"])
        self.assertIn("124bis", result["name"])

    def test_single_file(self):
        """Single file should be returned as-is."""
        files = [
            _f("RAN1#124bis schedule - v01.docx", datetime(2026, 4, 14, 8, 0)),
        ]
        result = _pick_latest_in_meeting_group(files)
        self.assertIn("v01", result["name"])

    def test_files_without_meeting_id_grouped_together(self):
        """Files without a meeting ID should be grouped under None."""
        files = [
            _f("schedule - v01.docx", datetime(2026, 4, 14, 8, 0)),
            _f("schedule - v02.docx", datetime(2026, 4, 14, 9, 0)),
        ]
        result = _pick_latest_in_meeting_group(files)
        self.assertIn("v02", result["name"])

    def test_bis_and_non_bis_are_different_meetings(self):
        """RAN1#124 and RAN1#124bis should be different groups."""
        files = [
            _f("RAN1#124 schedule - v05.docx", datetime(2026, 2, 10, 10, 0)),
            _f("RAN1#124bis schedule - v01.docx", datetime(2026, 4, 14, 8, 0)),
        ]
        result = _pick_latest_in_meeting_group(files)
        self.assertIn("124bis", result["name"])
        self.assertIn("v01", result["name"])

    def test_newer_regular_meeting_beats_cached_preferred_meeting(self):
        """A later plenary meeting should advance state automatically."""
        files = [
            _f("RAN1#124bis schedule - v08.docx", datetime(2026, 4, 18, 8, 3)),
            _f("RAN1#125 schedule - v01.docx", datetime(2026, 4, 19, 9, 0)),
        ]
        result = _pick_latest_in_meeting_group(
            files,
            preferred_meeting_id="ran1#124bis",
        )
        self.assertIn("125", result["name"])

    def test_irregular_meetings_fall_back_to_upload_time(self):
        """Irregular meetings are ordered by upload timestamp, not by name."""
        files = [
            _f("RAN1#124adhoc schedule - v01.docx", datetime(2026, 4, 17, 8, 0)),
            _f("RAN1#124e schedule - v01.docx", datetime(2026, 4, 18, 8, 0)),
        ]
        result = _pick_latest_in_meeting_group(files)
        self.assertIn("124e", result["name"])


class FindLatestScheduleMeetingAwareTests(unittest.TestCase):
    """Integration tests for find_latest_schedule with meeting grouping."""

    def test_returns_none_for_empty(self):
        self.assertIsNone(find_latest_schedule([]))

    def test_returns_none_when_no_schedule_files(self):
        files = [_f("agenda_v01.docx", datetime(2026, 4, 14, 8, 0))]
        self.assertIsNone(find_latest_schedule(files))

    def test_scenario_a_same_meeting_higher_version_wins(self):
        """Draft v01 uploaded after v02 → v02 should still win."""
        files = [
            _f("RAN1#124bis online and offline schedules - v02.docx", datetime(2026, 4, 14, 8, 38)),
            _f("Draft RAN1#124bis online and offline schedules - v01.docx", datetime(2026, 4, 14, 13, 3)),
        ]
        result = find_latest_schedule(files)
        assert result is not None
        self.assertIn("v02", result["name"])

    def test_scenario_b_old_meeting_high_version_loses(self):
        """Old meeting v09 should not beat current meeting v01."""
        files = [
            _f("RAN1#123 online and offline schedules - v09.docx", datetime(2026, 1, 15, 10, 0)),
            _f("RAN1#124bis online and offline schedules - v01.docx", datetime(2026, 4, 14, 8, 0)),
        ]
        result = find_latest_schedule(files)
        assert result is not None
        self.assertIn("124bis", result["name"])

    def test_fallback_no_timestamps(self):
        """When no timestamps, fallback to version number."""
        files = [
            _f("RAN1#124bis schedule - v01.docx"),
            _f("RAN1#124bis schedule - v03.docx"),
        ]
        result = find_latest_schedule(files)
        assert result is not None
        self.assertIn("v03", result["name"])

    def test_fallback_no_timestamps_handles_copy_suffix_versions(self):
        files = [
            _f("RAN1#125 schedule for Hiroki Adhoc2 sessions_v04.docx"),
            _f("RAN1#125 schedule for Hiroki Adhoc2 sessions_v04_1.docx"),
            _f("RAN1#125 schedule for Hiroki Adhoc2 sessions_v04_3.docx"),
        ]
        result = find_latest_schedule(files)
        assert result is not None
        self.assertEqual(
            result["name"],
            "RAN1#125 schedule for Hiroki Adhoc2 sessions_v04_3.docx",
        )

    def test_preferred_meeting_advances_to_newer_regular_meeting(self):
        files = [
            _f("RAN1#124bis schedule - v08.docx", datetime(2026, 4, 18, 8, 3)),
            _f("RAN1#125 schedule - v01.docx", datetime(2026, 4, 19, 9, 0)),
        ]
        result = find_latest_schedule(files, preferred_meeting_id="ran1#124bis")
        assert result is not None
        self.assertIn("125", result["name"])

    def test_last_resort_returns_first(self):
        """When no version or timestamp, return first file."""
        files = [_f("schedule.docx"), _f("another schedule.docx")]
        result = find_latest_schedule(files)
        assert result is not None
        self.assertEqual(result["name"], "schedule.docx")


class FindLatestChairNotesMeetingAwareTests(unittest.TestCase):
    """Integration tests for find_latest_chair_notes with meeting grouping."""

    def test_returns_none_for_empty(self):
        self.assertIsNone(find_latest_chair_notes([]))

    def test_scenario_b_old_meeting_high_version_loses(self):
        """Old meeting's high-version Chair notes should not win."""
        files = [
            _f("RAN1#123 chair notes - v09.docx", datetime(2026, 1, 15, 10, 0)),
            _f("RAN1#124bis chair notes - v01.docx", datetime(2026, 4, 14, 8, 0)),
        ]
        result = find_latest_chair_notes(files)
        assert result is not None
        self.assertIn("124bis", result["name"])

    def test_same_meeting_picks_highest_version(self):
        files = [
            _f("RAN1#124bis chair notes - v01.docx", datetime(2026, 4, 14, 8, 0)),
            _f("RAN1#124bis chair notes - v03.docx", datetime(2026, 4, 14, 12, 0)),
        ]
        result = find_latest_chair_notes(files)
        assert result is not None
        self.assertIn("v03", result["name"])

    def test_accepts_chairman_note_filenames(self):
        files = [
            _f("RAN1#124bis chairman notes - v01.docx", datetime(2026, 4, 14, 8, 0)),
        ]
        result = find_latest_chair_notes(files)
        assert result is not None
        self.assertIn("chairman", result["name"].lower())


class GetLatestChairNotesInfoTests(unittest.TestCase):
    """Tests for config-aware Chair notes lookup across inboxes and extras."""

    @patch("downloader.list_remote_files")
    def test_searches_all_configured_inboxes_and_extra_folders(self, mock_list_remote_files):
        mock_list_remote_files.side_effect = [
            [_f("RAN1#124 chair notes - v02.docx", datetime(2026, 4, 1, 9, 0))],
            [_f("RAN1#125 chairman notes - v01.docx", datetime(2026, 4, 27, 9, 0))],
            [_f("RAN1#125 chair notes - v03.docx", datetime(2026, 4, 28, 9, 0))],
        ]

        result = get_latest_chair_notes_info(
            urls=[
                "https://example.com/legacy/Inbox/",
                "https://example.com/next/Inbox/",
            ],
            extra_folders=[
                {"url": "https://example.com/custom/Chair_notes/", "name": "Chair_notes"},
            ],
        )

        assert result is not None
        self.assertEqual(result["name"], "RAN1#125 chair notes - v03.docx")
        self.assertEqual(result["source_url"], "https://example.com/custom/Chair_notes/")
        self.assertEqual(
            mock_list_remote_files.call_args_list[0].args[0],
            "https://example.com/legacy/Inbox/Chair_notes",
        )
        self.assertEqual(
            mock_list_remote_files.call_args_list[1].args[0],
            "https://example.com/next/Inbox/Chair_notes",
        )
        self.assertEqual(
            mock_list_remote_files.call_args_list[2].args[0],
            "https://example.com/custom/Chair_notes/",
        )


class DiscoverScheduleSourcesMeetingFilterTests(unittest.TestCase):
    """Tests for current-meeting filtering across discovered schedule sources."""

    @patch("downloader.list_remote_files")
    @patch("downloader.list_inbox_subfolders")
    def test_filters_sibling_folder_files_to_current_chair_meeting(
        self,
        mock_list_inbox_subfolders,
        mock_list_remote_files,
    ):
        mock_list_inbox_subfolders.return_value = [
            {"name": "Chair_notes", "url": "https://example.com/Inbox/Chair_notes"},
            {"name": "Hiroki_notes", "url": "https://example.com/Inbox/Hiroki_notes"},
        ]
        mock_list_remote_files.side_effect = [
            [_f("Draft RAN1#125 online and offline schedules - v00.docx", datetime(2026, 5, 11, 7, 43))],
            [
                _f("RAN1#124bis schedule for Hiroki Adhoc2 sessions_v11.docx", datetime(2026, 4, 17, 5, 42)),
                _f("RAN1#125 schedule for Hiroki sessions_v00.docx", datetime(2026, 5, 11, 8, 0)),
            ],
            [],
        ]

        result = discover_schedule_sources(urls=["https://example.com/Inbox/"])

        self.assertEqual(len(result), 2)
        by_person = {s.person_name: s for s in result if s.person_name}
        self.assertEqual(
            by_person["Hiroki"].file_info["name"],
            "RAN1#125 schedule for Hiroki sessions_v00.docx",
        )

    @patch("downloader.list_remote_files")
    @patch("downloader.list_inbox_subfolders")
    def test_filters_old_inbox_vice_chair_sources_after_meeting_advances(
        self,
        mock_list_inbox_subfolders,
        mock_list_remote_files,
    ):
        mock_list_inbox_subfolders.side_effect = [
            [
                {"name": "Chair_notes", "url": "https://example.com/old/Inbox/Chair_notes"},
                {"name": "Hiroki_notes", "url": "https://example.com/old/Inbox/Hiroki_notes"},
                {"name": "Sorour_notes", "url": "https://example.com/old/Inbox/Sorour_notes"},
            ],
            [
                {"name": "Chair_notes", "url": "https://example.com/new/Inbox/Chair_notes"},
            ],
        ]
        mock_list_remote_files.side_effect = [
            [_f("RAN1#124bis online and offline schedules - v08.docx", datetime(2026, 4, 17, 6, 15))],
            [_f("RAN1#124bis schedule for Hiroki Adhoc2 sessions_v11.docx", datetime(2026, 4, 17, 5, 42))],
            [_f("RAN1#124bis Sorour online and offline schedules - v04.docx", datetime(2026, 4, 16, 6, 29))],
            [],
            [_f("Draft RAN1#125 online and offline schedules - v00.docx", datetime(2026, 5, 11, 7, 43))],
            [],
        ]

        result = discover_schedule_sources(
            urls=[
                "https://example.com/old/Inbox/",
                "https://example.com/new/Inbox/",
            ],
            preferred_meeting_id="ran1#124bis",
        )

        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].is_main)
        self.assertEqual(
            result[0].file_info["name"],
            "Draft RAN1#125 online and offline schedules - v00.docx",
        )

    @patch("downloader.list_remote_files")
    @patch("downloader.list_inbox_subfolders")
    def test_change_check_uses_current_meeting_sources_only(
        self,
        mock_list_inbox_subfolders,
        mock_list_remote_files,
    ):
        mock_list_inbox_subfolders.side_effect = [
            [
                {"name": "Chair_notes", "url": "https://example.com/old/Inbox/Chair_notes"},
                {"name": "Hiroki_notes", "url": "https://example.com/old/Inbox/Hiroki_notes"},
                {"name": "Sorour_notes", "url": "https://example.com/old/Inbox/Sorour_notes"},
            ],
            [
                {"name": "Chair_notes", "url": "https://example.com/new/Inbox/Chair_notes"},
            ],
        ]
        mock_list_remote_files.side_effect = [
            [_f("RAN1#124bis online and offline schedules - v08.docx", datetime(2026, 4, 17, 6, 15))],
            [_f("RAN1#124bis schedule for Hiroki Adhoc2 sessions_v11.docx", datetime(2026, 4, 17, 5, 42))],
            [_f("RAN1#124bis Sorour online and offline schedules - v04.docx", datetime(2026, 4, 16, 6, 29))],
            [],
            [_f("Draft RAN1#125 online and offline schedules - v00.docx", datetime(2026, 5, 11, 7, 43))],
            [],
        ]

        result = get_all_remote_schedule_info(
            urls=[
                "https://example.com/old/Inbox/",
                "https://example.com/new/Inbox/",
            ],
            preferred_meeting_id="ran1#125",
        )

        self.assertEqual(
            result,
            [
                {
                    "folder": "Chair_notes",
                    "name": "Draft RAN1#125 online and offline schedules - v00.docx",
                    "uploaded_at": "2026-05-11T07:43:00",
                }
            ],
        )


class LoadScheduleStateTests(unittest.TestCase):
    """Tests for load_schedule_state."""

    def test_returns_empty_dict_for_missing_file(self):
        result = load_schedule_state(Path("/tmp/nonexistent_state.json"))
        self.assertEqual(result, {})

    def test_returns_empty_dict_for_invalid_json(self):
        p = Path("/tmp/test_bad_state.json")
        p.write_text("not json")
        try:
            result = load_schedule_state(p)
            self.assertEqual(result, {})
        finally:
            p.unlink(missing_ok=True)

    def test_migrates_legacy_list_format(self):
        p = Path("/tmp/test_legacy_state.json")
        legacy = [{"folder": "Chair_notes", "name": "sched.docx", "uploaded_at": "2026-01-01T00:00:00"}]
        p.write_text(json.dumps(legacy))
        try:
            result = load_schedule_state(p)
            self.assertEqual(result, {"files": legacy})
        finally:
            p.unlink(missing_ok=True)

    def test_loads_new_format_with_meeting_metadata(self):
        p = Path("/tmp/test_new_state.json")
        state = {
            "files": [{"folder": "Chair_notes", "name": "sched.docx", "uploaded_at": "2026-01-01T00:00:00"}],
            "meeting_id": "ran1#124bis",
            "timezone": "Europe/Malta",
        }
        p.write_text(json.dumps(state))
        try:
            result = load_schedule_state(p)
            self.assertEqual(result["meeting_id"], "ran1#124bis")
            self.assertEqual(result["timezone"], "Europe/Malta")
            self.assertEqual(len(result["files"]), 1)
        finally:
            p.unlink(missing_ok=True)


class SaveScheduleStateTests(unittest.TestCase):
    """Tests for save_schedule_state with meeting metadata."""

    def _make_source(self, folder: str, name: str, uploaded_at: datetime) -> MagicMock:
        s = MagicMock()
        s.folder_name = folder
        s.file_info = {"name": name, "uploaded_at": uploaded_at}
        return s

    def test_saves_with_meeting_metadata(self):
        p = Path("/tmp/test_save_state.json")
        sources = [
            self._make_source("Chair_notes", "RAN1#124bis schedule - v01.docx", datetime(2026, 4, 14, 8, 0)),
        ]
        try:
            save_schedule_state(sources, p, meeting_id="ran1#124bis", timezone="Europe/Malta")
            state = json.loads(p.read_text())
            self.assertEqual(state["meeting_id"], "ran1#124bis")
            self.assertEqual(state["timezone"], "Europe/Malta")
            self.assertIsInstance(state["files"], list)
            self.assertEqual(len(state["files"]), 1)
        finally:
            p.unlink(missing_ok=True)

    def test_saves_without_meeting_metadata(self):
        p = Path("/tmp/test_save_state_no_meta.json")
        sources = [
            self._make_source("Chair_notes", "schedule.docx", datetime(2026, 4, 14, 8, 0)),
        ]
        try:
            save_schedule_state(sources, p)
            state = json.loads(p.read_text())
            self.assertNotIn("meeting_id", state)
            self.assertNotIn("timezone", state)
            self.assertIn("files", state)
        finally:
            p.unlink(missing_ok=True)

    def test_roundtrip_save_and_load(self):
        p = Path("/tmp/test_roundtrip_state.json")
        sources = [
            self._make_source("Chair_notes", "RAN1#124bis schedule - v03.docx", datetime(2026, 4, 15, 6, 30)),
            self._make_source("Hiroki_notes", "RAN1#124bis Hiroki_v10.docx", datetime(2026, 4, 15, 14, 38)),
        ]
        try:
            save_schedule_state(sources, p, meeting_id="ran1#124bis", timezone="Europe/Malta")
            loaded = load_schedule_state(p)
            self.assertEqual(loaded["meeting_id"], "ran1#124bis")
            self.assertEqual(loaded["timezone"], "Europe/Malta")
            self.assertEqual(len(loaded["files"]), 2)
        finally:
            p.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
