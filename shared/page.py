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
# "v03" standing alone in a file name; "V2X" is a topic, not a version.
_VERSION = re.compile(r"(?<![A-Za-z0-9])v\d+(?![A-Za-z0-9])", re.IGNORECASE)
# "Draft" standing alone: the chair restarts at v00 when the draft is released.
_DRAFT = re.compile(r"(?<![A-Za-z0-9])draft(?![A-Za-z0-9])", re.IGNORECASE)
# "Final" and "EOM" standing alone, with their own number glued on ("eom",
# "eom1", "eom_02"): the closing notes, after the last version.
_CLOSING = re.compile(r"(?<![A-Za-z0-9])(eom|final)(?:_?(\d+))?(?![A-Za-z0-9])", re.IGNORECASE)
_CLOSING_LABEL = {"eom": "EOM", "final": "Final"}
# The agenda table a WG reads beside its schedule: "agenda.csv", "agenda_133bis.csv".
_AGENDA_CSV = re.compile(r"agenda[^/\\]*\.csv", re.IGNORECASE)


def normalize_presentation(value=None):
    """Validate public site metadata once, before any parsing/build side effects."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("presentation must be an object")
    defaults = {"creator": "", "creator_url": "", "creator_bio": "", "disclaimer": "",
                "feedback_url": "", "support_title": "Support this tool", "support_links": [],
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


def render_support_links(support_links, title):
    """The standing invitation at the right end of the meeting titles' row."""
    if not support_links:
        return ""
    return (
        '<p class="support-links"><span class="support-icon" aria-hidden="true">☕</span>'
        f'<span class="support-text"><strong>{escape(title)}</strong> '
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
        'I build and maintain it on my own time. If it saved you some time, you can buy me a coffee on '
        f'{_support_links(support_links)}&nbsp;☕. Or just say hi at the next meeting!</span>'
        '<button type="button" class="support-note-close" aria-label="Dismiss">&times;</button></p>\n'
        f'<script>{script}</script>'
    )


# The tour, top of the page to the bottom. Each step spotlights its target on
# the page itself: a CSS selector, or "cell" (a session in view) and "grid"
# (the day's grid) which the script finds. A step whose target the page does
# not have or does not show is skipped, so each WG gets the steps for what it
# has (the agreement panel on RAN1, the additional information on RAN2, the
# topics on RAN P). "scene" plays that feature on copies of the page's own
# cells in the card; "touch" steps are for phones only.
HELP_STEPS = (
    {"key": "switch", "target": "#wg-switch summary", "title": "Switch meetings from the title",
     "text": "The meeting name opens every WG\u2019s meeting, ongoing ones first."},
    {"key": "days", "target": ".schedule-toolbar", "title": "Pick a day",
     "text": "Each day of the meeting has its own grid. During the meeting, NOW marks the current time on today\u2019s."},
    {"key": "details", "target": "cell", "scene": "details", "title": "Tap a session for its details",
     "text": "Group, chair, room, notes and the agenda item names. Tap again, or anywhere else, to close it."},
    {"key": "filter", "target": ".filter-toggle", "scene": "filter", "title": "Follow only your agenda items",
     "text": "Open Filter here and tick what you follow; other sessions fade. "
             "The filter is kept for this meeting and is part of the link, so you can share what you see."},
    {"key": "pinch", "target": "grid", "scene": "pinch", "touch": True, "title": "Pinch to see the whole day",
     "text": "Pinch the grid itself to see every room at once. Spreading back, it stops at 100% for a moment."},
    {"key": "agreements", "target": "#agreement-panel:not(.tdoc-conclusions)", "title": "Agreements below the grid",
     "text": "Tapping a session shows the chair notes for its agenda item here. "
             "Parts added in the latest notes have a green bar."},
    {"key": "conclusions", "target": "#agreement-panel.tdoc-conclusions", "title": "TDoc conclusions below the grid",
     "text": "Tapping a session shows the TDocs of its agenda item and their conclusions here. "
             "Rows changed in the latest chair file have a green bar."},
    {"key": "informal", "target": ".email-informal", "title": "Off-offline and FL whereabouts",
     "text": "Informal meetings announced on the email threads for the chosen day. "
             "They are not on the schedule; tapping a session marks its own."},
    {"key": "supplements", "target": ".supplements", "title": "Additional information",
     "text": "What the chair\u2019s schedule says besides the grid, such as deadlines, breaks and offline discussions. "
             "Tap the heading to fold it."},
    {"key": "topics", "target": ".topic-references", "title": "Topics",
     "text": "The chair\u2019s topics with their documents, notes and agenda items. Tap the heading to fold it."},
    {"key": "help", "target": "#help-button", "title": "Help is always here",
     "text": "Start this tour again any time. The page reloads itself every few minutes "
             "and comes back to the day and spot you were looking at."},
)


