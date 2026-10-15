import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from working_groups.ran1.email_details import Cell, candidates, covers, request_text, resolve

FIXTURE = Path(__file__).parent / "fixtures" / "ran1" / "listserv_126bis"
TZ = ZoneInfo("Asia/Seoul")
MONDAY = date(2026, 10, 12)
CELLS = [
    Cell(g["id"], g["day"], g["start"], g["end"], g["room"], g["name"], g["agenda_item"], tuple(g["inferred_agenda_items"]),
         online=g["online"], part=g["part"])
    for g in json.loads((FIXTURE / "grid.json").read_text(encoding="utf-8"))["sessions"]
]
BY_ID = {cell.id: cell for cell in CELLS}


def _sent(day, hhmm):
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


def _item(**fields):
    return {"quote": "q", "kind": "session", "session_id": None, "topics": [], "split": [], "summary": "s", **fields}


def test_covers():
    assert covers(BY_ID["Wed 11:00-12:30 201"], "10.6.1.2")  # "10.6.x"
    assert covers(BY_ID["Wed 08:30-09:10 203"], "9.2")  # "MIMO", inferred 9.2
    assert not covers(BY_ID["Wed 12:00-12:30 Yeongju A Hall"], "10.3.1")  # AI 8.1


def test_candidates_are_the_items_sessions_still_to_come():
    offered = candidates(CELLS, ["10.3.1"], _sent(14, "09:10"), MONDAY, TZ)
    assert [cell.id for cell in offered] == [
        "Wed 14:30-15:10 203", "Wed 17:00-18:00 Yeongju A Hall",
        "Thu 15:30-16:10 201", "Thu 17:00-18:00 Yeongju A Hall",
    ]


def test_request_names_the_sending_time_and_the_offered_ids():
    offered = candidates(CELLS, ["10.3.1"], _sent(14, "09:10"), MONDAY, TZ)
    text = request_text("Re: [126bis-R20-6GR-Channel coding] x", _sent(14, "09:10"), ["10.3.1"],
                        {"10.3.1": "Channel coding"}, offered, "Dear all")
    assert "Sent: Wednesday 14 Oct 09:10 meeting time" in text
    assert "Wed 14:30-15:10 203\tWednesday 14:30-15:10\troom: 203" in text


def _resolve(items, ai, sent, **kw):
    offered = candidates(CELLS, [ai], sent, MONDAY, TZ)
    return resolve({"items": items}, offered=offered, cells=CELLS, agenda_items=[ai], sent=sent, **kw)


def test_session_by_id():
    got = _resolve([_item(session_id="Wed 11:30-12:30 203", start="11:30", end="12:30", day="Wednesday")],
                   "10.5.2.3", _sent(14, "09:44"))
    assert [(g["kind"], g["session"]) for g in got] == [("session", "Wed 11:30-12:30 203")]


def test_online_at_a_time_the_grid_lacks_is_tentative_in_the_items_room():
    # #15: "online session this morning from 12:00". Every 10.3.1 online is in
    # Yeongju A Hall, which has AI 8.1 at 12:00.
    for answer in (
        _item(mode="online", day="Wednesday", start="12:00"),
        _item(mode="online", session_id="Wed 14:30-15:10 203", start="12:00"),  # an id that does not hold the time
    ):
        got = _resolve([answer], "10.3.1", _sent(14, "09:10"), tdocs=["R1-2608628"])
        assert [(g["kind"], g["session"], g["agenda_item"]) for g in got] == [
            ("tentative", "Wed 12:00-12:30 Yeongju A Hall", "10.3.1")
        ]
        assert got[0]["tdocs"] == ["R1-2608628"]


def test_an_id_not_offered_and_no_time_is_dropped():
    assert _resolve([_item(session_id="Tue 17:00-18:00 Yeongju A Hall")], "10.3.1", _sent(14, "09:10")) == []
    assert _resolve([_item(session_id="made up")], "10.3.1", _sent(14, "09:10")) == []


