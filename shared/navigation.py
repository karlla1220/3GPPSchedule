"""Meeting navigation shared by every generated WG page."""
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo


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


def render_navigation(selected, schedules, groups):
    """Selected meeting is centered even when there is just one sibling."""
    entries = []
    for group in groups:
        schedule = schedules.get(group["id"])
        if schedule is None:
            continue
        status = meeting_status(schedule)
        # Undated legacy results stay accessible without claiming to be live.
        if group["id"] != selected.wg_id and status in {"Upcoming", "Ended"}:
            continue
        label = schedule.meeting_name
        href = f'../{group["id"]}/'
        if group["id"] == selected.wg_id:
            current = f'<div class="wg-current"><h1><a href="{escape(href)}" aria-current="page">{escape(label)}</a></h1><span class="meeting-status">{status}</span></div>'
        else:
            entries.append(f'<a class="wg-link" href="{escape(href)}">{escape(label)}<span class="meeting-status">{status}</span></a>')
    middle = (len(entries) + 1) // 2
    return ('<nav class="wg-navigation" aria-label="Working group meetings">'
            '<div class="wg-side wg-left">' + ''.join(entries[:middle]) + '</div>'
            + current + '<div class="wg-side wg-right">' + ''.join(entries[middle:]) + '</div></nav>')
