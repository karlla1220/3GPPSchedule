"""Read a RAN4 meeting schedule PPTX: one slide per weekday, one table per slide.

The chair keeps one file through the meeting (#118bis to #120bis used the same
layout): a cover slide, then a slide per weekday whose title shape names the
day and whose table has the session columns across ("RAN4 Main (Yang
Session)", "Ad hoc room") and the time blocks down ("8:30-10:30"). Break rows
are told by the time cell's fill, FFC000; their names follow the venue's
language ("커피 타임", "茶歇") and their width changes from day to day.

A cell is a list of paragraphs. A blank paragraph is a blank line, which the
chair uses to split a block into parts; struck-through text is deleted text.
Nothing that does not fit is dropped: rows without a readable time are kept as
notes, and text outside the tables is shown below the grid.

The file is a zip of XML parts, read without python-pptx.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile

from defusedxml import ElementTree

from shared.portal_meetings import meeting_key

A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
BREAK_FILL = 'FFC000'
CLOCK = r'(\d{1,2})[:.](\d{2})'
# "8:30-10:30", "9:00-09:30", "19:30 -22:00 RAN4 Dinner ...", "14:00-EOM"
SLOT = re.compile(CLOCK + r'\s*[-–—~]\s*(?:' + CLOCK + r'|(EOM)\b)', re.I)
# RAN4_120bis_meeting_schedule_THU_afternoon.pptx, RAN4_118bis_meeting_schedule_v11.pptx,
# RAN4_120_meeting_schedule_after Thursday.pptx
FILE = re.compile(r'RAN4[_ -]?(\d+)(bis|b)?[_ -]+meeting[_ -]+schedule(?:[_ -]+(.*?))?\.pptx', re.I)
TITLE = re.compile(r'RAN4\s*#?\s*(\d+)\s*(bis|b)?(?![a-z0-9])', re.I)
SESSION = re.compile(r'\(\s*([A-Za-z]+)\s+Session\s*\)', re.I)


class LayoutError(ValueError):
    """The schedule is not in a layout these rules recognise."""


def meeting_id(number, suffix) -> str:
    return meeting_key(f'RAN4#{number}{suffix or ""}')


def file_info(name: str):
    """(meeting_id, label) for a schedule file name, or None. The label is what follows
    "meeting_schedule": "v11", "THU_afternoon", "after Thursday" or ""."""
    if name.startswith('~$'):
        return None   # PowerPoint's lock file
    match = FILE.fullmatch(name.strip())
    if not match:
        return None
    return meeting_id(match[1], match[2]), (match[3] or '').strip()


def clock(hour, minute, meridiem=None) -> str:
    hour, minute = int(hour), int(minute)
    if meridiem and meridiem.lower() == 'pm' and hour < 12:
        hour += 12
    elif not meridiem and hour < 7:
        hour += 12   # "3:00" in the afternoon written without a meridiem
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f'Invalid time {hour}:{minute}')
    return f'{hour:02d}:{minute:02d}'


def squash(text: str) -> str:
    return ' '.join(text.replace('\xa0', ' ').split())


def _struck(run) -> bool:
    props = run.find(A + 'rPr')
    return props is not None and props.get('strike') not in (None, 'noStrike')


def paragraph_lines(element) -> list[str]:
    """One line per paragraph; '' for a blank one. A paragraph whose text is all
    struck through is deleted, not blank."""
    lines = []
    for paragraph in element.iter(A + 'p'):
        kept, struck = [], False
        for node in paragraph:
            if node.tag == A + 'r':
                text = ''.join(t.text or '' for t in node.iter(A + 't'))
                if _struck(node):
                    struck = struck or bool(text.strip())
                else:
                    kept.append(text)
            elif node.tag == A + 'br':
                kept.append('\n')
            elif node.tag == A + 'fld':
                kept.append(''.join(t.text or '' for t in node.iter(A + 't')))
        text = ''.join(kept)
        if struck and not text.strip():
            continue
        lines.extend(squash(part) for part in text.split('\n'))
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    # Two blank lines are one gap.
    return [line for i, line in enumerate(lines) if line or lines[i - 1]]


def _fill(cell) -> str:
    props = cell.find(A + 'tcPr')
    color = props.find(f'{A}solidFill/{A}srgbClr') if props is not None else None
    return (color.get('val') or '').upper() if color is not None else ''


def room_info(text: str, index: int) -> dict:
    """A session column: "RAN4 Main (Yang Session)" is the Yang session; other
    columns are named by their header, an unnamed one by its position."""
    name = squash(text)
    session = SESSION.search(name)
    if session:
        ident = session[1].lower()
    else:
        ident = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or f'column-{index}'
    return {'id': ident, 'name': name, 'session': session[1] if session else None}


def read_slot(text: str):
    """(start, end, eom, extra) of a time cell; end is None for "14:00-EOM".

    Parenthesised words are skipped ("15:00 (or earlier time) -16:00") and
    reported in extra, as are words after the times (a dinner row's name).
    """
    plain = re.sub(r'\([^()]*\)', ' ', text)
    match = SLOT.search(plain)
    if not match:
        return None
    start = clock(match[1], match[2])
    end = clock(match[3], match[4]) if match[3] else None
    extra = squash(plain[:match.start()] + ' ' + plain[match.end():])
    if re.search(r'\([^()]*\)', text):
        extra = squash(extra + ' ' + ' '.join(re.findall(r'\([^()]*\)', text)))
    if end is not None and end <= start:
        return None
    return start, end, bool(match[5]), extra


def _slide_texts(root) -> list[str]:
    """Text of the shapes outside tables, one entry per shape."""
    texts = []
    for shape in root.iter(P + 'sp'):
        lines = [line for line in paragraph_lines(shape) if line]
        if lines:
            texts.append('\n'.join(lines))
    return texts


def _read_table(table, day: str) -> dict:
    rows = table.findall(A + 'tr')
    grid = [list(row.findall(A + 'tc')) for row in rows]
    header_index = next((i for i, cells in enumerate(grid) if cells and read_slot(' '.join(paragraph_lines(cells[0])))), 1) - 1
    header = grid[header_index] if header_index >= 0 else []
    rooms = [room_info(' '.join(l for l in paragraph_lines(cell) if l), i) for i, cell in enumerate(header[1:], 1)]
    for i, room in enumerate(rooms):
        if any(r['id'] == room['id'] for r in rooms[:i]):
            room['id'] = f'{room["id"]}-{i + 1}'
    result = {'day': day, 'rooms': rooms, 'slots': [], 'breaks': [], 'cells': [], 'notes': []}
    pending = {}   # column -> cell still spanning down (rowSpan)
    for ri in range(header_index + 1, len(grid)):
        cells = grid[ri]
        if not cells:
            continue
        first = ' '.join(l for l in paragraph_lines(cells[0]) if l)
        slot = read_slot(first)
        if slot is None:
            content = ' | '.join(' / '.join(l for l in paragraph_lines(c) if l) for c in cells[1:])
            if squash(first + content.replace('|', '')):
                result['notes'].append(f'{first} — not placed on the grid: {content}'.strip(' —:'))
            continue
        start, end, eom, extra = slot
        if _fill(cells[0]) == BREAK_FILL:
            # The name is in the merged cell beside the time, or after the time
            # in a row merged across (the #119 dinner).
            names = [' '.join(l for l in paragraph_lines(c) if l) for c in cells[1:] if not c.get('hMerge')]
            name = next((n for n in names if n), extra) or 'Break'
            if end is not None:
                result['breaks'].append({'name': name, 'start': start, 'end': end})
            continue
        result['slots'].append({'start': start, 'end': end, 'eom': eom, 'extra': extra})
        for ci, cell in enumerate(cells[1:], 1):
            if ci > len(rooms):
                break
            if cell.get('hMerge') or cell.get('vMerge'):
                if cell.get('vMerge') and ci in pending:
                    pending[ci]['end'] = end
                    pending[ci]['eom'] = pending[ci]['eom'] or eom
                continue
            span = int(cell.get('gridSpan') or 1)
            entry = {'id': f'{day[:3].lower()}-r{ri}c{ci}', 'rooms': [r['id'] for r in rooms[ci - 1:ci - 1 + span]],
                     'start': start, 'end': end, 'eom': eom, 'row_extra': extra,
                     'lines': paragraph_lines(cell)}
            if int(cell.get('rowSpan') or 1) > 1:
                pending[ci] = entry
            else:
                pending.pop(ci, None)
            if any(entry['lines']):
                result['cells'].append(entry)
    return result


def core_properties(archive) -> dict:
    """cp:revision and dcterms:modified: the order of the chair's saves within a meeting."""
    try:
        root = ElementTree.fromstring(archive.read('docProps/core.xml'))
    except KeyError:
        return {'revision': 0, 'modified': ''}
    revision = next((e.text for e in root if e.tag.endswith('}revision')), '') or ''
    modified = next((e.text for e in root if e.tag.endswith('}modified')), '') or ''
    return {'revision': int(revision) if revision.strip().isdigit() else 0, 'modified': modified.strip()}


def read_core(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return core_properties(archive)


def _slides(archive) -> list[str]:
    names = [n for n in archive.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml', n)]
    return sorted(names, key=lambda n: int(re.search(r'(\d+)\.xml$', n)[1]))


def extract_document(data: bytes, name: str) -> dict:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise LayoutError(f'{name} is not a PPTX file') from exc
    with archive:
        info = file_info(name)
        days, supplements, title, cover = [], [], '', []
        for slide in _slides(archive):
            root = ElementTree.fromstring(archive.read(slide))
            texts = _slide_texts(root)
            table = next(root.iter(A + 'tbl'), None)
            if table is None:
                cover.extend(texts)
                title = title or next((t for t in texts if TITLE.search(t) and re.search(r'schedule', t, re.I)), '')
                continue
            day = next((t for t in texts if t in DAYS), None)
            if day is None:
                raise LayoutError(f'A table slide of {name} has no weekday title')
            days.append(_read_table(table, day))
            others = [t for t in texts if t != day]
            if others:
                supplements.append({'type': 'heading', 'text': day})
                supplements.extend({'type': 'paragraph', 'text': t, 'bold': False} for t in others)
        properties = core_properties(archive)
    if not days or not any(day['cells'] for day in days):
        raise LayoutError(f'{name} has no schedule table with sessions')
    match = TITLE.search(title)
    titled = meeting_id(match[1], match[2]) if match else None
    # The file name is what selected this file; a cover copied from the last
    # meeting must not move the schedule to another meeting.
    meeting = info[0] if info else titled
    if meeting is None:
        raise ValueError('Cannot tell the RAN4 meeting from the file name or the cover slide')
    if titled and info and titled != meeting:
        print(f'[ran4] Cover says {titled} but the file is {meeting}; using the file name')
    notes = [(day['day'], note) for day in days for note in day.pop('notes')]
    if notes:
        supplements.append({'type': 'heading', 'text': 'Notes in the schedule table'})
        supplements.append({'type': 'table', 'header': True,
                            'rows': [['Day', 'Note']] + [[d, n] for d, n in notes]})
    return {'meeting_id': meeting, 'title': title, 'cover': cover, 'label': info[1] if info else '',
            **properties, 'days': days, 'supplements': supplements}


def agenda_map(data: bytes) -> dict[str, str]:
    """agenda.csv published in each meeting's Agenda folder: "item","description"."""
    result = {}
    for row in csv.reader(io.StringIO(data.decode('utf-8-sig', errors='replace'))):
        if len(row) < 2:
            continue
        item, description = row[0].strip().rstrip('.'), squash(row[1])
        if re.fullmatch(r'\d+(?:\.\d+)*', item) and description:
            result.setdefault(item, description)
    return result
