"""Parse session text using Gemini API."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from models import RoomInfo, Session, time_to_minutes, minutes_to_time


CACHE_DIR = Path(".cache")
ROOM_DETECT_PROMPT_VERSION = 2

# ── JSON Schemas for structured output ───────────────────────────

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

GROUP_SIMPLIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "mappings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "original": {"type": "string"},
                    "simplified": {"type": "string"},
                },
                "required": ["original", "simplified"],
            },
        }
    },
    "required": ["mappings"],
}

GROUP_SIMPLIFY_SYSTEM_INSTRUCTION = """You simplify and normalize group header labels from a 3GPP RAN1 meeting schedule.

You receive a list of unique group_header strings currently used as legend labels in a
Gantt-chart schedule visualization. Many of them are overly specific — they include
sub-topic names, agenda-item numbers, or nested category paths joined by " / ".

Your task: map every input label to a SIMPLIFIED representative category so that the
final legend has a small, meaningful set of groups (typically 5–12).

Rules:
1. If a label has the form "X / Y / Z", X is usually the top-level category.
   Decide the appropriate level of simplification based on the FULL list.
2. Merge labels that clearly refer to the same work area.
   e.g. "R20 / AI 9.1. R20 AI/ML", "R20 / Coverage / R20 Coverage",
        "R20 / AI/ML / AI 9.1 R20 AI/ML", "R20 / ISAC" → all map to "R20".
3. "6GR / 10.2.1", "6GR / 10.2.1 Waveform" → "6GR".
4. "AI 7/8 / Maintenance" → "AI 7/8"  (drop the sub-detail).
5. Labels like "To be assigned by <name>" → "TBD".
6. Labels that are ALREADY simple and appear as top-level categories for others
   should remain unchanged (e.g. "R20", "6GR", "Maintenance", "AI 8").
7. For mixed/ambiguous labels like "NTN / R20", choose the category that best
   represents the primary work area based on context from the full list.
8. Keep the simplified names concise and human-readable.
9. Every input label MUST appear exactly once in the output mappings.
10. The simplified name should be one that already exists in the input list when
    possible (prefer reusing an existing short label over inventing a new one).

Output: {"mappings": [{"original": "<input label>", "simplified": "<category>"}]}
Return ONLY valid JSON."""


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


def get_timezone_from_location(location_text: str) -> str | None:
    """Use Gemini to determine the IANA timezone from a meeting location string.

    Args:
        location_text: e.g. "Gothenburg, SE, Feb. 9th ~ 13th, 2026"

    Returns:
        IANA timezone string (e.g. "Europe/Stockholm") or None on failure.
    """
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
    room_hints: dict | None = None,
) -> list[str] | None:
    """Use Gemini to determine which room(s) a schedule table belongs to.

    Analyses the paragraph text preceding a table in the DOCX to determine
    which room(s) from the available set this table represents.

    Args:
        context_text: Paragraph text preceding the table in the document.
        available_rooms: List of known room names from the main schedule.
        num_rooms_needed: How many room names to return (matches the number
            of room columns in the table).
        room_hints: Optional role hints for the current meeting, e.g.
            {
                "all_rooms": [...],
                "online_rooms": [...],
                "offline_rooms": [...],
                "main_room": "...",
                "breakout_rooms": [...],
            }

    Returns:
        List of room names from available_rooms, or None if detection fails.
    """
    if not context_text.strip():
        return None

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    # Ensure deterministic room order (preserve caller order)
    available_rooms = _ordered_unique(available_rooms)

    # Merge/derive meeting-specific hints for this run
    merged_hints = _merge_room_hints(available_rooms, room_hints)

    # Check cache
    cache_input = json.dumps(
        {
            "v": ROOM_DETECT_PROMPT_VERSION,
            "context_text": context_text,
            "available_rooms": available_rooms,
            "num_rooms_needed": num_rooms_needed,
            "room_hints": merged_hints,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    cache_key = hashlib.sha256(cache_input.encode()).hexdigest()[:16]
    cached = _load_cache(f"room_{cache_key}")
    if cached is not None:
        names = cached.get("room_names", [])
        if names:
            print(f"  Room detection (cached): {names}")
            return names

    # Fast deterministic pass before LLM (meeting-agnostic + role hints)
    heuristic_names = _heuristic_detect_rooms(
        context_text=context_text,
        available_rooms=available_rooms,
        num_rooms_needed=num_rooms_needed,
        room_hints=merged_hints,
    )
    if heuristic_names and len(heuristic_names) == num_rooms_needed:
        _save_cache(f"room_{cache_key}", {"room_names": heuristic_names})
        print(f"  Room detection (heuristic): {heuristic_names}")
        return heuristic_names

    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=api_key,
        http_options={"timeout": 30_000},
    )

    prompt = _build_room_detect_prompt(
        context_text=context_text,
        available_rooms=available_rooms,
        num_rooms_needed=num_rooms_needed,
        room_hints=merged_hints,
    )

    try:
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
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
            # Partial match — combine with deterministic hints if possible
            combined = _ordered_unique(valid_names + (heuristic_names or []))
            if combined:
                combined = combined[:num_rooms_needed]
                _save_cache(f"room_{cache_key}", {"room_names": combined})
                return combined
    except Exception as e:
        print(f"  Warning: Room detection LLM call failed: {e}")

    if heuristic_names:
        fallback = heuristic_names[:num_rooms_needed]
        _save_cache(f"room_{cache_key}", {"room_names": fallback})
        return fallback

    return None


def _ordered_unique(items: list[str]) -> list[str]:
    """Return unique strings preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it not in seen:
            out.append(it)
            seen.add(it)
    return out


