import argparse
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import main as main_module
from main import _agenda_state_for_save, _extract_meeting_name, main


def test_pending_timezone_cache_is_reused_while_reference_is_still_missing():
    state = {
        "meeting_id": "ran1#126",
        "timezone": "UTC",
        "timezone_status": "pending_timezone_ref",
        "timezone_ref": None,
    }

    assert main_module._timezone_cache_is_current(state, "ran1#126", None)


def test_late_timezone_reference_invalidates_pending_cache():
    state = {
        "meeting_id": "ran1#126",
        "timezone": "UTC",
        "timezone_status": "pending_timezone_ref",
        "timezone_ref": None,
    }
    new_ref = {
        "type": "chair_notes",
        "name": "Chair notes RAN1#126_v00.docm",
        "uploaded_at": "2026-08-23T14:29:00",
    }

    assert not main_module._timezone_cache_is_current(
        state,
        "ran1#126",
        new_ref,
    )


def test_resolved_timezone_cache_requires_same_reference():
    old_ref = {
        "type": "chair_notes",
        "name": "Chair notes RAN1#126_v00.docm",
        "uploaded_at": "2026-08-23T14:29:00",
    }
    state = {
        "meeting_id": "ran1#126",
        "timezone": "Europe/Amsterdam",
        "timezone_status": "resolved",
        "timezone_ref": old_ref,
    }

    assert main_module._timezone_cache_is_current(state, "ran1#126", old_ref)
    assert not main_module._timezone_cache_is_current(
        state,
        "ran1#126",
        {**old_ref, "name": "Chair notes RAN1#126_v01.docm"},
    )


def test_legacy_timezone_cache_without_reference_is_rechecked():
    state = {
        "meeting_id": "ran1#126",
        "timezone": "Asia/Shanghai",
    }

    assert not main_module._timezone_cache_is_current(state, "ran1#126", None)


def test_cached_remote_agenda_reference_survives_listing_failure(tmp_path):
    agenda_path = tmp_path / "RAN1#126 agenda.docx"
    agenda_path.write_bytes(b"cached-remote-agenda")
    cached_ref = {
        "type": "agenda",
        "name": agenda_path.name,
        "uploaded_at": "2026-08-20T08:00:00",
        "url": "https://example.org/agenda.docx",
        "source_url": "https://example.org/Agenda/",
    }

    result = main_module._agenda_timezone_reference(
        agenda_info=None,
        agenda_path=agenda_path,
        previous_ref=cached_ref,
    )

    assert result == cached_ref


def test_cached_remote_agenda_reference_survives_without_local_file():
    cached_ref = {
        "type": "agenda",
        "name": "RAN1#126 agenda.docx",
        "uploaded_at": "2026-08-20T08:00:00",
        "url": "https://example.org/agenda.docx",
    }

    result = main_module._agenda_timezone_reference(
        agenda_info=None,
        agenda_path=None,
        previous_ref=cached_ref,
    )

    assert result == cached_ref


def test_cached_agenda_is_preserved_only_for_same_meeting():
    previous_state = {
        "meeting_id": "ran1#126",
        "timezone_status": "resolved",
        "timezone_ref": {
            "type": "agenda",
            "name": "RAN1#126 agenda.docx",
        },
    }

    assert main_module._can_preserve_cached_agenda(
        previous_state,
        current_meeting_id="ran1#126",
        agenda_info_name="",
    )
    assert not main_module._can_preserve_cached_agenda(
        previous_state,
        current_meeting_id="ran1#127",
        agenda_info_name="",
    )


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


