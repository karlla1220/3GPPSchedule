"""Download schedule DOCX files from 3GPP FTP server."""

from __future__ import annotations

import hashlib
import os
import re
import time
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
from bs4 import BeautifulSoup

from models import ScheduleSource

BASE_URL = "https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN1/Inbox/Chair_notes"
INBOX_URL = "https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN1/Inbox/"

# Default local storage root for downloaded artifacts
DOWNLOADS_DIR = Path("downloads")

# Local directory containing manually-provided chairman schedule documents.
# Documents placed here (e.g. from manual/printed references) are treated as
# chairman (main) schedules alongside FTP downloads and take precedence
# over remotely discovered ones.
REF_IN_MANUAL_DIR = Path("ref_in_manual")

# Folders in Inbox/ that never contain schedule files
BLACKLISTED_FOLDERS = {"Agenda", "drafts", "Tdoc_list", "Welcome_speech"}

# Supported document extensions (order = preference when extracting from ZIP)
WORD_DOCUMENT_EXTENSIONS = (".docx", ".docm")
DOCUMENT_EXTENSIONS = (".docx", ".pptx", ".pdf")
# All extensions we accept from remote listings (documents + zip)
SUPPORTED_EXTENSIONS = DOCUMENT_EXTENSIONS + (".docm", ".zip")
SCHEDULE_EXTENSIONS = DOCUMENT_EXTENSIONS + (".zip",)
CHAIR_NOTES_EXTENSIONS = WORD_DOCUMENT_EXTENSIONS + (".zip",)

# Local directory for externally-linked files (config.json ``extra_files``)
# and the committed state file used by check_update.py change detection.
EXTRA_FILES_DIR = DOWNLOADS_DIR / "extra_files"
EXTRA_FILES_STATE_PATH = Path("docs/.extra_files_state.json")
# Ephemeral check-to-build transfer directory. It is uploaded as a workflow
# artifact only when check had to fetch an external file.
EXTRA_FILES_TRANSFER_DIR = DOWNLOADS_DIR / ".extra_files_transfer"

# Agenda folders can expose a plain agenda.csv instead of a TDoc archive.
# Preference is intentionally different from schedules: use CSV first,
# then DOCX, then ZIP contents.
AGENDA_EXTENSIONS = (".csv", ".docx", ".zip")
AGENDA_EXTRACT_EXTENSIONS = (".csv", ".docx")

# Retry configuration for transient server errors
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 5  # seconds

# ETSI's list server may disconnect requests that use httpx/requests' default
# Python User-Agent without sending an HTTP response.  A browser-compatible
# User-Agent is accepted by the endpoint and is also appropriate for the
# curl -OJL-compatible external-file download path.
_EXTERNAL_FILE_HEADERS = {"User-Agent": "Mozilla/5.0"}

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
    """Extract trailing primary version number from names like '... v09.zip'.

    Returns -1 when version is not found.
    """
    version_parts = _extract_version_parts_from_name(filename)
    return version_parts[0]


def _extract_version_parts_from_name(filename: str) -> tuple[int, ...]:
    """Extract trailing version parts from names like '... v04_3.docx'.

    Returns (-1,) when version is not found.
    """
    _ext_pattern = "|".join(re.escape(e) for e in SUPPORTED_EXTENSIONS)
    m = re.search(rf"v(\d+)((?:[_-]\d+)*)(?:{_ext_pattern})$", filename, re.IGNORECASE)
    if not m:
        return (-1,)
    suffix_parts = tuple(int(part) for part in re.findall(r"\d+", m.group(2)))
    return (int(m.group(1)), *suffix_parts)


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


# Suffixes used in TSGR_*_NNN folder/file names (``b`` is 3GPP's short
# form of ``bis`` for these newer per-meeting folders).
_TSGR_SUFFIXES = r"(?:b|bis)"

# Pattern to extract meeting identifiers like TSGR1_126, TSGR1_124b, etc.
# (per-meeting folders in the tsg_ran FTP tree).  Both short suffixes and
# full ones are accepted; the result is normalised to the same
# ``ran<team>#<num>`` form as the ``RAN1#124`` pattern.
_TSGR_ID_PATTERN = re.compile(
    rf"TSG[_ ]?R(\d+)[_ ](\d+)(?:[ _-]?({_TSGR_SUFFIXES}))?",
    re.IGNORECASE,
)


def _extract_meeting_id(filename: str) -> str | None:
    """Extract a normalised meeting identifier from a filename.

    Examples:
        'RAN1#124 online and offline schedules - v02.docx'  → 'ran1#124'
        'RAN1#124bis schedule for Hiroki_v07.docx'          → 'ran1#124bis'
        'RAN1#124-bis schedule - v01.docx'                  → 'ran1#124bis'
        'RAN1#124 bis schedule - v01.docx'                  → 'ran1#124bis'
        'TSGR1_126 online and offline schedules - v02.docx' → 'ran1#126'
        'TSGR1_124b schedule - v01.docx'                    → 'ran1#124bis'
        'custom schedule name.docx'                         → None

    The returned string is always lowercased with any hyphen/space between
    the number and the suffix removed (e.g. '124-bis' → '124bis').

    Two naming conventions are recognised:

    - Legacy ``RAN<N>#<num>`` (regex ``_MEETING_ID_PATTERN``)
    - Newer ``TSGR<N>_<num>`` (regex ``_TSGR_ID_PATTERN``), used by the
      ``tsg_ran/WG*/TSGR*_*`` FTP folders for recent meetings.  Both
      normalise to the same output so that files from the same meeting
      written in either style compare equal.
    """
    m = _MEETING_ID_PATTERN.search(filename)
    if m:
        base = m.group(1).lower()
        suffix = (m.group(2) or "").lower()
        return f"{base}{suffix}"

    m = _TSGR_ID_PATTERN.search(filename)
    if m:
        team, num, suffix = int(m.group(1)), m.group(2), (m.group(3) or "").lower()
        if suffix == "b":
            suffix = "bis"
        return f"ran{team}#{num}{suffix}"

    return None


# Regular plenary meeting ids look like ``ran1#124`` or ``ran1#124bis``.
# Ad-hoc / electronic / other irregular meetings are not totally-ordered
# by name, so we fall back to upload date for them.
_REGULAR_MEETING_RE = re.compile(r"^ran(\d+)#(\d+)(bis)?$", re.IGNORECASE)


def _local_doc_preference(
    p: Path, preferred_meeting_id: str | None = None
) -> tuple:
    """Deterministic sort key for locally-provided document files.

    Ordered by (preferred-meeting match, filename meeting rank, filename
    version, name) — never using filesystem metadata.  File mtimes are not
    stable across CI checkouts (a fresh ``git checkout`` gives every file
    the checkout time), so any mtime-based selection would pick
    non-deterministically on CI/CD runners.  Filename-derived data is
    committed content, so it is identical on every machine.

    A stable final component (``p.name``) ensures the ordering is a total
    order even when all other keys collide.
    """
    mid = _extract_meeting_id(p.name)
    favored = 1 if (
        preferred_meeting_id is not None
        and mid is not None
        and mid == preferred_meeting_id
    ) else 0
    return (
        favored,
        _meeting_rank(mid) or (0, 0, 0),
        _extract_version_parts_from_name(p.name),
        p.name.lower(),
        p.name,
    )


def _iter_local_files(
    directory: Path,
    extensions: tuple[str, ...],
) -> list[Path]:
    """Return files with supported suffixes, case-insensitively.

    ``Path.glob("*.docx")`` is case-sensitive on Linux but not on the
    default Windows filesystem.  CI runs on Linux, so relying on glob case
    behaviour would make the same checkout behave differently by platform.
    """
    if not directory.is_dir():
        return []
    try:
        return sorted(
            (
                path
                for path in directory.iterdir()
                if path.is_file() and path.suffix.lower() in extensions
            ),
            key=lambda path: (path.name.lower(), path.name),
        )
    except OSError:
        return []


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


