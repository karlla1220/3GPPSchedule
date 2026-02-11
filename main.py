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
from parser import build_room_list, parse_docx
from session_parser import parse_sessions
from generator import save_html
from downloader import download_latest_schedule, find_local_latest_schedule


def _extract_meeting_name(filepath: Path) -> str:
    """Try to extract meeting name from the filename."""
    name = filepath.stem
    match = re.search(r"(RAN\d+#\d+)", name)
    if match:
        return match.group(1)
    return name


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
            "Error: SCHEDULE_CONTACT_NAME and SCHEDULE_CONTACT_EMAIL must be set",
            file=sys.stderr,
        )
        sys.exit(1)
    email_pattern = r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@" r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$"
    if not re.match(email_pattern, contact_email):
        print(
            "Error: SCHEDULE_CONTACT_EMAIL must be a valid email address",
            file=sys.stderr,
        )
        sys.exit(1)
    if any(char in contact_name for char in "<>"):
        print(
            "Error: SCHEDULE_CONTACT_NAME contains invalid characters",
            file=sys.stderr,
        )
        sys.exit(1)

    # Step 1: Get the DOCX file
    docx_path: Path | None = None

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
    else:
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

    # Step 4: Parse sessions
    print("\nParsing sessions (Gemini API)...")
    sessions = parse_sessions(cells, day_rooms_map)
    print(f"Parsed {len(sessions)} sessions")

    # Step 5: Build Schedule model
    meeting_name = _extract_meeting_name(docx_path)
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

    schedule = Schedule(
        meeting_name=meeting_name,
        days=days,
        source_file=docx_path.name,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        contact_name=contact_name,
        contact_email=contact_email,
    )

    # Step 6: Generate HTML
    print("\nGenerating HTML...")
    output_path = save_html(schedule, args.output)
    print(f"\nDone! Open {output_path} in a browser to view the schedule.")


if __name__ == "__main__":
    main()