def _normalize_token(text: str) -> str:
    """Normalize a text token for robust room-code matching."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _room_alias_tokens(room_name: str) -> set[str]:
    """Generate likely textual aliases for a room label.

    Example: "F1+F2+F3" -> {"F1+F2+F3", "F1/F2/F3", "F1/2/3", ...}
    """
    aliases: set[str] = {room_name}
    base = room_name.strip()

    plus_parts = [p.strip() for p in base.split("+") if p.strip()]
    if len(plus_parts) > 1:
        aliases.add(" + ".join(plus_parts))
        aliases.add("/".join(plus_parts))

        # Compact form when all parts share prefix (e.g. F1+F2+F3 -> F1/2/3)
        parsed = [re.fullmatch(r"([A-Za-z]+)(\d+)", p) for p in plus_parts]
        if all(parsed):
            prefixes = [m.group(1) for m in parsed if m]
            nums = [m.group(2) for m in parsed if m]
            if len(set(prefixes)) == 1:
                aliases.add(f"{prefixes[0]}{'/'.join(nums)}")

    return aliases


def _merge_room_hints(
    available_rooms: list[str],
    room_hints: dict | None,
) -> dict:
    """Merge caller hints with safe defaults derived from available rooms."""
    hints = dict(room_hints or {})

    all_rooms = _ordered_unique(hints.get("all_rooms", []) + available_rooms)
    online_rooms = _ordered_unique(hints.get("online_rooms", []))
    offline_rooms = _ordered_unique(hints.get("offline_rooms", []))

    if not online_rooms:
        online_rooms = all_rooms

    # Ensure every hinted room exists in available list
    online_rooms = [r for r in online_rooms if r in all_rooms]
    offline_rooms = [r for r in offline_rooms if r in all_rooms]

    main_room = hints.get("main_room")
    if main_room not in all_rooms:
        main_room = online_rooms[0] if online_rooms else (all_rooms[0] if all_rooms else None)

    breakout_rooms = _ordered_unique(hints.get("breakout_rooms", []))
    breakout_rooms = [r for r in breakout_rooms if r in all_rooms and r != main_room]
    if not breakout_rooms and online_rooms:
        breakout_rooms = [r for r in online_rooms if r != main_room]

    return {
        "all_rooms": all_rooms,
        "online_rooms": online_rooms,
        "offline_rooms": offline_rooms,
        "main_room": main_room,
        "breakout_rooms": breakout_rooms,
    }


def _heuristic_detect_rooms(
    context_text: str,
    available_rooms: list[str],
    num_rooms_needed: int,
    room_hints: dict,
) -> list[str] | None:
    """Deterministically detect rooms from context text before LLM fallback."""
    if not available_rooms:
        return None

    text = context_text or ""
    text_lower = text.lower()
    text_norm = _normalize_token(text)

    matches: list[str] = []

    # 1) Explicit room-name/token matching
    for room in available_rooms:
        matched = False
        for alias in _room_alias_tokens(room):
            alias_norm = _normalize_token(alias)
            if len(alias_norm) < 2:
                continue
            if alias_norm in text_norm:
                matched = True
                break
        if matched:
            matches.append(room)

    # 2) Role phrase matching using meeting-specific hints
    breakout_rooms = room_hints.get("breakout_rooms", [])
    main_room = room_hints.get("main_room")
    online_rooms = room_hints.get("online_rooms", [])
    offline_rooms = room_hints.get("offline_rooms", [])

    if not matches:
        brk_nums = [
            int(n)
            for n in re.findall(r"(?:brk|break\s*[-_ ]?out)\s*#?\s*(\d+)", text_lower)
        ]
        for n in brk_nums:
            idx = n - 1
            if 0 <= idx < len(breakout_rooms):
                matches.append(breakout_rooms[idx])

    if not matches:
        if "offline session schedule" in text_lower or re.search(r"\boffline\b", text_lower):
            matches.extend(offline_rooms[:num_rooms_needed])

    if not matches:
        if "online session schedule" in text_lower or re.search(r"\bonline\b", text_lower):
            if num_rooms_needed > 1:
                matches.extend(online_rooms[:num_rooms_needed])
            elif main_room:
                matches.append(main_room)

    if not matches:
        if "main session" in text_lower and main_room:
            matches.append(main_room)

    matches = [m for m in _ordered_unique(matches) if m in available_rooms]

    if not matches:
        return None
    return matches[:num_rooms_needed]


def _build_room_detect_prompt(
    context_text: str,
    available_rooms: list[str],
    num_rooms_needed: int,
    room_hints: dict,
) -> str:
    """Build a meeting-agnostic room-detection prompt with dynamic room hints."""
    main_room = room_hints.get("main_room")
    breakout_rooms = room_hints.get("breakout_rooms", [])
    online_rooms = room_hints.get("online_rooms", [])
    offline_rooms = room_hints.get("offline_rooms", [])

    hint_lines = [
        f"- all_rooms: {available_rooms}",
        f"- main_room: {main_room}",
        f"- breakout_rooms_in_order: {breakout_rooms}",
        f"- online_rooms: {online_rooms}",
        f"- offline_rooms: {offline_rooms}",
    ]

    return f"""Given this title/heading text from a 3GPP RAN1 meeting schedule document, determine which room(s) this schedule table covers.

