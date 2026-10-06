"""Static, content-addressed agreement pages and a small lazy-loading manifest."""

import hashlib
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
    Schedule text and popup labels remain unchanged.
    """
    result = []
    for field in (value or '').split(','):
        field = field.strip()
        match = re.fullmatch(r'(\d+(?:\.\d+)*(?:\.x)?)(?:\.)?(?:\s+[^\d].*)?', field, re.I)
        tokens = [match[1]] if match else []
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


def current_agreements(schedule):
    """The schedule's agreements, or an empty set if they belong to another meeting."""
    data = schedule.chairman_agreements
    if data.get("meeting_id") != schedule.meeting_id.lower():
        return {"status": "unavailable", "sections": {}}
    return data


def agreement_assets(schedule):
    if schedule.wg_id != "ran1":
        return {}, {}
    data = current_agreements(schedule)
    manifest = {
        k: data[k]
        for k in (
            "status",
            "document_file",
            "source_name",
            "source_url",
            "sha256",
            "warnings",
            "unassigned_blocks",
            "content_scope",
        )
        if k in data
    }
    manifest.update(
        meeting_id=schedule.meeting_id,
        status=data.get("status", "unavailable"),
        sections={},
        document_css=DOCUMENT_CSS,
    )
    assets = {}
    for ai, section in data.get("sections", {}).items():
        if not re.fullmatch(r"\d+(?:\.\d+)*", ai):
            continue
        content = section.get("html", "")
        entry = {
            "title": section.get("title", ""),
            "excluded_tdoc_rows": section.get("excluded_tdoc_rows", 0),
        }
        if content:
            page = '<div class="docx-document"><article>' + content + "</article></div>"
            digest = hashlib.sha256(page.encode()).hexdigest()[:20]
            filename = f"agreements/{digest}.html"
            assets[filename] = page
            entry["url"] = "./" + filename
        manifest["sections"][ai] = entry
    return manifest, assets


def write_agreement_assets(schedule, directory):
    """Called by both site assembly and the standalone renderer.

    Old hash-named files may remain for already-open tabs across rebuilds.
    Only HTML fragments are published; source document bytes are not exported.
    """
    if schedule.wg_id != "ran1":
        return
    _, assets = agreement_assets(schedule)
    assets["agreements-panel.js"] = (TEMPLATES / "agreements-panel.js").read_text()
    for name, content in assets.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = content if isinstance(content, bytes) else content.encode("utf-8")
        if not path.exists() or path.read_bytes() != raw:
            path.write_bytes(raw)


def render_agreement_panel(schedule):
    if schedule.wg_id != "ran1":
        return ""
    manifest, _ = agreement_assets(schedule)
    payload = (
        json.dumps(manifest, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return (
        """<section id="agreement-panel" class="agreement-panel" aria-labelledby="agreement-title">
<div id="agreement-app"><h2 id="agreement-title">Agreements for</h2><p class="agreement-empty">Select a schedule cell to read its agreements here.</p></div>
<noscript>JavaScript is required to select and load agreements.</noscript>
</section>
<script type="application/json" id="agreement-data">"""
        + payload
        + """</script>
<script type="importmap">{"imports":{"dompurify":"https://cdn.jsdelivr.net/npm/dompurify@3.4.16/dist/purify.es.mjs","solid-js":"https://cdn.jsdelivr.net/npm/solid-js@1.9.9/dist/solid.js","solid-js/web":"https://cdn.jsdelivr.net/npm/solid-js@1.9.9/web/dist/web.js","solid-js/html":"https://cdn.jsdelivr.net/npm/solid-js@1.9.9/html/dist/html.js"}}</script>
<script type="module" src="./agreements-panel.js"></script>
"""
    )
