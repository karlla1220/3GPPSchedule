"""A released document supersedes every draft of the same meeting.

The chair restarts the version number when the "Draft" prefix is dropped, so
the order is meeting, then released over draft, then version.  The listing
is the one seen on 2026-10-11 in ``TSGR1_126b/Inbox/Chair_notes``.
"""

from datetime import datetime
from unittest.mock import patch

import pytest

import working_groups.ran1.agreements as agreements
import working_groups.ran1.check_update as check_update
from test_check_update import _run_check
from working_groups.ran1.downloader import (
    _dedup_sources,
    _eom_number,
    _is_draft_name,
    discover_schedule_sources,
    find_latest_chair_notes,
    find_latest_schedule,
    find_local_latest_schedule,
    find_local_schedule_sources,
    find_local_vice_chair_schedules,
    get_latest_chair_notes_info,
)
from working_groups.ran1.models import ScheduleSource
from working_groups.ran1.parser import find_chair_notes_docx

MEETING = "ran1#126bis"
DRAFT_NOTES = "Draft Chair notes RAN1#126bis_v01.docx"
FINAL_NOTES = "Chair notes RAN1#126bis_v00.docx"
NEXT_NOTES = "Chair notes RAN1#126bis_v01.docx"
DRAFT_SCHEDULES = [
    f"Draft RAN1#126b online and offline schedules - v0{n}.docx" for n in (1, 2, 3)
]
FINAL_SCHEDULE = "RAN1#126b online and offline schedules - v00.docx"
DRAFT_UPLOAD = datetime(2026, 10, 7, 9, 8)
INBOX = "https://www.3gpp.org/ftp/tsg_ran/WG1_RL1/TSGR1_126b/Inbox"


def _f(name, uploaded_at=None, url=""):
    return {"name": name, "url": url, "uploaded_at": uploaded_at}


LISTING = [
    _f(DRAFT_NOTES, DRAFT_UPLOAD),
    *[_f(name, DRAFT_UPLOAD) for name in DRAFT_SCHEDULES],
    _f(FINAL_SCHEDULE, datetime(2026, 10, 10, 6, 54)),
    _f(FINAL_NOTES, datetime(2026, 10, 10, 6, 55)),
]


@pytest.mark.parametrize("preferred", [None, MEETING])
def test_observed_listing_selects_the_released_files(preferred):
    assert find_latest_chair_notes(LISTING, preferred_meeting_id=preferred)["name"] == FINAL_NOTES
    assert find_latest_schedule(LISTING, preferred_meeting_id=preferred)["name"] == FINAL_SCHEDULE


def test_released_files_win_without_upload_times():
    listing = [_f(file["name"]) for file in LISTING]
    assert find_latest_chair_notes(listing)["name"] == FINAL_NOTES
    assert find_latest_schedule(listing)["name"] == FINAL_SCHEDULE
    # An unversioned released file still beats a versioned draft.
    unversioned = "Chair notes RAN1#126bis.docx"
    assert find_latest_chair_notes([_f(DRAFT_NOTES), _f(unversioned)])["name"] == unversioned


@pytest.mark.parametrize("draft_upload", [
    DRAFT_UPLOAD,
    datetime(2026, 10, 13, 9, 0),   # A draft uploaded again after the release.
])
def test_released_v01_wins_the_version_tie_with_draft_v01(draft_upload):
    listing = [
        _f(DRAFT_NOTES, draft_upload),
        _f(FINAL_NOTES, datetime(2026, 10, 10, 6, 55)),
        _f(NEXT_NOTES, datetime(2026, 10, 12, 11, 0)),
    ]
    assert find_latest_chair_notes(listing, preferred_meeting_id=MEETING)["name"] == NEXT_NOTES
    assert find_latest_chair_notes([_f(f["name"]) for f in listing])["name"] == NEXT_NOTES


