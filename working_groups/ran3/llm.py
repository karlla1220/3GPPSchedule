"""Gemini fallback for RAN3 time plan cells the rules cannot read with confidence.

The rules stay the default. A cell is asked about only when it has a line of
no known kind (``sessions.uncertainty``): "All CBs in AI8, AI9, AI31 will be
treated" under "Early CBs" (#129), "End of Year Social" in a CBs cell (#134).
An answer is a cell's pieces in order. It is used only when it can be checked
against the text: every line is cited once, titles use the cited lines' own
words, the chair is an initial written in the cell, agenda items are written
there (a range "1-8" covers 2 to 7) and listed in agenda.csv, and the pieces
are at least 30 minutes. The times are always the cell's: the time plan writes
no others. Calls, retries and the cache are shared (``shared/llm_cells.py``).

Two layouts can break instead of a cell. When the time plan table is not in
its form, the table and the paragraphs around it are read whole
(``read_document``): the answer gives the blocks and their pieces, each piece
copying its lines from the cells it cites, and is checked the same way. The
key and the CB blocks are still read by the rules, and the pieces become
sessions through ``sessions.session_of``, so the "*" note and CB items apply.
When an offline room sheet is not in its form, only that sheet is read
(``read_sheets``): every topic and moderator is a cited cell's text. Either
answer that fails its checks leaves the build as it was without the fallback
(the table: the build fails; a sheet: it is left out with a warning).
"""
from __future__ import annotations

from pathlib import Path
import re

from pydantic import BaseModel

from shared import llm_cells
from shared.llm_cells import check_times, citation_errors, gemini_request, has_key, title_errors, written_errors
from shared.office_outline import docx_blocks, texts as outline_texts, xlsx_blocks
from shared.schedule import MIN_PART_MINUTES, time_to_minutes
from shared.titles import clean

from .document import CLOCK, DAYS, LayoutError, clock, squash, surroundings
from .sessions import HEADS, _span, kind, line_items, session_of
from .sources import CACHE

PROMPTS = Path(__file__).parent / 'prompts'
VERSION = 1


class Piece(BaseModel):
    title: str
    chair: str | None
    agenda_items: list[str]
    lines: list[int]


class CellAnswer(BaseModel):
    id: str
    pieces: list[Piece]


def cell_payload(item: dict) -> dict:
    cell = item['cell']
    return {'id': cell['id'], 'day': cell['day'], 'cell_start': cell['start'], 'cell_end': cell['end'],
            'lines': [{'n': i, 'text': raw} for i, raw in enumerate(cell['lines'])],
            'reasons': item['reasons'],
            'rules': [{'title': s['name'], 'chair': s['chair_label'], 'agenda_items': s['agenda_items']}
                      for s in item['rules']]}


def check_cell(answer: CellAnswer, cell: dict, agenda: dict | None) -> list[str]:
    lines = cell['lines']
    errors = citation_errors(cell['id'], [p.lines for p in answer.pieces], lines)
    minutes = time_to_minutes(cell['end']) - time_to_minutes(cell['start'])
    outer = cell['part'][1] if cell['part'] else 1
    if not answer.pieces:
        errors.append(f'{cell["id"]}: no pieces')
    elif minutes < outer * len(answer.pieces) * MIN_PART_MINUTES:
        errors.append(f'{cell["id"]}: {len(answer.pieces)} pieces of {minutes // outer} min are under '
                      f'{MIN_PART_MINUTES} min; answer one piece')
    for n, piece in enumerate(answer.pieces):
        prefix = f'{cell["id"]} piece {n}'
        cited = [lines[i] for i in piece.lines if 0 <= i < len(lines)]
        source = '\n'.join(cited)
        errors += title_errors(prefix, piece.title, source)
        if piece.chair:
            initials = {x for x in lines if len(x) == 1 and x.isupper()}
            if piece.chair not in initials:
                errors.append(f'{prefix}: chair {piece.chair!r} is not an initial written in the cell')
        expanded = {i for x in cited for i in line_items(x, agenda)}
        errors += written_errors(prefix, [i for i in piece.agenda_items if i not in expanded], source, 'agenda item')
        if agenda:
            errors += [f'{prefix}: agenda item {i!r} is not in this meeting\'s agenda'
                       for i in piece.agenda_items if i not in agenda]
    return errors


