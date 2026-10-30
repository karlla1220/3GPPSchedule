"""Agenda items for cells whose schedule gives no agenda number (CLAUDE.md "AI 없는 셀의 AI 추정").

A cell such as "MIMO" or "6G system parameter" names its topic but not its
agenda item. An LLM reads the label against this meeting's agenda and names the
items a delegate would read it as. The answers are kept per WG and meeting in
``docs/<wg>/agenda_label_map/<meeting>.jsonl``, one line per label, including
labels that name no item ("TBD"), so a label is asked once and keeps its answer
on every later run. A new meeting starts a new list.

The result is used only to match the cell in the filter (and, in RAN1, to open
its agreements). It is never shown as the cell's agenda item.

Nothing is inferred without the meeting's agenda, and a failed call writes
nothing, so the label is asked again on the next run.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

LABEL_MAP_ROOT = Path("docs")
LABEL_JUDGE_MODEL = "gemini-3.8-flash"
LABEL_JUDGE_THINKING = "low"

INSTRUCTION = """You map session labels from a 3GPP {wg} meeting schedule to items of that meeting's agenda.

For each label, answer: when a delegate of this meeting reads this label on the schedule, which agenda items would they read the session as covering? Return every agenda item the label can be read as, judged by how {wg} uses the name, not by shared words.

Include
- Items whose subject the label names. When that subject runs across several releases or studies, include each of them.
- Abbreviations, work-item short names and spelling variants count as the name (AI/ML, A-IoT, 6G = 6GR, R20 = Rel-20 = Release 20).

Exclude
- Items where the label's word appears only as one aspect of a different subject.
- Maintenance items, unless the label says maint/maintenance, names an earlier release than the current one, or names the maintenance item's own subject.
- Labels that name no agenda subject: placeholders (TBD, To be assigned by <name>), running order (Sweep, Revisions, Session reports, Any other open issues), breaks and meals. These get no items.

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


def label_map_dir(wg_id: str) -> Path:
    return LABEL_MAP_ROOT / wg_id / "agenda_label_map"


def label_map_path(meeting_id: str, directory: Path) -> Path:
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

    "9.1" already covers "9.1.2" for the filter (and RAN1's agreements),
    so a child of an item in the list is dropped.
    """
    known = {item.strip().rstrip(".") for item in raw} & set(agenda)
    kept = [
        item for item in known
        if not any(item.startswith(other + ".") for other in known)
    ]
    return sorted(kept, key=_number_key)


def gemini_judge(labels: list[str], agenda: dict[str, str], *, instruction: str) -> dict[str, dict]:
    """Ask Gemini which agenda items each label is read as."""
    from google import genai
    from google.genai import types

    from shared.llm_cells import has_key
    if not has_key():   # reads .env too
        raise RuntimeError("GEMINI_API_KEY not set")
    api_key = os.environ["GEMINI_API_KEY"]
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
            system_instruction=instruction,
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


def apply_inferred_agenda_items(
    sessions: list,
    meeting_id: str | None,
    agenda: dict[str, str] | None,
    *,
    directory: Path,
    wg_name: str,
    instruction: str | None = None,
    judge: Judge | None = None,
    ask: bool = True,
) -> list:
    """Set ``inferred_agenda_items`` on sessions without an agenda item or rule-found items.

    ``agenda`` is this meeting's {number: description}; without it nothing is
    inferred. With ask false only recorded answers apply (offline, LLM off).
    """
    # A WG's own rules may already have found items for a cell the schedule
    # numbers nowhere (RAN4 candidate lists, SA1 topic names); those stand.
    unnumbered = [s for s in sessions
                  if not s.agenda_item and not s.inferred_agenda_items and label_key(s.name)]
    if not meeting_id or not unnumbered:
        return sessions
    if not agenda:
        print(f"  No agenda for {meeting_id}: cells without an AI stay unmapped")
        return sessions
    if judge is None:
        text = instruction or INSTRUCTION.format(wg=wg_name)
        judge = lambda labels, items: gemini_judge(labels, items, instruction=text)  # noqa: E731

    path = label_map_path(meeting_id, directory)
    mapping = load_label_map(path)
    new_labels = list(dict.fromkeys(
        label_key(s.name) for s in unnumbered if label_key(s.name) not in mapping
    ))
    if new_labels and ask:
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


def infer_for_schedule(schedule, wg_id: str, wg_name: str, agenda: dict[str, str] | None, *,
                       enabled: bool) -> None:
    """The step a WG pipeline runs after building its schedule.

    ``enabled`` is false offline or when the WG's LLM fallback is off; recorded
    answers still apply then, and new labels wait for a run that may ask.
    """
    sessions = [s for day in schedule.days for s in day.sessions]
    apply_inferred_agenda_items(sessions, schedule.meeting_id, agenda, directory=label_map_dir(wg_id),
                                wg_name=wg_name, ask=enabled)
