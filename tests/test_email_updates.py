"""The email step across runs: what is fetched, what is asked, what is kept."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from shared.schedule import DaySchedule, RoomInfo, Schedule, Session, load_schedule, save_schedule
from working_groups.ran1 import email_updates
from working_groups.ran1.email_sessions import body_text, needs_full_body, newest_reply, parse_feed

REAL_IN_MEETING = email_updates.in_meeting
FIXTURE = Path(__file__).parent / "fixtures" / "ran1" / "listserv_126bis"
read = lambda name: json.loads((FIXTURE / name).read_text(encoding="utf-8"))
MAILS = read("mails.json")
FEED = (FIXTURE / "feed_rss2_limit50.xml").read_text(encoding="utf-8")
ANSWERS = read("llm_answers.json")["answers"]
LABELS = read("labels.json")["mails"]
NOW = datetime(2026, 10, 14, 3, 30, tzinfo=timezone.utc)  # Wednesday 12:30 in Jeju, after the last mail


def _schedule():
    """The RAN1#126bis grid of the test set as a schedule, with its email threads."""
    days = {}
    for g in read("grid.json")["sessions"]:
        days.setdefault(g["day"], []).append(g)
    built = []
    for day, cells in days.items():
        rooms = []
        for g in cells:
            for room in g["room"].split(" + "):
                if room not in rooms:
                    rooms.append(room)
        sessions = []
        for g in cells:
            span = g["room"].split(" + ")
            start = rooms.index(span[0]) + 2
            sessions.append(Session(g["name"], 0, g["start"], g["end"], day, start, start + len(span),
                                    chair=g["chair"], agenda_item=g["agenda_item"],
                                    inferred_agenda_items=list(g["inferred_agenda_items"])))
        built.append(DaySchedule(day, [RoomInfo(name) for name in rooms], sessions))
    return Schedule("RAN1#126bis", built, "schedule.docx", "2026-10-14 12:30", timezone="Asia/Seoul",
                    wg_id="ran1", meeting_id="ran1#126bis", starts_on="2026-10-12", ends_on="2026-10-16",
                    chairman_agreements={"status": "ready", "meeting_id": "ran1#126bis",
                                         "email_threads": read("email_threads_chair.json")["threads"]})


@pytest.fixture
def meeting(tmp_path, monkeypatch):
    monkeypatch.setattr(email_updates, "in_meeting", REAL_IN_MEETING)
    agenda = tmp_path / "agenda_item_description.json"
    agenda.write_text(json.dumps(read("agenda_items.json")), encoding="utf-8")
    monkeypatch.setattr(email_updates, "AGENDA_PATH", agenda)
    return _schedule()


def _archive():
    """Fake list.etsi.org; ``calls`` lists every request by kind."""
    calls = []
    by_id = {m["id"]: m for m in MAILS}

    def handler(request):
        url = str(request.url)
        if "?RSS&" in url:
            calls.append("feed")
            return httpx.Response(200, text=FEED)
        for mail in MAILS:
            if "A2=" in url and mail["id"] in url.lower():
                calls.append("page")
                part = mail["body_url"].replace("https://list.etsi.org", "")
                return httpx.Response(200, text=f'<a href="{part}&amp;header=1">text</a>')
            if url == mail["body_url"]:
                calls.append("body")
                return httpx.Response(200, text=mail["body"])
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def _replies():
    """Newest reply of each mail as the collector reads it."""
    feed = {m.id: m for m in parse_feed(FEED)}
    return {
        mail["id"]: newest_reply(body_text(mail["body"]) if needs_full_body(feed[mail["id"]].snippet)
                                 else feed[mail["id"]].snippet)
        for mail in MAILS
    }


def _extract(asked, fail=()):
    replies = _replies()

    def extract(text):
        mail_id = next(i for i, reply in replies.items() if reply and f"(quoted earlier mails removed):\n{reply}" in text)
        asked.append(mail_id)
        if mail_id in fail:
            raise RuntimeError("model unavailable")
        return ANSWERS[mail_id]

    return extract


def _update(schedule, asked, fail=()):
    http, calls = _archive()
    new = email_updates.update_records(schedule, now=NOW, http=http, extract=_extract(asked, fail),
                                       sleep=lambda _: None)
    return new, calls