def test_lengths_stay_lengths():
    # #41: 10.6.1.2 40 min, 10.6.3 30 min, 10.6.1.1 20 min in the 10.6.x offline.
    # The mail states no clock time, so none is made up from the cell's start.
    split = [{"agenda_item": "10.6.1.2", "minutes": 40}, {"agenda_item": "10.6.3", "minutes": 30},
             {"agenda_item": "10.6.1.1", "minutes": 20}]
    got = _resolve([_item(session_id="Wed 11:00-12:30 201", split=split)], "10.6.1.1", _sent(13, "23:07"))
    assert got[0]["split"] == [
        {"agenda_item": "10.6.1.2", "minutes": 40}, {"agenda_item": "10.6.3", "minutes": 30},
        {"agenda_item": "10.6.1.1", "minutes": 20},
    ]
    stated = [{"topic": "PDSCH", "start": "17:00", "end": "17:30"}]
    got = _resolve([_item(session_id="Wed 17:00-18:00 203", split=stated)], "10.5.2.2", _sent(14, "11:12"))
    assert got[0]["split"] == stated


def test_informal_keeps_its_place():
    got = _resolve([_item(kind="informal", mode="off-offline", start="10:45", place="in front of Yeongju A Hall")],
                   "9.4", _sent(14, "08:30"))
    assert got == [{"kind": "informal", "quote": "q", "mode": "off-offline", "summary": "s", "topics": [],
                    "start": "10:45", "place": "in front of Yeongju A Hall"}]


def test_an_input_deadline_stays_as_written():
    # #28: "inputs until 1hr before our offline session today". The offline
    # may not start at 11:30, so no 10:30 is made up.
    got = _resolve([_item(session_id="Wed 11:30-12:30 203", input_due={"at": None, "minutes_before": 60})],
                   "10.5.2.3", _sent(14, "06:45"))
    assert got[0]["input_due"] == {"minutes_before": 60}
    got = _resolve([_item(session_id="Wed 19:00-19:30 Tamna BC Hall", input_due={"at": "14:00"})],
                   "10.5.3.1", _sent(14, "01:42"))
    assert got[0]["input_due"] == {"at": "14:00"}


def test_after_midnight_the_request_says_whose_tomorrow_it_is():
    text = request_text("t", _sent(14, "01:42"), ["10.5.3.1"], {}, [], "reply")
    assert "to the writer, 'today' may still be Tuesday and 'tomorrow' Wednesday" in text
    assert "after midnight" not in request_text("t", _sent(14, "09:10"), ["10.5.3.1"], {}, [], "reply")


def test_an_offline_at_a_time_the_grid_lacks_is_off_offline():
    # 9.5: "Let's start the offline at 3:30pm"; the next day the same FL
    # held an "off-offline … same place as yesterday".
    got = _resolve([_item(mode="offline", day="Tuesday", start="15:30")], "9.5", _sent(13, "15:02"))
    assert [(g["kind"], g["start"]) for g in got] == [("informal", "15:30")]


def test_a_time_the_mail_does_not_state_is_dropped():
    from working_groups.ran1.email_details import times_in

    # 9.3.3: "For Tuesday Online session, FL summary R1-2607193 is now
    # available"; the answer put 17:00 on it, the 9.3.3 online is at 17:30.
    reply = "For Tuesday Online session, FL summary R1-2607193 is now available in the inbox."
    answer = _item(mode="online", day="Tuesday", start="17:00")
    got = _resolve([answer], "9.3.3", _sent(13, "17:12"), allowed_times=times_in(reply))
    # The 17:00 goes; "Tuesday online" still names one session, in an online room.
    assert [(g["kind"], g["session"], "start" in g) for g in got] == [("session", "Tue 17:30-18:30 Yeongju B Hall", False)]
    assert _resolve([_item(day="Tuesday", start="17:00")], "9.3.3", _sent(13, "17:12"), allowed_times=set()) == [
        g | {"mode": None} for g in got]
    stated = _item(mode="online", session_id="Tue 17:30-18:30 Yeongju B Hall", start="17:00")
    got = _resolve([stated], "9.3.3", _sent(13, "17:12"), allowed_times=times_in(reply))
    assert [(g["kind"], g["session"], "start" in g) for g in got] == [("session", "Tue 17:30-18:30 Yeongju B Hall", False)]


