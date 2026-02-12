"""Parse session text using Gemini API."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from models import CellData, RoomInfo, Session, time_to_minutes, minutes_to_time


CACHE_DIR = Path(".cache")

# ── JSON Schemas for structured output ───────────────────────────

SESSION_PARSE_SCHEMA = {
    "type": "object",
    "properties": {
        "sessions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "duration_minutes": {"type": "integer"},
                    "chair": {"type": "string", "nullable": True},
                    "group_headers": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["name", "duration_minutes", "chair", "group_headers"],
            },
        }
    },
    "required": ["sessions"],
}

TIMEZONE_SCHEMA = {
    "type": "object",
    "properties": {
        "timezone": {"type": "string"},
        "city": {"type": "string"},
        "country": {"type": "string"},
    },
    "required": ["timezone", "city", "country"],
}

ROOM_DETECT_SCHEMA = {
    "type": "object",
    "properties": {
        "room_names": {
            "type": "array",
            "items": {"type": "string"},
        },
        "reasoning": {"type": "string"},
    },
    "required": ["room_names", "reasoning"],
}

MULTI_SOURCE_SESSION_SCHEMA = {
    "type": "object",
    "properties": {
        "sessions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "room_name": {"type": "string"},
                    "name": {"type": "string"},
                    "duration_minutes": {"type": "integer"},
                    "chair": {"type": "string", "nullable": True},
                    "group_header": {"type": "string"},
                    "agenda_item": {"type": "string", "nullable": True},
                },
                "required": ["room_name", "name", "duration_minutes", "chair", "group_header", "agenda_item"],
            },
        }
    },
    "required": ["sessions"],
}


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
                    model="gemini-3-flash-preview",
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION,
                        temperature=0.1,
                        response_mime_type="application/json",
                        response_json_schema=SESSION_PARSE_SCHEMA,
                        thinking_config=types.ThinkingConfig(thinking_level="minimal")
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



def get_timezone_from_location(location_text: str) -> str | None:
    """Use Gemini to determine the IANA timezone from a meeting location string.

    Args:
        location_text: e.g. "Gothenburg, SE, Feb. 9th ~ 13th, 2026"

    Returns:
        IANA timezone string (e.g. "Europe/Stockholm") or None on failure.
    """
    import time as _time
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Warning: GEMINI_API_KEY not set, cannot determine timezone")
        return None

    # Check cache
    cache_key = hashlib.sha256(f"tz:{location_text}".encode()).hexdigest()[:16]
    cached = _load_cache(f"tz_{cache_key}")
    if cached is not None:
        tz = cached.get("timezone")
        if tz:
            print(f"Using cached timezone: {tz}")
            return tz

    client = genai.Client(
        api_key=api_key,
        http_options={"timeout": 30_000},
    )

    prompt = f"""Given this 3GPP meeting location line, return the IANA timezone identifier for the city.

Location: "{location_text}"

Return ONLY valid JSON: {{"timezone": "IANA/Timezone", "city": "CityName", "country": "CountryName"}}"""

    try:
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                response_json_schema=TIMEZONE_SCHEMA,
                thinking_config=types.ThinkingConfig(thinking_level="minimal"),
            ),
        )
        result = json.loads(response.text.strip())
        tz = result.get("timezone")
        city = result.get("city", "")
        country = result.get("country", "")
        if tz:
            print(f"Meeting location: {city}, {country} → timezone: {tz}")
            _save_cache(f"tz_{cache_key}", result)
            return tz
    except Exception as e:
        print(f"Warning: Failed to determine timezone from location: {e}")

    return None


def detect_room_from_context(
    context_text: str,
    available_rooms: list[str],
    num_rooms_needed: int = 1,
) -> list[str] | None:
    """Use Gemini to determine which room(s) a schedule table belongs to.

    Analyses the paragraph text preceding a table in the DOCX to determine
    which room(s) from the available set this table represents.

    Args:
        context_text: Paragraph text preceding the table in the document.
        available_rooms: List of known room names from the main schedule.
        num_rooms_needed: How many room names to return (matches the number
            of room columns in the table).

    Returns:
        List of room names from available_rooms, or None if detection fails.
    """
    if not context_text.strip():
        return None

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    # Check cache
    cache_input = f"room:{context_text}:{','.join(available_rooms)}:{num_rooms_needed}"
    cache_key = hashlib.sha256(cache_input.encode()).hexdigest()[:16]
    cached = _load_cache(f"room_{cache_key}")
    if cached is not None:
        names = cached.get("room_names", [])
        if names:
            print(f"  Room detection (cached): {names}")
            return names

    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=api_key,
        http_options={"timeout": 30_000},
    )

    prompt = f"""Given this title/heading text from a 3GPP RAN1 meeting schedule document, determine which room(s) this schedule table covers.

