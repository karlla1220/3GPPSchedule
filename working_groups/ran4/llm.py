"""Gemini fallback for RAN4 cells the rules cannot read with confidence, and for
schedules whose slide layout the rules cannot read at all.

The rules stay the default (README "LLM 폴백", research/ran4/DECISIONS.md 15).
Only the cells they flag are asked, through ``shared.llm_cells``. An answer
is used only when it can be checked against the cell: every line is cited
once, times are the block's, the following break's or written in the cell,
pieces (part = [k, n]) follow the cell's blank lines, titles use the cited
lines' own words, and chairs and topic numbers are written there. A cell whose
answer fails keeps the rule-based reading.

The answer names topic numbers, not agenda items; the topic list turns them
into agenda items as it does for the rules. The column's chair ("Yang
Session") is not in the cell, so it is added after the answer, by the same
rule as for the rules' reading.

A file whose layout breaks the rules (a table slide with no weekday title, no
time column) is read whole from ``shared.office_outline`` blocks. That answer
cites block ids and is checked against the slides' text the same way; when it
fails, the build fails and the last page stays (``read_document``).
"""
from __future__ import annotations

from pathlib import Path
import re
import zipfile

from pydantic import BaseModel

from shared import llm_cells, office_outline
from shared.llm_cells import check_times, citation_errors, gemini_request, title_errors, written_errors
from shared.officials import join_chairs, split_chairs
from shared.schedule import MIN_PART_MINUTES, time_to_minutes
from .document import DAYS, TITLE, LayoutError, clock, file_info, meeting_id, read_core, room_info
from .sessions import (DASH_MARK, PROSE_TIME, RANGE_LINE, RANGE_PAREN, START_AT, UNTIL, Part,
                       build_session, coffee_break, end_of_meeting, resolve_overlaps)
from .sources import CACHE

PROMPTS = Path(__file__).parent / 'prompts'
VERSION = 1


class CellSession(BaseModel):
    lines: list[int]
    start: str
    end: str
    part: list[int] | None = None
    title: str
    chair: str | None = None
    topics: list[str] = []
    in_break: bool = False


class CellAnswer(BaseModel):
    id: str
    sessions: list[CellSession]


def written_times(lines: list[str]) -> set[str]:
    """Every clock written in the lines, as HH:MM: markers, ranges and prose."""
    found = set()
    for raw in lines:
        for pattern in (DASH_MARK, RANGE_LINE):
            match = pattern.match(raw)
            if match:
                groups = match.groups()
                for i in range(0, len(groups), 3):
                    if groups[i]:
                        found.add(clock(groups[i], groups[i + 1] or 0, groups[i + 2]))
        for pattern in (RANGE_PAREN, START_AT, UNTIL):
            for match in pattern.finditer(raw):
                groups = match.groups()
                for i in range(0, len(groups), 3):
                    if groups[i]:
                        found.add(clock(groups[i], groups[i + 1] or 0, groups[i + 2]))
        for match in PROSE_TIME.finditer(raw):
            text = match[0].lower().replace(' ', '')
            meridiem = 'pm' if text.endswith('pm') else 'am' if text.endswith('am') else None
            hour, _, minute = text.rstrip('apm').partition(':')
            try:
                found.add(clock(hour, minute or 0, meridiem))
            except ValueError:
                continue
    return found


def blank_groups(lines: list[str]) -> list[int]:
    """The blank-line group of each line (-1 for a blank line)."""
    groups, current = [], 0
    for i, raw in enumerate(lines):
        if raw:
            groups.append(current)
        else:
            groups.append(-1)
            if i and lines[i - 1]:
                current += 1
    return groups


def meaningful(raw: str) -> bool:
    return bool(re.search(r'[A-Za-z\[]', raw)) and not (DASH_MARK.match(raw) or RANGE_LINE.match(raw))


