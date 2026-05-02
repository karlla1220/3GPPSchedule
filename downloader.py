"""Download schedule DOCX files from 3GPP FTP server."""

from __future__ import annotations

import re
import time
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import httpx
from bs4 import BeautifulSoup

from models import ScheduleSource

BASE_URL = "https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN1/Inbox/Chair_notes"
INBOX_URL = "https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN1/Inbox/"

# Default local storage root for downloaded artifacts
DOWNLOADS_DIR = Path("downloads")

# Folders in Inbox/ that never contain schedule files
BLACKLISTED_FOLDERS = {"Agenda", "drafts", "Tdoc_list", "Welcome_speech"}

# Supported document extensions (order = preference when extracting from ZIP)
DOCUMENT_EXTENSIONS = (".docx", ".pptx", ".pdf")
# All extensions we accept from remote listings (documents + zip)
SUPPORTED_EXTENSIONS = DOCUMENT_EXTENSIONS + (".zip",)

# Retry configuration for transient server errors
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 5  # seconds

# Patterns that indicate the server returned an error page instead of real content
_SERVICE_ERROR_PATTERNS = (
    "Our services aren't available right now",
    "We're working to restore all services",
    "service unavailable",
)


class ServiceUnavailableError(Exception):
    """Raised when the 3GPP server returns an error page instead of content."""


def _check_response_is_error_page(text: str) -> bool:
    """Return True if the response body looks like an Azure/server error page."""
    for pattern in _SERVICE_ERROR_PATTERNS:
        if pattern.lower() in text.lower():
            return True
    return False


def _validate_html_response(resp: httpx.Response) -> None:
    """Raise ServiceUnavailableError if the response is a server error page.

    The 3GPP FTP server (Azure-hosted) sometimes returns HTTP 200 with an
    HTML error body when the backend is unavailable.  This helper detects
    that situation so callers can retry or fail gracefully.
    """
    # Only inspect text/html responses (binary downloads are fine)
    content_type = resp.headers.get("content-type", "")
    if "text/html" not in content_type and "text/plain" not in content_type:
        return
    body = resp.text
    if _check_response_is_error_page(body):
        raise ServiceUnavailableError(
            f"3GPP server returned error page (HTTP {resp.status_code}): "
            f"{body[:200]}"
        )


