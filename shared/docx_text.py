"""Accepted text of a Word paragraph, shared by the WGs that read DOCX schedules.

Tracked insertions are kept and tracked deletions dropped, as Word shows the
document with changes accepted. Struck-through runs are dropped too: chairs
strike out what is no longer planned (RAN3's legend: "not treated").
"""

from __future__ import annotations

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def struck(run) -> bool:
    props = run.find(W + 'rPr')
    if props is None:
        return False
    for tag in ('strike', 'dstrike'):
        node = props.find(W + tag)
        if node is not None and node.get(W + 'val', 'true') not in ('0', 'false', 'off'):
            return True
    return False


def tracked_deletion(run) -> bool:
    return any(a.tag in (W + 'del', W + 'moveFrom') for a in run.iterancestors())


def deleted(run) -> bool:
    """Not part of the accepted text: a tracked deletion or a struck run."""
    return tracked_deletion(run) or struck(run)


def paragraph_text(element, dropped: list[str] | None = None, *, keep_struck=False) -> str:
    """Accepted text: insertions kept, deletions and struck runs dropped.

    python-docx's .text skips runs inside <w:ins>, so it is not used. Struck
    text is collected in dropped when given, so a WG can say what it left out;
    keep_struck reads it as ordinary text.
    """
    parts, struck_parts = [], []
    for run in element.iter(W + 'r'):
        if tracked_deletion(run):
            continue
        target = struck_parts if struck(run) and not keep_struck else parts
        for node in run:
            if node.tag == W + 't':
                target.append(node.text or '')
            elif node.tag in (W + 'br', W + 'cr'):
                target.append('\n')
            elif node.tag == W + 'tab':
                target.append('\t')
    text = ' '.join(''.join(struck_parts).replace('\xa0', ' ').split())
    if dropped is not None and text:
        dropped.append(text)
    return ''.join(parts)
