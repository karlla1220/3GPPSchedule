"""Convert OMML to static MathML using the pinned MarkItDown math subset.

No browser scripts or external resources are needed. Convert one equation at a
 time so a malformed equation cannot silently remove the rest of a document.
"""

from html import escape

from latex2mathml.converter import convert
from lxml import etree as ET

from shared.vendor.markitdown_math.omml import oMath2Latex

MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
MATHML_NS = "http://www.w3.org/1998/Math/MathML"
TAGS = set(
    "math mrow mi mn mo mtext mspace ms mfrac msqrt mroot mstyle merror mpadded mphantom mfenced menclose msub msup msubsup munder mover munderover mmultiscripts mprescripts none mtable mtr mtd mlabeledtr".split()
)
ATTRS = set(
    "display mathvariant mathsize mathcolor mathbackground stretchy symmetric fence separator form largeop movablelimits accent accentunder lspace rspace minsize maxsize scriptlevel displaystyle linethickness bevelled width height depth rowspacing columnspacing columnalign rowalign columnlines rowlines frame framespacing equalrows equalcolumns rowspan columnspan notation open close separators".split()
)


def to_mathml(element):
    latex = oMath2Latex(element).latex
    if not latex.strip():
        if element.findall(".//{%s}t" % MATH_NS):
            raise ValueError("Equation converted to empty text")
        return ""
    root = ET.fromstring(
        convert(latex).encode(), ET.XMLParser(resolve_entities=False, no_network=True)
    )
    for node in root.iter():
        name = ET.QName(node).localname
        if ET.QName(node).namespace != MATHML_NS or name not in TAGS:
            raise ValueError("Unsupported generated MathML element")
        for key in list(node.attrib):
            if key not in ATTRS or any(
                c in node.attrib[key] for c in ("<", ">", '"', "'", "\\")
            ):
                del node.attrib[key]
    return ET.tostring(root, encoding="unicode")


def render_math(element, warnings):
    name = ET.QName(element).localname
    if name == "oMathPara":
        return (
            '<div class="equation-block">'
            + "".join(
                render_math(e, warnings)
                for e in element
                if ET.QName(e).localname == "oMath"
            )
            + "</div>"
        )
    try:
        return to_mathml(element)
    except Exception:
        warnings.add(
            "An equation could not be converted; its source text is shown. Check the original document."
        )
        text = "".join(element.itertext())
        return '<span class="math-fallback">[Equation: ' + escape(text) + "]</span>"
