"""Which meeting a WG's page shows, and which meetings it reads.

A page stays on its meeting until that meeting has ended by the venue's
calendar day; a preliminary schedule of the next one waits until then. Only
then does the page move, to the earliest meeting that has not ended and has a
schedule. With none, the page stays where it is.

Which folders a run reads is a separate question: the built meeting and the
next one, whatever the date (``to_read``). A file being found never moves a
page by itself.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import NamedTuple

from shared.meeting_folders import portal_key
from shared.navigation import meeting_phase
from shared.portal_meetings import timezone_reference

# Where a date ends last: a venue not known yet cannot end a meeting early.
ANYWHERE_ON_EARTH = 'Etc/GMT+12'


class Dates(NamedTuple):
    starts_on: str
    ends_on: str
    timezone: str = ANYWHERE_ON_EARTH


def right_now() -> datetime:
    return datetime.now(timezone.utc)


def known_dates(rows, previous: dict, configured: dict | None = None) -> dict[str, Dates]:
    """Dates per meeting: configured, then Portal, then the last build."""
    dates = {}
    saved = previous.get('metadata') or {}
    if previous.get('meeting_id') and saved.get('starts_on') and saved.get('ends_on'):
        dates[previous['meeting_id']] = Dates(saved['starts_on'], saved['ends_on'],
                                              saved.get('timezone') or ANYWHERE_ON_EARTH)

    def add(key, starts_on, ends_on, zone):
        known = dates[key].timezone if key in dates else ANYWHERE_ON_EARTH
        dates[key] = Dates(starts_on, ends_on, zone or known)

    for row in rows or []:
        key = portal_key(row)
        if key and row.get('StartDate') and row.get('EndDate'):
            add(key, row['StartDate'][:10], row['EndDate'][:10], (timezone_reference(row) or {}).get('timezone'))
    for key, value in (configured or {}).items():
        if value.get('starts_on') and value.get('ends_on'):
            add(key, value['starts_on'], value['ends_on'], value.get('timezone'))
    return dates


def _phase(meeting, dates, now):
    return meeting_phase(*Dates(*dates[meeting]), now) if meeting in dates else None


def holds(meeting, dates, now) -> bool:
    """Whether the meeting is known not to have ended."""
    return _phase(meeting, dates, now) in ('Upcoming', 'In progress')


def has_started(meeting, dates, now) -> bool:
    """Whether the meeting is known to have reached its first day.

    The sync area keeps the last meeting's files until it is copied afresh,
    the day before the next one: a file there that does not name its meeting
    (agenda.csv) can be taken for this meeting's only from then on.
    """
    return _phase(meeting, dates, now) in ('In progress', 'Ended')


def in_progress(meeting, dates, now) -> bool:
    return _phase(meeting, dates, now) == 'In progress'


def going(dates, now) -> list[str]:
    """Meetings that have not ended, earliest first."""
    return [meeting for _, meeting in sorted((Dates(*dates[m]).starts_on, m) for m in dates if holds(m, dates, now))]


def current_meeting(built, scheduled, dates, now, rank) -> str:
    """The meeting to show, given the meetings that have a schedule.

    Without any date for the built meeting there is nothing to wait for, and
    the highest meeting that has a schedule stands. It can be the built meeting
    with no file listed: the caller then keeps its page instead of moving on
    or stepping back.
    """
    if built and holds(built, dates, now):
        return built
    open_ones = [meeting for meeting in going(dates, now) if meeting in scheduled]
    return open_ones[0] if open_ones else max({*scheduled, *([built] if built else [])}, key=rank)


def to_read(built, dates, now) -> list[str]:
    """The meetings whose folders a run reads: the built one and the next."""
    upcoming = going(dates, now)
    if built is None:
        ended = sorted((Dates(*dates[m]).ends_on, m) for m in dates if m not in upcoming)
        built = ended[-1][1] if ended else next(iter(upcoming), None)
    start = Dates(*dates[built]).starts_on if built in dates else ''
    following = next((m for m in upcoming if m != built and Dates(*dates[m]).starts_on >= start), None)
    return [meeting for meeting in (built, following) if meeting]