def render_help():
    """The Help button at the top left and the tour it starts."""
    script = (Path(__file__).resolve().parent.parent / "templates" / "help-guide.js").read_text(encoding="utf-8")
    # "<" is escaped so no step text can close the inline script.
    steps = json.dumps(HELP_STEPS, ensure_ascii=False).replace("<", "\\u003c")
    return (
        '<button type="button" class="help-button" id="help-button" aria-label="Help" title="How to use this page" '
        'aria-haspopup="dialog" aria-controls="help-guide"><span aria-hidden="true">?</span></button>\n'
        '<dialog class="help-guide" id="help-guide" aria-labelledby="help-title" aria-describedby="help-text">'
        '<div class="help-spot" aria-hidden="true"></div>'
        '<section class="help-card"><div class="help-card-head"><span class="help-count"></span>'
        '<button type="button" class="help-close" aria-label="Close the tour">&times;</button></div>'
        '<h2 id="help-title"></h2><p id="help-text"></p>'
        '<div class="help-stage" aria-hidden="true" inert></div>'
        '<div class="help-nav"><button type="button" class="help-back">Back</button>'
        '<button type="button" class="help-next">Next</button></div></section></dialog>\n'
        f'<script>{script.replace("{{HELP_STEPS_JSON}}", steps)}</script>'
    )


def _source(name, label):
    """One source as "Main v03": readers check the version, the meeting is in the title above.

    A draft says so ("Main draft v03"): its numbers and the released file's are
    separate series, so "v03" alone does not tell which one is shown.
    The closing notes count on their own ("Main Final", "Main EOM", "Main EOM2").
    The file name stays on hover. A file without a label is shown by its name.
    """
    if not label:
        return escape(name)
    versions = _VERSION.findall(name)
    closing = _CLOSING.search(name)
    shown = [label, "draft" if _DRAFT.search(name) else "",
             f"{_CLOSING_LABEL[closing[1].lower()]}{int(closing[2]) if closing[2] else ''}" if closing else "",
             versions[-1].lower() if versions else ""]
    text = escape(" ".join(filter(None, shown))).replace(" ", "&nbsp;")
    return f'<span title="{escape(name, quote=True)}">{text}</span>'


def render_sources(schedule):
    # agenda.csv is read by every WG at every build; the line names the schedule files.
    names = [name for name in schedule.source_files if not _AGENDA_CSV.fullmatch(name)] or [schedule.source_file]
    return " · ".join(_source(name, schedule.source_labels.get(name)) for name in names)


def render_header(schedule, *, presentation=None, schedules=None, groups=None):
    """Reusable across WG layouts; supplies #tz-now for the schedule clock."""
    metadata = normalize_presentation(presentation)
    heading = (render_navigation(schedule, schedules, groups) if schedules is not None
               else f"<h1>{TITLE_EMOJI}{escape(schedule.meeting_name)}</h1>")
    parts = ["<header>", render_help(), heading, render_support_links(metadata["support_links"], metadata["support_title"]),
             f'<p class="meta">Sources: {render_sources(schedule)} &nbsp;|&nbsp; Generated: {escape(schedule.generated_at)} ({escape(schedule.timezone)}) &nbsp;|&nbsp; Now: <span id="tz-now">...</span> ({escape(schedule.timezone)})</p>']
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
