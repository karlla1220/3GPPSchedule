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


@pytest.mark.parametrize("sessions, agenda_items", [
    # Two rooms under one title, each on its own sub-item.
    ([_session("10.5.2.x", "14:30", "16:30", 2, "10.5.2.x"),
      _session("10.5.2.x", "14:30", "16:30", 4, "10.5.2.x")], ()),
    ([_session("10.5.2 PDCCH", "14:30", "15:00", 2, "10.5.2, 10.5.2.1"),
      _session("10.5.2 PDSCH", "14:30", "15:00", 4, "10.5.2, 10.5.2.2")], ()),
    # The agenda alone says 10.5.2 is a title.
    ([_session("10.5.2", "14:30", "16:30", 2, "10.5.2"),
      _session("10.5.2", "14:30", "16:30", 4, "10.5.2")], ("10.5.2", "10.5.2.1")),
])
def test_one_title_in_two_rooms_at_once_passes(sessions, agenda_items):
    reject_concurrent_agenda_duplicates(sessions, agenda_items=agenda_items)


def test_same_detailed_item_under_a_title_is_rejected():
    sessions = [
        _session("10.5.2.x", "14:30", "16:30", 2, "10.5.2.x, 10.5.2.1"),
        _session("10.5.2.1", "15:00", "15:30", 4, "10.5.2.1"),
    ]
    assert [n for n, _, _ in concurrent_agenda_duplicates(sessions)] == ["10.5.2.1"]


def test_item_without_sub_items_is_detailed():
    sessions = [
        _session("AI 9.2 MIMO", "17:00", "17:40", 3, "9.2"),
        _session("MIMO", "17:00", "17:40", 5, "9.2"),
    ]
    assert [n for n, _, _ in concurrent_agenda_duplicates(sessions, ("9.1", "9.1.1", "9.2"))] == ["9.2"]


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


def _tuesday_10_3_2():
    # RAN1#127 Main v01: the online room moved 10.3.2 to 14:30, the offline room kept it there.
    return [_session("AI 10.3.2 Modulation", "14:30", "15:00", 3, "10.3.2", day="Tuesday"),
            _session("10.3.2 Modulation", "14:30", "15:00", 6, "10.3.2", day="Tuesday")]


def test_duplicate_from_an_incremental_merge_is_still_rejected():
    with pytest.raises(RejectedState) as caught:
        reject_concurrent_agenda_duplicates(
            _tuesday_10_3_2(), incremental={SLOT_STATE_DIR / "Tuesday_02.json"})
    assert caught.value.discard == (SLOT_STATE_DIR / "Tuesday_02.json",)


def test_duplicate_read_cold_is_the_documents_and_is_published_with_a_notice():
    from shared import notices
    caveats = reject_concurrent_agenda_duplicates(_tuesday_10_3_2(), incremental=set(), meeting_id="ran1#127")
    [caveat] = caveats["Tuesday"]
    assert caveat.startswith("AI 10.3.2 is in two rooms at the same time: 14:30–15:00")
    assert "next update" in caveat
    [notice] = notices.read()
    assert notice["kind"] == "source-duplicate"
    assert any("Tuesday AI 10.3.2" in line for line in notice["lines"])