def _files_at_or_after_preferred_meeting(
    files: list[dict],
    preferred_meeting_id: str | None,
) -> list[dict]:
    """Keep files compatible with a cached/local regular meeting hint.

    A fresh FTP listing can temporarily omit the current meeting while still
    returning older folders.  Selecting one of those older files would make
    the check job disagree with a build that uses the cached/local meeting.
    For regular meetings, therefore, older and unidentifiable files are not
    eligible when a preferred meeting is known.  A later regular meeting is
    retained so normal meeting advancement still works.

    Irregular preferred meetings cannot be ordered reliably and retain the
    historical fallback behaviour.
    """
    preferred_rank = _meeting_rank(preferred_meeting_id)
    if preferred_rank is None:
        return files

    preferred = preferred_meeting_id.lower()
    regular_files: list[tuple[dict, tuple[int, int, int]]] = []
    for file_info in files:
        rank = _meeting_rank(_extract_meeting_id(file_info["name"]))
        if rank is not None:
            regular_files.append((file_info, rank))

    if regular_files:
        return [
            file_info
            for file_info, rank in regular_files
            if rank >= preferred_rank
        ]

    # No regular IDs are available.  Only an exact irregular/current group
    # match is safe; an unrelated group must not displace a regular hint.
    return [
        file_info
        for file_info in files
        if (_extract_meeting_id(file_info["name"]) or "").lower() == preferred
    ]


def _pick_latest_in_meeting_group(
    files: list[dict],
    label: str = "schedule",
    preferred_meeting_id: str | None = None,
) -> dict | None:
    """Select the best file using meeting-aware grouping.

    1. Group *files* by meeting identifier.
        2. Choose the "current" meeting group:
             - Among regular plenary ids, the highest meeting rank wins
                 (e.g. ``ran1#124bis`` beats ``ran1#124``; ``ran1#125`` beats
                 both).  This means a stray draft labelled with an *older*
                 meeting id cannot displace the current one, and a *newer*
                 meeting id automatically takes over as soon as it appears.
             - ``preferred_meeting_id`` acts as the cached/current hint from
                 state: if the best regular id is the same meeting, we stay on
                 it; if a later regular meeting appears, we advance to it.
             - If no regular ids are present (only ad-hoc / unknown), fall
                 back to the preferred group when present, else to the group
                 whose most-recent ``uploaded_at`` is newest.
    3. Within the chosen group pick the file with the highest version
       number, using ``uploaded_at`` as a tiebreaker.

    All files must have a non-None ``uploaded_at``.
    """
    from collections import defaultdict

    if preferred_meeting_id is not None:
        preferred_meeting_id = preferred_meeting_id.lower()

    files = _files_at_or_after_preferred_meeting(files, preferred_meeting_id)
    if not files:
        if preferred_meeting_id is not None:
            print(
                f"  No {label} file at or after preferred meeting "
                f"{preferred_meeting_id}; ignoring older/unidentified files."
            )
        return None

    groups: dict[str | None, list[dict]] = defaultdict(list)
    for f in files:
        mid = _extract_meeting_id(f["name"])
        groups[mid].append(f)

    def _group_max_ts(group: list[dict]) -> datetime:
        return max(f["uploaded_at"] for f in group)

    regular = {
        mid: grp for mid, grp in groups.items() if _meeting_rank(mid) is not None
    }
    pref_rank = _meeting_rank(preferred_meeting_id)

    current_mid: str | None
    if regular:
        highest_regular_mid = max(regular, key=lambda m: _meeting_rank(m))
        highest_regular_rank = _meeting_rank(highest_regular_mid)
        if (
            preferred_meeting_id is not None
            and preferred_meeting_id.lower() in regular
            and pref_rank is not None
            and highest_regular_rank is not None
            and pref_rank >= highest_regular_rank
        ):
            current_mid = preferred_meeting_id.lower()
        else:
            current_mid = highest_regular_mid

        if (
            preferred_meeting_id is not None
            and pref_rank is not None
            and highest_regular_rank is not None
            and highest_regular_rank > pref_rank
        ):
            print(
                f"  Newer meeting detected for {label}: "
                f"{preferred_meeting_id} → {current_mid}"
            )
    else:
        if preferred_meeting_id is not None and preferred_meeting_id in groups:
            current_mid = preferred_meeting_id
        else:
            # Only irregular / unparseable ids → fall back to upload date.
            current_mid = max(groups, key=lambda mid: _group_max_ts(groups[mid]))
        if (
            preferred_meeting_id is not None
            and current_mid != preferred_meeting_id
        ):
            print(
                f"  Note: preferred meeting {preferred_meeting_id!r} not found "
                f"among {label} files; falling back to {current_mid!r}"
            )
    current_group = groups[current_mid]

    latest = max(
        current_group,
        key=lambda x: (
            _extract_version_parts_from_name(x["name"]),
            x["uploaded_at"],
            x["name"].lower(),
            x["name"],
        ),
    )
    print(
        f"Latest {label} (meeting={current_mid}, "
        f"version={_extract_version_from_name(latest['name'])}, "
        f"uploaded={latest['uploaded_at']}): {latest['name']}"
    )
    return latest


def list_remote_files(
    url: str = BASE_URL,
    *,
    supported_extensions: tuple[str, ...] = SUPPORTED_EXTENSIONS,
) -> list[dict]:
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
        if not any(href.lower().endswith(ext) for ext in supported_extensions):
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
    locked_meeting_id: str | None = None,
) -> dict | None:
    """Find the latest schedule file using meeting-aware grouping.

    Looks for files containing 'schedule' in the name.
    Supports .docx, .docm, .pptx, .pdf, and .zip files.

    Selection strategy (in order):
    1. Group files by meeting identifier (e.g. RAN1#124bis).
    2. Pick the group whose most-recent upload timestamp is newest
       (= the current meeting).
    3. Within that group, pick the file with the highest version number.
       Upload timestamp is a secondary tiebreaker.

    This ensures that old high-version files from a previous meeting
    do not displace the current meeting's schedule, while still
    preferring the highest version within the current meeting.

    Falls back to version number (then filename) if timestamps are
    unavailable.  When ``locked_meeting_id`` is set, only that exact meeting
    is considered; this is used for authoritative local reference files.
    """
    schedule_files = [
        f
        for f in files
        if "schedule" in f["name"].lower()
        and f["name"].lower().endswith(SCHEDULE_EXTENSIONS)
    ]
    if locked_meeting_id is not None:
        locked = locked_meeting_id.lower()
        schedule_files = [
            f for f in schedule_files
            if _extract_meeting_id(f["name"]) == locked
        ]
        preferred_meeting_id = locked
    schedule_files = _files_at_or_after_preferred_meeting(
        schedule_files,
        preferred_meeting_id,
    )

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
        return max(
            versioned,
            key=lambda x: (
                _extract_version_parts_from_name(x["name"]),
                x["name"].lower(),
                x["name"],
            ),
        )

    # Last resort: use a stable filename order rather than listing order.
    return max(schedule_files, key=lambda f: (f["name"].lower(), f["name"]))


def get_remote_schedule_info(
    url: str = BASE_URL,
    preferred_meeting_id: str | None = None,
    locked_meeting_id: str | None = None,
) -> dict | None:
    """Return metadata (name + uploaded_at) of the latest schedule on FTP.

    This is a lightweight check — only fetches the directory listing,
    does NOT download any file.  Used for change detection.
    """
    files = list_remote_files(url)
    latest = find_latest_schedule(
        files,
        preferred_meeting_id=preferred_meeting_id,
        locked_meeting_id=locked_meeting_id,
    )
    if latest is None:
        return None
    return {
        "name": latest["name"],
        "uploaded_at": (
            latest["uploaded_at"].isoformat() if latest.get("uploaded_at") else None
        ),
    }


