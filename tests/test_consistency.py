from pathlib import Path

import pytest

from shared.lifecycle import RejectedState, preserve_on_failure
from shared.schedule import Session
from working_groups.ran1.consistency import (
    concurrent_agenda_duplicates,
    reject_concurrent_agenda_duplicates,
)
from working_groups.ran1.slot_state import SLOT_STATE_DIR


def _session(name, start, end, col, agenda_item=None, day="Thursday"):
    return Session(
        name=name, duration_minutes=0, start_time=start, end_time=end, day=day,
        room_col_start=col, room_col_end=col + 1, agenda_item=agenda_item,
    )


def test_same_agenda_in_two_rooms_at_once_rejects_its_slot():
    # RAN1#126bis Thursday: Hiroki's breakdown in Tamna and Yeongju B.
    sessions = [
        _session("10.5.4.4", "17:00", "17:30", 2, "10.5.4.4"),
        _session("10.5.4.4", "17:00", "17:30", 4, "10.5.4.4"),
        _session("10.5.1.1", "17:00", "17:50", 5, "10.5.1.1"),
    ]
    with pytest.raises(RejectedState) as caught:
        reject_concurrent_agenda_duplicates(sessions)
    assert caught.value.discard == (SLOT_STATE_DIR / "Thursday_03.json",)
    assert "Thursday AI 10.5.4.4" in str(caught.value)


def test_overlap_is_enough_and_numbers_are_split():
    sessions = [
        _session("6G 10.5.4.x", "14:30", "16:30", 6, "10.5.4.1, 10.5.4.3"),
        _session("10.5.4.3", "15:00", "15:40", 4, "10.5.4.3"),
    ]
    assert [number for number, _, _ in concurrent_agenda_duplicates(sessions)] == ["10.5.4.3"]


@pytest.mark.parametrize("sessions", [
    # Same agenda item later in another room.
    [_session("10.5.4.1", "17:00", "17:30", 2, "10.5.4.1"),
     _session("10.5.4.1", "17:30", "18:00", 4, "10.5.4.1")],
    # Same agenda item on another day.
    [_session("10.5.4.1", "17:00", "17:30", 2, "10.5.4.1"),
     _session("10.5.4.1", "17:00", "17:30", 2, "10.5.4.1", day="Friday")],
    # Sessions without an agenda number (breaks, dinner, placeholders).
    [_session("Early dinner", "18:30", "19:30", 2),
     _session("Early dinner", "18:30", "19:30", 5)],
    # Different agenda items at the same time.
    [_session("10.5.3.1", "17:00", "17:50", 2, "10.5.3.1"),
     _session("10.5.4.4", "17:00", "17:30", 4, "10.5.4.4")],
])
def test_schedules_without_concurrent_duplicates_pass(sessions):
    reject_concurrent_agenda_duplicates(sessions)


def test_rejected_paths_stay_removed_after_nested_rollbacks(tmp_path):
    slot = tmp_path / "slot.json"
    other = tmp_path / "other.json"
    slot.write_text("rejected merge")
    other.write_text("old")
    with pytest.raises(RejectedState):
        with preserve_on_failure((tmp_path,)):
            with preserve_on_failure((slot, other)):
                other.write_text("new")
                raise RejectedState("bad slot", discard=[slot])
    assert other.read_text() == "old"
    assert not slot.exists()
