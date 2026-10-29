"""Gemini fallback for RAN3 time plan cells the rules cannot read with confidence.

The rules stay the default. A cell is asked about only when it has a line of
no known kind (``sessions.uncertainty``): "All CBs in AI8, AI9, AI31 will be
treated" under "Early CBs" (#129), "End of Year Social" in a CBs cell (#134).
An answer is a cell's pieces in order. It is used only when it can be checked
against the text: every line is cited once, titles use the cited lines' own
words, the chair is an initial written in the cell, agenda items are written
there (a range "1-8" covers 2 to 7) and listed in agenda.csv, and the pieces
are at least 30 minutes. The times are always the cell's: the time plan writes
no others. Calls, retries and the cache are shared (``shared/llm_cells.py``).
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from shared import llm_cells
from shared.llm_cells import citation_errors, gemini_request, has_key, title_errors, written_errors
from shared.schedule import MIN_PART_MINUTES, time_to_minutes

from .sessions import _span, line_items, session_of
from .sources import CACHE

PROMPTS = Path(__file__).parent / 'prompts'
VERSION = 1


class Piece(BaseModel):
    title: str
    chair: str | None
    agenda_items: list[str]
    lines: list[int]


class CellAnswer(BaseModel):
    id: str
    pieces: list[Piece]


def cell_payload(item: dict) -> dict:
    cell = item['cell']
    return {'id': cell['id'], 'day': cell['day'], 'cell_start': cell['start'], 'cell_end': cell['end'],
            'lines': [{'n': i, 'text': raw} for i, raw in enumerate(cell['lines'])],
            'reasons': item['reasons'],
            'rules': [{'title': s['name'], 'chair': s['chair_label'], 'agenda_items': s['agenda_items']}
                      for s in item['rules']]}


def check_cell(answer: CellAnswer, cell: dict, agenda: dict | None) -> list[str]:
    lines = cell['lines']
    errors = citation_errors(cell['id'], [p.lines for p in answer.pieces], lines)
    minutes = time_to_minutes(cell['end']) - time_to_minutes(cell['start'])
    outer = cell['part'][1] if cell['part'] else 1
    if not answer.pieces:
        errors.append(f'{cell["id"]}: no pieces')
    elif minutes < outer * len(answer.pieces) * MIN_PART_MINUTES:
        errors.append(f'{cell["id"]}: {len(answer.pieces)} pieces of {minutes // outer} min are under '
                      f'{MIN_PART_MINUTES} min; answer one piece')
    for n, piece in enumerate(answer.pieces):
        prefix = f'{cell["id"]} piece {n}'
        cited = [lines[i] for i in piece.lines if 0 <= i < len(lines)]
        source = '\n'.join(cited)
        errors += title_errors(prefix, piece.title, source)
        if piece.chair:
            initials = {x for x in lines if len(x) == 1 and x.isupper()}
            if piece.chair not in initials:
                errors.append(f'{prefix}: chair {piece.chair!r} is not an initial written in the cell')
        expanded = {i for x in cited for i in line_items(x, agenda)}
        errors += written_errors(prefix, [i for i in piece.agenda_items if i not in expanded], source, 'agenda item')
        if agenda:
            errors += [f'{prefix}: agenda item {i!r} is not in this meeting\'s agenda'
                       for i in piece.agenda_items if i not in agenda]
    return errors


def to_sessions(answer: CellAnswer, cell: dict, agenda: dict | None, cbs: dict) -> list[dict]:
    n = len(answer.pieces)
    return [session_of(cell, [cell['lines'][i] for i in sorted(piece.lines)], piece.chair, _span(cell, k, n),
                       agenda, cbs, struck=cell['struck'] if k == 1 else [], title=' '.join(piece.title.split()),
                       items=list(dict.fromkeys(piece.agenda_items)))
            for k, piece in enumerate(answer.pieces, 1)]


class CellRefiner:
    """Callable for ``sessions.interpret``: uncertain cells in, validated sessions out."""

    def __init__(self, model: str, request=None, *, agenda: dict | None = None, cbs: dict | None = None,
                 cache_dir: Path = CACHE):
        self.model, self.request, self.cache_dir = model, request or gemini_request, cache_dir
        self.agenda, self.cbs = agenda or {}, cbs or {}
        self.summary = {'model': model, 'uncertain': 0, 'applied': 0, 'kept_rules': 0}

    def __call__(self, doubtful: list[dict]) -> dict[str, list[dict]]:
        cells = {item['cell']['id']: item['cell'] for item in doubtful}
        prompt = (PROMPTS / 'cells.md').read_text(encoding='utf-8')
        answers = llm_cells.ask(prompt, [cell_payload(item) for item in doubtful], CellAnswer,
                                lambda answer, _: check_cell(answer, cells[answer.id], self.agenda),
                                model=self.model, cache_dir=self.cache_dir, cache_name='cells', label='[ran3]',
                                request=self.request, version=VERSION)
        if answers.error:
            self.summary['error'] = answers.error
        self.summary.update(uncertain=len(cells), applied=len(answers), kept_rules=len(cells) - len(answers))
        print(f'[ran3] LLM fallback: {len(cells)} uncertain cells, {len(answers)} read by {self.model}')
        return {cell_id: to_sessions(answer, cells[cell_id], self.agenda, self.cbs)
                for cell_id, answer in answers.items()}