def _get_with_retry(
    url: str,
    *,
    timeout: int = 30,
    max_retries: int = _MAX_RETRIES,
    stream: bool = False,
) -> httpx.Response:
    """HTTP GET with automatic retry on transient server errors.

    Retries on:
    - httpx transport / timeout errors
    - HTTP 5xx status codes
    - Azure error pages (200 OK with HTML error body)
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            if stream:
                # Caller is responsible for closing; we return immediately.
                resp = httpx.stream("GET", url, follow_redirects=True, timeout=timeout)
                cm = resp.__enter__()
                cm.raise_for_status()
                return cm
            resp = httpx.get(url, follow_redirects=True, timeout=timeout)
            resp.raise_for_status()
            _validate_html_response(resp)
            return resp
        except ServiceUnavailableError as exc:
            last_exc = exc
            wait = _RETRY_BACKOFF_BASE * attempt
            print(
                f"  Server unavailable (attempt {attempt}/{max_retries}), "
                f"retrying in {wait}s…"
            )
            time.sleep(wait)
        except (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException) as exc:
            last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500:
                # Client errors (404 etc.) are not transient — don't retry
                raise
            wait = _RETRY_BACKOFF_BASE * attempt
            print(
                f"  HTTP error (attempt {attempt}/{max_retries}): {exc!r}, "
                f"retrying in {wait}s…"
            )
            time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def _extract_version_from_name(filename: str) -> int:
    """Extract trailing version number from names like '... v09.zip'.

    Returns -1 when version is not found.
    """
    _ext_pattern = "|".join(re.escape(e) for e in SUPPORTED_EXTENSIONS)
    m = re.search(rf"v(\d+)(?:{_ext_pattern})$", filename, re.IGNORECASE)
    if not m:
        return -1
    return int(m.group(1))


# Known meeting suffixes (case-insensitive).
# Only bis, e, and adhoc have been observed in practice.
_MEETING_SUFFIXES = r"(?:bis|e|adhoc)"

# Pattern to extract meeting identifiers like RAN1#124, RAN1#124bis,
# RAN1#124-bis, RAN1#124 bis, etc.  The suffix part is optional and may
# be separated by a hyphen or space.
_MEETING_ID_PATTERN = re.compile(
    rf"(RAN\d+#\d+)(?:[- ]?({_MEETING_SUFFIXES}))?",
    re.IGNORECASE,
)


def _extract_meeting_id(filename: str) -> str | None:
    """Extract a normalised meeting identifier from a filename.

    Examples:
        'RAN1#124 online and offline schedules - v02.docx'  → 'ran1#124'
        'RAN1#124bis schedule for Hiroki_v07.docx'          → 'ran1#124bis'
        'RAN1#124-bis schedule - v01.docx'                  → 'ran1#124bis'
        'RAN1#124 bis schedule - v01.docx'                  → 'ran1#124bis'
        'custom schedule name.docx'                         → None

    The returned string is always lowercased with any hyphen/space between
    the number and the suffix removed (e.g. '124-bis' → '124bis').
    """
    m = _MEETING_ID_PATTERN.search(filename)
    if not m:
        return None
    base = m.group(1).lower()
    suffix = (m.group(2) or "").lower()
    return f"{base}{suffix}"


# Regular plenary meeting ids look like ``ran1#124`` or ``ran1#124bis``.
# Ad-hoc / electronic / other irregular meetings are not totally-ordered
# by name, so we fall back to upload date for them.
_REGULAR_MEETING_RE = re.compile(r"^ran(\d+)#(\d+)(bis)?$", re.IGNORECASE)


def _meeting_rank(meeting_id: str | None) -> tuple[int, int, int] | None:
    """Return a sortable rank for a regular plenary meeting id.

    Higher tuple = later meeting. Returns ``None`` for irregular ids
    (ad-hoc, e-meeting, unknown), signalling that the caller should use
    upload date as the ordering criterion instead.

    Ordering examples:
        ran1#124    → (1, 124, 0)
        ran1#124bis → (1, 124, 1)   (bis follows the base meeting)
        ran1#125    → (1, 125, 0)
    """
    if meeting_id is None:
        return None
    m = _REGULAR_MEETING_RE.match(meeting_id)
    if not m:
        return None
    series = int(m.group(1))
    number = int(m.group(2))
    suffix_rank = 1 if m.group(3) else 0
    return (series, number, suffix_rank)


def _pick_latest_in_meeting_group(
    files: list[dict],
    label: str = "schedule",
    preferred_meeting_id: str | None = None,
) -> dict:
    """Select the best file using meeting-aware grouping.

    1. Group *files* by meeting identifier.
    2. Choose the "current" meeting group:
       - If ``preferred_meeting_id`` is supplied and at least one file
         matches it, that group wins (hard constraint — used to align
         vice-chair folders with the Chair_notes decision).
       - Otherwise, prefer the regular plenary id with the highest
         meeting rank (e.g. ``ran1#124bis`` beats ``ran1#124``;
         ``ran1#125`` beats both).  This means a stray draft labelled
         with an *older* meeting id cannot displace the current one,
         and a *newer* meeting id automatically takes over as soon as
         it appears.
       - If no regular ids are present (only ad-hoc / unknown), fall
         back to the group whose most-recent ``uploaded_at`` is newest.
    3. Within the chosen group pick the file with the highest version
       number, using ``uploaded_at`` as a tiebreaker.

    All files must have a non-None ``uploaded_at``.
    """
    from collections import defaultdict

    groups: dict[str | None, list[dict]] = defaultdict(list)
    for f in files:
        mid = _extract_meeting_id(f["name"])
        groups[mid].append(f)

    def _group_max_ts(group: list[dict]) -> datetime:
        return max(f["uploaded_at"] for f in group)

    current_mid: str | None
    if preferred_meeting_id is not None and preferred_meeting_id in groups:
        current_mid = preferred_meeting_id
    else:
        regular = {mid: grp for mid, grp in groups.items() if _meeting_rank(mid) is not None}
        if regular:
            # Highest-ranked regular meeting wins, regardless of upload time.
            current_mid = max(regular, key=lambda m: _meeting_rank(m))  # type: ignore[arg-type]
        else:
            # Only irregular / unparseable ids → fall back to upload date.
            current_mid = max(groups, key=lambda mid: _group_max_ts(groups[mid]))
        if (
            preferred_meeting_id is not None
            and current_mid != preferred_meeting_id
        ):
            pref_rank = _meeting_rank(preferred_meeting_id)
            cur_rank = _meeting_rank(current_mid)
            if (
                pref_rank is not None
                and cur_rank is not None
                and cur_rank > pref_rank
            ):
                print(
                    f"  Newer meeting detected for {label}: "
                    f"{preferred_meeting_id} → {current_mid}"
                )
            else:
                print(
                    f"  Note: preferred meeting {preferred_meeting_id!r} not found "
                    f"among {label} files; falling back to {current_mid!r}"
                )
    current_group = groups[current_mid]

    latest = max(
        current_group,
        key=lambda x: (
            _extract_version_from_name(x["name"]),
            x["uploaded_at"],
            x["name"].lower(),
        ),
    )
    print(
        f"Latest {label} (meeting={current_mid}, "
        f"version={_extract_version_from_name(latest['name'])}, "
        f"uploaded={latest['uploaded_at']}): {latest['name']}"
    )
    return latest


def list_remote_files(url: str = BASE_URL) -> list[dict]:
    """Fetch the FTP directory listing and return file info with upload timestamps.

    Each returned dict has keys: name, url, uploaded_at (datetime | None).
    """
    resp = _get_with_retry(url)

    soup = BeautifulSoup(resp.text, "html.parser")
    files = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue

        # td[2] = filename link, td[3] = upload date/time
        link = tds[2].find("a", href=True)
        if link is None:
            continue

        href = link["href"]
        if not any(href.lower().endswith(ext) for ext in SUPPORTED_EXTENSIONS):
            continue

        # Extract and decode filename
        name = unquote(href.split("/")[-1])
        full_url = href if href.startswith("http") else f"{url}/{href}"

        # Parse upload timestamp (format: "2026/02/08 22:20")
        date_text = tds[3].get_text(strip=True)
        uploaded_at = None
        try:
            uploaded_at = datetime.strptime(date_text, "%Y/%m/%d %H:%M")
        except (ValueError, TypeError):
            pass

        files.append({"name": name, "url": full_url, "uploaded_at": uploaded_at})
    return files


def find_latest_schedule(
    files: list[dict],
    preferred_meeting_id: str | None = None,
) -> dict | None:
    """Find the latest schedule file using meeting-aware grouping.

    Looks for files containing 'schedule' in the name.
    Supports .docx, .pptx, .pdf, and .zip files.

    Selection strategy (in order):
    1. Group files by meeting identifier (e.g. RAN1#124bis).
    2. Pick the group whose most-recent upload timestamp is newest
       (= the current meeting).
    3. Within that group, pick the file with the highest version number.
       Upload timestamp is a secondary tiebreaker.

    This ensures that old high-version files from a previous meeting
    do not displace the current meeting's schedule, while still
    preferring the highest version within the current meeting.

    Falls back to version number (then timestamp) if timestamps are
    unavailable.
    """
    schedule_files = [f for f in files if "schedule" in f["name"].lower()]

    if not schedule_files:
        return None

    # Use meeting-aware grouping when timestamps are available.
    files_with_ts = [f for f in schedule_files if f.get("uploaded_at") is not None]
    if files_with_ts:
        return _pick_latest_in_meeting_group(
            files_with_ts,
            label="schedule",
            preferred_meeting_id=preferred_meeting_id,
        )

    # Fallback: sort by version number in filename
    versioned = []
    for f in schedule_files:
        version = _extract_version_from_name(f["name"])
        if version >= 0:
            versioned.append({**f, "version": version})
    if versioned:
        return max(versioned, key=lambda x: x["version"])

    # Last resort: return the first schedule file
    return schedule_files[0]


def get_remote_schedule_info(url: str = BASE_URL) -> dict | None:
    """Return metadata (name + uploaded_at) of the latest schedule on FTP.

    This is a lightweight check — only fetches the directory listing,
    does NOT download any file.  Used for change detection.
    """
    files = list_remote_files(url)
    latest = find_latest_schedule(files)
    if latest is None:
        return None
    return {
        "name": latest["name"],
        "uploaded_at": (
            latest["uploaded_at"].isoformat() if latest.get("uploaded_at") else None
        ),
    }


def extract_document_from_zip(zip_path: Path) -> Path | None:
    """Extract a document file from a ZIP archive.

    Looks for .docx, .pptx, or .pdf files inside the ZIP (in that
    preference order).  Returns the path of the extracted file, or
    None if no supported document is found.
    """
    if not zipfile.is_zipfile(zip_path):
        print(f"Warning: {zip_path.name} is not a valid ZIP file")
        return None

    with zipfile.ZipFile(zip_path, "r") as zf:
        # Collect candidate entries (skip directories and __MACOSX junk)
        candidates: list[tuple[int, str]] = []
        for entry in zf.namelist():
            if entry.startswith("__MACOSX") or entry.endswith("/"):
                continue
            lower = entry.lower()
            for priority, ext in enumerate(DOCUMENT_EXTENSIONS):
                if lower.endswith(ext):
                    candidates.append((priority, entry))
                    break

        if not candidates:
            print(f"Warning: No document files found inside {zip_path.name}")
            return None

        # Pick best candidate (lowest priority number = most preferred)
        candidates.sort(key=lambda x: x[0])
        chosen = candidates[0][1]

        # Extract to the same directory as the ZIP
        dest_dir = zip_path.parent
        extracted_name = Path(chosen).name  # flatten nested paths
        extracted_path = dest_dir / extracted_name

        with zf.open(chosen) as src, open(extracted_path, "wb") as dst:
            dst.write(src.read())

        print(f"Extracted from ZIP: {extracted_name}")
        return extracted_path


def download_file(url: str, dest_path: Path) -> Path:
    """Download a file from URL to dest_path.

    Validates the downloaded content to ensure we didn't receive a
    server error page disguised as a successful response.
    """
    print(f"Downloading: {url}")
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=60) as resp:
                resp.raise_for_status()
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with open(dest_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=8192):
                        f.write(chunk)
            # Validate: error pages are typically small HTML files
            _validate_downloaded_file(dest_path)
            print(f"Saved to: {dest_path}")
            return dest_path
        except ServiceUnavailableError as exc:
            last_exc = exc
            # Remove the corrupt download
            dest_path.unlink(missing_ok=True)
            wait = _RETRY_BACKOFF_BASE * attempt
            print(
                f"  Downloaded file is a server error page "
                f"(attempt {attempt}/{_MAX_RETRIES}), retrying in {wait}s…"
            )
            time.sleep(wait)
        except (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException) as exc:
            last_exc = exc
            dest_path.unlink(missing_ok=True)
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500:
                raise
            wait = _RETRY_BACKOFF_BASE * attempt
            print(
                f"  Download error (attempt {attempt}/{_MAX_RETRIES}): {exc!r}, "
                f"retrying in {wait}s…"
            )
            time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def _validate_downloaded_file(path: Path) -> None:
    """Check a downloaded file is not a server error page.

    When the Azure-hosted 3GPP server is unavailable it may return
    HTTP 200 with a small HTML error body.  If we saved that to disk,
    detect it here and raise ServiceUnavailableError.
    """
    # Only inspect small files — real schedule docs are at least a few KB
    if path.stat().st_size > 4096:
        return
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    if _check_response_is_error_page(text):
        raise ServiceUnavailableError(
            f"Downloaded file is a server error page: {text[:200]}"
        )


def download_and_resolve(url: str, dest_path: Path) -> Path:
    """Download a file and, if it is a ZIP, extract the document inside.

    Returns the path to the final usable document (docx / pptx / pdf).
    """
    downloaded = download_file(url, dest_path)

    if downloaded.suffix.lower() == ".zip":
        extracted = extract_document_from_zip(downloaded)
        if extracted is not None:
            return extracted
        # ZIP contained nothing useful — return as-is (caller will handle)
        print("Warning: ZIP did not contain a supported document; using ZIP path")

    return downloaded


def download_latest_schedule(
    dest_dir: Path = DOWNLOADS_DIR / "Chair_notes",
) -> Path:
    """Download the latest schedule from 3GPP FTP.

    Supports .docx, .pptx, .pdf, and .zip (auto-extracted).
    Returns the path to the downloaded (or already existing) document.
    """
    files = list_remote_files()
    latest = find_latest_schedule(files)

    if latest is None:
        raise FileNotFoundError("No schedule files found on FTP server")

    dest_path = dest_dir / latest["name"]

    if dest_path.exists():
        # If it's a ZIP that was already downloaded, try to find the
        # previously extracted document next to it.
        if dest_path.suffix.lower() == ".zip":
            extracted = _find_extracted_document(dest_path)
            if extracted:
                print(f"Using previously extracted: {extracted}")
                return extracted
            # Re-extract
            extracted = extract_document_from_zip(dest_path)
            if extracted:
                return extracted
        print(f"File already exists: {dest_path}")
        return dest_path

    return download_and_resolve(latest["url"], dest_path)


def find_local_latest_schedule(
    dest_dir: Path = DOWNLOADS_DIR / "Chair_notes",
) -> Path | None:
    """Find the latest schedule document in the local directory by modification time.

    Searches for .docx, .pptx, and .pdf files containing 'schedule' in the name.
    Uses file modification time (mtime) to determine the latest file,
    since filenames may use meeting names instead of version numbers.
    """
    schedule_files = [
        f
        for ext in DOCUMENT_EXTENSIONS
        for f in dest_dir.glob(f"*{ext}")
        if "schedule" in f.name.lower()
    ]

    if not schedule_files:
        return None

    latest = max(schedule_files, key=lambda f: f.stat().st_mtime)
    print(f"Latest local schedule (by mtime): {latest.name}")
    return latest


def find_local_vice_chair_schedules(
    base_dir: Path = DOWNLOADS_DIR,
) -> dict[str, Path]:
    """Discover vice-chair schedule files from local directories.

    Scans sibling directories (e.g. Hiroki_notes/, Sorour_notes/) for
    schedule documents, mirroring the remote discover_schedule_sources logic
    but without any network access.

    Returns a dict mapping person_name → local DOCX path.
    """
    vice_chair_paths: dict[str, Path] = {}

    for entry in base_dir.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name.startswith("__"):
            continue
        if entry.name in BLACKLISTED_FOLDERS:
            continue

        # Skip Chair_notes (main schedule) and non-person folders
        person_name = _extract_person_name(entry.name)
        if person_name is None:
            continue

        # Find latest schedule file in this folder
        local = find_local_latest_schedule(entry)
        if local is not None:
            vice_chair_paths[person_name] = local
            print(f"  Local vice-chair schedule: {local} [{person_name}]")

    return vice_chair_paths


def find_latest_chair_notes(
    files: list[dict],
    preferred_meeting_id: str | None = None,
) -> dict | None:
    """Find the latest Chair notes file using meeting-aware grouping.

    Looks for files containing 'chair note' (case-insensitive) in the name.
    Supports .docx, .pptx, .pdf, and .zip files.

    Uses the same meeting-aware grouping strategy as find_latest_schedule():
    group by meeting ID → pick latest meeting → highest version within group.
    """
    chair_files = [
        f for f in files
        if (
            "chair note" in f["name"].lower()
            or "chair_note" in f["name"].lower()
            or "chairman note" in f["name"].lower()
            or "chairman_note" in f["name"].lower()
        )
    ]

    if not chair_files:
        return None

    # Use meeting-aware grouping when timestamps are available.
    files_with_ts = [f for f in chair_files if f.get("uploaded_at") is not None]
    if files_with_ts:
        return _pick_latest_in_meeting_group(
            files_with_ts,
            label="Chair notes",
            preferred_meeting_id=preferred_meeting_id,
        )

    # Fallback: sort by version number in filename
    versioned = []
    for f in chair_files:
        version = _extract_version_from_name(f["name"])
        if version >= 0:
            versioned.append({**f, "version": version})
    if versioned:
        return max(versioned, key=lambda x: x["version"])

    return chair_files[0]


def _chair_notes_url_from_inbox(url: str) -> str:
    """Return the Chair_notes folder URL for an Inbox URL."""
    return f"{url.rstrip('/')}/Chair_notes"


def get_latest_chair_notes_info(
    url: str | None = None,
    *,
    urls: list[str] | None = None,
    extra_folders: list[dict] | None = None,
) -> dict | None:
    """Return metadata of the latest Chair notes across configured sources.

    Searches the ``Chair_notes`` subfolder beneath each configured inbox URL,
    and also scans every configured ``extra_folder`` directly. This mirrors the
    schedule-source lookup so location/timezone detection can follow the same
    config.json settings as the main schedule download.
    """
    if urls is None:
        urls = [url] if url is not None else [INBOX_URL]

    candidates: list[dict] = []

    for inbox_url in urls:
        chair_url = _chair_notes_url_from_inbox(inbox_url)
        try:
            files = list_remote_files(chair_url)
        except Exception as e:
            print(f"Warning: Could not list Chair notes at {chair_url}: {e}")
            continue

        latest = find_latest_chair_notes(files)
        if latest is not None:
            candidates.append({**latest, "source_url": chair_url})

    for folder in extra_folders or []:
        folder_url = folder["url"]
        folder_name = folder["name"]
        try:
            files = list_remote_files(folder_url)
        except Exception as e:
            print(f"Warning: Could not list Chair notes in extra folder {folder_name}/: {e}")
            continue

        latest = find_latest_chair_notes(files)
        if latest is not None:
            candidates.append({**latest, "source_url": folder_url})

    if not candidates:
        return None

    latest = find_latest_chair_notes(candidates)
    if latest is not None and latest.get("source_url"):
        print(f"Latest Chair notes source: {latest['source_url']}")
    return latest


def download_latest_chair_notes(
    dest_dir: Path = DOWNLOADS_DIR / "Chair_notes",
    url: str | None = None,
    *,
    urls: list[str] | None = None,
    extra_folders: list[dict] | None = None,
) -> Path | None:
    """Download the latest Chair notes from 3GPP FTP.

    Supports .docx, .pptx, .pdf, and .zip (auto-extracted).
    Returns the path to the downloaded file, or None if not found.
    """
    latest = get_latest_chair_notes_info(
        url,
        urls=urls,
        extra_folders=extra_folders,
    )
    if latest is None:
        print("No Chair notes file found on FTP")
        return None

    dest_path = dest_dir / latest["name"]

    if dest_path.exists():
        if dest_path.suffix.lower() == ".zip":
            extracted = _find_extracted_document(dest_path)
            if extracted:
                print(f"Using previously extracted: {extracted}")
                return extracted
            extracted = extract_document_from_zip(dest_path)
            if extracted:
                return extracted
        print(f"Chair notes already exists: {dest_path}")
        return dest_path

    try:
        return download_and_resolve(latest["url"], dest_path)
    except Exception as e:
        print(f"Warning: Failed to download Chair notes: {e}")
        return None


# ── Agenda discovery & download ────────────────────────────────

def find_latest_agenda(files: list[dict]) -> dict | None:
    """Pick the newest agenda candidate from an Agenda-folder listing.

    Agenda files in 3GPP per-meeting folders are typically a single
    TDoc archive (e.g. ``R1-2601750.zip``) containing the agenda DOCX.
    The filename is *not* a reliable indicator, so we simply pick the
    most recently uploaded ZIP/DOCX/PPTX/PDF.

    Selection order:
        1. Highest ``uploaded_at`` among supported documents/archives.
        2. If no timestamps are available, return the first entry.
    """
    candidates = [
        f for f in files
        if any(f["name"].lower().endswith(ext) for ext in SUPPORTED_EXTENSIONS)
    ]
    if not candidates:
        return None

    with_ts = [f for f in candidates if f.get("uploaded_at") is not None]
    if with_ts:
        return max(with_ts, key=lambda f: f["uploaded_at"])
    return candidates[0]


def get_latest_agenda_info(agenda_urls: list[str]) -> dict | None:
    """Return metadata of the latest agenda across ``agenda_urls``.

    Each URL is expected to be a meeting-specific Agenda folder URL such
    as ``https://www.3gpp.org/ftp/tsg_ran/WG1_RL1/TSGR1_124b/Agenda/``.
    """
    candidates: list[dict] = []
    for agenda_url in agenda_urls:
        try:
            files = list_remote_files(agenda_url)
        except Exception as e:
            print(f"Warning: Could not list agenda at {agenda_url}: {e}")
            continue
        latest = find_latest_agenda(files)
        if latest is not None:
            candidates.append({**latest, "source_url": agenda_url})

    if not candidates:
        return None

    latest = find_latest_agenda(candidates)
    if latest is not None and latest.get("source_url"):
        print(f"Latest agenda source: {latest['source_url']}")
    return latest


def download_latest_agenda(
    agenda_urls: list[str],
    dest_dir: Path = DOWNLOADS_DIR / "Agenda",
) -> Path | None:
    """Download the latest agenda document from configured agenda URLs.

    Supports .docx, .pptx, .pdf, and .zip (auto-extracted).
    Returns the path to the downloaded (or already-cached) document,
    or None if nothing suitable was found.
    """
    if not agenda_urls:
        return None

    latest = get_latest_agenda_info(agenda_urls)
    if latest is None:
        print("No agenda file found on FTP")
        return None

    dest_path = dest_dir / latest["name"]

    if dest_path.exists():
        if dest_path.suffix.lower() == ".zip":
            extracted = _find_extracted_document(dest_path)
            if extracted:
                print(f"Using previously extracted agenda: {extracted}")
                return extracted
            extracted = extract_document_from_zip(dest_path)
            if extracted:
                return extracted
        print(f"Agenda already exists: {dest_path}")
        return dest_path

    try:
        return download_and_resolve(latest["url"], dest_path)
    except Exception as e:
        print(f"Warning: Failed to download agenda: {e}")
        return None


def find_local_latest_agenda(
    dest_dir: Path = DOWNLOADS_DIR / "Agenda",
) -> Path | None:
    """Return the newest locally-cached agenda document, if any."""
    if not dest_dir.exists():
        return None
    candidates: list[Path] = []
    for ext in DOCUMENT_EXTENSIONS:
        candidates.extend(dest_dir.glob(f"*{ext}"))
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.stat().st_mtime)


def _find_extracted_document(zip_path: Path) -> Path | None:
    """Look for documents that may have been previously extracted from a ZIP.

    Searches the same directory for document files whose stem matches or
    contains the ZIP's stem (heuristic).  Returns the newest match.
    """
    parent = zip_path.parent
    candidates = []
    for ext in DOCUMENT_EXTENSIONS:
        for f in parent.glob(f"*{ext}"):
            candidates.append(f)
    if not candidates:
        return None
    # Return the newest document file
    return max(candidates, key=lambda f: f.stat().st_mtime)


# ── Multi-folder discovery & download ──────────────────────────

def list_inbox_subfolders(url: str = INBOX_URL) -> list[dict]:
    """Fetch the Inbox directory listing and return subfolder info.

    Each returned dict has keys: name, url, uploaded_at (datetime | None).
    Only directories are returned (no files).
    """
    resp = _get_with_retry(url)

    soup = BeautifulSoup(resp.text, "html.parser")
    folders = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue

        link = tds[2].find("a", href=True)
        if link is None:
            continue

        href = link["href"]
        # Folders end with / and have no file extension
        if not href.rstrip("/").split("/")[-1]:
            continue
        # Skip file-like entries (have extensions)
        basename = unquote(href.rstrip("/").split("/")[-1])
        if "." in basename:
            continue

        full_url = href if href.startswith("http") else f"{url.rstrip('/')}/{basename}/"

        date_text = tds[3].get_text(strip=True)
        uploaded_at = None
        try:
            uploaded_at = datetime.strptime(date_text, "%Y/%m/%d %H:%M")
        except (ValueError, TypeError):
            pass

        folders.append({"name": basename, "url": full_url, "uploaded_at": uploaded_at})

    return folders


def _extract_person_name(folder_name: str) -> str | None:
    """Extract the person's name from a folder like 'Hiroki_notes' or 'David_sessions'.

    Returns None for non-person folders (e.g. 'Chair_notes').
    """
    lower = folder_name.lower()
    # Chair_notes is the main schedule, not a person
    if "chair" in lower:
        return None

    # Strip common suffixes
    for suffix in ("_notes", "_sessions", "_session"):
        if lower.endswith(suffix):
            return folder_name[: -len(suffix)]

    return None


def _extract_person_from_filename(filename: str) -> str | None:
    """Try to extract a person name from a schedule filename.

    E.g. 'RAN1#124 schedule for Hiroki Adhoc2 sessions_v02.docx' → 'Hiroki'
    E.g. 'RAN1#124 online and offline schedules - Sorour sessions - v01.docx' → 'Sorour'
    """
    # Pattern: "schedule for <Name>"
    m = re.search(r"schedule\s+for\s+(\w+)", filename, re.IGNORECASE)
    if m:
        return m.group(1)

    # Pattern: "schedules - <Name> sessions"
    m = re.search(r"schedules?\s*-\s*(\w+)\s+sessions?", filename, re.IGNORECASE)
    if m:
        return m.group(1)

    return None


def _discover_from_inbox(url: str) -> list[ScheduleSource]:
    """Scan a single inbox URL for schedule sources (subfolders + stray root files)."""
    sources: list[ScheduleSource] = []
    chair_notes_url = f"{url.rstrip('/')}/Chair_notes"

    # 1. Scan subfolders
    try:
        subfolders = list_inbox_subfolders(url)
    except Exception as e:
        print(f"Warning: Failed to list Inbox subfolders at {url}: {e}")
        return _fallback_chair_only(chair_notes_url)

    # First pass: locate the Chair_notes folder so we can pin the
    # current meeting id and use it as a hint for vice-chair folders
    # (where a stale or mislabelled file uploaded after the real latest
    # one would otherwise hijack the selection).
    main_meeting_id: str | None = None
    main_folder_name: str | None = None
    for folder in subfolders:
        if folder["name"] in BLACKLISTED_FOLDERS:
            continue
        person_name = _extract_person_name(folder["name"])
        if "chair" in folder["name"].lower() and person_name is None:
            try:
                chair_files = list_remote_files(folder["url"])
            except Exception as e:
                print(f"  Warning: Cannot access {folder['name']}/: {e}")
                break
            chair_latest = find_latest_schedule(chair_files)
            if chair_latest is not None:
                main_meeting_id = _extract_meeting_id(chair_latest["name"])
                main_folder_name = folder["name"]
                if main_meeting_id is not None:
                    print(f"  Current meeting (from {folder['name']}/): {main_meeting_id}")
                sources.append(
                    ScheduleSource(
                        folder_name=folder["name"],
                        person_name=None,
                        is_main=True,
                        file_info=chair_latest,
                    )
                )
                print(
                    f"  Found schedule in {folder['name']}/: "
                    f"{chair_latest['name']} [MAIN]"
                )
            break

    for folder in subfolders:
        if folder["name"] in BLACKLISTED_FOLDERS:
            continue
        # Already handled in the first pass.
        if folder["name"] == main_folder_name:
            continue

        folder_url = folder["url"]
        try:
            files = list_remote_files(folder_url)
        except Exception as e:
            print(f"  Warning: Cannot access {folder['name']}/: {e}")
            continue

        latest = find_latest_schedule(files, preferred_meeting_id=main_meeting_id)
        if latest is None:
            continue

        person_name = _extract_person_name(folder["name"])
        is_main = "chair" in folder["name"].lower() and person_name is None

        sources.append(
            ScheduleSource(
                folder_name=folder["name"],
                person_name=person_name,
                is_main=is_main,
                file_info=latest,
            )
        )
        label = "MAIN" if is_main else person_name or folder["name"]
        print(f"  Found schedule in {folder['name']}/: {latest['name']} [{label}]")

    # 2. Also check Inbox root for stray schedule files (with person names)
    try:
        root_files = list_remote_files(url)
        root_schedules = [f for f in root_files if "schedule" in f["name"].lower()]
        for sf in root_schedules:
            person = _extract_person_from_filename(sf["name"])
            if person is None:
                continue
            existing_persons = {s.person_name for s in sources if s.person_name}
            if person in existing_persons:
                continue
            # Honour the current meeting hint for stray files too: skip
            # files that clearly belong to an *older* meeting.  A newer
            # meeting id is allowed through (it would override Chair's
            # decision on the next run via state update).
            if main_meeting_id is not None:
                file_mid = _extract_meeting_id(sf["name"])
                main_rank = _meeting_rank(main_meeting_id)
                file_rank = _meeting_rank(file_mid)
                if (
                    file_mid is not None
                    and main_rank is not None
                    and file_rank is not None
                    and file_rank < main_rank
                ):
                    print(
                        f"  Skipping Inbox root file from older meeting "
                        f"({file_mid} < {main_meeting_id}): {sf['name']}"
                    )
                    continue
            sources.append(
                ScheduleSource(
                    folder_name="Inbox",
                    person_name=person,
                    is_main=False,
                    file_info=sf,
                )
            )
            print(f"  Found schedule in Inbox root: {sf['name']} [{person}]")
    except Exception as e:
        print(f"  Warning: Cannot scan Inbox root for schedule files: {e}")

    if not any(s.is_main for s in sources):
        print(f"  Warning: No main schedule (Chair_notes) found at {url}, using fallback")
        fallback = _fallback_chair_only(chair_notes_url)
        if fallback:
            sources.extend(fallback)

    return sources


def _source_from_extra_folder(folder: dict) -> ScheduleSource | None:
    """Build a ScheduleSource from a manually-configured extra folder entry."""
    folder_url = folder["url"]
    folder_name = folder["name"]
    try:
        files = list_remote_files(folder_url)
    except Exception as e:
        print(f"  Warning: Cannot access extra folder {folder_name}/: {e}")
        return None

    latest = find_latest_schedule(files)
    if latest is None:
        print(f"  No schedule file in extra folder {folder_name}/")
        return None

    person_name = folder.get("person_name") or _extract_person_name(folder_name)
    is_main = folder.get("is_main", False)
    label = "MAIN" if is_main else person_name or folder_name
    print(f"  Found schedule in [extra] {folder_name}/: {latest['name']} [{label}]")
    return ScheduleSource(
        folder_name=folder_name,
        person_name=person_name,
        is_main=is_main,
        file_info=latest,
    )


def _dedup_sources(sources: list[ScheduleSource]) -> list[ScheduleSource]:
    """Resolve collisions when aggregating across multiple inboxes.

    - At most one ``is_main`` survives (newest ``uploaded_at`` wins).
    - Vice-chair sources with the same ``person_name`` collapse to the newest.
    - Non-main, no-person entries with the same ``folder_name`` collapse to the newest.
    """
    from datetime import datetime

    def ts(s: ScheduleSource) -> datetime:
        v = s.file_info.get("uploaded_at")
        return v if isinstance(v, datetime) else datetime.min

    main: ScheduleSource | None = None
    by_person: dict[str, ScheduleSource] = {}
    by_folder: dict[str, ScheduleSource] = {}

    for s in sources:
        if s.is_main:
            if main is None or ts(s) > ts(main):
                if main is not None:
                    main.is_main = False
                    by_folder[main.folder_name] = main
                main = s
            else:
                s.is_main = False
                key = s.folder_name
                if key not in by_folder or ts(s) > ts(by_folder[key]):
                    by_folder[key] = s
        elif s.person_name:
            existing = by_person.get(s.person_name)
            if existing is None or ts(s) > ts(existing):
                by_person[s.person_name] = s
        else:
            key = s.folder_name
            if key not in by_folder or ts(s) > ts(by_folder[key]):
                by_folder[key] = s

    out: list[ScheduleSource] = []
    if main is not None:
        out.append(main)
    out.extend(by_person.values())
    out.extend(by_folder.values())
    return out


def discover_schedule_sources(
    url: str | None = None,
    *,
    urls: list[str] | None = None,
    extra_folders: list[dict] | None = None,
) -> list[ScheduleSource]:
    """Discover schedule sources across one or more inbox URLs and extra folders.

    ``urls`` accepts a list of inbox URLs to aggregate from.  The legacy
    single-URL ``url`` parameter is still honoured for backwards compatibility.
    ``extra_folders`` is a list of dicts with keys ``url`` (required),
    ``name``, ``person_name``, ``is_main`` — manually-specified folders
    to include alongside the inbox scans.
    """
    if urls is None:
        urls = [url] if url is not None else [INBOX_URL]

    all_sources: list[ScheduleSource] = []
    for u in urls:
        all_sources.extend(_discover_from_inbox(u))

    for folder in extra_folders or []:
        src = _source_from_extra_folder(folder)
        if src is not None:
            all_sources.append(src)

    return _dedup_sources(all_sources)


def _fallback_chair_only(chair_url: str = BASE_URL) -> list[ScheduleSource]:
    """Fallback: discover only the main Chair_notes schedule."""
    try:
        files = list_remote_files(chair_url)
        latest = find_latest_schedule(files)
        if latest:
            return [
                ScheduleSource(
                    folder_name="Chair_notes",
                    person_name=None,
                    is_main=True,
                    file_info=latest,
                )
            ]
    except Exception:
        pass
    return []


def download_schedule_source(
    source: ScheduleSource,
    base_dir: Path = DOWNLOADS_DIR,
) -> Path | None:
    """Download a single schedule source to a local directory.

    Saves to <base_dir>/<folder_name>/<filename>.
    Returns the path to the downloaded (or existing) document,
    or None on failure.
    """
    dest_dir = base_dir / source.folder_name
    dest_path = dest_dir / source.file_info["name"]

    if dest_path.exists():
        if dest_path.suffix.lower() == ".zip":
            extracted = _find_extracted_document(dest_path)
            if extracted:
                return extracted
            extracted = extract_document_from_zip(dest_path)
            if extracted:
                return extracted
        return dest_path

    try:
        result = download_and_resolve(source.file_info["url"], dest_path)
        return result
    except Exception as e:
        print(f"  Warning: Failed to download {source.file_info['name']}: {e}")
        return None


def download_all_schedules(
    sources: list[ScheduleSource],
    base_dir: Path = DOWNLOADS_DIR,
) -> tuple[Path | None, dict[str, Path]]:
    """Download all schedule sources.

    Returns:
        (main_path, vice_chair_paths) where:
        - main_path: Path to the main schedule DOCX (or None)
        - vice_chair_paths: dict mapping person_name → local DOCX path
    """
    main_path: Path | None = None
    vice_chair_paths: dict[str, Path] = {}

    for source in sources:
        local = download_schedule_source(source, base_dir)
        if local is None:
            continue
        source.local_path = local

        if source.is_main:
            main_path = local
        elif source.person_name:
            vice_chair_paths[source.person_name] = local

    return main_path, vice_chair_paths


def save_schedule_state(
    sources: list[ScheduleSource],
    state_path: Path = Path("docs/.schedule_state.json"),
    *,
    meeting_id: str | None = None,
    timezone: str | None = None,
) -> None:
    """Persist FTP state from already-fetched ScheduleSource objects.

    Called after a successful build so the next check job can compare
    without re-fetching from FTP.

    Optionally stores ``meeting_id`` (normalised, e.g. "ran1#124bis") and
    ``timezone`` (IANA, e.g. "Europe/Malta") so that expensive per-meeting
    operations (like LLM timezone detection) are only performed once.
    """
    import json

    info: list[dict] = []
    for s in sources:
        uploaded_at = s.file_info.get("uploaded_at")
        info.append({
            "folder": s.folder_name,
            "name": s.file_info["name"],
            "uploaded_at": (
                uploaded_at.isoformat()
                if isinstance(uploaded_at, datetime)
                else uploaded_at  # already a string or None
            ),
        })
    info.sort(key=lambda x: x.get("folder", ""))

    state: dict = {"files": info}
    if meeting_id is not None:
        state["meeting_id"] = meeting_id
    if timezone is not None:
        state["timezone"] = timezone

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(f"Schedule state saved ({len(info)} source(s)) → {state_path}")


def load_schedule_state(
    state_path: Path = Path("docs/.schedule_state.json"),
) -> dict:
    """Load persisted schedule state.

    Returns a dict with optional keys ``files`` (list[dict]), ``meeting_id``
    (str) and ``timezone`` (str).  Returns an empty dict when the file is
    missing or unparsable.

    Handles migration from the legacy list format (pre-meeting-id) by
    wrapping a bare list in ``{"files": <list>}``.
    """
    import json

    if not state_path.exists():
        return {}
    try:
        raw = json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}

    # Legacy format: plain list → wrap in dict
    if isinstance(raw, list):
        return {"files": raw}

    if isinstance(raw, dict):
        return raw

    return {}


def _collect_info_from_inbox(url: str) -> list[dict]:
    """Lightweight directory scan of a single inbox for change detection."""
    out: list[dict] = []
    try:
        subfolders = list_inbox_subfolders(url)
    except Exception as e:
        print(f"Warning: Failed to list inbox subfolders at {url} ({e}), falling back to Chair_notes")
        chair_url = f"{url.rstrip('/')}/Chair_notes"
        info = get_remote_schedule_info(chair_url)
        return [{"folder": "Chair_notes", **info}] if info else []

    for folder in subfolders:
        if folder["name"] in BLACKLISTED_FOLDERS:
            continue
        try:
            files = list_remote_files(folder["url"])
            latest = find_latest_schedule(files)
            if latest:
                out.append({
                    "folder": folder["name"],
                    "name": latest["name"],
                    "uploaded_at": (
                        latest["uploaded_at"].isoformat()
                        if latest.get("uploaded_at")
                        else None
                    ),
                })
        except Exception as e:
            print(f"Warning: Failed to check folder {folder['name']}: {e}")
            continue
    return out


def get_all_remote_schedule_info(
    url: str | None = None,
    *,
    urls: list[str] | None = None,
    extra_folders: list[dict] | None = None,
) -> list[dict]:
    """Return metadata of all schedule files across configured inbox folders.

    Aggregates across multiple inbox URLs (``urls``) and any manually-added
    ``extra_folders``.  Used for change detection — only fetches directory
    listings, no downloads.

    Entries are deduplicated by (folder, name) — if the same folder/file is
    seen in multiple inboxes, the newest ``uploaded_at`` wins.
    """
    if urls is None:
        urls = [url] if url is not None else [INBOX_URL]

    collected: list[dict] = []
    for u in urls:
        collected.extend(_collect_info_from_inbox(u))

    for folder in extra_folders or []:
        folder_url = folder["url"]
        folder_name = folder["name"]
        try:
            files = list_remote_files(folder_url)
            latest = find_latest_schedule(files)
            if latest:
                collected.append({
                    "folder": folder_name,
                    "name": latest["name"],
                    "uploaded_at": (
                        latest["uploaded_at"].isoformat()
                        if latest.get("uploaded_at")
                        else None
                    ),
                })
        except Exception as e:
            print(f"Warning: Failed to check extra folder {folder_name}: {e}")

    # Dedup by (folder, name) — newer uploaded_at wins.
    dedup: dict[tuple[str, str], dict] = {}
    for entry in collected:
        key = (entry.get("folder", ""), entry.get("name", ""))
        prior = dedup.get(key)
        if prior is None or (entry.get("uploaded_at") or "") > (prior.get("uploaded_at") or ""):
            dedup[key] = entry

    result = list(dedup.values())
    result.sort(key=lambda x: (x.get("folder", ""), x.get("name", "")))
    return result
