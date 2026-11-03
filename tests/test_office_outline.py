"""Office files as blocks for the whole-document LLM fallback, and the notices it leaves."""
import json
from pathlib import Path

from shared import notices, office_outline

FIXTURES = Path(__file__).parent / 'fixtures'


def test_the_docx_cli_ast_becomes_blocks_with_accepted_text_and_marks():
    ast = {'blocks': [
        {'id': 'p0', 'type': 'paragraph', 'runs': [
            {'type': 'text', 'text': 'Kept '},
            {'type': 'text', 'text': 'added', 'trackedChange': {'kind': 'ins'}},
            {'type': 'text', 'text': 'gone', 'trackedChange': {'kind': 'del'}},
            {'type': 'text', 'text': ' struck', 'strike': True},
            {'type': 'text', 'text': ' red', 'color': 'FF0000'}]},
        {'id': 'p1', 'type': 'paragraph', 'runs': []},
        {'id': 't0', 'type': 'table', 'rows': [{'cells': [
            {'blocks': [{'id': 't0:r0c0:p0', 'type': 'paragraph', 'runs': [{'type': 'text', 'text': 'Monday'}]}],
             'gridSpan': 2, 'shading': 'FDE9D9'},
            {'blocks': [{'id': 't0:r0c1:p0', 'type': 'paragraph', 'runs': [{'type': 'text', 'text': 'Q1'}]}],
             'vMerge': 'restart'}]}]}]}
    blocks = office_outline.blocks_from_ast(ast)
    assert blocks[0] == {'id': 'p0', 'text': 'Kept added red', 'struck': 'struck', 'colours': ['FF0000']}
    assert blocks[1] == {'id': 't0', 'rows': [[{'id': 't0:r0c0', 'text': 'Monday', 'span': 2, 'fill': 'FDE9D9'},
                                               {'id': 't0:r0c1', 'text': 'Q1', 'merge': 'restart'}]]}
    assert office_outline.texts(blocks) == {'p0': 'Kept added red', 't0:r0c0': 'Monday', 't0:r0c1': 'Q1'}


def test_python_docx_gives_the_same_shape_without_the_binary():
    data = (FIXTURES / 'ran2/R2_135_Schedule_v11.docx').read_bytes()
    blocks = office_outline.blocks(data, 'x.docx')
    tables = [b for b in blocks if 'rows' in b]
    assert tables and all(cell['id'].startswith(table['id'] + ':r') for table in tables
                          for row in table['rows'] for cell in row)
    assert any(cell.get('merge') == 'restart' for table in tables for row in table['rows'] for cell in row)


def test_a_pptx_keeps_every_shape_its_place_and_the_marks_inside_table_cells():
    data = (FIXTURES / 'ran4/RAN4_120bis_meeting_schedule_THU_afternoon.pptx').read_bytes()
    slides = office_outline.blocks(data, 'x.pptx')
    assert [s['id'] for s in slides] == [f's{n}' for n in range(1, len(slides) + 1)]
    cells = [c for s in slides for sh in s['shapes'] for row in sh.get('rows', []) for c in row]
    # A cell where one run is struck keeps the rest as text and the struck run apart.
    mixed = next(c for c in cells if c.get('struck'))
    assert mixed['text'] and 'Ad-hoc' in mixed['struck'] and 'Ad-hoc' not in mixed['text'].split('\n')[0]
    assert any(c.get('span') == 4 for c in cells)
    assert all(len(sh['box']) == 4 for s in slides for sh in s['shapes'] if 'box' in sh)


def test_an_xlsx_keeps_every_sheet_with_its_cells_and_merges():
    data = (FIXTURES / 'ran3/offline_131_ver_06.xlsx').read_bytes()
    sheets = office_outline.blocks(data, 'x.xlsx')
    first = sheets[0]
    assert first['cells'][0]['id'] == f'{first["id"]}!A1' and 'B1:C1' in first['merged']


def test_notices_reach_one_issue_per_title_once_per_key(tmp_path):
    calls, issues = [], {}

    def gh(args):
        calls.append(args)
        if args[:2] == ['issue', 'list']:
            return json.dumps([{'number': n, 'title': t} for n, (t, _) in issues.items()])
        if args[:2] == ['issue', 'create']:
            issues[len(issues) + 1] = (args[args.index('--title') + 1], [args[args.index('--body') + 1]])
        if args[:2] == ['issue', 'view']:
            title, bodies = issues[int(args[2])]
            return json.dumps({'body': bodies[0], 'comments': [{'body': b} for b in bodies[1:]]})
        if args[:2] == ['issue', 'comment']:
            issues[int(args[2])][1].append(args[args.index('--body') + 1])
        return ''

    notices.add('ran4', 'document-fallback', 'ran4:abc', 'RAN4: layout', ['Source: a.pptx'])
    notices.add('ran4', 'document-fallback', 'ran4:abc', 'RAN4: layout', ['Source: a.pptx'])
    assert len(notices.read()) == 1
    assert notices.report('https://run/1', 'owner', gh) == 1
    title, bodies = issues[1]
    assert title == 'RAN4: layout' and 'cc @owner' in bodies[0] and 'notice:ran4:abc' in bodies[0]
    # The next build reuses the cached answer: nothing new is posted.
    assert notices.report('https://run/2', 'owner', gh) == 0
    notices.add('ran4', 'document-fallback', 'ran4:def', 'RAN4: layout', ['Source: b.pptx'])
    assert notices.report('https://run/3', 'owner', gh) == 1
    assert len(issues) == 1 and len(issues[1][1]) == 2


def test_the_shared_document_fallback_retries_caches_and_leaves_a_notice(tmp_path):
    import pytest
    from pydantic import BaseModel
    from shared import llm_cells

    class Result(BaseModel):
        sessions: list[str]

    asked = []

    def request(message, schema, model, thinking='medium'):
        asked.append(message)
        return {'sessions': ['wrong']} if len(asked) == 1 else {'sessions': ['p0']}

    def check(result):
        return [] if result.sessions == ['p0'] else ['cite p0']

    blocks = [{'id': 'p0', 'text': 'Monday 09:00 Opening'}]
    result = llm_cells.read_document('Read it.', blocks, Result, check, model='m', cache_dir=tmp_path,
                                     wg_id='ran4', source='a.pptx', meeting='ran4#121', request=request)
    assert result.sessions == ['p0'] and len(asked) == 2 and 'FIX THESE PROBLEMS:\ncite p0' in asked[1]
    assert [n['wg'] for n in notices.read()] == ['ran4'] and 'ran4#121' in notices.read()[0]['lines'][0]
    # The cached answer is checked again and used without asking.
    llm_cells.read_document('Read it.', blocks, Result, check, model='m', cache_dir=tmp_path,
                            wg_id='ran4', source='a.pptx', request=request)
    assert len(asked) == 2
    with pytest.raises(ValueError):
        llm_cells.read_document('Other.', blocks, Result, check, model='m', cache_dir=tmp_path, wg_id='ran4',
                                source='a.pptx', request=lambda *a, **k: {'sessions': []})
