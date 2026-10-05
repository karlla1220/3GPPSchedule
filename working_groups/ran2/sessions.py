"""Turn RAN2 schedule cells into timed sessions with deterministic rules.

A room cell is free text. Sessions inside it are separated by time markers
("@12:30", "@8:30-9:30", "From 15:30:", "11:00-12:00 [004] ..."). Bracketed
numbers are agenda items ("[8.3]"), except budgets after a title
("NR20 AIoT [2]") and offline discussion numbers ("[004]", "[xxx]"). A name
in parentheses is a session chair unless it is a company. The original lines
always remain in the session popup, so a rule that misses only affects the
summary shown in the block.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
import re

from shared.schedule import (DaySchedule, RoomInfo, Schedule, Session, Timeline,
                             minutes_to_time, time_to_minutes)
from .document import clock

# Markers that start a session. A bare clock needs a colon so that agenda
# numbers such as "8.10 NR20 MIMO" are never mistaken for 08:10.
AT_MARK = re.compile(r'^(?:@|from\b)\s*(\d{1,2})(?:[:.](\d{2}))?'
                     r'(?:\s*(?:-|–|—|~|to)\s*(\d{1,2})(?:[:.]?(\d{2}))?)?(?!\d|[.:]\d)[\s:,–—-]*', re.I)
BARE_MARK = re.compile(r'^(\d{1,2}):(\d{2})(?:\s*(?:-|–|—|~|to)\s*(\d{1,2}):?(\d{2}))?(?!\d|[.:]\d)[\s:,–—-]*')
CAP = re.compile(r'\b(?:until|end(?:s|ing)?\s+(?:by|at)|finish(?:es)?\s+by)\s+(\d{1,2})[:.](\d{2})', re.I)
# "[8.3]", "[6G CP]", and the unclosed "[8.2.1 Organizational" seen in RAN2#134.
BRACKET = re.compile(r'\[\s*([^\[\]]*?)\s*\]|\[\s*(\d+(?:\.\d+)*)(?![\d.\]])')
SEPARATORS = re.compile(r'(?:[\s,;/&–-]|\band\b|\([^()]*\))*')
OFFLINE_ID = re.compile(r'(?:\d{3}|x{3}|POST\s*\d+\w*|AT\s*\d+\w*)', re.I)
BARE_AGENDA = re.compile(r'(?<![\w.-])\d{1,2}(?:\.\d{1,2})+(?![\w.])')
NAME = re.compile(r"[A-Z][a-z]+(?:[-'][A-Za-z]+)?")
PLACE = re.compile(r'(?:BO|Brk|Break\s*out)\s*(\d+)', re.I)
# "[7.8.1], [7.8.2]" repeats the AI field, so it is not repeated as a note.
AI_ONLY = re.compile(r'(?:[\s,;–-]|and|\[\s*\d+(?:\.\d+)*\s*\]?)*')

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
    owners: list[str] = field(default_factory=list)
    bullet: bool = False
    cb: bool = False


@dataclass
class Segment:
    start: str | None
    end: str | None
    lines: list[str] = field(default_factory=list)
    marker_only: bool = False   # a time-only marker line opened this segment
    blank_before: bool = False
    fixed: bool = True          # False when the start is only the cell's start


# ---------------------------------------------------------------- vocabulary

def learn_names(document) -> tuple[set[str], set[str]]:
    """Chairs and companies used in this document; companies win ties."""
    companies = set(COMPANIES)
    for record in document['offline']:
        companies.update(c.strip().lower() for c in re.findall(r'\(([^()]*)\)', record['coordinator'])
                         for c in c.split(','))
    chairs = set()
    lines = [line for day in document['days'] for cell in day['cells'] for line in cell['lines']]
    for line in lines:
        for group in re.findall(r'\(([^()]*)\)', line):
            parts = [p.strip() for p in re.split(r',|\band\b|/', group) if p.strip()]
            if parts and all(NAME.fullmatch(p) for p in parts):
                chairs.update(p for p in parts if p.lower() not in companies and p not in NOT_NAMES)
        cb = re.fullmatch(r'(?:@\S+\s*)?CBs?\s+([A-Z][a-z]+)(?:\s+TBD)?\s*:?', line.strip())
        if cb and cb[1].lower() not in companies and cb[1] not in NOT_NAMES:
            chairs.add(cb[1])
    return chairs, companies


def is_agenda(value: str, agenda: dict | None) -> bool:
    parts = value.split('.')
    if not all(p.isdigit() for p in parts) or (len(parts) == 1 and (len(value) > 2 or value.startswith('0'))):
        return False
    if int(parts[0]) > 30:
        return False
    return agenda is None or len(parts) == 1 or value in agenda or '.'.join(parts[:-1]) in agenda


def expand_range(first: str, last: str) -> list[str]:
    a, b = first.split('.'), last.split('.')
    if len(a) != len(b) or a[:-1] != b[:-1] or not 0 < int(b[-1]) - int(a[-1]) <= 40:
        return [first, last]
    prefix = '.'.join(a[:-1])
    return [f'{prefix}.{n}' if prefix else str(n) for n in range(int(a[-1]), int(b[-1]) + 1)]


def analyze(raw: str, chairs: set[str], companies: set[str], agenda: dict | None) -> Line:
    line = Line(raw)
    text = raw
    if re.match(r'^[-–•]\s*', text):
        line.bullet = True
        text = re.sub(r'^[-–•]\s*', '', text)

    ais, pieces = [], []
    leading, position = True, 0
    for match in BRACKET.finditer(text):
        between = text[position:match.start()]
        if not SEPARATORS.fullmatch(between):
            leading = False
        value = (match[1] if match[1] is not None else match[2]).replace(' ', '')
        pieces.append(between)
        if OFFLINE_ID.fullmatch(value):
            line.offline.append(value)
        elif not leading and re.fullmatch(r'\d(?:\.\d+)?', value) and float(value) <= 3:
            pass  # a time budget in TUs after a title: "NR20 AIoT [2]", "[0.5]"
        elif is_agenda(value, None):
            if leading:
                line.lead = True
            if ais and re.fullmatch(r'\s*[-–]\s*', between):
                ais.extend(expand_range(ais.pop(), value))
            else:
                ais.append(value)
        else:
            pieces[-1] += match[0]  # topic tags such as "[AIoT]" stay in the label
        position = match.end()
    pieces.append(text[position:])
    text = ''.join(pieces)

    # "6.0.2.4, 5.1.3.2, ...", "- 8.6.1 Organizational", "... and continue 8.3.2, 8.3.4"
    body = text.strip()
    if BARE_AGENDA.match(body):
        spans = [body]
    else:
        spans = [m[1] for m in re.finditer(r'\b(?:continue|cont\.?)\s+((?:\d[\d.]*(?:\s*(?:,|and)\s*)?)+)', body, re.I)]
    bare = [v for span in spans for v in BARE_AGENDA.findall(span) if is_agenda(v, agenda)]
    if bare:
        line.lead = line.lead or (not ais and BARE_AGENDA.match(body) is not None) or bool(
            re.match(r'(?:continue|cont\.?)\s+\d', body, re.I))
        ais.extend(bare)
        text = BARE_AGENDA.sub(' ', text)
    line.ais = list(dict.fromkeys(ais))

    def parenthetical(match):
        parts = [p.strip() for p in re.split(r',|\band\b|/', match[1]) if p.strip()]
        if parts and all(p in chairs for p in parts):
            line.chairs.extend(parts)
            return ' '
        if parts and all(p.lower() in companies for p in parts):
            line.owners.extend(parts)
            return ' '
        return match[0]
    text = re.sub(r'\(([^()]*)\)', parenthetical, text)
    cb = re.match(r'\s*CBs?\b(?:\s+(?:for\s+)?([A-Z][a-z]+)\b)?', text)
    if cb:
        line.cb = True
        if cb[1] in chairs:
            line.chairs.append(cb[1])
            text = text[:cb.start(1)] + text[cb.end(1):]
    # "[7.9] R19 IoT NTN [0] Sergio", but not "Session report from Mattias"
    tail = re.search(r'(\S+)?\s+([A-Z][a-z]+)\s*:?\s*$', text)
    if tail and tail[2] in chairs and (tail[1] or '').lower() not in {'from', 'by', 'of', 'with', 'to'}:
        line.chairs.append(tail[2])
        text = text[:tail.start(2)]
    line.chairs = list(dict.fromkeys(line.chairs))
    line.text = clean(text)
    return line


def clean(text: str) -> str:
    """Display label: drop qualifiers such as "(if time allows)", keep "(e)RedCap"."""
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r'\s*\([^()]*\)(?![A-Za-z])', ' ', text)
    text = re.sub(r'\s*\([^()]*$', ' ', text)
    text = re.sub(r'\[\s*\]', ' ', text)
    text = ' '.join(text.split()).strip(' ,;:/-–—&')
    # "Continue 8.4.2 and 8.4.3" leaves a dangling "and" once numbers are gone.
    text = re.sub(r'(?:\s*(?:,|\band\b|\bor\b))+$', '', text)
    return text.strip(' ,;:/-–—&.')


def is_descendant(item: str, ancestor: str) -> bool:
    return item != ancestor and item.startswith(ancestor + '.')


# --------------------------------------------------------------- segmentation

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
        return None
    return start, end, raw[match.end():].strip()


def caps(lines) -> int | None:
    values = []
    for raw in lines:
        for match in CAP.finditer(raw):
            try:
                values.append(time_to_minutes(clock(match[1], match[2])))
            except ValueError:
                pass
    return min(values) if values else None


def meaningful(raw: str) -> bool:
    """Drop stray fragments such as "8:" but keep "6.0.2.4, 5.1.3.2"."""
    return bool(re.search(r'[A-Za-z\[#]', raw) or BARE_AGENDA.search(raw))


def split_cell(cell: dict, bounds, analyze_line) -> list[Segment]:
    segments = [Segment(None, None)]
    blank = False
    for raw in cell['lines']:
        if not raw:
            blank = True
            continue
        marker = parse_marker(raw, bounds)
        current = segments[-1]
        if marker:
            start, end, rest = marker
            segment = Segment(start, end, marker_only=not rest, blank_before=blank)
            if rest and meaningful(rest):
                segment.lines.append(rest)
            segments.append(segment)
        elif meaningful(raw):
            info = analyze_line(raw)
            # "@8:30-9:30 ... <blank> CB Erlin ..." : a new labelled block
            # after an explicitly ended one starts where that one ends.
            if blank and current.end and is_label(info, first=True):
                segments.append(Segment(current.end, None, [raw], blank_before=True))
            else:
                current.lines.append(raw)
        blank = False

    cell_start, cell_end = cell['start'], cell['end']
    if segments[0].start is None:
        segments[0].start, segments[0].fixed = cell_start, False
    # Untimed lines followed by a marker at the same time are that part's
    # header: "CB Erlin / [7.1] ... / @11:00-11:45 / R2-2601643 ...".
    merged = []
    for segment, following in zip(segments, segments[1:] + [None]):
        if following is not None and segment.end is None and following.start <= segment.start:
            following.lines[:0] = segment.lines
            following.fixed = following.fixed or segment.fixed
        else:
            merged.append(segment)
    segments = merged

    # A header written just above a time-only marker belongs to that marker:
    # "[8.1] NR20 AI/M PHY (Erlin) / @18:30-19:30 / [8.1.1] / [8.1.2]".
    for before, after in zip(segments, segments[1:]):
        if not after.marker_only or after.blank_before or len(before.lines) < 2:
            continue
        last = analyze_line(before.lines[-1])
        rest = [analyze_line(r) for r in before.lines[:-1]]
        if not any(is_label(r) or (r.lead and r.text) for r in rest):
            continue
        infos = [analyze_line(r) for r in after.lines]
        own_heading = any(i.text and (i.lead or is_label(i)) for i in infos)
        children = [ai for i in infos for ai in i.ais]
        if (is_label(last) or last.cb) and not last.lead or (
                last.lead and not own_heading and children and last.ais
                and all(any(is_descendant(c, a) or c == a for a in last.ais) for c in children)):
            after.lines.insert(0, before.lines.pop())

    for segment in segments:
        late = re.search(r'\(\s*from\s+(\d{1,2})[:.](\d{2})\s*\)', segment.lines[0], re.I) if segment.lines else None
        if late and not segment.fixed:
            moved = parse_marker(f'@{late[1]}:{late[2]}', bounds)
            if moved and segment.start < moved[0] < cell_end:
                segment.start, segment.fixed = moved[0], True   # "CB Sergio NTN (from 9:00)"
    resolved = []
    for i, segment in enumerate(segments):
        following = next((s.start for s in segments[i + 1:] if s.start), None)
        end = segment.end or (following if following and following > segment.start else None) or cell_end
        limit = caps(segment.lines)
        if limit is not None and time_to_minutes(segment.start) < limit < time_to_minutes(end):
            end = minutes_to_time(limit)
        if segment.lines and end > segment.start:
            segment.end = end
            resolved.append(segment)
    return resolved


def is_label(info: Line, first: bool = False) -> bool:
    """A non-agenda line naming a block: "Rel-19 corrections (Erlin)", "CB Kyeongin".

    A block's first line also names it when it is a short CB/offline heading
    ("NTN CB session", "UP offline") or ends with a colon.
    """
    if info.lead or info.bullet:
        return False
    if info.chairs:
        return True
    short = re.fullmatch(r'(?:\S+\s+){0,2}(?:CBs?|offline)(?:\s+(?:session|slot))?', info.text, re.I)
    return first and bool(info.cb or short or info.raw.rstrip().endswith(':'))


# ------------------------------------------------------------------ labelling

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
    if description:
        return description.split(' - ')[0].strip()
    return f'AI {top}'


def label_segment(segment: Segment, infos: list[Line], previous_heads: list[Line], agenda: dict):
    """Return (title, chair, agenda items, heads, group item)."""
    heads = []
    for index, info in enumerate(infos):
        if info.bullet:
            continue
        if info.lead and info.text:
            if any(is_descendant(ai, head_ai) for head in heads for head_ai in head.ais for ai in info.ais):
                continue
            heads.append(info)
        elif is_label(info, first=index == 0):
            heads.append(info)
    ais = list(dict.fromkeys(ai for info in infos for ai in info.ais))
    chairs = list(dict.fromkeys(c for info in heads for c in info.chairs)) or \
        list(dict.fromkeys(c for info in infos for c in info.chairs))

    title = ''
    if heads and not heads[0].lead:
        label = re.sub(r'\bTBD\b', '', heads[0].text).strip(' :')
        if re.fullmatch(r'CBs?', label, re.I) or not label:
            # "CB Kyeongin / R19 NES comebacks / ...": name the comeback topics.
            following, seen = [], []
            for info in infos[infos.index(heads[0]) + 1:]:
                if info.bullet or not info.text or ':' in info.text:
                    continue
                if any(is_descendant(ai, a) for ai in info.ais for a in seen):
                    continue
                seen.extend(info.ais)
                topic = clean(re.sub(r'^\s*CBs?\b(?:\s+for\b)?', '', info.text))
                if topic and not topic[0].islower() and not re.match(
                        r'(?:TBD|Details|After|Remaining|Overflow|Outcome|Note|All|Continue|(?:Other )?issues'
                        r'|R\d-\d)\b', topic, re.I):
                    following.append(topic)
            if following:
                title = 'CB: ' + ' / '.join(following[:2]) + (' …' if len(following) > 2 else '')
            else:
                title = heads[0].text or 'CB'
        else:
            title = label
    elif heads:
        title = ' / '.join(h.text for h in heads[:3]) + (' …' if len(heads) > 3 else '')
    if not title:
        text = next((i.text for i in infos if i.text and not i.bullet), '')
        title = text
    if not title and ais:
        ancestor = next((h for h in reversed(previous_heads) if h.ais and all(
            any(is_descendant(ai, a) for a in h.ais) for ai in ais)), None)
        if ancestor:
            title = ancestor.text
            chairs = chairs or ancestor.chairs
        else:
            names = [d['description'] for d in filter(None, (describe(ai, agenda) for ai in ais[:2]))]
            title = ' / '.join(names)
    if not title:
        title = clean(segment.lines[0]) or segment.lines[0]
    group_item = next((h.ais[0] for h in heads if h.ais), ais[0] if ais else None)
    return title, ', '.join(chairs) or None, ais, heads, group_item


def room_of(place: str, rooms: list[dict]) -> str | None:
    match = PLACE.fullmatch(place.strip())
    ids = {r['id'] for r in rooms}
    if match and f'brk{match[1]}' in ids:
        return f'brk{match[1]}'
    if re.fullmatch(r'main(?:\s+room)?', place.strip(), re.I) and 'main' in ids:
        return 'main'
    return None


def coordinator_name(text: str) -> str:
    return re.sub(r'\s*\([^()]*\)?\s*$', '', text).strip()


# ------------------------------------------------------------------- assembly

def interpret(document: dict, agenda: dict | None = None) -> list[dict]:
    """Return session dicts in document order: day, rooms, times and labels."""
    agenda = agenda or {}
    chairs, companies = learn_names(document)
    cache = {}

    def analyze_line(raw):
        if raw not in cache:
            cache[raw] = analyze(raw, chairs, companies, agenda or None)
        return cache[raw]

    sessions = []
    for day in document['days']:
        if not day['slots']:
            continue
        bounds = (min(time_to_minutes(s) for s, _ in day['slots']),
                  max(time_to_minutes(e) for _, e in day['slots']))
        day_sessions = []
        for cell in day['cells']:
            previous_heads = []
            cb_chair = None
            for segment in split_cell(cell, bounds, analyze_line):
                infos = [analyze_line(raw) for raw in segment.lines]
                title, chair, ais, heads, group_item = label_segment(segment, infos, previous_heads, agenda)
                if title.startswith('CB'):
                    # "CB Erlin NR19 MIMO / @8:30 ... / CB NR19 Others / @9:30 ...": one CB session chair
                    chair = chair or cb_chair
                    cb_chair = chair
                previous_heads.extend(heads)
                # "- outcome of [301]" and comebacks mention offlines without being one.
                body = [i for i in infos if not i.bullet] or infos
                offline_ids = list(dict.fromkeys(o for i in body for o in i.offline))
                offline = not title.startswith('CB') and bool(
                    offline_ids or re.search(r"(?<!after )\boffline(?:s)?\b", title, re.I))
                day_sessions.append({
                    'day': day['day'], 'rooms': cell['rooms'], 'start': segment.start, 'end': segment.end,
                    'fixed': segment.fixed,
                    'name': title, 'chair': chair, 'agenda_items': ais, 'group_item': group_item,
                    'offline': offline, 'offline_ids': offline_ids,
                    'owners': list(dict.fromkeys(o for i in infos for o in i.owners)),
                    'lines': list(segment.lines)})
        sessions.extend(attach_offline_list(resolve_overlaps(day_sessions), document, day['day']))
    return sessions


def resolve_overlaps(sessions: list[dict]) -> list[dict]:
    """Give each room one block at a time.

    Blocks with a written start time are placed first. A block that only
    inherits its cell's start fills the time they leave free, so an untimed
    sub-row after "10:50-11:50 [009]" becomes 11:50 onwards. Within the
    written blocks, a later start cuts an earlier block short.
    """
    busy: dict[str, list[tuple[int, int]]] = {}
    placed = []
    order = sorted(sessions, key=lambda s: (not s['fixed'], s['start'], s['end']))
    for session in order:
        start, end = time_to_minutes(session['start']), time_to_minutes(session['end'])
        taken = sorted(interval for room in session['rooms'] for interval in busy.get(room, []))
        if session['fixed']:
            for other in placed:
                if (set(other['rooms']) & set(session['rooms']) and other['fixed']
                        and other['start'] < session['start'] < other['end']):
                    release(busy, other, time_to_minutes(session['start']))
                    other['end'] = session['start']
            taken = sorted(interval for room in session['rooms'] for interval in busy.get(room, []))
        pieces, cursor = [], start
        for a, b in taken:
            if b <= cursor or a >= end:
                continue
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
            piece = {**session, 'start': minutes_to_time(a), 'end': minutes_to_time(b),
                     'lines': session['lines'] if len(pieces) == 1 else list(session['lines'])}
            placed.append(piece)
            for room in piece['rooms']:
                busy.setdefault(room, []).append((a, b))
    return sorted(placed, key=lambda s: (s['start'], s['rooms']))


def release(busy, session, new_end):
    for room in session['rooms']:
        intervals = busy.get(room, [])
        old = (time_to_minutes(session['start']), time_to_minutes(session['end']))
        if old in intervals:
            intervals[intervals.index(old)] = (old[0], new_end)


def attach_offline_list(sessions: list[dict], document: dict, day: str) -> list[dict]:
    """Match the 'List of Offline Face to Face discussions' to table blocks."""
    rooms = document['rooms']
    for record in document['offline']:
        if record['day'] != day:
            continue
        room = room_of(record['place'], rooms)
        if room is None:
            continue
        numbered = [n for n in record['numbers'] if n.lower() != 'xxx']
        match = next((s for s in sessions if room in s['rooms'] and s['start'] == record['start']), None) or next(
            (s for s in sessions if room in s['rooms'] and set(numbered) & set(s['offline_ids'])), None)
        label = (f'[{numbered[-1]}] ' if numbered else '') + record['title']
        coordinator = coordinator_name(record['coordinator'])
        if match is not None:
            if not (match['offline'] or not match['agenda_items']):
                continue
            # The list's title is more specific than "[004] (Ericsson, Nokia)".
            same_number = set(numbered) & {n for n in match['offline_ids'] if n.lower() != 'xxx'}
            stripped = clean(re.sub(r'\[[^\]]*\]', ' ', match['name']))
            if same_number or not stripped:
                match['name'] = label
            match['chair'] = match['chair'] or coordinator or None
            match['offline'] = True
            match['lines'].append(f'Offline list: {label} — {record["coordinator"]}'.strip(' —'))
            continue
        start, end = time_to_minutes(record['start']), time_to_minutes(record['end'])
        if any(room in s['rooms'] and time_to_minutes(s['start']) < end and start < time_to_minutes(s['end'])
               for s in sessions):
            continue  # the table already uses this room then
        sessions.append({
            'day': day, 'rooms': [room], 'start': record['start'], 'end': record['end'], 'name': label,
            'chair': coordinator or None, 'agenda_items': [], 'group_item': None, 'offline': True,
            'offline_ids': record['numbers'], 'owners': [], 'fixed': True,
            'lines': [f'Offline list: {label} — {record["coordinator"]}'.strip(' —')]})
    return sorted(sessions, key=lambda s: (s['start'], s['rooms']))


def break_name(start: int, end: int, named: list[dict]) -> str:
    best, overlap = 'Break', 0
    for item in named:
        a, b = time_to_minutes(item['start']), time_to_minutes(item['end'])
        shared = min(b, end) - max(a, start)
        if shared > overlap:
            best, overlap = item['name'], shared
    return best


def make_schedule(document: dict, agenda: dict | None, metadata: dict, source_files: list[str],
                  generated_at: str) -> Schedule:
    agenda = agenda or {}
    first, last = date.fromisoformat(metadata['starts_on']), date.fromisoformat(metadata['ends_on'])
    if last < first or (last - first).days > 6:
        raise ValueError('A weekday schedule needs a meeting of at most seven days')
    dates = {(first + timedelta(days=i)).strftime('%A'): (first + timedelta(days=i)).isoformat()
             for i in range((last - first).days + 1)}
    sessions = interpret(document, agenda)
    room_names = {r['id']: r['name'] for r in document['rooms']}
    days = []
    for day in document['days']:
        raw = [s for s in sessions if s['day'] == day['day']]
        if not raw:
            continue
        if day['day'] not in dates:
            raise ValueError(f'{day["day"]} is outside the meeting dates {first}..{last}')
        used = {room for s in raw for room in s['rooms']}
        rooms = [RoomInfo(id=r['id'], name=room_names[r['id']]) for r in document['rooms'] if r['id'] in used]
        start = min(time_to_minutes(s['start']) for s in raw)
        start -= start % 30
        end = max(time_to_minutes(s['end']) for s in raw)
        end += -end % 5
        slots = sorted((time_to_minutes(a), time_to_minutes(b)) for a, b in day['slots'])
        breaks = []
        for (_, a_end), (b_start, _) in zip(slots, slots[1:]):
            if a_end < b_start and start <= a_end and b_start <= end:
                breaks.append({'name': break_name(a_end, b_start, document['breaks']),
                               'start': minutes_to_time(a_end), 'end': minutes_to_time(b_start),
                               'label_position': 'time-axis'})
        built = []
        for s in raw:
            # Describe the block's top items; "7.8.1" adds little under "7.8".
            tops = [ai for ai in s['agenda_items'] if not any(is_descendant(ai, a) for a in s['agenda_items'])]
            descriptions = [d for d in (describe(ai, agenda) for ai in tops[:6]) if d]
            notes = [line for line in s['lines'] if not AI_ONLY.fullmatch(line)]
            group = 'Offline' if s['offline'] else group_label(s['group_item'], agenda)
            begin, finish = round5(s['start']), round5(s['end'])
            if finish <= begin:
                continue
            built.append(Session(
                name=s['name'], start_time=begin, end_time=finish, day=day['day'],
                duration_minutes=time_to_minutes(finish) - time_to_minutes(begin),
                room_ids=list(s['rooms']), chair=s['chair'],
                agenda_item=', '.join(s['agenda_items']) or None, group_header=group,
                description='\n'.join(d['description'] for d in descriptions) or None,
                agenda_descriptions=descriptions, notes=notes))
        days.append(DaySchedule(day_name=day['day'], date=dates[day['day']], rooms=rooms,
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
        starts_at=metadata.get('starts_at'), ends_at=metadata.get('ends_at'))


def round5(value: str) -> str:
    minutes = time_to_minutes(value)
    return minutes_to_time(5 * round(minutes / 5))
