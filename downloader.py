"""Download schedule DOCX files from 3GPP FTP server."""

from __future__ import annotations

import re
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import httpx
from bs4 import BeautifulSoup

from models import ScheduleSource

BASE_URL = "https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN1/Inbox/Chair_notes"
INBOX_URL = "https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN1/Inbox/"

# Folders in Inbox/ that never contain schedule files
BLACKLISTED_FOLDERS = {"Agenda", "drafts", "Tdoc_list", "Welcome_speech"}

# Supported document extensions (order = preference when extracting from ZIP)
DOCUMENT_EXTENSIONS = (".docx", ".pptx", ".pdf")
# All extensions we accept from remote listings (documents + zip)
SUPPORTED_EXTENSIONS = DOCUMENT_EXTENSIONS + (".zip",)


def list_remote_files(url: str = BASE_URL) -> list[dict]:
    """Fetch the FTP directory listing and return file info with upload timestamps.

    Each returned dict has keys: name, url, uploaded_at (datetime | None).
    """
    resp = httpx.get(url, follow_redirects=True, timeout=30)
    resp.raise_for_status()

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


def find_latest_schedule(files: list[dict]) -> dict | None:
    """Find the latest schedule file by upload timestamp.

    Looks for files containing 'schedule' in the name.
    Supports .docx, .pptx, .pdf, and .zip files.
    Returns the one with the most recent upload time on the FTP server.
    Falls back to version number if timestamps are unavailable.
    """
    schedule_files = [f for f in files if "schedule" in f["name"].lower()]

    if not schedule_files:
        return None

    # Prefer sorting by upload timestamp
    files_with_ts = [f for f in schedule_files if f.get("uploaded_at") is not None]
    if files_with_ts:
        latest = max(files_with_ts, key=lambda x: x["uploaded_at"])
        print(f"Latest schedule (by upload time {latest['uploaded_at']}): {latest['name']}")
        return latest

    # Fallback: sort by version number in filename
    _ext_pattern = "|".join(re.escape(e) for e in SUPPORTED_EXTENSIONS)
    versioned = []
    for f in schedule_files:
        match = re.search(rf"v(\d+)(?:{_ext_pattern})$", f["name"], re.IGNORECASE)
        if match:
            versioned.append({**f, "version": int(match.group(1))})
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
    """Download a file from URL to dest_path."""
    print(f"Downloading: {url}")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as resp:
        resp.raise_for_status()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=8192):
                f.write(chunk)
    print(f"Saved to: {dest_path}")
    return dest_path


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


def download_latest_schedule(dest_dir: Path = Path("Chair_notes")) -> Path:
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


def find_local_latest_schedule(dest_dir: Path = Path("Chair_notes")) -> Path | None:
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
    base_dir: Path = Path("."),
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


def find_latest_chair_notes(files: list[dict]) -> dict | None:
    """Find the latest Chair notes file by upload timestamp.

    Looks for files containing 'chair note' (case-insensitive) in the name.
    Supports .docx, .pptx, .pdf, and .zip files.
    Returns the one with the most recent upload time.
    """
    chair_files = [
        f for f in files
        if "chair note" in f["name"].lower() or "chair_note" in f["name"].lower()
    ]

    if not chair_files:
        return None

    # Prefer sorting by upload timestamp
    files_with_ts = [f for f in chair_files if f.get("uploaded_at") is not None]
    if files_with_ts:
        latest = max(files_with_ts, key=lambda x: x["uploaded_at"])
        print(f"Latest Chair notes (by upload time {latest['uploaded_at']}): {latest['name']}")
        return latest

    # Fallback: sort by version number in filename
    _ext_pattern = "|".join(re.escape(e) for e in SUPPORTED_EXTENSIONS)
    versioned = []
    for f in chair_files:
        match = re.search(rf"v(\d+)(?:{_ext_pattern})$", f["name"], re.IGNORECASE)
        if match:
            versioned.append({**f, "version": int(match.group(1))})
    if versioned:
        return max(versioned, key=lambda x: x["version"])

    return chair_files[0]


def download_latest_chair_notes(dest_dir: Path = Path("Chair_notes")) -> Path | None:
    """Download the latest Chair notes from 3GPP FTP.

    Supports .docx, .pptx, .pdf, and .zip (auto-extracted).
    Returns the path to the downloaded file, or None if not found.
    """
    try:
        files = list_remote_files()
    except Exception as e:
        print(f"Warning: Could not list FTP for Chair notes: {e}")
        return None

    latest = find_latest_chair_notes(files)
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
    resp = httpx.get(url, follow_redirects=True, timeout=30)
    resp.raise_for_status()

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


def discover_schedule_sources(url: str = INBOX_URL) -> list[ScheduleSource]:
    """Discover all schedule sources from Inbox/ subfolders.

    Scans each non-blacklisted subfolder for schedule files.
    Also checks the Inbox root for schedule files with person names.

    Returns a list of ScheduleSource objects.
    """
    sources: list[ScheduleSource] = []

    # 1. Scan subfolders
    try:
        subfolders = list_inbox_subfolders(url)
    except Exception as e:
        print(f"Warning: Failed to list Inbox subfolders: {e}")
        # Fallback to Chair_notes only
        return _fallback_chair_only()

    for folder in subfolders:
        if folder["name"] in BLACKLISTED_FOLDERS:
            continue

        folder_url = folder["url"]
        try:
            files = list_remote_files(folder_url)
        except Exception as e:
            print(f"  Warning: Cannot access {folder['name']}/: {e}")
            continue

        latest = find_latest_schedule(files)
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
            # Skip if we already have a source for this person from their folder
            existing_persons = {s.person_name for s in sources if s.person_name}
            if person in existing_persons:
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
        print("  Warning: No main schedule (Chair_notes) found, using fallback")
        fallback = _fallback_chair_only()
        if fallback:
            sources.extend(fallback)

    return sources


def _fallback_chair_only() -> list[ScheduleSource]:
    """Fallback: discover only the main Chair_notes schedule."""
    try:
        files = list_remote_files(BASE_URL)
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
    source: ScheduleSource, base_dir: Path = Path("."),
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
    sources: list[ScheduleSource], base_dir: Path = Path("."),
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


def get_all_remote_schedule_info(url: str = INBOX_URL) -> list[dict]:
    """Return metadata of all schedule files across Inbox subfolders.

    Used for change detection — only fetches directory listings.
    Returns list of dicts with folder_name, name, uploaded_at.
    """
    all_info: list[dict] = []

    try:
        subfolders = list_inbox_subfolders(url)
    except Exception:
        # Fallback to single Chair_notes check
        info = get_remote_schedule_info()
        return [info] if info else []

    for folder in subfolders:
        if folder["name"] in BLACKLISTED_FOLDERS:
            continue
        try:
            files = list_remote_files(folder["url"])
            latest = find_latest_schedule(files)
            if latest:
                all_info.append({
                    "folder": folder["name"],
                    "name": latest["name"],
                    "uploaded_at": (
                        latest["uploaded_at"].isoformat()
                        if latest.get("uploaded_at")
                        else None
                    ),
                })
        except Exception:
            continue

    return all_info