class AgendaStateForSaveTests(unittest.TestCase):
    def test_uses_description_json_when_remote_agenda_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "agenda_item_description.json"
            json_path.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-05-19T23:24:55+00:00",
                        "source_type": "agenda_docx",
                        "source_file": "R1-2601750_Draft agenda.docx",
                        "source_url": "https://example.com/Agenda/R1-2601750.zip",
                        "source_uploaded_at": "2026-05-18T08:30:00",
                        "source_agenda_file": "R1-2601750.zip",
                    }
                )
            )

            state = _agenda_state_for_save(None, None, json_path)

        self.assertEqual(state["name"], "R1-2601750.zip")
        self.assertEqual(state["document_file"], "R1-2601750_Draft agenda.docx")
        self.assertEqual(state["description_json"], str(json_path))
        self.assertEqual(state["description_source_agenda_file"], "R1-2601750.zip")


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
    @patch(
        "main.get_latest_chair_notes_info",
        return_value={
            "name": "chair notes.docm",
            "url": "https://example.com/chair-notes.docm",
            "uploaded_at": None,
        },
    )
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
        mock_get_latest_chair_notes_info,
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
                rebuild_slots=False,
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
            preferred_meeting_id=None,
            latest_info=mock_get_latest_chair_notes_info.return_value,
            force=True,
        )


