"""Parse session text using Gemini API."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from models import CellData, Session, time_to_minutes, minutes_to_time


CACHE_DIR = Path(".cache")


def _cache_key(cells: list[CellData]) -> str:
    """Generate a cache key from cell data."""
    content = json.dumps(
        [(c.text, c.day, c.time_block_index, c.table_index) for c in cells],
        sort_keys=True,
    )
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _load_cache(key: str) -> list[dict] | None:
    """Load cached Gemini results."""
    cache_file = CACHE_DIR / f"{key}.json"
    if cache_file.exists():
        try:
            with open(cache_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _save_cache(key: str, data: list[dict]):
    """Save Gemini results to cache."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{key}.json"
    with open(cache_file, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


SYSTEM_INSTRUCTION = """You parse 3GPP RAN1 meeting schedule cells into leaf-level sessions.

A cell describes what happens in one or more rooms during a time block.

RULES:
1. "(N)" = N minutes duration.
2. A line whose duration equals the sum of subsequent items' durations → GROUP HEADER, not a session.
3. Lines without "(N)" before sessions are context labels → include in group_headers.
4. Lines starting with "." are sub-items of the preceding item.
5. Person names (e.g. Xiaodong, Sorour, Hiroki) appearing as group headers are chairs.
6. A line with "(N)" that has no sub-items summing to it → LEAF SESSION.
7. Group headers can nest. Always check sum-matching at every level.
8. Total leaf durations must not exceed the time block duration.
9. If no items have durations, estimate to fill the time block.
10. If cell mentions a start time (e.g. "commences at 09:00"), time before that is empty.

OUTPUT: {"sessions": [{"name":"...","duration_minutes":N,"chair":"person or null","group_headers":["labels"]}]}

EXAMPLES:

Xiaodong (120) / 6GR / Overall (60) / AI/ML (60)
→ Xiaodong(120)=60+60 → header (chair). 6GR = label.
[{"name":"Overall","duration_minutes":60,"chair":"Xiaodong","group_headers":["6GR"]},
 {"name":"AI/ML","duration_minutes":60,"chair":"Xiaodong","group_headers":["6GR"]}]

6GR (150) / Waveform(50) / Modulation(50) / Energy(50)
→ 6GR(150)=50×3 → header. No person → chair null.
[{"name":"Waveform","duration_minutes":50,"chair":null,"group_headers":["6GR"]},
 {"name":"Modulation","duration_minutes":50,"chair":null,"group_headers":["6GR"]},
 {"name":"Energy","duration_minutes":50,"chair":null,"group_headers":["6GR"]}]

Hiroki(40) / R20 ISAC(40) / Sorour(80) / R20 MIMO(40) / R20 NR-NTN(40)
→ Two chair blocks. Hiroki(40)=40, Sorour(80)=40+40.
[{"name":"R20 ISAC","duration_minutes":40,"chair":"Hiroki","group_headers":[]},
 {"name":"R20 MIMO","duration_minutes":40,"chair":"Sorour","group_headers":[]},
 {"name":"R20 NR-NTN","duration_minutes":40,"chair":"Sorour","group_headers":[]}]

R20 A-IoT (150)
→ No sub-items → leaf session.
[{"name":"R20 A-IoT","duration_minutes":150,"chair":null,"group_headers":[]}]

Xiaodong(120) / 6GR / Overall(40) / .TR skeleton(40) / Evaluation(80)
→ ".TR skeleton" is sub-item of Overall → Overall(40)=40 → nested header.
→ Xiaodong(120)=40+80 → chair header.
[{"name":"TR skeleton","duration_minutes":40,"chair":"Xiaodong","group_headers":["6GR","Overall"]},
 {"name":"Evaluation","duration_minutes":80,"chair":"Xiaodong","group_headers":["6GR"]}]

Return ONLY valid JSON: {"sessions": [...]}"""


def _build_cell_prompt(cell: CellData) -> str:
    """Build a user prompt for a single cell."""
    return f"""Day: {cell.day}, Time Block: {cell.time_block_start}-{cell.time_block_end} ({cell.time_block_duration} min)
Room indices: {cell.room_indices}
Text:
\"\"\"
{cell.text}
\"\"\""""


def _cell_cache_key(cell: CellData) -> str:
    """Generate a cache key for a single cell."""
    content = json.dumps(
        (cell.text, cell.day, cell.time_block_index, cell.table_index),
        sort_keys=True,
    )
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def parse_with_gemini(cells: list[CellData]) -> list[dict]:
    """Parse cells using Gemini API, one cell at a time (leverages prompt caching).

    Each cell result is cached individually so partial progress is preserved.
    Returns list of parsed cell results with cell_id keys.
    """
    import time as _time
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is not set")

    # Check full-batch cache first
    batch_key = _cache_key(cells)
    cached = _load_cache(batch_key)
    if cached is not None:
        print("Using cached Gemini results")
        return cached

    client = genai.Client(
        api_key=api_key,
        http_options={"timeout": 120_000},
    )

    all_parsed: list[dict] = []
    cache_hits = 0
    api_calls = 0
    MAX_RETRIES = 3

    for i, cell in enumerate(cells):
        cell_id = f"CELL_{i:03d}"

        # Check per-cell cache
        ck = _cell_cache_key(cell)
        cell_cached = _load_cache(f"cell_{ck}")
        if cell_cached is not None:
            cell_cached["cell_id"] = cell_id
            all_parsed.append(cell_cached)
            cache_hits += 1
            continue

        user_prompt = _build_cell_prompt(cell)
        print(f"  [{i+1}/{len(cells)}] {cell.day} TB{cell.time_block_index} rooms={cell.room_indices}...", end=" ", flush=True)

        # Rate-limit: 1 second between API calls
        if api_calls > 0:
            _time.sleep(1.0)

        for attempt in range(MAX_RETRIES):
            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION,
                        temperature=0.1,
                        response_mime_type="application/json",
                        thinking_config=types.ThinkingConfig(thinking_budget=0)
                    ),
                )
                break
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    wait = 5 * (attempt + 1)
                    print(f"retry({attempt+1}, wait {wait}s)...", end=" ", flush=True)
                    _time.sleep(wait)
                else:
                    print(f"FAILED: {e}")
                    parsed = {"cell_id": cell_id, "sessions": []}
                    all_parsed.append(parsed)
                    api_calls += 1
                    continue

        api_calls += 1
        result_text = response.text.strip()
        parsed = json.loads(result_text)

        # Normalize: ensure it has the cell_id wrapper
        if isinstance(parsed, dict) and "sessions" in parsed:
            parsed["cell_id"] = cell_id
        elif isinstance(parsed, list):
            parsed = {"cell_id": cell_id, "sessions": parsed}

        all_parsed.append(parsed)
        n_sessions = len(parsed.get("sessions", []))
        print(f"{n_sessions} sessions")

        # Cache individual cell
        _save_cache(f"cell_{ck}", parsed)

    # Cache the full batch
    _save_cache(batch_key, all_parsed)
    if cache_hits:
        print(f"  ({cache_hits} cells from cache)")
    print(f"Gemini parsing complete, {len(all_parsed)} cells parsed")

    return all_parsed





