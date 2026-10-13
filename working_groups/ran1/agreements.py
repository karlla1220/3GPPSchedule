"""Exact agenda sections from chairman notes, with lists of TDocs folded away.

Labels such as Agreement, Proposal and Potential agreement are ordinary source
content, never extraction boundaries or an assertion that the text was agreed.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import re

from lxml import etree as ET

from shared import remote_files
from shared.metafile_images import backend_identity
from shared.agreement_assets import finish_units
from shared.docx_html import DocxHTML, ListItem, NS, attr, val, text_of, render_blocks
from .agenda_descriptions import StyleMap, NumberingMap, _extract_leading_agenda_marker
from .downloader import (
    WORD_DOCUMENT_EXTENSIONS,
    _extract_meeting_id,
    _local_doc_preference,
    extract_document_from_zip,
    get_latest_chair_notes_info,
)
from .parser import find_chair_notes_docx

PARSER_VERSION = 19
CACHE_DIR = Path(".cache/ran1/agreements")
TDOC_ID = re.compile(r"R1-\d{6,}(?:\s*(?:rev\.?|r)\s*\d+)?", re.I)
# "(Late contribution)", "(Revision of R1-…)", "(Withdrawn)" under a TDoc row.
TDOC_REMARK = re.compile(r"\([^()]*\)\.?")
# The summary a session worked from: "FL summary #1 …", source "Moderator (…)".
SESSION_TDOC = re.compile(r"\bsummary\b|\bmoderator\b", re.I)
# Consecutive TDoc rows from this many on are a list of contributions. Fewer
# are what the text is about: an incoming LS, an FL summary, mirror CRs.
TDOC_LIST_ROWS = 5
_RICH = ('.//*[local-name()="drawing" or local-name()="pict" '
         'or local-name()="oMath" or local-name()="object"]')


def _tdoc_entry(text: str) -> bool:
    """Remove IDs alone or tabular ID/title[/source] entries, not prose citations.

    Whitespace-only separation is ambiguous: retaining it is safer than silently
    dropping substantive text beginning with a document number.
    """
    lines = text.strip().splitlines()
    return bool(lines) and all(
        TDOC_ID.fullmatch(line.split("\t", 1)[0].strip()) for line in lines
    )


def _tdoc_rows(block) -> int:
    """Number of TDoc rows in a paragraph that holds nothing else, or 0."""
    # An ID caption with an equation/image is content, not a metadata row.
    if ET.QName(block).localname != "p" or block.xpath(_RICH):
        return 0
    text = text_of(block)
    return len(text.strip().splitlines()) if _tdoc_entry(text) else 0


def _listed_tdocs(blocks) -> dict[int, int]:
    """The TDoc lists among one agenda section's blocks: {paragraph index: rows}.

    Each paragraph of a list maps to the number of rows the list folds away.
    Only a list of contributions is folded, never the document the text is
    about. The summaries that close a list stay in view when text follows:
    the chair appends the FL summary to the contributions and writes its
    agreements underneath. A remark under a row goes with it.
    """
    texts = [text_of(block).strip() for block in blocks]
    kinds = []
    for block, text in zip(blocks, texts):
        paragraph = ET.QName(block).localname == "p" and not block.xpath(_RICH)
        if _tdoc_rows(block):
            kind = "row"
        elif paragraph and not text:
            kind = "blank"
        elif (paragraph and kinds and kinds[-1] in {"row", "remark"}
              and TDOC_REMARK.fullmatch(text)):
            kind = "remark"
        else:
            kind = "text"
        kinds.append(kind)
    runs = []
    start = 0
    while start < len(blocks):
        end = start + 1
        if kinds[start] == "row":
            while end < len(blocks) and kinds[end] in {"row", "remark"}:
                end += 1
            runs.append((start, end))
        start = end
    listed = {}
    # Last run first: what comes below a run is then known.
    for start, end in reversed(runs):
        rows = [i for i in range(start, end) if kinds[i] == "row"]
        if sum(_tdoc_rows(blocks[i]) for i in rows) < TDOC_LIST_ROWS:
            continue
        below = end
        while below < len(blocks) and kinds[below] == "blank":
            below += 1
        keep = end
        if below < len(blocks) and below not in listed:  # Not another list.
            for i in reversed(rows):
                if not SESSION_TDOC.search(texts[i]):
                    break
                keep = i
        count = sum(_tdoc_rows(blocks[i]) for i in rows if i < keep)
        listed.update((i, count) for i in range(start, keep))
    return listed


def _without_tdocs(table):
    """Return a filtered copy of a table and the number of TDoc rows left out.

    Only a table that lists TDocs loses rows: every row is an ID followed by
    plain text cells, below at most a TDoc/Title/Source header. A table that
    refers to TDocs (TDoc number | Issue) or has merged/illustrated rows stays
    whole; half of it would misstate the source.
    """
    if ET.QName(table).localname != "tbl":
        return table, 0
    filtered = deepcopy(table)
    removed = 0
    rows = filtered.findall("w:tr", NS)
    entries = []
    for row in rows:
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
            entries.append(row)
    other = [row for row in rows if row not in entries]
    header = False
    if len(other) == 1 and other[0] is rows[0]:
        fields = [text_of(c).strip().lower() for c in other[0].findall("w:tc", NS)]
        header = bool(fields) and fields[0] in {"tdoc", "tdoc number", "document number"} and all(
            f in {"title", "source", "author", "authors", "company", "submitter"}
            for f in fields[1:]
        )
    if entries and (not other or header):
        return None, len(entries)
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


def _struck(t):
    rpr = t.getparent().find("w:rPr", NS)
    if rpr is None:
        return False
    return any(
        e is not None and attr(e) not in {"0", "false", "off", "none"}
        for e in (rpr.find("w:strike", NS), rpr.find("w:dstrike", NS))
    )


def _unit_key(block):
    """Comparison key of one Word paragraph or table, or None if it is blank.

    Built from the Word XML, not the HTML, so parser output changes do not read
    as edits. Whitespace and run splits are ignored; struck-out text is not.
    """
    segments = []
    for e in block.iter():
        name = ET.QName(e).localname
        if name in {"drawing", "pict", "object"}:
            segments.append(("[img]", False))
        elif name == "t":
            text = e.text or ""
            segments.append((text, bool(text.strip()) and _struck(e)))
        elif name in {"tab", "br", "cr"}:
            segments.append((" ", False))
        elif name == "tc":
            segments.append((" | ", False))
    merged = []
    for text, struck in segments:
        if merged and merged[-1][1] == struck:
            merged[-1][0] += text
        else:
            merged.append([text, struck])
    text = "".join(f"\x01{t}\x02" if struck else t for t, struck in merged)
    normalized = " ".join(text.split())
    if not normalized.strip(" |"):
        return None
    return hashlib.sha1(normalized.encode()).hexdigest()[:12]


def _tag_unit(rendered, index):
    """Mark a rendered block's first tag so finish_units can highlight it later."""
    tag = lambda html: re.sub(r"^<(\w+)", rf'<\1 data-unit="{index}"', html, count=1)
    if isinstance(rendered, ListItem):
        rendered.html = tag(rendered.html)
        return rendered
    return tag(rendered)


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
    pending = []  # Blocks of the current section: a list is judged as a whole.
    unassigned = 0

    def finish():
        nonlocal pending, unassigned
        listed = _listed_tdocs(pending)
        pieces = []
        folded = []
        excluded = 0
        for index, block in enumerate(pending):
            if index in listed:
                excluded += bool(_tdoc_rows(block))
                folded.append(converter.block(block))
                if index + 1 not in listed:
                    # The list folds into one line of ours. It is not a unit,
                    # so a late contribution does not read as a change.
                    rows = listed[index]
                    pieces.append((True, (
                        f'<details class="tdoc-list"><summary>{rows} TDoc{"s" if rows > 1 else ""}'
                        f"</summary>{render_blocks(folded)}</details>"
                    ), None))
                    folded = []
                continue
            clean, count = _without_tdocs(block)
            excluded += count
            if clean is None:
                converter.block(block)  # Advance Word counters even for removed rows.
                continue
            rendered = converter.block(clean)
            txt = text_of(block).strip()
            if agenda is None:
                unassigned += bool(txt) and not _tdoc_rows(block)
                continue
            has_content = bool(
                txt or ET.QName(block).localname == "tbl" or clean.xpath(_RICH)
            )
            pieces.append((has_content, rendered, _unit_key(clean) if has_content else None))
        pending = []
        if agenda is None:
            return
        if all(key is None for _, _, key in pieces):
            pieces = []  # Nothing but lists: the panel says so itself.
        # Blank paragraphs retain internal spacing; boundary whitespace is
        # not useful as standalone section content.
        while pieces and not pieces[-1][0]:
            pieces.pop()
        while pieces and not pieces[0][0]:
            pieces.pop(0)
        blocks = sections[agenda]["blocks"]
        parts = []
        for _, part, key in pieces:
            if key is not None:
                part = _tag_unit(part, len(blocks))
                blocks.append(key)
            parts.append(part)
        sections[agenda]["html"] += render_blocks(parts)
        sections[agenda]["excluded_tdoc_rows"] += excluded

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
                        "blocks": [],
                    })
                    converter.block(block)
                    continue
                # Unnumbered subheadings remain part of the current section.
        pending.append(block)
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


