"""Generate static HTML page with Gantt-chart style schedule visualization."""

from __future__ import annotations

import json
import re
from pathlib import Path

from models import (
    BREAKS,
    GROUP_COLORS,
    TIME_BLOCKS,
    Schedule,
    time_to_grid_row,
    time_to_minutes,
)

# Default color for sessions without a group header
_DEFAULT_COLOR = {"bg": "#F3F4F6", "border": "#9CA3AF", "text": "#374151"}

# Auto-refresh interval in minutes (0 to disable)
AUTO_REFRESH_MINUTES = 5


def _assign_group_colors(sessions: list) -> dict[str, dict]:
    """Assign colors to unique group_header values from the palette."""
    headers = sorted(set(s.group_header for s in sessions if s.group_header))
    color_map = {}
    for i, header in enumerate(headers):
        color_map[header] = GROUP_COLORS[i % len(GROUP_COLORS)]
    return color_map


def _natural_sort_key(s: str):
    """Sort key that handles numeric parts naturally (8 < 8.3 < 10)."""
    parts = re.split(r'(\d+)', s)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def _build_filter_data(all_sessions: list) -> str:
    """Build filter data JSON for the session filter panel.

    Returns a JSON string with:
    - groups: array of {name, sessions: [{name, key, ais}]}
    - allAIs: sorted array of all unique AI values

    The filter UI shows a three-level tree (Group → Session → AI)
    plus a flat top-level AI section.  AI checkboxes are duplicated
    and their checked state is bidirectionally synced.
    """
    # 1. Build group -> session_name -> {ais: set, hasNoAI: bool}
    group_sessions: dict[str, dict[str, dict]] = {}
    for session in all_sessions:
        group = session.group_header or ""
        name = session.name
        if group not in group_sessions:
            group_sessions[group] = {}
        if name not in group_sessions[group]:
            group_sessions[group][name] = {"ais": set(), "hasNoAI": False}
        if session.agenda_item:
            for ai in session.agenda_item.split(","):
                ai = ai.strip()
                if ai:
                    group_sessions[group][name]["ais"].add(ai)
        else:
            group_sessions[group][name]["hasNoAI"] = True

    # 2. Build structured data
    all_ais: set[str] = set()
    groups = []
    for group_name in sorted(group_sessions.keys(), key=lambda x: (x == "", x.lower())):
        sessions_data = []
        for sess_name in sorted(group_sessions[group_name].keys(), key=_natural_sort_key):
            info = group_sessions[group_name][sess_name]
            ais = sorted(info["ais"], key=_natural_sort_key)
            all_ais.update(ais)
            entry: dict = {
                "name": sess_name,
                "key": f"{sess_name}|{group_name}",
                "ais": ais,
            }
            # Only set hasNoAI when the session has AIs but also has
            # blocks without any AI assigned – this enables the
            # "Not assigned" sub-filter in the UI.
            if info["hasNoAI"] and ais:
                entry["hasNoAI"] = True
            sessions_data.append(entry)
        groups.append({
            "name": group_name if group_name else "Other",
            "key": group_name if group_name else "__other__",
            "sessions": sessions_data,
        })

    result = {
        "groups": groups,
        "allAIs": sorted(all_ais, key=_natural_sort_key),
    }
    return json.dumps(result, ensure_ascii=False)


def _agenda_description_popup_lines(session) -> list[str]:
    """Build compact popup HTML for agenda-item descriptions."""
    items = getattr(session, "agenda_descriptions", None) or []
    if not items and getattr(session, "description", None):
        items = [
            {
                "agenda_item": session.agenda_item or "",
                "matched_agenda_item": session.agenda_item or "",
                "description": session.description,
                "hierarchy": [],
            }
        ]

    entries: list[dict[str, object]] = []
    for item in items:
        agenda_item = str(item.get("agenda_item") or "")
        description = str(item.get("description") or "")
        if not description:
            continue

        entries.append(
            {
                "header": _agenda_description_header(agenda_item, description),
                "hierarchy": _agenda_hierarchy_levels(item.get("hierarchy") or []),
            }
        )

    if len(entries) > 1:
        common_prefix = _common_hierarchy_prefix(
            [entry["hierarchy"] for entry in entries]
        )
        if len(common_prefix) >= 1:
            prefix_lines = [_agenda_hierarchy_line(level) for level in common_prefix]
            path_html = (
                '<div class="popup-path">'
                + "<br>".join(prefix_lines)
                + "</div>"
            )
            leaf_html = "<br>".join(str(entry["header"]) for entry in entries)
            return [
                '<div class="popup-description">'
                + path_html
                + leaf_html
                + "</div>"
            ]

    lines: list[str] = []
    for entry in entries:
        hierarchy = entry["hierarchy"]
        path_html = ""
        prefix = hierarchy[:-1] if len(hierarchy) > 1 else []
        if prefix:
            path_html = (
                '<div class="popup-path">'
                + "<br>".join(_agenda_hierarchy_line(level) for level in prefix)
                + "</div>"
            )
        lines.append(
            f'<div class="popup-description">{path_html}{entry["header"]}</div>'
        )

    return lines


