"""Select, revalidate and stage one SA2 Session Plan (plus agenda.csv).

The chair revises ``S2-<TDoc>_SA2-<meeting>-Session Plan - r<N>.docx``.
During a meeting the versions appear only in the live copy,
``Meetings_3GPP_SYNC/SA2/Inbox/Drafts/_Session_Plan_Updates/``. Before it, the
meeting folder in ``tsg_sa/WG2_Arch/`` has the early versions (ZIP) in
``INBOX/DRAFTS/_Session_Plan_Updates/`` (#176 on) or ``Agenda/`` (#172 to
#175), and ``Agenda/agenda.csv``. Portal dates decide which meeting is current.

SA folder names carry the venue (``TSGS2_177_Prague_2026-10``), so they are
found from Portal's MtgDocURL, the record in ``meeting_specific`` or the
archive root (``shared/meeting_folders.py``). ``meeting_specific_override``
is set by hand and wins for its meeting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlparse
import zipfile
from zoneinfo import ZoneInfo

import httpx

from shared import meeting_folders, remote_files
from shared.json_files import read_json, write_json
from shared.meeting_folders import portal_key, portal_metadata
from shared.portal_meetings import TB_IDS, get_meetings, meeting_key
from shared.remote_files import client, fetch_file, listing_entries

from .document import file_info

CONFIG_PATH = Path('working_groups/sa2/config.json')
DOWNLOADS = Path('downloads/sa2')
OUTPUT = Path('docs/sa2')
TRANSFER = Path('.ci/transfers/sa2')
CACHE = Path('.cache/sa2')
DEFAULTS = {
    'sync_url': 'https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/SA2/Inbox/Drafts/_Session_Plan_Updates/',
    'archive_url': 'https://www.3gpp.org/ftp/tsg_sa/WG2_Arch/',
    # Written by each run: the meeting folders it found (do not edit).
    'meeting_specific': [],
    # Set by hand: a meeting folder that wins over the one found.
    'meeting_specific_override': [],
    'local_agenda': None,
    # The officials page code and the plan's convener names that are not an
    # official's name ("Mike" is STARSINIC, Michael). See shared/officials.py.
    'officials_code': 'S2',
    'chair_aliases': {'Mike': 'STARSINIC, Michael'},
    # Gemini reads the cells the rules are unsure about, and whole plans whose
    # table is not the template. Without GEMINI_API_KEY the rules' reading is used.
    'llm_fallback': True,
    'model': 'gemini-3.8-flash',
    'meetings': {},
}
METADATA_KEYS = {'starts_on', 'ends_on', 'starts_at', 'ends_at', 'location', 'country', 'timezone'}
FOLDER = re.compile(r'TSGS2_(\d+)(?:_[^/]*)?/?', re.I)
FOLDER_KEYS = ('meeting_specific', 'meeting_specific_override')
PLANS = 'DRAFTS/_Session_Plan_Updates/'


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8')) if CONFIG_PATH.exists() else {}
    unknown = set(cfg) - set(DEFAULTS)
    if unknown:
        raise ValueError(f'Unknown SA2 configuration: {sorted(unknown)}')
    result = {**DEFAULTS, **cfg}
    for name in ('sync_url', 'archive_url'):
        if urlparse(result[name]).scheme not in ('http', 'https'):
            raise ValueError(f'{name} must be an HTTP URL')
    if not isinstance(result['llm_fallback'], bool) or not isinstance(result['model'], str):
        raise ValueError('llm_fallback must be true/false and model a Gemini model name')
    if not isinstance(result['officials_code'], str) or not isinstance(result['chair_aliases'], dict):
        raise ValueError('officials_code must be a code such as "S2" and chair_aliases map a name to "FAMILY, Given"')
    for name in FOLDER_KEYS:
        if (not isinstance(result[name], list)
                or any(not isinstance(url, str) or urlparse(url).scheme not in ('http', 'https')
                       or not FOLDER.fullmatch(unquote(url.rstrip('/').rsplit('/', 1)[-1]) + '/')
                       for url in result[name])):
            raise ValueError(f'{name} must list SA2 meeting folder URLs such as .../TSGS2_177_Prague_2026-10/')
    if not isinstance(result['meetings'], dict):
        raise ValueError('meetings must map a meeting such as "178" to its metadata')
    for key, value in result['meetings'].items():
        if meeting_key(f'SA2#{key}') is None or not isinstance(value, dict) or set(value) - METADATA_KEYS:
            raise ValueError(f'Invalid SA2 meeting override: {key}')
    return result


def config_hash(cfg) -> str:
    # Where a document is found does not change how it is read.
    return digest(json.dumps({k: v for k, v in cfg.items() if k not in FOLDER_KEYS}, sort_keys=True).encode())


def rank(meeting: str) -> int:
    match = re.fullmatch(r'sa2#(\d+)', meeting)
    return int(match[1]) if match else -1


def override_for(cfg, meeting: str) -> dict:
    return next((v for k, v in cfg['meetings'].items() if meeting_key(f'SA2#{k}') == meeting), {})


def unpack(data: bytes, name: str) -> tuple[bytes, str]:
    """The DOCX inside a pre-meeting ZIP ("S2-2610195_SA2-177-Session Plan - r6.zip")."""
    if not name.lower().endswith('.zip'):
        return data, name
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        inner = [n for n in archive.namelist() if n.lower().endswith('.docx') and not n.startswith('__MACOSX')]
        if len(inner) != 1:
            raise ValueError(f'{name} holds {len(inner)} DOCX files, not one')
        return archive.read(inner[0]), name[:-4] + '.docx'


@dataclass
class Bundle:
    manifest: dict
    schedule: bytes   # as downloaded: DOCX, or the ZIP before the meeting
    agenda: bytes
    # Problems that leave the page behind without stopping it (not saved).
    warnings: list[str] = field(default_factory=list)

    def save(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'schedule.bin').write_bytes(self.schedule)
        (directory / 'agenda.csv').write_bytes(self.agenda)
        write_json(directory / 'manifest.json', self.manifest)

    @classmethod
    def load(cls, directory: Path):
        manifest = read_json(directory / 'manifest.json')
        schedule = (directory / 'schedule.bin').read_bytes()
        agenda = (directory / 'agenda.csv').read_bytes()
        if digest(schedule) != manifest['schedule']['sha256']:
            raise ValueError('Corrupt SA2 Session Plan cache')
        if digest(agenda) != (manifest.get('agenda') or {}).get('sha256', digest(b'')):
            raise ValueError('Corrupt SA2 agenda cache')
        return cls(manifest, schedule, agenda)

    def document(self) -> tuple[bytes, str]:
        return unpack(self.schedule, self.manifest['schedule']['name'])


# ------------------------------------------------------------------- listings

def fetch_listing(url: str, http) -> list[dict]:
    return listing_entries(remote_files.get_listing(url, http=http).text, url)


def _listed(url: str, http) -> list[dict]:
    """A folder's entries; [] when it does not exist (3GPP answers 403, not 404)."""
    try:
        return fetch_listing(url, http)
    except httpx.HTTPError as exc:
        if not remote_files.is_absent(exc):
            raise
        return []


