"""Find a WG's meeting folder in the 3GPP archive without hand-edited config.

The archive keeps each meeting in its own folder (``tsg_ran/WG1_RL1/TSGR1_126b/``)
with the versions from before and after the meeting and the ``agenda.csv``.
In order, a meeting's folder is:

1. the WG's ``meeting_specific_override`` entry for it (set by hand),
2. Portal's ``MtgDocURL`` (already fetched once per run, no request),
3. the WG's ``meeting_specific``, which records what earlier runs found,
4. the first candidate name that lists (``TSGR1_126b``; one request each),
5. the WG root listing (several hundred entries), at most every ROOT_EVERY.

Each run writes the folders it found to ``meeting_specific`` in the WG's
config.json, so the repository shows them; a meeting that is not listed
there was not found. An overridden meeting is looked up and recorded too,
so the maintainer can compare and decide whether to keep the override;
readers take the override for its meeting (ran1 config._derive). CI commits the file with the build. The list is left
out of the WG input fingerprint (ci.py), so writing it rebuilds nothing.

A folder that is not found is not recorded: before a meeting it usually
does not exist yet, and the next run looks again. Absence (403/404/550) is
normal; any other failure propagates.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup
import httpx

from shared import remote_files
from shared.portal_meetings import TB_IDS, meeting_key

# When the root was last listed for a meeting not found (not worth committing).
CACHE_PATH = Path('.cache/meeting-folders.json')
ROOT_EVERY = timedelta(hours=6)
CONFIG_PATHS = {
    'ran1': Path('working_groups/ran1/config.json'),
    'ran2': Path('working_groups/ran2/config.json'),
    'ran3': Path('working_groups/ran3/config.json'),
    'ran4': Path('working_groups/ran4/config.json'),
    'ran-plenary': Path('working_groups/ran_plenary/config.json'),
    'sa1': Path('working_groups/sa1/config.json'),
    'sa2': Path('working_groups/sa2/config.json'),
}
# This run's folders by WG and meeting (overrides excluded), and the lists
# it wrote to meeting_specific. The CI check reports a written list as a
# reason to build and hands it to the build job, which commits it.
_FOUND = {}
_WRITTEN = {}
# Archive root, folder prefix, the spellings of "bis" in folder names in the
# order they are tried, and whether names go on past the meeting. RAN2 used
# both b and bis (TSGR2_133b, TSGR2_135bis); RAN3 writes TSGR3_133-bis.
# SA names add the venue (TSGS1_116_Calgary, TSGS2_177_Prague_2026-10), so a
# name cannot be built from the meeting id: Portal, the record or the root
# listing finds it.
WGS = {
    'ran1': ('https://www.3gpp.org/ftp/tsg_ran/WG1_RL1/', 'TSGR1_', ('b', 'bis'), False),
    'ran2': ('https://www.3gpp.org/ftp/tsg_ran/WG2_RL2/', 'TSGR2_', ('bis', 'b'), False),
    'ran3': ('https://www.3gpp.org/ftp/tsg_ran/WG3_Iu/', 'TSGR3_', ('-bis', 'bis'), False),
    'ran4': ('https://www.3gpp.org/ftp/tsg_ran/WG4_Radio/', 'TSGR4_', ('bis', 'b'), False),
    'ran-plenary': ('https://www.3gpp.org/ftp/tsg_ran/TSG_RAN/', 'TSGR_', ('bis', 'b'), False),
    'sa1': ('https://www.3gpp.org/ftp/tsg_sa/WG1_Serv/', 'TSGS1_', (), True),
    'sa2': ('https://www.3gpp.org/ftp/tsg_sa/WG2_Arch/', 'TSGS2_', (), True),
}
_TSG = {'TSGR': 'ran', 'TSGS': 'sa'}


def root_url(wg_id):
    return WGS[wg_id][0]


def folder_key(wg_id, name):
    """The meeting id a folder name stands for: TSGR2_133b -> ran2#133bis.

    E-meeting folders carry a suffix (TSGR1_104-e, TSGR_88e, TSGR3_107_e,
    TSGR4_101-bis-e) and still name the meeting. SA names go on with the
    venue (TSGS2_177_Prague_2026-10 -> sa2#177); an ad hoc folder
    (TSGS2_175-AH-e_Electronic, TSGS1_112-Ad_Hoc-e) is no regular meeting.
    """
    prefix, named = WGS[wg_id][1], WGS[wg_id][3]
    venue = r'(?:_[^/]*)?' if named else ''
    match = re.fullmatch(re.escape(prefix) + r'(\d+)(?:-?(bis|b))?(?:[-_]?e)?' + venue + '/?', name, re.I)
    if not match:
        return None
    return f'{_TSG[prefix[:4]]}{prefix[4:-1]}#{match[1]}{"bis" if match[2] else ""}'


def candidate_names(wg_id, meeting):
    match = re.fullmatch(r'(?:ran|sa)\d?#(\d+)(bis)?', meeting or '')
    _, prefix, spellings, named = WGS[wg_id]
    if not match or named:
        return []
    if not match[2]:
        return [f'{prefix}{match[1]}']
    return [f'{prefix}{match[1]}{suffix}' for suffix in spellings]


def _folder(url):
    return url if url.endswith('/') else url + '/'


def _archive_url(url):
    """Portal writes ftp.3gpp.org; the rest of the site reads www.3gpp.org/ftp.

    SA rows double a slash (https://www.3gpp.org/ftp//tsg_sa/WG1_Serv/...).
    """
    parts = urlsplit(url)
    path = re.sub(r'/{2,}', '/', parts.path)
    if parts.hostname == 'ftp.3gpp.org':
        return 'https://www.3gpp.org/ftp' + path
    return parts._replace(path=path).geturl()


def _in_archive(wg_id, url, root):
    """The meeting folder within url, if url points into the WG's archive.

    RAN P's MtgDocURL names the Docs/ subfolder (.../TSGR_113/Docs/).
    """
    url = _archive_url(url)
    if not url.startswith(root):
        return None, None
    name = url[len(root):].split('/', 1)[0]
    return (root + name + '/', folder_key(wg_id, name)) if name else (None, None)


def overrides(wg_id, urls):
    """Configured folders by meeting id; a name that is no meeting is skipped."""
    found = {}
    for url in urls or []:
        key = folder_key(wg_id, unquote(url.rstrip('/').rsplit('/', 1)[-1]))
        if key:
            found.setdefault(key, _folder(url))
    return found


def portal_key(row):
    # Portal writes both "3GPPRAN2#135-bis" and "3GPP RAN2#132".
    return meeting_key(re.sub(r'^3GPP\s+', '3GPP', str(row.get('Title') or '').strip()))


def wg_rows(wg_id, rows):
    return [row for row in rows or [] if row.get('TBId') == TB_IDS[wg_id]]


def portal_folders(wg_id, rows, root=None):
    """MtgDocURL by meeting id, for the URLs that point to that meeting's folder."""
    found = {}
    for row in wg_rows(wg_id, rows):
        key = portal_key(row)
        if not key or not isinstance(row.get('MtgDocURL'), str):
            continue
        folder, named = _in_archive(wg_id, row['MtgDocURL'], root or root_url(wg_id))
        if folder and named == key:
            found[key] = folder
    return found


def upcoming(wg_id, rows, today):
    """Meetings that have not ended (by Portal EndDate), earliest first."""
    dated = []
    for row in wg_rows(wg_id, rows):
        key = portal_key(row)
        if key and str(row.get('StartDate') or '') and str(row.get('EndDate') or '')[:10] >= today:
            dated.append((row['StartDate'][:10], key))
    return [key for _, key in sorted(set(dated))]


def new_run():
    _FOUND.clear()
    _WRITTEN.clear()


def written(wg_id):
    """The meeting_specific this run wrote for the WG, or None if unchanged."""
    return _WRITTEN.get(wg_id)


def updates(wg_id):
    folders = written(wg_id)
    return [] if folders is None else [f'meeting folders now {", ".join(folders) or "none"}']


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def write(wg_id, folders):
    """Set meeting_specific in the WG's config.json; True if it changed.

    Only an existing file is changed (a local checkout without config.json
    keeps working), and only when the list differs.
    """
    path = Path(CONFIG_PATHS[wg_id])
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    if not isinstance(config, dict) or config.get('meeting_specific') == folders:
        return False
    config['meeting_specific'] = folders
    _write_json(path, config)
    print(f'[{wg_id}] Recorded meeting_specific: {folders}')
    return True


def _record(wg_id):
    folders = sorted(set(_FOUND[wg_id].values()))
    if write(wg_id, folders):
        _WRITTEN[wg_id] = folders


def _lists(url, http):
    try:
        remote_files.get_listing(url, http=http)
        return True
    except httpx.HTTPError as exc:
        if remote_files.is_absent(exc):
            return False
        raise


def _root_folders(wg_id, root, http):
    found = {}
    try:
        html = remote_files.get_listing(root, http=http).text
    except httpx.HTTPError as exc:
        if remote_files.is_absent(exc):
            return found
        raise
    for anchor in BeautifulSoup(html, 'html.parser').find_all('a', href=True):
        url = urljoin(root, anchor['href'])
        folder, key = _in_archive(wg_id, url, root)
        if key:
            found.setdefault(key, folder)
    return found


def resolve(wg_id, meetings, *, rows=None, override=(), recorded=(), root=None, http=None,
            now=None, record=True):
    """Meeting folder URL by meeting id for each of meetings that has one.

    override is the WG's meeting_specific_override, recorded its
    meeting_specific. With record, the folders found are written back.
    """
    root = _folder(root or root_url(wg_id))
    now = now or datetime.now(timezone.utc)
    manual, portal = overrides(wg_id, override), portal_folders(wg_id, rows, root)
    # A recorded folder under another archive_url is stale.
    known = {k: v for k, v in overrides(wg_id, recorded).items() if v.startswith(root)}
    cache = _read_json(CACHE_PATH)
    root_listed = cache.setdefault(wg_id, {}).setdefault('root_listed', {})
    before = json.dumps(cache, sort_keys=True)
    found = _FOUND.setdefault(wg_id, {})
    result = {}
    for meeting in dict.fromkeys(m for m in meetings if m):
        # An override is what is used, but the folder is still looked up and
        # recorded, so the maintainer can compare and drop the override.
        if meeting in manual:
            result[meeting] = manual[meeting]
        folder = portal.get(meeting) or known.get(meeting)
        if folder is None:
            folder = next((root + name + '/' for name in candidate_names(wg_id, meeting)
                           if _lists(root + name + '/', http)), None)
        if folder is None:
            try:
                last = datetime.fromisoformat(root_listed.get(meeting, ''))
            except ValueError:
                last = None
            if last is None or now - last >= ROOT_EVERY:
                root_listed[meeting] = now.isoformat(timespec='seconds')
                folder = _root_folders(wg_id, root, http).get(meeting)
        if folder is None:
            if meeting not in manual:
                print(f'[{wg_id}] No meeting folder for {meeting} yet; using the sync folder')
            continue
        root_listed.pop(meeting, None)
        found[meeting] = folder
        result.setdefault(meeting, folder)
    if json.dumps(cache, sort_keys=True) != before:
        _write_json(CACHE_PATH, cache)
    if record:
        _record(wg_id)
    return result
