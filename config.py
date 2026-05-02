"""Schedule source configuration loader.

Config is split into two kinds of base URLs:

* ``meeting_sync`` (single base URL, optional) — the rolling sync folder
  that updates throughout a meeting, e.g.
  ``https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN1/``.  Only an ``Inbox``
  subfolder exists here; no ``Agenda``.
* ``meeting_specific`` (list of base URLs, optional) — per-meeting folders
  such as ``https://www.3gpp.org/ftp/tsg_ran/WG1_RL1/TSGR1_125/``.  Both
  ``Inbox`` and ``Agenda`` subfolders are available.

The loader derives two convenience lists from the above:

* ``inbox_urls``  — every base URL with ``Inbox/`` appended.
* ``agenda_urls`` — only the ``meeting_specific`` URLs with ``Agenda/``
  appended (used for timezone detection from the meeting agenda DOCX,
  which is typically uploaded earlier than the Chair notes).

Resolution order (highest precedence first):

    1. Environment variables  — convenient for CI overrides
    2. ``config.json`` in the repo root
    3. Hard-coded defaults

Environment variables
---------------------
SCHEDULE_MEETING_SYNC
    Single base URL string.
SCHEDULE_MEETING_SPECIFIC
    JSON array of base URL strings, or a comma-separated string.
SCHEDULE_INBOX_URLS  *(deprecated)*
    JSON array / comma-separated list.  Used as a fallback when neither
    ``SCHEDULE_MEETING_SYNC`` nor ``SCHEDULE_MEETING_SPECIFIC`` is set.
    These URLs are added to ``inbox_urls`` directly and never produce
    derived ``agenda_urls``.
SCHEDULE_EXTRA_FOLDERS
    JSON array of extra folder entries; each entry is an object with keys
    ``url`` (required), ``name`` (optional — derived from URL if missing),
    ``person_name`` (optional), ``is_main`` (optional, default false).

config.json shape
-----------------
{
  "meeting_sync": "https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN1/",
  "meeting_specific": [
    "https://www.3gpp.org/ftp/tsg_ran/WG1_RL1/TSGR1_125/"
  ],
  "extra_folders": [
    {"url": "https://.../custom/", "person_name": "Alice"}
  ]
}

The legacy ``inbox_urls`` key is still accepted (with a deprecation
warning) and contributes to the derived ``inbox_urls`` only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import unquote

DEFAULT_MEETING_SYNC = "https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN1/"
DEFAULT_CONFIG_PATH = Path("config.json")

# Kept for backwards compatibility with callers that still import this name.
DEFAULT_INBOX_URL = DEFAULT_MEETING_SYNC.rstrip("/") + "/Inbox/"


def _normalize_url(u: str) -> str:
    return u if u.endswith("/") else u + "/"


def _normalize_extra(entry: dict) -> dict | None:
    """Fill in ``name`` from the URL if missing; drop entries with no URL."""
    url = entry.get("url")
    if not url:
        return None
    url = _normalize_url(url)
    name = entry.get("name")
    if not name:
        name = unquote(url.rstrip("/").split("/")[-1]) or "extra"
    return {
        "url": url,
        "name": name,
        "person_name": entry.get("person_name"),
        "is_main": bool(entry.get("is_main", False)),
    }


def _parse_url_list_env(raw: str) -> list[str]:
    """Parse JSON array or comma-separated URL list from an env var."""
    try:
        candidate = json.loads(raw)
        if isinstance(candidate, list):
            return [str(u) for u in candidate if str(u).strip()]
    except json.JSONDecodeError:
        pass
    return [u.strip() for u in raw.split(",") if u.strip()]


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    """Load configuration from config.json + environment overrides.

    Returns a dict with these keys:

        meeting_sync     : str | None   — single base URL or None
        meeting_specific : list[str]    — list of base URLs
        inbox_urls       : list[str]    — derived: each base + 'Inbox/'
                                          (plus any legacy inbox_urls)
        agenda_urls      : list[str]    — derived from meeting_specific only,
                                          each base + 'Agenda/'
        extra_folders    : list[dict]   — manually-listed folders
    """
    meeting_sync: str | None = DEFAULT_MEETING_SYNC
    meeting_specific: list[str] = []
    legacy_inbox: list[str] = []
    extra_folders: list[dict] = []

    # 1. config.json
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: Failed to load {path}: {e}")
            data = {}

        if "meeting_sync" in data:
            ms = data.get("meeting_sync")
            meeting_sync = str(ms) if ms else None

        if isinstance(data.get("meeting_specific"), list):
            meeting_specific = [
                str(u) for u in data["meeting_specific"] if str(u).strip()
            ]

        if isinstance(data.get("inbox_urls"), list) and data["inbox_urls"]:
            print(
                "Warning: 'inbox_urls' in config.json is deprecated — "
                "use 'meeting_sync' / 'meeting_specific' instead."
            )
            legacy_inbox = [str(u) for u in data["inbox_urls"] if str(u).strip()]
            # Heuristic migration: if neither new key is set, infer them so
            # agenda_urls can still be derived for per-meeting URLs.
            if "meeting_sync" not in data and "meeting_specific" not in data:
                meeting_sync = None
                meeting_specific = []
                for u in legacy_inbox:
                    base = u.rstrip("/")
                    if base.lower().endswith("/inbox"):
                        base = base[: -len("/inbox")]
                    if "/Meetings_3GPP_SYNC/" in base and meeting_sync is None:
                        meeting_sync = base + "/"
                    else:
                        meeting_specific.append(base + "/")
                legacy_inbox = []  # consumed by migration

        if isinstance(data.get("extra_folders"), list):
            extra_folders = [e for e in data["extra_folders"] if isinstance(e, dict)]

    # 2. Environment overrides
    env_sync = os.environ.get("SCHEDULE_MEETING_SYNC")
    env_specific = os.environ.get("SCHEDULE_MEETING_SPECIFIC")
    env_legacy = os.environ.get("SCHEDULE_INBOX_URLS")

    if env_sync is not None:
        meeting_sync = env_sync.strip() or None
    if env_specific is not None:
        meeting_specific = _parse_url_list_env(env_specific)
    if env_legacy and not env_sync and not env_specific:
        print(
            "Warning: SCHEDULE_INBOX_URLS is deprecated — "
            "use SCHEDULE_MEETING_SYNC / SCHEDULE_MEETING_SPECIFIC instead."
        )
        legacy_inbox = _parse_url_list_env(env_legacy)

    env_extras = os.environ.get("SCHEDULE_EXTRA_FOLDERS")
    if env_extras:
        try:
            parsed_extras = json.loads(env_extras)
            if isinstance(parsed_extras, list):
                extra_folders = [e for e in parsed_extras if isinstance(e, dict)]
        except json.JSONDecodeError:
            print("Warning: SCHEDULE_EXTRA_FOLDERS is not valid JSON — ignoring")

    # 3. Normalise + derive
    if meeting_sync:
        meeting_sync = _normalize_url(meeting_sync)
    meeting_specific = [_normalize_url(u) for u in meeting_specific]

    base_urls: list[str] = []
    if meeting_sync:
        base_urls.append(meeting_sync)
    base_urls.extend(meeting_specific)

    inbox_urls = [_normalize_url(b.rstrip("/") + "/Inbox") for b in base_urls]
    inbox_urls.extend(_normalize_url(u) for u in legacy_inbox)
    # Deduplicate while preserving order
    seen: set[str] = set()
    inbox_urls = [u for u in inbox_urls if not (u in seen or seen.add(u))]

    agenda_urls = [
        _normalize_url(b.rstrip("/") + "/Agenda") for b in meeting_specific
    ]

    extra_folders = [
        e for e in (_normalize_extra(x) for x in extra_folders) if e is not None
    ]

    return {
        "meeting_sync": meeting_sync,
        "meeting_specific": meeting_specific,
        "inbox_urls": inbox_urls,
        "agenda_urls": agenda_urls,
        "extra_folders": extra_folders,
    }
