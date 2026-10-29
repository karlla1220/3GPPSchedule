"""Select, revalidate and stage one RAN3 living agenda, its offline room sheet and agenda.csv.

The chair keeps the time plan at the end of the living agenda,
``RAN3_<meeting>_agenda_<YYYYMMDD>[_<HHMM>][_EOM].docx``. Before the meeting
the versions are in the archive's ``TSGR3_<meeting>/Inbox/Chairs_Notes/``;
during it only in ``Meetings_3GPP_SYNC/RAN3/Inbox/Chairs_Notes/``, copied to
the archive at the close. Vice chairs post copies with their initials
(``_LX``, ``_yxd``) that can carry an older time plan; they are never chosen.

The offline room has ``RAN3 offline discussion_ver_NN.xlsx`` in
``Offline_meeting_room/`` beside it, with no meeting in its name. It is taken
from the folder the living agenda came from, and the pipeline still drops
sheets whose dates are not the meeting's (the sync folder keeps the last
meeting's copy until the next one starts).

Meeting folders are found from the meeting id (``shared/meeting_folders.py``)
and recorded in ``meeting_specific``, as for RAN2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import httpx

from shared import meeting_folders, remote_files
from shared.meeting_folders import portal_key
from shared.portal_meetings import TB_IDS, get_meetings, meeting_key, timezone_reference
from shared.remote_files import client, fetch_file

from .document import file_info

CONFIG_PATH = Path('working_groups/ran3/config.json')
DOWNLOADS = Path('downloads/ran3')
OUTPUT = Path('docs/ran3')
TRANSFER = Path('.ci/transfers/ran3')
CACHE = Path('.cache/ran3')
DEFAULTS = {
    'sync_url': 'https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN3/',
    'archive_url': 'https://www.3gpp.org/ftp/tsg_ran/WG3_Iu/',
    # Written by each run: the meeting folders it found (do not edit).
    'meeting_specific': [],
    # Set by hand: a meeting folder that wins over the one found.
    'meeting_specific_override': [],
    'local_agenda': None,
    'local_offline': None,
    # Gemini rereads the cells with lines the rules do not know. Without
    # GEMINI_API_KEY the rules' reading is used as is.
    'llm_fallback': True,
    'model': 'gemini-3.8-flash',
    # The vice chairs' initials in the time plan, as officials write them.
    'officials_code': 'R3',
    'chair_aliases': {'L': 'XU, Lixiang', 'X': 'YANG, Xudong', 'G': 'CAO, Gen'},
    'meetings': {},
}
METADATA_KEYS = {'starts_on', 'ends_on', 'starts_at', 'ends_at', 'location', 'country', 'timezone'}
FOLDER = re.compile(r'TSGR3_(\d+)(-?bis)?(?:-?e)?/?', re.I)
FOLDER_KEYS = ('meeting_specific', 'meeting_specific_override')
CHAIRS = 'Inbox/Chairs_Notes/'
OFFLINE = 'Offline_meeting_room/'
OFFLINE_FILE = re.compile(r'RAN3[ _]offline[ _]discussion[ _]*ver[ _]*(\d+)\.xlsx', re.I)
TIMESTAMP = re.compile(r'(\d{4})[/-](\d{2})[/-](\d{2})\s+(\d{1,2}):(\d{2})')
INPUTS = ('schedule', 'offline', 'agenda')


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
        raise ValueError(f'Unknown RAN3 configuration: {sorted(unknown)}')
    result = {**DEFAULTS, **cfg}
    for name in ('sync_url', 'archive_url'):
        if urlparse(result[name]).scheme not in ('http', 'https'):
            raise ValueError(f'{name} must be an HTTP URL')
    if not isinstance(result['llm_fallback'], bool) or not isinstance(result['model'], str):
        raise ValueError('llm_fallback must be true/false and model a Gemini model name')
    if not isinstance(result['chair_aliases'], dict) or not all(
            isinstance(k, str) and isinstance(v, str) and ',' in v for k, v in result['chair_aliases'].items()):
        raise ValueError('chair_aliases must map an initial to "FAMILY, Given"')
    for name in FOLDER_KEYS:
        if (not isinstance(result[name], list)
                or any(not isinstance(url, str) or urlparse(url).scheme not in ('http', 'https')
                       or not FOLDER.fullmatch(url.rstrip('/').rsplit('/', 1)[-1] + '/')
                       for url in result[name])):
            raise ValueError(f'{name} must list RAN3 meeting folder URLs such as .../TSGR3_133-bis/')
    if not isinstance(result['meetings'], dict):
        raise ValueError('meetings must map a meeting such as "133bis" to its metadata')
    for key, value in result['meetings'].items():
        if meeting_key(f'RAN3#{key}') is None or not isinstance(value, dict) or set(value) - METADATA_KEYS:
            raise ValueError(f'Invalid RAN3 meeting override: {key}')
    return result


def config_hash(cfg) -> str:
    # Where a document is found does not change how it is read.
    return digest(json.dumps({k: v for k, v in cfg.items() if k not in FOLDER_KEYS},
                             sort_keys=True).encode())


def rank(meeting: str) -> tuple[int, int]:
    """ran3#133 < ran3#133bis < ran3#134"""
    match = re.fullmatch(r'ran3#(\d+)(bis)?', meeting)
    return (int(match[1]), 1 if match[2] else 0) if match else (-1, 0)