def check_cell(answer: CellAnswer, item: dict) -> list[str]:
    cell, held = item['cell'], item['break']
    lines = cell['lines']
    text = '\n'.join(lines)
    allowed = {cell['start'], cell['end']} | written_times(lines)
    if held:
        allowed |= {held['start'], held['end']}
    prefix = cell['id']
    if not answer.sessions:
        return [f'{prefix}: no sessions']
    errors = citation_errors(prefix, [s.lines for s in answer.sessions], lines, meaningful)
    groups = blank_groups(lines)
    blocks = {}
    for n, session in enumerate(answer.sessions):
        label = f'{prefix} session {n}'
        errors += check_times(label, session.start, session.end, allowed)
        cited = [i for i in session.lines if 0 <= i < len(lines) and lines[i]]
        source = '\n'.join(lines[i] for i in cited)
        errors += title_errors(label, session.title, source)
        if session.chair:
            errors += written_errors(label, split_chairs(session.chair), text, 'chair')
        errors += written_errors(label, session.topics, spelled_topics(source), 'topic number')
        if session.part:
            k, n_parts = (session.part + [0, 0])[:2]
            if n_parts < 2 or not 1 <= k <= n_parts:
                errors.append(f'{label}: part must be [k, n] with 1 <= k <= n and n >= 2')
            if len({groups[i] for i in cited}) != 1:
                errors.append(f'{label}: a piece cites lines across a blank line')
            blocks.setdefault((session.start, session.end), []).append((k, n_parts, groups[cited[0]] if cited else None))
    for (start, end), pieces in blocks.items():
        n_parts = pieces[0][1]
        if sorted(k for k, _, _ in pieces) != list(range(1, n_parts + 1)) or any(n != n_parts for _, n, _ in pieces):
            errors.append(f'{prefix}: pieces of {start}-{end} must be numbered 1..{n_parts}')
        elif len({g for _, _, g in pieces}) != n_parts:
            errors.append(f'{prefix}: each piece of {start}-{end} must be its own blank-line group')
        if time_to_minutes(end) - time_to_minutes(start) < n_parts * MIN_PART_MINUTES:
            errors.append(f'{prefix}: pieces of {start}-{end} would be shorter than {MIN_PART_MINUTES} min')
    timed = sorted((s.start, s.end) for s in answer.sessions if not s.part) + sorted(blocks)
    for (a_start, a_end), (b_start, _) in zip(timed, timed[1:]):
        if b_start < a_end:
            errors.append(f'{prefix}: sessions overlap at {b_start}')
    return errors


def cell_payload(item: dict) -> dict:
    cell, day, held = item['cell'], item['day'], item['break']
    room = next((r['name'] for r in day['rooms'] if r['id'] == cell['rooms'][0]), cell['rooms'][0])
    return {'id': cell['id'], 'day': day['day'], 'column': room or cell['rooms'][0],
            'block_start': cell['start'], 'block_end': cell['end'],
            'break_after': held, 'lines': [{'n': i, 'text': raw} for i, raw in enumerate(cell['lines'])],
            'reasons': item['reasons'],
            'rules': [{k: s[k] for k in ('start', 'end', 'part', 'name', 'chair', 'topics')} for s in item['rules']]}


def to_sessions(answer: CellAnswer, item: dict, context: dict) -> list[dict]:
    """The checked answer in the rules' form, so the page treats both alike."""
    cell = item['cell']
    result = []
    for session in answer.sessions:
        lines = [cell['lines'][i] for i in sorted(session.lines)]
        built = build_session(cell, Part(session.start, session.end, fixed=True), lines, context,
                         session.part, in_break=session.in_break)
        built['name'] = ' '.join(session.title.split())
        if session.chair:
            built['chair'] = join_chairs(split_chairs(session.chair))
        result.append(built)
    return result


class CellRefiner:
    """Callable for ``sessions.interpret``: uncertain cells in, checked sessions out."""

    def __init__(self, model: str, request=None, cache_dir: Path = CACHE):
        self.model, self.request, self.cache_dir = model, request, cache_dir
        self.summary = {'model': model, 'uncertain': 0, 'applied': 0, 'kept_rules': 0}

    def __call__(self, doubtful: list[dict], context: dict) -> dict[str, list[dict]]:
        for item in doubtful:
            item['break'] = coffee_break(item['cell'], item['day']['breaks'])
        items = {item['cell']['id']: item for item in doubtful}
        prompt = (PROMPTS / 'cells.md').read_text(encoding='utf-8')
        answers = llm_cells.ask(prompt, [cell_payload(item) for item in doubtful], CellAnswer,
                                lambda answer, _: check_cell(answer, items[answer.id]),
                                model=self.model, cache_dir=self.cache_dir, cache_name='cells', label='[ran4]',
                                request=self.request, version=VERSION)
        if answers.error:
            self.summary['error'] = answers.error
        self.summary.update(uncertain=len(items), applied=len(answers), kept_rules=len(items) - len(answers))
        print(f'[ran4] LLM fallback: {len(items)} uncertain cells, {len(answers)} read by {self.model}')
        return {cell_id: to_sessions(answer, items[cell_id], context) for cell_id, answer in answers.items()}


