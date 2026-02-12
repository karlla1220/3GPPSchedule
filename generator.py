"""Generate static HTML page with Gantt-chart style schedule visualization."""

from __future__ import annotations

from pathlib import Path

from models import (
    BREAKS,
    GROUP_COLORS,
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


def _generate_css(num_rooms_max: int) -> str:
    """Generate the CSS for the schedule page."""
    return """
:root {
    --slot-height: 7px;
    --time-col-width: 54px;
    --header-height: 36px;
    --break-bg: #F3F4F6;
    --grid-line: #E5E7EB;
    --bg: #FFFFFF;
    --text: #1F2937;
    --text-muted: #6B7280;
    --border: #D1D5DB;
    --shadow: 0 1px 3px rgba(0,0,0,0.1);
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #F9FAFB;
    color: var(--text);
    line-height: 1.4;
}

.container {
    max-width: 1400px;
    margin: 0 auto;
    padding: 16px;
}

header {
    text-align: center;
    margin-bottom: 16px;
}

header h1 {
    font-size: 22px;
    font-weight: 700;
    color: var(--text);
    margin-bottom: 4px;
}

header .meta {
    font-size: 12px;
    color: var(--text-muted);
}

/* Tabs */
.tabs {
    display: flex;
    gap: 4px;
    margin-bottom: 12px;
    border-bottom: 2px solid var(--grid-line);
    padding-bottom: 0;
    flex-wrap: wrap;
}

.tab {
    padding: 8px 20px;
    border: none;
    background: transparent;
    color: var(--text-muted);
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    border-bottom: 2px solid transparent;
    margin-bottom: -2px;
    transition: all 0.15s;
    border-radius: 6px 6px 0 0;
}

.tab:hover {
    background: #F3F4F6;
    color: var(--text);
}

.tab.active {
    color: #2563EB;
    border-bottom-color: #2563EB;
    font-weight: 600;
}

/* Day panels */
.day-panel {
    display: none;
}

.day-panel.active {
    display: block;
}

/* Legend */
.legend {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 12px;
    padding: 8px 12px;
    background: white;
    border-radius: 8px;
    box-shadow: var(--shadow);
    font-size: 11px;
}

.legend-item {
    display: flex;
    align-items: center;
    gap: 4px;
}

.legend-swatch {
    width: 12px;
    height: 12px;
    border-radius: 3px;
    border: 1px solid;
    flex-shrink: 0;
}

/* Schedule Grid */
.grid-wrapper {
    background: white;
    border-radius: 8px;
    box-shadow: var(--shadow);
    overflow-x: auto;
    padding: 0;
}

.schedule-grid {
    display: grid;
    grid-template-rows: var(--header-height) repeat(135, var(--slot-height));
    gap: 0;
    min-width: 600px;
    position: relative;
}

/* Room headers */
.room-header {
    grid-row: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
    font-weight: 600;
    color: var(--text);
    background: #F9FAFB;
    border-bottom: 2px solid var(--border);
    padding: 4px 6px;
    text-align: center;
    position: sticky;
    top: 0;
    z-index: 20;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.room-header.time-col {
    background: #F3F4F6;
    font-weight: 700;
    font-size: 10px;
    color: var(--text-muted);
}

/* Time labels */
.time-label {
    grid-column: 1;
    display: flex;
    align-items: flex-start;
    justify-content: center;
    font-size: 9px;
    font-weight: 600;
    color: var(--text-muted);
    padding-top: 1px;
    border-right: 1px solid var(--grid-line);
    background: #FAFAFA;
    z-index: 5;
}

/* 30-minute grid lines */
.grid-line {
    grid-column: 1 / -1;
    border-top: 1px solid var(--grid-line);
    pointer-events: none;
    z-index: 1;
}

.grid-line.major {
    border-top: 1px solid #D1D5DB;
}

/* Break bars */
.break-bar {
    grid-column: 1 / -1;
    background: var(--break-bg);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
    font-weight: 500;
    color: var(--text-muted);
    z-index: 3;
    border-top: 1px dashed #D1D5DB;
    border-bottom: 1px dashed #D1D5DB;
    position: relative;
}

.break-bar::before {
    content: '';
    position: absolute;
    inset: 0;
    background: repeating-linear-gradient(
        -45deg,
        transparent,
        transparent 4px,
        rgba(0,0,0,0.03) 4px,
        rgba(0,0,0,0.03) 8px
    );
}

/* Session blocks */
.session-block {
    border-radius: 4px;
    border-left: 3px solid var(--session-border);
    background: var(--session-bg);
    padding: 2px 5px;
    margin: 1px 2px;
    overflow: hidden;
    cursor: pointer;
    z-index: 10;
    position: relative;
    transition: box-shadow 0.15s;
    display: flex;
    flex-direction: column;
    justify-content: flex-start;
    min-height: 0;
}

.session-block:hover {
    box-shadow: 0 2px 8px rgba(0,0,0,0.18);
    z-index: 15;
}

.session-name {
    font-weight: 600;
    font-size: 11px;
    line-height: 1.2;
    color: var(--session-text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex-shrink: 0;
    position: relative;
    z-index: 2;
    background: var(--session-bg);
}

.session-details {
    flex: 1 1 0;
    min-height: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
}

.session-chair {
    font-size: 9px;
    color: var(--session-text);
    opacity: 0.7;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex-shrink: 0;
}

.session-duration {
    font-size: 9px;
    color: var(--session-text);
    opacity: 0.6;
    flex-shrink: 0;
}

.session-ai {
    font-size: 8px;
    color: var(--session-text);
    opacity: 0.55;
    white-space: normal;
    word-break: break-word;
    overflow: hidden;
    text-overflow: ellipsis;
    line-height: 1.2;
    flex: 1 1 0;
    min-height: 0;
}

/* Click-to-show popup */
.popup-backdrop {
    display: none;
    position: fixed;
    inset: 0;
    z-index: 90;
}

.popup-backdrop.active {
    display: block;
}

.popup-floating {
    display: none;
    position: fixed;
    background: #1F2937;
    color: white;
    padding: 12px 16px;
    border-radius: 8px;
    font-size: 12px;
    white-space: nowrap;
    z-index: 200;
    box-shadow: 0 4px 16px rgba(0,0,0,0.35);
    pointer-events: auto;
    line-height: 1.7;
    min-width: 180px;
    max-width: 400px;
}

.popup-floating.show {
    display: block;
}

.popup-floating .popup-close {
    position: absolute;
    top: 4px;
    right: 8px;
    cursor: pointer;
    font-size: 14px;
    color: #9CA3AF;
    background: none;
    border: none;
    line-height: 1;
}

.popup-floating .popup-close:hover {
    color: white;
}

/* Current time indicator */
.now-line {
    grid-column: 1 / -1;
    height: 2px;
    background: #EF4444;
    z-index: 25;
    position: relative;
    pointer-events: none;
}

.now-line::before {
    content: 'NOW';
    position: absolute;
    left: 4px;
    top: -8px;
    font-size: 9px;
    font-weight: 700;
    color: #EF4444;
    background: white;
    padding: 0 3px;
    border-radius: 2px;
}

/* Responsive */
@media (max-width: 768px) {
    .container { padding: 8px; }
    header h1 { font-size: 18px; }
    .tab { padding: 6px 14px; font-size: 13px; }
    :root { --slot-height: 6px; --time-col-width: 44px; }
    .session-name { font-size: 10px; }
    .room-header { font-size: 9px; padding: 3px 3px; }
}
"""


def _generate_js(timezone: str = "UTC", auto_refresh_minutes: int = AUTO_REFRESH_MINUTES) -> str:
    """Generate the JavaScript for tab switching, today selection, now-line, and auto-refresh."""
    auto_refresh_ms = auto_refresh_minutes * 60 * 1000
    return f"""
document.addEventListener('DOMContentLoaded', function() {{
    const MEETING_TZ = '{timezone}';
    const AUTO_REFRESH_MS = {auto_refresh_ms}; // {auto_refresh_minutes} minutes
    const STATE_KEY = '3gpp_schedule_state';

    // --- User state persistence (sessionStorage) ---
    function saveUserState() {{
        const activeTab = document.querySelector('.tab.active');
        const state = {{
            activeDay: activeTab ? activeTab.dataset.day : null,
            scrollX: window.scrollX,
            scrollY: window.scrollY
        }};
        try {{
            sessionStorage.setItem(STATE_KEY, JSON.stringify(state));
        }} catch (e) {{
            // sessionStorage may be unavailable; silently ignore
        }}
    }}

    function loadUserState() {{
        try {{
            const raw = sessionStorage.getItem(STATE_KEY);
            return raw ? JSON.parse(raw) : null;
        }} catch (e) {{
            return null;
        }}
    }}

    // Helper: get current Date components in the meeting timezone
    function nowInMeetingTZ() {{
        const now = new Date();
        const fmt = new Intl.DateTimeFormat('en-US', {{
            timeZone: MEETING_TZ,
            hour: 'numeric', minute: 'numeric',
            weekday: 'long',
            hour12: false
        }});
        const parts = fmt.formatToParts(now);
        let hour = 0, minute = 0, weekday = '';
        for (const p of parts) {{
            if (p.type === 'hour') hour = parseInt(p.value, 10);
            if (p.type === 'minute') minute = parseInt(p.value, 10);
            if (p.type === 'weekday') weekday = p.value.toLowerCase();
        }}
        return {{ hour, minute, weekday, minutes: hour * 60 + minute }};
    }}

    // Update the "Updated" display in meeting timezone
    function updateTimeDisplay() {{
        const el = document.getElementById('tz-now');
        if (!el) return;
        const now = new Date();
        const formatted = now.toLocaleString('en-US', {{
            timeZone: MEETING_TZ,
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit', hour12: false
        }});
        el.textContent = formatted;
    }}
    updateTimeDisplay();
    setInterval(updateTimeDisplay, 60000);

    // Tab switching
    const tabs = document.querySelectorAll('.tab');
    const panels = document.querySelectorAll('.day-panel');

    tabs.forEach(tab => {{
        tab.addEventListener('click', function() {{
            tabs.forEach(t => t.classList.remove('active'));
            panels.forEach(p => p.classList.remove('active'));
            this.classList.add('active');
            const day = this.dataset.day;
            const panel = document.getElementById(day);
            if (panel) panel.classList.add('active');
        }});
    }});

    // Restore saved state or auto-select today's tab
    const saved = loadUserState();
    if (saved && saved.activeDay) {{
        const savedTab = document.querySelector('[data-day="' + saved.activeDay + '"]');
        if (savedTab) {{
            savedTab.click();
            window.scrollTo(saved.scrollX || 0, saved.scrollY || 0);
        }} else {{
            const firstTab = document.querySelector('.tab');
            if (firstTab) firstTab.click();
        }}
    }} else {{
        const {{ weekday: today }} = nowInMeetingTZ();
        const todayTab = document.querySelector(`[data-day="${{today}}"]`);
        if (todayTab) {{
            todayTab.click();
        }} else {{
            const firstTab = document.querySelector('.tab');
            if (firstTab) firstTab.click();
        }}
    }}

    // Now-line: update position every minute (in meeting timezone)
    function updateNowLine() {{
        const {{ minutes }} = nowInMeetingTZ();
        const base = 8 * 60 + 30; // 08:30
        const end = 19 * 60 + 45; // 19:45

        document.querySelectorAll('.now-line').forEach(el => el.remove());

        if (minutes >= base && minutes <= end) {{
            const slot = Math.floor((minutes - base) / 5);
            const row = slot + 2;
            document.querySelectorAll('.schedule-grid').forEach(grid => {{
                const nowLine = document.createElement('div');
                nowLine.className = 'now-line';
                nowLine.style.gridRow = row + ' / ' + (row + 1);
                grid.appendChild(nowLine);
            }});
        }}
    }}

    updateNowLine();
    setInterval(updateNowLine, 60000);

    // Click-to-show popup on session blocks (shared floating popup)
    const backdrop = document.getElementById('popup-backdrop');
    const popupEl = document.getElementById('popup-floating');
    const popupContent = document.getElementById('popup-content');
    const popupCloseBtn = document.getElementById('popup-close-btn');

    function closePopup() {{
        popupEl.classList.remove('show');
        backdrop.classList.remove('active');
    }}

    document.querySelectorAll('.session-block').forEach(block => {{
        block.addEventListener('click', function(e) {{
            e.stopPropagation();
            const html = this.getAttribute('data-popup');
            if (!html) return;
            const wasOpen = popupEl.classList.contains('show');
            closePopup();
            if (!wasOpen || popupContent.innerHTML !== html) {{
                popupContent.innerHTML = html;
                const blockRect = this.getBoundingClientRect();
                popupEl.classList.add('show');
                backdrop.classList.add('active');
                // Initially place to the right of the block
                let left = blockRect.right + 4;
                let top = blockRect.top;
                // Measure popup after rendering
                const pRect = popupEl.getBoundingClientRect();
                // Flip left if off-screen right
                if (left + pRect.width > window.innerWidth - 8) {{
                    left = blockRect.left - pRect.width - 4;
                }}
                if (left < 8) left = 8;
                // Flip up if off-screen bottom
                if (top + pRect.height > window.innerHeight - 8) {{
                    top = window.innerHeight - pRect.height - 8;
                }}
                if (top < 8) top = 8;
                popupEl.style.left = left + 'px';
                popupEl.style.top = top + 'px';
            }}
        }});
    }});

    backdrop.addEventListener('click', closePopup);
    popupCloseBtn.addEventListener('click', function(e) {{
        e.stopPropagation();
        closePopup();
    }});

    // --- Auto-refresh: reload page periodically, preserving user state ---
    if (AUTO_REFRESH_MS > 0) {{
        setInterval(function() {{
            saveUserState();
            location.reload();
        }}, AUTO_REFRESH_MS);
    }}
}});
"""


def generate_html(schedule: Schedule) -> str:
    """Generate the complete HTML page for the schedule."""
    all_sessions = []
    for day in schedule.days:
        all_sessions.extend(day.sessions)

    color_map = _assign_group_colors(all_sessions)
    mailto_link = _esc(f"mailto:{schedule.contact_email}")

    # Build HTML
    html_parts = []
    html_parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{_esc(schedule.meeting_name)} - Schedule</title>
    <style>{_generate_css(10)}</style>
</head>
<body>
<div class="container">
    <header>
        <h1>📅 {_esc(schedule.meeting_name)}</h1>
        <p class="meta">Source: {_esc(schedule.source_file)} &nbsp;|&nbsp; Generated: {_esc(schedule.generated_at)} ({_esc(schedule.timezone)}) &nbsp;|&nbsp; Now: <span id="tz-now">...</span> ({_esc(schedule.timezone)})</p>
        <p class="meta">Contact: {_esc(schedule.contact_name)} (<a href="{mailto_link}">{_esc(schedule.contact_email)}</a>) for reports or feature requests.</p>
    </header>
""")

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
    html_parts.append('    <div class="tabs">\n')
    for day_schedule in schedule.days:
        day_lower = day_schedule.day_name.lower()
        day_short = day_schedule.day_name[:3]
        html_parts.append(
            f'        <button class="tab" data-day="{day_lower}">{day_short}</button>\n'
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
            name_html = f'<div class="session-name">{_esc(session.name)}</div>'
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

            html_parts.append(
                f'                <div class="session-block" style="{style}" data-popup="{popup_attr}">\n'
                f"                    {name_html}{details_html}\n"
                f"                </div>\n"
            )

        html_parts.append(
            "            </div>\n"
            "        </div>\n"
            "    </div>\n"
        )

    # Shared floating popup and backdrop
    html_parts.append('    <div class="popup-backdrop" id="popup-backdrop"></div>\n')
    html_parts.append('    <div class="popup-floating" id="popup-floating"><div id="popup-content"></div><button class="popup-close" id="popup-close-btn">&times;</button></div>\n')

    # Close container and add JS
    html_parts.append(f"""</div>
<script>{_generate_js(schedule.timezone)}</script>
</body>
</html>""")

    return "".join(html_parts)


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
