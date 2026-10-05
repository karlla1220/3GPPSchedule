"""Read a RAN2 session schedule DOCX conservatively.

The schedule is written by people, and its layout can change with the author.
Only broad conventions are assumed: one table whose first column holds time
ranges, full-width rows that name weekdays, and one column per room. A room
cell may be merged vertically, even across two time slots, so cells are
identified by their XML element. Tracked changes are read in accepted form.

Nothing is rejected or invented for not fitting: rows that cannot be placed,
day notes and every paragraph outside the table are kept as supplements, which
the page shows below the grid.
"""
from __future__ import annotations

import csv
import io
import re

from docx import Document
from docx.text.paragraph import Paragraph

from shared.portal_meetings import meeting_key

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
CLOCK = r'(\d{1,2})[:.](\d{2})'
SLOT = re.compile(CLOCK + r'\s*[-–—~]\s*' + CLOCK)
# R2_135_Schedule_v11.docx, R2_133b_Schedule_v14.docx, R2_131bis_Schedule v19.docx
FILE = re.compile(r'R2[_-]?(\d+)(bis|b)?[_ -]+Schedule[_ -]*v(\d+)\.docx', re.I)
TITLE = re.compile(r'RAN2\s*[-#]?\s*(\d+)\s*(bis|b)?(?![a-z0-9])', re.I)
# "Morning coffee:   10:30 to 11:00" names the gaps between slots.
BREAK = re.compile(r'^\s*([A-Za-z][A-Za-z ]*?)\s*:\s*' + CLOCK + r'\s*(?:to|[-–—])\s*' + CLOCK)


class LayoutError(ValueError):
    """The schedule table is not in a layout these rules recognise."""


def meeting_id(number, suffix) -> str:
    return meeting_key(f'RAN2#{number}{suffix or ""}')


def file_info(name: str):
    """Return (meeting_id, version) for a schedule filename, or None."""
    match = FILE.fullmatch(name.strip())
    if not match:
        return None
    return meeting_id(match[1], match[2]), int(match[3])


def clock(hour, minute) -> str:
    hour, minute = int(hour), int(minute)
    if hour < 7:
        hour += 12  # "6:45pm" style afternoon times written without a meridiem
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f'Invalid time {hour}:{minute}')
    return f'{hour:02d}:{minute:02d}'


def _deleted(run) -> bool:
    if any(a.tag in (W + 'del', W + 'moveFrom') for a in run.iterancestors()):
        return True
    props = run.find(W + 'rPr')
    if props is None:
        return False
    for tag in ('strike', 'dstrike'):
        node = props.find(W + tag)
        if node is not None and node.get(W + 'val', 'true') not in ('0', 'false', 'off'):
            return True
    return False


def paragraph_text(element) -> str:
    """Accepted text: insertions kept, deletions and struck runs dropped.

    python-docx's .text skips runs inside <w:ins>, so it is not used.
    """
    parts = []
    for run in element.iter(W + 'r'):
        if _deleted(run):
            continue
        for node in run:
            if node.tag == W + 't':
                parts.append(node.text or '')
            elif node.tag in (W + 'br', W + 'cr'):
                parts.append('\n')
            elif node.tag == W + 'tab':
                parts.append('\t')
    return ''.join(parts)


def squash(text: str) -> str:
    return ' '.join(text.replace(' ', ' ').split())


def cell_lines(cell) -> list[str]:
    """Text lines; one '' marks a blank line, which chairs use as a separator."""
    lines = []
    for paragraph in cell._tc.iter(W + 'p'):
        for line in paragraph_text(paragraph).split('\n'):
            line = squash(line)
            if line or (lines and lines[-1]):
                lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def room_info(text: str, index: int) -> dict:
    label = squash(text.replace('*', ' '))
    match = re.fullmatch(r'(?:Brk|Break[- ]?out)\s*(\d+)(?:\s+room)?', label, re.I)
    if match:
        return {'id': f'brk{match[1]}', 'name': f'Breakout {match[1]}', 'label': label}
    if re.fullmatch(r'Main(?:\s+room)?', label, re.I):
        return {'id': 'main', 'name': 'Main', 'label': label}
    slug = re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-') or f'room{index}'
    return {'id': slug, 'name': label or f'Room {index}', 'label': label}


