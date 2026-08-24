"""Tests for downloader meeting-ID extraction and schedule selection."""

import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zipfile import ZipFile, is_zipfile

from downloader import (
    _current_meeting_from_sources,
    _extract_meeting_id,
    _filter_sources_to_meeting,
    _meeting_rank,
    _extract_version_from_name,
    _pick_latest_in_meeting_group,
    discover_schedule_sources,
    download_latest_chair_notes,
    download_latest_agenda,
    extract_document_from_zip,
    find_latest_agenda,
    find_latest_chair_notes,
    find_latest_schedule,
    get_all_remote_schedule_info,
    get_latest_chair_notes_info,
    find_local_schedule_sources,
    local_reference_hashes,
    local_reference_meeting_id,
    load_schedule_state,
    list_remote_files,
    save_schedule_state,
)
from models import ScheduleSource


def _f(name: str, uploaded_at: datetime | None = None, url: str = "") -> dict:
    """Helper to build a file dict for testing."""
    return {"name": name, "url": url, "uploaded_at": uploaded_at}


def test_extract_document_from_agenda_zip_returns_valid_docx(tmp_path):
    docx_path = tmp_path / "R1-2601750_Draft agenda for RAN1#125_v01.docx"
    with ZipFile(docx_path, "w") as docx:
        docx.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>""",
        )
        docx.writestr(
            "word/document.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>9.3.2.1 Deep agenda item</w:t></w:r></w:p></w:body>
</w:document>""",
        )

    agenda_zip = tmp_path / "R1-2601750.zip"
    with ZipFile(agenda_zip, "w") as zf:
        zf.write(docx_path, arcname=f"nested/{docx_path.name}")
    docx_path.unlink()

    extracted = extract_document_from_zip(agenda_zip)

    assert extracted is not None
    assert extracted.suffix == ".docx"
    assert extracted.name == "R1-2601750_Draft agenda for RAN1#125_v01.docx"
    assert is_zipfile(extracted)


def test_extract_agenda_zip_prefers_csv_over_docx(tmp_path):
    agenda_zip = tmp_path / "R1-2601750.zip"
    with ZipFile(agenda_zip, "w") as zf:
        zf.writestr("nested/agenda.docx", b"docx")
        zf.writestr("nested/agenda.csv", '"1","Opening"\n')

    extracted = extract_document_from_zip(
        agenda_zip,
        document_extensions=(".csv", ".docx"),
    )

    assert extracted is not None
    assert extracted.name == "agenda.csv"
    assert extracted.read_text() == '"1","Opening"\n'


def test_find_latest_agenda_prefers_csv_before_newer_docx_or_zip():
    latest = find_latest_agenda(
        [
            _f("agenda.docx", datetime(2026, 5, 20, 9, 0)),
            _f("R1-2601750.zip", datetime(2026, 5, 21, 9, 0)),
            _f("agenda.csv", datetime(2026, 5, 19, 5, 42)),
        ]
    )

    assert latest is not None
    assert latest["name"] == "agenda.csv"


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

    def test_tsg_folder_style_plain(self):
        self.assertEqual(
            _extract_meeting_id("TSGR1_126 online and offline schedules - v02.docx"),
            "ran1#126",
        )

    def test_tsg_folder_style_short_bis(self):
        self.assertEqual(
            _extract_meeting_id("TSGR1_124b online and offline schedules - v01.docx"),
            "ran1#124bis",
        )

    def test_tsg_folder_style_full_bis(self):
        self.assertEqual(
            _extract_meeting_id("TSGR1_124bis schedule - v01.docx"),
            "ran1#124bis",
        )

    def test_tsg_lower_case_and_mixed(self):
        # Filename case folding is handled by the IGNORECASE flag; verify
        # a whole filename still parses when the meeting token is lowercase.
        self.assertEqual(
            _extract_meeting_id("tsgR1_126 agenda.csv"),
            "ran1#126",
        )

    def test_tsg_group_number_example(self):
        self.assertEqual(
            _extract_meeting_id("TSGR1_123 chair schedule.docx"),
            "ran1#123",
        )

    def test_ran_hash_style_still_wins_over_tsg(self):
        both = "RAN1#124 and TSGR1_126 in one name.docx"
        self.assertEqual(_extract_meeting_id(both), "ran1#124")

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

    def test_macro_enabled_schedule_is_not_selected_for_table_parser(self):
        latest = find_latest_schedule(
            [
                _f(
                    "RAN1#126 schedule - v01.docm",
                    datetime(2026, 8, 24, 9, 0),
                ),
            ]
        )

        self.assertIsNone(latest)

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

    def test_preferred_meeting_does_not_fall_back_to_older_file(self):
        files = [
            _f("RAN1#125 schedule - v99.docx", datetime(2026, 5, 1, 9, 0)),
        ]
        self.assertIsNone(
            find_latest_schedule(files, preferred_meeting_id="ran1#126")
        )

    def test_preferred_meeting_still_allows_later_meeting(self):
        files = [
            _f("RAN1#125 schedule - v99.docx", datetime(2026, 5, 1, 9, 0)),
            _f("RAN1#127 schedule - v01.docx", datetime(2026, 5, 2, 9, 0)),
        ]
        result = find_latest_schedule(files, preferred_meeting_id="ran1#126")
        assert result is not None
        self.assertIn("127", result["name"])

    def test_locked_meeting_excludes_later_remote_meeting(self):
        files = [
            _f("RAN1#126 schedule - v03.docx", datetime(2026, 5, 1, 9, 0)),
            _f("RAN1#127 schedule - v01.docx", datetime(2026, 5, 2, 9, 0)),
        ]
        result = find_latest_schedule(
            files,
            preferred_meeting_id="ran1#126",
            locked_meeting_id="ran1#126",
        )
        assert result is not None
        self.assertIn("126", result["name"])