def extract_document_from_zip(
    zip_path: Path,
    *,
    document_extensions: tuple[str, ...] = DOCUMENT_EXTENSIONS,
) -> Path | None:
    """Extract a document file from a ZIP archive.

    Looks for files matching ``document_extensions`` inside the ZIP in
    the given preference order. Returns the path of the extracted file,
    or None if no supported document is found.
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
            for priority, ext in enumerate(document_extensions):
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


def download_and_resolve(
    url: str,
    dest_path: Path,
    *,
    document_extensions: tuple[str, ...] = DOCUMENT_EXTENSIONS,
) -> Path:
    """Download a file and, if it is a ZIP, extract the document inside.

    Returns the path to the final usable document (docx / pptx / pdf).
    """
    downloaded = download_file(url, dest_path)

    if downloaded.suffix.lower() == ".zip":
        extracted = extract_document_from_zip(
            downloaded,
            document_extensions=document_extensions,
        )
        if extracted is not None:
            return extracted
        # ZIP contained nothing useful — return as-is (caller will handle)
        print("Warning: ZIP did not contain a supported document; using ZIP path")

    return downloaded


def download_latest_schedule(
    dest_dir: Path = DOWNLOADS_DIR / "Chair_notes",
) -> Path:
    """Download the latest schedule from 3GPP FTP.

    Supports .docx, .docm, .pptx, .pdf, and .zip (auto-extracted).
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
    """Find the latest schedule document in the local directory.

    Searches for .docx, .pptx, and .pdf files containing 'schedule' in the name.
    Selection is by filename (version, then name), not mtime — mtimes are not
    stable across CI checkouts, so a fresh runner would pick
    non-deterministically.
    """
    schedule_files = [
        f
        for f in _iter_local_files(dest_dir, DOCUMENT_EXTENSIONS)
        if "schedule" in f.name.lower()
    ]

    if not schedule_files:
        return None

    latest = max(schedule_files, key=_local_doc_preference)
    print(f"Latest local schedule (by filename): {latest.name}")
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
    Supports .docx, .docm, and .zip files.

    Uses the same meeting-aware grouping strategy as find_latest_schedule():
    group by meeting ID → pick latest meeting → highest version within group.
    """
    chair_files = [
        f for f in files
        if (
            f["name"].lower().endswith(CHAIR_NOTES_EXTENSIONS)
            and (
                "chair note" in f["name"].lower()
                or "chair_note" in f["name"].lower()
                or "chairman note" in f["name"].lower()
                or "chairman_note" in f["name"].lower()
            )
        )
    ]

    if not chair_files:
        return None

    chair_files = _files_at_or_after_preferred_meeting(
        chair_files,
        preferred_meeting_id,
    )
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
        return max(
            versioned,
            key=lambda x: (
                _extract_version_parts_from_name(x["name"]),
                x["name"].lower(),
                x["name"],
            ),
        )

    return max(chair_files, key=lambda f: (f["name"].lower(), f["name"]))


def _chair_notes_url_from_inbox(url: str) -> str:
    """Return the Chair_notes folder URL for an Inbox URL."""
    return f"{url.rstrip('/')}/Chair_notes"


def get_latest_chair_notes_info(
    url: str | None = None,
    *,
    urls: list[str] | None = None,
    extra_folders: list[dict] | None = None,
    preferred_meeting_id: str | None = None,
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

        if preferred_meeting_id is not None:
            files = _filter_files_to_meeting(files, preferred_meeting_id)
        latest = find_latest_chair_notes(
            files,
            preferred_meeting_id=preferred_meeting_id,
        )
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

        if preferred_meeting_id is not None:
            files = _filter_files_to_meeting(files, preferred_meeting_id)
        latest = find_latest_chair_notes(
            files,
            preferred_meeting_id=preferred_meeting_id,
        )
        if latest is not None:
            candidates.append({**latest, "source_url": folder_url})

    if not candidates:
        return None

    latest = find_latest_chair_notes(
        candidates,
        preferred_meeting_id=preferred_meeting_id,
    )
    if latest is not None and latest.get("source_url"):
        print(f"Latest Chair notes source: {latest['source_url']}")
    return latest


def download_latest_chair_notes(
    dest_dir: Path = DOWNLOADS_DIR / "Chair_notes",
    url: str | None = None,
    *,
    urls: list[str] | None = None,
    extra_folders: list[dict] | None = None,
    preferred_meeting_id: str | None = None,
    latest_info: dict | None = None,
    force: bool = False,
) -> Path | None:
    """Download the latest Chair notes from 3GPP FTP.

    Supports .docx, .docm, and .zip (auto-extracted).
    Returns the path to the downloaded file, or None if not found.
    """
    latest = latest_info or get_latest_chair_notes_info(
        url,
        urls=urls,
        extra_folders=extra_folders,
        preferred_meeting_id=preferred_meeting_id,
    )
    if latest is None:
        print("No Chair notes file found on FTP")
        return None

    dest_path = dest_dir / latest["name"]

    if dest_path.exists() and not force:
        if dest_path.suffix.lower() == ".zip":
            extracted = _find_extracted_document(
                dest_path,
                document_extensions=WORD_DOCUMENT_EXTENSIONS,
            )
            if extracted:
                print(f"Using previously extracted: {extracted}")
                return extracted
            extracted = extract_document_from_zip(
                dest_path,
                document_extensions=WORD_DOCUMENT_EXTENSIONS,
            )
            if extracted:
                return extracted
        print(f"Chair notes already exists: {dest_path}")
        return dest_path

    try:
        return download_and_resolve(
            latest["url"],
            dest_path,
            document_extensions=WORD_DOCUMENT_EXTENSIONS,
        )
    except Exception as e:
        print(f"Warning: Failed to download Chair notes: {e}")
        return None


# ── Agenda discovery & download ────────────────────────────────

def find_latest_agenda(files: list[dict]) -> dict | None:
    """Pick the newest agenda candidate from an Agenda-folder listing.

    Agenda files in 3GPP per-meeting folders may be a plain
    ``agenda.csv``, a DOCX, or a TDoc archive containing those files.

    Selection order:
        1. Prefer CSV, then DOCX, then ZIP.
        2. Within the same type, use highest ``uploaded_at``.
        3. If no timestamps are available, return the first entry for
           the preferred type.
    """
    candidates = [
        f for f in files
        if any(f["name"].lower().endswith(ext) for ext in AGENDA_EXTENSIONS)
    ]
    if not candidates:
        return None

    def _agenda_priority(file_info: dict) -> int:
        name = file_info["name"].lower()
        for priority, ext in enumerate(AGENDA_EXTENSIONS):
            if name.endswith(ext):
                return priority
        return len(AGENDA_EXTENSIONS)

    best_priority = min(_agenda_priority(f) for f in candidates)
    preferred = [f for f in candidates if _agenda_priority(f) == best_priority]
    with_ts = [f for f in preferred if f.get("uploaded_at") is not None]
    if with_ts:
        return max(
            with_ts,
            key=lambda f: (
                f["uploaded_at"],
                f["name"].lower(),
                f["name"],
            ),
        )
    return max(preferred, key=lambda f: (f["name"].lower(), f["name"]))


def get_latest_agenda_info(agenda_urls: list[str]) -> dict | None:
    """Return metadata of the latest agenda across ``agenda_urls``.

    Each URL is expected to be a meeting-specific Agenda folder URL such
    as ``https://www.3gpp.org/ftp/tsg_ran/WG1_RL1/TSGR1_124b/Agenda/``.
    """
    candidates: list[dict] = []
    for agenda_url in agenda_urls:
        try:
            files = list_remote_files(
                agenda_url,
                supported_extensions=AGENDA_EXTENSIONS,
            )
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
    *,
    latest_info: dict | None = None,
    force: bool = False,
) -> Path | None:
    """Download the latest agenda document from configured agenda URLs.

    Supports .docx, .pptx, .pdf, and .zip (auto-extracted).
    Returns the path to the downloaded (or already-cached) document,
    or None if nothing suitable was found.
    """
    if not agenda_urls:
        return None

    latest = latest_info or get_latest_agenda_info(agenda_urls)
    if latest is None:
        print("No agenda file found on FTP")
        return None

    dest_path = dest_dir / latest["name"]

    if dest_path.exists() and not force:
        if dest_path.suffix.lower() == ".zip":
            extracted = _find_extracted_document(
                dest_path,
                document_extensions=AGENDA_EXTRACT_EXTENSIONS,
            )
            if extracted:
                print(f"Using previously extracted agenda: {extracted}")
                return extracted
            extracted = extract_document_from_zip(
                dest_path,
                document_extensions=AGENDA_EXTRACT_EXTENSIONS,
            )
            if extracted:
                return extracted
        print(f"Agenda already exists: {dest_path}")
        return dest_path

    try:
        downloaded = download_file(latest["url"], dest_path)
        if downloaded.suffix.lower() == ".zip":
            extracted = extract_document_from_zip(
                downloaded,
                document_extensions=AGENDA_EXTRACT_EXTENSIONS,
            )
            if extracted is not None:
                return extracted
            print("Warning: agenda ZIP did not contain CSV or DOCX")
        return downloaded
    except Exception as e:
        print(f"Warning: Failed to download agenda: {e}")
        return None


def find_local_latest_agenda(
    dest_dir: Path = DOWNLOADS_DIR / "Agenda",
) -> Path | None:
    """Return the newest locally-cached agenda document, if any."""
    candidates = _iter_local_files(dest_dir, AGENDA_EXTRACT_EXTENSIONS)
    if not candidates:
        return None
    # Deterministic filename selection — mtimes are not stable across
    # CI checkouts (every fresh checkout shares the checkout time).
    return max(candidates, key=_local_doc_preference)


def _find_extracted_document(
    zip_path: Path,
    *,
    document_extensions: tuple[str, ...] = DOCUMENT_EXTENSIONS,
) -> Path | None:
    """Look for documents that may have been previously extracted from a ZIP.

    Searches the same directory for document files whose stem matches or
    contains the ZIP's stem (heuristic).  Returns the newest match.
    """
    parent = zip_path.parent
    candidates = []
    stem = zip_path.stem.lower()
    for f in _iter_local_files(parent, document_extensions):
        if stem in f.stem.lower():
            candidates.append(f)
    if not candidates:
        return None
    # Return the highest-version document file (deterministic across CI
    # checkouts — mtime varies per runner).
    return max(candidates, key=_local_doc_preference)


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


def _filter_files_to_meeting(files: list[dict], meeting_id: str | None) -> list[dict]:
    """Return only files whose filename explicitly matches ``meeting_id``.

    Once a current meeting is known, files from other meetings should not
    participate in schedule selection, even if their version or upload time is
    higher.  Files without a parseable meeting id are also excluded because we
    cannot prove they belong to the selected meeting.
    """
    if meeting_id is None:
        return files
    return [f for f in files if _extract_meeting_id(f["name"]) == meeting_id]


def _discover_from_inbox(
    url: str,
    preferred_meeting_id: str | None = None,
    locked_meeting_id: str | None = None,
) -> list[ScheduleSource]:
    """Scan a single inbox URL for schedule sources (subfolders + stray root files)."""
    sources: list[ScheduleSource] = []
    chair_notes_url = f"{url.rstrip('/')}/Chair_notes"

    # 1. Scan subfolders
    try:
        subfolders = list_inbox_subfolders(url)
    except Exception as e:
        print(f"Warning: Failed to list Inbox subfolders at {url}: {e}")
        return _fallback_chair_only(
            chair_notes_url,
            preferred_meeting_id=preferred_meeting_id,
            locked_meeting_id=locked_meeting_id,
        )

    # First pass: locate the Chair_notes folder so we can determine the
    # canonical meeting for this run. The cached state meeting id is a
    # hint, not a lock: a newer regular meeting is allowed to take over.
    main_meeting_id: str | None = None
    main_folder_name: str | None = None
    chair_folder_seen = False
    chair_scan_failed = False
    for folder in subfolders:
        if folder["name"] in BLACKLISTED_FOLDERS:
            continue
        person_name = _extract_person_name(folder["name"])
        if "chair" in folder["name"].lower() and person_name is None:
            chair_folder_seen = True
            main_folder_name = folder["name"]
            try:
                chair_files = list_remote_files(folder["url"])
            except Exception as e:
                print(f"  Warning: Cannot access {folder['name']}/: {e}")
                chair_scan_failed = True
                break
            chair_latest = find_latest_schedule(
                chair_files,
                preferred_meeting_id=preferred_meeting_id,
                locked_meeting_id=locked_meeting_id,
            )
            if chair_latest is not None:
                main_meeting_id = _extract_meeting_id(chair_latest["name"])
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

        if main_meeting_id is not None:
            files = _filter_files_to_meeting(files, main_meeting_id)
            if not files:
                print(
                    f"  No schedule for current meeting {main_meeting_id} "
                    f"in {folder['name']}/"
                )
                continue

        latest = find_latest_schedule(
            files,
            preferred_meeting_id=main_meeting_id or preferred_meeting_id,
            locked_meeting_id=locked_meeting_id,
        )
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
            meeting_hint = locked_meeting_id or main_meeting_id or preferred_meeting_id
            if meeting_hint is not None:
                file_mid = _extract_meeting_id(sf["name"])
                if file_mid != meeting_hint:
                    print(
                        f"  Skipping Inbox root file outside current meeting "
                        f"{meeting_hint}: {sf['name']}"
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

    if not any(s.is_main for s in sources) and (
        not chair_folder_seen or chair_scan_failed
    ):
        print(f"  Warning: No main schedule (Chair_notes) found at {url}, using fallback")
        fallback = _fallback_chair_only(
            chair_notes_url,
            preferred_meeting_id=main_meeting_id or preferred_meeting_id,
            locked_meeting_id=locked_meeting_id,
        )
        if fallback:
            sources.extend(fallback)

    return sources


def _source_from_extra_folder(
    folder: dict,
    preferred_meeting_id: str | None = None,
    locked_meeting_id: str | None = None,
) -> ScheduleSource | None:
    """Build a ScheduleSource from a manually-configured extra folder entry."""
    folder_url = folder["url"]
    folder_name = folder["name"]
    try:
        files = list_remote_files(folder_url)
    except Exception as e:
        print(f"  Warning: Cannot access extra folder {folder_name}/: {e}")
        return None

    latest = find_latest_schedule(
        files,
        preferred_meeting_id=preferred_meeting_id,
        locked_meeting_id=locked_meeting_id,
    )
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

        - At most one ``is_main`` survives (locally-provided mains win over FTP
            mains; local collisions use filename meeting/version, remote collisions
            use ``uploaded_at``).
        - Vice-chair sources with the same ``person_name`` collapse using the same
            local-priority/remote-timestamp ranking.
        - Non-main, no-person entries with the same ``folder_name`` collapse using
            the same ranking.
    """
    from datetime import datetime

    def ts(s: ScheduleSource) -> datetime:
        v = s.file_info.get("uploaded_at")
        return v if isinstance(v, datetime) else datetime.min

    def source_rank(s: ScheduleSource) -> tuple:
        """Rank a source without relying on local filesystem metadata."""
        if s.local_path is not None:
            # Locally-provided sources are explicit operator inputs.  Among
            # them, prefer the highest filename-derived meeting/version so
            # multiple local candidates do not resolve by insertion order.
            return (1, _local_doc_preference(s.local_path))
        name = s.file_info.get("name", "")
        return (0, ts(s), name.lower(), name)

    main: ScheduleSource | None = None
    by_person: dict[str, ScheduleSource] = {}
    by_folder: dict[str, ScheduleSource] = {}

    for s in sources:
        if s.is_main:
            if main is None or source_rank(s) > source_rank(main):
                if main is not None:
                    main.is_main = False
                    by_folder[main.folder_name] = main
                main = s
            else:
                s.is_main = False
                key = s.folder_name
                if key not in by_folder or source_rank(s) > source_rank(by_folder[key]):
                    by_folder[key] = s
        elif s.person_name:
            existing = by_person.get(s.person_name)
            if existing is None or source_rank(s) > source_rank(existing):
                by_person[s.person_name] = s
        else:
            key = s.folder_name
            if key not in by_folder or source_rank(s) > source_rank(by_folder[key]):
                by_folder[key] = s

    out: list[ScheduleSource] = []
    if main is not None:
        out.append(main)
    out.extend(by_person.values())
    out.extend(by_folder.values())
    return out