Title text:
\"\"\"{context_text}\"\"\"

Available rooms in this meeting: {available_rooms}

I need exactly {num_rooms_needed} room name(s) from the available rooms list.

Rules:
- Match room codes mentioned in the title to available room names.
  e.g. "A3" in title matches "A3" in available rooms.
  e.g. "F1/2/3" matches "F1+F2+F3".
- "Brk#1" or "RAN1_Brk#1" → the first break-out room (typically "A1" or similar).
- "Brk#2" or "RAN1_Brk#2" → the second break-out room (typically "A3" or similar).
- "Main Session" → the main/largest room (typically "F1+F2+F3" or similar).
- "Online Session Schedule" without room specifics → the combined main room.
- "Offline Session Schedule" → offline rooms (e.g. "J1", "J2").
- If the title explicitly states a room code like "(room: RAN1_Brk#2, A3, Level 1)",
  match "A3" directly from the available rooms.
- Return room names EXACTLY as they appear in the available rooms list.
- If you truly cannot determine the room, return the first {num_rooms_needed} available rooms."""

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                response_json_schema=ROOM_DETECT_SCHEMA,
            ),
        )
        result = json.loads(response.text.strip())
        names = result.get("room_names", [])

        # Validate: only accept names that are in available_rooms
        valid_names = [n for n in names if n in available_rooms]

        if valid_names and len(valid_names) == num_rooms_needed:
            _save_cache(f"room_{cache_key}", {"room_names": valid_names})
            reasoning = result.get("reasoning", "")
            context_preview = context_text[:80].replace('\n', ' ')
            print(f"  Room detection: '{context_preview}' → {valid_names}")
            if reasoning:
                print(f"    Reasoning: {reasoning}")
            return valid_names
        elif valid_names:
            # Partial match — still useful
            _save_cache(f"room_{cache_key}", {"room_names": valid_names})
            return valid_names
    except Exception as e:
        print(f"  Warning: Room detection LLM call failed: {e}")

    return None


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


# ── Multi-source time-slot parsing ───────────────────────────────


MULTI_SOURCE_SYSTEM_INSTRUCTION = """You produce a unified session list for a 3GPP RAN1 time-slot.

You receive MULTIPLE schedule tables that all describe the SAME time duration.
They come from different sources (main chair schedule + vice-chair schedules).
Your job: cross-reference ALL sources and output the MOST DETAILED session list
for each target room.

## Cell text syntax
- "(N)" after a name means N minutes duration.
- A line whose duration = sum of subsequent lines → GROUP HEADER (not a session).
- Lines starting with "." are sub-items of the preceding item.
- Lines without "(N)" before sessions are context/category labels → group_headers.
- Person names (Xiaodong, Sorour, Hiroki) as group headers → session chair.

## How to parse a cell (with examples)

Example 1 – chair + category + sub-items:
  Xiaodong (120) / 6GR / .10.6.x (60) / .10.5.4.1 (40) / .10.5.4.3 (20)
  → Xiaodong(120) = 60+40+20 → chair header. "6GR" = category label.
  Result: [{name:"10.6.x", dur:60, chair:"Xiaodong", group_headers:["6GR"]},
           {name:"10.5.4.1", dur:40, chair:"Xiaodong", group_headers:["6GR"]},
           {name:"10.5.4.3", dur:20, chair:"Xiaodong", group_headers:["6GR"]}]

Example 2 – nested group headers:
  R20 (80) / NTN-NR (40) / NTN-IoT (40) / 6GR (40) / .10.7.1 NTN (40)
  → R20(80) = 40+40 → header. 6GR(40) = 40 → header. Leaves: NTN-NR, NTN-IoT, 10.7.1 NTN.
  Result: [{name:"NTN-NR", dur:40, group_headers:["R20"]},
           {name:"NTN-IoT", dur:40, group_headers:["R20"]},
           {name:"10.7.1 NTN", dur:40, group_headers:["6GR"]}]

Example 3 – single leaf:
  R20 A-IoT (120)
  → No sub-items → leaf session.
  Result: [{name:"R20 A-IoT", dur:120}]

## Multi-source merging

1. The Main Schedule defines the AUTHORITATIVE room layout.
   Each main-schedule cell tells you exactly what topics are scheduled in each target room.

2. Vice-chair sources add DETAIL (e.g. AI numbers, sub-session breakdowns).
   Their room labels are UNRELIABLE — do NOT use them to decide target room assignment.

