"""Select, revalidate and stage one RAN2 session schedule (plus agenda.csv).

RAN2 publishes ``R2_<meeting>_Schedule_v<NN>.docx`` in each meeting's
``Agenda/`` folder. During a meeting the live copy is in
``Meetings_3GPP_SYNC/RAN2/Agenda/``; before it, the next meeting's folder in
``tsg_ran/WG2_RL2/TSGR2_<meeting>/Agenda/`` already carries v00 and the
``agenda.csv``. Portal dates decide which meeting is current, so an early v00
for the following meeting never replaces the one in progress.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import httpx

from shared import remote_files
from shared.portal_meetings import TB_IDS, get_meetings, meeting_key, timezone_reference
from shared.remote_files import client, fetch_file

from .document import file_info

CONFIG_PATH = Path('working_groups/ran2/config.json')
DOWNLOADS = Path('downloads/ran2')
OUTPUT = Path('docs/ran2')
TRANSFER = Path('.ci/transfers/ran2')
DEFAULTS = {
    'sync_url': 'https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN2/Agenda/',
    'archive_url': 'https://www.3gpp.org/ftp/tsg_ran/WG2_RL2/',
    'local_agenda': None,
    'meetings': {},
}
METADATA_KEYS = {'starts_on', 'ends_on', 'starts_at', 'ends_at', 'location', 'country', 'timezone'}
FOLDER = re.compile(r'TSGR2_(\d+)(bis|b)?/?', re.I)
TIMESTAMP = re.compile(r'(\d{4})[/-](\d{2})[/-](\d{2})\s+(\d{1,2}):(\d{2})')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8')) if CONFIG_PATH.exists() else {}
    unknown = set(cfg) - set(DEFAULTS)
    if unknown:
        raise ValueError(f'Unknown RAN2 configuration: {sorted(unknown)}')
    result = {**DEFAULTS, **cfg}
    for name in ('sync_url', 'archive_url'):
        if urlparse(result[name]).scheme not in ('http', 'https'):
            raise ValueError(f'{name} must be an HTTP URL')
    if not isinstance(result['meetings'], dict):
        raise ValueError('meetings must map a meeting such as "135bis" to its metadata')
    for key, value in result['meetings'].items():
        if meeting_key(f'RAN2#{key}') is None or not isinstance(value, dict) or set(value) - METADATA_KEYS:
            raise ValueError(f'Invalid RAN2 meeting override: {key}')
    return result


def config_hash(cfg) -> str:
    return digest(json.dumps(cfg, sort_keys=True).encode())


def rank(meeting: str) -> tuple[int, int]:
    """ran2#135 < ran2#135bis < ran2#136"""
    match = re.fullmatch(r'ran2#(\d+)(bis)?', meeting)
    return (int(match[1]), 1 if match[2] else 0) if match else (-1, 0)


def override_for(cfg, meeting: str) -> dict:
    return next((v for k, v in cfg['meetings'].items() if meeting_key(f'RAN2#{k}') == meeting), {})


@dataclass
class Bundle:
    manifest: dict
    schedule: bytes
    agenda: bytes

    def save(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'schedule.docx').write_bytes(self.schedule)
        (directory / 'agenda.csv').write_bytes(self.agenda)
        write_json(directory / 'manifest.json', self.manifest)

    @classmethod
    def load(cls, directory: Path):
        manifest = read_json(directory / 'manifest.json')
        schedule = (directory / 'schedule.docx').read_bytes()
        agenda = (directory / 'agenda.csv').read_bytes()
        if digest(schedule) != manifest['schedule']['sha256']:
            raise ValueError('Corrupt RAN2 schedule cache')
        if digest(agenda) != (manifest.get('agenda') or {}).get('sha256', digest(b'')):
            raise ValueError('Corrupt RAN2 agenda cache')
        return cls(manifest, schedule, agenda)


# ------------------------------------------------------------------- listings

