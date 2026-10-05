"""Estimate timed sessions from RAN2 schedule cells with a few general rules.

Cells are free text, and their conventions change with the author, so only
broad conventions are relied on:

- a line starting with a clock ("@12:30", "@8:30-9:30", "From 15:30:",
  "11:00-12:00 ...") starts a new part of the cell;
- bracketed numbers are agenda items, except small budgets after a title
  ("NR20 AIoT [2]") and offline numbers ("[004]", "[xxx]");
- a capitalised name in parentheses is a chair unless it is a company.

The block title is an estimate from the first lines. Every original line stays
in the popup, so a wrong guess hides nothing. Text that is not a session, such
as the offline discussion list, is shown below the grid instead of being
merged into it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
import re

from shared.schedule import (DaySchedule, RoomInfo, Schedule, Session, Timeline,
                             minutes_to_time, time_to_minutes)
from .document import clock

# A bare clock needs a colon, so "8.10 NR20 MIMO" is never read as 08:10.
AT_MARK = re.compile(r'^(?:@|from\b)\s*(\d{1,2})(?:[:.](\d{2}))?'
                     r'(?:\s*(?:-|–|—|~|to)\s*(\d{1,2})(?:[:.]?(\d{2}))?)?(?!\d|[.:]\d)[\s:,–—-]*', re.I)
BARE_MARK = re.compile(r'^(\d{1,2}):(\d{2})(?:\s*(?:-|–|—|~|to)\s*(\d{1,2}):?(\d{2}))?(?!\d|[.:]\d)[\s:,–—-]*')
# "[8.3]", "[6G CP]", and an unclosed "[8.2.1 Organizational".
BRACKET = re.compile(r'\[\s*([^\[\]]*?)\s*\]|\[\s*(\d+(?:\.\d+)*)(?![\d.\]])')
SEPARATORS = re.compile(r'(?:[\s,;/&–-]|\band\b|\([^()]*\))*')
OFFLINE_ID = re.compile(r'(?:\d{3}|x{3}|POST\s*\d+\w*|AT\s*\d+\w*)', re.I)
BARE_AGENDA = re.compile(r'(?<![\w.-])\d{1,2}(?:\.\d{1,2})+(?![\w.])')
# A line that is only agenda numbers: "6.0.2.4, 5.1.3.2" or "[7.8.1], [7.8.2]".
AI_ONLY = re.compile(r'(?:[\s,;–-]|and|\[\s*\d+(?:\.\d+)*\s*\]?|(?<![\w.])\d{1,2}(?:\.\d{1,2})+(?![\w.]))*')
NAME = re.compile(r"[A-Z][a-z]+(?:[-'][A-Za-z]+)?")

COMPANIES = {name.lower() for name in '''
    Apple AT&T Bosch CATT CMCC ChinaTelecom ChinaUnicom Comcast Convida Docomo Ericsson Fujitsu
    Futurewei Google Hisilicon Huawei Intel InterDigital Kddi Keysight Kyocera Lenovo LG LGE
    MediaTek Meta Motorola NEC Nokia Nvidia NTT Ofinno OPPO Orange Panasonic Philips Qualcomm
    Rakuten Samsung Sharp Softbank Sony Spreadtrum Telefonica Thales Toyota Unisoc Verizon
    vivo Vodafone Xiaomi ZTE'''.split()}
NOT_NAMES = {'Note', 'Notes', 'Main', 'Continue', 'Continued', 'Cont', 'Optional', 'Offline',
             'Online', 'Email', 'Other', 'Others', 'Session', 'Report', 'Reports', 'Details',
             'Break', 'Breakout', 'Lunch', 'Coffee', 'Comebacks', 'Joint', 'Common', 'General'}


@dataclass
class Line:
    raw: str
    text: str = ''
    ais: list[str] = field(default_factory=list)
    lead: bool = False          # starts with an agenda item
    offline: list[str] = field(default_factory=list)
    chairs: list[str] = field(default_factory=list)
    bullet: bool = False


@dataclass
class Part:
    start: str
    end: str | None
    lines: list[str] = field(default_factory=list)
    fixed: bool = True          # False when the start is only the cell's start


# ------------------------------------------------------------------- lines

def learn_chairs(document) -> set[str]:
    """Capitalised names that appear in parentheses and are not companies."""
    chairs = set()
    for day in document['days']:
        for cell in day['cells']:
            for line in cell['lines']:
                for group in re.findall(r'\(([^()]*)\)', line):
                    parts = [p.strip() for p in re.split(r',|\band\b|/', group) if p.strip()]
                    if parts and all(NAME.fullmatch(p) for p in parts):
                        chairs.update(p for p in parts if p.lower() not in COMPANIES and p not in NOT_NAMES)
    return chairs


def is_agenda(value: str, agenda: dict | None = None) -> bool:
    parts = value.split('.')
    if not all(p.isdigit() for p in parts) or (len(parts) == 1 and (len(value) > 2 or value.startswith('0'))):
        return False
    if int(parts[0]) > 30:
        return False
    return not agenda or len(parts) == 1 or value in agenda or '.'.join(parts[:-1]) in agenda


def expand_range(first: str, last: str) -> list[str]:
    a, b = first.split('.'), last.split('.')
    if len(a) != len(b) or a[:-1] != b[:-1] or not 0 < int(b[-1]) - int(a[-1]) <= 40:
        return [first, last]
    prefix = '.'.join(a[:-1])
    return [f'{prefix}.{n}' if prefix else str(n) for n in range(int(a[-1]), int(b[-1]) + 1)]


def clean(text: str) -> str:
    """Display label: drop qualifiers such as "(if time allows)", keep "(e)RedCap"."""
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r'\s*\([^()]*\)(?![A-Za-z])', ' ', text)
    text = re.sub(r'\s*\([^()]*$', ' ', text)
    text = re.sub(r'\[\s*\]', ' ', text)
    return ' '.join(text.split()).strip(' ,;:/-–—&.')


def analyze(raw: str, chairs: set[str], agenda: dict | None = None) -> Line:
    line = Line(raw)
    text = raw
    if re.match(r'^[-–•]\s*', text):
        line.bullet = True
        text = re.sub(r'^[-–•]\s*', '', text)
    ais, pieces = [], []
    leading, position = True, 0
    for match in BRACKET.finditer(text):
        between = text[position:match.start()]
        leading = leading and bool(SEPARATORS.fullmatch(between))
        value = (match[1] if match[1] is not None else match[2]).replace(' ', '')
        pieces.append(between)
        if OFFLINE_ID.fullmatch(value):
            line.offline.append(value)
        elif not leading and re.fullmatch(r'\d(?:\.\d+)?', value) and float(value) <= 3:
            pass  # a time budget after a title: "NR20 AIoT [2]", "[0.5]"
        elif is_agenda(value):
            line.lead = line.lead or leading
            if ais and re.fullmatch(r'\s*[-–]\s*', between):
                ais.extend(expand_range(ais.pop(), value))   # "[6.0.2.1] - [6.0.2.12]"
            else:
                ais.append(value)
        else:
            pieces[-1] += match[0]  # topic tags such as "[AIoT]" stay in the label
        position = match.end()
    pieces.append(text[position:])
    text = ''.join(pieces)
    if AI_ONLY.fullmatch(text) and BARE_AGENDA.search(text):
        # Unbracketed numbers count only on a line of nothing but numbers.
        bare = [v for v in BARE_AGENDA.findall(text) if is_agenda(v, agenda)]
        ais.extend(bare)
        line.lead = line.lead or bool(bare)
        text = BARE_AGENDA.sub(' ', text)
    line.ais = list(dict.fromkeys(ais))

    def parenthetical(match):
        parts = [p.strip() for p in re.split(r',|\band\b|/', match[1]) if p.strip()]
        if parts and all(p in chairs for p in parts):
            line.chairs.extend(parts)
            return ' '
        if parts and all(p.lower() in COMPANIES for p in parts):
            return ' '
        return match[0]
    line.text = clean(re.sub(r'\(([^()]*)\)', parenthetical, text))
    line.chairs = list(dict.fromkeys(line.chairs))
    return line


def is_descendant(item: str, ancestor: str) -> bool:
    return item != ancestor and item.startswith(ancestor + '.')


# ------------------------------------------------------------------- parts

def parse_marker(raw: str, bounds: tuple[int, int]):
    match = AT_MARK.match(raw) or BARE_MARK.match(raw)
    if not match:
        return None
    try:
        start = clock(match[1], match[2] or 0)
        end = clock(match[3], match[4] or 0) if match[3] else None
    except ValueError:
        return None
    low, high = bounds
    values = [time_to_minutes(start)] + ([time_to_minutes(end)] if end else [])
    if any(v < low - 60 or v > high + 60 for v in values) or (end and end <= start):
        return None   # not a plausible time of this day: keep the line as text
    return start, end, raw[match.end():].strip()


def meaningful(raw: str) -> bool:
    """Drop stray fragments such as "8:" but keep "6.0.2.4, 5.1.3.2"."""
    return bool(re.search(r'[A-Za-z\[#]', raw) or BARE_AGENDA.search(raw))


def split_cell(cell: dict, bounds) -> list[Part]:
    """Parts of one cell, each with a start and an end."""
    parts = [Part(cell['start'], None, fixed=False)]
    blank = False
    for raw in cell['lines']:
        if not raw:
            blank = True
            continue
        marker = parse_marker(raw, bounds)
        if marker:
            start, end, rest = marker
            parts.append(Part(start, end, [rest] if rest and meaningful(rest) else []))
        elif meaningful(raw):
            current = parts[-1]
            if blank and current.end and current.lines:
                # After "@8:30-9:30 ...", a blank line ends that part; the
                # following lines continue from 9:30.
                parts.append(Part(current.end, None, [raw]))
            else:
                current.lines.append(raw)
        blank = False

    # Lines above a marker at the same time are that part's header:
    # "CB Erlin / @11:00-11:45 / R2-2601643 ...".
    merged = []
    for part, following in zip(parts, parts[1:] + [None]):
        if following is not None and part.end is None and following.start <= part.start:
            following.lines[:0] = part.lines
            following.fixed = following.fixed or part.fixed
        else:
            merged.append(part)
    result = []
    for i, part in enumerate(merged):
        following = next((p.start for p in merged[i + 1:] if p.start > part.start), None)
        part.end = part.end or following or cell['end']
        if part.lines and part.end > part.start:
            result.append(part)
    return result


def title_and_chair(infos: list[Line]):
    """Title from the first lines: AI headings, lines naming a chair, a short first line."""
    heads, seen = [], []
    for info in infos:
        if info.bullet or not info.text:
            continue
        if info.lead:
            # Sub-items and lowercase continuations ("cont., to cover ...") describe a heading.
            if info.text[0].islower() or any(is_descendant(ai, s) for ai in info.ais for s in seen):
                continue
        elif not (info.chairs or (not heads and len(info.text.split()) <= 6)):
            continue
        heads.append(info)
        seen.extend(info.ais)
    if heads:
        title = ' / '.join(h.text for h in heads[:3]) + (' …' if len(heads) > 3 else '')
    else:
        title = next((i.text for i in infos if i.text), '')
    chairs = [c for h in heads for c in h.chairs] or [c for i in infos for c in i.chairs]
    group_item = next((h.ais[0] for h in heads if h.ais), None)
    return title, ', '.join(dict.fromkeys(chairs)) or None, group_item, bool(heads)


# Times inside prose use a colon; "[7.10]" is an agenda item, not 07:10.
CLOCK_TEXT = re.compile(r'(?<![\d.:])(\d{1,2}):(\d{2})(?!\d)')
DURATION = re.compile(r'\b\d+(?:\.\d+)?\s*(?:min(?:ute)?s?|hrs?|hours?)\b|\(\s*\d+(?:\.\d+)?\s*h\s*\)', re.I)


def written_times(lines: list[str], bounds) -> set[str]:
    """Clock times written anywhere in the lines, as HH:MM, within the day's reach."""
    low, high = bounds
    found = set()
    for raw in lines:
        marker = parse_marker(raw, bounds)
        if marker:
            found.update(t for t in marker[:2] if t)
        for match in CLOCK_TEXT.finditer(raw):
            try:
                value = clock(match[1], match[2])
            except ValueError:
                continue
            if low - 60 <= time_to_minutes(value) <= high + 60:
                found.add(value)
    return found


