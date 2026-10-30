"""Agenda items for cells whose schedule gives no agenda number.

A cell such as "MIMO" or "AI/ML" names its topic but not its agenda item.
An LLM reads the label against this meeting's agenda and names the items a
delegate would read it as. The answers are kept per meeting in
``docs/ran1/agenda_label_map/<meeting>.jsonl``, one line per label, including
labels that name no item ("TBD"), so a label is asked once and keeps its
answer on every later run. A new meeting starts a new list; a label the list
does not have yet is asked when it first appears.

The result is used only to match the cell in the filter and to open its
agreements. It is never shown as the cell's agenda item.

Nothing is inferred without this meeting's agenda, and a failed call writes
nothing, so the label is asked again on the next run. The mechanism is shared
with the other WGs (shared/agenda_labels.py); RAN1 keeps its own examples and
reads its agenda from agenda_item_description.json.
"""

from __future__ import annotations

from pathlib import Path

from shared.agenda_labels import (  # noqa: F401 - the RAN1 names tests and callers use
    Judge,
    cell_label,
    LABEL_JUDGE_MODEL,
    LABEL_JUDGE_THINKING,
    label_key,
    label_map_path,
    load_label_map,
    normalize_agenda_items,
)
from shared import agenda_labels

from .agenda_descriptions import (
    DEFAULT_JSON_PATH,
    description_meeting_id,
    load_agenda_description_map,
)

LABEL_MAP_DIR = Path("docs/ran1/agenda_label_map")

LABEL_JUDGE_INSTRUCTION = """You map session labels from a 3GPP RAN1 meeting schedule to items of that meeting's agenda.

For each label, answer: when a delegate of this meeting reads this label on the schedule, which agenda items would they read the session as covering? Return every agenda item the label can be read as, judged by how RAN1 uses the name, not by shared words.

Include
- Items whose subject the label names. When that subject runs across several releases or studies, include each of them.
- Abbreviations, work-item short names and spelling variants count as the name (AI/ML, A-IoT, LP-WUS, 6G = 6GR, R20 = Rel-20 = Release 20).

Exclude
- Items where the label's word appears only as one aspect of a different subject. A label that names a work item does not cover a sub-clause of another study that happens to use the same word.
- Maintenance items, unless the label says maint/maintenance, names an earlier release than the current one (the current release is the one the agenda's new work is under), or names the maintenance item's own subject (e.g. "UE features" for "Maintenance on UE features"). A maintenance label goes to the maintenance item that names its feature; when no maintenance item names it, it goes to the catch-all maintenance item of that release (e.g. "Maintenance on others"), or the pre-release item for older releases.
- Labels that name no agenda subject: placeholders (TBD, To be assigned by <name>), running order (Sweep, Session reports, Any other open issues), breaks and meals. These get no items.

Examples (by item title; numbers change between meetings)
- "MIMO" -> the NR MIMO Phase N work item only. Not "MIMO operation" under the 6GR multi-antenna study, not maintenance of an earlier MIMO phase.
- "NTN" -> every NTN item: GNSS resilient NR-NTN operation, NR NTN with downlink compensation, the 6GR NTN item. Not NTN maintenance.
- "6G waveform" -> the 6GR waveform item. Not A-IoT "R2D signals, channels, waveform and procedures", not ISAC "Waveform and frame structure".
- "TBD" -> none.

A label is a cell's title, followed by the cell's other lines after " ⏎ " when it has any; read the whole cell. Echo each label exactly as given.

Use agenda item numbers exactly as listed. When both a parent and its children apply, you may give the parent alone. For each item give a one-line reason. When nothing applies, return an empty list and say why in one line."""


def _agenda_for_meeting(meeting_id: str, agenda_path: Path) -> dict[str, str]:
    """This meeting's agenda, or nothing when it is missing or another meeting's."""
    if description_meeting_id(agenda_path) != meeting_id:
        return {}
    return load_agenda_description_map(agenda_path, meeting_id=meeting_id)


def apply_inferred_agenda_items(
    sessions: list,
    meeting_id: str | None,
    *,
    agenda_path: Path = DEFAULT_JSON_PATH,
    directory: Path = LABEL_MAP_DIR,
    judge: Judge | None = None,
) -> list:
    """Set ``inferred_agenda_items`` on sessions without an agenda item."""
    if not meeting_id or not any(not s.agenda_item and cell_label(s) for s in sessions):
        return sessions
    return agenda_labels.apply_inferred_agenda_items(
        sessions, meeting_id, _agenda_for_meeting(meeting_id, agenda_path),
        directory=directory, wg_name="RAN1", instruction=LABEL_JUDGE_INSTRUCTION, judge=judge)
