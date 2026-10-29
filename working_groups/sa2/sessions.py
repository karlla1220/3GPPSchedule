"""Turn SA2 Session Plan cells into sessions with a few fixed rules.

The template splits each quarter into two cells per weekday, and those cells
are the only split the chair marks: different text in the two cells is two
pieces of the quarter (part 1/2 and 2/2, no times are written), the same text
is the whole quarter. Q0 (50 minutes) is too short for two 30-minute pieces,
so its cells stay one session. A paragraph is a topic,
"KI#18 (AI for Network) (20.6.18) [5+15]"; several topics in a cell are
not split.

The cell title is the cell's own text with every bracket dropped, as RAN2
does: "KI#18". Topics are joined with " / ", three at most. Every original
line stays in the notes. Agenda items are read from the original line.
"""
from __future__ import annotations

from datetime import date, timedelta
import re

from shared.schedule import (MIN_PART_MINUTES, DaySchedule, RoomInfo, Schedule, Session, Timeline,
                             minutes_to_time, time_to_minutes)
from shared.agenda_groups import group_label
from .document import clock

AI = r'\d{1,2}(?:\.\d{1,2})*'
# A line-leading clock starts a timed part: "14:00 List of agreed tdocs", "13:00 (Main room) Drafting".
MARK = re.compile(r'^(\d{1,2}):(\d{2})\s*')
# The block's own times inside its first line: "Plenary session (1330 - 1630)".
WRITTEN_SPAN = re.compile(r'\((\d{2})(\d{2})\s*-\s*(\d{2})(\d{2})\)')
# "Drafting: (1300) FS_ARCH_enIMS (main room)" (#172's lunch row).
DRAFTING_TIME = re.compile(r'^Drafting:\s*\((\d{2})(\d{2})\)', re.I)
ROOM_WORD = re.compile(r'\((main\s*room|break[- ]?out\s*(\d))\)', re.I)
ANNOUNCEMENT = re.compile(r'(?:Opening|Close) of (?:the )?meeting\b.*', re.I)
PAREN = re.compile(r'\(([^()]*)(?:\)|$)')
BRACKET_AI = re.compile(r'\[\s*(\d{1,2}(?:\.\d{1,2})+)\s*\]')
BRACKETS = re.compile(r'\[[^\[\]]*\]?')
# Times written in a form the rules do not read (anything but the forms above).
OTHER_TIME = re.compile(r'(?<![\d.:])\d{1,2}:\d{2}(?!\d)|\b\d{1,2}\s*(?:am|pm)\b|\b\d+\s*min(?:ute)?s?\b', re.I)


# ------------------------------------------------------------------- lines

def clean(text: str) -> str:
    """Display label: drop parentheses as RAN2's clean does, keep "(e)RedCap".

    Removing "(1)" leaves "Opening , Agenda"; the space before the comma goes too.
    """
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r'\s*\([^()]*\)(?![A-Za-z])', ' ', text)
    text = re.sub(r'\s*\([^()]*$', ' ', text)
    text = re.sub(r'\[\s*\]', ' ', text)
    text = re.sub(r'\s+([,;])', r'\1', ' '.join(text.split()))
    return text.strip(' ,;:/-–—&.')


def topic(raw: str) -> str:
    """A line's label: no leading clock, no brackets ("[5+15]", "[19.2.2]"), no parentheses."""
    return clean(BRACKETS.sub(' ', MARK.sub('', raw)))


def _expand(token: str, previous: str | None) -> list[str] | None:
    """Agenda numbers one comma-separated token names, or None if it names none.

    "20.6.12 - 16" is a range, "19.48/49/50" siblings, "5.x" all of 5. A bare
    "12" after "20.6.14" is another sibling ("20.6.14, 12, 13"); alone, "(1)"
    is item 1.
    """
    token = token.strip()
    if re.fullmatch(AI + r'(?:\.x)?', token, re.I):
        if '.' not in token and previous and '.' in previous:
            return [previous.rsplit('.', 1)[0] + '.' + token]
        return [token]
    match = re.fullmatch(rf'({AI})\.(\d{{1,2}})\s*[-–]\s*(\d{{1,2}})', token)
    if match and int(match[2]) <= int(match[3]):
        return [f'{match[1]}.{n}' for n in range(int(match[2]), int(match[3]) + 1)]
    parts = [p.strip() for p in token.split('/')]
    if len(parts) > 1 and re.fullmatch(AI, parts[0]) and '.' in parts[0] and all(p.isdigit() for p in parts[1:]):
        parent = parts[0].rsplit('.', 1)[0]
        return [parts[0]] + [f'{parent}.{p}' for p in parts[1:]]
    return None