def uncertainty(cell: dict, bounds, untitled: int) -> list[str]:
    """Why the rules may have misread this cell; empty when they are on firm ground."""
    reasons = []
    lines = cell['lines']
    for i, raw in enumerate(lines):
        if not raw:
            continue
        marker = parse_marker(raw, bounds)
        rest = marker[2] if marker else raw
        if raw.lstrip().startswith('@') and not marker:
            reasons.append(f'unreadable time marker: {raw!r}')
        elif CLOCK_TEXT.search(rest) or DURATION.search(rest):
            reasons.append(f'time written inside a line: {raw!r}')
        elif marker and not rest and i and lines[i - 1] and marker[0] != cell['start']:
            reasons.append(f'header written right above a time marker: {lines[i - 1]!r} / {raw!r}')
    if untitled:
        reasons.append('a part has no line that names it')
    return list(dict.fromkeys(reasons))


def resolve_overlaps(sessions: list[dict]) -> list[dict]:
    """Give each room one block at a time.

    Blocks with a written start are placed first. A block that only inherits
    its cell's start fills the time they leave free, so an untimed sub-row
    after "10:50-11:50 [009]" becomes 11:50 onwards. Among written blocks, a
    later start cuts an earlier block short.
    """
    busy: dict[str, list[list[int]]] = {}
    placed = []
    for session in sorted(sessions, key=lambda s: (not s['fixed'], s['start'], s['end'])):
        start, end = time_to_minutes(session['start']), time_to_minutes(session['end'])
        if session['fixed']:
            for other in placed:
                if (other['fixed'] and set(other['rooms']) & set(session['rooms'])
                        and other['start'] < session['start'] < other['end']):
                    for interval in (i for room in other['rooms'] for i in busy.get(room, [])):
                        if interval[1] == time_to_minutes(other['end']):
                            interval[1] = start
                    other['end'] = session['start']
        taken = sorted(tuple(i) for room in session['rooms'] for i in busy.get(room, []))
        pieces, cursor = [], start
        for a, b in taken:
            if b > cursor and a < end:
                if a > cursor:
                    pieces.append((cursor, a))
                cursor = max(cursor, b)
        if cursor < end:
            pieces.append((cursor, end))
        pieces = [(a, b) for a, b in pieces if b - a >= 10]
        if not pieces:
            covering = next((o for o in placed if set(o['rooms']) & set(session['rooms'])
                             and time_to_minutes(o['start']) < end and start < time_to_minutes(o['end'])), None)
            if covering is not None:
                covering['lines'].append(f'Also listed here: {session["name"]}')
            continue
        for a, b in pieces:
            placed.append({**session, 'start': minutes_to_time(a), 'end': minutes_to_time(b),
                           'lines': list(session['lines'])})
            for room in session['rooms']:
                busy.setdefault(room, []).append([a, b])
    return sorted(placed, key=lambda s: (s['start'], s['rooms']))


