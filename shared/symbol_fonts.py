"""Legacy Word symbol fonts (Symbol, Wingdings 1-3, MT Extra) to Unicode.

Word stores these as a byte of the font: `w:sym w:char="F0E8"`, or a
private-use character U+F020-U+F0FF in a run set in that font.
"""

from functools import lru_cache
import json
from pathlib import Path

from shared.metafile_images import symbol_map


@lru_cache(maxsize=1)
def _tables():
    # wmf2svg's SymbolFontMappings (Apache-2.0), via specduck-converter.
    data = json.loads((Path(__file__).parent / "vendor/symbol-font-map.json").read_text())
    tables = {
        font.lower(): {int(code, 16): char for code, char in table.items()}
        for font, table in data["fonts"].items()
    }
    tables["symbol"] = symbol_map()  # Adobe's table, as the metafile pictures use
    return tables


def symbol_char(font, code):
    """The character for `code` in a legacy symbol `font`, or None."""
    table = _tables().get((font or "").strip().lower())
    if table is None:
        return None
    if 0xF000 <= code <= 0xF0FF:
        code -= 0xF000
    return table.get(code)


def symbol_text(font, text):
    """`text` with the symbol font's private-use characters made Unicode."""
    if not text or (font or "").strip().lower() not in _tables():
        return text
    return "".join(
        (symbol_char(font, ord(c)) or c) if 0xF020 <= ord(c) <= 0xF0FF else c
        for c in text
    )


def run_font(run):
    """The ASCII font of a Word or OMML run (`w:rPr/w:rFonts`)."""
    for node in run.iter("{*}rFonts"):
        return node.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}ascii") or node.get(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}hAnsi"
        )
    return None


def sym_char(sym):
    """Word's Insert Symbol (`w:sym`) as a character, or None."""
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        return symbol_char(sym.get(w + "font"), int(sym.get(w + "char") or "", 16))
    except ValueError:
        return None
