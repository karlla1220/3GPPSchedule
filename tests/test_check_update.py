"""Regression tests for CI change detection without filesystem timestamps."""

from unittest.mock import patch

import check_update


def _run_check(
    *,
    state: dict,
    local_refs: dict[str, str],
    remote=None,
    remote_error: Exception | None = None,
    extra_files: list[dict] | None = None,
    extra_changed: bool = False,
    local_meeting_hint: str | None = None,
    external_state: dict | None = None,
    agenda_info: dict | None = None,
    chair_notes_info: dict | None = None,
):
    outputs: list[tuple[str, str]] = []
    cfg = {
        "inbox_urls": [],
        "extra_folders": [],
        "extra_files": extra_files or [],
        "agenda_urls": ["https://example.org/Agenda/"],
    }
    remote_patch = patch(
        "check_update.get_all_remote_schedule_info",
        side_effect=remote_error if remote_error is not None else None,
        return_value=remote if remote_error is None else None,
    )
    with (
        patch("check_update.load_config", return_value=cfg),
        patch("check_update.load_schedule_state", return_value=state),
        patch("check_update.local_reference_hashes", return_value=local_refs),
        patch(
            "check_update.local_reference_meeting_id",
            return_value=local_meeting_hint,
        ),
        remote_patch as remote_mock,
        patch(
            "check_update.get_latest_agenda_info",
            return_value=agenda_info,
            create=True,
        ),
        patch(
            "check_update.get_latest_chair_notes_info",
            return_value=chair_notes_info,
            create=True,
        ),
        patch(
            "check_update.check_external_files",
            return_value=(extra_changed, {"files": {}}),
        ),
        patch(
            "check_update.load_external_files_state",
            return_value=external_state or {"files": {}},
        ),
        patch(
            "check_update._set_output",
            side_effect=lambda name, value: outputs.append((name, value)),
        ),
    ):
        check_update.main()
    return outputs, remote_mock


def test_late_chair_notes_appearance_triggers_pending_timezone_rebuild():
    outputs, _ = _run_check(
        state={
            "files": [],
            "meeting_id": "ran1#126",
            "timezone": "UTC",
            "timezone_status": "pending_timezone_ref",
            "timezone_ref": None,
            "local_refs": {},
        },
        local_refs={},
        remote=[],
        chair_notes_info={
            "name": "Chair notes RAN1#126_v00.docm",
            "uploaded_at": "2026-08-23T14:29:00",
            "url": "https://example.org/Chair%20notes%20RAN1%23126_v00.docm",
            "source_url": "https://example.org/Chair_notes",
        },
    )

    assert outputs == [("changed", "true")]


def test_same_timezone_reference_does_not_trigger_rebuild():
    timezone_ref = {
        "type": "chair_notes",
        "name": "Chair notes RAN1#126_v00.docm",
        "uploaded_at": "2026-08-23T14:29:00",
        "url": "https://example.org/Chair%20notes%20RAN1%23126_v00.docm",
        "source_url": "https://example.org/Chair_notes",
    }
    outputs, _ = _run_check(
        state={
            "files": [],
            "meeting_id": "ran1#126",
            "timezone": "Europe/Amsterdam",
            "timezone_status": "resolved",
            "timezone_ref": timezone_ref,
            "local_refs": {},
        },
        local_refs={},
        remote=[],
        chair_notes_info={k: v for k, v in timezone_ref.items() if k != "type"},
    )

    assert outputs == [("changed", "false")]


def test_partial_chair_notes_listing_does_not_downgrade_cached_version():
    cached_ref = {
        "type": "chair_notes",
        "name": "Chair notes RAN1#126_v03.docm",
        "uploaded_at": "2026-08-24T09:00:00",
        "url": "https://example.org/source-a/v03.docm",
        "source_url": "https://example.org/source-a/Chair_notes",
    }
    outputs, _ = _run_check(
        state={
            "files": [],
            "meeting_id": "ran1#126",
            "timezone": "Europe/Amsterdam",
            "timezone_status": "resolved",
            "timezone_ref": cached_ref,
            "local_refs": {},
        },
        local_refs={},
        remote=[],
        chair_notes_info={
            "name": "Chair notes RAN1#126_v02.docm",
            "uploaded_at": "2026-08-23T14:29:00",
            "url": "https://example.org/source-b/v02.docm",
            "source_url": "https://example.org/source-b/Chair_notes",
        },
    )

    assert outputs == [("changed", "false")]


def test_resolved_agenda_reference_ignores_later_chair_notes():
    agenda_ref = {
        "type": "agenda",
        "name": "RAN1#126 agenda.docx",
        "uploaded_at": "2026-08-20T08:00:00",
        "url": "https://example.org/Agenda/RAN1%23126%20agenda.docx",
        "source_url": "https://example.org/Agenda/",
    }
    outputs, _ = _run_check(
        state={
            "files": [],
            "meeting_id": "ran1#126",
            "timezone": "Europe/Amsterdam",
            "timezone_status": "resolved",
            "timezone_ref": agenda_ref,
            "local_refs": {},
        },
        local_refs={},
        remote=[],
        agenda_info={k: v for k, v in agenda_ref.items() if k != "type"},
        chair_notes_info={
            "name": "Chair notes RAN1#126_v00.docm",
            "uploaded_at": "2026-08-23T14:29:00",
            "url": "https://example.org/chair.docm",
            "source_url": "https://example.org/Chair_notes",
        },
    )

    assert outputs == [("changed", "false")]


