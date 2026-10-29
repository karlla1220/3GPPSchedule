"""Select, revalidate and stage one SA1 chair file (plus the timetable-only file).

SA1 keeps agenda, schedule and TDoc conclusions in one chair file. During a
meeting the newest is in ``Meetings_3GPP_SYNC/SA1/Inbox/agenda/`` and older
ones in ``previous_ones/``. Names carry no vNN but the local weekday and time
of the version ("Agenda for SA1#115_Thursday_1030.docx",
"S1-261408_Agenda_SA1#113_16_00_friday.docx"); the last is
"..._END_OF_MEETING.docx". Before the meeting the 2nd draft and the agenda are
TDoc zips (``S1-263001.zip``, ``S1-263002.zip``) in the sync ``Inbox/`` or the
meeting folder's ``Docs/``; after it the closing file is in the meeting folder
(``temp_reports/``, ``report/``, ``draft_report/``). FTP listing times are not
an order: the sync area is re-copied in bulk (2026-08-28 10:18).

The chair sometimes uploads only the schedule table under another name
("Breakout sessions schedule_Prague_WED_1730.docx"). It is used when its name's
weekday and time are later than the newest chair file's; the agenda still comes
from the chair file. A version without a schedule table (the 1st draft, #114's
closing file) is skipped, so the page keeps the last schedule.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlparse
import zipfile
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import httpx

from shared import meeting_folders, remote_files
from shared.meeting_folders import portal_key
from shared.portal_meetings import TB_IDS, get_meetings, meeting_key, timezone_reference
from shared.remote_files import client, fetch_file

CONFIG_PATH = Path('working_groups/sa1/config.json')
DOWNLOADS = Path('downloads/sa1')
OUTPUT = Path('docs/sa1')
TRANSFER = Path('.ci/transfers/sa1')
CACHE = Path('.cache/sa1')
DEFAULTS = {
    'sync_url': 'https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/SA1/Inbox/',
    'archive_url': 'https://www.3gpp.org/ftp/tsg_sa/WG1_Serv/',
    # Written by each run: the meeting folders it found (do not edit).
    'meeting_specific': [],
    # Set by hand: a meeting folder that wins over the one found.
    'meeting_specific_override': [],
    # The 3GPP officials code: chairs written in the NOTE get the officials' spelling.
    'officials_code': 'S1',
    # Gemini reads the cells the rules are unsure about. Without
    # GEMINI_API_KEY the rules' reading is used as is.
    'llm_fallback': True,
    'model': 'gemini-3.8-flash',
    'meetings': {},
}
METADATA_KEYS = {'starts_on', 'ends_on', 'starts_at', 'ends_at', 'location', 'country', 'timezone'}
FOLDER_KEYS = ('meeting_specific', 'meeting_specific_override')
FOLDER = re.compile(r'TSGS1_\d+[^/]*/?', re.I)
TIMESTAMP = re.compile(r'(\d{4})[/-](\d{2})[/-](\d{2})\s+(\d{1,2}):(\d{2})')
# "Agenda for SA1#115", "Agenda_of_SA1#112", "Agenda_SA1#113", "SA1#114_ChairNotes"; not the
# session chairs' "Agenda_AI_SA1#115_WED_10_30" or "AgendaDrafting_Massive_SA1#115".
CHAIR_FILE = re.compile(r'^(?:S1-\d+_)?(?:Agenda(?:\s+for|_of)?[\s_]*SA1\s*#|SA1\s*#\s*\d+\w*?_Chair\s*Notes)', re.I)
TIMETABLE_FILE = re.compile(r'schedule', re.I)
AGENDA_TDOC = re.compile(r'^S1-\d{3}00([12])\.zip$', re.I)
REPORT_FOLDERS = re.compile(r'^(?:docs|report|temp_reports|draft_report)$', re.I)
DAY_WORD = re.compile(r'(?<![A-Za-z])(mon|tue|wed|thu|fri)[a-z]*(?![A-Za-z])', re.I)
TIME_WORD = re.compile(r'(?<!\d)([01]?\d|2[0-3])[_:.]?([0-5]\d)(?!\d)')
DAY_NAMES = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri')


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
        raise ValueError(f'Unknown SA1 configuration: {sorted(unknown)}')
    result = {**DEFAULTS, **cfg}
    for name in ('sync_url', 'archive_url'):
        if urlparse(result[name]).scheme not in ('http', 'https'):
            raise ValueError(f'{name} must be an HTTP URL')
    if not isinstance(result['llm_fallback'], bool) or not isinstance(result['model'], str):
        raise ValueError('llm_fallback must be true/false and model a Gemini model name')
    if not isinstance(result['officials_code'], str) or not result['officials_code']:
        raise ValueError('officials_code must be the 3GPP officials code (S1)')
    for name in FOLDER_KEYS:
        if (not isinstance(result[name], list)
                or any(not isinstance(url, str) or urlparse(url).scheme not in ('http', 'https')
                       or not FOLDER.fullmatch(unquote(url.rstrip('/').rsplit('/', 1)[-1]) + '/')
                       for url in result[name])):
            raise ValueError(f'{name} must list SA1 meeting folder URLs such as .../TSGS1_116_Calgary/')
    if not isinstance(result['meetings'], dict):
        raise ValueError('meetings must map a meeting such as "116" to its metadata')
    for key, value in result['meetings'].items():
        if meeting_key(f'SA1#{key}') is None or not isinstance(value, dict) or set(value) - METADATA_KEYS:
            raise ValueError(f'Invalid SA1 meeting override: {key}')
    return result


def config_hash(cfg) -> str:
    return digest(json.dumps({k: v for k, v in cfg.items() if k not in FOLDER_KEYS}, sort_keys=True).encode())


def rank(meeting: str) -> tuple[int, int]:
    """sa1#115 < sa1#116"""
    match = re.fullmatch(r'sa1#(\d+)(bis)?', meeting or '')
    return (int(match[1]), 1 if match[2] else 0) if match else (-1, 0)