def listing_entries(html: str, url: str) -> list[dict]:
    entries = []
    for anchor in BeautifulSoup(html, 'html.parser').find_all('a', href=True):
        href = urljoin(url, anchor['href'])
        name = unquote(urlparse(href).path.rstrip('/').rsplit('/', 1)[-1])
        row = anchor.find_parent('tr')
        text = row.get_text(' ', strip=True) if row else ''
        stamp = TIMESTAMP.search(text)
        uploaded = (f'{stamp[1]}-{stamp[2]}-{stamp[3]} {int(stamp[4]):02d}:{stamp[5]}' if stamp else '')
        entries.append({'url': href, 'name': name, 'uploaded_at': uploaded, 'listing': url})
    return entries


def fetch_listing(url: str, http) -> list[dict]:
    return listing_entries(remote_files.get_listing(url, http=http).text, url)


def agenda_folder(folder: str, http) -> list[dict]:
    """A meeting's Agenda/ entries; [] while the folder holds only an Invitation.

    3GPP answers 403, not 404, for a folder that does not exist, so look first
    instead of treating an access error as an empty folder.
    """
    if not any(e['name'].lower() == 'agenda' for e in fetch_listing(folder, http)):
        return []
    return fetch_listing(folder + 'Agenda/', http)


def archive_folders(entries: list[dict]) -> dict[str, str]:
    folders = {}
    for entry in entries:
        match = FOLDER.fullmatch(entry['name'] + '/')
        if match:
            folders[meeting_key(f'RAN2#{match[1]}{match[2] or ""}')] = entry['url'].rstrip('/') + '/'
    return folders


def schedule_candidates(entries: list[dict]) -> list[dict]:
    result = []
    for entry in entries:
        info = file_info(entry['name'])
        if info:
            result.append({**entry, 'meeting_id': info[0], 'version': info[1]})
    return result


# --------------------------------------------------------------------- Portal

def portal_rows() -> list[dict] | None:
    """RAN2 rows of the shared, once-per-process Portal query; None if unavailable."""
    try:
        return [row for row in get_meetings() if row.get('TBId') == TB_IDS['ran2']]
    except (httpx.HTTPError, ValueError) as exc:
        print(f'[ran2] Portal unavailable ({exc}); using saved or configured meeting dates')
        return None


def portal_key(row) -> str | None:
    # Portal writes both "3GPPRAN2#135-bis" and "3GPP RAN2#132".
    return meeting_key(re.sub(r'^3GPP\s+', '3GPP', str(row.get('Title') or '').strip()))


def portal_metadata(rows: list[dict], meeting: str) -> dict | None:
    matches = [row for row in rows if portal_key(row) == meeting]
    if len(matches) != 1:
        return None
    row = matches[0]
    reference = timezone_reference(row) or {}
    metadata = {'starts_on': row['StartDate'][:10], 'ends_on': row['EndDate'][:10],
                'location': row.get('Location') or '', 'country': row.get('Country') or '',
                'portal_timezone': row.get('StartTimeZone') or '', 'portal_id': row.get('Id')}
    for key in ('timezone', 'starts_at', 'ends_at', 'starts_on', 'ends_on'):
        if reference.get(key):
            metadata[key] = reference[key]
    return metadata


def meeting_dates(cfg, rows, previous) -> dict[str, tuple[str, str]]:
    """Known (starts_on, ends_on) per meeting: overrides, Portal, last build."""
    dates = {}
    if previous.get('meeting_id') and previous.get('metadata', {}).get('ends_on'):
        dates[previous['meeting_id']] = (previous['metadata']['starts_on'], previous['metadata']['ends_on'])
    for row in rows or []:
        key = portal_key(row)
        if key and row.get('StartDate') and row.get('EndDate'):
            dates[key] = (row['StartDate'][:10], row['EndDate'][:10])
    for key, value in cfg['meetings'].items():
        if value.get('starts_on') and value.get('ends_on'):
            dates[meeting_key(f'RAN2#{key}')] = (value['starts_on'], value['ends_on'])
    return dates