def known(item: str, agenda: dict) -> bool:
    """In agenda.csv, or the parent of something there ("1" for 1.1, "5.x")."""
    if not agenda:
        return True
    stem = item[:-2] if item.lower().endswith('.x') else item
    return stem in agenda or any(key.startswith(stem + '.') for key in agenda)


def line_items(raw: str, agenda: dict) -> tuple[list[str], list[str]]:
    """(agenda items, numbers that look like items but were not read as known ones) of one line.

    Items are numbers in parentheses, "(20.6.18)", "(5.x, 6.x, 7.x )", an
    unclosed "(19.8.2 [4]", "(including 30.1)"; dotted numbers in brackets,
    "[19.2.2]" (a plain "[15]" is a count); and a line of nothing but numbers,
    "20.48, 20.49, 20.50 [15]". "(see 30.1)" names 30.1 too. "(Option 1, 2, 3)" and
    "(1330 - 1630)" are not.
    """
    found = list(BRACKET_AI.findall(raw))
    text = BRACKETS.sub(' ', raw)   # "(19.8.2 [4]" is "(19.8.2" left open
    for content in PAREN.findall(text):
        content = re.sub(r'^(?:incl?(?:uding|\.)?|see)\s+', '', content.strip(), flags=re.I)
        items, previous = [], None
        for token in filter(None, (t.strip() for t in content.split(','))):
            expanded = _expand(token, previous)
            if expanded is None:
                items = None
                break
            items.extend(expanded)
            previous = expanded[-1]
        if items:
            found.extend(items)
    rest = PAREN.sub(' ', text)
    loose = re.findall(r'(?<![\w.#-])\d{1,2}\.(?:\d{1,2}|x)(?:\.\d{1,2})*(?![\w.])', rest)
    if re.search(r'\d', rest) and re.fullmatch(rf'(?:[\s,;/]|{AI}(?:\.x)?)*', rest):
        loose = []
        previous = None
        for token in filter(None, (t.strip() for t in rest.replace(';', ',').split(','))):
            expanded = _expand(token, previous) or []
            found.extend(expanded)
            previous = expanded[-1] if expanded else previous
    found = list(dict.fromkeys(found))
    # Numbers among words outside parentheses ("5.x [0], 6.7 [3], eNA_Ph2 (8.1)") are not read; say so.
    return [i for i in found if known(i, agenda)], [i for i in found if not known(i, agenda)] + loose


def uncertainty(lines: list[str], unknown: list[str]) -> list[str]:
    """Why the rules may have misread a cell; empty when they are on firm ground."""
    reasons = []
    if unknown:
        reasons.append(f'agenda numbers not in agenda.csv: {unknown}')
    for raw in lines:
        if re.search(r'\][^\s\[\],;/)]', raw):
            reasons.append(f'two topics run together: {raw!r}')   # "TEI20_DAMP [1]TEI20_UDCE-ARC [2]"
        rest = WRITTEN_SPAN.sub(' ', DRAFTING_TIME.sub(' ', MARK.sub(' ', raw)))
        if OTHER_TIME.search(rest):
            reasons.append(f'time written in a form the rules do not read: {raw!r}')
    return list(dict.fromkeys(reasons))