Title text:
\"\"\"{context_text}\"\"\"

Available rooms in this meeting (EXACT output candidates): {available_rooms}

Meeting-specific room-role hints (derived from this meeting only):
{chr(10).join(hint_lines)}

I need exactly {num_rooms_needed} room name(s) from the available rooms list.

Rules:
- Meeting-independent behavior: do NOT assume any fixed physical room names.
- Match explicit room tokens/codes mentioned in the title to available rooms.
  Use normalized matching (ignore case and separators like spaces, '/', '+', '-', '_').
- If title mentions breakout notation like "Brk#N", "Breakout #N", "RAN1_Brk#N",
  map N to breakout_rooms_in_order[N-1] when available.
- "Main Session" usually maps to main_room.
- "Online Session Schedule" without explicit room codes usually maps to online_rooms
  (or main_room when only one room is requested).
- "Offline Session Schedule" usually maps to offline_rooms.
- If explicit room code appears in text (e.g. inside parentheses), prioritize that explicit match.
- Return room names EXACTLY as they appear in available rooms.
- If you truly cannot determine the room, return the first {num_rooms_needed} available rooms.

Return JSON only with schema: {{"room_names": ["..."], "reasoning": "..."}}"""


# ── Multi-source time-slot parsing ───────────────────────────────


_PROMPT_VERSION = 2  # Bump to invalidate time-slot caches on prompt changes


def build_room_aliases(
    day_rooms: list[RoomInfo],
) -> tuple[dict[str, str], dict[str, str]]:
    """Build stable role-based aliases for rooms.

    Assigns aliases like RAN1_main, RAN1_brk1, RAN1_off1 so that LLM
    prompts are stable across meetings regardless of physical room names.

    Returns:
        (name_to_alias, alias_to_name) mappings.
        alias_to_name also includes multi-room shortcuts:
          ALL_ONLINE  = main + all breakout rooms
          ALL_ROOMS   = every room
    """
    # Group by table_index to separate online vs offline rooms
    tables: dict[int, list[RoomInfo]] = {}
    for r in day_rooms:
        tables.setdefault(r.table_index, []).append(r)
    sorted_tables = sorted(tables.items())

    name_to_alias: dict[str, str] = {}
    alias_to_name: dict[str, str] = {}
    online_aliases: list[str] = []

    if sorted_tables:
        # First table = online rooms (main + breakouts)
        _, online_rooms = sorted_tables[0]
        alias = "RAN1_main"
        name_to_alias[online_rooms[0].name] = alias
        alias_to_name[alias] = online_rooms[0].name
        online_aliases.append(alias)

        for i, r in enumerate(online_rooms[1:], 1):
            alias = f"RAN1_brk{i}"
            name_to_alias[r.name] = alias
            alias_to_name[alias] = r.name
            online_aliases.append(alias)

    # Remaining tables = offline rooms
    off_counter = 1
    for _, rooms in sorted_tables[1:]:
        for r in rooms:
            alias = f"RAN1_off{off_counter}"
            name_to_alias[r.name] = alias
            alias_to_name[alias] = r.name
            off_counter += 1

    # Multi-room shortcuts
    if len(online_aliases) > 1:
        alias_to_name["ALL_ONLINE"] = " + ".join(
            alias_to_name[a] for a in online_aliases
        )
    all_aliases = list(name_to_alias.values())
    if len(all_aliases) > 1:
        alias_to_name["ALL_ROOMS"] = " + ".join(
            alias_to_name[a] for a in all_aliases
        )

    return name_to_alias, alias_to_name


def _alias_room_label(label: str, name_to_alias: dict[str, str]) -> str:
    """Convert a room label to use aliases.

    Handles formats:
      "F1+F2+F3"               → "RAN1_main"
      "F1+F2+F3 + A1 + A3"    → "RAN1_main + RAN1_brk1 + RAN1_brk2"
      "Sorour: F1+F2+F3"      → "Sorour: RAN1_main"
    """
    # Handle "Person: RoomName" prefix
    prefix = ""
    rest = label
    if ": " in label:
        prefix_part, rest = label.split(": ", 1)
        prefix = prefix_part + ": "

    parts = [p.strip() for p in rest.split(" + ")]
    aliased = [name_to_alias.get(p, p) for p in parts]
    return prefix + " + ".join(aliased)


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

## Agenda items

- Vice-chair sources often list MULTIPLE agenda-item numbers for a single session,
  e.g. "9.3.2.3, 9.3, 9.3.1, 9.3.2.1, 9.3.2.2".
- You MUST preserve ALL listed agenda items in the agenda_item field as a
  comma-separated string. Do NOT summarize them to a parent (e.g. do NOT
  collapse "9.3.2.3, 9.3, 9.3.1, 9.3.2.1, 9.3.2.2" into just "9.3").
- Keep the original order from the source data.

## Room aliases

Target rooms use STABLE role-based aliases instead of physical room names.
The prompt header shows a legend like:
    RAN1_main  (= <main online room name>)
    RAN1_brk1  (= <first breakout room name>)
    RAN1_brk2  (= <second breakout room name>)
    RAN1_off1  (= <first offline room name>)
    RAN1_off2  (= <second offline room name>)

Always use the ALIAS in room_name, never the physical room name.
Multi-room shortcuts:
  ALL_ONLINE  = all online rooms (main + breakouts)
  ALL_ROOMS   = every room including offline

## Multi-room sessions (plenaries, ceremonies, sweeps)

When a cell in the main schedule spans multiple rooms (shown as
e.g. [RAN1_main + RAN1_brk1 + RAN1_brk2]), the session runs in ALL
those rooms simultaneously. For such sessions:
- Use room_name = "ALL_ONLINE" if spanning all online rooms.
- Use room_name = "ALL_ROOMS" if spanning ALL rooms (including offline).
- Output the session ONCE with the multi-room alias.
  Do NOT duplicate it into each individual room.
- Common examples: opening/closing plenaries, remembrance gatherings,
  sweep sessions, agenda approval.

## Output format

```json
{
  "sessions": [
    {
      "room_name": "<room alias or ALL_ONLINE / ALL_ROOMS>",
      "name": "session name (include AI number if known)",
      "duration_minutes": N,
      "chair": "person or null",
      "group_header": "category labels joined by ' / ', or empty string",
      "agenda_item": "9.3.2.3, 9.3, 9.3.1, 9.3.2.1, 9.3.2.2 or null (preserve ALL items)"
    }
  ]
}
```

- Use EXACTLY the room ALIAS in room_name (never physical room names).
- For sessions spanning multiple rooms, use ALL_ONLINE or ALL_ROOMS.
- Sessions for all rooms in a single flat array, grouped by room, chronologically ordered.
- Every target room should have at least one entry (if nothing scheduled, omit it).
- group_header is a single string (join multiple labels with " / "), not an array.
- agenda_item: comma-separated list of ALL agenda items from vice-chair detail. Never drop items.
- Return ONLY valid JSON."""


