"""SA1 sessions from the schedule cells, by rules (research/sa1/DECISIONS.md).

A cell is one weekday's slot. Its rooms are written inside it: a head line
("Plenary", "Breakout 1:", "Drafting 2:") starts a room, "=====" ends one,
and lines before any head have no room (shown across all rooms). The cell
title copies the cell's shape: a Plenary cell is titled "Plenary" with its
items as notes; any other room's lines are its title ("7.3.3 AI", several
joined with " / "). Nothing is split without a written time; times written in
a cell ("13:00- 14:00h ...", "(18:30-20:00)", "Meeting ends 16:00",
"Last 30 min ...") are used as written.

The breakout chairs come from the NOTE below the table: a cell takes the
chair of the one NOTE line whose topic words name it ("AI, Computing –
chaired by Vasil Aleksiev" for "7.3.9 Computing"). A Plenary cell and a cell
without a room name nobody.
"""
from __future__ import annotations

from datetime import date, timedelta
import re

from shared.agenda import describe, group_label, is_descendant
from shared.schedule import (DaySchedule, RoomInfo, Schedule, Session, Timeline,
                             minutes_to_time, time_to_minutes)
from shared.titles import clean
from .document import clock

HEAD = re.compile(r'^(Plenary|(?:Breakout|Drafting)\s*\d)\b\s*:?\s*(.*)$', re.I)
SEPARATOR = re.compile(r'\s*={3,}\s*$')
START_AT = re.compile(r'^\(start at (\d{1,2})[:.](\d{2})\)$', re.I)
ENDS = re.compile(r'^Meeting ends (\d{1,2})[:.](\d{2})\s*h?$', re.I)
SLASH = re.compile(r'^/$')
LAST = re.compile(r'^Last (\d+)\s*min(?:utes?|s)?\s+(.+)$', re.I)
RANGE_LEAD = re.compile(r'^(\d{1,2}):(\d{2})\s*h?\s*[-–—]\s*(\d{1,2}):(\d{2})\s*h?\s+(.+)$')
RANGE_PAREN = re.compile(r'^\((\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})\)$')
START_PAREN = re.compile(r'^\((\d{1,2}):(\d{2})\)$')
KNOWN = (START_AT, ENDS, SLASH, LAST, RANGE_LEAD, RANGE_PAREN, START_PAREN)
# Times and durations elsewhere in a line are something the rules do not read.
CLOCK_TEXT = re.compile(r'(?<![\d.:])(\d{1,2}):(\d{2})(?!\d)')
DURATION = re.compile(r'\b\d+\s*(?:min(?:ute)?s?|hrs?|hours?)\b', re.I)
# "7.3.3 AI", "2. Reports", "7.3.6 Immersive+7.3.10 IMS", "7.2 FS_AddCapEm_REQ 7.3.1 General"
NUMBERED = re.compile(r'(?:^|(?<=[\s+]))(\d{1,2}(?:\.\d{1,2})*)\.?(?=\s+\S)')
LEADING_NUMBER = re.compile(r'^\d{1,2}(?:\.\d{1,2})*\.?\s+')


def words(text: str) -> list[str]:
    return re.findall(r'[a-z0-9]+', text.casefold())


def contains(outer: str, inner: str) -> bool:
    """inner's words appear in outer as a run: "System and Operation" in "6G System and Operation Aspects"."""
    a, b = words(outer), words(inner)
    return bool(b) and any(a[i:i + len(b)] == b for i in range(len(a) - len(b) + 1))


def topics(line: str) -> list[str]:
    """The named topics of a line: "7.3.6 Immersive+7.3.10 IMS" -> ["Immersive", "IMS"]."""
    return [name for name in (LEADING_NUMBER.sub('', piece.strip()).strip() for piece in line.split('+')) if name]


def is_agenda(value: str) -> bool:
    return int(value.split('.')[0]) <= 30


# ------------------------------------------------------------------- cells

