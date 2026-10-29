"""Gemini fallback for SA2 cells and Session Plans the rules cannot read with confidence.

The rules stay the default (README "LLM 폴백"). The table fixes each session's
place, so a doubtful cell is asked only for what its words may hide: the title,
the agenda items and, where a time is written in a form the rules do not read,
the times within the cell. A Session Plan whose table is not the template is
read whole. An answer is used only when it checks against the text; otherwise
the rules' reading stands (cells) or the build fails and the last page stays
(documents). Calls, checks and caching are shared/llm_cells.py.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import re

from docx import Document
from pydantic import BaseModel

from shared import llm_cells
from shared.llm_cells import check_times, gemini_request, title_errors, written_errors
from shared.schedule import MIN_PART_MINUTES, time_to_minutes
from .document import DAYS, LayoutError, W, _grid, _text, cell_lines, identify, paragraph_text, squash, supplements
from .sessions import known, line_items
from .sources import CACHE, digest, read_json, write_json

PROMPTS = Path(__file__).parent / 'prompts'
VERSION = 1
CLOCK = re.compile(r'(?<![\d.:])(\d{1,2}):(\d{2})(?!\d)')
RANGE = re.compile(r'(?<![\d.])(\d{2})(\d{2})\s*-\s*(\d{2})(\d{2})(?![\d.])')
SINGLE = re.compile(r'(?:\(|\b(?:at|by)\s+)(\d{2})(\d{2})(?![\d.])', re.I)


def written_times(text: str) -> set[str]:
    """Clock times in the forms SA2 writes: "13:00", "0900 - 1030", "(1300)", "by 1630".

    Four digits count only in a range, in parentheses or after at/by, so a
    TDoc number ("LS's, 9779, 10337") is not a time.
    """
    pairs = CLOCK.findall(text) + [p for m in RANGE.findall(text) for p in (m[:2], m[2:])] + SINGLE.findall(text)
    return {f'{int(h):02d}:{m}' for h, m in pairs if int(h) < 24 and int(m) < 60}


def item_errors(prefix, items, source: str, agenda: dict) -> list[str]:
    """Agenda items written in the source, directly or by the chair's shorthand.

    "20.6.13" is written by "(20.6.12 - 16)" and "19.49" by "19.48/49/50".
    """
    errors = []
    read = {item for line in source.split('\n') for item in line_items(line, {})[0]}
    for item in items:
        if item not in read and written_errors(prefix, [item], source, 'agenda item'):
            errors.append(f'{prefix}: agenda item {item!r} is not written in its cited text')
        elif not known(item, agenda):
            errors.append(f'{prefix}: agenda item {item!r} is not in agenda.csv')
    return errors


# ------------------------------------------------------------------- cells

class CellAnswer(BaseModel):
    id: str
    start: str
    end: str
    title: str
    agenda_items: list[str]


def cell_payload(session: dict, rooms: dict) -> dict:
    return {'id': session['id'], 'day': session['day'],
            'room': ', '.join(rooms.get(r, r) for r in session['rooms']) or 'all rooms',
            'block_start': session['block'][0], 'block_end': session['block'][1], 'part': session['part'],
            'lines': [{'n': i, 'text': raw} for i, raw in enumerate(session['lines'])],
            'reasons': session['reasons'],
            'rules': {k: session[k] for k in ('start', 'end', 'name', 'agenda_items')}}


def check_cell(answer: CellAnswer, session: dict, agenda: dict) -> list[str]:
    source = '\n'.join(session['lines'])
    start, end = session['block']
    allowed = {start, end} | written_times(source)
    prefix = session['id']
    errors = check_times(prefix, answer.start, answer.end, allowed)
    if not (start <= answer.start and answer.end <= end):
        errors.append(f'{prefix}: {answer.start}-{answer.end} is outside the cell {start}-{end}')
    if session['part'] and (answer.start, answer.end) != (start, end):
        errors.append(f'{prefix}: a piece keeps its block times {start}-{end}')
    return errors + title_errors(prefix, answer.title, source) + item_errors(prefix, answer.agenda_items, source, agenda)


class CellRefiner:
    """Callable for ``sessions.interpret``: doubtful sessions in, checked readings out."""

    def __init__(self, model: str, request=None, cache_dir: Path = CACHE, *, rooms: dict | None = None,
                 agenda: dict | None = None):
        self.model, self.request, self.cache_dir = model, request or gemini_request, cache_dir
        self.rooms, self.agenda = rooms or {}, agenda or {}
        self.summary = {'model': model, 'uncertain': 0, 'applied': 0, 'kept_rules': 0}

    def __call__(self, doubtful: list[dict]) -> dict[str, dict]:
        items = {s['id']: s for s in doubtful}
        prompt = (PROMPTS / 'cells.md').read_text(encoding='utf-8')
        answers = llm_cells.ask(prompt, [cell_payload(s, self.rooms) for s in doubtful], CellAnswer,
                                lambda answer, _: check_cell(answer, items[answer.id], self.agenda),
                                model=self.model, cache_dir=self.cache_dir, cache_name='cells', label='[sa2]',
                                request=self.request, version=VERSION)
        if answers.error:
            self.summary['error'] = answers.error
        self.summary.update(uncertain=len(items), applied=len(answers), kept_rules=len(items) - len(answers))
        print(f'[sa2] LLM fallback: {len(items)} uncertain cells, {len(answers)} read by {self.model}')
        return {key: {'start': a.start, 'end': a.end, 'name': ' '.join(a.title.split()),
                      'agenda_items': list(dict.fromkeys(a.agenda_items)),
                      'group_item': a.agenda_items[0] if a.agenda_items else None}
                for key, a in answers.items()}


# --------------------------------------------------------------- document

class DocRoom(BaseModel):
    id: str
    name: str


class DocSession(BaseModel):
    day: str
    start: str
    end: str
    part: list[int] | None
    room_ids: list[str]
    title: str
    agenda_items: list[str]
    refs: list[str]


class DocResult(BaseModel):
    rooms: list[DocRoom]
    days: list[str]
    sessions: list[DocSession]


def outline(doc) -> list[dict]:
    """The whole body: paragraphs, and table cells with their row and column span and headers.

    In SA2's table a merged or empty cell means something (a quarter's two
    cells, a cell across all streams, a stream with no session), so each cell
    says which grid rows and columns it covers and what the row and column
    headers there read.
    """
    blocks, table_index = [], 0
    for index, child in enumerate(doc.element.body.iterchildren()):
        if child.tag == W + 'p':
            text = '\n'.join(squash(t) for t in paragraph_text(child).split('\n') if squash(t))
            if text:
                blocks.append({'id': f'p{index}', 'text': text})
        elif child.tag == W + 'tbl':
            table = next(t for t in doc.tables if t._tbl is child)
            grid = _grid(table)
            spans = {}
            for ri, row in enumerate(grid):
                for ci, cell in enumerate(row):
                    spans.setdefault(id(cell['tc']), {'tc': cell['tc'], 'rows': [], 'cols': []})
                    span = spans[id(cell['tc'])]
                    if ri not in span['rows']:
                        span['rows'].append(ri)
                    if ci not in span['cols']:
                        span['cols'].append(ci)
            cells = []
            for span in spans.values():
                r0, c0 = span['rows'][0], span['cols'][0]
                cells.append({'id': f't{table_index}.r{r0}c{c0}', 'rows': [r0, span['rows'][-1]],
                              'cols': [c0, span['cols'][-1]], 'text': '\n'.join(cell_lines(span['tc']))})
            blocks.append({'id': f't{table_index}', 'cells': cells})
            table_index += 1
    return blocks


def _pieces(session: DocSession) -> tuple[int, int]:
    start, end = time_to_minutes(session.start), time_to_minutes(session.end)
    if session.part:
        k, n = session.part
        return start + (end - start) * (k - 1) // n, start + (end - start) * k // n
    return start, end


def check_document(result: DocResult, texts: dict[str, str], agenda: dict) -> list[str]:
    errors = []
    allowed = written_times('\n'.join(texts.values()))
    rooms = {r.id for r in result.rooms}
    if not rooms or len(rooms) != len(result.rooms) or any(not r.id.strip() or not r.name.strip() for r in result.rooms):
        errors.append('rooms need unique, non-empty ids and names')
    if any(day not in DAYS for day in result.days) or len(set(result.days)) != len(result.days):
        errors.append(f'days must be distinct weekdays: {result.days}')
    if not result.sessions:
        errors.append('no sessions found')
    used = []
    for n, session in enumerate(result.sessions):
        prefix = f'session {n}'
        if session.day not in result.days:
            errors.append(f'{prefix}: day {session.day!r} is not listed in days')
        errors += check_times(prefix, session.start, session.end, allowed)
        if not session.room_ids or not set(session.room_ids) <= rooms:
            errors.append(f'{prefix}: room_ids must name listed rooms')
        if not session.refs or not set(session.refs) <= texts.keys():
            errors.append(f'{prefix}: refs must cite paragraph or cell ids')
            continue
        if session.part is not None:
            k, n_parts = (session.part + [0, 0])[:2]
            minutes = time_to_minutes(session.end) - time_to_minutes(session.start) if not errors else 0
            if len(session.part) != 2 or not 1 <= k <= n_parts or n_parts < 2:
                errors.append(f'{prefix}: part must be [k, n] with 1 <= k <= n and n >= 2')
            elif minutes and minutes < n_parts * MIN_PART_MINUTES:
                errors.append(f'{prefix}: {n_parts} pieces of {minutes} min are shorter than {MIN_PART_MINUTES} min')
            # A piece is a table cell of its own: the block's n pieces cite n different cells.
            siblings = [s for s in result.sessions if (s.day, s.start, s.end, s.room_ids) ==
                        (session.day, session.start, session.end, session.room_ids) and s.part]
            if any('.' not in ref or ref.startswith('p') for ref in session.refs) or \
                    len({tuple(s.refs) for s in siblings}) != len(siblings) or \
                    sorted(tuple(s.part) for s in siblings) != [(k, n_parts) for k in range(1, n_parts + 1)]:
                errors.append(f'{prefix}: the {n_parts} pieces of a block must each cite their own table cell')
        source = '\n'.join(texts[ref] for ref in session.refs)
        errors += title_errors(prefix, session.title, source)
        errors += item_errors(prefix, session.agenda_items, source, agenda)
        if not any(e.startswith(prefix + ':') for e in errors):
            a, b = _pieces(session)
            for other_day, x, y, other_rooms in used:
                if other_day == session.day and set(other_rooms) & set(session.room_ids) and a < y and x < b:
                    errors.append(f'{prefix}: overlaps another session in the same room')
            used.append((session.day, a, b, session.room_ids))
    return errors


def read_document(data: bytes, name: str, model: str, agenda: dict | None = None, request=None,
                  cache_dir: Path = CACHE):
    """(document, sessions) for ``make_schedule`` when the table is not the template."""
    request = request or gemini_request
    agenda = agenda or {}
    doc = Document(io.BytesIO(data))
    meeting = identify(doc, name)
    blocks = outline(doc)
    texts = {b['id']: b['text'] for b in blocks if 'text' in b}
    texts.update({c['id']: c['text'] for b in blocks for c in b.get('cells', []) if c['text']})
    prompt = (PROMPTS / 'document.md').read_text(encoding='utf-8')
    key = digest(json.dumps([VERSION, prompt, model, blocks], sort_keys=True).encode())
    path = cache_dir / f'document-{key}.json'
    result, errors = None, ['no answer']
    cached = read_json(path)
    if cached:
        try:
            result = DocResult.model_validate(cached['result'])
            errors = check_document(result, texts, agenda)
        except (KeyError, ValueError):
            result = None
    message = prompt + '\n\nDOCUMENT (untrusted data):\n' + json.dumps(blocks, ensure_ascii=False)
    for attempt in range(2):
        if result is not None and not errors:
            break
        raw = request(message, DocResult.model_json_schema(), model, thinking='high')
        result = DocResult.model_validate(raw)
        errors = check_document(result, texts, agenda)
        message += ('\n\nYOUR PREVIOUS ANSWER:\n' + json.dumps(raw, ensure_ascii=False)
                    + '\n\nFIX THESE PROBLEMS:\n' + '\n'.join(errors))
    if errors:
        raise LayoutError(f'The LLM could not read the Session Plan either: {errors[:3]}')
    write_json(path, {'result': result.model_dump()})
    print(f'[sa2] Table not the template; {len(result.sessions)} sessions read by {model}')

    every = [r.id for r in result.rooms]
    sessions = []
    for n, s in enumerate(result.sessions):
        shared = sorted(s.room_ids) == sorted(every) and len(every) > 1
        lines = [line for ref in s.refs for line in texts[ref].split('\n')]
        sessions.append({'id': f'doc{n}', 'cell': f'doc{n}', 'day': s.day, 'start': s.start, 'end': s.end,
                         'part': s.part, 'stream': None, 'rooms': [] if shared else s.room_ids,
                         'scope': 'shared' if shared else 'assigned', 'lines': lines,
                         'name': ' '.join(s.title.split()), 'agenda_items': list(dict.fromkeys(s.agenda_items)),
                         'group_item': s.agenda_items[0] if s.agenda_items else None, 'reasons': []})
    notice = {'type': 'paragraph', 'bold': True,
              'text': 'The Session Plan table was not in its usual layout, so the blocks above were read by an LLM. '
                      'The document text follows for reference.'}
    document = {'meeting_id': meeting, 'days': [d for d in DAYS if d in result.days],
                'rooms': [{'id': r.id, 'name': r.name} for r in result.rooms], 'breaks': [], 'streams': [],
                'conveners': {}, 'stream_colours': {}, 'notes': [],
                'supplements': [notice, *supplements(doc, None, titled=True)]}
    return document, sessions