def test_local_reference_selection_uses_filename_not_mtime(tmp_path):
    ref_dir = tmp_path / "ref_in_manual"
    ref_dir.mkdir()
    older_name = ref_dir / "RAN1#125 online and offline schedules - v99.DOCX"
    newer_name = ref_dir / "RAN1#126 online and offline schedules - v01.docx"
    older_name.write_bytes(b"old")
    newer_name.write_bytes(b"new")

    # Deliberately make the older meeting appear newer on disk.
    older_name.touch()
    newer_name.touch()
    import os

    os.utime(older_name, (2_000_000_000, 2_000_000_000))
    os.utime(newer_name, (1_000_000_000, 1_000_000_000))

    sources, chosen = find_local_schedule_sources(ref_dir)

    assert chosen == newer_name
    assert sources[0].local_path == newer_name
    assert local_reference_meeting_id(ref_dir) == "ran1#126"


def test_local_main_source_meeting_is_authoritative(tmp_path):
    local_path = tmp_path / "RAN1#126 schedule.docx"
    local_path.write_bytes(b"local")
    local = ScheduleSource(
        folder_name="ref_in_manual",
        person_name=None,
        is_main=True,
        file_info={"name": local_path.name, "uploaded_at": None},
        local_path=local_path,
    )
    remote = ScheduleSource(
        folder_name="Chair_notes",
        person_name=None,
        is_main=True,
        file_info={
            "name": "RAN1#127 schedule.docx",
            "uploaded_at": datetime(2026, 5, 2, 9, 0),
        },
    )

    assert _current_meeting_from_sources([remote, local]) == "ran1#126"


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


def test_list_remote_files_includes_docm_chair_notes():
    html = """<table><tr>
      <td></td><td></td>
      <td><a href="Chair%20notes%20RAN1%23126_v00.docm">file</a></td>
      <td>2026/08/23 14:29</td>
    </tr></table>"""

    with patch(
        "downloader._get_with_retry",
        return_value=MagicMock(text=html),
    ):
        files = list_remote_files("https://example.com/Chair_notes")

    assert [f["name"] for f in files] == ["Chair notes RAN1#126_v00.docm"]


def test_forced_chair_notes_refresh_replaces_same_filename(tmp_path):
    target = tmp_path / "Chair notes RAN1#126_v00.docm"
    target.write_bytes(b"old-content")
    latest = {
        "name": target.name,
        "url": "https://example.org/chair.docm",
        "uploaded_at": datetime(2026, 8, 24, 9, 0),
    }

    def fake_download(_url, path, **_kwargs):
        path.write_bytes(b"new-content")
        return path

    with patch("downloader.download_and_resolve", side_effect=fake_download):
        result = download_latest_chair_notes(
            tmp_path,
            latest_info=latest,
            force=True,
        )

    assert result == target
    assert target.read_bytes() == b"new-content"


