"""Gemini fallback for the SA1 cells the rules flag as uncertain (shared/llm_cells.py).

The rules stay the default and almost never doubt an SA1 cell (none in the
#115 versions). A flagged cell is asked once with its reasons and the rules'
reading; an answer is used only when it checks out against the cell: every
line cited once, times written in the cell or its bounds, titles in the
cited lines' words, agenda items written there, and each session in the room
whose head its lines follow. Chairs are not asked: they come from the NOTE
below the table, as for every other cell.

A weekday table whose slots the rules cannot read at all is read whole
(``read_document``): the table and the paragraphs around it go to the LLM, and
the answer is checked against their text the same way. Without an answer that
passes, the build fails and the last page stays.
"""
from __future__ import annotations

from pathlib import Path
import re

from pydantic import BaseModel

from shared import llm_cells, office_outline
from shared.llm_cells import check_times, citation_errors, title_errors, written_errors
from .document import DAYS, ROOM_ROLE, LayoutError, clock, room_list, squash
from .sessions import (CLOCK_TEXT, HEAD, SEPARATOR, SLASH, START_AT, agenda_of, chair_of, checked_item, is_agenda,
                       known_names, place)

PROMPTS = Path(__file__).parent / 'prompts'
CACHE = Path('.cache/sa1')
VERSION = 1
# Blocks kept on each side of the schedule table for the whole-document fallback.
AROUND = 15
WRITTEN_TIMES = (re.compile(r'(?<![\d.:])(\d{1,2})[:.h](\d{2})(?![\d.:])'),       # 08:00, 8.00, 8h30
                 re.compile(r'(?<![\d.:-])([01]\d|2[0-3])([0-5]\d)(?![\d.:])'),    # 0800
                 re.compile(r'(?<![\d.:])(\d{1,2})h()(?![\w])'))                     # 8h


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


# --------------------------------------------------------------- document

class DocRoom(BaseModel):
    id: str
    name: str


class DocSession(BaseModel):
    day: str
    start: str
    end: str
    room: str | None
    title: str
    agenda_items: list[str]
    refs: list[str]


class DocResult(BaseModel):
    rooms: list[DocRoom]
    days: list[str]
    sessions: list[DocSession]


def schedule_blocks(data: bytes) -> list[dict]:
    """The schedule table with the paragraphs around it (MEETING ROOMS above, NOTE below).

    The chair file's TDoc table follows and holds thousands of blocks the
    answer never needs; the NOTE chairs may be a one-row table (#112 to #114).
    """
    blocks = office_outline.docx_blocks(data)

    def weekdays(block):
        first = (block.get('rows') or [[]])[0]
        return sum(squash(cell.get('text', '')) in DAYS for cell in first)

    at = next((i for i, b in enumerate(blocks) if 'rows' in b and weekdays(b) >= 3), None)
    if at is None:
        return []
    start = at
    while start > 0 and 'rows' not in blocks[start - 1] and at - start < AROUND:
        start -= 1
    end = at + 1
    while end < len(blocks) and end - at <= AROUND and len(blocks[end].get('rows') or []) <= 2:
        end += 1
    return blocks[start:end]


def written_times(text: str) -> set[str]:
    """Clock times as a schedule may write them: "08:00", "8.00", "0800", "8h"."""
    found = set()
    for pattern in WRITTEN_TIMES:
        for match in pattern.finditer(text):
            try:
                found.add(clock(match[1], match[2] or 0))
            except ValueError:
                pass
    return found


def paragraph_lines(texts: dict[str, str]) -> list[str]:
    """Lines of the paragraphs, not of table cells ("Breakout 2: 7.3.9 Computing" is no room line)."""
    return [line for key, text in texts.items() if ':' not in key for line in text.split('\n')]


def doc_rooms(result: DocResult, lines: list[str]) -> tuple[list[dict], dict[str, str]]:
    """(rooms, answer id -> room id): the answer's rooms read as the rules read MEETING ROOMS lines.

    A room the lines name keeps the rules' id ("plenary", "breakout-2"), so the
    page is the same as a rule-read build; another room keeps the answer's id.
    """
    listed = {squash(room['name']).casefold(): room for room in room_list(lines)}
    rooms, ids = [], {}
    for answer in result.rooms:
        known = listed.get(squash(answer.name).casefold())
        if known:
            room = dict(known)
        else:
            aliases = {f'{m[1].lower()} {m[2]}' for m in ROOM_ROLE.finditer(answer.name)}
            if 'plenary' in answer.name.lower():
                aliases.add('plenary')
            room = {'id': answer.id, 'name': squash(answer.name), 'place': '', 'aliases': sorted(aliases)}
        ids[answer.id] = room['id']
        rooms.append(room)
    return rooms, ids


def owned_lines(text: str, room: str | None, rooms: list[dict]) -> list[str]:
    """The lines of a cited cell that follow the head of ``room`` (all of them in a cell without heads)."""
    lines = [line for line in text.split('\n') if line.strip()]
    owners = line_rooms({'lines': lines}, rooms)
    if all(owner is None for owner in owners.values()):
        return lines
    return [line for i, line in enumerate(lines) if owners[i] == room]


