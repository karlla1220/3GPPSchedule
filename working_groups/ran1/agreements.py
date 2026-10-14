"""Exact agenda sections from chairman notes, with lists of TDocs folded away.

Labels such as Agreement, Proposal and Potential agreement are ordinary source
content, never extraction boundaries or an assertion that the text was agreed.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
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
    _extract_person_name,
    _local_doc_preference,
    _remote_doc_preference,
    list_inbox_subfolders,
    list_remote_files,
    extract_document_from_zip,
    get_latest_chair_notes_info,
)
from .parser import find_chair_notes_docx

PARSER_VERSION = 21
CACHE_DIR = Path(".cache/ran1/agreements")
SESSION_NOTES_DIR = Path("downloads/ran1/Session_notes")
TDOC_ID = re.compile(r"R1-\d{6,}(?:\s*(?:rev\.?|r)\s*\d+)?", re.I)
# "(Late contribution)", "(Revision of R1-…)", "(Withdrawn)" under a TDoc row.
TDOC_REMARK = re.compile(r"\([^()]*\)\.?")
# The summary a session worked from: "FL summary #1 …", source "Moderator (…)".
SESSION_TDOC = re.compile(r"\bsummary\b|\bmoderator\b", re.I)
# Consecutive TDoc rows from this many on are a list of contributions. Fewer
# are what the text is about: an incoming LS, an FL summary, mirror CRs.
TDOC_LIST_ROWS = 5
# The header of a TDoc: "3GPP TSG RAN WG1 #126bis", "TSG-RAN WG1 Meeting #126-bis".
SESSION_MEETING = re.compile(
    r"\bRAN\s*WG\s*(\d)\b[^#\n]{0,30}#\s*(\d+)\s*-?\s*(bis|b|e)?(?![A-Za-z0-9])", re.I
)
SESSION_NOTE = re.compile(r"Session\s*notes?\s+of\s+AI\s*(\d+(?:\.\d+)*)", re.I)
CHAIR_SOURCE = "chair"
# Text similarity at which two paragraphs are one paragraph reworded.
SIMILAR = 0.9
AGENDA_LINE = re.compile(r"\s*Agenda\s+item\s*:?\s*(\d+(?:\.\d+)*\.?)(?![\d.])", re.I)
# The email discussion a section names: "[126bis-R20-MIMO] Email discussion
# on Rel-20 MIMO – Darcy (MTK)". The bracketed tag is the subject tag of the
# thread on the mailing list; the FLs and their companies follow the dash.
EMAIL_THREAD = re.compile(r"\[([^\[\]]+)\]\s*Email\s+discussion\b", re.I)
EMAIL_THREAD_FL = re.compile(r"[–—]\s*([^–—()]+?)\s*\(([^()]*)\)\s*\.?\s*$")
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


def _email_thread(line):
    """Tag, FLs and companies of an email discussion line, or None."""
    m = EMAIL_THREAD.match(line)
    if not m:
        return None
    line = " ".join(line.split())
    fl = EMAIL_THREAD_FL.search(line)
    names = lambda text: [name.strip() for name in text.split(",") if name.strip()]
    return {
        "tag": " ".join(m[1].split()),
        "line": line,
        "fl": names(fl[1]) if fl else [],
        "companies": names(fl[2]) if fl else [],
    }


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


def _part(rendered, key):
    """One block of a section, kept as data until the section is rendered.

    Sections of two documents are merged block by block, so they are rendered
    only once the merged order is known.
    """
    if isinstance(rendered, ListItem):
        return {"item": asdict(rendered), "key": key}
    return {"html": rendered, "key": key}


def _render(section):
    """Fill a section's html and block keys from its parts."""
    blocks = []
    rendered = []
    for part in section["parts"]:
        block = ListItem(**part["item"]) if "item" in part else part["html"]
        if part["key"] is not None:
            block = _tag_unit(block, len(blocks))
            blocks.append(part["key"])
        rendered.append(block)
    section["html"] = render_blocks(rendered)
    section["blocks"] = blocks
    return section