def test_a_later_draft_with_a_higher_version_does_not_replace_the_release():
    listing = LISTING + [_f("Draft Chair notes RAN1#126bis_v05.docx", datetime(2026, 10, 12, 9, 0))]
    assert find_latest_chair_notes(listing, preferred_meeting_id=MEETING)["name"] == FINAL_NOTES


def test_version_and_upload_time_still_order_one_class():
    drafts = [file for file in LISTING if _is_draft_name(file["name"])]
    assert find_latest_schedule(drafts)["name"] == DRAFT_SCHEDULES[-1]
    assert find_latest_chair_notes(drafts)["name"] == DRAFT_NOTES
    released = [_f(NEXT_NOTES, datetime(2026, 10, 12, 11, 0)), _f(FINAL_NOTES, datetime(2026, 10, 13, 8, 0))]
    assert find_latest_chair_notes(released)["name"] == NEXT_NOTES
    same_version = [_f("Chair notes RAN1#126bis_v01.docx", datetime(2026, 10, 12, 11, 0)),
                    _f("Chair_notes RAN1#126bis_v01.docx", datetime(2026, 10, 12, 12, 0))]
    assert find_latest_chair_notes(same_version)["name"] == "Chair_notes RAN1#126bis_v01.docx"


def test_the_meeting_is_compared_before_the_draft_marker():
    listing = [
        _f("Chair notes RAN1#126_v13.docx", datetime(2026, 8, 28, 9, 52)),
        _f("RAN1#126 online and offline schedules - v03.docx", datetime(2026, 8, 28, 8, 41)),
        _f("Draft Chair notes RAN1#126bis_v00.docx", datetime(2026, 10, 4, 8, 57)),
        _f("Draft RAN1#126b online and offline schedules - v00.docx", datetime(2026, 10, 4, 8, 57)),
    ]
    assert find_latest_chair_notes(listing)["name"] == "Draft Chair notes RAN1#126bis_v00.docx"
    assert find_latest_schedule(listing)["name"] == "Draft RAN1#126b online and offline schedules - v00.docx"


@pytest.mark.parametrize("name, draft", [
    (DRAFT_NOTES, True),
    ("DRAFT_RAN1#126b schedules_v01.docx", True),
    ("R1-2601750_Draft agenda for RAN1#125_v01.docx", True),
    (FINAL_NOTES, False),
    ("RAN1#126b schedule for drafting sessions_v01.docx", False),
    ("Redrafted RAN1#126b schedules_v01.docx", False),
])
def test_draft_is_a_whole_word_in_the_file_name(name, draft):
    assert _is_draft_name(name) is draft


def test_released_note_wins_across_inbox_locations():
    """A mirror that still lists only the draft, with a later copy time."""
    def listing(url):
        if "sync" in url:
            return [_f(DRAFT_NOTES, datetime(2026, 10, 11, 1, 0))] if url.endswith("Chair_notes") else []
        return LISTING if url.endswith("Chair_notes") else []
    with patch("working_groups.ran1.downloader.list_remote_files", side_effect=listing):
        result = get_latest_chair_notes_info(
            urls=["https://example.org/sync/Inbox/", INBOX + "/"],
            preferred_meeting_id=MEETING, strict=True)
    assert result["name"] == FINAL_NOTES
    assert result["source_url"] == INBOX + "/Chair_notes"


def test_released_schedule_wins_across_inboxes():
    def remote(name, uploaded_at):
        return ScheduleSource(folder_name="Chair_notes", person_name=None, is_main=True,
                              file_info=_f(name, uploaded_at))
    # Deduplication clears is_main on the loser, so every case builds its own sources.
    def main_of(*sources):
        return [(s.file_info["name"], s.file_info["uploaded_at"]) for s in _dedup_sources(list(sources)) if s.is_main]
    mirror_copy = datetime(2026, 10, 11, 1, 0)
    released = (FINAL_SCHEDULE, datetime(2026, 10, 10, 6, 54))
    assert main_of(remote(DRAFT_SCHEDULES[-1], mirror_copy), remote(*released)) == [released]
    assert main_of(remote(*released), remote(DRAFT_SCHEDULES[-1], mirror_copy)) == [released]
    # Within one class the later upload still wins.
    assert main_of(remote(DRAFT_SCHEDULES[-1], DRAFT_UPLOAD), remote(DRAFT_SCHEDULES[-1], mirror_copy)) == [
        (DRAFT_SCHEDULES[-1], mirror_copy)]