3. To merge: match vice-chair entries to main-schedule rooms by CONTENT:
   - Match by topic keyword and duration.
     e.g. Main has "AI/ML (120)" in Brk#1 → vice-chair shows "AI 9.1 R20 AI/ML (120) / AI 9.1.1 (60) / AI 9.1.2 (60)"
     → AI 9.1.1 and AI 9.1.2 go into Brk#1 (replacing the coarse "AI/ML" entry).
   - e.g. Main has "NTN-NR (40)" in Brk#1 → vice-chair shows "AI 9.6 R20 NTN-NR (40)"
     → Use "AI 9.6 R20 NTN-NR" as the enriched name for that session in Brk#1.

4. Always prefer the MOST SPECIFIC name. "AI 9.1.1" > "AI/ML". "AI 9.6 R20 NTN-NR" > "NTN-NR".

5. For offline rooms: the main schedule content is authoritative.
   e.g. "Hiroki (120) / R20 / A-IoT (120)" → Offline B has one session, chair=Hiroki.
   Vice-chair detail for offline rooms just confirms this.

6. Total leaf durations per room must NOT exceed the time block duration.

## Output format

```json
{
  "sessions": [
    {
      "room_name": "<exact target room name>",
      "name": "session name (include AI number if known)",
      "duration_minutes": N,
      "chair": "person or null",
      "group_header": "category labels joined by ' / ', or empty string",
      "agenda_item": "9.1.1 or null"
    }
  ]
}
```

- Use EXACTLY the target room names in room_name.
- Sessions for all rooms in a single flat array, grouped by room, chronologically ordered.
- Every target room should have at least one entry (if nothing scheduled, omit it).
- group_header is a single string (join multiple labels with " / "), not an array.
- Return ONLY valid JSON."""


def _build_time_slot_prompt(slot) -> str:
    """Build prompt for a multi-source time slot.

    Structures data so that each target room's content from all sources
    is grouped together, making cross-referencing easy for the LLM.

    Args:
        slot: a TimeSlotData object from merger.py
    """
    parts = []
    parts.append(
        f"Day: {slot.day}, Time Block: {slot.time_block_start}-{slot.time_block_end} "
        f"({slot.time_block_duration} min)"
    )

    room_names = [r.name for r in slot.main_rooms]
    parts.append(f"\nTARGET ROOMS: {', '.join(room_names)}")

    # Group: first show main schedule per room, then all vice-chair data
    main_source = slot.sources[0] if slot.sources else None
    vc_sources = slot.sources[1:] if len(slot.sources) > 1 else []

    # Main schedule (authoritative room→content mapping)
    if main_source:
        parts.append(f"\n## Main Schedule (defines what goes in each room)")
        for entry in main_source.entries:
            parts.append(f'\n[{entry.room_label}]')
            parts.append(entry.cell_text)

    # Vice-chair detail (adds specificity; room labels unreliable)
    if vc_sources:
        parts.append(f"\n## Vice-chair detail (match by CONTENT to target rooms, ignore room labels)")
        for source in vc_sources:
            for entry in source.entries:
                parts.append(f'\n[{source.label} — {entry.room_label}]')
                parts.append(entry.cell_text)

    return "\n".join(parts)


def _time_slot_cache_key(slot) -> str:
    """Generate a cache key for a time slot's combined data."""
    content = json.dumps(
        {
            "day": slot.day,
            "tb": slot.time_block_index,
            "rooms": [r.name for r in slot.main_rooms],
            "sources": [
                {
                    "label": s.label,
                    "entries": [(e.room_label, e.cell_text) for e in s.entries],
                }
                for s in slot.sources
            ],
        },
        sort_keys=True,
    )
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def parse_time_slots(
    time_slots: list,
    day_rooms_map: dict[str, list[RoomInfo]],
) -> list[Session]:
    """Parse all time slots into Session objects using multi-source Gemini calls.

    One Gemini call per time slot with all source data combined.

    Args:
        time_slots: list of TimeSlotData from merger.collect_time_slot_data()
        day_rooms_map: day → list[RoomInfo] for grid column mapping

    Returns:
        List of Session objects with calculated start/end times.
    """
    import time as _time
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is not set")

    client = genai.Client(
        api_key=api_key,
        http_options={"timeout": 120_000},
    )

    all_sessions: list[Session] = []
    cache_hits = 0
    api_calls = 0
    MAX_RETRIES = 3

    for slot_idx, slot in enumerate(time_slots):
        slot_label = f"{slot.day} TB{slot.time_block_index} ({slot.time_block_start}-{slot.time_block_end})"

        # Check cache
        ck = _time_slot_cache_key(slot)
        cached = _load_cache(f"slot_{ck}")
        if cached is not None:
            # Convert cached result to sessions
            sessions = _slot_result_to_sessions(cached, slot, day_rooms_map)
            all_sessions.extend(sessions)
            cache_hits += 1
            continue

        # Build prompt
        user_prompt = _build_time_slot_prompt(slot)
        n_sources = len(slot.sources)
        n_entries = sum(len(s.entries) for s in slot.sources)
        print(
            f"  [{slot_idx+1}/{len(time_slots)}] {slot_label} "
            f"({n_sources} sources, {n_entries} entries)...",
            end=" ", flush=True,
        )

        # Rate limit
        if api_calls > 0:
            _time.sleep(1.0)

        # Call Gemini
        parsed_result = None
        for attempt in range(MAX_RETRIES):
            try:
                response = client.models.generate_content(
                    model="gemini-3-flash-preview",
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=MULTI_SOURCE_SYSTEM_INSTRUCTION,
                        temperature=0.1,
                        response_mime_type="application/json",
                        response_json_schema=MULTI_SOURCE_SESSION_SCHEMA,
                        thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                    ),
                )
                parsed_result = json.loads(response.text.strip())
                break
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    wait = 5 * (attempt + 1)
                    print(f"retry({attempt+1}, wait {wait}s)...", end=" ", flush=True)
                    _time.sleep(wait)
                else:
                    print(f"FAILED: {e}")
                    parsed_result = {"sessions": []}

        api_calls += 1

        if parsed_result is None:
            parsed_result = {"sessions": []}

        # Cache the result
        _save_cache(f"slot_{ck}", parsed_result)

        # Convert to sessions
        sessions = _slot_result_to_sessions(parsed_result, slot, day_rooms_map)
        all_sessions.extend(sessions)

        n_sessions = len(sessions)
        print(f"{n_sessions} sessions")

    if cache_hits:
        print(f"  ({cache_hits} time slots from cache)")
    print(f"Multi-source parsing complete: {len(all_sessions)} sessions from {len(time_slots)} time slots")

    return all_sessions