def test_forced_agenda_refresh_replaces_same_filename(tmp_path):
    target = tmp_path / "RAN1#126 agenda.docx"
    target.write_bytes(b"old-content")
    latest = {
        "name": target.name,
        "url": "https://example.org/agenda.docx",
        "uploaded_at": datetime(2026, 8, 24, 9, 0),
    }

    def fake_download(_url, path):
        path.write_bytes(b"new-content")
        return path

    with patch("downloader.download_file", side_effect=fake_download):
        result = download_latest_agenda(
            ["https://example.org/Agenda/"],
            tmp_path,
            latest_info=latest,
            force=True,
        )

    assert result == target
    assert target.read_bytes() == b"new-content"


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

    @patch("downloader.list_remote_files")
    def test_current_meeting_does_not_use_future_chair_notes(
        self,
        mock_list_remote_files,
    ):
        mock_list_remote_files.return_value = [
            _f("Chair notes RAN1#126_v00.docm", datetime(2026, 8, 23, 14, 29)),
            _f("Chair notes RAN1#127_v00.docm", datetime(2026, 11, 1, 9, 0)),
        ]

        result = get_latest_chair_notes_info(
            urls=["https://example.org/Inbox/"],
            preferred_meeting_id="ran1#126",
        )

        assert result is not None
        self.assertEqual(result["name"], "Chair notes RAN1#126_v00.docm")

    @patch("downloader.list_remote_files")
    def test_unsupported_chair_notes_format_is_not_selected(
        self,
        mock_list_remote_files,
    ):
        mock_list_remote_files.return_value = [
            _f("Chair notes RAN1#126_v00.pdf", datetime(2026, 8, 23, 14, 29)),
        ]

        result = get_latest_chair_notes_info(
            urls=["https://example.org/Inbox/"],
            preferred_meeting_id="ran1#126",
        )

        self.assertIsNone(result)


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

    def test_saves_meeting_source_metadata(self):
        p = Path("/tmp/test_save_state_meeting_source.json")
        sources = [
            self._make_source(
                "Chair_notes",
                "RAN1#126 schedule - v01.docx",
                datetime(2026, 5, 1, 8, 0),
            ),
        ]
        try:
            save_schedule_state(
                sources,
                p,
                meeting_id="ran1#126",
                meeting_source="local",
            )
            state = json.loads(p.read_text())
            self.assertEqual(state["meeting_source"], "local")
        finally:
            p.unlink(missing_ok=True)

    def test_saves_pending_timezone_without_reference(self):
        p = Path("/tmp/test_save_state_pending_timezone.json")
        sources = [
            self._make_source(
                "Chair_notes",
                "RAN1#126 schedule - v01.docx",
                datetime(2026, 8, 18, 8, 0),
            ),
        ]
        try:
            save_schedule_state(
                sources,
                p,
                meeting_id="ran1#126",
                timezone="UTC",
                timezone_status="pending_timezone_ref",
                timezone_ref=None,
            )
            state = json.loads(p.read_text())
            self.assertEqual(state["timezone_status"], "pending_timezone_ref")
            self.assertIsNone(state["timezone_ref"])
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

    def test_saves_agenda_metadata(self):
        p = Path("/tmp/test_save_state_agenda.json")
        sources = [
            self._make_source("Chair_notes", "RAN1#125 schedule - v02.docx", datetime(2026, 5, 19, 0, 17)),
        ]
        agenda = {
            "name": "R1-2601750.zip",
            "uploaded_at": "2026-05-18T08:30:00",
            "url": "https://example.com/Agenda/R1-2601750.zip",
            "source_url": "https://example.com/Agenda/",
            "document_file": "R1-2601750_Draft agenda.docx",
            "local_path": "downloads/Agenda/R1-2601750_Draft agenda.docx",
        }
        try:
            save_schedule_state(
                sources,
                p,
                meeting_id="ran1#125",
                timezone="Asia/Shanghai",
                agenda=agenda,
            )
            loaded = load_schedule_state(p)
            self.assertEqual(loaded["agenda"], agenda)
        finally:
            p.unlink(missing_ok=True)

    def test_saves_local_refs_metadata(self):
        p = Path("/tmp/test_save_state_local_refs.json")
        sources = [
            self._make_source("Chair_notes", "schedule.docx", datetime(2026, 4, 14, 8, 0)),
        ]
        local_refs = {"draft - v01.docx": "abc123"}
        try:
            save_schedule_state(
                sources, p, meeting_id="ran1#125", local_refs=local_refs
            )
            loaded = load_schedule_state(p)
            self.assertEqual(loaded["local_refs"], local_refs)
        finally:
            p.unlink(missing_ok=True)

    def test_omits_local_refs_when_not_provided(self):
        p = Path("/tmp/test_save_state_no_local_refs.json")
        sources = [
            self._make_source("Chair_notes", "schedule.docx", datetime(2026, 4, 14, 8, 0)),
        ]
        try:
            save_schedule_state(sources, p)
            loaded = load_schedule_state(p)
            self.assertNotIn("local_refs", loaded)
        finally:
            p.unlink(missing_ok=True)


