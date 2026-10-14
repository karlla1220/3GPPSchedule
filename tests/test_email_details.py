import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from working_groups.ran1.email_details import Cell, candidates, covers, request_text, resolve

FIXTURE = Path(__file__).parent / "fixtures" / "ran1" / "listserv_126bis"
TZ = ZoneInfo("Asia/Seoul")
MONDAY = date(2026, 10, 12)
CELLS = [
    Cell(g["id"], g["day"], g["start"], g["end"], g["room"], g["name"], g["agenda_item"], tuple(g["inferred_agenda_items"]))
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
    assert got == []  # no time and no session: nothing shown on a guess
    stated = _item(mode="online", session_id="Tue 17:30-18:30 Yeongju B Hall", start="17:00")
    got = _resolve([stated], "9.3.3", _sent(13, "17:12"), allowed_times=times_in(reply))
    assert [(g["kind"], g["session"], "start" in g) for g in got] == [("session", "Tue 17:30-18:30 Yeongju B Hall", False)]


def test_times_in():
    from working_groups.ran1.email_details import times_in

    assert {15 * 60 + 30, 3 * 60 + 30} <= times_in("start the offline at 3:30pm")
    assert 12 * 60 in times_in("online session this morning from 12:00am")
    assert 16 * 60 in times_in("offline session on Wednesday ~4:00PM")
    assert times_in("Proposal 3.1.1-4 in v33, 2 hours before") == set()
