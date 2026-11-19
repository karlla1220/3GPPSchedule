"""Links from the popup's agenda tree to each item's folder under Inbox/drafts.

FLs put their drafts in one folder per agenda item, nested by number and
named by hand: ``drafts/10.5.2 (MIMO operation)/10.5.2.2 (DL transmission
scheme for shared)/``. The name is not the agenda's title, so the folder is
found by its leading number in the FTP listing. The links go to the sync area
(``Meetings_3GPP_SYNC/RAN1/Inbox/drafts/``), which is current; the meeting's
archive folder lags it. The sync area keeps the last meeting's folders until
the next one, so it is listed only while the meeting is in progress.

Found folders are kept per meeting in ``docs/ran1/drafts_folders/<meeting>.json``
and the FTP is listed only for an item the list does not have yet. An item
with no folder is looked for again on the next build (FLs add folders during
the meeting); that costs the root listing and its ancestors' listings.
Failing to list is not an error: the links are a convenience.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from shared.navigation import meeting_phase

from .downloader import list_inbox_subfolders

DIRECTORY = Path("docs/ran1/drafts_folders")

_NUMBER = re.compile(r"\s*(\d+(?:\.\d+)*)")


def map_path(meeting_id: str) -> Path:
    return DIRECTORY / f"{meeting_id.replace('#', '_')}.json"


def folder_number(agenda_item) -> str:
    """An "x" item is its parent's folder: 10.6.x is 10.6 (as the popup tree shows it)."""
    return re.sub(r"\.[xX]$", "", str(agenda_item or ""))


def tree_numbers(item: dict) -> list[str]:
    """Agenda numbers one popup tree entry shows: the item, its parents, an "x" item's children."""
    entries = [item, *(item.get("hierarchy") or []), *(item.get("children") or [])]
    return [n for n in (folder_number(e.get("agenda_item")) for e in entries) if n]


def find(wanted: set[str], known: dict[str, str], root: str) -> dict[str, str]:
    """``known`` plus the folders found for the ``wanted`` numbers it lacks.

    Lists the root, then the deepest found folder above each missing number,
    until no listed folder leads further. Every folder seen is kept.
    """
    found = dict(known)
    listed: set[str] = set()

    def missing():
        return {n for n in wanted if n not in found}

    def visit(url):
        listed.add(url)
        for folder in list_inbox_subfolders(url):
            match = _NUMBER.match(folder["name"])
            if match:
                found.setdefault(match[1], folder["url"])

    if not missing():
        return found
    visit(root)
    while True:
        below = {found[k] for n in missing() for k in list(found)
                 if n.startswith(k + ".") and found[k] not in listed}
        if not below:
            return found
        for url in below:
            visit(url)


def apply(schedule, meeting_sync: str | None, *, offline: bool, now=None) -> None:
    """Set ``drafts_url`` on the agenda tree entries of the schedule's sessions whose folder is known."""
    if not schedule.meeting_id or schedule.is_demo:
        return
    sessions = [s for day in schedule.days for s in day.sessions]
    root = meeting_sync.rstrip("/") + "/Inbox/drafts/" if meeting_sync else None
    path = map_path(schedule.meeting_id)
    try:
        known = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = {}
    if root:  # a folder listed under another root (the archive, before) is looked up again
        known = {k: v for k, v in known.items() if v.startswith(root)}
    wanted = {n for s in sessions for item in s.agenda_descriptions for n in tree_numbers(item)}
    in_progress = meeting_phase(schedule.starts_on, schedule.ends_on, schedule.timezone or "UTC", now) == "In progress"
    if not root and wanted:
        print("  Drafts folders: no meeting_sync in config, no new links")
    if not offline and root and in_progress and wanted - known.keys():
        try:
            found = find(wanted, known, root)
        except Exception as e:  # a missing link is not worth a failed build
            print(f"  Warning: could not list drafts folders: {e}")
            found = known
        if found != known:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(found, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            print(f"  Drafts folders: {len(found) - len(known)} new")
            known = found

    def linked(entry: dict) -> dict:
        number = folder_number(entry.get("agenda_item"))
        return {**entry, "drafts_url": known[number]} if number in known else entry

    for session in sessions:
        session.agenda_descriptions = [
            {**linked(item),
             "hierarchy": [linked(level) for level in item.get("hierarchy") or []],
             **({"children": [linked(c) for c in item["children"]]} if item.get("children") else {})}
            for item in session.agenda_descriptions
        ]
