"""TDoc conclusions under each agenda item, for the panel below the grid (README "TDoc 결론 패널").

The chair file ends in one TDoc table: a heading row per agenda item (the
same headings that give the agenda), then a row per TDoc (Doc Type, Tdoc
number, Source, Title, Conclusion, Comments) shaded by its conclusion, and
the odd row of prose across the table. Each item's rows become one table in
the RAN1 agreement panel's format (shared/agreement_assets.py), at Word's
column widths. The conclusion shading is kept, softened like the panel's
Word highlights. Rows new or changed since the last build of the same meeting
are marked as added, as RAN1 marks new chair-note text.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import io
import json
import re

from lxml import etree

from shared.docx_html import DocxHTML, NS
from shared.docx_text import paragraph_text
from .document import headings, squash

W = '{%s}' % NS['w']
VERSION = 1
# What the panel says, in place of RAN1's chair-note wording (templates/agreements-panel.js).
TEXT = {
    'heading': 'TDoc conclusions for',
    'select': 'Select a schedule cell to read its TDoc conclusions here.',
    'unavailable': 'No TDoc table is available for this meeting.',
    'loading': 'Loading TDoc conclusions…',
    'error': 'Could not load these TDoc conclusions.',
    'missing': 'is not a heading in the chair file.',
    'subsections': 'No TDoc is listed directly under this item; see its subsections.',
    'empty': 'No TDoc is listed under this item yet.',
    'updated': 'Rows changed in the latest chair file',
}
# Word's strong fills as the panel softens Word highlights (shared/docx_html.py); others are mixed with white.
SOFT = {'00FF00': 'c6f0bd', '00FFFF': 'bdeef3', 'FFFF00': 'fff0a0', 'FF00FF': 'f5c8ee'}
FILL = re.compile(r'background:#([0-9a-fA-F]{6})')
# The chair links each TDoc to the copy on their own disk ("file:///C:\\TSGS1_115_Prague\\Docs\\S1-263194.zip");
# the same file is in the meeting folder of the 3GPP archive.
LOCAL_TDOC = re.compile(r'(TSGS1_[^\\/]+)[\\/]Docs[\\/](S1-\d+\.zip)$', re.I)
ARCHIVE = 'https://www.3gpp.org/ftp/tsg_sa/WG1_Serv/'


def soften(fill: str) -> str:
    fill = fill.upper()
    if fill in SOFT:
        return SOFT[fill]
    channels = [int(fill[i:i + 2], 16) for i in (0, 2, 4)]
    if min(channels) >= 0xC0:
        return fill.lower()   # already a light fill ("CCFFCC" Endorsed)
    return ''.join(f'{round(255 - (255 - c) * 0.3):02x}' for c in channels)


def _cells(row) -> list[str]:
    return [squash(' '.join(paragraph_text(p) for p in cell.iter(W + 'p'))) for cell in row.findall(W + 'tc')]


def _key(row) -> str:
    """A row as compared between builds: its text and shading."""
    fills = [cell.find(f'{W}tcPr/{W}shd') for cell in row.findall(W + 'tc')]
    value = [_cells(row), [f.get(W + 'fill') if f is not None else None for f in fills]]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()[:16]


def _header(body) -> list[str] | None:
    """The column names of the legend table above the schedule ("Doc Type", "Tdoc number", ...)."""
    for table in body.iter(W + 'tbl'):
        rows = table.findall(W + 'tr')
        if rows and any(re.fullmatch(r'Tdoc\s*number', text, re.I) for text in _cells(rows[0])):
            return _cells(rows[0])
    return None


def _table_html(renderer: DocxHTML, table, rows, header) -> str:
    part = etree.Element(table.tag)
    for child in table:
        if child.tag in (W + 'tblPr', W + 'tblGrid'):
            part.append(copy.deepcopy(child))
    for row in rows:
        part.append(copy.deepcopy(row))
    html = renderer.table(part)
    html = FILL.sub(lambda m: f'background:#{soften(m[1])}', html)
    # Each TDoc row is one unit the change marks; the bar sits on its first cell.
    count = iter(range(len(rows)))
    html = re.sub(r'<tr><td ', lambda m: f'<tr><td data-unit="{next(count)}" ', html)
    if header and len(header) == len(part.findall(f'{W}tblGrid/{W}gridCol')):
        head = '<tr>' + ''.join(f'<th>{text}</th>' for text in map(_escape, header)) + '</tr>'
        html = html.replace('</colgroup>', '</colgroup>' + head, 1)
    return html


def _escape(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def read_sections(data: bytes, archive: str = ARCHIVE) -> dict[str, dict]:
    """Agenda number -> {'title', 'html' (when it lists rows), 'blocks' (row keys)}."""
    renderer = DocxHTML(io.BytesIO(data))
    for rel in renderer.rels.values():
        match = LOCAL_TDOC.search(rel.get('Target') or '')
        if match:
            rel.set('Target', f'{archive}{match[1]}/Docs/{match[2]}')
    body = renderer.body
    found_headings = headings(body)   # held, so each paragraph keeps its element object
    marks = {paragraph: (number, title) for paragraph, number, title in found_headings}
    header = _header(body)
    sections = {number: {'title': title, 'blocks': []} for number, title in marks.values()}
    for table in body.findall(W + 'tbl'):
        current, rows = None, {}
        for row in table.findall(W + 'tr'):
            found = [marks[p] for p in row.iter(W + 'p') if p in marks]
            if found:
                current = found[-1][0]
                continue
            if current is not None and any(_cells(row)):
                rows.setdefault(current, []).append(row)
        for number, listed in rows.items():
            sections[number]['blocks'] = [_key(row) for row in listed]
            sections[number]['html'] = _table_html(renderer, table, listed, header)
    return sections


def compare(sections: dict, previous: dict | None, name: str, changed_at: str | None) -> None:
    """Mark each section new, updated or as before, against the last build of the same meeting."""
    before = (previous or {}).get('sections', {})
    for number, section in sections.items():
        keys, old = section['blocks'], before.get(number)
        if previous is None:
            change = {'change': 'initial', 'added': []}
        elif old is None or old.get('blocks') is None:
            change = {'change': 'new', 'added': list(range(len(keys)))} if keys else {'change': 'initial', 'added': []}
        elif old['blocks'] == keys:
            change = {k: old[k] for k in ('change', 'added', 'changed_at', 'changed_in') if k in old}
        else:
            matcher = SequenceMatcher(None, old['blocks'], keys, autojunk=False)
            added = [j for op, _, _, j1, j2 in matcher.get_opcodes() if op in {'insert', 'replace'}
                     for j in range(j1, j2)]
            change = {'change': 'updated', 'added': added}
        change.setdefault('added', [])
        if changed_at:
            change.setdefault('changed_at', changed_at)
        change.setdefault('changed_in', name)
        section.update(change)


def uploaded(value: str | None) -> str | None:
    """The listing's "2026-08-28 13:52" (UTC) as ISO, or None for a local file."""
    try:
        return datetime.strptime(value or '', '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def build(data: bytes, file: dict, meeting_id: str, previous: dict | None, archive: str = ARCHIVE) -> dict:
    """The schedule's ``chairman_agreements``: TDoc conclusions by agenda item.

    ``file`` is the chair file's manifest entry (name, sha256, uploaded_at).
    ``previous`` is what the last build published for this meeting, or None.
    """
    sections = read_sections(data, archive)
    if not any(section.get('html') for section in sections.values()):
        raise ValueError('the chair file has no TDoc rows under its agenda headings')
    changed_at = uploaded(file.get('uploaded_at'))
    if previous is not None and previous.get('parser_version') != VERSION:
        previous = None   # another reading of the same table is no edit
    same = previous is not None and previous.get('sha256') == file.get('sha256')
    compare(sections, previous, file['name'], changed_at)
    return {'wg_id': 'sa1', 'status': 'ready', 'meeting_id': meeting_id, 'parser_version': VERSION, 'kind': 'tdoc-conclusions',
            'text': TEXT, 'source_name': file['name'], 'document_file': file['name'],
            'sha256': file.get('sha256', ''),
            'document_changed_at': previous.get('document_changed_at', changed_at) if same else changed_at,
            'sections': sections}
