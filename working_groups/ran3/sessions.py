"""Turn RAN3 time plan cells and offline room slots into sessions.

A main session cell lists topics as label lines ("6G SI: AI 10.7, 10.4"),
with a vice chair's initial ("L") on a line of its own. Each label line is a
piece the chair set apart: a cell with two of them is shown as "1/2 of 120 min"
and "2/2 of 120 min", in the order written. When the cell uses blank lines,
the blank-line groups are the pieces instead. Every other line (a continued
item "10.3.1.1 (cont.)", a modifier "(1hr until 19:30 at latest)", an offline
held in the same row) belongs to the piece above it and is kept as a note.

The title is the cell's own label with brackets dropped as RAN2 drops them
("6G Offline (CB#8)" -> "6G Offline"); the agenda items go to the AI line.
A chair is only what the cell writes: the initial, resolved to a name by the
pipeline. Lines the rules do not know are flagged for the LLM fallback; the
rules' reading stands when it is not used or not accepted.
"""
from __future__ import annotations

from datetime import date, timedelta
import re

from shared.agenda import describe, group_label, is_descendant, written_ranges
from shared.schedule import (DaySchedule, MIN_PART_MINUTES, RoomInfo, Schedule, Session, Timeline,
                             minutes_to_time, time_to_minutes)
from shared.titles import clean

ROOMS = (('main', 'Main session'), ('offline', 'Offline room'))
NUMBER = r'\d{1,2}(?:\.\d{1,2})*'
ITEM = rf'(?:AI\s*)?{NUMBER}(?:\s*-\s*{NUMBER})?'
ITEMS = rf'{ITEM}(?:\s*[,/]\s*{ITEM})*\s*,?'
TAIL = r'(?:\s*\([^()]*\))?'
# "6G SI: AI 10.7, 10.4", "Corrections: AI 9/31", "IoT NTN WI AI 15" (#129, no colon).
LABEL = re.compile(rf'^(?!AI\s*\d)(?P<label>[^:]*?[A-Za-z][^:]*?)\s*(?::\s*|\s+(?=AI\s*\d))AI\s*(?P<items>{ITEMS}){TAIL}$')
LABEL_LIKE = re.compile(r'^(?!AI\s*\d)[^:]*[A-Za-z][^:]*:\s*AI\b')
# "10.3.1.1 (cont.)", "AI 8 (cont.)", "AI32, AI6", "AI31.3".
NUMBERS = re.compile(rf'^(?P<items>{ITEMS}){TAIL}$')
CLOCK_LINE = re.compile(r'^(\d{1,2}):?(\d{2}):?\s+(?P<rest>\S.*)$')
MARKER = re.compile(r'\b(?:START|END)\s+OF\s+MEETING\b', re.I)
OFFLINE = re.compile(r'^(?:6G\s*)?Offline\b', re.I)
PROGRESS = re.compile(r'^(?:Early\s+)?CBs\*?$', re.I)
# "(CB#8)", "(CB9)", "(CB#16, CB#17)", "CB: # 1_R20N3pathFail"; not "CB on SBFD" or "CBs".
CB_REF = re.compile(r'\bCB\s*(?::\s*)?(?:#\s*([A-Za-z0-9]+)|(\d+))', re.I)
INITIAL = re.compile(r'^[A-Z]$')
# Offline room rows that are not offline room sessions: a ban on booking, the
# main room's own 6G offline (listed in the time plan), the social event.
NOT_OFFLINE = re.compile(r'^(?:No parallel offlines|No offlines|6G offline in main room|Reserved\s*\(6G offline\)'
                         r'|(?:RAN3\s+)?Social Event)', re.I)
AI_TAG = re.compile(r'\[\s*AI\s*:\s*([\d.]+)\s*\]', re.I)
PLACEHOLDERS = {'tbd', 'tba', '-'}


def cb_refs(text: str) -> list[str]:
    return [a or b for a, b in CB_REF.findall(text)]


def kind(line: str) -> str:
    """What a time plan line is; 'unknown' lines make the cell uncertain."""
    if not line:
        return 'blank'
    if MARKER.search(line):
        return 'marker'
    if CLOCK_LINE.match(line) and int(CLOCK_LINE.match(line)[1]) < 24:
        return 'clock'
    if LABEL.match(line):
        return 'label'
    if NUMBERS.match(line):
        return 'number'
    if INITIAL.match(line):
        return 'initial'
    if OFFLINE.match(line):
        return 'offline'
    if PROGRESS.match(line):
        return 'progress'
    if re.fullmatch(r'\(.*\)', line):
        return 'modifier'
    return 'unknown'


HEADS = {'label', 'number', 'progress', 'offline', 'clock'}


