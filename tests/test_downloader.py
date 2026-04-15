"""Tests for downloader meeting-ID extraction and schedule selection."""

import unittest
from datetime import datetime

from downloader import (
    _extract_meeting_id,
    _extract_version_from_name,
    _pick_latest_in_meeting_group,
    find_latest_chair_notes,
    find_latest_schedule,
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

    def test_same_meeting_picks_highest_version(self):
        """Scenario A: within the same meeting, pick the highest version."""
        files = [
            _f("RAN1#124bis schedule - v02.docx", datetime(2026, 4, 14, 8, 0)),
            _f("Draft RAN1#124bis schedule - v01.docx", datetime(2026, 4, 14, 13, 0)),
        ]
        result = _pick_latest_in_meeting_group(files)
        self.assertIn("v02", result["name"])

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


if __name__ == "__main__":
    unittest.main()