def _current_meeting_from_sources(
    sources: list[ScheduleSource],
    preferred_meeting_id: str | None = None,
) -> str | None:
    """Determine the current meeting from discovered main schedule sources.

    Only files whose names contain a parseable *regular plenary* meeting id
    can establish a current meeting. When no main file has a regular id the
    meeting is unidentifiable; in that case the cached
    ``preferred_meeting_id`` is kept and the meeting-filter keeps the
    previous meeting's sources rather than switching to an unidentifiable
    one.
    """
    local_main_ids = {
        _extract_meeting_id(s.file_info["name"])
        for s in sources
        if s.is_main
        and s.local_path is not None
        and _meeting_rank(_extract_meeting_id(s.file_info["name"])) is not None
    }
    if local_main_ids:
        # ref_in_manual/ and explicitly configured extra schedule files are
        # operator-selected inputs.  Their meeting must not be replaced by
        # an older (or newer-but-unwanted) remote listing.  A later meeting
        # becomes active when the local reference itself is changed/removed.
        current_local = max(local_main_ids, key=lambda mid: _meeting_rank(mid))
        print(f"  Current meeting (from local main source): {current_local}")
        return current_local

    main_files = [s.file_info for s in sources if s.is_main]
    if not main_files:
        return preferred_meeting_id

    if not any(f.get("uploaded_at") is not None for f in main_files):
        # Without timestamps we cannot order anything; keep the cached hint.
        return preferred_meeting_id

    files_with_ts = [f for f in main_files if f.get("uploaded_at") is not None]
    groups: dict[str | None, list[dict]] = {}
    for f in files_with_ts:
        mid = _extract_meeting_id(f["name"])
        groups.setdefault(mid, []).append(f)

    regular = {mid: grp for mid, grp in groups.items() if _meeting_rank(mid) is not None}
    if not regular:
        print(
            "  No regular plenary meeting id found in main schedule file "
            "names; keeping previous meeting "
            f"{preferred_meeting_id!r} for filtering."
        )
        return preferred_meeting_id

    current_mid = max(regular, key=lambda m: _meeting_rank(m))
    pref_rank = _meeting_rank(preferred_meeting_id)
    if (
        preferred_meeting_id is not None
        and preferred_meeting_id in regular
        and pref_rank is not None
        and pref_rank >= _meeting_rank(current_mid)
    ):
        current_mid = preferred_meeting_id
        return current_mid

    if (
        preferred_meeting_id is not None
        and _meeting_rank(current_mid) is not None
        and _meeting_rank(current_mid) > pref_rank
    ):
        print(
            f"  Newer meeting detected: {preferred_meeting_id} → {current_mid}"
        )
    return current_mid