def test_local_reference_hashes_contents(tmp_path):
    ref_dir = tmp_path / "ref_in_manual"
    ref_dir.mkdir()
    (ref_dir / "schedule - v01.docx").write_bytes(b"draft-v1")
    (ref_dir / "schedule - v02.docx").write_bytes(b"draft-v2")
    (ref_dir / "notes.txt").write_bytes(b"ignored")

    result = local_reference_hashes(ref_dir)

    import hashlib

    assert set(result) == {"schedule - v01.docx", "schedule - v02.docx"}
    assert result["schedule - v01.docx"] == hashlib.sha256(b"draft-v1").hexdigest()
    assert result["schedule - v02.docx"] == hashlib.sha256(b"draft-v2").hexdigest()


def test_local_reference_hashes_missing_dir(tmp_path):
    assert local_reference_hashes(tmp_path / "nope") == {}


def test_local_reference_hashes_detects_content_change(tmp_path):
    ref_dir = tmp_path / "ref_in_manual"
    ref_dir.mkdir()
    f = ref_dir / "schedule - v01.docx"
    f.write_bytes(b"v1")
    first = local_reference_hashes(ref_dir)
    f.write_bytes(b"v2")
    second = local_reference_hashes(ref_dir)
    assert first != second


# ── External files (config.json ``extra_files``) ───────────────

import io
import zipfile as _zipfile
from urllib.parse import quote

from downloader import (
    EXTRA_FILES_DIR,
    EXTRA_FILES_STATE_PATH,
    _resolve_external_filename,
    _sanitize_filename,
    check_external_files,
    download_external_files,
    load_external_files_state,
    save_external_files_state,
)
import httpx


class FakeStreamResponse:
    """Context-manager mimicking httpx's stream response for mocking."""

    def __init__(self, headers: dict | None = None, body: bytes = b"", status_code: int = 200):
        self.headers = httpx.Headers(headers or {})
        self._body = body
        self.status_code = status_code
        self.request = httpx.Request("GET", "https://example.com/x")
        self._consumed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=self.request,
                response=httpx.Response(self.status_code, headers=self.headers, request=self.request),
            )

    def iter_bytes(self, chunk_size: int = 8192):
        if self._consumed:
            raise httpx.StreamConsumed("Attempted to stream already consumed response")
        self._consumed = True
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class SanitizeFilenameTests(unittest.TestCase):
    def test_replaces_unsafe_chars(self):
        self.assertEqual(_sanitize_filename('a\\b/c:d*e?f"g<h>i|j'), "a_b_c_d_e_f_g_h_i_j")

    def test_strips_whitespace_and_dots(self):
        self.assertEqual(_sanitize_filename("  ..name.docx..  "), "name.docx")

    def test_drops_control_chars(self):
        self.assertEqual(_sanitize_filename("a\x01b.docx"), "ab.docx")