def day_bounds(day: dict) -> tuple[int, int]:
    return (min(time_to_minutes(s) for s, _ in day['slots']),
            max(time_to_minutes(e) for _, e in day['slots']))


def rule_based(cell: dict, bounds, chairs, agenda) -> tuple[list[dict], int]:
    """Sessions of one cell by the rules, and how many parts have no naming line."""
    found, untitled = [], 0
    for part in split_cell(cell, bounds):
        infos = [analyze(raw, chairs, agenda) for raw in part.lines]
        ais = list(dict.fromkeys(ai for info in infos for ai in info.ais))
        title, chair, group_item, named = title_and_chair(infos)
        untitled += not named and not any(info.offline for info in infos)  # "[004] (Xiaomi)" is as named as it gets
        # Nothing but numbers and companies, e.g. "[004] (Ericsson, Nokia)": keep it as written.
        title = title or (f'AI {", ".join(ais[:3])}' if ais else part.lines[0])
        offline = bool(infos[0].offline) or bool(re.search(r'(?<!after )\boffline', title, re.I))
        found.append({'rooms': cell['rooms'], 'start': part.start, 'end': part.end, 'fixed': part.fixed,
                      'name': title, 'chair': chair, 'agenda_items': ais,
                      'group_item': group_item or (ais[0] if ais else None), 'offline': offline,
                      'lines': list(part.lines)})
    return found, untitled


