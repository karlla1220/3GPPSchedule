"""List nesting regression minimized from RAN1#126 EOM, AI 10.6.2."""
from pathlib import Path
from bs4 import BeautifulSoup
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from shared.docx_html import DocxHTML, render_blocks


def element(tag, **attrs):
    e = OxmlElement('w:' + tag)
    for name, value in attrs.items():
        e.set(qn('w:' + name), str(value))
    return e


def make_note(path: Path, direct_left=None):
    doc = Document()
    # Original List Paragraph style (aff): one shared 42pt/400-char-unit indent.
    style = doc.styles['List Paragraph'].element.get_or_add_pPr()
    style.append(element('ind', left=840, leftChars=400))
    numbering = doc.part.numbering_part.element
    abstract = element('abstractNum', abstractNumId=500)
    for depth, left in enumerate([720, 1440, 2160]):
        lvl = element('lvl', ilvl=depth)
        lvl.append(element('numFmt', val='bullet'))
        lvl.append(element('lvlText', val=['\uf0b7', 'o', '\uf0a7'][depth]))
        pr = element('pPr'); pr.append(element('ind', left=left, hanging=360))
        lvl.append(pr); abstract.append(lvl)
    numbering.append(abstract)
    num = element('num', numId=500); num.append(element('abstractNumId', val=500))
    numbering.append(num)
    for depth, text in [(0, 'Parent'), (1, 'Option 1'), (2, 'Detail'), (1, 'Option 2')]:
        p = doc.add_paragraph(text, style='List Paragraph')
        pr = p._p.get_or_add_pPr()
        numpr = element('numPr'); numpr.append(element('ilvl', val=depth)); numpr.append(element('numId', val=500))
        pr.append(numpr)
        # Source's leftChars=0 is not a direct twip indent.
        pr.append(element('ind', leftChars=0, **({'left': direct_left[depth]} if direct_left else {})))
    doc.save(path)


def test_shared_paragraph_style_does_not_flatten_numbered_list_depth(tmp_path):
    path = tmp_path / 'nested.docx'; make_note(path)
    doc = DocxHTML(path)
    dom = BeautifulSoup(render_blocks(doc.block(p) for p in doc.body), 'html.parser')
    assert [(p.text, len(p.find_parents('ul'))) for p in dom.select('li > p')] == [
        ('Parent', 1), ('Option 1', 2), ('Detail', 3), ('Option 2', 2)]


def test_direct_positions_override_numbering_depth_and_shared_style(tmp_path):
    path = tmp_path / 'direct.docx'; make_note(path, direct_left=[400, 709, 709])
    doc = DocxHTML(path)
    dom = BeautifulSoup(render_blocks(doc.block(p) for p in doc.body), 'html.parser')
    assert [(p.text, len(p.find_parents('ul'))) for p in dom.select('li > p')] == [
        ('Parent', 1), ('Option 1', 2), ('Detail', 2), ('Option 2', 2)]


def test_normal_paragraph_keeps_shared_style_indent(tmp_path):
    path = tmp_path / 'normal.docx'; make_note(path)
    doc = DocxHTML(path)
    p = doc.body[0]
    ppr = p.find(qn('w:pPr')); ppr.remove(ppr.find(qn('w:numPr')))
    assert 'margin-left:42pt' in doc.paragraph(p)