class ResolveExternalFilenameTests(unittest.TestCase):
    def test_content_disposition_filename_star_wins(self):
        hdrs = {"content-disposition": "attachment; filename*=UTF-8''A%20B%20v01.docx"}
        self.assertEqual(
            _resolve_external_filename(hdrs, "https://example.com/x/y.zip", {}, 0),
            "A B v01.docx",
        )

    def test_content_disposition_quoted_filename(self):
        hdrs = {"content-disposition": 'attachment; filename="my file.docx"'}
        self.assertEqual(
            _resolve_external_filename(hdrs, "https://example.com/x", {}, 0),
            "my file.docx",
        )

    def test_content_disposition_plain_filename(self):
        hdrs = {"content-disposition": "attachment; filename=some_file.pptx"}
        self.assertEqual(
            _resolve_external_filename(hdrs, "https://example.com/x", {}, 0),
            "some_file.pptx",
        )

    def test_url_path_fallback_when_no_cd(self):
        self.assertEqual(
            _resolve_external_filename({}, "https://example.com/dir/RAN1%23124 sched.docx", {}, 3),
            "RAN1#124 sched.docx",
        )

    def test_entry_name_fallback(self):
        self.assertEqual(
            _resolve_external_filename({}, "https://example.com/", {"name": "picked.pdf"}, 7),
            "picked.pdf",
        )

    def test_numeric_fallback_last(self):
        self.assertEqual(
            _resolve_external_filename({}, "https://example.com/", {}, 5),
            "external_5",
        )

    def test_unsafe_cd_name_sanitized(self):
        hdrs = {"content-disposition": 'attachment; filename="a<b>c.docx"'}
        self.assertEqual(
            _resolve_external_filename(hdrs, "https://example.com/", {}, 0),
            "a_b_c.docx",
        )

    def test_cd_name_sanitizes_empty_falls_to_url(self):
        hdrs = {"content-disposition": "attachment; filename=\"   \""}
        self.assertEqual(
            _resolve_external_filename(hdrs, "https://example.com/final.pdf", {}, 1),
            "final.pdf",
        )