class MainExtraFilesWiringTests(unittest.TestCase):
    """Wiring assertions: extra_files -> download -> schedule / chair notes."""

    def _enter_common(self, extra_files: list, no_download: bool):
        """Enter the common patch stack shared by all wiring tests."""
        import contextlib

        stack = contextlib.ExitStack()
        args = argparse.Namespace(
            local=None,
            no_download=no_download,
            output="docs/index.html",
            rebuild_slots=False,
        )
        stack.enter_context(
            patch("argparse.ArgumentParser.parse_args", return_value=args)
        )
        stack.enter_context(
            patch.dict(
                os.environ,
                {"SCHEDULE_CONTACT_NAME": "Tester", "SCHEDULE_CONTACT_EMAIL": "t@e.com"},
                clear=False,
            )
        )
        stack.enter_context(
            patch(
                "main.load_config",
                return_value={
                    "meeting_sync": None,
                    "meeting_specific": [],
                    "inbox_urls": ["https://example.com/Inbox/"],
                    "agenda_urls": [],
                    "extra_folders": [],
                    "extra_files": extra_files,
                },
            )
        )
        # CRITICAL: return {} so tz detection runs instead of reusing a cached tz.
        stack.enter_context(patch("main.load_schedule_state", return_value={}))
        stack.enter_context(patch("main.parse_docx", return_value=([], [])))
        stack.enter_context(patch("main.build_room_list", return_value={}))
        stack.enter_context(patch("main.collect_time_slot_data", return_value=[]))
        stack.enter_context(patch("main.parse_time_slots", return_value=[]))
        stack.enter_context(
            patch("main.normalize_group_headers", side_effect=lambda s: s)
        )
        stack.enter_context(patch("main.fill_missing_groups", side_effect=lambda s: s))
        stack.enter_context(patch("main.save_schedule_state", return_value=None))
        stack.enter_context(patch("main.save_html", return_value="docs/index.html"))
        stack.enter_context(patch("main.find_local_latest_agenda", return_value=None))
        stack.enter_context(patch("main.find_local_vice_chair_schedules", return_value={}))
        # Defensive: keep local/FTP local-schedule fallbacks inert.
        stack.enter_context(patch("main.find_local_latest_schedule", return_value=None))
        stack.enter_context(patch("main.download_latest_schedule", return_value=None))
        stack.enter_context(
            patch("main.get_latest_chair_notes_info", return_value=None)
        )
        return stack

    def test_download_path_schedule_entry_merged_into_local_sources(self):
        """Download path: schedule entry becomes a local ScheduleSource fed to
        discover_schedule_sources; external state is persisted."""
        from models import ScheduleSource
        from downloader import EXTRA_FILES_DIR

        url = "https://x/e.docx"
        with tempfile.TemporaryDirectory() as tmpdir:
            docx = Path(tmpdir) / "RAN1#126 online and offline schedules - v01.docx"
            docx.write_text("placeholder")
            entries = [{"url": url, "type": "schedule"}]
            entry_obj = dict(entries[0], person_name=None, is_main=True)
            # main builds the merged source with folder_name=EXTRA_FILES_DIR.name,
            # so this must match for the dataclass equality in assertIn below.
            extra_source = ScheduleSource(
                folder_name=EXTRA_FILES_DIR.name,
                person_name=None,
                is_main=True,
                file_info={"name": docx.name, "url": url, "uploaded_at": None},
                local_path=docx,
            )

            with self._enter_common(entries, no_download=False) as stack:
                stack.enter_context(
                    patch("main.find_local_schedule_sources", return_value=([], None))
                )
                stack.enter_context(patch("main.find_chair_notes_docx", return_value=None))
                stack.enter_context(
                    patch("main.download_latest_chair_notes", return_value=None)
                )
                stack.enter_context(
                    patch("main.extract_meeting_location", return_value="Malta, Malta")
                )
                stack.enter_context(
                    patch("main.get_timezone_from_location", return_value="Europe/Malta")
                )
                mock_dl = stack.enter_context(
                    patch(
                        "main.download_external_files",
                        return_value=([(entry_obj, docx)], {url: "deadbeef"}),
                    )
                )
                mock_save = stack.enter_context(patch("main.save_external_files_state"))
                mock_disc = stack.enter_context(
                    patch("main.discover_schedule_sources", return_value=[extra_source])
                )
                stack.enter_context(
                    patch("main.download_all_schedules", return_value=(docx, {}))
                )

                main()

                mock_dl.assert_called_once_with(entries)
                mock_save.assert_called_once_with(
                    {
                        "files": {url: "deadbeef"},
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
                )
                self.assertIn(
                    extra_source,
                    mock_disc.call_args.kwargs["local_schedule_sources"],
                )

    def test_download_path_chair_notes_entry_used_in_tz_block_before_ftp(self):
        """TZ block: an extra_files chair_notes entry is used for location
        extraction, and the FTP chair-notes download is skipped."""
        from models import ScheduleSource

        url = "https://x/e.docx"
        with tempfile.TemporaryDirectory() as tmpdir:
            docx = Path(tmpdir) / "RAN1#126 online and offline schedules - v01.docx"
            docx.write_text("placeholder")
            chair_docx = Path(tmpdir) / "chair-notes.docx"
            chair_docx.write_text("placeholder")

            entries = [{"url": url, "type": "chair_notes"}]
            main_src = ScheduleSource(
                folder_name="Chair_notes",
                person_name=None,
                is_main=True,
                file_info={
                    "name": docx.name,
                    "url": "https://ftpx/m.docx",
                    "uploaded_at": None,
                },
                local_path=None,
            )

            with self._enter_common(entries, no_download=False) as stack:
                stack.enter_context(
                    patch("main.find_local_schedule_sources", return_value=([], None))
                )
                stack.enter_context(
                    patch(
                        "main.download_external_files",
                        return_value=(
                            [(dict(entries[0], is_main=None), chair_docx)],
                            {url: "abc"},
                        ),
                    )
                )
                stack.enter_context(patch("main.save_external_files_state"))
                stack.enter_context(
                    patch("main.discover_schedule_sources", return_value=[main_src])
                )
                stack.enter_context(
                    patch("main.download_all_schedules", return_value=(docx, {}))
                )
                stack.enter_context(patch("main.find_chair_notes_docx", return_value=None))
                mock_loc = stack.enter_context(
                    patch("main.extract_meeting_location", return_value="Malta, Malta")
                )
                stack.enter_context(
                    patch("main.get_timezone_from_location", return_value="Europe/Malta")
                )
                mock_ftp = stack.enter_context(patch("main.download_latest_chair_notes"))

                main()

                mock_loc.assert_called_once_with(chair_docx)
                mock_ftp.assert_not_called()

    def test_late_remote_chair_notes_resolves_pending_timezone_and_saves_reference(self):
        from models import ScheduleSource

        chair_info = {
            "name": "Chair notes RAN1#126_v00.docm",
            "uploaded_at": datetime(2026, 8, 23, 14, 29),
            "url": "https://example.org/Chair%20notes%20RAN1%23126_v00.docm",
            "source_url": "https://example.org/Chair_notes",
        }
        expected_ref = {
            "type": "chair_notes",
            "name": "Chair notes RAN1#126_v00.docm",
            "uploaded_at": "2026-08-23T14:29:00",
            "url": chair_info["url"],
            "source_url": chair_info["source_url"],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            schedule_path = Path(tmpdir) / "RAN1#126 schedule - v01.docx"
            schedule_path.write_text("placeholder")
            chair_path = Path(tmpdir) / chair_info["name"]
            chair_path.write_text("placeholder")
            main_source = ScheduleSource(
                folder_name="Chair_notes",
                person_name=None,
                is_main=True,
                file_info={
                    "name": schedule_path.name,
                    "url": "https://example.org/schedule.docx",
                    "uploaded_at": None,
                },
                local_path=None,
            )

            with self._enter_common([], no_download=False) as stack:
                stack.enter_context(
                    patch(
                        "main.load_schedule_state",
                        return_value={
                            "meeting_id": "ran1#126",
                            "timezone": "UTC",
                            "timezone_status": "pending_timezone_ref",
                            "timezone_ref": None,
                        },
                    )
                )
                stack.enter_context(
                    patch("main.find_local_schedule_sources", return_value=([], None))
                )
                stack.enter_context(
                    patch("main.discover_schedule_sources", return_value=[main_source])
                )
                stack.enter_context(
                    patch(
                        "main.download_all_schedules",
                        return_value=(schedule_path, {}),
                    )
                )
                stack.enter_context(patch("main.find_chair_notes_docx", return_value=None))
                stack.enter_context(
                    patch(
                        "main.get_latest_chair_notes_info",
                        return_value=chair_info,
                        create=True,
                    )
                )
                stack.enter_context(
                    patch("main.download_latest_chair_notes", return_value=chair_path)
                )
                stack.enter_context(
                    patch(
                        "main.extract_meeting_location",
                        return_value="Maastricht, NL, Aug 24th-28th, 2026",
                    )
                )
                stack.enter_context(
                    patch(
                        "main.get_timezone_from_location",
                        return_value="Europe/Amsterdam",
                    )
                )
                stack.enter_context(patch("main.save_external_files_state"))
                mock_save = stack.enter_context(patch("main.save_schedule_state"))

                main()

                self.assertEqual(mock_save.call_args.kwargs["timezone"], "Europe/Amsterdam")
                self.assertEqual(mock_save.call_args.kwargs["timezone_status"], "resolved")
                self.assertEqual(mock_save.call_args.kwargs["timezone_ref"], expected_ref)

    def test_no_download_scans_extra_files_dir(self):
        """no_download: the REAL scanner runs against a temp EXTRA_FILES_DIR;
        the scan-picked schedule becomes the main doc and the chair notes in
        the same dir feed the tz block."""
        from downloader import find_local_schedule_sources as real_find

        with tempfile.TemporaryDirectory() as tmpdir:
            extra = Path(tmpdir) / "extra_files"
            extra.mkdir()
            sched = extra / "RAN1#126 online and offline schedules - v02.docx"
            chair = extra / "Chair notes RAN1#126_v01.docx"
            sched.write_text("placeholder")
            chair.write_text("placeholder")

            with self._enter_common([], no_download=True) as stack:
                stack.enter_context(patch("main.EXTRA_FILES_DIR", extra))
                stack.enter_context(
                    patch(
                        "main.find_local_schedule_sources",
                        side_effect=lambda ref_dir=None, preferred_meeting_id=None: (
                            ([], None)
                            if ref_dir is None
                            else real_find(ref_dir, preferred_meeting_id)
                        ),
                    )
                )
                # NOTE: main.find_chair_notes_docx is NOT patched, so the REAL
                # scanner runs against docx_path.parent (== extra) and finds chair.
                mock_loc = stack.enter_context(
                    patch("main.extract_meeting_location", return_value="Malta, Malta")
                )
                stack.enter_context(
                    patch("main.get_timezone_from_location", return_value="Europe/Malta")
                )
                mock_ftp = stack.enter_context(patch("main.download_latest_chair_notes"))
                mock_parse = stack.enter_context(
                    patch("main.parse_docx", return_value=([], []))
                )

                main()

                self.assertEqual(mock_parse.call_args[0][0], sched)
                self.assertEqual(mock_loc.call_args[0][0], chair)
                mock_ftp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