def _agenda_description_header(label: str, description: str) -> str:
    if label:
        return f"<strong>{_esc(label)}:</strong> {_esc(description)}"
    return f"<strong>Description:</strong> {_esc(description)}"


def _agenda_hierarchy_levels(raw_levels: list) -> list[dict[str, str | None]]:
    levels: list[dict[str, str | None]] = []
    for level in raw_levels:
        level_ai = str(level.get("agenda_item") or "")
        if not level_ai:
            continue
        level_desc = level.get("description")
        levels.append(
            {
                "agenda_item": level_ai,
                "description": str(level_desc) if level_desc else None,
            }
        )
    return levels


def _agenda_hierarchy_line(level: dict[str, str | None]) -> str:
    agenda_item = level["agenda_item"] or ""
    description = level.get("description")
    if description:
        return f"{_esc(agenda_item)} - {_esc(description)}"
    return _esc(agenda_item)


def _common_hierarchy_prefix(
    hierarchies: list[list[dict[str, str | None]]],
) -> list[dict[str, str | None]]:
    if not hierarchies or any(not hierarchy for hierarchy in hierarchies):
        return []

    prefix: list[dict[str, str | None]] = []
    for levels in zip(*hierarchies):
        first = levels[0]
        if all(level == first for level in levels[1:]):
            prefix.append(first)
        else:
            break
    return prefix


def _load_template(name: str) -> str:
    """Load a source template stored alongside the generator."""
    return (Path(__file__).with_name("templates") / name).read_text(encoding="utf-8")