def override_for(cfg, meeting: str) -> dict:
    return next((v for k, v in cfg['meetings'].items() if meeting_key(f'SA1#{k}') == meeting), {})


# ------------------------------------------------------------------ versions

def name_time(name: str) -> tuple[int, int] | None:
    """(weekday 0-4, minutes) written in a file name, or None.

    The meeting and TDoc numbers are removed first so "#115" is never 1:15.
    """
    text = re.sub(r'SA1\s*#\s*\d+(?:bis|b)?|S1-\d+', ' ', Path(name).stem, flags=re.I)
    day = DAY_WORD.search(text)
    if not day:
        return None
    rest = text[:day.start()] + ' ' + text[day.end():]
    clock = TIME_WORD.search(rest)
    minutes = int(clock[1]) * 60 + int(clock[2]) if clock else 0
    return DAY_NAMES.index(day[1][:3].title()), minutes


def classify(entry: dict) -> dict | None:
    """A schedule candidate with its order key, or None.

    The key orders versions of one meeting: (stage, weekday, minutes). Stage 0
    and 1 are the 2nd draft and the agenda TDoc, 2 the versions during the
    meeting, 3 the closing file. A timetable-only file is stage 2.
    """
    name = entry['name']
    lower = name.lower()
    tdoc = AGENDA_TDOC.match(name)
    if tdoc:
        stage = int(tdoc[1]) - 1
        return {**entry, 'kind': 'chair', 'meeting_id': entry.get('folder_meeting'), 'order': [stage, 0, 0],
                'label': f"Chair's Notes {name[:-4]}"}
    if not lower.endswith(('.docx', '.zip')):
        return None
    when = name_time(name)
    closing = 'end_of_meeting' in lower or 'final' in lower or entry.get('report')
    if CHAIR_FILE.match(name) or (entry.get('report') and 'agenda' in lower):
        from .document import name_meeting
        meeting = name_meeting(name) or entry.get('folder_meeting')
        if closing:
            order, label = [3, *(when or (9, 0))], "Chair's Notes EOM"
        elif when:
            order, label = [2, *when], f"Chair's Notes {DAY_NAMES[when[0]]} {when[1] // 60:02d}:{when[1] % 60:02d}"
        else:
            order, label = [2, -1, 0], "Chair's Notes"
        return {**entry, 'kind': 'chair', 'meeting_id': meeting, 'order': order, 'label': label, 'timed': bool(when)}
    if TIMETABLE_FILE.search(name) and lower.endswith('.docx') and when:
        return {**entry, 'kind': 'timetable', 'meeting_id': None, 'order': [2, *when],
                'label': f'Breakout schedule {DAY_NAMES[when[0]]} {when[1] // 60:02d}:{when[1] % 60:02d}'}
    return None