def expand(items: str, agenda: dict | None, ranges: list | None = None) -> list[str]:
    """"AI 1-8" -> 1..8 (as the agenda lists them), "9/31" -> 9, 31.

    A range is noted in ranges, so the AI line can show it as written ("1–8").
    """
    result = []
    for token in re.split(r'\s*[,/]\s*', items.strip().rstrip(',')):
        token = re.sub(r'^AI\s*', '', token.strip(), flags=re.I)
        span = re.fullmatch(r'(\d+)\s*-\s*(\d+)', token)
        if span and int(span[1]) < int(span[2]) <= int(span[1]) + 40:
            numbers = [str(n) for n in range(int(span[1]), int(span[2]) + 1)]
            result += [n for n in numbers if not agenda or n in agenda]
            if ranges is not None:
                ranges.append((f'{span[1]}–{span[2]}', numbers))
        elif re.fullmatch(NUMBER, token):
            result.append(token)
    return list(dict.fromkeys(result))


def line_items(line: str, agenda: dict | None, ranges: list | None = None) -> list[str]:
    match = LABEL.match(line) or NUMBERS.match(line)
    return expand(match['items'], agenda, ranges) if match else []


# ------------------------------------------------------------------- pieces

def pieces(lines: list[str]) -> tuple[list[list[str]], bool]:
    """The cell's pieces in order, and whether blank lines set them apart."""
    if '' in lines:
        groups, current = [], []
        for line in lines + ['']:
            if line:
                current.append(line)
            elif current:
                groups.append(current)
                current = []
        result = []
        for group in groups:
            if not result or kind(group[0]) in HEADS:
                result.append(group)
            else:
                result[-1] = result[-1] + group     # "END OF MEETING" closes the piece above
        return result, True
    result = []
    for line in lines:
        k = kind(line)
        if not result or k == 'label' and any(kind(x) == 'label' for x in result[-1]):
            result.append([line])
        elif k == 'label' and kind(result[-1][0]) in HEADS - {'label'}:
            result.append([line])                    # "AI 8 (cont.)" then "Corrections: AI 9"
        else:
            result[-1].append(line)
    return result, False


def title_of(piece: list[str]) -> str:
    labels = [clean(LABEL.match(line)['label']) for line in piece if kind(line) == 'label']
    if labels:
        return ' / '.join(labels[:3]) + (' …' if len(labels) > 3 else '')
    for line in piece:
        if kind(line) in ('marker', 'initial', 'blank'):
            continue
        clock = CLOCK_LINE.match(line)
        text = clean(clock['rest'] if clock else line)
        if text:
            return text
    return ''


def uncertainty(cell: dict) -> list[str]:
    """Why the rules may have misread a cell (U1, U2 in research/ran3/DECISIONS.md)."""
    if cell['evening']:
        return []    # the evening rows are taken as written: events, dinners, a 6G offline
    reasons = []
    for line in cell['lines']:
        if kind(line) == 'unknown':
            reasons.append(('label with an unreadable agenda part: ' if LABEL_LIKE.match(line)
                            else 'line of no known kind: ') + repr(line))
    return reasons


def _span(cell, k, n):
    """Part [k, n] of the cell, nested in the cell's own part of its block."""
    if n == 1:
        return cell['part']
    if cell['part']:
        outer_k, outer_n = cell['part']
        return [(outer_k - 1) * n + k, outer_n * n]
    return [k, n]


def read_cell(cell: dict, agenda: dict | None, cbs: dict) -> list[dict]:
    """Sessions of one time plan cell by the rules."""
    groups, blank_split = pieces(cell['lines'])
    groups = [g for g in groups if any(kind(x) not in ('marker', 'initial') for x in g)]
    if not groups:
        return []
    minutes = time_to_minutes(cell['end']) - time_to_minutes(cell['start'])
    outer = cell['part'][1] if cell['part'] else 1
    if len(groups) > 1 and minutes < outer * len(groups) * MIN_PART_MINUTES:
        groups = [[x for g in groups for x in g]]   # pieces would be under 30 min: one cell, all lines
    initials = [x for x in cell['lines'] if kind(x) == 'initial']
    result = []
    for k, group in enumerate(groups, 1):
        chair = ([x for x in group if kind(x) == 'initial'] if blank_split else initials)[:1]
        result.append(session_of(cell, group, chair[0] if chair else None, _span(cell, k, len(groups)),
                                 agenda, cbs, struck=cell['struck'] if k == 1 else []))
    return result