def test_discovery_reports_the_released_main_and_vice_chair_schedules():
    hiroki_draft = "Draft RAN1#126bis schedule for Hiroki Adhoc2 sessions_v02.docx"
    hiroki_final = "RAN1#126bis schedule for Hiroki Adhoc2 sessions_v00.docx"
    folders = [{"name": name, "url": f"{INBOX}/{name}", "uploaded_at": None}
               for name in ("Chair_notes", "Hiroki_notes")]

    def listing(url):
        if url.endswith("Chair_notes"):
            return LISTING
        if url.endswith("Hiroki_notes"):
            return [_f(hiroki_draft, DRAFT_UPLOAD), _f(hiroki_final, datetime(2026, 10, 9, 0, 29))]
        return []
    with (
        patch("working_groups.ran1.downloader.list_inbox_subfolders", return_value=folders),
        patch("working_groups.ran1.downloader.list_remote_files", side_effect=listing),
    ):
        sources = discover_schedule_sources(urls=[INBOX], preferred_meeting_id=MEETING)
    assert {s.folder_name: s.file_info["name"] for s in sources} == {
        "Chair_notes": FINAL_SCHEDULE, "Hiroki_notes": hiroki_final}


@pytest.mark.parametrize("names, expected", [
    ([DRAFT_NOTES, FINAL_NOTES], FINAL_NOTES),
    ([DRAFT_NOTES, FINAL_NOTES, NEXT_NOTES], NEXT_NOTES),
    ([DRAFT_NOTES, NEXT_NOTES], NEXT_NOTES),      # Same version: the name must not decide.
    ([DRAFT_NOTES, "Draft Chair notes RAN1#126bis_v00.docx"], DRAFT_NOTES),
])
def test_local_chair_notes_follow_the_remote_order(tmp_path, monkeypatch, names, expected):
    cache = tmp_path / "downloads" / "ran1" / "Chair_notes"
    cache.mkdir(parents=True)
    for name in names:
        (cache / name).write_bytes(b"note")
    assert find_chair_notes_docx(cache, meeting_id=MEETING).name == expected
    # The offline build reads the same download cache.
    monkeypatch.chdir(tmp_path)
    assert agreements.local_note_path({}, MEETING, offline=True).name == expected
    remote = [_f(name, datetime(2026, 10, 7 + index, 9, 0)) for index, name in enumerate(names)]
    assert find_latest_chair_notes(remote, preferred_meeting_id=MEETING)["name"] == expected


def test_local_schedules_follow_the_remote_order(tmp_path):
    chair = tmp_path / "Chair_notes"
    hiroki = tmp_path / "Hiroki_notes"
    hiroki_final = "RAN1#126bis schedule for Hiroki Adhoc2 sessions_v00.docx"
    for folder, names in ((chair, [*DRAFT_SCHEDULES, FINAL_SCHEDULE]),
                          (hiroki, ["Draft RAN1#126bis schedule for Hiroki Adhoc2 sessions_v02.docx", hiroki_final])):
        folder.mkdir()
        for name in names:
            (folder / name).write_bytes(b"schedule")
    assert find_local_latest_schedule(chair).name == FINAL_SCHEDULE
    assert find_local_vice_chair_schedules(tmp_path, meeting_id=MEETING) == {"Hiroki": hiroki / hiroki_final}
    sources, chosen = find_local_schedule_sources(chair, preferred_meeting_id=MEETING)
    assert chosen.name == FINAL_SCHEDULE
    assert _dedup_sources(sources)[0].local_path.name == FINAL_SCHEDULE