def to_sessions(answer: CellAnswer, cell: dict, agenda: dict | None, cbs: dict) -> list[dict]:
    n = len(answer.pieces)
    return [session_of(cell, [cell['lines'][i] for i in sorted(piece.lines)], piece.chair, _span(cell, k, n),
                       agenda, cbs, struck=cell['struck'] if k == 1 else [], title=' '.join(piece.title.split()),
                       items=list(dict.fromkeys(piece.agenda_items)))
            for k, piece in enumerate(answer.pieces, 1)]


class CellRefiner:
    """Callable for ``sessions.interpret``: uncertain cells in, validated sessions out."""

    def __init__(self, model: str, request=None, *, agenda: dict | None = None, cbs: dict | None = None,
                 cache_dir: Path = CACHE):
        self.model, self.request, self.cache_dir = model, request or gemini_request, cache_dir
        self.agenda, self.cbs = agenda or {}, cbs or {}
        self.summary = {'model': model, 'uncertain': 0, 'applied': 0, 'kept_rules': 0}

    def __call__(self, doubtful: list[dict]) -> dict[str, list[dict]]:
        cells = {item['cell']['id']: item['cell'] for item in doubtful}
        prompt = (PROMPTS / 'cells.md').read_text(encoding='utf-8')
        answers = llm_cells.ask(prompt, [cell_payload(item) for item in doubtful], CellAnswer,
                                lambda answer, _: check_cell(answer, cells[answer.id], self.agenda),
                                model=self.model, cache_dir=self.cache_dir, cache_name='cells', label='[ran3]',
                                request=self.request, version=VERSION)
        if answers.error:
            self.summary['error'] = answers.error
        self.summary.update(uncertain=len(cells), applied=len(answers), kept_rules=len(cells) - len(answers))
        print(f'[ran3] LLM fallback: {len(cells)} uncertain cells, {len(answers)} read by {self.model}')
        return {cell_id: to_sessions(answer, cells[cell_id], self.agenda, self.cbs)
                for cell_id, answer in answers.items()}


# --------------------------------------------------------------- document

class DocBreak(BaseModel):
    day: str
    name: str
    start: str
    end: str
    ref: str


class DocSession(BaseModel):
    day: str
    start: str
    end: str
    part: list[int] | None
    title: str
    chair: str | None
    agenda_items: list[str]
    refs: list[str]
    lines: list[str]


class DocResult(BaseModel):
    days: list[str]
    breaks: list[DocBreak]
    sessions: list[DocSession]


DAY_NAME = re.compile(r'\s*(?:Mon|Tue|Wed|Thu|Fri)', re.I)
FALLBACK_NOTICE = ('The time plan table was not in its usual layout, so the main session blocks above were read '
                   'by an LLM and checked against the document text. Check the chair\'s file for anything that '
                   'looks wrong.')
SHEET_NOTICE = ('The offline room sheets {sheets} were not in their usual layout, so their bookings above were '
                'read by an LLM and checked against the sheet text.')


def block_lines(text: str) -> list[str]:
    """A block's lines as the rules see a cell's: squashed, no blank lines."""
    return [squash(line) for line in (text or '').split('\n') if squash(line)]


def written_times(text: str) -> set[str]:
    """Clock times written in the forms the time plan uses: "08:00", "0800", "10:30~11:00"."""
    found = set()
    for hour, minute in CLOCK.findall(text):
        try:
            found.add(clock(hour, minute))
        except ValueError:
            pass
    return found


def time_plan(blocks: list[dict]) -> list[dict]:
    """The blocks to send: the table whose row names the most weekdays, and the paragraphs around it.

    The living agenda is the whole agenda with the chair's notes, far longer
    than the time plan. The key follows the table. Without such a table the
    whole document goes.
    """
    def weekdays(block):   # cells of one row that name a weekday: the header row
        return max((sum(bool(DAY_NAME.match(c.get('text', ''))) for c in row) for row in block['rows']), default=0)
    tables = [(weekdays(b), n) for n, b in enumerate(blocks) if 'rows' in b]
    best = max(tables, default=(0, None))
    if best[0] < 3:
        return blocks
    at = best[1]
    before = [n for n in range(max(0, at - 3), at) if 'rows' not in blocks[n]]
    after = []
    for n in range(at + 1, min(len(blocks), at + 16)):
        if 'rows' in blocks[n]:
            break
        after.append(n)
    return [blocks[n] for n in before + [at] + after]


