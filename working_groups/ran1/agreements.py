"""Exact agenda sections from chairman notes, excluding only TDoc metadata.

Labels such as Agreement, Proposal and Potential agreement are ordinary source
content, never extraction boundaries or an assertion that the text was agreed.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from lxml import etree as ET

from shared import remote_files
from shared.metafile_images import backend_identity
from shared.docx_html import DocxHTML, NS, val, text_of, render_blocks
from .agenda_descriptions import StyleMap, NumberingMap, _extract_leading_agenda_marker
from .downloader import (
    _extract_meeting_id,
    _local_doc_preference,
    get_latest_chair_notes_info,
    download_latest_chair_notes,
)
from .parser import find_chair_notes_docx

PARSER_VERSION = 16
CACHE_DIR = Path(".cache/ran1/agreements")
TDOC_ID = re.compile(r"R1-\d{6,}(?:\s*(?:rev\.?|r)\s*\d+)?", re.I)


def _tdoc_entry(text: str) -> bool:
    """Remove IDs alone or tabular ID/title[/source] entries, not prose citations.

    Whitespace-only separation is ambiguous: retaining it is safer than silently
    dropping substantive text beginning with a document number.
    """
    lines = text.strip().splitlines()
    return bool(lines) and all(
        TDOC_ID.fullmatch(line.split("\t", 1)[0].strip()) for line in lines
    )


def _without_tdocs(block):
    """Return a filtered copy and number of excluded metadata rows.

    A table row is metadata only if its first cell is an ID and each cell is a
    single text paragraph. Mixed/merged/illustrated rows stay intact. A known
    TDoc header is removed only when every data row in the table is metadata.
    """
    tag = ET.QName(block).localname
    if tag == "p":
        # An ID caption with an equation/image is content, not a metadata row.
        rich = block.xpath('.//*[local-name()="drawing" or local-name()="pict" '
                           'or local-name()="oMath" or local-name()="object"]')
        return (None, 1) if not rich and _tdoc_entry(text_of(block)) else (block, 0)
    if tag != "tbl":
        return block, 0
    filtered = deepcopy(block)
    removed = 0
    for row in list(filtered.findall("w:tr", NS)):
        cells = row.findall("w:tc", NS)
        simple = cells and all(
            len(c.findall("w:p", NS)) == 1
            and not c.xpath('.//*[local-name()="tbl" or local-name()="drawing" '
                            'or local-name()="pict" or local-name()="oMath" '
                            'or local-name()="object" or local-name()="vMerge" '
                            'or local-name()="gridSpan"]')
            for c in cells
        )
        if simple and TDOC_ID.fullmatch(text_of(cells[0]).strip()):
            filtered.remove(row)
            removed += 1
    rows = filtered.findall("w:tr", NS)
    if removed and len(rows) == 1:
        fields = [text_of(c).strip().lower() for c in rows[0].findall("w:tc", NS)]
        if fields and fields[0] in {"tdoc", "tdoc number", "document number"} and all(
            f in {"title", "source", "author", "authors", "company", "submitter"}
            for f in fields[1:]
        ):
            filtered.remove(rows[0])
    if not filtered.findall("w:tr", NS):
        return None, removed
    # Recurse into nested tables; do not erase IDs from retained complex rows.
    for cell in filtered.findall("w:tr/w:tc", NS):
        for child in cell.findall("w:tbl", NS):
            clean, count = _without_tdocs(child)
            removed += count
            if clean is None:
                cell.remove(child)
            elif clean is not child:
                cell.replace(child, clean)
    return filtered, removed


def _body_blocks(parent):
    """Unwrap block-level content controls without losing their agenda headings."""
    for block in parent:
        tag = ET.QName(block).localname
        if tag in {"sdt", "sdtContent", "customXml"}:
            yield from _body_blocks(block)
        elif tag in {"p", "tbl"}:
            yield block


def parse_agreements(path: Path, meeting_id: str, *, source_name: str | None = None) -> dict:
    """Return portable HTML fragments plus source identity; reject unknown meetings.

    ``source_name`` is the published name when ``path`` was extracted from a
    zip: the inner document is often named without the meeting.
    """
    if not meeting_id or _extract_meeting_id(source_name or path.name) != meeting_id.lower():
        raise ValueError("Chairman note must identify the exact schedule meeting")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    backend = hashlib.sha256(backend_identity().encode()).hexdigest()[:12]
    cache = CACHE_DIR / f"{PARSER_VERSION}-{backend}-{digest}.json"
    if cache.exists():
        try:
            cached = json.loads(cache.read_text())
            if (
                cached["meeting_id"] == meeting_id.lower()
                and cached["sha256"] == digest
            ):
                return {**cached, "document_file": path.name}
        except (ValueError, KeyError, TypeError):
            pass
    converter = DocxHTML(path)
    styles = StyleMap(converter.parts["word/styles.xml"])
    numbers = NumberingMap(converter.parts.get("word/numbering.xml"))
    sections = {}
    agenda = None
    pieces = []
    excluded = 0
    unassigned = 0

    def finish():
        nonlocal pieces, excluded
        if agenda is not None:
            # Blank paragraphs retain internal spacing; boundary whitespace is
            # not useful as standalone section content.
            while pieces and not pieces[-1][0]:
                pieces.pop()
            while pieces and not pieces[0][0]:
                pieces.pop(0)
            sections[agenda]["html"] += render_blocks(part for _, part in pieces)
            sections[agenda]["excluded_tdoc_rows"] += excluded
        pieces, excluded = [], 0

    for block in _body_blocks(converter.body):
        tag = ET.QName(block).localname
        txt = text_of(block).strip()
        if tag == "p":
            sid = val(block, "w:pPr/w:pStyle")
            outline = val(block, "w:pPr/w:outlineLvl")
            level = styles.get_outline_level(
                sid, int(outline) if outline is not None else None
            )
            if level is not None and txt:
                explicit = _extract_leading_agenda_marker(txt)
                if explicit:
                    marker, title = explicit
                    numbers.apply_explicit_marker(marker)
                else:
                    nid = val(block, "w:pPr/w:numPr/w:numId")
                    ilvl = val(block, "w:pPr/w:numPr/w:ilvl")
                    if nid is None and ilvl is None:
                        nid, ilvl = styles.get_style_num_pr(sid)
                    marker = numbers.get_heading_marker(
                        num_id=nid,
                        ilvl=int(ilvl) if ilvl is not None else None,
                        outline_level=level,
                    )
                    title = txt
                if marker and re.fullmatch(r"\d+(?:\.\d+)*", marker):
                    finish()
                    agenda = marker
                    sections.setdefault(agenda, {
                        "title": title, "html": "", "excluded_tdoc_rows": 0,
                    })
                    converter.block(block)
                    continue
                # Unnumbered subheadings remain part of the current section.
        clean, count = _without_tdocs(block)
        excluded += count
        if clean is None:
            converter.block(block)  # Advance Word counters even for removed rows.
            continue
        rendered = converter.block(clean)
        if agenda is not None:
            has_content = bool(txt or tag == "tbl" or clean.xpath(
                './/*[local-name()="drawing" or local-name()="pict" '
                'or local-name()="oMath" or local-name()="object"]'
            ))
            pieces.append((has_content, rendered))
        elif txt:
            unassigned += 1
    finish()
    result = {
        "status": "ready",
        "meeting_id": meeting_id.lower(),
        "document_file": path.name,
        "sha256": digest,
        "parser_version": PARSER_VERSION,
        "sections": sections,
        "warnings": sorted(converter.warnings),
        "unassigned_blocks": unassigned,
        "content_scope": "agenda-section-excluding-tdocs",
    }
    cache.parent.mkdir(parents=True, exist_ok=True)
    temp = cache.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False))
    temp.replace(cache)
    return result


def remote_reference(cfg, meeting_id):
    """Revalidate bytes even when filename, upload time and timezone are unchanged.

    Check and build share the transport cache. Failures propagate so a temporary
    listing/download failure cannot erase a previously published agreement.
    """
    if not meeting_id:
        return None
    info = get_latest_chair_notes_info(
        urls=cfg["inbox_urls"],
        extra_folders=cfg["extra_folders"],
        preferred_meeting_id=meeting_id,
        strict=True,
    )
    if info is None:
        return None
    if _extract_meeting_id(info["name"]) != meeting_id.lower():
        raise ValueError("Remote chairman note belongs to a different meeting")
    _, meta = remote_files.fetch_file(None, info["url"])
    return {**info, "sha256": meta["sha256"]}


def reference_identity(info):
    if info is None:
        return None
    return {k: info[k] for k in ("name", "url", "sha256") if k in info}


def _unavailable(meeting_id, warning=None):
    data = {"status": "unavailable", "meeting_id": meeting_id or "", "sections": {}}
    if warning:
        data["warnings"] = [warning]
    return data


def local_note_path(cfg, meeting_id, *, offline=False, schedule_path=None):
    """The local note both the check and the build use, so their identities agree.

    Manual references and configured extra_files take precedence over remote
    documents. Download caches count only offline; a previous meeting's file
    never becomes a fallback.
    """
    if not meeting_id:
        return None
    directories = [Path("ref_in_manual/ran1")]
    if offline or any(
        entry.get("type") == "chair_notes" for entry in cfg.get("extra_files", [])
    ):
        directories.append(Path("downloads/ran1/extra_files"))
    if offline:
        if schedule_path is not None:
            directories.append(schedule_path.parent)
        directories.append(Path("downloads/ran1/Chair_notes"))
    paths = [find_chair_notes_docx(d, meeting_id=meeting_id) for d in directories]
    paths = [p for p in paths if p is not None]
    return max(paths, key=_local_doc_preference) if paths else None


def build_agreements(*, cfg, meeting_id, schedule_path, offline, previous=None):
    """Return (agreements, source identity).

    A note that cannot be parsed keeps ``previous`` (the last published
    agreements of this meeting) but still reports its identity, so the check
    job does not rebuild for the same broken document every hour. Listing and
    download failures propagate; the caller keeps the previous identity.
    """
    if not meeting_id:
        return _unavailable(meeting_id), None
    info = None
    path = local_note_path(cfg, meeting_id, offline=offline, schedule_path=schedule_path)
    if path is None:
        if offline:
            return _unavailable(meeting_id), None
        info = remote_reference(cfg, meeting_id)
        if info is None:
            return _unavailable(meeting_id), None
        path = download_latest_chair_notes(
            latest_info=info, preferred_meeting_id=meeting_id, force=True
        )
        if path is None:
            raise RuntimeError("Selected chairman note could not be downloaded")
        identity = reference_identity(info)
    else:
        identity = {
            "origin": "local",
            "name": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    try:
        result = parse_agreements(
            path, meeting_id, source_name=info["name"] if info else None
        )
    except Exception as exc:
        warning = f"Chairman note {identity['name']} could not be parsed: {exc}"
        print(f"Warning: {warning}")
        return previous or _unavailable(meeting_id, warning), identity
    if info:
        result["source_url"] = info["url"]
        result["source_name"] = info["name"]
        result["warnings"] = sorted(set(result["warnings"] + info.get("source_warnings", [])))
    return result, identity


def previous_agreements(meeting_id, snapshot=Path("docs/ran1/schedule.json")):
    """Agreements last published for this meeting, or None."""
    if not meeting_id:
        return None
    try:
        data = json.loads(snapshot.read_text(encoding="utf-8")).get("chairman_agreements")
    except (OSError, ValueError, AttributeError):
        return None
    if isinstance(data, dict) and data.get("status") == "ready" and data.get("meeting_id") == meeting_id.lower():
        return data
    return None


def local_note_reference(cfg, meeting_id):
    """Identity of an authoritative manual/extra note, never a download fallback."""
    path = local_note_path(cfg, meeting_id)
    if path is None:
        return None
    return {
        "origin": "local",
        "name": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