def _slot_result_to_sessions(
    parsed: dict,
    slot,
    day_rooms_map: dict[str, list[RoomInfo]],
) -> list[Session]:
    """Convert a Gemini time-slot result into Session objects.

    Handles the flat schema format where sessions is a flat array
    with room_name on each entry. Groups by room and assigns
    sequential start/end times within each room.
    """
    sessions: list[Session] = []
    day_rooms = day_rooms_map.get(slot.day, [])
    flat_sessions = parsed.get("sessions", [])

    # Group sessions by room_name to assign sequential times
    from collections import OrderedDict
    rooms_ordered: OrderedDict[str, list[dict]] = OrderedDict()
    for sd in flat_sessions:
        rn = sd.get("room_name", "")
        if rn not in rooms_ordered:
            rooms_ordered[rn] = []
        rooms_ordered[rn].append(sd)

    for room_name, room_sessions in rooms_ordered.items():
        # Find grid columns for this room
        col_start, col_end = _find_room_columns(room_name, day_rooms)

        block_start_min = time_to_minutes(slot.time_block_start)
        current_min = block_start_min

        for sd in room_sessions:
            duration = sd.get("duration_minutes") or 0
            if duration <= 0:
                continue

            name = sd.get("name", "Unknown")
            group_header = sd.get("group_header", "")
            agenda_item = sd.get("agenda_item")

            # Post-process: extract agenda_item from name if not provided
            if not agenda_item:
                agenda_match = re.match(r"^(?:AI\s+)?\.?\s*(\d+\.\d[\d.xX]*)\s*(.*)", name)
                if agenda_match:
                    agenda_item = agenda_match.group(1).strip(".")
                    rest = agenda_match.group(2).strip()
                    if rest:
                        name = rest
                # Check group_header for agenda context
                if not agenda_item and group_header:
                    m = re.match(r"AI\s+(\d[\d.]*)", group_header)
                    if m:
                        agenda_item = m.group(1).strip(".")

            start_time = minutes_to_time(current_min)
            end_time = minutes_to_time(current_min + duration)

            session = Session(
                name=name,
                duration_minutes=duration,
                start_time=start_time,
                end_time=end_time,
                day=slot.day,
                room_col_start=col_start,
                room_col_end=col_end,
                chair=sd.get("chair"),
                agenda_item=agenda_item,
                group_header=group_header,
            )
            sessions.append(session)
            current_min += duration

    return sessions


def _find_room_columns(
    room_name: str,
    day_rooms: list[RoomInfo],
) -> tuple[int, int]:
    """Find grid column range for a room name.

    Returns (col_start, col_end) as 1-indexed grid columns.
    """
    # Exact match
    for idx, ri in enumerate(day_rooms):
        if ri.name == room_name:
            return (idx + 2, idx + 3)  # +2: col 1 = time label

    # Fuzzy match: check if room_name is contained in or contains the room info name
    room_lower = room_name.lower()
    for idx, ri in enumerate(day_rooms):
        ri_lower = ri.name.lower()
        if room_lower in ri_lower or ri_lower in room_lower:
            return (idx + 2, idx + 3)

    # Fallback
    return (2, 3)
