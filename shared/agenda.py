"""agenda.csv: item names, the popup's agenda tree and a cell's legend group.

Every WG but RAN1 reads the meeting's agenda.csv the same way and colours a cell
by the top item of its first agenda item (CLAUDE.md "셀 구조"); RAN1 keeps the
schedule's own group heads.
"""

from __future__ import annotations

import csv
import io
import re


def agenda_map(data: bytes) -> dict[str, str]:
    """agenda.csv published in each meeting's Agenda folder: "item","description".

    Malformed rows are skipped; descriptions are only an aid.
    """
    result = {}
    for row in csv.reader(io.StringIO(data.decode('utf-8-sig', errors='replace'))):
        if len(row) < 2:
            continue
        item, description = row[0].strip().rstrip('.'), ' '.join(row[1].replace('\xa0', ' ').split())
        if re.fullmatch(r'\d+(?:\.\d+)*', item) and description:
            result.setdefault(item, description)
    return result


def describe(item: str, agenda: dict) -> dict | None:
    """The popup's tree for an AI: the nearest listed item ("10.6.x" → 10.6) and its parents."""
    match = item
    while match not in agenda and '.' in match:
        match = match.rsplit('.', 1)[0]
    if match not in agenda:
        return None
    parts = match.split('.')
    path = ['.'.join(parts[:i]) for i in range(1, len(parts) + 1)]
    return {'agenda_item': item, 'matched_agenda_item': match, 'description': agenda[match],
            'hierarchy': [{'agenda_item': p, 'description': agenda.get(p)} for p in path]}


def written_ranges(items: list[str], ranges=()) -> list[str]:
    """The AI line's items: a range the source wrote stays one range.

    ``ranges`` are (label, members) pairs a WG read ("20.6.12 - 16" ->
    ("20.6.12–20.6.16", [20.6.12 … 20.6.16])). Where the members still
    present stand together in items, they show as the label; the filter and the
    popup's tree use the members (shared/agreement_assets.py reads the label back).
    """
    shown = list(items)
    for label, members in ranges:
        kept = [m for m in members if m in shown]
        if len(kept) < 2:
            continue
        at = shown.index(kept[0])
        if shown[at:at + len(kept)] == kept:
            shown[at:at + len(kept)] = [label]
    return shown


def is_descendant(item: str, ancestor: str) -> bool:
    return item != ancestor and item.startswith(ancestor + '.')


def _number_key(item: str) -> tuple[int, ...]:
    return tuple(int(part) for part in item.split('.'))


def group_label(item: str | None, descriptions: dict) -> str:
    """"NR Rel-20" for 8.2 when the agenda says "8 | NR Rel-20 - ...".

    An agenda without the top item names it by its first listed item (SA2 has
    "1.1 Opening of the meeting" but no "1"); "AI 8" when it lists nothing under 8.
    """
    if not item:
        return ''
    top = item.split('.')[0]
    description = descriptions.get(top)
    if not description:
        below = sorted((k for k in descriptions if k.startswith(top + '.')), key=_number_key)
        description = descriptions[below[0]] if below else None
    return description.split(' - ')[0].strip().rstrip(' -–') if description else f'AI {top}'
