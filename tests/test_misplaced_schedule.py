"""A vice chair's schedule uploaded to Chair_notes by mistake, and the ignore list."""

import json
from datetime import datetime
from unittest.mock import patch

import pytest

from shared import notices
from working_groups.ran1 import config as ran1_config
from working_groups.ran1.downloader import discover_schedule_sources

INBOX = "https://example.org/RAN1/Inbox"
MAIN = "RAN1#127 online and offline schedules - v02.docx"
SOROUR = "RAN1#127 Sorour sessions online and offline schedules - v03.docx"


def _f(name, uploaded_at):
    return {"name": name, "url": f"{INBOX}/x/{name}", "uploaded_at": uploaded_at}


@pytest.fixture(autouse=True)
def notice_file(tmp_path, monkeypatch):
    monkeypatch.setattr(notices, "PATH", tmp_path / "notices.json")
    return tmp_path / "notices.json"


def _discover(listings, **kwargs):
    folders = [{"name": name, "url": f"{INBOX}/{name}", "uploaded_at": None} for name in listings]
    with (
        patch("working_groups.ran1.downloader.list_inbox_subfolders", return_value=folders),
        patch("working_groups.ran1.downloader.list_remote_files",
              side_effect=lambda url: list(listings.get(url.rsplit("/", 1)[-1], []))),
    ):
        sources = discover_schedule_sources(urls=[INBOX], preferred_meeting_id="ran1#127", **kwargs)
    return {s.folder_name: (s.file_info["name"], s.is_main) for s in sources}


def test_a_vice_chairs_schedule_in_chair_notes_is_theirs_not_main(notice_file):
    # RAN1#127, 18 Nov: Sorour's v03 was uploaded to Sorour_notes and, by
    # mistake, to Chair_notes, where as the highest version it became Main.
    found = _discover({
        "Chair_notes": [_f(MAIN, datetime(2026, 11, 17, 16, 45)), _f(SOROUR, datetime(2026, 11, 18, 14, 5))],
        "Sorour_notes": [_f("RAN1#127 Sorour sessions online and offline schedules - v02.docx",
                            datetime(2026, 11, 17, 16, 53)), _f(SOROUR, datetime(2026, 11, 18, 11, 40))],
    })
    assert found == {"Chair_notes": (MAIN, True), "Sorour_notes": (SOROUR, False)}
    notice = json.loads(notice_file.read_text())
    assert [n["kind"] for n in notice] == ["misplaced-schedule"]
    assert f"Chair_notes/{SOROUR}" in notice[0]["lines"][0]


def test_a_misplaced_newer_version_still_counts_for_its_owner():
    found = _discover({
        "Chair_notes": [_f(MAIN, datetime(2026, 11, 17, 16, 45)), _f(SOROUR, datetime(2026, 11, 18, 14, 5))],
        "Sorour_notes": [_f("RAN1#127 Sorour sessions online and offline schedules - v02.docx",
                            datetime(2026, 11, 17, 16, 53))],
    })
    assert found == {"Chair_notes": (MAIN, True), "Sorour_notes": (SOROUR, False)}


def test_a_vice_chairs_copy_of_main_does_not_take_main():
    # Vice chairs keep copies of Main under Main's name; the name of a person decides, not a shared file.
    found = _discover({
        "Chair_notes": [_f(MAIN, datetime(2026, 11, 17, 16, 45))],
        "Hiroki_notes": [_f(MAIN, datetime(2026, 11, 17, 18, 56))],
    })
    assert found["Chair_notes"] == (MAIN, True)


def test_a_name_of_no_vice_chair_stays_main():
    online = "RAN1#127 online sessions schedules - v03.docx"
    found = _discover({
        "Chair_notes": [_f(MAIN, datetime(2026, 11, 17, 16, 45)), _f(online, datetime(2026, 11, 18, 9, 0))],
        "Sorour_notes": [_f("RAN1#127 Sorour sessions online and offline schedules - v02.docx",
                            datetime(2026, 11, 17, 16, 53))],
    })
    assert found["Chair_notes"] == (online, True)


def test_ignored_files_are_never_read():
    wrong = "RAN1#127 online and offline schedules - v09.docx"
    listings = {"Chair_notes": [_f(MAIN, datetime(2026, 11, 17, 16, 45)), _f(wrong, datetime(2026, 11, 18, 9, 0))]}
    assert _discover(listings)["Chair_notes"] == (wrong, True)
    assert _discover(listings, ignored_files={"Chair_notes": [wrong]})["Chair_notes"] == (MAIN, True)
    # A name is ignored only in its folder.
    assert _discover(listings, ignored_files={"Sorour_notes": [wrong]})["Chair_notes"] == (wrong, True)


def test_config_reads_ignored_files(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"ignored_files": {"Chair_notes": [SOROUR], "Hiroki_notes": []}}))
    assert ran1_config.load_config(path)["ignored_files"] == {"Chair_notes": [SOROUR]}
    path.write_text(json.dumps({"ignored_files": {"Chair_notes": SOROUR}}))
    assert ran1_config.load_config(path)["ignored_files"] == {}
    assert ran1_config.load_config(tmp_path / "none.json")["ignored_files"] == {}
