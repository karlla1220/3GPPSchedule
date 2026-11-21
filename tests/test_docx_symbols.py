from lxml import etree as ET

from shared.docx_html import NS, symbol_char


def sym(font, char):
    return ET.fromstring(
        f'<w:sym xmlns:w="{NS["w"]}" w:font="{font}" w:char="{char}"/>'
    )


def test_insert_symbol_becomes_its_character():
    assert symbol_char(sym("Symbol", "F0AE")) == "→"  # RAN1 "R2D→D2R"
    assert symbol_char(sym("Symbol", "61")) == "α"
    assert symbol_char(sym("Wingdings", "F0E8")) == "→"  # RAN3 agenda


def test_unknown_symbol_keeps_placeholder():
    assert symbol_char(sym("Wingdings", "F04A")) is None  # smiley
    assert symbol_char(sym("Webdings", "F0E8")) is None
    assert symbol_char(sym("Symbol", "zz")) is None
