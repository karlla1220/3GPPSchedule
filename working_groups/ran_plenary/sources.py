"""Select, revalidate and stage one coherent timeplan/agenda input bundle."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup
import httpx

from shared import remote_files
from shared.remote_files import client, fetch_file
from shared.portal_meetings import TB_IDS, get_meetings, timezone_reference

from .document import FILE

CONFIG_PATH = Path('working_groups/ran_plenary/config.json')
DOWNLOADS = Path('downloads/ran-plenary')
OUTPUT = Path('docs/ran-plenary')
CACHE = Path('.cache/ran-plenary')
TRANSFER = Path('.ci/transfers/ran-plenary')
DEFAULTS = {
    'chair_url': 'https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN/Inbox/Chair/',
    # The meeting folder keeps agenda.csv; the sync copy is emptied at some
    # point after the meeting, so it is only the fallback.
    'meeting_agenda_url': 'https://www.3gpp.org/ftp/tsg_ran/TSG_RAN/TSGR_{number}/Agenda/agenda.csv',
    'agenda_url': 'https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN/Agenda/agenda.csv',
    'model': 'gemini-3.8-flash',
    'local_agenda': None,
    'meetings': {},
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    unknown = set(cfg) - set(DEFAULTS)
    if unknown:
        raise ValueError(f'Unknown RAN P configuration: {sorted(unknown)}')
    result = {**DEFAULTS, **cfg}
    for name in ('chair_url', 'agenda_url', 'meeting_agenda_url'):
        if urlparse(result[name]).scheme not in ('http', 'https'):
            raise ValueError(f'{name} must be an HTTP URL')
    if '{number}' not in result['meeting_agenda_url']:
        raise ValueError('meeting_agenda_url must contain {number}')
    if not isinstance(result['meetings'], dict):
        raise ValueError('meetings must be a dictionary keyed by meeting number')
    return result


def config_hash(cfg):
    return digest(json.dumps(cfg, sort_keys=True).encode())



@dataclass
class Bundle:
    manifest: dict
    timeplan: bytes
    agenda: bytes

    def save(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'timeplan.bin').write_bytes(self.timeplan)
        (directory / 'agenda.csv').write_bytes(self.agenda)
        write_json(directory / 'manifest.json', self.manifest)

    @classmethod
    def load(cls, directory: Path):
        manifest = read_json(directory / 'manifest.json')
        data = [(directory / file).read_bytes() for file in ('timeplan.bin', 'agenda.csv')]
        for key, content in zip(('timeplan', 'agenda'), data):
            if digest(content) != manifest[key]['sha256']:
                raise ValueError(f'Corrupt {key} cache')
        return cls(manifest, *data)


class NothingNewer(ValueError):
    """The Chair listing has no timeplan at least as new as the built one."""


def select_timeplan(html: str, listing_url: str, previous: dict) -> dict:
    candidates = []
    for anchor in BeautifulSoup(html, 'html.parser').find_all('a', href=True):
        url = urljoin(listing_url, anchor['href'])
        name = unquote(urlparse(url).path.rsplit('/', 1)[-1])
        match = FILE.fullmatch(name)
        if not match:
            continue
        row = anchor.find_parent('tr')
        uploaded = row.get_text(' ', strip=True) if row else ''
        timestamp = re.search(r'\d{4}[/-]\d{2}[/-]\d{2}\s+\d{2}:\d{2}(?::\d{2})?', uploaded)
        candidates.append({'url': url, 'name': name, 'meeting_number': int(match.group(1)),
                           'version': int(match.group(2)), 'listing_metadata': uploaded,
                           'uploaded_at': timestamp.group().replace('/', '-') if timestamp else ''})
    if not candidates:
        raise NothingNewer('No RAN timeplan found in Chair listing')
    chosen = max(candidates, key=lambda c: (c['meeting_number'], c['version'], c['uploaded_at'], c['name']))
    old_rank = (previous.get('meeting_number', 0), previous.get('version', 0))
    if (chosen['meeting_number'], chosen['version']) < old_rank:
        raise NothingNewer('Chair listing would roll back the last successful timeplan')
    return chosen


def built_bundle(cfg, previous, reason) -> Bundle:
    """The last built inputs, read back from the shared 3GPP cache.

    The sync area follows the meeting: the Chair folder and its timeplans
    appear and disappear. Their absence keeps the page; it is not an error.
    """
    plan_info, agenda_info = previous.get('timeplan') or {}, previous.get('agenda') or {}
    plan = remote_files.cached(plan_info['url'], plan_info.get('sha256')) if plan_info.get('url') else None
    agenda = remote_files.cached(agenda_info['url'], agenda_info.get('sha256')) if agenda_info.get('url') else None
    if plan is None or agenda is None:
        raise ValueError(f'{reason}, and the last built inputs are not cached')
    print(f"[ran-plenary] {reason}; keeping {plan_info.get('name')}")
    return Bundle({'meeting_number': previous['meeting_number'], 'version': previous['version'],
                   'config_hash': config_hash(cfg), 'timeplan': plan_info, 'agenda': agenda_info}, plan, agenda)


def fetch_agenda(cfg, number, previous, http, cached):
    """agenda.csv from the meeting folder, else the sync folder, else as built."""
    urls = [cfg['meeting_agenda_url'].format(number=number), cfg['agenda_url']]
    for url in urls:
        try:
            return fetch_file(http, url, previous.get('agenda', {}), cached.agenda if cached else None)
        except httpx.HTTPError as exc:
            if not remote_files.is_absent(exc):
                raise
    old = previous.get('agenda') or {}
    if previous.get('meeting_number') == number and old.get('url'):
        body = remote_files.cached(old['url'], old.get('sha256'))
        if body is not None:
            print(f'[ran-plenary] No agenda.csv listed for RAN#{number}; keeping the built one')
            return body, old
    raise ValueError(f'No agenda.csv for RAN#{number} at {" or ".join(urls)}')



def fetch_bundle(cfg, previous, http, cache_dir=DOWNLOADS / 'inputs') -> Bundle:
    try:
        listing = remote_files.get_listing(cfg['chair_url'], http=http)
        chosen = select_timeplan(listing.text, cfg['chair_url'], previous)
    except (httpx.HTTPError, NothingNewer) as exc:
        absent = isinstance(exc, NothingNewer) or remote_files.is_absent(exc)
        if not (absent and previous.get('timeplan')):
            raise
        return built_bundle(cfg, previous, 'Chair folder absent' if isinstance(exc, httpx.HTTPError) else str(exc))
    cached = None
    try:
        cached = Bundle.load(cache_dir)
    except (OSError, ValueError, KeyError):
        pass
    plan, plan_info = fetch_file(http, chosen['url'], previous.get('timeplan', {}),
                                cached.timeplan if cached else None)
    agenda, agenda_info = fetch_agenda(cfg, chosen['meeting_number'], previous, http, cached)
    manifest = {'meeting_number': chosen['meeting_number'], 'version': chosen['version'],
                'config_hash': config_hash(cfg), 'timeplan': {**chosen, **plan_info}, 'agenda': agenda_info}
    return Bundle(manifest, plan, agenda)


def local_bundle(path: Path, cfg, previous) -> Bundle:
    from .document import extract_document
    data = path.read_bytes()
    number = extract_document(data, path.name)['meeting_number']
    if cfg.get('local_agenda'):
        agenda_path = Path(cfg['local_agenda'])
        agenda = agenda_path.read_bytes()
        agenda_info = {'url': str(agenda_path), 'sha256': digest(agenda)}
    else:
        cached = Bundle.load(DOWNLOADS / 'inputs')
        if cached.manifest['meeting_number'] != number:
            raise ValueError('Local timeplan needs an agenda for the same meeting; configure local_agenda')
        agenda, agenda_info = cached.agenda, cached.manifest['agenda']
    match = FILE.fullmatch(path.name)
    return Bundle({'meeting_number': number, 'version': int(match.group(2)) if match else 0,
                   'config_hash': config_hash(cfg),
                   'timeplan': {'name': path.name, 'url': str(path.resolve()), 'sha256': digest(data)},
                   'agenda': agenda_info}, data, agenda)


def portal_meeting(number: int, rows=None) -> dict:
    """RAN#number from the shared Portal query (one per process, all WGs)."""
    rows = get_meetings() if rows is None else rows
    matches = {m['Id']: m for m in rows if m.get('TBId') == TB_IDS['ran-plenary'] and
               re.fullmatch(rf'(?:3GPP)?RAN\s*#\s*{number}', m.get('Title', ''), re.I)}
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one Portal RAN#{number} meeting; configure metadata for historical meetings')
    m = next(iter(matches.values()))
    reference = timezone_reference(m) or {}
    return {'starts_at': reference.get('starts_at', m['StartDate']),
            'ends_at': reference.get('ends_at', m['EndDate']),
            'starts_on': m['StartDate'][:10], 'ends_on': m['EndDate'][:10],
            'location': m.get('Location', ''), 'country': m.get('Country', ''),
            'portal_timezone': m.get('StartTimeZone', ''), 'portal_id': m['Id']}