def _day_of(text: str):
    word = text.split()[0].rstrip(':,') if text.split() else ''
    return next((d for d in DAYS if d.lower() == word.lower()), None)


def _first_cell(row) -> str:
    return squash(paragraph_text(row.cells[0]._tc)) if row.cells else ''


def _schedule_table(doc):
    """The table with the most time-range rows in its first column."""
    scored = [(sum(bool(SLOT.search(_first_cell(row))) for row in table.rows), i, table)
              for i, table in enumerate(doc.tables)]
    best = max(scored, default=(0, 0, None), key=lambda s: (s[0], -s[1]))
    if not best[0]:
        raise LayoutError('No table with time ranges in its first column')
    return best[2]


# ------------------------------------------------------------------ the table

def _read_table(table):
    """Rooms, weekdays and cells; whatever does not fit becomes a note."""
    rows = list(table.rows)
    start = next((i for i, row in enumerate(rows)
                  if SLOT.search(_first_cell(row)) or _day_of(_first_cell(row))), len(rows))
    header = rows[start - 1].cells if start else []
    rooms = [room_info(squash(paragraph_text(cell._tc)), i) for i, cell in enumerate(header[1:], 1)]
    if not rooms:
        rooms = [room_info('', i) for i in range(1, max(len(r.cells) for r in rows))]
    for i, room in enumerate(rooms):
        if any(r['id'] == room['id'] for r in rooms[:i]):
            room['id'] = f'{room["id"]}-{i + 1}'

    days, notes, current = [], [], None

    def note(text, day=None):
        text = squash(text)
        if text:
            notes.append({'day': day or (current['day'] if current else ''), 'text': text})

    for ri, row in enumerate(rows[start:], start):
        cells = row.cells
        first = _first_cell(row)
        if len({id(c._tc) for c in cells}) == 1:
            day = _day_of(first)
            if day:
                current = {'day': day, 'slots': [], 'cells': [], 'notes': []}
                days.append(current)
            elif current is not None and first:
                current['notes'].append(first)
                note(first)
            else:
                note(first)
            continue
        content = [' / '.join(line for line in cell_lines(c) if line) for c in cells[1:]]
        slot = SLOT.search(first)
        try:
            begin, end = (clock(slot[1], slot[2]), clock(slot[3], slot[4])) if slot else (None, None)
        except ValueError:
            begin = end = None
        if begin is None or begin >= end or current is None:
            if any(content):
                note(f'{first} — not placed on the grid: ' + ' | '.join(c for c in content if c))
            continue
        if current['slots'] and begin < current['slots'][-1][0]:
            # The clock went back under a blank full-width row: most likely the
            # next weekday's label was left empty (RAN2#135 v00 lost Friday).
            index = DAYS.index(current['day']) + 1
            if index < len(DAYS) and not any(d['day'] == DAYS[index] for d in days):
                current = {'day': DAYS[index], 'slots': [], 'cells': [], 'notes': []}
                days.append(current)
                note(f'Weekday label missing in the table; rows from {begin} are shown as {current["day"]}.')
        if (begin, end) not in current['slots']:
            current['slots'].append((begin, end))
        known = {id(c['_tc']): c for c in current['cells']}
        for ci, room in enumerate(rooms, 1):
            if ci >= len(cells):
                break
            tc = cells[ci]._tc
            if any(c._tc is tc for c in cells[:ci]):
                continue  # horizontal merge: the leftmost column owns the text
            entry = known.get(id(tc))
            if entry is None:
                span = [rooms[i - 1]['id'] for i in range(1, min(len(cells), len(rooms) + 1)) if cells[i]._tc is tc]
                entry = {'_tc': tc, 'id': f'r{ri}c{ci}', 'rooms': span, 'start': begin, 'end': end,
                         'lines': cell_lines(cells[ci])}
                current['cells'].append(entry)
                known[id(tc)] = entry
            else:
                entry['start'], entry['end'] = min(entry['start'], begin), max(entry['end'], end)
    for day in days:
        for cell in day['cells']:
            del cell['_tc']
        day['cells'] = [c for c in day['cells'] if any(c['lines'])]
        day['slots'].sort()
    return rooms, days, notes