def reading(lines: list[str], agenda: dict) -> dict:
    """Title, agenda items and doubts of a cell part.

    Topic lines are those naming an agenda item, and the first line. A line
    that is only numbers ("(5.x, 6.x, 7.x ) [9]") adds its items to the topic
    above; other lines ("Architecture", "Then Option 1, 2, 3 drafting") are
    notes on it.
    """
    heads, items, unknown, group_item = [], [], [], None
    for i, raw in enumerate(lines):
        found, missing = line_items(raw, agenda)
        items.extend(found)
        unknown.extend(missing)
        label = topic(raw)
        if label and (found or i == 0) and label not in heads:
            heads.append(label)
            group_item = group_item or (found[0] if found else None)
    items = list(dict.fromkeys(items))
    title = ' / '.join(heads[:3]) + (' …' if len(heads) > 3 else '')
    return {'name': title or lines[0], 'agenda_items': items,
            'group_item': group_item or (items[0] if items else None),
            'reasons': uncertainty(lines, unknown)}


def same_text(a: list[str], b: list[str]) -> bool:
    """Equal but for spaces and paragraph breaks ("(5.x, 6.x, 7.x )" / "(5.x, 6.x, 7.x)")."""
    return re.sub(r'\s+', '', ''.join(a)) == re.sub(r'\s+', '', ''.join(b))


def timed_parts(lines: list[str], start: str, end: str) -> list[tuple[str, str, list[str]]]:
    """Split at line-leading clocks inside the block: the plenary's "14:00 ...", "15:00 ..."."""
    parts = [(start, [])]
    for raw in lines:
        mark = MARK.match(raw)
        value = clock(mark[1], mark[2]) if mark else None
        if value and start < value < end and value > parts[-1][0]:
            parts.append((value, [raw]))
        else:
            parts[-1][1].append(raw)
    parts = [(a, lines) for a, lines in parts if lines]
    return [(a, parts[i + 1][0] if i + 1 < len(parts) else end, lines) for i, (a, lines) in enumerate(parts)]


# ------------------------------------------------------------------ cells

def _session(cell_id, day, lines, start, end, agenda, *, stream=None, rooms=(), scope='assigned', part=None):
    return {'cell': cell_id, 'day': day, 'start': start, 'end': end, 'stream': stream, 'rooms': list(rooms),
            'scope': scope, 'part': part, 'lines': list(lines), **reading(lines, agenda)}


def _spanning(cell, document, agenda) -> list[dict]:
    """A cell merged down several rows: the opening across three streams, the Friday plenary."""
    quarters, breaks = document['quarters'], document['breaks']
    spans = ([(quarters[q]['start'], quarters[q]['end']) for q in cell['quarters']]
             + [(breaks[b]['start'], breaks[b]['end']) for b in cell['breaks']])
    start, end = min(a for a, _ in spans), max(b for _, b in spans)
    written = next((WRITTEN_SPAN.search(raw) for raw in cell['lines'] if WRITTEN_SPAN.search(raw)), None)
    if written:
        start, end = clock(written[1], written[2]), clock(written[3], written[4])
    streams = cell['streams']
    shared = streams == document['streams']
    rooms = [] if shared else [f'stream{s}' for s in streams]
    stream = streams[0] if len(streams) == 1 else None
    return [_session(cell['id'], cell['day'], lines, a, b, agenda, stream=stream, rooms=rooms,
                     scope='shared' if shared else 'assigned')
            for a, b, lines in timed_parts(cell['lines'], start, end)]


def _quarter(day, q, stream, halves, document, agenda) -> list[dict]:
    """One stream's quarter from its two cells (or one cell over both)."""
    quarter = document['quarters'][q]
    start, end = quarter['start'], quarter['end']
    room = [f'stream{stream}']
    whole = halves.get((1, 2))
    if whole:
        return [_session(whole['id'], day, whole['lines'], start, end, agenda, stream=stream, rooms=room)]
    left, right = halves.get((1,)), halves.get((2,))
    filled = [(k, c) for k, c in ((1, left), (2, right)) if c]
    if not filled:
        return []
    if len(filled) == 2 and same_text(left['lines'], right['lines']):
        # The same text twice is one topic for the whole quarter.
        return [_session(left['id'], day, left['lines'], start, end, agenda, stream=stream, rooms=room)]
    minutes = time_to_minutes(end) - time_to_minutes(start)
    if minutes < 2 * MIN_PART_MINUTES:
        # Q0: pieces would be shorter than a cell can show; one session, left cell first.
        lines = list(dict.fromkeys(line for _, c in filled for line in c['lines']))
        return [_session(filled[0][1]['id'], day, lines, start, end, agenda, stream=stream, rooms=room)]
    return [_session(c['id'], day, c['lines'], start, end, agenda, stream=stream, rooms=room, part=[k, 2])
            for k, c in filled]


