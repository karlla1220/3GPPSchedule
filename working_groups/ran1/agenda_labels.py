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
nothing, so the label is asked again on the next run.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .agenda_descriptions import (
    DEFAULT_JSON_PATH,
    description_meeting_id,
    load_agenda_description_map,
)

LABEL_MAP_DIR = Path("docs/ran1/agenda_label_map")
LABEL_JUDGE_MODEL = "gemini-3.8-flash"
LABEL_JUDGE_THINKING = "low"

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

Use agenda item numbers exactly as listed. When both a parent and its children apply, you may give the parent alone. For each item give a one-line reason. When nothing applies, return an empty list and say why in one line."""

LABEL_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "agenda_item": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "required": ["agenda_item", "reason"],
                        },
                    },
                    "note": {"type": "string"},
                },
                "required": ["label", "items", "note"],
            },
        }
    },
    "required": ["labels"],
}

# labels, agenda {number: description} → {label: {"items": [{agenda_item, reason}], "note"}}
Judge = Callable[[list[str], dict[str, str]], dict[str, dict]]


def label_key(name: str | None) -> str:
    """The label as written, with runs of whitespace collapsed."""
    return " ".join((name or "").split())


def label_map_path(meeting_id: str, directory: Path = LABEL_MAP_DIR) -> Path:
    return directory / f"{meeting_id.replace('#', '_')}.jsonl"


def load_label_map(path: Path) -> dict[str, list[str]]:
    """Agenda items by label. A later line for the same label wins."""
    result: dict[str, list[str]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        label = record.get("label")
        items = record.get("agenda_items")
        if isinstance(label, str) and isinstance(items, list):
            result[label] = [str(item) for item in items]
    return result


def _number_key(value: str) -> list[int | str]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]


def normalize_agenda_items(raw: list[str], agenda: dict[str, str]) -> list[str]:
    """Keep the agenda's own numbers, and only the top of each subtree.

    "9.1" already covers "9.1.2" for both the filter and the agreements,
    so a child of an item in the list is dropped.
    """
    known = {item.strip().rstrip(".") for item in raw} & set(agenda)
    kept = [
        item for item in known
        if not any(item.startswith(other + ".") for other in known)
    ]
    return sorted(kept, key=_number_key)


def gemini_judge(labels: list[str], agenda: dict[str, str]) -> dict[str, dict]:
    """Ask Gemini which agenda items each label is read as."""
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    client = genai.Client(api_key=api_key, http_options={"timeout": 120_000})
    prompt = (
        "Agenda:\n"
        + "\n".join(f"{number}\t{description}" for number, description in
                    sorted(agenda.items(), key=lambda kv: _number_key(kv[0])))
        + "\n\nLabels:\n"
        + "\n".join(json.dumps(label, ensure_ascii=False) for label in labels)
    )
    response = client.models.generate_content(
        model=LABEL_JUDGE_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=LABEL_JUDGE_INSTRUCTION,
            response_mime_type="application/json",
            response_json_schema=LABEL_JUDGE_SCHEMA,
            thinking_config=types.ThinkingConfig(thinking_level=LABEL_JUDGE_THINKING),
        ),
    )
    answers = json.loads(response.text.strip()).get("labels") or []
    return {
        answer["label"]: answer
        for answer in answers
        if isinstance(answer, dict) and isinstance(answer.get("label"), str)
    }


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
    judge: Judge = gemini_judge,
) -> list:
    """Set ``inferred_agenda_items`` on sessions without an agenda item."""
    unnumbered = [s for s in sessions if not s.agenda_item and label_key(s.name)]
    if not meeting_id or not unnumbered:
        return sessions
    agenda = _agenda_for_meeting(meeting_id, agenda_path)
    if not agenda:
        print(f"  No agenda for {meeting_id}: cells without an AI stay unmapped")
        return sessions

    path = label_map_path(meeting_id, directory)
    mapping = load_label_map(path)
    new_labels = list(dict.fromkeys(
        label_key(s.name) for s in unnumbered if label_key(s.name) not in mapping
    ))
    if new_labels:
        print(f"  Mapping {len(new_labels)} new label(s) to agenda items (Gemini)...")
        try:
            answers = judge(new_labels, agenda)
        except Exception as e:
            # Nothing is written: the labels are asked again on the next run.
            print(f"  Warning: agenda label mapping failed: {e}")
            answers = {}
        decided_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        lines = []
        for label in new_labels:
            answer = answers.get(label)
            if answer is None:
                continue
            raw = [
                {"agenda_item": str(item.get("agenda_item", "")),
                 "reason": str(item.get("reason", ""))}
                for item in answer.get("items") or []
                if isinstance(item, dict)
            ]
            items = normalize_agenda_items([r["agenda_item"] for r in raw], agenda)
            mapping[label] = items
            lines.append(json.dumps({
                "label": label,
                "agenda_items": items,
                "raw": raw,
                "note": str(answer.get("note") or ""),
                "model": f"{LABEL_JUDGE_MODEL} ({LABEL_JUDGE_THINKING})",
                "decided_at": decided_at,
            }, ensure_ascii=False))
        if lines:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            print(f"  Recorded {len(lines)} label(s) in {path}")

    for session in unnumbered:
        session.inferred_agenda_items = list(mapping.get(label_key(session.name), []))
    return sessions
