"""Topic numbers to agenda items, from the meeting's topic list XLSX.

RAN4 cells name email-thread topics ("[101-A]", "[346]"), not agenda items.
The chairs publish a topic list each meeting (List_Topics_moderators-RAN4_120bis_final.xlsx):
one sheet per session, a row per topic with its title and the agenda items it
covers. Those are typed by hand ("8.2.1, 8.2.2, ,8.2.7", numbers, numbers with
a title glued on), so only number patterns are taken, and only those the
meeting's agenda.csv lists.

The "Summary agenda" column is where the moderator posts the summary, not
what the topic covers; it is left out.
"""
from __future__ import annotations

import io
import re

TOPIC = re.compile(r'\d{3}(?:-[A-Z])?')
NUMBER = re.compile(r'(?<![\w.-])\d{1,2}(?:\.\d{1,2})*(?![\d])')  # not "Rel-19"


def _columns(header) -> tuple[int | None, int | None]:
    """(title, agenda items) column positions of a sheet's header row."""
    names = [str(h or '').strip().lower() for h in header]
    items = next((i for i, n in enumerate(names) if n.startswith('ai') and 'summary' not in n), None)
    title = next((i for i, n in enumerate(names) if n == 'title'), 1 if len(names) > 1 else None)
    return title, items


def read_topics(data: bytes, agenda: dict | None) -> dict[str, dict]:
    """{"101-A": {"title": "6G system parameter (part I)", "agenda_items": ["8.2.1", ...]}}."""
    import openpyxl
    book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    topics = {}
    try:
        for sheet in book.worksheets:
            rows = sheet.iter_rows(values_only=True)
            header = next(rows, None)
            if not header:
                continue
            title_at, items_at = _columns(header)
            if items_at is None:
                continue
            for row in rows:
                if not row:
                    continue
                key = str(row[0] if row[0] is not None else '').strip().upper()
                if isinstance(row[0], float) and row[0].is_integer():
                    key = str(int(row[0]))
                if not TOPIC.fullmatch(key):
                    continue
                cell = row[items_at] if items_at < len(row) else None
                if isinstance(cell, (int, float)):
                    cell = f'{cell:g}'
                items = [n for n in NUMBER.findall(str(cell or ''))
                         if agenda is None or n in agenda]
                title = str(row[title_at] or '').strip() if title_at is not None and title_at < len(row) else ''
                topics.setdefault(key, {'title': ' '.join(title.split()), 'agenda_items': list(dict.fromkeys(items))})
    finally:
        book.close()
    return topics


def expand(topic: str, topics: dict) -> list[str]:
    """The listed topics a cell's number stands for: "105" is 105-A and 105-B,
    "102-A/B" both halves. A number the list does not have stands for nothing."""
    topic = topic.upper()
    if topic in topics:
        return [topic]
    match = re.fullmatch(r'(\d{3})-([A-Z])/([A-Z])', topic)
    if match:
        return [t for t in (f'{match[1]}-{match[2]}', f'{match[1]}-{match[3]}') if t in topics]
    if re.fullmatch(r'\d{3}', topic):
        return sorted(t for t in topics if t.startswith(topic + '-'))
    return []