class _IsolatedDownloadEnv:
    """Keeps a TemporaryDirectory alive until the test releases it.

    Usage: with _IsolatedDownloadEnv() as env: ...
    """

    def __init__(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()

    @property
    def root(self) -> Path:
        return Path(self._tmp.name)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._tmp.cleanup()


def _run_download_external(entries, resp_factory):
    """Run download_external_files with patched stream responses into an isolated temp dir."""
    env = _IsolatedDownloadEnv()

    def make_stream(m, u, **k):
        return _OneshotCtx(resp_factory())

    with (
        patch("downloader.httpx.stream", side_effect=make_stream),
        patch("time.sleep"),
    ):
        result = download_external_files(
            entries,
            dest_dir=env.root,
            state={"files": {}},
        )

    return result, env


def test_file_download_returns_paths_and_state():
    url = "https://example.com/wa.exe?A3=x"
    entry = {"url": url, "type": "schedule"}
    resp = FakeStreamResponse(
        headers={
            "content-disposition": 'attachment; filename="RAN1#126 schedule - v02.docx"',
        },
        body=b"docx!\x00",
    )
    with _IsolatedDownloadEnv() as env:
        (results, state), _ = _run_download_external([entry], lambda: resp)
        assert len(results) == 1
        assert results[0][0] is entry
        assert results[0][1].name == "RAN1#126 schedule - v02.docx"
    import hashlib
    assert state == {
        url: {
            "sha256": hashlib.sha256(b"docx!\x00").hexdigest(),
            "filename": "RAN1#126 schedule - v02.docx",
        }
    }


def test_file_download_entry_name_fallback():
    url = "https://example.com/?L=ran1"
    entry = {"url": url, "type": "chair_notes", "name": "RAN1#126 Chair_notes - v03.docx"}
    resp = FakeStreamResponse(body=b"chair")
    with _IsolatedDownloadEnv() as env:
        (results, state), _ = _run_download_external([entry], lambda: resp)
        assert results[0][1].name == "RAN1#126 Chair_notes - v03.docx"
        assert results[0][1].read_bytes() == b"chair"
    import hashlib
    assert state == {
        url: {
            "sha256": hashlib.sha256(b"chair").hexdigest(),
            "filename": "RAN1#126 Chair_notes - v03.docx",
        }
    }


def test_zip_extraction_appends_doc_path():
    docx_bytes = b"PK\x03\x04 fake docx payload"
    buf = io.BytesIO()
    with _zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("nested/RAN1#126 schedule - v01.docx", docx_bytes)
    zip_bytes = buf.getvalue()

    url = "https://example.com/zip?dl=1"
    entry = {"url": url, "type": "schedule"}
    resp = FakeStreamResponse(
        headers={"content-disposition": 'attachment; filename="RAN1#126.zip"'},
        body=zip_bytes,
    )
    with _IsolatedDownloadEnv() as env:
        (results, state), _ = _run_download_external([entry], lambda: resp)
        # one result, pointing at the unpacked document
        assert len(results) == 1
        assert results[0][1].read_bytes() == docx_bytes
    import hashlib
    assert state == {
        url: {
            "sha256": hashlib.sha256(zip_bytes).hexdigest(),
            "filename": "RAN1#126.zip",
        }
    }


def test_4xx_no_retry_no_state():
    url = "https://example.com/missing"
    entry = {"url": url, "type": "schedule", "name": "x.docx"}
    resp = FakeStreamResponse(status_code=404, body=b"")
    sleeps = []
    with _IsolatedDownloadEnv() as env:
        with (
            patch("downloader.httpx.stream", side_effect=lambda m, u, **k: _OneshotCtx(resp)),
            patch("time.sleep", side_effect=sleeps.append),
        ):
            results, state = download_external_files([entry], dest_dir=env.root)
    assert results == []
    assert state == {}
    assert sleeps == []  # no retry on 4xx


def test_5xx_retries_then_succeeds():
    url = "https://example.com/dir/flaky.docx"
    entry = {"url": url, "type": "schedule"}
    calls = {"n": 0}

    def make_stream(m, u, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            return _OneshotCtx(FakeStreamResponse(status_code=503, body=b"err"))
        return _OneshotCtx(FakeStreamResponse(body=b"ok-docx"))

    sleeps = []
    with _IsolatedDownloadEnv() as env:
        with (
            patch("downloader.httpx.stream", side_effect=make_stream),
            patch("time.sleep", side_effect=sleeps.append),
        ):
            results, state = download_external_files([entry], dest_dir=env.root)
        assert len(results) == 1
        assert results[0][1].read_bytes() == b"ok-docx"
    assert sleeps == [5, 10]  # backoff applied
    import hashlib
    assert state == {
        url: {
            "sha256": hashlib.sha256(b"ok-docx").hexdigest(),
            "filename": "flaky.docx",
        }
    }


def test_filename_from_url_when_no_cd():
    url = "https://example.com/dir/RAN1%23126%20sched%20v04.docx"
    entry = {"url": url, "type": "chair_notes"}
    resp = FakeStreamResponse(body=b"d")
    with _IsolatedDownloadEnv() as env:
        (results, state), _ = _run_download_external([entry], lambda: resp)
        assert results[0][1].name == "RAN1#126 sched v04.docx"


class _OneshotCtx:
    """Wraps a FakeStreamResponse so it's usable as a context manager entry."""

    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self._resp

    def __exit__(self, *a):
        return self._resp.__exit__(*a)


def test_external_download_uses_browser_compatible_user_agent():
    url = "https://example.com/dir/schedule.docx"
    calls = []

    def fake_stream(method, request_url, **kwargs):
        calls.append(kwargs)
        return _OneshotCtx(FakeStreamResponse(body=b"docx"))

    with _IsolatedDownloadEnv() as env:
        with patch("downloader.httpx.stream", side_effect=fake_stream):
            download_external_files(
                [{"url": url, "type": "schedule"}],
                dest_dir=env.root,
            )

    assert calls[0]["headers"]["User-Agent"].startswith("Mozilla/5.0")


def test_external_download_reuses_matching_cached_file():
    import hashlib

    url = "https://example.com/dir/schedule.docx"
    body = b"cached-docx"
    filename = "schedule.docx"
    cached_state = {
        "files": {
            url: {
                "sha256": hashlib.sha256(body).hexdigest(),
                "filename": filename,
            }
        }
    }

    with _IsolatedDownloadEnv() as env:
        cached_path = env.root / filename
        cached_path.write_bytes(body)
        with patch(
            "downloader.httpx.stream",
            side_effect=AssertionError("cache hit must not download"),
        ):
            results, state = download_external_files(
                [{"url": url, "type": "schedule"}],
                dest_dir=env.root,
                state=cached_state,
            )

    assert results[0][1] == cached_path
    assert state == cached_state["files"]


class CheckExternalFilesTests(unittest.TestCase):
    def _patch_stream_bodies(self, bodies: dict[str, bytes], errors: dict[str, Exception] | None = None):
        """Patch httpx.stream: URL → body, or URL → raised exception."""
        errors = errors or {}

        def fake_stream(m, u, **k):
            if u in errors:
                raise errors[u]
            return _OneshotCtx(FakeStreamResponse(body=bodies[u]))

        return patch("downloader.httpx.stream", side_effect=fake_stream), {}

    def test_check_uses_browser_compatible_user_agent(self):
        url = "https://example.com/schedule.docx"
        calls = []

        def fake_stream(method, request_url, **kwargs):
            calls.append(kwargs)
            return _OneshotCtx(FakeStreamResponse(body=b"docx"))

        with patch("downloader.httpx.stream", side_effect=fake_stream):
            changed, _ = check_external_files(
                [{"url": url, "type": "schedule"}],
                state={"files": {}},
            )

        self.assertTrue(changed)
        self.assertTrue(calls[0]["headers"]["User-Agent"].startswith("Mozilla/5.0"))

    def test_check_stages_cache_miss_for_build_transfer(self):
        url = "https://example.com/schedule.docx"
        body = b"staged-docx"

        def fake_stream(method, request_url, **kwargs):
            return _OneshotCtx(
                FakeStreamResponse(
                    headers={
                        "content-disposition": 'attachment; filename="schedule.docx"',
                    },
                    body=body,
                )
            )

        with _IsolatedDownloadEnv() as env:
            with patch("downloader.httpx.stream", side_effect=fake_stream):
                changed, state = check_external_files(
                    [{"url": url, "type": "schedule"}],
                    state={"files": {}},
                    staging_dir=env.root,
                )

            self.assertTrue(changed)
            self.assertEqual((env.root / "schedule.docx").read_bytes(), body)
            self.assertEqual(state["files"][url]["filename"], "schedule.docx")

    def test_check_reuses_matching_cached_file_without_network(self):
        import hashlib

        url = "https://example.com/schedule.docx"
        body = b"cached-docx"
        cached_state = {
            "files": {
                url: {
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "filename": "schedule.docx",
                }
            }
        }

        with _IsolatedDownloadEnv() as env:
            (env.root / "schedule.docx").write_bytes(body)
            with patch(
                "downloader.httpx.stream",
                side_effect=AssertionError("cache hit must not download"),
            ):
                changed, state = check_external_files(
                    [{"url": url, "type": "schedule"}],
                    state=cached_state,
                    cache_dir=env.root,
                )

        self.assertFalse(changed)
        self.assertEqual(state, cached_state)

    def test_empty_list_noop(self):
        changed, state = check_external_files([])
        self.assertFalse(changed)
        self.assertEqual(state, {"files": {}})

    def test_matching_hash_unchanged(self):
        import hashlib
        url = "https://example.com/f?x=1"
        body = b"unchanged-content"
        state_in = {"files": {url: hashlib.sha256(body).hexdigest()}}
        p, _ = self._patch_stream_bodies({url: body})
        with p:
            changed, state_out = check_external_files([{"url": url, "type": "schedule"}], state=state_in)
        self.assertFalse(changed)
        self.assertEqual(state_out, state_in)

    def test_different_hash_changed(self):
        import hashlib
        url = "https://example.com/f?x=2"
        state_in = {"files": {url: hashlib.sha256(b"v1").hexdigest()}}
        p, _ = self._patch_stream_bodies({url: b"v2"})
        with p:
            changed, state_out = check_external_files([{"url": url, "type": "schedule"}], state=state_in)
        self.assertTrue(changed)
        self.assertEqual(state_out["files"][url], hashlib.sha256(b"v2").hexdigest())

    def test_same_hash_different_filename_changed(self):
        import hashlib

        url = "https://example.com/f?x=filename"
        body = b"unchanged-body"
        state_in = {
            "files": {
                url: {
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "filename": "RAN1#126 schedule - v01.docx",
                }
            }
        }

        def fake_stream(method, request_url, **kwargs):
            return _OneshotCtx(
                FakeStreamResponse(
                    headers={
                        "content-disposition": (
                            'attachment; filename="RAN1#126 schedule - v02.docx"'
                        )
                    },
                    body=body,
                )
            )

        with patch("downloader.httpx.stream", side_effect=fake_stream):
            changed, state_out = check_external_files(
                [{"url": url, "type": "schedule"}],
                state=state_in,
            )

        self.assertTrue(changed)
        self.assertEqual(
            state_out["files"][url]["filename"],
            "RAN1#126 schedule - v02.docx",
        )

    def test_same_url_and_body_different_routing_changed(self):
        import hashlib

        url = "https://example.com/f?x=routing"
        body = b"same-body"
        p, _ = self._patch_stream_bodies({url: body})
        state_in = {
            "files": {url: hashlib.sha256(body).hexdigest()},
            "config": [
                {
                    "url": url,
                    "type": "schedule",
                    "name": None,
                    "person_name": None,
                    "is_main": True,
                }
            ],
        }
        with p:
            changed, state_out = check_external_files(
                [{"url": url, "type": "chair_notes"}],
                state=state_in,
            )

        self.assertTrue(changed)
        self.assertEqual(state_out["config"][0]["type"], "chair_notes")

    def test_new_url_counts_as_changed(self):
        import hashlib
        url = "https://example.com/brand-new"
        p, _ = self._patch_stream_bodies({url: b"fresh"})
        with p:
            changed, state_out = check_external_files([{"url": url, "type": "chair_notes"}], state={"files": {}})
        self.assertTrue(changed)
        self.assertEqual(
            state_out,
            {
                "files": {
                    url: {
                        "sha256": hashlib.sha256(b"fresh").hexdigest(),
                        "filename": "brand-new",
                    }
                }
            },
        )

    def test_404_url_ignored_no_effect(self):
        import hashlib
        deleted = "https://example.com/missing"
        other = "https://example.com/ok"
        ok_body = b"okay"
        state_in = {"files": {
            deleted: hashlib.sha256(b"old-gone").hexdigest(),
            other: hashlib.sha256(ok_body).hexdigest(),
        }}
        p, _ = self._patch_stream_bodies(
            {other: ok_body},
            errors={deleted: httpx.HTTPStatusError(
                "HTTP 404",
                request=httpx.Request("GET", deleted),
                response=httpx.Response(404, request=httpx.Request("GET", deleted)),
            )},
        )
        with p:
            changed, state_out = check_external_files(
                [
                    {"url": deleted, "type": "schedule"},
                    {"url": other, "type": "schedule"},
                ],
                state=state_in,
            )
        # 404 is ignored: unchanged other URL keeps changed=False, and the
        # deleted URL drops out of the returned state (stale auto-removal).
        self.assertFalse(changed)
        self.assertNotIn(deleted, state_out["files"])
        self.assertEqual(state_out["files"][other], hashlib.sha256(ok_body).hexdigest())

    def test_transport_error_url_ignored_no_effect(self):
        import hashlib
        broken = "https://example.com/broken"
        state_in = {"files": {broken: hashlib.sha256(b"old").hexdigest()}}
        p, _ = self._patch_stream_bodies({}, errors={broken: httpx.ConnectError("boom")})
        with p:
            changed, state_out = check_external_files([{"url": broken, "type": "schedule"}], state=state_in)
        self.assertFalse(changed)
        self.assertEqual(state_out, {"files": {}})


def test_state_roundtrip(tmp_path):
    import hashlib
    p = tmp_path / ".extra_files_state.json"
    state = {
        "files": {
            "https://example.com/a": hashlib.sha256(b"abc").hexdigest()
        }
    }
    save_external_files_state(state, p)
    loaded = load_external_files_state(p)
    assert loaded == state


def test_load_missing_returns_empty(tmp_path):
    p = tmp_path / "nope.json"
    assert load_external_files_state(p) == {"files": {}}


def test_load_invalid_json_returns_empty(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json", encoding="utf-8")
    assert load_external_files_state(p) == {"files": {}}


def test_load_wrong_shape_returns_empty(tmp_path):
    p = tmp_path / "weird.json"
    p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert load_external_files_state(p) == {"files": {}}


def test_load_files_not_dict_returns_empty(tmp_path):
    p = tmp_path / "list_files.json"
    p.write_text(json.dumps({"files": [1, 2]}), encoding="utf-8")
    assert load_external_files_state(p) == {"files": {}}


def test_save_creates_parent_dirs(tmp_path):
    p = tmp_path / "nested" / "dir" / ".state.json"
    save_external_files_state({"files": {}}, p)
    assert p.exists()


if __name__ == "__main__":
    unittest.main()
