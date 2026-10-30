"""Gemini fallback for RAN4 cells the rules cannot read with confidence.

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
"""
from __future__ import annotations

from pathlib import Path
import re

from pydantic import BaseModel

from shared import llm_cells
from shared.llm_cells import check_times, citation_errors, title_errors, written_errors
from shared.schedule import MIN_PART_MINUTES, time_to_minutes
from .document import clock
from .sessions import (DASH_MARK, PROSE_TIME, RANGE_LINE, RANGE_PAREN, START_AT, UNTIL, Part,
                       build_session, coffee_break)
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
            errors += written_errors(label, [c.strip() for c in session.chair.split(',') if c.strip()], text, 'chair')
        errors += written_errors(label, session.topics, source, 'topic number')
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
            built['chair'] = session.chair.strip()
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