def _filter_sources_to_meeting(
    sources: list[ScheduleSource],
    meeting_id: str | None,
) -> list[ScheduleSource]:
    """Drop schedule sources that do not belong to the selected meeting.

    Locally-provided sources (``local_path`` set) are always kept: they are
    manually curated, so the operator's choice is authoritative even when
    their filename doesn't reference the selected meeting id.

    Files whose names do not contain a parseable meeting id are never
    adopted: an unidentifiable meeting cannot establish a new current
    meeting, so such files are dropped as well when a meeting is selected.
    """
    if meeting_id is None:
        return sources

    filtered: list[ScheduleSource] = []
    for source in sources:
        if source.local_path is not None:
            filtered.append(source)
            continue
        source_mid = _extract_meeting_id(source.file_info["name"])
        if source_mid == meeting_id:
            filtered.append(source)
        else:
            label = "MAIN" if source.is_main else source.person_name or source.folder_name
            detail = (
                source_mid
                if source_mid is not None
                else "unidentifiable meeting id"
            )
            print(
                f"  Skipping {source.folder_name}/ {detail} "
                f"(current meeting {meeting_id}): {source.file_info['name']} [{label}]"
            )
    return filtered


def _latest_local_schedule(
    paths: list[Path], preferred_meeting_id: str | None = None
) -> Path:
    """From a set of local schedule document paths, pick the one to use.

    Manual references carry no remote version info, so selection is
    purely by filename: preferred-meeting match, meeting rank, version
    (treating an unparseable version as (-1,)), then name.  No filesystem
    metadata is used — mtimes are not stable across CI checkouts, so an
    mtime tiebreaker would select a different file on every fresh runner.

    Filename filtering ("schedule" substring, meeting id) is intentionally
    NOT applied here — the operator decides what belongs in the folder.

    ``preferred_meeting_id`` is only used to order matches: if any candidate
    filename references it, prefer those over files from other meetings.
    """
    if len(paths) == 1:
        return paths[0]

    return max(paths, key=lambda p: _local_doc_preference(p, preferred_meeting_id))


def find_local_schedule_sources(
    ref_dir: Path = REF_IN_MANUAL_DIR,
    preferred_meeting_id: str | None = None,
) -> tuple[list[ScheduleSource], Path | None]:
    """Build chairman-schedule sources from manually-provided local documents.

    Scans ``ref_dir`` for supported document files (.docx, .pptx, .pdf, .zip)
    and returns ``(sources, chosen_path)``:

    - ``sources``: one ``ScheduleSource`` per candidate, all marked
      ``is_main=True`` (chairman schedule), ``local_path`` set, and deduped
      against each other by folder name.
    - ``chosen_path``: the single document selected for this run
      (or ``None`` when the directory is absent/empty).

    ``preferred_meeting_id`` biases candidate selection toward files that
    reference that meeting in their filename, and falls back to the highest
    filename version, then name, when none do (deterministic — no mtime).
    """
    if not ref_dir.is_dir():
        return [], None

    candidates = _iter_local_files(ref_dir, SUPPORTED_EXTENSIONS)

    if not candidates:
        return [], None

    chosen = _latest_local_schedule(candidates, preferred_meeting_id)
    print(f"  Local chairman schedule reference: {chosen.name}")

    sources: list[ScheduleSource] = []
    # Keep the preferred candidate first for stable discovery logs.  All
    # local candidates are retained for backwards compatibility; deduplication
    # independently resolves the main source by filename-derived rank.
    ordered_candidates = sorted(
        candidates,
        key=lambda p: _local_doc_preference(p, preferred_meeting_id),
        reverse=True,
    )
    for f in ordered_candidates:
        sources.append(
            ScheduleSource(
                folder_name=ref_dir.name,
                person_name=None,
                is_main=True,
                file_info={
                    "name": f.name,
                    "url": None,
                    # Local references have no remote upload timestamp.
                    # Storing None (rather than mtime) keeps persisted
                    # state independent of checkout time on CI runners.
                    "uploaded_at": None,
                },
                local_path=f,
            )
        )
    return sources, chosen


