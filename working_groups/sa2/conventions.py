"""SA2 Session Plan conventions: what the chair's plans have always written.

The rules read what a plan writes. Where a plan leaves something unclear, the
convention fills in only that part, and only with a value that agrees with
everything the plan does write: "1100 - 12" is Q2, and every plan since #161
has Q2 at 11:00-12:30, so it is 11:00-12:30. A part no convention value fits
is not guessed. The values are in conventions.json and the history behind
them in CONVENTIONS.md.

Each use is described in a sentence (the document's ``filled``), which the
page shows below the grid and the build reports to the repository owner.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

VALUES = json.loads((Path(__file__).parent / 'conventions.json').read_text(encoding='utf-8'))
# A time as the plans write it, "1100", or an hour alone, "12".
PART = re.compile(r'(\d{2})(\d{2})?')
# A range in a plan's text with a part missing its minutes: "1100 - 12".
INCOMPLETE = re.compile(r'(?<![\d.])(\d{2})(\d{2})?\s*-\s*(\d{2})(\d{2})?(?![\d.])')


def usual_quarters(q: int) -> list[tuple[str, str]]:
    return [tuple(pair) for pair in VALUES['quarters'].get(str(q), [])]


def _fits(written: tuple[str, str | None], usual: str) -> bool:
    hour, minute = written
    return f'{int(hour):02d}' == usual[:2] and (minute is None or minute == usual[3:])


def quarter_times(q: int, start: tuple[str, str | None], end: tuple[str, str | None]) -> tuple[str, str] | None:
    """A quarter's (start, end) from its written parts, as (hour, minutes or None).

    Written in full, it is as written. With minutes missing, it is the first
    usual time of that quarter that agrees with every written part, or None.
    """
    if start[1] is not None and end[1] is not None:
        return f'{int(start[0]):02d}:{start[1]}', f'{int(end[0]):02d}:{end[1]}'
    return next(((a, b) for a, b in usual_quarters(q) if _fits(start, a) and _fits(end, b)), None)


def completed_times(text: str) -> set[str]:
    """Times the quarter convention gives a range written with a part missing ("1100 - 12": 11:00, 12:30).

    For a reader that does not know the quarter (the LLM answer check): a
    range counts when exactly one usual quarter time fits it.
    """
    found = set()
    for match in INCOMPLETE.finditer(text):
        start, end = (match[1], match[2]), (match[3], match[4])
        if start[1] is not None and end[1] is not None:
            continue
        fits = {(a, b) for q in VALUES['quarters'] for a, b in usual_quarters(int(q))
                if _fits(start, a) and _fits(end, b)}
        if len(fits) == 1:
            found.update(fits.pop())
    return found


def stream_colour(stream: int) -> str | None:
    return VALUES['stream_colours'].get(str(stream))


def convener_colour(number: str) -> str | None:
    return VALUES['convener_colours'].get(str(int(number)))


def prompt_note() -> str:
    """The quarter convention for the whole-document LLM prompt."""
    usual = ', '.join(f'Q{q} ' + ' or '.join(f'{a}-{b}' for a, b in usual_quarters(int(q))) for q in VALUES['quarters'])
    return ('\n- A quarter time written with a part missing ("1100 - 12") takes the usual time of that quarter that '
            f'fits what is written ("1100 - 12" is 11:00-12:30). Usual quarter times: {usual}.\n')
