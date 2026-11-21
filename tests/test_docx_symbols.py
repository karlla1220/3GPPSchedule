from lxml import etree as ET

from shared.docx_math import to_mathml
from shared.symbol_fonts import sym_char, symbol_text

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def sym(font, char):
    return ET.fromstring(f'<w:sym xmlns:w="{W}" w:font="{font}" w:char="{char}"/>')


def test_insert_symbol_becomes_its_character():
    assert sym_char(sym("Symbol", "F0AE")) == "→"  # RAN1 "R2D→D2R"
    assert sym_char(sym("Symbol", "61")) == "α"
    assert sym_char(sym("Wingdings", "F0E8")) == "🡺"  # RAN3 agenda
    assert sym_char(sym("Wingdings 3", "F0C6")) is not None


def test_unknown_symbol_keeps_placeholder():
    assert sym_char(sym("Webdings", "F0E8")) is None
    assert sym_char(sym("Symbol", "zz")) is None


def test_private_use_text_in_symbol_font():
    assert symbol_text("Symbol", "ab") == "a→b"
    assert symbol_text("Calibri", "ab") == "ab"


def test_symbols_inside_equations_survive_into_tex():
    # The OMML converter dropped w:sym without a trace: "R2DD2R".
    math = ET.fromstring(
        f'<m:oMath xmlns:m="{M}" xmlns:w="{W}"><m:r><m:t>R2D</m:t></m:r>'
        '<m:r><w:sym w:font="Symbol" w:char="F0AE"/></m:r><m:r><m:t>D2R</m:t></m:r>'
        '<m:r><w:rPr><w:rFonts w:ascii="Symbol"/></w:rPr><m:t></m:t></m:r></m:oMath>'
    )
    html = to_mathml(math)
    assert 'alttext="R2D\\rightarrow D2Rα"' in html  # what Markdown copy uses
    assert "→" in html and "α" in html
