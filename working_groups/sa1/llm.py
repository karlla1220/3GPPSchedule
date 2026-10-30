"""Gemini fallback for the SA1 cells the rules flag as uncertain (shared/llm_cells.py).

The rules stay the default and almost never doubt an SA1 cell (none in the
#115 versions). A flagged cell is asked once with its reasons and the rules'
reading; an answer is used only when it checks out against the cell: every
line cited once, times written in the cell or its bounds, titles in the
cited lines' words, agenda items written there, and each session in the room
whose head its lines follow. Chairs are not asked: they come from the NOTE
below the table, as for every other cell.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from shared import llm_cells
from shared.llm_cells import check_times, citation_errors, title_errors, written_errors
from .document import clock
from .sessions import (CLOCK_TEXT, HEAD, SEPARATOR, SLASH, START_AT, agenda_of, chair_of, checked_item, is_agenda,
                       known_names)

PROMPTS = Path(__file__).parent / 'prompts'
CACHE = Path('.cache/sa1')
VERSION = 1


class CellSession(BaseModel):
    room: str | None
    start: str
    end: str
    title: str
    agenda_items: list[str]
    lines: list[int]


class CellAnswer(BaseModel):
    id: str
    sessions: list[CellSession]


def content(raw: str) -> str:
    """A line's text without its room head and "=====" ("Breakout 1: 7.3.3 AI ===" -> "7.3.3 AI")."""
    line = SEPARATOR.sub('', raw).strip()
    match = HEAD.match(line)
    return match[2] if match else line


def meaningful(raw: str) -> bool:
    text = content(raw)
    return bool(text) and not START_AT.match(text) and not SLASH.match(text)


def line_rooms(cell: dict, rooms: list[dict]) -> dict[int, str | None]:
    """The room each line belongs to by the heads, as the rules read them."""
    result = {}
    current = None
    alias = {a: room['id'] for room in rooms for a in room['aliases']}
    for index, raw in enumerate(cell['lines']):
        line = SEPARATOR.sub('', raw).strip()
        match = HEAD.match(line)
        key = ' '.join(match[1].lower().split()) if match else None
        if key in alias:
            current = alias[key]
        result[index] = current
        if SEPARATOR.search(raw):
            current = None
    return result


def check_cell(answer: CellAnswer, item: dict) -> list[str]:
    cell, rooms = item['cell'], item['rooms']
    lines = cell['lines']
    allowed = {cell['start'], cell['end']}
    for match in CLOCK_TEXT.finditer('\n'.join(lines)):
        try:
            allowed.add(clock(match[1], match[2]))
        except ValueError:
            pass
    owners = line_rooms(cell, rooms)
    ids = {room['id'] for room in rooms}
    errors, busy = [], {}
    if not answer.sessions:
        return [f'{cell["id"]}: no sessions']
    for n, session in enumerate(answer.sessions):
        prefix = f'{cell["id"]} session {n}'
        errors += check_times(prefix, session.start, session.end, allowed)
        if session.room is not None and session.room not in ids:
            errors.append(f'{prefix}: room {session.room!r} is not one of {sorted(ids)}')
        valid = [i for i in session.lines if 0 <= i < len(lines)]
        if any(owners[i] != session.room for i in valid):
            errors.append(f'{prefix}: its lines follow the head of another room')
        for start, end in busy.get(session.room, []):
            if start < session.end and session.start < end:
                errors.append(f'{prefix}: overlaps another session of the same room')
        busy.setdefault(session.room, []).append((session.start, session.end))
        if valid:
            source = '\n'.join(lines[i] for i in valid)
            errors += title_errors(prefix, session.title, source)
            errors += [f'{prefix}: agenda item {i!r} is not written in its cited text'
                       for i in session.agenda_items if not (i.replace('.', '').isdigit() and is_agenda(i))]
            errors += written_errors(prefix, [i for i in session.agenda_items if i.replace('.', '').isdigit()],
                                     source, 'agenda item')
    return errors + citation_errors(cell['id'], [s.lines for s in answer.sessions], lines, meaningful)


def to_sessions(answer: CellAnswer, item: dict) -> list[dict]:
    cell, document = item['cell'], item['document']
    plenary = {room['id'] for room in document['rooms'] if 'plenary' in room['aliases']}
    names = known_names(document)
    result = []
    for session in answer.sessions:
        lines = [content(cell['lines'][i]) for i in sorted(session.lines) if meaningful(cell['lines'][i])]
        is_plenary = session.room in plenary and session.title == 'Plenary'
        written = (checked_item(i, '', document['agenda']) for i in dict.fromkeys(session.agenda_items))
        items = [i for i in written if i]
        # Topics the cited lines name without a number match the filter as the rules find them.
        inferred = [i for i in agenda_of(lines, document['agenda'], names)[1] if i not in items]
        result.append({'room': session.room, 'start': session.start, 'end': session.end,
                       'name': ' '.join(session.title.split()), 'agenda_items': items, 'inferred_items': inferred,
                       'chair': None if is_plenary or session.room is None else chair_of(lines, document['chairs']),
                       'lines': lines})
    return result


def cell_payload(item: dict) -> dict:
    cell = item['cell']
    return {'id': cell['id'], 'day': item['day']['day'], 'row': cell['row'] or 'slot',
            'cell_start': cell['start'], 'cell_end': cell['end'],
            'rooms': [{'id': r['id'], 'name': r['name'], 'heads': r['aliases']} for r in item['rooms']],
            'lines': [{'n': i, 'text': raw} for i, raw in enumerate(cell['lines'])],
            'reasons': item['reasons'],
            'rules': [{k: s[k] for k in ('room', 'start', 'end', 'name', 'agenda_items')} for s in item['rules']]}


class CellRefiner:
    """Callable for ``sessions.interpret``: uncertain cells in, checked sessions out."""

    def __init__(self, document: dict, model: str, request=None, cache_dir: Path = CACHE):
        self.document, self.model, self.request, self.cache_dir = document, model, request, cache_dir
        self.summary = {'model': model, 'uncertain': 0, 'applied': 0, 'kept_rules': 0}

    def __call__(self, doubtful: list[dict]) -> dict[str, list[dict]]:
        items = {d['cell']['id']: {**d, 'rooms': self.document['rooms'], 'document': self.document}
                 for d in doubtful}
        prompt = (PROMPTS / 'cells.md').read_text(encoding='utf-8')
        answers = llm_cells.ask(prompt, [cell_payload(item) for item in items.values()], CellAnswer,
                                lambda answer, _: check_cell(answer, items[answer.id]),
                                model=self.model, cache_dir=self.cache_dir, cache_name='cells', label='[sa1]',
                                request=self.request, version=VERSION)
        if answers.error:
            self.summary['error'] = answers.error
        self.summary.update(uncertain=len(items), applied=len(answers), kept_rules=len(items) - len(answers))
        print(f'[sa1] LLM fallback: {len(items)} uncertain cells, {len(answers)} read by {self.model}')
        return {cell_id: to_sessions(answer, items[cell_id]) for cell_id, answer in answers.items()}
