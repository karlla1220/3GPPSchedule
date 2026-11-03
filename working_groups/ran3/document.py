"""Read the RAN3 time plan from the chair's living agenda, and the offline room sheets.

The living agenda (``RAN3_133bis_agenda_20261014_1825.docx``) is the agenda,
the chair's notes and the schedule in one document. The schedule is the table
whose first row names Monday to Friday: a time column, then one column per day
for the single main session room. The table has kept one fixed form since
RAN3#129 (13 rows, 7 columns), so it is read by rules, and a table that does
not fit is a LayoutError rather than a guess.

Only some rows carry their times: breaks name their range (``10:30~11:00``),
the first rows their starts (``08:00 / 08:30``, ``09:00``). A block whose time
cell is empty runs from the break above to the next written time. Rows under
one empty time cell (17:00-19:00) are pieces of one block without times; the
pipeline shows them as ``1/2 of 120 min``.

The offline room has its own workbook, ``RAN3 offline discussion_ver_NN.xlsx``:
a sheet per day (``Oct14 Wed``), 30-minute slots, a topic and a moderator.
"""
from __future__ import annotations

import io
import re

from docx import Document

from shared.docx_text import paragraph_text, W
from shared.portal_meetings import meeting_key

DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
ROWS, COLUMNS = 13, 7
# RAN3_133bis_agenda_20261014_1825.docx, RAN3_134_agenda_20261023.docx,
# RAN3_133bis_agenda_20261016_EOM.docx, RAN3_129_agenda_20250829_1620_EOM.docx.
# Vice-chair copies carry initials instead (_LX, _yxd, -yxd) and are not schedules.
FILE = re.compile(r'RAN3_(\d+)(bis)?_agenda_(\d{8})(?:_(\d{4}))?(?:([_-])([A-Za-z]+))?\.docx', re.I)
TITLE = re.compile(r'WG3\s+Meeting\s*#\s*(\d+)\s*-?\s*(bis)?', re.I)
CLOCK = re.compile(r'(?<!\d)(\d{1,2}):?(\d{2})(?!\d)')
RANGE = re.compile(r'(\d{1,2}):?(\d{2})\s*~\s*(\d{1,2}):?(\d{2})')
BREAK = re.compile(r'^(?:Coffee|Lunch)\s+(?:Break|Time)\*?$', re.I)
# CB: # 8_6GSBI ... (moderator - CATT); "# 6GAIML_framework" and "# AIML1_Multi-hop" too.
CB_LINE = re.compile(r'\bCB\s*:\s*#\s*([A-Za-z0-9]+)(?:_(\S*))?')
MODERATOR = re.compile(r'\(\s*moderator\s*[-–:]\s*([^)]+?)\s*\)', re.I)
HEADING = re.compile(r'^(\d+(?:\.\d+)*)\.?\s+\S')
MONTHS = {m: i for i, m in enumerate(('jan', 'feb', 'mar', 'apr', 'may', 'jun',
                                      'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), 1)}
SHEET = re.compile(r'^\s*([A-Za-z]{3})[a-z]*\s*(\d{1,2})\s+([A-Za-z]{3})', re.I)
SLOT = re.compile(r'^\s*(\d{1,2}):?(\d{2})\s*~\s*(\d{1,2}):?(\d{2})\s*$')


class LayoutError(ValueError):
    """The schedule table is not in the form these rules read."""


def meeting_id(number, suffix) -> str:
    return meeting_key(f'RAN3#{number}{suffix or ""}')


def file_info(name: str):
    """(meeting_id, version) for a chair's living agenda; None for anything else.

    The version sorts as the files do: by date, a closing EOM after every
    version of its day, then the time of day ("20261014.0.1825").
    """
    match = FILE.fullmatch(name.strip())
    if not match or (match[6] and match[6].upper() != 'EOM'):
        return None
    eom = 1 if match[6] else 0
    return meeting_id(match[1], match[2]), f'{match[3]}.{eom}.{match[4] or "0000"}'


def source_label(name: str) -> str:
    """The header name of a living agenda: its file name without the meeting.

    "RAN3_133bis_agenda_20261014_1825.docx" is "agenda_20261014_1825", so the
    file can be found from it. page.py appends "EOM" itself.
    """
    match = FILE.fullmatch(name.strip())
    if not match:
        return file_stem(name)
    return f'agenda_{match[3]}_{match[4]}' if match[4] else f'agenda_{match[3]}'


def file_stem(name: str) -> str:
    """A file name without its extension and a leading "RAN3"/"RAN3_134bis"."""
    stem = name.strip().rsplit('.', 1)[0]
    return re.sub(r'^RAN3[ _](?:\d+(?:-?bis)?_)?', '', stem, flags=re.I) or stem


def clock(hour, minute) -> str:
    hour, minute = int(hour), int(minute)
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f'Invalid time {hour}:{minute}')
    return f'{hour:02d}:{minute:02d}'


def squash(text: str) -> str:
    return ' '.join(text.replace('\xa0', ' ').split())


def cell_lines(tc, struck: list[str] | None = None) -> list[str]:
    """Text lines of a cell; one '' marks a blank line between lines."""
    lines = []
    for paragraph in tc.iter(W + 'p'):
        for line in paragraph_text(paragraph, struck).split('\n'):
            line = squash(line)
            if line or (lines and lines[-1]):
                lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _grid(table):
    """Each row's cell element per grid column; merged cells share one element."""
    return [[cell._tc for cell in row.cells] for row in table.rows]


def weekday_table(doc):
    """(table, head) for the table whose first row names the weekdays, in any shape; else (None, None)."""
    for table in doc.tables:
        head = [squash(c.text) for c in table.rows[0].cells] if table.rows else []
        if all(day in head for day in DAYS):
            return table, head
    return None, None


def schedule_table(doc):
    """The table whose first row names the weekdays, checked against RAN3's form."""
    table, head = weekday_table(doc)
    if table is None:
        raise LayoutError('No table with Monday to Friday in its first row')
    if len(table.rows) != ROWS or len(table.columns) != COLUMNS:
        raise LayoutError(f'Schedule table is {len(table.rows)}x{len(table.columns)}, '
                          f'not the {ROWS}x{COLUMNS} RAN3 form')
    return table, head


# ------------------------------------------------------------------ blocks

def _row_groups(grid) -> list[list[int]]:
    """Rows under one time cell, in order. Consecutive rows with empty time
    cells are one block too, merged or not (17:00-19:00 is two rows)."""
    groups, previous, previous_empty = [], None, False
    for r in range(1, len(grid)):
        tc = grid[r][0]
        empty = not cell_lines(tc)
        if groups and (tc is previous or (empty and previous_empty)):
            groups[-1].append(r)
        else:
            groups.append([r])
        previous, previous_empty = tc, empty
    return groups


def blocks(grid) -> list[dict]:
    """Times of each row group: {'rows', 'start', 'end', 'row_times', 'break', 'last'}.

    row_times is a start per row when the time cell writes one per row
    (``08:00 / 08:30``); otherwise the rows are untimed pieces of the block.
    """
    raw = []
    for rows in _row_groups(grid):
        text = ' '.join(cell_lines(grid[rows[0]][0]))
        span = RANGE.search(text)
        if span:
            raw.append({'rows': rows, 'start': clock(span[1], span[2]), 'end': clock(span[3], span[4]),
                        'break': True})
            continue
        times = [clock(h, m) for h, m in CLOCK.findall(text)]
        raw.append({'rows': rows, 'times': times, 'start': times[0] if times else None, 'break': False})
    result = []
    for i, block in enumerate(raw):
        if block['start'] is None:
            if not result:
                raise LayoutError('The first schedule row has no time')
            block['start'] = result[-1]['end']
        if 'end' not in block:
            following = next((b['start'] for b in raw[i + 1:] if b['start']), None)
            times = block['times']
            if following is None and len(times) >= 2:
                block['end'] = times[-1]       # the evening rows: "19:00 / 20:00"
                block['last'] = True
            elif following is None:
                raise LayoutError(f'No end time for the schedule rows from {block["start"]}')
            else:
                block['end'] = following
            if following is not None and len(times) == len(block['rows']) > 1:
                block['row_times'] = times
        if block['end'] <= block['start']:
            raise LayoutError(f'Schedule rows from {block["start"]} end at {block["end"]}')
        result.append(block)
    return result


def _span_times(block, rows):
    """Start, end and part of a cell over rows of one block."""
    if block.get('row_times'):
        times = block['row_times'] + [block['end']]
        index = block['rows'].index(rows[0])
        return times[index], times[index + len(rows)], None
    n = len(block['rows'])
    if block.get('last') or len(rows) == n or n == 1:
        return block['start'], block['end'], None
    # Untimed rows of one block (17:00-19:00): piece k of n.
    if len(rows) == 1:
        return block['start'], block['end'], [block['rows'].index(rows[0]) + 1, n]
    raise LayoutError(f'A cell covers {len(rows)} of the {n} untimed rows from {block["start"]}')


def read_days(table, head) -> list[dict]:
    grid = _grid(table)
    plan = blocks(grid)
    days = []
    for col, name in enumerate(head):
        if name not in DAYS:
            continue
        cells, breaks, seen = [], [], []
        for r in range(1, len(grid)):
            tc = grid[r][col]
            if any(tc is s for s in seen):
                continue
            seen.append(tc)
            rows = [x for x in range(r, len(grid)) if grid[x][col] is tc]
            covered = []
            for b in plan:
                part = [x for x in rows if x in b['rows']]
                if part:
                    covered.append((b, part))
            struck = []
            lines = cell_lines(tc, struck)
            if not lines and not struck:
                continue
            block = covered[0][0]
            if block['break']:
                label = lines[0] if lines and BREAK.match(lines[0]) else None
                if label:
                    breaks.append({'name': label, 'start': block['start'], 'end': block['end']})
                    lines = lines[1:]
                    while lines and not lines[0]:
                        lines.pop(0)
                if not lines and not struck:
                    continue
            if len(covered) == 1:
                start, end, part = _span_times(block, covered[0][1])
            else:
                start, part = _span_times(*covered[0])[0], None
                end = _span_times(*covered[-1])[1]
            evening = block.get('last', False)
            if evening:
                lines = [line for line in lines if line]
            cells.append({'id': f'{name[:3].lower()}.r{r}', 'day': name, 'start': start, 'end': end,
                          'part': part, 'lines': lines, 'struck': struck, 'in_break': block['break'],
                          'evening': evening})
        evening = [c for c in cells if c['evening']]
        if len(evening) > 1:
            # Rows of the evening block carry one text: "RAN3 Social Event" / "(self-funded dinner)".
            joined = {**evening[0], 'start': min(c['start'] for c in evening), 'end': max(c['end'] for c in evening),
                      'lines': [line for c in evening for line in c['lines']],
                      'struck': [text for c in evening for text in c['struck']]}
            cells = [c for c in cells if not c['evening']] + [joined]
        days.append({'day': name, 'cells': cells, 'breaks': breaks})
    return days


# ---------------------------------------------------------------- the rest

def legend(doc, table) -> list[list[str]]:
    """The key printed under the table: "L, X" -> "chaired by Vice-Chairs", "*" -> "if needed"."""
    if table is None:
        return []
    body = list(doc.element.body.iterchildren())
    index = body.index(table._tbl)
    rows = []
    for element in body[index + 1:]:
        if element.tag != W + 'p':
            break
        # The key shows strikethrough by striking its own word.
        for line in paragraph_text(element, keep_struck=True).split('\n'):
            pair = [squash(part) for part in line.split('\t') if squash(part)]
            if len(pair) == 2:
                rows.append(pair)
        if rows:
            break
    return rows


def cb_blocks(doc, schedule) -> dict[str, dict]:
    """CB id -> the agenda item whose notes hold its "CB: # 8_6GSBI" block.

    The time plan writes only "6G Offline (CB#8)"; the living agenda says under
    which item the chair opened CB #8 and who moderates it. The EOM version
    drops these blocks, so the pipeline keeps the last ones it saw.
    """
    found = {}
    for table in doc.tables:
        if schedule is not None and table._tbl is schedule._tbl:
            continue
        item = None
        for row in table.rows:
            elements = []
            for cell in row.cells:
                if not any(cell._tc is e for e in elements):
                    elements.append(cell._tc)
            texts = ['\n'.join(cell_lines(tc)) for tc in elements]
            heading = HEADING.match(texts[0]) if len(elements) == 1 and texts else None
            if heading:
                item = heading[1]
                continue
            for text in texts:
                matches = list(CB_LINE.finditer(text))
                for i, match in enumerate(matches):
                    # The reminders show the form itself: "CB: # 1_Name".
                    if item is None or (match[2] or '').lower() == 'name' or match[1] in found:
                        continue
                    tail = text[match.start():matches[i + 1].start() if i + 1 < len(matches) else None]
                    moderator = MODERATOR.search(tail)
                    found[match[1]] = {'agenda_item': item, 'name': squash(text[match.start():].split('\n')[0]),
                                       'moderator': moderator[1] if moderator else None}
    return found


def identify(doc, name: str) -> str:
    """The meeting: the file name, else the title paragraph."""
    info = file_info(name)
    if info:
        return info[0]
    for paragraph in doc.paragraphs[:10]:
        match = TITLE.search(paragraph.text)
        if match:
            return meeting_id(match[1], match[2])
    raise LayoutError(f'Cannot tell the RAN3 meeting of {name}')


def extract_document(data: bytes, name: str) -> dict:
    doc = Document(io.BytesIO(data))
    meeting = identify(doc, name)
    table, head = schedule_table(doc)
    return {'meeting_id': meeting, 'days': read_days(table, head), 'legend': legend(doc, table),
            'cbs': cb_blocks(doc, table)}


def surroundings(data: bytes, name: str) -> dict:
    """What the rules still read when the time plan table is not in its form.

    The key under the table and the CB blocks in the agenda's notes do not
    depend on the table's shape, so the LLM fallback (llm.read_document) gets
    them from the rules as usual; a table that cannot be found has no key.
    """
    doc = Document(io.BytesIO(data))
    table, _ = weekday_table(doc)
    return {'meeting_id': identify(doc, name), 'legend': legend(doc, table), 'cbs': cb_blocks(doc, table)}


# ------------------------------------------------------------ offline room

def _filled(ws):
    """Cell values with merged ranges filled from their top-left cell."""
    values = {(c.row, c.column): c.value for row in ws.iter_rows() for c in row}
    for merged in ws.merged_cells.ranges:
        top = values.get((merged.min_row, merged.min_col))
        for r in range(merged.min_row, merged.max_row + 1):
            for c in range(merged.min_col, merged.max_col + 1):
                values[(r, c)] = top
    return values, ws.max_row, ws.max_column


def _text(value) -> str:
    return squash(str(value)) if value is not None else ''


def offline_sheets(data: bytes) -> tuple[list[dict], list[str]]:
    """Booked slots per sheet, and the sheets that could not be read.

    A slot is {'day', 'month', 'date', 'start', 'end', 'topic', 'moderator'}.
    Repeated or merged rows of one topic are joined by the pipeline.
    """
    from openpyxl import load_workbook
    workbook = load_workbook(io.BytesIO(data), data_only=True)
    slots, unread = [], []
    for ws in workbook.worksheets:
        name = SHEET.match(ws.title)
        day = next((d for d in DAYS if name and d[:3].lower() == name[3].lower()), None)
        if not name or not day or name[1].lower() not in MONTHS:
            unread.append(ws.title)
            continue
        values, rows, cols = _filled(ws)
        head = next(((r, {_text(values.get((r, c))).lower(): c for c in range(1, cols + 1)})
                     for r in range(1, min(rows, 10) + 1)
                     if {'time slot', 'topic', 'moderator'} <= {_text(values.get((r, c))).lower()
                                                               for c in range(1, cols + 1)}), None)
        if head is None:
            unread.append(ws.title)
            continue
        first, columns = head
        for r in range(first + 1, rows + 1):
            slot = SLOT.match(_text(values.get((r, columns['time slot']))))
            if not slot:
                continue
            topic = _text(values.get((r, columns['topic'])))
            if topic:
                slots.append({'day': day, 'month': MONTHS[name[1].lower()], 'date': int(name[2]),
                              'start': clock(slot[1], slot[2]), 'end': clock(slot[3], slot[4]),
                              'topic': topic, 'moderator': _text(values.get((r, columns['moderator'])))})
    return slots, unread