def test_transient_agenda_listing_failure_keeps_resolved_agenda_reference():
    agenda_ref = {
        "type": "agenda",
        "name": "RAN1#126 agenda.docx",
        "uploaded_at": "2026-08-20T08:00:00",
        "url": "https://example.org/Agenda/RAN1%23126%20agenda.docx",
        "source_url": "https://example.org/Agenda/",
    }
    outputs, _ = _run_check(
        state={
            "files": [],
            "meeting_id": "ran1#126",
            "timezone": "Europe/Amsterdam",
            "timezone_status": "resolved",
            "timezone_ref": agenda_ref,
            "local_refs": {},
        },
        local_refs={},
        remote=[],
        agenda_info=None,
        chair_notes_info={
            "name": "Chair notes RAN1#126_v00.docm",
            "uploaded_at": "2026-08-23T14:29:00",
            "url": "https://example.org/chair.docm",
            "source_url": "https://example.org/Chair_notes",
        },
    )

    assert outputs == [("changed", "false")]


def test_local_timezone_reference_is_not_replaced_by_remote_candidate():
    outputs, _ = _run_check(
        state={
            "files": [],
            "meeting_id": "ran1#126",
            "timezone": "Europe/Amsterdam",
            "timezone_status": "resolved",
            "timezone_ref": {
                "type": "agenda",
                "name": "local agenda.docx",
                "sha256": "local-content-hash",
                "origin": "local",
            },
            "local_refs": {},
        },
        local_refs={},
        remote=[],
        chair_notes_info={
            "name": "Chair notes RAN1#126_v00.docm",
            "uploaded_at": "2026-08-23T14:29:00",
            "url": "https://example.org/chair.docm",
            "source_url": "https://example.org/Chair_notes",
        },
    )

    assert outputs == [("changed", "false")]


def test_deleted_local_reference_is_detected_when_directory_is_empty():
    outputs, _ = _run_check(
        state={
            "files": [],
            "local_refs": {"RAN1#126 schedule.docx": "old-hash"},
        },
        local_refs={},
        remote=[],
    )

    assert outputs == [("changed", "true")]


def test_local_change_is_detected_when_ftp_check_fails():
    outputs, _ = _run_check(
        state={"files": [], "local_refs": {"schedule.docx": "old-hash"}},
        local_refs={"schedule.docx": "new-hash"},
        remote_error=RuntimeError("temporary FTP outage"),
    )

    assert outputs == [("changed", "true")]


def test_extra_file_change_is_checked_when_ftp_returns_no_files():
    outputs, _ = _run_check(
        state={"files": [], "local_refs": {}},
        local_refs={},
        remote=[],
        extra_files=[{"url": "https://example.org/schedule.docx"}],
        extra_changed=True,
    )

    assert outputs == [
        ("extra_files_artifact", "false"),
        ("changed", "true"),
    ]


def test_local_meeting_hint_is_preferred_over_cached_meeting():
    outputs, remote_mock = _run_check(
        state={"files": [], "meeting_id": "ran1#125", "local_refs": {}},
        local_refs={},
        remote=[],
        local_meeting_hint="ran1#126",
    )

    assert outputs == [("changed", "false")]
    # The patch is inspected through the call captured by the mock rather
    # than relying on filesystem state or checkout timestamps.
    assert remote_mock.call_args.kwargs["preferred_meeting_id"] == "ran1#126"


def test_local_meeting_lock_keeps_check_aligned_with_authoritative_local_build():
    outputs, remote_mock = _run_check(
        state={
            "files": [
                {
                    "folder": "Chair_notes",
                    "name": "RAN1#126 schedule - v03.docx",
                    "uploaded_at": "2026-05-01T09:00:00",
                }
            ],
            "meeting_id": "ran1#126",
            "local_refs": {"RAN1#126 schedule.docx": "stable-hash"},
        },
        local_refs={"RAN1#126 schedule.docx": "stable-hash"},
        remote=[
            {
                "folder": "Chair_notes",
                "name": "RAN1#126 schedule - v03.docx",
                "uploaded_at": "2026-05-01T09:00:00",
            }
        ],
        local_meeting_hint="ran1#126",
    )

    assert outputs == [("changed", "false")]
    assert remote_mock.call_args.kwargs["locked_meeting_id"] == "ran1#126"


def test_removed_extra_files_are_detected_from_stale_state():
    outputs, _ = _run_check(
        state={"files": [], "local_refs": {}},
        local_refs={},
        remote=[],
        external_state={"files": {"https://example.org/old.docx": "old-hash"}},
    )

    assert outputs == [("changed", "true")]


def test_external_cache_miss_exposes_transfer_artifact(tmp_path):
    outputs: list[tuple[str, str]] = []
    url = "https://example.org/schedule.docx"
    cfg = {
        "inbox_urls": [],
        "extra_folders": [],
        "extra_files": [{"url": url, "type": "schedule"}],
    }

    def fake_external_check(*args, **kwargs):
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "schedule.docx").write_bytes(b"staged")
        return True, {
            "files": {
                url: {
                    "sha256": "staged-hash",
                    "filename": "schedule.docx",
                }
            }
        }

    with (
        patch("check_update.load_config", return_value=cfg),
        patch("check_update.load_schedule_state", return_value={"files": []}),
        patch("check_update.local_reference_hashes", return_value={}),
        patch("check_update.local_reference_meeting_id", return_value=None),
        patch("check_update.get_all_remote_schedule_info", return_value=[]),
        patch("check_update.EXTRA_FILES_TRANSFER_DIR", tmp_path),
        patch("check_update.check_external_files", side_effect=fake_external_check),
        patch(
            "check_update._set_output",
            side_effect=lambda name, value: outputs.append((name, value)),
        ),
    ):
        check_update.main()

    assert outputs == [
        ("extra_files_artifact", "true"),
        ("changed", "true"),
    ]
    assert (tmp_path / ".extra_files_state.json").exists()
