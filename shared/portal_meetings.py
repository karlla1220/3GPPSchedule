"""Public Portal metadata shared by schedule builds and site selection.

Portal timezone labels are display names, not IANA identifiers. Known labels
map to regional rules (including DST); an unknown label stays unresolved.
"""
from datetime import datetime, timedelta
from functools import lru_cache
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

URL = 'https://portal.3gpp.org/webservices/Rest/Meetings.svc/GetMeetings'
TB_IDS = {'ran-plenary': 373, 'ran1': 379, 'ran2': 380, 'ran3': 381, 'ran4': 382}
HEADERS = {
    'Accept': 'application/json, text/plain, */*',
    'Origin': 'https://portal.3gpp.org', 'Referer': 'https://portal.3gpp.org/',
    'User-Agent': 'Mozilla/5.0 (3gppschedule) PortalMeetingsClient/1.0',
}
# Labels observed on Portal, plus the corresponding standard-time variants
# (the GMT prefix can change seasonally). Country selects a regional alias.
ZONES = {
    'Seoul': ('Asia/Seoul', {}),
    'Beijing, Chongqing, Hong Kong, Urumqi': ('Asia/Shanghai', {'HK': 'Asia/Hong_Kong'}),
    'Kuala Lumpur, Singapore': ('Asia/Singapore', {'MY': 'Asia/Kuala_Lumpur'}),
    'Osaka, Sapporo, Tokyo': ('Asia/Tokyo', {}),
    'Taipei': ('Asia/Taipei', {}),
    'Brussels, Copenhagen, Madrid, Paris': ('Europe/Paris', {
        'BE': 'Europe/Brussels', 'DK': 'Europe/Copenhagen', 'ES': 'Europe/Madrid',
        'NL': 'Europe/Amsterdam', 'FR': 'Europe/Paris', 'MT': 'Europe/Malta'}),
    'Amsterdam, Berlin, Bern, Rome, Stockholm, Vienna': ('Europe/Berlin', {
        'NL': 'Europe/Amsterdam', 'DE': 'Europe/Berlin', 'CH': 'Europe/Zurich',
        'IT': 'Europe/Rome', 'SE': 'Europe/Stockholm', 'AT': 'Europe/Vienna',
        'MT': 'Europe/Malta'}),
    'Eastern Time (US & Canada)': ('America/New_York', {'CA': 'America/Toronto'}),
    'Central Time (US & Canada)': ('America/Chicago', {'CA': 'America/Winnipeg'}),
    'Mountain Time (US & Canada)': ('America/Denver', {'CA': 'America/Edmonton'}),
    'Pacific Time (US & Canada)': ('America/Los_Angeles', {'CA': 'America/Vancouver'}),
    'Greenwich Mean Time': ('UTC', {}),
    'Greenwich Mean Time : Dublin, Edinburgh, Lisbon, London': ('Europe/London', {
        'IE': 'Europe/Dublin', 'PT': 'Europe/Lisbon'}),
    'UTC': ('UTC', {}),
}


def timezone_from_label(label, country=None):
    if not isinstance(label, str) or not label.strip():
        return None
    label = label.strip()
    try:
        return ZoneInfo(label).key
    except (ZoneInfoNotFoundError, ValueError):
        pass
    name = re.sub(r'^\((?:GMT|UTC)(?:[+-]\d{2}[:.]\d{2})?\)\s*', '', label)
    name = ' '.join(name.split())
    entry = ZONES.get(name)
    return entry[1].get(country, entry[0]) if entry else None


def meeting_key(value):
    """Match complete regular meeting identities, never social-event titles."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'(?:3GPP)?RAN([1-4]?)#(\d+)(?:[ -]?(bis|b|ter))?', value.strip(), re.I)
    if not match:
        return None
    wg, number, suffix = match.groups()
    suffix = (suffix or '').lower()
    return f'ran{wg}#{number}{"bis" if suffix == "b" else suffix}'


@lru_cache(maxsize=1)
def _fetch_meetings(today):
    rows = []
    with httpx.Client(headers=HEADERS, timeout=30) as client:
        # A bounded search covers recent, ongoing and next regular meetings.
        # Follow full pages rather than silently treating truncation as absence.
        for _ in range(20):
            payload = {'StartRow': len(rows), 'ResultsPerPage': 100,
                       'SortBy': 'Date', 'SortAscending': False,
                       'StartDate': f'{today - timedelta(days=366)} 00:00:00',
                       'EndDate': f'{today + timedelta(days=183)} 23:59:59',
                       'Tbs': list(TB_IDS.values()), 'IncludeChildTbs': False,
                       'IncludeNonTBMeetings': False, 'Reference': '', 'Registered': False}
            response = client.post(URL, json={'getMeetingsInput': payload})
            response.raise_for_status()
            page = response.json()
            if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
                raise ValueError('Unexpected Portal meetings response')
            if page and rows[-len(page):] == page:
                raise ValueError('Portal repeated a page')
            rows.extend(page)
            if len(page) < 100:
                return rows
    raise ValueError('Portal meetings page limit reached')


def get_meetings():
    """One shared fetch per process/day; failures remain errors to callers."""
    return _fetch_meetings(datetime.now(ZoneInfo('Asia/Seoul')).date())


def clear_meetings_cache():
    """Start a fresh check when several CI runs share one Python process."""
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


def timezone_reference(row):
    if row is None:
        return None
    start = timezone_from_label(row.get('StartTimeZone'), row.get('Country'))
    end = timezone_from_label(row.get('EndTimeZone'), row.get('Country'))
    if not start or (row.get('EndTimeZone') and start != end):
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


def lookup_timezone_reference(wg_id, identity):
    try:
        row = get_meeting(wg_id, identity)
        ref = timezone_reference(row)
        if row is not None and ref is None:
            print(f'Warning: Portal timezone unresolved for {identity}: {row.get("StartTimeZone")!r}')
        return ref
    except (httpx.HTTPError, ValueError) as exc:
        print(f'Warning: Portal metadata lookup failed: {exc}')
        return None