def _header_fields(lines):
    """Meeting and agenda item a document states on its cover lines.

    A session note is named by its TDoc number and agenda item alone; the
    meeting appears only in the 3GPP header (``3GPP TSG RAN WG1 #126bis``).
    """
    meeting = agenda = None
    for line in lines:
        if meeting is None:
            m = SESSION_MEETING.search(line)
            if m:
                suffix = (m[3] or "").lower()
                meeting = f"ran{m[1]}#{m[2]}{'bis' if suffix == 'b' else suffix}"
        if agenda is None:
            m = AGENDA_LINE.match(line)
            if m:
                agenda = m[1].rstrip(".")
    return {"meeting_id": meeting, "agenda_item": agenda}


def _parse(path: Path) -> dict:
    """Sections of one Word document, cached by its bytes."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    backend = hashlib.sha256(backend_identity().encode()).hexdigest()[:12]
    cache = CACHE_DIR / f"{PARSER_VERSION}-{backend}-{digest}.json"
    if cache.exists():
        try:
            cached = json.loads(cache.read_text())
            if cached["sha256"] == digest and cached["parser_version"] == PARSER_VERSION:
                return cached
        except (ValueError, KeyError, TypeError):
            pass
    converter = DocxHTML(path)
    styles = StyleMap(converter.parts["word/styles.xml"])
    numbers = NumberingMap(converter.parts.get("word/numbering.xml"))
    sections = {}
    agenda = None
    pending = []  # Blocks of the current section: a list is judged as a whole.
    unassigned = 0
    cover = []  # Text above the first agenda heading: the 3GPP header lines.
    threads = []  # Email discussions the sections name, in document order.

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
                if txt and len(cover) < 20:
                    cover.extend(txt.splitlines())
                continue
            has_content = bool(
                txt or ET.QName(block).localname == "tbl" or clean.xpath(_RICH)
            )
            if ET.QName(block).localname == "p":
                for line in txt.splitlines():
                    thread = _email_thread(line.strip())
                    if thread:
                        threads.append({"agenda_item": agenda, **thread})
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
        sections[agenda]["parts"].extend(_part(part, key) for _, part, key in pieces)
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
                        "title": title, "parts": [], "excluded_tdoc_rows": 0,
                    })
                    converter.block(block)
                    continue
                # Unnumbered subheadings remain part of the current section.
        pending.append(block)
    finish()
    for section in sections.values():
        _render(section)
    result = {
        "sha256": digest,
        "parser_version": PARSER_VERSION,
        "sections": sections,
        "warnings": sorted(converter.warnings),
        "unassigned_blocks": unassigned,
        "content_scope": "agenda-section-excluding-tdocs",
        "email_threads": threads,
        "header": _header_fields(cover),
    }
    cache.parent.mkdir(parents=True, exist_ok=True)
    temp = cache.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False))
    temp.replace(cache)
    return result


def parse_agreements(path: Path, meeting_id: str, *, source_name: str | None = None) -> dict:
    """Return portable HTML fragments plus source identity; reject unknown meetings.

    ``source_name`` is the published name when ``path`` was extracted from a
    zip: the inner document is often named without the meeting.
    """
    if not meeting_id or _extract_meeting_id(source_name or path.name) != meeting_id.lower():
        raise ValueError("Chairman note must identify the exact schedule meeting")
    parsed = _parse(path)
    return {
        "status": "ready",
        "meeting_id": meeting_id.lower(),
        "document_file": path.name,
        **{k: v for k, v in parsed.items() if k != "header"},
    }


def _title_key(title):
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


def _rebase(sections, agenda_item, titles):
    """Renumber a session note's sections under the agenda item it covers.

    A session note copies its headings from the agenda but restarts Word's
    numbering, so its 10.5.4.1 reads as 10.5.0.1 and its 9.2 as 8.1. Its first
    heading is the stated agenda item, or the item above or below it whose
    heading in the chairman note has the same title (a note for 10.2.1 opens
    with 10.2). Subsections keep their place relative to it; anything else
    stays under the stated item.
    """
    if not sections:
        return {}
    first = next(iter(sections))
    anchor = agenda_item
    wanted = _title_key(sections[first]["title"])
    for ai, title in titles.items():
        related = (ai == agenda_item or ai.startswith(agenda_item + ".")
                   or agenda_item.startswith(ai + "."))
        if related and _title_key(title) == wanted:
            anchor = ai
            break
    old = first.split(".")
    new = anchor.split(".")
    rebased = {}
    for marker, section in sections.items():
        parts = marker.split(".")
        if parts[:len(old)] == old:
            target = new + parts[len(old):]
        elif anchor != agenda_item and len(parts) >= len(old) and parts[:len(old) - 1] == old[:-1]:
            # A sibling of a subsection anchor: 10.5.0.2 next to 10.5.0.1.
            step = int(parts[len(old) - 1]) - int(old[-1])
            target = new[:-1] + [str(int(new[-1]) + step)] + parts[len(old):]
        else:
            target = agenda_item.split(".")
        ai = ".".join(target)
        if ai in rebased:
            rebased[ai]["parts"] += section["parts"]
            rebased[ai]["excluded_tdoc_rows"] += section["excluded_tdoc_rows"]
        else:
            rebased[ai] = {k: deepcopy(section[k]) for k in ("title", "parts", "excluded_tdoc_rows")}
    return rebased


def session_note_agenda(name):
    m = SESSION_NOTE.search(name)
    return m[1].rstrip(".") if m else None


def session_note_id(name):
    """A session note keeps its TDoc number across versions."""
    m = re.search(r"R1-\d{7}", name)
    return m[0] if m else f"AI {session_note_agenda(name)}"


def parse_session_note(path: Path, meeting_id: str, *, titles=None, source_name=None) -> dict:
    """Sections of a vice-chair's session note, numbered like the chairman note.

    Its file name has no meeting, so the meeting comes from the 3GPP header of
    the document. ``titles`` are the chairman note's section titles by number.
    """
    parsed = _parse(path)
    header = parsed["header"]
    if not meeting_id or header["meeting_id"] != meeting_id.lower():
        raise ValueError(
            f"Session note belongs to {header['meeting_id'] or 'an unknown meeting'}"
        )
    agenda_item = header["agenda_item"] or session_note_agenda(source_name or path.name)
    if not agenda_item:
        raise ValueError("Session note states no agenda item")
    sections = _rebase(parsed["sections"], agenda_item, titles or {})
    return {
        "document_file": path.name,
        "sha256": parsed["sha256"],
        "agenda_item": agenda_item,
        "sections": sections,
        "warnings": parsed["warnings"],
    }


def _html(part):
    return part["item"]["html"] if "item" in part else part["html"]


def _text(part):
    return " ".join(re.sub(r"<[^>]+>", " ", _html(part)).split())


def _token(part):
    """What makes two parts the same block in two documents.

    A folded TDoc list is one block wherever it is: the chairman note adds
    the FL summaries to the session note's list, and two lists would read as
    two. Blank paragraphs differ only in spacing.
    """
    if part["key"] is not None:
        return part["key"]
    return "~tdocs" if _html(part).startswith('<details class="tdoc-list"') else "~blank"


def _same_text(a, b):
    """A paragraph the chair edited while gathering it: same block, new wording."""
    if a["key"] is None or b["key"] is None:
        return False
    matcher = SequenceMatcher(None, _text(a), _text(b), autojunk=False)
    return matcher.real_quick_ratio() >= SIMILAR and matcher.ratio() >= SIMILAR


def _merge_parts(base, extra, *, extra_newer=False):
    """Both documents' blocks in one order, each shared block once.

    The chair gathers the session notes into the chairman note at some point,
    so the two come to hold the same blocks; until then a session note runs
    ahead. Aligned blocks appear once, and a block of either document alone
    stays where it stands, after the base document's blocks at the same place.
    A block one document reworded is one block in the wording of the document
    uploaded later.
    """
    matcher = SequenceMatcher(
        None, [_token(p) for p in base], [_token(p) for p in extra], autojunk=False
    )
    merged = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            # The longer of two TDoc lists has the summaries added since.
            merged += [
                b if _token(a) == "~tdocs" and len(_html(b)) > len(_html(a)) else a
                for a, b in zip(base[i1:i2], extra[j1:j2])
            ]
            continue
        merged += base[i1:i2]
        if op == "delete":
            continue
        unpaired = [a for a in base[i1:i2] if a["key"] is not None]
        added = []
        for b in extra[j1:j2]:
            twin = next((a for a in unpaired if _same_text(a, b)), None)
            if twin is not None:
                unpaired.remove(twin)
                if extra_newer:
                    at = next(i for i, part in enumerate(merged) if part is twin)
                    merged[at] = b
            elif _token(b) == "~tdocs" and any(_token(a) == "~tdocs" for a in base[i1:i2]):
                pass
            else:
                added.append(b)
        if any(b["key"] is not None for b in added):  # Not blank lines alone.
            merged += added
    return merged


def _source_digest(parts):
    keys = [p["key"] for p in parts if p["key"] is not None]
    return hashlib.sha1(json.dumps(keys).encode()).hexdigest()[:12] if keys else None


def _later(a, b):
    """True when upload time ``a`` is after ``b`` (ISO strings, either may be None)."""
    try:
        return datetime.fromisoformat(a) > datetime.fromisoformat(b)
    except (TypeError, ValueError):
        return False


def merge_session_notes(result, notes, *, chair_at=None):
    """Fold session notes into the chairman note's sections.

    Each section records a digest of every document's share of it, so a
    change can be dated by the document that made it. ``chair_at`` and each
    note's ``changed_at`` are upload times: the later wording of a reworded
    block wins.
    """
    latest = {}
    for ai, section in result["sections"].items():
        digest = _source_digest(section.get("parts", []))
        section["sources"] = {CHAIR_SOURCE: digest} if digest else {}
        latest[ai] = chair_at
    for note in notes:
        for ai, extra in note["sections"].items():
            section = result["sections"].get(ai)
            if section is None:
                section = result["sections"][ai] = {
                    "title": extra["title"], "parts": [], "excluded_tdoc_rows": 0, "sources": {},
                }
            newer = _later(note["changed_at"], latest.get(ai))
            section["parts"] = _merge_parts(section["parts"], extra["parts"], extra_newer=newer)
            if newer or latest.get(ai) is None:
                latest[ai] = note["changed_at"]
            section["excluded_tdoc_rows"] = max(
                section["excluded_tdoc_rows"], extra["excluded_tdoc_rows"]
            )
            digest = _source_digest(extra["parts"])
            if digest:
                section["sources"][note["id"]] = digest
            _render(section)
    result["sections"] = dict(sorted(
        result["sections"].items(), key=lambda item: [int(n) for n in item[0].split(".")]
    ))
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


def _changed_by(section, old, sources, same_chair):
    """The document whose share of a section changed, latest first, or None.

    Each section keeps a digest of every document's share. A snapshot from
    before session notes were read has none: the chairman note's share then
    counts as changed only when the note did.
    """
    current = section.get("sources")
    if current is None or not sources:
        return None
    before = (old or {}).get("sources")
    if before is None:
        before = {CHAIR_SOURCE: current.get(CHAIR_SOURCE)} if same_chair and old else {}
    moved = [
        sources[sid] for sid in sorted(set(current) | set(before))
        if current.get(sid) != before.get(sid) and sid in sources
    ]
    return max(moved, key=lambda source: source["changed_at"]) if moved else None


def track_changes(result, previous, *, changed_at, document, sources=None):
    """Date each section's last change and highlight the blocks it added.

    A section is compared as merged from the chairman and session notes, so a
    block that moves from a session note into the chairman note is no change.
    The change is dated by the document whose share of the section changed
    (``sources``: id → name and upload time).

    Sections compare by block keys, so whitespace, run splits and listed TDocs
    do not count as changes. An unchanged section keeps its earlier change and
    highlights; the first note of a meeting is a baseline with none.

    Another parser version cuts the same note into other blocks. That build
    carries each section's change over instead of reading the parser's
    difference as an edit; a note that changed in the same build is compared
    again from the next one.
    """
    before = (previous or {}).get("sections", {})
    # Another parser version, or the first build that reads session notes:
    # what the previous build did not read is no edit.
    introduced = (previous is not None and "session_notes" not in previous
                  and bool(result.get("session_notes")))
    reparsed = previous is not None and (
        previous.get("parser_version") != result.get("parser_version") or introduced
    )
    same_chair = previous is not None and previous.get("sha256") == result.get("sha256")
    for ai, section in result["sections"].items():
        keys = section["blocks"]
        old = before.get(ai)
        if previous is None:
            change = {"change": "initial", "added": []}
        elif old is None and reparsed:
            change = {"change": "initial", "added": []}
        elif (introduced and old.get("blocks") != keys
              and any(sid != CHAIR_SOURCE for sid in section.get("sources", {}))):
            # Now read with its session notes: a baseline as of the latest of them.
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
        if "changed_at" not in change and sources:
            source = None
            if change["change"] in {"new", "updated"}:
                source = _changed_by(section, old, sources, same_chair)
            if source is None:
                # A baseline is as of the latest document that has a share in it.
                source = max(
                    (sources[sid] for sid in section.get("sources", {}) if sid in sources),
                    key=lambda source: source["changed_at"], default=None,
                )
            if source is not None:
                change.update(changed_at=source["changed_at"], changed_in=source["name"])
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


def list_session_notes(cfg):
    """The latest version of each session note in the vice-chairs' folders.

    Session notes sit beside the vice-chairs' schedules (``Hiroki_notes``),
    one file per agenda item, named by TDoc number. The name carries no
    meeting; the document is checked when it is parsed.
    """
    files = []
    for inbox in cfg.get("inbox_urls", []):
        try:
            folders = list_inbox_subfolders(inbox)
        except Exception as exc:
            if remote_files.is_absent(exc):
                continue
            raise
        for folder in folders:
            if _extract_person_name(folder["name"]) is None:
                continue
            try:
                listed = list_remote_files(folder["url"].rstrip("/"))
            except Exception as exc:
                if remote_files.is_absent(exc):
                    continue
                raise
            files += [
                {**f, "folder": folder["name"]} for f in listed
                if SESSION_NOTE.search(f["name"]) and not f["name"].startswith("~$")
            ]
    latest = {}
    for f in files:
        key = session_note_id(f["name"])
        if key not in latest or _remote_doc_preference(f) > _remote_doc_preference(latest[key]):
            latest[key] = f
    return [latest[key] for key in sorted(latest)]


def local_session_notes(directory=SESSION_NOTES_DIR):
    """Downloaded session notes, the latest version of each, for offline builds."""
    latest = {}
    for path in sorted(directory.glob("*/*")) if directory.is_dir() else []:
        if (path.suffix.lower() not in WORD_DOCUMENT_EXTENSIONS
                or not SESSION_NOTE.search(path.name) or path.name.startswith("~$")):
            continue
        key = session_note_id(path.name)
        if key not in latest or _local_doc_preference(path) > _local_doc_preference(latest[key]):
            latest[key] = path
    return [latest[key] for key in sorted(latest)]


def session_note_reference(info):
    """A session note's identity by listing alone: each version is a new name."""
    uploaded = info.get("uploaded_at")
    return {
        "name": info["name"],
        "url": info["url"],
        "uploaded_at": uploaded.isoformat() if isinstance(uploaded, datetime) else uploaded,
    }


