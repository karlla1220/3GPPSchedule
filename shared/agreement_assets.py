"""Static, content-addressed agreement pages and a small lazy-loading manifest."""

import hashlib
from html import escape
import json
from pathlib import Path
import re

from shared.docx_html import DOCUMENT_CSS

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def agenda_section_ids(value, sections):
    """Resolve explicit agenda notation, never an ordinary numeric prefix.

    Keep unknown IDs for the UI's missing-section message. A section brings
    its subsections, since agreements often sit only there; .x brings the
    subsections alone. Slash shorthand denotes siblings (10.3.1/4).
    A range names the siblings it spans (20.6.12–20.6.16, 1–8).
    Schedule text and popup labels remain unchanged.
    """
    result = []
    for field in (value or '').split(','):
        field = field.strip()
        match = re.fullmatch(r'(\d+(?:\.\d+)*(?:\.x)?)(?:\.)?(?:\s+[^\d].*)?', field, re.I)
        tokens = [match[1]] if match else _range(field)
        if not tokens and '/' in field:
            parts = [part.strip().rstrip('.') for part in field.split('/')]
            if all(re.fullmatch(r'\d+(?:\.\d+)*', part) for part in parts):
                tokens = [parts[0]]
                parent = parts[0].rsplit('.', 1)[0] if '.' in parts[0] else ''
                tokens.extend(parent + '.' + part if parent and '.' not in part else part
                              for part in parts[1:])
        for token in tokens or ([field] if field else []):
            if token.lower().endswith('.x'):
                children = [ai for ai in sections if ai.startswith(token[:-1])]
                result.extend(children or [token])
            else:
                result.append(token)
                result.extend(ai for ai in sections if ai.startswith(token + '.'))
    return list(dict.fromkeys(result))


def _range(field):
    """"20.6.12–20.6.16" or "1–8": the siblings from the first to the last, else []."""
    match = re.fullmatch(r'((?:\d+\.)*)(\d+)\s*[–-]\s*((?:\d+\.)*)(\d+)', field)
    if not match or match[3] not in ('', match[1]):
        return []
    first, last = int(match[2]), int(match[4])
    if not first < last <= first + 40:
        return []
    return [f'{match[1]}{n}' for n in range(first, last + 1)]


def has_panel(schedule):
    """RAN1 always has its agreement panel. Another WG has it when its own data
    says so with its ``wg_id`` (SA1: the TDoc conclusions of its chair file,
    working_groups/sa1/conclusions.py); RAN1 data on another page is not shown."""
    return schedule.wg_id == "ran1" or (schedule.chairman_agreements or {}).get("wg_id") == schedule.wg_id


# The panel's words for RAN1's chair notes; a WG's data may give its own ("text").
PANEL_TEXT = {
    "heading": "Agreements for",
    "select": "Select a schedule cell to read its agreements here.",
}


def current_agreements(schedule):
    """The schedule's agreements, or an empty set if they belong to another meeting."""
    data = schedule.chairman_agreements
    if data.get("meeting_id") != schedule.meeting_id.lower():
        return {"status": "unavailable", "sections": {}}
    return data


ADDED_CLASS = "agreement-added"
_UNIT = re.compile(r'<(\w+) data-unit="(\d+)"(?: class="([^"]*)")?')
_FILE = re.compile(r"agreements/[0-9a-f]{20}\.html")


def finish_units(html, added):
    """Turn parse-time unit markers into the highlight class of added blocks."""
    added = set(added)

    def replace(match):
        classes = [ADDED_CLASS] if int(match[2]) in added else []
        classes += [match[3]] if match[3] else []
        return f"<{match[1]}" + (f' class="{" ".join(classes)}"' if classes else "")

    return _UNIT.sub(replace, html)


def _fragment(content):
    page = '<div class="docx-document"><article>' + content + "</article></div>"
    return f"agreements/{hashlib.sha256(page.encode()).hexdigest()[:20]}.html", page


def _files(data):
    return {
        section["file"]
        for section in (data or {}).get("sections", {}).values()
        if _FILE.fullmatch(section.get("file") or "")
    }


def package_agreements(data, previous=None):
    """Split agreements into the snapshot record and its fragment files.

    The record keeps what the panel and the next change comparison need; the
    HTML of each section becomes a content-addressed file next to
    schedule.json, so a new note commits only the sections it changed. Files of
    the previous build stay for one more build, for pages that are still open.
    """
    stored = {k: v for k, v in data.items() if k != "sections"}
    stored["sections"] = {}
    fragments = {}
    for ai, section in data.get("sections", {}).items():
        # Parts are parse-time data for merging documents; the HTML becomes a file.
        entry = {k: v for k, v in section.items() if k not in {"html", "parts"}}
        if section.get("html"):
            entry["file"], page = _fragment(
                finish_units(section["html"], section.get("added", []))
            )
            fragments[entry["file"]] = page
        stored["sections"][ai] = entry
    stored["retained_files"] = sorted(_files(previous) - _files(stored))
    return stored, fragments