def _modified_at(meta):
    """Upload time from Last-Modified (FTP MDTM or HTTP), as UTC ISO, or None."""
    try:
        return parsedate_to_datetime(meta["last_modified"]).astimezone(timezone.utc).isoformat()
    except (KeyError, TypeError, ValueError):
        return None


def fetch_remote_note(cfg, meeting_id, listed=None):
    """Return (info, body) of the meeting's latest remote note, or (None, None).

    Bytes are revalidated even when filename, upload time and timezone are
    unchanged. ``listed`` reuses a lookup the caller already made for this
    meeting, so the folders are not listed twice. Failures propagate so a
    temporary listing/download failure cannot erase a published agreement.
    """
    if not meeting_id:
        return None, None
    info = listed
    if not info or _extract_meeting_id(info.get("name", "")) != meeting_id.lower():
        info = get_latest_chair_notes_info(
            urls=cfg["inbox_urls"],
            extra_folders=cfg["extra_folders"],
            preferred_meeting_id=meeting_id,
            strict=True,
        )
    if info is None:
        return None, None
    if _extract_meeting_id(info["name"]) != meeting_id.lower():
        raise ValueError("Remote chairman note belongs to a different meeting")
    body, meta = remote_files.fetch_file(None, info["url"])
    return {**info, "sha256": meta["sha256"], "modified_at": _modified_at(meta)}, body


