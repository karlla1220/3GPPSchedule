"""Select, revalidate and stage one RAN4 meeting schedule (plus topic list and agenda.csv).

The chairs keep the schedule in each meeting's ``Inbox/Meeting_Arrangements/``
as ``RAN4_<meeting>_meeting_schedule*.pptx``, next to the topic list
(``List_Topics_moderators-RAN4_120bis_final.xlsx``). During a meeting the live
copy is in ``Meetings_3GPP_SYNC/RAN4/Inbox/Meeting_Arrangements/``: one file at
its root, older ones moved to ``History/``. Before and after the meeting the
files are in ``tsg_ran/WG4_Radio/TSGR4_<meeting>/Inbox/Meeting_Arrangements/``
and agenda.csv in that folder's ``Agenda/``.

File names do not order the versions: "v04", then "THU", "THU_morning",
"THU_afternoon". Listing times do not either (the archive re-copies files
after the meeting). PowerPoint's ``cp:revision`` does, within one meeting, so
every candidate of the current meeting is read for it (each download is a
conditional request after the first).

Portal dates decide which meeting is current. Meeting folders are found from
the meeting id (``shared/meeting_folders.py``) and recorded in
``meeting_specific``; ``meeting_specific_override`` is set by hand and wins.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
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

from .document import TITLE, file_info, meeting_id, read_core

CONFIG_PATH = Path('working_groups/ran4/config.json')
DOWNLOADS = Path('downloads/ran4')
OUTPUT = Path('docs/ran4')
TRANSFER = Path('.ci/transfers/ran4')
CACHE = Path('.cache/ran4')
DEFAULTS = {
    'sync_url': 'https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN4/Inbox/Meeting_Arrangements/',
    'sync_agenda_url': 'https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN4/Agenda/',
    'archive_url': 'https://www.3gpp.org/ftp/tsg_ran/WG4_Radio/',
    # Written by each run: the meeting folders it found (do not edit).
    'meeting_specific': [],
    # Set by hand: a meeting folder that wins over the one found.
    'meeting_specific_override': [],
    # For --local builds: the topic list and agenda.csv beside a local schedule.
    'local_topics': None,
    'local_agenda': None,
    # Gemini reads the cells the rules are unsure about (README "LLM 폴백").
    # Without GEMINI_API_KEY the rules' reading is used as is.
    'llm_fallback': True,
    'model': 'gemini-3.8-flash',
    # The 3GPP officials history code, and what the schedule writes for an
    # official whose label alone is ambiguous ("Yang" is TANG Yang and YANG Shan).
    'officials': 'R4',
    'chair_aliases': {'Yang': 'TANG, Yang'},
    'meetings': {},
}
METADATA_KEYS = {'starts_on', 'ends_on', 'starts_at', 'ends_at', 'location', 'country', 'timezone'}
FOLDER = re.compile(r'TSGR4_(\d+)(bis|b)?(?:-?e)?/?', re.I)
FOLDER_KEYS = ('meeting_specific', 'meeting_specific_override')
TOPIC_LIST = re.compile(r'(?:list[_ ]*(?:of[_ ]*)?topics|topics?[_ ]*list)', re.I)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8')) if CONFIG_PATH.exists() else {}
    unknown = set(cfg) - set(DEFAULTS)
    if unknown:
        raise ValueError(f'Unknown RAN4 configuration: {sorted(unknown)}')
    result = {**DEFAULTS, **cfg}
    for name in ('sync_url', 'sync_agenda_url', 'archive_url'):
        if urlparse(result[name]).scheme not in ('http', 'https'):
            raise ValueError(f'{name} must be an HTTP URL')
    if not isinstance(result['llm_fallback'], bool) or not isinstance(result['model'], str):
        raise ValueError('llm_fallback must be true/false and model a Gemini model name')
    if not isinstance(result['officials'], str) or not isinstance(result['chair_aliases'], dict):
        raise ValueError('officials must be a code such as "R4" and chair_aliases a mapping')
    for name in FOLDER_KEYS:
        if (not isinstance(result[name], list)
                or any(not isinstance(url, str) or urlparse(url).scheme not in ('http', 'https')
                       or not FOLDER.fullmatch(url.rstrip('/').rsplit('/', 1)[-1] + '/')
                       for url in result[name])):
            raise ValueError(f'{name} must list RAN4 meeting folder URLs such as .../TSGR4_120bis/')
    if not isinstance(result['meetings'], dict):
        raise ValueError('meetings must map a meeting such as "120bis" to its metadata')
    for key, value in result['meetings'].items():
        if meeting_key(f'RAN4#{key}') is None or not isinstance(value, dict) or set(value) - METADATA_KEYS:
            raise ValueError(f'Invalid RAN4 meeting override: {key}')
    return result


def config_hash(cfg) -> str:
    # Where a document is found does not change how it is read.
    return digest(json.dumps({k: v for k, v in cfg.items() if k not in FOLDER_KEYS},
                             sort_keys=True).encode())


def rank(meeting: str) -> tuple[int, int]:
    """ran4#120 < ran4#120bis < ran4#121"""
    match = re.fullmatch(r'ran4#(\d+)(bis)?', meeting)
    return (int(match[1]), 1 if match[2] else 0) if match else (-1, 0)


