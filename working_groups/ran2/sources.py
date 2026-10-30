"""Select, revalidate and stage one RAN2 session schedule (plus agenda.csv).

RAN2 publishes ``R2_<meeting>_Schedule_v<NN>.docx`` in each meeting's
``Agenda/`` folder. During a meeting the live copy is in
``Meetings_3GPP_SYNC/RAN2/Agenda/``; before it, the next meeting's folder in
``tsg_ran/WG2_RL2/TSGR2_<meeting>/Agenda/`` already carries v00 and the
``agenda.csv``. Portal dates decide which meeting is current, so an early v00
for the following meeting never replaces the one in progress.

Meeting folders are found from the meeting id (``shared/meeting_folders.py``:
Portal's MtgDocURL, then ``TSGR2_135bis``/``TSGR2_135b``, then the archive
root) and recorded in ``meeting_specific``. ``meeting_specific_override``
(``.../WG2_RL2/TSGR2_135bis/``) is set by hand and wins for its meeting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from shared import meeting_folders, remote_files
from shared.json_files import read_json, write_json
from shared.meeting_folders import portal_key, portal_metadata
from shared.portal_meetings import TB_IDS, get_meetings, meeting_key
from shared.remote_files import client, fetch_file, listing_entries

from .document import file_info

CONFIG_PATH = Path('working_groups/ran2/config.json')
DOWNLOADS = Path('downloads/ran2')
OUTPUT = Path('docs/ran2')
TRANSFER = Path('.ci/transfers/ran2')
CACHE = Path('.cache/ran2')
DEFAULTS = {
    'sync_url': 'https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN2/Agenda/',
    'archive_url': 'https://www.3gpp.org/ftp/tsg_ran/WG2_RL2/',
    # Written by each run: the meeting folders it found (do not edit).
    'meeting_specific': [],
    # Set by hand: a meeting folder that wins over the one found.
    'meeting_specific_override': [],
    'local_agenda': None,
    # Gemini reads the cells the rules are unsure about, and whole documents
    # whose table layout they do not recognise. Without GEMINI_API_KEY the
    # rules' reading is used as is.
    'llm_fallback': True,
    'model': 'gemini-3.8-flash',
    'meetings': {},
}
METADATA_KEYS = {'starts_on', 'ends_on', 'starts_at', 'ends_at', 'location', 'country', 'timezone'}
FOLDER = re.compile(r'TSGR2_(\d+)(bis|b)?(?:-?e)?/?', re.I)
FOLDER_KEYS = ('meeting_specific', 'meeting_specific_override')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8')) if CONFIG_PATH.exists() else {}
    unknown = set(cfg) - set(DEFAULTS)
    if unknown:
        raise ValueError(f'Unknown RAN2 configuration: {sorted(unknown)}')
    result = {**DEFAULTS, **cfg}
    for name in ('sync_url', 'archive_url'):
        if urlparse(result[name]).scheme not in ('http', 'https'):
            raise ValueError(f'{name} must be an HTTP URL')
    if not isinstance(result['llm_fallback'], bool) or not isinstance(result['model'], str):
        raise ValueError('llm_fallback must be true/false and model a Gemini model name')
    for name in FOLDER_KEYS:
        if (not isinstance(result[name], list)
                or any(not isinstance(url, str) or urlparse(url).scheme not in ('http', 'https')
                       or not FOLDER.fullmatch(url.rstrip('/').rsplit('/', 1)[-1] + '/')
                       for url in result[name])):
            raise ValueError(f'{name} must list RAN2 meeting folder URLs such as .../TSGR2_135bis/')
    if not isinstance(result['meetings'], dict):
        raise ValueError('meetings must map a meeting such as "135bis" to its metadata')
    for key, value in result['meetings'].items():
        if meeting_key(f'RAN2#{key}') is None or not isinstance(value, dict) or set(value) - METADATA_KEYS:
            raise ValueError(f'Invalid RAN2 meeting override: {key}')
    return result


def config_hash(cfg) -> str:
    # Where a document is found does not change how it is read; the manifest
    # already records the URL of every input.
    return digest(json.dumps({k: v for k, v in cfg.items() if k not in FOLDER_KEYS},
                             sort_keys=True).encode())


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
    # Problems that leave the page behind without stopping it (not saved).
    warnings: list[str] = field(default_factory=list)

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

def fetch_listing(url: str, http) -> list[dict]:
    return listing_entries(remote_files.get_listing(url, http=http).text, url)


def agenda_folder(folder: str, http) -> list[dict]:
    """A meeting's Agenda/ entries; [] while the folder holds only an Invitation.

    3GPP answers 403, not 404, for a folder that does not exist, so look first
    instead of treating an access error as an empty folder. Portal can name a
    meeting folder before it is made: an absent one is empty, not an error.
    """
    try:
        entries = fetch_listing(folder, http)
    except httpx.HTTPError as exc:
        if not remote_files.is_absent(exc):
            raise
        print(f'[ran2] Meeting folder absent: {folder}')
        return []
    if not any(e['name'].lower() == 'agenda' for e in entries):
        return []
    return fetch_listing(folder + 'Agenda/', http)


def find_folders(cfg, meetings, rows, http) -> dict[str, str]:
    """Meeting folders by meeting id; meeting_specific_override wins."""
    return meeting_folders.resolve('ran2', meetings, rows=rows, override=cfg['meeting_specific_override'],
                                   recorded=cfg['meeting_specific'], root=cfg['archive_url'], http=http)


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

def sync_listing(cfg, http) -> list[dict]:
    """The sync folder's entries; none while the folder does not exist.

    The sync area follows the meeting: its folders and files appear and
    disappear (Agenda/ was removed on 2026-10-11, the day before #135bis).
    Absence is normal; only a real failure (5xx, connection) raises.
    """
    try:
        return fetch_listing(cfg['sync_url'], http)
    except httpx.HTTPError as exc:
        if not remote_files.is_absent(exc):
            raise
    print('[ran2] Sync folder absent; using the meeting folder')
    return []


def sync_warnings(bundle, error, dates, today) -> Bundle:
    """During the meeting only the sync folder carries new versions.

    The meeting folder holds what was uploaded before or after the meeting,
    so building from it keeps the page up but misses every update; say so.
    """
    meeting = bundle.manifest['meeting_id']
    if error is not None and meeting in dates and dates[meeting][0] <= today <= dates[meeting][1]:
        bundle.warnings.append(f'RAN2 sync folder unreadable during {meeting} ({error}); '
                               f"the page keeps {bundle.manifest['schedule']['name']} and misses newer versions")
    return bundle


def built_copy(cfg, previous, chosen, candidates, rows, error) -> Bundle | None:
    """The built version, when its file is gone and nothing newer is listed.

    New versions during a meeting appear only in the sync folder, which can
    lose them at any time (replaced, cleared); the meeting folder has the
    versions from before or after the meeting. Never step back within a
    meeting: keep what is built. Without its stored body, raise error.
    """
    old = previous.get('schedule') or {}
    if (previous.get('meeting_id') != chosen['meeting_id']
            or previous.get('version', -1) < chosen['version']
            or any(c['url'] == old.get('url') for c in candidates)):
        return None
    error = error or ValueError(f"{old.get('name')} is no longer listed and its stored copy is missing")
    schedule = remote_files.cached(old['url'], old.get('sha256'))
    agenda_info = previous.get('agenda') or None
    agenda = remote_files.cached(agenda_info['url'], agenda_info.get('sha256')) if agenda_info else b''
    if schedule is None or agenda is None:
        raise error
    manifest = {'meeting_id': previous['meeting_id'], 'version': previous['version'],
                'config_hash': config_hash(cfg), 'schedule': old, 'agenda': agenda_info}
    metadata = portal_metadata(rows or [], previous['meeting_id'])
    if metadata:
        manifest['portal_metadata'] = metadata
    return Bundle(manifest, schedule, agenda)


def fetch_bundle(cfg, previous, http, *, rows=None, today=None, cache_dir=DOWNLOADS / 'inputs') -> Bundle:
    today = today or datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
    try:
        entries, sync_error = sync_listing(cfg, http), None
    except httpx.HTTPError as exc:
        # A real failure, not absence. The meeting folder still has the
        # versions from before the meeting; sync_warnings reports the gap.
        print(f'[ran2] Sync folder unreadable ({exc}); reading the meeting folder')
        entries, sync_error = [], exc
    dates = meeting_dates(cfg, rows, previous)
    # Archive folders worth listing: the next two meetings that have not
    # ended, plus the last built one (its Agenda folder holds agenda.csv
    # after the event). Folders found once are remembered per meeting.
    upcoming = sorted((start, m) for m, (start, end) in dates.items() if end >= today)
    wanted = [m for _, m in upcoming[:2]]
    if previous.get('meeting_id') and previous['meeting_id'] not in wanted:
        wanted.append(previous['meeting_id'])
    archive = find_folders(cfg, wanted, rows, http)
    wanted = [m for m in wanted if m in archive]
    listed = {}
    for meeting in wanted:
        listed[meeting] = agenda_folder(archive[meeting], http)
        entries.extend(listed[meeting])
    candidates = schedule_candidates(entries)
    if sync_error is not None and not candidates:
        raise sync_error
    chosen = choose_schedule(candidates, dates, today)
    kept = built_copy(cfg, previous, chosen, candidates, rows, sync_error)
    if kept is not None:
        print(f"[ran2] Keeping {kept.manifest['schedule']['name']}: it is no longer listed, "
              f"and {chosen['name']} is not newer")
        return sync_warnings(kept, sync_error, dates, today)
    meeting = chosen['meeting_id']
    if meeting not in listed:
        # A meeting known only from the sync folder (Portal unavailable).
        archive.update(find_folders(cfg, [meeting], rows, http))
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
    return sync_warnings(Bundle(manifest, schedule, agenda), sync_error, dates, today)


def local_bundle(path: Path, cfg) -> Bundle:
    import io
    from docx import Document
    from .document import identify
    data = path.read_bytes()
    meeting = identify(Document(io.BytesIO(data)), path.name)[0]   # the table may need the LLM
    agenda, agenda_info = b'', None
    if cfg.get('local_agenda'):
        agenda_path = Path(cfg['local_agenda'])
        agenda = agenda_path.read_bytes()
        agenda_info = {'name': agenda_path.name, 'url': str(agenda_path), 'sha256': digest(agenda)}
    info = file_info(path.name)
    return Bundle({'meeting_id': meeting, 'version': info[1] if info else 0, 'config_hash': config_hash(cfg),
                   'schedule': {'name': path.name, 'url': str(path.resolve()), 'sha256': digest(data)},
                   'agenda': agenda_info}, data, agenda)