@pytest.fixture(autouse=True)
def unlimited(monkeypatch):
    monkeypatch.setattr(email_updates, "MAX_ASKS", 100)


def test_each_mail_is_fetched_and_asked_once(meeting):
    asked = []
    new, calls = _update(meeting, asked)
    statuses = [r["status"] for r in new]
    assert len(new) == 48  # every mail on a thread or with an agenda number
    assert statuses.count("asked") == len(asked) == 41
    assert statuses.count("skipped") == 7  # no word of time or place: "updated v33, please check"
    assert calls.count("feed") == 1
    assert calls.count("body") == calls.count("page") == 20  # only cut previews of wanted mails
    # The next run reads the feed, finds every mail recorded, and stops there.
    again, calls = _update(meeting, asked)
    assert again == [] and calls == ["feed"] and len(asked) == 41


def test_records_are_kept_in_docs_and_in_the_ci_cache(meeting):
    asked = []
    _update(meeting, asked)
    docs = email_updates.record_path("ran1#126bis")
    cache = email_updates.record_path("ran1#126bis", email_updates.CACHE_DIR)
    assert len(docs.read_text().splitlines()) == len(cache.read_text().splitlines()) == 48
    # A failed build rolls docs/ back but saves the cache: nothing is asked
    # again, and the committed list catches up from the cache.
    docs.unlink()
    again, _ = _update(meeting, asked)
    assert again == [] and len(asked) == 41
    assert len(docs.read_text().splitlines()) == 48


def test_a_failed_call_is_not_recorded_and_is_asked_next_run(meeting):
    first = MAILS[4]["id"]  # #5, the plan of the DL shared channel offline and onlines
    asked = []
    new, _ = _update(meeting, asked, fail={first})
    assert first in asked and first not in {r["id"] for r in new}
    asked.clear()
    again, _ = _update(meeting, asked)
    assert asked == [first] and [r["id"] for r in again] == [first]


