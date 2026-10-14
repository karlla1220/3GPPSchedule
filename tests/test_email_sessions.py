import json
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
    kept = [mail for mail in parse_feed(FEED) if is_meeting_thread(mail.title, tag)]
    assert len(kept) == 48
    assert {mail.id for mail in kept} >= {item["id"] for item in LABELS["schedule"]}


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
