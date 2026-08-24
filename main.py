"""3GPP Schedule Parser — CLI entrypoint.

Downloads schedule DOCX from 3GPP FTP, parses tables, extracts sessions
using Gemini API, and generates a static HTML Gantt-chart visualization.

Usage:
    python main.py                           # Full pipeline (download + parse + generate)
    python main.py --local path/to/file.docx # Parse a local DOCX file
    python main.py --no-download             # Use latest local file, skip download

Environment variables:
    GEMINI_API_KEY  — Google Gemini API key (required unless --no-llm)
    SCHEDULE_CONTACT_NAME  — Contact name displayed in generated HTML
    SCHEDULE_CONTACT_EMAIL — Contact email displayed in generated HTML
    SCHEDULE_INBOX_URLS    — JSON array or comma-separated list of inbox URLs
                             to aggregate (overrides config.json / default)
    SCHEDULE_EXTRA_FOLDERS — JSON array of extra folder entries to include
                             (overrides config.json)

Config file:
    config.json (repo root, optional) — see config.py for schema.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from models import DAY_ORDER, DaySchedule, Schedule, ScheduleSource
from parser import build_room_list, parse_docx, extract_meeting_location, find_chair_notes_docx
from session_parser import parse_time_slots, get_timezone_from_location, normalize_group_headers, fill_missing_groups
from generator import save_html
from downloader import (
    download_latest_schedule,
    download_latest_chair_notes,
    download_latest_agenda,
    get_latest_agenda_info,
    get_latest_chair_notes_info,
    find_local_latest_schedule,
    find_local_latest_agenda,
    find_local_vice_chair_schedules,
    find_local_schedule_sources,
    local_reference_hashes,
    discover_schedule_sources,
    download_all_schedules,
    save_schedule_state,
    load_schedule_state,
    _extract_meeting_id,
    local_reference_meeting_id,
    download_external_files,
    save_external_files_state,
    _external_config_fingerprint,
    EXTRA_FILES_DIR,
    _local_doc_preference,
    _meeting_rank,
)
from merger import collect_time_slot_data
from config import load_config
from agenda_descriptions import (
    DEFAULT_JSON_PATH,
    update_agenda_description_json,
)


def _iso_value(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def _agenda_state_from_info(
    agenda_info: dict | None,
    agenda_path: Path | None,
) -> dict | None:
    """Build the agenda block stored in docs/.schedule_state.json."""
    if agenda_info is None and agenda_path is None:
        return None

    state: dict[str, object] = {}
    if agenda_info is not None:
        state.update(
            {
                "name": agenda_info.get("name"),
                "uploaded_at": _iso_value(agenda_info.get("uploaded_at")),
                "url": agenda_info.get("url"),
                "source_url": agenda_info.get("source_url"),
            }
        )
    if agenda_path is not None:
        state["document_file"] = agenda_path.name
        state["local_path"] = str(agenda_path)

    return {k: v for k, v in state.items() if v is not None}


def _agenda_state_from_description_json(json_path: Path) -> dict | None:
    """Recover reflected agenda metadata from agenda_item_description.json."""
    if not json_path.exists():
        return None

    try:
        data = json.loads(json_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    source_file = data.get("source_file")
    source_agenda_file = data.get("source_agenda_file") or source_file
    state = {
        "name": source_agenda_file,
        "uploaded_at": data.get("source_uploaded_at"),
        "url": data.get("source_url"),
        "document_file": source_file,
        "description_json": str(json_path),
        "description_generated_at": data.get("generated_at"),
        "description_source_type": data.get("source_type"),
        "description_source_file": source_file,
        "description_source_agenda_file": source_agenda_file,
        "description_source_url": data.get("source_url"),
        "description_source_uploaded_at": data.get("source_uploaded_at"),
    }
    return {k: v for k, v in state.items() if v is not None}


def _agenda_state_for_save(
    agenda_info: dict | None,
    agenda_path: Path | None,
    json_path: Path = DEFAULT_JSON_PATH,
) -> dict | None:
    """Combine remote/local agenda metadata with reflected description metadata."""
    state = _agenda_state_from_info(agenda_info, agenda_path) or {}
    description_state = _agenda_state_from_description_json(json_path) or {}
    for key, value in description_state.items():
        state.setdefault(key, value)
    return state or None


def _timezone_cache_is_current(
    state: dict,
    meeting_id: str | None,
    timezone_ref: dict | None,
) -> bool:
    """Return whether a saved timezone still describes the current input."""
    if not meeting_id or state.get("meeting_id") != meeting_id:
        return False
    if not state.get("timezone"):
        return False

    status = state.get("timezone_status")
    if status == "pending_timezone_ref":
        return state.get("timezone_ref") is None and timezone_ref is None
    if status == "resolved":
        return state.get("timezone_ref") == timezone_ref
    return False


def _timezone_reference_from_info(ref_type: str, info: dict) -> dict:
    uploaded_at = _iso_value(info.get("uploaded_at"))
    return {
        key: value
        for key, value in {
            "type": ref_type,
            "name": info.get("name"),
            "uploaded_at": uploaded_at,
            "url": info.get("url"),
            "source_url": info.get("source_url"),
        }.items()
        if value is not None
    }


def _timezone_reference_from_path(ref_type: str, path: Path) -> dict:
    return {
        "type": ref_type,
        "name": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "origin": "local",
    }


def _agenda_timezone_reference(
    agenda_info: dict | None,
    agenda_path: Path | None,
    previous_ref: dict | None,
) -> dict:
    """Preserve remote Agenda identity when only its cached file is visible."""
    if agenda_info is not None:
        return _timezone_reference_from_info("agenda", agenda_info)
    if isinstance(previous_ref, dict) and previous_ref.get("type") == "agenda":
        return previous_ref
    if agenda_path is None:
        raise ValueError("agenda_path is required without remote or cached metadata")
    return _timezone_reference_from_path("agenda", agenda_path)


def _can_preserve_cached_agenda(
    previous_state: dict,
    current_meeting_id: str | None,
    agenda_info_name: str,
) -> bool:
    previous_ref = previous_state.get("timezone_ref")
    return (
        current_meeting_id is not None
        and previous_state.get("meeting_id") == current_meeting_id
        and previous_state.get("timezone_status") == "resolved"
        and isinstance(previous_ref, dict)
        and previous_ref.get("type") == "agenda"
        and not agenda_info_name.endswith(".docx")
    )


def _extract_meeting_name(filepath: Path) -> str:
    """Try to extract meeting name from the filename."""
    name = filepath.stem
    match = re.search(r"(RAN\d+#\d+\w*)", name)
    if match:
        return match.group(1)
    return name


def _validate_contact_email(contact_email: str) -> str | None:
    """Validate contact email address."""
    if contact_email.count("@") != 1:
        return "SCHEDULE_CONTACT_EMAIL must contain exactly one '@' symbol"
    local_part, domain_part = contact_email.split("@", 1)
    if not local_part or not domain_part:
        return "SCHEDULE_CONTACT_EMAIL must include a local part and domain"
    if ".." in local_part or ".." in domain_part:
        return "SCHEDULE_CONTACT_EMAIL must not contain consecutive dots"
    if not re.match(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}\-]+$", local_part):
        return "SCHEDULE_CONTACT_EMAIL has an invalid local part"
    if not re.match(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*$", domain_part):
        return "SCHEDULE_CONTACT_EMAIL has an invalid domain"
    return None


def main():
    argparser = argparse.ArgumentParser(
        description="3GPP Schedule → Static Site Generator"
    )
    argparser.add_argument(
        "--local",
        type=str,
        help="Path to a local DOCX file to parse (skip download)",
    )
    argparser.add_argument(
        "--no-download",
        action="store_true",
        help="Skip download, use the latest local schedule file",
    )
    argparser.add_argument(
        "--output",
        type=str,
        default="docs/index.html",
        help="Output HTML file path (default: docs/index.html)",
    )
    argparser.add_argument(
        "--rebuild-slots",
        action="store_true",
        help=(
            "Wipe per-slot incremental state cache (docs/slot_state/) and "
            "rebuild from raw sources. Use when carry-forward errors have "
            "accumulated. Operators can also delete individual files from "
            "docs/slot_state/ via the GitHub web UI to force a cold "
            "rebuild of a single slot."
        ),
    )
    args = argparser.parse_args()

    if args.rebuild_slots:
        from slot_state import clear_all_slot_states

        n = clear_all_slot_states()
        print(
            f"Cleared {n} slot state file(s) from docs/slot_state/; "
            f"will rebuild from scratch."
        )

    contact_name = os.getenv("SCHEDULE_CONTACT_NAME")
    contact_email = os.getenv("SCHEDULE_CONTACT_EMAIL")
    if not contact_name or not contact_email:
        print(
            "Error: Environment variables SCHEDULE_CONTACT_NAME and "
            "SCHEDULE_CONTACT_EMAIL must be set",
            file=sys.stderr,
        )
        sys.exit(1)
    email_error = _validate_contact_email(contact_email)
    if email_error:
        print(f"Error: {email_error}", file=sys.stderr)
        sys.exit(1)
    cfg = load_config()
    prev_state = load_schedule_state()
    cached_meeting_id = prev_state.get("meeting_id")
    if not isinstance(cached_meeting_id, str) or not cached_meeting_id:
        cached_meeting_id = None
    local_meeting_hint = local_reference_meeting_id()
    preferred_meeting_id = local_meeting_hint or cached_meeting_id
    locked_meeting_id = local_meeting_hint
    cached_tz = prev_state.get("timezone")

    # Step 1: Get the DOCX file(s)
    docx_path: Path | None = None
    vice_chair_paths: dict[str, Path] = {}
    sources: list | None = None  # set during FTP discovery (step 1)
    extra_chair_notes_paths: list[Path] = []

    if args.local:
        docx_path = Path(args.local)
        if not docx_path.exists():
            print(f"Error: File not found: {docx_path}")
            sys.exit(1)
    elif args.no_download:
        # Locally-provided chairman references (ref_in_manual/) take
        # precedence over cached downloaded copies, which in turn take
        # precedence over extra_files (previously-downloaded external URLs).
        _local_ref_sources, _local_ref_chosen = find_local_schedule_sources(
            preferred_meeting_id=preferred_meeting_id
        )
        docx_path = _local_ref_chosen
        if docx_path is None:
            _extra_ref_sources, _extra_ref_chosen = find_local_schedule_sources(
                ref_dir=EXTRA_FILES_DIR
            )
            docx_path = _extra_ref_chosen
        if docx_path is None:
            docx_path = find_local_latest_schedule()
            if docx_path is None:
                print("Error: No schedule files found locally in downloads/Chair_notes/")
                sys.exit(1)
        print(f"Using local file: {docx_path}")
        # Discover local vice-chair schedules (mirrors default download behavior)
        vice_chair_paths = find_local_vice_chair_schedules()
        if vice_chair_paths:
            print(f"Vice-chair schedules: {', '.join(vice_chair_paths.keys())}")
        extra_chair_notes_paths = [
            find_chair_notes_docx(EXTRA_FILES_DIR, meeting_id=preferred_meeting_id)
        ]
        extra_chair_notes_paths = [p for p in extra_chair_notes_paths if p is not None]
    else:
        # Discover all schedule sources from configured inbox URLs
        print(
            "Discovering schedule sources from FTP "
            f"({len(cfg['inbox_urls'])} inbox URL(s), "
            f"{len(cfg['extra_folders'])} extra folder(s))..."
        )
        # Locally-provided chairman schedule references (ref_in_manual/)
        # always take precedence over FTP-discovered documents.
        local_ref_sources, _local_ref_chosen = find_local_schedule_sources(
            preferred_meeting_id=preferred_meeting_id
        )
        # Download external files (curl -OJL equivalent) into
        # downloads/extra_files/.  Schedule entries become local
        # ScheduleSources so they go through the same meeting filter /
        # dedup as ref_in_manual/; chair_notes entries are kept for the
        # timezone block below.
        ext_results: list[tuple[dict, Path]] = []
        if cfg.get("extra_files"):
            print(f"Resolving extra files ({len(cfg['extra_files'])} URL(s))…")
            try:
                ext_results, ext_file_state = download_external_files(
                    cfg["extra_files"]
                )
                save_external_files_state(
                    {
                        "files": ext_file_state,
                        "config": _external_config_fingerprint(cfg["extra_files"]),
                    }
                )
            except Exception as e:
                print(f"Warning: extra files download failed: {e}")
        else:
            # Keep the committed state aligned when extra_files is removed
            # from config; otherwise the check job would report a change on
            # every run after the removal.
            save_external_files_state(
                {
                    "files": {},
                    "config": _external_config_fingerprint([]),
                }
            )
        for entry, path in ext_results:
            if str(entry.get("type", "")).lower() == "chair_notes":
                extra_chair_notes_paths.append(path)
            else:
                local_ref_sources.append(
                    ScheduleSource(
                        folder_name=EXTRA_FILES_DIR.name,
                        person_name=entry.get("person_name"),
                        is_main=bool(entry.get("is_main", True)),
                        file_info={
                            "name": path.name,
                            "url": entry["url"],
                            "uploaded_at": None,
                        },
                        local_path=path,
                    )
                )
        local_main_ids = {
            _extract_meeting_id(source.file_info["name"])
            for source in local_ref_sources
            if source.is_main
            and _extract_meeting_id(source.file_info["name"]) is not None
        }
        regular_local_ids = [
            meeting_id
            for meeting_id in local_main_ids
            if _meeting_rank(meeting_id) is not None
        ]
        if regular_local_ids:
            locked_meeting_id = max(regular_local_ids, key=_meeting_rank)
            preferred_meeting_id = locked_meeting_id
        elif len(local_main_ids) == 1:
            # There is no total ordering for irregular meetings, but a
            # single explicit local meeting is still safe to pin exactly.
            locked_meeting_id = next(iter(local_main_ids))
            preferred_meeting_id = locked_meeting_id
        try:
            sources = discover_schedule_sources(
                urls=cfg["inbox_urls"],
                extra_folders=cfg["extra_folders"],
                local_schedule_sources=local_ref_sources,
                preferred_meeting_id=preferred_meeting_id,
                locked_meeting_id=locked_meeting_id,
            )
            if sources:
                print(f"Found {len(sources)} schedule source(s)")
                docx_path, vice_chair_paths = download_all_schedules(sources)
                if vice_chair_paths:
                    print(f"\nVice-chair schedules: {', '.join(vice_chair_paths.keys())}")
        except Exception as e:
            print(f"Discovery failed: {e}")

        if docx_path is None:
            # Fallback: try legacy single-download
            print("Falling back to Chair_notes only...")
            try:
                docx_path = download_latest_schedule()
            except Exception as e:
                print(f"Download failed: {e}")
                print("Trying local files...")
                docx_path = find_local_latest_schedule()
                if docx_path is None:
                    # Last resort: use the locally-provided chairman reference
                    docx_path = _local_ref_chosen
                if docx_path is None:
                    print("Error: No schedule files found locally either")
                    sys.exit(1)
                print(f"Using local file: {docx_path}")

    # Step 2: Parse DOCX tables
    print(f"\nParsing: {docx_path}")
    cells, tables_meta = parse_docx(docx_path)
    print(f"Extracted {len(cells)} schedule cells from {len(tables_meta)} tables")

    # Step 3: Build room mapping
    day_rooms_map = build_room_list(tables_meta)
    for day, rooms in sorted(
        day_rooms_map.items(),
        key=lambda x: DAY_ORDER.index(x[0]) if x[0] in DAY_ORDER else 99,
    ):
        room_names = [r.name for r in rooms]
        print(f"  {day}: {len(rooms)} rooms — {', '.join(room_names)}")

    # Extract meeting id early so slot-state freshness can invalidate the
    # incremental cache when the target meeting changes.
    current_meeting_id = _extract_meeting_id(docx_path.name)

    # Step 4: Parse sessions (always use time-slot grouping for fewer LLM calls)
    print("\nCollecting schedule data...")
    time_slots = collect_time_slot_data(
        cells, day_rooms_map, vice_chair_paths, meeting_id=current_meeting_id
    )
    n_enriched = sum(1 for s in time_slots if len(s.sources) > 1)
    if vice_chair_paths:
        print(f"  {len(time_slots)} time slots ({n_enriched} enriched with vice-chair detail)")
    else:
        print(f"  {len(time_slots)} time slots (from {len(cells)} cells)")

    agenda_path: Path | None = None
    agenda_info: dict | None = None
    agenda_urls = cfg.get("agenda_urls") or []
    if agenda_urls and not args.no_download:
        print(
            f"\nLooking up meeting agenda from "
            f"{len(agenda_urls)} agenda URL(s)..."
        )
        agenda_info = get_latest_agenda_info(agenda_urls)
        if agenda_info is not None:
            agenda_candidate_ref = _timezone_reference_from_info(
                "agenda",
                agenda_info,
            )
            agenda_candidate_name = str(agenda_info.get("name", "")).lower()
            agenda_path = download_latest_agenda(
                agenda_urls,
                latest_info=agenda_info,
                force=(
                    agenda_candidate_name.endswith(".docx")
                    and agenda_candidate_ref != prev_state.get("timezone_ref")
                ),
            )
        else:
            print("No agenda file found on FTP")
    if agenda_path is None:
        agenda_path = find_local_latest_agenda()
        if agenda_path is not None:
            print(f"\nUsing local agenda: {agenda_path.name}")

    if not args.no_download and (agenda_path is not None or not DEFAULT_JSON_PATH.exists()):
        print("\nFetching agenda item descriptions...")
        try:
            update_agenda_description_json(
                output_path=DEFAULT_JSON_PATH,
                agenda_docx_path=agenda_path,
                agenda_source_info=agenda_info,
            )
            print(f"  Wrote {DEFAULT_JSON_PATH}")
        except Exception as e:
            print(f"  Warning: failed to fetch agenda item descriptions: {e}")
    elif DEFAULT_JSON_PATH.exists():
        print(f"\nUsing agenda item descriptions: {DEFAULT_JSON_PATH}")

    print("\nParsing time slots (Gemini API)...")
    sessions = parse_time_slots(time_slots, day_rooms_map, meeting_id=current_meeting_id)
    print(f"Parsed {len(sessions)} sessions")

    # Step 4b: Normalize group headers for cleaner legend
    sessions = normalize_group_headers(sessions)

    # Step 4c: Fill missing groups by name/substring matching
    sessions = fill_missing_groups(sessions)

    # Step 5: Build Schedule model
    meeting_name = _extract_meeting_name(docx_path)

    # Resolve the best available timezone reference before consulting the cache.
    # Agenda DOCX and Chair notes DOCX/DOCM share the OOXML location extractor.
    meeting_tz = "UTC"
    timezone_status = "pending_timezone_ref"
    timezone_ref: dict | None = None
    remote_chair_info: dict | None = None
    previous_timezone_ref = prev_state.get("timezone_ref")
    agenda_info_name = str((agenda_info or {}).get("name", "")).lower()
    location_source: Path | None = (
        agenda_path
        if agenda_path
        and agenda_path.suffix.lower() == ".docx"
        and (agenda_info is None or agenda_info_name.endswith(".docx"))
        else None
    )
    if location_source is not None:
        timezone_ref = _agenda_timezone_reference(
            agenda_info,
            location_source,
            prev_state.get("timezone_ref"),
        )
    elif _can_preserve_cached_agenda(
        prev_state,
        current_meeting_id,
        agenda_info_name,
    ):
        timezone_ref = _agenda_timezone_reference(
            None,
            None,
            previous_timezone_ref,
        )
    else:
        local_chair_notes = find_chair_notes_docx(
            docx_path.parent,
            meeting_id=current_meeting_id,
        )
        matching_extra = [
            path
            for path in extra_chair_notes_paths
            if _extract_meeting_id(path.name) in {None, current_meeting_id}
        ]
        if matching_extra:
            location_source = max(matching_extra, key=_local_doc_preference)
            timezone_ref = _timezone_reference_from_path(
                "chair_notes",
                location_source,
            )
            print(f"  Using extra files chair notes: {location_source.name}")
        elif (args.local or args.no_download) and local_chair_notes is not None:
            location_source = local_chair_notes
            timezone_ref = _timezone_reference_from_path(
                "chair_notes",
                location_source,
            )
        elif not args.no_download:
            remote_chair_info = get_latest_chair_notes_info(
                urls=cfg["inbox_urls"],
                extra_folders=cfg["extra_folders"],
                preferred_meeting_id=current_meeting_id,
            )
            if remote_chair_info is not None:
                timezone_ref = _timezone_reference_from_info(
                    "chair_notes",
                    remote_chair_info,
                )

    if _timezone_cache_is_current(prev_state, current_meeting_id, timezone_ref):
        print(f"\nReusing cached timezone for {current_meeting_id}: {cached_tz}")
        meeting_tz = cached_tz
        timezone_status = prev_state["timezone_status"]
    else:
        if location_source is None and remote_chair_info is not None:
            print("\nDownloading timezone reference from FTP...")
            location_source = download_latest_chair_notes(
                docx_path.parent,
                urls=cfg["inbox_urls"],
                extra_folders=cfg["extra_folders"],
                preferred_meeting_id=current_meeting_id,
                latest_info=remote_chair_info,
                force=timezone_ref != prev_state.get("timezone_ref"),
            )
            if location_source is None:
                # Keep the reference pending so check_update retries after a
                # transient download failure instead of treating it as cached.
                timezone_ref = None

        if location_source:
            print(f"\nExtracting meeting location from: {location_source.name}")
            location_text = extract_meeting_location(location_source)
            if location_text:
                print(f"  Location line: {location_text}")
                tz = get_timezone_from_location(location_text)
                if tz:
                    meeting_tz = tz
                    timezone_status = "resolved"
                else:
                    timezone_status = "detection_failed"
            else:
                timezone_status = "detection_failed"
                print(
                    f"  Warning: Could not find location line in "
                    f"{location_source.name}"
                )
        else:
            print("\nWarning: No agenda or Chair notes DOCX found, using UTC timezone")

    # Persist state (FTP file listing + meeting metadata) for the next run.
    # Locally-provided chairman documents (ref_in_manual/) are excluded: the
    # state is compared by check_update.py against a fresh remote scan, and
    # local mtimes are not stable across CI checkouts.
    if sources is not None:
        selected_main_source = next((s for s in sources if s.is_main), None)
        meeting_source = (
            "local"
            if selected_main_source is not None
            and selected_main_source.local_path is not None
            else "remote"
        )
        save_schedule_state(
            [s for s in sources if s.local_path is None],
            meeting_id=current_meeting_id,
            meeting_source=meeting_source,
            timezone=meeting_tz,
            timezone_status=timezone_status,
            timezone_ref=timezone_ref,
            agenda=_agenda_state_for_save(agenda_info, agenda_path),
            local_refs=local_reference_hashes(),
        )

    days = []
    for day_name in DAY_ORDER:
        if day_name not in day_rooms_map:
            continue
        rooms = day_rooms_map[day_name]
        day_sessions = [s for s in sessions if s.day == day_name]
        if rooms:
            days.append(
                DaySchedule(
                    day_name=day_name,
                    rooms=rooms,
                    sessions=day_sessions,
                )
            )

    # Generate timestamp in meeting timezone
    from zoneinfo import ZoneInfo

    try:
        tz_info = ZoneInfo(meeting_tz)
        generated_at = datetime.now(tz_info).strftime("%Y-%m-%d %H:%M")
    except Exception:
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Collect all source files
    source_files = [docx_path.name]
    if vice_chair_paths:
        source_files.extend([p.name for p in vice_chair_paths.values()])

    schedule = Schedule(
        meeting_name=meeting_name,
        days=days,
        source_file=docx_path.name,
        source_files=source_files,
        generated_at=generated_at,
        contact_name=contact_name,
        contact_email=contact_email,
        timezone=meeting_tz,
    )

    # Step 6: Generate HTML
    print("\nGenerating HTML...")
    output_path = save_html(schedule, args.output)
    print(f"\nDone! Open {output_path} in a browser to view the schedule.")


if __name__ == "__main__":
    main()
