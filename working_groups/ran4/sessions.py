"""Read sessions from RAN4 schedule cells with a few rules (research/ran4/DECISIONS.md).

A cell is one session column in one time block. What the chair writes in it:

- times that cut the block: a dashed line ("—12:00—", "---3:00pm"), a range on
  its own line ("8:30 – 10:30") or in parentheses ("BR2 Ad-hoc: 5G BSRF
  (14:30 – 15:30)"), "Start at 8:00" and "(until 11:00)";
- blank lines that split an untimed block into parts. Those are drawn as equal
  pieces, part = [k, n], because the source orders them but gives no times.
  Candidate lists ("Return to", "EARLY RETURN TO") are not split, nor are
  annotations ("(107-B and 107-A)") or a heading alone on its line, which
  belong to the part next to them;
- category lines ("Rel-20 Demod") over topic lines ("[211] FS_NR_demod_SCM");
- an ad hoc owner ("Main Ad-hoc", "BR2 Ad-hoc: Ambient IoT D2T2");
- "Coffee break AH: ..." for a session held in the following break;
- "Chaired by Jerry (Apple)" or "by Aijun (CATT)".

The cell title is the part's top lines (a category, a topic, an ad hoc owner),
up to three joined with " / ", with round brackets removed as RAN2 does. Every
line stays in the notes. Agenda items come from the topic numbers through the
topic list. What the rules cannot read with confidence is reported per cell
for the LLM fallback; without it, the rules' reading is published.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
import re

from shared.agenda import describe, group_label, is_descendant
from shared.officials import join_chairs
from shared.schedule import (MIN_PART_MINUTES, DaySchedule, RoomInfo, Schedule, Session, Timeline,
                             minutes_to_time, time_to_minutes)
from shared.titles import clean
from . import topics as topicmod
from .document import clock

TIME = r'(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?'
COLON_TIME = r'(\d{1,2})[:.](\d{2})\s*(am|pm)?'
# "—12:00—", "----9.30-------", "---3:00pm"
DASH_MARK = re.compile(r'^[-—–]+\s*' + TIME + r'\s*[-—–]*$', re.I)
RANGE = COLON_TIME + r'\s*[-–—~]\s*' + COLON_TIME
# "8:30 – 10:30" on its own line, or "(14:30 – 15:30)" anywhere.
RANGE_LINE = re.compile(r'^' + RANGE + r'\s*$', re.I)
RANGE_PAREN = re.compile(r'\(\s*' + RANGE + r'\s*\)', re.I)
START_AT = re.compile(r'\(?\s*\bstart(?:s|ing)?\s+at\s+' + TIME + r'\s*\)?', re.I)
UNTIL = re.compile(r'\(\s*until\s+' + TIME + r'\s*\)', re.I)
# A clock written in prose: "by 13:30pm", "no later than15:00", "(not earlier than 3pm)".
PROSE_TIME = re.compile(r'(?<![\d.])\d{1,2}:\d{2}(?!\d)|(?<![\d.])\d{1,2}\s*[ap]m\b', re.I)
COFFEE = re.compile(r'^co\w{2,5}\s*break\b', re.I)
COFFEE_ONLY = re.compile(r'^co\w{2,5}\s*break(?:\s*(?:AH|ad[- ]?hoc|offline))?\s*:?$', re.I)
AD_HOC = re.compile(r'^(?:(?:Main|BR\d|RRM|BDaT)\s+)?Ad[- ]?hoc\b\s*:?', re.I)
AD_HOC_WORD = re.compile(r'\bad[- ]?hoc\b|\bAH\b', re.I)
LIST_HEAD = re.compile(r'^(?:\w+\s+)?(?:early\s+)?return\s+to\b', re.I)
CONT = re.compile(r'\(\s*cont\.?\s*\)|\bcont(?:inued)?\.?\s*$', re.I)
CHAIRED = re.compile(r'[,\s]*\(?\s*\bchaired\s+by\s+(.+?)\)?\s*$', re.I)
# "6G operation efficiency by Aijun (CATT)": a capitalised name ending the line.
BY_NAME = re.compile(r"\s+by\s+([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?)\s*(?:\([^()]*\)?)?\s*$")
MISSPELT_CHAIR = re.compile(r'\bch\w*d\s+by\b', re.I)
TOPIC_BRACKET = re.compile(r'\[\s*(\d{3}(?:-[A-Z](?:/[A-Z])?)?)\s*\]')
TOPIC_BARE = re.compile(r'(?<![\w-])(\d{3}-[A-Z](?:/[A-Z])?)(?![\w-])')
TOPIC_LIST = re.compile(r'\(\s*(\d{3}(?:\s*,?\s*\d{3})*)\s*\)')
TOPIC_LINE = re.compile(r'^\[\s*\d{3}')
TDOC = re.compile(r'^R4-\d{7}')
WRITTEN_AI = re.compile(r'\bAI\s+(\d+(?:\.\d+)+)')


# ------------------------------------------------------------------- lines

def written_chairs(line: str) -> tuple[list[str], str]:
    """(chairs, line without them): "…, Chaired by Leo (Huawei)/Jinqiang (OPPO)"."""
    match = CHAIRED.search(line)
    if match:
        names = re.split(r'\s*(?:/|\band/or\b|\band\b|,)\s*', match[1])
    else:
        match = BY_NAME.search(line)
        if not match:
            return [], line
        names = [match[1]]
    chairs = [clean(re.sub(r'\([^()]*\)?', ' ', n)) for n in names]
    return [c for c in chairs if c and not c[0].isdigit()], line[:match.start()]


def topic_numbers(lines: list[str]) -> list[str]:
    """Topic numbers as written: "[101-A]", "[214] [215]", "105-B", "(346, 347)"."""
    found = []
    for line in lines:
        found += TOPIC_BRACKET.findall(line) + TOPIC_BARE.findall(line)
        for group in TOPIC_LIST.findall(line):
            found += re.findall(r'\d{3}', group)
    return list(dict.fromkeys(found))


def _norm(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', text.lower())


def misnamed(line: str, topics: dict) -> str | None:
    """The topic a "[206] NR_Mob_Ph5_RRM" line names when its number is another's.

    The name written after the number is compared with the topic list: a
    name that fits another topic and not its own number points at a typo.
    """
    match = re.match(r'^\[\s*(\d{3}(?:-[A-Z](?:/[A-Z])?)?)\s*\]\s*(.+)$', line)
    if not match or not topics:
        return None
    name = _norm(clean(written_chairs(match[2])[1]))
    if len(name) < 6:
        return None
    own = topicmod.expand(match[1], topics)
    def fits(title):
        title = _norm(title)
        return bool(title) and (title in name or name in title)
    if not own or any(fits(topics[t]['title']) for t in own):
        return None
    others = [t for t, v in topics.items() if t not in own and fits(v['title'])]
    return others[0] if others else None


@dataclass
class Part:
    start: str
    end: str | None
    lines: list[str] = field(default_factory=list)
    fixed: bool = True        # False when the start is only the block's start
    whole: bool = False       # the time stands on its own line and covers the parts below it


def _time(hour, minute, meridiem) -> str:
    return clock(hour, minute or 0, meridiem)


def split_times(cell: dict) -> tuple[list[Part], list[str], list[str]]:
    """(parts, coffee lines, reasons) of one cell, split at the times written in it."""
    parts = [Part(cell['start'], None, fixed=False, whole=True)]
    coffee, reasons = [], []
    in_coffee = blank = False
    for raw in cell['lines']:
        if not raw:
            blank, in_coffee = True, False
            continue
        if COFFEE.match(raw) or in_coffee:
            coffee.append(raw)
            in_coffee, blank = True, False
            continue
        current = parts[-1]
        dash = DASH_MARK.match(raw)
        alone = RANGE_LINE.match(raw)
        paren = RANGE_PAREN.search(raw)
        start_at = START_AT.search(raw)
        try:
            if dash:
                parts.append(Part(_time(*dash.groups()), None, whole=True))
            elif alone:
                parts.append(Part(_time(*alone.groups()[:3]), _time(*alone.groups()[3:]), whole=True))
            elif paren:
                parts.append(Part(_time(*paren.groups()[:3]), _time(*paren.groups()[3:]), [raw]))
            elif start_at:
                rest = START_AT.sub(' ', raw).strip()
                if current.lines and any(current.lines):
                    parts.append(Part(_time(*start_at.groups()), None, [raw] if rest else [], whole=not rest))
                else:   # "Return to (start at 8:30)" heads its block: the block starts later
                    current.start, current.fixed = _time(*start_at.groups()), True
                    current.lines.extend([raw] if rest else [])
            elif current.end and not current.whole and current.lines and blank:
                # After "(8:30-9:30) [211]", a blank line ends that part; the
                # next lines continue from 9:30.
                parts.append(Part(current.end, None, [raw]))
            else:
                current.lines.extend([''] if blank and current.lines else [])
                current.lines.append(raw)
        except ValueError:
            reasons.append(f'unreadable time: {raw!r}')
            current.lines.append(raw)
        if re.match(r'^[-—–]{2,}', raw) and not dash:
            reasons.append(f'unreadable time marker: {raw!r}')
        until = UNTIL.search(raw)
        if until:
            try:
                parts[-1].end = _time(*until.groups())
            except ValueError:
                reasons.append(f'unreadable time: {raw!r}')
        blank = False
    result = []
    for i, part in enumerate(parts):
        following = next((p.start for p in parts[i + 1:] if p.start > part.start), None)
        part.end = part.end or following or cell['end']
        while part.lines and not part.lines[-1]:
            part.lines.pop()
        if not any(part.lines):
            continue
        if time_to_minutes(part.end) <= time_to_minutes(part.start):
            reasons.append(f'no time left for {next(l for l in part.lines if l)!r}')
            continue
        result.append(part)
    return result, coffee, reasons


# ------------------------------------------------------------------- groups

def _annotation(group: list[str]) -> bool:
    return all(_kind(line) == 'note' for line in group)


def _heading_alone(group: list[str], following: list[str] | None) -> bool:
    """A heading set apart from what it heads: "Maintenance" over "[113] …", or
    an ad hoc owner. "5G UE RF (cont)" is a part of its own (DECISIONS.md 2),
    and so is a name followed by another name ("6G operation efficiency")."""
    line = group[0]
    if len(group) != 1 or CONT.search(line) or following is None:
        return False
    return _kind(line) == 'owner' or (_kind(line) == 'text' and _kind(following[0]) == 'item')


def groups_of(lines: list[str]) -> list[list[str]]:
    """The parts the chair split a block into with blank lines.

    Annotations join the group before them, a heading alone joins the group
    after it ("Maintenance" over "[113] …"). A candidate list keeps the
    whole block together: "EARLY RETURN TO" names fallbacks, not an order.
    """
    groups, current = [], []
    for line in lines + ['']:
        if line:
            current.append(line)
        elif current:
            groups.append(current)
            current = []
    if any(LIST_HEAD.match(g[0]) for g in groups):
        return [[line for line in lines if line]]
    merged = []
    for group in groups:
        if _annotation(group) and merged:
            merged[-1] += group
        else:
            merged.append(group)
    result, carry = [], []
    for i, group in enumerate(merged):
        if _heading_alone(group, merged[i + 1] if i + 1 < len(merged) else None):
            carry += group
            continue
        result.append(carry + group)
        carry = []
    if carry:
        if result:
            result[-1] += carry
        else:
            result.append(carry)
    return result


# -------------------------------------------------------------------- title

def _kind(line: str) -> str:
    if re.fullmatch(r'\(.*\)', line) or re.match(r'^notes?\s*:', line, re.I):
        return 'note'
    if re.match(r'^\(?\s*chaired\s+by\b', line, re.I):
        return 'chair'
    if COFFEE_ONLY.match(line) or (AD_HOC.match(line) and not clean(AD_HOC.sub('', line))):
        return 'owner'
    if TOPIC_LINE.match(line) or TDOC.match(line):
        return 'item'
    if LIST_HEAD.match(line):
        return 'list'
    return 'text'


def title_of(lines: list[str]) -> tuple[str, bool]:
    """(title, named) from the top units of a part's lines.

    A text line over items ("NTN" over "[346] …") is a category and stands for
    them; an item or text line under no category is a unit itself; an owner
    line ("Main Ad-hoc") is a unit; a list head stands for its whole list.
    """
    kinds = [_kind(line) for line in lines]
    units, scope = [], None
    for i, (line, kind) in enumerate(zip(lines, kinds)):
        if kind in ('note', 'chair'):
            continue
        if scope == 'list':
            continue
        if kind == 'item' and scope == 'category':
            continue
        following = next((k for k in kinds[i + 1:] if k not in ('note', 'chair')), None)
        if kind == 'list':
            scope = 'list'
        elif kind == 'text':
            scope = 'category' if following == 'item' else None
        elif kind == 'owner':
            scope = None
        text = clean(written_chairs(line)[1])
        if text:
            units.append(text)
    if not units:
        return clean(next((l for l in lines if l), '')), False
    return ' / '.join(units[:3]) + (' …' if len(units) > 3 else ''), True


# ----------------------------------------------------------------- sessions

def build_session(cell, part: Part, lines: list[str], context: dict, piece=None, in_break=False) -> dict:
    topics, agenda = context['topics'], context['agenda']
    title, named = title_of(lines)
    chairs, warnings = [], []
    for line in lines:
        chairs += written_chairs(line)[0]
    chairs = list(dict.fromkeys(chairs))
    owner = context['column_chairs'].get(cell['rooms'][0])
    # The column's chair holds the column's own sessions: not an ad hoc, not one in a break.
    if (not chairs and owner and not in_break and len(cell['rooms']) == 1
            and not any(AD_HOC_WORD.search(l) for l in lines)):
        chairs = [owner]
    numbers = topic_numbers(lines)
    items = []
    for line in lines:
        other = misnamed(line, topics)
        if other:
            warnings.append(f'{cell["id"]}: {line!r} names topic {other}, not its number; agenda items left out')
    if not warnings:
        for number in numbers:
            for topic in topicmod.expand(number, topics):
                items += topics[topic]['agenda_items']
        for line in lines:
            items += [ai for ai in WRITTEN_AI.findall(line) if ai in agenda]
    return {'rooms': cell['rooms'], 'start': part.start, 'end': part.end, 'fixed': part.fixed,
            'name': title, 'named': named, 'chair': join_chairs(chairs),
            'agenda_items': list(dict.fromkeys(items)), 'topics': numbers, 'part': piece,
            'lines': [l for l in lines if l], 'warnings': warnings}


def uncertainty(cell: dict, reasons: list[str], found: list[dict]) -> list[str]:
    """Why the rules may have misread this cell (DECISIONS.md 15, 17); empty when they are on firm ground."""
    reasons = list(reasons)
    for raw in cell['lines']:
        if not raw:
            continue
        rest = RANGE_PAREN.sub(' ', UNTIL.sub(' ', START_AT.sub(' ', raw)))
        if not (DASH_MARK.match(raw) or RANGE_LINE.match(raw)) and PROSE_TIME.search(rest):
            reasons.append(f'time written inside a line: {raw!r}')
        if re.search(r'\bbreak\b', raw, re.I) and not COFFEE.match(raw) and not re.search(r'coffee\s+break', raw, re.I):
            reasons.append(f'break line not recognised: {raw!r}')
        if MISSPELT_CHAIR.search(raw) and not re.search(r'\bchaired\s+by\b', raw, re.I):
            reasons.append(f'chair not read: {raw!r}')
    if cell.get('row_extra'):
        reasons.append(f'row time with extra words: {cell["row_extra"]!r}')
    reasons += [f'a part has no line that names it: {s["lines"][0]!r}' for s in found if not s['named']]
    return list(dict.fromkeys(reasons))


def coffee_break(cell: dict, breaks: list[dict]) -> dict | None:
    """The break right after the block: where "Coffee break AH: …" is held."""
    end = time_to_minutes(cell['end'])
    return next((b for b in sorted(breaks, key=lambda b: b['start'])
                 if 0 <= time_to_minutes(b['start']) - end <= 30), None)


def read_cell(cell: dict, day: dict, context: dict) -> tuple[list[dict], list[str]]:
    """Sessions of one cell by the rules, and the reasons they may be wrong."""
    parts, coffee, reasons = split_times(cell)
    found = []
    for part in parts:
        groups = groups_of(part.lines)
        minutes = time_to_minutes(part.end) - time_to_minutes(part.start)
        if len(groups) > 1 and minutes >= len(groups) * MIN_PART_MINUTES:
            found += [build_session(cell, part, g, context, [k, len(groups)]) for k, g in enumerate(groups, 1)]
        else:
            found.append(build_session(cell, part, [l for l in part.lines], context))
    if coffee:
        held = coffee_break(cell, day['breaks'])
        if held is None:
            reasons.append(f'no break after the block for {coffee[0]!r}')
            if found:
                found[-1]['lines'] += coffee
        else:
            found.append(build_session(cell, Part(held['start'], held['end']), coffee, context, in_break=True))
    return found, uncertainty(cell, reasons, found)


def resolve_overlaps(sessions: list[dict]) -> list[dict]:
    """One session at a time per room: a later start cuts an earlier session short.

    Pieces of one block share its times and never cut each other.
    """
    placed = []
    for session in sorted(sessions, key=lambda s: (s['start'], s['part'] or [0])):
        for other in placed:
            if not set(other['rooms']) & set(session['rooms']):
                continue
            if other['part'] and session['part'] and (other['start'], other['end']) == (session['start'], session['end']):
                continue
            if other['start'] < session['end'] and session['start'] < other['end']:
                if session['start'] > other['start']:
                    other['end'] = session['start']
                else:
                    session['start'] = other['end']
        if session['start'] < session['end']:
            placed.append(session)
    return [s for s in placed if s['start'] < s['end']]


def interpret(document: dict, context: dict, refine=None) -> tuple[list[dict], list[str]]:
    """Session dicts for every day, and the build warnings.

    ``refine`` is the LLM fallback: it receives the cells the rules are unsure
    about, with the reasons and the rules' reading, and returns checked
    sessions for the cells it could read. Every other cell keeps the rules.
    """
    readings, doubtful, warnings = [], [], []
    for day in document['days']:
        for cell in day['cells']:
            found, reasons = read_cell(cell, day, context)
            readings.append((day, cell, found))
            warnings += [w for s in found for w in s['warnings']]
            if reasons:
                doubtful.append({'day': day, 'cell': cell, 'reasons': reasons, 'rules': found})
    replaced = refine(doubtful, context) if refine and doubtful else {}
    sessions = []
    for day in document['days']:
        found = [{**s, 'day': day['day']} for owner, cell, rules in readings if owner is day
                 for s in replaced.get(cell['id'], rules)]
        sessions += resolve_overlaps(found)
    return sessions, warnings


# ---------------------------------------------------------------- schedule

def round5(value: str) -> str:
    return minutes_to_time(5 * round(time_to_minutes(value) / 5))


def end_of_meeting(metadata: dict, start: str) -> tuple[str, str | None]:
    """(end, warning) of the "14:00-EOM" row: the meeting's end in Portal, else 18:00."""
    ends_at = metadata.get('ends_at') or ''
    end = ends_at[11:16] if len(ends_at) >= 16 else ''
    if not end or end <= start:
        fallback = max('18:00', minutes_to_time(time_to_minutes(start) + 30))
        return fallback, f'No Portal end time after {start} for the EOM row; shown until {fallback}'
    return end, None