def meeting_folder(folder: str, http) -> tuple[list[dict], list[dict]]:
    """(plan entries, Agenda/ entries) of a meeting folder; empty while it is not made.

    Plans are in INBOX/DRAFTS/_Session_Plan_Updates/ (#176 on) or Agenda/
    (#172 to #175). The folder is listed first, as for RAN2, so a missing
    subfolder costs no request.
    """
    entries = _listed(folder, http)
    if not entries:
        print(f'[sa2] Meeting folder absent or empty: {folder}')
    names = {e['name'].lower(): e['name'] for e in entries}
    plans = _listed(f'{folder}{names["inbox"]}/{PLANS}', http) if 'inbox' in names else []
    agenda = _listed(f'{folder}{names["agenda"]}/', http) if 'agenda' in names else []
    return plans + agenda, agenda


def find_folders(cfg, meetings, rows, http) -> dict[str, str]:
    """Meeting folders by meeting id; meeting_specific_override wins."""
    return meeting_folders.resolve('sa2', meetings, rows=rows, override=cfg['meeting_specific_override'],
                                   recorded=cfg['meeting_specific'], root=cfg['archive_url'], http=http)


def schedule_candidates(entries: list[dict]) -> list[dict]:
    result = []
    for entry in entries:
        info = file_info(entry['name'])
        if info:
            result.append({**entry, 'meeting_id': info[0], 'version': info[1]})
    return result


def order(entry: dict) -> tuple:
    """Revision, then upload time: "-r14 - Friday plan" came after "-r14", and #176's r15 before r14."""
    return entry['version'], entry.get('uploaded_at') or '', 'Meetings_3GPP_SYNC' in entry['url'], entry['name']


# --------------------------------------------------------------------- Portal

def portal_rows() -> list[dict] | None:
    """SA2 rows of the shared, once-per-process Portal query; None if unavailable."""
    try:
        return [row for row in get_meetings() if row.get('TBId') == TB_IDS['sa2']]
    except (httpx.HTTPError, ValueError) as exc:
        print(f'[sa2] Portal unavailable ({exc}); using saved or configured meeting dates')
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
            dates[meeting_key(f'SA2#{key}')] = (value['starts_on'], value['ends_on'])
    return dates