def parse_sessions(
    cells: list[CellData],
    day_rooms_map: dict[str, list],
) -> list[Session]:
    """Parse all cells into Session objects.

    Args:
        cells: Raw cell data from DOCX parser
        day_rooms_map: day -> list of RoomInfo (for column mapping)

    Returns:
        List of Session objects with calculated start/end times.
    """
    from parser import compute_room_global_col

    parsed_cells = parse_with_gemini(cells)

    all_sessions = []

    for i, cell in enumerate(cells):
        day_rooms = day_rooms_map.get(cell.day, [])
        col_start, col_end = compute_room_global_col(cell, day_rooms)

        # Find the matching parsed cell from Gemini results
        cell_id = f"CELL_{i:03d}"
        parsed = None
        for pc in parsed_cells:
            if pc.get("cell_id") == cell_id:
                parsed = pc
                break

        cell_sessions_data = parsed.get("sessions", []) if parsed else []

        # Fallback: when Gemini returns no sessions for a non-empty cell,
        # create a single session covering the full time block so the
        # schedule content isn't silently dropped.
        if not cell_sessions_data and cell.text.strip():
            # Build a readable name from the first meaningful lines
            lines = [
                ln.strip()
                for ln in cell.text.split("\n")
                if ln.strip() and not ln.strip().startswith(".")
            ]
            # Filter out meta lines (break notices, "resume at", etc.)
            name_lines = [
                ln
                for ln in lines
                if not any(
                    kw in ln.lower()
                    for kw in ("break", "resume", "commences", "expected to close")
                )
            ]
            fallback_name = " / ".join(name_lines[:3]) if name_lines else lines[0] if lines else cell.text[:60]
            cell_sessions_data = [
                {
                    "name": fallback_name,
                    "duration_minutes": cell.time_block_duration,
                    "chair": None,
                    "group_headers": [],
                }
            ]

        # Calculate start/end times from sequential positions
        block_start_min = time_to_minutes(cell.time_block_start)
        current_min = block_start_min

        for sd in cell_sessions_data:
            duration = sd.get("duration_minutes", 0)
            if duration <= 0:
                continue

            name = sd.get("name", "Unknown")
            group_headers = sd.get("group_headers", [])

            # Post-process: extract agenda_item from name
            agenda_item = sd.get("agenda_item")  # regex path provides this
            if agenda_item is None:
                agenda_match = re.match(r"^\.?\s*(\d+\.\d[\d.xX]*)\s*(.*)", name)
                if agenda_match:
                    agenda_item = agenda_match.group(1).strip(".")
                    rest = agenda_match.group(2).strip()
                    name = rest if rest else f"AI {agenda_item}"
                # Also check group_headers for agenda context
                if not agenda_item:
                    for h in group_headers:
                        m = re.match(r"AI\s+(\d[\d.]*)", h)
                        if m:
                            agenda_item = m.group(1).strip(".")
                            break

            start_time = minutes_to_time(current_min)
            end_time = minutes_to_time(current_min + duration)

            session = Session(
                name=name,
                duration_minutes=duration,
                start_time=start_time,
                end_time=end_time,
                day=cell.day,
                room_col_start=col_start,
                room_col_end=col_end,
                chair=sd.get("chair"),
                agenda_item=agenda_item,
                group_header=" / ".join(group_headers) if group_headers else "",
            )
            all_sessions.append(session)
            current_min += duration

    return all_sessions