def test_times_in():
    from working_groups.ran1.email_details import times_in

    assert {15 * 60 + 30, 3 * 60 + 30} <= times_in("start the offline at 3:30pm")
    assert 12 * 60 in times_in("online session this morning from 12:00am")
    assert 16 * 60 in times_in("offline session on Wednesday ~4:00PM")
    assert times_in("Proposal 3.1.1-4 in v33, 2 hours before") == set()


def test_online_rooms_are_those_the_table_heading_names():
    from shared.schedule import RoomInfo
    from working_groups.ran1.rooms import online_rooms, table_kind, tells_kinds

    # The text above a table holds the room text boxes, then the heading.
    assert table_kind("Tamna BC HallYeongju A HallRAN1#126b Online Session Schedule") == "online"
    assert table_kind("201203RAN1#126b Offline Session Schedule") == "offline"
    assert table_kind("RAN1#126 Detailed Schedule for RAN1 Main Session") == ""
    names = ["Tamna BC Hall", "Yeongju A Hall", "Yeongju B Hall", "201", "203"]
    headed = [RoomInfo(n, t, kind=k) for n, t, k in zip(names, [0, 0, 0, 1, 1], ["online"] * 3 + ["offline"] * 2)]
    assert online_rooms(headed) == names[:3]
    # Saved before kinds were read: the first table, as the order of tables holds during a meeting.
    assert online_rooms([RoomInfo(n, t) for n, t in zip(names, [0, 0, 0, 1, 1])]) == names[:3]
    assert not tells_kinds([RoomInfo(n) for n in names]) and tells_kinds(headed)
    assert online_rooms([]) == []


def test_parts_of_the_day_come_from_its_breaks():
    from working_groups.ran1.email_details import day_parts, part_of

    breaks = [{"name": "Morning Coffee Break", "start": "10:30", "end": "11:00"},
              {"name": "Lunch Break", "start": "13:00", "end": "14:30"},
              {"name": "Afternoon Coffee Break", "start": "16:30", "end": "17:00"}]
    parts = day_parts(breaks)
    assert [part_of(t, parts) for t in ["08:30", "12:00", "14:30", "16:00", "17:00", "19:00"]] == [
        "morning", "morning", "afternoon", "afternoon", "evening", "evening"]
    assert day_parts(breaks[:1]) is None  # no lunch break: no parts, rather than assumed hours
    assert part_of("09:00", None) is None


def test_the_request_says_which_rooms_are_online():
    offered = candidates(CELLS, ["10.3.1"], _sent(14, "09:10"), MONDAY, TZ)
    text = request_text("t", _sent(14, "09:10"), ["10.3.1"], {}, offered, "reply")
    assert "room: 203 (offline room)" in text
    assert "room: Yeongju A Hall (online room)" in text


FRIDAY = [  # #126bis Friday after the chair split "10.5.2.x" (09:30 Yeongju A Hall, Sorour)
    Cell("Fri 08:30-09:15 201", "Friday", "08:30", "09:15", "201", "10.5.2.2", "10.5.2.2", online=False, part="morning"),
    Cell("Fri 09:30-10:30 Yeongju A Hall", "Friday", "09:30", "10:30", "Yeongju A Hall", "10.5.2.1", "10.5.2.1",
         online=True, part="morning"),
    Cell("Fri 11:00-12:00 Yeongju A Hall", "Friday", "11:00", "12:00", "Yeongju A Hall", "10.5.2.2", "10.5.2.2",
         online=True, part="morning"),
    Cell("Fri 14:30-15:30 203", "Friday", "14:30", "15:30", "203", "10.5.2.2", "10.5.2.2", online=False, part="afternoon"),
]
PARTS = {"Friday": (13 * 60, 14 * 60 + 30, 17 * 60)}


