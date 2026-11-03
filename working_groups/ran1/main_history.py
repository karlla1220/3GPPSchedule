"""Every Main Schedule cell text seen in a meeting, by time slot.

Vice-chair documents carry copies of Main Schedule tables pasted in at some
point. The copy does not follow the chair's later edits, so a cold merge
would read the old Main text as vice-chair detail and put it back
(RAN1#126bis: Hiroki v08's Friday Tamna BC Hall and Yeongju A Hall were
Main v02). A vice-chair cell whose text is exactly a Main text seen for the
same slot in any version so far is such a copy, and is left out of the
merge. Any other cell (one the vice chair edited, its own tables) stays.

The history is a record of the source documents, not of our merges, so it
lives apart from the slot states (``docs/ran1/main_history/``) and a
force-deploy, which wipes those, keeps it. Each run adds the Main version
it reads; versions it never saw are missing, which only leaves their copies
in, never drops a vice-chair edit. Only distinct texts are kept.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from shared.json_files import write_json

HISTORY_DIR = Path("docs/ran1/main_history")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def slot_key(day: str, time_block_index: int) -> str:
    return f"{day}_{time_block_index:02d}"


def _path(meeting_id: str) -> Path:
    name = re.sub(r"[^0-9A-Za-z]+", "_", meeting_id).strip("_").lower()
    return HISTORY_DIR / f"{name}.json"


def load(meeting_id: str) -> dict[str, set[str]]:
    try:
        data = json.loads(_path(meeting_id).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("meeting_id") != meeting_id:
        return {}
    return {key: set(texts) for key, texts in (data.get("slots") or {}).items()}


def add(history: dict[str, set[str]], cells) -> dict[str, set[str]]:
    """``history`` with the texts of ``cells`` (Main Schedule ``CellData``) added."""
    merged = {key: set(texts) for key, texts in history.items()}
    for cell in cells:
        text = normalize(cell.text)
        if text:
            merged.setdefault(slot_key(cell.day, cell.time_block_index), set()).add(text)
    return merged


def record(meeting_id: str | None, cells) -> dict[str, set[str]]:
    """Add this run's Main cells to the meeting's history and return it.

    Without a meeting id nothing is stored; the current cells alone are
    returned, which drops only exact copies of the current Main.
    """
    if not meeting_id:
        return add({}, cells)
    history = load(meeting_id)
    merged = add(history, cells)
    if merged != history:
        write_json(_path(meeting_id), {
            "meeting_id": meeting_id,
            "slots": {key: sorted(texts) for key, texts in sorted(merged.items())},
        })
    return merged
