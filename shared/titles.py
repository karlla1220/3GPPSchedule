"""Cell titles: the source's head line with its brackets dropped (CLAUDE.md "정보 위계")."""

from __future__ import annotations

import re


def clean(text: str) -> str:
    """Display label: round brackets go whatever they say, except those glued to a
    word ("(e)RedCap"); the source line stays in the notes.

    Removing "(1)" from "Opening (1), Agenda (2)" leaves "Opening , Agenda";
    the space before the comma goes too. No word changes.
    """
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r'\s*\([^()]*\)(?![A-Za-z])', ' ', text)
    text = re.sub(r'\s*\([^()]*$', ' ', text)
    text = re.sub(r'\[\s*\]', ' ', text)
    text = re.sub(r'\s+([,;])', r'\1', ' '.join(text.split()))
    return text.strip(' ,;:/-–—&.')