def session_of(cell, group, chair, part, agenda, cbs, struck=(), title=None, items=None) -> dict:
    start = cell['start']
    clock = next((CLOCK_LINE.match(x) for x in group[:1] if kind(x) == 'clock'), None)
    if clock and part is None:
        written = f'{int(clock[1]):02d}:{clock[2]}'
        if cell['start'] <= written < cell['end'] and time_to_minutes(written) % 5 == 0:
            start = written
    head = next((x for x in group if kind(x) not in ('initial', 'marker', 'blank')), '')
    ranges = []
    written = [i for x in group for i in line_items(x, agenda, ranges)]
    # An LLM answer brings its own items (written ones only); a CB's item comes from the agenda's notes.
    items = written if items is None else list(items)
    if kind(head) == 'offline' or (kind(head) == 'clock' and OFFLINE.match(clock['rest'])):
        items += [cbs[c]['agenda_item'] for x in group for c in cb_refs(x) if c in cbs]
    notes = list(group) + [f'Not treated: {text}' for text in struck]
    for ref in dict.fromkeys(c for x in group for c in cb_refs(x)):
        if ref in cbs and kind(head) in ('offline', 'clock'):
            moderator = cbs[ref].get('moderator')
            notes.append(cbs[ref]['name'] + (f' (moderator - {moderator})' if moderator else ''))
    return {'id': cell['id'], 'day': cell['day'], 'room': 'main', 'start': start, 'end': cell['end'],
            'part': part, 'name': title if title is not None else title_of(group), 'chair_label': chair,
            'agenda_items': list(dict.fromkeys(items)), 'ranges': ranges, 'notes': notes}


def interpret(document: dict, agenda: dict | None = None, cbs: dict | None = None, refine=None) -> list[dict]:
    """Main session sessions per cell; ``refine`` (LLM) rereads the cells the rules flag.

    A document the LLM read whole (llm.read_document) brings its pieces instead
    of cells; they become sessions the same way, so CB blocks still give items.
    """
    cbs = cbs or {}
    if document.get('pieces') is not None:
        return [session_of(p['cell'], p['lines'], p['chair'], p['part'], agenda, cbs, struck=p['struck'],
                           title=p['title'], items=p['items']) for p in document['pieces']]
    readings, doubtful = [], []
    for day in document['days']:
        for cell in day['cells']:
            found = read_cell(cell, agenda, cbs)
            readings.append((cell, found))
            reasons = uncertainty(cell)
            if reasons:
                doubtful.append({'cell': cell, 'reasons': reasons, 'rules': found})
    replaced = refine(doubtful) if refine and doubtful else {}
    return [s for cell, found in readings for s in replaced.get(cell['id'], found)]


# --------------------------------------------------------------- offline room

def offline_sessions(slots: list[dict], cbs: dict, agenda: dict | None, dates: dict[str, str]) -> tuple[list[dict], list[str]]:
    """Offline room sessions from booked slots, and what was left out.

    The workbook has no meeting in its name and the sync folder keeps the last
    meeting's copy, so a sheet whose date is not the meeting's is dropped.
    """
    warnings, kept = [], []
    for slot in slots:
        on = dates.get(slot['day'])
        if on is None or (int(on[5:7]), int(on[8:10])) != (slot['month'], slot['date']):
            warnings.append(f"{slot['day']} {slot['month']:02d}-{slot['date']:02d}")
            continue
        if not NOT_OFFLINE.match(slot['topic']):
            kept.append(slot)
    merged = []
    for slot in kept:
        last = merged[-1] if merged else None
        if (last and last['day'] == slot['day'] and last['end'] == slot['start']
                and (last['topic'], last['moderator']) == (slot['topic'], slot['moderator'])):
            last['end'] = slot['end']
        else:
            merged.append(dict(slot))
    result = []
    for n, slot in enumerate(merged):
        topic, moderator = slot['topic'], slot['moderator']
        items = AI_TAG.findall(topic)
        items += [cbs[c]['agenda_item'] for c in cb_refs(topic) if c in cbs]
        if agenda:
            items = [i for i in items if i in agenda or i.rsplit('.', 1)[0] in agenda]
        name = clean(AI_TAG.sub(' ', topic))
        chair = clean(moderator) if moderator and moderator.lower() not in PLACEHOLDERS else None
        result.append({'id': f'offline.{n}', 'day': slot['day'], 'room': 'offline', 'start': slot['start'],
                       'end': slot['end'], 'part': None, 'name': name, 'chair': chair,
                       'agenda_items': list(dict.fromkeys(items)),
                       'notes': [topic] + ([moderator] if moderator and moderator != chair else [])})
    days = sorted(set(warnings))
    return result, ([f'offline sheets not of this meeting dropped: {", ".join(days)}'] if days else [])


# --------------------------------------------------------------- schedule

def known_items(items: list[str], agenda: dict, missing: set) -> list[str]:
    """Items this meeting's agenda.csv lists; the others are reported, not shown."""
    if not agenda:
        return items
    kept = [i for i in items if i in agenda]
    missing.update(i for i in items if i not in agenda)
    return kept


