from types import SimpleNamespace

from shared.renderer import _agenda_description_popup_lines
from working_groups.ran1 import drafts_folders

ROOT = "https://x/TSGR1_127/Inbox/drafts/"
TREE = {
    ROOT: ["10 (FS_6G_Radio)", "10.5.2 (MIMO operation)", "9.2 (NR_MIMO_Ph6)"],
    ROOT + "10/": [],
    ROOT + "10.5.2/": ["10.5.2.2 (DL transmission scheme for shared)"],
}


def folder_listing(calls):
    def list_folders(url):
        calls.append(url)
        return [{"name": name, "url": ROOT + name.split()[0] + "/"} for name in TREE.get(url, [])]
    return list_folders


def session():
    level = lambda n: {"agenda_item": n, "description": f"title {n}"}
    item = {**level("10.5.2.2"), "hierarchy": [level("10"), level("10.5"), level("10.5.2"), level("10.5.2.2")]}
    return SimpleNamespace(agenda_descriptions=[item], agenda_item="10.5.2.2", description="title 10.5.2.2")


def test_links_nested_folders_and_lists_only_for_unknown_items(monkeypatch):
    calls = []
    monkeypatch.setattr(drafts_folders, "list_inbox_subfolders", folder_listing(calls))
    urls = ["https://x/TSGR1_127/Agenda/"]

    [s] = drafts_folders.apply([session()], "ran1#127", urls, offline=False)
    item = s.agenda_descriptions[0]
    assert item["drafts_url"] == ROOT + "10.5.2.2/"
    assert [lv.get("drafts_url") for lv in item["hierarchy"]] == [ROOT + "10/", None, ROOT + "10.5.2/", ROOT + "10.5.2.2/"]
    assert set(calls) == {ROOT, ROOT + "10/", ROOT + "10.5.2/"}

    # 10.5 has no folder: the next build lists again, but known items are not looked for.
    calls.clear()
    drafts_folders.apply([session()], "ran1#127", urls, offline=False)
    assert calls == [ROOT, ROOT + "10/"]

    calls.clear()
    drafts_folders.apply([session()], "ran1#127", urls, offline=True)
    assert calls == []

    html = "".join(_agenda_description_popup_lines(s))
    assert html.count('title="Drafts folder"') == 3
    assert f'href="{ROOT}10.5.2.2/"' in html


def test_x_item_is_its_parent_folder():
    assert drafts_folders.tree_numbers({"agenda_item": "10.6.x", "children": [{"agenda_item": "10.6.1"}]}) == ["10.6", "10.6.1"]


def test_says_when_the_meeting_folder_is_unknown(capsys):
    drafts_folders.apply([session()], "ran1#127", [], offline=False)
    assert "no meeting folder" in capsys.readouterr().out