def _build_time_slot_prompt(
    slot,
    name_to_alias: dict[str, str] | None = None,
) -> str:
    """Build prompt for a multi-source time slot.

    Structures data so that each target room's content from all sources
    is grouped together, making cross-referencing easy for the LLM.

    Args:
        slot: a TimeSlotData object from merger.py
        name_to_alias: room-name → alias mapping (if None, uses raw names)
    """
    parts = []
    parts.append(
        f"Day: {slot.day}, Time Block: {slot.time_block_start}-{slot.time_block_end} "
        f"({slot.time_block_duration} min)"
    )

    # TARGET ROOMS with alias legend
    if name_to_alias:
        legend_lines = []
        for r in slot.main_rooms:
            alias = name_to_alias.get(r.name, r.name)
            legend_lines.append(f"  {alias}  (= {r.name})")
        parts.append("\nTARGET ROOMS:\n" + "\n".join(legend_lines))
    else:
        room_names = [r.name for r in slot.main_rooms]
        parts.append(f"\nTARGET ROOMS: {', '.join(room_names)}")

    def _alias_label(label: str) -> str:
        return _alias_room_label(label, name_to_alias) if name_to_alias else label

    # Group: first show main schedule per room, then all vice-chair data
    main_source = slot.sources[0] if slot.sources else None
    vc_sources = slot.sources[1:] if len(slot.sources) > 1 else []

    # Main schedule (authoritative room→content mapping)
    if main_source:
        parts.append("\n## Main Schedule (defines what goes in each room)")
        for entry in main_source.entries:
            parts.append(f'\n[{_alias_label(entry.room_label)}]')
            parts.append(entry.cell_text)

    # Vice-chair detail (adds specificity; room labels unreliable)
    if vc_sources:
        parts.append("\n## Vice-chair detail (match by CONTENT to target rooms, ignore room labels)")
        for source in vc_sources:
            for entry in source.entries:
                parts.append(f'\n[{source.label} — {_alias_label(entry.room_label)}]')
                parts.append(entry.cell_text)

    return "\n".join(parts)


