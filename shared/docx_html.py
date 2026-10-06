"""Allowlisted OOXML → HTML, without executing fields, macros or relationships.

Fragments are sanitized and displayed in an isolated Shadow DOM. This is a
flow-layout reader, not Word pagination. Unsupported objects are called out.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from html import escape
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit
from zipfile import ZipFile

from lxml import etree as ET

from shared.docx_math import render_math
from shared.metafile_images import render_metafile_svg

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
}


def attr(node, name="val", default=None):
    return (
        node.get("{%s}%s" % (NS["w"], name), default) if node is not None else default
    )


def val(node, path, default=None):
    return attr(node.find(path, NS), default=default) if node is not None else default


def xml(data):
    return ET.fromstring(data, ET.XMLParser(resolve_entities=False, no_network=True))


def safe_url(url):
    return bool(
        url
        and not re.search(r"[\x00-\x20]", url)
        and urlsplit(url).scheme.lower() in {"http", "https"}
        and urlsplit(url).netloc
    )


def text_of(node):
    parts = []
    for e in node.iter():
        tag = ET.QName(e).localname
        if tag == "t":
            parts.append(e.text or "")
        elif tag == "tab":
            parts.append("\t")
        elif tag in {"br", "cr"}:
            parts.append("\n")
    return "".join(parts)


@dataclass
class ListItem:
    depth: int
    kind: str
    number: int
    html: str
    indent_twips: int | None = None


def render_blocks(blocks):
    """Group Word paragraphs into nested GFM-style lists, including in table cells.

    Paragraph indentation determines relative nesting, not sparse Word ilvl IDs.
    One-point differences are treated as alignment noise. Normal blocks reset
    the indent stack; explicit li values preserve ordered-list continuity.
    """
    output, roots, stack, indents = [], [], [], []

    def emit(group):
        rows = []
        for item in group["items"]:
            attrs = (
                ' class="list-gap"'
                if item["gap"]
                else (f' value="{item["number"]}"' if group["kind"] == "ol" else "")
            )
            rows.append(
                "<li"
                + attrs
                + ">"
                + item["html"]
                + "".join(emit(child) for child in item["children"])
                + "</li>"
            )
        kind = group["kind"]
        start = (
            f' start="{group["items"][0]["number"]}"'
            if kind == "ol" and group["items"] and not group["items"][0]["gap"]
            else ""
        )
        return "<" + kind + start + ">" + "".join(rows) + "</" + kind + ">"

    def flush():
        output.extend(emit(root) for root in roots)
        roots.clear()
        stack.clear()
        indents.clear()

    for block in blocks:
        if not isinstance(block, ListItem):
            flush()
            output.append(block)
            continue
        indent = block.indent_twips if block.indent_twips is not None else block.depth * 720
        while len(indents) > 1 and indent < indents[-1] - 20:
            indents.pop()
        if not indents or indent > indents[-1] + 20:
            indents.append(indent)
        elif indent < indents[-1] - 20:
            indents[-1] = indent
        depth = len(indents) - 1
        del stack[depth + 1 :]
        if len(stack) == depth + 1 and stack[-1]["kind"] != block.kind:
            stack.pop()
        while len(stack) <= depth:
            group = {"kind": block.kind if len(stack) == depth else "ul", "items": []}
            if stack:
                if not stack[-1]["items"]:
                    stack[-1]["items"].append(
                        {"html": "", "number": 1, "gap": True, "children": []}
                    )
                stack[-1]["items"][-1]["children"].append(group)
            else:
                roots.append(group)
            stack.append(group)
        stack[-1]["items"].append(
            {"html": block.html, "number": block.number, "gap": False, "children": []}
        )
    flush()
    return "".join(output)


_KEPT_FONTS = re.compile(r"symbol|wingdings|webdings|math|courier|consolas|mono", re.I)


class DocxHTML:
    def __init__(self, path):
        with ZipFile(path) as z:
            self.parts = {n: z.read(n) for n in z.namelist() if n.startswith("word/")}
        self.body = xml(self.parts["word/document.xml"]).find("w:body", NS)
        self.styles_root = xml(self.parts["word/styles.xml"])
        self.styles = {
            attr(s, "styleId"): s for s in self.styles_root.findall("w:style", NS)
        }
        self.numbering = xml(self.parts.get("word/numbering.xml", b"<numbering/>"))
        self.rels = {}
        if "word/_rels/document.xml.rels" in self.parts:
            self.rels = {
                r.get("Id"): r for r in xml(self.parts["word/_rels/document.xml.rels"])
            }
        self.counters = {}
        self.warnings = set()

    def style_chain(self, sid):
        chain, seen = [], set()
        while sid in self.styles and sid not in seen:
            seen.add(sid)
            s = self.styles[sid]
            chain.insert(0, s)
            sid = val(s, "w:basedOn")
        return chain

    def props(self, node, kind, paragraph=None):
        defaults = self.styles_root.find(
            "w:docDefaults/w:%sDefault/w:%s" % (kind, kind), NS
        )
        layers = [defaults] if defaults is not None else []
        if paragraph is not None:
            sid = val(paragraph, "w:pPr/w:pStyle", "Normal")
            layers += [s.find("w:" + kind, NS) for s in self.style_chain(sid)]
        sid = val(node, "w:%s/w:%sStyle" % (kind, "p" if kind == "pPr" else "r"))
        layers += [s.find("w:" + kind, NS) for s in self.style_chain(sid)]
        layers.append(node.find("w:" + kind, NS))
        result = {}
        for layer in layers:
            if layer is not None:
                for e in layer:
                    name = ET.QName(e).localname
                    values = {ET.QName(k).localname: v for k, v in e.attrib.items()}
                    resolved = result.setdefault(name, {})
                    if name == "ind":
                        if "firstLine" in values or "firstLineChars" in values:
                            resolved.pop("hanging", None)
                            resolved.pop("hangingChars", None)
                        if "hanging" in values or "hangingChars" in values:
                            resolved.pop("firstLine", None)
                            resolved.pop("firstLineChars", None)
                    resolved.update(values)
                    if not e.attrib:
                        result[name] = {"val": "1"}
        return result

    def run(self, r, p):
        props = self.props(r, "rPr", p)
        css = []
        enabled = lambda k: (
            k in props and props[k].get("val") not in {"0", "false", "off", "none"}
        )
        if "b" in props:
            css.append("font-weight:" + ("bold" if enabled("b") else "normal"))
        if "i" in props:
            css.append("font-style:" + ("italic" if enabled("i") else "normal"))
        decorations = []
        if enabled("u"):
            decorations.append("underline")
        if enabled("strike") or enabled("dstrike"):
            decorations.append("line-through")
        if decorations:
            css.append("text-decoration:" + " ".join(decorations))
        color = props.get("color", {}).get("val", "")
        if re.fullmatch("[0-9a-fA-F]{6}", color):
            css.append("color:#" + color)
        # Sizes are relative to Word's 11pt body, so the page sets the scale.
        sz = props.get("sz", {}).get("val", "")
        if sz.isdigit():
            css.append(f"font-size:{min(int(sz), 144) / 22:.3g}em")
        # Body faces follow the page; only faces that change meaning stay.
        font = props.get("rFonts", {}).get("ascii", "")
        if font and re.fullmatch(r"[\w -]+", font) and _KEPT_FONTS.search(font):
            css.append(f"font-family:'{font}'")
        highlight = props.get("highlight", {}).get("val", "")
        # Word's light highlights, softened but the same hue.
        highlights = {
            "green": "#c6f0bd",
            "yellow": "#fff0a0",
            "cyan": "#bdeef3",
            "magenta": "#f5c8ee",
            "red": "#ff0000",
            "blue": "#0000ff",
            "darkYellow": "#808000",
            "lightGray": "#c0c0c0",
        }
        if highlight in highlights:
            css.append("background-color:" + highlights[highlight])
        fill = props.get("shd", {}).get("fill", "")
        if re.fullmatch("[0-9a-fA-F]{6}", fill):
            css.append("background-color:#" + fill)
        content = self.inline(r, p)
        vert = props.get("vertAlign", {}).get("val")
        if vert in {"subscript", "superscript"}:
            tag = "sub" if vert == "subscript" else "sup"
            content = f"<{tag}>{content}</{tag}>"
        return f'<span style="{escape(";".join(css), quote=True)}">{content}</span>'

    def inline(self, node, p):
        result = []
        for e in node:
            tag = ET.QName(e).localname
            if tag in {"t", "delText"}:
                result.append(escape(e.text or ""))
            elif tag == "r":
                result.append(self.run(e, p))
            elif tag in {"br", "cr"}:
                result.append("<br>")
            elif tag == "tab":
                result.append("&#9;")
            elif tag == "hyperlink":
                content = self.inline(e, p)
                rel = self.rels.get(e.get("{%s}id" % NS["r"]))
                url = rel.get("Target") if rel is not None else None
                result.append(
                    f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{content}</a>'
                    if safe_url(url)
                    else content
                )
            elif tag in {"drawing", "pict", "object"}:
                if tag == "object":
                    self.warnings.add("Embedded OLE objects are shown using their static image previews.")
                result.append(self.image(e))
            elif tag in {"oMath", "oMathPara"}:
                result.append(render_math(e, self.warnings))
            elif tag in {"ins", "del"}:
                self.warnings.add(
                    "Tracked insertions/deletions are shown inline; Word revision balloons are not reproduced."
                )
                result.append(f"<{tag}>" + self.inline(e, p) + f"</{tag}>")
            elif tag in {"sdt", "sdtContent", "smartTag", "customXml"}:
                result.append(self.inline(e, p))
            elif tag in {"altChunk", "sym"}:
                self.warnings.add(
                    "An embedded object or legacy symbol requires the source document."
                )
                result.append("<span>[Object: see source document]</span>")
        return "".join(result)

    def image(self, node):
        images = []
        width = None
        for child in node.iter():
            if ET.QName(child).localname == "extent" and child.get("cx", "").isdigit():
                width = int(child.get("cx")) / 9525
                break
        if width is None:
            for shape in node.iter():
                if ET.QName(shape).localname != "shape":
                    continue
                match = re.search(r"(?:^|;)\s*width:\s*(\d+(?:\.\d+)?)(pt|px|in)\s*(?:;|$)", shape.get("style", ""))
                if match:
                    width = float(match[1]) * {"pt": 96 / 72, "px": 1, "in": 96}[match[2]]
                    break
        dimensions = (
            f' style="width:{width:g}px;max-width:100%;height:auto"' if width else ""
        )
        for e in node.iter():
            rid = e.get("{%s}embed" % NS["r"]) or e.get("{%s}id" % NS["r"])
            rel = self.rels.get(rid)
            if rel is None or rel.get("TargetMode") == "External":
                continue
            target = PurePosixPath("word") / rel.get("Target", "")
            if ".." in target.parts:
                continue
            data = self.parts.get(str(target))
            ext = target.suffix.lower()
            mime = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
            }.get(ext)
            if data and ext in {".wmf", ".emf"}:
                try:
                    data, notices = render_metafile_svg(data)
                    self.warnings.update(notices)
                    mime = "image/svg+xml"
                except Exception:
                    self.warnings.add(
                        "A WMF/EMF figure could not be converted; see source document."
                    )
            if data and mime:
                images.append(
                    f'<img alt="Chairman note figure"{dimensions} src="data:{mime};base64,{base64.b64encode(data).decode()}">'
                )
        if not images:
            self.warnings.add(
                "An image/shape is unsupported or external; see source document."
            )
            return "[Image/shape: see source document]"
        return "".join(images)

    def list_info(self, p):
        ppr = p.find("w:pPr", NS)
        sid = val(p, "w:pPr/w:pStyle")
        numpr = ppr.find("w:numPr", NS) if ppr is not None else None
        if numpr is None:
            for s in reversed(self.style_chain(sid)):
                numpr = s.find("w:pPr/w:numPr", NS)
                if numpr is not None:
                    break
        nid = val(numpr, "w:numId")
        level = int(val(numpr, "w:ilvl", "0"))
        num = self.numbering.find(f'w:num[@w:numId="{nid}"]', NS)
        aid = val(num, "w:abstractNumId")
        lvl = self.numbering.find(
            f'w:abstractNum[@w:abstractNumId="{aid}"]/w:lvl[@w:ilvl="{level}"]', NS
        )
        if lvl is None or nid == "0":
            return "", None
        counters = self.counters.setdefault(nid, {})
        start = int(
            val(
                num,
                f'w:lvlOverride[@w:ilvl="{level}"]/w:startOverride',
                val(lvl, "w:start", "1"),
            )
        )
        counters[level] = counters.get(level, start - 1) + 1
        for k in list(counters):
            if k > level:
                del counters[k]
        return ListItem(
            level,
            "ul" if val(lvl, "w:numFmt") == "bullet" else "ol",
            counters[level],
            "",
        ), lvl

    def paragraph(self, p):
        props = self.props(p, "pPr", p)
        item, lvl = self.list_info(p)
        css = []
        ind = (
            dict(lvl.find("w:pPr/w:ind", NS).attrib)
            if lvl is not None and lvl.find("w:pPr/w:ind", NS) is not None
            else {}
        )
        ind = {ET.QName(k).localname: v for k, v in ind.items()}
        numbering_ind = ind.copy()
        ind.update(props.get("ind", {}))
        if item:
            # GFM list hierarchy uses a direct paragraph position first. When
            # absent, use the selected numbering level's position before the
            # shared paragraph style. E.g. #126's List Paragraph style gives
            # *every* level left=840; treating that common offset as nesting
            # flattens ilvl 0/1/2 despite numbering indents 720/1440/2160.
            # This is a list-normalization rule, not Word's general cascade.
            direct = p.find("w:pPr/w:ind", NS)
            direct_ind = (
                {ET.QName(k).localname: v for k, v in direct.attrib.items()}
                if direct is not None else {}
            )
            for source in (direct_ind, numbering_ind, props.get("ind", {})):
                left = source.get("start", source.get("left", ""))
                if re.fullmatch(r"-?\d+", left):
                    item.indent_twips = int(left)
                    break
            ind = {}
        for key, prop in [
            ("left", "margin-left"),
            ("right", "margin-right"),
            ("firstLine", "text-indent"),
        ]:
            if re.fullmatch(r"-?\d+", ind.get(key, "")):
                css.append(f"{prop}:{int(ind[key]) / 20:g}pt")
        if "hanging" in ind and ind["hanging"].isdigit():
            css.append(f"text-indent:{-int(ind['hanging']) / 20:g}pt")
        for key, prop in [("before", "margin-top"), ("after", "margin-bottom")]:
            value = props.get("spacing", {}).get(key, "")
            if value.isdigit():
                css.append(f"{prop}:{int(value) / 20:g}pt")
        align = props.get("jc", {}).get("val")
        if item:
            align = "left"
        if align in {"left", "right", "center", "both"}:
            css.append("text-align:" + ("justify" if align == "both" else align))
        paragraph = (
            f'<p style="{escape(";".join(css), quote=True)}">'
            + self.inline(p, p)
            + "</p>"
        )
        if item:
            item.html = paragraph
            return item
        return paragraph

    def table(self, t):
        rows = []
        active = {}
        for tr in t.findall("w:tr", NS):
            cells = []
            col = int(val(tr, "w:trPr/w:gridBefore", "0"))
            for tc in tr.findall("w:tc", NS):
                span = int(val(tc, "w:tcPr/w:gridSpan", "1"))
                merge = tc.find("w:tcPr/w:vMerge", NS)
                if merge is not None and attr(merge) != "restart" and col in active:
                    active[col]["rows"] += 1
                else:
                    cell = {
                        "html": render_blocks(
                            self.block(e)
                            for e in tc
                            if ET.QName(e).localname in {"p", "tbl"}
                        ),
                        "span": span,
                        "rows": 1,
                        "fill": val(tc, "w:tcPr/w:shd", ""),
                    }
                    shade = tc.find("w:tcPr/w:shd", NS)
                    cell["fill"] = attr(shade, "fill", "")
                    cells.append(cell)
                    if merge is not None:
                        active[col] = cell
                    else:
                        active.pop(col, None)
                col += span
            rows.append(cells)

        def cell_html(c):
            shade = (
                f' style="background:#{c["fill"]}"'
                if re.fullmatch("[0-9a-fA-F]{6}", c["fill"])
                else ""
            )
            return f'<td colspan="{c["span"]}" rowspan="{c["rows"]}"{shade}>{c["html"]}</td>'

        return (
            '<div class="table-scroll"><table>'
            + "".join(
                "<tr>" + "".join(cell_html(c) for c in row) + "</tr>" for row in rows
            )
            + "</table></div>"
        )

    def block(self, e):
        tag = ET.QName(e).localname
        if tag == "p":
            return self.paragraph(e)
        if tag == "tbl":
            return self.table(e)
        return ""


DOCUMENT_CSS = """:host{display:block;min-width:0}.docx-document{font-family:inherit;font-size:14px;color:var(--ink,#0f172a);line-height:1.55;overflow-wrap:anywhere}ul,ol{margin:.4em 0;padding-left:1.6em}ul{list-style-type:disc}ul ul{list-style-type:circle}ul ul ul{list-style-type:square}ol{list-style-type:decimal}li>ul,li>ol{margin-top:0;margin-bottom:0}li+li{margin-top:.2em}li>p{margin:0!important}.list-gap{list-style-type:none}li::marker{color:var(--ink-3,#8a94a6)}p{white-space:pre-wrap;margin:0 0 .6em;min-height:.3em}img{max-width:100%;height:auto}table{border-collapse:collapse;width:100%;font-size:.93em}td,th{border:1px solid var(--hairline,#e7e9ee);padding:6px 8px;vertical-align:top}th{background:var(--break-bg,#f8f9fb);font-weight:600}.table-scroll{margin:12px 0}article{border-top:1px solid var(--hairline,#e7e9ee);padding-top:16px;margin-top:20px}article:first-child{border-top:0;padding-top:0;margin-top:0}math{font-size:1.1em}.equation-block{margin:8px 0}.agreement-added{background:var(--added-tint,#eaf6ef);box-shadow:inset 3px 0 0 var(--added,#2f9e62);padding-left:9px;border-radius:2px}a{color:#275d8c;text-decoration-color:#93adc4;text-underline-offset:2px}a:hover{color:#173f65;text-decoration-color:currentColor}@media(max-width:600px){.docx-document{font-size:13.5px}table,tbody,thead,tr,td,th{display:block;width:auto}tr{margin:12px 0}td,th{min-width:0}td+td,th+th{border-top:0}}"""