def test_no_key_no_call(meeting, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    http, _ = _archive()
    new = email_updates.update_records(meeting, now=NOW, http=http, sleep=lambda _: None)
    assert {r["status"] for r in new} == {"skipped"}  # only what needs no call is recorded


def test_nothing_is_requested_outside_the_meeting_or_without_threads(meeting):
    asked = []
    http, calls = _archive()
    after = datetime(2026, 10, 25, tzinfo=timezone.utc)
    assert email_updates.update_records(meeting, now=after, http=http, extract=_extract(asked)) == []
    assert email_updates.pending(meeting, now=after, http=http) is None
    meeting.chairman_agreements["email_threads"] = []  # the chairman note names no thread yet
    assert email_updates.update_records(meeting, now=NOW, http=http, extract=_extract(asked)) == []
    assert calls == [] and asked == []


def test_check_finds_only_unrecorded_mails_with_words_of_time(meeting):
    http, calls = _archive()
    # By the preview alone: 36 of the 41 asked mails show a word of time in it.
    assert len(email_updates.pending(meeting, now=NOW, http=http)) == 36
    _update(meeting, [])
    assert email_updates.pending(meeting, now=NOW, http=http) == []
    assert calls == ["feed", "feed"]


def test_recorded_answers_go_on_the_grid(meeting, tmp_path):
    _update(meeting, [])
    email_updates.apply_records(meeting)
    cells, sessions = email_updates.cells_of(meeting)
    got = {}
    for cell_id, session in sessions.items():
        for item in session.email_details:
            got.setdefault(item["mail"], []).append((item["kind"], cell_id))
    ids = {m["id"]: m["n"] for m in MAILS}
    # The 8.1 cell holds "maybe 10.3.1": #3 restates the 12:00 online of #15
    # with the FL proposal, so its plan is the one kept.
    maybe = [i for i in sessions["Wed 12:00-12:30 Yeongju A Hall"].email_details if i["kind"] == "tentative"]
    assert [(ids[i["mail"]], i["agenda_item"], i["start"], i["tdocs"]) for i in maybe] == [
        (3, "10.3.1", "12:00", ["R1-2608628"])]
    # #36 replaced the plan of #38 for the same MIMO offlines.
    mimo = sessions["Wed 08:30-09:10 203"].email_details
    assert {ids[i["mail"]] for i in mimo} == {36}
    # Off-offline stays off the grid, by agenda item.
    assert any(ids[i["mail"]] == 20 for i in meeting.email_informal["9.4"])
    # Every labelled session item is found.
    want = {(n, item["kind"], item["session"]) for n, items in LABELS.items() for item in items if item.get("session")}
    found = {(str(ids[m]), kind, cell) for m, items in got.items() for kind, cell in items}
    assert want - found == {("38", "session", "Wed 08:30-09:10 203"), ("38", "session", "Wed 12:30-13:00 203"),
                            ("15", "tentative", "Wed 12:00-12:30 Yeongju A Hall")}  # replaced by #36 and #3
    # The snapshot keeps them.
    save_schedule(meeting, tmp_path / "schedule.json")
    loaded = load_schedule(tmp_path / "schedule.json")
    assert loaded.email_informal == meeting.email_informal
    assert sum(len(s.email_details) for d in loaded.days for s in d.sessions) == sum(
        len(s.email_details) for s in sessions.values())


def test_calls_per_run_are_capped(meeting, monkeypatch):
    monkeypatch.setattr(email_updates, "MAX_ASKS", 40)
    asked = []
    first, _ = _update(meeting, asked)
    assert len(asked) == 40 and len(first) == 47  # the newest mail waits
    again, _ = _update(meeting, asked)
    assert len(asked) == 41 and [r["id"] for r in again] == [MAILS[0]["id"]]


def test_a_run_stopped_half_way_keeps_what_it_asked(meeting):
    asked = []

    class Stop(BaseException):
        pass

    answers = _extract(asked)

    def extract(text):
        if len(asked) == 5:
            raise Stop  # the runner is killed
        return answers(text)

    http, _ = _archive()
    with pytest.raises(Stop):
        email_updates.update_records(meeting, now=NOW, http=http, extract=extract, sleep=lambda _: None)
    kept = email_updates.load_records("ran1#126bis")
    assert sum(r["status"] == "asked" for r in kept.values()) == 5
    again, _ = _update(meeting, asked)
    assert len(asked) == 41  # the five are not asked again


def test_the_grid_shows_maybe_items_filters_them_and_lists_the_mails(meeting):
    from bs4 import BeautifulSoup
    from shared.renderer import generate_html

    _update(meeting, [])
    email_updates.apply_records(meeting)
    page = BeautifulSoup(generate_html(meeting), "html.parser")
    blocks = [b for b in page.select(".session-block") if b.select_one(".session-name").get_text() == "AI 8.1"]
    cell = next(b for b in blocks if "maybe" in b.get_text())
    assert cell.select_one(".session-ai").get_text() == "AI 8.1 · maybe 10.3.1"
    assert cell.select_one(".session-maybe").get_text() == "maybe 10.3.1"
    assert cell["data-ai-scope"] == "8.1.*|10.3.1.*" and cell["data-ai"] == "8.1"  # filtered like its own
    popup = BeautifulSoup(cell["data-popup"], "html.parser")
    line = popup.select_one(".popup-email-item").get_text()
    assert line.startswith("maybe 10.3.1 · Online 12:00 · ")
    assert popup.select_one(".popup-email-tdoc").get_text() == "R1-2608628"
    # "↗" after the sender opens the mail in the list archive; the name is not a link.
    source = popup.select_one(".popup-email-from")
    link = source.select_one("a.mail-link")
    assert link["href"].startswith("https://list.etsi.org/scripts/wa.exe?A2=3GPP_TSG_RAN_WG1;")
    assert link["target"] == "_blank" and link.get_text() == "↗"
    assert source.get_text().endswith("\xa0↗") and len(source.select("a")) == 1
    # A plan with a split, given by lengths only, keeps the lengths.
    wus = next(b for b in page.select(".session-block") if "10.6.1.2" in (b.get("data-popup") or "") and "From email" in b["data-popup"])
    text = BeautifulSoup(wus["data-popup"], "html.parser").select_one(".popup-email").get_text()
    assert "10.6.1.2 (40 min), 10.6.3 (30 min), 10.6.1.1 (20 min)" in text


def test_the_cell_shows_the_plan_where_it_has_room(meeting):
    # Delegates do not read release notes: what the FL announced is in the
    # cell's spare lines, not only in the popup.
    from bs4 import BeautifulSoup
    from shared.renderer import generate_html

    _update(meeting, [])
    email_updates.apply_records(meeting)
    page = BeautifulSoup(generate_html(meeting), "html.parser")

    def cell(title, start):
        return next(b for b in page.select(".session-block")
                    if b.select_one(".session-name").get_text() == title
                    and b.select_one(".session-range") and start in b.select_one(".session-range").get_text())

    wus = [n.get_text() for n in cell("10.6.x", "11:00").select(".session-note")]
    assert wus[0] == "10.6.1.2 40 min · 10.6.3 30 min · 10.6.1.1 20 min"  # lengths, no clock times
    pdsch = [n.get_text() for n in cell("10.5.2.2", "17:00").select(".session-note")]
    assert pdsch == ["17:00–17:30 PDSCH · 17:30–18:00 RS for PDSCH"]
    maybe = [n.get_text() for n in cell("AI 8.1", "12:00").select(".session-note")]
    assert maybe == []  # 30 minutes: title, time and AI line fill the cell


def test_off_offline_is_one_list_below_the_agreements(meeting):
    from bs4 import BeautifulSoup
    from shared.renderer import generate_html

    _update(meeting, [])
    email_updates.apply_records(meeting)
    page = BeautifulSoup(generate_html(meeting), "html.parser")
    box = page.select_one("section.email-informal")
    # After the agreement panel, not inside it.
    assert box is not None and box.find_parent(id="agreement-panel") is None
    rows = box.select("li")
    # Every item, whatever its agenda item, in time order, each with its item and date.
    assert len(rows) == sum(len(items) for items in meeting.email_informal.values())
    dates = [r["data-date"] for r in rows]
    assert dates == sorted(dates)
    row = next(r for r in rows if r["data-ai"] == "9.4" and "10:45" in r.get_text())
    # #20: "off-offline discussion at 10:45 today. Let's meet in front of Yeongju A Hall" (Wed 08:30).
    assert row["data-date"] == "2026-10-14" and row["data-day"] == "wednesday"
    # No weekday on the row: the list follows the day tab.
    assert row.select_one(".email-informal-when").get_text() == "10:45"
    assert row.select_one(".email-informal-ai").get_text() == "AI 9.4"
    assert "Yeongju A Hall" in row.get_text()
    assert row.select_one(".email-informal-from").get_text() == "— EL JAAFARI Mohamed\xa0↗"
    link = row.select_one(".email-informal-from a.mail-link")
    assert link["href"] == f"https://list.etsi.org/scripts/wa.exe?A2=3GPP_TSG_RAN_WG1;{next(i['mail'] for i in meeting.email_informal['9.4'] if i.get('start') == '10:45')}"
    # The script after it shows the selected tab's day and marks the selected cell's rows.
    script = box.find_next_sibling("script").string
    assert "row.dataset.day !== day" in script and "classList.toggle('related'" in script


def test_no_list_without_off_offline(meeting):
    from shared.renderer import generate_html

    assert '<section class="email-informal"' not in generate_html(meeting)


def test_sending_times_of_records_and_mails_both_read():
    from zoneinfo import ZoneInfo

    seoul = ZoneInfo("Asia/Seoul")
    assert email_updates._sent("2026-10-14T01:42:24+09:00", seoul).strftime("%a %H:%M") == "Wed 01:42"
    assert email_updates._sent("Wed, 14 Oct 2026 00:42:24 +0800", seoul).strftime("%a %H:%M") == "Wed 01:42"
    assert email_updates._sent("not a date", seoul) is None


def test_a_place_the_summary_names_is_not_repeated():
    from shared.renderer import _says

    assert _says("FL available near cute bears on 3rd floor", "near the cute bears on the 3rd floor")
    assert _says("Off-offline outside RAN1 main room", "one outside the RAN1 main room")
    assert not _says("Off-offline at 15:00", "same place as yesterday")


@pytest.mark.parametrize("topic, subject", [
    ("Part 1 (PUSCH MIMO)", "Part 1 (PUSCH MIMO)"),
    ("Proposal 4.3.1-1/2: DMRS sequence (for CP-OFDM, DFTS-OFDM)", "DMRS sequence (for CP-OFDM, DFTS-OFDM)"),
    ("Proposal 3-1 (II) in Section 4.2", "Section 4.2"),
    ("section 2.3 in R1-2608701", "section 2.3"),
    ("proposals in Section 2", "proposals in Section 2"),
    # Numbers to look up in the document stay in the popup.
    ("Proposal 3.1.1-4", ""), ("FL Proposal 5.3-6A", ""), ("proposals #4-4A", ""),
    ("observation 3.1.2", ""), ("Proposals 3.1-2B and 5.3-7A", ""),
])
def test_cell_subjects_leave_numbers_to_the_popup(topic, subject):
    from shared.renderer import _subject

    assert _subject(topic) == subject


def test_cell_line_names_the_subject_not_the_proposals(meeting):
    from bs4 import BeautifulSoup
    from shared.renderer import generate_html

    _update(meeting, [])
    email_updates.apply_records(meeting)
    page = BeautifulSoup(generate_html(meeting), "html.parser")
    cell = next(b for b in page.select(".session-block")
                if b.select_one(".session-name").get_text() == "10.5.2.3"
                and "11:30" in b.select_one(".session-range").get_text())
    assert [n.get_text() for n in cell.select(".session-note")] == ["Part 1 (PUSCH MIMO)"]
    assert "3.1.1-4" in cell["data-popup"]  # still in the popup


def test_cell_line_leads_with_the_document(meeting):
    # #18: "R1-2608681 is uploaded for online discussion (Wed 9:20). Please
    # check the proposals in Section 2 where focus is on DL bandwidth settings".
    from bs4 import BeautifulSoup
    from shared.renderer import generate_html

    _update(meeting, [])
    email_updates.apply_records(meeting)
    page = BeautifulSoup(generate_html(meeting), "html.parser")
    cell = next(b for b in page.select(".session-block")
                if b.select_one(".session-name").get_text() == "10.5.1.3"
                and "09:20" in b.select_one(".session-range").get_text())
    lines = [n.get_text() for n in cell.select(".session-note")]
    assert lines and lines[0].startswith("R1-2608681 · ")


def _cell(cell_id, ai):
    day, times, room = cell_id.split(" ", 2)
    start, end = times.split("-")
    name = {"Fri": "Friday"}[day]
    return email_updates.Cell(cell_id, name, start, end, room, ai, ai)


def test_a_recorded_session_follows_its_item_when_the_schedule_changes():
    # #126bis: "We will treat Observation 4-7-4 on Friday morning online
    # session" (10.5.2.2) was asked when Fri 09:30 Yeongju A Hall was
    # "10.5.2.x". The chair then split it: 10.5.2.1 at 09:30, 10.5.2.2 at 11:00.
    record = {
        "id": "8baf41ac.2610b", "status": "asked", "date": "2026-10-15T01:04:48+09:00",
        "thread": "126bis-R20-6GR-Transmission scheme for DL shared channel",
        "agenda_items": ["10.5.2.2"], "other": [], "tdocs": [], "reply": "Friday morning online session",
        "offered": ["Thu 12:15-13:00 203", "Fri 09:30-10:30 Yeongju A Hall"],
        "answer": {"items": [{"kind": "session", "mode": "online", "day": "Friday", "quote": "q", "summary": "s",
                              "topics": [], "split": [], "session_id": "Fri 09:30-10:30 Yeongju A Hall"}]},
    }
    tz = email_updates.ZoneInfo("Asia/Seoul")

    def placed(*cells):
        return [item["session"] for item in email_updates._history([record], list(cells), tz)[record["id"]]]

    before = _cell("Fri 09:30-10:30 Yeongju A Hall", "10.5.2.x")
    assert placed(before, _cell("Fri 11:00-12:00 Yeongju A Hall", "TBD")) == ["Fri 09:30-10:30 Yeongju A Hall"]
    split = [_cell("Fri 09:30-10:30 Yeongju A Hall", "10.5.2.1"), _cell("Fri 11:00-12:00 Yeongju A Hall", "10.5.2.2")]
    assert placed(*split) == ["Fri 11:00-12:00 Yeongju A Hall"]
    # A shifted cell is the same session.
    assert placed(_cell("Fri 09:45-10:45 Yeongju A Hall", "10.5.2.2")) == ["Fri 09:45-10:45 Yeongju A Hall"]
    # Another room is another chair's session: not followed.
    assert placed(split[0], _cell("Fri 11:00-12:00 201", "10.5.2.2")) == []
    # Two candidates and none at the old time: no guess.
    assert placed(split[0], split[1], _cell("Fri 14:00-15:00 Yeongju A Hall", "10.5.2.2")) == []