def override_for(cfg, meeting: str) -> dict:
    return next((v for k, v in cfg['meetings'].items() if meeting_key(f'RAN3#{k}') == meeting), {})


def offline_version(name: str) -> int | None:
    match = OFFLINE_FILE.fullmatch(name.strip())
    return int(match[1]) if match else None


def source_names(manifest: dict) -> list[str]:
    return [manifest[key]['name'] for key in INPUTS if manifest.get(key)]


@dataclass
class Bundle:
    manifest: dict
    schedule: bytes
    offline: bytes
    agenda: bytes
    # Problems that leave the page behind without stopping it (not saved).
    warnings: list[str] = field(default_factory=list)

    def save(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'schedule.docx').write_bytes(self.schedule)
        (directory / 'offline.xlsx').write_bytes(self.offline)
        (directory / 'agenda.csv').write_bytes(self.agenda)
        write_json(directory / 'manifest.json', self.manifest)

    @classmethod
    def load(cls, directory: Path):
        manifest = read_json(directory / 'manifest.json')
        data = {key: (directory / name).read_bytes()
                for key, name in zip(INPUTS, ('schedule.docx', 'offline.xlsx', 'agenda.csv'))}
        for key, body in data.items():
            if digest(body) != (manifest.get(key) or {}).get('sha256', digest(b'')):
                raise ValueError(f'Corrupt RAN3 {key} cache')
        return cls(manifest, data['schedule'], data['offline'], data['agenda'])


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


def subfolder(folder: str, path: str, http) -> list[dict]:
    """Entries of folder + path ("Inbox/Chairs_Notes/"); [] when any level is absent.

    3GPP answers 403, not 404, for a folder that does not exist, so each level
    is looked up in its parent's listing before it is opened.
    """
    url = folder
    for part in path.strip('/').split('/'):
        try:
            entries = fetch_listing(url, http)
        except httpx.HTTPError as exc:
            if not remote_files.is_absent(exc):
                raise
            print(f'[ran3] Folder absent: {url}')
            return []
        if not any(e['name'].lower() == part.lower() for e in entries):
            return []
        url = url + part + '/'
    return fetch_listing(url, http)


def find_folders(cfg, meetings, rows, http) -> dict[str, str]:
    """Meeting folders by meeting id; meeting_specific_override wins."""
    return meeting_folders.resolve('ran3', meetings, rows=rows, override=cfg['meeting_specific_override'],
                                   recorded=cfg['meeting_specific'], root=cfg['archive_url'], http=http)


def schedule_candidates(entries: list[dict]) -> list[dict]:
    result = []
    for entry in entries:
        info = file_info(entry['name'])
        if info:
            result.append({**entry, 'meeting_id': info[0], 'version': info[1]})
    return result