def remote_reference(cfg, meeting_id, listed=None):
    info, _ = fetch_remote_note(cfg, meeting_id, listed)
    return info


def _store_note(name, body, directory=Path("downloads/ran1/Chair_notes")):
    """Save fetched bytes; a zip yields the Word document inside it."""
    path = directory / Path(name).name
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(body)
    temp.replace(path)
    if path.suffix.lower() == ".zip":
        extracted = extract_document_from_zip(path, document_extensions=WORD_DOCUMENT_EXTENSIONS)
        if extracted is None:
            raise RuntimeError(f"{name} contains no Word document")
        return extracted
    return path


def track_changes(result, previous, *, changed_at, document):
    """Date each section's last change and highlight the blocks it added.

    Sections compare by block keys, so whitespace, run splits and listed TDocs
    do not count as changes. An unchanged section keeps its earlier change and
    highlights; the first note of a meeting is a baseline with none.

    Another parser version cuts the same note into other blocks. That build
    carries each section's change over instead of reading the parser's
    difference as an edit; a note that changed in the same build is compared
    again from the next one.
    """
    before = (previous or {}).get("sections", {})
    reparsed = previous is not None and previous.get("parser_version") != result.get("parser_version")
    for ai, section in result["sections"].items():
        keys = section["blocks"]
        old = before.get(ai)
        if previous is None:
            change = {"change": "initial", "added": []}
        elif old is None:
            change = {"change": "new", "added": list(range(len(keys)))}
        elif old.get("blocks") is None:
            change = {"change": "initial", "added": []}  # Nothing to compare with.
        elif old.get("blocks") == keys:
            change = {k: old[k] for k in ("change", "added", "changed_at", "changed_in") if k in old}
        elif reparsed:
            change = {k: old[k] for k in ("change", "changed_at", "changed_in") if k in old}
            matcher = SequenceMatcher(None, old.get("blocks") or [], keys, autojunk=False)
            moved = {
                i1 + n: j1 + n
                for op, i1, i2, j1, _ in matcher.get_opcodes()
                if op == "equal"
                for n in range(i2 - i1)
            }
            change["added"] = [moved[i] for i in old.get("added", []) if i in moved]
            if old.get("added") and not change["added"]:
                change["change"] = "initial"  # What it had added is no longer shown.
        else:
            matcher = SequenceMatcher(None, old.get("blocks") or [], keys, autojunk=False)
            added = [
                j
                for op, _, _, j1, j2 in matcher.get_opcodes()
                if op in {"insert", "replace"}
                for j in range(j1, j2)
            ]
            change = {"change": "updated", "added": added}
        change.setdefault("changed_at", changed_at)
        change.setdefault("changed_in", document)
        section.update(change)
        section["html"] = finish_units(section["html"], section["added"])
    # Rebuilding from the same note (a local one has no upload time) keeps its date.
    same_note = previous is not None and previous.get("sha256") == result.get("sha256")
    result["document_changed_at"] = (
        previous.get("document_changed_at", changed_at) if same_note else changed_at
    )
    return result


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


def build_agreements(*, cfg, meeting_id, schedule_path, offline, previous=None,
                     listed=None, now=None):
    """Return (agreements, source identity).

    ``previous`` is what was last published for this meeting: the baseline for
    change tracking, and what stays when the new note cannot be parsed. Its
    identity is still reported, so the check job does not rebuild for the same
    broken document every hour. Listing and download failures propagate; the
    caller keeps the previous identity.
    """
    if not meeting_id:
        return _unavailable(meeting_id), None
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    info = None
    path = local_note_path(cfg, meeting_id, offline=offline, schedule_path=schedule_path)
    if path is None:
        if offline:
            return _unavailable(meeting_id), None
        info, body = fetch_remote_note(cfg, meeting_id, listed)
        if info is None:
            if previous:
                # Replaced or cleared in the sync folder: the caller keeps the
                # published agreements and their identity.
                raise LookupError(f"No chair notes listed for {meeting_id}")
            return _unavailable(meeting_id), None
        path = _store_note(info["name"], body)
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
    # A local note has no upload time; the build that first sees it dates it.
    changed_at = (info or {}).get("modified_at") or now
    track_changes(result, previous, changed_at=changed_at, document=identity["name"])
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
