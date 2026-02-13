"""Generate static HTML page with Gantt-chart style schedule visualization."""

from __future__ import annotations

import json
import re
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
    # 1. Build group -> session_name -> set[AI]
    group_sessions: dict[str, dict[str, set[str]]] = {}
    for session in all_sessions:
        group = session.group_header or ""
        name = session.name
        if group not in group_sessions:
            group_sessions[group] = {}
        if name not in group_sessions[group]:
            group_sessions[group][name] = set()
        if session.agenda_item:
            for ai in session.agenda_item.split(","):
                ai = ai.strip()
                if ai:
                    group_sessions[group][name].add(ai)

    # 2. Build structured data
    all_ais: set[str] = set()
    groups = []
    for group_name in sorted(group_sessions.keys(), key=lambda x: (x == "", x.lower())):
        sessions_data = []
        for sess_name in sorted(group_sessions[group_name].keys(), key=_natural_sort_key):
            ais = sorted(group_sessions[group_name][sess_name], key=_natural_sort_key)
            all_ais.update(ais)
            sessions_data.append({
                "name": sess_name,
                "key": f"{sess_name}|{group_name}",
                "ais": ais,
            })
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


def _generate_css(num_rooms_max: int) -> str:
    """Generate the CSS for the schedule page."""
    return """
:root {
    --slot-height: 8px;
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
    padding: 1px 4px;
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

.session-block.short-session {
    min-height: 16px;
    border-left-width: 2px;
    padding: 1px 3px;
}

.session-block.tiny-session {
    min-height: 14px;
}

.session-block.short-session .session-details {
    display: none;
}

.session-block.short-session .session-name {
    font-size: 10px;
    line-height: 1.1;
}

.session-block.tiny-session .session-name {
    font-size: 9px;
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
    :root { --slot-height: 7px; --time-col-width: 44px; }
    .session-name { font-size: 10px; }
    .room-header { font-size: 9px; padding: 3px 3px; }
}

/* ── Session Filter Panel ── */
.filter-panel {
    position: fixed;
    right: 0;
    top: 50%;
    transform: translateY(-50%);
    z-index: 100;
    width: 260px;
    max-height: 80vh;
    background: white;
    border-radius: 8px 0 0 8px;
    box-shadow: -2px 0 12px rgba(0,0,0,0.15);
    transition: transform 0.3s ease;
    display: flex;
    flex-direction: column;
}

.filter-panel.collapsed {
    transform: translateX(calc(100%)) translateY(-50%);
}

.filter-toggle {
    position: absolute;
    left: -32px;
    top: 50%;
    transform: translateY(-50%);
    width: 32px;
    height: 80px;
    background: white;
    border: 1px solid #D1D5DB;
    border-right: none;
    border-radius: 8px 0 0 8px;
    cursor: pointer;
    font-size: 11px;
    color: #6B7280;
    display: flex;
    align-items: center;
    justify-content: center;
    writing-mode: vertical-rl;
    box-shadow: -2px 0 6px rgba(0,0,0,0.1);
    z-index: 101;
}

.filter-toggle:hover {
    background: #F3F4F6;
    color: #374151;
}

.filter-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 12px;
    border-bottom: 1px solid #E5E7EB;
    flex-shrink: 0;
}

.filter-header span {
    font-weight: 600;
    font-size: 13px;
    color: #374151;
}

.filter-clear {
    font-size: 11px;
    color: #6B7280;
    background: none;
    border: 1px solid #D1D5DB;
    border-radius: 4px;
    padding: 2px 8px;
    cursor: pointer;
}

.filter-clear:hover {
    background: #F3F4F6;
    color: #374151;
}

.filter-list {
    overflow-y: auto;
    padding: 6px 8px;
    flex: 1;
    max-height: calc(80vh - 50px);
}

.filter-group { margin-bottom: 1px; }

.filter-item {
    display: flex;
    align-items: center;
    gap: 5px;
    padding: 3px 4px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 12px;
    color: #374151;
}

.filter-item:hover { background: #F3F4F6; }

.filter-item input[type="checkbox"] {
    width: 14px;
    height: 14px;
    cursor: pointer;
    flex-shrink: 0;
    accent-color: #3B82F6;
}

.filter-item label {
    cursor: pointer;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
    user-select: none;
}

.filter-item .tree-toggle {
    width: 16px;
    height: 16px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10px;
    color: #9CA3AF;
    cursor: pointer;
    flex-shrink: 0;
    border: none;
    background: none;
    padding: 0;
}

.filter-item .tree-toggle:hover { color: #374151; }

.filter-children {
    padding-left: 16px;
    display: none;
}

.filter-children.expanded { display: block; }

.filter-children .filter-item {
    font-size: 11px;
    padding: 2px 4px;
    color: #6B7280;
}

.filter-separator {
    font-size: 11px;
    font-weight: 600;
    color: #9CA3AF;
    padding: 6px 4px 3px;
    margin-top: 4px;
    border-top: 1px solid #E5E7EB;
    user-select: none;
}

.filter-active-count {
    font-size: 10px;
    color: #3B82F6;
    font-weight: 600;
    margin-left: auto;
    flex-shrink: 0;
}

/* Session dimming when filter is active */
.session-block {
    transition: opacity 0.2s;
}

.session-block.dimmed {
    opacity: 0.12;
    pointer-events: none;
    transition: opacity 0.2s;
}

@media (max-width: 768px) {
    .filter-panel { width: 220px; }
    .filter-toggle {
        left: -28px;
        width: 28px;
        height: 60px;
        font-size: 10px;
    }
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

    // ── Session Filter ──
    const filterDataEl = document.getElementById('filter-data');
    if (filterDataEl) {{
        const FD = JSON.parse(filterDataEl.textContent);
        const filterPanel = document.querySelector('.filter-panel');
        const filterToggle = document.querySelector('.filter-toggle');
        const filterClear = document.querySelector('.filter-clear');
        const filterList = document.querySelector('.filter-list');
        const filterCount = document.querySelector('.filter-active-count');
        // Only two sets – sessions (no-AI only) and AIs – are the source of truth.
        // Group / session visual state is DERIVED from children.
        const activeSessions = new Set();  // keys of sessions WITHOUT AIs
        const activeAIs = new Set();

        // --- helpers to look up FD ---
        function findGroup(key) {{ return FD.groups.find(function(g){{ return g.key===key; }}); }}
        function findSess(key) {{
            var out = null;
            FD.groups.forEach(function(g){{ g.sessions.forEach(function(s){{ if(s.key===key) out=s; }}); }});
            return out;
        }}

        function mkEl(tag, cls) {{ const e = document.createElement(tag); if (cls) e.className = cls; return e; }}
        function mkSpacer() {{ const s = document.createElement('span'); s.style.width='16px'; s.style.flexShrink='0'; return s; }}
        function mkToggle(container) {{
            const btn = mkEl('button','tree-toggle');
            btn.textContent = '\u25B6';
            btn.addEventListener('click', function(e) {{
                e.stopPropagation();
                const ch = container.querySelector(':scope > .filter-children');
                if (!ch) return;
                const exp = ch.classList.toggle('expanded');
                btn.textContent = exp ? '\u25BC' : '\u25B6';
            }});
            return btn;
        }}

        function buildFilterList() {{
            filterList.innerHTML = '';
            // --- Group trees ---
            FD.groups.forEach(function(group, gi) {{
                const grpDiv = mkEl('div','filter-group');
                // Group header row
                const grpRow = mkEl('div','filter-item');
                grpRow.appendChild(mkToggle(grpDiv));
                const gcb = document.createElement('input');
                gcb.type = 'checkbox'; gcb.id = 'fg'+gi; gcb.dataset.gk = group.key;
                gcb.addEventListener('change', function() {{ onGroupChange(group.key, gcb.checked); }});
                grpRow.appendChild(gcb);
                const gl = document.createElement('label'); gl.htmlFor = gcb.id;
                gl.textContent = group.name; gl.title = group.name;
                grpRow.appendChild(gl);
                grpDiv.appendChild(grpRow);

                // Sessions under this group
                const sessC = mkEl('div','filter-children');
                group.sessions.forEach(function(sess, si) {{
                    const sessWrap = mkEl('div','filter-group');
                    const sessRow = mkEl('div','filter-item');
                    if (sess.ais.length > 0) {{
                        sessRow.appendChild(mkToggle(sessWrap));
                    }} else {{
                        sessRow.appendChild(mkSpacer());
                    }}
                    const scb = document.createElement('input');
                    scb.type = 'checkbox'; scb.id = 'fs'+gi+'_'+si; scb.dataset.sk = sess.key;
                    scb.addEventListener('change', function() {{ onSessionChange(sess.key, scb.checked); }});
                    sessRow.appendChild(scb);
                    const sl = document.createElement('label'); sl.htmlFor = scb.id;
                    sl.textContent = sess.name; sl.title = sess.name;
                    sessRow.appendChild(sl);
                    sessWrap.appendChild(sessRow);

                    // AIs under this session
                    if (sess.ais.length > 0) {{
                        const aiC = mkEl('div','filter-children');
                        sess.ais.forEach(function(ai, ai_i) {{
                            const aiRow = mkEl('div','filter-item');
                            aiRow.appendChild(mkSpacer());
                            const acb = document.createElement('input');
                            acb.type = 'checkbox'; acb.id = 'fsa'+gi+'_'+si+'_'+ai_i; acb.dataset.ai = ai;
                            acb.addEventListener('change', function() {{ onAIChange(ai, acb.checked); }});
                            aiRow.appendChild(acb);
                            const al = document.createElement('label'); al.htmlFor = acb.id;
                            al.textContent = 'AI '+ai;
                            aiRow.appendChild(al);
                            aiC.appendChild(aiRow);
                        }});
                        sessWrap.appendChild(aiC);
                    }}
                    sessC.appendChild(sessWrap);
                }});
                grpDiv.appendChild(sessC);
                filterList.appendChild(grpDiv);
            }});

            // --- Separator + flat AI list ---
            if (FD.allAIs.length > 0) {{
                const sep = mkEl('div','filter-separator');
                sep.textContent = '\u2500\u2500 AI \u2500\u2500';
                filterList.appendChild(sep);
                FD.allAIs.forEach(function(ai, i) {{
                    const row = mkEl('div','filter-item');
                    row.appendChild(mkSpacer());
                    const cb = document.createElement('input');
                    cb.type = 'checkbox'; cb.id = 'fa'+i; cb.dataset.ai = ai;
                    cb.addEventListener('change', function() {{ onAIChange(ai, cb.checked); }});
                    row.appendChild(cb);
                    const lb = document.createElement('label'); lb.htmlFor = cb.id;
                    lb.textContent = 'AI '+ai;
                    row.appendChild(lb);
                    filterList.appendChild(row);
                }});
            }}
        }}

        // ── Cascade handlers ──

        // Group click → cascade to all child sessions → AIs
        function onGroupChange(key, checked) {{
            var group = findGroup(key);
            if (!group) return;
            group.sessions.forEach(function(sess) {{
                if (sess.ais.length > 0) {{
                    sess.ais.forEach(function(ai) {{
                        if (checked) activeAIs.add(ai); else activeAIs.delete(ai);
                    }});
                }} else {{
                    if (checked) activeSessions.add(sess.key); else activeSessions.delete(sess.key);
                }}
            }});
            syncCheckboxes(); applyFilter(); updateFilterHash();
        }}

        // Session click → cascade to child AIs
        function onSessionChange(key, checked) {{
            var sess = findSess(key);
            if (!sess) return;
            if (sess.ais.length > 0) {{
                sess.ais.forEach(function(ai) {{
                    if (checked) activeAIs.add(ai); else activeAIs.delete(ai);
                }});
            }} else {{
                if (checked) activeSessions.add(key); else activeSessions.delete(key);
            }}
            syncCheckboxes(); applyFilter(); updateFilterHash();
        }}

        // AI click → just toggle the AI; parents derive visually
        function onAIChange(ai, checked) {{
            if (checked) activeAIs.add(ai); else activeAIs.delete(ai);
            syncCheckboxes(); applyFilter(); updateFilterHash();
        }}

        // ── Derive visual state from activeAIs + activeSessions ──
        function syncCheckboxes() {{
            // 1. Sync all AI checkboxes (tree duplicates + flat list)
            document.querySelectorAll('input[data-ai]').forEach(function(cb) {{
                cb.checked = activeAIs.has(cb.dataset.ai);
            }});

            // 2. Session checkboxes: derive from children
            FD.groups.forEach(function(group) {{
                group.sessions.forEach(function(sess) {{
                    var scb = document.querySelector('input[data-sk="' + sess.key + '"]');
                    if (!scb) return;
                    if (sess.ais.length > 0) {{
                        var n = 0;
                        sess.ais.forEach(function(ai) {{ if (activeAIs.has(ai)) n++; }});
                        scb.checked = (n === sess.ais.length);
                        scb.indeterminate = (n > 0 && n < sess.ais.length);
                    }} else {{
                        scb.checked = activeSessions.has(sess.key);
                        scb.indeterminate = false;
                    }}
                }});
            }});

            // 3. Group checkboxes: derive from child sessions
            FD.groups.forEach(function(group) {{
                var gcb = document.querySelector('input[data-gk="' + group.key + '"]');
                if (!gcb) return;
                var total = group.sessions.length;
                if (total === 0) {{ gcb.checked = false; gcb.indeterminate = false; return; }}
                var full = 0, partial = 0;
                group.sessions.forEach(function(sess) {{
                    var scb = document.querySelector('input[data-sk="' + sess.key + '"]');
                    if (!scb) return;
                    if (scb.checked) full++;
                    else if (scb.indeterminate) partial++;
                }});
                gcb.checked = (full === total);
                gcb.indeterminate = (!gcb.checked && (full > 0 || partial > 0));
            }});

            // 4. Badge count
            var total = activeAIs.size + activeSessions.size;
            if (filterCount) {{ filterCount.textContent = total > 0 ? total : ''; }}
        }}

        function applyFilter() {{
            var hasFilter = activeAIs.size > 0 || activeSessions.size > 0;
            document.querySelectorAll('.session-block').forEach(function(block) {{
                if (!hasFilter) {{ block.classList.remove('dimmed'); return; }}
                var grp = block.getAttribute('data-group') || '';
                var nm  = block.getAttribute('data-name') || '';
                var raw = block.getAttribute('data-ai') || '';
                var aiVals = raw.split('|').filter(function(v){{ return v.trim(); }});
                var match = activeSessions.has(nm + '|' + grp) ||
                            aiVals.some(function(v){{ return activeAIs.has(v); }});
                if (match) {{ block.classList.remove('dimmed'); }} else {{ block.classList.add('dimmed'); }}
            }});
        }}

        // URL hash: s:key, a:val
        function updateFilterHash() {{
            var parts = [];
            activeSessions.forEach(function(v){{ parts.push('s:'+encodeURIComponent(v)); }});
            activeAIs.forEach(function(v){{ parts.push('a:'+encodeURIComponent(v)); }});
            if (parts.length === 0) {{
                history.replaceState(null, '', location.pathname + location.search);
            }} else {{
                history.replaceState(null, '', '#filter=' + parts.join(','));
            }}
        }}

        function loadFilterHash() {{
            var h = location.hash;
            if (!h || !h.startsWith('#filter=')) return;
            h.slice(8).split(',').forEach(function(tok) {{
                var c = tok.indexOf(':');
                if (c < 0) return;
                var type = tok.slice(0, c);
                var val  = decodeURIComponent(tok.slice(c+1));
                if (!val) return;
                if (type === 's') activeSessions.add(val);
                else if (type === 'a') activeAIs.add(val);
            }});
            syncCheckboxes();
            applyFilter();
            // Auto-expand trees with active items
            filterList.querySelectorAll('.filter-group').forEach(function(grpEl) {{
                var ch = grpEl.querySelector(':scope > .filter-children');
                if (!ch) return;
                var hasActive = ch.querySelector('input:checked') || ch.querySelector('input:indeterminate');
                if (hasActive) {{
                    ch.classList.add('expanded');
                    var tog = grpEl.querySelector(':scope > .filter-item > .tree-toggle');
                    if (tog) tog.textContent = '\u25BC';
                }}
            }});
        }}

        // Panel toggle
        filterToggle.addEventListener('click', function() {{
            var collapsed = filterPanel.classList.toggle('collapsed');
            filterToggle.textContent = collapsed ? '\u25C0 Filter' : '\u25B6';
            try {{ sessionStorage.setItem('3gpp_filter_panel', collapsed ? 'c' : 'o'); }} catch(e) {{}}
        }});

        // Restore panel state
        try {{
            if (sessionStorage.getItem('3gpp_filter_panel') === 'o') {{
                filterPanel.classList.remove('collapsed');
                filterToggle.textContent = '\u25B6';
            }}
        }} catch(e) {{}}

        // Clear all
        filterClear.addEventListener('click', function() {{
            activeSessions.clear(); activeAIs.clear();
            syncCheckboxes(); applyFilter(); updateFilterHash();
        }});

        buildFilterList();
        loadFilterHash();
    }}

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
    filter_data_json = _build_filter_data(all_sessions)
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
            block_classes = "session-block"
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

            html_parts.append(
                f'                <div class="{block_classes}" style="{style}"'
                f' data-popup="{popup_attr}"'
                f' data-ai="{data_ai_attr}"'
                f' data-name="{data_name_attr}"'
                f' data-group="{data_group_attr}">\n'
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

    # Filter panel
    html_parts.append('    <div class="filter-panel collapsed">\n')
    html_parts.append('        <button class="filter-toggle">&#9664; Filter</button>\n')
    html_parts.append('        <div class="filter-header"><span>Session Filter <span class="filter-active-count"></span></span><button class="filter-clear">Clear</button></div>\n')
    html_parts.append('        <div class="filter-list"></div>\n')
    html_parts.append('    </div>\n')

    # Filter data (JSON, read by JS)
    html_parts.append(f'    <script type="application/json" id="filter-data">{filter_data_json}</script>\n')

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


def _compact_session_label(name: str, agenda_item: str | None) -> str:
    """Return a compact single-line label for very short session blocks."""
    if agenda_item:
        return f"AI {agenda_item}"

    normalized = " ".join(name.split())
    if len(normalized) <= 22:
        return normalized
    return f"{normalized[:21]}…"