def local_reference_hashes(
    ref_dir: Path = REF_IN_MANUAL_DIR,
) -> dict[str, str]:
    """Hash manually-provided local reference files for change detection.

    Returns ``{filename: sha256-hex}`` for every supported document in
    ``ref_dir``.  Content-based (not mtime) so the result is stable across
    CI checkouts — the files are committed to the repository, so identical
    contents always produce identical hashes.
    """
    import hashlib

    if not ref_dir.is_dir():
        return {}
    out: dict[str, str] = {}
    for f in _iter_local_files(ref_dir, SUPPORTED_EXTENSIONS):
        out[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def local_reference_meeting_id(
    ref_dir: Path = REF_IN_MANUAL_DIR,
) -> str | None:
    """Return the highest regular meeting id found in local ref filenames.

    This is a deterministic *hint*, not a content or filesystem timestamp.
    A local reference is intentionally authoritative over older remote FTP
    listings, while a later regular meeting may still be selected after the
    local reference is removed or replaced.

    Irregular meeting IDs cannot be totally ordered, so they do not produce
    a hint here; the persisted meeting state remains the fallback for them.
    """
    meeting_ids = {
        _extract_meeting_id(path.name)
        for path in _iter_local_files(ref_dir, SUPPORTED_EXTENSIONS)
    }
    regular_ids = [mid for mid in meeting_ids if _meeting_rank(mid) is not None]
    if not regular_ids:
        return None
    return max(regular_ids, key=lambda mid: _meeting_rank(mid))


def discover_schedule_sources(
    url: str | None = None,
    *,
    urls: list[str] | None = None,
    extra_folders: list[dict] | None = None,
    local_schedule_sources: list[ScheduleSource] | None = None,
    preferred_meeting_id: str | None = None,
    locked_meeting_id: str | None = None,
) -> list[ScheduleSource]:
    """Discover schedule sources across one or more inbox URLs and extra folders.

    ``urls`` accepts a list of inbox URLs to aggregate from.  The legacy
    single-URL ``url`` parameter is still honoured for backwards compatibility.
    ``extra_folders`` is a list of dicts with keys ``url`` (required),
    ``name``, ``person_name``, ``is_main`` — manually-specified folders
    to include alongside the inbox scans.

    ``local_schedule_sources`` are pre-built ``ScheduleSource`` objects for
    locally-provided chairman documents (see
    :func:`find_local_schedule_sources`); they participate in the same
    meeting-filtering and dedup as remote sources and win main-schedule
    collisions.

    ``preferred_meeting_id`` is typically loaded from persisted state and
    used as the current-meeting hint. Older meetings are ignored when a
    newer regular meeting is present; newer meetings are allowed to advance
    the run automatically.

    ``locked_meeting_id`` is used for an authoritative local reference. When
    set, all remote discovery is restricted to that exact meeting so a newer
    FTP meeting cannot displace the local input.
    """
    if urls is None:
        urls = [url] if url is not None else [INBOX_URL]

    all_sources: list[ScheduleSource] = []
    for u in urls:
        all_sources.extend(
            _discover_from_inbox(
                u,
                preferred_meeting_id=preferred_meeting_id,
                locked_meeting_id=locked_meeting_id,
            )
        )

    for folder in extra_folders or []:
        src = _source_from_extra_folder(
            folder,
            preferred_meeting_id=preferred_meeting_id,
            locked_meeting_id=locked_meeting_id,
        )
        if src is not None:
            all_sources.append(src)

    if local_schedule_sources:
        all_sources.extend(local_schedule_sources)

    current_meeting_id = _current_meeting_from_sources(
        all_sources,
        preferred_meeting_id=preferred_meeting_id,
    )
    return _dedup_sources(_filter_sources_to_meeting(all_sources, current_meeting_id))


def _fallback_chair_only(
    chair_url: str = BASE_URL,
    preferred_meeting_id: str | None = None,
    locked_meeting_id: str | None = None,
) -> list[ScheduleSource]:
    """Fallback: discover only the main Chair_notes schedule."""
    try:
        files = list_remote_files(chair_url)
        latest = find_latest_schedule(
            files,
            preferred_meeting_id=preferred_meeting_id,
            locked_meeting_id=locked_meeting_id,
        )
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
        if source.local_path is not None:
            local = source.local_path
            if local.suffix.lower() == ".zip":
                extracted = _find_extracted_document(local)
                if extracted is None:
                    extracted = extract_document_from_zip(local)
                if extracted is not None:
                    local = extracted
                else:
                    print(
                        f"  Warning: local ZIP {local.name} contained no "
                        f"supported document; skipping"
                    )
                    continue
        else:
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
    meeting_source: str | None = None,
    timezone: str | None = None,
    timezone_status: str | None = None,
    timezone_ref: dict | None = None,
    agenda: dict | None = None,
    local_refs: dict[str, str] | None = None,
) -> None:
    """Persist FTP state from already-fetched ScheduleSource objects.

    Called after a successful build so the next check job can compare
    without re-fetching from FTP.

    Optionally stores ``meeting_id`` (normalised, e.g. "ran1#124bis"),
    ``meeting_source`` (``"local"`` or ``"remote"``), and ``timezone``
    (IANA, e.g. "Europe/Malta") so that expensive per-meeting
    operations (like LLM timezone detection) are only performed once.
    ``agenda`` stores the remote/local agenda metadata that fed timezone and
    agenda-item description generation.  ``local_refs`` stores content
    hashes (name → sha256) of manually-provided documents in
    ``ref_in_manual/`` so ``check_update.py`` can detect when those
    committed local files change.
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
    if meeting_source in {"local", "remote"}:
        state["meeting_source"] = meeting_source
    if timezone is not None:
        state["timezone"] = timezone
    if timezone_status is not None:
        state["timezone_status"] = timezone_status
        state["timezone_ref"] = timezone_ref
    if agenda is not None:
        state["agenda"] = agenda
    if local_refs is not None:
        state["local_refs"] = local_refs

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(f"Schedule state saved ({len(info)} source(s)) → {state_path}")


def load_schedule_state(
    state_path: Path = Path("docs/.schedule_state.json"),
) -> dict:
    """Load persisted schedule state.

    Returns a dict with optional keys ``files`` (list[dict]), ``meeting_id``
    (str), ``meeting_source`` (``"local"`` or ``"remote"``), and
    ``timezone`` (str).  Returns an empty dict when the file is missing or
    unparsable.

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