def override_for(cfg, meeting: str) -> dict:
    return next((v for k, v in cfg['meetings'].items() if meeting_key(f'RAN4#{k}') == meeting), {})


@dataclass
class Bundle:
    manifest: dict
    schedule: bytes
    topics: bytes
    agenda: bytes
    # Problems that leave the page behind without stopping it (not saved).
    warnings: list[str] = field(default_factory=list)

    FILES = (('schedule', 'schedule.pptx'), ('topics', 'topics.xlsx'), ('agenda', 'agenda.csv'))

    def save(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        for key, name in self.FILES:
            (directory / name).write_bytes(getattr(self, key))
        write_json(directory / 'manifest.json', self.manifest)

    @classmethod
    def load(cls, directory: Path):
        manifest = read_json(directory / 'manifest.json')
        bodies = {}
        for key, name in cls.FILES:
            bodies[key] = (directory / name).read_bytes()
            expected = (manifest.get(key) or {}).get('sha256', digest(b''))
            if digest(bodies[key]) != expected:
                raise ValueError(f'Corrupt RAN4 {key} cache')
        return cls(manifest, **bodies)


# ------------------------------------------------------------------- listings

def fetch_listing(url: str, http) -> list[dict]:
    return listing_entries(remote_files.get_listing(url, http=http).text, url)


def optional_listing(url: str, http, what: str) -> list[dict]:
    """A folder's entries; none while it does not exist (3GPP answers 403 for that)."""
    try:
        return fetch_listing(url, http)
    except httpx.HTTPError as exc:
        if not remote_files.is_absent(exc):
            raise
    print(f'[ran4] {what} absent: {url}')
    return []


def meeting_folder(folder: str, http) -> list[dict]:
    """A meeting folder's Inbox/Meeting_Arrangements and Agenda entries.

    Look before opening: a folder holding only an Invitation has neither, and
    an absent folder answers 403 rather than 404.
    """
    top = optional_listing(folder, http, 'Meeting folder')
    names = {e['name'].lower() for e in top}
    entries = []
    if 'inbox' in names:
        inbox = fetch_listing(folder + 'Inbox/', http)
        if any(e['name'].lower() == 'meeting_arrangements' for e in inbox):
            entries += fetch_listing(folder + 'Inbox/Meeting_Arrangements/', http)
    if 'agenda' in names:
        entries += fetch_listing(folder + 'Agenda/', http)
    return entries


def find_folders(cfg, meetings, rows, http) -> dict[str, str]:
    """Meeting folders by meeting id; meeting_specific_override wins."""
    return meeting_folders.resolve('ran4', meetings, rows=rows, override=cfg['meeting_specific_override'],
                                   recorded=cfg['meeting_specific'], root=cfg['archive_url'], http=http)


def schedule_candidates(entries: list[dict]) -> list[dict]:
    result = []
    for entry in entries:
        info = file_info(entry['name'])
        if info:
            result.append({**entry, 'meeting_id': info[0], 'label': info[1]})
    return result


def topic_candidates(entries: list[dict]) -> list[dict]:
    """Topic lists by meeting: "List_Topics_moderators-RAN4_120bis_final.xlsx",
    "RAN4_120 list of topics 6G FL and 5G moderators v1.xlsx"."""
    result = []
    for entry in entries:
        name = entry['name']
        if not name.lower().endswith('.xlsx') or name.startswith('~$') or not TOPIC_LIST.search(name):
            continue
        match = TITLE.search(name.replace('_', ' '))
        if match:
            result.append({**entry, 'meeting_id': meeting_id(match[1], match[2])})
    return result


def topic_rank(entry: dict) -> tuple:
    """final > highest vNN; the live copy, then the later upload, breaks a tie."""
    name = entry['name'].lower()
    version = re.findall(r'(?<![a-z0-9])v(\d+)(?![a-z0-9])', name)
    return ('final' in name, int(version[-1]) if version else -1,
            'Meetings_3GPP_SYNC' in entry['url'], entry['uploaded_at'], entry['name'])


# --------------------------------------------------------------------- Portal

def portal_rows() -> list[dict] | None:
    """RAN4 rows of the shared, once-per-process Portal query; None if unavailable."""
    try:
        return [row for row in get_meetings() if row.get('TBId') == TB_IDS['ran4']]
    except (httpx.HTTPError, ValueError) as exc:
        print(f'[ran4] Portal unavailable ({exc}); using saved or configured meeting dates')
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
            dates[meeting_key(f'RAN4#{key}')] = (value['starts_on'], value['ends_on'])
    return dates


def choose_meeting(candidates: list[dict], dates: dict, today: str) -> str:
    """The earliest meeting, by Portal dates, that has not ended and already
    has a schedule. Without one, the highest meeting that has a schedule."""
    if not candidates:
        raise ValueError('No RAN4 meeting schedule (RAN4_<meeting>_meeting_schedule*.pptx) found')
    meetings = {c['meeting_id'] for c in candidates}
    current = sorted((dates[m][0], m) for m in meetings if m in dates and dates[m][1] >= today)
    return current[0][1] if current else max(meetings, key=rank)


def version_key(entry: dict) -> tuple:
    """Saves of one file: cp:revision, then the save time; the live copy breaks a tie."""
    return (entry['revision'], entry['modified'], 'Meetings_3GPP_SYNC' in entry['url'],
            entry['uploaded_at'], entry['name'])


# -------------------------------------------------------------------- bundles

def fetch_bundle(cfg, previous, http, *, rows=None, today=None, cache_dir=DOWNLOADS / 'inputs') -> Bundle:
    today = today or datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
    warnings = []
    try:
        entries, sync_error = optional_listing(cfg['sync_url'], http, 'Sync folder'), None
        entries += optional_listing(cfg['sync_agenda_url'], http, 'Sync agenda folder')
    except httpx.HTTPError as exc:
        # A real failure, not absence: the meeting folder still has the
        # versions from before the meeting.
        print(f'[ran4] Sync folder unreadable ({exc}); reading the meeting folder')
        entries, sync_error = [], exc
    dates = meeting_dates(cfg, rows, previous)
    # Archive folders worth listing: the next two meetings that have not
    # ended, the last one that has (a page between meetings), and the last
    # built one.
    upcoming = sorted((start, m) for m, (start, end) in dates.items() if end >= today)
    ended = sorted((end, m) for m, (start, end) in dates.items() if end < today)
    wanted = [m for _, m in upcoming[:2]] + [m for _, m in ended[-1:]]
    if previous.get('meeting_id'):
        wanted.append(previous['meeting_id'])
    wanted = list(dict.fromkeys(wanted))
    archive = find_folders(cfg, wanted, rows, http)
    listed = {m: meeting_folder(archive[m], http) for m in wanted if m in archive}
    for found in listed.values():
        entries.extend(found)
    candidates = schedule_candidates(entries)
    if sync_error is not None and not candidates:
        raise sync_error
    meeting = choose_meeting(candidates, dates, today)
    if sync_error is not None and meeting in dates and dates[meeting][0] <= today <= dates[meeting][1]:
        warnings.append(f'RAN4 sync folder unreadable during {meeting} ({sync_error}); '
                        'the page may miss newer versions')

    cached = None
    try:
        cached = Bundle.load(cache_dir)
    except (OSError, ValueError, KeyError):
        pass
    old = previous.get('schedule') or {}
    versions = []
    for entry in (c for c in candidates if c['meeting_id'] == meeting):
        known = old if old.get('url') == entry['url'] else {}
        body, info = fetch_file(http, entry['url'], known, cached.schedule if cached and known else None)
        versions.append({**entry, **info, **read_core(body), 'body': body})
    chosen = max(versions, key=version_key)
    if (previous.get('meeting_id') == meeting and old.get('revision', -1) > chosen['revision']
            and not any(v['url'] == old.get('url') for v in versions)):
        # The built version vanished (the sync folder moves it to History/) and
        # nothing newer is listed: never step back within a meeting.
        body = remote_files.cached(old['url'], old.get('sha256'))
        if body is not None:
            print(f"[ran4] Keeping {old['name']}: it is no longer listed, and {chosen['name']} is older")
            chosen = {**old, 'body': body}

    topic_entry = max((t for t in topic_candidates(entries) if t['meeting_id'] == meeting),
                      key=topic_rank, default=None)
    topics, topics_info = b'', None
    if topic_entry is not None:
        topics, topics_info = fetch_file(http, topic_entry['url'], previous.get('topics') or {},
                                         cached.topics if cached and cached.topics else None)
    else:
        warnings.append(f'No RAN4 topic list for {meeting}; cells show no agenda items')
    agenda_entry = next((e for e in listed.get(meeting, []) if e['name'].lower() == 'agenda.csv'), None)
    if agenda_entry is None:
        agenda_entry = next((e for e in entries if e['listing'] == cfg['sync_agenda_url']
                             and e['name'].lower() == 'agenda.csv'), None)
    agenda, agenda_info = b'', None
    if agenda_entry is not None:
        agenda, agenda_info = fetch_file(http, agenda_entry['url'], previous.get('agenda') or {},
                                         cached.agenda if cached and cached.agenda else None)
    schedule_info = {k: v for k, v in chosen.items() if k not in ('body', 'listing', 'meeting_id')}
    manifest = {'meeting_id': meeting, 'version': chosen['revision'], 'config_hash': config_hash(cfg),
                'schedule': schedule_info, 'topics': topics_info, 'agenda': agenda_info}
    metadata = portal_metadata(rows or [], meeting)
    if metadata:
        manifest['portal_metadata'] = metadata
    return Bundle(manifest, chosen['body'], topics, agenda, warnings)


def local_bundle(path: Path, cfg) -> Bundle:
    data = path.read_bytes()
    info = file_info(path.name)
    if info is None:
        raise ValueError(f'{path.name} is not named like RAN4_<meeting>_meeting_schedule*.pptx')
    bodies, infos = {}, {}
    for key in ('topics', 'agenda'):
        local = cfg.get(f'local_{key}')
        bodies[key] = Path(local).read_bytes() if local else b''
        infos[key] = ({'name': Path(local).name, 'url': str(Path(local).resolve()), 'sha256': digest(bodies[key])}
                      if local else None)
    core = read_core(data)
    schedule = {'name': path.name, 'url': str(path.resolve()), 'sha256': digest(data), 'label': info[1], **core}
    return Bundle({'meeting_id': info[0], 'version': core['revision'], 'config_hash': config_hash(cfg),
                   'schedule': schedule, **infos}, data, bodies['topics'], bodies['agenda'])