def segments(cell: dict, rooms: list[dict]) -> list[dict]:
    """The cell's rooms in order: {'room': id or None, 'plenary', 'lines', 'indexes', 'head'}."""
    alias = {a: room['id'] for room in rooms for a in room['aliases']}
    result, current = [], None
    for index, raw in enumerate(cell['lines']):
        ends = bool(SEPARATOR.search(raw))
        line = SEPARATOR.sub('', raw).strip()
        if not line:
            current = None
            continue
        match = HEAD.match(line)
        key = ' '.join(match[1].lower().split()) if match else None
        if key in alias:
            current = {'room': alias[key], 'plenary': key == 'plenary', 'head': key, 'lines': [], 'indexes': []}
            result.append(current)
            if match[2]:
                current['lines'].append(match[2])
                current['indexes'].append(index)
        else:
            if current is None:
                current = {'room': None, 'plenary': False, 'head': None, 'lines': [], 'indexes': []}
                result.append(current)
            current['lines'].append(line)
            current['indexes'].append(index)
        if ends:
            current = None
    return result


def parts(segment: dict, cell: dict) -> list[dict]:
    """Timed parts of one room's lines; written times are used, nothing else is split."""
    start, end = cell['start'], cell['end']
    found = [{'start': start, 'end': end, 'lines': [], 'start_only': None}]
    for line in segment['lines']:
        if START_AT.match(line) or SLASH.match(line):
            continue
        match = ENDS.match(line)
        if match:
            new_end = clock(match[1], match[2])
            for part in found:
                if part['end'] == end:
                    part['end'] = new_end
            end = new_end
            continue
        match = LAST.match(line)
        if match and int(match[1]) < time_to_minutes(end) - time_to_minutes(found[-1]['start']):
            cut = minutes_to_time(time_to_minutes(end) - int(match[1]))
            found[-1]['end'] = cut
            found.append({'start': cut, 'end': end, 'lines': [match[2]], 'start_only': None})
            continue
        match = RANGE_LEAD.match(line)
        if match:
            found.append({'start': clock(match[1], match[2]), 'end': clock(match[3], match[4]),
                          'lines': [match[5]], 'start_only': None})
            continue
        match = RANGE_PAREN.match(line)
        if match:
            found[-1]['start'], found[-1]['end'] = clock(match[1], match[2]), clock(match[3], match[4])
            continue
        match = START_PAREN.match(line)
        if match:
            found[-1]['start_only'] = clock(match[1], match[2])
            continue
        found[-1]['lines'].append(line)
    return [p for p in found if p['lines'] or (segment['plenary'] and p is found[0])]


def uncertainty(cell: dict, rooms: list[dict]) -> list[str]:
    """Why the rules may have misread this cell (U1-U4 in DECISIONS.md); empty when on firm ground."""
    reasons = []
    alias = {a for room in rooms for a in room['aliases']}
    for raw in cell['lines']:
        line = SEPARATOR.sub('', raw).strip()
        if any(p.match(line) for p in KNOWN):
            continue
        if CLOCK_TEXT.search(line) or DURATION.search(line):
            reasons.append(f'time written inside a line: {raw!r}')
        match = HEAD.match(line)
        if match and ' '.join(match[1].lower().split()) not in alias:
            reasons.append(f'room head not in MEETING ROOMS: {raw!r}')
    found = segments(cell, rooms)
    if any(s['room'] for s in found):
        stray = [line for s in found if s['room'] is None for line in s['lines']
                 if not any(p.match(line) for p in KNOWN)]
        if stray:
            reasons.append(f'lines before the first room head: {stray!r}')
    if cell['kind'] == 'break' and any((p['start'], p['end']) == (cell['start'], cell['end'])
                                       for s in found for p in parts(s, cell) if p['lines']):
        reasons.append(f'a line without a time in the {cell["row"] or "break"} row')
    return list(dict.fromkeys(reasons))


# ---------------------------------------------------------------- reading

def known_names(document: dict) -> dict[str, set[str]]:
    """Topic name -> numbers written with it anywhere in the table ("immersive" -> {"8.1.6"})."""
    names = {}
    for day in document['days']:
        for cell in day['cells']:
            for raw in cell['lines']:
                line = SEPARATOR.sub('', raw).strip()
                match = HEAD.match(line)
                line = match[2] if match else line
                for piece in line.split('+'):
                    number = NUMBERED.match(piece.strip())
                    if number:
                        name = LEADING_NUMBER.sub('', piece.strip()).strip().casefold()
                        names.setdefault(name, set()).add(number[1])
    return names


def checked_item(number: str, name: str, agenda: dict) -> str | None:
    """A number written in a cell, checked against the meeting's agenda (DECISIONS.md #10).

    A number on the agenda stays even if its name differs (which one is wrong
    is unknown). A number off the agenda is replaced by the one item whose
    title names it ("8.8 Verticals" -> 8.1.8), else dropped.
    """
    if not agenda or number in agenda:
        return number
    matches = [item for item, title in agenda.items() if name and contains(title, name)]
    matches = [m for m in matches if not any(is_descendant(m, other) for other in matches)]
    return matches[0] if len(matches) == 1 else None


