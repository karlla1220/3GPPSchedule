"""Gemini fallback for RAN2 cells and documents the rules cannot read with confidence.

The rules stay the default. The LLM sees only the cells they flag as uncertain,
or the whole document when its table layout is not recognised. An answer is
used only when it can be checked against the text: every line is cited once,
times are the cell's bounds or written in it, titles use the cited lines' own
words, and chairs and agenda items are written there. A cell whose answer fails
the checks keeps the rule-based reading, so the fallback can improve the page
but never takes it down. Answers are cached by their exact input.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

from docx import Document
from pydantic import BaseModel

from shared import llm_cells
from shared.llm_cells import check_times, citation_errors, gemini_request, has_key, title_errors, written_errors
from .document import DAYS, LayoutError, clock, extract_breaks, identify, outline, supplements
from .sessions import CLOCK_TEXT, COMPANIES, is_agenda, meaningful, parse_marker, written_times
from .sources import CACHE, digest, read_json, write_json

PROMPTS = Path(__file__).parent / 'prompts'
VERSION = 1


class CellSession(BaseModel):
    start: str
    end: str
    title: str
    chair: str | None
    agenda_items: list[str]
    offline: bool
    lines: list[int]


class CellAnswer(BaseModel):
    id: str
    sessions: list[CellSession]


class DocRoom(BaseModel):
    id: str
    name: str


class DocSlot(BaseModel):
    start: str
    end: str


class DocDay(BaseModel):
    day: str
    slots: list[DocSlot]


class DocSession(BaseModel):
    day: str
    start: str
    end: str
    room_ids: list[str]
    title: str
    chair: str | None
    agenda_items: list[str]
    offline: bool
    refs: list[str]


class DocResult(BaseModel):
    rooms: list[DocRoom]
    days: list[DocDay]
    sessions: list[DocSession]


# ----------------------------------------------------------------- checks

def check_session(prefix, title, chair, agenda_items, source: str, text: str) -> list[str]:
    names = [n for n in (n.strip() for n in (chair or '').split(',')) if n]
    errors = title_errors(prefix, title, source)
    errors += [f'{prefix}: chair {n!r} is not a name written in the text' for n in names if n.lower() in COMPANIES]
    errors += written_errors(prefix, [n for n in names if n.lower() not in COMPANIES], text, 'chair')
    errors += [f'{prefix}: agenda item {i!r} is not written in its cited text' for i in agenda_items if not is_agenda(i)]
    return errors + written_errors(prefix, [i for i in agenda_items if is_agenda(i)], source, 'agenda item')


def check_cell(answer: CellAnswer, item: dict) -> list[str]:
    cell, bounds = item['cell'], item['bounds']
    lines = cell['lines']
    text = '\n'.join(lines)
    allowed = {cell['start'], cell['end']} | written_times(lines, bounds)
    errors, previous = [], None
    if not answer.sessions:
        return [f'{cell["id"]}: no sessions']
    for n, session in enumerate(answer.sessions):
        prefix = f'{cell["id"]} session {n}'
        errors += check_times(prefix, session.start, session.end, allowed)
        if previous and session.start < previous:
            errors.append(f'{prefix}: overlaps or is out of order')
        previous = session.end
        if session.lines and all(0 <= i < len(lines) and lines[i] for i in session.lines):
            source = '\n'.join(lines[i] for i in session.lines)
            errors += check_session(prefix, session.title, session.chair, session.agenda_items, source, text)
    return errors + citation_errors(cell['id'], [s.lines for s in answer.sessions], lines, meaningful)


def to_sessions(answer: CellAnswer, item: dict) -> list[dict]:
    cell, bounds = item['cell'], item['bounds']
    marked = {m[0] for m in (parse_marker(raw, bounds) for raw in cell['lines'] if raw) if m}
    result = []
    for session in answer.sessions:
        items = list(dict.fromkeys(session.agenda_items))
        result.append({'rooms': cell['rooms'], 'start': session.start, 'end': session.end,
                       'fixed': session.start != cell['start'] or session.start in marked,
                       'name': ' '.join(session.title.split()), 'chair': (session.chair or '').strip() or None,
                       'agenda_items': items, 'group_item': items[0] if items else None,
                       'offline': session.offline, 'lines': [cell['lines'][i] for i in sorted(session.lines)]})
    return result


# ------------------------------------------------------------------ cells

def cell_payload(item: dict) -> dict:
    cell = item['cell']
    return {'id': cell['id'], 'day': item['day']['day'], 'room': ' + '.join(cell['rooms']),
            'cell_start': cell['start'], 'cell_end': cell['end'],
            'day_slots': [f'{a}-{b}' for a, b in item['day']['slots']],
            'lines': [{'n': i, 'text': raw} for i, raw in enumerate(cell['lines'])],
            'reasons': item['reasons'],
            'rules': [{k: s[k] for k in ('start', 'end', 'name', 'chair', 'agenda_items')} for s in item['rules']]}


class CellRefiner:
    """Callable for ``sessions.interpret``: uncertain cells in, validated sessions out."""

    def __init__(self, model: str, request=None, cache_dir: Path = CACHE):
        self.model, self.request, self.cache_dir = model, request or gemini_request, cache_dir
        self.summary = {'model': model, 'uncertain': 0, 'applied': 0, 'kept_rules': 0}

    def __call__(self, doubtful: list[dict]) -> dict[str, list[dict]]:
        items = {item['cell']['id']: item for item in doubtful}
        prompt = (PROMPTS / 'cells.md').read_text(encoding='utf-8')
        answers = llm_cells.ask(prompt, [cell_payload(item) for item in doubtful], CellAnswer,
                                lambda answer, _: check_cell(answer, items[answer.id]),
                                model=self.model, cache_dir=self.cache_dir, cache_name='cells', label='[ran2]',
                                request=self.request, version=VERSION)
        if answers.error:
            self.summary['error'] = answers.error
        self.summary.update(uncertain=len(items), applied=len(answers), kept_rules=len(items) - len(answers))
        print(f'[ran2] LLM fallback: {len(items)} uncertain cells, {len(answers)} read by {self.model}')
        return {cell_id: to_sessions(answer, items[cell_id]) for cell_id, answer in answers.items()}


# --------------------------------------------------------------- document

def check_document(result: DocResult, texts: dict[str, str]) -> list[str]:
    errors = []
    everything = '\n'.join(texts.values())
    written = set()
    for match in CLOCK_TEXT.finditer(everything):
        try:
            written.add(clock(match[1], match[2]))
        except ValueError:
            pass
    rooms = {r.id for r in result.rooms}
    if not rooms or len(rooms) != len(result.rooms) or any(not r.id.strip() or not r.name.strip() for r in result.rooms):
        errors.append('rooms need unique, non-empty ids and names')
    days = {}
    for day in result.days:
        if day.day not in DAYS or day.day in days:
            errors.append(f'{day.day!r} is not a single weekday')
        days[day.day] = day
        for slot in day.slots:
            errors += check_times(day.day, slot.start, slot.end, written)
    if not result.sessions:
        errors.append('no sessions found')
    used = []
    for n, session in enumerate(result.sessions):
        prefix = f'session {n}'
        if session.day not in days:
            errors.append(f'{prefix}: day {session.day!r} is not listed in days')
        errors += check_times(prefix, session.start, session.end, written)
        if not session.room_ids or not set(session.room_ids) <= rooms:
            errors.append(f'{prefix}: room_ids must name listed rooms')
        if not session.refs or not set(session.refs) <= texts.keys():
            errors.append(f'{prefix}: refs must cite paragraph or cell ids')
            continue
        source = '\n'.join(texts[ref] for ref in session.refs)
        errors += check_session(prefix, session.title, session.chair, session.agenda_items, source, everything)
        for other_day, start, end, other_rooms in used:
            if (other_day == session.day and set(other_rooms) & set(session.room_ids)
                    and start < session.end and session.start < end):
                errors.append(f'{prefix}: overlaps another session in the same room')
        used.append((session.day, session.start, session.end, session.room_ids))
    return errors


def read_document(data: bytes, name: str, model: str, request=None, cache_dir: Path = CACHE):
    """(document, sessions) for ``make_schedule`` when the table layout is unknown."""
    request = request or gemini_request
    doc = Document(io.BytesIO(data))
    meeting, title, paragraphs = identify(doc, name)
    blocks = outline(doc)
    texts = {b['id']: b['text'] for b in blocks if 'text' in b}
    texts.update({c['id']: c['text'] for b in blocks for row in b.get('rows', []) for c in row})
    prompt = (PROMPTS / 'document.md').read_text(encoding='utf-8')
    key = digest(json.dumps([VERSION, prompt, model, blocks], sort_keys=True).encode())
    path = cache_dir / f'document-{key}.json'
    result, errors = None, ['no answer']
    cached = read_json(path)
    if cached:
        try:
            result = DocResult.model_validate(cached['result'])
            errors = check_document(result, texts)
        except (KeyError, ValueError):
            result = None
    message = prompt + '\n\nDOCUMENT (untrusted data):\n' + json.dumps(blocks, ensure_ascii=False)
    for attempt in range(2):
        if result is not None and not errors:
            break
        raw = request(message, DocResult.model_json_schema(), model, thinking='high')
        result = DocResult.model_validate(raw)
        errors = check_document(result, texts)
        message += ('\n\nYOUR PREVIOUS ANSWER:\n' + json.dumps(raw, ensure_ascii=False)
                    + '\n\nFIX THESE PROBLEMS:\n' + '\n'.join(errors))
    if errors:
        raise LayoutError(f'The LLM could not read the schedule either: {errors[:3]}')
    write_json(path, {'result': result.model_dump()})
    print(f'[ran2] Table layout not recognised; {len(result.sessions)} sessions read by {model}')

    rooms = [{'id': r.id, 'name': r.name, 'label': r.name} for r in result.rooms]
    days = [{'day': d.day, 'slots': sorted((s.start, s.end) for s in d.slots), 'cells': [], 'notes': []}
            for d in sorted(result.days, key=lambda d: DAYS.index(d.day))]
    sessions = [{'day': s.day, 'rooms': s.room_ids, 'start': s.start, 'end': s.end, 'fixed': True,
                 'name': ' '.join(s.title.split()), 'chair': (s.chair or '').strip() or None,
                 'agenda_items': list(dict.fromkeys(s.agenda_items)),
                 'group_item': s.agenda_items[0] if s.agenda_items else None, 'offline': s.offline,
                 'lines': [line for ref in s.refs for line in texts[ref].split('\n')]}
                for s in result.sessions]
    notice = {'type': 'paragraph', 'bold': True,
              'text': 'The schedule table layout was not recognised, so the blocks above were read by an LLM. '
                      'The document text follows for reference.'}
    document = {'meeting_id': meeting, 'title': title, 'rooms': rooms, 'days': days,
                'breaks': extract_breaks(paragraphs), 'supplements': [notice, *supplements(doc, None, title, [])]}
    return document, sessions
