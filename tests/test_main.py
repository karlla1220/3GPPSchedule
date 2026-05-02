import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from main import _extract_meeting_name, main


class ExtractMeetingNameTests(unittest.TestCase):
    def test_extracts_basic_ran_meeting_name(self):
        self.assertEqual(
            _extract_meeting_name(Path("RAN1#124 online and offline schedules - v00.docx")),
            "RAN1#124",
        )

    def test_preserves_meeting_suffixes(self):
        self.assertEqual(
            _extract_meeting_name(Path("RAN1#124bis online and offline schedules - v00.docx")),
            "RAN1#124bis",
        )

    def test_falls_back_to_file_stem_when_no_meeting_name_is_found(self):
        self.assertEqual(
            _extract_meeting_name(Path("custom schedule name.docx")),
            "custom schedule name",
        )


class MainChairNotesLookupTests(unittest.TestCase):
    @patch("main.save_html", return_value="docs/index.html")
    @patch("main.fill_missing_groups", side_effect=lambda sessions: sessions)
    @patch("main.normalize_group_headers", side_effect=lambda sessions: sessions)
    @patch("main.parse_time_slots", return_value=[])
    @patch("main.collect_time_slot_data", return_value=[])
    @patch("main.build_room_list", return_value={})
    @patch("main.parse_docx", return_value=([], []))
    @patch("main.load_schedule_state", return_value={})
    @patch("main.find_chair_notes_docx", return_value=None)
    @patch("main.find_local_latest_agenda", return_value=None)
    @patch("main.download_latest_agenda", return_value=None)
    @patch("main.download_latest_chair_notes", return_value=None)
    @patch("main.load_config", return_value={
        "meeting_sync": None,
        "meeting_specific": [],
        "inbox_urls": ["https://example.com/legacy/Inbox/", "https://example.com/next/Inbox/"],
        "agenda_urls": [],
        "extra_folders": [{"url": "https://example.com/custom/Chair_notes/", "name": "Chair_notes"}],
    })
    def test_passes_configured_sources_to_chair_notes_download(
        self,
        mock_load_config,
        mock_download_latest_chair_notes,
        mock_download_latest_agenda,
        mock_find_local_latest_agenda,
        mock_find_chair_notes_docx,
        mock_load_schedule_state,
        mock_parse_docx,
        mock_build_room_list,
        mock_collect_time_slot_data,
        mock_parse_time_slots,
        mock_normalize_group_headers,
        mock_fill_missing_groups,
        mock_save_html,
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            docx_path = Path(tmpdir) / "custom schedule.docx"
            docx_path.write_text("placeholder")

            args = argparse.Namespace(
                local=str(docx_path),
                no_download=False,
                output="docs/index.html",
            )

            with patch("argparse.ArgumentParser.parse_args", return_value=args):
                with patch.dict(
                    os.environ,
                    {
                        "SCHEDULE_CONTACT_NAME": "Tester",
                        "SCHEDULE_CONTACT_EMAIL": "tester@example.com",
                    },
                    clear=False,
                ):
                    main()

        mock_download_latest_chair_notes.assert_called_once_with(
            docx_path.parent,
            urls=["https://example.com/legacy/Inbox/", "https://example.com/next/Inbox/"],
            extra_folders=[{"url": "https://example.com/custom/Chair_notes/", "name": "Chair_notes"}],
        )


if __name__ == "__main__":
    unittest.main()