def agenda_of(lines: list[str], agenda: dict, names: dict) -> tuple[list[str], list[str]]:
    """(numbers the cell writes, numbers found for topics it names without one).

    The first are the cell's agenda items. The second only match the cell in
    the filter (``Session.inferred_agenda_items``): the cell does not write
    them, so they are not shown as its AI (CLAUDE.md "AI 없는 셀의 AI 추정").
    """
    written, inferred = [], []
    for line in lines:
        pieces = line.split('+')
        for piece in pieces:
            piece = piece.strip()
            numbers = [m for m in NUMBERED.finditer(piece) if is_agenda(m[1])]
            if not numbers:
                # A Plenary item named without its number ("Immersive", #112): the
                # number the table writes with that name elsewhere, if only one.
                known = names.get(piece.casefold(), set())
                numbers_known = [checked_item(n, piece, agenda) for n in known]
                if len(known) == 1 and numbers_known[0]:
                    inferred.append(numbers_known[0])
                continue
            for i, match in enumerate(numbers):
                tail = piece[match.end():numbers[i + 1].start() if i + 1 < len(numbers) else None]
                item = checked_item(match[1], tail.strip(), agenda)
                if item:
                    written.append(item)
    written = list(dict.fromkeys(written))
    return written, [i for i in dict.fromkeys(inferred) if i not in written]


def chair_of(lines: list[str], chairs: list[dict]) -> str | None:
    """The one NOTE line whose topics name this cell's topics."""
    named = [t for line in lines for t in topics(line)]
    matched = {i for i, entry in enumerate(chairs)
               for piece in re.split(r'[,+]', entry['topic']) if piece.strip()
               for topic in named if contains(piece, topic) or contains(topic, piece)}
    return chairs[matched.pop()]['chair'] if len(matched) == 1 else None


def title_of(segment: dict, lines: list[str]) -> str:
    if segment['plenary']:
        return 'Plenary'
    names = [clean(line) or line for line in lines]
    return ' / '.join(names[:3]) + (' …' if len(names) > 3 else '')


def rule_based(cell: dict, document: dict, names: dict) -> tuple[list[dict], list[dict]]:
    """(sessions, lines not placed on the grid) of one cell by the rules."""
    agenda, found, unplaced = document['agenda'], [], []
    for segment in segments(cell, document['rooms']):
        for part in parts(segment, cell):
            if part['start_only']:
                # A start with no end ("MMS (18:30)"): no time is made up (DECISIONS.md #8).
                unplaced.append(' '.join(part['lines']) + f' ({part["start_only"]})')
                continue
            items, inferred = agenda_of(part['lines'], agenda, names)
            found.append({'room': segment['room'], 'start': part['start'], 'end': part['end'],
                          'name': title_of(segment, part['lines']), 'agenda_items': items,
                          'inferred_items': inferred,
                          'chair': None if segment['plenary'] or segment['room'] is None
                          else chair_of(part['lines'], document['chairs']),
                          'lines': list(part['lines'])})
    return found, unplaced


def place(sessions: list[dict]) -> tuple[list[dict], list[dict]]:
    """Sessions that fit the grid, and those without a room that would cover another."""
    placed = [s for s in sessions if s['room']]
    unplaced = []
    for session in sorted((s for s in sessions if not s['room']), key=lambda s: (s['start'], s['end'])):
        if any(o['start'] < session['end'] and session['start'] < o['end'] for o in placed):
            unplaced.append(session)
        else:
            placed.append(session)
    return placed, unplaced


def interpret(document: dict, refine=None) -> tuple[list[dict], list[dict]]:
    """(sessions with 'day', notes for below the grid).

    ``refine`` is the LLM fallback: it receives the cells the rules are unsure
    about and returns checked sessions for the cells it could read.
    """
    names = known_names(document)
    readings, doubtful = [], []
    for day in document['days']:
        for cell in day['cells']:
            found, unplaced = rule_based(cell, document, names)
            readings.append((day, cell, found, unplaced))
            reasons = uncertainty(cell, document['rooms'])
            if reasons:
                doubtful.append({'day': day, 'cell': cell, 'reasons': reasons, 'rules': found})
    replaced = refine(doubtful) if refine and doubtful else {}
    sessions, notes = [], []
    for day in document['days']:
        found = []
        for owner, cell, rules, unplaced in readings:
            if owner is day:
                found.extend(replaced.get(cell['id'], rules))
                notes.extend({'day': day['day'], 'text': text} for text in unplaced)
        placed, left = place(found)
        sessions.extend({**s, 'day': day['day']} for s in placed)
        notes.extend({'day': day['day'], 'text': ' '.join(s['lines']) + f' ({s["start"]}–{s["end"]})'}
                     for s in left)
    return sessions, notes