def _collect_info_from_inbox(
    url: str,
    preferred_meeting_id: str | None = None,
    locked_meeting_id: str | None = None,
) -> list[dict]:
    """Lightweight directory scan of a single inbox for change detection.

    ``preferred_meeting_id`` is used as a stability hint from persisted
    state so older meetings do not temporarily displace the current one,
    while later regular meetings can still advance automatically.
    """
    out: list[dict] = []
    try:
        subfolders = list_inbox_subfolders(url)
    except Exception as e:
        print(f"Warning: Failed to list inbox subfolders at {url} ({e}), falling back to Chair_notes")
        chair_url = f"{url.rstrip('/')}/Chair_notes"
        info = get_remote_schedule_info(
            chair_url,
            preferred_meeting_id=preferred_meeting_id,
            locked_meeting_id=locked_meeting_id,
        )
        return [{"folder": "Chair_notes", **info}] if info else []

    for folder in subfolders:
        if folder["name"] in BLACKLISTED_FOLDERS:
            continue
        try:
            files = list_remote_files(folder["url"])
            latest = find_latest_schedule(
                files,
                preferred_meeting_id=preferred_meeting_id,
                locked_meeting_id=locked_meeting_id,
            )
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
    preferred_meeting_id: str | None = None,
    locked_meeting_id: str | None = None,
) -> list[dict]:
    """Return metadata of the schedule sources selected for a build.

    Aggregates across multiple inbox URLs (``urls``) and any manually-added
    ``extra_folders``. Used for change detection — only fetches directory
    listings, no downloads.

    ``preferred_meeting_id`` is the meeting id cached in
    ``docs/.schedule_state.json``. It helps keep the comparison stable
    across reruns while still allowing a newer regular meeting id to take
    precedence over the cached one.

    ``locked_meeting_id`` restricts selection to an authoritative local
    reference meeting and must match the build's discovery lock.

    This intentionally mirrors ``discover_schedule_sources`` because
    ``save_schedule_state`` persists those selected sources after a successful
    build. Comparing against the same selected/current-meeting set prevents
    older sibling inboxes from triggering false positives after the main
    meeting has advanced.
    """
    sources = discover_schedule_sources(
        url=url,
        urls=urls,
        extra_folders=extra_folders,
        preferred_meeting_id=preferred_meeting_id,
        locked_meeting_id=locked_meeting_id,
    )

    result: list[dict] = []
    for source in sources:
        uploaded_at = source.file_info.get("uploaded_at")
        result.append({
            "folder": source.folder_name,
            "name": source.file_info["name"],
            "uploaded_at": (
                uploaded_at.isoformat()
                if isinstance(uploaded_at, datetime)
                else uploaded_at
            ),
        })

    result.sort(key=lambda x: (x.get("folder", ""), x.get("name", "")))
    return result


# ── External files (config.json ``extra_files``) ───────────────

def _sanitize_filename(name: str) -> str:
    """Replace filesystem-unsafe characters and strip leading/trailing junk.

    ``\\ / : * ? " < > |`` become ``_``; control characters are dropped;
    leading/trailing whitespace and dots are stripped (Windows forbids
    trailing dots/spaces).  Caller decides what to do with an empty result.
    """
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = "".join(ch for ch in name if ord(ch) >= 0x20)
    return name.strip().strip(".")


def _resolve_external_filename(headers: dict, url: str, entry: dict, index: int) -> str:
    """Resolve the local filename for an external file download.

    Preference order: Content-Disposition ``filename*`` param, Content-
    Disposition ``filename`` param, last URL path segment (percent-decoded),
    entry ``name`` field, then ``external_<index>``.  Each candidate is
    sanitized; the first non-empty result wins.
    """
    final = f"external_{index}"

    for raw in _external_filename_candidates(headers, url, entry):
        sanitized = _sanitize_filename(raw)
        if sanitized:
            final = sanitized
            break

    return final


def _external_filename_candidates(
    headers: dict, url: str, entry: dict
) -> tuple[str | None, ...]:
    """Yield raw filename candidates in preference order (may be None)."""
    cd = headers.get("content-disposition")
    candidates: list[str | None] = []
    if cd:
        star = re.search(r"filename\*\s*=\s*([^;]+)", cd, re.IGNORECASE)
        if star:
            # e.g. filename*=UTF-8''A%20B.docx → decode after the second apostrophe
            raw_value = star.group(1).strip()
            if "''" in raw_value:
                raw_value = raw_value.split("''", 1)[1]
            candidates.append(unquote(raw_value))
        plain = re.search(r'filename\s*=\s*("([^"]*)"|([^;"\s]+))', cd, re.IGNORECASE)
        if plain:
            candidates.append(plain.group(2) if plain.group(2) is not None else plain.group(3))

    path_seg = unquote(urlsplit(url).path)
    if path_seg:
        last = path_seg.rsplit("/", 1)[-1]
        if last:
            candidates.append(last)

    if entry.get("name"):
        candidates.append(str(entry["name"]))

    return tuple(candidates)


def _sha256_of_response(resp: httpx.Response) -> str:
    """Compute the sha256 of a streaming response body (hex digest).

    Mirrors the content-hash approach of :func:`local_reference_hashes` —
    ETag/Last-Modified are not reliable on every host (e.g. ETSI's
    ``wa.exe`` provides neither), so change detection is content-based.
    """
    h = hashlib.sha256()
    for chunk in resp.iter_bytes(chunk_size=65536):
        h.update(chunk)
    return h.hexdigest()


def _sha256_of_file(path: Path) -> str:
    """Compute the sha256 of a local file without loading it all at once."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_external_one(
    url: str, entry: dict | None, index: int, dest_dir: Path
) -> tuple[Path, str]:
    """Download a single external file. Returns (final_path, sha256_hash).

    The hash is the sha256 of the downloaded body — the same value the
    build job persists in state and the check job compares against.
    Raises on exhausted retries or 4xx errors.
    """
    print(f"Downloading extra file: {url}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        tmp_path: Path | None = None
        try:
            with httpx.stream(
                "GET",
                url,
                follow_redirects=True,
                timeout=300,
                headers=_EXTERNAL_FILE_HEADERS,
            ) as resp:
                resp.raise_for_status()
                headers = dict(resp.headers)
                filename = _resolve_external_filename(headers, url, entry or {}, index)
                target = dest_dir / filename
                tmp_path = target.with_name(target.name + ".tmp")
                hasher = hashlib.sha256()
                with open(tmp_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=8192):
                        f.write(chunk)
                        hasher.update(chunk)
                content_hash = hasher.hexdigest()
            os.replace(tmp_path, target)
            tmp_path = None
            # Validate: error pages are typically small HTML files
            _validate_downloaded_file(target)
            print(f"Saved to: {target}")
            return target, content_hash
        except ServiceUnavailableError as exc:
            last_exc = exc
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            wait = _RETRY_BACKOFF_BASE * attempt
            print(
                f"  Extra file is a server error page "
                f"(attempt {attempt}/{_MAX_RETRIES}), retrying in {wait}s…"
            )
            time.sleep(wait)
        except (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException) as exc:
            last_exc = exc
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500:
                # Client errors (404 etc.) are not transient — don't retry
                raise
            wait = _RETRY_BACKOFF_BASE * attempt
            print(
                f"  Extra file download error (attempt {attempt}/{_MAX_RETRIES}): {exc!r}, "
                f"retrying in {wait}s…"
            )
            time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def download_external_files(
    extra_files: list[dict],
    dest_dir: Path = EXTRA_FILES_DIR,
    state: dict | None = None,
) -> tuple[list[tuple[dict, Path]], dict]:
    """Resolve externally-linked files (config.json ``extra_files``).

    A committed state entry is reused when its recorded filename exists in
    ``dest_dir`` and its local sha256 still matches.  Otherwise the URL is
    downloaded into ``dest_dir``.

    Filenames follow the ``curl -OJ`` convention (Content-Disposition →
    URL path → entry ``name``); ``.zip`` bodies are unpacked to the
    contained document so the folder scans see plain documents.  From
    here on the files are just files on disk, handled like
    ``ref_in_manual/`` documents.

    ``state`` can be supplied by callers that already loaded the persisted
    state; when omitted, ``docs/.extra_files_state.json`` is loaded.

    Returns ``(results, state)``: ``results`` is a list of
    ``(entry, path)`` pairs per successful download (``path`` being the
    cached or unpacked document for ZIPs), and ``state`` maps each successful URL to
    ``{"sha256": ..., "filename": ...}``.  The filename is the resolved
    downloaded filename and the hash uses the same content-hash scheme as
    :func:`local_reference_hashes`; ETag/Last-Modified are not reliable on
    all hosts.  Legacy URL-to-string-hash state remains readable.
    """
    results: list[tuple[dict, Path]] = []
    persisted_state = state if state is not None else load_external_files_state()
    previous_files = (
        persisted_state.get("files")
        if isinstance(persisted_state, dict)
        else None
    )
    if not isinstance(previous_files, dict):
        previous_files = {}
    new_state: dict[str, str | dict[str, str]] = {}

    for index, entry in enumerate(extra_files):
        url = entry["url"]
        cached = _cached_external_file(previous_files.get(url), dest_dir)
        if cached is not None:
            target, content_hash = cached
            print(f"Using cached extra file: {target}")
        else:
            try:
                target, content_hash = _download_external_one(
                    url,
                    entry,
                    index,
                    dest_dir,
                )
            except Exception as e:
                print(f"Warning: Failed to download extra file {url}: {e}")
                continue
        # Record the source filename (the ZIP filename when the body is a ZIP,
        # before any contained document is extracted).  A same-body filename
        # change is meaningful to local selection and must be observable.
        new_state[url] = {
            "sha256": content_hash,
            "filename": target.name,
        }

        doc = target
        if target.suffix.lower() == ".zip":
            extracted = extract_document_from_zip(target)
            if extracted is not None:
                print(f"  Unpacked extra ZIP → {extracted.name}")
                doc = extracted
        results.append((entry, doc))

    return results, new_state


def load_external_files_state(
    state_path: Path = EXTRA_FILES_STATE_PATH,
) -> dict:
    """Load persisted external-file state, accepting old hash-only entries."""
    import json

    if not state_path.exists():
        return {"files": {}}
    try:
        raw = json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"files": {}}

    if isinstance(raw, dict) and isinstance(raw.get("files"), dict):
        state = {"files": raw["files"]}
        if isinstance(raw.get("config"), list):
            state["config"] = raw["config"]
        return state
    return {"files": {}}


def save_external_files_state(
    state: dict,
    state_path: Path = EXTRA_FILES_STATE_PATH,
) -> None:
    """Persist external-file state (resolved filename and SHA-256 per URL)."""
    import json

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(f"Extra files state saved ({len(state.get('files', {}))} URL(s)) → {state_path}")


def _remote_file_sha256(url: str) -> str:
    """Fetch and hash (sha256) the remote body of a URL.

    Streaming GET — the body must be read to compute the hash (ETSI files
    are a few hundred KB at most, so this stays lightweight).  Raises on
    4xx/5xx and transport failures.
    """
    with httpx.stream(
        "GET",
        url,
        follow_redirects=True,
        timeout=60,
        headers=_EXTERNAL_FILE_HEADERS,
    ) as resp:
        resp.raise_for_status()
        return _sha256_of_response(resp)


def _remote_file_fingerprint(url: str, entry: dict, index: int) -> dict[str, str]:
    """Fetch an external file and return its resolved filename plus SHA-256."""
    with httpx.stream(
        "GET",
        url,
        follow_redirects=True,
        timeout=60,
        headers=_EXTERNAL_FILE_HEADERS,
    ) as resp:
        resp.raise_for_status()
        return {
            "sha256": _sha256_of_response(resp),
            "filename": _resolve_external_filename(dict(resp.headers), url, entry, index),
        }


def _external_state_hash(value: object) -> str | None:
    """Read a hash from either legacy or filename-aware external state."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("sha256"), str):
        return value["sha256"]
    return None


