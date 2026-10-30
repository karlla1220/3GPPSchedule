"""Generate static HTML page with Gantt-chart style schedule visualization."""

from __future__ import annotations

from shared.topic_references import render_supplements, render_topic_references

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from shared.schedule import (
    GROUP_COLORS,
    Schedule,
    Timeline,
    time_to_minutes,
)

from shared.page import render_header
from shared.agreement_assets import (
    agenda_section_ids,
    current_agreements,
    render_agreement_panel,
    write_agreement_assets,
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


_AI_NUMBER = re.compile(r'\d+(?:\.\d+)*')


def _ai_ids(value: str) -> list[str]:
    """Agenda numbers an AI as written names: "10.5.4.x" → 10.5.4, "10.6.2/10.6.1.1" → both.

    Text that is not an agenda number stays as written and only matches itself.
    """
    ids = []
    for token in agenda_section_ids(value, ()):
        if token.lower().endswith(".x"):
            token = token[:-2]
        ids.append(token)
    return list(dict.fromkeys(ids))


def _ai_filter_scope(agenda_item: str | None) -> str:
    """A cell's filter scope: "N.*" covers N's sub-items, "N" is N alone.

    An x item covers its sub-items. A bare number does too ("10.5.4"
    schedules all of 10.5.4), unless the same cell lists one of its
    sub-items: "8.1, 8.1.2" then means 8.1's own part next to 8.1.2.
    """
    tokens = agenda_section_ids(agenda_item or "", ())
    ids = [t[:-2] if t.lower().endswith(".x") else t for t in tokens]
    scope = []
    for token, ai_id in zip(tokens, ids):
        covers = _AI_NUMBER.fullmatch(ai_id) and (
            token.lower().endswith(".x")
            or not any(other.startswith(ai_id + ".") for other in ids)
        )
        scope.append(ai_id + ".*" if covers else ai_id)
    return "|".join(dict.fromkeys(scope))


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

    # The agenda numbers a filter entry selects, where they differ from the
    # entry itself (10.5.4.x → 10.5.4). A cell matches when its scope
    # (data-ai-scope) holds the number, an item under it, or covers it.
    # Iterate in sorted order: set order follows the per-process hash seed,
    # and any change in the page makes CI re-render and commit it.
    sorted_ais = sorted(all_ais, key=_natural_sort_key)
    ai_ids = {ai: _ai_ids(ai) for ai in sorted_ais}
    result = {
        "groups": groups,
        "allAIs": sorted_ais,
        "aiIds": {ai: ids for ai, ids in ai_ids.items() if ids != [ai]},
    }
    return json.dumps(result, ensure_ascii=False).replace("<", "\\u003c")


# A tree this short is shown whole: folding would hide almost nothing.
_AGENDA_OPEN_ROWS = 4


def _agenda_description_popup_lines(session) -> list[str]:
    """Build the popup's agenda section: one tree, parents folded when long.

    The session's own items are always shown, and an "x" item ("10.6.x")
    with every item under it. Their parent levels are merged into one tree,
    so a shared parent appears once. Rows are not indented: the number,
    bold labels and the smaller parent lines carry the hierarchy. A tree of
    up to _AGENDA_OPEN_ROWS rows, or with one parent row (the toggle would
    take its place), is always open; any other hides the parents until the
    popup's toggle shows them (schedule.js keeps that choice across popups).
    """
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

    entries: list[tuple[str, list[dict[str, str | None]], str, list[dict]]] = []
    for item in items:
        agenda_item = str(item.get("agenda_item") or "")
        description = str(item.get("description") or "")
        if not description:
            continue
        hierarchy = _agenda_hierarchy_levels(item.get("hierarchy") or [])
        parents = hierarchy[:-1] if len(hierarchy) > 1 else []
        children = [c for c in item.get("children") or [] if c.get("agenda_item") and c.get("description")]
        entries.append((agenda_item, parents, _agenda_description_header(agenda_item, description), children))
    # An item already listed under an "x" item is not repeated on its own.
    covered = {c["agenda_item"] for entry in entries for c in entry[3]}
    entries = [entry for entry in entries if entry[0] not in covered]
    if not entries:
        return []

    # Agenda numbers are hierarchical, so their natural order is tree order:
    # each parent lands right above its own children.
    entries.sort(key=lambda entry: _agenda_sort_key(entry[0]))
    rows: list[str] = []
    # An item that is also another item's parent ("15" above "15.1") is
    # listed once, as the item.
    shown: set[str] = {_agenda_tree_label(entry[0]) for entry in entries}
    parent_rows = 0
    for agenda_item, parents, header, children in entries:
        for level in parents:
            key = level["agenda_item"] or ""
            if key in shown:
                continue
            shown.add(key)
            parent_rows += 1
            rows.append(f'<div class="popup-agenda-parent">{_agenda_hierarchy_line(level)}</div>')
        rows.append(f'<div class="popup-agenda-item">{header}</div>')
        for child in children:
            rows.append(
                '<div class="popup-agenda-item">'
                f'{_agenda_description_header(child["agenda_item"], child["description"])}</div>'
            )

    if parent_rows <= 1 or len(rows) <= _AGENDA_OPEN_ROWS:
        return [f'<div class="popup-description agenda-open">{"".join(rows)}</div>']
    toggle = (
        '<button type="button" class="popup-agenda-toggle" aria-expanded="false">'
        '<span class="when-folded">\u25b8 Show parent items</span>'
        '<span class="when-unfolded">\u25be Hide parent items</span>'
        "</button>"
    )
    return [f'<div class="popup-description">{toggle}{"".join(rows)}</div>']


def _agenda_sort_key(agenda_item: str) -> list[tuple[int, int | str]]:
    """Natural order for numbers like 5.1.10 (after 5.1.9)."""
    return [(0, int(part)) if part.isdigit() else (1, part) for part in agenda_item.split(".")]


def _agenda_tree_label(agenda_item: str) -> str:
    """An "x" item is its parent item: the tree shows 10.4.x as 10.4."""
    return re.sub(r"\.[xX]$", "", agenda_item)


def _agenda_description_header(label: str, description: str) -> str:
    if label:
        return f"<strong>{_esc(_agenda_tree_label(label))}:</strong> {_esc(description)}"
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


def _load_template(name: str) -> str:
    """Load a source template stored alongside the generator."""
    return (Path(__file__).resolve().parent.parent / "templates" / name).read_text(encoding="utf-8")

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
    """Load the shared schedule stylesheet."""
    return _load_template("schedule.css")


def _meeting_interval(starts_at, ends_at):
    """Only offset-aware, ordered instants may enable the browser NOW line."""
    try:
        start, end = (datetime.fromisoformat(value) for value in (starts_at, ends_at))
        if start.utcoffset() is None or end.utcoffset() is None:
            return None, None
        start_ms, end_ms = (int(value.timestamp() * 1000) for value in (start, end))
        return (start_ms, end_ms) if start_ms <= end_ms else (None, None)
    except (TypeError, ValueError, OverflowError):
        return None, None


def _generate_js(timezone: str = "UTC", auto_refresh_minutes: int = AUTO_REFRESH_MINUTES,
                 state_id: str = "schedule", starts_at=None, ends_at=None) -> str:
    """Render browser behavior with meeting-specific identity and instants."""
    start_ms, end_ms = _meeting_interval(starts_at, ends_at)
    return _render_template("schedule.js", {
        "MEETING_TIMEZONE_JSON": json.dumps(timezone),
        "AUTO_REFRESH_MS": str(auto_refresh_minutes * 60 * 1000),
        "AUTO_REFRESH_MINUTES": str(auto_refresh_minutes),
        "STATE_ID_JSON": json.dumps(state_id),
        "MEETING_START_MS": json.dumps(start_ms),
        "MEETING_END_MS": json.dumps(end_ms),
    })


def generate_html(schedule: Schedule, *, schedules=None, groups=None, presentation=None) -> str:
    """Generate the complete HTML page for the schedule."""
    all_sessions = []
    for day in schedule.days:
        all_sessions.extend(day.sessions)

    color_map = _assign_group_colors(all_sessions)
    filter_data_json = _build_filter_data(all_sessions)
    # None outside RAN1: cells there have no agreement panel to control.
    agreement_sections = (
        current_agreements(schedule).get("sections", {}) if schedule.wg_id == "ran1" else None
    )
    page_header = render_header(schedule, presentation=presentation, schedules=schedules, groups=groups)

    # Build HTML
    html_parts = []
    # Legend (group headers)
    if color_map:
        html_parts.append('    <div class="legend">\n')
        for header, colors in sorted(color_map.items()):
            html_parts.append(
                f'        <div class="legend-item">'
                f'<span class="legend-swatch" style="--session-border:{colors["border"]}"></span>'
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
            # Named so the meeting switch slides each day's tab to its place (schedule.css).
            f'            <button class="tab" data-day="{day_lower}" style="view-transition-name:day-tab-{day_lower};view-transition-class:grid-move">{day_short}</button>\n'
        )
    html_parts.append("        </div>\n")
    html_parts.append(
        '        <button class="now-toggle" id="now-toggle" type="button" style="view-transition-name:now-toggle;view-transition-class:grid-move" '
        'aria-pressed="false" disabled title="NOW is available during the meeting">NOW</button>\n'
    )
    html_parts.append("    </div>\n")
    # Picks the day before its grid is parsed, so the page never draws without one.
    html_parts.append("    <script>" + _render_template("day-select.js", {
        "STATE_ID_JSON": json.dumps(schedule.wg_id + ":" + schedule.meeting_id).replace("<", "\\u003c"),
        "MEETING_TIMEZONE_JSON": json.dumps(schedule.timezone).replace("<", "\\u003c"),
    }) + "</script>\n")

    # Day panels
    for day_schedule in schedule.days:
        day_lower = day_schedule.day_name.lower()
        num_rooms = len(day_schedule.rooms)
        if num_rooms == 0:
            continue

        timeline = day_schedule.timeline

        # Grid template columns
        col_template = f"var(--axis-col-width) repeat({num_rooms}, 1fr)"

        html_parts.append(
            f'    <div class="day-panel" id="{day_lower}" data-date="{_esc(day_schedule.date or "")}">\n'
            f'        <div class="grid-wrapper">\n'
            f'            <div class="schedule-grid" '
            f'data-start="{time_to_minutes(timeline.start)}" data-end="{time_to_minutes(timeline.end)}" '
            f'data-slot="{timeline.slot_minutes}" '
            f'style="grid-template-columns: {col_template};--time-rows:{timeline.rows};--slot-scale:{timeline.slot_minutes / 5:g}">\n'
        )

        # Room headers
        html_parts.append(
            '                <div class="room-header time-col" '
            'style="grid-column:1;grid-row:1;view-transition-name:time-head;view-transition-class:grid-move"><span>Time</span></div>\n'
        )
        for ri, room in enumerate(day_schedule.rooms):
            col = ri + 2
            if room.detail:
                # Where the room is, under its name; the whole line stays on hover.
                html_parts.append(
                    f'                <div class="room-header has-place" title="{_esc(_room_text([room]))}" '
                    f'style="grid-column:{col};grid-row:1"><span class="room-name">{_esc(room.name)}</span>'
                    f'<span class="room-place">{_esc(room.detail)}</span></div>\n'
                )
            else:
                html_parts.append(
                    f'                <div class="room-header" title="{_esc(room.name)}" '
                    f'style="grid-column:{col};grid-row:1">{_room_name_html(room.name)}</div>\n'
                )

        # Each day supplies its own range, precision and label spacing.
        base_min = time_to_minutes(timeline.start)
        end_min = time_to_minutes(timeline.end)
        for minute in range(base_min, end_min, timeline.label_minutes):
            row = (minute - base_min) // timeline.slot_minutes + 2
            span = min(timeline.label_minutes // timeline.slot_minutes, timeline.rows + 2 - row)
            label = f"{minute // 60:02d}:{minute % 60:02d}"
            major = " major" if minute % 60 == 0 else ""
            # Named by its time: the meeting switch moves 09:00 to where 09:00 is on the next grid.
            html_parts.append(f'<div class="time-label" style="grid-row:{row}/{row + span};view-transition-name:time-{minute};view-transition-class:grid-move"><span>{label}</span></div>')
            html_parts.append(f'<div class="grid-line{major}" style="grid-row:{row}"></div>')

        for brk in timeline.breaks:
            row_start = max(2, timeline.row(brk["start"]))
            row_end = min(timeline.rows + 2, timeline.row(brk["end"]))
            if row_end > row_start:
                label = _esc(brk["name"])
                if brk.get("label_position") == "time-axis":
                    html_parts.append(f'<div class="break-bar" aria-hidden="true" style="grid-row:{row_start}/{row_end}"></div>')
                    html_parts.append(f'<div class="break-axis" style="grid-row:{row_start}/{row_end}" title="{label}">'
                                      f'{_axis_break_label(brk["name"])}</div>')
                else:
                    html_parts.append(f'<div class="break-bar" style="grid-row:{row_start}/{row_end}">{label}</div>')

        # Session blocks
        for session in day_schedule.sessions:
            colors = color_map.get(session.group_header, _DEFAULT_COLOR)
            row_start = timeline.row(session.start_time)
            row_end = timeline.row(session.end_time)

            if row_end <= row_start:
                if session.duration_minutes > 0:
                    row_end = row_start + 1
                else:
                    continue  # Skip zero/negative duration
            if session.part:
                # Equal heights: the schedule gives the order, not the times.
                row_start, row_end = _part_rows(session.part, row_start, row_end)

            # Map session room columns to this day's room layout.
            # session.room_col_start/end are global (with col 1=time),
            # but we need to ensure they fit within this day's room count.
            col_start = session.room_col_start
            col_end = session.room_col_end
            if session.room_scope not in {"assigned", "shared", "unassigned"}:
                raise ValueError(f"Unknown room scope: {session.room_scope}")
            if session.room_scope in {"shared", "unassigned"}:
                if session.room_ids:
                    raise ValueError("Merged display spans cannot claim physical rooms")
                col_start, col_end = 2, num_rooms + 2
            elif session.room_ids:
                room_columns = {room.id: i + 2 for i, room in enumerate(day_schedule.rooms)}
                columns = sorted(room_columns[room_id] for room_id in session.room_ids)
                if columns != list(range(columns[0], columns[-1] + 1)):
                    raise ValueError("A shared session must span adjacent rooms")
                col_start, col_end = columns[0], columns[-1] + 1
            # Clamp to valid range
            col_end = min(col_end, num_rooms + 2)
            col_start = max(col_start, 2)
            if col_start >= col_end:
                col_start = 2
                col_end = 3

            style = (
                f"grid-row:{row_start}/{row_end};"
                f"grid-column:{col_start}/{col_end};"
                f"--session-border:{colors['border']}"
            )

            # Content based on block height — order: Name, Time, AI, Chair
            slots = (row_end - row_start) * timeline.slot_minutes / 5
            is_short = slots <= 2
            is_tiny = slots <= 1
            name_html = f'<div class="session-name">{_esc(session.name)}</div>'
            scope_label = {"shared": "Common", "unassigned": "Room unassigned"}.get(session.room_scope)
            if scope_label and not is_short:
                name_html += f'<div class="session-scope">{scope_label}</div>'
            chair_html = ""
            dur_html = ""
            ai_html = ""

            # The title is the only primary line; duration, clock range, AI and
            # chair are one quiet secondary tone.
            if slots >= 3 and session.chair:
                chair_html = (
                    f'<div class="session-chair">{_esc(_cell_chair(session.chair))}</div>'
                )
            if slots >= 4:
                # Duration leads: it is what people look for first. The row is
                # one line tall, so a narrow column drops the clock range whole.
                dur_html = (
                    f'<div class="session-duration">'
                    f'<span class="session-minutes">{_part_prefix(session.part)}'
                    f'{session.duration_minutes} <span class="session-unit">min</span></span>'
                    f'<span class="session-range">\u00b7 {session.start_time}\u2013{session.end_time}</span>'
                    f"</div>"
                )
            # Another item an FL announced for this slot before the schedule
            # caught up (RAN1 email threads): "maybe 10.3.1", in the faintest tone.
            maybe = _maybe_items(session)
            maybe_html = (
                f'<span class="session-maybe">maybe {_esc(", ".join(maybe))}</span>' if maybe else ""
            )
            if session.agenda_item and slots >= 6:
                ai_html = (
                    f'<div class="session-ai">AI {_esc(session.agenda_item)}'
                    f'{" · " + maybe_html if maybe_html else ""}</div>'
                )
            elif maybe_html and slots >= 6:
                ai_html = f'<div class="session-ai">{maybe_html}</div>'
            notes = _cell_notes(session)
            # What the email threads say the session will do shares the notes'
            # spare lines: delegates see it without opening the popup.
            cell_lines = notes + [line for line in _email_cell_lines(session) if line not in notes]
            notes_html = ""
            if cell_lines and dur_html:
                notes_html = _notes_html(cell_lines, slots, ai_html, scope_label, chair_html)

            # Popup (click-to-show)
            popup_lines = [f"<strong>{_esc(session.name)}</strong>"]
            if session.group_header:
                popup_lines.append(f"Group: {_esc(session.group_header)}")
            if session.chair:
                popup_lines.append(f"Chair: {_esc(session.chair)}")
            if session.agenda_item:
                popup_lines.append(f"AI: {_esc(session.agenda_item)}")
            popup_lines.append(
                f"Time: {session.start_time} - {session.end_time} "
                f"({_part_text(session.part)}{session.duration_minutes} min)"
            )
            rooms_in_span = [day_schedule.rooms[ri] for ri in range(col_start - 2, min(col_end - 2, num_rooms))
                             if ri < len(day_schedule.rooms)]
            if scope_label:
                popup_lines.append(f"Room: {scope_label} (merged display, not a booking of every room)")
            elif rooms_in_span:
                popup_lines.append(f"Room: {_esc(_room_text(rooms_in_span))}")
            if notes:
                # One separated block; a "Note:" label on every line only adds noise.
                popup_lines.append(
                    '<div class="popup-notes">' + "<br>".join(_esc(n) for n in notes) + "</div>"
                )
            email_html = _email_popup_html(session)
            if email_html:
                popup_lines.append(email_html)
            # Agenda names last: the notes say what this session covers.
            popup_lines.extend(_agenda_description_popup_lines(session))
            popup_html = _join_popup_lines(popup_lines)

            # Escape popup_html for use in data attribute
            popup_attr = popup_html.replace('&', '&amp;').replace('"', '&quot;').replace("'", '&#39;')

            # Build secondary details wrapped in a clipping container
            details_inner = f"{dur_html}{ai_html}{notes_html}"
            details_class = "session-details has-notes" if notes_html else "session-details"
            details_html = f'<div class="{details_class}">{details_inner}</div>' if details_inner else ""
            is_long = _crosses_time_block(session.start_time, session.end_time, timeline)
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
            # A cell is also matched and opened by items it does not write
            # (inferred_agenda_items: its label read by an LLM, or a WG's rules
            # such as RAN4 candidate lists); data-ai stays as written.
            found_ai = ", ".join(session.inferred_agenda_items)
            scope_ai = ", ".join(filter(None, [session.agenda_item, found_ai]))
            # A "maybe" item is filtered like the cell's own: the slot may be its session.
            # Written and found items are scoped apart, so a found sub-item does
            # not narrow a written "8.1" to 8.1's own part.
            filter_ai = "|".join(filter(None, [
                _ai_filter_scope(", ".join(filter(None, [session.agenda_item, *maybe]))),
                _ai_filter_scope(found_ai)]))
            data_ai_scope_attr = _esc("|".join(dict.fromkeys(filter_ai.split("|"))) if filter_ai else "").replace('"', '&quot;')
            # The panel opens the chair-note sections an AI covers (10.6.x →
            # 10.6.1, 10.6.2). The filter keeps matching the AI as written.
            agreement_attrs = ""
            if agreement_sections is not None:
                agreement_ai = "|".join(agenda_section_ids(scope_ai, agreement_sections))
                label = f"{session.name}, AI {session.agenda_item}" if session.agenda_item else session.name
                agreement_attrs = (
                    ' tabindex="0" role="button" aria-controls="agreement-panel" aria-pressed="false"'
                    f' aria-label="{_esc(label).replace(chr(34), "&quot;")}"'
                    f' data-agreement-ai="{_esc(agreement_ai).replace(chr(34), "&quot;")}"'
                )
            data_name_attr = _esc(session.name).replace('"', '&quot;')
            data_group_attr = _esc(session.group_header).replace('"', '&quot;')
            data_description_attr = _esc(session.description or "").replace('"', '&quot;')

            html_parts.append(
                f'                <div class="{block_classes}" style="{style}"'
                f'{agreement_attrs}'
                f' data-popup="{popup_attr}"'
                f' data-room-scope="{session.room_scope}"'
                f' data-ai="{data_ai_attr}"'
                f' data-ai-scope="{data_ai_scope_attr}"'
                f' data-name="{data_name_attr}"'
                f' data-group="{data_group_attr}"'
                f' data-description="{data_description_attr}"'
                f'>\n'
                f"                    {name_html}{details_html}{chair_html}\n"
                f"                </div>\n"
            )

        html_parts.append(
            "            </div>\n"
            "        </div>\n"
            "    </div>\n"
        )

    # The end of the grids, which a page arriving from another WG waits for (switch-arrival.js).
    html_parts.append('    <div id="grid-end" hidden></div>\n')
    html_parts.append(render_agreement_panel(schedule))
    html_parts.append(_render_email_informal(schedule))
    html_parts.append(render_topic_references(schedule.topic_references, schedule.topic_preamble))
    html_parts.append(render_supplements(schedule.supplements))

    return _render_template("schedule.html", {
        "MEETING_NAME": _esc(schedule.meeting_name),
        # Inline, so every page carries its icon without another file to publish.
        "FAVICON": "data:image/svg+xml," + quote(_load_template("favicon.svg").replace("\n", "").replace("\r", "")),
        "HEADER": page_header,
        "STYLES": _generate_css(10),
        "ARRIVAL_SCRIPT": _load_template("switch-arrival.js"),
        "SCHEDULE_CONTENT": "".join(html_parts),
        "FILTER_DATA": filter_data_json,
        "SCRIPT": _generate_js(schedule.timezone,
            state_id=schedule.wg_id + ":" + schedule.meeting_id,
            starts_at=None if schedule.is_demo else schedule.starts_at,
            ends_at=None if schedule.is_demo else schedule.ends_at),
    })


def _maybe_items(session) -> list[str]:
    """Items announced on the email threads for this slot although the schedule gives it to another."""
    return list(dict.fromkeys(
        d["agenda_item"] for d in getattr(session, "email_details", [])
        if d.get("kind") == "tentative" and d.get("agenda_item")
    ))


def _split_text(detail: dict) -> str:
    """The plan's split as the cell writes it: "10.6.1.2 40 min · 10.6.3 30 min", "08:30–08:40 SRS · 08:40–09:10 CSI"."""
    pieces = []
    for part in detail.get("split") or []:
        label = part.get("agenda_item") or part.get("topic") or ""
        when = "–".join(filter(None, [part.get("start"), part.get("end")]))
        size = f"{part['minutes']} min" if part.get("minutes") and not when else ""
        pieces.append(" ".join(filter(None, [when, label, size])))
    return " · ".join(p for p in pieces if p)


# Numbers a delegate looks up in the document, not read at a glance: they
# stay in the popup. "Proposal 3.1.1-4", "FL Proposal 5.3-6A", "proposals
# #4-4A", "Proposal 3-1 (II)", "observation 3.1.2". TDoc numbers are taken
# out of a topic too, since the cell line leads with the mail's own list.
_LOOKUP_NUMBER = re.compile(
    r"\b(?:FL\s+)?(?:proposals?|observations?|obs\.?|prop\.?)\s*#?\s*[\w./-]*\d[\w./-]*(?:\s*\([IVX]+\))?"
    r"(?:\s*(?:,|and|&)\s*#?[\w./-]*\d[\w./-]*(?:\s*\([IVX]+\))?)*"
    r"|#[\w.-]*\d[\w.-]*|\bR1-\d{7}\b",
    re.IGNORECASE,
)
_LOOSE_ENDS = re.compile(r"^(?:\s|,|;|:|-|\b(?:in|of|on|and|for|with)\b)+|(?:\s|,|;|:|-|\b(?:in|of|on|and|for|with)\b)+$",
                         re.IGNORECASE)


def _subject(topic: str) -> str:
    """What a topic is about, without the numbers to look up: "Proposal 4.3.1-1/2: DMRS sequence" -> "DMRS sequence"."""
    head, sep, tail = topic.partition(": ")
    if sep and not _LOOKUP_NUMBER.sub("", head).strip(" ,;:-"):
        topic = tail  # the number names what follows
    return _LOOSE_ENDS.sub("", _LOOKUP_NUMBER.sub("", topic)).strip()


def _email_cell_lines(session) -> list[str]:
    """One short line per announcement for the cell: what the session will treat, at a glance.

    The TDoc numbers first (the document to open for the details), then the
    split if there is one, else the subjects the mail names ("Part 1 (PUSCH
    MIMO)", "Section 2.3", "DMRS sequence"). Proposal numbers are looked up
    in the documents, not read at a glance, so they stay in the popup, and
    so does an announcement with none of these (a summary cut after "Check
    proposals in" says nothing). A line the title
    already holds ("PDSCH" on "10.5.2.2 PDSCH") is left out; an item another
    session holds is named first ("10.3.1: …").
    """
    title = session.name.casefold()
    lines = []
    for detail in getattr(session, "email_details", []):
        subjects = []
        for topic in detail.get("topics") or []:
            subject = _subject(topic)
            if subject and subject.casefold() not in {s.casefold() for s in subjects}:
                subjects.append(subject)
        text = _split_text(detail) or ", ".join(subjects)
        if text.casefold() in title:
            text = ""
        # The document comes first: it is what to open to read the details.
        text = " · ".join(filter(None, [", ".join(detail.get("tdocs") or []), text]))
        if not text:
            continue
        text = text[0].upper() + text[1:]
        if detail.get("kind") == "tentative":
            text = f"{detail['agenda_item']}: {text}"
        if text not in lines:
            lines.append(text)
    return lines


_TDOC = re.compile(r"\bR1-\d{7}\b")


def _nowrap_tdocs(text: str) -> str:
    """Escaped text in which a TDoc number never breaks at its hyphen."""
    return _TDOC.sub(lambda m: f'<span class="popup-email-tdoc">{m.group(0)}</span>', _esc(text))


def _email_detail_html(detail: dict, session) -> str:
    """One announcement in full: the cell's line spelled out, then who said it.

    The popup holds everything the cell line says (the TDoc numbers, the
    split, the subjects) and expands it: the mail's gist, every topic with
    its proposal numbers, the input deadline. Read alone, it needs nothing
    from the cell. No "Online"/"Offline" label: the delegate opened this
    cell, and the cell is the session. The mail's time is given only where
    it is not the cell's own.
    """
    gist = []
    if detail.get("kind") == "tentative":
        gist.append(f"maybe {detail['agenda_item']}")
    start, end = detail.get("start"), detail.get("end")
    if (start or end) and (start or session.start_time, end or session.end_time) != (session.start_time, session.end_time):
        gist.append("–".join(filter(None, [start, end])))
    summary = (detail.get("summary") or "").strip()
    if summary:
        gist.append(summary)
    rows = [f'<div class="popup-email-gist">{_nowrap_tdocs(" · ".join(gist))}</div>'] if gist else []

    # What the gist already says is not said again below it.
    told = summary.casefold()
    facts = [", ".join(f'<span class="popup-email-tdoc">{_esc(t)}</span>'
                       for t in detail.get("tdocs") or [] if t.casefold() not in told)]
    due = detail.get("input_due") or {}
    if due.get("at"):
        if due["at"] not in told:
            facts.append(f"Input by {_esc(due['at'])}")
    elif due.get("minutes_before") is not None and not re.search(r"\bbefore\b", told):  # "until 1 hr before offline"
        minutes = due["minutes_before"]
        facts.append("Input before the session" if not minutes
                     else f"Input {minutes // 60} h before" if minutes % 60 == 0 else f"Input {minutes} min before")
    if any(facts):
        rows.append(f'<div class="popup-email-facts">{" · ".join(f for f in facts if f)}</div>')

    split = _split_text(detail)
    if split:
        rows.append(f'<div class="popup-email-split">{_esc(split)}</div>')
    # The topics in full, proposal numbers included. Ones the split or the
    # TDoc line already names are not repeated.
    said = {p.get(k, "").casefold() for p in detail.get("split") or [] for k in ("agenda_item", "topic") if p.get(k)}
    said |= {t.casefold() for t in detail.get("tdocs") or []}
    topics = []
    for topic in detail.get("topics") or []:
        topic = topic.strip()
        if topic and topic.casefold() not in said and topic.casefold() not in told and topic not in topics:
            topics.append(topic)
    if topics:
        items = "".join(f"<li>{_nowrap_tdocs(t[0].upper() + t[1:])}</li>" for t in topics)
        rows.append(f'<ul class="popup-email-topics">{items}</ul>')

    sent = detail.get("sent") or ""
    author = (detail.get("author") or "").strip()
    who = ", ".join(filter(None, [author if author not in {"<>", ""} else "",
                                  _short_time(sent)]))
    source = _mail_source(who, detail.get("url"))
    if source:
        rows.append(f'<div class="popup-email-from">{source}</div>')
    return "".join(rows)


def _mail_source(who: str, url: str | None) -> str:
    """"— sender ↗": the sender as plain text, an arrow after it to the mail in the list archive.

    Not the sender's name as the link: a linked name reads as an address to
    write to. Only the arrow, not underlined: a word and an underline on every
    row stood out more than the rows themselves.
    """
    parts = [f"— {_esc(who)}"] if who else []
    if url:
        parts.append(f'<a class="mail-link" href="{_esc(url)}" target="_blank" rel="noopener"'
                     ' title="Original mail" aria-label="Original mail">↗</a>')
    return "&nbsp;".join(parts)


def _short_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%a %H:%M")
    except (TypeError, ValueError):
        return ""


def _email_popup_html(session) -> str:
    """What the email threads add to this cell, one block per announcement, oldest first."""
    details = getattr(session, "email_details", [])
    if not details:
        return ""
    lines = "".join(f'<div class="popup-email-item">{_email_detail_html(d, session)}</div>' for d in details)
    return f'<div class="popup-email"><div class="popup-email-head">From email threads</div>{lines}</div>'


_FILLER = {"the", "a", "an", "one", "on", "in", "at", "of", "to", "by", "and"}


def _says(text: str, place: str) -> bool:
    """The summary already names the place ("near cute bears on 3rd floor" for "near the cute bears on the 3rd floor")."""
    words = set(re.findall(r"[a-z0-9]+", text.casefold()))
    return all(w in words for w in re.findall(r"[a-z0-9]+", place.casefold()) if w not in _FILLER)


def _render_email_informal(schedule) -> str:
    """Every off-offline and FL whereabouts from the email threads, in one list below the agreements.

    Not by agenda item: a delegate scans the day's informal meetings at once.
    Each row carries its item and date; the script after it shows the rows of
    the day tab selected above, so a row needs no weekday, and marks the rows
    of the cell last selected.
    """
    rows = []
    for ai, items in (getattr(schedule, "email_informal", None) or {}).items():
        for item in items:
            day = item.get("date")
            if not day:
                continue
            when = "–".join(filter(None, [item.get("start"), item.get("end")]))
            text = item.get("summary") or item.get("quote") or ""
            place = item.get("place") or ""
            if place and not _says(text, place):
                text = f"{text} ({place})" if text else place
            author = (item.get("author") or "").strip()
            rows.append((day, when, ai, text, "" if author in {"", "<>"} else author, item.get("url") or ""))
    # A row shows under the tab of its weekday, so a date outside the meeting
    # week (sent the weekend before) would land on the wrong day.
    first, last = (str(value or "")[:10] for value in (schedule.starts_at, schedule.ends_at))
    if first and last:
        rows = [row for row in rows if first <= row[0] <= last]
    if not rows:
        return ""
    items_html = []
    for day, when, ai, text, author, url in sorted(rows):
        weekday = datetime.fromisoformat(day).strftime("%A").lower()
        items_html.append(
            f'<li data-ai="{_esc(ai)}" data-date="{_esc(day)}" data-day="{weekday}" hidden>'
            + (f'<span class="email-informal-when">{_esc(when)}</span> ' if when else "")
            + f'<span class="email-informal-ai">AI {_esc(ai)}</span> {_esc(text)}'
            + (f' <span class="email-informal-from">{source}</span>' if (source := _mail_source(author, url)) else "")
            + "</li>"
        )
    return (
        # Hidden until the script below picks the day's rows: no flash of every day's.
        '<section class="email-informal" aria-labelledby="email-informal-title" hidden>'
        '<div class="email-informal-header"><h2 id="email-informal-title">Off-offline and FL whereabouts</h2>'
        "<p>From the email threads, not on the schedule</p></div>"
        f'<ul>{"".join(items_html)}</ul></section>\n'
        "<script>(() => {\n"
        "    const box = document.currentScript.previousElementSibling;\n"
        "    const rows = [...box.querySelectorAll('li[data-date]')];\n"
        "    // Only the day of the tab selected above; the tabs are already in the page.\n"
        "    // RAN1 day panels carry no date, so the weekday decides.\n"
        "    const showDay = tab => {\n"
        "        const day = tab ? tab.dataset.day : '';\n"
        "        const panel = day && document.getElementById(day);\n"
        "        const date = panel ? panel.dataset.date : '';\n"
        "        rows.forEach(row => { row.hidden = row.dataset.day !== day || (!!date && row.dataset.date !== date); });\n"
        "        box.hidden = rows.every(row => row.hidden);\n"
        "    };\n"
        "    document.querySelectorAll('.tab[data-day]').forEach(tab => tab.addEventListener('click', () => showDay(tab)));\n"
        "    showDay(document.querySelector('.tab.active'));\n"
        "    // The rows of the selected cell's items, in its own accent. Capture:\n"
        "    // the cell's own handler stops the click from bubbling.\n"
        "    document.addEventListener('click', event => {\n"
        "        const block = event.target instanceof Element && event.target.closest('.session-block');\n"
        "        if (!block) return;\n"
        "        const chosen = (block.dataset.aiScope || '').split('|').map(id => id.replace(/\\.\\*$/, '')).filter(Boolean);\n"
        "        const accent = getComputedStyle(block).getPropertyValue('--accent').trim();\n"
        "        if (accent) box.style.setProperty('--selected-accent', accent);\n"
        "        rows.forEach(row => {\n"
        "            const ai = row.dataset.ai;\n"
        "            row.classList.toggle('related', chosen.some(id => ai === id || ai.startsWith(id + '.') || id.startsWith(ai + '.')));\n"
        "        });\n"
        "    }, true);\n"
        "})();</script>\n"
    )


def _join_popup_lines(lines: list[str]) -> str:
    """Break between text lines only; a block section already starts a line."""
    html = ""
    for line in lines:
        if html and not html.endswith("</div>") and not line.startswith("<div"):
            html += "<br>"
        html += line
    return html


# Cell height budget in px; see the comment above .session-block in schedule.css.
_CELL_CHROME = 12          # margin 4 + border 2 + padding 6
_TITLE_LINE = 16.2         # 13.5px x 1.2
_SCOPE_LINE = 13.5         # "Common" under the title, 10px at normal line height
_DETAILS_GAP = 1
_SECONDARY_LINE = 14.175   # 10.5px x 1.35
_CHAIR_ROW = _SECONDARY_LINE + 1  # pinned 3px above the border, 1px into the content box
_NOTES_RULE = 7            # 3px gap, the short rule, 4px gap above the first note


_AI_ONLY_NOTE = re.compile(r'(?:\bAI(?![A-Za-z])|\band\b|\d+(?:\.\d+)*(?:\.x)?|[\s,;/\[\]()–-])*', re.I)


def _repeats_ai_line(note: str, ai_ids: set[str]) -> bool:
    """A note of nothing but agenda numbers the AI line already shows ("AI32, AI6")."""
    if not _AI_ONLY_NOTE.fullmatch(note) or not re.search(r'\d', note):
        return False
    text = re.sub(r'\bAI(?![A-Za-z])|\band\b|[\[\]()]', ' ', note, flags=re.I)
    fields = [f.strip() for f in re.split(r'[,;]', text) if f.strip()]
    ids = {i for f in fields for i in agenda_section_ids(re.sub(r'\s+', ' ', f), ())}
    return bool(ids) and ids <= ai_ids


def _cell_notes(session) -> list[str]:
    """Notes worth showing: a line that only repeats the title, or only lists
    agenda numbers the AI line shows, adds nothing. Head lines stay even when
    the title is made from them: the title may have lost words in the cleaning."""
    name = session.name.strip().casefold()
    ai_ids = set(agenda_section_ids(session.agenda_item or "", ()))
    return [n for n in session.notes
            if n.strip() and n.strip().casefold() != name and not _repeats_ai_line(n, ai_ids)]


def _notes_html(notes: list[str], slots: float, ai_html: str, scope_label: str | None,
                chair_html: str) -> str:
    """Notes fill whole secondary lines left under the title, time and AI lines.

    The container is clipped to a whole number of lines so no note is cut
    through the middle. The chair's row is kept clear while the chair shows;
    columns too narrow for the chair get that row back (see schedule.css).
    """
    used = _TITLE_LINE + _DETAILS_GAP + _SECONDARY_LINE + _NOTES_RULE
    if scope_label:
        used += _SCOPE_LINE
    if ai_html:
        used += _SECONDARY_LINE
    free = slots * 10 - _CELL_CHROME - used
    lines_full = max(0, int(free // _SECONDARY_LINE))
    lines = max(0, int((free - _CHAIR_ROW) // _SECONDARY_LINE)) if chair_html else lines_full
    if not lines_full:
        return ""
    items = "".join(f'<div class="session-note">{_esc(n)}</div>' for n in notes)
    # Without a line beside the chair, only columns that hide the chair show notes.
    classes = "session-notes" if lines else "session-notes beside-chair"
    return (
        f'<div class="{classes}" style="--note-lines:{lines};--note-lines-full:{lines_full}">'
        f"{items}</div>"
    )


def _part_rows(part, row_start: int, row_end: int) -> tuple[int, int]:
    """Grid rows of piece k of n: equal shares of the block, whole rows."""
    k, n = part
    rows = row_end - row_start
    return row_start + rows * (k - 1) // n, row_start + rows * k // n


def _part_prefix(part) -> str:
    """"1/2 of " before the block minutes; "of" is sized like the unit."""
    return f'{part[0]}/{part[1]} <span class="session-unit">of</span> ' if part else ""


def _cell_chair(chair: str) -> str:
    """The name the cell shows: the given name of a "Given FAMILY" name.

    The cell's chair is the quietest line and is cut short in narrow columns, so
    it carries the name people call the chair by ("Lixiang XU" -> "Lixiang");
    the popup keeps the full name. A name without a capitalised family part is
    shown as written ("Hiroki", "Ruixin Wang"). Several chairs keep their
    separator ("Yang TANG / Shan YANG" -> "Yang / Shan", shared/officials.py).
    """
    def given(name: str) -> str:
        words = name.split()
        for i, word in enumerate(words[1:], 1):
            letters = [c for c in word if c.isalpha()]
            if len(letters) > 1 and all(c.isupper() for c in letters):
                return ' '.join(words[:i])
        return name

    return ' / '.join(given(part) for part in chair.split(' / '))


def _room_name_html(name: str) -> str:
    """A room name that may take two lines, breaking before its last bracket.

    "RAN4 Breakout 2 (BR2)(Gene Session)" is "RAN4 Breakout 2 (BR2)" over
    "(Gene Session)": the bracket names whose session or hours it is, so it
    moves down whole. Words with a hyphen or slash ("A-IOT", "3/4/5/6") do
    not break inside; the browser breaks only at spaces and before the bracket.
    """
    def word(text: str) -> str:
        return f'<span class="room-word">{_esc(text)}</span>' if re.search(r'[-/–]', text) else _esc(text)

    match = re.fullmatch(r'(.*?\S)(\s*)(\([^()]*\))', name.strip())
    head, gap, tail = (match[1], match[2], match[3]) if match else (name.strip(), '', '')
    html = ' '.join(word(w) for w in head.split())
    if tail:
        html += (' ' if gap else '<wbr>') + f'<span class="room-word">{_esc(tail)}</span>'
    return f'<span class="room-name-text">{html}</span>'


def _room_text(rooms):
    """The popup Room line: "Breakout 1 · B3 (Floor 2) · Stream 2".

    A place may hold a comma ("D3/D4, Floor 3"), so rooms with one are
    separated by semicolons.
    """
    shown = [f"{room.name} · {room.detail}" if room.detail else room.name for room in rooms]
    return ("; " if any(room.detail for room in rooms) else ", ").join(shown)


def _part_text(part) -> str:
    return f"{part[0]}/{part[1]} of " if part else ""


def _crosses_time_block(start_time: str, end_time: str, timeline: Timeline) -> bool:
    """Return True if the session spans across a TIME_BLOCK boundary."""
    s = time_to_minutes(start_time)
    e = time_to_minutes(end_time)
    if not timeline.blocks:
        return False
    for tb in timeline.blocks:
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
    write_agreement_assets(schedule, output_path.parent)
    output_path.write_text(html, encoding="utf-8")
    print(f"HTML saved to: {output_path}")
    return output_path


def _axis_break_label(name: str) -> str:
    """One word fits the time axis: "MORNING COFFEE BREAK" -> "Coffee"."""
    match = re.search(r"\b(coffee|lunch)\b", name, re.I)
    return match[1].capitalize() if match else "Break"


def _esc(text: str) -> str:
    """HTML-escape a string."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