# --------------------------------------------------------------- document

class DocRoom(BaseModel):
    id: str
    name: str


class DocBreak(BaseModel):
    name: str
    start: str
    end: str


class DocDay(BaseModel):
    day: str
    rooms: list[DocRoom]
    breaks: list[DocBreak] = []


class DocSession(BaseModel):
    day: str
    start: str
    end: str
    room_ids: list[str]
    part: list[int] | None = None
    title: str
    topics: list[str] = []
    chair: str | None = None
    in_break: bool = False
    refs: list[str]
    line_numbers: list[int] | None = None


class DocResult(BaseModel):
    days: list[DocDay]
    sessions: list[DocSession]


def squash(text: str) -> str:
    return ' '.join(text.split())


def written_numbers(session: DocSession, texts: dict[str, str]) -> list[int]:
    """A session's line numbers without blank lines: a blank between its lines says nothing."""
    lines = texts[session.refs[0]].split('\n')
    return sorted(i for i in session.line_numbers if 0 <= i < len(lines) and lines[i])


def spelled_topics(text: str) -> str:
    """The text with "102-A/B" also written as 102-A and 102-B, the numbers it stands for."""
    return re.sub(r'\b(\d{3})-([A-Z])/([A-Z])\b', r'\g<0> \1-\2 \1-\3', text)


def without_times(lines: list[str]) -> list[str]:
    """The lines without those that only mark a time, which the rules take out as they split a cell."""
    lines = [squash(line) for line in lines]
    return [line for line in lines
            if not (DASH_MARK.match(line) or RANGE_LINE.match(line) or not START_AT.sub('', line).strip())]


def cited_lines(session: DocSession, texts: dict[str, str]) -> list[str]:
    """The lines a session cites: chosen lines of one cell, or whole cells."""
    if session.line_numbers is not None:
        lines = texts[session.refs[0]].split('\n')
        return [lines[i] for i in written_numbers(session, texts)]
    return [line for ref in session.refs for line in texts[ref].split('\n')]


def _minutes(value: str) -> int:
    return 24 * 60 if value == 'EOM' else time_to_minutes(value)


