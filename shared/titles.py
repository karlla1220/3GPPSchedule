"""Cell titles: the source's head line, without the brackets shown elsewhere (CLAUDE.md "정보 위계")."""

from __future__ import annotations

import re

# A longer bracket is a sentence, not part of a label. It stays in the notes.
LONGEST_BRACKET = 24

# Numbers and clocks with no word of their own: "(40)", "(20.6.18)", "(AI 8.1, 8.2)",
# "(5.x, 6.x)", "(1330 - 1630)", "(until 11:00)". The AI and time lines show those.
_NUMBERS = re.compile(r'(?:\bAI(?![A-Za-z])|\band\b|\buntil\b|\d+(?:[.:]\d+)*(?:\.x)?|[\s,;/&+#–-])*', re.I)
_BRACKET = re.compile(r'\s*\(([^()]*)\)(?![A-Za-z])')
_OPEN_BRACKET = re.compile(r'\s*\(([^()]*)$')
_OPEN, _CLOSE = '\x01', '\x02'     # a bracket already kept, hidden from the pattern above


def _tidy(text: str) -> str:
    text = re.sub(r'\[\s*\]', ' ', text)
    text = re.sub(r'\[(?=[^\]]*$)', '', text)       # never closed: "[NTN (cont)"
    text = re.sub(r'\s+([,;])', r'\1', ' '.join(text.split()))
    return text.strip(' ,;:/-–—&.')


def _keeps(bracket: str) -> bool:
    return len(bracket) <= LONGEST_BRACKET and not _NUMBERS.fullmatch(bracket)


def clean(text: str) -> str:
    """Display label: a bracket goes when something else shows it or it is too long.

    "KI#18 (AI for Network) (20.6.18)" -> "KI#18 (AI for Network)". Numbers and
    clocks go by their shape, whatever the WG. A name that became the chair is
    taken out by the WG, which knows its chairs, before this runs. A bracket
    glued to a word ("(e)RedCap") is part of the word.

    Removing "(1)" from "Opening (1), Agenda (2)" leaves "Opening , Agenda";
    the space before the comma goes too. No word changes.
    """
    def judged(match):
        inner = match[1].strip()
        return f' {_OPEN}{inner}{_CLOSE}' if _keeps(inner) else ' '

    previous = None
    while previous != text:
        previous = text
        text = _BRACKET.sub(judged, text)
    text = _OPEN_BRACKET.sub(judged, text).replace(_OPEN, '(').replace(_CLOSE, ')')
    return _tidy(re.sub(r'\(\(([^()]*)\)\)', r'(\1)', text))      # "((if time allows))"


def unbracketed(text: str) -> str:
    """The words outside any bracket: for names and for telling lines apart, not for titles."""
    previous = None
    while previous != text:
        previous = text
        text = _BRACKET.sub(' ', text)
    return _tidy(_OPEN_BRACKET.sub(' ', text))
