"""WG officials (chair, vice chairs) from the 3GPP officials history, by date.

Shared reading only. What a WG writes for its chairs, and how that maps to an
official, is WG configuration: its officials code (``R3``, ``S2``) and an
alias table from the schedule's own labels (RAN3 writes ``L`` for Lixiang XU).

The history page lists every term with its election date and end, so one page
answers both today's officials and those of an earlier meeting. A term starts
on its election date, which 3GPP sets to the day after the electing meeting;
on that meeting's days the previous officials are still in office.

A chair is shown only when the schedule writes one. This module resolves what
was written; it never supplies a chair for a cell that names nobody.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re

from bs4 import BeautifulSoup

from shared import remote_files

URL = 'https://www.3gpp.org/dynareport?code=TSG-WG--{code}--officialsHistory.htm'
POSITIONS = ('Chair', 'Vice Chair')


@dataclass(frozen=True)
class Official:
    position: str  # "Chair" or "Vice Chair"
    family: str  # as 3GPP writes it: "XU"
    given: str  # "Lixiang"
    organization: str
    elected: date
    ended: date | None  # None while in office

    @property
    def name(self) -> str:
        return full_name(self.given, self.family)

    def serves_on(self, day: date) -> bool:
        return self.elected <= day and (self.ended is None or day <= self.ended)


def full_name(given: str, family: str) -> str:
    """"Given FAMILY". A given name 3GPP wrote in capitals ("WEI, QUN") is
    capitalised as a name ("Qun WEI"), so the family part stays the one in capitals."""
    if given.isupper():
        given = re.sub(r"[A-Z][A-Z']*", lambda m: m[0].capitalize(), given)
    return f'{given} {family}'.strip()


def _date(text: str) -> date | None:
    return date.fromisoformat(text) if re.fullmatch(r'\d{4}-\d{2}-\d{2}', text) else None


def parse_history(html: str) -> list[Official]:
    """Chairs and vice chairs of every term on an officials history page.

    A wrong code still answers 200 with empty tables, so a page without a
    single chair is an error, not a WG without officials.
    """
    officials = []
    for table in BeautifulSoup(html, 'html.parser').select('table.dsptab'):
        head = table.find('th')
        position = head.get_text(' ', strip=True) if head else ''
        if position not in POSITIONS:
            continue
        for row in table.find_all('tr'):
            cells = [td.get_text(' ', strip=True) for td in row.find_all('td')]
            if len(cells) < 5 or _date(cells[1]) is None:
                continue
            family, _, given = cells[3].partition(',')
            officials.append(Official(position, ' '.join(family.split()), ' '.join(given.split()),
                                      ' '.join(cells[4].split()), _date(cells[1]), _date(cells[2])))
    if not any(o.position == 'Chair' for o in officials):
        raise ValueError('No chair on the officials history page')
    return officials


def fetch_history(code: str, http=None) -> list[Official]:
    """The officials history of one WG (``R3``, ``S2``). Raises on failure."""
    if http is None:
        with remote_files.client(timeout=30) as client:
            return fetch_history(code, client)
    response = http.get(URL.format(code=code))
    response.raise_for_status()
    return parse_history(response.text)


def in_office(officials: list[Official], day: date) -> list[Official]:
    """Who held office on day, chair first."""
    return sorted((o for o in officials if o.serves_on(day)), key=lambda o: POSITIONS.index(o.position))


def _key(text: str) -> str:
    return ' '.join(text.replace(',', ' ').split()).casefold()


def resolve(label: str | None, officials: list[Official], aliases: dict[str, str] | None = None) -> str | None:
    """The full name for a chair label the schedule wrote, or None.

    officials are those in office at the meeting (``in_office``). A label
    matches an official by name in any order ("XU Lixiang", "Lixiang XU",
    "XU, Lixiang") or by one name (given or family) no other official has. Otherwise the WG's aliases map the
    label to a name: an official's ("L": "XU, Lixiang") or a session chair's
    who is not one ("G": "CAO, Gen"). An unknown label gives None; the caller
    shows it as written rather than guessing.
    """
    if not label or not label.strip():
        return None
    wanted = _key(label)
    for official in officials:
        if wanted in {_key(f'{official.family} {official.given}'), _key(official.name)}:
            return official.name
    # One name alone only when it fits one official, given or family
    # (RAN4 "Yang" could be TANG Yang or YANG Shan).
    by_part = {o.name for o in officials if wanted in {_key(o.given), _key(o.family)}}
    if len(by_part) == 1:
        return by_part.pop()
    alias = (aliases or {}).get(label.strip())
    if alias is None:
        return None
    family, _, given = alias.partition(',')
    return resolve(f'{family} {given}', officials) or full_name(given.strip(), family.strip())