def newest_offline(entries: list[dict]) -> dict | None:
    found = [(offline_version(e['name']), e['uploaded_at'], e['name'], e) for e in entries
             if offline_version(e['name']) is not None]
    return max(found, key=lambda f: f[:3])[3] if found else None


# --------------------------------------------------------------------- Portal

def portal_rows() -> list[dict] | None:
    """RAN3 rows of the shared, once-per-process Portal query; None if unavailable."""
    try:
        return [row for row in get_meetings() if row.get('TBId') == TB_IDS['ran3']]
    except (httpx.HTTPError, ValueError) as exc:
        print(f'[ran3] Portal unavailable ({exc}); using saved or configured meeting dates')
        return None


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
            dates[meeting_key(f'RAN3#{key}')] = (value['starts_on'], value['ends_on'])
    return dates


def choose_schedule(candidates: list[dict], dates: dict, today: str) -> dict:
    """The current meeting's newest living agenda.

    The current meeting is the earliest one, by Portal dates, that has not
    ended and already has a living agenda. Without one, keep the highest meeting.
    """
    if not candidates:
        raise ValueError('No RAN3 living agenda (RAN3_<meeting>_agenda_<date>.docx) found')
    meetings = {c['meeting_id'] for c in candidates}
    current = sorted((dates[m][0], m) for m in meetings if m in dates and dates[m][1] >= today)
    meeting = current[0][1] if current else max(meetings, key=rank)
    same = [c for c in candidates if c['meeting_id'] == meeting]
    # The sync folder is the live copy; the archive gets the same files at the close.
    return max(same, key=lambda c: (c['version'], 'Meetings_3GPP_SYNC' in c['url'], c['uploaded_at'], c['name']))


# -------------------------------------------------------------------- bundles

def sync_listing(url, http) -> list[dict]:
    """A sync folder's entries; none while the folder does not exist.

    Absence is normal; only a real failure (5xx, connection) raises.
    """
    try:
        return fetch_listing(url, http)
    except httpx.HTTPError as exc:
        if not remote_files.is_absent(exc):
            raise
    print(f'[ran3] Sync folder absent: {url}')
    return []


def sync_warnings(bundle, error, dates, today) -> Bundle:
    """During the meeting only the sync folder carries new versions; say when it was not read."""
    meeting = bundle.manifest['meeting_id']
    if error is not None and meeting in dates and dates[meeting][0] <= today <= dates[meeting][1]:
        bundle.warnings.append(f'RAN3 sync folder unreadable during {meeting} ({error}); '
                               f"the page keeps {bundle.manifest['schedule']['name']} and misses newer versions")
    return bundle


def built_copy(cfg, previous, chosen, candidates, rows, error) -> Bundle | None:
    """The built version, when its file is gone and nothing newer is listed.

    Never step back within a meeting: the sync folder can lose a version at
    any time. Without its stored body, raise error.
    """
    old = previous.get('schedule') or {}
    if (previous.get('meeting_id') != chosen['meeting_id']
            or str(previous.get('version', '')) < chosen['version']
            or any(c['url'] == old.get('url') for c in candidates)):
        return None
    error = error or ValueError(f"{old.get('name')} is no longer listed and its stored copy is missing")
    bodies = {}
    for key in INPUTS:
        info = previous.get(key) or None
        bodies[key] = remote_files.cached(info['url'], info.get('sha256')) if info else b''
        if bodies[key] is None:
            raise error
    manifest = {'meeting_id': previous['meeting_id'], 'version': previous['version'],
                'config_hash': config_hash(cfg), **{key: previous.get(key) or None for key in INPUTS}}
    metadata = portal_metadata(rows or [], previous['meeting_id'])
    if metadata:
        manifest['portal_metadata'] = metadata
    return Bundle(manifest, bodies['schedule'], bodies['offline'], bodies['agenda'])