def _ref(name, uploaded_at):
    return {"type": "chair_notes", "name": name, "uploaded_at": uploaded_at,
            "url": f"{INBOX}/Chair_notes/{name}", "source_url": f"{INBOX}/Chair_notes"}


def test_released_v00_after_draft_v01_is_not_a_downgrade():
    draft = _ref(DRAFT_NOTES, "2026-10-07T09:08:00")
    released = _ref(FINAL_NOTES, "2026-10-10T06:55:00")
    assert not check_update._is_chair_notes_downgrade(released, draft)
    assert check_update._is_chair_notes_downgrade(draft, released)
    assert check_update._is_chair_notes_downgrade(released, _ref(NEXT_NOTES, "2026-10-12T11:00:00"))


@pytest.mark.parametrize("built_from, changed", [(DRAFT_NOTES, "true"), (FINAL_NOTES, "false")])
def test_check_job_selects_the_note_the_build_publishes(built_from, changed):
    """The check lists the folder with the build's function, then compares names."""
    uploads = {DRAFT_NOTES: "2026-10-07T09:08:00", FINAL_NOTES: "2026-10-10T06:55:00"}
    listing = [_f(f["name"], f["uploaded_at"], f"{INBOX}/Chair_notes/{f['name']}") for f in LISTING]
    listed = {**find_latest_chair_notes(listing, preferred_meeting_id=MEETING),
              "source_url": f"{INBOX}/Chair_notes"}
    listed["uploaded_at"] = listed["uploaded_at"].isoformat()
    state = {
        "files": [], "meeting_id": MEETING, "local_refs": {},
        "timezone": "Asia/Seoul", "timezone_status": "resolved",
        "timezone_ref": _ref(built_from, uploads[built_from]),
        "agreements_ref": {"name": built_from, "url": f"{INBOX}/Chair_notes/{built_from}",
                           "sha256": built_from},
    }
    with (
        patch.object(check_update, "lookup_timezone_reference", return_value=None),
        patch.object(agreements.remote_files, "fetch_file",
                     side_effect=lambda _, url: (b"", {"sha256": url.rsplit("/", 1)[-1]})),
    ):
        outputs, _ = _run_check(state=state, local_refs={}, remote=[], chair_notes_info=listed)
    assert outputs == [("changed", changed)]


# The end-of-meeting notes follow the last numbered version and count on their
# own.  Listings seen on 2026-10-19 in ``TSGR1_*/Inbox/Chair_notes``.
EOM_NOTES = "Chair notes RAN1#126_eom.docx"
EOM_LISTING = [
    _f("Chair notes RAN1#126_v14.docx", datetime(2026, 8, 29, 9, 0)),
    _f("Chair notes RAN1#126_v15.docx", datetime(2026, 8, 29, 16, 0)),
    _f(EOM_NOTES, datetime(2026, 9, 3, 3, 36)),
]


@pytest.mark.parametrize("preferred", [None, "ran1#126"])
def test_eom_note_follows_the_last_version(preferred):
    assert find_latest_chair_notes(EOM_LISTING, preferred_meeting_id=preferred)["name"] == EOM_NOTES
    # Upload times do not decide: a mirror may copy v15 after the EOM.
    assert find_latest_chair_notes([_f(f["name"]) for f in EOM_LISTING])["name"] == EOM_NOTES
    late_copy = [*EOM_LISTING[:2], _f("Chair notes RAN1#126_v15.docx", datetime(2026, 9, 5, 8, 0)),
                 EOM_LISTING[2]]
    assert find_latest_chair_notes(late_copy)["name"] == EOM_NOTES


