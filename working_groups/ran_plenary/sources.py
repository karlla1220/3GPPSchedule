"""Select, revalidate and stage one coherent timeplan/agenda input bundle."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup
import httpx

from shared import ftp_transport
from shared.portal_meetings import timezone_reference

from .document import FILE

CONFIG_PATH = Path('working_groups/ran_plenary/config.json')
DOWNLOADS = Path('downloads/ran-plenary')
OUTPUT = Path('docs/ran-plenary')
CACHE = Path('.cache/ran-plenary')
TRANSFER = Path('.ci/transfers/ran-plenary')
DEFAULTS = {
    'chair_url': 'https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN/Inbox/Chair/',
    'agenda_url': 'https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN/Agenda/agenda.csv',
    'model': 'gemini-3-flash-preview',
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
    for name in ('chair_url', 'agenda_url'):
        if urlparse(result[name]).scheme not in ('http', 'https'):
            raise ValueError(f'{name} must be an HTTP URL')
    if not isinstance(result['meetings'], dict):
        raise ValueError('meetings must be a dictionary keyed by meeting number')
    return result


def config_hash(cfg):
    return digest(json.dumps(cfg, sort_keys=True).encode())


def client():
    return httpx.Client(follow_redirects=True, timeout=45,
                        headers={'User-Agent': '3GPPSchedule/1.0', 'Accept': '*/*'})


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
        raise ValueError('No RAN timeplan found in Chair listing')
    chosen = max(candidates, key=lambda c: (c['meeting_number'], c['version'], c['uploaded_at'], c['name']))
    old_rank = (previous.get('meeting_number', 0), previous.get('version', 0))
    if (chosen['meeting_number'], chosen['version']) < old_rank:
        raise ValueError('Chair listing would roll back the last successful timeplan')
    return chosen


def fetch_file(http, url, old, local: bytes | None):
    headers = {}
    if old.get('url') == url:
        if old.get('etag'):
            headers['If-None-Match'] = old['etag']
        if old.get('last_modified'):
            headers['If-Modified-Since'] = old['last_modified']
    response = ftp_transport.get(url, http=http, headers=headers)
    if response.status_code == 304:
        if local is not None and digest(local) == old.get('sha256'):
            return local, old
        response = ftp_transport.get(url, http=http)  # Fresh CI runner may lack the original body.
    response.raise_for_status()
    body = response.content
    if not body or len(body) > 30_000_000:
        raise ValueError(f'Empty or excessive input: {url}')
    return body, {'url': url, 'sha256': digest(body), 'etag': response.headers.get('etag'),
                  'last_modified': response.headers.get('last-modified')}


def fetch_bundle(cfg, previous, http, cache_dir=DOWNLOADS / 'inputs') -> Bundle:
    listing = ftp_transport.get(cfg['chair_url'], http=http, listing=True)
    listing.raise_for_status()
    chosen = select_timeplan(listing.text, cfg['chair_url'], previous)
    cached = None
    try:
        cached = Bundle.load(cache_dir)
    except (OSError, ValueError, KeyError):
        pass
    plan, plan_info = fetch_file(http, chosen['url'], previous.get('timeplan', {}),
                                cached.timeplan if cached else None)
    agenda, agenda_info = fetch_file(http, cfg['agenda_url'], previous.get('agenda', {}),
                                    cached.agenda if cached else None)
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


def portal_meeting(http, number: int) -> dict:
    year = date.today().year
    rows = []
    for start in range(0, 1000, 100):
        payload = {'getMeetingsInput': {
            'StartRow': start, 'ResultsPerPage': 100, 'SortBy': 'Date', 'SortAscending': False,
            'StartDate': f'{year-1}-01-01 00:00:00', 'EndDate': f'{year+1}-12-31 23:59:59',
            'Tbs': [373], 'IncludeChildTbs': False, 'IncludeNonTBMeetings': False,
            'Reference': '', 'Registered': False}}
        response = http.post('https://portal.3gpp.org/webservices/Rest/Meetings.svc/GetMeetings',
                             json=payload, headers={'Origin': 'https://portal.3gpp.org',
                                                     'Referer': 'https://portal.3gpp.org/'})
        response.raise_for_status()
        page = response.json()
        if not isinstance(page, list):
            raise ValueError('Invalid Portal response')
        rows.extend(page)
        if len(page) < 100:
            break
    matches = {m['Id']: m for m in rows if m.get('TBId') == 373 and
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