def docx_of(data: bytes, name: str) -> bytes:
    """The chair file itself; a TDoc zip holds it next to nothing else of use."""
    if not name.lower().endswith('.zip'):
        return data
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        inner = [i for i in archive.infolist() if i.filename.lower().endswith('.docx')]
        if not inner:
            raise ValueError(f'No DOCX in {name}')
        return archive.read(max(inner, key=lambda i: ('agenda' in i.filename.lower(), i.file_size)))


@dataclass
class Bundle:
    manifest: dict
    schedule: bytes          # the file the grid is read from (DOCX or TDoc zip)
    chair: bytes = b''       # the chair file for the agenda and chairs, when the schedule is timetable-only
    warnings: list[str] = field(default_factory=list)

    def save(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'schedule.bin').write_bytes(self.schedule)
        (directory / 'chair.bin').write_bytes(self.chair)
        write_json(directory / 'manifest.json', self.manifest)

    @classmethod
    def load(cls, directory: Path):
        manifest = read_json(directory / 'manifest.json')
        schedule = (directory / 'schedule.bin').read_bytes()
        chair = (directory / 'chair.bin').read_bytes()
        if digest(schedule) != manifest['schedule']['sha256']:
            raise ValueError('Corrupt SA1 schedule cache')
        if digest(chair) != (manifest.get('chair') or {}).get('sha256', digest(b'')):
            raise ValueError('Corrupt SA1 chair file cache')
        return cls(manifest, schedule, chair)

    def documents(self) -> tuple[bytes, bytes | None]:
        """(schedule DOCX, chair DOCX or None when the schedule is the chair file)."""
        schedule = docx_of(self.schedule, self.manifest['schedule']['name'])
        chair = self.manifest.get('chair')
        return schedule, docx_of(self.chair, chair['name']) if chair else None


# ------------------------------------------------------------------- listings

def listing_entries(html: str, url: str) -> list[dict]:
    entries = []
    for anchor in BeautifulSoup(html, 'html.parser').find_all('a', href=True):
        href = urljoin(url, anchor['href'])
        if not href.startswith(url) or href.rstrip('/') == url.rstrip('/'):
            continue
        name = unquote(urlparse(href).path.rstrip('/').rsplit('/', 1)[-1])
        row = anchor.find_parent('tr')
        text = row.get_text(' ', strip=True) if row else ''
        stamp = TIMESTAMP.search(text)
        uploaded = (f'{stamp[1]}-{stamp[2]}-{stamp[3]} {int(stamp[4]):02d}:{stamp[5]}' if stamp else '')
        entries.append({'url': href, 'name': name, 'uploaded_at': uploaded, 'listing': url})
    return entries


def fetch_listing(url: str, http) -> list[dict]:
    """A folder's entries; none while it does not exist (3GPP answers 403 for absent folders)."""
    try:
        return listing_entries(remote_files.get_listing(url, http=http).text, url)
    except httpx.HTTPError as exc:
        if not remote_files.is_absent(exc):
            raise
    return []


def sync_entries(cfg, http) -> list[dict]:
    """The chair files in Inbox/agenda/ (and previous_ones/), and agenda TDoc zips in Inbox/."""
    entries = []
    for entry in fetch_listing(cfg['sync_url'], http):
        if AGENDA_TDOC.match(entry['name']):
            entries.append(entry)
    for folder in ('agenda/', 'agenda/previous_ones/'):
        entries.extend(fetch_listing(cfg['sync_url'] + folder, http))
    return entries