def _bound(answer, cells, asked=None, ai="10.5.2.2"):
    offered = candidates(cells, [ai], _sent(15, "01:04"), MONDAY, TZ)
    got = resolve({"items": [answer]}, offered=offered, cells=cells, agenda_items=[ai], sent=_sent(15, "01:04"),
                  asked=asked, day_parts=PARTS)
    return [(g["kind"], g.get("session")) for g in got]


def test_a_recorded_session_is_found_again_when_the_schedule_changes():
    # "We will treat Observation 4-7-4 on Friday morning online session" was
    # read when Fri 09:30 Yeongju A Hall was "10.5.2.x", the only Friday
    # 10.5.2.2 session. The online is the one in an online room: 11:00, not
    # Sorour's 08:30 in 201.
    picked = _item(mode="online", day="Friday", session_id="Fri 09:30-10:30 Yeongju A Hall")
    assert _bound(picked, FRIDAY, asked=["Fri 09:30-10:30 Yeongju A Hall"]) == [
        ("session", "Fri 11:00-12:00 Yeongju A Hall")]


def test_a_mail_from_the_chairs_forecast_waits_for_the_schedule():
    # "After discussing with Mr Chair, we will have 30min offline and then
    # online tomorrow. Detailed slots to be updated." (10.4.2)
    forecast = _item(mode="online", day="Friday", part_of_day="morning")
    assert _bound(forecast, FRIDAY[:2], asked=[]) == []  # no 10.5.2.2 online yet: nothing shown
    assert _bound(forecast, FRIDAY, asked=[]) == [("session", "Fri 11:00-12:00 Yeongju A Hall")]
    afternoon = _item(mode="offline", day="Friday", part_of_day="afternoon")
    assert _bound(afternoon, FRIDAY, asked=[]) == [("session", "Fri 14:30-15:30 203")]


def test_without_day_time_or_pick_nothing_is_bound():
    assert _bound(_item(mode="online"), FRIDAY, asked=[]) == []


def test_a_pick_against_what_the_mail_says_is_not_followed():
    # 10.8.3 "tomorrow's offline discussion": the answer picked the online at
    # Thu 15:40 Yeongju B Hall; the offlines are at 11:00 and 14:30 in 201.
    cells = [
        Cell("Thu 11:00-12:30 201", "Thursday", "11:00", "12:30", "201", "10.8.3", "10.8.3", online=False, part="morning"),
        Cell("Thu 14:30-15:00 201", "Thursday", "14:30", "15:00", "201", "10.8.3", "10.8.3", online=False, part="afternoon"),
        Cell("Thu 15:40-16:30 Yeongju B Hall", "Thursday", "15:40", "16:30", "Yeongju B Hall", "10.8.3", "10.8.3",
             online=True, part="afternoon"),
    ]
    picked = _item(mode="offline", day="Thursday", session_id="Thu 15:40-16:30 Yeongju B Hall")
    sent = _sent(14, "22:00")
    got = resolve({"items": [picked]}, offered=candidates(cells, ["10.8.3"], sent, MONDAY, TZ), cells=cells,
                  agenda_items=["10.8.3"], sent=sent, day_parts={"Thursday": PARTS["Friday"]})
    assert got == []  # two offlines, and the pick's time says nothing about which


def test_a_tentative_slot_is_in_an_online_room():
    from working_groups.ran1.email_details import _home_room

    cells = [Cell(f"Wed {h}:00-{h}:30 201", "Wednesday", f"{h}:00", f"{h}:30", "201", "9.9", "9.9", online=False)
             for h in (14, 15)] + [Cell("Wed 16:00-16:30 Yeongju B Hall", "Wednesday", "16:00", "16:30",
                                        "Yeongju B Hall", "9.9", "9.9", online=True)]
    assert _home_room(cells, "9.9") == "Yeongju B Hall"
