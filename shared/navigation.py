"""Meeting navigation shared by every generated WG page."""
from datetime import date, datetime
from html import escape
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from shared.portal_meetings import find_meeting

# Marks every meeting name, the page's own title and its sibling links alike,
# so the other WGs read as meetings to switch to rather than meta text.
TITLE_EMOJI = '<span class="wg-emoji" aria-hidden="true">📅</span>'


def select_default_wg(config, schedules, meetings=None, now=None):
    """Prefer an ongoing, available meeting; keep configured order for ties.

    Live Portal classifications are authoritative when supplied. Offline
    rendering uses saved venue dates. Demo/undated pages cannot claim priority.
    """
    ongoing = []
    for group in config['working_groups']:
        wg = group['id']
        schedule = schedules.get(wg)
        if schedule is None or schedule.is_demo:
            continue
        if meetings is None:
            active = meeting_status(schedule, now) == 'In progress'
        else:
            row = find_meeting(meetings, wg, schedule.meeting_id)
            active = row is not None and row.get('MeetingPeriod') == 'ONGOING'
        if active:
            ongoing.append(wg)
    default = config['default_wg']
    return default if not ongoing or default in ongoing else ongoing[0]


def meeting_status(schedule, now=None):
    if schedule.is_demo:
        return "Demo"
    if not schedule.starts_on or not schedule.ends_on:
        return "Latest schedule"
    today = (now or datetime.now(ZoneInfo(schedule.timezone))).astimezone(ZoneInfo(schedule.timezone)).date().isoformat()
    if today < schedule.starts_on:
        return "Upcoming"
    if today > schedule.ends_on:
        return "Ended"
    return "In progress"


# Menu sections, top to bottom: meetings that are on or coming first, ended ones last.
STATUS_ORDER = ("In progress", "Upcoming", "Latest schedule", "Ended", "Demo")
# 3GPP's own order: TSG RAN, then SA, then CT; each plenary before its WGs.
_TSG_ORDER = ("ran", "sa", "ct")
_WG_ID = re.compile(r"(ran|sa|ct)(\d*)(-plenary)?")
# Spelled out, not strftime("%b"): the build machine's locale must not reach the page.
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def tsg_rank(wg_id):
    """RAN plenary, RAN1, RAN2, ..., SA, SA1, ...; ids outside this scheme come last."""
    match = _WG_ID.fullmatch(wg_id)
    if not match:
        return (len(_TSG_ORDER), 0)
    return (_TSG_ORDER.index(match[1]), int(match[2] or 0))


def _date_range(starts_on, ends_on):
    """"13–17 Oct", "28 Sep – 2 Oct"; empty when the venue dates are unknown."""
    try:
        start, end = date.fromisoformat(starts_on), date.fromisoformat(ends_on)
    except (TypeError, ValueError):
        return ""
    first, last = f"{start.day} {_MONTHS[start.month - 1]}", f"{end.day} {_MONTHS[end.month - 1]}"
    if start == end:
        return first
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day}\u2013{last}"
    return f"{first} \u2013 {last}"


def _menu(selected, entries):
    sections = []
    for status in STATUS_ORDER:
        rows = []
        for group, schedule in entries:
            if meeting_status(schedule) != status:
                continue
            classes = "wg-switch-item" + (" wg-switch-past" if status in ("Ended", "Demo") else "")
            current = ' aria-current="page"' if group["id"] == selected.wg_id else ""
            dates = _date_range(schedule.starts_on, schedule.ends_on)
            rows.append(f'<a class="{classes}" href="../{escape(group["id"])}/"{current}>'
                        f'<span>{escape(schedule.meeting_name)}</span>'
                        + (f'<span class="wg-switch-dates">{dates}</span>' if dates else "") + '</a>')
        if rows:
            sections.append(f'<p class="wg-switch-group">{status}</p>' + "".join(rows))
    return '<nav class="wg-switch-menu" aria-label="Working group meetings">' + "".join(sections) + "</nav>"


def render_navigation(selected, schedules, groups):
    """The meeting title is the switcher: it opens every WG's meeting, active ones first."""
    configured = [(group, schedules[group["id"]]) for group in groups if group["id"] in schedules]
    order = {group["id"]: index for index, (group, _) in enumerate(configured)}
    entries = sorted(configured, key=lambda entry: (
        STATUS_ORDER.index(meeting_status(entry[1])), tsg_rank(entry[0]["id"]), order[entry[0]["id"]]))
    title = f"<h1>{TITLE_EMOJI}{escape(selected.meeting_name)}</h1>"
    status = f'<span class="meeting-status">{meeting_status(selected)}</span>'
    if len(entries) < 2:
        return f'<div class="wg-title">{title}{status}</div>'
    others = ", ".join(escape(schedule.meeting_name) for group, schedule in entries if group["id"] != selected.wg_id)
    script = (Path(__file__).resolve().parent.parent / "templates" / "wg-switch.js").read_text(encoding="utf-8")
    # "<" is escaped so a WG id can never close the inline script.
    ids = json.dumps(sorted(group["id"] for group, _ in entries)).replace("<", "\\u003c")
    return (
        '<div class="wg-title"><details class="wg-switch" id="wg-switch">'
        f'<summary title="Switch meeting">{title}<span class="wg-switch-chevron" aria-hidden="true">\u25BC</span></summary>'
        f'{_menu(selected, entries)}</details>{status}'
        '<p class="wg-switch-hint" id="wg-switch-hint" hidden>'
        f'<span><strong>Switch meetings here</strong> \u2014 {others}</span>'
        '<button type="button">Got it</button></p></div>\n'
        f'<script>{script.replace("{{WG_IDS_JSON}}", ids)}</script>'
    )