def _break_row(day, b, cells, document, agenda) -> list[dict]:
    """Text in a Break/Coffee/Lunch row: a session of its own in the break's time.

    "13:00 (Main room) Drafting for ..." starts at 13:00 in the main room and
    runs to the end of the break (no end is written). Without a room it is
    shown across all rooms as unassigned.
    """
    item = document['breaks'][b]
    rooms_by_word = {r['name'].lower(): r['id'] for r in document['rooms']}
    lines = list(dict.fromkeys(line for c in cells for line in c['lines']))
    parts = []
    for raw in lines:
        mark = MARK.match(raw) or DRAFTING_TIME.match(raw)
        word = ROOM_WORD.search(raw)
        room = None
        if word:
            room = rooms_by_word.get('main') if word[1].lower().startswith('main') else \
                rooms_by_word.get(f'breakout {word[2]}')
        if parts and not mark and not word:
            parts[-1]['lines'].append(raw)
            continue
        begin = clock(mark[1], mark[2]) if mark else item['start']
        if not item['start'] <= begin < item['end']:
            begin = item['start']
        parts.append({'start': begin, 'room': room, 'lines': [raw]})
    return [_session(cells[0]['id'], day, p['lines'], p['start'], item['end'], agenda,
                     stream=int(p['room'][6:]) if p['room'] else None,
                     rooms=[p['room']] if p['room'] else [], scope='assigned' if p['room'] else 'unassigned')
            for p in parts]


def interpret(document: dict, agenda: dict | None = None, refine=None) -> tuple[list[dict], list[dict]]:
    """(sessions, notes): session dicts, and cell text that is no session.

    ``refine`` is the LLM fallback. It receives the cells the rules are unsure
    about, with the reasons and the rule-based reading, and returns validated
    readings (title, agenda items, times within the cell) by session id; every
    other session keeps the rules.
    """
    agenda = agenda or {}
    quarters, notes, found = {}, [], []
    break_cells = {}
    for cell in document['cells']:
        lines = cell['lines']
        if all(line == '-' for line in lines):
            continue   # the stream rests (6G single stream)
        if len(lines) == 1 and ANNOUNCEMENT.fullmatch(lines[0]):
            notes.append({'day': cell['day'], 'text': lines[0]})   # "Opening of meeting at 0900"
            continue
        if not cell['streams']:
            break_cells.setdefault((cell['day'], cell['breaks'][0]), []).append(cell)
        elif len(cell['streams']) == 1 and len(cell['quarters']) == 1 and not cell['breaks']:
            key = (cell['day'], cell['quarters'][0], cell['streams'][0])
            quarters.setdefault(key, {})[tuple(cell['halves'])] = cell
        else:
            found.extend(_spanning(cell, document, agenda))
    for (day, q, stream), halves in quarters.items():
        found.extend(_quarter(day, q, stream, halves, document, agenda))
    for (day, b), cells in break_cells.items():
        found.extend(_break_row(day, b, cells, document, agenda))
    for n, session in enumerate(found):
        session['id'] = f"{session['cell']}.{n}"
        session['block'] = (session['start'], session['end'])
    doubtful = [s for s in found if s['reasons']]
    replaced = refine(doubtful) if refine and doubtful else {}
    sessions = [{**s, **replaced[s['id']]} if s['id'] in replaced else s for s in found]
    order = {day: i for i, day in enumerate(document['days'])}
    sessions.sort(key=lambda s: (order[s['day']], s['start'], s['part'] or [0], s['rooms']))
    return sessions, notes


# --------------------------------------------------------------- schedule

def describe(item: str, agenda: dict) -> dict | None:
    match = item[:-2] if item.lower().endswith('.x') else item
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