@pytest.mark.parametrize("names, expected", [
    # #126: eom, then eom1.
    (["Chair notes RAN1#126_v00.docm", EOM_NOTES, "Chair notes RAN1#126_eom1.docx"],
     "Chair notes RAN1#126_eom1.docx"),
    # #124bis: eom … eom5 after v15; eom10 would sort after eom5, not after eom1.
    (["Chair notes RAN1#124bis_v15.docx", "Chair notes RAN1#124bis_eom.docx",
      *[f"Chair notes RAN1#124bis_eom{n}.docx" for n in range(1, 6)]],
     "Chair notes RAN1#124bis_eom5.docx"),
    (["Chair notes RAN1#124bis_eom5.docx", "Chair notes RAN1#124bis_eom10.docx"],
     "Chair notes RAN1#124bis_eom10.docx"),
    # #124: an underscore before the number.
    (["Chair notes RAN1#124 - v10.docx", "Chair notes RAN1#124 - eom.docx",
      "Chair notes RAN1#124 - eom_01.docx", "Chair notes RAN1#124 - eom_03.docx"],
     "Chair notes RAN1#124 - eom_03.docx"),
    # #117: eom0 first; #118 skips eom3.
    (["Chair notes RAN1#117 v19.zip", "Chair notes RAN1#117 eom0.zip", "Chair notes RAN1#117 eom1.zip"],
     "Chair notes RAN1#117 eom1.zip"),
    (["Chair notes RAN1#118 eom2.zip", "Chair notes RAN1#118 eom4.zip", "Chair notes RAN1#118 v20.zip"],
     "Chair notes RAN1#118 eom4.zip"),
])
def test_eom_notes_are_ordered_by_their_own_number(names, expected):
    uploads = [_f(name, datetime(2026, 9, 1, 8, 0)) for name in names]
    assert find_latest_chair_notes(uploads)["name"] == expected
    assert find_latest_chair_notes([_f(name) for name in reversed(names)])["name"] == expected


def test_eom_of_a_past_meeting_does_not_beat_the_next_meeting():
    listing = [*EOM_LISTING, _f("Draft Chair notes RAN1#126bis_v00.docx", datetime(2026, 10, 4, 8, 57))]
    assert find_latest_chair_notes(listing)["name"] == "Draft Chair notes RAN1#126bis_v00.docx"


@pytest.mark.parametrize("name, eom", [
    (EOM_NOTES, 0),
    ("Chair notes RAN1#117 eom0.zip", 0),
    ("Chair notes RAN1#126_eom1.docx", 1),
    ("Chair notes RAN1#123 - eom3.docx", 3),
    ("Chair notes RAN1#124 - eom_02.docx", 2),
    ("Chair notes RAN1#126_v15.docx", None),
    ("Chair notes RAN1#126 geometry_v01.docx", None),
    ("Chair notes RAN1#126 eomx.docx", None),
])
def test_eom_is_a_whole_word_in_the_file_name(name, eom):
    assert _eom_number(name) == eom


def test_local_eom_note_follows_the_remote_order(tmp_path):
    for name in ("Chair notes RAN1#126_v15.docx", EOM_NOTES, "Chair notes RAN1#126_eom1.docx"):
        (tmp_path / name).write_bytes(b"note")
    assert find_chair_notes_docx(tmp_path, meeting_id="ran1#126").name == "Chair notes RAN1#126_eom1.docx"


def test_eom_after_the_last_version_is_not_a_downgrade():
    v15 = _ref("Chair notes RAN1#126_v15.docx", "2026-08-29T16:00:00")
    eom = _ref(EOM_NOTES, "2026-09-03T03:36:00")
    eom1 = _ref("Chair notes RAN1#126_eom1.docx", "2026-09-04T09:24:00")
    assert not check_update._is_chair_notes_downgrade(eom, v15)
    assert not check_update._is_chair_notes_downgrade(eom1, eom)
    assert check_update._is_chair_notes_downgrade(v15, eom)
    assert check_update._is_chair_notes_downgrade(eom, eom1)
