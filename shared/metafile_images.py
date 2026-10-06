"""Render WMF/EMF with pinned headless Java, or a portable Python fallback.

Version 0.3.0 decodes legacy Symbol bytes as cp1252. Correct only generated
SVG text carrying that exact font, using Adobe's encoding table. Keep vector
positions and strip the inaccurate raster fallback. No private API patches.
"""

from functools import lru_cache
from pathlib import Path
import re

from lxml import etree as ET

SVG_NS = "http://www.w3.org/2000/svg"
SVG_TAGS = set(
    "svg g defs clipPath path rect circle ellipse line polyline polygon text tspan image".split()
)
SVG_ATTRS = set(
    "width height viewBox x y x1 y1 x2 y2 cx cy r rx ry d points fill fill-rule stroke stroke-width stroke-linecap stroke-linejoin stroke-miterlimit stroke-dasharray stroke-dashoffset opacity fill-opacity stroke-opacity transform clip-path id font-family font-size font-weight font-style text-anchor dominant-baseline dx dy textLength lengthAdjust preserveAspectRatio".split()
)
# Unicode 2.0 assigned these glyph pieces to PUA; modern Unicode has names.
PIECES = {
    0xE6: 0x239B,
    0xE7: 0x239C,
    0xE8: 0x239D,
    0xE9: 0x23A1,
    0xEA: 0x23A2,
    0xEB: 0x23A3,
    0xEC: 0x23A7,
    0xED: 0x23A8,
    0xEE: 0x23A9,
    0xEF: 0x23AA,
    0xF4: 0x23AE,
    0xF6: 0x239E,
    0xF7: 0x239F,
    0xF8: 0x23A0,
    0xF9: 0x23A4,
    0xFA: 0x23A5,
    0xFB: 0x23A6,
    0xFC: 0x23AB,
    0xFD: 0x23AC,
    0xFE: 0x23AD,
}


@lru_cache(maxsize=1)
def symbol_map():
    result = {}
    for line in (
        (Path(__file__).parent / "vendor/adobe-symbol.txt").read_text().splitlines()
    ):
        if line and not line.startswith("#"):
            unicode, byte, *_ = line.split()
            result.setdefault(int(byte, 16), chr(int(unicode, 16)))
    result.update({k: chr(v) for k, v in PIECES.items()})
    return result


def sanitize_svg(data, *, legacy_symbol_bytes=True):
    root = ET.fromstring(data, ET.XMLParser(resolve_entities=False, no_network=True))
    # Java's SVG emitter uses simple generated class rules. Inline only allowed
    # presentation properties, then remove CSS so it cannot load external assets.
    rules = {}
    for stylesheet in root.findall("{" + SVG_NS + "}style"):
        for names, declarations in re.findall(
            r"([.\w\s,]+)\{([^{}]*)\}", stylesheet.text or ""
        ):
            for name in names.split(","):
                if re.fullmatch(r"\.[\w-]+", name.strip()):
                    rules[name.strip()[1:]] = declarations
        root.remove(stylesheet)
    for node in list(root.iter()):
        declarations = (
            ";".join(rules.get(c, "") for c in node.get("class", "").split())
            + ";"
            + node.get("style", "")
        )
        for declaration in declarations.split(";"):
            name, separator, value = declaration.partition(":")
            if separator and name.strip() in SVG_ATTRS:
                node.set(name.strip(), value.strip())
        tag = ET.QName(node).localname
        if tag == "metadata":
            node.getparent().remove(node)
            continue
        if ET.QName(node).namespace != SVG_NS or tag not in SVG_TAGS:
            raise ValueError("Unsupported generated SVG element")
        if (
            tag == "text"
            and node.get("font-family", "").split(",")[0].strip().strip('"').lower()
            == "symbol"
        ):
            if legacy_symbol_bytes:
                raw = (node.text or "").encode("cp1252", errors="strict")
                node.text = "".join(symbol_map().get(b, chr(b)) for b in raw)
            node.set("font-family", "STIX Two Math, Cambria Math, DejaVu Sans, serif")
        for name, value in list(node.attrib.items()):
            local = ET.QName(name).localname
            if (
                local == "href"
                and tag == "image"
                and re.fullmatch(r"data:image/png;base64,[A-Za-z0-9+/=\s]+", value)
            ):
                continue
            if (
                local not in SVG_ATTRS
                or ":" in name
                or ("url(" in value and not re.fullmatch(r"url\(#[\w-]+\)", value))
            ):
                del node.attrib[name]
    return ET.tostring(root, encoding="utf-8")


def backend_identity():
    from shared import wmf2svg

    return (
        "wmf2svg-" + wmf2svg.SHA256 if wmf2svg.available() else "metafile-render-0.3.0"
    )


def render_metafile_svg(data):
    from shared import wmf2svg

    fallback_warnings = []
    if wmf2svg.available():
        try:
            svg = sanitize_svg(wmf2svg.convert(data), legacy_symbol_bytes=False)
            return svg, [
                "WMF/EMF converted with wmf2svg 0.10.6; browser font substitution may change spacing."
            ]
        except Exception:
            fallback_warnings.append(
                "wmf2svg failed; used the portable metafile renderer instead."
            )
    from metafile_render import render_metafile

    result = render_metafile(data, output_format="svg", backend="replay", dpi=200)
    warnings = fallback_warnings
    if result.partial:
        warnings.append(
            "A WMF/EMF figure was only partially rendered; verify it against the original document."
        )
    if any(d.code == "font_substituted" for d in result.diagnostics):
        warnings.append(
            "Metafile fonts may be substituted; Symbol characters are mapped to Unicode. Spacing may differ from Word."
        )
    return sanitize_svg(result.data), warnings
