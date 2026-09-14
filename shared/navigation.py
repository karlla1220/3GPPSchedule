"""Meeting navigation shared by every generated WG page."""
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from shared.portal_meetings import find_meeting


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


def render_navigation(selected, schedules, groups):
    """Selected meeting is centered even when there is just one sibling."""
    entries = []
    for group in groups:
        schedule = schedules.get(group["id"])
        if schedule is None:
            continue
        status = meeting_status(schedule)
        # Each WG keeps its latest saved schedule accessible until replaced.
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