def check_document(result: DocResult, texts: dict[str, str]) -> list[str]:
    """Problems of a whole-document answer against the slides' text.

    Times, room and break names, titles, topic numbers and chairs are written
    in the source; a session cites the cells it comes from, and cells shared
    by several sessions are split by line numbers; pieces (part = [k, n]) are
    blank-line groups of one cell; sessions in one room do not overlap.
    """
    errors = []
    source = '\n'.join(texts.values())
    written = squash(source).lower()
    allowed = written_times(source.split('\n'))
    eom = bool(re.search(r'\bEOM\b', source))
    days = {d.day: d for d in result.days}
    if not days or any(d not in DAYS for d in days) or len(days) != len(result.days):
        errors.append(f'days must be distinct weekdays: {[d.day for d in result.days]}')
    for day in result.days:
        ids = [r.id for r in day.rooms]
        if not ids or len(set(ids)) != len(ids) or any(not i.strip() for i in ids):
            errors.append(f'{day.day}: rooms need unique, non-empty ids')
        errors += [f'{day.day}: room name {r.name!r} is not written in the document'
                   for r in day.rooms if squash(r.name).lower() not in written]
        for b in day.breaks:
            errors += check_times(f'{day.day} break {b.name!r}', b.start, b.end, allowed)
            if not b.name.strip() or squash(b.name).lower() not in written:
                errors.append(f'{day.day}: break name {b.name!r} is not written in the document')
    if not result.sessions:
        errors.append('no sessions found')
    cited_by, blocks, placed = {}, {}, []
    for n, s in enumerate(result.sessions):
        prefix = f'session {n}'
        own = []
        day = days.get(s.day)
        if day is None:
            own.append(f'{prefix}: day {s.day!r} is not listed in days')
        end = '23:55' if s.end == 'EOM' and eom else s.end   # "14:00-EOM": the meeting's end
        own += check_times(prefix, s.start, end, allowed | {'23:55'} if end != s.end else allowed)
        if day and (not s.room_ids or not set(s.room_ids) <= {r.id for r in day.rooms}):
            own.append(f'{prefix}: room_ids must name rooms listed for {s.day}')
        if not s.refs or not set(s.refs) <= texts.keys():
            own.append(f'{prefix}: refs must cite block ids')
        elif s.line_numbers is not None:
            lines = texts[s.refs[0]].split('\n')
            if len(s.refs) != 1:
                own.append(f'{prefix}: line_numbers need exactly one ref')
            elif any(not 0 <= i < len(lines) for i in s.line_numbers) or not written_numbers(s, texts):
                own.append(f'{prefix}: line_numbers must be lines of {s.refs[0]}, not only blank ones')
        if own:
            errors += own
            continue
        for ref in s.refs:
            cited_by.setdefault(ref, []).append(n)
        text = '\n'.join(cited_lines(s, texts))
        errors += title_errors(prefix, s.title, text)
        errors += written_errors(prefix, s.topics, spelled_topics(text), 'topic number')
        errors += written_errors(prefix, split_chairs(s.chair), text, 'chair')
        if s.in_break and not any((b.start, b.end) == (s.start, s.end) for b in day.breaks):
            errors.append(f'{prefix}: a session held in a break takes that break\'s times')
        key = (s.day, s.start, s.end, tuple(sorted(s.room_ids)))
        if s.part is not None:
            k, n_parts = (list(s.part) + [0, 0])[:2]
            if len(s.part) != 2 or not 1 <= k <= n_parts or n_parts < 2:
                errors.append(f'{prefix}: part must be [k, n] with 1 <= k <= n and n >= 2')
                continue
            blocks.setdefault(key, []).append(n)
        else:
            placed.append((key, [n]))
    for ref, numbers in cited_by.items():
        chosen = [None if result.sessions[i].line_numbers is None else written_numbers(result.sessions[i], texts)
                  for i in numbers]
        if len(numbers) > 1 and (None in chosen or len(sum(chosen, [])) != len(set(sum(chosen, [])))):
            errors.append(f'sessions {numbers} share {ref}: each must give its own line_numbers')
    for key, numbers in blocks.items():
        pieces = sorted((result.sessions[i] for i in numbers), key=lambda s: s.part[0])
        n_parts = pieces[0].part[1]
        label = f'pieces of {key[0]} {key[1]}-{key[2]}'
        if [p.part for p in pieces] != [[k, n_parts] for k in range(1, n_parts + 1)]:
            errors.append(f'{label} must be numbered 1..{n_parts}')
        elif _minutes(key[2]) - _minutes(key[1]) < n_parts * MIN_PART_MINUTES:
            errors.append(f'{label} would be shorter than {MIN_PART_MINUTES} min')
        elif len({tuple(p.refs) for p in pieces}) != 1 or len(pieces[0].refs) != 1 or \
                any(p.line_numbers is None for p in pieces):
            errors.append(f'{label} are blank-line groups of one cell: cite it with line_numbers')
        else:
            lines = texts[pieces[0].refs[0]].split('\n')
            for a, b in zip(pieces, pieces[1:]):
                gap = range(max(written_numbers(a, texts)) + 1, min(written_numbers(b, texts)))
                if not any(not lines[j] for j in gap):
                    errors.append(f'{label}: the source does not split pieces {a.part[0]} and {b.part[0]} '
                                  'with a blank line')
        placed.append((key, numbers))
    for i, (a, _) in enumerate(placed):
        for b, _ in placed[i + 1:]:
            if a[0] == b[0] and set(a[3]) & set(b[3]) and _minutes(a[1]) < _minutes(b[2]) \
                    and _minutes(b[1]) < _minutes(a[2]):
                errors.append(f'{a[0]}: sessions at {a[1]} and {b[1]} overlap in the same room')
    return errors