# ------------------------------------------------------------- outside the table

def _bold(paragraph: Paragraph) -> bool:
    style_bold = False
    style = paragraph.style
    while style is not None:
        if style.font.bold is not None:
            style_bold = style.font.bold
            break
        style = style.base_style
    runs = [r for r in paragraph._p.iter(W + 'r') if not _deleted(r) and squash(paragraph_text(r))]
    if not runs:
        return False
    for run in runs:
        node = run.find(f'{W}rPr/{W}b')
        bold = style_bold if node is None else node.get(W + 'val', 'true') not in ('0', 'false', 'off')
        if not bold:
            return False
    return True


def _tab_table(texts: list[str]) -> dict:
    """Tab-aligned paragraphs as table rows.

    Alignment tabs leave empty cells, which are dropped; a leading empty cell
    marks a wrapped line, which continues the previous row's last cell when
    that row is still short ("[004]  ... based" / "  on submitted documents ...").
    """
    raw = []
    for text in texts:
        cells = [squash(c) for c in text.split('\t')]
        lead = bool(cells) and not cells[0]
        raw.append((lead, [c for c in cells if c]))
    width = max((len(cells) + lead for lead, cells in raw), default=0)
    rows = []
    for lead, cells in raw:
        if lead and rows and cells and len(rows[-1]) + len(cells) - 1 <= width and len(rows[-1]) < width:
            rows[-1][-1] = f'{rows[-1][-1]} {cells[0]}'
            rows[-1].extend(cells[1:])
        else:
            rows.append(([''] if lead else []) + cells)
    rows = [row + [''] * (width - len(row)) for row in rows if any(row)]
    # "Number  Title  Day/Time ..." heads a list even before any entry is added.
    header = (bool(rows) and (len(rows) > 1 or width >= 3) and all(rows[0])
              and not any(re.search(r'\d', c) for c in rows[0]))
    return {'type': 'table', 'header': header, 'rows': rows}


def _other_table(table) -> dict:
    rows = []
    for row in table.rows:
        seen, cells = set(), []
        for cell in row.cells:
            if id(cell._tc) not in seen:
                seen.add(id(cell._tc))
                cells.append('\n'.join(line for line in cell_lines(cell) if line))
        if any(cells):
            rows.append(cells)
    width = max((len(r) for r in rows), default=0)
    return {'type': 'table', 'header': False, 'rows': [r + [''] * (width - len(r)) for r in rows]}


def supplements(doc, schedule_table, title: str, notes: list[dict]) -> list[dict]:
    """Everything outside the schedule table, in document order.

    Short bold paragraphs become headings, runs of tab-aligned paragraphs
    become tables, other paragraphs stay as text. Notes taken from the table
    itself (day notes, rows not placed) follow at the end.
    """
    blocks, run = [], []

    def flush():
        if run:
            blocks.append(_tab_table(run))
            run.clear()

    for child in doc.element.body.iterchildren():
        if child.tag == W + 'tbl':
            flush()
            if schedule_table is None or child is not schedule_table._tbl:
                block = _other_table(next(t for t in doc.tables if t._tbl is child))
                if block['rows']:
                    blocks.append(block)
            continue
        if child.tag != W + 'p':
            continue
        text = paragraph_text(child).strip('\n')
        if not squash(text) or squash(text) == title:
            continue
        if '\t' in text:
            run.append(text)
            continue
        flush()
        paragraph = Paragraph(child, doc._body)
        bold = _bold(paragraph)
        lines = [squash(line) for line in text.split('\n')]
        text = '\n'.join(line for line in lines if line)
        if bold and len(text.split()) <= 8 and '\n' not in text:
            blocks.append({'type': 'heading', 'text': text})
        else:
            blocks.append({'type': 'paragraph', 'text': text, 'bold': bold})
    flush()
    if notes:
        blocks.append({'type': 'heading', 'text': 'Notes in the schedule table'})
        blocks.append({'type': 'table', 'header': True,
                       'rows': [['Day', 'Note']] + [[n['day'], n['text']] for n in notes]})
    return blocks