# --------------------------------------------------------------- schedule

def make_schedule(document: dict, metadata: dict, source_files: list[str], source_labels: dict,
                  generated_at: str, *, refine=None, resolve_chair=None) -> Schedule:
    """``resolve_chair`` turns a NOTE name into the officials' spelling (shared/officials.py)."""
    agenda = document['agenda']
    first, last = date.fromisoformat(metadata['starts_on']), date.fromisoformat(metadata['ends_on'])
    dates = {(first + timedelta(days=i)).strftime('%A'): (first + timedelta(days=i)).isoformat()
             for i in range(min((last - first).days, 6) + 1)}
    sessions, notes = interpret(document, refine)
    rooms = [RoomInfo(id=r['id'], name=r['name'], detail=r.get('place', '')) for r in document['rooms']]
    days = []
    for day in document['days']:
        raw = [s for s in sessions if s['day'] == day['day']]
        if not raw:
            continue
        if day['day'] not in dates:
            print(f'[sa1] {day["day"]} is outside the meeting dates {first}..{last}; shown without a date')
        start = min(time_to_minutes(s['start']) for s in raw)
        start -= start % 30
        end = max(time_to_minutes(s['end']) for s in raw)
        end += -end % 5
        breaks = []
        for item in day['breaks']:
            a, b = time_to_minutes(item['start']), time_to_minutes(item['end'])
            if start <= a and b <= end:
                busy = any(time_to_minutes(s['start']) < b and a < time_to_minutes(s['end']) for s in raw)
                breaks.append({**item, **({'label_position': 'time-axis'} if busy else {})})
        built = []
        for s in raw:
            begin, finish = s['start'], s['end']
            tops = [ai for ai in s['agenda_items'] if not any(is_descendant(ai, a) for a in s['agenda_items'])]
            descriptions = [d for d in (describe(ai, agenda) for ai in tops[:6]) if d]
            chair = s['chair'] and ((resolve_chair or (lambda name: name))(s['chair']) or s['chair'])
            built.append(Session(
                name=s['name'], start_time=begin, end_time=finish, day=day['day'],
                duration_minutes=time_to_minutes(finish) - time_to_minutes(begin),
                room_ids=[s['room']] if s['room'] else [], room_scope='assigned' if s['room'] else 'unassigned',
                chair=chair, agenda_item=', '.join(s['agenda_items']) or None,
                group_header=group_label(s['agenda_items'][0] if s['agenda_items'] else None, agenda),
                description='\n'.join(d['description'] for d in descriptions) or None,
                agenda_descriptions=descriptions, notes=list(s['lines']),
                inferred_agenda_items=list(s.get('inferred_items', []))))
        days.append(DaySchedule(day_name=day['day'], date=dates.get(day['day']), rooms=list(rooms),
                                sessions=sorted(built, key=lambda s: (s.start_time, s.room_ids)),
                                timeline=Timeline(start=minutes_to_time(start), end=minutes_to_time(end),
                                                  breaks=breaks)))
    if not days:
        raise ValueError('The SA1 schedule produced no sessions')
    supplements = list(document.get('supplements', []))
    if notes:
        supplements.append({'type': 'heading', 'text': 'Not placed on the grid'})
        supplements.append({'type': 'table', 'header': True,
                            'rows': [['Day', 'Schedule text']] + [[n['day'], n['text']] for n in notes]})
    number = document['meeting_id'].split('#', 1)[1]
    return Schedule(
        wg_id='sa1', meeting_id=document['meeting_id'], meeting_name=f'SA1#{number}', days=days,
        source_file=source_files[0], source_files=source_files, source_labels=source_labels,
        generated_at=generated_at, timezone=metadata['timezone'], starts_on=metadata['starts_on'],
        ends_on=metadata['ends_on'], starts_at=metadata.get('starts_at'), ends_at=metadata.get('ends_at'),
        supplements=supplements)