def _cached_external_file(value: object, dest_dir: Path) -> tuple[Path, str] | None:
    """Return a committed external file when its recorded hash still matches."""
    if not isinstance(value, dict):
        return None

    expected_hash = value.get("sha256")
    filename = value.get("filename")
    if not isinstance(expected_hash, str) or not isinstance(filename, str):
        return None
    if not filename or filename in {".", ".."}:
        return None
    if "/" in filename or "\\" in filename or Path(filename).name != filename:
        return None

    target = dest_dir / filename
    if not target.is_file():
        return None
    try:
        actual_hash = _sha256_of_file(target)
    except OSError:
        return None
    if actual_hash != expected_hash:
        return None
    return target, actual_hash


def _external_config_fingerprint(extra_files: list[dict]) -> list[dict]:
    """Return the routing metadata that affects an external file's use."""
    fingerprint = []
    for entry in extra_files:
        person_name = entry.get("person_name")
        is_main = entry.get("is_main")
        if not isinstance(is_main, bool):
            is_main = person_name is None
        fingerprint.append(
            {
                "url": entry.get("url"),
                "type": entry.get("type"),
                "name": entry.get("name") or None,
                "person_name": person_name,
                "is_main": is_main,
            }
        )
    return fingerprint


def check_external_files(
    extra_files: list[dict],
    state: dict | None = None,
    cache_dir: Path = EXTRA_FILES_DIR,
    staging_dir: Path | None = None,
) -> tuple[bool, dict]:
    """Check externally-linked files for configuration or cache changes.

    A committed file whose recorded filename and local SHA-256 match is reused
    without a network download.  Cache misses are fetched (streaming GET), and
    the resolved filename and body SHA-256 are compared against the persisted
    state.  A URL absent from the previous state counts as changed (first
    appearance, or a replaced ETSI wa.exe URL — the URL is regenerated per
    message).  When ``staging_dir`` is supplied, cache misses are downloaded
    there so a CI job can pass the files to a following build job.

    A URL that fails (404 / deleted / transport error) is **ignored**:
    it neither sets ``changed`` nor appears in the returned state, so a
    stale configured URL is dropped from state on the next build and
    removed URLs never block a rebuild.

    Returns ``(changed, {"files": {url: fingerprint}})`` containing only the
    successfully checked URLs. Legacy string-hash entries are retained in the
    returned shape until a successful build writes filename-aware state.
    """
    if extra_files == []:
        return False, {"files": {}}
    if state is None:
        state = load_external_files_state()
    prev_avail = state.get("files") if isinstance(state, dict) else None
    if not isinstance(prev_avail, dict):
        prev_avail = {}

    changed = False
    previous_config = state.get("config") if isinstance(state, dict) else None
    current_config = _external_config_fingerprint(extra_files)
    if isinstance(previous_config, list) and previous_config != current_config:
        changed = True
        print("External file configuration changed")

    new_state: dict[str, str | dict[str, str]] = {}
    for index, entry in enumerate(extra_files):
        url = entry["url"]
        previous = prev_avail.get(url)
        cached = _cached_external_file(previous, cache_dir)
        cache_mismatch = (
            isinstance(previous, dict)
            and isinstance(previous.get("sha256"), str)
            and isinstance(previous.get("filename"), str)
            and cached is None
        )
        if cached is not None:
            target, cached_hash = cached
            fingerprint = {
                "sha256": cached_hash,
                "filename": target.name,
            }
            print(f"Using cached extra file for check: {target}")
        else:
            try:
                if staging_dir is None:
                    fingerprint = _remote_file_fingerprint(url, entry, index)
                else:
                    target, content_hash = _download_external_one(
                        url,
                        entry,
                        index,
                        staging_dir,
                    )
                    fingerprint = {
                        "sha256": content_hash,
                        "filename": target.name,
                    }
            except Exception as e:
                print(
                    f"Warning: Could not check extra file {url}: {e} "
                    f"— skipping (no effect on changed/state)"
                )
                continue
            if cache_mismatch:
                changed = True
                print(f"Cached extra file missing or hash-mismatched: {url}")

        previous_hash = _external_state_hash(previous)
        filename_changed = (
            isinstance(previous, dict)
            and previous.get("filename") != fingerprint["filename"]
        )
        new_state[url] = (
            fingerprint
            if staging_dir is not None or not isinstance(previous, str)
            else fingerprint["sha256"]
        )
        if url not in prev_avail:
            changed = True
            print(f"Extra file added: {url}")
        elif previous_hash != fingerprint["sha256"] or filename_changed:
            changed = True
            detail = "filename or content changed" if filename_changed else "content changed"
            print(f"Extra file changed ({detail}): {url}")

    result: dict = {"files": new_state}
    if isinstance(previous_config, list):
        result["config"] = current_config
    return changed, result
