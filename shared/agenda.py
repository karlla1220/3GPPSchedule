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


def is_descendant(item: str, ancestor: str) -> bool:
    return item != ancestor and item.startswith(ancestor + '.')


def group_label(item: str | None, descriptions: dict) -> str:
    """"NR Rel-20" for 8.2 when the agenda says "8 | NR Rel-20 - ..."; "AI 8" without one."""
    if not item:
        return ''
    top = item.split('.')[0]
    description = descriptions.get(top)
    return description.split(' - ')[0].strip().rstrip(' -–') if description else f'AI {top}'