def choose_schedule(candidates: list[dict], dates: dict, today: str) -> dict:
    """The current meeting's newest plan.

    The current meeting is the earliest one, by Portal dates, that has not
    ended and already has a plan. Without one, keep the highest meeting.
    """
    if not candidates:
        raise ValueError('No SA2 Session Plan (S2-<TDoc>_SA2-<meeting>-Session Plan - r<N>.docx) found')
    meetings = {c['meeting_id'] for c in candidates}
    current = sorted((dates[m][0], m) for m in meetings if m in dates and dates[m][1] >= today)
    meeting = current[0][1] if current else max(meetings, key=rank)
    return max((c for c in candidates if c['meeting_id'] == meeting), key=order)


# -------------------------------------------------------------------- bundles

def sync_listing(cfg, http) -> list[dict]:
    """The live copy's entries; none while the folder does not exist (normal between meetings)."""
    try:
        return fetch_listing(cfg['sync_url'], http)
    except httpx.HTTPError as exc:
        if not remote_files.is_absent(exc):
            raise
    print('[sa2] Sync folder absent; using the meeting folder')
    return []


def sync_warnings(bundle, error, dates, today) -> Bundle:
    """During the meeting only the sync folder carries new plans; say when it cannot be read."""
    meeting = bundle.manifest['meeting_id']
    if error is not None and meeting in dates and dates[meeting][0] <= today <= dates[meeting][1]:
        bundle.warnings.append(f'SA2 sync folder unreadable during {meeting} ({error}); '
                               f"the page keeps {bundle.manifest['schedule']['name']} and misses newer plans")
    return bundle


def built_copy(cfg, previous, chosen, candidates, rows, error) -> Bundle | None:
    """The built plan, when its file is gone and nothing newer is listed.

    The sync folder can lose a plan at any time (replaced, cleared); never
    step back within a meeting. Without its stored body, raise error.
    """
    old = previous.get('schedule') or {}
    if (previous.get('meeting_id') != chosen['meeting_id']
            or (previous.get('version', -1), old.get('uploaded_at') or '') < (chosen['version'], chosen['uploaded_at'])
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
        print(f'[sa2] Sync folder unreadable ({exc}); reading the meeting folder')
        entries, sync_error = [], exc
    dates = meeting_dates(cfg, rows, previous)
    # Archive folders worth listing: the next two meetings that have not
    # ended, plus the last built one (its Agenda folder holds agenda.csv).
    upcoming = sorted((start, m) for m, (start, end) in dates.items() if end >= today)
    wanted = [m for _, m in upcoming[:2]]
    if previous.get('meeting_id') and previous['meeting_id'] not in wanted:
        wanted.append(previous['meeting_id'])
    archive = find_folders(cfg, wanted, rows, http)
    agendas = {}
    for meeting in [m for m in wanted if m in archive]:
        plans, agendas[meeting] = meeting_folder(archive[meeting], http)
        entries.extend(plans)
    candidates = schedule_candidates(entries)
    if sync_error is not None and not candidates:
        raise sync_error
    chosen = choose_schedule(candidates, dates, today)
    kept = built_copy(cfg, previous, chosen, candidates, rows, sync_error)
    if kept is not None:
        print(f"[sa2] Keeping {kept.manifest['schedule']['name']}: it is no longer listed, "
              f"and {chosen['name']} is not newer")
        return sync_warnings(kept, sync_error, dates, today)
    meeting = chosen['meeting_id']
    if meeting not in agendas:
        # A meeting known only from the sync folder (Portal unavailable).
        archive.update(find_folders(cfg, [meeting], rows, http))
        if meeting in archive:
            agendas[meeting] = meeting_folder(archive[meeting], http)[1]
    agenda_entry = next((e for e in agendas.get(meeting, []) if e['name'].lower() == 'agenda.csv'), None)

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
    from docx import Document
    from .document import identify
    data = path.read_bytes()
    document, name = unpack(data, path.name)
    meeting = identify(Document(io.BytesIO(document)), name)
    agenda, agenda_info = b'', None
    if cfg.get('local_agenda'):
        agenda_path = Path(cfg['local_agenda'])
        agenda = agenda_path.read_bytes()
        agenda_info = {'name': agenda_path.name, 'url': str(agenda_path), 'sha256': digest(agenda)}
    info = file_info(path.name)
    return Bundle({'meeting_id': meeting, 'version': info[1] if info else 0, 'config_hash': config_hash(cfg),
                   'schedule': {'name': path.name, 'url': str(path.resolve()), 'sha256': digest(data),
                                'uploaded_at': ''},
                   'agenda': agenda_info}, data, agenda)
