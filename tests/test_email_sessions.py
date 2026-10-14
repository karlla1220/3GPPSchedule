import json
import re
from pathlib import Path

import httpx
import pytest

from working_groups.ran1.email_sessions import (
    body_url,
    collect,
    is_meeting_thread,
    meeting_tag,
    needs_full_body,
    newest_reply,
    parse_feed,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ran1" / "listserv_126bis"
FEED = (FIXTURE / "feed_rss2_limit50.xml").read_text(encoding="utf-8")
MAILS = json.loads((FIXTURE / "mails.json").read_text(encoding="utf-8"))
LABELS = json.loads((FIXTURE / "labels.json").read_text(encoding="utf-8"))
BY_N = {mail["n"]: mail for mail in MAILS}


def _words(text):
    return "".join(ch for ch in text if ch.isalnum())


def test_feed_parses_in_order():
    mails = parse_feed(FEED)
    assert [mail.id for mail in mails] == [mail["id"] for mail in MAILS]
    assert all(mail.date is not None and mail.date.tzinfo for mail in mails)
    assert "[...]" in mails[1].snippet
    assert "<https://" not in "".join(mail.snippet for mail in mails)


def test_full_body_rule_matches_labels():
    mails = parse_feed(FEED)
    need = [n for n, mail in enumerate(mails, 1) if needs_full_body(mail.snippet)]
    assert need == LABELS["need_full_body"]


def test_preview_is_enough_whenever_the_rule_says_so():
    # No mail the rule skips has its newest reply cut in the preview.
    for n, mail in enumerate(parse_feed(FEED), 1):
        if needs_full_body(mail.snippet):
            continue
        full = newest_reply(BY_N[n]["body"])
        assert _words(full) in _words(newest_reply(mail.snippet)), n


def test_schedule_text_is_in_the_newest_reply():
    expected = {
        5: "Online session 2 (18:30~19:30)",
        12: "Time: 11:30 ~ 12:30",
        36: "08:30 – 09:10: CSI",
        39: "off-offline discussion from 9:00 to 10:00 AM",
        41: "10.6.1.2:",
        20: "off-offline discussion at 10:45 today",
    }
    for n, text in expected.items():
        assert text in newest_reply(BY_N[n]["body"]), n
    # The older plan quoted under #36 is not part of its newest reply.
    assert "08:30 – 08:40: SRS" not in newest_reply(BY_N[36]["body"])


def test_quote_styles_cut():
    assert newest_reply("Hi\n\nOK\n> On Oct 14, 2026, at 12:57 AM, X wrote:\n> old") == "Hi\nOK"
    assert newest_reply("Hi\n-----原始邮件-----\nold") == "Hi"
    assert newest_reply("Hi\nOriginal\n\nFrom: a\nold") == "Hi"
    assert newest_reply("Hi\n发件人: a\nold") == "Hi"
    assert newest_reply("Dear all, plan [...]") == "Dear all, plan"


def test_meeting_threads():
    tag = meeting_tag("ran1#126bis")
    assert tag == "126bis"
    assert is_meeting_thread("Re: [126bis-R20-MIMO] Email discussion on Rel-20 MIMO", tag)
    assert is_meeting_thread("[RAN1#126bis] [7] TDoc Revision", tag)
    assert not is_meeting_thread("Jeju On-site Updates – Lost & Found", tag)
    assert not is_meeting_thread("Re: [126-R20-MIMO] last meeting", tag)
    assert is_meeting_thread("Re: [126b-R20-AI/ML] Email discussion on Rel-20 NR AIML", tag)
    assert is_meeting_thread("[RAN1#126b][10.5.5] t-doc revision R1-2608694", tag)
    assert not is_meeting_thread("Re: [Post-126bis-6GR-waveform] results", tag)
    kept = [mail for mail in parse_feed(FEED) if is_meeting_thread(mail.title, tag)]
    assert len(kept) == 48
    assert {mail.id for mail in kept} >= {BY_N[int(n)]["id"] for n in LABELS["mails"]}


def test_body_url_is_first_plain_part_without_header():
    page = (FIXTURE / "message_page_83dadb14.html").read_text(encoding="utf-8")
    url = body_url(page)
    assert url.startswith("https://list.etsi.org/scripts/wa.exe?A3=ind2610B&L=3GPP_TSG_RAN_WG1")
    assert "T=text%2Fplain" in url and "header=1" not in url
    assert body_url("<html>no parts</html>") is None


def _archive(fail=()):
    """Fake list.etsi.org: feed, a message page per mail, and its text part."""
    calls = []

    def handler(request):
        url = str(request.url)
        calls.append(url)
        if "?RSS&" in url:
            return httpx.Response(200, text=FEED)
        for mail in MAILS:
            if mail["id"] in url.lower() and "A2=" in url:
                if mail["n"] in fail:
                    raise httpx.ConnectError("TLS handshake stalled")
                part = mail["body_url"].replace("https://list.etsi.org", "")
                return httpx.Response(200, text=f'<iframe src="x"></iframe><a href="{part}&amp;header=1">text</a>')
        for mail in MAILS:
            if url == mail["body_url"]:
                return httpx.Response(200, text=mail["body"])
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_collect_fetches_only_cut_previews():
    http, calls = _archive()
    mails = collect("126bis", http=http, sleep=lambda _: None)
    assert len(mails) == 48
    assert all(mail.reply for mail in mails)
    full = {mail.id for mail in mails if mail.full_body}
    need = {BY_N[n]["id"] for n in LABELS["need_full_body"]}
    assert full == need - {BY_N[n]["id"] for n in (13, 22)}  # not 126bis threads
    assert len(calls) == 1 + 2 * len(full)
    by_id = {mail.id: mail for mail in mails}
    assert "I will be always there before 11:00 AM" in by_id[BY_N[39]["id"]].reply


def test_collect_skips_seen_and_leaves_failures_for_next_run():
    http, calls = _archive(fail={5})
    seen = {BY_N[n]["id"] for n in range(10, 51)}
    sleeps = []
    mails = collect("126bis", seen, http=http, sleep=sleeps.append)
    assert [mail.id for mail in mails] == [BY_N[n]["id"] for n in range(1, 10)]
    failed = next(mail for mail in mails if mail.id == BY_N[5]["id"])
    assert failed.reply is None and not failed.full_body
    assert 15.0 in sleeps  # one later retry before giving up
    assert all(gap > 0 for gap in sleeps)


def test_collect_budget():
    http, calls = _archive()
    mails = collect("126bis", http=http, sleep=lambda _: None, max_bodies=2)
    assert sum(mail.full_body for mail in mails) == 2
    assert len(calls) == 1 + 2 * 2


def test_feed_failure_raises():
    http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    with pytest.raises(httpx.HTTPStatusError):
        collect("126bis", http=http, sleep=lambda _: None)


def test_threads_from_chair_notes_name_the_agenda_item():
    from working_groups.ran1.email_sessions import thread_index, thread_of

    chair = json.loads((FIXTURE / "email_threads_chair.json").read_text(encoding="utf-8"))
    index = thread_index(chair["threads"])
    found = {mail["n"]: thread_of(mail["title"], index) for mail in MAILS}
    # Every email discussion mail; the rest are a TDoc revision notice, an LS and lost & found.
    assert [n for n, thread in found.items() if thread is None] == [11, 13, 22]
    agenda = {n: thread["agenda_item"] for n, thread in found.items() if thread}
    assert agenda[4] == "10.5.1.2"  # "Re: [126bis-R20-6GR-PRACH]", subject cut to the tag
    assert agenda[7] == "10.5.4.2"  # "…scheduling]Email Discussion for AI 10.5.4.2", rewritten after the tag
    assert agenda[14] == "9.3"  # "回复: [126bis-R20-A-IoT Phase 2] …"; 9.3.1 is only in the body
    assert {n: agenda[n] for n in (5, 12, 36, 41, 39, 20, 6, 17, 18, 15)} == {
        5: "10.5.2.2", 12: "10.5.2.3", 36: "9.2", 41: "10.6.1.1", 39: "10.5.3.3",
        20: "9.4", 6: "9.5", 17: "10.5.4.2", 18: "10.5.1.3", 15: "10.3.1",
    }
    assert thread_of("Re: [126BIS-R20-mimo]  Email discussion", index)["agenda_item"] == "9.2"


WEEK = parse_feed((FIXTURE / "feed_rss2_week.xml").read_text(encoding="utf-8"))
AGENDA = {item["agenda_item"] for item in json.loads((FIXTURE / "agenda_items.json").read_text(encoding="utf-8"))["agenda_items"]}


def _index():
    from working_groups.ran1.email_sessions import thread_index

    return thread_index(json.loads((FIXTURE / "email_threads_chair.json").read_text(encoding="utf-8"))["threads"])


@pytest.mark.parametrize("title, agenda_item, match", [
    ("Re: [126bis-R20-MIMO] Email discussion on Rel-20 MIMO", "9.2", "tag"),
    # The note's line reads "Ema-il discussion"; the mails write the meeting "126b".
    ("Re: [126b-R20-AI/ML] Email discussion on Rel-20 NR AIML air interface Ph2", "9.1", "tag"),
    ("Re: [126b-R20-6GR-ISAC-Evaluations] Email discussion", "10.8.1", "tag"),
    ("[126bis-R19-UE feature] Email discussion on Rel-19 UE feature", "8.3", "tag"),
    # The FL of 10.3.2 kept the tag of meeting #126.
    ("Re: [126-R20-6GR-Modulation, joint channel coding and modulation] Email discussion", "10.3.2", "earlier_meeting"),
])
def test_thread_tag_variants(title, agenda_item, match):
    from working_groups.ran1.email_sessions import thread_of

    thread = thread_of(title, _index())
    assert (thread["agenda_item"], thread["match"]) == (agenda_item, match)


@pytest.mark.parametrize("title", [
    "Re: [Post-126-6GR-waveform] UL Low PAPR results collection",  # after meeting #126
    "Re: [126-R20-6GR-Something new] Email discussion",  # #126 thread with no #126bis twin
    "Re: [RAN1#126bis] Meeting management",
    "[6G modulation] Open-source link-level simulator with AMC",
])
def test_not_a_thread_of_this_meeting(title):
    from working_groups.ran1.email_sessions import thread_of

    assert thread_of(title, _index()) is None


def test_week_threads():
    from working_groups.ran1.email_sessions import thread_of

    index = _index()
    matches = [(thread_of(mail.title, index) or {}).get("match") for mail in WEEK]
    assert len(WEEK) == 268
    assert matches.count("tag") == 227
    assert matches.count("earlier_meeting") == 4
    # The rest: Post-126 threads, notices, and TDoc revisions that state an item instead.
    rest = [mail for mail, match in zip(WEEK, matches) if match is None]
    assert len(rest) == 37
    assert not any("email discussion" in mail.title.lower() for mail in rest)


def test_mail_agenda_items_in_the_test_set():
    from working_groups.ran1.email_sessions import agenda_of

    index = _index()
    got = {
        mail["n"]: agenda_of(mail["title"], newest_reply(mail["body"]), index, AGENDA)
        for mail in MAILS
    }
    items = {n: result["agenda_items"] for n, result in got.items()}
    assert items[14] == ["9.3.1"] and items[24] == ["9.3.1"]  # "9.3.1" alone on the first line
    assert items[25] == ["9.3.3"] and items[34] == ["9.3.3"]  # "[9.3.3]"
    assert items[30] == ["9.4.1"]  # "for agenda item 9.4.1" in the 9.4 thread
    assert items[35] == ["10.5.3.1"]  # drafts/10.5.3 (CSI acquisition)/… : the parent folder adds nothing
    assert items[10] == ["8.2"]
    assert items[11] == ["7"] and got[11]["match"] == "stated"  # "[RAN1#126bis] [7] TDoc Revision"
    assert items[13] == [] and items[22] == []
    # Only #41 names other items: it plans the joint 10.6.x offline session.
    assert {n: result["other"] for n, result in got.items() if result["other"]} == {41: ["10.6.1.2", "10.6.3"]}
    # A labelled session is a session of the mail's item, or of an item it names.
    grid = {g["id"]: g for g in json.loads((FIXTURE / "grid.json").read_text(encoding="utf-8"))["sessions"]}
    for n, labelled in LABELS["mails"].items():
        result = got[int(n)]
        mine = result["agenda_items"] + result["other"]
        for item in labelled:
            if item["kind"] == "tentative":
                assert item["agenda_item"] in mine, n
                continue
            for sid in [item.get("session")] + item.get("candidates", []):
                if sid:
                    assert any(_covers(grid[sid], ai) for ai in mine), (n, sid, mine)


def _covers(session, ai):
    """The grid cell is about ``ai``: its items, "10.6.x" for all of 10.6, or the item inferred for "MIMO"."""
    stated = re.split(r"[,/]\s*", session["agenda_item"] or "") + session["inferred_agenda_items"]
    for item in filter(None, (s.strip() for s in stated)):
        root = item[:-2] if item.endswith(".x") else item
        if ai == root or ai.startswith(root + ".") or root.startswith(ai + "."):
            return True
    return False


@pytest.mark.parametrize("title, body, items, match", [
    ("Updated tdoc - AI 10.5.2.2 DL transmission schemes for PDSCH", "", ["10.5.2.2"], "stated"),
    ("Updated tdoc - NTN requirements and design 10.7.1", "", ["10.7.1"], "stated"),
    ("[RAN1#126b][10.5.5] t-doc revision R1-2608694 from R1-2607476", "", ["10.5.5"], "stated"),
    ("Tdoc revision", "Revised R1-2608001 for AI 10.6.2.", ["10.6.2"], "stated"),
    # Proposal, section and version numbers are not agenda items.
    ("Re: [126bis-R20-6GR-PRACH]", "Proposal 3.1.1-4 in Section 6.1.1.3 of v33", ["10.5.1.2"], "tag"),
    # A number outside the thread's item is a reference, not the mail's item.
    ("Re: [126bis-R20-6GR-PRACH]", "Aligned with AI 10.5.2.1.", ["10.5.1.2"], "tag"),
    # Not on this meeting's agenda.
    ("Updated tdoc - AI 10.9.9 Something", "", [], None),
    # A number alone, in the subject or the body.
    ("Offline plan 8.2", "", ["8.2"], "stated"),
    ("Offline plan", "8.2\nDear all, the offline is at 15:00.", ["8.2"], "stated"),
    ("Offline plan", "Dear all, 30min for 10.5.4.1 after the break.", ["10.5.4.1"], "stated"),
    ("Offline plan", "R1-2608001 (10.5.2.2) revised", ["10.5.2.2"], "stated"),
    # A word says the number is something else.
    ("Offline plan", "See Section 8.2 and Clause 8.1 of TS 38.213, Table 9.1, v10.2", [], None),
    ("Offline plan", "Proposal 3.1.1-4, observation 3.1.2, 0.3 – 1.62 dB, http://10.10.10.10/ftp", [], None),
])
def test_stated_agenda_items(title, body, items, match):
    from working_groups.ran1.email_sessions import agenda_of

    result = agenda_of(title, body, _index(), AGENDA)
    assert (result["agenda_items"], result["match"]) == (items, match)


def test_reference_outside_the_thread_is_reported():
    from working_groups.ran1.email_sessions import agenda_of

    result = agenda_of("Re: [126bis-R20-6GR-PRACH]", "Aligned with AI 10.5.2.1.", _index(), AGENDA)
    assert result["other"] == ["10.5.2.1"]


def test_no_agenda_trusts_no_number():
    from working_groups.ran1.email_sessions import agenda_of

    index = _index()
    assert agenda_of("Updated tdoc - AI 10.5.2.2", "", index, set())["agenda_items"] == []
    assert agenda_of("Re: [126bis-R20-A-IoT Phase 2] x", "[9.3.3]", index, set())["agenda_items"] == ["9.3"]


def test_untagged_tdoc_notices_state_their_item():
    from working_groups.ran1.email_sessions import agenda_of, thread_of

    index = _index()
    rest = [mail for mail in WEEK if thread_of(mail.title, index) is None]
    stated = {
        mail.title: agenda_of(mail.title, newest_reply(mail.snippet), index, AGENDA)["agenda_items"]
        for mail in rest
    }
    notices = {t: items for t, items in stated.items() if re.search(r"t-?doc|\btoc\b", t, re.I)}
    assert len(notices) == 20
    assert all(len(items) == 1 for items in notices.values())
    assert stated["Tdoc revision"] == ["9.5.1"]  # "(Agenda 9.5.1)" on the first line
    assert stated["Updated tdoc - NTN requirements and design 10.7.1"] == ["10.7.1"]
    # Post-meeting and admin mails state no item of this meeting.
    assert all(not items for t, items in stated.items() if t not in notices)


def test_labelled_sessions_come_after_the_mail_in_meeting_time():
    # Mail dates carry the sender's zone; "tomorrow" in #32 (00:42 +0800) is
    # Wednesday 01:42 in Jeju, and the session it names is that evening.
    from datetime import datetime, timedelta
    from email.utils import parsedate_to_datetime
    from zoneinfo import ZoneInfo

    seoul = ZoneInfo("Asia/Seoul")
    days = {"Mon": 12, "Tue": 13, "Wed": 14, "Thu": 15, "Fri": 16}
    grid = {g["id"]: g for g in json.loads((FIXTURE / "grid.json").read_text(encoding="utf-8"))["sessions"]}
    for n, labelled in LABELS["mails"].items():
        sent = parsedate_to_datetime(BY_N[int(n)]["date"]).astimezone(seoul)
        for item in labelled:
            for sid in filter(None, [item.get("session")] + item.get("candidates", [])):
                hour, minute = map(int, grid[sid]["start"].split(":"))
                start = datetime(2026, 10, days[sid[:3]], hour, minute, tzinfo=seoul)
                assert sent < start < sent + timedelta(days=2), (n, sid, sent)


def test_informal_items_have_no_room_of_their_own():
    # Online and offline sessions are held in a room; a meeting outside, in
    # front of or near one, or with no room, is off-offline.
    for n, labelled in LABELS["mails"].items():
        for item in labelled:
            if item["kind"] == "informal":
                assert "session" not in item, n


def test_tentative_items_sit_in_the_room_of_the_items_sessions():
    # #15 "online session … from 12:00" for 10.3.1: the grid has no 10.3.1
    # then, and Yeongju A Hall, where every 10.3.1 online is, has AI 8.1.
    # The FL arranged the slot before the schedule caught up; the 8.1 cell
    # shows "maybe 10.3.1".
    from collections import Counter

    grid = {g["id"]: g for g in json.loads((FIXTURE / "grid.json").read_text(encoding="utf-8"))["sessions"]}
    tentative = [(n, item) for n, items in LABELS["mails"].items() for item in items if item["kind"] == "tentative"]
    assert [n for n, _ in tentative] == ["3", "15"]
    for n, item in tentative:
        cell = grid[item["session"]]
        ai = item["agenda_item"]
        assert not _covers(cell, ai), n  # the grid gives the slot to another item
        rooms = Counter(g["room"] for g in grid.values() if _covers(g, ai) and g["agenda_item"])
        assert cell["room"] == rooms.most_common(1)[0][0], (n, rooms)