def agreement_assets(schedule):
    if not has_panel(schedule):
        return {}, {}
    data = current_agreements(schedule)
    manifest = {
        k: data[k]
        for k in (
            "status",
            "document_file",
            "document_changed_at",
            "source_name",
            "source_url",
            "sha256",
            "warnings",
            "unassigned_blocks",
            "content_scope",
        )
        if k in data
    }
    if isinstance(data.get("text"), dict):
        manifest["text"] = {k: v for k, v in data["text"].items() if isinstance(v, str)}
    manifest.update(
        meeting_id=schedule.meeting_id,
        status=data.get("status", "unavailable"),
        timezone=schedule.timezone,
        sections={},
        document_css=DOCUMENT_CSS,
    )
    # Session notes by id: name and upload time, for the panel's source line.
    notes = {
        sid: {k: note[k] for k in ("name", "changed_at") if note.get(k)}
        for sid, note in (data.get("session_notes") or {}).items()
    }
    if notes:
        manifest["session_notes"] = notes
    assets = {}
    for ai, section in data.get("sections", {}).items():
        if not re.fullmatch(r"\d+(?:\.\d+)*", ai):
            continue
        entry = {
            "title": section.get("title", ""),
            "excluded_tdoc_rows": section.get("excluded_tdoc_rows", 0),
        }
        for key in ("change", "changed_at", "changed_in"):
            if section.get(key):
                entry[key] = section[key]
        held = [sid for sid in section.get("sources", {}) if sid in notes]
        if held:
            entry["notes"] = held
        if section.get("added"):
            entry["added"] = len(section["added"])
        if _FILE.fullmatch(section.get("file") or ""):
            entry["url"] = "./" + section["file"]
        elif section.get("html"):
            # Parse output that was not packaged (previews, tests).
            filename, assets[filename] = _fragment(
                finish_units(section["html"], section.get("added", []))
            )
            entry["url"] = "./" + filename
        manifest["sections"][ai] = entry
    return manifest, assets


def write_agreement_assets(schedule, directory):
    """Called by both site assembly and the standalone renderer.

    Writes the panel and any fragments not yet on disk, then removes fragments
    that neither this build nor the previous one refers to. Only HTML
    fragments are published; source document bytes are not exported.
    """
    if not has_panel(schedule):
        return
    manifest, assets = agreement_assets(schedule)
    referenced = {s["url"][2:] for s in manifest["sections"].values() if "url" in s}
    assets.update({k: v for k, v in schedule.agreement_fragments.items() if k in referenced})
    assets["agreements-panel.js"] = (TEMPLATES / "agreements-panel.js").read_text()
    for name, content in assets.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = content if isinstance(content, bytes) else content.encode("utf-8")
        if not path.exists() or path.read_bytes() != raw:
            path.write_bytes(raw)
    keep = referenced | set(current_agreements(schedule).get("retained_files", []))
    for path in sorted((directory / "agreements").glob("*.html")):
        if f"agreements/{path.name}" not in keep:
            path.unlink()
    missing = sorted(name for name in referenced if not (directory / name).exists())
    if missing:
        print(f"Warning: {len(missing)} agreement fragment(s) missing from {directory}")


def render_agreement_panel(schedule):
    if not has_panel(schedule):
        return ""
    manifest, _ = agreement_assets(schedule)
    text = {**PANEL_TEXT, **manifest.get("text", {})}
    kind = current_agreements(schedule).get("kind")
    classes = "agreement-panel" + (f" {kind}" if kind and re.fullmatch(r"[a-z-]+", kind) else "")
    payload = (
        json.dumps(manifest, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return (
        f"""<section id="agreement-panel" class="{classes}" aria-labelledby="agreement-title">
<div id="agreement-app"><div class="agreement-header"><h2 id="agreement-title">{escape(text["heading"])}</h2></div><p class="agreement-status">{escape(text["select"])}</p></div>
<noscript>JavaScript is required to select and load agreements.</noscript>
</section>
<script type="application/json" id="agreement-data">"""
        + payload
        + """</script>
<script type="importmap">{"imports":{"dompurify":"https://cdn.jsdelivr.net/npm/dompurify@3.4.16/dist/purify.es.mjs","solid-js":"https://cdn.jsdelivr.net/npm/solid-js@1.9.9/dist/solid.js","solid-js/web":"https://cdn.jsdelivr.net/npm/solid-js@1.9.9/web/dist/web.js","solid-js/html":"https://cdn.jsdelivr.net/npm/solid-js@1.9.9/html/dist/html.js"}}</script>
<script type="module" src="./agreements-panel.js"></script>
"""
    )
