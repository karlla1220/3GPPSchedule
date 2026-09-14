"""Shared page components, independent of a WG's schedule body/layout."""
from html import escape
import re

from shared.navigation import render_navigation

DEFAULT_NOTICE = (
    "ℹ️ This page is automatically generated from uploaded documents. "
    "Some details may not be fully accurate — your feedback helps improve it."
)
DEMO_NOTICE = "Demo schedule — fixed sample data, not an official meeting schedule."


def normalize_presentation(value=None):
    """Validate public site metadata once, before any parsing/build side effects."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("presentation must be an object")
    defaults = {"creator": "", "contact_name": "", "contact_email": "", "notice": DEFAULT_NOTICE}
    unknown = value.keys() - defaults.keys()
    if unknown:
        raise ValueError(f"Unknown presentation fields: {sorted(unknown)}")
    result = defaults | value
    if any(not isinstance(v, str) for v in result.values()):
        raise ValueError("presentation fields must be strings")
    email = result["contact_email"]
    if email and (not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*", email)
                  or ".." in email or email.startswith(".") or ".@" in email):
        raise ValueError("presentation.contact_email must be a plain email address")
    return result


def render_header(schedule, *, presentation=None, schedules=None, groups=None):
    """Reusable across WG layouts; supplies #tz-now for the schedule clock."""
    metadata = normalize_presentation(presentation)
    heading = (render_navigation(schedule, schedules, groups) if schedules is not None
               else f"<h1>{escape(schedule.meeting_name)}</h1>")
    sources = ", ".join(schedule.source_files) if schedule.source_files else schedule.source_file
    parts = ["<header>", heading,
             f'<p class="meta">Updated Files: {escape(sources)} &nbsp;|&nbsp; Generated: {escape(schedule.generated_at)} ({escape(schedule.timezone)}) &nbsp;|&nbsp; Now: <span id="tz-now">...</span> ({escape(schedule.timezone)})</p>']
    if schedule.is_demo:
        parts.append(f'<p class="demo-notice">{DEMO_NOTICE}</p>')
    elif metadata["notice"]:
        parts.append(f'<p class="meta">{escape(metadata["notice"])}</p>')
    if metadata["creator"]:
        parts.append(f'<p class="meta">Created by {escape(metadata["creator"])}</p>')
    if metadata["contact_email"]:
        email = escape(metadata["contact_email"], quote=True)
        name = escape(metadata["contact_name"])
        parts.append(f'<p class="meta">Contact: {name} (<a href="mailto:{email}">{email}</a>) for reports or feature requests.</p>')
    parts.append("</header>")
    return "\n".join(parts)
