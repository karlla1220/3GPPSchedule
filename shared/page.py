"""Shared page components, independent of a WG's schedule body/layout."""
from datetime import date, datetime, time
from html import escape
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from shared.navigation import TITLE_EMOJI, render_navigation

DEFAULT_NOTICE = (
    "ℹ️ This page is automatically generated from uploaded documents. "
    "Some details may not be fully accurate — your feedback helps improve it."
)
DEMO_NOTICE = "Demo schedule — fixed sample data, not an official meeting schedule."
_HTTPS_URL = r"https://[^\s\"'<>\\]+"


def normalize_presentation(value=None):
    """Validate public site metadata once, before any parsing/build side effects."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("presentation must be an object")
    defaults = {"creator": "", "creator_url": "", "creator_bio": "", "disclaimer": "",
                "feedback_url": "", "support_links": [],
                "contact_name": "", "contact_email": "", "notice": DEFAULT_NOTICE}
    unknown = value.keys() - defaults.keys()
    if unknown:
        raise ValueError(f"Unknown presentation fields: {sorted(unknown)}")
    result = defaults | value
    if any(not isinstance(v, str) for k, v in result.items() if k != "support_links"):
        raise ValueError("presentation fields must be strings")
    email = result["contact_email"]
    if email and (not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*", email)
                  or ".." in email or email.startswith(".") or ".@" in email):
        raise ValueError("presentation.contact_email must be a plain email address")
    for key in ("creator_url", "feedback_url"):
        if result[key] and not re.fullmatch(_HTTPS_URL, result[key]):
            raise ValueError(f"presentation.{key} must be an https URL")
    links = result["support_links"]
    if not isinstance(links, list) or any(
            not isinstance(link, dict) or link.keys() != {"label", "url"}
            or not all(isinstance(v, str) and v.strip() for v in link.values())
            or not re.fullmatch(_HTTPS_URL, link["url"]) for link in links):
        raise ValueError('presentation.support_links must be a list of {"label", "url"} with https URLs')
    return result


def _link(url, text):
    return f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener">{text}</a>'


def _support_links(links):
    """Every way to give, named: "Patreon or GitHub Sponsors"."""
    items = [_link(link["url"], escape(link["label"])) for link in links]
    return " or ".join(filter(None, [", ".join(items[:-1]), items[-1]]))


def render_support_links(support_links):
    """The standing invitation at the right end of the meeting titles' row."""
    if not support_links:
        return ""
    return (
        '<p class="support-links"><span class="support-icon" aria-hidden="true">☕</span>'
        '<span class="support-text"><strong>Support this tool</strong> '
        f'<span>on {_support_links(support_links)}</span></span></p>'
    )


def _support_from_ms(schedule):
    """Noon of the last meeting day at the venue; None when there is no such day."""
    if schedule.is_demo or not schedule.ends_on:
        return None
    try:
        noon = datetime.combine(date.fromisoformat(schedule.ends_on), time(12), ZoneInfo(schedule.timezone))
    except (ValueError, KeyError):
        return None
    return int(noon.timestamp() * 1000)


def render_support_note(schedule, support_links):
    """One dismissible line of thanks, shown from the last afternoon of a meeting.

    The browser decides: pages are regenerated only when a schedule changes, so
    the generated HTML cannot know that the meeting has ended since.
    """
    show_from = _support_from_ms(schedule)
    if not support_links or show_from is None:
        return ""
    script = (Path(__file__).resolve().parent.parent / "templates" / "support-note.js").read_text(encoding="utf-8")
    # "<" is escaped so a meeting id can never close the inline script.
    state_id = json.dumps(schedule.wg_id + ":" + schedule.meeting_id).replace("<", "\\u003c")
    script = script.replace("{{SHOW_FROM_MS}}", str(show_from)).replace("{{STATE_ID_JSON}}", state_id)
    return (
        '<p class="support-note" id="support-note" hidden><span>'
        f'Hope this helped you through {escape(schedule.meeting_name)}. '
        'I build and maintain it on my own time. If it saved you some time, you can support it on '
        f'{_support_links(support_links)}&nbsp;☕. Or just say hi at the next meeting!</span>'
        '<button type="button" class="support-note-close" aria-label="Dismiss">&times;</button></p>\n'
        f'<script>{script}</script>'
    )


def render_header(schedule, *, presentation=None, schedules=None, groups=None):
    """Reusable across WG layouts; supplies #tz-now for the schedule clock."""
    metadata = normalize_presentation(presentation)
    heading = (render_navigation(schedule, schedules, groups) if schedules is not None
               else f"<h1>{TITLE_EMOJI}{escape(schedule.meeting_name)}</h1>")
    sources = ", ".join(schedule.source_files) if schedule.source_files else schedule.source_file
    parts = ["<header>", heading, render_support_links(metadata["support_links"]),
             f'<p class="meta">Updated Files: {escape(sources)} &nbsp;|&nbsp; Generated: {escape(schedule.generated_at)} ({escape(schedule.timezone)}) &nbsp;|&nbsp; Now: <span id="tz-now">...</span> ({escape(schedule.timezone)})</p>']
    if schedule.is_demo:
        parts.append(f'<p class="demo-notice">{DEMO_NOTICE}</p>')
    elif metadata["notice"]:
        parts.append(f'<p class="meta">{escape(metadata["notice"])}</p>')
    support_note = render_support_note(schedule, metadata["support_links"])
    if support_note:
        parts.append(support_note)
    email = escape(metadata["contact_email"], quote=True)
    mailto = f'<a href="mailto:{email}">{email}</a>'
    # The creator's own address follows the name; another contact gets its own line.
    own_email = bool(email and metadata["creator"] and metadata["contact_name"] in ("", metadata["creator"]))
    if metadata["creator"]:
        creator = escape(metadata["creator"])
        if metadata["creator_url"]:
            creator = _link(metadata["creator_url"], creator)
        if own_email:
            creator += f" ({mailto})"
        bio = f', {escape(metadata["creator_bio"])}' if metadata["creator_bio"] else ""
        parts.append(f'<p class="meta">Created by {creator}{bio}</p>')
    about = [escape(metadata["disclaimer"])] if metadata["disclaimer"] else []
    if metadata["feedback_url"]:
        about.append(_link(metadata["feedback_url"], "Feedback"))
    if about:
        parts.append(f'<p class="meta">{" · ".join(about)}</p>')
    if email and not own_email:
        name = escape(metadata["contact_name"])
        parts.append(f'<p class="meta">Contact: {name} ({mailto}) for reports or feature requests.</p>')
    parts.append("</header>")
    return "\n".join(filter(None, parts))