def archive_entries(folder: str, meeting: str, http) -> list[dict]:
    """Agenda TDoc zips in Docs/ and closing files in the report folders of one meeting folder."""
    entries = []
    for sub in fetch_listing(folder, http):
        if not REPORT_FOLDERS.match(sub['name']) or not sub['url'].endswith('/'):
            continue
        report = sub['name'].lower() != 'docs'
        for entry in fetch_listing(sub['url'], http):
            if report or AGENDA_TDOC.match(entry['name']):
                entries.append({**entry, 'folder_meeting': meeting, 'report': report})
    return entries


def find_folders(cfg, meetings, rows, http) -> dict[str, str]:
    return meeting_folders.resolve('sa1', meetings, rows=rows, override=cfg['meeting_specific_override'],
                                   recorded=cfg['meeting_specific'], root=cfg['archive_url'], http=http)


# --------------------------------------------------------------------- Portal

def portal_rows() -> list[dict] | None:
    try:
        return [row for row in get_meetings() if row.get('TBId') == TB_IDS['sa1']]
    except (httpx.HTTPError, ValueError) as exc:
        print(f'[sa1] Portal unavailable ({exc}); using saved or configured meeting dates')
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
            dates[meeting_key(f'SA1#{key}')] = (value['starts_on'], value['ends_on'])
    return dates


# ------------------------------------------------------------------- choosing

def choose_meeting(candidates: list[dict], dates: dict, today: str) -> str:
    """The earliest meeting, by Portal dates, that has not ended and has a chair file; else the highest."""
    meetings = {c['meeting_id'] for c in candidates if c['meeting_id']}
    if not meetings:
        raise ValueError('No SA1 chair file (Agenda for SA1#<meeting>_<day>_<time>.docx) found')
    current = sorted((dates[m][0], m) for m in meetings if m in dates and dates[m][1] >= today)
    return current[0][1] if current else max(meetings, key=rank)


def ordered(candidates: list[dict], meeting: str, dates: dict) -> list[dict]:
    """The meeting's versions, newest first.

    An untimed chair file ("SA1#116_ChairNotes.docx", overwritten in place) is
    as new as its upload when no version of the meeting has a time in its
    name; a timetable-only file counts only when uploaded during the meeting.
    """
    own = [c for c in candidates if c['meeting_id'] == meeting]
    timed = any(c.get('timed') for c in own)
    start, end = dates.get(meeting, ('', ''))
    for c in candidates:
        if c['kind'] == 'timetable' and start and start <= c['uploaded_at'][:10] <= end:
            own.append({**c, 'meeting_id': meeting})
    for c in own:
        if c['kind'] == 'chair' and c['order'][0] == 2 and c['order'][1] == -1 and not timed and c['uploaded_at']:
            c['order'] = [2, 9, 0]   # the only kind of version: its upload decides against a timetable file
    # Equal keys: the chair file wins (it is the complete one), then the live sync copy.
    return sorted(own, key=lambda c: (c['order'], c['kind'] == 'chair', 'Meetings_3GPP_SYNC' in c['url'],
                                      c['uploaded_at'], c['name']), reverse=True)