def _time_slot_cache_key(slot) -> str:
    """Generate a cache key for a time slot's combined data."""
    content = json.dumps(
        {
            "v": _PROMPT_VERSION,
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
            day_rooms = day_rooms_map.get(slot.day, [])
            _, alias_to_name = build_room_aliases(day_rooms)
            sessions = _slot_result_to_sessions(cached, slot, day_rooms_map, alias_to_name)
            all_sessions.extend(sessions)
            cache_hits += 1
            continue

        # Build prompt with room aliases
        day_rooms = day_rooms_map.get(slot.day, [])
        name_to_alias, alias_to_name = build_room_aliases(day_rooms)
        user_prompt = _build_time_slot_prompt(slot, name_to_alias)
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
        sessions = _slot_result_to_sessions(parsed_result, slot, day_rooms_map, alias_to_name)
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
    alias_to_name: dict[str, str] | None = None,
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
        # Find grid columns for this room (supports aliases and multi-room)
        col_start, col_end = _find_room_columns(room_name, day_rooms, alias_to_name)

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


def normalize_group_headers(sessions: list[Session]) -> list[Session]:
    """Normalize group_header values across all sessions using LLM.

    Collects all unique group_header strings, asks Gemini to produce a
    simplification mapping, then applies it in-place.

    Args:
        sessions: List of Session objects (modified in-place).

    Returns:
        The same list with group_header values replaced.
    """
    import time as _time
    from google import genai
    from google.genai import types

    unique_headers = sorted(set(s.group_header for s in sessions if s.group_header))

    if len(unique_headers) <= 1:
        print(f"Group normalization: {len(unique_headers)} unique group(s), skipping.")
        return sessions

    print(f"\nNormalizing group headers ({len(unique_headers)} unique)...")

    # Cache key from sorted unique headers
    cache_content = json.dumps(unique_headers, sort_keys=True)
    cache_hash = hashlib.sha256(cache_content.encode()).hexdigest()[:16]
    cache_key = f"group_map_{cache_hash}"

    cached = _load_cache(cache_key)
    mapping: dict[str, str] = {}

    if cached is not None:
        # Rebuild mapping from cached result
        for entry in cached.get("mappings", []):
            mapping[entry["original"]] = entry["simplified"]
        print(f"  Loaded mapping from cache ({len(mapping)} entries)")
    else:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("  Warning: GEMINI_API_KEY not set, skipping normalization")
            return sessions

        client = genai.Client(
            api_key=api_key,
            http_options={"timeout": 120_000},
        )

        user_prompt = (
            "Here are all unique group_header labels from the schedule:\n\n"
            + json.dumps(unique_headers, indent=2, ensure_ascii=False)
            + "\n\nProduce the simplification mapping."
        )

        MAX_RETRIES = 3
        result = None
        for attempt in range(MAX_RETRIES):
            try:
                response = client.models.generate_content(
                    model="gemini-3-flash-preview",
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=GROUP_SIMPLIFY_SYSTEM_INSTRUCTION,
                        temperature=0.0,
                        response_mime_type="application/json",
                        response_json_schema=GROUP_SIMPLIFY_SCHEMA,
                    ),
                )
                result = json.loads(response.text.strip())
                break
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    wait = 5 * (attempt + 1)
                    print(f"  retry({attempt+1}, wait {wait}s)...", flush=True)
                    _time.sleep(wait)
                else:
                    print(f"  Group normalization failed: {e}")
                    return sessions

        if result is None:
            return sessions

        _save_cache(cache_key, result)

        for entry in result.get("mappings", []):
            mapping[entry["original"]] = entry["simplified"]

    # Log the mapping
    changed = {k: v for k, v in mapping.items() if k != v}
    simplified_groups = sorted(set(mapping.values()))
    if changed:
        print(f"  Mapping: {len(unique_headers)} → {len(simplified_groups)} groups")
        for orig, simp in sorted(changed.items()):
            print(f"    '{orig}' → '{simp}'")
    else:
        print("  All groups already normalized.")

    # Apply mapping in-place
    for session in sessions:
        if session.group_header and session.group_header in mapping:
            session.group_header = mapping[session.group_header]

    return sessions


def fill_missing_groups(sessions: list[Session]) -> list[Session]:
    """Fill empty group_header for sessions by name matching and substring matching.

    Two-pass approach:
    1. If a session has no group but another session with the SAME name has a group,
       copy that group.
    2. If still no group, check if the session name contains any existing group name
       as a substring — assign the longest matching group (most specific match).

    Args:
        sessions: List of Session objects (modified in-place).

    Returns:
        The same list with missing group_header values filled where possible.
    """
    # Collect name → group mapping from sessions that have groups
    name_to_group: dict[str, str] = {}
    for s in sessions:
        if s.group_header and s.name not in name_to_group:
            name_to_group[s.name] = s.group_header

    # Pass 1: match by identical session name
    filled_by_name = 0
    for s in sessions:
        if not s.group_header and s.name in name_to_group:
            s.group_header = name_to_group[s.name]
            filled_by_name += 1

    # Collect all known groups (after pass 1)
    known_groups = sorted(
        set(s.group_header for s in sessions if s.group_header),
        key=len,
        reverse=True,  # longest first for most-specific matching
    )

    # Pass 2: match by group name substring in session name
    filled_by_substring = 0
    for s in sessions:
        if not s.group_header:
            name_lower = s.name.lower()
            for group in known_groups:
                if group.lower() in name_lower:
                    s.group_header = group
                    filled_by_substring += 1
                    break

    total_missing = sum(1 for s in sessions if not s.group_header)
    if filled_by_name or filled_by_substring:
        print(
            f"  Fill missing groups: {filled_by_name} by name, "
            f"{filled_by_substring} by substring"
            + (f" ({total_missing} still empty)" if total_missing else "")
        )
    elif total_missing:
        print(f"  Fill missing groups: {total_missing} sessions have no group")

    return sessions


def _find_room_columns(
    room_name: str,
    day_rooms: list[RoomInfo],
    alias_to_name: dict[str, str] | None = None,
) -> tuple[int, int]:
    """Find grid column range for a room name, alias, or multi-room key.

    Handles:
      - Regular room names: "F1+F2+F3"
      - Single aliases: "RAN1_main"
      - Multi-room aliases: "ALL_ONLINE", "ALL_ROOMS"
      - Combined names: "RAN1_main + RAN1_brk1 + RAN1_brk2"

    Returns (col_start, col_end) as 1-indexed grid columns.
    """
    # Resolve multi-room aliases (ALL_ONLINE, ALL_ROOMS)
    if alias_to_name and room_name in alias_to_name:
        resolved = alias_to_name[room_name]
        if " + " in resolved:
            return _find_multi_room_columns(resolved, day_rooms)
        room_name = resolved

    # Handle combined names ("X + Y + Z")
    if " + " in room_name:
        return _find_multi_room_columns(room_name, day_rooms, alias_to_name)

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


def _find_multi_room_columns(
    combined_name: str,
    day_rooms: list[RoomInfo],
    alias_to_name: dict[str, str] | None = None,
) -> tuple[int, int]:
    """Find grid columns spanning multiple rooms.

    Args:
        combined_name: "F1+F2+F3 + A1 + A3" or "RAN1_main + RAN1_brk1"
        day_rooms: room list for the day
        alias_to_name: optional alias resolution dict

    Returns (col_start, col_end) spanning all matched rooms.
    """
    parts = [p.strip() for p in combined_name.split(" + ")]
    all_indices: list[int] = []

    for part in parts:
        # Resolve alias if needed
        if alias_to_name and part in alias_to_name:
            part = alias_to_name[part]
        for idx, ri in enumerate(day_rooms):
            if ri.name == part:
                all_indices.append(idx)
                break

    if all_indices:
        return (min(all_indices) + 2, max(all_indices) + 3)
    return (2, 3)