def extract_breaks(paragraphs: list[str]) -> list[dict]:
    breaks = []
    for text in paragraphs:
        match = BREAK.match(text)
        if match and re.search(r'coffee|lunch|break|tea', match[1], re.I):
            try:
                breaks.append({'name': squash(match[1]), 'start': clock(match[2], match[3]),
                               'end': clock(match[4], match[5])})
            except ValueError:
                continue
    return breaks


def identify(doc, name: str) -> tuple[str, str, list[str]]:
    """(meeting id, title paragraph, paragraph texts)."""
    paragraphs = [paragraph_text(p._p) for p in doc.paragraphs]
    title = next((squash(p) for p in paragraphs if TITLE.search(p) and re.search(r'schedule', p, re.I)), '')
    match = TITLE.search(title)
    titled = meeting_id(match[1], match[2]) if match else None
    info = file_info(name)
    # The filename is what selected this file; a title copied from the last
    # meeting's document must not move the schedule to another meeting.
    meeting = info[0] if info else titled
    if meeting is None:
        raise ValueError('Cannot tell the RAN2 meeting from the filename or the schedule title')
    if titled and info and titled != meeting:
        print(f'[ran2] Title says {titled} but the file is {meeting}; using the filename')
    return meeting, title, paragraphs


def extract_document(data: bytes, name: str) -> dict:
    doc = Document(io.BytesIO(data))
    meeting, title, paragraphs = identify(doc, name)
    table = _schedule_table(doc)
    rooms, days, notes = _read_table(table)
    if not any(day['cells'] for day in days):
        raise LayoutError('The RAN2 schedule table has no sessions')
    return {'meeting_id': meeting, 'title': title, 'rooms': rooms, 'days': days,
            'breaks': extract_breaks(paragraphs),
            'supplements': supplements(doc, table, title, notes)}


def outline(doc) -> list[dict]:
    """The whole body for the LLM fallback: paragraphs and table cells with ids.

    A merged cell is listed once, at its first row and column.
    """
    blocks, table_index = [], 0
    for index, child in enumerate(doc.element.body.iterchildren()):
        if child.tag == W + 'p':
            text = paragraph_text(child).strip()
            if squash(text):
                blocks.append({'id': f'p{index}', 'text': '\n'.join(squash(t) for t in text.split('\n') if squash(t))})
        elif child.tag == W + 'tbl':
            table = next(t for t in doc.tables if t._tbl is child)
            seen, rows = set(), []
            for ri, row in enumerate(table.rows):
                cells = []
                for ci, cell in enumerate(row.cells):
                    if id(cell._tc) in seen:
                        continue
                    seen.add(id(cell._tc))
                    text = '\n'.join(line for line in cell_lines(cell) if line)
                    if text:
                        cells.append({'id': f't{table_index}.r{ri}.c{ci}', 'text': text})
                if cells:
                    rows.append(cells)
            blocks.append({'id': f't{table_index}', 'rows': rows})
            table_index += 1
    return blocks


def agenda_map(data: bytes) -> dict[str, str]:
    """agenda.csv published in each meeting's Agenda folder: "item","description".

    Malformed rows are skipped; descriptions are only an aid.
    """
    result = {}
    for row in csv.reader(io.StringIO(data.decode('utf-8-sig', errors='replace'))):
        if len(row) < 2:
            continue
        item, description = row[0].strip().rstrip('.'), squash(row[1])
        if re.fullmatch(r'\d+(?:\.\d+)*', item) and description:
            result.setdefault(item, description)
    return result