def fetch_bundle(cfg, previous, http, *, rows=None, today=None, cache_dir=DOWNLOADS / 'inputs') -> Bundle:
    from .document import has_schedule, identify
    from docx import Document
    today = today or datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
    dates = meeting_dates(cfg, rows, previous)
    entries = sync_entries(cfg, http)
    upcoming = sorted((start, m) for m, (start, end) in dates.items() if end >= today)
    wanted = [m for _, m in upcoming[:2]]
    if previous.get('meeting_id') and previous['meeting_id'] not in wanted:
        wanted.append(previous['meeting_id'])
    for meeting, folder in find_folders(cfg, wanted, rows, http).items():
        entries.extend(archive_entries(folder, meeting, http))
    candidates = [c for c in map(classify, entries) if c]

    cached = None
    try:
        cached = Bundle.load(cache_dir)
    except (OSError, ValueError, KeyError):
        pass

    def fetch(candidate):
        old = next((info for info in (previous.get('schedule'), previous.get('chair'))
                    if info and info.get('url') == candidate['url']), {})
        local = cached.schedule if cached and cached.manifest['schedule'].get('url') == candidate['url'] else None
        return fetch_file(http, candidate['url'], old, local)

    # TDoc zips in the sync Inbox do not name their meeting: open them only
    # while the next meeting has no other file yet (they are revalidated, not refetched).
    named = {c['meeting_id'] for c in candidates if c['meeting_id']}
    following = next((m for _, m in upcoming), None)
    if following and following not in named:
        for candidate in sorted((c for c in candidates if not c['meeting_id'] and c['kind'] == 'chair'),
                                key=lambda c: c['name'], reverse=True):
            data, _ = fetch(candidate)
            if identify(Document(io.BytesIO(docx_of(data, candidate['name']))), candidate['name'])[0] == following:
                candidate['meeting_id'] = following
    meeting = choose_meeting(candidates, dates, today)
    chosen = None
    for candidate in ordered(candidates, meeting, dates):
        data, info = fetch(candidate)
        if has_schedule(docx_of(data, candidate['name'])):
            chosen = (candidate, data, info)
            break
        print(f"[sa1] {candidate['name']} has no schedule table; trying the version before it")
    if chosen is None:
        raise ValueError(f'No version of {meeting} has a schedule table yet')
    candidate, data, info = chosen
    kept = kept_copy(previous, meeting, candidate)
    if kept is not None:
        return kept
    chair_bytes, chair_info = b'', None
    if candidate['kind'] == 'timetable':
        chair = next((c for c in ordered(candidates, meeting, dates) if c['kind'] == 'chair'), None)
        if chair is not None:
            chair_bytes, chair_info = fetch(chair)
            chair_info = {**chair_info, 'name': chair['name'], 'label': chair['label']}
    manifest = {'meeting_id': meeting, 'version': candidate['label'], 'order': candidate['order'],
                'config_hash': config_hash(cfg),
                'schedule': {**info, 'name': candidate['name'], 'label': candidate['label'],
                             'uploaded_at': candidate['uploaded_at']},
                'chair': chair_info}
    metadata = portal_metadata(rows or [], meeting)
    if metadata:
        manifest['portal_metadata'] = metadata
    return Bundle(manifest, data, chair_bytes)


def kept_copy(previous: dict, meeting: str, chosen: dict) -> Bundle | None:
    """Never step back within a meeting: the built version, when it is newer than what is listed."""
    old = previous.get('schedule') or {}
    if previous.get('meeting_id') != meeting or list(previous.get('order') or []) <= list(chosen['order']):
        return None
    schedule = remote_files.cached(old.get('url', ''), old.get('sha256'))
    chair_info = previous.get('chair')
    chair = remote_files.cached(chair_info['url'], chair_info.get('sha256')) if chair_info else b''
    if schedule is None or chair is None:
        return None
    print(f"[sa1] Keeping {old['name']}: {chosen['name']} is not newer")
    return Bundle({k: previous[k] for k in ('meeting_id', 'version', 'order', 'config_hash', 'schedule', 'chair')
                   if k in previous}, schedule, chair)


def local_bundle(path: Path, cfg) -> Bundle:
    from docx import Document
    from .document import identify
    data = path.read_bytes()
    meeting = identify(Document(io.BytesIO(docx_of(data, path.name))), path.name)[0]
    if meeting is None:
        raise ValueError(f'Cannot tell the SA1 meeting of {path.name}; use a chair file')
    candidate = classify({'name': path.name, 'url': str(path.resolve()), 'uploaded_at': ''}) or {
        'label': "Chair's Notes", 'order': [2, -1, 0]}
    return Bundle({'meeting_id': meeting, 'version': candidate['label'], 'order': candidate['order'],
                   'config_hash': config_hash(cfg),
                   'schedule': {'name': path.name, 'url': str(path.resolve()), 'sha256': digest(data),
                                'label': candidate['label']},
                   'chair': None}, data)
