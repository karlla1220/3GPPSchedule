"""Public Portal metadata shared by schedule builds and site selection.

Portal timezone labels are display names, not IANA identifiers; shared.timezones
resolves them. By default a row resolves without asking (labels and the venue
list); builds pass ``timezones.resolve`` to let an unknown venue be asked once.
"""
from datetime import datetime, timedelta
from functools import lru_cache
import re
from zoneinfo import ZoneInfo

import httpx

from shared import remote_files, timezones

URL = 'https://portal.3gpp.org/webservices/Rest/Meetings.svc/GetMeetings'
TB_IDS = {'ran-plenary': 373, 'ran1': 379, 'ran2': 380, 'ran3': 381, 'ran4': 382, 'sa1': 384, 'sa2': 385}
HEADERS = {
    'Accept': 'application/json, text/plain, */*',
    'Origin': 'https://portal.3gpp.org', 'Referer': 'https://portal.3gpp.org/',
    'User-Agent': 'Mozilla/5.0 (3gppschedule) PortalMeetingsClient/1.0',
}
def meeting_key(value):
    """Match complete regular meeting identities, never social-event titles.

    RAN and SA alike: "3GPPRAN2#135-bis" -> ran2#135bis, "3GPPSA2#177" -> sa2#177.
    Ad hoc and e-meeting titles ("3GPPSA2#178-AH-e", "3GPPSA1#112-Ad Hoc-e")
    are not regular meetings and give None, as RAN e-meetings always have.
    """
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'(?:3GPP)?(RAN|SA)([1-6]?)#(\d+)(?:[ -]?(bis|b|ter))?', value.strip(), re.I)
    if not match:
        return None
    tsg, wg, number, suffix = match.groups()
    if tsg.upper() == 'RAN' and wg not in ('', '1', '2', '3', '4'):
        return None
    suffix = (suffix or '').lower()
    return f'{tsg.lower()}{wg}#{number}{"bis" if suffix == "b" else suffix}'


def fetch_meeting_rows(client, payload, *, max_pages=20):
    """Shared bounded Portal pagination for all WGs and site navigation."""
    rows = []
    page_size = payload.get('ResultsPerPage', 100)
    for _ in range(max_pages):
        query = {**payload, 'StartRow': len(rows), 'ResultsPerPage': page_size}
        response = client.post(URL, json={'getMeetingsInput': query}, headers=HEADERS)
        response.raise_for_status()
        page = response.json()
        if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
            raise ValueError('Unexpected Portal meetings response')
        if page and rows[-len(page):] == page:
            raise ValueError('Portal repeated a page')
        rows.extend(page)
        if len(page) < page_size:
            return rows
    raise ValueError('Portal meetings page limit reached')


@lru_cache(maxsize=1)
def _fetch_meetings(today):
    with remote_files.client(headers=HEADERS, timeout=30) as client:
        return fetch_meeting_rows(client, {
            'SortBy': 'Date', 'SortAscending': False,
            'StartDate': f'{today - timedelta(days=366)} 00:00:00',
            'EndDate': f'{today + timedelta(days=183)} 23:59:59',
            'Tbs': list(TB_IDS.values()), 'IncludeChildTbs': False,
            'IncludeNonTBMeetings': False, 'Reference': '', 'Registered': False})


_given = None


def get_meetings():
    """One shared fetch per process/day; failures remain errors to callers."""
    if _given is not None:
        return _given
    return _fetch_meetings(datetime.now(ZoneInfo('Asia/Seoul')).date())


def use_meetings(rows):
    """Answer get_meetings() with rows this run already fetched (check -> build)."""
    global _given
    _given = list(rows)


def clear_meetings_cache():
    """Start a fresh check when several CI runs share one Python process."""
    global _given
    _given = None
    _fetch_meetings.cache_clear()


def find_meeting(rows, wg_id, identity):
    key = meeting_key(identity)
    if key is None or wg_id not in TB_IDS:
        return None
    matches = {row.get('Id'): row for row in rows
               if row.get('TBId') == TB_IDS[wg_id]
               and meeting_key(row.get('Title')) == key
               and type(row.get('Id')) is int}
    if len(matches) != 1:
        return None
    return next(iter(matches.values()))


def get_meeting(wg_id, identity):
    if meeting_key(identity) is None:
        return None
    return find_meeting(get_meetings(), wg_id, identity)


def timezone_reference(row, resolve=timezones.known):
    if row is None:
        return None
    try:
        on = datetime.fromisoformat(row['StartDate']).date()
    except (KeyError, TypeError, ValueError):
        return None
    venue = {'country': row.get('Country'), 'location': row.get('Location')}
    start = resolve(row.get('StartTimeZone'), on=on, **venue)
    end = resolve(row.get('EndTimeZone'), on=on, **venue) if row.get('EndTimeZone') else start
    if not start or start != end:
        return None
    try:
        starts = datetime.fromisoformat(row['StartDate'])
        ends = datetime.fromisoformat(row['EndDate'])
        zone = ZoneInfo(start)
        starts = starts.replace(tzinfo=zone) if starts.tzinfo is None else starts.astimezone(zone)
        ends = ends.replace(tzinfo=zone) if ends.tzinfo is None else ends.astimezone(zone)
        if ends.timestamp() < starts.timestamp():
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return {'type': 'portal', 'id': row['Id'], 'timezone': start,
            'starts_on': starts.date().isoformat(), 'ends_on': ends.date().isoformat(),
            'starts_at': starts.isoformat(), 'ends_at': ends.isoformat(),
            'start_timezone': row.get('StartTimeZone'), 'end_timezone': row.get('EndTimeZone'),
            'location': row.get('Location'), 'country': row.get('Country')}


def lookup_timezone_reference(wg_id, identity, resolve=timezones.known):
    try:
        row = get_meeting(wg_id, identity)
        ref = timezone_reference(row, resolve)
        if row is not None and ref is None:
            print(f'Warning: Portal timezone unresolved for {identity}: {row.get("StartTimeZone")!r}')
        return ref
    except (httpx.HTTPError, ValueError) as exc:
        print(f'Warning: Portal metadata lookup failed: {exc}')
        return None
