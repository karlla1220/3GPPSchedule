from datetime import datetime, timezone
from types import SimpleNamespace

from shared.renderer import _agenda_description_popup_lines
from working_groups.ran1 import drafts_folders

SYNC = "https://x/Meetings_3GPP_SYNC/RAN1/"
ROOT = SYNC + "Inbox/drafts/"
TREE = {
    ROOT: ["10 (FS_6G_Radio)", "10.5.2 (MIMO operation)", "9.2 (NR_MIMO_Ph6)"],
    ROOT + "10/": [],
    ROOT + "10.5.2/": ["10.5.2.2 (DL transmission scheme for shared)"],
}
DURING = datetime(2026, 11, 18, 3, tzinfo=timezone.utc)


def folder_listing(calls):
    def list_folders(url):
        calls.append(url)
        return [{"name": name, "url": ROOT + name.split()[0] + "/"} for name in TREE.get(url, [])]
    return list_folders


def schedule():
    level = lambda n: {"agenda_item": n, "description": f"title {n}"}
    item = {**level("10.5.2.2"), "hierarchy": [level("10"), level("10.5"), level("10.5.2"), level("10.5.2.2")]}
    session = SimpleNamespace(agenda_descriptions=[item], agenda_item="10.5.2.2", description="title 10.5.2.2")
    return SimpleNamespace(days=[SimpleNamespace(sessions=[session])], meeting_id="ran1#127", is_demo=False,
                           starts_on="2026-11-16", ends_on="2026-11-20", timezone="Asia/Seoul")


def session_of(s):
    return s.days[0].sessions[0]


def test_links_sync_folders_and_lists_only_for_unknown_items(monkeypatch):
    calls = []
    monkeypatch.setattr(drafts_folders, "list_inbox_subfolders", folder_listing(calls))

    s = schedule()
    drafts_folders.apply(s, SYNC, offline=False, now=DURING)
    item = session_of(s).agenda_descriptions[0]
    assert item["drafts_url"] == ROOT + "10.5.2.2/"
    assert [lv.get("drafts_url") for lv in item["hierarchy"]] == [ROOT + "10/", None, ROOT + "10.5.2/", ROOT + "10.5.2.2/"]
    assert set(calls) == {ROOT, ROOT + "10/", ROOT + "10.5.2/"}

    # 10.5 has no folder: the next build lists again, but known items are not looked for.
    calls.clear()
    drafts_folders.apply(schedule(), SYNC, offline=False, now=DURING)
    assert calls == [ROOT, ROOT + "10/"]

    calls.clear()
    drafts_folders.apply(schedule(), SYNC, offline=True, now=DURING)
    assert calls == []

    html = "".join(_agenda_description_popup_lines(session_of(s)))
    assert html.count('title="Drafts folder"') == 3
    assert f'href="{ROOT}10.5.2.2/"' in html


def test_sync_is_not_listed_outside_the_meeting(monkeypatch):
    """Before the first day the sync area still holds the last meeting's folders."""
    calls = []
    monkeypatch.setattr(drafts_folders, "list_inbox_subfolders", folder_listing(calls))
    drafts_folders.apply(schedule(), SYNC, offline=False, now=datetime(2026, 11, 14, tzinfo=timezone.utc))
    assert calls == []


def test_another_meetings_folders_are_not_used(monkeypatch):
    drafts_folders.PATH.write_text('{"meeting_id": "ran1#126bis", "folders": {"10.5.2.2": "https://old/"}}')
    monkeypatch.setattr(drafts_folders, "list_inbox_subfolders", folder_listing([]))
    s = schedule()
    drafts_folders.apply(s, SYNC, offline=False, now=DURING)
    assert session_of(s).agenda_descriptions[0]["drafts_url"] == ROOT + "10.5.2.2/"
    assert '"ran1#127"' in drafts_folders.PATH.read_text()


def test_x_item_is_its_parent_folder():
    assert drafts_folders.tree_numbers({"agenda_item": "10.6.x", "children": [{"agenda_item": "10.6.1"}]}) == ["10.6", "10.6.1"]


def test_says_when_the_sync_folder_is_unknown(capsys):
    drafts_folders.apply(schedule(), None, offline=False, now=DURING)
    assert "no meeting_sync" in capsys.readouterr().out