def make_schedule(document: dict, agenda: dict | None, topics: dict | None, metadata: dict,
                  source_files: list[str], source_labels: dict, generated_at: str, *,
                  column_chairs: dict | None = None, refine=None) -> tuple[Schedule, list[str]]:
    agenda, topics = agenda or {}, topics or {}
    first, last = date.fromisoformat(metadata['starts_on']), date.fromisoformat(metadata['ends_on'])
    dates = {(first + timedelta(days=i)).strftime('%A'): (first + timedelta(days=i)).isoformat()
             for i in range(min((last - first).days, 6) + 1)}
    eom = set()
    for day in document['days']:
        for cell in day['cells']:
            if cell['end'] is None:
                cell['end'], warning = end_of_meeting(metadata, cell['start'])
                eom.update([warning] if warning else [])
    context = {'agenda': agenda, 'topics': topics, 'column_chairs': column_chairs or {}}
    sessions, warnings = interpret(document, context, refine)
    warnings = sorted(eom) + warnings
    days = []
    for day in document['days']:
        raw = [s for s in sessions if s['day'] == day['day']]
        if not raw:
            continue
        if day['day'] not in dates:
            print(f'[ran4] {day["day"]} is outside the meeting dates {first}..{last}; shown without a date')
        used = {room for s in raw for room in s['rooms']}
        rooms = [RoomInfo(id=r['id'], name=r['name']) for r in day['rooms'] if r['id'] in used]
        built = []
        for s in raw:
            begin, finish = round5(s['start']), round5(s['end'])
            if finish <= begin:
                continue
            tops = [ai for ai in s['agenda_items'] if not any(is_descendant(ai, a) for a in s['agenda_items'])]
            descriptions = [d for d in (describe(ai, agenda) for ai in tops[:6]) if d]
            piece = s.get('part')
            if piece and time_to_minutes(finish) - time_to_minutes(begin) < piece[1] * MIN_PART_MINUTES:
                piece = None   # cut short by another session: too short to split
            built.append(Session(
                name=s['name'], start_time=begin, end_time=finish, day=day['day'],
                duration_minutes=time_to_minutes(finish) - time_to_minutes(begin),
                room_ids=list(s['rooms']), chair=s['chair'],
                agenda_item=', '.join(s['agenda_items']) or None,
                group_header=group_label(s['agenda_items'][0] if s['agenda_items'] else None, agenda),
                description='\n'.join(d['description'] for d in descriptions) or None,
                agenda_descriptions=descriptions, notes=list(s['lines']), part=piece))
        start = min([time_to_minutes(s.start_time) for s in built] + [time_to_minutes(b['start']) for b in day['breaks']])
        start -= start % 30
        end = max(time_to_minutes(s.end_time) for s in built)
        end += -end % 5
        breaks = []
        for b in day['breaks']:
            a, z = time_to_minutes(b['start']), time_to_minutes(b['end'])
            if a < start or z > end:
                continue
            held = any(time_to_minutes(s.start_time) < z and a < time_to_minutes(s.end_time) for s in built)
            breaks.append({'name': b['name'], 'start': b['start'], 'end': b['end'],
                           **({'label_position': 'time-axis'} if held else {})})
        days.append(DaySchedule(day_name=day['day'], date=dates.get(day['day']), rooms=rooms,
                                sessions=sorted(built, key=lambda s: (s.start_time, s.room_ids, s.part or [0])),
                                timeline=Timeline(start=minutes_to_time(start), end=minutes_to_time(end),
                                                  breaks=breaks)))
    if not days:
        raise ValueError('The RAN4 schedule produced no sessions')
    number = document['meeting_id'].split('#', 1)[1]
    schedule = Schedule(
        wg_id='ran4', meeting_id=document['meeting_id'], meeting_name=f'RAN4#{number}', days=days,
        source_file=source_files[0], source_files=source_files, source_labels=source_labels,
        generated_at=generated_at, timezone=metadata['timezone'], starts_on=metadata['starts_on'],
        ends_on=metadata['ends_on'], starts_at=metadata.get('starts_at'), ends_at=metadata.get('ends_at'),
        supplements=document.get('supplements', []))
    return schedule, warnings