def check_document(result: DocResult, texts: dict[str, str], agenda: dict | None) -> list[str]:
    errors = []
    allowed = written_times('\n'.join(texts.values()))
    if not result.days or any(d not in DAYS for d in result.days) or len(set(result.days)) != len(result.days):
        errors.append(f'days must be distinct weekdays Monday to Friday: {result.days}')
    if not result.sessions:
        errors.append('no sessions found')
    for n, brk in enumerate(result.breaks):
        prefix = f'break {n}'
        errors += check_times(prefix, brk.start, brk.end, allowed)
        if brk.day not in result.days or brk.ref not in texts:
            errors.append(f'{prefix}: day must be listed and ref must be a block id')
        else:
            errors += title_errors(prefix, brk.name, texts[brk.ref])
    cited = {}
    for n, session in enumerate(result.sessions):
        prefix = f'session {n}'
        if session.day not in result.days:
            errors.append(f'{prefix}: day {session.day!r} is not listed in days')
        errors += check_times(prefix, session.start, session.end, allowed)
        if not session.refs or not set(session.refs) <= texts.keys():
            errors.append(f'{prefix}: refs must cite block ids')
            continue
        written = [line for ref in session.refs for line in block_lines(texts[ref])]
        lines = [squash(line) for line in session.lines]
        if not lines or any(line not in written for line in lines):
            errors.append(f'{prefix}: lines must be lines of its cited blocks, copied exactly')
            continue
        for ref in session.refs:
            # A session over two rows may repeat their one line; only other sessions make it a double citation.
            own = [line for line in lines if line in block_lines(texts[ref])]
            cited.setdefault(ref, []).extend(dict.fromkeys(own))
        source = '\n'.join(lines)
        errors += title_errors(prefix, session.title, source)
        expanded = {i for line in lines for i in line_items(line, agenda)}
        errors += written_errors(prefix, [i for i in session.agenda_items if i not in expanded], source, 'agenda item')
        if agenda:
            errors += [f'{prefix}: agenda item {i!r} is not in this meeting\'s agenda'
                       for i in session.agenda_items if i not in agenda]
        if session.chair and not (session.chair in written and kind(session.chair) == 'initial'):
            errors.append(f'{prefix}: chair {session.chair!r} is not an initial written in its cited blocks')
        if session.part is not None:
            if kind(lines[0]) not in HEADS:
                errors.append(f'{prefix}: a piece starts with its own label line ("6G SI: AI 10"), not {lines[0]!r}')
    for ref, lines in cited.items():
        if len(lines) != len(set(lines)):
            errors.append(f'{ref}: a line is cited by more than one session')
        missing = [line for line in block_lines(texts[ref]) if kind(line) == 'label' and line not in lines]
        if missing:
            errors.append(f'{ref}: label lines {missing} are not in any session')
    if errors:
        return errors
    blocks = {}
    for n, session in enumerate(result.sessions):
        blocks.setdefault((session.day, session.start, session.end), []).append((n, session))
    used = []
    for (day, start, end), members in blocks.items():
        parts = [s.part for _, s in members]
        minutes = time_to_minutes(end) - time_to_minutes(start)
        if len(members) > 1 or any(parts):
            count = len(members)
            if sorted(tuple(p or ()) for p in parts) != [(k, count) for k in range(1, count + 1)]:
                errors.append(f'{day} {start}-{end}: its {count} sessions must be parts [1, {count}] to '
                              f'[{count}, {count}]')
            elif minutes < count * MIN_PART_MINUTES:
                errors.append(f'{day} {start}-{end}: {count} parts of {minutes} min are under '
                              f'{MIN_PART_MINUTES} min; make it one session')
        a, b = time_to_minutes(start), time_to_minutes(end)
        if any(d == day and a < y and x < b for d, x, y in used):
            errors.append(f'{day} {start}-{end}: overlaps another block of the main session room')
        used.append((day, a, b))
    return errors