class DocumentReading:
    """The checked answer as ``make_schedule`` reads sessions: through the same
    ``build_session`` as the rules, so the column's chair, topic numbers to
    agenda items and candidate lists follow the same rules."""

    def __init__(self, result: DocResult, texts: dict[str, str], ids: dict[str, dict[str, str]]):
        self.result, self.texts, self.ids = result, texts, ids

    def __call__(self, context: dict, metadata: dict) -> tuple[list[dict], list[str]]:
        found, warnings = {}, []
        for n, s in enumerate(self.result.sessions):
            end = s.end
            if end == 'EOM':
                end, warning = end_of_meeting(metadata, s.start)
                warnings += [warning] if warning else []
            cell = {'id': f'doc{n}', 'rooms': [self.ids[s.day][r] for r in s.room_ids]}
            built = build_session(cell, Part(s.start, end, fixed=True), without_times(cited_lines(s, self.texts)), context,
                                  s.part, in_break=s.in_break)
            if not built['named']:
                built['name'] = ' '.join(s.title.split())
            if s.chair:
                built['chair'] = join_chairs(split_chairs(s.chair))
            warnings += built['warnings']
            found.setdefault(s.day, []).append({**built, 'day': s.day})
        sessions = [s for day in DAYS for s in resolve_overlaps(found.get(day, []))]
        return sessions, list(dict.fromkeys(warnings))


def slide_texts(blocks: list[dict]) -> list[dict]:
    """The text outside tables, slide by slide, for the page's notes."""
    out = []
    for slide in blocks:
        paragraphs = [shape['text'] for shape in slide['shapes'] if 'rows' not in shape and shape.get('text', '').strip()]
        if paragraphs:
            out.append({'type': 'heading', 'text': f'Slide {slide["id"][1:]}'})
            out += [{'type': 'paragraph', 'text': text, 'bold': False} for text in paragraphs]
    return out


def read_document(data: bytes, name: str, model: str, request=None, cache_dir: Path = CACHE):
    """(document, reader) for ``make_schedule`` when the slides' layout is not the usual one."""
    try:
        blocks = office_outline.blocks(data, name)
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise LayoutError(f'{name} cannot be outlined for the LLM: {exc}') from exc
    texts = {key: text for key, text in office_outline.texts(blocks).items() if text.strip()}
    info = file_info(name)
    titled = next((TITLE.search(t) for t in texts.values() if TITLE.search(t)), None)
    meeting = info[0] if info else meeting_id(titled[1], titled[2]) if titled else None
    if meeting is None:
        raise ValueError('Cannot tell the RAN4 meeting from the file name or the slides')
    prompt = (PROMPTS / 'document.md').read_text(encoding='utf-8')
    try:
        result = llm_cells.read_document(prompt, blocks, DocResult, lambda r: check_document(r, texts), model=model,
                                         cache_dir=cache_dir, wg_id='ran4', source=name, meeting=meeting,
                                         request=request or gemini_request, version=VERSION)
    except ValueError as exc:
        raise LayoutError(f'The LLM could not read the schedule either: {exc}') from exc
    print(f'[ran4] Slide layout not recognised; {len(result.sessions)} sessions read by {model}')
    days, ids = [], {}
    for day in sorted(result.days, key=lambda d: DAYS.index(d.day)):
        rooms, ids[day.day] = [], {}
        for index, room in enumerate(day.rooms, 1):
            info_ = room_info(room.name, index)
            ident = info_['id'] if info_['id'] not in ids[day.day].values() else room.id
            ids[day.day][room.id] = ident
            rooms.append({**info_, 'id': ident})
        days.append({'day': day.day, 'rooms': rooms, 'cells': [],
                     'breaks': [{'name': squash(b.name), 'start': b.start, 'end': b.end} for b in day.breaks]})
    notice = {'type': 'paragraph', 'bold': True,
              'text': 'The schedule slides were not in their usual layout, so the sessions above were read by an LLM. '
                      'The text outside the tables follows for reference.'}
    document = {'meeting_id': meeting, 'title': '', 'cover': [], 'label': info[1] if info else '',
                **read_core(data), 'days': days, 'supplements': [notice, *slide_texts(blocks)]}
    return document, DocumentReading(result, texts, ids)