def interpret(document: dict, agenda: dict | None = None, refine=None) -> list[dict]:
    """Session dicts per day: rooms, times, title, chair and agenda items.

    ``refine`` is the LLM fallback. It receives the cells the rules are unsure
    about, with the reasons and the rule-based reading, and returns validated
    sessions for the cells it could read; every other cell keeps the rules.
    """
    chairs = learn_chairs(document)
    readings, doubtful = [], []
    for day in document['days']:
        if not day['slots']:
            continue
        bounds = day_bounds(day)
        for cell in day['cells']:
            found, untitled = rule_based(cell, bounds, chairs, agenda)
            readings.append((day, cell, found))
            reasons = uncertainty(cell, bounds, untitled)
            if reasons:
                doubtful.append({'day': day, 'cell': cell, 'bounds': bounds, 'reasons': reasons, 'rules': found})
    replaced = refine(doubtful) if refine and doubtful else {}
    sessions = []
    for day in document['days']:
        found = []
        for owner, cell, rules in readings:
            if owner is day:
                found.extend({**s, 'day': day['day']} for s in replaced.get(cell['id'], rules))
        sessions.extend(resolve_overlaps(found))
    return sessions


# --------------------------------------------------------------- schedule

def describe(item: str, agenda: dict) -> dict | None:
    match = item
    while match not in agenda and '.' in match:
        match = match.rsplit('.', 1)[0]
    if match not in agenda:
        return None
    parts = match.split('.')
    path = ['.'.join(parts[:i]) for i in range(1, len(parts) + 1)]
    return {'agenda_item': item, 'matched_agenda_item': match, 'description': agenda[match],
            'hierarchy': [{'agenda_item': p, 'description': agenda.get(p)} for p in path]}