def read_document(data: bytes, name: str, model: str, agenda: dict | None = None, request=None,
                  cache_dir: Path = CACHE) -> dict:
    """The document for ``make_schedule`` when the time plan table is not in its form.

    The LLM reads the table (and the paragraphs around it) into blocks and
    pieces; the key and the CB blocks are still the rules'. Raises LayoutError
    when no answer passes the checks, so the build fails as before.
    """
    found = surroundings(data, name)
    blocks = time_plan(docx_blocks(data))
    texts = outline_texts(blocks)
    prompt = (PROMPTS / 'document.md').read_text(encoding='utf-8')
    try:
        result = llm_cells.read_document(prompt, blocks, DocResult, lambda r: check_document(r, texts, agenda),
                                         model=model, cache_dir=cache_dir, wg_id='ran3', source=name,
                                         meeting=found['meeting_id'], request=request or gemini_request,
                                         version=VERSION)
    except ValueError as exc:
        raise LayoutError(f'The LLM could not read the time plan either: {exc}') from exc
    print(f'[ran3] Time plan table not in its form; {len(result.sessions)} sessions read by {model}')
    struck = {}
    for block in blocks:
        for row in block.get('rows', []):
            for cell in row:
                if cell.get('struck'):
                    struck[cell['id']] = cell['struck']
    pieces = []
    for n, s in enumerate(result.sessions):
        first = not s.part or s.part[0] == 1
        cell = {'id': f'doc{n}', 'day': s.day, 'start': s.start, 'end': s.end, 'part': None,
                'lines': [squash(line) for line in s.lines], 'struck': [], 'in_break': False, 'evening': False}
        pieces.append({'cell': cell, 'lines': cell['lines'], 'chair': s.chair, 'part': s.part,
                       'struck': [struck[r] for r in s.refs if r in struck] if first else [],
                       'title': clean(s.title) or ' '.join(s.title.split()),
                       'items': list(dict.fromkeys(s.agenda_items))})
    days = [{'day': day, 'cells': [],
             'breaks': [{'name': b.name, 'start': b.start, 'end': b.end} for b in result.breaks if b.day == day]}
            for day in DAYS if day in result.days]
    return {**found, 'days': days, 'pieces': pieces, 'notices': [FALLBACK_NOTICE]}


# ------------------------------------------------------------ offline room

class SheetSlot(BaseModel):
    start: str
    end: str
    topic: str
    moderator: str | None
    refs: list[str]


class SheetDay(BaseModel):
    sheet: str
    day: str
    month: int
    date: int
    slots: list[SheetSlot]


class SheetResult(BaseModel):
    sheets: list[SheetDay]


MONTH_NAMES = ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec')


def check_sheets(result: SheetResult, blocks: list[dict]) -> list[str]:
    errors = []
    sheets = {b['id']: b for b in blocks}
    if sorted(s.sheet for s in result.sheets) != sorted(sheets):
        errors.append(f'answer each sheet once: {sorted(sheets)}')
    for day in result.sheets:
        if day.sheet not in sheets:
            continue
        texts = outline_texts([sheets[day.sheet]])
        prefix = day.sheet
        heading = ' '.join([day.sheet, *texts.values()]).lower()
        if day.day not in DAYS or day.day[:3].lower() not in heading:
            errors.append(f'{prefix}: day {day.day!r} is not a weekday written on the sheet')
        if not (1 <= day.month <= 12 and 1 <= day.date <= 31) or MONTH_NAMES[day.month - 1] not in heading \
                or not re.search(rf'(?<!\d)0?{day.date}(?!\d)', heading):
            errors.append(f'{prefix}: the date {day.month}/{day.date} is not written on the sheet')
        allowed = written_times('\n'.join(texts.values()))
        for n, slot in enumerate(day.slots):
            here = f'{prefix} slot {n}'
            errors += check_times(here, slot.start, slot.end, allowed)
            if not slot.refs or not set(slot.refs) <= texts.keys():
                errors.append(f'{here}: refs must cite cells of this sheet')
                continue
            cited = [squash(texts[r]) for r in slot.refs]
            if squash(slot.topic) not in cited:
                errors.append(f'{here}: topic must be the text of a cited cell, copied exactly')
            if slot.moderator and squash(slot.moderator) not in cited:
                errors.append(f'{here}: moderator must be the text of a cited cell, copied exactly')
    return errors


def read_sheets(data: bytes, unread: list[str], name: str, model: str, meeting: str | None = None,
                request=None, cache_dir: Path = CACHE) -> list[dict]:
    """Booked slots of the offline room sheets the rules could not read, in ``offline_sheets``' form.

    Raises ValueError when no answer passes the checks; the sheets are then
    left out with a warning, as before.
    """
    blocks = [b for b in xlsx_blocks(data) if b['id'] in unread]
    prompt = (PROMPTS / 'sheets.md').read_text(encoding='utf-8')
    result = llm_cells.read_document(prompt, blocks, SheetResult, lambda r: check_sheets(r, blocks), model=model,
                                     cache_dir=cache_dir, wg_id='ran3', source=f'{name} ({", ".join(unread)})',
                                     meeting=meeting, request=request or gemini_request, version=VERSION)
    print(f'[ran3] Offline sheets {", ".join(unread)} not in their form; read by {model}')
    return [{'day': day.day, 'month': day.month, 'date': day.date, 'start': slot.start, 'end': slot.end,
             'topic': squash(slot.topic), 'moderator': squash(slot.moderator or '')}
            for day in result.sheets for slot in day.slots]