def make_schedule(document: dict, agenda: dict | None, metadata: dict, source_files: list[str],
                  source_labels: dict[str, str], generated_at: str, *, chairs: dict[str, str] | None = None,
                  refine=None, sessions: list[dict] | None = None) -> Schedule:
    """``chairs`` maps a convener as written ("Mike") to the name shown ("Michael STARSINIC").

    ``sessions`` replaces the cell reading when the LLM read the whole document.
    """
    agenda = agenda or {}
    chairs = chairs or {}
    first, last = date.fromisoformat(metadata['starts_on']), date.fromisoformat(metadata['ends_on'])
    dates = {(first + timedelta(days=i)).strftime('%A'): (first + timedelta(days=i)).isoformat()
             for i in range(min((last - first).days, 6) + 1)}
    sessions, notes = interpret(document, agenda, refine) if sessions is None else (sessions, [])
    # The convener of a stream is linked by colour: the name's highlight in the
    # "Convener" line is the highlight on that stream's row labels.
    convener = {stream: document['conveners'].get(colour) for stream, colour in document['stream_colours'].items()}
    rooms = [RoomInfo(id=r['id'], name=r['name']) for r in document['rooms']]
    days = []
    for day in document['days']:
        raw = [s for s in sessions if s['day'] == day]
        if not raw:
            continue
        if day not in dates:
            print(f'[sa2] {day} is outside the meeting dates {first}..{last}; shown without a date')
        start = min(time_to_minutes(s['start']) for s in raw)
        start -= start % 30
        end = max(time_to_minutes(s['end']) for s in raw)
        end += -end % 5
        breaks = []
        for item in document['breaks']:
            a, b = time_to_minutes(item['start']), time_to_minutes(item['end'])
            if not (start <= a and b <= end):
                continue
            entry = {'name': item['name'], 'start': item['start'], 'end': item['end']}
            if any(time_to_minutes(s['start']) < b and a < time_to_minutes(s['end']) for s in raw):
                entry['label_position'] = 'time-axis'   # a session runs in the break
            breaks.append(entry)
        built = []
        for s in raw:
            tops = [ai for ai in s['agenda_items'] if not any(is_descendant(ai, a) for a in s['agenda_items'])]
            descriptions = [d for d in (describe(ai, agenda) for ai in tops[:6]) if d]
            label = convener.get(s['stream']) if s['scope'] == 'assigned' and s['stream'] else None
            built.append(Session(
                name=s['name'], start_time=s['start'], end_time=s['end'], day=day,
                duration_minutes=time_to_minutes(s['end']) - time_to_minutes(s['start']),
                room_ids=s['rooms'], room_scope=s['scope'], part=s['part'],
                chair=chairs.get(label, label) if label else None,
                agenda_item=', '.join(s['agenda_items']) or None,
                group_header=group_label(s['group_item'], agenda),
                description='\n'.join(d['description'] for d in descriptions) or None,
                agenda_descriptions=descriptions, notes=s['lines']))
        days.append(DaySchedule(day_name=day, date=dates.get(day), rooms=rooms, sessions=built,
                                timeline=Timeline(start=minutes_to_time(start), end=minutes_to_time(end),
                                                  breaks=breaks)))
    if not days:
        raise ValueError('The SA2 Session Plan produced no sessions')
    supplements = list(document.get('supplements', []))
    notes = document.get('notes', []) + notes
    if notes:
        supplements.append({'type': 'heading', 'text': 'Notes in the schedule table'})
        supplements.append({'type': 'table', 'header': True,
                            'rows': [['Day', 'Note']] + [[n['day'], n['text']] for n in notes]})
    number = document['meeting_id'].split('#', 1)[1]
    return Schedule(
        wg_id='sa2', meeting_id=document['meeting_id'], meeting_name=f'SA2#{number}', days=days,
        source_file=source_files[0], source_files=source_files, source_labels=source_labels,
        generated_at=generated_at, timezone=metadata['timezone'], starts_on=metadata['starts_on'],
        ends_on=metadata['ends_on'], starts_at=metadata.get('starts_at'), ends_at=metadata.get('ends_at'),
        supplements=supplements)