def group_label(item: str | None, agenda: dict) -> str:
    if not item:
        return ''
    top = item.split('.')[0]
    description = agenda.get(top)
    return description.split(' - ')[0].strip() if description else f'AI {top}'


def break_name(start: int, end: int, named: list[dict]) -> str:
    best, overlap = 'Break', 0
    for item in named:
        shared = min(time_to_minutes(item['end']), end) - max(time_to_minutes(item['start']), start)
        if shared > overlap:
            best, overlap = item['name'], shared
    return best


def round5(value: str) -> str:
    return minutes_to_time(5 * round(time_to_minutes(value) / 5))


def make_schedule(document: dict, agenda: dict | None, metadata: dict, source_files: list[str],
                  generated_at: str, *, refine=None, sessions: list[dict] | None = None) -> Schedule:
    """``sessions`` replaces the cell reading when the whole document was read by the LLM."""
    agenda = agenda or {}
    first, last = date.fromisoformat(metadata['starts_on']), date.fromisoformat(metadata['ends_on'])
    dates = {(first + timedelta(days=i)).strftime('%A'): (first + timedelta(days=i)).isoformat()
             for i in range(min((last - first).days, 6) + 1)}
    sessions = interpret(document, agenda, refine) if sessions is None else sessions
    room_names = {r['id']: r['name'] for r in document['rooms']}
    days = []
    for day in document['days']:
        raw = [s for s in sessions if s['day'] == day['day']]
        if not raw:
            continue
        if day['day'] not in dates:
            print(f'[ran2] {day["day"]} is outside the meeting dates {first}..{last}; shown without a date')
        used = {room for s in raw for room in s['rooms']}
        rooms = [RoomInfo(id=r['id'], name=room_names[r['id']]) for r in document['rooms'] if r['id'] in used]
        start = min(time_to_minutes(s['start']) for s in raw)
        start -= start % 30
        end = max(time_to_minutes(s['end']) for s in raw)
        end += -end % 5
        slots = sorted((time_to_minutes(a), time_to_minutes(b)) for a, b in day['slots'])
        breaks = [{'name': break_name(a_end, b_start, document['breaks']),
                   'start': minutes_to_time(a_end), 'end': minutes_to_time(b_start), 'label_position': 'time-axis'}
                  for (_, a_end), (b_start, _) in zip(slots, slots[1:])
                  if a_end < b_start and start <= a_end and b_start <= end]
        built = []
        for s in raw:
            begin, finish = round5(s['start']), round5(s['end'])
            if finish <= begin:
                continue
            # Describe the block's top items; "7.8.1" adds little under "7.8".
            tops = [ai for ai in s['agenda_items'] if not any(is_descendant(ai, a) for a in s['agenda_items'])]
            descriptions = [d for d in (describe(ai, agenda) for ai in tops[:6]) if d]
            built.append(Session(
                name=s['name'], start_time=begin, end_time=finish, day=day['day'],
                duration_minutes=time_to_minutes(finish) - time_to_minutes(begin),
                room_ids=list(s['rooms']), chair=s['chair'],
                agenda_item=', '.join(s['agenda_items']) or None,
                group_header='Offline' if s['offline'] else group_label(s['group_item'], agenda),
                description='\n'.join(d['description'] for d in descriptions) or None,
                agenda_descriptions=descriptions,
                notes=[line for line in s['lines'] if not AI_ONLY.fullmatch(line)]))
        days.append(DaySchedule(day_name=day['day'], date=dates.get(day['day']), rooms=rooms,
                                sessions=sorted(built, key=lambda s: (s.start_time, s.room_ids)),
                                timeline=Timeline(start=minutes_to_time(start), end=minutes_to_time(end),
                                                  breaks=breaks)))
    if not days:
        raise ValueError('The RAN2 schedule produced no sessions')
    number = document['meeting_id'].split('#', 1)[1]
    return Schedule(
        wg_id='ran2', meeting_id=document['meeting_id'], meeting_name=f'RAN2#{number}', days=days,
        source_file=source_files[0], source_files=source_files, generated_at=generated_at,
        timezone=metadata['timezone'], starts_on=metadata['starts_on'], ends_on=metadata['ends_on'],
        starts_at=metadata.get('starts_at'), ends_at=metadata.get('ends_at'),
        supplements=document.get('supplements', []))