def fetch_bundle(cfg, previous, http, *, rows=None, today=None, cache_dir=DOWNLOADS / 'inputs') -> Bundle:
    today = today or datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
    sync_chairs, sync_offline = cfg['sync_url'] + CHAIRS, cfg['sync_url'] + OFFLINE
    try:
        entries, sync_error = sync_listing(sync_chairs, http), None
    except httpx.HTTPError as exc:
        print(f'[ran3] Sync folder unreadable ({exc}); reading the meeting folder')
        entries, sync_error = [], exc
    dates = meeting_dates(cfg, rows, previous)
    # Archive folders worth listing: the next two meetings that have not
    # ended, plus the last built one.
    upcoming = sorted((start, m) for m, (start, end) in dates.items() if end >= today)
    wanted = [m for _, m in upcoming[:2]]
    if previous.get('meeting_id') and previous['meeting_id'] not in wanted:
        wanted.append(previous['meeting_id'])
    archive = find_folders(cfg, wanted, rows, http)
    for meeting in [m for m in wanted if m in archive]:
        entries.extend(subfolder(archive[meeting], CHAIRS, http))
    candidates = schedule_candidates(entries)
    if sync_error is not None and not candidates:
        raise sync_error
    chosen = choose_schedule(candidates, dates, today)
    kept = built_copy(cfg, previous, chosen, candidates, rows, sync_error)
    if kept is not None:
        print(f"[ran3] Keeping {kept.manifest['schedule']['name']}: it is no longer listed, "
              f"and {chosen['name']} is not newer")
        return sync_warnings(kept, sync_error, dates, today)
    meeting = chosen['meeting_id']
    if meeting not in archive:
        # A meeting known only from the sync folder (Portal unavailable).
        archive.update(find_folders(cfg, [meeting], rows, http))
    folder = archive.get(meeting)
    live = chosen['listing'] == sync_chairs
    # The offline sheet beside the living agenda: live during the meeting, archived after it.
    offline_entries = (sync_listing(sync_offline, http) if live and sync_error is None
                       else subfolder(folder, OFFLINE, http) if folder else [])
    offline_entry = newest_offline(offline_entries)
    agenda_entry = next((e for e in (subfolder(folder, 'Agenda/', http) if folder else [])
                         if e['name'].lower() == 'agenda.csv'), None)

    cached = None
    try:
        cached = Bundle.load(cache_dir)
    except (OSError, ValueError, KeyError):
        pass
    manifest = {'meeting_id': meeting, 'version': chosen['version'], 'config_hash': config_hash(cfg)}
    bodies = {}
    for key, entry in zip(INPUTS, (chosen, offline_entry, agenda_entry)):
        if entry is None:
            bodies[key], manifest[key] = b'', None
            continue
        old_body = getattr(cached, key) if cached and getattr(cached, key) else None
        bodies[key], info = fetch_file(http, entry['url'], previous.get(key) or {}, old_body)
        manifest[key] = {**{k: entry[k] for k in ('name', 'url', 'uploaded_at')}, **info}
    metadata = portal_metadata(rows or [], meeting)
    if metadata:
        manifest['portal_metadata'] = metadata
    bundle = Bundle(manifest, bodies['schedule'], bodies['offline'], bodies['agenda'])
    return sync_warnings(bundle, sync_error, dates, today)


def local_bundle(path: Path, cfg) -> Bundle:
    import io
    from docx import Document
    from .document import identify
    data = path.read_bytes()
    meeting = identify(Document(io.BytesIO(data)), path.name)
    info = file_info(path.name)
    manifest = {'meeting_id': meeting, 'version': info[1] if info else '', 'config_hash': config_hash(cfg),
                'schedule': {'name': path.name, 'url': str(path.resolve()), 'sha256': digest(data)}}
    bodies = {'schedule': data}
    for key, setting in (('offline', 'local_offline'), ('agenda', 'local_agenda')):
        bodies[key], manifest[key] = b'', None
        if cfg.get(setting):
            local = Path(cfg[setting])
            bodies[key] = local.read_bytes()
            manifest[key] = {'name': local.name, 'url': str(local), 'sha256': digest(bodies[key])}
    return Bundle(manifest, bodies['schedule'], bodies['offline'], bodies['agenda'])