def session_note_references(cfg):
    return [session_note_reference(info) for info in list_session_notes(cfg)]


def _identity(chair, notes):
    """The documents a build read. Without session notes, the chairman note's alone."""
    if chair is None and not notes:
        return None
    identity = dict(chair or {})
    if notes:
        identity["session_notes"] = notes
    return identity


def agreement_reference(cfg, meeting_id, listed=None):
    """What the check job compares: (chairman note identity, all documents)."""
    chair = local_note_reference(cfg, meeting_id)
    if chair is None:
        chair = reference_identity(remote_reference(cfg, meeting_id, listed))
    return chair, _identity(chair, session_note_references(cfg))


def _session_notes(cfg, meeting_id, *, offline, titles, now):
    """Parsed session notes of the meeting and their identities.

    A note of another meeting is left out. One that cannot be read is
    reported among the failed ids; the others still count.
    """
    notes, references, warnings, failed = [], [], [], []
    if offline:
        found = [(path, None, None) for path in local_session_notes()]
    else:
        found = []
        for info in list_session_notes(cfg):
            references.append(session_note_reference(info))
            body, meta = remote_files.fetch_file(None, info["url"])
            path = _store_note(info["name"], body, SESSION_NOTES_DIR / info["folder"])
            found.append((path, info, _modified_at(meta)))
    for path, info, modified_at in found:
        name = info["name"] if info else path.name
        if info is None:
            references.append({
                "origin": "local", "name": name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
        try:
            note = parse_session_note(path, meeting_id, titles=titles, source_name=name)
        except Exception as exc:
            print(f"Warning: session note {name} left out: {exc}")
            if "belongs to" not in str(exc):
                warnings.append(f"Session note {name} could not be parsed: {exc}")
                failed.append(session_note_id(name))
            continue
        note.update(id=session_note_id(name), name=name, changed_at=modified_at or now)
        if info:
            note["source_url"] = info["url"]
        notes.append(note)
    return notes, references, warnings, failed


def build_agreements(*, cfg, meeting_id, schedule_path, offline, previous=None,
                     listed=None, now=None):
    """Return (agreements, source identity).

    The chairman note is read with the vice-chairs' session notes: until the
    chair gathers them at the end of the meeting, a session note holds what
    its sessions agreed. Each agenda item shows both documents merged, and
    changes are found in the merged text.

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
    identity = None
    path = local_note_path(cfg, meeting_id, offline=offline, schedule_path=schedule_path)
    if path is None and not offline:
        info, body = fetch_remote_note(cfg, meeting_id, listed)
        if info is None and previous:
            # Replaced or cleared in the sync folder: the caller keeps the
            # published agreements and their identity.
            raise LookupError(f"No chair notes listed for {meeting_id}")
        if info is not None:
            path = _store_note(info["name"], body)
            identity = reference_identity(info)
    elif path is not None:
        identity = {
            "origin": "local",
            "name": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    result = None
    failure = None
    if path is not None:
        try:
            result = parse_agreements(
                path, meeting_id, source_name=info["name"] if info else None
            )
        except Exception as exc:
            failure = f"Chairman note {identity['name']} could not be parsed: {exc}"
            print(f"Warning: {failure}")
    titles = {ai: s["title"] for ai, s in (result or {}).get("sections", {}).items()}
    notes, references, warnings, failed = _session_notes(
        cfg, meeting_id, offline=offline, titles=titles, now=now
    )
    identity = _identity(identity, references)
    if failure:
        return previous or _unavailable(meeting_id, failure), identity
    if result is None:
        if not notes:
            return _unavailable(meeting_id), identity
        result = {
            "status": "ready", "meeting_id": meeting_id.lower(), "parser_version": PARSER_VERSION,
            "sections": {}, "warnings": [], "unassigned_blocks": 0,
            "content_scope": "agenda-section-excluding-tdocs", "email_threads": [],
        }
    if info:
        result["source_url"] = info["url"]
        result["source_name"] = info["name"]
        warnings += info.get("source_warnings", [])
    result["warnings"] = sorted(set(result["warnings"] + warnings))
    # A local note has no upload time; the build that first sees it dates it.
    changed_at = (info or {}).get("modified_at") or now
    sources = {}
    if path is not None:
        sources[CHAIR_SOURCE] = {"name": identity["name"], "changed_at": changed_at}
    for note in notes:
        sources[note["id"]] = {"name": note["name"], "changed_at": note["changed_at"]}
        result["warnings"] = sorted(set(result["warnings"] + note["warnings"]))
    merge_session_notes(result, notes, chair_at=changed_at if path is not None else None)
    result["session_notes"] = {
        note["id"]: {
            "name": note["name"], "agenda_item": note["agenda_item"],
            "changed_at": note["changed_at"], "sha256": note["sha256"],
            **({"source_url": note["source_url"]} if "source_url" in note else {}),
        }
        for note in notes
    }
    track_changes(
        result, previous, changed_at=changed_at,
        document=identity["name"] if path is not None else None, sources=sources,
    )
    for section in result["sections"].values():
        section.pop("parts", None)
    # A session note that cannot be read leaves its items as last published,
    # rather than without what its sessions agreed, until it can be read again.
    for ai, section in (previous or {}).get("sections", {}).items():
        if any(sid in failed for sid in section.get("sources", {})):
            result["sections"][ai] = section
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
