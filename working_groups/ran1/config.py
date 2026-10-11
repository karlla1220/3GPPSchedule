"""Schedule source configuration loader.

Config is split into two kinds of base URLs:

* ``meeting_sync`` (single base URL, optional) — the rolling sync folder
  that updates throughout a meeting, e.g.
  ``https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN1/``.  Only an ``Inbox``
  subfolder exists here; no ``Agenda``.
* ``meeting_specific`` (list of base URLs) — per-meeting folders such as
  ``https://www.3gpp.org/ftp/tsg_ran/WG1_RL1/TSGR1_125/``.  Both ``Inbox``
  and ``Agenda`` subfolders are available.  Written by each run:
  ``with_meeting_folders`` finds the folders of the next meeting and the
  built one (``shared/meeting_folders.py``) and records them here.
* ``meeting_specific_override`` (list of base URLs, optional) — set by
  hand; a folder here wins over the one found for its meeting.

The loader derives two convenience lists from the above:

* ``inbox_urls``  — every base URL with ``Inbox/`` appended.
* ``agenda_urls`` — only the meeting folder URLs with ``Agenda/``
  appended (used for timezone detection from the meeting agenda DOCX,
  which is typically uploaded earlier than the Chair notes).

Resolution order (highest precedence first):

    1. Environment variables  — convenient for CI overrides
    2. ``config.json`` beside this module
    3. Hard-coded defaults

Environment variables
---------------------
SCHEDULE_MEETING_SYNC
    Single base URL string.
SCHEDULE_MEETING_SPECIFIC_OVERRIDE (or SCHEDULE_MEETING_SPECIFIC)
    JSON array of base URL strings, or a comma-separated string; replaces
    ``meeting_specific_override``.
SCHEDULE_INBOX_URLS  *(deprecated)*
    JSON array / comma-separated list.  Used as a fallback when neither
    ``SCHEDULE_MEETING_SYNC`` nor ``SCHEDULE_MEETING_SPECIFIC`` is set.
    These URLs are added to ``inbox_urls`` directly and never produce
    derived ``agenda_urls``.
SCHEDULE_EXTRA_FOLDERS
    JSON array of extra folder entries; each entry is an object with keys
    ``url`` (required), ``name`` (optional — derived from URL if missing),
    ``person_name`` (optional), ``is_main`` (optional, default false).
SCHEDULE_EXTRA_FILES
    JSON array of external file entries; each entry is an object with keys
    ``url`` (required), ``type`` (required, one of ``schedule`` or
    ``chair_notes``), ``name`` (optional), ``person_name`` (optional),
    ``is_main`` (optional — unset: ``True`` when ``person_name`` is absent,
    ``False`` when present; explicit value always wins).

config.json shape
-----------------
{
  "meeting_sync": "https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN1/",
  "meeting_specific": [
    "https://www.3gpp.org/ftp/tsg_ran/WG1_RL1/TSGR1_125/"
  ],
  "meeting_specific_override": [],
  "extra_folders": [
    {"url": "https://.../custom/", "person_name": "Alice"}
  ],
  "extra_files": [
    {"url": "https://list.etsi.org/scripts/wa.exe?...", "type": "schedule"},
    {"url": "https://example.org/hiroki_notes.docx", "type": "schedule", "person_name": "Hiroki"},
    {"url": "https://example.org/chair_notes.docx", "type": "chair_notes"}
  ]
}

``extra_files`` entries reference single remote files (not folders). At
build time they are downloaded to ``downloads/ran1/extra_files/`` (equivalent
of ``curl -OJL`` — redirects followed, file name taken from
``Content-Disposition`` when present).  CI change-detection
(``check_update.py``) compares the resolved filename, sha256, and routing
metadata of each URL's content against ``docs/ran1/.extra_files_state.json``
(same content-hash scheme as ``ref_in_manual`` — ETSI provides no
ETag/Last-Modified headers).

The legacy ``inbox_urls`` key is still accepted (with a deprecation
warning) and contributes to the derived ``inbox_urls`` only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import unquote

DEFAULT_MEETING_SYNC = "https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN1/"
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")

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


def _normalize_extra_file(entry: dict) -> dict | None:
    """Validate/normalise a single ``extra_files`` entry.

    Requires a non-empty ``url`` and a ``type`` of ``schedule`` or
    ``chair_notes``; anything else is dropped with a warning.
    """
    if not isinstance(entry, dict):
        print(f"Warning: Skipping extra_files entry without valid url/type: {entry!r}")
        return None
    url = entry.get("url")
    url = url.strip() if isinstance(url, str) else ""
    etype = entry.get("type")
    if not url or etype not in ("schedule", "chair_notes"):
        print(f"Warning: Skipping extra_files entry without valid url/type: {entry!r}")
        return None
    person_name = entry.get("person_name")
    # Default: named entries are vice-chair (main=False); unnamed entries
    # are main (main=True).  An explicit bool always takes precedence.
    is_main_raw = entry.get("is_main")
    if isinstance(is_main_raw, bool):
        is_main = is_main_raw
    else:
        is_main = person_name is None
    return {
        "url": url,
        "type": etype,
        "name": entry.get("name") or None,
        "person_name": person_name,
        "is_main": is_main,
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
        meeting_specific : list[str]    — base URLs found (recorded)
        meeting_specific_override : list[str] — base URLs set by hand
        inbox_urls       : list[str]    — derived: each base + 'Inbox/'
                                          (plus any legacy inbox_urls)
        agenda_urls      : list[str]    — derived from meeting folders only,
                                          each base + 'Agenda/'
        extra_folders    : list[dict]   — manually-listed folders
        extra_files      : list[dict]   — manually-listed external files
    """
    meeting_sync: str | None = DEFAULT_MEETING_SYNC
    meeting_specific: list[str] = []
    meeting_specific_override: list[str] = []
    legacy_inbox: list[str] = []
    extra_folders: list[dict] = []
    extra_files: list[dict] = []

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
        if isinstance(data.get("meeting_specific_override"), list):
            meeting_specific_override = [
                str(u) for u in data["meeting_specific_override"] if str(u).strip()
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
                meeting_specific_override = []
                for u in legacy_inbox:
                    base = u.rstrip("/")
                    if base.lower().endswith("/inbox"):
                        base = base[: -len("/inbox")]
                    if "/Meetings_3GPP_SYNC/" in base and meeting_sync is None:
                        meeting_sync = base + "/"
                    else:
                        meeting_specific_override.append(base + "/")
                legacy_inbox = []  # consumed by migration

        if isinstance(data.get("extra_folders"), list):
            extra_folders = [e for e in data["extra_folders"] if isinstance(e, dict)]

        if isinstance(data.get("extra_files"), list):
            extra_files = [e for e in data["extra_files"] if isinstance(e, dict)]

    # 2. Environment overrides
    env_sync = os.environ.get("SCHEDULE_MEETING_SYNC")
    env_specific = os.environ.get(
        "SCHEDULE_MEETING_SPECIFIC_OVERRIDE", os.environ.get("SCHEDULE_MEETING_SPECIFIC")
    )
    env_legacy = os.environ.get("SCHEDULE_INBOX_URLS")

    if env_sync is not None:
        meeting_sync = env_sync.strip() or None
    if env_specific is not None:
        meeting_specific_override = _parse_url_list_env(env_specific)
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

    env_files = os.environ.get("SCHEDULE_EXTRA_FILES")
    if env_files:
        try:
            parsed_files = json.loads(env_files)
            if isinstance(parsed_files, list):
                extra_files = [e for e in parsed_files if isinstance(e, dict)]
        except json.JSONDecodeError:
            print("Warning: SCHEDULE_EXTRA_FILES is not valid JSON — ignoring")

    # 3. Normalise + derive
    if meeting_sync:
        meeting_sync = _normalize_url(meeting_sync)
    meeting_specific = [_normalize_url(u) for u in meeting_specific]
    meeting_specific_override = [_normalize_url(u) for u in meeting_specific_override]

    extra_folders = [
        e for e in (_normalize_extra(x) for x in extra_folders) if e is not None
    ]
    extra_files = [
        e for e in (_normalize_extra_file(x) for x in extra_files) if e is not None
    ]

    return _derive({
        "meeting_sync": meeting_sync,
        "meeting_specific": meeting_specific,
        "meeting_specific_override": meeting_specific_override,
        "legacy_inbox": [_normalize_url(u) for u in legacy_inbox],
        "extra_folders": extra_folders,
        "extra_files": extra_files,
    })


def _derive(cfg: dict) -> dict:
    """Fill ``inbox_urls``/``agenda_urls`` from the sync and meeting folders."""
    folders = list(dict.fromkeys(cfg["meeting_specific_override"] + cfg["meeting_specific"]))
    base_urls = ([cfg["meeting_sync"]] if cfg["meeting_sync"] else []) + folders
    inbox_urls = [_normalize_url(b.rstrip("/") + "/Inbox") for b in base_urls]
    inbox_urls.extend(cfg["legacy_inbox"])
    return {
        **cfg,
        # Deduplicate while preserving order
        "inbox_urls": list(dict.fromkeys(inbox_urls)),
        "agenda_urls": [_normalize_url(b.rstrip("/") + "/Agenda") for b in folders],
    }


def with_meeting_folders(
    cfg: dict,
    built_meeting_id: str | None,
    *,
    rows: list[dict] | None = None,
    today: str | None = None,
    http=None,
) -> dict:
    """``cfg`` plus the meeting folders found for this run.

    The meetings are the earliest one by Portal dates that has not ended
    and the built one, which stays until the next meeting's files replace
    it. ``meeting_specific_override`` wins for its meeting, and the folders
    found are recorded in ``meeting_specific``. A folder that cannot be
    looked up (outage) is left out for this run: the sync folder and the
    built state still work, as when the folder does not exist yet.
    """
    import httpx

    from shared import meeting_folders, portal_meetings

    if rows is None:
        try:
            rows = portal_meetings.get_meetings()
        except (httpx.HTTPError, ValueError) as exc:
            print(f"Warning: Portal unavailable ({exc}); finding meeting folders by name")
            rows = []
    if today is None:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    meetings = meeting_folders.upcoming("ran1", rows, today)[:1]
    if isinstance(built_meeting_id, str) and built_meeting_id:
        meetings.append(built_meeting_id.lower())
    try:
        found = meeting_folders.resolve(
            "ran1", meetings, rows=rows, override=cfg["meeting_specific_override"],
            recorded=cfg["meeting_specific"], http=http,
        )
    except httpx.HTTPError as exc:
        print(f"Warning: meeting folder lookup failed: {exc}")
        return cfg
    for meeting, url in found.items():
        print(f"  Meeting folder for {meeting}: {url}")
    manual = set(cfg["meeting_specific_override"])
    return _derive({**cfg, "meeting_specific": [u for u in found.values() if u not in manual]})