def choose_schedule(candidates: list[dict], dates: dict, today: str) -> dict:
    """The current meeting's newest version.

    The current meeting is the earliest one, by Portal dates, that has not
    ended and already has a schedule. Without one, keep the highest meeting.
    """
    if not candidates:
        raise ValueError('No RAN2 session schedule (R2_<meeting>_Schedule_vNN.docx) found')
    meetings = {c['meeting_id'] for c in candidates}
    current = sorted((dates[m][0], m) for m in meetings if m in dates and dates[m][1] >= today)
    meeting = current[0][1] if current else max(meetings, key=rank)
    same = [c for c in candidates if c['meeting_id'] == meeting]
    # The sync folder is the live copy; the archive re-uploads the same version later.
    return max(same, key=lambda c: (c['version'], 'Meetings_3GPP_SYNC' in c['url'], c['uploaded_at'], c['name']))


# -------------------------------------------------------------------- bundles

def fetch_bundle(cfg, previous, http, *, rows=None, today=None, cache_dir=DOWNLOADS / 'inputs') -> Bundle:
    today = today or datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
    entries = fetch_listing(cfg['sync_url'], http)
    archive = archive_folders(fetch_listing(cfg['archive_url'], http))
    dates = meeting_dates(cfg, rows, previous)
    # Archive folders worth listing: meetings that have not ended yet, plus
    # the last built one (its Agenda folder holds agenda.csv after the event).
    upcoming = sorted((start, m) for m, (start, end) in dates.items() if end >= today and m in archive)
    wanted = [m for _, m in upcoming[:2]]
    if previous.get('meeting_id') in archive and previous['meeting_id'] not in wanted:
        wanted.append(previous['meeting_id'])
    listed = {}
    for meeting in wanted:
        listed[meeting] = agenda_folder(archive[meeting], http)
        entries.extend(listed[meeting])
    chosen = choose_schedule(schedule_candidates(entries), dates, today)
    meeting = chosen['meeting_id']
    if meeting not in listed and meeting in archive:
        listed[meeting] = agenda_folder(archive[meeting], http)
    agenda_entry = next((e for e in listed.get(meeting, []) if e['name'].lower() == 'agenda.csv'), None)
    if agenda_entry is None and chosen['listing'] == cfg['sync_url']:
        agenda_entry = next((e for e in entries if e['listing'] == cfg['sync_url']
                             and e['name'].lower() == 'agenda.csv'), None)

    cached = None
    try:
        cached = Bundle.load(cache_dir)
    except (OSError, ValueError, KeyError):
        pass
    schedule, schedule_info = fetch_file(http, chosen['url'], previous.get('schedule', {}),
                                         cached.schedule if cached else None)
    agenda, agenda_info = b'', None
    if agenda_entry is not None:
        agenda, agenda_info = fetch_file(http, agenda_entry['url'], previous.get('agenda') or {},
                                         cached.agenda if cached and cached.agenda else None)
    manifest = {'meeting_id': meeting, 'version': chosen['version'], 'config_hash': config_hash(cfg),
                'schedule': {**{k: chosen[k] for k in ('name', 'url', 'uploaded_at')}, **schedule_info},
                'agenda': agenda_info}
    metadata = portal_metadata(rows or [], meeting)
    if metadata:
        manifest['portal_metadata'] = metadata
    return Bundle(manifest, schedule, agenda)


def local_bundle(path: Path, cfg) -> Bundle:
    from .document import extract_document
    data = path.read_bytes()
    meeting = extract_document(data, path.name)['meeting_id']
    agenda, agenda_info = b'', None
    if cfg.get('local_agenda'):
        agenda_path = Path(cfg['local_agenda'])
        agenda = agenda_path.read_bytes()
        agenda_info = {'name': agenda_path.name, 'url': str(agenda_path), 'sha256': digest(agenda)}
    info = file_info(path.name)
    return Bundle({'meeting_id': meeting, 'version': info[1] if info else 0, 'config_hash': config_hash(cfg),
                   'schedule': {'name': path.name, 'url': str(path.resolve()), 'sha256': digest(data)},
                   'agenda': agenda_info}, data, agenda)