def check_document(result: DocResult, texts: dict[str, str]) -> list[str]:
    errors = []
    everything = '\n'.join(texts.values())
    allowed = written_times(everything)
    ids = [r.id for r in result.rooms]
    if not ids or len(set(ids)) != len(ids) or any(not r.id.strip() for r in result.rooms):
        errors.append('rooms need unique, non-empty ids')
    for room in result.rooms:
        if squash(room.name).casefold() not in squash(everything).casefold():
            errors.append(f'room {room.name!r} is not written in the document')
    header = {squash(t) for t in texts.values()}
    if len(set(result.days)) != len(result.days) or any(d not in DAYS or d not in header for d in result.days):
        errors.append(f'days must be distinct weekdays written as column heads: {result.days}')
    if not result.sessions:
        errors.append('no sessions found')
    rooms, room_ids = doc_rooms(result, paragraph_lines(texts))
    busy = {}
    for n, session in enumerate(result.sessions):
        prefix = f'session {n}'
        if session.day not in result.days:
            errors.append(f'{prefix}: day {session.day!r} is not listed in days')
        errors += check_times(prefix, session.start, session.end, allowed)
        if session.room is not None and session.room not in ids:
            errors.append(f'{prefix}: room {session.room!r} is not one of {ids}')
            continue
        if not session.refs or not set(session.refs) <= texts.keys():
            errors.append(f'{prefix}: refs must cite paragraph or cell ids')
            continue
        room = room_ids.get(session.room) if session.room is not None else None
        source = '\n'.join(line for ref in session.refs for line in owned_lines(texts[ref], room, rooms))
        if not source:
            errors.append(f'{prefix}: its cited text follows the head of another room')
            continue
        errors += title_errors(prefix, session.title, source)
        errors += [f'{prefix}: agenda item {i!r} is not an agenda number'
                   for i in session.agenda_items if not (i.replace('.', '').isdigit() and is_agenda(i))]
        errors += written_errors(prefix, [i for i in session.agenda_items if i.replace('.', '').isdigit()],
                                 source, 'agenda item')
        if session.room is not None:
            for start, end in busy.get((session.day, session.room), []):
                if start < session.end and session.start < end:
                    errors.append(f'{prefix}: overlaps another session in the same room')
            busy.setdefault((session.day, session.room), []).append((session.start, session.end))
    return errors


def read_document(data: bytes, name: str, partial: dict, model: str, request=None,
                  cache_dir: Path = CACHE) -> tuple[dict, tuple[list[dict], list[dict]]]:
    """(document, (sessions, notes)) for ``make_schedule`` when the rules cannot read the table.

    ``partial`` is what the rules did read (meeting, NOTE chairs, agenda,
    supplements). Chairs are not asked: the NOTE rule is applied to the
    answer's sessions as to the rules' own.
    """
    blocks = schedule_blocks(data)
    texts = office_outline.texts(blocks)
    prompt = (PROMPTS / 'document.md').read_text(encoding='utf-8')
    try:
        result = llm_cells.read_document(prompt, blocks, DocResult, lambda r: check_document(r, texts), model=model,
                                         cache_dir=cache_dir, wg_id='sa1', source=name,
                                         meeting=partial.get('meeting_id'), request=request, version=VERSION)
    except ValueError as exc:
        raise LayoutError(f'The LLM could not read the SA1 schedule table either: {exc}') from exc
    print(f'[sa1] Schedule table not in a known layout; {len(result.sessions)} sessions read by {model}')

    rooms, room_ids = doc_rooms(result, paragraph_lines(texts))
    plenary = {room['id'] for room in rooms if 'plenary' in room['aliases']}
    agenda = partial['agenda']
    found = []
    for s in result.sessions:
        room = room_ids[s.room] if s.room is not None else None
        lines = [content(line) for ref in s.refs for line in owned_lines(texts[ref], room, rooms)]
        lines = [line for line in lines if meaningful(line)]
        written = (checked_item(i, '', agenda) for i in dict.fromkeys(s.agenda_items))
        items = [i for i in written if i]
        found.append({'day': s.day, 'room': room, 'start': s.start, 'end': s.end,
                      'name': ' '.join(s.title.split()), 'agenda_items': items, 'lines': lines,
                      'chair': None if room is None or (room in plenary and s.title == 'Plenary')
                      else chair_of(lines, partial['chairs'])})
    # Topics named without a number find the numbers the table writes with them, for the filter only (#96).
    cells = [{'lines': text.split('\n')} for key, text in texts.items() if ':' in key]
    names = known_names({'days': [{'cells': cells}]})
    for s in found:
        s['inferred_items'] = [i for i in agenda_of(s['lines'], agenda, names)[1] if i not in s['agenda_items']]
    days = [d for d in DAYS if d in result.days]
    sessions, notes = [], []
    for day in days:
        placed, left = place([s for s in found if s['day'] == day])
        sessions += placed
        notes += [{'day': day, 'text': ' '.join(s['lines']) + f' ({s["start"]}–{s["end"]})'} for s in left]
    notice = {'type': 'paragraph', 'bold': True,
              'text': 'The schedule table was not in its usual layout, so the sessions above were read by an LLM '
                      'and checked against the table text. Compare with the chair file.'}
    document = {**partial, 'rooms': rooms, 'supplements': [notice, *partial['supplements']],
                'days': [{'day': day, 'slots': [], 'breaks': [], 'cells': []} for day in days]}
    return document, (sessions, notes)
