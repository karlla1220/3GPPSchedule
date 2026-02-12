"""Download schedule DOCX files from 3GPP FTP server."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import httpx
from bs4 import BeautifulSoup

BASE_URL = "https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN1/Inbox/Chair_notes"


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
        if not href.lower().endswith(".docx"):
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
    """Find the latest schedule DOCX file by upload timestamp.

    Looks for files containing 'schedule' in the name.
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
    versioned = []
    for f in schedule_files:
        match = re.search(r"v(\d+)\.docx$", f["name"], re.IGNORECASE)
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


def download_latest_schedule(dest_dir: Path = Path("Chair_notes")) -> Path:
    """Download the latest schedule DOCX from 3GPP FTP.

    Returns the path to the downloaded (or already existing) file.
    """
    files = list_remote_files()
    latest = find_latest_schedule(files)

    if latest is None:
        raise FileNotFoundError("No schedule files found on FTP server")

    dest_path = dest_dir / latest["name"]

    if dest_path.exists():
        print(f"File already exists: {dest_path}")
        return dest_path

    return download_file(latest["url"], dest_path)


def find_local_latest_schedule(dest_dir: Path = Path("Chair_notes")) -> Path | None:
    """Find the latest schedule DOCX in the local directory by modification time.

    Uses file modification time (mtime) to determine the latest file,
    since filenames may use meeting names instead of version numbers.
    """
    schedule_files = [
        f for f in dest_dir.glob("*.docx") if "schedule" in f.name.lower()
    ]

    if not schedule_files:
        return None

    latest = max(schedule_files, key=lambda f: f.stat().st_mtime)
    print(f"Latest local schedule (by mtime): {latest.name}")
    return latest


def find_latest_chair_notes(files: list[dict]) -> dict | None:
    """Find the latest Chair notes DOCX file by upload timestamp.

    Looks for files containing 'chair note' (case-insensitive) in the name.
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
    versioned = []
    for f in chair_files:
        match = re.search(r"v(\d+)\.docx$", f["name"], re.IGNORECASE)
        if match:
            versioned.append({**f, "version": int(match.group(1))})
    if versioned:
        return max(versioned, key=lambda x: x["version"])

    return chair_files[0]


def download_latest_chair_notes(dest_dir: Path = Path("Chair_notes")) -> Path | None:
    """Download the latest Chair notes DOCX from 3GPP FTP.

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
        print(f"Chair notes already exists: {dest_path}")
        return dest_path

    try:
        return download_file(latest["url"], dest_path)
    except Exception as e:
        print(f"Warning: Failed to download Chair notes: {e}")
        return None