def meeting_dates(metadata: dict) -> dict[str, str]:
    first, last = date.fromisoformat(metadata['starts_on']), date.fromisoformat(metadata['ends_on'])
    return {(first + timedelta(days=i)).strftime('%A'): (first + timedelta(days=i)).isoformat()
            for i in range(min((last - first).days, 6) + 1)}


def legend_notes(found: list[dict], legend: list[list[str]]) -> None:
    """A cell marked with a sign the key explains gets the key's own line: "6G Offline*" -> "* if needed".

    The key sits under the table, far from the cells. Only signs are matched
    ("*"), not words ("blue", "L, X"); a version whose key was not read adds nothing.
    """
    signs = [(key, f'{key} {meaning}') for key, meaning in legend if not any(c.isalnum() for c in key)]
    for s in found:
        written = list(s['notes'])
        s['notes'] += [line for key, line in signs if any(key in x for x in written) and line not in written]


def make_schedule(document: dict, agenda: dict | None, metadata: dict, source_files: list[str],
                  source_labels: dict[str, str], generated_at: str, *, slots=None, cbs=None, chair=None,
                  refine=None, warnings: list[str] | None = None) -> Schedule:
    """``chair(label)`` resolves an initial ("L") to a name; ``slots`` are the offline room's."""
    agenda = agenda or {}
    cbs = cbs or {}
    warnings = warnings if warnings is not None else []
    dates = meeting_dates(metadata)
    found = interpret(document, agenda, cbs, refine)
    legend_notes(found, document.get('legend') or [])
    for s in found:
        s['chair'] = (chair(s['chair_label']) if chair else None) or s['chair_label']
    offline, dropped = offline_sessions(slots or [], cbs, agenda, dates)
    warnings += dropped
    missing = set()
    days = []
    for day in document['days']:
        raw = [s for s in found + offline if s['day'] == day['day'] and s['name']]
        if not raw:
            continue
        rooms = [RoomInfo(id=i, name=n) for i, n in ROOMS if any(s['room'] == i for s in raw)]
        start = min(time_to_minutes(s['start']) for s in raw)
        start -= start % 30
        end = max(time_to_minutes(s['end']) for s in raw)
        end += -end % 5
        # On the time axis only: offlines run in the main room during coffee.
        breaks = [{'name': b['name'], 'start': b['start'], 'end': b['end'], 'label_position': 'time-axis'}
                  for b in day['breaks']
                  if start <= time_to_minutes(b['start']) and time_to_minutes(b['end']) <= end]
        built = []
        for s in raw:
            items = known_items(s['agenda_items'], agenda, missing)
            tops = [ai for ai in items if not any(is_descendant(ai, a) for a in items)]
            descriptions = [d for d in (describe(ai, agenda) for ai in tops[:6]) if d]
            built.append(Session(
                name=s['name'], start_time=s['start'], end_time=s['end'], day=day['day'],
                duration_minutes=time_to_minutes(s['end']) - time_to_minutes(s['start']),
                room_ids=[s['room']], chair=s['chair'],
                agenda_item=', '.join(written_ranges(items, s.get('ranges', ()))) or None,
                group_header=group_label(items[0] if items else None, agenda),
                description='\n'.join(d['description'] for d in descriptions) or None,
                agenda_descriptions=descriptions, notes=s['notes'], part=s['part']))
        days.append(DaySchedule(day_name=day['day'], date=dates.get(day['day']), rooms=rooms,
                                sessions=sorted(built, key=lambda s: (s.start_time, s.room_ids, s.part or [0, 0])),
                                timeline=Timeline(start=minutes_to_time(start), end=minutes_to_time(end),
                                                  breaks=breaks)))
    if missing:
        warnings.append(f'agenda items not in agenda.csv: {", ".join(sorted(missing))}')
    if not days:
        raise ValueError('The RAN3 time plan produced no sessions')
    # Said under the grid when a part of the page was read by the LLM because its layout was new.
    supplements = [{'type': 'paragraph', 'bold': True, 'text': text} for text in document.get('notices', [])]
    if document.get('legend'):
        supplements += [{'type': 'heading', 'text': 'Key to the time plan'},
                        {'type': 'table', 'rows': document['legend'], 'header': False}]
    number = document['meeting_id'].split('#', 1)[1]
    return Schedule(
        wg_id='ran3', meeting_id=document['meeting_id'], meeting_name=f'RAN3#{number}', days=days,
        source_file=source_files[0], source_files=source_files, source_labels=source_labels,
        generated_at=generated_at, timezone=metadata['timezone'], starts_on=metadata['starts_on'],
        ends_on=metadata['ends_on'], starts_at=metadata.get('starts_at'), ends_at=metadata.get('ends_at'),
        supplements=supplements)
