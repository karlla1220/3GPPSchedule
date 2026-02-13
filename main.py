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
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from models import DAY_ORDER, DaySchedule, Schedule
from parser import build_room_list, parse_docx, extract_meeting_location, find_chair_notes_docx
from session_parser import parse_time_slots, get_timezone_from_location, normalize_group_headers, fill_missing_groups
from generator import save_html
from downloader import (
    download_latest_schedule,
    download_latest_chair_notes,
    find_local_latest_schedule,
    find_local_vice_chair_schedules,
    discover_schedule_sources,
    download_all_schedules,
)
from merger import collect_time_slot_data


def _extract_meeting_name(filepath: Path) -> str:
    """Try to extract meeting name from the filename."""
    name = filepath.stem
    match = re.search(r"(RAN\d+#\d+)", name)
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
    args = argparser.parse_args()

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
    # Step 1: Get the DOCX file(s)
    docx_path: Path | None = None
    vice_chair_paths: dict[str, Path] = {}

    if args.local:
        docx_path = Path(args.local)
        if not docx_path.exists():
            print(f"Error: File not found: {docx_path}")
            sys.exit(1)
    elif args.no_download:
        docx_path = find_local_latest_schedule()
        if docx_path is None:
            print("Error: No schedule files found locally in Chair_notes/")
            sys.exit(1)
        print(f"Using local file: {docx_path}")
        # Discover local vice-chair schedules (mirrors default download behavior)
        vice_chair_paths = find_local_vice_chair_schedules()
        if vice_chair_paths:
            print(f"Vice-chair schedules: {', '.join(vice_chair_paths.keys())}")
    else:
        # Discover all schedule sources from Inbox/
        print("Discovering schedule sources from FTP...")
        try:
            sources = discover_schedule_sources()
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

    # Step 4: Parse sessions (always use time-slot grouping for fewer LLM calls)
    print("\nCollecting schedule data...")
    time_slots = collect_time_slot_data(cells, day_rooms_map, vice_chair_paths)
    n_enriched = sum(1 for s in time_slots if len(s.sources) > 1)
    if vice_chair_paths:
        print(f"  {len(time_slots)} time slots ({n_enriched} enriched with vice-chair detail)")
    else:
        print(f"  {len(time_slots)} time slots (from {len(cells)} cells)")

    print("\nParsing time slots (Gemini API)...")
    sessions = parse_time_slots(time_slots, day_rooms_map)
    print(f"Parsed {len(sessions)} sessions")

    # Step 4b: Normalize group headers for cleaner legend
    sessions = normalize_group_headers(sessions)

    # Step 4c: Fill missing groups by name/substring matching
    sessions = fill_missing_groups(sessions)

    # Step 5: Build Schedule model
    meeting_name = _extract_meeting_name(docx_path)

    # Detect meeting timezone from Chair notes DOCX
    meeting_tz = "UTC"
    chair_notes_path = find_chair_notes_docx(docx_path.parent)
    if chair_notes_path is None and not args.no_download:
        # Try downloading Chair notes from FTP
        print("\nNo local Chair notes found, downloading from FTP...")
        chair_notes_path = download_latest_chair_notes(docx_path.parent)
    if chair_notes_path:
        print(f"\nExtracting meeting location from: {chair_notes_path.name}")
        location_text = extract_meeting_location(chair_notes_path)
        if location_text:
            print(f"  Location line: {location_text}")
            tz = get_timezone_from_location(location_text)
            if tz:
                meeting_tz = tz
        else:
            print("  Warning: Could not find location line in Chair notes")
    else:
        print("\nWarning: No Chair notes DOCX found, using UTC timezone")

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

    schedule = Schedule(
        meeting_name=meeting_name,
        days=days,
        source_file=docx_path.name,
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