def _render_template(name: str, values: dict[str, str]) -> str:
    """Render a template using explicit, dependency-free placeholders."""
    rendered = _load_template(name)
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)

    unresolved = sorted(set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", rendered)))
    if unresolved:
        raise ValueError(
            f"Unresolved placeholders in {name}: {', '.join(unresolved)}"
        )
    return rendered


def _generate_css(num_rooms_max: int) -> str:
    """Load the schedule stylesheet.

    The num_rooms_max argument is retained for compatibility with existing callers.
    """
    del num_rooms_max
    return _load_template("schedule.css")


def _generate_js(timezone: str = "UTC", auto_refresh_minutes: int = AUTO_REFRESH_MINUTES) -> str:
    """Render the schedule client script with runtime configuration."""
    return _render_template(
        "schedule.js",
        {
            "MEETING_TIMEZONE_JSON": json.dumps(timezone, ensure_ascii=False),
            "AUTO_REFRESH_MS": str(auto_refresh_minutes * 60 * 1000),
            "AUTO_REFRESH_MINUTES": str(auto_refresh_minutes),
        },
    )


def generate_html(schedule: Schedule) -> str:
    """Generate the complete HTML page for the schedule."""
    all_sessions = []
    for day in schedule.days:
        all_sessions.extend(day.sessions)

    color_map = _assign_group_colors(all_sessions)
    filter_data_json = _build_filter_data(all_sessions)

    # Join multiple source files if available.
    sources_text = (
        ", ".join(schedule.source_files)
        if getattr(schedule, "source_files", [])
        else schedule.source_file
    )

    # Build only the data-driven portion of the page. The document shell,
    # header, styles, and client script live in templates/.
    html_parts: list[str] = []

    # Legend (group headers)
    if color_map:
        html_parts.append('    <div class="legend">\n')
        for header, colors in sorted(color_map.items()):
            html_parts.append(
                f'        <div class="legend-item">'
                f'<span class="legend-swatch" style="background:{colors["bg"]};border-color:{colors["border"]}"></span>'
                f'{_esc(header)}</div>\n'
            )
        html_parts.append("    </div>\n")

    # Day tabs
    html_parts.append('    <div class="schedule-toolbar">\n')
    html_parts.append('        <div class="tabs">\n')
    for day_schedule in schedule.days:
        day_lower = day_schedule.day_name.lower()
        day_short = day_schedule.day_name[:3]
        html_parts.append(
            f'            <button class="tab" data-day="{day_lower}">{day_short}</button>\n'
        )
    html_parts.append("        </div>\n")
    html_parts.append(
        '        <button class="now-toggle" id="now-toggle" type="button" '
        'aria-pressed="true" title="Hide NOW line">NOW</button>\n'
    )
    html_parts.append("    </div>\n")

    # Day panels
    for day_schedule in schedule.days:
        day_lower = day_schedule.day_name.lower()
        num_rooms = len(day_schedule.rooms)
        if num_rooms == 0:
            continue

        # Grid template columns
        col_template = f"var(--time-col-width) repeat({num_rooms}, 1fr)"

        html_parts.append(
            f'    <div class="day-panel" id="{day_lower}">\n'
            f'        <div class="grid-wrapper">\n'
            f'            <div class="schedule-grid" '
            f'style="grid-template-columns: {col_template}">\n'
        )

        # Room headers
        html_parts.append(
            '                <div class="room-header time-col" '
            'style="grid-column:1;grid-row:1">Time</div>\n'
        )
        for ri, room in enumerate(day_schedule.rooms):
            col = ri + 2
            html_parts.append(
                f'                <div class="room-header" '
                f'style="grid-column:{col};grid-row:1">{_esc(room.name)}</div>\n'
            )

        # Time labels at 30-minute intervals
        time_min = time_to_minutes("08:30")
        end_min = time_to_minutes("19:45")
        while time_min <= end_min:
            row = (time_min - time_to_minutes("08:30")) // 5 + 2
            t_str = f"{time_min // 60:02d}:{time_min % 60:02d}"
            # Only show labels at 30-min intervals
            if time_min % 30 == 0:
                html_parts.append(
                    f'                <div class="time-label" '
                    f'style="grid-row:{row}/{row + 6}">{t_str}</div>\n'
                )
            time_min += 30

        # Grid lines every 30 minutes
        time_min = time_to_minutes("08:30")
        while time_min <= end_min:
            row = (time_min - time_to_minutes("08:30")) // 5 + 2
            major = " major" if time_min % 60 == 0 else ""
            html_parts.append(
                f'                <div class="grid-line{major}" '
                f'style="grid-row:{row}"></div>\n'
            )
            time_min += 30

        # Break bars
        for brk in BREAKS:
            row_start = time_to_grid_row(brk["start"])
            row_end = time_to_grid_row(brk["end"])
            html_parts.append(
                f'                <div class="break-bar" '
                f'style="grid-row:{row_start}/{row_end}">'
                f'{_esc(brk["name"])}</div>\n'
            )

        # Session blocks
        for session in day_schedule.sessions:
            colors = color_map.get(session.group_header, _DEFAULT_COLOR)
            row_start = time_to_grid_row(session.start_time)
            row_end = time_to_grid_row(session.end_time)

            if row_end <= row_start:
                if session.duration_minutes > 0:
                    row_end = row_start + 1
                else:
                    continue  # Skip zero/negative duration

            # Map session room columns to this day's room layout.
            # session.room_col_start/end are global (with col 1=time),
            # but we need to ensure they fit within this day's room count.
            col_start = session.room_col_start
            col_end = session.room_col_end
            # Clamp to valid range
            col_end = min(col_end, num_rooms + 2)
            col_start = max(col_start, 2)
            if col_start >= col_end:
                col_start = 2
                col_end = 3

            style = (
                f"grid-row:{row_start}/{row_end};"
                f"grid-column:{col_start}/{col_end};"
                f"--session-bg:{colors['bg']};"
                f"--session-border:{colors['border']};"
                f"--session-text:{colors['text']}"
            )

            # Content based on block height — order: Name, Chair, Time, AI
            slots = row_end - row_start
            is_short = slots <= 2
            is_tiny = slots <= 1
            display_name = (
                _compact_session_label(session.name, session.agenda_item)
                if is_tiny
                else session.name
            )
            name_html = f'<div class="session-name">{_esc(display_name)}</div>'
            chair_html = ""
            dur_html = ""
            ai_html = ""

            if slots >= 3 and session.chair:
                chair_html = (
                    f'<div class="session-chair">{_esc(session.chair)}</div>'
                )
            if slots >= 4:
                dur_html = (
                    f'<div class="session-duration">'
                    f"{session.start_time}-{session.end_time} "
                    f"({session.duration_minutes}m)</div>"
                )
            if session.agenda_item and slots >= 6:
                ai_html = (
                    f'<div class="session-ai">AI {_esc(session.agenda_item)}</div>'
                )

            # Popup (click-to-show)
            popup_lines = [f"<strong>{_esc(session.name)}</strong>"]
            if session.group_header:
                popup_lines.append(f"Group: {_esc(session.group_header)}")
            if session.chair:
                popup_lines.append(f"Chair: {_esc(session.chair)}")
            if session.agenda_item:
                popup_lines.append(f"AI: {_esc(session.agenda_item)}")
            popup_lines.extend(_agenda_description_popup_lines(session))
            popup_lines.append(
                f"Time: {session.start_time} - {session.end_time} ({session.duration_minutes} min)"
            )
            room_names_in_span = []
            for ri in range(col_start - 2, min(col_end - 2, num_rooms)):
                if ri < len(day_schedule.rooms):
                    room_names_in_span.append(day_schedule.rooms[ri].name)
            if room_names_in_span:
                popup_lines.append(f"Room: {', '.join(room_names_in_span)}")
            popup_html = "<br>".join(popup_lines)

            # Escape popup_html for use in data attribute
            popup_attr = popup_html.replace('&', '&amp;').replace('"', '&quot;').replace("'", '&#39;')

            # Build secondary details wrapped in a clipping container
            details_inner = f"{chair_html}{dur_html}{ai_html}"
            details_html = f'<div class="session-details">{details_inner}</div>' if details_inner else ""
            is_long = _crosses_time_block(session.start_time, session.end_time)
            block_classes = "session-block"
            if is_long:
                block_classes += " long-session"
            if is_short:
                block_classes += " short-session"
            if is_tiny:
                block_classes += " tiny-session"

            # Filter data attributes – only actual AI values
            if session.agenda_item:
                ai_vals = [a.strip() for a in session.agenda_item.split(",") if a.strip()]
                data_ai = "|".join(ai_vals)
            else:
                data_ai = ""
            data_ai_attr = _esc(data_ai).replace('"', '&quot;')
            data_name_attr = _esc(session.name).replace('"', '&quot;')
            data_group_attr = _esc(session.group_header).replace('"', '&quot;')
            data_description_attr = _esc(session.description or "").replace('"', '&quot;')

            html_parts.append(
                f'                <div class="{block_classes}" style="{style}"'
                f' data-popup="{popup_attr}"'
                f' data-ai="{data_ai_attr}"'
                f' data-name="{data_name_attr}"'
                f' data-group="{data_group_attr}"'
                f' data-description="{data_description_attr}"'
                f'>\n'
                f"                    {name_html}{details_html}\n"
                f"                </div>\n"
            )

        html_parts.append(
            "            </div>\n"
            "        </div>\n"
            "    </div>\n"
        )

    return _render_template(
        "schedule.html",
        {
            "MEETING_NAME": _esc(schedule.meeting_name),
            "SOURCES_TEXT": _esc(sources_text),
            "GENERATED_AT": _esc(schedule.generated_at),
            "TIMEZONE": _esc(schedule.timezone),
            "CONTACT_NAME": _esc(schedule.contact_name),
            "CONTACT_EMAIL": _esc(schedule.contact_email),
            "CONTACT_MAILTO": _esc(f"mailto:{schedule.contact_email}"),
            "STYLES": _generate_css(10),
            "SCHEDULE_CONTENT": "".join(html_parts),
            "FILTER_DATA": filter_data_json,
            "SCRIPT": _generate_js(schedule.timezone),
        },
    )


def _crosses_time_block(start_time: str, end_time: str) -> bool:
    """Return True if the session spans across a TIME_BLOCK boundary."""
    s = time_to_minutes(start_time)
    e = time_to_minutes(end_time)
    for tb in TIME_BLOCKS:
        tb_start = time_to_minutes(tb["start"])
        tb_end = time_to_minutes(tb["end"])
        if s >= tb_start and e <= tb_end:
            return False
    return True


def save_html(schedule: Schedule, output_path: str | Path = "docs/index.html"):
    """Generate and save the HTML file."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    html = generate_html(schedule)
    output_path.write_text(html, encoding="utf-8")
    print(f"HTML saved to: {output_path}")
    return output_path


def _esc(text: str) -> str:
    """HTML-escape a string."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _compact_session_label(name: str, agenda_item: str | None) -> str:
    """Return a compact single-line label for very short session blocks."""
    if agenda_item:
        return f"AI {agenda_item}"

    normalized = " ".join(name.split())
    if len(normalized) <= 22:
        return normalized
    return f"{normalized[:21]}…"
